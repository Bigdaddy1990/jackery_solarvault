"""Shared helpers for Jackery SolarVault entities."""

import calendar
import contextlib
from datetime import UTC, date, datetime, timedelta
import json
import operator
import os
from pathlib import Path
import re
from typing import Any, NamedTuple

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
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_TOTAL_CARBON,
    APP_STAT_TOTAL_CHARGE,
    APP_STAT_TOTAL_CT_INPUT_ENERGY,
    APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
    APP_STAT_TOTAL_DISCHARGE,
    APP_STAT_TOTAL_GENERATION,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    APP_STAT_TOTAL_REVENUE,
    APP_STAT_TOTAL_SOLAR_ENERGY,
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
    DATA_QUALITY_KEY_REFERENCE_VALUE,
    DATA_QUALITY_KEY_SOURCE_CHART_SERIES_KEY,
    DATA_QUALITY_KEY_SOURCE_REQUEST,
    DATA_QUALITY_KEY_SOURCE_SECTION,
    DATA_QUALITY_KEY_SOURCE_VALUE,
    DATA_QUALITY_KEY_TOTAL_METHOD,
    DATA_QUALITY_LEVEL_WARNING,
    DATA_QUALITY_REASON_LIFETIME_LESS_THAN_YEAR,
    DATA_QUALITY_REASON_MONTH_LESS_THAN_WEEK,
    DATA_QUALITY_REASON_YEAR_LESS_THAN_MONTH,
    DATA_QUALITY_REASON_YEAR_LESS_THAN_WEEK,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DEFAULT_ENABLE_UNREDACTED_DIAGNOSTICS,
    FIELD_CURRENT_VERSION,
    FIELD_DEVICE_SN,
    FIELD_DEV_SN,
    FIELD_GRID_IN_PW,
    FIELD_GRID_OUT_PW,
    FIELD_HOME_LOAD_PW,
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
    TASK_PLAN_BODY,
    TASK_PLAN_TASKS,
)

# CPU-Optimierung: Regex auf Modulebene kompilieren, nicht pro Schleifendurchlauf
_DAY_CHART_MINUTE_RE = re.compile(r"\s*(\d{1,2}):(\d{2})\s*")
_DEV_MODE_ENV: str = "JACKERY_DEV_MODE"
_DEV_MODE_CACHED: bool | None = None


def config_entry_bool_option(entry: Any, key: str, default: bool) -> bool:  # noqa: ANN401
    """Read a boolean configuration option from an entry, falling back to legacy setup data and a provided default.

    Parameters:
        entry (Any): An object with optional `options` and `data` mappings (commonly a config entry).
        key (str): The option key to read.
        default (bool): The boolean to return when the option is missing or unparseable.

    Returns:
        `true` if the resolved option parses as a truthy value, `false` if it parses as a falsy value; returns `default` when the option is missing or cannot be parsed as a boolean.
    """  # noqa: E501
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    value = options.get(key)
    if value is None:
        value = data.get(key, default)
    parsed = safe_bool(value)
    return default if parsed is None else parsed


def config_entry_str_option(entry: Any, key: str, default: str) -> str:  # noqa: ANN401
    """Read a string-valued configuration option from an entry, falling back to legacy setup data and the provided default.

    Parameters:
        entry (Any): Config-entry-like object with `options` and `data` mappings.
        key (str): Option key to look up.
        default (str): Value to return when neither `options` nor `data` provide a non-`None` value.

    Returns:
        str: The resolved option coerced to `str`, or `default` if no value is found.
    """  # noqa: E501
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    value = options.get(key)
    if value is None:
        value = data.get(key, default)
    if value is None:
        return default
    return str(value)


def config_entry_int_option(entry: Any, key: str, default: int) -> int:  # noqa: ANN401
    """Read an integer option from a config entry, falling back to legacy entry.data and finally to a default.

    Parameters:
        entry (Any): An object exposing optional mapping attributes `options` and `data`.
        key (str): The option name to read.
        default (int): The value to return if the option is missing or cannot be interpreted as an integer.

    Returns:
        int: The resolved integer value; if the option is missing or cannot be parsed as an integer, returns `default`.
    """  # noqa: E501
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


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(UTC)


def parse_utc_datetime(value: Any) -> datetime:  # noqa: ANN401
    """Parse a Jackery timestamp and normalize it to UTC.

    Accepts:
    - datetime: used as-is,
    - int/float (excluding bool): treated as a POSIX timestamp in seconds; values with absolute magnitude >= 100_000_000_000 are treated as milliseconds and divided by 1000,
    - str: trimmed; empty strings are rejected; first attempted as a numeric timestamp (as above), otherwise parsed as ISO 8601 (a trailing "Z" is treated as "+00:00").

    Parameters:
        value (Any): The timestamp value to parse.

    Returns:
        A timezone-aware UTC datetime.

    Raises:
        ValueError: If the input is empty, unsupported, or cannot be parsed as a UTC timestamp.
    """  # noqa: E501
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
    """Return a cheap shape-hash of the coordinator data for entity setup."""
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
        meter_heads = payload.get(PAYLOAD_METER_HEADS) or []
        meter_count = (
            sum(1 for item in meter_heads if isinstance(item, dict))
            if isinstance(meter_heads, list)
            else 0
        )

        sig.append((
            dev_id,
            plug_keys,
            pack_count,
            meter_count,
            payload.get(PAYLOAD_ALARM) is not None,
            bool((payload.get(PAYLOAD_OTA) or {}).get(FIELD_CURRENT_VERSION)),
            payload.get(PAYLOAD_CT_METER) is not None,
        ))
    return tuple(sig)


