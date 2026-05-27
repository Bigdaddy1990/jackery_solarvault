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
    """
    Read and return the UTF-8 text of a source file from the appropriate component directory.
    
    Parameters:
        name (str): Filename to read; "mqtt_push.py" is read from the client component, all other names are read from the integration component.
    
    Returns:
        str: File contents decoded as UTF-8.
    """
    base = CLIENT_COMPONENT if name in {"mqtt_push.py"} else INTEGRATION_COMPONENT
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
    """
    Ensure the MQTT client's credential-change detection does not retain raw password data.
    
    Verifies the module computes and stores a hashed credential fingerprint (using `hashlib.sha256` and `_credential_fingerprint`) and exposes a `_fingerprint` member, and asserts the source does not contain a stored tuple of raw credentials `(client_id, username, password)`.
    """
    src = _read("mqtt_push.py")
    assert "import hashlib" in src, src
    assert "self._fingerprint: str | None = None" in src, src
    assert "def _credential_fingerprint(" in src, src
    assert "hashlib.sha256()" in src, src
    assert "fingerprint = self._credential_fingerprint(" in src, src
    assert "fingerprint = (client_id, username, password)" not in src, src


def test_every_connect_resubscribes_all_topics() -> None:
    """
    Ensure the session runner re-subscribes all configured MQTT topics on each connection.
    
    Asserts that `_async_run_session` iterates over `self._topics` and calls `await client.subscribe(...)` so a reconnect does not leave the integration unsubscribed and without telemetry.
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
    """
    Ensure the broker CONNACK failure reason is preserved when the broker rejects the connection and closes the socket.
    
    Asserts that the disconnect handler does not overwrite an actionable CONNACK reason (the `"connect rc=..."` signature) and that the connect-failure mapper exposes broker CONNACK reasons via `MQTT_CONNACK_REASONS` and formats them as `f"connect rc={rc}"`.
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
    auth rejections (debug until repeated, actionable at tolerance) from
    transient refusals (debug) based on the CONNACK rc, and
    ``_handle_disconnect_error`` keeps already-mapped connect failures from
    being overwritten by generic disconnect text.
    """
    mqtt_src = _read("mqtt_push.py")
    coordinator_src = _read("coordinator.py")

    # The legacy filter must be gone.
    assert "_GmqttConnectionNoiseFilter" not in mqtt_src
    assert "logger=_GMQTT_LOGGER" not in mqtt_src
    # Connect failures have differentiated severity. Single auth failures stay
    # out of the default HA log; repeated auth failures carry the streak
    # counter so users can distinguish transient app races from bad creds.
    assert "_is_connect_auth_failure_rc" in mqtt_src
    assert '"Jackery MQTT connect failed: %s (streak=%d)"' in mqtt_src
    assert '"Jackery MQTT connect failed repeatedly: %s (streak=%d)"' in mqtt_src
    assert "MQTT_AUTH_FAILURE_TOLERANCE" in mqtt_src
    assert "_LOGGER.warning(" in mqtt_src
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
    """
    Ensure passive broker socket reset messages are filtered from Home Assistant error logs.
    
    Asserts that mqtt_push.py defines `_AioMqttPassiveDisconnectFilter`, contains common passive-reset message substrings (e.g. "failed to receive on socket", "Errno 104", "Connection reset by peer", "WinError 10054"), registers the filter with `_AIOMQTT_LOGGER`, and does not register the filter on the general `_LOGGER`.
    """
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
    """
    Assert that the diagnostics_snapshot method exposes the `seconds_since_last_message` value and the `mqtt_silent_for_too_long` flag.
    
    Raises:
        AssertionError: If the diagnostics_snapshot method is missing or either key/flag is not present.
    """
    src = _read("mqtt_push.py")
    diag_match = re.search(
        r"def diagnostics_snapshot\(self.*?(?=\n    @property\n    def diagnostics|\nclass )",
        src,
        re.S,
    )
    assert diag_match is not None, "diagnostics_snapshot method not found"
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
    """
    Verify that a passive broker socket reset causes the coordinator to immediately attempt reconnect recovery.
    
    Asserts that:
    - The MQTT client API accepts and stores a `disconnect_callback` parameter.
    - The session runner only invokes the disconnect callback after a real connected session (uses `was_connected` and checks `self._disconnect_callback is not None`) and logs a `"disconnect-recover"` marker.
    - The coordinator constructs the client with `disconnect_callback=self._async_handle_mqtt_disconnect`.
    - The coordinator's disconnect handler resets throttling (`self._last_mqtt_connect_attempt = 0.0`), forces a reconnect (`_async_ensure_mqtt(force=True, wait_connected=True)`), and reclassifies/auth-handles failures (`JackeryAuthError`, `ConfigEntryAuthFailed`, `self._defer_background_auth_failure(err)`, and `_pause_mqtt_after_auth_failure`).
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
    assert "_async_ensure_mqtt(force=True, wait_connected=True)" in handler_body, (
        handler_body
    )
    assert "JackeryAuthError" in handler_body, handler_body
    assert "ConfigEntryAuthFailed" in handler_body, handler_body
    assert "self._defer_background_auth_failure(err)" in handler_body, handler_body
    assert "_pause_mqtt_after_auth_failure" in coord_src, coord_src


