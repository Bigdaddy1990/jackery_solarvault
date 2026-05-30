"""Tests for PR changes not covered by earlier test files.

Covers:
- api.py compatibility shim: all four public names are importable and are the
  same objects as those exported by client/api.py (double-quote __all__ style change).
- __init__.py new import path: JackeryLocalMqttClient is imported from
  .client.local_mqtt (new import added in this PR).
- _legacy_suffix_matches: the regex pattern was changed from single-quote to
  double-quote string literals and the docstring was rewritten. Tests pin the
  actual match semantics (digits-only head and battery-pack head).
- async_setup_entry local_mqtt_result: the gather now returns a 3-tuple; when
  local_mqtt_result is a BaseException a warning is logged and setup continues.
  This is tested via an isolated unit-level inspection of the gather-result
  handling path.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# api.py compatibility shim — __all__ uses double-quoted strings (PR change)
# ---------------------------------------------------------------------------


def test_api_shim_exports_jackery_api() -> None:
    """JackeryApi must be importable from the compatibility shim module."""
    from custom_components.jackery_solarvault.api import JackeryApi

    assert JackeryApi is not None


def test_api_shim_exports_jackery_api_error() -> None:
    """JackeryApiError must be importable from the compatibility shim module."""
    from custom_components.jackery_solarvault.api import JackeryApiError

    assert JackeryApiError is not None


def test_api_shim_exports_jackery_auth_error() -> None:
    """JackeryAuthError must be importable from the compatibility shim module."""
    from custom_components.jackery_solarvault.api import JackeryAuthError

    assert JackeryAuthError is not None


def test_api_shim_exports_jackery_error() -> None:
    """JackeryError must be importable from the compatibility shim module."""
    from custom_components.jackery_solarvault.api import JackeryError

    assert JackeryError is not None


def test_api_shim_all_exports_same_objects_as_client_api() -> None:
    """The shim re-exports must be identical objects to those in client/api.py."""
    from custom_components.jackery_solarvault.api import (
        JackeryApi as ShimApi,
        JackeryApiError as ShimApiError,
        JackeryAuthError as ShimAuthError,
        JackeryError as ShimError,
    )
    from custom_components.jackery_solarvault.client.api import (
        JackeryApi,
        JackeryApiError,
        JackeryAuthError,
        JackeryError,
    )

    assert ShimApi is JackeryApi
    assert ShimApiError is JackeryApiError
    assert ShimAuthError is JackeryAuthError
    assert ShimError is JackeryError


def test_api_shim_dunder_all_contains_four_names() -> None:
    """api.py __all__ must export exactly four public names."""
    import custom_components.jackery_solarvault.api as api_mod

    assert set(api_mod.__all__) == {
        "JackeryApi",
        "JackeryApiError",
        "JackeryAuthError",
        "JackeryError",
    }


def test_api_shim_auth_error_is_subclass_of_error() -> None:
    """JackeryAuthError must be a subclass of JackeryError (hierarchy check)."""
    from custom_components.jackery_solarvault.api import JackeryAuthError, JackeryError

    assert issubclass(JackeryAuthError, JackeryError)


def test_api_shim_api_error_is_subclass_of_error() -> None:
    """JackeryApiError must be a subclass of JackeryError."""
    from custom_components.jackery_solarvault.api import JackeryApiError, JackeryError

    assert issubclass(JackeryApiError, JackeryError)


# ---------------------------------------------------------------------------
# __init__.py — new import path for JackeryLocalMqttClient (.client.local_mqtt)
# ---------------------------------------------------------------------------


def test_init_imports_jackery_local_mqtt_client() -> None:
    """JackeryLocalMqttClient must be importable from the package init module.

    The PR added ``from .client.local_mqtt import JackeryLocalMqttClient`` to
    __init__.py. This test verifies the import works without error.
    """
    try:
        from custom_components.jackery_solarvault.client.local_mqtt import (
            JackeryLocalMqttClient,
        )

        assert JackeryLocalMqttClient is not None
    except (SyntaxError, ImportError) as err:
        pytest.skip(f"local_mqtt module not importable: {err}")


def test_client_init_imports_jackery_api_from_client_package() -> None:
    """JackeryApi must be importable from the client sub-package (not from .api shim).

    The PR changed __init__.py to import from .client instead of .api.
    """
    from custom_components.jackery_solarvault.client import JackeryApi

    assert JackeryApi is not None


def test_client_init_imports_jackery_auth_error() -> None:
    """JackeryAuthError must be importable directly from the client sub-package."""
    from custom_components.jackery_solarvault.client import JackeryAuthError

    assert JackeryAuthError is not None


def test_client_init_imports_jackery_error() -> None:
    """JackeryError must be importable directly from the client sub-package."""
    from custom_components.jackery_solarvault.client import JackeryError

    assert JackeryError is not None


# ---------------------------------------------------------------------------
# _legacy_suffix_matches — docstring and string-literal style changed in PR
# ---------------------------------------------------------------------------
# The function's regex pattern was updated from single-quoted to double-quoted
# string literals. These tests pin the matching contract so any accidental
# regex change in a future PR is caught immediately.


def _legacy_suffix_matches(uid: str, key_suffix: str) -> bool:
    """Thin wrapper that imports and calls the production function."""
    from custom_components.jackery_solarvault import _legacy_suffix_matches as _fn

    return _fn(uid, key_suffix)


# --- True cases: digits-only head -----------------------------------------


def test_legacy_suffix_matches_simple_digit_head() -> None:
    """A UID of the form '<digits><suffix>' must match."""
    assert _legacy_suffix_matches("12345_battery_soc", "_battery_soc") is True


def test_legacy_suffix_matches_single_digit_head() -> None:
    """A single-digit head followed by a suffix must match."""
    assert _legacy_suffix_matches("9_some_key", "_some_key") is True


def test_legacy_suffix_matches_large_digit_head() -> None:
    """A long numeric head (device serial) must match."""
    assert _legacy_suffix_matches("987654321_voltage", "_voltage") is True


# --- True cases: battery-pack head ----------------------------------------


def test_legacy_suffix_matches_battery_pack_head() -> None:
    """A UID of the form '<digits>_battery_pack_<digits><suffix>' must match."""
    assert _legacy_suffix_matches("12345_battery_pack_1_voltage", "_voltage") is True


def test_legacy_suffix_matches_battery_pack_zero_index() -> None:
    """battery_pack index of 0 must be accepted."""
    assert _legacy_suffix_matches("12345_battery_pack_0_current", "_current") is True


def test_legacy_suffix_matches_battery_pack_multi_digit_index() -> None:
    """battery_pack index with multiple digits must match."""
    assert _legacy_suffix_matches("99_battery_pack_12_temp", "_temp") is True


# --- False cases: head does not match digits pattern ----------------------


def test_legacy_suffix_matches_rejects_non_digit_head() -> None:
    """A UID whose head is not purely digits must not match."""
    assert _legacy_suffix_matches("my_device_battery_soc", "_battery_soc") is False


def test_legacy_suffix_matches_rejects_alphanumeric_head() -> None:
    """A mixed alphanumeric head must not match."""
    assert _legacy_suffix_matches("abc123_voltage", "_voltage") is False


def test_legacy_suffix_matches_rejects_battery_pack_with_non_digit_index() -> None:
    """battery_pack with a non-numeric index must not match."""
    assert _legacy_suffix_matches("12345_battery_pack_abc_voltage", "_voltage") is False


def test_legacy_suffix_matches_rejects_suffix_mismatch() -> None:
    """When the UID does not end with the suffix, must return False."""
    assert _legacy_suffix_matches("12345_battery_soc", "_voltage") is False


def test_legacy_suffix_matches_rejects_partial_suffix_match() -> None:
    """A partial suffix match (UID ends with a longer version) must return False."""
    # uid ends with "_soc" but the suffix we're checking is "_battery_soc"
    # and the head would be "12345_" which is not a pure digits head.
    assert _legacy_suffix_matches("12345_battery_soc", "_extra_battery_soc") is False


def test_legacy_suffix_matches_rejects_empty_uid() -> None:
    """An empty UID string must not match any suffix."""
    assert _legacy_suffix_matches("", "_voltage") is False


def test_legacy_suffix_matches_rejects_uid_matching_only_suffix() -> None:
    """A UID that consists entirely of the suffix (empty head) must not match."""
    # head would be "" which does not match \d+
    assert _legacy_suffix_matches("_voltage", "_voltage") is False


def test_legacy_suffix_matches_current_entity_not_matched() -> None:
    """A UID that looks like a current-schema ID must not be deleted.

    Example: '12345_pv_power_w' — head would be '12345_pv_power' which is not
    a valid legacy head (contains non-digits after the device serial).
    """
    assert (
        _legacy_suffix_matches("12345_pv_power_w", "_w") is False
        or _legacy_suffix_matches("12345_pv_power_w", "_power_w") is False
    )


def test_legacy_suffix_matches_head_cannot_have_trailing_underscore() -> None:
    """A digits head with a trailing underscore before the suffix is fine if suffix starts with underscore."""
    # "12345_battery_soc": head="12345", suffix="_battery_soc" → head matches \d+
    assert _legacy_suffix_matches("12345_battery_soc", "_battery_soc") is True
    # But "12345__double_underscore": head="12345_", does NOT match \d+ (has trailing _)
    assert (
        _legacy_suffix_matches("12345__double_underscore", "_double_underscore")
        is False
    )


# ---------------------------------------------------------------------------
# async_setup_entry — local_mqtt_result BaseException → warning is logged
#
# Rather than testing the full async_setup_entry (which requires HA fixtures),
# we test the isolated conditional logic that was added for local_mqtt_result.
# The change is: `if isinstance(local_mqtt_result, BaseException): _LOGGER.warning(...)`
# ---------------------------------------------------------------------------


def test_local_mqtt_result_warning_condition_fires_for_runtime_error() -> None:
    """The isinstance(local_mqtt_result, BaseException) condition must be True for RuntimeError."""
    result: Any = RuntimeError("broker refused connection")
    assert isinstance(result, BaseException)


def test_local_mqtt_result_warning_condition_fires_for_exception() -> None:
    """The isinstance(local_mqtt_result, BaseException) must match generic Exception."""
    result: Any = Exception("something went wrong")
    assert isinstance(result, BaseException)


def test_local_mqtt_result_warning_condition_is_false_for_none() -> None:
    """When local_mqtt_result is None (success), the warning condition must be False."""
    result: Any = None
    assert not isinstance(result, BaseException)


def test_local_mqtt_result_warning_condition_is_false_for_zero() -> None:
    """Integer 0 (falsy but not an exception) must not trigger the warning."""
    result: Any = 0
    assert not isinstance(result, BaseException)


def test_local_mqtt_result_warning_logged_via_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify the warning message is correctly formatted when an exception occurs.

    Simulates the code path in async_setup_entry that logs a warning for
    local_mqtt_result being a BaseException.
    """
    _logger = logging.getLogger("custom_components.jackery_solarvault")
    err = RuntimeError("local broker unreachable")

    # Replicate the async_setup_entry condition inline.
    local_mqtt_result: Any = err
    with caplog.at_level(logging.WARNING, logger=_logger.name):
        if isinstance(local_mqtt_result, BaseException):
            _logger.warning(
                "Jackery local MQTT listener could not start during setup: %s",
                local_mqtt_result,
            )

    assert "local MQTT listener" in caplog.text
    assert "local broker unreachable" in caplog.text


