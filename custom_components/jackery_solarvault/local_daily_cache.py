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

from __future__ import annotations

from datetime import date
from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_STORAGE_VERSION: Final = 1
_STORAGE_KEY: Final = f"{DOMAIN}.local_daily_cache"
_KEY_ENTRIES: Final = "entries"
_KEY_DAY: Final = "day"
_KEY_VALUES: Final = "values"


def _store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    """Return the HA Store backing the local-daily cache."""
    return Store(hass, _STORAGE_VERSION, _STORAGE_KEY)


def _isoformat_day(today: date) -> str:
    """
    Format a date as an ISO day string (YYYY-MM-DD).
    
    Returns:
        iso_day (str): The date formatted as `YYYY-MM-DD`.
    """
    return today.isoformat()


async def async_load_daily_cache(
    hass: HomeAssistant, entry_id: str
) -> dict[str, dict[str, Any]]:
    """
    Load cached midnight snapshots for the given config entry.
    
    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        entry_id (str): Config entry identifier whose snapshots to load.
    
    Returns:
        dict[str, dict[str, Any]]: Mapping of device_id to snapshot objects with shape
        {"day": "YYYY-MM-DD", "values": {metric: wh}}. Returns an empty dict if the
        store is missing or contains unparseable data. The returned snapshots may
        belong to a different day; callers should compare the snapshot "day" to the
        current date before using the stored values.
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
            except TypeError, ValueError:
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
    """
    Persist per-device midnight snapshot data for a configuration entry.
    
    Cleans and writes `snapshots` into the module's persistent store for `entry_id`. The function accepts a mapping of device IDs to payloads of the form `{"day": "YYYY-MM-DD", "values": {metric: number}}`; non-dict payloads, non-string days, non-dict values, non-string metric keys, and values that cannot be converted to `int` are omitted. Existing store data for other entries is preserved; invalid fields in the provided snapshots are dropped rather than raising errors.
    
    Parameters:
        hass: HomeAssistant instance (provided by the caller).
        entry_id: Configuration entry identifier whose snapshots will be stored.
        snapshots: Mapping from device ID to snapshot payloads. Each payload should contain:
            - "day": ISO date string ("YYYY-MM-DD").
            - "values": mapping of metric keys (str) to numeric values (int|float|None).
    """
    store = _store(hass)
    data = await store.async_load()
    if not isinstance(data, dict):
        data = {}
    entries = data.get(_KEY_ENTRIES)
    if not isinstance(entries, dict):
        entries = {}
    cleaned: dict[str, dict[str, Any]] = {}
    for device_id, payload in snapshots.items():
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
            except TypeError, ValueError:
                continue
        cleaned[str(device_id)] = {
            _KEY_DAY: day,
            _KEY_VALUES: clean_values,
        }
    entries[entry_id] = cleaned
    data[_KEY_ENTRIES] = entries
    await store.async_save(data)


def daily_delta(
    snapshot: dict[str, Any] | None,
    metric_key: str,
    current_lifetime_wh: int | float | None,
    *,
    today: date,
) -> int | None:
    """
    Compute today's energy delta in watt-hours for a metric using the stored midnight anchor.
    
    Returns:
        int: The difference between the current lifetime counter and the stored midnight anchor in Wh.
        None: When the snapshot is missing or not a dict, the snapshot's day does not match `today`, the metric is not present, numeric parsing fails, `current_lifetime_wh` is `None`, or the current value is less than the stored anchor (counter reset/overflow).
    """
    if current_lifetime_wh is None:
        return None
    try:
        current = int(current_lifetime_wh)
    except TypeError, ValueError:
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
    except TypeError, ValueError:
        return None
    if current < anchor_int:
        return None
    return current - anchor_int


def refresh_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    today: date,
    current_values: dict[str, int | float | None],
) -> dict[str, Any]:
    """
    Produce an updated midnight snapshot for a device given today's date and current lifetime metric readings.
    
    If `snapshot` is missing or its stored day differs from `today`, the function anchors every available metric from `current_values`. If the snapshot is for the same day, it preserves existing anchors and only adds metrics that do not already have an anchor. Inputs that are `None` or cannot be converted to an integer are skipped and do not overwrite existing anchors.
    
    Parameters:
        snapshot (dict[str, Any] | None): Existing per-device snapshot (may be `None`).
        today (date): Current date used to determine the snapshot day.
        current_values (dict[str, int | float | None]): Current lifetime metric readings; values of `None` or non-numeric values are ignored.
    
    Returns:
        dict[str, Any]: A snapshot dictionary with keys `"day"` (ISO `YYYY-MM-DD`) and `"values"` (mapping metric keys to integer Wh anchors).
    """
    today_iso = _isoformat_day(today)
    if not isinstance(snapshot, dict) or snapshot.get(_KEY_DAY) != today_iso:
        clean_values: dict[str, int] = {}
        for metric, value in current_values.items():
            if value is None:
                continue
            try:
                clean_values[metric] = int(value)
            except TypeError, ValueError:
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
        except TypeError, ValueError:
            continue
    for metric, value in current_values.items():
        if metric in merged:
            continue
        if value is None:
            continue
        try:
            merged[metric] = int(value)
        except TypeError, ValueError:
            continue
    return {_KEY_DAY: today_iso, _KEY_VALUES: merged}


def is_new_day(snapshot: dict[str, Any] | None, today: date) -> bool:
    """
    Determine whether a snapshot is for a different day than the given `today`.
    
    Parameters:
        snapshot (dict[str, Any] | None): Persisted per-device snapshot that may contain an ISO day under the `"day"` key.
        today (date): The current date to compare against the snapshot's stored day.
    
    Returns:
        `True` if `snapshot` is not a dict or its stored day is not equal to `today`'s ISO date, `False` otherwise.
    """
    if not isinstance(snapshot, dict):
        return True
    return snapshot.get(_KEY_DAY) != _isoformat_day(today)


def snapshot_day(snapshot: dict[str, Any] | None) -> str | None:
    """
    Return the stored ISO day string (YYYY-MM-DD) from a snapshot, or `None` if absent.
    
    Used by diagnostics to show when the midnight anchor was last rotated without exposing raw store data.
    
    Parameters:
        snapshot (dict[str, Any] | None): Per-device snapshot previously produced by the caching layer.
    
    Returns:
        str | None: The ISO day string if present and a string, otherwise `None`.
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
