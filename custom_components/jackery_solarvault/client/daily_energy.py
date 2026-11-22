"""Persist current-day anchors and bounded own-counter period observations.

Historical energy belongs to Home Assistant's Recorder. This Store keeps the
minimum state required to continue already-observed counter intervals after a
restart. Four current-period counter checkpoints retain coverage, not historical
power samples or reconstructed values for unobserved days.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta
import json
import logging
from typing import TYPE_CHECKING, Any, Final, cast

from homeassistant.core import CoreState

from ..const import (
    CACHE_ENTRIES_KEY,
    CACHE_STORAGE_VERSION,
    LOCAL_DAILY_CACHE_DAY_KEY,
    LOCAL_DAILY_CACHE_FULL_DAY_METRICS_KEY,
    LOCAL_DAILY_CACHE_STORAGE_KEY,
    LOCAL_DAILY_CACHE_VALUES_KEY,
)
from ..util import get_store_lock
from .storage import JackeryStore as Store

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_STORAGE_VERSION: Final = CACHE_STORAGE_VERSION
_STORAGE_KEY: Final = LOCAL_DAILY_CACHE_STORAGE_KEY
_LOCK_KEY: Final = f"{_STORAGE_KEY}.lock"
_STORE_INSTANCE_KEY: Final = f"{_STORAGE_KEY}.store"
_KEY_ENTRIES: Final = CACHE_ENTRIES_KEY
_KEY_DAY: Final = LOCAL_DAILY_CACHE_DAY_KEY
_KEY_VALUES: Final = LOCAL_DAILY_CACHE_VALUES_KEY
_KEY_FULL_DAY_METRICS: Final = LOCAL_DAILY_CACHE_FULL_DAY_METRICS_KEY
_KEY_CONFLICTING_METRICS: Final = "_conflicting_full_day_metrics"
_KEY_LAST_VALUES: Final = "_last_observed_values"
_KEY_PERIODS: Final = "_period_counter_observations"
_KEY_PERIOD_OWNER: Final = "_period_counter_owner"
_KEY_DAILY_OWNERS: Final = "_daily_counter_owners"


def daily_counter_owner(value: object) -> dict[str, str] | None:
    """Normalize a proven parent and optional CT identity for one own counter."""
    if not isinstance(value, dict):
        return None
    result: dict[str, str] = {}
    for key in (
        "parent_serial",
        "system_id",
        "system_serial",
        "ct_device_id",
        "ct_serial",
    ):
        raw = value.get(key)
        if raw is None:
            continue
        if isinstance(raw, bool) or not isinstance(raw, str | int):
            return None
        normalized = str(raw).strip()
        if not normalized or len(normalized) > 128:  # ruff: ignore[magic-value-comparison]
            return None
        result[key] = normalized.casefold() if key.endswith("serial") else normalized
    return result if result.get("parent_serial") else None


def daily_counter_owners_match(first: object, second: object) -> bool:
    """Keep sparse enrichment while rejecting another parent or CT accessory."""
    old, new = daily_counter_owner(first), daily_counter_owner(second)
    return bool(
        old is not None
        and new is not None
        and old["parent_serial"] == new["parent_serial"]
        and old.get("ct_device_id") == new.get("ct_device_id")
        and all(
            key not in old or key not in new or old[key] == new[key]
            for key in ("system_id", "system_serial", "ct_serial")
        )
    )


def _daily_owners(snapshot: object) -> dict[str, dict[str, str]]:
    raw = snapshot.get(_KEY_DAILY_OWNERS) if isinstance(snapshot, dict) else None
    return (
        {
            metric: owner
            for metric, value in raw.items()
            if isinstance(metric, str)
            and (owner := daily_counter_owner(value)) is not None
        }
        if isinstance(raw, dict)
        else {}
    )


def _remove_daily_metric(snapshot: dict[str, Any], metric: str) -> None:
    for key in (_KEY_VALUES, _KEY_LAST_VALUES, _KEY_DAILY_OWNERS):
        if isinstance(snapshot.get(key), dict):
            snapshot[key].pop(metric, None)
    for key in (_KEY_FULL_DAY_METRICS, _KEY_CONFLICTING_METRICS):
        if key in snapshot:
            snapshot[key] = [item for item in snapshot[key] if item != metric]


def _bind_daily_owners(
    snapshot: object, owners: Mapping[str, object]
) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    result = deepcopy(snapshot)
    previous = _daily_owners(result)
    for metric, owner in owners.items():
        if not daily_counter_owners_match(previous.get(metric), owner):
            _remove_daily_metric(result, metric)
    return result


def _copy_daily_owners(source: dict[str, Any], target: dict[str, Any]) -> None:
    owners = {
        metric: owner
        for metric, owner in _daily_owners(source).items()
        if metric in target.get(_KEY_VALUES, {})
    }
    if owners:
        target[_KEY_DAILY_OWNERS] = owners


def _counter_period_start(period: str, today: date) -> date | None:
    if period == "day":
        return today
    if period == "week":
        return today - timedelta(days=today.weekday())
    if period == "month":
        return today.replace(day=1)
    if period == "year":
        return today.replace(month=1, day=1)
    return None


def _counter_owner(value: object) -> tuple[str, str, str] | None:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3  # ruff: ignore[magic-value-comparison]
        or any(type(part) is not str or len(part) > 128 for part in value)  # ruff: ignore[magic-value-comparison]
        or not value[0]
        or not value[1].strip()
    ):
        return None
    return value[0], value[1], value[2]


def _counter_integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= 9_007_199_254_740_991  # ruff: ignore[magic-value-comparison]


def _counter_owners_compatible(
    first: tuple[str, str, str] | None, second: tuple[str, str, str] | None
) -> bool:
    """A model discovery fills metadata; it must not replace the same battery."""
    return bool(
        first is not None
        and second is not None
        and first[:2] == second[:2]
        and (not first[2] or not second[2] or first[2] == second[2])
    )


def _live_counter_integer(value: object) -> int | None:
    """Accept the same integral counter from JSON numbers or PropertyBean strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = int(value)
    except OverflowError, TypeError, ValueError:
        return None
    if isinstance(value, float) and parsed != value:
        return None
    return parsed if _counter_integer(parsed) else None


