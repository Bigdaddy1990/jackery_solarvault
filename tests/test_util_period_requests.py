"""Characterisation tests for the app period-range request helpers.

These pin the begin/end date arithmetic that drives the 5-minute-to-day,
week, month and year statistics backfill requests.
"""

from datetime import date, datetime

import pytest

from custom_components.jackery_solarvault.const import (
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
)
from custom_components.jackery_solarvault.util import (
    _app_period_bound_to_date,  # ruff: ignore[import-private-name]
    app_month_request_kwargs,
    app_period_date_bounds,
    app_period_range,
    app_period_request_kwargs,
    app_year_request_kwargs,
    validate_app_period_date_type,
)

_WED = date(2024, 5, 15)  # a Wednesday
_FEB_LEAP_LAST = "2024-02-29"


def test_validate_period_type_passes_known_and_rejects_unknown() -> None:
    """Known period types pass through; unknown ones raise."""
    assert validate_app_period_date_type(DATE_TYPE_DAY) == DATE_TYPE_DAY
    with pytest.raises(ValueError, match="Unsupported"):
        validate_app_period_date_type("decade")


def test_app_period_range_day() -> None:
    """A day period is the single reference date."""
    assert app_period_range(DATE_TYPE_DAY, today=_WED) == (_WED, _WED)


def test_app_period_range_week_is_monday_to_sunday() -> None:
    """A week runs Monday..Sunday around the reference date."""
    assert app_period_range(DATE_TYPE_WEEK, today=_WED) == (
        date(2024, 5, 13),
        date(2024, 5, 19),
    )


def test_app_period_range_month_is_first_to_last() -> None:
    """A month runs from the 1st to the calendar last day."""
    assert app_period_range(DATE_TYPE_MONTH, today=_WED) == (
        date(2024, 5, 1),
        date(2024, 5, 31),
    )


def test_app_period_range_year_is_jan_to_dec() -> None:
    """A year runs Jan 1 .. Dec 31."""
    assert app_period_range(DATE_TYPE_YEAR, today=_WED) == (
        date(2024, 1, 1),
        date(2024, 12, 31),
    )


def test_period_bound_accepts_datetime_date_and_iso() -> None:
    """A bound accepts datetime, date and ISO strings."""
    assert (
        _app_period_bound_to_date(datetime(2024, 5, 15, 9, 30), field_name="beginDate")
        == _WED
    )
    assert _app_period_bound_to_date(_WED, field_name="beginDate") == _WED
    assert _app_period_bound_to_date("2024-05-15", field_name="beginDate") == _WED


def test_period_bound_rejects_empty_and_malformed() -> None:
    """Empty and non-ISO bounds raise."""
    with pytest.raises(ValueError, match="must not be empty"):
        _app_period_bound_to_date("  ", field_name="beginDate")
    with pytest.raises(ValueError, match="ISO date"):
        _app_period_bound_to_date("15/05/2024", field_name="beginDate")


def test_period_date_bounds_defaults_and_explicit() -> None:
    """Bounds default from the period and accept explicit overrides."""
    assert app_period_date_bounds(DATE_TYPE_MONTH, today=_WED) == (
        "2024-05-01",
        "2024-05-31",
    )
    assert app_period_date_bounds(
        DATE_TYPE_DAY, begin_date="2024-05-10", end_date="2024-05-12"
    ) == ("2024-05-10", "2024-05-12")


def test_period_date_bounds_rejects_reversed_range() -> None:
    """A begin later than end is rejected."""
    with pytest.raises(ValueError, match="before or equal"):
        app_period_date_bounds(
            DATE_TYPE_DAY, begin_date="2024-05-12", end_date="2024-05-10"
        )


def test_period_request_kwargs_carries_bounds() -> None:
    """The request kwargs carry the resolved begin/end ISO dates."""
    result = app_period_request_kwargs(DATE_TYPE_MONTH, today=_WED)

    assert "2024-05-01" in result.values()
    assert "2024-05-31" in result.values()


def test_month_request_kwargs_uses_leap_last_day() -> None:
    """A February month request ends on the leap-year last day."""
    result = app_month_request_kwargs(2024, 2)

    assert "2024-02-01" in result.values()
    assert _FEB_LEAP_LAST in result.values()


def test_month_request_kwargs_rejects_bad_month() -> None:
    """Months outside 1..12 are rejected."""
    with pytest.raises(ValueError, match="month"):
        app_month_request_kwargs(2024, 13)


def test_year_request_kwargs_spans_full_year() -> None:
    """A year request spans Jan 1 .. Dec 31."""
    result = app_year_request_kwargs(2024)

    assert "2024-01-01" in result.values()
    assert "2024-12-31" in result.values()
