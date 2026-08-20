"""Behavioral tests for MQTT/BLE connection-state helpers.

These cover the pure state machines the coordinator delegates to for MQTT
reconnect decisions (:class:`MqttConnectionManager`), per-device BLE connect
spacing (:class:`BleConnectBackoff`), rejection accounting
(:class:`RejectionMetrics`), and the broker-message classifiers. The governing
invariant throughout: MQTT/BLE failures adjust local backoff/pause state only —
they never drive HA reauthentication.
"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.const import (
    MQTT_APP_CONFLICT_PAUSE_SEC,
    MQTT_CONNECT_BACKOFF_STEPS_SEC,
    MQTT_RECONNECT_THROTTLE_SEC,
    MQTT_TRANSIENT_BACKOFF_STEPS_SEC,
)
from custom_components.jackery_solarvault.coordinator import (
    BleConnectBackoff,
    JackerySolarVaultCoordinator,
    MqttConnectionManager,
    RejectionMetrics,
    is_mqtt_auth_failure,
    is_transient_connect_failure,
    mqtt_connect_failure_signature,
)

_MODULE = "custom_components.jackery_solarvault.coordinator"


def _freeze_monotonic(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    monkeypatch.setattr(f"{_MODULE}.time.monotonic", lambda: value)


# ---------------------------------------------------------------------------
# BleConnectBackoff
# ---------------------------------------------------------------------------


def test_ble_backoff_starts_at_initial_delay() -> None:
    """The first failure opens a window one initial-delay wide (±25% jitter)."""
    backoff = BleConnectBackoff()

    delay = backoff.record_failure(now=100.0)

    assert delay == backoff.initial_sec
    # seconds_until_allowed includes ±25% jitter (0.75-1.25 x initial_sec)
    wait = backoff.seconds_until_allowed(now=100.0)
    assert wait >= backoff.initial_sec * 0.75
    assert wait <= backoff.initial_sec * 1.25


def test_ble_backoff_doubles_until_capped() -> None:
    """Repeated failures double the delay but never exceed the ceiling."""
    backoff = BleConnectBackoff()

    first = backoff.record_failure(now=0.0)
    second = backoff.record_failure(now=0.0)

    assert first == backoff.initial_sec
    assert second == min(first * 2, backoff.max_sec)
    delay = second
    while delay < backoff.max_sec:
        next_delay = backoff.record_failure(now=0.0)
        assert next_delay == min(delay * 2, backoff.max_sec)
        delay = next_delay
    # Once capped, further failures never exceed the ceiling.
    assert backoff.record_failure(now=0.0) == backoff.max_sec


def test_ble_backoff_success_resets_ladder() -> None:
    """A success clears the window so the next attempt is allowed immediately."""
    backoff = BleConnectBackoff()
    backoff.record_failure(now=0.0)

    backoff.record_success()

    assert backoff.seconds_until_allowed(now=0.0) == pytest.approx(0.0)
    assert backoff.record_failure(now=0.0) == backoff.initial_sec


def test_ble_backoff_window_elapses_with_time() -> None:
    """Once the clock passes the window (including ±25% jitter), no wait remains."""
    backoff = BleConnectBackoff()
    backoff.record_failure(now=100.0)
    # The actual window includes ±25% jitter, so wait until max possible jittered delay
    max_wait = backoff.initial_sec * 1.25

    assert backoff.seconds_until_allowed(now=100.0 + max_wait + 1) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Broker message classifiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "connect rc=5",
        "Connection failed [code:134]",
        "bad user name or password",
        "client is not authorized",
    ],
)
def test_auth_failure_messages_are_recognised(message: str) -> None:
    """Broker credential rejections are classified as auth failures."""
    assert is_mqtt_auth_failure(message) is True


def test_low_paho_error_codes_are_not_auth_failures() -> None:
    """A low paho client-side code (code:4 = no-conn) must not read as auth."""
    assert is_mqtt_auth_failure("mqtt error code:4 no connection") is False


def test_auth_failure_is_never_transient() -> None:
    """Auth rejections are permanent even if wording looks transient."""
    assert is_transient_connect_failure("connect rc=5 server unavailable") is False


@pytest.mark.parametrize(
    "message",
    ["server unavailable", "connection refused", "connection timed out", "unknown"],
)
def test_transient_failures_are_recognised(message: str) -> None:
    """Network-class failures are classified as transient."""
    assert is_transient_connect_failure(message) is True


def test_connect_failure_signature_normalises_tls_errors() -> None:
    """Known TLS failures collapse to stable signatures for dedup."""
    assert (
        mqtt_connect_failure_signature("x Missing Authority Key Identifier y")
        == "tls_missing_authority_key_identifier"
    )
    assert (
        mqtt_connect_failure_signature("ssl: CERTIFICATE_VERIFY_FAILED")
        == "tls_certificate_verify_failed"
    )
    assert mqtt_connect_failure_signature("") == "unknown"


# ---------------------------------------------------------------------------
# MqttConnectionManager: backoff
# ---------------------------------------------------------------------------


def test_manager_permanent_backoff_walks_the_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated permanent failures advance through the permanent step ladder."""
    _freeze_monotonic(monkeypatch, 1_000.0)
    mgr = MqttConnectionManager()

    mgr.note_connect_failure("permanent boom")
    assert mgr.backoff_remaining() == MQTT_CONNECT_BACKOFF_STEPS_SEC[0]

    mgr.note_connect_failure("permanent boom")
    assert mgr.backoff_remaining() == MQTT_CONNECT_BACKOFF_STEPS_SEC[1]


