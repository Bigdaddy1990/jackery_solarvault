"""Characterisation tests for the year-series total/tolerance helpers."""

from typing import Any

import pytest

from custom_components.jackery_solarvault.const import APP_STAT_UNIT
from custom_components.jackery_solarvault.util import (
    attach_calculated_savings_metadata,
    backfill_year_payload_from_months,
    effective_period_total_value,
    trend_series_total,
    year_payload_appears_current_month_only,
)

_SCALAR = 7.5
_MAY = 5


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


@pytest.mark.parametrize("month_value", [0.0, 7.5])
@pytest.mark.parametrize("series", [None, [], [None] * 12])
def test_year_backfill_preserves_missing_energy_months(
    month_value: float, series: list[None] | None
) -> None:
    """One reported month is evidence for that month, not twelve readings."""
    year: dict[str, Any] = {"unit": "kWh"}
    if series is not None:
        year["y"] = series

    result = backfill_year_payload_from_months(
        year,
        "device_pv_stat",
        ("totalSolarEnergy",),
        {4: {"unit": "kWh", "totalSolarEnergy": month_value}},
    )

    assert result["y"] == [None] * 3 + [month_value] + [None] * 8
    assert "totalSolarEnergy" not in result
    assert "pvEgy" not in result
    assert (
        effective_period_total_value(result, "device_pv_stat_year", "totalSolarEnergy")
        is None
    )
    assert trend_series_total(result, "device_pv_stat_year", "totalSolarEnergy") is None
    assert year.get("y") == series


def test_year_backfill_keeps_independent_energy_total_and_known_months() -> None:
    """An incomplete chart never replaces an independent annual total."""
    year: dict[str, Any] = {
        "unit": "kWh",
        "totalCharge": "200.00",
        "y1": [None] * 4 + [92.62, 70.65] + [None] * 6,
    }

    result = backfill_year_payload_from_months(
        year,
        "device_battery_stat",
        ("totalCharge",),
        {4: {"unit": "kWh", "totalCharge": "47.05"}},
    )

    assert result["y1"] == [None] * 3 + [47.05, 92.62, 70.65] + [None] * 6
    assert result["totalCharge"] == "200.00"
    assert effective_period_total_value(
        result, "device_battery_stat_year", "totalCharge"
    ) == pytest.approx(200.0)
    assert trend_series_total(
        result, "device_battery_stat_year", "totalCharge"
    ) == pytest.approx(200.0)


@pytest.mark.parametrize("month_revenue", [0.0, 7.5])
def test_year_backfill_preserves_missing_revenue_months(month_revenue: float) -> None:
    """An isolated revenue month cannot fabricate annual revenue or profit."""
    year: dict[str, Any] = {"unit": "kWh"}

    result = backfill_year_payload_from_months(
        year,
        "device_pv_stat",
        ("totalSolarEnergy",),
        {4: {"unit": "kWh", "totalSolarRevenue": month_revenue}},
    )

    assert result["y6"] == [None] * 3 + [month_revenue * 10_000_000] + [None] * 8
    assert "totalSolarRevenue" not in result
    assert "pvProfit" not in result
    assert "y6" not in year


def test_year_backfill_preserves_known_revenue_and_independent_total() -> None:
    """Monthly repair retains unqueried revenue buckets and annual scalars."""
    year: dict[str, Any] = {
        "unit": "kWh",
        "totalSolarRevenue": "100.00",
        "pvProfit": 1_000_000_000,
        "y6": [None] * 4 + [200_000_000] + [None] * 7,
    }

    result = backfill_year_payload_from_months(
        year,
        "device_pv_stat",
        ("totalSolarEnergy",),
        {4: {"unit": "kWh", "totalSolarRevenue": "150.00"}},
    )

    assert result["y6"] == [None] * 3 + [1_500_000_000, 200_000_000] + [None] * 7
    assert result["totalSolarRevenue"] == "100.00"
    assert result["pvProfit"] == year["pvProfit"]


def test_year_backfill_complete_revenue_series_preserves_other_months() -> None:
    """A complete revenue chart can be summed without losing known months."""
    year: dict[str, Any] = {
        "unit": "kWh",
        "totalSolarRevenue": "20.00",
        "y6": [0.0] * 4 + [200_000_000] + [0.0] * 7,
    }

    result = backfill_year_payload_from_months(
        year,
        "device_pv_stat",
        ("totalSolarEnergy",),
        {4: {"unit": "kWh", "totalSolarRevenue": "10.00"}},
    )

    assert result["y6"] == [0.0] * 3 + [100_000_000, 200_000_000] + [0.0] * 7
    assert result["totalSolarRevenue"] == pytest.approx(30.0)
    assert result["pvProfit"] == pytest.approx(300_000_000)


