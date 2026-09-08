"""Tests for helper functions in button.py."""

from custom_components.jackery_solarvault.button import (
    _has_home_payload_evidence,  # ruff: ignore[import-private-name]
    _is_portable_payload,  # ruff: ignore[import-private-name]
    _payload_has_home_payload_evidence,  # ruff: ignore[import-private-name]
)


def test_has_home_payload_evidence() -> None:
    """Test detection of Home/System-body-only fields in properties dict."""
    assert _has_home_payload_evidence({"batSoc": 80}) is True
    assert _has_home_payload_evidence({"gridInPw": 500}) is True
    assert _has_home_payload_evidence({"unrelatedKey": 123}) is False
    assert _has_home_payload_evidence({}) is False


def test_payload_has_home_payload_evidence() -> None:
    """Test detection of Home evidence in full payload objects."""
    # 1. Evidence in passed props dict
    assert _payload_has_home_payload_evidence({}, props={"batSoc": 90}) is True

    # 2. Evidence in system dict
    assert _payload_has_home_payload_evidence({"system": {"id": 1}}) is True

    # 3. Evidence in raw properties dict
    assert _payload_has_home_payload_evidence({"properties": {"swEps": 1}}) is True

    # 4. No evidence
    assert _payload_has_home_payload_evidence({"properties": {"foo": "bar"}}) is False


def test_is_portable_payload() -> None:
    """Test detection of Explorer/Portable legacy bind payloads."""
    # Has home evidence -> Not portable
    assert _is_portable_payload({"system": {"id": 1}}) is False

    # Legacy bind discovery -> Portable
    portable_payload = {"discovery": {"discovery_source": "legacy_bind_list"}}
    assert _is_portable_payload(portable_payload) is True

    # Unrelated -> Not portable
    assert _is_portable_payload({"discovery": {}}) is False
