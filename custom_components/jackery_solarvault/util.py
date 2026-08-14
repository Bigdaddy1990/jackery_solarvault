"""Shared helpers for Jackery SolarVault entities."""

import calendar
import contextlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
import math
import operator
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, Final, NamedTuple, cast

from .const import (
    APP_CHART_LABELS,
    APP_CHART_SERIES_Y,
    APP_CHART_SERIES_Y1,
    APP_CHART_SERIES_Y2,
    APP_CHART_SERIES_Y3,
    APP_CHART_SERIES_Y4,
    APP_CHART_SERIES_Y5,
    APP_CHART_SERIES_Y6,
    APP_CHART_STAT_PERIODS,
    APP_HOME_GRID_SERIES_KEYS,
    APP_PERIOD_DATE_TYPES,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_DATE_TYPE_ALT,
    APP_REQUEST_END_DATE,
    APP_REQUEST_END_DATE_ALT,
    APP_REQUEST_META,
    APP_REQUEST_STAT_TYPE,
    APP_SAVINGS_CALC_META,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_BATTERY_TRENDS,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_PV_PROFIT,
    APP_STAT_TOTAL_CARBON,
    APP_STAT_TOTAL_CHARGE,
    APP_STAT_TOTAL_CT_INPUT_ENERGY,
    APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
    APP_STAT_TOTAL_DISCHARGE,
    APP_STAT_TOTAL_GENERATION,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_IN_EPS_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_OUT_EPS_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    APP_STAT_TOTAL_REVENUE,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_TOTAL_SOLAR_REVENUE,
    APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
    APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
    APP_STAT_UNIT,
    APP_UNIT_KWH,
    APP_UNIT_WH,
    APP_YEAR_BACKFILL_META,
    CT_PHASE_POWER_PAIRS,
    CT_TOTAL_POWER_PAIR,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    FIELD_ACCESSORIES,
    FIELD_CURRENT_VERSION,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEVICE_TYPE,
    FIELD_DEV_ID,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_GRID_IN_PW,
    FIELD_GRID_OUT_PW,
    FIELD_ID,
    FIELD_IDX,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_IN_ONGRID_PW,
    FIELD_MAC,
    FIELD_OTHER_LOAD_PW,
    FIELD_OUT_GRID_SIDE_PW,
    FIELD_OUT_ONGRID_PW,
    FIELD_SINGLE_PRICE,
    FIELD_SN,
    FIELD_WNAME,
    PAYLOAD_ALARM,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_CIRCUIT_PROPERTY,
    PAYLOAD_CT_METER,
    PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX,
    PAYLOAD_DEBUG_LOG_MAX_BYTES,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_METER_HEADS,
    PAYLOAD_OTA,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_STATISTIC,
    PAYLOAD_SUBDEVICES,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
    REDACTED_VALUE,
    REDACT_KEYS,
    SUBDEVICE_SCAN_NAME_LABELS,
    SUBDEVICE_SCAN_NAME_MANUFACTURERS,
    TASK_PLAN_BODY,
    TASK_PLAN_TASKS,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

# CPU-Optimierung: Regex auf Modulebene kompilieren, nicht pro Schleifendurchlauf
_DAY_CHART_MINUTE_RE = re.compile(r"\s*(\d{1,2}):(\d{2})\s*")
_MAX_COMPACT_YEAR_VALUE_TEXT_LENGTH = 64
_SUBDEVICE_ID_RE = re.compile(r"[^A-Za-z0-9_]+")
_REDACT_KEYS_CASEFOLD: Final[frozenset[str]] = frozenset(
    key.casefold() for key in REDACT_KEYS
)
_MIN_REDACT_LITERAL_LENGTH: Final = 4

# Calendar / time bounds used in validation guards.
_MONTHS_PER_YEAR: Final = 12
_MAX_MONTH_BUCKETS: Final = 31
_HOURS_PER_DAY: Final = 24
_MAX_HOUR: Final = 23
_MAX_MINUTE: Final = 59
# A POSIX timestamp at/above this magnitude is in milliseconds, not seconds.
_MILLIS_TIMESTAMP_THRESHOLD: Final = 100_000_000_000
# Treat |value| below this as numerically zero for chart-bucket presence checks.
_NEAR_ZERO_EPSILON: Final = 0.00001
# Plausible upper bound for a single electricity price / per-kWh derived rate.
_MAX_PRICE_PER_KWH: Final = 10
# Plausible upper bound for a carbon-per-generation correction factor.
_MAX_CARBON_FACTOR: Final = 5
_APP_UNIT_WATT: Final = "w"
_DAY_POWER_SAMPLE_MINUTES: Final = 5
_MINUTES_PER_HOUR: Final = 60
_WATTS_PER_KILOWATT: Final = 1000
WHOLE_INT_TEXT_RE = re.compile(r"[+-]?\d+(?:\.0+)?\Z")


def app_energy_unit_scale(source: Mapping[str, Any]) -> float | None:
    """Return the factor that converts a stat payload's values into kWh.

    The app stat bodies carry their own ``unit`` field (default ``"kWh"``, see
    ``CtStatApi.Bean``/``EpsStatApi.Bean``). Treating every non-kWh answer as
    unusable silently dropped valid Wh payloads, while passing them through
    unscaled would inflate them by 1000.

    Returns:
        float | None: ``1.0`` for kWh (or a missing unit), ``0.001`` for Wh and
        ``None`` when the unit is present but not a supported energy unit.
    """
    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if not unit or unit == APP_UNIT_KWH:
        return 1.0
    if unit == APP_UNIT_WH:
        return 1 / _WATTS_PER_KILOWATT
    return None


def config_entry_bool_option(entry: object, key: str, default: bool) -> bool:
    """Resolve a boolean configuration option.

    Fall back to legacy entry data when options are absent.

    Parameters:
        entry (Any): Config entry-like object with optional `options` and legacy `data`
        mappings.
        key (str): Option name to look up.
        default (bool): Value to return when the option is not present or cannot be
        parsed.

    Returns:
        bool: The resolved boolean value (`true` or `false`), or `default` if the value
        is missing or not parseable.
    """
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    value = options.get(key)
    if value is None:
        value = data.get(key, default)
    parsed = safe_bool(value)
    return default if parsed is None else parsed


def config_entry_str_option(entry: object, key: str, default: str) -> str:
    """Resolve a string configuration option from a config entry.

    Fall back to legacy entry data and a provided default.

    Looks up `key` first in `entry.options`, then in `entry.data`, and returns the
    resolved value coerced to `str`. If the resolved value is `None`, returns `default`.

    Parameters:
        entry (Any): Configuration entry object that may have `.options` and `.data`
        mappings.
        key (str): Option key to look up.
        default (str): Default string to return when the option is not set or resolves
        to `None`.

    Returns:
        str: The resolved option value coerced to `str`, or `default` when unset.
    """
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    value = options.get(key)
    if value is None:
        value = data.get(key, default)
    if value is None:
        return default
    return str(value)


def config_entry_int_option(entry: object, key: str, default: int) -> int:
    """Retrieve an integer option from a config entry.

    Fall back to legacy setup data when the option is absent.

    Parameters:
        entry (Any): Config entry-like object with optional `options` and `data`
        mappings.
        key (str): Option key to read from `entry.options` or `entry.data`.
        default (int): Value to return when the option is missing or cannot be converted
        to an int.

    Returns:
        int: The resolved integer option or `default` if not present or not convertible.
    """
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    value = options.get(key)
    if value is None:
        value = data.get(key, default)
    if value is None:
        return default
    try:
        return int(value)
    except TypeError, ValueError:
        return default


def subdevice_branding(scan_name: object) -> tuple[str | None, str | None]:
    """Return manufacturer and model label for a documented subdevice `scan_name`.

    Looks up `scan_name` in the internal accessory catalog and returns a tuple
    (manufacturer, model_label). If `scan_name` is not a non-empty string or
    is not found in the catalog, returns `(None, None)` so callers may fall
    back to other payload fields.

    Returns:
        tuple[str | None, str | None]: `(manufacturer, model_label)` or
        `(None, None)` when unknown or invalid.
    """
    if not isinstance(scan_name, str) or not scan_name:
        return None, None
    manufacturer = SUBDEVICE_SCAN_NAME_MANUFACTURERS.get(scan_name)
    label = SUBDEVICE_SCAN_NAME_LABELS.get(scan_name)
    return manufacturer, label


def utc_now() -> datetime:
    """Get the current UTC time as a timezone-aware datetime.

    Returns:
        The current UTC datetime with tzinfo set to UTC.
    """
    return datetime.now(UTC)


def parse_utc_datetime(
    value: float | str | datetime,
) -> datetime:  # parsed timestamp types
    """Parse various timestamp representations and return a timezone-aware UTC datetime.

    Parameters:
        value (Any): A datetime, a numeric timestamp (seconds; milliseconds are accepted and will be converted), or a string containing either a numeric timestamp or an ISO-8601 datetime (trailing "Z" is accepted). Empty strings and unsupported types are rejected.

    Returns:
        datetime: The parsed datetime normalized to UTC with tzinfo set.

    Raises:
        ValueError: If the input is an empty string, an unsupported type, or an invalid
        timestamp/ISO string.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            timestamp = float(value)
        except (OverflowError, ValueError) as err:
            msg = f"invalid UTC timestamp: {value!r}"
            raise ValueError(msg) from err
        if not math.isfinite(timestamp):
            msg = f"invalid UTC timestamp: {value!r}"
            raise ValueError(msg)
        if abs(timestamp) >= _MILLIS_TIMESTAMP_THRESHOLD:
            timestamp /= 1000
        try:
            parsed = datetime.fromtimestamp(timestamp, UTC)
        except (OSError, OverflowError, ValueError) as err:
            msg = f"invalid UTC timestamp: {value!r}"
            raise ValueError(msg) from err
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            msg = "timestamp must not be empty"
            raise ValueError(msg)
        with contextlib.suppress(ValueError, OSError, OverflowError):
            timestamp = float(normalized)
            if abs(timestamp) >= _MILLIS_TIMESTAMP_THRESHOLD:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, UTC)
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as err:
            msg = f"invalid UTC timestamp: {value!r}"
            raise ValueError(msg) from err
    else:
        msg = f"unsupported UTC timestamp: {value!r}"
        raise TypeError(msg)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def coordinator_entity_signature(  # ruff: ignore[too-many-locals] - one field per entity-shape component
    coordinator_data: dict[str, Any] | None,
) -> tuple[tuple[Any, ...], ...]:
    """Produce a deterministic coordinator-payload shape signature.

    The lightweight signature is used during entity setup.

    Parameters:
        coordinator_data (dict[str, Any] | None): Mapping of device IDs to their
        coordinator payloads; may be None.

    Returns:
        tuple[tuple[Any, ...], ...]: A tuple of per-device signature tuples. Each entry
        preserves the device ID and includes,
        in order: a tuple of smart-plug serials, battery pack count, meter head count, a
        boolean indicating presence of an
        alarm payload, a boolean indicating presence of an OTA current version, a
        boolean indicating presence of a CT meter. **Live property keys and stat
        section usability are intentionally excluded** so routine poll updates do not
        re-trigger dynamic entity setup (which causes entity_registry spam).
    """
    if not coordinator_data:
        return ()

    def _present_fields(item: dict[str, Any]) -> tuple[str, ...]:
        return tuple(sorted(str(key) for key in item if not str(key).startswith("_")))

    sig: list[Any] = []
    for dev_id in sorted(coordinator_data):
        payload = coordinator_data.get(dev_id) or {}
        discovery_source = ""
        for section_name in (PAYLOAD_DEVICE, PAYLOAD_DISCOVERY):
            section = payload.get(section_name) or {}
            if not isinstance(section, dict):
                continue
            raw_source = section.get(PAYLOAD_DISCOVERY_SOURCE)
            if raw_source not in {None, ""}:
                discovery_source = str(raw_source)
                break
        system = payload.get(PAYLOAD_SYSTEM) or payload.get(PAYLOAD_SYSTEM_META) or {}
        accessories = payload.get(FIELD_ACCESSORIES)
        if not isinstance(accessories, list) and isinstance(system, dict):
            accessories = system.get(FIELD_ACCESSORIES)
        valid_accessories = (
            [item for item in accessories if isinstance(item, dict)]
            if isinstance(accessories, list)
            else []
        )
        accessory_keys = tuple(
            sorted(
                (
                    str(
                        accessory.get(FIELD_DEV_TYPE)
                        or accessory.get(FIELD_DEVICE_TYPE)
                        or ""
                    ),
                    first_nonblank_text(
                        accessory.get(FIELD_DEVICE_SN),
                        accessory.get(FIELD_DEV_SN),
                        accessory.get(FIELD_SN),
                        accessory.get(FIELD_DEVICE_ID),
                        accessory.get(FIELD_ID),
                        accessory.get(FIELD_DEV_ID),
                        fallback=f"index_{index}",
                    ),
                    _present_fields(accessory),
                )
                for index, accessory in enumerate(valid_accessories, start=1)
            )
        )
        plugs = sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS))
        plug_keys = tuple(
            (smart_plug_serial(plug), _present_fields(plug)) for plug in plugs
        )
        packs = payload.get(PAYLOAD_BATTERY_PACKS) or []
        valid_packs = (
            [item for item in packs if isinstance(item, dict)]
            if isinstance(packs, list)
            else []
        )
        pack_keys = tuple(
            sorted(
                (
                    first_nonblank_text(
                        pack.get(FIELD_DEVICE_SN),
                        pack.get(FIELD_DEV_SN),
                        pack.get(FIELD_SN),
                        fallback=f"index_{index}",
                    ),
                    _present_fields(pack),
                )
                for index, pack in enumerate(valid_packs, start=1)
            )
        )
        meter_heads = sorted_meter_heads(payload.get(PAYLOAD_METER_HEADS))
        meter_keys = tuple(
            (meter_head_serial(meter_head), _present_fields(meter_head))
            for meter_head in meter_heads
        )
        circuit_keys = tuple(
            (
                stable_subdevice_key("breaker", circuit_id(circuit), index),
                _present_fields(circuit),
            )
            for index, circuit in enumerate(
                sorted_circuits(payload.get(PAYLOAD_CIRCUIT_PROPERTY)),
                start=1,
            )
        )
        subdevice_keys = tuple(
            (
                stable_subdevice_key(
                    "sub_device",
                    sub_device_serial(sub_device),
                    index,
                ),
                _present_fields(sub_device),
            )
            for index, sub_device in enumerate(
                sorted_sub_devices(payload.get(PAYLOAD_SUBDEVICES)),
                start=1,
            )
        )

        sig.append((
            dev_id,
            discovery_source,
            accessory_keys,
            plug_keys,
            pack_keys,
            meter_keys,
            circuit_keys,
            subdevice_keys,
            payload.get(PAYLOAD_ALARM) is not None,
            bool((payload.get(PAYLOAD_OTA) or {}).get(FIELD_CURRENT_VERSION)),
            payload.get(PAYLOAD_CT_METER) is not None,
        ))
    return tuple(sig)


def append_unique_entity[EntityT](
    entities: list[EntityT],
    seen_unique_ids: set[str],
    entity: EntityT,
) -> bool:
    """Add an entity only when its unique ID has not been seen.

    Otherwise skip it.

    If the entity has a `unique_id` that already exists in `seen_unique_ids`, the entity
    is not appended and a debug message is emitted.

    Returns:
        `True` if the entity was appended, `False` if it was skipped due to a duplicate
        `unique_id`.
    """
    uid = getattr(entity, "unique_id", None)
    if uid and uid in seen_unique_ids:
        return False
    if uid:
        seen_unique_ids.add(uid)
    entities.append(entity)
    return True


def validate_app_period_date_type(date_type: str) -> str:
    """Return a supported Jackery app period type or raise ValueError."""
    if date_type not in APP_PERIOD_DATE_TYPES:
        msg = f"Unsupported Jackery app period dateType: {date_type!r}"
        raise ValueError(msg)
    return date_type


def app_period_range(date_type: str, *, today: date | None = None) -> tuple[date, date]:
    """Compute the Jackery app's inclusive period bounds.

    The bounds correspond to the given period type.

    Parameters:
        date_type (str): One of the documented app period types (day, week, month,
        year).
        today (date | None): Reference date used to compute the period; defaults to the
        current local date.

    Returns:
        tuple[date, date]: (begin_date, end_date) for the requested period, inclusive.
    """
    date_type = validate_app_period_date_type(date_type)
    if today is None:
        today = datetime.now(UTC).astimezone().date()
    if date_type == DATE_TYPE_DAY:
        return today, today
    if date_type == DATE_TYPE_WEEK:
        begin = today - timedelta(days=today.weekday())
        return begin, begin + timedelta(days=6)
    if date_type == DATE_TYPE_MONTH:
        begin = today.replace(day=1)
        end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return begin, end
    return today.replace(month=1, day=1), today.replace(month=12, day=31)


def _app_period_bound_to_date(value: str | date, *, field_name: str) -> date:
    """Return a validated ISO date bound for a Jackery app period request."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    normalized = str(value).strip()
    if not normalized:
        msg = f"Jackery app period {field_name} must not be empty"
        raise ValueError(msg)
    try:
        return date.fromisoformat(normalized)
    except ValueError as err:
        msg = (
            f"Jackery app period {field_name} must be an ISO date (YYYY-MM-DD): "
            f"{value!r}"
        )
        raise ValueError(msg) from err


def app_period_date_bounds(
    date_type: str,
    *,
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    today: date | None = None,
) -> tuple[str, str]:
    """Produce validated ISO-formatted app-period bounds.

    Return begin and end date strings for the specified period.

    Parameters:
        date_type (str): App period type (must be one of the module's supported date
        types).
        begin_date (str | date | None): Optional begin bound (ISO date string or date).
        When None, the period default begin is used.
        end_date (str | date | None): Optional end bound (ISO date string or date). When
        None, the period default end is used.
        today (date | None): Optional reference date used to compute period defaults
        when begin/end are omitted.

    Returns:
        tuple[str, str]: A pair of ISO date strings (begin_iso, end_iso).

    Raises:
        ValueError: If inputs are invalid for a date bound or if the resolved begin date
        is after the resolved end date.
    """
    default_begin, default_end = app_period_range(date_type, today=today)
    begin = _app_period_bound_to_date(
        default_begin if begin_date is None else begin_date,
        field_name=APP_REQUEST_BEGIN_DATE,
    )
    end = _app_period_bound_to_date(
        default_end if end_date is None else end_date,
        field_name=APP_REQUEST_END_DATE,
    )
    if begin > end:
        msg = (
            "Jackery app period beginDate must be before or equal to endDate: "
            f"{begin.isoformat()} > {end.isoformat()}"
        )
        raise ValueError(msg)
    return begin.isoformat(), end.isoformat()


def app_period_request_kwargs(
    date_type: str, *, today: date | None = None
) -> dict[str, str]:
    """Return method kwargs for documented app-period API calls."""
    begin, end = app_period_date_bounds(date_type, today=today)
    return {
        APP_REQUEST_DATE_TYPE_ALT: date_type,
        APP_REQUEST_BEGIN_DATE_ALT: begin,
        APP_REQUEST_END_DATE_ALT: end,
    }


def app_month_request_kwargs(year: int, month: int) -> dict[str, str]:
    """Return method kwargs for one explicit calendar-month app request."""
    if month < 1 or month > _MONTHS_PER_YEAR:
        msg = f"Unsupported Jackery app month: {month!r}"
        raise ValueError(msg)
    first = date(year, month, 1)
    last = first.replace(day=calendar.monthrange(year, month)[1])
    begin, end = app_period_date_bounds(
        DATE_TYPE_MONTH,
        begin_date=first,
        end_date=last,
    )
    return {
        APP_REQUEST_DATE_TYPE_ALT: DATE_TYPE_MONTH,
        APP_REQUEST_BEGIN_DATE_ALT: begin,
        APP_REQUEST_END_DATE_ALT: end,
    }


def app_year_request_kwargs(year: int) -> dict[str, str]:
    """Return method kwargs for one explicit calendar-year app request."""
    first = date(year, 1, 1)
    last = date(year, 12, 31)
    begin, end = app_period_date_bounds(
        DATE_TYPE_YEAR,
        begin_date=first,
        end_date=last,
    )
    return {
        APP_REQUEST_DATE_TYPE_ALT: DATE_TYPE_YEAR,
        APP_REQUEST_BEGIN_DATE_ALT: begin,
        APP_REQUEST_END_DATE_ALT: end,
    }


def statistics_http_backfill_dates(
    today: date,
    *,
    window_days: int,
    include_current_year: bool = False,
) -> list[date]:
    """Return completed local days covered by the automatic HTTP backfill.

    The automatic day-chart backfill repairs recently completed days by
    re-fetching their hourly app-stat curves over HTTP. Only fully
    *completed* local days qualify: ``today`` itself is always excluded
    because its buckets are still accumulating and are covered by the
    live current-day import path instead.

    Args:
        today: The current local calendar day (the running, incomplete day).
        window_days: How many recent completed days to cover when
            ``include_current_year`` is ``False``. Values ``<= 0`` yield an
            empty window.
        include_current_year: When ``False`` (the default) the window starts
            ``window_days`` days before ``today`` (a rolling recent window).
            When ``True`` the window instead starts at January 1st of
            ``today``'s calendar year, covering every completed day so far
            this year.

    Returns:
        Completed local days in ascending order, from the window start
        through ``today - 1`` (``today`` itself is always excluded).
    """
    end_day = today - timedelta(days=1)
    if include_current_year:
        start_day = today.replace(month=1, day=1)
    else:
        start_day = today - timedelta(days=max(0, window_days))
    if start_day > end_day:
        return []
    return [
        start_day + timedelta(days=offset)
        for offset in range((end_day - start_day).days + 1)
    ]


def historical_day_payload_from_sources(
    section_sources: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Convert fetched section-source dicts into the normal day payload shape.

    ``section_sources`` is keyed by the app-stat *section prefix* (for
    example ``device_battery_stat`` or ``home_trends``) as produced by the
    historical day-chart fetch. The day-chart importer, however, looks each
    source up under the same section keys the live day payload uses: a
    per-metric ``"{prefix}_day"`` key (for example ``device_battery_stat_day``)
    for regular stat sections, and the trend section name for home trends.
    This remaps each prefix onto that payload key so downstream chart
    conversion resolves the source unchanged.

    Args:
        section_sources: Gated section sources keyed by section prefix.

    Returns:
        The same sources re-keyed into normal day-payload section keys.
        Empty input (or entries with empty values) yields empty output.
    """
    payload: dict[str, dict[str, Any]] = {}
    for section_prefix, source in section_sources.items():
        if not source:
            continue
        section = (
            PAYLOAD_HOME_TRENDS
            if section_prefix == APP_SECTION_HOME_TRENDS
            else f"{section_prefix}_{DATE_TYPE_DAY}"
        )
        payload[section] = source
    return payload


def filter_completed_app_points(
    points: list[Any],
    date_type: str,
    reset_period: str,
    today: date,
) -> list[Any]:
    """Filter app points to completed buckets for entity-stat imports.

    Day points are always included. For longer periods, only points
    whose bucket date is strictly before today are included.
    """
    if date_type == "day":
        return points
    completed: list[Any] = []
    for point in points:
        start = point.start_date
        point_date = start.date() if isinstance(start, datetime) else start
        if not isinstance(point_date, date):
            continue
        if reset_period in {"day", "week", "month"}:
            if point_date >= today:
                continue
        elif reset_period == "year" and (
            point_date.year,
            point_date.month,
        ) >= (today.year, today.month):
            continue
        completed.append(point)
    return completed


def parse_statistics_backfill_date(value: object) -> date | None:
    """Parse a persisted ISO date for statistics repair decisions."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def statistics_current_year_recovery_needed(
    *,
    last_success: date,
    last_repair: date | None,
    failed_bucket_count: int,
    today: date,
) -> bool:
    """Return True when an old success marker may have skipped history.

    Older builds could persist ``last_successful_import_date`` from the
    current snapshot while the historical month/year repair never ran
    because a live MQTT window returned early. In that state the normal
    month-boundary branch would never revisit elapsed months of the same
    calendar year. Use ``last_repair_date`` as the recovery marker: once
    a repair has run in the same month as ``last_success``, the one-time
    current-year recovery is complete.
    """
    if today.month == 1:
        return False
    if last_success.year != today.year:
        return False
    if failed_bucket_count > 0:
        return last_repair is None or last_repair < today
    if last_repair is None:
        return True
    last_success_month = last_success.replace(day=1)
    return last_repair < last_success_month


def iter_calendar_months(start_date: date, end_date: date) -> list[date]:
    """Return first-of-month dates intersecting an inclusive date range."""
    cursor = start_date.replace(day=1)
    end_month = end_date.replace(day=1)
    months: list[date] = []
    while cursor <= end_month:
        months.append(cursor)
        if cursor.month == 12:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)
    return months


