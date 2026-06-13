"""MQTT message type classifiers extracted from coordinator.

Pure functions that classify inbound MQTT messages by type, action_id, and cmd
without touching coordinator state.  Used by ``_async_handle_mqtt_message`` to
route messages to the correct merge branch.

Source: coordinator.py lines 2135-2181 (Phase 2c extraction).
"""

from typing import Any

from ..const import (
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
    """Return True when the MQTT message is an alarm or alert."""
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
    """Return True when the MQTT message carries third-party MQTT config."""
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
    """Return True when the MQTT message is a WiFi config query."""
    return (
        msg_type == MQTT_MESSAGE_QUERY_WIFI_CONFIG
        or action_id == ACTION_ID_QUERY_WIFI_CONFIG
        or body.get(FIELD_CMD) == MQTT_CMD_QUERY_WIFI_CONFIG
    )


def is_wifi_list_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Return True when the MQTT message is a WiFi list read."""
    return (
        action_id == ACTION_ID_READ_WIFI_LIST
        or body.get(FIELD_CMD) == MQTT_CMD_READ_WIFI_LIST
    )


def is_time_zone_config_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Return True when the MQTT message is a timezone get/set."""
    return action_id in {
        ACTION_ID_GET_TIME_ZONE,
        ACTION_ID_SEND_TIME_ZONE,
    } or body.get(FIELD_CMD) in {MQTT_CMD_GET_TIME_ZONE, MQTT_CMD_SEND_TIME_ZONE}


def is_grid_standard_sync_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Return True when the MQTT message is a grid standard sync."""
    return (
        action_id == ACTION_ID_SYNC_GRID_STANDARD
        or body.get(FIELD_CMD) == MQTT_CMD_SYNC_GRID_STANDARD
    )


def is_mqtt_connect_info_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Return True when the MQTT message is an MQTT connect info sync."""
    return (
        action_id == ACTION_ID_SYNC_MQTT_CONNECT_INFO
        or body.get(FIELD_CMD) == MQTT_CMD_SYNC_MQTT_CONNECT_INFO
    )


def is_device_ota_version_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Return True when the MQTT message is a device OTA version query."""
    return (
        action_id == ACTION_ID_GET_DEVICE_OTA_VERSION
        or body.get(FIELD_CMD) == MQTT_CMD_GET_DEVICE_OTA_VERSION
    )
