"""Targeted tests for uncovered lines in coordinator.py to achieve 100% coverage."""

from datetime import date
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

from custom_components.jackery_solarvault.const import (
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
)
from custom_components.jackery_solarvault.coordinator import (
    MqttConnectionManager,
    _backfill_period_is_closed,  # noqa: PLC2701, RUF105
    _load_mqtt_push_client,  # noqa: PLC2701, RUF105
    mqtt_connect_failure_signature,
)


def test_load_mqtt_push_client_imports_correctly() -> None:
    """_load_mqtt_push_client returns the JackeryMqttPushClient class."""
    # This tests lines 834-835 which were uncovered
    client_class = _load_mqtt_push_client()
    assert client_class is not None
    assert client_class.__name__ == "JackeryMqttPushClient"


class TestBackfillPeriodIsClosed:
    """Test _backfill_period_is_closed edge cases for all date types."""

    def test_day_type_closed_when_yesterday(self) -> None:  # noqa: PLR6301, RUF105
        """DAY bucket is closed when period_end < today."""
        today = date(2026, 8, 16)
        yesterday = date(2026, 8, 15)
        assert _backfill_period_is_closed(DATE_TYPE_DAY, yesterday, today=today) is True

    def test_day_type_open_when_today(self) -> None:  # noqa: PLR6301, RUF105
        """DAY bucket is open when period_end == today."""
        today = date(2026, 8, 16)
        assert _backfill_period_is_closed(DATE_TYPE_DAY, today, today=today) is False

    def test_week_type_closed_when_last_week(self) -> None:  # noqa: PLR6301, RUF105
        """WEEK bucket is closed when week has fully elapsed."""
        today = date(2026, 8, 16)  # Sunday
        last_week_start = date(2026, 8, 3)  # Previous Monday
        assert (
            _backfill_period_is_closed(DATE_TYPE_WEEK, last_week_start, today=today)
            is True
        )

    def test_week_type_open_when_current_week(self) -> None:  # noqa: PLR6301, RUF105
        """WEEK bucket is open during current week."""
        today = date(2026, 8, 16)
        this_week_start = date(2026, 8, 10)  # Current Monday
        assert (
            _backfill_period_is_closed(DATE_TYPE_WEEK, this_week_start, today=today)
            is False
        )

    def test_month_type_december_boundary(self) -> None:  # noqa: PLR6301, RUF105
        """MONTH bucket handles December -> January year rollover correctly (line 864)."""  # noqa: RUF105
        today = date(2026, 1, 15)
        dec_start = date(2025, 12, 1)
        # December period ends Dec 31, which is < Jan 15
        assert (
            _backfill_period_is_closed(DATE_TYPE_MONTH, dec_start, today=today) is True
        )

    def test_month_type_january_open(self) -> None:  # noqa: PLR6301, RUF105
        """MONTH bucket for January is open in mid-January."""
        today = date(2026, 1, 15)
        jan_start = date(2026, 1, 1)
        assert (
            _backfill_period_is_closed(DATE_TYPE_MONTH, jan_start, today=today) is False
        )

    def test_month_type_regular_month_closed(self) -> None:  # noqa: PLR6301, RUF105
        """Regular month boundary (not December) works correctly."""
        today = date(2026, 8, 16)
        july_start = date(2026, 7, 1)
        assert (
            _backfill_period_is_closed(DATE_TYPE_MONTH, july_start, today=today) is True
        )

    def test_month_type_current_month_open(self) -> None:  # noqa: PLR6301, RUF105
        """Current month is open."""
        today = date(2026, 8, 16)
        aug_start = date(2026, 8, 1)
        assert (
            _backfill_period_is_closed(DATE_TYPE_MONTH, aug_start, today=today) is False
        )

    def test_year_type_closed_when_last_year(self) -> None:  # noqa: PLR6301, RUF105
        """YEAR bucket is closed for previous year."""
        today = date(2026, 8, 16)
        last_year_start = date(2025, 1, 1)
        assert (
            _backfill_period_is_closed(DATE_TYPE_YEAR, last_year_start, today=today)
            is True
        )

    def test_year_type_open_when_current_year(self) -> None:  # noqa: PLR6301, RUF105
        """YEAR bucket is open for current year."""
        today = date(2026, 8, 16)
        this_year_start = date(2026, 1, 1)
        assert (
            _backfill_period_is_closed(DATE_TYPE_YEAR, this_year_start, today=today)
            is False
        )

    def test_unknown_date_type_returns_false(self) -> None:  # noqa: PLR6301, RUF105
        """Unknown date_type returns False (line 875)."""
        today = date(2026, 8, 16)
        assert _backfill_period_is_closed("UNKNOWN", today, today=today) is False


