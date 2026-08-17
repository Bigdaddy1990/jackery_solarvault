"""Per-device midnight snapshots of lifetime energy counters.

The SolarVault firmware exposes monotonic 0.01 kWh counters in every BLE/MQTT
property frame: ``pvEgy``, ``batChgEgy``, ``batDisChgEgy``, ``inOngridEgy``,
``outOngridEgy``, ``batOtGridEgy``, ``pvOtBatEgy``, ``pvOtOngridEgy``, plus
the per-MPPT ``pv1Egy``..``pv4Egy``. Third-party CT counters in this cache use
Wh. These counters are reliable even when the Jackery cloud is offline,
because they ride the same local payload that already mergedinto
``coordinator.data[device_id][PAYLOAD_PROPERTIES]``.

This module snapshots each counter at 00:00 local time and exposes
``daily_delta(device_id, metric_key, current_lifetime_value)`` so the Tages-
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
from datetime import date, timedelta
import json
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from ..const import (
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DOMAIN,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_STORAGE_VERSION: Final = 1
_STORAGE_KEY: Final = f"{DOMAIN}.local_daily_cache"
_LOCK_KEY: Final = f"{_STORAGE_KEY}.lock"
_KEY_ENTRIES: Final = "entries"
_KEY_DAY: Final = "day"
_KEY_VALUES: Final = "values"
_KEY_COMPLETED_DAYS: Final = "completed_days"
_KEY_LAST_DELTAS: Final = "last_deltas"
_MAX_COMPLETED_DAY_HISTORY: Final = 400


def _clean_metric_values(values: object) -> dict[str, int]:
    """Return non-negative integer metric values from an arbitrary object."""
    if not isinstance(values, dict):
        return {}
    cleaned: dict[str, int] = {}
    for metric, value in values.items():
        if not isinstance(metric, str):
            continue
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            continue
        if normalized >= 0:
            cleaned[metric] = normalized
    return cleaned


def _clean_completed_days(value: object) -> dict[str, dict[str, int]]:
    """Return validated ISO-day history rows in native counter units."""
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, dict[str, int]] = {}
    for day, metrics in value.items():
        if not isinstance(day, str):
            continue
        try:
            if date.fromisoformat(day).isoformat() != day:
                continue
        except ValueError:
            continue
        clean_metrics = _clean_metric_values(metrics)
        if clean_metrics:
            cleaned[day] = clean_metrics
    return cleaned


def _merge_completed_days(
    first: dict[str, dict[str, int]],
    second: dict[str, dict[str, int]],
) -> dict[str, dict[str, int]]:
    """Merge reauth cache rows, retaining the largest observed daily delta."""
    merged = {day: dict(metrics) for day, metrics in first.items()}
    for day, metrics in second.items():
        target = merged.setdefault(day, {})
        for metric, value in metrics.items():
            target[metric] = max(target.get(metric, value), value)
    return merged


def _normalize_snapshot(payload: object) -> dict[str, Any] | None:
    """Return one validated cache snapshot, or ``None`` when malformed."""
    if not isinstance(payload, dict):
        return None
    day = payload.get(_KEY_DAY)
    values = payload.get(_KEY_VALUES)
    if not isinstance(day, str) or not isinstance(values, dict):
        return None
    try:
        if date.fromisoformat(day).isoformat() != day:
            return None
    except ValueError:
        return None
    clean_values = _clean_metric_values(values)
    if not clean_values:
        return None
    normalized: dict[str, Any] = {
        _KEY_DAY: day,
        _KEY_VALUES: clean_values,
    }
    completed_days = _clean_completed_days(payload.get(_KEY_COMPLETED_DAYS))
    if completed_days:
        normalized[_KEY_COMPLETED_DAYS] = completed_days
    last_deltas = _clean_metric_values(payload.get(_KEY_LAST_DELTAS))
    if last_deltas:
        normalized[_KEY_LAST_DELTAS] = last_deltas
    return normalized


def _merge_snapshots(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merge reauth rows without losing anchors or completed-day history."""
    if existing is None:
        return incoming
    merged_history = _merge_completed_days(
        _clean_completed_days(existing.get(_KEY_COMPLETED_DAYS)),
        _clean_completed_days(incoming.get(_KEY_COMPLETED_DAYS)),
    )
    existing_day = existing.get(_KEY_DAY)
    incoming_day = incoming[_KEY_DAY]
    if isinstance(existing_day, str) and existing_day != incoming_day:
        older = existing if existing_day < incoming_day else incoming
        older_day = older.get(_KEY_DAY)
        older_last_deltas = _clean_metric_values(older.get(_KEY_LAST_DELTAS))
        if isinstance(older_day, str) and older_last_deltas:
            merged_history = _merge_completed_days(
                merged_history,
                {older_day: older_last_deltas},
            )
    if not isinstance(existing_day, str) or incoming_day > existing_day:
        merged = dict(incoming)
    elif incoming_day < existing_day:
        merged = dict(existing)
    else:
        existing_values = _clean_metric_values(existing.get(_KEY_VALUES))
        incoming_values = _clean_metric_values(incoming.get(_KEY_VALUES))
        merged = {
            _KEY_DAY: incoming_day,
            _KEY_VALUES: {
                metric: min(incoming_values.get(metric, value), value)
                for metric, value in existing_values.items()
            }
            | {
                metric: value
                for metric, value in incoming_values.items()
                if metric not in existing_values
            },
        }
        merged_last_deltas = _clean_metric_values(existing.get(_KEY_LAST_DELTAS))
        for metric, value in _clean_metric_values(
            incoming.get(_KEY_LAST_DELTAS)
        ).items():
            merged_last_deltas[metric] = max(
                merged_last_deltas.get(metric, value), value
            )
        if merged_last_deltas:
            merged[_KEY_LAST_DELTAS] = merged_last_deltas
    if merged_history:
        merged[_KEY_COMPLETED_DAYS] = merged_history
    return merged


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


