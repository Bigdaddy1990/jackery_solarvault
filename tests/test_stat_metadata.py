"""Metadata regression tests for app-period statistics.

These tests use AST/source parsing only so they do not need a Home Assistant
runtime. They guard the integration contract that period totals are not exposed
as monotonically increasing lifetime counters.
"""

import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SENSOR_PATH = ROOT / "custom_components" / "jackery_solarvault" / "sensor.py"
COORDINATOR_PATH = ROOT / "custom_components" / "jackery_solarvault" / "coordinator.py"
INIT_PATH = ROOT / "custom_components" / "jackery_solarvault" / "__init__.py"
CONST_PATH = ROOT / "custom_components" / "jackery_solarvault" / "const.py"
API_PATH = ROOT / "custom_components" / "jackery_solarvault" / "client" / "api.py"
COMPONENT_PATH = ROOT / "custom_components" / "jackery_solarvault"


def _eval_static_string(node: ast.AST, constants: dict[str, str]) -> str | None:
    """Resolve a static string value from an AST node when it can be determined at compile time.

    Supports:
    - string literal constants,
    - names resolved via the provided `constants` mapping,
    - simple f-strings (ast.JoinedStr) composed only of literal parts and resolvable subexpressions.

    Parameters:
        node (ast.AST): The AST node to evaluate.
        constants (dict[str, str]): Mapping of identifier names to string values used to resolve ast.Name nodes.

    Returns:
        str | None: The resolved string when determinable, otherwise `None`.
    """  # noqa: E501, RUF105
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
    """Return the attribute name used for the `state_class` keyword in an AST call expression.

    Parameters:
        call (ast.Call): The AST call node to inspect.

    Returns:
        str | None: The `attr` string from the `state_class=` keyword when its value is an `ast.Attribute`, or `None` if the keyword is absent or not an attribute.
    """  # noqa: E501, RUF105
    for keyword in call.keywords:
        if keyword.arg == "state_class":
            value = keyword.value
            if isinstance(value, ast.Attribute):
                return value.attr
    return None


def _device_class_keyword(call: ast.Call) -> str | None:
    """Extract the attribute name used for the `device_class` keyword from an AST call node.

    Parameters:
        call (ast.Call): The AST call node to inspect for a `device_class=` keyword.

    Returns:
        str | None: The attribute name (the `.attr` value) if `device_class` is provided as an `ast.Attribute`, `None` otherwise.
    """  # noqa: E501, RUF105
    for keyword in call.keywords:
        if keyword.arg == "device_class":
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
    """Collect all AST Call nodes that instantiate `JackeryStatSensorDescription` in the sensor source.

    Returns:
        list[ast.Call]: AST `Call` nodes corresponding to each `JackeryStatSensorDescription(...)` call found when parsing the file at `SENSOR_PATH`.
    """  # noqa: E501, RUF105
    tree = ast.parse(SENSOR_PATH.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "JackeryStatSensorDescription"
    ]


def _savings_detail_description_calls() -> list[ast.Call]:
    """Collect all AST call nodes for `JackerySavingsDetailSensorDescription` in the sensor source.

    Parses the file at `SENSOR_PATH` and returns every `ast.Call` node whose function is named `"JackerySavingsDetailSensorDescription"`.

    Returns:
        calls (list[ast.Call]): List of matching AST `Call` nodes.
    """  # noqa: E501, RUF105
    tree = ast.parse(SENSOR_PATH.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "JackerySavingsDetailSensorDescription"
    ]