def append_unique_entity(
    entities: list[Any],
    seen_unique_ids: set[str],
    entity: Any,  # noqa: ANN401
    *,
    platform: str,
    logger: Any,  # noqa: ANN401
) -> bool:
    """Append an entity to the provided list only if its `unique_id` has not already been recorded, and record the `unique_id` when appended.

    Parameters:
        entities (list[Any]): Destination list for the entity.
        seen_unique_ids (set[str]): Set of unique IDs already seen; updated when a new entity is appended.
        entity (Any): Entity object; its `unique_id` attribute (if present) is used for deduplication.
        platform (str): Platform name used in the duplicate debug message.
        logger (Any): Logger used to emit a debug message when a duplicate is skipped.

    Returns:
        bool: `True` if the entity was appended, `False` if skipped due to a duplicate `unique_id`.
    """  # noqa: E501
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
    """Return the documented Jackery app begin/end range for a period."""
    date_type = validate_app_period_date_type(date_type)
    if today is None:
        today = date.today()  # noqa: DTZ011
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
        raise ValueError(f"Jackery app period {field_name} must not be empty")  # noqa: TRY003
    try:
        return date.fromisoformat(normalized)
    except ValueError as err:
        raise ValueError(  # noqa: TRY003
            f"Jackery app period {field_name} must be an ISO date (YYYY-MM-DD): "
            f"{value!r}",
        ) from err


def app_period_date_bounds(
    date_type: str,
    *,
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    today: date | None = None,
) -> tuple[str, str]:
    """Validate and normalize begin and end date bounds for a Jackery app period request.

    Parameters:
        date_type (str): Period type (one of APP_PERIOD_DATE_TYPES) used to compute defaults when a bound is omitted.
        begin_date (str | date | None): Optional begin bound; may be a date, datetime, or ISO date string. When None, the period's default begin is used.
        end_date (str | date | None): Optional end bound; may be a date, datetime, or ISO date string. When None, the period's default end is used.
        today (date | None): Optional "today" date used when computing period defaults; if None, the current date is used.

    Returns:
        tuple[str, str]: A pair (begin_iso, end_iso) of ISO-formatted date strings (YYYY-MM-DD).

    Raises:
        ValueError: If a provided bound is empty or unparseable, or if the resolved begin date is after the resolved end date.
    """  # noqa: E501
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
            f"{begin.isoformat()} > {end.isoformat()}",
        )
    return begin.isoformat(), end.isoformat()


