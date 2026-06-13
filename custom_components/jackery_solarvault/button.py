"""Button platform for Jackery SolarVault."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .const import (
    ACTION_ID_GET_DEVICE_OTA_VERSION,
    ACTION_ID_GET_TIME_ZONE,
    ACTION_ID_PORTABLE_CURRENT_CHARGE_PLAN,
    ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
    ACTION_ID_PORTABLE_GET_ELECTRICITY_DATA_COUNT,
    ACTION_ID_PORTABLE_GET_PEAKS_TROUGHS,
    ACTION_ID_PORTABLE_GET_POWER_PACK_LIST,
    ACTION_ID_PORTABLE_GET_WIFI_CONFIG,
    ACTION_ID_PORTABLE_POWER_OFF,
    ACTION_ID_PORTABLE_POWER_PACK_BLINK,
    ACTION_ID_PORTABLE_READ_DEVICE_INFO,
    ACTION_ID_PORTABLE_READ_SUB_CT,
    ACTION_ID_PORTABLE_READ_WIFI_LIST,
    ACTION_ID_PORTABLE_RESTART,
    ACTION_ID_PORTABLE_SEND_TIME_ZONE,
    ACTION_ID_PORTABLE_SYNC_MQTT_INFO,
    ACTION_ID_QUERY_COMBINE_DATA,
    ACTION_ID_QUERY_DEVICE_PROPERTY,
    ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
    ACTION_ID_QUERY_WIFI_CONFIG,
    ACTION_ID_READ_WIFI_LIST,
    ACTION_ID_SEND_TIME_ZONE,
    ACTION_ID_SUBDEVICE_3014,
    ACTION_ID_SUBDEVICE_3031,
    ACTION_ID_SUBDEVICE_3032,
    ACTION_ID_SUBDEVICE_3033,
    ACTION_ID_SUBDEVICE_3037,
    ACTION_ID_SYNC_MQTT_CONNECT_INFO,
    DOMAIN,
    FIELD_ALERT_ID,
    FIELD_CMD,
    FIELD_DEVICE_SN,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_END_TS,
    FIELD_MANUAL,
    FIELD_MESSAGE_TYPE,
    FIELD_REBOOT,
    FIELD_SN,
    FIELD_START_TS,
    FIELD_STATUS,
    FIELD_STORM,
    MQTT_CMD_GET_DEVICE_OTA_VERSION,
    MQTT_CMD_GET_TIME_ZONE,
    MQTT_CMD_QUERY_COMBINE_DATA,
    MQTT_CMD_QUERY_DEVICE_PROPERTY,
    MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_QUERY_WIFI_CONFIG,
    MQTT_CMD_READ_WIFI_LIST,
    MQTT_CMD_SEND_TIME_ZONE,
    MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
    MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
    MQTT_MESSAGE_QUERY_COMBINE_DATA,
    MQTT_MESSAGE_QUERY_CURRENT_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
    MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_QUERY_TOU_SCHEDULE,
    MQTT_MESSAGE_QUERY_WIFI_CONFIG,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_WEATHER_PLAN,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_COMBO,
    SUBDEVICE_DEV_TYPE_CT,
    SUBDEVICE_DEV_TYPE_METER_HEAD,
    SUBDEVICE_DEV_TYPE_SOCKET,
    TIMER_TASK_TYPE_CUSTOM_MODE,
    TIMER_TASK_TYPE_SMART_PLUG,
    TIMER_TASK_TYPE_TIME_ELEC,
)
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import append_unique_entity, coordinator_entity_signature, sorted_smart_plugs

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import JackeryConfigEntry

# Limit concurrent control-write/update calls. This is a setter platform:
# writes go to the cloud and to MQTT. Serializing keeps the queue depth on
# the broker bounded and prevents reordering of `DevicePropertyChange`
# commands per HA dev guidance for write-heavy platforms.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


QueryButtonAction = Callable[[JackerySolarVaultCoordinator, str], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class JackeryQueryButtonDescription:
    """Metadata for a documented app read/query command."""

    key: str
    translation_key: str
    icon: str
    action: QueryButtonAction
    message_type: str
    action_id: int
    cmd: int
    dev_type: int | None = None


async def _query_system_info(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Request system information for a device via the coordinator."""
    await coordinator.async_query_system_info(device_id)


