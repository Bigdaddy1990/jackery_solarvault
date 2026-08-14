"""Tests for helper functions in number.py."""

import pytest

from custom_components.jackery_solarvault.number import (
    _rounded_int,  # ruff: ignore[import-private-name]
)
from homeassistant.exceptions import HomeAssistantError


def test_rounded_int() -> None:
    """Test integer rounding and error raising in _rounded_int."""
    assert _rounded_int(12.7) == 13
    assert _rounded_int(12.2) == 12
    assert _rounded_int("42") == 42
    assert _rounded_int(10) == 10

    with pytest.raises(HomeAssistantError, match="invalid number value"):
        _rounded_int("invalid_string_val")