def _counter_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return (
            parsed
            if parsed.tzinfo is not None and parsed.utcoffset() is not None
            else None
        )
    except TypeError, ValueError, OverflowError:
        return None


def _period_counter_records(
    snapshot: object, *, only_period: str | None = None, only_metric: str | None = None
) -> dict[str, dict[str, dict[str, Any]]]:
    """Validate bounded checkpoints independently from the legacy day anchor."""
    if (
        not isinstance(snapshot, dict)
        or _counter_owner(snapshot.get(_KEY_PERIOD_OWNER)) is None
    ):
        return {}
    raw = snapshot.get(_KEY_PERIODS)
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for period in ("day", "week", "month", "year"):
        if only_period is not None and period != only_period:
            continue
        metrics = raw.get(period)
        if not isinstance(metrics, dict):
            continue
        for metric, record in metrics.items():
            if only_metric is not None and metric != only_metric:
                continue
            if metric not in {"pv1Egy", "pv2Egy", "pv3Egy", "pv4Egy"} or not isinstance(
                record, dict
            ):
                continue
            try:
                start = date.fromisoformat(record.get("period_start", ""))
            except TypeError, ValueError:
                continue
            since = _counter_time(record.get("since"))
            until = _counter_time(record.get("until"))
            if (
                _counter_period_start(period, start) != start  # ruff: ignore[too-many-boolean-expressions]
                or since is None
                or until is None
                or until < since
                or _counter_period_start(period, since.date()) != start
                or _counter_period_start(period, until.date()) != start
                or any(
                    not _counter_integer(record.get(key))
                    for key in ("last_raw", "total_raw", "reset_count", "observed_days")
                )
                or record.get("unit") != "0.01 kWh"
                or type(record.get("has_interval")) is not bool
                or type(record.get("complete")) is not bool
            ):
                continue
            clean = {
                key: record[key]
                for key in (
                    "period_start",
                    "since",
                    "until",
                    "last_raw",
                    "total_raw",
                    "reset_count",
                    "observed_days",
                    "has_interval",
                    "unit",
                    "complete",
                )
            }
            clean["complete"] = bool(
                clean["complete"]
                and not clean["reset_count"]
                and since.date() == start
                and since.time() == datetime.min.time()
            )
            result.setdefault(period, {})[metric] = clean
    return result


