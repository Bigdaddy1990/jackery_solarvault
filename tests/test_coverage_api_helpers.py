"""Tests for Jackery API client internal logging helpers and shape formatters."""

from custom_components.jackery_solarvault.client.api import _log_body, _log_value_shape


def test_log_value_shape() -> None:
    """Test value shape formatting for dicts, lists, and primitives."""
    assert _log_value_shape({"a": 1, "b": 2}) == "dict[2]"
    assert _log_value_shape([1, 2, 3]) == "list[3]"
    assert _log_value_shape("hello") == "str"
    assert _log_value_shape(42) == "int"
    assert _log_value_shape(None) == "NoneType"


def test_log_body() -> None:
    """Test body logging shape formatters."""
    d = {"code": 0, "msg": "ok", "data": {"items": [1, 2]}}
    log_str = _log_body(d)
    assert "code=int" in log_str
    assert "data=dict[1]" in log_str
    assert "msg=str" in log_str

    lst = [1, 2, 3]
    assert _log_body(lst) == "list[3]"
    assert _log_body(100) == "int"