def app_period_request_kwargs(
    date_type: str,
    *,
    today: date | None = None,
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


def first_nonblank_int(*values: Any) -> int | None:  # noqa: ANN401
    """Return the first provided value that can be parsed as an integer.

    Boolean values, blank strings, and non-finite numeric strings are rejected
    so callers can safely use this for protocol fields that require a real
    integer value.
    """
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                continue
        else:
            candidate = value
        try:
            parsed = int(candidate)
        except (TypeError, ValueError):
            try:
                parsed_float = float(candidate)
            except (TypeError, ValueError):
                continue
            if not parsed_float.is_integer():
                continue
            parsed = int(parsed_float)
        return parsed
    return None


def safe_float(value: Any) -> float | None:  # noqa: ANN401, PLR0911
    """Parse a Jackery payload value into a floating-point number.

    Accepts numbers or strings (including strings using a single comma as the decimal separator) and returns the numeric value when parseable.

    Returns:
        float | None: The parsed float, or `None` when the input is `None` or cannot be parsed as a float.
    """  # noqa: E501
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


def safe_int(value: Any) -> int | None:  # noqa: ANN401
    """Convert a Jackery payload value to int, returning None on error."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def dev_mode_redactions_disabled() -> bool:
    """Determine whether developer-mode disables payload redactions based on the JACKERY_DEV_MODE environment variable.

    The environment values "1", "true", "yes", or "on" (case-insensitive) enable developer mode; the computed result is cached for the process lifetime.

    Returns:
        `true` if developer-mode redactions are disabled, `false` otherwise.
    """  # noqa: E501
    global _DEV_MODE_CACHED  # noqa: PLW0603
    if _DEV_MODE_CACHED is None:
        raw = os.environ.get(_DEV_MODE_ENV, "")
        _DEV_MODE_CACHED = raw.strip().lower() in {"1", "true", "yes", "on"}
    return _DEV_MODE_CACHED


def diagnostic_redactions_disabled(entry: Any | None = None) -> bool:  # noqa: ANN401
    """Return True when diagnostics should bypass sensitive-field redaction."""
    if dev_mode_redactions_disabled():
        return True
    if entry is None:
        return False
    return config_entry_bool_option(
        entry,
        CONF_ENABLE_UNREDACTED_DIAGNOSTICS,
        DEFAULT_ENABLE_UNREDACTED_DIAGNOSTICS,
    )


def _payload_debug_redacted(value: Any, redactions_disabled: bool | None = None) -> Any:  # noqa: ANN401
    """Return a recursively redacted, JSON-serializable debug payload."""
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


def _payload_debug_passthrough(value: Any) -> Any:  # noqa: ANN401
    """Recursively normalise ``value`` to JSON-serializable types only."""
    if isinstance(value, dict):
        return {
            str(key): _payload_debug_passthrough(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_payload_debug_passthrough(item) for item in value]
    if isinstance(value, tuple):
        return [_payload_debug_passthrough(item) for item in value]
    return value


def redacted_json_safe_payload(value: Any) -> Any:  # noqa: ANN401
    """Return a JSON-safe payload with sensitive Jackery fields redacted."""
    return _payload_debug_redacted(value, redactions_disabled=False)


def active_redact_keys(entry: Any | None = None) -> frozenset[str]:  # noqa: ANN401
    """Return ``REDACT_KEYS`` or an empty set depending on dev-mode switches."""
    if diagnostic_redactions_disabled(entry):
        return frozenset()
    return frozenset(REDACT_KEYS)


def chart_series_debug(source: Any) -> dict[str, Any]:  # noqa: ANN401
    """Produce diagnostics for chart-series arrays found in an app payload.

    For each chart-series key (Y, Y1..Y6) present as a list, the returned mapping contains:
    - raw_count: number of items in the series.
    - parsed_sum: sum of all values that parse as floats, rounded to 5 decimals, or `None` if no parsable values.
    - items: list of per-index diagnostics with keys `index`, `raw`, `raw_type`, and `parsed_float`.

    If present, the payload's chart labels are returned under the `labels` key and request metadata under the `request` key.

    Returns:
        dict[str, Any]: A diagnostics dictionary keyed by chart-series names and optionally `labels` and `request`.
    """  # noqa: E501
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
    """Append a redacted JSON line to a debug file and rotate the file when it exceeds the configured size.

    Parameters:
        path (str | Path): Filesystem path of the JSONL debug file to append to; parent directories will be created if needed.
        event (dict[str, Any]): Diagnostic payload to redact and write as a single JSON line.
        redactions_disabled (bool | None): If True, do not redact sensitive fields; if False, redact; if None, use the module's default detection.
    """  # noqa: E501
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
            json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str),
        )
        file.write("\n")


def safe_bool(value: Any) -> bool | None:  # noqa: ANN401, PLR0911
    """Parse a value into a boolean according to Jackery payload conventions.

    Recognizes booleans, numeric truthiness (nonzero), and common string tokens: truthy {"1", "true", "on", "yes"} and falsy {"0", "false", "off", "no"}.

    Returns:
        `True` if the value represents truth, `False` if the value represents false, `None` when the value is `None` or cannot be parsed as a boolean.
    """  # noqa: E501
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


def smart_plug_serial(plug: Any) -> str | None:  # noqa: ANN401
    """Return the deviceSn for a smart-plug subdevice payload, or None."""
    if not isinstance(plug, dict):
        return None
    raw = plug.get(FIELD_DEVICE_SN) or plug.get(FIELD_DEV_SN) or plug.get(FIELD_SN)
    if raw is None:
        return None
    serial = str(raw).strip()
    return serial or None


def sorted_smart_plugs(plugs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """Return plug entries sorted by serial, dropping entries without one."""
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


def jackery_online_state(value: Any) -> bool | None:  # noqa: ANN401
    """Determine whether a Jackery device state value indicates online or offline.

    Returns:
        bool | None: `True` if the value indicates online, `False` if it indicates offline, `None` when unknown or unparseable.
    """  # noqa: E501
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
        """Return a diagnostics-safe mapping for coordinator payloads."""
        payload: dict[str, object] = {
            DATA_QUALITY_KEY_LEVEL: self.level,
            DATA_QUALITY_KEY_REASON: self.reason,
            DATA_QUALITY_KEY_METRIC_KEY: self.metric_key,
            DATA_QUALITY_KEY_LABEL: self.label,
            DATA_QUALITY_KEY_SOURCE_SECTION: self.source_section,
            DATA_QUALITY_KEY_SOURCE_VALUE: self.source_value,
            DATA_QUALITY_KEY_REFERENCE_SECTION: self.reference_section,  # noqa: F821
            DATA_QUALITY_KEY_REFERENCE_VALUE: self.reference_value,
        }
        if self.source_request is not None:
            payload[DATA_QUALITY_KEY_SOURCE_REQUEST] = dict(self.source_request)
        if self.reference_request is not None:
            payload[DATA_QUALITY_KEY_REFERENCE_REQUEST] = dict(self.reference_request)  # noqa: F821
        if self.source_chart_series_key is not None:
            payload[DATA_QUALITY_KEY_SOURCE_CHART_SERIES_KEY] = (
                self.source_chart_series_key
            )
        if self.total_method is not None:
            payload[DATA_QUALITY_KEY_TOTAL_METHOD] = self.total_method
        return payload


def normalized_data_quality_warnings(
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize and deduplicate data-quality warning mappings into a deterministic list.

    Parameters:
        warnings (list[dict[str, Any]]): Iterable of warning mappings. Each warning may include the keys
            for reason, metric key, source section, source value, reference section, and reference value;
            these six fields are used to determine duplicates.

    Returns:
        list[dict[str, Any]]: Deduplicated warnings, keeping the first occurrence for each unique tuple of
        (reason, metric_key, source_section, source_value, reference_section, reference_value) and
        returned in a deterministic, sorted order.
    """  # noqa: E501
    deduped: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        key = (
            str(warning.get(DATA_QUALITY_KEY_REASON) or ""),
            str(warning.get(DATA_QUALITY_KEY_METRIC_KEY) or ""),
            str(warning.get(DATA_QUALITY_KEY_SOURCE_SECTION) or ""),
            str(warning.get(DATA_QUALITY_KEY_SOURCE_VALUE) or ""),
            str(warning.get(DATA_QUALITY_KEY_REFERENCE_SECTION) or ""),  # noqa: F821
            str(warning.get(DATA_QUALITY_KEY_REFERENCE_VALUE) or ""),
        )
        deduped.setdefault(key, dict(warning))
    return [deduped[key] for key in sorted(deduped)]


def _format_request_range(request: Any) -> str | None:  # noqa: ANN401
    """Return a compact dateType/range summary for diagnostics messages."""
    if not isinstance(request, dict):
        return None
    date_type = request.get(APP_REQUEST_DATE_TYPE) or request.get(
        APP_REQUEST_DATE_TYPE_ALT,
    )
    begin = request.get(APP_REQUEST_BEGIN_DATE) or request.get(
        APP_REQUEST_BEGIN_DATE_ALT,
    )
    end = request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT)
    if not date_type and not begin and not end:
        return None
    if begin or end:
        return f"{date_type or 'unknown'} {begin or '?'}..{end or '?'}"
    return str(date_type)


def format_data_quality_warning(warning: dict[str, Any]) -> str:
    """Format a data-quality warning mapping into a compact, deterministic diagnostic string.

    Parameters:
        warning (dict[str, Any]): A warning mapping that may contain label or metric key,
            source and reference section identifiers and values, and optional source/reference
            request metadata. Expected keys include `DATA_QUALITY_KEY_LABEL` or
            `DATA_QUALITY_KEY_METRIC_KEY`, `DATA_QUALITY_KEY_SOURCE_SECTION`,
            `DATA_QUALITY_KEY_SOURCE_VALUE`, `DATA_QUALITY_KEY_REFERENCE_SECTION`,
            `DATA_QUALITY_KEY_REFERENCE_VALUE`, and optional `DATA_QUALITY_KEY_SOURCE_REQUEST`
            / `DATA_QUALITY_KEY_REFERENCE_REQUEST`.

    Returns:
        str: A single-line summary in the form
        "`<metric>: <source_section>=<source_value> < <reference_section>=<reference_value>`"
        optionally followed by request range summaries in square brackets. Missing or
        None fields are rendered as `"unknown"`.
    """  # noqa: E501
    metric = (
        warning.get(DATA_QUALITY_KEY_LABEL)
        or warning.get(DATA_QUALITY_KEY_METRIC_KEY)
        or "unknown"
    )
    source_section = warning.get(DATA_QUALITY_KEY_SOURCE_SECTION) or "unknown"
    source_value = warning.get(DATA_QUALITY_KEY_SOURCE_VALUE)
    reference_section = warning.get(DATA_QUALITY_KEY_REFERENCE_SECTION) or "unknown"  # noqa: F821
    reference_value = warning.get(DATA_QUALITY_KEY_REFERENCE_VALUE)
    source_text = "unknown" if source_value is None else str(source_value)
    reference_text = "unknown" if reference_value is None else str(reference_value)

    text = f"{metric}: {source_section}={source_text} < {reference_section}={reference_text}"  # noqa: E501
    source_request = _format_request_range(warning.get(DATA_QUALITY_KEY_SOURCE_REQUEST))
    reference_request = _format_request_range(
        warning.get(DATA_QUALITY_KEY_REFERENCE_REQUEST),  # noqa: F821
    )

    if source_request or reference_request:
        text += f" [{source_section}: {source_request or 'unknown'}; {reference_section}: {reference_request or 'unknown'}]"  # noqa: E501
    return text


