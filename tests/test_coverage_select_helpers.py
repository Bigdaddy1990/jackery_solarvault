"""Tests for helper functions and mappings in select.py."""

from custom_components.jackery_solarvault.select import (
    _CT_PHASE_TO_OPTION,  # ruff: ignore[import-private-name]
    _HOURS_TO_AUTO_OFF_OPTION,  # ruff: ignore[import-private-name]
    _OPTION_TO_CT_PHASE,  # ruff: ignore[import-private-name]
    _storm_minutes_value,  # ruff: ignore[import-private-name]
)


def test_ct_phase_mappings() -> None:
    """Test CT phase enum to option mapping."""
    assert _CT_PHASE_TO_OPTION[1] == "phase_1"
    assert _CT_PHASE_TO_OPTION[4] == "combined_phases"
    assert _OPTION_TO_CT_PHASE["phase_1"] == 1
    assert _OPTION_TO_CT_PHASE["phase_4"] == 4  # ruff: ignore[magic-value-comparison]


def test_auto_off_hours_mappings() -> None:
    """Test auto off hours options mapping."""
    assert len(_HOURS_TO_AUTO_OFF_OPTION) > 0


def test_storm_minutes_value() -> None:
    """Test lead time extraction in storm_minutes_value."""
    # 1. Found in properties (must be >= STORM_MINUTES_MIN_VALID)
    assert _storm_minutes_value({"wpc": 60}, {}, {}) == 60  # ruff: ignore[magic-value-comparison]

    # 2. Found in weather_plan
    assert _storm_minutes_value({}, {"minsInterval": 120}, {}) == 120  # ruff: ignore[magic-value-comparison]

    # 3. Found in task_plan
    assert _storm_minutes_value({}, {}, {"wpc": 180}) == 180  # ruff: ignore[magic-value-comparison]

    # 4. Found in weather_plan list storm items
    weather_plan_list = {"storm": [{"minsInterval": 240}]}
    assert _storm_minutes_value({}, weather_plan_list, {}) == 240  # ruff: ignore[magic-value-comparison]

    # 5. Invalid / sentinel values (< STORM_MINUTES_MIN_VALID)
    assert _storm_minutes_value({"wpc": 1}, {}, {}) is None

    # 6. Not found
    assert _storm_minutes_value({}, {}, {}) is None
