"""Statistics utility functions extracted from coordinator.

Pure helpers for HA Recorder statistics import and backfill.
Source: coordinator.py lines 5988-9600 (Phase 3 extraction).
"""

from datetime import date, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from ...const import (
    APP_CHART_STAT_METRICS,
    APP_CHART_STAT_PERIODS,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
    APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    FIELD_CMD,
    FIELD_DEVICE_NAME,
    FIELD_WNAME,
    PAYLOAD_BATTERY_TRENDS,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_DISCOVERY,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_LIFETIME_COUNTERS,
    PAYLOAD_PROPERTIES,
    PAYLOAD_PV_TRENDS,
    PAYLOAD_SYSTEM,
)
from ...handlers.property_merge import merge_dict_values
from ...util import safe_float
from .ingest import (
    TransportSource,
    gate_payload_section,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_LOGGER = logging.getLogger(__name__)  # noqa: RUF067


# Canonical mapping from metric key to (trend_section, stat_key) used by
# ``day_chart_source_candidates`` when building day power-curve candidates.
DAY_TREND_SOURCE_BY_METRIC_KEY: dict[str, tuple[str, str]] = {  # noqa: RUF067
    "pv_energy": (PAYLOAD_PV_TRENDS, APP_STAT_TOTAL_SOLAR_ENERGY),
    "battery_charge_energy": (
        PAYLOAD_BATTERY_TRENDS,
        APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
    ),
    "battery_discharge_energy": (
        PAYLOAD_BATTERY_TRENDS,
        APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
    ),
    "home_energy": (PAYLOAD_HOME_TRENDS, APP_STAT_TOTAL_HOME_ENERGY),
}


def stat_row_start(row: Mapping[str, Any]) -> float | None:  # noqa: RUF067
    """Return a statistics row start timestamp in seconds."""
    start = row.get("start")
    if isinstance(start, datetime):
        return start.timestamp()
    return safe_float(start)


def filter_completed_app_points(  # noqa: RUF067
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


def parse_statistics_backfill_date(value: object) -> date | None:  # noqa: RUF067
    """Parse a persisted ISO date for statistics repair decisions."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def statistics_current_year_recovery_needed(  # noqa: RUF067
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


def iter_calendar_months(start_date: date, end_date: date) -> list[date]:  # noqa: RUF067
    """Return first-of-month dates intersecting an inclusive date range.

    The missing ``@staticmethod`` decorator on the original coordinator
    method caused ``self._iter_calendar_months(from_date, to_date)`` to
    pass three positional arguments to a two-arg function, breaking every
    ``async_import_statistics`` entity-stat repair attempt. Extracted as a
    pure function to prevent recurrence.
    """
    cursor = start_date.replace(day=1)
    end_month = end_date.replace(day=1)
    months: list[date] = []
    while cursor <= end_month:
        months.append(cursor)
        if cursor.month == 12:  # noqa: PLR2004
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)
    return months


def iter_calendar_weeks(start_date: date, end_date: date) -> list[date]:  # noqa: RUF067
    """Return Monday week starts intersecting an inclusive date range."""
    cursor = start_date - timedelta(days=start_date.weekday())
    end_week = end_date - timedelta(days=end_date.weekday())
    weeks: list[date] = []
    while cursor <= end_week:
        weeks.append(cursor)
        cursor += timedelta(days=7)
    return weeks


def iter_calendar_years(start_date: date, end_date: date) -> list[int]:  # noqa: RUF067
    """Return calendar years intersecting an inclusive date range."""
    return list(range(start_date.year, end_date.year + 1))


def app_chart_period_meta(date_type: str) -> tuple[str, str] | None:  # noqa: RUF067
    """Return the external bucket id and label for an app chart period."""
    for period_date_type, bucket, bucket_label in APP_CHART_STAT_PERIODS:
        if period_date_type == date_type:
            return bucket, bucket_label
    return None


def app_chart_name_prefix(device_id: str, payload: dict[str, Any]) -> str:  # noqa: RUF067
    """Return a stable, user-readable app chart statistic name prefix."""
    return (
        (payload.get(PAYLOAD_SYSTEM) or {}).get(FIELD_DEVICE_NAME)
        or (payload.get(PAYLOAD_DISCOVERY) or {}).get(FIELD_DEVICE_NAME)
        or (payload.get(PAYLOAD_PROPERTIES) or {}).get(FIELD_WNAME)
        or f"Jackery {device_id}"
    )


def day_chart_source_candidates(  # noqa: RUF067
    section_prefix: str,
    stat_key: str,
    metric_key: str,
) -> list[tuple[str, str]]:
    """Return candidate payload sections for one day power-curve metric."""
    candidates: list[tuple[str, str]] = []
    trend_source = DAY_TREND_SOURCE_BY_METRIC_KEY.get(metric_key)
    if trend_source is not None:
        candidates.append(trend_source)
    candidates.append((f"{section_prefix}_{DATE_TYPE_DAY}", stat_key))

    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


# Canonical mapping from metric key to {date_type: entity_statistic_key}
# used by ``entity_targets_for_app_points``.
ENTITY_STATISTIC_KEY_BY_METRIC_PERIOD: dict[str, dict[str, str]] = {  # noqa: RUF067
    "pv_energy": {
        DATE_TYPE_DAY: "device_today_pv_energy",
        DATE_TYPE_WEEK: "pv_week_energy",
        DATE_TYPE_MONTH: "pv_month_energy",
        DATE_TYPE_YEAR: "pv_year_energy",
    },
    "pv1_energy": {
        DATE_TYPE_DAY: "device_pv1_day_energy",
        DATE_TYPE_WEEK: "device_pv1_week_energy",
        DATE_TYPE_MONTH: "device_pv1_month_energy",
        DATE_TYPE_YEAR: "device_pv1_year_energy",
    },
    "pv2_energy": {
        DATE_TYPE_DAY: "device_pv2_day_energy",
        DATE_TYPE_WEEK: "device_pv2_week_energy",
        DATE_TYPE_MONTH: "device_pv2_month_energy",
        DATE_TYPE_YEAR: "device_pv2_year_energy",
    },
    "pv3_energy": {
        DATE_TYPE_DAY: "device_pv3_day_energy",
        DATE_TYPE_WEEK: "device_pv3_week_energy",
        DATE_TYPE_MONTH: "device_pv3_month_energy",
        DATE_TYPE_YEAR: "device_pv3_year_energy",
    },
    "pv4_energy": {
        DATE_TYPE_DAY: "device_pv4_day_energy",
        DATE_TYPE_WEEK: "device_pv4_week_energy",
        DATE_TYPE_MONTH: "device_pv4_month_energy",
        DATE_TYPE_YEAR: "device_pv4_year_energy",
    },
    "device_ongrid_input_energy": {
        DATE_TYPE_DAY: "device_today_ongrid_input",
        DATE_TYPE_WEEK: "device_ongrid_input_week_energy",
        DATE_TYPE_MONTH: "device_ongrid_input_month_energy",
        DATE_TYPE_YEAR: "device_ongrid_input_year_energy",
    },
    "device_ongrid_output_energy": {
        DATE_TYPE_DAY: "device_today_ongrid_output",
        DATE_TYPE_WEEK: "device_ongrid_output_week_energy",
        DATE_TYPE_MONTH: "device_ongrid_output_month_energy",
        DATE_TYPE_YEAR: "device_ongrid_output_year_energy",
    },
    "battery_charge_energy": {
        DATE_TYPE_DAY: "device_today_battery_charge",
        DATE_TYPE_WEEK: "battery_charge_week_energy",
        DATE_TYPE_MONTH: "battery_charge_month_energy",
        DATE_TYPE_YEAR: "battery_charge_year_energy",
    },
    "battery_discharge_energy": {
        DATE_TYPE_DAY: "device_today_battery_discharge",
        DATE_TYPE_WEEK: "battery_discharge_week_energy",
        DATE_TYPE_MONTH: "battery_discharge_month_energy",
        DATE_TYPE_YEAR: "battery_discharge_year_energy",
    },
    "ct_input_energy": {
        DATE_TYPE_DAY: "ct_input_day_energy",
        DATE_TYPE_WEEK: "ct_input_week_energy",
        DATE_TYPE_MONTH: "ct_input_month_energy",
        DATE_TYPE_YEAR: "ct_input_year_energy",
    },
    "ct_output_energy": {
        DATE_TYPE_DAY: "ct_output_day_energy",
        DATE_TYPE_WEEK: "ct_output_week_energy",
        DATE_TYPE_MONTH: "ct_output_month_energy",
        DATE_TYPE_YEAR: "ct_output_year_energy",
    },
    "eps_input_energy": {
        DATE_TYPE_DAY: "eps_input_day_energy",
        DATE_TYPE_WEEK: "eps_input_week_energy",
        DATE_TYPE_MONTH: "eps_input_month_energy",
        DATE_TYPE_YEAR: "eps_input_year_energy",
    },
    "eps_output_energy": {
        DATE_TYPE_DAY: "eps_output_day_energy",
        DATE_TYPE_WEEK: "eps_output_week_energy",
        DATE_TYPE_MONTH: "eps_output_month_energy",
        DATE_TYPE_YEAR: "eps_output_year_energy",
    },
    "home_energy": {
        DATE_TYPE_DAY: "today_load",
        DATE_TYPE_WEEK: "home_week_energy",
        DATE_TYPE_MONTH: "home_month_energy",
        DATE_TYPE_YEAR: "home_year_energy",
    },
}

# Window for automatic HTTP backfill of recent day statistics.
STATISTICS_HTTP_BACKFILL_WINDOW_DAYS: int = 7  # noqa: RUF067


def entity_targets_for_app_points(  # noqa: RUF067
    metric_key: str,
    date_type: str,
) -> tuple[tuple[str, str, bool], ...]:
    """Return entity-key/reset/cumulative-state targets for app buckets."""
    periods = ENTITY_STATISTIC_KEY_BY_METRIC_PERIOD.get(metric_key)
    if not periods:
        return ()
    if date_type == DATE_TYPE_DAY:
        key = periods.get(DATE_TYPE_DAY)
        return ((key, DATE_TYPE_DAY, True),) if key else ()
    if date_type == DATE_TYPE_WEEK:
        key = periods.get(DATE_TYPE_WEEK)
        return ((key, DATE_TYPE_WEEK, True),) if key else ()
    if date_type == DATE_TYPE_MONTH:
        targets: list[tuple[str, str, bool]] = []
        month_key = periods.get(DATE_TYPE_MONTH)
        year_key = periods.get(DATE_TYPE_YEAR)
        if month_key:
            targets.append((month_key, DATE_TYPE_MONTH, True))
        if year_key:
            targets.append((year_key, DATE_TYPE_YEAR, True))
        return tuple(targets)
    return ()


def entity_source_priority(reset_period: str, date_type: str) -> int:  # noqa: RUF067
    """Return priority for duplicate buckets within the same period."""
    return 1 if reset_period == date_type else 0


def statistics_http_backfill_dates(  # noqa: RUF067
    today: date,
    *,
    window_days: int = STATISTICS_HTTP_BACKFILL_WINDOW_DAYS,
    include_current_year: bool = False,
) -> list[date]:
    """Return completed local days covered by automatic HTTP backfill."""
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


def historical_day_payload_from_sources(  # noqa: RUF067
    section_sources: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Convert section-source dicts into the normal day payload shape."""
    payload: dict[str, dict[str, Any]] = {}
    for section_prefix, source in section_sources.items():
        if section_prefix == APP_SECTION_HOME_TRENDS:
            gated_source = gate_payload_section(
                TransportSource.HTTP,
                PAYLOAD_HOME_TRENDS,
                source,
            )
            if gated_source:
                payload[PAYLOAD_HOME_TRENDS] = gated_source
        else:
            section = f"{section_prefix}_{DATE_TYPE_DAY}"
            gated_source = gate_payload_section(TransportSource.HTTP, section, source)
            if gated_source:
                payload[section] = gated_source
    return payload


def is_derived_home_energy_candidate(  # noqa: RUF067
    *,
    metric_key: str,
    section_prefix: str,
    stat_key: str,
    candidate_prefix: str,
    candidate_stat_key: str,
) -> bool:
    """Return True when a candidate is the derived home-energy fallback."""
    return (
        metric_key == "home_energy"
        and section_prefix == APP_SECTION_HOME_TRENDS
        and stat_key == APP_STAT_TOTAL_HOME_ENERGY
        and candidate_prefix == APP_SECTION_HOME_STAT
        and candidate_stat_key == APP_STAT_TOTAL_OUT_GRID_ENERGY
    )


def merge_device_statistic_data(  # noqa: RUF067
    updated: dict[str, Any],
    source: dict[str, Any],
    device_statistic_live_keys: frozenset[str],
) -> bool:
    """Merge app day-energy snapshots into PAYLOAD_DEVICE_STATISTIC.

    Transport ``cmd=120`` frames carry cumulative Wh/kWh lifetime counters
    on the same wire keys. Those must not overwrite the HTTP
    ``deviceStatistic`` day bucket that backs the ``device_today_*``
    sensors.
    """
    if source.get(FIELD_CMD) is not None:
        return False
    statistic = {
        key: value
        for key, value in source.items()
        if key in device_statistic_live_keys and value is not None
    }
    if not statistic:
        return False
    current = updated.get(PAYLOAD_DEVICE_STATISTIC)
    current_dict = current if isinstance(current, dict) else {}
    merged = merge_dict_values(current_dict, statistic)
    if merged == current_dict:
        return False
    updated[PAYLOAD_DEVICE_STATISTIC] = merged
    return True


def merge_lifetime_counter_data(  # noqa: RUF067
    updated: dict[str, Any],
    source: dict[str, Any],
    lifetime_counter_keys: frozenset[str],
) -> bool:
    """Merge transport lifetime energy counters into their own bucket."""
    counters = {
        key: value
        for key, value in source.items()
        if key in lifetime_counter_keys and value is not None
    }
    if not counters:
        return False
    current = updated.get(PAYLOAD_LIFETIME_COUNTERS)
    current_dict = current if isinstance(current, dict) else {}
    merged = merge_dict_values(current_dict, counters)
    if merged == current_dict:
        return False
    updated[PAYLOAD_LIFETIME_COUNTERS] = merged
    return True


def current_app_chart_entity_source_batches(  # noqa: RUF067
    payload: dict[str, Any],
    source: TransportSource,
) -> list[tuple[str, dict[str, dict[str, Any]]]]:
    """Return current-payload period sources safe for entity history import."""
    prefixes = tuple(dict.fromkeys(metric[0] for metric in APP_CHART_STAT_METRICS))
    source_batches: list[tuple[str, dict[str, dict[str, Any]]]] = []
    for date_type in (DATE_TYPE_DAY, DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR):
        section_sources: dict[str, dict[str, Any]] = {}
        for section_prefix in prefixes:
            section = f"{section_prefix}_{date_type}"
            section_payload = payload.get(section)
            if isinstance(section_payload, dict):
                gated_source = gate_payload_section(source, section, section_payload)
                if gated_source:
                    section_sources[section_prefix] = gated_source
        if section_sources:
            source_batches.append((date_type, section_sources))
    return source_batches
