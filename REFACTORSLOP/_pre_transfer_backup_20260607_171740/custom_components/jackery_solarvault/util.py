"""Shared helpers for Jackery SolarVault entities."""

import calendar
import contextlib
from datetime import UTC, date, datetime, timedelta
import functools
import json
import logging
import math
import operator
import os
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
    APP_CHART_STAT_METRICS,
    APP_HOME_GRID_SERIES_KEYS,
    APP_PERIOD_DATE_TYPES,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_DATE_TYPE_ALT,
    APP_REQUEST_END_DATE,
    APP_REQUEST_END_DATE_ALT,
    APP_REQUEST_META,
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
    APP_TOTAL_GUARD_META,
    APP_UNIT_KWH,
    APP_YEAR_BACKFILL_META,
    CONF_ENABLE_UNREDACTED_DIAGNOSTICS,
    CT_PHASE_POWER_PAIRS,
    CT_TOTAL_POWER_PAIR,
    DATA_QUALITY_KEY_LABEL,
    DATA_QUALITY_KEY_LEVEL,
    DATA_QUALITY_KEY_METRIC_KEY,
    DATA_QUALITY_KEY_REASON,
    DATA_QUALITY_KEY_REFERENCE_CHART_SERIES_KEY,
    DATA_QUALITY_KEY_REFERENCE_REQUEST,
    DATA_QUALITY_KEY_REFERENCE_SECTION,
    DATA_QUALITY_KEY_REFERENCE_VALUE,
    DATA_QUALITY_KEY_SOURCE_CHART_SERIES_KEY,
    DATA_QUALITY_KEY_SOURCE_REQUEST,
    DATA_QUALITY_KEY_SOURCE_SECTION,
    DATA_QUALITY_KEY_SOURCE_VALUE,
    DATA_QUALITY_KEY_TOTAL_METHOD,
    DATA_QUALITY_LEVEL_WARNING,
    DATA_QUALITY_REASON_LIFETIME_LESS_THAN_YEAR,
    DATA_QUALITY_REASON_MONTH_LESS_THAN_WEEK,
    DATA_QUALITY_REASON_WEEK_LESS_THAN_DAY,
    DATA_QUALITY_REASON_YEAR_LESS_THAN_MONTH,
    DATA_QUALITY_REASON_YEAR_LESS_THAN_WEEK,
    DATA_QUALITY_REASON_ZERO_UNCONFIRMED,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DEFAULT_ENABLE_UNREDACTED_DIAGNOSTICS,
    FIELD_CURRENT_VERSION,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEV_ID,
    FIELD_DEV_SN,
    FIELD_GRID_IN_PW,
    FIELD_GRID_OUT_PW,
    FIELD_HOME_LOAD_PW,
    FIELD_ID,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_IN_ONGRID_PW,
    FIELD_LOAD_PW,
    FIELD_OTHER_LOAD_PW,
    FIELD_OUT_GRID_SIDE_PW,
    FIELD_OUT_ONGRID_PW,
    FIELD_SINGLE_PRICE,
    FIELD_SN,
    PAYLOAD_ALARM,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_CT_METER,
    PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX,
    PAYLOAD_DEBUG_LOG_MAX_BYTES,
    PAYLOAD_METER_HEADS,
    PAYLOAD_OTA,
    PAYLOAD_PRICE,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_STATISTIC,
    REDACTED_VALUE,
    REDACT_KEYS,
    SUBDEVICE_SCAN_NAME_LABELS,
    SUBDEVICE_SCAN_NAME_MANUFACTURERS,
    TASK_PLAN_BODY,
    TASK_PLAN_TASKS,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import tzinfo as TZInfo  # noqa: N812

_LOGGER = logging.getLogger(__name__)

# CPU-Optimierung: Regex auf Modulebene kompilieren, nicht pro Schleifendurchlauf
_DAY_CHART_MINUTE_RE = re.compile(r"\s*(\d{1,2}):(\d{2})\s*")
_SUBDEVICE_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")
_DEV_MODE_ENV: str = "JACKERY_DEV_MODE"


def config_entry_bool_option(entry: object, key: str, default: bool) -> bool:
    """Resolve a boolean configuration option, falling back to legacy entry data when options are absent.

    Parameters:
        entry (Any): Config entry-like object with optional `options` and legacy `data` mappings.
        key (str): Option name to look up.
        default (bool): Value to return when the option is not present or cannot be parsed.

    Returns:
        bool: The resolved boolean value (`true` or `false`), or `default` if the value is missing or not parseable.
    """  # noqa: E501, RUF100
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    value = options.get(key)
    if value is None:
        value = data.get(key, default)
    parsed = safe_bool(value)
    return default if parsed is None else parsed


def config_entry_str_option(entry: object, key: str, default: str) -> str:
    """Resolve a string configuration option from a config entry, falling back to legacy entry data and a provided default.

    Looks up `key` first in `entry.options`, then in `entry.data`, and returns the resolved value coerced to `str`. If the resolved value is `None`, returns `default`.

    Parameters:
        entry (Any): Configuration entry object that may have `.options` and `.data` mappings.
        key (str): Option key to look up.
        default (str): Default string to return when the option is not set or resolves to `None`.

    Returns:
        str: The resolved option value coerced to `str`, or `default` when unset.
    """  # noqa: E501, RUF100
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    value = options.get(key)
    if value is None:
        value = data.get(key, default)
    if value is None:
        return default
    return str(value)


def config_entry_int_option(entry: object, key: str, default: int) -> int:
    """Retrieve an integer option from a config entry, falling back to legacy setup data when the option is absent.

    Parameters:
        entry (Any): Config entry-like object with optional `options` and `data` mappings.
        key (str): Option key to read from `entry.options` or `entry.data`.
        default (int): Value to return when the option is missing or cannot be converted to an int.

    Returns:
        int: The resolved integer option or `default` if not present or not convertible.
    """  # noqa: E501, RUF100
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    value = options.get(key)
    if value is None:
        value = data.get(key, default)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
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
    value: Any,  # noqa: ANN401, RUF100
) -> datetime:  # arbitrary payload timestamp, coerced at runtime
    """Parse various timestamp representations and return a timezone-aware UTC datetime.

    Parameters:
        value (Any): A datetime, a numeric timestamp (seconds; milliseconds are accepted and will be converted), or a string containing either a numeric timestamp or an ISO-8601 datetime (trailing "Z" is accepted). Empty strings and unsupported types are rejected.

    Returns:
        datetime: The parsed datetime normalized to UTC with tzinfo set.

    Raises:
        ValueError: If the input is an empty string, an unsupported type, or an invalid timestamp/ISO string.
    """  # noqa: E501, RUF100
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if abs(timestamp) >= 100_000_000_000:  # noqa: PLR2004
            timestamp /= 1000
        try:
            parsed = datetime.fromtimestamp(timestamp, UTC)
        except (OSError, OverflowError, ValueError) as err:
            raise ValueError(f"invalid UTC timestamp: {value!r}") from err  # noqa: TRY003
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError("timestamp must not be empty")  # noqa: TRY003
        with contextlib.suppress(ValueError, OSError, OverflowError):
            timestamp = float(normalized)
            if abs(timestamp) >= 100_000_000_000:  # noqa: PLR2004
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, UTC)
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as err:
            raise ValueError(f"invalid UTC timestamp: {value!r}") from err  # noqa: TRY003
    else:
        raise ValueError(f"unsupported UTC timestamp: {value!r}")  # noqa: TRY003, TRY004

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def coordinator_entity_signature(
    coordinator_data: dict[str, Any] | None,
) -> tuple[Any, ...]:
    """Produce a deterministic, lightweight "shape" signature for coordinator payloads used during entity setup.

    Parameters:
        coordinator_data (dict[str, Any] | None): Mapping of device IDs to their coordinator payloads; may be None.

    Returns:
        tuple[tuple[Any, ...], ...]: A tuple of per-device signature tuples. Each entry preserves the device ID and includes,
        in order: a tuple of smart-plug serials, battery pack count, a tuple of meter-head serials, a boolean indicating presence of an
        alarm payload, a boolean indicating presence of an OTA current version, and a boolean indicating presence of a CT meter.
    """  # noqa: E501, RUF100
    if not coordinator_data:
        return ()
    sig: list[Any] = []
    for dev_id in sorted(coordinator_data):
        payload = coordinator_data.get(dev_id) or {}
        plugs = sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS))
        plug_keys = tuple(smart_plug_serial(p) for p in plugs)
        packs = payload.get(PAYLOAD_BATTERY_PACKS) or []
        pack_count = (
            sum(1 for item in packs if isinstance(item, dict))
            if isinstance(packs, list)
            else 0
        )
        meter_heads = sorted_meter_heads(payload.get(PAYLOAD_METER_HEADS))
        meter_keys = tuple(meter_head_serial(p) for p in meter_heads)

        sig.append((
            dev_id,
            plug_keys,
            pack_count,
            meter_keys,
            payload.get(PAYLOAD_ALARM) is not None,
            bool((payload.get(PAYLOAD_OTA) or {}).get(FIELD_CURRENT_VERSION)),
            payload.get(PAYLOAD_CT_METER) is not None,
        ))
    return tuple(sig)


def append_unique_entity(
    entities: list[Any],
    seen_unique_ids: set[str],
    entity: object,
    *,
    platform: str,
    logger: logging.Logger,
) -> bool:
    """Add the entity to `entities` if its `unique_id` has not been seen; otherwise skip it.

    If the entity has a `unique_id` that already exists in `seen_unique_ids`, the entity is not appended and a debug message is emitted.

    Returns:
        `True` if the entity was appended, `False` if it was skipped due to a duplicate `unique_id`.
    """  # noqa: E501, RUF100
    uid = getattr(entity, "unique_id", None)
    if uid and uid in seen_unique_ids:
        logger.debug("Skip duplicate %s unique_id=%s", platform, uid)
        return False
    if uid:
        seen_unique_ids.add(uid)
    entities.append(entity)
    return True


def validate_app_period_date_type(date_type: str) -> str:
    """Return a supported Jackery app period type or raise ValueError."""
    if date_type not in APP_PERIOD_DATE_TYPES:
        raise ValueError(f"Unsupported Jackery app period dateType: {date_type!r}")  # noqa: TRY003
    return date_type


def app_period_range(date_type: str, *, today: date | None = None) -> tuple[date, date]:
    """Compute the Jackery app's inclusive begin and end dates for the given period type.

    Parameters:
        date_type (str): One of the documented app period types (day, week, month, year).
        today (date | None): Reference date used to compute the period; defaults to the current local date.

    Returns:
        tuple[date, date]: (begin_date, end_date) for the requested period, inclusive.
    """  # noqa: E501, RUF100
    date_type = validate_app_period_date_type(date_type)
    if today is None:
        today = datetime.now(tz=UTC).date()
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


