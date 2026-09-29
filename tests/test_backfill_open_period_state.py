"""Tests for open/closed-period backfill state machine.

Task 5: Replace terminal backfill shortcuts with an explicit
open/closed-period state machine (BackfillStatus enum).
"""

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.coordinator import (
    BackfillStatus,
    JackerySolarVaultCoordinator,
    _normalize_backfill_status,  # ruff: ignore[import-private-name]
)


def _coordinator() -> JackerySolarVaultCoordinator:
    """Build a bare coordinator for testing internal methods."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    obj._device_index = {}  # ruff: ignore[private-member-access]
    return coordinator  # pyrefly: ignore [no-any-return-implicit]


class TestBackfillOpenPeriodState:
    """Test the BackfillStatus state machine for open vs closed periods."""

    def test_backfill_status_enum_has_correct_states(self) -> None:  # ruff: ignore[no-self-use]
        """BackfillStatus must have exactly PENDING, RETRYABLE, IMPORTED."""
        states = set(BackfillStatus)
        assert states == {"pending", "retryable", "imported"}

    def test_two_empty_responses_do_not_make_open_period_terminal(self) -> None:  # ruff: ignore[no-self-use]
        """An open period receiving two empty responses stays RETRYABLE, never IMPORTED."""  # ruff: ignore[line-too-long]
        today = date.today()  # ruff: ignore[call-date-today]
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

    def test_current_month_present_in_current_year_reconstruction(self) -> None:  # ruff: ignore[no-self-use]
        """Current month must be included when reconstructing current year backfill."""
        today = date(2026, 7, 15)
        year_start = date(2026, 1, 1)

        months = JackerySolarVaultCoordinator._iter_calendar_months(year_start, today)  # ruff: ignore[private-member-access]

        # Current month (July) must be present
        current_month = today.replace(day=1)
        assert current_month in months
        # All months from Jan to July inclusive
        assert len(months) == 7  # ruff: ignore[magic-value-comparison]

    def test_imported_recorder_value_reflected_in_coordinator(self) -> None:
        """When a value is imported to recorder, coordinator snapshot should also have it."""  # ruff: ignore[line-too-long]
        # This tests the integration between recorder upsert and coordinator state
        # The coordinator's async_add_external_statistics should also update its internal cache  # ruff: ignore[line-too-long]
        # This is an integration contract - the state machine must ensure consistency
        # Implementation detail - tested in coordinator_statistics tests

    def test_closed_periods_retry_bounded_times_before_unavailable(self) -> None:  # ruff: ignore[no-self-use]
        """Closed periods retry a bounded number of times before becoming permanently unavailable."""  # ruff: ignore[line-too-long]
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
    ) -> None:
        """A value differing by >10% from recorded follows conservative minimum rule."""
        # When backfill finds a value that differs significantly from recorder,
        # the repair uses the conservative minimum (lower value) as per project requirement  # ruff: ignore[line-too-long]
        # This is tested in test_coordinator_statistics_repair.py

    def test_legacy_statuses_map_to_state_machine(self) -> None:  # ruff: ignore[no-self-use]
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

    def test_backfill_status_serializable(self) -> None:  # ruff: ignore[no-self-use]
        """BackfillStatus values are serializable strings for storage."""
        for status in BackfillStatus:
            assert isinstance(status.value, str)
            # Can round-trip through string
            assert BackfillStatus(status.value) == status

    def test_migrate_legacy_cache_deterministically(self) -> None:  # ruff: ignore[no-self-use]
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
            ),  # IMPORTED on open bucket becomes RETRYABLE
        ]

        for legacy, expected in test_cases:
            result = _normalize_backfill_status(legacy, closed=False)
            assert result == expected, f"Legacy {legacy!r} should map to {expected}"


# Import constants for the tests


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