def test_http_property_polling_is_not_skipped_when_mqtt_is_live() -> None:
    """
    Ensure the coordinator continues HTTP property polling on every tick even when MQTT is live.
    
    Verifies the captured `_async_update_data` implementation:
    - does not use a `finally:` section that would short-circuit polling,
    - evaluates `skip_fast_property_fetch = self._should_skip_fast_property_fetch()` but does not short-circuit with `return snapshot` or an `if skip_fast_property_fetch:` that skips scheduling,
    - does not call `_schedule_mqtt_backfill_queries` in that section,
    - performs `payload = await self.api.async_get_device_property(dev_id)` and calls `_fetch_device_extras` after the device property fetch,
    - schedules statistics import before scheduling MQTT poll queries,
    - does not run `_async_import_and_repair_app_chart_statistics` in this path,
    - updates `self._last_http_refresh_completed_monotonic` when `property_fetch_completed` is true.
    
    Also asserts:
    - `_async_mqtt_poll_queries` calls `_async_query_subdevices_for_missing` before `_async_query_system_info_for_missing`,
    - `_should_skip_fast_property_fetch` always returns `False` and does not reference `MQTT_LIVE_THRESHOLD_SEC`.
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
    assert "skip_fast_property_fetch = self._should_skip_fast_property_fetch()" in body
    assert "return snapshot" not in body, body
    assert "if skip_fast_property_fetch:" not in body, body
    assert "_schedule_mqtt_backfill_queries" not in body, body
    assert "payload = await self.api.async_get_device_property(dev_id)" in body, body
    assert body.index(
        "payload = await self.api.async_get_device_property"
    ) < body.index("extras = await _fetch_device_extras")
    assert body.index("self._schedule_statistics_import(result)") < (
        body.index("self._schedule_mqtt_poll_queries(result)")
    ), body
    assert "await self._async_import_and_repair_app_chart_statistics(result)" not in (
        body
    ), body
    assert "if property_fetch_completed:" in body, body
    assert "self._last_http_refresh_completed_monotonic = completed" in body, body

    poll_match = re.search(
        r"async def _async_mqtt_poll_queries\(.*?(?=\n    # --)",
        src,
        re.S,
    )
    assert poll_match is not None
    poll_body = poll_match.group(0)
    assert poll_body.index("_async_query_subdevices_for_missing") < (
        poll_body.index("_async_query_system_info_for_missing")
    ), poll_body

    skip_match = re.search(
        r"def _should_skip_fast_property_fetch\(self\).*?(?=\n    async def )",
        src,
        re.S,
    )
    assert skip_match is not None
    skip_body = skip_match.group(0)
    assert "return False" in skip_body, skip_body
    assert "MQTT_LIVE_THRESHOLD_SEC" not in skip_body, skip_body


def test_mqtt_partial_updates_do_not_reset_http_poll_timer() -> None:
    """MQTT live pushes must not starve the coordinator's HTTP polling timer."""
    src = _read("coordinator.py")
    helper = re.search(
        r"def _push_partial_update\(.*?(?=\n    # --)",
        src,
        re.S,
    )
    assert helper is not None
    body = helper.group(0)
    assert "self.data = new_data" in body
    assert "self.last_update_success = True" in body
    assert "self.async_update_listeners()" in body
    assert "async_set_updated_data" not in body
    assert "async_set_updated_data" not in src