def _copy_counter_periods(source: dict[str, Any], target: dict[str, Any]) -> None:
    owner = _counter_owner(source.get(_KEY_PERIOD_OWNER))
    records = _period_counter_records(source)
    if owner is not None:
        target[_KEY_PERIOD_OWNER] = list(owner)
    if records:
        target[_KEY_PERIODS] = records


def _merge_counter_periods(  # ruff: ignore[too-many-branches, too-many-locals]
    current: dict[str, Any], candidate: dict[str, Any], target: dict[str, Any]
) -> None:
    current_owner = _counter_owner(current.get(_KEY_PERIOD_OWNER))
    candidate_owner = _counter_owner(candidate.get(_KEY_PERIOD_OWNER))
    owner = current_owner or candidate_owner
    if owner is None:
        return
    if (
        current_owner is not None
        and candidate_owner is not None
        and _counter_owners_compatible(current_owner, candidate_owner)
    ):
        owner = (owner[0], owner[1], current_owner[2] or candidate_owner[2])
    target[_KEY_PERIOD_OWNER] = list(owner)
    target_day = date.fromisoformat(target[_KEY_DAY])
    valid_period_starts = {
        period: start.isoformat()
        for period in ("day", "week", "month", "year")
        if (start := _counter_period_start(period, target_day)) is not None
    }
    merged = {
        period: {
            metric: record
            for metric, record in rows.items()
            if record["period_start"] == valid_period_starts.get(period)
        }
        for period, rows in _period_counter_records(current).items()
    }
    if not _counter_owners_compatible(candidate_owner, owner):
        if merged:
            target[_KEY_PERIODS] = merged
        return
    for period, rows in _period_counter_records(candidate).items():
        for metric, incoming in rows.items():
            if incoming["period_start"] != valid_period_starts.get(period):
                continue
            existing = merged.setdefault(period, {}).get(metric)
            if existing is None or existing["period_start"] != incoming["period_start"]:
                if (
                    existing is None
                    or incoming["period_start"] > existing["period_start"]
                ):
                    merged[period][metric] = incoming
                continue
            if existing["reset_count"] or incoming["reset_count"]:
                if (
                    existing["since"] == incoming["since"]
                    and incoming["until"] > existing["until"]
                ):
                    merged[period][metric] = incoming
                continue
            existing_since = _counter_time(existing["since"])
            incoming_since = _counter_time(incoming["since"])
            existing_until = _counter_time(existing["until"])
            incoming_until = _counter_time(incoming["until"])
            if (
                existing_since is None
                or incoming_since is None
                or existing_until is None
                or incoming_until is None
            ):
                continue
            early = existing if existing_since <= incoming_since else incoming
            late = existing if existing_until >= incoming_until else incoming
            early_until = existing_until if early is existing else incoming_until
            late_until = existing_until if late is existing else incoming_until
            baseline = early["last_raw"] - early["total_raw"]
            if late["last_raw"] < early["last_raw"] or baseline < 0:
                continue
            joined = dict(early)
            joined.update(
                last_raw=late["last_raw"],
                until=late["until"],
                total_raw=late["last_raw"] - baseline,
                observed_days=max(
                    early["observed_days"]
                    + int(late_until.date() > early_until.date()),
                    late["observed_days"],
                ),
                has_interval=bool(
                    existing["has_interval"]
                    or incoming["has_interval"]
                    or late["last_raw"] != baseline
                ),
            )
            merged[period][metric] = joined
    if merged:
        target[_KEY_PERIODS] = merged


