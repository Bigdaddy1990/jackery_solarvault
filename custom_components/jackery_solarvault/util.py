"""Shared helpers for Jackery SolarVault entities."""

import calendar
import contextlib
from datetime import UTC, date, datetime, timedelta
import json
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
    DATA_QUALITY_REASON_YEAR_LESS_THAN_MONTH,
    DATA_QUALITY_REASON_YEAR_LESS_THAN_WEEK,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DEFAULT_ENABLE_UNREDACTED_DIAGNOSTICS,
    FIELD_CURRENT_VERSION,
    FIELD_DEV_SN,
    FIELD_DEVICE_SN,
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
    REDACT_KEYS,
    REDACTED_VALUE,
    TASK_PLAN_BODY,
    TASK_PLAN_TASKS,
)

# CPU-Optimierung: Regex auf Modulebene kompilieren, nicht pro Schleifendurchlauf
_DAY_CHART_MINUTE_RE = re.compile(r'\s*(\d{1,2}):(\d{2})\s*')
_DEV_MODE_ENV: str = 'JACKERY_DEV_MODE'
_DEV_MODE_CACHED: bool | None = None

def config_entry_bool_option(entry: Any, key: str, default: bool) -> bool:
    """Return a bool option with legacy setup-data fallback."""
    options = getattr(entry, 'options', {}) or {}
    data = getattr(entry, 'data', {}) or {}
    value = options.get(key)
    if value is None:
        value = data.get(key, default)
    parsed = safe_bool(value)
    return default if parsed is None else parsed


def config_entry_str_option(entry: Any, key: str, default: str) -> str:
    """Return a str option with legacy setup-data fallback."""
    options = getattr(entry, 'options', {}) or {}
    data = getattr(entry, 'data', {}) or {}
    value = options.get(key)
    if value is None:
        value = data.get(key, default)
    if value is None:
        return default
    return str(value)


def config_entry_int_option(entry: Any, key: str, default: int) -> int:
    """Return an int option with legacy setup-data fallback."""
    options = getattr(entry, 'options', {}) or {}
    data = getattr(entry, 'data', {}) or {}
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


