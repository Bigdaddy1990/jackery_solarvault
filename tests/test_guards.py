"""Behaviour of the value guards in guards.py."""

from datetime import date

import pytest

from custom_components.jackery_solarvault.guards import period_data_offset

_TODAY = date(2026, 9, 25)


@pytest.mark.parametrize(
    ["begin_iso", "expected"],
    [
        ["2026-09-24", -1],  # cloud still serves yesterday after midnight
        ["2026-09-25", 0],
        ["2026-09-26", 1],  # cloud already serves a later period
        [None, 0],
        ["not-a-date", 0],
    ],
)
def test_period_data_offset_classifies_the_served_period(
    begin_iso: str | None, expected: int
) -> None:
    """A period sensor recognises data of another period before publishing it."""
    assert period_data_offset(begin_iso, _TODAY) == expected
