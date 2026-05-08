"""Unit tests for Jackery power-flow math helpers.

These tests deliberately load util.py directly so they do not need a full
Home Assistant test harness. They cover the parts that are easiest to break:
CT saldierung, gross phase flow and live home consumption.
"""

import importlib.util
from pathlib import Path
import sys
import types


def _load_util_module():
    package_dir = (
        Path(__file__).resolve().parents[1] / "custom_components" / "jackery_solarvault"
    )
    sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
    package = types.ModuleType("custom_components.jackery_solarvault")
    package.__path__ = [str(package_dir)]
    sys.modules.setdefault("custom_components.jackery_solarvault", package)

    const_spec = importlib.util.spec_from_file_location(
        "custom_components.jackery_solarvault.const",
        package_dir / "const.py",
    )
    assert const_spec is not None
    const_module = importlib.util.module_from_spec(const_spec)
    sys.modules[const_spec.name] = const_module
    assert const_spec.loader is not None
    const_spec.loader.exec_module(const_module)

    spec = importlib.util.spec_from_file_location(
        "custom_components.jackery_solarvault.util",
        package_dir / "util.py",
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


util = _load_util_module()


def test_app_period_range_contract() -> None:
    """Implement test app period range contract."""
    today = util.date(2026, 5, 3)

    assert util.app_period_range("day", today=today) == (
        util.date(2026, 5, 3),
        util.date(2026, 5, 3),
    )
    assert util.app_period_range("week", today=today) == (
        util.date(2026, 4, 27),
        util.date(2026, 5, 3),
    )
    assert util.app_period_range("month", today=today) == (
        util.date(2026, 5, 1),
        util.date(2026, 5, 31),
    )
    assert util.app_period_range("year", today=today) == (
        util.date(2026, 1, 1),
        util.date(2026, 12, 31),
    )


def test_app_period_range_handles_boundaries_and_leap_years() -> None:
    """Implement test app period range handles boundaries and leap years."""
    assert util.app_period_range("week", today=util.date(2026, 1, 1)) == (
        util.date(2025, 12, 29),
        util.date(2026, 1, 4),
    )
    assert util.app_period_range("month", today=util.date(2024, 2, 15)) == (
        util.date(2024, 2, 1),
        util.date(2024, 2, 29),
    )
    assert util.app_period_range("month", today=util.date(2026, 2, 15)) == (
        util.date(2026, 2, 1),
        util.date(2026, 2, 28),
    )


def test_app_period_range_rejects_unknown_date_types() -> None:
    """Implement test app period range rejects unknown date types."""
    try:
        util.app_period_range("quarter", today=util.date(2026, 5, 3))
    except ValueError as err:
        assert "Unsupported Jackery app period dateType" in str(err)
    else:
        raise AssertionError("unknown Jackery app dateType was silently accepted")


def test_app_period_date_bounds_fills_only_missing_sides() -> None:
    """Implement test app period date bounds fills only missing sides."""
    today = util.date(2026, 5, 3)

    assert util.app_period_date_bounds("month", today=today) == (
        "2026-05-01",
        "2026-05-31",
    )
    assert util.app_period_date_bounds(
        "month", begin_date="2026-05-02", today=today
    ) == (
        "2026-05-02",
        "2026-05-31",
    )
    assert util.app_period_date_bounds(
        "month", end_date=util.date(2026, 5, 20), today=today
    ) == (
        "2026-05-01",
        "2026-05-20",
    )


def test_app_period_date_bounds_rejects_bad_manual_bounds() -> None:
    """Implement test app period date bounds rejects bad manual bounds."""
    today = util.date(2026, 5, 3)

    for kwargs in (
        {"begin_date": ""},
        {"begin_date": "2026/05/01"},
        {"end_date": "not-a-date"},
        {"begin_date": "2026-05-31", "end_date": "2026-05-01"},
    ):
        try:
            util.app_period_date_bounds("month", today=today, **kwargs)
        except ValueError as err:
            assert "Jackery app period" in str(err)
        else:
            raise AssertionError(f"invalid app period bounds were accepted: {kwargs!r}")


def test_app_period_date_bounds_strips_manual_date_strings() -> None:
    """Implement test app period date bounds strips manual date strings."""
    assert util.app_period_date_bounds(
        "month",
        begin_date=" 2026-05-02 ",
        end_date=" 2026-05-03 ",
        today=util.date(2026, 5, 3),
    ) == ("2026-05-02", "2026-05-03")


def test_app_period_date_bounds_converts_datetime_to_date_only() -> None:
    """Implement test app period date bounds converts datetime to date only."""
    assert util.app_period_date_bounds(
        "month",
        begin_date=util.datetime(2026, 5, 2, 12, 30),
        end_date=util.datetime(2026, 5, 3, 23, 59),
        today=util.date(2026, 5, 3),
    ) == ("2026-05-02", "2026-05-03")


def test_app_period_request_kwargs_uses_snake_case_method_arguments() -> None:
    """Implement test app period request kwargs uses snake case method arguments."""
    assert util.app_period_request_kwargs("week", today=util.date(2026, 5, 3)) == {
        "date_type": "week",
        "begin_date": "2026-04-27",
        "end_date": "2026-05-03",
    }


def test_parse_utc_datetime_normalizes_iso_z_and_naive_values() -> None:
    """Battery-pack stale cleanup needs stable UTC timestamp parsing."""
    assert util.parse_utc_datetime("2026-05-06T10:23:07Z").isoformat() == (
        "2026-05-06T10:23:07+00:00"
    )
    assert util.parse_utc_datetime(util.datetime(2026, 5, 6, 10, 23, 7)).isoformat() == (
        "2026-05-06T10:23:07+00:00"
    )


def test_parse_utc_datetime_rejects_invalid_values() -> None:
    """Invalid pack timestamps should be explicit and recoverable."""
    try:
        util.parse_utc_datetime("not-a-time")
    except ValueError as err:
        assert "invalid UTC timestamp" in str(err)
    else:
        raise AssertionError("expected ValueError")


def test_app_month_request_kwargs_builds_explicit_calendar_month() -> None:
    """Historical year backfill must query explicit month ranges."""
    assert util.app_month_request_kwargs(2026, 4) == {
        "date_type": "month",
        "begin_date": "2026-04-01",
        "end_date": "2026-04-30",
    }
    assert util.app_month_request_kwargs(2024, 2)["end_date"] == "2024-02-29"


def test_smart_meter_net_and_gross_values_from_signed_phases() -> None:
    """Implement test smart meter net and gross values from signed phases."""
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
    """Implement test smart meter net falls back to total fields."""
    assert util.smart_meter_net_power({"tPhasePw": 10}) == 10
    assert util.smart_meter_net_power({"tnPhasePw": 15}) == -15
    assert util.smart_meter_net_power({"tPhasePw": 3, "tnPhasePw": 7}) == -4


def test_smart_meter_net_prefers_app_total_over_phase_sum() -> None:
    """Implement test smart meter net prefers app total over phase sum."""
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
    """Implement test jackery corrected home consumption discharging."""
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
    """Implement test jackery corrected home consumption charging."""
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
    """Implement test grid side helpers prefer ongrid fields from live diagnostics."""
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
    """Implement test jackery reported home load preferred from live diagnostics."""
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
    """Implement test jackery reported home load does not require ct payload."""
    result = util.jackery_corrected_home_consumption_power({}, {"otherLoadPw": 385})

    assert result is not None
    assert result.value == 385
    assert result.smart_meter_net_power is None
    assert result.source == "otherLoadPw"


def test_jackery_corrected_home_consumption_requires_ct_for_fallback_formula() -> None:
    """Implement test jackery corrected home consumption requires ct for fallback formula."""
    assert (
        util.jackery_corrected_home_consumption_power({}, {"outGridSidePw": 70}) is None
    )
    assert util.jackery_corrected_home_consumption_power({"tPhasePw": 10}, {}) is None


def test_period_trend_totals_use_same_chart_series_logic_for_week_month_year() -> None:
    """Implement test period trend totals use same chart series logic for week month year."""
    week = {"totalHomeEgy": "999", "y": [12.54, 15.3, 15.57, 15.36, 15.53, 0.42, 0.0]}
    month = {"totalHomeEgy": "999", "y": [15.53, 0.42] + [0.0] * 29}
    year = {"totalHomeEgy": "999", "y": [0.0, 0.0, 0.0, 0.0, 15.95] + [0.0] * 7}

    assert util.trend_series_total(week, "home_trends_week", "totalHomeEgy") == 74.72
    assert util.trend_series_total(month, "home_trends_month", "totalHomeEgy") == 15.95
    assert util.trend_series_total(year, "home_trends_year", "totalHomeEgy") == 15.95


def test_period_trend_entities_can_be_created_from_series_without_server_total() -> (
    None
):
    """Implement test period trend entities can be created from series without server total."""
    source = {"y": [0.0, 1.25, None, 2.75]}

    assert util.trend_payload_has_value(source, "home_trends_month", "totalHomeEgy")
    assert util.trend_series_total(source, "home_trends_month", "totalHomeEgy") == 4.0


def test_battery_month_and_year_follow_week_series_keys() -> None:
    """Implement test battery month and year follow week series keys."""
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
    assert (
        util.trend_series_total(month, "battery_trends_month", "totalDisChgEgy") == 3.72
    )
    assert util.trend_series_total(year, "battery_trends_year", "totalChgEgy") == 3.49
    assert (
        util.trend_series_total(year, "battery_trends_year", "totalDisChgEgy") == 3.72
    )


def test_device_period_stats_follow_app_series_keys() -> None:
    """Implement test device period stats follow app series keys."""
    pv_month = {"unit": "kWh", "totalSolarEnergy": "999", "y": [1.0, 2.5, 0.0]}
    battery_month = {
        "unit": "kWh",
        "totalCharge": "999",
        "totalDischarge": "999",
        "y1": [3.0, 0.5],
        "y2": [1.25, 2.0],
    }

    assert (
        util.trend_series_total(pv_month, "device_pv_stat_month", "totalSolarEnergy")
        == 3.5
    )
    assert (
        util.trend_series_total(
            battery_month, "device_battery_stat_month", "totalCharge"
        )
        == 3.5
    )
    assert (
        util.trend_series_total(
            battery_month, "device_battery_stat_month", "totalDischarge"
        )
        == 3.25
    )


def test_device_grid_and_ct_period_stats_follow_app_series_keys() -> None:
    """Implement test device grid and ct period stats follow app series keys."""
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

    assert (
        util.trend_series_total(
            grid_month, "device_home_stat_month", "totalInGridEnergy"
        )
        == 3.5
    )
    assert (
        util.trend_series_total(
            grid_month, "device_home_stat_month", "totalOutGridEnergy"
        )
        == 1.0
    )
    assert (
        util.trend_series_total(ct_month, "device_ct_stat_month", "totalInCtEnergy")
        == 3.5
    )
    assert (
        util.trend_series_total(ct_month, "device_ct_stat_month", "totalOutCtEnergy")
        == 3.25
    )


def test_empty_ct_period_series_falls_back_to_server_totals() -> None:
    """Implement test empty ct period series falls back to server totals."""
    source = {
        "unit": "kWh",
        "totalInCtEnergy": "0",
        "totalOutCtEnergy": "0",
        "y1": [],
        "y2": [],
    }

    assert util.trend_series_has_value(
        source,
        "device_ct_stat_month",
        "totalInCtEnergy",
    )
    assert util.trend_series_has_value(
        source,
        "device_ct_stat_month",
        "totalOutCtEnergy",
    )
    assert (
        util.trend_series_total(
            source,
            "device_ct_stat_month",
            "totalInCtEnergy",
        )
        == 0.0
    )


def test_zero_filled_ct_period_series_is_a_valid_zero_statistic() -> None:
    """Implement test zero filled ct period series is a valid zero statistic."""
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
    assert (
        util.trend_series_total(
            source,
            "device_ct_stat_month",
            "totalInCtEnergy",
        )
        == 0.0
    )


def test_period_trend_totals_ignore_day_power_curves_in_watts() -> None:
    """Implement test period trend totals ignore day power curves in watts."""
    source = {"unit": "W", "y": [256, 332, 456]}

    assert util.trend_series_total(source, "home_trends_month", "totalHomeEgy") is None


def test_period_trend_totals_from_latest_diagnostics() -> None:
    """Implement test period trend totals from latest diagnostics."""
    pv_week = {"unit": "kWh", "y": [18.41, 22.01, 22.83, 22.41, 22.29, 0.0, 0.0]}
    pv_month = {"unit": "kWh", "y": [22.29] + [0.0] * 30}
    pv_year = {"unit": "kWh", "y": [0.0, 0.0, 0.0, 0.0, 22.29] + [0.0] * 7}
    home_week = {"unit": "kWh", "y": [12.54, 15.3, 15.57, 15.36, 15.53, 0.52, 0.0]}
    home_month = {"unit": "kWh", "y": [15.53, 0.52] + [0.0] * 29}
    home_year = {"unit": "kWh", "y": [0.0, 0.0, 0.0, 0.0, 16.05] + [0.0] * 7}
    bat_week = {
        "unit": "kWh",
        "y1": [3.74, 3.23, 3.47, 3.62, 3.49, 0.0, 0.0],
        "y2": [2.92, 3.16, 3.12, 2.96, 3.3, 0.52, 0.0],
    }
    bat_month = {
        "unit": "kWh",
        "y1": [3.49] + [0.0] * 30,
        "y2": [3.3, 0.52] + [0.0] * 29,
    }
    bat_year = {
        "unit": "kWh",
        "y1": [0.0, 0.0, 0.0, 0.0, 3.49] + [0.0] * 7,
        "y2": [0.0, 0.0, 0.0, 0.0, 3.82] + [0.0] * 7,
    }

    assert (
        util.trend_series_total(pv_week, "pv_trends_week", "totalSolarEnergy") == 107.95
    )
    assert (
        util.trend_series_total(pv_month, "pv_trends_month", "totalSolarEnergy")
        == 22.29
    )
    assert (
        util.trend_series_total(pv_year, "pv_trends_year", "totalSolarEnergy") == 22.29
    )
    assert (
        util.trend_series_total(home_week, "home_trends_week", "totalHomeEgy") == 74.82
    )
    assert (
        util.trend_series_total(home_month, "home_trends_month", "totalHomeEgy")
        == 16.05
    )
    assert (
        util.trend_series_total(home_year, "home_trends_year", "totalHomeEgy") == 16.05
    )
    assert (
        util.trend_series_total(bat_week, "battery_trends_week", "totalChgEgy") == 17.55
    )
    assert (
        util.trend_series_total(bat_week, "battery_trends_week", "totalDisChgEgy")
        == 15.98
    )
    assert (
        util.trend_series_total(bat_month, "battery_trends_month", "totalChgEgy")
        == 3.49
    )
    assert (
        util.trend_series_total(bat_month, "battery_trends_month", "totalDisChgEgy")
        == 3.82
    )
    assert (
        util.trend_series_total(bat_year, "battery_trends_year", "totalChgEgy") == 3.49
    )
    assert (
        util.trend_series_total(bat_year, "battery_trends_year", "totalDisChgEgy")
        == 3.82
    )


def test_trend_series_points_build_week_daily_buckets() -> None:
    """Implement test trend series points build week daily buckets."""
    source = {
        "unit": "kWh",
        "_request": {
            "dateType": "week",
            "beginDate": "2026-04-27",
            "endDate": "2026-05-03",
        },
        "y": [12.54, 15.3, 15.57, 15.36, 15.53, 0.52, 0.0],
    }

    points = util.trend_series_points(
        source,
        "home_trends_week",
        "totalHomeEgy",
        today=util.date(2026, 5, 3),
    )

    assert points == [
        util.TrendStatisticPoint(util.date(2026, 4, 27), 12.54),
        util.TrendStatisticPoint(util.date(2026, 4, 28), 15.3),
        util.TrendStatisticPoint(util.date(2026, 4, 29), 15.57),
        util.TrendStatisticPoint(util.date(2026, 4, 30), 15.36),
        util.TrendStatisticPoint(util.date(2026, 5, 1), 15.53),
        util.TrendStatisticPoint(util.date(2026, 5, 2), 0.52),
        util.TrendStatisticPoint(util.date(2026, 5, 3), 0.0),
    ]


def test_trend_series_points_build_month_daily_buckets_and_skip_future() -> None:
    """Implement test trend series points build month daily buckets and skip future."""
    source = {
        "unit": "kWh",
        "_request": {
            "dateType": "month",
            "beginDate": "2026-05-01",
            "endDate": "2026-05-31",
        },
        "y1": [3.49, 4.35, 0.0, 99.0],
    }

    points = util.trend_series_points(
        source,
        "device_battery_stat_month",
        "totalCharge",
        today=util.date(2026, 5, 3),
    )

    assert points == [
        util.TrendStatisticPoint(util.date(2026, 5, 1), 3.49),
        util.TrendStatisticPoint(util.date(2026, 5, 2), 4.35),
        util.TrendStatisticPoint(util.date(2026, 5, 3), 0.0),
    ]


def test_trend_series_points_build_year_monthly_buckets_and_skip_future() -> None:
    """Implement test trend series points build year monthly buckets and skip future."""
    source = {
        "unit": "kWh",
        # Documented year total anchors compact expansion: 7.84 -> April=7, May=84
        # plus 99 in June. Without this anchor the disambiguation (Path 3b)
        # would publish raw values verbatim per DATA_SOURCE_PRIORITY.md.
        "totalOutGridEnergy": "190",
        "_request": {
            "dateType": "year",
            "beginDate": "2026-01-01",
            "endDate": "2026-12-31",
        },
        "y2": [0.0, 0.0, 0.0, 0.0, 7.84, 99.0],
    }

    points = util.trend_series_points(
        source,
        "device_home_stat_year",
        "totalOutGridEnergy",
        today=util.date(2026, 5, 3),
    )

    assert points == [
        util.TrendStatisticPoint(util.date(2026, 1, 1), 0.0),
        util.TrendStatisticPoint(util.date(2026, 2, 1), 0.0),
        util.TrendStatisticPoint(util.date(2026, 3, 1), 0.0),
        util.TrendStatisticPoint(util.date(2026, 4, 1), 7.0),
        util.TrendStatisticPoint(util.date(2026, 5, 1), 84.0),
    ]


def test_external_trend_statistic_id_uses_colon_external_id() -> None:
    """Implement test external trend statistic id uses colon external id."""
    assert (
        util.external_trend_statistic_id(
            "jackery_solarvault",
            "ABC-123",
            "battery_charge_energy",
            "daily",
        )
        == "jackery_solarvault:abc_123_battery_charge_energy_daily"
    )


def test_app_data_quality_warns_without_repairing_cross_period_totals() -> None:
    """Surface contradictions as warnings, never silent cross-period repairs.

    ``DATA_SOURCE_PRIORITY.md`` is the contract this test enforces.

    Setup: year payload encodes May as compact ``30.28`` (April=30 + May=28)
    with documented ``totalOutGridEnergy=58`` confirming the encoding.
    The full week (89.08 kWh) lies inside the same year, so 58 < 89.08
    is a real contradiction in Jackery's data.
    """
    payload = {
        "device_home_stat_week": {
            "unit": "kWh",
            "_request": {
                "dateType": "week",
                "beginDate": "2026-04-27",
                "endDate": "2026-05-03",
            },
            "totalOutGridEnergy": "89.08",
            "x": ["1", "2", "3", "4", "5", "6", "7"],
            "y2": [15.0, 12.0, 10.0, 11.8, 14.0, 12.0, 14.28],
        },
        "device_home_stat_month": {
            "unit": "kWh",
            "totalOutGridEnergy": "30.28",
            "x": list(range(1, 32)),
            "y2": [15.0, 14.0, 1.28] + [0.0] * 28,
        },
        "device_home_stat_year": {
            "unit": "kWh",
            "_request": {
                "dateType": "year",
                "beginDate": "2026-01-01",
                "endDate": "2026-12-31",
            },
            # Anchor confirming compact: 30.28 in slot[4] -> April=30 + May=28 = 58
            "totalOutGridEnergy": "58",
            "x": [str(i) for i in range(1, 13)],
            "y2": [0.0, 0.0, 0.0, 0.0, 30.28, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
    }

    warnings = util.app_data_quality_warnings(payload, today=util.date(2026, 5, 3))

    assert [warning.reason for warning in warnings] == ["year_less_than_week"]
    assert warnings[0].source_section == "device_home_stat_year"
    assert warnings[0].source_value == 58.0
    assert warnings[0].reference_section == "device_home_stat_week"
    assert warnings[0].reference_value == 89.08
    assert warnings[0].source_request == {
        "dateType": "year",
        "beginDate": "2026-01-01",
        "endDate": "2026-12-31",
    }
    assert warnings[0].reference_request == {
        "dateType": "week",
        "beginDate": "2026-04-27",
        "endDate": "2026-05-03",
    }
    assert warnings[0].source_chart_series_key == "y2"
    assert warnings[0].reference_chart_series_key == "y2"
    assert warnings[0].total_method == "chart_series_sum"


def test_app_data_quality_does_not_warn_month_less_than_week_across_month_boundary() -> (
    None
):
    """Implement test app data quality does not warn month less than week across month boundary."""
    payload = {
        "device_home_stat_week": {
            "unit": "kWh",
            "totalOutGridEnergy": "89.08",
            "y2": [15.0, 12.0, 10.0, 11.8, 14.0, 12.0, 14.28],
        },
        "device_home_stat_month": {
            "unit": "kWh",
            "totalOutGridEnergy": "30.28",
            "y2": [15.0, 14.0, 1.28] + [0.0] * 28,
        },
        "device_home_stat_year": {
            "unit": "kWh",
            "totalOutGridEnergy": "100.00",
            "y2": [0.0, 0.0, 0.0, 0.0, 100.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
    }

    warnings = util.app_data_quality_warnings(payload, today=util.date(2026, 5, 3))

    assert not warnings


def test_app_data_quality_warns_when_lifetime_generation_is_lower_than_year() -> None:
    """Implement test app data quality warns when lifetime generation is lower than year."""
    payload = {
        "statistic": {"totalGeneration": "41.31"},
        "device_pv_stat_year": {
            "unit": "kWh",
            "totalSolarEnergy": "126.97",
            "y": [0.0, 0.0, 0.0, 0.0, 126.97, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
    }

    warnings = util.app_data_quality_warnings(payload, today=util.date(2026, 5, 3))

    assert [warning.reason for warning in warnings] == ["lifetime_less_than_year"]
    assert warnings[0].source_section == "statistic"
    assert warnings[0].reference_section == "device_pv_stat_year"


def test_data_quality_warnings_are_normalized_and_formatted_for_repairs() -> None:
    """Implement test data quality warnings are normalized and formatted for repairs."""
    warning_a = util.AppDataQualityWarning(
        level="warning",
        reason="year_less_than_week",
        metric_key="device_ongrid_output_energy",
        label="Device grid-side output energy",
        source_section="device_home_stat_year",
        source_value=30.28,
        reference_section="device_home_stat_week",
        reference_value=89.08,
    ).as_dict()
    warning_b = dict(warning_a)
    warning_c = util.AppDataQualityWarning(
        level="warning",
        reason="lifetime_less_than_year",
        metric_key="pv_energy",
        label="PV energy",
        source_section="statistic",
        source_value=41.31,
        reference_section="device_pv_stat_year",
        reference_value=126.97,
    ).as_dict()

    normalized = util.normalized_data_quality_warnings([
        warning_b,
        warning_c,
        warning_a,
    ])

    assert normalized == [warning_c, warning_a]
    assert util.format_data_quality_warning(normalized[0]) == (
        "PV energy: statistic=41.31 < device_pv_stat_year=126.97"
    )
    assert util.format_data_quality_warning(normalized[1]) == (
        "Device grid-side output energy: device_home_stat_year=30.28 "
        "< device_home_stat_week=89.08"
    )


def test_data_quality_warning_format_includes_request_ranges_when_available() -> None:
    """Implement test data quality warning format includes request ranges when available."""
    warning = util.AppDataQualityWarning(
        level="warning",
        reason="year_less_than_week",
        metric_key="device_ongrid_output_energy",
        label="Device grid-side output energy",
        source_section="device_home_stat_year",
        source_value=40.45,
        reference_section="device_home_stat_week",
        reference_value=99.25,
        source_request={
            "dateType": "year",
            "beginDate": "2026-01-01",
            "endDate": "2026-12-31",
        },
        reference_request={
            "dateType": "week",
            "beginDate": "2026-04-27",
            "endDate": "2026-05-03",
        },
        source_chart_series_key="y2",
        reference_chart_series_key="y2",
        total_method="chart_series_sum",
    ).as_dict()

    assert warning["source_request"] == {
        "dateType": "year",
        "beginDate": "2026-01-01",
        "endDate": "2026-12-31",
    }
    assert warning["reference_request"] == {
        "dateType": "week",
        "beginDate": "2026-04-27",
        "endDate": "2026-05-03",
    }
    assert warning["source_chart_series_key"] == "y2"
    assert warning["reference_chart_series_key"] == "y2"
    assert warning["total_method"] == "chart_series_sum"
    assert util.format_data_quality_warning(warning) == (
        "Device grid-side output energy: device_home_stat_year=40.45 "
        "< device_home_stat_week=99.25 "
        "[device_home_stat_year: year 2026-01-01..2026-12-31; "
        "device_home_stat_week: week 2026-04-27..2026-05-03]"
    )


def test_year_month_backfill_reconstructs_cloud_month_only_year_payload() -> None:
    """May-only cloud year values are guarded by explicit monthly app payloads."""
    payload = {
        "price": {"singlePrice": "0.28"},
        "statistic": {
            "totalGeneration": "85.57",
            "totalRevenue": "23.96",
            "totalCarbon": "85.31",
        },
        "device_pv_stat_year": {
            "unit": "kWh",
            "totalSolarEnergy": "81.51",
            "totalSolarRevenue": "22.82",
            "pvProfit": 228228000.0,
            "x": [str(i) for i in range(1, 13)],
            "y": [0.0, 0.0, 0.0, 0.0, 81.51, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
        "device_home_stat_year": {
            "unit": "kWh",
            "totalInGridEnergy": "0.11",
            "totalOutGridEnergy": "59.80",
            "x": [str(i) for i in range(1, 13)],
            "y1": [0.0, 0.0, 0.0, 0.0, 0.11, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "y2": [0.0, 0.0, 0.0, 0.0, 59.80, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
        "device_battery_stat_year": {
            "unit": "kWh",
            "totalCharge": "20.96",
            "totalDischarge": "20.99",
            "x": [str(i) for i in range(1, 13)],
            "y1": [0.0, 0.0, 0.0, 0.0, 20.96, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "y2": [0.0, 0.0, 0.0, 0.0, 20.99, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
        "home_trends_year": {
            "unit": "kWh",
            "totalHomeEgy": "59.80",
            "x": [str(i) for i in range(1, 13)],
            "y": [0.0, 0.0, 0.0, 0.0, 59.80, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
    }
    month_history = {
        "device_pv_stat": {
            4: {
                "unit": "kWh",
                "totalSolarEnergy": "146.51",
                "totalSolarRevenue": "41.04",
                "x": list(range(1, 31)),
                "y": [146.51] + [0.0] * 29,
            },
            5: {
                "unit": "kWh",
                "totalSolarEnergy": "81.51",
                "totalSolarRevenue": "22.82",
                "x": list(range(1, 32)),
                "y": [81.51] + [0.0] * 30,
            },
        },
        "device_home_stat": {
            4: {
                "unit": "kWh",
                "totalInGridEnergy": "0.00",
                "totalOutGridEnergy": "107.17",
                "x": list(range(1, 31)),
                "y1": [0.0] * 30,
                "y2": [107.17] + [0.0] * 29,
            },
            5: {
                "unit": "kWh",
                "totalInGridEnergy": "0.11",
                "totalOutGridEnergy": "59.80",
                "x": list(range(1, 32)),
                "y1": [0.11] + [0.0] * 30,
                "y2": [59.80] + [0.0] * 30,
            },
        },
        "device_battery_stat": {
            4: {
                "unit": "kWh",
                "totalCharge": "47.05",
                "totalDischarge": "33.54",
                "x": list(range(1, 31)),
                "y1": [47.05] + [0.0] * 29,
                "y2": [33.54] + [0.0] * 29,
            },
            5: {
                "unit": "kWh",
                "totalCharge": "20.96",
                "totalDischarge": "20.99",
                "x": list(range(1, 32)),
                "y1": [20.96] + [0.0] * 30,
                "y2": [20.99] + [0.0] * 30,
            },
        },
        "home_trends": {
            4: {
                "unit": "kWh",
                "totalHomeEgy": "107.17",
                "x": list(range(1, 31)),
                "y": [107.17] + [0.0] * 29,
            },
            5: {
                "unit": "kWh",
                "totalHomeEgy": "59.80",
                "x": list(range(1, 32)),
                "y": [59.80] + [0.0] * 30,
            },
        },
    }

    util.apply_year_month_backfill(payload, month_history)
    util.guard_statistic_totals_from_year(payload)

    year = payload["device_pv_stat_year"]
    assert year["totalSolarEnergy"] == 228.02
    assert year["totalSolarRevenue"] == 63.86
    assert year["y"] == [
        0.0,
        0.0,
        0.0,
        146.51,
        81.51,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
    ]
    assert year["_year_month_backfill"]["corrected"]["totalSolarEnergy"] == {
        "raw_total": 81.51,
        "corrected_total": 228.02,
        "series_key": "y",
        "months": [4, 5],
    }
    assert payload["statistic"]["totalGeneration"] == 228.02
    assert payload["statistic"]["totalRevenue"] == "23.96"
    assert payload["statistic"]["totalCarbon"] == 227.33
    assert payload["statistic"]["_savings_calculation"]["calculated_total"] == 46.72
    assert payload["statistic"]["_savings_calculation"]["energy_kwh"] == 166.86
    assert payload["statistic"]["_savings_calculation"]["source_energy"] == {
        "pv_year_kwh": 228.02,
        "device_grid_side_input_year_kwh": 0.11,
        "device_grid_side_output_year_kwh": 166.97,
        "device_grid_side_net_output_year_kwh": 166.86,
        "savings_basis_ac_year_kwh": 166.86,
        "home_consumption_year_kwh": 166.97,
        "ct_public_export_year_kwh": None,
        "battery_charge_year_kwh": 68.01,
        "battery_discharge_year_kwh": 54.53,
        "battery_charge_discharge_gap_kwh": 13.48,
        "conversion_loss_year_kwh": 47.68,
        "conversion_loss_year_kwh_signed": 47.68,
        "pv_residual_after_self_consumption_year_kwh": 61.16,
        "pv_not_savings_ac_energy_kwh": 61.16,
    }


def test_year_month_backfill_keeps_larger_correct_cloud_year_payload() -> None:
    """Correct future cloud year payloads must win over partial month history."""
    payload = {
        "price": {"singlePrice": "0.28"},
        "statistic": {
            "totalGeneration": "300.00",
            "totalRevenue": "84.00",
            "totalCarbon": "299.00",
        },
        "device_pv_stat_year": {
            "unit": "kWh",
            "totalSolarEnergy": "228.02",
            "totalSolarRevenue": "63.86",
            "x": [str(i) for i in range(1, 13)],
            "y": [0.0, 0.0, 0.0, 146.51, 81.51, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
        "device_home_stat_year": {
            "unit": "kWh",
            "totalOutGridEnergy": "166.97",
            "x": [str(i) for i in range(1, 13)],
            "y2": [0.0, 0.0, 0.0, 107.17, 59.80, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
        "home_trends_year": {
            "unit": "kWh",
            "totalHomeEgy": "166.97",
            "x": [str(i) for i in range(1, 13)],
            "y": [0.0, 0.0, 0.0, 107.17, 59.80, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
    }
    month_history = {
        "device_pv_stat": {
            5: {
                "unit": "kWh",
                "totalSolarEnergy": "81.51",
                "totalSolarRevenue": "22.82",
                "x": list(range(1, 32)),
                "y": [81.51] + [0.0] * 30,
            },
        }
    }

    util.apply_year_month_backfill(payload, month_history)
    util.guard_statistic_totals_from_year(payload)

    assert payload["device_pv_stat_year"]["totalSolarEnergy"] == "228.02"
    assert "_year_month_backfill" not in payload["device_pv_stat_year"]
    assert payload["statistic"]["totalGeneration"] == "300.00"
    assert payload["statistic"]["totalRevenue"] == "84.00"
    assert (
        payload["statistic"]["_savings_calculation"]["published_value_source"]
        == "cloud_total"
    )
    assert (
        payload["statistic"]["_savings_calculation"]["decision"]
        == "cloud_total_higher_than_current_year_savings"
    )
    assert "_total_lower_bound_guard" not in payload["statistic"]


def test_total_savings_uses_house_side_energy_not_pv_revenue() -> None:
    """A PV-revenue shaped cloud total is kept raw beside calculated savings."""
    payload = {
        "price": {"singlePrice": "0.28"},
        "statistic": {
            "totalGeneration": "228.02",
            "totalRevenue": "63.86",
            "totalCarbon": "227.33",
        },
        "device_pv_stat_year": {
            "unit": "kWh",
            "totalSolarEnergy": "228.02",
            "totalSolarRevenue": "63.86",
            "x": [str(i) for i in range(1, 13)],
            "y": [0.0, 0.0, 0.0, 146.51, 81.51, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
        "device_home_stat_year": {
            "unit": "kWh",
            "totalOutGridEnergy": "166.97",
            "x": [str(i) for i in range(1, 13)],
            "y2": [0.0, 0.0, 0.0, 107.17, 59.80, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
        "home_trends_year": {
            "unit": "kWh",
            "totalHomeEgy": "166.97",
            "x": [str(i) for i in range(1, 13)],
            "y": [0.0, 0.0, 0.0, 107.17, 59.80, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
    }

    util.guard_statistic_totals_from_year(payload)

    assert payload["statistic"]["totalRevenue"] == "63.86"
    assert payload["statistic"]["_savings_calculation"]["calculated_total"] == 46.75
    assert payload["statistic"]["_savings_calculation"]["would_replace_cloud_total"]
    assert (
        payload["statistic"]["_savings_calculation"]["decision"]
        == "cloud_total_matches_pv_revenue_not_savings"
    )


def test_total_savings_subtracts_ct_export_when_available() -> None:
    """Public CT export is subtracted before house-consumption bounding."""
    payload = {
        "price": {"singlePrice": "0.28"},
        "statistic": {
            "totalGeneration": "228.02",
            "totalRevenue": "63.86",
            "totalCarbon": "227.33",
        },
        "device_pv_stat_year": {
            "unit": "kWh",
            "totalSolarEnergy": "228.02",
            "totalSolarRevenue": "63.86",
        },
        "device_home_stat_year": {
            "unit": "kWh",
            "totalOutGridEnergy": "180.00",
        },
        "device_ct_stat_year": {
            "unit": "kWh",
            "totalOutCtEnergy": "20.00",
        },
        "home_trends_year": {
            "unit": "kWh",
            "totalHomeEgy": "166.97",
        },
    }

    util.guard_statistic_totals_from_year(payload)

    assert payload["statistic"]["totalRevenue"] == "63.86"
    assert payload["statistic"]["_savings_calculation"]["calculated_total"] == 44.8
    assert payload["statistic"]["_savings_calculation"]["energy_kwh"] == 160.0
    assert (
        payload["statistic"]["_savings_calculation"]["method"]
        == "device_grid_side_output_minus_ct_export_bounded_by_home"
    )


def test_safe_int_decimal_strings_and_bad_values() -> None:
    """Implement test safe int decimal strings and bad values."""
    assert util.safe_int("8") == 8
    assert util.safe_int("8.0") == 8
    assert util.safe_int(8.9) == 8
    assert util.safe_int(None) is None
    assert util.safe_int("not-a-number") is None


def test_safe_float_parses_decimal_comma_without_deleting_it() -> None:
    """Implement test safe float parses decimal comma without deleting it."""
    assert util.safe_float("40,96") == 40.96
    assert util.safe_float(" 59,43 ") == 59.43
    assert util.safe_float("40,96") != 4096
    assert util.safe_float("1,2,3") is None


def test_device_year_series_decimal_comma_items_use_compact_bucket_semantics() -> None:
    """Compact-bucket expansion is applied when the documented total confirms it.

    Per ``DATA_SOURCE_PRIORITY.md`` the device year series can encode two
    adjacent months in one slot. The ``totalSolarEnergy`` field on the
    payload anchors the disambiguation: ``40,96`` is interpreted as
    ``40 + 96 = 136`` only when the documented total agrees.
    """
    source = {
        "unit": "kWh",
        "totalSolarEnergy": "136",  # anchors the compact interpretation
        "y": ["0", "0", "40,96", "0"],
    }

    assert (
        util.trend_series_total(source, "device_pv_stat_year", "totalSolarEnergy")
        == 136.0
    )
    # Without an array context the month section is plain decimal.
    month_source = {
        "unit": "kWh",
        "totalSolarEnergy": "40.96",
        "y": ["0", "0", "40,96", "0"],
    }
    assert (
        util.trend_series_total(
            month_source, "device_pv_stat_month", "totalSolarEnergy"
        )
        == 40.96
    )
    assert (
        util.trend_series_total(source, "device_pv_stat_year", "totalSolarEnergy")
        != 4096
    )


def test_device_year_compact_bucket_expands_previous_and_current_months() -> None:
    """Documented spec example from REPAIR_ROADMAP.md ("Device year compact bucket expansion").

    Raw series ``[0,0,0,0,13.26,0,...]`` with documented year total ``39``
    is published as ``[0,0,0,13,26,0,...]`` (April=13, May=26). The
    ``totalDischarge="39"`` field is the anchor that confirms the compact
    interpretation; without it path 3b would publish raw.
    """
    source = {
        "unit": "kWh",
        # The documented year total proves compact encoding is in effect.
        "totalDischarge": "39",
        "x": [str(i) for i in range(1, 13)],
        "y2": [0.0, 0.0, 0.0, 0.0, 13.26, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "_request": {
            "dateType": "year",
            "beginDate": "2026-01-01",
            "endDate": "2026-12-31",
        },
    }

    assert util.effective_trend_series_values(
        source, "device_battery_stat_year", "totalDischarge"
    ) == [0.0, 0.0, 0.0, 13.0, 26.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert (
        util.effective_period_total_value(
            source, "device_battery_stat_year", "totalDischarge"
        )
        == 39.0
    )
    assert (
        util.trend_series_total(source, "device_battery_stat_year", "totalDischarge")
        == 39.0
    )


def test_device_year_real_payload_is_published_unchanged_when_total_matches_raw() -> (
    None
):
    """Regression test for the real diag fixture (May 2026 SolarVault Pro Max).

    Diagnostic data showed ``y[4] = 71.72`` paired with
    ``totalSolarEnergy = "71.72"``. The previous unconditional expansion
    inflated the published year value to ``143``, contaminating HA
    long-term statistics with phantom April energy. Disambiguation must
    keep this as a single Float and not invent a second month.
    """
    source = {
        "unit": "kWh",
        # totalSolarEnergy matches sum(raw) -> path 1, no expansion.
        "totalSolarEnergy": "71.72",
        "x": [str(i) for i in range(1, 13)],
        "y": [0.0, 0.0, 0.0, 0.0, 71.72, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "_request": {
            "dateType": "year",
            "beginDate": "2026-01-01",
            "endDate": "2026-12-31",
        },
    }

    assert util.effective_trend_series_values(
        source, "device_pv_stat_year", "totalSolarEnergy"
    ) == [0.0, 0.0, 0.0, 0.0, 71.72, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert (
        util.effective_period_total_value(
            source, "device_pv_stat_year", "totalSolarEnergy"
        )
        == 71.72
    )
    assert (
        util.trend_series_total(source, "device_pv_stat_year", "totalSolarEnergy")
        == 71.72
    )


def test_device_year_inconsistent_payload_publishes_raw_without_repair() -> None:
    """Publish raw and surface contradiction when neither sum matches the total.

    When neither the raw chart sum nor the compact-expanded sum matches the
    documented total field, the integration must publish raw values and
    surface the contradiction via ``data_quality`` (per
    ``STRICT_WORK_INSTRUCTIONS.md`` rule 7: "Never hide, synthesize,
    extrapolate, or repair energy values silently").
    """
    source = {
        "unit": "kWh",
        # Total contradicts both raw (71.72) and expanded (143).
        "totalSolarEnergy": "100",
        "x": [str(i) for i in range(1, 13)],
        "y": [0.0, 0.0, 0.0, 0.0, 71.72, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "_request": {
            "dateType": "year",
            "beginDate": "2026-01-01",
            "endDate": "2026-12-31",
        },
    }

    # Raw is published verbatim — no silent "repair" to either 71.72 or 143.
    values = util.effective_trend_series_values(
        source, "device_pv_stat_year", "totalSolarEnergy"
    )
    assert values == [0.0, 0.0, 0.0, 0.0, 71.72, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert (
        util.trend_series_total(source, "device_pv_stat_year", "totalSolarEnergy")
        == 71.72
    )


def test_month_series_does_not_use_compact_year_expansion() -> None:
    """Implement test month series does not use compact year expansion."""
    source = {
        "unit": "kWh",
        "totalDischarge": "13.26",
        "x": [1, 2, 3],
        "y2": [13.26, 0.0, 0.0],
        "_request": {
            "dateType": "month",
            "beginDate": "2026-05-01",
            "endDate": "2026-05-31",
        },
    }

    assert util.effective_trend_series_values(
        source, "device_battery_stat_month", "totalDischarge"
    ) == [13.26, 0.0, 0.0]
    assert (
        util.trend_series_total(source, "device_battery_stat_month", "totalDischarge")
        == 13.26
    )


def test_config_entry_bool_option_parses_legacy_string_values() -> None:
    """Boolean options must not treat legacy string 'false' as truthy."""

    class Entry:
        options = {"enabled": "false"}
        data = {"enabled": True, "fallback": "yes"}

    assert util.config_entry_bool_option(Entry(), "enabled", True) is False
    assert util.config_entry_bool_option(Entry(), "fallback", False) is True
    assert util.config_entry_bool_option(Entry(), "missing", True) is True


def test_jackery_online_state_parses_numeric_and_text_markers() -> None:
    """Entity availability must handle Jackery string markers safely."""
    assert util.jackery_online_state("0") is False
    assert util.jackery_online_state("1") is True
    assert util.jackery_online_state("offline") is False
    assert util.jackery_online_state("online") is True
    assert util.jackery_online_state("unknown") is None
