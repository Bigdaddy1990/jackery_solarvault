"""Persistent MQTT session cache for Cloud-Outage tolerance.

The Jackery cloud login returns three fields that fully determine the MQTT
credentials a coordinator can use to (re-)connect to the broker:

* ``userId``       — drives the MQTT ``clientId`` and ``username``
* ``macId``        — identifies the session inside the broker
* ``mqttPassWord`` — 32-byte base64 seed used as AES-256-CBC key + IV

Once these are known, ``JackeryApi.async_get_mqtt_credentials`` can build a
valid broker password locally without any further HTTP call. Persisting them
allows the integration to start the MQTT push channel during a cloud outage
or right after a Home Assistant restart, before the first login round-trip
has succeeded.
"""

from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    MQTT_SESSION_MAC_ID,
    MQTT_SESSION_MAC_ID_SOURCE,
    MQTT_SESSION_SEED_B64,
    MQTT_SESSION_USER_ID,
)

_STORAGE_VERSION: Final = 1
_STORAGE_KEY: Final = f"{DOMAIN}.mqtt_session_cache"
_KEY_ENTRIES: Final = 'entries'
_KEY_CACHED_AT: Final = 'cached_at'


def _store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    """Get the Home Assistant Store used to persist the MQTT session cache.

    @returns Store[dict[str, Any]]: A configured Home Assistant `Store` instance using the module's storage key and version for MQTT session cache persistence.
    """
    return Store(hass, _STORAGE_VERSION, _STORAGE_KEY)


async def async_load_mqtt_session(
    hass: HomeAssistant, entry_id: str
) -> dict[str, str] | None:
    """Return cached MQTT session fields for ``entry_id`` or ``None``.

    The returned dict contains string-typed values for ``user_id``, ``seed_b64``
    and ``mac_id`` plus optional ``mac_id_source``. Returns ``None`` when any
    of the three mandatory fields is missing — partial cache rows are not
    useful because the AES password derivation needs all three.
    """
    data = await _store(hass).async_load()
    if not isinstance(data, dict):
        return None
    entries = data.get(_KEY_ENTRIES)
    if not isinstance(entries, dict):
        return None
    row = entries.get(entry_id)
    if not isinstance(row, dict):
        return None
    user_id = row.get(MQTT_SESSION_USER_ID)
    seed_b64 = row.get(MQTT_SESSION_SEED_B64)
    mac_id = row.get(MQTT_SESSION_MAC_ID)
    if not (isinstance(user_id, str) and user_id):
        return None
    if not (isinstance(seed_b64, str) and seed_b64):
        return None
    if not (isinstance(mac_id, str) and mac_id):
        return None
    source = row.get(MQTT_SESSION_MAC_ID_SOURCE)
    result: dict[str, str] = {
        MQTT_SESSION_USER_ID: user_id,
        MQTT_SESSION_SEED_B64: seed_b64,
        MQTT_SESSION_MAC_ID: mac_id,
    }
    if isinstance(source, str) and source:
        result[MQTT_SESSION_MAC_ID_SOURCE] = source
    return result


async def async_save_mqtt_session(
    hass: HomeAssistant,
    entry_id: str,
    *,
    user_id: str,
    seed_b64: str,
    mac_id: str,
    mac_id_source: str | None = None,
    cached_at: float | None = None,
) -> None:
    """Persist the MQTT session fields for ``entry_id``.

    A subsequent successful login that returns different credentials overwrites
    the row; this is the same shape the in-memory ``JackeryApi`` fields take.
    """
    store = _store(hass)
    data = await store.async_load()
    if not isinstance(data, dict):
        data = {}
    entries = data.get(_KEY_ENTRIES)
    if not isinstance(entries, dict):
        entries = {}
    row: dict[str, Any] = {
        MQTT_SESSION_USER_ID: user_id,
        MQTT_SESSION_SEED_B64: seed_b64,
        MQTT_SESSION_MAC_ID: mac_id,
    }
    if mac_id_source:
        row[MQTT_SESSION_MAC_ID_SOURCE] = mac_id_source
    if cached_at is not None:
        row[_KEY_CACHED_AT] = cached_at
    entries[entry_id] = row
    data[_KEY_ENTRIES] = entries
    await store.async_save(data)


async def async_clear_mqtt_session(hass: HomeAssistant, entry_id: str) -> None:
    """Drop the cached MQTT session for ``entry_id``.

    Called after the broker explicitly rejects cached credentials so the next
    setup pass forces a fresh login instead of replaying stale values.
    """
    store = _store(hass)
    data = await store.async_load()
    if not isinstance(data, dict):
        return
    entries = data.get(_KEY_ENTRIES)
    if not isinstance(entries, dict):
        return
    if entry_id not in entries:
        return
    entries.pop(entry_id, None)
    data[_KEY_ENTRIES] = entries
    await store.async_save(data)