def iter_calendar_weeks(start_date: date, end_date: date) -> list[date]:
    """Return Monday week starts intersecting an inclusive date range."""
    cursor = start_date - timedelta(days=start_date.weekday())
    end_week = end_date - timedelta(days=end_date.weekday())
    weeks: list[date] = []
    while cursor <= end_week:
        weeks.append(cursor)
        cursor += timedelta(days=7)
    return weeks


def iter_calendar_years(start_date: date, end_date: date) -> list[int]:
    """Return calendar years intersecting an inclusive date range."""
    return list(range(start_date.year, end_date.year + 1))


def app_chart_period_meta(date_type: str) -> tuple[str, str] | None:
    """Return the external bucket id and label for an app chart period."""
    for period_date_type, bucket, bucket_label in APP_CHART_STAT_PERIODS:
        if period_date_type == date_type:
            return bucket, bucket_label
    return None


def app_chart_name_prefix(device_id: str, payload: dict[str, Any]) -> str:
    """Return a stable, user-readable app chart statistic name prefix."""
    return (
        (payload.get(PAYLOAD_SYSTEM) or {}).get(FIELD_DEVICE_NAME)
        or (payload.get(PAYLOAD_DISCOVERY) or {}).get(FIELD_DEVICE_NAME)
        or (payload.get(PAYLOAD_PROPERTIES) or {}).get(FIELD_WNAME)
        or f"Jackery {device_id}"
    )


def stat_row_start(row: Mapping[str, Any]) -> float | None:
    """Return a statistics row start timestamp in seconds."""
    start = row.get("start")
    if isinstance(start, datetime):
        return start.timestamp()
    return safe_float(start)


def safe_float(
    value: float | str | None,
) -> float | None:  # numeric payload value
    """Parse a payload value into a Python float.

    Return None when the value cannot be interpreted.

    Parameters:
        value (Any): Input value from a Jackery payload. Accepts numbers, None, or
        strings (including numeric strings,
            optionally using a single comma as the decimal separator when no dot is
            present). Empty or malformed strings
            and unsupported types produce `None`.

    Returns:
        float_value (float | None): The parsed float on success, or `None` if `value` is
        `None` or cannot be converted.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return _parse_float_string(value)
    try:
        parsed = float(value)
    except TypeError, ValueError, OverflowError:
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_float_string(value: str) -> float | None:
    """Parse a payload string into a float, tolerating a single comma decimal.

    Parameters:
        value (str): Candidate numeric string.

    Returns:
        float | None: The parsed float, or None for empty/malformed strings.
    """
    candidate = value.strip()
    if not candidate:
        return None
    if "," in candidate and "." not in candidate:
        if candidate.count(",") != 1:
            return None
        candidate = candidate.replace(",", ".")
    try:
        parsed = float(candidate)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def safe_int(value: object) -> int | None:  # integral payload value
    """Convert a value to an integer when possible.

    Returns None for a None input or when the value cannot be converted to an integer.

    Returns:
        int: The converted integer if successful, `None` otherwise.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        finite_integral = math.isfinite(value) and value.is_integer()
        return int(value) if finite_integral else None
    if isinstance(value, str):
        candidate = value.strip()
        try:
            return int(candidate)
        except ValueError:
            return None
    return None