async def async_load_daily_cache(
    hass: HomeAssistant, entry_id: str
) -> dict[str, dict[str, Any]]:
    """Load cached midnight snapshots for a config entry.

    Returns a mapping keyed by device_id where each value is a snapshot object
    with the shape ``{"day": "YYYY-MM-DD", "values": {metric: raw_units}}``.
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
            normalized_snapshot = _normalize_snapshot(payload)
            if normalized_snapshot is None:
                continue
            normalized_device_id = str(device_id)
            result[normalized_device_id] = _merge_snapshots(
                result.get(normalized_device_id),
                normalized_snapshot,
            )
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
        clean_values = _clean_metric_values(values)
        if not clean_values:
            continue
        clean_snapshot: dict[str, Any] = {
            _KEY_DAY: day,
            _KEY_VALUES: clean_values,
        }
        completed_days = _clean_completed_days(payload.get(_KEY_COMPLETED_DAYS))
        if completed_days:
            clean_snapshot[_KEY_COMPLETED_DAYS] = completed_days
        last_deltas = _clean_metric_values(payload.get(_KEY_LAST_DELTAS))
        if last_deltas:
            clean_snapshot[_KEY_LAST_DELTAS] = last_deltas
        cleaned[str(device_id)] = clean_snapshot

    async def _async_persist() -> None:
        """Finish the serialized Store transaction even if setup is cancelled."""
        async with _store_lock(hass):
            store = _store(hass)
            loaded = await store.async_load()
            data = dict(loaded) if isinstance(loaded, dict) else {}
            raw_entries = data.get(_KEY_ENTRIES)
            entries = dict(raw_entries) if isinstance(raw_entries, dict) else {}
            entries[entry_id] = cleaned
            data[_KEY_ENTRIES] = entries
            await store.async_save(data)

    persist_task = hass.async_create_task(
        _async_persist(),
        name=f"{DOMAIN}_save_local_daily_cache_{entry_id}",
        eager_start=False,
    )
    await asyncio.shield(persist_task)


def daily_delta(
    snapshot: dict[str, Any] | None,
    metric_key: str,
    current_lifetime_value: float | None,
    *,
    today: date,
) -> int | None:
    """Compute today's energy delta for a metric using a stored midnight anchor.

    Parameters:
        snapshot (dict | None): Stored snapshot with an ISO ``day`` and a
            ``values`` mapping containing anchored raw counter values.
        metric_key (str): Metric key to read from `snapshot["values"]`.
        current_lifetime_value (int | float | None): Current lifetime energy counter
            for the metric; if `None` the delta is disabled.
        today (date): Local date used to validate the snapshot day.

    Returns:
        int | None: The computed delta in the metric's raw counter unit when the
        snapshot and current lifetime counter are valid; otherwise ``None``.
    """
    if current_lifetime_value is None:
        return None
    try:
        current = int(current_lifetime_value)
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
        containing integer native-unit anchors. Jackery main-device counters
        use 0.01 kWh units; CT counters use Wh.
    """
    today_iso = _isoformat_day(today)
    completed_days = _clean_completed_days(
        snapshot.get(_KEY_COMPLETED_DAYS) if isinstance(snapshot, dict) else None
    )
    if not isinstance(snapshot, dict) or snapshot.get(_KEY_DAY) != today_iso:
        previous_day = snapshot.get(_KEY_DAY) if isinstance(snapshot, dict) else None
        last_deltas = _clean_metric_values(
            snapshot.get(_KEY_LAST_DELTAS) if isinstance(snapshot, dict) else None
        )
        if isinstance(previous_day, str) and last_deltas:
            try:
                parsed_previous_day = date.fromisoformat(previous_day)
            except ValueError:
                parsed_previous_day = None
            if parsed_previous_day is not None and parsed_previous_day < today:
                completed_days[previous_day] = _merge_completed_days(
                    {previous_day: completed_days.get(previous_day, {})},
                    {previous_day: last_deltas},
                )[previous_day]
        cutoff = (today - timedelta(days=_MAX_COMPLETED_DAY_HISTORY)).isoformat()
        completed_days = {
            day: metrics for day, metrics in completed_days.items() if day >= cutoff
        }
        clean_values: dict[str, int] = {}
        for metric, value in current_values.items():
            if value is None:
                continue
            try:
                clean_values[metric] = int(value)
            except (TypeError, ValueError):
                continue
        refreshed: dict[str, Any] = {
            _KEY_DAY: today_iso,
            _KEY_VALUES: clean_values,
        }
        if completed_days:
            refreshed[_KEY_COMPLETED_DAYS] = completed_days
        return refreshed
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
    refreshed = {_KEY_DAY: today_iso, _KEY_VALUES: merged}
    if completed_days:
        refreshed[_KEY_COMPLETED_DAYS] = completed_days
    last_deltas = _clean_metric_values(snapshot.get(_KEY_LAST_DELTAS))
    if last_deltas:
        refreshed[_KEY_LAST_DELTAS] = last_deltas
    return refreshed