def refresh_period_counter_observations(  # ruff: ignore[too-many-branches]
    snapshot: dict[str, Any] | None,
    *,
    owner: tuple[str, str, str],
    current_values: Mapping[str, object],
    today: date,
    observed_at: datetime,
) -> dict[str, Any]:
    """Extend only measured, non-overlapping intervals of each own PV counter."""
    result = deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    if (
        _counter_owner(owner) is None
        or observed_at.tzinfo is None
        or observed_at.date() != today
    ):
        return result
    clean_values = {
        key: parsed
        for key, value in current_values.items()
        if key in {"pv1Egy", "pv2Egy", "pv3Egy", "pv4Egy"}
        and (parsed := _live_counter_integer(value)) is not None
    }
    if not clean_values:
        return result
    old_owner = _counter_owner(result.get(_KEY_PERIOD_OWNER))
    owner_changed = old_owner is not None and not _counter_owners_compatible(
        old_owner, owner
    )
    periods = {} if owner_changed else _period_counter_records(result)
    if owner_changed:
        # A replacement device must not inherit its predecessor's day anchor.
        result = refresh_snapshot(None, today=today, current_values=clean_values)
    elif (
        not isinstance(result.get(_KEY_VALUES), dict)
        or result.get(_KEY_DAY) != today.isoformat()
    ):
        result = refresh_snapshot(result, today=today, current_values=clean_values)
    if old_owner is not None and _counter_owners_compatible(old_owner, owner):
        owner = (owner[0], owner[1], owner[2] or old_owner[2])
    result[_KEY_PERIOD_OWNER] = list(owner)
    for period in ("day", "week", "month", "year"):
        start = _counter_period_start(period, today)
        if start is None:
            continue
        rows = periods.setdefault(period, {})
        for metric, current in clean_values.items():
            record = rows.get(metric)
            if record is not None and record["period_start"] != start.isoformat():
                record = None
            if record is None:
                anchor = _live_counter_integer(result.get(_KEY_VALUES, {}).get(metric))
                trusted_day = (
                    not owner_changed
                    and result.get(_KEY_DAY) == today.isoformat()
                    and daily_counter_owners_match(
                        _daily_owners(result).get(metric), {"parent_serial": owner[1]}
                    )
                    and metric in result.get(_KEY_FULL_DAY_METRICS, ())
                    and metric not in result.get(_KEY_CONFLICTING_METRICS, ())
                    and anchor is not None
                    and _counter_integer(anchor)
                    and anchor <= current
                )
                since = (
                    datetime.combine(
                        today, datetime.min.time(), tzinfo=observed_at.tzinfo
                    )
                    if trusted_day
                    else observed_at
                )
                rows[metric] = {
                    "period_start": start.isoformat(),
                    "since": since.isoformat(),
                    "until": observed_at.isoformat(),
                    "last_raw": current,
                    "total_raw": (
                        current - anchor if trusted_day and anchor is not None else 0
                    ),
                    "reset_count": 0,
                    "observed_days": 1,
                    "unit": "0.01 kWh",
                    "has_interval": bool(trusted_day),
                    "complete": bool(trusted_day and today == start),
                }
                continue
            until = _counter_time(record["until"])
            if until is None or observed_at < until:
                continue
            if current == record["last_raw"] and record["has_interval"]:
                # Receipt clocks alone must not rewrite Store data or HA states.
                continue
            if current < record["last_raw"]:
                record["reset_count"] += 1
                record["complete"] = False
            else:
                record["total_raw"] += current - record["last_raw"]
                record["has_interval"] = bool(
                    record["has_interval"]
                    or current != record["last_raw"]
                    or observed_at > until
                )
            if observed_at.date() != until.date():
                record["observed_days"] += 1
            record["last_raw"] = current
            record["until"] = observed_at.isoformat()
    result[_KEY_PERIODS] = periods
    return result


def local_counter_period_value(
    snapshot: dict[str, Any] | None,
    metric: str,
    *,
    period: str,
    today: date,
    owner: tuple[str, str, str],
) -> tuple[float | None, dict[str, Any]]:
    """Return observed kWh and its actual coverage, never an invented full period."""
    if not isinstance(snapshot, dict) or not _counter_owners_compatible(
        _counter_owner(snapshot.get(_KEY_PERIOD_OWNER)), owner
    ):
        return None, {}
    record = (
        _period_counter_records(snapshot, only_period=period, only_metric=metric)
        .get(period, {})
        .get(metric)
    )
    start = _counter_period_start(period, today)
    if record is None or start is None or record["period_start"] != start.isoformat():
        return None, {}
    until = _counter_time(record["until"])
    if until is None or until.date() > today or not record["has_interval"]:
        return None, {}
    partial = not record["complete"]
    return round(record["total_raw"] / 100, 5), {
        "baseline_source": "own_observed_counter",
        "coverage": "partial_observed_period"
        if partial
        else "complete_observed_period",
        "partial_period": partial,
        "observed_since": record["since"],
        "observed_until": record["until"],
        "observed_days": record["observed_days"],
        "period_start": record["period_start"],
        "source_metric": metric,
        "source_counter_unit": "0.01 kWh",
        "period_value_unit": "kWh",
        "counter_resets": record["reset_count"],
    }


