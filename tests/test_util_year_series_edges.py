"""Behavior tests for public calendar and period helpers."""

from datetime import date

from custom_components.jackery_solarvault.util import (
    is_day_period_payload,
    iter_calendar_months,
    iter_calendar_weeks,
    iter_calendar_years,
)


def test_is_day_period_payload_reads_explicit_suffix() -> None:
    """The section suffix decides the period without consulting metadata."""
    assert is_day_period_payload({}, "pv_trend_day") is True
    assert is_day_period_payload({}, "home_stat_week") is False
    assert is_day_period_payload({}, "home_stat_month") is False
    assert is_day_period_payload({}, "home_stat_year") is False


def test_iter_calendar_months_spans_year_boundary() -> None:
    """Month starts roll from December into the next January."""
    months = iter_calendar_months(date(2023, 11, 15), date(2024, 2, 3))

    assert months == [
        date(2023, 11, 1),
        date(2023, 12, 1),
        date(2024, 1, 1),
        date(2024, 2, 1),
    ]


def test_iter_calendar_weeks_returns_monday_starts() -> None:
    """Week starts are the Mondays intersecting the inclusive range."""
    weeks = iter_calendar_weeks(date(2024, 1, 3), date(2024, 1, 20))

    assert weeks == [date(2024, 1, 1), date(2024, 1, 8), date(2024, 1, 15)]


def test_iter_calendar_years_is_inclusive() -> None:
    """The year range includes both endpoints."""
    assert iter_calendar_years(date(2022, 6, 1), date(2024, 2, 1)) == [2022, 2023, 2024]