def app_data_quality_warnings(
    payload: dict[str, Any],
    *,
    today: date | None = None,
    tolerance: float = 0.05,
) -> list[AppDataQualityWarning]:
    """Detect contradictory statistics across app period sections and produce data-quality warnings.

    Parameters:
        payload (dict[str, Any]): Parsed app payload containing period sections (e.g., "{prefix}_week", "{prefix}_month", "{prefix}_year") and optional `statistic` section.
        today (date | None): Reference date used to compute the current week/month/year boundaries. When `None`, uses the current local date.
        tolerance (float): Absolute tolerance applied when comparing totals (a value is considered inconsistent when it exceeds the reference by more than this tolerance).

    Returns:
        list[AppDataQualityWarning]: A list of warnings describing detected contradictions. Warnings are produced for cases such as:
            - year total less than month total,
            - year total less than week total (when the week is inside the current year),
            - month total less than week total (when the week is inside the current month),
            - lifetime generation less than current-year generation.
        Each warning includes rounded source/reference values, optional request metadata for the involved sections, chart-series keys when applicable, and `total_method="chart_series_sum"` for warnings derived from chart-series totals (omitted when the source is the aggregated `statistic` section).
    """  # noqa: E501
    if today is None:
        today = date.today()  # noqa: DTZ011
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
        """Append a standardized AppDataQualityWarning to the module-level `warnings` list.

        Parameters:
                reason (str): Short code describing why the warning was generated.
                metric_key (str): Metric identifier the warning applies to.
                label (str): Human-readable label for the metric.
                stat_key (str): Statistic key used to derive chart-series keys for diagnostics.
                source_section (str): Section name containing the source value.
                source_value (float): Numeric source value that triggered the warning.
                reference_section (str): Section name used as the reference for comparison.
                reference_value (float): Numeric reference value used for comparison.
        """  # noqa: E501
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
                    source_section,
                    stat_key,
                ),
                reference_chart_series_key=_chart_series_key_for_section(
                    reference_section,
                    stat_key,
                ),
                total_method="chart_series_sum"
                if source_section != PAYLOAD_STATISTIC
                else None,
            ),
        )

    for prefix, stat_key, metric_key, label in APP_CHART_STAT_METRICS:
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

    statistic = payload.get(PAYLOAD_STATISTIC)
    if isinstance(statistic, dict):
        lifetime_generation = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
        year_generation = _period_total(
            APP_SECTION_PV_STAT,
            DATE_TYPE_YEAR,
            APP_STAT_TOTAL_SOLAR_ENERGY,
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


def statistic_id_part(value: Any) -> str:  # noqa: ANN401
    """Normalize a value into a lowercase, HA-safe statistic id fragment.

    Parameters:
        value (Any): Value to normalize; typically a string or identifier.

    Returns:
        str: A lowercase string containing only letters, digits, and underscores,
        with consecutive non-alphanumeric characters collapsed to a single underscore,
        trimmed of leading/trailing underscores. Returns `"unknown"` if the result is empty.
    """  # noqa: E501
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
    """Construct an external statistic id for trend statistics.

    The id has the form "domain:device_metric_bucket" where each component is converted to a normalized identifier part.

    Returns:
        external_id (str): The constructed external statistic id.
    """  # noqa: E501
    return (
        f"{domain}:"
        f"{statistic_id_part(device_id)}_"
        f"{statistic_id_part(metric_key)}_"
        f"{statistic_id_part(bucket)}"
    )


def _parse_iso_date(value: Any) -> date | None:  # noqa: ANN401
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _trend_date_type(section: str, source: dict[str, Any]) -> str | None:
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


def _is_day_period_payload(source: dict[str, Any], section: str) -> bool:
    """Return True for app ``dateType=day`` payloads."""
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
    """Return True for device app dateType=year statistic payloads."""
    return _trend_date_type(section, source) == DATE_TYPE_YEAR and section.startswith((
        APP_SECTION_PV_STAT,
        APP_SECTION_HOME_STAT,
        APP_SECTION_BATTERY_STAT,
        APP_SECTION_CT_STAT,
    ))


def _compact_year_parts(value: Any) -> tuple[float, float] | None:  # noqa: ANN401
    """Parse a Jackery "compact year" value into previous-bucket and current-bucket contributions.

    Accepts numeric values or string-like representations (commas are accepted as decimal separators). Interprets values with a decimal point as "whole.fraction" where the whole part is treated as the previous bucket contribution and the fractional digits represent the current bucket contribution. Values without a decimal point are returned as (0.0, parsed_value). Returns None for None, boolean inputs, or when the value cannot be reliably parsed into the expected compact format.

    Parameters:
        value (Any): Numeric or string-like compact year value (e.g., "12.345" or 12345).

    Returns:
        tuple[float, float] | None: `(previous, current)` contributions as floats when parsing succeeds, `None` otherwise.
    """  # noqa: E501
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None

    sign = -1.0 if text.startswith("-") else 1.0
    unsigned = text.removeprefix("-")
    if "." not in unsigned:
        parsed = safe_float(value)
        return None if parsed is None else (0.0, parsed)

    whole_text, fraction_text = unsigned.split(".", 1)
    if not whole_text:
        whole_text = "0"
    if not whole_text.isdigit() or not fraction_text.isdigit():
        parsed = safe_float(value)
        return None if parsed is None else (0.0, parsed)

    whole = sign * float(int(whole_text))
    fraction = sign * float(int(fraction_text)) if int(fraction_text) else 0.0

    if fraction == 0.0:  # noqa: RUF069
        parsed = safe_float(value)
        return None if parsed is None else (0.0, parsed)
    return whole, fraction


def expanded_year_series_values(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> list[float] | None:
    """Return chart values with Jackery device-year compact buckets expanded."""
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None
    series = source.get(series_key)
    if not isinstance(series, list):
        return None

    raw_values = [round(safe_float(item) or 0.0, 5) for item in series]
    raw_sum = round(sum(raw_values), 2)
    direct_total = safe_float(source.get(stat_key))

    if direct_total is not None:
        tolerance = max(0.05, abs(direct_total) * 0.005)
        if abs(raw_sum - direct_total) <= tolerance:
            return raw_values

    expanded = [0.0 for _ in series]
    for index, raw_value in enumerate(series):
        parts = _compact_year_parts(raw_value)
        if parts is None:
            continue
        previous_value, current_value = parts
        if previous_value:
            target = index - 1 if index > 0 else index
            expanded[target] += previous_value
        if current_value:
            expanded[index] += current_value

    expanded = [round(value, 5) for value in expanded]

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
    """Return the effective chart series used for state/statistic calculations."""
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
    """Return the effective app period total for one statistic field."""
    if is_device_year_period_section(source, section):
        values = effective_trend_series_values(source, section, stat_key)
        if values is not None:
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
    """Detect whether a year-period payload contains non-zero data only for the current month (a common app month-only bug).

    Parameters:
        source: The payload dictionary for the section being inspected.
        section: The section name within the payload whose series should be examined.
        stat_keys: Tuple of statistic keys to check inside the section.
        current_month: The current month as an integer (1–12) used to identify month-only data.

    Returns:
        `true` if any inspected series has non-zero values only in `current_month`, `false` otherwise.
    """  # noqa: E501, RUF002
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
    """Extract the PV revenue value from a payload dictionary.

    Parameters:
        source (dict[str, Any]): Payload dictionary that may contain `totalSolarRevenue` or `pvProfit`.

    Returns:
        float: The total PV revenue in normal currency units, rounded to 5 decimals, if present or derivable; `None` otherwise.
    """  # noqa: E501
    revenue = safe_float(source.get("totalSolarRevenue"))
    if revenue is not None:
        return revenue
    profit = safe_float(source.get("pvProfit"))
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
    """Return a configured per-kWh price if present, otherwise derive a price from yearly generation and revenue.

    Parameters:
        payload (dict[str, Any]): Payload that may contain a price configuration under the `PAYLOAD_PRICE` key.
        year_generation (float | None): Total generation for the year in kWh used to derive a price when configured price is absent.
        year_revenue (float | None): Total revenue for the year in currency units used to derive a price when configured price is absent.

    Returns:
        tuple[float | None, str | None]: A pair `(price, source)` where `price` is the per-kWh price (configured value or a derived value rounded to 5 decimals) and `source` is the string indicating the origin of the price (e.g., "`PAYLOAD_PRICE.FIELD_SINGLE_PRICE`" or `"pv_year_revenue_per_kwh"`). Returns `(None, None)` when no valid configured or derivable price is available.
    """  # noqa: E501
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
    """Generate numeric candidate PV revenue totals for comparison and deduplication.

    Collects possible revenue values from:
    - the provided `year_revenue`,
    - the product of `raw_generation` and `price` (when both are provided),
    - backfilled metadata found at `pv_year[APP_YEAR_BACKFILL_META]["corrected"]["totalSolarRevenue"]` (`raw_total` and `corrected_total`) when present.

    All candidate values are rounded to 2 decimal places and deduplicated using a tolerance based on the magnitudes of compared values.

    Parameters:
        pv_year (dict[str, Any]): Year payload which may contain backfill metadata under `APP_YEAR_BACKFILL_META`.
        year_revenue (float | None): Reported year revenue from cloud/statistics, or `None`.
        raw_generation (float | None): Year generation (kWh) used to derive a revenue candidate when combined with `price`.
        price (float | None): Unit price used to compute `raw_generation * price`, or `None`.

    Returns:
        list[float]: Deduplicated list of candidate revenue totals (rounded to 2 decimals).
    """  # noqa: E501
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


def _calculated_savings_from_year(  # noqa: PLR0914
    payload: dict[str, Any],
    *,
    year_generation: float | None,
    year_revenue: float | None,
) -> dict[str, Any] | None:
    """Estimate monetary savings and provide a breakdown of energy components using annual generation/revenue and payload totals.

    Parameters:
        payload (dict[str, Any]): App payload containing trend/statistic sections used to derive totals.
        year_generation (float | None): Annual PV generation in kWh when available.
        year_revenue (float | None): Annual PV revenue when available.

    Returns:
        dict[str, Any]: A mapping with keys:
            - "method": string describing the calculation method used,
            - "calculated_total": monetary savings rounded to 2 decimals,
            - "energy_kwh": savings energy in kWh rounded to 2 decimals,
            - "price": price used (rounded to 5 decimals),
            - "price_source": source identifier for the price,
            - "source_energy": dict of rounded energy diagnostics (pv_year_kwh, device/grid inputs/outputs, home consumption, CT public export, battery charge/discharge, conversion loss, residual PV energy, etc.).
        None: If required inputs or derived price are unavailable (no device output, no home/CT totals, or no price).
    """  # noqa: E501
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
            0.0,
            year_generation - savings_energy,
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
            "ct_public_export_year_kwh": _round_stat_value(public_export),
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
    """Decides whether the cloud-reported revenue should be published (kept) or replaced, and provides a short machine-readable reason.

    Parameters:
        raw_revenue (float | None): Cloud-provided total revenue, or `None` if missing.
        calculated_revenue (float): Revenue calculated from local data.
        raw_generation (float | None): Cloud-provided lifetime generation, or `None` if missing.
        year_generation (float | None): Current-year generation derived from year payloads, or `None`.
        pv_revenue_candidates (list[float]): Candidate revenue values derived from PV data for shape comparison.

    Returns:
        tuple[publish (bool), reason (str)]: `publish` is `True` when the cloud total should be used (or cloud total is missing and publishing is allowed); `False` when the cloud total appears implausibly high compared with the current-year calculation. `reason` is a short identifier explaining the decision (e.g., `"missing_cloud_total_revenue"`, `"cloud_total_matches_calculated_savings"`, `"cloud_total_below_current_year_savings"`, `"cloud_total_matches_pv_revenue_not_savings"`, `"cloud_total_higher_than_current_year_savings"`).
    """  # noqa: E501
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

    return False, "cloud_total_higher_than_current_year_savings"


def _backfill_pv_revenue(
    out: dict[str, Any],
    year_source: dict[str, Any],
    month_sources: dict[int, dict[str, Any]],
    meta: dict[str, Any],
) -> None:
    """Backfill yearly PV revenue fields from per-month sources when monthly totals differ from the year value.

    Computes per-month revenue values using _pv_revenue_value from month_sources, sums them to a monthly total, and if the monthly total is not approximately equal to the existing year total, mutates `out` to set:
    - "totalSolarRevenue" to the rounded monthly total (kWh-based revenue total),
    - "pvProfit" to monthly total scaled by 10_000_000 (rounded to 0.1),
    - APP_CHART_SERIES_Y6 to the per-month series scaled by 10_000_000 (rounded to 0.1).

    Also records the correction under meta["corrected"]["totalSolarRevenue"] with keys "raw_total", "corrected_total", and "months".

    Parameters:
        out (dict[str, Any]): Mutable output dictionary to receive corrected year fields.
        year_source (dict[str, Any]): Original year payload used to read the existing raw total.
        month_sources (dict[int, dict[str, Any]]): Mapping of month (1–12) to month payload dicts used to derive per-month revenues.
        meta (dict[str, Any]): Mutable metadata dict where correction details are recorded.

    Behavior notes:
    - If no valid monthly revenues are found, the function returns without modifying `out` or `meta`.
    - If the monthly total is within tolerance of the existing year total, no modifications are made.
    """  # noqa: E501, RUF002
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
        raw_total,
        monthly_total,
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


def backfill_year_payload_from_months(  # noqa: PLR0912
    year_source: dict[str, Any],
    section_prefix: str,
    stat_keys: tuple[str, ...],
    month_sources: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Produce a year-period payload corrected from explicit monthly payloads when monthly totals indicate a different yearly total.

    If monthly sources provide values for any requested stat_keys and the summed monthly total exceeds the existing yearly total beyond a computed tolerance, returns a shallow-copied year payload with:
    - the per-month series written to the appropriate chart-series key,
    - the stat total replaced by the summed monthly total,
    - legacy short-field mirrors for certain stat keys (e.g., `pvEgy`),
    - an `APP_YEAR_BACKFILL_META` entry describing corrections and months used.
    If no corrections are needed or applicable, returns the original `year_source` unchanged.

    Parameters:
        year_source (dict[str, Any]): The original year-period payload to inspect and potentially correct.
        section_prefix (str): Prefix identifying the section (e.g., PV/home/battery) used to derive section names.
        stat_keys (tuple[str, ...]): Statistic keys to consider for monthly backfill and correction.
        month_sources (dict[int, dict[str, Any]]): Mapping from month number (1–12) to monthly payload dicts.

    Returns:
        dict[str, Any]: Either the original `year_source` (when unchanged) or a modified copy containing corrected series, totals, and backfill metadata.
    """  # noqa: E501, RUF002
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
            raw_total,
            monthly_total,
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
    """Apply same-endpoint month backfill to known year statistic sections."""
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
    """Ensure app KPI totals in `payload[PAYLOAD_STATISTIC]` are not lower than plausible current-year or previous-year derived values.

    Mutates `payload` in-place when corrections are required: it may update total generation, total revenue, and total carbon and attach metadata under `APP_TOTAL_GUARD_META` and/or `APP_SAVINGS_CALC_META`. Corrections are based on the current-year PV section, a provided previous statistic snapshot, and derived savings/revenue calculations; when no correction is needed the payload is left unchanged.

    Parameters:
        payload (dict[str, Any]): The full app payload to inspect and possibly modify. Must be a mapping that may contain `PAYLOAD_STATISTIC` and a year section for PV (e.g. `"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}"`).
        previous_statistic (dict[str, Any] | None): Optional previous statistics mapping used as a lower-bound reference for total generation when current-year data is absent.
    """  # noqa: E501
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
                },
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
        payload,
        year_generation=year_generation,
        year_revenue=year_revenue,
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
                pv_revenue_candidates=candidates,
            )
            savings.update({
                "raw_cloud_total": raw_revenue,
                "pv_revenue_candidates": candidates,
                "decision": reason,
                "would_replace_cloud_total": publish_calculated,
                "published_value": raw_revenue,
                "published_value_source": "cloud_total",
            })
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
            raw_carbon,
            corrected_carbon,
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


def compact_json(value: Any) -> str:  # noqa: ANN401
    """Produce a compact JSON representation of a JSON-serializable value.

    Parameters:
        value (Any): A value that can be serialized to JSON.

    Returns:
        compact_json (str): Compact JSON string representation of `value`.
    """
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def trend_series_points(  # noqa: PLR0912
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    today: date | None = None,
) -> list[TrendStatisticPoint]:
    """Convert an app trend/chart series into dated TrendStatisticPoint buckets.

    Maps series indexes to bucket start dates using the request's begin/end bounds and the section's inferred date type (week, month, year). Skips buckets outside the request range or after `today`, and ignores non-kWh units or missing series.

    Parameters:
        today (date | None): Optional override for the current date used to bound points. If omitted, uses the system date.

    Returns:
        list[TrendStatisticPoint]: Points containing the bucket start date and the series value rounded to 5 decimals; returns an empty list when the series is missing, the unit is not kWh, or the request begin date cannot be determined.
    """  # noqa: E501
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return []
    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if unit and unit != APP_UNIT_KWH:
        return []
    series = effective_trend_series_values(source, section, stat_key)
    if not isinstance(series, list) or not series:
        return []

    request = source.get(APP_REQUEST_META)
    begin = None
    end = None
    if isinstance(request, dict):
        begin = _parse_iso_date(
            request.get(APP_REQUEST_BEGIN_DATE)
            or request.get(APP_REQUEST_BEGIN_DATE_ALT),
        )
        end = _parse_iso_date(
            request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT),
        )

    date_type = _trend_date_type(section, source)
    if begin is None:
        return []
    if today is None:
        today = date.today()  # noqa: DTZ011

    points: list[TrendStatisticPoint] = []
    for index, value in enumerate(series):
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
        points.append(TrendStatisticPoint(bucket_start, round(value, 5)))
    return points


def _parse_day_chart_minute(value: Any) -> int | None:  # noqa: ANN401
    """Parse an app day-chart label into minutes after local midnight."""
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
    """Return a local day minute for one power-curve sample."""
    if labels is not None and index < len(labels):
        minute = _parse_day_chart_minute(labels[index])
        if minute is not None:
            return minute
    minute = index * 5
    return minute if 0 <= minute < 24 * 60 else None


def day_power_energy_points(  # noqa: PLR0911, PLR0912, PLR0913, PLR0914, PLR0915
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    bucket_minutes: int = 60,
    today: date | None = None,
    now: datetime | None = None,
) -> list[TrendStatisticPoint]:
    """Convert a single-day chart series into time-bucketed kWh points.

    The function reads the day-series values and produces a list of TrendStatisticPoint
    objects where each point represents the summed energy (kWh) for a bucket
    of length `bucket_minutes` starting at that bucket's minute-of-day. If the
    payload provides a server-side period total, bucket values are scaled so their
    sum matches that total. Invalid or incompatible inputs (missing/empty series,
    non-day payload, unsupported unit, invalid request bounds, negative server
    total, etc.) result in an empty list.

    Parameters:
        source (dict[str, Any]): The payload containing chart series, labels and
            request metadata.
        section (str): The payload section name (used to locate totals/metadata).
        stat_key (str): The statistic key whose series/total to use.
        bucket_minutes (int, optional): Bucket size in minutes; must divide 24*60.
            Defaults to 60.
        today (date | None, optional): Reference current date for validation.
            Defaults to date.today() when omitted.
        now (datetime | None, optional): Reference current datetime used when the
            requested day is the current day to limit samples; defaults to now.

    Returns:
        list[TrendStatisticPoint]: Ordered list of points for each bucket start time
        (local date/time at bucket boundary) and the bucket energy in kWh.
    """
    if bucket_minutes <= 0 or 24 * 60 % bucket_minutes != 0:
        return []
    series_key = day_power_series_key(source, section, stat_key)
    if not series_key:
        return []
    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if unit and unit not in {"w", APP_UNIT_KWH}:
        return []
    series = source.get(series_key)
    if not isinstance(series, list) or not series:
        return []

    request = source.get(APP_REQUEST_META)
    begin = None
    end = None
    if isinstance(request, dict):
        begin = _parse_iso_date(
            request.get(APP_REQUEST_BEGIN_DATE)
            or request.get(APP_REQUEST_BEGIN_DATE_ALT),
        )
        end = _parse_iso_date(
            request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT),
        )

    if begin is None or (end is not None and begin > end):
        return []
    if today is None:
        today = date.today()  # noqa: DTZ011
    if begin > today:
        return []
    if now is None:
        now = datetime.now()

    labels = source.get(APP_CHART_LABELS)
    parsed_labels = labels if isinstance(labels, list) else None
    current_day_limit_minute = (
        now.hour * 60 + now.minute if begin == now.date() else 24 * 60 - 1
    )

    buckets: dict[int, float] = {}
    max_bucket_minute: int | None = None
    for index, raw in enumerate(series):
        minute = _day_power_sample_minute(parsed_labels, index)
        if minute is None or minute > current_day_limit_minute:
            continue
        sample_value = safe_float(raw)
        if sample_value is None:
            continue

        bucket_minute = (minute // bucket_minutes) * bucket_minutes
        max_bucket_minute = (
            bucket_minute
            if max_bucket_minute is None
            else max(max_bucket_minute, bucket_minute)
        )
        sample_kwh = (
            max(sample_value, 0.0)
            if unit == APP_UNIT_KWH
            else max(sample_value, 0.0) * 5 / 60 / 1000
        )
        buckets[bucket_minute] = buckets.get(bucket_minute, 0.0) + sample_kwh

    if max_bucket_minute is None:
        return []

    for minute in range(0, max_bucket_minute + 1, bucket_minutes):
        buckets.setdefault(minute, 0.0)

    raw_total = sum(buckets.values())
    scalar_total = effective_period_total_value(source, section, stat_key)
    if scalar_total is not None:
        if scalar_total < 0:
            return []
        if raw_total > 0:
            scale = scalar_total / raw_total
            buckets = {minute: value * scale for minute, value in buckets.items()}
        elif scalar_total > 0:
            return []

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
                max(rounded_values[target_index] + diff, 0.0),
                5,
            )

    return [
        TrendStatisticPoint(
            datetime(begin.year, begin.month, begin.day, minute // 60, minute % 60),
            bucket_value,
        )
        for (minute, _value), bucket_value in zip(
            bucket_items,
            rounded_values,
            strict=False,
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
    """Return positive-key sum minus negative-key sum if any value exists."""
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
    """Return signed CT phase powers; positive=grid import, negative=export."""
    values: list[float] = []
    for pos_key, neg_key in CT_PHASE_POWER_PAIRS:
        value = directional_power_value(ct, (pos_key,), (neg_key,))
        if value is None:
            return None
        values.append(value)
    return values


def smart_meter_net_power(ct: dict[str, Any]) -> float | None:
    """Return app-reported CT grid power; positive=import, negative=export."""
    total = directional_power_value(
        ct,
        (CT_TOTAL_POWER_PAIR[0],),
        (CT_TOTAL_POWER_PAIR[1],),
    )
    if total is not None:
        return total
    phases = signed_phase_power_values(ct)
    return sum(phases) if phases is not None else None


def calculated_smart_meter_power(  # noqa: PLR0911
    ct: dict[str, Any],
    calculation: str,
) -> float | None:
    """Compute a derived smart-meter power metric from CT payload values.

    Parameters:
        ct (dict[str, Any]): CT payload containing phase and total power keys.
        calculation (str): One of:
            - "net_import": net power imported (non-negative),
            - "net_export": net power exported (non-negative),
            - "gross_import": sum of positive phase powers,
            - "gross_export": sum of exported (negative) phase powers as positive values,
            - "gross_flow": sum of absolute phase powers.
        Unrecognized values produce no result.

    Returns:
        float | None: The calculated power (same units as the CT values) when computable, or `None` if required inputs are missing or `calculation` is not supported.
    """  # noqa: E501
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
    """Return the first available numeric power value for the given keys."""
    for key in keys:
        if key in source and source.get(key) is not None:
            value = safe_float(source.get(key))
            if value is not None:
                return value
    return None


def jackery_reported_home_load_power(props: dict[str, Any]) -> float | None:
    """Return Jackery's reported live home/other-load power if available."""
    return first_power_value(
        props,
        FIELD_OTHER_LOAD_PW,
        FIELD_HOME_LOAD_PW,
        FIELD_LOAD_PW,
    )


def jackery_grid_side_input_power(props: dict[str, Any]) -> float | None:
    """AC power drawn by Jackery from the grid/home side."""
    return first_power_value(
        props,
        FIELD_IN_ONGRID_PW,
        FIELD_GRID_IN_PW,
        FIELD_IN_GRID_SIDE_PW,
    )


def jackery_grid_side_output_power(props: dict[str, Any]) -> float | None:
    """AC power supplied by Jackery to the grid/home side."""
    return first_power_value(
        props,
        FIELD_OUT_ONGRID_PW,
        FIELD_GRID_OUT_PW,
        FIELD_OUT_GRID_SIDE_PW,
    )


def jackery_corrected_home_consumption_power(
    ct: dict[str, Any],
    props: dict[str, Any],
) -> HomeConsumptionPower | None:
    """Determine the current home consumption power and provide diagnostic components.

    Parameters:
        ct (dict[str, Any]): Smart-meter/CT payload used to derive net meter power.
        props (dict[str, Any]): Jackery live properties used to read reported home load and grid-side input/output.

    Returns:
        HomeConsumptionPower: Named tuple containing:
                - value: non-negative estimated home consumption (kW or W consistent with inputs),
                - smart_meter_net_power: net power from the smart meter or None,
                - jackery_input_power: jackery reported grid-side input power,
                - jackery_output_power: jackery reported grid-side output power,
                - source: string identifying the source of the returned value (`FIELD_OTHER_LOAD_PW` when Jackery reports the load, otherwise `"smart_meter_net_minus_input_plus_output"`).
        None: When the smart-meter net is unavailable or Jackery input/output are both zero and no reported home load exists.
    """  # noqa: E501
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

    if meter_net is None or (jackery_input == 0.0 and jackery_output == 0.0):  # noqa: RUF069
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
    """Return the app chart-series key for one section/stat pair."""
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


def trend_series_key(section: str, stat_key: str) -> str | None:
    """Return the chart series key for app week/month/year trend payloads."""
    if not section.endswith((
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
    """Return the power-curve series key for an app ``dateType=day`` payload."""
    if not _is_day_period_payload(source, section):
        return None
    return _chart_series_key_for_stat(section, stat_key)


def trend_series_total(  # noqa: PLR0911
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> float | None:
    """Compute the period total for a statistic from a chart or stat payload.

    If the payload represents a day period, the function uses the payload's effective period total. For trend payloads it only accepts kWh units and returns the rounded sum of parsed series values when a chart-series list is present. Special cases:
    - For home-stat sections: if the server-side total equals 0.0 but any grid-series lists exist, returns 0.0.
    - For CT-stat sections: if no usable series values exist, falls back to the server-side total when available.

    Returns:
        float: The total rounded to 2 decimal places when available.
        None: When a total cannot be determined.
    """  # noqa: E501
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
            and server_total == 0.0  # noqa: RUF069
            and any(isinstance(source.get(k), list) for k in APP_HOME_GRID_SERIES_KEYS)
        ):
            return 0.0
        if section.startswith(APP_SECTION_CT_STAT) and server_total is not None:
            return round(server_total, 2)
        return None

    values = effective_trend_series_values(source, section, stat_key) or []
    valid_values = [v for v in values if v is not None]

    if not valid_values:
        server_total = effective_period_total_value(source, section, stat_key)
        if section.startswith(APP_SECTION_CT_STAT) and server_total is not None:
            return round(server_total, 2)
        return None

    return round(sum(valid_values), 2)


def trend_series_has_value(  # noqa: PLR0911
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> bool:
    """Determine whether the provided app-period payload contains a usable value for the given section and statistic key.

    Parameters:
        source (dict): The payload dictionary to inspect.
        section (str): The payload section name (e.g., a suffix like `_day`, `_week`, `_month`, `_year`).
        stat_key (str): The statistic key to test for presence or derivation.

    Returns:
        bool: `True` if a usable value is available for the section and stat key, `False` otherwise.
    """  # noqa: E501
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
            and server_total == 0.0  # noqa: RUF069
            and any(isinstance(source.get(k), list) for k in APP_HOME_GRID_SERIES_KEYS)
        ):
            return True
        return bool(
            section.startswith(APP_SECTION_CT_STAT) and server_total is not None,
        )

    if any(safe_float(item) is not None for item in series):
        return True

    return bool(
        section.startswith(APP_SECTION_CT_STAT)
        and safe_float(source.get(stat_key)) is not None,
    )


def task_plan_value(task_plan: dict[str, Any], *keys: str) -> Any:  # noqa: ANN401
    """Read a value from the task-plan shapes documented in PROTOCOL.md §3-§5."""
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
    """Return True when a trend payload can produce a period sensor value."""
    if trend_series_total(source, section, stat_key) is not None:
        return True
    return safe_float(source.get(stat_key)) is not None