def _stat_description_metadata() -> dict[str, dict[str, object]]:
    """Collect metadata for all `JackeryStatSensorDescription` call sites found in the sensor source.

    Only call sites whose `key` argument resolves to a string are included. For each description key the returned mapping contains:
    - `"section"`: the resolved `section` string, or `"statistic"` when not resolvable as a string.
    - `"stat_key"`: the resolved `stat_key` string, or an empty string when not resolvable as a string.
    - `"fallback_sources"`: a tuple of `(left, right)` string pairs parsed from the `fallback_sources` keyword (empty tuple when absent or not resolvable).

    Returns:
        A dict mapping each description `key` (string) to its metadata dict as described above.
    """  # noqa: E501, RUF105
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
    """Extract top-level string literal assignments from a Python source file.

    Parameters:
        path (Path): Path to the Python source file to read.

    Returns:
        dict[str, str]: Mapping of top-level variable names to their string literal values.
        Only plain assignments and annotated assignments where the value is a string literal
        are included; other statement forms and non-string values are ignored.
    """  # noqa: E501, RUF105
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
    """Verify app-period statistic sensor descriptions use `TOTAL` state class and the expected `reset_period` values.

    Asserts that the set of stat description keys present in the integration matches the expected app-period keys, and for each key:
    - the `state_class` is `"TOTAL"`;
    - the `reset_period` equals the expected period string (`"day"`, `"week"`, `"month"`, or `"year"`).
    """  # noqa: E501, RUF105
    expected: dict[str, str] = {
        "today_load": "day",
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
        call_key = _const_keyword(call, "key")
        if isinstance(call_key, str) and call_key in expected:
            found[call_key] = (
                _state_class_keyword(call),
                _const_keyword(call, "reset_period"),
            )

    assert set(found) == set(expected)
    for key, reset_period in expected.items():
        state_class, actual_reset_period = found[key]
        assert state_class == "TOTAL", key
        assert actual_reset_period == reset_period, key


def test_documented_stat_paths_match_const_values() -> None:
    """Verify top-level statistic path constants in const.py match the expected API endpoint strings.

    Asserts that each documented constant (e.g., DEVICE_STATISTIC_PATH, PV_TRENDS_PATH) is defined as the exact path string expected by the integration.
    """  # noqa: E501, RUF105
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
    """Assert that week, month, and year energy stat descriptions use the same source-family section naming pattern.

    This test builds expected source-prefix mappings for several energy families (pv, home, battery_charge, battery_discharge, device_ongrid_input, device_ongrid_output) and verifies that each `<family>_{period}_energy` stat description reports a `section` equal to `<expected_prefix>_{period}` for period in `("week", "month", "year")`.
    """  # noqa: E501, RUF105
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


def test_device_day_sensors_prefer_day_period_sources() -> None:
    """Day energy sensors must prefer dated period sources over stale totals."""
    metadata = _stat_description_metadata()
    expected = {
        "device_today_pv_energy": (
            "device_pv_stat_day",
            "totalSolarEnergy",
            (),
        ),
        "device_today_battery_charge": (
            "device_battery_stat_day",
            "totalCharge",
            (),
        ),
        "device_today_ongrid_input": (
            "device_home_stat_day",
            "totalInGridEnergy",
            (),
        ),
        "device_today_ongrid_output": (
            "device_home_stat_day",
            "totalOutGridEnergy",
            (),
        ),
    }
    for key, (section, stat_key, fallback) in expected.items():
        assert metadata[key]["section"] == section, key
        assert metadata[key]["stat_key"] == stat_key, key
        assert metadata[key]["fallback_sources"] == fallback, key

    assert (
        metadata["device_today_battery_discharge"]["section"]
        == "device_battery_stat_day"
    )
    assert metadata["device_today_battery_discharge"]["stat_key"] == "totalDischarge"
    assert metadata["device_today_battery_discharge"]["fallback_sources"] == ()


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


def test_obsolete_period_entities_are_not_created() -> None:
    """Assert that obsolete period-based entity classes and keys are not created in the sensor source while required internal constant names remain present.

    Checks that:
    - The `JackeryPvTrendsTodaySensor` class is not present in the sensor source.
    - Period-scoped grid import/export keys for week/month/year are not present in `sensor.py` but their corresponding internal constant names (prefixed with `_`) exist in `const.py`.
    - `_pv_today_energy` and `_system_pv_today_energy` internal constants exist in `const.py`.
    """  # noqa: E501, RUF105
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
    """Verify non-app diagnostic sensor classes are not appended to entity lists while their class definitions and diagnostic suffixes remain present.

    This test asserts that specific diagnostic sensor class names appear in the sensor source but are not added to entity construction via `_append_unique(<ClassName>`, and that the corresponding legacy diagnostic suffix strings exist in the constants source.
    """  # noqa: E501, RUF105
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


# Diagnostic/raw entities should stay available for users who need them without
# being enabled by default on every new install.
INTENTIONALLY_DISABLED_BY_DEFAULT: frozenset[str] = frozenset({"power_price"})


def test_former_disabled_app_sensor_suffixes_remain_documented() -> None:
    """Ensure legacy disabled app sensor suffix strings remain present in the integration's constants.

    Asserts that a fixed set of former sensor suffix identifiers (kept for documentation/compatibility) are still contained in the `const.py` source.
    """  # noqa: E501, RUF105
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
    """Verify external app chart statistic bucket constants are period-scoped.

    Asserts that the module-level constants map each DATE_TYPE to the appropriate EXTERNAL_STAT_BUCKET for day, week, month, and year, and that literal string mappings like `"daily"` or `"monthly"` are not used for month/year.
    """  # noqa: E501, RUF105
    source = CONST_PATH.read_text(encoding="utf-8")

    assert "EXTERNAL_STAT_BUCKET_DAY_HOURLY" in source
    assert "DATE_TYPE_WEEK: EXTERNAL_STAT_BUCKET_WEEK_DAILY" in source
    assert "DATE_TYPE_MONTH: EXTERNAL_STAT_BUCKET_MONTH_DAILY" in source
    assert "DATE_TYPE_YEAR: EXTERNAL_STAT_BUCKET_YEAR_MONTHLY" in source
    assert 'DATE_TYPE_MONTH: "daily"' not in source
    assert 'DATE_TYPE_YEAR: "monthly"' not in source


def test_fast_http_property_fetch_is_never_skipped() -> None:
    """MQTT overlays HTTP values but must not suppress HTTP polling.

    The skip hook itself was removed: HTTP is the primary, unconditional
    data path (docs/AGENTS.md transport invariants) and no MQTT liveness
    heuristic may gate, delay, or skip the fast property fetch.
    """
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert "_should_skip_fast_property_fetch" not in coordinator_source
    assert "ADAPTIVE_KEEPALIVE_INTERVAL_SEC" not in coordinator_source
    # MQTT_LIVE_THRESHOLD_SEC may exist for freshness telemetry and local
    # reachability checks, but never inside the HTTP update path decision.


def test_period_sensor_translations_do_not_use_this_period_wording() -> None:
    """Assert that the integration's translation files do not contain locale phrases that use "this week", "this month", or "this year" wording.

    This test reads the component's strings.json and all JSON files in the translations directory and fails if any of the forbidden phrases (English, German, Spanish, and French variants) appear in the source.
    """  # noqa: E501, RUF105
    for path in (
        COMPONENT_PATH / "strings.json",
        *sorted((COMPONENT_PATH / "translations").glob("*.json")),
    ):
        source = path.read_text(encoding="utf-8")
        for forbidden in (
            "diese Woche",
            "dieser Monat",
            "dieses Jahr",
            "this week",
            "this month",
            "this year",
            "esta semana",
            "este mes",
            "este año",
            "cette semaine",
            "ce mois",
            "cette année",
        ):
            assert forbidden not in source


def test_savings_detail_energy_sensor_state_classes_match_semantics() -> None:
    """Existing statistics-compatible savings details keep their state class."""
    calls = _savings_detail_description_calls()
    found: dict[str, tuple[str | None, str | None]] = {}
    for call in calls:
        key = _const_keyword(call, "key")
        if isinstance(key, str):
            found[key] = (_device_class_keyword(call), _state_class_keyword(call))

    energy_keys = {
        key
        for key, (device_class, _state_class) in found.items()
        if device_class == "ENERGY"
    }
    assert energy_keys == {
        "savings_energy",
        "savings_battery_loss_year_energy",
        "savings_conversion_loss_year_energy",
        "savings_pv_residual_year_energy",
    }
    # All four year-energy savings details carry TOTAL uniformly:
    # conversion-loss and PV-residual are the same derived year-period
    # energies as battery-loss, and TOTAL (unlike TOTAL_INCREASING)
    # tolerates downward corrections of the derived values.
    assert found["savings_energy"][1] == "TOTAL"
    assert found["savings_battery_loss_year_energy"][1] == "TOTAL"
    assert found["savings_conversion_loss_year_energy"][1] == "TOTAL"
    assert found["savings_pv_residual_year_energy"][1] == "TOTAL"
    assert found["savings_calculated_total"] == ("MONETARY", "TOTAL")
    assert found["savings_price"] == (None, "MEASUREMENT")


def test_conversion_loss_required_component_check_uses_components_values() -> None:
    """Conversion-loss sensor should validate all component values directly."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    block = sensor_source.split(
        "class JackeryConversionLossPowerSensor(JackeryEntity, SensorEntity):", 1
    )[1].split("BATTERY_PACK_SENSOR_DESCRIPTIONS", 1)[0]

    assert "if any(value is None for value in c.values()):" in block
    assert "required = (" not in block


def test_non_period_stat_source_diagnostics_are_not_overbuilt() -> None:
    """Implement test non period stat source diagnostics are not overbuilt."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    const_source = CONST_PATH.read_text(encoding="utf-8")

    assert "SOURCE_CONTRACT_" not in const_source
    assert "SOURCE_KIND_" not in const_source
    assert 'attrs["source_contract"]' not in sensor_source
    assert 'attrs["source_kind"]' not in sensor_source


def test_stat_state_class_matrix_for_totals_periods_and_prices() -> None:
    """Validate that stat sensor descriptions use the expected `state_class` and `reset_period` values for totals, period totals, and price metrics.

    Builds an expected mapping of stat description keys to their required `(state_class, reset_period)` tuples, locates `JackeryStatSensorDescription` call sites, and asserts:
    - The set of discovered keys matches the expected matrix.
    - Each discovered entry's `(state_class, reset_period)` equals the expected tuple.
    - Any stat description that declares `reset_period` as `day`, `week`, `month`, or `year` uses `state_class == "TOTAL"`.
    """  # noqa: E501, RUF105
    matrix = {
        "today_load": ("TOTAL", "day"),
        "total_generation": ("TOTAL_INCREASING", None),
        # total_revenue uses TOTAL_INCREASING per CHANGELOG "Three-part fix".
        # The HA-validator restriction "MONETARY -> {TOTAL} only" does not
        # apply here because the entity has no monetary device class.
        # TOTAL_INCREASING lets the Recorder treat the
        # midnight cloud transient as a reset rather than misreading it
        # as a real loss.
        "total_revenue": ("TOTAL_INCREASING", None),
        "total_carbon_saved": ("TOTAL_INCREASING", None),
        # power_price is a spot €/kWh reading: MEASUREMENT, consistent
        # with savings_price below (no device class, no reset period).
        "power_price": ("MEASUREMENT", None),
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


# ---------- 2.3.3+: Midnight period race condition guards ---------------


def test_last_reset_is_data_driven_not_wall_clock() -> None:
    """Ensure an entity's `last_reset` is derived from the API request's `begin_date` metadata rather than the wall-clock period start.

    This prevents a midnight race where the recorder records a new day's `last_reset` before fresh period totals arrive, which could appear as a sudden drop. The test verifies the sensor implements `_period_begin_from_meta()`, uses `begin_iso = self._period_begin_from_meta()`, and only falls back to the wall-clock `_period_start` when `begin_iso is None`.
    """  # noqa: E501, RUF105
    sensor_source = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "jackery_solarvault"
        / "sensor.py"
    ).read_text(encoding="utf-8")
    # The last_reset property must consult begin_date metadata
    assert "_period_begin_from_meta" in sensor_source
    # And must NOT just return _period_start unconditionally
    assert "begin_iso = self._period_begin_from_meta()" in sensor_source
    # The fallback to wall-clock _period_start is documented and only
    # applies when begin_iso is None
    assert "if begin_iso is None:" in sensor_source


def test_empty_day_period_entities_can_be_created_from_sibling_charts() -> None:
    """Empty day endpoints must not leave existing PV day entities restored only."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")

    assert "def _day_period_sibling_has_value" in sensor_source
    assert "for date_type in (DATE_TYPE_MONTH, DATE_TYPE_WEEK, DATE_TYPE_YEAR):" in (
        sensor_source
    )
    assert "reset_period = _period_from_stat_description(description)" in sensor_source
    assert "reset_period=reset_period" in sensor_source


def test_day_period_sensors_fallback_to_current_day_chart_bucket() -> None:
    """Day sensors use today's month/week bucket when the day endpoint is empty."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    stat_block = sensor_source.split(
        "class JackeryStatSensor(JackeryEntity, RestoreSensor):", 1
    )[1].split("class JackeryBatteryPackSensor", 1)[0]

    assert "def _chart_value_for_day" in sensor_source
    assert "def _current_day_bucket_from_period_chart" in stat_block
    assert "_current_day_bucket_from_period_chart(" in stat_block
    assert "current_day_bucket_from_" in stat_block


