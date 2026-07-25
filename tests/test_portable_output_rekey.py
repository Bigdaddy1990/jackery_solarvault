"""Regression contracts for portable command write-key correctness.

Source-of-truth (`docs/source-of-truth`, PortableBody.java) defines the exact
DevicePropertyChange body key each portable setting writes. These tests pin the
setter wiring to those keys so a mis-key (e.g. the historical
``pss``/``dl``/``ast`` scrambles) fails loudly instead of silently writing to
the wrong field on a device that cannot be live-verified.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.const import (
    ACTION_ID_PORTABLE_AC_COUNTDOWN,
    ACTION_ID_PORTABLE_AC_OUTPUT_DELAY,
    ACTION_ID_PORTABLE_AUTO_SHUTDOWN_TIME,
    ACTION_ID_PORTABLE_BLUETOOTH_SLEEP,
    ACTION_ID_PORTABLE_DC_CAR_COUNTDOWN,
    ACTION_ID_PORTABLE_DC_COUNTDOWN,
    ACTION_ID_PORTABLE_DC_USB_COUNTDOWN,
    ACTION_ID_PORTABLE_DISCHARGE_MEMORY,
    ACTION_ID_PORTABLE_ENERGY_STORAGE_CHARGE_LIMIT,
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY,
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SOC,
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SWITCH,
    ACTION_ID_PORTABLE_SETTING_BATTERY,
    ACTION_ID_PORTABLE_SETTING_CHARGE,
    ACTION_ID_PORTABLE_SET_CHARGE_POWER,
)
from custom_components.jackery_solarvault.number import (
    _async_portable_set_number,
    _set_portable_ac1_priority_soc,
    _set_portable_ac2_priority_soc,
    _set_portable_ac_countdown,
    _set_portable_ac_output_delay,
    _set_portable_auto_shutdown_time,
    _set_portable_bluetooth_sleep,
    _set_portable_charge_power,
    _set_portable_custom_use_charge_limit,
    _set_portable_custom_use_discharge_limit,
    _set_portable_dc_car_countdown,
    _set_portable_dc_countdown,
    _set_portable_dc_priority_soc,
    _set_portable_dc_usb_countdown,
    _set_portable_energy_storage_charge_limit,
)
from custom_components.jackery_solarvault.select import (
    _OPTION_TO_BATTERY_MODE,
    _OPTION_TO_CHARGE_MODE,
    _OPTION_TO_OUTPUT_PRIORITY,
    _portable_ac1_priority_select,
    _portable_ac2_priority_select,
    _portable_battery_mode_select,
    _portable_charge_mode_select,
    _portable_dc_priority_select,
)
from custom_components.jackery_solarvault.switch import (
    _set_portable_discharge_memory,
    _set_portable_output_priority_switch,
)

_DEVICE = "dev-1"
_SLEEP_MINUTES = 30
_PRIORITY_SOC_PCT = 20
_AC_DELAY_SECONDS = 3600
_CUSTOM_LOWER = 15
_CUSTOM_UPPER = 85
_ANY_PRIORITY_OPTION = next(iter(_OPTION_TO_OUTPUT_PRIORITY))


def _coord() -> Any:  # ruff:ignore[any-type]
    """Coordinator stub exposing the two portable command seams as mocks."""
    coord = type("_C", (), {})()
    coord.async_portable_toggle_output = AsyncMock()
    coord.async_portable_set_number = AsyncMock()
    return coord


@pytest.mark.asyncio()
async def test_output_priority_switch_writes_out_prio() -> None:
    """MsgId 47 master toggle writes PortableBody ``outPrio`` (not ``pss``)."""
    coord = _coord()

    await _set_portable_output_priority_switch(coord, _DEVICE, True)

    kwargs = coord.async_portable_toggle_output.await_args.kwargs
    assert kwargs["action_id"] == ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SWITCH
    assert kwargs["field"] == "outPrio"
    assert kwargs["enabled"] is True


@pytest.mark.asyncio()
async def test_discharge_memory_writes_dhg_recall() -> None:
    """MsgId 53 discharge memory writes ``dhg_recall`` (not ``dl``)."""
    coord = _coord()

    await _set_portable_discharge_memory(coord, _DEVICE, False)

    kwargs = coord.async_portable_toggle_output.await_args.kwargs
    assert kwargs["action_id"] == ACTION_ID_PORTABLE_DISCHARGE_MEMORY
    assert kwargs["field"] == "dhg_recall"
    assert kwargs["enabled"] is False


@pytest.mark.asyncio()
async def test_bluetooth_sleep_writes_tmt() -> None:
    """MsgId 44 Bluetooth sleep time writes ``tmt`` minutes (not ``ast``)."""
    coord = _coord()

    await _set_portable_bluetooth_sleep(coord, _DEVICE, _SLEEP_MINUTES)

    kwargs = coord.async_portable_set_number.await_args.kwargs
    assert kwargs["action_id"] == ACTION_ID_PORTABLE_BLUETOOTH_SLEEP
    assert kwargs["field"] == "tmt"
    assert kwargs["value"] == _SLEEP_MINUTES


def _select_entity() -> Any:  # ruff:ignore[any-type]
    """Select-entity stub exposing the portable set-select command seam."""
    entity = type("_E", (), {})()
    entity._device_id = _DEVICE
    entity.coordinator = type("_C", (), {})()
    entity.coordinator.async_portable_set_select = AsyncMock()
    return entity


@pytest.mark.parametrize(
    ["select_fn", "field"],
    [
        [_portable_ac1_priority_select, "oac1Prio"],
        [_portable_ac2_priority_select, "oac2Prio"],
        [_portable_dc_priority_select, "odcPrio"],
    ],
)
@pytest.mark.asyncio()
async def test_per_port_priority_uses_priority_action_not_soc(
    select_fn: Any,  # ruff:ignore[any-type]
    field: str,
) -> None:
    """Per-port priority (oacNPrio/odcPrio) is msgId 48, not the SOC msgId 49."""
    entity = _select_entity()

    await select_fn(entity, _ANY_PRIORITY_OPTION)

    kwargs = entity.coordinator.async_portable_set_select.await_args.kwargs
    assert kwargs["action_id"] == ACTION_ID_PORTABLE_OUTPUT_PRIORITY
    assert kwargs["field"] == field


@pytest.mark.parametrize(
    ["soc_setter", "field"],
    [
        [_set_portable_ac1_priority_soc, "oac1PrioSoc"],
        [_set_portable_ac2_priority_soc, "oac2PrioSoc"],
        [_set_portable_dc_priority_soc, "odcPrioSoc"],
    ],
)
@pytest.mark.asyncio()
async def test_per_port_priority_soc_writes_per_port_field(
    soc_setter: Any,  # ruff:ignore[any-type]
    field: str,
) -> None:
    """Per-port priority SOC (msgId 49) writes oacNPrioSoc/odcPrioSoc, not ``pss``."""
    coord = _coord()

    await soc_setter(coord, _DEVICE, _PRIORITY_SOC_PCT)

    kwargs = coord.async_portable_set_number.await_args.kwargs
    assert kwargs["action_id"] == ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SOC
    assert kwargs["field"] == field
    assert kwargs["value"] == _PRIORITY_SOC_PCT


@pytest.mark.asyncio()
async def test_ac_output_delay_writes_acdt_via_delay_action() -> None:
    """MsgId 41 AC output delay writes ``acdt`` seconds (distinct from oact/34)."""
    coord = _coord()

    await _set_portable_ac_output_delay(coord, _DEVICE, _AC_DELAY_SECONDS)

    kwargs = coord.async_portable_set_number.await_args.kwargs
    assert kwargs["action_id"] == ACTION_ID_PORTABLE_AC_OUTPUT_DELAY
    assert kwargs["field"] == "acdt"
    assert kwargs["value"] == _AC_DELAY_SECONDS


@pytest.mark.asyncio()
async def test_battery_mode_writes_lps_via_setting_battery() -> None:
    """MsgId 22 battery mode writes ``lps`` (0/1/2) via SETTING_BATTERY."""
    entity = _select_entity()
    option = next(iter(_OPTION_TO_BATTERY_MODE))

    await _portable_battery_mode_select(entity, option)

    kwargs = entity.coordinator.async_portable_set_select.await_args.kwargs
    assert kwargs["action_id"] == ACTION_ID_PORTABLE_SETTING_BATTERY
    assert kwargs["field"] == "lps"
    assert kwargs["value"] == _OPTION_TO_BATTERY_MODE[option]


@pytest.mark.asyncio()
async def test_charge_mode_writes_cs_via_setting_charge() -> None:
    """MsgId 21 charge mode writes ``cs`` (0/1/2) via SETTING_CHARGE."""
    entity = _select_entity()
    option = next(iter(_OPTION_TO_CHARGE_MODE))

    await _portable_charge_mode_select(entity, option)

    kwargs = entity.coordinator.async_portable_set_select.await_args.kwargs
    assert kwargs["action_id"] == ACTION_ID_PORTABLE_SETTING_CHARGE
    assert kwargs["field"] == "cs"
    assert kwargs["value"] == _OPTION_TO_CHARGE_MODE[option]


@pytest.mark.asyncio()
async def test_custom_use_discharge_limit_delegates_to_lower_bound() -> None:
    """The dl number delegates to the custom-use multi-field write as lower bound."""
    coord = type("_C", (), {})()
    coord.async_portable_set_custom_use_battery = AsyncMock()

    await _set_portable_custom_use_discharge_limit(coord, _DEVICE, _CUSTOM_LOWER)

    kwargs = coord.async_portable_set_custom_use_battery.await_args.kwargs
    assert kwargs["discharge_limit"] == _CUSTOM_LOWER


@pytest.mark.asyncio()
async def test_custom_use_charge_limit_delegates_to_upper_bound() -> None:
    """The cl number delegates to the custom-use multi-field write as upper bound."""
    coord = type("_C", (), {})()
    coord.async_portable_set_custom_use_battery = AsyncMock()

    await _set_portable_custom_use_charge_limit(coord, _DEVICE, _CUSTOM_UPPER)

    kwargs = coord.async_portable_set_custom_use_battery.await_args.kwargs
    assert kwargs["charge_limit"] == _CUSTOM_UPPER


_CHARGE_POWER_WATTS = 500
_ENERGY_STORAGE_CHARGE_LIMIT_PCT = 90
_AUTO_SHUTDOWN_MINUTES = 45
_AC_COUNTDOWN_SECONDS = 1800
_DC_COUNTDOWN_SECONDS = 900
_DC_USB_COUNTDOWN_SECONDS = 600
_DC_CAR_COUNTDOWN_SECONDS = 1200


@pytest.mark.asyncio()
async def test_async_portable_set_number_forwards_action_field_and_int_value() -> None:
    """The shared helper forwards action_id/field and coerces value to int."""
    coord = _coord()

    await _async_portable_set_number(
        coord,
        _DEVICE,
        action_id=ACTION_ID_PORTABLE_SET_CHARGE_POWER,
        field="csc",
        value=_CHARGE_POWER_WATTS,
    )

    coord.async_portable_set_number.assert_awaited_once_with(
        _DEVICE,
        action_id=ACTION_ID_PORTABLE_SET_CHARGE_POWER,
        field="csc",
        value=_CHARGE_POWER_WATTS,
    )


@pytest.mark.asyncio()
async def test_async_portable_set_number_truncates_float_toward_zero() -> None:
    """A fractional value is truncated via ``int()``, not rounded."""
    coord = _coord()

    await _async_portable_set_number(
        coord,
        _DEVICE,
        action_id=ACTION_ID_PORTABLE_SET_CHARGE_POWER,
        field="csc",
        value=12.7,
    )

    kwargs = coord.async_portable_set_number.await_args.kwargs
    assert kwargs["value"] == 12


@pytest.mark.parametrize(
    ["setter", "action_id", "field", "raw_value"],
    [
        [
            _set_portable_charge_power,
            ACTION_ID_PORTABLE_SET_CHARGE_POWER,
            "csc",
            _CHARGE_POWER_WATTS,
        ],
        [
            _set_portable_energy_storage_charge_limit,
            ACTION_ID_PORTABLE_ENERGY_STORAGE_CHARGE_LIMIT,
            "dt",
            _ENERGY_STORAGE_CHARGE_LIMIT_PCT,
        ],
        [
            _set_portable_auto_shutdown_time,
            ACTION_ID_PORTABLE_AUTO_SHUTDOWN_TIME,
            "ast",
            _AUTO_SHUTDOWN_MINUTES,
        ],
        [
            _set_portable_ac_countdown,
            ACTION_ID_PORTABLE_AC_COUNTDOWN,
            "oact",
            _AC_COUNTDOWN_SECONDS,
        ],
        [
            _set_portable_dc_countdown,
            ACTION_ID_PORTABLE_DC_COUNTDOWN,
            "odct",
            _DC_COUNTDOWN_SECONDS,
        ],
        [
            _set_portable_dc_usb_countdown,
            ACTION_ID_PORTABLE_DC_USB_COUNTDOWN,
            "odcut",
            _DC_USB_COUNTDOWN_SECONDS,
        ],
        [
            _set_portable_dc_car_countdown,
            ACTION_ID_PORTABLE_DC_CAR_COUNTDOWN,
            "odcct",
            _DC_CAR_COUNTDOWN_SECONDS,
        ],
    ],
)
@pytest.mark.asyncio()
async def test_number_setter_routes_through_shared_helper_with_correct_key(
    setter: Any,  # ruff:ignore[any-type]
    action_id: int,
    field: str,
    raw_value: int,
) -> None:
    """Each refactored setter still writes its pinned action_id/field/value.

    These setters were rewired to delegate to ``_async_portable_set_number``;
    this pins their per-field wiring so the shared-helper refactor cannot
    silently swap one setter's action_id/field for another's.
    """
    coord = _coord()

    await setter(coord, _DEVICE, raw_value)

    kwargs = coord.async_portable_set_number.await_args.kwargs
    assert kwargs["action_id"] == action_id
    assert kwargs["field"] == field
    assert kwargs["value"] == raw_value
