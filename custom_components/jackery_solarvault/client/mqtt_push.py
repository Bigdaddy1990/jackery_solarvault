"""Async MQTT push client for Jackery SolarVault cloud broker."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from datetime import UTC, datetime
import hashlib
import json
import logging
from pathlib import Path
import ssl
from typing import Any

import aiomqtt
from aiomqtt import Client as MQTTClient, MqttError
from aiomqtt.exceptions import MqttCodeError

from ..const import (
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

_LOGGER = logging.getLogger(__name__)
_AIOMQTT_LOGGER = logging.getLogger(f"{__name__}.aiomqtt")
logging.getLogger("aiomqtt").setLevel(logging.WARNING)


class _AioMqttPassiveDisconnectFilter(logging.Filter):
    """Hide expected passive broker reset noise from aiomqtt internals."""

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Suppress expected aiomqtt passive socket-reset log messages.
        
        Evaluates a LogRecord and filters out known benign "failed to receive on socket" messages that include common socket-reset markers.
        
        Parameters:
            record (logging.LogRecord): The log record to evaluate.
        
        Returns:
            bool: `True` if the record should be logged, `False` otherwise.
        """
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
        hass: Any,
        message_callback: Callable[[str, dict[str, Any]], Awaitable[None]],
        connect_callback: Callable[[], Awaitable[None]] | None = None,
        disconnect_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """
        Initialize a Jackery MQTT push client and set up its internal state and lifecycle callbacks.
        
        Parameters:
            hass (Any): Home Assistant instance used for scheduling tasks and accessing runtime executors.
            message_callback (Callable[[str, dict[str, Any]], Awaitable[None]]): Async callback invoked for each received message with arguments (topic, parsed JSON object).
            connect_callback (Callable[[], Awaitable[None]] | None): Optional async callback invoked once after a successful connection is established.
            disconnect_callback (Callable[[], Awaitable[None]] | None): Optional async callback invoked after a prior successful connection when the client disconnects.
        """
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
        """
        Start or restart the MQTT push client session with the given credentials and user scope.
        
        If the provided credentials match the currently running session and the client is already connected, the call returns immediately. Otherwise the method stops any existing session, prepares user-scoped subscription topics, builds an SSLContext, records the credential fingerprint and connection attempt, and starts the session runner as a background task. After starting the runner, it waits up to 12 seconds for the client to report connected; a timeout is suppressed.
        
        Parameters:
            client_id (str): MQTT client identifier used for the session.
            username (str): MQTT username for authentication.
            password (str): MQTT password for authentication.
            user_id (str): User identifier used to construct the subscription topic namespace.
        """
        fingerprint = self._credential_fingerprint(client_id, username, password)
        async with self._lock:
            if self._runner_task is not None and self._fingerprint == fingerprint:
                if self._connected:
                    return
                _LOGGER.info(
                    "Jackery MQTT: reconnecting async client with unchanged credentials"
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
                self._build_ssl_context_blocking
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

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._connected_event.wait(), timeout=12.0)

    @staticmethod
    def _credential_fingerprint(client_id: str, username: str, password: str) -> str:
        """
        Produce a deterministic fingerprint for MQTT credentials.
        
        The fingerprint is the hexadecimal SHA-256 digest of client_id, username, and password,
        each encoded as UTF-8 and prefixed with a 4-byte big-endian length before hashing.
        
        Returns:
            str: Hexadecimal SHA-256 digest of the provided credentials.
        """
        hasher = hashlib.sha256()
        for value in (client_id, username, password):
            encoded = value.encode()
            hasher.update(len(encoded).to_bytes(4, "big"))
            hasher.update(encoded)
        return hasher.hexdigest()

    async def async_stop(self) -> None:
        """
        Stop the MQTT runner and disconnect the client.
        """
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
        """
        Publish a mapping as compact UTF-8 JSON to the given MQTT topic.
        
        If not already connected, waits up to 12 seconds for the client to become connected.
        Serializes `payload` using compact JSON (no unnecessary whitespace, UTF-8) and publishes it
        with the specified `qos` and `retain` flags. On success updates the client's last-published
        topic and publish timestamp.
        
        Parameters:
        	topic: MQTT topic to publish to.
        	payload: Mapping to serialize as the message body.
        	qos: MQTT Quality of Service level (default 0).
        	retain: Whether the broker should retain the message (default False).
        
        Raises:
        	RuntimeError: If the MQTT client is not running or the publish fails.
        """
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        if not self._connected:
            await self._async_wait_connected(timeout_sec=12.0)
        client = self._client
        if client is None:
            raise RuntimeError("MQTT client is not running")
        try:
            await client.publish(topic, text, qos=qos, retain=retain)
        except MqttError as err:
            self._connected = False
            self._connected_event.clear()
            self._last_error = f"publish failed: {err}"
            raise RuntimeError(f"MQTT publish failed: {err}") from err
        self._last_published_topic = topic
        self._last_publish_at = self._utc_now_iso()

    async def async_wait_until_connected(self, timeout_sec: float = 15.0) -> None:
        """
        Wait for the MQTT runner to establish a connection or until the specified timeout elapses.
        
        Parameters:
            timeout_sec (float): Maximum seconds to wait for the MQTT connection.
        
        Raises:
            RuntimeError: If the MQTT client runner is not started, or if the client fails to connect within `timeout_sec`.
        """
        if self._runner_task is None:
            raise RuntimeError("MQTT client is not running")
        await self._async_wait_connected(timeout_sec=timeout_sec)

    async def _async_wait_connected(self, timeout_sec: float) -> None:
        """
        Wait until the MQTT client becomes connected or raise a RuntimeError on timeout or connection failure.
        
        If the internal connected event is not set within timeout_sec, sets self._last_error to
        "publish timeout waiting for MQTT connect" when there is no prior error and raises RuntimeError("MQTT not connected yet").
        If the event is set but the client is not marked connected, raises RuntimeError including the current self._last_error.
        
        Parameters:
            timeout_sec (float): Maximum number of seconds to wait for the connection.
        """
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout_sec)
        except TimeoutError as err:
            if self._last_error:
                raise RuntimeError(
                    f"MQTT not connected yet ({self._last_error})"
                ) from err
            self._last_error = "publish timeout waiting for MQTT connect"
            raise RuntimeError("MQTT not connected yet") from err
        if not self._connected:
            raise RuntimeError(f"MQTT not connected yet ({self._last_error})")

    async def _async_stop_locked(self) -> None:
        """
        Stop the current runner task and clear internal connection state.
        
        If a background runner task exists, cancel it and wait for its completion while suppressing cancellation, MQTT, and generic exceptions. Clears the stored client, fingerprint, subscribed topics, connected flag, and connected event so the instance is left in a stopped state.
        """
        task = self._runner_task
        if task is None:
            return
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

    async def _async_run_session(
        self,
        *,
        client_id: str,
        username: str,
        password: str,
        ssl_context: ssl.SSLContext,
    ) -> None:
        """
        Manage a single MQTT session: connect to the broker, subscribe to configured topics, process incoming messages, and maintain connection state.
        
        On successful connection this sets internal connection flags and timestamps, subscribes to topics in self._topics, forwards each incoming message to the internal message handler, and schedules the configured connect callback once. On termination, updates disconnect timestamps and schedules the configured disconnect callback only if the session had been previously established. On connection or runtime errors, records a descriptive last-error string and sets or clears the internal connected event to indicate whether the termination represents a connect failure.
        """
        connected = False
        try:
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
                self._consecutive_auth_failures = 0
                _LOGGER.info(
                    "Jackery MQTT connected; subscribing to %d topic(s) [TLS source=%s]",
                    len(self._topics),
                    self._tls_certificate_source,
                )
                for topic in self._topics:
                    try:
                        await client.subscribe(topic, qos=0)
                    except MqttError as err:
                        _LOGGER.warning(
                            "Jackery MQTT subscribe failed for %s: %s", topic, err
                        )
                if self._connect_callback is not None:
                    self._schedule_coroutine(
                        self._connect_callback(), "connect snapshot"
                    )
                async for message in client.messages:
                    self._handle_message(str(message.topic), message.payload)
        except MqttCodeError as err:
            self._handle_connect_failure(self._extract_mqtt_code(err))
        except MqttError as err:
            self._handle_disconnect_error(str(err), connected)
        except asyncio.CancelledError:
            raise
        except Exception as err:
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
                self._connected_event.set()
            else:
                self._connected_event.clear()
            if was_connected and self._disconnect_callback is not None:
                self._schedule_coroutine(
                    self._disconnect_callback(), "disconnect-recover"
                )

    def _handle_connect_failure(self, rc: int) -> None:
        """
        Record an MQTT CONNACK failure and update connection and authentication-failure state.
        
        Sets the client as disconnected, stores a human-readable `_last_error` of the form `connect rc=<rc> (<reason>)`, sets `_connected_event` to unblock waiters, and updates authentication-failure tracking and the `_last_connect_failure_signature`. If the failure repeats, emits a debug log; if the repeated failure is an authentication failure that reaches the configured tolerance, emits a warning.
        
        Parameters:
            rc (int): MQTT CONNACK return code indicating the connect failure reason.
        """
        self._connected = False
        reason = MQTT_CONNACK_REASONS.get(rc, "unknown")
        message = f"connect rc={rc} ({reason})"
        self._last_error = message
        self._connected_event.set()
        if self._is_connect_auth_failure_rc(rc):
            self._consecutive_auth_failures += 1
        else:
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
        """
        Record a disconnect or connection-failure error and update the client's last-error state.
        
        If the current `_last_error` already represents a connect failure, this method is a no-op.
        Sets `_last_error` to either `"disconnect: <error>"` when `was_connected` is True, or
        `"connect failed: <error>"` when `was_connected` is False, and emits a debug log entry.
        
        Parameters:
            error (str): The error message to record.
            was_connected (bool): If True, record the error as a disconnect; if False, record it as a connect failure.
        """
        if self._is_connect_failure_error(self._last_error):
            return
        if was_connected:
            self._last_error = f"disconnect: {error}"
            _LOGGER.debug("Jackery MQTT disconnected: %s", error)
        else:
            self._last_error = f"connect failed: {error}"
            _LOGGER.debug("Jackery MQTT connect setup failed: %s", error)

    @staticmethod
    def _extract_mqtt_code(err: MqttCodeError) -> int:
        """
        Return the integer MQTT return code contained in a MqttCodeError.
        
        Extracts an integer code from `err.rc` or `err.rc.value`. If neither yields an integer, returns `0`.
        
        Parameters:
            err (MqttCodeError): Exception instance that may expose a numeric return code.
        
        Returns:
            int: The extracted MQTT return code, or `0` if no integer code is present.
        """
        rc = getattr(err, "rc", None)
        if isinstance(rc, int):
            return rc
        value = getattr(rc, "value", None)
        if isinstance(value, int):
            return value
        return 0

    @staticmethod
    def _is_connect_auth_failure_rc(rc: int) -> bool:
        """
        Determine whether an MQTT CONNACK return code indicates an authentication failure.
        
        Parameters:
            rc (int): CONNACK return code to evaluate.
        
        Returns:
            True if `rc` is one of 4, 5, 134, or 135 (authentication failure codes), False otherwise.
        """
        return rc in (4, 5, 134, 135)

    @staticmethod
    def _is_connect_failure_error(error: str | None) -> bool:
        """
        Detects whether an error message represents an MQTT connection failure.
        
        Parameters:
            error (str | None): Error text to evaluate; `None` is treated as an empty string.
        
        Returns:
            bool: `True` if the text starts with "connect rc=" or "connect failed:", `False` otherwise.
        """
        return str(error or "").startswith(("connect rc=", "connect failed:"))

    def _build_ssl_context_blocking(self) -> ssl.SSLContext:
        """
        Create and configure an SSLContext for verifying the MQTT broker's server certificate.
        
        Attempts to load an optional custom CA bundle from the integration directory; on success sets
        `self._tls_custom_ca_loaded = True`. Records the certificate source descriptor in
        `self._tls_certificate_source` (e.g. "system_default+jackery_ca:<path>"). Always enables hostname
        verification and requires certificate validation; sets a minimum TLS version of 1.2 when available.
        
        Returns:
            ssl.SSLContext: Configured context with `check_hostname = True` and `verify_mode = ssl.CERT_REQUIRED`.
        """
        ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        source_parts = ["system_default"]
        self._tls_custom_ca_loaded = False

        ca_path = Path(
            self._hass.config.path("custom_components", "jackery_solarvault", "jackery_ca.crt")
        )
        if ca_path.is_file():
            try:
                ctx.load_verify_locations(cafile=str(ca_path))
            except (OSError, ssl.SSLError) as err:
                _LOGGER.warning("Jackery MQTT CA file %s could not be loaded: %s", ca_path, err)
            else:
                self._tls_custom_ca_loaded = True
                source_parts.append(f"jackery_ca:{ca_path}")
        else:
            _LOGGER.warning("Jackery MQTT CA file missing at %s", ca_path)

        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED

        if hasattr(ssl, "TLSVersion"):
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2

        self._tls_certificate_source = "+".join(source_parts)
        return ctx

    def _handle_message(
        self,
        topic: str,
        payload: bytes | bytearray | str,
    ) -> None:
        """
        Process an incoming MQTT message: validate and normalize its JSON payload, update diagnostics, and dispatch it to the configured async message callback.
        
        Parameters:
        	topic (str): MQTT topic the message was received on.
        	payload (bytes | bytearray | str): Raw message payload; bytes/bytearray are decoded as UTF-8, str is used as-is.
        
        Behavior:
        	- Parses the payload as JSON and requires the top-level value to be an object (dict). On decode or parse failure, or when the JSON value is not an object, increments `_messages_dropped` and sets `_last_message_error`.
        	- If the parsed object lacks a dict at `FIELD_BODY` but contains a dict at `FIELD_DATA`, copies `FIELD_DATA` into `FIELD_BODY`.
        	- On successful validation increments `_messages_seen`, records `_last_message_at` as a UTC ISO timestamp, clears `_last_message_error`, and schedules the configured async message callback with `(topic, data)`.
        """
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
        if not isinstance(data.get(FIELD_BODY), dict):
            alt_body = data.get(FIELD_DATA)
            if isinstance(alt_body, dict):
                data[FIELD_BODY] = alt_body

        self._messages_seen += 1
        self._last_message_at = self._utc_now_iso()
        self._last_message_error = None
        self._schedule_coroutine(self._message_callback(topic, data), "message")

    def _schedule_coroutine(self, coro: Awaitable[None], label: str) -> None:
        task = self._hass.async_create_task(coro, name=f"jackery_mqtt_{label}")

        def _log_task_result(done: asyncio.Task[None]) -> None:
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception as err:
                _LOGGER.error("Jackery MQTT %s handler failed: %s", label, err)

        task.add_done_callback(_log_task_result)

    @staticmethod
    def _utc_now_iso() -> str:
        """
        Get the current UTC time as an ISO 8601 formatted string.
        
        Returns:
            str: UTC timestamp in ISO 8601 format including timezone information.
        """
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _redact_topic(topic: str | None) -> str | None:
        """
        Redacts the user identifier segment from an MQTT topic when the topic uses the configured prefix.
        
        Parameters:
        	topic (str | None): MQTT topic to redact, or `None`.
        
        Returns:
        	None if `topic` is `None`; otherwise the topic with the third slash-separated segment replaced by `REDACTED_VALUE` when the first two segments equal `MQTT_TOPIC_PREFIX`, or the original topic string if no redaction applies.
        """
        if topic is None:
            return None
        parts = topic.split("/")
        if len(parts) >= 4 and "/".join(parts[:2]) == MQTT_TOPIC_PREFIX:
            parts[2] = REDACTED_VALUE
        return "/".join(parts)

    def diagnostics_snapshot(self, *, redact_topics: bool = True) -> dict[str, Any]:
        """
        Provide a snapshot of the client's current diagnostics and computed metrics.
        
        Parameters:
            redact_topics (bool): If True, redact identifying parts of topic strings in the returned
                `topics` list and `last_published_topic`; if False, return topics unchanged.
        
        Returns:
            dict[str, Any]: Mapping containing connection state, counters, timestamps, broker configuration,
            TLS information, and computed diagnostics. Notable keys include:
              - "connected": whether the client is currently connected
              - "started": whether the client runner task exists
              - "messages_seen", "messages_dropped": message counters
              - "topics": list of subscribed topics (redacted when `redact_topics` is True)
              - "topic_count": number of subscribed topics
              - "last_error", "last_message_error": last observed error strings
              - "last_published_topic", "last_connect_at", "last_disconnect_at",
                "last_message_at", "last_publish_at": last-seen topic/timestamps (ISO strings or None)
              - "seconds_since_last_message": seconds elapsed since last message (float) or None
              - "mqtt_silent_for_too_long": whether the connection has been silent past the threshold
              - "host", "port": broker connection constants
              - "connect_attempts", "consecutive_auth_failures", "last_connect_failure_signature"
              - "tls_insecure", "tls_x509_strict_disabled", "tls_custom_ca_loaded",
                "tls_certificate_source": TLS and certificate source flags
              - "library": identifier of the MQTT client library
        """
        def topic_value(topic: str | None) -> str | None:
            """
            Return a topic with its user-specific segment redacted when topic redaction is enabled.
            
            Parameters:
                topic (str | None): MQTT topic to process; may be None.
            
            Returns:
                str | None: Topic with the user segment replaced by the redaction marker when redaction is enabled, the original `topic` when redaction is disabled, or `None` if `topic` is `None`.
            """
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
        """
        Provide a diagnostics snapshot for the MQTT client.
        
        Returns:
            dict[str, Any]: Diagnostics snapshot containing connection state and flags, timestamps for last connect/disconnect/message/publish, message counters and last message error, subscribed topics and last published topic (topics may be redacted), TLS status and certificate source, broker constants, connection attempt and authentication-failure metrics, and other fields useful for debugging and monitoring.
        """
        return self.diagnostics_snapshot()

    def _seconds_since_last_message(self) -> float | None:
        """
        Compute the non-negative number of seconds elapsed since the last received message.
        
        Parses the ISO-8601 timestamp stored in `self._last_message_at` and returns the difference between now and that timestamp. If `_last_message_at` is `None` or cannot be parsed as an ISO timestamp, returns `None`.
        
        Returns:
            float | None: Non-negative seconds since the last message, or `None` if unavailable or invalid.
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
        """
        Seconds elapsed since the last received MQTT message.
        
        Returns:
            float | None: Number of seconds since the last message, or `None` if no last-message timestamp is available.
        """
        return self._seconds_since_last_message()

    @property
    def consecutive_auth_failures(self) -> int:
        """
        Report how many consecutive MQTT connect attempts failed due to authentication.
        
        Returns:
            int: Count of consecutive authentication failure occurrences.
        """
        return self._consecutive_auth_failures

    def _mqtt_silent_for_too_long(self) -> bool:
        """
        Determine whether the MQTT connection has been silent longer than the configured threshold.
        
        If a most-recent message timestamp is available, use it; otherwise fall back to the last successful connect time. If the client is not connected or no usable timestamp exists, this returns `False`.
        
        Returns:
            `True` if the elapsed time since the selected timestamp exceeds MQTT_SILENT_THRESHOLD_SEC, `False` otherwise.
        """
        if not self._connected:
            return False
        elapsed = self._seconds_since_last_message()
        if elapsed is None:
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
        """
        Report whether the MQTT push client's background runner task has been started.
        
        Returns:
            True if a background runner task exists, False otherwise.
        """
        return self._runner_task is not None

    @property
    def is_connected(self) -> bool:
        """
        Return whether the MQTT client currently has an active connection.
        
        Returns:
            `true` if the client is connected to the MQTT broker, `false` otherwise.
        """
        return self._connected