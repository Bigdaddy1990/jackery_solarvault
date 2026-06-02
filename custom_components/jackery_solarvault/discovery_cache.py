"""Persistent discovery cache for local offline startup."""

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
    Create a Store for this integration's discovery cache.
    
    The Store is configured with the module's storage key and storage version.
    
    Returns:
        Store[dict[str, Any]]: Store configured to persist this integration's discovery cache.
    """
    return Store(hass, _STORAGE_VERSION, _STORAGE_KEY)


async def async_load_discovery_cache(
    hass: HomeAssistant, entry_id: str
) -> dict[str, dict[str, Any]]:
    """
    Load the cached device index for a config entry from persistent storage.
    
    If the stored payload is missing or does not match the expected nested mapping structure, an empty dict is returned.
    
    Returns:
        Mapping of device ID strings to shallow copies of each device's metadata dict; empty dict if no valid cache exists.
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
    Persistently save the discovery device index for a config entry in the integration's Store.
    
    Overwrites any existing cache for the specified config entry. All device IDs are converted to strings and each device metadata dict is shallow-copied before storing.
    
    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        entry_id (str): Config entry identifier whose cache will be saved.
        device_index (dict[str, dict[str, Any]]): Mapping of device IDs to metadata; each metadata dict will be shallow-copied and saved with the device ID converted to a string.
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
