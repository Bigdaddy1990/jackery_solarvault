"""Unit tests for app_energy_unit_scale function."""


from custom_components.jackery_solarvault.const import APP_STAT_UNIT
from custom_components.jackery_solarvault.util import app_energy_unit_scale


def test_app_energy_unit_scale_missing_unit() -> None:
    """Missing unit field should default to kWh (1.0)."""
    source = {}
    assert app_energy_unit_scale(source) == 1.0


def test_app_energy_unit_scale_empty_string() -> None:
    """Empty string unit should default to kWh (1.0)."""
    source = {APP_STAT_UNIT: ""}
    assert app_energy_unit_scale(source) == 1.0


def test_app_energy_unit_scale_kwh() -> None:
    """Explicit kWh should return 1.0."""
    source = {APP_STAT_UNIT: "kWh"}
    assert app_energy_unit_scale(source) == 1.0


def test_app_energy_unit_scale_kwh_case_insensitive() -> None:
    """KWh should be case-insensitive."""
    source = {APP_STAT_UNIT: "KWH"}
    assert app_energy_unit_scale(source) == 1.0


def test_app_energy_unit_scale_wh() -> None:
    """Wh should return 0.001 (1/1000)."""
    source = {APP_STAT_UNIT: "Wh"}
    assert app_energy_unit_scale(source) == 0.001


def test_app_energy_unit_scale_wh_case_insensitive() -> None:
    """Wh should be case-insensitive."""
    source = {APP_STAT_UNIT: "WH"}
    assert app_energy_unit_scale(source) == 0.001


def test_app_energy_unit_scale_unknown_unit() -> None:
    """Unknown unit should default to kWh (1.0) and log debug."""
    source = {APP_STAT_UNIT: "Joules"}
    assert app_energy_unit_scale(source) == 1.0


def test_app_energy_unit_scale_none_value() -> None:
    """None unit value should default to kWh."""
    source = {APP_STAT_UNIT: None}
    assert app_energy_unit_scale(source) == 1.0


def test_app_energy_unit_scale_whitespace() -> None:
    """Whitespace-only unit should default to kWh."""
    source = {APP_STAT_UNIT: "   "}
    assert app_energy_unit_scale(source) == 1.0
