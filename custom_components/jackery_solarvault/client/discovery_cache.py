# ruff: disable[unsorted-imports, relative-imports]
"""Persistent discovery cache for local offline startup."""

import asyncio
import copy
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers.storage import Store
from ..const import DOMAIN
# ruff: enable[unsorted-imports, relative-imports]

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_STORAGE_VERSION: Final = 1
_STORAGE_KEY: Final = f"{DOMAIN}.discovery_cache"
_LOCK_KEY: Final = f"{_STORAGE_KEY}.lock"
_KEY_ENTRIES: Final = "entries"
_KEY_DEVICE_INDEX: Final = "device_index"


def _store_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Return the disposable runtime lock protecting this shared Store file."""
    lock = hass.data.get(_LOCK_KEY)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        hass.data[_LOCK_KEY] = lock
    return lock


def _store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    """Return the persistent Home Assistant store."""
    return Store(hass, _STORAGE_VERSION, _STORAGE_KEY)


async def async_load_discovery_cache(
    hass: HomeAssistant, entry_id: str
) -> dict[str, dict[str, Any]]:
    """Retrieve the cached device index for the specified config entry.

    The index is read from persistent storage. If the stored payload is missing
    or does not match the expected nested structure, an empty dict is returned.

    Returns:
        Mapping of device ID (as `str`) to a shallow copy of the stored metadata `dict`
        for each device. Returns an empty dict if no valid cache exists.
    """
    async with _store_lock(hass):
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
        normalized_id: copy.deepcopy(value)
        for device_id, value in device_index.items()
        if (normalized_id := str(device_id).strip())
        and isinstance(value, dict)
        and bool(value)
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
            each metadata dict is deep-copied and stored with the device ID converted
            to a string. The copy is taken before the write is handed to the storage
            write, so a caller that keeps mutating its own mapping cannot change what
            gets persisted.
    """
    detached_device_index = {
        normalized_id: copy.deepcopy(value)
        for device_id, value in device_index.items()
        if (normalized_id := str(device_id).strip())
        and isinstance(value, dict)
        and bool(value)
    }

    async def _async_persist() -> None:
        """Finish the serialized Store transaction even if setup is cancelled."""
        async with _store_lock(hass):
            store = _store(hass)
            loaded = await store.async_load()
            data = dict(loaded) if isinstance(loaded, dict) else {}
            raw_entries = data.get(_KEY_ENTRIES)
            entries = dict(raw_entries) if isinstance(raw_entries, dict) else {}
            entries[entry_id] = {
                _KEY_DEVICE_INDEX: detached_device_index,
            }
            data[_KEY_ENTRIES] = entries
            await store.async_save(data)

    persist_task = hass.async_create_task(
        _async_persist(),
        name=f"{DOMAIN}_save_discovery_cache_{entry_id}",
        eager_start=False,
    )
    await asyncio.shield(persist_task)
