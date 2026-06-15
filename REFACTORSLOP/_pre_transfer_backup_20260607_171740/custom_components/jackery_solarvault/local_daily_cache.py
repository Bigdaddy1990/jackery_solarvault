"""Per-device midnight snapshots of lifetime energy counters.

The SolarVault firmware exposes monotonic Wh counters in every BLE/MQTT
property frame: ``pvEgy``, ``batChgEgy``, ``batDisChgEgy``, ``inOngridEgy``,
``outOngridEgy``, ``batOtGridEgy``, ``pvOtBatEgy``, ``pvOtOngridEgy``, plus
the per-MPPT ``pv1Egy``..``pv4Egy`` and the per-battery-pack ``inEgy`` /
``outEgy``. These are reliable even when the Jackery cloud is offline,
because they ride the same local payload that already mergedinto
``coordinator.data[device_id][PAYLOAD_PROPERTIES]``.

This module snapshots each counter at 00:00 local time and exposes
``daily_delta(device_id, metric_key, current_lifetime_wh)`` so the Tages-
sensors can show ``today's energy`` without depending on the cloud's
``/v1/device/stat/*?dateType=day`` endpoint. The HA Recorder still receives
the same ``state_class=total_increasing`` lifetime value through the
existing sensor implementations; the daily delta is an *additional* view
for the Energy-Dashboard "today" sensors that the cloud usually fills.

Persistence is mandatory: a HA restart in the middle of the day must not
reset the midnight anchor. The cache key is ``DOMAIN.local_daily_cache``
and is stored under HA's standard :class:`Store`.
"""

import asyncio
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers.storage import Store

from .const import DOMAIN

if TYPE_CHECKING:
    from datetime import date

    from homeassistant.core import HomeAssistant

_STORAGE_VERSION: Final = 1
_STORAGE_KEY: Final = f"{DOMAIN}.local_daily_cache"
_KEY_ENTRIES: Final = "entries"
_KEY_DAY: Final = "day"
_KEY_VALUES: Final = "values"
_ENTRY_LOCKS: dict[str, asyncio.Lock] = {}


def _entry_lock(entry_id: str) -> asyncio.Lock:
    """Return the in-process lock for one config-entry cache row."""
    return _ENTRY_LOCKS.setdefault(entry_id, asyncio.Lock())


def _store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    """Return the HA Store backing the local-daily cache."""
    return Store(hass, _STORAGE_VERSION, _STORAGE_KEY)


def _isoformat_day(today: date) -> str:
    """Return ISO ``YYYY-MM-DD`` for the supplied local date."""
    return today.isoformat()


