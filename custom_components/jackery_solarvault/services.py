"""Service-action setup for the Jackery SolarVault integration.

The handlers live here (instead of inline in __init__.py) so the global
async_setup stays focused on bootstrap and so the per-action routing logic
is easy to unit-test in isolation.

The actions follow the same routing contract:

1. Resolve the target coordinator by looking up the requested system or
   device id inside ``coordinator.data``. This makes multi-account setups
   correct: a service call with a device-id from account A is dispatched
   to account A's coordinator instead of being attempted blindly against
   the first-loaded account.
2. Forward the request to the coordinator (for rename_system this reaches
   its API client through ``coordinator.async_set_system_name``, which is a
   system-scoped REST call); command/API execution stays in
   ``coordinator.py``.
3. Surface invalid input as ``ServiceValidationError`` and external action
   failures as ``HomeAssistantError`` with localized integration messages.
"""

import base64
from collections.abc import Callable, Coroutine, Mapping
import io
import json
import logging
import math
import re
from typing import TYPE_CHECKING, Any, Final, NamedTuple, cast

import segno
import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .client import JackeryAuthError, JackeryError
from .client.credentials import (
    MAX_PASSWORD_LENGTH,
    MAX_TOKEN_LENGTH,
    MAX_USERNAME_LENGTH,
    credential_text,
    redacted_error,
)
from .const import (
    APP_PERIOD_DATE_TYPES,
    APP_VERSION_QUERY_NAME,
    APP_VERSION_QUERY_TYPE,
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    DOMAIN,
    FAULT_LOG_MAX_PAGE_NUM,
    FAULT_LOG_MAX_PAGE_SIZE,
    FIELD_DEVICE_SN,
    FIELD_DEV_SN,
    FIELD_ID,
    FIELD_QR_CODE_ID,
    FIELD_SYSTEM_ID,
    FIELD_USER_ID,
    MQTT_ACTION_IDS_SCHEDULE,
    PAYLOAD_BLE_OTA_VERSIONS,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_META,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_SYSTEM,
    PORTABLE_PEAK_THROUGH_MODELS,
    PORTABLE_TOU_QUERY_MODELS,
    SERVICE_ACCEPT_SHARED_DEVICE,
    SERVICE_AGREE_PRIVACY_CONSENT,
    SERVICE_BIND_CURRENCY,
    SERVICE_BIND_DEVICE,
    SERVICE_BIND_SMART_PART,
    SERVICE_CANCEL_DYNAMIC_PRICE_CONTRACT_AUTH,
    SERVICE_CHECK_ACCESSORIES_EXIST,
    SERVICE_CHECK_APP_VERSION,
    SERVICE_CHECK_BLE_OTA_VERSIONS,
    SERVICE_CHECK_JACKERY_ACCESSORIES_EXIST,
    SERVICE_CHECK_PRIVACY_UPDATE,
    SERVICE_CHECK_SYSTEM_BOUND,
    SERVICE_DELETE_ELECTRICITY_STRATEGY,
    SERVICE_DELETE_STORM_ALERT,
    SERVICE_FIELD_ACCESSORY_ID,
    SERVICE_FIELD_ACCESSORY_SN,
    SERVICE_FIELD_ACCOUNT_NICKNAME,
    SERVICE_FIELD_ACK_TIMEOUT,
    SERVICE_FIELD_ACTION_ID,
    SERVICE_FIELD_AC_PORT,
    SERVICE_FIELD_ALARM_KEY,
    SERVICE_FIELD_ALERT_ID,
    SERVICE_FIELD_BEGIN_DATE,
    SERVICE_FIELD_BINDING_ID,
    SERVICE_FIELD_BIND_IDS,
    SERVICE_FIELD_BIND_KEY,
    SERVICE_FIELD_BIND_USER_ID,
    SERVICE_FIELD_BODY,
    SERVICE_FIELD_CMD,
    SERVICE_FIELD_CONNECT_TOKEN,
    SERVICE_FIELD_CONTACT_INFO,
    SERVICE_FIELD_CONTENT,
    SERVICE_FIELD_CONTRACT_ID,
    SERVICE_FIELD_COUNTRY,
    SERVICE_FIELD_CURRENCY,
    SERVICE_FIELD_CURRENT_TIME,
    SERVICE_FIELD_CUSTOMER_NUMBER,
    SERVICE_FIELD_CUSTOM_ID,
    SERVICE_FIELD_DATE_TYPE,
    SERVICE_FIELD_DEVICES,
    SERVICE_FIELD_DEVICE_ID,
    SERVICE_FIELD_DEVICE_SN,
    SERVICE_FIELD_DEVICE_SN_INFOS,
    SERVICE_FIELD_ENABLE,
    SERVICE_FIELD_END_DATE,
    SERVICE_FIELD_FLAGS,
    SERVICE_FIELD_GUID,
    SERVICE_FIELD_ID,
    SERVICE_FIELD_IP,
    SERVICE_FIELD_KEY,
    SERVICE_FIELD_LATITUDE,
    SERVICE_FIELD_LEVEL,
    SERVICE_FIELD_LONGITUDE,
    SERVICE_FIELD_NEW_NAME,
    SERVICE_FIELD_NICKNAME,
    SERVICE_FIELD_PAGE_INDEX,
    SERVICE_FIELD_PAGE_NO,
    SERVICE_FIELD_PAGE_SIZE,
    SERVICE_FIELD_PASSWORD,
    SERVICE_FIELD_PENDING_AGREE_VERSION_IDS,
    SERVICE_FIELD_PLATFORM_COMPANY_ID,
    SERVICE_FIELD_PORT,
    SERVICE_FIELD_QR_CODE_ID,
    SERVICE_FIELD_SET,
    SERVICE_FIELD_SHELLY_DEVICE_ID,
    SERVICE_FIELD_STATE,
    SERVICE_FIELD_SYSTEM_ID,
    SERVICE_FIELD_TARGET_DEV_ID,
    SERVICE_FIELD_TIMEZONE_OFFSET,
    SERVICE_FIELD_TOKEN,
    SERVICE_FIELD_TYPE,
    SERVICE_FIELD_USERNAME,
    SERVICE_FIELD_VERSION_NAME,
    SERVICE_FIELD_WAIT_FOR_ACK,
    SERVICE_FIELD_ZONE_ID,
    SERVICE_GET_ALARM_DETAIL,
    SERVICE_GET_BATTERY_PASSPORT,
    SERVICE_GET_DEVICE_CURRENCY,
    SERVICE_GET_DYNAMIC_PRICE_LOGIN_URL,
    SERVICE_GET_FAULT_LOG,
    SERVICE_GET_PRODUCT_INSTRUCTION,
    SERVICE_GET_PUSH_CONFIG,
    SERVICE_GET_SHARE_QR_CODE,
    SERVICE_GET_SHELLY_AUTH_URL,
    SERVICE_GET_SMART_SCHEDULE_PREDICTION,
    SERVICE_GET_UNREAD_COUNT,
    SERVICE_GET_USER_INFO,
    SERVICE_INSERT_ELECTRICITY_STRATEGY,
    SERVICE_LIST_ACCESSORIES,
    SERVICE_LIST_BANNERS,
    SERVICE_LIST_COUNTRY_ZONES,
    SERVICE_LIST_CURRENCIES,
    SERVICE_LIST_DYNAMIC_PRICE_CONTRACTS,
    SERVICE_LIST_FAQS,
    SERVICE_LIST_FAQ_ANSWERS,
    SERVICE_LIST_GRID_STANDARDS,
    SERVICE_LIST_NOTIFICATIONS,
    SERVICE_LIST_SHARED_DEVICES,
    SERVICE_LIST_SHARED_MANAGERS,
    SERVICE_LIST_SHELLY_BINDING_FAILURES,
    SERVICE_LIST_SHELLY_DEVICES,
    SERVICE_NON_EMPTY_TEXT_PATTERN,
    SERVICE_NUMERIC_ID_PATTERN,
    SERVICE_QUERY_BOX_STAT,
    SERVICE_QUERY_CARBON_STAT,
    SERVICE_QUERY_CHARGE_REPORT,
    SERVICE_QUERY_CUTOFF_STAT,
    SERVICE_QUERY_ELECTRICITY_STRATEGY,
    SERVICE_QUERY_PROFIT_STAT,
    SERVICE_QUERY_SOCKET_STAT,
    SERVICE_QUERY_SOC_STAT,
    SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    SERVICE_QUERY_TOU_PLAN,
    SERVICE_REFRESH_SUBDEVICES,
    SERVICE_REFRESH_WEATHER_PLAN,
    SERVICE_REMOVE_ALL_SHARED_ACCESS,
    SERVICE_REMOVE_SHARED_ACCESS,
    SERVICE_RENAME_SYSTEM,
    SERVICE_REPORT_DEVICE_TIMEZONE,
    SERVICE_RESPONSE_QR_CODE_ID,
    SERVICE_RESPONSE_USER_ID,
    SERVICE_SAVE_DYNAMIC_PRICE_CONTRACT_AUTH,
    SERVICE_SAVE_DYNAMIC_PRICE_LOCATION_ID,
    SERVICE_SAVE_TOU_PLAN,
    SERVICE_SEND_BLE_COMMAND,
    SERVICE_SEND_DEVICE_SCHEDULE,
    SERVICE_SET_ACCESSORY_NAME,
    SERVICE_SET_ACCOUNT_NICKNAME,
    SERVICE_SET_AC_NICKNAME,
    SERVICE_SET_DEVICE_NICKNAME,
    SERVICE_SET_PUSH_CONFIG,
    SERVICE_SET_STORM_ALERT_LOCATION,
    SERVICE_SET_THIRD_PARTY_MQTT_CONFIG,
    SERVICE_START_BLE_OTA_UPDATE,
    SERVICE_SUBMIT_FEEDBACK,
    SERVICE_SYNC_ALERTS,
    SERVICE_SYNC_OFFLINE_STATISTICS,
    SERVICE_UNBIND_ACCESSORIES,
    SERVICE_UNBIND_DEVICE,
    SERVICE_UNBIND_SHELLY_ACCOUNT,
    SERVICE_UNBIND_SHELLY_DEVICE,
    SERVICE_UNBIND_SMART_PART,
    SERVICE_UPDATE_ELECTRICITY_STRATEGY,
    _BLE_SERVICE_CONNECT_TIMEOUT_SEC,
)
from .coordinator import JackerySolarVaultCoordinator
from .schema_compat import Schema
from .util import (
    payload_has_home_payload_evidence as _payload_has_home_payload_evidence,
    safe_bool,
    safe_float,
    stable_subdevice_key,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
    from homeassistant.util.json import JsonValueType

_LOGGER = logging.getLogger(__name__)
_MAX_SYSTEM_NAME_LENGTH: Final = 64


def _coerce_service_int(raw: object) -> int:
    """Return a whole service integer without truncating fractional numbers."""
    if isinstance(raw, bool):
        msg = "expected integer"
        raise vol.Invalid(msg)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if math.isfinite(raw) and raw.is_integer():
            return int(raw)
        msg = "expected integer"
        raise vol.Invalid(msg)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            msg = "expected integer"
            raise vol.Invalid(msg)
        signed = text[0] in "+-"
        digits = text[1:] if signed else text
        if digits.isascii() and digits.isdecimal():
            try:
                return int(text)
            except ValueError as err:
                msg = "expected integer"
                raise vol.Invalid(msg) from err
    msg = "expected integer"
    raise vol.Invalid(msg)


def _coerce_service_float(raw: object) -> float:
    """Return a finite service float without accepting booleans."""
    if isinstance(raw, bool):
        msg = "expected finite number"
        raise vol.Invalid(msg)
    if not isinstance(raw, str | int | float):
        msg = "expected finite number"
        raise vol.Invalid(msg)
    try:
        parsed = float(raw)
    except (TypeError, ValueError, OverflowError) as err:
        msg = "expected finite number"
        raise vol.Invalid(msg) from err
    if not math.isfinite(parsed):
        msg = "expected finite number"
        raise vol.Invalid(msg)
    return parsed


# ---------------------------------------------------------------------------
# Schemas — kept here next to the Home Assistant service dispatchers that
# consume them. Numeric-id pattern accepts surrounding whitespace so service
# callers from automations may include indentation; dispatchers strip before
# forwarding to the coordinator.
# ---------------------------------------------------------------------------

RENAME_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_SYSTEM_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NUMERIC_ID_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_NEW_NAME): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=64),
    ),
})
REFRESH_WEATHER_PLAN_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
REFRESH_SUBDEVICES_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
DELETE_STORM_ALERT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_ALERT_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
SET_STORM_ALERT_LOCATION_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_LATITUDE): vol.All(
        vol.Coerce(float),
        vol.Range(min=-90, max=90),
    ),
    vol.Required(SERVICE_FIELD_LONGITUDE): vol.All(
        vol.Coerce(float),
        vol.Range(min=-180, max=180),
    ),
})
# Experimental third-party MQTT bridge — see implementation notes §15. ``username``/
# ``password`` accept any printable text; the device firmware is expected to
# AES-256-CBC-encrypt them itself if the cloud relay forbids plaintext, but
# until that path is verified the integration sends them as-is and lets the
# user verify reception against their own broker.
SET_THIRD_PARTY_MQTT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_ENABLE): cv.boolean,
    vol.Required(SERVICE_FIELD_IP): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_PORT): vol.All(
        vol.Coerce(int),
        vol.Range(min=1, max=65535),
    ),
    vol.Optional(SERVICE_FIELD_USERNAME, default=""): vol.All(
        lambda value: credential_text(
            value, field="username", max_length=MAX_USERNAME_LENGTH
        ),
    ),
    vol.Optional(SERVICE_FIELD_PASSWORD, default=""): vol.All(
        lambda value: credential_text(
            value, field="password", max_length=MAX_PASSWORD_LENGTH
        ),
    ),
    vol.Optional(SERVICE_FIELD_TOKEN, default=""): vol.All(
        lambda value: credential_text(
            value, field="token", max_length=MAX_TOKEN_LENGTH
        ),
    ),
})
QUERY_THIRD_PARTY_MQTT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
CHECK_BLE_OTA_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Optional("target", default="device"): vol.In(("device", "ground_accessory")),
})
SEND_BLE_COMMAND_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_CMD): vol.All(
        vol.Coerce(int),
        vol.Range(min=1, max=65535),
    ),
    vol.Required(SERVICE_FIELD_BODY): vol.Any(dict, cv.string),
    vol.Optional(SERVICE_FIELD_FLAGS, default=0): vol.All(
        vol.Coerce(int),
        vol.Range(min=0, max=65535),
    ),
    vol.Optional(SERVICE_FIELD_WAIT_FOR_ACK, default=False): cv.boolean,
    vol.Optional(SERVICE_FIELD_ACK_TIMEOUT, default=5.0): vol.All(
        vol.Coerce(float),
        vol.Range(min=0.5, max=60.0),
    ),
})
SEND_DEVICE_SCHEDULE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
    vol.Required(SERVICE_FIELD_ACTION_ID): vol.All(
        vol.Coerce(int),
        vol.In(MQTT_ACTION_IDS_SCHEDULE),
    ),
    vol.Required(SERVICE_FIELD_BODY): vol.Any(dict, cv.string),
})
QUERY_TOU_PLAN_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
})
SAVE_TOU_PLAN_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
    vol.Required(SERVICE_FIELD_BODY): vol.Any(list, dict, cv.string),
})
CURRENCY_READ_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
BIND_CURRENCY_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_CURRENCY): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=32),
    ),
})
LIST_ACCESSORIES_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
CHECK_ACCESSORIES_EXIST_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_DEVICES): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=4096),
    ),
})
CHECK_JACKERY_ACCESSORIES_EXIST_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_DEVICE_SN_INFOS): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=4096),
    ),
})
SET_ACCESSORY_NAME_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_ACCESSORY_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_NICKNAME): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=64),
    ),
})
DYNAMIC_PRICE_LOGIN_URL_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_PLATFORM_COMPANY_ID): vol.All(
        _coerce_service_int,
        vol.Range(min=0, max=2147483647),
    ),
})
DYNAMIC_PRICE_CONTRACT_LIST_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_CUSTOMER_NUMBER): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_PLATFORM_COMPANY_ID): vol.All(
        _coerce_service_int,
        vol.Range(min=0, max=2147483647),
    ),
})
DYNAMIC_PRICE_CONTRACT_AUTH_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_CONTRACT_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_CUSTOM_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_PLATFORM_COMPANY_ID): vol.All(
        _coerce_service_int,
        vol.Range(min=0, max=2147483647),
    ),
})
DYNAMIC_PRICE_CANCEL_CONTRACT_AUTH_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_PLATFORM_COMPANY_ID): vol.All(
        _coerce_service_int,
        vol.Range(min=0, max=2147483647),
    ),
})
DYNAMIC_PRICE_LOCATION_ID_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_CONNECT_TOKEN): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=4096),
    ),
})
QUERY_SOCKET_STAT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_TARGET_DEV_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_DATE_TYPE): vol.In(APP_PERIOD_DATE_TYPES),
    vol.Required(SERVICE_FIELD_BEGIN_DATE): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=32),
    ),
    vol.Required(SERVICE_FIELD_END_DATE): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=32),
    ),
})
LIST_COUNTRY_ZONES_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
LIST_GRID_STANDARDS_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_COUNTRY): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=16),
    ),
})
CHECK_SYSTEM_BOUND_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_BIND_KEY): vol.All(
        # bind_key is a string end-to-end: the handler parses it with
        # _service_required_text and api.async_check_system_bound sends it
        # verbatim as the ``bindKey`` query param (str). The direct-call
        # schema already uses cv.string; keep this registered schema aligned.
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_DEVICE_SN): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_GUID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
})
GET_ALARM_DETAIL_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_ALARM_KEY): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=256),
    ),
})
LIST_NOTIFICATIONS_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Optional(SERVICE_FIELD_CURRENT_TIME, default=0): vol.All(
        _coerce_service_int,
        vol.Range(min=0, max=9999999999999),
    ),
    vol.Optional(SERVICE_FIELD_DEVICE_SN, default=""): vol.All(
        cv.string,
        vol.Length(max=128),
    ),
    vol.Optional(SERVICE_FIELD_PAGE_NO, default=1): vol.All(
        _coerce_service_int,
        vol.Range(min=1, max=10000),
    ),
    vol.Optional(SERVICE_FIELD_PAGE_SIZE, default=20): vol.All(
        _coerce_service_int,
        vol.Range(min=1, max=100),
    ),
})
ACCOUNT_READ_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
GET_BATTERY_PASSPORT_SCHEMA = ACCOUNT_READ_SCHEMA
GET_FAULT_LOG_SCHEMA = ACCOUNT_READ_SCHEMA.extend({
    vol.Optional(SERVICE_FIELD_PAGE_NO, default=1): vol.All(
        _coerce_service_int,
        vol.Range(min=1, max=FAULT_LOG_MAX_PAGE_NUM),
    ),
    vol.Optional(SERVICE_FIELD_PAGE_SIZE, default=20): vol.All(
        _coerce_service_int,
        vol.Range(min=1, max=FAULT_LOG_MAX_PAGE_SIZE),
    ),
})
SYNC_OFFLINE_STATISTICS_SCHEMA = ACCOUNT_READ_SCHEMA.extend({
    vol.Required(SERVICE_FIELD_BODY): vol.All(dict, vol.Length(min=1)),
})
CHECK_APP_VERSION_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Optional(SERVICE_FIELD_TYPE, default=APP_VERSION_QUERY_TYPE): vol.All(
        cv.string,
        vol.Length(max=64),
    ),
    vol.Optional(SERVICE_FIELD_VERSION_NAME, default=APP_VERSION_QUERY_NAME): vol.All(
        cv.string,
        vol.Length(max=64),
    ),
})
SET_PUSH_CONFIG_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_SET): vol.All(
        _coerce_service_int,
        vol.Range(min=-2147483648, max=2147483647),
    ),
})
SYNC_ALERTS_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_CONTENT): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=65535),
    ),
    vol.Required(SERVICE_FIELD_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
})
QUERY_CHARGE_REPORT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_DEVICE_SN): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Optional(SERVICE_FIELD_PAGE_INDEX, default=1): vol.All(
        _coerce_service_int,
        vol.Range(min=1, max=10000),
    ),
})
QUERY_CUTOFF_STAT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_DEVICE_SN): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_BEGIN_DATE): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=32),
    ),
    vol.Required(SERVICE_FIELD_END_DATE): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=32),
    ),
})
QUERY_SOC_STAT_SCHEMA = ACCOUNT_READ_SCHEMA
QUERY_CARBON_STAT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_DEVICE_SN): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
})
QUERY_PROFIT_STAT_SCHEMA = ACCOUNT_READ_SCHEMA
QUERY_BOX_STAT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_DEVICE_SN): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_DATE_TYPE): vol.In(APP_PERIOD_DATE_TYPES),
    vol.Required(SERVICE_FIELD_BEGIN_DATE): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=32),
    ),
    vol.Required(SERVICE_FIELD_END_DATE): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=32),
    ),
    vol.Optional(SERVICE_FIELD_KEY, default=""): vol.All(
        cv.string,
        vol.Length(max=128),
    ),
})
SUBMIT_FEEDBACK_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_CONTACT_INFO): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=256),
    ),
    vol.Required(SERVICE_FIELD_CONTENT): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=65535),
    ),
    vol.Optional(SERVICE_FIELD_DEVICE_SN, default=""): vol.All(
        cv.string,
        vol.Length(max=128),
    ),
})
AGREE_PRIVACY_CONSENT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_PENDING_AGREE_VERSION_IDS): vol.All(
        cv.ensure_list,
        [vol.All(vol.Coerce(int), vol.Range(min=0))],
        vol.Length(min=1, max=100),
    ),
})
GET_PRODUCT_INSTRUCTION_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_DEVICE_SN): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Optional(SERVICE_FIELD_TYPE, default=""): vol.All(
        cv.string,
        vol.Length(max=64),
    ),
})
ELECTRICITY_STRATEGY_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
    vol.Required(SERVICE_FIELD_BODY): vol.Any(dict, cv.string),
})
QUERY_ELECTRICITY_STRATEGY_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
})
SET_DEVICE_NICKNAME_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_NICKNAME): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=64),
    ),
})
SET_ACCOUNT_NICKNAME_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_ACCOUNT_NICKNAME): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=64),
    ),
})
BIND_DEVICE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_TARGET_DEV_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_BIND_KEY): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_GUID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_TIMEZONE_OFFSET): vol.All(
        _coerce_service_int,
        vol.Range(min=-86400, max=86400),
    ),
})
UNBIND_DEVICE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
BIND_SMART_PART_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_ACCESSORY_SN): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=64),
    ),
})
UNBIND_SMART_PART_SCHEMA = BIND_SMART_PART_SCHEMA
BIND_SMART_PART_SCHEMA = BIND_SMART_PART_SCHEMA.extend({vol.Required("smart"): dict})
LIST_SHARED_DEVICES_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
GET_SHARE_QR_CODE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
GET_SMART_SCHEDULE_PREDICTION_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
UNBIND_ACCESSORIES_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_BIND_IDS): vol.All(
        cv.ensure_list,
        [
            vol.All(
                cv.string,
                vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
                vol.Length(max=128),
            )
        ],
        vol.Length(min=1, max=32),
    ),
})
SET_AC_NICKNAME_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_AC_PORT): vol.All(vol.Coerce(int), vol.In((1, 2))),
    vol.Required(SERVICE_FIELD_NICKNAME): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=64),
    ),
})
REPORT_DEVICE_TIMEZONE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_ZONE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=64),
    ),
    vol.Required(SERVICE_FIELD_TIMEZONE_OFFSET): vol.Coerce(int),
})
ACCEPT_SHARED_DEVICE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_TARGET_DEV_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_QR_CODE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=256),
    ),
})
LIST_SHARED_MANAGERS_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_BIND_USER_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Optional(SERVICE_FIELD_LEVEL, default=0): vol.All(
        vol.Coerce(int),
        vol.Range(min=0, max=65535),
    ),
})
REMOVE_SHARED_ACCESS_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Optional(SERVICE_FIELD_TARGET_DEV_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_BIND_USER_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
})
REMOVE_ALL_SHARED_ACCESS_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_BIND_USER_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Optional(SERVICE_FIELD_LEVEL, default=0): vol.All(
        vol.Coerce(int),
        vol.Range(min=0, max=65535),
    ),
})
UNBIND_SHELLY_DEVICE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Required(SERVICE_FIELD_BINDING_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
    vol.Required(SERVICE_FIELD_SHELLY_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=128),
    ),
})
UNBIND_SHELLY_ACCOUNT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
GET_SHELLY_AUTH_URL_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
LIST_SHELLY_DEVICES_SCHEMA = GET_SHELLY_AUTH_URL_SCHEMA
LIST_SHELLY_BINDING_FAILURES_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
    vol.Optional(SERVICE_FIELD_STATE, default=""): vol.All(
        cv.string,
        vol.Length(max=64),
    ),
})


