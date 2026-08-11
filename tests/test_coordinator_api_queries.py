"""Behavioral tests for the coordinator's device *query* command methods.

Query methods publish an app-style read frame (via the BLE-first / MQTT publish
helpers) whose response arrives asynchronously on a push topic. The meaningful
behavior is the command family selected — in particular the portable-vs-home
routing which swaps both the action id and the ble cmd — and any body value
derived from arguments. The publish transport is the mocked seam; the tests
assert the frame the coordinator would emit.
"""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import coordinator as coord_mod
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.exceptions import HomeAssistantError

_DEVICE = "573702884982521856"


def _coordinator(*, portable: bool = False) -> JackerySolarVaultCoordinator:
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.data = {}
    obj._async_publish_command_ble_first = AsyncMock()
    obj._apply_local_system_patch = MagicMock()
    obj._is_portable_device_id = MagicMock(return_value=portable)
    return coordinator


def _call(coordinator: JackerySolarVaultCoordinator) -> Any:
    return cast("Any", coordinator)._async_publish_command_ble_first.await_args


@pytest.mark.asyncio
async def test_query_system_info_uses_combine_family() -> None:
    """System info reads the combine-data snapshot."""
    coordinator = _coordinator()

    await coordinator.async_query_system_info(_DEVICE)

    call = _call(coordinator)
    assert call.kwargs["action_id"] == coord_mod.ACTION_ID_QUERY_COMBINE_DATA
    assert call.kwargs["body_fields"] == {}
    assert call.kwargs["ensure_mqtt"] is True


@pytest.mark.asyncio
async def test_query_system_info_can_skip_mqtt_ensure() -> None:
    """The ensure_mqtt flag is forwarded to the publish helper."""
    coordinator = _coordinator()

    await coordinator.async_query_system_info(_DEVICE, ensure_mqtt=False)

    assert _call(coordinator).kwargs["ensure_mqtt"] is False


@pytest.mark.asyncio
async def test_query_device_info_uses_property_family() -> None:
    """Device info reads the device-property snapshot."""
    coordinator = _coordinator()

    await coordinator.async_query_device_info(_DEVICE)

    assert _call(coordinator).kwargs["action_id"] == (
        coord_mod.ACTION_ID_QUERY_DEVICE_PROPERTY
    )


@pytest.mark.asyncio
async def test_query_wifi_list_uses_read_wifi_action() -> None:
    """Wi-Fi list uses the READ_WIFI_LIST action id."""
    coordinator = _coordinator()

    await coordinator.async_query_wifi_list(_DEVICE)

    assert _call(coordinator).kwargs["action_id"] == coord_mod.ACTION_ID_READ_WIFI_LIST


@pytest.mark.asyncio
async def test_query_battery_packs_selects_battery_dev_type() -> None:
    """Battery-pack query carries devType for battery packs."""
    coordinator = _coordinator()

    await coordinator.async_query_battery_packs(_DEVICE)

    assert _call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_DEV_TYPE: coord_mod.SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    }


@pytest.mark.asyncio
async def test_query_smart_meter_selects_ct_dev_type() -> None:
    """Smart-meter query carries the CT devType."""
    coordinator = _coordinator()

    await coordinator.async_query_smart_meter(_DEVICE)

    assert _call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_DEV_TYPE: coord_mod.SUBDEVICE_DEV_TYPE_CT,
    }


@pytest.mark.asyncio
async def test_query_meter_heads_selects_meter_head_dev_type() -> None:
    """Meter-head query carries the meter-head devType."""
    coordinator = _coordinator()

    await coordinator.async_query_meter_heads(_DEVICE)

    assert _call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_DEV_TYPE: coord_mod.SUBDEVICE_DEV_TYPE_METER_HEAD,
    }


@pytest.mark.asyncio
async def test_query_smart_plugs_selects_socket_dev_type() -> None:
    """Smart-plug query carries the socket devType."""
    coordinator = _coordinator()

    await coordinator.async_query_smart_plugs(_DEVICE)

    assert _call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_DEV_TYPE: coord_mod.SUBDEVICE_DEV_TYPE_SOCKET,
    }


@pytest.mark.asyncio
async def test_query_subdevice_combo_selects_combo_dev_type() -> None:
    """Combo query carries the combo devType."""
    coordinator = _coordinator()

    await coordinator.async_query_subdevice_combo(_DEVICE)

    assert _call(coordinator).kwargs["body_fields"] == {
        coord_mod.FIELD_DEV_TYPE: coord_mod.SUBDEVICE_DEV_TYPE_COMBO,
    }


@pytest.mark.asyncio
async def test_ota_version_home_device_uses_home_action() -> None:
    """A home device queries OTA version with the home action id."""
    coordinator = _coordinator(portable=False)

    await coordinator.async_query_device_ota_version(_DEVICE)

    assert _call(coordinator).kwargs["action_id"] == (
        coord_mod.ACTION_ID_GET_DEVICE_OTA_VERSION
    )


