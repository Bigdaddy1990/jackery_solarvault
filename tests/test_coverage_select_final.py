"""Focused behavioural branch coverage for the Jackery select platform."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.const import (
    ACTION_ID_PORTABLE_AC_OUTPUT_MODE,
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY,
    ACTION_ID_PORTABLE_SETTING_BATTERY,
    ACTION_ID_PORTABLE_SETTING_CHARGE,
    DEFAULT_STORM_WARNING_MINUTES,
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    FIELD_COMPANY_NAME,
    FIELD_COUNTRY,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_MINS_INTERVAL,
    FIELD_OFF_GRID_TIME,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_SCHE_PHASE,
    FIELD_SYSTEM_REGION,
    FIELD_WPC,
    FIELD_WPS,
    PAYLOAD_CT_METER,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_PRICE,
    PAYLOAD_PRICE_SOURCES,
    PAYLOAD_PROPERTIES,
    PAYLOAD_WEATHER_PLAN,
)
from custom_components.jackery_solarvault.select import (
    SELECT_DESCRIPTIONS,
    JackerySelect,
    _SelectState,  # ruff: ignore[import-private-name]
    async_setup_entry,
)
from homeassistant.exceptions import HomeAssistantError

_DEVICE_ID = "device-coverage"

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


def _description(key: str) -> Any:  # noqa: ANN401, RUF105
    """Return one production select description by stable key."""
    return next(
        description for description in SELECT_DESCRIPTIONS if description.key == key
    )


def _coordinator(data: dict[str, Any]) -> MagicMock:
    """Build a coordinator boundary with observable async write methods."""
    coordinator = MagicMock(name="coordinator")
    coordinator.data = data
    coordinator.last_update_success = True
    coordinator.is_device_locally_reachable = MagicMock(return_value=False)
    coordinator.device_supports_advanced = MagicMock(return_value=False)
    for method_name in _ASYNC_METHODS:
        setattr(coordinator, method_name, AsyncMock(return_value=None))
    return coordinator


def _select(key: str, payload: dict[str, Any]) -> JackerySelect:
    """Construct a real select around a mocked coordinator integration boundary."""
    entity = JackerySelect.__new__(JackerySelect)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator({_DEVICE_ID: payload})
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = _description(key)
    mutable._state = _SelectState()  # ruff: ignore[private-member-access]
    return entity


def test_storm_minutes_uses_explicit_weather_value_before_enabled_fallback() -> None:
    """An explicit valid lead time wins over the generic enabled-plan default."""
    entity = _select(
        "storm_warning_minutes_select",
        {
            PAYLOAD_PROPERTIES: {FIELD_WPS: 1},
            PAYLOAD_WEATHER_PLAN: {FIELD_MINS_INTERVAL: "90"},
        },
    )

    assert entity.current_option == "min_90"
    assert "min_90" in entity.options


def test_storm_minutes_replaces_firmware_sentinel_with_enabled_default() -> None:
    """A sub-minimum firmware sentinel must not leak as an untranslated option."""
    entity = _select(
        "storm_warning_minutes_select",
        {PAYLOAD_PROPERTIES: {FIELD_WPC: 1, FIELD_WPS: 1}},
    )

    assert entity.current_option == f"min_{DEFAULT_STORM_WARNING_MINUTES}"
    assert "min_1" not in entity.options


async def test_island_auto_off_reads_minutes_and_writes_selected_hours() -> None:
    """Minute-form payloads map to hours while writes retain the app's minute unit."""
    entity = _select(
        "auto_off_island_mode",
        {PAYLOAD_PROPERTIES: {FIELD_OFF_GRID_TIME: 120}},
    )

    assert entity.current_option == "h_2"
    await entity.async_select_option("h_8")

    entity.coordinator.async_set_off_grid_time.assert_awaited_once_with(
        _DEVICE_ID,
        480,
    )


async def test_dynamic_price_mode_rejects_missing_provider_without_writing() -> None:
    """Dynamic pricing is unavailable when neither current nor offered source exists."""
    entity = _select("electricity_price_mode", {PAYLOAD_PRICE: {}})

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_select_option("dynamic")

    assert error.value.translation_key == "dynamic_tariff_unavailable"
    entity.coordinator.async_set_price_mode_dynamic.assert_not_awaited()


async def test_active_dynamic_price_mode_remains_selectable_without_sources() -> None:
    """A currently active dynamic mode remains writable during a source-list outage."""
    entity = _select(
        "electricity_price_mode",
        {PAYLOAD_PRICE: {FIELD_DYNAMIC_OR_SINGLE: 1}},
    )

    assert entity.current_option == "dynamic"
    await entity.async_select_option("dynamic")

    entity.coordinator.async_set_price_mode_dynamic.assert_awaited_once_with(_DEVICE_ID)