# ---------------------------------------------------------------------------
# Coordinator routing helpers
# ---------------------------------------------------------------------------


def _loaded_coordinators(hass: HomeAssistant) -> list[JackerySolarVaultCoordinator]:
    """Collect active JackerySolarVaultCoordinator instances from loaded config entries.

    Returns:
        list[JackerySolarVaultCoordinator]: Active coordinators for loaded Jackery
        config entries.
    """
    coordinators: list[JackerySolarVaultCoordinator] = []
    for loaded_entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = getattr(loaded_entry, "runtime_data", None)
        if isinstance(coordinator, JackerySolarVaultCoordinator):
            coordinators.append(coordinator)
    return coordinators


# Jackery numeric main-device ids are digits only (e.g.
# ``573702884982521856`` per PROTOCOL.md §12). Subdevice identifiers in the
# integration follow ``<parent_digits>_<suffix>`` where ``<suffix>`` is
# ``battery_pack_<n>``, ``smart_meter``, ``smart_plug_<n>``, ``meter_head_<n>``,
# ``collector_<n>``, etc. This regex captures only the parent digits so the
# command path always targets the main device's radio — subdevices share that
# radio and do not accept BLE/MQTT commands of their own (PROTOCOL.md §3.4 +
# §4 + docs/Markdown/APP_POLLING_MQTT.md §"Subdevice-Polling").
_JACKERY_MAIN_DEVICE_RE: Final = re.compile(r"^(\d+)(?:_.+)?$")


def _strip_jackery_subdevice_suffix(device_id: str) -> str:
    """Return the parent numeric Jackery device identifier.

    Remove a recognized subdevice suffix before returning the identifier.

    Parameters:
        device_id (str): Device identifier that may include a trailing `_suffix`
        (e.g., "12345_child").

    Returns:
        str: The leading numeric device identifier if a suffix is present (e.g.,
        "12345"), otherwise the original input.
    """
    match = _JACKERY_MAIN_DEVICE_RE.match(device_id)
    return match.group(1) if match else device_id


def _resolve_jackery_device_id(hass: HomeAssistant, raw: str) -> str:
    """Resolve an identifier to the parent Jackery numeric device id.

    Accept a Home Assistant device-registry id or Jackery compound id.

    Parameters:
        raw (str): A device-registry id or a Jackery device identifier that may include
        a documented subdevice suffix.

    Returns:
        parent_id (str): The parent Jackery numeric device id with any documented
        subdevice suffix removed.
    """
    registry = dr.async_get(hass)
    # Battery packs and accessories are HA child devices (HA 2026.9); the UI
    # device selector passes their id, so climb to the parent main device.
    device = registry.async_get(raw)
    if device is not None:
        seen: set[str] = set()
        while (
            parent_id := getattr(device, "parent_device_id", None)
            or getattr(device, "via_device_id", None)
        ) and parent_id not in seen:
            seen.add(parent_id)
            parent = registry.async_get(parent_id)
            if parent is None:
                break
            device = parent
        for domain, identifier in device.identifiers:
            if domain == DOMAIN:
                return _strip_jackery_subdevice_suffix(str(identifier))
    return _strip_jackery_subdevice_suffix(raw)


def _coordinator_for_device(
    hass: HomeAssistant,
    device_id: str,
    *,
    config_entry_id: str | None = None,
) -> JackerySolarVaultCoordinator | None:
    """Locate the coordinator that manages the specified Jackery device id.

    Returns:
        The coordinator that manages the device, or `None` if no matching coordinator is
        loaded.
    """
    for coordinator in _loaded_coordinators(hass):
        if config_entry_id is not None and (
            coordinator.config_entry is None
            or coordinator.config_entry.entry_id != config_entry_id
        ):
            continue
        if device_id in (coordinator.data or {}):
            return coordinator
    return None


def _is_portable_device(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> bool:
    """Return True when the target payload is an Explorer/Portable model."""
    payload = (coordinator.data or {}).get(device_id) or {}
    if _payload_has_home_payload_evidence(payload):
        return False
    for section in (PAYLOAD_DEVICE, PAYLOAD_DISCOVERY):
        meta = payload.get(section) or {}
        if (
            isinstance(meta, dict)
            and meta.get(PAYLOAD_DISCOVERY_SOURCE) == DISCOVERY_SOURCE_LEGACY_BIND_LIST
        ):
            return True
    return False


def _raise_if_portable_home_service(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    *,
    translation_key: str,
    service_name: str,
) -> None:
    """Reject Home-family command services for portable devices."""
    if not _is_portable_device(coordinator, device_id):
        return
    raise _service_validation_error(
        translation_key,
        device_id=device_id,
        error=(
            f"{service_name} is a Home-family command and is not valid "
            "for portable devices"
        ),
    )


def _raise_if_unsupported_portable_tou(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    *,
    translation_key: str,
    supported_models: frozenset[int],
) -> bool:
    """Allow TOU operations only for App-supported Portable models."""
    family_reader = getattr(coordinator, "device_family", None)
    family = family_reader(device_id) if callable(family_reader) else None
    if family == "unknown":
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error="TOU plan requires an unambiguous device family",
        )
    portable = (
        family == "portable"
        if family is not None
        else _is_portable_device(coordinator, device_id)
    )
    if not portable:
        return False
    model_reader = getattr(coordinator, "device_model_code", None)
    model = model_reader(device_id) if callable(model_reader) else None
    if (
        isinstance(model, int)
        and not isinstance(model, bool)
        and model in supported_models
    ):
        return True
    raise _service_validation_error(
        translation_key,
        device_id=device_id,
        error="TOU plan is not supported for this Portable model",
    )


def _coordinator_for_system(
    hass: HomeAssistant,
    system_id: str,
) -> JackerySolarVaultCoordinator | None:
    """Finds the loaded coordinator that manages the specified Jackery system id.

    Searches each loaded coordinator's payloads for a `system` object whose `id` or
    `system_id` (converted to string) equals the provided `system_id`.

    Returns:
        The matching JackerySolarVaultCoordinator if found, `None` otherwise.
    """
    for coordinator in _loaded_coordinators(hass):
        for payload in (coordinator.data or {}).values():
            system: dict[str, Any] = payload.get(PAYLOAD_SYSTEM) or {}
            for key in (FIELD_ID, FIELD_SYSTEM_ID):
                if str(system.get(key) or "") == system_id:
                    return coordinator
    return None


def _service_validation_error(
    translation_key: str,
    *,
    device_id: str,
    error: object,
    extra_placeholders: dict[str, str] | None = None,
) -> ServiceValidationError:
    """Construct a translated ``ServiceValidationError``.

    Populate the integration domain and placeholders for a device and an error.

    Parameters:
        translation_key (str): Translation key to identify the localized error message.
        device_id (str): Value inserted into the `device_id` translation placeholder.
        error (object): Value converted to string and inserted into the `error`
        translation placeholder.

    Returns:
        ServiceValidationError: Error with `translation_domain` set to DOMAIN,
        `translation_key` set to `translation_key`, and `translation_placeholders`
        containing `device_id` and `error`.
    """
    placeholders = {
        "device_id": device_id,
        "error": redacted_error(error),
    }
    if extra_placeholders is not None:
        placeholders.update(extra_placeholders)
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders=placeholders,
    )


def _service_action_error(
    translation_key: str,
    *,
    device_id: str,
    error: object,
    entry: object | None = None,
    extra_placeholders: dict[str, str] | None = None,
) -> HomeAssistantError:
    """Construct a translated error for an external service action failure."""
    placeholders = {
        "device_id": device_id,
        "error": redacted_error(error, entry=entry),
    }
    if extra_placeholders is not None:
        placeholders.update(extra_placeholders)
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders=placeholders,
    )


def _device_id_from_service(
    hass: HomeAssistant,
    raw: object,
    *,
    translation_key: str,
    extra_placeholders: dict[str, str] | None = None,
) -> str:
    """Return a resolved Jackery device id from a direct service call."""
    device_id = ""
    if isinstance(raw, str):
        device_id = raw.strip()
        if device_id:
            return _resolve_jackery_device_id(hass, device_id)
        error = f"{SERVICE_FIELD_DEVICE_ID} must not be empty"
    else:
        error = f"{SERVICE_FIELD_DEVICE_ID} must be text"
    raise _service_validation_error(
        translation_key,
        device_id=device_id,
        error=error,
        extra_placeholders=extra_placeholders,
    )


def _rename_system_id_from_service(raw: object) -> str:
    """Return a validated system id from a direct rename service call."""
    system_id = ""
    if isinstance(raw, str):
        system_id = raw.strip()
        if not system_id:
            error = f"{SERVICE_FIELD_SYSTEM_ID} must not be empty"
        elif system_id.isascii() and system_id.isdecimal():
            return system_id
        else:
            error = f"{SERVICE_FIELD_SYSTEM_ID} must be numeric"
    else:
        error = f"{SERVICE_FIELD_SYSTEM_ID} must be text"
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="rename_system_failed",
        translation_placeholders={
            "system_id": system_id,
            "error": error,
        },
    )


def _rename_name_from_service(raw: object, system_id: str) -> str:
    """Return a validated system name from a direct rename service call."""
    if not isinstance(raw, str):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="rename_system_failed",
            translation_placeholders={
                "system_id": system_id,
                "error": f"{SERVICE_FIELD_NEW_NAME} must be text",
            },
        )
    parsed = raw.strip()
    if not parsed:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="rename_system_failed",
            translation_placeholders={
                "system_id": system_id,
                "error": f"{SERVICE_FIELD_NEW_NAME} must not be empty",
            },
        )
    if len(parsed) > _MAX_SYSTEM_NAME_LENGTH:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="rename_system_failed",
            translation_placeholders={
                "system_id": system_id,
                "error": (
                    f"{SERVICE_FIELD_NEW_NAME} must be at most "
                    f"{_MAX_SYSTEM_NAME_LENGTH} characters"
                ),
            },
        )
    return parsed


