"""Characterisation tests for the year-series total/tolerance helpers."""

import pytest

from custom_components.jackery_solarvault.const import (
    APP_STAT_PV_PROFIT,
    APP_STAT_TOTAL_SOLAR_REVENUE,
    APP_STAT_UNIT,
)
from custom_components.jackery_solarvault.util import (
    _month_value,  # ruff: ignore[import-private-name]
    _nonzero_months,  # ruff: ignore[import-private-name]
    _period_section,  # ruff: ignore[import-private-name]
    _pv_revenue_value,  # ruff: ignore[import-private-name]
    _tolerance_for_values,  # ruff: ignore[import-private-name]
    effective_period_total_value,
    year_payload_appears_current_month_only,
)

_FLOOR = 0.05
_SCALAR = 7.5
_REVENUE = 3.5
_PROFIT_RAW = 50_000_000
_PROFIT_SCALED = 5.0
_MAY = 5


def test_tolerance_uses_floor_and_magnitude() -> None:
    """Tolerance is at least the floor and scales with the largest magnitude."""
    assert _tolerance_for_values(None, 1.0) == _FLOOR
    assert _tolerance_for_values(100.0) == pytest.approx(0.5)


def test_period_section_joins_prefix_and_type() -> None:
    """A period section is prefix_datetype."""
    assert _period_section("device_pv_stat", "year") == "device_pv_stat_year"


def test_nonzero_months_returns_one_based_indices() -> None:
    """Months with a non-zero value are reported one-based, first 12 only."""
    assert _nonzero_months([0.0, 5.0, 0.0, 3.0]) == [2, 4]


def test_pv_revenue_prefers_direct_then_scales_profit() -> None:
    """Direct revenue wins; otherwise profit is scaled; missing is None."""
    assert _pv_revenue_value({APP_STAT_TOTAL_SOLAR_REVENUE: "3.5"}) == _REVENUE
    assert _pv_revenue_value({APP_STAT_PV_PROFIT: _PROFIT_RAW}) == _PROFIT_SCALED
    assert _pv_revenue_value({}) is None


def test_effective_period_total_scalar_for_non_year_section() -> None:
    """A non-year section returns the parsed scalar at the stat key."""
    result = effective_period_total_value(
        {"energy": "7.5"}, "device_pv_stat_day", "energy"
    )

    assert result == _SCALAR


def test_month_value_falls_back_to_scalar() -> None:
    """Without a trend series the month value reads the scalar key."""
    assert _month_value({"energy": "7.5"}, "device_pv_stat_month", "energy") == _SCALAR


def test_year_month_only_false_in_january() -> None:
    """The month-only heuristic never fires for January."""
    assert not year_payload_appears_current_month_only(
        {}, "device_pv_stat_year", ("energy",), current_month=1
    )


def test_year_month_only_false_for_non_kwh_unit() -> None:
    """A non-kWh unit excludes the month-only heuristic."""
    assert not year_payload_appears_current_month_only(
        {APP_STAT_UNIT: "watt"},
        "device_pv_stat_year",
        ("energy",),
        current_month=_MAY,
    )
