"""Shared helpers for Jackery SolarVault entities."""

from __future__ import annotations

import calendar
import contextlib
from datetime import UTC, date, datetime, timedelta
import json
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
    PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX,
    PAYLOAD_DEBUG_LOG_MAX_BYTES,
    PAYLOAD_PRICE,
    PAYLOAD_STATISTIC,
    REDACT_KEYS,
    REDACTED_VALUE,
    TASK_PLAN_BODY,
    TASK_PLAN_TASKS,
)


def config_entry_bool_option(entry: Any, key: str, default: bool) -> bool:
    """Return a boolean config-entry option with setup-data fallback."""
    return bool(entry.options.get(key, entry.data.get(key, default)))


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp.

    Home Assistant deprecated ``dt_util.utcnow()``; keeping UTC stamping here
    gives coordinator lifecycle code one non-deprecated source of truth.
    """
    return datetime.now(UTC)


def parse_utc_datetime(value: str) -> datetime:
    """Parse an ISO timestamp and normalize legacy naive values to UTC."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def append_unique_entity(
    entities: list[Any],
    seen_unique_ids: set[str],
    entity: Any,
    *,
    platform: str,
    logger: Any,
) -> bool:
    """Append an entity once per unique_id during platform setup.

    Several platforms build entities from the same mixed REST/MQTT/app payload.
    Duplicates should be skipped deterministically without spreading platform-
    local helper copies across every entity file. The unique_id itself remains
    owned by JackeryEntity so this only guards the setup batch.
    """
    uid = getattr(entity, "unique_id", None)
    if uid and uid in seen_unique_ids:
        logger.debug("Skip duplicate %s unique_id=%s", platform, uid)
        return False
    if uid:
        seen_unique_ids.add(uid)
    entities.append(entity)
    return True


def validate_app_period_date_type(date_type: str) -> str:
    """Return a supported Jackery app period type or raise ValueError.

    Silent fallback to ``day`` would make malformed callers issue a plausible
    but wrong cloud request. Keep invalid values loud so request-contract bugs
    are caught during development instead of producing day-like statistics.
    """
    if date_type not in APP_PERIOD_DATE_TYPES:
        raise ValueError(f"Unsupported Jackery app period dateType: {date_type!r}")
    return date_type


def app_period_range(date_type: str, *, today: date | None = None) -> tuple[date, date]:
    """Return the documented Jackery app begin/end range for a period.

    APP_POLLING_MQTT.md requires explicit full ranges for period endpoints:
    day=today..today, week=Monday..Sunday, month=first..last day,
    year=Jan 1..Dec 31. Keeping this in one helper prevents the REST client
    and coordinator diagnostics from drifting apart.
    """
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
    """Return validated ISO begin/end strings for a Jackery app request.

    API callers may override one side explicitly, but missing sides are filled
    from the same documented period contract used by coordinator diagnostics.
    Invalid manual bounds stay loud so the integration never sends plausible but
    wrong chart/statistic ranges to the cloud.
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
        raise ValueError(
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


def safe_float(value: Any) -> float | None:
    """Convert a Jackery payload value to float, returning None on error.

    The Jackery cloud normally returns numeric JSON numbers or dot-decimal
    strings. Some app-facing payloads/exports can use a locale decimal comma
    (``"40,96"``). Treat that as ``40.96`` and never strip the comma away;
    removing it would turn 40.96 kWh into 4096 kWh. Thousands separators are not
    accepted because the Jackery app protocol docs do not define them.
    """
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
    except TypeError, ValueError:
        return None


def safe_int(value: Any) -> int | None:
    """Convert a Jackery payload value to int, returning None on error.

    Handles numeric strings that were written with a decimal point
    ("8.0" -> 8) so that downstream enum lookups still succeed.
    """
    if value is None:
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        try:
            return int(float(value))
        except TypeError, ValueError:
            return None


def _payload_debug_redacted(value: Any) -> Any:
    """Return a recursively redacted, JSON-serializable debug payload."""
    if isinstance(value, dict):
        return {
            str(key): REDACTED_VALUE
            if str(key) in REDACT_KEYS
            else _payload_debug_redacted(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_payload_debug_redacted(item) for item in value]
    if isinstance(value, tuple):
        return [_payload_debug_redacted(item) for item in value]
    return value


def chart_series_debug(source: Any) -> dict[str, Any]:
    """Return raw/parsed chart-series diagnostics for app payload arrays.

    This exists to avoid the ambiguous HA UI representation where comma is both
    list separator and decimal separator, e.g. ``0, 0, 14,84, 0``. Each item is
    logged with raw value, Python type and parsed float so parser bugs are
    visible without guessing.
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


