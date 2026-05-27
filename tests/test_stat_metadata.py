"""Metadata regression tests for app-period statistics.

These tests use AST/source parsing only so they do not need a Home Assistant
runtime. They guard the integration contract that period totals are not exposed
as monotonically increasing lifetime counters.
"""

import ast
from datetime import UTC, datetime
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


def _device_class_keyword(call: ast.Call) -> str | None:
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
    tree = ast.parse(SENSOR_PATH.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "JackeryStatSensorDescription"
    ]


def _savings_detail_description_calls() -> list[ast.Call]:
    tree = ast.parse(SENSOR_PATH.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "JackerySavingsDetailSensorDescription"
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


def test_device_day_sensors_prefer_day_period_sources() -> None:
    """Day energy sensors must prefer dated period sources over stale totals."""
    metadata = _stat_description_metadata()
    expected = {
        "device_today_pv_energy": (
            "device_pv_stat_day",
            "totalSolarEnergy",
            (("device_statistic", "pvEgy"),),
        ),
        "device_today_battery_charge": (
            "device_battery_stat_day",
            "totalCharge",
            (("device_statistic", "batChgEgy"),),
        ),
        "device_today_ongrid_input": (
            "device_home_stat_day",
            "totalInGridEnergy",
            (("device_statistic", "inOngridEgy"),),
        ),
        "device_today_ongrid_output": (
            "device_home_stat_day",
            "totalOutGridEnergy",
            (("device_statistic", "outOngridEgy"),),
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
    assert metadata["device_today_battery_discharge"]["fallback_sources"] == (
        ("device_statistic", "batDisChgEgy"),
    )


def test_entity_statistics_metric_map_targets_existing_entities() -> None:
    """Cloud-bucket repair keys must resolve to real stat sensor entities."""
    from custom_components.jackery_solarvault.coordinator import (
        _ENTITY_STATISTIC_KEY_BY_METRIC_PERIOD,
        DATE_TYPE_DAY,
    )

    metadata = _stat_description_metadata()
    mapped_keys = {
        entity_key
        for periods in _ENTITY_STATISTIC_KEY_BY_METRIC_PERIOD.values()
        for entity_key in periods.values()
    }

    assert not (mapped_keys - set(metadata))
    assert (
        _ENTITY_STATISTIC_KEY_BY_METRIC_PERIOD["battery_discharge_energy"][
            DATE_TYPE_DAY
        ]
        == "device_today_battery_discharge"
    )


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

    assert "App statistic requests require explicit period ranges" in coordinator_source
    assert "app_period_request_kwargs(date_type, today=self._local_today())" in (
        coordinator_source
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


def test_timestamp_sensor_uses_safe_integer_parser() -> None:
    """Timestamp diagnostics must not cast raw cloud payloads directly."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    block = sensor_source.split("class JackeryTimestampSensor", 1)[1].split(
        "\n\n# ---------------------------------------------------------------------------\n"
        "# Generic system-meta sensor",
        1,
    )[0]

    assert "raw_ts_ms = self._device_meta.get(self._source_key)" in block
    assert "ts_ms = safe_int(raw_ts_ms)" in block
    assert "datetime.fromtimestamp(ts_ms / 1000, tz=UTC)" in block
    assert "except OSError, OverflowError, ValueError:" in block
    assert "int(ts_ms)" not in block


# Diagnostic/raw entities should stay available for users who need them without
# being enabled by default on every new install.
INTENTIONALLY_DISABLED_BY_DEFAULT: frozenset[str] = frozenset({"power_price"})


def test_diagnostic_sensor_descriptions_are_disabled_by_default() -> None:
    """Diagnostic app sensors must not be enabled by default."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    binary_source = (COMPONENT_PATH / "binary_sensor.py").read_text(encoding="utf-8")

    assert "description.entity_category != EntityCategory.DIAGNOSTIC" in sensor_source
    assert "description.entity_category != EntityCategory.DIAGNOSTIC" in binary_source
    assert "pack_desc.entity_registry_enabled_default" in sensor_source
    assert "pack_desc.entity_category" in sensor_source
    assert "_attr_entity_registry_enabled_default = False" in sensor_source

    import re

    pattern = re.compile(
        r'Jackery(?:Stat)?SensorDescription\(\s*\n'
        r'\s*key="([^"]+)"'
        r'(?:(?!\n    \),).)*?'
        r'entity_category=EntityCategory\.DIAGNOSTIC',
        re.MULTILINE | re.DOTALL,
    )
    found = set(pattern.findall(sensor_source))
    missing = INTENTIONALLY_DISABLED_BY_DEFAULT - found
    assert not missing, (
        f"Whitelisted diagnostic keys no longer diagnostic: {sorted(missing)}. "
        f"Either add the entity category back or remove from "
        f"INTENTIONALLY_DISABLED_BY_DEFAULT."
    )


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

    assert "EXTERNAL_STAT_BUCKET_DAY_HOURLY" in source
    assert "DATE_TYPE_WEEK: EXTERNAL_STAT_BUCKET_WEEK_DAILY" in source
    assert "DATE_TYPE_MONTH: EXTERNAL_STAT_BUCKET_MONTH_DAILY" in source
    assert "DATE_TYPE_YEAR: EXTERNAL_STAT_BUCKET_YEAR_MONTHLY" in source
    assert 'DATE_TYPE_MONTH: "daily"' not in source
    assert 'DATE_TYPE_YEAR: "monthly"' not in source


def test_app_chart_curves_use_official_recorder_imports() -> None:
    """App chart buckets use official Recorder APIs only.

    The previous ``unsafe-import cleanup`` and ``state-history restore``
    paths wrote / deleted ``Statistics`` rows through
    ``homeassistant.components.recorder.db_schema`` directly, which is not
    part of the documented integration contract (see docs/ZIP baseline) and
    is fragile across HA versions. They have been removed; what remains are
    the official ``async_add_external_statistics`` /
    ``async_import_statistics`` write paths.
    """
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")
    util_source = (COMPONENT_PATH / "util.py").read_text(encoding="utf-8")
    day_import = coordinator_source.split(
        "async def _async_import_day_chart_statistics", 1
    )[1].split("async def _async_import_app_chart_statistics", 1)[0]
    period_import = coordinator_source.split(
        "async def _async_import_app_chart_statistics", 1
    )[1].split("async def _async_import_current_app_chart_statistics_job", 1)[0]

    assert "def day_power_energy_points(" in util_source
    assert "_async_import_day_chart_statistics(snapshot)" in coordinator_source
    assert "bucket_minutes=60" in coordinator_source
    assert "async_add_external_statistics" in coordinator_source
    assert "_async_import_app_chart_entity_statistics_for_device" in coordinator_source
    assert "_async_compiled_statistic_hour_starts" in coordinator_source
    assert "_APP_CHART_ENTITY_KEY_BY_METRIC_PERIOD" not in coordinator_source
    assert "source=RECORDER_DOMAIN" not in coordinator_source
    assert "async_import_statistics" not in day_import
    assert "async_import_statistics" not in period_import
    assert 'source="recorder"' in coordinator_source
    assert '"last_reset": reset_start' in coordinator_source
    assert "date_type=DATE_TYPE_DAY" not in day_import
    # Removed direct-write/delete paths must stay removed.
    assert (
        "_async_cleanup_unsafe_imported_hourly_statistics_once"
        not in coordinator_source
    )
    assert (
        "_async_restore_cleared_entity_statistics_from_states_once"
        not in coordinator_source
    )
    assert "_hourly_statistics_from_state_rows" not in coordinator_source
    assert "Statistics.id.in_(orphan_hour_ids)" not in coordinator_source
    assert "_UNSAFE_ENTITY_STATISTICS_CLEANUP_VERSION" not in coordinator_source
    assert "StatisticsShortTerm.id.in_(orphan_short_ids)" not in coordinator_source
    assert "async_clear_statistics" not in coordinator_source
    offset_reader = coordinator_source.split(
        "async def _async_entity_statistic_offsets", 1
    )[1].split("\n    async def _async_compiled_statistic_hour_starts", 1)[0]
    assert "statistics_during_period" in offset_reader
    assert "homeassistant.components.recorder.db_schema" not in offset_reader
    assert "StatisticsMeta" not in offset_reader
    assert '"last_reset"' in offset_reader


def test_period_app_bucket_entity_import_fills_only_uncompiled_hours() -> None:
    """Current period imports only fill hours HA has not compiled yet.

    Week/month entity imports may only fill gaps left by HA — never overwrite
    rows HA already covered via its 5-minute statistics run at HH:55.

    The contributing rows below carry hours 2026-05-01, 05-02, 05-03.
    HA already compiled 05-01 and 05-03 (they live in
    ``compiled_hour_starts``); only 05-02 is a gap and may be written.
    """
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")
    assert "if not contributions:" in coordinator_source
    assert "statistics == []" not in coordinator_source

    from custom_components.jackery_solarvault.coordinator import (
        DATE_TYPE_MONTH,
        JackerySolarVaultCoordinator,
    )

    coordinator = object.__new__(JackerySolarVaultCoordinator)
    coordinator._local_timezone = lambda: UTC

    def start(day: int) -> datetime:
        return datetime(2026, 5, day, tzinfo=UTC)

    compiled = {
        int(start(1).timestamp()),
        int(start(3).timestamp()),
    }
    statistics = coordinator._entity_statistics_from_contributions(
        [
            (start(1), 22.29, DATE_TYPE_MONTH, True),
            (start(2), 19.02, DATE_TYPE_MONTH, True),
            (start(3), 18.12, DATE_TYPE_MONTH, True),
        ],
        compiled_hour_starts=compiled,
        sum_offset=100.0,
        state_offset=10.0,
    )

    # Only the gap hour 2026-05-02 is filled. state continues the entity's
    # previous reset window (state_offset 10.0 + bucket 19.02 = 29.02);
    # sum continues the previous monotonic total (100.0 + 19.02 = 119.02).
    assert [(stat["state"], stat["sum"]) for stat in statistics] == [
        (29.02, 119.02),
    ]
    assert [int(stat["start"].timestamp()) for stat in statistics] == [
        int(start(2).timestamp()),
    ]
    assert {stat["last_reset"] for stat in statistics} == {start(1)}


def test_historical_day_entity_repair_can_replace_compiled_spike_rows() -> None:
    """Historical day repair rewrites first-run spike rows from app curves."""
    from custom_components.jackery_solarvault.coordinator import (
        DATE_TYPE_DAY,
        JackerySolarVaultCoordinator,
    )

    coordinator = object.__new__(JackerySolarVaultCoordinator)
    coordinator._local_timezone = lambda: UTC

    def hour(hour: int) -> datetime:
        return datetime(2026, 5, 17, hour, tzinfo=UTC)

    compiled = {
        int(hour(19).timestamp()),
        int(hour(20).timestamp()),
    }
    statistics = coordinator._entity_statistics_from_contributions(
        [
            (hour(19), 0.35, DATE_TYPE_DAY, True),
            (hour(20), 0.42, DATE_TYPE_DAY, True),
        ],
        compiled_hour_starts=compiled,
        replace_existing_hours=True,
        sum_offset=3.0,
        state_offset=1.0,
    )

    assert [(stat["state"], stat["sum"]) for stat in statistics] == [
        (1.35, 3.35),
        (1.77, 3.77),
    ]
    assert [int(stat["start"].timestamp()) for stat in statistics] == [
        int(hour(19).timestamp()),
        int(hour(20).timestamp()),
    ]


def test_fast_http_property_fetch_is_never_skipped() -> None:
    """MQTT overlays HTTP values but must not suppress HTTP polling."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")
    method_source = coordinator_source.split("def _should_skip_fast_property_fetch", 1)[
        1
    ].split("async def async_shutdown", 1)[0]

    assert "return False" in method_source
    assert "MQTT_LIVE_THRESHOLD_SEC" not in coordinator_source
    assert "ADAPTIVE_KEEPALIVE_INTERVAL_SEC" not in coordinator_source


def test_period_sensor_translations_do_not_use_this_period_wording() -> None:
    """Implement test period sensor translations do not use this period wording."""
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
    assert found["savings_energy"][1] == "TOTAL"
    assert found["savings_battery_loss_year_energy"][1] == "TOTAL"
    assert found["savings_conversion_loss_year_energy"][1] is None
    assert found["savings_pv_residual_year_energy"][1] is None
    assert found["savings_calculated_total"] == ("MONETARY", "TOTAL")
    assert found["savings_price"] == (None, "MEASUREMENT")


def test_savings_price_rounding_uses_named_precision_constant() -> None:
    """Savings price precision should be named, not an unexplained literal."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")

    assert "SAVINGS_PRICE_PRECISION = 5" in sensor_source
    assert "round(value, SAVINGS_PRICE_PRECISION)" in sensor_source
    assert "round(value, 5)" not in sensor_source


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
    """Implement test stat state class matrix for totals periods and prices."""
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


# ---------- 2.3.3+: Midnight period race condition guards ---------------


def test_last_reset_is_data_driven_not_wall_clock() -> None:
    """``last_reset`` must derive from APP_REQUEST_META, not dt_util.now().

    Wall-clock anchoring caused a midnight race: at 00:00:01 local time
    HA Recorder saw ``last_reset = today 00:00`` together with the
    cloud's still-stale yesterday total, so the new day's bucket
    started at yesterday's value and looked like a loss when the real
    smaller value arrived seconds later. The fix anchors to the
    begin_date stamped on the source by the API request, advancing
    only when fresh data has actually arrived.
    """
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


def test_period_sensors_do_not_publish_stale_period_totals() -> None:
    """Stale-period guard publishes None for ALL periods (CHANGELOG fix).

    Per CHANGELOG "Three-part fix" / Midnight race: when the wall clock
    has crossed a period boundary but the source data still has the
    previous period's begin_date, ``native_value`` is set to ``None``
    for ALL periods (including DAY). HA Recorder writes ``unavailable``
    for that brief window and never sees an artificial spike+drop.

    A previous ``raw = 0 if DAY else None`` carve-out (intended UX of
    "show 0 for fresh day") reintroduced the midnight delta bug — the
    Recorder saw ``state=0`` next to yesterday's positive value with
    the same ``last_reset`` and computed a negative delta. Never
    reintroduce the carve-out.
    """
    sensor_source = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "jackery_solarvault"
        / "sensor.py"
    ).read_text(encoding="utf-8")
    # The helper exists.
    assert "def _is_period_data_stale(self) -> bool:" in sensor_source
    # And is consulted in _refresh_cache before assigning native_value.
    assert (
        "stale_period = self._reset_period and self._is_period_data_stale()"
        in sensor_source
    )
    # All period sensors publish None (incl. DAY) when stale. NEVER a
    # carve-out for DAY periods publishing 0.
    assert "raw = 0 if self._reset_period == DATE_TYPE_DAY" not in sensor_source, (
        "Stale-guard must not publish raw=0 for DAY — re-creates midnight spike."
    )
    assert "if stale_period or future_period:" in sensor_source, (
        "stale/future guard must collapse to a single None-assignment branch."
    )
    # Attribute is informative: "unavailable until the next refresh cycle
    # lands within the local period".
    assert (
        'attrs["stale_period_fallback"] = "unknown_until_local_period"'
    ) in sensor_source
    assert "def _non_negative_period_raw(self, raw: Any) -> Any:" in sensor_source
    assert "parsed is not None and parsed < 0" in sensor_source


def test_period_sensors_do_not_publish_future_period_totals() -> None:
    """Early next-period app payloads must not create negative Energy deltas."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    assert "def _is_period_data_future(self) -> bool:" in sensor_source
    assert "data_begin > wall_clock_start.date()" in sensor_source
    assert "if self._is_period_data_future():" in sensor_source
    assert (
        "future_period = self._reset_period and self._is_period_data_future()"
        in sensor_source
    )
    # The stale/future branches collapse to a single None-assignment per
    # the CHANGELOG three-part fix (None for ALL periods, including DAY).
    assert (
        "if stale_period or future_period:\n                raw = None" in sensor_source
    )
    assert 'attrs["future_period_data"] = True' in sensor_source
    assert (
        'attrs["future_period_fallback"] = "unknown_until_local_period"'
        in sensor_source
    )
    assert 'self._cached_attrs["future_period_data"] = True' in sensor_source
    assert (
        'self._cached_attrs["future_period_fallback"] = "unknown_until_local_period"'
        in sensor_source
    )


def test_empty_day_period_entities_can_be_created_from_sibling_charts() -> None:
    """Empty day endpoints must not leave existing PV day entities restored only."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")

    assert "def _day_period_sibling_has_value" in sensor_source
    assert "for date_type in (DATE_TYPE_MONTH, DATE_TYPE_WEEK, DATE_TYPE_YEAR):" in (
        sensor_source
    )
    assert "reset_period = _period_from_stat_description(description)" in sensor_source
    assert "reset_period=reset_period" in sensor_source


def test_zero_period_totals_create_entities_without_chart_series() -> None:
    """Monday week payloads can have ``y: null`` but scalar total ``0``.

    A scalar period total, including zero, is still a valid app statistic.
    Entity creation must not require a non-empty chart series, otherwise
    week entities disappear on Monday morning.
    """
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    helper = sensor_source.split("def _stat_description_has_value", 1)[1].split(
        "\n\nSTAT_DESCRIPTIONS:",
        1,
    )[0]

    assert "trend_series_has_value(" in helper
    assert "effective_period_total_value(" in helper
    assert helper.index("trend_series_has_value(") < helper.index(
        "effective_period_total_value("
    )


def test_day_period_sensors_fallback_to_current_day_chart_bucket() -> None:
    """Day sensors use today's month/week bucket when the day endpoint is empty."""
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    stat_block = sensor_source.split(
        "class JackeryStatSensor(JackeryEntity, SensorEntity):", 1
    )[1].split("class JackeryBatteryPackSensor", 1)[0]

    assert "def _chart_value_for_day" in sensor_source
    assert "def _current_day_bucket_from_period_chart" in stat_block
    assert "_current_day_bucket_from_period_chart(" in stat_block
    assert "current_day_bucket_from_" in stat_block


def test_total_revenue_uses_total_increasing_without_monetary_class() -> None:
    """``total_revenue`` must use TOTAL_INCREASING without MONETARY device_class.

    History (CHANGELOG and 2026-05-16 user audit):

    * The CHANGELOG "Three-part fix" / Midnight race condition
      explicitly sets ``state_class=TOTAL_INCREASING`` so the Recorder
      treats the midnight cloud transient as a reset rather than as a
      real loss (no negative spikes in CO2/Einnahmen).
    * An earlier revert added ``device_class=MONETARY``, which triggers
      HA's validator restriction "MONETARY only allows TOTAL or None"
      and forced ``state_class=TOTAL``. That regression reintroduced
      the midnight drop.
    * The user pointed out that ``MONETARY`` is not part of the intended
      entity model. Removing it eliminates the validator conflict and
      restores TOTAL_INCREASING.

    Lock the docs-compliant combination so it cannot regress again.
    """
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")
    pattern = re.compile(
        r'JackeryStatSensorDescription\(\s*\n'
        r'(?:(?!\n    \),).)*?'
        r'key="total_revenue"'
        r'(?:(?!\n    \),).)*',
        re.S | re.M,
    )
    match = pattern.search(sensor_source)
    assert match is not None, "total_revenue description not found"
    block = match.group(0)
    assert "SensorStateClass.TOTAL_INCREASING" in block, (
        'total_revenue must use SensorStateClass.TOTAL_INCREASING per CHANGELOG '
        '"Three-part fix" — TOTAL alone causes the midnight Recorder drop.'
    )
    assert "SensorDeviceClass.MONETARY" not in block, (
        "total_revenue must NOT carry device_class=MONETARY. The integration "
        "docs do not prescribe it, and it forces state_class back to TOTAL via "
        "the HA-validator restriction, undoing the three-part fix."
    )


def test_statistics_backfill_state_is_removed() -> None:
    """Extra statistics backfill state must not be loaded or persisted."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")
    init_source = INIT_PATH.read_text(encoding="utf-8")

    assert "from homeassistant.helpers.storage import Store" not in coordinator_source
    assert "_statistics_backfill_store" not in coordinator_source
    assert "async_load_statistics_backfill_state" not in coordinator_source
    assert "_async_save_statistics_backfill_state" not in coordinator_source
    assert "statistics_backfill_diagnostics" not in coordinator_source
    assert "async_load_statistics_backfill_state" not in init_source
    assert "def statistics_import_diagnostics" in coordinator_source


def test_statistics_import_adds_http_backfill_then_current_payload() -> None:
    """Bounded HTTP backfill runs before current buckets for sum continuity."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")
    import_source = coordinator_source.split(
        "async def _async_import_current_app_chart_statistics_job", 1
    )[1].split(
        "\n    # ------------------------------------------------------------------", 1
    )[0]

    assert "async def _async_repair_missing_app_chart_statistics" not in (
        coordinator_source
    )
    assert "async def async_repair_statistics" not in coordinator_source
    assert "_async_add_app_chart_entity_statistics" not in coordinator_source
    assert "_STATISTICS_BACKFILL_LAST_ENTITY_REPAIR" not in coordinator_source
    assert "_STATISTICS_BACKFILL_LAST_DAY_ENTITY_REPAIR" not in coordinator_source
    assert "_STATISTICS_BACKFILL_EXTERNAL_REPAIR_VERSION" not in coordinator_source
    assert "_EXTERNAL_STATISTICS_REPAIR_VERSION" not in coordinator_source
    assert "_STATISTICS_BACKFILL_ENTITY_REPAIR_VERSION" not in coordinator_source
    assert "_ENTITY_STATISTICS_REPAIR_VERSION" not in coordinator_source
    current_import = import_source.index(
        "successful_devices = await self._async_import_app_chart_statistics(snapshot)"
    )
    current_entity_import = import_source.index(
        "_async_import_current_app_chart_entity_statistics(snapshot)"
    )
    http_backfill = import_source.index("_async_http_backfill_recent_day_statistics(")
    assert "_async_repair_missing_app_chart_statistics(" not in import_source
    assert "_statistics_repair_from_date(" not in import_source
    assert "_statistics_rolling_backfill_from_date(" not in import_source
    current_payload_source = coordinator_source.split(
        "def _current_app_chart_entity_source_batches", 1
    )[1].split("\n    async def _async_import_current_app_chart_entity_statistics", 1)[
        0
    ]
    assert "for date_type in (DATE_TYPE_DAY, DATE_TYPE_WEEK, DATE_TYPE_MONTH):" in (
        current_payload_source
    )
    assert "DATE_TYPE_YEAR" not in current_payload_source
    assert http_backfill < current_import
    assert current_import < current_entity_import


