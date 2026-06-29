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
2. Forward the request to the coordinator (or its API client for
   rename_system, which is a system-scoped REST call).
3. Surface failures as ``ServiceValidationError`` with the integration's
   ``translation_domain`` so HA can render a localized error to the user.
"""

from collections.abc import Callable, Coroutine
import json
import logging
import math
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import voluptuous as vol

from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .client.api import JackeryAuthError, JackeryError
from .const import (
    DOMAIN,
    FIELD_ID,
    FIELD_QR_CODE_ID,
    FIELD_SYSTEM_ID,
    FIELD_USER_ID,
    MQTT_ACTION_IDS_SCHEDULE,
    PAYLOAD_SYSTEM,
    SERVICE_BIND_SMART_PART,
    SERVICE_DELETE_ELECTRICITY_STRATEGY,
    SERVICE_DELETE_STORM_ALERT,
    SERVICE_FIELD_ACCESSORY_SN,
    SERVICE_FIELD_ACK_TIMEOUT,
    SERVICE_FIELD_ACTION_ID,
    SERVICE_FIELD_ALERT_ID,
    SERVICE_FIELD_BIND_USER_ID,
    SERVICE_FIELD_BODY,
    SERVICE_FIELD_CMD,
    SERVICE_FIELD_DEVICE_ID,
    SERVICE_FIELD_ENABLE,
    SERVICE_FIELD_FLAGS,
    SERVICE_FIELD_IP,
    SERVICE_FIELD_LEVEL,
    SERVICE_FIELD_NEW_NAME,
    SERVICE_FIELD_NICKNAME,
    SERVICE_FIELD_PASSWORD,
    SERVICE_FIELD_PORT,
    SERVICE_FIELD_SYSTEM_ID,
    SERVICE_FIELD_TOKEN,
    SERVICE_FIELD_USERNAME,
    SERVICE_FIELD_WAIT_FOR_ACK,
    SERVICE_GET_SHARE_QR_CODE,
    SERVICE_INSERT_ELECTRICITY_STRATEGY,
    SERVICE_LIST_SHARED_DEVICES,
    SERVICE_LIST_SHARED_MANAGERS,
    SERVICE_NON_EMPTY_TEXT_PATTERN,
    SERVICE_NUMERIC_ID_PATTERN,
    SERVICE_QUERY_ELECTRICITY_STRATEGY,
    SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    SERVICE_REFRESH_WEATHER_PLAN,
    SERVICE_REMOVE_ALL_SHARED_ACCESS,
    SERVICE_REMOVE_SHARED_ACCESS,
    SERVICE_RENAME_SYSTEM,
    SERVICE_RESPONSE_QR_CODE_ID,
    SERVICE_RESPONSE_USER_ID,
    SERVICE_SEND_BLE_COMMAND,
    SERVICE_SEND_DEVICE_SCHEDULE,
    SERVICE_SET_DEVICE_NICKNAME,
    SERVICE_SET_THIRD_PARTY_MQTT_CONFIG,
    SERVICE_UNBIND_DEVICE,
    SERVICE_UNBIND_SMART_PART,
    SERVICE_UPDATE_ELECTRICITY_STRATEGY,
    _BLE_SERVICE_CONNECT_TIMEOUT_SEC,
    _JACKERY_MAIN_DEVICE_RE,
)
from .coordinator import JackerySolarVaultCoordinator
from .util import safe_bool

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
    from homeassistant.util.json import JsonValueType

_LOGGER = logging.getLogger(__name__)


def _coerce_service_int(raw: Any) -> int:  # noqa: ANN401
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


def _coerce_service_float(raw: Any) -> float:  # noqa: ANN401
    """Return a finite service float without accepting booleans."""
    if isinstance(raw, bool):
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
# Schemas — kept here so test_code_quality can locate them next to the
# handlers that consume them. Numeric-id pattern accepts surrounding
# whitespace so service callers from automations may include indentation;
# handlers strip before forwarding to the cloud.
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
        cv.string,
        vol.Length(max=128),
    ),
    vol.Optional(SERVICE_FIELD_PASSWORD, default=""): vol.All(
        cv.string,
        vol.Length(max=128),
    ),
    vol.Optional(SERVICE_FIELD_TOKEN, default=""): vol.All(
        cv.string,
        vol.Length(max=512),
    ),
})
QUERY_THIRD_PARTY_MQTT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
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


# ---------------------------------------------------------------------------
# Coordinator routing helpers
# ---------------------------------------------------------------------------


def _loaded_coordinators(hass: HomeAssistant) -> list[JackerySolarVaultCoordinator]:
    """Return runtime coordinators for loaded Jackery config entries."""
    coordinators: list[JackerySolarVaultCoordinator] = []
    for loaded_entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = getattr(loaded_entry, "runtime_data", None)
        if isinstance(coordinator, JackerySolarVaultCoordinator):
            coordinators.append(coordinator)
    return coordinators


def _resolve_jackery_device_id(hass: HomeAssistant, raw: str) -> str:
    """Map an HA device-registry id (UUID) to the Jackery device id, if possible.

    The device selector in services.yaml hands the handler an HA device-id
    (e.g. ``a1b2c3...``). The integration's device-registry identifiers carry
    the matching ``(DOMAIN, jackery_device_id)`` tuple, so we can translate
    back to the cloud-facing id. Accessory rows use their own identifiers but
    point at the parent SolarVault through ``via_device_id``; service calls must
    act on that parent. If the input is already a Jackery numeric id (legacy
    automations), the lookup misses and we return the raw value unchanged.
    """
    registry = dr.async_get(hass)
    device = registry.async_get(raw)
    if device is None:
        return raw
    if device.via_device_id is not None:
        via_device = registry.async_get(device.via_device_id)
        if via_device is not None:
            for domain, identifier in via_device.identifiers:
                if domain == DOMAIN:
                    return str(identifier)
    for domain, identifier in device.identifiers:
        if domain == DOMAIN:
            return str(identifier)
    return raw


def _coordinator_for_device(
    hass: HomeAssistant,
    device_id: str,
) -> JackerySolarVaultCoordinator | None:
    """Return the coordinator whose payload contains the given device id."""
    for coordinator in _loaded_coordinators(hass):
        if device_id in (coordinator.data or {}):
            return coordinator
    return None


def _coordinator_for_system(
    hass: HomeAssistant,
    system_id: str,
) -> JackerySolarVaultCoordinator | None:
    """Return the coordinator whose payload owns the given system id."""
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
    """Build a translated service validation error with common placeholders."""
    placeholders = {
        "device_id": device_id,
        "error": str(error),
    }
    if extra_placeholders is not None:
        placeholders.update(extra_placeholders)
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders=placeholders,
    )


def _device_id_from_service(
    hass: HomeAssistant,
    raw: Any,  # noqa: ANN401
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


def _rename_system_id_from_service(raw: Any) -> str:  # noqa: ANN401
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


def _rename_name_from_service(raw: Any, system_id: str) -> str:  # noqa: ANN401
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
    if len(parsed) > 64:  # noqa: PLR2004
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="rename_system_failed",
            translation_placeholders={
                "system_id": system_id,
                "error": f"{SERVICE_FIELD_NEW_NAME} must be at most 64 characters",
            },
        )
    return parsed


def _storm_alert_id_from_service(raw: Any, device_id: str) -> str:  # noqa: ANN401
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


def _json_native_value(value: Any) -> Any:  # noqa: ANN401
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
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = "body object keys must be strings"
                raise ValueError(msg)  # noqa: TRY004
            normalized[key] = _json_native_value(item)
        return normalized
    msg = "body must contain only JSON-compatible values"
    raise ValueError(msg)


def _json_native_body(body: dict[Any, Any], device_id: str) -> dict[str, Any]:
    """Return a JSON-native object body or raise a translated service error."""
    try:
        normalized = _json_native_value(body)
    except ValueError as err:
        msg = "send_ble_command_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
        ) from err
    assert isinstance(normalized, dict)
    return normalized


def _ble_body_from_service(raw_body: Any, device_id: str) -> dict[str, Any]:  # noqa: ANN401
    """Return a dict body from a service object or JSON string."""
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


def _service_bool(
    raw: Any,  # noqa: ANN401
    *,
    field_name: str,
    translation_key: str,
    device_id: str,
) -> bool:
    """Return a parsed service boolean or raise a translated validation error."""
    parsed = safe_bool(raw)
    if parsed is None:
        raise _service_validation_error(
            translation_key,
            device_id=device_id,
            error=f"{field_name} must be a boolean",
        )
    return parsed


def _service_required_text(
    raw: Any,  # noqa: ANN401
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
    raw: Any,  # noqa: ANN401
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


def _service_int(  # noqa: PLR0913
    raw: Any,  # noqa: ANN401
    *,
    field_name: str,
    translation_key: str,
    device_id: str,
    min_value: int,
    max_value: int,
) -> int:
    """Return a parsed service integer within the schema range."""
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


def _service_float(  # noqa: PLR0913
    raw: Any,  # noqa: ANN401
    *,
    field_name: str,
    translation_key: str,
    device_id: str,
    min_value: float,
    max_value: float,
) -> float:
    """Return a parsed service float within the schema range."""
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
# Service-action handlers
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
        ok = await coordinator.api.async_set_system_name(system_id, new_name)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while renaming a system. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(
            msg,
        ) from err
    except JackeryError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="rename_system_failed",
            translation_placeholders={
                "system_id": system_id,
                "error": str(err),
            },
        ) from err
    if not ok:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="rename_system_failed",
            translation_placeholders={
                "system_id": system_id,
                "error": "server returned false",
            },
        )
    await coordinator.async_request_refresh()


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
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="refresh_weather_plan_failed",
            translation_placeholders={
                "device_id": device_id,
                "error": str(err),
            },
        ) from err


async def _async_handle_delete_storm_alert(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Delete a storm alert on the matching coordinator."""
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
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="delete_storm_alert_failed",
            translation_placeholders={
                "device_id": device_id,
                "alert_id": alert_id,
                "error": str(err),
            },
        ) from err


