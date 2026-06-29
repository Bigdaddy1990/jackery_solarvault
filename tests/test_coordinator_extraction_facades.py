"""Characterization tests for coordinator extraction facades."""

import pytest

from custom_components.jackery_solarvault.client.ingest.validators import (
    verify_and_backfill,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    JackerySolarVaultCoordinator as LegacyCoordinator,
)


def test_coordinator_facade_exports_characterized_class() -> None:
    """Facade preserves the public coordinator class identity."""
    assert JackerySolarVaultCoordinator is LegacyCoordinator


@pytest.mark.parametrize(
    ["cloud", "local", "expected"],
    [
        [None, None, None],
        [12.0, None, 12.0],
        [None, 11.0, 11.0],
        [0.0, 8.0, 8.0],
        [100.0, 70.0, 70.0],
        [100.0, 95.0, 100.0],
    ],
)
def test_verify_and_backfill_matches_documented_hierarchy(
    cloud: float | None,
    local: float | None,
    expected: float | None,
) -> None:
    """Stats validator preserves documented cloud/local hierarchy."""
    assert verify_and_backfill(cloud, local) == expected