def test_manager_transient_failure_uses_transient_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient signature backs off on the short ladder."""
    _freeze_monotonic(monkeypatch, 1_000.0)
    mgr = MqttConnectionManager()

    mgr.note_connect_failure("connection refused")

    assert mgr.backoff_remaining() == MQTT_TRANSIENT_BACKOFF_STEPS_SEC[0]


def test_manager_new_signature_resets_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing the failure signature restarts the ladder at step zero."""
    _freeze_monotonic(monkeypatch, 1_000.0)
    mgr = MqttConnectionManager()
    mgr.note_connect_failure("permanent one")
    mgr.note_connect_failure("permanent one")

    mgr.note_connect_failure("permanent two")

    assert mgr.backoff_remaining() == MQTT_CONNECT_BACKOFF_STEPS_SEC[0]


def test_manager_clear_backoff_zeroes_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recovered session clears the backoff window and signature."""
    _freeze_monotonic(monkeypatch, 1_000.0)
    mgr = MqttConnectionManager()
    mgr.note_connect_failure("permanent boom")

    mgr.clear_connect_backoff()

    assert mgr.backoff_remaining() == 0
    assert mgr.backoff_signature is None
    assert mgr.backoff_step == -1


# ---------------------------------------------------------------------------
# MqttConnectionManager: auth-failure pause
# ---------------------------------------------------------------------------


def test_manager_auth_failure_opens_pause_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broker credential rejection opens a fixed app-conflict pause."""
    _freeze_monotonic(monkeypatch, 500.0)
    mgr = MqttConnectionManager()

    mgr.pause_after_auth_failure("rejected", streak=2)

    assert mgr.app_conflict_pause_cycles == 1
    assert mgr.paused_until_monotonic == pytest.approx(
        500.0 + MQTT_APP_CONFLICT_PAUSE_SEC
    )


