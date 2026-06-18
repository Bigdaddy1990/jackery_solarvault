"""Service-action setup for the Jackery SolarVault integration.

The handlers live here (instead of inline in __init__.py) so the global
async_setup stays focused on bootstrap and so the per-action routing logic
is easy to unit-test in isolation.

All three actions follow the same routing contract:

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

import logging
from typing import Any

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
    SERVICE_FIELD_ALERT_ID,
    SERVICE_FIELD_DEVICE_ID,
    SERVICE_FIELD_NEW_NAME,
    SERVICE_FIELD_SYSTEM_ID,
    SERVICE_NON_EMPTY_TEXT_PATTERN,
    SERVICE_NUMERIC_ID_PATTERN,
    SERVICE_REFRESH_WEATHER_PLAN,
    SERVICE_RENAME_SYSTEM,
)
from .coordinator import JackerySolarVaultCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
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
    """Send a device schedule frame to a Jackery device.

    Parameters:
        call (ServiceCall): Service call whose `data` must include:
            - `device_id` (str): device identifier or Home Assistant device
              registry id to resolve
            - `action_id` (int): schedule action identifier (one of 3015,
              3016, 3017, 3018)
            - `body` (dict | str): schedule payload as a mapping or a
              JSON-encoded object string

    Raises:
        ServiceValidationError: if the device cannot be resolved, the body is
        invalid, or sending fails
    """
    raw = call.data[SERVICE_FIELD_DEVICE_ID].strip()
    device_id = _resolve_jackery_device_id(hass, raw)
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
            action_id=int(call.data[SERVICE_FIELD_ACTION_ID]),
            body=body,
        )
    except (JackeryError, LookupError, RuntimeError, ValueError) as err:
        msg = "send_device_schedule_failed"
        raise _service_validation_error(
            msg,
            device_id=device_id,
            error=err,
        ) from err


def _entry_for_coordinator(
    hass: HomeAssistant, coordinator: JackerySolarVaultCoordinator
) -> ConfigEntry | None:
    """Locate the loaded config entry that owns a coordinator.

    Returns:
        The matching ConfigEntry, or None if not found.
    """
    for loaded_entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if getattr(loaded_entry, "runtime_data", None) is coordinator:
            return loaded_entry
    return None