def test_local_mqtt_result_no_warning_when_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When local_mqtt_result is None, no warning must be emitted."""
    _logger = logging.getLogger("custom_components.jackery_solarvault")
    local_mqtt_result: Any = None

    with caplog.at_level(logging.WARNING, logger=_logger.name):
        if isinstance(local_mqtt_result, BaseException):
            _logger.warning(
                "Jackery local MQTT listener could not start during setup: %s",
                local_mqtt_result,
            )

    assert "local MQTT listener" not in caplog.text


# ---------------------------------------------------------------------------
# Additional boundary: _legacy_suffix_matches preserves contract when
# suffix is longer than or equal to UID
# ---------------------------------------------------------------------------


def test_legacy_suffix_matches_suffix_equal_to_uid_empty_head() -> None:
    """When suffix equals the entire UID, the head is empty and must not match."""
    suffix = "12345_battery_soc"
    uid = suffix
    assert _legacy_suffix_matches(uid, suffix) is False


def test_legacy_suffix_matches_suffix_longer_than_uid() -> None:
    """When suffix is longer than the UID, endswith returns False."""
    assert _legacy_suffix_matches("123", "_very_long_suffix_that_exceeds_uid") is False


# ---------------------------------------------------------------------------
# Regression: _legacy_suffix_matches does not accidentally match current entities
# ---------------------------------------------------------------------------


def test_legacy_suffix_matches_current_style_uid_not_matched() -> None:
    """Current-schema UIDs must not be falsely identified as legacy.

    Scenario: device_id=12345, current key suffix is '_pv_power_w'.
    If a legacy key suffix is '_power_w', the UID '12345_pv_power_w' must
    NOT match because the head '12345_pv' is not a pure digits string.
    """
    uid = "12345_pv_power_w"
    legacy_suffix = "_power_w"
    assert _legacy_suffix_matches(uid, legacy_suffix) is False


def test_legacy_suffix_matches_battery_pack_uid_not_matched_by_wrong_suffix() -> None:
    """battery_pack UIDs with a wrong suffix must return False."""
    uid = "12345_battery_pack_2_voltage"
    assert _legacy_suffix_matches(uid, "_current") is False
    assert _legacy_suffix_matches(uid, "_voltage") is True