def _store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    """Return the Home Assistant Store containing current-day anchors."""
    store = hass.data.get(_STORE_INSTANCE_KEY)
    if store is None:
        store = Store(hass, _STORAGE_VERSION, _STORAGE_KEY)
        hass.data[_STORE_INSTANCE_KEY] = store
    return cast("Store[dict[str, Any]]", store)


def _clean_values(value: object) -> dict[str, int]:
    """Return integer lifetime counters keyed by metric name."""
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, int] = {}
    for metric, raw in value.items():
        if not isinstance(metric, str) or isinstance(raw, bool) or raw is None:
            continue
        if isinstance(raw, int | float) and raw < 0:
            continue
        try:
            parsed = int(raw)
        except OverflowError, TypeError, ValueError:
            continue
        if parsed >= 0:
            cleaned[metric] = parsed
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
    if not values and not _period_counter_records(value):
        return None
    raw_conflicts = value.get(_KEY_CONFLICTING_METRICS)
    conflicts = (
        sorted(
            metric
            for metric in raw_conflicts
            if isinstance(metric, str) and metric in values
        )
        if isinstance(raw_conflicts, list | tuple | set | frozenset)
        else []
    )
    raw_full_day = value.get(_KEY_FULL_DAY_METRICS)
    full_day_metrics = (
        sorted(
            metric
            for metric in raw_full_day
            if isinstance(metric, str) and metric in values and metric not in conflicts
        )
        if isinstance(raw_full_day, list | tuple | set | frozenset)
        else []
    )
    snapshot: dict[str, Any] = {_KEY_DAY: day, _KEY_VALUES: values}
    if full_day_metrics:
        snapshot[_KEY_FULL_DAY_METRICS] = full_day_metrics
    if conflicts:
        snapshot[_KEY_CONFLICTING_METRICS] = conflicts
    last_values = _clean_values(value.get(_KEY_LAST_VALUES))
    last_values = {key: raw for key, raw in last_values.items() if key in values}
    if last_values:
        snapshot[_KEY_LAST_VALUES] = last_values
    _copy_counter_periods(value, snapshot)
    _copy_daily_owners(value, snapshot)
    return snapshot