def append_payload_debug_line(path: str | Path, event: dict[str, Any]) -> None:
    """Append one redacted JSONL diagnostic event and rotate at a small limit."""
    debug_path = Path(path)
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    if debug_path.exists() and debug_path.stat().st_size > PAYLOAD_DEBUG_LOG_MAX_BYTES:
        backup = debug_path.with_name(debug_path.name + PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX)
        with contextlib.suppress(OSError):
            backup.unlink()
        with contextlib.suppress(OSError):
            debug_path.replace(backup)
    redacted = _payload_debug_redacted(event)
    with debug_path.open("a", encoding="utf-8") as file:
        file.write(
            json.dumps(redacted, ensure_ascii=False, sort_keys=True, default=str)
        )
        file.write("\n")


def safe_bool(value: Any) -> bool | None:
    """Convert a Jackery payload value to bool, returning None when unknown.

    Accepts bool, int, float, and common string truthy markers. Mirrors the
    logic that has been duplicated across switch.py and binary_sensor.py.
    """
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
    except TypeError, ValueError:
        return None


def entry_bool_option(entry: Any, key: str, default: bool) -> bool:
    """Return a config-entry boolean option with safe legacy value parsing."""
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    value = options.get(key, data.get(key, default))
    parsed = safe_bool(value)
    return default if parsed is None else parsed


def jackery_online_state(value: Any) -> bool | None:
    """Return a parsed Jackery online/offline marker, or None when unknown."""
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

    start_date: date
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
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return deterministic, de-duplicated data-quality warnings.

    A repair issue is updated every refresh. Sorting and de-duplicating the
    payload prevents needless issue churn when multiple payload paths report
    the same contradiction or device iteration order changes. Values are not
    changed; this only normalizes the diagnostics/repair presentation.
    """
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


def _format_request_range(request: Any) -> str | None:
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
    """Return a compact, deterministic repair/diagnostic warning example."""
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
    text = (
        f"{metric}: {source_section}={source_text} "
        f"< {reference_section}={reference_text}"
    )
    source_request = _format_request_range(warning.get(DATA_QUALITY_KEY_SOURCE_REQUEST))
    reference_request = _format_request_range(
        warning.get(DATA_QUALITY_KEY_REFERENCE_REQUEST)
    )
    if source_request or reference_request:
        text += (
            f" [{source_section}: {source_request or 'unknown'}; "
            f"{reference_section}: {reference_request or 'unknown'}]"
        )
    return text


def app_data_quality_warnings(
    payload: dict[str, Any],
    *,
    today: date | None = None,
    tolerance: float = 0.05,
) -> list[AppDataQualityWarning]:
    """Return non-mutating warnings for contradictory app statistics.

    DATA_SOURCE_PRIORITY.md forbids repairing one period with another. This
    helper therefore only detects contradictions that are mathematically valid
    for the current calendar range, then exposes them for diagnostics/repairs.
    Entity values keep their documented source unchanged.
    """
    if today is None:
        today = date.today()
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
        if isinstance(source, dict):
            return trend_series_key(section, stat_key)
        return None

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
        """Append a single contradiction warning to the loop-scoped list.

        Keyword-only by design: with eight strings/floats it is otherwise
        too easy to swap source and reference values silently. The
        existing call sites all pass explicit kwargs.
        """
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


def statistic_id_part(value: Any) -> str:
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
    """Build the external statistics id used for app chart imports.

    External statistics intentionally use ``domain:object_id`` instead of a
    sensor entity id. They back the Statistic Graph / long-term statistics
    views, while the normal period-total sensors keep their current state.
    """
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
        date_type = request.get(APP_REQUEST_DATE_TYPE) or request.get(
            APP_REQUEST_DATE_TYPE_ALT
        )
        if isinstance(date_type, str):
            return date_type
    for suffix in (DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR):
        if section.endswith(f"_{suffix}"):
            return suffix
    return None


def is_device_year_period_section(source: dict[str, Any], section: str) -> bool:
    """Return True for device app dateType=year statistic payloads."""
    date_type = _trend_date_type(section, source)
    return date_type == DATE_TYPE_YEAR and section.startswith((
        APP_SECTION_PV_STAT,
        APP_SECTION_HOME_STAT,
        APP_SECTION_BATTERY_STAT,
        APP_SECTION_CT_STAT,
    ))


def _compact_year_parts(value: Any) -> tuple[float, float] | None:
    """Return previous/current-month parts for a candidate Jackery compact year value.

    See ``DATA_SOURCE_PRIORITY.md`` ("Device year compact bucket expansion") and
    ``REPAIR_ROADMAP.md`` ("Device year compact bucket expansion"): for device
    ``dateType=year`` payloads Jackery can encode two adjacent monthly buckets
    into one current-month slot, e.g. ``13.26`` means previous month ``13`` and
    current month ``26`` (yearly total ``39``). This parser only extracts the
    *candidate* split. Whether to apply it is decided by
    ``expanded_year_series_values`` after cross-checking the documented
    period total field.

    Not used for week/month payloads or system trend endpoints — those always
    keep normal decimal semantics per ``DATA_SOURCE_PRIORITY.md``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", ".")
    sign = -1.0 if text.startswith("-") else 1.0
    unsigned = text[1:] if text.startswith("-") else text
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
    if fraction == 0.0:
        # 13.0 / 13.00 is a normal value, not a two-month compact value.
        parsed = safe_float(value)
        return None if parsed is None else (0.0, parsed)
    return whole, fraction


