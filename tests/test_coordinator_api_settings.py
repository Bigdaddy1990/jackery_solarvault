"""Behavioral tests for the coordinator's device-setter command methods.

These setters translate a Home Assistant control action into a Jackery app
command frame (routed through the BLE-first / MQTT publish helpers) and mirror
the requested value into the local coordinator cache so entities reflect the
change before the device echoes it back.

The tests mock only the publish transport helpers and the local-cache patch
helpers (integration boundaries within the coordinator's own send path) and
assert the *business outcome*: the correct command family (message type /
action id / cmd) and the correctly derived body value, plus that the optimistic
local patch carries the same derived value.
"""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import coordinator as coord_mod
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

_DEVICE = "573702884982521856"


def _coordinator() -> JackerySolarVaultCoordinator:
    """Build a coordinator with the send path stubbed to integration seams."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.data = {}
    obj._async_publish_command = AsyncMock()  # ruff: ignore[private-member-access]
    obj._async_publish_command_ble_first = AsyncMock()  # ruff: ignore[private-member-access]
    obj._apply_local_property_patch = MagicMock()  # ruff: ignore[private-member-access]
    obj._apply_local_system_patch = MagicMock()  # ruff: ignore[private-member-access]
    obj._apply_local_weather_plan_patch = MagicMock()  # ruff: ignore[private-member-access]
    return coordinator


def _ble_call(coordinator: JackerySolarVaultCoordinator) -> Any:
    return cast("Any", coordinator)._async_publish_command_ble_first.await_args  # ruff: ignore[private-member-access]


def _plain_call(coordinator: JackerySolarVaultCoordinator) -> Any:
    return cast("Any", coordinator)._async_publish_command.await_args  # ruff: ignore[private-member-access]


@pytest.mark.asyncio
async def test_set_eps_enabled_sends_one_and_patches() -> None:
    """Enabling EPS sends swEps=1 on the property-change family and mirrors it."""
    coordinator = _coordinator()

    await coordinator.async_set_eps(_DEVICE, enabled=True)

    call = _ble_call(coordinator)
    assert call.args[0] == _DEVICE
    assert call.kwargs["action_id"] == coord_mod.ACTION_ID_EPS_ENABLED
    assert call.kwargs["body_fields"] == {coord_mod.FIELD_SW_EPS: 1}
    cast("Any", coordinator)._apply_local_property_patch.assert_called_once_with(  # ruff: ignore[private-member-access]
        _DEVICE,
        {coord_mod.FIELD_SW_EPS: 1},
    )


@pytest.mark.asyncio
async def test_set_eps_disabled_sends_zero() -> None:
    """Disabling EPS derives swEps=0."""
    coordinator = _coordinator()

    await coordinator.async_set_eps(_DEVICE, enabled=False)

    assert _ble_call(coordinator).kwargs["body_fields"] == {coord_mod.FIELD_SW_EPS: 0}


@pytest.mark.asyncio
async def test_set_soc_limits_requires_at_least_one_side() -> None:
    """Calling with neither limit is a usage error surfaced as UpdateFailed."""
    coordinator = _coordinator()

    with pytest.raises(UpdateFailed):
        await coordinator.async_set_soc_limits(_DEVICE)


@pytest.mark.asyncio
async def test_set_soc_limits_fills_missing_side_from_state() -> None:
    """Only-charge given: discharge is filled from last-known coordinator state."""
    coordinator = _coordinator()
    cast("Any", coordinator).data = {
        _DEVICE: {
            coord_mod.PAYLOAD_PROPERTIES: {
                coord_mod.FIELD_SOC_DISCHG_LIMIT: 20,
                coord_mod.FIELD_SOC_FORCE_CHG: 1,
            },
        },
    }

    await coordinator.async_set_soc_limits(_DEVICE, charge_limit=90)

    body = _ble_call(coordinator).kwargs["body_fields"]
    assert body[coord_mod.FIELD_SOC_CHG_LIMIT] == 90
    assert body[coord_mod.FIELD_SOC_DISCHG_LIMIT] == 20
    assert body[coord_mod.FIELD_SOC_FORCE_CHG] == 1


@pytest.mark.asyncio
async def test_set_soc_limits_defaults_when_state_missing() -> None:
    """With no cached state, charge defaults to 100 and discharge to 0."""
    coordinator = _coordinator()

    await coordinator.async_set_soc_limits(_DEVICE, discharge_limit=15)

    body = _ble_call(coordinator).kwargs["body_fields"]
    assert body[coord_mod.FIELD_SOC_DISCHG_LIMIT] == 15
    assert body[coord_mod.FIELD_SOC_CHG_LIMIT] == 100


@pytest.mark.asyncio
async def test_set_soc_limits_rejects_out_of_range() -> None:
    """A value outside 0..100 is coerced to None and rejected as UpdateFailed."""
    coordinator = _coordinator()

    with pytest.raises(UpdateFailed):
        await coordinator.async_set_soc_limits(_DEVICE, charge_limit=150)


@pytest.mark.asyncio
async def test_set_max_feed_grid_mirrors_both_fields() -> None:
    """Max feed-grid mirrors the value under both the app and legacy keys."""
    coordinator = _coordinator()

    await coordinator.async_set_max_feed_grid(_DEVICE, 800)

    assert _ble_call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_MAX_FEED_GRID: 800,
    }
    cast("Any", coordinator)._apply_local_property_patch.assert_called_once_with(  # ruff: ignore[private-member-access]
        _DEVICE,
        {coord_mod.FIELD_MAX_FEED_GRID: 800, coord_mod.FIELD_MAX_GRID_STD_PW: 800},
    )


@pytest.mark.asyncio
async def test_set_max_output_power_uses_property_change_family() -> None:
    """3038 routes via DevicePropertyChange, not ControlCombine."""
    coordinator = _coordinator()

    await coordinator.async_set_max_output_power(_DEVICE, 1200)

    call = _ble_call(coordinator)
    assert call.kwargs["message_type"] == coord_mod.MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE
    assert call.kwargs["body_fields"] == {coord_mod.FIELD_MAX_OUT_PW: 1200}


@pytest.mark.asyncio
async def test_set_auto_standby_hours_is_boolean_flag() -> None:
    """Any positive hour count maps to the isAutoStandby=1 boolean flag."""
    coordinator = _coordinator()

    await coordinator.async_set_auto_standby_hours(_DEVICE, 5)

    assert _ble_call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_IS_AUTO_STANDBY: 1,
    }
    cast("Any", coordinator)._apply_local_property_patch.assert_called_once_with(  # ruff: ignore[private-member-access]
        _DEVICE,
        {coord_mod.FIELD_IS_AUTO_STANDBY: 1, coord_mod.FIELD_AUTO_STANDBY: 1},
    )


@pytest.mark.asyncio
async def test_set_auto_standby_hours_zero_disables() -> None:
    """Zero hours disables auto standby and mirrors the enum POWER_ON=2."""
    coordinator = _coordinator()

    await coordinator.async_set_auto_standby_hours(_DEVICE, 0)

    assert _ble_call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_IS_AUTO_STANDBY: 0,
    }
    cast("Any", coordinator)._apply_local_property_patch.assert_called_once_with(  # ruff: ignore[private-member-access]
        _DEVICE,
        {coord_mod.FIELD_IS_AUTO_STANDBY: 0, coord_mod.FIELD_AUTO_STANDBY: 2},
    )


@pytest.mark.asyncio
async def test_set_auto_standby_bool_delegates_to_hours() -> None:
    """The legacy bool switch reuses the hours setter with 1/0."""
    coordinator = _coordinator()

    await coordinator.async_set_auto_standby(_DEVICE, enabled=True)

    assert _ble_call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_IS_AUTO_STANDBY: 1,
    }


@pytest.mark.asyncio
async def test_set_standby_maps_true_to_sleep_one() -> None:
    """Standby enabled maps to autoStandby=1 (SLEEP)."""
    coordinator = _coordinator()

    await coordinator.async_set_standby(_DEVICE, enabled=True)

    assert _ble_call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_AUTO_STANDBY: 1,
    }


@pytest.mark.asyncio
async def test_set_standby_maps_false_to_power_on_two() -> None:
    """Standby disabled maps to autoStandby=2 (POWER_ON)."""
    coordinator = _coordinator()

    await coordinator.async_set_standby(_DEVICE, enabled=False)

    assert _ble_call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_AUTO_STANDBY: 2,
    }


@pytest.mark.asyncio
async def test_set_work_model_casts_to_int() -> None:
    """Work model forwards an int-coerced mode value."""
    coordinator = _coordinator()

    await coordinator.async_set_work_model(_DEVICE, 3)

    assert _ble_call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_WORK_MODEL: 3,
    }


@pytest.mark.asyncio
async def test_set_off_grid_shutdown_derives_flag() -> None:
    """Off-grid shutdown derives a 0/1 flag from the bool."""
    coordinator = _coordinator()

    await coordinator.async_set_off_grid_shutdown(_DEVICE, enabled=True)

    assert _ble_call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_OFF_GRID_DOWN: 1,
    }


@pytest.mark.asyncio
async def test_set_off_grid_time_forwards_minutes() -> None:
    """Off-grid time forwards an int minute value."""
    coordinator = _coordinator()

    await coordinator.async_set_off_grid_time(_DEVICE, 45)

    assert _ble_call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_OFF_GRID_TIME: 45,
    }


@pytest.mark.asyncio
async def test_set_default_power_forwards_watts() -> None:
    """Default power forwards an int watt value."""
    coordinator = _coordinator()

    await coordinator.async_set_default_power(_DEVICE, 600)

    assert _ble_call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_DEFAULT_PW: 600,
    }


@pytest.mark.asyncio
async def test_set_follow_meter_derives_flag() -> None:
    """Follow-meter derives a 0/1 flag."""
    coordinator = _coordinator()

    await coordinator.async_set_follow_meter(_DEVICE, enabled=False)

    assert _ble_call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_IS_FOLLOW_METER_PW: 0,
    }


@pytest.mark.asyncio
async def test_set_storm_warning_uses_plain_publish_and_dual_patch() -> None:
    """Storm warning goes via the plain publish path and patches two caches."""
    coordinator = _coordinator()

    await coordinator.async_set_storm_warning(_DEVICE, enabled=True)

    assert _plain_call(coordinator).kwargs["body_fields"] == {coord_mod.FIELD_WPS: 1}
    cast("Any", coordinator)._apply_local_property_patch.assert_called_once_with(  # ruff: ignore[private-member-access]
        _DEVICE,
        {coord_mod.FIELD_WPS: 1},
    )
    cast("Any", coordinator)._apply_local_weather_plan_patch.assert_called_once_with(  # ruff: ignore[private-member-access]
        _DEVICE,
        {coord_mod.FIELD_WPS: 1},
    )


@pytest.mark.asyncio
async def test_set_storm_minutes_mirrors_wpc_and_interval() -> None:
    """Storm minutes mirrors the value under both wpc and interval keys."""
    coordinator = _coordinator()

    await coordinator.async_set_storm_minutes(_DEVICE, 30)

    body = _plain_call(coordinator).kwargs["body_fields"]
    assert body == {coord_mod.FIELD_MINS_INTERVAL: 30}
    cast("Any", coordinator)._apply_local_property_patch.assert_called_once_with(  # ruff: ignore[private-member-access]
        _DEVICE,
        {coord_mod.FIELD_WPC: 30, coord_mod.FIELD_MINS_INTERVAL: 30},
    )


@pytest.mark.asyncio
async def test_set_temp_unit_forwards_int() -> None:
    """Temp unit forwards an int-coerced unit code."""
    coordinator = _coordinator()

    await coordinator.async_set_temp_unit(_DEVICE, 1)

    assert _ble_call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_TEMP_UNIT: 1,
    }


@pytest.mark.asyncio
async def test_reboot_device_sends_reboot_flag() -> None:
    """Reboot sends the reboot=1 flag via the BLE-first router.

    Reboot is no longer cloud-MQTT-locked: it goes through
    ``_async_publish_command_ble_first`` so a local BLE session can carry
    it and cloud MQTT stays the fallback.
    """
    coordinator = _coordinator()

    await coordinator.async_reboot_device(_DEVICE)

    assert _ble_call(coordinator).kwargs["body_fields"] == {coord_mod.FIELD_REBOOT: 1}


@pytest.mark.asyncio
async def test_bind_smart_part_sends_accessory_sn() -> None:
    """Binding a smart accessory carries the accessory serial in the body."""
    coordinator = _coordinator()

    await coordinator.async_bind_smart_part(_DEVICE, "ACC-1")

    assert _plain_call(coordinator).kwargs["body_fields"] == {"sn": "ACC-1"}


@pytest.mark.asyncio
async def test_unbind_smart_part_sends_accessory_sn() -> None:
    """Unbinding a smart accessory carries the accessory serial in the body."""
    coordinator = _coordinator()

    await coordinator.async_unbind_smart_part(_DEVICE, "ACC-2")

    assert _plain_call(coordinator).kwargs["body_fields"] == {"sn": "ACC-2"}


@pytest.mark.asyncio
async def test_set_ct_phase_requires_serial() -> None:
    """A missing CT serial is a HomeAssistantError before any publish."""
    coordinator = _coordinator()

    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_ct_phase(_DEVICE, "", 1)


@pytest.mark.asyncio
async def test_set_ct_phase_rejects_out_of_range_phase() -> None:
    """A phase outside 1..4 is rejected."""
    coordinator = _coordinator()

    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_ct_phase(_DEVICE, "CT-1", 9)


@pytest.mark.asyncio
async def test_set_ct_phase_sends_valid_phase() -> None:
    """A valid CT phase publishes the sub-device control body."""
    coordinator = _coordinator()

    await coordinator.async_set_ct_phase(_DEVICE, "CT-1", 4)

    body = _ble_call(coordinator).kwargs["body_fields"]
    assert body[coord_mod.FIELD_DEVICE_SN] == "CT-1"
    assert body[coord_mod.FIELD_SCHE_PHASE] == 4


@pytest.mark.asyncio
async def test_set_smart_plug_switch_on_patches_state() -> None:
    """Toggling a plug on sends sysSwitch=1 and optimistically patches it."""
    coordinator = _coordinator()
    patch = MagicMock()
    cast("Any", coordinator)._apply_local_smart_plug_switch_patch = patch  # ruff: ignore[private-member-access]

    await coordinator.async_set_smart_plug_switch(_DEVICE, plug_sn="P1", on=True)

    body = _ble_call(coordinator).kwargs["body_fields"]
    assert body[coord_mod.FIELD_DEVICE_SN] == "P1"
    assert body[coord_mod.FIELD_SYS_SWITCH] == 1
    patch.assert_called_once_with(_DEVICE, "P1", True)


@pytest.mark.asyncio
async def test_set_breaker_switch_off_sends_zero() -> None:
    """Toggling a breaker off sends sw=0 with the breaker index."""
    coordinator = _coordinator()
    cast("Any", coordinator)._apply_local_breaker_switch_patch = MagicMock()  # ruff: ignore[private-member-access]

    await coordinator.async_set_breaker_switch(_DEVICE, "2", on=False)

    body = _ble_call(coordinator).kwargs["body_fields"]
    assert body[coord_mod.FIELD_IDX] == 2
    assert body[coord_mod.FIELD_SW] == 0


@pytest.mark.asyncio
async def test_set_smart_plug_priority_patches_priority() -> None:
    """Enabling plug priority sends socketPri=1 and mirrors it into the plug."""
    coordinator = _coordinator()
    patch = MagicMock()
    cast("Any", coordinator)._apply_local_smart_plug_patch = patch  # ruff: ignore[private-member-access]

    await coordinator.async_set_smart_plug_priority(_DEVICE, plug_sn="P1", enabled=True)

    body = _ble_call(coordinator).kwargs["body_fields"]
    assert body[coord_mod.FIELD_SOCKET_PRIORITY] == 1
    patch.assert_called_once_with(_DEVICE, "P1", {coord_mod.FIELD_SOCKET_PRIORITY: 1})


@pytest.mark.asyncio
async def test_set_shelly_cloud_switch_uses_cloud_api() -> None:
    """Shelly Cloud sockets are controlled through the cloud API, not BLE."""
    coordinator = _coordinator()
    cast("Any", coordinator).api = MagicMock()
    cast("Any", coordinator).api.async_control_shelly_device = AsyncMock()
    cast("Any", coordinator)._apply_local_smart_plug_switch_patch = MagicMock()  # ruff: ignore[private-member-access]

    await coordinator.async_set_shelly_cloud_switch(
        _DEVICE,
        shelly_device_id="SH-1",
        on=True,
    )

    cast("Any", coordinator).api.async_control_shelly_device.assert_awaited_once()
    call = cast("Any", coordinator).api.async_control_shelly_device.await_args
    assert call.args[0] == "SH-1"
    assert call.kwargs["action"] == coord_mod.SHELLY_CONTROL_ACTION_ON


@pytest.mark.asyncio
async def test_read_device_schedule_rejects_unknown_task_type() -> None:
    """An unsupported task type is a ValueError before dispatch."""
    coordinator = _coordinator()
    cast("Any", coordinator).async_send_device_schedule = AsyncMock()

    with pytest.raises(ValueError, match="Unsupported task_type"):
        await coordinator.async_read_device_schedule(_DEVICE, task_type=99)


@pytest.mark.asyncio
async def test_read_device_schedule_adds_plug_sn_for_socket_task() -> None:
    """The smart-plug timer task type carries the plug serial in the body."""
    coordinator = _coordinator()
    send = AsyncMock()
    cast("Any", coordinator).async_send_device_schedule = send

    await coordinator.async_read_device_schedule(
        _DEVICE,
        task_type=coord_mod.TIMER_TASK_TYPE_SMART_PLUG,
        plug_sn="P9",
    )

    call = send.await_args
    assert call is not None
    body = call.kwargs["body"]
    assert body[coord_mod.FIELD_DEVICE_SN] == "P9"
    assert body[coord_mod.FIELD_TASK_TYPE] == coord_mod.TIMER_TASK_TYPE_SMART_PLUG
