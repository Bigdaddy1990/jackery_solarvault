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
    """Return the HA Store used for cached discovery metadata."""
    return Store(hass, _STORAGE_VERSION, _STORAGE_KEY)


async def async_load_discovery_cache(
    hass: HomeAssistant, entry_id: str
) -> dict[str, dict[str, Any]]:
    """Load the cached device index for one config entry."""
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
    """Persist discovery metadata needed for local BLE startup."""
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
