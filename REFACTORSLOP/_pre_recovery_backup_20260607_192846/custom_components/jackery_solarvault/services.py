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

import json  # noqa: I001
import logging
import math
from typing import Any, Final

from homeassistant.core import HomeAssistant, ServiceCall, callback  # noqa: TC001
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .client.api import JackeryAuthError, JackeryError
from .const import (
    DOMAIN,
    FIELD_ID,
    FIELD_SYSTEM_ID,
    PAYLOAD_SYSTEM,
    SERVICE_DELETE_STORM_ALERT,
    SERVICE_FIELD_ACK_TIMEOUT,
    SERVICE_FIELD_ALERT_ID,
    SERVICE_FIELD_BODY,
    SERVICE_FIELD_CMD,
    SERVICE_FIELD_DEVICE_ID,
    SERVICE_FIELD_ENABLE,
    SERVICE_FIELD_FLAGS,
    SERVICE_FIELD_IP,
    SERVICE_FIELD_NEW_NAME,
    SERVICE_FIELD_PASSWORD,
    SERVICE_FIELD_PORT,
    SERVICE_FIELD_SYSTEM_ID,
    SERVICE_FIELD_TOKEN,
    SERVICE_FIELD_USERNAME,
    SERVICE_FIELD_WAIT_FOR_ACK,
    SERVICE_NON_EMPTY_TEXT_PATTERN,
    SERVICE_NUMERIC_ID_PATTERN,
    SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    SERVICE_REFRESH_WEATHER_PLAN,
    SERVICE_RENAME_SYSTEM,
    SERVICE_SEND_BLE_COMMAND,
    SERVICE_SET_THIRD_PARTY_MQTT_CONFIG,
    SERVICE_FIELD_ACTION_ID,
)
from .coordinator import JackerySolarVaultCoordinator
from .util import safe_bool
import re

_LOGGER = logging.getLogger(__name__)

_BLE_SERVICE_CONNECT_TIMEOUT_SEC = 35.0


_JACKERY_MAIN_DEVICE_RE: Final = re.compile(r"^(\d+)(?:_.+)?$")


def _coerce_service_int(raw: Any) -> int:  # noqa: ANN401, E302, RUF100
    """Return a whole service integer without truncating fractional numbers."""
    if isinstance(raw, bool):
        raise vol.Invalid("expected integer")  # noqa: TRY003
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if math.isfinite(raw) and raw.is_integer():
            return int(raw)
        raise vol.Invalid("expected integer")  # noqa: TRY003
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise vol.Invalid("expected integer")  # noqa: TRY003
        signed = text[0] in "+-"
        digits = text[1:] if signed else text
        if digits.isascii() and digits.isdecimal():
            try:
                return int(text)
            except ValueError as err:
                raise vol.Invalid("expected integer") from err  # noqa: TRY003
    raise vol.Invalid("expected integer")  # noqa: TRY003


def _coerce_service_float(raw: Any) -> float:  # noqa: ANN401
    """Return a finite service float without accepting booleans."""
    if isinstance(raw, bool):
        raise vol.Invalid("expected finite number")  # noqa: TRY003
    try:
        parsed = float(raw)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid("expected finite number") from err  # noqa: TRY003
    if not math.isfinite(parsed):
        raise vol.Invalid("expected finite number")  # noqa: TRY003
    return parsed


# ---------------------------------------------------------------------------
# Schemas — kept here so test_code_quality can locate them next to the
# handlers that consume them. Numeric-id pattern accepts surrounding
# whitespace so service callers from automations may include indentation;
# handlers strip before forwarding to the cloud.
# ---------------------------------------------------------------------------