def test_total_revenue_uses_total_increasing_without_monetary_class() -> None:
    """Ensure the `total_revenue` stat description uses SensorStateClass.TOTAL_INCREASING and does not include SensorDeviceClass.MONETARY.

    This test verifies the integration documents `total_revenue` with the `TOTAL_INCREASING` state class and without the `MONETARY` device class to prevent Recorder midnight-reset regressions caused by the validator interaction between `MONETARY` and `state_class`.
    """  # noqa: E501, RUF105
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r"JackeryStatSensorDescription\(\s*\n"
        r"(?:(?!\n    \),).)*?"
        r'key="total_revenue"'
        r"(?:(?!\n    \),).)*",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(sensor_source)
    assert match is not None, "total_revenue description not found"
    block = match.group(0)
    assert "SensorStateClass.TOTAL_INCREASING" in block, (
        "total_revenue must use SensorStateClass.TOTAL_INCREASING per CHANGELOG "
        '"Three-part fix" — TOTAL alone causes the midnight Recorder drop.'
    )
    assert "SensorDeviceClass.MONETARY" not in block, (
        "total_revenue must NOT carry device_class=MONETARY. The integration "
        "docs do not prescribe it, and it forces state_class back to TOTAL via "
        "the HA-validator restriction, undoing the three-part fix."
    )