def test_year_backfill_never_lowers_larger_independent_energy_total() -> None:
    """A complete but stale chart does not replace a larger independent total."""
    year: dict[str, Any] = {
        "unit": "kWh",
        "totalCharge": "300.00",
        "y1": [0.0] * 4 + [100.0] + [0.0] * 7,
    }

    result = backfill_year_payload_from_months(
        year,
        "device_battery_stat",
        ("totalCharge",),
        {4: {"unit": "kWh", "totalCharge": "100.00"}},
    )

    assert result["totalCharge"] == "300.00"
    assert result["y1"][3:5] == [100.0, 100.0]
    assert effective_period_total_value(
        result, "device_battery_stat_year", "totalCharge"
    ) == pytest.approx(200.0)
    assert trend_series_total(
        result, "device_battery_stat_year", "totalCharge"
    ) == pytest.approx(200.0)


@pytest.mark.parametrize("month_total", [None, 12.0])
def test_year_backfill_does_not_sum_incomplete_month_curve(
    month_total: float | None,
) -> None:
    """A partial day chart establishes a month total only via a real scalar."""
    year: dict[str, Any] = {"unit": "kWh"}
    month: dict[str, Any] = {"unit": "kWh", "y": [7.5, None]}
    if month_total is not None:
        month["totalSolarEnergy"] = month_total

    result = backfill_year_payload_from_months(
        year, "device_pv_stat", ("totalSolarEnergy",), {4: month}
    )

    if month_total is None:
        assert result is year
    else:
        assert result["y"] == [None] * 3 + [month_total] + [None] * 8
    assert "totalSolarEnergy" not in result


def test_year_backfill_does_not_reintroduce_invalid_channel_lifetime_offset() -> None:
    """Rejected PV-channel offsets cannot become annual buckets via months."""
    year: dict[str, Any] = {"unit": "kWh", "totalSolarEnergy": "10.00"}
    month: dict[str, Any] = {
        "unit": "kWh",
        "totalSolarEnergy": "7.50",
        "pv1Egy": "120432.3",
        "y": [7.5, None],
        "y1": [120432.3, None],
    }

    result = backfill_year_payload_from_months(
        year, "device_pv_stat", ("pv1Egy",), {4: month}
    )

    assert result is year
    assert "pv1Egy" not in result
    assert "y1" not in result


@pytest.mark.parametrize(["year_unit", "expected"], [["kWh", 7.5], ["Wh", 7500.0]])
@pytest.mark.parametrize("month_unit", ["kWh", "Wh"])
def test_year_backfill_normalizes_month_energy_units(
    year_unit: str, expected: float, month_unit: str
) -> None:
    """Only documented energy units determine the month/year conversion."""
    year: dict[str, Any] = {"unit": year_unit}
    month: dict[str, Any] = {
        "unit": month_unit,
        "totalSolarEnergy": 7.5 if month_unit == "kWh" else 7500.0,
    }

    result = backfill_year_payload_from_months(
        year, "device_pv_stat", ("totalSolarEnergy",), {4: month}
    )

    assert result["y"] == [None] * 3 + [expected] + [None] * 8
    assert "totalSolarEnergy" not in result


@pytest.mark.parametrize("section", ["device_pv_stat_year", "pv_trends_year"])
@pytest.mark.parametrize("annual_total", [None, 7.5])
def test_year_total_does_not_sum_shortened_numeric_chart(
    section: str, annual_total: float | None
) -> None:
    """Unreported trailing months are missing even without null placeholders."""
    source: dict[str, Any] = {"unit": "kWh", "y": [1.0, 2.0, 3.0]}
    if annual_total is not None:
        source["totalSolarEnergy"] = annual_total

    assert (
        effective_period_total_value(source, section, "totalSolarEnergy")
        == annual_total
    )
    assert trend_series_total(source, section, "totalSolarEnergy") == annual_total


def test_year_channel_total_does_not_sum_short_chart_after_offset_rejection() -> None:
    """Rejecting a lifetime scalar does not make three months a full year."""
    source: dict[str, Any] = {
        "unit": "kWh",
        "totalSolarEnergy": "100.00",
        "pv1Egy": "500.00",
        "y": [10.0, 10.0, 10.0],
        "y1": [1.0, 2.0, 3.0],
    }

    assert effective_period_total_value(source, "device_pv_stat_year", "pv1Egy") is None
    assert trend_series_total(source, "device_pv_stat_year", "pv1Egy") is None
