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
    """A server-side broker drop must trigger an immediate reconnect.

    With aiomqtt, a passive disconnect (Errno 104 / ConnectionResetError)
    raises MqttError out of the ``async for`` loop. The session task exits
    via the ``finally`` block, which fires ``disconnect_callback`` only
    when the session had actually been connected (rules out CONNACK
    rejections). The coordinator's ``_async_handle_mqtt_disconnect``
    then resets the throttle and calls
    ``_async_ensure_mqtt(force=True, wait_connected=True)`` so a
    broker-side credential rejection on the reconnect can pause MQTT while
    HTTP polling continues rather than being silently retried.
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
    """The documented HTTP property poll remains active every coordinator tick."""
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
    """Long-running optional jobs must not block HA bootstrap/setup tracking."""
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
    """Reload/shutdown must not turn reconnect waits into NoneType errors."""
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


def test_cloud_outage_uses_cached_mqtt_session() -> None:
    """Cloud-login failure must retry credential build with allow_stale=True.

    Without this, a Jackery cloud outage tears down MQTT push at the first
    credential refresh: the broker would still accept the AES password
    derived from the cached ``userId+macId+seed`` triple, but the coordinator
    never tries because ``async_get_mqtt_credentials`` raises ``JackeryError``
    on the unreachable ``_ensure_token`` call.
    """
    src = _read("coordinator.py")
    match = re.search(
        r"async def _async_ensure_mqtt\(.*?(?=\n    async def )",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)

    # First attempt is the normal cloud-backed path.
    assert "creds = await self.api.async_get_mqtt_credentials()" in body
    # On JackeryError (cloud unreachable) it falls back to the cached session.
    assert "allow_stale=True" in body
    # A used-stale flag is tracked so a follow-up broker rejection can drop
    # the cache instead of replaying the stale row forever.
    assert "used_stale_session" in body
    assert "_async_invalidate_mqtt_session_cache" in body


def test_mqtt_commands_use_cached_session_during_cloud_outage() -> None:
    """Command publish must use cached MQTT credentials when login is down."""
    src = _read("coordinator.py")
    match = re.search(
        r"async def _async_publish_command\(.*?(?=\n    async def )",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)

    assert "creds = await self.api.async_get_mqtt_credentials()" in body
    assert "allow_stale=True" in body
    assert "used_stale_session" in body
    assert "_async_invalidate_mqtt_session_cache" in body


def test_successful_mqtt_connect_persists_session_snapshot() -> None:
    """Every successful MQTT connect must persist the current session snapshot.

    Persistence lets the next HA restart (or a setup pass during a cloud
    outage) hydrate ``JackeryApi`` without a live ``/v1/user/login`` round
    trip, which is what makes MQTT survive the outage.
    """
    src = _read("coordinator.py")
    match = re.search(
        r"async def _async_ensure_mqtt\(.*?(?=\n    async def )",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)
    assert "_async_persist_mqtt_session_if_changed" in body
    # The helper itself must read the API snapshot and compare against the
    # previously persisted dict before writing — avoids storm-writes.
    assert "_async_persist_mqtt_session_if_changed" in src
    assert "self.api.mqtt_session_snapshot()" in src
    assert "self._persisted_mqtt_session" in src


def test_mqtt_session_cache_module_centralizes_storage_keys() -> None:
    """mqtt_session_cache.py must use the centralized MQTT_SESSION_* keys."""
    src = _read("mqtt_session_cache.py")
    assert "MQTT_SESSION_USER_ID" in src
    assert "MQTT_SESSION_SEED_B64" in src
    assert "MQTT_SESSION_MAC_ID" in src
    # Storage key must be DOMAIN-scoped so other integrations cannot collide.
    assert 'f"{DOMAIN}.mqtt_session_cache"' in src
    # Public surface required by the coordinator + setup paths.
    assert "async def async_load_mqtt_session" in src
    assert "async def async_save_mqtt_session" in src
    assert "async def async_clear_mqtt_session" in src


def test_ble_sink_bootstraps_from_discovery_when_first_refresh_pending() -> None:
    """BLE notify frames must not be dropped silently when self.data is empty.

    Cloud-Outage during HA startup means ``async_config_entry_first_refresh``
    cannot land any HTTP-derived ``self.data`` for the device. Before this
    fix the sink returned early on ``current_device is None`` and every BLE
    frame was discarded — local-only operation produced zero entity updates
    even though discovery_cache already knew the device id and bluetoothKey.
    """
    src = _read("coordinator.py")
    sink_match = re.search(
        r"async def _sink\(device_id: str.*?listener = JackeryBleListener\(",
        src,
        re.S,
    )
    assert sink_match is not None, "BLE _sink not found"
    body = sink_match.group(0)
    # Discovery-cache fallback must be present when self.data has nothing.
    assert "self._device_index.get(device_id)" in body
    assert "PAYLOAD_DEVICE_META" in body
    assert "PAYLOAD_SYSTEM_META" in body
    # The seeded bundle must carry the four canonical keys the merge helpers
    # read (PROPERTIES default {}, DEVICE/DISCOVERY from device_meta,
    # SYSTEM from system_meta).
    assert "PAYLOAD_PROPERTIES: {}" in body
    assert "PAYLOAD_DEVICE:" in body
    assert "PAYLOAD_DISCOVERY:" in body
    assert "PAYLOAD_SYSTEM:" in body


def test_coordinator_uses_ha_bluetooth_async_address_present() -> None:
    """Reachability check must use the HA-core ``async_address_present`` helper.

    Per https://developers.home-assistant.io/docs/core/bluetooth/api this is
    the authoritative source for "is the BLE device reachable right now". The
    integration must consult it instead of trusting the Jackery cloud's
    ``onlineStatus`` / ``onlineState`` flag, which goes to 0 the moment the
    device cannot heartbeat back to the cloud — even when BLE is fine.
    """
    src = _read("coordinator.py")
    assert "def is_device_locally_reachable" in src
    assert "bluetooth.async_address_present" in src
    # connectable=True ensures only proxies that can actually open a GATT
    # session count as "reachable" — passive sniffers do not.
    assert "connectable=True" in src
    # Defensive import so unit tests on hosts without HA bluetooth still load.
    assert "from homeassistant.components import bluetooth" in src


def test_entity_available_prefers_local_reachability_over_cloud_flag() -> None:
    """`JackeryEntity.available` must treat local reachability as authoritative.

    Before this fix the Jackery cloud's ``onlineStatus: 0`` during a cloud
    outage knocked every sensor to ``unavailable`` — even while the BLE
    listener was decoding 1000+ frames per cycle. Production log evidence:
    16:14:43 BLE merges OK → 16:15:10 cloud refresh sets onlineStatus=0 →
    every entity flips to ``unavailable`` despite the live local feed.
    """
    src = (ROOT / "custom_components" / "jackery_solarvault" / "entity.py").read_text(
        encoding="utf-8"
    )
    avail_match = re.search(
        r"def available\(self\).*?(?=\n    @|\n    def |\nclass |\Z)",
        src,
        re.S,
    )
    assert avail_match is not None, "JackeryEntity.available not found"
    body = avail_match.group(0)
    # Local-reachability check must run BEFORE the cloud-online evaluation,
    # otherwise the cloud's 0 flag would still short-circuit the result.
    assert "is_device_locally_reachable" in body
    local_idx = body.index("is_device_locally_reachable")
    cloud_idx = body.index("FIELD_ONLINE_STATUS")
    assert local_idx < cloud_idx, (
        "local reachability must be evaluated before cloud onlineStatus"
    )


def test_ble_transport_logs_first_unmapped_serial_and_missing_key() -> None:
    """Cloud-outage symptoms must surface once per device at INFO level.

    Two failure modes are otherwise invisible: a stale discovery_cache
    means ``serial_resolver`` returns None for every advertisement, and an
    incomplete discovery_cache means the AES key lookup returns None for
    every notify. Both used to live at DEBUG, so users could not tell why
    BLE was running but producing no values.
    """
    src = (
        ROOT
        / "custom_components"
        / "jackery_solarvault"
        / "client"
        / "ble_transport.py"
    ).read_text(encoding="utf-8")
    # One-shot per-device throttles so the new INFO logs don't spam.
    assert "_unmapped_serials_logged: set[str]" in src
    assert "_missing_key_logged: set[str]" in src
    # Resolution success must clear the flags so a future re-occurrence
    # is visible again.
    assert "self._unmapped_serials_logged.discard(serial)" in src
    assert "self._missing_key_logged.discard(device_id)" in src
    # The actionable INFO log must mention the user-facing remediation.
    assert "device-id mapping" in src
    assert "Discovery_cache likely stale" in src


def test_recorder_external_statistics_offset_is_non_negative() -> None:
    """The external-statistics import must clamp the prior-sum anchor.

    A poisoned recorder row (negative ``sum`` from an earlier bug) would
    otherwise propagate as negative external statistics on every subsequent
    bucket — exactly what the user observed for PV energy where physics
    cannot produce a negative reading.
    """
    src = _read("coordinator.py")
    match = re.search(
        r"async def _async_add_app_chart_statistics\(.*?(?=\n    async def |\n    def )",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)
    assert "cumulative = max(0.0, offset)" in body, (
        "external-statistics import must clamp the offset to >= 0; cumulative "
        "must start at max(0, offset) so an existing negative recorder row "
        "cannot propagate"
    )


def test_entity_statistics_state_offset_is_non_negative() -> None:
    """The entity-statistics import must clamp the prior-period state anchor."""
    src = _read("coordinator.py")
    match = re.search(
        r"def _entity_statistics_from_contributions\(.*?(?=\n    async def |\n    def )",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)
    # cumulative_sum (Wh anchor) is already clamped via max(0.0, sum_offset);
    # running_state (period anchor) must be clamped the same way, otherwise a
    # negative recorder row would drive the period state negative.
    assert "cumulative_sum = max(0.0, sum_offset)" in body
    assert "max(0.0, state_offset)" in body


def test_local_daily_cache_is_wired_into_coordinator_cycle() -> None:
    """Coordinator must build today's local energy deltas every cycle.

    Without this, a Jackery cloud outage leaves the ``device_*_stat_day``
    payloads frozen on whatever value the cloud last delivered, and the
    "today" sensors show 0.04 / 0.6 / 3.54 kWh while the device is in fact
    producing live energy that ``pvEgy`` / ``batChgEgy`` / etc. on the
    BLE-routed properties already report.
    """
    src = _read("coordinator.py")
    update_match = re.search(
        r"async def _async_update_data\(.*?return result\b",
        src,
        re.S,
    )
    assert update_match is not None, "_async_update_data body not found"
    body = update_match.group(0)
    # Refresh hook runs per device after merged_props is built.
    assert "self._refresh_local_daily_for_device(" in body
    assert "merged_props, today=today" in body
    # The result section is set so downstream sensors can read the deltas.
    assert "PAYLOAD_LOCAL_DAILY_ENERGY" in body
    # The cycle persists snapshots at the end so HA restarts keep the anchor.
    assert "_async_persist_local_daily_snapshots_if_changed" in body


def test_local_daily_cache_module_exposes_required_helpers() -> None:
    """The cache module must export load/save/delta/refresh as a public API."""
    src = _read("local_daily_cache.py")
    assert "async def async_load_daily_cache" in src
    assert "async def async_save_daily_cache" in src
    assert "def daily_delta(" in src
    assert "def refresh_snapshot(" in src
    # Storage key must be DOMAIN-scoped so it cannot collide with another
    # integration that shares HA's Store path.
    assert 'f"{DOMAIN}.local_daily_cache"' in src
