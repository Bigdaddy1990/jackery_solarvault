"""Direct local-broker MQTT transport for Jackery telemetry."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Literal, Self, TypedDict, Unpack, cast

from aiomqtt import Client as MqttClient, MqttError

from homeassistant.components.mqtt.util import valid_subscribe_topic
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.json import json_dumps

from ..const import (
    DOMAIN,
    LOCAL_MQTT_DEFAULT_TOPIC,
    LOCAL_MQTT_MAX_PAYLOAD_BYTES,
    LOCAL_MQTT_MAX_TOPIC_NAMES,
    LOCAL_MQTT_RECONNECT_FACTOR,
    LOCAL_MQTT_RECONNECT_INITIAL_SEC,
    LOCAL_MQTT_RECONNECT_MAX_SEC,
    REDACTED_VALUE,
)

_LOGGER = logging.getLogger(__name__)
_AIOMQTT_LOGGER = logging.getLogger(f"{__name__}.aiomqtt")

MqttQos = Literal[0, 1, 2]
LocalMqttSink = Callable[[str, dict[str, Any] | None, bytes], Awaitable[bool | None]]
LocalMqttSnapshotRequester = Callable[[], Awaitable[int]]
_DEFAULT_MQTT_PORT = 1883
_MAX_MQTT_PORT = 65_535
_SELF_PUBLISH_ECHO_TTL_SEC = 30.0
_MAX_PENDING_SELF_PUBLISH_ECHOES = 128
# MQTT 3.1.1 SUBACK: granted QoS 0..2, anything from 0x80 up means the broker
# refused the subscription (MQTT-3.9.3-2).
_SUBACK_FAILURE_CODE = 0x80


def _subscription_topic(topic_filter: str) -> str:
    """Normalize only the documented legacy default; preserve user topics."""
    topic = topic_filter.strip()
    return "homeassistant/#" if topic == "homeassistant" else topic


def subscription_refusals(codes: object) -> list[str]:
    """Return the SUBACK entries where the broker refused the subscription.

    ``aiomqtt.Client.subscribe`` only raises when the *local* paho call fails;
    a broker that refuses the filter answers with a failure reason code in the
    SUBACK and the coroutine returns normally. Ignoring that return value made
    an ACL-denied subscription look completely healthy: ``connected`` and
    ``subscribed`` both true, publishes flowing, and not a single frame ever
    delivered — not even our own, which the broker would otherwise echo back
    into the subscribed tree.

    MQTT 3.1.1 reports granted QoS as plain ints where ``0x80`` means failure;
    MQTT 5 returns ``ReasonCode`` objects exposing ``is_failure``.
    """
    refused: list[str] = []
    entries = codes if isinstance(codes, (list, tuple)) else ()
    for code in entries:
        is_failure = getattr(code, "is_failure", None)
        failed = (
            bool(is_failure)
            if is_failure is not None
            else isinstance(code, int) and code >= _SUBACK_FAILURE_CODE
        )
        if failed:
            refused.append(str(code))
    return refused


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
    """Receive and request Jackery frames on the configured local broker."""

    def __init__(
        self,
        hass: HomeAssistant,
        settings: LocalMqttConnectionSettings | None = None,
        *,
        sink: LocalMqttSink | None = None,
        config_entry: ConfigEntry | None = None,
        **connection_options: Unpack[_LocalMqttConnectionOptions],
    ) -> None:
        """Initialize the direct local-broker client."""
        settings = _connection_settings(settings, connection_options)
        self._hass = hass
        self._config_entry = config_entry
        self._host = settings.host.strip()
        self._port = settings.port
        self._username = settings.username or None
        self._password = settings.password or None
        self._client_id = settings.client_id
        self._sink = sink
        self._topic_filter = settings.topic_filter
        self._topic_filters = (_subscription_topic(settings.topic_filter),)
        if settings.qos not in {0, 1, 2}:
            raise LocalMqttConfigurationError.invalid_qos()
        self._qos = settings.qos
        self._lifecycle_lock = asyncio.Lock()
        self._subscription_active = self._snapshot_request_pending = False
        self._subscribed_topics: set[str] = set()
        self._client: MqttClient | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._connected_event = asyncio.Event()
        self._snapshot_task: asyncio.Task[None] | None = None
        self._periodic_snapshot_task: asyncio.Task[None] | None = None
        self._snapshot_interval_sec = 15.0
        self._snapshot_requester: LocalMqttSnapshotRequester | None = None
        self._message_queue: deque[tuple[str, bytes | str]] = deque()
        self._message_consumer_task: asyncio.Task[None] | None = None
        self._message_delivery_task: asyncio.Task[None] | None = None
        self._message_delivery_item: tuple[str, bytes | str] | None = None
        self._message_tasks: set[asyncio.Task[None]] = set()
        self._stopping = self._connected = self._topics_seen_truncated = False
        self._messages_received = self._messages_dropped = 0
        self._messages_forwarded = self._messages_filtered = 0
        self._messages_rejected_by_sink = self._sink_errors = 0
        self._payload_too_large_count = self._retained_messages_dropped = 0
        self._topics_seen: list[str] = []
        self._topics_seen_set: set[str] = set()
        self._last_topic: str | None = None
        self._last_message_at: str | None = None
        self._last_connect_at: str | None = None
        self._last_disconnect_at: str | None = None
        self._last_error: str | None = None
        self._last_sink_error: str | None = None
        self._connect_attempts = self._messages_published = self._publish_errors = 0
        self._last_publish_at: str | None = None
        self._pending_self_publish_echoes: deque[tuple[float, str, bytes]] = deque()
        self._self_publish_echoes_ignored = 0

    async def async_start(self) -> None:
        """Start the entry-owned direct-broker reconnect supervisor."""
        async with self._lifecycle_lock:
            self._stopping = False
            if self._runner_task is not None and not self._runner_task.done():
                return
            if not self._host:
                raise LocalMqttConfigurationError.missing_host()
            if not 1 <= self._port <= _MAX_MQTT_PORT:
                raise LocalMqttConfigurationError.invalid_port()
            valid_subscribe_topic(self._topic_filter)
            self._connected_event.clear()
            self._runner_task = self._create_background_task(
                self._async_run_forever(),
                name="jackery_local_mqtt_runner",
            )
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._connected_event.wait(), timeout=10.0)

    async def async_stop(self, *, wait_for_drain: bool = True) -> None:
        """Remove subscriptions and optionally await accepted-frame delivery."""
        self._stopping = True
        async with self._lifecycle_lock:
            await self._async_stop_locked(wait_for_drain=wait_for_drain)

    async def _async_stop_locked(self, *, wait_for_drain: bool) -> None:
        """Stop while holding the shared start/stop lifecycle fence."""
        self._stopping = True
        self._subscription_active = False
        periodic_task = self._periodic_snapshot_task
        self._periodic_snapshot_task = None
        if periodic_task is not None and periodic_task is not asyncio.current_task():
            periodic_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await periodic_task
        self._snapshot_request_pending = False
        snapshot_task = self._snapshot_task
        self._snapshot_task = None
        if snapshot_task is not None and snapshot_task is not asyncio.current_task():
            snapshot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await snapshot_task
        runner_task = self._runner_task
        self._runner_task = None
        if runner_task is not None and runner_task is not asyncio.current_task():
            runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, MqttError):
                await runner_task
        self._client = None
        self._subscribed_topics.clear()
        self._connected_event.clear()
        self._connected = False
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
        while True:
            self._connect_attempts += 1
            connected = await self._async_run_session()
            if connected:
                reconnect_delay = LOCAL_MQTT_RECONNECT_INITIAL_SEC
            await self._async_reconnect_sleep(reconnect_delay)
            if not connected:
                reconnect_delay = min(
                    reconnect_delay * LOCAL_MQTT_RECONNECT_FACTOR,
                    LOCAL_MQTT_RECONNECT_MAX_SEC,
                )

    @staticmethod
    async def _async_reconnect_sleep(delay: float) -> None:
        """Wait before retrying a failed direct-broker session."""
        await asyncio.sleep(delay)

    async def _async_run_session(self) -> bool:
        """Connect, subscribe and feed broker frames into the ordered FIFO."""
        connected = False
        topics = self._minimal_subscription_topics(self._topic_filters)
        try:
            async with MqttClient(
                hostname=self._host,
                port=self._port,
                identifier=self._client_id,
                username=self._username,
                password=self._password,
                logger=_AIOMQTT_LOGGER,
            ) as client:
                await self._async_consume_session(client, topics)
                connected = self._subscription_active
        except asyncio.CancelledError:
            raise
        except MqttError as err:
            connected = self._subscription_active
            error = f"{type(err).__name__}: {err}"
            log = _LOGGER.warning if error != self._last_error else _LOGGER.debug
            self._last_error = error
            log("Jackery local MQTT connection failed: %s", err)
        except Exception as err:  # ruff: ignore[blind-except]
            connected = self._subscription_active
            error = f"{type(err).__name__}: {err}"
            log = _LOGGER.warning if error != self._last_error else _LOGGER.debug
            self._last_error = error
            log("Jackery local MQTT session failed: %s", err)
        finally:
            self._client = None
            self._connected = False
            self._subscription_active = False
            self._subscribed_topics.clear()
            self._connected_event.set()
            if connected:
                self._last_disconnect_at = self._utc_now_iso()
            periodic_task = self._periodic_snapshot_task
            self._periodic_snapshot_task = None
            if (
                periodic_task is not None
                and periodic_task is not asyncio.current_task()
            ):
                periodic_task.cancel()
        return connected

    async def _async_consume_session(
        self,
        client: MqttClient,
        topics: list[str],
    ) -> None:
        """Subscribe and consume one established direct-broker session."""
        self._client = client
        for topic in topics:
            refused = subscription_refusals(
                await client.subscribe(topic, qos=self._qos)
            )
            if refused:
                msg = (
                    f"broker refused the subscription to {topic!r} "
                    f"(SUBACK {", ".join(refused)}) — check the broker ACL for "
                    f"read access on this topic tree"
                )
                raise MqttError(msg)
        self._subscribed_topics = set(topics)
        recovered = self._last_error is not None
        self._connected = True
        self._subscription_active = True
        self._last_connect_at = self._utc_now_iso()
        self._last_error = None
        self._connected_event.set()
        if recovered:
            _LOGGER.info("Jackery local MQTT connection restored")
        self._schedule_snapshot_request()
        self._ensure_periodic_snapshot()
        async for message in client.messages:
            if self._stopping:
                break
            self._enqueue_message(str(message.topic), bytes(message.payload))

    def _enqueue_message(self, topic: str, payload: bytes | str) -> None:
        """Accept one broker frame into the ordered no-drop FIFO."""
        if self._stopping:
            return
        self._message_queue.append((topic, payload))
        self._ensure_message_consumer()

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

    def _create_message_task(
        self,
        operation: Coroutine[Any, Any, None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        """Create finite message work owned by the config entry when available."""
        if self._config_entry is not None:
            return cast(  # ty: ignore[redundant-cast]
                "asyncio.Task[None]",
                self._config_entry.async_create_task(
                    self._hass,
                    operation,
                    name=name,
                    eager_start=False,
                ),
            )
        return self._hass.async_create_task(operation, name=name, eager_start=False)

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
        task = self._create_background_task(
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
                continue
            if not self._message_queue:
                return
            item = self._message_queue.popleft()
            self._message_delivery_item = item
            self._message_delivery_task = self._create_message_task(
                self._async_deliver_message(item),
                name="jackery_local_mqtt_message_delivery",
            )

    async def _async_deliver_message(self, item: tuple[str, bytes | str]) -> None:
        """Deliver one local frame exactly once despite owner cancellation."""
        topic, payload = item
        sink_task: asyncio.Task[None] = self._hass.async_create_task(
            self._process_message(topic, payload),
            name="jackery_local_mqtt_sink_delivery",
            eager_start=False,
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
        except Exception:
            _LOGGER.exception("Local Jackery MQTT delivery task failed")

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

    async def _handle_message(
        self, topic: str, payload: bytes | bytearray | str
    ) -> None:
        """Process one direct call unless lifecycle stop blocks new ingress."""
        if self._stopping:
            return
        await self._process_message(topic, payload)

    async def _process_message(
        self, topic: str, payload: bytes | bytearray | str
    ) -> None:
        """Decode and forward one frame that already crossed acceptance."""
        raw = (
            payload.encode(errors="replace")
            if isinstance(payload, str)
            else bytes(payload)
        )
        if self._consume_self_publish_echo(topic, raw):
            self._self_publish_echoes_ignored += 1
        if topic not in self._topics_seen_set:
            if len(self._topics_seen_set) < LOCAL_MQTT_MAX_TOPIC_NAMES:
                self._topics_seen_set.add(topic)
                self._topics_seen.append(topic)
            else:
                self._topics_seen_truncated = True
        self._messages_received += 1
        self._last_topic = topic
        self._last_message_at = self._utc_now_iso()
        if len(raw) > LOCAL_MQTT_MAX_PAYLOAD_BYTES:
            self._payload_too_large_count += 1
            self._messages_dropped += 1
            self._last_error = (
                f"MQTT payload exceeds {LOCAL_MQTT_MAX_PAYLOAD_BYTES} byte limit"
            )
            return
        # No content gate here. docs/AGENTS.md §1.1 Data Integrity First:
        # "live MQTT/BLE ingress is not filtered or dropped merely because a
        # field is unknown or incomplete." Scoping is the topic filter's job;
        # a key-based gate also silently drops any field the firmware adds.
        data: dict[str, Any] | None = None
        try:
            parsed = json.loads(raw.decode())
            if isinstance(parsed, dict):
                data = parsed
        except UnicodeDecodeError, json.JSONDecodeError:
            pass
        if self._sink is None:
            self._messages_dropped += 1
            return
        try:
            accepted = await self._sink(topic, data, raw)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            self._sink_errors += 1
            self._messages_dropped += 1
            self._last_sink_error = f"{type(err).__name__}: {err}"
            _LOGGER.exception("Local Jackery MQTT sink failed")
            return
        self._messages_forwarded += 1
        if accepted is False:
            self._messages_rejected_by_sink += 1

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
        """Update the independent Local MQTT request cadence."""
        self._snapshot_interval_sec = max(1.0, interval_sec)

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
    ) -> None:
        """Publish one JSON request through the configured local broker."""
        text = json.dumps(payload, separators=(",", ":"))
        echo_record = self._register_self_publish_echo(topic, text.encode())
        client = self._client
        if client is None or not self._connected:
            self._discard_self_publish_echo(echo_record)
            self._publish_errors += 1
            raise LocalMqttNotConnectedError
        try:
            await client.publish(
                topic,
                text,
                qos=qos,
                retain=retain,
            )
        except asyncio.CancelledError:
            self._discard_self_publish_echo(echo_record)
            raise
        except Exception:
            self._discard_self_publish_echo(echo_record)
            self._publish_errors += 1
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

    def diagnostics_snapshot(self, *, redact: bool = True) -> dict[str, Any]:
        """Return privacy-safe transport diagnostics."""
        topics = (
            [REDACTED_VALUE] * len(self._topics_seen)
            if redact
            else list(self._topics_seen)
        )
        return {
            "enabled": True,
            "transport": "direct_mqtt",
            "library": "aiomqtt",
            "configured_target": {
                "host": REDACTED_VALUE if redact else self._host,
                "port": self._port,
            },
            "subscribed": self.is_started,
            "connected": self._connected,
            # Compatibility alias consumed by the coordinator diagnostic entity.
            "broker_connected": self._connected,
            "started": self.is_started,
            "reconnect_supervisor_active": bool(
                self._runner_task is not None and not self._runner_task.done()
            ),
            "subscription_retry_active": self.is_started and not self._connected,
            "subscription_filter_count": len(self._subscribed_topics),
            "snapshot_requester_installed": self._snapshot_requester is not None,
            # The direct broker requester follows the configured coordinator
            # cadence; the one-shot flag remains separate for diagnostics.
            "periodic_requests_active": bool(
                self._periodic_snapshot_task is not None
                and not self._periodic_snapshot_task.done()
            ),
            "snapshot_request_active": self._snapshot_task is not None,
            "topic_filter": REDACTED_VALUE if redact else self._topic_filter,
            "qos": self._qos,
            "retained_messages_dropped": self._retained_messages_dropped,
            "topics_seen_count": len(self._topics_seen),
            "topics_seen": topics,
            "topics_seen_truncated": self._topics_seen_truncated,
            "messages_received": self._messages_received,
            "messages_dropped": self._messages_dropped,
            "messages_forwarded": self._messages_forwarded,
            "messages_filtered": self._messages_filtered,
            "messages_published": self._messages_published,
            "self_publish_echoes_ignored": self._self_publish_echoes_ignored,
            "pending_self_publish_echoes": len(self._pending_self_publish_echoes),
            "publish_errors": self._publish_errors,
            "last_publish_at": self._last_publish_at,
            "messages_rejected_by_sink": self._messages_rejected_by_sink,
            "sink_errors": self._sink_errors,
            "last_sink_error": self._last_sink_error,
            "last_topic": REDACTED_VALUE
            if redact and self._last_topic
            else self._last_topic,
            "last_message_at": self._last_message_at,
            "last_connect_at": self._last_connect_at,
            "last_disconnect_at": self._last_disconnect_at,
            "last_error": self._last_error,
            "connect_attempts": self._connect_attempts,
            "payload_too_large_count": self._payload_too_large_count,
            "messages_oversized": self._payload_too_large_count,
            "pending_message_tasks": len(self._message_tasks),
            "message_queue_unbounded": True,
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

    @property
    def is_connected(self) -> bool:
        """Whether the configured local broker is connected."""
        return self._connected

    @property
    def is_started(self) -> bool:
        """Whether the reconnect supervisor is running."""
        return self._runner_task is not None and not self._runner_task.done()

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(UTC).isoformat()


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