async def _async_handle_set_third_party_mqtt_config(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Publish the experimental SET_THIRD_PARTY_MQTT_CONFIG (3046) frame."""
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
                min_value=1,
                max_value=65535,
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
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="set_third_party_mqtt_config_failed",
            translation_placeholders={
                "device_id": device_id,
                "error": str(err),
            },
        ) from err


async def _async_handle_query_third_party_mqtt_config(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Publish the experimental GET_THIRD_PARTY_MQTT_CONFIG (3047) frame."""
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
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="query_third_party_mqtt_config_failed",
            translation_placeholders={
                "device_id": device_id,
                "error": str(err),
            },
        ) from err


async def _async_handle_send_ble_command(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Write one experimental binary command frame over the active BLE session."""
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
                min_value=1,
                max_value=65535,
            ),
            body=body,
            flags=_service_int(
                call.data.get(SERVICE_FIELD_FLAGS, 0),
                field_name=SERVICE_FIELD_FLAGS,
                translation_key="send_ble_command_failed",
                device_id=device_id,
                min_value=0,
                max_value=65535,
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
                min_value=0.5,
                max_value=60.0,
            ),
            connect_timeout_sec=_BLE_SERVICE_CONNECT_TIMEOUT_SEC,
        )
    except ConfigEntryAuthFailed:
        raise
    except ServiceValidationError:
        raise
    except (HomeAssistantError, RuntimeError, ValueError) as err:
        msg = "send_ble_command_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
        ) from err
    if not sent:
        msg = "send_ble_command_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=(
                "BLE writes are disabled or no active BLE session exists after "
                "waiting for reconnect"
            ),
        )


async def _async_handle_set_device_nickname(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Set a device nickname through the API client of the matching coordinator."""
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
        await coordinator.api.async_set_device_nickname(device_id, nickname)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while setting a device nickname. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except JackeryError as err:
        msg = "set_device_nickname_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
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
        await coordinator.api.async_unbind_device(device_id)
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while unbinding a device. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except JackeryError as err:
        msg = "unbind_device_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
        ) from err
    await coordinator.async_request_refresh()


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
    accessory_sn = _service_required_text(
        call.data[SERVICE_FIELD_ACCESSORY_SN],
        field_name=SERVICE_FIELD_ACCESSORY_SN,
        translation_key="bind_smart_part_failed",
        device_id=device_id,
        max_length=64,
    )
    try:
        await coordinator.async_bind_smart_part(device_id, accessory_sn)
    except ConfigEntryAuthFailed:
        raise
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while binding a smart part. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "bind_smart_part_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
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
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "unbind_smart_part_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
        ) from err