def parse_utc_datetime(value: Any) -> datetime:
    """Parse a Jackery timestamp and normalize it to timezone-aware UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if abs(timestamp) >= 100_000_000_000:
            timestamp /= 1000
        try:
            parsed = datetime.fromtimestamp(timestamp, UTC)
        except (OSError, OverflowError, ValueError) as err:
            raise ValueError(f"invalid UTC timestamp: {value!r}") from err
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise ValueError('timestamp must not be empty')
        with contextlib.suppress(ValueError, OSError, OverflowError):
            timestamp = float(normalized)
            if abs(timestamp) >= 100_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, UTC)
        if normalized.endswith('Z'):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as err:
            raise ValueError(f"invalid UTC timestamp: {value!r}") from err
    else:
        raise ValueError(f"unsupported UTC timestamp: {value!r}")

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
        pack_count = sum(1 for item in packs if isinstance(item, dict)) if isinstance(packs, list) else 0
        meter_heads = payload.get(PAYLOAD_METER_HEADS) or []
        meter_count = sum(1 for item in meter_heads if isinstance(item, dict)) if isinstance(meter_heads, list) else 0
        
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
    entity: Any,
    *,
    platform: str,
    logger: Any,
) -> bool:
    """Append an entity once per unique_id during platform setup."""
    uid = getattr(entity, 'unique_id', None)
    if uid and uid in seen_unique_ids:
        logger.debug('Skip duplicate %s unique_id=%s', platform, uid)
        return False
    if uid:
        seen_unique_ids.add(uid)
    entities.append(entity)
    return True


def validate_app_period_date_type(date_type: str) -> str:
    """Return a supported Jackery app period type or raise ValueError."""
    if date_type not in APP_PERIOD_DATE_TYPES:
        raise ValueError(f"Unsupported Jackery app period dateType: {date_type!r}")
    return date_type


def app_period_range(date_type: str, *, today: date | None = None) -> tuple[date, date]:
    """Return the documented Jackery app begin/end range for a period."""
    date_type = validate_app_period_date_type(date_type)
    if today is None:
        today = date.today()
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
        raise ValueError(f"Jackery app period {field_name} must not be empty")
    try:
        return date.fromisoformat(normalized)
    except ValueError as err:
        raise ValueError(
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
    """Return validated ISO begin/end strings for a Jackery app request."""
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
        raise ValueError(
            'Jackery app period beginDate must be before or equal to endDate: '
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
    if month < 1 or month > 12:
        raise ValueError(f"Unsupported Jackery app month: {month!r}")
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


def safe_float(value: Any) -> float | None:
    """Convert a Jackery payload value to float, returning None on error."""
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if ',' in candidate and '.' not in candidate:
            if candidate.count(',') != 1:
                return None
            candidate = candidate.replace(',', '.')
        try:
            return float(candidate)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
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
    """Return True when the JACKERY_DEV_MODE env var is truthy. Cached for I/O performance."""
    global _DEV_MODE_CACHED
    if _DEV_MODE_CACHED is None:
        raw = os.environ.get(_DEV_MODE_ENV, '')
        _DEV_MODE_CACHED = raw.strip().lower() in {'1', 'true', 'yes', 'on'}
    return _DEV_MODE_CACHED


def diagnostic_redactions_disabled(entry: Any | None = None) -> bool:
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


def _payload_debug_redacted(value: Any, redactions_disabled: bool | None = None) -> Any:
    """Return a recursively redacted, JSON-serializable debug payload."""
    if redactions_disabled is None:
        redactions_disabled = diagnostic_redactions_disabled()
    if redactions_disabled:
        return _payload_debug_passthrough(value)
    
    if isinstance(value, dict):
        return {
            str(key): REDACTED_VALUE if str(key) in REDACT_KEYS else _payload_debug_redacted(item, redactions_disabled=redactions_disabled)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_payload_debug_redacted(item, redactions_disabled=redactions_disabled) for item in value]
    if isinstance(value, tuple):
        return [_payload_debug_redacted(item, redactions_disabled=redactions_disabled) for item in value]
    return value


def _payload_debug_passthrough(value: Any) -> Any:
    """Recursively normalise ``value`` to JSON-serializable types only."""
    if isinstance(value, dict):
        return {str(key): _payload_debug_passthrough(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_payload_debug_passthrough(item) for item in value]
    if isinstance(value, tuple):
        return [_payload_debug_passthrough(item) for item in value]
    return value


def redacted_json_safe_payload(value: Any) -> Any:
    """Return a JSON-safe payload with sensitive Jackery fields redacted."""
    return _payload_debug_redacted(value, redactions_disabled=False)


def active_redact_keys(entry: Any | None = None) -> frozenset[str]:
    """Return ``REDACT_KEYS`` or an empty set depending on dev-mode switches."""
    if diagnostic_redactions_disabled(entry):
        return frozenset()
    return frozenset(REDACT_KEYS)


def chart_series_debug(source: Any) -> dict[str, Any]:
    """Return raw/parsed chart-series diagnostics for app payload arrays."""
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
                'index': index,
                'raw': raw,
                'raw_type': type(raw).__name__,
                'parsed_float': parsed,
            })
            if parsed is not None:
                total += parsed
                found = True
        result[key] = {
            'raw_count': len(series),
            'parsed_sum': round(total, 5) if found else None,
            'items': parsed_items,
        }
    if isinstance(source.get(APP_CHART_LABELS), list):
        result['labels'] = source.get(APP_CHART_LABELS)
    if isinstance(source.get(APP_REQUEST_META), dict):
        result['request'] = source.get(APP_REQUEST_META)
    return result


def append_payload_debug_line(
    path: str | Path,
    event: dict[str, Any],
    redactions_disabled: bool | None = None,
) -> None:
    """Append one redacted JSONL diagnostic event and rotate at a small limit."""
    debug_path = Path(path)
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    if debug_path.exists() and debug_path.stat().st_size > PAYLOAD_DEBUG_LOG_MAX_BYTES:
        backup = debug_path.with_name(debug_path.name + PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX)
        with contextlib.suppress(OSError):
            backup.unlink()
        with contextlib.suppress(OSError):
            debug_path.replace(backup)
    redacted = _payload_debug_redacted(event, redactions_disabled=redactions_disabled)
    with debug_path.open('a', encoding='utf-8') as file:
        file.write(
            json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
        )
        file.write('\n')


def safe_bool(value: Any) -> bool | None:
    """Convert a Jackery payload value to bool, returning None when unknown."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        val = value.strip().lower()
        if val in {'1', 'true', 'on', 'yes'}:
            return True
        if val in {'0', 'false', 'off', 'no'}:
            return False
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return None


def smart_plug_serial(plug: Any) -> str | None:
    """Return the deviceSn for a smart-plug subdevice payload, or None."""
    if not isinstance(plug, dict):
        return None
    raw = plug.get(FIELD_DEVICE_SN) or plug.get(FIELD_DEV_SN) or plug.get(FIELD_SN)
    if raw is None:
        return None
    serial = str(raw).strip()
    return serial or None


def sorted_smart_plugs(plugs: Any) -> list[dict[str, Any]]:
    """Return plug entries sorted by serial, dropping entries without one."""
    if not isinstance(plugs, list):
        return []
    entries: list[tuple[str, dict[str, Any]]] = []
    for entry in plugs:
        sn = smart_plug_serial(entry)
        if sn is None:
            continue
        entries.append((sn, entry))
    entries.sort(key=lambda item: item[0])
    return [entry for _, entry in entries]


