"""Persist only current-day lifetime-counter anchors.

Historical energy belongs to Home Assistant's Recorder. This Store keeps the
minimum state required to continue an already-observed local day after a Home
Assistant restart; it never stores completed days, weeks, months, or years.
"""

from __future__ import annotations

import asyncio
import logging
import json
from collections.abc import Mapping
from datetime import date
from typing import Any, Final, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.json import json_dumps
from homeassistant.helpers.storage import Store

from ..const import (
    CACHE_ENTRIES_KEY,
    CACHE_STORAGE_VERSION,
    LOCAL_DAILY_CACHE_DAY_KEY,
    LOCAL_DAILY_CACHE_FULL_DAY_METRICS_KEY,
    LOCAL_DAILY_CACHE_STORAGE_KEY,
    LOCAL_DAILY_CACHE_VALUES_KEY,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_STORAGE_VERSION: Final = CACHE_STORAGE_VERSION
_STORAGE_KEY: Final = LOCAL_DAILY_CACHE_STORAGE_KEY
_LOCK_KEY: Final = f"{_STORAGE_KEY}.lock"
_KEY_ENTRIES: Final = CACHE_ENTRIES_KEY
_KEY_DAY: Final = LOCAL_DAILY_CACHE_DAY_KEY
_KEY_VALUES: Final = LOCAL_DAILY_CACHE_VALUES_KEY
_KEY_FULL_DAY_METRICS: Final = LOCAL_DAILY_CACHE_FULL_DAY_METRICS_KEY


def _store_lock(hass: HomeAssistant) -> asyncio.Lock:
    """Return the runtime lock protecting the shared Store file."""
    lock = hass.data.get(_LOCK_KEY)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        hass.data[_LOCK_KEY] = lock
    return lock


def _store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    """Return the Home Assistant Store containing current-day anchors."""
    return Store(hass, _STORAGE_VERSION, _STORAGE_KEY)


def _clean_values(value: object) -> dict[str, int]:
    """Return integer lifetime counters keyed by metric name."""
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, int] = {}
    for metric, raw in value.items():
        if not isinstance(metric, str) or isinstance(raw, bool) or raw is None:
            continue
        try:
            cleaned[metric] = int(raw)
        except OverflowError, TypeError, ValueError:
            continue
    return cleaned


def _normalize_snapshot(value: object) -> dict[str, Any] | None:
    """Validate one persisted current-day anchor."""
    if not isinstance(value, dict):
        return None
    day = value.get(_KEY_DAY)
    if not isinstance(day, str):
        return None
    try:
        if date.fromisoformat(day).isoformat() != day:
            return None
    except ValueError:
        return None
    values = _clean_values(value.get(_KEY_VALUES))
    if not values:
        return None
    raw_full_day = value.get(_KEY_FULL_DAY_METRICS)
    full_day_metrics = (
        sorted(
            metric
            for metric in raw_full_day
            if isinstance(metric, str) and metric in values
        )
        if isinstance(raw_full_day, list | tuple | set | frozenset)
        else []
    )
    snapshot: dict[str, Any] = {_KEY_DAY: day, _KEY_VALUES: values}
    if full_day_metrics:
        snapshot[_KEY_FULL_DAY_METRICS] = full_day_metrics
    return snapshot