def record_latest_deltas(
    snapshot: dict[str, Any], deltas: Mapping[str, int]
) -> dict[str, Any]:
    """Return a snapshot carrying the latest observed delta for each metric."""
    recorded = dict(snapshot)
    clean_deltas = _clean_metric_values(dict(deltas))
    if clean_deltas:
        latest = _clean_metric_values(recorded.get(_KEY_LAST_DELTAS))
        for metric, value in clean_deltas.items():
            latest[metric] = max(latest.get(metric, value), value)
        recorded[_KEY_LAST_DELTAS] = latest
    return recorded


def period_delta(
    snapshot: dict[str, Any] | None,
    metric_key: str,
    current_day_delta: float | None,
    *,
    today: date,
    period: str,
) -> int | None:
    """Sum a native-unit period delta only when every elapsed day is covered."""
    if current_day_delta is None:
        return None
    try:
        current_delta = int(current_day_delta)
    except (TypeError, ValueError):
        return None
    if current_delta < 0:
        return None
    if period == DATE_TYPE_DAY:
        return current_delta
    if period == DATE_TYPE_WEEK:
        period_start = today - timedelta(days=today.weekday())
    elif period == DATE_TYPE_MONTH:
        period_start = today.replace(day=1)
    elif period == DATE_TYPE_YEAR:
        period_start = today.replace(month=1, day=1)
    else:
        return None
    completed_days = _clean_completed_days(
        snapshot.get(_KEY_COMPLETED_DAYS) if isinstance(snapshot, dict) else None
    )
    total = current_delta
    cursor = period_start
    while cursor < today:
        metrics = completed_days.get(cursor.isoformat())
        if metrics is None or metric_key not in metrics:
            return None
        total += metrics[metric_key]
        cursor += timedelta(days=1)
    return total


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
    "period_delta",
    "record_latest_deltas",
    "refresh_snapshot",
    "snapshot_day",
]