def _app_period_bound_to_date(value: str | date | datetime, *, field_name: str) -> date:
    """Return a validated ISO date bound for a Jackery app period request."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"Jackery app period {field_name} must not be empty")  # noqa: TRY003
    try:
        return date.fromisoformat(normalized)
    except ValueError as err:
        raise ValueError(  # noqa: TRY003
            f"Jackery app period {field_name} must be an ISO date (YYYY-MM-DD): "
            f"{value!r}"
        ) from err


def app_period_date_bounds(
    date_type: str,
    *,
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    today: date | None = None,
) -> tuple[str, str]:
    """Produce ISO-formatted begin and end date strings validated for the specified app period.

    Parameters:
        date_type (str): App period type (must be one of the module's supported date types).
        begin_date (str | date | None): Optional begin bound (ISO date string or date). When None, the period default begin is used.
        end_date (str | date | None): Optional end bound (ISO date string or date). When None, the period default end is used.
        today (date | None): Optional reference date used to compute period defaults when begin/end are omitted.

    Returns:
        tuple[str, str]: A pair of ISO date strings (begin_iso, end_iso).

    Raises:
        ValueError: If inputs are invalid for a date bound or if the resolved begin date is after the resolved end date.
    """  # noqa: E501, RUF100
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
        raise ValueError(  # noqa: TRY003
            "Jackery app period beginDate must be before or equal to endDate: "
            f"{begin.isoformat()} > {end.isoformat()}"
        )
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
    if month < 1 or month > 12:  # noqa: PLR2004
        raise ValueError(f"Unsupported Jackery app month: {month!r}")  # noqa: TRY003
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


def safe_float(  # noqa: PLR0911
    value: Any,  # noqa: ANN401, RUF100
) -> float | None:  # arbitrary payload value, coerced at runtime
    """Parse a payload value into a Python float or return None when it cannot be interpreted.

    Parameters:
        value (Any): Input value from a Jackery payload. Accepts numbers, None, or strings (including numeric strings,
            optionally using a single comma as the decimal separator when no dot is present). Empty or malformed strings
            and unsupported types produce `None`.

    Returns:
        float_value (float | None): The parsed float on success, or `None` if `value` is `None` or cannot be converted.
    """  # noqa: E501, RUF100
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if "," in candidate and "." not in candidate:
            if candidate.count(",") != 1:
                return None
            candidate = candidate.replace(",", ".")
        try:
            return float(candidate)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:  # arbitrary payload value, coerced at runtime  # noqa: ANN401, PLR0911, RUF100
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
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        signless = candidate.removeprefix("-").removeprefix("+")
        if not signless.isdigit():
            return None
        try:
            return int(candidate)
        except ValueError:
            return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def first_nonblank_int(value: object) -> int | None:
    """Return the first integer-parseable, non-blank token of ``value``.

    Accepts a scalar or a whitespace/comma-separated string and returns the
    first token that parses as an integer, or None when the value is
    None/blank/unparseable. Used by the cloud client to coerce loosely typed
    id/code fields that may arrive as zero-padded or whitespace-wrapped strings.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    for token in text.replace(",", " ").split():
        parsed = safe_int(token)
        if parsed is not None:
            return parsed
    return None


@functools.cache
def dev_mode_redactions_disabled() -> bool:
    """Indicates whether developer-mode redactions are disabled based on the JACKERY_DEV_MODE environment variable.

    The result is cached on first call to avoid repeated environment lookups.

    Returns:
        `True` if `JACKERY_DEV_MODE` is set to one of "1", "true", "yes", or "on" (case-insensitive), `False` otherwise.
    """  # noqa: E501, RUF100
    raw = os.environ.get(_DEV_MODE_ENV, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def diagnostic_redactions_disabled(entry: object | None = None) -> bool:
    """Determine whether diagnostic payload redactions are disabled.

    Parameters:
        entry (Any | None): Optional config entry to read the per-entry diagnostics setting. If `None`, only the global dev-mode check is applied.

    Returns:
        bool: `True` if redactions are disabled, `False` otherwise.
    """  # noqa: E501, RUF100
    if dev_mode_redactions_disabled():
        return True
    if entry is None:
        return False
    return config_entry_bool_option(
        entry,
        CONF_ENABLE_UNREDACTED_DIAGNOSTICS,
        DEFAULT_ENABLE_UNREDACTED_DIAGNOSTICS,
    )


def _payload_debug_redacted(
    value: Any,  # noqa: ANN401, RUF100
    redactions_disabled: bool | None = None,
) -> Any:  # recursive JSON walker over arbitrary payload  # noqa: ANN401, RUF100
    """Create a JSON-serializable copy of `value` with sensitive fields redacted.

    When `redactions_disabled` is True (or when omitted and diagnostics redactions are disabled), returns a normalized passthrough of `value`. Otherwise, recursively replaces values for keys listed in `REDACT_KEYS` with `REDACTED_VALUE`, preserves overall structure, and converts tuples to lists so the result is JSON-serializable.

    Parameters:
        value (Any): The input payload to redact.
        redactions_disabled (bool | None): If True, skip redaction and return a normalized passthrough.
            If None, the function checks `diagnostic_redactions_disabled()` to decide.

    Returns:
        Any: A redacted, JSON-serializable representation of `value` (or a normalized passthrough when redactions are disabled).
    """  # noqa: E501, RUF100
    if redactions_disabled is None:
        redactions_disabled = diagnostic_redactions_disabled()
    if redactions_disabled:
        return _payload_debug_passthrough(value)

    if isinstance(value, dict):
        return {
            str(key): REDACTED_VALUE
            if str(key) in REDACT_KEYS
            else _payload_debug_redacted(item, redactions_disabled=redactions_disabled)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _payload_debug_redacted(item, redactions_disabled=redactions_disabled)
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            _payload_debug_redacted(item, redactions_disabled=redactions_disabled)
            for item in value
        ]
    return value


def _payload_debug_passthrough(
    value: Any,  # noqa: ANN401, RUF100
) -> Any:  # recursive JSON walker over arbitrary payload  # noqa: ANN401, RUF100
    """Normalize a nested structure into JSON-serializable types.

    Converts mapping keys to strings and converts tuples to lists while recursively
    processing dicts, lists, and tuples so the resulting structure is safe for JSON
    serialization.

    Parameters:
        value (Any): The input value to normalise; may be a dict, list, tuple, or any
            JSON-serializable leaf.

    Returns:
        Any: The normalised structure with dict keys as `str` and tuples converted to
        `list`, preserving other values unchanged.
    """
    if isinstance(value, dict):
        return {
            str(key): _payload_debug_passthrough(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_payload_debug_passthrough(item) for item in value]
    if isinstance(value, tuple):
        return [_payload_debug_passthrough(item) for item in value]
    return value


def redacted_json_safe_payload(
    value: Any,  # noqa: ANN401, RUF100
) -> Any:  # recursive JSON walker over arbitrary payload  # noqa: ANN401, RUF100
    """Produce a JSON-serializable payload with known sensitive Jackery fields redacted.

    The redaction is applied recursively to nested dicts/lists/tuples while preserving the overall structure and types that are JSON-serializable.

    Returns:
        Any: The input value converted into a JSON-safe structure with sensitive fields replaced by the module's redaction marker.
    """  # noqa: E501, RUF100
    return _payload_debug_redacted(value, redactions_disabled=False)


def active_redact_keys(entry: object | None = None) -> frozenset[str]:
    """Determine which diagnostic keys should be redacted.

    Parameters:
        entry (Any | None): Optional config entry used to evaluate diagnostics redaction settings. When omitted, global/dev-mode settings are used.

    Returns:
        frozenset[str]: An empty set when redactions are disabled, otherwise a frozenset containing the keys that must be redacted (`REDACT_KEYS`).
    """  # noqa: E501, RUF100
    if diagnostic_redactions_disabled(entry):
        return frozenset()
    return frozenset(REDACT_KEYS)


def chart_series_debug(source: object) -> dict[str, Any]:
    """Produce diagnostics for chart-series arrays in an app payload.

    Parses each chart-series list found under the keys `APP_CHART_SERIES_Y`, `APP_CHART_SERIES_Y1`…`APP_CHART_SERIES_Y6`
    and records per-series diagnostics. For each series the diagnostics include the number of raw entries (`raw_count`),
    the sum of successfully parsed numeric values rounded to 5 decimals (`parsed_sum`, or `None` if no numeric values),
    and an `items` list describing each element with `index`, `raw`, `raw_type`, and `parsed_float`.

    Returns:
        dict[str, Any]: Mapping of chart-series keys to diagnostics objects as described above.
        When present in the source, includes top-level `labels` (from `APP_CHART_LABELS`) and `request`
        (from `APP_REQUEST_META`) entries.
    """  # noqa: E501, RUF100
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
    redactions_disabled: bool | None = None,
) -> None:
    """Write a single JSON Lines (JSONL) diagnostic entry for `event`, applying redaction by default, and rotate the target file when it exceeds the configured maximum size.

    Parameters:
        path (str | Path): Path to the JSONL file to append. Parent directories will be created if missing.
        event (dict[str, Any]): Event payload to serialize and write (will be redacted unless redactions are disabled).
        redactions_disabled (bool | None): When `True`, write the event without redaction; when `False`, enforce redaction; when `None`, use the module's default redaction behavior.
    """  # noqa: E501, RUF100
    debug_path = Path(path)
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    if debug_path.exists() and debug_path.stat().st_size > PAYLOAD_DEBUG_LOG_MAX_BYTES:
        backup = debug_path.with_name(debug_path.name + PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX)
        with contextlib.suppress(OSError):
            backup.unlink()
        with contextlib.suppress(OSError):
            debug_path.replace(backup)
    redacted = _payload_debug_redacted(event, redactions_disabled=redactions_disabled)
    with debug_path.open("a", encoding="utf-8") as file:
        file.write(
            json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
        )
        file.write("\n")


def safe_bool(value: Any) -> bool | None:  # arbitrary payload value, coerced at runtime  # noqa: ANN401, PLR0911, RUF100
    """Interpret a payload value as a boolean.

    Returns:
        `True` if the value represents a true state, `False` if it represents a false state, `None` if the value is `None` or cannot be interpreted.
    """  # noqa: E501, RUF100
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        val = value.strip().lower()
        if val in {"1", "true", "on", "yes"}:
            return True
        if val in {"0", "false", "off", "no"}:
            return False
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return None


def smart_plug_serial(plug: object) -> str | None:
    """Extract the stable identity from a smart-plug subdevice payload.

    Parameters:
        plug (Any): A subdevice payload, expected to be a dict containing one of the serial fields.

    Returns:
        serial (str | None): The trimmed value from serial fields, falling back to cloud id fields for Shelly Cloud sockets.
    """  # noqa: E501, RUF100
    if not isinstance(plug, dict):
        return None
    raw = (
        plug.get(FIELD_DEVICE_SN)
        or plug.get(FIELD_DEV_SN)
        or plug.get(FIELD_SN)
        or plug.get(FIELD_DEVICE_ID)
        or plug.get(FIELD_ID)
        or plug.get(FIELD_DEV_ID)
    )
    if raw is None:
        return None
    serial = str(raw).strip()
    return serial or None


def sorted_smart_plugs(plugs: object) -> list[dict[str, Any]]:
    """Return plug entries sorted by stable serial/id values.

    Parameters:
        plugs (Any): Iterable expected to be a list of plug payloads; if not a list, an empty list is returned.

    Returns:
        list[dict[str, Any]]: The input entries that contain a stable identity (as determined by `smart_plug_serial`), sorted ascending by that identity. Entries without an identity are omitted.
    """  # noqa: E501, RUF100
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
    raw = (
        meter_head.get(FIELD_DEVICE_SN)
        or meter_head.get(FIELD_DEV_SN)
        or meter_head.get(FIELD_SN)
        or meter_head.get(FIELD_DEVICE_ID)
        or meter_head.get(FIELD_ID)
        or meter_head.get(FIELD_DEV_ID)
    )
    if raw is None:
        return None
    serial = str(raw).strip()
    return serial or None


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


def stable_subdevice_key(prefix: str, identity: str | None, fallback_index: int) -> str:
    """Build a safe stable suffix for a subdevice unique/device id."""
    raw = str(identity or "").strip() or str(fallback_index)
    normalized = _SUBDEVICE_ID_RE.sub("_", raw).strip("_").lower()
    return f"{prefix}_{normalized or fallback_index}"


def jackery_online_state(value: object) -> bool | None:
    """Determine whether a Jackery online/offline marker indicates the device is online.

    Recognizes common string markers for online and offline states; for other types or unrecognized strings, falls back to generic boolean parsing.

    Returns:
        True if the marker indicates online, False if it indicates offline, None when the value cannot be interpreted.
    """  # noqa: E501, RUF100
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"online", "connected", "available"}:
            return True
        if normalized in {"offline", "disconnected", "unavailable"}:
            return False
    return safe_bool(value)


# ---------------------------------------------------------------------------
# App trend/statistic chart helpers
# ---------------------------------------------------------------------------
class TrendStatisticPoint(NamedTuple):
    """One app chart bucket converted to a dated statistic point."""

    start_date: date | datetime
    value: float


class AppDataQualityWarning(NamedTuple):
    """One non-mutating warning about contradictory app statistics."""

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

    def as_dict(self) -> dict[str, object]:
        """Return a deterministic diagnostics dictionary representing this data-quality warning.

        The mapping always includes the keys:
        - DATA_QUALITY_KEY_LEVEL: warning level
        - DATA_QUALITY_KEY_REASON: human-readable reason code or text
        - DATA_QUALITY_KEY_METRIC_KEY: metric identifier
        - DATA_QUALITY_KEY_LABEL: metric label
        - DATA_QUALITY_KEY_SOURCE_SECTION: source section name
        - DATA_QUALITY_KEY_SOURCE_VALUE: source numeric value (rounded where applicable)
        - DATA_QUALITY_KEY_REFERENCE_SECTION: reference section name
        - DATA_QUALITY_KEY_REFERENCE_VALUE: reference numeric value (rounded where applicable)

        When present on the instance, the mapping also includes:
        - DATA_QUALITY_KEY_SOURCE_REQUEST: a shallow copy of the source request metadata
        - DATA_QUALITY_KEY_REFERENCE_REQUEST: a shallow copy of the reference request metadata
        - DATA_QUALITY_KEY_SOURCE_CHART_SERIES_KEY: the chart-series key used for the source
        - DATA_QUALITY_KEY_TOTAL_METHOD: the method used to derive totals (e.g., "chart_series_sum")

        Returns:
            dict[str, object]: Diagnostic dictionary containing required fields and any available optional fields.
        """  # noqa: E501, RUF100
        payload: dict[str, object] = {
            DATA_QUALITY_KEY_LEVEL: self.level,
            DATA_QUALITY_KEY_REASON: self.reason,
            DATA_QUALITY_KEY_METRIC_KEY: self.metric_key,
            DATA_QUALITY_KEY_LABEL: self.label,
            DATA_QUALITY_KEY_SOURCE_SECTION: self.source_section,
            DATA_QUALITY_KEY_SOURCE_VALUE: self.source_value,
            DATA_QUALITY_KEY_REFERENCE_SECTION: self.reference_section,
            DATA_QUALITY_KEY_REFERENCE_VALUE: self.reference_value,
        }
        if self.source_request is not None:
            payload[DATA_QUALITY_KEY_SOURCE_REQUEST] = dict(self.source_request)
        if self.reference_request is not None:
            payload[DATA_QUALITY_KEY_REFERENCE_REQUEST] = dict(self.reference_request)
        if self.source_chart_series_key is not None:
            payload[DATA_QUALITY_KEY_SOURCE_CHART_SERIES_KEY] = (
                self.source_chart_series_key
            )
        if self.reference_chart_series_key is not None:
            payload[DATA_QUALITY_KEY_REFERENCE_CHART_SERIES_KEY] = (
                self.reference_chart_series_key
            )
        if self.total_method is not None:
            payload[DATA_QUALITY_KEY_TOTAL_METHOD] = self.total_method
        return payload


def normalized_data_quality_warnings(
    warnings: list[Any],
) -> list[dict[str, Any]]:
    """Return deterministic, de-duplicated data-quality warnings."""
    deduped: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        key = (
            str(warning.get(DATA_QUALITY_KEY_REASON) or ""),
            str(warning.get(DATA_QUALITY_KEY_METRIC_KEY) or ""),
            str(warning.get(DATA_QUALITY_KEY_SOURCE_SECTION) or ""),
            str(warning.get(DATA_QUALITY_KEY_SOURCE_VALUE) or ""),
            str(warning.get(DATA_QUALITY_KEY_REFERENCE_SECTION) or ""),
            str(warning.get(DATA_QUALITY_KEY_REFERENCE_VALUE) or ""),
        )
        deduped.setdefault(key, dict(warning))
    return [deduped[key] for key in sorted(deduped)]


def _format_request_range(request: object) -> str | None:
    """Return a compact dateType/range summary for diagnostics messages."""
    if not isinstance(request, dict):
        return None
    date_type = request.get(APP_REQUEST_DATE_TYPE) or request.get(
        APP_REQUEST_DATE_TYPE_ALT
    )
    begin = request.get(APP_REQUEST_BEGIN_DATE) or request.get(
        APP_REQUEST_BEGIN_DATE_ALT
    )
    end = request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT)
    if not date_type and not begin and not end:
        return None
    if begin or end:
        return f"{date_type or 'unknown'} {begin or '?'}..{end or '?'}"
    return str(date_type)


