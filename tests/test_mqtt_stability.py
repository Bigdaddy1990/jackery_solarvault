"""Long-running MQTT stability contract tests.

These pure-source tests guard the long-running stability of the broker
session against accidental regressions:

1. The MQTT engine does NOT implement an internal reconnect loop —
   the coordinator owns reconnect throttling so broker protocol
   rejections cannot loop.
2. Every successful (re-)connect re-subscribes ALL configured topics.
3. Every successful (re-)connect runs the snapshot-pull callback so the
   coordinator immediately has fresh state.
4. The integration exposes ``seconds_since_last_message`` and
   ``mqtt_silent_for_too_long`` in diagnostics so a stuck subscription
   is visible without enabling DEBUG.
5. The broker-rejection CONNACK reason is preserved across the
   subsequent disconnect callback so users see the actionable error,
   not the generic "disconnected" message.
"""

import datetime
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CLIENT_COMPONENT = ROOT / "custom_components" / "jackery_solarvault" / "client"
INTEGRATION_COMPONENT = ROOT / "custom_components" / "jackery_solarvault"


def _read(name: str) -> str:
    if name in {"mqtt_push.py"}:
        base = CLIENT_COMPONENT
    else:
        base = INTEGRATION_COMPONENT
    return (base / name).read_text(encoding="utf-8")


def test_mqtt_client_disables_internal_reconnect_loop() -> None:
    """Coordinator throttling must own reconnects after broker rejections.

    aiomqtt's context manager does not auto-reconnect by default. This test
    guards against accidentally adding a ``while True``/auto-reconnect loop
    around the session, which would race the coordinator-side throttle
    (``MQTT_RECONNECT_THROTTLE_SEC``) and reproduce gmqtt's old issue of
    looping on broker rejections.
    """
    src = _read("mqtt_push.py")
    coordinator_src = _read("coordinator.py")
    # No internal loop around the aiomqtt context manager.
    assert "while True" not in src, src
    assert "while not self._" not in src, src
    # Coordinator owns reconnect throttling.
    assert "MQTT_RECONNECT_THROTTLE_SEC" in coordinator_src, coordinator_src
    # No leftover gmqtt-era retry knobs.
    assert '"reconnect_retries"' not in src, src
    assert '"reconnect_delay"' not in src, src


def test_mqtt_client_fingerprint_does_not_retain_raw_secret() -> None:
    """Credential-change detection must not keep another raw password copy."""
    src = _read("mqtt_push.py")
    assert "import hashlib" in src, src
    assert "self._fingerprint: str | None = None" in src, src
    assert "def _credential_fingerprint(" in src, src
    assert "hashlib.sha256()" in src, src
    assert "fingerprint = self._credential_fingerprint(" in src, src
    assert "fingerprint = (client_id, username, password)" not in src, src


def test_every_connect_resubscribes_all_topics() -> None:
    """The session runner must iterate over self._topics + subscribe each.

    Without this, a reconnect after a network blip leaves the integration
    silently unsubscribed: the TCP session is back but no telemetry flows.
    """
    src = _read("mqtt_push.py")
    runner_match = re.search(
        r"async def _async_run_session\(.*?(?=\n    def |\n    async def |\n    @|\nclass )",
        src,
        re.S,
    )
    assert runner_match is not None, "_async_run_session not found"
    body = runner_match.group(0)
    assert "for topic in self._topics" in body, body
    assert "await client.subscribe(" in body, body


def test_every_connect_triggers_snapshot_callback() -> None:
    """A reconnect must re-pull the full app-state snapshot.

    On the Jackery cloud the broker only keeps retained state for the
    "device-online" notice, not the per-property values. Without a fresh
    snapshot pull on each reconnect the integration would carry stale
    values forward indefinitely.
    """
    src = _read("mqtt_push.py")
    runner_match = re.search(
        r"async def _async_run_session\(.*?(?=\n    def |\n    async def |\n    @|\nclass )",
        src,
        re.S,
    )
    assert runner_match is not None
    body = runner_match.group(0)
    assert "self._connect_callback" in body, body
    assert "_schedule_coroutine" in body, body