RENAME_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_SYSTEM_ID): vol.All(
        cv.string, vol.Match(SERVICE_NUMERIC_ID_PATTERN)
    ),
    vol.Required(SERVICE_FIELD_NEW_NAME): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=64),
    ),
})
REFRESH_WEATHER_PLAN_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string, vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN)
    ),
})
DELETE_STORM_ALERT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string, vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN)
    ),
    vol.Required(SERVICE_FIELD_ALERT_ID): vol.All(
        cv.string, vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN)
    ),
})
# Experimental third-party MQTT bridge — see implementation notes §15. ``username``/
# ``password`` accept any printable text; the device firmware is expected to
# AES-256-CBC-encrypt them itself if the cloud relay forbids plaintext, but
# until that path is verified the integration sends them as-is and lets the
# user verify reception against their own broker.
SET_THIRD_PARTY_MQTT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string, vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN)
    ),
    vol.Required(SERVICE_FIELD_ENABLE): cv.boolean,
    vol.Required(SERVICE_FIELD_IP): vol.All(
        cv.string, vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN), vol.Length(max=128)
    ),
    vol.Required(SERVICE_FIELD_PORT): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=65535)
    ),
    vol.Optional(SERVICE_FIELD_USERNAME, default=""): vol.All(
        cv.string, vol.Length(max=128)
    ),
    vol.Optional(SERVICE_FIELD_PASSWORD, default=""): vol.All(
        cv.string, vol.Length(max=128)
    ),
    vol.Optional(SERVICE_FIELD_TOKEN, default=""): vol.All(
        cv.string, vol.Length(max=512)
    ),
})
QUERY_THIRD_PARTY_MQTT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string, vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN)
    ),
})
SEND_BLE_COMMAND_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string, vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN)
    ),
    vol.Required(SERVICE_FIELD_CMD): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=65535)
    ),
    vol.Required(SERVICE_FIELD_BODY): vol.Any(dict, cv.string),
    vol.Optional(SERVICE_FIELD_FLAGS, default=0): vol.All(
        vol.Coerce(int), vol.Range(min=0, max=65535)
    ),
    vol.Optional(SERVICE_FIELD_WAIT_FOR_ACK, default=False): cv.boolean,
    vol.Optional(SERVICE_FIELD_ACK_TIMEOUT, default=5.0): vol.All(
        vol.Coerce(float), vol.Range(min=0.5, max=60.0)
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
    hass: HomeAssistant, device_id: str
) -> JackerySolarVaultCoordinator | None:
    """Return the coordinator whose payload contains the given device id."""
    for coordinator in _loaded_coordinators(hass):
        if device_id in (coordinator.data or {}):
            return coordinator
    return None


