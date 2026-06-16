"""MQTT message type classifiers extracted from coordinator.

Pure functions that classify inbound MQTT messages by type, action_id, and cmd
without touching coordinator state.  Used by ``_async_handle_mqtt_message`` to
route messages to the correct merge branch.

Source: coordinator.py lines 2135-2181 (Phase 2c extraction).
"""

from typing import Any

from custom_components.jackery_solarvault.const import (
    ACTION_ID_GET_DEVICE_OTA_VERSION,
    ACTION_ID_GET_TIME_ZONE,
    ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
    ACTION_ID_QUERY_WIFI_CONFIG,
    ACTION_ID_READ_WIFI_LIST,
    ACTION_ID_SEND_TIME_ZONE,
    ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
    ACTION_ID_SYNC_GRID_STANDARD,
    ACTION_ID_SYNC_MQTT_CONNECT_INFO,
    FIELD_CMD,
    MQTT_ACTION_IDS_ALARM,
    MQTT_CMD_GET_DEVICE_OTA_VERSION,
    MQTT_CMD_GET_TIME_ZONE,
    MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_QUERY_WIFI_CONFIG,
    MQTT_CMD_READ_WIFI_LIST,
    MQTT_CMD_SEND_TIME_ZONE,
    MQTT_CMD_SYNC_GRID_STANDARD,
    MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
    MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_UPLOAD_DEVICE_ALERT,
    MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_QUERY_WIFI_CONFIG,
    MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_UPLOAD_DEVICE_ALERT,
)


def is_alarm_message(
    msg_type: str | None,
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether an MQTT message represents an alarm or alert.

    Checks the message type, action ID, and the command field in `body` for known
    alarm/alert indicators.

    Returns:
        `true` if the message is an alarm or alert, `false` otherwise.
    """
    return (
        msg_type == MQTT_MESSAGE_UPLOAD_DEVICE_ALERT
        or action_id in MQTT_ACTION_IDS_ALARM
        or body.get(FIELD_CMD) == MQTT_CMD_UPLOAD_DEVICE_ALERT
    )


def is_third_party_mqtt_config_message(
    msg_type: str | None,
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether an MQTT message represents a third-party MQTT configuration.

    operation.

    Parameters:
        msg_type (str | None): The message type to check.
        action_id (int | None): The numeric action identifier to check.
        body (dict[str, Any]): The message payload; the function checks
        `body.get(FIELD_CMD)` for command matching.

    Returns:
        True if the message type, action id, or `body[FIELD_CMD]` indicates a
        third-party MQTT config request or query, False otherwise.
    """
    return (
        msg_type
        in {
            MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
            MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
        }
        or action_id
        in {
            ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
            ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
        }
        or body.get(FIELD_CMD)
        in {
            MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
            MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
        }
    )


def is_wifi_config_message(
    msg_type: str | None,
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether the MQTT message represents a WiFi configuration query.

    Returns:
        `true` if the message is a WiFi config query, `false` otherwise.
    """
    return (
        msg_type == MQTT_MESSAGE_QUERY_WIFI_CONFIG
        or action_id == ACTION_ID_QUERY_WIFI_CONFIG
        or body.get(FIELD_CMD) == MQTT_CMD_QUERY_WIFI_CONFIG
    )


def is_wifi_list_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether the MQTT message requests the WiFi list.

    Parameters:
        action_id (int | None): Action identifier from the message header; may match
        ACTION_ID_READ_WIFI_LIST.
        body (dict[str, Any]): Message payload; may contain a command under FIELD_CMD.

    Returns:
        true if the message requests a WiFi list, false otherwise.
    """
    return (
        action_id == ACTION_ID_READ_WIFI_LIST
        or body.get(FIELD_CMD) == MQTT_CMD_READ_WIFI_LIST
    )


def is_time_zone_config_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Identify MQTT messages that request or provide the device time zone.

    Parameters:
        action_id (int | None): Message action identifier that may indicate a time zone
        get/send.
        body (dict[str, Any]): Message payload; `FIELD_CMD` may contain the command key.

    Returns:
        True if the message is a time zone get or send command, False otherwise.
    """
    return action_id in {
        ACTION_ID_GET_TIME_ZONE,
        ACTION_ID_SEND_TIME_ZONE,
    } or body.get(FIELD_CMD) in {MQTT_CMD_GET_TIME_ZONE, MQTT_CMD_SEND_TIME_ZONE}


def is_grid_standard_sync_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether the MQTT message represents a grid standard synchronization.

    @returns
        `true` if the message represents a grid standard sync, `false` otherwise.
    """
    return (
        action_id == ACTION_ID_SYNC_GRID_STANDARD
        or body.get(FIELD_CMD) == MQTT_CMD_SYNC_GRID_STANDARD
    )


def is_mqtt_connect_info_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether a message requests or synchronizes MQTT connection information.

    Parameters:
        action_id (int | None): Numeric action identifier from the message metadata;
        may be None.
        body (dict[str, Any]): Message body; the function checks the value under
        `FIELD_CMD`.

    Returns:
        bool: `True` if `action_id` equals `ACTION_ID_SYNC_MQTT_CONNECT_INFO` or
        `body.get(FIELD_CMD)` equals `MQTT_CMD_SYNC_MQTT_CONNECT_INFO`, `False`
        otherwise.
    """
    return (
        action_id == ACTION_ID_SYNC_MQTT_CONNECT_INFO
        or body.get(FIELD_CMD) == MQTT_CMD_SYNC_MQTT_CONNECT_INFO
    )


def is_device_ota_version_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether the MQTT message requests the device OTA version.

    Parameters:
        action_id (int | None): Numeric action identifier from the MQTT message.
        body (dict[str, Any]): Message payload; may contain a command under FIELD_CMD.

    Returns:
        bool: `true` if the message is a device OTA version query, `false` otherwise.
    """
    return (
        action_id == ACTION_ID_GET_DEVICE_OTA_VERSION
        or body.get(FIELD_CMD) == MQTT_CMD_GET_DEVICE_OTA_VERSION
    )
