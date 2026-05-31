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
import re
from typing import Any, Final

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .client import JackeryError
from .const import (
    DOMAIN,
    FIELD_ID,
    FIELD_SYSTEM_ID,
    PAYLOAD_SYSTEM,
    SERVICE_DELETE_STORM_ALERT,
    SERVICE_FIELD_ACK_TIMEOUT,
    SERVICE_FIELD_ACTION_ID,
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
    SERVICE_SEND_DEVICE_SCHEDULE,
    SERVICE_SET_THIRD_PARTY_MQTT_CONFIG,
)
from .coordinator import JackerySolarVaultCoordinator

_LOGGER = logging.getLogger(__name__)


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
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
})
DELETE_STORM_ALERT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
    vol.Required(SERVICE_FIELD_ALERT_ID): vol.All(
        cv.string, vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN)
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
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
})
SEND_BLE_COMMAND_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
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
SEND_DEVICE_SCHEDULE_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(cv.string, vol.Length(min=1)),
    vol.Required(SERVICE_FIELD_ACTION_ID): vol.All(
        vol.Coerce(int), vol.In((3015, 3016, 3017, 3018))
    ),
    vol.Required(SERVICE_FIELD_BODY): vol.Any(dict, cv.string),
})


# ---------------------------------------------------------------------------
# Coordinator routing helpers
# ---------------------------------------------------------------------------


def _loaded_coordinators(hass: HomeAssistant) -> list[JackerySolarVaultCoordinator]:
    """Collect active JackerySolarVaultCoordinator instances from loaded config entries.

    Returns:
        list[JackerySolarVaultCoordinator]: Active coordinators for loaded Jackery config entries.
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
    """Return the parent numeric Jackery device identifier by removing a recognized subdevice suffix.

    Parameters:
        device_id (str): Device identifier that may include a trailing `_suffix` (e.g., "12345_child").

    Returns:
        str: The leading numeric device identifier if a suffix is present (e.g., "12345"), otherwise the original input.
    """
    match = _JACKERY_MAIN_DEVICE_RE.match(device_id)
    return match.group(1) if match else device_id


def _resolve_jackery_device_id(hass: HomeAssistant, raw: str) -> str:
    """Resolve a Home Assistant device-registry id or Jackery compound id to the parent Jackery numeric device id.

    Parameters:
        raw (str): A device-registry id or a Jackery device identifier that may include a documented subdevice suffix.

    Returns:
        parent_id (str): The parent Jackery numeric device id with any documented subdevice suffix removed.
    """
    registry = dr.async_get(hass)
    device = registry.async_get(raw)
    if device is not None:
        seen: set[str] = set()
        while device.via_device_id and device.via_device_id not in seen:
            seen.add(device.via_device_id)
            parent = registry.async_get(device.via_device_id)
            if parent is None:
                break
            device = parent
        for domain, identifier in device.identifiers:
            if domain == DOMAIN:
                return _strip_jackery_subdevice_suffix(str(identifier))
    return _strip_jackery_subdevice_suffix(raw)


def _coordinator_for_device(
    hass: HomeAssistant, device_id: str
) -> JackerySolarVaultCoordinator | None:
    """Locate the coordinator that manages the specified Jackery device id.

    Returns:
        The coordinator that manages the device, or `None` if no matching coordinator is loaded.
    """
    for coordinator in _loaded_coordinators(hass):
        if device_id in (coordinator.data or {}):
            return coordinator
    return None


def _coordinator_for_system(
    hass: HomeAssistant, system_id: str
) -> JackerySolarVaultCoordinator | None:
    """Finds the loaded coordinator that manages the specified Jackery system id.

    Searches each loaded coordinator's payloads for a `system` object whose `id` or `system_id` (converted to string) equals the provided `system_id`.

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
) -> ServiceValidationError:
    """Constructs a ServiceValidationError with the integration DOMAIN and populated translation placeholders for a device and an error.

    Parameters:
        translation_key (str): Translation key to identify the localized error message.
        device_id (str): Value inserted into the `device_id` translation placeholder.
        error (object): Value converted to string and inserted into the `error` translation placeholder.

    Returns:
        ServiceValidationError: Error with `translation_domain` set to DOMAIN, `translation_key` set to `translation_key`, and `translation_placeholders` containing `device_id` and `error`.
    """
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders={
            "device_id": device_id,
            "error": str(error),
        },
    )