def test_day_entity_repair_can_replace_recorder_spikes() -> None:
    """Daily ``sensor.*`` rows may replace Recorder spike rows."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")
    targets_fn = coordinator_source.split("def _entity_targets_for_app_points", 1)[
        1
    ].split("\n    def _completed_entity_app_points", 1)[0]
    month_branch = targets_fn.split("if date_type == DATE_TYPE_MONTH:", 1)[1].split(
        "return tuple(targets)", 1
    )[0]

    assert "if date_type == DATE_TYPE_DAY:" in targets_fn
    assert "periods.get(DATE_TYPE_DAY)" in targets_fn
    assert "day_key" not in month_branch
    assert "periods.get(DATE_TYPE_MONTH)" in month_branch
    assert "periods.get(DATE_TYPE_YEAR)" in month_branch

    importer = coordinator_source.split(
        "async def _async_import_app_chart_entity_statistics_for_device", 1
    )[1].split("\n    def _current_app_chart_entity_source_batches", 1)[0]
    assert "if date_type == DATE_TYPE_DAY:" in importer
    assert "day_power_energy_points(" in importer
    assert "_async_compiled_statistic_hour_starts" in importer
    assert "compiled_hour_starts" in importer
    assert "replace_existing_hours" in importer
    assert "include_current_day_completed" in importer

    current_import = coordinator_source.split(
        "async def _async_import_current_app_chart_entity_statistics", 1
    )[1].split("\n    async def _async_update_data_quality_issue", 1)[0]
    day_call = current_import.split("if day_batches:", 1)[1].split(
        "if period_batches:", 1
    )[0]
    assert "replace_existing_hours=True" in day_call
    assert "include_current_day_completed=True" in day_call

    assert "_DAY_ENTITY_STATISTICS_REPAIR_WINDOW_DAYS" not in coordinator_source


def test_day_chart_sources_prefer_device_minute_curves_for_pv_and_battery() -> None:
    """PV/battery day buckets must not depend on optional system trends."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")
    source_map = coordinator_source.split("_DAY_TREND_SOURCE_BY_METRIC_KEY = {", 1)[
        1
    ].split("\n}", 1)[0]
    fallback_map = coordinator_source.split("_METRIC_SOURCE_FALLBACKS", 1)[1].split(
        "\n}\n",
        1,
    )[0]
    candidates_fn = coordinator_source.split("def _day_chart_source_candidates", 1)[
        1
    ].split("\n    def _day_chart_points_for_metric", 1)[0]
    fetch_system = coordinator_source.split("async def _fetch_system", 1)[1].split(
        "\n        async def _fetch_device_extras", 1
    )[0]

    assert '"pv_energy"' not in source_map
    assert '"battery_charge_energy"' not in source_map
    assert '"battery_discharge_energy"' not in source_map
    assert '"home_energy"' in source_map
    assert '"home_energy"' not in fallback_map
    assert "_metric_source_candidates(" in candidates_fn
    assert 'f"{candidate_prefix}_{DATE_TYPE_DAY}"' in candidates_fn
    assert "system_trend_timeout_sec" in coordinator_source
    assert "timeout_sec=system_trend_timeout_sec" in fetch_system


