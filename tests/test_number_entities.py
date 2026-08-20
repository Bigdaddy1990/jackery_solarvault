"""Behavioural tests for the Jackery number platform.

These construct the real ``JackeryNumber`` entity against a mocked
coordinator boundary (no Home Assistant runtime, no recorder). They assert
business outcomes: the native value read from coordinator data, dynamic max /
unit / allowed-values, and that each setter forwards the coerced wire value to
the correct coordinator method. ``async_setup_entry`` is exercised with a stub
coordinator to assert model/family gating drives entity creation.
"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import number as number_mod
from custom_components.jackery_solarvault.const import (
    ACTION_ID_PORTABLE_SET_CHARGE_POWER,
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    FIELD_DT,
    FIELD_MAX_OUT_PW,
    FIELD_SINGLE_PRICE,
    FIELD_SOC_CHG_LIMIT,
    FIELD_THIRD_PARTY_MQTT_PORT,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
)
from custom_components.jackery_solarvault.number import (
    NUMBER_DESCRIPTIONS,
    JackeryNumber,
    async_setup_entry,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

_DEVICE_ID = "dev-1"

_ASYNC_METHODS = (
    "async_set_soc_limits",
    "async_set_max_feed_grid",
    "async_set_max_output_power",
    "async_set_default_power",
    "async_set_single_price",
    "async_update_third_party_mqtt_config",
    "async_portable_set_number",
)


def _description(key: str) -> Any:
    return next(desc for desc in NUMBER_DESCRIPTIONS if desc.key == key)


def _coordinator(data: dict[str, Any]) -> MagicMock:
    coord = MagicMock(name="coordinator")
    coord.data = data
    coord.last_update_success = True
    coord.is_device_locally_reachable = MagicMock(return_value=False)
    coord.is_device_reachable = MagicMock(
        side_effect=lambda device_id: device_id in coord.data
    )
    coord.third_party_mqtt_config_plaintext = MagicMock(
        side_effect=lambda device_id: (coord.data.get(device_id) or {}).get(
            PAYLOAD_THIRD_PARTY_MQTT_CONFIG, {}
        )
    )
    for name in _ASYNC_METHODS:
        setattr(coord, name, AsyncMock(return_value=None))
    return coord


def _number(key: str, data: dict[str, Any]) -> JackeryNumber:
    entity = JackeryNumber.__new__(JackeryNumber)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator(data)
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = _description(key)
    return entity


def test_native_value_reads_first_present_source_key() -> None:
    """The slider value comes from the first present payload source key."""
    entity = _number(
        "soc_charge_limit_set",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SOC_CHG_LIMIT: 80}}},
    )

    assert entity.native_value == pytest.approx(80.0)


def test_native_value_none_fallback_for_default_power() -> None:
    """Default-power exposes its 0.0 fallback when firmware omits the field."""
    entity = _number("default_power_set", {_DEVICE_ID: {PAYLOAD_PROPERTIES: {}}})

    assert entity.native_value == pytest.approx(0.0)


def test_integer_value_description_rounds_to_int() -> None:
    """The MQTT port renders as an int, never ``1883.0``."""
    entity = _number(
        "third_party_mqtt_port",
        {
            _DEVICE_ID: {
                PAYLOAD_THIRD_PARTY_MQTT_CONFIG: {FIELD_THIRD_PARTY_MQTT_PORT: 1883}
            }
        },
    )

    value = entity.native_value

    assert value == 1883
    assert isinstance(value, int)


def test_energy_storage_charge_limit_reads_dt_matching_its_setter() -> None:
    """MsgId=31 reads and writes the same field, per the shared msgId=0 pattern.

    Every other portable DevicePropertyChange command (``ast``, ``oact``,
    ``acdt``, ...) reads and writes the same field. ``PortableWorkingModeActivity``
    (standard portables) funnels ``PortableBody.dt`` into both ``initData`` and
    the msgId=31 ``onCommandResponse`` handler, matching
    ``_set_portable_energy_storage_charge_limit``'s write field. ``cl`` belongs
    to the unrelated ``portable_custom_use_charge_limit`` entity (msgId=33,
    ``SetBatteryBoundry``).
    """
    entity = _number(
        "portable_energy_storage_charge_limit",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_DT: 42}}},
    )

    assert entity.native_value == pytest.approx(42.0)


def test_dynamic_max_and_allowed_values_scale_with_capability() -> None:
    """A 2500 W-capable device offers both feed-in choices."""
    entity = _number(
        "max_feed_grid",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_MAX_OUT_PW: 2500}}},
    )

    assert entity.native_max_value == pytest.approx(2500.0)
    assert entity._allowed_values() == (800.0, 2500.0)


def test_dynamic_max_limits_low_power_device_to_single_choice() -> None:
    """An 800 W device only allows 800 W feed-in."""
    entity = _number(
        "max_feed_grid",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_MAX_OUT_PW: 800}}},
    )

    assert entity.native_max_value == pytest.approx(800.0)
    assert entity._allowed_values() == (800.0,)


def test_dynamic_unit_prefers_price_currency() -> None:
    """Single-tariff price uses the device's reported currency symbol."""
    entity = _number(
        "single_tariff_price_set",
        {_DEVICE_ID: {PAYLOAD_PRICE: {FIELD_SINGLE_PRICE: 0.3, "singleCurrency": "$"}}},
    )

    assert entity.native_unit_of_measurement == "$"