def _merge_snapshots(
    current: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Merge reauth rows without manufacturing historical data."""
    if current is None:
        return candidate
    if candidate[_KEY_DAY] != current[_KEY_DAY]:
        newer, older = (
            (candidate, current)
            if candidate[_KEY_DAY] > current[_KEY_DAY]
            else (current, candidate)
        )
        merged = deepcopy(newer)
        _merge_counter_periods(newer, older, merged)
        return merged
    return _merge_same_day_snapshots(current, candidate)


def _merge_same_day_snapshots(  # ruff: ignore[too-many-branches, too-many-locals]
    current: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """Merge same-day anchors without transferring evidence between values."""
    current_owner = _counter_owner(current.get(_KEY_PERIOD_OWNER))
    candidate_owner = _counter_owner(candidate.get(_KEY_PERIOD_OWNER))
    if (
        current_owner is not None
        and candidate_owner is not None
        and not _counter_owners_compatible(current_owner, candidate_owner)
    ):
        return deepcopy(current)
    current, candidate = deepcopy(current), deepcopy(candidate)
    current_daily_owners, candidate_daily_owners = (
        _daily_owners(current),
        _daily_owners(candidate),
    )
    for metric in set(current.get(_KEY_VALUES, {})) | set(
        candidate.get(_KEY_VALUES, {})
    ):
        old, new = current_daily_owners.get(metric), candidate_daily_owners.get(metric)
        if old is not None and not daily_counter_owners_match(old, new):
            _remove_daily_metric(candidate, metric)
        elif old is None and new is not None:
            _remove_daily_metric(current, metric)
    current_values = _clean_values(current.get(_KEY_VALUES))
    candidate_values = _clean_values(candidate.get(_KEY_VALUES))
    values = dict(current_values)
    last_values = _clean_values(current.get(_KEY_LAST_VALUES))
    for observations in (
        current_values,
        candidate_values,
        _clean_values(candidate.get(_KEY_LAST_VALUES)),
    ):
        for metric, raw in observations.items():
            last_values[metric] = max(last_values.get(metric, raw), raw)
    conflicts = set(current.get(_KEY_CONFLICTING_METRICS, ())) | set(
        candidate.get(_KEY_CONFLICTING_METRICS, ())
    )
    full_day_metrics = set(current.get(_KEY_FULL_DAY_METRICS, ())) - conflicts
    candidate_full = set(candidate.get(_KEY_FULL_DAY_METRICS, ()))
    for metric, raw in candidate_values.items():
        if metric in conflicts:
            values.setdefault(metric, raw)
            continue
        old_full = metric in full_day_metrics
        new_full = metric in candidate_full
        old_value = values.get(metric)
        if old_full:
            contradictory = (new_full and old_value != raw) or (
                not new_full and old_value is not None and raw < old_value
            )
        else:
            contradictory = new_full and old_value is not None and old_value < raw
        if contradictory:
            conflicts.add(metric)
            full_day_metrics.discard(metric)
        elif new_full:
            values[metric] = raw
            full_day_metrics.add(metric)
        elif not old_full:
            values[metric] = min(values.get(metric, raw), raw)
    merged: dict[str, Any] = {
        _KEY_DAY: current[_KEY_DAY],
        _KEY_VALUES: values,
    }
    if full_day_metrics:
        merged[_KEY_FULL_DAY_METRICS] = sorted(full_day_metrics)
    if conflicts:
        merged[_KEY_CONFLICTING_METRICS] = sorted(conflicts)
    if last_values:
        merged[_KEY_LAST_VALUES] = last_values
    _merge_counter_periods(current, candidate, merged)
    merged_owners = _daily_owners(candidate)
    for metric, owner in _daily_owners(current).items():
        merged_owners[metric] = {**merged_owners.get(metric, {}), **owner}
    _copy_daily_owners({_KEY_DAILY_OWNERS: merged_owners}, merged)
    return merged


def merge_daily_counter_snapshots(
    current: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    """Reconcile a late Store read without discarding accepted own observations."""
    return _merge_snapshots(
        _normalize_snapshot(current), _normalize_snapshot(candidate) or current
    )


async def async_load_daily_cache(
    hass: HomeAssistant,
    entry_id: str,
) -> dict[str, dict[str, Any]]:
    """Load current-day anchors, recovering same-device rows after reauth."""
    async with get_store_lock(hass, _LOCK_KEY):
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
    commit_guard: Callable[[], bool] | None = None,
) -> bool | None:
    """Save current-day anchors; None means HA deferred the final write."""
    cleaned: dict[str, dict[str, Any]] = {}
    for device_id, raw_snapshot in snapshots.items():
        snapshot = _normalize_snapshot(raw_snapshot)
        normalized_id = str(device_id).strip()
        if snapshot is not None and normalized_id:
            cleaned[normalized_id] = snapshot
    async with get_store_lock(hass, _LOCK_KEY):
        if commit_guard is not None and not commit_guard():
            return False
        store = _store(hass)
        loaded = await store.async_load()
        if commit_guard is not None and not commit_guard():
            return False
        data = dict(loaded) if isinstance(loaded, dict) else {}
        raw_entries = data.get(_KEY_ENTRIES)
        entries = dict(raw_entries) if isinstance(raw_entries, dict) else {}
        entries[entry_id] = cleaned
        data[_KEY_ENTRIES] = entries
        await store.async_save(data)
        if hass.state is CoreState.stopping:
            return None
        return await store.async_load() == data


def daily_delta(  # ruff: ignore[too-many-return-statements]
    snapshot: dict[str, Any] | None,
    metric_key: str,
    current_lifetime_value: float | None,
    *,
    today: date,
    owner: dict[str, str] | None = None,
) -> int | None:
    """Return today's native-unit delta from a verified full-day anchor."""
    if (
        current_lifetime_value is None
        or isinstance(current_lifetime_value, bool)
        or (
            isinstance(current_lifetime_value, int | float)
            and current_lifetime_value < 0
        )
    ):
        return None
    if not isinstance(snapshot, dict) or snapshot.get(_KEY_DAY) != today.isoformat():
        return None
    if owner is not None and not daily_counter_owners_match(
        _daily_owners(snapshot).get(metric_key), owner
    ):
        return None
    values = _clean_values(snapshot.get(_KEY_VALUES))
    if metric_key in set(
        snapshot.get(_KEY_CONFLICTING_METRICS, ())
    ) or metric_key not in set(snapshot.get(_KEY_FULL_DAY_METRICS, ())):
        return None
    anchor = values.get(metric_key)
    if anchor is None:
        return None
    try:
        current = int(current_lifetime_value)
    except OverflowError, TypeError, ValueError:
        return None
    last = _clean_values(snapshot.get(_KEY_LAST_VALUES)).get(metric_key)
    return (
        current - anchor
        if current >= anchor and (last is None or current >= last)
        else None
    )


def refresh_snapshot(  # ruff: ignore[too-many-branches]
    snapshot: dict[str, Any] | None,
    *,
    today: date,
    current_values: Mapping[str, int | float | None],
    baseline_covers_full_day: bool = False,
    owners: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Refresh the current-day anchor without retaining prior-day history."""
    today_iso = today.isoformat()
    normalized = _normalize_snapshot(
        _bind_daily_owners(snapshot, owners) if owners is not None else snapshot
    )
    if normalized is None or normalized[_KEY_DAY] != today_iso:
        values = _clean_values(dict(current_values))
        refreshed: dict[str, Any] = {_KEY_DAY: today_iso, _KEY_VALUES: values}
        if baseline_covers_full_day and values:
            refreshed[_KEY_FULL_DAY_METRICS] = sorted(values)
        if normalized is not None:
            _copy_counter_periods(normalized, refreshed)
        if owners is not None:
            _copy_daily_owners({_KEY_DAILY_OWNERS: dict(owners)}, refreshed)
        return refreshed

    values = _clean_values(normalized.get(_KEY_VALUES))
    full_day_metrics = {
        metric
        for metric in normalized.get(_KEY_FULL_DAY_METRICS, ())
        if isinstance(metric, str) and metric in values
    }
    conflicts = set(normalized.get(_KEY_CONFLICTING_METRICS, ()))
    observed = _clean_values(dict(current_values))
    last_values = _clean_values(normalized.get(_KEY_LAST_VALUES))
    for metric, raw in observed.items():
        last = last_values.get(metric, values.get(metric))
        if last is not None and raw < last:
            conflicts.add(metric)
            full_day_metrics.discard(metric)
        last_values[metric] = raw
        if baseline_covers_full_day and metric not in full_day_metrics | conflicts:
            values[metric] = raw
        else:
            values.setdefault(metric, raw)
    if baseline_covers_full_day:
        full_day_metrics.update(
            metric for metric in observed if metric not in conflicts
        )
    refreshed = {_KEY_DAY: today_iso, _KEY_VALUES: values}
    if full_day_metrics:
        refreshed[_KEY_FULL_DAY_METRICS] = sorted(full_day_metrics)
    if conflicts:
        refreshed[_KEY_CONFLICTING_METRICS] = sorted(conflicts)
    if last_values:
        refreshed[_KEY_LAST_VALUES] = last_values
    _copy_counter_periods(normalized, refreshed)
    _copy_daily_owners(normalized, refreshed)
    if owners is not None:
        combined_owners = _daily_owners(refreshed)
        for metric, raw_owner in owners.items():
            if (owner := daily_counter_owner(raw_owner)) is not None:
                combined_owners[metric] = {**combined_owners.get(metric, {}), **owner}
        _copy_daily_owners({_KEY_DAILY_OWNERS: combined_owners}, refreshed)
    return refreshed


def local_daily_signature(snapshots: Mapping[str, Any]) -> str:
    """Return a stable change-detection signature for current-day anchors."""
    return json.dumps(snapshots, sort_keys=True, default=str)


__all__ = [
    "async_load_daily_cache",
    "async_save_daily_cache",
    "daily_counter_owner",
    "daily_counter_owners_match",
    "daily_delta",
    "local_counter_period_value",
    "local_daily_signature",
    "merge_daily_counter_snapshots",
    "refresh_period_counter_observations",
    "refresh_snapshot",
]
