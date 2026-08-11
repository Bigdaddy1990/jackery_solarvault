# ruff: disable[unsorted-imports, relative-imports]
"""Persistent MQTT session cache for Cloud-Outage tolerance.

The Jackery cloud login returns three fields that fully determine the MQTT
credentials a coordinator can use to (re-)connect to the broker:

* ``userId``       — drives the MQTT ``clientId`` and ``username``
* ``macId``        — identifies the session inside the broker
* ``mqttPassWord`` — 32-byte base64 seed used as AES-256-CBC key + IV

Once these are known and hydrated into ``JackeryApi``,
``JackeryApi.get_cached_mqtt_credentials`` can build a valid broker password
locally without any further HTTP call. Persisting them
allows the integration to start the MQTT push channel during a cloud outage
or right after a Home Assistant restart, before the first login round-trip
has succeeded.
"""

import asyncio
import base64
import binascii
import math
import time
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers.storage import Store
from ..const import (
    DOMAIN,
    MQTT_SESSION_MAC_ID,
    MQTT_SESSION_MAC_ID_SOURCE,
    MQTT_SESSION_SEED_B64,
    MQTT_SESSION_USER_ID,
)
# ruff: enable[unsorted-imports, relative-imports]

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_STORAGE_VERSION: Final = 1
_STORAGE_KEY: Final = f"{DOMAIN}.mqtt_session_cache"
_LOCK_KEY: Final = f"{_STORAGE_KEY}.lock"
_KEY_ENTRIES: Final = "entries"
_KEY_CACHED_AT: Final = "cached_at"
_KEY_EXPIRES_AT: Final = "expires_at"
_CACHE_CLOCK_SKEW_SEC: Final = 300.0
_MQTT_SEED_LEN: Final = 32


def normalize_mqtt_session_snapshot(
    raw: object,
    *,
    now: float | None = None,
) -> dict[str, str] | None:
    """Return a complete App-compatible MQTT session or reject it safely."""
    if not isinstance(raw, dict):
        return None
    current_time = time.time() if now is None else now
    cached_at = raw.get(_KEY_CACHED_AT)
    if cached_at is not None:
        if isinstance(cached_at, bool) or not isinstance(cached_at, int | float):
            return None
        if (
            not math.isfinite(cached_at)
            or cached_at < 0
            or cached_at > current_time + _CACHE_CLOCK_SKEW_SEC
        ):
            return None
    expires_at = raw.get(_KEY_EXPIRES_AT)
    if expires_at is not None:
        if isinstance(expires_at, bool) or not isinstance(expires_at, int | float):
            return None
        if not math.isfinite(expires_at) or expires_at <= current_time:
            return None
    user_id = raw.get(MQTT_SESSION_USER_ID)
    seed_b64 = raw.get(MQTT_SESSION_SEED_B64)
    mac_id = raw.get(MQTT_SESSION_MAC_ID)
    if not (isinstance(user_id, str) and user_id.strip()):
        return None
    if not (isinstance(seed_b64, str) and seed_b64.strip()):
        return None
    if not (isinstance(mac_id, str) and mac_id.strip()):
        return None
    try:
        seed = base64.b64decode(seed_b64, validate=True)
    except binascii.Error, ValueError:
        return None
    if len(seed) != _MQTT_SEED_LEN:
        return None
    result = {
        MQTT_SESSION_USER_ID: user_id,
        MQTT_SESSION_SEED_B64: seed_b64,
        MQTT_SESSION_MAC_ID: mac_id,
    }
    source = raw.get(MQTT_SESSION_MAC_ID_SOURCE)
    if isinstance(source, str) and source.strip():
        result[MQTT_SESSION_MAC_ID_SOURCE] = source
    return result


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


async def async_load_mqtt_session(
    hass: HomeAssistant, entry_id: str
) -> dict[str, str] | None:
    """Load cached MQTT session credentials for the given config entry.

    The session is read from persistent storage and validated before it is
    returned.

    Returns:
        dict[str, str]: Mapping with keys `MQTT_SESSION_USER_ID`,
        `MQTT_SESSION_SEED_B64`, and `MQTT_SESSION_MAC_ID`. Includes
        `MQTT_SESSION_MAC_ID_SOURCE` if present.
        None: If storage is missing or malformed, or any required field is missing or
        empty.
    """
    async with _store_lock(hass):
        data = await _store(hass).async_load()
    if not isinstance(data, dict):
        return None
    entries = data.get(_KEY_ENTRIES)
    if not isinstance(entries, dict):
        return None
    row = entries.get(entry_id)
    if not isinstance(row, dict):
        return None
    return normalize_mqtt_session_snapshot(row)


