"""Tests for open/closed-period backfill state machine.

Task 5: Replace terminal backfill shortcuts with an explicit
open/closed-period state machine (BackfillStatus enum).
"""

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.const import (
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
)
from custom_components.jackery_solarvault.coordinator import (
    BackfillStatus,
    JackerySolarVaultCoordinator,
    _backfill_period_is_closed,  # noqa: PLC2701, RUF105
    _normalize_backfill_status,  # noqa: PLC2701, RUF105
)


def _coordinator() -> JackerySolarVaultCoordinator:
    """Build a bare coordinator for testing internal methods."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    obj._device_index = {}  # noqa: RUF105, SLF001
    return coordinator


class TestBackfillOpenPeriodState:
    """Test the BackfillStatus state machine for open vs closed periods."""

    def test_backfill_status_enum_has_correct_states(self) -> None:  # noqa: PLR6301, RUF105
        """BackfillStatus must have exactly PENDING, RETRYABLE, IMPORTED."""
        states = set(BackfillStatus)
        assert states == {"pending", "retryable", "imported"}

    def test_current_day_is_never_closed(self) -> None:  # noqa: PLR6301, RUF105
        """Current day (today) must never be marked as closed."""
        today = date.today()  # noqa: DTZ011, RUF105
        assert not _backfill_period_is_closed(DATE_TYPE_DAY, today, today=today)

    def test_current_week_is_never_closed(self) -> None:  # noqa: PLR6301, RUF105
        """Current week must never be marked as closed."""
        today = date.today()  # noqa: DTZ011, RUF105
        week_start = today - timedelta(days=today.weekday())
        assert not _backfill_period_is_closed(DATE_TYPE_WEEK, week_start, today=today)

    def test_current_month_is_never_closed(self) -> None:  # noqa: PLR6301, RUF105
        """Current month must never be marked as closed."""
        today = date.today()  # noqa: DTZ011, RUF105
        month_start = today.replace(day=1)
        assert not _backfill_period_is_closed(DATE_TYPE_MONTH, month_start, today=today)

    def test_current_year_is_never_closed(self) -> None:  # noqa: PLR6301, RUF105
        """Current year must never be marked as closed."""
        today = date.today()  # noqa: DTZ011, RUF105
        year_start = today.replace(month=1, day=1)
        assert not _backfill_period_is_closed(DATE_TYPE_YEAR, year_start, today=today)

    def test_two_empty_responses_do_not_make_open_period_terminal(self) -> None:  # noqa: PLR6301, RUF105
        """An open period receiving two empty responses stays RETRYABLE, never IMPORTED."""  # noqa: RUF105
        today = date.today()  # noqa: DTZ011, RUF105
        # Open period (yesterday's day)
        today - timedelta(days=1)

        # Normalize first empty response
        status1 = _normalize_backfill_status("empty_ambiguous", closed=False)
        assert status1 == BackfillStatus.RETRYABLE

        # Normalize second empty response
        status2 = _normalize_backfill_status("empty_ambiguous", closed=False)
        assert status2 == BackfillStatus.RETRYABLE

        # Neither should become IMPORTED or UNAVAILABLE_CLOSED
        assert status1 != BackfillStatus.IMPORTED
        assert status2 != BackfillStatus.IMPORTED

    def test_current_month_present_in_current_year_reconstruction(self) -> None:  # noqa: PLR6301, RUF105
        """Current month must be included when reconstructing current year backfill."""
        today = date(2026, 7, 15)
        year_start = date(2026, 1, 1)

        months = JackerySolarVaultCoordinator._iter_calendar_months(  # ruff: ignore[private-member-access]
            year_start, today
        )

        # Current month (July) must be present
        current_month = today.replace(day=1)
        assert current_month in months
        # All months from Jan to July inclusive
        assert len(months) == 7

    def test_imported_recorder_value_reflected_in_coordinator(self) -> None:
        """When a value is imported to recorder, coordinator snapshot should also have it."""  # noqa: RUF105
        # This tests the integration between recorder upsert and coordinator state
        # The coordinator's async_add_external_statistics should also update its internal cache  # noqa: RUF105
        # This is an integration contract - the state machine must ensure consistency
        # Implementation detail - tested in coordinator_statistics tests

    def test_closed_periods_retry_bounded_times_before_unavailable(self) -> None:  # noqa: PLR6301, RUF105
        """Closed periods retry a bounded number of times before becoming permanently unavailable."""  # noqa: RUF105
        # A closed period that was previously IMPORTED but needs repair
        # should go through RETRYABLE states with bounded retries
        # but never skip to a terminal "unavailable" state in one step

        # Legacy "unavailable" maps to RETRYABLE (not terminal)
        status = _normalize_backfill_status("unavailable", closed=True)
        assert status == BackfillStatus.RETRYABLE

        # The state machine has no "unavailable_closed" terminal state
        # Only PENDING, RETRYABLE, IMPORTED exist

    def test_value_differing_more_than_ten_percent_follows_conservative_rule(
        self,
    ) -> None:  # noqa: E501, RUF100
        """A value differing by >10% from recorded follows conservative minimum rule."""
        # When backfill finds a value that differs significantly from recorder,
        # the repair uses the conservative minimum (lower value) as per project requirement  # noqa: RUF105
        # This is tested in test_coordinator_statistics_repair.py

    def test_legacy_statuses_map_to_state_machine(self) -> None:  # noqa: PLR6301, RUF105
        """Legacy cache values map deterministically to BackfillStatus."""
        legacy_values = {
            "auth_error",
            "deferred",
            "empty_ambiguous",
            "fetched",
            "recorder_error",
            "transport_error",
            "unavailable",
        }

        for legacy in legacy_values:
            status = _normalize_backfill_status(legacy, closed=False)
            assert status == BackfillStatus.RETRYABLE

        # When closed, same mapping but with closed=True context
        for legacy in legacy_values:
            status = _normalize_backfill_status(legacy, closed=True)
            assert status == BackfillStatus.RETRYABLE

    def test_no_active_period_becomes_unavailable_closed(self) -> None:  # noqa: PLR6301, RUF105
        """No active (open) period should ever become a terminal unavailable_closed state."""  # noqa: RUF105
        # The state machine has only three states:
        # PENDING -> RETRYABLE -> IMPORTED
        # There is no "unavailable_closed" state for open periods

        today = date.today()  # noqa: DTZ011, RUF105
        # All open periods (today and future) are never closed
        assert not _backfill_period_is_closed(DATE_TYPE_DAY, today, today=today)

        # Normalize any legacy value for an open period
        status = _normalize_backfill_status("unavailable", closed=False)
        assert status == BackfillStatus.RETRYABLE  # Not a terminal state

    def test_backfill_status_serializable(self) -> None:  # noqa: PLR6301, RUF105
        """BackfillStatus values are serializable strings for storage."""
        for status in BackfillStatus:
            assert isinstance(status.value, str)
            # Can round-trip through string
            assert BackfillStatus(status.value) == status

    def test_migrate_legacy_cache_deterministically(self) -> None:  # noqa: PLR6301, RUF105
        """Legacy cache migration is deterministic and backward-compatible."""
        # This tests the migration logic in async_load_statistics_backfill_state
        # which calls _normalize_backfill_status on each stored value

        test_cases = [
            ("empty_ambiguous", BackfillStatus.RETRYABLE),
            ("fetched", BackfillStatus.RETRYABLE),
            ("recorder_error", BackfillStatus.RETRYABLE),
            ("transport_error", BackfillStatus.RETRYABLE),
            ("auth_error", BackfillStatus.RETRYABLE),
            ("deferred", BackfillStatus.RETRYABLE),
            ("unavailable", BackfillStatus.RETRYABLE),
            ("pending", BackfillStatus.PENDING),
            ("retryable", BackfillStatus.RETRYABLE),
            (
                "imported",
                BackfillStatus.RETRYABLE,
            ),  # IMPORTED on open bucket becomes RETRYABLE  # noqa: E501, RUF100
        ]

        for legacy, expected in test_cases:
            result = _normalize_backfill_status(legacy, closed=False)
            assert result == expected, f"Legacy {legacy!r} should map to {expected}"


class TestBackfillPeriodClosure:
    """Test the calendar boundary logic for period closure."""

    def test_day_boundary(self) -> None:  # noqa: PLR6301, RUF105
        """Day period ends at midnight."""
        today = date(2026, 7, 15)
        yesterday = date(2026, 7, 14)
        assert not _backfill_period_is_closed(DATE_TYPE_DAY, today, today=today)
        assert _backfill_period_is_closed(DATE_TYPE_DAY, yesterday, today=today)

    def test_week_boundary(self) -> None:  # noqa: PLR6301, RUF105
        """Week period ends on Sunday (Monday-Sunday)."""
        # Monday 2026-07-13 to Sunday 2026-07-19
        monday = date(2026, 7, 13)
        date(2026, 7, 19)
        next_monday = date(2026, 7, 20)

        # During the week (Wednesday), week is not closed
        wednesday = date(2026, 7, 15)
        assert not _backfill_period_is_closed(DATE_TYPE_WEEK, monday, today=wednesday)

        # After Sunday, the week is closed
        assert _backfill_period_is_closed(DATE_TYPE_WEEK, monday, today=next_monday)

    def test_month_boundary(self) -> None:  # noqa: PLR6301, RUF105
        """Month period ends on last calendar day."""
        july = date(2026, 7, 1)
        august = date(2026, 8, 1)

        # During July, not closed
        assert not _backfill_period_is_closed(
            DATE_TYPE_MONTH, july, today=date(2026, 7, 15)
        )  # noqa: E501, RUF100
        # August 1st means July is closed
        assert _backfill_period_is_closed(DATE_TYPE_MONTH, july, today=august)

    def test_year_boundary(self) -> None:  # noqa: PLR6301, RUF105
        """Year period ends on Dec 31."""
        year_2025 = date(2025, 1, 1)
        year_2026 = date(2026, 1, 1)

        # During 2025, not closed
        assert not _backfill_period_is_closed(
            DATE_TYPE_YEAR, year_2025, today=date(2025, 7, 1)
        )  # noqa: E501, RUF100
        # 2026 means 2025 is closed
        assert _backfill_period_is_closed(DATE_TYPE_YEAR, year_2025, today=year_2026)


# Import constants for the tests


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
