"""Behavioural tests for the Jackery switch platform.

Real switch entities are constructed against a mocked coordinator boundary
(no HA runtime). They assert the on/off state read from coordinator data and
that turning a switch on/off forwards the boolean to the correct coordinator
method, plus smart-plug cloud-vs-local routing and family gating in
``async_setup_entry``.
"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import switch as switch_mod
from custom_components.jackery_solarvault.const import (
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    FIELD_ACCESSORIES,
    FIELD_AUTO_STANDBY,
    FIELD_CONTROL_ALLOWED,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEV_TYPE,
    FIELD_IS_CLOUD,
    FIELD_SCAN_NAME,
    FIELD_SOCKET_PRIORITY,
    FIELD_SWITCH_STATE,
    FIELD_SW_EPS,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_SYSTEM,
    SUBDEVICE_DEV_TYPE_SOCKET,
)
from custom_components.jackery_solarvault.switch import (
    SWITCH_DESCRIPTIONS,
    JackeryDescriptionSwitch,
    JackerySmartPlugSwitch,
    async_setup_entry,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

_DEVICE_ID = "dev-1"

_ASYNC_METHODS = (
    "async_set_eps",
    "async_set_auto_standby",
    "async_set_standby",
    "async_set_follow_meter",
    "async_set_off_grid_shutdown",
    "async_set_storm_warning",
    "async_update_third_party_mqtt_config",
    "async_portable_toggle_output",
    "async_set_shelly_cloud_switch",
    "async_set_smart_plug_switch",
    "async_set_smart_plug_priority",
    "async_set_breaker_switch",
)


def _description(key: str) -> Any:
    return next(desc for desc in SWITCH_DESCRIPTIONS if desc.key == key)


def _coordinator(data: dict[str, Any]) -> MagicMock:
    coord = MagicMock(name="coordinator")
    coord.data = data
    coord.last_update_success = True
    coord.is_device_locally_reachable = MagicMock(return_value=False)
    coord.device_supports_advanced = MagicMock(return_value=False)
    for name in _ASYNC_METHODS:
        setattr(coord, name, AsyncMock(return_value=None))
    return coord


def _switch(key: str, data: dict[str, Any]) -> JackeryDescriptionSwitch:
    entity = JackeryDescriptionSwitch.__new__(JackeryDescriptionSwitch)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator(data)
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = _description(key)
    return entity


def test_switch_platform_serializes_writes() -> None:
    """Switch is a write platform; PARALLEL_UPDATES must serialize writes."""
    assert switch_mod.PARALLEL_UPDATES == 1


def test_is_on_reads_boolean_source_key() -> None:
    """The EPS switch reflects the swEps property value."""
    on = _switch("eps_output", {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SW_EPS: 1}}})
    off = _switch("eps_output", {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SW_EPS: 0}}})

    assert on.is_on is True
    assert off.is_on is False


def test_is_on_unknown_without_source() -> None:
    """A switch with no source value reports unknown state."""
    entity = _switch("eps_output", {_DEVICE_ID: {PAYLOAD_PROPERTIES: {}}})

    assert entity.is_on is None


def test_standby_custom_transform_maps_one_to_on() -> None:
    """Standby uses its custom transform: autoStandby==1 means on."""
    entity = _switch(
        "standby",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_AUTO_STANDBY: 1}}},
    )

    assert entity.is_on is True


async def test_turn_on_forwards_true() -> None:
    """Turning the EPS switch on forwards True to the coordinator setter."""
    entity = _switch(
        "eps_output", {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SW_EPS: 0}}}
    )

    await entity.async_turn_on()

    entity.coordinator.async_set_eps.assert_awaited_once_with(_DEVICE_ID, True)


async def test_turn_off_forwards_false() -> None:
    """Turning the EPS switch off forwards False to the coordinator setter."""
    entity = _switch(
        "eps_output", {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SW_EPS: 1}}}
    )

    await entity.async_turn_off()

    entity.coordinator.async_set_eps.assert_awaited_once_with(_DEVICE_ID, False)


async def test_portable_switch_routes_through_toggle_output() -> None:
    """A portable output switch routes through async_portable_toggle_output."""
    entity = _switch(
        "portable_dc_output", {_DEVICE_ID: {PAYLOAD_PROPERTIES: {"odc": 0}}}
    )

    await entity.async_turn_on()

    entity.coordinator.async_portable_toggle_output.assert_awaited_once()
    _args, kwargs = entity.coordinator.async_portable_toggle_output.call_args
    assert kwargs["action_id"] == 10  # ACTION_ID_PORTABLE_OUTPUT_DC  # noqa: RUF105
    assert kwargs["field"] == "odc"
    assert kwargs["enabled"] is True


def test_portable_usb_output_reads_odcu_not_usba1() -> None:
    """The USB output switch reflects ``odcu`` (DC-USB enable), not ``usba1``.

    ``usba1`` is a live USB-A port-1 power reading; ``odcu`` is the DC-USB
    output enable field the setter (``_set_portable_dc_usb_output``) actually
    writes. Reading ``usba1`` caused snap-back after toggling and a wrong
    state when nothing was plugged into port 1.
    """
    on = _switch(
        "portable_usb_output",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {"odcu": 1, "usba1": 0}}},
    )
    off = _switch(
        "portable_usb_output",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {"odcu": 0, "usba1": 5}}},
    )

    assert on.is_on is True
    assert off.is_on is False


def test_portable_car_output_reads_odcc_not_idc() -> None:
    """The car output switch reflects ``odcc`` (DC-car enable), not ``idc``.

    ``idc`` is DC input current telemetry (unrelated to the car output
    relay); ``odcc`` is the DC-car-output enable field the setter
    (``_set_portable_dc_car_output``) actually writes. Reading ``idc``
    reflected an unrelated current reading instead of the relay state.
    """
    on = _switch(
        "portable_car_output",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {"odcc": 1, "idc": 0}}},
    )
    off = _switch(
        "portable_car_output",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {"odcc": 0, "idc": 5}}},
    )

    assert on.is_on is True
    assert off.is_on is False


async def test_write_error_becomes_translated_action_error() -> None:
    """A boundary write error is converted to a translated action error."""
    entity = _switch(
        "eps_output", {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SW_EPS: 0}}}
    )
    entity.coordinator.async_set_eps = AsyncMock(side_effect=TimeoutError("slow"))

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_turn_on()

    assert err.value.translation_key == "entity_action_failed"


async def test_auth_error_propagates_as_config_entry_auth_failed() -> None:
    """A rejected credential during a toggle surfaces as ConfigEntryAuthFailed."""
    entity = _switch(
        "eps_output", {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SW_EPS: 0}}}
    )
    entity.coordinator.async_set_eps = AsyncMock(
        side_effect=switch_mod.JackeryAuthError("nope"),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await entity.async_turn_on()


def _smart_plug(plug: dict[str, Any]) -> JackerySmartPlugSwitch:
    entity = JackerySmartPlugSwitch.__new__(JackerySmartPlugSwitch)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator(
        {_DEVICE_ID: {PAYLOAD_SMART_PLUGS: [plug]}},
    )
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable._plug_index = 1  # ruff: ignore[private-member-access]
    mutable._plug_sn = str(plug.get(FIELD_DEVICE_SN))  # ruff: ignore[private-member-access]
    mutable._plug_key = "smart_plug_1"  # ruff: ignore[private-member-access]
    return entity


async def test_local_smart_plug_routes_by_serial() -> None:
    """A local plug write routes through the serial-based coordinator setter."""
    entity = _smart_plug({FIELD_DEVICE_SN: "SN-PLUG-1", FIELD_SWITCH_STATE: 0})

    await entity.async_turn_on()

    entity.coordinator.async_set_smart_plug_switch.assert_awaited_once_with(
        _DEVICE_ID,
        plug_sn="SN-PLUG-1",
        on=True,
    )


async def test_cloud_shelly_plug_routes_through_cloud_setter() -> None:
    """A Shelly cloud plug write routes through the cloud device-id setter."""
    entity = _smart_plug({
        FIELD_DEVICE_SN: "SN-PLUG-2",
        FIELD_DEVICE_ID: "shelly-123",
        FIELD_SCAN_NAME: "shellyplusplugs",
        FIELD_IS_CLOUD: 1,
        FIELD_CONTROL_ALLOWED: 1,
        FIELD_SWITCH_STATE: 0,
    })

    await entity.async_turn_off()

    entity.coordinator.async_set_shelly_cloud_switch.assert_awaited_once_with(
        _DEVICE_ID,
        shelly_device_id="shelly-123",
        on=False,
    )


async def test_cloud_plug_without_control_permission_raises() -> None:
    """A cloud plug that disallows control raises a translated action error."""
    entity = _smart_plug({
        FIELD_DEVICE_SN: "SN-PLUG-3",
        FIELD_DEVICE_ID: "shelly-9",
        FIELD_SCAN_NAME: "shellyplusplugs",
        FIELD_IS_CLOUD: 1,
        FIELD_CONTROL_ALLOWED: 0,
    })

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_turn_on()

    assert err.value.translation_key == "entity_action_failed"
    entity.coordinator.async_set_shelly_cloud_switch.assert_not_awaited()


async def test_setup_entry_gates_home_switches_and_smart_plug() -> None:
    """A home device gets gated control switches plus a per-plug switch."""
    coordinator = _coordinator({
        _DEVICE_ID: {
            PAYLOAD_PROPERTIES: {FIELD_SW_EPS: 1},
            PAYLOAD_SMART_PLUGS: [
                {FIELD_DEVICE_SN: "SN-PLUG-1", FIELD_SOCKET_PRIORITY: 1},
            ],
        },
    })
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await cast("Any", async_setup_entry)(None, entry, added.extend)

    unique_ids = {ent.unique_id for ent in added}
    assert f"{_DEVICE_ID}_eps_output" in unique_ids
    assert any("smart_plug" in uid for uid in unique_ids)
    assert not any("portable" in uid for uid in unique_ids)


async def test_setup_entry_registers_eps_from_advanced_capability() -> None:
    """EPS remains registered when the first transport payload omits ``swEps``."""
    coordinator = _coordinator({
        _DEVICE_ID: {
            PAYLOAD_PROPERTIES: {},
        },
    })
    coordinator.device_supports_advanced.return_value = True
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await cast("Any", async_setup_entry)(None, entry, added.extend)

    assert f"{_DEVICE_ID}_eps_output" in {ent.unique_id for ent in added}


async def test_setup_entry_registers_eps_without_optional_transport_snapshot() -> None:
    """EPS registration is a home capability, not a BLE/MQTT value gate."""
    coordinator = _coordinator({
        _DEVICE_ID: {
            PAYLOAD_PROPERTIES: {},
        },
    })
    coordinator.device_supports_advanced.return_value = False
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await cast("Any", async_setup_entry)(None, entry, added.extend)

    assert f"{_DEVICE_ID}_eps_output" in {ent.unique_id for ent in added}


async def test_setup_entry_registers_home_and_plug_switches_from_discovery() -> None:
    """HTTP discovery, not a push snapshot, owns switch registration."""
    coordinator = _coordinator({
        _DEVICE_ID: {
            PAYLOAD_PROPERTIES: {},
            PAYLOAD_SYSTEM: {
                FIELD_ACCESSORIES: [
                    {
                        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                        FIELD_DEVICE_SN: "plug-1",
                    },
                ],
            },
        },
    })
    coordinator.device_supports_advanced.return_value = True
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await cast("Any", async_setup_entry)(None, entry, added.extend)

    unique_ids = {ent.unique_id for ent in added}
    for description in SWITCH_DESCRIPTIONS:
        if not description.key.startswith("portable_"):
            assert f"{_DEVICE_ID}_{description.key}" in unique_ids
    assert f"{_DEVICE_ID}_smart_plug_plug_1_switch" in unique_ids


async def test_setup_entry_creates_portable_switch_family() -> None:
    """A legacy-bind portable device gets only the portable_ switch family."""
    coordinator = _coordinator({
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {
                PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
            },
            PAYLOAD_PROPERTIES: {"odc": 1},
        },
    })
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await cast("Any", async_setup_entry)(None, entry, added.extend)

    keys = {ent.entity_description.key for ent in added}
    assert keys
    assert all(k.startswith("portable_") for k in keys)
