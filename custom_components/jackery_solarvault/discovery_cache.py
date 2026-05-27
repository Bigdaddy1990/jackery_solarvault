"""Persistent discovery cache for local offline startup."""

from __future__ import annotations

from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_STORAGE_VERSION: Final = 1
_STORAGE_KEY: Final = f"{DOMAIN}.discovery_cache"
_KEY_ENTRIES: Final = "entries"
_KEY_DEVICE_INDEX: Final = "device_index"


def _store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    """
    Get the Home Assistant Store configured for persistent discovery metadata.
    
    Returns:
        Store[dict[str, Any]]: A Store instance used to persist the discovery cache.
    """
    return Store(hass, _STORAGE_VERSION, _STORAGE_KEY)


async def async_load_discovery_cache(
    hass: HomeAssistant, entry_id: str
) -> dict[str, dict[str, Any]]:
    """
    Retrieve the cached device-index for the given config entry from persistent storage.
    
    Returns:
        dict[str, dict[str, Any]]: Mapping from device ID (as `str`) to a shallow copy of the stored metadata dict for each device. Returns an empty dict if no valid cache exists or stored data is malformed.
    """
    data = await _store(hass).async_load()
    if not isinstance(data, dict):
        return {}
    entries = data.get(_KEY_ENTRIES)
    if not isinstance(entries, dict):
        return {}
    entry_data = entries.get(entry_id)
    if not isinstance(entry_data, dict):
        return {}
    device_index = entry_data.get(_KEY_DEVICE_INDEX)
    if not isinstance(device_index, dict):
        return {}
    return {
        str(device_id): dict(value)
        for device_id, value in device_index.items()
        if isinstance(value, dict)
    }


async def async_save_discovery_cache(
    hass: HomeAssistant,
    entry_id: str,
    device_index: dict[str, dict[str, Any]],
) -> None:
    """
    Persist discovery metadata for a config entry to the integration's Store.
    
    This overwrites any existing cache for the given config entry and normalizes
    device IDs to strings while copying each device's metadata.
    
    Parameters:
        entry_id (str): Config entry identifier whose cache to save.
        device_index (dict[str, dict[str, Any]]): Mapping of device IDs to metadata;
            each metadata dict will be shallow-copied and stored with the device ID
            converted to a string.
    """
    store = _store(hass)
    data = await store.async_load()
    if not isinstance(data, dict):
        data = {}
    entries = data.get(_KEY_ENTRIES)
    if not isinstance(entries, dict):
        entries = {}
    entries[entry_id] = {
        _KEY_DEVICE_INDEX: {
            str(device_id): dict(value) for device_id, value in device_index.items()
        }
    }
    data[_KEY_ENTRIES] = entries
    await store.async_save(data)
