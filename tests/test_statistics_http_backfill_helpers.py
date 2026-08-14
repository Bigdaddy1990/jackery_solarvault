"""Regression tests for the HTTP day-backfill pure helpers.

``coordinator.py`` delegates its automatic HTTP day-chart backfill to two
module-level pure functions in ``util.py`` via thin staticmethod wrappers:

* :func:`statistics_http_backfill_dates` — the completed-day window that
  decides *which* days get re-fetched. Only fully completed local days are
  returned in ascending order (never ``today``): the default is a rolling
  ``window_days`` window ending yesterday, while ``include_current_year=True``
  instead returns every completed day since January 1st of ``today``'s year.
* :func:`historical_day_payload_from_sources` — remaps fetched section
  sources (keyed by section prefix) into the normal day-payload section
  keys the day-chart importer resolves against.

Both were referenced by the coordinator but never defined, so every
automatic backfill raised ``NameError`` at runtime. These behavioural
tests pin the date-window and section->payload contracts.
"""

from datetime import date

from custom_components.jackery_solarvault.const import (
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_HOME_TRENDS,
    DATE_TYPE_DAY,
    PAYLOAD_HOME_TRENDS,
)
from custom_components.jackery_solarvault.util import (
    historical_day_payload_from_sources,
    statistics_http_backfill_dates,
)


def test_backfill_window_returns_completed_days_excluding_today() -> None:
    """The window covers the N most recent completed days ascending, never today."""
    today = date(2026, 7, 9)

    days = statistics_http_backfill_dates(today, window_days=3)

    assert days == [date(2026, 7, 6), date(2026, 7, 7), date(2026, 7, 8)]
    assert today not in days


def test_backfill_window_is_empty_for_non_positive_window() -> None:
    """A zero or negative window yields no days to backfill."""
    today = date(2026, 7, 9)

    assert statistics_http_backfill_dates(today, window_days=0) == []
    assert statistics_http_backfill_dates(today, window_days=-5) == []


def test_backfill_window_default_is_rolling_across_year_boundary() -> None:
    """The default rolling window ends yesterday and may cross the year edge."""
    today = date(2026, 1, 2)

    days = statistics_http_backfill_dates(today, window_days=3)

    assert days == [date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 1)]
    assert today not in days


def test_backfill_window_current_year_covers_year_to_date() -> None:
    """``include_current_year=True`` returns Jan 1 through yesterday, ascending."""
    today = date(2026, 1, 5)

    days = statistics_http_backfill_dates(
        today,
        window_days=3,
        include_current_year=True,
    )

    assert days == [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 3),
        date(2026, 1, 4),
    ]
    assert all(day.year == today.year for day in days)


def test_payload_from_sources_remaps_prefixes_to_day_sections() -> None:
    """Regular stat prefixes map to ``"{prefix}_day"`` payload sections."""
    battery_source = {"y": [{"time": "00:00", "value": "1.0"}]}
    section_sources = {APP_SECTION_BATTERY_STAT: battery_source}

    payload = historical_day_payload_from_sources(section_sources)

    expected_key = f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}"
    assert payload == {expected_key: battery_source}


def test_payload_from_sources_maps_home_trends_to_trend_section() -> None:
    """The home-trends prefix maps to the trend section, not a ``_day`` key."""
    trend_source = {"y": [{"time": "00:00", "value": "2.0"}]}
    section_sources = {APP_SECTION_HOME_TRENDS: trend_source}

    payload = historical_day_payload_from_sources(section_sources)

    assert payload == {PAYLOAD_HOME_TRENDS: trend_source}
    assert f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_DAY}" not in payload


def test_payload_from_sources_empty_input_yields_empty_output() -> None:
    """Empty input, and entries with empty values, produce no sections."""
    assert historical_day_payload_from_sources({}) == {}
    assert historical_day_payload_from_sources({APP_SECTION_BATTERY_STAT: {}}) == {}
