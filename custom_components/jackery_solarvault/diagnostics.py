"""Diagnostics support for Jackery SolarVault."""

from collections.abc import Mapping
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import JackeryConfigEntry
from .const import REDACT_KEYS
from .coordinator import JackerySolarVaultCoordinator


def _redacted_payload_map(
    payloads: Mapping[Any, Any],
    prefix: str,
) -> dict[str, Any]:
    """Redact payloads without exposing device IDs or serials as dict keys.

    Home Assistant's async_redact_data redacts values by key inside payloads. It
    does not anonymize mapping keys such as device IDs, system IDs or battery
    serial numbers, so diagnostics that are safe to share must replace those
    outer keys with stable local labels.
    """
    redacted: dict[str, Any] = {}
    for index, key in enumerate(
        sorted(payloads, key=lambda value: str(value)), start=1
    ):
        payload = payloads[key]
        label = f"{prefix}_{index}"
        if isinstance(payload, dict):
            redacted[label] = async_redact_data(payload, REDACT_KEYS)
        else:
            redacted[label] = async_redact_data({"value": payload}, REDACT_KEYS)
    return redacted


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> dict[str, Any]:
    """Get config entry diagnostics."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data

    devices = _redacted_payload_map(coordinator.data or {}, "device")

    raw = {
        "coordinator": {
            "update_interval_seconds": (
                int(coordinator.configured_update_interval.total_seconds())
            ),
            "fixed_rate_polling": True,
        },
        "login_response": async_redact_data(
            coordinator.api.last_login_response or {}, REDACT_KEYS
        ),
        "system_list_response": async_redact_data(
            coordinator.api.last_system_list_response or {}, REDACT_KEYS
        ),
        "property_responses": _redacted_payload_map(
            coordinator.api.last_property_responses, "property_response"
        ),
        "alarm_response": async_redact_data(
            coordinator.api.last_alarm_response or {}, REDACT_KEYS
        ),
        "statistic_response": async_redact_data(
            coordinator.api.last_statistic_response or {}, REDACT_KEYS
        ),
        "price_response": async_redact_data(
            coordinator.api.last_price_response or {}, REDACT_KEYS
        ),
        "price_sources_response": async_redact_data(
            coordinator.api.last_price_sources_response or {}, REDACT_KEYS
        ),
        "price_history_config_response": async_redact_data(
            coordinator.api.last_price_history_config_response or {}, REDACT_KEYS
        ),
        "device_statistic_responses": _redacted_payload_map(
            coordinator.api.last_device_statistic_responses, "device_statistic_response"
        ),
        "device_period_stat_responses": _redacted_payload_map(
            coordinator.api.last_device_period_stat_responses,
            "device_period_stat_response",
        ),
        "battery_pack_responses": _redacted_payload_map(
            coordinator.api.last_battery_pack_responses, "battery_pack_response"
        ),
        "ota_responses": _redacted_payload_map(
            coordinator.api.last_ota_responses, "ota_response"
        ),
        "location_responses": _redacted_payload_map(
            coordinator.api.last_location_responses, "location_response"
        ),
        "mqtt": async_redact_data(coordinator.mqtt_diagnostics, REDACT_KEYS),
    }

    return {
        "entry_data": async_redact_data(dict(entry.data), REDACT_KEYS),
        "options": async_redact_data(dict(entry.options), REDACT_KEYS),
        "devices": devices,
        "raw_api": raw,
    }
