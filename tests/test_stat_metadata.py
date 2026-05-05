"""Metadata regression tests for app-period statistics.

These tests use AST/source parsing only so they do not need a Home Assistant
runtime. They guard the integration contract that period totals are not exposed
as monotonically increasing lifetime counters.
"""

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SENSOR_PATH = ROOT / "custom_components" / "jackery_solarvault" / "sensor.py"
COORDINATOR_PATH = ROOT / "custom_components" / "jackery_solarvault" / "coordinator.py"
INIT_PATH = ROOT / "custom_components" / "jackery_solarvault" / "__init__.py"
CONST_PATH = ROOT / "custom_components" / "jackery_solarvault" / "const.py"
API_PATH = ROOT / "custom_components" / "jackery_solarvault" / "api.py"


def _eval_static_string(node: ast.AST, constants: dict[str, str]) -> str | None:
    """Resolve literal strings, const names and simple f-strings from sensor.py."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for item in node.values:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                parts.append(item.value)
                continue
            if isinstance(item, ast.FormattedValue):
                value = _eval_static_string(item.value, constants)
                if value is None:
                    return None
                parts.append(value)
                continue
            return None
        return "".join(parts)
    return None


def _const_keyword(call: ast.Call, name: str) -> object | None:
    constants = _const_string_assignments(CONST_PATH)
    for keyword in call.keywords:
        if keyword.arg == name:
            value = _eval_static_string(keyword.value, constants)
            if value is not None:
                return value
            if isinstance(keyword.value, ast.Constant):
                return keyword.value.value
    return None


def _state_class_keyword(call: ast.Call) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == "state_class":
            value = keyword.value
            if isinstance(value, ast.Attribute):
                return value.attr
    return None


def _string_tuple_pairs_keyword(
    call: ast.Call,
    name: str,
) -> tuple[tuple[str, str], ...]:
    for keyword in call.keywords:
        if keyword.arg != name:
            continue
        value = keyword.value
        if not isinstance(value, ast.Tuple):
            return ()
        pairs: list[tuple[str, str]] = []
        for item in value.elts:
            if isinstance(item, ast.Tuple) and len(item.elts) == 2:
                constants = _const_string_assignments(CONST_PATH)
                left = _eval_static_string(item.elts[0], constants)
                right = _eval_static_string(item.elts[1], constants)
                if left is not None and right is not None:
                    pairs.append((left, right))
        return tuple(pairs)
    return ()


def _stat_description_calls() -> list[ast.Call]:
    tree = ast.parse(SENSOR_PATH.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "JackeryStatSensorDescription"
    ]


def _stat_description_metadata() -> dict[str, dict[str, object]]:
    metadata: dict[str, dict[str, object]] = {}
    for call in _stat_description_calls():
        key = _const_keyword(call, "key")
        if not isinstance(key, str):
            continue
        section = _const_keyword(call, "section")
        stat_key = _const_keyword(call, "stat_key")
        metadata[key] = {
            "section": section if isinstance(section, str) else "statistic",
            "stat_key": stat_key if isinstance(stat_key, str) else "",
            "fallback_sources": _string_tuple_pairs_keyword(call, "fallback_sources"),
        }
    return metadata


def _const_string_assignments(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                assignments[node.targets[0].id] = node.value.value
            continue
        if isinstance(node, ast.AnnAssign):
            if not isinstance(node.target, ast.Name):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                assignments[node.target.id] = node.value.value
    return assignments


def test_app_period_stat_descriptions_use_total_with_reset_period() -> None:
    """Implement test app period stat descriptions use total with reset period."""
    expected: dict[str, str] = {
        "today_load": "day",
        "today_battery_charge": "day",
        "today_battery_discharge": "day",
        "today_generation": "day",
        "device_today_pv_energy": "day",
        "device_today_battery_charge": "day",
        "device_today_battery_discharge": "day",
        "device_today_ongrid_input": "day",
        "device_today_ongrid_output": "day",
        "device_today_ongrid_to_battery": "day",
        "device_today_pv_to_battery": "day",
        "device_today_battery_to_ongrid": "day",
    }
    for key in (
        "pv",
        "device_pv1",
        "device_pv2",
        "device_pv3",
        "device_pv4",
        "home",
        "battery_charge",
        "battery_discharge",
    ):
        expected[f"{key}_week_energy"] = "week"
        expected[f"{key}_month_energy"] = "month"
        expected[f"{key}_year_energy"] = "year"
    for key in ("device_ongrid_input", "device_ongrid_output"):
        expected[f"{key}_week_energy"] = "week"
        expected[f"{key}_month_energy"] = "month"
        expected[f"{key}_year_energy"] = "year"

    found: dict[str, tuple[str | None, object | None]] = {}
    for call in _stat_description_calls():
        key = _const_keyword(call, "key")
        if isinstance(key, str) and key in expected:
            found[key] = (
                _state_class_keyword(call),
                _const_keyword(call, "reset_period"),
            )

    assert set(found) == set(expected)
    for key, reset_period in expected.items():
        state_class, actual_reset_period = found[key]
        assert state_class == "TOTAL", key
        assert actual_reset_period == reset_period, key


def test_documented_stat_paths_match_const_values() -> None:
    """Implement test documented stat paths match const values."""
    expected_paths = {
        "DEVICE_STATISTIC_PATH": "/v1/device/stat/deviceStatistic",
        "DEVICE_PV_STAT_PATH": "/v1/device/stat/pv",
        "DEVICE_BATTERY_STAT_PATH": "/v1/device/stat/battery",
        "DEVICE_HOME_STAT_PATH": "/v1/device/stat/onGrid",
        "DEVICE_CT_STAT_PATH": "/v1/device/stat/ct",
        "PV_TRENDS_PATH": "/v1/device/stat/sys/pv/trends",
        "HOME_TRENDS_PATH": "/v1/device/stat/sys/home/trends",
        "BATTERY_TRENDS_PATH": "/v1/device/stat/sys/battery/trends",
    }
    assignments = _const_string_assignments(CONST_PATH)
    for key, value in expected_paths.items():
        assert assignments.get(key) == value, key


def test_week_month_year_sensors_keep_same_source_family() -> None:
    """Implement test week month year sensors keep same source family."""
    metadata = _stat_description_metadata()
    expected_source_prefix = {
        "pv": "device_pv_stat",
        "home": "home_trends",
        "battery_charge": "device_battery_stat",
        "battery_discharge": "device_battery_stat",
        "device_ongrid_input": "device_home_stat",
        "device_ongrid_output": "device_home_stat",
    }
    for family, prefix in expected_source_prefix.items():
        for period in ("week", "month", "year"):
            key = f"{family}_{period}_energy"
            assert metadata[key]["section"] == f"{prefix}_{period}", key


def test_device_day_sensors_fallback_to_day_period_sources() -> None:
    """Implement test device day sensors fallback to day period sources."""
    metadata = _stat_description_metadata()
    expected = {
        "device_today_pv_energy": (("device_pv_stat_day", "totalSolarEnergy"),),
        "device_today_battery_charge": (("device_battery_stat_day", "totalCharge"),),
        "device_today_battery_discharge": (
            ("device_battery_stat_day", "totalDischarge"),
        ),
        "device_today_ongrid_input": (("device_home_stat_day", "totalInGridEnergy"),),
        "device_today_ongrid_output": (("device_home_stat_day", "totalOutGridEnergy"),),
    }
    for key, fallback in expected.items():
        assert metadata[key]["section"] == "device_statistic", key
        assert metadata[key]["fallback_sources"] == fallback, key


def test_ct_period_stats_remain_removed_from_polling_and_chart_imports() -> None:
    """Implement test ct period stats remain removed from polling and chart imports."""
    source = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert "device_ct_stat_day" not in source
    assert "device_ct_stat_week" not in source
    assert "device_ct_stat_month" not in source
    assert "device_ct_stat_year" not in source
    const_source = CONST_PATH.read_text(encoding="utf-8")
    chart_metric_block = const_source.partition("APP_CHART_STAT_METRICS")[2].partition(
        ")\n\n# Service names"
    )[0]
    assert "device_ct_stat" not in chart_metric_block


def test_external_chart_import_uses_raw_app_period_points() -> None:
    """Implement test external chart import uses raw app period points."""
    source = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert "trend_series_points(" in source
    assert "Only rows before the rewritten range" in source


def test_device_period_stats_poll_all_app_periods() -> None:
    """Implement test device period stats poll all app periods."""
    source = COORDINATOR_PATH.read_text(encoding="utf-8")

    # Collapse whitespace so multi-line ruff format doesn't break the
    # substring asserts (the call may wrap across lines after formatting).
    flat = re.sub(r"\s+", " ", source)
    assert "for date_type in APP_PERIOD_DATE_TYPES" in flat
    assert (
        "self._app_period_section( APP_SECTION_PV_STAT, date_type )" in flat
        or "self._app_period_section(APP_SECTION_PV_STAT, date_type)" in flat
    )
    assert (
        "self._app_period_section( APP_SECTION_BATTERY_STAT, date_type )" in flat
        or "self._app_period_section(APP_SECTION_BATTERY_STAT, date_type)" in flat
    )
    assert (
        "self._app_period_section( APP_SECTION_HOME_STAT, date_type )" in flat
        or "self._app_period_section(APP_SECTION_HOME_STAT, date_type)" in flat
    )


def test_period_ranges_are_explicit_full_app_periods() -> None:
    """Implement test period ranges are explicit full app periods."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")
    api_source = API_PATH.read_text(encoding="utf-8")
    util_source = (
        ROOT / "custom_components" / "jackery_solarvault" / "util.py"
    ).read_text(encoding="utf-8")

    assert "APP_POLLING_MQTT.md requires explicit app ranges" in coordinator_source
    assert (
        "app_period_request_kwargs(date_type, today=dt_util.now().date())"
        in coordinator_source
    )
    assert "app_period_date_bounds(" in api_source
    assert "begin = today - timedelta(days=today.weekday())" in util_source
    assert "calendar.monthrange(today.year, today.month)" in util_source
    assert "today.replace(month=12, day=31)" in util_source


