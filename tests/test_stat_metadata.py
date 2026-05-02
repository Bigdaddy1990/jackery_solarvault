"""Metadata regression tests for app-period statistics.

These tests use AST/source parsing only so they do not need a Home Assistant
runtime. They guard the integration contract that period totals are not exposed
as monotonically increasing lifetime counters.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENSOR_PATH = ROOT / "custom_components" / "jackery_solarvault" / "sensor.py"
COORDINATOR_PATH = ROOT / "custom_components" / "jackery_solarvault" / "coordinator.py"


def _const_keyword(call: ast.Call, name: str) -> object | None:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def _state_class_keyword(call: ast.Call) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == "state_class":
            value = keyword.value
            if isinstance(value, ast.Attribute):
                return value.attr
    return None


def _stat_description_calls() -> list[ast.Call]:
    tree = ast.parse(SENSOR_PATH.read_text())
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "JackeryStatSensorDescription"
    ]


def test_app_period_stat_descriptions_use_total_with_reset_period() -> None:
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
        "home",
        "grid_import",
        "grid_export",
        "smart_meter_import",
        "smart_meter_export",
        "battery_charge",
        "battery_discharge",
    ):
        expected[f"{key}_week_energy"] = "week"
        expected[f"{key}_month_energy"] = "month"
        expected[f"{key}_year_energy"] = "year"

    found: dict[str, tuple[str | None, object | None]] = {}
    for call in _stat_description_calls():
        key = _const_keyword(call, "key")
        if isinstance(key, str) and key in expected:
            found[key] = (_state_class_keyword(call), _const_keyword(call, "reset_period"))

    assert set(found) == set(expected)
    for key, reset_period in expected.items():
        state_class, actual_reset_period = found[key]
        assert state_class == "TOTAL", key
        assert actual_reset_period == reset_period, key


def test_ct_stat_rollover_clears_id_suffixed_cache_keys() -> None:
    source = COORDINATOR_PATH.read_text()

    assert "device_ct_stat_week:" in source
    assert "device_ct_stat_month:" in source
    assert "device_ct_stat_year:" in source
    assert "cache_key.startswith(cache_key_prefixes_to_clear)" in source