def _storm_alert_id_from_service(raw: object, device_id: str) -> str:
    """Return a validated storm-alert id from a direct service call."""
    alert_id = ""
    if isinstance(raw, str):
        alert_id = raw.strip()
        if alert_id:
            return alert_id
        error = f"{SERVICE_FIELD_ALERT_ID} must not be empty"
    else:
        error = f"{SERVICE_FIELD_ALERT_ID} must be text"
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="delete_storm_alert_failed",
        translation_placeholders={
            "device_id": device_id,
            "alert_id": alert_id,
            "error": error,
        },
    )


def _reject_json_constant(constant: str) -> object:
    """Reject non-standard JSON constants such as NaN and Infinity."""
    msg = f"invalid JSON constant: {constant}"
    raise ValueError(msg)


def _json_native_value(value: object) -> JsonValueType:
    """Return a JSON-native value or raise ValueError."""
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        msg = "body must contain only finite numbers"
        raise ValueError(msg)
    if isinstance(value, list):
        return [_json_native_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, JsonValueType] = {}
        for key, item in cast("dict[object, object]", value).items():
            if not isinstance(key, str):
                msg = "body object keys must be strings"
                raise TypeError(msg)
            normalized[key] = _json_native_value(item)
        return normalized
    msg = "body must contain only JSON-compatible values"
    raise ValueError(msg)


def _json_native_body(
    body: dict[object, object],
    device_id: str,
) -> dict[str, JsonValueType]:
    """Return a JSON-native object body or raise a translated service error."""
    try:
        normalized = _json_native_value(body)
    except (TypeError, ValueError) as err:
        msg = "send_ble_command_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
        ) from err
    if not isinstance(normalized, dict):
        msg = "send_ble_command_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=TypeError(f"Expected dict body, got {type(normalized).__name__}"),
        )
    return normalized


def _ble_body_from_service(
    raw_body: object,
    device_id: str,
) -> dict[str, JsonValueType]:
    """Parse a BLE command `body` value from a service call into a dict.

    Accept a mapping or a JSON-encoded object string. String values must decode
    to an object; other values raise a translated ``ServiceValidationError``.

    Parameters:
        raw_body (object): Mapping or JSON string encoding an object to use as the BLE
        body.
        device_id (str): Jackery device identifier included in validation error
        placeholders.

    Returns:
        dict[str, JsonValueType]: Parsed body mapping to send with the BLE command.

    Raises:
        ServiceValidationError: If `raw_body` is neither a mapping nor a JSON object
        string, or if JSON parsing fails.
    """
    if isinstance(raw_body, dict):
        return _json_native_body(raw_body, device_id)
    if isinstance(raw_body, str):
        try:
            parsed = json.loads(
                raw_body.strip(),
                parse_constant=_reject_json_constant,
            )
        except ValueError as err:
            msg = "send_ble_command_failed"
            raise _service_validation_error(
                msg,
                device_id=device_id,
                error=f"body is not valid JSON: {err}",
            ) from err
        if isinstance(parsed, dict):
            return _json_native_body(parsed, device_id)
        msg = "send_ble_command_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="body JSON must be an object",
        )
    msg = "send_ble_command_failed"
    raise _service_validation_error(
        msg,
        device_id=device_id,
        error="body must be a mapping or JSON object string",
    )


def _tou_tasks_from_service(
    raw_body: object,
    device_id: str,
) -> list[dict[str, JsonValueType]]:
    """Return a TOU tasks list from a service object or JSON string."""
    parsed: object = raw_body
    if isinstance(raw_body, str):
        try:
            parsed = json.loads(
                raw_body.strip(),
                parse_constant=_reject_json_constant,
            )
        except ValueError as err:
            msg = "save_tou_plan_failed"
            raise _service_validation_error(
                msg,
                device_id=device_id,
                error=f"body is not valid JSON: {err}",
            ) from err
    if isinstance(parsed, dict):
        parsed = parsed.get("tasks")
    if not isinstance(parsed, list):
        msg = "save_tou_plan_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="body must be a tasks list or an object containing tasks",
        )
    tasks: list[dict[str, JsonValueType]] = []
    for item in parsed:
        if not isinstance(item, dict):
            msg = "save_tou_plan_failed"
            raise _service_validation_error(
                msg,
                device_id=device_id,
                error="each TOU task must be a JSON object",
            )
        tasks.append(_json_native_body(cast("dict[object, object]", item), device_id))
    return tasks


def _validate_portable_tou_tasks(  # ruff: ignore[too-many-branches]
    tasks: list[dict[str, JsonValueType]], device_id: str
) -> None:
    """Validate the App's Portable PeaksTroughs fields before writing."""

    def fail() -> None:
        raise _service_validation_error(
            "save_tou_plan_failed",
            device_id=device_id,
            error="Portable TOU plan contains an invalid App field type",
        )

    def app_integer(value: object) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and -(2**31) <= value < 2**31
        )

    for item in tasks:
        month = item.get("month")
        if month is not None and not isinstance(month, str):
            fail()
        follow = item.get("follow")
        if "follow" in item and not app_integer(follow):
            fail()
        for field in ("crest", "flat", "lowest", "trough"):
            if field not in item:
                continue
            value = item[field]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or safe_float(value) is None
            ):
                fail()
        for field in ("work", "weekend"):
            periods = item.get(field)
            if periods is None:
                continue
            if not isinstance(periods, list):
                fail()
                continue
            for period in periods:
                if period is None:
                    continue
                if not isinstance(period, dict):
                    fail()
                    continue
                for name in ("start", "end"):
                    value = period.get(name)
                    if value is not None and not isinstance(value, str):
                        fail()
                if "type" in period and not app_integer(period["type"]):
                    fail()


def _service_bool(
    raw: object,
    *,
    field_name: str,
    translation_key: str,
    device_id: str,
) -> bool:
    """Return a parsed service boolean or raise a translated validation error."""
    parsed = (
        safe_bool(raw)
        if raw is None or isinstance(raw, bool | int | float | str)
        else None
    )
    if parsed is None:
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error=f"{field_name} must be a boolean",
        )
    return parsed


def _service_required_text(
    raw: object,
    *,
    field_name: str,
    translation_key: str,
    device_id: str,
    max_length: int,
) -> str:
    """Return a stripped non-empty service text field within the schema length."""
    if not isinstance(raw, str):
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error=f"{field_name} must be text",
        )
    parsed = raw.strip()
    if not parsed:
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error=f"{field_name} must not be empty",
        )
    if len(parsed) > max_length:
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error=f"{field_name} must be at most {max_length} characters",
        )
    return parsed


def _service_optional_text(
    raw: object,
    *,
    field_name: str,
    translation_key: str,
    device_id: str,
    max_length: int,
) -> str:
    """Return an optional service text field within the schema length."""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error=f"{field_name} must be text",
        )
    parsed = raw
    if len(parsed) > max_length:
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error=f"{field_name} must be at most {max_length} characters",
        )
    return parsed


def _service_int(
    raw: object,
    *,
    field_name: str,
    translation_key: str,
    device_id: str,
    bounds: tuple[int, int],
) -> int:
    """Return a parsed service integer within the schema range."""
    min_value, max_value = bounds
    try:
        parsed = _coerce_service_int(raw)
    except vol.Invalid as err:
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error=f"{field_name} must be an integer",
        ) from err
    if parsed < min_value or parsed > max_value:
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error=f"{field_name} must be between {min_value} and {max_value}",
        )
    return parsed


def _service_float(
    raw: object,
    *,
    field_name: str,
    translation_key: str,
    device_id: str,
    bounds: tuple[float, float],
) -> float:
    """Return a parsed service float within the schema range."""
    min_value, max_value = bounds
    try:
        parsed = _coerce_service_float(raw)
    except vol.Invalid as err:
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error=f"{field_name} must be a number",
        ) from err
    if not math.isfinite(parsed) or parsed < min_value or parsed > max_value:
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error=f"{field_name} must be between {min_value} and {max_value}",
        )
    return parsed


# ---------------------------------------------------------------------------
# Service-action dispatchers
# ---------------------------------------------------------------------------


async def _async_handle_rename(hass: HomeAssistant, call: ServiceCall) -> None:
    """Forward a rename to the API client of the matching coordinator."""
    system_id = _rename_system_id_from_service(call.data[SERVICE_FIELD_SYSTEM_ID])
    new_name = _rename_name_from_service(call.data[SERVICE_FIELD_NEW_NAME], system_id)
    coordinator = _coordinator_for_system(hass, system_id)
    if coordinator is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="rename_system_failed",
            translation_placeholders={
                "system_id": system_id,
                "error": "no Jackery entry owns this system id",
            },
        )
    try:
        await coordinator.async_set_system_name(system_id, new_name)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while renaming a system. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(
            msg,
        ) from err
    except JackeryError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="rename_system_failed",
            translation_placeholders={
                "system_id": system_id,
                "error": redacted_error(err, entry=getattr(coordinator, "entry", None)),
            },
        ) from err


async def _async_handle_refresh_weather_plan(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Trigger a weather-plan refresh on the matching coordinator."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="refresh_weather_plan_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="refresh_weather_plan_failed",
            translation_placeholders={
                "device_id": device_id,
                "error": "no Jackery entry owns this device id",
            },
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="refresh_weather_plan_failed",
        service_name=SERVICE_REFRESH_WEATHER_PLAN,
    )
    try:
        await coordinator.async_query_weather_plan(device_id)
    except ConfigEntryAuthFailed:
        raise
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while refreshing a weather plan. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (HomeAssistantError, JackeryError, LookupError) as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="refresh_weather_plan_failed",
            translation_placeholders={
                "device_id": device_id,
                "error": redacted_error(err, entry=getattr(coordinator, "entry", None)),
            },
        ) from err


async def _async_handle_refresh_subdevices(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Force accessory/sub-device rediscovery on the matching coordinator.

    Re-runs the smart-accessory enumeration against the Jackery cloud, then
    requests a full coordinator refresh so any newly discovered (or removed)
    accessories are reflected in the entity registry. This is the manual
    counterpart to the automatic enumeration that runs on the polling cycle.
    """
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="refresh_subdevices_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="refresh_subdevices_failed",
            translation_placeholders={
                "device_id": device_id,
                "error": "no Jackery entry owns this device id",
            },
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="refresh_subdevices_failed",
        service_name=SERVICE_REFRESH_SUBDEVICES,
    )
    try:
        await coordinator.async_refresh_subdevices(context_device_id=device_id)
    except ConfigEntryAuthFailed:
        raise
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while refreshing sub-devices. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (HomeAssistantError, JackeryError, LookupError) as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="refresh_subdevices_failed",
            translation_placeholders={
                "device_id": device_id,
                "error": redacted_error(err, entry=getattr(coordinator, "entry", None)),
            },
        ) from err


async def _async_handle_delete_storm_alert(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Delete a storm alert for the specified Jackery device.

    Raises:
        ServiceValidationError: If no coordinator owns the resolved device id, or if the
        coordinator reports an error while deleting the alert. The error includes
        translation placeholders `device_id`, `alert_id`, and `error`.
    """
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="delete_storm_alert_failed",
        extra_placeholders={"alert_id": ""},
    )
    alert_id = _storm_alert_id_from_service(
        call.data[SERVICE_FIELD_ALERT_ID],
        device_id,
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="delete_storm_alert_failed",
            translation_placeholders={
                "device_id": device_id,
                "alert_id": alert_id,
                "error": "no Jackery entry owns this device id",
            },
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="delete_storm_alert_failed",
        service_name=SERVICE_DELETE_STORM_ALERT,
    )
    try:
        await coordinator.async_delete_storm_alert(device_id, alert_id)
    except ConfigEntryAuthFailed:
        raise
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while deleting a storm alert. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (HomeAssistantError, JackeryError, LookupError) as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="delete_storm_alert_failed",
            translation_placeholders={
                "device_id": device_id,
                "alert_id": alert_id,
                "error": redacted_error(err, entry=getattr(coordinator, "entry", None)),
            },
        ) from err