def test_obsolete_period_entities_are_not_created() -> None:
    """Implement test obsolete period entities are not created."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    INIT_PATH.read_text(encoding="utf-8")
    const_source = CONST_PATH.read_text(encoding="utf-8")

    assert "JackeryPvTrendsTodaySensor" not in sensor_source
    for key in (
        "grid_import_week_energy",
        "grid_import_month_energy",
        "grid_import_year_energy",
        "grid_export_week_energy",
        "grid_export_month_energy",
        "grid_export_year_energy",
    ):
        assert f'key="{key}"' not in sensor_source
        assert f"_{key}" in const_source

    assert "_pv_today_energy" in const_source
    assert "_system_pv_today_energy" in const_source


def test_non_app_diagnostic_sensors_are_not_created() -> None:
    """Implement test non app diagnostic sensors are not created."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    INIT_PATH.read_text(encoding="utf-8")
    const_source = CONST_PATH.read_text(encoding="utf-8")

    for class_name in (
        "JackeryRawPropertiesSensor",
        "JackeryWeatherPlanSensor",
        "JackeryTaskPlanSensor",
        "JackeryTimestampSensor",
        "JackerySystemMetaSensor",
        "JackeryLocationSensor",
    ):
        assert f"_append_unique({class_name}" not in sensor_source
        assert class_name in sensor_source

    for suffix in (
        "_raw_properties",
        "_weather_plan",
        "_task_plan",
        "_last_online",
        "_latitude",
        "_longitude",
    ):
        assert suffix in const_source