async def test_price_provider_options_keep_current_and_write_offered_source() -> None:
    """The current provider stays visible while an offered provider remains writable."""
    offered = {
        FIELD_PLATFORM_COMPANY_ID: 8,
        FIELD_COUNTRY: "DE",
        FIELD_COMPANY_NAME: "Grid Eight",
    }
    entity = _select(
        "electricity_price_provider",
        {
            PAYLOAD_PRICE: {
                FIELD_PLATFORM_COMPANY_ID: 9,
                FIELD_SYSTEM_REGION: "AT",
                FIELD_COMPANY_NAME: "Current Nine",
            },
            PAYLOAD_PRICE_SOURCES: [offered],
        },
    )

    assert entity.options == ["Grid Eight (DE) #8", "Current Nine (AT) #9"]
    await entity.async_select_option("Grid Eight (DE) #8")

    entity.coordinator.async_set_price_source.assert_awaited_once_with(
        _DEVICE_ID,
        offered,
    )


async def test_ct_phase_write_requires_a_nonblank_meter_serial() -> None:
    """A CT phase command without the accessory serial fails before transport IO."""
    entity = _select(
        "ct_phase_select",
        {PAYLOAD_CT_METER: {FIELD_SCHE_PHASE: 2, "deviceSn": "  "}},
    )

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_select_option("phase_1")

    assert error.value.translation_key == "entity_action_failed"
    entity.coordinator.async_set_ct_phase.assert_not_awaited()


async def test_untranslated_write_error_becomes_entity_action_error() -> None:
    """Transport-facing HA errors receive the integration's translated wrapper."""
    entity = _select(
        "temp_unit_select",
        {PAYLOAD_PROPERTIES: {"tempUnit": 0}},
    )
    entity.coordinator.async_set_temp_unit = AsyncMock(
        side_effect=HomeAssistantError("transport failed"),
    )

    with pytest.raises(HomeAssistantError) as error:
        await entity.async_select_option("fahrenheit")

    assert error.value.translation_key == "entity_action_failed"


@pytest.mark.parametrize(
    [
        "key",
        "field",
        "raw_value",
        "current_option",
        "option",
        "wire_value",
        "action_id",
    ],
    [
        [
            "portable_battery_mode",
            "lps",
            1,
            "preset",
            "custom",
            2,
            ACTION_ID_PORTABLE_SETTING_BATTERY,
        ],
        [
            "portable_charge_mode",
            "cs",
            0,
            "fast",
            "rush",
            1,
            ACTION_ID_PORTABLE_SETTING_CHARGE,
        ],
        [
            "portable_ac_output_mode",
            "acmode",
            1,
            "quiet",
            "high-performance",
            2,
            ACTION_ID_PORTABLE_AC_OUTPUT_MODE,
        ],
        [
            "portable_output_priority",
            "outPrio",
            0,
            "battery-first",
            "solar-first",
            2,
            ACTION_ID_PORTABLE_OUTPUT_PRIORITY,
        ],
        [
            "portable_ac1_priority",
            "oac1Prio",
            1,
            "grid-first",
            "battery-first",
            0,
            ACTION_ID_PORTABLE_OUTPUT_PRIORITY,
        ],
        [
            "portable_ac2_priority",
            "oac2Prio",
            2,
            "solar-first",
            "grid-first",
            1,
            ACTION_ID_PORTABLE_OUTPUT_PRIORITY,
        ],
        [
            "portable_dc_priority",
            "odcPrio",
            0,
            "battery-first",
            "grid-first",
            1,
            ACTION_ID_PORTABLE_OUTPUT_PRIORITY,
        ],
    ],
)
async def test_portable_select_families_map_current_and_wire_values(
    key: str,
    field: str,
    raw_value: int,
    current_option: str,
    option: str,
    wire_value: int,
    action_id: int,
) -> None:
    """Each portable selector exposes its state and forwards its documented code."""
    entity = _select(key, {PAYLOAD_PROPERTIES: {field: raw_value}})

    assert entity.current_option == current_option
    await entity.async_select_option(option)

    entity.coordinator.async_portable_set_select.assert_awaited_once()
    _args, kwargs = entity.coordinator.async_portable_set_select.call_args
    assert kwargs["action_id"] == action_id
    assert kwargs["field"] == field
    assert kwargs["value"] == wire_value


async def test_setup_listener_adds_new_home_selects_once_after_discovery() -> None:
    """A later payload discovers selects once and unchanged callbacks add no duplicates."""  # noqa: E501, RUF105
    coordinator = _coordinator({})
    listeners: list[Any] = []

    def _capture_listener(listener: Any) -> Any:  # noqa: ANN401, RUF105
        listeners.append(listener)
        return lambda: None

    coordinator.async_add_listener = MagicMock(side_effect=_capture_listener)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await cast("Any", async_setup_entry)(None, entry, added.extend)
    assert added == []

    coordinator.data = {
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {
                PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
            },
            PAYLOAD_PROPERTIES: {"workModel": 2},
        },
    }
    listeners[0]()
    discovered_count = len(added)
    listeners[0]()

    assert discovered_count == 2
    assert {entity.entity_description.key for entity in added} == {
        "electricity_price_mode",
        "work_mode_select",
    }
    assert len(added) == discovered_count