def _coordinator_for_system(
    hass: HomeAssistant, system_id: str
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
    raise ValueError(f"invalid JSON constant: {constant}")  # noqa: TRY003


def _json_native_value(value: Any) -> Any:  # noqa: ANN401
    """Return a JSON-native value or raise ValueError."""
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise ValueError("body must contain only finite numbers")  # noqa: TRY003
    if isinstance(value, list):
        return [_json_native_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("body object keys must be strings")  # noqa: TRY003, TRY004
            normalized[key] = _json_native_value(item)
        return normalized
    raise ValueError("body must contain only JSON-compatible values")  # noqa: TRY003


def _json_native_body(body: dict[Any, Any], device_id: str) -> dict[str, Any]:
    """Return a JSON-native object body or raise a translated service error."""
    try:
        normalized = _json_native_value(body)
    except ValueError as err:
        raise _service_validation_error(
            "send_ble_command_failed",
            device_id=device_id,
            error=err,
        ) from err
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
            raise _service_validation_error(
                "send_ble_command_failed",
                device_id=device_id,
                error=f"body is not valid JSON: {err}",
            ) from err
        if isinstance(parsed, dict):
            return _json_native_body(parsed, device_id)
        raise _service_validation_error(
            "send_ble_command_failed",
            device_id=device_id,
            error="body JSON must be an object",
        )
    raise _service_validation_error(
        "send_ble_command_failed",
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
        raise ConfigEntryAuthFailed(  # noqa: TRY003
            "Jackery credentials were rejected while renaming a system. "
            "Re-authentication is required."
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
    hass: HomeAssistant, call: ServiceCall
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
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Delete a storm alert on the matching coordinator."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="delete_storm_alert_failed",
        extra_placeholders={"alert_id": ""},
    )
    alert_id = _storm_alert_id_from_service(
        call.data[SERVICE_FIELD_ALERT_ID], device_id
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
    hass: HomeAssistant, call: ServiceCall
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
    hass: HomeAssistant, call: ServiceCall
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
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Write one experimental binary command frame over the active BLE session."""
    device_id = _device_id_from_service(
        hass,
        call.data[SERVICE_FIELD_DEVICE_ID],
        translation_key="send_ble_command_failed",
    )
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        raise _service_validation_error(
            "send_ble_command_failed",
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
        raise _service_validation_error(
            "send_ble_command_failed",
            device_id=device_id,
            error=err,
        ) from err
    if not sent:
        raise _service_validation_error(
            "send_ble_command_failed",
            device_id=device_id,
            error=(
                "BLE writes are disabled or no active BLE session exists after "
                "waiting for reconnect"
            ),
        )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's domain-scoped service actions.

    Called once from ``async_setup``. HA tears the services down on
    shutdown automatically; multi-entry setups share the same handler
    instances which then dispatch to the matching coordinator.
    """
    if not hass.services.has_service(DOMAIN, SERVICE_RENAME_SYSTEM):

        async def _handle_rename(call: ServiceCall) -> None:
            await _async_handle_rename(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_RENAME_SYSTEM,
            _handle_rename,
            schema=RENAME_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_WEATHER_PLAN):

        async def _handle_refresh_weather_plan(call: ServiceCall) -> None:
            await _async_handle_refresh_weather_plan(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_WEATHER_PLAN,
            _handle_refresh_weather_plan,
            schema=REFRESH_WEATHER_PLAN_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_DELETE_STORM_ALERT):

        async def _handle_delete_storm_alert(call: ServiceCall) -> None:
            await _async_handle_delete_storm_alert(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_DELETE_STORM_ALERT,
            _handle_delete_storm_alert,
            schema=DELETE_STORM_ALERT_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SET_THIRD_PARTY_MQTT_CONFIG):

        async def _handle_set_third_party_mqtt(call: ServiceCall) -> None:
            await _async_handle_set_third_party_mqtt_config(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_THIRD_PARTY_MQTT_CONFIG,
            _handle_set_third_party_mqtt,
            schema=SET_THIRD_PARTY_MQTT_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG):

        async def _handle_query_third_party_mqtt(call: ServiceCall) -> None:
            await _async_handle_query_third_party_mqtt_config(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG,
            _handle_query_third_party_mqtt,
            schema=QUERY_THIRD_PARTY_MQTT_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_BLE_COMMAND):

        async def _handle_send_ble_command(call: ServiceCall) -> None:
            await _async_handle_send_ble_command(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_BLE_COMMAND,
            _handle_send_ble_command,
            schema=SEND_BLE_COMMAND_SCHEMA,
        )


def _strip_jackery_subdevice_suffix(device_id: str) -> str:  # noqa: E302, RUF100
    """Return the parent numeric Jackery device identifier by removing a recognized subdevice suffix.

    Parameters:
        device_id (str): Device identifier that may include a trailing `_suffix` (e.g., "12345_child").

    Returns:
        str: The leading numeric device identifier if a suffix is present (e.g., "12345"), otherwise the original input.
    """
    match = _JACKERY_MAIN_DEVICE_RE.match(device_id)
    return match.group(1) if match else device_id


async def _async_handle_send_device_schedule(  # noqa: E302, RUF100
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Send a device schedule frame (TIMER_TASK_ADD/DELETE/UPDATE/READ) to a Jackery device.

    Resolves the provided device identifier to the owning Jackery device, parses the schedule `body` (accepts a mapping or a JSON object string), and forwards the action to the coordinator to send the schedule frame.

    Parameters:
        call (ServiceCall): Service call whose `data` must include:
            - `device_id` (str): device identifier or Home Assistant device registry id to resolve.
            - `action_id` (int): schedule action identifier (one of 3015, 3016, 3017, 3018).
            - `body` (dict | str): schedule payload as a mapping or a JSON-encoded object string.

    Raises:
        ServiceValidationError: if the device cannot be resolved to a coordinator, if `body` is invalid, or if sending the schedule fails.
    """
    raw = call.data[SERVICE_FIELD_DEVICE_ID].strip()
    device_id = _resolve_jackery_device_id(hass, raw)
    coordinator = _coordinator_for_device(hass, device_id)
    if coordinator is None:
        raise _service_validation_error(
            "send_device_schedule_failed",
            device_id=device_id,
            error="no Jackery entry owns this device id",
        )
    body = _ble_body_from_service(call.data[SERVICE_FIELD_BODY], device_id)
    try:
        await coordinator.async_send_device_schedule(
            device_id,
            action_id=int(call.data[SERVICE_FIELD_ACTION_ID]),
            body=body,
        )
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        raise _service_validation_error(
            "send_device_schedule_failed",
            device_id=device_id,
            error=err,
        ) from err
