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
    """Read and return the UTF-8 text of a source file from the appropriate component directory.

    Parameters:
        name (str): Filename to read; "mqtt_push.py" is read from the client component, all other names are read from the integration component.

    Returns:
        str: File contents decoded as UTF-8.
    """
    base = CLIENT_COMPONENT if name == "mqtt_push.py" else INTEGRATION_COMPONENT
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
    """Ensure the MQTT client's credential-change detection does not retain raw password data.

    Verifies the module delegates to the shared length-delimited credential
    fingerprint helper, stores only the digest, and never stores a tuple of raw
    credentials.
    """
    src = _read("mqtt_push.py")
    assert "from .credentials import credential_fingerprint" in src, src
    assert "self._fingerprint: str | None = None" in src, src
    assert "def _credential_fingerprint(" in src, src
    assert "return credential_fingerprint({" in src, src
    assert "fingerprint = self._credential_fingerprint(" in src, src
    assert "fingerprint = (client_id, username, password)" not in src, src


def test_connack_reason_preserved_across_post_reject_disconnect() -> None:
    """Ensure the broker CONNACK failure reason is preserved when the broker rejects the connection and closes the socket.

    Asserts that the disconnect handler does not overwrite an actionable CONNACK reason (the `"connect rc=..."` signature) and that the connect-failure mapper exposes broker CONNACK reasons via `MQTT_CONNACK_REASONS` and formats them as `f"connect rc={rc}"`.
    """
    src = _read("mqtt_push.py")
    on_disc_match = re.search(
        r"def _handle_disconnect_error\(self.*?(?=\n    @staticmethod|\n    def |\nclass )",
        src,
        re.DOTALL,
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
        re.DOTALL,
    )
    assert fail_match is not None, "_handle_connect_failure not found"
    fail_body = fail_match.group(0)
    assert "MQTT_CONNACK_REASONS" in fail_body, fail_body
    assert 'f"connect rc={rc}' in fail_body, fail_body


def test_aiomqtt_logger_stays_visible_under_home_assistant_logging() -> None:
    """The integration must not hide aiomqtt diagnostics from HA logging."""
    src = _read("mqtt_push.py")

    assert '_AIOMQTT_LOGGER = logging.getLogger(f"{__name__}.aiomqtt")' in src
    assert "_AioMqttPassiveDisconnectFilter" not in src
    assert "_AIOMQTT_LOGGER.addFilter(" not in src
    assert "_AIOMQTT_LOGGER.setLevel(" not in src


def test_diagnostics_exposes_stale_subscription_signals() -> None:
    """Assert that the diagnostics_snapshot method exposes the `seconds_since_last_message` value and the `mqtt_silent_for_too_long` flag.

    Raises:
        AssertionError: If the diagnostics_snapshot method is missing or either key/flag is not present.
    """
    src = _read("mqtt_push.py")
    diag_match = re.search(
        r"def diagnostics_snapshot\(self.*?(?=\n    @property\n    def diagnostics|\nclass )",
        src,
        re.DOTALL,
    )
    assert diag_match is not None, "diagnostics_snapshot method not found"
    body = diag_match.group(0)
    assert "seconds_since_last_message" in body, body
    assert "mqtt_silent_for_too_long" in body, body


def test_getter_response_correlation_is_bounded_and_diagnostic() -> None:
    """Getter waiters are bounded session state and cannot replace ingest."""
    src = _read("mqtt_push.py")

    assert "_MAX_PENDING_RESPONSES" in src
    assert "_MQTT_RESPONSE_TIMEOUT_SEC" in src
    assert "_resolve_pending_response(data)" in src
    assert "self._message_callback(topic, data)" in src
    assert '"pending_responses"' in src
    assert '"responses_correlated"' in src
    assert '"responses_expired"' in src


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
        re.DOTALL,
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
        re.DOTALL,
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


def test_optional_background_jobs_are_not_setup_tracked() -> None:
    """Ensure optional long-running background jobs are scheduled without blocking Home Assistant setup tracking.

    Asserts that the coordinator schedules the following optional jobs using `async_create_background_task` (and not `async_create_task`):
    - statistics import scheduler
    - MQTT poll queries scheduler
    - battery pack OTA enrichment scheduler
    """
    src = _read("coordinator.py")

    schedule_import = re.search(
        r"def _schedule_statistics_import\(.*?(?=\n    async def )",
        src,
        re.DOTALL,
    )
    assert schedule_import is not None
    assert "async_create_background_task(" in schedule_import.group(0)
    assert "async_create_task(" not in schedule_import.group(0)

    schedule_mqtt = re.search(
        r"def _schedule_mqtt_poll_queries\(.*?(?=\n    def )",
        src,
        re.DOTALL,
    )
    assert schedule_mqtt is not None
    assert "async_create_background_task(" in schedule_mqtt.group(0)
    assert "async_create_task(" not in schedule_mqtt.group(0)

    schedule_ota = re.search(
        r"def _schedule_battery_pack_ota_enrichment\(.*?(?=\n    async def )",
        src,
        re.DOTALL,
    )
    assert schedule_ota is not None
    assert "async_create_background_task(" in schedule_ota.group(0)
    assert "async_create_task(" not in schedule_ota.group(0)


def test_mqtt_ensure_uses_stable_client_handle_across_awaits() -> None:
    """Ensure the coordinator preserves a stable MQTT client reference across awaits to avoid NoneType errors during reload or shutdown.

    Asserts that the `_async_ensure_mqtt` implementation captures `self._mqtt` into a local `mqtt` variable, checks for replacement (`if self._mqtt is not mqtt:`), awaits lifecycle calls on the local handle (`async_start`, `async_wait_until_connected`), reads diagnostics via `mqtt.diagnostics.get`, and does not call `self._mqtt.async_wait_until_connected` directly.
    """
    src = _read("coordinator.py")
    match = re.search(
        r"async def _async_ensure_mqtt\(.*?(?=\n    async def )",
        src,
        re.DOTALL,
    )
    assert match is not None
    body = match.group(0)

    assert "mqtt = self._mqtt" in body
    assert "if self._mqtt is not mqtt:" in body
    assert "await mqtt.async_start(" in body
    assert "await mqtt.async_wait_until_connected(" in body
    assert "mqtt_last_error = mqtt.diagnostics.get" in body
    assert "self._mqtt.async_wait_until_connected" not in body