def test_optional_background_jobs_are_not_setup_tracked() -> None:
    """
    Ensure optional long-running background jobs are scheduled without blocking Home Assistant setup tracking.
    
    Asserts that the coordinator schedules the following optional jobs using `async_create_background_task` (and not `async_create_task`):
    - statistics import scheduler
    - MQTT poll queries scheduler
    - battery pack OTA enrichment scheduler
    """
    src = _read("coordinator.py")

    schedule_import = re.search(
        r"def _schedule_statistics_import\(.*?(?=\n    async def )",
        src,
        re.S,
    )
    assert schedule_import is not None
    assert "async_create_background_task(" in schedule_import.group(0)
    assert "async_create_task(" not in schedule_import.group(0)

    schedule_mqtt = re.search(
        r"def _schedule_mqtt_poll_queries\(.*?(?=\n    def )",
        src,
        re.S,
    )
    assert schedule_mqtt is not None
    assert "async_create_background_task(" in schedule_mqtt.group(0)
    assert "async_create_task(" not in schedule_mqtt.group(0)

    schedule_ota = re.search(
        r"def _schedule_battery_pack_ota_enrichment\(.*?(?=\n    async def )",
        src,
        re.S,
    )
    assert schedule_ota is not None
    assert "async_create_background_task(" in schedule_ota.group(0)
    assert "async_create_task(" not in schedule_ota.group(0)


def test_mqtt_disconnect_reconnect_skips_ha_shutdown_states() -> None:
    """MQTT reconnect callbacks must not run while HA is shutting down."""
    src = _read("coordinator.py")
    match = re.search(
        r"async def _async_handle_mqtt_disconnect\(.*?(?=\n    def )",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)
    guard = body.split("# Reset the throttle window", 1)[0]

    assert "CoreState.final_write" in guard
    assert "CoreState.stopped" in guard
    assert "CoreState.stopping" in guard
    assert "await self._async_ensure_mqtt(" not in guard


def test_mqtt_ensure_uses_stable_client_handle_across_awaits() -> None:
    """
    Ensure the coordinator preserves a stable MQTT client reference across awaits to avoid NoneType errors during reload or shutdown.
    
    Asserts that the `_async_ensure_mqtt` implementation captures `self._mqtt` into a local `mqtt` variable, checks for replacement (`if self._mqtt is not mqtt:`), awaits lifecycle calls on the local handle (`async_start`, `async_wait_until_connected`), reads diagnostics via `mqtt.diagnostics.get`, and does not call `self._mqtt.async_wait_until_connected` directly.
    """
    src = _read("coordinator.py")
    match = re.search(
        r"async def _async_ensure_mqtt\(.*?(?=\n    async def )",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)

    assert "mqtt = self._mqtt" in body
    assert "if self._mqtt is not mqtt:" in body
    assert "await mqtt.async_start(" in body
    assert "await mqtt.async_wait_until_connected(" in body
    assert "mqtt_last_error = mqtt.diagnostics.get" in body
    assert "self._mqtt.async_wait_until_connected" not in body