def _render_share_qr_png_data_uri(qr_code_id: str) -> str:
    """Return a base64 PNG ``data:`` URI encoding the share ``qrCodeId``.

    The Jackery app's accept-bind flow scans a QR, extracts only the
    ``qrCodeId`` string, and pairs it with the *scanner's* own device id
    (``device/accept_bind`` takes ``devId`` + ``qrCodeId``). The displayed QR
    therefore carries just the ``qrCodeId``; ``userId`` and ``devId`` are not
    part of the scanned payload. This exact scan format is reverse-engineered,
    not vendor-documented, so the rendering is best-effort.

    segno is imported lazily so the module import stays cheap for the common
    path that never renders a QR.
    """
    import base64  # noqa: PLC0415
    import io  # noqa: PLC0415

    import segno  # noqa: PLC0415

    buffer = io.BytesIO()
    segno.make(qr_code_id, error="m").save(buffer, kind="png", scale=6, border=2)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _notify_share_qr_code(
    hass: HomeAssistant,
    *,
    qr_code_id: object,
    user_id: object,
) -> None:
    """Create a persistent notification with a scannable share QR image.

    Best-effort: a render or notification failure must never fail the service,
    which still returns the ``{qr_code_id, user_id}`` envelope. The notification
    documents that the encoded payload (the ``qrCodeId`` string) is a
    reverse-engineered assumption so the owner can correct it if it does not
    scan in the Jackery app.
    """
    if not isinstance(qr_code_id, str) or not qr_code_id:
        return
    try:
        from homeassistant.components import (  # noqa: PLC0415
            persistent_notification,
        )

        data_uri = _render_share_qr_png_data_uri(qr_code_id)
        message = (
            f"![Share QR code]({data_uri})\n\n"
            f"Scan diesen QR-Code mit einem zweiten Jackery-Konto, um den "
            f"SolarVault zu teilen.\n\n"
            f"qrCodeId: `{qr_code_id}`\n"
            f"userId: `{user_id}`\n\n"
            f"Hinweis: Der QR-Code kodiert die `qrCodeId` (Best-Effort, "
            f"aus der App rekonstruiert). Falls das Scannen fehlschlägt, "
            f"nutze die `qrCodeId` oben manuell."
        )
        persistent_notification.async_create(
            hass,
            message,
            title="Jackery SolarVault - Freigabe-QR-Code",
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
        data = await coordinator.api.async_get_qr_code()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while fetching the share QR code. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "get_share_qr_code_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
        ) from err
    qr_code_id = data.get(FIELD_QR_CODE_ID)
    user_id = data.get(FIELD_USER_ID)
    _notify_share_qr_code(hass, qr_code_id=qr_code_id, user_id=user_id)
    return {
        SERVICE_RESPONSE_QR_CODE_ID: qr_code_id,
        SERVICE_RESPONSE_USER_ID: user_id,
    }


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
        devices = await coordinator.api.async_get_device_shared_list()
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while listing shared devices. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "list_shared_devices_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
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
        min_value=0,
        max_value=65535,
    )
    try:
        managers = await coordinator.api.async_get_device_shared_managers(
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
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
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
    try:
        await coordinator.api.async_remove_shared_access(
            bind_user_id=bind_user_id,
            device_id=device_id,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while removing shared access. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError) as err:
        msg = "remove_shared_access_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
        ) from err
    await coordinator.async_request_refresh()


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
        min_value=0,
        max_value=65535,
    )
    try:
        await coordinator.api.async_remove_all_shared_access(
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
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
        ) from err
    await coordinator.async_request_refresh()


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
            SERVICE_DELETE_STORM_ALERT,
            _async_handle_delete_storm_alert,
            DELETE_STORM_ALERT_SCHEMA,
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
            SERVICE_SEND_DEVICE_SCHEDULE,
            _async_handle_send_device_schedule,
            SEND_DEVICE_SCHEDULE_SCHEMA,
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
            SERVICE_UNBIND_DEVICE,
            _async_handle_unbind_device,
            UNBIND_DEVICE_SCHEMA,
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
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's domain-scoped service actions.

    Called once from ``async_setup``. HA tears the services down on
    shutdown automatically; multi-entry setups share the same handler
    instances which then dispatch to the matching coordinator.
    """

    def _make_handler(
        handler: _ServiceHandler,
    ) -> Callable[[ServiceCall], Coroutine[Any, Any, ServiceResponse | None]]:
        async def _handle(call: ServiceCall) -> ServiceResponse | None:
            return await handler(hass, call)

        return _handle

    for registration in _service_registrations():
        if not hass.services.has_service(DOMAIN, registration.name):
            hass.services.async_register(
                DOMAIN,
                registration.name,
                _make_handler(registration.handler),
                schema=registration.schema,
                supports_response=registration.supports_response,
            )


def _strip_jackery_subdevice_suffix(device_id: str) -> str:
    """Return the parent numeric Jackery device identifier by removing a recognized.

    subdevice suffix.

    Parameters:
        device_id (str): Device identifier that may include a trailing `_suffix` (e.g.,
        "12345_child").

    Returns:
        str: The leading numeric device identifier if a suffix is present (e.g.,
        "12345"), otherwise the original input.
    """
    match = _JACKERY_MAIN_DEVICE_RE.match(device_id)
    return match.group(1) if match else device_id


async def _async_handle_send_device_schedule(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Send a device schedule frame (TIMER_TASK_ADD/DELETE/UPDATE/READ) to a Jackery.

    device.

    Resolves the provided device identifier to the owning Jackery device, parses the
    schedule `body` (accepts a mapping or a JSON object string), and forwards the
    action to the coordinator to send the schedule frame.

    Parameters:
        call (ServiceCall): Service call whose `data` must include:
            - `device_id` (str): device identifier or Home Assistant device registry id
            to resolve.
            - `action_id` (int): schedule action identifier (one of 3015, 3016, 3017,
            3018).
            - `body` (dict | str): schedule payload as a mapping or a JSON-encoded
            object string.

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
    body = _ble_body_from_service(call.data[SERVICE_FIELD_BODY], device_id)
    try:
        await coordinator.async_send_device_schedule(
            device_id,
            action_id=_service_int(
                call.data[SERVICE_FIELD_ACTION_ID],
                field_name=SERVICE_FIELD_ACTION_ID,
                translation_key="send_device_schedule_failed",
                device_id=device_id,
                min_value=1,
                max_value=65535,
            ),
            body=body,
        )
    except JackeryAuthError as err:
        msg = (
            "Jackery credentials were rejected while sending a device schedule. "
            "Re-authentication is required."
        )
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "send_device_schedule_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
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
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "insert_electricity_strategy_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
        ) from err


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
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "update_electricity_strategy_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
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
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "delete_electricity_strategy_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
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
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "query_electricity_strategy_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
        ) from err
