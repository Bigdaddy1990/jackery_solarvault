"""Tests for the __init__.py helpers introduced or clarified for this integration.

Covers:
- _legacy_suffix_matches: anchored suffix match against the legacy device-head pattern
- _async_call_if_present: conditional async method dispatch
- _defer_coordinator_auth_failure: stores an auth failure on the coordinator
- _async_discover_with_cache_fallback: handles UpdateFailed / ConfigEntryAuthFailed
- _handle_refresh_startup_result: handles HTTP first-refresh results
- _handle_optional_startup_result: handles optional startup layer results
- _load_dotenv_if_present: reads JACKERY_* env vars from .env file

All tests use lightweight stubs so no HA fixtures are required.
"""

import os
from pathlib import Path
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from custom_components.jackery_solarvault import (
    _async_call_if_present,  # noqa: PLC2701
    _async_discover_with_cache_fallback,  # noqa: PLC2701
    _defer_coordinator_auth_failure,  # noqa: PLC2701
    _handle_optional_startup_result,  # noqa: PLC2701
    _handle_refresh_startup_result,  # noqa: PLC2701
    _legacy_suffix_matches,  # noqa: PLC2701
    _load_dotenv_if_present,  # noqa: PLC2701
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

# ---------------------------------------------------------------------------
# _legacy_suffix_matches
# ---------------------------------------------------------------------------


class TestLegacySuffixMatches:
    """Tests for _legacy_suffix_matches()."""

    # --- Positive matches: plain numeric head ---

    def test_plain_digits_head_and_suffix(self) -> None:  # noqa: PLR6301
        """Numeric head followed by _suffix must return True."""
        assert (  # noqa: S101
            _legacy_suffix_matches(
                "123456_today_battery_charge",
                "_today_battery_charge",
            )
            is True
        )

    def test_single_digit_head_and_suffix(self) -> None:  # noqa: PLR6301
        """Single digit head with suffix must return True."""
        assert _legacy_suffix_matches("1_battery_level", "_battery_level") is True  # noqa: S101

    def test_many_digit_head_and_suffix(self) -> None:  # noqa: PLR6301
        """Long numeric head with suffix must return True."""
        assert _legacy_suffix_matches("9999999999_online", "_online") is True  # noqa: S101

    # --- Positive matches: battery-pack head ---

    def test_battery_pack_head_and_suffix(self) -> None:  # noqa: PLR6301
        """Battery-pack head (<digits>_battery_pack_<digits>) with suffix must return.

        True.
        """
        assert (  # noqa: S101
            _legacy_suffix_matches("123_battery_pack_1_cell_voltage", "_cell_voltage")
            is True
        )

    def test_battery_pack_double_digit_index(self) -> None:  # noqa: PLR6301
        """Battery-pack head with two-digit index must match."""
        assert (  # noqa: S101
            _legacy_suffix_matches("123456_battery_pack_12_charge", "_charge") is True
        )

    # --- Negative matches: current non-legacy ids ---

    def test_does_not_match_current_device_prefix(self) -> None:  # noqa: PLR6301
        """A suffix embedded in a longer current key must not match."""
        # "_today_battery_charge" is a legacy suffix; current key adds "device_" in
        # between
        assert (  # noqa: S101
            _legacy_suffix_matches(
                "123456_device_today_battery_charge",
                "_today_battery_charge",
            )
            is False
        )

    def test_does_not_match_when_head_has_non_digit_prefix(self) -> None:  # noqa: PLR6301
        """A non-numeric head prefix must not match."""
        assert (  # noqa: S101
            _legacy_suffix_matches("abc_today_battery_charge", "_today_battery_charge")
            is False
        )

    def test_does_not_match_when_suffix_not_at_end(self) -> None:  # noqa: PLR6301
        """Suffix that is not at the end must not match."""
        assert _legacy_suffix_matches("123_online_extra", "_online") is False  # noqa: S101

    def test_does_not_match_when_uid_is_only_suffix(self) -> None:  # noqa: PLR6301
        """UID equal to the suffix only (no head) must not match."""
        assert (  # noqa: S101
            _legacy_suffix_matches("_today_battery_charge", "_today_battery_charge")
            is False
        )

    def test_does_not_match_when_head_has_trailing_non_digit(self) -> None:  # noqa: PLR6301
        """Head with trailing letters must not match."""
        assert _legacy_suffix_matches("123abc_online", "_online") is False  # noqa: S101

    # --- Edge cases for empty suffix ---

    def test_empty_suffix_matches_plain_numeric_uid(self) -> None:  # noqa: PLR6301
        """Empty suffix must return True if uid is a plain numeric string."""
        assert _legacy_suffix_matches("123456", "") is True  # noqa: S101

    def test_empty_suffix_matches_battery_pack_uid(self) -> None:  # noqa: PLR6301
        """Empty suffix must return True if uid is a battery-pack head."""
        assert _legacy_suffix_matches("123_battery_pack_1", "") is True  # noqa: S101

    def test_empty_suffix_does_not_match_arbitrary_string(self) -> None:  # noqa: PLR6301
        """Empty suffix with non-head uid must return False."""
        assert _legacy_suffix_matches("abc_something", "") is False  # noqa: S101

    # --- No false positives for common mismatches ---

    def test_no_match_when_legacy_suffix_appears_in_middle(self) -> None:  # noqa: PLR6301
        """Legacy suffix that appears in the middle of a current uid must not match."""
        # Current uid: <dev_id>_smart_plug_<sn>_switch_state
        # Legacy suffix: _switch_state would not be preceded by a plain digits head
        uid = "123456_smart_plug_sn001_switch_state"
        assert _legacy_suffix_matches(uid, "_switch_state") is False  # noqa: S101

    def test_regression_device_today_battery_charge(self) -> None:  # noqa: PLR6301
        """Regression: 'device_today_battery_charge' current uid must NOT match legacy.

        '_today_battery_charge'.
        """
        # This is the exact regression that caused statistics gaps at a user site
        current_uid = "123456_device_today_battery_charge"
        legacy_suffix = "_today_battery_charge"
        assert _legacy_suffix_matches(current_uid, legacy_suffix) is False  # noqa: S101

    def test_regression_legacy_today_battery_charge_does_match(self) -> None:  # noqa: PLR6301
        """Legacy uid '123456_today_battery_charge' must still be cleaned up."""
        legacy_uid = "123456_today_battery_charge"
        legacy_suffix = "_today_battery_charge"
        assert _legacy_suffix_matches(legacy_uid, legacy_suffix) is True  # noqa: S101

    def test_no_match_when_head_is_empty(self) -> None:  # noqa: PLR6301
        """Head of empty string does not match the legacy head pattern."""
        assert _legacy_suffix_matches("_online", "_online") is False  # noqa: S101


# ---------------------------------------------------------------------------
# _async_call_if_present
# ---------------------------------------------------------------------------


class TestAsyncCallIfPresent:
    """Tests for _async_call_if_present()."""

    async def test_calls_async_method_when_present(self) -> None:  # noqa: PLR6301
        """Calls and awaits the named async method when it exists on obj."""
        called: list[str] = []

        class _Obj:
            async def my_method(self) -> None:  # noqa: PLR6301
                called.append("called")

        await _async_call_if_present(_Obj(), "my_method")
        assert called == ["called"]  # noqa: S101

    async def test_calls_sync_method_when_present(self) -> None:  # noqa: PLR6301
        """Calls a non-async callable when it is present on obj."""
        called: list[str] = []

        class _Obj:
            def my_sync(self) -> None:  # noqa: PLR6301
                called.append("sync")

        await _async_call_if_present(_Obj(), "my_sync")
        assert called == ["sync"]  # noqa: S101

    async def test_does_nothing_when_attribute_absent(self) -> None:  # noqa: PLR6301
        """No error is raised when the named attribute does not exist."""
        # Should not raise
        await _async_call_if_present(object(), "nonexistent_method")

    async def test_does_nothing_when_attribute_is_not_callable(self) -> None:  # noqa: PLR6301
        """No error when the attribute is present but not callable."""

        class _Obj:
            my_attr = "not-a-method"

        # Should not raise
        await _async_call_if_present(_Obj(), "my_attr")

    async def test_does_nothing_when_attribute_is_none(self) -> None:  # noqa: PLR6301
        """No error when the attribute is explicitly None."""

        class _Obj:
            my_method = None

        await _async_call_if_present(_Obj(), "my_method")

    async def test_awaitable_result_is_awaited(self) -> None:  # noqa: PLR6301
        """Awaitable return values from the callable are properly awaited."""
        awaited: list[bool] = []

        class _Obj:
            def my_method(self) -> Any:  # noqa: ANN401, PLR6301
                async def _inner() -> None:  # noqa: RUF029
                    awaited.append(True)

                return _inner()

        await _async_call_if_present(_Obj(), "my_method")
        assert awaited == [True]  # noqa: S101

    async def test_works_with_async_mock(self) -> None:  # noqa: PLR6301
        """Works when the method is an AsyncMock."""
        obj = MagicMock()
        obj.stop = AsyncMock()
        await _async_call_if_present(obj, "stop")
        obj.stop.assert_called_once()


# ---------------------------------------------------------------------------
# _defer_coordinator_auth_failure
# ---------------------------------------------------------------------------


class TestDeferCoordinatorAuthFailure:
    """Tests for _defer_coordinator_auth_failure()."""

    def test_calls_defer_background_auth_failure(self) -> None:  # noqa: PLR6301
        """Must call coordinator._defer_background_auth_failure with the error."""
        coordinator = MagicMock()
        err = ConfigEntryAuthFailed("bad creds")

        _defer_coordinator_auth_failure(coordinator, err)

        coordinator._defer_background_auth_failure.assert_called_once_with(err)  # noqa: SLF001

    def test_sets_mqtt_auth_failure_message(self) -> None:  # noqa: PLR6301
        """Must set coordinator._mqtt_auth_failure_message to str(err)."""
        coordinator = MagicMock()
        err = ConfigEntryAuthFailed("rejected: bad token")

        _defer_coordinator_auth_failure(coordinator, err)

        # The coordinator mock captures the attribute assignment
        assert coordinator._mqtt_auth_failure_message == str(err)  # noqa: S101, SLF001

    def test_message_matches_str_of_error(self) -> None:  # noqa: PLR6301
        """The message stored on the coordinator must equal str(err)."""
        coordinator = MagicMock()
        message = "Jackery login rejected the credentials: bad token"
        err = ConfigEntryAuthFailed(message)

        _defer_coordinator_auth_failure(coordinator, err)

        assert coordinator._mqtt_auth_failure_message == message  # noqa: S101, SLF001


# ---------------------------------------------------------------------------
# _async_discover_with_cache_fallback
# ---------------------------------------------------------------------------


class TestAsyncDiscoverWithCacheFallback:
    """Tests for _async_discover_with_cache_fallback()."""

    async def test_returns_true_on_success(self) -> None:  # noqa: PLR6301
        """Returns True when discovery succeeds without exceptions."""
        coordinator = MagicMock()
        coordinator.async_discover = AsyncMock()
        result = await _async_discover_with_cache_fallback(coordinator)
        assert result is True  # noqa: S101
        coordinator.async_discover.assert_called_once()

    async def test_returns_false_on_config_entry_auth_failed(self) -> None:  # noqa: PLR6301
        """Returns False when discovery raises ConfigEntryAuthFailed."""
        coordinator = MagicMock()
        err = ConfigEntryAuthFailed("expired token")
        coordinator.async_discover = AsyncMock(side_effect=err)

        result = await _async_discover_with_cache_fallback(coordinator)

        assert result is False  # noqa: S101
        coordinator._defer_background_auth_failure.assert_called_once_with(err)  # noqa: SLF001

    async def test_defers_auth_failure_on_config_entry_auth_failed(self) -> None:  # noqa: PLR6301
        """Calls _defer_coordinator_auth_failure when ConfigEntryAuthFailed is.

        raised.
        """
        coordinator = MagicMock()
        err = ConfigEntryAuthFailed("expired token")
        coordinator.async_discover = AsyncMock(side_effect=err)

        await _async_discover_with_cache_fallback(coordinator)

        coordinator._defer_background_auth_failure.assert_called_once_with(err)  # noqa: SLF001

    async def test_returns_true_on_update_failed_with_no_cache(self) -> None:  # noqa: PLR6301
        """Returns True (but logs warning) when UpdateFailed and no cache available."""
        coordinator = MagicMock()
        coordinator.async_discover = AsyncMock(side_effect=UpdateFailed("timeout"))
        coordinator.cached_discovery_snapshot = MagicMock(return_value=None)

        result = await _async_discover_with_cache_fallback(coordinator)

        assert result is True  # noqa: S101

    async def test_uses_cache_on_update_failed(self) -> None:  # noqa: PLR6301
        """Loads cached discovery snapshot when UpdateFailed and cache available."""
        coordinator = MagicMock()
        coordinator.async_discover = AsyncMock(side_effect=UpdateFailed("cloud down"))
        cached = {"device1": {"battery": 80}}
        coordinator.cached_discovery_snapshot = MagicMock(return_value=cached)

        result = await _async_discover_with_cache_fallback(coordinator)

        assert result is True  # noqa: S101
        coordinator.async_set_updated_data.assert_called_once_with(cached)

    async def test_does_not_set_data_when_no_cache(self) -> None:  # noqa: PLR6301
        """Does NOT call async_set_updated_data when UpdateFailed and no cache."""
        coordinator = MagicMock()
        coordinator.async_discover = AsyncMock(side_effect=UpdateFailed("timeout"))
        coordinator.cached_discovery_snapshot = MagicMock(return_value=None)

        await _async_discover_with_cache_fallback(coordinator)

        coordinator.async_set_updated_data.assert_not_called()


# ---------------------------------------------------------------------------
# _handle_refresh_startup_result
# ---------------------------------------------------------------------------


class TestHandleRefreshStartupResult:
    """Tests for _handle_refresh_startup_result()."""

    def test_does_nothing_for_non_exception_result(self) -> None:  # noqa: PLR6301
        """No action is taken when result is a normal value (not an exception)."""
        coordinator = MagicMock()
        _handle_refresh_startup_result(coordinator, {"data": "ok"})
        coordinator._defer_background_auth_failure.assert_not_called()  # noqa: SLF001
        coordinator.async_set_updated_data.assert_not_called()

    def test_does_nothing_for_none_result(self) -> None:  # noqa: PLR6301
        """No action is taken when result is None."""
        coordinator = MagicMock()
        _handle_refresh_startup_result(coordinator, None)
        coordinator._defer_background_auth_failure.assert_not_called()  # noqa: SLF001

    def test_defers_auth_failure_on_config_entry_auth_failed(self) -> None:  # noqa: PLR6301
        """Calls _defer_coordinator_auth_failure when result is.

        ConfigEntryAuthFailed.
        """
        coordinator = MagicMock()
        err = ConfigEntryAuthFailed("expired")
        _handle_refresh_startup_result(coordinator, err)
        coordinator._defer_background_auth_failure.assert_called_once_with(err)  # noqa: SLF001

    def test_uses_cache_on_update_failed_with_cache(self) -> None:  # noqa: PLR6301
        """Calls async_set_updated_data when result is UpdateFailed and cache is.

        available.
        """
        coordinator = MagicMock()
        cached = {"device1": {"battery": 80}}
        coordinator.cached_discovery_snapshot = MagicMock(return_value=cached)

        err = UpdateFailed("cloud offline")
        _handle_refresh_startup_result(coordinator, err)

        coordinator.async_set_updated_data.assert_called_once_with(cached)

    def test_no_cache_call_on_update_failed_without_cache(self) -> None:  # noqa: PLR6301
        """Does not call async_set_updated_data when UpdateFailed and no cache."""
        coordinator = MagicMock()
        coordinator.cached_discovery_snapshot = MagicMock(return_value=None)

        err = UpdateFailed("cloud offline")
        _handle_refresh_startup_result(coordinator, err)

        coordinator.async_set_updated_data.assert_not_called()

    def test_logs_warning_on_generic_exception(self) -> None:  # noqa: PLR6301
        """Logs a warning when result is a generic BaseException (not UpdateFailed)."""
        coordinator = MagicMock()
        err = RuntimeError("unexpected error")
        # Should not raise; only log
        _handle_refresh_startup_result(coordinator, err)
        coordinator._defer_background_auth_failure.assert_not_called()  # noqa: SLF001
        coordinator.async_set_updated_data.assert_not_called()

    def test_auth_failure_not_treated_as_generic_exception(self) -> None:  # noqa: PLR6301
        """ConfigEntryAuthFailed must NOT also log as a generic exception warning."""
        coordinator = MagicMock()
        err = ConfigEntryAuthFailed("bad creds")
        _handle_refresh_startup_result(coordinator, err)
        # Only auth path is taken; no generic warning path
        coordinator._defer_background_auth_failure.assert_called_once()  # noqa: SLF001


# ---------------------------------------------------------------------------
# _handle_optional_startup_result
# ---------------------------------------------------------------------------


class TestHandleOptionalStartupResult:
    """Tests for _handle_optional_startup_result()."""

    def test_does_nothing_for_non_exception_result(self) -> None:  # noqa: PLR6301
        """No action when result is a normal value."""
        coordinator = MagicMock()
        _handle_optional_startup_result(coordinator, {"status": "ok"}, label="MQTT")
        coordinator._defer_background_auth_failure.assert_not_called()  # noqa: SLF001

    def test_defers_auth_failure_on_config_entry_auth_failed(self) -> None:  # noqa: PLR6301
        """Defers auth failure when result is ConfigEntryAuthFailed."""
        coordinator = MagicMock()
        err = ConfigEntryAuthFailed("expired token")
        _handle_optional_startup_result(coordinator, err, label="BLE")
        coordinator._defer_background_auth_failure.assert_called_once_with(err)  # noqa: SLF001

    def test_logs_warning_on_generic_base_exception(self) -> None:  # noqa: PLR6301
        """Logs a warning (not raises) when result is a generic BaseException."""
        coordinator = MagicMock()
        err = RuntimeError("broker down")
        # Should NOT raise
        _handle_optional_startup_result(coordinator, err, label="local MQTT")
        coordinator._defer_background_auth_failure.assert_not_called()  # noqa: SLF001

    def test_does_nothing_for_none_result(self) -> None:  # noqa: PLR6301
        """No action when result is None."""
        coordinator = MagicMock()
        _handle_optional_startup_result(coordinator, None, label="BLE")
        coordinator._defer_background_auth_failure.assert_not_called()  # noqa: SLF001

    def test_does_nothing_for_false_result(self) -> None:  # noqa: PLR6301
        """No action when result is False (falsy non-exception)."""
        coordinator = MagicMock()
        _handle_optional_startup_result(coordinator, False, label="MQTT")
        coordinator._defer_background_auth_failure.assert_not_called()  # noqa: SLF001

    def test_label_does_not_affect_auth_failure_path(self) -> None:  # noqa: PLR6301
        """The label parameter is for logging only and does not change logic."""
        coordinator = MagicMock()
        err = ConfigEntryAuthFailed("bad creds")
        _handle_optional_startup_result(coordinator, err, label="any label")
        coordinator._defer_background_auth_failure.assert_called_once_with(err)  # noqa: SLF001


# ---------------------------------------------------------------------------
# _load_dotenv_if_present
# ---------------------------------------------------------------------------


class TestLoadDotenvIfPresent:
    """Tests for _load_dotenv_if_present()."""

    async def test_does_nothing_when_env_file_absent(self) -> None:  # noqa: PLR6301
        """No error when .env file does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Clear env and call the function
            key = "JACKERY_TEST_ABSENT_KEY_XYZ"
            os.environ.pop(key, None)
            await _load_dotenv_if_present(Path(tmpdir))
            assert os.environ.get(key) is None  # noqa: S101

    async def test_loads_jackery_env_var_from_env_file(self) -> None:  # noqa: PLR6301
        """JACKERY_* keys from .env must be added to os.environ."""
        key = "JACKERY_TEST_KEY_FROM_ENV_FILE"
        os.environ.pop(key, None)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                env_file = Path(tmpdir) / ".env"
                env_file.write_text(f"{key}=test_value_42\n", encoding="utf-8")
                await _load_dotenv_if_present(Path(tmpdir))
            assert os.environ.get(key) == "test_value_42"  # noqa: S101
        finally:
            os.environ.pop(key, None)

    async def test_skips_non_jackery_keys(self) -> None:  # noqa: PLR6301
        """Keys that do not start with JACKERY_ must not be added to os.environ."""
        key = "MY_OTHER_KEY"
        os.environ.pop(key, None)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                env_file = Path(tmpdir) / ".env"
                env_file.write_text(f"{key}=should_be_ignored\n", encoding="utf-8")
                await _load_dotenv_if_present(Path(tmpdir))
            assert os.environ.get(key) is None  # noqa: S101
        finally:
            os.environ.pop(key, None)

    async def test_skips_comment_lines(self) -> None:  # noqa: PLR6301
        """Lines starting with # must be ignored."""
        key = "JACKERY_COMMENTED_OUT"
        os.environ.pop(key, None)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                env_file = Path(tmpdir) / ".env"
                env_file.write_text(f"# {key}=not_set\n", encoding="utf-8")
                await _load_dotenv_if_present(Path(tmpdir))
            assert os.environ.get(key) is None  # noqa: S101
        finally:
            os.environ.pop(key, None)

    async def test_skips_lines_without_equals(self) -> None:  # noqa: PLR6301
        """Lines without '=' are skipped without error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("JACKERY_NO_EQUALS_HERE\n", encoding="utf-8")
            # Should not raise
            await _load_dotenv_if_present(Path(tmpdir))

    async def test_strips_quotes_from_values(self) -> None:  # noqa: PLR6301
        """Quoted values (single and double) must be stripped."""
        key_single = "JACKERY_QUOTED_SINGLE"
        key_double = "JACKERY_QUOTED_DOUBLE"
        for k in (key_single, key_double):
            os.environ.pop(k, None)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                env_file = Path(tmpdir) / ".env"
                env_file.write_text(
                    f"{key_single}='single_quoted'\n{key_double}=\"double_quoted\"\n",
                    encoding="utf-8",
                )
                await _load_dotenv_if_present(Path(tmpdir))
            assert os.environ.get(key_single) == "single_quoted"  # noqa: S101
            assert os.environ.get(key_double) == "double_quoted"  # noqa: S101
        finally:
            for k in (key_single, key_double):
                os.environ.pop(k, None)

    async def test_does_not_overwrite_existing_env_var(self) -> None:  # noqa: PLR6301
        """Setdefault semantics: existing env vars must not be overwritten."""
        key = "JACKERY_EXISTING_KEY_PERSIST"
        os.environ[key] = "original_value"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                env_file = Path(tmpdir) / ".env"
                env_file.write_text(f"{key}=new_value\n", encoding="utf-8")
                await _load_dotenv_if_present(Path(tmpdir))
            assert os.environ.get(key) == "original_value"  # noqa: S101
        finally:
            os.environ.pop(key, None)

    async def test_skips_empty_lines(self) -> None:  # noqa: PLR6301
        """Empty lines must be skipped without error."""
        key = "JACKERY_AFTER_BLANK"
        os.environ.pop(key, None)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                env_file = Path(tmpdir) / ".env"
                env_file.write_text(f"\n\n{key}=after_blank\n\n", encoding="utf-8")
                await _load_dotenv_if_present(Path(tmpdir))
            assert os.environ.get(key) == "after_blank"  # noqa: S101
        finally:
            os.environ.pop(key, None)

    async def test_multiple_keys_loaded(self) -> None:  # noqa: PLR6301
        """Multiple JACKERY_* keys in the file must all be loaded."""
        keys = ["JACKERY_MULTI_A", "JACKERY_MULTI_B", "JACKERY_MULTI_C"]
        for k in keys:
            os.environ.pop(k, None)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                env_file = Path(tmpdir) / ".env"
                content = "\n".join(f"{k}=value_{i}" for i, k in enumerate(keys))
                env_file.write_text(content + "\n", encoding="utf-8")
                await _load_dotenv_if_present(Path(tmpdir))
            for i, k in enumerate(keys):
                assert os.environ.get(k) == f"value_{i}", f"Key {k} not loaded"  # noqa: S101
        finally:
            for k in keys:
                os.environ.pop(k, None)