def test_app_sensor_descriptions_are_not_disabled_by_default() -> None:
    """Implement test app sensor descriptions are not disabled by default."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")

    assert "entity_registry_enabled_default=False" not in sensor_source
    assert "self._attr_entity_registry_enabled_default = True" in sensor_source


def test_former_disabled_app_sensor_suffixes_remain_documented() -> None:
    """Implement test former disabled app sensor suffixes remain documented."""
    const_source = CONST_PATH.read_text(encoding="utf-8")

    for suffix in (
        "_eps_in_power",
        "_stack_out_power",
        "_system_state",
        "_max_system_output_power",
        "_charge_plan_power",
        "_function_enable_flags",
    ):
        assert suffix in const_source


def test_external_app_chart_statistics_are_period_scoped() -> None:
    """Implement test external app chart statistics are period scoped."""
    source = CONST_PATH.read_text(encoding="utf-8")

    assert "DATE_TYPE_WEEK: EXTERNAL_STAT_BUCKET_WEEK_DAILY" in source
    assert "DATE_TYPE_MONTH: EXTERNAL_STAT_BUCKET_MONTH_DAILY" in source
    assert "DATE_TYPE_YEAR: EXTERNAL_STAT_BUCKET_YEAR_MONTHLY" in source
    assert 'DATE_TYPE_MONTH: "daily"' not in source
    assert 'DATE_TYPE_YEAR: "monthly"' not in source


def test_period_sensor_translations_do_not_use_this_period_wording() -> None:
    """Implement test period sensor translations do not use this period wording."""
    for path in (
        ROOT / "custom_components" / "jackery_solarvault" / "strings.json",
        ROOT / "custom_components" / "jackery_solarvault" / "translations" / "de.json",
        ROOT / "custom_components" / "jackery_solarvault" / "translations" / "en.json",
    ):
        source = path.read_text(encoding="utf-8")
        for forbidden in (
            "diese Woche",
            "dieser Monat",
            "dieses Jahr",
            "this week",
            "this month",
            "this year",
        ):
            assert forbidden not in source


def test_non_period_stat_source_diagnostics_are_not_overbuilt() -> None:
    """Implement test non period stat source diagnostics are not overbuilt."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    const_source = CONST_PATH.read_text(encoding="utf-8")

    assert "SOURCE_CONTRACT_" not in const_source
    assert "SOURCE_KIND_" not in const_source
    assert 'attrs["source_contract"]' not in sensor_source
    assert 'attrs["source_kind"]' not in sensor_source


def test_stat_state_class_matrix_for_totals_periods_and_prices() -> None:
    """Implement test stat state class matrix for totals periods and prices."""
    matrix = {
        "today_load": ("TOTAL", "day"),
        "today_battery_charge": ("TOTAL", "day"),
        "today_battery_discharge": ("TOTAL", "day"),
        "today_generation": ("TOTAL", "day"),
        "total_generation": ("TOTAL_INCREASING", None),
        "total_revenue": ("TOTAL", None),
        "total_carbon_saved": ("TOTAL_INCREASING", None),
        "power_price": (None, None),
    }
    calls = _stat_description_calls()
    found: dict[str, tuple[str | None, object | None]] = {}
    for call in calls:
        key = _const_keyword(call, "key")
        if isinstance(key, str) and key in matrix:
            found[key] = (
                _state_class_keyword(call),
                _const_keyword(call, "reset_period"),
            )

    assert set(found) == set(matrix)
    for key, expected in matrix.items():
        assert found[key] == expected, key

    for call in calls:
        key = _const_keyword(call, "key")
        reset_period = _const_keyword(call, "reset_period")
        if isinstance(key, str) and reset_period in {"day", "week", "month", "year"}:
            assert _state_class_keyword(call) == "TOTAL", key
