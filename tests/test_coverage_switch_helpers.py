"""Tests for the shared standby mode parser."""

from custom_components.jackery_solarvault.util import standby_is_on


def teststandby_is_on() -> None:
    """Test autoStandby value conversion to boolean on/off state."""
    assert standby_is_on(1) is True
    assert standby_is_on(0) is False
    assert standby_is_on("1") is True
    assert standby_is_on("0") is False
    assert standby_is_on(True) is True
    assert standby_is_on(False) is False
    assert standby_is_on(None) is None
    assert standby_is_on("invalid") is None