def jackery_online_state(value: Any) -> bool | None:
    """Return a parsed Jackery online/offline marker, or None when unknown."""
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'online', 'connected', 'available'}:
            return True
        if normalized in {'offline', 'disconnected', 'unavailable'}:
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
            DATA_QUALITY_KEY_REFERENCE_SECTION: self.reference_section,
            DATA_QUALITY_KEY_REFERENCE_VALUE: self.reference_value,
        }
        if self.source_request is not None:
            payload[DATA_QUALITY_KEY_SOURCE_REQUEST] = dict(self.source_request)
        if self.reference_request is not None:
            payload[DATA_QUALITY_KEY_REFERENCE_REQUEST] = dict(self.reference_request)
        if self.source_chart_series_key is not None:
            payload[DATA_QUALITY_KEY_SOURCE_CHART_SERIES_KEY] = self.source_chart_series_key
        if self.total_method is not None:
            payload[DATA_QUALITY_KEY_TOTAL_METHOD] = self.total_method
        return payload


def normalized_data_quality_warnings(
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return deterministic, de-duplicated data-quality warnings."""
    deduped: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        key = (
            str(warning.get(DATA_QUALITY_KEY_REASON) or ''),
            str(warning.get(DATA_QUALITY_KEY_METRIC_KEY) or ''),
            str(warning.get(DATA_QUALITY_KEY_SOURCE_SECTION) or ''),
            str(warning.get(DATA_QUALITY_KEY_SOURCE_VALUE) or ''),
            str(warning.get(DATA_QUALITY_KEY_REFERENCE_SECTION) or ''),
            str(warning.get(DATA_QUALITY_KEY_REFERENCE_VALUE) or ''),
        )
        deduped.setdefault(key, dict(warning))
    return [deduped[key] for key in sorted(deduped)]


def _format_request_range(request: Any) -> str | None:
    """Return a compact dateType/range summary for diagnostics messages."""
    if not isinstance(request, dict):
        return None
    date_type = request.get(APP_REQUEST_DATE_TYPE) or request.get(APP_REQUEST_DATE_TYPE_ALT)
    begin = request.get(APP_REQUEST_BEGIN_DATE) or request.get(APP_REQUEST_BEGIN_DATE_ALT)
    end = request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT)
    if not date_type and not begin and not end:
        return None
    if begin or end:
        return f"{date_type or 'unknown'} {begin or '?'}..{end or '?'}"
    return str(date_type)


def format_data_quality_warning(warning: dict[str, Any]) -> str:
    """Return a compact, deterministic repair/diagnostic warning example."""
    metric = warning.get(DATA_QUALITY_KEY_LABEL) or warning.get(DATA_QUALITY_KEY_METRIC_KEY) or 'unknown'
    source_section = warning.get(DATA_QUALITY_KEY_SOURCE_SECTION) or 'unknown'
    source_value = warning.get(DATA_QUALITY_KEY_SOURCE_VALUE)
    reference_section = warning.get(DATA_QUALITY_KEY_REFERENCE_SECTION) or 'unknown'
    reference_value = warning.get(DATA_QUALITY_KEY_REFERENCE_VALUE)
    source_text = 'unknown' if source_value is None else str(source_value)
    reference_text = 'unknown' if reference_value is None else str(reference_value)
    
    text = f"{metric}: {source_section}={source_text} < {reference_section}={reference_text}"
    source_request = _format_request_range(warning.get(DATA_QUALITY_KEY_SOURCE_REQUEST))
    reference_request = _format_request_range(warning.get(DATA_QUALITY_KEY_REFERENCE_REQUEST))
    
    if source_request or reference_request:
        text += f" [{source_section}: {source_request or 'unknown'}; {reference_section}: {reference_request or 'unknown'}]"
    return text


def app_data_quality_warnings(
    payload: dict[str, Any],
    *,
    today: date | None = None,
    tolerance: float = 0.05,
) -> list[AppDataQualityWarning]:
    """Return non-mutating warnings for contradictory app statistics."""
    if today is None:
        today = date.today()
    week_begin, week_end = app_period_range(DATE_TYPE_WEEK, today=today)
    week_inside_current_month = (
        week_begin.year == today.year
        and week_end.year == today.year
        and week_begin.month == today.month
        and week_end.month == today.month
    )
    week_inside_current_year = (week_begin.year == today.year and week_end.year == today.year)

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

    def _add_warning(
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
                source_chart_series_key=_chart_series_key_for_section(source_section, stat_key),
                reference_chart_series_key=_chart_series_key_for_section(reference_section, stat_key),
                total_method='chart_series_sum' if source_section != PAYLOAD_STATISTIC else None,
            )
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
        if week_inside_current_year and year is not None and week is not None and year + tolerance < week:
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
        if week_inside_current_month and month is not None and week is not None and month + tolerance < week:
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
        year_generation = _period_total(APP_SECTION_PV_STAT, DATE_TYPE_YEAR, APP_STAT_TOTAL_SOLAR_ENERGY)
        if lifetime_generation is not None and year_generation is not None and lifetime_generation + tolerance < year_generation:
            _add_warning(
                reason=DATA_QUALITY_REASON_LIFETIME_LESS_THAN_YEAR,
                metric_key='pv_energy',
                label='PV energy',
                stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
                source_section=PAYLOAD_STATISTIC,
                source_value=lifetime_generation,
                reference_section=_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR),
                reference_value=year_generation,
            )

    return warnings


def statistic_id_part(value: Any) -> str:
    """Return a Home-Assistant-safe external statistic id component."""
    text = str(value or '').strip().lower()
    text = re.sub(r'[^a-z0-9_]+', '_', text)
    text = re.sub(r'_+', '_', text).strip('_')
    return text or 'unknown'


def external_trend_statistic_id(
    domain: str,
    device_id: str,
    metric_key: str,
    bucket: str,
) -> str:
    """Build the external statistics id used for app chart imports."""
    return (
        f"{domain}:"
        f"{statistic_id_part(device_id)}_"
        f"{statistic_id_part(metric_key)}_"
        f"{statistic_id_part(bucket)}"
    )


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _trend_date_type(section: str, source: dict[str, Any]) -> str | None:
    request = source.get(APP_REQUEST_META)
    if isinstance(request, dict):
        date_type = request.get(APP_REQUEST_DATE_TYPE) or request.get(APP_REQUEST_DATE_TYPE_ALT)
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
    if section.endswith((f"_{DATE_TYPE_WEEK}", f"_{DATE_TYPE_MONTH}", f"_{DATE_TYPE_YEAR}")):
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


def _compact_year_parts(value: Any) -> tuple[float, float] | None:
    """Return previous/current-month parts for a candidate Jackery compact year value."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(',', '.')
    if not text:
        return None
    
    sign = -1.0 if text.startswith('-') else 1.0
    unsigned = text[1:] if text.startswith('-') else text
    if '.' not in unsigned:
        parsed = safe_float(value)
        return None if parsed is None else (0.0, parsed)
        
    whole_text, fraction_text = unsigned.split('.', 1)
    if not whole_text:
        whole_text = '0'
    if not whole_text.isdigit() or not fraction_text.isdigit():
        parsed = safe_float(value)
        return None if parsed is None else (0.0, parsed)
        
    whole = sign * float(int(whole_text))
    fraction = sign * float(int(fraction_text)) if int(fraction_text) else 0.0
    
    if fraction == 0.0:
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
        
    return [0.0 if (val := safe_float(raw)) is None else round(val, 5) for raw in series]


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
        if abs(safe_float(value) or 0.0) > 0.00001
    ]


