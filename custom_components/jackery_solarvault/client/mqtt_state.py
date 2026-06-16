"""Cloud MQTT connection state manager for Jackery SolarVault.

Encapsulates backoff, pause, auth-failure detection, and reconnect
throttling so the coordinator never touches MQTT protocol state directly.
"""

import logging
import time
from typing import TYPE_CHECKING

from custom_components.jackery_solarvault.const import (
    MQTT_APP_CONFLICT_PAUSE_SEC,
    MQTT_RECONNECT_THROTTLE_SEC,
)

if TYPE_CHECKING:
    from .mqtt_push import JackeryMqttPushClient

_LOGGER = logging.getLogger(__name__)

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
    """Determine whether the given message indicates a broker-side MQTT credential.

    rejection.

    Parameters:
        message (object): An object convertible to text (e.g., an exception or log
        string) to be inspected for known broker rejection patterns.

    Returns:
        True if the text of `message` matches known broker credential-rejection
        indicators, False otherwise.
    """
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
    """Detect whether an MQTT connection failure message indicates a transient network.

    or server issue.

    Checks the message text for known transient indicators such as "connect rc=133",
    "server unavailable",
    "connection refused", "connection timed out", or the word "unknown".

    Returns:
        `true` if the message indicates a transient connect failure, `false` otherwise.
    """
    text = str(message or "").lower()
    return (
        "connect rc=133" in text
        or "server unavailable" in text
        or "connection refused" in text
        or "connection timed out" in text
        or "unknown" in text
    )