def _ble_body_from_service(raw_body: Any, device_id: str) -> dict[str, Any]:
    """Parse a BLE command `body` value from a service call into a dict.

    Accepts a mapping (returned as a shallow copy) or a JSON-encoded object string. If `raw_body` is a string it must decode to a JSON object; otherwise this function raises ServiceValidationError with translation key "send_ble_command_failed" and translation placeholders that include the provided `device_id` and an error message.

    Parameters:
        raw_body (Any): Mapping or JSON string encoding an object to use as the BLE body.
        device_id (str): Jackery device identifier included in validation error placeholders.

    Returns:
        dict[str, Any]: Parsed body mapping to send with the BLE command.

    Raises:
        ServiceValidationError: If `raw_body` is neither a mapping nor a JSON object string, or if JSON parsing fails.
    """
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
    """Forward a rename to the API client of the matching coordinator."""
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
    hass: HomeAssistant, call: ServiceCall
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
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Delete a storm alert for the specified Jackery device.

    Raises:
        ServiceValidationError: If no coordinator owns the resolved device id, or if the coordinator reports an error while deleting the alert. The error includes translation placeholders `device_id`, `alert_id`, and `error`.
    """
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
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Send a third-party MQTT configuration to the coordinator that owns the resolved Jackery device.

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
        ServiceValidationError: If no loaded coordinator owns the resolved device id, or if applying the configuration fails. The error includes translation placeholders `device_id` and `error`.
    """
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
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Query a device's third-party MQTT configuration.

    Resolves the provided device identifier to the owning Jackery coordinator and requests the device's third-party MQTT settings from the coordinator API.

    Parameters:
        call (ServiceCall): Service call containing SERVICE_FIELD_DEVICE_ID with the device identifier to query.

    Raises:
        ServiceValidationError: If no Jackery coordinator owns the resolved device id, or if the coordinator query fails. The integration uses translation_key `query_third_party_mqtt_config_failed` for errors.
    """
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
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Send a BLE command frame to a Jackery device.

    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        call (ServiceCall): Service call containing the following data fields:
            - SERVICE_FIELD_DEVICE_ID: target device identifier (string). May be a registry device id or a Jackery device id; the handler resolves to the parent Jackery numeric id.
            - SERVICE_FIELD_CMD: numeric command identifier.
            - SERVICE_FIELD_BODY: either a mapping (dict) or a JSON-encoded object string that decodes to a mapping.
            - SERVICE_FIELD_FLAGS (optional): integer flags (default 0).
            - SERVICE_FIELD_WAIT_FOR_ACK (optional): boolean indicating whether to wait for an acknowledgement (default False).
            - SERVICE_FIELD_ACK_TIMEOUT (optional): float acknowledgement timeout in seconds (default 5.0).

    Raises:
        ServiceValidationError: with translation key "send_ble_command_failed" when the target coordinator cannot be found, the `BODY` is invalid or not a mapping, the send operation raises an error, or the BLE write was not performed (for example, writes disabled or no active BLE session).
    """
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


async def _async_handle_send_device_schedule(
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


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's domain-scoped Home Assistant services and their handlers.

    Registers the following services (if not already present) and wires each to the integration's internal async handler: rename system, refresh weather plan, delete storm alert, set/query third-party MQTT config, send BLE command, and send device schedule. Each service is registered with this module's corresponding voluptuous schema and forwards validated ServiceCall objects to the integration handlers.
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
            """Dispatches the Home Assistant service call to the integration handler that applies a device's third-party MQTT configuration.

            The `call` data must conform to `SET_THIRD_PARTY_MQTT_SCHEMA`.
            """
            await _async_handle_set_third_party_mqtt_config(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_THIRD_PARTY_MQTT_CONFIG,
            _handle_set_third_party_mqtt,
            schema=SET_THIRD_PARTY_MQTT_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG):

        async def _handle_query_third_party_mqtt(call: ServiceCall) -> None:
            """Forward a "query third-party MQTT config" service call to the integration's async handler."""
            await _async_handle_query_third_party_mqtt_config(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG,
            _handle_query_third_party_mqtt,
            schema=QUERY_THIRD_PARTY_MQTT_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_BLE_COMMAND):

        async def _handle_send_ble_command(call: ServiceCall) -> None:
            """Forward a Home Assistant send-BLE-command service call to the integration's internal handler.

            Parameters:
                call (ServiceCall): Service call whose data must include the send-BLE-command fields:
                    - `device_id`: target device identifier (string)
                    - `cmd`: command id (int)
                    - `body`: command payload (dict or JSON string)
                    - `flags` (optional): flags for the command (int)
                    - `wait_for_ack` (optional): whether to wait for an acknowledgement (bool)
                    - `ack_timeout` (optional): acknowledgement timeout in seconds (float)
            """
            await _async_handle_send_ble_command(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_BLE_COMMAND,
            _handle_send_ble_command,
            schema=SEND_BLE_COMMAND_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_DEVICE_SCHEDULE):

        async def _handle_send_device_schedule(call: ServiceCall) -> None:
            """Dispatches a service call to send a device schedule frame through the integration.

            Parameters:
                call (ServiceCall): Service call data containing `device_id` (target device identifier), `action_id` (action code for the schedule), and `body` (schedule payload as a dict or JSON string).
            """
            await _async_handle_send_device_schedule(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_DEVICE_SCHEDULE,
            _handle_send_device_schedule,
            schema=SEND_DEVICE_SCHEDULE_SCHEMA,
        )