def test_manager_pause_is_not_reopened_while_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second rejection inside the window does not extend or recount it."""
    _freeze_monotonic(monkeypatch, 500.0)
    mgr = MqttConnectionManager()
    mgr.pause_after_auth_failure("rejected")

    mgr.pause_after_auth_failure("rejected again")

    assert mgr.app_conflict_pause_cycles == 1


# ---------------------------------------------------------------------------
# MqttConnectionManager: should_skip_reconnect
# ---------------------------------------------------------------------------


def _mqtt_stub(*, started: bool, connected: bool) -> SimpleNamespace:
    return SimpleNamespace(
        is_started=started,
        is_connected=connected,
        diagnostics={},
        consecutive_auth_failures=0,
    )


def test_skip_reconnect_when_no_client() -> None:
    """Without a client there is nothing to reconnect."""
    mgr = MqttConnectionManager()

    assert mgr.should_skip_reconnect(None, ("c", "h", "s")) is True


def test_skip_reconnect_fast_path_clears_stale_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy matching connection short-circuits and clears any stale pause."""
    _freeze_monotonic(monkeypatch, 10.0)
    mgr = MqttConnectionManager()
    fingerprint = ("c", "h", "s")
    mgr.fingerprint = fingerprint
    mgr.app_conflict_pause_cycles = 3
    mqtt = _mqtt_stub(started=True, connected=True)

    assert mgr.should_skip_reconnect(cast("Any", mqtt), fingerprint) is True
    assert mgr.app_conflict_pause_cycles == 0
    assert mgr.paused_until_monotonic == pytest.approx(0.0)


def test_connected_client_with_unknown_fingerprint_reaches_credential_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connectivity alone cannot prove which credential session is active."""
    _freeze_monotonic(monkeypatch, 1_000.0)
    mgr = MqttConnectionManager()
    mgr.note_connect_failure("permanent boom")
    fingerprint = ("c", "h", "s")
    mqtt = _mqtt_stub(started=True, connected=True)

    assert mgr.should_skip_reconnect(cast("Any", mqtt), fingerprint) is False
    assert mgr.fingerprint is None
    assert mgr.backoff_remaining() > 0
    assert mgr.backoff_signature == "permanent boom"


def test_skip_reconnect_during_active_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An active pause window suppresses reconnects."""
    _freeze_monotonic(monkeypatch, 100.0)
    mgr = MqttConnectionManager()
    mgr.paused_until_monotonic = 200.0
    mqtt = _mqtt_stub(started=False, connected=False)

    assert mgr.should_skip_reconnect(cast("Any", mqtt), ("c", "h", "s")) is True


def test_skip_reconnect_throttles_recent_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fingerprint change reconnect is throttled shortly after an attempt."""
    _freeze_monotonic(monkeypatch, 1_000.0)
    mgr = MqttConnectionManager()
    mgr.fingerprint = ("old", "h", "s")
    mgr.last_connect_attempt = 1_000.0 - (MQTT_RECONNECT_THROTTLE_SEC / 2)
    mqtt = _mqtt_stub(started=True, connected=False)

    assert mgr.should_skip_reconnect(cast("Any", mqtt), ("new", "h", "s")) is True


def test_force_bypasses_throttle_and_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced (command) connect ignores the throttle fast path."""
    _freeze_monotonic(monkeypatch, 1_000.0)
    mgr = MqttConnectionManager()
    mgr.fingerprint = ("old", "h", "s")
    mgr.last_connect_attempt = 1_000.0
    mgr.paused_until_monotonic = 2_000.0
    mgr.backoff_until_monotonic = 2_000.0
    mqtt = _mqtt_stub(started=True, connected=False)

    assert (
        mgr.should_skip_reconnect(cast("Any", mqtt), ("new", "h", "s"), force=True)
        is False
    )


