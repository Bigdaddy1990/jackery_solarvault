"""Targeted tests for uncovered lines in coordinator.py to achieve 100% coverage."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

from custom_components.jackery_solarvault.coordinator import (
    MqttConnectionManager,
    _load_mqtt_push_client,  # ruff: ignore[import-private-name]
    mqtt_connect_failure_signature,
)


def test_load_mqtt_push_client_imports_correctly() -> None:
    """_load_mqtt_push_client returns the JackeryMqttPushClient class."""
    # This tests lines 834-835 which were uncovered
    client_class = _load_mqtt_push_client()
    assert client_class is not None
    assert client_class.__name__ == "JackeryMqttPushClient"


class TestMqttConnectFailureSignature:
    """Test mqtt_connect_failure_signature edge cases."""

    def test_mqtt_not_connected_yet_prefix(self) -> None:  # ruff: ignore[no-self-use]
        """Messages starting with 'MQTT not connected yet' return first 160 chars (line 1108)."""  # ruff: ignore[line-too-long]
        msg = "MQTT not connected yet - waiting for broker"
        result = mqtt_connect_failure_signature(msg)
        assert result == msg[:160]
        assert result.startswith("MQTT not connected yet")

    def test_mqtt_not_connected_yet_long_message_truncated(self) -> None:  # ruff: ignore[no-self-use]
        """Long 'MQTT not connected yet' messages are truncated to 160 chars."""
        msg = "MQTT not connected yet - " + "x" * 200
        result = mqtt_connect_failure_signature(msg)
        assert len(result) == 160  # ruff: ignore[magic-value-comparison]
        assert result.startswith("MQTT not connected yet")

    def test_empty_message_returns_unknown(self) -> None:  # ruff: ignore[no-self-use]
        """Empty or falsy messages return 'unknown'."""
        assert mqtt_connect_failure_signature("") == "unknown"
        assert mqtt_connect_failure_signature(None) == "unknown"
        assert mqtt_connect_failure_signature("   ") == "unknown"

    def test_generic_message_truncated_to_160(self) -> None:  # ruff: ignore[no-self-use]
        """Generic messages are truncated to 160 chars (line 1109)."""
        msg = "Some generic error message " + "x" * 200
        result = mqtt_connect_failure_signature(msg)
        assert len(result) == 160  # ruff: ignore[magic-value-comparison]
        assert result == msg[:160]


class TestMqttConnectionManagerCoverageGaps:
    """Test MqttConnectionManager paths that were uncovered."""

    def test_retry_delay_calculates_max_of_three_delays(self) -> None:  # ruff: ignore[no-self-use]
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
            assert delay == 1000.0  # ruff: ignore[magic-value-comparison, float-equality-comparison]

    def test_retry_delay_zero_when_all_expired(self) -> None:  # ruff: ignore[no-self-use]
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
            assert delay == 0.0  # ruff: ignore[float-equality-comparison]

    def test_record_connect_success_early_return_when_mqtt_none(self) -> None:  # ruff: ignore[no-self-use]
        """record_connect_success returns early when mqtt is None (line 1380)."""
        mgr = MqttConnectionManager()
        # Should not raise, just return
        # pyrefly: ignore [bad-argument-type]
        mgr.record_connect_success(None, ("client", "host", "session"))
        # State unchanged
        assert mgr.fingerprint is None

    def test_handle_connect_error_early_return_when_mqtt_none(self) -> None:  # ruff: ignore[no-self-use]
        """handle_connect_error returns early when mqtt is None (line 1410)."""
        mgr = MqttConnectionManager()
        # Should not raise, just return
        mgr.handle_connect_error(None, "some error")
        # State unchanged
        assert mgr.app_conflict_pause_cycles == 0
        assert mgr.backoff_until_monotonic == 0.0  # ruff: ignore[float-equality-comparison]

    def test_handle_connect_error_prefers_last_error_from_diagnostics(self) -> None:  # ruff: ignore[no-self-use]
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

    def test_handle_connect_error_fallback_to_passed_error(self) -> None:  # ruff: ignore[no-self-use]
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

    def test_defer_background_auth_failure_with_none_mqtt(self) -> None:  # ruff: ignore[no-self-use]
        """defer_background_auth_failure handles None mqtt gracefully."""
        mgr = MqttConnectionManager()
        # Should not raise
        mgr.defer_background_auth_failure(None, "MQTT broker rejected credentials")
        # Should still trigger pause even with None mqtt
        assert mgr.app_conflict_pause_cycles == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
