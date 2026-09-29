"""Characterisation tests for the statistics backfill date/window helpers."""

from datetime import date

from custom_components.jackery_solarvault.const import (
    APP_SECTION_HOME_TRENDS,
    PAYLOAD_HOME_TRENDS,
)
from custom_components.jackery_solarvault.util import (
    historical_day_payload_from_sources,
    parse_statistics_backfill_date,
    statistics_current_year_recovery_needed,
    statistics_http_backfill_dates,
)

_TODAY = date(2024, 5, 15)
_WINDOW = 3
_FAILED = 2


# --- statistics_http_backfill_dates --------------------------------------


def test_backfill_dates_rolling_window_excludes_today() -> None:
    """A rolling window ends yesterday and spans window_days back."""
    assert statistics_http_backfill_dates(_TODAY, window_days=_WINDOW) == [
        date(2024, 5, 12),
        date(2024, 5, 13),
        date(2024, 5, 14),
    ]


def test_backfill_dates_zero_window_is_empty() -> None:
    """A zero/negative window yields no completed days."""
    assert statistics_http_backfill_dates(_TODAY, window_days=0) == []


def test_backfill_dates_current_year_from_january() -> None:
    """Current-year mode starts at January 1st through yesterday."""
    assert statistics_http_backfill_dates(
        date(2024, 1, 3), window_days=0, include_current_year=True
    ) == [date(2024, 1, 1), date(2024, 1, 2)]


def test_backfill_dates_current_year_on_jan_first_is_empty() -> None:
    """On January 1st there is no completed day yet this year."""
    assert (
        statistics_http_backfill_dates(
            date(2024, 1, 1), window_days=0, include_current_year=True
        )
        == []
    )


# --- historical_day_payload_from_sources ---------------------------------


def test_historical_payload_rekeys_and_skips_empty() -> None:
    """Stat prefixes gain a _day suffix; empty sources are dropped."""
    result = historical_day_payload_from_sources({
        "device_battery_stat": {"x": 1},
        "device_pv_stat": {},
    })

    assert result == {"device_battery_stat_day": {"x": 1}}


def test_historical_payload_maps_home_trends_section() -> None:
    """The home-trends prefix maps onto the trend payload section."""
    result = historical_day_payload_from_sources({APP_SECTION_HOME_TRENDS: {"pv": [1]}})

    assert result == {PAYLOAD_HOME_TRENDS: {"pv": [1]}}


# --- parse_statistics_backfill_date --------------------------------------


def test_parse_backfill_date_variants() -> None:
    """ISO dates (and datetime prefixes) parse; others are None."""
    assert parse_statistics_backfill_date("2024-05-15") == _TODAY
    assert parse_statistics_backfill_date("2024-05-15T09:00:00") == _TODAY
    assert parse_statistics_backfill_date(20240515) is None
    assert parse_statistics_backfill_date("nonsense") is None


# --- statistics_current_year_recovery_needed -----------------------------


def test_recovery_false_in_january() -> None:
    """January needs no current-year recovery."""
    assert not statistics_current_year_recovery_needed(
        last_success=date(2024, 1, 5),
        last_repair=None,
        failed_bucket_count=0,
        today=date(2024, 1, 20),
    )


def test_recovery_false_for_prior_year_success() -> None:
    """A success from a previous year does not trigger recovery."""
    assert not statistics_current_year_recovery_needed(
        last_success=date(2023, 12, 5),
        last_repair=None,
        failed_bucket_count=0,
        today=_TODAY,
    )


def test_recovery_true_with_failed_buckets_and_no_repair() -> None:
    """Failed buckets with no repair marker require recovery."""
    assert statistics_current_year_recovery_needed(
        last_success=date(2024, 5, 1),
        last_repair=None,
        failed_bucket_count=_FAILED,
        today=_TODAY,
    )


def test_recovery_true_when_repair_precedes_success_month() -> None:
    """A repair older than the success month leaves the year unrepaired."""
    assert statistics_current_year_recovery_needed(
        last_success=date(2024, 5, 1),
        last_repair=date(2024, 3, 1),
        failed_bucket_count=0,
        today=_TODAY,
    )


def test_recovery_false_when_repair_in_success_month() -> None:
    """A repair within the success month completes recovery."""
    assert not statistics_current_year_recovery_needed(
        last_success=date(2024, 5, 1),
        last_repair=date(2024, 5, 10),
        failed_bucket_count=0,
        today=_TODAY,
    )
