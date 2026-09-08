"""Button descriptions for Jackery SolarVault integration.

Holds the documented app read/query command catalogue. The platform module
keeps ``async_press`` because a query fans out over MQTT and HTTP in
parallel — orchestration that belongs on the entity, not in a description.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntityDescription

from ..const import (
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
    FIELD_REBOOT,
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
    PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_COMBO,
    SUBDEVICE_DEV_TYPE_CT,
    SUBDEVICE_DEV_TYPE_METER_HEAD,
    SUBDEVICE_DEV_TYPE_SOCKET,
)
from ..entity import ALL_LIVE_DATA_SOURCES, HTTP_DATA_SOURCES, LAYER5_COMMAND_SOURCES

if TYPE_CHECKING:
    from ..coordinator import JackerySolarVaultCoordinator


type QueryButtonAction = Callable[[JackerySolarVaultCoordinator, str], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class JackeryButtonDescription(ButtonEntityDescription):
    """Metadata for a documented app read/query command."""

    action: QueryButtonAction
    message_type: str
    action_id: int
    cmd: int
    dev_type: int | None = None
    http_device_property: bool = False
    http_system_shadow: bool = False
    http_battery_packs: bool = False
    http_subdevice_dev_type: int | None = None
    data_sources: tuple[str, ...] = ()
    command_sources: tuple[str, ...] = ()
    device_registry_role: str = "head"

    def __post_init__(self) -> None:
        """Resolve direct read and command transports for this App command."""
        realtime_sources = LAYER5_COMMAND_SOURCES
        if not self.data_sources:
            object.__setattr__(
                self,
                "data_sources",
                (ALL_LIVE_DATA_SOURCES if self.has_http_read else realtime_sources),
            )
        if not self.command_sources:
            object.__setattr__(
                self,
                "command_sources",
                (
                    (HTTP_DATA_SOURCES[0], *realtime_sources)
                    if self.has_http_read
                    else realtime_sources
                ),
            )

    @property
    def has_http_read(self) -> bool:
        """Whether this query has a documented HTTP read equivalent."""
        return (
            self.http_device_property
            or self.http_system_shadow
            or self.http_battery_packs
            or self.http_subdevice_dev_type is not None
        )


def _portable_cmd(action_id: int) -> int:
    """Return the portable BLE message type used as MQTT body cmd."""
    return PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id]


async def _query_system_info(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request system information for a device via the coordinator."""
    await coordinator.async_query_system_info(device_id)