def mqtt_connect_failure_signature(message: object) -> str:
    """Create a short, stable signature string from an MQTT/setup error message for.

    backoff deduplication.

    Parameters:
        message (object): Error text or object convertible to string describing the
        connect/setup failure.

    Returns:
        signature (str): A normalized signature:
            - "tls_missing_authority_key_identifier" for messages containing "Missing
            Authority Key Identifier".
            - "tls_certificate_verify_failed" for messages containing
            "CERTIFICATE_VERIFY_FAILED".
            - The first 160 characters of the message for messages starting with "MQTT
            not connected yet" or any other message.
            - "unknown" if the message is empty or falsy.
    """
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
        """Initialize MQTT connection state tracker.

        Tracks connection fingerprint, reconnect/backoff timers and steps, auth-failure
        state, pause windows for app-conflict handling, and a flag for whether a
        generated MAC warning was logged.

        Attributes:
            fingerprint (tuple[str|None, str|None, str|None] | None): Last known
            connection fingerprint (client_id, host, session or similar).
            generated_mac_warning_logged (bool): Whether a generated MAC warning has
            been logged.
            last_connect_attempt (float): Monotonic timestamp of the last connect
            attempt.
            auth_failure_message (str | None): Deferred background auth-failure message
            awaiting reauth handling.
            paused_until_monotonic (float): Monotonic timestamp until which reconnects
            are paused due to auth/app-conflict.
            app_conflict_pause_cycles (int): Number of auth-triggered pause cycles
            applied.
            backoff_until_monotonic (float): Monotonic timestamp until which reconnect
            attempts are backed off.
            backoff_step (int): Current index in the backoff sequence (-1 means
            cleared).
            backoff_signature (str | None): Normalized signature of the last failure
            used to deduplicate/backoff progression.
        """
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
        """Return the number of seconds remaining in the Cloud-MQTT reconnect backoff.

        window.

        Returns:
            int: Seconds remaining until backoff expires, or 0 if no backoff is active.
        """
        return max(0, int(self.backoff_until_monotonic - time.monotonic()))

    def note_connect_failure(self, message: object) -> None:
        """Enter or extend the Cloud-MQTT reconnect backoff window after a setup or.

        connect failure.

        Selects a transient or permanent backoff sequence based on the provided error
        message, advances the backoff step when the failure signature repeats (or
        resets it when the signature changes), sets the next backoff expiry (monotonic
        timestamp), and logs the resulting pause duration and failure signature.

        Parameters:
            message (object): Error or diagnostic text/object used to derive a
            normalized failure signature and to classify the failure as transient or
            permanent.
        """
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
        """Pause MQTT reconnect attempts for a fixed app-conflict window after the.

        broker rejects credentials.

        This sets a pause window during which reconnect attempts are suppressed (HTTP
        polling is expected to remain active) and increments the internal app-conflict
        pause cycle counter. If a pause is already active, this call does nothing.

        Parameters:
            message (object): The broker rejection message or diagnostic text used for
            logging.
            streak (int | None): Consecutive authentication failure count, if known;
            used only for logging.
        """
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
        """Decides whether a reconnect attempt should be skipped.

        Performs checks for a matching connection fingerprint, an app-conflict pause
        window, active backoff, and a short reconnect throttle; a matching
        started-and-connected client or any active pause/backoff/throttle causes the
        reconnect to be skipped unless overridden.

        Parameters:
            mqtt: The MQTT client instance (or None) used to determine
            started/connected state.
            current_fingerprint: The fingerprint tuple for the currently available
            connection; compared to the manager's stored fingerprint to detect changes.
            force: If True, bypasses the fast-path fingerprint match and the throttle
            check.

        Returns:
            True if the coordinator should not attempt a reconnect now, False otherwise.
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
        """Record the monotonic timestamp of the most recent MQTT connection attempt.

        Updates the manager's last_connect_attempt to the current monotonic time.
        """
        self.last_connect_attempt = time.monotonic()

    def record_connect_success(
        self,
        mqtt: JackeryMqttPushClient | None,
        current_fingerprint: tuple[str | None, str | None, str | None] | None,
    ) -> None:
        """Record a successful MQTT connection by updating the stored fingerprint and.

        clearing any connect backoff.

        Parameters:
            mqtt (JackeryMqttPushClient | None): The MQTT client that succeeded; if
            None, no state is changed.
            current_fingerprint (tuple[str | None, str | None, str | None] | None):
            Fingerprint tuple to store as the last successful connection.
        """
        if mqtt is not None:
            self.fingerprint = current_fingerprint
            self.clear_connect_backoff()

    def handle_connect_error(
        self,
        mqtt: JackeryMqttPushClient | None,
        error: object,
    ) -> None:
        """Classify a connection error and trigger either an auth-failure pause or a.

        connect backoff.

        If the provided MQTT client exposes a stored "last_error" in its diagnostics,
        that value is preferred for classification. If the error (or last_error)
        indicates broker-side credential rejection, schedule an app-conflict pause via
        pause_after_auth_failure and pass the client's consecutive_auth_failures as the
        streak. Otherwise, record the failure for backoff using note_connect_failure.
        If `mqtt` is None, no action is taken.

        Parameters:
            mqtt: MQTT client whose diagnostics and consecutive_auth_failures are
            consulted; may be None.
            error: The error object or message to classify (used when diagnostics do
            not provide a last_error).
        """
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
        """Handle a background MQTT authentication failure by pausing reconnects or.

        deferring reauthentication.

        If the message indicates broker-side credential rejection, start an
        app-conflict pause window (using the client's consecutive auth-failure streak
        when available). Otherwise store the failure message so a reauthentication will
        be triggered on the next refresh and emit a warning.

        Parameters:
            mqtt (JackeryMqttPushClient | None): The MQTT client instance, or None if
            unavailable.
            message (str): The authentication failure message text to inspect and store.
        """
        if "MQTT broker rejected credentials" in message or is_mqtt_auth_failure(
            message,
        ):
            streak = mqtt.consecutive_auth_failures if mqtt else None
            self.pause_after_auth_failure(message, streak=streak)
            return
        self.auth_failure_message = message
        _LOGGER.warning(
            "Jackery credentials rejected in a background task; "
            "Home Assistant reauth will be triggered on next refresh",
        )
