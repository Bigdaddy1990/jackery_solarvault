"""Local third-party MQTT subscriber for Jackery SolarVault.

Separate from :mod:`.mqtt_push` (cloud broker, TLS, fixed Jackery topics).
This client connects to the user's LAN broker — see PROTOCOL.md §5 /
docs/Markdown/APP_POLLING_MQTT.md — to capture telemetry that the
SolarVault publishes when the device-side third-party bridge is enabled.

Topics published by the firmware are not fully documented across all
SKUs, so users must configure the subscription filter explicitly in the
options flow. Decoded payloads are pushed to an optional sink callback;
we do NOT touch the coordinator from here so the cloud parsing pipeline
stays the single source of truth.
"""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Literal, Self, TypedDict, Unpack, cast

from aiomqtt import Client as MqttClient, MqttError

from ..const import (
    DOMAIN,
    LOCAL_MQTT_DEFAULT_TOPIC,
    LOCAL_MQTT_MAX_PAYLOAD_BYTES,
    LOCAL_MQTT_MAX_TOPIC_NAMES,
    LOCAL_MQTT_RECONNECT_FACTOR,
    LOCAL_MQTT_RECONNECT_INITIAL_SEC,
    LOCAL_MQTT_RECONNECT_MAX_SEC,
    MQTT_CONNACK_REASONS,
    MQTT_KEEPALIVE_SEC,
    REDACTED_VALUE,
)
from ..util import async_create_message_task
from .ingress_receipt import ReceivedBytes, with_config_receipt
from .mqtt_session import mqtt_client_session, subscription_refusals
from .safety_ingress import SafetyIngressRegistry

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from aiomqtt.exceptions import MqttCodeError

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from ..models import BoxSafetyIngress

_LOGGER = logging.getLogger(__name__)
_AIOMQTT_LOGGER = logging.getLogger(f"{__name__}.aiomqtt")
# The per-client aiomqtt logger can emit one DEBUG line per PUBLISH frame.
# Keep it at WARNING so enabling integration DEBUG does not create a
# packet-log storm on busy brokers.
_AIOMQTT_LOGGER.setLevel(logging.WARNING)

MqttQos = Literal[0, 1, 2]
# Sink signature kept loose so the wiring layer can pass any async callable
# that accepts ``(topic, payload_dict_or_None, raw_payload_bytes)``. ``None``
# for the dict means the payload was not valid JSON; the raw bytes are still
# forwarded so a future binary protocol decoder can plug in without touching
# this module.
LocalMqttSink = Callable[
    [str, dict[str, Any] | None, bytes],
    Awaitable[bool | Literal["broker_metadata"] | None],
]
LocalMqttSnapshotRequester = Callable[[], Awaitable[int]]
LocalMqttMetadataCapture = Callable[[str, bytes], bool | Literal["own_export"]]

# Strict by default: no implicit wildcard subscription.
# The default lives in const.py, as in diff/1406; do not shadow it here.

_HOME_ASSISTANT_EVENT_HEAD_BYTES: int = 1024
_DEFAULT_MQTT_PORT = 1883
_MAX_MQTT_PORT = 65_535
_SELF_PUBLISH_ECHO_TTL_SEC = 30.0
_MAX_PENDING_SELF_PUBLISH_ECHOES = 128
_MAX_QUEUED_MESSAGES = 4096
_MAX_METADATA_MESSAGES_PER_TURN = 32
_MAX_METADATA_BYTES_PER_TURN = 128 * 1024
_QUEUE_FULL_LOG_INTERVAL_SEC = 60.0
_SUBSCRIPTION_TIMEOUT_SEC = 10.0
_SUBSCRIPTION_RETRY_ATTEMPTS = 3
_SUBSCRIPTION_RETRY_INITIAL_SEC = 5.0
_LOCAL_MQTT_JACKERY_MARKER_KEYS = {
    "actionId",
    "batSoc",
    "body",
    "cmd",
    "data",
    "devId",
    "devSn",
    "deviceId",
    "deviceSn",
    "gridInPw",
    "gridOutPw",
    "messageType",
    "payload",
    "pvPw",
    "sn",
    "soc",
}


def _subscription_topic(topic_filter: str) -> str:
    """Preserve the configured MQTT filter, including explicit wildcards."""
    return topic_filter.strip() or LOCAL_MQTT_DEFAULT_TOPIC


class _LocalMqttConnectionOptions(TypedDict, total=False):
    """Backward-compatible keyword form of local broker settings."""

    host: str
    port: int
    username: str | None
    password: str | None
    client_id: str
    topic_filter: str
    qos: MqttQos


@dataclass(frozen=True, slots=True, kw_only=True)
class LocalMqttConnectionSettings:
    """Immutable direct-broker connection settings."""

    host: str = ""
    port: int = _DEFAULT_MQTT_PORT
    username: str | None = None
    password: str | None = None
    client_id: str = "ha-jackery-local"
    topic_filter: str = LOCAL_MQTT_DEFAULT_TOPIC
    qos: MqttQos = 0


class LocalMqttConfigurationError(ValueError):
    """Invalid direct-broker configuration."""

    @classmethod
    def conflicting_settings(cls) -> Self:
        """Build the error raised for mixed settings forms."""
        return cls("Pass either settings or connection keywords, not both")

    @classmethod
    def invalid_qos(cls) -> Self:
        """Build the error raised for an unsupported QoS."""
        return cls("MQTT QoS must be 0, 1, or 2")

    @classmethod
    def missing_host(cls) -> Self:
        """Build the error raised for an empty broker host."""
        return cls("Local MQTT broker host is required")

    @classmethod
    def invalid_port(cls) -> Self:
        """Build the error raised for an out-of-range broker port."""
        return cls("Local MQTT broker port must be between 1 and 65535")


class LocalMqttNotConnectedError(RuntimeError):
    """A publish was requested without an active direct-broker session."""

    def __init__(self) -> None:
        """Initialize the fixed not-connected error."""
        super().__init__("Local MQTT broker is not connected")


def _connection_settings(
    settings: LocalMqttConnectionSettings | None,
    options: _LocalMqttConnectionOptions,
) -> LocalMqttConnectionSettings:
    """Normalize the typed and backward-compatible settings forms."""
    if settings is not None and options:
        raise LocalMqttConfigurationError.conflicting_settings()
    return settings or LocalMqttConnectionSettings(**options)