def format_data_quality_warning(warning: dict[str, Any]) -> str:
    """Produce a compact, deterministic diagnostic message for a data-quality warning.

    The returned string has the form:
    "<metric>: <source_section>=<source_value> < <reference_section>=<reference_value>"
    and, when request-range metadata is present, appends:
    " [<source_section>: <source_request>; <reference_section>: <reference_request>]".
    Missing metric/section/value/request fields are rendered as "unknown".

    Parameters:
        warning (dict[str, Any]): Warning mapping that may include keys for
            DATA_QUALITY_KEY_LABEL or DATA_QUALITY_KEY_METRIC_KEY (metric label),
            DATA_QUALITY_KEY_SOURCE_SECTION, DATA_QUALITY_KEY_SOURCE_VALUE,
            DATA_QUALITY_KEY_REFERENCE_SECTION, DATA_QUALITY_KEY_REFERENCE_VALUE,
            DATA_QUALITY_KEY_SOURCE_REQUEST, and DATA_QUALITY_KEY_REFERENCE_REQUEST.

    Returns:
        str: Single-line diagnostic message describing the data-quality discrepancy.
    """
    metric = (
        warning.get(DATA_QUALITY_KEY_LABEL)
        or warning.get(DATA_QUALITY_KEY_METRIC_KEY)
        or "unknown"
    )
    source_section = warning.get(DATA_QUALITY_KEY_SOURCE_SECTION) or "unknown"
    source_value = warning.get(DATA_QUALITY_KEY_SOURCE_VALUE)
    reference_section = warning.get(DATA_QUALITY_KEY_REFERENCE_SECTION) or "unknown"
    reference_value = warning.get(DATA_QUALITY_KEY_REFERENCE_VALUE)
    source_text = "unknown" if source_value is None else str(source_value)
    reference_text = "unknown" if reference_value is None else str(reference_value)

    text = f"{metric}: {source_section}={source_text} < {reference_section}={reference_text}"  # noqa: E501, RUF100
    source_request = _format_request_range(warning.get(DATA_QUALITY_KEY_SOURCE_REQUEST))
    reference_request = _format_request_range(
        warning.get(DATA_QUALITY_KEY_REFERENCE_REQUEST)
    )

    if source_request or reference_request:
        text += f" [{source_section}: {source_request or 'unknown'}; {reference_section}: {reference_request or 'unknown'}]"  # noqa: E501, RUF100
    return text


def verify_and_backfill(  # noqa: PLR0911, PLR0912
    cloud_value: float | None,
    local_value: float | None,
    *,
    label: str = "value",
    tolerance_fraction: float = 0.10,
    on_rejection: Callable[[str], None] | None = None,
) -> float | None:
    """Arbitrate between cloud and local source per AGENTS.md §2.3.

    Rules (cloud is authoritative unless):
    - Both None → None (no data).
    - Cloud is None → local (cloud unavailable).
    - Cloud == 0 and local > 0 → local with warning (cloud boundary reset).
    - |cloud - local| > tolerance_fraction * cloud → min(cloud, local) with warning
      (implausible divergence; conservative choice avoids Energy Dashboard spikes).
    - Otherwise → cloud.

    Parameters:
        cloud_value: Value from the authoritative HTTP cloud endpoint.
        local_value: Value from a local/fallback source (BLE, MQTT, or secondary endpoint).
        label: Human-readable metric name for log messages.
        tolerance_fraction: Relative divergence threshold (default 10 %).
        on_rejection: Optional callback for diagnostics rejection counters.
    """  # noqa: E501, RUF100

    def _record(reason: str) -> None:
        if on_rejection is not None:
            on_rejection(f"{label}:{reason}")

    if cloud_value is None and local_value is None:
        return None
    if cloud_value is None:
        if local_value is None:
            return None
        if math.isnan(local_value) or math.isinf(local_value) or local_value < 0:
            _record("invalid_local")
            _LOGGER.debug(
                "verify_and_backfill %s: rejecting invalid local value %.4f",
                label,
                local_value,
            )
            return None
        return local_value
    if math.isinf(cloud_value):
        _record("invalid_cloud")
        _LOGGER.debug(
            "verify_and_backfill %s: rejecting infinite cloud value %.4f",
            label,
            cloud_value,
        )
        return None
    if math.isnan(cloud_value) or cloud_value < 0:
        if local_value is None:
            _record("invalid_cloud")
            _LOGGER.debug(
                "verify_and_backfill %s: rejecting invalid cloud value %.4f",
                label,
                cloud_value,
            )
            return None
        if math.isnan(local_value) or math.isinf(local_value) or local_value < 0:
            _record("invalid_cloud_and_local")
            _LOGGER.debug(
                "verify_and_backfill %s: rejecting invalid cloud=%.4f local=%.4f",
                label,
                cloud_value,
                local_value,
            )
            return None
        _LOGGER.debug(
            "verify_and_backfill %s: cloud=%.4f invalid; using local=%.4f",
            label,
            cloud_value,
            local_value,
        )
        return local_value
    if local_value is None:
        return cloud_value
    if math.isinf(local_value):
        _record("invalid_local")
        _LOGGER.debug(
            "verify_and_backfill %s: rejecting infinite local value %.4f",
            label,
            local_value,
        )
        return None
    if math.isnan(local_value) or local_value < 0:
        _record("invalid_local")
        _LOGGER.debug(
            "verify_and_backfill %s: ignoring invalid local value %.4f",
            label,
            local_value,
        )
        return cloud_value
    if math.isclose(cloud_value, 0.0) and math.isclose(local_value, 0.0):
        _record("zero_unconfirmed")
        _LOGGER.debug(
            "verify_and_backfill %s: rejecting unconfirmed zero values",
            label,
        )
        return None
    if math.isclose(cloud_value, 0.0) and local_value > 0:
        _record("cloud_zero_local_positive")
        _LOGGER.debug(
            "verify_and_backfill %s: cloud=0 but local=%.4f — "
            "using local (cloud likely in boundary-reset window)",
            label,
            local_value,
        )
        return local_value
    if cloud_value > 0:
        divergence = abs(cloud_value - local_value) / cloud_value
        if divergence > tolerance_fraction:
            chosen = min(cloud_value, local_value)
            _record("divergence")
            _LOGGER.debug(
                "verify_and_backfill %s: cloud=%.4f local=%.4f diverge %.0f%% > %.0f%% — "  # noqa: E501, RUF100
                "using conservative min=%.4f",
                label,
                cloud_value,
                local_value,
                divergence * 100,
                tolerance_fraction * 100,
                chosen,
            )
            return chosen
    return cloud_value


