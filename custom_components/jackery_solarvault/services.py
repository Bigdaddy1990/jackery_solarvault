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

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

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
    """Return runtime coordinators for loaded Jackery config entries."""
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
    """Return the parent Jackery device id by stripping any subdevice suffix.

    Pass-through for inputs that do not start with digits so non-Jackery
    callers (and legacy main-device ids that already have the parent form)
    stay backwards-compatible.
    """
    match = _JACKERY_MAIN_DEVICE_RE.match(device_id)
    return match.group(1) if match else device_id


def _resolve_jackery_device_id(hass: HomeAssistant, raw: str) -> str:
    """Map an HA device-registry id (UUID) to the parent Jackery device id.

    The device selector in services.yaml hands the handler an HA device-id
    (e.g. ``a1b2c3...``). The integration's device-registry identifiers carry
    the matching ``(DOMAIN, jackery_device_id)`` tuple. Subdevices (battery
    packs, smart meter, plugs, meter heads, collectors) are registered with
    a ``via_device`` link pointing at the parent SolarVault — they share the
    parent's BLE/MQTT radio per PROTOCOL.md §3 and §4. This resolver walks
    the ``via_device`` chain so a subdevice pick from the UI ends up at the
    parent's numeric id, then strips any remaining documented subdevice
    suffix so legacy automations passing the compound id (e.g.
    ``573702884982521856_battery_pack_1``) also resolve to the parent.

    Falls back to ``raw`` unchanged when the input is neither an HA UUID nor
    a recognisable Jackery compound id (defensive: keeps handlers usable in
    test/automation contexts).
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
) -> ServiceValidationError:
    """Build a translated service validation error with common placeholders."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders={
            "device_id": device_id,
            "error": str(error),
        },
    )


def _ble_body_from_service(raw_body: Any, device_id: str) -> dict[str, Any]:
    """Return a dict body from a service object or JSON string."""
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
    """Delete a storm alert on the matching coordinator."""
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
    """Publish the experimental SET_THIRD_PARTY_MQTT_CONFIG (3046) frame."""
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
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Write one experimental binary command frame over the active BLE session."""
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
    """Publish a DownloadDeviceSchedule frame (TIMER_TASK_ADD/DELETE/UPDATE/READ).

    Body shape is the Frida-PCAP-captured layout from
    ``docs/Markdown/MQTT_PROTOCOL.md`` §DownloadDeviceSchedule. The caller
    constructs the body verbatim (mapping or JSON string); the handler
    routes through ``coordinator.async_send_device_schedule`` which adds
    only the wire-invariant ``cmd: 112``.
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

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_DEVICE_SCHEDULE):

        async def _handle_send_device_schedule(call: ServiceCall) -> None:
            await _async_handle_send_device_schedule(hass, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_DEVICE_SCHEDULE,
            _handle_send_device_schedule,
            schema=SEND_DEVICE_SCHEDULE_SCHEMA,
        )
