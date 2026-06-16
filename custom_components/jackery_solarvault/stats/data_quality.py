"""Data-quality warning generation for Jackery app statistics."""

from datetime import date
import math
from typing import Any, NamedTuple

from custom_components.jackery_solarvault.const import (
    APP_CHART_STAT_METRICS,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_DATE_TYPE_ALT,
    APP_REQUEST_END_DATE,
    APP_REQUEST_END_DATE_ALT,
    APP_REQUEST_META,
    APP_SECTION_PV_STAT,
    APP_STAT_TOTAL_GENERATION,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    DATA_QUALITY_KEY_LABEL,
    DATA_QUALITY_KEY_LEVEL,
    DATA_QUALITY_KEY_METRIC_KEY,
    DATA_QUALITY_KEY_REASON,
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
    PAYLOAD_STATISTIC,
)


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
        """Return a deterministic diagnostics dictionary."""
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
        if self.total_method is not None:
            payload[DATA_QUALITY_KEY_TOTAL_METHOD] = self.total_method
        return payload


def normalized_data_quality_warnings(
    warnings: list[Any],
) -> list[dict[str, Any]]:
    """
    De-duplicate data-quality warnings based on reason, metric key, and source and reference values.
    
    Keeps the first occurrence of each unique warning and filters out non-dictionary items. Returns results in deterministic sorted order.
    
    Returns:
        A list of de-duplicated warning dictionaries.
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


def _format_request_range(request: object) -> str | None:
    """
    Format a compact date range summary from a request object.
    
    Parameters:
        request (object): A request object containing optional date fields.
    
    Returns:
        str | None: A formatted date range string if the request is a dict with date information, None otherwise.
    """
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
    """
    Format a data quality warning dictionary into a diagnostic message.
    
    Produces a string showing the metric name and a comparison of source and reference values across sections, with optional request date ranges.
    
    Returns:
        A formatted warning string in the form "metric: source_section=source_value < reference_section=reference_value" with optional date ranges appended in brackets.
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
    """
    Identify logical inconsistencies in app statistics across time periods and between lifetime and yearly totals.
    
    Detects contradictory numeric relationships by comparing statistics across day/week/month/year periods and checking if lifetime generation is less than yearly generation. Warnings are generated when logical relationships violate constraints beyond the tolerance threshold.
    
    Parameters:
        today (date | None): The reference date for determining week/month/year boundaries. Defaults to today's date.
        tolerance (float): The threshold for allowable differences in period comparisons. Defaults to 0.05.
    
    Returns:
        list[AppDataQualityWarning]: Warnings for detected logical inconsistencies.
    """
    from custom_components.jackery_solarvault.util import (
        app_period_range,
        safe_float,
        trend_series_key,
        trend_series_total,
    )

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
        """
        Constructs a section name from a prefix and date type.
        
        Returns:
            str: A section name formatted as {prefix}_{date_type}.
        """
        return f"{prefix}_{date_type}"

    def _period_total(prefix: str, date_type: str, stat_key: str) -> float | None:
        """
        Retrieve the total value for a specified period and statistic metric.
        
        Parameters:
            prefix: Prefix used to construct the payload section name.
            date_type: The period type (day, week, month, year, etc.).
            stat_key: The statistic key to retrieve.
        
        Returns:
            The total value as a float, or None if the section does not contain a dictionary.
        """
        section = _section(prefix, date_type)
        source = payload.get(section)
        if not isinstance(source, dict):
            return None
        if date_type in {DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR}:
            return trend_series_total(source, section, stat_key)
        return safe_float(source.get(stat_key))

    def _request_for_section(section: str) -> dict[str, Any] | None:
        """
        Extract the request metadata from a payload section.
        
        Returns:
            A copy of the request metadata dictionary if present, `None` otherwise.
        """
        source = payload.get(section)
        if not isinstance(source, dict):
            return None
        request = source.get(APP_REQUEST_META)
        return dict(request) if isinstance(request, dict) else None

    def _chart_series_key_for_section(section: str, stat_key: str) -> str | None:
        """
        Retrieve the chart-series key for a payload section.
        
        Returns:
            str | None: The chart-series key if the section exists as a dictionary, `None` otherwise.
        """
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
        """Append a warning for one cross-period discrepancy."""
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
        if week is not None and day is not None and week + tolerance < day:
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
        if (
            day is not None  # noqa: PLR0916
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
                else month,  # type: ignore[arg-type]  # noqa: E501, RUF100
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