# Earliest local hour at which a zero day-total is considered suspicious.
# Before this, a 0 for "today" is normal (PV makes nothing overnight, little
# has charged since local midnight), so the §2.2 §5 zero-confirmation rule must
# not raise a repair issue — otherwise it fires a false positive every night.
ZERO_CONFIRM_MIN_LOCAL_HOUR: Final = 20


def app_data_quality_warnings(
    payload: dict[str, Any],
    *,
    today: date | None = None,
    now: datetime | None = None,
    tolerance: float = 0.05,
    zero_confirm_min_hour: int = ZERO_CONFIRM_MIN_LOCAL_HOUR,
) -> list[AppDataQualityWarning]:
    """Detect contradictory statistics in an app payload and produce structured warnings.

    Scans documented trend/statistic sections in `payload` for inconsistent totals and returns a list of warnings describing each contradiction. Checked cases include:
    - year total smaller than month or week totals for the same metric,
    - month total smaller than week total when the week lies fully inside the current month,
    - lifetime generation smaller than reported PV year generation.

    Parameters:
        payload (dict[str, Any]): App payload containing trend and statistic sections to inspect.
        today (date | None): Reference date used to determine "current" week/month/year boundaries; defaults to today.
        now (datetime | None): Local wall-clock time used to gate the zero-confirm check on `now.hour`. Must be in the
            HA local timezone (tz-aware) or naive local time — passing a UTC datetime causes the hour comparison to
            use UTC hours instead of local hours, firing the gate at the wrong wall-clock time.
        tolerance (float): Absolute tolerance added to the smaller value when comparing totals; a warning is emitted only when
            left_value + tolerance < right_value.

    Returns:
        list[AppDataQualityWarning]: A list of deterministic warnings (possibly empty). Each warning includes rounded
        source/reference values (5 decimal places) and optional request and chart-series metadata for diagnostics.
    """  # noqa: E501, RUF100
    if today is None:
        today = datetime.now(tz=UTC).date()
    week_begin, week_end = app_period_range(DATE_TYPE_WEEK, today=today)
    week_inside_current_month = (
        week_begin.year == today.year
        and week_end.year == today.year
        and week_begin.month == today.month
        and week_end.month == today.month
    )
    week_inside_current_year = (
        week_begin.year == today.year and week_end.year == today.year
    )

    warnings: list[AppDataQualityWarning] = []

    def _section(prefix: str, date_type: str) -> str:
        return f"{prefix}_{date_type}"

    def _period_total(prefix: str, date_type: str, stat_key: str) -> float | None:
        section = _section(prefix, date_type)
        source = payload.get(section)
        if not isinstance(source, dict):
            return None
        if date_type in {DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR}:
            return trend_series_total(source, section, stat_key)
        return safe_float(source.get(stat_key))

    def _request_for_section(section: str) -> dict[str, Any] | None:
        source = payload.get(section)
        if not isinstance(source, dict):
            return None
        request = source.get(APP_REQUEST_META)
        return dict(request) if isinstance(request, dict) else None

    def _chart_series_key_for_section(section: str, stat_key: str) -> str | None:
        """Return the chart-series key associated with a given payload section and statistic.

        Parameters:
            section (str): Payload section key to inspect.
            stat_key (str): Statistic key within the section whose chart-series key is requested.

        Returns:
            str | None: The chart-series key for the given section and statistic when the section exists and contains a mapping; `None` otherwise.
        """  # noqa: E501, RUF100
        source = payload.get(section)
        return trend_series_key(section, stat_key) if isinstance(source, dict) else None

    def _add_warning(  # noqa: PLR0913
        *,
        reason: str,
        metric_key: str,
        label: str,
        stat_key: str,
        source_section: str,
        source_value: float,
        reference_section: str,
        reference_value: float,
    ) -> None:
        """Create and append an AppDataQualityWarning describing a discrepancy between two period totals.

        Parameters:
                reason (str): Short machine-readable reason code for the warning.
                metric_key (str): Identifier of the metric being compared (used in diagnostics).
                label (str): Human-friendly label for the metric used in formatted messages.
                stat_key (str): Statistic key name used to derive chart-series keys for the affected sections.
                source_section (str): Section name providing the observed (source) total.
                source_value (float): Numeric total reported by the source section (will be rounded for the warning).
                reference_section (str): Section name providing the reference total to compare against.
                reference_value (float): Numeric total reported by the reference section (will be rounded for the warning).

        Side effects:
                Appends a populated AppDataQualityWarning to the module-level `warnings` list. The warning includes chart-series key hints (derived from `stat_key`) and, when the source is not the overall statistic section, marks the total method as `"chart_series_sum"`.
        """  # noqa: E501, RUF100
        warnings.append(
            AppDataQualityWarning(
                level=DATA_QUALITY_LEVEL_WARNING,
                reason=reason,
                metric_key=metric_key,
                label=label,
                source_section=source_section,
                source_value=round(source_value, 5),
                reference_section=reference_section,
                reference_value=round(reference_value, 5),
                source_request=_request_for_section(source_section),
                reference_request=_request_for_section(reference_section),
                source_chart_series_key=_chart_series_key_for_section(
                    source_section, stat_key
                ),
                reference_chart_series_key=_chart_series_key_for_section(
                    reference_section, stat_key
                ),
                total_method="chart_series_sum"
                if source_section != PAYLOAD_STATISTIC
                else None,
            )
        )

    day_in_current_week = True  # today is always within the current week

    for prefix, stat_key, metric_key, label in APP_CHART_STAT_METRICS:
        day = _period_total(prefix, DATE_TYPE_DAY, stat_key)
        week = _period_total(prefix, DATE_TYPE_WEEK, stat_key)
        month = _period_total(prefix, DATE_TYPE_MONTH, stat_key)
        year = _period_total(prefix, DATE_TYPE_YEAR, stat_key)

        if year is not None and month is not None and year + tolerance < month:
            _add_warning(
                reason=DATA_QUALITY_REASON_YEAR_LESS_THAN_MONTH,
                metric_key=metric_key,
                label=label,
                stat_key=stat_key,
                source_section=_section(prefix, DATE_TYPE_YEAR),
                source_value=year,
                reference_section=_section(prefix, DATE_TYPE_MONTH),
                reference_value=month,
            )
        if (
            week_inside_current_year
            and year is not None
            and week is not None
            and year + tolerance < week
        ):
            _add_warning(
                reason=DATA_QUALITY_REASON_YEAR_LESS_THAN_WEEK,
                metric_key=metric_key,
                label=label,
                stat_key=stat_key,
                source_section=_section(prefix, DATE_TYPE_YEAR),
                source_value=year,
                reference_section=_section(prefix, DATE_TYPE_WEEK),
                reference_value=week,
            )
        if (
            week_inside_current_month
            and month is not None
            and week is not None
            and month + tolerance < week
        ):
            _add_warning(
                reason=DATA_QUALITY_REASON_MONTH_LESS_THAN_WEEK,
                metric_key=metric_key,
                label=label,
                stat_key=stat_key,
                source_section=_section(prefix, DATE_TYPE_MONTH),
                source_value=month,
                reference_section=_section(prefix, DATE_TYPE_WEEK),
                reference_value=week,
            )

        # §2.2: day ≤ week — today's value cannot exceed the current week total.
        if (
            day_in_current_week
            and week_inside_current_year
            and week is not None
            and day is not None
            and week + tolerance < day
        ):
            _add_warning(
                reason=DATA_QUALITY_REASON_WEEK_LESS_THAN_DAY,
                metric_key=metric_key,
                label=label,
                stat_key=stat_key,
                source_section=_section(prefix, DATE_TYPE_WEEK),
                source_value=week,
                reference_section=_section(prefix, DATE_TYPE_DAY),
                reference_value=day,
            )

        # §2.2 §5: 0-value confirmation — flag when day=0 but week/month are
        # meaningfully non-zero, indicating a probable cloud boundary-reset zero
        # that has not been confirmed by an adjacent period.
        #
        # Gate on local time: overnight/early-morning a 0 day-total is normal
        # (no PV yet, little charged since midnight), so the cloud returning an
        # empty day-stat then is NOT an inconsistency. Only treat a zero day as
        # suspicious once the local day is advanced enough that real data is
        # expected. When ``now`` is unknown the guard is skipped (legacy/tests).
        zero_confirm_ready = now is None or now.hour >= zero_confirm_min_hour
        if (
            day is not None  # noqa: PLR0916
            and zero_confirm_ready
            and math.isclose(day, 0.0)
            and (
                (week is not None and week > tolerance)
                or (month is not None and month > tolerance)
            )
        ):
            _add_warning(
                reason=DATA_QUALITY_REASON_ZERO_UNCONFIRMED,
                metric_key=metric_key,
                label=label,
                stat_key=stat_key,
                source_section=_section(prefix, DATE_TYPE_DAY),
                source_value=0.0,
                reference_section=_section(
                    prefix,
                    DATE_TYPE_WEEK
                    if week is not None and week > tolerance
                    else DATE_TYPE_MONTH,
                ),
                reference_value=week
                if week is not None and week > tolerance
                else (month or 0.0),
            )

    statistic = payload.get(PAYLOAD_STATISTIC)
    if isinstance(statistic, dict):
        lifetime_generation = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
        year_generation = _period_total(
            APP_SECTION_PV_STAT, DATE_TYPE_YEAR, APP_STAT_TOTAL_SOLAR_ENERGY
        )
        if (
            lifetime_generation is not None
            and year_generation is not None
            and lifetime_generation + tolerance < year_generation
        ):
            _add_warning(
                reason=DATA_QUALITY_REASON_LIFETIME_LESS_THAN_YEAR,
                metric_key="pv_energy",
                label="PV energy",
                stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
                source_section=PAYLOAD_STATISTIC,
                source_value=lifetime_generation,
                reference_section=_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR),
                reference_value=year_generation,
            )

    return warnings


def statistic_id_part(value: object) -> str:
    """Return a Home-Assistant-safe external statistic id component."""
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"