async def _async_handle_set_storm_alert_location(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Update the storm-alert GPS location on the matching coordinator."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="set_storm_alert_location_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        raise _service_validation_error(
            "set_storm_alert_location_failed",
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="set_storm_alert_location_failed",
        service_name=SERVICE_SET_STORM_ALERT_LOCATION,
    )
    latitude = call.data[SERVICE_FIELD_LATITUDE]
    longitude = call.data[SERVICE_FIELD_LONGITUDE]
    try:
        await coordinator.async_update_storm_alert_location(
            device_id,
            latitude,
            longitude,
        )
    except ConfigEntryAuthFailed:
        raise
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while updating the storm-alert "
            "location. Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (HomeAssistantError, JackeryError, LookupError) as err:
        raise _service_action_error(
            "set_storm_alert_location_failed",
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_set_third_party_mqtt_config(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Send a third-party MQTT configuration to the owning coordinator.

    Resolve the Jackery device before forwarding the configuration.

    Parameters:
        hass (HomeAssistant): Home Assistant instance.
        call (ServiceCall): Service call whose `data` must include:
            - SERVICE_FIELD_DEVICE_ID: device identifier string to resolve.
            - SERVICE_FIELD_ENABLE: boolean to enable or disable third-party MQTT.
            - SERVICE_FIELD_IP: IP address or hostname string.
            - SERVICE_FIELD_PORT: integer port number.
            - SERVICE_FIELD_USERNAME: optional username string (default "").
            - SERVICE_FIELD_PASSWORD: optional password string (default "").
            - SERVICE_FIELD_TOKEN: optional token string (default "").

    Raises:
        ServiceValidationError: If no loaded coordinator owns the resolved device id, or
        if applying the configuration fails. The error includes translation placeholders
        `device_id` and `error`.
    """
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="set_third_party_mqtt_config_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="set_third_party_mqtt_config_failed",
            translation_placeholders={
                "device_id": device_id,
                "error": "no Jackery entry owns this device id",
            },
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="set_third_party_mqtt_config_failed",
        service_name=SERVICE_SET_THIRD_PARTY_MQTT_CONFIG,
    )
    try:
        await coordinator.async_set_third_party_mqtt_config(
            device_id,
            enable=_service_bool(
                call.data[SERVICE_FIELD_ENABLE],
                field_name=SERVICE_FIELD_ENABLE,
                translation_key="set_third_party_mqtt_config_failed",
                device_id=device_id,
            ),
            ip=_service_required_text(
                call.data[SERVICE_FIELD_IP],
                field_name=SERVICE_FIELD_IP,
                translation_key="set_third_party_mqtt_config_failed",
                device_id=device_id,
                max_length=128,
            ),
            port=_service_int(
                call.data[SERVICE_FIELD_PORT],
                field_name=SERVICE_FIELD_PORT,
                translation_key="set_third_party_mqtt_config_failed",
                device_id=device_id,
                bounds=(1, 65535),
            ),
            username=_service_optional_text(
                call.data.get(SERVICE_FIELD_USERNAME, ""),
                field_name=SERVICE_FIELD_USERNAME,
                translation_key="set_third_party_mqtt_config_failed",
                device_id=device_id,
                max_length=128,
            ),
            password=_service_optional_text(
                call.data.get(SERVICE_FIELD_PASSWORD, ""),
                field_name=SERVICE_FIELD_PASSWORD,
                translation_key="set_third_party_mqtt_config_failed",
                device_id=device_id,
                max_length=128,
            ),
            token=_service_optional_text(
                call.data.get(SERVICE_FIELD_TOKEN, ""),
                field_name=SERVICE_FIELD_TOKEN,
                translation_key="set_third_party_mqtt_config_failed",
                device_id=device_id,
                max_length=512,
            ),
        )
    except ConfigEntryAuthFailed:
        raise
    except ServiceValidationError:
        raise
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while setting third-party MQTT. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="set_third_party_mqtt_config_failed",
            translation_placeholders={
                "device_id": device_id,
                "error": redacted_error(err, entry=getattr(coordinator, "entry", None)),
            },
        ) from err


async def _async_handle_query_third_party_mqtt_config(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Query a device's third-party MQTT configuration.

    Resolves the provided device identifier to the owning Jackery coordinator and
    requests the device's third-party MQTT settings from the coordinator API.

    Parameters:
        call (ServiceCall): Service call containing SERVICE_FIELD_DEVICE_ID with the
        device identifier to query.

    Raises:
        ServiceValidationError: If no Jackery coordinator owns the resolved device id,
        or if the coordinator query fails. The integration uses translation_key
        `query_third_party_mqtt_config_failed` for errors.
    """
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="query_third_party_mqtt_config_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="query_third_party_mqtt_config_failed",
            translation_placeholders={
                "device_id": device_id,
                "error": "no Jackery entry owns this device id",
            },
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="query_third_party_mqtt_config_failed",
        service_name=SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    )
    try:
        await coordinator.async_query_third_party_mqtt_config(device_id)
    except ConfigEntryAuthFailed:
        raise
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while querying third-party MQTT. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="query_third_party_mqtt_config_failed",
            translation_placeholders={
                "device_id": device_id,
                "error": redacted_error(err, entry=getattr(coordinator, "entry", None)),
            },
        ) from err


def _registry_device_serial_number(device: dr.BaseDeviceEntry | None) -> str | None:
    """Read the physical serial only on full devices, never HA child entries."""
    return device.serial_number if isinstance(device, dr.DeviceEntry) else None


def _ble_ota_child_serial(  # ruff: ignore[too-many-return-statements]
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    selected: dr.BaseDeviceEntry,
) -> str | None:
    """Resolve a child serial without guessing from its registry identifier."""
    is_pack = any(
        domain == DOMAIN and identifier.startswith(f"{device_id}_battery_pack_")
        for domain, identifier in selected.identifiers
    )
    if not is_pack:
        return selected.serial_number if isinstance(selected, dr.DeviceEntry) else None
    matches: list[tuple[int, str]] = []
    for index in range(1, 6):
        serial = coordinator.battery_pack_identity_serial(device_id, index)
        if serial is None:
            continue
        key = stable_subdevice_key("battery_pack", serial, index)
        identifier = f"{device_id}_{key}"
        if (DOMAIN, identifier) in selected.identifiers:
            matches.append((index, serial))
    if len(matches) != 1:
        return None
    matched_index, serial = matches[0]
    if isinstance(selected, dr.DeviceEntry) and selected.serial_number != serial:
        return None
    confirmation = coordinator.battery_pack_recent_confirmation(device_id, serial)
    if confirmation is False:
        return None
    if confirmation is True:
        return serial
    versions = (coordinator.data or {}).get(device_id, {}).get(PAYLOAD_BLE_OTA_VERSIONS)
    if not isinstance(versions, list) or not any(
        isinstance(version, dict) and version.get(FIELD_DEVICE_SN) == serial
        for version in versions
    ):
        return None
    key = stable_subdevice_key("battery_pack", serial, matched_index)
    observed_serials = [
        observed
        for index in range(1, 6)
        if (observed := coordinator.battery_pack_observed_serial(device_id, index))
        is not None
    ]
    if observed_serials.count(serial) != 1:
        return None
    for observed in observed_serials:
        if (
            observed != serial
            and stable_subdevice_key("battery_pack", observed, matched_index) == key
        ):
            return None
    return serial


def _ble_ota_parent_serial(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    parent_device: object | None,
) -> str | None:
    """Resolve a current parent serial from live payload or bound HA identity.

    The device-registry value is a cached identity tied to the selected Jackery
    config entry. Use it only when the coordinator has no serial yet; a conflict
    between the live payload and registry is ambiguous and must be rejected.
    """
    live_serial = coordinator._resolve_device_sn(device_id)  # ruff: ignore[private-member-access]
    registry_serial = getattr(parent_device, "serial_number", None)
    if not isinstance(registry_serial, str) or not registry_serial.strip():
        registry_serial = None
    indexed = coordinator._device_index.get(device_id) or {}  # ruff: ignore[private-member-access]
    payload = (coordinator.data or {}).get(device_id, {})
    serials: set[str] = set()
    for source in (
        indexed.get(PAYLOAD_DEVICE_META),
        payload.get(PAYLOAD_DEVICE),
        payload.get(PAYLOAD_DISCOVERY),
    ):
        if isinstance(source, Mapping):
            serials.update(
                str(value)
                for field in (FIELD_DEVICE_SN, FIELD_DEV_SN)
                if (value := source.get(field))
            )
    if len(serials) > 1 or (
        registry_serial is not None
        and any(serial != registry_serial for serial in serials)
    ):
        return None
    return live_serial or registry_serial


async def _async_check_ground_ble_ota_versions(  # ruff: ignore[too-many-arguments, too-many-statements]
    hass: HomeAssistant,
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    raw_device_id: str,
    selected: dr.BaseDeviceEntry | None,
    *,
    force_fresh_report: bool = False,
) -> ServiceResponse:
    """Check a Ground candidate while its parent and BLE evidence remain bound."""
    pending = coordinator._ble_ota_pending  # ruff: ignore[private-member-access]
    marker = (
        dict(pending[device_id])
        if pending is not None and device_id in pending
        else None
    )
    recovering = marker is not None
    if marker is not None and marker.get("target_kind") != "ground_accessory":
        msg = "Pending BLE OTA belongs to a different target"
        raise HomeAssistantError(msg)
    identity = coordinator.ground_ble_identity(device_id)
    target_sn = (
        identity.serial
        if recovering and identity is not None
        else coordinator.ground_ble_ota_target(device_id)
    )
    if marker is not None and marker.get("target_sn") != target_sn:
        msg = "Pending BLE OTA belongs to a different ground accessory"
        raise HomeAssistantError(msg)
    if target_sn is None or identity is None:
        raise _service_validation_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error="no current BLE ground accessory identity and version report",
        )
    parent_sn = coordinator._resolve_device_sn(device_id)  # ruff: ignore[private-member-access]
    if not parent_sn:
        raise _service_validation_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error="owning device serial number is missing",
        )
    model, key = (
        coordinator._ble_ota_model_evidence(device_id),  # ruff: ignore[private-member-access]
        coordinator.device_bluetooth_key(device_id),
    )
    registry = dr.async_get(hass)
    selected_snapshot = (
        (
            frozenset(selected.identifiers),
            getattr(selected, "created_at", None),
            selected.config_entry_id,
        )
        if selected
        else None
    )
    changed = False

    def observe_identity() -> None:
        nonlocal changed
        properties = (coordinator.data or {}).get(device_id, {}).get("properties", {})
        serial = properties.get("grdAccSn") if isinstance(properties, dict) else None
        if (
            not isinstance(serial, str)
            or serial.strip() != target_sn
            or coordinator._ble_ota_model_evidence(device_id) != model  # ruff: ignore[private-member-access]
            or coordinator.device_bluetooth_key(device_id) != key
        ):
            changed = True

    def identity_is_current() -> bool:  # ruff: ignore[too-many-return-statements]
        current = coordinator._ble_ground_identity.get(device_id)  # ruff: ignore[private-member-access]
        current_pending = coordinator._ble_ota_pending  # ruff: ignore[private-member-access]
        if (
            changed
            or current_pending is None
            or current_pending.get(device_id) != marker
            or (
                not recovering and device_id in coordinator._ble_ota_recovery_active  # ruff: ignore[private-member-access]
            )
        ):
            return False
        current_parent_sn = coordinator._resolve_device_sn(device_id)  # ruff: ignore[private-member-access]
        if current_parent_sn is not None and current_parent_sn != parent_sn:
            return False
        if current is None:
            return False
        if (
            current.generation != identity.generation
            or current.first_sequence != identity.first_sequence
            or coordinator._ble_ota_model_evidence(device_id) != model  # ruff: ignore[private-member-access]
            or coordinator.device_bluetooth_key(device_id) != key
        ):
            return False
        if (
            not recovering and coordinator.ground_ble_ota_target(device_id) != target_sn
        ) or (recovering and coordinator.ground_ble_identity(device_id) != current):
            return False
        current_device = registry.async_get(raw_device_id)
        if selected is None:
            return current_device is None and raw_device_id == device_id
        return bool(
            current_device is not None
            and (
                frozenset(current_device.identifiers),
                getattr(current_device, "created_at", None),
                current_device.config_entry_id,
            )
            == selected_snapshot
            and _resolve_jackery_device_id(hass, raw_device_id) == device_id
            and not (
                getattr(current_device, "parent_device_id", None)
                or getattr(current_device, "via_device_id", None)
            )
        )

    unsubscribe = coordinator.async_add_listener(observe_identity)
    try:
        if not identity_is_current():
            msg = "Ground accessory identity or OTA interlock is not current"
            raise HomeAssistantError(msg)
        result = await coordinator.async_check_ble_ota_versions(
            device_id,
            target_sn=target_sn,
            parent_sn=parent_sn,
            identity_is_current=identity_is_current if recovering else None,
            force_fresh_report=force_fresh_report,
        )
    except JackeryAuthError as err:
        raise ConfigEntryAuthFailed from err
    except (JackeryError, LookupError) as err:
        raise _service_action_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    finally:
        unsubscribe()
    if not identity_is_current():
        msg = "Selected ground accessory identity changed during the BLE update check"
        raise HomeAssistantError(msg)
    return cast("ServiceResponse", result)


