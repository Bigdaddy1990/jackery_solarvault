"""Shared helpers for Jackery SolarVault entities."""

from __future__ import annotations

import calendar
import contextlib
from datetime import date, datetime, timedelta
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
    APP_STAT_TOTAL_CHARGE,
    APP_STAT_TOTAL_CT_INPUT_ENERGY,
    APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
    APP_STAT_TOTAL_DISCHARGE,
    APP_STAT_TOTAL_GENERATION,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
    APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
    APP_STAT_UNIT,
    APP_UNIT_KWH,
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
    PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX,
    PAYLOAD_DEBUG_LOG_MAX_BYTES,
    PAYLOAD_STATISTIC,
    REDACT_KEYS,
    REDACTED_VALUE,
    TASK_PLAN_BODY,
    TASK_PLAN_TASKS,
)


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
    except (TypeError, ValueError):
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
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
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
    except (TypeError, ValueError):
        return None


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
