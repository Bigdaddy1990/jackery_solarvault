"""Characterisation tests for the device-year backfill edge helpers.

Pins the documented ``whole.fraction`` split and the raw-year-series
preference guard so the 5-minute-to-year rollup keeps splitting compact
device-year buckets exactly as the app encodes them.
"""

from datetime import date

from custom_components.jackery_solarvault.const import APP_SECTION_PV_STAT
from custom_components.jackery_solarvault.util import (
    _compact_year_parts,
    _prefer_raw_year_series_for_real_payload,
    is_day_period_payload,
    iter_calendar_months,
    iter_calendar_weeks,
    iter_calendar_years,
)

_TWELVE = 12


def test_compact_year_parts_splits_whole_and_fraction() -> None:
    """A packed bucket carries two whole-number month values, not a decimal.

    Anchored by documented cloud totals (audit 2026-07-24): "40,96" sums
    to 136 with its sibling months — the separator packs two integer
    buckets (previous month 40, current month 96), never 40.96 kWh.
    """
    assert _compact_year_parts("13.26") == (13.0, 26.0)


def test_compact_year_parts_zero_fraction_is_whole_only() -> None:
    """A ``.00`` fraction is not packed; the value stays a current scalar."""
    assert _compact_year_parts("2.00") == (0.0, 2.0)


def test_compact_year_parts_integer_has_no_current_part() -> None:
    """An integer string is a plain current-month scalar."""
    assert _compact_year_parts("3") == (0.0, 3.0)


def test_compact_year_parts_preserves_sign() -> None:
    """A negative packed bucket keeps the sign on both parts."""
    assert _compact_year_parts("-13.26") == (-13.0, -26.0)


def test_compact_year_parts_rejects_missing_and_bool() -> None:
    """None and bool inputs are not numeric buckets."""
    assert _compact_year_parts(None) is None
    assert _compact_year_parts(value=True) is None


def test_compact_year_parts_rejects_non_finite() -> None:
    """NaN / infinite strings do not parse to a bucket."""
    assert _compact_year_parts("nan") is None
    assert _compact_year_parts("inf") is None


def test_prefer_raw_year_series_true_for_single_nonzero_pv_snapshot() -> None:
    """A 12-month PV series with one nonzero month matching the total stays raw."""
    raw = [0.0] * 11 + [42.0]

    assert _prefer_raw_year_series_for_real_payload(
        APP_SECTION_PV_STAT,
        raw,
        42.0,
        tolerance=0.01,
    )


def test_prefer_raw_year_series_false_for_non_pv_section() -> None:
    """Only PV year snapshots qualify for the raw-preference shortcut."""
    raw = [0.0] * 11 + [42.0]

    assert not _prefer_raw_year_series_for_real_payload(
        "device_home_stat",
        raw,
        42.0,
        tolerance=0.01,
    )


def test_prefer_raw_year_series_false_when_short_or_no_total() -> None:
    """A short series or a missing scalar total both disqualify the shortcut."""
    assert not _prefer_raw_year_series_for_real_payload(
        APP_SECTION_PV_STAT,
        [1.0] * (_TWELVE - 1),
        1.0,
        tolerance=0.01,
    )
    assert not _prefer_raw_year_series_for_real_payload(
        APP_SECTION_PV_STAT,
        [0.0] * 11 + [42.0],
        None,
        tolerance=0.01,
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
