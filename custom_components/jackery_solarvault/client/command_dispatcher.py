"""Outbound transport dispatching and device control mixin."""

import asyncio
import base64
import binascii
from collections.abc import Awaitable, Mapping
import contextlib
import copy
from dataclasses import dataclass
from functools import partial
import hashlib
import json
import logging
import math
import time
from typing import TYPE_CHECKING, Any, Final, NoReturn, TypedDict, Unpack, cast

from homeassistant.core import CoreState
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from ..client import JackeryAuthError, JackeryError
from ..const import (
    ACTION_ID_AUTO_STANDBY,
    ACTION_ID_BIND_SMART_PART,
    ACTION_ID_BOX_READ_DEVICE_INFO,
    ACTION_ID_CONTROL_SOCKET_PRIORITY,
    ACTION_ID_CONTROL_SOCKET_SWITCH,
    ACTION_ID_CT_PHASE,
    ACTION_ID_DEFAULT_PW,
    ACTION_ID_DELETE_STORM_ALERT,
    ACTION_ID_EPS_ENABLED,
    ACTION_ID_FOLLOW_METER_PW,
    ACTION_ID_GET_DEVICE_OTA_VERSION,
    ACTION_ID_GET_TIME_ZONE,
    ACTION_ID_MAX_FEED_GRID,
    ACTION_ID_MAX_OUT_PW,
    ACTION_ID_OFF_GRID_DOWN,
    ACTION_ID_OFF_GRID_TIME,
    ACTION_ID_PORTABLE_AC_OUTPUT_MODE,
    ACTION_ID_PORTABLE_ADD_CHARGE_PLAN,
    ACTION_ID_PORTABLE_CURRENT_CHARGE_PLAN,
    ACTION_ID_PORTABLE_CUSTOM_USE_BATTERY,
    ACTION_ID_PORTABLE_DELETE_CHARGE_PLAN,
    ACTION_ID_PORTABLE_ENERGY_SAVING,
    ACTION_ID_PORTABLE_ENERGY_STORAGE_CHARGE_LIMIT,
    ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
    ACTION_ID_PORTABLE_GET_ELECTRICITY_DATA_COUNT,
    ACTION_ID_PORTABLE_GET_PEAKS_TROUGHS,
    ACTION_ID_PORTABLE_GET_POWER_PACK_LIST,
    ACTION_ID_PORTABLE_GET_WIFI_CONFIG,
    ACTION_ID_PORTABLE_LIGHT,
    ACTION_ID_PORTABLE_OTA_VERSION,
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY,
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SOC,
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SWITCH,
    ACTION_ID_PORTABLE_POWER_OFF,
    ACTION_ID_PORTABLE_POWER_PACK_BLINK,
    ACTION_ID_PORTABLE_READ_DEVICE_INFO,
    ACTION_ID_PORTABLE_READ_SUB_CT,
    ACTION_ID_PORTABLE_READ_WIFI_LIST,
    ACTION_ID_PORTABLE_RESERVE_SWITCH,
    ACTION_ID_PORTABLE_RESERVE_THRESHOLD,
    ACTION_ID_PORTABLE_RESTART,
    ACTION_ID_PORTABLE_SEND_TIME_ZONE,
    ACTION_ID_PORTABLE_SET_PEAKS_TROUGHS,
    ACTION_ID_PORTABLE_UPDATE_CHARGE_PLAN,
    ACTION_ID_PORTABLE_UPS_MODEL,
    ACTION_ID_PORTABLE_USE_POWER_MODE,
    ACTION_ID_QUERY_COMBINE_DATA,
    ACTION_ID_QUERY_DEVICE_PROPERTY,
    ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
    ACTION_ID_QUERY_WEATHER_PLAN,
    ACTION_ID_QUERY_WIFI_CONFIG,
    ACTION_ID_READ_WIFI_LIST,
    ACTION_ID_REBOOT_DEVICE,
    ACTION_ID_SEND_TIME_ZONE,
    ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
    ACTION_ID_SOC_LIMITS,
    ACTION_ID_STANDBY,
    ACTION_ID_STORM_MINUTES,
    ACTION_ID_STORM_WARNING,
    ACTION_ID_SUBDEVICE_3014,
    ACTION_ID_SUBDEVICE_3031,
    ACTION_ID_SUBDEVICE_3032,
    ACTION_ID_SUBDEVICE_3033,
    ACTION_ID_SUBDEVICE_3037,
    ACTION_ID_SYNC_GRID_STANDARD,
    ACTION_ID_SYNC_MQTT_CONNECT_INFO,
    ACTION_ID_TEMP_UNIT,
    ACTION_ID_TIMER_TASK_ADD,
    ACTION_ID_TIMER_TASK_DELETE,
    ACTION_ID_TIMER_TASK_READ,
    ACTION_ID_TIMER_TASK_UPDATE,
    ACTION_ID_UNBIND_SMART_PART,
    ACTION_ID_WORK_MODEL,
    BLE_CMD_BOX_READ_DEVICE_INFO,
    BLE_COMMAND_CONNECT_TIMEOUT_SEC,
    BOX_BLE_WRITE_PAIRS,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CUSTOM_USE_BATTERY_BC_OFFSET,
    DEFAULT_THIRD_PARTY_MQTT_PORT,
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    DOMAIN,
    FIELD_ACTION_ID,
    FIELD_ACTION_TYPE,
    FIELD_ALERT_ID,
    FIELD_AUTO_STANDBY,
    FIELD_BATTERY_PACKS,
    FIELD_BODY,
    FIELD_CID,
    FIELD_CMD,
    FIELD_COLLECTORS,
    FIELD_COMPANY_NAME,
    FIELD_COUNTRY,
    FIELD_COUNTRY_CODE,
    FIELD_CURRENCY,
    FIELD_CURRENCY_CODE,
    FIELD_DATA,
    FIELD_DEFAULT_PW,
    FIELD_DEVICE_SN,
    FIELD_DEVICE_TYPE,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_GRID_STANDARD,
    FIELD_HOST,
    FIELD_ID,
    FIELD_IDX,
    FIELD_IS_AUTO_STANDBY,
    FIELD_IS_FOLLOW_METER_PW,
    FIELD_LATITUDE,
    FIELD_LOGIN_ALLOWED,
    FIELD_LONGITUDE,
    FIELD_MAX_FEED_GRID,
    FIELD_MAX_OUT_PW,
    FIELD_MESSAGE_TYPE,
    FIELD_MINS_INTERVAL,
    FIELD_MODEL_CODE,
    FIELD_NAME,
    FIELD_OFF_GRID_DOWN,
    FIELD_OFF_GRID_TIME,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_PLUGS,
    FIELD_POWER_PRICE_RESOURCE,
    FIELD_PV1,
    FIELD_PV2,
    FIELD_PV3,
    FIELD_PV4,
    FIELD_PV_NAME,
    FIELD_REBOOT,
    FIELD_SAFETY,
    FIELD_SCAN_NAME,
    FIELD_SCHE_PHASE,
    FIELD_SINGLE_CURRENCY,
    FIELD_SINGLE_CURRENCY_CODE,
    FIELD_SINGLE_PRICE,
    FIELD_SN,
    FIELD_SOCKET_PRIORITY,
    FIELD_SOC_CHARGE_LIMIT,
    FIELD_SOC_CHG_LIMIT,
    FIELD_SOC_DISCHARGE_LIMIT,
    FIELD_SOC_DISCHG_LIMIT,
    FIELD_SOC_FORCE_CHG,
    FIELD_SRFC,
    FIELD_SRL,
    FIELD_STATUS,
    FIELD_SUB_DEVICE,
    FIELD_SW,
    FIELD_SWITCH_STATE,
    FIELD_SW_EPS,
    FIELD_SYSTEM_REGION,
    FIELD_SYS_SWITCH,
    FIELD_TASK_TYPE,
    FIELD_TEMP_UNIT,
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_THIRD_PARTY_MQTT_IP,
    FIELD_THIRD_PARTY_MQTT_PASSWORD,
    FIELD_THIRD_PARTY_MQTT_PORT,
    FIELD_THIRD_PARTY_MQTT_TOKEN,
    FIELD_THIRD_PARTY_MQTT_USERNAME,
    FIELD_TIMESTAMP,
    FIELD_TIMEZONE,
    FIELD_TS,
    FIELD_UNBIND,
    FIELD_UO,
    FIELD_UPDATE_STATUS,
    FIELD_VERSION,
    FIELD_WORK_MODEL,
    FIELD_WPC,
    FIELD_WPS,
    MQTT_CMD_BIND_SMART_PART,
    MQTT_CMD_CONTROL_COMBINE,
    MQTT_CMD_CONTROL_SUB_DEVICE,
    MQTT_CMD_DEVICE_PROPERTY_CHANGE,
    MQTT_CMD_DOWNLOAD_DEVICE_SCHEDULE,
    MQTT_CMD_GET_DEVICE_OTA_VERSION,
    MQTT_CMD_GET_TIME_ZONE,
    MQTT_CMD_NONE,
    MQTT_CMD_QUERY_COMBINE_DATA,
    MQTT_CMD_QUERY_DEVICE_PROPERTY,
    MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_QUERY_WEATHER_PLAN,
    MQTT_CMD_QUERY_WIFI_CONFIG,
    MQTT_CMD_READ_WIFI_LIST,
    MQTT_CMD_SEND_TIME_ZONE,
    MQTT_CMD_SYNC_GRID_STANDARD,
    MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
    MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_UNBIND_SMART_PART,
    MQTT_CREDENTIAL_USER_ID,
    MQTT_HOST,
    MQTT_MESSAGE_BIND_SMART_ACCESSORY,
    MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
    MQTT_MESSAGE_CONTROL_COMBINE,
    MQTT_MESSAGE_CONTROL_SUB_DEVICE,
    MQTT_MESSAGE_DELETE_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
    MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE,
    MQTT_MESSAGE_INSERT_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_COMBINE_DATA,
    MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
    MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_QUERY_WEATHER_PLAN,
    MQTT_MESSAGE_QUERY_WIFI_CONFIG,
    MQTT_MESSAGE_REMOVE_SMART_ACCESSORY,
    MQTT_MESSAGE_SEND_WEATHER_ALERT,
    MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_UPDATE_ELECTRICITY_STRATEGY,
    MQTT_PORT,
    MQTT_TOPIC_COMMAND,
    MQTT_TOPIC_PREFIX,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_BLE_OTA_VERSIONS,
    PAYLOAD_CIRCUIT_PROPERTY,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_META,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_LOCATION,
    PAYLOAD_PRICE,
    PAYLOAD_PRICE_SOURCES,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_SUBDEVICES,
    PAYLOAD_SYSTEM,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
    PORTABLE_AC_TRANSFER_MODE_MODELS,
    PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID,
    PORTABLE_ENERGY_SAVING_MINUTES,
    PORTABLE_LIGHT_DETAIL_MODELS,
    PORTABLE_OUTPUT_PRIORITY_MODELS,
    PORTABLE_PEAK_THROUGH_MAX,
    PORTABLE_PEAK_THROUGH_MIN,
    PORTABLE_PEAK_THROUGH_MODELS,
    PORTABLE_RESERVE_ALLOWED_VALUES,
    PORTABLE_RESERVE_MODELS,
    PORTABLE_UPS_MODE_MODELS,
    PORTABLE_WORKING_MODES,
    SHELLY_CONTROL_ACTION_OFF,
    SHELLY_CONTROL_ACTION_ON,
    SHELLY_CONTROL_FUNCTION_SWITCH,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_BREAKER,
    SUBDEVICE_DEV_TYPE_COMBO,
    SUBDEVICE_DEV_TYPE_CT,
    SUBDEVICE_DEV_TYPE_METER_HEAD,
    SUBDEVICE_DEV_TYPE_SOCKET,
    SUBDEVICE_SCAN_NAME_DEV_TYPES,
    TIMER_TASK_ACTION_READ,
    TIMER_TASK_TYPE_CUSTOM_MODE,
    TIMER_TASK_TYPE_SMART_PLUG,
    TIMER_TASK_TYPE_TIME_ELEC,
    _THIRD_PARTY_MQTT_CONFIG_KEYS,
)
from ..ingest import (
    TransportSource,
    is_blank as _is_blank_value,
    merge_present_dict_values,
)
from ..models import Observation
from ..util import (
    WHOLE_INT_TEXT_RE,
    circuit_id,
    first_nonblank_int,
    safe_bool,
    safe_float,
    safe_int,
    utc_now,
)
from .ble import portable_ble_profile
from .credentials import redacted_error
from .operation_errors import (
    BridgeWriteOutcomeUnknownError,
    MqttPublishOutcomeUnknownError,
)
from .ota_pending import async_set_pending
from .pack_candidates import (
    _PACK_STATE_DELETED as _PACK_STATE_DELETED,  # ruff: ignore[useless-import-alias] - compatibility export
    _PACK_TRANSACTION as _PACK_TRANSACTION,  # ruff: ignore[useless-import-alias] - compatibility export
    _HttpPackFieldCandidate as _HttpPackFieldCandidate,  # ruff: ignore[useless-import-alias] - compatibility export
    _PackCandidateTransaction as _PackCandidateTransaction,  # ruff: ignore[useless-import-alias] - compatibility export
    _commit_pack_candidates as _commit_pack_candidates,  # ruff: ignore[useless-import-alias] - compatibility export
    _pack_state_owner_key as _pack_state_owner_key,  # ruff: ignore[useless-import-alias] - compatibility export
    _pack_transaction as _pack_transaction,  # ruff: ignore[useless-import-alias] - compatibility export
    _transactional_pack_candidates as _transactional_pack_candidates,  # ruff: ignore[useless-import-alias] - compatibility export
)
from .third_party_mqtt_codec import (
    decode_third_party_mqtt_config_body,
    encode_third_party_mqtt_field,
    stable_third_party_mqtt_token,
    third_party_mqtt_config_plaintext,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence
    from typing import ClassVar

    from ..models import IngestResult
    from .mqtt_push import JackeryMqttPushClient

_SOC_PERCENT_MAX = 100

_PV_CHANNEL_FIELDS: tuple[str, ...] = (FIELD_PV1, FIELD_PV2, FIELD_PV3, FIELD_PV4)

# PortableDeviceController explicitly inserts cmd in MQTT for these five
# actions; other portable actions keep their own App-provided body unchanged.
_PORTABLE_MQTT_EXPLICIT_CMD_ACTIONS: Final = frozenset({
    ACTION_ID_PORTABLE_POWER_PACK_BLINK,
    ACTION_ID_PORTABLE_SET_PEAKS_TROUGHS,
    ACTION_ID_PORTABLE_GET_PEAKS_TROUGHS,
    ACTION_ID_PORTABLE_RESTART,
    ACTION_ID_PORTABLE_POWER_OFF,
})


def _portable_mqtt_body(
    action_id: int, cmd: int, body_fields: dict[str, Any]
) -> dict[str, Any]:
    """Build the MQTT-only body for the App's portable command."""
    body = dict(body_fields)
    if action_id in _PORTABLE_MQTT_EXPLICIT_CMD_ACTIONS:
        body[FIELD_CMD] = cmd
    return body


_HOME_BLE_COMMAND_PAIRS: Final[frozenset[tuple[int, int]]] = frozenset({
    (3001, 1),
    (3002, 2),
    (3003, 3),
    (3004, 22),
    (3005, 99),
    (3006, 100),
    (3007, 101),
    (3008, 102),
    (3009, 103),
    (3010, 105),
    (3011, 106),
    (3012, 108),
    (3013, 109),
    (3014, 110),
    (3015, 112),
    (3016, 112),
    (3017, 112),
    (3018, 112),
    (3019, 120),
    (3020, 23),
    (3021, 121),
    (3022, 107),
    (3023, 107),
    (3024, 111),
    (3025, 111),
    (3026, 111),
    (3027, 121),
    (3028, 107),
    (3029, 121),
    (3030, 107),
    (3031, 110),
    (3032, 110),
    (3033, 110),
    (3034, 0),
    (3035, 0),
    (3036, 0),
    (3037, 110),
    (3038, 107),
    (3039, 121),
    (3040, 121),
    (3041, 121),
    (3042, 122),
    (3043, 121),
    (3044, 121),
    (3045, 124),
    (3046, 113),
    (3047, 114),
})

_BOX_BLE_READ_PAIRS: Final[frozenset[tuple[int, int]]] = frozenset({
    (ACTION_ID_BOX_READ_DEVICE_INFO, BLE_CMD_BOX_READ_DEVICE_INFO),
    (7, 10),
    (12, 15),
    (18, 21),
    (19, 23),
    (254, 1),
    (248, 100),
})

_BOX_BLE_COMMAND_PAIRS: Final[frozenset[tuple[int, int]]] = (
    _BOX_BLE_READ_PAIRS | BOX_BLE_WRITE_PAIRS
)

_BOX_WORKING_MODE_PAIR: Final = (17, 20)

_BOX_CHARGE_NOW_PAIR: Final = (3, 5)

_BOX_WORKING_MODE_MODELS: Final = frozenset({2001, 2002})


def _is_transport_connected(client: object) -> bool:
    """Safely check if a transport client is connected whether property or callable."""
    if client is None:
        return False
    status = getattr(client, "is_connected", False)
    return bool(status() if callable(status) else status)


_BOX_OTA_FIRMWARE_TYPES: Final[frozenset[str]] = frozenset({
    "BMS",
    "BMSX",
    "BMSX_BOOT",
    "DIY",
    "EBMS",
    "EBMS_BOOT",
    "ESP32",
    "HMI",
    "INV1",
    "INV2",
    "LEDBOARD",
    "MAIN",
    "PCSAC",
    "PCSAC_BOOT",
    "PCSDC",
    "PCSDC_BOOT",
    "PV",
})

_BOX_OTA_MAX_TOTAL_BYTES: Final = 64 * 1024 * 1024

_BOX_OTA_VERSION_CURRENT: Final = 6

_BOX_APP_INTEGER_MAX: Final = 2_147_483_647

_BOX_APP_PORT_MAX: Final = 65535


def _ble_ota_decimal_id(value: object) -> str | None:
    """Accept lossless JSON integer IDs and the App bean's decimal strings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if isinstance(value, str) and value.isdecimal():
        return value
    return None


def _ble_ota_module_ids(value: object) -> list[str] | None:
    """Project the App's List<String> without changing the raw API response."""
    if not isinstance(value, list):
        return None
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            if not item.strip() or "," in item:
                return None
            result.append(item)
        elif isinstance(item, int) and not isinstance(item, bool) and item >= 0:
            result.append(str(item))
        else:
            return None
    return result


def _ble_ota_firmware_urls(
    data: object, target_sn: str, family_name: str
) -> dict[str, str]:
    """Read the selected device's app firmware map without conflating its IDs.

    DeviceBleOTALinkQuiryApi.Bean.versionId is an independent integer response
    field marked IgnoreInMap. BaseBleOTAActivity.downloadFirmware consumes the
    Bean.t() URL map; it does not compare that field with targetVersionId, the
    full string identifier sent in the request.
    """
    if not isinstance(data, dict) or data.get(FIELD_DEVICE_SN) != target_sn:
        msg = f"{family_name} OTA firmware links do not identify the selected target"
        raise HomeAssistantError(msg)
    if any(
        name in _BOX_OTA_FIRMWARE_TYPES and url is not None and not isinstance(url, str)
        for name, url in data.items()
    ):
        msg = f"{family_name} OTA firmware links contain invalid entries"
        raise HomeAssistantError(msg)
    firmware_links = {
        name: url
        for name, url in data.items()
        if name in _BOX_OTA_FIRMWARE_TYPES and isinstance(url, str) and url
    }
    if not firmware_links:
        msg = f"{family_name} OTA firmware links are empty"
        raise HomeAssistantError(msg)
    return firmware_links


def _portable_reserve_body_is_valid(action_id: int, body: object) -> bool:
    field = FIELD_SRFC if action_id == ACTION_ID_PORTABLE_RESERVE_SWITCH else FIELD_SRL
    if not isinstance(body, dict) or set(body) != {field}:
        return False
    value = body[field]
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    if action_id == ACTION_ID_PORTABLE_RESERVE_SWITCH:
        return value in {0, 1}
    return value in PORTABLE_RESERVE_ALLOWED_VALUES


def _portable_mode_body_is_valid(
    action_id: int, model: int | None, body: object
) -> bool:
    if model is None:
        return False
    if action_id == ACTION_ID_PORTABLE_ENERGY_SAVING:
        field, allowed = "pm", PORTABLE_ENERGY_SAVING_MINUTES.get(model, ())
    elif action_id == ACTION_ID_PORTABLE_USE_POWER_MODE:
        field, allowed = "en", PORTABLE_WORKING_MODES.get(model, ())
    else:
        return False
    if not isinstance(body, dict) or set(body) != {field}:
        return False
    value = body[field]
    return isinstance(value, int) and not isinstance(value, bool) and value in allowed


def _portable_peak_body_is_valid(model: int | None, body: object) -> bool:
    if model not in PORTABLE_PEAK_THROUGH_MODELS or not isinstance(body, dict):
        return False
    if set(body) != {"bpc"}:
        return False
    value = body["bpc"]
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and PORTABLE_PEAK_THROUGH_MIN <= value <= PORTABLE_PEAK_THROUGH_MAX
    )


def _portable_light_body_is_valid(model: int | None, body: object) -> bool:
    if model not in PORTABLE_LIGHT_DETAIL_MODELS or not isinstance(body, dict):
        return False
    if set(body) != {"lm"}:
        return False
    value = body["lm"]
    return isinstance(value, int) and not isinstance(value, bool) and value in {0, 2, 3}


def _portable_light_wire_is_valid(action_id: int, cmd: int, body: object) -> bool:
    if (
        action_id != ACTION_ID_PORTABLE_LIGHT
        or cmd != PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[ACTION_ID_PORTABLE_LIGHT]
        or not isinstance(body, dict)
        or set(body) != {"lm"}
    ):
        return False
    value = body["lm"]
    return (
        isinstance(value, int) and not isinstance(value, bool) and value in {0, 1, 2, 3}
    )


_PORTABLE_PRIORITY_FIELDS = {
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SWITCH: frozenset({"outPrio"}),
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY: frozenset({"oac1Prio", "oac2Prio", "odcPrio"}),
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SOC: frozenset({
        "oac1PrioSoc",
        "oac2PrioSoc",
        "odcPrioSoc",
    }),
}

_ALL_PORTABLE_PRIORITY_FIELDS = frozenset().union(*_PORTABLE_PRIORITY_FIELDS.values())

_PORTABLE_UPS_FIELDS = {
    ACTION_ID_PORTABLE_UPS_MODEL: ("ups", PORTABLE_UPS_MODE_MODELS),
    ACTION_ID_PORTABLE_AC_OUTPUT_MODE: ("acmode", PORTABLE_AC_TRANSFER_MODE_MODELS),
}

_ALL_PORTABLE_UPS_FIELDS = frozenset(
    field for field, _ in _PORTABLE_UPS_FIELDS.values()
)


def _portable_priority_body_is_valid(
    action_id: int, cmd: int, model: int | None, body: object
) -> bool:
    if (
        model not in PORTABLE_OUTPUT_PRIORITY_MODELS
        or action_id not in _PORTABLE_PRIORITY_FIELDS
        or cmd != PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id]
        or not isinstance(body, dict)
        or len(body) != 1
    ):
        return False
    field, value = next(iter(body.items()))
    if field not in _PORTABLE_PRIORITY_FIELDS[action_id]:
        return False
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    if action_id == ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SOC:
        return 1 <= value <= _SOC_PERCENT_MAX
    return value in {0, 1}


def _portable_ups_body_is_valid(
    action_id: int, cmd: int, model: int | None, body: object, region: str | None = None
) -> bool:
    setting = _PORTABLE_UPS_FIELDS.get(action_id)
    if setting is None or not isinstance(body, dict):
        return False
    field, models = setting
    if (
        (
            model not in models
            and not (
                action_id == ACTION_ID_PORTABLE_UPS_MODEL
                and model in {13, 22}
                and region in {"US", "JP"}
            )
        )
        or cmd != PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id]
        or set(body) != {field}
    ):
        return False
    value = body[field]
    return isinstance(value, int) and not isinstance(value, bool) and value in {0, 1}


def _portable_bound_command_model(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    action_id: int,
    cmd: int,
    model: int | None,
    family: str,
    body: dict[str, Any],
    region: str | None = None,
) -> int | None:
    if action_id in _PORTABLE_UPS_FIELDS or (
        family == "portable" and _ALL_PORTABLE_UPS_FIELDS & body.keys()
    ):
        if not _portable_ups_body_is_valid(action_id, cmd, model, body, region):
            msg = "Invalid Portable UPS command or model"
            raise HomeAssistantError(msg)
        return model
    if (
        action_id in _PORTABLE_PRIORITY_FIELDS
        or _ALL_PORTABLE_PRIORITY_FIELDS & body.keys()
    ):
        if not _portable_priority_body_is_valid(action_id, cmd, model, body):
            msg = "Invalid Portable output priority command or model"
            raise HomeAssistantError(msg)
        return model
    if "lm" in body:
        value = body["lm"]
        if not _portable_light_wire_is_valid(action_id, cmd, body):
            msg = "Invalid Portable light mode"
            raise HomeAssistantError(msg)
        if value in {2, 3} or (value == 0 and model in PORTABLE_LIGHT_DETAIL_MODELS):
            if (
                action_id != ACTION_ID_PORTABLE_LIGHT
                or cmd != PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id]
                or not _portable_light_body_is_valid(model, body)
            ):
                msg = "Invalid Portable light command or model"
                raise HomeAssistantError(msg)
            return model
    if "bpc" in body:
        if (
            action_id != ACTION_ID_PORTABLE_ENERGY_STORAGE_CHARGE_LIMIT
            or cmd != PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id]
            or not _portable_peak_body_is_valid(model, body)
        ):
            msg = "Invalid Portable peak-through command or model"
            raise HomeAssistantError(msg)
        return model
    return None


def _box_app_write_is_valid(  # ruff: ignore[too-many-return-statements]
    pair: tuple[int, int], body: dict[str, Any] | bytes
) -> bool:
    """Validate App-backed Box writes before they reach the BLE transport."""
    if not isinstance(body, dict):
        return False
    if FIELD_CMD in body and body[FIELD_CMD] != pair[1]:
        return False
    fields = {key: value for key, value in body.items() if key != FIELD_CMD}

    def valid_int(value: object) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= _BOX_APP_INTEGER_MAX
        )

    single = {
        (2, 4): ("pss", {0, 1}),
        (3, 5): ("rc", {0, 1}),
        (4, 6): ("ups", {0, 1}),
        (17, 20): ("en", {0, 1, 2}),
    }.get(pair)
    if single is not None:
        name, allowed = single
        value = fields.get(name)
        return set(fields) == {name} and valid_int(value) and value in allowed
    if pair == (5, 7):
        return len(fields) == 1 and all(
            name in {"dt", "autoDt", "cdsDt", "selfDt"} and valid_int(value)
            for name, value in fields.items()
        )
    if pair == (6, 8):
        return (
            set(fields) == {"ps", "pst"}
            and valid_int(fields["ps"])
            and fields["ps"] in {0, 1}
            and valid_int(fields["pst"])
        )
    if pair in {(8, 11), (10, 13)}:
        if set(fields) != {"cir"} or not isinstance(fields["cir"], list):
            return False
        field = "sph" if pair == (8, 11) else "pr"
        return all(
            isinstance(item, dict)
            and set(item) == {"idx", field}
            and valid_int(item["idx"])
            and valid_int(item[field])
            and (field != "pr" or item[field] in {0, 1, 2})
            for item in fields["cir"]
        )
    if pair == (11, 14):
        return (
            set(fields) == {"idx", "nm"}
            and valid_int(fields["idx"])
            and isinstance(fields["nm"], str)
        )
    if pair == (16, 19):
        return set(fields) == {"ddt"} and valid_int(fields["ddt"])
    if pair == (23, 99):
        host = fields.get("host")
        port = fields.get("port")
        return (
            set(fields) == {"host", "port"}
            and isinstance(host, str)
            and bool(host)
            and host == host.strip()
            and not any(char.isspace() for char in host)
            and isinstance(port, int)
            and not isinstance(port, bool)
            and 1 <= port <= _BOX_APP_PORT_MAX
        )
    return False


_BLE_DATA_RESPONSE_MSG_TYPES: Final[frozenset[int]] = frozenset({
    MQTT_CMD_QUERY_DEVICE_PROPERTY,
    MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_CMD_QUERY_COMBINE_DATA,
    MQTT_CMD_QUERY_WEATHER_PLAN,
    MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
})

_PORTABLE_BLE_READ_ACTIONS: Final[frozenset[int]] = frozenset({
    ACTION_ID_PORTABLE_READ_WIFI_LIST,
    ACTION_ID_PORTABLE_READ_DEVICE_INFO,
    ACTION_ID_PORTABLE_GET_POWER_PACK_LIST,
    ACTION_ID_PORTABLE_GET_ELECTRICITY_DATA_COUNT,
    ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
    ACTION_ID_PORTABLE_CURRENT_CHARGE_PLAN,
    ACTION_ID_PORTABLE_GET_PEAKS_TROUGHS,
    ACTION_ID_PORTABLE_READ_SUB_CT,
    ACTION_ID_PORTABLE_GET_WIFI_CONFIG,
})


def exception_debug_message(err: BaseException) -> str:
    """Return a useful debug message for exceptions with empty ``str(err)``."""
    return f"{type(err).__name__}: {err or "(no message)"}"


def _raise_config_entry_auth_failed(message: str, err: JackeryAuthError) -> NoReturn:
    """Raise HA reauth trigger for rejected Jackery credentials."""
    msg = f"{message}. Re-authentication is required."
    raise ConfigEntryAuthFailed(msg) from err


ACTION_WRITE_ERRORS = (
    JackeryError,
    HomeAssistantError,
    TimeoutError,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
)

_SHA256_HEX_LENGTH = hashlib.sha256().digest_size * 2


class JackeryMqttCredentialsPendingError(HomeAssistantError):
    """Raised while the HTTP login session has not been cached yet.

    This is an expected start-up warm-up state, not an operational failure.
    HTTP is the only auth path and Layer 5 only ever uses cached credentials,
    so every transport necessarily fails until the first login lands. The
    bounded retry resolves it on its own; callers use this type to keep the
    warm-up out of the error log.
    """


_HTTP_BATTERY_PACK_RAW_SECTION: Final = "http_battery_pack_response"

_LOGGER = logging.getLogger(__name__)


def normalized_company_id(value: object) -> int | None:
    """Return a provider ID when the app payload encodes a whole number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text or not WHOLE_INT_TEXT_RE.fullmatch(text):
        return None
    try:
        return int(float(text))
    except ValueError as err:
        _LOGGER.debug("Discarding non-numeric provider ID %r: %s", text, err)
        return None


def normalized_region(value: object) -> str | None:
    """Return a normalized electricity-price region token."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def source_regions(source: dict[str, Any]) -> list[str]:
    """Extract and normalize region tokens from a price source."""
    raw = source.get(FIELD_SYSTEM_REGION) or source.get(FIELD_COUNTRY)
    if raw in {None, ""}:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def normalized_source_regions(source: dict[str, Any]) -> list[str]:
    """Return normalized region tokens for a price provider source."""
    regions: list[str] = []
    for region in source_regions(source):
        normalized = normalized_region(region)
        if normalized is not None and normalized not in regions:
            regions.append(normalized)
    return regions


