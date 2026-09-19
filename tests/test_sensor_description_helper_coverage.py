# ruff: noqa: SLF001
"""Focused branch coverage for sensor description value helpers."""

from types import SimpleNamespace
from typing import Any, cast

from custom_components.jackery_solarvault.descriptions import sensor as descriptions


def _entity(**payload: Any) -> Any:
    return cast("Any", SimpleNamespace(
        payload=payload,
        merged_properties=payload.get("properties", {}),
    ))


def test_scalar_conversion_helpers_cover_invalid_and_valid_inputs() -> None:
    """Conversion helpers reject containers and preserve supported scalars."""
    assert descriptions._state_value({"bad": "state"}) is None
    assert descriptions._state_value(0) == 0
    assert descriptions._identity("value") == "value"
    assert descriptions._div(10)("25") == 2.5
    assert descriptions._div(10)("bad") is None
    assert descriptions.safe_int([]) is None
    assert descriptions.safe_int("bad") is None
    assert descriptions.safe_int("2") == 2
    assert descriptions.safe_float({}) is None
    assert descriptions.safe_float("bad") is None
    assert descriptions.safe_float("2.5") == 2.5
    assert descriptions.safe_bool(None) is None
    assert descriptions.safe_bool(True) is True
    assert descriptions.safe_bool(0) is False
    assert descriptions.safe_bool("YES") is True
    assert descriptions.safe_bool("no") is False
    assert descriptions.safe_bool([]) is None


def test_first_nonblank_and_source_helpers_cover_all_shapes() -> None:
    """Legacy scalar and source variants remain deterministic."""
    assert descriptions.first_nonblank_int(None) is None
    assert descriptions.first_nonblank_int(2.9) == 2
    assert descriptions.first_nonblank_int(" ") is None
    assert descriptions.first_nonblank_int("3.9") == 3
    assert descriptions.first_nonblank_int("bad") is None
    assert descriptions.first_nonblank_int([]) is None
    assert descriptions.property_data_sources("x", layer5_proven=True) == (
        descriptions.ALL_LIVE_DATA_SOURCES
    )
    assert descriptions.property_data_sources("x") == descriptions.HTTP_DATA_SOURCES
    assert descriptions._task_plan_value({"second": 0}, "first", "second") == 0
    assert descriptions._task_plan_value({}, "missing") is None
    assert descriptions._first_non_none(None, 0, 1) == 0
    assert descriptions._first_non_none_state(None, "ok") == "ok"


def test_storm_plan_helpers_cover_root_rows_defaults_and_fallbacks() -> None:
    """Storm warning variants preserve explicit zero and documented defaults."""
    wpc = descriptions.FIELD_WPC
    interval = descriptions.FIELD_MINS_INTERVAL
    storm = descriptions.FIELD_STORM
    enabled = descriptions.FIELD_WPS

    assert descriptions._storm_minutes_from_plan({wpc: "15"}) == 15
    assert descriptions._storm_minutes_from_plan({wpc: -1, interval: 0}) == 0
    assert descriptions._storm_minutes_from_plan({storm: ["bad", {interval: "7"}]}) == 7
    assert descriptions._storm_minutes_from_plan({storm: [{}]}) == (
        descriptions.DEFAULT_STORM_WARNING_MINUTES
    )
    assert descriptions._storm_minutes_from_plan({storm: []}) == 0
    assert descriptions._storm_minutes_from_plan({}) is None

    assert descriptions._storm_minutes_fallback({enabled: 1}, {}, {}) == (
        descriptions.DEFAULT_STORM_WARNING_MINUTES
    )
    assert descriptions._storm_minutes_fallback({enabled: 0}, {}, {}) == 0
    assert descriptions._storm_minutes_fallback({enabled: "bad"}, {}, {}) is None
    assert descriptions._storm_minutes_fallback({}, {storm: [{}]}, {}) == (
        descriptions.DEFAULT_STORM_WARNING_MINUTES
    )
    assert descriptions._storm_minutes_fallback({}, {storm: []}, {}) == 0
    assert descriptions._storm_minutes_fallback({}, {}, {}) is None


def test_payload_access_helpers_cover_missing_and_fallback_values() -> None:
    """Description accessors handle absent, malformed, and populated payloads."""
    assert descriptions._get_first_list_count(_entity(section="bad"), "section", "a") is None
    assert descriptions._get_first_list_count(
        _entity(section={"a": "bad", "b": [1, 2]}), "section", "a", "b"
    ) == 2
    assert descriptions._get_first_list_count(
        _entity(section={"a": "bad"}), "section", "a"
    ) is None

    entity = _entity(
        properties={
            "present": 0,
            "missing_value": None,
            "pv": {descriptions.FIELD_PV_PW: 12},
            "bad_pv": "bad",
        }
    )
    assert descriptions._get_prop_any(entity, "missing", "present") == 0
    assert descriptions._get_prop_any(entity, "missing_value") is None
    assert descriptions._get_prop_or_disconnected(entity, "present") == 0
    assert descriptions._get_prop_or_disconnected(entity, "missing") == "—"
    assert descriptions._get_pv_channel_power(entity, "pv") == 12
    assert descriptions._get_pv_channel_power(entity, "bad_pv") is None
