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
from datetime import date
import json
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers.storage import Store

from ..const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

_STORAGE_VERSION: Final = 1
_STORAGE_KEY: Final = f"{DOMAIN}.local_daily_cache"
_LOCK_KEY: Final = f"{_STORAGE_KEY}.lock"
_KEY_ENTRIES: Final = "entries"
_KEY_DAY: Final = "day"
_KEY_VALUES: Final = "values"


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


def _isoformat_day(today: date) -> str:
    """Get the ISO-formatted day string for the specified date.

    Returns:
        str: ISO date in `YYYY-MM-DD` format.
    """
    return today.isoformat()


async def async_load_daily_cache(  # ruff: ignore[too-many-branches]
    hass: HomeAssistant, entry_id: str
) -> dict[str, dict[str, Any]]:
    """Load cached midnight snapshots for a config entry.

    Returns a mapping keyed by device_id where each value is a snapshot object
    with the shape ``{"day": "YYYY-MM-DD", "values": {metric: wh}}``.
    Malformed store entries are ignored; an empty dict is returned when the
    store is missing or contains no valid snapshots. Callers should compare
    each snapshot's day to the current date before using its values.

    Parameters:
        entry_id (str): Config entry identifier whose snapshots to load.

    Returns:
        dict[str, dict[str, Any]]: Device-id -> snapshot mapping containing validated
        and normalized snapshot data.
    """
    async with _store_lock(hass):
        data = await _store(hass).async_load()
    if not isinstance(data, dict):
        return {}
    entries = data.get(_KEY_ENTRIES)
    if not isinstance(entries, dict):
        return {}
    # A reauth/reconfigure cycle can replace the config-entry id while the
    # physical device and its monotonic lifetime counters stay the same.  Read
    # the requested row first, then recover same-device anchors from older
    # rows.  For one day/metric the lowest counter is the earliest observation
    # and therefore the best available midnight-side anchor.
    requested_row = entries.get(entry_id)
    rows = [requested_row] if isinstance(requested_row, dict) else []
    rows.extend(
        row
        for candidate_entry_id, row in entries.items()
        if candidate_entry_id != entry_id and isinstance(row, dict)
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        for device_id, payload in row.items():
            if not isinstance(payload, dict):
                continue
            day = payload.get(_KEY_DAY)
            values = payload.get(_KEY_VALUES)
            if not isinstance(day, str) or not isinstance(values, dict):
                continue
            try:
                parsed_day = date.fromisoformat(day)
                if parsed_day.isoformat() != day:
                    continue
            except ValueError:
                continue
            clean_values: dict[str, int] = {}
            for metric, value in values.items():
                if not isinstance(metric, str):
                    continue
                try:
                    normalized = int(value)
                except TypeError, ValueError:
                    continue
                if normalized < 0:
                    continue
                clean_values[metric] = normalized
            if not clean_values:
                continue
            normalized_device_id = str(device_id)
            existing = result.get(normalized_device_id)
            if not isinstance(existing, dict):
                result[normalized_device_id] = {
                    _KEY_DAY: day,
                    _KEY_VALUES: clean_values,
                }
                continue
            existing_day = existing.get(_KEY_DAY)
            if not isinstance(existing_day, str) or day > existing_day:
                result[normalized_device_id] = {
                    _KEY_DAY: day,
                    _KEY_VALUES: clean_values,
                }
                continue
            if day != existing_day:
                continue
            existing_values = existing.get(_KEY_VALUES)
            if not isinstance(existing_values, dict):
                existing_values = {}
            result[normalized_device_id] = {
                _KEY_DAY: day,
                _KEY_VALUES: {
                    metric: min(clean_values.get(metric, value), value)
                    for metric, value in existing_values.items()
                }
                | {
                    metric: value
                    for metric, value in clean_values.items()
                    if metric not in existing_values
                },
            }
    return result


async def async_save_daily_cache(
    hass: HomeAssistant,
    entry_id: str,
    *,
    snapshots: dict[str, dict[str, Any] | Any],
) -> None:
    """Persist per-device midnight snapshot data for a configuration entry.

    Cleans and writes ``snapshots`` into the module's persistent store for
    ``entry_id``. The function accepts a mapping of device IDs to payloads of
    the form ``{"day": "YYYY-MM-DD", "values": {metric: number}}``. Invalid
    payloads, dates, keys, and values are omitted. Existing data for other
    entries is preserved.

    Parameters:
        hass: HomeAssistant instance (provided by the caller).
        entry_id: Configuration entry identifier whose snapshots will be stored.
        snapshots: Mapping from device ID to snapshot payloads. Each payload should
        contain:
            - "day": ISO date string ("YYYY-MM-DD").
            - "values": mapping of metric keys (str) to numeric values (int|float|None).
    """
    cleaned: dict[str, dict[str, Any]] = {}
    for device_id, payload in snapshots.items():
        if not isinstance(payload, dict):
            continue
        day = payload.get(_KEY_DAY)
        values = payload.get(_KEY_VALUES)
        if not isinstance(day, str) or not isinstance(values, dict):
            continue
        try:
            if date.fromisoformat(day).isoformat() != day:
                continue
        except ValueError:
            continue
        clean_values: dict[str, int] = {}
        for metric, value in values.items():
            if not isinstance(metric, str):
                continue
            try:
                normalized = int(value)
            except TypeError, ValueError:
                continue
            if normalized < 0:
                continue
            clean_values[metric] = normalized
        if not clean_values:
            continue
        cleaned[str(device_id)] = {
            _KEY_DAY: day,
            _KEY_VALUES: clean_values,
        }

    async with _store_lock(hass):
        store = _store(hass)
        loaded = await store.async_load()
        data = dict(loaded) if isinstance(loaded, dict) else {}
        raw_entries = data.get(_KEY_ENTRIES)
        entries = dict(raw_entries) if isinstance(raw_entries, dict) else {}
        entries[entry_id] = cleaned
        data[_KEY_ENTRIES] = entries
        await store.async_save(data)


def daily_delta(  # ruff: ignore[too-many-return-statements]
    snapshot: dict[str, Any] | None,
    metric_key: str,
    current_lifetime_wh: float | None,
    *,
    today: date,
) -> int | None:
    """Compute today's energy delta for a metric using a stored midnight anchor.

    Parameters:
        snapshot (dict | None): Stored snapshot with an ISO ``day`` and a
            ``values`` mapping containing anchored Wh values.
        metric_key (str): Metric key to read from `snapshot["values"]`.
        current_lifetime_wh (int | float | None): Current lifetime energy counter for
        the metric; if `None` the delta is disabled.
        today (date): Local date used to validate the snapshot day.

    Returns:
        int | None: The computed delta in watt-hours when the snapshot and
        current lifetime counter are valid; otherwise ``None``.
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
    """Produce today's snapshot with integer anchors for lifetime metrics.

    If the snapshot is missing or belongs to another day, every usable current
    metric becomes a new anchor. For an existing same-day snapshot, existing
    anchors are preserved and newly available metrics are added.

    Parameters:
        snapshot (dict[str, Any] | None): Existing per-device snapshot; may be `None`.
        today (date): Current date used as the snapshot day.
        current_values (dict[str, int | float | None]): Current lifetime metric
        readings; `None` or non-numeric values are ignored.

    Returns:
        dict[str, Any]: Snapshot with an ISO ``day`` and a ``values`` mapping
        containing integer Wh anchors.
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
    """Determine whether the snapshot represents a different day.

    Returns:
        ``True`` when the snapshot is missing or its stored day differs from
        ``today``; otherwise ``False``.
    """
    if not isinstance(snapshot, dict):
        return True
    return snapshot.get(_KEY_DAY) != _isoformat_day(today)


def snapshot_day(snapshot: dict[str, Any] | None) -> str | None:
    """Return the stored ISO day string from a snapshot.

    Parameters:
        snapshot (dict | None): Snapshot object expected to contain a string value under
        the key `_KEY_DAY`.

    Returns:
        str | None: The ISO day string (`YYYY-MM-DD`) if present and a string, otherwise
        `None`.
    """
    if not isinstance(snapshot, dict):
        return None
    day = snapshot.get(_KEY_DAY)
    return day if isinstance(day, str) else None


def local_daily_signature(snapshots: Mapping[str, Any]) -> str:
    """Produce a stable JSON signature for a snapshots mapping.

    Parameters:
        snapshots (Mapping[str, Any]): Mapping of device IDs to per-device snapshot
        objects; used to detect content changes.

    Returns:
        signature (str): Deterministic JSON string representation of `snapshots` (stable
        key ordering) suitable for change detection.
    """
    return json.dumps(snapshots, sort_keys=True, default=str)


__all__ = [
    "async_load_daily_cache",
    "async_save_daily_cache",
    "daily_delta",
    "is_new_day",
    "local_daily_signature",
    "refresh_snapshot",
    "snapshot_day",
]