def external_trend_statistic_id(
    domain: str,
    device_id: str,
    metric_key: str,
    bucket: str,
) -> str:
    """Construct an external statistic id for importing app chart data.

    Parameters:
        domain (str): Statistic domain (e.g., sensor domain prefix).
        device_id (str): Device identifier to include in the id.
        metric_key (str): Metric key or name to include in the id.
        bucket (str): Bucket suffix (e.g., hour/day/month) to include in the id.

    Returns:
        str: A statistic id string in the form "<domain>:<device>_<metric>_<bucket>" where each part is normalized by `statistic_id_part`.
    """  # noqa: E501, RUF100
    return (
        f"{domain}:"
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
    """Determine the app period date type for a trend section, using an explicit request override if present or inferring from the section suffix.

    Parameters:
        section (str): The chart/section key (e.g., ending with `_day`, `_week`, `_month`, or `_year`).
        source (dict[str, Any]): Payload that may include `APP_REQUEST_META` with `APP_REQUEST_DATE_TYPE` or `APP_REQUEST_DATE_TYPE_ALT` to explicitly specify the date type.

    Returns:
        str | None: One of the `DATE_TYPE_*` suffix values when found, `None` if no date type can be determined.
    """  # noqa: E501, RUF100
    request = source.get(APP_REQUEST_META)
    if isinstance(request, dict):
        date_type = request.get(APP_REQUEST_DATE_TYPE) or request.get(
            APP_REQUEST_DATE_TYPE_ALT
        )
        if isinstance(date_type, str):
            return date_type
    for suffix in (DATE_TYPE_DAY, DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR):
        if section.endswith(f"_{suffix}"):
            return suffix
    return None


def _is_day_period_payload(source: dict[str, Any], section: str) -> bool:
    """Determine whether the given trend payload corresponds to a day-period request.

    Inspects the section suffix for explicit period markers and, if absent, consults the payload's request metadata to infer the date type.

    Parameters:
        source (dict[str, Any]): The payload or source dictionary containing optional request metadata.
        section (str): The section key to evaluate (e.g., "pv_trend_day", "home_stat_year").

    Returns:
        bool: `True` if the section/request date type is day, `False` otherwise.
    """  # noqa: E501, RUF100
    if section.endswith(f"_{DATE_TYPE_DAY}"):
        return True
    if section.endswith((
        f"_{DATE_TYPE_WEEK}",
        f"_{DATE_TYPE_MONTH}",
        f"_{DATE_TYPE_YEAR}",
    )):
        return False
    return _trend_date_type(section, source) == DATE_TYPE_DAY


def is_device_year_period_section(source: dict[str, Any], section: str) -> bool:
    """Determine whether a section represents a device-level "year" period statistic.

    Returns:
        `true` if the section's request dateType is year and the section name starts with a device statistic or trends prefix, `false` otherwise.
    """  # noqa: E501, RUF100
    return _trend_date_type(section, source) == DATE_TYPE_YEAR and section.startswith((
        APP_SECTION_PV_STAT,
        APP_SECTION_PV_TRENDS,
        APP_SECTION_HOME_STAT,
        APP_SECTION_HOME_TRENDS,
        APP_SECTION_BATTERY_STAT,
        APP_SECTION_BATTERY_TRENDS,
        APP_SECTION_CT_STAT,
    ))


def _compact_year_parts(value: object) -> tuple[float, float] | None:
    """Parse a compact year bucket value into previous- and current-month parts.

    Accepts numeric or string inputs that encode a whole (previous months) and a fractional
    component (current-month share), and returns a tuple of two floats: (previous_part, current_part).
    Returns None for None, boolean, empty, or otherwise unparsable/unsupported values.

    Returns:
        tuple[float, float] | None: `(previous_part, current_part)` when the value can be
        interpreted, or `None` when the input is missing or invalid.
    """  # noqa: E501, RUF100
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None

    sign = -1.0 if text.startswith("-") else 1.0
    unsigned = text.removeprefix("-")
    if "." not in unsigned:
        parsed = safe_float(value)
        return None if parsed is None else (parsed, 0.0)

    whole_text, fraction_text = unsigned.split(".", 1)
    if not whole_text:
        whole_text = "0"
    if not whole_text.isdigit() or not fraction_text.isdigit():
        parsed = safe_float(value)
        return None if parsed is None else (0.0, parsed)

    whole = sign * float(int(whole_text))
    fraction_int = int(fraction_text)
    fraction = sign * (fraction_int / (10 ** len(fraction_text)))

    if fraction == 0.0:  # noqa: RUF069  # fraction is integer-derived (float(int(...))), exact
        return whole, 0.0
    return whole, fraction


def expanded_year_series_values(  # noqa: PLR0911
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> list[float] | None:
    """Expand Jackery device-year compact chart buckets into a full per-bucket (monthly) value list.

    Parameters:
        source (dict[str, Any]): Payload containing chart series and stat fields.
        section (str): Section key used to locate the trend/chart series.
        stat_key (str): Statistic key whose documented total may anchor expansion.

    Returns:
        list[float] | None: A list of per-bucket values (rounded to 5 decimals) when a chart series is present.
            - If a documented scalar total (`stat_key`) is present, returns the expanded list only when its sum matches
              the documented total within a small tolerance; otherwise returns the raw series values.
            - If the series key is missing or the series is not a list, returns `None`.
    """  # noqa: E501, RUF100
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None
    series = source.get(series_key)
    if not isinstance(series, list):
        return None

    raw_values = [round(safe_float(item) or 0.0, 5) for item in series]
    raw_sum = round(sum(raw_values), 2)
    direct_total = safe_float(source.get(stat_key))

    expanded = [0.0 for _ in series]
    compact_encoded = False
    for index, raw_value in enumerate(series):
        parts = _compact_year_parts(raw_value)
        if parts is None:
            continue
        previous_value, current_value = parts
        if previous_value:
            target = index - 1 if index > 0 else index
            expanded[target] += previous_value
            compact_encoded = compact_encoded or (
                isinstance(raw_value, str) and target != index
            )
        if current_value:
            expanded[index] += current_value
            compact_encoded = compact_encoded or isinstance(raw_value, str)

    expanded = [round(value, 5) for value in expanded]
    if compact_encoded and expanded != raw_values:
        return expanded

    if direct_total is not None:
        tolerance = max(0.05, abs(direct_total) * 0.005)
        if abs(raw_sum - direct_total) <= tolerance:
            return raw_values

    if direct_total is not None:
        expanded_sum = round(sum(expanded), 2)
        tolerance = max(0.05, abs(direct_total) * 0.005)
        if abs(expanded_sum - direct_total) <= tolerance:
            return expanded
        return raw_values

    return raw_values


def effective_trend_series_values(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> list[float] | None:
    """Return the normalized series of numeric values for a given section and statistic key.

    For device-year payloads, returns expanded year-series values when expansion is applicable; for other payloads, returns a list where each entry is a float (non-parsable entries become `0.0`) rounded to 5 decimal places.

    Parameters:
        source (dict[str, Any]): Payload containing chart series and metadata.
        section (str): Payload section key (e.g., "pv_year", "home_month").
        stat_key (str): Statistic key within the section to locate the chart series.

    Returns:
        list[float] | None: Normalized list of floats rounded to 5 decimals, or `None` if the chart series key is not applicable or the series value is not a list.
    """  # noqa: E501, RUF100
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None
    series = source.get(series_key)
    if not isinstance(series, list):
        return None
    if is_device_year_period_section(source, section):
        return expanded_year_series_values(source, section, stat_key)

    return [
        0.0 if (val := safe_float(raw)) is None else round(val, 5) for raw in series
    ]


def effective_period_total_value(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> float | None:
    """Determine the effective total for a statistic within the given app period section.

    When the section represents a device year period, uses the section's trend-series values (expanded when applicable) and returns their sum rounded to 2 decimals; otherwise returns the parsed scalar value found at `stat_key`.

    Returns:
        float: The period total rounded to 2 decimals when available, `None` if no value can be determined.
    """  # noqa: E501, RUF100
    if is_device_year_period_section(source, section):
        values = effective_trend_series_values(source, section, stat_key)
        if values is not None:
            if not values:
                return None
            return round(sum(values), 2)
    return safe_float(source.get(stat_key))


def _tolerance_for_values(*values: float | None) -> float:
    """Return a kWh/EUR tolerance large enough for app rounding noise."""
    magnitude = max((abs(value) for value in values if value is not None), default=0.0)
    return max(0.05, magnitude * 0.005)


def _period_section(prefix: str, date_type: str) -> str:
    return f"{prefix}_{date_type}"


def _nonzero_months(values: list[float]) -> list[int]:
    """Return one-based month numbers with non-zero app values."""
    return [
        index + 1
        for index, value in enumerate(values[:12])
        if abs(safe_float(value) or 0.0) > 0.00001  # noqa: PLR2004
    ]


def year_payload_appears_current_month_only(
    source: dict[str, Any],
    section: str,
    stat_keys: tuple[str, ...],
    *,
    current_month: int,
) -> bool:
    """Detect whether a year-period payload contains non-zero values only for the given current month (the app's month-only bug).

    Checks series values for the provided `stat_keys` within the given year `section`. Only considers payloads with unit `"kwh"` (or no unit) and requires `current_month` > 1.

    Parameters:
        source (dict[str, Any]): The payload section containing chart series and metadata.
        section (str): The year-section key to inspect (e.g., `"pv_stat_year"`).
        stat_keys (tuple[str, ...]): Statistic keys to examine within the section.
        current_month (int): One-based current month index (1-12) used to detect a month-only pattern.

    Returns:
        bool: `True` if any inspected series has non-zero values only for `current_month`, `False` otherwise.
    """  # noqa: E501, RUF100
    if current_month <= 1:
        return False
    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if unit and unit != APP_UNIT_KWH:
        return False
    for stat_key in stat_keys:
        values = effective_trend_series_values(source, section, stat_key)
        if not isinstance(values, list) or len(values) < current_month:
            continue
        nonzero = _nonzero_months(values)
        if nonzero and set(nonzero).issubset({current_month}):
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
        if configured is not None and 0 <= configured <= 10:  # noqa: PLR2004
            return configured, f"{PAYLOAD_PRICE}.{FIELD_SINGLE_PRICE}"

    if year_generation is not None and year_generation > 0 and year_revenue is not None:
        derived = year_revenue / year_generation
        if 0 <= derived <= 10:  # noqa: PLR2004
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
            revenue_meta = corrected.get(APP_STAT_TOTAL_SOLAR_REVENUE)
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


def _calculated_savings_from_year(  # noqa: PLR0914
    payload: dict[str, Any],
    *,
    year_generation: float | None,
    year_revenue: float | None,
) -> dict[str, Any] | None:
    """Estimate annual PV savings (money) and a rounded energy breakdown based on the provided app payload and optional year totals.

    Parameters:
        payload (dict): App payload used to derive period totals and chart-series values.
        year_generation (float | None): Documented year PV generation in kWh, or None if unavailable.
        year_revenue (float | None): Documented year PV revenue (currency units) or None if unavailable.

    Returns:
        dict: Mapping with keys:
            - `method` (str): Descriptor of how savings were computed.
            - `calculated_total` (float): Savings monetary total (rounded to 2 decimals).
            - `energy_kwh` (float): Savings energy in kWh (rounded to 2 decimals).
            - `price` (float): Price used per kWh (rounded to 5 decimals).
            - `price_source` (str): Source label for the price (configured or derived).
            - `source_energy` (dict): Rounded kWh diagnostics including `pv_year_kwh`, device grid input/output, home consumption, CT public export, battery charge/discharge, conversion loss, and residual PV not counted as savings.
        None: If required inputs are missing (no usable device/home/CT totals or no configured/derivable price).
    """  # noqa: E501, RUF100
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
                net_device_output
            ),
            "savings_basis_ac_year_kwh": _round_stat_value(delivered_ac),
            "home_consumption_year_kwh": _round_stat_value(home_consumption),
            "ct_public_export_year_kwh": _round_stat_value(public_export),
            "battery_charge_year_kwh": _round_stat_value(battery_charge),
            "battery_discharge_year_kwh": _round_stat_value(battery_discharge),
            "battery_charge_discharge_gap_kwh": _round_stat_value(battery_gap),
            "conversion_loss_year_kwh": _round_stat_value(conversion_loss_energy),
            "conversion_loss_year_kwh_signed": _round_stat_value(
                conversion_loss_energy_signed
            ),
            "pv_residual_after_self_consumption_year_kwh": _round_stat_value(
                pv_residual_after_self_consumption_energy
            ),
            "pv_not_savings_ac_energy_kwh": _round_stat_value(
                pv_residual_after_self_consumption_energy
            ),
        },
    }


def _savings_publish_decision(  # noqa: PLR0911, PLR0913
    *,
    raw_revenue: float | None,
    calculated_revenue: float,
    raw_generation: float | None,
    year_generation: float | None,
    year_revenue: float | None,
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
    # Shape-match: only check raw_revenue against year_revenue (the YTD
    # PV-revenue from pv_stat_year.totalSolarRevenue). If they match, the
    # cloud is reporting the current-year PV income instead of cumulative
    # savings, so the calculated savings figure is more accurate.
    # Do NOT match against the derived raw_generation * price candidate —
    # that coincides with the cloud value whenever the cloud correctly
    # aggregates lifetime revenue at the current tariff.
    ytd_only_candidates = [year_revenue] if year_revenue is not None else []
    if ytd_only_candidates and _matches_pv_revenue_shape(
        raw_revenue, ytd_only_candidates
    ):
        if has_prior_lifetime_generation:
            return (
                True,
                "cloud_total_matches_pv_revenue_shape_despite_lifetime_generation",
            )
        return True, "cloud_total_matches_pv_revenue_not_savings"

    if has_prior_lifetime_generation:
        return False, "cloud_total_is_lifetime_higher_than_ytd_calculated"

    # Full-candidate shape-match for non-lifetime cloud totals
    if _matches_pv_revenue_shape(raw_revenue, pv_revenue_candidates):
        return True, "cloud_total_matches_pv_revenue_not_savings"

    return False, "cloud_total_higher_than_current_year_savings"


def _backfill_pv_revenue(
    out: dict[str, Any],
    year_source: dict[str, Any],
    month_sources: dict[int, dict[str, Any]],
    meta: dict[str, Any],
) -> None:
    """Backfills yearly PV revenue fields in `out` using monthly revenue values when the monthly-derived total differs from the yearly source.

    Iterates `month_sources` (keys 1-12) to collect per-month PV revenue values, sums them, and — if the derived monthly total exceeds the yearly `year_source` total beyond the computed tolerance — writes corrected values into `out` and records metadata in `meta`.

    Parameters:
        out (dict[str, Any]): Mutable output payload to update with corrected yearly PV revenue fields.
        year_source (dict[str, Any]): Original year-level payload used to read the existing yearly PV revenue.
        month_sources (dict[int, dict[str, Any]]): Mapping of 1-based month index to month payloads used to derive monthly revenue values; months outside 1-12 are ignored.
        meta (dict[str, Any]): Mutable metadata dictionary; when a correction is applied, `meta["corrected"]["totalSolarRevenue"]` is set with keys `raw_total`, `corrected_total`, and `months`.

    Side effects:
        - May set `out[APP_STAT_TOTAL_SOLAR_REVENUE]`, `out[APP_STAT_PV_PROFIT]`, and `out[APP_CHART_SERIES_Y6]`.
        - May add correction details under `meta["corrected"]["totalSolarRevenue"]`.
    """  # noqa: E501, RUF100
    revenue_values = [0.0 for _ in range(12)]
    found_months: list[int] = []
    for month, month_source in sorted(month_sources.items()):
        if month < 1 or month > 12:  # noqa: PLR2004
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

    out[APP_STAT_TOTAL_SOLAR_REVENUE] = monthly_total
    out[APP_STAT_PV_PROFIT] = round(monthly_total * 10_000_000, 1)
    # y6 stores per-month revenue in the payload's declared currency unit (EUR/USD).
    # pvProfit keeps the 1e7-scaled wire value for protocol compatibility, but y6
    # must match the kWh/W convention of sibling series so external consumers
    # (apexcharts-card, energy templates) can read it without a divisor.
    out[APP_CHART_SERIES_Y6] = [round(value, 5) for value in revenue_values]
    meta.setdefault("corrected", {})[APP_STAT_TOTAL_SOLAR_REVENUE] = {
        "raw_total": raw_total,
        "corrected_total": monthly_total,
        "months": found_months,
    }


def backfill_year_payload_from_months(  # noqa: PLR0912
    year_source: dict[str, Any],
    section_prefix: str,
    stat_keys: tuple[str, ...],
    month_sources: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Builds and returns a year-period payload corrected from explicit monthly payloads when monthly data indicates the year totals are incomplete or inconsistent.

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
        section_prefix: Prefix identifying the section (e.g., PV/home/battery) used to form period keys.
        stat_keys: Tuple of statistic keys to attempt backfill for.
        month_sources: Mapping from 1-12 month index to that month's payload dictionary.

    Returns:
        A dictionary payload: either the unchanged `year_source` or a modified copy with corrected series/stat fields and `APP_YEAR_BACKFILL_META` when corrections were applied.
    """  # noqa: E501, RUF100
    if not isinstance(year_source, dict) or not month_sources:
        return year_source

    year_section = _period_section(section_prefix, DATE_TYPE_YEAR)
    month_section = _period_section(section_prefix, DATE_TYPE_MONTH)
    unit = str(year_source.get(APP_STAT_UNIT) or "").strip().lower()
    if unit and unit != APP_UNIT_KWH:
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
            if month < 1 or month > 12:  # noqa: PLR2004
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

        partial_year = len(found_months) < 12  # noqa: PLR2004
        corrected_total = (
            round(monthly_total * 12 / len(found_months), 2)
            if partial_year
            else monthly_total
        )

        out[series_key] = monthly_values
        out[stat_key] = corrected_total
        if stat_key == APP_STAT_TOTAL_SOLAR_ENERGY:
            out["pvEgy"] = corrected_total
        elif stat_key == APP_STAT_TOTAL_IN_GRID_ENERGY:
            out["inOngridEgy"] = corrected_total
        elif stat_key == APP_STAT_TOTAL_OUT_GRID_ENERGY:
            out["outOngridEgy"] = corrected_total
        elif stat_key == APP_STAT_TOTAL_DISCHARGE:
            out["batOtGridEgy"] = corrected_total

        meta.setdefault("corrected", {})[stat_key] = {
            "raw_total": raw_total,
            "monthly_total": monthly_total,
            "corrected_total": corrected_total,
            "series_key": series_key,
            "months": found_months,
            "partial_year": partial_year,
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
    """Backfill year-period payloads from available month histories for known statistic sections.

    This mutates `payload` in-place, replacing year-section entries with corrected year payloads when monthly data are available and a backfill is performed.

    Parameters:
        payload (dict[str, Any]): The full app payload to update; year-section keys (e.g. "<prefix>_year") may be replaced.
        month_history (dict[str, dict[int, dict[str, Any]]]): Mapping from section prefix to a mapping of 1-based month index -> month payload dict used to reconstruct year-series values.
    """  # noqa: E501, RUF100
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


def guard_statistic_totals_from_year(  # noqa: PLR0914
    payload: dict[str, Any],
    *,
    previous_statistic: dict[str, Any] | None = None,
) -> None:
    """Ensure statistic KPIs are bounded and corrected using current-year and previous-year PV totals.

    When a `PAYLOAD_STATISTIC` dict is present in `payload`, this function:
    - Uses the current-year PV section to derive a guarded `generation` total and, when available, revenue/savings and carbon corrections.
    - If the PV year section is missing, uses `previous_statistic` (when provided) as a lower bound for `APP_STAT_TOTAL_GENERATION`.
    - May overwrite `APP_STAT_TOTAL_GENERATION`, `APP_STAT_TOTAL_REVENUE`, and `APP_STAT_TOTAL_CARBON` when corrected values exceed cloud-reported totals beyond a computed tolerance.
    - Records any corrections or savings calculation metadata under `APP_TOTAL_GUARD_META` and `APP_SAVINGS_CALC_META` inside the statistic object.
    - Leaves `payload` unchanged when no correction or savings metadata is produced.

    Parameters:
        payload (dict[str, Any]): App payload containing `PAYLOAD_STATISTIC` and period sections (e.g., PV year section).
        previous_statistic (dict[str, Any] | None): Optional prior statistic mapping whose `APP_STAT_TOTAL_GENERATION` may be used as a lower bound when the PV year section is absent.
    """  # noqa: E501, RUF100
    statistic = payload.get(PAYLOAD_STATISTIC)
    if not isinstance(statistic, dict):
        return

    previous_generation = (
        safe_float(previous_statistic.get(APP_STAT_TOTAL_GENERATION))
        if isinstance(previous_statistic, dict)
        else None
    )
    raw_generation = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
    pv_year = payload.get(_period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR))

    if not isinstance(pv_year, dict):
        if previous_generation is None or (
            raw_generation is not None
            and previous_generation
            <= raw_generation
            + _tolerance_for_values(raw_generation, previous_generation)
        ):
            return
        out = dict(statistic)
        out[APP_STAT_TOTAL_GENERATION] = round(previous_generation, 2)
        out[APP_TOTAL_GUARD_META] = {
            "method": "previous_total_lower_bound",
            "corrected": {
                APP_STAT_TOTAL_GENERATION: {
                    "raw_total": raw_generation,
                    "corrected_total": round(previous_generation, 2),
                    "previous_total": previous_generation,
                }
            },
        }
        payload[PAYLOAD_STATISTIC] = out
        return

    year_generation = effective_period_total_value(
        pv_year,
        _period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR),
        APP_STAT_TOTAL_SOLAR_ENERGY,
    )
    year_revenue = _pv_revenue_value(pv_year)
    savings = _calculated_savings_from_year(
        payload, year_generation=year_generation, year_revenue=year_revenue
    )

    if year_generation is None and year_revenue is None and savings is None:
        return

    out = dict(statistic)
    meta: dict[str, Any] = {
        "method": "current_year_lower_bound",
        "source_section": _period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR),
    }

    generation_candidates = [
        val for val in (year_generation, previous_generation) if val is not None
    ]
    corrected_generation = max(generation_candidates) if generation_candidates else None

    if corrected_generation is not None and (
        raw_generation is None
        or corrected_generation
        > raw_generation + _tolerance_for_values(raw_generation, corrected_generation)
    ):
        out[APP_STAT_TOTAL_GENERATION] = round(corrected_generation, 2)
        meta.setdefault("corrected", {})[APP_STAT_TOTAL_GENERATION] = {
            "raw_total": raw_generation,
            "corrected_total": round(corrected_generation, 2),
            "current_year_total": year_generation,
            "previous_total": previous_generation,
        }

    raw_revenue = safe_float(statistic.get(APP_STAT_TOTAL_REVENUE))
    if savings is not None:
        calculated_revenue = safe_float(savings.get("calculated_total"))
        if calculated_revenue is not None:
            candidates = _pv_revenue_candidates(
                pv_year,
                year_revenue=year_revenue,
                raw_generation=raw_generation,
                price=safe_float(savings.get("price")),
            )
            publish_calculated, reason = _savings_publish_decision(
                raw_revenue=raw_revenue,
                calculated_revenue=calculated_revenue,
                raw_generation=raw_generation,
                year_generation=year_generation,
                year_revenue=year_revenue,
                pv_revenue_candidates=candidates,
            )
            published_value = calculated_revenue if publish_calculated else raw_revenue
            savings.update({
                "raw_cloud_total": raw_revenue,
                "pv_revenue_candidates": candidates,
                "decision": reason,
                "would_replace_cloud_total": publish_calculated,
                "published_value": published_value,
                "published_value_source": (
                    "calculated_savings" if publish_calculated else "cloud_total"
                ),
            })
            if publish_calculated:
                out[APP_STAT_TOTAL_REVENUE] = published_value
            out[APP_SAVINGS_CALC_META] = savings

    raw_carbon = safe_float(statistic.get(APP_STAT_TOTAL_CARBON))
    if (
        year_generation is not None
        and raw_generation is not None
        and raw_generation > 0
        and raw_carbon is not None
    ):
        factor = raw_carbon / raw_generation
        corrected_carbon = round(year_generation * factor, 2)
        if 0 < factor < 5 and corrected_carbon > raw_carbon + _tolerance_for_values(  # noqa: PLR2004
            raw_carbon, corrected_carbon
        ):
            out[APP_STAT_TOTAL_CARBON] = corrected_carbon
            meta.setdefault("corrected", {})[APP_STAT_TOTAL_CARBON] = {
                "raw_total": raw_carbon,
                "corrected_total": corrected_carbon,
                "kg_per_kwh": round(factor, 5),
            }

    if "corrected" in meta:
        out[APP_TOTAL_GUARD_META] = meta

    if "corrected" in meta or APP_SAVINGS_CALC_META in out:
        payload[PAYLOAD_STATISTIC] = out


def compact_json(value: object) -> str:
    """Produce a compact JSON string of the given value suitable for diagnostics.

    Returns:
        compact (str): JSON string with non-ASCII characters preserved and without unnecessary whitespace.
    """  # noqa: E501, RUF100
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def trend_series_points(  # noqa: PLR0912
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
        stat_key (str): Statistic key used to resolve the specific series within the section.
        today (date | None): Optional upper bound for returned points; defaults to today when None.

    Returns:
        list[TrendStatisticPoint]: Points for each valid series bucket with the bucket start date and the value rounded to 5 decimals. Empty list when the series is missing, not kWh, out of range, or cannot be mapped to dates.
    """  # noqa: E501, RUF100
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return []
    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if unit and unit != APP_UNIT_KWH:
        return []
    series = effective_trend_series_values(source, section, stat_key)
    if not isinstance(series, list) or not series:
        return []
    series_values = cast("list[Any]", series)

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
        today = datetime.now(tz=UTC).date()

    points: list[TrendStatisticPoint] = []
    for index, value in enumerate(series_values):
        if value is None:
            continue
        if date_type == DATE_TYPE_YEAR:
            month = index + 1
            if month < 1 or month > 12:  # noqa: PLR2004
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
        points.append(TrendStatisticPoint(bucket_start, round(value_float, 5)))
    return points


def _parse_day_chart_minute(value: object) -> int | None:
    """Parse an app day-chart label into minutes after local midnight.

    Parameters:
        value (Any): Label expected as an H:MM-style string (hours and minutes).

    Returns:
        int: Minutes after local midnight for a valid label (0-1439).
        None: If the input is not a valid H:MM label or represents the disallowed `24:00` end marker.
    """  # noqa: E501, RUF100
    if not isinstance(value, str):
        return None
    match = _DAY_CHART_MINUTE_RE.fullmatch(value)
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour == 24 and minute == 0:  # noqa: PLR2004
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:  # noqa: PLR2004
        return hour * 60 + minute
    return None


def _day_power_sample_minute(
    labels: list[Any] | None,
    index: int,
) -> int | None:
    """Determine the minute-of-day for a power-curve sample using an optional label list.

    Parameters:
        labels (list[Any] | None): Optional list of sample labels (e.g., "H:MM"); when present and the label at `index` can be parsed to minutes, that value is used.
        index (int): Zero-based sample index; used as a fallback to compute minute = index * 5.

    Returns:
        minute_of_day (int | None): Minutes after local midnight (0-1439) for the sample, or `None` if the computed minute is outside the day range or no valid label/index mapping exists.
    """  # noqa: E501, RUF100
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
) -> float | None:
    """Return the directional app day-curve sample value to integrate."""
    value = safe_float(raw)
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
            return abs(value) if value < 0 else max(value, 0.0)
    return max(value, 0.0)


def _scalar_day_energy_points(
    *,
    begin: date,
    scalar_total: float | None,
    current_day_limit_minute: int,
    bucket_minutes: int,
    local_tzinfo: TZInfo | None = None,
) -> list[TrendStatisticPoint]:
    """Represent a scalar-only app day total as one safe Recorder bucket."""
    if scalar_total is None or scalar_total < 0:
        return []
    bucket_minute = (current_day_limit_minute // bucket_minutes) * bucket_minutes
    bucket_minute = min(bucket_minute, 24 * 60 - bucket_minutes)
    if bucket_minute < 0:
        return []
    naive_dt = datetime(
        begin.year,
        begin.month,
        begin.day,
        bucket_minute // 60,
        bucket_minute % 60,
    )
    if local_tzinfo is not None:
        dt: datetime = naive_dt.replace(tzinfo=local_tzinfo).astimezone(UTC)
    else:
        dt = naive_dt
    return [TrendStatisticPoint(dt, round(scalar_total, 5))]


def day_power_energy_points(  # noqa: PLR0911, PLR0912, PLR0913, PLR0914, PLR0915
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    bucket_minutes: int = 60,
    today: date | None = None,
    now: datetime | None = None,
) -> list[TrendStatisticPoint]:
    """Convert a day chart curve into kWh statistic buckets for the requested day.

    Parses a chart-series day curve (watts or kWh sampled at ~5-minute intervals) and aggregates samples into contiguous buckets of `bucket_minutes`, optionally constraining to `today`/`now` when the request begins today. If the payload includes a scalar period total, bucket values are scaled to match that total; scalar-only non-zero day totals are represented as one safe bucket so empty app curves still reach Recorder.

    Parameters:
        source (dict[str, Any]): App payload containing chart series, optional labels and request meta.
        section (str): Payload section key used to resolve series and totals.
        stat_key (str): Statistic key used to locate the scalar period total when present.
        bucket_minutes (int): Size of each output bucket in minutes; must evenly divide 24*60. Defaults to 60.
        today (date | None): Reference date for "today" comparisons; defaults to the current local date.
        now (datetime | None): Reference time for limiting samples when the request begins today; defaults to current time.

    Returns:
        list[TrendStatisticPoint]: Ordered list of points where `start_date` is the bucket start (local date/time for the request day) and `value` is the bucket kWh (rounded to 5 decimal places). Returns an empty list for invalid inputs, unsupported units, out-of-range request dates, or when scaling rules prevent producing buckets.
    """  # noqa: E501, RUF100
    if bucket_minutes <= 0 or 24 * 60 % bucket_minutes != 0:
        return []
    series_key = day_power_series_key(source, section, stat_key)
    if not series_key:
        return []
    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if unit and unit not in {"w", APP_UNIT_KWH}:
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

    if begin is None or (end is not None and begin > end):
        return []
    if today is None:
        today = datetime.now(tz=UTC).date()
    if begin > today:
        return []
    if now is None:
        now = datetime.now()

    labels = source.get(APP_CHART_LABELS)
    parsed_labels = labels if isinstance(labels, list) else None
    current_day_limit_minute = (
        now.hour * 60 + now.minute if begin == now.date() else 24 * 60 - 1
    )
    series = source.get(series_key)
    scalar_total = effective_period_total_value(source, section, stat_key)
    if not isinstance(series, list) or not series:
        if scalar_total != 0.0:  # noqa: RUF069  # exact zero means safe zero-fill
            return _scalar_day_energy_points(
                begin=begin,
                scalar_total=scalar_total,
                current_day_limit_minute=current_day_limit_minute,
                bucket_minutes=bucket_minutes,
                local_tzinfo=now.tzinfo if now is not None else None,
            )
        zero_fill_max_bucket_minute = (current_day_limit_minute // bucket_minutes) * (
            bucket_minutes
        )
        return [
            TrendStatisticPoint(
                datetime(begin.year, begin.month, begin.day, minute // 60, minute % 60),
                0.0,
            )
            for minute in range(0, zero_fill_max_bucket_minute + 1, bucket_minutes)
        ]

    buckets: dict[int, float] = {}
    last_bucket_minute: int | None = None
    for index, raw in enumerate(series):
        minute = _day_power_sample_minute(parsed_labels, index)
        if minute is None or minute > current_day_limit_minute:
            continue
        sample_value = _day_power_sample_energy_value(raw, section, stat_key)
        if sample_value is None:
            continue

        bucket_minute = (minute // bucket_minutes) * bucket_minutes
        last_bucket_minute = (
            bucket_minute
            if last_bucket_minute is None
            else max(last_bucket_minute, bucket_minute)
        )
        sample_kwh = (
            sample_value if unit == APP_UNIT_KWH else sample_value * 5 / 60 / 1000
        )
        buckets[bucket_minute] = buckets.get(bucket_minute, 0.0) + sample_kwh

    if last_bucket_minute is None:
        return _scalar_day_energy_points(
            begin=begin,
            scalar_total=scalar_total,
            current_day_limit_minute=current_day_limit_minute,
            bucket_minutes=bucket_minutes,
            local_tzinfo=now.tzinfo if now is not None else None,
        )

    for minute in range(0, last_bucket_minute + 1, bucket_minutes):
        buckets.setdefault(minute, 0.0)

    raw_total = sum(buckets.values())
    if scalar_total is not None:
        if scalar_total < 0:
            return []
        if raw_total > 0:
            scale = scalar_total / raw_total
            buckets = {minute: value * scale for minute, value in buckets.items()}
        elif scalar_total > 0:
            return _scalar_day_energy_points(
                begin=begin,
                scalar_total=scalar_total,
                current_day_limit_minute=current_day_limit_minute,
                bucket_minutes=bucket_minutes,
                local_tzinfo=now.tzinfo if now is not None else None,
            )

    bucket_items = sorted(buckets.items())
    rounded_values = [round(max(value, 0.0), 5) for _minute, value in bucket_items]

    if scalar_total is not None and raw_total > 0 and rounded_values:
        diff = round(scalar_total - sum(rounded_values), 5)
        if diff:
            target_index = next(
                (
                    idx
                    for idx in range(len(rounded_values) - 1, -1, -1)
                    if rounded_values[idx] > 0
                ),
                len(rounded_values) - 1,
            )
            rounded_values[target_index] = round(
                max(rounded_values[target_index] + diff, 0.0), 5
            )

    return [
        TrendStatisticPoint(
            datetime(begin.year, begin.month, begin.day, minute // 60, minute % 60),
            bucket_value,
        )
        for (minute, _value), bucket_value in zip(
            bucket_items, rounded_values, strict=False
        )
    ]


# ---------------------------------------------------------------------------
# Power-flow calculation helpers
# ---------------------------------------------------------------------------
def directional_power_value(
    source: dict[str, Any],
    positive_keys: tuple[str, ...],
    negative_keys: tuple[str, ...],
) -> float | None:
    """Compute the net directional power by summing values from positive keys and subtracting sums from negative keys.

    Parameters:
        source (dict[str, Any]): Mapping containing numeric power values.
        positive_keys (tuple[str, ...]): Keys in `source` whose values contribute positively to the net sum.
        negative_keys (tuple[str, ...]): Keys in `source` whose values contribute negatively to the net sum.

    Returns:
        float | None: The net power (sum of positive keys minus sum of negative keys) if at least one numeric value is present, `None` otherwise.
    """  # noqa: E501, RUF100
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
    """Determine signed power for each CT phase, where positive indicates grid import and negative indicates export.

    Parameters:
        ct (dict[str, Any]): CT payload mapping containing phase power fields referenced by CT_PHASE_POWER_PAIRS.

    Returns:
        list[float] | None: A list of signed per-phase power values in the same order as CT_PHASE_POWER_PAIRS, or `None` if any phase value is missing or cannot be computed.
    """  # noqa: E501, RUF100
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
    phases = signed_phase_power_values(ct)
    if total is not None:
        if phases is not None:
            phase_sum = sum(phases)
            drift = abs(phase_sum - total)
            threshold = max(50.0, abs(total) * 0.05)
            if drift > threshold:
                _LOGGER.debug(
                    "CT meter per-phase sum (%.1f W) deviates from tPhasePw "
                    "(%.1f W) by %.1f W (%.1f%%). Shelly 3EM ADC sampling "
                    "jitter; using tPhasePw as authoritative.",
                    phase_sum,
                    total,
                    drift,
                    100 * drift / abs(total) if total else 0,
                )
        return total
    return sum(phases) if phases is not None else None


def calculated_smart_meter_power(  # noqa: PLR0911
    ct: dict[str, Any],
    calculation: str,
) -> float | None:
    """Return a derived power value computed from CT payloads according to the requested calculation mode.

    Parameters:
        ct (dict): CT/meter payload used to derive signed net and per-phase power values.
        calculation (str): One of: "net_import", "net_export", "gross_import", "gross_export", "gross_flow".
                - "net_import": positive portion of net power (grid import).
                - "net_export": positive portion of negated net power (grid export).
                - "gross_import": sum of positive per-phase powers.
                - "gross_export": sum of per-phase exports (absolute negative phase contributions).
                - "gross_flow": sum of absolute per-phase powers.

    Returns:
        float | None: Calculated power in the same units as the input values, or `None` when required inputs are missing or the calculation mode is unrecognized.
    """  # noqa: E501, RUF100
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
    """Get the first numeric power value found in `source` for the provided `keys`, checking them in order.

    Parameters:
        source (dict[str, Any]): Mapping containing candidate power values.
        *keys (str): Keys to check in priority order.

    Returns:
        float | None: The first value successfully coerced to a number, or `None` if no numeric value is found.
    """  # noqa: E501, RUF100
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

    Checks the known fields for reported home/other load power and returns the first available value.

    Parameters:
        props (dict[str, Any]): Device properties payload to inspect for power fields.

    Returns:
        float | None: The reported power in watts if present and parseable, `None` otherwise.
    """  # noqa: E501, RUF100
    return first_power_value(
        props, FIELD_OTHER_LOAD_PW, FIELD_HOME_LOAD_PW, FIELD_LOAD_PW
    )


def jackery_grid_side_input_power(props: dict[str, Any]) -> float | None:
    """AC input power reported by the Jackery device from the grid/home side.

    Returns:
        float: AC input power in watts, or `None` if no suitable value is present.
    """
    return first_nonzero_power_value(
        props, FIELD_GRID_IN_PW, FIELD_IN_ONGRID_PW, FIELD_IN_GRID_SIDE_PW
    )


def jackery_grid_side_output_power(props: dict[str, Any]) -> float | None:
    """Return the AC power Jackery is supplying to the grid/home side.

    Parameters:
        props (dict[str, Any]): Device properties dictionary to read output power fields from.

    Returns:
        float: Power in watts if a known output field contains a numeric value, `None` otherwise.
    """  # noqa: E501, RUF100
    return first_nonzero_power_value(
        props, FIELD_GRID_OUT_PW, FIELD_OUT_ONGRID_PW, FIELD_OUT_GRID_SIDE_PW
    )


def jackery_corrected_home_consumption_power(
    ct: dict[str, Any],
    props: dict[str, Any],
) -> HomeConsumptionPower | None:
    """Compute corrected home consumption power and accompanying diagnostic fields.

    If the Jackery device reports an explicit home/other load, that reported value (clamped to zero) is used and returned with diagnostic fields. If no reported home load is available and either the smart-meter net power is missing or both Jackery input and output powers are zero, the function returns `None`. Otherwise the function computes `meter_net - jackery_input + jackery_output`, clamps the result to zero, and returns it with diagnostic fields and a source identifier.

    Parameters:
        ct (dict[str, Any]): CT/smart-meter payload used to derive smart-meter net power.
        props (dict[str, Any]): Jackery device properties payload used to read reported home load and grid-side input/output powers.

    Returns:
        HomeConsumptionPower | None: A NamedTuple with fields
            - `value`: corrected home consumption power (kW or W as provided by inputs) clamped to >= 0.0,
            - `smart_meter_net_power`: the smart-meter net power (or `None` if not available),
            - `jackery_input_power`: Jackery grid-side input power,
            - `jackery_output_power`: Jackery grid-side output power,
            - `source`: string indicating which data was used (`FIELD_OTHER_LOAD_PW` when reported, otherwise `"smart_meter_net_minus_input_plus_output"`).
        Returns `None` when insufficient inputs are available to compute a corrected consumption.
    """  # noqa: E501, RUF100
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

    if meter_net is None or (jackery_input == 0.0 and jackery_output == 0.0):  # noqa: RUF069  # parsed device powers (or 0.0 default); exact-zero means absent/zero
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
def _chart_series_key_for_stat(section: str, stat_key: str) -> str | None:  # noqa: PLR0911, PLR0912
    """Map an app section and statistic key to the corresponding chart-series key.

    Parameters:
        section (str): App payload section identifier (e.g., PV/home/CT/battery trend or stat section).
        stat_key (str): Statistic key within the section.

    Returns:
        str | None: The chart-series key (e.g., `APP_CHART_SERIES_Y`, `APP_CHART_SERIES_Y1`, ...) associated with the given section/stat pair, or `None` if no mapping exists.
    """  # noqa: E501, RUF100
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


def trend_series_key(section: str, stat_key: str) -> str | None:
    """Map a section and statistic key to the corresponding chart-series key for day/week/month/year payloads.

    Only returns a chart-series key when `section` denotes a day, week, month, or year payload; otherwise returns `None`.

    Returns:
        str: The chart-series key (for example `"y"`, `"y1"`, `"y2"`, etc.), or `None` when the section is not a week/month/year payload or no mapping exists.
    """  # noqa: E501, RUF100
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
    """Get the chart-series key used for power-curve data when the app payload represents a day period.

    Returns:
        The chart-series key string for the given `section`/`stat_key` when `source` is a day-period payload, `None` otherwise.
    """  # noqa: E501, RUF100
    if not _is_day_period_payload(source, section):
        return None
    if (
        section.startswith((APP_SECTION_BATTERY_STAT, APP_SECTION_BATTERY_TRENDS))
        and stat_key
        in {
            APP_STAT_TOTAL_DISCHARGE,
            APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
        }
        and _series_contains_negative_samples(source, APP_CHART_SERIES_Y1)
    ):
        return APP_CHART_SERIES_Y1
    return _chart_series_key_for_stat(section, stat_key)


def trend_series_total(  # noqa: PLR0911
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> float | None:
    """Compute the period total for a trend/chart statistic section from an app payload.

    For day-period sections the function uses the effective period total derived from the payload.
    For non-day sections it requires a mapped chart-series key and that the section unit is `kwh`.
    If the chart-series list is missing the function applies guarded fallbacks:
    - For home-stat sections: returns `0.0` when the server total equals `0.0` but grid-related series lists are present.
    - For CT-stat sections: returns the server-reported total when present.

    Returns:
        float: The period total rounded to 2 decimals, or `None` when a reliable total cannot be determined.
    """  # noqa: E501, RUF100
    if _is_day_period_payload(source, section):
        total = effective_period_total_value(source, section, stat_key)
        return round(total, 2) if total is not None else None

    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None

    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if unit and unit != APP_UNIT_KWH:
        return None

    series = source.get(series_key)
    if not isinstance(series, list):
        server_total = effective_period_total_value(source, section, stat_key)
        if (
            section.startswith(APP_SECTION_HOME_STAT)
            and server_total == 0.0  # noqa: RUF069  # parsed/round(,2) period total; exact-zero is intentional
            and any(isinstance(source.get(k), list) for k in APP_HOME_GRID_SERIES_KEYS)
        ):
            return 0.0
        if (
            section.startswith((APP_SECTION_CT_STAT, APP_SECTION_EPS_STAT))
            and server_total is not None
        ):
            return round(server_total, 2)
        return None

    values = effective_trend_series_values(source, section, stat_key) or []
    valid_values = [v for v in values if v is not None]

    if not valid_values:
        server_total = effective_period_total_value(source, section, stat_key)
        if (
            section.startswith((APP_SECTION_CT_STAT, APP_SECTION_EPS_STAT))
            and server_total is not None
        ):
            return round(server_total, 2)
        return None

    return round(sum(valid_values), 2)


def trend_series_has_value(  # noqa: PLR0911
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> bool:
    """Determine whether the given app period payload contains a usable numeric value for the specified section and statistic key.

    Considers day-period scalars, chart-series lists for non-day periods (only when the unit is kWh or unspecified), and the module's special-case allowances for home and CT sections when series data or server totals imply a valid value.

    Returns:
        `true` if a numeric value can be derived from the payload for the section and stat_key, `false` otherwise.
    """  # noqa: E501, RUF100
    if _is_day_period_payload(source, section):
        return safe_float(source.get(stat_key)) is not None

    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return False

    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if unit and unit != APP_UNIT_KWH:
        return False

    series = source.get(series_key)
    if not isinstance(series, list):
        server_total = effective_period_total_value(source, section, stat_key)
        if (
            section.startswith(APP_SECTION_HOME_STAT)
            and server_total == 0.0  # noqa: RUF069  # parsed/round(,2) period total; exact-zero is intentional
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
) -> Any:  # payload value of unknown type by design  # noqa: ANN401, RUF100
    """Retrieve the first non-None value for any of the given keys from a task-plan payload.

    Searches in this order: the top-level of `task_plan`, the `TASK_PLAN_BODY` dictionary (if present), then each dictionary item in the `TASK_PLAN_TASKS` list (if present). Keys are checked in the order provided and the first non-`None` match is returned.

    Parameters:
        task_plan (dict): The task-plan payload to search.
        *keys (str): One or more keys to look up, checked in order.

    Returns:
        Any: The first non-`None` value found for the provided keys, or `None` if none are present.
    """  # noqa: E501, RUF100
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
    """Determine whether the provided trend payload contains a usable period sensor value.

    Checks for a computed chart-series total for the given section/statistic and, if absent, falls back to the scalar value at `stat_key`.

    Returns:
        True if a usable period value exists, False otherwise.
    """  # noqa: E501, RUF100
    if trend_series_total(source, section, stat_key) is not None:
        return True
    return safe_float(source.get(stat_key)) is not None