def year_payload_appears_current_month_only(
    source: dict[str, Any],
    section: str,
    stat_keys: tuple[str, ...],
    *,
    current_month: int,
) -> bool:
    """Return True when a year payload looks like the app's month-only bug."""
    if current_month <= 1:
        return False
    unit = str(source.get(APP_STAT_UNIT) or '').strip().lower()
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
    revenue = safe_float(source.get('totalSolarRevenue'))
    if revenue is not None:
        return revenue
    profit = safe_float(source.get('pvProfit'))
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
        if configured is not None and 0 <= configured <= 10:
            return configured, f"{PAYLOAD_PRICE}.{FIELD_SINGLE_PRICE}"

    if year_generation is not None and year_generation > 0 and year_revenue is not None:
        derived = year_revenue / year_generation
        if 0 <= derived <= 10:
            return round(derived, 5), 'pv_year_revenue_per_kwh'
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
        corrected = backfill.get('corrected')
        if isinstance(corrected, dict):
            revenue_meta = corrected.get('totalSolarRevenue')
            if isinstance(revenue_meta, dict):
                for key in ('raw_total', 'corrected_total'):
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


def _calculated_savings_from_year(
    payload: dict[str, Any],
    *,
    year_generation: float | None,
    year_revenue: float | None,
) -> dict[str, Any] | None:
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
    method_prefix = 'device_grid_side_output'
    if device_input is not None:
        delivered_ac = max(0.0, delivered_ac - max(0.0, device_input))
        method_prefix = 'device_grid_side_net_output'
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
        'method': method,
        'calculated_total': calculated_total,
        'energy_kwh': round(savings_energy, 2),
        'price': round(price, 5),
        'price_source': price_source,
        'source_energy': {
            'pv_year_kwh': _round_stat_value(year_generation),
            'device_grid_side_input_year_kwh': _round_stat_value(device_input),
            'device_grid_side_output_year_kwh': _round_stat_value(device_output),
            'device_grid_side_net_output_year_kwh': _round_stat_value(net_device_output),
            'savings_basis_ac_year_kwh': _round_stat_value(delivered_ac),
            'home_consumption_year_kwh': _round_stat_value(home_consumption),
            'ct_public_export_year_kwh': _round_stat_value(public_export),
            'battery_charge_year_kwh': _round_stat_value(battery_charge),
            'battery_discharge_year_kwh': _round_stat_value(battery_discharge),
            'battery_charge_discharge_gap_kwh': _round_stat_value(battery_gap),
            'conversion_loss_year_kwh': _round_stat_value(conversion_loss_energy),
            'conversion_loss_year_kwh_signed': _round_stat_value(conversion_loss_energy_signed),
            'pv_residual_after_self_consumption_year_kwh': _round_stat_value(pv_residual_after_self_consumption_energy),
            'pv_not_savings_ac_energy_kwh': _round_stat_value(pv_residual_after_self_consumption_energy),
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
        return True, 'missing_cloud_total_revenue'

    tolerance = _tolerance_for_values(raw_revenue, calculated_revenue)
    if abs(raw_revenue - calculated_revenue) <= tolerance:
        return True, 'cloud_total_matches_calculated_savings'
    if calculated_revenue > raw_revenue + tolerance:
        return True, 'cloud_total_below_current_year_savings'

    has_prior_lifetime_generation = (
        raw_generation is not None
        and year_generation is not None
        and raw_generation > year_generation + _tolerance_for_values(raw_generation, year_generation)
    )
    if not has_prior_lifetime_generation and _matches_pv_revenue_shape(raw_revenue, pv_revenue_candidates):
        return True, 'cloud_total_matches_pv_revenue_not_savings'

    return False, 'cloud_total_higher_than_current_year_savings'


def _backfill_pv_revenue(
    out: dict[str, Any],
    year_source: dict[str, Any],
    month_sources: dict[int, dict[str, Any]],
    meta: dict[str, Any],
) -> None:
    revenue_values = [0.0 for _ in range(12)]
    found_months: list[int] = []
    for month, month_source in sorted(month_sources.items()):
        if month < 1 or month > 12:
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
    if raw_total is not None and monthly_total <= raw_total + _tolerance_for_values(raw_total, monthly_total):
        return

    out['totalSolarRevenue'] = monthly_total
    out['pvProfit'] = round(monthly_total * 10_000_000, 1)
    out[APP_CHART_SERIES_Y6] = [round(value * 10_000_000, 1) for value in revenue_values]
    meta.setdefault('corrected', {})['totalSolarRevenue'] = {
        'raw_total': raw_total,
        'corrected_total': monthly_total,
        'months': found_months,
    }


def backfill_year_payload_from_months(
    year_source: dict[str, Any],
    section_prefix: str,
    stat_keys: tuple[str, ...],
    month_sources: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Return a year payload guarded by explicit monthly app payloads."""
    if not isinstance(year_source, dict) or not month_sources:
        return year_source

    year_section = _period_section(section_prefix, DATE_TYPE_YEAR)
    month_section = _period_section(section_prefix, DATE_TYPE_MONTH)
    unit = str(year_source.get(APP_STAT_UNIT) or '').strip().lower()
    if unit and unit != APP_UNIT_KWH:
        return year_source

    out = dict(year_source)
    out.setdefault(APP_CHART_LABELS, [str(month) for month in range(1, 13)])
    meta: dict[str, Any] = {
        'method': 'same_endpoint_month_sum',
        'source_period': DATE_TYPE_MONTH,
        'target_period': DATE_TYPE_YEAR,
    }

    for stat_key in stat_keys:
        series_key = trend_series_key(year_section, stat_key)
        if not series_key:
            continue

        monthly_values = [0.0 for _ in range(12)]
        found_months: list[int] = []
        for month, month_source in sorted(month_sources.items()):
            if month < 1 or month > 12:
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
        if raw_total is not None and monthly_total <= raw_total + _tolerance_for_values(raw_total, monthly_total):
            continue

        out[series_key] = monthly_values
        out[stat_key] = monthly_total
        if stat_key == APP_STAT_TOTAL_SOLAR_ENERGY:
            out['pvEgy'] = monthly_total
        elif stat_key == APP_STAT_TOTAL_IN_GRID_ENERGY:
            out['inOngridEgy'] = monthly_total
        elif stat_key == APP_STAT_TOTAL_OUT_GRID_ENERGY:
            out['outOngridEgy'] = monthly_total
        elif stat_key == APP_STAT_TOTAL_DISCHARGE:
            out['batOtGridEgy'] = monthly_total
            
        meta.setdefault('corrected', {})[stat_key] = {
            'raw_total': raw_total,
            'corrected_total': monthly_total,
            'series_key': series_key,
            'months': found_months,
        }

    if section_prefix in {APP_SECTION_PV_STAT, APP_SECTION_PV_TRENDS}:
        _backfill_pv_revenue(out, year_source, month_sources, meta)

    if 'corrected' not in meta:
        return year_source
    out[APP_YEAR_BACKFILL_META] = meta
    return out


def apply_year_month_backfill(
    payload: dict[str, Any],
    month_history: dict[str, dict[int, dict[str, Any]]],
) -> None:
    """Apply same-endpoint month backfill to known year statistic sections."""
    section_metrics: tuple[tuple[str, tuple[str, ...]], ...] = (
        (APP_SECTION_PV_STAT, (APP_STAT_TOTAL_SOLAR_ENERGY, APP_STAT_PV1_ENERGY, APP_STAT_PV2_ENERGY, APP_STAT_PV3_ENERGY, APP_STAT_PV4_ENERGY)),
        (APP_SECTION_HOME_STAT, (APP_STAT_TOTAL_IN_GRID_ENERGY, APP_STAT_TOTAL_OUT_GRID_ENERGY)),
        (APP_SECTION_BATTERY_STAT, (APP_STAT_TOTAL_CHARGE, APP_STAT_TOTAL_DISCHARGE)),
        (APP_SECTION_HOME_TRENDS, (APP_STAT_TOTAL_HOME_ENERGY,)),
        (APP_SECTION_PV_TRENDS, (APP_STAT_TOTAL_SOLAR_ENERGY,)),
        (APP_SECTION_BATTERY_TRENDS, (APP_STAT_TOTAL_TREND_CHARGE_ENERGY, APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY)),
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


def guard_statistic_totals_from_year(
    payload: dict[str, Any],
    *,
    previous_statistic: dict[str, Any] | None = None,
) -> None:
    """Guard app total KPIs with corrected current-year period values."""
    statistic = payload.get(PAYLOAD_STATISTIC)
    if not isinstance(statistic, dict):
        return

    previous_generation = safe_float(previous_statistic.get(APP_STAT_TOTAL_GENERATION)) if isinstance(previous_statistic, dict) else None
    raw_generation = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
    pv_year = payload.get(_period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR))
    
    if not isinstance(pv_year, dict):
        if previous_generation is None or (raw_generation is not None and previous_generation <= raw_generation + _tolerance_for_values(raw_generation, previous_generation)):
            return
        out = dict(statistic)
        out[APP_STAT_TOTAL_GENERATION] = round(previous_generation, 2)
        out[APP_TOTAL_GUARD_META] = {
            'method': 'previous_total_lower_bound',
            'corrected': {
                APP_STAT_TOTAL_GENERATION: {
                    'raw_total': raw_generation,
                    'corrected_total': round(previous_generation, 2),
                    'previous_total': previous_generation,
                }
            },
        }
        payload[PAYLOAD_STATISTIC] = out
        return

    year_generation = effective_period_total_value(pv_year, _period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR), APP_STAT_TOTAL_SOLAR_ENERGY)
    year_revenue = _pv_revenue_value(pv_year)
    savings = _calculated_savings_from_year(payload, year_generation=year_generation, year_revenue=year_revenue)
    
    if year_generation is None and year_revenue is None and savings is None:
        return

    out = dict(statistic)
    meta: dict[str, Any] = {
        'method': 'current_year_lower_bound',
        'source_section': _period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR),
    }

    generation_candidates = [val for val in (year_generation, previous_generation) if val is not None]
    corrected_generation = max(generation_candidates) if generation_candidates else None
    
    if corrected_generation is not None and (raw_generation is None or corrected_generation > raw_generation + _tolerance_for_values(raw_generation, corrected_generation)):
        out[APP_STAT_TOTAL_GENERATION] = round(corrected_generation, 2)
        meta.setdefault('corrected', {})[APP_STAT_TOTAL_GENERATION] = {
            'raw_total': raw_generation,
            'corrected_total': round(corrected_generation, 2),
            'current_year_total': year_generation,
            'previous_total': previous_generation,
        }

    raw_revenue = safe_float(statistic.get(APP_STAT_TOTAL_REVENUE))
    if savings is not None:
        calculated_revenue = safe_float(savings.get('calculated_total'))
        if calculated_revenue is not None:
            candidates = _pv_revenue_candidates(pv_year, year_revenue=year_revenue, raw_generation=raw_generation, price=safe_float(savings.get('price')))
            publish_calculated, reason = _savings_publish_decision(
                raw_revenue=raw_revenue,
                calculated_revenue=calculated_revenue,
                raw_generation=raw_generation,
                year_generation=year_generation,
                pv_revenue_candidates=candidates,
            )
            savings.update({
                'raw_cloud_total': raw_revenue,
                'pv_revenue_candidates': candidates,
                'decision': reason,
                'would_replace_cloud_total': publish_calculated,
                'published_value': raw_revenue,
                'published_value_source': 'cloud_total'
            })
            out[APP_SAVINGS_CALC_META] = savings

    raw_carbon = safe_float(statistic.get(APP_STAT_TOTAL_CARBON))
    if year_generation is not None and raw_generation is not None and raw_generation > 0 and raw_carbon is not None:
        factor = raw_carbon / raw_generation
        corrected_carbon = round(year_generation * factor, 2)
        if 0 < factor < 5 and corrected_carbon > raw_carbon + _tolerance_for_values(raw_carbon, corrected_carbon):
            out[APP_STAT_TOTAL_CARBON] = corrected_carbon
            meta.setdefault('corrected', {})[APP_STAT_TOTAL_CARBON] = {
                'raw_total': raw_carbon,
                'corrected_total': corrected_carbon,
                'kg_per_kwh': round(factor, 5),
            }

    if 'corrected' in meta:
        out[APP_TOTAL_GUARD_META] = meta
    
    if 'corrected' in meta or APP_SAVINGS_CALC_META in out:
        payload[PAYLOAD_STATISTIC] = out


def compact_json(value: Any) -> str:
    """Return compact JSON for diagnostic attributes."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def trend_series_points(
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    today: date | None = None,
) -> list[TrendStatisticPoint]:
    """Return app chart buckets as dated statistic points."""
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return []
    unit = str(source.get(APP_STAT_UNIT) or '').strip().lower()
    if unit and unit != APP_UNIT_KWH:
        return []
    series = effective_trend_series_values(source, section, stat_key)
    if not isinstance(series, list) or not series:
        return []

    request = source.get(APP_REQUEST_META)
    begin = None
    end = None
    if isinstance(request, dict):
        begin = _parse_iso_date(request.get(APP_REQUEST_BEGIN_DATE) or request.get(APP_REQUEST_BEGIN_DATE_ALT))
        end = _parse_iso_date(request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT))
        
    date_type = _trend_date_type(section, source)
    if begin is None:
        return []
    if today is None:
        today = date.today()

    points: list[TrendStatisticPoint] = []
    for index, value in enumerate(series):
        if value is None:
            continue
        if date_type == DATE_TYPE_YEAR:
            month = index + 1
            if month < 1 or month > 12:
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


