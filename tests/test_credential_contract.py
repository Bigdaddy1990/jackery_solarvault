"""Credential boundary regression tests."""

import logging

import pytest
import voluptuous as vol

from custom_components.jackery_solarvault.credentials import (
    MAX_TOKEN_LENGTH,
    credential_fingerprint,
    credential_text,
    redacted_error,
)
from custom_components.jackery_solarvault.util import redacted_json_safe_payload

SECRET = "embedded-secret-do-not-leak"


def test_error_detail_never_exposes_embedded_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Exception rendering and logs retain only a safe classification."""
    error = RuntimeError(f"broker rejected password={SECRET}")
    safe = redacted_error(error)
    logging.getLogger(__name__).warning("transport failed: %s", safe)
    assert SECRET not in safe
    assert SECRET not in caplog.text
    assert "RuntimeError" in safe


def test_credential_validation_does_not_echo_secret() -> None:
    """Central validation limits strings and never echoes rejected input."""
    secret = SECRET * (MAX_TOKEN_LENGTH + 1)
    with pytest.raises(vol.Invalid) as caught:
        credential_text(secret, field="token", max_length=MAX_TOKEN_LENGTH)
    assert secret not in str(caught.value)


def test_fingerprint_is_opaque_and_field_separated() -> None:
    """Digest cannot disclose credentials and ambiguous field sets differ."""
    first = credential_fingerprint({"username": "ab", "password": "c"})
    second = credential_fingerprint({"username": "a", "password": "bc"})
    assert first != second
    assert SECRET not in credential_fingerprint({"token": SECRET})
    assert len(first) == 64


def test_diagnostic_payload_redacts_embedded_secret() -> None:
    """Diagnostic boundary removes secret fields and embedded secret literals."""
    payload = redacted_json_safe_payload(
        {"nested": {"error": f"request failed for {SECRET}"}, "token": SECRET},
        sensitive_sources=({"token": SECRET},),
    )
    rendered = str(payload)
    assert SECRET not in rendered
