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


import json
import logging
from typing import TYPE_CHECKING, Any, Final

from homeassistant.helpers.storage import Store

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)  # noqa: RUF067
_STORAGE_VERSION: Final = 1
_STORAGE_KEY: Final = f"{DOMAIN}.local_daily_cache"
_KEY_ENTRIES: Final = "entries"
_KEY_DAY: Final = "day"
_KEY_VALUES: Final = "values"


def _store(hass: HomeAssistant) -> Store[dict[str, Any]]:
    """Provide the Home Assistant Store configured for the module's local midnight snapshot cache.

    Returns:
        The `Store` configured with this module's storage key and version.
    """
    return Store(hass, _STORAGE_VERSION, _STORAGE_KEY)


def _isoformat_day(today: date) -> str:
    """
    Return the ISO-formatted day string for the given date.
    
    Returns:
        The date formatted as `YYYY-MM-DD`.
    """
    return today.isoformat()


async def async_load_daily_cache(
    hass: HomeAssistant, entry_id: str
) -> dict[str, dict[str, Any]]:
    """Load cached midnight snapshots for a config entry.

    Returns a mapping keyed by device_id where each value is a snapshot object with the shape
    {"day": "YYYY-MM-DD", "values": {metric: wh}}. Malformed store entries are ignored; an
    empty dict is returned when the store is missing or contains no valid snapshots. Callers
    should compare each snapshot's "day" to the current date before using its values.

    Parameters:
        entry_id (str): Config entry identifier whose snapshots to load.

    Returns:
        dict[str, dict[str, Any]]: Device-id -> snapshot mapping containing validated
        and normalized snapshot data.
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
    """Persist per-device midnight snapshot data for a configuration entry.

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
            continue  # type: ignore[unreachable]
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
    """
    Compute today's energy delta by subtracting the stored midnight anchor from the current lifetime counter.
    
    Parameters:
        snapshot (dict | None): Stored snapshot expected to contain `"day"` (ISO date string) and `"values"` (mapping metric keys to anchored Wh values).
        metric_key (str): Key in `snapshot["values"]` identifying the metric anchor to use.
        current_lifetime_wh (int | float | None): Current lifetime energy counter for the metric; if `None` the delta is disabled.
        today (date): Local date used to validate that `snapshot["day"]` matches the current day.
    
    Returns:
        int | None: Delta in watt-hours as an `int` when the snapshot is valid for `today`, the anchor exists and both the anchor and current value convert to integers and `current >= anchor`; `None` otherwise.
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
        return None
    return current - anchor_int


def refresh_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    today: date,
    current_values: dict[str, int | float | None],
) -> dict[str, Any]:
    """
    Produce a per-device snapshot anchored to `today` containing integer-convertible lifetime metric anchors.
    
    If `snapshot` is missing or its recorded day differs from `today`, a new snapshot is created by anchoring every metric in `current_values` whose value is not `None` and can be converted to `int`. If `snapshot` is already for `today`, existing integer anchors are preserved and metrics from `current_values` are added only when an anchor does not already exist and the value is convertible to `int`. Entries with `None` or non-convertible values are omitted.
    
    Parameters:
        snapshot (dict[str, Any] | None): Existing per-device snapshot; may be `None`.
        today (date): Current date used as the snapshot day.
        current_values (dict[str, int | float | None]): Current lifetime metric readings; `None` or non-numeric values are ignored.
    
    Returns:
        dict[str, Any]: Snapshot with keys `"day"` (ISO `YYYY-MM-DD`) and `"values"` (mapping metric keys to integer Wh anchors).
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
    """
    Report whether the snapshot represents a different day than the given date.
    
    Returns:
        `True` when `snapshot` is not a dict or its `"day"` value does not equal `today.isoformat()`, `False` otherwise.
    """
    if not isinstance(snapshot, dict):
        return True
    return snapshot.get(_KEY_DAY) != _isoformat_day(today)


def snapshot_day(snapshot: dict[str, Any] | None) -> str | None:
    """Extracts the ISO day string from a snapshot.

    Parameters:
        snapshot (dict[str, Any] | None): Snapshot expected to contain the day value under the module's day key.

    Returns:
        str | None: The ISO day string (`YYYY-MM-DD`) if present and a `str`, otherwise `None`.
    """
    if not isinstance(snapshot, dict):
        return None
    day = snapshot.get(_KEY_DAY)
    return day if isinstance(day, str) else None


def local_daily_signature(snapshots: Mapping[str, Any]) -> str:
    """
    Produce a stable JSON signature for a snapshots mapping.
    
    Parameters:
        snapshots (Mapping[str, Any]): Mapping of device IDs to per-device snapshot objects; used to detect content changes.
    
    Returns:
        signature (str): Deterministic JSON string representation of `snapshots` (stable key ordering) suitable for change detection.
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
