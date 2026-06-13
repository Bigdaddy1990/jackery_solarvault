"""Cloud MQTT connection state manager for Jackery SolarVault.

Encapsulates backoff, pause, auth-failure detection, and reconnect
throttling so the coordinator never touches MQTT protocol state directly.
"""

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .mqtt_push import JackeryMqttPushClient

_LOGGER = logging.getLogger(__name__)

MQTT_RECONNECT_THROTTLE_SEC = 5
MQTT_APP_CONFLICT_PAUSE_SEC = 300

# Permanent failures (auth / protocol) — long backoff
MQTT_CONNECT_BACKOFF_STEPS_SEC: tuple[int, ...] = (
    300,
    900,
    3600,
    21600,
)

# Transient failures (server unavailable, network hiccup) — short backoff
MQTT_TRANSIENT_BACKOFF_STEPS_SEC: tuple[int, ...] = (
    30,
    60,
    120,
    300,
)


def is_mqtt_auth_failure(message: object) -> bool:
    """Return True for broker-side MQTT credential rejection."""
    text = str(message or "").lower()
    return (
        "connect rc=4" in text
        or "connect rc=5" in text
        or "connect rc=134" in text
        or "connect rc=135" in text
        or "code 134" in text
        or "code 135" in text
        or "bad user name or password" in text
        or "not authorized" in text
    )


def is_transient_connect_failure(message: object) -> bool:
    """Return True for transient MQTT connect failures (server unavailable, network)."""
    text = str(message or "").lower()
    return (
        "connect rc=133" in text
        or "server unavailable" in text
        or "connection refused" in text
        or "connection timed out" in text
        or "unknown" in text
    )


def mqtt_connect_failure_signature(message: object) -> str:
    """Normalize MQTT setup errors for deduplicated backoff logging."""
    text = str(message or "").strip() or "unknown"
    if "Missing Authority Key Identifier" in text:
        return "tls_missing_authority_key_identifier"
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return "tls_certificate_verify_failed"
    if text.startswith("MQTT not connected yet"):
        return text[:160]
    return text[:160]


