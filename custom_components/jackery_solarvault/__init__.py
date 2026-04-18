"""Jackery SolarVault integration."""
from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import JackeryApi, JackeryAuthError, JackeryError
from .const import DEFAULT_SCAN_INTERVAL_SEC, DOMAIN, PLATFORMS
from .coordinator import JackerySolarVaultCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_RENAME_SYSTEM = "rename_system"
SERVICE_SET_MAX_POWER = "set_max_power"
RENAME_SCHEMA = vol.Schema(
    {
        vol.Required("system_id"): cv.string,
        vol.Required("new_name"): vol.All(cv.string, vol.Length(min=1, max=64)),
    }
)
SET_MAX_POWER_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): cv.string,
        vol.Required("max_power"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=2500)
        ),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Jackery SolarVault from a config entry."""
    session = async_get_clientsession(hass)

    api = JackeryApi(
        session=session,
        account=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
    )
    try:
        await api.async_login()
    except JackeryAuthError as err:
        _LOGGER.error("Jackery login failed: %s", err)
        return False

    interval_sec = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC)
    coordinator = JackerySolarVaultCoordinator(
        hass, entry, api, timedelta(seconds=interval_sec)
    )

    await coordinator.async_discover()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the rename service (once — subsequent config entries share it)
    if not hass.services.has_service(DOMAIN, SERVICE_RENAME_SYSTEM):
        async def _handle_rename(call: ServiceCall) -> None:
            system_id = call.data["system_id"]
            new_name = call.data["new_name"]
            # Find any coordinator that knows this system_id and use its API
            for coord in hass.data.get(DOMAIN, {}).values():
                try:
                    await coord.api.async_set_system_name(system_id, new_name)
                    await coord.async_request_refresh()
                    return
                except JackeryError as err:
                    _LOGGER.debug(
                        "rename_system via %s failed: %s", coord.entry.entry_id, err,
                    )
                    continue
            raise RuntimeError(
                f"No Jackery coordinator could rename system {system_id}"
            )

        hass.services.async_register(
            DOMAIN, SERVICE_RENAME_SYSTEM, _handle_rename, schema=RENAME_SCHEMA,
        )

    # Experimental: set_max_power service
    if not hass.services.has_service(DOMAIN, SERVICE_SET_MAX_POWER):
        async def _handle_set_max_power(call: ServiceCall) -> None:
            device_id = call.data["device_id"]
            max_power = call.data["max_power"]
            last_err: Exception | None = None
            for coord in hass.data.get(DOMAIN, {}).values():
                try:
                    await coord.api.async_set_max_power(device_id, max_power)
                    await coord.async_request_refresh()
                    return
                except JackeryError as err:
                    last_err = err
                    _LOGGER.debug(
                        "set_max_power via %s failed: %s",
                        coord.entry.entry_id, err,
                    )
                    continue
            raise RuntimeError(
                f"set_max_power failed for device {device_id}: {last_err}"
            )

        hass.services.async_register(
            DOMAIN, SERVICE_SET_MAX_POWER, _handle_set_max_power,
            schema=SET_MAX_POWER_SCHEMA,
        )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Remove services only when the last entry is gone
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_RENAME_SYSTEM)
            hass.services.async_remove(DOMAIN, SERVICE_SET_MAX_POWER)
    return unload_ok
