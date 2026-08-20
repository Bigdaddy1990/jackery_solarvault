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
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from ..const import (
    CACHE_ENTRIES_KEY,
    CACHE_STORAGE_VERSION,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DOMAIN,
    LOCAL_DAILY_CACHE_COMPLETE_DAYS_KEY,
    LOCAL_DAILY_CACHE_COMPLETED_DAYS_KEY,
    LOCAL_DAILY_CACHE_DAY_KEY,
    LOCAL_DAILY_CACHE_HISTORY_DAYS,
    LOCAL_DAILY_CACHE_LAST_DELTAS_KEY,
    LOCAL_DAILY_CACHE_STORAGE_KEY,
    LOCAL_DAILY_CACHE_VALUES_KEY,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)
_STORAGE_VERSION: Final = CACHE_STORAGE_VERSION
_STORAGE_KEY: Final = LOCAL_DAILY_CACHE_STORAGE_KEY
_LOCK_KEY: Final = f"{_STORAGE_KEY}.lock"
_KEY_ENTRIES: Final = CACHE_ENTRIES_KEY
_KEY_DAY: Final = LOCAL_DAILY_CACHE_DAY_KEY
_KEY_VALUES: Final = LOCAL_DAILY_CACHE_VALUES_KEY
_KEY_COMPLETED_DAYS: Final = LOCAL_DAILY_CACHE_COMPLETED_DAYS_KEY
_KEY_COMPLETE_DAYS: Final = LOCAL_DAILY_CACHE_COMPLETE_DAYS_KEY
_KEY_LAST_DELTAS: Final = LOCAL_DAILY_CACHE_LAST_DELTAS_KEY
_MAX_COMPLETED_DAY_HISTORY: Final = LOCAL_DAILY_CACHE_HISTORY_DAYS


def _is_iso_day(day: object, *, context: str) -> bool:
    """Validate an ISO day string, reporting why a value was rejected."""
    if not isinstance(day, str):
        _LOGGER.warning(
            "Jackery daily cache: dropping non-string %s key %r", context, day
        )
        return False
    try:
        canonical = date.fromisoformat(day).isoformat()
    except ValueError as err:
        _LOGGER.warning(
            "Jackery daily cache: dropping unparsable %s %r: %s", context, day, err
        )
        return False
    if canonical != day:
        _LOGGER.warning(
            "Jackery daily cache: dropping non-canonical %s %r (expected %r)",
            context,
            day,
            canonical,
        )
        return False
    return True


def _clean_metric_values(values: object) -> dict[str, int]:
    """Return non-negative integer metric values from an arbitrary object."""
    if not isinstance(values, dict):
        return {}
    cleaned: dict[str, int] = {}
    for metric, value in values.items():
        if not isinstance(metric, str):
            _LOGGER.warning(
                "Jackery daily cache: dropping non-string metric key %r", metric
            )
            continue
        if isinstance(value, bool):
            _LOGGER.warning(
                "Jackery daily cache: dropping boolean metric %s=%r", metric, value
            )
            continue
        try:
            normalized = int(value)
        except (OverflowError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Jackery daily cache: dropping metric %s with unusable value "
                "%r: %s",
                metric,
                value,
                err,
            )
            continue
        if normalized < 0:
            _LOGGER.warning(
                "Jackery daily cache: dropping negative metric %s=%d",
                metric,
                normalized,
            )
            continue
        cleaned[metric] = normalized
    return cleaned


def _clean_completed_days(value: object) -> dict[str, dict[str, int]]:
    """Return validated ISO-day history rows in native counter units."""
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, dict[str, int]] = {}
    for day, metrics in value.items():
        if not _is_iso_day(day, context="completed-day history"):
            continue
        clean_metrics = _clean_metric_values(metrics)
        if clean_metrics:
            cleaned[day] = clean_metrics
    return cleaned


def _clean_complete_days(value: object) -> set[str]:
    """Return ISO days whose anchors and rollover samples cover a full day."""
    if not isinstance(value, list):
        return set()
    cleaned: set[str] = set()
    for day in value:
        if not _is_iso_day(day, context="complete-day marker"):
            continue
        cleaned.add(day)
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
    complete_days = _clean_complete_days(payload.get(_KEY_COMPLETE_DAYS))
    if complete_days:
        normalized[_KEY_COMPLETE_DAYS] = sorted(complete_days)
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
    merged_complete_days = _clean_complete_days(existing.get(_KEY_COMPLETE_DAYS))
    merged_complete_days.update(
        _clean_complete_days(incoming.get(_KEY_COMPLETE_DAYS))
    )
    existing_day = existing.get(_KEY_DAY)
    incoming_day = incoming[_KEY_DAY]
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
    if merged_complete_days:
        merged[_KEY_COMPLETE_DAYS] = sorted(merged_complete_days)
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
        if not isinstance(values, dict):
            _LOGGER.warning(
                "Jackery daily cache: dropping day %r with non-dict values %r",
                day,
                values,
            )
            continue
        if not _is_iso_day(day, context="stored day"):
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
        complete_days = _clean_complete_days(payload.get(_KEY_COMPLETE_DAYS))
        if complete_days:
            clean_snapshot[_KEY_COMPLETE_DAYS] = sorted(complete_days)
        last_deltas = _clean_metric_values(payload.get(_KEY_LAST_DELTAS))
        if last_deltas:
            clean_snapshot[_KEY_LAST_DELTAS] = last_deltas
        normalized_device_id = str(device_id).strip()
        if normalized_device_id:
            cleaned[normalized_device_id] = clean_snapshot

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
    if current_lifetime_value is None or isinstance(current_lifetime_value, bool):
        return None
    try:
        current = int(current_lifetime_value)
    except (OverflowError, TypeError, ValueError):
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
    if anchor is None or isinstance(anchor, bool):
        return None
    try:
        anchor_int = int(anchor)
    except (OverflowError, TypeError, ValueError):
        return None
    if current < anchor_int:
        return None
    return current - anchor_int