async def test_setter_forwards_coerced_soc_limit() -> None:
    """Setting the charge slider forwards an int charge limit to the coordinator."""
    entity = _number(
        "soc_charge_limit_set",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SOC_CHG_LIMIT: 50}}},
    )

    await entity.async_set_native_value(75.0)

    entity.coordinator.async_set_soc_limits.assert_awaited_once_with(
        _DEVICE_ID,
        charge_limit=75,
    )


async def test_setter_snaps_max_feed_grid_to_discrete_value() -> None:
    """A high feed-in request snaps to the 2500 W discrete command."""
    entity = _number(
        "max_feed_grid",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_MAX_OUT_PW: 2500}}},
    )

    await entity.async_set_native_value(2500.0)

    entity.coordinator.async_set_max_feed_grid.assert_awaited_once_with(
        _DEVICE_ID,
        2500,
    )


async def test_portable_setter_forwards_action_and_field() -> None:
    """A portable number setter routes through async_portable_set_number."""
    entity = _number(
        "portable_charge_power", {_DEVICE_ID: {PAYLOAD_PROPERTIES: {"csc": 500}}}
    )

    await entity.async_set_native_value(600.0)

    entity.coordinator.async_portable_set_number.assert_awaited_once()
    _args, kwargs = entity.coordinator.async_portable_set_number.call_args
    assert kwargs["action_id"] == ACTION_ID_PORTABLE_SET_CHARGE_POWER
    assert kwargs["field"] == "csc"
    assert kwargs["value"] == 600


def test_portable_charge_power_reads_csc_the_field_its_setter_writes() -> None:
    """The charge-power slider reads ``csc``, matching its own setter's write key.

    Source-of-truth (``fc/a.smali:2697``, ``SET_CHARGE_POWER``) confirms ``csc``
    is the wire key; the legacy ``rc`` read key never appears in the payload and
    left the entity permanently Unknown.
    """
    entity = _number(
        "portable_charge_power",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {"csc": 500}}},
    )

    assert entity.native_value == pytest.approx(500.0)


def test_portable_auto_shutdown_time_reads_ast_the_field_its_setter_writes() -> None:
    """The auto-shutdown number reads ``ast``, matching its own setter's write key.

    Source-of-truth (``fc/a.smali:4396``, ``SETTING_AUTO_SHUTDOWN_TIME``) confirms
    ``ast`` is the configured timer's wire key; ``dt`` is discharge-time telemetry
    that drifts with load and does not represent the configured value.
    """
    entity = _number(
        "portable_auto_shutdown_time",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {"ast": 45}}},
    )

    assert entity.native_value == pytest.approx(45.0)


async def test_out_of_range_value_raises_translated_error() -> None:
    """A value beyond native max is rejected with a translated action error."""
    entity = _number(
        "soc_charge_limit_set",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SOC_CHG_LIMIT: 50}}},
    )

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_set_native_value(150.0)

    assert err.value.translation_key == "invalid_number_range"
    entity.coordinator.async_set_soc_limits.assert_not_awaited()


async def test_disallowed_discrete_value_raises_translated_error() -> None:
    """A feed-in value outside the discrete set is rejected before any write."""
    entity = _number(
        "max_feed_grid",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_MAX_OUT_PW: 2500}}},
    )

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_set_native_value(1650.0)

    assert err.value.translation_key == "invalid_number_allowed_values"


async def test_auth_failure_from_setter_becomes_config_entry_auth_failed() -> None:
    """A rejected credential during a write surfaces as ConfigEntryAuthFailed."""
    entity = _number(
        "soc_charge_limit_set",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SOC_CHG_LIMIT: 50}}},
    )
    entity.coordinator.async_set_soc_limits = AsyncMock(
        side_effect=number_mod.JackeryAuthError("nope"),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await entity.async_set_native_value(60.0)


def test_available_true_when_device_present() -> None:
    """The entity is available while its device is in coordinator data."""
    entity = _number(
        "soc_charge_limit_set",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SOC_CHG_LIMIT: 50}}},
    )

    assert entity.available is True


def test_unavailable_when_device_absent() -> None:
    """The entity is unavailable once its device drops out of coordinator data."""
    entity = _number(
        "soc_charge_limit_set",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SOC_CHG_LIMIT: 50}}},
    )
    entity.coordinator.data = {"other": {}}

    assert entity.available is False


async def test_setup_entry_creates_gated_home_numbers() -> None:
    """A home device only gets the numbers its payload gates into existence."""
    coordinator = _coordinator(
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SOC_CHG_LIMIT: 50}}},
    )
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await cast("Any", async_setup_entry)(None, entry, added.extend)

    keys = {ent.entity_description.key for ent in added}
    assert "soc_charge_limit_set" in keys
    assert not any(k.startswith("portable_") for k in keys)


async def test_setup_entry_creates_portable_family_for_legacy_device() -> None:
    """A legacy-bind portable device only gets the portable_ number family."""
    coordinator = _coordinator({
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {
                PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
            },
            PAYLOAD_PROPERTIES: {"rc": 500},
        },
    })
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await cast("Any", async_setup_entry)(None, entry, added.extend)

    keys = {ent.entity_description.key for ent in added}
    assert keys
    assert all(k.startswith("portable_") for k in keys)
