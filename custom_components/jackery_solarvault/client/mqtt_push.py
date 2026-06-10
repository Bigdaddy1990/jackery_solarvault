"""Async MQTT push client for Jackery SolarVault cloud broker."""

import asyncio
import contextlib
from datetime import UTC, datetime
import hashlib
import json
import logging
from pathlib import Path
import ssl
from typing import TYPE_CHECKING, Any

import aiomqtt
from aiomqtt import MqttError
from aiomqtt.exceptions import MqttCodeError

from jackery_solarvault.const import (
    FIELD_BODY,
    FIELD_DATA,
    MQTT_AUTH_FAILURE_TOLERANCE,
    MQTT_CLIENT_LIBRARY,
    MQTT_CONNACK_REASONS,
    MQTT_HOST,
    MQTT_KEEPALIVE_SEC,
    MQTT_PORT,
    MQTT_SILENT_THRESHOLD_SEC,
    MQTT_TOPIC_PREFIX,
    MQTT_TOPIC_SUFFIXES,
    REDACTED_VALUE,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiomqtt import Client as MQTTClient

_LOGGER = logging.getLogger(__name__)
_AIOMQTT_LOGGER = logging.getLogger(f"{__name__}.aiomqtt")
# aiomqtt and paho-mqtt log under their own module names. Keep them at WARNING
# so transient connect/disconnect noise stays out of normal HA logs unless the
# user opts in via the integration's own debug logger. The connect-failure
# pathway below differentiates auth rejections (warning) from transient
# refusals (debug) on its own.
logging.getLogger("aiomqtt").setLevel(logging.WARNING)


class _AioMqttPassiveDisconnectFilter(logging.Filter):
    """Hide expected passive broker reset noise from aiomqtt internals."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: PLR6301
        """Suppress aiomqtt log records for known passive socket-reset disconnect messages.

        Checks the log record's message for the substring "failed to receive on socket" combined with platform-specific reset markers.

        Returns:
            `False` if the record matches a known passive disconnect/reset message, `True` otherwise.
        """  # noqa: E501
        message = record.getMessage()
        if "failed to receive on socket" not in message:
            return True
        return not any(
            marker in message
            for marker in (
                "Errno 104",
                "Connection reset by peer",
                "WinError 10054",
            )
        )


_AIOMQTT_LOGGER.addFilter(_AioMqttPassiveDisconnectFilter())


class JackeryMqttPushClient:
    """Async-native MQTT client for Jackery cloud topics in PROTOCOL.md §3."""

    def __init__(
        self,
        hass: Any,  # noqa: ANN401
        message_callback: Callable[[str, dict[str, Any]], Awaitable[None]],
        connect_callback: Callable[[], Awaitable[None]] | None = None,
        disconnect_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize the MQTT push client and set up internal state, locks, and synchronization primitives used to manage a single aiomqtt session.

        Parameters:
            hass: Home Assistant runtime instance used to schedule tasks and access helpers.
            message_callback: Coroutine function called for each received message with signature (topic: str, message: dict).
            connect_callback: Optional coroutine called once after a successful connection.
            disconnect_callback: Optional coroutine called once after a clean disconnect.
        """  # noqa: E501
        self._hass = hass
        self._message_callback = message_callback
        self._connect_callback = connect_callback
        self._disconnect_callback = disconnect_callback
        self._lock = asyncio.Lock()
        self._client: MQTTClient | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._fingerprint: str | None = None
        self._topics: list[str] = []
        self._connected_event = asyncio.Event()
        self._connected = False
        self._messages_seen = 0
        self._messages_dropped = 0
        self._last_error: str | None = None
        self._last_message_error: str | None = None
        self._last_published_topic: str | None = None
        self._last_connect_at: str | None = None
        self._last_disconnect_at: str | None = None
        self._last_message_at: str | None = None
        self._last_publish_at: str | None = None
        self._connect_attempts = 0
        self._last_connect_failure_signature: str | None = None
        # Counter for consecutive CONNACK auth rejections (rc=4/5/134/135).
        # Resets to 0 the moment the broker accepts a session. The coordinator
        # uses this to differentiate a transient token-rotation race (the
        # Jackery cloud rotates credentials when the official app logs in at
        # the same time) from a persistent credential failure that warrants
        # opening Home Assistant's reauth UI.
        self._consecutive_auth_failures = 0
        self._tls_custom_ca_loaded = False
        self._tls_certificate_source = "not_built"

    async def async_start(
        self,
        *,
        client_id: str,
        username: str,
        password: str,
        user_id: str,
    ) -> None:
        """Start or restart the MQTT background runner using the provided credentials.

        If a runner is already running with the same credential fingerprint and the client
        is connected, this call returns without restarting. Otherwise it builds a TLS
        context, configures subscription topics for the given `user_id`, and schedules
        the session runner as a Home Assistant background task. After starting the
        runner, waits up to 12 seconds for an initial connect result or CONNACK failure;
        a timeout during that wait is suppressed.

        Parameters:
            client_id (str): MQTT client identifier.
            username (str): MQTT username.
            password (str): MQTT password.
            user_id (str): User identifier used to construct subscription topics.

        Returns:
            None
        """  # noqa: E501
        fingerprint = self._credential_fingerprint(client_id, username, password)
        async with self._lock:
            if self._runner_task is not None and self._fingerprint == fingerprint:
                if self._connected:
                    return
                _LOGGER.info(
                    "Jackery MQTT: reconnecting async client with unchanged credentials",  # noqa: E501
                )

            await self._async_stop_locked()

            self._topics = [
                f"{MQTT_TOPIC_PREFIX}/{user_id}/{suffix}"
                for suffix in MQTT_TOPIC_SUFFIXES
            ]
            self._connected_event.clear()
            self._connected = False
            self._last_error = None
            self._last_connect_failure_signature = None

            ssl_context = await self._hass.async_add_executor_job(
                self._build_ssl_context_blocking,
            )

            self._fingerprint = fingerprint
            self._connect_attempts += 1
            _LOGGER.info(
                "Jackery MQTT: connecting to %s:%s with aiomqtt (TLS source=%s)",
                MQTT_HOST,
                MQTT_PORT,
                self._tls_certificate_source,
            )
            self._runner_task = self._hass.async_create_background_task(
                self._async_run_session(
                    client_id=client_id,
                    username=username,
                    password=password,
                    ssl_context=ssl_context,
                ),
                name="jackery_mqtt_runner",
            )

        # Best-effort wait so the caller (coordinator) sees connect-success or
        # the first CONNACK rejection in diagnostics within a bounded window
        # without holding the start-lock open. Reconnect-throttling stays the
        # coordinator's job; we only surface the initial outcome here.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._connected_event.wait(), timeout=12.0)

    @staticmethod
    def _credential_fingerprint(client_id: str, username: str, password: str) -> str:
        """Compute a stable, non-secret fingerprint for the given MQTT credentials.

        Returns:
            str: Hex-encoded SHA-256 fingerprint derived from the provided client_id, username, and password.
        """  # noqa: E501
        hasher = hashlib.sha256()
        for value in (client_id, username, password):
            encoded = value.encode()
            hasher.update(len(encoded).to_bytes(4, "big"))
            hasher.update(encoded)
        return hasher.hexdigest()

    async def async_stop(self) -> None:
        """Stop MQTT connection."""
        async with self._lock:
            await self._async_stop_locked()

    async def async_publish_json(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        """Publish a dict payload to the given MQTT topic as compact UTF-8 JSON.

        The payload is serialized with no extra whitespace (separators=(",", ":")) and with non-ASCII characters preserved (ensure_ascii=False). If the client is not currently connected, this call waits up to 12 seconds for a connection to be established. Raises RuntimeError if the MQTT client is not running or if the publish operation fails.

        Parameters:
            topic (str): MQTT topic to publish to.
            payload (dict[str, Any]): Object to serialize and publish.
            qos (int, optional): Quality of Service level for the message. Defaults to 0.
            retain (bool, optional): Whether the message should be retained by the broker. Defaults to False.

        Raises:
            RuntimeError: If the MQTT client is not running.
            RuntimeError: If the publish operation fails.
        """  # noqa: E501
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        if not self._connected:
            await self._async_wait_connected(timeout_sec=12.0)
        client = self._client
        if client is None:
            raise RuntimeError("MQTT client is not running")  # noqa: TRY003
        try:
            await client.publish(topic, text, qos=qos, retain=retain)
        except MqttError as err:
            self._connected = False
            self._connected_event.clear()
            self._last_error = f"publish failed: {err}"
            raise RuntimeError(f"MQTT publish failed: {err}") from err  # noqa: TRY003
        self._last_published_topic = topic
        self._last_publish_at = self._utc_now_iso()

    async def async_wait_until_connected(self, timeout_sec: float = 15.0) -> None:
        """Waits for the MQTT client to become connected, up to the given timeout.

        Parameters:
            timeout_sec (float): Maximum seconds to wait for a connection.

        Raises:
            RuntimeError: If the MQTT client runner is not started.
            RuntimeError: If the client does not become connected within the timeout or a connect failure occurs.
        """  # noqa: E501
        if self._runner_task is None:
            raise RuntimeError("MQTT client is not running")  # noqa: TRY003
        await self._async_wait_connected(timeout_sec=timeout_sec)

    async def _async_wait_connected(self, timeout_sec: float) -> None:
        """Waits up to timeout_sec for the MQTT client to signal a successful connection; raises if the client is not connected.

        If the wait times out and a previous connection error string exists, raises RuntimeError including that error. If the wait times out and no previous error exists, sets self._last_error to "publish timeout waiting for MQTT connect" and raises RuntimeError("MQTT not connected yet"). If the wait completes but the client is not marked connected, raises RuntimeError including the last error string.

        Raises:
            RuntimeError: If the client is not connected within the timeout or if the connection event completes but the client is not connected.
        """  # noqa: E501
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout_sec)
        except TimeoutError as err:
            if self._last_error:
                raise RuntimeError(  # noqa: TRY003
                    f"MQTT not connected yet ({self._last_error})",
                ) from err
            self._last_error = "publish timeout waiting for MQTT connect"
            raise RuntimeError("MQTT not connected yet") from err  # noqa: TRY003
        if not self._connected:
            raise RuntimeError(f"MQTT not connected yet ({self._last_error})")  # noqa: TRY003

    async def _async_stop_locked(self) -> None:
        task = self._runner_task
        if task is None:
            return
        # The aiomqtt context manager handles socket teardown on cancel:
        # a live session sends DISCONNECT; a rejected connect just lets the
        # already-closed socket finalize.
        self._runner_task = None
        self._client = None
        self._fingerprint = None
        self._topics = []
        self._connected = False
        self._connected_event.clear()
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, MqttError, Exception):
            await task

    async def _async_run_session(  # noqa: PLR0912
        self,
        *,
        client_id: str,
        username: str,
        password: str,
        ssl_context: ssl.SSLContext,
    ) -> None:
        """Maintain a single MQTT broker session for the lifetime of one client.

        Opens and holds an MQTT connection, marks connection state and timestamps, subscribes to the configured topics, and routes inbound messages to the internal message handler. On successful connect and on disconnect it sets or clears the connection event and schedules the configured connect/disconnect callbacks. If the broker rejects the connection or other MQTT errors occur, it records an actionable connect failure or disconnect error so callers waiting on the initial broker check can observe the reason.
        """  # noqa: E501
        connected = False
        try:  # noqa: PLW0717
            async with aiomqtt.Client(
                hostname=MQTT_HOST,
                port=MQTT_PORT,
                identifier=client_id,
                username=username,
                password=password,
                tls_context=ssl_context,
                keepalive=MQTT_KEEPALIVE_SEC,
                clean_session=True,
                logger=_AIOMQTT_LOGGER,
            ) as client:
                self._client = client
                self._connected = True
                connected = True
                self._last_connect_at = self._utc_now_iso()
                self._connected_event.set()
                self._last_error = None
                self._last_connect_failure_signature = None
                # Successful broker handshake clears any transient auth-failure
                # streak — the next rejection starts the tolerance count over.
                self._consecutive_auth_failures = 0
                _LOGGER.info(
                    "Jackery MQTT connected; subscribing to %d topic(s) "
                    "[TLS source=%s]",
                    len(self._topics),
                    self._tls_certificate_source,
                )
                for topic in self._topics:
                    try:
                        await client.subscribe(topic, qos=0)
                    except MqttError as err:
                        _LOGGER.warning(
                            "Jackery MQTT subscribe failed for %s: %s",
                            topic,
                            err,
                        )
                if self._connect_callback is not None:
                    self._schedule_coroutine(
                        self._connect_callback(),
                        "connect snapshot",
                    )
                async for message in client.messages:
                    self._handle_message(str(message.topic), message.payload)
        except MqttCodeError as err:
            # Broker rejected the CONNACK (rc != 0) or returned a non-zero
            # MQTT-5 reason code. Preserve the actionable reason for callers
            # waiting on the initial broker check.
            self._handle_connect_failure(self._extract_mqtt_code(err))
        except MqttError as err:
            self._handle_disconnect_error(str(err), connected)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            self._last_error = f"connect failed: {err}"
            self._connected_event.set()
            _LOGGER.debug("Jackery MQTT connect setup failed: %s", err)
        finally:
            was_connected = connected
            self._client = None
            self._connected = False
            if was_connected:
                self._last_disconnect_at = self._utc_now_iso()
            if self._is_connect_failure_error(self._last_error):
                # Preserve the actionable connect failure for callers waiting
                # on the initial broker check. Some brokers close immediately
                # after a rejected CONNACK and we already mapped the rc above.
                self._connected_event.set()
            else:
                self._connected_event.clear()
            # Notify the upper layer so it can throttle a fresh reconnect.
            # Only fires when we actually had a live session — a CONNACK
            # rejection routes through ``_handle_connect_failure`` and must
            # not pretend the broker dropped a working connection.
            if was_connected and self._disconnect_callback is not None:
                self._schedule_coroutine(
                    self._disconnect_callback(),
                    "disconnect-recover",
                )

    def _handle_connect_failure(self, rc: int) -> None:
        """Record and handle an MQTT CONNACK failure code by updating connection state, tracking authentication-failure streaks, and logging connect-failure diagnostics.

        Parameters:
            rc (int): CONNACK return code to classify and record (used to derive a human-readable reason).
        """  # noqa: E501
        self._connected = False
        reason = MQTT_CONNACK_REASONS.get(rc, "unknown")
        message = f"connect rc={rc} ({reason})"
        self._last_error = message
        self._connected_event.set()
        if self._is_connect_auth_failure_rc(rc):
            self._consecutive_auth_failures += 1
        else:
            # Non-auth rejection (e.g. server unavailable) does not count toward
            # the auth-tolerance streak — the streak is for credential issues.
            self._consecutive_auth_failures = 0
        if message == self._last_connect_failure_signature:
            if (
                self._is_connect_auth_failure_rc(rc)
                and self._consecutive_auth_failures == MQTT_AUTH_FAILURE_TOLERANCE
            ):
                _LOGGER.warning(
                    "Jackery MQTT connect failed repeatedly: %s (streak=%d)",
                    message,
                    self._consecutive_auth_failures,
                )
            else:
                _LOGGER.debug(
                    "Jackery MQTT repeated connect failure: %s (streak=%d)",
                    message,
                    self._consecutive_auth_failures,
                )
            return
        self._last_connect_failure_signature = message
        if self._is_connect_auth_failure_rc(rc):
            _LOGGER.debug(
                "Jackery MQTT connect failed: %s (streak=%d)",
                message,
                self._consecutive_auth_failures,
            )
        else:
            _LOGGER.debug("Jackery MQTT connect failed: %s", message)

    def _handle_disconnect_error(self, error: str, was_connected: bool) -> None:
        """Record and categorize a disconnect or connection-setup failure for the current session.

        If a prior connect-failure is already recorded, preserve that actionable reason and do not overwrite it. Otherwise set `_last_error` to either `"disconnect: <error>"` when `was_connected` is True or `"connect failed: <error>"` when False, and emit a corresponding debug log.

        Parameters:
            error (str): Human-readable error message describing the failure.
            was_connected (bool): True when the client had an established session before the error, False for failures during connection setup.
        """  # noqa: E501
        if self._is_connect_failure_error(self._last_error):
            # Already mapped by ``_handle_connect_failure`` — keep the
            # actionable reason instead of overwriting it with the generic
            # disconnect message that some brokers emit immediately after a
            # rejected CONNACK.
            return
        if was_connected:
            self._last_error = f"disconnect: {error}"
            _LOGGER.debug("Jackery MQTT disconnected: %s", error)
        else:
            self._last_error = f"connect failed: {error}"
            _LOGGER.debug("Jackery MQTT connect setup failed: %s", error)

    @staticmethod
    def _extract_mqtt_code(err: MqttCodeError) -> int:
        """Extract the numeric MQTT reason code from a MqttCodeError.

        Parameters:
            err (MqttCodeError): The exception from which to extract a reason code.

        Returns:
            int: The numeric reason code if present, or `0` when no numeric code can be determined.
        """  # noqa: E501
        rc = getattr(err, "rc", None)
        if isinstance(rc, int):
            return rc
        # ReasonCodes from paho-mqtt expose ``.value`` for MQTT-5 codes.
        value = getattr(rc, "value", None)
        if isinstance(value, int):
            return value
        return 0

    @staticmethod
    def _is_connect_auth_failure_rc(rc: int) -> bool:
        """Return True for CONNACK codes that mean credentials are rejected."""
        return rc in {4, 5, 134, 135}

    @staticmethod
    def _is_connect_failure_error(error: str | None) -> bool:
        """Determine whether an error string represents a connect failure.

        Checks for the connect-failure prefixes `"connect rc="` and `"connect failed:"`.

        Returns:
            `True` if the provided error starts with one of the connect-failure prefixes, `False` otherwise.
        """  # noqa: E501
        return str(error or "").startswith(("connect rc=", "connect failed:"))

    def _build_ssl_context_blocking(self) -> ssl.SSLContext:
        """Create and return an SSLContext configured for verified TLS to connect to the Jackery MQTT broker.

        This builds a default, certificate-validated TLS context, attempts to load a custom CA file
        named `jackery_ca.crt` from the integration's `custom_components/jackery_solarvault` directory
        (if present), and enforces hostname checking and certificate verification. If OpenSSL's
        `VERIFY_X509_STRICT` flag is available, it is cleared to accommodate broker certificates
        that omit the Authority Key Identifier while preserving chain, hostname, and signature checks.
        Side effects: sets `self._tls_custom_ca_loaded` to `True` when the custom CA is successfully loaded
        and records a descriptive string in `self._tls_certificate_source`.

        Returns:
            ssl.SSLContext: An SSL context ready for use with the MQTT client, configured for verified TLS.
        """  # noqa: E501
        ctx = ssl.create_default_context()
        source_parts = ["system_default"]
        self._tls_custom_ca_loaded = False
        ca_path = Path(
            self._hass.config.path(
                "custom_components",
                "jackery_solarvault",
                "jackery_ca.crt",
            ),
        )
        if ca_path.is_file():
            try:
                ctx.load_verify_locations(cafile=str(ca_path))
            except (OSError, ssl.SSLError) as err:
                _LOGGER.warning(
                    "Jackery MQTT CA file %s could not be loaded: %s",
                    ca_path,
                    err,
                )
            else:
                self._tls_custom_ca_loaded = True
                source_parts.append(f"jackery_ca:{ca_path}")
        else:
            _LOGGER.warning("Jackery MQTT CA file missing at %s", ca_path)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        # OpenSSL 3.x / Python 3.10+ ssl.create_default_context() enables
        # VERIFY_X509_STRICT by default. This enforces the presence of the
        # Authority Key Identifier (AKID) extension in every certificate in
        # the chain. Jackery's broker certificate does not include this
        # extension, causing CERTIFICATE_VERIFY_FAILED on affected Python
        # versions. Disabling only this strict flag preserves full chain
        # verification, hostname checking, and signature validation.
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
            source_parts.append("no_x509_strict")
            _LOGGER.debug(
                "Jackery MQTT TLS: VERIFY_X509_STRICT cleared "
                "(broker cert missing AKID; chain/hostname/signature still verified)",
            )
        self._tls_certificate_source = "+".join(source_parts)
        return ctx

    def _handle_message(
        self,
        topic: str,
        payload: bytes | bytearray | str,
    ) -> None:
        """Process an inbound MQTT message payload and dispatch a parsed JSON object to the configured coordinator callback.

        Decodes the payload (bytes/bytearray as UTF-8, or uses a string), parses JSON, and validates that the result is a mapping suitable for coordinator consumption. If the payload lacks an object under the primary body field but contains an object under the alternate data field, the alternate is promoted to the expected body key. On successful parsing, records receive metadata and schedules the configured message callback; on malformed or non-object payloads, increments drop counters and records a last-message error.

        Parameters:
                topic (str): The MQTT topic the message was received on.
                payload (bytes | bytearray | str): Raw message payload; bytes/bytearray are decoded as UTF-8, strings are used directly. JSON must decode to a top-level object (dict) to be forwarded; otherwise the message is dropped.
        """  # noqa: E501
        try:
            if isinstance(payload, str):
                text = payload
            else:
                text = bytes(payload).decode("utf-8")
            data = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as err:
            self._messages_dropped += 1
            self._last_message_error = f"invalid JSON payload: {err}"
            return
        if not isinstance(data, dict):
            self._messages_dropped += 1
            self._last_message_error = "non-object JSON payload"
            return
        # PROTOCOL.md §3 documents body-based routing; some broker variants
        # send the same structure as data. Normalize before coordinator routing.
        if not isinstance(data.get(FIELD_BODY), dict):
            alt_body = data.get(FIELD_DATA)
            if isinstance(alt_body, dict):
                data[FIELD_BODY] = alt_body

        self._messages_seen += 1
        self._last_message_at = self._utc_now_iso()
        self._last_message_error = None
        self._schedule_coroutine(self._message_callback(topic, data), "message")

    def _schedule_coroutine(self, coro: Awaitable[None], label: str) -> None:
        """Schedule a coroutine as a Home Assistant task and attach a done-callback that logs non-cancellation exceptions.

        Parameters:
            coro (Awaitable[None]): Coroutine to schedule as an HA task.
            label (str): Short label appended to the task name and used in error messages.
        """  # noqa: E501
        task = self._hass.async_create_task(coro, name=f"jackery_mqtt_{label}")

        def _log_task_result(done: asyncio.Task[None]) -> None:
            """Handle a completed asyncio Task by suppressing cancellation and logging other exceptions.

            Parameters:
                done (asyncio.Task[None]): The finished task whose result or exception should be observed; `CancelledError` is ignored, and any other exception is logged as an error.
            """  # noqa: E501
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception as err:
                _LOGGER.exception("Jackery MQTT %s handler failed: %s", label, err)  # noqa: TRY401

        task.add_done_callback(_log_task_result)

    @staticmethod
    def _utc_now_iso() -> str:
        """Return a compact UTC timestamp for diagnostics."""
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _redact_topic(topic: str | None) -> str | None:
        """Redact the userId segment from Jackery MQTT topics.

        Returns:
            The topic string with the userId path segment replaced by the redacted marker when the topic begins with the Jackery topic prefix and contains at least four slash-separated segments; `None` if `topic` is `None`.
        """  # noqa: E501
        if topic is None:
            return None
        parts = topic.split("/")
        if len(parts) >= 4 and "/".join(parts[:2]) == MQTT_TOPIC_PREFIX:  # noqa: PLR2004
            parts[2] = REDACTED_VALUE
        return "/".join(parts)

    def diagnostics_snapshot(self, *, redact_topics: bool = True) -> dict[str, Any]:
        """Provide a diagnostic snapshot of the client's current MQTT state.

        Parameters:
            redact_topics (bool): If True, redact identifying segments of topic strings in the returned `topics` list and any topic fields (for example, user IDs). Defaults to True.

        Returns:
            dict[str, Any]: A mapping of diagnostic fields including connection flags (`connected`, `started`), message counters (`messages_seen`, `messages_dropped`), subscribed `topics` and `topic_count`, recent timestamps (`last_connect_at`, `last_disconnect_at`, `last_message_at`, `last_publish_at`), `seconds_since_last_message`, connection/error metadata (`last_error`, `last_message_error`, `last_connect_failure_signature`, `connect_attempts`), TLS information (`tls_custom_ca_loaded`, `tls_certificate_source`, `tls_insecure`, `tls_x509_strict_disabled`), network (`host`, `port`), authentication state (`consecutive_auth_failures`), and the client library identifier (`library`).
        """  # noqa: E501

        def topic_value(topic: str | None) -> str | None:
            """Return the topic string with the user-specific segment redacted when redaction is enabled.

            Parameters:
                topic (str | None): The MQTT topic to process; may be None.

            Returns:
                str | None: The redacted topic if redaction is enabled, the original topic otherwise; returns `None` if `topic` is `None`.
            """  # noqa: E501
            return self._redact_topic(topic) if redact_topics else topic

        return {
            "connected": self._connected,
            "started": self._runner_task is not None,
            "messages_seen": self._messages_seen,
            "messages_dropped": self._messages_dropped,
            "topics": [topic_value(topic) for topic in self._topics],
            "topic_count": len(self._topics),
            "last_error": self._last_error,
            "last_message_error": self._last_message_error,
            "last_published_topic": topic_value(self._last_published_topic),
            "last_connect_at": self._last_connect_at,
            "last_disconnect_at": self._last_disconnect_at,
            "last_message_at": self._last_message_at,
            "last_publish_at": self._last_publish_at,
            "seconds_since_last_message": self._seconds_since_last_message(),
            "mqtt_silent_for_too_long": self._mqtt_silent_for_too_long(),
            "host": MQTT_HOST,
            "port": MQTT_PORT,
            "connect_attempts": self._connect_attempts,
            "consecutive_auth_failures": self._consecutive_auth_failures,
            "last_connect_failure_signature": self._last_connect_failure_signature,
            "tls_insecure": False,
            "tls_x509_strict_disabled": False,
            "tls_custom_ca_loaded": self._tls_custom_ca_loaded,
            "tls_certificate_source": self._tls_certificate_source,
            "library": MQTT_CLIENT_LIBRARY,
        }

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Return a redacted snapshot of the MQTT client state for diagnostics."""
        return self.diagnostics_snapshot()

    def _seconds_since_last_message(self) -> float | None:
        """Return seconds elapsed since the last inbound MQTT frame, or None.

        ``None`` means we have not received a single message yet on this
        client lifetime — the broker connect may have succeeded but the
        topic subscriptions may not have been honoured. The
        ``mqtt_silent_for_too_long`` flag combines this with a threshold.
        """
        if self._last_message_at is None:
            return None
        try:
            then = datetime.fromisoformat(self._last_message_at)
        except ValueError:
            return None
        now = datetime.now(tz=then.tzinfo)
        return max(0.0, (now - then).total_seconds())

    @property
    def seconds_since_last_message(self) -> float | None:
        """Public read-only view of the last-message age helper.

        Coordinator-side adaptive polling reads this property to gate
        fast HTTP refreshes while MQTT push is delivering fresh frames.
        """
        return self._seconds_since_last_message()

    @property
    def consecutive_auth_failures(self) -> int:
        """Public read-only view of the auth-failure streak counter.

        The coordinator reads this to decide whether a transient credential
        rejection should be tolerated (token-rotation race with the official
        app) or surfaced as ``ConfigEntryAuthFailed`` so HA opens the reauth
        flow. Increments on CONNACK rc=4/5/134/135, resets on the first
        successful broker handshake.
        """
        return self._consecutive_auth_failures

    def _mqtt_silent_for_too_long(self) -> bool:
        """Return True when the broker is "connected" but no message arrives.

        Real Jackery devices emit at least one heartbeat per ~30 s. A
        sustained silence of ``MQTT_SILENT_THRESHOLD_SEC`` (default 300 s)
        while the connection is still open is a strong signal the
        subscription is broken even though TCP is alive — surface it in
        diagnostics so the user can investigate without enabling DEBUG.
        """
        if not self._connected:
            return False
        elapsed = self._seconds_since_last_message()
        if elapsed is None:
            # Still waiting for the first frame after connect — only
            # flag if it has been silent longer than the threshold AND
            # the connect itself was that long ago.
            if self._last_connect_at is None:
                return False
            try:
                then = datetime.fromisoformat(self._last_connect_at)
            except ValueError:
                return False
            now = datetime.now(tz=then.tzinfo)
            return (now - then).total_seconds() > MQTT_SILENT_THRESHOLD_SEC
        return elapsed > MQTT_SILENT_THRESHOLD_SEC

    @property
    def is_started(self) -> bool:
        """Return True once the connect/start lifecycle has run at least once."""
        return self._runner_task is not None

    @property
    def is_connected(self) -> bool:
        """Return True when the MQTT client has an active broker session."""
        return self._connected
