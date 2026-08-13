"""Async MQTT push client for Jackery SolarVault cloud broker."""

from __future__ import annotations

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

from ..const import (
    FIELD_BODY,
    FIELD_DATA,
    MQTT_AUTH_FAILURE_RCS,
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

    from homeassistant.core import HomeAssistant


_LOGGER = logging.getLogger(__name__)
_AIOMQTT_LOGGER = logging.getLogger(f"{__name__}.aiomqtt")
# Keep this logger at NOTSET and unfiltered. Home Assistant owns the effective
# level; enabling integration DEBUG must expose every aiomqtt connection,
# subscription, publish and teardown record.
_MQTT_STOP_TIMEOUT_SEC = 5.0
_MAX_PENDING_MESSAGE_TASKS = 32
# Getter response correlation constants
_MAX_PENDING_RESPONSES = 100
_MQTT_RESPONSE_TIMEOUT_SEC = 10.0


class JackeryMqttPushClient:
    """Async-native MQTT client for Jackery cloud topics in PROTOCOL.md §3.

    Internal state keys:
    - "pending_responses": dict[int, asyncio.Future] — getter request waiters
    - "responses_correlated": int — successful correlations
    - "responses_expired": int — timed-out correlations
    """

    def __init__(
        self,
        hass: HomeAssistant,
        message_callback: Callable[[str, dict[str, Any]], Awaitable[object]],
        connect_callback: Callable[[], Awaitable[None]] | None = None,
        disconnect_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize a Jackery MQTT push client and set up its internal state and
        lifecycle callbacks.

        Parameters:
            hass (Any): Home Assistant instance used for scheduling tasks and accessing
            runtime executors.
            message_callback (Callable[[str, dict[str, Any]], Awaitable[None]]): Async
            callback invoked for each received message with arguments (topic, parsed
            JSON object).
            connect_callback (Callable[[], Awaitable[None]] | None): Optional async
            callback invoked once after a successful connection is established.
            disconnect_callback (Callable[[], Awaitable[None]] | None): Optional async
            callback invoked after a prior successful connection when the client
            disconnects.
        """  # noqa: D205
        self._hass = hass
        self._message_callback = message_callback
        self._connect_callback = connect_callback
        self._disconnect_callback = disconnect_callback
        self._lock = asyncio.Lock()
        self._client: MQTTClient | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._stopping = False
        self._session_generation = 0
        self._fingerprint: str | None = None
        self._topics: list[str] = []
        self._subscribed_topics: list[str] = []
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
        self._tls_certificate_source = "jackery_ca.crt"
        self._tls_x509_strict_disabled = False
        # Getter response correlation (bounded session state)
        self._pending_responses: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._responses_correlated = 0
        self._responses_expired = 0
        # "Birth" = the on-connect app-snapshot publish the broker expects
        # after every successful CONNACK (MQTT_PROTOCOL.md §3: publish
        # QueryCombineData / QueryWeatherPlan / QuerySubDeviceGroupProperty).
        # The Jackery broker/app protocol uses clean_session + QoS 0 and sets
        # NO Last Will (MQTT_PROTOCOL.md "Clean Session: Yes"), so presence is
        # carried by this snapshot publish, not an LWT — adding a retained will
        # would be harmful on the shared single-session account. These counters
        # back the birth/availability diagnostics surfaced by the Cloud MQTT and
        # HTTP-API diagnostic sensors.
        self._birth_publishes = 0
        self._birth_publish_failed = 0
        self._birth_not_connected_logged = False
        self._last_birth_at: str | None = None
        self._message_tasks: set[asyncio.Task[None]] = set()
        self._lifecycle_tasks: dict[asyncio.Task[None], object] = {}

    async def async_start(
        self,
        *,
        client_id: str,
        username: str,
        password: str,
        user_id: str,
        wait_connected: bool = False,
    ) -> None:
        """Start or restart the MQTT push client session using the provided credentials.

        If the provided credentials produce the same fingerprint as the running session
        and the client is already connected, this returns immediately. Otherwise it
        stops any existing session, prepares the user-scoped subscription topics, builds
        an SSLContext, records the credential fingerprint and connection attempt, and
        starts the session runner as a background task. After starting the runner, waits
        up to 12 seconds for the client to report connected; a timeout is suppressed (no
        exception).

        Parameters:
            client_id (str): MQTT client identifier for the session.
            username (str): MQTT username for authentication.
            password (str): MQTT password for authentication.
            user_id (str): User identifier used to construct the subscription topic
            namespace.
        """
        fingerprint = self._credential_fingerprint(client_id, username, password)
        async with self._lock:
            if (
                self._runner_task is not None
                and not self._runner_task.done()
                and not self._stopping
                and self._fingerprint == fingerprint
            ):
                return

            await self._async_stop_locked()
            self._stopping = False
            self._session_generation += 1
            generation = self._session_generation

            self._topics = [
                f"{MQTT_TOPIC_PREFIX}/{user_id}/{suffix}"
                for suffix in MQTT_TOPIC_SUFFIXES
            ]
            session_topics = tuple(self._topics)
            self._subscribed_topics = []
            self._connected_event.clear()
            self._connected = False
            self._last_error = None
            self._last_connect_failure_signature = None

            ssl_context = await self._hass.async_add_executor_job(
                self._build_ssl_context_blocking
            )
            if not self._session_is_current(generation):
                return

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
                    topics=session_topics,
                    generation=generation,
                ),
                name="jackery_mqtt_runner",
                eager_start=False,
            )

        if wait_connected:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._connected_event.wait(), timeout=12.0)

    @staticmethod
    def _credential_fingerprint(client_id: str, username: str, password: str) -> str:
        """Produce a deterministic fingerprint for MQTT credentials.

        The fingerprint is the hexadecimal SHA-256 digest of client_id, username, and
        password,
        each encoded as UTF-8 and prefixed with a 4-byte big-endian length before
        hashing.

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
        """Stop the MQTT runner and disconnect the client.

        Acquires the internal lock, stops any active background session task, and clears
        internal connection state before returning.
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
        """Publish a mapping as compact UTF-8 JSON to the given MQTT topic.

        If not already connected, waits up to 12 seconds for the client to become
        connected.
        Serializes `payload` using compact JSON (no unnecessary whitespace, UTF-8) and
        publishes it
        with the specified `qos` and `retain` flags. On success updates the client's
        last-published
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
        generation = self._session_generation
        owner_task = self._runner_task
        if not self._connected:
            await self._async_wait_connected(timeout_sec=12.0)
        if not self._session_is_current(generation, owner_task):
            msg = "MQTT session ownership changed before publish"
            raise RuntimeError(msg)
        client = self._client
        if client is None:
            msg = "MQTT client is not running"
            raise RuntimeError(msg)
        try:
            await client.publish(topic, text, qos=qos, retain=retain)
        except asyncio.CancelledError:
            raise
        except MqttError as err:
            if self._session_is_current(generation, owner_task):
                self._connected = False
                self._connected_event.clear()
                self._last_error = f"publish failed: {err}"
            msg = f"MQTT publish failed: {err}"
            raise RuntimeError(msg) from err
        if not self._session_is_current(generation, owner_task):
            return
        self._last_published_topic = topic
        self._last_publish_at = self._utc_now_iso()

    async def async_wait_until_connected(self, timeout_sec: float = 15.0) -> None:
        """Wait for the MQTT runner to establish a connection or until the specified
        timeout elapses.

        Parameters:
            timeout_sec (float): Maximum seconds to wait for the MQTT connection.

        Raises:
            RuntimeError: If the MQTT client runner is not started, or if the client
            fails to connect within `timeout_sec`.
        """  # noqa: D205
        if not self.is_started:
            msg = "MQTT client is not running"
            raise RuntimeError(msg)
        await self._async_wait_connected(timeout_sec=timeout_sec)

    async def _async_wait_connected(self, timeout_sec: float) -> None:
        """Block until the MQTT client is marked connected or raise a RuntimeError if it
        does not become connected.

        Waits up to `timeout_sec` seconds for the internal connected event. If the wait
        times out, sets `self._last_error` to
        "publish timeout waiting for MQTT connect" when there is no prior error and then raises `RuntimeError("MQTT not connected yet")`.
        If a prior error exists or the event completes but the client is not marked
        connected, raises `RuntimeError` including
        the current `self._last_error`.

        Parameters:
            timeout_sec (float): Maximum number of seconds to wait for the connection.
        """  # noqa: D205
        generation = self._session_generation
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout_sec)
        except TimeoutError as err:
            if not self._session_is_current(generation):
                msg = "MQTT session ownership changed while waiting for connect"
                raise RuntimeError(msg) from err
            if self._last_error:
                msg = f"MQTT not connected yet ({self._last_error})"
                raise RuntimeError(msg) from err
            self._last_error = "publish timeout waiting for MQTT connect"
            msg = "MQTT not connected yet"
            raise RuntimeError(msg) from err
        if not self._session_is_current(generation):
            msg = "MQTT session ownership changed while waiting for connect"
            raise RuntimeError(msg)
        if not self._connected:
            msg = f"MQTT not connected yet ({self._last_error})"
            raise RuntimeError(msg)

    def _session_is_current(
        self,
        generation: int,
        runner_task: asyncio.Task[None] | None = None,
    ) -> bool:
        """Return whether a session still owns this client's shared state."""
        return (
            not self._stopping
            and self._session_generation == generation
            and (runner_task is None or self._runner_task is runner_task)
        )

    async def _async_stop_locked(self) -> None:
        """Stop the current runner task and clear internal connection state.

        If a background runner task exists, cancel it and wait for its completion while
        suppressing cancellation, MQTT, and generic exceptions. Clears the stored
        client, fingerprint, subscribed topics, connected flag, and connected event so
        the instance is left in a stopped state.
        """
        self._stopping = True
        self._session_generation += 1
        current_task = asyncio.current_task()
        runner_task = self._runner_task
        runner_session_entered = self._client is not None
        lifecycle_tasks = {
            pending for pending in self._lifecycle_tasks if pending is not current_task
        }
        message_tasks = {
            pending for pending in self._message_tasks if pending is not current_task
        }
        owned_tasks = set(lifecycle_tasks | message_tasks)
        if runner_task is not None and runner_task is not current_task:
            owned_tasks.add(runner_task)
        self._client = None
        self._fingerprint = None
        self._topics = []
        self._subscribed_topics = []
        self._connected = False
        # Wake callers already blocked on a connection attempt. They observe the
        # invalidated generation and fail immediately; a new start clears the event.
        self._connected_event.set()
        for pending in owned_tasks:
            # aiomqtt 2.5.1 does not unwind a cancelled ``__aenter__`` cleanly.
            # Let an in-progress connect finish or time out; once the context
            # has been entered, normal cancellation runs ``__aexit__``.
            if pending is runner_task and not runner_session_entered:
                continue
            if not pending.done():
                pending.cancel()
        if not owned_tasks:
            return
        done, still_pending = await asyncio.wait(
            owned_tasks,
            timeout=_MQTT_STOP_TIMEOUT_SEC,
        )
        for completed in done:
            with contextlib.suppress(
                asyncio.CancelledError,
                MqttError,
                Exception,
            ):
                completed.result()
        if runner_task in done and self._runner_task is runner_task:
            self._runner_task = None
        for completed in done & lifecycle_tasks:
            token = self._lifecycle_tasks.get(completed)
            if token is not None and self._lifecycle_tasks.get(completed) is token:
                self._lifecycle_tasks.pop(completed, None)
        self._message_tasks.difference_update(done)
        if still_pending:
            runner_count = int(runner_task in still_pending)
            lifecycle_count = len(still_pending & lifecycle_tasks)
            message_count = len(still_pending & message_tasks)
            msg = (
                "Jackery MQTT stop timed out after "
                f"{_MQTT_STOP_TIMEOUT_SEC:.1f}s "
                f"(runner={runner_count}, lifecycle={lifecycle_count}, "
                f"messages={message_count})"
            )
            raise RuntimeError(msg)

    async def _async_run_session(
        self,
        *,
        client_id: str,
        username: str,
        password: str,
        ssl_context: ssl.SSLContext,
        topics: tuple[str, ...],
        generation: int,
    ) -> None:
        """Manage an MQTT client session: connect, subscribe to configured topics,
        process incoming messages, and update connection state.

        On successful connection, sets internal connection flags and timestamps,
        subscribes to topics in self._topics, and forwards incoming messages to the
        internal message handler. If configured, schedules the connect callback once
        connected and schedules the disconnect callback when a previously established
        session ends. On errors, updates internal error state and sets or clears the
        connected event to reflect whether the termination was a connect failure.
        """  # noqa: D205
        runner_task = asyncio.current_task()
        broker_connected = False
        subscription_error: str | None = None
        try:
            raw_client = aiomqtt.Client(
                hostname=MQTT_HOST,
                port=MQTT_PORT,
                identifier=client_id,
                username=username,
                password=password,
                tls_context=ssl_context,
                keepalive=MQTT_KEEPALIVE_SEC,
                clean_session=True,
                logger=_AIOMQTT_LOGGER,
            )
            async with raw_client as client:
                if not self._session_is_current(generation, runner_task):
                    return
                self._client = client
                broker_connected = True
                self._last_connect_failure_signature = None
                self._consecutive_auth_failures = 0
                _LOGGER.info(
                    "Jackery MQTT connected; subscribing to %d topic(s) [TLS source=%s]",
                    len(topics),
                    self._tls_certificate_source,
                )
                for topic in topics:
                    if not self._session_is_current(generation, runner_task):
                        return
                    try:
                        await client.subscribe(topic, qos=0)
                    except MqttError as err:
                        if not self._session_is_current(generation, runner_task):
                            return
                        subscription_error = f"subscribe failed for {topic}: {err}"
                        self._last_error = subscription_error
                        _LOGGER.warning(
                            "Jackery MQTT subscribe failed for %s: %s", topic, err
                        )
                        raise
                    if not self._session_is_current(generation, runner_task):
                        return
                    self._subscribed_topics.append(topic)
                if not self._session_is_current(generation, runner_task):
                    return
                self._connected = True
                self._last_connect_at = self._utc_now_iso()
                self._connected_event.set()
                self._last_error = None
                # Fresh session: the next not-connected birth failure is
                # news again.
                self._birth_not_connected_logged = False
                if self._connect_callback is not None:
                    self._schedule_birth_snapshot(
                        self._connect_callback,
                        generation=generation,
                    )
                async for message in client.messages:
                    if not self._session_is_current(generation, runner_task):
                        return
                    self._handle_message(
                        str(message.topic),
                        message.payload,
                        generation=generation,
                        runner_task=runner_task,
                    )
        except MqttCodeError as err:
            if self._session_is_current(generation, runner_task):
                if subscription_error is not None:
                    self._handle_disconnect_error(subscription_error, broker_connected)
                else:
                    self._handle_connect_failure(self._extract_mqtt_code(err))
        except MqttError as err:
            if self._session_is_current(generation, runner_task):
                self._handle_disconnect_error(
                    subscription_error or str(err), broker_connected
                )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001
            if self._session_is_current(generation, runner_task):
                self._last_error = f"connect failed: {err}"
                self._connected_event.set()
                _LOGGER.debug("Jackery MQTT connect setup failed: %s", err)
        finally:
            was_connected = broker_connected
            is_current = self._session_is_current(generation, runner_task)
            if is_current:
                self._client = None
                self._connected = False
            if was_connected and is_current:
                self._last_disconnect_at = self._utc_now_iso()
            if is_current and (
                subscription_error or self._is_connect_failure_error(self._last_error)
            ):
                self._connected_event.set()
            elif is_current:
                self._connected_event.clear()
            if is_current:
                self._runner_task = None
            if (
                is_current
                and not self._stopping
                and self._disconnect_callback is not None
            ):
                self._schedule_lifecycle_callback(
                    self._disconnect_callback,
                    "disconnect-recover",
                    generation=generation,
                )

    def _handle_connect_failure(self, rc: int) -> None:
        """Record an MQTT CONNACK failure, update connection state and
        authentication-failure tracking, and notify any waiters.

        Sets `_connected` to False, stores a human-readable `_last_error` of the form
        `connect rc=<rc> (<reason>)`, sets `_connected_event`, increments or resets
        `_consecutive_auth_failures` depending on whether `rc` indicates an
        authentication failure, updates `_last_connect_failure_signature`, and emits a
        log message indicating a new or repeated failure.

        Parameters:
            rc (int): MQTT CONNACK return code indicating the connect failure reason.
        """  # noqa: D205
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
            # Auth rejections are actionable (wrong credentials / shared
            # session) — surface them at WARNING so the user can act.
            _LOGGER.warning("Jackery MQTT connect failed: %s", message)
        else:
            # Transient broker refusals are expected noise on an optional
            # push layer — keep them at DEBUG.
            _LOGGER.debug("Jackery MQTT connect failed: %s", message)

    def _handle_disconnect_error(self, error: str, was_connected: bool) -> None:
        """Record a disconnect or connection-failure error and emit a corresponding
        debug log.

        If the current `_last_error` already indicates a connect failure, this method
        does nothing.
        Parameters:
                error (str): The error message to record.
                was_connected (bool): If True, record the error as a disconnect; if
                False, record it as a connect failure.
        """  # noqa: D205
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
        """Extract the integer MQTT return code from a `MqttCodeError`.

        Parameters:
            err (MqttCodeError): Error that may expose a numeric return code via
            `err.rc` or `err.rc.value`.

        Returns:
            int: Extracted integer return code, or 0 if no integer code is present.
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
        """Determine whether an MQTT CONNACK return code represents an authentication
        failure.

        Parameters:
            rc (int): CONNACK return code to evaluate.

        Returns:
            True if `rc` is one of 4, 5, 134, or 135 (authentication failure codes),
            False otherwise.
        """  # noqa: D205
        return rc in MQTT_AUTH_FAILURE_RCS

    @staticmethod
    def _is_connect_failure_error(error: str | None) -> bool:
        """Detects whether an error message represents an MQTT connection failure.

        Parameters:
            error (str | None): Error text to evaluate; `None` is treated as an empty
            string.

        Returns:
            bool: `True` if the text starts with "connect rc=" or "connect failed:", `False` otherwise.
        """
        return str(error or "").startswith(("connect rc=", "connect failed:"))

    def _build_ssl_context_blocking(self) -> ssl.SSLContext:
        """Create and configure an SSLContext for verifying the MQTT broker's server
        certificate.

        Attempts to load an optional custom CA bundle from the integration directory; on
        success sets
        `self._tls_custom_ca_loaded = True`. Records the certificate source descriptor
        in
        `self._tls_certificate_source` (e.g. "system_default+jackery_ca:<path>"). Always enables hostname
        verification and requires certificate validation; sets a minimum TLS version of
        1.2 when available.

        Returns:
            ssl.SSLContext: Configured context with `check_hostname = True` and
            `verify_mode = ssl.CERT_REQUIRED`.
        """  # noqa: D205
        ctx = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        source_parts = ["system_default"]
        self._tls_custom_ca_loaded = False
        self._tls_x509_strict_disabled = False

        ca_path = Path(
            self._hass.config.path(
                "custom_components", "jackery_solarvault", "jackery_ca.crt"
            )
        )
        if ca_path.is_file():
            try:
                ctx.load_verify_locations(cafile=str(ca_path))
            except (OSError, ssl.SSLError) as err:
                _LOGGER.warning(
                    "Jackery MQTT CA file %s could not be loaded: %s", ca_path, err
                )
            else:
                self._tls_custom_ca_loaded = True
                source_parts.append(f"jackery_ca:{ca_path}")
        else:
            _LOGGER.warning("Jackery MQTT CA file missing at %s", ca_path)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED

        if hasattr(ssl, "TLSVersion"):
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2

        strict_flag = getattr(ssl, "VERIFY_X509_STRICT", None)
        if (
            isinstance(strict_flag, int)
            and hasattr(ctx, "verify_flags")
            and ctx.verify_flags & strict_flag
        ):
            ctx.verify_flags &= ~strict_flag
            self._tls_x509_strict_disabled = True
            source_parts.append("x509_strict_disabled")

        self._tls_certificate_source = "+".join(source_parts)
        return ctx

    def _handle_message(
        self,
        topic: str,
        payload: bytes | bytearray | str,
        *,
        generation: int | None = None,
        runner_task: asyncio.Task[None] | None = None,
    ) -> None:
        """Parse and validate an incoming MQTT message, update diagnostics, and dispatch
        it to the configured async message callback.

        Parameters:
                topic (str): MQTT topic the message was received on.
                payload (bytes | bytearray | str): Raw message payload; bytes/bytearray
                are decoded as UTF-8, str is used as-is.

        Behavior:
                Parses the payload as JSON and requires the top-level value to be an
                object (dict). On decode or parse failure, or when the JSON value is not
                an object, increments `_messages_dropped` and sets
                `_last_message_error`. If the parsed object does not contain a dict at
                `FIELD_BODY` but does contain a dict at `FIELD_DATA`, copies
                `FIELD_DATA` into `FIELD_BODY`. On successful validation increments
                `_messages_seen`, records `_last_message_at` (UTC ISO), clears
                `_last_message_error`, and schedules the configured message callback
                with `(topic, data)`.
        """  # noqa: D205
        if generation is not None and not self._session_is_current(
            generation, runner_task
        ):
            return
        try:
            if isinstance(payload, str):
                text = payload
            else:
                text = bytes(payload).decode("utf-8")
            data = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as err:
            self._messages_dropped += 1
            self._last_message_error = f"invalid JSON payload: {err}"
            _LOGGER.debug("Jackery MQTT: dropped invalid payload on %r: %s", topic, err)
            return
        if not isinstance(data, dict):
            self._messages_dropped += 1
            self._last_message_error = "non-object JSON payload"
            _LOGGER.debug(
                "Jackery MQTT: dropped non-object JSON on %r: %s",
                topic,
                type(data).__name__,
            )
            return
        if not isinstance(data.get(FIELD_BODY), dict):
            alt_body = data.get(FIELD_DATA)
            if isinstance(alt_body, dict):
                data[FIELD_BODY] = alt_body

        self._messages_seen += 1
        self._last_message_at = self._utc_now_iso()
        self._last_message_error = None
        # Resolve any pending getter response correlation
        self._resolve_pending_response(data)
        body_value = data.get(FIELD_BODY)
        body_keys = (
            sorted(str(key) for key in body_value)[:24]
            if isinstance(body_value, dict)
            else []
        )
        _LOGGER.debug(
            "Jackery MQTT RX: bytes=%d keys=%s body_keys=%s",
            len(text),
            sorted(str(key) for key in data)[:24],
            body_keys,
        )
        self._schedule_coroutine(
            lambda: self._message_callback(topic, data),
            "message",
            generation=generation,
            runner_task=runner_task,
            tracked_tasks=self._message_tasks,
        )

    def _schedule_coroutine(
        self,
        coro_factory: Callable[[], Awaitable[object]],
        label: str,
        *,
        generation: int | None = None,
        runner_task: asyncio.Task[None] | None = None,
        tracked_tasks: set[asyncio.Task[None]] | None = None,
    ) -> None:
        if generation is not None and not self._session_is_current(
            generation, runner_task
        ):
            return

        async def _runner() -> None:
            if generation is not None and not self._session_is_current(
                generation, runner_task
            ):
                return
            await coro_factory()

        task = self._hass.async_create_background_task(
            _runner(),
            name=f"jackery_mqtt_{label}",
            eager_start=False,
        )
        if tracked_tasks is not None:
            tracked_tasks.add(task)

        def _log_task_result(done: asyncio.Task[None]) -> None:
            if tracked_tasks is not None:
                tracked_tasks.discard(done)
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception:
                _LOGGER.exception("Jackery MQTT %s handler failed", label)

        task.add_done_callback(_log_task_result)

    def _schedule_lifecycle_callback(
        self,
        callback: Callable[[], Awaitable[None]],
        label: str,
        *,
        generation: int,
    ) -> None:
        """Schedule a session callback only while its session is current."""
        if self._stopping or generation != self._session_generation:
            return

        async def _runner() -> None:
            if self._stopping or generation != self._session_generation:
                return
            await callback()

        task = self._hass.async_create_background_task(
            _runner(),
            name=f"jackery_mqtt_{label}",
            eager_start=False,
        )
        token = object()
        self._lifecycle_tasks[task] = token

        def _track_lifecycle_result(done: asyncio.Task[None]) -> None:
            if self._lifecycle_tasks.get(done) is token:
                self._lifecycle_tasks.pop(done, None)
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception:
                _LOGGER.exception("Jackery MQTT %s handler failed", label)

        task.add_done_callback(_track_lifecycle_result)

    def _schedule_birth_snapshot(
        self,
        publish: Callable[[], Awaitable[None]],
        *,
        generation: int | None = None,
    ) -> None:
        """Dispatch the on-connect app-snapshot publish and track it as a birth.

        The snapshot publish is the Jackery protocol "birth" (MQTT_PROTOCOL.md
        §3): there is no Last Will, so presence is asserted by this publish. The
        attempt is counted and timestamped eagerly; failures are recorded so the
        birth/availability diagnostics stay accurate. Taking the CALLABLE (not a
        coroutine) lets a dead session skip without ever creating the publish
        coroutine, and "not connected" failures — expected while the broker
        rejects the session (app conflict / rc=133 pause) — log once at DEBUG
        instead of spamming ERROR on every reconnect attempt.

        Args:
            publish: Zero-arg factory for the snapshot-publish coroutine.
            generation: Session generation captured by the connection callback;
                the current generation is used when omitted.
        """
        self._birth_publishes += 1
        self._last_birth_at = self._utc_now_iso()
        callback_generation = (
            self._session_generation if generation is None else generation
        )
        if not self._connected or self._stopping:
            self._birth_publish_failed += 1
            if not self._birth_not_connected_logged:
                self._birth_not_connected_logged = True
                _LOGGER.debug(
                    "Jackery MQTT birth snapshot skipped: session not connected"
                )
            return

        async def _publish() -> None:
            try:
                await publish()
            except Exception as err:  # noqa: BLE001
                if not self._session_is_current(callback_generation):
                    return
                self._birth_publish_failed += 1
                if "not connected" in str(err).lower():
                    if not self._birth_not_connected_logged:
                        self._birth_not_connected_logged = True
                        _LOGGER.debug(
                            "Jackery MQTT birth snapshot failed (not connected): %s",
                            err,
                        )
                    return
                _LOGGER.exception("Jackery MQTT birth snapshot handler failed")

        self._schedule_lifecycle_callback(
            _publish,
            "birth snapshot",
            generation=callback_generation,
        )

    @staticmethod
    def _utc_now_iso() -> str:
        """Get the current UTC time as an ISO 8601 formatted string.

        Returns:
            str: UTC timestamp in ISO 8601 format including timezone information.
        """
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _redact_topic(topic: str | None) -> str | None:
        """Redact the user identifier segment from an MQTT topic that uses the
        configured topic prefix.

        Replaces the third slash-separated segment with `REDACTED_VALUE` when the first
        two segments joined by `/` equal `MQTT_TOPIC_PREFIX`.

        Parameters:
                topic (str | None): MQTT topic to redact, or `None`.

        Returns:
                None if `topic` is `None`; otherwise the possibly-redacted topic string.
        """  # noqa: D205
        if topic is None:
            return None
        parts = topic.split("/")
        if len(parts) >= 4 and "/".join(parts[:2]) == MQTT_TOPIC_PREFIX:
            parts[2] = REDACTED_VALUE
        return "/".join(parts)

    # Getter response correlation (bounded session state)
    async def _wait_for_response(
        self, request_id: int, timeout_sec: float = _MQTT_RESPONSE_TIMEOUT_SEC
    ) -> dict[str, Any]:
        """Wait for a response to a getter request.

        Args:
            request_id: The request ID to wait for.
            timeout_sec: Maximum seconds to wait for response.

        Returns:
            The response data dictionary.

        Raises:
            asyncio.TimeoutError: If the response is not received within timeout.
            RuntimeError: If the response queue is full.
        """
        if len(self._pending_responses) >= _MAX_PENDING_RESPONSES:
            msg = "MQTT getter response queue full"
            raise RuntimeError(msg)

        future: asyncio.Future[dict[str, Any]] = asyncio.Future()
        self._pending_responses[request_id] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout_sec)
        except TimeoutError:
            self._responses_expired += 1
            raise
        finally:
            self._pending_responses.pop(request_id, None)

    def _resolve_pending_response(self, data: dict[str, Any]) -> None:
        """Resolve a pending getter response from an incoming message.

        Args:
            data: The parsed MQTT message data containing request_id and response.
        """
        request_id = data.get("request_id")
        if isinstance(request_id, int) and request_id in self._pending_responses:
            future = self._pending_responses.pop(request_id)
            if not future.done():
                future.set_result(data)
                self._responses_correlated += 1

    @property
    def responses_correlated(self) -> int:
        """Number of getter responses successfully correlated to requests."""
        return self._responses_correlated

    @property
    def responses_expired(self) -> int:
        """Number of getter requests that timed out waiting for response."""
        return self._responses_expired

    def diagnostics_snapshot(self) -> dict[str, Any]:
        """Provide a snapshot of the client's current diagnostics and computed metrics.

        Returns:
            dict[str, Any]: Mapping containing connection state, counters, timestamps,
            broker configuration,
            TLS information, and computed diagnostics. Notable keys include:
              - "connected": whether the client is currently connected
              - "started": whether the client runner task exists
              - "messages_seen", "messages_dropped": message counters
              - "topics": list of subscribed topics with identifying segments redacted
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
            """Produce a topic with its user-specific segment redacted.

            Parameters:
                topic (str | None): MQTT topic to process; may be None.

            Returns:
                str | None: The redacted topic, or `None` if `topic` is `None`.
            """
            return self._redact_topic(topic)

        requested_topics = [topic_value(topic) for topic in self._topics]
        subscribed_topics = [topic_value(topic) for topic in self._subscribed_topics]

        return {
            "connected": self._connected,
            "started": self.is_started,
            "stopping": self._stopping,
            "messages_seen": self._messages_seen,
            "messages_dropped": self._messages_dropped,
            "pending_message_tasks": len(self._message_tasks),
            "max_pending_message_tasks": _MAX_PENDING_MESSAGE_TASKS,
            "topics": subscribed_topics,
            "topic_count": len(self._subscribed_topics),
            "requested_topics": requested_topics,
            "requested_topic_count": len(self._topics),
            "subscribed_topics": subscribed_topics,
            "subscribed_topic_count": len(self._subscribed_topics),
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
            "tls_x509_strict_disabled": self._tls_x509_strict_disabled,
            "tls_custom_ca_loaded": self._tls_custom_ca_loaded,
            "tls_certificate_source": self._tls_certificate_source,
            "library": MQTT_CLIENT_LIBRARY,
            "birth_publishes": self._birth_publishes,
            "birth_publish_failed": self._birth_publish_failed,
            "last_birth_at": self._last_birth_at,
        }

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Provide a diagnostics snapshot for the MQTT client.

        Returns:
            dict[str, Any]: Diagnostics snapshot containing connection state and flags,
            timestamps for last connect/disconnect/message/publish, message counters and
            last message error, subscribed topics and last published topic (topics may
            be redacted), TLS status and certificate source, broker constants,
            connection attempt and authentication-failure metrics, and other fields
            useful for debugging and monitoring.
        """
        return self.diagnostics_snapshot()

    def _seconds_since_last_message(self) -> float | None:
        """Return the non-negative number of seconds elapsed since the last received
        message.

        Parses the ISO-8601 timestamp stored in `self._last_message_at` and computes the
        difference between now and that timestamp. If `_last_message_at` is `None` or
        cannot be parsed, returns `None`.

        Returns:
            float | None: Non-negative seconds since the last message, or `None` if
            unavailable or invalid.
        """  # noqa: D205
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
        """Seconds elapsed since the last received MQTT message.

        Returns:
            float | None: Number of seconds since the last message, or `None` if no
            last-message timestamp is available.
        """
        return self._seconds_since_last_message()

    @property
    def consecutive_auth_failures(self) -> int:
        """Number of consecutive MQTT authentication failures.

        Returns:
            int: Count of consecutive authentication failures observed for connect
            attempts.
        """
        return self._consecutive_auth_failures

    def _mqtt_silent_for_too_long(self) -> bool:
        """Return whether the MQTT connection has been silent longer than
        MQTT_SILENT_THRESHOLD_SEC.

        Uses the time of the most recent received message when available; otherwise
        falls back to the last connect time. If the client is not connected or no usable
        timestamp is available, returns False.

        Returns:
            `True` if the elapsed time since the chosen timestamp exceeds
            MQTT_SILENT_THRESHOLD_SEC, `False` otherwise.
        """  # noqa: D205
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
            now: datetime = datetime.now(tz=then.tzinfo)
            return bool((now - then).total_seconds() > MQTT_SILENT_THRESHOLD_SEC)
        return bool(elapsed > MQTT_SILENT_THRESHOLD_SEC)

    @property
    def is_started(self) -> bool:
        """Whether the MQTT push client's background runner task exists.

        Returns:
            `True` if the client has an active runner task, `False` otherwise.
        """
        return (
            self._runner_task is not None
            and not self._runner_task.done()
            and not self._stopping
        )

    @property
    def is_connected(self) -> bool:
        """Report whether the MQTT client currently has an active connection.

        Returns:
            True if the client is connected to the MQTT broker, False otherwise.
        """
        return self._connected

    @property
    def session_generation(self) -> int:
        """Synchronous ownership generation of the current session."""
        return self._session_generation