@pytest.mark.asyncio
async def test_ota_version_portable_device_uses_portable_action() -> None:
    """A portable device swaps to the portable OTA-version action id."""
    coordinator = _coordinator(portable=True)

    await coordinator.async_query_device_ota_version(_DEVICE)

    assert _call(coordinator).kwargs["action_id"] == (
        coord_mod.ACTION_ID_PORTABLE_OTA_VERSION
    )


@pytest.mark.asyncio
async def test_ota_total_page_forwards_page_count() -> None:
    """The total-page notify forwards the page count in the body."""
    coordinator = _coordinator()

    await coordinator.async_notify_device_ota_total_page(_DEVICE, total_pages=7)

    assert _call(coordinator).kwargs["body_fields"] == {"totalPages": 7}


@pytest.mark.asyncio
async def test_ota_page_data_forwards_page_index() -> None:
    """The page-data request forwards the requested page index."""
    coordinator = _coordinator()

    await coordinator.async_device_get_ota_page_data(_DEVICE, page_index=3)

    assert _call(coordinator).kwargs["body_fields"] == {"pageIndex": 3}


@pytest.mark.asyncio
async def test_query_wifi_config_portable_swaps_action() -> None:
    """Portable Wi-Fi config uses the portable action id."""
    coordinator = _coordinator(portable=True)

    await coordinator.async_query_wifi_config(_DEVICE)

    assert _call(coordinator).kwargs["action_id"] == (
        coord_mod.ACTION_ID_PORTABLE_GET_WIFI_CONFIG
    )


@pytest.mark.asyncio
async def test_query_weather_plan_uses_weather_family() -> None:
    """Weather-plan query uses the weather-plan message family."""
    coordinator = _coordinator()

    await coordinator.async_query_weather_plan(_DEVICE)

    assert _call(coordinator).kwargs["message_type"] == (
        coord_mod.MQTT_MESSAGE_QUERY_WEATHER_PLAN
    )


@pytest.mark.asyncio
async def test_sync_grid_standard_patches_local_and_sends_safety() -> None:
    """Grid-standard sync sends the safety code and mirrors it as a string."""
    coordinator = _coordinator()

    await coordinator.async_sync_grid_standard(_DEVICE, 5)

    body = _call(coordinator).kwargs["body_fields"]
    assert body[coord_mod.FIELD_SAFETY] == 5
    cast("Any", coordinator)._apply_local_system_patch.assert_called_once_with(
        _DEVICE,
        {coord_mod.FIELD_GRID_STANDARD: "5"},
    )


@pytest.mark.asyncio
async def test_sync_mqtt_connect_info_carries_broker_host() -> None:
    """MQTT connect-info sync carries the integration's broker host."""
    coordinator = _coordinator()

    await coordinator.async_sync_mqtt_connect_info(_DEVICE)

    body = _call(coordinator).kwargs["body_fields"]
    assert body[coord_mod.FIELD_HOST] == coord_mod.MQTT_HOST


@pytest.mark.asyncio
async def test_send_time_zone_invalid_zone_raises() -> None:
    """An unresolvable time-zone name is a HomeAssistantError."""
    coordinator = _coordinator()
    hass = MagicMock()
    hass.config.time_zone = "UTC"
    cast("Any", coordinator).hass = hass

    with pytest.raises(HomeAssistantError):
        await coordinator.async_send_time_zone(_DEVICE, timezone_name="Not/AZone")


@pytest.mark.asyncio
async def test_send_time_zone_patches_resolved_name() -> None:
    """A valid time zone is published and mirrored into the system cache."""
    coordinator = _coordinator()
    hass = MagicMock()
    hass.config.time_zone = "UTC"
    cast("Any", coordinator).hass = hass

    await coordinator.async_send_time_zone(_DEVICE, timezone_name="Europe/Berlin")

    body = _call(coordinator).kwargs["body_fields"]
    assert body[coord_mod.FIELD_TIMEZONE] == "Europe/Berlin"
    cast("Any", coordinator)._apply_local_system_patch.assert_called_once_with(
        _DEVICE,
        {coord_mod.FIELD_TIMEZONE: "Europe/Berlin"},
    )


@pytest.mark.asyncio
async def test_query_third_party_mqtt_config_uses_query_family() -> None:
    """The third-party MQTT read uses the query config family with empty body."""
    coordinator = _coordinator()

    await coordinator.async_query_third_party_mqtt_config(_DEVICE)

    call = _call(coordinator)
    assert call.kwargs["action_id"] == (
        coord_mod.ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG
    )
    assert call.kwargs["body_fields"] == {}
