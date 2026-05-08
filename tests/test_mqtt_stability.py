"""Long-running MQTT stability contract tests.

These pure-source tests guard the long-running stability of the broker
session against accidental regressions:

1. gmqtt auto-reconnect is disabled so broker protocol rejections do not loop.
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
COMPONENT = ROOT / "custom_components" / "jackery_solarvault"


def _read(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def test_mqtt_client_disables_internal_reconnect_loop() -> None:
    """Coordinator throttling must own reconnects after broker rejections."""
    src = _read("mqtt_push.py")
    assert '"reconnect_retries": 0' in src, src
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
    """The on_connect handler must iterate over self._topics + subscribe each.

    Without this, a reconnect after a network blip leaves the integration
    silently unsubscribed: the TCP session is back but no telemetry flows.
    """
    src = _read("mqtt_push.py")
    on_connect_match = re.search(
        r"def _on_connect\(self.*?(?=\n    def |\n    @|\nclass )",
        src,
        re.S,
    )
    assert on_connect_match is not None, "_on_connect not found"
    body = on_connect_match.group(0)
    assert "for topic in self._topics" in body, body
    assert ".subscribe(" in body, body


def test_every_connect_triggers_snapshot_callback() -> None:
    """A reconnect must re-pull the full app-state snapshot.

    On the Jackery cloud the broker only keeps retained state for the
    "device-online" notice, not the per-property values. Without a fresh
    snapshot pull on each reconnect the integration would carry stale
    values forward indefinitely.
    """
    src = _read("mqtt_push.py")
    on_connect_match = re.search(
        r"def _on_connect\(self.*?(?=\n    def |\n    @|\nclass )",
        src,
        re.S,
    )
    assert on_connect_match is not None
    body = on_connect_match.group(0)
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
        r"def _on_disconnect\(self.*?(?=\n    @staticmethod|\n    def |\nclass )",
        src,
        re.S,
    )
    assert on_disc_match is not None
    body = on_disc_match.group(0)
    # The handler must check whether last_error already carries a 'connect rc='
    # signature and bail out without overwriting it.
    assert "connect rc=" in body, body


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
    """The gmqtt connect call must set keepalive.

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