def _payload_debug_redacted(
    value: object,
) -> object:  # recursive JSON walker over payload
    """Create a JSON-serializable copy of `value` with sensitive fields redacted.

    Redaction is mandatory and case-insensitive. Values for keys listed in
    `REDACT_KEYS` are replaced recursively with `REDACTED_VALUE`; tuples become
    lists so the resulting structure can be serialized as JSON.

    Parameters:
        value (Any): The input payload to redact.

    Returns:
        Any: A redacted, JSON-serializable representation of `value`.
    """
    if isinstance(value, dict):
        return {
            str(key): REDACTED_VALUE
            if str(key).casefold() in _REDACT_KEYS_CASEFOLD
            else _payload_debug_redacted(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_payload_debug_redacted(item) for item in value]
    if isinstance(value, tuple):
        return [_payload_debug_redacted(item) for item in value]
    return value


def _sensitive_text_values(value: object) -> frozenset[str]:
    """Collect non-trivial scalar values stored below sensitive keys."""
    sensitive_values: set[str] = set()

    def _collect_scalar(item: object) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                _collect_scalar(nested)
            return
        if isinstance(item, list | tuple):
            for nested in item:
                _collect_scalar(nested)
            return
        if isinstance(item, bool) or item is None:
            return
        if isinstance(item, str | int | float):
            text = str(item)
            if len(text) >= _MIN_REDACT_LITERAL_LENGTH and text != REDACTED_VALUE:
                sensitive_values.add(text)

    def _walk(item: object) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if str(key).casefold() in _REDACT_KEYS_CASEFOLD:
                    _collect_scalar(nested)
                else:
                    _walk(nested)
            return
        if isinstance(item, list | tuple):
            for nested in item:
                _walk(nested)

    _walk(value)
    return frozenset(sensitive_values)


def _redact_sensitive_text_values(
    value: object,
    sensitive_values: frozenset[str],
) -> object:
    """Scrub known sensitive literals echoed under otherwise safe keys."""
    if isinstance(value, dict):
        return {
            str(key): _redact_sensitive_text_values(item, sensitive_values)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact_sensitive_text_values(item, sensitive_values) for item in value]
    if isinstance(value, str):
        redacted = value
        for sensitive in sorted(sensitive_values, key=len, reverse=True):
            redacted = redacted.replace(sensitive, REDACTED_VALUE)
        return redacted
    if (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and str(value) in sensitive_values
    ):
        return REDACTED_VALUE
    return value


def redacted_json_safe_payload(
    value: object,
    *,
    sensitive_sources: tuple[object, ...] = (),
) -> object:  # recursive JSON walker over payload
    """Produce a JSON-serializable payload with known sensitive Jackery fields redacted.

    The redaction is applied recursively to nested dicts/lists/tuples. Known
    sensitive values are also scrubbed when a transport error echoes them under
    a non-sensitive key such as ``last_error``. Additional raw sources may only
    add values to the mandatory redaction set; they cannot weaken redaction.

    Returns:
        Any: The input value converted into a JSON-safe structure with sensitive fields
        replaced by the module's redaction marker.
    """
    sensitive_values = set(_sensitive_text_values(value))
    for source in sensitive_sources:
        sensitive_values.update(_sensitive_text_values(source))
    return _redact_sensitive_text_values(
        _payload_debug_redacted(value),
        frozenset(sensitive_values),
    )


def active_redact_keys() -> frozenset[str]:
    """Return the immutable mandatory diagnostics redaction-key set."""
    return REDACT_KEYS


def chart_series_debug(source: object) -> dict[str, Any]:
    """Produce diagnostics for chart-series arrays in an app payload.

    Parses each chart-series list found under the keys `APP_CHART_SERIES_Y`,
    `APP_CHART_SERIES_Y1`…`APP_CHART_SERIES_Y6`
    and records per-series diagnostics. For each series the diagnostics include the
    number of raw entries (`raw_count`),
    the sum of successfully parsed numeric values rounded to 5 decimals (`parsed_sum`,
    or `None` if no numeric values),
    and an `items` list describing each element with `index`, `raw`, `raw_type`, and
    `parsed_float`.

    Returns:
        dict[str, Any]: Mapping of chart-series keys to diagnostics objects as described
        above.
        When present in the source, includes top-level `labels` (from
        `APP_CHART_LABELS`) and `request`
        (from `APP_REQUEST_META`) entries.
    """
    if not isinstance(source, dict):
        return {}
    result: dict[str, Any] = {}
    for key in (
        APP_CHART_SERIES_Y,
        APP_CHART_SERIES_Y1,
        APP_CHART_SERIES_Y2,
        APP_CHART_SERIES_Y3,
        APP_CHART_SERIES_Y4,
        APP_CHART_SERIES_Y5,
        APP_CHART_SERIES_Y6,
    ):
        series = source.get(key)
        if not isinstance(series, list):
            continue
        parsed_items: list[dict[str, Any]] = []
        total = 0.0
        found = False
        for index, raw in enumerate(series):
            parsed = safe_float(raw)
            parsed_items.append({
                "index": index,
                "raw": raw,
                "raw_type": type(raw).__name__,
                "parsed_float": parsed,
            })
            if parsed is not None:
                total += parsed
                found = True
        result[key] = {
            "raw_count": len(series),
            "parsed_sum": round(total, 5) if found else None,
            "items": parsed_items,
        }
    if isinstance(source.get(APP_CHART_LABELS), list):
        result["labels"] = source.get(APP_CHART_LABELS)
    if isinstance(source.get(APP_REQUEST_META), dict):
        result["request"] = source.get(APP_REQUEST_META)
    return result


def append_payload_debug_line(
    path: str | Path,
    event: dict[str, Any],
) -> None:
    """Write one mandatorily redacted JSONL event and rotate oversized output.

    Parameters:
        path (str | Path): Path to the JSONL file to append. Parent directories will be
            created if missing.
        event (dict[str, Any]): Event payload to redact and serialize.
    """
    debug_path = Path(path)
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    if debug_path.exists() and debug_path.stat().st_size > PAYLOAD_DEBUG_LOG_MAX_BYTES:
        backup = debug_path.with_name(debug_path.name + PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX)
        with contextlib.suppress(OSError):
            backup.unlink()
        with contextlib.suppress(OSError):
            debug_path.replace(backup)
    redacted = redacted_json_safe_payload(event)
    with debug_path.open("a", encoding="utf-8") as file:
        file.write(
            json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
        )
        file.write("\n")


def safe_bool(
    value: bool | float | str | None,
) -> (
    bool | None
):  # boolean payload value; flat type-dispatch guard chain is clearest as-is
    """Interpret a payload value as a boolean.

    Returns:
        `True` if the value represents a true state, `False` if it represents a false
        state, `None` if the value is `None` or cannot be interpreted.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return int(value) != 0
    if isinstance(value, str):
        val = value.strip().lower()
        if val in {"1", "true", "on", "yes"}:
            return True
        if val in {"0", "false", "off", "no"}:
            return False
    try:
        return int(value) != 0
    except TypeError, ValueError:
        return None


def smart_plug_serial(plug: object) -> str | None:
    """Extract the serial number from a smart-plug subdevice payload.

    Parameters:
        plug (Any): A subdevice payload, expected to be a dict containing one of the
        serial fields.

    Returns:
        serial (str | None): The trimmed serial string from `FIELD_DEVICE_SN`,
        `FIELD_DEV_SN`, or `FIELD_SN`, or `None` if the input is not a dict or no serial
        is present.
    """
    if not isinstance(plug, dict):
        return None
    return first_nonblank_text(
        plug.get(FIELD_DEVICE_SN),
        plug.get(FIELD_DEV_SN),
        plug.get(FIELD_SN),
        plug.get(FIELD_DEVICE_ID),
        plug.get(FIELD_ID),
        plug.get(FIELD_DEV_ID),
    )


def sorted_smart_plugs(plugs: object) -> list[dict[str, Any]]:
    """Return plug entries sorted by their serial numbers.

    Parameters:
        plugs (Any): Iterable expected to be a list of plug payloads; if not a list, an
        empty list is returned.

    Returns:
        list[dict[str, Any]]: The input entries that contain a serial (as determined by
        `smart_plug_serial`), sorted ascending by serial. Entries without a serial are
        omitted.
    """
    if not isinstance(plugs, list):
        return []
    entries: list[tuple[str, dict[str, Any]]] = []
    for entry in plugs:
        sn = smart_plug_serial(entry)
        if sn is None:
            continue
        entries.append((sn, entry))
    entries.sort(key=operator.itemgetter(0))
    return [entry for _, entry in entries]


def meter_head_serial(meter_head: object) -> str | None:
    """Extract the stable identity from a meter-head/collector payload."""
    if not isinstance(meter_head, dict):
        return None
    return first_nonblank_text(
        meter_head.get(FIELD_DEVICE_SN),
        meter_head.get(FIELD_DEV_SN),
        meter_head.get(FIELD_SN),
        meter_head.get(FIELD_DEVICE_ID),
        meter_head.get(FIELD_ID),
        meter_head.get(FIELD_DEV_ID),
    )


def smart_meter_identity(smart_meter: object) -> str | None:
    """Extract a stable serial/id identity from a CT smart-meter payload."""
    if not isinstance(smart_meter, dict):
        return None
    return first_nonblank_text(
        smart_meter.get(FIELD_DEVICE_SN),
        smart_meter.get(FIELD_SN),
        smart_meter.get(FIELD_MAC),
        smart_meter.get(FIELD_DEVICE_ID),
        smart_meter.get(FIELD_ID),
        smart_meter.get(FIELD_DEV_ID),
    )


def sorted_meter_heads(meter_heads: object) -> list[dict[str, Any]]:
    """Return meter-head entries sorted by stable serial/id values."""
    if not isinstance(meter_heads, list):
        return []
    entries: list[tuple[str, dict[str, Any]]] = []
    for entry in meter_heads:
        sn = meter_head_serial(entry)
        if sn is None:
            continue
        entries.append((sn, entry))
    entries.sort(key=operator.itemgetter(0))
    return [entry for _, entry in entries]


def circuit_id(circuit: object) -> str | None:
    """Extract the stable index identity from a circuit/breaker payload.

    Circuit entries come from MQTT ``QueryCircuitProperty`` payloads and are
    identified by their ``idx`` (circuit index). ``idx`` may be ``0``, so the
    presence test uses ``is None`` rather than truthiness.
    """
    if not isinstance(circuit, dict):
        return None
    return first_nonblank_text(
        circuit.get(FIELD_IDX),
        circuit.get(FIELD_ID),
    )


def sorted_circuits(circuits: object) -> list[dict[str, Any]]:
    """Return circuit/breaker entries sorted by stable index identity."""
    if not isinstance(circuits, list):
        return []
    entries: list[tuple[str, dict[str, Any]]] = []
    for entry in circuits:
        cid = circuit_id(entry)
        if cid is None:
            continue
        entries.append((cid, entry))
    entries.sort(key=operator.itemgetter(0))
    return [entry for _, entry in entries]


def sub_device_serial(sub_device: object) -> str | None:
    """Extract the app subdevice serial without substituting a cloud ID."""
    if not isinstance(sub_device, dict):
        return None
    return first_nonblank_text(
        sub_device.get(FIELD_DEVICE_SN),
        sub_device.get(FIELD_DEV_SN),
        sub_device.get(FIELD_SN),
    )


def sorted_sub_devices(sub_devices: object) -> list[dict[str, Any]]:
    """Return subdevice entries sorted by stable serial identity."""
    if not isinstance(sub_devices, list):
        return []
    entries: list[tuple[str, dict[str, Any]]] = []
    for entry in sub_devices:
        sn = sub_device_serial(entry)
        if sn is None:
            continue
        entries.append((sn, entry))
    entries.sort(key=operator.itemgetter(0))
    return [entry for _, entry in entries]


def stable_subdevice_key(prefix: str, identity: str | None, fallback_index: int) -> str:
    """Build a safe stable suffix for a subdevice unique/device id."""
    raw = str(identity or "").strip() or str(fallback_index)
    normalized = _SUBDEVICE_ID_RE.sub("_", raw).strip("_").lower()
    return f"{prefix}_{normalized or fallback_index}"


def nonblank_text(value: object) -> str | None:
    """Return trimmed text for non-empty payload metadata."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def first_nonblank_text(*values: object, fallback: str | None = None) -> str | None:
    """Return the first non-empty metadata field, optionally with fallback."""
    for value in values:
        text = nonblank_text(value)
        if text is not None:
            return text
    return fallback


def jackery_online_state(value: object) -> bool | None:
    """Determine whether a Jackery online/offline marker indicates the device is online.

    Recognizes common string markers for online and offline states; for other types or
    unrecognized strings, falls back to generic boolean parsing.

    Returns:
        True if the marker indicates online, False if it indicates offline, None when
        the value cannot be interpreted.
    """
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"online", "connected", "available"}:
            return True
        if normalized in {"offline", "disconnected", "unavailable"}:
            return False
    if isinstance(value, (bool, int, float, str, type(None))):
        return safe_bool(value)
    return None


# ---------------------------------------------------------------------------
# App trend/statistic chart helpers
# ---------------------------------------------------------------------------
class TrendStatisticPoint(NamedTuple):
    """One app chart bucket converted to a dated statistic point."""

    start_date: date | datetime
    value: float


def statistic_id_part(value: object) -> str:
    """Return a Home-Assistant-safe external statistic id component."""
    text = str(value or "").strip()
    # Replace any non-alnum (except underscore) with underscore
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", text)
    # Collapse multiple underscores
    text = re.sub(r"_+", "_", text)
    # Strip leading/trailing underscores
    text = text.strip("_")
    return text.lower() or "unknown"


def external_trend_statistic_id(
    domain: str,
    device_id: str,
    metric_key: str,
    bucket: str,
) -> str:
    """Construct an external statistic id for importing app chart data.

    Parameters:
        domain (str): Statistic domain (e.g., ``jackery_solarvault``).
        device_id (str): Device identifier to include in the id.
        metric_key (str): Metric key or name to include in the id.
        bucket (str): Bucket suffix (e.g., hour/day/month) to include in the id.

    Returns:
        str: A statistic id in the HA external-statistics form
        ``<domain>:<object_id>`` where ``<object_id>`` joins the normalised
        device id, metric key and bucket. ``async_add_external_statistics``
        rejects ``sensor.*`` entity-style ids (``valid_statistic_id`` requires
        a ``:`` separator), so the Energy Dashboard correlates via the
        ``statistic_id`` suffix rather than an entity domain prefix.
    """
    return (
        f"{statistic_id_part(domain)}:"
        f"{statistic_id_part(device_id)}_"
        f"{statistic_id_part(metric_key)}_"
        f"{statistic_id_part(bucket)}"
    )


def _parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _trend_date_type(section: str, source: dict[str, Any]) -> str | None:
    """Determine the app period date type for a trend section.

    Use an explicit request override when present or infer it from the section suffix.

    Parameters:
        section (str): The chart/section key (e.g., ending with `_day`, `_week`,
        `_month`, or `_year`).
        source (dict[str, Any]): Payload that may include `APP_REQUEST_META` with
        `APP_REQUEST_DATE_TYPE` or `APP_REQUEST_DATE_TYPE_ALT` to explicitly specify the
        date type.

    Returns:
        str | None: One of the `DATE_TYPE_*` suffix values when found, `None` if no date
        type can be determined.
    """
    request = source.get(APP_REQUEST_META)
    if isinstance(request, dict):
        date_type = request.get(APP_REQUEST_DATE_TYPE) or request.get(
            APP_REQUEST_DATE_TYPE_ALT,
        )
        if isinstance(date_type, str):
            return date_type
    for suffix in (DATE_TYPE_DAY, DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR):
        if section.endswith(f"_{suffix}"):
            return suffix
    return None


def is_day_period_payload(source: dict[str, Any], section: str) -> bool:
    """Determine whether the given trend payload corresponds to a day-period request."""
    if section.endswith(f"_{DATE_TYPE_DAY}"):
        return True
    req = source.get(APP_REQUEST_META)
    return isinstance(req, dict) and (
        req.get(APP_REQUEST_STAT_TYPE) == DATE_TYPE_DAY
        or req.get(APP_REQUEST_DATE_TYPE) == DATE_TYPE_DAY
        or req.get(APP_REQUEST_DATE_TYPE_ALT) == DATE_TYPE_DAY
    )


_is_day_period_payload = is_day_period_payload


def _is_non_day_period(section: str) -> bool:
    return section.endswith((
        f"_{DATE_TYPE_WEEK}",
        f"_{DATE_TYPE_MONTH}",
        f"_{DATE_TYPE_YEAR}",
    ))


def is_device_year_period_section(source: dict[str, Any], section: str) -> bool:
    """Determine whether a section represents a device-level "year" period statistic.

    Returns:
        `true` if the section's request dateType is year and the section name starts
        with a device statistic prefix (PV, home, battery, or CT), `false` otherwise.
    """
    return _trend_date_type(section, source) == DATE_TYPE_YEAR and section.startswith((
        APP_SECTION_PV_STAT,
        APP_SECTION_HOME_STAT,
        APP_SECTION_BATTERY_STAT,
        APP_SECTION_CT_STAT,
    ))


def _compact_year_parts(value: object) -> tuple[float, float] | None:
    """Parse one year-chart slot into (previous-month, current-month) parts.

    The device year chart can pack two adjacent *integer* month totals into
    one slot as text: ``"40,96"`` / ``"13.26"`` means previous month ``40``
    (``13``) plus current month ``96`` (``26``) — the text after the
    separator is the current-month integer, NOT a decimal fraction. The
    documented scalar total anchors whether that reading applies (see
    :func:`_disambiguate_year_series`); a plain value stays in its own slot
    with a zero previous part so the expanded series remains complete.

    Returns:
        tuple[float, float] | None: ``(previous_part, current_part)`` or
        ``None`` for missing/boolean/unparsable input.
    """
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float, str))
    ):
        return None
    text = str(value).strip()
    if not text or len(text) > _MAX_COMPACT_YEAR_VALUE_TEXT_LENGTH:
        return None
    sign = 1.0
    if text.startswith("-"):
        sign = -1.0
        text = text[1:]
    whole_text, separator, fraction_text = text.replace(",", ".", 1).partition(".")
    if not whole_text.isdigit():
        return None
    # "13.0" / "13,000" is a plain decimal, not a packed pair.
    packed = bool(separator) and fraction_text.isdigit() and int(fraction_text) != 0
    if packed:
        return sign * float(whole_text), sign * float(fraction_text)
    parsed = safe_float(value)
    if parsed is None or math.isnan(parsed) or math.isinf(parsed):
        return None
    return 0.0, parsed


