"""Unit tests for Jackery power-flow math helpers.

These tests deliberately load util.py directly so they do not need a full
Home Assistant test harness. They cover the parts that are easiest to break:
CT saldierung, gross phase flow and live home consumption.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_util_module():
    util_path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "jackery_solarvault"
        / "util.py"
    )
    spec = importlib.util.spec_from_file_location("jackery_solarvault_util", util_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


util = _load_util_module()


def test_smart_meter_net_and_gross_values_from_signed_phases() -> None:
    ct = {
        "aPhasePw": 2.9,
        "bPhasePw": 0,
        "bnPhasePw": 70.2,
        "cPhasePw": 68.8,
    }

    assert util.signed_phase_power_values(ct) == [2.9, -70.2, 68.8]
    assert round(util.smart_meter_net_power(ct), 2) == 1.5
    assert round(util.calculated_smart_meter_power(ct, "net_import"), 2) == 1.5
    assert round(util.calculated_smart_meter_power(ct, "net_export"), 2) == 0.0
    assert round(util.calculated_smart_meter_power(ct, "gross_import"), 2) == 71.7
    assert round(util.calculated_smart_meter_power(ct, "gross_export"), 2) == 70.2
    assert round(util.calculated_smart_meter_power(ct, "gross_flow"), 2) == 141.9


def test_smart_meter_net_falls_back_to_total_fields() -> None:
    assert util.smart_meter_net_power({"tPhasePw": 10}) == 10
    assert util.smart_meter_net_power({"tnPhasePw": 15}) == -15
    assert util.smart_meter_net_power({"tPhasePw": 3, "tnPhasePw": 7}) == -4


def test_smart_meter_net_prefers_app_total_over_phase_sum() -> None:
    ct = {
        "aPhasePw": 3,
        "anPhasePw": 0,
        "bPhasePw": 0,
        "bnPhasePw": 216,
        "cPhasePw": 210,
        "cnPhasePw": 0,
        "tPhasePw": 0,
        "tnPhasePw": 429,
    }

    assert sum(util.signed_phase_power_values(ct)) == -3
    assert util.smart_meter_net_power(ct) == -429
    assert util.calculated_smart_meter_power(ct, "net_export") == 429
    assert util.calculated_smart_meter_power(ct, "gross_flow") == 429


def test_jackery_corrected_home_consumption_discharging() -> None:
    ct = {
        "aPhasePw": 2.9,
        "bnPhasePw": 70.2,
        "cPhasePw": 68.8,
    }
    props = {"outGridSidePw": 70.2}

    result = util.jackery_corrected_home_consumption_power(ct, props)

    assert result is not None
    assert round(result.value, 2) == 71.7
    assert round(result.smart_meter_net_power, 2) == 1.5
    assert result.jackery_input_power == 0.0
    assert result.jackery_output_power == 70.2
    assert result.source == "smart_meter_net_minus_input_plus_output"


def test_jackery_corrected_home_consumption_charging() -> None:
    ct = {"aPhasePw": 300, "bPhasePw": 0, "cPhasePw": 0}
    props = {"inGridSidePw": 200}

    result = util.jackery_corrected_home_consumption_power(ct, props)

    assert result is not None
    assert result.value == 100
    assert result.smart_meter_net_power == 300
    assert result.jackery_input_power == 200
    assert result.jackery_output_power == 0.0
    assert result.source == "smart_meter_net_minus_input_plus_output"


def test_grid_side_helpers_prefer_ongrid_fields_from_live_diagnostics() -> None:
    props = {
        "outGridSidePw": 0,
        "outOngridPw": 385,
        "gridOutPw": 385,
        "inGridSidePw": 0,
        "inOngridPw": 0,
        "gridInPw": 0,
    }

    assert util.jackery_grid_side_input_power(props) == 0
    assert util.jackery_grid_side_output_power(props) == 385


def test_jackery_reported_home_load_preferred_from_live_diagnostics() -> None:
    ct = {
        "aPhasePw": 3,
        "bPhasePw": 0,
        "bnPhasePw": 239,
        "cPhasePw": 247,
    }
    props = {
        "otherLoadPw": 408,
        "outGridSidePw": 0,
        "outOngridPw": 408,
        "gridOutPw": 408,
        "inGridSidePw": 0,
        "inOngridPw": 0,
        "gridInPw": 0,
    }

    result = util.jackery_corrected_home_consumption_power(ct, props)

    assert result is not None
    assert result.value == 408
    assert result.smart_meter_net_power == 11
    assert result.jackery_input_power == 0
    assert result.jackery_output_power == 408
    assert result.source == "otherLoadPw"


def test_jackery_reported_home_load_does_not_require_ct_payload() -> None:
    result = util.jackery_corrected_home_consumption_power({}, {"otherLoadPw": 385})

    assert result is not None
    assert result.value == 385
    assert result.smart_meter_net_power is None
    assert result.source == "otherLoadPw"


def test_jackery_corrected_home_consumption_requires_ct_for_fallback_formula() -> None:
    assert util.jackery_corrected_home_consumption_power({}, {"outGridSidePw": 70}) is None
    assert util.jackery_corrected_home_consumption_power({"tPhasePw": 10}, {}) is None


def test_period_trend_totals_use_same_chart_series_logic_for_week_month_year() -> None:
    week = {"totalHomeEgy": "999", "y": [12.54, 15.3, 15.57, 15.36, 15.53, 0.42, 0.0]}
    month = {"totalHomeEgy": "999", "y": [15.53, 0.42] + [0.0] * 29}
    year = {"totalHomeEgy": "999", "y": [0.0, 0.0, 0.0, 0.0, 15.95] + [0.0] * 7}

    assert util.trend_series_total(week, "home_trends_week", "totalHomeEgy") == 74.72
    assert util.trend_series_total(month, "home_trends_month", "totalHomeEgy") == 15.95
    assert util.trend_series_total(year, "home_trends_year", "totalHomeEgy") == 15.95


def test_period_trend_entities_can_be_created_from_series_without_server_total() -> None:
    source = {"y": [0.0, 1.25, None, 2.75]}

    assert util.trend_payload_has_value(source, "home_trends_month", "totalHomeEgy")
    assert util.trend_series_total(source, "home_trends_month", "totalHomeEgy") == 4.0


def test_battery_month_and_year_follow_week_series_keys() -> None:
    month = {
        "totalChgEgy": "999",
        "totalDisChgEgy": "999",
        "y1": [3.49] + [0.0] * 30,
        "y2": [3.3, 0.42] + [0.0] * 29,
    }
    year = {
        "totalChgEgy": "999",
        "totalDisChgEgy": "999",
        "y1": [0.0, 0.0, 0.0, 0.0, 3.49] + [0.0] * 7,
        "y2": [0.0, 0.0, 0.0, 0.0, 3.72] + [0.0] * 7,
    }

    assert util.trend_series_total(month, "battery_trends_month", "totalChgEgy") == 3.49
    assert util.trend_series_total(month, "battery_trends_month", "totalDisChgEgy") == 3.72
    assert util.trend_series_total(year, "battery_trends_year", "totalChgEgy") == 3.49
    assert util.trend_series_total(year, "battery_trends_year", "totalDisChgEgy") == 3.72


def test_device_period_stats_follow_app_series_keys() -> None:
    pv_month = {"unit": "kWh", "totalSolarEnergy": "999", "y": [1.0, 2.5, 0.0]}
    battery_month = {
        "unit": "kWh",
        "totalCharge": "999",
        "totalDischarge": "999",
        "y1": [3.0, 0.5],
        "y2": [1.25, 2.0],
    }

    assert util.trend_series_total(
        pv_month, "device_pv_stat_month", "totalSolarEnergy"
    ) == 3.5
    assert util.trend_series_total(
        battery_month, "device_battery_stat_month", "totalCharge"
    ) == 3.5
    assert util.trend_series_total(
        battery_month, "device_battery_stat_month", "totalDischarge"
    ) == 3.25


def test_device_grid_and_ct_period_stats_follow_app_series_keys() -> None:
    grid_month = {
        "unit": "kWh",
        "totalInGridEnergy": "999",
        "totalOutGridEnergy": "999",
        "y1": [1.0, 2.5],
        "y2": [0.25, 0.75],
    }
    ct_month = {
        "unit": "kWh",
        "totalInCtEnergy": "999",
        "totalOutCtEnergy": "999",
        "y1": [3.0, 0.5],
        "y2": [1.25, 2.0],
    }

    assert util.trend_series_total(
        grid_month, "device_home_stat_month", "totalInGridEnergy"
    ) == 3.5
    assert util.trend_series_total(
        grid_month, "device_home_stat_month", "totalOutGridEnergy"
    ) == 1.0
    assert util.trend_series_total(
        ct_month, "device_ct_stat_month", "totalInCtEnergy"
    ) == 3.5
    assert util.trend_series_total(
        ct_month, "device_ct_stat_month", "totalOutCtEnergy"
    ) == 3.25


def test_empty_ct_period_series_is_not_a_usable_app_statistic() -> None:
    source = {
        "unit": "kWh",
        "totalInCtEnergy": "0",
        "totalOutCtEnergy": "0",
        "y1": [],
        "y2": [],
    }

    assert not util.trend_series_has_value(
        source,
        "device_ct_stat_month",
        "totalInCtEnergy",
    )
    assert not util.trend_series_has_value(
        source,
        "device_ct_stat_month",
        "totalOutCtEnergy",
    )
    assert util.trend_series_total(
        source,
        "device_ct_stat_month",
        "totalInCtEnergy",
    ) is None


def test_zero_filled_ct_period_series_is_a_valid_zero_statistic() -> None:
    source = {
        "unit": "kWh",
        "totalInCtEnergy": "0",
        "totalOutCtEnergy": "0",
        "y1": [0.0, 0.0],
        "y2": [0.0, 0.0],
    }

    assert util.trend_series_has_value(
        source,
        "device_ct_stat_month",
        "totalInCtEnergy",
    )
    assert util.trend_series_total(
        source,
        "device_ct_stat_month",
        "totalInCtEnergy",
    ) == 0.0


def test_period_trend_totals_ignore_day_power_curves_in_watts() -> None:
    source = {"unit": "W", "y": [256, 332, 456]}

    assert util.trend_series_total(source, "home_trends_month", "totalHomeEgy") is None


def test_period_trend_totals_from_latest_diagnostics() -> None:
    pv_week = {"unit": "kWh", "y": [18.41, 22.01, 22.83, 22.41, 22.29, 0.0, 0.0]}
    pv_month = {"unit": "kWh", "y": [22.29] + [0.0] * 30}
    pv_year = {"unit": "kWh", "y": [0.0, 0.0, 0.0, 0.0, 22.29] + [0.0] * 7}
    home_week = {"unit": "kWh", "y": [12.54, 15.3, 15.57, 15.36, 15.53, 0.52, 0.0]}
    home_month = {"unit": "kWh", "y": [15.53, 0.52] + [0.0] * 29}
    home_year = {"unit": "kWh", "y": [0.0, 0.0, 0.0, 0.0, 16.05] + [0.0] * 7}
    bat_week = {"unit": "kWh", "y1": [3.74, 3.23, 3.47, 3.62, 3.49, 0.0, 0.0], "y2": [2.92, 3.16, 3.12, 2.96, 3.3, 0.52, 0.0]}
    bat_month = {"unit": "kWh", "y1": [3.49] + [0.0] * 30, "y2": [3.3, 0.52] + [0.0] * 29}
    bat_year = {"unit": "kWh", "y1": [0.0, 0.0, 0.0, 0.0, 3.49] + [0.0] * 7, "y2": [0.0, 0.0, 0.0, 0.0, 3.82] + [0.0] * 7}

    assert util.trend_series_total(pv_week, "pv_trends_week", "totalSolarEnergy") == 107.95
    assert util.trend_series_total(pv_month, "pv_trends_month", "totalSolarEnergy") == 22.29
    assert util.trend_series_total(pv_year, "pv_trends_year", "totalSolarEnergy") == 22.29
    assert util.trend_series_total(home_week, "home_trends_week", "totalHomeEgy") == 74.82
    assert util.trend_series_total(home_month, "home_trends_month", "totalHomeEgy") == 16.05
    assert util.trend_series_total(home_year, "home_trends_year", "totalHomeEgy") == 16.05
    assert util.trend_series_total(bat_week, "battery_trends_week", "totalChgEgy") == 17.55
    assert util.trend_series_total(bat_week, "battery_trends_week", "totalDisChgEgy") == 15.98
    assert util.trend_series_total(bat_month, "battery_trends_month", "totalChgEgy") == 3.49
    assert util.trend_series_total(bat_month, "battery_trends_month", "totalDisChgEgy") == 3.82
    assert util.trend_series_total(bat_year, "battery_trends_year", "totalChgEgy") == 3.49
    assert util.trend_series_total(bat_year, "battery_trends_year", "totalDisChgEgy") == 3.82