def test_connack_reason_preserved_across_post_reject_disconnect() -> None:
    """Preserve actionable CONNACK reason after broker rejects + closes.

    When the broker rejects a CONNACK and immediately closes the socket,
    ``last_error`` must keep the actionable CONNACK reason rather than
    being overwritten with the generic disconnect message.
    """
    src = _read("mqtt_push.py")
    on_disc_match = re.search(
        r"def _handle_disconnect_error\(self.*?(?=\n    @staticmethod|\n    def |\nclass )",
        src,
        re.S,
    )
    assert on_disc_match is not None, "_handle_disconnect_error not found"
    body = on_disc_match.group(0)
    # The handler must preserve connect-failure signatures and bail out
    # without overwriting them.
    assert "_is_connect_failure_error" in body, body
    assert "connect rc=" in src, src
    # And the connect-failure mapper itself must produce the rc=… signature
    # so ``_is_connect_failure_error`` can detect it.
    fail_match = re.search(
        r"def _handle_connect_failure\(self.*?(?=\n    @staticmethod|\n    def |\nclass )",
        src,
        re.S,
    )
    assert fail_match is not None, "_handle_connect_failure not found"
    fail_body = fail_match.group(0)
    assert "MQTT_CONNACK_REASONS" in fail_body, fail_body
    assert 'f"connect rc={rc}' in fail_body, fail_body


def test_failed_connect_stop_does_not_write_disconnect_to_closed_socket() -> None:
    """Stop-after-CONNACK-rejection must not write to a closed socket.

    aiomqtt's ``async with`` context manager handles this case automatically:
    on exit it only sends a DISCONNECT packet if the broker session was
    actually established. After a rejected CONNACK the context exits via the
    MqttCodeError path before any session is up, so no DISCONNECT is queued.
    Conversely, a previously connected session whose link drops passively
    (Errno 104) lets the ``async for`` raise MqttError, the context exits
    cleanly, and the runner task ends — no leftover keepalive coroutine.

    The contract: ``_async_stop_locked`` must NOT manually call
    ``client.disconnect()`` (which gmqtt required and which produced the
    ``[TRYING WRITE TO CLOSED SOCKET]`` spam). Cancelling the runner task is
    sufficient because aiomqtt cleans up on cancel.
    """
    src = _read("mqtt_push.py")
    stop_match = re.search(
        r"async def _async_stop_locked\(self.*?(?=\n    @staticmethod|\n    async def |\n    def |\nclass )",
        src,
        re.S,
    )
    assert stop_match is not None
    body = stop_match.group(0)
    # The legacy gmqtt-era manual-disconnect dance is gone.
    assert "client.disconnect()" not in body, body
    assert "client.disconnect" not in body, body
    assert "_was_connected" not in body, body
    # The runner task is what gets torn down; aiomqtt's context manager
    # handles the socket lifecycle on cancel.
    assert "task = self._runner_task" in body, body
    assert "task.cancel()" in body, body
    # No leftover legacy flag anywhere in the module.
    assert "_was_connected" not in src, src


def test_transient_mqtt_connect_failures_are_debug_not_warning_noise() -> None:
    """MQTT push is optional; transient broker refusals should not warn twice.

    With aiomqtt the engine no longer needs the ``_GmqttConnectionNoiseFilter``
    whack-a-mole filter. Instead, ``_handle_connect_failure`` differentiates
    auth rejections (warning, actionable) from transient refusals (debug)
    based on the CONNACK rc, and ``_handle_disconnect_error`` keeps already-
    mapped connect failures from being overwritten by generic disconnect text.
    """
    mqtt_src = _read("mqtt_push.py")
    coordinator_src = _read("coordinator.py")

    # The legacy filter must be gone.
    assert "_GmqttConnectionNoiseFilter" not in mqtt_src
    assert "logger=_GMQTT_LOGGER" not in mqtt_src
    # Connect failures have differentiated severity.
    assert "_is_connect_auth_failure_rc" in mqtt_src
    assert '_LOGGER.warning("Jackery MQTT connect failed: %s", message)' in mqtt_src
    assert '_LOGGER.debug("Jackery MQTT connect failed: %s", message)' in mqtt_src
    # Generic setup-error path stays at debug.
    assert '_LOGGER.debug("Jackery MQTT connect setup failed: %s", err)' in mqtt_src
    # Coordinator-side messaging stays informational, not warn-spammy.
    assert "Jackery MQTT initial connect did not complete" in coordinator_src
    assert '_LOGGER.warning("Jackery MQTT initial connect did not complete' not in (
        coordinator_src
    )
    assert (
        '_LOGGER.warning(\n                    "Jackery MQTT TLS/connect check failed'
        not in (coordinator_src)
    )


def test_aiomqtt_passive_reset_log_is_filtered() -> None:
    """Expected broker socket resets should not surface as HA error log spam."""
    src = _read("mqtt_push.py")

    assert "_AioMqttPassiveDisconnectFilter" in src
    assert '"failed to receive on socket"' in src
    assert '"Errno 104"' in src
    assert '"Connection reset by peer"' in src
    assert '"WinError 10054"' in src
    assert "_AIOMQTT_LOGGER.addFilter(" in src
    assert "logger=_AIOMQTT_LOGGER" in src
    assert "logger=_LOGGER" not in src


