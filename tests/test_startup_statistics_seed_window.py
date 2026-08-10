"""Behavioral tests for the startup statistics seed window.

Startup must seed the full current-year statistics once (per AGENTS.md
§1.2 / PROTOCOL.md §8), not a truncated recent-days window. These tests pin the
startup backfill floor to the documented 120-day recovery horizon and confirm
the current-year window helper starts at January 1st so the seed covers the
whole year.
"""

from datetime import date

from custom_components.jackery_solarvault import coordinator
from custom_components.jackery_solarvault.util import statistics_http_backfill_dates


def test_startup_backfill_floor_reaches_mid_april_from_late_july() -> None:
    """The startup HTTP fallback horizon covers the requested mid-April data."""
    expected_startup_min_days = 120
    startup_min_days = coordinator._STATISTICS_HTTP_STARTUP_BACKFILL_MIN_DAYS  # ruff: ignore[private-member-access]
    assert startup_min_days == expected_startup_min_days


def test_current_year_backfill_window_starts_january_first() -> None:
    """Current-year seeding starts at Jan 1 and ends on the last completed day."""
    today = date(2026, 7, 20)
    startup_min_days = coordinator._STATISTICS_HTTP_STARTUP_BACKFILL_MIN_DAYS  # ruff: ignore[private-member-access]
    dates = statistics_http_backfill_dates(
        today,
        window_days=startup_min_days,
        include_current_year=True,
    )
    assert dates[0] == date(2026, 1, 1)
    assert dates[-1] == date(2026, 7, 19)
