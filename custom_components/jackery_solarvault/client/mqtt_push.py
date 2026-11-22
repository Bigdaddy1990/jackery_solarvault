"""Async MQTT push client for Jackery SolarVault cloud broker."""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
import json
import logging
from pathlib import Path
import ssl
import time
from typing import TYPE_CHECKING, Any, Final, cast

import aiomqtt
from aiomqtt import MqttError
from aiomqtt.exceptions import MqttCodeError

from ..const import (
    FIELD_ACTION_ID,
    FIELD_BODY,
    FIELD_DATA,
    FIELD_ID,
    MQTT_APP_CONFLICT_PAUSE_SEC,
    MQTT_AUTH_FAILURE_RCS,
    MQTT_CLIENT_LIBRARY,
    MQTT_CONNACK_REASONS,
    MQTT_HOST,
    MQTT_KEEPALIVE_SEC,
    MQTT_PORT,
    MQTT_SILENT_THRESHOLD_SEC,
    MQTT_TOPIC_COMMAND,
    MQTT_TOPIC_PREFIX,
    MQTT_TOPIC_SUFFIXES,
)
from ..util import async_create_message_task
from .credentials import credential_fingerprint, redacted_error
from .ingress_receipt import with_config_receipt
from .mqtt_session import mqtt_client_session, subscription_refusals
from .operation_errors import MqttPublishOutcomeUnknownError, OperationErrors
from .safety_ingress import SafetyIngressRegistry

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

    from aiomqtt import Client as MQTTClient

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from ..models import BoxSafetyIngress

_LOGGER = logging.getLogger(__name__)
_AIOMQTT_LOGGER = logging.getLogger(f"{__name__}.aiomqtt")
# Keep this logger at NOTSET and unfiltered. Home Assistant owns the effective
# level; enabling integration DEBUG must expose every aiomqtt connection,
# subscription, publish and teardown record.
_MQTT_STOP_TIMEOUT_SEC = 5.0
_MQTT_OPERATION_TIMEOUT_SEC = 4.0
_MQTT_SUBSCRIPTION_RECOVERY_GRACE_SEC = 60.0
_MQTT_SUBSCRIPTION_RETRY_DELAYS_SEC = (1.0, 5.0, 15.0)
_MQTT_SESSION_TAKEN_OVER = 0x8E
_MAX_QUEUED_MESSAGES: Final = 256
# Getter response correlation constants
_MAX_PENDING_RESPONSES = 100
_MQTT_RESPONSE_TIMEOUT_SEC = 10.0


class _NormalizedMqttEnvelope(dict[str, Any]):  # ruff: ignore[subclass-builtin] - callback compatibility
    """Keep the received wire envelope alongside its normalized projection."""

    def __init__(self, payload: dict[str, Any], original: bytes) -> None:
        super().__init__(payload)
        self._original = original
        self.received_at_monotonic = getattr(payload, "received_at_monotonic", None)

    @property
    def original_payload(self) -> dict[str, Any]:
        """The unmodified received JSON envelope."""
        return cast("dict[str, Any]", json.loads(self._original))


class MqttSessionState(StrEnum):
    """Lifecycle state of the currently owned broker session."""

    CONNECTING = "connecting"
    SUBSCRIBED = "subscribed"
    ONLINE = "online"
    RECONNECTING = "reconnecting"
    OFFLINE = "offline"


class MqttMessageType(StrEnum):
    """Jackery MQTT message classes with protocol-defined delivery settings."""

    SUBSCRIPTION = "subscription"
    COMMAND = "command"
    BIRTH = "birth"


@dataclass(frozen=True, slots=True)
class MqttMessageSpec:
    """Delivery contract for one Jackery MQTT message class."""

    topic_suffixes: tuple[str, ...]
    qos: int
    retain: bool


@dataclass(frozen=True, slots=True)
class _MqttSessionConfig:
    """Immutable inputs for one owned broker session generation."""

    client_id: str
    username: str
    password: str
    ssl_context: ssl.SSLContext
    topics: tuple[str, ...]
    generation: int


@dataclass(slots=True)
class _MqttSessionRunState:
    """Mutable connection evidence retained across session cleanup."""

    config: _MqttSessionConfig
    runner_task: asyncio.Task[None] | None
    client: MQTTClient | None = None
    broker_connected: bool = False
    subscription_error: str | None = None
    # Match the recorded exception itself; an optional-topic failure must not
    # hide a later, independent broker disconnect.
    subscription_exception: Exception | None = None
    subscription_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    timed_out_subscriptions: set[asyncio.Task[None]] = field(default_factory=set)
    subscription_operations: dict[str, asyncio.Task[None]] = field(default_factory=dict)
    subscription_errors: dict[str, str] = field(default_factory=dict)
    subscription_attempts: dict[str, int] = field(default_factory=dict)
    subscription_retry_at: dict[str, float] = field(default_factory=dict)
    subscription_changed: asyncio.Event = field(default_factory=asyncio.Event)
    subscription_deadline: float | None = None


@dataclass(frozen=True, slots=True)
class _MqttStopPlan:
    """Owned MQTT tasks captured before state invalidation."""

    runner_task: asyncio.Task[None] | None
    lifecycle_tasks: frozenset[asyncio.Task[None]]
    owned_tasks: frozenset[asyncio.Task[None]]


# Jackery uses clean sessions and does not permit an LWT or retained presence.
JACKERY_MQTT_SUPPORTS_LWT: Final = False
JACKERY_MQTT_SUPPORTS_RETAINED_PRESENCE: Final = False
MQTT_MESSAGE_SPECS: Final = {
    MqttMessageType.SUBSCRIPTION: MqttMessageSpec(
        topic_suffixes=MQTT_TOPIC_SUFFIXES, qos=0, retain=False
    ),
    MqttMessageType.COMMAND: MqttMessageSpec(
        topic_suffixes=(MQTT_TOPIC_COMMAND,), qos=0, retain=False
    ),
    MqttMessageType.BIRTH: MqttMessageSpec(
        topic_suffixes=(MQTT_TOPIC_COMMAND,), qos=0, retain=False
    ),
}


class JackeryMqttTransportError(RuntimeError):
    """An RPC was interrupted because its MQTT session ended."""


class _MqttSubscriptionRefusedError(MqttError):
    """The broker explicitly refused a filter, rather than missing its ACK."""