def test_statistics_backfill_state_is_persisted_on_demand() -> None:
    """Source/day progress survives restarts without extending setup latency."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")
    init_source = INIT_PATH.read_text(encoding="utf-8")

    assert "from homeassistant.helpers.storage import Store" in coordinator_source
    assert "_statistics_backfill_store" in coordinator_source
    assert "async_load_statistics_backfill_state" in coordinator_source
    assert "_async_save_statistics_backfill_state" in coordinator_source
    assert "_async_ensure_statistics_backfill_state_loaded" in coordinator_source
    assert "statistics_backfill_diagnostics" in coordinator_source
    assert "async_load_statistics_backfill_state" not in init_source
    assert "def statistics_import_diagnostics" in coordinator_source


def test_statistics_import_adds_http_backfill_then_current_payload() -> None:
    """Bounded HTTP queues run independently from current Recorder imports."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")
    import_source = coordinator_source.split(
        "async def _async_import_current_app_chart_statistics_job", 1
    )[1].split(
        "\n    # ------------------------------------------------------------------", 1
    )[0]
    backfill_source = coordinator_source.split(
        "async def _async_advance_statistics_backfill", 1
    )[1].split(
        "\n    # ------------------------------------------------------------------", 1
    )[0]

    current_day_import = import_source.index(
        "_async_import_day_chart_statistics(snapshot)"
    )
    current_period_import = import_source.index("_async_import_app_chart_statistics(")
    assert current_day_import < current_period_import

    assert "_async_http_backfill_period_statistics(" not in import_source
    assert "_async_http_backfill_recent_day_statistics(" not in import_source

    day_backfill = backfill_source.index("_async_http_backfill_recent_day_statistics(")
    period_backfill = backfill_source.index("_async_http_backfill_period_statistics(")
    assert day_backfill < period_backfill

    assert "period_pending = period_backfill_result.get(" in backfill_source
    assert "day_pending = backfill_result.get(" in backfill_source
    assert "include_current_year=startup_sync" in backfill_source
    assert "else _STATISTICS_HTTP_BACKFILL_WINDOW_DAYS" in backfill_source
    assert "period_pending == 0 and day_pending == 0" in backfill_source

    # The active bounded job must never call the old unbounded repair state.
    combined_current_backfill = import_source + backfill_source
    assert (
        "_async_repair_missing_app_chart_statistics(" not in combined_current_backfill
    )
    assert "_statistics_repair_from_date(" not in combined_current_backfill
    assert "_statistics_rolling_backfill_from_date(" not in combined_current_backfill
    for legacy_state in (
        "_STATISTICS_BACKFILL_LAST_REPAIR",
        "_STATISTICS_BACKFILL_EXTERNAL_REPAIR_VERSION",
        "_STATISTICS_BACKFILL_ENTITY_REPAIR_VERSION",
    ):
        assert legacy_state not in combined_current_backfill

    # A retained compatibility entry point is safe only when it delegates to
    # the same bounded job instead of reviving the old repair implementation.
    next_section = "\n    # " + ("-" * 66)
    wrapper_source = coordinator_source.split(
        "async def _async_import_and_repair_app_chart_statistics", 1
    )[1].split(next_section, 1)[0]
    assert "_async_import_current_app_chart_statistics_job(" in wrapper_source
    assert "_async_repair_missing_app_chart_statistics(" not in wrapper_source
    assert "_statistics_repair_from_date(" not in wrapper_source


