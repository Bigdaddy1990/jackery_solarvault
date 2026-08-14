"""Focused regression tests for shared entity conversion helpers."""

from custom_components.jackery_solarvault.util import (
    is_day_period_payload,
    safe_bool,
    safe_float,
    safe_int,
)


def test_safe_helpers_edge_cases() -> None:
    """Test safe conversion helper functions."""
    assert safe_float(None) is None
    assert safe_float("invalid") is None
    assert safe_float(12.34) == 12.34  # ruff: ignore[float-equality-comparison]

    assert safe_int(None) is None
    assert safe_int("invalid") is None
    assert safe_int(100) == 100

    assert safe_bool(None) is None
    assert safe_bool("true") is True
    assert safe_bool("false") is False
    assert safe_bool(1) is True
    assert safe_bool(0) is False


def test_is_day_period_payload() -> None:
    """Test day period payload detection helper."""
    assert is_day_period_payload({"dateType": "day"}, "sys_pv_day") is True
    assert is_day_period_payload({"dateType": "month"}, "sys_pv_month") is False
    assert is_day_period_payload({}, "sys_pv_day") is True
