"""Diagnostics support for Jackery SolarVault."""

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import JackeryConfigEntry, _local_mqtt_client
from .const import REDACT_KEYS as _STATIC_REDACT_KEYS
from .coordinator import JackerySolarVaultCoordinator
from .util import (
    active_redact_keys,
    dev_mode_redactions_disabled,
    diagnostic_redactions_disabled,
)

_LOGGER = logging.getLogger(__name__)

# Kept as an import alias so tests / external callers can still reference the
# static redact-key set when needed. Runtime redaction in this module always
# goes through ``active_redact_keys()`` so the ``JACKERY_DEV_MODE`` env-var
# toggle takes effect without restart.
REDACT_KEYS = _STATIC_REDACT_KEYS


def _redacted_payload_map(
    payloads: Mapping[Any, Any],
    prefix: str,
    redact_keys: frozenset[str],
) -> dict[str, Any]:
    """
    Replace mapping outer keys (e.g., device IDs or serials) with stable local labels and return a map of each label to a redacted payload.
    
    Parameters:
        payloads (Mapping[Any, Any]): Mapping whose keys will be replaced by generated labels; values are payloads to redact.
        prefix (str): Prefix used for generated labels (labels are formatted as "<prefix>_<index>" starting at 1).
        redact_keys (frozenset[str]): Set of field names that must be redacted from each payload.
    
    Returns:
        dict[str, Any]: A dictionary mapping generated labels to redacted payloads. If a payload is not a dict it is wrapped as {"value": payload} before redaction.
    """
    redacted: dict[str, Any] = {}
    for index, key in enumerate(
        sorted(payloads, key=lambda value: str(value)), start=1
    ):
        payload = payloads[key]
        label = f"{prefix}_{index}"
        if isinstance(payload, dict):
            redacted[label] = async_redact_data(payload, redact_keys)
        else:
            redacted[label] = async_redact_data({"value": payload}, redact_keys)
    return redacted


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> dict[str, Any]:
    """
    Build the diagnostics export for the given config entry.
    
    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        entry (JackeryConfigEntry): The integration's config entry to export diagnostics for.
    
    Returns:
        dict[str, Any]: Diagnostics payload containing:
            - `entry_data`: redacted copy of the config entry's stored data.
            - `options`: redacted copy of the config entry's options.
            - `devices`: mapping of stable local device labels to redacted device payloads.
            - `raw_api`: raw diagnostics of coordinator/API responses and transport snapshots (redacted according to the entry's redaction settings). Note: if diagnostics redactions are disabled for the entry, sensitive fields such as credentials, serial numbers, and bluetoothKey may be included unredacted.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    redact_keys = active_redact_keys(entry)
    redactions_disabled = diagnostic_redactions_disabled(entry)
    if redactions_disabled:
        source = (
            "JACKERY_DEV_MODE=1"
            if dev_mode_redactions_disabled()
            else "enable_unredacted_diagnostics option"
        )
        _LOGGER.warning(
            "Jackery diagnostics export is running with %s - "
            "credentials, serial numbers and the bluetoothKey are included "
            "unredacted. Do NOT share this export publicly.",
            source,
        )

    devices = _redacted_payload_map(coordinator.data or {}, "device", redact_keys)

    raw = {
        "coordinator": {
            "update_interval_seconds": (
                int(coordinator.configured_update_interval.total_seconds())
            ),
            "coordinator_polling": True,
            "dev_mode": dev_mode_redactions_disabled(),
            "redactions_disabled": redactions_disabled,
        },
        "login_response": async_redact_data(
            coordinator.api.last_login_response or {}, redact_keys
        ),
        "system_list_response": async_redact_data(
            coordinator.api.last_system_list_response or {}, redact_keys
        ),
        "property_responses": _redacted_payload_map(
            coordinator.api.last_property_responses, "property_response", redact_keys
        ),
        "alarm_response": async_redact_data(
            coordinator.api.last_alarm_response or {}, redact_keys
        ),
        "statistic_response": async_redact_data(
            coordinator.api.last_statistic_response or {}, redact_keys
        ),
        "price_response": async_redact_data(
            coordinator.api.last_price_response or {}, redact_keys
        ),
        "price_sources_response": async_redact_data(
            coordinator.api.last_price_sources_response or {}, redact_keys
        ),
        "price_history_config_response": async_redact_data(
            coordinator.api.last_price_history_config_response or {}, redact_keys
        ),
        "device_statistic_responses": _redacted_payload_map(
            coordinator.api.last_device_statistic_responses,
            "device_statistic_response",
            redact_keys,
        ),
        "device_period_stat_responses": _redacted_payload_map(
            coordinator.api.last_device_period_stat_responses,
            "device_period_stat_response",
            redact_keys,
        ),
        "battery_pack_responses": _redacted_payload_map(
            coordinator.api.last_battery_pack_responses,
            "battery_pack_response",
            redact_keys,
        ),
        "ota_responses": _redacted_payload_map(
            coordinator.api.last_ota_responses, "ota_response", redact_keys
        ),
        "location_responses": _redacted_payload_map(
            coordinator.api.last_location_responses, "location_response", redact_keys
        ),
        "mqtt": async_redact_data(
            coordinator.mqtt_diagnostics_snapshot(
                redact_topics=not redactions_disabled
            ),
            redact_keys,
        ),
        "local_mqtt": _local_mqtt_diagnostics(hass, entry, redactions_disabled),
        "ble_transport": _redacted_payload_map(
            coordinator.ble_observations(), "ble_device", redact_keys
        ),
        "statistics_backfill": async_redact_data(
            coordinator.statistics_backfill_diagnostics,
            redact_keys,
        ),
    }

    return {
        "entry_data": async_redact_data(dict(entry.data), redact_keys),
        "options": async_redact_data(dict(entry.options), redact_keys),
        "devices": devices,
        "raw_api": raw,
    }


def _local_mqtt_diagnostics(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    redactions_disabled: bool,
) -> dict[str, Any]:
    """
    Build the diagnostics block for the integration's local MQTT client, or report that local MQTT is disabled.
    
    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        entry (JackeryConfigEntry): Config entry for the integration.
        redactions_disabled (bool): If True, request the client's diagnostics without redaction; if False, request a redacted snapshot.
    
    Returns:
        dict[str, Any]: `{"enabled": False}` when no local MQTT client is available, otherwise the client's diagnostics snapshot (respecting the requested redaction).
    """
    client = _local_mqtt_client(hass, entry)
    if client is None:
        return {"enabled": False}
    return client.diagnostics_snapshot(redact=not redactions_disabled)