def test_historical_statistics_fetch_path_is_bounded_http_backfill() -> None:
    """Historical day backfill uses explicit HTTP app-stat requests."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert "async def _async_fetch_historical_app_chart_source" not in (
        coordinator_source
    )
    assert "async def _async_fetch_historical_day_chart_sources" in (coordinator_source)
    assert "_STATISTICS_HTTP_BACKFILL_WINDOW_DAYS = 7" in coordinator_source
    assert "_STATISTICS_HTTP_BACKFILL_INTERVAL_SEC = 6 * 60 * 60" in (
        coordinator_source
    )
    assert "app_period_request_kwargs(DATE_TYPE_DAY, today=target_day)" in (
        coordinator_source
    )
    assert "Skip historical app chart fetch" not in coordinator_source
    assert "period_start > today" not in coordinator_source


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

    assert "async def _async_repair_missing_app_chart_statistics" not in (
        coordinator_source
    )

    current_source = coordinator_source.split(
        "def _current_app_chart_entity_source_batches", 1
    )[1].split("\n    async def _async_import_current_app_chart_entity_statistics", 1)[
        0
    ]
    assert "enabled_date_types = self._enabled_app_chart_date_types()" in current_source

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
        assert key in base["config"]["step"]["reconfigure"]["data"], (
            f"{key} missing in strings.json reconfigure step"
        )

    # Runtime: the helper honours the entry options. DAY stays on always.
    from custom_components.jackery_solarvault.coordinator import (
        DATE_TYPE_DAY,
        DATE_TYPE_MONTH,
        DATE_TYPE_WEEK,
        DATE_TYPE_YEAR,
        JackerySolarVaultCoordinator,
    )

    class _StubEntry:
        def __init__(self, options: dict[str, bool]) -> None:
            self.options = options
            self.data: dict[str, bool] = {}

    coordinator = object.__new__(JackerySolarVaultCoordinator)
    coordinator.entry = _StubEntry({
        "enable_week_statistics": True,
        "enable_month_statistics": True,
        "enable_year_statistics": True,
    })
    assert coordinator._enabled_app_chart_date_types() == {
        DATE_TYPE_DAY,
        DATE_TYPE_WEEK,
        DATE_TYPE_MONTH,
        DATE_TYPE_YEAR,
    }

    coordinator.entry = _StubEntry({
        "enable_week_statistics": False,
        "enable_month_statistics": False,
        "enable_year_statistics": False,
    })
    assert coordinator._enabled_app_chart_date_types() == {DATE_TYPE_DAY}

    coordinator.entry = _StubEntry({
        "enable_week_statistics": True,
        "enable_month_statistics": False,
        "enable_year_statistics": True,
    })
    assert coordinator._enabled_app_chart_date_types() == {
        DATE_TYPE_DAY,
        DATE_TYPE_WEEK,
        DATE_TYPE_YEAR,
    }


def test_day_external_history_backfill_uses_http_day_curves() -> None:
    """Day external statistics may be repaired from historical HTTP curves."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert "def _iter_calendar_days" not in coordinator_source
    assert "(DATE_TYPE_DAY, self._iter_calendar_days(from_date, to_date))" not in (
        coordinator_source
    )
    assert "app_period_request_kwargs(DATE_TYPE_DAY, today=period_start)" not in (
        coordinator_source
    )

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
    """Historical day HTTP buckets can replace Recorder spike rows."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")

    assert "async def _async_repair_missing_app_chart_statistics" not in (
        coordinator_source
    )
    assert "if day_entity_source_batches:" not in coordinator_source
    assert "if period_entity_source_batches:" not in coordinator_source
    assert "replace_existing_day_hours: bool = True" not in coordinator_source
    assert "replace_existing_hours=replace_existing_day_hours" not in (
        coordinator_source
    )
    backfill_source = coordinator_source.split(
        "async def _async_http_backfill_recent_day_statistics", 1
    )[1].split("\n    async def _async_update_data_quality_issue", 1)[0]
    assert "source_batches=[(DATE_TYPE_DAY, section_sources)]" in backfill_source
    assert "replace_existing_hours=True" in backfill_source


def test_entity_statistics_import_handles_day_week_month_not_year() -> None:
    """Entity-stats import fills current day/week/month; year remains external."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")

    importer = coordinator_source.split(
        "async def _async_import_app_chart_entity_statistics_for_device", 1
    )[1].split("\n    def _current_app_chart_entity_source_batches", 1)[0]
    assert "if date_type == DATE_TYPE_DAY:" in importer
    assert "day_power_energy_points(" in importer
    assert "_day_chart_points_for_metric(" not in importer
    assert "payload: dict[str, Any]" not in importer
    assert "payload=payload" not in importer

    targets_fn = coordinator_source.split("def _entity_targets_for_app_points", 1)[
        1
    ].split("\n    def _completed_entity_app_points", 1)[0]
    assert "if date_type == DATE_TYPE_DAY:" in targets_fn
    assert "periods.get(DATE_TYPE_DAY)" in targets_fn
    assert "periods.get(DATE_TYPE_WEEK)" in targets_fn
    assert "periods.get(DATE_TYPE_MONTH)" in targets_fn

    completed_fn = coordinator_source.split("def _completed_entity_app_points", 1)[
        1
    ].split("\n    def _entity_statistics_from_contributions", 1)[0]
    assert "DATE_TYPE_DAY" in completed_fn
    assert "include_current_day_completed" in completed_fn
    assert "current_hour_start" in completed_fn
    assert "date_type: str," not in completed_fn

    assert "day_entity_source_batches.append" not in coordinator_source
    assert "period_entity_source_batches.append" not in coordinator_source
    assert "(DATE_TYPE_DAY, self._iter_calendar_days(from_date, to_date))" not in (
        coordinator_source
    )


