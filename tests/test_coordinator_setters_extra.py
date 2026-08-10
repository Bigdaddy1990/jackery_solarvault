"""Additional behavioral tests for device-setter command contracts.

These extend :mod:`tests.test_coordinator_setters` to the setters that were
still uncovered: the remaining property-frame writes, the weather/storm
commands (which route through ``_async_publish_command`` rather than the
BLE-first dispatcher), device reboot, and the sub-device switches (smart plug,
breaker, CT phase). The command-dispatch seam is a separately tested boundary
and is mocked; each test asserts the setter's own contract — the emitted
command frame (message type, action id, cmd, coerced body fields) and, where
the setter mirrors it, the optimistic local patch.
"""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault import coordinator as coord_mod
from custom_components.jackery_solarvault.const import (
    PAYLOAD_CIRCUIT_PROPERTY,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_WEATHER_PLAN,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.exceptions import HomeAssistantError

_DEVICE = "dev-1"
_MAX_OUT_W = 800
_OFF_GRID_MIN = 30
_DEFAULT_W = 1200
_STORM_MIN = 45


def _coordinator(entry: dict[str, Any] | None = None) -> Any:  # ruff:ignore[any-type]
    """Bare coordinator with both command-dispatch seams mocked."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    shell = cast("Any", coordinator)
    shell.data = {_DEVICE: entry if entry is not None else {PAYLOAD_PROPERTIES: {}}}
    shell._shutdown_started = False  # ruff: ignore[private-member-access]
    shell._property_overrides = {}  # ruff: ignore[private-member-access]
    shell._price_overrides = {}  # ruff: ignore[private-member-access]
    shell._live_property_key_monotonic = {}  # ruff: ignore[private-member-access]
    shell._listeners = {}  # ruff: ignore[private-member-access]
    shell._async_publish_command_ble_first = AsyncMock()  # ruff: ignore[private-member-access]
    shell._async_publish_command = AsyncMock()  # ruff: ignore[private-member-access]
    return shell


def _ble_frame(coordinator: Any) -> dict[str, Any]:  # ruff:ignore[any-type]
    """Return the kwargs of the last BLE-first command frame."""
    return coordinator._async_publish_command_ble_first.await_args.kwargs  # ruff: ignore[private-member-access]


def _cmd_frame(coordinator: Any) -> dict[str, Any]:  # ruff:ignore[any-type]
    """Return the kwargs of the last direct command frame."""
    return coordinator._async_publish_command.await_args.kwargs  # ruff: ignore[private-member-access]


# --- property-frame setters ------------------------------------------------


@pytest.mark.asyncio()
async def test_set_max_output_power_routes_via_property_change() -> None:
    """3038 uses DevicePropertyChange, not ControlCombine."""
    coordinator = _coordinator()

    await coordinator.async_set_max_output_power(_DEVICE, _MAX_OUT_W)

    frame = _ble_frame(coordinator)
    assert frame["body_fields"] == {coord_mod.FIELD_MAX_OUT_PW: _MAX_OUT_W}
    assert frame["message_type"] == coord_mod.MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE
    assert frame["action_id"] == coord_mod.ACTION_ID_MAX_OUT_PW
    assert (
        coordinator.data[_DEVICE][PAYLOAD_PROPERTIES][coord_mod.FIELD_MAX_OUT_PW]
        == _MAX_OUT_W
    )


@pytest.mark.asyncio()
async def test_set_auto_standby_bool_delegates_to_hours() -> None:
    """The legacy bool switch maps True->1 hour flag."""
    coordinator = _coordinator()

    await coordinator.async_set_auto_standby(_DEVICE, enabled=True)

    assert _ble_frame(coordinator)["body_fields"][coord_mod.FIELD_IS_AUTO_STANDBY] == 1


@pytest.mark.asyncio()
async def test_set_off_grid_time_coerces_int() -> None:
    """Off-grid time is coerced to int and sent via ControlCombine."""
    coordinator = _coordinator()

    await coordinator.async_set_off_grid_time(_DEVICE, minutes=_OFF_GRID_MIN)

    frame = _ble_frame(coordinator)
    assert frame["body_fields"] == {coord_mod.FIELD_OFF_GRID_TIME: _OFF_GRID_MIN}
    assert frame["cmd"] == coord_mod.MQTT_CMD_CONTROL_COMBINE


@pytest.mark.asyncio()
async def test_set_default_power_frames_control_combine() -> None:
    """Default power carries the coerced watts in a ControlCombine frame."""
    coordinator = _coordinator()

    await coordinator.async_set_default_power(_DEVICE, _DEFAULT_W)

    assert _ble_frame(coordinator)["body_fields"] == {
        coord_mod.FIELD_DEFAULT_PW: _DEFAULT_W,
    }


@pytest.mark.asyncio()
async def test_set_follow_meter_encodes_boolean() -> None:
    """Follow-meter enable is encoded as the boolean flag field."""
    coordinator = _coordinator()

    await coordinator.async_set_follow_meter(_DEVICE, enabled=False)

    assert _ble_frame(coordinator)["body_fields"] == {
        coord_mod.FIELD_IS_FOLLOW_METER_PW: 0,
    }


@pytest.mark.asyncio()
async def test_set_temp_unit_coerces_and_patches() -> None:
    """Temp unit coerces to int and lands in local properties."""
    coordinator = _coordinator()

    await coordinator.async_set_temp_unit(_DEVICE, unit=1)

    assert _ble_frame(coordinator)["body_fields"] == {coord_mod.FIELD_TEMP_UNIT: 1}
    assert coordinator.data[_DEVICE][PAYLOAD_PROPERTIES][coord_mod.FIELD_TEMP_UNIT] == 1


# --- weather / storm commands ---------------------------------------------


@pytest.mark.asyncio()
async def test_set_storm_warning_uses_direct_command_seam() -> None:
    """Storm warning routes through the direct (non-BLE-first) command seam."""
    coordinator = _coordinator({PAYLOAD_PROPERTIES: {}, PAYLOAD_WEATHER_PLAN: {}})

    await coordinator.async_set_storm_warning(_DEVICE, enabled=True)

    frame = _cmd_frame(coordinator)
    assert frame["body_fields"] == {coord_mod.FIELD_WPS: 1}
    assert frame["action_id"] == coord_mod.ACTION_ID_STORM_WARNING
    coordinator._async_publish_command_ble_first.assert_not_awaited()  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_set_storm_minutes_sends_weather_alert_frame() -> None:
    """Storm minutes emits a SendWeatherAlert frame with the interval field."""
    coordinator = _coordinator({PAYLOAD_PROPERTIES: {}, PAYLOAD_WEATHER_PLAN: {}})

    await coordinator.async_set_storm_minutes(_DEVICE, minutes=_STORM_MIN)

    frame = _cmd_frame(coordinator)
    assert frame["message_type"] == coord_mod.MQTT_MESSAGE_SEND_WEATHER_ALERT
    assert frame["body_fields"] == {coord_mod.FIELD_MINS_INTERVAL: _STORM_MIN}


@pytest.mark.asyncio()
async def test_reboot_device_sends_reboot_flag() -> None:
    """Reboot emits the reboot flag through BLE-first with cloud-MQTT fallback."""
    coordinator = _coordinator()

    await coordinator.async_reboot_device(_DEVICE)

    assert _ble_frame(coordinator)["body_fields"] == {coord_mod.FIELD_REBOOT: 1}
    coordinator._async_publish_command.assert_not_awaited()  # ruff: ignore[private-member-access]


# --- sub-device switches ---------------------------------------------------


@pytest.mark.asyncio()
async def test_set_smart_plug_switch_frames_and_mirrors_state() -> None:
    """Smart-plug on emits ControlSubDevice and mirrors the plug state."""
    plug = {coord_mod.FIELD_DEVICE_SN: "PLUG-9", coord_mod.FIELD_SYS_SWITCH: 0}
    coordinator = _coordinator(
        {PAYLOAD_PROPERTIES: {}, PAYLOAD_SMART_PLUGS: [plug]},
    )

    await coordinator.async_set_smart_plug_switch(_DEVICE, plug_sn="PLUG-9", on=True)

    frame = _ble_frame(coordinator)
    assert frame["message_type"] == coord_mod.MQTT_MESSAGE_CONTROL_SUB_DEVICE
    assert frame["body_fields"] == {
        coord_mod.FIELD_DEV_TYPE: coord_mod.SUBDEVICE_DEV_TYPE_SOCKET,
        coord_mod.FIELD_DEVICE_SN: "PLUG-9",
        coord_mod.FIELD_SYS_SWITCH: 1,
    }
    mirrored = coordinator.data[_DEVICE][PAYLOAD_SMART_PLUGS][0]
    assert mirrored[coord_mod.FIELD_SYS_SWITCH] == 1


@pytest.mark.asyncio()
async def test_set_smart_plug_priority_frames_socket_priority() -> None:
    """Smart-plug priority toggles the socketPri field in a ControlSubDevice."""
    plug = {coord_mod.FIELD_DEVICE_SN: "PLUG-9"}
    coordinator = _coordinator(
        {PAYLOAD_PROPERTIES: {}, PAYLOAD_SMART_PLUGS: [plug]},
    )

    await coordinator.async_set_smart_plug_priority(
        _DEVICE,
        plug_sn="PLUG-9",
        enabled=True,
    )

    frame = _ble_frame(coordinator)
    assert frame["action_id"] == coord_mod.ACTION_ID_CONTROL_SOCKET_PRIORITY
    assert frame["body_fields"][coord_mod.FIELD_SOCKET_PRIORITY] == 1


@pytest.mark.asyncio()
async def test_set_breaker_switch_frames_and_mirrors_state() -> None:
    """Breaker toggle emits ControlSubDevice with the breaker index and switch."""
    breaker = {coord_mod.FIELD_IDX: 2, coord_mod.FIELD_SW: 0}
    coordinator = _coordinator(
        {PAYLOAD_PROPERTIES: {}, PAYLOAD_CIRCUIT_PROPERTY: [breaker]},
    )

    await coordinator.async_set_breaker_switch(_DEVICE, "2", on=True)

    frame = _ble_frame(coordinator)
    assert frame["body_fields"] == {
        coord_mod.FIELD_DEV_TYPE: coord_mod.SUBDEVICE_DEV_TYPE_BREAKER,
        coord_mod.FIELD_IDX: 2,
        coord_mod.FIELD_SW: 1,
    }


@pytest.mark.asyncio()
async def test_set_ct_phase_rejects_out_of_range_phase() -> None:
    """A CT phase outside 1..4 is rejected before any command is sent."""
    coordinator = _coordinator()

    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_ct_phase(_DEVICE, "CT-1", 9)

    coordinator._async_publish_command_ble_first.assert_not_awaited()  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_set_ct_phase_missing_sn_raises() -> None:
    """A missing CT serial is rejected before any command is sent."""
    coordinator = _coordinator()

    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_ct_phase(_DEVICE, "", 1)

    coordinator._async_publish_command_ble_first.assert_not_awaited()  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_set_ct_phase_valid_sends_combined_phase() -> None:
    """A valid CT phase (4 = combined) is forwarded with the CT devType."""
    coordinator = _coordinator()

    await coordinator.async_set_ct_phase(_DEVICE, "CT-1", 4)

    frame = _ble_frame(coordinator)
    assert frame["body_fields"] == {
        coord_mod.FIELD_DEV_TYPE: coord_mod.SUBDEVICE_DEV_TYPE_CT,
        coord_mod.FIELD_DEVICE_SN: "CT-1",
        coord_mod.FIELD_SCHE_PHASE: 4,
    }
