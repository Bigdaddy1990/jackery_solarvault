"""Behavioural tests for the Jackery select platform.

Real select entities are constructed against a mocked coordinator boundary
(no HA runtime). They assert the current option derived from coordinator data,
the option list, that selecting an option forwards the mapped wire value to the
correct coordinator method, invalid-option rejection, and family gating in
``async_setup_entry``.
"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import select as select_mod
from custom_components.jackery_solarvault.const import (
    ACTION_ID_PORTABLE_SCREEN,
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    FIELD_PM,
    FIELD_SCHE_PHASE,
    FIELD_SLTB,
    FIELD_TEMP_UNIT,
    FIELD_UPS,
    FIELD_WORK_MODEL,
    PAYLOAD_CT_METER,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_PROPERTIES,
)
from custom_components.jackery_solarvault.select import (
    SELECT_DESCRIPTIONS,
    JackerySelect,
    _SelectState,  # ruff: ignore[import-private-name]
    async_setup_entry,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

_DEVICE_ID = "dev-1"

_ASYNC_METHODS = (
    "async_set_work_model",
    "async_set_temp_unit",
    "async_set_off_grid_time",
    "async_set_storm_minutes",
    "async_set_price_mode_dynamic",
    "async_set_price_mode_single",
    "async_set_price_source",
    "async_set_ct_phase",
    "async_portable_set_select",
)


def _description(key: str) -> Any:  # ruff:ignore[any-type]
    return next(desc for desc in SELECT_DESCRIPTIONS if desc.key == key)


def _coordinator(data: dict[str, Any]) -> MagicMock:
    coord = MagicMock(name="coordinator")
    coord.data = data
    coord.last_update_success = True
    coord.is_device_locally_reachable = MagicMock(return_value=False)
    coord.device_supports_advanced = MagicMock(return_value=False)
    for name in _ASYNC_METHODS:
        setattr(coord, name, AsyncMock(return_value=None))
    return coord


def _select(key: str, data: dict[str, Any]) -> JackerySelect:
    entity = JackerySelect.__new__(JackerySelect)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator(data)
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = _description(key)
    mutable._state = _SelectState()  # ruff: ignore[private-member-access]
    return entity


def test_work_mode_current_maps_code_to_option() -> None:
    """Work mode 7 maps to the tariff option."""
    entity = _select(
        "work_mode_select",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_WORK_MODEL: 7}}},
    )

    assert entity.current_option == "tariff"


def test_work_mode_unknown_code_reports_none_and_warns_once() -> None:
    """An unmapped work-mode code reports unknown and caches the warning."""
    entity = _select(
        "work_mode_select",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_WORK_MODEL: 99}}},
    )

    assert entity.current_option is None
    assert 99 in entity._state.warned_unknown_values  # ruff: ignore[magic-value-comparison, private-member-access]


def test_temp_unit_options_and_current() -> None:
    """Temp-unit select exposes both options and reflects the current value."""
    entity = _select(
        "temp_unit_select",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_TEMP_UNIT: 1}}},
    )

    assert set(entity.options) == {"celsius", "fahrenheit"}
    assert entity.current_option == "fahrenheit"


async def test_select_work_mode_forwards_mapped_code() -> None:
    """Selecting 'self_use' forwards work-mode code 2 to the coordinator."""
    entity = _select(
        "work_mode_select",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_WORK_MODEL: 0}}},
    )

    await entity.async_select_option("self_use")

    entity.coordinator.async_set_work_model.assert_awaited_once_with(_DEVICE_ID, 2)


async def test_select_temp_unit_forwards_mapped_code() -> None:
    """Selecting 'celsius' forwards temp-unit code 0 to the coordinator."""
    entity = _select(
        "temp_unit_select",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_TEMP_UNIT: 1}}},
    )

    await entity.async_select_option("celsius")

    entity.coordinator.async_set_temp_unit.assert_awaited_once_with(_DEVICE_ID, 0)


async def test_invalid_option_raises_translated_error() -> None:
    """An option outside the map is rejected with a translated action error."""
    entity = _select(
        "temp_unit_select",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_TEMP_UNIT: 0}}},
    )

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_select_option("kelvin")

    assert err.value.translation_key == "invalid_select_option"
    entity.coordinator.async_set_temp_unit.assert_not_awaited()


async def test_ct_phase_select_forwards_phase_and_serial() -> None:
    """Selecting a CT phase forwards the phase and meter serial."""
    entity = _select(
        "ct_phase_select",
        {_DEVICE_ID: {PAYLOAD_CT_METER: {FIELD_SCHE_PHASE: 1, "deviceSn": "CT-1"}}},
    )

    await entity.async_select_option("phase_2")

    entity.coordinator.async_set_ct_phase.assert_awaited_once_with(
        _DEVICE_ID,
        "CT-1",
        2,
    )


def test_ct_phase_current_maps_combined_phases() -> None:
    """App schePhase==4 maps to the combined-phases option."""
    entity = _select(
        "ct_phase_select",
        {_DEVICE_ID: {PAYLOAD_CT_METER: {FIELD_SCHE_PHASE: 4}}},
    )

    assert entity.current_option == "combined_phases"


async def test_portable_ups_model_forwards_via_portable_setter() -> None:
    """Selecting a UPS model routes through async_portable_set_select."""
    entity = _select(
        "portable_ups_model",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_UPS: 0}}},
    )

    await entity.async_select_option("lifepo4")

    entity.coordinator.async_portable_set_select.assert_awaited_once()
    _args, kwargs = entity.coordinator.async_portable_set_select.call_args
    assert kwargs["field"] == FIELD_UPS
    assert kwargs["value"] == 1


def test_portable_power_mode_options_and_current() -> None:
    """Power mode select exposes standard/eco/performance and reflects pm.

    Regression for F-SWEEP-1: the redundant ``portable_energy_saving`` switch
    was removed because "Energy Saving" is not a boolean -- it is this ``pm``
    field. This select is the sole, correctly-keyed control for it.
    """
    entity = _select(
        "portable_power_mode",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_PM: 1}}},
    )

    assert set(entity.options) == {"standard", "eco", "performance"}
    assert entity.current_option == "eco"


async def test_portable_power_mode_forwards_via_portable_setter() -> None:
    """Selecting 'performance' forwards pm code 2 via the portable setter."""
    entity = _select(
        "portable_power_mode",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_PM: 0}}},
    )

    await entity.async_select_option("performance")

    entity.coordinator.async_portable_set_select.assert_awaited_once()
    _args, kwargs = entity.coordinator.async_portable_set_select.call_args
    assert kwargs["field"] == FIELD_PM
    assert kwargs["value"] == 2  # ruff: ignore[magic-value-comparison]


@pytest.mark.parametrize(
    ["raw_value", "expected_option"],
    [
        [1, "off"],
        [2, "2min"],
        [3, "2h"],
    ],
)
def test_portable_screen_maps_state_to_current_option(
    raw_value: int,
    expected_option: str,
) -> None:
    """The app's sltb state maps to the matching screen-timeout option."""
    entity = _select(
        "portable_screen",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SLTB: raw_value}}},
    )

    assert entity.current_option == expected_option


