"""Jackery SolarVault integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import JackeryApi, JackeryAuthError
from .const import DEFAULT_SCAN_INTERVAL_SEC, DOMAIN, PLATFORMS
from .coordinator import JackerySolarVaultCoordinator

_LOGGER = logging.getLogger(__name__)


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
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