def test_week_month_year_statistic_toggles_filter_imports() -> None:
    """W/M/Y config-flow toggles gate the matching statistic imports.

    When the user disables, say, year statistics in the options/reconfigure
    flow, the coordinator must:

    * Skip the YEAR branch when iterating ``APP_CHART_STAT_PERIODS`` in
      ``_async_import_app_chart_statistics``.
    * Skip the YEAR ``date_type`` filter in
      ``_current_app_chart_entity_source_batches``.

    DAY-hourly external statistics carry the Energy-Dashboard's hour-by-hour
    breakdown and have no HA-vs-Cloud conflict — they stay always on.
    """
    const_source = (COMPONENT_PATH / "const.py").read_text(encoding="utf-8")
    assert (
        'CONF_ENABLE_WEEK_STATISTICS: Final = "enable_week_statistics"'
    ) in const_source
    assert (
        'CONF_ENABLE_MONTH_STATISTICS: Final = "enable_month_statistics"'
    ) in const_source
    assert (
        'CONF_ENABLE_YEAR_STATISTICS: Final = "enable_year_statistics"'
    ) in const_source
    assert "DEFAULT_ENABLE_WEEK_STATISTICS: Final = True" in const_source
    assert "DEFAULT_ENABLE_MONTH_STATISTICS: Final = True" in const_source
    assert "DEFAULT_ENABLE_YEAR_STATISTICS: Final = True" in const_source

    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")
    assert "def _enabled_app_chart_date_types" in coordinator_source

    import_fn = coordinator_source.split(
        "async def _async_import_app_chart_statistics", 1
    )[1].split("\n    async def _async_import_current_app_chart_statistics_job", 1)[0]
    assert "enabled_date_types = self._enabled_app_chart_date_types()" in import_fn
    assert "if date_type not in enabled_date_types:" in import_fn

    period_queue = coordinator_source.split(
        "async def _async_http_backfill_period_statistics", 1
    )[1].split("\n    @property\n    def polling_diagnostics", 1)[0]
    assert "enabled = self._enabled_app_chart_date_types()" in period_queue
    assert "if item[0] in enabled" in period_queue
    assert "_STATISTICS_HTTP_PERIOD_BACKFILL_REQUEST_BUDGET" in period_queue

    # Entity-id imports were removed because HA Recorder owns those rows.
    # Options therefore gate the current external import and period queue only.
    assert "def _current_app_chart_entity_source_batches" not in coordinator_source
    assert (
        "async def _async_import_current_app_chart_entity_statistics"
        not in coordinator_source
    )

    # Config-flow schemas expose the three toggles in both the options-flow
    # and reconfigure entry points.
    config_flow_source = (COMPONENT_PATH / "config_flow.py").read_text(encoding="utf-8")
    for key in (
        "CONF_ENABLE_WEEK_STATISTICS",
        "CONF_ENABLE_MONTH_STATISTICS",
        "CONF_ENABLE_YEAR_STATISTICS",
    ):
        # Both schemas (options-flow init + reconfigure) must reference each
        # constant — at least two occurrences per key.
        assert config_flow_source.count(key) >= 2, key

    # Translations carry the new labels in every locale so HA renders them.
    base = json.loads((COMPONENT_PATH / "strings.json").read_text(encoding="utf-8"))
    for key in (
        "enable_week_statistics",
        "enable_month_statistics",
        "enable_year_statistics",
    ):
        assert key in base["options"]["step"]["init"]["data"], (
            f"{key} missing in strings.json options step"
        )
        assert key in base["config"]["step"]["reconfigure_credentials"]["data"], (
            f"{key} missing in strings.json reconfigure_credentials step"
        )