def _parse_day_chart_minute(value: Any) -> int | None:
    """Parse an app day-chart label into minutes after local midnight."""
    if not isinstance(value, str):
        return None
    match = _DAY_CHART_MINUTE_RE.fullmatch(value)
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour == 24 and minute == 0:
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
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


def day_power_energy_points(
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    bucket_minutes: int = 60,
    today: date | None = None,
    now: datetime | None = None,
) -> list[TrendStatisticPoint]:
    """Convert an app day chart curve into kWh statistic buckets."""
    if bucket_minutes <= 0 or 24 * 60 % bucket_minutes != 0:
        return []
    series_key = day_power_series_key(source, section, stat_key)
    if not series_key:
        return []
    unit = str(source.get(APP_STAT_UNIT) or '').strip().lower()
    if unit and unit not in {'w', APP_UNIT_KWH}:
        return []
    series = source.get(series_key)
    if not isinstance(series, list) or not series:
        return []

    request = source.get(APP_REQUEST_META)
    begin = None
    end = None
    if isinstance(request, dict):
        begin = _parse_iso_date(request.get(APP_REQUEST_BEGIN_DATE) or request.get(APP_REQUEST_BEGIN_DATE_ALT))
        end = _parse_iso_date(request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT))
        
    if begin is None or (end is not None and begin > end):
        return []
    if today is None:
        today = date.today()
    if begin > today:
        return []
    if now is None:
        now = datetime.now()

    labels = source.get(APP_CHART_LABELS)
    parsed_labels = labels if isinstance(labels, list) else None
    current_day_limit_minute = now.hour * 60 + now.minute if begin == now.date() else 24 * 60 - 1

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
        max_bucket_minute = bucket_minute if max_bucket_minute is None else max(max_bucket_minute, bucket_minute)
        sample_kwh = max(sample_value, 0.0) if unit == APP_UNIT_KWH else max(sample_value, 0.0) * 5 / 60 / 1000
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
            target_index = next((idx for idx in range(len(rounded_values) - 1, -1, -1) if rounded_values[idx] > 0), len(rounded_values) - 1)
            rounded_values[target_index] = round(max(rounded_values[target_index] + diff, 0.0), 5)

    return [
        TrendStatisticPoint(
            datetime(begin.year, begin.month, begin.day, minute // 60, minute % 60),
            bucket_value
        )
        for (minute, _value), bucket_value in zip(bucket_items, rounded_values, strict=False)
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
    total = directional_power_value(ct, (CT_TOTAL_POWER_PAIR[0],), (CT_TOTAL_POWER_PAIR[1],))
    if total is not None:
        return total
    phases = signed_phase_power_values(ct)
    return sum(phases) if phases is not None else None


def calculated_smart_meter_power(
    ct: dict[str, Any],
    calculation: str,
) -> float | None:
    """Calculate derived CT powers from signed phase values."""
    net = smart_meter_net_power(ct)
    phases = signed_phase_power_values(ct)
    
    if calculation == 'net_import':
        return None if net is None else max(net, 0.0)
    if calculation == 'net_export':
        return None if net is None else max(-net, 0.0)
        
    if phases is None:
        return None
        
    if calculation == 'gross_import':
        return sum(max(value, 0.0) for value in phases)
    if calculation == 'gross_export':
        return sum(max(-value, 0.0) for value in phases)
    if calculation == 'gross_flow':
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
    return first_power_value(props, FIELD_OTHER_LOAD_PW, FIELD_HOME_LOAD_PW, FIELD_LOAD_PW)


def jackery_grid_side_input_power(props: dict[str, Any]) -> float | None:
    """AC power drawn by Jackery from the grid/home side."""
    return first_power_value(props, FIELD_IN_ONGRID_PW, FIELD_GRID_IN_PW, FIELD_IN_GRID_SIDE_PW)


def jackery_grid_side_output_power(props: dict[str, Any]) -> float | None:
    """AC power supplied by Jackery to the grid/home side."""
    return first_power_value(props, FIELD_OUT_ONGRID_PW, FIELD_GRID_OUT_PW, FIELD_OUT_GRID_SIDE_PW)


def jackery_corrected_home_consumption_power(
    ct: dict[str, Any],
    props: dict[str, Any],
) -> HomeConsumptionPower | None:
    """Return live home load and its diagnostic components."""
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

    if meter_net is None or (jackery_input == 0.0 and jackery_output == 0.0):
        return None

    calculated = meter_net - jackery_input + jackery_output
    return HomeConsumptionPower(
        value=max(calculated, 0.0),
        smart_meter_net_power=meter_net,
        jackery_input_power=jackery_input,
        jackery_output_power=jackery_output,
        source='smart_meter_net_minus_input_plus_output',
    )


# ---------------------------------------------------------------------------
# Trend/statistic helpers
# ---------------------------------------------------------------------------
def _chart_series_key_for_stat(section: str, stat_key: str) -> str | None:
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
        if stat_key == APP_STAT_TOTAL_IN_GRID_ENERGY: return APP_CHART_SERIES_Y1
        if stat_key == APP_STAT_TOTAL_OUT_GRID_ENERGY: return APP_CHART_SERIES_Y2
        
    if section.startswith(APP_SECTION_CT_STAT):
        if stat_key == APP_STAT_TOTAL_CT_INPUT_ENERGY: return APP_CHART_SERIES_Y1
        if stat_key == APP_STAT_TOTAL_CT_OUTPUT_ENERGY: return APP_CHART_SERIES_Y2
        
    if section.startswith(APP_SECTION_BATTERY_TRENDS):
        if stat_key == APP_STAT_TOTAL_TREND_CHARGE_ENERGY: return APP_CHART_SERIES_Y1
        if stat_key == APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY: return APP_CHART_SERIES_Y2
        
    if section.startswith(APP_SECTION_BATTERY_STAT):
        if stat_key == APP_STAT_TOTAL_CHARGE: return APP_CHART_SERIES_Y1
        if stat_key == APP_STAT_TOTAL_DISCHARGE: return APP_CHART_SERIES_Y2
        
    return None


def trend_series_key(section: str, stat_key: str) -> str | None:
    """Return the chart series key for app week/month/year trend payloads."""
    if not section.endswith((f"_{DATE_TYPE_WEEK}", f"_{DATE_TYPE_MONTH}", f"_{DATE_TYPE_YEAR}")):
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


def trend_series_total(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> float | None:
    """Return the app period total for a chart/stat payload."""
    if _is_day_period_payload(source, section):
        total = effective_period_total_value(source, section, stat_key)
        return round(total, 2) if total is not None else None

    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None
        
    unit = str(source.get(APP_STAT_UNIT) or '').strip().lower()
    if unit and unit != APP_UNIT_KWH:
        return None
        
    series = source.get(series_key)
    if not isinstance(series, list):
        server_total = effective_period_total_value(source, section, stat_key)
        if section.startswith(APP_SECTION_HOME_STAT) and server_total == 0.0 and any(isinstance(source.get(k), list) for k in APP_HOME_GRID_SERIES_KEYS):
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


def trend_series_has_value(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> bool:
    """Return True when an app period payload can produce a usable value."""
    if _is_day_period_payload(source, section):
        return safe_float(source.get(stat_key)) is not None

    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return False
        
    unit = str(source.get(APP_STAT_UNIT) or '').strip().lower()
    if unit and unit != APP_UNIT_KWH:
        return False
        
    series = source.get(series_key)
    if not isinstance(series, list):
        server_total = effective_period_total_value(source, section, stat_key)
        if section.startswith(APP_SECTION_HOME_STAT) and server_total == 0.0 and any(isinstance(source.get(k), list) for k in APP_HOME_GRID_SERIES_KEYS):
            return True
        return bool(section.startswith(APP_SECTION_CT_STAT) and server_total is not None)
        
    if any(safe_float(item) is not None for item in series):
        return True
        
    return bool(section.startswith(APP_SECTION_CT_STAT) and safe_float(source.get(stat_key)) is not None)


def task_plan_value(task_plan: dict[str, Any], *keys: str) -> Any:
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