class JackeryMqttPushClient:
    """Async-native MQTT client for Jackery cloud topics in PROTOCOL.md §3.

    Internal state keys:
    - "pending_responses": dict[int, asyncio.Future] — getter request waiters
    - "responses_correlated": int — successful correlations
    - "responses_expired": int — timed-out correlations
    """

    def __init__(  # ruff: ignore[too-many-arguments, too-many-statements, too-many-positional-arguments]
        self,
        hass: HomeAssistant,
        message_callback: Callable[[str, dict[str, Any]], Awaitable[object]],
        connect_callback: Callable[[], Awaitable[None]] | None = None,
        disconnect_callback: Callable[[], Awaitable[None]] | None = None,
        config_entry: ConfigEntry | None = None,
        box_safety_capture: Callable[[str], BoxSafetyIngress | None] | None = None,
        box_safety_callback: Callable[
            [str, dict[str, Any], BoxSafetyIngress], Awaitable[object]
        ]
        | None = None,
    ) -> None:
        """Initialize the MQTT push client and its lifecycle callbacks.

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
        """
        self._hass = hass
        self._config_entry = config_entry
        self._message_callback = message_callback
        self._box_safety_ingress = SafetyIngressRegistry(box_safety_capture)
        self._box_safety_callback = box_safety_callback
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
        self._session_state = MqttSessionState.OFFLINE
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
        self._pending_responses: dict[
            tuple[int, int, str | int | None], asyncio.Future[dict[str, Any]]
        ] = {}
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
        self._birth_generation: int | None = None
        self._message_queue: deque[tuple[str, dict[str, Any]]] = deque()
        self._reserved_ingress_item: tuple[str, dict[str, Any]] | None = None
        self._message_queue_full_count = 0
        self._message_consumer_task: asyncio.Task[None] | None = None
        self._message_delivery_task: asyncio.Task[None] | None = None
        self._message_delivery_item: tuple[str, dict[str, Any]] | None = None
        # Compatibility tracking used by diagnostics and bounded shutdown. A
        # FIFO client owns at most one message-consumer task at a time.
        self._message_tasks: set[asyncio.Task[None]] = set()
        self._messages_delivered = 0
        self._message_handler_errors = 0
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
        starts the session runner as a background task. Connection waiting is owned by
        the coordinator so authentication and backoff handling live in one place.

        Parameters:
            client_id (str): MQTT client identifier for the session.
            username (str): MQTT username for authentication.
            password (str): MQTT password for authentication.
            user_id (str): User identifier used to construct the subscription topic
            namespace.
        """
        if getattr(self, "_takeover_pause_until", 0.0) > time.monotonic():
            if wait_connected:
                msg = "MQTT reconnect paused after session takeover"
                raise RuntimeError(msg)
            return
        fingerprint = self._credential_fingerprint(client_id, username, password)
        async with self._lock:
            if (
                self._runner_task is not None
                and not self._runner_task.done()
                and not self._stopping
                and self._fingerprint == fingerprint
                and self._topics
                == [
                    f"{MQTT_TOPIC_PREFIX}/{user_id}/{suffix}"
                    for suffix in MQTT_TOPIC_SUFFIXES
                ]
            ):
                return

            await self._async_stop_locked()
            self._stopping = False
            self._session_generation += 1
            generation = self._session_generation
            self._session_state = (
                MqttSessionState.RECONNECTING
                if self._connect_attempts
                else MqttSessionState.CONNECTING
            )

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
            self._runner_task = self._create_background_task(
                self._async_run_session(
                    _MqttSessionConfig(
                        client_id=client_id,
                        username=username,
                        password=password,
                        ssl_context=ssl_context,
                        topics=session_topics,
                        generation=generation,
                    )
                ),
                name="jackery_mqtt_runner",
            )

        del wait_connected

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
        return credential_fingerprint({
            "client_id": client_id,
            "username": username,
            "password": password,
        })

    def session_matches_credentials(
        self,
        *,
        client_id: str,
        username: str,
        password: str,
        user_id: str,
        generation: int,
    ) -> bool:
        """Confirm that a live session owns these credentials and topics."""
        return (
            self.is_started
            and self._session_generation == generation
            and self._fingerprint
            == self._credential_fingerprint(client_id, username, password)
            and self._topics
            == [
                f"{MQTT_TOPIC_PREFIX}/{user_id}/{suffix}"
                for suffix in MQTT_TOPIC_SUFFIXES
            ]
        )

    async def async_stop(self) -> None:
        """Stop the MQTT runner and disconnect the client.

        Acquires the internal lock, stops any active background session task, and clears
        internal connection state before returning.
        """
        async with self._lock:
            await self._async_stop_locked()

    async def async_publish_json(  # ruff: ignore[too-many-arguments]
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        qos: int | None = None,
        retain: bool | None = None,
        message_type: MqttMessageType = MqttMessageType.COMMAND,
        before_publish: Callable[[], None] | None = None,
    ) -> None:
        """Publish a mapping as compact UTF-8 JSON to the given MQTT topic.

        If not already connected, waits up to 30 seconds for the client to become
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
        spec = MQTT_MESSAGE_SPECS[message_type]
        publish_qos = spec.qos if qos is None else qos
        publish_retain = spec.retain if retain is None else retain
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        generation = self._session_generation
        owner_task = self._runner_task
        if not self._connected:
            await self._async_wait_connected(timeout_sec=30.0)
        if not self._session_is_current(generation, owner_task):
            msg = "MQTT session ownership changed before publish"
            raise RuntimeError(msg)
        client = self._client
        if client is None:
            msg = "MQTT client is not running"
            raise RuntimeError(msg)
        if before_publish is not None:
            before_publish()
        try:
            await client.publish(topic, text, qos=publish_qos, retain=publish_retain)
        except asyncio.CancelledError:
            raise
        except (MqttError, TimeoutError, RuntimeError) as err:
            self._record_operation_error("publish", topic, publish_qos, err)
            detail = redacted_error(err, entry=self._config_entry)
            if self._session_is_current(generation, owner_task):
                self._last_error = f"publish failed: {detail}"
                # aiomqtt 2.5.1 _wait_for times out one publish callback, even
                # for QoS 0. Delivery is uncertain; session loss is not proven.
                # Keep unrelated response waiters and the live receive runner.
                publish_timeout = isinstance(err, TimeoutError) or (
                    type(err) is MqttError
                    and (
                        isinstance(err.__context__, TimeoutError)
                        or str(err) == "Operation timed out"
                    )
                )
                if not publish_timeout:
                    self._connected = False
                    self._connected_event.clear()
                    self._session_state = MqttSessionState.RECONNECTING
                    self._fail_pending_responses(
                        "MQTT publish disconnected the session"
                    )
                    # Actual transport failures still release their own runner;
                    # receive-side disconnects also retire it independently.
                    if (
                        owner_task is not None
                        and owner_task is not asyncio.current_task()
                    ):
                        owner_task.cancel()
            msg = f"MQTT publish failed: {detail}"
            raise MqttPublishOutcomeUnknownError(msg) from err
        if not self._session_is_current(generation, owner_task):
            msg = "MQTT session changed while publish was in flight"
            raise MqttPublishOutcomeUnknownError(msg)
        self._last_published_topic = topic
        self._last_publish_at = self._utc_now_iso()

    async def async_wait_until_connected(self, timeout_sec: float = 30.0) -> None:
        """Wait for the MQTT runner to connect before the specified timeout.

        Parameters:
            timeout_sec (float): Maximum seconds to wait for the MQTT connection.

        Raises:
            RuntimeError: If the MQTT client runner is not started, or if the client
            fails to connect within `timeout_sec`.
        """
        if not self.is_started:
            msg = "MQTT client is not running"
            raise RuntimeError(msg)
        await self._async_wait_connected(timeout_sec=timeout_sec)

    def _record_operation_error(
        self, operation: str, topic: str, qos: int | None, err: Exception
    ) -> None:
        """Own cloud operation evidence independently of the calling entity."""
        evidence = getattr(self, "_operation_errors", None)
        if evidence is None:
            evidence = self._operation_errors = OperationErrors(
                _LOGGER, "cloud_transport"
            )
        evidence.record(
            err,
            operation=operation,
            target=self._redact_topic(topic, self._config_entry) or "unknown",
            qos=qos,
            entry=self._config_entry,
        )

    async def _async_wait_connected(self, timeout_sec: float) -> None:
        """Wait for current-session connection evidence within the timeout."""
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

    def _mqtt_stop_plan(self) -> _MqttStopPlan:
        """Capture all tasks owned by the current session except this caller."""
        current_task = asyncio.current_task()
        lifecycle_tasks = frozenset(
            task for task in self._lifecycle_tasks if task is not current_task
        )
        runner_task = self._runner_task
        owned_tasks = set(lifecycle_tasks)
        if runner_task is not None and runner_task is not current_task:
            owned_tasks.add(runner_task)
        return _MqttStopPlan(
            runner_task=runner_task,
            lifecycle_tasks=lifecycle_tasks,
            owned_tasks=frozenset(owned_tasks),
        )

    @staticmethod
    def _cancel_mqtt_stop_tasks(plan: _MqttStopPlan) -> None:
        """Cancel session owners; SDK waiters survive until broker context exit."""
        for task in plan.owned_tasks:
            if not task.done():
                task.cancel()

    def _settle_mqtt_stop_tasks(
        self,
        plan: _MqttStopPlan,
        done: set[asyncio.Task[None]],
    ) -> None:
        """Observe completed task failures and release exact owned handles."""
        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                continue
            except Exception as err:  # ruff: ignore[blind-except]
                _LOGGER.warning(
                    "Jackery cloud MQTT task %s failed during stop: %s",
                    task.get_name(),
                    err,
                )
        if plan.runner_task in done and self._runner_task is plan.runner_task:
            self._runner_task = None
        for task in done & plan.lifecycle_tasks:
            token = self._lifecycle_tasks.get(task)
            if token is not None and self._lifecycle_tasks.get(task) is token:
                self._lifecycle_tasks.pop(task, None)

    async def _async_stop_locked(self) -> None:
        """Stop the current runner task and clear internal connection state.

        If a background runner task exists, cancel it and wait for its completion while
        suppressing cancellation, MQTT, and generic exceptions. Clears the stored
        client, fingerprint, subscribed topics, connected flag, and connected event so
        the instance is left in a stopped state.
        """
        self._stopping = True
        self._session_generation += 1
        self._fail_pending_responses("MQTT client stopped")
        plan = self._mqtt_stop_plan()
        self._client = None
        self._fingerprint = None
        self._topics = []
        self._subscribed_topics = []
        self._connected = False
        self._session_state = MqttSessionState.OFFLINE
        # Wake callers already blocked on a connection attempt. They observe the
        # invalidated generation and fail immediately; a new start clears the event.
        self._connected_event.set()
        for future in self._pending_responses.values():
            if not future.done():
                future.cancel()
        self._pending_responses.clear()
        self._cancel_mqtt_stop_tasks(plan)
        done: set[asyncio.Task[None]] = set()
        still_pending: set[asyncio.Task[None]] = set()
        if plan.owned_tasks:
            done, still_pending = await asyncio.wait(
                plan.owned_tasks,
                timeout=_MQTT_STOP_TIMEOUT_SEC,
            )
        self._settle_mqtt_stop_tasks(plan, done)
        if still_pending:
            runner_count = int(plan.runner_task in still_pending)
            lifecycle_count = len(still_pending & plan.lifecycle_tasks)
            accepted_backlog = len(self._message_queue) + int(
                self._message_delivery_item is not None
            )
            msg = (
                "Jackery MQTT stop timed out after "
                f"{_MQTT_STOP_TIMEOUT_SEC:.1f}s "
                f"(runner={runner_count}, lifecycle={lifecycle_count}, "
                f"messages={accepted_backlog})"
            )
            raise RuntimeError(msg)
        try:
            await self.async_wait_message_queue_idle(timeout_sec=_MQTT_STOP_TIMEOUT_SEC)
        except TimeoutError as err:
            accepted_backlog = len(self._message_queue) + int(
                self._message_delivery_item is not None
            )
            msg = (
                "Jackery MQTT stop timed out after "
                f"{_MQTT_STOP_TIMEOUT_SEC:.1f}s while draining accepted messages "
                f"(messages={accepted_backlog})"
            )
            raise RuntimeError(msg) from err

    @staticmethod
    async def _async_sdk_subscribe(client: MQTTClient, topic: str) -> None:
        """Keep the SDK MID waiter until its ACK or broker-context teardown."""
        codes = await client.subscribe(
            topic,
            qos=MQTT_MESSAGE_SPECS[MqttMessageType.SUBSCRIPTION].qos,
            timeout=float("inf"),
        )
        refused = subscription_refusals(codes)
        if refused:
            message = f"broker refused the subscription: {", ".join(refused)}"
            raise _MqttSubscriptionRefusedError(message)

    def _record_subscription_failure(
        self, state: _MqttSessionRunState, topic: str, error: Exception
    ) -> None:
        """Retain one topic's failure and bound its independent recovery."""
        detail = redacted_error(error, entry=self._config_entry)
        message = f"subscribe failed for {topic}: {detail}"
        previous = state.subscription_error
        state.subscription_errors[topic] = message
        state.subscription_error = message
        state.subscription_exception = error
        if self._last_error is None or self._last_error == previous:
            self._last_error = message
        now = time.monotonic()
        if state.subscription_deadline is None:
            state.subscription_deadline = now + _MQTT_SUBSCRIPTION_RECOVERY_GRACE_SEC
        attempt = state.subscription_attempts.get(topic, 1)
        if isinstance(error, _MqttSubscriptionRefusedError):
            state.subscription_retry_at[topic] = state.subscription_deadline
        elif attempt <= len(_MQTT_SUBSCRIPTION_RETRY_DELAYS_SEC):
            state.subscription_retry_at[topic] = (
                now + _MQTT_SUBSCRIPTION_RETRY_DELAYS_SEC[attempt - 1]
            )
        operation = state.subscription_operations.get(topic)
        awaiting_suback = (
            isinstance(error, MqttError)
            and str(error) == "Operation timed out"
            and operation is not None
            and operation in state.timed_out_subscriptions
            and not operation.done()
        )
        # A timed-out caller has not proved a failed SUBSCRIBE: the SDK MID
        # waiter is still alive and can receive a late SUBACK. Record a real
        # failure only if the SDK rejects it or recovery's grace expires.
        if not awaiting_suback:
            self._record_operation_error(
                "subscribe",
                topic,
                MQTT_MESSAGE_SPECS[MqttMessageType.SUBSCRIPTION].qos,
                error,
            )
        state.subscription_changed.set()

    def _settle_sdk_subscription(
        self,
        state: _MqttSessionRunState,
        topic: str,
        operation: asyncio.Task[None],
    ) -> None:
        """Observe every result and accept late evidence only for its live owner."""
        state.subscription_changed.set()
        if operation.cancelled():
            return
        error = operation.exception()
        if (
            not self._session_is_current(state.config.generation, state.runner_task)
            or self._client is not state.client
            or state.subscription_operations.get(topic) is not operation
        ):
            return
        if isinstance(error, Exception):
            # Immediate failures are handled by the bounded caller. A late
            # SDK failure must also wake recovery after that caller returned.
            if operation in state.timed_out_subscriptions:
                self._record_subscription_failure(state, topic, error)
            return
        if error is not None:
            return
        if topic not in self._subscribed_topics:
            self._subscribed_topics.append(topic)
        previous = state.subscription_error
        state.subscription_errors.pop(topic, None)
        state.subscription_retry_at.pop(topic, None)
        state.subscription_error = next(
            reversed(state.subscription_errors.values()), None
        )
        if self._last_error == previous:
            self._last_error = state.subscription_error

    async def _async_subscribe_topic(
        self, client: MQTTClient, state: _MqttSessionRunState, topic: str
    ) -> None:
        """Bound the caller's wait while preserving the SDK protocol operation."""
        operation = state.subscription_operations.get(topic)
        if operation is None or operation.done():
            operation = async_create_message_task(
                self._hass,
                self._config_entry,
                self._async_sdk_subscribe(client, topic),
                name=f"jackery-mqtt-subscribe-{state.config.generation}",
                eager_start=True,
            )
            state.subscription_operations[topic] = operation
            state.subscription_attempts[topic] = (
                state.subscription_attempts.get(topic, 0) + 1
            )
            state.subscription_tasks.add(operation)
            operation.add_done_callback(
                partial(self._settle_sdk_subscription, state, topic)
            )
        done, _ = await asyncio.wait({operation}, timeout=_MQTT_OPERATION_TIMEOUT_SEC)
        if not done and not operation.done():
            state.timed_out_subscriptions.add(operation)
            message = "Operation timed out"
            raise MqttError(message)
        operation.result()

    async def _async_subscribe_session(
        self,
        client: MQTTClient,
        config: _MqttSessionConfig,
        runner_task: asyncio.Task[None] | None,
        state: _MqttSessionRunState,
        *,
        primary_only: bool = False,
    ) -> bool:
        """Subscribe every configured topic while ownership remains current."""
        for topic in config.topics:
            if primary_only and not topic.endswith("/device"):
                continue
            if not self._session_is_current(config.generation, runner_task):
                return False
            try:
                await self._async_subscribe_topic(client, state, topic)
            except (MqttError, TimeoutError) as err:
                if not self._session_is_current(config.generation, runner_task):
                    return False
                self._record_subscription_failure(state, topic, err)
                if topic.endswith("/device"):
                    raise
                continue
            if not self._session_is_current(config.generation, runner_task):
                return False
            if topic not in self._subscribed_topics:
                self._subscribed_topics.append(topic)
        if not self._subscribed_topics:
            raise MqttError(state.subscription_error or "No MQTT topics subscribed")
        return self._session_is_current(config.generation, runner_task)

    async def _async_recover_subscriptions(  # ruff: ignore[too-many-branches]
        self, client: MQTTClient, state: _MqttSessionRunState
    ) -> None:
        """Recover missing secondary topics without interrupting live telemetry."""
        config = state.config
        while (
            self._session_is_current(config.generation, state.runner_task)
            and self._client is client
        ):
            state.subscription_changed.clear()
            missing = [
                topic for topic in config.topics if topic not in self._subscribed_topics
            ]
            if not missing:
                return
            now = time.monotonic()
            deadline = state.subscription_deadline
            if deadline is None:
                deadline = state.subscription_deadline = (
                    now + _MQTT_SUBSCRIPTION_RECOVERY_GRACE_SEC
                )
            if now >= deadline:
                message = "MQTT subscription recovery timed out for " + ", ".join(
                    missing
                )
                error = MqttError(message)
                previous = state.subscription_error
                state.subscription_error = message
                state.subscription_exception = error
                if self._last_error == previous:
                    self._last_error = message
                self._record_operation_error(
                    "subscribe_recovery",
                    ", ".join(missing),
                    MQTT_MESSAGE_SPECS[MqttMessageType.SUBSCRIPTION].qos,
                    error,
                )
                raise error
            for topic in missing:
                if (
                    not self._session_is_current(config.generation, state.runner_task)
                    or self._client is not client
                ):
                    return
                operation = state.subscription_operations.get(topic)
                if operation is not None and not operation.done():
                    # The SDK still owns this MID. A second SUBSCRIBE would
                    # duplicate it and cancellation would break a late SUBACK.
                    continue
                attempts = state.subscription_attempts.get(topic, 0)
                if attempts > len(_MQTT_SUBSCRIPTION_RETRY_DELAYS_SEC):
                    continue
                if time.monotonic() < state.subscription_retry_at.get(topic, 0.0):
                    continue
                try:
                    await self._async_subscribe_topic(client, state, topic)
                except (MqttError, TimeoutError) as err:
                    if (
                        self._session_is_current(config.generation, state.runner_task)
                        and self._client is client
                    ):
                        self._record_subscription_failure(state, topic, err)
                if time.monotonic() >= deadline:
                    break
            # A late ACK/failure wakes recovery immediately. Otherwise wake
            # only for the next bounded retry or the finite session grace.
            now = time.monotonic()
            wake_at = deadline
            for topic in config.topics:
                if topic in self._subscribed_topics:
                    continue
                operation = state.subscription_operations.get(topic)
                if (
                    operation is None or operation.done()
                ) and state.subscription_attempts.get(topic, 0) <= len(
                    _MQTT_SUBSCRIPTION_RETRY_DELAYS_SEC
                ):
                    wake_at = min(wake_at, state.subscription_retry_at.get(topic, now))
            try:
                async with asyncio.timeout(max(0.0, wake_at - now)):
                    await state.subscription_changed.wait()
            except TimeoutError:
                pass

    async def _async_consume_with_subscription_recovery(
        self,
        client: MQTTClient,
        config: _MqttSessionConfig,
        runner_task: asyncio.Task[None] | None,
        state: _MqttSessionRunState,
    ) -> None:
        """Keep successful subscriptions active while recovering missing topics."""
        if all(topic in self._subscribed_topics for topic in config.topics):
            await self._async_consume_session_messages(client, config, runner_task)
            return
        consumer = asyncio.create_task(
            self._async_consume_session_messages(client, config, runner_task),
            name=f"jackery-mqtt-messages-{config.generation}",
        )
        recovery = asyncio.create_task(
            self._async_recover_subscriptions(client, state),
            name=f"jackery-mqtt-subscription-recovery-{config.generation}",
        )
        try:
            done, _pending = await asyncio.wait(
                {consumer, recovery}, return_when=asyncio.FIRST_COMPLETED
            )
            if recovery in done:
                recovery.result()
                if (
                    not self._session_is_current(config.generation, runner_task)
                    or self._client is not client
                ):
                    return
            await consumer
        finally:
            consumer.cancel()
            recovery.cancel()
            await asyncio.gather(consumer, recovery, return_exceptions=True)

    def _mark_session_connected(self, config: _MqttSessionConfig) -> None:
        """Mark usable primary telemetry while missing secondary topics recover."""
        self._session_state = MqttSessionState.SUBSCRIBED
        self._connected = True
        self._last_connect_at = self._utc_now_iso()
        self._connected_event.set()
        self._last_error = None
        self._birth_not_connected_logged = False
        if self._connect_callback is not None:
            self._schedule_birth_snapshot(
                self._connect_callback,
                generation=config.generation,
            )
        else:
            self._session_state = MqttSessionState.ONLINE

    async def _async_consume_session_messages(
        self,
        client: MQTTClient,
        config: _MqttSessionConfig,
        runner_task: asyncio.Task[None] | None,
    ) -> None:
        """Feed every broker message into the serial delivery queue."""
        if (
            self._reserved_ingress_item is not None
            or len(self._message_queue) > _MAX_QUEUED_MESSAGES
        ):
            await self.async_wait_message_queue_idle()
            if not self._session_is_current(config.generation, runner_task):
                return
        async for message in client.messages:
            if not self._session_is_current(config.generation, runner_task):
                return
            full = len(self._message_queue) >= _MAX_QUEUED_MESSAGES
            self._handle_message(
                str(message.topic),
                message.payload,
                generation=config.generation,
                runner_task=runner_task,
                reserved_ingress=full,
            )
            if full:
                await self.async_wait_message_queue_idle()
            await asyncio.sleep(0)

    def _finalize_session(
        self,
        config: _MqttSessionConfig,
        runner_task: asyncio.Task[None] | None,
        state: _MqttSessionRunState,
    ) -> None:
        """Release one session generation and schedule independent recovery."""
        is_current = self._session_is_current(config.generation, runner_task)
        if not is_current:
            return
        self._client = None
        self._connected = False
        self._session_state = (
            MqttSessionState.OFFLINE
            if self._stopping or not state.broker_connected
            else MqttSessionState.RECONNECTING
        )
        self._fail_pending_responses("MQTT session disconnected")
        if state.broker_connected:
            self._last_disconnect_at = self._utc_now_iso()
        if state.subscription_error or self._is_connect_failure_error(self._last_error):
            self._connected_event.set()
        else:
            self._connected_event.clear()
        self._runner_task = None
        if not self._stopping and self._disconnect_callback is not None:
            self._schedule_lifecycle_callback(
                self._disconnect_callback,
                "disconnect-recover",
                generation=config.generation,
            )

    async def _async_open_session(
        self,
        config: _MqttSessionConfig,
        runner_task: asyncio.Task[None] | None,
        state: _MqttSessionRunState,
    ) -> None:
        """Open the broker context and run its subscribed message stream."""
        raw_client = aiomqtt.Client(
            hostname=MQTT_HOST,
            port=MQTT_PORT,
            identifier=config.client_id,
            username=config.username,
            password=config.password,
            tls_context=config.ssl_context,
            keepalive=MQTT_KEEPALIVE_SEC,
            clean_session=True,
            logger=_AIOMQTT_LOGGER,
            timeout=_MQTT_OPERATION_TIMEOUT_SEC,
        )
        async with mqtt_client_session(raw_client, entry=self._config_entry) as client:
            if not self._session_is_current(config.generation, runner_task):
                return
            self._client = client
            state.client = client
            state.broker_connected = True
            self._last_connect_failure_signature = None
            self._consecutive_auth_failures = 0
            _LOGGER.info(
                "Jackery MQTT connected; subscribing to %d topic(s) [TLS source=%s]",
                len(config.topics),
                self._tls_certificate_source,
            )
            if not await self._async_subscribe_session(
                client,
                config,
                runner_task,
                state,
                primary_only=True,
            ):
                return
            if not self._session_is_current(config.generation, runner_task):
                return
            self._mark_session_connected(config)
            if state.subscription_error is not None:
                self._last_error = state.subscription_error
            await self._async_consume_with_subscription_recovery(
                client, config, runner_task, state
            )

    @staticmethod
    async def _async_reap_subscriptions(state: _MqttSessionRunState) -> None:
        """Release SDK waiters only after their broker context has ended."""
        for operation in state.subscription_tasks:
            if not operation.done():
                operation.cancel()
        if state.subscription_tasks:
            await asyncio.gather(*state.subscription_tasks, return_exceptions=True)

    async def _async_run_session(self, config: _MqttSessionConfig) -> None:
        """Own one connect, subscribe, message, and disconnect lifecycle."""
        runner_task = asyncio.current_task()
        state = _MqttSessionRunState(config=config, runner_task=runner_task)
        try:
            await self._async_open_session(config, runner_task, state)
        except MqttCodeError as err:
            if self._session_is_current(config.generation, runner_task):
                if (
                    state.broker_connected
                    and self._extract_mqtt_code(err) == _MQTT_SESSION_TAKEN_OVER
                ):
                    self._session_takeovers = getattr(self, "_session_takeovers", 0) + 1
                    self._takeover_pause_until = (
                        time.monotonic() + MQTT_APP_CONFLICT_PAUSE_SEC
                    )
                    self._last_error = "disconnect: session taken over (0x8E)"
                    self._record_operation_error(
                        "session_takeover", MQTT_HOST, None, err
                    )
                elif err is state.subscription_exception:
                    reported_error: Exception | str = (
                        state.subscription_error or err or "MQTT subscribe failed"
                    )
                    self._handle_disconnect_error(
                        reported_error,
                        state.broker_connected,
                        record=False,
                    )
                elif state.broker_connected:
                    self._handle_disconnect_error(err, was_connected=True)
                else:
                    self._handle_connect_failure(self._extract_mqtt_code(err), err)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # ruff: ignore[blind-except]
            if self._session_is_current(config.generation, runner_task):
                subscription_recorded = err is state.subscription_exception
                self._handle_disconnect_error(
                    (state.subscription_error or err) if subscription_recorded else err,
                    state.broker_connected,
                    record=not subscription_recorded,
                )
        finally:
            try:
                await self._async_reap_subscriptions(state)
            finally:
                self._finalize_session(config, runner_task, state)

    def _handle_connect_failure(self, rc: int, error: Exception | None = None) -> None:
        """Record an MQTT CONNACK failure and notify connection waiters.

        Sets `_connected` to False, stores a human-readable `_last_error` of the form
        `connect rc=<rc> (<reason>)`, sets `_connected_event`, increments or resets
        `_consecutive_auth_failures` depending on whether `rc` indicates an
        authentication failure and records bounded operation evidence through
        `OperationErrors`.

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
        self._last_connect_failure_signature = message
        self._record_operation_error(
            "connect", MQTT_HOST, None, error or MqttError(message)
        )

    def _handle_disconnect_error(
        self,
        error: str | Exception,
        was_connected: bool,
        *,
        record: bool = True,
    ) -> None:
        """Record a disconnect or connection failure with operation context.

        If the current `_last_error` already indicates a connect failure, this method
        does nothing.
        Parameters:
                error (str): The error message to record.
                was_connected (bool): If True, record the error as a disconnect; if
                False, record it as a connect failure.
                record (bool): Whether to add operation evidence. Set False when the
                    source subscription failure was already recorded with its topic.
        """
        if self._is_connect_failure_error(self._last_error):
            return
        detail = (
            redacted_error(error, entry=self._config_entry)
            if isinstance(error, Exception)
            else error
        )
        if was_connected:
            self._last_error = f"disconnect: {detail}"
        else:
            self._last_error = f"connect failed: {detail}"
        if record:
            self._record_operation_error(
                "disconnect" if was_connected else "connect",
                MQTT_HOST,
                None,
                error if isinstance(error, Exception) else MqttError(detail),
            )

    @staticmethod
    def _extract_mqtt_code(err: object) -> int:
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
        """Return whether a CONNACK code represents authentication failure.

        Parameters:
            rc (int): CONNACK return code to evaluate.

        Returns:
            True if `rc` is one of 4, 5, 134, or 135 (authentication failure codes),
            False otherwise.
        """
        return rc in MQTT_AUTH_FAILURE_RCS

    @staticmethod
    def _is_connect_failure_error(error: str | None) -> bool:
        """Return whether text has a known connection-failure prefix."""
        return str(error or "").startswith(("connect rc=", "connect failed:"))

    def _build_ssl_context_blocking(self) -> ssl.SSLContext:
        """Create a validating TLS context with the optional Jackery CA."""
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
                    "Jackery MQTT CA file %s could not be loaded: %s",
                    ca_path,
                    redacted_error(err, entry=self._config_entry),
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
        reserved_ingress: bool = False,
    ) -> None:
        """Validate an MQTT message and dispatch it to the async callback.

        Parameters:
                topic (str): MQTT topic the message was received on.
                payload (bytes | bytearray | str): Raw message payload; bytes/bytearray
                are decoded as UTF-8, str is used as-is.

        Behavior:
                Parses JSON objects for routing. Other shapes and decode failures
                retain exact wire bytes and the error in a diagnostic envelope
                delivered through the same FIFO. If the parsed object has no dict at
                `FIELD_BODY` but does contain a dict at `FIELD_DATA`, copies
                `FIELD_DATA` into `FIELD_BODY`. On successful validation increments
                `_messages_seen`, records `_last_message_at` (UTC ISO), clears
                `_last_message_error`, and schedules the configured message callback
                with `(topic, data)`.
        """
        if self._stopping:
            return
        if generation is not None and not self._session_is_current(
            generation, runner_task
        ):
            return
        received_at_monotonic = time.monotonic()
        raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        try:
            text = raw.decode("utf-8")
            decoded: Any = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as err:
            decoded = {"raw_hex": raw.hex(), "payload_decode_error": str(err)}
        data: dict[str, Any]
        if isinstance(decoded, dict):
            data = cast("dict[str, Any]", decoded)
        else:
            data = {
                "raw_hex": raw.hex(),
                "raw_json": decoded,
                "payload_decode_error": "non-object JSON payload",
            }
        data = with_config_receipt(data, received_at_monotonic)
        if not isinstance(data.get(FIELD_BODY), dict):
            alt_body = data.get(FIELD_DATA)
            if isinstance(alt_body, dict):
                data = _NormalizedMqttEnvelope(data, raw)
                data[FIELD_BODY] = alt_body

        self._messages_seen += 1
        self._last_message_at = self._utc_now_iso()
        self._last_message_error = data.get("payload_decode_error")
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
            len(raw),
            sorted(str(key) for key in data)[:24],
            body_keys,
        )
        # Preserve accepted FIFO order, including bursts above the watermark.
        if len(self._message_queue) >= _MAX_QUEUED_MESSAGES:
            # Preserve callback bursts; async receivers apply backpressure.
            self._message_queue_full_count += 1
        item = (topic, data)
        if reserved_ingress:
            self._reserved_ingress_item = item
        self._box_safety_ingress.add(item)
        self._message_queue.append(item)
        self._ensure_message_consumer()

    def _create_background_task(
        self,
        operation: Coroutine[Any, Any, None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        """Create long-lived work owned by the config entry when available."""
        if self._config_entry is not None:
            return cast(  # ty: ignore[redundant-cast]
                "asyncio.Task[None]",
                self._config_entry.async_create_background_task(
                    self._hass,
                    operation,
                    name=name,
                    eager_start=False,
                ),
            )
        return self._hass.async_create_background_task(
            operation,
            name=name,
            eager_start=False,
        )

    def _ensure_message_consumer(self) -> None:
        """Start the sole FIFO consumer when accepted frames are waiting."""
        if not self._message_queue and self._message_delivery_task is None:
            return
        current = self._message_consumer_task
        if current is not None and not current.done():
            return
        task = async_create_message_task(
            self._hass,
            self._config_entry,
            self._async_consume_messages(),
            name="jackery_mqtt_message_fifo",
        )
        self._message_consumer_task = task
        self._message_tasks.add(task)

        def _consumer_done(done: asyncio.Task[None]) -> None:
            self._settle_message_consumer(done)
            if not self._stopping and (
                self._message_queue or self._message_delivery_task is not None
            ):
                self._ensure_message_consumer()

        task.add_done_callback(_consumer_done)

    async def _async_consume_messages(self) -> None:
        """Deliver every accepted cloud frame serially in broker order."""
        while self._message_queue or self._message_delivery_task is not None:
            delivery_task = self._message_delivery_task
            if delivery_task is not None:
                if not delivery_task.done():
                    try:
                        await asyncio.shield(delivery_task)
                    except asyncio.CancelledError:
                        if not delivery_task.cancelled():
                            raise
                self._settle_message_delivery(delivery_task)
                await asyncio.sleep(0)
                continue
            if not self._message_queue:
                return
            item = self._message_queue.popleft()
            self._message_delivery_item = item
            self._message_delivery_task = async_create_message_task(
                self._hass,
                self._config_entry,
                self._async_deliver_message(item),
                name="jackery_mqtt_message_delivery",
                eager_start=True,
            )

    async def _async_deliver_message(
        self,
        item: tuple[str, dict[str, Any]],
    ) -> None:
        """Deliver one cloud frame exactly once despite owner cancellation."""
        topic, data = item

        async def _invoke_callback() -> None:
            receipt = self._box_safety_ingress.get(item)
            if receipt is None or self._box_safety_callback is None:
                await self._message_callback(topic, data)
                return
            receipt.active = True
            try:
                await self._box_safety_callback(topic, data, receipt)
            finally:
                receipt.active = False

        callback_task: asyncio.Task[None] = self._hass.async_create_background_task(
            _invoke_callback(),
            name="jackery_mqtt_message_callback",
            eager_start=True,
        )
        if await self._async_wait_delivery_task(callback_task):
            self._message_handler_errors += 1
            self._last_message_error = (
                "message callback cancelled before completing accepted frame"
            )
            _LOGGER.error(
                "Jackery MQTT message callback was cancelled before completing "
                "an accepted frame"
            )
            return
        try:
            callback_task.result()
        except Exception:
            self._message_handler_errors += 1
            _LOGGER.exception("Jackery MQTT message handler failed")
        else:
            self._messages_delivered += 1

    @staticmethod
    async def _async_wait_delivery_task(task: asyncio.Task[None]) -> bool:
        """Await a started callback once while swallowing cancellation of its owner."""
        while not task.done():
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.done():
                    break
                current = asyncio.current_task()
                if current is not None:
                    while current.cancelling():
                        current.uncancel()
            except Exception:  # ruff:ignore[blind-except]  # callback outcome is inspected by the caller
                break
        return task.cancelled()

    def _settle_message_delivery(self, task: asyncio.Task[None]) -> None:
        """Finish one delivery and requeue it only when cancelled."""
        if self._message_delivery_task is not task:
            return
        item = self._message_delivery_item
        self._message_delivery_task = None
        self._message_delivery_item = None
        try:
            task.result()
        except asyncio.CancelledError:
            if item is not None:
                self._message_queue.appendleft(item)
                return
        except Exception:
            _LOGGER.exception("Jackery MQTT delivery task failed")
        if item is not None:
            self._box_safety_ingress.finish(item)
            if self._reserved_ingress_item is item:
                self._reserved_ingress_item = None

    def _settle_message_consumer(self, task: asyncio.Task[None]) -> None:
        """Consume one actor outcome exactly once and clear its identity safely."""
        if task not in self._message_tasks and self._message_consumer_task is not task:
            return
        self._message_tasks.discard(task)
        if self._message_consumer_task is task:
            self._message_consumer_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            if self._message_queue or self._message_delivery_task is not None:
                _LOGGER.warning(
                    "Jackery MQTT FIFO actor was cancelled with accepted delivery "
                    "still pending"
                )
        except Exception:
            _LOGGER.exception("Jackery MQTT FIFO consumer failed")

    async def async_wait_message_queue_idle(
        self,
        *,
        timeout_sec: float | None = None,
    ) -> None:
        """Wait until every accepted frame has completed serial delivery."""

        async def _wait_until_idle() -> None:
            while (
                self._message_queue
                or self._message_delivery_task is not None
                or self._message_consumer_task is not None
            ):
                consumer = self._message_consumer_task
                if consumer is not None and consumer.done():
                    self._settle_message_consumer(consumer)
                delivery = self._message_delivery_task
                if delivery is not None and delivery.done():
                    self._settle_message_delivery(delivery)
                if (
                    not self._message_queue
                    and self._message_delivery_task is None
                    and self._message_consumer_task is None
                ):
                    return
                self._ensure_message_consumer()
                consumer = self._message_consumer_task
                delivery = self._message_delivery_task
                current = asyncio.current_task()
                if consumer is current or delivery is current:
                    return
                waiter = consumer or delivery
                if waiter is None:
                    await asyncio.sleep(0)
                    continue
                try:
                    await asyncio.shield(waiter)
                except asyncio.CancelledError:
                    if not waiter.cancelled():
                        raise

        if timeout_sec is None:
            await _wait_until_idle()
            return
        async with asyncio.timeout(timeout_sec):
            await _wait_until_idle()

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

        task = self._create_background_task(
            _runner(),
            name=f"jackery_mqtt_{label}",
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

        task = self._create_background_task(
            _runner(),
            name=f"jackery_mqtt_{label}",
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
        successful operation is counted and timestamped; failures are recorded so
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
        callback_generation = (
            self._session_generation if generation is None else generation
        )
        if self._birth_generation == callback_generation:
            return
        self._birth_generation = callback_generation
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
            except Exception as err:
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
            else:
                if self._session_is_current(callback_generation):
                    self._birth_publishes += 1
                    self._last_birth_at = self._utc_now_iso()
                    self._session_state = MqttSessionState.ONLINE

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
    def _redact_topic(
        topic: str | None, entry: ConfigEntry | None = None
    ) -> str | None:
        """Return the exact MQTT topic for transport diagnostics.

        Parameters:
                topic (str | None): MQTT topic to redact, or `None`.

        Returns:
                None if `topic` is `None`; otherwise the possibly-redacted topic string.
        """
        return topic

    # Getter response correlation (bounded session state)
    @staticmethod
    def _normalize_response_type(value: object) -> str | int | None:
        """Canonicalize numeric protocol types while preserving named types."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        if not normalized:
            return None
        return int(normalized) if normalized.isdecimal() else normalized

    async def _wait_for_response(
        self,
        request_id: int,
        timeout_sec: float = _MQTT_RESPONSE_TIMEOUT_SEC,
        *,
        expected_response_type: str | int | None = None,
    ) -> dict[str, Any]:
        """Wait for a response to a getter request.

        Args:
            request_id: The request ID to wait for.
            timeout_sec: Maximum seconds to wait for response.
            expected_response_type: Response type to disambiguate keyed lookups
                when multiple response types can share a request_id/generation.

        Returns:
            The response data dictionary.

        Raises:
            asyncio.TimeoutError: If the response is not received within timeout.
            RuntimeError: If the response queue is full.
        """
        if len(self._pending_responses) >= _MAX_PENDING_RESPONSES:
            msg = "MQTT getter response queue full"
            raise RuntimeError(msg)

        generation = self._session_generation
        key = (
            generation,
            request_id,
            self._normalize_response_type(expected_response_type),
        )
        future: asyncio.Future[dict[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending_responses[key] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout_sec)
        except TimeoutError:
            self._responses_expired += 1
            raise
        finally:
            self._pending_responses.pop(key, None)

    def _resolve_pending_response(self, data: dict[str, Any]) -> None:
        """Resolve a pending getter response from an incoming message.

        Args:
            data: The parsed MQTT message data containing request_id and response.
        """
        raw_request_id = data.get("request_id", data.get(FIELD_ID))
        if not isinstance(raw_request_id, (str, int)) or isinstance(
            raw_request_id, bool
        ):
            return
        try:
            request_id = int(raw_request_id)
        except (TypeError, ValueError):
            return
        response_type = self._normalize_response_type(
            data.get("response_type", data.get(FIELD_ACTION_ID))
        )
        keys = (
            (self._session_generation, request_id, response_type),
            (self._session_generation, request_id, None),
        )
        for key in dict.fromkeys(keys):
            future = self._pending_responses.pop(key, None)
            if future is None:
                continue
            if not future.done():
                future.set_result(data)
                self._responses_correlated += 1
            return

    def _fail_pending_responses(self, reason: str) -> None:
        """Fail and remove all RPC waiters owned by any ending session."""
        pending, self._pending_responses = self._pending_responses, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(JackeryMqttTransportError(reason))

    @property
    def responses_correlated(self) -> int:
        """Number of getter responses successfully correlated to requests."""
        return self._responses_correlated

    @property
    def responses_expired(self) -> int:
        """Number of getter requests that timed out waiting for response."""
        return self._responses_expired

    def diagnostics_snapshot(self) -> dict[str, Any]:
        """Return redacted connection, FIFO, TLS, and error diagnostics."""

        def topic_value(topic: str | None) -> str | None:
            """Produce a topic with its user-specific segment redacted.

            Parameters:
                topic (str | None): MQTT topic to process; may be None.

            Returns:
                str | None: The original topic, or `None` if `topic` is `None`.
            """
            return self._redact_topic(topic, self._config_entry)

        requested_topics = [topic_value(topic) for topic in self._topics]
        subscribed_topics = [topic_value(topic) for topic in self._subscribed_topics]

        return {
            "connected": self._connected,
            "session_state": self._session_state,
            "session_generation": self._session_generation,
            "session_takeovers": getattr(self, "_session_takeovers", 0),
            "session_takeover_detection": "explicit_disconnect_reason_only",
            "session_takeover_backoff_seconds": max(
                0.0, getattr(self, "_takeover_pause_until", 0.0) - time.monotonic()
            ),
            "started": self.is_started,
            "stopping": self._stopping,
            "messages_seen": self._messages_seen,
            "messages_dropped": self._messages_dropped,
            "pending_message_tasks": len(self._message_tasks),
            "max_pending_message_tasks": _MAX_QUEUED_MESSAGES,
            "message_queue_unbounded": False,
            "message_queue_limit": _MAX_QUEUED_MESSAGES,
            "message_queue_ingress_reserve": 1,
            "message_queue_requeue_reserve": 1,
            "message_queue_full_count": getattr(self, "_message_queue_full_count", 0),
            "message_queue_depth": len(self._message_queue),
            "message_consumer_running": bool(
                self._message_consumer_task is not None
                and not self._message_consumer_task.done()
            ),
            "message_delivery_running": bool(
                self._message_delivery_task is not None
                and not self._message_delivery_task.done()
            ),
            "messages_delivered": self._messages_delivered,
            "message_handler_errors": self._message_handler_errors,
            "operation_errors": (
                self._operation_errors.snapshot()
                if getattr(self, "_operation_errors", None) is not None
                else {"errors": 0, "last_error": None}
            ),
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
        """Return non-negative seconds since the last received message.

        Parses the ISO-8601 timestamp stored in `self._last_message_at` and computes the
        difference between now and that timestamp. If `_last_message_at` is `None` or
        cannot be parsed, returns `None`.

        Returns:
            float | None: Non-negative seconds since the last message, or `None` if
            unavailable or invalid.
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
        """Return whether MQTT silence exceeds ``MQTT_SILENT_THRESHOLD_SEC``.

        Uses the time of the most recent received message when available; otherwise
        falls back to the last connect time. If the client is not connected or no usable
        timestamp is available, returns False.

        Returns:
            `True` if the elapsed time since the chosen timestamp exceeds
            MQTT_SILENT_THRESHOLD_SEC, `False` otherwise.
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
    def session_state(self) -> MqttSessionState:
        """Explicit lifecycle state of the owned MQTT session."""
        return self._session_state

    @property
    def session_generation(self) -> int:
        """Synchronous ownership generation of the current session."""
        return self._session_generation