def expanded_year_series_values(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> list[float] | None:
    """Return chart values with Jackery device-year compact buckets expanded.

    Reference: ``DATA_SOURCE_PRIORITY.md`` ("Device year compact bucket
    expansion") and ``REPAIR_ROADMAP.md`` ("Device year compact bucket
    expansion"). Documented example: a May-slot value of ``13.26`` means
    April=``13`` and May=``26``, total ``39``. Raw series
    ``[0,0,0,0,13.26,0,...]`` must be published as effective buckets
    ``[0,0,0,13,26,0,...]``.

    Disambiguation problem
    ----------------------
    The compact two-month encoding is structurally indistinguishable from a
    real floating-point monthly bucket. ``71.72`` could mean either
    ``71 + 72 = 143`` (compact, two months) OR a single-month value of
    ``71.72`` kWh. Real diagnostic fixtures (May 2026 SolarVault Pro Max)
    show ``y[4] = 71.72`` paired with ``totalSolarEnergy = "71.72"``, which
    is unambiguously a single Float — the previous unconditional expansion
    inflated the published year value to ``143`` and contaminated HA's
    long-term statistics with phantom April energy.

    Disambiguation strategy
    -----------------------
    Jackery period payloads carry the documented period total alongside the
    chart series under the same ``stat_key``:

    * ``device_pv_stat_*``    -> ``totalSolarEnergy`` / ``pv1Egy``...
    * ``device_battery_stat_*`` -> ``totalCharge`` / ``totalDischarge``
    * ``device_home_stat_*``  -> ``totalInGridEnergy`` / ``totalOutGridEnergy``
    * ``device_ct_stat_*``    -> ``totalCtInputEnergy`` / ``totalCtOutputEnergy``

    The total field is the anchor for disambiguation:

    1. If ``sum(raw) ≈ direct_total`` (within tolerance) -> the chart values
       are real floats; return raw. No expansion.
    2. Else if ``sum(expanded) ≈ direct_total`` -> the compact encoding is
       in effect; return expanded.
    3. Else (neither matches, or no direct total at all) -> return raw.
       This honours ``STRICT_WORK_INSTRUCTIONS.md`` rule 7 ("Never hide,
       synthesize, extrapolate, or repair energy values silently"): when the
       payload itself is internally inconsistent we publish what was sent
       and let ``data_quality`` diagnostics surface the contradiction.

    Tolerance is the larger of 0.05 kWh (covers rounding noise like
    ``71.72`` vs sum ``71.72000003``) and 0.5 % of the absolute total.

    Returns:
    -------
    list[float] | None
        Effective per-month series, or ``None`` if the section/stat_key is
        unknown or the payload contains no list.
    """
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None
    series = source.get(series_key)
    if not isinstance(series, list):
        return None

    # Materialise the raw float view of the series exactly once.
    raw_values = [round(safe_float(item) or 0.0, 5) for item in series]
    raw_sum = round(sum(raw_values), 2)

    # ``stat_key`` is the documented period-total field on the same payload
    # (see APP_POLLING_MQTT.md HTTP table). ``safe_float`` handles both
    # numeric and locale-formatted strings.
    direct_total = safe_float(source.get(stat_key))
    if direct_total is not None:
        tolerance = max(0.05, abs(direct_total) * 0.005)
        if abs(raw_sum - direct_total) <= tolerance:
            # Path 1: the raw chart already matches the documented total.
            # Floats are real, no compact encoding active.
            return raw_values

    # Path 2/3: build the compact-expanded interpretation.
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
            # Path 2: the compact-expanded sum matches the documented total.
            # This is the genuine ``13.26 -> 13/26`` case from the .md spec.
            return expanded
        # Path 3a: payload is internally inconsistent (neither raw nor
        # expanded sum match the documented total). Per
        # STRICT_WORK_INSTRUCTIONS.md rule 7 we do not invent a corrected
        # value; the raw series is published and data_quality picks up the
        # discrepancy via app_data_quality_warnings().
        return raw_values

    # Path 3b: no direct total to anchor against. The conservative default
    # is to publish raw — fabricating energy in adjacent months without a
    # source-of-truth anchor would silently inflate HA's long-term
    # statistics, which DATA_SOURCE_PRIORITY.md ("Non-negotiable rules")
    # forbids. The compact-expansion is only safe when payload-internal
    # cross-validation succeeds.
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
    values: list[float] = []
    for raw in series:
        value = safe_float(raw)
        values.append(0.0 if value is None else round(value, 5))
    return values


def effective_period_total_value(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> float | None:
    """Return the effective app period total for one statistic field.

    For device ``dateType=year`` sections we sum the disambiguated chart
    series (see ``expanded_year_series_values``). When the chart series is
    missing entirely we fall back to the documented period-total field on
    the payload (``totalSolarEnergy``/``totalCharge``/...). The legacy
    naive ``_compact_year_parts`` fallback was removed: it had no anchor
    to cross-validate against and would inflate single-month values like
    ``71.72`` into ``71+72=143``, polluting HA long-term statistics.

    For non-year sections this returns the documented total field
    unchanged, parsed via ``safe_float``.
    """
    if is_device_year_period_section(source, section):
        values = effective_trend_series_values(source, section, stat_key)
        if values is not None:
            return round(sum(values), 2)
        # No chart series available — use the documented period-total field
        # verbatim. We never re-interpret a single scalar as compact-encoded
        # without the array context that makes the encoding identifiable.
        return safe_float(source.get(stat_key))
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
    """Return True when a year payload looks like the app's month-only bug.

    The SolarVault app can return a ``dateType=year`` chart where every month
    except the current one is zero, and the scalar "year" total is the current
    month total. When that shape appears after January, the coordinator fetches
    explicit month payloads for the elapsed year and lets
    ``backfill_year_payload_from_months`` decide whether the monthly sum should
    replace the cloud year value. A corrected future Jackery year payload with
    older non-zero months does not trigger this extra backfill path.
    """
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
    price_source = payload.get(PAYLOAD_PRICE)
    if isinstance(price_source, dict):
        configured = safe_float(price_source.get(FIELD_SINGLE_PRICE))
        if configured is not None and 0 <= configured <= 10:
            return configured, f"{PAYLOAD_PRICE}.{FIELD_SINGLE_PRICE}"

    if year_generation is not None and year_generation > 0 and year_revenue is not None:
        derived = year_revenue / year_generation
        if 0 <= derived <= 10:
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

    pv_not_savings_energy = None
    if year_generation is not None:
        pv_not_savings_energy = max(0.0, year_generation - savings_energy)

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
            "pv_not_savings_ac_energy_kwh": _round_stat_value(pv_not_savings_energy),
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
        raw_revenue, pv_revenue_candidates
    ):
        return True, "cloud_total_matches_pv_revenue_not_savings"

    return False, "cloud_total_higher_than_current_year_savings"


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


def backfill_year_payload_from_months(
    year_source: dict[str, Any],
    section_prefix: str,
    stat_keys: tuple[str, ...],
    month_sources: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Return a year payload guarded by explicit monthly app payloads.

    This is intentionally narrower than a general cross-period repair:
    month payloads must come from the same documented app endpoint family and
    the current calendar year. The function only raises a year total when the
    month sum is greater than the cloud year value. If Jackery later returns a
    correct year payload, that value is kept because it is already >= the
    independently fetched month lower bound.
    """
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
        (
            APP_SECTION_BATTERY_STAT,
            (APP_STAT_TOTAL_CHARGE, APP_STAT_TOTAL_DISCHARGE),
        ),
        (APP_SECTION_HOME_TRENDS, (APP_STAT_TOTAL_HOME_ENERGY,)),
        (APP_SECTION_PV_TRENDS, (APP_STAT_TOTAL_SOLAR_ENERGY,)),
        (
            APP_SECTION_BATTERY_TRENDS,
            (
                APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
                APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
            ),
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


def guard_statistic_totals_from_year(payload: dict[str, Any]) -> None:
    """Guard app total KPIs with corrected current-year period values.

    The Jackery ``systemStatistic`` endpoint can suffer the same month-only bug
    as the app year charts. Generation and carbon remain lower-bound guarded by
    the corrected PV year total. ``totalRevenue`` is different: it represents
    savings and must not equal raw PV revenue when part of the energy is stored,
    exported, or lost in conversion. When enough year-flow data is available we
    calculate savings from grid-side AC output after conversion losses, bounded
    by house consumption and reduced by CT export if a CT period payload exists.
    """
    statistic = payload.get(PAYLOAD_STATISTIC)
    pv_year = payload.get(_period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR))
    if not isinstance(statistic, dict) or not isinstance(pv_year, dict):
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

    raw_generation = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
    if year_generation is not None and (
        raw_generation is None
        or year_generation
        > raw_generation + _tolerance_for_values(raw_generation, year_generation)
    ):
        out[APP_STAT_TOTAL_GENERATION] = round(year_generation, 2)
        meta.setdefault("corrected", {})[APP_STAT_TOTAL_GENERATION] = {
            "raw_total": raw_generation,
            "corrected_total": round(year_generation, 2),
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
            savings["raw_cloud_total"] = raw_revenue
            savings["pv_revenue_candidates"] = candidates
            savings["decision"] = reason
            if publish_calculated:
                out[APP_STAT_TOTAL_REVENUE] = round(calculated_revenue, 2)
                savings["published_value"] = round(calculated_revenue, 2)
                savings["published_value_source"] = "calculated_savings"
            else:
                savings["published_value"] = raw_revenue
                savings["published_value_source"] = "cloud_total"
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
        if 0 < factor < 5 and corrected_carbon > raw_carbon + _tolerance_for_values(
            raw_carbon, corrected_carbon
        ):
            out[APP_STAT_TOTAL_CARBON] = corrected_carbon
            meta.setdefault("corrected", {})[APP_STAT_TOTAL_CARBON] = {
                "raw_total": raw_carbon,
                "corrected_total": corrected_carbon,
                "kg_per_kwh": round(factor, 5),
            }

    if "corrected" not in meta and APP_SAVINGS_CALC_META not in out:
        return
    if "corrected" in meta:
        out[APP_TOTAL_GUARD_META] = meta
    payload[PAYLOAD_STATISTIC] = out


def compact_json(value: Any) -> str:
    """Return compact JSON for diagnostic attributes."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def trend_series_points(
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    today: date | None = None,
) -> list[TrendStatisticPoint]:
    """Return app chart buckets as dated statistic points.

    APP_POLLING_MQTT.md defines explicit full-period app requests. This helper
    converts the returned chart arrays into dated buckets: week/month -> daily
    buckets, year -> monthly buckets. Future buckets from full-period app
    requests are skipped so HA is not filled with future zero values.
    """
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
            or request.get(APP_REQUEST_BEGIN_DATE_ALT)
        )
        end = _parse_iso_date(
            request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT)
        )
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
        if end is not None and bucket_start > end:
            continue
        if bucket_start > today:
            continue
        points.append(TrendStatisticPoint(bucket_start, round(value, 5)))
    return points