async def _async_handle_check_ble_ota_versions(  # ruff: ignore[too-many-locals, too-many-statements, too-many-branches, complex-structure]
    hass: HomeAssistant,
    call: ServiceCall,
    *,
    force_fresh_report: bool = False,
) -> ServiceResponse:
    """Return update candidates for a recent device-reported BLE version list."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="check_ble_ota_versions_failed",
    )
    raw_device_id = call.data[SERVICE_FIELD_DEVICE_ID]
    registry = dr.async_get(hass)
    selected = registry.async_get(raw_device_id)
    coordinator = _coordinator_for_device(
        hass,
        device_id,
        config_entry_id=selected.config_entry_id if selected is not None else None,
    )
    if coordinator is None:
        raise _service_validation_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    parent_link = (
        getattr(selected, "parent_device_id", None)
        or getattr(selected, "via_device_id", None)
        if selected is not None
        else None
    )
    parent_registry_id = parent_link or (
        getattr(selected, "id", None) if selected is not None else None
    )
    parent_registry = (
        registry.async_get(parent_registry_id)
        if parent_registry_id is not None
        else None
    )
    is_child = parent_link is not None
    is_ground = call.data.get("target") == "ground_accessory"
    if is_ground and is_child:
        raise _service_validation_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error="select the owning Portable device for a ground accessory",
        )
    if selected is not None and not any(
        domain == DOMAIN for domain, _ in selected.identifiers
    ):
        raise _service_validation_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error="selected device is not a Jackery device",
        )
    if selected is None and raw_device_id != device_id:
        raise _service_validation_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error="subdevice identity is not registered",
        )
    if selected is not None and (
        coordinator.config_entry is None
        or selected.config_entry_id != coordinator.config_entry.entry_id
        or (parent_link is not None and registry.async_get(parent_link) is None)
    ):
        raise _service_validation_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error="selected device ownership is not valid",
        )
    if parent_link is not None and (
        selected is None
        or parent_registry is None
        or parent_registry.config_entry_id != selected.config_entry_id
        or not any(domain == DOMAIN for domain, _ in parent_registry.identifiers)
    ):
        raise _service_validation_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error="selected parent device ownership is not valid",
        )
    if parent_registry is not None and (
        (DOMAIN, device_id) not in parent_registry.identifiers
        or getattr(parent_registry, "parent_device_id", None)
        or getattr(parent_registry, "via_device_id", None)
    ):
        raise _service_validation_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error="selected parent is not the owning Jackery main device",
        )
    if is_ground:
        return await _async_check_ground_ble_ota_versions(
            hass,
            coordinator,
            device_id,
            raw_device_id,
            selected,
            force_fresh_report=force_fresh_report,
        )
    parent_sn = _ble_ota_parent_serial(coordinator, device_id, parent_registry)
    if not parent_sn:
        raise _service_validation_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error="owning device serial number is missing or inconsistent",
        )
    target_sn = (
        _ble_ota_child_serial(coordinator, device_id, selected)
        if is_child and selected is not None
        else parent_sn
    )
    if not target_sn or (is_child and target_sn == parent_sn):
        raise _service_validation_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error="selected device serial number is missing",
        )
    pack_revision = coordinator.battery_pack_identity_revision(device_id, target_sn)
    child_identity_changed = False

    def observe_child_identity() -> None:
        nonlocal child_identity_changed
        current = registry.async_get(raw_device_id)
        if (
            current is None
            or _ble_ota_child_serial(coordinator, device_id, current) != target_sn
        ):
            child_identity_changed = True

    selected_identifiers = frozenset(selected.identifiers) if selected else None
    selected_created = getattr(selected, "created_at", None) if selected else None
    selected_entry = selected.config_entry_id if selected else None
    parent_identifiers = (
        frozenset(parent_registry.identifiers) if parent_registry is not None else None
    )
    parent_created = getattr(parent_registry, "created_at", None)
    parent_entry = getattr(parent_registry, "config_entry_id", None)
    parent_registry_serial = getattr(parent_registry, "serial_number", None)

    def parent_registry_identity_is_current() -> bool:
        if parent_registry_id is None:
            return parent_registry is None
        current_parent = registry.async_get(parent_registry_id)
        return bool(
            current_parent is not None
            and getattr(current_parent, "created_at", None) == parent_created
            and current_parent.config_entry_id == parent_entry
            and frozenset(current_parent.identifiers) == parent_identifiers
            and getattr(current_parent, "serial_number", None) == parent_registry_serial
        )

    def selected_identity_is_current() -> bool:
        current_parent_sn = coordinator._resolve_device_sn(device_id)  # ruff: ignore[private-member-access]
        if current_parent_sn is not None and current_parent_sn != parent_sn:
            return False
        if (
            parent_registry_id is not None
            and _ble_ota_parent_serial(
                coordinator, device_id, registry.async_get(parent_registry_id)
            )
            != parent_sn
        ):
            return False
        current = registry.async_get(raw_device_id)
        if selected is None:
            return current is None and raw_device_id == device_id
        return bool(
            current is not None
            and not child_identity_changed
            and _resolve_jackery_device_id(hass, raw_device_id) == device_id
            and current.config_entry_id == selected_entry
            and frozenset(current.identifiers) == selected_identifiers
            and getattr(current, "created_at", None) == selected_created
            and (
                getattr(current, "parent_device_id", None)
                or getattr(current, "via_device_id", None)
            )
            == parent_link
            and (parent_link is None or registry.async_get(parent_link) is not None)
            and parent_registry_identity_is_current()
            and (
                not is_child
                or (
                    _ble_ota_child_serial(coordinator, device_id, current) == target_sn
                    and coordinator.battery_pack_identity_revision(device_id, target_sn)
                    == pack_revision
                )
            )
        )

    unsubscribe = (
        coordinator.async_add_listener(observe_child_identity) if is_child else None
    )
    try:
        result = await coordinator.async_check_ble_ota_versions(
            device_id,
            target_sn=target_sn,
            parent_sn=parent_sn,
            identity_is_current=selected_identity_is_current,
            force_fresh_report=force_fresh_report,
        )
    except JackeryAuthError as err:
        raise ConfigEntryAuthFailed from err
    except (JackeryError, LookupError) as err:
        raise _service_action_error(
            "check_ble_ota_versions_failed",
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    finally:
        if unsubscribe is not None:
            unsubscribe()
    if selected is not None:
        current = registry.async_get(raw_device_id)
        if current is None:
            msg = "Selected device identity changed during the BLE update check"
            raise HomeAssistantError(msg)
        registry_changed = (
            child_identity_changed
            or _resolve_jackery_device_id(hass, raw_device_id) != device_id
            or current.config_entry_id != selected.config_entry_id
            or current.identifiers != selected.identifiers
            or getattr(current, "created_at", None)
            != getattr(selected, "created_at", None)
        )
        relationship_changed = (
            (
                getattr(current, "parent_device_id", None)
                or getattr(current, "via_device_id", None)
            )
            != parent_link
            or (parent_link is not None and registry.async_get(parent_link) is None)
            or (
                is_child
                and _ble_ota_child_serial(coordinator, device_id, current) != target_sn
            )
            or (
                is_child
                and coordinator.battery_pack_identity_revision(device_id, target_sn)
                != pack_revision
            )
        )
        if (
            registry_changed
            or relationship_changed
            or not selected_identity_is_current()
        ):
            msg = "Selected device identity changed during the BLE update check"
            raise HomeAssistantError(msg)
    return cast("ServiceResponse", result)


async def _async_start_ground_ble_ota_update(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    raw_device_id: str,
    candidate: dict[str, Any],
) -> ServiceResponse:
    """Run Ground OTA while its parent and accessory identity remain stable."""
    target = candidate.get(FIELD_DEVICE_SN)
    identity = coordinator.ground_ble_identity(device_id)
    evidence = coordinator._ble_ota_registry_evidence(device_id, raw_device_id)  # ruff: ignore[private-member-access]
    if (
        not isinstance(target, str)
        or identity is None
        or coordinator.ground_ble_ota_target(device_id) != target
        or evidence is None
    ):
        msg = "Ground OTA target or parent identity is not current"
        raise HomeAssistantError(msg)
    model = coordinator._ble_ota_model_evidence(device_id)  # ruff: ignore[private-member-access]
    key = coordinator.device_bluetooth_key(device_id)
    changed = False

    def observe_identity() -> None:
        nonlocal changed
        properties = (coordinator.data or {}).get(device_id, {}).get("properties", {})
        serial = properties.get("grdAccSn") if isinstance(properties, dict) else None
        if (
            not isinstance(serial, str)
            or serial.strip() != target
            or coordinator._ble_ota_model_evidence(device_id) != model  # ruff: ignore[private-member-access]
            or coordinator.device_bluetooth_key(device_id) != key
        ):
            changed = True

    def identity_is_current() -> bool:
        current = coordinator.ground_ble_identity(device_id, require_connected=False)
        return bool(
            not changed
            and current is not None
            and current.serial == target
            and current.generation == identity.generation
            and current.first_sequence == identity.first_sequence
            and coordinator._ble_ota_model_evidence(device_id) == model  # ruff: ignore[private-member-access]
            and coordinator.device_bluetooth_key(device_id) == key
            and coordinator._ble_ota_registry_evidence(  # ruff: ignore[private-member-access]
                device_id, raw_device_id
            )
            == evidence
        )

    unsubscribe = coordinator.async_add_listener(observe_identity)
    try:
        status = await coordinator.async_start_box_ble_ota(
            device_id,
            target_sn=target,
            candidate=candidate,
            identity_is_current=identity_is_current,
            selected_device_id=raw_device_id,
            target_kind="ground_accessory",
        )
    except JackeryAuthError as err:
        raise ConfigEntryAuthFailed from err
    except (JackeryError, LookupError, RuntimeError, ValueError, TimeoutError) as err:
        raise _service_action_error(
            "start_ble_ota_update_failed",
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    finally:
        unsubscribe()
    return cast("ServiceResponse", {"status": status})


async def _async_handle_start_ble_ota_update(  # ruff: ignore[too-many-locals]  # ruff: ignore[too-many-statements]
    hass: HomeAssistant,
    call: ServiceCall,
    *,
    _registry_retry: bool = False,
) -> ServiceResponse:
    """Run the selected device's current Box BLE firmware update."""
    raw_device_id = call.data[SERVICE_FIELD_DEVICE_ID]
    device_id = _device_id_from_service(
        hass, raw_device_id, translation_key="start_ble_ota_update_failed"
    )
    registry = dr.async_get(hass)
    selected = registry.async_get(raw_device_id)
    coordinator = _coordinator_for_device(
        hass,
        device_id,
        config_entry_id=selected.config_entry_id if selected is not None else None,
    )
    if coordinator is None:
        raise _service_validation_error(
            "start_ble_ota_update_failed",
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    parent_link = (
        getattr(selected, "parent_device_id", None)
        or getattr(selected, "via_device_id", None)
        if selected is not None
        else None
    )
    selected_identifiers = (
        frozenset(selected.identifiers) if selected is not None else None
    )
    selected_modified = selected.modified_at if selected is not None else None
    selected_entry = selected.config_entry_id if selected is not None else None
    selected_created = getattr(selected, "created_at", None)
    selected_serial = _registry_device_serial_number(selected)
    parent_epoch = getattr(coordinator, "_http_parent_identity_epochs", {}).get(
        device_id, 0
    )
    system_epoch = getattr(coordinator, "_system_binding_epochs", {}).get(device_id, 0)

    def selected_identity_is_current(*, check_revision: bool = False) -> bool:
        if (
            _coordinator_for_device(hass, device_id, config_entry_id=selected_entry)
            is not coordinator
            or getattr(coordinator, "_http_parent_identity_epochs", {}).get(
                device_id, 0
            )
            != parent_epoch
            or getattr(coordinator, "_system_binding_epochs", {}).get(device_id, 0)
            != system_epoch
        ):
            return False
        current = registry.async_get(raw_device_id)
        if selected is None:
            return current is None and raw_device_id == device_id
        if (
            current is None  # ruff: ignore[too-many-boolean-expressions]
            or getattr(current, "created_at", None) != selected_created
            or _registry_device_serial_number(current) != selected_serial
            or (check_revision and current.modified_at != selected_modified)
            or current.config_entry_id != selected_entry
            or frozenset(current.identifiers) != selected_identifiers
            or _resolve_jackery_device_id(hass, raw_device_id) != device_id
            or (
                getattr(current, "parent_device_id", None)
                or getattr(current, "via_device_id", None)
            )
            != parent_link
        ):
            return False
        return parent_link is None or registry.async_get(parent_link) is not None

    def can_repeat_preflight() -> bool:
        """Allow one new read-only check after a registry metadata update."""
        current = registry.async_get(raw_device_id)
        return bool(
            not _registry_retry
            and selected is not None
            and current is not None
            and current.modified_at != selected_modified
            and current.config_entry_id == selected_entry
            and frozenset(current.identifiers) == selected_identifiers
            and getattr(current, "created_at", None) == selected_created
            and _registry_device_serial_number(current) == selected_serial
            and (
                getattr(current, "parent_device_id", None)
                or getattr(current, "via_device_id", None)
            )
            == parent_link
            and (parent_link is None or registry.async_get(parent_link) is not None)
            and _resolve_jackery_device_id(hass, raw_device_id) == device_id
            and _coordinator_for_device(hass, device_id, config_entry_id=selected_entry)
            is coordinator
            and getattr(coordinator, "_http_parent_identity_epochs", {}).get(
                device_id, 0
            )
            == parent_epoch
            and getattr(coordinator, "_system_binding_epochs", {}).get(device_id, 0)
            == system_epoch
        )

    try:
        checked = await _async_handle_check_ble_ota_versions(
            hass, call, force_fresh_report=_registry_retry
        )
    except ConfigEntryAuthFailed:
        raise
    except HomeAssistantError:
        if can_repeat_preflight():
            return await _async_handle_start_ble_ota_update(
                hass, call, _registry_retry=True
            )
        raise
    if not selected_identity_is_current(check_revision=True):
        if can_repeat_preflight():
            return await _async_handle_start_ble_ota_update(
                hass, call, _registry_retry=True
            )
        msg = "Selected device identity changed during the BLE update check"
        raise HomeAssistantError(msg)
    candidates = checked.get("data") if isinstance(checked, dict) else None
    if (
        not isinstance(candidates, list)
        or len(candidates) != 1
        or not isinstance(candidates[0], dict)
    ):
        msg = "A unique BLE OTA update candidate is required"
        raise HomeAssistantError(msg)
    candidate = candidates[0]
    if call.data.get("target") == "ground_accessory":
        return await _async_start_ground_ble_ota_update(
            coordinator, device_id, raw_device_id, candidate
        )
    target_sn = candidate.get(FIELD_DEVICE_SN)
    if not isinstance(target_sn, str) or not target_sn:
        msg = "A BLE OTA target serial is required"
        raise HomeAssistantError(msg)
    pack_revision = coordinator.battery_pack_identity_revision(device_id, target_sn)

    def identity_is_current() -> bool:
        if not selected_identity_is_current():
            return False
        current = registry.async_get(raw_device_id)
        if parent_link is not None:
            return (
                current is not None
                and _ble_ota_child_serial(coordinator, device_id, current) == target_sn
                and coordinator.battery_pack_identity_revision(device_id, target_sn)
                == pack_revision
            )
        return True

    try:
        status = await coordinator.async_start_box_ble_ota(
            device_id,
            target_sn=target_sn,
            candidate=candidate,
            identity_is_current=identity_is_current,
            selected_device_id=raw_device_id,
        )
    except JackeryAuthError as err:
        raise ConfigEntryAuthFailed from err
    except (JackeryError, LookupError, RuntimeError, ValueError, TimeoutError) as err:
        raise _service_action_error(
            "start_ble_ota_update_failed",
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return cast("ServiceResponse", {"status": status})


async def _async_handle_send_ble_command(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Send a BLE command frame to a Jackery device.

    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        call (ServiceCall): Service call containing the following data fields:
            - SERVICE_FIELD_DEVICE_ID: registry or Jackery device identifier resolved
              to the parent Jackery numeric id.
            - SERVICE_FIELD_CMD: numeric command identifier.
            - SERVICE_FIELD_BODY: mapping or JSON object string.
            - SERVICE_FIELD_FLAGS: optional integer flags (default 0).
            - SERVICE_FIELD_WAIT_FOR_ACK: optional ACK wait flag (default False).
            - SERVICE_FIELD_ACK_TIMEOUT: optional timeout in seconds (default 5.0).

    Raises:
        ServiceValidationError: If the target or command input is invalid.
        HomeAssistantError: If the BLE action cannot be completed.
    """
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="send_ble_command_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "send_ble_command_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    body = _ble_body_from_service(call.data[SERVICE_FIELD_BODY], device_id)
    try:
        sent = await coordinator.async_send_ble_command(
            device_id,
            cmd=_service_int(
                call.data[SERVICE_FIELD_CMD],
                field_name=SERVICE_FIELD_CMD,
                translation_key="send_ble_command_failed",
                device_id=device_id,
                bounds=(1, 65535),
            ),
            body=body,
            flags=_service_int(
                call.data.get(SERVICE_FIELD_FLAGS, 0),
                field_name=SERVICE_FIELD_FLAGS,
                translation_key="send_ble_command_failed",
                device_id=device_id,
                bounds=(0, 65535),
            ),
            wait_for_ack=_service_bool(
                call.data.get(SERVICE_FIELD_WAIT_FOR_ACK, False),
                field_name=SERVICE_FIELD_WAIT_FOR_ACK,
                translation_key="send_ble_command_failed",
                device_id=device_id,
            ),
            ack_timeout_sec=_service_float(
                call.data.get(SERVICE_FIELD_ACK_TIMEOUT, 5.0),
                field_name=SERVICE_FIELD_ACK_TIMEOUT,
                translation_key="send_ble_command_failed",
                device_id=device_id,
                bounds=(0.5, 60.0),
            ),
            connect_timeout_sec=_BLE_SERVICE_CONNECT_TIMEOUT_SEC,
        )
    except ConfigEntryAuthFailed:
        raise
    except ServiceValidationError:
        raise
    except (HomeAssistantError, RuntimeError, ValueError) as err:
        msg = "send_ble_command_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    if not sent:
        msg = "send_ble_command_failed"
        pending = getattr(coordinator, "_ble_ota_pending", None)
        ota_blocked = (
            isinstance(pending, dict)
            and device_id in pending
            and coordinator._ble_ota_pending_blocked(device_id)  # ruff: ignore[private-member-access]
        )
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=(
                "BLE OTA recovery is pending for this device"
                if ota_blocked
                else "BLE writes are disabled or no active BLE session exists after "
                "waiting for reconnect"
            ),
        )


async def _async_handle_set_device_nickname(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Set a device nickname through the matching coordinator."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="set_device_nickname_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "set_device_nickname_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    nickname = _service_required_text(
        call.data[SERVICE_FIELD_NICKNAME],
        field_name=SERVICE_FIELD_NICKNAME,
        translation_key="set_device_nickname_failed",
        device_id=device_id,
        max_length=64,
    )
    try:
        await coordinator.async_set_device_nickname(device_id, nickname)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while setting a device nickname. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (HomeAssistantError, JackeryError) as err:
        msg = "set_device_nickname_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_set_account_nickname(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Set the account display nickname on the selected account.

    The device id resolves only *which* account's coordinator handles the call;
    the cloud write itself is account-scoped (the nickname is never ingested into
    coordinator data, so there is no local echo to patch).
    """
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="set_account_nickname_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "set_account_nickname_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    nickname = _service_required_text(
        call.data[SERVICE_FIELD_ACCOUNT_NICKNAME],
        field_name=SERVICE_FIELD_ACCOUNT_NICKNAME,
        translation_key="set_account_nickname_failed",
        device_id=device_id,
        max_length=64,
    )
    try:
        await coordinator.async_update_user_info(nickname)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while setting the account nickname. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (HomeAssistantError, JackeryError) as err:
        msg = "set_account_nickname_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_unbind_device(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Unbind a device from the account and refresh so the device drops.

    Destructive: on success the coordinator is asked to refresh so the now-removed
    device disappears from Home Assistant.
    """
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="unbind_device_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "unbind_device_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        await coordinator.async_unbind_device(device_id)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while unbinding a device. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except JackeryError as err:
        msg = "unbind_device_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_bind_device(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Bind a device to the selected account through the matching coordinator."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="bind_device_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "bind_device_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    bind_key = call.data[SERVICE_FIELD_BIND_KEY]
    target_dev_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_TARGET_DEV_ID],
        translation_key="bind_device_failed",
    )
    guid = _service_required_text(
        call.data[SERVICE_FIELD_GUID],
        field_name=SERVICE_FIELD_GUID,
        translation_key="bind_device_failed",
        device_id=device_id,
        max_length=128,
    )
    try:
        await coordinator.async_bind_device(
            bind_key=bind_key,
            dev_id=target_dev_id,
            guid=guid,
            timezone_offset=call.data[SERVICE_FIELD_TIMEZONE_OFFSET],
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while binding a device. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except JackeryError as err:
        msg = "bind_device_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_bind_smart_part(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Bind a smart accessory to the device via the matching coordinator."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="bind_smart_part_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "bind_smart_part_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="bind_smart_part_failed",
        service_name=SERVICE_BIND_SMART_PART,
    )
    accessory_sn = _service_required_text(
        call.data[SERVICE_FIELD_ACCESSORY_SN],
        field_name=SERVICE_FIELD_ACCESSORY_SN,
        translation_key="bind_smart_part_failed",
        device_id=device_id,
        max_length=64,
    )
    try:
        await coordinator.async_bind_smart_part(
            device_id, accessory_sn, smart=call.data["smart"]
        )
    except ConfigEntryAuthFailed:
        raise
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while binding a smart part. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "bind_smart_part_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_unbind_smart_part(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Unbind a smart accessory from the device via the matching coordinator."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="unbind_smart_part_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "unbind_smart_part_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="unbind_smart_part_failed",
        service_name=SERVICE_UNBIND_SMART_PART,
    )
    accessory_sn = _service_required_text(
        call.data[SERVICE_FIELD_ACCESSORY_SN],
        field_name=SERVICE_FIELD_ACCESSORY_SN,
        translation_key="unbind_smart_part_failed",
        device_id=device_id,
        max_length=64,
    )
    try:
        await coordinator.async_unbind_smart_part(device_id, accessory_sn)
    except ConfigEntryAuthFailed:
        raise
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while unbinding a smart part. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "unbind_smart_part_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


def _render_share_qr_png_data_uri(qr_code_id: str, user_id: str | None) -> str:
    """Encode the App's QrCodeApi bean JSON as a PNG data URI."""
    buffer = io.BytesIO()
    invitation = {FIELD_QR_CODE_ID: qr_code_id}
    if user_id is not None:
        invitation[FIELD_USER_ID] = user_id
    segno.make(
        json.dumps(invitation, separators=(",", ":"), ensure_ascii=False), error="m"
    ).save(buffer, kind="png", scale=6, border=2)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


async def _notify_share_qr_code(
    hass: HomeAssistant,
    *,
    qr_code_id: object,
    user_id: object,
) -> None:
    """Create a persistent notification with a scannable share QR image.

    Best-effort: a render or notification failure must never fail the service,
    which still returns the ``{qr_code_id, user_id}`` envelope. The notification
    carries the App's JSON bean, from which the scanner extracts ``qrCodeId``.
    """
    if not isinstance(qr_code_id, str) or not qr_code_id:
        return
    try:
        data_uri = await hass.async_add_executor_job(
            _render_share_qr_png_data_uri,
            qr_code_id,
            user_id if isinstance(user_id, str) else None,
        )
        message = (
            f"![Share QR code]({data_uri})\n\n"
            f"Sharing invitation for this Jackery account. The device owner can "
            f"scan it in the Jackery app's device-sharing screen.\n\n"
            f"qrCodeId: `{qr_code_id}`\n"
            f"userId: `{user_id}`"
        )
        persistent_notification.async_create(
            hass,
            message,
            title="Jackery SolarVault share QR code",
        )
    except Exception:
        _LOGGER.debug(
            "Failed to render or publish the share QR-code notification",
            exc_info=True,
        )


async def _async_handle_get_share_qr_code(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return the share QR code for the account that owns the selected device."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="get_share_qr_code_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "get_share_qr_code_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        data = await coordinator.async_get_share_qr_code()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while fetching the share QR code. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "get_share_qr_code_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    qr_code_id = data.get(FIELD_QR_CODE_ID)
    user_id = data.get(FIELD_USER_ID)
    await _notify_share_qr_code(hass, qr_code_id=qr_code_id, user_id=user_id)
    return {
        SERVICE_RESPONSE_QR_CODE_ID: qr_code_id,
        SERVICE_RESPONSE_USER_ID: user_id,
    }


async def _async_handle_get_smart_schedule_prediction(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return the AI smart-schedule prediction for the selected device."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="get_smart_schedule_prediction_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "get_smart_schedule_prediction_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="get_smart_schedule_prediction_failed",
        service_name=SERVICE_GET_SMART_SCHEDULE_PREDICTION,
    )
    try:
        prediction = await coordinator.async_get_smart_schedule_prediction(device_id)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while fetching the smart schedule "
            "prediction. Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "get_smart_schedule_prediction_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"prediction": cast("JsonValueType", prediction)}


async def _async_handle_unbind_accessories(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Unbind smart accessories by bind id on the owning account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="unbind_accessories_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "unbind_accessories_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    bind_ids = [str(item) for item in call.data[SERVICE_FIELD_BIND_IDS]]
    try:
        result = await coordinator.async_unbind_accessories(bind_ids)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while unbinding accessories. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "unbind_accessories_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"result": cast("JsonValueType", result)}


async def _async_handle_set_ac_nickname(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Rename one AC output port of the selected portable device."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="set_ac_nickname_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "set_ac_nickname_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    name = _service_required_text(
        call.data[SERVICE_FIELD_NICKNAME],
        field_name=SERVICE_FIELD_NICKNAME,
        translation_key="set_ac_nickname_failed",
        device_id=device_id,
        max_length=64,
    )
    try:
        await coordinator.async_set_ac_nickname(
            device_id,
            ac_port=int(call.data[SERVICE_FIELD_AC_PORT]),
            name=name,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while renaming the AC port. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "set_ac_nickname_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_report_device_timezone(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Report the selected device's timezone to the Jackery cloud."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="report_device_timezone_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "report_device_timezone_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        await coordinator.async_report_device_timezone(
            device_id,
            zone_id=call.data[SERVICE_FIELD_ZONE_ID],
            time_offset=int(call.data[SERVICE_FIELD_TIMEZONE_OFFSET]),
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reporting the device "
            "timezone. Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "report_device_timezone_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_accept_shared_device(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Accept a shared device on the account that owns the selected device."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="accept_shared_device_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "accept_shared_device_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    target_dev_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_TARGET_DEV_ID],
        translation_key="accept_shared_device_failed",
    )
    qr_code_id = _service_required_text(
        call.data[SERVICE_FIELD_QR_CODE_ID],
        field_name=SERVICE_FIELD_QR_CODE_ID,
        translation_key="accept_shared_device_failed",
        device_id=device_id,
        max_length=256,
    )
    try:
        await coordinator.async_accept_shared_device(
            dev_id=target_dev_id,
            qr_code_id=qr_code_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while accepting a shared device. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "accept_shared_device_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_list_shared_devices(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return the devices shared with the account that owns the selected device."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_shared_devices_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_shared_devices_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        devices = await coordinator.async_list_shared_devices()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while listing shared devices. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "list_shared_devices_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    # The cloud payload is JSON-decoded, so its entries are JSON-native at runtime;
    # cast documents that trust boundary for the ServiceResponse contract.
    return {"devices": cast("list[JsonValueType]", devices)}


async def _async_handle_list_shared_managers(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return the managers for a shared-device binding on the matching account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_shared_managers_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_shared_managers_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    bind_user_id = _service_required_text(
        call.data[SERVICE_FIELD_BIND_USER_ID],
        field_name=SERVICE_FIELD_BIND_USER_ID,
        translation_key="list_shared_managers_failed",
        device_id=device_id,
        max_length=128,
    )
    level = _service_int(
        call.data.get(SERVICE_FIELD_LEVEL, 0),
        field_name=SERVICE_FIELD_LEVEL,
        translation_key="list_shared_managers_failed",
        device_id=device_id,
        bounds=(0, 65535),
    )
    try:
        managers = await coordinator.async_list_shared_managers(
            bind_user_id=bind_user_id,
            level=level,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while listing shared managers. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "list_shared_managers_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    # The cloud payload is JSON-decoded, so its entries are JSON-native at runtime;
    # cast documents that trust boundary for the ServiceResponse contract.
    return {"managers": cast("list[JsonValueType]", managers)}


async def _async_handle_remove_shared_access(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Remove a single shared access and refresh so the change reflects in HA.

    Destructive: the coordinator is refreshed only after the cloud call succeeds.
    """
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="remove_shared_access_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "remove_shared_access_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    bind_user_id = _service_required_text(
        call.data[SERVICE_FIELD_BIND_USER_ID],
        field_name=SERVICE_FIELD_BIND_USER_ID,
        translation_key="remove_shared_access_failed",
        device_id=device_id,
        max_length=128,
    )
    target_device_id = _device_id_from_service(
        hass,
        call.data.get(SERVICE_FIELD_TARGET_DEV_ID, device_id),
        translation_key="remove_shared_access_failed",
    )
    try:
        await coordinator.async_remove_shared_access(
            bind_user_id=bind_user_id,
            device_id=target_device_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while removing shared access. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "remove_shared_access_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_remove_all_shared_access(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Remove all shared access for a user at a share level and refresh on success.

    Destructive: the coordinator is refreshed only after the cloud call succeeds.
    """
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="remove_all_shared_access_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "remove_all_shared_access_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    bind_user_id = _service_required_text(
        call.data[SERVICE_FIELD_BIND_USER_ID],
        field_name=SERVICE_FIELD_BIND_USER_ID,
        translation_key="remove_all_shared_access_failed",
        device_id=device_id,
        max_length=128,
    )
    level = _service_int(
        call.data.get(SERVICE_FIELD_LEVEL, 0),
        field_name=SERVICE_FIELD_LEVEL,
        translation_key="remove_all_shared_access_failed",
        device_id=device_id,
        bounds=(0, 65535),
    )
    try:
        await coordinator.async_remove_all_shared_access(
            bind_user_id=bind_user_id,
            level=level,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while removing all shared access. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "remove_all_shared_access_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_get_shelly_auth_url(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return the Jackery-owned Shelly OAuth authorization payload."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="get_shelly_auth_url_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "get_shelly_auth_url_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        auth_url = await coordinator.async_get_shelly_auth_url()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading Shelly auth URL. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "get_shelly_auth_url_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"auth_url": cast("JsonValueType", auth_url)}


async def _async_handle_list_shelly_devices(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return Shelly Cloud devices linked to the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_shelly_devices_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_shelly_devices_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        devices = await coordinator.async_get_shelly_devices()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while listing Shelly devices. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "list_shelly_devices_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"devices": cast("list[JsonValueType]", devices)}


async def _async_handle_unbind_shelly_device(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Unbind one Shelly Cloud device through the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="unbind_shelly_device_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "unbind_shelly_device_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    binding_id = _service_required_text(
        call.data[SERVICE_FIELD_BINDING_ID],
        field_name=SERVICE_FIELD_BINDING_ID,
        translation_key="unbind_shelly_device_failed",
        device_id=device_id,
        max_length=128,
    )
    shelly_device_id = _service_required_text(
        call.data[SERVICE_FIELD_SHELLY_DEVICE_ID],
        field_name=SERVICE_FIELD_SHELLY_DEVICE_ID,
        translation_key="unbind_shelly_device_failed",
        device_id=device_id,
        max_length=128,
    )
    try:
        accepted = await coordinator.async_unbind_shelly_device(
            binding_id=binding_id,
            shelly_device_id=shelly_device_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while unbinding a Shelly device. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "unbind_shelly_device_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    if not accepted:
        msg = "unbind_shelly_device_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="Jackery API did not accept the Shelly device unbind request",
        )


async def _async_handle_unbind_shelly_account(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Unbind the Shelly account through the account owning the selected device."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="unbind_shelly_account_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "unbind_shelly_account_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        accepted = await coordinator.async_unbind_shelly_account()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while unbinding the Shelly account. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "unbind_shelly_account_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    if not accepted:
        msg = "unbind_shelly_account_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="Jackery API did not accept the Shelly account unbind request",
        )


async def _async_handle_list_shelly_binding_failures(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return Shelly binding failures through the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_shelly_binding_failures_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_shelly_binding_failures_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    state = _service_optional_text(
        call.data.get(SERVICE_FIELD_STATE, ""),
        field_name=SERVICE_FIELD_STATE,
        translation_key="list_shelly_binding_failures_failed",
        device_id=device_id,
        max_length=64,
    ).strip()
    try:
        failures = await coordinator.async_get_shelly_binding_failures(state=state)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while listing Shelly binding failures. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "list_shelly_binding_failures_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"failures": cast("JsonValueType", failures)}


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


_ServiceHandler = Callable[
    ["HomeAssistant", "ServiceCall"],
    Coroutine[Any, Any, "ServiceResponse | None"],
]


class _ServiceRegistration(NamedTuple):
    """One declarative service registration row.

    ``supports_response`` lets read services opt into ``SupportsResponse.ONLY`` so
    their handler return value is surfaced to the caller; all command services keep
    the default ``SupportsResponse.NONE``.
    """

    name: str
    handler: _ServiceHandler
    schema: vol.Schema
    supports_response: SupportsResponse = SupportsResponse.NONE


def _service_registrations() -> tuple[_ServiceRegistration, ...]:
    """Return the declarative table of domain-scoped service actions.

    Kept as a single declarative table so adding a service is one row instead of a
    fresh ``if not has_service`` branch — that branch-per-service pattern pushed
    ``async_setup_services`` past the complexity gates. Rows default to
    ``SupportsResponse.NONE``; read services pass ``SupportsResponse.ONLY``.
    """
    return (
        _ServiceRegistration(
            SERVICE_RENAME_SYSTEM, _async_handle_rename, RENAME_SCHEMA
        ),
        _ServiceRegistration(
            SERVICE_REFRESH_WEATHER_PLAN,
            _async_handle_refresh_weather_plan,
            REFRESH_WEATHER_PLAN_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_REFRESH_SUBDEVICES,
            _async_handle_refresh_subdevices,
            REFRESH_SUBDEVICES_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_DELETE_STORM_ALERT,
            _async_handle_delete_storm_alert,
            DELETE_STORM_ALERT_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_SET_STORM_ALERT_LOCATION,
            _async_handle_set_storm_alert_location,
            SET_STORM_ALERT_LOCATION_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_SET_THIRD_PARTY_MQTT_CONFIG,
            _async_handle_set_third_party_mqtt_config,
            SET_THIRD_PARTY_MQTT_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG,
            _async_handle_query_third_party_mqtt_config,
            QUERY_THIRD_PARTY_MQTT_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_SEND_BLE_COMMAND,
            _async_handle_send_ble_command,
            SEND_BLE_COMMAND_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_CHECK_BLE_OTA_VERSIONS,
            _async_handle_check_ble_ota_versions,
            CHECK_BLE_OTA_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_START_BLE_OTA_UPDATE,
            _async_handle_start_ble_ota_update,
            CHECK_BLE_OTA_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_SEND_DEVICE_SCHEDULE,
            _async_handle_send_device_schedule,
            SEND_DEVICE_SCHEDULE_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_QUERY_TOU_PLAN,
            _async_handle_query_tou_plan,
            QUERY_TOU_PLAN_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_SAVE_TOU_PLAN,
            _async_handle_save_tou_plan,
            SAVE_TOU_PLAN_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_LIST_CURRENCIES,
            _async_handle_list_currencies,
            CURRENCY_READ_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_GET_DEVICE_CURRENCY,
            _async_handle_get_device_currency,
            CURRENCY_READ_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_BIND_CURRENCY,
            _async_handle_bind_currency,
            BIND_CURRENCY_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_LIST_ACCESSORIES,
            _async_handle_list_accessories,
            LIST_ACCESSORIES_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_CHECK_ACCESSORIES_EXIST,
            _async_handle_check_accessories_exist,
            CHECK_ACCESSORIES_EXIST_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_CHECK_JACKERY_ACCESSORIES_EXIST,
            _async_handle_check_jackery_accessories_exist,
            CHECK_JACKERY_ACCESSORIES_EXIST_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_SET_ACCESSORY_NAME,
            _async_handle_set_accessory_name,
            SET_ACCESSORY_NAME_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_GET_DYNAMIC_PRICE_LOGIN_URL,
            _async_handle_get_dynamic_price_login_url,
            DYNAMIC_PRICE_LOGIN_URL_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_LIST_DYNAMIC_PRICE_CONTRACTS,
            _async_handle_list_dynamic_price_contracts,
            DYNAMIC_PRICE_CONTRACT_LIST_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_SAVE_DYNAMIC_PRICE_CONTRACT_AUTH,
            _async_handle_save_dynamic_price_contract_auth,
            DYNAMIC_PRICE_CONTRACT_AUTH_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_CANCEL_DYNAMIC_PRICE_CONTRACT_AUTH,
            _async_handle_cancel_dynamic_price_contract_auth,
            DYNAMIC_PRICE_CANCEL_CONTRACT_AUTH_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_SAVE_DYNAMIC_PRICE_LOCATION_ID,
            _async_handle_save_dynamic_price_location_id,
            DYNAMIC_PRICE_LOCATION_ID_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_QUERY_SOCKET_STAT,
            _async_handle_query_socket_stat,
            QUERY_SOCKET_STAT_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_LIST_COUNTRY_ZONES,
            _async_handle_list_country_zones,
            LIST_COUNTRY_ZONES_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_LIST_GRID_STANDARDS,
            _async_handle_list_grid_standards,
            LIST_GRID_STANDARDS_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_CHECK_SYSTEM_BOUND,
            _async_handle_check_system_bound,
            CHECK_SYSTEM_BOUND_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_GET_ALARM_DETAIL,
            _async_handle_get_alarm_detail,
            GET_ALARM_DETAIL_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_GET_BATTERY_PASSPORT,
            _async_handle_get_battery_passport,
            GET_BATTERY_PASSPORT_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_GET_FAULT_LOG,
            _async_handle_get_fault_log,
            GET_FAULT_LOG_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_LIST_NOTIFICATIONS,
            _async_handle_list_notifications,
            LIST_NOTIFICATIONS_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_GET_UNREAD_COUNT,
            _async_handle_get_unread_count,
            ACCOUNT_READ_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_GET_PUSH_CONFIG,
            _async_handle_get_push_config,
            ACCOUNT_READ_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_SET_PUSH_CONFIG,
            _async_handle_set_push_config,
            SET_PUSH_CONFIG_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_SYNC_ALERTS,
            _async_handle_sync_alerts,
            SYNC_ALERTS_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_SYNC_OFFLINE_STATISTICS,
            _async_handle_sync_offline_statistics,
            SYNC_OFFLINE_STATISTICS_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_QUERY_CHARGE_REPORT,
            _async_handle_query_charge_report,
            QUERY_CHARGE_REPORT_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_QUERY_CUTOFF_STAT,
            _async_handle_query_cutoff_stat,
            QUERY_CUTOFF_STAT_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_QUERY_SOC_STAT,
            _async_handle_query_soc_stat,
            QUERY_SOC_STAT_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_QUERY_CARBON_STAT,
            _async_handle_query_carbon_stat,
            QUERY_CARBON_STAT_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_QUERY_PROFIT_STAT,
            _async_handle_query_profit_stat,
            QUERY_PROFIT_STAT_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_QUERY_BOX_STAT,
            _async_handle_query_box_stat,
            QUERY_BOX_STAT_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_CHECK_APP_VERSION,
            _async_handle_check_app_version,
            CHECK_APP_VERSION_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_LIST_BANNERS,
            _async_handle_list_banners,
            ACCOUNT_READ_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_SUBMIT_FEEDBACK,
            _async_handle_submit_feedback,
            SUBMIT_FEEDBACK_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_LIST_FAQS,
            _async_handle_list_faqs,
            ACCOUNT_READ_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_LIST_FAQ_ANSWERS,
            _async_handle_list_faq_answers,
            ACCOUNT_READ_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_CHECK_PRIVACY_UPDATE,
            _async_handle_check_privacy_update,
            ACCOUNT_READ_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_AGREE_PRIVACY_CONSENT,
            _async_handle_agree_privacy_consent,
            AGREE_PRIVACY_CONSENT_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_GET_PRODUCT_INSTRUCTION,
            _async_handle_get_product_instruction,
            GET_PRODUCT_INSTRUCTION_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_GET_USER_INFO,
            _async_handle_get_user_info,
            ACCOUNT_READ_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_INSERT_ELECTRICITY_STRATEGY,
            _async_handle_insert_electricity_strategy,
            ELECTRICITY_STRATEGY_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_UPDATE_ELECTRICITY_STRATEGY,
            _async_handle_update_electricity_strategy,
            ELECTRICITY_STRATEGY_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_DELETE_ELECTRICITY_STRATEGY,
            _async_handle_delete_electricity_strategy,
            ELECTRICITY_STRATEGY_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_QUERY_ELECTRICITY_STRATEGY,
            _async_handle_query_electricity_strategy,
            QUERY_ELECTRICITY_STRATEGY_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_SET_DEVICE_NICKNAME,
            _async_handle_set_device_nickname,
            SET_DEVICE_NICKNAME_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_SET_ACCOUNT_NICKNAME,
            _async_handle_set_account_nickname,
            SET_ACCOUNT_NICKNAME_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_UNBIND_DEVICE,
            _async_handle_unbind_device,
            UNBIND_DEVICE_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_BIND_DEVICE,
            _async_handle_bind_device,
            BIND_DEVICE_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_BIND_SMART_PART,
            _async_handle_bind_smart_part,
            BIND_SMART_PART_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_UNBIND_SMART_PART,
            _async_handle_unbind_smart_part,
            UNBIND_SMART_PART_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_LIST_SHARED_DEVICES,
            _async_handle_list_shared_devices,
            LIST_SHARED_DEVICES_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_LIST_SHARED_MANAGERS,
            _async_handle_list_shared_managers,
            LIST_SHARED_MANAGERS_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_REMOVE_SHARED_ACCESS,
            _async_handle_remove_shared_access,
            REMOVE_SHARED_ACCESS_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_REMOVE_ALL_SHARED_ACCESS,
            _async_handle_remove_all_shared_access,
            REMOVE_ALL_SHARED_ACCESS_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_GET_SHARE_QR_CODE,
            _async_handle_get_share_qr_code,
            GET_SHARE_QR_CODE_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_GET_SMART_SCHEDULE_PREDICTION,
            _async_handle_get_smart_schedule_prediction,
            GET_SMART_SCHEDULE_PREDICTION_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_UNBIND_ACCESSORIES,
            _async_handle_unbind_accessories,
            UNBIND_ACCESSORIES_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_SET_AC_NICKNAME,
            _async_handle_set_ac_nickname,
            SET_AC_NICKNAME_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_REPORT_DEVICE_TIMEZONE,
            _async_handle_report_device_timezone,
            REPORT_DEVICE_TIMEZONE_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_ACCEPT_SHARED_DEVICE,
            _async_handle_accept_shared_device,
            ACCEPT_SHARED_DEVICE_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_GET_SHELLY_AUTH_URL,
            _async_handle_get_shelly_auth_url,
            GET_SHELLY_AUTH_URL_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_LIST_SHELLY_DEVICES,
            _async_handle_list_shelly_devices,
            LIST_SHELLY_DEVICES_SCHEMA,
            SupportsResponse.ONLY,
        ),
        _ServiceRegistration(
            SERVICE_UNBIND_SHELLY_DEVICE,
            _async_handle_unbind_shelly_device,
            UNBIND_SHELLY_DEVICE_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_UNBIND_SHELLY_ACCOUNT,
            _async_handle_unbind_shelly_account,
            UNBIND_SHELLY_ACCOUNT_SCHEMA,
        ),
        _ServiceRegistration(
            SERVICE_LIST_SHELLY_BINDING_FAILURES,
            _async_handle_list_shelly_binding_failures,
            LIST_SHELLY_BINDING_FAILURES_SCHEMA,
            SupportsResponse.ONLY,
        ),
    )


@callback
async def async_setup_services(hass: HomeAssistant) -> None:  # ruff: ignore[unused-async]
    """Register the integration's domain-scoped Home Assistant services.

    Bind each service to its handler.

    Registers the following services (if not already present) and wires each to the
    integration's internal async handler: rename system, refresh weather plan, delete
    storm alert, set/query third-party MQTT config, send BLE command, and send device
    schedule. Each service is registered with this module's corresponding voluptuous
    schema and forwards validated ServiceCall objects to the integration handlers.
    """

    def _make_handler(
        handler: _ServiceHandler,
    ) -> Callable[[ServiceCall], Coroutine[Any, Any, ServiceResponse | None]]:
        async def _handle(call: ServiceCall) -> ServiceResponse | None:
            try:
                return await handler(hass, call)
            except ConfigEntryAuthFailed:
                coordinator = None
                if device_id := call.data.get(SERVICE_FIELD_DEVICE_ID):
                    coordinator = _coordinator_for_device(
                        hass, _resolve_jackery_device_id(hass, device_id)
                    )
                elif system_id := call.data.get(SERVICE_FIELD_SYSTEM_ID):
                    coordinator = _coordinator_for_system(hass, str(system_id))
                if coordinator is not None and coordinator.config_entry is not None:
                    coordinator.config_entry.async_start_reauth(hass)
                raise

        return _handle

    for registration in _service_registrations():
        if not hass.services.has_service(DOMAIN, registration.name):
            hass.services.async_register(
                DOMAIN,
                registration.name,
                _make_handler(registration.handler),
                schema=Schema(
                    registration.schema.schema,
                    required=registration.schema.required,
                    extra=registration.schema.extra,
                ),
                supports_response=registration.supports_response,
            )


async def _async_handle_send_device_schedule(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Send a device schedule frame to a Jackery device.

    Support TIMER_TASK_ADD, TIMER_TASK_DELETE, TIMER_TASK_UPDATE, and TIMER_TASK_READ.

    Resolves the provided device identifier to the owning Jackery device, parses the
    schedule `body` (accepts a mapping or a JSON object string), and forwards the action
    to the coordinator to send the schedule frame.

    Parameters:
        call (ServiceCall): Service call whose `data` must include:
            - `device_id`: Jackery or Home Assistant registry device identifier.
            - `action_id`: one of 3015, 3016, 3017 or 3018.
            - `body`: schedule mapping or JSON object string.

    Raises:
        ServiceValidationError: if the device cannot be resolved to a coordinator, if
        `body` is invalid, or if sending the schedule fails.
    """
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="send_device_schedule_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "send_device_schedule_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="send_device_schedule_failed",
        service_name=SERVICE_SEND_DEVICE_SCHEDULE,
    )
    body = _ble_body_from_service(call.data[SERVICE_FIELD_BODY], device_id)
    try:
        await coordinator.async_send_device_schedule(
            device_id,
            action_id=_service_int(
                call.data[SERVICE_FIELD_ACTION_ID],
                field_name=SERVICE_FIELD_ACTION_ID,
                translation_key="send_device_schedule_failed",
                device_id=device_id,
                bounds=(1, 65535),
            ),
            body=body,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while sending a device schedule. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "send_device_schedule_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_insert_electricity_strategy(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Add a new electricity strategy plan."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="insert_electricity_strategy_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "insert_electricity_strategy_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    body = _ble_body_from_service(call.data[SERVICE_FIELD_BODY], device_id)
    try:
        await coordinator.async_insert_electricity_strategy(device_id, body)
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "insert_electricity_strategy_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_save_tou_plan(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Save a TOU schedule plan through the Jackery HTTP API."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="save_tou_plan_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "save_tou_plan_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    portable = _raise_if_unsupported_portable_tou(
        coordinator,
        device_id,
        translation_key="save_tou_plan_failed",
        supported_models=PORTABLE_PEAK_THROUGH_MODELS,
    )
    tasks = _tou_tasks_from_service(call.data[SERVICE_FIELD_BODY], device_id)
    if portable:
        _validate_portable_tou_tasks(tasks, device_id)
    try:
        await coordinator.async_save_tou_plan(device_id, tasks)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while saving the TOU plan. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "save_tou_plan_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_query_tou_plan(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return the current TOU schedule plan through the Jackery HTTP API."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="query_tou_plan_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "query_tou_plan_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_unsupported_portable_tou(
        coordinator,
        device_id,
        translation_key="query_tou_plan_failed",
        supported_models=PORTABLE_TOU_QUERY_MODELS,
    )
    try:
        plan = await coordinator.async_query_tou_plan(device_id)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while querying the TOU plan. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "query_tou_plan_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"plan": cast("JsonValueType", plan)}


async def _async_handle_list_currencies(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return currencies supported by the Jackery account owning the device."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_currencies_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_currencies_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        currencies = await coordinator.async_list_currencies()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while listing currencies. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "list_currencies_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"currencies": cast("list[JsonValueType]", currencies)}


async def _async_handle_get_device_currency(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return the selected device's currency payload."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="get_device_currency_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "get_device_currency_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="get_device_currency_failed",
        service_name=SERVICE_GET_DEVICE_CURRENCY,
    )
    try:
        currency = await coordinator.async_get_device_currency(device_id)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading device currency. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "get_device_currency_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"currency": cast("JsonValueType", currency)}


async def _async_handle_bind_currency(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Bind a currency to the selected device through the Jackery HTTP API."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="bind_currency_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "bind_currency_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="bind_currency_failed",
        service_name=SERVICE_BIND_CURRENCY,
    )
    currency = _service_required_text(
        call.data[SERVICE_FIELD_CURRENCY],
        field_name=SERVICE_FIELD_CURRENCY,
        translation_key="bind_currency_failed",
        device_id=device_id,
        max_length=32,
    )
    try:
        await coordinator.async_bind_currency(device_id, currency)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while binding currency. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "bind_currency_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_list_accessories(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return smart accessories for the selected parent device."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_accessories_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_accessories_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="list_accessories_failed",
        service_name=SERVICE_LIST_ACCESSORIES,
    )
    try:
        accessories = await coordinator.async_list_accessories(device_id)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while listing accessories. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "list_accessories_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"accessories": cast("list[JsonValueType]", accessories)}


async def _async_handle_check_accessories_exist(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return the account accessory-existence payload for a devices body."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="check_accessories_exist_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "check_accessories_exist_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="check_accessories_exist_failed",
        service_name=SERVICE_CHECK_ACCESSORIES_EXIST,
    )
    devices = _service_required_text(
        call.data[SERVICE_FIELD_DEVICES],
        field_name=SERVICE_FIELD_DEVICES,
        translation_key="check_accessories_exist_failed",
        device_id=device_id,
        max_length=4096,
    )
    try:
        exists = await coordinator.async_check_accessories_exist(
            devices=devices,
            context_device_id=device_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while checking accessories. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "check_accessories_exist_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"exists": cast("JsonValueType", exists)}


async def _async_handle_check_jackery_accessories_exist(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return Jackery accessory-existence payload for device SN info."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="check_jackery_accessories_exist_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "check_jackery_accessories_exist_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="check_jackery_accessories_exist_failed",
        service_name=SERVICE_CHECK_JACKERY_ACCESSORIES_EXIST,
    )
    device_sn_infos = _service_required_text(
        call.data[SERVICE_FIELD_DEVICE_SN_INFOS],
        field_name=SERVICE_FIELD_DEVICE_SN_INFOS,
        translation_key="check_jackery_accessories_exist_failed",
        device_id=device_id,
        max_length=4096,
    )
    try:
        exists = await coordinator.async_check_jackery_accessories_exist(
            device_sn_infos=device_sn_infos,
            context_device_id=device_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while checking Jackery accessories. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "check_jackery_accessories_exist_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"exists": cast("JsonValueType", exists)}


async def _async_handle_set_accessory_name(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Rename a smart accessory through the selected account's HTTP API."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="set_accessory_name_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "set_accessory_name_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="set_accessory_name_failed",
        service_name=SERVICE_SET_ACCESSORY_NAME,
    )
    accessory_id = _service_required_text(
        call.data[SERVICE_FIELD_ACCESSORY_ID],
        field_name=SERVICE_FIELD_ACCESSORY_ID,
        translation_key="set_accessory_name_failed",
        device_id=device_id,
        max_length=128,
    )
    nickname = _service_required_text(
        call.data[SERVICE_FIELD_NICKNAME],
        field_name=SERVICE_FIELD_NICKNAME,
        translation_key="set_accessory_name_failed",
        device_id=device_id,
        max_length=64,
    )
    try:
        await coordinator.async_set_accessory_name(
            accessory_id=accessory_id,
            nickname=nickname,
            context_device_id=device_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while renaming the accessory. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "set_accessory_name_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_get_dynamic_price_login_url(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return a dynamic-price login URL for the selected device/system."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="get_dynamic_price_login_url_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "get_dynamic_price_login_url_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="get_dynamic_price_login_url_failed",
        service_name=SERVICE_GET_DYNAMIC_PRICE_LOGIN_URL,
    )
    platform_company_id = _service_int(
        call.data[SERVICE_FIELD_PLATFORM_COMPANY_ID],
        field_name=SERVICE_FIELD_PLATFORM_COMPANY_ID,
        translation_key="get_dynamic_price_login_url_failed",
        device_id=device_id,
        bounds=(0, 2147483647),
    )
    try:
        login = await coordinator.async_get_dynamic_price_login_url(
            device_id,
            platform_company_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading the dynamic price "
            "login URL. Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "get_dynamic_price_login_url_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"login": cast("JsonValueType", login)}


async def _async_handle_list_dynamic_price_contracts(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return dynamic-price contracts for a customer/platform pair."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_dynamic_price_contracts_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_dynamic_price_contracts_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    customer_number = _service_required_text(
        call.data[SERVICE_FIELD_CUSTOMER_NUMBER],
        field_name=SERVICE_FIELD_CUSTOMER_NUMBER,
        translation_key="list_dynamic_price_contracts_failed",
        device_id=device_id,
        max_length=128,
    )
    platform_company_id = _service_int(
        call.data[SERVICE_FIELD_PLATFORM_COMPANY_ID],
        field_name=SERVICE_FIELD_PLATFORM_COMPANY_ID,
        translation_key="list_dynamic_price_contracts_failed",
        device_id=device_id,
        bounds=(0, 2147483647),
    )
    try:
        contracts = await coordinator.async_list_dynamic_price_contracts(
            customer_number=customer_number,
            platform_company_id=platform_company_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while listing dynamic price "
            "contracts. Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "list_dynamic_price_contracts_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"contracts": cast("list[JsonValueType]", contracts)}


async def _async_handle_save_dynamic_price_contract_auth(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Save dynamic-price contract authorization for the selected device."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="save_dynamic_price_contract_auth_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "save_dynamic_price_contract_auth_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="save_dynamic_price_contract_auth_failed",
        service_name=SERVICE_SAVE_DYNAMIC_PRICE_CONTRACT_AUTH,
    )
    contract_id = _service_required_text(
        call.data[SERVICE_FIELD_CONTRACT_ID],
        field_name=SERVICE_FIELD_CONTRACT_ID,
        translation_key="save_dynamic_price_contract_auth_failed",
        device_id=device_id,
        max_length=128,
    )
    custom_id = _service_required_text(
        call.data[SERVICE_FIELD_CUSTOM_ID],
        field_name=SERVICE_FIELD_CUSTOM_ID,
        translation_key="save_dynamic_price_contract_auth_failed",
        device_id=device_id,
        max_length=128,
    )
    platform_company_id = _service_int(
        call.data[SERVICE_FIELD_PLATFORM_COMPANY_ID],
        field_name=SERVICE_FIELD_PLATFORM_COMPANY_ID,
        translation_key="save_dynamic_price_contract_auth_failed",
        device_id=device_id,
        bounds=(0, 2147483647),
    )
    try:
        await coordinator.async_save_dynamic_price_contract_auth(
            device_id,
            contract_id=contract_id,
            custom_id=custom_id,
            platform_company_id=platform_company_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while saving dynamic price "
            "contract auth. Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "save_dynamic_price_contract_auth_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_cancel_dynamic_price_contract_auth(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Cancel dynamic-price contract authorization for the selected device."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="cancel_dynamic_price_contract_auth_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "cancel_dynamic_price_contract_auth_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="cancel_dynamic_price_contract_auth_failed",
        service_name=SERVICE_CANCEL_DYNAMIC_PRICE_CONTRACT_AUTH,
    )
    platform_company_id = _service_int(
        call.data[SERVICE_FIELD_PLATFORM_COMPANY_ID],
        field_name=SERVICE_FIELD_PLATFORM_COMPANY_ID,
        translation_key="cancel_dynamic_price_contract_auth_failed",
        device_id=device_id,
        bounds=(0, 2147483647),
    )
    try:
        await coordinator.async_cancel_dynamic_price_contract_auth(
            device_id,
            platform_company_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while canceling dynamic price "
            "contract auth. Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "cancel_dynamic_price_contract_auth_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_save_dynamic_price_location_id(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Save a Flatpeak dynamic-price location token for the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="save_dynamic_price_location_id_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "save_dynamic_price_location_id_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    connect_token = _service_required_text(
        call.data[SERVICE_FIELD_CONNECT_TOKEN],
        field_name=SERVICE_FIELD_CONNECT_TOKEN,
        translation_key="save_dynamic_price_location_id_failed",
        device_id=device_id,
        max_length=4096,
    )
    try:
        await coordinator.async_save_dynamic_price_location_id(
            connect_token=connect_token,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while saving the dynamic price "
            "location token. Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "save_dynamic_price_location_id_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_query_socket_stat(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return app socket chart statistics for an accessory device id."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="query_socket_stat_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "query_socket_stat_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="query_socket_stat_failed",
        service_name=SERVICE_QUERY_SOCKET_STAT,
    )
    target_device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_TARGET_DEV_ID],
        translation_key="query_socket_stat_failed",
    )
    begin_date = _service_required_text(
        call.data[SERVICE_FIELD_BEGIN_DATE],
        field_name=SERVICE_FIELD_BEGIN_DATE,
        translation_key="query_socket_stat_failed",
        device_id=device_id,
        max_length=32,
    )
    end_date = _service_required_text(
        call.data[SERVICE_FIELD_END_DATE],
        field_name=SERVICE_FIELD_END_DATE,
        translation_key="query_socket_stat_failed",
        device_id=device_id,
        max_length=32,
    )
    try:
        socket_stat = await coordinator.async_query_socket_stat(
            target_device_id,
            date_type=call.data[SERVICE_FIELD_DATE_TYPE],
            begin_date=begin_date,
            end_date=end_date,
            context_device_id=device_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while querying socket statistics. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "query_socket_stat_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"socket_stat": cast("JsonValueType", socket_stat)}


async def _async_handle_list_country_zones(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return country/zone entries through the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_country_zones_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_country_zones_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        zones = await coordinator.async_list_country_zones()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while listing country zones. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "list_country_zones_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"zones": cast("list[JsonValueType]", zones)}


async def _async_handle_list_grid_standards(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return grid-connection standards for a country code."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_grid_standards_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_grid_standards_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    country = _service_required_text(
        call.data[SERVICE_FIELD_COUNTRY],
        field_name=SERVICE_FIELD_COUNTRY,
        translation_key="list_grid_standards_failed",
        device_id=device_id,
        max_length=16,
    )
    try:
        standards = await coordinator.async_list_grid_standards(
            country=country,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while listing grid standards. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "list_grid_standards_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"standards": cast("list[JsonValueType]", standards)}


async def _async_handle_check_system_bound(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return whether a bind-key/device-SN/GUID tuple is already bound."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="check_system_bound_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "check_system_bound_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    bind_key = _service_required_text(
        call.data[SERVICE_FIELD_BIND_KEY],
        field_name=SERVICE_FIELD_BIND_KEY,
        translation_key="check_system_bound_failed",
        device_id=device_id,
        max_length=128,
    )
    device_sn = _service_required_text(
        call.data[SERVICE_FIELD_DEVICE_SN],
        field_name=SERVICE_FIELD_DEVICE_SN,
        translation_key="check_system_bound_failed",
        device_id=device_id,
        max_length=128,
    )
    guid = _service_required_text(
        call.data[SERVICE_FIELD_GUID],
        field_name=SERVICE_FIELD_GUID,
        translation_key="check_system_bound_failed",
        device_id=device_id,
        max_length=128,
    )
    try:
        exists = await coordinator.async_check_system_bound(
            bind_key=bind_key,
            device_sn=device_sn,
            guid=guid,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while checking system binding. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "check_system_bound_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"exists": cast("JsonValueType", exists)}


def _app_metric_device_owner(
    hass: HomeAssistant,
    call: ServiceCall,
    *,
    translation_key: str,
) -> tuple[str, JackerySolarVaultCoordinator, str]:
    """Resolve an explicitly requested device to its authenticated serial owner."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key=translation_key,
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error="no Jackery account owns this device id",
        )
    serial = coordinator._resolve_device_sn(device_id)  # ruff: ignore[private-member-access]
    if not isinstance(serial, str) or not serial.strip():
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error="device serial is not confirmed for this device",
        )
    return device_id, coordinator, serial


async def _async_handle_get_battery_passport(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Read all App 2.4.7 battery-passport fields only when explicitly asked."""
    device_id, coordinator, serial = _app_metric_device_owner(
        hass, call, translation_key="get_battery_passport_failed"
    )
    try:
        result = await coordinator.api.async_get_battery_passport(serial)
    except JackeryAuthError as err:
        msg = "Jackery credentials rejected the battery passport request"
        raise ConfigEntryAuthFailed(msg) from err
    except (
        JackeryError,
        HomeAssistantError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        raise _service_action_error(
            "get_battery_passport_failed",
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    # No field renaming, filtering or guessed values. Preserve future API keys.
    return {"passport": cast("JsonValueType", result)}


async def _async_handle_get_fault_log(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return one owner-scoped page with all original fault record fields."""
    device_id, coordinator, serial = _app_metric_device_owner(
        hass, call, translation_key="get_fault_log_failed"
    )
    page_no = cast("int", call.data.get(SERVICE_FIELD_PAGE_NO, 1))
    page_size = cast("int", call.data.get(SERVICE_FIELD_PAGE_SIZE, 20))
    try:
        result = await coordinator.api.async_get_fault_log(
            serial, page_num=page_no, page_size=page_size
        )
    except JackeryAuthError as err:
        msg = "Jackery credentials rejected the fault history request"
        raise ConfigEntryAuthFailed(msg) from err
    except (
        JackeryError,
        HomeAssistantError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        raise _service_action_error(
            "get_fault_log_failed",
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"fault_log": cast("JsonValueType", result)}


async def _async_handle_get_alarm_detail(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return alarm detail for an alarm key."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="get_alarm_detail_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "get_alarm_detail_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="get_alarm_detail_failed",
        service_name=SERVICE_GET_ALARM_DETAIL,
    )
    alarm_key = _service_required_text(
        call.data[SERVICE_FIELD_ALARM_KEY],
        field_name=SERVICE_FIELD_ALARM_KEY,
        translation_key="get_alarm_detail_failed",
        device_id=device_id,
        max_length=256,
    )
    try:
        detail = await coordinator.async_get_alarm_detail(
            alarm_key,
            context_device_id=device_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading alarm detail. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "get_alarm_detail_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"alarm": cast("JsonValueType", detail)}


async def _async_handle_list_notifications(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return a paginated Jackery push notification list."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_notifications_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_notifications_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    device_sn = _service_optional_text(
        call.data.get(SERVICE_FIELD_DEVICE_SN, ""),
        field_name=SERVICE_FIELD_DEVICE_SN,
        translation_key="list_notifications_failed",
        device_id=device_id,
        max_length=128,
    )
    current_time = _service_int(
        call.data.get(SERVICE_FIELD_CURRENT_TIME, 0),
        field_name=SERVICE_FIELD_CURRENT_TIME,
        translation_key="list_notifications_failed",
        device_id=device_id,
        bounds=(0, 9999999999999),
    )
    page_no = _service_int(
        call.data.get(SERVICE_FIELD_PAGE_NO, 1),
        field_name=SERVICE_FIELD_PAGE_NO,
        translation_key="list_notifications_failed",
        device_id=device_id,
        bounds=(1, 10000),
    )
    page_size = _service_int(
        call.data.get(SERVICE_FIELD_PAGE_SIZE, 20),
        field_name=SERVICE_FIELD_PAGE_SIZE,
        translation_key="list_notifications_failed",
        device_id=device_id,
        bounds=(1, 100),
    )
    try:
        notifications = await coordinator.async_list_notifications(
            current_time=current_time,
            device_sn=device_sn.strip(),
            page_no=page_no,
            page_size=page_size,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while listing notifications. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "list_notifications_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"notifications": cast("list[JsonValueType]", notifications)}


async def _async_handle_get_unread_count(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return unread notification counts for the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="get_unread_count_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "get_unread_count_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        unread = await coordinator.async_get_unread_count()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading unread count. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "get_unread_count_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"unread": cast("JsonValueType", unread)}


async def _async_handle_get_push_config(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return push-notification configuration for the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="get_push_config_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "get_push_config_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        config = await coordinator.async_get_push_config()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading push config. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "get_push_config_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"config": cast("JsonValueType", config)}


async def _async_handle_set_push_config(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Set push-notification configuration for the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="set_push_config_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "set_push_config_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    set_value = call.data[SERVICE_FIELD_SET]
    try:
        await coordinator.async_set_push_config(set_value)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while setting push config. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "set_push_config_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_sync_alerts(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Sync faults and alarms through the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="sync_alerts_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "sync_alerts_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="sync_alerts_failed",
        service_name=SERVICE_SYNC_ALERTS,
    )
    content = _service_required_text(
        call.data[SERVICE_FIELD_CONTENT],
        field_name=SERVICE_FIELD_CONTENT,
        translation_key="sync_alerts_failed",
        device_id=device_id,
        max_length=65535,
    )
    sync_id = _service_required_text(
        call.data[SERVICE_FIELD_ID],
        field_name=SERVICE_FIELD_ID,
        translation_key="sync_alerts_failed",
        device_id=device_id,
        max_length=128,
    )
    try:
        await coordinator.async_sync_alerts(
            content=content,
            id=sync_id,
            context_device_id=device_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while syncing alerts. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "sync_alerts_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_sync_offline_statistics(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Explicitly upload the supplied original offline device body."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="sync_offline_statistics_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "sync_offline_statistics_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    _raise_if_portable_home_service(
        coordinator,
        device_id,
        translation_key="sync_offline_statistics_failed",
        service_name=SERVICE_SYNC_OFFLINE_STATISTICS,
    )
    try:
        accepted = await coordinator.async_sync_offline_statistics(
            call.data[SERVICE_FIELD_BODY],
            context_device_id=device_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while uploading offline statistics. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "sync_offline_statistics_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    if not accepted:
        msg = "sync_offline_statistics_failed"
        raise _service_action_error(
            msg, device_id=device_id, error="backend did not acknowledge the upload"
        )
    return {"synced": True}


async def _async_handle_query_charge_report(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return charge-report history for a device serial number."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="query_charge_report_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "query_charge_report_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    # App 2.4.1 opens ChargeReportActivity only from the portable settings
    # screens; the cloud rejects device/chargeReport for Home devices.
    if not _is_portable_device(coordinator, device_id):
        raise _service_validation_error(
            "query_charge_report_failed",
            device_id=device_id,
            error="charge reports exist only for portable devices",
        )
    device_sn = _service_required_text(
        call.data[SERVICE_FIELD_DEVICE_SN],
        field_name=SERVICE_FIELD_DEVICE_SN,
        translation_key="query_charge_report_failed",
        device_id=device_id,
        max_length=128,
    )
    page_index = _service_int(
        call.data.get(SERVICE_FIELD_PAGE_INDEX, 1),
        field_name=SERVICE_FIELD_PAGE_INDEX,
        translation_key="query_charge_report_failed",
        device_id=device_id,
        bounds=(1, 10000),
    )
    try:
        report = await coordinator.async_query_charge_report(
            device_sn=device_sn,
            page_index=page_index,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading charge report. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "query_charge_report_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"charge_report": cast("JsonValueType", report)}


async def _async_handle_query_cutoff_stat(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return cutoff/power-outage statistics for a device serial number."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="query_cutoff_stat_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "query_cutoff_stat_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    device_sn = _service_required_text(
        call.data[SERVICE_FIELD_DEVICE_SN],
        field_name=SERVICE_FIELD_DEVICE_SN,
        translation_key="query_cutoff_stat_failed",
        device_id=device_id,
        max_length=128,
    )
    begin_date = _service_required_text(
        call.data[SERVICE_FIELD_BEGIN_DATE],
        field_name=SERVICE_FIELD_BEGIN_DATE,
        translation_key="query_cutoff_stat_failed",
        device_id=device_id,
        max_length=32,
    )
    end_date = _service_required_text(
        call.data[SERVICE_FIELD_END_DATE],
        field_name=SERVICE_FIELD_END_DATE,
        translation_key="query_cutoff_stat_failed",
        device_id=device_id,
        max_length=32,
    )
    try:
        stat = await coordinator.async_query_cutoff_stat(
            device_sn=device_sn,
            begin_date=begin_date,
            end_date=end_date,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading cutoff statistics. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "query_cutoff_stat_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"cutoff_stat": cast("JsonValueType", stat)}


async def _async_handle_query_soc_stat(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return SOC statistics for the selected device."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="query_soc_stat_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "query_soc_stat_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        stat = await coordinator.async_query_soc_stat(device_id)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading SOC statistics. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "query_soc_stat_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"soc_stat": cast("JsonValueType", stat)}


async def _async_handle_query_carbon_stat(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return carbon-offset statistics for a device serial number."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="query_carbon_stat_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "query_carbon_stat_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    device_sn = _service_required_text(
        call.data[SERVICE_FIELD_DEVICE_SN],
        field_name=SERVICE_FIELD_DEVICE_SN,
        translation_key="query_carbon_stat_failed",
        device_id=device_id,
        max_length=128,
    )
    try:
        stat = await coordinator.async_query_carbon_stat(device_sn)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading carbon statistics. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "query_carbon_stat_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"carbon_stat": cast("JsonValueType", stat)}


async def _async_handle_query_profit_stat(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return profit/revenue statistics for the selected device."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="query_profit_stat_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "query_profit_stat_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        stat = await coordinator.async_query_profit_stat(device_id)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading profit statistics. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "query_profit_stat_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"profit_stat": cast("JsonValueType", stat)}


async def _async_handle_query_box_stat(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return generic box electricity statistics for a device serial number."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="query_box_stat_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "query_box_stat_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    device_sn = _service_required_text(
        call.data[SERVICE_FIELD_DEVICE_SN],
        field_name=SERVICE_FIELD_DEVICE_SN,
        translation_key="query_box_stat_failed",
        device_id=device_id,
        max_length=128,
    )
    begin_date = _service_required_text(
        call.data[SERVICE_FIELD_BEGIN_DATE],
        field_name=SERVICE_FIELD_BEGIN_DATE,
        translation_key="query_box_stat_failed",
        device_id=device_id,
        max_length=32,
    )
    end_date = _service_required_text(
        call.data[SERVICE_FIELD_END_DATE],
        field_name=SERVICE_FIELD_END_DATE,
        translation_key="query_box_stat_failed",
        device_id=device_id,
        max_length=32,
    )
    key = _service_optional_text(
        call.data.get(SERVICE_FIELD_KEY, ""),
        field_name=SERVICE_FIELD_KEY,
        translation_key="query_box_stat_failed",
        device_id=device_id,
        max_length=128,
    ).strip()
    try:
        stat = await coordinator.async_query_box_stat(
            device_sn=device_sn,
            date_type=call.data[SERVICE_FIELD_DATE_TYPE],
            begin_date=begin_date,
            end_date=end_date,
            key=key,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading box statistics. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "query_box_stat_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"box_stat": cast("JsonValueType", stat)}


async def _async_handle_check_app_version(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return Jackery app-version metadata for the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="check_app_version_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "check_app_version_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    app_type = _service_optional_text(
        call.data.get(SERVICE_FIELD_TYPE, ""),
        field_name=SERVICE_FIELD_TYPE,
        translation_key="check_app_version_failed",
        device_id=device_id,
        max_length=64,
    ).strip()
    version_name = _service_optional_text(
        call.data.get(SERVICE_FIELD_VERSION_NAME, ""),
        field_name=SERVICE_FIELD_VERSION_NAME,
        translation_key="check_app_version_failed",
        device_id=device_id,
        max_length=64,
    ).strip()
    try:
        version = await coordinator.async_check_app_version(
            type=app_type,
            version_name=version_name,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading app version. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "check_app_version_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"app_version": cast("JsonValueType", version)}


async def _async_handle_list_banners(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return Jackery app banner entries for the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_banners_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_banners_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        banners = await coordinator.async_list_banners()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading banners. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "list_banners_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"banners": cast("JsonValueType", banners)}


async def _async_handle_submit_feedback(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Submit user feedback through the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="submit_feedback_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "submit_feedback_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    contact_info = _service_required_text(
        call.data[SERVICE_FIELD_CONTACT_INFO],
        field_name=SERVICE_FIELD_CONTACT_INFO,
        translation_key="submit_feedback_failed",
        device_id=device_id,
        max_length=256,
    )
    content = _service_required_text(
        call.data[SERVICE_FIELD_CONTENT],
        field_name=SERVICE_FIELD_CONTENT,
        translation_key="submit_feedback_failed",
        device_id=device_id,
        max_length=65535,
    )
    device_sn = _service_optional_text(
        call.data.get(SERVICE_FIELD_DEVICE_SN, ""),
        field_name=SERVICE_FIELD_DEVICE_SN,
        translation_key="submit_feedback_failed",
        device_id=device_id,
        max_length=128,
    ).strip()
    try:
        await coordinator.async_submit_feedback(
            contact_info=contact_info,
            content=content,
            device_sn=device_sn,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while submitting feedback. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "submit_feedback_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_list_faqs(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return Jackery FAQ entries for the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_faqs_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_faqs_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        faqs = await coordinator.async_list_faqs()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading FAQs. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "list_faqs_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"faqs": cast("JsonValueType", faqs)}


async def _async_handle_list_faq_answers(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return Jackery FAQ answer entries for the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="list_faq_answers_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "list_faq_answers_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        answers = await coordinator.async_list_faq_answers()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading FAQ answers. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "list_faq_answers_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"faq_answers": cast("JsonValueType", answers)}


async def _async_handle_check_privacy_update(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return whether updated privacy consent is required."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="check_privacy_update_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "check_privacy_update_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        privacy_update = await coordinator.async_check_privacy_update()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while checking privacy update. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "check_privacy_update_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"privacy_update": cast("JsonValueType", privacy_update)}


async def _async_handle_agree_privacy_consent(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Record privacy consent agreement through the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="agree_privacy_consent_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "agree_privacy_consent_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    pending_agree_version_ids = [
        int(version_id)
        for version_id in call.data[SERVICE_FIELD_PENDING_AGREE_VERSION_IDS]
    ]
    try:
        await coordinator.async_agree_privacy_consent(
            pending_agree_version_ids=pending_agree_version_ids,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while agreeing privacy consent. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "agree_privacy_consent_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_get_product_instruction(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return product-instruction payload for a device serial number."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="get_product_instruction_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "get_product_instruction_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    device_sn = _service_required_text(
        call.data[SERVICE_FIELD_DEVICE_SN],
        field_name=SERVICE_FIELD_DEVICE_SN,
        translation_key="get_product_instruction_failed",
        device_id=device_id,
        max_length=128,
    )
    instruction_type = _service_optional_text(
        call.data.get(SERVICE_FIELD_TYPE, ""),
        field_name=SERVICE_FIELD_TYPE,
        translation_key="get_product_instruction_failed",
        device_id=device_id,
        max_length=64,
    ).strip()
    try:
        instruction = await coordinator.async_get_product_instruction(
            device_sn=device_sn,
            type=instruction_type,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading product instruction. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "get_product_instruction_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"product_instruction": cast("JsonValueType", instruction)}


async def _async_handle_get_user_info(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Return authenticated-user profile information for the selected account."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="get_user_info_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "get_user_info_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        user_info = await coordinator.async_get_user_info()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while reading user info. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "get_user_info_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
    return {"user_info": cast("JsonValueType", user_info)}


async def _async_handle_update_electricity_strategy(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Update an existing electricity strategy plan."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="update_electricity_strategy_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "update_electricity_strategy_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    body = _ble_body_from_service(call.data[SERVICE_FIELD_BODY], device_id)
    try:
        await coordinator.async_update_electricity_strategy(device_id, body)
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "update_electricity_strategy_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_delete_electricity_strategy(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Delete an electricity strategy plan."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="delete_electricity_strategy_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "delete_electricity_strategy_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    body = _ble_body_from_service(call.data[SERVICE_FIELD_BODY], device_id)
    try:
        await coordinator.async_delete_electricity_strategy(device_id, body)
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "delete_electricity_strategy_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err


async def _async_handle_query_electricity_strategy(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Query all electricity strategy plans."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="query_electricity_strategy_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        msg = "query_electricity_strategy_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    try:
        await coordinator.async_query_electricity_strategy(device_id)
    except (
        HomeAssistantError,
        JackeryError,
        LookupError,
        RuntimeError,
        ValueError,
    ) as err:
        msg = "query_electricity_strategy_failed"
        raise _service_action_error(
            msg,
            device_id=device_id,
            error=err,
            entry=getattr(coordinator, "entry", None),
        ) from err