def _prefer_raw_year_series_for_real_payload(
    section: str,
    raw_values: list[float | None],
    direct_total: float | None,
    tolerance: float,
) -> bool:
    """Keep known app PV year snapshots raw when the scalar total matches exactly."""
    if (
        not section.startswith(APP_SECTION_PV_STAT)
        or direct_total is None
        or len(raw_values) < 12
    ):
        return False
    numeric_values = [value for value in raw_values if value is not None]
    nonzero = [value for value in numeric_values if abs(value) > _NEAR_ZERO_EPSILON]
    return len(nonzero) == 1 and abs(sum(numeric_values) - direct_total) <= tolerance


def expanded_year_series_values(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> list[float | None] | None:
    """Expand compact Jackery device-year chart buckets.

    Produce a full per-bucket monthly value list.

    Parameters:
        source (dict[str, Any]): Payload containing chart series and stat fields.
        section (str): Section key used to locate the trend/chart series.
        stat_key (str): Statistic key whose documented total may anchor expansion.

    Returns:
        list[float | None] | None: Per-bucket numeric values/placeholders when a
        chart series is present.
            - If a documented scalar total (`stat_key`) is present, returns the expanded list only when its sum matches
              the documented total within a small tolerance; otherwise returns the raw
              series values.
            - If the series key is missing or the series is not a list, returns `None`.
    """
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None
    series = source.get(series_key)
    if not isinstance(series, list):
        return None

    raw_values = [
        None if (value := safe_float(item)) is None else round(value, 5)
        for item in series
    ]
    direct_total = safe_float(source.get(stat_key))

    expanded: list[float | None] = [
        None if value is None else 0.0 for value in raw_values
    ]
    has_compact_bucket = False
    for index, raw_value in enumerate(series):
        parts = _compact_year_parts(raw_value)
        if parts is None:
            continue
        previous_value, current_value = parts
        if previous_value:
            has_compact_bucket = True
            target = index - 1 if index > 0 else index
            expanded[target] = (expanded[target] or 0.0) + previous_value
        if current_value:
            expanded[index] = (expanded[index] or 0.0) + current_value

    expanded = [None if value is None else round(value, 5) for value in expanded]

    if direct_total is None:
        return raw_values
    return _disambiguate_year_series(
        section,
        raw_values,
        expanded,
        direct_total,
        has_compact_bucket=has_compact_bucket,
    )


def _disambiguate_year_series(
    section: str,
    raw_values: list[float | None],
    expanded: list[float | None],
    direct_total: float,
    *,
    has_compact_bucket: bool,
) -> list[float | None]:
    """Pick raw vs. compact-expanded year buckets against the documented total.

    When compact encoding is in effect the expanded series is authoritative even
    if the raw series happens to sum to the same documented total -- the raw
    layout misattributes a completed month's energy to the wrong bucket (e.g.
    ``"13.26"`` -> April=13, May=0.26 rather than a lone May=13.26). Known
    single-month PV snapshots stay raw via
    :func:`_prefer_raw_year_series_for_real_payload`.

    Returns:
        list[float | None]: The chosen series (expanded when it reconciles the
        total and compact encoding applies, otherwise raw).
    """
    tolerance = max(0.05, abs(direct_total) * 0.005)
    raw_total = sum(value for value in raw_values if value is not None)
    expanded_total = sum(value for value in expanded if value is not None)
    raw_matches = abs(round(raw_total, 2) - direct_total) <= tolerance
    expanded_matches = abs(round(expanded_total, 2) - direct_total) <= tolerance
    prefer_expanded = (
        has_compact_bucket
        and expanded_matches
        and not _prefer_raw_year_series_for_real_payload(
            section,
            raw_values,
            direct_total,
            tolerance,
        )
    )
    if prefer_expanded:
        return expanded
    if raw_matches:
        return raw_values
    if has_compact_bucket and expanded_matches:
        return expanded
    return raw_values


def effective_trend_series_values(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> list[float | None] | None:
    """Return normalized numeric values for a statistic series.

    Resolve the series for the given section and statistic key.

    For device-year payloads, returns expanded year-series values when expansion is
    applicable; for other payloads, returns a list where numeric entries are
    rounded to 5 decimal places and non-parsable placeholders remain ``None``.

    Parameters:
        source (dict[str, Any]): Payload containing chart series and metadata.
        section (str): Payload section key (e.g., "pv_year", "home_month").
        stat_key (str): Statistic key within the section to locate the chart series.

    Returns:
        list[float | None] | None: Normalized numeric values/placeholders, or
        ``None`` if the chart series key is not applicable or the series value
        is not a list.
    """
    # dateType=day arrays are W power curves (served through
    # day_power_series_key), never energy chart buckets — their energy
    # totals live in the scalar fields.
    if is_day_period_payload(source, section):
        return None
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None
    series = source.get(series_key)
    if not isinstance(series, list):
        return None
    if is_device_year_period_section(source, section):
        return expanded_year_series_values(source, section, stat_key)

    return [
        None if (val := safe_float(raw)) is None else round(val, 5) for raw in series
    ]


def effective_period_total_value(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> float | None:
    """Determine the effective statistic total for an app period.

    Resolve the total within the given period section.

    When the section represents a device year period, uses the section's trend-series
    values (expanded when applicable) and returns their sum rounded to 2 decimals;
    otherwise returns the parsed scalar value found at `stat_key`.

    Returns:
        float: The period total rounded to 2 decimals when available, `None` if no value
        can be determined.
    """
    if is_device_year_period_section(source, section):
        values = effective_trend_series_values(source, section, stat_key)
        if values is not None:
            series_total = round(sum(value for value in values if value is not None), 2)
            direct_total = safe_float(source.get(stat_key))
            if series_total or direct_total is None:
                return series_total
            if direct_total > 0:
                return direct_total
            return series_total
    return safe_float(source.get(stat_key))


def _tolerance_for_values(*values: float | None) -> float:
    """Return a kWh/EUR tolerance large enough for app rounding noise."""
    magnitude = max((abs(value) for value in values if value is not None), default=0.0)
    return max(0.05, magnitude * 0.005)


def _period_section(prefix: str, date_type: str) -> str:
    return f"{prefix}_{date_type}"


def _nonzero_months(values: list[float | None]) -> list[int]:
    """Return one-based month numbers with non-zero app values."""
    return [
        index + 1
        for index, value in enumerate(values[:12])
        if abs(safe_float(value) or 0.0) > _NEAR_ZERO_EPSILON
    ]


def year_payload_appears_current_month_only(
    source: dict[str, Any],
    section: str,
    stat_keys: tuple[str, ...],
    *,
    current_month: int,
) -> bool:
    """Detect the app's year-period month-only bug.

    Check whether a payload contains non-zero values only for the current month.

    Checks series values for the provided `stat_keys` within the given year `section`. Only considers payloads with unit `"kwh"` (or no unit) and requires `current_month` > 1.

    Parameters:
        source (dict[str, Any]): The payload section containing chart series and
        metadata.
        section (str): The year-section key to inspect (e.g., `"pv_stat_year"`).
        stat_keys (tuple[str, ...]): Statistic keys to examine within the section.
        current_month (int): One-based current month index (1-12) used to detect a
        month-only pattern.

    Returns:
        bool: `True` if any inspected series has non-zero values only for
        `current_month`, `False` otherwise.
    """
    if current_month <= 1:
        return False
    if app_energy_unit_scale(source) is None:
        return False
    for stat_key in stat_keys:
        values = effective_trend_series_values(source, section, stat_key)
        if not isinstance(values, list) or len(values) < current_month:
            continue
        nonzero = _nonzero_months(values)
        if not nonzero or set(nonzero).issubset({current_month}):
            return True
    return False


def _month_value(
    month_source: dict[str, Any],
    month_section: str,
    stat_key: str,
) -> float | None:
    value = trend_series_total(month_source, month_section, stat_key)
    if value is not None:
        return value
    return safe_float(month_source.get(stat_key))


def _pv_revenue_value(source: dict[str, Any]) -> float | None:
    revenue = safe_float(source.get(APP_STAT_TOTAL_SOLAR_REVENUE))
    if revenue is not None:
        return revenue
    profit = safe_float(source.get(APP_STAT_PV_PROFIT))
    if profit is None:
        return None
    return round(profit / 10_000_000, 5)


def _period_total_from_payload(
    payload: dict[str, Any],
    section_prefix: str,
    stat_key: str,
) -> float | None:
    section = _period_section(section_prefix, DATE_TYPE_YEAR)
    source = payload.get(section)
    if not isinstance(source, dict):
        return None
    return effective_period_total_value(source, section, stat_key)


def _round_stat_value(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _configured_or_derived_price(
    payload: dict[str, Any],
    *,
    year_generation: float | None,
    year_revenue: float | None,
) -> tuple[float | None, str | None]:
    price_source = payload.get(PAYLOAD_PRICE)
    if isinstance(price_source, dict):
        configured = safe_float(price_source.get(FIELD_SINGLE_PRICE))
        if configured is not None and 0 <= configured <= _MAX_PRICE_PER_KWH:
            return configured, f"{PAYLOAD_PRICE}.{FIELD_SINGLE_PRICE}"

    if year_generation is not None and year_generation > 0 and year_revenue is not None:
        derived = year_revenue / year_generation
        if 0 <= derived <= _MAX_PRICE_PER_KWH:
            return round(derived, 5), "pv_year_revenue_per_kwh"
    return None, None


def _pv_revenue_candidates(
    pv_year: dict[str, Any],
    *,
    year_revenue: float | None,
    raw_generation: float | None,
    price: float | None,
) -> list[float]:
    candidates: list[float] = []
    if year_revenue is not None:
        candidates.append(round(year_revenue, 2))
    if raw_generation is not None and price is not None:
        candidates.append(round(raw_generation * price, 2))

    backfill = pv_year.get(APP_YEAR_BACKFILL_META)
    if isinstance(backfill, dict):
        corrected = backfill.get("corrected")
        if isinstance(corrected, dict):
            revenue_meta = corrected.get("totalSolarRevenue")
            if isinstance(revenue_meta, dict):
                for key in ("raw_total", "corrected_total"):
                    value = safe_float(revenue_meta.get(key))
                    if value is not None:
                        candidates.append(round(value, 2))

    unique: list[float] = []
    for value in candidates:
        if not any(
            abs(value - existing) <= _tolerance_for_values(value, existing)
            for existing in unique
        ):
            unique.append(value)
    return unique


def _matches_pv_revenue_shape(
    raw_revenue: float,
    candidates: list[float],
) -> bool:
    for candidate in candidates:
        tolerance = max(0.5, abs(candidate) * 0.05)
        if abs(raw_revenue - candidate) <= tolerance:
            return True
    return False


def _calculated_savings_from_year(  # ruff: ignore[too-many-locals] - cohesive savings estimate with named totals
    payload: dict[str, Any],
    *,
    year_generation: float | None,
    year_revenue: float | None,
) -> dict[str, Any] | None:
    """Estimate annual PV savings and an energy breakdown.

    Base the result on the app payload and optional year totals.

    Parameters:
        payload (dict): App payload used to derive period totals and chart-series
        values.
        year_generation (float | None): Documented year PV generation in kWh, or None if
        unavailable.
        year_revenue (float | None): Documented year PV revenue (currency units) or None
        if unavailable.

    Returns:
        dict: Mapping with keys:
            - `method` (str): Descriptor of how savings were computed.
            - `calculated_total` (float): Savings monetary total (rounded to 2 decimals).
            - `energy_kwh` (float): Savings energy in kWh (rounded to 2 decimals).
            - `price` (float): Price used per kWh (rounded to 5 decimals).
            - `price_source` (str): Source label for the price (configured or derived).
            - `source_energy` (dict): Rounded kWh diagnostics including `pv_year_kwh`, device grid input/output, home consumption, CT public export, battery charge/discharge, conversion loss, and residual PV not counted as savings.
        None: If required inputs are missing (no usable device/home/CT totals or no
        configured/derivable price).
    """
    device_output = _period_total_from_payload(
        payload,
        APP_SECTION_HOME_STAT,
        APP_STAT_TOTAL_OUT_GRID_ENERGY,
    )
    if device_output is None:
        return None
    device_input = _period_total_from_payload(
        payload,
        APP_SECTION_HOME_STAT,
        APP_STAT_TOTAL_IN_GRID_ENERGY,
    )

    home_consumption = _period_total_from_payload(
        payload,
        APP_SECTION_HOME_TRENDS,
        APP_STAT_TOTAL_HOME_ENERGY,
    )
    public_export = _period_total_from_payload(
        payload,
        APP_SECTION_CT_STAT,
        APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
    )
    public_export_present = public_export is not None
    if home_consumption is None and public_export is None:
        return None

    price, price_source = _configured_or_derived_price(
        payload,
        year_generation=year_generation,
        year_revenue=year_revenue,
    )
    if price is None:
        return None

    delivered_ac = max(0.0, device_output)
    method_prefix = "device_grid_side_output"
    if device_input is not None:
        delivered_ac = max(0.0, delivered_ac - max(0.0, device_input))
        method_prefix = "device_grid_side_net_output"
    net_device_output = delivered_ac
    if public_export is not None:
        delivered_ac = max(0.0, delivered_ac - max(0.0, public_export))
        method_prefix = f"{method_prefix}_minus_ct_export"
    if home_consumption is not None:
        savings_energy = min(max(0.0, home_consumption), delivered_ac)
        method = f"{method_prefix}_bounded_by_home"
    else:
        savings_energy = delivered_ac
        method = method_prefix

    battery_charge = _period_total_from_payload(
        payload,
        APP_SECTION_BATTERY_STAT,
        APP_STAT_TOTAL_CHARGE,
    )
    battery_discharge = _period_total_from_payload(
        payload,
        APP_SECTION_BATTERY_STAT,
        APP_STAT_TOTAL_DISCHARGE,
    )
    battery_gap = None
    if battery_charge is not None and battery_discharge is not None:
        battery_gap = max(0.0, battery_charge - battery_discharge)

    conversion_loss_energy = None
    conversion_loss_energy_signed = None
    if (
        year_generation is not None
        and battery_charge is not None
        and battery_discharge is not None
    ):
        conversion_loss_energy_signed = (
            max(0.0, year_generation)
            + max(0.0, device_input or 0.0)
            + max(0.0, battery_discharge)
            - max(0.0, device_output)
            - max(0.0, battery_charge)
        )
        conversion_loss_energy = max(0.0, conversion_loss_energy_signed)

    pv_residual_after_self_consumption_energy = None
    if year_generation is not None:
        pv_residual_after_self_consumption_energy = max(
            0.0, year_generation - savings_energy
        )

    calculated_total = round(savings_energy * price, 2)
    return {
        "method": method,
        "calculated_total": calculated_total,
        "energy_kwh": round(savings_energy, 2),
        "price": round(price, 5),
        "price_source": price_source,
        "source_energy": {
            "pv_year_kwh": _round_stat_value(year_generation),
            "device_grid_side_input_year_kwh": _round_stat_value(device_input),
            "device_grid_side_output_year_kwh": _round_stat_value(device_output),
            "device_grid_side_net_output_year_kwh": _round_stat_value(
                net_device_output,
            ),
            "savings_basis_ac_year_kwh": _round_stat_value(delivered_ac),
            "home_consumption_year_kwh": _round_stat_value(home_consumption),
            "ct_public_export_year_kwh": _round_stat_value(public_export or 0.0),
            "ct_public_export_year_kwh_present": public_export_present,
            "battery_charge_year_kwh": _round_stat_value(battery_charge),
            "battery_discharge_year_kwh": _round_stat_value(battery_discharge),
            "battery_charge_discharge_gap_kwh": _round_stat_value(battery_gap),
            "conversion_loss_year_kwh": _round_stat_value(conversion_loss_energy),
            "conversion_loss_year_kwh_signed": _round_stat_value(
                conversion_loss_energy_signed,
            ),
            "pv_residual_after_self_consumption_year_kwh": _round_stat_value(
                pv_residual_after_self_consumption_energy,
            ),
            "pv_not_savings_ac_energy_kwh": _round_stat_value(
                pv_residual_after_self_consumption_energy,
            ),
        },
    }


def _savings_publish_decision(
    *,
    raw_revenue: float | None,
    calculated_revenue: float,
    raw_generation: float | None,
    year_generation: float | None,
    pv_revenue_candidates: list[float],
) -> tuple[bool, str]:
    if raw_revenue is None:
        return True, "missing_cloud_total_revenue"

    tolerance = _tolerance_for_values(raw_revenue, calculated_revenue)
    if abs(raw_revenue - calculated_revenue) <= tolerance:
        return True, "cloud_total_matches_calculated_savings"
    if calculated_revenue > raw_revenue + tolerance:
        return True, "cloud_total_below_current_year_savings"

    has_prior_lifetime_generation = (
        raw_generation is not None
        and year_generation is not None
        and raw_generation
        > year_generation + _tolerance_for_values(raw_generation, year_generation)
    )
    if not has_prior_lifetime_generation and _matches_pv_revenue_shape(
        raw_revenue,
        pv_revenue_candidates,
    ):
        return True, "cloud_total_matches_pv_revenue_not_savings"
    if has_prior_lifetime_generation:
        return False, "cloud_total_is_lifetime_higher_than_ytd_calculated"

    return False, "cloud_total_higher_than_current_year_savings"


def _backfill_pv_revenue(
    out: dict[str, Any],
    year_source: dict[str, Any],
    month_sources: dict[int, dict[str, Any]],
    meta: dict[str, Any],
) -> None:
    """Backfill yearly PV revenue fields from monthly values.

    Apply the derived total when it differs from the yearly source.

    Iterates `month_sources` (keys 1-12) to collect per-month PV revenue values, sums
    them, and - if the derived monthly total exceeds the yearly `year_source` total
    beyond the computed tolerance - writes corrected values into `out` and records
    metadata in `meta`.

    Parameters:
        out (dict[str, Any]): Mutable output payload to update with corrected yearly PV
        revenue fields.
        year_source (dict[str, Any]): Original year-level payload used to read the
        existing yearly PV revenue.
        month_sources (dict[int, dict[str, Any]]): Mapping of 1-based month index to
        month payloads used to derive monthly revenue values; months outside 1-12 are
        ignored.
        meta (dict[str, Any]): Mutable metadata dictionary; when a correction is applied, `meta["corrected"]["totalSolarRevenue"]` is set with keys `raw_total`, `corrected_total`, and `months`.

    Side effects:
        - May set `out["totalSolarRevenue"]`, `out["pvProfit"]`, and `out[APP_CHART_SERIES_Y6]`.
        - May add correction details under `meta["corrected"]["totalSolarRevenue"]`.
    """
    revenue_values = [0.0 for _ in range(12)]
    found_months: list[int] = []
    for month, month_source in sorted(month_sources.items()):
        if month < 1 or month > _MONTHS_PER_YEAR:
            continue
        revenue = _pv_revenue_value(month_source)
        if revenue is None:
            continue
        revenue_values[month - 1] = round(revenue, 5)
        found_months.append(month)
    if not found_months:
        return

    monthly_total = round(sum(revenue_values), 2)
    raw_total = _pv_revenue_value(year_source)
    if raw_total is not None and monthly_total <= raw_total + _tolerance_for_values(
        raw_total, monthly_total
    ):
        return

    out["totalSolarRevenue"] = monthly_total
    out["pvProfit"] = round(monthly_total * 10_000_000, 1)
    out[APP_CHART_SERIES_Y6] = [
        round(value * 10_000_000, 1) for value in revenue_values
    ]
    meta.setdefault("corrected", {})["totalSolarRevenue"] = {
        "raw_total": raw_total,
        "corrected_total": monthly_total,
        "months": found_months,
    }


def backfill_year_payload_from_months(  # per-month aggregation dispatch; branch chain mirrors the section shape
    year_source: dict[str, Any],
    section_prefix: str,
    stat_keys: tuple[str, ...],
    month_sources: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Build a corrected year-period payload from monthly payloads.

    Apply corrections when monthly data show incomplete or inconsistent year totals.

    For each requested statistic key this function:
    - Collects up to 12 monthly values from provided month_sources.
    - If at least one month is present and the summed monthly total exceeds the existing year total beyond a tolerance, replaces the year's chart-series and scalar stat with the monthly-derived values and records correction metadata.
    - Adds lightweight aliases for well-known stat keys (PV/in/out/discharge) when corrected.

    Behavior notes:
    - No changes are made and the original `year_source` is returned when `year_source` is not a dict, `month_sources` is empty, the year unit is present and not `"kwh"`, no monthly data is found for any stat_key, or monthly totals do not exceed the documented year total within tolerance.
    - When corrections are applied, the returned payload includes `APP_YEAR_BACKFILL_META` describing the correction method, source/target periods, per-statistic raw and corrected totals, the series key used, and the months found.
    - If the section_prefix indicates PV data, PV revenue backfill is attempted and its results are recorded in the same metadata.

    Parameters:
        year_source: The original year-period payload (expected dictionary shape).
        section_prefix: Prefix identifying the section (e.g., PV/home/battery) used to
        form period keys.
        stat_keys: Tuple of statistic keys to attempt backfill for.
        month_sources: Mapping from 1-12 month index to that month's payload dictionary.

    Returns:
        A dictionary payload: either the unchanged `year_source` or a modified copy with
        corrected series/stat fields and `APP_YEAR_BACKFILL_META` when corrections were
        applied.
    """
    if not isinstance(year_source, dict) or not month_sources:
        return year_source

    year_section = _period_section(section_prefix, DATE_TYPE_YEAR)
    month_section = _period_section(section_prefix, DATE_TYPE_MONTH)
    if app_energy_unit_scale(year_source) is None:
        return year_source

    out = dict(year_source)
    out.setdefault(APP_CHART_LABELS, [str(month) for month in range(1, 13)])
    meta: dict[str, Any] = {
        "method": "same_endpoint_month_sum",
        "source_period": DATE_TYPE_MONTH,
        "target_period": DATE_TYPE_YEAR,
    }

    for stat_key in stat_keys:
        series_key = trend_series_key(year_section, stat_key)
        if not series_key:
            continue

        monthly_values = [0.0 for _ in range(12)]
        found_months: list[int] = []
        for month, month_source in sorted(month_sources.items()):
            if month < 1 or month > _MONTHS_PER_YEAR:
                continue
            value = _month_value(month_source, month_section, stat_key)
            if value is None:
                continue
            monthly_values[month - 1] = round(value, 5)
            found_months.append(month)
        if not found_months:
            continue

        monthly_total = round(sum(monthly_values), 2)
        raw_values = effective_trend_series_values(year_source, year_section, stat_key)
        raw_total = (
            round(sum(value for value in raw_values if value is not None), 2)
            if isinstance(raw_values, list)
            else safe_float(year_source.get(stat_key))
        )
        if raw_total is not None and monthly_total <= raw_total + _tolerance_for_values(
            raw_total, monthly_total
        ):
            continue

        out[series_key] = monthly_values
        out[stat_key] = monthly_total
        if stat_key == APP_STAT_TOTAL_SOLAR_ENERGY:
            out["pvEgy"] = monthly_total
        elif stat_key == APP_STAT_TOTAL_IN_GRID_ENERGY:
            out["inOngridEgy"] = monthly_total
        elif stat_key == APP_STAT_TOTAL_OUT_GRID_ENERGY:
            out["outOngridEgy"] = monthly_total
        elif stat_key == APP_STAT_TOTAL_DISCHARGE:
            out["batOtGridEgy"] = monthly_total

        meta.setdefault("corrected", {})[stat_key] = {
            "raw_total": raw_total,
            "corrected_total": monthly_total,
            "series_key": series_key,
            "months": found_months,
        }

    if section_prefix in {APP_SECTION_PV_STAT, APP_SECTION_PV_TRENDS}:
        _backfill_pv_revenue(out, year_source, month_sources, meta)

    if "corrected" not in meta:
        return year_source
    out[APP_YEAR_BACKFILL_META] = meta
    return out


def apply_year_month_backfill(
    payload: dict[str, Any],
    month_history: dict[str, dict[int, dict[str, Any]]],
) -> None:
    """Backfill year-period payloads from month histories.

    Process the known statistic sections.

    This mutates `payload` in-place, replacing year-section entries with corrected year
    payloads when monthly data are available and a backfill is performed.

    Parameters:
        payload (dict[str, Any]): The full app payload to update; year-section keys (e.g. "<prefix>_year") may be replaced.
        month_history (dict[str, dict[int, dict[str, Any]]]): Mapping from section
        prefix to a mapping of 1-based month index -> month payload dict used to
        reconstruct year-series values.
    """
    section_metrics: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            APP_SECTION_PV_STAT,
            (
                APP_STAT_TOTAL_SOLAR_ENERGY,
                APP_STAT_PV1_ENERGY,
                APP_STAT_PV2_ENERGY,
                APP_STAT_PV3_ENERGY,
                APP_STAT_PV4_ENERGY,
            ),
        ),
        (
            APP_SECTION_HOME_STAT,
            (APP_STAT_TOTAL_IN_GRID_ENERGY, APP_STAT_TOTAL_OUT_GRID_ENERGY),
        ),
        (APP_SECTION_BATTERY_STAT, (APP_STAT_TOTAL_CHARGE, APP_STAT_TOTAL_DISCHARGE)),
        (APP_SECTION_HOME_TRENDS, (APP_STAT_TOTAL_HOME_ENERGY,)),
        (APP_SECTION_PV_TRENDS, (APP_STAT_TOTAL_SOLAR_ENERGY,)),
        (
            APP_SECTION_BATTERY_TRENDS,
            (APP_STAT_TOTAL_TREND_CHARGE_ENERGY, APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY),
        ),
    )

    for section_prefix, stat_keys in section_metrics:
        year_section = _period_section(section_prefix, DATE_TYPE_YEAR)
        year_source = payload.get(year_section)
        months = month_history.get(section_prefix)
        if not isinstance(year_source, dict) or not isinstance(months, dict):
            continue
        payload[year_section] = backfill_year_payload_from_months(
            year_source,
            section_prefix,
            stat_keys,
            months,
        )


def attach_calculated_savings_metadata(payload: dict[str, Any]) -> None:
    """Attach calculated-savings details without changing cloud KPI values.

    The app's ``totalGeneration``, ``totalRevenue`` and ``totalCarbon`` fields
    remain byte-for-byte owned by their HTTP payload. Calculated values are
    published only through the separate savings-detail sensors.
    """
    statistic = payload.get(PAYLOAD_STATISTIC)
    if not isinstance(statistic, dict):
        return
    raw_generation = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
    pv_year = payload.get(_period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR))
    if not isinstance(pv_year, dict):
        return
    year_generation = effective_period_total_value(
        pv_year,
        _period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR),
        APP_STAT_TOTAL_SOLAR_ENERGY,
    )
    year_revenue = _pv_revenue_value(pv_year)
    savings = _calculated_savings_from_year(
        payload,
        year_generation=year_generation,
        year_revenue=year_revenue,
    )
    if savings is None:
        return
    raw_revenue = safe_float(statistic.get(APP_STAT_TOTAL_REVENUE))
    savings.update({
        "raw_cloud_total": raw_revenue,
        "pv_revenue_candidates": _pv_revenue_candidates(
            pv_year,
            year_revenue=year_revenue,
            raw_generation=raw_generation,
            price=safe_float(savings.get("price")),
        ),
        "decision": "calculated_separate_from_cloud_kpi",
        "published_value": safe_float(savings.get("calculated_total")),
        "published_value_source": "calculated_detail_sensor",
    })
    payload[PAYLOAD_STATISTIC] = {
        **statistic,
        APP_SAVINGS_CALC_META: savings,
    }


def guard_statistic_totals_from_year(  # ruff: ignore[too-many-locals] - lifetime guard retains diagnostic operands
    payload: dict[str, Any],
    *,
    previous_statistic: dict[str, Any] | None = None,
) -> None:
    """Preserve non-decreasing lifetime KPIs and attach savings diagnostics.

    The app occasionally returns lifetime ``statistic.totalGeneration`` below the
    current-year PV total. Per the period hierarchy, that smaller lifetime value is
    not allowed to replace a verified longer lower bound. The raw cloud revenue is
    still kept as its own KPI; calculated savings remain metadata/detail-sensor input.
    """
    statistic = payload.get(PAYLOAD_STATISTIC)
    if not isinstance(statistic, dict):
        return

    pv_year_section = _period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR)
    pv_year = payload.get(pv_year_section)
    year_generation = (
        effective_period_total_value(
            pv_year,
            pv_year_section,
            APP_STAT_TOTAL_SOLAR_ENERGY,
        )
        if isinstance(pv_year, dict)
        else None
    )
    year_revenue = _pv_revenue_value(pv_year) if isinstance(pv_year, dict) else None

    raw_generation = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
    previous_generation = (
        safe_float(previous_statistic.get(APP_STAT_TOTAL_GENERATION))
        if isinstance(previous_statistic, dict)
        else None
    )
    candidates = [
        value
        for value in (raw_generation, year_generation, previous_generation)
        if value is not None
    ]
    corrected_generation = max(candidates) if candidates else None
    guard_meta: dict[str, Any] | None = None
    if (
        raw_generation is not None
        and corrected_generation is not None
        and corrected_generation
        > raw_generation + _tolerance_for_values(corrected_generation, raw_generation)
    ):
        statistic = dict(statistic)
        statistic[APP_STAT_TOTAL_GENERATION] = round(corrected_generation, 2)
        method = (
            "previous_total_lower_bound"
            if previous_generation is not None
            and previous_generation
            >= corrected_generation
            - _tolerance_for_values(previous_generation, corrected_generation)
            else "year_total_lower_bound"
        )
        guard_meta = {
            "method": method,
            "corrected": {
                APP_STAT_TOTAL_GENERATION: {
                    "raw_total": round(raw_generation, 2),
                    "corrected_total": round(corrected_generation, 2),
                    "current_year_total": _round_stat_value(year_generation),
                    "previous_total": _round_stat_value(previous_generation),
                }
            },
        }
        statistic["_total_lower_bound_guard"] = guard_meta
        raw_carbon = safe_float(statistic.get(APP_STAT_TOTAL_CARBON))
        if (
            raw_carbon is not None
            and raw_generation > 0
            and raw_carbon >= 0
            and corrected_generation >= 0
        ):
            carbon_factor = raw_carbon / raw_generation
            if 0 <= carbon_factor <= _MAX_CARBON_FACTOR:
                statistic[APP_STAT_TOTAL_CARBON] = round(
                    corrected_generation * carbon_factor,
                    2,
                )
        payload[PAYLOAD_STATISTIC] = statistic
    elif previous_generation is not None and raw_generation is None:
        statistic = dict(statistic)
        statistic[APP_STAT_TOTAL_GENERATION] = round(previous_generation, 2)
        statistic["_total_lower_bound_guard"] = {
            "method": "previous_total_lower_bound",
            "corrected": {
                APP_STAT_TOTAL_GENERATION: {
                    "raw_total": None,
                    "corrected_total": round(previous_generation, 2),
                    "current_year_total": _round_stat_value(year_generation),
                    "previous_total": round(previous_generation, 2),
                }
            },
        }
        payload[PAYLOAD_STATISTIC] = statistic

    statistic = payload.get(PAYLOAD_STATISTIC)
    if not isinstance(statistic, dict):
        return
    savings = _calculated_savings_from_year(
        payload,
        year_generation=year_generation,
        year_revenue=year_revenue,
    )
    if savings is None:
        return
    raw_revenue = safe_float(statistic.get(APP_STAT_TOTAL_REVENUE))
    raw_generation_after_guard = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
    pv_revenue_candidates = (
        _pv_revenue_candidates(
            pv_year,
            year_revenue=year_revenue,
            raw_generation=raw_generation_after_guard,
            price=safe_float(savings.get("price")),
        )
        if isinstance(pv_year, dict)
        else []
    )
    calculated_total = safe_float(savings.get("calculated_total"))
    should_publish, decision = (
        _savings_publish_decision(
            raw_revenue=raw_revenue,
            calculated_revenue=calculated_total,
            raw_generation=raw_generation_after_guard,
            year_generation=year_generation,
            pv_revenue_candidates=pv_revenue_candidates,
        )
        if calculated_total is not None
        else (False, "missing_calculated_savings")
    )
    savings.update({
        "raw_cloud_total": raw_revenue,
        "pv_revenue_candidates": pv_revenue_candidates,
        "would_replace_cloud_total": should_publish,
        "decision": decision,
        "published_value": calculated_total if should_publish else raw_revenue,
        "published_value_source": "calculated_savings"
        if should_publish
        else "cloud_total",
    })
    if guard_meta is not None:
        savings["total_lower_bound_guard"] = guard_meta
    payload[PAYLOAD_STATISTIC] = {
        **statistic,
        APP_SAVINGS_CALC_META: savings,
    }


def compact_json(value: object) -> str:
    """Produce a compact JSON string of the given value suitable for diagnostics.

    Returns:
        compact (str): JSON string with non-ASCII characters preserved and without
        unnecessary whitespace.
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def trend_series_points(  # trend-series parsing dispatches over unit/label/series shapes
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    today: date | None = None,
) -> list[TrendStatisticPoint]:
    """Convert an app chart series into dated TrendStatisticPoint buckets.

    Parameters:
        source (dict): App payload containing chart series and optional request meta.
        section (str): Payload section key used to locate the chart series.
        stat_key (str): Statistic key used to resolve the specific series within the
        section.
        today (date | None): Optional upper bound for returned points; defaults to today
        when None.

    Returns:
        list[TrendStatisticPoint]: Points for each valid series bucket with the bucket
        start date and the value rounded to 5 decimals. Empty list when the series is
        missing, not kWh, out of range, or cannot be mapped to dates.
    """
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return []
    unit_scale = app_energy_unit_scale(source)
    if unit_scale is None:
        return []
    series = effective_trend_series_values(source, section, stat_key)
    if not isinstance(series, list) or not series:
        return []
    series_values = cast("list[Any]", series)
    if not any(
        (numeric := safe_float(value)) is not None and abs(numeric) > _NEAR_ZERO_EPSILON
        for value in series_values
    ):
        # A single all-zero HTTP chart is an unconfirmed no-data shape, not a
        # Recorder series.  Zero buckets remain valid when another bucket in
        # the same chart proves that the period contains real activity.
        return []

    request = source.get(APP_REQUEST_META)
    begin = None
    end = None
    if isinstance(request, dict):
        begin = _parse_iso_date(
            request.get(APP_REQUEST_BEGIN_DATE)
            or request.get(APP_REQUEST_BEGIN_DATE_ALT)
        )
        end = _parse_iso_date(
            request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT)
        )

    date_type = _trend_date_type(section, source)
    if begin is None:
        return []
    if today is None:
        today = datetime.now(UTC).astimezone().date()

    points: list[TrendStatisticPoint] = []
    for index, value in enumerate(series_values):
        if value is None:
            continue
        if date_type == DATE_TYPE_YEAR:
            month = index + 1
            if month < 1 or month > _MONTHS_PER_YEAR:
                continue
            bucket_start = begin.replace(month=month, day=1)
        elif date_type in {DATE_TYPE_WEEK, DATE_TYPE_MONTH}:
            bucket_start = begin + timedelta(days=index)
        else:
            continue

        if (end is not None and bucket_start > end) or bucket_start > today:
            continue
        value_float = safe_float(value)
        if value_float is None:
            continue
        points.append(
            TrendStatisticPoint(bucket_start, round(value_float * unit_scale, 5))
        )
    return points


def _parse_day_chart_minute(value: object) -> int | None:
    """Parse an app day-chart label into minutes after local midnight.

    Parameters:
        value (Any): Label expected as an H:MM-style string (hours and minutes).

    Returns:
        int: Minutes after local midnight for a valid label (0-1439).
        None: If the input is not a valid H:MM label or represents the disallowed
        `24:00` end marker.
    """
    if not isinstance(value, str):
        return None
    match = _DAY_CHART_MINUTE_RE.fullmatch(value)
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour == _HOURS_PER_DAY and minute == 0:
        return None
    if 0 <= hour <= _MAX_HOUR and 0 <= minute <= _MAX_MINUTE:
        return hour * 60 + minute
    return None


def _day_power_sample_minute(
    labels: list[Any] | None,
    index: int,
) -> int | None:
    """Determine the minute of day for a power-curve sample.

    Use an optional label list before falling back to the sample index.

    Parameters:
        labels (list[Any] | None): Optional list of sample labels (e.g., "H:MM"); when present and the label at `index` can be parsed to minutes, that value is used.
        index (int): Zero-based sample index; used as a fallback to compute minute =
        index * 5.

    Returns:
        minute_of_day (int | None): Minutes after local midnight (0-1439) for the
        sample, or `None` if the computed minute is outside the day range or no valid
        label/index mapping exists.
    """
    if labels is not None and index < len(labels):
        minute = _parse_day_chart_minute(labels[index])
        if minute is not None:
            return minute
    minute = index * 5
    return minute if 0 <= minute < 24 * 60 else None


def _day_power_sample_energy_value(
    raw: object,
    section: str,
    stat_key: str,
    series_key: str,
) -> float | None:
    """Return the directional app day-curve sample value to integrate."""
    if raw is None:
        return None
    value = safe_float(raw) if isinstance(raw, (int, float, str)) else None
    if value is None:
        return None
    if section.startswith((APP_SECTION_BATTERY_STAT, APP_SECTION_BATTERY_TRENDS)):
        if stat_key in {
            APP_STAT_TOTAL_CHARGE,
            APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
        }:
            return max(value, 0.0)
        if stat_key in {
            APP_STAT_TOTAL_DISCHARGE,
            APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
        }:
            if series_key == APP_CHART_SERIES_Y1:
                # Some accounts return one combined signed y1 curve: positive
                # samples are charging and negative samples are discharging.
                return abs(value) if value < 0 else 0.0
            # The documented/App-2.4.0 split payload uses y1 for charge and y2
            # for discharge. Those y2 samples are positive magnitudes.
            return max(value, 0.0)
    return max(value, 0.0)


def _reconcile_rounded_day_values(
    rounded_values: list[float], scalar_total: float
) -> list[float]:
    """Reconcile rounded buckets exactly to a non-negative scalar total.

    Parameters:
        rounded_values (list[float]): Per-bucket kWh values already rounded.
        scalar_total (float): Target period total the buckets should sum to.

    Returns:
        list[float]: The reconciled values (a new list; input is not mutated).
    """
    if not rounded_values:
        return rounded_values
    precision = 100_000
    adjusted = [
        max(0, round(bucket_value * precision)) for bucket_value in rounded_values
    ]
    target = max(0, round(scalar_total * precision))
    difference = target - sum(adjusted)
    if difference >= 0:
        target_index = next(
            (idx for idx in range(len(adjusted) - 1, -1, -1) if adjusted[idx] > 0),
            len(adjusted) - 1,
        )
        adjusted[target_index] += difference
    else:
        remaining = -difference
        for idx in range(len(adjusted) - 1, -1, -1):
            removable = min(adjusted[idx], remaining)
            adjusted[idx] -= removable
            remaining -= removable
            if remaining == 0:
                break
    return [round(bucket_value / precision, 5) for bucket_value in adjusted]


def _resolve_day_request_window(
    source: dict[str, Any],
    *,
    today: date | None,
    now: datetime | None,
) -> tuple[date, datetime] | None:
    """Resolve and validate the request day window for a day-power payload.

    Parameters:
        source (dict[str, Any]): App payload containing request meta.
        today (date | None): Reference date for "today" comparisons; defaults to
            the current local date.
        now (datetime | None): Reference time; defaults to current time.

    Returns:
        tuple[date, datetime] | None: The resolved ``(begin, now)`` pair, or
            ``None`` when the request window is missing or out of range.
    """
    request = source.get(APP_REQUEST_META)
    begin = None
    end = None
    if isinstance(request, dict):
        begin = _parse_iso_date(
            request.get(APP_REQUEST_BEGIN_DATE)
            or request.get(APP_REQUEST_BEGIN_DATE_ALT)
        )
        end = _parse_iso_date(
            request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT)
        )

    if begin is None or (end is not None and begin > end):
        return None
    if today is None:
        today = datetime.now(UTC).astimezone().date()
    if begin > today:
        return None
    if now is None:
        # Local wall clock, but timezone-aware: ``now.date()`` must match the
        # local ``today`` computed above, and callers compare it against the
        # app's local ``beginDate``.
        now = datetime.now(UTC).astimezone()
    return begin, now


def day_power_energy_points(  # ruff: ignore[too-many-locals] - cohesive day-curve-to-kWh bucketing pipeline
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    bucket_minutes: int = 60,
    today: date | None = None,
    now: datetime | None = None,
) -> list[TrendStatisticPoint]:
    """Convert a day chart curve into kWh statistic buckets for the requested day.

    Parses a chart-series day curve containing documented five-minute power samples in
    watts or energy samples in kWh and aggregates them into contiguous kWh buckets of
    `bucket_minutes`, optionally constraining to `today`/`now` when the request begins
    today. Watt samples are integrated over their five-minute interval; they are never
    treated as energy or summed as watts. If the payload includes a positive scalar
    period total, real curve buckets are reconciled to that total. A scalar without a
    curve cannot establish when energy occurred and is therefore not imported into
    Recorder.

    Parameters:
        source (dict[str, Any]): App payload containing chart series, optional labels
        and request meta.
        section (str): Payload section key used to resolve series and totals.
        stat_key (str): Statistic key used to locate the scalar period total when
        present.
        bucket_minutes (int): Size of each output bucket in minutes; must evenly divide
        24*60. Defaults to 60.
        today (date | None): Reference date for "today" comparisons; defaults to the current local date.
        now (datetime | None): Reference time for limiting samples when the request
        begins today; defaults to current time.

    Returns:
        list[TrendStatisticPoint]: Ordered list of points where `start_date` is the
        bucket start (local date/time for the request day) and `value` is the bucket kWh
        (rounded to 5 decimal places). Returns an empty list for invalid inputs,
        unsupported units, out-of-range request dates, or when scaling rules prevent
        producing buckets.
    """
    if bucket_minutes <= 0 or 24 * 60 % bucket_minutes != 0:
        return []
    series_key = day_power_series_key(source, section, stat_key)
    if not series_key:
        return []
    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    # App 2.4.x returns dateType=day curves as documented five-minute watt
    # observations. Convert each real sample by duration (W * h / 1000) rather
    # than summing watts. A missing/unknown unit cannot establish the quantity.
    if unit not in {_APP_UNIT_WATT, APP_UNIT_KWH}:
        return []

    window = _resolve_day_request_window(source, today=today, now=now)
    if window is None:
        return []
    begin, now = window

    labels = source.get(APP_CHART_LABELS)
    parsed_labels = labels if isinstance(labels, list) else None
    current_day_limit_minute = (
        now.hour * 60 + now.minute if begin == now.date() else 24 * 60 - 1
    )
    series = source.get(series_key)
    scalar_total = effective_period_total_value(source, section, stat_key)
    if not isinstance(series, list) or not series:
        return []

    buckets: dict[int, float] = {}
    last_bucket_minute: int | None = None
    for index, raw in enumerate(series):
        minute = _day_power_sample_minute(parsed_labels, index)
        if minute is None or minute > current_day_limit_minute:
            continue
        sample_value = _day_power_sample_energy_value(
            raw,
            section,
            stat_key,
            series_key,
        )
        if sample_value is None:
            continue
        sample_kwh = (
            sample_value
            if unit == APP_UNIT_KWH
            else sample_value
            * _DAY_POWER_SAMPLE_MINUTES
            / _MINUTES_PER_HOUR
            / _WATTS_PER_KILOWATT
        )

        bucket_minute = (minute // bucket_minutes) * bucket_minutes
        last_bucket_minute = (
            bucket_minute
            if last_bucket_minute is None
            else max(last_bucket_minute, bucket_minute)
        )
        buckets[bucket_minute] = buckets.get(bucket_minute, 0.0) + sample_kwh

    if last_bucket_minute is None:
        return []

    raw_total = sum(buckets.values())
    reconciled_total = scalar_total
    if reconciled_total is not None and reconciled_total > 0:
        if raw_total > 0:
            scale = reconciled_total / raw_total
            buckets = {minute: value * scale for minute, value in buckets.items()}
        else:
            return []

    bucket_items = sorted(buckets.items())
    rounded_values = [round(max(value, 0.0), 5) for _minute, value in bucket_items]
    if reconciled_total is not None and reconciled_total > 0 and raw_total > 0:
        rounded_values = _reconcile_rounded_day_values(
            rounded_values,
            reconciled_total,
        )

    # Intentionally naive: these are LOCAL wall-clock bucket starts from the
    # app's day curve. ``JackerySolarVaultCoordinator._local_statistic_start``
    # attaches Home Assistant's configured timezone. Adding tzinfo here would
    # shift every bucket by the UTC offset and corrupt long-term statistics.
    return [
        TrendStatisticPoint(
            datetime(begin.year, begin.month, begin.day, minute // 60, minute % 60),
            bucket_value,
        )
        for (minute, _value), bucket_value in zip(
            bucket_items, rounded_values, strict=False
        )
    ]


@dataclass(frozen=True, slots=True)
class AppDataQualityWarning:
    """One non-repairing app-statistics contradiction warning."""

    level: str
    reason: str
    metric_key: str
    label: str
    source_section: str
    source_value: float
    reference_section: str
    reference_value: float
    source_request: dict[str, Any] | None = None
    reference_request: dict[str, Any] | None = None
    source_chart_series_key: str | None = None
    reference_chart_series_key: str | None = None
    total_method: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a compact diagnostics-safe dict without empty optional fields."""
        data: dict[str, Any] = {
            "level": self.level,
            "reason": self.reason,
            "metric_key": self.metric_key,
            "label": self.label,
            "source_section": self.source_section,
            "source_value": self.source_value,
            "reference_section": self.reference_section,
            "reference_value": self.reference_value,
        }
        if self.source_request is not None:
            data["source_request"] = self.source_request
        if self.reference_request is not None:
            data["reference_request"] = self.reference_request
        if self.source_chart_series_key is not None:
            data["source_chart_series_key"] = self.source_chart_series_key
        if self.reference_chart_series_key is not None:
            data["reference_chart_series_key"] = self.reference_chart_series_key
        if self.total_method is not None:
            data["total_method"] = self.total_method
        return data


_DATA_QUALITY_PERIOD_METRICS: Final = (
    (
        "device_ongrid_output_energy",
        "Device grid-side output energy",
        APP_SECTION_HOME_STAT,
        APP_STAT_TOTAL_OUT_GRID_ENERGY,
    ),
    (
        "device_ongrid_input_energy",
        "Device grid-side input energy",
        APP_SECTION_HOME_STAT,
        APP_STAT_TOTAL_IN_GRID_ENERGY,
    ),
    (
        "pv_energy",
        "PV energy",
        APP_SECTION_PV_STAT,
        APP_STAT_TOTAL_SOLAR_ENERGY,
    ),
    (
        "battery_charge_energy",
        "Battery charge energy",
        APP_SECTION_BATTERY_STAT,
        APP_STAT_TOTAL_CHARGE,
    ),
    (
        "battery_discharge_energy",
        "Battery discharge energy",
        APP_SECTION_BATTERY_STAT,
        APP_STAT_TOTAL_DISCHARGE,
    ),
)


def _request_dict(source: dict[str, Any]) -> dict[str, Any] | None:
    request = source.get(APP_REQUEST_META)
    return dict(request) if isinstance(request, dict) else None


def _period_source(
    payload: dict[str, Any],
    section_prefix: str,
    period: str,
) -> tuple[str, dict[str, Any]] | None:
    section = _period_section(section_prefix, period)
    source = payload.get(section)
    if not isinstance(source, dict):
        return None
    return section, source


def _period_warning(
    *,
    reason: str,
    metric_key: str,
    label: str,
    source_section: str,
    source_source: dict[str, Any],
    source_value: float,
    reference_section: str,
    reference_source: dict[str, Any],
    reference_value: float,
    stat_key: str,
) -> AppDataQualityWarning:
    return AppDataQualityWarning(
        level="warning",
        reason=reason,
        metric_key=metric_key,
        label=label,
        source_section=source_section,
        source_value=source_value,
        reference_section=reference_section,
        reference_value=reference_value,
        source_request=_request_dict(source_source),
        reference_request=_request_dict(reference_source),
        source_chart_series_key=trend_series_key(source_section, stat_key),
        reference_chart_series_key=trend_series_key(reference_section, stat_key),
        total_method="chart_series_sum",
    )


def app_data_quality_warnings(
    payload: dict[str, Any],
    *,
    today: date | None = None,
) -> list[AppDataQualityWarning]:
    """Return contradictions without mutating or repairing app statistics."""
    _ = today
    warnings: list[AppDataQualityWarning] = []
    for metric_key, label, section_prefix, stat_key in _DATA_QUALITY_PERIOD_METRICS:
        year = _period_source(payload, section_prefix, DATE_TYPE_YEAR)
        week = _period_source(payload, section_prefix, DATE_TYPE_WEEK)
        if year is not None and week is not None:
            year_section, year_source = year
            week_section, week_source = week
            year_total = effective_period_total_value(
                year_source, year_section, stat_key
            )
            week_total = effective_period_total_value(
                week_source, week_section, stat_key
            )
            if (
                year_total is not None
                and week_total is not None
                and year_total + _NEAR_ZERO_EPSILON < week_total
            ):
                warnings.append(
                    _period_warning(
                        reason="year_less_than_week",
                        metric_key=metric_key,
                        label=label,
                        source_section=year_section,
                        source_source=year_source,
                        source_value=year_total,
                        reference_section=week_section,
                        reference_source=week_source,
                        reference_value=week_total,
                        stat_key=stat_key,
                    )
                )

    statistic = payload.get("statistic")
    pv_year = _period_source(payload, APP_SECTION_PV_STAT, DATE_TYPE_YEAR)
    if isinstance(statistic, dict) and pv_year is not None:
        year_section, year_source = pv_year
        lifetime_total = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
        year_total = effective_period_total_value(
            year_source,
            year_section,
            APP_STAT_TOTAL_SOLAR_ENERGY,
        )
        if (
            lifetime_total is not None
            and year_total is not None
            and lifetime_total + _NEAR_ZERO_EPSILON < year_total
        ):
            warnings.append(
                AppDataQualityWarning(
                    level="warning",
                    reason="lifetime_less_than_year",
                    metric_key="pv_energy",
                    label="PV energy",
                    source_section="statistic",
                    source_value=lifetime_total,
                    reference_section=year_section,
                    reference_value=year_total,
                    reference_request=_request_dict(year_source),
                    reference_chart_series_key=trend_series_key(
                        year_section,
                        APP_STAT_TOTAL_SOLAR_ENERGY,
                    ),
                    total_method="chart_series_sum",
                )
            )
    return warnings


def normalized_data_quality_warnings(
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate and sort warning dictionaries for stable diagnostics."""
    unique: dict[str, dict[str, Any]] = {}
    for warning in warnings:
        key = json.dumps(warning, sort_keys=True, default=str, separators=(",", ":"))
        unique[key] = warning
    return sorted(
        unique.values(),
        key=lambda item: (
            str(item.get("reason") or ""),
            str(item.get("metric_key") or ""),
            str(item.get("source_section") or ""),
            str(item.get("reference_section") or ""),
        ),
    )


def _request_range_text(section: str, request: object) -> str | None:
    if not isinstance(request, dict):
        return None
    date_type = request.get(APP_REQUEST_DATE_TYPE) or request.get(
        APP_REQUEST_DATE_TYPE_ALT
    )
    begin = request.get(APP_REQUEST_BEGIN_DATE) or request.get(
        APP_REQUEST_BEGIN_DATE_ALT
    )
    end = request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT)
    if not date_type or not begin or not end:
        return None
    return f"{section}: {date_type} {begin}..{end}"


def format_data_quality_warning(warning: dict[str, Any]) -> str:
    """Format one data-quality warning for repairs/system-log messages."""
    label = warning.get("label") or warning.get("metric_key") or "App statistic"
    source_section = warning.get("source_section")
    reference_section = warning.get("reference_section")
    source_value = warning.get("source_value")
    reference_value = warning.get("reference_value")
    message = (
        f"{label}: {source_section}={source_value} "
        f"< {reference_section}={reference_value}"
    )
    ranges = [
        value
        for value in (
            _request_range_text(str(source_section), warning.get("source_request")),
            _request_range_text(
                str(reference_section),
                warning.get("reference_request"),
            ),
        )
        if value is not None
    ]
    if ranges:
        message = f"{message} [{"; ".join(ranges)}]"
    return message


# ---------------------------------------------------------------------------
# Power-flow calculation helpers
# ---------------------------------------------------------------------------
def directional_power_value(
    source: dict[str, Any],
    positive_keys: tuple[str, ...],
    negative_keys: tuple[str, ...],
) -> float | None:
    """Compute the net directional power.

    Sum values from positive keys and subtract values from negative keys.

    Parameters:
        source (dict[str, Any]): Mapping containing numeric power values.
        positive_keys (tuple[str, ...]): Keys in `source` whose values contribute
        positively to the net sum.
        negative_keys (tuple[str, ...]): Keys in `source` whose values contribute
        negatively to the net sum.

    Returns:
        float | None: The net power (sum of positive keys minus sum of negative keys) if
        at least one numeric value is present, `None` otherwise.
    """
    positive = 0.0
    negative = 0.0
    found = False

    for key in positive_keys:
        if key in source and source.get(key) is not None:
            value = safe_float(source.get(key))
            if value is not None:
                positive += value
                found = True

    for key in negative_keys:
        if key in source and source.get(key) is not None:
            value = safe_float(source.get(key))
            if value is not None:
                negative += value
                found = True

    return positive - negative if found else None


def signed_phase_power_values(ct: dict[str, Any]) -> list[float] | None:
    """Determine signed power for each CT phase.

    Positive values indicate grid import and negative values indicate export.

    Parameters:
        ct (dict[str, Any]): CT payload mapping containing phase power fields referenced
        by CT_PHASE_POWER_PAIRS.

    Returns:
        list[float] | None: A list of signed per-phase power values in the same order as
        CT_PHASE_POWER_PAIRS, or `None` if any phase value is missing or cannot be
        computed.
    """
    values: list[float] = []
    for pos_key, neg_key in CT_PHASE_POWER_PAIRS:
        value = directional_power_value(ct, (pos_key,), (neg_key,))
        if value is None:
            return None
        values.append(value)
    return values


def smart_meter_net_power(ct: dict[str, Any]) -> float | None:
    """Determine the net grid power from a CT payload.

    Returns:
        float: Net grid power in watts; positive = import, negative = export.
        `None` if no CT-derived power values are available.
    """
    total = directional_power_value(
        ct, (CT_TOTAL_POWER_PAIR[0],), (CT_TOTAL_POWER_PAIR[1],)
    )
    if total is not None:
        return total
    phases = signed_phase_power_values(ct)
    return sum(phases) if phases is not None else None


def calculated_smart_meter_power(  # flat guard chain over CT calculation variants; clearest as-is
    ct: dict[str, Any],
    calculation: str,
) -> float | None:
    """Return a power value derived from CT payloads.

    Apply the requested calculation mode.

    Parameters:
        ct (dict): CT/meter payload used to derive signed net and per-phase power
        values.
        calculation (str): One of: "net_import", "net_export", "gross_import", "gross_export", "gross_flow".
                - "net_import": positive portion of net power (grid import).
                - "net_export": positive portion of negated net power (grid export).
                - "gross_import": sum of positive per-phase powers.
                - "gross_export": sum of per-phase exports (absolute negative phase contributions).
                - "gross_flow": sum of absolute per-phase powers.

    Returns:
        float | None: Calculated power in the same units as the input values, or `None`
        when required inputs are missing or the calculation mode is unrecognized.
    """
    net = smart_meter_net_power(ct)
    phases = signed_phase_power_values(ct)

    if calculation == "net_import":
        return None if net is None else max(net, 0.0)
    if calculation == "net_export":
        return None if net is None else max(-net, 0.0)

    if phases is None:
        return None

    if calculation == "gross_import":
        return sum(max(value, 0.0) for value in phases)
    if calculation == "gross_export":
        return sum(max(-value, 0.0) for value in phases)
    if calculation == "gross_flow":
        return sum(abs(value) for value in phases)
    return None


class HomeConsumptionPower(NamedTuple):
    """Calculated home-load value plus diagnostic components."""

    value: float
    smart_meter_net_power: float | None
    jackery_input_power: float
    jackery_output_power: float
    source: str


def first_power_value(source: dict[str, Any], *keys: str) -> float | None:
    """Get the first numeric power value from the provided keys.

    Check keys in source order.

    Parameters:
        source (dict[str, Any]): Mapping containing candidate power values.
        *keys (str): Keys to check in priority order.

    Returns:
        float | None: The first value successfully coerced to a number, or `None` if no
        numeric value is found.
    """
    for key in keys:
        if key in source and source.get(key) is not None:
            value = safe_float(source.get(key))
            if value is not None:
                return value
    return None


def first_nonzero_power_value(source: dict[str, Any], *keys: str) -> float | None:
    """Return the first non-zero power value, falling back to the first zero."""
    first_zero: float | None = None
    for key in keys:
        if key not in source or source.get(key) is None:
            continue
        value = safe_float(source.get(key))
        if value is None:
            continue
        if value != 0:
            return value
        if first_zero is None:
            first_zero = value
    return first_zero


def jackery_reported_home_load_power(props: dict[str, Any]) -> float | None:
    """Get the Jackery-reported live home/other-load power from device properties.

    Checks the known fields for reported home/other load power and returns the first
    available value.

    Parameters:
        props (dict[str, Any]): Device properties payload to inspect for power fields.

    Returns:
        float | None: The reported power in watts if present and parseable, `None`
        otherwise.
    """
    return first_power_value(props, FIELD_OTHER_LOAD_PW)


def jackery_grid_side_input_power(props: dict[str, Any]) -> float | None:
    """AC input power reported by the Jackery device from the grid/home side.

    Returns:
        float: AC input power in watts, or `None` if no suitable value is present.
    """
    return first_nonzero_power_value(
        props,
        FIELD_IN_GRID_SIDE_PW,
        FIELD_IN_ONGRID_PW,
    )


def jackery_grid_side_output_power(props: dict[str, Any]) -> float | None:
    """Return the AC power Jackery is supplying to the grid/home side.

    Parameters:
        props (dict[str, Any]): Device properties dictionary to read output power fields
        from.

    Returns:
        float: Power in watts if a known output field contains a numeric value, `None`
        otherwise.
    """
    return first_nonzero_power_value(
        props,
        FIELD_OUT_GRID_SIDE_PW,
        FIELD_OUT_ONGRID_PW,
    )


def jackery_inverter_ac_input_power(props: dict[str, Any]) -> float | None:
    """AC power the inverter draws on its AC port (grid/AC charging).

    ``gridInPw`` (SystemBody) / ``inOngridPw`` (HomeBody) are the
    inverter's AC input, NOT the household grid import — the import
    measurement point is ``inGridSidePw``.

    Returns:
        float | None: AC input power in watts, or `None` when neither
        inverter field is present.
    """
    return first_nonzero_power_value(
        props,
        FIELD_GRID_IN_PW,
        FIELD_IN_ONGRID_PW,
    )


def jackery_inverter_ac_output_power(props: dict[str, Any]) -> float | None:
    """Total AC power the inverter delivers (house share + grid export).

    ``gridOutPw`` (SystemBody) / ``outOngridPw`` (HomeBody) per the
    SystemBody wire identity ``otherLoadPw = gridOutPw - outGridSidePw +
    inGridSidePw``; the pure export is ``outGridSidePw``.

    Returns:
        float | None: AC output power in watts, or `None` when neither
        inverter field is present.
    """
    return first_nonzero_power_value(
        props,
        FIELD_GRID_OUT_PW,
        FIELD_OUT_ONGRID_PW,
    )


def jackery_grid_net_power(props: dict[str, Any]) -> int | None:
    """Net grid-side power: positive = import, negative = export.

    Uses only the true grid-side measurement fields ``inGridSidePw`` /
    ``outGridSidePw``. The on-grid family (``gridIn/OutPw``,
    ``in/outOngridPw``) is the inverter's AC input/output (house share +
    export) per the SystemBody wire identity ``otherLoadPw = gridOutPw -
    outGridSidePw + inGridSidePw`` and must never be used as an
    import/export fallback.

    Returns:
        int | None: Net grid power in watts, or `None` when the device
        reports no grid-side measurement point (HomeBody frames).
    """
    in_pw = safe_int(first_power_value(props, FIELD_IN_GRID_SIDE_PW))
    out_pw = safe_int(first_power_value(props, FIELD_OUT_GRID_SIDE_PW))
    if in_pw is None or out_pw is None:
        return None
    return in_pw - out_pw


def jackery_corrected_home_consumption_power(
    ct: dict[str, Any],
    props: dict[str, Any],
) -> HomeConsumptionPower | None:
    """Compute corrected home consumption power and accompanying diagnostic fields.

    If the Jackery device reports an explicit home/other load, that reported value
    (clamped to zero) is used and returned with diagnostic fields. If no reported home
    load is available and either the smart-meter net power is missing or both Jackery
    input and output powers are zero, the function returns `None`. Otherwise the
    function computes `meter_net - jackery_input + jackery_output`, clamps the result to
    zero, and returns it with diagnostic fields and a source identifier.

    Parameters:
        ct (dict[str, Any]): CT/smart-meter payload used to derive smart-meter net
        power.
        props (dict[str, Any]): Jackery device properties payload used to read reported
        home load and grid-side input/output powers.

    Returns:
        HomeConsumptionPower | None: A NamedTuple with fields
            - `value`: corrected home consumption power (kW or W as provided by inputs) clamped to >= 0.0,
            - `smart_meter_net_power`: the smart-meter net power (or `None` if not available),
            - `jackery_input_power`: Jackery grid-side input power,
            - `jackery_output_power`: Jackery grid-side output power,
            - `source`: string indicating which data was used (`FIELD_OTHER_LOAD_PW` when reported, otherwise `"smart_meter_net_minus_input_plus_output"`).
        Returns `None` when insufficient inputs are available to compute a corrected
        consumption.
    """
    meter_net = smart_meter_net_power(ct)
    jackery_input = jackery_grid_side_input_power(props) or 0.0
    jackery_output = jackery_grid_side_output_power(props) or 0.0

    reported_home_load = jackery_reported_home_load_power(props)
    if reported_home_load is not None:
        return HomeConsumptionPower(
            value=max(reported_home_load, 0.0),
            smart_meter_net_power=meter_net,
            jackery_input_power=jackery_input,
            jackery_output_power=jackery_output,
            source=FIELD_OTHER_LOAD_PW,
        )

    if meter_net is None or (not jackery_input and not jackery_output):
        return None

    calculated = meter_net - jackery_input + jackery_output
    return HomeConsumptionPower(
        value=max(calculated, 0.0),
        smart_meter_net_power=meter_net,
        jackery_input_power=jackery_input,
        jackery_output_power=jackery_output,
        source="smart_meter_net_minus_input_plus_output",
    )


# ---------------------------------------------------------------------------
# Trend/statistic helpers
# ---------------------------------------------------------------------------
def _chart_series_key_for_stat(  # exhaustive section/stat → series-key mapping table
    section: str, stat_key: str
) -> str | None:
    """Map an app section and statistic key to the corresponding chart-series key.

    Parameters:
        section (str): App payload section identifier (e.g., PV/home/CT/battery trend or
        stat section).
        stat_key (str): Statistic key within the section.

    Returns:
        str | None: The chart-series key (e.g., `APP_CHART_SERIES_Y`,
        `APP_CHART_SERIES_Y1`, ...) associated with the given section/stat pair, or
        `None` if no mapping exists.
    """
    if section.startswith((APP_SECTION_PV_TRENDS, APP_SECTION_HOME_TRENDS)):
        return APP_CHART_SERIES_Y

    if section.startswith(APP_SECTION_PV_STAT):
        mapping = {
            APP_STAT_TOTAL_SOLAR_ENERGY: APP_CHART_SERIES_Y,
            APP_STAT_PV1_ENERGY: APP_CHART_SERIES_Y1,
            APP_STAT_PV2_ENERGY: APP_CHART_SERIES_Y2,
            APP_STAT_PV3_ENERGY: APP_CHART_SERIES_Y3,
            APP_STAT_PV4_ENERGY: APP_CHART_SERIES_Y4,
        }
        return mapping.get(stat_key)

    if section.startswith(APP_SECTION_HOME_STAT):
        if stat_key == APP_STAT_TOTAL_IN_GRID_ENERGY:
            return APP_CHART_SERIES_Y1
        if stat_key == APP_STAT_TOTAL_OUT_GRID_ENERGY:
            return APP_CHART_SERIES_Y2

    if section.startswith(APP_SECTION_CT_STAT):
        if stat_key == APP_STAT_TOTAL_CT_INPUT_ENERGY:
            return APP_CHART_SERIES_Y1
        if stat_key == APP_STAT_TOTAL_CT_OUTPUT_ENERGY:
            return APP_CHART_SERIES_Y2

    if section.startswith(APP_SECTION_EPS_STAT):
        # ``EpsStatApi.Bean`` (App 2.4.1) fuehrt DREI Serien: ``y``, ``y1`` und
        # ``y2`` — im Gegensatz zu ``CtStatApi.Bean``, das nur ``y1``/``y2`` hat.
        # ``y`` bleibt bewusst unzugeordnet: es gibt weder ein passendes
        # Skalar-Total (nur ``totalInEpsEnergy``/``totalOutEpsEnergy``) noch eine
        # EPS-Chart-Klasse in der App, aus der sich die Bedeutung ableiten
        # liesse. Erst zuordnen, wenn ein Live-Payload sie belegt — nicht raten.
        if stat_key == APP_STAT_TOTAL_IN_EPS_ENERGY:
            return APP_CHART_SERIES_Y1
        if stat_key == APP_STAT_TOTAL_OUT_EPS_ENERGY:
            return APP_CHART_SERIES_Y2

    if section.startswith(APP_SECTION_BATTERY_TRENDS):
        if stat_key == APP_STAT_TOTAL_TREND_CHARGE_ENERGY:
            return APP_CHART_SERIES_Y1
        if stat_key == APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY:
            return APP_CHART_SERIES_Y2

    if section.startswith(APP_SECTION_BATTERY_STAT):
        if stat_key == APP_STAT_TOTAL_CHARGE:
            return APP_CHART_SERIES_Y1
        if stat_key == APP_STAT_TOTAL_DISCHARGE:
            return APP_CHART_SERIES_Y2

    return None


def _series_contains_negative_samples(source: dict[str, Any], series_key: str) -> bool:
    """Return true if an app chart series contains signed negative samples."""
    series = source.get(series_key)
    if not isinstance(series, list):
        return False
    return any((value := safe_float(raw)) is not None and value < 0 for raw in series)


def _series_contains_positive_samples(source: dict[str, Any], series_key: str) -> bool:
    """Return true if an app chart series contains usable positive samples."""
    series = source.get(series_key)
    if not isinstance(series, list):
        return False
    return any((value := safe_float(raw)) is not None and value > 0 for raw in series)


def _series_signed_magnitude(
    source: dict[str, Any],
    series_key: str,
    *,
    negative: bool,
) -> float:
    """Return the total magnitude of samples with the requested sign."""
    series = source.get(series_key)
    if not isinstance(series, list):
        return 0.0
    magnitude = 0.0
    for raw in series:
        value = safe_float(raw)
        if value is None:
            continue
        if negative and value < 0:
            magnitude -= value
        elif not negative and value > 0:
            magnitude += value
    return magnitude


def trend_series_key(section: str, stat_key: str) -> str | None:
    """Map a statistic to its period chart-series key.

    Use the section and statistic key to select the corresponding series.

    Only returns a chart-series key when `section` denotes a day, week, month, or
    year payload; otherwise returns `None`. Day sections map too - the sensor
    layer uses this as its period-sensor dispatch - but their arrays are W power
    curves, so the energy interpretation is blocked downstream
    (:func:`effective_trend_series_values` returns ``None`` for day payloads).

    Returns:
        str: The chart-series key (for example `"y"`, `"y1"`, `"y2"`, etc.), or `None` when the section is not a period payload or no mapping exists.
    """
    if not section.endswith((
        f"_{DATE_TYPE_DAY}",
        f"_{DATE_TYPE_WEEK}",
        f"_{DATE_TYPE_MONTH}",
        f"_{DATE_TYPE_YEAR}",
    )):
        return None
    return _chart_series_key_for_stat(section, stat_key)


def day_power_series_key(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> str | None:
    """Get the chart-series key used for day power curves.

    Require the app payload to represent a day period.

    Returns:
        The chart-series key string for the given `section`/`stat_key` when `source` is
        a day-period payload, `None` otherwise.
    """
    if not is_day_period_payload(source, section):
        return None
    if section.startswith((
        APP_SECTION_BATTERY_STAT,
        APP_SECTION_BATTERY_TRENDS,
    )) and stat_key in {
        APP_STAT_TOTAL_DISCHARGE,
        APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
    }:
        # App payloads occur in both forms: a split positive y2 discharge
        # curve, and a signed y1 curve whose negative samples are discharge.
        # Select the dominant directional magnitude. This retains y2 when y1
        # contains only a small negative noise sample, while preserving the
        # actual discharge timeline when y1 carries the substantive energy.
        signed_y1 = _series_signed_magnitude(
            source,
            APP_CHART_SERIES_Y1,
            negative=True,
        )
        positive_y2 = _series_signed_magnitude(
            source,
            APP_CHART_SERIES_Y2,
            negative=False,
        )
        if signed_y1 > positive_y2:
            return APP_CHART_SERIES_Y1
    return _chart_series_key_for_stat(section, stat_key)


def trend_series_total(  # flat guard chain over series/total shapes; clearest as-is
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> float | None:
    """Compute the period total for a trend/chart statistic section from an app payload.

    For day-period sections the function uses the effective period total derived from
    the payload.
    For non-day sections it requires a mapped chart-series key and that the section unit
    is `kwh`.
    If the chart-series list is missing the function applies guarded fallbacks:
    - For home-stat sections: returns `0.0` when the server total equals `0.0` but grid-related series lists are present.
    - For CT-stat sections: returns the server-reported total when present.

    Returns:
        float: The period total rounded to 2 decimals, or `None` when a reliable total
        cannot be determined.
    """
    if is_day_period_payload(source, section):
        total = effective_period_total_value(source, section, stat_key)
        return round(total, 2) if total is not None else None

    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None

    unit_scale = app_energy_unit_scale(source)
    if unit_scale is None:
        return None

    series = source.get(series_key)
    if not isinstance(series, list):
        server_total = effective_period_total_value(source, section, stat_key)
        if (
            section.startswith(APP_SECTION_HOME_STAT)
            and server_total is not None
            and not server_total
            and any(isinstance(source.get(k), list) for k in APP_HOME_GRID_SERIES_KEYS)
        ):
            return 0.0
        if (
            section.startswith((APP_SECTION_CT_STAT, APP_SECTION_EPS_STAT))
            and server_total is not None
        ):
            return round(server_total * unit_scale, 2)
        return None

    values = effective_trend_series_values(source, section, stat_key) or []
    valid_values = [v for v in values if v is not None]

    if not valid_values:
        server_total = effective_period_total_value(source, section, stat_key)
        if (
            section.startswith((APP_SECTION_CT_STAT, APP_SECTION_EPS_STAT))
            and server_total is not None
        ):
            return round(server_total * unit_scale, 2)
        return None

    return round(sum(valid_values) * unit_scale, 2)


def trend_series_has_value(  # flat guard chain over series/value shapes; clearest as-is
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> bool:
    """Determine whether an app period has a usable numeric value.

    Inspect the specified section and statistic key.

    Considers day-period scalars, chart-series lists for non-day periods (only when the
    unit is kWh or unspecified), and the module's special-case allowances for home and
    CT sections when series data or server totals imply a valid value.

    Returns:
        `true` if a numeric value can be derived from the payload for the section and
        stat_key, `false` otherwise.
    """
    if is_day_period_payload(source, section):
        return safe_float(source.get(stat_key)) is not None

    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return False

    if app_energy_unit_scale(source) is None:
        return False

    series = source.get(series_key)
    if not isinstance(series, list):
        server_total = effective_period_total_value(source, section, stat_key)
        if (
            section.startswith(APP_SECTION_HOME_STAT)
            and server_total is not None
            and not server_total
            and any(isinstance(source.get(k), list) for k in APP_HOME_GRID_SERIES_KEYS)
        ):
            return True
        return bool(
            section.startswith((APP_SECTION_CT_STAT, APP_SECTION_EPS_STAT))
            and server_total is not None
        )

    if any(safe_float(item) is not None for item in series):
        return True

    return bool(
        section.startswith((APP_SECTION_CT_STAT, APP_SECTION_EPS_STAT))
        and safe_float(source.get(stat_key)) is not None
    )


def task_plan_value(
    task_plan: dict[str, Any], *keys: str
) -> str | int | float | bool | None:  # primitive payload value
    """Retrieve the first non-None task-plan value.

    Search for any of the given keys.

    Searches in this order: the top-level of `task_plan`, the `TASK_PLAN_BODY`
    dictionary (if present), then each dictionary item in the `TASK_PLAN_TASKS` list (if
    present). Keys are checked in the order provided and the first non-`None` match is
    returned.

    Parameters:
        task_plan (dict): The task-plan payload to search.
        *keys (str): One or more keys to look up, checked in order.

    Returns:
        Any: The first non-`None` value found for the provided keys, or `None` if none
        are present.
    """
    for key in keys:
        if key in task_plan and task_plan.get(key) is not None:
            return task_plan.get(key)

    body = task_plan.get(TASK_PLAN_BODY)
    if isinstance(body, dict):
        for key in keys:
            if key in body and body.get(key) is not None:
                return body.get(key)

    tasks = task_plan.get(TASK_PLAN_TASKS)
    if isinstance(tasks, list):
        for item in tasks:
            if isinstance(item, dict):
                for key in keys:
                    if key in item and item.get(key) is not None:
                        return item.get(key)
    return None


def trend_payload_has_value(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> bool:
    """Determine whether a trend payload has a usable period value.

    Inspect the provided period sensor data.

    Checks for a computed chart-series total for the given section/statistic and, if
    absent, falls back to the scalar value at `stat_key`.

    Returns:
        True if a usable period value exists, False otherwise.
    """
    if trend_series_total(source, section, stat_key) is not None:
        return True
    return safe_float(source.get(stat_key)) is not None


def first_nonblank(*values: Any) -> str | None:
    """Return the first value that still has content after stripping."""
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def first_nonblank_int(*values: Any) -> int | None:
    """Return the first nonblank value parsed as an integer."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value) if value.is_integer() else None
        text = str(value).strip()
        if not text:
            continue
        if not WHOLE_INT_TEXT_RE.fullmatch(text):
            return None
        whole, _dot, _fraction = text.partition(".")
        try:
            return int(whole)
        except ValueError:
            return None
    return None


def _is_signed_battery_energy_curve(section: str, stat_key: str) -> bool:
    """Return whether a day curve is a signed battery charge/discharge curve."""
    return section.startswith((
        APP_SECTION_BATTERY_STAT,
        APP_SECTION_BATTERY_TRENDS,
    )) and stat_key in {
        APP_STAT_TOTAL_CHARGE,
        APP_STAT_TOTAL_DISCHARGE,
        APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
        APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
    }


def _can_distribute_scalar_day_total(section: str, stat_key: str) -> bool:
    """Return whether an explicit zero day curve may be filled from its total."""
    return section.startswith(APP_SECTION_HOME_STAT) and stat_key in {
        APP_STAT_TOTAL_IN_GRID_ENERGY,
        APP_STAT_TOTAL_OUT_GRID_ENERGY,
    }


def normalize_account(value: str) -> str:
    """Normalize user-facing account identifiers before auth and unique IDs."""
    return value.strip()


def entry_bool_option(entry: Any, key: str, default: bool) -> bool:
    """Return a config-entry boolean option with safe legacy value parsing."""
    return config_entry_bool_option(entry, key, default)
