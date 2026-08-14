"""Tests for helper functions in switch.py."""

from custom_components.jackery_solarvault.switch import (
    _standby_is_on,  # ruff: ignore[import-private-name]
)


def test_standby_is_on() -> None:
    """Test autoStandby value conversion to boolean on/off state."""
    assert _standby_is_on(1) is True
    assert _standby_is_on(0) is False
    assert _standby_is_on("1") is True
    assert _standby_is_on("0") is False
    assert _standby_is_on(True) is True
    assert _standby_is_on(False) is False
    assert _standby_is_on(None) is None
    assert _standby_is_on("invalid") is None
