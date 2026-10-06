"""Regression tests for malformed energy data and detached coordinator fallback."""

from typing import Any, cast

import pytest

from custom_components.jackery_solarvault import coordinator as coordinator_module
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.util import backfill_year_payload_from_months


def test_detached_parent_cleanup_does_not_access_the_registry() -> None:
    """Teardown can detach an entry before parent-device cleanup executes."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator).config_entry = None
    # No hass or registry is installed: detached cleanup must return immediately.
    assert coordinator._unlink_removed_parent_devices({"removed"}) == 0  # ruff: ignore[private-member-access]


@pytest.mark.parametrize("totals", [None, [], "invalid"])
def test_malformed_certified_totals_are_not_used_as_statistics(totals: object) -> None:
    """A rule marker alone cannot certify an invalid serialized totals value."""
    state = {
        "verified_totals_rule": vars(coordinator_module)[
            "_STATISTICS_HTTP_VERIFIED_TOTALS_RULE"
        ],
        "verified_totals": totals,
    }
    assert (
        JackerySolarVaultCoordinator._cached_verified_day_totals(  # ruff: ignore[private-member-access]
            state, "device_pv_stat"
        )
        == {}
    )


@pytest.mark.parametrize("invalid_period", ["year", "month"])
def test_power_units_cannot_backfill_an_energy_total(invalid_period: str) -> None:
    """A watts payload must not be added to or converted into a kWh total."""
    year: dict[str, Any] = {"unit": "kWh", "totalSolarEnergy": 7.5}
    month = {"unit": "kWh", "totalSolarEnergy": 900.0}
    (year if invalid_period == "year" else month)["unit"] = "W"
    original = dict(year)
    result = backfill_year_payload_from_months(
        year, "device_pv_stat", ("totalSolarEnergy",), {1: month}
    )
    assert result == original
    assert year == original