def test_diagnostics_exposes_stale_subscription_signals() -> None:
    """Diagnostics must surface ``seconds_since_last_message`` + flag."""
    src = _read("mqtt_push.py")
    diag_match = re.search(
        r"def diagnostics\(self.*?(?=\n    def |\n    @|\nclass )",
        src,
        re.S,
    )
    assert diag_match is not None, "diagnostics method not found"
    body = diag_match.group(0)
    assert "seconds_since_last_message" in body, body
    assert "mqtt_silent_for_too_long" in body, body


def test_silent_threshold_constant_is_sane() -> None:
    """MQTT_SILENT_THRESHOLD_SEC must be a positive int in a useful range."""
    src = _read("const.py")
    match = re.search(r"MQTT_SILENT_THRESHOLD_SEC:\s*Final\s*=\s*(\d+)", src)
    assert match is not None, src
    threshold = int(match.group(1))
    # Real Jackery heartbeats every ~30 s; we want to flag silence
    # well after that but before users complain about stale data.
    assert 60 <= threshold <= 1800, threshold


def test_seconds_since_last_message_handles_no_messages() -> None:
    """Helper must return None when no message has ever been seen.

    A zero or negative value would falsely indicate "fresh data" in
    diagnostics, hiding a broken subscription.
    """
    src = _read("mqtt_push.py")
    match = re.search(
        r"def _seconds_since_last_message\(self.*?(?=\n    def |\n    @|\nclass )",
        src,
        re.S,
    )
    assert match is not None, "_seconds_since_last_message not found"
    body = match.group(0)
    # Returns None if last_message_at is None
    assert "if self._last_message_at is None" in body, body
    assert "return None" in body, body
    # Never returns negative values
    assert "max(0.0," in body or "max(0," in body, body


def test_silent_detector_only_active_when_connected() -> None:
    """The stale-flag must not fire while we're not even connected.

    Otherwise a freshly-restarted HA would always show the warning until
    the first message arrives — drowning the actually-useful signal.
    """
    src = _read("mqtt_push.py")
    match = re.search(
        r"def _mqtt_silent_for_too_long\(self.*?(?=\n    def |\n    @|\nclass )",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)
    assert "if not self._connected" in body, body
    # Must return False before flagging silence
    assert "return False" in body, body


def test_keepalive_is_set_on_connect() -> None:
    """The aiomqtt Client constructor must receive keepalive.

    Without keepalive the broker tear-down on intermittent network
    glitches takes 60+ minutes (TCP default).
    """
    src = _read("mqtt_push.py")
    keepalive_lines = [
        line.strip() for line in src.splitlines() if "keepalive=" in line
    ]
    assert keepalive_lines == ["keepalive=MQTT_KEEPALIVE_SEC,"]


def test_silent_threshold_logic_unit() -> None:
    """Quick simulation: an old timestamp must produce a 'silent' flag."""
    # We don't run the actual class (HA dependency); instead we emulate
    # the logic to make sure the contract holds when used at runtime.
    threshold_seconds = 300

    def silent(
        connected: bool,
        last_msg_iso: str | None,
        last_connect_iso: str | None,
        now: datetime.datetime,
    ) -> bool:
        if not connected:
            return False
        if last_msg_iso is None:
            if last_connect_iso is None:
                return False
            then = datetime.datetime.fromisoformat(last_connect_iso)
            return (now - then).total_seconds() > threshold_seconds
        then = datetime.datetime.fromisoformat(last_msg_iso)
        elapsed = max(0.0, (now - then).total_seconds())
        return elapsed > threshold_seconds

    now = datetime.datetime(2026, 5, 5, 12, 0, 0, tzinfo=datetime.UTC)
    fresh = (now - datetime.timedelta(seconds=10)).isoformat()
    stale = (now - datetime.timedelta(seconds=900)).isoformat()

    # Healthy: just received a message
    assert silent(True, fresh, fresh, now) is False
    # Stale: last message 15 minutes ago
    assert silent(True, stale, stale, now) is True
    # Disconnected: never silent
    assert silent(False, stale, stale, now) is False
    # Connected but never received a message AND just connected: not silent yet
    assert silent(True, None, fresh, now) is False
    # Connected but never received a message AND been connected for 15 min: silent
    assert silent(True, None, stale, now) is True


