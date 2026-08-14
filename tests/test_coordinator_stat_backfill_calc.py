"""Behavioral tests for coordinator statistics / backfill calculation helpers.

These target the pure and near-pure computation helpers the coordinator uses
when it turns Jackery app-chart buckets into Home Assistant statistics: the
completed-day backfill window, calendar iterators, entity-target resolution,
completed-bucket filtering, per-row reset alignment, and the cumulative
entity-statistic builder. The only integration boundary these touch is the
Home Assistant local timezone (read from ``hass.config.time_zone``); everything
else is real production logic, so nothing internal is mocked.
"""

from datetime import UTC, date, datetime  # ruff:ignore[unused-import]
from types import SimpleNamespace
from typing import Any, cast

import pytest  # ruff:ignore[unused-import]

from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)


def _coordinator(*, time_zone: str = "UTC") -> JackerySolarVaultCoordinator:
    """Build a bare coordinator whose only wired boundary is the HA timezone."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.hass = SimpleNamespace(config=SimpleNamespace(time_zone=time_zone))
    obj._device_index = {}  # ruff: ignore[private-member-access]
    return coordinator


# --- _statistics_http_backfill_dates -------------------------------------


def test_backfill_dates_rolling_window_excludes_today() -> None:
    """The default rolling window covers window_days completed days, not today."""
    today = date(2026, 7, 9)

    days = JackerySolarVaultCoordinator._statistics_http_backfill_dates(  # ruff: ignore[private-member-access]
        today,
        window_days=3,
    )

    assert days == [date(2026, 7, 6), date(2026, 7, 7), date(2026, 7, 8)]
    assert today not in days


def test_backfill_dates_include_current_year_starts_january() -> None:
    """Year mode covers every completed day from Jan 1 through yesterday."""
    today = date(2026, 1, 4)

    days = JackerySolarVaultCoordinator._statistics_http_backfill_dates(  # ruff: ignore[private-member-access]
        today,
        include_current_year=True,
    )

    assert days == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]


def test_backfill_dates_non_positive_window_is_empty() -> None:
    """A window of zero (or less) yields no completed days."""
    assert (
        JackerySolarVaultCoordinator._statistics_http_backfill_dates(  # ruff: ignore[private-member-access]
            date(2026, 7, 9),
            window_days=0,
        )
        == []
    )


# --- calendar iterators ---------------------------------------------------


def test_iter_calendar_months_crosses_year_boundary() -> None:
    """Month starts include every first-of-month across a year boundary."""
    months = JackerySolarVaultCoordinator._iter_calendar_months(  # ruff: ignore[private-member-access]
        date(2025, 11, 20),
        date(2026, 2, 3),
    )

    assert months == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]


def test_iter_calendar_weeks_returns_monday_starts() -> None:
    """Week iteration yields Monday-aligned starts spanning the range."""
    weeks = JackerySolarVaultCoordinator._iter_calendar_weeks(  # ruff: ignore[private-member-access]
        date(2026, 7, 8),
        date(2026, 7, 20),
    )

    assert weeks == [date(2026, 7, 6), date(2026, 7, 13), date(2026, 7, 20)]
    assert all(day.weekday() == 0 for day in weeks)


def test_iter_calendar_years_is_inclusive_range() -> None:
    """Year iteration returns every calendar year inclusive of both ends."""
    assert JackerySolarVaultCoordinator._iter_calendar_years(  # ruff: ignore[private-member-access]
        date(2024, 6, 1),
        date(2026, 2, 1),
    ) == [2024, 2025, 2026]


# --- _historical_day_payload_from_sources --------------------------------


def test_historical_day_payload_remaps_prefixes_and_skips_empty() -> None:
    """Section prefixes remap to ``{prefix}_day`` keys and empties drop out."""
    payload = JackerySolarVaultCoordinator._historical_day_payload_from_sources(  # ruff: ignore[private-member-access]
        {
            "device_battery_stat": {"unit": 1},
            "device_pv_stat": {},
        },
    )

    assert payload == {"device_battery_stat_day": {"unit": 1}}