# ---------------------------------------------------------------------------
# Power-flow calculation helpers
# ---------------------------------------------------------------------------
def directional_power_value(
    source: dict[str, Any],
    positive_keys: tuple[str, ...],
    negative_keys: tuple[str, ...],
) -> float | None:
    """Return positive-key sum minus negative-key sum if any value exists.

    Jackery/CT payloads often expose import and export as separate positive
    fields instead of one signed value, for example ``aPhasePw`` and
    ``anPhasePw``. Positive means grid import; negative means grid export.
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

    if not found:
        return None
    return positive - negative


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
    """Return app-reported CT grid power; positive=import, negative=export.

    SolarVault CT payloads contain both phase-level fields and aggregate total
    fields. The Jackery app uses the aggregate pair for its net CT value; the
    phase fields remain available for gross import/export/flow diagnostics.
    """
    total = directional_power_value(
        ct, (CT_TOTAL_POWER_PAIR[0],), (CT_TOTAL_POWER_PAIR[1],)
    )
    if total is not None:
        return total
    phases = signed_phase_power_values(ct)
    if phases is not None:
        return sum(phases)
    return None


def calculated_smart_meter_power(
    ct: dict[str, Any],
    calculation: str,
) -> float | None:
    """Calculate derived CT powers from signed phase values."""
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
        if key not in source or source.get(key) is None:
            continue
        value = safe_float(source.get(key))
        if value is not None:
            return value
    return None


def jackery_reported_home_load_power(props: dict[str, Any]) -> float | None:
    """Return Jackery's reported live home/other-load power if available.

    SolarVault 3 Pro Max diagnostics show ``otherLoadPw`` as the app's live
    home-load value. Prefer this direct app field over reconstructing the load
    from grid-side fields, because grid-side field availability differs by
    firmware and message source.
    """
    return first_power_value(
        props, FIELD_OTHER_LOAD_PW, FIELD_HOME_LOAD_PW, FIELD_LOAD_PW
    )


def jackery_grid_side_input_power(props: dict[str, Any]) -> float | None:
    """AC power drawn by Jackery from the grid/home side.

    Prefer the on-grid fields observed in live diagnostics. The older
    ``inGridSidePw`` name is kept as a compatibility fallback.
    """
    return first_power_value(
        props, FIELD_IN_ONGRID_PW, FIELD_GRID_IN_PW, FIELD_IN_GRID_SIDE_PW
    )


def jackery_grid_side_output_power(props: dict[str, Any]) -> float | None:
    """AC power supplied by Jackery to the grid/home side.

    Prefer the on-grid fields observed in live diagnostics. The older
    ``outGridSidePw`` name is kept as a compatibility fallback.
    """
    return first_power_value(
        props, FIELD_OUT_ONGRID_PW, FIELD_GRID_OUT_PW, FIELD_OUT_GRID_SIDE_PW
    )


def jackery_corrected_home_consumption_power(
    ct: dict[str, Any],
    props: dict[str, Any],
) -> HomeConsumptionPower | None:
    """Return live home load and its diagnostic components.

    Preferred source:
    ``otherLoadPw`` from the SolarVault/App payload, because live diagnostics
    show it as the already calculated house load.

    Fallback formula:
    ``home_load = max(smart_meter_net - jackery_input + jackery_output, 0)``.

    The fallback intentionally corrects only Jackery/SolarVault AC flows.
    External PV sources that are not measured by Jackery still need their own
    measurement.
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

    if meter_net is None or (jackery_input == 0.0 and jackery_output == 0.0):
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
def trend_series_key(section: str, stat_key: str) -> str | None:
    """Return the chart series key for app week/month/year trend payloads.

    Jackery's period endpoints expose the app values as chart arrays:
    - PV/home period payloads use ``y``
    - battery charge uses ``y1``
    - battery discharge uses ``y2``

    All week/month/year sensors must use this same chart-series key selection.
    Device dateType=year payloads additionally expand compact monthly buckets
    (for example raw May value 13.26 -> April 13, May 26) before totals or
    external statistics are published.
    """
    if not section.endswith((
        f"_{DATE_TYPE_WEEK}",
        f"_{DATE_TYPE_MONTH}",
        f"_{DATE_TYPE_YEAR}",
    )):
        return None
    if section.startswith((APP_SECTION_PV_TRENDS, APP_SECTION_HOME_TRENDS)):
        return APP_CHART_SERIES_Y
    if section.startswith(APP_SECTION_PV_STAT):
        if stat_key == APP_STAT_TOTAL_SOLAR_ENERGY:
            return APP_CHART_SERIES_Y
        if stat_key == APP_STAT_PV1_ENERGY:
            return APP_CHART_SERIES_Y1
        if stat_key == APP_STAT_PV2_ENERGY:
            return APP_CHART_SERIES_Y2
        if stat_key == APP_STAT_PV3_ENERGY:
            return APP_CHART_SERIES_Y3
        if stat_key == APP_STAT_PV4_ENERGY:
            return APP_CHART_SERIES_Y4
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