def test_portable_screen_exposes_app_timeout_options() -> None:
    """The screen select exposes exactly the three app-supported timeouts."""
    entity = _select(
        "portable_screen",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SLTB: 1}}},
    )

    assert set(entity.options) == {"off", "2min", "2h"}


@pytest.mark.parametrize(
    ["option", "command_value", "state_value"],
    [
        ["off", 0, 1],
        ["2min", 2, 2],
        ["2h", 120, 3],
    ],
)
async def test_portable_screen_forwards_wire_value_and_updates_sltb(
    option: str,
    command_value: int,
    state_value: int,
) -> None:
    """Screen writes use action 18 and patch the separate sltb state field."""
    entity = _select(
        "portable_screen",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SLTB: 1}}},
    )

    await entity.async_select_option(option)

    entity.coordinator.async_portable_set_select.assert_awaited_once_with(
        _DEVICE_ID,
        action_id=ACTION_ID_PORTABLE_SCREEN,
        field="slt",
        value=command_value,
        local_patch={FIELD_SLTB: state_value},
    )


async def test_portable_screen_rejects_unknown_option() -> None:
    """Unsupported screen timeouts fail without sending a device command."""
    entity = _select(
        "portable_screen",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_SLTB: 1}}},
    )

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_select_option("5min")

    assert err.value.translation_key == "invalid_select_option"
    entity.coordinator.async_portable_set_select.assert_not_awaited()


async def test_auth_error_propagates_as_config_entry_auth_failed() -> None:
    """A rejected credential during a select surfaces as ConfigEntryAuthFailed."""
    entity = _select(
        "temp_unit_select",
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_TEMP_UNIT: 0}}},
    )
    entity.coordinator.async_set_temp_unit = AsyncMock(
        side_effect=select_mod.JackeryAuthError("nope"),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await entity.async_select_option("fahrenheit")


async def test_setup_entry_gates_home_selects() -> None:
    """A home device with work-mode + temp-unit props gets those selects."""
    coordinator = _coordinator({
        _DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_WORK_MODEL: 2, FIELD_TEMP_UNIT: 0}},
    })
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await async_setup_entry(None, entry, added.extend)

    keys = {ent.entity_description.key for ent in added}
    assert "work_mode_select" in keys
    assert "temp_unit_select" in keys
    assert not any(k.startswith("portable_") for k in keys)


async def test_setup_entry_creates_portable_select_family() -> None:
    """A legacy-bind portable device gets only the portable_ select family."""
    coordinator = _coordinator({
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {
                PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
            },
            PAYLOAD_PROPERTIES: {FIELD_UPS: 0},
        },
    })
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await async_setup_entry(None, entry, added.extend)

    keys = {ent.entity_description.key for ent in added}
    assert keys
    assert all(k.startswith("portable_") for k in keys)