async def async_save_mqtt_session(
    hass: HomeAssistant,
    entry_id: str,
    *,
    user_id: str,
    seed_b64: str,
    mac_id: str,
    mac_id_source: str | None = None,
    cached_at: float | None = None,
    expires_at: float | None = None,
) -> None:
    """Persist MQTT session fields for a config entry.

    Stores the `userId`, base64 `mqttPassWord` seed, and `macId` for `entry_id` in the
    integration's Home Assistant storage, overwriting any existing row.

    Parameters:
        entry_id (str): The config entry identifier to associate the cached session
        with.
        user_id (str): `userId` returned by the Jackery cloud (used for MQTT
        clientId/username).
        seed_b64 (str): Base64-encoded `mqttPassWord` seed used to derive MQTT
        credentials.
        mac_id (str): `macId` broker session identifier.
        mac_id_source (str | None): Optional human-readable source or provenance of
        `mac_id`.
        cached_at (float | None): Optional UNIX timestamp (seconds) when the session was
            cached.
        expires_at (float | None): Optional App/server supplied UNIX expiry timestamp.
            No maximum cache age is invented when the App supplies no expiry.
    """
    effective_cached_at = time.time() if cached_at is None else cached_at
    if (
        isinstance(effective_cached_at, bool)
        or not isinstance(effective_cached_at, int | float)
        or not math.isfinite(effective_cached_at)
        or effective_cached_at < 0
        or effective_cached_at > time.time() + _CACHE_CLOCK_SKEW_SEC
    ):
        msg = "MQTT session cached_at must be a finite current UNIX timestamp"
        raise ValueError(msg)
    if expires_at is not None and (
        isinstance(expires_at, bool)
        or not isinstance(expires_at, int | float)
        or not math.isfinite(expires_at)
        or expires_at < 0
    ):
        msg = "MQTT session expires_at must be a finite UNIX timestamp"
        raise ValueError(msg)

    async def _async_persist() -> None:
        """Finish the serialized Store transaction even if setup is cancelled."""
        async with _store_lock(hass):
            store = _store(hass)
            loaded = await store.async_load()
            data = dict(loaded) if isinstance(loaded, dict) else {}
            raw_entries = data.get(_KEY_ENTRIES)
            entries = dict(raw_entries) if isinstance(raw_entries, dict) else {}
            row: dict[str, Any] = {
                MQTT_SESSION_USER_ID: user_id,
                MQTT_SESSION_SEED_B64: seed_b64,
                MQTT_SESSION_MAC_ID: mac_id,
            }
            if mac_id_source:
                row[MQTT_SESSION_MAC_ID_SOURCE] = mac_id_source
            row[_KEY_CACHED_AT] = effective_cached_at
            if expires_at is not None:
                row[_KEY_EXPIRES_AT] = expires_at
            entries[entry_id] = row
            data[_KEY_ENTRIES] = entries
            await store.async_save(data)

    persist_task = hass.async_create_task(
        _async_persist(),
        name=f"{DOMAIN}_save_mqtt_session_cache_{entry_id}",
        eager_start=False,
    )
    await asyncio.shield(persist_task)


async def async_clear_mqtt_session(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the cached MQTT session row for the given config entry.

    Performs no action if the storage layout or the entry's row does not exist.
    """
    async def _async_persist() -> None:
        """Finish the serialized Store transaction even if cleanup is cancelled."""
        async with _store_lock(hass):
            store = _store(hass)
            loaded = await store.async_load()
            if not isinstance(loaded, dict):
                return
            data = dict(loaded)
            raw_entries = data.get(_KEY_ENTRIES)
            if not isinstance(raw_entries, dict) or entry_id not in raw_entries:
                return
            entries = dict(raw_entries)
            entries.pop(entry_id, None)
            data[_KEY_ENTRIES] = entries
            await store.async_save(data)

    persist_task = hass.async_create_task(
        _async_persist(),
        name=f"{DOMAIN}_clear_mqtt_session_cache_{entry_id}",
        eager_start=False,
    )
    await asyncio.shield(persist_task)