async def _query_device_info(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request an updated device information payload for the specified device."""
    await coordinator.async_query_device_info(device_id)


async def _query_wifi_list(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the device's configured and available Wi-Fi networks.

    Parameters:
        device_id (str): Identifier of the target device to query.
    """
    await coordinator.async_query_wifi_list(device_id)


async def _get_time_zone(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the coordinator to query the device's configured time zone.

    Parameters:
        device_id (str): Identifier of the device whose time zone will be queried.
    """
    await coordinator.async_get_time_zone(device_id)


async def _send_time_zone(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Send the configured time zone to the given device.

    Parameters:
        device_id (str): Identifier of the target device.
    """
    await coordinator.async_send_time_zone(device_id)


async def _sync_mqtt_connect_info(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the coordinator to synchronize MQTT connection information for the given.

    device.
    """
    await coordinator.async_sync_mqtt_connect_info(device_id)


async def _query_device_ota_version(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the OTA firmware version for the specified device.

    Parameters:
        device_id (str): Unique identifier of the target device.
    """
    await coordinator.async_query_device_ota_version(device_id)


async def _query_third_party_mqtt_config(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the device's third-party MQTT configuration via the coordinator.

    Parameters:
        device_id (str): The identifier of the device whose third-party MQTT
        configuration should be queried.
    """
    await coordinator.async_query_third_party_mqtt_config(device_id)


async def _query_wifi_config(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the Wi-Fi configuration for the specified device through the coordinator.

    Parameters:
        device_id (str): Coordinator-managed device identifier.
    """
    await coordinator.async_query_wifi_config(device_id)


async def _query_battery_packs(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request an updated battery-packs report for the specified device from the.

    coordinator.

    Parameters:
        coordinator (JackerySolarVaultCoordinator): Coordinator instance to perform the
        query.
        device_id (str): Identifier of the target device.
    """
    await coordinator.async_query_battery_packs(device_id)


async def _query_smart_meter(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Trigger a smart meter query for the specified device."""
    await coordinator.async_query_smart_meter(device_id)


async def _query_meter_heads(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Trigger a meter-head query for the specified device."""
    await coordinator.async_query_meter_heads(device_id)


async def _query_smart_plugs(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the device's smart-plug inventory from the coordinator.

    Triggers the coordinator to fetch and update the smart-plug list for the given
    device.
    """
    await coordinator.async_query_smart_plugs(device_id)


async def _query_subdevice_combo(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Trigger the coordinator to query combined subdevice information for the.

    specified device.

    Parameters:
        coordinator (JackerySolarVaultCoordinator): Coordinator responsible for
        communicating with the device.
        device_id (str): Identifier of the device whose subdevice combo should be
        queried.
    """
    await coordinator.async_query_subdevice_combo(device_id)


async def _portable_restart(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Restart a portable Explorer device by sending the portable reboot command.

    Parameters:
        coordinator (JackerySolarVaultCoordinator): Coordinator used to send the
        command.
        device_id (str): Identifier of the target portable device.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_RESTART,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_RESTART),
        body_fields={FIELD_REBOOT: 1},
    )


async def _portable_power_off(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Shuts down (powers off) the specified portable Explorer device.

    Sends the portable power-off command for the given device identifier.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_POWER_OFF,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_POWER_OFF),
        body_fields={FIELD_REBOOT: 2},
    )


async def _portable_power_pack_blink(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Blink the power pack LEDs on the specified portable device.

    Sends a portable command to request the device blink its power pack LEDs.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_POWER_PACK_BLINK,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_POWER_PACK_BLINK),
        body_fields={},
    )


async def _portable_read_device_info(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request device information from a portable (Explorer) device via the coordinator.

    Parameters:
        device_id (str): Identifier of the target portable device.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_READ_DEVICE_INFO,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_READ_DEVICE_INFO),
        body_fields={},
    )


async def _portable_read_wifi_list(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the portable device's available WiFi networks.

    Sends a portable command to query the device WiFi list (msgId=5, bleMsgType=1).
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_READ_WIFI_LIST,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_READ_WIFI_LIST),
        body_fields={},
    )


async def _portable_get_power_pack_list(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the portable device's list of power/battery packs.

    Parameters:
        coordinator: The integration coordinator managing device communication.
        device_id (str): Identifier of the target portable device.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_GET_POWER_PACK_LIST,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_GET_POWER_PACK_LIST),
        body_fields={},
    )


async def _portable_get_electricity_data_count(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the portable device's stored electricity data count.

    Sends a portable command via the coordinator to query how many electricity data
    records the portable device holds (uses action_id
    ACTION_ID_PORTABLE_GET_ELECTRICITY_DATA_COUNT, cmd=7).
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_GET_ELECTRICITY_DATA_COUNT,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_GET_ELECTRICITY_DATA_COUNT),
        body_fields={},
    )


async def _portable_send_time_zone(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Send the coordinator's configured time zone to the specified portable device."""
    await coordinator.async_send_portable_time_zone(device_id)


async def _portable_sync_mqtt_info(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Synchronize MQTT connection information on a portable device.

    Sends the appropriate portable command through the coordinator to update the
    device's MQTT connection settings.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_SYNC_MQTT_INFO,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_SYNC_MQTT_INFO),
        body_fields={},
    )


async def _portable_get_wifi_config(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Query portable WiFi config (msgId=52, bleMsgType=124)."""
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_GET_WIFI_CONFIG,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_GET_WIFI_CONFIG),
        body_fields={},
    )


async def _portable_get_charge_plan(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the portable unit's charge/discharge plan.

    Sends the portable query for the device's electricity strategy (message type
    MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY, command 15).
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_GET_CHARGE_PLAN),
        body_fields={},
        message_type=MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
    )


async def _portable_current_charge_plan(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Query portable current charge/discharge plan (msgId=30, bleMsgType=21)."""
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_CURRENT_CHARGE_PLAN,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_CURRENT_CHARGE_PLAN),
        body_fields={},
        message_type=MQTT_MESSAGE_QUERY_CURRENT_ELECTRICITY_STRATEGY,
    )


async def _portable_get_peaks_troughs(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the portable device's time-of-use (TOU) peaks and troughs schedule.

    Parameters:
        device_id (str): Identifier of the target portable device.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_GET_PEAKS_TROUGHS,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_GET_PEAKS_TROUGHS),
        body_fields={},
        message_type=MQTT_MESSAGE_QUERY_TOU_SCHEDULE,
    )


async def _portable_read_sub_ct(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Request the portable device to read sub-device CT (current transformer).

    properties.

    This triggers sending the appropriate portable query command for sub-device CT to
    the coordinator.
    """
    await coordinator.async_send_portable_command(
        device_id,
        action_id=ACTION_ID_PORTABLE_READ_SUB_CT,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_READ_SUB_CT),
        body_fields={},
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
    )


BUTTON_DESCRIPTIONS: tuple[JackeryButtonDescription, ...] = (
    JackeryButtonDescription(
        key="refresh_system_info",
        translation_key="refresh_system_info",
        action=_query_system_info,
        message_type=MQTT_MESSAGE_QUERY_COMBINE_DATA,
        action_id=ACTION_ID_QUERY_COMBINE_DATA,
        cmd=MQTT_CMD_QUERY_COMBINE_DATA,
        http_system_shadow=True,
        device_registry_role="system",
    ),
    JackeryButtonDescription(
        key="refresh_device_info",
        translation_key="refresh_device_info",
        action=_query_device_info,
        message_type=MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
        action_id=ACTION_ID_QUERY_DEVICE_PROPERTY,
        cmd=MQTT_CMD_QUERY_DEVICE_PROPERTY,
        http_device_property=True,
    ),
    JackeryButtonDescription(
        key="refresh_wifi_list",
        translation_key="refresh_wifi_list",
        action=_query_wifi_list,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_READ_WIFI_LIST,
        cmd=MQTT_CMD_READ_WIFI_LIST,
    ),
    JackeryButtonDescription(
        key="refresh_time_zone",
        translation_key="refresh_time_zone",
        action=_get_time_zone,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_GET_TIME_ZONE,
        cmd=MQTT_CMD_GET_TIME_ZONE,
    ),
    JackeryButtonDescription(
        key="sync_time_zone",
        translation_key="sync_time_zone",
        action=_send_time_zone,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_SEND_TIME_ZONE,
        cmd=MQTT_CMD_SEND_TIME_ZONE,
    ),
    JackeryButtonDescription(
        key="sync_cloud_mqtt_info",
        translation_key="sync_cloud_mqtt_info",
        action=_sync_mqtt_connect_info,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_SYNC_MQTT_CONNECT_INFO,
        cmd=MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
    ),
    JackeryButtonDescription(
        key="refresh_device_ota_version",
        translation_key="refresh_device_ota_version",
        action=_query_device_ota_version,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_GET_DEVICE_OTA_VERSION,
        cmd=MQTT_CMD_GET_DEVICE_OTA_VERSION,
    ),
    JackeryButtonDescription(
        key="refresh_third_party_mqtt_config",
        translation_key="refresh_third_party_mqtt_config",
        action=_query_third_party_mqtt_config,
        message_type=MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
        action_id=ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
        cmd=MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
    ),
    JackeryButtonDescription(
        key="refresh_wifi_config",
        translation_key="refresh_wifi_config",
        action=_query_wifi_config,
        message_type=MQTT_MESSAGE_QUERY_WIFI_CONFIG,
        action_id=ACTION_ID_QUERY_WIFI_CONFIG,
        cmd=MQTT_CMD_QUERY_WIFI_CONFIG,
    ),
    JackeryButtonDescription(
        key="refresh_battery_packs",
        translation_key="refresh_battery_packs",
        action=_query_battery_packs,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3014,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_BATTERY_PACK,
        http_battery_packs=True,
    ),
    JackeryButtonDescription(
        key="refresh_smart_meter",
        translation_key="refresh_smart_meter",
        action=_query_smart_meter,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3031,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_CT,
        http_subdevice_dev_type=SUBDEVICE_DEV_TYPE_CT,
    ),
    JackeryButtonDescription(
        key="refresh_meter_heads",
        translation_key="refresh_meter_heads",
        action=_query_meter_heads,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3033,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_METER_HEAD,
        http_subdevice_dev_type=SUBDEVICE_DEV_TYPE_METER_HEAD,
    ),
    JackeryButtonDescription(
        key="refresh_smart_plugs",
        translation_key="refresh_smart_plugs",
        action=_query_smart_plugs,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3032,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_SOCKET,
        http_subdevice_dev_type=SUBDEVICE_DEV_TYPE_SOCKET,
    ),
    JackeryButtonDescription(
        key="refresh_subdevice_combo",
        translation_key="refresh_subdevice_combo",
        action=_query_subdevice_combo,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3037,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_COMBO,
        http_subdevice_dev_type=SUBDEVICE_DEV_TYPE_COMBO,
    ),
    # --- Portable / Explorer powerstation buttons ---
    JackeryButtonDescription(
        key="portable_restart",
        translation_key="portable_restart",
        action=_portable_restart,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_RESTART,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_RESTART),
    ),
    JackeryButtonDescription(
        key="portable_power_off",
        translation_key="portable_power_off",
        action=_portable_power_off,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_POWER_OFF,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_POWER_OFF),
    ),
    JackeryButtonDescription(
        key="portable_power_pack_blink",
        translation_key="portable_power_pack_blink",
        action=_portable_power_pack_blink,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_POWER_PACK_BLINK,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_POWER_PACK_BLINK),
    ),
    JackeryButtonDescription(
        key="portable_refresh_device_info",
        translation_key="portable_refresh_device_info",
        action=_portable_read_device_info,
        message_type=MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
        action_id=ACTION_ID_PORTABLE_READ_DEVICE_INFO,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_READ_DEVICE_INFO),
        http_device_property=True,
    ),
    JackeryButtonDescription(
        key="portable_refresh_wifi_list",
        translation_key="portable_refresh_wifi_list",
        action=_portable_read_wifi_list,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_READ_WIFI_LIST,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_READ_WIFI_LIST),
    ),
    JackeryButtonDescription(
        key="portable_refresh_battery_packs",
        translation_key="portable_refresh_battery_packs",
        action=_portable_get_power_pack_list,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_GET_POWER_PACK_LIST,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_GET_POWER_PACK_LIST),
        http_battery_packs=True,
    ),
    JackeryButtonDescription(
        key="portable_refresh_electricity_count",
        translation_key="portable_refresh_electricity_count",
        action=_portable_get_electricity_data_count,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_GET_ELECTRICITY_DATA_COUNT,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_GET_ELECTRICITY_DATA_COUNT),
    ),
    JackeryButtonDescription(
        key="portable_sync_time_zone",
        translation_key="portable_sync_time_zone",
        action=_portable_send_time_zone,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_SEND_TIME_ZONE,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_SEND_TIME_ZONE),
    ),
    JackeryButtonDescription(
        key="portable_sync_mqtt_info",
        translation_key="portable_sync_mqtt_info",
        action=_portable_sync_mqtt_info,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_SYNC_MQTT_INFO,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_SYNC_MQTT_INFO),
    ),
    JackeryButtonDescription(
        key="portable_refresh_wifi_config",
        translation_key="portable_refresh_wifi_config",
        action=_portable_get_wifi_config,
        message_type=MQTT_MESSAGE_QUERY_WIFI_CONFIG,
        action_id=ACTION_ID_PORTABLE_GET_WIFI_CONFIG,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_GET_WIFI_CONFIG),
    ),
    JackeryButtonDescription(
        key="portable_get_charge_plan",
        translation_key="portable_get_charge_plan",
        action=_portable_get_charge_plan,
        message_type=MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
        action_id=ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_GET_CHARGE_PLAN),
    ),
    JackeryButtonDescription(
        key="portable_current_charge_plan",
        translation_key="portable_current_charge_plan",
        action=_portable_current_charge_plan,
        message_type=MQTT_MESSAGE_QUERY_CURRENT_ELECTRICITY_STRATEGY,
        action_id=ACTION_ID_PORTABLE_CURRENT_CHARGE_PLAN,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_CURRENT_CHARGE_PLAN),
    ),
    JackeryButtonDescription(
        key="portable_get_peaks_troughs",
        translation_key="portable_get_peaks_troughs",
        action=_portable_get_peaks_troughs,
        message_type=MQTT_MESSAGE_QUERY_TOU_SCHEDULE,
        action_id=ACTION_ID_PORTABLE_GET_PEAKS_TROUGHS,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_GET_PEAKS_TROUGHS),
    ),
    JackeryButtonDescription(
        key="portable_refresh_sub_ct",
        translation_key="portable_refresh_sub_ct",
        action=_portable_read_sub_ct,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_PORTABLE_READ_SUB_CT,
        cmd=_portable_cmd(ACTION_ID_PORTABLE_READ_SUB_CT),
        http_subdevice_dev_type=SUBDEVICE_DEV_TYPE_CT,
    ),
)