def test_period_time_math_uses_home_assistant_local_timezone() -> None:
    """App day/week/month/year boundaries must not use UTC by accident."""
    coordinator_source = COORDINATOR_PATH.read_text(encoding="utf-8")
    sensor_source = SENSOR_PATH.read_text(encoding="utf-8")

    assert "def _local_timezone(self)" in coordinator_source
    assert "def _local_today(self)" in coordinator_source
    assert "today=self._local_today()" in coordinator_source
    assert "now = self._local_now()" in coordinator_source
    assert "today = self._local_today()" in coordinator_source
    assert "def _local_timezone(self)" in sensor_source
    assert "_period_start(self._reset_period, self._local_timezone())" in sensor_source
    assert "today = self._local_today()" in sensor_source


def test_statistics_repair_seed_path_is_removed() -> None:
    """First-run historical statistic seeding must not exist."""
    src = COORDINATOR_PATH.read_text(encoding="utf-8")
    assert "def _statistics_repair_from_date(" not in src
    assert "_statistics_current_year_recovery_needed" not in src
    assert "_STATISTICS_BACKFILL_LAST_REPAIR" not in src


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
    assert "_statistics_repair_from_date(device_id, today)" not in import_job
    assert "_statistics_rolling_backfill_from_date(" not in import_job
    assert "_async_repair_missing_app_chart_statistics(" not in import_job
    assert "_async_http_backfill_recent_day_statistics(" in import_job


def test_statistics_repair_source_matrix_is_removed() -> None:
    """Backfill source matrix diagnostics must not exist."""
    src = COORDINATOR_PATH.read_text(encoding="utf-8")
    for removed in (
        "source_counts: dict[str, dict[str, int]] = {}",
        'source_key = f"{section_prefix}_{date_type}"',
        "self._last_statistics_repair_source_counts",
    ):
        assert removed not in src
