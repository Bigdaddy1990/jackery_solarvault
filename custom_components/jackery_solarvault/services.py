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

import json
import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.core import callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .api import JackeryError
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
)
from .coordinator import JackerySolarVaultCoordinator

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

_LOGGER = logging.getLogger(__name__)


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
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
})
DELETE_STORM_ALERT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
    vol.Required(SERVICE_FIELD_ALERT_ID): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
    ),
})
# Experimental third-party MQTT bridge — see PROTOCOL.md §15. ``username``/
# ``password`` accept any printable text; the device firmware is expected to
# AES-256-CBC-encrypt them itself if the cloud relay forbids plaintext, but
# until that path is verified the integration sends them as-is and lets the
# user verify reception against their own broker.
SET_THIRD_PARTY_MQTT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
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
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
})
SEND_BLE_COMMAND_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
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


# ---------------------------------------------------------------------------
# Coordinator routing helpers
# ---------------------------------------------------------------------------


def _loaded_coordinators(hass: HomeAssistant) -> list[JackerySolarVaultCoordinator]:
    """Get runtime coordinators for all loaded Jackery config entries.

    Returns:
        list[JackerySolarVaultCoordinator]: Coordinator instances attached to loaded config entries.
    """  # noqa: E501
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
    back to the cloud-facing id. If the input is already a Jackery numeric
    id (legacy automations), the lookup misses and we return the raw value
    unchanged so handlers stay backwards-compatible.
    """
    device = dr.async_get(hass).async_get(raw)
    if device is None:
        return raw
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
    """Finds the first loaded coordinator that contains a system whose ID equals the provided `system_id`.

    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        system_id (str): System identifier to match against each coordinator's stored system `FIELD_ID` or `FIELD_SYSTEM_ID` (string comparison).

    Returns:
        JackerySolarVaultCoordinator | None: The matching coordinator if found, otherwise `None`.
    """  # noqa: E501
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
) -> ServiceValidationError:
    """Create a ServiceValidationError localized to the integration's DOMAIN with common placeholders.

    Parameters:
        translation_key (str): Translation key identifying the error message.
        device_id (str): Device or system identifier to include in the translation placeholders.
        error (object): Error detail; will be converted to a string and included in the placeholders.

    Returns:
        ServiceValidationError: An error with `translation_domain` set to the integration DOMAIN and `translation_placeholders` containing `device_id` and the stringified `error`.
    """  # noqa: E501
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders={
            "device_id": device_id,
            "error": str(error),
        },
    )


def _ble_body_from_service(raw_body: Any, device_id: str) -> dict[str, Any]:  # noqa: ANN401
    """Convert a service-provided BLE command body into a dictionary.

    Accepts either a mapping (returned as a shallow copy) or a JSON string representing an object.
    If a JSON string is provided it is parsed; if parsing fails or the parsed value is not an object,
    a ServiceValidationError is raised and includes `device_id` as a placeholder for error messages.

    Parameters:
        raw_body (Any): A dict-like object or a JSON string representing an object.
        device_id (str): Identifier included in error placeholders when validation fails.

    Returns:
        dict[str, Any]: A dictionary representing the BLE command body.

    Raises:
        ServiceValidationError: If `raw_body` is neither a mapping nor a valid JSON object string,
        or if JSON parsing fails.
    """  # noqa: E501
    if isinstance(raw_body, dict):
        return dict(raw_body)
    if isinstance(raw_body, str):
        try:
            parsed = json.loads(raw_body.strip())
        except ValueError as err:
            raise _service_validation_error(
                "send_ble_command_failed",
                device_id=device_id,
                error=f"body is not valid JSON: {err}",
            ) from err
        if isinstance(parsed, dict):
            return parsed
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


# ---------------------------------------------------------------------------
# Service-action handlers
# ---------------------------------------------------------------------------


async def _async_handle_rename(hass: HomeAssistant, call: ServiceCall) -> None:
    """Send a system rename request to the coordinator that owns the given system id.

    Raises:
        ServiceValidationError: if no coordinator owns the provided `system_id`, or if the API fails; the error includes `system_id` and `error` placeholders.
    """  # noqa: E501
    system_id = call.data[SERVICE_FIELD_SYSTEM_ID].strip()
    new_name = call.data[SERVICE_FIELD_NEW_NAME].strip()
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
        await coordinator.api.async_set_system_name(system_id, new_name)
    except JackeryError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="rename_system_failed",
            translation_placeholders={
                "system_id": system_id,
                "error": str(err),
            },
        ) from err
    await coordinator.async_request_refresh()


async def _async_handle_refresh_weather_plan(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Trigger a weather-plan refresh on the matching coordinator."""
    raw = call.data[SERVICE_FIELD_DEVICE_ID].strip()
    device_id = _resolve_jackery_device_id(hass, raw)
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
    except (JackeryError, LookupError) as err:
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
    """Delete a storm alert for the Jackery device identified in the service call.

    Reads `device_id` and `alert_id` from the service data, resolves the corresponding
    coordinator, and requests the coordinator to delete the specified storm alert.
    If no coordinator owns the resolved device id or the coordinator/API call fails,
    a ServiceValidationError is raised with `translation_key="delete_storm_alert_failed"`
    and placeholders `device_id`, `alert_id`, and `error`.
    """  # noqa: E501
    raw = call.data[SERVICE_FIELD_DEVICE_ID].strip()
    alert_id = call.data[SERVICE_FIELD_ALERT_ID].strip()
    device_id = _resolve_jackery_device_id(hass, raw)
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
    except (JackeryError, LookupError) as err:
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
    """Configure third-party MQTT settings for a Jackery device.

    Expects the service call's data to include:
    - device_id (str): HA device id or Jackery device id; will be resolved to the internal Jackery device id.
    - enable (bool): whether to enable third-party MQTT.
    - ip (str): MQTT broker IP or host (string, whitespace will be stripped).
    - port (int): MQTT broker port (1..65535).
    - username (str, optional): MQTT username (defaults to empty string).
    - password (str, optional): MQTT password (defaults to empty string).
    - token (str, optional): MQTT authentication token (defaults to empty string).

    Raises:
        ServiceValidationError: If no coordinator owns the resolved device_id, or if the coordinator/API returns an error; the exception's translation_placeholders include `device_id` and an `error` message.
    """  # noqa: E501
    raw = call.data[SERVICE_FIELD_DEVICE_ID].strip()
    device_id = _resolve_jackery_device_id(hass, raw)
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
            enable=bool(call.data[SERVICE_FIELD_ENABLE]),
            ip=str(call.data[SERVICE_FIELD_IP]).strip(),
            port=int(call.data[SERVICE_FIELD_PORT]),
            username=str(call.data.get(SERVICE_FIELD_USERNAME, "")),
            password=str(call.data.get(SERVICE_FIELD_PASSWORD, "")),
            token=str(call.data.get(SERVICE_FIELD_TOKEN, "")),
        )
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
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
    raw = call.data[SERVICE_FIELD_DEVICE_ID].strip()
    device_id = _resolve_jackery_device_id(hass, raw)
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
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
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
    """Send a BLE command frame to the device specified in the service call by forwarding the request to the device's coordinator.

    Parameters:
        call (ServiceCall): Service call whose `data` must include:
            - `device_id` (str): Target device id or Home Assistant device registry id.
            - `cmd` (int): Numeric command identifier.
            - `body` (dict | str): Command payload as a mapping or a JSON string representing an object.
            - `flags` (int, optional): BLE flags (default 0).
            - `wait_for_ack` (bool, optional): Whether to wait for an ACK (default False).
            - `ack_timeout` (float, optional): ACK timeout in seconds (default 5.0).

    Raises:
        ServiceValidationError: If no coordinator owns the device id, if the `body` is invalid,
        if parameter coercion/validation fails, or if the command could not be sent (including when
        BLE writes are disabled or there is no active BLE session). The error will use the
        `translation_key` "send_ble_command_failed".
    """  # noqa: E501
    raw = call.data[SERVICE_FIELD_DEVICE_ID].strip()
    device_id = _resolve_jackery_device_id(hass, raw)
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
            cmd=int(call.data[SERVICE_FIELD_CMD]),
            body=body,
            flags=int(call.data.get(SERVICE_FIELD_FLAGS, 0)),
            wait_for_ack=bool(call.data.get(SERVICE_FIELD_WAIT_FOR_ACK, False)),
            ack_timeout_sec=float(call.data.get(SERVICE_FIELD_ACK_TIMEOUT, 5.0)),
        )
    except (RuntimeError, ValueError) as err:
        raise _service_validation_error(
            "send_ble_command_failed",
            device_id=device_id,
            error=err,
        ) from err
    if not sent:
        raise _service_validation_error(
            "send_ble_command_failed",
            device_id=device_id,
            error="BLE writes are disabled or no active BLE session exists",
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