def trend_series_total(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> float | None:
    """Return the app chart-series total for a week/month/year payload.

    Week, month and year follow the documented app chart series. Device
    dateType=year series are expanded from Jackery compact buckets before
    summing. ``None`` entries are skipped; zero buckets still count as valid
    datapoints so a real zero-period returns ``0.0``.
    """
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None
    # Week/month/year trend arrays from the Jackery app are energy series in
    # kWh. Guard against accidentally summing day-view power curves in W.
    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if unit and unit != APP_UNIT_KWH:
        return None
    series = source.get(series_key)
    if not isinstance(series, list):
        # Some app endpoints omit an all-zero series but still provide the
        # corresponding server total. For /stat/onGrid this is a valid 0 kWh
        # input/output side when the opposite chart series exists. Do not apply
        # this to CT endpoints unconditionally unless the server total exists.
        server_total = effective_period_total_value(source, section, stat_key)
        if (
            section.startswith(APP_SECTION_HOME_STAT)
            and server_total == 0.0
            and any(
                isinstance(source.get(key), list) for key in APP_HOME_GRID_SERIES_KEYS
            )
        ):
            return 0.0
        if section.startswith(APP_SECTION_CT_STAT) and server_total is not None:
            return round(server_total, 2)
        return None
    total = 0.0
    found = False
    values = effective_trend_series_values(source, section, stat_key)
    if values is None:
        values = []
    for value in values:
        if value is None:
            continue
        total += value
        found = True
    if not found:
        server_total = effective_period_total_value(source, section, stat_key)
        if section.startswith(APP_SECTION_CT_STAT) and server_total is not None:
            return round(server_total, 2)
        return None
    return round(total, 2)


def trend_series_has_value(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> bool:
    """Return True when an app chart payload contains a usable series.

    Server totals alone are not enough for app period charts. A valid zero
    period must have an explicit chart array containing numeric zero values;
    an empty array means the cloud did not provide a usable series.
    """
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
            and server_total == 0.0
            and any(
                isinstance(source.get(key), list) for key in APP_HOME_GRID_SERIES_KEYS
            )
        ):
            return True
        return bool(
            section.startswith(APP_SECTION_CT_STAT) and server_total is not None
        )
    if any(safe_float(item) is not None for item in series):
        return True
    return bool(
        section.startswith(APP_SECTION_CT_STAT)
        and safe_float(source.get(stat_key)) is not None
    )


def task_plan_value(task_plan: dict[str, Any], *keys: str) -> Any:
    """Read a value from the task-plan shapes documented in MQTT_PROTOCOL.md.

    App/MQTT task-plan responses can expose values directly, inside ``body``
    or as items in ``tasks``. Platforms should use this helper instead of
    duplicating shape-specific fallbacks.
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
            if not isinstance(item, dict):
                continue
            for key in keys:
                if key in item and item.get(key) is not None:
                    return item.get(key)
    return None


def trend_payload_has_value(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> bool:
    """Return True when a trend payload can produce a period sensor value.

    Older code only checked for the server total key. That can wrongly suppress
    month/year entities on payloads that contain the chart series but omit or
    misname the total. Week/month/year now follow the same creation criterion.
    """
    if trend_series_total(source, section, stat_key) is not None:
        return True
    return safe_float(source.get(stat_key)) is not None