class MqttConnectionManager:
    """Cloud MQTT connection state: backoff, pause, throttle, auth.

    The coordinator creates one instance and delegates all MQTT
    connection lifecycle decisions here.  The manager never owns the
    MQTT client itself — it only tracks state and answers *"should we
    try to connect / reconnect?"* questions.
    """

    def __init__(self) -> None:
        """Initialise MQTT connection state tracker."""
        self.fingerprint: tuple[str | None, str | None, str | None] | None = None
        self.generated_mac_warning_logged = False
        self.last_connect_attempt: float = 0.0
        self.auth_failure_message: str | None = None
        self.paused_until_monotonic: float = 0.0
        self.app_conflict_pause_cycles: int = 0
        self.backoff_until_monotonic: float = 0.0
        self.backoff_step: int = -1
        self.backoff_signature: str | None = None

    # ------------------------------------------------------------------
    # Backoff helpers
    # ------------------------------------------------------------------

    def backoff_remaining(self) -> int:
        """Return remaining Cloud-MQTT connect backoff seconds."""
        return max(0, int(self.backoff_until_monotonic - time.monotonic()))

    def note_connect_failure(self, message: object) -> None:
        """Enter or extend Cloud-MQTT backoff after a setup/connect failure."""
        signature = mqtt_connect_failure_signature(message)
        transient = is_transient_connect_failure(message)
        backoff_steps = (
            MQTT_TRANSIENT_BACKOFF_STEPS_SEC
            if transient
            else MQTT_CONNECT_BACKOFF_STEPS_SEC
        )
        if signature == self.backoff_signature:
            self.backoff_step = min(
                self.backoff_step + 1,
                len(backoff_steps) - 1,
            )
        else:
            self.backoff_signature = signature
            self.backoff_step = 0
        delay = backoff_steps[self.backoff_step]
        self.backoff_until_monotonic = time.monotonic() + delay
        _LOGGER.info(
            "Jackery MQTT paused for %ds after %s connect failure (%s); "
            "HTTP, BLE and local MQTT remain active",
            delay,
            "transient" if transient else "permanent",
            signature,
        )

    def clear_connect_backoff(self) -> None:
        """Clear Cloud-MQTT connect backoff after a successful broker session."""
        if self.backoff_signature is not None:
            _LOGGER.debug(
                "Jackery MQTT connect backoff recovered after %s",
                self.backoff_signature,
            )
        self.backoff_until_monotonic = 0.0
        self.backoff_step = -1
        self.backoff_signature = None

    def pause_after_auth_failure(
        self,
        message: object,
        *,
        streak: int | None = None,
    ) -> None:
        """Pause MQTT after a broker auth rejection while HTTP keeps polling."""
        now = time.monotonic()
        if self.paused_until_monotonic > now:
            return
        self.app_conflict_pause_cycles += 1
        self.paused_until_monotonic = now + MQTT_APP_CONFLICT_PAUSE_SEC
        _LOGGER.info(
            "Jackery MQTT paused for %ds after broker credential rejection "
            "(streak %s, pause cycle %d: %s); HTTP polling remains active",
            MQTT_APP_CONFLICT_PAUSE_SEC,
            streak if streak is not None else "unknown",
            self.app_conflict_pause_cycles,
            message,
        )

    # ------------------------------------------------------------------
    # Reconnect decision helpers
    # ------------------------------------------------------------------

    def should_skip_reconnect(
        self,
        mqtt: JackeryMqttPushClient | None,
        current_fingerprint: tuple[str | None, str | None, str | None] | None,
        *,
        force: bool = False,
    ) -> bool:
        """Return True if the coordinator should NOT attempt a reconnect now.

        Checks pause windows, backoff timers, throttle limits, and
        fingerprint changes to decide whether a reconnect is appropriate.
        """
        if mqtt is None:
            return True

        now = time.monotonic()

        # Fast path: already connected with matching fingerprint, no force.
        if (
            not force
            and mqtt.is_started
            and self.fingerprint is not None
            and self.fingerprint == current_fingerprint
            and mqtt.is_connected
        ):
            if self.app_conflict_pause_cycles or self.paused_until_monotonic:
                self.app_conflict_pause_cycles = 0
                self.paused_until_monotonic = 0.0
                self.clear_connect_backoff()
            return True

        # App-conflict pause
        if self.paused_until_monotonic > now:
            _LOGGER.debug(
                "Jackery MQTT: paused until app-conflict window clears "
                "(%.0fs remaining, cycle %d)",
                self.paused_until_monotonic - now,
                self.app_conflict_pause_cycles,
            )
            return True

        # Backoff
        backoff = self.backoff_remaining()
        if backoff > 0:
            _LOGGER.debug(
                "Jackery MQTT: connect retry is backed off for %ss after %s",
                backoff,
                self.backoff_signature,
            )
            return True

        # Throttle
        if (
            not force  # noqa: PLR0916
            and mqtt.is_started
            and (
                (
                    self.fingerprint is not None
                    and self.fingerprint != current_fingerprint
                )
                or not mqtt.is_connected
            )
            and (now - self.last_connect_attempt) < MQTT_RECONNECT_THROTTLE_SEC
        ):
            _LOGGER.debug(
                "Jackery MQTT: reconnect is throttled (%.1fs/%ss)",
                now - self.last_connect_attempt,
                MQTT_RECONNECT_THROTTLE_SEC,
            )
            return True

        return False

    def record_connect_attempt(self) -> None:
        """Stamp the last connect attempt timestamp."""
        self.last_connect_attempt = time.monotonic()

    def record_connect_success(
        self,
        mqtt: JackeryMqttPushClient | None,
        current_fingerprint: tuple[str | None, str | None, str | None] | None,
    ) -> None:
        """Update fingerprint and clear backoff after a successful session."""
        if mqtt is not None:
            self.fingerprint = current_fingerprint
            self.clear_connect_backoff()

    def handle_connect_error(
        self,
        mqtt: JackeryMqttPushClient | None,
        error: object,
    ) -> None:
        """Classify a connect error as auth-failure or transient and act."""
        if mqtt is None:
            return
        last_error = mqtt.diagnostics.get("last_error")
        if is_mqtt_auth_failure(error) or is_mqtt_auth_failure(last_error):
            streak = mqtt.consecutive_auth_failures
            self.pause_after_auth_failure(last_error or error, streak=streak)
        else:
            self.note_connect_failure(last_error or error)

    def defer_background_auth_failure(
        self,
        mqtt: JackeryMqttPushClient | None,
        message: str,
    ) -> None:
        """Route a background auth failure to either MQTT pause or reauth."""
        if "MQTT broker rejected credentials" in message or is_mqtt_auth_failure(
            message
        ):
            streak = mqtt.consecutive_auth_failures if mqtt else None
            self.pause_after_auth_failure(message, streak=streak)
            return
        self.auth_failure_message = message
        _LOGGER.warning(
            "Jackery credentials rejected in a background task; "
            "Home Assistant reauth will be triggered on next refresh"
        )
