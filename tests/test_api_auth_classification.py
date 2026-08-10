"""Behavior tests: auth-failure classification is code-driven, not keyword-driven.

Live problem (F-SWEEP-3): a legitimate business error that merely contains an
auth-ish word in its message — e.g. ``async_save_contract_auth`` /
``async_cancel_contract_auth`` returning HTTP 200 with
``{"code": 10501, "msg": "Contract authorization not found for this
system"}`` — tripped the substring keyword classifier. That forced an
automatic re-login which reproduced the same business error, escalating to
``JackeryAuthError`` -> ``ConfigEntryAuthFailed`` (integration-wide reauth) or
a misleading accept-shared flow abort.

Contract under test: ``_is_auth_failure_response`` must key off the actual
backend auth-failure code (``CODE_TOKEN_EXPIRED``) or the HTTP-level 401/403
status, never off generic message-text substrings for HTTP 200 business
errors.
"""

from unittest.mock import Mock

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import (
    CODE_TOKEN_EXPIRED,
    FIELD_CODE,
    FIELD_MSG,
)

_CONTRACT_AUTH_NOT_FOUND_CODE = 10501


def _make_api() -> JackeryApi:
    """Build an API client whose transport boundary is irrelevant here."""
    return JackeryApi(Mock(), "tester@example.com", "secret")


def test_contract_auth_business_error_is_not_classified_as_auth_failure() -> None:
    """A business error whose message merely mentions 'authorization' is not auth."""
    api = _make_api()
    data = {
        FIELD_CODE: _CONTRACT_AUTH_NOT_FOUND_CODE,
        FIELD_MSG: "Contract authorization not found for this system",
    }

    assert api._is_auth_failure_response(200, data) is False  # ruff: ignore[private-member-access]


def test_token_expired_code_is_classified_as_auth_failure() -> None:
    """The real backend auth-failure code still triggers the auth-failure path."""
    api = _make_api()
    data = {FIELD_CODE: CODE_TOKEN_EXPIRED, FIELD_MSG: "token expired"}

    assert api._is_auth_failure_response(200, data) is True  # ruff: ignore[private-member-access]


def test_http_401_status_is_still_classified_as_auth_failure() -> None:
    """HTTP-level 401 remains a reliable auth-failure signal regardless of body."""
    api = _make_api()
    data = {FIELD_MSG: "Unauthorized"}

    assert api._is_auth_failure_response(401, data) is True  # ruff: ignore[private-member-access]