def first_nonblank_source_name(source: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty provider display-name field."""
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def valid_price_sources(sources: object) -> list[dict[str, Any]]:
    """Return price provider dictionaries with a company id and region."""
    if not isinstance(sources, list):
        return []
    valid: list[dict[str, Any]] = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        company_id = normalized_company_id(item.get(FIELD_PLATFORM_COMPANY_ID))
        if company_id is None or not normalized_source_regions(item):
            continue
        valid.append(item)
    return valid


def _has_invalid_subdevice_serial(item: Mapping[str, Any]) -> bool:
    """Reject malformed values where the protocol requires a serial."""
    for field in (FIELD_DEVICE_SN, FIELD_DEV_SN, FIELD_SN):
        value = item.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            return True
        if value and (not value.strip() or not value.isprintable()):
            return True
    return False


def subdevice_serial(item: dict[str, Any]) -> str | None:
    """Return the stable serial field used by app subdevice payloads."""
    if _has_invalid_subdevice_serial(item):
        return None
    serial = item.get(FIELD_DEVICE_SN) or item.get(FIELD_DEV_SN) or item.get(FIELD_SN)
    if serial in {None, ""}:
        return None
    value = str(serial).strip()
    return value or None


def _subdevice_type_from_scan_name(scan_name: str) -> int | None:
    """Resolve the App's accessory catalogue without case sensitivity."""
    return next(
        (
            kind
            for name, kind in SUBDEVICE_SCAN_NAME_DEV_TYPES.items()
            if name.casefold() == scan_name.casefold()
        ),
        None,
    )


def subdevice_dev_type(
    item: Mapping[str, Any],
    *,
    rejection_callback: Callable[[str], None] | None = None,
) -> int | None:
    """Return the documented subdevice devType, including Shelly scan names."""
    rejection_reason: str | None = None
    raw_dev_type = item.get(FIELD_DEV_TYPE)
    if not _is_blank_value(raw_dev_type):
        try:
            return int(str(raw_dev_type))
        except TypeError:
            rejection_reason = "subdevice_dev_type_type_error"
        except ValueError:
            rejection_reason = "subdevice_dev_type_value_error"

    # Shelly Cloud uses ``deviceType`` for a textual product category such as
    # ``"relay"``.  Some Jackery payload variants also use that key for a
    # numeric devType, so accept numeric values without treating documented
    # textual categories as malformed numeric schema data.
    raw_device_type = item.get(FIELD_DEVICE_TYPE)
    if not _is_blank_value(raw_device_type):
        try:
            return int(str(raw_device_type))
        except (TypeError, ValueError):
            _LOGGER.debug(
                "Jackery: %s=%r is not numeric; falling back to scan-name resolution",
                FIELD_DEVICE_TYPE,
                raw_device_type,
            )
    scan_name = str(item.get(FIELD_SCAN_NAME) or "").lower()
    resolved_type = _subdevice_type_from_scan_name(scan_name)
    if resolved_type is not None:
        return resolved_type
    if rejection_reason is not None and rejection_callback is not None:
        rejection_callback(rejection_reason)
    return None


def _dict_list(value: object, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Copy dictionary entries from a list-like payload boundary."""
    if not isinstance(value, list | tuple):
        return []
    items = [dict(item) for item in value if isinstance(item, dict)]
    return items if limit is None else items[:limit]


_THIRD_PARTY_MQTT_READBACK_TIMEOUT_SEC = 30.0
_THIRD_PARTY_MQTT_TRANSACTION_TIMEOUT_SEC = 30.0

_THIRD_PARTY_MQTT_READBACK_ATTEMPTS = 3

_THIRD_PARTY_MQTT_READBACK_ATTEMPT_TIMEOUT_SEC = 5.0

_THIRD_PARTY_MQTT_READBACK_RETRY_DELAY_SEC = 1.0

_CT_MODEL_CONFLICT_KEY: Final = ("jackery_ct_model_conflict",)


class _MqttSessionChangedBeforePublishError(RuntimeError):
    """A cloud command lost its session before attempting publication."""


@dataclass(slots=True)
class _CloudCommandAttempt:
    """Bind one Cloud command result to its actual MQTT session and attempt."""

    command_key: tuple[str, int, int]
    attempt_id: int
    session_generation: int
    mqtt_owner: JackeryMqttPushClient | None = None
    expected_mqtt_fingerprint: str | None = None
    expected_client_generation: int | None = None
    expected_read_device_sn: str | None = None
    expected_reserve_model: int | None = None
    expected_ups_identity: tuple[str, str] | None = None
    expected_box_charge_identity: tuple[int, str] | None = None
    identity_guard: Callable[[], bool] | None = None

    def __init__(  # ruff: ignore[too-many-arguments]
        self,
        command_key: tuple[str, int, int],
        attempt_id: int,
        session_generation: int,
        *,
        mqtt_owner: JackeryMqttPushClient | None = None,
        expected_mqtt_fingerprint: str | None = None,
        expected_client_generation: int | None = None,
        expected_read_device_sn: str | None = None,
        expected_reserve_model: int | None = None,
        expected_ups_identity: tuple[str, str] | None = None,
        expected_box_charge_identity: tuple[int, str] | None = None,
        identity_guard: Callable[[], bool] | None = None,
    ) -> None:
        self.command_key = command_key
        self.attempt_id = attempt_id
        self.session_generation = session_generation
        self.mqtt_owner = mqtt_owner
        self.expected_mqtt_fingerprint = expected_mqtt_fingerprint
        self.expected_client_generation = expected_client_generation
        self.expected_read_device_sn = expected_read_device_sn
        self.expected_reserve_model = expected_reserve_model
        self.expected_ups_identity = expected_ups_identity
        self.expected_box_charge_identity = expected_box_charge_identity
        self.identity_guard = identity_guard


@dataclass(frozen=True, slots=True)
class _ThirdPartyMqttReadbackContext:
    """Bind a readback wait to its local ingress barrier and physical owner."""

    received_after: float
    device_sn: str | None


@dataclass(frozen=True, slots=True)
class _ThirdPartyMqttReadbackSnapshot:
    """Keep the last received full config independent of delayed ingest commits."""

    received_at: float
    device_sn: str
    config: dict[str, Any]


class _BleModelOptions(TypedDict, total=False):
    expected_model: int
    expected_ups_identity: tuple[str, str]
    identity_guard: Callable[[], bool]


class _PublishCommandRequired(TypedDict):
    message_type: str
    action_id: int
    cmd: int
    body_fields: dict[str, Any]


class _PublishCommandOptions(_PublishCommandRequired, total=False):
    ensure_mqtt: bool
    cloud_attempt: _CloudCommandAttempt | None


class _PublishAllTransportOptions(_PublishCommandRequired, total=False):
    ble_extra_body_fields: dict[str, Any] | None
    ensure_mqtt: bool
    allow_ble: bool
    allow_cloud: bool
    allow_local_mqtt: bool
    wait_for_all_transports: bool
    http_read: _DocumentedHttpReadOptions
    identity_guard: Callable[[], bool]


class _ThirdPartyMqttRequired(TypedDict):
    enable: bool
    ip: str
    port: int


class _ThirdPartyMqttOptions(_ThirdPartyMqttRequired, total=False):
    username: str
    password: str
    token: str


class _DocumentedHttpReadOptions(TypedDict, total=False):
    device_property: bool
    system_shadow: bool
    battery_packs: bool
    subdevice_dev_type: int | None


type _DocumentedHttpRequest = tuple[
    str,
    int | None,
    str | None,
    Callable[[], Awaitable[Any]],
]


if TYPE_CHECKING:
    from datetime import datetime
    from typing import Literal

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, callback

    from ..coordinator import (
        MqttConnectionManager,
        _BleSendOptions,
        _MergePropertiesOptions,
        _OriginalFrameOptions,
    )
    from .api import JackeryApi
    from .ble_events import _BleGroundIdentity


class JackeryCommandDispatcherMixin:  # ruff: ignore[too-many-public-methods]
    """Mixin handling outbound transport commands and device control actions."""

    if TYPE_CHECKING:
        _CT_METER_KEYS: ClassVar[frozenset[str]]
        _SHADOW_DEV_TYPE_BUCKETS: ClassVar[dict[int, str]]
        _SUBDEVICE_MAIN_MIRROR_KEYS: ClassVar[frozenset[str]]
        _SYSTEM_INFO_KEYS: ClassVar[frozenset[str]]

        _accessory_source_state: dict[tuple[str, str, str], IngestResult]

        _ble_ground_recovery_triggers: dict[str, int]

        _ble_listener: Any

        _ble_ota_pending: dict[str, dict[str, str]] | None

        _ble_ota_recovered: dict[str, tuple[Any, ...]]

        _ble_ota_recovery_active: set[str]

        _ble_ota_recovery_done: dict[str, asyncio.Event]

        _ble_ota_recovery_retry_at: dict[str, float]

        _ble_ota_versions_fingerprint: dict[str, bytes | None]

        _ble_ota_versions_first_sequence: dict[str, int | None]

        _ble_ota_versions_generation: dict[str, int | None]

        _ble_ota_versions_observed_at: dict[str, datetime]

        _ble_ota_candidate_receipts: dict[tuple[str, str], dict[str, Any]]

        _cloud_mqtt_command_attempt_sequence: int

        _cloud_mqtt_command_attempts: dict[tuple[str, int, int], int]

        _cloud_mqtt_command_failures: dict[tuple[str, int, int], str]

        _device_index: dict[str, dict[str, Any]]

        _device_registry_observer: Callable[[Mapping[str, dict[str, Any]]], None] | None

        _generated_third_party_mqtt_token: str | None

        _http_pack_original_generations: dict[str, int]

        _last_http_device_refresh_monotonic: dict[str, float]

        _local_mqtt_config_observer: Callable[[dict[str, Any]], None] | None

        _mqtt: JackeryMqttPushClient | None

        _mqtt_mgr: MqttConnectionManager

        _mqtt_session_actions_seen: set[tuple[str, int]]

        _portable_reserve_override_barriers: dict[
            tuple[str, str], tuple[object | None, tuple[int, int] | None]
        ]

        _shutdown_started: bool

        _third_party_mqtt_config_waiters: dict[
            str, list[asyncio.Future[dict[str, Any]]]
        ]

        _third_party_mqtt_readback_contexts: dict[
            asyncio.Future[dict[str, Any]], _ThirdPartyMqttReadbackContext
        ]

        _third_party_mqtt_readback_snapshots: dict[str, _ThirdPartyMqttReadbackSnapshot]

        _third_party_mqtt_readback_receipts: dict[str, tuple[str, float]]

        _third_party_mqtt_readback_tokens: dict[str, tuple[str, str]]

        _third_party_mqtt_transaction_locks: dict[str, asyncio.Lock]

        api: JackeryApi

        data: dict[str, dict[str, Any]]

        entry: ConfigEntry

        hass: HomeAssistant

        # Cross-mixin methods implemented by the coordinator and BLE ingest mixin.
        def _ble_writes_enabled(self) -> bool:
            raise NotImplementedError

        def box_ble_ota_allowed(self, device_id: str, generation: int) -> bool:
            """Contract for the BLE ingest mixin's Box OTA safety check."""
            raise NotImplementedError

        @classmethod
        def _battery_packs_from_source(
            cls,
            source: object,
            *,
            rejection_callback: Callable[[str], None] | None = None,
        ) -> list[dict[str, Any]] | None:
            raise NotImplementedError

        @staticmethod
        def _entry_accessories(entry: dict[str, Any]) -> list[dict[str, Any]]:
            raise NotImplementedError

        @staticmethod
        def _find_dict_with_any_key(
            obj: object, keys: frozenset[str]
        ) -> dict[str, Any] | None:
            raise NotImplementedError

        @staticmethod
        def _property_value_present(value: object) -> bool:
            raise NotImplementedError

        def _apply_local_device_name_patch(self, device_id: str, new_name: str) -> None:
            raise NotImplementedError

        def _apply_local_price_patch(
            self, device_id: str, updates: dict[str, Any]
        ) -> None:
            raise NotImplementedError

        def _apply_local_property_patch(
            self, device_id: str, updates: dict[str, Any]
        ) -> None:
            raise NotImplementedError

        def _apply_local_system_name_patch(self, system_id: str, new_name: str) -> None:
            raise NotImplementedError

        def _apply_local_system_patch(
            self, device_id: str, updates: dict[str, Any]
        ) -> None:
            raise NotImplementedError

        def _apply_local_weather_plan_patch(
            self, device_id: str, updates: dict[str, Any]
        ) -> None:
            raise NotImplementedError

        async def _async_ensure_mqtt(
            self,
            *,
            force: bool = False,
            wait_connected: bool = False,
            cloud_attempt: _CloudCommandAttempt | None = None,
        ) -> None:
            raise NotImplementedError

        async def _async_refresh_http_accessories(self) -> list[BaseException]:
            raise NotImplementedError

        async def _async_http_call(self, call: Callable[[], Awaitable[Any]]) -> Any:  # ruff: ignore[any-type]
            raise NotImplementedError

        def _ble_ota_model_evidence(self, device_id: str) -> tuple[Any, ...]:
            raise NotImplementedError

        def _ble_ota_profile_matches(self, device_id: str, profile: str | None) -> bool:
            raise NotImplementedError

        def _ble_ota_report_matches_target(
            self,
            device_id: str,
            generation: int,
            target_sn: str,
            *,
            allow_started: bool = False,
        ) -> bool:
            raise NotImplementedError

        def _ble_ota_target_is_owned(
            self, device_id: str, generation: int, target_sn: str
        ) -> bool:
            raise NotImplementedError

        def _ble_pending_target_is_owned(
            self,
            device_id: str,
            generation: int,
            target_sn: str,
            pending: dict[str, str],
        ) -> bool:
            raise NotImplementedError

        def _box_safety_device_identity(self, device_id: str) -> tuple[int, str] | None:
            raise NotImplementedError

        @staticmethod
        def _ground_ble_session_fingerprint(identity: _BleGroundIdentity) -> str:
            raise NotImplementedError

        def _http_data_conflicts_with_parent(
            self,
            device_id: str,
            parent_binding: tuple[tuple[object, ...] | None, int, str | None],
        ) -> bool:
            raise NotImplementedError

        def _http_parent_binding(
            self, device_id: str
        ) -> tuple[tuple[object, ...] | None, int, str | None]:
            raise NotImplementedError

        def _http_parent_binding_is_current(
            self,
            device_id: str,
            binding: tuple[tuple[object, ...] | None, int, str | None],
        ) -> bool:
            raise NotImplementedError

        def _http_property_response_matches_owner(
            self,
            device_id: str,
            result: object,
            binding: tuple[tuple[object, ...] | None, int, str | None],
        ) -> bool:
            raise NotImplementedError

        def _invalidate_system_cache(
            self, system_id: str | None, *cache_keys: str
        ) -> None:
            raise NotImplementedError

        def _merge_main_properties_for_device(
            self,
            device_id: str,
            base: dict[str, Any],
            updates: dict[str, Any],
            **options: Unpack[_MergePropertiesOptions],
        ) -> dict[str, Any]:
            raise NotImplementedError

        def _merge_subdevice_data(
            self,
            updated: dict[str, Any],
            source: dict[str, Any],
            *,
            device_id: str | None = None,
            source_transport: TransportSource = TransportSource.CLOUD_MQTT,
            observed_at: datetime | None = None,
            **options: Unpack[_OriginalFrameOptions],
        ) -> bool:
            raise NotImplementedError

        def _merge_system_info_fields(
            self, device_id: str, working: dict[str, Any], system_body: dict[str, Any]
        ) -> bool:
            raise NotImplementedError

        def _push_partial_update(  # ruff: ignore[too-many-arguments]
            self,
            new_data: dict[str, dict[str, Any]],
            *,
            source: TransportSource | None = None,
            observed_at: datetime | None = None,
            allow_during_shutdown: bool = False,
            battery_packs_complete: bool = False,
            acknowledge_unchanged: bool = False,
            request_id: str | None = None,
            on_commit: Callable[[], None] | None = None,
            mark_success: bool = True,
            replace_device_ids: frozenset[str] = frozenset(),
            remove_device_ids: frozenset[str] = frozenset(),
            record_source_payload: bool = True,
            weather_plan_rows: Mapping[str, list[Any]] | None = None,
            notify_listeners: bool = True,
        ) -> bool:
            raise NotImplementedError

        def _record_layer_source_payload(
            self,
            observation: Observation,
            identity: str = "",
            *,
            original_frame: Mapping[str, object] | None = None,
            original_payload: Mapping[str, object] | None = None,
            _target_state: dict[tuple[str, str, str], dict[str, dict[str, Any]]]
            | None = None,
        ) -> None:
            raise NotImplementedError

        def _remember_local_mqtt_device_token(
            self, device_id: str, token: object
        ) -> None:
            raise NotImplementedError

        def _require_command_family(
            self, device_id: str, action_id: int, cmd: int
        ) -> str:
            raise NotImplementedError

        def _require_home_config_context(self, device_id: str, action: str) -> None:
            raise NotImplementedError

        def _resolve_device_sn(self, device_id: str) -> str | None:
            raise NotImplementedError

        def _resolve_system_id(self, device_id: str) -> str | None:
            raise NotImplementedError

        @classmethod
        def _sanitize_main_properties(cls, props: dict[str, Any]) -> dict[str, Any]:
            raise NotImplementedError

        def _schedule_payload_debug_event(
            self, event_or_factory: dict[str, Any] | Callable[[], dict[str, Any]]
        ) -> None:
            raise NotImplementedError

        @staticmethod
        def _shadow_parent_device_sn(entry: dict[str, Any]) -> str | None:
            raise NotImplementedError

        @staticmethod
        def _shadow_system_id(entry: dict[str, Any]) -> str | None:
            raise NotImplementedError

        @classmethod
        def _subdevice_identity_values(cls, item: Mapping[str, Any]) -> set[str]:
            raise NotImplementedError

        def _synchronize_mqtt_session_generation(self) -> int:
            raise NotImplementedError

        def _transport_source_freshness_window(self) -> float:
            raise NotImplementedError

        async def async_request_refresh(self) -> None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        async def async_send_ble_command(
            self, device_id: str, **options: Unpack[_BleSendOptions]
        ) -> bool:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        async def async_start_mqtt(self) -> None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        async def async_start_ble_transport(self) -> None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def _schedule_background_once(
            self,
            key: str,
            factory: Callable[[], Coroutine[Any, Any, Any]],
            *,
            name: str,
        ) -> asyncio.Task[Any] | None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        @callback
        def async_update_listeners(self) -> None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def box_ble_ota_target_allowed(
            self, device_id: str, generation: int, target_sn: str
        ) -> bool:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def box_charge_now_allowed(
            self, device_id: str, enabled: bool, *, require_rc: bool = False
        ) -> bool:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def device_ble_profile(self, device_id: str) -> str | None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def device_bluetooth_key(self, device_id: str) -> bytes | None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def device_family(
            self, device_id: str
        ) -> Literal["home", "box", "portable", "unknown"]:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def device_model_code(self, device_id: str) -> int | None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def device_ups_identity(self, device_id: str) -> tuple[str, str] | None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def ground_ble_identity(
            self, device_id: str, *, require_connected: bool = True
        ) -> _BleGroundIdentity | None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def ground_ble_ota_target(self, device_id: str) -> str | None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def home_ble_ota_allowed(self, device_id: str, generation: int) -> bool:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def portable_ble_ota_allowed(self, device_id: str, generation: int) -> bool:
            """Provide the coordinator host operation."""
            raise NotImplementedError

    @staticmethod
    def _coerce_transport_cmd(cmd: object) -> int:
        """Coerce transport cmd input to an integer.

        Accepts plain ints plus integral numeric strings (e.g. ``"107"``,
        ``"107.0"``). Rejects booleans, NaN/inf and non-integral values.
        """
        if isinstance(cmd, bool):
            # ValueError (not TypeError) on purpose: the callers at the
            # transport boundary catch ValueError for every coercion failure.
            msg = "cmd must be an integer"
            raise ValueError(msg)  # ruff: ignore[type-check-without-type-error]  # Transport callers normalize coercion failures as ValueError.
        if isinstance(cmd, int):
            return cmd
        if isinstance(cmd, float):
            if not math.isfinite(cmd) or not cmd.is_integer():
                msg = "cmd must be an integer"
                raise ValueError(msg)
            return int(cmd)
        if isinstance(cmd, str):
            text = cmd.strip()
            if not text:
                msg = "cmd must be an integer"
                raise ValueError(msg)
            try:
                return int(text, 10)
            except ValueError as err:
                _LOGGER.debug(
                    "cmd %r is not a base-10 integer, trying float parse: %s",
                    text,
                    err,
                )
            try:
                parsed = float(text)
            except ValueError as err:
                _LOGGER.debug("cmd %r is not a parseable float: %s", text, err)
            else:
                if math.isfinite(parsed) and parsed.is_integer():
                    return int(parsed)
            msg = "cmd must be an integer"
            raise ValueError(msg)
        msg = "cmd must be an integer"
        raise ValueError(msg)

    @staticmethod
    def _command_body_for_transport(
        body_fields: dict[str, Any],
        *,
        cmd: object,
    ) -> dict[str, Any]:
        """Build the command body shared by MQTT and BLE command transports."""
        body: dict[str, Any] = dict(body_fields)
        cmd_value = JackeryCommandDispatcherMixin._coerce_transport_cmd(cmd)
        if cmd_value > 0:
            body[FIELD_CMD] = cmd_value
        return body

    def _record_independent_cloud_mqtt_result(
        self,
        cloud_attempt: _CloudCommandAttempt,
        succeeded: list[str],
        failures: list[str],
    ) -> None:
        """Log a changed Cloud MQTT failure when another transport succeeded."""
        if (
            cloud_attempt.mqtt_owner is not self._mqtt
            or cloud_attempt.session_generation
            != self._synchronize_mqtt_session_generation()
            or self._cloud_mqtt_command_attempts.get(cloud_attempt.command_key)
            != cloud_attempt.attempt_id
        ):
            return
        command_key = cloud_attempt.command_key
        if not hasattr(self, "_cloud_mqtt_command_failures"):
            self._cloud_mqtt_command_failures = {}
        if "Cloud MQTT" in succeeded:
            self._cloud_mqtt_command_failures.pop(command_key, None)
            return
        cloud_failure = next(
            (failure for failure in failures if failure.startswith("Cloud MQTT=")),
            None,
        )
        if cloud_failure is None:
            return
        previous_failure = self._cloud_mqtt_command_failures.get(command_key)
        self._cloud_mqtt_command_failures[command_key] = cloud_failure
        if not succeeded:
            # The all-transports-failed branch logs the command error below.
            # Retain this Cloud-specific failure so its own retry decision does
            # not depend on BLE availability or success.
            return
        log = _LOGGER.warning if previous_failure != cloud_failure else _LOGGER.debug
        device_id, action_id, cmd = command_key
        log(
            "Jackery Cloud MQTT command failed independently for %s "
            "actionId=%s cmd=%s: %s; successful transports remain active",
            device_id,
            action_id,
            cmd,
            cloud_failure,
        )

    def _bind_cloud_command_attempt(
        self,
        cloud_attempt: _CloudCommandAttempt | None,
    ) -> None:
        """Bind an attempt immediately before use of the current MQTT session."""
        if cloud_attempt is None:
            return
        cloud_attempt.mqtt_owner = self._mqtt
        cloud_attempt.session_generation = self._synchronize_mqtt_session_generation()
        if not hasattr(self, "_cloud_mqtt_command_attempts"):
            self._cloud_mqtt_command_attempts = {}
        previous_attempt = self._cloud_mqtt_command_attempts.get(
            cloud_attempt.command_key,
            0,
        )
        if cloud_attempt.attempt_id >= previous_attempt:
            self._cloud_mqtt_command_attempts[cloud_attempt.command_key] = (
                cloud_attempt.attempt_id
            )

    def _cloud_mqtt_action_needs_retry(
        self,
        device_id: str,
        action_id: int,
        *,
        require_response: bool = True,
    ) -> bool:
        """Return whether this Cloud action still needs an independent retry."""
        if self._mqtt is None or not self._mqtt.is_connected:
            return False
        publish_failed = any(
            failed_device_id == device_id and failed_action_id == action_id
            for failed_device_id, failed_action_id, _cmd in (
                self._cloud_mqtt_command_failures
            )
        )
        return publish_failed or (
            require_response
            and (device_id, action_id) not in self._mqtt_session_actions_seen
        )

    def _collect_command_transport_results(
        self,
        operations: list[tuple[str, asyncio.Task[bool]]],
        results: list[Any],
        succeeded: list[str],
        failures: list[str],
    ) -> Exception | None:
        """Collect independent command results without masking cancellation."""
        first_error: Exception | None = None
        for (label, _task), result in zip(operations, results, strict=True):
            if result is True:
                succeeded.append(label)
                continue
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                first_error = first_error or result
                error = redacted_error(result, entry=getattr(self, "entry", None))
                failures.append(f"{label}={error}")
                continue
            failures.append(f"{label}=unavailable")
        return first_error

    def _record_successful_command_transports(
        self,
        cloud_attempt: _CloudCommandAttempt,
        succeeded: list[str],
        failures: list[str],
    ) -> None:
        """Record and log a command that succeeded on at least one transport."""
        self._record_independent_cloud_mqtt_result(
            cloud_attempt,
            succeeded,
            failures,
        )
        command_key = cloud_attempt.command_key
        device_id, action_id, cmd_value = command_key
        _LOGGER.debug(
            "Jackery command succeeded for %s actionId=%s cmd=%s via %s%s",
            device_id,
            action_id,
            cmd_value,
            ", ".join(succeeded),
            f"; independent failures: {", ".join(failures)}" if failures else "",
        )

    async def _async_finish_independent_command_transports(
        self,
        cloud_attempt: _CloudCommandAttempt,
        operations: list[tuple[str, asyncio.Task[bool]]],
        succeeded: list[str],
        failures: list[str],
    ) -> None:
        """Observe transports that continue after another one succeeded."""
        results = await asyncio.gather(
            *(task for _label, task in operations),
            return_exceptions=True,
        )
        self._collect_command_transport_results(
            operations,
            results,
            succeeded,
            failures,
        )
        self._record_successful_command_transports(
            cloud_attempt,
            succeeded,
            failures,
        )

    def _new_cloud_command_attempt(
        self,
        device_id: str,
        action_id: int,
        cmd_value: int,
        *,
        register_cloud: bool = True,
    ) -> _CloudCommandAttempt:
        """Create one attempt; BLE-only reads do not supersede Cloud results."""
        self._cloud_mqtt_command_attempt_sequence = (
            getattr(self, "_cloud_mqtt_command_attempt_sequence", 0) + 1
        )
        attempt = _CloudCommandAttempt(
            command_key=(device_id, action_id, cmd_value),
            attempt_id=self._cloud_mqtt_command_attempt_sequence,
            session_generation=getattr(self, "_mqtt_session_generation", 0),
            expected_reserve_model=(
                self.device_model_code(device_id)
                if (
                    action_id
                    in {
                        ACTION_ID_PORTABLE_RESERVE_SWITCH,
                        ACTION_ID_PORTABLE_RESERVE_THRESHOLD,
                    }
                    or (
                        action_id
                        in {
                            ACTION_ID_PORTABLE_ENERGY_SAVING,
                            ACTION_ID_PORTABLE_USE_POWER_MODE,
                        }
                        and cmd_value == PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id]
                    )
                    or (
                        (action_id, cmd_value) == _BOX_WORKING_MODE_PAIR
                        and self.device_family(device_id) == "box"
                    )
                )
                else None
            ),
        )
        if register_cloud:
            self._bind_cloud_command_attempt(attempt)
        return attempt

    def _mqtt_read_retry_session_valid(
        self,
        mqtt: JackeryMqttPushClient,
        fingerprint: str,
        generation: int,
        *,
        require_connected: bool,
    ) -> bool:
        """Check that a read retry still owns the same permitted session."""
        if self._mqtt is not mqtt or self._shutdown_started:
            return False
        if not (mqtt.is_started or mqtt.is_connected):
            return False
        if require_connected and not mqtt.is_connected:
            return False
        if (
            self._mqtt_mgr.paused_until_monotonic > time.monotonic()
            or (
                safe_float(mqtt.diagnostics.get("session_takeover_backoff_seconds"))
                or 0
            )
            > 0
        ):
            return False
        if self.api.mqtt_fingerprint != fingerprint:
            return False
        credentials = self.api.get_cached_mqtt_credentials()
        return credentials is not None and mqtt.session_matches_credentials(
            **credentials, generation=generation
        )

    async def _async_send_cloud_command_transport(
        self,
        device_id: str,
        cmd_value: int,
        options: _PublishAllTransportOptions,
        cloud_attempt: _CloudCommandAttempt,
    ) -> bool:
        """Send one command over Cloud MQTT and normalize success."""
        is_config_read = (
            options["action_id"] == ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG
            and cmd_value == MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG
        )
        mqtt = self._mqtt if is_config_read else None
        fingerprint = self.api.mqtt_fingerprint if is_config_read else None
        target_sn = self._resolve_device_sn(device_id) if is_config_read else None
        if is_config_read:
            cloud_attempt.expected_read_device_sn = target_sn
        try:
            await self._async_publish_command(
                device_id,
                message_type=options["message_type"],
                action_id=options["action_id"],
                cmd=cmd_value,
                body_fields=options["body_fields"],
                ensure_mqtt=options.get("ensure_mqtt", True),
                cloud_attempt=cloud_attempt,
            )
        except _MqttSessionChangedBeforePublishError as original_error:
            if not is_config_read:
                raise
            if mqtt is None or fingerprint is None:
                raise
            generation = mqtt.session_generation
            if not self._mqtt_read_retry_session_valid(
                mqtt, fingerprint, generation, require_connected=False
            ):
                raise
            if not mqtt.is_connected:
                await mqtt.async_wait_until_connected(timeout_sec=30.0)
            if not self._mqtt_read_retry_session_valid(
                mqtt, fingerprint, generation, require_connected=True
            ):
                raise
            if target_sn is None or self._resolve_device_sn(device_id) != target_sn:
                msg = "MQTT read target changed during command retry"
                raise HomeAssistantError(msg) from original_error
            retry = self._new_cloud_command_attempt(
                device_id, options["action_id"], cmd_value
            )
            retry.expected_mqtt_fingerprint = fingerprint
            retry.expected_client_generation = generation
            retry.expected_read_device_sn = target_sn
            try:
                await self._async_publish_command(
                    device_id,
                    message_type=options["message_type"],
                    action_id=options["action_id"],
                    cmd=cmd_value,
                    body_fields=options["body_fields"],
                    ensure_mqtt=options.get("ensure_mqtt", True),
                    cloud_attempt=retry,
                )
            except Exception as err:
                previous_failure = getattr(
                    self, "_cloud_mqtt_command_failures", {}
                ).get(retry.command_key)
                error = redacted_error(err, entry=getattr(self, "entry", None))
                self._record_independent_cloud_mqtt_result(
                    retry, [], [f"Cloud MQTT={error}"]
                )
                current_failure = getattr(self, "_cloud_mqtt_command_failures", {}).get(
                    retry.command_key
                )
                if current_failure is not None and current_failure != previous_failure:
                    _LOGGER.warning(
                        "Jackery Cloud MQTT read retry failed for %s actionId=%s "
                        "cmd=%s: %s",
                        device_id,
                        options["action_id"],
                        cmd_value,
                        error,
                    )
                raise
            self._record_independent_cloud_mqtt_result(retry, ["Cloud MQTT"], [])
        return True

    def _command_transport_operations(
        self,
        device_id: str,
        cmd_value: int,
        options: _PublishAllTransportOptions,
        cloud_attempt: _CloudCommandAttempt,
    ) -> list[tuple[str, Coroutine[Any, Any, bool]]]:
        """Build independently executable command and documented HTTP reads."""
        action_id = options["action_id"]
        body_fields = options["body_fields"]
        operations: list[tuple[str, Coroutine[Any, Any, bool]]] = []
        http_read = options.get("http_read")
        if http_read and any(http_read.values()):
            operations.append((
                "HTTP",
                self.async_refresh_documented_http_read(device_id, **http_read),
            ))
        ble_supported = not isinstance(action_id, bool) and (
            (action_id, cmd_value) in _HOME_BLE_COMMAND_PAIRS
            or PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID.get(action_id) == cmd_value
            or (
                (action_id, cmd_value) in _BOX_BLE_COMMAND_PAIRS
                and self.device_ble_profile(device_id) == "box_fixed"
            )
        )
        if options.get("allow_ble", True) and ble_supported:
            model_options: _BleModelOptions = {}
            expected_model = getattr(cloud_attempt, "expected_reserve_model", None)
            if expected_model is not None:
                model_options["expected_model"] = expected_model
            expected_ups_identity = getattr(
                cloud_attempt, "expected_ups_identity", None
            )
            if expected_ups_identity is not None:
                model_options["expected_ups_identity"] = expected_ups_identity
            identity_guard = getattr(cloud_attempt, "identity_guard", None)
            if identity_guard is not None:
                model_options["identity_guard"] = identity_guard
            ble_body_fields = options.get("ble_extra_body_fields")
            transport_body = (
                body_fields
                if ble_body_fields is None
                else {**body_fields, **ble_body_fields}
            )
            operations.append((
                "BLE",
                self.async_send_ble_command(
                    device_id,
                    cmd=cmd_value,
                    flags=action_id,
                    body=(
                        dict(transport_body)
                        if self._is_portable_device_id(device_id)
                        else self._command_body_for_transport(
                            transport_body,
                            cmd=cmd_value,
                        )
                    ),
                    wait_for_ack=(
                        cmd_value not in _BLE_DATA_RESPONSE_MSG_TYPES
                        and not (
                            action_id in _PORTABLE_BLE_READ_ACTIONS
                            and self._is_portable_device_id(device_id)
                        )
                        and not (
                            (action_id, cmd_value) in _BOX_BLE_READ_PAIRS
                            and self.device_ble_profile(device_id) == "box_fixed"
                        )
                    ),
                    connect_timeout_sec=BLE_COMMAND_CONNECT_TIMEOUT_SEC,
                    **model_options,
                ),
            ))
        if options.get("allow_cloud", True) and (
            options.get("ensure_mqtt", True)
            or (self._mqtt is not None and self._mqtt.is_connected)
        ):
            operations.append((
                "Cloud MQTT",
                self._async_send_cloud_command_transport(
                    device_id,
                    cmd_value,
                    options,
                    cloud_attempt,
                ),
            ))
        # The third-party bridge publishes device telemetry to the broker;
        # the App supplies no local MQTT command topic or setter contract.
        return operations

    def _create_command_task(
        self,
        label: str,
        operation: Coroutine[Any, Any, bool],
        cloud_attempt: _CloudCommandAttempt,
    ) -> asyncio.Task[bool]:
        """Create a tracked command task, falling back for test shells."""
        _device_id, action_id, cmd_value = cloud_attempt.command_key
        task_name = (
            f"{DOMAIN}_command_{action_id}_{cmd_value}_"
            f"{label.lower().replace(" ", "_")}"
        )
        entry = getattr(self, "entry", None)
        if entry is not None:
            return cast(
                "asyncio.Task[bool]",
                entry.async_create_background_task(
                    self.hass,
                    operation,
                    name=task_name,
                    eager_start=False,
                ),
            )
        return cast(
            "asyncio.Task[bool]",
            self.hass.async_create_task(
                operation,
                name=task_name,
                eager_start=False,
            ),
        )

    async def _async_wait_for_command_transport_result(  # ruff: ignore[too-many-locals]
        self,
        cloud_attempt: _CloudCommandAttempt,
        task_operations: list[tuple[str, asyncio.Task[bool]]],
        *,
        wait_for_all_transports: bool = False,
    ) -> tuple[list[str], list[str], Exception | None]:
        """Wait until one transport succeeds or every transport finishes."""
        pending = {task for _label, task in task_operations}
        succeeded: list[str] = []
        failures: list[str] = []
        first_error: Exception | None = None
        while pending:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            completed = [
                (label, task) for label, task in task_operations if task in done
            ]
            results = await asyncio.gather(
                *(task for _label, task in completed),
                return_exceptions=True,
            )
            batch_error = self._collect_command_transport_results(
                completed,
                results,
                succeeded,
                failures,
            )
            first_error = first_error or batch_error
            if not succeeded:
                continue
            if not pending:
                self._record_successful_command_transports(
                    cloud_attempt,
                    succeeded,
                    failures,
                )
                return succeeded, failures, first_error

            if wait_for_all_transports:
                continue

            pending_operations = [
                (label, task) for label, task in task_operations if task in pending
            ]
            remainder = self._async_finish_independent_command_transports(
                cloud_attempt,
                pending_operations,
                list(succeeded),
                list(failures),
            )
            _device_id, action_id, cmd_value = cloud_attempt.command_key
            remainder_name = f"{DOMAIN}_command_remainder_{action_id}_{cmd_value}"
            entry = getattr(self, "entry", None)
            if entry is not None:
                entry.async_create_background_task(
                    self.hass,
                    remainder,
                    name=remainder_name,
                    eager_start=False,
                )
            else:
                remainder_task = self.hass.async_create_task(
                    remainder,
                    name=remainder_name,
                    eager_start=False,
                )
                remainder_task.add_done_callback(lambda done: done.exception())
            return succeeded, failures, first_error
        return succeeded, failures, first_error

    @staticmethod
    async def _async_cancel_command_tasks(
        task_operations: list[tuple[str, asyncio.Task[bool]]],
    ) -> None:
        """Cancel and drain every command task owned by one caller."""
        tasks = [task for _label, task in task_operations]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _async_publish_command_ble_first(  # ruff: ignore[too-many-locals, too-many-branches, too-many-statements]
        self,
        device_id: str,
        **options: Unpack[_PublishAllTransportOptions],
    ) -> None:
        """Write independently through every supported app command transport.

        BLE and Cloud MQTT are started concurrently. A failure or unavailable
        state on one transport never prevents the other transport from sending;
        the command succeeds when at least one supported transport succeeds.
        The historical method name is retained for call-site compatibility.
        """
        action_id = options["action_id"]
        cmd_value = self._coerce_transport_cmd(options["cmd"])
        bound_model = None
        bound_identity = None
        bound_box_charge_identity = None
        if (
            {"lm", "bpc"} & options["body_fields"].keys()  # ruff: ignore[too-many-boolean-expressions]
            or _ALL_PORTABLE_PRIORITY_FIELDS & options["body_fields"].keys()
            or action_id in _PORTABLE_PRIORITY_FIELDS
            or action_id in _PORTABLE_UPS_FIELDS
            or (
                self.device_family(device_id) == "portable"
                and _ALL_PORTABLE_UPS_FIELDS & options["body_fields"].keys()
            )
        ):
            body_snapshot = dict(options["body_fields"])
            model = self.device_model_code(device_id)
            if action_id == ACTION_ID_PORTABLE_UPS_MODEL and model in {13, 22}:
                bound_identity = self.device_ups_identity(device_id)
            bound_model = _portable_bound_command_model(
                action_id,
                cmd_value,
                model,
                self.device_family(device_id),
                body_snapshot,
                bound_identity[0] if bound_identity is not None else None,
            )
            if bound_model is not None:
                options["body_fields"] = body_snapshot
        if (action_id, cmd_value) == _BOX_WORKING_MODE_PAIR and self.device_family(
            device_id
        ) == "box":
            body_snapshot = dict(options["body_fields"])
            if self.device_model_code(
                device_id
            ) not in _BOX_WORKING_MODE_MODELS or not _box_app_write_is_valid(
                _BOX_WORKING_MODE_PAIR, body_snapshot
            ):
                msg = "Invalid Box working mode or model"
                raise HomeAssistantError(msg)
            options["body_fields"] = body_snapshot
        if (action_id, cmd_value) == _BOX_CHARGE_NOW_PAIR and self.device_family(
            device_id
        ) == "box":
            body_snapshot = dict(options["body_fields"])
            bound_box_charge_identity = self._box_safety_device_identity(device_id)
            if (
                bound_box_charge_identity is None
                or not _box_app_write_is_valid(_BOX_CHARGE_NOW_PAIR, body_snapshot)
                or not self.box_charge_now_allowed(
                    device_id, body_snapshot.get("rc") == 1
                )
            ):
                msg = "Box charge-now command lacks fresh safety evidence"
                raise HomeAssistantError(msg)
            bound_model = bound_box_charge_identity[0]
            options["body_fields"] = body_snapshot
        if (
            action_id
            in {
                ACTION_ID_PORTABLE_ENERGY_SAVING,
                ACTION_ID_PORTABLE_USE_POWER_MODE,
            }
            and cmd_value == PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id]
        ):
            body_snapshot = dict(options["body_fields"])
            if not _portable_mode_body_is_valid(
                action_id, self.device_model_code(device_id), body_snapshot
            ):
                msg = "Invalid Portable mode command body or model"
                raise HomeAssistantError(msg)
            options["body_fields"] = body_snapshot
        if action_id in {
            ACTION_ID_PORTABLE_RESERVE_SWITCH,
            ACTION_ID_PORTABLE_RESERVE_THRESHOLD,
        }:
            body_snapshot = dict(options["body_fields"])
            if not _portable_reserve_body_is_valid(action_id, body_snapshot):
                msg = "Invalid Portable reserve command body"
                raise HomeAssistantError(msg)
            options["body_fields"] = body_snapshot
        self._require_command_family(device_id, action_id, cmd_value)
        ensure_mqtt = options.get("ensure_mqtt", True)
        allow_ble = options.get("allow_ble", True)
        cloud_attempt = self._new_cloud_command_attempt(
            device_id,
            action_id,
            cmd_value,
            register_cloud=options.get("allow_cloud", True),
        )
        if bound_model is not None:
            cloud_attempt.expected_reserve_model = bound_model
        cloud_attempt.expected_ups_identity = bound_identity
        cloud_attempt.expected_box_charge_identity = bound_box_charge_identity
        if identity_guard := options.get("identity_guard"):
            cloud_attempt.identity_guard = identity_guard
        operations = self._command_transport_operations(
            device_id,
            cmd_value,
            options,
            cloud_attempt,
        )
        task_operations: list[tuple[str, asyncio.Task[bool]]] = [
            (
                label,
                self._create_command_task(label, operation, cloud_attempt),
            )
            for label, operation in operations
        ]
        try:
            (
                succeeded,
                failures,
                first_error,
            ) = await self._async_wait_for_command_transport_result(
                cloud_attempt,
                task_operations,
                wait_for_all_transports=options.get("wait_for_all_transports", False),
            )
        except asyncio.CancelledError:
            await self._async_cancel_command_tasks(task_operations)
            raise

        if succeeded:
            return

        self._record_independent_cloud_mqtt_result(
            cloud_attempt,
            succeeded,
            failures,
        )

        # While the HTTP login session has not been cached yet, Layer 5 has no
        # credentials to work with and every transport necessarily fails. That
        # is a bounded startup warm-up covered by the retry above, not an
        # operational error — do not report it as one.
        credentials_pending = isinstance(
            first_error,
            JackeryMqttCredentialsPendingError,
        )
        if (
            action_id == ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG
            and cmd_value == MQTT_CMD_THIRD_PARTY_MQTT_CONFIG
            and not allow_ble
            and isinstance(first_error, HomeAssistantError)
            and isinstance(first_error.__cause__, MqttPublishOutcomeUnknownError)
        ):
            _LOGGER.warning(
                "Jackery MQTT config write outcome is unconfirmed for %s "
                "actionId=%s cmd=%s: %s",
                device_id,
                action_id,
                cmd_value,
                "; ".join(failures),
            )
        else:
            # A disconnected BLE-only background read is expected during GATT
            # handover. Preserve the failed result for its caller without
            # reporting a user command error or involving Cloud MQTT.
            background_ble_unavailable = (
                not ensure_mqtt
                and options.get("allow_ble", True)
                and not options.get("allow_cloud", True)
                and not any((options.get("http_read") or {}).values())
                and cmd_value in _BLE_DATA_RESPONSE_MSG_TYPES
                and failures == ["BLE=unavailable"]
            )
            _LOGGER.log(
                logging.DEBUG
                if (
                    credentials_pending
                    or (not ensure_mqtt and not allow_ble)
                    or background_ble_unavailable
                )
                else logging.ERROR,
                "Jackery command failed on every supported app transport for %s "
                "actionId=%s cmd=%s: %s",
                device_id,
                action_id,
                cmd_value,
                "; ".join(failures) or "no transport available",
            )
        if first_error is not None:
            raise first_error
        msg = "No supported Jackery app command transport is available"
        raise HomeAssistantError(msg)

    async def _async_get_mqtt_credentials(
        self,
        ensure_mqtt: bool,
    ) -> dict[str, str]:
        """Return cached MQTT credentials or raise a clear HomeAssistantError.

        Credentials are checked before starting the MQTT client so a missing
        cached login session fails fast instead of leaving a half-initialized
        client behind (``_async_ensure_mqtt`` returns early from connect when
        no cached session exists).
        """
        creds = self.api.get_cached_mqtt_credentials()
        if creds is not None:
            return creds
        del ensure_mqtt
        msg = (
            "Jackery MQTT credentials are not available yet; the HTTP login "
            "session has not been cached"
        )
        raise JackeryMqttCredentialsPendingError(msg)

    def _require_mqtt_owner(self, owner: JackeryMqttPushClient | None) -> None:
        """Reject a command whose client was replaced while it awaited connection."""
        if self._mqtt is owner:
            return
        message = "MQTT session ownership changed while waiting for connect"
        raise RuntimeError(message)

    async def _async_ensure_mqtt_connected(
        self,
        ensure_mqtt: bool,
        *,
        cloud_attempt: _CloudCommandAttempt | None = None,
    ) -> None:
        """Ensure a connected MQTT client exists before publishing.

        Layered after the credential check so the "client not initialized"
        path can only be reached with real credentials in hand. The client
        must be *connected*, not merely instantiated.
        """
        if ensure_mqtt and self._mqtt is None:
            # A foreground entity command may run before the deferred Layer-5
            # startup completed (or after supplemental transport cleanup).
            # Build the cloud client on demand; _async_ensure_mqtt cannot do so
            # itself because it deliberately owns connection state only.
            await self.async_start_mqtt()
        mqtt = self._mqtt
        initially_connected = mqtt.is_connected if mqtt is not None else False
        if ensure_mqtt and mqtt is not None and not initially_connected:
            # A server-side reset leaves the runtime allocated but disconnected.
            # Foreground commands must be able to restart that same independent
            # Cloud-MQTT layer instead of failing before the publish retry path.
            generation = mqtt.session_generation
            try:
                await self._async_ensure_mqtt(
                    force=True, wait_connected=True, cloud_attempt=cloud_attempt
                )
            except RuntimeError as err:
                if (
                    self._mqtt is mqtt
                    and (mqtt.is_started or mqtt.is_connected)
                    and mqtt.session_generation != generation
                ):
                    raise _MqttSessionChangedBeforePublishError(str(err)) from err
                raise
            self._require_mqtt_owner(mqtt)
        if self._mqtt is None or not self._mqtt.is_connected:
            msg = "MQTT client is not connected"
            raise HomeAssistantError(msg)

    def _require_cloud_read_retry_identity(
        self,
        device_id: str,
        mqtt: JackeryMqttPushClient,
        payload: dict[str, Any],
        attempt: _CloudCommandAttempt,
    ) -> None:
        """Validate the retry's session and physical target before publish."""
        if (
            attempt.expected_mqtt_fingerprint is not None
            and self.api.mqtt_fingerprint != attempt.expected_mqtt_fingerprint
        ):
            msg = "MQTT credentials changed during command retry"
            raise HomeAssistantError(msg)
        if attempt.expected_client_generation is not None:
            credentials = self.api.get_cached_mqtt_credentials()
            if credentials is None or not mqtt.session_matches_credentials(
                **credentials, generation=attempt.expected_client_generation
            ):
                msg = "MQTT session changed during command retry"
                raise HomeAssistantError(msg)
        if attempt.expected_read_device_sn is not None and (
            self._resolve_device_sn(device_id) != attempt.expected_read_device_sn
            or payload.get(FIELD_DEVICE_SN) != attempt.expected_read_device_sn
        ):
            msg = "MQTT read target changed before publish"
            raise HomeAssistantError(msg)

    async def _async_publish_with_retry(  # ruff: ignore[too-many-arguments, too-many-branches, too-many-statements]
        self,
        device_id: str,
        topic: str,
        payload: dict[str, Any],
        *,
        command_cmd: int,
        cloud_attempt: _CloudCommandAttempt | None = None,
        expected_family: str | None = None,
    ) -> None:
        """Publish an MQTT JSON command with a single stop-and-retry cycle."""
        message_type, action_id, request_id = (
            str(payload.get(FIELD_MESSAGE_TYPE) or ""),
            safe_int(payload.get(FIELD_ACTION_ID)),
            payload.get(FIELD_ID),
        )
        last_err: Exception | None = None
        for attempt in range(2):
            generation: int | None = None
            try:  # ruff: ignore[too-many-statements-in-try-clause]  # Connect, bind, and publish form one retry attempt.
                mqtt = self._mqtt
                generation = mqtt.session_generation if mqtt is not None else None
                if mqtt is not None and not mqtt.is_connected:
                    await self._async_ensure_mqtt(
                        force=not (self._mqtt is not None and self._mqtt.is_connected),
                        wait_connected=True,
                        cloud_attempt=cloud_attempt,
                    )
                    self._require_mqtt_owner(mqtt)
                if mqtt is None or not mqtt.is_connected:
                    msg = "MQTT client is not connected"
                    raise RuntimeError(msg)  # ruff: ignore[raise-within-try]  # Retry handling needs this attempt-local failure.
                if cloud_attempt is not None:
                    self._bind_cloud_command_attempt(cloud_attempt)
                    self._require_cloud_read_retry_identity(
                        device_id, mqtt, payload, cloud_attempt
                    )
                generation = mqtt.session_generation
                if expected_family is not None and (
                    self.device_family(device_id) != expected_family
                    or action_id is None
                    or self._require_command_family(device_id, action_id, command_cmd)
                    != expected_family
                ):
                    msg = f"Device model changed before MQTT publish for {device_id}"
                    raise HomeAssistantError(msg)
                if (
                    cloud_attempt is not None
                    and cloud_attempt.expected_reserve_model is not None
                    and self.device_model_code(device_id)
                    != cloud_attempt.expected_reserve_model
                ):
                    msg = f"Reserve model changed before MQTT publish for {device_id}"
                    raise HomeAssistantError(msg)
                if (
                    cloud_attempt is not None
                    and cloud_attempt.expected_ups_identity is not None
                    and (
                        self.device_ups_identity(device_id)
                        != cloud_attempt.expected_ups_identity
                        or payload.get(FIELD_DEVICE_SN)
                        != cloud_attempt.expected_ups_identity[1]
                    )
                ):
                    msg = "Portable UPS device identity changed before MQTT publish"
                    raise HomeAssistantError(msg)
                if (
                    cloud_attempt is not None
                    and cloud_attempt.expected_box_charge_identity is not None
                ):
                    box_identity = cloud_attempt.expected_box_charge_identity
                    body = payload.get(FIELD_BODY)
                    if (
                        self._box_safety_device_identity(device_id) != box_identity
                        or payload.get(FIELD_DEVICE_SN) != box_identity[1]
                        or not isinstance(body, dict)
                        or not _box_app_write_is_valid(_BOX_CHARGE_NOW_PAIR, body)
                        or not self.box_charge_now_allowed(
                            device_id, body.get("rc") == 1
                        )
                    ):
                        msg = (
                            "Box charge-now safety evidence changed before MQTT publish"
                        )
                        raise HomeAssistantError(msg)
                identity_guard = getattr(cloud_attempt, "identity_guard", None)
                if identity_guard is None:
                    await mqtt.async_publish_json(topic, payload, qos=0, retain=False)
                else:

                    def require_current_identity() -> None:
                        if identity_guard() is not True:  # ruff: ignore[function-uses-loop-variable]
                            msg = "MQTT command device identity changed before publish"
                            raise HomeAssistantError(msg)

                    await mqtt.async_publish_json(
                        topic,
                        payload,
                        qos=0,
                        retain=False,
                        before_publish=require_current_identity,
                    )
                _LOGGER.debug(
                    "Jackery MQTT TX %s: messageType=%s actionId=%s cmd=%s",
                    device_id,
                    message_type,
                    action_id,
                    command_cmd,
                )
                break
            except MqttPublishOutcomeUnknownError as err:
                mqtt = self._mqtt
                mqtt_last_error = mqtt.diagnostics.get("last_error") if mqtt else None
                error_detail = redacted_error(err, entry=getattr(self, "entry", None))
                # Preserve the operation identity even when the SDK cannot
                # confirm delivery. Do not retry an uncertain setter.
                self._schedule_payload_debug_event(
                    lambda: {
                        "kind": "mqtt_tx_outcome_unknown",
                        "device_id": device_id,
                        "messageType": message_type,
                        "actionId": action_id,
                        "cmd": command_cmd,
                        "id": request_id,
                        "error": error_detail,  # ruff: ignore[function-uses-loop-variable]
                    }
                )
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="mqtt_command_failed",
                    translation_placeholders={
                        "error": str(err),
                        "mqtt_last_error": (
                            str(mqtt_last_error) if mqtt_last_error else "n/a"
                        ),
                    },
                ) from err
            except RuntimeError as err:
                if self._mqtt is not mqtt or (
                    mqtt is not None and mqtt.session_generation != generation
                ):
                    raise
                last_err = err
                if attempt == 0:
                    if mqtt is not None:
                        await mqtt.async_stop()
                    continue
        else:
            mqtt = self._mqtt
            mqtt_last_error = mqtt.diagnostics.get("last_error") if mqtt else None
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mqtt_command_failed",
                translation_placeholders={
                    "error": str(last_err) if last_err else "unknown",
                    "mqtt_last_error": (
                        str(mqtt_last_error) if mqtt_last_error else "n/a"
                    ),
                },
            ) from last_err

        # TX record in the payload-debug JSONL so outbound commands can be
        # correlated with device ACK/property frames; the JSONL otherwise
        # only carries RX events (B-button finding 2026-07-03).
        self._schedule_payload_debug_event(
            lambda: {
                "kind": "mqtt_tx",
                "device_id": device_id,
                "payload": {
                    **({FIELD_ID: request_id} if request_id is not None else {}),
                    FIELD_DEVICE_SN: str(payload.get(FIELD_DEVICE_SN)),
                    "messageType": message_type,
                    "actionId": action_id,
                    "cmd": command_cmd,
                },
            },
        )

    async def _async_publish_command(  # ruff: ignore[too-many-locals]
        self,
        device_id: str,
        **options: Unpack[_PublishCommandOptions],
    ) -> None:
        """Publish an app-compatible command through the Cloud-MQTT path."""
        message_type = options["message_type"]
        action_id = options["action_id"]
        cmd = options["cmd"]
        body_fields = options["body_fields"]
        ensure_mqtt = options.get("ensure_mqtt", True)
        cloud_attempt = options.get("cloud_attempt")
        expected_family = self._require_command_family(device_id, action_id, cmd)
        creds = await self._async_get_mqtt_credentials(ensure_mqtt)
        device_sn = self._resolve_device_sn(device_id)
        if not device_sn:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mqtt_missing_device_sn",
                translation_placeholders={"device_id": str(device_id)},
            )
        if (
            cloud_attempt is not None
            and cloud_attempt.expected_ups_identity is not None
            and device_sn != cloud_attempt.expected_ups_identity[1]
        ):
            msg = "Portable UPS MQTT target differs from the bound device identity"
            raise HomeAssistantError(msg)
        if (
            cloud_attempt is not None
            and cloud_attempt.expected_box_charge_identity is not None
            and device_sn != cloud_attempt.expected_box_charge_identity[1]
        ):
            msg = "Box charge-now MQTT target differs from the bound device identity"
            raise HomeAssistantError(msg)

        await self._async_ensure_mqtt_connected(
            ensure_mqtt, cloud_attempt=cloud_attempt
        )

        user_id = creds[MQTT_CREDENTIAL_USER_ID]
        topic = f"{MQTT_TOPIC_PREFIX}/{user_id}/{MQTT_TOPIC_COMMAND}"
        # App uses the same real millisecond timestamp for id and timestamp.
        # Keep recent IDs across clock corrections and wait for the next real
        # millisecond if concurrent requests land in the same one.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 1.0
        recent_ids = getattr(self, "_recent_cloud_request_ids", None)
        if recent_ids is None:
            self._recent_cloud_request_ids: dict[int, float] = {}
            recent_ids = self._recent_cloud_request_ids
        while True:
            now = loop.time()
            for old_id, expiry in tuple(recent_ids.items()):
                if expiry <= now:
                    recent_ids.pop(old_id, None)
            timestamp_ms = int(time.time() * 1000)
            if timestamp_ms not in recent_ids and len(recent_ids) < 4096:  # ruff: ignore[magic-value-comparison]
                request_id = timestamp_ms
                recent_ids[request_id] = now + 60.0
                break
            if now >= deadline:
                raise HomeAssistantError("Unable to allocate a unique MQTT timestamp")  # ruff: ignore[raise-vanilla-args]
            await asyncio.sleep(0.001)
        body = (
            _portable_mqtt_body(action_id, cmd, body_fields)
            if expected_family == "portable"
            else self._command_body_for_transport(body_fields, cmd=cmd)
        )

        payload = {
            FIELD_DEVICE_SN: device_sn,
            "id": request_id,
            FIELD_VERSION: 0,
            FIELD_MESSAGE_TYPE: message_type,
            FIELD_ACTION_ID: action_id,
            FIELD_TIMESTAMP: timestamp_ms,
            # App 2.4.0 HomeControlFormat.h() serializes the body map and its
            # MQTT branch j() inserts that JSON directly into the envelope.
            # The bb/c Layer-C crypto call exists only in BLE branch f().
            FIELD_BODY: body,
        }

        await self._async_publish_with_retry(
            device_id,
            topic,
            payload,
            command_cmd=cmd,
            cloud_attempt=cloud_attempt,
            expected_family=expected_family,
        )

    async def async_bind_smart_part(
        self,
        device_id: str,
        accessory_sn: str,
        *,
        smart: dict[str, Any] | None = None,
    ) -> None:
        """Bind a smart accessory to the device (actionId 3012, cmd 108)."""
        # HomeCmdAction.BindSmartBean requires identity, types and scan name.
        # Its synthetic constructor defaults only param, linkType and bindKey.
        if not isinstance(smart, dict):
            msg = "Binding requires smart metadata: devType, subType and scanName"
            raise JackeryError(msg)
        descriptor = {
            "deviceSn": accessory_sn,
            "param": 0,
            "linkType": "",
            "bindKey": 0,
            **smart,
        }
        allowed = {
            "deviceSn",
            "devType",
            "subType",
            "scanName",
            "param",
            "linkType",
            "bindKey",
        }
        valid_types = all(
            isinstance(descriptor.get(key), int)
            and not isinstance(descriptor[key], bool)
            for key in ("devType", "subType", "param", "bindKey")
        ) and all(
            isinstance(descriptor.get(key), str) for key in ("scanName", "linkType")
        )
        if (
            descriptor["deviceSn"] != accessory_sn
            or not accessory_sn.strip()
            or not valid_types
            or descriptor.keys() - allowed
        ):
            msg = "Invalid smart accessory descriptor or mismatched deviceSn"
            raise JackeryError(msg)
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_BIND_SMART_ACCESSORY,
            action_id=ACTION_ID_BIND_SMART_PART,
            cmd=MQTT_CMD_BIND_SMART_PART,
            body_fields={"smart": [descriptor]},
        )

    async def async_unbind_smart_part(self, device_id: str, accessory_sn: str) -> None:
        """Unbind a smart accessory from the device (actionId 3013, cmd 109)."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_REMOVE_SMART_ACCESSORY,
            action_id=ACTION_ID_UNBIND_SMART_PART,
            cmd=MQTT_CMD_UNBIND_SMART_PART,
            body_fields={FIELD_DEVICE_SN: accessory_sn},
        )

    async def async_set_system_name(self, system_id: str, new_name: str) -> None:
        """Rename a Jackery system through the primary HTTP API."""
        ok = await self.api.async_set_system_name(system_id, new_name)
        if not ok:
            msg = "server returned false"
            raise JackeryError(msg)
        self._apply_local_system_name_patch(system_id, new_name)
        await self.async_request_refresh()
        self._apply_local_system_name_patch(system_id, new_name)

    async def async_set_pv_name(self, device_id: str, index: int, name: str) -> None:
        """Rename a PV input on a specific device via HTTP and patch locally.

        The 0-based ``index`` selects the ``pv<n>`` properties block. The device
        is resolved from ``device_id`` (its own meta/discovery) rather than by
        scanning for the first Home that exposes the channel, so renames stay
        correct on multi-device accounts.
        """
        field = _PV_CHANNEL_FIELDS[index]
        device_sn = self._resolve_device_sn(device_id)
        if not device_sn:
            msg = "missing deviceSn"
            raise JackeryError(msg)
        await self.api.async_modify_pv_name(
            device_sn=device_sn,
            index=index,
            name=name,
        )
        self._apply_local_pv_name_patch(device_id, field, name)
        await self.async_request_refresh()
        self._apply_local_pv_name_patch(device_id, field, name)

    def _apply_local_pv_name_patch(
        self,
        device_id: str,
        field: str,
        name: str,
    ) -> None:
        """Mirror a PV-input rename into the device's live properties.

        The echo key is unconfirmed in source-of-truth, so the value is stored
        optimistically under ``FIELD_PV_NAME`` (live-verify assumption).
        """
        if not self.data or device_id not in self.data:
            return
        entry = dict(self.data[device_id])
        props = dict(entry.get(PAYLOAD_PROPERTIES) or {})
        channel = props.get(field)
        if not isinstance(channel, dict):
            return
        next_channel = dict(channel)
        next_channel[FIELD_PV_NAME] = name
        props[field] = next_channel
        entry[PAYLOAD_PROPERTIES] = props
        new_data = dict(self.data)
        new_data[device_id] = entry
        self._push_partial_update(new_data)

    async def async_refresh_subdevices(
        self,
        *,
        context_device_id: str | None = None,
    ) -> None:
        """Refresh cloud accessory discovery and then refresh coordinator data."""
        if context_device_id is not None:
            self._require_home_config_context(context_device_id, "refresh subdevices")
        if failures := await self._async_refresh_http_accessories():
            raise failures[0]
        await self.async_request_refresh()

    async def async_set_device_nickname(
        self,
        device_id: str,
        nickname: str,
    ) -> None:
        """Set a Jackery device nickname through the primary HTTP API and refresh."""
        await self.api.async_set_device_nickname(device_id, nickname)
        self._apply_local_device_name_patch(device_id, nickname)
        await self.async_request_refresh()
        self._apply_local_device_name_patch(device_id, nickname)

    async def async_set_device_name(
        self,
        device_id: str,
        device_name: str,
    ) -> None:
        """Rename one D-I-Y system device through the explicit App REST endpoint."""
        await self.api.async_modify_device_name(
            device_name=device_name,
            id=device_id,
        )
        self._apply_local_device_name_patch(device_id, device_name)
        await self.async_request_refresh()
        self._apply_local_device_name_patch(device_id, device_name)

    async def async_unbind_device(self, device_id: str) -> None:
        """Unbind a Jackery device through the primary HTTP API and refresh."""
        await self.api.async_unbind_device(device_id)
        await self.async_request_refresh()

    async def async_unbind_accessories(
        self,
        bind_ids: Sequence[str],
    ) -> dict[str, Any]:
        """Unbind smart accessories by bind id and refresh the device list.

        Calls ``/v1/device/accessories/unbind`` (POST, app 2.4.0).
        """
        result = await self.api.async_unbind_accessories(bind_ids=bind_ids)
        await self.async_request_refresh()
        return result

    async def async_set_ac_nickname(
        self,
        device_id: str,
        *,
        ac_port: int,
        name: str,
    ) -> None:
        """Rename one AC output port of a portable device and refresh.

        Calls ``/v1/device/property/updateAcNickName`` (POST, app 2.4.0).
        """
        device_sn = self._resolve_device_sn(device_id)
        if not device_sn:
            msg = "missing deviceSn"
            raise JackeryError(msg)
        await self.api.async_set_ac_nickname(
            device_sn=device_sn,
            ac_port=ac_port,
            name=name,
        )
        await self.async_request_refresh()

    async def async_report_device_timezone(
        self,
        device_id: str,
        *,
        zone_id: str,
        time_offset: int,
    ) -> dict[str, Any]:
        """Report a device's timezone to the Jackery cloud (app parity).

        Calls ``/v1/device/timezone`` (POST, app 2.4.0). Pure report — no
        payload refresh is required.
        """
        return await self.api.async_report_device_timezone(
            device_id=device_id,
            zone_id=zone_id,
            time_offset=time_offset,
        )

    async def async_bind_device(
        self,
        *,
        bind_key: int,
        dev_id: str,
        guid: str,
        timezone_offset: int,
    ) -> None:
        """Bind a Jackery device through the primary HTTP API and refresh."""
        await self.api.async_bind_device(
            bind_key=bind_key,
            dev_id=dev_id,
            guid=guid,
            timezone_offset=timezone_offset,
        )
        await self.async_request_refresh()

    async def async_get_share_qr_code(self) -> dict[str, Any]:
        """Return the account share QR-code envelope from the primary HTTP API."""
        return await self.api.async_get_qr_code()

    async def async_list_shared_devices(self) -> list[Any]:
        """Return devices shared with this Jackery account."""
        return await self.api.async_get_device_shared_list()

    async def async_list_shared_managers(
        self,
        *,
        bind_user_id: str,
        level: int,
    ) -> list[Any]:
        """Return managers for a Jackery shared-device binding."""
        return await self.api.async_get_device_shared_managers(
            bind_user_id=bind_user_id,
            level=level,
        )

    async def async_remove_shared_access(
        self,
        *,
        bind_user_id: str,
        device_id: str,
    ) -> None:
        """Remove one shared-access binding through the primary HTTP API."""
        await self.api.async_remove_shared_access(
            bind_user_id=bind_user_id,
            device_id=device_id,
        )
        await self.async_request_refresh()

    async def async_remove_all_shared_access(
        self,
        *,
        bind_user_id: str,
        level: int,
    ) -> None:
        """Remove all shared-access bindings for a user and level."""
        await self.api.async_remove_all_shared_access(
            bind_user_id=bind_user_id,
            level=level,
        )
        await self.async_request_refresh()

    async def async_accept_shared_device(
        self,
        *,
        dev_id: str,
        qr_code_id: str,
    ) -> None:
        """Accept a shared Jackery device through the primary HTTP API."""
        await self.api.async_accept_shared_device(
            dev_id=dev_id,
            qr_code_id=qr_code_id,
        )
        await self.async_request_refresh()

    async def async_get_shelly_auth_url(self) -> dict[str, Any]:
        """Return the Jackery-owned Shelly OAuth authorization payload."""
        return await self.api.async_get_shelly_auth_url()

    async def async_get_shelly_devices(self) -> list[dict[str, Any]]:
        """Return Shelly Cloud devices linked to this Jackery account."""
        return await self.api.async_get_shelly_devices()

    async def async_get_shelly_binding_failures(
        self,
        state: str = "",
    ) -> dict[str, Any]:
        """Return Shelly binding failures from the primary HTTP API."""
        return await self.api.async_get_shelly_binding_failures(state=state)

    async def async_unbind_shelly_device(
        self,
        *,
        binding_id: str,
        shelly_device_id: str,
    ) -> bool:
        """Unbind one Shelly device through the primary HTTP API."""
        accepted = await self.api.async_unbind_shelly_device(
            binding_id=binding_id,
            device_id=shelly_device_id,
        )
        if accepted:
            await self.async_request_refresh()
        return accepted

    async def async_unbind_shelly_account(self) -> bool:
        """Unbind the Shelly account through the primary HTTP API."""
        accepted = await self.api.async_unbind_shelly_account()
        if accepted:
            await self.async_request_refresh()
        return accepted

    async def async_set_eps(self, device_id: str, enabled: bool) -> None:
        """Set eps."""
        val = 1 if enabled else 0
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_EPS_ENABLED,
            cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
            body_fields={FIELD_SW_EPS: val},
        )
        self._apply_local_property_patch(device_id, {FIELD_SW_EPS: val})

    async def async_set_soc_limits(
        self,
        device_id: str,
        *,
        charge_limit: int | None = None,
        discharge_limit: int | None = None,
    ) -> None:
        """Set SOC limits.

        Verified against ``HomeCmdAction.smali``: the official app sends both
        limits in a single ``SET_CHARGE_DISCHARGE_LINE`` (actionId 3028)
        frame. Missing sides are filled from the last-known coordinator
        state so the frame always carries the full pair the device expects.
        """
        if charge_limit is None and discharge_limit is None:
            msg = "Cannot set SOC limits without charge_limit or discharge_limit"
            raise UpdateFailed(
                msg,
            )
        current = ((self.data or {}).get(device_id, {}) or {}).get(
            PAYLOAD_PROPERTIES,
            {},
        ) or {}

        def _soc_limit(value: object) -> int | None:
            parsed = safe_int(value)
            if parsed is None or parsed < 0 or parsed > _SOC_PERCENT_MAX:
                return None
            return parsed

        def _current_soc_limit(primary: str, legacy: str, default: int) -> int:
            for raw in (current.get(primary), current.get(legacy)):
                parsed = _soc_limit(raw)
                if parsed is not None:
                    return parsed
            return default

        chg = (
            _soc_limit(charge_limit)
            if charge_limit is not None
            else _current_soc_limit(FIELD_SOC_CHG_LIMIT, FIELD_SOC_CHARGE_LIMIT, 100)
        )
        dis = (
            _soc_limit(discharge_limit)
            if discharge_limit is not None
            else _current_soc_limit(
                FIELD_SOC_DISCHG_LIMIT,
                FIELD_SOC_DISCHARGE_LIMIT,
                0,
            )
        )
        if chg is None or dis is None:
            msg = "Invalid SOC limit"
            raise UpdateFailed(msg)
        body_fields = {
            FIELD_SOC_DISCHG_LIMIT: dis,
            FIELD_SOC_CHG_LIMIT: chg,
        }
        force_charge = safe_int(current.get(FIELD_SOC_FORCE_CHG))
        if force_charge in {0, 1}:
            # App ``ChargeDischargeLimitActivity`` always forwards its current
            # force-charge checkbox in the same 3028 frame. Preserve that
            # independently observed value when either SOC limit changes.
            body_fields[FIELD_SOC_FORCE_CHG] = force_charge
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_SOC_LIMITS,
            cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
            body_fields=body_fields,
        )
        self._apply_local_property_patch(
            device_id,
            {
                FIELD_SOC_CHARGE_LIMIT: chg,
                FIELD_SOC_CHG_LIMIT: chg,
                FIELD_SOC_DISCHARGE_LIMIT: dis,
                FIELD_SOC_DISCHG_LIMIT: dis,
            },
        )

    async def async_set_max_feed_grid(self, device_id: str, watts: int) -> None:
        """Set max feed grid."""
        value = int(watts)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_MAX_FEED_GRID,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_MAX_FEED_GRID: value},
        )
        self._apply_local_property_patch(
            device_id,
            {FIELD_MAX_FEED_GRID: value},
        )

    async def async_set_max_output_power(self, device_id: str, watts: int) -> None:
        """Set max output power.

        3038 routes via DevicePropertyChange (cmd 107), not ControlCombine —
        verified against official app via Frida capture 2026-05-14.
        """
        value = int(watts)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_MAX_OUT_PW,
            cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
            body_fields={FIELD_MAX_OUT_PW: value},
        )
        self._apply_local_property_patch(device_id, {FIELD_MAX_OUT_PW: value})

    async def async_set_auto_standby_hours(
        self,
        device_id: str,
        hours: int,
    ) -> None:
        # App-side setter uses a boolean payload key "isAutoStandby"
        # (0/1), not an hour value.
        """Set auto standby hours."""
        val = 1 if int(hours) > 0 else 0
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_AUTO_STANDBY,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_IS_AUTO_STANDBY: val},
        )
        self._apply_local_property_patch(
            device_id,
            {FIELD_IS_AUTO_STANDBY: val},
        )

    async def async_set_auto_standby(self, device_id: str, enabled: bool) -> None:
        """Backward-compatible bool setter (legacy switch entity)."""
        val = 1 if enabled else 0
        await self.async_set_auto_standby_hours(device_id, val)

    async def async_set_standby(self, device_id: str, enabled: bool) -> None:
        """Put the unit into standby/sleep or power it back on.

        App mapping: HomeDeviceController.a.SLEEP=1, POWER_ON=2.
        """
        value = 1 if enabled else 2
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_STANDBY,
            cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
            body_fields={FIELD_AUTO_STANDBY: value},
        )
        self._apply_local_property_patch(device_id, {FIELD_AUTO_STANDBY: value})

    async def async_set_work_model(self, device_id: str, mode: int) -> None:
        """Set work model."""
        value = int(mode)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_WORK_MODEL,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_WORK_MODEL: value},
        )
        self._apply_local_property_patch(device_id, {FIELD_WORK_MODEL: value})

    async def async_set_off_grid_shutdown(self, device_id: str, enabled: bool) -> None:
        """Set off grid shutdown."""
        val = 1 if enabled else 0
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_OFF_GRID_DOWN,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_OFF_GRID_DOWN: val},
        )
        self._apply_local_property_patch(device_id, {FIELD_OFF_GRID_DOWN: val})

    async def async_set_off_grid_time(self, device_id: str, minutes: int) -> None:
        """Set off grid time."""
        value = int(minutes)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_OFF_GRID_TIME,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_OFF_GRID_TIME: value},
        )
        self._apply_local_property_patch(device_id, {FIELD_OFF_GRID_TIME: value})

    async def async_set_default_power(self, device_id: str, watts: int) -> None:
        """Set default power."""
        value = int(watts)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_DEFAULT_PW,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_DEFAULT_PW: value},
        )
        self._apply_local_property_patch(device_id, {FIELD_DEFAULT_PW: value})

    async def async_set_follow_meter(self, device_id: str, enabled: bool) -> None:
        """Set follow meter."""
        val = 1 if enabled else 0
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_FOLLOW_METER_PW,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_IS_FOLLOW_METER_PW: val},
        )
        self._apply_local_property_patch(device_id, {FIELD_IS_FOLLOW_METER_PW: val})

    async def async_set_storm_warning(self, device_id: str, enabled: bool) -> None:
        """Set storm warning."""
        val = 1 if enabled else 0
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_STORM_WARNING,
            cmd=MQTT_CMD_NONE,
            body_fields={FIELD_WPS: val},
        )
        self._apply_local_property_patch(device_id, {FIELD_WPS: val})
        self._apply_local_weather_plan_patch(device_id, {FIELD_WPS: val})

    async def async_set_storm_minutes(self, device_id: str, minutes: int) -> None:
        """Set storm minutes."""
        value = int(minutes)
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_SEND_WEATHER_ALERT,
            action_id=(
                20
                if self.device_ble_profile(device_id) == "box_fixed"
                else ACTION_ID_STORM_MINUTES
            ),
            cmd=MQTT_CMD_NONE,
            body_fields={FIELD_MINS_INTERVAL: value},
        )
        # Some payloads expose this value as wpc in system/config snapshots.
        self._apply_local_property_patch(
            device_id,
            {FIELD_WPC: value, FIELD_MINS_INTERVAL: value},
        )
        self._apply_local_weather_plan_patch(
            device_id,
            {FIELD_WPC: value, FIELD_MINS_INTERVAL: value},
        )

    async def async_delete_storm_alert(self, device_id: str, alert_id: str) -> None:
        """Delete storm alert."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
            action_id=(
                21
                if self.device_ble_profile(device_id) == "box_fixed"
                else ACTION_ID_DELETE_STORM_ALERT
            ),
            cmd=MQTT_CMD_NONE,
            body_fields={FIELD_ALERT_ID: alert_id},
        )

    async def async_update_storm_alert_location(
        self,
        device_id: str,
        latitude: float,
        longitude: float,
    ) -> None:
        """Update storm-alert GPS coordinates via HTTP and patch locally.

        Unlike the MQTT-published storm-alert delete, this is a direct HTTP PUT
        to ``device/location``; it mirrors the system-rename write/patch/refresh
        shape so dependent entities reflect the new location immediately.
        """
        await self.api.async_update_location(
            device_id=device_id,
            latitude=latitude,
            longitude=longitude,
        )
        self._apply_local_location_patch(device_id, latitude, longitude)
        await self.async_request_refresh()
        self._apply_local_location_patch(device_id, latitude, longitude)

    def _apply_local_location_patch(
        self,
        device_id: str,
        latitude: float,
        longitude: float,
    ) -> None:
        """Mirror a location update into the device's cached location block."""
        if not self.data or device_id not in self.data:
            return
        payload = dict(self.data[device_id])
        location = dict(payload.get(PAYLOAD_LOCATION) or {})
        location[FIELD_LATITUDE] = latitude
        location[FIELD_LONGITUDE] = longitude
        payload[PAYLOAD_LOCATION] = location
        new_data = dict(self.data)
        new_data[device_id] = payload
        self._push_partial_update(new_data)

    async def async_set_temp_unit(self, device_id: str, unit: int) -> None:
        """Set temp unit."""
        value = int(unit)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_TEMP_UNIT,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_TEMP_UNIT: value},
        )
        self._apply_local_property_patch(device_id, {FIELD_TEMP_UNIT: value})

    async def async_set_single_price(self, device_id: str, price_value: float) -> None:
        """Set single price."""
        self._require_home_config_context(device_id, "set single tariff")
        price = safe_float(price_value)
        if price is None or not math.isfinite(price) or price < 0:
            msg = f"Cannot set single tariff for {device_id}: invalid singlePrice"
            raise HomeAssistantError(msg)
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = f"Cannot set single tariff for {device_id}: missing systemId"
            raise UpdateFailed(
                msg,
            )
        current = ((self.data or {}).get(device_id, {}) or {}).get(PAYLOAD_PRICE) or {}
        currency = (
            current.get(FIELD_SINGLE_CURRENCY)
            or current.get(FIELD_CURRENCY)
            or current.get(FIELD_SINGLE_CURRENCY_CODE)
            or current.get(FIELD_CURRENCY_CODE)
            or "€"
        )
        try:
            success = await self.api.async_set_single_mode(
                system_id=system_id,
                single_price=price,
                currency=str(currency),
            )
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while saving the single tariff",
                err,
            )
        if not success:
            msg = (
                f"Cannot set single tariff for {device_id}: API rejected single tariff"
            )
            raise HomeAssistantError(msg)
        self._invalidate_system_cache(system_id, PAYLOAD_PRICE)
        self._apply_local_price_patch(
            device_id,
            {
                FIELD_DYNAMIC_OR_SINGLE: 2,
                FIELD_SINGLE_PRICE: round(price, 4),
            },
        )

    async def async_set_price_mode_single(self, device_id: str) -> None:
        """Set price mode single."""
        self._require_home_config_context(device_id, "set single tariff mode")
        current = ((self.data or {}).get(device_id, {}) or {}).get(PAYLOAD_PRICE) or {}
        single_price = current.get(FIELD_SINGLE_PRICE)
        if single_price is None:
            system_id = self._resolve_system_id(device_id)
            if not system_id:
                msg = (
                    f"Cannot switch to single tariff for {device_id}: missing systemId"
                )
                raise HomeAssistantError(
                    msg,
                )
            try:
                latest = await self.api.async_get_power_price(system_id)
            except JackeryAuthError as err:
                _raise_config_entry_auth_failed(
                    "Jackery credentials were rejected"
                    " while reading the current tariff",
                    err,
                )
            except JackeryError as err:
                msg = f"Cannot switch to single tariff for {device_id}: {err}"
                raise HomeAssistantError(
                    msg,
                ) from err
            if isinstance(latest, dict):
                single_price = latest.get(FIELD_SINGLE_PRICE)
        if single_price is None:
            msg = f"Cannot switch to single tariff for {device_id}: missing singlePrice"
            raise HomeAssistantError(
                msg,
            )
        price = safe_float(single_price)
        if price is None or not math.isfinite(price):
            msg = f"Cannot switch to single tariff for {device_id}: invalid singlePrice"
            raise HomeAssistantError(msg)
        await self.async_set_single_price(device_id, price)

    @staticmethod
    def _valid_price_sources(sources: object) -> list[dict[str, Any]]:
        return valid_price_sources(sources)

    async def _async_price_sources_for_device(
        self,
        device_id: str,
    ) -> list[dict[str, Any]]:
        self._require_home_config_context(device_id, "read dynamic price sources")
        payload = (self.data or {}).get(device_id, {}) or {}
        sources = self._valid_price_sources(payload.get(PAYLOAD_PRICE_SOURCES))
        if sources:
            return sources

        system_id = self._resolve_system_id(device_id)
        if not system_id:
            return []
        try:
            sources = self._valid_price_sources(
                await self.api.async_get_price_sources(system_id),
            )
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while reading price sources",
                err,
            )
        except JackeryError as err:
            _LOGGER.debug("price source fetch failed for %s: %s", device_id, err)
            return []

        if self.data and device_id in self.data:
            new_data = dict(self.data)
            entry = dict(new_data[device_id])
            entry[PAYLOAD_PRICE_SOURCES] = sources
            new_data[device_id] = entry
            self._push_partial_update(new_data)
        return sources

    def _device_country_code(self, device_id: str) -> str | None:
        payload = (self.data or {}).get(device_id, {}) or {}
        for section_name in (PAYLOAD_SYSTEM, PAYLOAD_DEVICE, PAYLOAD_DISCOVERY):
            section = payload.get(section_name) or {}
            if not isinstance(section, dict):
                continue
            raw = (
                section.get(FIELD_COUNTRY_CODE)
                or section.get(FIELD_COUNTRY)
                or section.get(FIELD_SYSTEM_REGION)
            )
            if raw not in {None, ""}:
                return str(raw).strip().upper()
        return None

    def _source_region_for_device(
        self,
        device_id: str,
        source: dict[str, Any],
    ) -> str | None:
        regions = normalized_source_regions(source)
        if not regions:
            return None
        country = self._device_country_code(device_id)
        if country:
            for region in regions:
                if region == country:
                    return region
        return regions[0]

    def _find_matching_price_source(
        self,
        device_id: str,
        sources: list[dict[str, Any]],
        current: dict[str, Any],
    ) -> dict[str, Any] | None:
        company_id = normalized_company_id(current.get(FIELD_PLATFORM_COMPANY_ID))
        if company_id is None:
            return None
        region = normalized_region(current.get(FIELD_SYSTEM_REGION))
        country = self._device_country_code(device_id)
        matches = [
            source
            for source in sources
            if normalized_company_id(source.get(FIELD_PLATFORM_COMPANY_ID))
            == company_id
        ]
        if not matches:
            return None
        if region is not None:
            for source in matches:
                if region in normalized_source_regions(source):
                    return source
        if country:
            for source in matches:
                if country in normalized_source_regions(source):
                    return source
        return matches[0] if len(matches) == 1 else None

    async def async_set_price_source(
        self,
        device_id: str,
        source: dict[str, Any],
    ) -> None:
        """Select a dynamic-price provider via the app's saveDynamicMode API."""
        self._require_home_config_context(device_id, "set dynamic tariff provider")
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = f"Cannot set dynamic tariff for {device_id}: missing systemId"
            raise HomeAssistantError(
                msg,
            )

        region = self._source_region_for_device(device_id, source)
        company_id_int = normalized_company_id(source.get(FIELD_PLATFORM_COMPANY_ID))
        if company_id_int is None or not region:
            msg = (
                "Cannot set dynamic tariff: selected provider is missing "
                "platformCompanyId/country."
            )
            raise HomeAssistantError(
                msg,
            )

        try:
            success = await self.api.async_set_dynamic_mode(
                system_id=system_id,
                platform_company_id=company_id_int,
                system_region=region,
            )
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while saving the dynamic tariff",
                err,
            )
        if not success:
            msg = (
                f"Cannot set dynamic tariff for {device_id}: "
                "API rejected dynamic tariff"
            )
            raise HomeAssistantError(msg)
        self._invalidate_system_cache(system_id, PAYLOAD_PRICE)
        self._apply_local_price_patch(
            device_id,
            {
                FIELD_DYNAMIC_OR_SINGLE: 1,
                FIELD_PLATFORM_COMPANY_ID: company_id_int,
                FIELD_SYSTEM_REGION: region,
                FIELD_COMPANY_NAME: first_nonblank_source_name(
                    source,
                    FIELD_COMPANY_NAME,
                    FIELD_NAME,
                ),
                FIELD_POWER_PRICE_RESOURCE: source.get(FIELD_CID),
                FIELD_LOGIN_ALLOWED: source.get(FIELD_LOGIN_ALLOWED),
            },
        )

    async def async_set_price_mode_dynamic(self, device_id: str) -> None:
        """Set price mode dynamic."""
        self._require_home_config_context(device_id, "set dynamic tariff mode")
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = f"Cannot set dynamic tariff for {device_id}: missing systemId"
            raise HomeAssistantError(
                msg,
            )
        current = ((self.data or {}).get(device_id, {}) or {}).get(PAYLOAD_PRICE) or {}
        company_id_int = normalized_company_id(current.get(FIELD_PLATFORM_COMPANY_ID))
        region = normalized_region(current.get(FIELD_SYSTEM_REGION))
        if company_id_int is None or region is None:
            sources = await self._async_price_sources_for_device(device_id)
            source = self._find_matching_price_source(device_id, sources, current)
            if source is not None:
                await self.async_set_price_source(device_id, source)
                return
            if len(sources) == 1:
                await self.async_set_price_source(device_id, sources[0])
                return
            msg = (
                "Dynamic tariff requires provider selection. Use the "
                "'Electricity price provider' select entity first."
            )
            raise HomeAssistantError(
                msg,
            )
        try:
            success = await self.api.async_set_dynamic_mode(
                system_id=system_id,
                platform_company_id=company_id_int,
                system_region=region,
            )
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while saving the dynamic tariff",
                err,
            )
        if not success:
            msg = (
                f"Cannot set dynamic tariff for {device_id}: "
                "API rejected dynamic tariff"
            )
            raise HomeAssistantError(msg)
        self._invalidate_system_cache(system_id, PAYLOAD_PRICE)
        self._apply_local_price_patch(
            device_id,
            {
                FIELD_DYNAMIC_OR_SINGLE: 1,
                FIELD_PLATFORM_COMPANY_ID: company_id_int,
                FIELD_SYSTEM_REGION: region,
            },
        )

    async def async_query_system_info(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
        allow_ble: bool = True,
        allow_cloud: bool = True,
        allow_local_mqtt: bool = True,
        include_http: bool = False,
    ) -> None:
        """Query system info."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_COMBINE_DATA,
            action_id=ACTION_ID_QUERY_COMBINE_DATA,
            cmd=MQTT_CMD_QUERY_COMBINE_DATA,
            body_fields={},
            ensure_mqtt=ensure_mqtt,
            allow_ble=allow_ble,
            allow_cloud=allow_cloud,
            allow_local_mqtt=allow_local_mqtt,
            http_read={"system_shadow": include_http},
        )

    @classmethod
    def _http_shadow_candidates(
        cls,
        entry: dict[str, Any],
        dev_type: int,
    ) -> list[tuple[str, int]]:
        """Return known accessory serials safe for one documented subShadow read."""
        candidates = [
            accessory
            for accessory in cls._entry_accessories(entry)
            if subdevice_dev_type(accessory) == dev_type
        ]
        bucket = cls._SHADOW_DEV_TYPE_BUCKETS.get(dev_type)
        bucket_value = entry.get(bucket) if bucket is not None else None
        if isinstance(bucket_value, dict):
            candidates.append(bucket_value)
        elif isinstance(bucket_value, list):
            candidates.extend(
                item
                for item in bucket_value
                if isinstance(item, dict)
                and (
                    dev_type != SUBDEVICE_DEV_TYPE_COMBO
                    or subdevice_dev_type(item) == dev_type
                )
            )

        unique: dict[str, tuple[str, int]] = {}
        for candidate in candidates:
            serial = subdevice_serial(candidate)
            if serial is not None:
                unique.setdefault(serial, (serial, dev_type))
        return list(unique.values())

    @classmethod
    def _documented_http_shadow_response_recognized(
        cls,
        response: dict[str, Any],
        *,
        kind: str,
        dev_type: int | None,
    ) -> bool:
        """Return whether a shadow response matches its documented payload family."""
        recognized = False
        if kind == "system_shadow":
            recognized = any(
                cls._property_value_present(response.get(key))
                for key in cls._SYSTEM_INFO_KEYS | cls._SUBDEVICE_MAIN_MIRROR_KEYS
            )
        elif dev_type == SUBDEVICE_DEV_TYPE_BATTERY_PACK:
            recognized = bool(cls._battery_packs_from_source(response))
        elif dev_type == SUBDEVICE_DEV_TYPE_CT:
            recognized = (
                cls._find_dict_with_any_key(response, cls._CT_METER_KEYS) is not None
            )
        elif dev_type == SUBDEVICE_DEV_TYPE_METER_HEAD:
            collectors = response.get(FIELD_COLLECTORS)
            recognized = isinstance(collectors, list) and bool(collectors)
        elif dev_type == SUBDEVICE_DEV_TYPE_SOCKET:
            plugs = response.get(FIELD_PLUGS)
            recognized = isinstance(plugs, list) and bool(plugs)
        elif dev_type == SUBDEVICE_DEV_TYPE_COMBO:
            sub_devices = response.get(FIELD_SUB_DEVICE)
            recognized = isinstance(sub_devices, list) and bool(sub_devices)
        return recognized

    def _documented_http_read_requests(
        self,
        device_id: str,
        entry: dict[str, Any],
        options: _DocumentedHttpReadOptions,
    ) -> list[_DocumentedHttpRequest]:
        """Build all documented HTTP read awaitables before gathering them."""
        parent_sn = self._shadow_parent_device_sn(entry)
        requests: list[_DocumentedHttpRequest] = []
        if options.get("device_property", False):
            requests.append((
                "device_property",
                None,
                None,
                lambda: self.api.async_get_device_property(device_id),
            ))
        if options.get("system_shadow", False) and parent_sn:
            system_id = self._shadow_system_id(entry)
            if system_id is not None:
                requests.append((
                    "system_shadow",
                    None,
                    system_id,
                    lambda: self.api.async_get_system_shadow(
                        device_sn=parent_sn,
                        diy_sn=system_id,
                    ),
                ))
        battery_packs = options.get("battery_packs", False)
        shadow_types = {
            dev_type
            for dev_type in (
                SUBDEVICE_DEV_TYPE_BATTERY_PACK if battery_packs else None,
                options.get("subdevice_dev_type"),
            )
            if dev_type is not None
        }
        if battery_packs and parent_sn:
            requests.append((
                "battery_pack_list",
                SUBDEVICE_DEV_TYPE_BATTERY_PACK,
                None,
                lambda: self._async_fetch_http_battery_packs(device_id, parent_sn),
            ))
        if parent_sn:
            for dev_type in shadow_types:
                for subdevice_sn, _ in self._http_shadow_candidates(entry, dev_type):
                    requests.append((
                        "sub_shadow",
                        dev_type,
                        subdevice_sn,
                        partial(
                            self.api.async_get_sub_shadow,
                            dev_type=str(dev_type),
                            device_sn=parent_sn,
                            sub_device_sn=subdevice_sn,
                        ),
                    ))
        return requests

    def _merge_documented_device_property_read(
        self,
        device_id: str,
        working: dict[str, Any],
        result: object,
    ) -> tuple[bool, bool]:
        """Merge one recognized HTTP device-property response."""
        if not isinstance(result, dict) or not result:
            return False, False
        raw_properties = result.get(PAYLOAD_PROPERTIES)
        device_meta = result.get(PAYLOAD_DEVICE)
        http_properties = (
            self._sanitize_main_properties(raw_properties)
            if isinstance(raw_properties, dict)
            else {}
        )
        recognized = bool(http_properties) or (
            isinstance(device_meta, dict) and bool(device_meta)
        )
        if not recognized:
            if isinstance(raw_properties, dict):
                self._record_layer_source_payload(
                    Observation(
                        source=TransportSource.HTTP,
                        device_id=device_id,
                        section=PAYLOAD_PROPERTIES,
                        payload=raw_properties,
                        family=self.device_family(device_id),
                    )
                )
            return False, False
        if isinstance(raw_properties, dict):
            working[PAYLOAD_HTTP_PROPERTIES] = http_properties
            working[PAYLOAD_PROPERTIES] = self._merge_main_properties_for_device(
                device_id,
                working.get(PAYLOAD_PROPERTIES) or {},
                raw_properties,
                source=TransportSource.HTTP,
            )
        if isinstance(device_meta, dict):
            working[PAYLOAD_DEVICE] = merge_present_dict_values(
                working.get(PAYLOAD_DEVICE) or {},
                device_meta,
            )
        self._last_http_device_refresh_monotonic[device_id] = time.monotonic()
        return True, True

    async def _async_fetch_http_battery_packs(
        self, device_id: str, device_sn: str
    ) -> list[dict[str, Any]]:
        """Capture the request-owned HTTP envelope before any pack selection."""
        epochs = {
            serial: epoch
            for (owner, serial), epoch in getattr(
                self, "_http_pack_owner_epochs", {}
            ).items()
            if owner == device_id
        }
        parent_binding = self._http_parent_binding(device_id)
        data_epoch = (
            getattr(self, "_http_data_identity_epochs", {}).get(device_id, 0)
            if isinstance((getattr(self, "data", None) or {}).get(device_id), dict)
            else None
        )
        result = await self.api.async_get_battery_pack_list(device_sn)
        if isinstance(getattr(result, "request_generation", None), int):
            vars(result)["http_pack_owner_epochs"] = epochs
            vars(result)["http_parent_binding"] = parent_binding
            vars(result)["http_data_epoch"] = data_epoch
        original = getattr(result, "original_payload", None)
        if isinstance(original, dict):
            identity = ""
            if (
                self._http_data_conflicts_with_parent(device_id, parent_binding)
                or (not self._http_parent_binding_is_current(device_id, parent_binding))
                or (
                    data_epoch is not None
                    and data_epoch
                    != getattr(self, "_http_data_identity_epochs", {}).get(device_id, 0)
                )
            ):
                request_generation = getattr(result, "request_generation", None)
                request_token = (
                    request_generation
                    if isinstance(request_generation, int)
                    else time.monotonic_ns()
                )
                identity = f"stale_parent_{parent_binding[1]}_request_{request_token}"
            self._record_layer_source_payload(
                Observation(
                    source=TransportSource.HTTP,
                    device_id=device_id,
                    section=_HTTP_BATTERY_PACK_RAW_SECTION,
                    payload=original,
                ),
                identity=identity,
                original_payload=original,
            )
        return result

    def _http_pack_latest_original(
        self, device_id: str, result: object
    ) -> Mapping[str, object] | None:
        """Keep the newest request's original as the selected HTTP receipt."""
        original = getattr(result, "original_payload", None)
        if not isinstance(original, dict):
            return None
        parent_binding = getattr(result, "http_parent_binding", None)
        if parent_binding is not None and not self._http_parent_binding_is_current(
            device_id, parent_binding
        ):
            return None
        data_epoch = getattr(result, "http_data_epoch", None)
        if data_epoch is not None and data_epoch != getattr(
            self, "_http_data_identity_epochs", {}
        ).get(device_id, 0):
            return None
        generation = getattr(result, "request_generation", None)
        if not isinstance(generation, int):
            return original
        generations = getattr(self, "_http_pack_original_generations", None)
        if generations is None:
            generations = self._http_pack_original_generations = {}
        if generation < generations.get(device_id, 0):
            return None
        generations[device_id] = generation
        return original

    def _record_http_battery_pack_response(
        self, device_id: str, result: object
    ) -> None:
        """Record the response bound to this HTTP request before projection."""
        original = self._http_pack_latest_original(device_id, result)
        if original is None:
            return
        self._record_layer_source_payload(
            Observation(
                source=TransportSource.HTTP,
                device_id=device_id,
                section=PAYLOAD_SUBDEVICES,
                payload=original,
            ),
            original_payload=original,
        )

    def _merge_documented_battery_pack_list_read(
        self,
        device_id: str,
        working: dict[str, Any],
        result: object,
    ) -> tuple[bool, bool]:
        """Merge one recognized HTTP battery-pack list."""
        if not isinstance(result, list):
            return False, False
        if not result:
            self._record_http_battery_pack_response(device_id, result)
            return False, False
        original = self._http_pack_latest_original(device_id, result)
        raw_original = getattr(result, "original_payload", None)
        generation = getattr(result, "request_generation", None)
        received_at = getattr(result, "received_at_monotonic", None)
        owner_epochs = getattr(result, "http_pack_owner_epochs", None)
        merged = self._merge_subdevice_data(
            working,
            {FIELD_BATTERY_PACKS: list(result)},
            device_id=device_id,
            source_transport=TransportSource.HTTP,
            original_payload=original if isinstance(original, dict) else None,
            http_pack_generation=generation if isinstance(generation, int) else None,
            http_pack_received_at=(
                received_at if isinstance(received_at, float) else None
            ),
            http_pack_original_payload=(
                raw_original if isinstance(raw_original, dict) else None
            ),
            http_pack_owner_epochs=(
                owner_epochs if isinstance(owner_epochs, dict) else None
            ),
        )
        return True, merged

    def _merge_documented_shadow_read(
        self,
        device_id: str,
        working: dict[str, Any],
        result: object,
        *,
        kind: str,
        dev_type: int | None,
    ) -> tuple[bool, bool]:
        """Merge one recognized system or subdevice shadow response."""
        if not isinstance(result, dict):
            return False, False
        original = getattr(result, "original_payload", None)
        if not result:
            if isinstance(original, dict):
                self._record_layer_source_payload(
                    Observation(
                        source=TransportSource.HTTP,
                        device_id=device_id,
                        section=PAYLOAD_SUBDEVICES,
                        payload=original,
                    ),
                    identity=f"http_{kind}_{dev_type}",
                    original_payload=original,
                )
            return False, False
        merged = self._merge_subdevice_data(
            working,
            result,
            device_id=device_id,
            source_transport=TransportSource.HTTP,
            original_payload=original if isinstance(original, dict) else None,
        )
        if kind == "system_shadow":
            merged = (
                self._merge_system_info_fields(device_id, working, result) or merged
            )
        recognized = merged or self._documented_http_shadow_response_recognized(
            result,
            kind=kind,
            dev_type=dev_type,
        )
        return recognized, merged

    def _merge_documented_http_read_result(
        self,
        device_id: str,
        working: dict[str, Any],
        result: object,
        *,
        kind: str,
        dev_type: int | None,
    ) -> tuple[bool, bool]:
        """Dispatch one documented response to its family-specific merger."""
        if kind == "device_property":
            return self._merge_documented_device_property_read(
                device_id,
                working,
                result,
            )
        if kind == "battery_pack_list":
            return self._merge_documented_battery_pack_list_read(
                device_id,
                working,
                result,
            )
        return self._merge_documented_shadow_read(
            device_id,
            working,
            result,
            kind=kind,
            dev_type=dev_type,
        )

    @_transactional_pack_candidates
    async def async_refresh_documented_http_read(  # ruff: ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]
        self,
        device_id: str,
        *,
        device_property: bool = False,
        system_shadow: bool = False,
        battery_packs: bool = False,
        subdevice_dev_type: int | None = None,
    ) -> bool:
        """Refresh one documented read surface over HTTP and merge it safely.

        This is the independent HTTP half of a manual refresh button. The button
        runs its BLE -> cloud-MQTT query concurrently. HTTP responses use the same
        source-aware merge sinks as normal polling, so fresh local/BLE/cloud
        fields retain precedence while HTTP-only or missing fields are filled.

        Returns:
            True when at least one response matched its documented HTTP payload
            family, even when source priority made the merge a state no-op.
        """
        entry = (self.data or {}).get(device_id)
        started_with_data = isinstance(entry, dict)
        if not isinstance(entry, dict):
            entry = self._device_index.get(device_id)
            if not isinstance(entry, dict):
                return False
        parent_binding = self._http_parent_binding(device_id)
        data_epoch = (
            getattr(self, "_http_data_identity_epochs", {}).get(device_id, 0)
            if started_with_data
            else None
        )

        options: _DocumentedHttpReadOptions = {
            "device_property": device_property,
            "system_shadow": system_shadow,
            "battery_packs": battery_packs,
            "subdevice_dev_type": subdevice_dev_type,
        }
        requests = self._documented_http_read_requests(device_id, entry, options)
        if not requests:
            return False

        results = await asyncio.gather(
            *(self._async_http_call(request[3]) for request in requests),
            return_exceptions=True,
        )
        if (
            self._http_data_conflicts_with_parent(device_id, parent_binding)  # ruff: ignore[too-many-boolean-expressions]
            or (not self._http_parent_binding_is_current(device_id, parent_binding))
            or (
                started_with_data
                and not isinstance((self.data or {}).get(device_id), dict)
            )
            or (
                data_epoch is not None
                and data_epoch
                != getattr(self, "_http_data_identity_epochs", {}).get(device_id, 0)
            )
        ):
            for (kind, _dev_type, _identity, _request), result in zip(
                requests, results, strict=True
            ):
                if kind == "battery_pack_list" or not isinstance(result, dict):
                    continue
                original = getattr(result, "original_payload", None)
                payload = original if isinstance(original, dict) else result
                self._record_layer_source_payload(
                    Observation(
                        source=TransportSource.HTTP,
                        device_id=device_id,
                        section=PAYLOAD_PROPERTIES
                        if kind == "device_property"
                        else PAYLOAD_SUBDEVICES,
                        payload=payload,
                    ),
                    identity=f"stale_http_{kind}_{time.monotonic_ns()}",
                    original_payload=original if isinstance(original, dict) else None,
                )
            return False
        # A BLE/MQTT/local-MQTT frame may arrive while the HTTP requests are
        # awaiting I/O. Merge into that latest snapshot, not the pre-await
        # entry, so the manual refresh cannot replay stale live values.
        latest_entry = (self.data or {}).get(device_id)
        working = dict(latest_entry) if isinstance(latest_entry, dict) else dict(entry)
        usable = False
        response_succeeded = False
        accessory_state = self._accessory_source_state
        previous_accessories = {
            key: value for key, value in accessory_state.items() if key[0] == device_id
        }
        previous_conflicts = {
            key
            for key, value in previous_accessories.items()
            if _CT_MODEL_CONFLICT_KEY in value.provenance
        }
        staged_accessories: dict[tuple[str, str, str], IngestResult] | None = None
        committed = False

        def note_commit() -> None:
            nonlocal committed
            _commit_pack_candidates(self)
            committed = True

        try:
            for (kind, dev_type, identity, _request), result in zip(
                requests,
                results,
                strict=True,
            ):
                if isinstance(result, asyncio.CancelledError):
                    raise result
                if isinstance(result, BaseException):
                    _LOGGER.debug(
                        "Jackery documented HTTP read %s failed for %s/%s: %s",
                        kind,
                        device_id,
                        identity or dev_type or "main",
                        exception_debug_message(result),
                    )
                    continue
                if (
                    kind == "device_property"
                    and not self._http_property_response_matches_owner(
                        device_id, result, parent_binding
                    )
                ):
                    original = getattr(result, "original_payload", result)
                    if isinstance(original, dict):
                        self._record_layer_source_payload(
                            Observation(
                                source=TransportSource.HTTP,
                                device_id=device_id,
                                section=PAYLOAD_PROPERTIES,
                                payload=original,
                            ),
                            identity=f"unbound_http_property_{time.monotonic_ns()}",
                            original_payload=original,
                        )
                    continue
                succeeded, changed = self._merge_documented_http_read_result(
                    device_id,
                    working,
                    result,
                    kind=kind,
                    dev_type=dev_type,
                )
                response_succeeded = succeeded or response_succeeded
                usable = changed or usable

            staged_accessories = {
                key: value
                for key, value in accessory_state.items()
                if key[0] == device_id
            }
            previous_data = self.data
            if usable:
                self._push_partial_update(
                    {device_id: working},
                    source=TransportSource.HTTP,
                    battery_packs_complete=True,
                    acknowledge_unchanged=True,
                    on_commit=note_commit,
                    record_source_payload=False,
                )
            if (
                committed
                and self.data is previous_data
                and previous_conflicts
                != {
                    key
                    for key, value in accessory_state.items()
                    if key[0] == device_id
                    and _CT_MODEL_CONFLICT_KEY in value.provenance
                }
            ):
                self.async_update_listeners()
            return response_succeeded
        finally:
            if not committed:
                written = staged_accessories
                if written is None:
                    written = {
                        key: value
                        for key, value in accessory_state.items()
                        if key[0] == device_id
                    }
                for accessory_key, value in written.items():
                    if (
                        accessory_key[1] == PAYLOAD_BATTERY_PACKS
                        and _pack_transaction(self) is not None
                    ):
                        continue
                    if accessory_state.get(accessory_key) is not value:
                        continue
                    if accessory_key in previous_accessories:
                        accessory_state[accessory_key] = previous_accessories[
                            accessory_key
                        ]
                    else:
                        accessory_state.pop(accessory_key, None)

    async def async_query_device_info(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
        allow_ble: bool = True,
        allow_cloud: bool = True,
        allow_local_mqtt: bool = True,
        include_http: bool = False,
    ) -> None:
        """Query the app's device-property snapshot over MQTT."""
        if self.device_ble_profile(device_id) == "box_fixed":
            action_id = ACTION_ID_BOX_READ_DEVICE_INFO
            cmd = BLE_CMD_BOX_READ_DEVICE_INFO
        elif self._is_portable_device_id(device_id):
            action_id = ACTION_ID_PORTABLE_READ_DEVICE_INFO
            cmd = PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id]
        else:
            action_id = ACTION_ID_QUERY_DEVICE_PROPERTY
            cmd = MQTT_CMD_QUERY_DEVICE_PROPERTY
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
            action_id=action_id,
            cmd=cmd,
            body_fields={},
            ensure_mqtt=ensure_mqtt,
            allow_ble=allow_ble,
            allow_cloud=allow_cloud,
            allow_local_mqtt=allow_local_mqtt,
            http_read={"device_property": include_http},
        )

    async def async_query_wifi_list(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query nearby Wi-Fi list (READ_WIFI_LIST, actionId 3001/cmd 1)."""
        box = self.device_ble_profile(device_id) == "box_fixed"
        if box and getattr(self, "_box_wifi_scan_receipts", {}).pop(device_id, None):
            self.async_update_listeners()
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=254 if box else ACTION_ID_READ_WIFI_LIST,
            cmd=MQTT_CMD_READ_WIFI_LIST,
            body_fields={},
            ensure_mqtt=ensure_mqtt,
            allow_cloud=not box,
        )

    async def async_get_time_zone(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query device time-zone config (GET_TIME_ZONE, actionId 3004/cmd 22)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_GET_TIME_ZONE,
            cmd=MQTT_CMD_GET_TIME_ZONE,
            body_fields={},
            ensure_mqtt=ensure_mqtt,
        )

    def _time_zone_command_body(
        self,
        timezone_name: str | None = None,
    ) -> tuple[str, dict[str, Any], int]:
        """Build the app time-zone command body shared by home and portable devices."""
        name = (timezone_name or self.hass.config.time_zone or "UTC").strip()
        timezone = dt_util.get_time_zone(name)
        if timezone is None:
            msg = f"Invalid time zone: {name}"
            raise HomeAssistantError(msg)
        now = dt_util.now(timezone)
        offset = now.utcoffset()
        utc_offset_seconds = int(offset.total_seconds()) if offset is not None else 0
        return (
            name,
            {
                FIELD_UO: utc_offset_seconds,
                FIELD_TIMEZONE: name,
            },
            int(now.timestamp()),
        )

    async def async_send_time_zone(
        self,
        device_id: str,
        *,
        timezone_name: str | None = None,
        allow_ble: bool = True,
        allow_cloud: bool = True,
        ensure_mqtt: bool = True,
    ) -> None:
        """Sync the Home Assistant time zone via the app's SEND_TIME_ZONE body."""
        name, body, timestamp = self._time_zone_command_body(timezone_name)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=(
                1
                if self.device_ble_profile(device_id) == "box_fixed"
                else ACTION_ID_SEND_TIME_ZONE
            ),
            cmd=MQTT_CMD_SEND_TIME_ZONE,
            body_fields=body,
            ble_extra_body_fields={FIELD_TS: timestamp},
            allow_ble=allow_ble,
            allow_cloud=allow_cloud,
            ensure_mqtt=ensure_mqtt,
        )
        self._apply_local_system_patch(device_id, {FIELD_TIMEZONE: name})

    async def async_sync_grid_standard(
        self,
        device_id: str,
        safety: int,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Sync the app grid standard (SYNC_GRID_STANDARD, actionId 3010/cmd 105)."""
        value = int(safety)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_SYNC_GRID_STANDARD,
            cmd=MQTT_CMD_SYNC_GRID_STANDARD,
            body_fields={FIELD_SAFETY: value, FIELD_UNBIND: 1},
            ensure_mqtt=ensure_mqtt,
        )
        self._apply_local_system_patch(device_id, {FIELD_GRID_STANDARD: str(value)})

    async def async_sync_mqtt_connect_info(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
        allow_ble: bool = True,
        allow_cloud: bool = True,
    ) -> None:
        """Sync app cloud-MQTT broker endpoint to device (3005/cmd 99)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=(
                23
                if self.device_ble_profile(device_id) == "box_fixed"
                else ACTION_ID_SYNC_MQTT_CONNECT_INFO
            ),
            cmd=MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
            body_fields={FIELD_HOST: MQTT_HOST, "port": MQTT_PORT},
            ensure_mqtt=ensure_mqtt,
            allow_ble=allow_ble,
            allow_cloud=allow_cloud,
        )

    async def async_check_ble_ota_versions(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        *,
        target_sn: str,
        parent_sn: str | None = None,
        recovery_query: bool = True,
        identity_is_current: Callable[[], bool] | None = None,
        force_fresh_report: bool = False,
    ) -> dict[str, Any]:
        """Check update candidates using the device's recent BLE version report."""
        # BLE starts independently after entry setup. Join its locked startup
        # before inspecting persisted recovery state when an update arrives early.
        if (
            recovery_query  # ruff: ignore[too-many-boolean-expressions]
            and (self._ble_listener is None or self._ble_ota_pending is None)
            and self._ble_ota_runtime_ready()
            and self._ble_writes_enabled()
            and (parent_sn or self._resolve_device_sn(device_id))
            and target_sn
            and self.device_bluetooth_key(device_id) is not None
            and (identity_is_current is None or identity_is_current())
        ):
            start_task = self._schedule_background_once(
                "ble_ota_transport_start",
                self.async_start_ble_transport,
                name="jackery_solarvault_ble_ota_transport_start",
            )
            if start_task is None:
                msg = "BLE firmware version query cannot start the BLE transport"
                raise HomeAssistantError(msg)
            try:
                async with asyncio.timeout(BLE_COMMAND_CONNECT_TIMEOUT_SEC):
                    await asyncio.shield(start_task)
            except TimeoutError as err:
                msg = (
                    "BLE firmware version query timed out waiting for transport startup"
                )
                raise HomeAssistantError(msg) from err
        if (
            recovery_query
            and self._ble_ota_pending is None
            and self._ble_writes_enabled()
        ):
            msg = "BLE firmware version query requires the loaded OTA recovery state"
            raise HomeAssistantError(msg)
        recovering = recovery_query and device_id in (self._ble_ota_pending or {})
        recovery_done = None
        if recovering:
            if device_id in self._ble_ota_recovery_active:
                msg = "BLE OTA recovery is already in progress"
                raise HomeAssistantError(msg)
            self._ble_ota_recovered.pop(device_id, None)
            self._ble_ota_recovery_active.add(device_id)
            recovery_done = asyncio.Event()
            self._ble_ota_recovery_done[device_id] = recovery_done
        try:
            return await self._async_check_ble_ota_versions(
                device_id,
                target_sn=target_sn,
                parent_sn=parent_sn,
                recovery_query=recovery_query,
                identity_is_current=identity_is_current,
                force_fresh_report=force_fresh_report,
            )
        finally:
            if recovering:
                self._ble_ota_recovery_active.discard(device_id)
                if self._ble_ota_recovery_done.get(device_id) is recovery_done:
                    self._ble_ota_recovery_done.pop(device_id, None)
                if recovery_done is not None:
                    recovery_done.set()

    async def _async_check_ble_ota_versions(  # ruff: ignore[too-many-locals, too-many-branches]  # ruff: ignore[too-many-statements]  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        *,
        target_sn: str,
        parent_sn: str | None = None,
        recovery_query: bool,
        identity_is_current: Callable[[], bool] | None,
        force_fresh_report: bool = False,
    ) -> dict[str, Any]:
        """Validate one version report and its selected update candidate."""
        current_pending = (self._ble_ota_pending or {}).get(device_id)
        pending = dict(current_pending) if current_pending is not None else None
        recovery_generation = None
        if pending is not None and recovery_query:
            if identity_is_current is None or not identity_is_current():
                msg = "BLE OTA recovery requires current selected-device identity"
                raise HomeAssistantError(msg)
            recovery_generation = await self._async_refresh_pending_ota_report(
                device_id, target_sn, pending, identity_is_current
            )
        elif pending is None and recovery_query:
            await self._async_ensure_ble_ota_version_report(
                device_id,
                target_sn,
                parent_sn,
                identity_is_current,
                force_fresh_report=force_fresh_report,
            )
        device = (self.data or {}).get(device_id, {})
        versions = device.get(PAYLOAD_BLE_OTA_VERSIONS)
        observed_at = self._ble_ota_versions_observed_at.get(device_id)
        age = (utc_now() - observed_at).total_seconds() if observed_at else None
        expected_parent_sn = parent_sn or self._resolve_device_sn(device_id)
        if not expected_parent_sn or not target_sn:
            msg = "BLE firmware update target serial is required"
            raise HomeAssistantError(msg)
        if (
            not isinstance(versions, list)
            or age is None
            or not 0 <= age <= self._transport_source_freshness_window()
        ):
            msg = "A recent BLE firmware version report is required"
            raise HomeAssistantError(msg)
        listener = self._ble_listener
        generation = (
            listener.current_session_generation(device_id) if listener else None
        )
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or not self._ble_ota_report_matches_target(device_id, generation, target_sn)
        ):
            msg = "The target requires a current, matching BLE version report"
            raise HomeAssistantError(msg)
        for version in versions:
            if not isinstance(version, dict):
                continue
            status = version.get(FIELD_STATUS)
            if (
                isinstance(status, (int, float))
                and not isinstance(status, bool)
                and 1 <= status < 2  # ruff: ignore[magic-value-comparison]
            ):
                msg = "A device firmware update is already in progress"
                raise HomeAssistantError(msg)
        reported_versions = copy.deepcopy(versions)
        result = await self.api.async_get_ble_ota_versions(reported_versions)
        age_after_request = (
            (utc_now() - observed_at).total_seconds() if observed_at else None
        )
        if age_after_request is None or not (
            0 <= age_after_request <= self._transport_source_freshness_window()
        ):
            msg = "BLE firmware version report expired during the update check"
            raise HomeAssistantError(msg)
        if (
            (self.data or {}).get(device_id, {}).get(PAYLOAD_BLE_OTA_VERSIONS)  # ruff: ignore[too-many-boolean-expressions]
            != reported_versions
            or (
                (current_parent_sn := self._resolve_device_sn(device_id)) is not None
                and current_parent_sn != expected_parent_sn
            )
            or (identity_is_current is not None and not identity_is_current())
            or not self._ble_ota_report_matches_target(device_id, generation, target_sn)
        ):
            msg = "BLE firmware version report changed during the update check"
            raise HomeAssistantError(msg)
        candidates = result.get(FIELD_DATA)
        if not isinstance(candidates, list):
            msg = "BLE firmware update candidates are not a list"
            raise HomeAssistantError(msg)
        matched = [
            candidate
            for candidate in candidates
            if isinstance(candidate, dict)
            and candidate.get(FIELD_DEVICE_SN) == target_sn
        ]
        if len(matched) != 1:
            msg = "BLE firmware update candidate does not uniquely match the target"
            raise HomeAssistantError(msg)
        if pending is not None and recovery_generation is not None:
            candidate = matched[0]
            listener = self._ble_listener
            if (
                isinstance(candidate.get(FIELD_UPDATE_STATUS), int)  # ruff: ignore[too-many-boolean-expressions]
                and not isinstance(candidate.get(FIELD_UPDATE_STATUS), bool)
                and candidate.get(FIELD_UPDATE_STATUS) in {0, _BOX_OTA_VERSION_CURRENT}
                and candidate.get("currentVersion") == pending.get("target_version")
                and listener is not None
                and self._ble_ota_pending is not None
                and self._ble_ota_pending.get(device_id) == pending
                and listener.current_session_generation(device_id)
                == recovery_generation
                and self._pending_ota_identity_current(device_id, target_sn, pending)
                and self._ble_pending_target_is_owned(
                    device_id, recovery_generation, target_sn, pending
                )
                and identity_is_current is not None
                and identity_is_current()
            ):
                self._authorize_ota_current_session(
                    device_id, recovery_generation, pending, identity_is_current
                )
        receipts = getattr(self, "_ble_ota_candidate_receipts", None)
        if receipts is None:
            self._ble_ota_candidate_receipts = receipts = {}
        receipts[device_id, target_sn] = {
            "parent_sn": expected_parent_sn,
            "generation": generation,
            "fingerprint": self._ble_ota_versions_fingerprint.get(device_id),
            "report": reported_versions,
            "candidate": copy.deepcopy(matched[0]),
        }
        return {**result, FIELD_DATA: matched}

    async def _async_ensure_ble_ota_version_report(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
        self,
        device_id: str,
        target_sn: str,
        parent_sn: str | None,
        identity_is_current: Callable[[], bool] | None,
        *,
        force_fresh_report: bool = False,
    ) -> None:
        """Acquire missing version proof before the first firmware update check."""
        listener = self._ble_listener
        expected_parent_sn = parent_sn or self._resolve_device_sn(device_id)
        profile = self.device_ble_profile(device_id)
        # Model metadata may be populated from another source while BLE starts.
        # Compare its canonical, unambiguous numeric identity here so an added
        # duplicate source (or int/string representation change) cannot make a
        # still-identical device fail the initial read-only version query.
        model = self.device_model_code(device_id)
        key = self.device_bluetooth_key(device_id)

        def identity_failure_reasons() -> tuple[str, ...]:
            """Name each stale identity component without exposing credentials."""
            failures: list[str] = []
            if listener is None or self._ble_listener is not listener:
                failures.append("ble_listener_changed")
            if not self._ble_ota_runtime_ready():
                failures.append("ble_runtime_not_ready")
            if not self._ble_writes_enabled():
                failures.append("ble_writes_disabled")
            if not expected_parent_sn or not target_sn:
                failures.append("device_serial_missing")
            elif (
                current_parent_sn := self._resolve_device_sn(device_id)
            ) is not None and current_parent_sn != expected_parent_sn:
                failures.append("device_serial_changed")
            if not self._ble_ota_profile_matches(device_id, profile):
                failures.append("ble_profile_changed")
            if self.device_model_code(device_id) != model:
                failures.append("device_model_changed")
            if key is None or self.device_bluetooth_key(device_id) != key:
                failures.append("bluetooth_key_changed")
            if self._ble_ota_pending is None:
                failures.append("ota_recovery_state_unloaded")
            elif device_id in self._ble_ota_pending:
                failures.append("ota_transfer_became_pending")
            if identity_is_current is not None and not identity_is_current():
                failures.append("selected_registry_identity_changed")
            return tuple(failures)

        def identity_current() -> bool:
            return not identity_failure_reasons()

        if not self._ble_ota_runtime_ready():
            msg = "BLE firmware version query is unavailable during shutdown"
            raise HomeAssistantError(msg)
        if not self._ble_writes_enabled():
            msg = "BLE firmware version query requires BLE transport to be enabled"
            raise HomeAssistantError(msg)
        if not expected_parent_sn or not target_sn:
            msg = "BLE firmware version query requires the current device identity"
            raise HomeAssistantError(msg)
        if key is None:
            msg = "BLE firmware version query requires the device Bluetooth key"
            raise HomeAssistantError(msg)
        if identity_is_current is not None and not identity_is_current():
            msg = "BLE firmware version query requires the current selected-device identity"  # ruff: ignore[line-too-long]
            raise HomeAssistantError(msg)
        if listener is None:
            msg = "BLE firmware version query requires an enabled, ready BLE transport"
            raise HomeAssistantError(msg)
        if self._ble_ota_pending is None:
            msg = "BLE firmware version query requires the loaded OTA recovery state"
            raise HomeAssistantError(msg)
        if device_id in self._ble_ota_pending:
            msg = "BLE firmware version query requires recovery of the pending transfer"
            raise HomeAssistantError(msg)
        if profile not in {
            None,
            "box_fixed",
            "rc4",
            "aes_fixed",
            "aes_random",
        }:
            msg = "BLE firmware version query requires a supported, unambiguous device model"  # ruff: ignore[line-too-long]
            raise HomeAssistantError(msg)
        if not self._ble_ota_profile_matches(device_id, profile):
            msg = "BLE firmware version query requires a known device model matching its BLE profile"  # ruff: ignore[line-too-long]
            raise HomeAssistantError(msg)
        identity_failures = identity_failure_reasons()
        if identity_failures:
            _LOGGER.warning(
                "Jackery BLE OTA version preflight rejected stale identity "
                "evidence for %s: %s",
                device_id,
                ",".join(identity_failures),
            )
            msg = "BLE firmware version query identity preflight failed: " + ", ".join(
                identity_failures
            )
            raise HomeAssistantError(msg)
        assert listener is not None
        generation = listener.current_session_generation(device_id)
        if (
            not force_fresh_report
            and isinstance(generation, int)
            and not isinstance(generation, bool)
        ) and self._ble_ota_report_matches_target(device_id, generation, target_sn):
            return
        if (
            not await listener.async_ensure_connected(
                device_id, timeout_sec=BLE_COMMAND_CONNECT_TIMEOUT_SEC
            )
            or not identity_current()
        ):
            msg = "A connected BLE session is required to query firmware versions"
            raise HomeAssistantError(msg)
        generation = listener.current_session_generation(device_id)
        if isinstance(generation, bool) or not isinstance(generation, int):
            msg = "BLE firmware version query has no current session"
            raise HomeAssistantError(msg)
        family = (
            "box" if profile == "box_fixed" else "home" if profile is None else profile
        )
        barrier = await listener.async_query_box_ota_version(
            device_id,
            session_generation=generation,
            family=family,
            is_safe=identity_current,
        )
        deadline = asyncio.get_running_loop().time() + 30.0
        while True:
            if not identity_current() or (
                listener.current_session_generation(device_id) != generation
            ):
                _LOGGER.warning(
                    "Jackery BLE OTA version query lost source continuity for %s: "
                    "identity=%s session_changed=%s",
                    device_id,
                    ",".join(identity_failure_reasons()) or "none",
                    listener.current_session_generation(device_id) != generation,
                )
                msg = "BLE firmware version query identity or session changed"
                raise HomeAssistantError(msg)
            observed = self._ble_ota_versions_observed_at.get(device_id)
            first = self._ble_ota_versions_first_sequence.get(device_id)
            if (
                observed is not None
                and first is not None
                and first > barrier
                and self._ble_ota_report_matches_target(
                    device_id, generation, target_sn
                )
            ):
                if (
                    not await listener.async_wait_for_notification_drain(
                        device_id, generation
                    )
                    or not identity_current()
                ):
                    msg = "BLE firmware version report is no longer current"
                    raise HomeAssistantError(msg)
                return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                msg = "A current matching BLE firmware version report did not arrive"
                raise HomeAssistantError(msg)
            await asyncio.sleep(min(0.25, remaining))

    def _authorize_ota_current_session(
        self,
        device_id: str,
        generation: int,
        pending: dict[str, str],
        identity_is_current: Callable[[], bool],
        directly_completed: bool = False,
    ) -> None:
        listener = self._ble_listener
        observed = self._ble_ota_versions_observed_at.get(device_id)
        versions = (self.data or {}).get(device_id, {}).get(PAYLOAD_BLE_OTA_VERSIONS)
        fingerprint = self._ble_ota_versions_fingerprint.get(device_id)
        first = self._ble_ota_versions_first_sequence.get(device_id)
        key = self.device_bluetooth_key(device_id)
        if (
            listener is None  # ruff: ignore[too-many-boolean-expressions]
            or observed is None
            or not isinstance(versions, list)
            or self._ble_ota_versions_generation.get(device_id) != generation
            or key is None
            or fingerprint != hashlib.sha256(key).digest()
            or not identity_is_current()
            or not self._pending_ota_identity_current(
                device_id, pending.get("target_sn", ""), pending
            )
            or (
                pending.get("target_kind") == "ground_accessory"
                and not self._ble_pending_target_is_owned(
                    device_id, generation, pending.get("target_sn", ""), pending
                )
            )
        ):
            msg = "BLE OTA session could not be confirmed"
            raise HomeAssistantError(msg)
        listener.release_ota_recovery(device_id, generation)
        self._ble_ota_recovered[device_id] = (
            generation,
            copy.deepcopy(pending),
            observed,
            copy.deepcopy(versions),
            fingerprint,
            first,
            identity_is_current,
            directly_completed,
        )

    def _ble_ota_pending_blocked(self, device_id: str) -> bool:
        pending = self._ble_ota_pending
        if pending is None:
            return True
        marker = pending.get(device_id)
        if marker is None:
            return False
        if device_id in self._ble_ota_recovery_active:
            return True
        recovered = self._ble_ota_recovered.get(device_id)
        listener = self._ble_listener
        if recovered is None or listener is None:
            return True
        (
            generation,
            expected_marker,
            observed_at,
            versions,
            fingerprint,
            expected_first,
            identity_is_current,
            directly_completed,
        ) = recovered
        first = self._ble_ota_versions_first_sequence.get(device_id)
        current_versions = (
            (self.data or {}).get(device_id, {}).get(PAYLOAD_BLE_OTA_VERSIONS)
        )
        report_is_current = (
            self._ble_ota_versions_observed_at.get(device_id) == observed_at
            and first == expected_first
            and current_versions == versions
            and 0
            <= (utc_now() - observed_at).total_seconds()
            <= self._transport_source_freshness_window()
        )
        if directly_completed:
            report_is_current = self._ble_ota_versions_idle_for_target(
                current_versions, marker.get("target_sn", "")
            )
        return not (
            marker == expected_marker
            and isinstance(generation, int)
            and first is not None
            and self._ble_ota_versions_generation.get(device_id) == generation
            and self._ble_ota_versions_fingerprint.get(device_id) == fingerprint
            and listener.current_session_generation(device_id) == generation
            and listener.session_is_current(device_id, generation, first)
            and report_is_current
            and self._pending_ota_identity_current(
                device_id, marker.get("target_sn", ""), marker
            )
            and (
                marker.get("target_kind") != "ground_accessory"
                or self._ble_pending_target_is_owned(
                    device_id, generation, marker.get("target_sn", ""), marker
                )
            )
            and (
                directly_completed
                or self._ble_pending_target_is_owned(
                    device_id, generation, marker.get("target_sn", ""), marker
                )
            )
            and callable(identity_is_current)
            and identity_is_current()
        )

    def _pending_ota_identity_current(
        self, device_id: str, target_sn: str, pending: dict[str, str]
    ) -> bool:
        profile = self.device_ble_profile(device_id)
        key = self.device_bluetooth_key(device_id)
        target_kind = pending.get("target_kind")
        if target_kind == "ground_accessory":
            identity = self.ground_ble_identity(device_id)
            if identity is None or identity.serial != target_sn:
                return False
        elif target_kind not in {None, "device"}:
            return False
        return (
            self._ble_ota_runtime_ready()
            and self._ble_ota_pending is not None
            and self._ble_ota_pending.get(device_id) == pending
            and target_sn == pending.get("target_sn")
            and self._resolve_device_sn(device_id) == pending.get("parent_sn")
            and (profile or "home") == pending.get("profile")
            and self._ble_ota_profile_matches(device_id, profile)
            and self._ble_ota_registry_is_current(device_id, pending)
            and json.dumps(
                self._ble_ota_model_evidence(device_id), separators=(",", ":")
            )
            == pending.get("model_evidence")
            and key is not None
            and hashlib.sha256(key).hexdigest() == pending.get("key_fingerprint")
        )

    @staticmethod
    def _ble_ota_versions_idle_for_target(versions: object, target_sn: str) -> bool:
        return bool(
            isinstance(versions, list)
            and versions
            and all(
                isinstance(row, dict)
                and isinstance(row.get(FIELD_STATUS), int)
                and not isinstance(row.get(FIELD_STATUS), bool)
                and row.get(FIELD_STATUS) == 0
                for row in versions
            )
            and sum(row.get(FIELD_DEVICE_SN) == target_sn for row in versions) == 1
        )

    def _ble_ota_runtime_ready(self) -> bool:
        return not self._shutdown_started and self.hass.state not in {
            CoreState.stopping,
            CoreState.final_write,
            CoreState.stopped,
        }

    def _ble_ota_registry_evidence(
        self, device_id: str, selected_id: str
    ) -> dict[str, Any] | None:
        registry = dr.async_get(self.hass)
        selected = registry.async_get(selected_id)
        if selected is None:
            if selected_id != device_id:
                return None
            owner_device = registry.async_get_device_by_identifier(
                (DOMAIN, device_id), self.entry.entry_id
            )
            if owner_device is None:
                return None
            return {
                "selected_id": selected_id,
                "parent_id": owner_device.id,
                "parent_entry_id": owner_device.config_entry_id,
                "parent_identifiers": sorted(owner_device.identifiers),
            }
        if selected.config_entry_id != self.entry.entry_id:
            return None
        parent = getattr(selected, "parent_device_id", None) or getattr(
            selected, "via_device_id", None
        )
        parent_device = registry.async_get(parent) if parent is not None else selected
        if (
            parent_device is None
            or parent_device.config_entry_id != self.entry.entry_id
            or (DOMAIN, device_id) not in parent_device.identifiers
        ):
            return None
        return {
            "selected_id": selected_id,
            "entry_id": selected.config_entry_id,
            "identifiers": sorted(selected.identifiers),
            "parent_id": parent,
            "parent_entry_id": parent_device.config_entry_id,
            "parent_identifiers": sorted(parent_device.identifiers),
            "serial": (
                selected.serial_number if isinstance(selected, dr.DeviceEntry) else None
            ),
        }

    def _ble_ota_registry_is_current(
        self, device_id: str, marker: dict[str, str]
    ) -> bool:
        selected_id = marker.get("selected_device_id")
        if not selected_id:
            return False
        evidence = self._ble_ota_registry_evidence(device_id, selected_id)
        return evidence is not None and json.dumps(
            evidence, sort_keys=True, separators=(",", ":")
        ) == marker.get("registry_evidence")

    async def _async_run_ground_ota_recovery(self, device_id: str) -> None:
        """Coalesce Ground rebindings and honor the existing recovery backoff."""
        while not self._shutdown_started:
            trigger = self._ble_ground_recovery_triggers.get(device_id, 0)
            if device_id in self._ble_ota_recovery_active:
                finished = self._ble_ota_recovery_done.get(device_id)
                if finished is None:
                    return
                await finished.wait()
                continue
            listener = self._ble_listener
            marker = (self._ble_ota_pending or {}).get(device_id)
            if listener is None or marker is None:
                return
            generation = listener.current_session_generation(device_id)
            if generation is None or not (
                await listener.async_wait_for_notification_drain(device_id, generation)
            ):
                if self._ble_ground_recovery_triggers.get(device_id, 0) != trigger:
                    continue
                return
            if not self._ble_ota_pending_blocked(device_id):
                return
            delay = self._ble_ota_recovery_retry_at.get(device_id, 0) - time.monotonic()
            if not math.isfinite(delay):
                return
            if delay > 0:
                await asyncio.sleep(delay)
            if device_id in self._ble_ota_recovery_active:
                continue
            await self._async_try_automatic_ota_recovery(device_id)
            if self._ble_ground_recovery_triggers.get(device_id, 0) == trigger:
                self._ble_ground_recovery_triggers.pop(device_id, None)
                return

    async def _async_try_automatic_ota_recovery(self, device_id: str) -> None:
        """Refresh a pending OTA proof on demand without blocking other transports."""
        current_pending = (self._ble_ota_pending or {}).get(device_id)
        pending = dict(current_pending) if current_pending is not None else None
        listener = self._ble_listener
        now = time.monotonic()
        if (
            pending is None  # ruff: ignore[too-many-boolean-expressions]
            or listener is None
            or not self._ble_ota_runtime_ready()
            or listener.current_session_generation(device_id) is None
            or device_id in self._ble_ota_recovery_active
            or now < self._ble_ota_recovery_retry_at.get(device_id, 0)
            or not self._ble_ota_registry_is_current(device_id, pending)
        ):
            return
        target_sn = pending.get("target_sn", "")
        if pending.get("target_kind") == "ground_accessory":
            generation = listener.current_session_generation(device_id)
            if generation is None or not (
                await listener.async_wait_for_notification_drain(device_id, generation)
            ):
                return
            if (
                self._ble_listener is not listener
                or (self._ble_ota_pending or {}).get(device_id) != pending
            ):
                return
        ground_identity = (
            self.ground_ble_identity(device_id)
            if pending.get("target_kind") == "ground_accessory"
            else None
        )
        if pending.get("target_kind") == "ground_accessory" and ground_identity is None:
            return

        def identity_is_current() -> bool:
            if not self._ble_ota_registry_is_current(device_id, pending):
                return False
            if pending.get("target_kind") != "ground_accessory":
                return True
            current = self.ground_ble_identity(device_id)
            return bool(
                ground_identity is not None
                and current is not None
                and current.generation == ground_identity.generation
                and current.first_sequence == ground_identity.first_sequence
                and current.fingerprint == ground_identity.fingerprint
            )

        recovered = False
        try:
            await self.async_check_ble_ota_versions(
                device_id,
                target_sn=target_sn,
                parent_sn=pending.get("parent_sn"),
                identity_is_current=identity_is_current,
            )
            recovered = device_id in self._ble_ota_recovered
        except (
            HomeAssistantError,
            JackeryError,
            OSError,
            RuntimeError,
            ValueError,
            TimeoutError,
        ) as err:
            _LOGGER.debug(
                "Jackery BLE OTA recovery for %s is pending: %s", device_id, err
            )
        finally:
            if recovered:
                self._ble_ota_recovery_retry_at.pop(device_id, None)
            else:
                self._ble_ota_recovery_retry_at[device_id] = time.monotonic() + 60.0

    async def _async_refresh_pending_ota_report(
        self,
        device_id: str,
        target_sn: str,
        pending: dict[str, str],
        identity_is_current: Callable[[], bool],
    ) -> int:
        """Request a fresh session-bound version list for a pending transfer."""
        listener = self._ble_listener
        generation = (
            listener.current_session_generation(device_id) if listener else None
        )
        if (
            listener is None
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or not self._pending_ota_identity_current(device_id, target_sn, pending)
            or not identity_is_current()
        ):
            msg = "BLE OTA recovery requires the original current device session"
            raise HomeAssistantError(msg)
        if pending.get("target_kind") == "ground_accessory":
            identity = self.ground_ble_identity(device_id)
            prior_session = pending.get("source_session")
            if (
                identity is None
                or not isinstance(prior_session, str)
                or len(prior_session) != _SHA256_HEX_LENGTH
                or not all(char in "0123456789abcdef" for char in prior_session)
                or self._ground_ble_session_fingerprint(identity) == prior_session
            ):
                msg = "Ground OTA recovery requires a new BLE session"
                raise HomeAssistantError(msg)
        profile = self.device_ble_profile(device_id)
        key = self.device_bluetooth_key(device_id)
        if key is None:
            msg = "BLE OTA recovery has no Bluetooth key"
            raise HomeAssistantError(msg)
        family = (
            "box" if profile == "box_fixed" else "home" if profile is None else profile
        )
        previous = self._ble_ota_versions_observed_at.get(device_id)
        barrier = await listener.async_query_box_ota_version(
            device_id,
            session_generation=generation,
            is_safe=lambda: (
                self._pending_ota_identity_current(device_id, target_sn, pending)
                and identity_is_current()
            ),
            family=family,
        )
        deadline = asyncio.get_running_loop().time() + 30.0
        while True:
            observed = self._ble_ota_versions_observed_at.get(device_id)
            first = self._ble_ota_versions_first_sequence.get(device_id)
            if (
                observed is not None  # ruff: ignore[too-many-boolean-expressions]
                and (previous is None or observed > previous)
                and self._ble_ota_versions_generation.get(device_id) == generation
                and self._ble_ota_versions_fingerprint.get(device_id)
                == hashlib.sha256(key).digest()
                and first is not None
                and first > barrier
            ):
                break
            if listener.current_session_generation(device_id) != generation or not (
                self._pending_ota_identity_current(device_id, target_sn, pending)
                and identity_is_current()
            ):
                msg = "BLE OTA recovery identity or session changed"
                raise HomeAssistantError(msg)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                msg = "BLE OTA recovery version report did not arrive"
                raise HomeAssistantError(msg)
            await asyncio.sleep(min(0.25, remaining))
        versions = (self.data or {}).get(device_id, {}).get(PAYLOAD_BLE_OTA_VERSIONS)
        if not self._ble_ota_versions_idle_for_target(
            versions, target_sn
        ) or not listener.session_is_current(device_id, generation, first):
            msg = "BLE OTA recovery report is incomplete or update is active"
            raise HomeAssistantError(msg)
        return generation

    async def _async_refresh_ground_ota_report(
        self,
        device_id: str,
        target_sn: str,
        generation: int,
        identity_is_current: Callable[[], bool],
    ) -> None:
        """Request a fresh, target-bound Ground version report before transfer."""
        listener = self._ble_listener
        profile = self.device_ble_profile(device_id)
        key = self.device_bluetooth_key(device_id)
        if (
            listener is None
            or profile not in {"rc4", "aes_fixed", "aes_random"}
            or key is None
        ):
            msg = "Ground OTA requires a current Portable BLE session"
            raise HomeAssistantError(msg)
        previous = self._ble_ota_versions_observed_at.get(device_id)
        barrier = await listener.async_query_box_ota_version(
            device_id,
            session_generation=generation,
            family=profile,
            is_safe=lambda: (
                identity_is_current()
                and self.ground_ble_ota_target(device_id) == target_sn
                and self.portable_ble_ota_allowed(device_id, generation)
            ),
        )
        deadline = asyncio.get_running_loop().time() + 30.0
        while True:
            observed = self._ble_ota_versions_observed_at.get(device_id)
            first = self._ble_ota_versions_first_sequence.get(device_id)
            if (
                observed is not None  # ruff: ignore[too-many-boolean-expressions]
                and (previous is None or observed > previous)
                and self._ble_ota_versions_generation.get(device_id) == generation
                and self._ble_ota_versions_fingerprint.get(device_id)
                == hashlib.sha256(key).digest()
                and first is not None
                and first > barrier
            ):
                if (
                    not await listener.async_wait_for_notification_drain(
                        device_id, generation
                    )
                    or not identity_is_current()
                    or self.ground_ble_ota_target(device_id) != target_sn
                ):
                    msg = "Ground OTA version report is invalid or no longer current"
                    raise HomeAssistantError(msg)
                return
            if (
                not identity_is_current()
                or listener.current_session_generation(device_id) != generation
                or self.device_bluetooth_key(device_id) != key
            ):
                msg = "Ground OTA identity or session changed during version query"
                raise HomeAssistantError(msg)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                msg = "Ground OTA version report did not arrive"
                raise HomeAssistantError(msg)
            await asyncio.sleep(min(0.25, remaining))

    def _ble_ota_safety_observation_is_current(
        self,
        device_id: str,
        generation: int,
        profile: str | None,
        *,
        after_sequence: int | None = None,
    ) -> bool:
        """Return whether a current BLE safety report belongs to this session."""
        listener = self._ble_listener
        if profile == "box_fixed":
            report = getattr(self, "_ble_box_ota_faults", {}).get(device_id)
            if not isinstance(report, tuple) or len(report) != 5:  # ruff: ignore[magic-value-comparison]
                return False
            observed_at, observed_generation, sequence, _allowed, fingerprint = report
        elif profile in {None, "rc4", "aes_fixed", "aes_random"}:
            report = getattr(self, "_ble_ota_battery", {}).get(device_id)
            if not isinstance(report, tuple) or len(report) != 6:  # ruff: ignore[magic-value-comparison]
                return False
            (
                observed_at,
                observed_generation,
                sequence,
                _allowed,
                fingerprint,
                observed_profile,
            ) = report
            if observed_profile != profile:
                return False
        else:
            return False
        key = self.device_bluetooth_key(device_id)
        age = (utc_now() - observed_at).total_seconds()
        return bool(
            listener is not None
            and isinstance(sequence, int)
            and not isinstance(sequence, bool)
            and (after_sequence is None or sequence > after_sequence)
            and observed_generation == generation
            and key is not None
            and fingerprint == hashlib.sha256(key).digest()
            and 0 <= age <= self._transport_source_freshness_window()
            and listener.session_is_current(device_id, generation, sequence)
        )

    async def _async_ensure_ble_ota_safety_observation(
        self,
        device_id: str,
        generation: int,
        profile: str | None,
        identity_is_current: Callable[[], bool],
        *,
        force_refresh: bool = False,
    ) -> None:
        """Refresh a missing or stale battery/fault proof over the current BLE session."""  # ruff: ignore[line-too-long]
        listener = self._ble_listener
        if (
            listener is None
            or self._ble_listener is not listener
            or listener.current_session_generation(device_id) != generation
            or not identity_is_current()
        ):
            msg = "BLE OTA safety refresh requires the current selected device session"
            raise HomeAssistantError(msg)
        if not force_refresh and self._ble_ota_safety_observation_is_current(
            device_id, generation, profile
        ):
            return
        marker = listener.current_notification_marker(device_id)
        if marker is None or marker[0] != generation:
            msg = "BLE OTA safety refresh has no current notification sequence"
            raise HomeAssistantError(msg)
        await self.async_query_device_info(
            device_id,
            ensure_mqtt=False,
            allow_ble=True,
            allow_cloud=False,
            allow_local_mqtt=False,
        )
        deadline = asyncio.get_running_loop().time() + BLE_COMMAND_CONNECT_TIMEOUT_SEC
        while True:
            if (
                self._ble_listener is not listener
                or listener.current_session_generation(device_id) != generation
                or not identity_is_current()
            ):
                msg = "BLE OTA identity or session changed during safety refresh"
                raise HomeAssistantError(msg)
            if self._ble_ota_safety_observation_is_current(
                device_id,
                generation,
                profile,
                after_sequence=marker[1],
            ):
                if (
                    not await listener.async_wait_for_notification_drain(
                        device_id, generation
                    )
                    or not identity_is_current()
                ):
                    msg = "BLE OTA safety response is no longer current"
                    raise HomeAssistantError(msg)
                if self._ble_ota_safety_observation_is_current(
                    device_id,
                    generation,
                    profile,
                    after_sequence=marker[1],
                ):
                    return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                msg = "A fresh BLE OTA battery/fault status did not arrive"
                raise HomeAssistantError(msg)
            await asyncio.sleep(min(0.25, remaining))

    async def async_start_box_ble_ota(  # ruff: ignore[too-many-locals, too-many-branches, too-many-statements, complex-structure, too-many-arguments]
        self,
        device_id: str,
        *,
        target_sn: str,
        candidate: dict[str, Any],
        identity_is_current: Callable[[], bool],
        selected_device_id: str | None = None,
        target_kind: str = "device",
    ) -> str:
        """Download the selected app firmware and run its guarded BLE transfer."""
        listener = self._ble_listener
        parent_sn = self._resolve_device_sn(device_id)
        profile = self.device_ble_profile(device_id)
        family_name = (
            "Home"
            if profile is None
            else "Box"
            if profile == "box_fixed"
            else "Portable"
        )
        model_evidence = self._ble_ota_model_evidence(device_id)
        if target_kind not in {"device", "ground_accessory"}:
            msg = "Unsupported BLE OTA target kind"
            raise HomeAssistantError(msg)
        if profile not in {None, "box_fixed", "rc4", "aes_fixed", "aes_random"}:
            msg = "BLE OTA is not available for this device family"
            raise HomeAssistantError(msg)
        generation = (
            listener.current_session_generation(device_id)
            if listener is not None
            else None
        )
        if listener is None or generation is None or not parent_sn:
            msg = f"A connected {family_name} BLE session is required"
            raise HomeAssistantError(msg)
        if listener.box_ota_started(device_id, generation):
            msg = "BLE OTA requires a new session after a previous transfer"
            raise HomeAssistantError(msg)
        if self._ble_ota_pending_blocked(device_id):
            msg = "A previous BLE firmware transfer requires recovery"
            raise HomeAssistantError(msg)
        ground_identity = (
            self.ground_ble_identity(device_id)
            if target_kind == "ground_accessory"
            else None
        )
        if target_kind == "ground_accessory":
            if (
                ground_identity is None
                or self.ground_ble_ota_target(device_id) != target_sn
            ):
                msg = "Ground OTA target is not current"
                raise HomeAssistantError(msg)
            await self._async_refresh_ground_ota_report(
                device_id, target_sn, generation, identity_is_current
            )
            fresh = await self.async_check_ble_ota_versions(
                device_id,
                target_sn=target_sn,
                parent_sn=parent_sn,
                recovery_query=False,
            )
            candidates = fresh.get(FIELD_DATA)
            if (
                not isinstance(candidates, list)
                or len(candidates) != 1
                or candidates[0] != candidate
                or not identity_is_current()
            ):
                msg = "Ground OTA candidate changed after the version query"
                raise HomeAssistantError(msg)
        report_time = self._ble_ota_versions_observed_at.get(device_id)
        report_generation = self._ble_ota_versions_generation.get(device_id)
        report_fingerprint = self._ble_ota_versions_fingerprint.get(device_id)
        session_key = self.device_bluetooth_key(device_id)
        report = copy.deepcopy(
            (self.data or {}).get(device_id, {}).get(PAYLOAD_BLE_OTA_VERSIONS)
        )
        owned_marker: dict[str, str] | None = None

        def is_current(selected: str) -> bool:
            pending_marker = (self._ble_ota_pending or {}).get(device_id)
            return (
                selected == target_sn
                and identity_is_current() is True
                and self._resolve_device_sn(device_id) == parent_sn
                and self._ble_ota_profile_matches(device_id, profile)
                and self._ble_ota_model_evidence(device_id) == model_evidence
                and (
                    target_kind != "ground_accessory"
                    or (
                        (
                            current_ground := self.ground_ble_identity(
                                device_id, require_connected=False
                            )
                        )
                        is not None
                        and ground_identity is not None
                        and current_ground.generation == ground_identity.generation
                        and current_ground.first_sequence
                        == ground_identity.first_sequence
                        and current_ground.serial == target_sn
                    )
                )
                and (
                    pending_marker == owned_marker
                    if target_kind == "ground_accessory"
                    else (
                        pending_marker is None
                        or self._ble_ota_registry_is_current(device_id, pending_marker)
                    )
                )
            )

        def is_safe(selected: str) -> bool:
            return (
                is_current(selected)
                and self._ble_ota_runtime_ready()
                and (
                    self.box_ble_ota_target_allowed(device_id, generation, selected)
                    if profile == "box_fixed"
                    else (
                        self.ground_ble_ota_target(device_id) == selected
                        if target_kind == "ground_accessory"
                        else self._ble_ota_target_is_owned(
                            device_id, generation, selected
                        )
                    )
                    and (
                        self.home_ble_ota_allowed(device_id, generation)
                        if profile is None
                        else self.portable_ble_ota_allowed(device_id, generation)
                    )
                )
                and (
                    listener.box_ota_started(device_id, generation)
                    or (
                        report_time is not None
                        and report_generation == generation
                        and session_key is not None
                        and report_fingerprint == hashlib.sha256(session_key).digest()
                        and self.device_bluetooth_key(device_id) == session_key
                        and 0
                        <= (utc_now() - report_time).total_seconds()
                        <= self._transport_source_freshness_window()
                        and (self.data or {})
                        .get(device_id, {})
                        .get(PAYLOAD_BLE_OTA_VERSIONS)
                        == report
                    )
                )
            )

        if target_kind != "ground_accessory":
            await self._async_ensure_ble_ota_safety_observation(
                device_id,
                generation,
                profile,
                lambda: is_current(target_sn),
            )
        if is_safe(target_sn) is not True:
            if not is_current(target_sn):
                msg = f"{family_name} BLE OTA selected device identity or model is no longer current"  # ruff: ignore[line-too-long]
            elif not self._ble_ota_runtime_ready():
                msg = f"{family_name} BLE OTA runtime is not available"
            elif not self._ble_writes_enabled():
                msg = f"{family_name} BLE OTA device writes are disabled"
            elif listener.current_session_generation(device_id) != generation:
                msg = f"{family_name} BLE OTA session changed"
            elif self.device_bluetooth_key(device_id) != session_key:
                msg = f"{family_name} BLE OTA Bluetooth key changed"
            elif (
                self.ground_ble_ota_target(device_id) != target_sn
                if target_kind == "ground_accessory"
                else not self._ble_ota_target_is_owned(device_id, generation, target_sn)
            ):
                msg = f"{family_name} BLE OTA requires a current version report for the selected target"  # ruff: ignore[line-too-long]
            elif profile == "box_fixed" and not self.box_ble_ota_allowed(
                device_id, generation
            ):
                msg = "Box BLE OTA requires a current fault-free device status"
            elif profile is None and not self.home_ble_ota_allowed(
                device_id, generation
            ):
                msg = "Home BLE OTA requires a current main-battery reading with charge above 10%"  # ruff: ignore[line-too-long]
            elif profile in {
                "rc4",
                "aes_fixed",
                "aes_random",
            } and not self.portable_ble_ota_allowed(device_id, generation):
                msg = "Portable BLE OTA requires a current battery reading with charge above 10%"  # ruff: ignore[line-too-long]
            else:
                msg = f"{family_name} BLE OTA firmware version report changed or is no longer current"  # ruff: ignore[line-too-long]
            raise HomeAssistantError(msg)
        # Gson's String/List<String> OTA bean also accepts integral JSON IDs.
        # Keep that projection local so original payload diagnostics stay raw.
        version = _ble_ota_decimal_id(candidate.get("targetVersionId"))
        target_version = candidate.get("targetVersion")
        modules = _ble_ota_module_ids(candidate.get("targetModuleVersion"))
        status = candidate.get(FIELD_UPDATE_STATUS)
        if (
            candidate.get(FIELD_DEVICE_SN) != target_sn  # ruff: ignore[too-many-boolean-expressions]
            or version is None
            or not isinstance(target_version, str)
            or not target_version.strip()
            or isinstance(status, bool)
            or not isinstance(status, int)
            or status != 0
            or modules is None
        ):
            msg = f"{family_name} BLE OTA update candidate is invalid"
            raise HomeAssistantError(msg)
        links = await self.api.async_get_ble_ota_link(
            device_sn=parent_sn,
            sub_device_sn="" if target_sn == parent_sn else target_sn,
            target_firmware_ids=",".join(modules),
            target_version_id=version,
        )
        if not is_safe(target_sn):
            msg = f"{family_name} OTA target changed during firmware selection"
            raise HomeAssistantError(msg)
        firmware_links = _ble_ota_firmware_urls(
            links.get(FIELD_DATA), target_sn, family_name
        )
        mtu = listener.mtu_for_device(device_id)
        if profile in {"rc4", "aes_fixed"}:
            mtu = min(mtu, 315)
        max_bytes = min(
            max(0, mtu - (50 if profile == "box_fixed" else 60)) * 65535,
            _BOX_OTA_MAX_TOTAL_BYTES,
        )
        if not max_bytes:
            msg = f"{family_name} OTA session MTU is too small"
            raise HomeAssistantError(msg)
        firmware: dict[str, bytes] = {}
        remaining = _BOX_OTA_MAX_TOTAL_BYTES
        for name, url in firmware_links.items():
            if remaining <= 0:
                msg = f"{family_name} OTA firmware exceeds the total size limit"
                raise HomeAssistantError(msg)
            firmware[name] = await self.api.async_download_ble_ota_firmware(
                url, max_bytes=min(max_bytes, remaining)
            )
            remaining -= len(firmware[name])
            if (
                not is_current(target_sn)
                or listener.current_session_generation(device_id) != generation
                or self.device_bluetooth_key(device_id) != session_key
            ):
                msg = f"{family_name} OTA target changed during firmware download"
                raise HomeAssistantError(msg)
        if target_kind != "ground_accessory":
            fresh = await self.async_check_ble_ota_versions(
                device_id,
                target_sn=target_sn,
                parent_sn=parent_sn,
                recovery_query=True,
                identity_is_current=lambda: is_current(target_sn),
                force_fresh_report=True,
            )
            fresh_candidates = fresh.get(FIELD_DATA)
            if (
                not isinstance(fresh_candidates, list)
                or len(fresh_candidates) != 1
                or fresh_candidates[0] != candidate
                or listener.current_session_generation(device_id) != generation
                or not is_current(target_sn)
            ):
                msg = f"{family_name} OTA candidate changed during firmware download"
                raise HomeAssistantError(msg)
            report_time = self._ble_ota_versions_observed_at.get(device_id)
            report_generation = self._ble_ota_versions_generation.get(device_id)
            report_fingerprint = self._ble_ota_versions_fingerprint.get(device_id)
            session_key = self.device_bluetooth_key(device_id)
            report = copy.deepcopy(
                (self.data or {}).get(device_id, {}).get(PAYLOAD_BLE_OTA_VERSIONS)
            )
            if report_time is None or session_key is None:
                msg = f"{family_name} OTA version proof is missing after revalidation"
                raise HomeAssistantError(msg)
            await self._async_ensure_ble_ota_safety_observation(
                device_id,
                generation,
                profile,
                lambda: is_current(target_sn),
                force_refresh=True,
            )
            if not is_safe(target_sn):
                if profile == "box_fixed":
                    msg = "Box BLE OTA requires a current fault-free device status"
                elif profile is None:
                    msg = "Home BLE OTA requires a current main-battery reading with charge above 10%"  # ruff: ignore[line-too-long]
                else:
                    msg = "Portable BLE OTA requires a current battery reading with charge above 10%"  # ruff: ignore[line-too-long]
                raise HomeAssistantError(msg)
        if self._ble_ota_pending_blocked(device_id):
            msg = "A BLE firmware transfer is already pending"
            raise HomeAssistantError(msg)
        if session_key is None:
            msg = "BLE OTA session has no Bluetooth key"
            raise HomeAssistantError(msg)
        selected_id = selected_device_id or device_id
        registry_evidence = self._ble_ota_registry_evidence(device_id, selected_id)
        if registry_evidence is None:
            msg = "BLE OTA selected device registry identity is unavailable"
            raise HomeAssistantError(msg)
        marker = {
            "parent_sn": parent_sn,
            "target_sn": target_sn,
            "target_version": target_version,
            "profile": profile or "home",
            "model_evidence": json.dumps(model_evidence, separators=(",", ":")),
            "key_fingerprint": hashlib.sha256(session_key).hexdigest(),
            "selected_device_id": selected_id,
            "registry_evidence": json.dumps(
                registry_evidence, sort_keys=True, separators=(",", ":")
            ),
        }
        if target_kind == "ground_accessory":
            assert ground_identity is not None
            marker["target_kind"] = target_kind
            marker["source_session"] = self._ground_ble_session_fingerprint(
                ground_identity
            )
            owned_marker = dict(marker)
        self._ble_ota_recovered.pop(device_id, None)
        assert self._ble_ota_pending is not None
        self._ble_ota_pending[device_id] = marker
        await async_set_pending(self.hass, self.entry.entry_id, device_id, marker)
        if not is_safe(target_sn):
            msg = "BLE OTA target changed while saving the transfer state"
            raise HomeAssistantError(msg)
        run_transfer = (
            listener.async_run_box_ota
            if profile == "box_fixed"
            else listener.async_run_home_ota
            if profile is None
            else listener.async_run_portable_ota
        )
        result = cast(
            "str",
            await run_transfer(
                device_id,
                target_sn=target_sn,
                firmware=firmware,
                session_generation=generation,
                is_safe=is_safe,
                is_current=is_current,
                timeout_sec=30.0,
            ),
        )
        if result == "success":
            if (
                not is_current(target_sn)
                or self.device_bluetooth_key(device_id) != session_key
                or listener.current_session_generation(device_id) != generation
            ):
                msg = "BLE OTA identity changed before success could be accepted"
                raise HomeAssistantError(msg)
            self._authorize_ota_current_session(
                device_id,
                generation,
                marker,
                identity_is_current,
                directly_completed=True,
            )
            return result
        if result != "reconnect_required":
            msg = f"{family_name} OTA transfer returned an unknown result"
            raise HomeAssistantError(msg)
        if target_kind == "ground_accessory":
            return result
        confirmed = await self._async_confirm_box_ble_ota_after_restart(
            device_id,
            target_sn=target_sn,
            parent_sn=parent_sn,
            previous_generation=generation,
            target_version=candidate.get("targetVersion"),
            identity_is_current=identity_is_current,
            profile=profile,
            model_evidence=model_evidence,
        )
        confirmed_generation = listener.current_session_generation(device_id)
        if confirmed_generation is None:
            msg = "BLE OTA confirmed session was lost"
            raise HomeAssistantError(msg)
        self._authorize_ota_current_session(
            device_id, confirmed_generation, marker, identity_is_current
        )
        return confirmed

    async def _async_confirm_box_ble_ota_after_restart(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        *,
        target_sn: str,
        parent_sn: str,
        previous_generation: int,
        target_version: object,
        identity_is_current: Callable[[], bool],
        profile: str | None = "box_fixed",
        model_evidence: tuple[Any, ...],
    ) -> str:
        """Confirm the selected firmware after the Box reconnects."""
        listener = self._ble_listener
        if (
            listener is None
            or not isinstance(target_version, str)
            or not target_version
        ):
            msg = "Box OTA restart requires a target version and BLE listener"
            raise HomeAssistantError(msg)
        deadline = asyncio.get_running_loop().time() + 120.0
        while True:
            if (
                not identity_is_current()
                or (
                    (current_parent_sn := self._resolve_device_sn(device_id))
                    is not None
                    and current_parent_sn != parent_sn
                )
                or not self._ble_ota_profile_matches(device_id, profile)
                or self._ble_ota_model_evidence(device_id) != model_evidence
            ):
                msg = "Box OTA target changed during restart"
                raise HomeAssistantError(msg)
            generation = listener.current_session_generation(device_id)
            if generation is not None and generation != previous_generation:
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                msg = "Box OTA device did not reconnect"
                raise HomeAssistantError(msg)
            await listener.async_ensure_connected(
                device_id, timeout_sec=min(remaining, 2.0)
            )
            await asyncio.sleep(min(0.25, remaining))
        key = self.device_bluetooth_key(device_id)
        if key is None:
            msg = "Box OTA reconnect has no Bluetooth key"
            raise HomeAssistantError(msg)
        previous_report = self._ble_ota_versions_observed_at.get(device_id)
        query_options = (
            {"family": "home" if profile is None else profile}
            if profile != "box_fixed"
            else {}
        )
        query_sequence = await listener.async_query_box_ota_version(
            device_id,
            session_generation=generation,
            is_safe=lambda: (
                identity_is_current()
                and (
                    (current_parent_sn := self._resolve_device_sn(device_id)) is None
                    or current_parent_sn == parent_sn
                )
                and self._ble_ota_profile_matches(device_id, profile)
                and self._ble_ota_model_evidence(device_id) == model_evidence
                and self.device_bluetooth_key(device_id) == key
            ),
            **query_options,
        )
        while True:
            observed = self._ble_ota_versions_observed_at.get(device_id)
            if (
                observed is not None
                and (previous_report is None or observed > previous_report)
                and self._ble_ota_versions_generation.get(device_id) == generation
                and self._ble_ota_versions_fingerprint.get(device_id)
                == hashlib.sha256(key).digest()
            ):
                first_sequence = self._ble_ota_versions_first_sequence.get(device_id)
                if first_sequence is None or first_sequence <= query_sequence:
                    msg = "Box OTA version report predates the query"
                    raise HomeAssistantError(msg)
                break
            if (
                not identity_is_current()
                or listener.current_session_generation(device_id) != generation
                or self.device_bluetooth_key(device_id) != key
                or not self._ble_ota_profile_matches(device_id, profile)
                or self._ble_ota_model_evidence(device_id) != model_evidence
            ):
                msg = "Box OTA identity changed during version confirmation"
                raise HomeAssistantError(msg)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                msg = "Box OTA version report did not arrive after restart"
                raise HomeAssistantError(msg)
            await asyncio.sleep(min(0.25, remaining))
        result = await self.async_check_ble_ota_versions(
            device_id,
            target_sn=target_sn,
            parent_sn=parent_sn,
            recovery_query=False,
        )
        candidates = result.get(FIELD_DATA)
        confirmed = (
            candidates[0]
            if isinstance(candidates, list) and len(candidates) == 1
            else None
        )
        if (
            not isinstance(confirmed, dict)  # ruff: ignore[too-many-boolean-expressions]
            or not isinstance(confirmed.get(FIELD_UPDATE_STATUS), int)
            or isinstance(confirmed.get(FIELD_UPDATE_STATUS), bool)
            or confirmed.get(FIELD_UPDATE_STATUS) not in {0, _BOX_OTA_VERSION_CURRENT}
            or confirmed.get("currentVersion") != target_version
            or not identity_is_current()
            or listener.current_session_generation(device_id) != generation
            or self.device_bluetooth_key(device_id) != key
            or (
                (current_parent_sn := self._resolve_device_sn(device_id)) is not None
                and current_parent_sn != parent_sn
            )
            or not self._ble_ota_profile_matches(device_id, profile)
            or self._ble_ota_model_evidence(device_id) != model_evidence
        ):
            msg = "Box OTA installed version could not be confirmed"
            raise HomeAssistantError(msg)
        return "success"

    async def async_query_device_ota_version(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query device OTA version with the command family matching the device."""
        box = self.device_ble_profile(device_id) == "box_fixed"
        action_id = (
            248
            if box
            else ACTION_ID_PORTABLE_OTA_VERSION
            if self._is_portable_device_id(device_id)
            else ACTION_ID_GET_DEVICE_OTA_VERSION
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID.get(
                action_id,
                MQTT_CMD_GET_DEVICE_OTA_VERSION,
            ),
            body_fields={},
            ensure_mqtt=ensure_mqtt,
            allow_cloud=not box,
        )

    async def async_notify_device_can_ota(  # ruff: ignore[no-self-use]
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Notify device that OTA update is available (NOTIFY_DEVICE_CAN_OTA, 3007/cmd.

        101).
        """
        msg = "BLE OTA requires a session-bound firmware transfer"
        raise HomeAssistantError(msg)

    async def async_notify_device_ota_total_page(  # ruff: ignore[no-self-use]
        self,
        device_id: str,
        *,
        total_pages: int,
        ensure_mqtt: bool = True,
    ) -> None:
        """Tell device the total OTA page count (NOTIFY_DEVICE_OTA_TOTAL_PAGE, 3008/cmd.

        102).
        """
        msg = "BLE OTA requires a session-bound firmware transfer"
        raise HomeAssistantError(msg)

    async def async_device_get_ota_page_data(  # ruff: ignore[no-self-use]
        self,
        device_id: str,
        *,
        page_index: int,
        ensure_mqtt: bool = True,
    ) -> None:
        """Request OTA firmware page data from device (DEVICE_GET_OTA_PAGE_DATA,.

        3009/cmd 103).
        """
        msg = "BLE OTA requires a session-bound firmware transfer"
        raise HomeAssistantError(msg)

    def _is_portable_device_id(self, device_id: str) -> bool:
        """Return whether discovery or the App model identifies a Portable."""
        data = getattr(self, "data", None)
        payload = data.get(device_id) if isinstance(data, dict) else None
        index = getattr(self, "_device_index", {}).get(device_id) or {}
        metadata = [index.get(PAYLOAD_DEVICE_META)]
        if isinstance(payload, dict):
            metadata.extend(
                payload.get(section)
                for section in (PAYLOAD_DEVICE, PAYLOAD_DISCOVERY, PAYLOAD_DEVICE_META)
            )
        if any(
            isinstance(meta, Mapping)
            and str(meta.get(FIELD_MODEL_CODE, "")).strip()
            in {"2001", "2002", "3001", "3002", "3003", "3004"}
            for meta in metadata
        ):
            return False
        for meta in metadata:
            if isinstance(meta, Mapping) and (
                meta.get(PAYLOAD_DISCOVERY_SOURCE) == DISCOVERY_SOURCE_LEGACY_BIND_LIST
                or portable_ble_profile(meta.get(FIELD_MODEL_CODE)) is not None
                or str(meta.get(FIELD_DEVICE_TYPE, "")).strip().casefold() == "portable"
            ):
                return True
        return False

    async def async_query_weather_plan(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
        allow_ble: bool = True,
        allow_local_mqtt: bool = True,
    ) -> None:
        """Query weather plan."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_WEATHER_PLAN,
            action_id=(
                19
                if self.device_ble_profile(device_id) == "box_fixed"
                else ACTION_ID_QUERY_WEATHER_PLAN
            ),
            cmd=MQTT_CMD_QUERY_WEATHER_PLAN,
            body_fields={},
            ensure_mqtt=ensure_mqtt,
            allow_ble=allow_ble,
            allow_local_mqtt=allow_local_mqtt,
        )

    async def async_query_wifi_config(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query the app Wi-Fi config with the matching command family."""
        action_id = (
            ACTION_ID_PORTABLE_GET_WIFI_CONFIG
            if self._is_portable_device_id(device_id)
            else ACTION_ID_QUERY_WIFI_CONFIG
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_WIFI_CONFIG,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID.get(
                action_id,
                MQTT_CMD_QUERY_WIFI_CONFIG,
            ),
            body_fields={},
            ensure_mqtt=ensure_mqtt,
        )

    # ------------------------------------------------------------------
    # Third-party MQTT bridge (actionId 3046/3047)
    # ------------------------------------------------------------------
    # Per ``HomeCmdAction.smali``: ``SET_THIRD_PARTY_MQTT_CONFIG``
    # (cmd=113 ``ThirdPartMQTTConfig``) and ``GET_THIRD_PARTY_MQTT_CONFIG``
    # (cmd=114 ``QueryThirdPartMQTTConfig``). Body schema from
    # ``ThirdPartyMqttBody.smali``:

    #     {"enable":0|1, "ip":<str>, "port":<int>,
    #      "userName":<str>, "password":<str>, "token":<str>}

    # These methods bypass the REST relay and publish the same app command
    # body to the device over the available write transport.

    # App 2.4.0 ``MqttMsgActivity.initData$lambda$10`` reuses a decoded token
    # when present and otherwise generates nine decimal digits with
    # ``Random.nextInt(10)``. ``HomeDeviceController.t1`` then sends
    # ``userName``/``password``/``token`` through the bb/* codec before
    # publishing. For SolarVault home devices the concrete codec is AES/CBC
    # with the decoded bluetoothKey as key+IV and Base64 ciphertext output.

    def _stable_third_party_mqtt_token(self, token: object) -> tuple[str, bool]:
        """Return a stable App-style token and persist a generated fallback."""
        options = dict(self.entry.options)
        prior_token = self._generated_third_party_mqtt_token or options.get(
            CONF_THIRD_PARTY_MQTT_TOKEN,
        )
        result_token, use_generated, new_generated = stable_third_party_mqtt_token(
            token,
            prior_token,
        )
        if new_generated is not None:
            self._generated_third_party_mqtt_token = new_generated
            if options.get(CONF_THIRD_PARTY_MQTT_TOKEN) != new_generated:
                options[CONF_THIRD_PARTY_MQTT_TOKEN] = new_generated
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    options=options,
                )
            _LOGGER.debug("Jackery: generated stable 9-digit third-party MQTT token")
        return result_token, use_generated

    def _decode_third_party_mqtt_config_body(
        self,
        device_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Decode app-encoded ThirdPartMQTTConfig credential fields."""
        bluetooth_key = self.device_bluetooth_key(device_id)
        if bluetooth_key is None and not self._has_encoded_third_party_mqtt_field(
            body,
        ):
            return dict(body)
        return decode_third_party_mqtt_config_body(
            body,
            bluetooth_key,
        )

    def _store_third_party_mqtt_config_body(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        body: dict[str, Any],
        action_id: int | None,
        *,
        message_type: str | None = None,
        command: int | None = None,
        received_at_monotonic: float | None = None,
        reported_device_sn: str | None = None,
    ) -> dict[str, Any]:
        """Decode a device-reported 3047 config and wake readback waiters."""
        decoded = self._decode_third_party_mqtt_config_body(device_id, body)
        config = copy.deepcopy(decoded)
        config["_raw_body"] = copy.deepcopy(body)
        command = (
            command if command is not None else first_nonblank_int(body.get(FIELD_CMD))
        )
        is_query = action_id == ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG or (
            action_id is None
            and command != MQTT_CMD_THIRD_PARTY_MQTT_CONFIG
            and message_type != MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG
            and (
                command == MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG
                or message_type == MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG
            )
        )
        is_complete_readback = (
            is_query and self._third_party_mqtt_config_readback_complete(config)
        )
        has_usable_token = is_query and self._third_party_mqtt_token_readback_usable(
            config
        )
        fresh_readback = False
        if is_complete_readback or has_usable_token:
            fresh_readback = self._remember_third_party_mqtt_readback(
                device_id, config, received_at_monotonic, reported_device_sn
            )
        if has_usable_token and fresh_readback:
            self._remember_local_mqtt_device_token(
                device_id,
                config.get(FIELD_THIRD_PARTY_MQTT_TOKEN),
            )
        waiter_was_active = False
        if is_complete_readback or has_usable_token:
            waiters = self._third_party_mqtt_config_waiters.get(device_id, [])
            for waiter in tuple(waiters):
                if not waiter.done():
                    waiter_was_active = True
                    if fresh_readback and self._third_party_mqtt_readback_is_fresh(
                        device_id,
                        waiter,
                        received_at_monotonic,
                        reported_device_sn,
                    ):
                        waiter.set_result(dict(config))
        if (
            is_complete_readback
            and fresh_readback
            and not waiter_was_active
            and self._local_mqtt_config_observer is not None
        ):
            self._local_mqtt_config_observer(dict(config))
        return config

    def _remember_third_party_mqtt_readback(
        self,
        device_id: str,
        config: dict[str, Any],
        received_at_monotonic: float | None,
        reported_device_sn: str | None,
    ) -> bool:
        """Accept only locally timed current-owner readbacks for side effects."""
        if (
            isinstance(received_at_monotonic, bool)
            or not isinstance(received_at_monotonic, int | float)
            or not math.isfinite(received_at_monotonic)
            or received_at_monotonic < 0
            or received_at_monotonic > time.monotonic()
        ):
            return False
        device_sn = self._resolve_device_sn(device_id)
        if device_sn is None or (
            reported_device_sn is not None and str(reported_device_sn) != device_sn
        ):
            return False
        receipts = getattr(self, "_third_party_mqtt_readback_receipts", None)
        if receipts is None:
            receipts = self._third_party_mqtt_readback_receipts = {}
        previous_receipt = receipts.get(device_id)
        if (
            previous_receipt is not None
            and previous_receipt[0] == device_sn
            and previous_receipt[1] > received_at_monotonic
        ):
            return False
        receipts[device_id] = (device_sn, received_at_monotonic)
        snapshots = getattr(self, "_third_party_mqtt_readback_snapshots", None)
        if snapshots is None:
            snapshots = self._third_party_mqtt_readback_snapshots = {}
        previous = snapshots.get(device_id)
        if (
            previous is not None
            and previous.device_sn == device_sn
            and previous.received_at > received_at_monotonic
        ):
            return False
        if self._third_party_mqtt_token_readback_usable(config):
            tokens = getattr(self, "_third_party_mqtt_readback_tokens", None)
            if tokens is None:
                tokens = self._third_party_mqtt_readback_tokens = {}
            tokens[device_id] = (
                device_sn,
                str(config[FIELD_THIRD_PARTY_MQTT_TOKEN]),
            )
        if self._third_party_mqtt_config_readback_complete(config):
            snapshots[device_id] = _ThirdPartyMqttReadbackSnapshot(
                received_at=received_at_monotonic,
                device_sn=device_sn,
                config=copy.deepcopy(config),
            )
        return True

    def _third_party_mqtt_readback_is_fresh(
        self,
        device_id: str,
        waiter: asyncio.Future[dict[str, Any]],
        received_at_monotonic: float | None,
        reported_device_sn: str | None,
    ) -> bool:
        """Exclude pre-query ingress and a changed owner from confirmation."""
        contexts = getattr(self, "_third_party_mqtt_readback_contexts", {})
        context = contexts.get(waiter)
        if context is None:
            return False
        if (
            isinstance(received_at_monotonic, bool)
            or not isinstance(received_at_monotonic, int | float)
            or not math.isfinite(received_at_monotonic)
            or received_at_monotonic < context.received_after
            or received_at_monotonic > time.monotonic()
        ):
            return False
        return (
            context.device_sn is not None
            and self._resolve_device_sn(device_id) == context.device_sn
            and (
                reported_device_sn is None
                or str(reported_device_sn) == context.device_sn
            )
        )

    @staticmethod
    def _third_party_mqtt_config_readback_complete(
        config: dict[str, Any],
    ) -> bool:
        """Return whether a 3047 body can confirm all documented fields."""
        required_fields = {
            FIELD_THIRD_PARTY_MQTT_ENABLE,
            FIELD_THIRD_PARTY_MQTT_IP,
            FIELD_THIRD_PARTY_MQTT_PORT,
            FIELD_THIRD_PARTY_MQTT_USERNAME,
            FIELD_THIRD_PARTY_MQTT_PASSWORD,
            FIELD_THIRD_PARTY_MQTT_TOKEN,
        }
        failed_fields = config.get("_decode_failed_fields")
        decode_error = config.get("_decode_error")
        return (
            required_fields <= config.keys()
            and not failed_fields
            and not decode_error
            and bool(str(config.get(FIELD_THIRD_PARTY_MQTT_IP) or "").strip())
            and (safe_int(config.get(FIELD_THIRD_PARTY_MQTT_PORT)) or 0) > 0
            # Anonymous brokers legitimately use blank username/password.
            # The token alone must be non-empty because it is the device-owned
            # value needed before the next 3046 write.
            and bool(str(config.get(FIELD_THIRD_PARTY_MQTT_TOKEN) or ""))
        )

    @staticmethod
    def _third_party_mqtt_token_readback_usable(config: dict[str, Any]) -> bool:
        """Return whether 3047 proved a reusable plaintext device token."""
        failed_fields = set(config.get("_decode_failed_fields") or ())
        return bool(
            not config.get("_decode_error")
            and FIELD_THIRD_PARTY_MQTT_TOKEN not in failed_fields
            and str(config.get(FIELD_THIRD_PARTY_MQTT_TOKEN) or "")
        )

    def set_local_mqtt_config_observer(
        self,
        observer: Callable[[dict[str, Any]], None],
    ) -> None:
        """Register the entry-runtime callback for confirmed 3047 readbacks."""
        self._local_mqtt_config_observer = observer

    def set_device_registry_observer(
        self,
        observer: Callable[[Mapping[str, dict[str, Any]]], None],
    ) -> None:
        """Register a synchronous parent-device hook before listener updates."""
        self._device_registry_observer = observer

    @staticmethod
    def _has_encoded_third_party_mqtt_field(body: dict[str, Any]) -> bool:
        """Return True when credential fields look like app AES/Base64 ciphertext."""
        for key in _THIRD_PARTY_MQTT_CONFIG_KEYS:
            value = body.get(key)
            if not isinstance(value, str) or not value:
                continue
            try:
                decoded = base64.b64decode(value, validate=True)
            except (binascii.Error, ValueError):
                continue
            if decoded and len(decoded) % 16 == 0:
                return True
        return False

    def third_party_mqtt_config_plaintext(self, device_id: str) -> dict[str, Any]:
        """Return plaintext third-party MQTT config for HA entities."""
        device_data: dict[str, Any] | None = None
        snapshot = getattr(self, "_third_party_mqtt_readback_snapshots", {}).get(
            device_id
        )
        if snapshot is not None and snapshot.device_sn == self._resolve_device_sn(
            device_id
        ):
            device_data = {
                PAYLOAD_THIRD_PARTY_MQTT_CONFIG: snapshot.config,
            }
        current = (
            device_data.get(PAYLOAD_THIRD_PARTY_MQTT_CONFIG)
            if isinstance(device_data, dict)
            else None
        )
        token_getter = getattr(self, "_local_mqtt_device_token", None)
        if callable(token_getter):
            # This resolver only accepts decoded device tokens and otherwise
            # creates/persists the app fallback. Do not use raw ciphertext.
            prior_generated = token_getter(device_id)
        else:
            current_token = (
                self._decoded_third_party_mqtt_cached_token(current)
                if isinstance(current, dict)
                and hasattr(self, "_decoded_third_party_mqtt_cached_token")
                else None
            )
            prior_generated, _ = self._stable_third_party_mqtt_token(current_token)
        options = dict(self.entry.options)
        options[CONF_THIRD_PARTY_MQTT_TOKEN] = prior_generated
        config = third_party_mqtt_config_plaintext(
            options,
            prior_generated,
            device_data,
        )
        observed_token = getattr(self, "_third_party_mqtt_readback_tokens", {}).get(
            device_id
        )
        if observed_token is not None and observed_token[0] == self._resolve_device_sn(
            device_id
        ):
            # A later token-only 3047 can supersede the full snapshot's token.
            # Other fields still come from the independently complete snapshot.
            config[FIELD_THIRD_PARTY_MQTT_TOKEN] = observed_token[1]
        return config

    @staticmethod
    def _third_party_mqtt_config_mismatches(
        observed: dict[str, Any],
        expected: dict[str, Any],
    ) -> tuple[str, ...]:
        """Return app config field names whose 3047 readback differs."""
        mismatches: list[str] = []
        for key in (
            FIELD_THIRD_PARTY_MQTT_ENABLE,
            FIELD_THIRD_PARTY_MQTT_IP,
            FIELD_THIRD_PARTY_MQTT_PORT,
            FIELD_THIRD_PARTY_MQTT_USERNAME,
            FIELD_THIRD_PARTY_MQTT_PASSWORD,
            FIELD_THIRD_PARTY_MQTT_TOKEN,
        ):
            if key not in observed:
                mismatches.append(key)
                continue
            if key == FIELD_THIRD_PARTY_MQTT_ENABLE:
                observed_bool = safe_bool(observed.get(key))
                expected_bool = safe_bool(expected.get(key))
                if observed_bool is not expected_bool:
                    mismatches.append(key)
                continue
            if key == FIELD_THIRD_PARTY_MQTT_PORT:
                if safe_int(observed.get(key)) != safe_int(expected.get(key)):
                    mismatches.append(key)
                continue
            if key == FIELD_THIRD_PARTY_MQTT_IP:
                observed_value = str(observed.get(key) or "").strip()
                expected_value = str(expected.get(key) or "").strip()
            else:
                # App 2.4.x preserves decoded credentials verbatim.  Do not
                # let trimming make a device-mutated credential look equal.
                observed_value = str(observed.get(key) or "")
                expected_value = str(expected.get(key) or "")
            if observed_value != expected_value:
                mismatches.append(key)
        return tuple(mismatches)

    async def _async_query_third_party_mqtt_config_readback(
        self,
        device_id: str,
        *,
        timeout_sec: float = _THIRD_PARTY_MQTT_READBACK_ATTEMPT_TIMEOUT_SEC,
    ) -> dict[str, Any] | None:
        """Publish 3047 and wait for one complete device-reported config."""
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        waiters = self._third_party_mqtt_config_waiters.setdefault(device_id, [])
        contexts = getattr(self, "_third_party_mqtt_readback_contexts", None)
        if contexts is None:
            contexts = self._third_party_mqtt_readback_contexts = {}
        readback_context = _ThirdPartyMqttReadbackContext(
            received_after=time.monotonic(),
            device_sn=self._resolve_device_sn(device_id),
        )
        contexts[future] = readback_context
        waiters.append(future)
        try:
            async with asyncio.timeout(timeout_sec):
                await self.async_query_third_party_mqtt_config(device_id)
                readback = await future
                if self._resolve_device_sn(device_id) != readback_context.device_sn:
                    return None
                return readback
        except TimeoutError:
            return None
        finally:
            contexts.pop(future, None)
            current_waiters = self._third_party_mqtt_config_waiters.get(device_id)
            if current_waiters is not None:
                with contextlib.suppress(ValueError):
                    current_waiters.remove(future)
                if not current_waiters:
                    self._third_party_mqtt_config_waiters.pop(device_id, None)

    def _third_party_mqtt_config_payloads(
        self,
        device_id: str,
        options: _ThirdPartyMqttOptions,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Build the encrypted command body and plaintext readback contract."""
        normalized_token, _ = self._stable_third_party_mqtt_token(
            options.get("token", "")
        )
        bluetooth_key = self.device_bluetooth_key(device_id)
        if bluetooth_key is None:
            msg = "Cannot set third-party MQTT config without device bluetoothKey"
            raise HomeAssistantError(msg)

        expected: dict[str, Any] = {
            FIELD_THIRD_PARTY_MQTT_ENABLE: 1 if options["enable"] else 0,
            FIELD_THIRD_PARTY_MQTT_IP: options["ip"],
            FIELD_THIRD_PARTY_MQTT_PORT: options["port"],
            FIELD_THIRD_PARTY_MQTT_USERNAME: options.get("username", ""),
            FIELD_THIRD_PARTY_MQTT_PASSWORD: options.get("password", ""),
            FIELD_THIRD_PARTY_MQTT_TOKEN: normalized_token,
        }
        try:
            command = dict(expected)
            for key in (
                FIELD_THIRD_PARTY_MQTT_USERNAME,
                FIELD_THIRD_PARTY_MQTT_PASSWORD,
                FIELD_THIRD_PARTY_MQTT_TOKEN,
            ):
                command[key] = encode_third_party_mqtt_field(
                    str(expected[key]),
                    bluetooth_key,
                )
        except ValueError as err:
            msg = f"Cannot encode third-party MQTT credentials: {err}"
            raise HomeAssistantError(msg) from err
        return command, expected

    async def _async_confirm_third_party_mqtt_config(
        self,
        device_id: str,
        expected: dict[str, Any],
    ) -> dict[str, Any]:
        """Wait until 3047 confirms the complete plaintext configuration."""
        readback: dict[str, Any] | None = None
        mismatches: tuple[str, ...] = ()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _THIRD_PARTY_MQTT_READBACK_TIMEOUT_SEC
        for attempt in range(_THIRD_PARTY_MQTT_READBACK_ATTEMPTS):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            readback = await self._async_query_third_party_mqtt_config_readback(
                device_id,
                timeout_sec=min(
                    remaining,
                    _THIRD_PARTY_MQTT_READBACK_ATTEMPT_TIMEOUT_SEC,
                ),
            )
            if readback is not None:
                mismatches = self._third_party_mqtt_config_mismatches(
                    readback,
                    expected,
                )
                if not mismatches:
                    return readback
            if attempt + 1 < _THIRD_PARTY_MQTT_READBACK_ATTEMPTS:
                remaining = deadline - loop.time()
                if remaining > 0:
                    await asyncio.sleep(
                        min(_THIRD_PARTY_MQTT_READBACK_RETRY_DELAY_SEC, remaining),
                    )
        if not mismatches:
            msg = (
                "Third-party MQTT config write was not confirmed by "
                f"3047 readback within {_THIRD_PARTY_MQTT_READBACK_TIMEOUT_SEC:.0f}s"
            )
            raise HomeAssistantError(msg)
        msg = (
            "Third-party MQTT config readback did not match written fields: "
            f"{", ".join(mismatches)}"
        )
        raise HomeAssistantError(msg)

    def _third_party_mqtt_transaction_lock(self, device_id: str) -> asyncio.Lock:
        """Serialize each device's complete config read/modify/write cycle."""
        locks: dict[str, asyncio.Lock] | None = getattr(
            self, "_third_party_mqtt_transaction_locks", None
        )
        if locks is None:
            locks = {}
            self._third_party_mqtt_transaction_locks = locks
        return locks.setdefault(device_id, asyncio.Lock())

    async def async_update_third_party_mqtt_config(
        self,
        device_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Merge one entity patch only after the preceding write is confirmed."""
        async with self._third_party_mqtt_transaction_lock(device_id):
            await self._async_update_third_party_mqtt_config_locked(device_id, updates)

    async def _async_update_third_party_mqtt_config_locked(
        self,
        device_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Update one or more ThirdPartMQTTConfig fields via HA entities."""
        config = self.third_party_mqtt_config_plaintext(device_id)
        config.update(updates)
        enabled = safe_bool(config.get(FIELD_THIRD_PARTY_MQTT_ENABLE)) is True
        if enabled and not str(config.get(FIELD_THIRD_PARTY_MQTT_IP) or "").strip():
            msg = "Third-party MQTT host/IP is required"
            raise HomeAssistantError(msg)
        port = safe_int(
            config.get(FIELD_THIRD_PARTY_MQTT_PORT) or DEFAULT_THIRD_PARTY_MQTT_PORT,
        )
        await self._async_set_third_party_mqtt_config_locked(
            device_id,
            enable=enabled,
            ip=str(config.get(FIELD_THIRD_PARTY_MQTT_IP) or "").strip(),
            port=port if port is not None else DEFAULT_THIRD_PARTY_MQTT_PORT,
            username=str(config.get(FIELD_THIRD_PARTY_MQTT_USERNAME) or ""),
            password=str(config.get(FIELD_THIRD_PARTY_MQTT_PASSWORD) or ""),
            token=str(config.get(FIELD_THIRD_PARTY_MQTT_TOKEN) or ""),
        )

    async def async_set_third_party_mqtt_config(
        self,
        device_id: str,
        **options: Unpack[_ThirdPartyMqttOptions],
    ) -> None:
        """Write and confirm a complete bridge configuration without overlap."""
        expected_device_sn = self._resolve_device_sn(device_id)
        if expected_device_sn is None:
            msg = "Cannot set third-party MQTT config without current device serial"
            raise HomeAssistantError(msg)
        async with self._third_party_mqtt_transaction_lock(device_id):
            if self._resolve_device_sn(device_id) != expected_device_sn:
                msg = (
                    "Cannot set third-party MQTT config: device identity changed "
                    "while waiting for the configuration lock"
                )
                raise HomeAssistantError(msg)
            await self._async_set_third_party_mqtt_config_locked(device_id, **options)

    async def _async_set_third_party_mqtt_config_locked(
        self,
        device_id: str,
        **options: Unpack[_ThirdPartyMqttOptions],
    ) -> None:
        """Configure the device's third-party MQTT bridge (experimental).

        Publishes ``SET_THIRD_PARTY_MQTT_CONFIG`` (actionId 3046, cmd 113).
        ``userName``, ``password`` and ``token`` use the App's AES/Base64
        codec; ``enable``, ``ip`` and ``port`` remain plain body fields.
        """
        owner_serial = self._resolve_device_sn(device_id)
        owner_key = self.device_bluetooth_key(device_id)
        parent_epoch = getattr(self, "_http_parent_identity_epochs", {}).get(
            device_id, 0
        )
        system_epoch = getattr(self, "_system_binding_epochs", {}).get(device_id, 0)
        body, expected = self._third_party_mqtt_config_payloads(device_id, options)

        def owner_is_current() -> bool:
            return bool(
                owner_serial
                and owner_key is not None
                and self._resolve_device_sn(device_id) == owner_serial
                and self.device_bluetooth_key(device_id) == owner_key
                and getattr(self, "_http_parent_identity_epochs", {}).get(device_id, 0)
                == parent_epoch
                and getattr(self, "_system_binding_epochs", {}).get(device_id, 0)
                == system_epoch
            )

        if not owner_is_current():
            msg = "Third-party MQTT config requires the current device identity"
            raise HomeAssistantError(msg)

        _LOGGER.info(
            "Jackery: publishing SET_THIRD_PARTY_MQTT_CONFIG (3046) to %s "
            "enable=%s target_configured=%s username_set=%s token_set=%s",
            device_id,
            bool(expected[FIELD_THIRD_PARTY_MQTT_ENABLE]),
            bool(expected[FIELD_THIRD_PARTY_MQTT_IP])
            and int(expected[FIELD_THIRD_PARTY_MQTT_PORT]) > 0,
            bool(expected[FIELD_THIRD_PARTY_MQTT_USERNAME]),
            bool(expected[FIELD_THIRD_PARTY_MQTT_TOKEN]),
        )
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            async with asyncio.timeout(_THIRD_PARTY_MQTT_TRANSACTION_TIMEOUT_SEC):
                # The App selects one transport for this configuration write.
                # Use BLE only when its current session is notification-ready;
                # otherwise use Cloud. Never duplicate or replay a setter after
                # an uncertain outcome. The following 3047 read stays independent.
                listener = getattr(self, "_ble_listener", None)
                ble_ready = (
                    listener is not None
                    and self._ble_writes_enabled()
                    and listener.current_session_generation(device_id) is not None
                )
                uncertain_write: HomeAssistantError | None = None
                try:
                    await self._async_publish_command_ble_first(
                        device_id,
                        message_type=MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
                        action_id=ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
                        cmd=MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
                        body_fields=body,
                        allow_ble=ble_ready,
                        allow_cloud=not ble_ready,
                        allow_local_mqtt=False,
                        wait_for_all_transports=True,
                        identity_guard=owner_is_current,
                    )
                except HomeAssistantError as err:
                    if ble_ready or not isinstance(
                        err.__cause__, MqttPublishOutcomeUnknownError
                    ):
                        raise
                    # The setter may already have reached the device. Never
                    # resend it; a fresh 3047 read can establish actual state.
                    uncertain_write = err
                if not owner_is_current():
                    msg = "Third-party MQTT config device identity changed before readback"  # ruff: ignore[line-too-long]
                    raise HomeAssistantError(msg) from uncertain_write
                try:
                    readback = await self._async_confirm_third_party_mqtt_config(
                        device_id,
                        expected,
                    )
                except HomeAssistantError as err:
                    if uncertain_write is not None:
                        raise err from uncertain_write
                    raise err from BridgeWriteOutcomeUnknownError(
                        "Bridge write was sent but its readback was not confirmed"
                    )
                if not owner_is_current():
                    msg = "Third-party MQTT config device identity changed during readback"  # ruff: ignore[line-too-long]
                    raise HomeAssistantError(msg) from uncertain_write
        except TimeoutError as err:
            msg = (
                "Third-party MQTT config write and confirmation did not complete "
                f"within {_THIRD_PARTY_MQTT_TRANSACTION_TIMEOUT_SEC:.0f}s; "
                "pending command transports were cancelled"
            )
            unknown = BridgeWriteOutcomeUnknownError(msg)
            unknown.__cause__ = err
            raise HomeAssistantError(msg) from unknown
        if self._local_mqtt_config_observer is not None:
            self._local_mqtt_config_observer(dict(readback))
        _LOGGER.info(
            "Jackery: confirmed SET_THIRD_PARTY_MQTT_CONFIG (3046) for %s "
            "via 3047 readback",
            device_id,
        )

    async def async_query_third_party_mqtt_config(
        self,
        device_id: str,
        *,
        allow_ble: bool = True,
        allow_local_mqtt: bool = True,
    ) -> None:
        """Read back the device's third-party MQTT bridge config (experimental).

        Publishes ``GET_THIRD_PARTY_MQTT_CONFIG`` (actionId 3047, cmd 114).
        The response — if any — arrives on the ``device`` topic and is
        captured in the redacted payload-debug log. Inspect
        ``jackery_solarvault_payload_debug.jsonl`` after calling.
        """
        _LOGGER.info(
            "Jackery: publishing GET_THIRD_PARTY_MQTT_CONFIG "
            "(3047) to %s; check payload_debug log for the response",
            device_id,
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
            action_id=ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
            cmd=MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
            body_fields={},
            allow_ble=allow_ble,
            allow_local_mqtt=allow_local_mqtt,
        )

    async def async_send_device_schedule(
        self,
        device_id: str,
        *,
        action_id: int,
        body: dict[str, Any],
        allow_ble: bool = True,
        allow_cloud: bool = True,
    ) -> None:
        """Publish a DownloadDeviceSchedule frame (cmd=112, actionId 3015-3018).

        Empirical schedule body from Frida-PCAP per
        ``docs/Markdown/MQTT_PROTOCOL.md`` §DownloadDeviceSchedule:
        ``{"actionType": int, "taskType": int, "mode": int, "pw": int,
        "sysSwitch": int, "end": "HH:MM", "loops": "1111111", "start":
        "HH:MM", "tid": "<task-id>", "cmd": 112}``. The body is forwarded
        verbatim so callers can match observed wire layouts without the
        integration locking in one interpretation; only ``cmd`` is
        injected (and overwrites any caller-supplied value) so the
        wire-protocol invariant cmd=112 holds.

        ``action_id`` must be one of ACTION_ID_TIMER_TASK_*
        (3015=add, 3016=delete, 3017=update, 3018=read). The caller is
        responsible for picking the right one; the actionType inside
        the body is independent of the action_id selector per the
        captured frame layout.
        """
        if action_id not in {
            ACTION_ID_TIMER_TASK_ADD,
            ACTION_ID_TIMER_TASK_DELETE,
            ACTION_ID_TIMER_TASK_UPDATE,
            ACTION_ID_TIMER_TASK_READ,
        }:
            msg = (
                "action_id must be one of 3015/3016/3017/3018 "
                "(TIMER_TASK_ADD/DELETE/UPDATE/READ); got "
                f"{action_id!r}"
            )
            raise ValueError(
                msg,
            )
        merged_body = dict(body)
        merged_body[FIELD_CMD] = MQTT_CMD_DOWNLOAD_DEVICE_SCHEDULE
        _LOGGER.debug(
            "Jackery: publishing DownloadDeviceSchedule "
            "(actionId=%s) to %s — body keys=%s",
            action_id,
            device_id,
            sorted(merged_body),
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE,
            action_id=action_id,
            cmd=MQTT_CMD_DOWNLOAD_DEVICE_SCHEDULE,
            body_fields=merged_body,
            allow_ble=allow_ble,
            allow_cloud=allow_cloud,
        )

    async def async_read_device_schedule(
        self,
        device_id: str,
        *,
        task_type: int,
        plug_sn: str = "",
        allow_ble: bool = True,
        allow_cloud: bool = True,
    ) -> None:
        """Read an app schedule bucket via ``TIMER_TASK_READ``.

        Smali ``HomeDeviceController`` builds
        ``{"actionType":4,"taskType":<1|2|3>}`` and adds ``deviceSn`` for
        ``SMART_PLUG_TIMER``. ``cmd=112`` is injected by
        ``async_send_device_schedule``.
        """
        task_type_int = int(task_type)
        if task_type_int not in {
            TIMER_TASK_TYPE_SMART_PLUG,
            TIMER_TASK_TYPE_CUSTOM_MODE,
            TIMER_TASK_TYPE_TIME_ELEC,
        }:
            msg = f"Unsupported task_type {task_type!r}"
            raise ValueError(msg)
        body: dict[str, Any] = {
            FIELD_ACTION_TYPE: TIMER_TASK_ACTION_READ,
            FIELD_TASK_TYPE: task_type_int,
        }
        if task_type_int == TIMER_TASK_TYPE_SMART_PLUG:
            body[FIELD_DEVICE_SN] = str(plug_sn)
        await self.async_send_device_schedule(
            device_id,
            action_id=ACTION_ID_TIMER_TASK_READ,
            body=body,
            allow_ble=allow_ble,
            allow_cloud=allow_cloud,
        )

    async def async_query_battery_packs(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
        allow_ble: bool = True,
        allow_local_mqtt: bool = True,
        include_http: bool = False,
    ) -> None:
        """Query battery packs (devType=1, ``READ_SUB_DEVICE_BATTERY_PACK``)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3014,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK},
            ensure_mqtt=ensure_mqtt,
            allow_ble=allow_ble,
            allow_local_mqtt=allow_local_mqtt,
            http_read={"battery_packs": include_http},
        )

    async def async_query_smart_meter(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
        allow_ble: bool = True,
        allow_local_mqtt: bool = True,
        include_http: bool = False,
    ) -> None:
        """Query smart meter / CT (devType=3, ``READ_SUB_DEVICE_CT``)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3031,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_CT},
            ensure_mqtt=ensure_mqtt,
            allow_ble=allow_ble,
            allow_local_mqtt=allow_local_mqtt,
            http_read={
                "subdevice_dev_type": SUBDEVICE_DEV_TYPE_CT if include_http else None
            },
        )

    async def async_query_meter_heads(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
        allow_ble: bool = True,
        allow_local_mqtt: bool = True,
        include_http: bool = False,
    ) -> None:
        """Query meter-head / collector subdevices (devType=4)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3033,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_METER_HEAD},
            ensure_mqtt=ensure_mqtt,
            allow_ble=allow_ble,
            allow_local_mqtt=allow_local_mqtt,
            http_read={
                "subdevice_dev_type": (
                    SUBDEVICE_DEV_TYPE_METER_HEAD if include_http else None
                )
            },
        )

    async def async_query_smart_plugs(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
        allow_ble: bool = True,
        allow_local_mqtt: bool = True,
        include_http: bool = False,
    ) -> None:
        """Query smart plug / socket subdevices.

        Mirrors the Jackery app's ``READ_SUB_DEVICE_SOCKET`` action:
        ``messageType=QuerySubDeviceGroupProperty`` with ``actionId=3032``,
        ``cmd=110`` and ``devType=6`` per ``HomeSubDeviceType.SOCKET``.
        The response arrives as ``UploadSubDeviceGroupProperty`` with a
        ``plugs`` array (see docs/PROTOCOL.md §2 and PROTOCOL.md §3).
        """
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3032,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET},
            ensure_mqtt=ensure_mqtt,
            allow_ble=allow_ble,
            allow_local_mqtt=allow_local_mqtt,
            http_read={
                "subdevice_dev_type": SUBDEVICE_DEV_TYPE_SOCKET
                if include_http
                else None
            },
        )

    async def async_set_smart_plug_switch(
        self,
        device_id: str,
        *,
        plug_sn: str,
        on: bool,
    ) -> None:
        """Toggle a smart plug on or off.

        Mirrors the Jackery app's ``SUB_CONTROL_SOCKET_SWITCH`` (verified
        against ``HomeCmdAction.smali``): ``messageType=ControlSubDevice``,
        ``cmd=111``, ``actionId=3024`` with body ``{devType: 6, deviceSn:
        <plug_sn>, sysSwitch: 0|1}``. The Jackery device echoes the new state
        back in the next ``UploadSubDeviceGroupProperty`` frame for
        ``plugs``.
        """
        requested_at = time.monotonic()
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_SUB_DEVICE,
            action_id=ACTION_ID_CONTROL_SOCKET_SWITCH,
            cmd=MQTT_CMD_CONTROL_SUB_DEVICE,
            body_fields={
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                FIELD_DEVICE_SN: plug_sn,
                FIELD_SYS_SWITCH: 1 if on else 0,
            },
        )
        # Optimistic local update so the entity reflects the new state until
        # next ``UploadSubDeviceGroupProperty`` frame confirms it.
        self._apply_local_smart_plug_switch_patch(
            device_id, plug_sn, on, requested_at=requested_at
        )

    async def async_insert_electricity_strategy(
        self,
        device_id: str,
        body: dict[str, Any],
    ) -> None:
        """Add a new electricity strategy plan."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_INSERT_ELECTRICITY_STRATEGY,
            action_id=(
                13
                if self.device_ble_profile(device_id) == "box_fixed"
                else ACTION_ID_PORTABLE_ADD_CHARGE_PLAN
            ),
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[ACTION_ID_PORTABLE_ADD_CHARGE_PLAN],
            body_fields=body,
        )

    async def async_update_electricity_strategy(
        self,
        device_id: str,
        body: dict[str, Any],
    ) -> None:
        """Update an existing electricity strategy plan."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_UPDATE_ELECTRICITY_STRATEGY,
            action_id=(
                14
                if self.device_ble_profile(device_id) == "box_fixed"
                else ACTION_ID_PORTABLE_UPDATE_CHARGE_PLAN
            ),
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[
                ACTION_ID_PORTABLE_UPDATE_CHARGE_PLAN
            ],
            body_fields=body,
        )

    async def async_delete_electricity_strategy(
        self,
        device_id: str,
        body: dict[str, Any],
    ) -> None:
        """Delete an electricity strategy plan."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DELETE_ELECTRICITY_STRATEGY,
            action_id=(
                15
                if self.device_ble_profile(device_id) == "box_fixed"
                else ACTION_ID_PORTABLE_DELETE_CHARGE_PLAN
            ),
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[
                ACTION_ID_PORTABLE_DELETE_CHARGE_PLAN
            ],
            body_fields=body,
        )

    async def async_query_electricity_strategy(
        self,
        device_id: str,
    ) -> None:
        """Query all electricity strategy plans."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
            action_id=(
                12
                if self.device_ble_profile(device_id) == "box_fixed"
                else ACTION_ID_PORTABLE_GET_CHARGE_PLAN
            ),
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[ACTION_ID_PORTABLE_GET_CHARGE_PLAN],
            body_fields={},
        )

    async def async_set_breaker_switch(
        self,
        device_id: str,
        breaker_id: str,
        on: bool,
    ) -> None:
        """Toggle a circuit breaker on or off.

        Mirrors the app's breaker control logic: ``messageType=ControlSubDevice``,
        ``cmd=111`` with body ``{devType: 7, idx: <breaker_id>, sw: 0|1}``.
        """
        box = self.device_ble_profile(device_id) == "box_fixed"
        await self._async_publish_command_ble_first(
            device_id,
            message_type=(
                MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE
                if box
                else MQTT_MESSAGE_CONTROL_SUB_DEVICE
            ),
            action_id=9 if box else ACTION_ID_CONTROL_SOCKET_SWITCH,
            cmd=12 if box else MQTT_CMD_CONTROL_SUB_DEVICE,
            body_fields={
                **({} if box else {FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BREAKER}),
                FIELD_IDX: int(breaker_id),
                FIELD_SW: 1 if on else 0,
            },
        )
        # Optimistic local update
        self._apply_local_breaker_switch_patch(device_id, breaker_id, on)

    async def async_set_shelly_cloud_switch(
        self,
        device_id: str,
        *,
        shelly_device_id: str,
        on: bool,
    ) -> None:
        """Toggle a Shelly Cloud socket exactly like ``ShellySocketPanelVM``.

        Smali wires ``function="switch"`` and ``action="on"|"off"`` to
        ``wss-cloud/device/shelly/device/control``. This path is separate
        from Jackery ``ControlSubDevice`` because Shelly Cloud sockets are
        cloud-to-cloud accessories, not local Jackery BLE sockets.
        """
        requested_at = time.monotonic()
        await self.api.async_control_shelly_device(
            shelly_device_id,
            action=SHELLY_CONTROL_ACTION_ON if on else SHELLY_CONTROL_ACTION_OFF,
            function=SHELLY_CONTROL_FUNCTION_SWITCH,
            control_allowed=True,
        )
        self._apply_local_smart_plug_switch_patch(
            device_id, shelly_device_id, on, requested_at=requested_at
        )

    async def async_set_smart_plug_priority(
        self,
        device_id: str,
        *,
        plug_sn: str,
        enabled: bool,
    ) -> None:
        """Toggle smart-plug priority for load management.

        Mirrors the Jackery app's ``SUB_CONTROL_SOCKET_PRI_ENABLE``:
        ``messageType=ControlSubDevice``, ``cmd=111``, ``actionId=3025`` with
        body ``{devType: 6, deviceSn: <plug_sn>, socketPri: 0|1}``.
        """
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_SUB_DEVICE,
            action_id=ACTION_ID_CONTROL_SOCKET_PRIORITY,
            cmd=MQTT_CMD_CONTROL_SUB_DEVICE,
            body_fields={
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                FIELD_DEVICE_SN: plug_sn,
                FIELD_SOCKET_PRIORITY: 1 if enabled else 0,
            },
        )
        self._apply_local_smart_plug_patch(
            device_id,
            plug_sn,
            {
                FIELD_SOCKET_PRIORITY: 1 if enabled else 0,
            },
        )

    def _apply_local_smart_plug_switch_patch(
        self,
        device_id: str,
        plug_sn: str,
        on: bool,
        *,
        requested_at: float,
    ) -> None:
        """Preview a successful command only until actual switch feedback arrives."""
        entry = (self.data or {}).get(device_id) or {}
        source_state = getattr(self, "_accessory_source_state", {})
        target = 1 if on else 0
        updates = {FIELD_SYS_SWITCH: target, FIELD_SWITCH_STATE: target}
        for index, plug in enumerate(_dict_list(entry.get(PAYLOAD_SMART_PLUGS)), 1):
            if str(plug_sn) not in self._subdevice_identity_values(plug):
                continue
            identity = subdevice_serial(plug) or f"index_{index}"
            state = source_state.get((device_id, PAYLOAD_SMART_PLUGS, identity))
            for field in tuple(updates):
                feedback = state.provenance.get(field) if state else None
                if (
                    feedback is not None
                    and feedback.received_at_monotonic >= requested_at
                ):
                    del updates[field]
        if updates:
            self._apply_local_smart_plug_patch(device_id, plug_sn, updates)

    def _apply_local_breaker_switch_patch(
        self,
        device_id: str,
        breaker_id: str,
        on: bool,
    ) -> None:
        """Mirror the requested breaker state into ``circuit_property`` immediately."""
        if not self.data or device_id not in self.data:
            return
        payload = dict(self.data[device_id])
        circuits = payload.get(PAYLOAD_CIRCUIT_PROPERTY)
        if not isinstance(circuits, list):
            return
        target = 1 if on else 0
        updated_circuits: list[Any] = []
        touched = False
        for breaker in circuits:
            if isinstance(breaker, dict) and circuit_id(breaker) == breaker_id:
                next_breaker = dict(breaker)
                next_breaker[FIELD_SW] = target
                updated_circuits.append(next_breaker)
                touched = True
            else:
                updated_circuits.append(breaker)
        if touched:
            payload[PAYLOAD_CIRCUIT_PROPERTY] = updated_circuits
            new_data = dict(self.data)
            new_data[device_id] = payload
            self._push_partial_update(new_data)

    def _apply_local_smart_plug_patch(
        self,
        device_id: str,
        plug_sn: str,
        updates: dict[str, Any],
    ) -> None:
        """Mirror requested smart-plug fields into ``smart_plugs`` immediately."""
        if not self.data or device_id not in self.data:
            return
        payload = dict(self.data[device_id])
        plugs = payload.get(PAYLOAD_SMART_PLUGS)
        if not isinstance(plugs, list):
            return
        updated_plugs = []
        touched = False
        for plug in plugs:
            if not isinstance(plug, dict):
                updated_plugs.append(plug)
                continue
            plug_ids = self._subdevice_identity_values(plug)
            if str(plug_sn) in plug_ids:
                next_plug = dict(plug)
                next_plug.update(updates)
                updated_plugs.append(next_plug)
                touched = True
            else:
                updated_plugs.append(plug)
        if touched:
            payload[PAYLOAD_SMART_PLUGS] = updated_plugs
            new_data = dict(self.data)
            new_data[device_id] = payload
            self._push_partial_update(new_data)

    async def async_query_subdevice_combo(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
        allow_ble: bool = True,
        allow_local_mqtt: bool = True,
        include_http: bool = False,
    ) -> None:
        """Query combo subdevice (devType=2, ``READ_SUB_DEVICE_COMBO``)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3037,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_COMBO},
            ensure_mqtt=ensure_mqtt,
            allow_ble=allow_ble,
            allow_local_mqtt=allow_local_mqtt,
            http_read={
                "subdevice_dev_type": SUBDEVICE_DEV_TYPE_COMBO if include_http else None
            },
        )

    async def async_reboot_device(self, device_id: str) -> None:
        """Reboot device."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_REBOOT_DEVICE,
            cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
            body_fields={FIELD_REBOOT: 1},
        )
        self._apply_local_property_patch(device_id, {FIELD_REBOOT: 1})

    async def async_set_ct_phase(self, device_id: str, ct_sn: str, phase: int) -> None:
        """Assign a CT (current transformer) sub-device to a phase (1..4).

        Verified body shape from Frida capture (2026-05-14, app v2.1.1):
        ``{"devType":3,"deviceSn":"<ct-sn>","schePhase":<1..4>,"cmd":111}``.
        ``ct_sn`` is the CT's own MAC/serial (sub-device), not the SolarVault.
        """
        if not ct_sn:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mqtt_missing_subdevice_sn",
                translation_placeholders={"device_id": str(device_id)},
            )
        phase_int = safe_int(phase)
        if phase_int not in {1, 2, 3, 4}:
            msg = f"CT phase must be 1..4 (got {phase_int})"
            raise HomeAssistantError(msg)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_SUB_DEVICE,
            action_id=ACTION_ID_CT_PHASE,
            cmd=MQTT_CMD_CONTROL_SUB_DEVICE,
            body_fields={
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_CT,
                FIELD_DEVICE_SN: ct_sn,
                FIELD_SCHE_PHASE: phase_int,
            },
        )

    # --- Portable / Explorer powerstation commands ---------------------------
    # Portable devices use ``action_id=<portable_msg_id>`` (1-53) and
    # ``cmd=<ble_msg_type>`` with the same
    # ``messageType=DevicePropertyChange`` envelope as home commands but routed
    # through the ``PortableControlFormat`` on the broker.

    async def async_send_portable_command(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        *,
        action_id: int,
        cmd: int,
        body_fields: dict[str, Any],
        message_type: str = MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        http_read: _DocumentedHttpReadOptions | None = None,
    ) -> None:
        """Send an arbitrary portable command via its direct App wire identifiers."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=message_type,
            action_id=action_id,
            cmd=cmd,
            body_fields=body_fields,
            http_read=http_read or {},
        )

    async def async_send_portable_time_zone(
        self,
        device_id: str,
        *,
        timezone_name: str | None = None,
        allow_ble: bool = True,
        allow_cloud: bool = True,
    ) -> None:
        """Sync the Home Assistant time zone via portable SEND_TIME_ZONE."""
        name, body, timestamp = self._time_zone_command_body(timezone_name)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_PORTABLE_SEND_TIME_ZONE,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[ACTION_ID_PORTABLE_SEND_TIME_ZONE],
            body_fields=body,
            ble_extra_body_fields={FIELD_TS: timestamp},
            allow_ble=allow_ble,
            allow_cloud=allow_cloud,
        )
        self._apply_local_system_patch(device_id, {FIELD_TIMEZONE: name})

    def _record_portable_reserve_override_barrier(
        self, device_id: str, field: str
    ) -> None:
        listener = self._ble_listener
        marker = listener.current_notification_marker(device_id) if listener else None
        self._portable_reserve_override_barriers[device_id, field] = (listener, marker)

    async def async_portable_toggle_output(
        self,
        device_id: str,
        *,
        action_id: int,
        field: str,
        enabled: bool,
    ) -> None:
        """Toggle a portable output (DC/DC-USB/DC-CAR/AC/AC240/light/screen).

        Sends ``{field: 1}`` to enable or ``{field: 0}`` to disable via
        ``DevicePropertyChange`` with ``cmd=<ble_msg_type>``.
        """
        priority_model = None
        if (
            action_id in _PORTABLE_PRIORITY_FIELDS
            or field in _ALL_PORTABLE_PRIORITY_FIELDS
        ):
            priority_model = self.device_model_code(device_id)
            if not _portable_priority_body_is_valid(
                action_id,
                PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID.get(action_id, -1),
                priority_model,
                {field: 1 if enabled else 0},
            ):
                msg = "Invalid Portable output priority command or model"
                raise HomeAssistantError(msg)
        reserve_model = (
            self.device_model_code(device_id)
            if action_id == ACTION_ID_PORTABLE_RESERVE_SWITCH
            else None
        )
        if action_id == ACTION_ID_PORTABLE_RESERVE_SWITCH and (
            field != FIELD_SRFC
            or self.device_family(device_id) != "portable"
            or reserve_model not in PORTABLE_RESERVE_MODELS
        ):
            msg = "Portable emergency discharge is not supported for this model"
            raise HomeAssistantError(msg)
        await self.async_send_portable_command(
            device_id,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id],
            body_fields={field: 1 if enabled else 0},
        )
        if (
            priority_model is not None
            and self.device_model_code(device_id) != priority_model
        ):
            msg = "Portable priority command was sent but its model changed"
            raise HomeAssistantError(msg)
        if action_id == ACTION_ID_PORTABLE_RESERVE_SWITCH:
            if self.device_model_code(device_id) != reserve_model:
                msg = "Portable reserve command was sent but its model changed"
                raise HomeAssistantError(msg)
            self._record_portable_reserve_override_barrier(device_id, FIELD_SRFC)
        self._apply_local_property_patch(device_id, {field: 1 if enabled else 0})

    async def async_portable_set_number(
        self,
        device_id: str,
        *,
        action_id: int,
        field: str,
        value: int,
    ) -> None:
        """Set a numeric value on a portable device (charge power, countdown, etc.)."""
        priority_model = None
        if (
            action_id in _PORTABLE_PRIORITY_FIELDS
            or field in _ALL_PORTABLE_PRIORITY_FIELDS
        ):
            priority_model = self.device_model_code(device_id)
            if not _portable_priority_body_is_valid(
                action_id,
                PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID.get(action_id, -1),
                priority_model,
                {field: value},
            ):
                msg = "Invalid Portable output priority command or model"
                raise HomeAssistantError(msg)
        peak_model = (
            self.device_model_code(device_id)
            if action_id == ACTION_ID_PORTABLE_ENERGY_STORAGE_CHARGE_LIMIT
            and field == "bpc"
            else None
        )
        if field == "bpc" and (
            action_id != ACTION_ID_PORTABLE_ENERGY_STORAGE_CHARGE_LIMIT
            or self.device_family(device_id) != "portable"
            or not _portable_peak_body_is_valid(peak_model, {field: value})
        ):
            msg = "Portable peak-through limit is not valid for this model"
            raise HomeAssistantError(msg)
        reserve_model = (
            self.device_model_code(device_id)
            if action_id == ACTION_ID_PORTABLE_RESERVE_THRESHOLD
            else None
        )
        if action_id == ACTION_ID_PORTABLE_RESERVE_THRESHOLD and (
            field != FIELD_SRL
            or self.device_family(device_id) != "portable"
            or reserve_model not in PORTABLE_RESERVE_MODELS
            or not _portable_reserve_body_is_valid(action_id, {field: value})
        ):
            msg = "Portable reserve threshold is not valid for this model"
            raise HomeAssistantError(msg)
        await self.async_send_portable_command(
            device_id,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id],
            body_fields={field: value},
        )
        if peak_model is not None and self.device_model_code(device_id) != peak_model:
            msg = "Portable peak-through command was sent but its model changed"
            raise HomeAssistantError(msg)
        if (
            priority_model is not None
            and self.device_model_code(device_id) != priority_model
        ):
            msg = "Portable priority command was sent but its model changed"
            raise HomeAssistantError(msg)
        if action_id == ACTION_ID_PORTABLE_RESERVE_THRESHOLD:
            if self.device_model_code(device_id) != reserve_model:
                msg = "Portable reserve command was sent but its model changed"
                raise HomeAssistantError(msg)
            self._record_portable_reserve_override_barrier(device_id, FIELD_SRL)
        self._apply_local_property_patch(device_id, {field: value})

    async def async_portable_set_custom_use_battery(
        self,
        device_id: str,
        *,
        discharge_limit: int | None = None,
        charge_limit: int | None = None,
    ) -> None:
        """Set the custom-use battery bounds on a portable device (msgId=33).

        The app's SetBatteryBoundry frame carries the lower (``dl``) and upper
        (``cl``) bounds plus a derived ``bc = cl - CUSTOM_USE_BATTERY_BC_OFFSET``
        back-off together, so a missing side is filled from the last-known
        coordinator state and all three fields ship in a single command.
        """
        if discharge_limit is None and charge_limit is None:
            msg = "Cannot set custom-use battery without a bound"
            raise UpdateFailed(msg)
        current = ((self.data or {}).get(device_id, {}) or {}).get(
            PAYLOAD_PROPERTIES,
            {},
        ) or {}

        def _bound(value: object, default: int) -> int:
            parsed = safe_int(value)
            if parsed is None or parsed < 0 or parsed > _SOC_PERCENT_MAX:
                return default
            return parsed

        lower = (
            _bound(discharge_limit, 0)
            if discharge_limit is not None
            else _bound(current.get("dl"), 0)
        )
        upper = (
            _bound(charge_limit, 100)
            if charge_limit is not None
            else _bound(current.get("cl"), 100)
        )
        body = {
            "dl": lower,
            "cl": upper,
            "bc": upper - CUSTOM_USE_BATTERY_BC_OFFSET,
        }
        await self.async_send_portable_command(
            device_id,
            action_id=ACTION_ID_PORTABLE_CUSTOM_USE_BATTERY,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[
                ACTION_ID_PORTABLE_CUSTOM_USE_BATTERY
            ],
            body_fields=body,
        )
        self._apply_local_property_patch(device_id, body)

    async def async_set_box_working_mode(self, device_id: str, value: int) -> None:
        """Send an App-defined Box mode; state follows subsequent device reports."""
        model = self.device_model_code(device_id)
        body = {"en": value}
        if (
            self.device_family(device_id) != "box"
            or model not in _BOX_WORKING_MODE_MODELS
            or not _box_app_write_is_valid(_BOX_WORKING_MODE_PAIR, body)
        ):
            msg = "Invalid Box working mode or model"
            raise HomeAssistantError(msg)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=_BOX_WORKING_MODE_PAIR[0],
            cmd=_BOX_WORKING_MODE_PAIR[1],
            body_fields=body,
        )
        if self.device_model_code(device_id) != model:
            msg = "Box working mode was sent but its model changed"
            raise HomeAssistantError(msg)

    async def async_set_box_charge_now(self, device_id: str, enabled: bool) -> None:
        """Send the App Box charge-now action using fresh safety evidence."""
        identity = self._box_safety_device_identity(device_id)
        if (
            not isinstance(enabled, bool)
            or identity is None
            or not self.box_charge_now_allowed(device_id, enabled)
        ):
            msg = "Box charge-now command lacks fresh safety evidence"
            raise HomeAssistantError(msg)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=_BOX_CHARGE_NOW_PAIR[0],
            cmd=_BOX_CHARGE_NOW_PAIR[1],
            body_fields={"rc": int(enabled)},
        )
        if self._box_safety_device_identity(device_id) != identity:
            msg = "Box charge-now command was sent but its device identity changed"
            raise HomeAssistantError(msg)

    async def async_portable_set_select(
        self,
        device_id: str,
        *,
        action_id: int,
        field: str,
        value: int,
        local_patch: dict[str, Any] | None = None,
    ) -> None:
        """Set a select value on a portable device (charge mode, power mode, etc.)."""
        mode_model = (
            self.device_model_code(device_id)
            if action_id
            in {
                ACTION_ID_PORTABLE_ENERGY_SAVING,
                ACTION_ID_PORTABLE_USE_POWER_MODE,
                ACTION_ID_PORTABLE_LIGHT,
                ACTION_ID_PORTABLE_UPS_MODEL,
                ACTION_ID_PORTABLE_AC_OUTPUT_MODE,
            }
            else None
        )
        mode_identity = (
            self.device_ups_identity(device_id)
            if action_id == ACTION_ID_PORTABLE_UPS_MODEL and mode_model in {13, 22}
            else None
        )
        if action_id == ACTION_ID_PORTABLE_LIGHT and not _portable_light_body_is_valid(
            mode_model, {field: value}
        ):
            msg = "Invalid Portable light command or model"
            raise HomeAssistantError(msg)
        if action_id in _PORTABLE_UPS_FIELDS and not _portable_ups_body_is_valid(
            action_id,
            PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id],
            mode_model,
            {field: value},
            mode_identity[0] if mode_identity is not None else None,
        ):
            msg = "Invalid Portable UPS command or model"
            raise HomeAssistantError(msg)
        if action_id in {
            ACTION_ID_PORTABLE_ENERGY_SAVING,
            ACTION_ID_PORTABLE_USE_POWER_MODE,
        } and not _portable_mode_body_is_valid(action_id, mode_model, {field: value}):
            msg = "Invalid Portable mode command body or model"
            raise HomeAssistantError(msg)
        await self.async_send_portable_command(
            device_id,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id],
            body_fields={field: value},
        )
        if mode_model is not None and self.device_model_code(device_id) != mode_model:
            msg = "Portable mode command was sent but its model changed"
            raise HomeAssistantError(msg)
        if (
            mode_identity is not None
            and self.device_ups_identity(device_id) != mode_identity
        ):
            msg = "Portable UPS command was sent but its device identity changed"
            raise HomeAssistantError(msg)
        self._apply_local_property_patch(
            device_id, local_patch if local_patch is not None else {field: value}
        )
