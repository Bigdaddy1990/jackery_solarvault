"""Diagnostics support for Jackery SolarVault."""

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import JackeryConfigEntry
from .const import (
    DIAGNOSTICS_SCHEMA_VERSION,
    DOMAIN,
    REDACT_KEYS as _STATIC_REDACT_KEYS,
)
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
_LOCAL_MQTT_RUNTIME_KEY = "local_mqtt_client"


def _redacted_payload_map(
    payloads: Mapping[Any, Any],
    prefix: str,
    redact_keys: frozenset[str],
) -> dict[str, Any]:
    """Redact payloads without exposing device IDs or serials as dict keys.

    Home Assistant's ``async_redact_data`` redacts values by key inside
    payloads. It does not anonymize mapping keys such as device IDs,
    system IDs or battery serial numbers, so diagnostics that are safe to
    share must replace those outer keys with stable local labels.

    The redact set comes from :func:`.util.active_redact_keys` so the entry
    option and legacy ``JACKERY_DEV_MODE`` env var can disable redaction during
    local integration development.
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


def _local_mqtt_diagnostics(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    redactions_disabled: bool,
) -> dict[str, Any]:
    """Return the live local-MQTT client diagnostics for this entry."""
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(bucket, dict):
        return {"enabled": False}
    client = bucket.get(_LOCAL_MQTT_RUNTIME_KEY)
    snapshot = getattr(client, "diagnostics_snapshot", None)
    if not callable(snapshot):
        return {"enabled": False}
    diag = snapshot(redact=not redactions_disabled)
    return diag if isinstance(diag, dict) else {"enabled": True}


async def async_get_config_entry_diagnostics(  # noqa: RUF029 - HA hook is async.
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> dict[str, Any]:
    """Get config entry diagnostics."""
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
        "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
        "rejection_metrics": {
            "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
            "counters": {
                "http_auth_rejections": 0,
                "mqtt_broker_rejections": 0,
                "payload_validation_rejections": 0,
                "schema_rejections": 0,
                "timestamp_skew_rejections": 0,
                "auth_token_expiry_rejections": 0,
            },
            "last_rejection": None,
        },
    }