def test_day_external_history_backfill_uses_http_day_curves() -> None:
    """Day history uses its own dated HTTP curve queue and import path."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert "def _iter_calendar_days" not in coordinator_source
    assert "(DATE_TYPE_DAY, self._iter_calendar_days(from_date, to_date))" not in (
        coordinator_source
    )
    day_fetch = coordinator_source.split(
        "async def _async_fetch_historical_day_chart_source", 1
    )[1].split(
        "\n    async def _async_import_historical_day_chart_statistics_for_device",
        1,
    )[0]
    assert "app_period_request_kwargs(DATE_TYPE_DAY, today=target_day)" in day_fetch

    current_day_source = coordinator_source.split(
        "async def _async_import_day_chart_statistics", 1
    )[1].split("\n    def _enabled_app_chart_date_types", 1)[0]
    assert "EXTERNAL_STAT_BUCKET_DAY_HOURLY" in current_day_source
    assert "APP_DAY_CHART_BUCKET_LABEL" in current_day_source
    assert "_day_chart_points_for_metric(" in current_day_source

    backfill_source = coordinator_source.split(
        "async def _async_import_historical_day_chart_statistics_for_device", 1
    )[1].split("\n    async def _async_http_backfill_recent_day_statistics", 1)[0]
    assert "EXTERNAL_STAT_BUCKET_DAY_HOURLY" in backfill_source
    assert "_day_chart_points_for_metric(" in backfill_source
    assert "_async_add_app_chart_statistics(" in backfill_source


def test_historical_entity_statistics_repair_uses_http_day_curves() -> None:
    """Historical day HTTP buckets feed only the bounded external importer."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert "if day_entity_source_batches:" not in coordinator_source
    assert "if period_entity_source_batches:" not in coordinator_source
    assert "replace_existing_day_hours: bool = True" not in coordinator_source
    assert "replace_existing_hours=replace_existing_day_hours" not in (
        coordinator_source
    )
    backfill_source = coordinator_source.split(
        "async def _async_http_backfill_recent_day_statistics", 1
    )[1].split("\n    async def _async_http_backfill_period_statistics", 1)[0]
    assert "_async_repair_missing_app_chart_statistics(" not in backfill_source
    assert "_async_import_historical_day_chart_statistics_for_device(" in (
        backfill_source
    )
    assert "section_sources={section_prefix: source}" in backfill_source
    assert "replace_existing_hours" not in backfill_source


