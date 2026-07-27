"""Persistent discovery cache for local offline startup."""

import asyncio
import copy
import logging
from typing import TYPE_CHECKING, Any, Final, cast

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)  # noqa: RUF067
_STORAGE_VERSION: Final = 1
_STORAGE_KEY: Final = f"{DOMAIN}.discovery_cache"
_RUNTIME_KEY: Final = f"{_STORAGE_KEY}.runtime"
_KEY_ENTRIES: Final = "entries"
_KEY_DEVICE_INDEX: Final = "device_index"

type _StoreRuntime = tuple[Store[dict[str, Any]], asyncio.Lock]


def _runtime(hass: HomeAssistant) -> _StoreRuntime:
    """Return the shared store and transaction lock for this HA runtime."""
    runtime = hass.data.get(_RUNTIME_KEY)
    if runtime is None:
        runtime = (Store(hass, _STORAGE_VERSION, _STORAGE_KEY), asyncio.Lock())
        hass.data[_RUNTIME_KEY] = runtime
    return cast("_StoreRuntime", runtime)


async def async_load_discovery_cache(
    hass: HomeAssistant, entry_id: str
) -> dict[str, dict[str, Any]]:
    """Retrieve the cached device index for the specified config entry from persistent
    storage.

    If the stored payload is missing or does not match the expected nested structure, an
    empty dict is returned.

    Returns:
        Mapping of device ID (as `str`) to a shallow copy of the stored metadata `dict`
        for each device. Returns an empty dict if no valid cache exists.
    """
    store, transaction_lock = _runtime(hass)
    async with transaction_lock:
        data = await store.async_load()
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
        str(device_id): copy.deepcopy(value)
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
            each metadata dict is deep-copied and stored with the device ID converted
            to a string. The copy is taken before the write is handed to the storage
            task, so a caller that keeps mutating its own mapping cannot change what
            gets persisted.
    """
    detached_device_index = {
        str(device_id): copy.deepcopy(value)
        for device_id, value in device_index.items()
    }
    store, transaction_lock = _runtime(hass)

    async def _async_save_transaction() -> None:
        """Merge this entry's detached device index under the shared lock."""
        async with transaction_lock:
            loaded = await store.async_load()
            data = dict(loaded) if isinstance(loaded, dict) else {}
            raw_entries = data.get(_KEY_ENTRIES)
            entries = dict(raw_entries) if isinstance(raw_entries, dict) else {}
            entries[entry_id] = {
                _KEY_DEVICE_INDEX: detached_device_index,
            }
            data[_KEY_ENTRIES] = entries
            await store.async_save(data)

    task = hass.async_create_task(
        _async_save_transaction(),
        name=f"{DOMAIN}_discovery_cache_save_{entry_id}",
        eager_start=False,
    )
    await asyncio.shield(task)