def refresh_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    today: date,
    current_values: dict[str, int | float | None],
    baseline_covers_full_day: bool = False,
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
        baseline_covers_full_day: Whether this anchor was established by a
            continuously observed local-day rollover. Cold-start anchors must
            leave this false because they omit energy produced before startup.

    Returns:
        dict[str, Any]: Snapshot with an ISO ``day`` and a ``values`` mapping
        containing integer native-unit anchors. Jackery main-device counters
        use 0.01 kWh units; CT counters use Wh.
    """
    today_iso = _isoformat_day(today)
    completed_days = _clean_completed_days(
        snapshot.get(_KEY_COMPLETED_DAYS) if isinstance(snapshot, dict) else None
    )
    complete_days = _clean_complete_days(
        snapshot.get(_KEY_COMPLETE_DAYS) if isinstance(snapshot, dict) else None
    )
    if not isinstance(snapshot, dict) or snapshot.get(_KEY_DAY) != today_iso:
        previous_day = snapshot.get(_KEY_DAY) if isinstance(snapshot, dict) else None
        last_deltas = _clean_metric_values(
            snapshot.get(_KEY_LAST_DELTAS) if isinstance(snapshot, dict) else None
        )
        if (
            baseline_covers_full_day
            and isinstance(previous_day, str)
            and previous_day in complete_days
            and last_deltas
        ):
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
        complete_days = {day for day in complete_days if day >= cutoff}
        if baseline_covers_full_day:
            complete_days.add(today_iso)
        clean_values: dict[str, int] = {}
        for metric, value in current_values.items():
            if value is None:
                continue
            try:
                clean_values[metric] = int(value)
            except (OverflowError, TypeError, ValueError) as err:
                _LOGGER.warning(
                    "Jackery daily cache: dropping current metric %s=%r: %s",
                    metric,
                    value,
                    err,
                )
                continue
        refreshed: dict[str, Any] = {
            _KEY_DAY: today_iso,
            _KEY_VALUES: clean_values,
        }
        if completed_days:
            refreshed[_KEY_COMPLETED_DAYS] = completed_days
        if complete_days:
            refreshed[_KEY_COMPLETE_DAYS] = sorted(complete_days)
        return refreshed
    existing_values = snapshot.get(_KEY_VALUES)
    if not isinstance(existing_values, dict):
        existing_values = {}
    merged: dict[str, int] = {}
    for metric, value in existing_values.items():
        if not isinstance(metric, str):
            _LOGGER.warning(
                "Jackery daily cache: dropping non-string metric key %r", metric
            )
            continue
        try:
            merged[metric] = int(value)
        except (OverflowError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Jackery daily cache: dropping stored metric %s=%r: %s",
                metric,
                value,
                err,
            )
            continue
    for metric, value in current_values.items():
        if metric in merged:
            continue
        if value is None:
            continue
        try:
            merged[metric] = int(value)
        except (OverflowError, TypeError, ValueError) as err:
            _LOGGER.warning(
                "Jackery daily cache: dropping incoming metric %s=%r: %s",
                metric,
                value,
                err,
            )
            continue
    refreshed = {_KEY_DAY: today_iso, _KEY_VALUES: merged}
    if completed_days:
        refreshed[_KEY_COMPLETED_DAYS] = completed_days
    if baseline_covers_full_day:
        complete_days.add(today_iso)
    if complete_days:
        refreshed[_KEY_COMPLETE_DAYS] = sorted(complete_days)
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
    if current_day_delta is None or isinstance(current_day_delta, bool):
        return None
    try:
        current_delta = int(current_day_delta)
    except (OverflowError, TypeError, ValueError):
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
    complete_days = _clean_complete_days(
        snapshot.get(_KEY_COMPLETE_DAYS) if isinstance(snapshot, dict) else None
    )
    total = current_delta
    cursor = period_start
    while cursor < today:
        cursor_iso = cursor.isoformat()
        if cursor_iso not in complete_days:
            return None
        metrics = completed_days.get(cursor_iso)
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