def test_record_connect_success_stores_fingerprint_and_clears_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A success records the fingerprint and clears any pending backoff."""
    _freeze_monotonic(monkeypatch, 1_000.0)
    mgr = MqttConnectionManager()
    mgr.note_connect_failure("permanent boom")
    fingerprint = ("c", "h", "s")
    mqtt = _mqtt_stub(started=True, connected=True)

    mgr.record_connect_success(cast("Any", mqtt), fingerprint)

    assert mgr.fingerprint == fingerprint
    assert mgr.backoff_remaining() == 0


# ---------------------------------------------------------------------------
# MqttConnectionManager: error handling never triggers reauth
# ---------------------------------------------------------------------------


def test_handle_connect_error_auth_path_pauses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An auth error routes to a pause, not reauth."""
    _freeze_monotonic(monkeypatch, 500.0)
    mgr = MqttConnectionManager()
    mqtt = SimpleNamespace(
        diagnostics={"last_error": "connect rc=5"},
        consecutive_auth_failures=4,
    )

    mgr.handle_connect_error(cast("Any", mqtt), "irrelevant")

    assert mgr.app_conflict_pause_cycles == 1
    assert mgr.backoff_until_monotonic == pytest.approx(0.0)
    assert mgr.paused_until_monotonic > 500.0


def test_mqtt_auth_pause_never_invalidates_http_session() -> None:
    """A broker rejection must not clear HTTP auth or force an HTTP login."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    api = MagicMock()
    coordinator.api = cast("Any", api)
    coordinator.rejection_metrics = RejectionMetrics()
    coordinator._mqtt_mgr = MqttConnectionManager()

    coordinator._pause_mqtt_after_auth_failure(
        "connect rc=134 (bad user name or password)",
        streak=1,
    )

    api.invalidate_mqtt_session_for_http_refresh.assert_not_called()
    assert coordinator._mqtt_mgr.app_conflict_pause_cycles == 1


def test_handle_connect_error_non_auth_path_backs_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network error routes to connect backoff."""
    _freeze_monotonic(monkeypatch, 500.0)
    mgr = MqttConnectionManager()
    mqtt = SimpleNamespace(diagnostics={}, consecutive_auth_failures=0)

    mgr.handle_connect_error(cast("Any", mqtt), "connection refused")

    assert mgr.backoff_remaining() == MQTT_TRANSIENT_BACKOFF_STEPS_SEC[0]
    assert mgr.app_conflict_pause_cycles == 0


def test_defer_background_auth_failure_pauses_on_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A background broker rejection pauses MQTT but leaves reauth untouched."""
    _freeze_monotonic(monkeypatch, 500.0)
    mgr = MqttConnectionManager()
    mqtt = SimpleNamespace(consecutive_auth_failures=1)

    mgr.defer_background_auth_failure(
        cast("Any", mqtt),
        "MQTT broker rejected credentials",
    )

    assert mgr.app_conflict_pause_cycles == 1


def test_defer_background_auth_failure_ignores_generic_notice() -> None:
    """A non-rejection background notice is ignored for reauth purposes."""
    mgr = MqttConnectionManager()

    mgr.defer_background_auth_failure(None, "some unrelated warning")

    assert mgr.app_conflict_pause_cycles == 0
    assert mgr.backoff_until_monotonic == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# RejectionMetrics
# ---------------------------------------------------------------------------


def test_rejection_metrics_counts_first_seen_reason() -> None:
    """A new (counter, reason) pair increments and is remembered as latest."""
    metrics = RejectionMetrics()

    metrics.increment("schema_rejections", "missing-field")

    assert metrics.schema_rejections == 1
    assert metrics.last_rejection is not None
    assert metrics.last_rejection["reason"] == "missing-field"


def test_rejection_metrics_dedupes_repeated_reason() -> None:
    """The same (counter, reason) pair is only counted once."""
    metrics = RejectionMetrics()

    metrics.increment("schema_rejections", "missing-field")
    metrics.increment("schema_rejections", "missing-field")

    assert metrics.schema_rejections == 1


def test_rejection_metrics_as_dict_exposes_all_counters() -> None:
    """The diagnostics payload surfaces every counter and the latest rejection."""
    metrics = RejectionMetrics()
    metrics.increment("http_auth_rejections", "401")

    payload = metrics.as_dict()

    assert payload["counters"]["http_auth_rejections"] == 1
    assert payload["counters"]["schema_rejections"] == 0
    assert payload["last_rejection"]["counter"] == "http_auth_rejections"