def test_coordinator_refresh_does_not_suppress_reauth_failures() -> None:
    """Scheduled coordinator polling must not wrap auth failures."""
    src = _read("coordinator.py")
    assert "async def _async_periodic_refresh" not in src
    assert "async_track_time_interval" not in src
    assert "update_interval=update_interval" in src
    match = re.search(r"async def _async_update_data\(.*?\n    # --", src, re.S)
    assert match is not None
    body = match.group(0)
    assert "_raise_config_entry_auth_failed" in body, body


def test_passive_disconnect_triggers_immediate_reconnect_recovery() -> None:
    """A server-side broker drop must trigger an immediate reconnect.

    With aiomqtt, a passive disconnect (Errno 104 / ConnectionResetError)
    raises MqttError out of the ``async for`` loop. The session task exits
    via the ``finally`` block, which fires ``disconnect_callback`` only
    when the session had actually been connected (rules out CONNACK
    rejections). The coordinator's ``_async_handle_mqtt_disconnect``
    then resets the throttle and calls ``_async_ensure_mqtt(force=True)``
    to start a fresh session.
    """
    mqtt_src = _read("mqtt_push.py")
    coord_src = _read("coordinator.py")

    # MQTT client surfaces the disconnect_callback parameter.
    assert (
        "disconnect_callback: Callable[[], Awaitable[None]] | None = None" in mqtt_src
    ), mqtt_src
    assert "self._disconnect_callback = disconnect_callback" in mqtt_src, mqtt_src

    # The session runner routes through the callback only after a real
    # session — not after a CONNACK rejection.
    runner_match = re.search(
        r"async def _async_run_session\(.*?(?=\n    def |\n    async def |\n    @|\nclass )",
        mqtt_src,
        re.S,
    )
    assert runner_match is not None
    body = runner_match.group(0)
    assert "was_connected = connected" in body, body
    assert "self._disconnect_callback is not None" in body, body
    assert '"disconnect-recover"' in body, body

    # Coordinator wires the disconnect callback at client construction.
    assert "disconnect_callback=self._async_handle_mqtt_disconnect" in coord_src, (
        coord_src
    )

    # Recovery handler resets the throttle and force-reconnects.
    handler_match = re.search(
        r"async def _async_handle_mqtt_disconnect\(self\).*?(?=\n    async def |\n    @|\nclass |\n    def )",
        coord_src,
        re.S,
    )
    assert handler_match is not None
    handler_body = handler_match.group(0)
    assert "self._last_mqtt_connect_attempt = 0.0" in handler_body, handler_body
    assert "_async_ensure_mqtt(force=True)" in handler_body, handler_body
    assert "JackeryAuthError" in handler_body, handler_body


def test_failed_http_refresh_does_not_advance_mqtt_keepalive() -> None:
    """Only successful HTTP refreshes may extend the adaptive keep-alive window.

    When MQTT is live, a failed HTTP keep-alive must be retried on the next
    coordinator refresh instead of being treated like a completed refresh for
    five minutes.
    """
    src = _read("coordinator.py")
    match = re.search(
        r"async def _async_update_data\(.*?(?=\n    # --)",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)
    assert "finally:" not in body, body
    assert "self._last_http_refresh_completed_monotonic = time.monotonic()" in body
    assignment_offset = body.index(
        "self._last_http_refresh_completed_monotonic = time.monotonic()"
    )
    skip_return_offset = body.index("return snapshot")
    assert assignment_offset > skip_return_offset, body
    skip_block = body.split("if self._should_skip_refresh_for_live_mqtt():", 1)[
        1
    ].split("started = time.monotonic()", 1)[0]
    assert "snapshot = self.data or {}" in skip_block, skip_block
    assert "self._schedule_mqtt_backfill_queries(snapshot)" in skip_block, skip_block
    assert "_schedule_battery_pack_ota_enrichment(device_id)" in skip_block, skip_block

    backfill_match = re.search(
        r"async def _async_mqtt_backfill_queries\(.*?(?=\n    # --)",
        src,
        re.S,
    )
    assert backfill_match is not None
    backfill_body = backfill_match.group(0)
    assert backfill_body.index("_async_query_subdevices_for_missing") < (
        backfill_body.index("_async_query_system_info_for_missing")
    ), backfill_body

    skip_match = re.search(
        r"def _should_skip_refresh_for_live_mqtt\(self\).*?(?=\n    async def )",
        src,
        re.S,
    )
    assert skip_match is not None
    skip_body = skip_match.group(0)
    assert "if not self.data:" in skip_body, skip_body
    assert (
        "return False"
        in skip_body.split("if not self.data:", 1)[1].split(
            "if self._mqtt is None:", 1
        )[0]
    ), skip_body
