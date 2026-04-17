"""Diagnostics support for Jackery SolarVault."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import JackerySolarVaultCoordinator

REDACT = {
    CONF_PASSWORD, CONF_USERNAME,
    "macId", "token", "mqttPassWord", "mqttPassword",
    "deviceSn", "devSn", "sn", "systemSn",
    "wname", "mac", "wip",
    "bluetoothKey", "deviceSecret", "randomSalt",
    "phone", "mobPhone", "email", "bindEmail",
    "avatar", "appUserName", "nickname",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: JackerySolarVaultCoordinator = hass.data[DOMAIN][entry.entry_id]

    devices = {}
    for dev_id, payload in (coordinator.data or {}).items():
        devices[dev_id] = async_redact_data(payload, REDACT)

    raw = {
        "login_response": async_redact_data(
            coordinator.api.last_login_response or {}, REDACT
        ),
        "system_list_response": async_redact_data(
            coordinator.api.last_system_list_response or {}, REDACT
        ),
        "property_responses": {
            dev_id: async_redact_data(resp, REDACT)
            for dev_id, resp in coordinator.api.last_property_responses.items()
        },
        "alarm_response": async_redact_data(
            coordinator.api.last_alarm_response or {}, REDACT
        ),
        "statistic_response": async_redact_data(
            coordinator.api.last_statistic_response or {}, REDACT
        ),
        "price_response": async_redact_data(
            coordinator.api.last_price_response or {}, REDACT
        ),
    }

    return {
        "entry_data": async_redact_data(dict(entry.data), REDACT),
        "options": dict(entry.options),
        "devices": devices,
        "raw_api": raw,
    }
