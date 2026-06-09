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
    Create a Store instance for this integration's persistent discovery cache.
    
    Returns:
        Store[dict[str, Any]]: A Store configured with the integration's storage key and storage schema version.
    """
    return Store(hass, _STORAGE_VERSION, _STORAGE_KEY)


async def async_load_discovery_cache(
    hass: HomeAssistant, entry_id: str
) -> dict[str, dict[str, Any]]:
    """Retrieve the cached device index for the specified config entry from persistent storage.

    If the stored payload is missing or does not match the expected nested structure, an empty dict is returned.

    Returns:
        Mapping of device ID (as `str`) to a shallow copy of the stored metadata `dict` for each device. Returns an empty dict if no valid cache exists.
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
    """Persist discovery metadata for a config entry to the integration's Store.

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
