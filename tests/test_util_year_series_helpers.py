"""Characterisation tests for the year-series total/tolerance helpers."""

from typing import Any

import pytest

from custom_components.jackery_solarvault.const import (
    APP_STAT_PV_PROFIT,
    APP_STAT_TOTAL_SOLAR_REVENUE,
    APP_STAT_UNIT,
)
from custom_components.jackery_solarvault.util import (
    _month_value,
    _nonzero_months,
    _period_section,
    _pv_revenue_value,
    _tolerance_for_values,
    attach_calculated_savings_metadata,
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


def test_savings_metadata_uses_app_system_pv_year_totals() -> None:
    """Savings diagnostics must use the same SysPvStatApi totals as the App."""
    payload: dict[str, Any] = {
        "price": {"singlePrice": "0.28"},
        "statistic": {
            "totalGeneration": "967.89",
            "totalRevenue": "228.13",
        },
        "pv_trends_year": {
            "totalSolarEnergy": "967.89",
            "totalSolarRevenue": "270.78",
        },
        "device_pv_stat_year": {
            "totalSolarEnergy": "957.80",
            "totalSolarRevenue": "168.11",
        },
        "device_home_stat_year": {
            "totalInGridEnergy": "2.41",
            "totalOutGridEnergy": "695.04",
        },
        "home_trends_year": {"totalHomeEgy": "702.50"},
        "device_ct_stat_year": {"totalOutCtEnergy": "0.00"},
        "device_battery_stat_year": {
            "totalCharge": "233.44",
            "totalDischarge": "225.84",
        },
    }

    attach_calculated_savings_metadata(payload)

    calculation = payload["statistic"]["_savings_calculation"]
    assert calculation["source_energy"]["pv_year_kwh"] == pytest.approx(967.89)
    assert calculation["source_energy"][
        "battery_charge_discharge_balance_year_kwh"
    ] == pytest.approx(7.60)
    assert calculation["pv_revenue_candidates"][0] == pytest.approx(270.78)


def test_savings_metadata_exposes_signed_battery_energy_balance() -> None:
    """Discharge above charge is a negative balance, never a battery loss."""
    payload: dict[str, Any] = {
        "price": {"singlePrice": "0.28"},
        "statistic": {
            "totalGeneration": "100.00",
            "totalRevenue": "28.00",
        },
        "pv_trends_year": {
            "totalSolarEnergy": "100.00",
            "totalSolarRevenue": "28.00",
        },
        "device_home_stat_year": {
            "totalInGridEnergy": "0.00",
            "totalOutGridEnergy": "50.00",
        },
        "home_trends_year": {"totalHomeEgy": "50.00"},
        "device_ct_stat_year": {"totalOutCtEnergy": "0.00"},
        "device_battery_stat_year": {
            "totalCharge": "225.84",
            "totalDischarge": "233.44",
        },
    }

    attach_calculated_savings_metadata(payload)

    source_energy = payload["statistic"]["_savings_calculation"]["source_energy"]
    assert source_energy["battery_charge_discharge_balance_year_kwh"] == (
        pytest.approx(-7.60)
    )
    assert "battery_charge_discharge_gap_kwh" not in source_energy


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
