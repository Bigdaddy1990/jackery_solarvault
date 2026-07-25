"""Tests for helper functions in text.py."""

from custom_components.jackery_solarvault.text import (
    _has_home_payload_evidence,
    _is_portable_payload,
    _payload_has_home_payload_evidence,
)


def test_has_home_payload_evidence() -> None:
    """Test evidence detection in text module."""
    assert _has_home_payload_evidence({"batSoc": 50}) is True
    assert _has_home_payload_evidence({"foo": "bar"}) is False


def test_payload_has_home_payload_evidence() -> None:
    """Test full payload evidence detection in text module."""
    assert _payload_has_home_payload_evidence({}, props={"batSoc": 50}) is True
    assert _payload_has_home_payload_evidence({"system": {"id": 10}}) is True
    assert _payload_has_home_payload_evidence({"properties": {"swEps": 1}}) is True
    assert _payload_has_home_payload_evidence({}) is False


def test_is_portable_payload() -> None:
    """Test legacy bind detection in text module."""
    assert _is_portable_payload({"system": {"id": 10}}) is False
    assert (
        _is_portable_payload({"discovery": {"discovery_source": "legacy_bind_list"}})
        is True
    )
    assert _is_portable_payload({}) is False