def _merge_snapshots(
    current: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Merge reauth rows without manufacturing historical data."""
    if current is None or candidate[_KEY_DAY] > current[_KEY_DAY]:
        return candidate
    if candidate[_KEY_DAY] < current[_KEY_DAY]:
        return current
    current_values = _clean_values(current.get(_KEY_VALUES))
    candidate_values = _clean_values(candidate.get(_KEY_VALUES))
    values = dict(current_values)
    for metric, raw in candidate_values.items():
        values[metric] = min(values.get(metric, raw), raw)
    full_day_metrics = {
        metric
        for snapshot in (current, candidate)
        for metric in snapshot.get(_KEY_FULL_DAY_METRICS, ())
        if isinstance(metric, str) and metric in values
    }
    merged: dict[str, Any] = {
        _KEY_DAY: current[_KEY_DAY],
        _KEY_VALUES: values,
    }
    if full_day_metrics:
        merged[_KEY_FULL_DAY_METRICS] = sorted(full_day_metrics)
    return merged


async def async_load_daily_cache(
    hass: HomeAssistant,
    entry_id: str,
) -> dict[str, dict[str, Any]]:
    """Load current-day anchors, recovering same-device rows after reauth."""
    async with _store_lock(hass):
        data = await _store(hass).async_load()
    if not isinstance(data, dict):
        return {}
    entries = data.get(_KEY_ENTRIES)
    if not isinstance(entries, dict):
        return {}
    requested = entries.get(entry_id)
    rows = [requested] if isinstance(requested, dict) else []
    rows.extend(
        row
        for candidate_id, row in entries.items()
        if candidate_id != entry_id and isinstance(row, dict)
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        for device_id, raw_snapshot in row.items():
            snapshot = _normalize_snapshot(raw_snapshot)
            if snapshot is None:
                continue
            normalized_id = str(device_id)
            result[normalized_id] = _merge_snapshots(
                result.get(normalized_id),
                snapshot,
            )
    return result


async def async_save_daily_cache(
    hass: HomeAssistant,
    entry_id: str,
    *,
    snapshots: Mapping[str, object],
) -> None:
    """Persist only current-day anchors and discard legacy history fields."""
    cleaned: dict[str, dict[str, Any]] = {}
    for device_id, raw_snapshot in snapshots.items():
        snapshot = _normalize_snapshot(raw_snapshot)
        normalized_id = str(device_id).strip()
        if snapshot is not None and normalized_id:
            cleaned[normalized_id] = snapshot
    async with _store_lock(hass):
        store = _store(hass)
        loaded = await store.async_load()
        data = dict(loaded) if isinstance(loaded, dict) else {}
        raw_entries = data.get(_KEY_ENTRIES)
        entries = dict(raw_entries) if isinstance(raw_entries, dict) else {}
        entries[entry_id] = cleaned
        data[_KEY_ENTRIES] = entries
        await store.async_save(data)


def daily_delta(
    snapshot: dict[str, Any] | None,
    metric_key: str,
    current_lifetime_value: float | None,
    *,
    today: date,
) -> int | None:
    """Return today's native-unit delta from a verified full-day anchor."""
    if current_lifetime_value is None or isinstance(current_lifetime_value, bool):
        return None
    if not isinstance(snapshot, dict) or snapshot.get(_KEY_DAY) != today.isoformat():
        return None
    values = _clean_values(snapshot.get(_KEY_VALUES))
    if metric_key not in set(snapshot.get(_KEY_FULL_DAY_METRICS, ())):
        return None
    anchor = values.get(metric_key)
    if anchor is None:
        return None
    try:
        current = int(current_lifetime_value)
    except OverflowError, TypeError, ValueError:
        return None
    return current - anchor if current >= anchor else None


def refresh_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    today: date,
    current_values: Mapping[str, int | float | None],
    baseline_covers_full_day: bool = False,
) -> dict[str, Any]:
    """Refresh the current-day anchor without retaining prior-day history."""
    today_iso = today.isoformat()
    normalized = _normalize_snapshot(snapshot)
    if normalized is None or normalized[_KEY_DAY] != today_iso:
        values = _clean_values(dict(current_values))
        refreshed: dict[str, Any] = {_KEY_DAY: today_iso, _KEY_VALUES: values}
        if baseline_covers_full_day and values:
            refreshed[_KEY_FULL_DAY_METRICS] = sorted(values)
        return refreshed

    values = _clean_values(normalized.get(_KEY_VALUES))
    for metric, raw in _clean_values(dict(current_values)).items():
        values.setdefault(metric, raw)
    full_day_metrics = {
        metric
        for metric in normalized.get(_KEY_FULL_DAY_METRICS, ())
        if isinstance(metric, str) and metric in values
    }
    if baseline_covers_full_day:
        full_day_metrics.update(values)
    refreshed = {_KEY_DAY: today_iso, _KEY_VALUES: values}
    if full_day_metrics:
        refreshed[_KEY_FULL_DAY_METRICS] = sorted(full_day_metrics)
    return refreshed


def local_daily_signature(snapshots: Mapping[str, Any]) -> str:
    """Return a stable change-detection signature for current-day anchors."""
    return json.dumps(snapshots, sort_keys=True, default=str)


__all__ = [
    "async_load_daily_cache",
    "async_save_daily_cache",
    "daily_delta",
    "local_daily_signature",
    "refresh_snapshot",
]