async def async_load_daily_cache(
    hass: HomeAssistant, entry_id: str
) -> dict[str, dict[str, Any]]:
    """Return cached midnight snapshots for ``entry_id``.

    Shape: ``{device_id: {"day": "YYYY-MM-DD", "values": {metric: wh}}}``.
    Returns an empty dict when the store is empty or unparseable. The caller
    must compare ``day`` against today before trusting the values.
    """
    data = await _store(hass).async_load()
    if not isinstance(data, dict):
        return {}
    entries = data.get(_KEY_ENTRIES)
    if not isinstance(entries, dict):
        return {}
    row = entries.get(entry_id)
    if not isinstance(row, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for device_id, payload in row.items():
        if not isinstance(payload, dict):
            continue
        day = payload.get(_KEY_DAY)
        values = payload.get(_KEY_VALUES)
        if not isinstance(day, str) or not isinstance(values, dict):
            continue
        clean_values: dict[str, int] = {}
        for metric, value in values.items():
            if not isinstance(metric, str):
                continue
            try:
                clean_values[metric] = int(value)
            except (TypeError, ValueError):
                continue
        result[str(device_id)] = {
            _KEY_DAY: day,
            _KEY_VALUES: clean_values,
        }
    return result


async def async_save_daily_cache(
    hass: HomeAssistant,
    entry_id: str,
    *,
    snapshots: dict[str, dict[str, Any]],
) -> None:
    """Persist midnight snapshots for ``entry_id``.

    ``snapshots`` mirrors the shape returned by :func:`async_load_daily_cache`.
    """
    async with _entry_lock(entry_id):
        store = _store(hass)
        data = await store.async_load()
        if not isinstance(data, dict):
            data = {}
        entries = data.get(_KEY_ENTRIES)
        if not isinstance(entries, dict):
            entries = {}
        cleaned: dict[str, dict[str, Any]] = {}
        for device_id, payload in snapshots.items():
            day = payload.get(_KEY_DAY)
            values = payload.get(_KEY_VALUES)
            if not isinstance(day, str) or not isinstance(values, dict):
                continue
            clean_values: dict[str, int] = {}
            for metric, value in values.items():
                if not isinstance(metric, str):
                    continue
                try:
                    clean_values[metric] = int(value)
                except (TypeError, ValueError):
                    continue
            cleaned[str(device_id)] = {
                _KEY_DAY: day,
                _KEY_VALUES: clean_values,
            }
        entries[entry_id] = cleaned
        data[_KEY_ENTRIES] = entries
        await store.async_save(data)


def daily_delta(  # noqa: PLR0911
    snapshot: dict[str, Any] | None,
    metric_key: str,
    current_lifetime_wh: float | None,
    *,
    today: date,
) -> int | None:
    """Return the today-delta for ``metric_key`` in Wh, or None.

    ``snapshot`` is the per-device entry from :func:`async_load_daily_cache`.
    Returns ``None`` when the snapshot is missing, refers to a different day,
    has no value for ``metric_key`` or the current value is below the
    midnight anchor (firmware counter reset / overflow). Callers must treat
    ``None`` as ``unknown`` and fall back to the existing cloud value.
    """
    if current_lifetime_wh is None:
        return None
    try:
        current = int(current_lifetime_wh)
    except (TypeError, ValueError):
        return None
    if not isinstance(snapshot, dict):
        return None
    day = snapshot.get(_KEY_DAY)
    if day != _isoformat_day(today):
        return None
    values = snapshot.get(_KEY_VALUES)
    if not isinstance(values, dict):
        return None
    anchor = values.get(metric_key)
    if anchor is None:
        return None
    try:
        anchor_int = int(anchor)
    except (TypeError, ValueError):
        return None
    if current < anchor_int:
        diff = anchor_int - current
        if diff > 2**31:
            return current + 2**32 - anchor_int
        return None
    return current - anchor_int


def refresh_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    today: date,
    current_values: dict[str, int | float | None],
) -> dict[str, Any]:
    """Return an updated midnight snapshot for the supplied device.

    Behaviour:

    * If ``snapshot`` is missing or its day differs from ``today``: anchor
      every available metric to ``current_values`` (start of a new day).
    * Otherwise: keep the existing anchor values, only add metrics whose
      anchor is still unset (firmware just started reporting a new counter
      mid-day).

    Counters that resolve to ``None`` / non-numeric inputs are skipped so a
    transient missing field cannot clobber an existing anchor.
    """
    today_iso = _isoformat_day(today)
    if not isinstance(snapshot, dict) or snapshot.get(_KEY_DAY) != today_iso:
        clean_values: dict[str, int] = {}
        for metric, value in current_values.items():
            if value is None:
                continue
            try:
                clean_values[metric] = int(value)
            except (TypeError, ValueError):
                continue
        return {_KEY_DAY: today_iso, _KEY_VALUES: clean_values}
    existing_values = snapshot.get(_KEY_VALUES)
    if not isinstance(existing_values, dict):
        existing_values = {}
    merged: dict[str, int] = {}
    for metric, value in existing_values.items():
        if not isinstance(metric, str):
            continue
        try:
            merged[metric] = int(value)
        except (TypeError, ValueError):
            continue
    for metric, value in current_values.items():
        if metric in merged:
            continue
        if value is None:
            continue
        try:
            merged[metric] = int(value)
        except (TypeError, ValueError):
            continue
    return {_KEY_DAY: today_iso, _KEY_VALUES: merged}


def is_new_day(snapshot: dict[str, Any] | None, today: date) -> bool:
    """Return True when ``snapshot`` belongs to a different day than ``today``."""
    if not isinstance(snapshot, dict):
        return True
    return snapshot.get(_KEY_DAY) != _isoformat_day(today)


def snapshot_day(snapshot: dict[str, Any] | None) -> str | None:
    """Return the ISO day stored in ``snapshot`` or None.

    Used by diagnostics so the user can see when the midnight anchor was
    last rotated without needing the raw Store JSON.
    """
    if not isinstance(snapshot, dict):
        return None
    day = snapshot.get(_KEY_DAY)
    return day if isinstance(day, str) else None


__all__ = [
    "async_load_daily_cache",
    "async_save_daily_cache",
    "daily_delta",
    "is_new_day",
    "refresh_snapshot",
    "snapshot_day",
]