def test_entity_id_statistics_import_path_is_removed() -> None:
    """Recorder imports use stable external statistic IDs, not entity-ID repairs."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert "async def _async_import_day_chart_statistics" in coordinator_source
    assert "async def _async_import_app_chart_statistics" in coordinator_source
    assert "_async_add_app_chart_statistics(" in coordinator_source


def test_statistics_import_uses_http_backfill_without_old_repair_state() -> None:
    """Automatic backfill is HTTP-only and does not restore old repair state."""
    src = COORDINATOR_PATH.read_text(encoding="utf-8")
    for removed in (
        "_STATISTICS_ROLLING_BACKFILL_WINDOW_DAYS",
        "_STATISTICS_ROLLING_BACKFILL_INTERVAL_SEC",
        "self._last_statistics_rolling_backfill_monotonic",
        "def _statistics_rolling_backfill_from_date",
        "async def async_repair_statistics",
        "_STATISTICS_BACKFILL_LAST_MANUAL_FROM",
        "_STATISTICS_BACKFILL_LAST_SOURCE_COUNTS",
    ):
        assert removed not in src

    import_job = src.split(
        "async def _async_import_current_app_chart_statistics_job", 1
    )[1].split(
        "\n    # ------------------------------------------------------------------", 1
    )[0]
    backfill_job = src.split("async def _async_advance_statistics_backfill", 1)[
        1
    ].split(
        "\n    # ------------------------------------------------------------------", 1
    )[0]
    assert "_statistics_repair_from_date(device_id, today)" not in import_job
    assert "_statistics_rolling_backfill_from_date(" not in import_job
    assert "_async_repair_missing_app_chart_statistics(" not in import_job
    assert "_async_http_backfill_period_statistics(" not in import_job
    assert "_async_http_backfill_recent_day_statistics(" not in import_job
    assert "_async_http_backfill_period_statistics(" in backfill_job
    assert "_async_http_backfill_recent_day_statistics(" in backfill_job
    assert backfill_job.index("_async_http_backfill_recent_day_statistics(") < (
        backfill_job.index("_async_http_backfill_period_statistics(")
    )


def test_statistics_repair_source_matrix_is_removed() -> None:
    """Backfill source matrix diagnostics must not exist."""
    src = COORDINATOR_PATH.read_text(encoding="utf-8")
    for removed in (
        "source_counts: dict[str, dict[str, int]] = {}",
        'source_key = f"{section_prefix}_{date_type}"',
        "self._last_statistics_repair_source_counts",
    ):
        assert removed not in src