async def _query_device_info(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Request an updated device information payload for the specified device."""
    await coordinator.async_query_device_info(device_id)


async def _query_wifi_list(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Request the device's configured and available Wi‑Fi networks.

    Parameters:
        device_id (str): Identifier of the target device to query.
    """  # noqa: RUF002
    await coordinator.async_query_wifi_list(device_id)


async def _get_time_zone(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Request the coordinator to query the device's configured time zone.

    Parameters:
        device_id (str): Identifier of the device whose time zone will be queried.
    """
    await coordinator.async_get_time_zone(device_id)


async def _send_time_zone(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Send the configured time zone to the given device.
    
    Parameters:
        device_id (str): Identifier of the target device.
    """
    await coordinator.async_send_time_zone(device_id)


async def _sync_mqtt_connect_info(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Request the coordinator to synchronize MQTT connection information for the given device.
    
    """
    await coordinator.async_sync_mqtt_connect_info(device_id)


async def _query_device_ota_version(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Request the OTA firmware version for the specified device.
    
    Parameters:
        device_id (str): Unique identifier of the target device.
    """
    await coordinator.async_query_device_ota_version(device_id)


async def _query_third_party_mqtt_config(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Request the device's third-party MQTT configuration via the coordinator.

    Parameters:
        device_id (str): The identifier of the device whose third-party MQTT configuration should be queried.
    """
    await coordinator.async_query_third_party_mqtt_config(device_id)


async def _query_wifi_config(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Request the Wi-Fi configuration for the specified device through the coordinator.
    
    Parameters:
        device_id (str): Coordinator-managed device identifier.
    """
    await coordinator.async_query_wifi_config(device_id)


async def _query_battery_packs(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Request an updated battery-packs report for the specified device from the coordinator.

    Parameters:
        coordinator (JackerySolarVaultCoordinator): Coordinator instance to perform the query.
        device_id (str): Identifier of the target device.
    """
    await coordinator.async_query_battery_packs(device_id)


async def _query_smart_meter(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Trigger a smart meter query for the specified device."""
    await coordinator.async_query_smart_meter(device_id)


async def _query_meter_heads(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Trigger a meter-head query for the specified device."""
    await coordinator.async_query_meter_heads(device_id)


async def _query_smart_plugs(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Request the device's smart-plug inventory from the coordinator.
    
    Triggers the coordinator to fetch and update the smart-plug list for the given device.
    """
    await coordinator.async_query_smart_plugs(device_id)


async def _query_subdevice_combo(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Trigger the coordinator to query combined subdevice information for the specified device.

    Parameters:
        coordinator (JackerySolarVaultCoordinator): Coordinator responsible for communicating with the device.
        device_id (str): Identifier of the device whose subdevice combo should be queried.
    """
    await coordinator.async_query_subdevice_combo(device_id)


# --- Portable / Explorer powerstation actions --------------------------------


async def _portable_restart(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Restart a portable Explorer device by sending the portable reboot command.
    
    Parameters:
        coordinator (JackerySolarVaultCoordinator): Coordinator used to send the command.
        device_id (str): Identifier of the target portable device.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_RESTART,
        cmd=45,
        body_fields={FIELD_REBOOT: 1},
    )


async def _portable_power_off(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Shuts down (powers off) the specified portable Explorer device.
    
    Sends the portable power-off command for the given device identifier.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_POWER_OFF,
        cmd=46,
        body_fields={FIELD_REBOOT: 2},
    )


async def _portable_power_pack_blink(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Blink the power pack LEDs on the specified portable device.
    
    Sends a portable command to request the device blink its power pack LEDs.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_POWER_PACK_BLINK,
        cmd=39,
        body_fields={},
    )


async def _portable_read_device_info(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Request device information from a portable (Explorer) device via the coordinator.
    
    Parameters:
        device_id (str): Identifier of the target portable device.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_READ_DEVICE_INFO,
        cmd=6,
        body_fields={},
    )


async def _portable_read_wifi_list(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Request the portable device's available WiFi networks.
    
    Sends a portable command to query the device WiFi list (msgId=5, bleMsgType=1).
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_READ_WIFI_LIST,
        cmd=5,
        body_fields={},
    )


async def _portable_get_power_pack_list(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Request the portable device's list of power/battery packs.
    
    Parameters:
        coordinator: The integration coordinator managing device communication.
        device_id (str): Identifier of the target portable device.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_GET_POWER_PACK_LIST,
        cmd=8,
        body_fields={},
    )


async def _portable_get_electricity_data_count(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Request the portable device's stored electricity data count.
    
    Sends a portable command via the coordinator to query how many electricity data records the portable device holds (uses action_id ACTION_ID_PORTABLE_GET_ELECTRICITY_DATA_COUNT, cmd=9).
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_GET_ELECTRICITY_DATA_COUNT,
        cmd=9,
        body_fields={},
    )


async def _portable_send_time_zone(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Send the coordinator's configured time zone to the specified portable device.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_SEND_TIME_ZONE,
        cmd=25,
        body_fields={},
    )


async def _portable_sync_mqtt_info(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Synchronize MQTT connection information on a portable device.
    
    Sends the appropriate portable command through the coordinator to update the device's MQTT connection settings.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_SYNC_MQTT_INFO,
        cmd=50,
        body_fields={},
    )


async def _portable_get_wifi_config(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Query portable WiFi config (msgId=52, bleMsgType=124)."""
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_GET_WIFI_CONFIG,
        cmd=52,
        body_fields={},
    )


async def _portable_get_charge_plan(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Request the portable unit's charge/discharge plan.
    
    Sends the portable query for the device's electricity strategy (message type MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY, command 26).
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
        cmd=26,
        body_fields={},
        message_type=MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
    )


async def _portable_current_charge_plan(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Query portable current charge/discharge plan (msgId=30)."""
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_CURRENT_CHARGE_PLAN,
        cmd=30,
        body_fields={},
        message_type=MQTT_MESSAGE_QUERY_CURRENT_ELECTRICITY_STRATEGY,
    )


async def _portable_get_peaks_troughs(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Request the portable device's time-of-use (TOU) peaks and troughs schedule.
    
    Parameters:
        device_id (str): Identifier of the target portable device.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_GET_PEAKS_TROUGHS,
        cmd=43,
        body_fields={},
        message_type=MQTT_MESSAGE_QUERY_TOU_SCHEDULE,
    )


async def _portable_read_sub_ct(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """
    Request the portable device to read sub-device CT (current transformer) properties.
    
    This triggers sending the appropriate portable query command for sub-device CT to the coordinator.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_READ_SUB_CT,
        cmd=51,
        body_fields={},
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
    )


QUERY_BUTTON_DESCRIPTIONS: tuple[JackeryQueryButtonDescription, ...] = (
    JackeryQueryButtonDescription(
        key="refresh_system_info",
        translation_key="refresh_system_info",
        icon="mdi:home-lightning-bolt",
        action=_query_system_info,
        message_type=MQTT_MESSAGE_QUERY_COMBINE_DATA,
        action_id=ACTION_ID_QUERY_COMBINE_DATA,
        cmd=MQTT_CMD_QUERY_COMBINE_DATA,
    ),
    JackeryQueryButtonDescription(
        key="refresh_device_info",
        translation_key="refresh_device_info",
        icon="mdi:database-refresh",
        action=_query_device_info,
        message_type=MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
        action_id=ACTION_ID_QUERY_DEVICE_PROPERTY,
        cmd=MQTT_CMD_QUERY_DEVICE_PROPERTY,
    ),
    JackeryQueryButtonDescription(
        key="refresh_wifi_list",
        translation_key="refresh_wifi_list",
        icon="mdi:wifi-refresh",
        action=_query_wifi_list,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_READ_WIFI_LIST,
        cmd=MQTT_CMD_READ_WIFI_LIST,
    ),
    JackeryQueryButtonDescription(
        key="refresh_time_zone",
        translation_key="refresh_time_zone",
        icon="mdi:map-clock",
        action=_get_time_zone,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_GET_TIME_ZONE,
        cmd=MQTT_CMD_GET_TIME_ZONE,
    ),
    JackeryQueryButtonDescription(
        key="sync_time_zone",
        translation_key="sync_time_zone",
        icon="mdi:clock-check",
        action=_send_time_zone,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_SEND_TIME_ZONE,
        cmd=MQTT_CMD_SEND_TIME_ZONE,
    ),
    JackeryQueryButtonDescription(
        key="sync_cloud_mqtt_info",
        translation_key="sync_cloud_mqtt_info",
        icon="mdi:cloud-sync",
        action=_sync_mqtt_connect_info,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_SYNC_MQTT_CONNECT_INFO,
        cmd=MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
    ),
    JackeryQueryButtonDescription(
        key="refresh_device_ota_version",
        translation_key="refresh_device_ota_version",
        icon="mdi:update",
        action=_query_device_ota_version,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_GET_DEVICE_OTA_VERSION,
        cmd=MQTT_CMD_GET_DEVICE_OTA_VERSION,
    ),
    JackeryQueryButtonDescription(
        key="refresh_third_party_mqtt_config",
        translation_key="refresh_third_party_mqtt_config",
        icon="mdi:mqtt",
        action=_query_third_party_mqtt_config,
        message_type=MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
        action_id=ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
        cmd=MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
    ),
    JackeryQueryButtonDescription(
        key="refresh_wifi_config",
        translation_key="refresh_wifi_config",
        icon="mdi:wifi-cog",
        action=_query_wifi_config,
        message_type=MQTT_MESSAGE_QUERY_WIFI_CONFIG,
        action_id=ACTION_ID_QUERY_WIFI_CONFIG,
        cmd=MQTT_CMD_QUERY_WIFI_CONFIG,
    ),
    JackeryQueryButtonDescription(
        key="refresh_battery_packs",
        translation_key="refresh_battery_packs",
        icon="mdi:battery-sync",
        action=_query_battery_packs,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3014,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    ),
    JackeryQueryButtonDescription(
        key="refresh_smart_meter",
        translation_key="refresh_smart_meter",
        icon="mdi:meter-electric",
        action=_query_smart_meter,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3031,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_CT,
    ),
    JackeryQueryButtonDescription(
        key="refresh_meter_heads",
        translation_key="refresh_meter_heads",
        icon="mdi:meter-electric-outline",
        action=_query_meter_heads,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3033,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_METER_HEAD,
    ),
    JackeryQueryButtonDescription(
        key="refresh_smart_plugs",
        translation_key="refresh_smart_plugs",
        icon="mdi:power-plug-battery",
        action=_query_smart_plugs,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3032,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_SOCKET,
    ),
    JackeryQueryButtonDescription(
        key="refresh_subdevice_combo",
        translation_key="refresh_subdevice_combo",
        icon="mdi:devices",
        action=_query_subdevice_combo,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3037,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_COMBO,
    ),
    # --- Portable / Explorer powerstation buttons ---
    JackeryQueryButtonDescription(
        key="portable_restart",
        translation_key="portable_restart",
        icon="mdi:restart",
        action=_portable_restart,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_RESTART,
        cmd=45,
    ),
    JackeryQueryButtonDescription(
        key="portable_power_off",
        translation_key="portable_power_off",
        icon="mdi:power",
        action=_portable_power_off,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_POWER_OFF,
        cmd=46,
    ),
    JackeryQueryButtonDescription(
        key="portable_power_pack_blink",
        translation_key="portable_power_pack_blink",
        icon="mdi:led-outline",
        action=_portable_power_pack_blink,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_POWER_PACK_BLINK,
        cmd=39,
    ),
    JackeryQueryButtonDescription(
        key="portable_refresh_device_info",
        translation_key="portable_refresh_device_info",
        icon="mdi:database-refresh",
        action=_portable_read_device_info,
        message_type=MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
        action_id=ACTION_ID_PORTABLE_READ_DEVICE_INFO,
        cmd=6,
    ),
    JackeryQueryButtonDescription(
        key="portable_refresh_wifi_list",
        translation_key="portable_refresh_wifi_list",
        icon="mdi:wifi-refresh",
        action=_portable_read_wifi_list,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_READ_WIFI_LIST,
        cmd=5,
    ),
    JackeryQueryButtonDescription(
        key="portable_refresh_battery_packs",
        translation_key="portable_refresh_battery_packs",
        icon="mdi:battery-sync",
        action=_portable_get_power_pack_list,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_GET_POWER_PACK_LIST,
        cmd=8,
    ),
    JackeryQueryButtonDescription(
        key="portable_refresh_electricity_count",
        translation_key="portable_refresh_electricity_count",
        icon="mdi:counter",
        action=_portable_get_electricity_data_count,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_GET_ELECTRICITY_DATA_COUNT,
        cmd=9,
    ),
    JackeryQueryButtonDescription(
        key="portable_sync_time_zone",
        translation_key="portable_sync_time_zone",
        icon="mdi:clock-check",
        action=_portable_send_time_zone,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_SEND_TIME_ZONE,
        cmd=25,
    ),
    JackeryQueryButtonDescription(
        key="portable_sync_mqtt_info",
        translation_key="portable_sync_mqtt_info",
        icon="mdi:cloud-sync",
        action=_portable_sync_mqtt_info,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_SYNC_MQTT_INFO,
        cmd=50,
    ),
    JackeryQueryButtonDescription(
        key="portable_refresh_wifi_config",
        translation_key="portable_refresh_wifi_config",
        icon="mdi:wifi-cog",
        action=_portable_get_wifi_config,
        message_type=MQTT_MESSAGE_QUERY_WIFI_CONFIG,
        action_id=ACTION_ID_PORTABLE_GET_WIFI_CONFIG,
        cmd=52,
    ),
    JackeryQueryButtonDescription(
        key="portable_get_charge_plan",
        translation_key="portable_get_charge_plan",
        icon="mdi:battery-clock",
        action=_portable_get_charge_plan,
        message_type=MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
        action_id=ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
        cmd=26,
    ),
    JackeryQueryButtonDescription(
        key="portable_current_charge_plan",
        translation_key="portable_current_charge_plan",
        icon="mdi:clock-outline",
        action=_portable_current_charge_plan,
        message_type=MQTT_MESSAGE_QUERY_CURRENT_ELECTRICITY_STRATEGY,
        action_id=ACTION_ID_PORTABLE_CURRENT_CHARGE_PLAN,
        cmd=30,
    ),
    JackeryQueryButtonDescription(
        key="portable_get_peaks_troughs",
        translation_key="portable_get_peaks_troughs",
        icon="mdi:chart-line",
        action=_portable_get_peaks_troughs,
        message_type=MQTT_MESSAGE_QUERY_TOU_SCHEDULE,
        action_id=ACTION_ID_PORTABLE_GET_PEAKS_TROUGHS,
        cmd=43,
    ),
    JackeryQueryButtonDescription(
        key="portable_refresh_sub_ct",
        translation_key="portable_refresh_sub_ct",
        icon="mdi:meter-electric",
        action=_portable_read_sub_ct,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_PORTABLE_READ_SUB_CT,
        cmd=51,
    ),
)


def _storm_alert_id(alert: object) -> str | None:
    """Extract the storm alert's alertId from an alert mapping.

    Parameters:
        alert (object): The alert object, expected to be a mapping (dict) containing the alert identifier.

    Returns:
        str | None: The alert id as a string when present and not empty, otherwise `None`.
    """
    if not isinstance(alert, dict):
        return None
    raw = alert.get(FIELD_ALERT_ID)
    if raw in {None, ""}:
        return None
    return str(raw)


def _storm_alerts(weather_plan: object) -> list[dict[str, Any]]:
    """Extract active storm alert dictionaries from a weather plan that have stable alert IDs.

    Parameters:
        weather_plan (object): The weather plan payload (expected to be a dict) which may contain a list of storm alerts under FIELD_STORM.

    Returns:
        list[dict[str, Any]]: List of alert dictionaries that include a stable alert id; returns an empty list if the input is invalid or no matching alerts exist.
    """
    if not isinstance(weather_plan, dict):
        return []
    storm = weather_plan.get(FIELD_STORM)
    if not isinstance(storm, list):
        return []
    return [
        alert
        for alert in storm
        if isinstance(alert, dict) and _storm_alert_id(alert) is not None
    ]


def _smart_plug_device_sn(plug: object) -> str | None:
    """
    Extract the stable serial number for a smart plug from a plug mapping.
    
    Checks the plug mapping for FIELD_DEVICE_SN, then FIELD_DEV_SN, then FIELD_SN and returns the first non-empty value found.
    
    Parameters:
        plug (object): A mapping-like object representing a smart plug (typically a dict).
    
    Returns:
        str: The serial number as a string when available, `None` otherwise.
    """
    if not isinstance(plug, dict):
        return None
    raw = plug.get(FIELD_DEVICE_SN) or plug.get(FIELD_DEV_SN) or plug.get(FIELD_SN)
    if raw in {None, ""}:
        return None
    return str(raw)


async def async_setup_entry(  # noqa: RUF029  # HA awaits this entry point
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up app-command Button entities for devices in the config entry.

    Create a JackeryRebootButton for each coordinator-managed device that either reports support for advanced features or exposes the reboot property, avoid registering duplicate entities, and only add entities when the coordinator-derived device signature changes. Registers a coordinator listener to update discovery when the signature changes.

    Parameters:
        entry (JackeryConfigEntry): Config entry whose runtime_data contains the integration coordinator.
        async_add_entities (AddEntitiesCallback): Callback to register new ButtonEntity instances with Home Assistant.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[ButtonEntity], entity: ButtonEntity) -> None:
        """
        Append a ButtonEntity to the provided list if its unique identifier has not already been recorded.
        
        Records the entity's unique id to prevent registering duplicate button entities.
        
        Parameters:
            entities (list[ButtonEntity]): List to append the entity to when unique.
            entity (ButtonEntity): Button entity to check and append.
        """
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="button", logger=_LOGGER
        )

    def _collect_entities() -> list[ButtonEntity]:
        """Collect ButtonEntity instances for all devices currently tracked by the coordinator.

        Discovers and constructs unique button entities to expose for each device, including:
        - documented query buttons defined in QUERY_BUTTON_DESCRIPTIONS,
        - a weather-plan refresh button,
        - schedule-read buttons for custom and time-electricity tasks,
        - per-smart-plug schedule-read buttons when plugs with stable serials exist,
        - a reboot button when the device supports advanced features or advertises reboot,
        - delete-storm-alert buttons for each active storm alert with a stable alert id.

        Returns:
            list[ButtonEntity]: List of unique ButtonEntity instances ready for registration.
        """
        entities: list[ButtonEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload.get(PAYLOAD_PROPERTIES) or {}
            for description in QUERY_BUTTON_DESCRIPTIONS:
                _append_unique(
                    entities,
                    JackeryQueryButton(coordinator, dev_id, description=description),
                )
            _append_unique(
                entities, JackeryRefreshWeatherPlanButton(coordinator, dev_id)
            )
            _append_unique(
                entities,
                JackeryReadScheduleButton(
                    coordinator,
                    dev_id,
                    task_type=TIMER_TASK_TYPE_CUSTOM_MODE,
                    key_suffix="read_custom_mode_schedule",
                    translation_key="read_custom_mode_schedule",
                    icon="mdi:calendar-clock",
                ),
            )
            _append_unique(
                entities,
                JackeryReadScheduleButton(
                    coordinator,
                    dev_id,
                    task_type=TIMER_TASK_TYPE_TIME_ELEC,
                    key_suffix="read_time_electricity_schedule",
                    translation_key="read_time_electricity_schedule",
                    icon="mdi:calendar-sync",
                ),
            )
            if coordinator.device_supports_advanced(dev_id) or FIELD_REBOOT in props:
                _append_unique(entities, JackeryRebootButton(coordinator, dev_id))
            for plug in sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS)):
                plug_sn = _smart_plug_device_sn(plug)
                if plug_sn is None:
                    continue
                _append_unique(
                    entities,
                    JackeryReadScheduleButton(
                        coordinator,
                        dev_id,
                        task_type=TIMER_TASK_TYPE_SMART_PLUG,
                        key_suffix=f"smart_plug_{plug_sn}_read_schedule",
                        translation_key="read_smart_plug_schedule",
                        icon="mdi:calendar-clock",
                        plug_sn=plug_sn,
                    ),
                )
            for alert in _storm_alerts(payload.get(PAYLOAD_WEATHER_PLAN)):
                alert_id = _storm_alert_id(alert)
                if alert_id is None:
                    continue
                _append_unique(
                    entities,
                    JackeryDeleteStormAlertButton(
                        coordinator,
                        dev_id,
                        alert_id=alert_id,
                    ),
                )
        return entities

    last_signature: tuple[Any, ...] = ()

    def _add_new_entities() -> None:
        """Register new button entities when the coordinator's entity signature changes.

        If the current coordinator-derived signature differs from the previously cached signature, update the cache and call the platform entity addition callback with any newly discovered entities.
        """
        nonlocal last_signature
        sig = coordinator_entity_signature(coordinator.data)
        if sig == last_signature:
            return
        last_signature = sig
        entities = _collect_entities()
        if entities:
            async_add_entities(entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class JackeryQueryButton(JackeryEntity, ButtonEntity):
    """Run one documented app read/query command."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        description: JackeryQueryButtonDescription,
    ) -> None:
        """
        Create a query button entity for a specific device from a query button description.
        
        Parameters:
            description (JackeryQueryButtonDescription): Metadata for the button; `description.key` is used as the entity key and `description.translation_key` / `description.icon` are applied to the entity.
        """
        super().__init__(coordinator, device_id, description.key)
        self._query_description = description
        self._attr_translation_key = description.translation_key
        self._attr_icon = description.icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the app-command metadata for this query button.

        Returns:
            dict[str, Any]: Mapping with the command metadata. Keys:
                - FIELD_MESSAGE_TYPE: the MQTT/app message type for the command
                - "actionId": the action identifier sent with the command
                - FIELD_CMD: the command value
                - FIELD_DEV_TYPE: the device type (included only when available)
        """
        description = self._query_description
        attrs: dict[str, Any] = {
            FIELD_MESSAGE_TYPE: description.message_type,
            "actionId": description.action_id,
            FIELD_CMD: description.cmd,
        }
        if description.dev_type is not None:
            attrs[FIELD_DEV_TYPE] = description.dev_type
        return attrs

    def _raise_action_error(self, error: object) -> None:
        """
        Raise a Home Assistant translated "action failed" error for this button.
        
        Raises:
            HomeAssistantError: Error with translation_domain=DOMAIN, translation_key="entity_action_failed",
            and translation_placeholders containing `entity`, `device_id`, and `error`.
        """
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": str(self._attr_translation_key),
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def async_press(self) -> None:
        """
        Execute the query action defined by this entity's query description.
        
        Calls the configured action callable for this entity's device. Propagates ConfigEntryAuthFailed; if a HomeAssistantError with a `translation_key` is raised it is propagated unchanged; any other exception is converted and raised via this entity's `_raise_action_error` helper.
        """
        try:
            await self._query_description.action(self.coordinator, self._device_id)
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:  # noqa: BLE001
            self._raise_action_error(err)


class JackeryRebootButton(JackeryEntity, ButtonEntity):
    """Restart the SolarVault device via PROTOCOL.md §4 reboot command."""

    _attr_translation_key = "reboot_device"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Create a button entity that triggers a reboot of the specified device.

        Parameters:
            coordinator (JackerySolarVaultCoordinator): Coordinator that manages device state and provides control actions.
            device_id (str): Unique identifier of the target device for the reboot action.
        """
        super().__init__(coordinator, device_id, "reboot_device")

    def _raise_action_error(self, error: object) -> None:
        """
        Raise a translatable HomeAssistantError for a failed reboot action.
        
        The exception uses translation_domain=DOMAIN and translation_key="entity_action_failed" and includes translation placeholders:
        - "entity": "reboot_device"
        - "device_id": this entity's device id
        - "error": str(error)
        """
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": "reboot_device",
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def async_press(self) -> None:
        """
        Reboot the associated device and request the coordinator to refresh its data.
        
        If authentication fails, the original ConfigEntryAuthFailed is propagated. A HomeAssistantError that already has a `translation_key` is re-raised unchanged; all other exceptions are converted and surfaced via `_raise_action_error`.
        """
        try:
            await self.coordinator.async_reboot_device(self._device_id)
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:  # noqa: BLE001
            self._raise_action_error(err)


class JackeryRefreshWeatherPlanButton(JackeryEntity, ButtonEntity):
    """Query the app weather/storm plan via ``QueryWeatherPlan``."""

    _attr_translation_key = "refresh_weather_plan"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:weather-cloudy-clock"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """
        Create the button entity that triggers querying the device weather and storm plan.
        """
        super().__init__(coordinator, device_id, "refresh_weather_plan")

    def _raise_action_error(self, error: object) -> None:
        """Raise a translated Home AssistantError for a failed entity action on the target device.

        Uses the integration translation domain and the "entity_action_failed" translation key.
        Placeholders set in the raised error:
        - "entity": "refresh_weather_plan"
        - "device_id": the target device identifier (self._device_id)
        - "error": the string representation of `error`

        Parameters:
            error (object): The original error to include in the translation placeholders.
        """
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": "refresh_weather_plan",
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def async_press(self) -> None:
        """
        Refresh the device weather/storm plan and trigger a coordinator refresh.
        
        Raises:
            ConfigEntryAuthFailed: If authentication with the config entry has failed.
            HomeAssistantError: If the action fails; if the caught error already has a `translation_key` it is re-raised unchanged, otherwise a `HomeAssistantError` is raised with a translation key indicating the entity action failed.
        """
        try:
            await self.coordinator.async_query_weather_plan(self._device_id)
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:  # noqa: BLE001
            self._raise_action_error(err)


class JackeryReadScheduleButton(JackeryEntity, ButtonEntity):
    """Read one app schedule bucket via ``DownloadDeviceSchedule``."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(  # noqa: PLR0913
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        task_type: int,
        key_suffix: str,
        translation_key: str,
        icon: str,
        plug_sn: str = "",
    ) -> None:
        """Create a button entity that triggers reading a specific schedule/task bucket from the device.

        Parameters:
            coordinator: Coordinator that manages device communication and state.
            device_id (str): Unique device identifier this button targets.
            task_type (int): Identifier of the schedule/task bucket to read (use the module's TIMER_TASK_TYPE_* constants).
            key_suffix (str): Suffix appended to the entity unique key to distinguish this schedule read button.
            translation_key (str): Translation key used for the button's name.
            icon (str): Material Design Icon name for the button.
            plug_sn (str, optional): Smart-plug device serial number to target when reading a plug-specific schedule; omit for device-level schedules.
        """
        super().__init__(coordinator, device_id, key_suffix)
        self._task_type = task_type
        self._plug_sn = plug_sn
        self._attr_translation_key = translation_key
        self._attr_icon = icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """
        Expose the schedule-read command metadata as entity attributes.
        
        Includes "taskType" and, when this button targets a specific smart plug, the plug's device serial under FIELD_DEVICE_SN.
        
        Returns:
            dict[str, Any]: Attributes dictionary containing "taskType" and optionally FIELD_DEVICE_SN.
        """
        attrs: dict[str, Any] = {"taskType": self._task_type}
        if self._plug_sn:
            attrs[FIELD_DEVICE_SN] = self._plug_sn
        return attrs

    def _raise_action_error(self, error: object) -> None:
        """
        Raise a Home Assistant translated "action failed" error for this button.
        
        Raises:
            HomeAssistantError: Error with translation_domain=DOMAIN, translation_key="entity_action_failed",
            and translation_placeholders containing `entity`, `device_id`, and `error`.
        """
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": str(self._attr_translation_key),
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def async_press(self) -> None:
        """Trigger a device schedule read for the configured task bucket and request a coordinator refresh.

        Raises:
            ConfigEntryAuthFailed: Re-raised when authentication has failed.
            HomeAssistantError: Re-raised unchanged if it already has a `translation_key`; other exceptions are converted into a translated `HomeAssistantError` indicating the entity action failed.
        """
        try:
            await self.coordinator.async_read_device_schedule(
                self._device_id,
                task_type=self._task_type,
                plug_sn=self._plug_sn,
            )
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:  # noqa: BLE001
            self._raise_action_error(err)


class JackeryDeleteStormAlertButton(JackeryEntity, ButtonEntity):
    """Delete one active app storm alert via ``CancelWeatherAlert``."""

    _attr_translation_key = "delete_storm_alert"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:weather-lightning-rainy"

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        alert_id: str,
    ) -> None:
        """Create a delete-storm-alert button entity bound to a specific alert id.

        Parameters:
            coordinator (JackerySolarVaultCoordinator): Coordinator managing device state and actions.
            device_id (str): Identifier of the device the alert belongs to.
            alert_id (str): Stable identifier of the storm alert; included in the entity's unique id.
        """
        super().__init__(coordinator, device_id, f"delete_storm_alert_{alert_id}")
        self._alert_id = alert_id

    @property
    def _alert(self) -> dict[str, Any]:
        """
        Finds the storm alert in the current payload that matches this button's alert id.
        
        Scans the entity payload's weather plan alerts and returns the alert dictionary whose stable alert id equals this button's stored alert id.
        
        Returns:
            dict[str, Any]: The matching alert dictionary, or an empty dict if no matching alert is present.
        """
        payload = self._payload
        if payload:
            for alert in _storm_alerts(payload.get(PAYLOAD_WEATHER_PLAN)):
                if _storm_alert_id(alert) == self._alert_id:
                    return alert
        return {}

    @property
    def available(self) -> bool:
        """Determine whether the delete storm alert button is currently available.

        The button is available only when the base entity is available and the targeted storm alert still exists.

        Returns:
            True if the base entity is available and the referenced storm alert exists, False otherwise.
        """
        return super().available and bool(self._alert)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes for the delete-storm-alert button.

        Includes the alert's ID under FIELD_ALERT_ID and, if present in the current alert, any of FIELD_START_TS, FIELD_END_TS, FIELD_STATUS, and FIELD_MANUAL.

        Returns:
            dict[str, Any]: Mapping containing `FIELD_ALERT_ID` and any of `FIELD_START_TS`, `FIELD_END_TS`, `FIELD_STATUS`, `FIELD_MANUAL` present on the alert.
        """
        attrs: dict[str, Any] = {FIELD_ALERT_ID: self._alert_id}
        alert = self._alert
        for key in (FIELD_START_TS, FIELD_END_TS, FIELD_STATUS, FIELD_MANUAL):
            if key in alert:
                attrs[key] = alert.get(key)
        return attrs

    def _raise_action_error(self, error: object) -> None:
        """Raise a localized Home AssistantError indicating the delete-storm-alert action failed.

        The error uses the integration translation domain and the `entity_action_failed` translation key.
        Placeholders provided: `entity` ("delete_storm_alert"), `device_id`, and `error`.

        Raises:
            HomeAssistantError: localized error for a failed entity action.
        """
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": "delete_storm_alert",
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def async_press(self) -> None:
        """
        Delete the associated storm alert and request a coordinator data refresh.
        
        Raises:
            ConfigEntryAuthFailed: If authentication with the config entry failed (re-raised).
            HomeAssistantError: If an error occurs; errors that already have a `translation_key` are re-raised, other exceptions are converted and raised via the entity's `_raise_action_error`.
        """
        try:
            await self.coordinator.async_delete_storm_alert(
                self._device_id,
                self._alert_id,
            )
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:  # noqa: BLE001
            self._raise_action_error(err)