class JackeryLocalMqttClient:
    """Async-native subscriber for the user's local MQTT broker."""

    def __init__(  # ruff: ignore[too-many-arguments, too-many-statements]
        self,
        hass: HomeAssistant,
        settings: LocalMqttConnectionSettings | None = None,
        *,
        sink: LocalMqttSink | None = None,
        config_entry: ConfigEntry | None = None,
        broker_metadata_capture: LocalMqttMetadataCapture | None = None,
        box_safety_capture: Callable[[str], BoxSafetyIngress | None] | None = None,
        box_safety_sink: Callable[
            [str, dict[str, Any] | None, bytes, BoxSafetyIngress],
            Awaitable[bool | Literal["broker_metadata"]],
        ]
        | None = None,
        **connection_options: Unpack[_LocalMqttConnectionOptions],
    ) -> None:
        """Initialize the local MQTT client configuration and internal runtime state without connecting to the broker.

        Parameters:
            hass: Home Assistant instance (passed for creating tasks and logging; not used to open network connections here).
            host (str): MQTT broker hostname or IP address.
            port (int): MQTT broker TCP port.
            username (str | None): Optional username for broker authentication.
            password (str | None): Optional password for broker authentication.
            client_id (str): MQTT client identifier to use when connecting.
            sink (LocalMqttSink | None): Optional async callback invoked for each received message as (topic, parsed_dict_or_None, raw_bytes).
            topic_filter (str): MQTT subscription topic filter to use when the client connects.
        """  # ruff: ignore[line-too-long]
        settings = _connection_settings(settings, connection_options)
        self._hass = hass
        self._config_entry = config_entry
        self._host = settings.host.strip()
        self._port = settings.port
        self._username = settings.username or None
        self._password = settings.password or None
        self._client_id = settings.client_id
        self._sink = sink
        self._lock = asyncio.Lock()
        self._broker_metadata_capture = broker_metadata_capture
        self._box_safety_ingress = SafetyIngressRegistry(box_safety_capture)
        self._box_safety_sink = box_safety_sink
        # Keep the caller's filter verbatim, as in diff/0606 and diff/1406.
        # Normalization must not turn an exact topic into a wildcard tree.
        configured_topic = _subscription_topic(settings.topic_filter)
        self._topic_filter = configured_topic
        self._topic_filters = (configured_topic,)
        self._device_response_topics: set[str] = set()
        self._device_subscription_lock = asyncio.Lock()
        if settings.qos not in {0, 1, 2}:
            raise LocalMqttConfigurationError.invalid_qos()
        self._qos = settings.qos
        self._lifecycle_lock = asyncio.Lock()
        self._subscription_active = self._snapshot_request_pending = (
            self._configuration_error
        ) = False
        self._subscribed_topics: set[str] = set()
        self._subscription_attempt_failures: dict[str, int] = {}
        self._subscription_tasks: set[asyncio.Task[None]] = set()
        self._client: MqttClient | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._connected_event = asyncio.Event()
        self._publish_failed_event = asyncio.Event()
        self._connected = False
        self._snapshot_task: asyncio.Task[None] | None = None
        self._periodic_snapshot_task: asyncio.Task[None] | None = None
        self._snapshot_interval_sec = 15.0
        self._snapshot_requester: LocalMqttSnapshotRequester | None = None
        self._message_queue: deque[tuple[str, bytes | str]] = deque()
        self._message_received_at: dict[int, float] = {}
        self._reserved_ingress_item: tuple[str, bytes | str] | None = None
        self._message_consumer_task: asyncio.Task[None] | None = None
        self._message_delivery_task: asyncio.Task[None] | None = None
        self._message_delivery_item: tuple[str, bytes | str] | None = None
        self._message_tasks: set[asyncio.Task[None]] = set()
        self._stopping = self._connected = self._topics_seen_truncated = False
        self._messages_received = self._messages_dropped = 0
        self._device_status_messages_received = 0
        self._device_event_messages_received = 0
        self._messages_forwarded = self._messages_filtered = (
            self._messages_broker_metadata
        ) = 0
        self._messages_rejected_by_sink = self._sink_errors = (
            self._message_queue_full_count
        ) = 0
        self._payload_too_large_count = self._retained_messages_dropped = 0
        self._oversized_identified_own_export = 0
        self._topics_seen: list[str] = []
        self._topics_seen_set: set[str] = set()
        self._topics_seen_truncated = False
        self._instance_started_at = self._utc_now_iso()
        self._last_topic: str | None = None
        self._last_message_at: str | None = None
        self._last_connect_at: str | None = None
        self._last_disconnect_at: str | None = None
        self._last_error: str | None = None
        self._last_sink_error: str | None = None
        self._connect_attempts = self._messages_published = self._publish_errors = 0
        self._last_publish_at: str | None = None
        self._last_publish_error: str | None = None
        self._pending_self_publish_echoes: deque[tuple[float, str, bytes]] = deque()
        self._self_publish_echoes_matched = 0
        self._blocked_by_filter_count = 0
        self._home_assistant_event_count = 0

    async def async_start(self) -> None:
        """Start one owned reconnect supervisor without waiting for the broker."""
        async with self._lock:
            if self._runner_task is not None and not self._runner_task.done():
                return
            self._stopping = False
            self._configuration_error = False
            self._connected_event.clear()
            self._connected = False
            self._last_error = None
            _LOGGER.info(
                "Jackery local MQTT: connecting to %s:%s (topic filter=%r)",
                self._host,
                self._port,
                self._topic_filter,
            )
            # The one-shot reference runner cannot reconnect after socket loss.
            # Keep the current supervisor; it owns attempt counts and backoff.
            self._runner_task = self._create_background_task(
                self._async_run_forever(), name="jackery_local_mqtt_runner"
            )

    async def async_stop(self, *, wait_for_drain: bool = True) -> None:
        """Stop the owned MQTT session and all periodic/status request tasks."""
        async with self._lock:
            self._stopping = True
            self._snapshot_request_pending = False
            self._client = None
            self._connected = False
            self._subscription_active = False
            # A pending async_start must also wake when its owner is stopped.
            self._connected_event.set()
            current = asyncio.current_task()
            tasks = {
                task
                for task in (
                    self._runner_task,
                    self._snapshot_task,
                    self._periodic_snapshot_task,
                    *self._subscription_tasks,
                )
                if task is not None and task is not current
            }
            self._runner_task = None
            self._snapshot_task = None
            self._periodic_snapshot_task = None
            # The historical early return when no runner existed left newer
            # snapshot tasks alive after config-entry unload. Stop every owner.
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        _LOGGER.warning(
                            "Local MQTT task failed during shutdown: %s", result
                        )
            self._subscription_tasks.clear()
            self._subscribed_topics.clear()
            # Do not cancel the FIFO: accepted device frames retain their
            # existing delivery owner instead of being silently discarded.
            if wait_for_drain:
                await self.async_wait_message_queue_idle()
            else:
                self._ensure_background_message_drain()

    def _create_background_task(
        self,
        operation: Coroutine[Any, Any, None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        """Create a long-lived task owned by the config entry when available."""
        if self._config_entry is not None and not self._stopping:
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

    async def _async_run_forever(self) -> None:
        """Reconnect to the configured broker until entry unload."""
        reconnect_delay = LOCAL_MQTT_RECONNECT_INITIAL_SEC
        while not self._stopping:
            self._connect_attempts += 1
            connected = await self._async_run_session()
            if connected:
                reconnect_delay = LOCAL_MQTT_RECONNECT_INITIAL_SEC
                self._configuration_error = False
            elif self._configuration_error:
                _LOGGER.error(
                    "Jackery local MQTT configuration error detected (broker ACL). "
                    "Stopping reconnect supervisor. Fix broker ACL and restart "
                    "integration."
                )
                break
            await self._async_reconnect_sleep(reconnect_delay)
            if not connected and not self._configuration_error:
                reconnect_delay = min(
                    reconnect_delay * LOCAL_MQTT_RECONNECT_FACTOR,
                    LOCAL_MQTT_RECONNECT_MAX_SEC,
                )

    @staticmethod
    async def _async_reconnect_sleep(delay: float) -> None:
        """Wait before retrying a failed direct-broker session."""
        await asyncio.sleep(delay)

    async def _async_run_session(self) -> bool:  # ruff: ignore[too-many-statements]
        """Connect, subscribe and feed broker frames into the ordered FIFO."""
        connected = False
        topics = self._requested_subscription_topics()
        try:
            raw_client = MqttClient(
                hostname=self._host,
                port=self._port,
                identifier=self._client_id,
                username=self._username,
                password=self._password,
                keepalive=MQTT_KEEPALIVE_SEC,
                logger=_AIOMQTT_LOGGER,
                timeout=_SUBSCRIPTION_TIMEOUT_SEC,
                # Do not resume a broker-side subscription tree from an older
                # integration version (for example homeassistant/#). This
                # receiver must use only the exact filters configured below.
                clean_session=True,
            )
            async with mqtt_client_session(
                raw_client, entry=self._config_entry
            ) as client:
                # One session owner handles SUBACKs, ordered delivery and reconnect.
                # A separate iterator here would bypass snapshots and failure signals.
                await self._async_consume_session(client, topics)
                # A completed message iterator means the broker session ended.
                # Prior SUBACK success must not reset reconnect backoff.
        except asyncio.CancelledError:
            raise
        except MqttError as err:
            # A subscription that was active before the iterator failed does
            # not make this a successful session. Preserve exponential
            # reconnect backoff during broker flapping instead of resetting
            # the supervisor to its initial delay after every disconnect.
            error = f"{type(err).__name__}: {err}"
            # Check if this is a SUBACK refusal (configuration error)
            if "broker refused the subscription" in str(err):
                _LOGGER.exception(
                    "Jackery local MQTT broker ACL denied subscription. "
                    "Check broker ACL for read access on topic tree. "
                    "Stopping reconnect attempts."
                )
                # Mark as configuration error - don't reconnect
                self._configuration_error = True
                self._last_error = f"CONFIG_ERROR: {error}"
                return False
            # A CONNACK code 135 ("Not authorized") is a credential/ACL
            # problem, not a transient network glitch. Retry forever
            # against a broker that rejects the client identity.
            if "code:135" in str(err) or "Not authorized" in str(err):
                _LOGGER.exception(
                    "Jackery local MQTT broker rejected the client "
                    "credentials (CONNACK code 135). Verify the broker "
                    "username/password and ACL for this client id. "
                    "Stopping reconnect attempts."
                )
                self._configuration_error = True
                self._last_error = f"CONFIG_ERROR: {error}"
                return False
            log = _LOGGER.warning if error != self._last_error else _LOGGER.debug
            self._last_error = error
            log("Jackery local MQTT connection failed: %s", err)
        except Exception as err:  # ruff: ignore[blind-except]
            error = f"{type(err).__name__}: {err}"
            log = _LOGGER.warning if error != self._last_error else _LOGGER.debug
            self._last_error = error
            log("Jackery local MQTT session failed: %s", err)
        finally:
            self._client = None
            for operation in self._subscription_tasks:
                if not operation.done():
                    operation.cancel()
            if self._subscription_tasks:
                await asyncio.gather(*self._subscription_tasks, return_exceptions=True)
                self._subscription_tasks.clear()
            was_connected = self._subscription_active
            self._client = None
            self._connected = False
            if was_connected:
                self._last_disconnect_at = self._utc_now_iso()
            # Make sure waiters in ``async_start`` cannot deadlock on a session
            # that exited before the broker accepted us.
            self._connected_event.set()
            self._connected = False
            self._subscription_active = False
            self._subscribed_topics.clear()
            self._connected_event.set()
            periodic_task = self._periodic_snapshot_task
            self._periodic_snapshot_task = None
            if (
                periodic_task is not None
                and periodic_task is not asyncio.current_task()
            ):
                periodic_task.cancel()
            # A snapshot requested by this session must finish before reconnect.
            self._snapshot_request_pending = False
            snapshot_task = self._snapshot_task
            self._snapshot_task = None
            if snapshot_task is not None:
                snapshot_task.cancel()
                await asyncio.gather(snapshot_task, return_exceptions=True)
        return connected

    def _requested_subscription_topics(self) -> list[str]:
        """Keep the configured filter and the response routes of known devices."""
        return self._minimal_subscription_topics((
            *self._topic_filters,
            *sorted(self._device_response_topics),
        ))

    async def async_subscribe_device_responses(self, serial: str) -> None:
        """Receive this device's replies without broadening the user's topic."""
        if not serial or any(char in serial for char in "/+#\0"):
            return
        async with self._device_subscription_lock:
            client = self._client
            if client is None or not self._connected or self._stopping:
                return
            # homeassistant is our export topic, not the Jackery response path.
            # Subscribe the documented serial routes before issuing local reads.
            for suffix in ("event", "status"):
                topic = f"hb/device/{serial}/{suffix}"
                self._device_response_topics.add(topic)
                if any(
                    self._topic_filter_covers(existing, topic)
                    for existing in self._subscribed_topics
                ):
                    continue
                try:
                    await self._async_recover_broker_subscription(client, topic)
                except MqttError as err:
                    self._last_error = (
                        f"subscribe failed for {topic}: {type(err).__name__}: {err}"
                    )
                    failures = self._subscription_attempt_failures
                    failures[topic] = failures.get(topic, 0) + 1
                    log = _LOGGER.warning if failures[topic] == 1 else _LOGGER.debug
                    log(
                        "Local MQTT response subscription failed for %s: %s", topic, err
                    )
                    # One refused filter must not suppress other accepted data.
                    continue
                finally:
                    # ACK waiters still pending belong to the session teardown;
                    # completed attempts must not accumulate on every poll retry.
                    self._subscription_tasks.difference_update(
                        tuple(task for task in self._subscription_tasks if task.done())
                    )
                if self._client is not client or self._stopping:
                    raise asyncio.CancelledError
                self._subscribed_topics.add(topic)
            self._subscription_active = all(
                any(
                    self._topic_filter_covers(active, topic)
                    for active in self._subscribed_topics
                )
                for topic in self._requested_subscription_topics()
            )

    async def _async_subscribe_broker_topic(
        self, client: MqttClient, topic: str
    ) -> None:
        """Keep each SDK ACK waiter alive until its result or context teardown."""
        refused = subscription_refusals(
            await client.subscribe(topic, qos=self._qos, timeout=float("inf"))
        )
        if not refused:
            return
        if topic != _subscription_topic(self._topic_filter):
            _LOGGER.warning(
                "Jackery local MQTT broker refused telemetry topic %s: %s",
                topic,
                ", ".join(refused),
            )
            msg = f"broker refused the subscription to {topic!r}"
        else:
            msg = (
                f"broker refused the subscription to {topic!r} "
                f"(SUBACK {", ".join(refused)}) — check the broker ACL for "
                f"read access on this topic tree"
            )
            # This is a configuration error (broker ACL), not a transient
            # failure. Raise MqttError but the outer loop will detect it and
            # stop reconnecting.
        raise MqttError(msg)

    async def _async_recover_broker_subscription(  # ruff: ignore[too-many-statements]
        self, client: MqttClient, topic: str
    ) -> None:
        """Retry a missing filter without cancelling late SDK ACK waiters."""
        pending: set[asyncio.Task[None]] = set()
        last_error: MqttError | None = None

        def current() -> bool:
            return self._client is client and not self._stopping

        def completed(done: set[asyncio.Task[None]]) -> bool:
            nonlocal last_error
            succeeded = False
            refusal: MqttError | None = None
            for operation in done:
                pending.discard(operation)
                try:
                    operation.result()
                except MqttError as err:
                    last_error = err
                    if "broker refused the subscription" in str(err):
                        refusal = err
                else:
                    succeeded = True
            if refusal is not None and not succeeded:
                raise refusal
            return succeeded

        def settle(operation: asyncio.Task[None]) -> None:
            if not operation.cancelled():
                operation.exception()

        for attempt in range(_SUBSCRIPTION_RETRY_ATTEMPTS):
            if not current():
                raise asyncio.CancelledError
            operation = self._create_background_task(
                self._async_subscribe_broker_topic(client, topic),
                name="jackery_local_mqtt_subscription_attempt",
            )
            pending.add(operation)
            self._subscription_tasks.add(operation)
            operation.add_done_callback(settle)
            done, _ = await asyncio.wait(
                pending,
                timeout=_SUBSCRIPTION_TIMEOUT_SEC,
                return_when=asyncio.FIRST_COMPLETED,
            )
            done.update(operation for operation in pending if operation.done())
            if not current():
                raise asyncio.CancelledError
            if completed(done):
                return
            if last_error is None:
                last_error = MqttError("Operation timed out")
            failures = self._subscription_attempt_failures
            failures[topic] = failures.get(topic, 0) + 1
            log = _LOGGER.warning if failures[topic] == 1 else _LOGGER.debug
            log(
                "Jackery local MQTT subscription attempt %d/%d failed for %s: %s "
                "failure_count=%d",
                attempt + 1,
                _SUBSCRIPTION_RETRY_ATTEMPTS,
                topic,
                last_error,
                failures[topic],
            )
            if attempt + 1 == _SUBSCRIPTION_RETRY_ATTEMPTS:
                break
            delay = _SUBSCRIPTION_RETRY_INITIAL_SEC * 2**attempt
            if pending:
                done, _ = await asyncio.wait(
                    pending, timeout=delay, return_when=asyncio.FIRST_COMPLETED
                )
                done.update(operation for operation in pending if operation.done())
            else:
                await asyncio.sleep(delay)
                done = set()
            if not current():
                raise asyncio.CancelledError
            if completed(done):
                return
        # Retire the owning context before cancelling its SDK ACK waiters.
        # SUBSCRIBE is idempotent; uncertain device writes are never replayed.
        msg = f"subscription retries exhausted for {topic}: {last_error}"
        raise MqttError(msg)

    async def _async_consume_session(  # ruff: ignore[complex-structure, too-many-statements]
        self,
        client: MqttClient,
        topics: list[str],
    ) -> None:
        """Subscribe the configured filters, then start the ordered receiver.

        Required-filter failures unwind the session for reconnect; optional-filter
        recovery remains with the same owner. Incoming frames never bypass FIFO.
        """
        self._client, self._publish_failed_event = client, asyncio.Event()
        self._subscription_failed_event = asyncio.Event()
        self._subscription_failure_error: MqttError | None = None
        subscribed: set[str] = set()
        errors: dict[str, str] = {}
        required_topic = _subscription_topic(self._topic_filter)

        def settle_subscription(operation: asyncio.Task[None]) -> None:
            if not operation.cancelled():
                operation.exception()

        async def subscribe(topic: str) -> None:
            previous_subscription_error: str | None = None
            retry_delay = max(
                _SUBSCRIPTION_RETRY_INITIAL_SEC,
                _SUBSCRIPTION_RETRY_INITIAL_SEC
                * 2 ** (_SUBSCRIPTION_RETRY_ATTEMPTS - 1),
            )
            try:
                while True:
                    if self._client is not client or self._stopping:
                        raise asyncio.CancelledError
                    try:
                        if topic == required_topic:
                            await self._async_subscribe_broker_topic(client, topic)
                        else:
                            await self._async_recover_broker_subscription(client, topic)
                    except MqttError as err:
                        previous_subscription_error = next(
                            reversed(errors.values()), None
                        )
                        errors[topic] = (
                            f"subscribe failed for {topic}: {type(err).__name__}: {err}"
                        )
                        if self._client is client and not self._stopping:
                            self._last_error = errors[topic]
                        if topic == required_topic:
                            self._subscription_failure_error = err
                            self._subscription_failed_event.set()
                            raise
                        if "broker refused the subscription" in str(err):
                            # A supplemental ACL refusal is recorded, while
                            # successful filters keep delivering telemetry.
                            failures = self._subscription_attempt_failures
                            failures[topic] = failures.get(topic, 0) + 1
                            return
                        # A transient optional-filter timeout must not retire
                        # the working broker session. Retry this filter under
                        # the same session owner with bounded backoff.
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, 120.0)
                        continue
                    if self._client is client and not self._stopping:
                        subscribed.add(topic)
                    previous_subscription_error = next(reversed(errors.values()), None)
                    errors.pop(topic, None)
                    return
            finally:
                if self._client is client and not self._stopping:
                    self._subscription_active = all(
                        target in subscribed for target in topics
                    )
                    if self._connected and (
                        self._last_error is None
                        or self._last_error == previous_subscription_error
                    ):
                        self._last_error = next(reversed(errors.values()), None)

        for topic in [topic for topic in topics if topic != required_topic] + [
            topic for topic in topics if topic == required_topic
        ]:
            operation = self._create_background_task(
                subscribe(topic), name="jackery_local_mqtt_subscribe"
            )
            self._subscription_tasks.add(operation)
            operation.add_done_callback(settle_subscription)
            done, _ = await asyncio.wait({operation}, timeout=_SUBSCRIPTION_TIMEOUT_SEC)
            if not done:
                errors[topic] = f"subscribe failed for {topic}: Operation timed out"
                if topic == required_topic:
                    msg = "Operation timed out"
                    raise MqttError(msg)
                _LOGGER.warning("Jackery local MQTT %s", errors[topic])
                continue
            try:
                operation.result()
            except MqttError:
                if topic == required_topic:
                    raise
        if self._client is not client or self._stopping:
            raise asyncio.CancelledError
        self._subscribed_topics = subscribed
        self._configuration_error = False
        recovered = self._last_error is not None
        self._connected = True
        self._subscription_active = all(topic in subscribed for topic in topics)
        self._last_connect_at = self._utc_now_iso()
        self._last_error = next(reversed(errors.values()), None)
        self._connected_event.set()
        if recovered:
            _LOGGER.info("Jackery local MQTT connection restored")
        self._schedule_snapshot_request()
        self._ensure_periodic_snapshot()
        await self._async_receive_until_publish_failure(client)

    async def _async_receive_until_publish_failure(self, client: MqttClient) -> None:
        """Retire a session on receiver, publishing or subscription failure."""
        receiver = self._create_background_task(
            self._async_receive_session_messages(client),
            name="jackery_local_mqtt_receive",
        )
        publish_failed = asyncio.create_task(self._publish_failed_event.wait())
        subscription_failed = asyncio.create_task(
            self._subscription_failed_event.wait()
        )
        try:
            done, _pending = await asyncio.wait(
                {receiver, publish_failed, subscription_failed},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if subscription_failed in done:
                raise self._subscription_failure_error or MqttError(
                    "MQTT telemetry subscription failed"
                )
            if publish_failed in done:
                message = "MQTT publish disconnected the local session"
                raise MqttError(message)
            await receiver
        finally:
            receiver.cancel()
            publish_failed.cancel()
            subscription_failed.cancel()
            await asyncio.gather(
                receiver,
                publish_failed,
                subscription_failed,
                return_exceptions=True,
            )

    async def _async_receive_session_messages(self, client: MqttClient) -> None:
        """Drain the established session while preserving ordered delivery."""
        if (
            self._reserved_ingress_item is not None
            or len(self._message_queue) > _MAX_QUEUED_MESSAGES
        ):
            await self.async_wait_message_queue_idle()
            if self._stopping:
                return
        metadata_since_yield = 0
        metadata_bytes_since_yield = 0
        async for message in client.messages:
            if self._stopping:
                break
            full = len(self._message_queue) >= _MAX_QUEUED_MESSAGES
            raw = bytes(message.payload)
            metadata = self._enqueue_message(
                str(message.topic), raw, reserved_ingress=full
            )
            if metadata:
                # Captured broker metadata never enters the device FIFO. Let
                # a bounded ready burst reach the next device frame without
                # paying a whole busy HA loop turn per retained config.
                metadata_since_yield += 1
                metadata_bytes_since_yield += len(raw)
                if (
                    metadata_since_yield < _MAX_METADATA_MESSAGES_PER_TURN
                    and metadata_bytes_since_yield < _MAX_METADATA_BYTES_PER_TURN
                ):
                    continue
            elif full:
                await self.async_wait_message_queue_idle()
                # Device frames keep their existing ordered delivery turn.
            metadata_since_yield = 0
            metadata_bytes_since_yield = 0
            await asyncio.sleep(0)

    def _enqueue_message(
        self, topic: str, payload: bytes | str, *, reserved_ingress: bool = False
    ) -> bool:
        """Queue a frame, returning true only for lightweight captured traffic."""
        if self._stopping:
            return False
        if self._broker_metadata_capture is not None:
            raw = (
                payload.encode(errors="replace")
                if isinstance(payload, str)
                else payload
            )
            # Match our own publishes before the metadata fast path; otherwise
            # a captured echo would leave its fingerprint queued and appear as
            # foreign ingress. Ordinary direct calls still use _process_message.
            consume_echo = getattr(self, "_consume_self_publish_echo", None)
            if callable(consume_echo) and consume_echo(topic, raw):
                self._record_received_message(topic)
                self._self_publish_echoes_matched += 1
                return True
            try:
                metadata = self._broker_metadata_capture(topic, raw)
            except Exception as err:
                self._sink_errors += 1
                self._last_sink_error = f"{type(err).__name__}: {err}"
                _LOGGER.exception("Local Jackery MQTT metadata capture failed")
            else:
                if metadata:
                    self._record_received_message(topic)
                    if len(raw) > LOCAL_MQTT_MAX_PAYLOAD_BYTES:
                        if metadata == "own_export":
                            # This is our complete outgoing telemetry document,
                            # not an oversized inbound device message.
                            self._oversized_identified_own_export += 1
                        else:
                            self._payload_too_large_count += 1
                    self._messages_broker_metadata += 1
                    return True
        if len(self._message_queue) >= _MAX_QUEUED_MESSAGES:
            # Preserve callback bursts; async receivers apply backpressure.
            self._message_queue_full_count += 1
        item = (topic, payload)
        receipts = getattr(self, "_message_received_at", None)
        if receipts is None:
            receipts = self._message_received_at = {}
        receipts[id(item)] = time.monotonic()
        if reserved_ingress:
            self._reserved_ingress_item = item
        self._box_safety_ingress.add(item)
        self._message_queue.append(item)
        self._ensure_message_consumer()
        return False

    @staticmethod
    def _topic_filter_covers(covering: str, candidate: str) -> bool:
        """Return whether every topic matched by candidate is matched by covering."""
        cover_levels = covering.split("/")
        candidate_levels = candidate.split("/")
        index = 0
        while True:
            if index == len(cover_levels):
                return index == len(candidate_levels)
            cover = cover_levels[index]
            if cover == "#":
                return True
            if index == len(candidate_levels):
                return False
            item = candidate_levels[index]
            if item == "#":
                return False
            if cover not in {"+", item}:
                return False
            index += 1

    @classmethod
    def _minimal_subscription_topics(cls, topics: tuple[str, ...]) -> list[str]:
        """Remove exact and wildcard-overlapping MQTT subscription filters."""
        result: list[str] = []
        for topic in topics:
            if any(cls._topic_filter_covers(existing, topic) for existing in result):
                continue
            result = [
                existing
                for existing in result
                if not cls._topic_filter_covers(topic, existing)
            ]
            result.append(topic)
        return result

    def _ensure_background_message_drain(self) -> None:
        """Keep accepted frames draining after broker ingress has stopped."""
        self._hass.async_create_background_task(
            self.async_wait_message_queue_idle(),
            name="jackery_local_mqtt_message_drain",
            eager_start=False,
        )

    def _ensure_message_consumer(self) -> None:
        """Start the sole Local-MQTT FIFO consumer when work is queued."""
        if not self._message_queue and self._message_delivery_task is None:
            return
        current = self._message_consumer_task
        if current is not None and not current.done():
            return
        task = async_create_message_task(
            self._hass,
            self._config_entry,
            self._async_consume_messages(),
            name="jackery_local_mqtt_message_fifo",
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
        """Deliver every accepted local frame serially in broker order."""
        while True:
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
                name="jackery_local_mqtt_message_delivery",
                eager_start=True,
            )

    async def _async_deliver_message(self, item: tuple[str, bytes | str]) -> None:
        """Deliver one local frame exactly once despite owner cancellation."""
        topic, payload = item
        sink_task: asyncio.Task[None] = self._hass.async_create_background_task(
            self._process_message(topic, payload, self._box_safety_ingress.get(item)),
            name="jackery_local_mqtt_sink_delivery",
            eager_start=True,
        )
        if await self._async_wait_delivery_task(sink_task):
            self._sink_errors += 1
            self._messages_dropped += 1
            self._last_sink_error = "CancelledError: sink cancelled before completion"
            _LOGGER.error(
                "Local Jackery MQTT sink was cancelled before completing an "
                "accepted frame"
            )
            return
        try:
            sink_task.result()
        except Exception as err:  # defensive actor boundary
            self._sink_errors += 1
            self._last_sink_error = f"{type(err).__name__}: {err}"
            _LOGGER.exception("Local Jackery MQTT FIFO delivery failed")

    @staticmethod
    async def _async_wait_delivery_task(task: asyncio.Task[None]) -> bool:
        """Await a started sink once while swallowing cancellation of its owner."""
        while True:
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
            except TimeoutError, OSError:
                # Let the outer transport logic handle reconnects.
                break
        return task.cancelled()

    def _settle_message_delivery(self, task: asyncio.Task[None]) -> None:
        """Finish one delivery and requeue it only when it was cancelled."""
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
            _LOGGER.exception("Local Jackery MQTT delivery task failed")
        if item is not None:
            getattr(self, "_message_received_at", {}).pop(id(item), None)
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
                    "Local Jackery MQTT FIFO actor was cancelled with accepted "
                    "delivery still pending"
                )
        except Exception:
            _LOGGER.exception("Local Jackery MQTT FIFO consumer failed")

    async def async_wait_message_queue_idle(self) -> None:
        """Wait until every accepted frame has completed serial delivery."""
        while True:
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

    def _handle_connect_failure(self, rc: int) -> None:
        """Mark the client as disconnected after a broker CONNACK refusal and update diagnostic state.

        Parameters:
            rc (int): MQTT CONNACK return code provided by the broker indicating the reason for refusal.
        """  # ruff: ignore[line-too-long]
        self._connected = False
        reason = MQTT_CONNACK_REASONS.get(rc, "unknown")
        self._last_error = f"connect rc={rc} ({reason})"
        self._connected_event.set()
        _LOGGER.warning(
            "Jackery local MQTT connect rejected by %s:%s — %s",
            self._host,
            self._port,
            self._last_error,
        )

    def _handle_disconnect_error(self, error: str, was_connected: bool) -> None:
        """Record a connection setup failure or a disconnect and update the client's last-error state.

        Parameters:
            error (str): Human-readable error message to record.
            was_connected (bool): True if the client had already established a connection when the error occurred; False if the failure happened while attempting to connect.
        """  # ruff: ignore[line-too-long]
        if was_connected:
            self._last_error = f"disconnect: {error}"
            _LOGGER.debug("Jackery local MQTT disconnected: %s", error)
        else:
            self._last_error = f"connect failed: {error}"
            _LOGGER.debug("Jackery local MQTT connect setup failed: %s", error)

    @staticmethod
    def _extract_mqtt_code(err: MqttCodeError) -> int:
        """Extract the numeric MQTT return code from a MqttCodeError instance.

        Parameters:
            err (MqttCodeError): The exception object that may contain an `rc` attribute or an `rc.value` holding the numeric code.

        Returns:
            int: The MQTT return code if present, otherwise `0`.
        """  # ruff: ignore[line-too-long]
        rc = getattr(err, "rc", None)
        if isinstance(rc, int):
            return rc
        value = getattr(rc, "value", None)
        if isinstance(value, int):
            return value
        return 0

    async def _handle_message(
        self,
        topic: str,
        payload: bytes | bytearray | str,
    ) -> None:
        """Forward accepted ingress through the same awaited decoder as the FIFO.

        Keep unknown JSON fields and opaque bytes intact. Topic scoping happens
        at subscription, not in a second content filter after broker delivery.
        """
        await self._process_message(topic, payload)

    def _record_received_message(self, topic: str) -> None:
        """Count one received broker message without adding device-queue work."""
        if topic not in self._topics_seen_set:
            if len(self._topics_seen_set) < LOCAL_MQTT_MAX_TOPIC_NAMES:
                self._topics_seen_set.add(topic)
                self._topics_seen.append(topic)
            else:
                self._topics_seen_truncated = True
        self._messages_received += 1
        if topic.startswith("hb/device/"):
            parts = topic.split("/")
            if len(parts) == 4 and parts[2]:  # ruff: ignore[magic-value-comparison]
                if parts[3] == "status":
                    self._device_status_messages_received += 1
                elif parts[3] == "event":
                    self._device_event_messages_received += 1
        self._last_topic = topic
        self._last_message_at = self._utc_now_iso()

    async def _process_message(  # ruff: ignore[too-many-branches]
        self,
        topic: str,
        payload: bytes | bytearray | str,
        safety_receipt: BoxSafetyIngress | None = None,
    ) -> None:
        """Decode and forward one frame that already crossed acceptance."""
        item = getattr(self, "_message_delivery_item", None)
        received_at = (
            getattr(self, "_message_received_at", {}).get(id(item))
            if item is not None and item[0] == topic and item[1] is payload
            else None
        )
        if received_at is None:
            received_at = time.monotonic()
        raw = (
            payload.encode(errors="replace")
            if isinstance(payload, str)
            else bytes(payload)
        )
        self_echo = self._consume_self_publish_echo(topic, raw)
        self._self_publish_echoes_matched += int(self_echo)
        self._record_received_message(topic)
        if self_echo:
            return
        if len(raw) > LOCAL_MQTT_MAX_PAYLOAD_BYTES:
            self._payload_too_large_count += 1
        # No content gate here. docs/AGENTS.md §1.1 Data Integrity First:
        # "live MQTT/BLE ingress is not filtered or dropped merely because a
        # field is unknown or incomplete." Scoping is the topic filter's job;
        # a key-based gate also silently drops any field the firmware adds.
        data: dict[str, Any] | None = None
        try:
            parsed = json.loads(raw.decode())
            if isinstance(parsed, dict):
                data = with_config_receipt(parsed, received_at)
        except UnicodeDecodeError, json.JSONDecodeError:
            pass
        if data is None:
            raw = ReceivedBytes(raw, received_at)
        if self._sink is None:
            self._messages_dropped += 1
            return
        accepted: bool | Literal["broker_metadata"] | None
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            if safety_receipt is not None and self._box_safety_sink is not None:
                safety_receipt.active = True
                try:
                    accepted = await self._box_safety_sink(
                        topic, data, raw, safety_receipt
                    )
                finally:
                    safety_receipt.active = False
            else:
                accepted = await self._sink(topic, data, raw)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._sink_errors += 1
            self._messages_dropped += 1
            self._last_sink_error = f"{type(err).__name__}: {err}"
            _LOGGER.exception("Local Jackery MQTT sink failed")
            return
        if accepted == "broker_metadata":
            self._messages_broker_metadata += 1
            return
        if accepted is False:
            self._messages_rejected_by_sink += 1
            return
        self._messages_forwarded += 1

    @property
    def snapshot_requester_installed(self) -> bool:
        """Whether this listener has a configured read-only request callback."""
        return self._snapshot_requester is not None

    def set_snapshot_requester(
        self,
        requester: LocalMqttSnapshotRequester,
        *,
        interval_sec: float,
    ) -> None:
        """Request an initial snapshot and keep live counters on the same cadence."""
        self._snapshot_requester = requester
        self.set_snapshot_interval(interval_sec)
        self._schedule_snapshot_request()
        self._ensure_periodic_snapshot()

    def set_snapshot_interval(self, interval_sec: float) -> None:
        """Apply a shorter cadence without waiting out the previous sleep."""
        previous = self._snapshot_interval_sec
        self._snapshot_interval_sec = max(1.0, interval_sec)
        periodic = self._periodic_snapshot_task
        if (
            self._snapshot_interval_sec < previous
            and periodic is not None
            and not periodic.done()
        ):
            self._periodic_snapshot_task = None
            periodic.cancel()
            self._ensure_periodic_snapshot()

    @property
    def snapshot_interval_seconds(self) -> float:
        """Configured cadence of independent device requests."""
        return self._snapshot_interval_sec

    def _ensure_periodic_snapshot(self) -> None:
        """Start one entry-owned periodic requester while connected."""
        if (
            self._stopping
            or not self._connected
            or self._snapshot_requester is None
            or (
                self._periodic_snapshot_task is not None
                and not self._periodic_snapshot_task.done()
            )
        ):
            return
        self._periodic_snapshot_task = self._create_background_task(
            self._async_request_snapshots_periodically(),
            name="jackery_local_mqtt_periodic_snapshot",
        )

    async def _async_request_snapshots_periodically(self) -> None:
        """Request fresh official-protocol counters independently of HTTP."""
        try:
            while self._connected and not self._stopping:
                await asyncio.sleep(self._snapshot_interval_sec)
                if self._connected and not self._stopping:
                    self._schedule_snapshot_request()
        finally:
            if self._periodic_snapshot_task is asyncio.current_task():
                self._periodic_snapshot_task = None

    def _schedule_snapshot_request(self) -> None:
        """Schedule one coalesced snapshot request."""
        if self._stopping or not self._connected or self._snapshot_requester is None:
            return
        if self._snapshot_task is not None and not self._snapshot_task.done():
            self._snapshot_request_pending = True
            return
        self._snapshot_request_pending = False
        self._snapshot_task = self._create_background_task(
            self._async_request_snapshot(),
            name="jackery_local_mqtt_snapshot_request",
        )

    async def _async_request_snapshot(self) -> None:
        """Run one bounded official-protocol snapshot request."""
        try:
            requester = self._snapshot_requester
            if requester is None or self._stopping or not self._connected:
                return
            await requester()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # ruff: ignore[blind-except]
            self._last_error = f"{type(err).__name__}: {err}"
            _LOGGER.warning("Unable to request local Jackery MQTT snapshot: %s", err)
        finally:
            if self._snapshot_task is asyncio.current_task():
                self._snapshot_task = None
            if self._snapshot_request_pending:
                self._schedule_snapshot_request()

    async def async_publish(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        qos: MqttQos = 0,
        retain: bool = False,
        timeout_sec: float | None = None,
    ) -> None:
        """Publish one JSON request through the configured local broker."""
        text = json.dumps(payload, separators=(",", ":"))
        echo_record = self._register_self_publish_echo(topic, text.encode())
        client = self._client
        if client is None or not self._connected:
            self._discard_self_publish_echo(echo_record)
            self._publish_errors += 1
            self._last_publish_error = LocalMqttNotConnectedError.__name__
            raise LocalMqttNotConnectedError
        try:
            if timeout_sec is None:
                await client.publish(topic, text, qos=qos, retain=retain)
            else:
                # The deadline belongs inside this method. An outer timeout
                # converts the cancellation after our error handler exits and
                # leaves the MQTT session incorrectly marked as connected.
                async with asyncio.timeout(timeout_sec):
                    await client.publish(topic, text, qos=qos, retain=retain)
        except asyncio.CancelledError:
            self._discard_self_publish_echo(echo_record)
            raise
        except Exception as err:
            self._discard_self_publish_echo(echo_record)
            self._publish_errors += 1
            self._last_publish_error = type(err).__name__
            if (
                isinstance(err, MqttError | TimeoutError | OSError)
                and self._client is client
            ):
                self._connected = False
                self._connected_event.clear()
                self._publish_failed_event.set()
            raise
        self._messages_published += 1
        self._last_publish_at = self._utc_now_iso()

    def _purge_self_publish_echoes(self, *, now: float | None = None) -> None:
        """Expire bounded command fingerprints that never returned from the broker."""
        current = time.monotonic() if now is None else now
        while (
            self._pending_self_publish_echoes
            and self._pending_self_publish_echoes[0][0] <= current
        ):
            self._pending_self_publish_echoes.popleft()

    def _register_self_publish_echo(
        self,
        topic: str,
        raw: bytes,
    ) -> tuple[float, str, bytes]:
        """Register one exact, one-shot fingerprint before broker publication."""
        now = time.monotonic()
        self._purge_self_publish_echoes(now=now)
        record = (now + _SELF_PUBLISH_ECHO_TTL_SEC, topic, raw)
        self._pending_self_publish_echoes.append(record)
        while len(self._pending_self_publish_echoes) > _MAX_PENDING_SELF_PUBLISH_ECHOES:
            self._pending_self_publish_echoes.popleft()
        return record

    def _discard_self_publish_echo(
        self,
        record: tuple[float, str, bytes],
    ) -> None:
        """Remove a fingerprint when its corresponding publish did not complete."""
        with contextlib.suppress(ValueError):
            self._pending_self_publish_echoes.remove(record)

    def _consume_self_publish_echo(self, topic: str, raw: bytes) -> bool:
        """Consume one exact publish fingerprint without filtering other actions."""
        self._purge_self_publish_echoes()
        for record in self._pending_self_publish_echoes:
            _expires_at, published_topic, published_raw = record
            if published_topic == topic and published_raw == raw:
                self._pending_self_publish_echoes.remove(record)
                return True
        return False

    def diagnostics_snapshot(self, *, redact: bool | None = None) -> dict[str, Any]:
        """Produce a JSON-serializable snapshot of the client's runtime state for diagnostics.

        Parameters:
            redact (bool): If True, redact sensitive fields (host, port, and topic names); if False, include real host, port, and topic names.

        Returns:
            dict[str, Any]: A snapshot containing connection/configuration flags, topic diagnostics, message counters, last-seen timestamps/errors, connect attempts, and the MQTT client library identifier.
        """  # ruff: ignore[line-too-long]
        # Explicit annotation so the redacted (all-str) and unredacted (str + int
        # port) branches do not lock the inferred dict type to ``dict[str, str]``.
        target: dict[str, Any]
        if redact:
            target = {
                "host": REDACTED_VALUE,
                "port": REDACTED_VALUE,
            }
            last_topic: str | None = (
                REDACTED_VALUE if self._last_topic is not None else None
            )
            # Topic NAMES can contain device IDs / MAC fragments; redact them
            # in normal diagnostics exports. The count and ``topics_truncated``
            # flag are still useful to confirm the listener is receiving.
            topics = [REDACTED_VALUE for _ in self._topics_seen]
        else:
            target = {"host": self._host, "port": self._port}
            last_topic = self._last_topic
            topics = list(self._topics_seen)
        return {
            "enabled": True,
            "transport": "direct_mqtt",
            "library": "aiomqtt",
            "instance_started_at": self._instance_started_at,
            "configured_target": target,
            "subscribed": self._connected and self._subscription_active,
            "connected": self._connected,
            # Compatibility alias consumed by the coordinator diagnostic entity.
            "broker_connected": self._connected,
            "started": self._runner_task is not None,
            "reconnect_supervisor_active": bool(
                self._runner_task is not None and not self._runner_task.done()
            ),
            "subscription_retry_active": (
                any(not task.done() for task in self._subscription_tasks)
            ),
            "subscription_filter_count": len(self._subscribed_topics),
            "subscription_attempt_failure_count": sum(
                self._subscription_attempt_failures.values()
            ),
            "subscription_failures_by_topic": [
                {
                    "topic": REDACTED_VALUE if redact else topic,
                    "attempt_count": count,
                }
                for topic, count in sorted(self._subscription_attempt_failures.items())
            ],
            "requested_topics": (
                [REDACTED_VALUE] * len(self._requested_subscription_topics())
                if redact
                else self._requested_subscription_topics()
            ),
            "subscribed_topics": (
                [REDACTED_VALUE] * len(self._subscribed_topics)
                if redact
                else sorted(self._subscribed_topics)
            ),
            "snapshot_requester_installed": self._snapshot_requester is not None,
            # The direct broker requester follows the configured coordinator
            # cadence; the one-shot flag remains separate for diagnostics.
            "periodic_requests_active": bool(
                self._periodic_snapshot_task is not None
                and not self._periodic_snapshot_task.done()
            ),
            "snapshot_request_active": self._snapshot_task is not None,
            "topic_filter": self._topic_filter,
            "qos": self._qos,
            "retained_messages_dropped": self._retained_messages_dropped,
            "topics_seen_count": len(self._topics_seen),
            "topics_seen": topics,
            "topics_seen_truncated": self._topics_seen_truncated,
            "messages_received": self._messages_received,
            "device_status_messages_received": self._device_status_messages_received,
            "device_event_messages_received": self._device_event_messages_received,
            "messages_dropped": self._messages_dropped,
            "messages_forwarded": self._messages_forwarded,
            "messages_broker_metadata": self._messages_broker_metadata,
            "snapshot_interval_seconds": self.snapshot_interval_seconds,
            "messages_filtered": self._messages_filtered,
            "messages_published": self._messages_published,
            "self_publish_echoes_matched": self._self_publish_echoes_matched,
            "self_publish_echoes_ignored": self._self_publish_echoes_matched,
            "pending_self_publish_echoes": len(self._pending_self_publish_echoes),
            "publish_errors": self._publish_errors,
            "last_publish_at": self._last_publish_at,
            "last_publish_error": self._last_publish_error,
            "messages_rejected_by_sink": self._messages_rejected_by_sink,
            "sink_errors": self._sink_errors,
            "last_sink_error": self._last_sink_error,
            "last_topic": last_topic,
            "last_message_at": self._last_message_at,
            "last_connect_at": self._last_connect_at,
            "last_disconnect_at": self._last_disconnect_at,
            "last_error": self._last_error,
            "connect_attempts": self._connect_attempts,
            "home_assistant_event_count": self._home_assistant_event_count,
            "blocked_by_filter_count": self._blocked_by_filter_count,
            "payload_too_large_count": self._payload_too_large_count,
            "messages_oversized": self._payload_too_large_count,
            "messages_oversized_identified_own_export": self._oversized_identified_own_export,  # ruff: ignore[line-too-long]
            "own_export_oversized_counted_separately": True,
            "pending_message_tasks": len(self._message_tasks),
            "message_queue_unbounded": False,
            "message_queue_limit": _MAX_QUEUED_MESSAGES,
            "message_queue_ingress_reserve": 1,
            "message_queue_requeue_reserve": 1,
            "message_queue_full_count": self._message_queue_full_count,
            "message_queue_depth": len(self._message_queue),
            "message_consumer_running": bool(
                self._message_consumer_task is not None
                and not self._message_consumer_task.done()
            ),
            "message_delivery_running": bool(
                self._message_delivery_task is not None
                and not self._message_delivery_task.done()
            ),
        }

    def matches_configuration(
        self,
        settings: LocalMqttConnectionSettings | None = None,
        **connection_options: Unpack[_LocalMqttConnectionOptions],
    ) -> bool:
        """Whether the client already owns exactly this broker configuration."""
        settings = _connection_settings(settings, connection_options)
        return (
            settings.host == self._host
            and settings.port == self._port
            and settings.username == self._username
            and settings.password == self._password
            and settings.topic_filter == self._topic_filter
            and settings.qos == self._qos
        )

    def _should_drop_broad_noise_topic(self, topic: str) -> bool:
        """Return True for known high-volume non-device topics."""
        if topic.startswith("$SYS/"):
            return True
        if not self._is_broad_topic_filter():
            return False
        return topic.startswith("homeassistant/")

    @property
    def is_connected(self) -> bool:
        """Indicates whether the client currently has an active MQTT broker session.

        Returns:
            True if the client has an active MQTT broker session, False otherwise.
        """
        return self._connected

    @property
    def is_started(self) -> bool:
        """Report whether the background MQTT session runner task has been started.

        Returns:
            True if the runner task exists, False otherwise.
        """
        return self._runner_task is not None and not self._runner_task.done()

    @staticmethod
    def _utc_now_iso() -> str:
        """Return the current UTC time as an ISO 8601 string including the timezone offset.

        Returns:
            iso_timestamp (str): ISO 8601 formatted UTC timestamp including timezone offset (e.g. "2026-05-27T12:34:56+00:00").
        """  # ruff: ignore[line-too-long]
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _looks_like_home_assistant_state_event_payload(payload: bytes) -> bool:
        """Return True for high-volume HA state events on a shared topic."""
        head = payload[:_HOME_ASSISTANT_EVENT_HEAD_BYTES]
        return (
            b'"event_type"' in head
            and b'"state_changed"' in head
            and b'"event_data"' in head
            and b'"old_state"' in head
            and b'"new_state"' in head
        )

    @staticmethod
    def _extract_local_jackery_payload(
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return a Jackery payload from raw JSON or an HA-event wrapper."""
        if "event_type" not in payload or "event_data" not in payload:
            return payload
        event_data = payload.get("event_data")
        if not isinstance(event_data, dict):
            return None
        candidates = (
            event_data.get("payload"),
            event_data.get("body"),
            event_data.get("data"),
            event_data,
        )
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if _LOCAL_MQTT_JACKERY_MARKER_KEYS.intersection(candidate):
                return candidate
        return None

    def _is_broad_topic_filter(self) -> bool:
        """Return True when the current topic filter is globally broad."""
        return self._topic_filter in {"#", "+/#"}


_LOCAL_MQTT_RUNTIME_KEY = "local_mqtt_client"


def _local_mqtt_client(
    hass: HomeAssistant, entry: ConfigEntry
) -> JackeryLocalMqttClient | None:
    """Return the local MQTT adapter stored for a config entry."""
    coordinator = getattr(entry, "runtime_data", None)
    runtime_client = getattr(coordinator, "local_mqtt_client", None)
    if isinstance(runtime_client, JackeryLocalMqttClient):
        return runtime_client
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(bucket, dict):
        return None
    client = bucket.get(_LOCAL_MQTT_RUNTIME_KEY)
    return client if isinstance(client, JackeryLocalMqttClient) else None