class TestMqttConnectFailureSignature:
    """Test mqtt_connect_failure_signature edge cases."""

    def test_mqtt_not_connected_yet_prefix(self) -> None:  # noqa: PLR6301, RUF105
        """Messages starting with 'MQTT not connected yet' return first 160 chars (line 1108)."""  # noqa: RUF105
        msg = "MQTT not connected yet - waiting for broker"
        result = mqtt_connect_failure_signature(msg)
        assert result == msg[:160]
        assert result.startswith("MQTT not connected yet")

    def test_mqtt_not_connected_yet_long_message_truncated(self) -> None:  # noqa: PLR6301, RUF105
        """Long 'MQTT not connected yet' messages are truncated to 160 chars."""
        msg = "MQTT not connected yet - " + "x" * 200
        result = mqtt_connect_failure_signature(msg)
        assert len(result) == 160
        assert result.startswith("MQTT not connected yet")

    def test_empty_message_returns_unknown(self) -> None:  # noqa: PLR6301, RUF105
        """Empty or falsy messages return 'unknown'."""
        assert mqtt_connect_failure_signature("") == "unknown"
        assert mqtt_connect_failure_signature(None) == "unknown"
        assert mqtt_connect_failure_signature("   ") == "unknown"

    def test_generic_message_truncated_to_160(self) -> None:  # noqa: PLR6301, RUF105
        """Generic messages are truncated to 160 chars (line 1109)."""
        msg = "Some generic error message " + "x" * 200
        result = mqtt_connect_failure_signature(msg)
        assert len(result) == 160
        assert result == msg[:160]


class TestMqttConnectionManagerCoverageGaps:
    """Test MqttConnectionManager paths that were uncovered."""

    def test_retry_delay_calculates_max_of_three_delays(self) -> None:  # noqa: PLR6301, RUF105
        """retry_delay returns max of pause, backoff, and throttle (lines 1172-1173)."""
        mgr = MqttConnectionManager()
        # Set all three timers in the future
        # throttle = last_connect_attempt + MQTT_RECONNECT_THROTTLE_SEC (90)
        mgr.paused_until_monotonic = 2000.0
        mgr.backoff_until_monotonic = 1500.0
        mgr.last_connect_attempt = 1910.0  # throttle = 1910 + 90 = 2000

        with patch(
            "custom_components.jackery_solarvault.coordinator.time.monotonic",
            return_value=1000.0,
        ):
            delay = mgr.retry_delay()
            # max(1000, 500, 1000) = 1000
            assert delay == 1000.0  # noqa: RUF069, RUF105

    def test_retry_delay_zero_when_all_expired(self) -> None:  # noqa: PLR6301, RUF105
        """retry_delay returns 0 when all timers are in the past."""
        mgr = MqttConnectionManager()
        mgr.paused_until_monotonic = 500.0
        mgr.backoff_until_monotonic = 500.0
        mgr.last_connect_attempt = 500.0

        with patch(
            "custom_components.jackery_solarvault.coordinator.time.monotonic",
            return_value=1000.0,
        ):
            delay = mgr.retry_delay()
            assert delay == 0.0  # noqa: RUF069, RUF105

    def test_record_connect_success_early_return_when_mqtt_none(self) -> None:  # noqa: PLR6301, RUF105
        """record_connect_success returns early when mqtt is None (line 1380)."""
        mgr = MqttConnectionManager()
        # Should not raise, just return
        mgr.record_connect_success(None, ("client", "host", "session"))
        # State unchanged
        assert mgr.fingerprint is None

    def test_handle_connect_error_early_return_when_mqtt_none(self) -> None:  # noqa: PLR6301, RUF105
        """handle_connect_error returns early when mqtt is None (line 1410)."""
        mgr = MqttConnectionManager()
        # Should not raise, just return
        mgr.handle_connect_error(None, "some error")
        # State unchanged
        assert mgr.app_conflict_pause_cycles == 0
        assert mgr.backoff_until_monotonic == 0.0  # noqa: RUF069, RUF105

    def test_handle_connect_error_prefers_last_error_from_diagnostics(self) -> None:  # noqa: PLR6301, RUF105
        """handle_connect_error uses mqtt.diagnostics.last_error over passed error."""
        mgr = MqttConnectionManager()
        mqtt = SimpleNamespace(
            diagnostics={"last_error": "connect rc=5 auth failed"},
            consecutive_auth_failures=2,
        )
        # Even though we pass "generic error", the last_error from diagnostics is used
        mgr.handle_connect_error(cast("Any", mqtt), "generic error")

        # Should trigger auth pause because last_error contains auth failure
        assert mgr.app_conflict_pause_cycles == 1

    def test_handle_connect_error_fallback_to_passed_error(self) -> None:  # noqa: PLR6301, RUF105
        """handle_connect_error falls back to passed error when no last_error."""
        mgr = MqttConnectionManager()
        mqtt = SimpleNamespace(
            diagnostics={},  # No last_error
            consecutive_auth_failures=0,
        )
        mgr.handle_connect_error(cast("Any", mqtt), "connection refused")

        # Should trigger transient backoff
        assert mgr.backoff_until_monotonic > 0
        assert mgr.app_conflict_pause_cycles == 0

    def test_defer_background_auth_failure_with_none_mqtt(self) -> None:  # noqa: PLR6301, RUF105
        """defer_background_auth_failure handles None mqtt gracefully."""
        mgr = MqttConnectionManager()
        # Should not raise
        mgr.defer_background_auth_failure(None, "MQTT broker rejected credentials")
        # Should still trigger pause even with None mqtt
        assert mgr.app_conflict_pause_cycles == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
