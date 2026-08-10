"""Behavioral tests for gating the LIVE per-device entry period hierarchy.

The coordinator already withholds hierarchy-violating period sections from the
recorder *snapshot* (``_gate_snapshot_period_hierarchy``). These tests pin the
sibling gate that must run on the LIVE per-device ``entry`` before it becomes
coordinator data a stat sensor reads: a period section flagged by
``app_data_quality_warnings`` (for example a month total exceeding its
containing year) must be dropped, while live property sections are left
untouched. The only production logic exercised here is the pure staticmethod
and the real ``app_data_quality_warnings`` detector — nothing internal is
mocked.
"""

from custom_components.jackery_solarvault.const import (
    APP_CHART_SERIES_Y,
    APP_SECTION_PV_STAT,
    DATE_TYPE_MONTH,
    DATE_TYPE_YEAR,
    PAYLOAD_PROPERTIES,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.util import app_data_quality_warnings

_PV_YEAR = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}"
_PV_MONTH = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}"


def test_live_entry_gate_drops_violating_period_but_keeps_live_properties() -> None:
    """A month-exceeds-year violation is dropped; live properties survive intact."""
    live_properties = {"1": {"batSoc": 55, "batInPw": 120, "batOutPw": 0}}
    entry = {
        # Year total (1.0 + 2.0 = 3.0) is smaller than its own month total
        # (50.0), which violates the AGENTS.md §2.2 period hierarchy.
        _PV_YEAR: {APP_CHART_SERIES_Y: [1.0, 2.0]},
        _PV_MONTH: {APP_CHART_SERIES_Y: [50.0]},
        PAYLOAD_PROPERTIES: live_properties,
    }
    warnings = app_data_quality_warnings(entry)
    # Sanity: the detector flags the inflated month as the exceeding section.
    assert any(warning.reference_section == _PV_MONTH for warning in warnings), (
        "expected a period-hierarchy warning naming the month section"
    )

    result = JackerySolarVaultCoordinator._gate_period_hierarchy_from_warnings(  # ruff: ignore[private-member-access]
        entry,
        warnings,
    )

    assert _PV_MONTH not in result
    assert _PV_YEAR in result
    # The live property section must pass through byte-identical.
    assert result[PAYLOAD_PROPERTIES] == live_properties


def test_live_entry_gate_returns_entry_unchanged_without_warnings() -> None:
    """With no hierarchy warnings the entry is returned with every section intact."""
    live_properties = {"1": {"batSoc": 80}}
    entry = {
        # Year (100.0) >= month (50.0): a consistent, non-violating hierarchy.
        _PV_YEAR: {APP_CHART_SERIES_Y: [100.0]},
        _PV_MONTH: {APP_CHART_SERIES_Y: [50.0]},
        PAYLOAD_PROPERTIES: live_properties,
    }
    warnings = app_data_quality_warnings(entry)
    assert warnings == []

    result = JackerySolarVaultCoordinator._gate_period_hierarchy_from_warnings(  # ruff: ignore[private-member-access]
        entry,
        warnings,
    )

    assert result == entry
