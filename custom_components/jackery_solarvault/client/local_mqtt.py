"""Home Assistant MQTT adapter for local Jackery telemetry.

The integration deliberately shares Home Assistant's MQTT connection.  Owning a
second broker client here duplicates credentials, reconnect handling and network
resources, and can race Home Assistant during shutdown.
"""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
import contextlib
from datetime import UTC, datetime
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Literal, cast

from homeassistant.components import mqtt
from homeassistant.components.mqtt.util import valid_subscribe_topic

from ..const import (
    DOMAIN,
    FIELD_BODY,
    LOCAL_MQTT_DEFAULT_TOPIC,
    LOCAL_MQTT_MAX_PAYLOAD_BYTES,
    LOCAL_MQTT_MAX_TOPIC_NAMES,
    LOCAL_MQTT_RECONNECT_FACTOR,
    LOCAL_MQTT_RECONNECT_INITIAL_SEC,
    LOCAL_MQTT_RECONNECT_MAX_SEC,
    REDACTED_VALUE,
    SHELLY_RPC_EVENT_TOPIC,
)

if TYPE_CHECKING:
    from homeassistant.components.mqtt.models import ReceiveMessage
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

MqttQos = Literal[0, 1, 2]
LocalMqttSink = Callable[[str, dict[str, Any] | None, bytes], Awaitable[bool | None]]
LocalMqttSnapshotRequester = Callable[[], Awaitable[int]]
_SELF_PUBLISH_ECHO_TTL_SEC = 30.0
_MAX_PENDING_SELF_PUBLISH_ECHOES = 128


class JackeryLocalMqttClient:
    """Receive local Jackery frames through Home Assistant's MQTT client."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        sink: LocalMqttSink | None = None,
        topic_filter: str = LOCAL_MQTT_DEFAULT_TOPIC,
        qos: MqttQos = 0,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize an adapter without opening a network connection."""
        self._hass = hass
        self._config_entry = config_entry
        self._sink = sink
        self._topic_filter = topic_filter
        if qos not in {0, 1, 2}:
            raise ValueError("MQTT QoS must be 0, 1, or 2")
        self._qos = qos
        self._lifecycle_lock = asyncio.Lock()
        self._unsubscribe: Callable[[], None] | None = None
        self._topic_unsubscribes: list[tuple[str, Callable[[], None]]] = []
        self._unsubscribe_official_alias: Callable[[], None] | None = None
        self._unsubscribe_shelly_rpc: Callable[[], None] | None = None
        self._unsubscribe_status: Callable[[], None] | None = None
        self._subscription_active = False
        self._retry_task: asyncio.Task[None] | None = None
        self._snapshot_task: asyncio.Task[None] | None = None
        self._snapshot_requester: LocalMqttSnapshotRequester | None = None
        self._snapshot_request_pending = False
        self._message_queue: deque[tuple[str, bytes | str]] = deque()
        self._message_consumer_task: asyncio.Task[None] | None = None
        self._message_delivery_task: asyncio.Task[None] | None = None
        self._message_delivery_item: tuple[str, bytes | str] | None = None
        self._message_tasks: set[asyncio.Task[None]] = set()
        self._stopping = False
        self._mqtt_integration_available = False
        self._connected = False
        self._messages_received = 0
        self._messages_dropped = 0
        self._messages_forwarded = 0
        self._messages_filtered = 0
        self._messages_rejected_by_sink = 0
        self._sink_errors = 0
        self._payload_too_large_count = 0
        self._retained_messages_dropped = 0
        self._topics_seen: list[str] = []
        self._topics_seen_set: set[str] = set()
        self._topics_seen_truncated = False
        self._last_topic: str | None = None
        self._last_message_at: str | None = None
        self._last_connect_at: str | None = None
        self._last_disconnect_at: str | None = None
        self._last_error: str | None = None
        self._last_sink_error: str | None = None
        self._connect_attempts = 0
        self._messages_published = 0
        self._publish_errors = 0
        self._last_publish_at: str | None = None
        self._pending_self_publish_echoes: deque[tuple[float, str, bytes]] = deque()
        self._self_publish_echoes_ignored = 0

    async def async_start(self) -> None:
        """Register the subscription, retrying transient MQTT startup gaps."""
        async with self._lifecycle_lock:
            self._stopping = False
            if self._subscription_active or (
                self._retry_task is not None and not self._retry_task.done()
            ):
                return
            if self._has_subscription_cleanup_pending():
                cleanup_errors = self._unsubscribe_registered()
                if cleanup_errors:
                    self._schedule_subscription_retry()
                    return
            if not await self._async_subscribe_once() and not self._stopping:
                self._schedule_subscription_retry()

    async def async_stop(self) -> None:
        """Remove subscriptions and quiesce callbacks during entry unload."""
        # Fence ingress and cancel a retry that may currently own the lifecycle
        # lock before waiting for that same lock. This avoids a stop-vs-retry
        # deadlock while the lock still serializes every subscription mutation.
        self._stopping = True
        retry_task = self._retry_task
        if retry_task is not None and retry_task is not asyncio.current_task():
            retry_task.cancel()
        async with self._lifecycle_lock:
            await self._async_stop_locked()

    async def _async_stop_locked(self) -> None:
        """Stop while holding the shared start/stop lifecycle fence."""
        self._stopping = True
        self._subscription_active = False
        retry_task = self._retry_task
        if retry_task is not None and retry_task is not asyncio.current_task():
            retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await retry_task
        if self._retry_task is retry_task:
            self._retry_task = None
        self._snapshot_request_pending = False
        snapshot_task = self._snapshot_task
        self._snapshot_task = None
        if snapshot_task is not None and snapshot_task is not asyncio.current_task():
            snapshot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await snapshot_task
        stop_errors = self._unsubscribe_registered()
        await self.async_wait_message_queue_idle()
        self._connected = False
        if stop_errors:
            self._last_error = (
                f"{len(stop_errors)} Local MQTT unsubscribe callback(s) failed"
            )
            raise RuntimeError(self._last_error) from stop_errors[0]

    def _schedule_subscription_retry(self) -> None:
        """Own exactly one retry supervisor for a failed subscription lifecycle."""
        current = self._retry_task
        if current is not None and not current.done():
            return
        self._retry_task = self._hass.async_create_background_task(
            self._async_retry_subscription(),
            name="jackery_local_mqtt_subscription_retry",
            eager_start=False,
        )

    def _has_subscription_cleanup_pending(self) -> bool:
        """Return whether a failed subscribe/unsubscribe left retryable handles."""
        return bool(
            self._topic_unsubscribes
            or self._unsubscribe is not None
            or self._unsubscribe_status is not None
        )

    def _unsubscribe_registered(self) -> list[Exception]:
        """Run every owned unsubscribe and retain only callbacks that failed."""
        topic_unsubscribes = list(self._topic_unsubscribes)
        if self._unsubscribe is not None and not any(
            unsubscribe is self._unsubscribe
            for _topic, unsubscribe in topic_unsubscribes
        ):
            topic_unsubscribes.insert(0, (self._topic_filter, self._unsubscribe))
        failed: list[tuple[str, Callable[[], None], Exception]] = []
        for topic, unsubscribe in reversed(topic_unsubscribes):
            try:
                unsubscribe()
            except Exception as err:  # ruff: ignore[blind-except]
                failed.append((topic, unsubscribe, err))
        failed.reverse()
        self._topic_unsubscribes = [
            (topic, unsubscribe) for topic, unsubscribe, _err in failed
        ]
        self._unsubscribe = (
            self._topic_unsubscribes[0][1] if self._topic_unsubscribes else None
        )
        plural_default_topic = LOCAL_MQTT_DEFAULT_TOPIC.replace(
            "/device/",
            "/devices/",
        )
        self._unsubscribe_official_alias = next(
            (
                unsubscribe
                for topic, unsubscribe in self._topic_unsubscribes
                if topic in {LOCAL_MQTT_DEFAULT_TOPIC, plural_default_topic}
                and topic != self._topic_filter
            ),
            None,
        )
        self._unsubscribe_shelly_rpc = next(
            (
                unsubscribe
                for topic, unsubscribe in self._topic_unsubscribes
                if topic == SHELLY_RPC_EVENT_TOPIC and topic != self._topic_filter
            ),
            None,
        )
        errors = [err for _topic, _unsubscribe, err in failed]
        unsubscribe_status = self._unsubscribe_status
        if unsubscribe_status is not None:
            try:
                unsubscribe_status()
            except Exception as err:  # ruff: ignore[blind-except]
                errors.append(err)
            else:
                self._unsubscribe_status = None
        return errors

    async def _async_subscribe_once(self) -> bool:
        """Attempt one HA MQTT subscription registration."""
        self._connect_attempts += 1
        try:
            valid_subscribe_topic(self._topic_filter)
        except ValueError as err:
            self._last_error = f"Invalid MQTT topic filter: {err}"
            _LOGGER.error("Invalid local Jackery MQTT topic filter: %s", err)
            return False
        self._mqtt_integration_available = await mqtt.async_wait_for_mqtt_client(
            self._hass
        )
        if not self._mqtt_integration_available:
            self._last_error = "Home Assistant MQTT client unavailable"
            return False
        unsubscribe_status: Callable[[], None] | None = None
        plural_default_topic = LOCAL_MQTT_DEFAULT_TOPIC.replace(
            "/device/",
            "/devices/",
        )
        subscription_topics = self._minimal_subscription_topics((
            self._topic_filter,
            LOCAL_MQTT_DEFAULT_TOPIC,
            plural_default_topic,
            SHELLY_RPC_EVENT_TOPIC,
        ))
        for topic in subscription_topics:
            try:
                valid_subscribe_topic(topic)
            except ValueError as err:
                self._last_error = f"Invalid MQTT topic filter: {err}"
                _LOGGER.error("Invalid local Jackery MQTT topic filter: %s", err)
                return False
        topic_unsubscribes: list[tuple[str, Callable[[], None]]] = []
        try:
            unsubscribe_status = mqtt.async_subscribe_connection_status(
                self._hass, self._async_connection_status_changed
            )
            for topic in subscription_topics:
                unsubscribe = await mqtt.async_subscribe(
                    self._hass,
                    topic,
                    self._async_message_received,
                    qos=self._qos,
                    encoding=None,
                )
                topic_unsubscribes.append((topic, unsubscribe))
        except asyncio.CancelledError:
            self._subscription_active = False
            self._topic_unsubscribes = topic_unsubscribes
            self._unsubscribe = topic_unsubscribes[0][1] if topic_unsubscribes else None
            self._unsubscribe_status = unsubscribe_status
            cleanup_errors = self._unsubscribe_registered()
            if cleanup_errors:
                _LOGGER.warning(
                    "Local Jackery MQTT subscription cancellation left %d "
                    "cleanup callback(s) retryable",
                    len(cleanup_errors),
                )
            raise
        except Exception as err:  # ruff: ignore[blind-except]
            self._subscription_active = False
            self._topic_unsubscribes = topic_unsubscribes
            self._unsubscribe = topic_unsubscribes[0][1] if topic_unsubscribes else None
            self._unsubscribe_status = unsubscribe_status
            cleanup_errors = self._unsubscribe_registered()
            cleanup_suffix = (
                f"; {len(cleanup_errors)} cleanup callback(s) will retry"
                if cleanup_errors
                else ""
            )
            self._last_error = f"{type(err).__name__}: {err}{cleanup_suffix}"
            _LOGGER.warning("Unable to subscribe to local Jackery MQTT: %s", err)
            return False
        assert topic_unsubscribes
        assert unsubscribe_status is not None
        if self._stopping:
            self._subscription_active = False
            self._topic_unsubscribes = topic_unsubscribes
            self._unsubscribe = topic_unsubscribes[0][1]
            self._unsubscribe_status = unsubscribe_status
            cleanup_errors = self._unsubscribe_registered()
            if cleanup_errors:
                _LOGGER.warning(
                    "Local Jackery MQTT stop overlapped subscription setup and "
                    "left %d cleanup callback(s) retryable",
                    len(cleanup_errors),
                )
            return False
        self._topic_unsubscribes = topic_unsubscribes
        self._unsubscribe = topic_unsubscribes[0][1]
        self._unsubscribe_official_alias = next(
            (
                unsubscribe
                for topic, unsubscribe in topic_unsubscribes
                if topic in {LOCAL_MQTT_DEFAULT_TOPIC, plural_default_topic}
                and topic != self._topic_filter
            ),
            None,
        )
        self._unsubscribe_shelly_rpc = next(
            (
                unsubscribe
                for topic, unsubscribe in topic_unsubscribes
                if topic == SHELLY_RPC_EVENT_TOPIC and topic != self._topic_filter
            ),
            None,
        )
        self._unsubscribe_status = unsubscribe_status
        self._subscription_active = True
        self._connected = mqtt.is_connected(self._hass)
        if self._connected:
            self._last_connect_at = self._utc_now_iso()
            self._schedule_snapshot_request()
        self._last_error = None
        return True

    async def _async_retry_subscription(self) -> None:
        """Retry subscription registration with capped exponential backoff."""
        delay = float(LOCAL_MQTT_RECONNECT_INITIAL_SEC)
        try:
            while not self._stopping:
                await asyncio.sleep(delay)
                if await self._async_retry_subscription_once():
                    return
                delay = min(
                    delay * LOCAL_MQTT_RECONNECT_FACTOR,
                    float(LOCAL_MQTT_RECONNECT_MAX_SEC),
                )
        finally:
            current_task = asyncio.current_task()
            if self._retry_task is current_task:
                self._retry_task = None

    async def _async_retry_subscription_once(self) -> bool:
        """Return whether retry supervision should stop."""
        async with self._lifecycle_lock:
            if self._stopping or self._subscription_active:
                return True
            if self._has_subscription_cleanup_pending():
                if self._unsubscribe_registered():
                    return False
            return await self._async_subscribe_once()

    def _async_connection_status_changed(self, connected: bool) -> None:
        """Observe the shared broker status without controlling it."""
        if connected == self._connected:
            return
        self._connected = connected
        if connected:
            self._last_connect_at = self._utc_now_iso()
            self._schedule_snapshot_request()
        else:
            self._last_disconnect_at = self._utc_now_iso()
            snapshot_task = self._snapshot_task
            if snapshot_task is not None and not snapshot_task.done():
                snapshot_task.cancel()

    def _async_message_received(self, message: ReceiveMessage) -> None:
        """Accept one live HA MQTT frame into the ordered delivery FIFO."""
        if self._stopping:
            return
        if message.retain:
            # Jackery publishes live telemetry with retain=False. Replayed
            # retained frames are stale broker state and must not update devices.
            self._retained_messages_dropped += 1
            self._messages_dropped += 1
            return
        payload = (
            message.payload
            if isinstance(message.payload, str)
            else bytes(message.payload)
        )
        self._message_queue.append((str(message.topic), payload))
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
            return cast(
                asyncio.Task[None],
                self._config_entry.async_create_task(
                    self._hass,
                    operation,
                    name=name,
                    eager_start=False,
                ),
            )
        return self._hass.async_create_task(operation, name=name, eager_start=False)

    def _ensure_message_consumer(self) -> None:
        """Start the sole Local-MQTT FIFO consumer when work is queued."""
        if not self._message_queue and self._message_delivery_task is None:
            return
        current = self._message_consumer_task
        if current is not None and not current.done():
            return
        task = self._create_message_task(
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
            except Exception:
                break
        return task.cancelled()

    def _settle_message_delivery(self, task: asyncio.Task[None]) -> None:
        """Finish one delivery identity and requeue it only if delivery was cancelled."""
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
            return
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
        data: dict[str, Any] | None = None
        try:
            parsed = json.loads(raw.decode())
            if isinstance(parsed, dict):
                data = parsed
        except UnicodeDecodeError, json.JSONDecodeError:
            pass
        if topic == SHELLY_RPC_EVENT_TOPIC:
            rpc_body: dict[str, Any] | None = None
            if data is not None:
                nested = data.get(FIELD_BODY)
                rpc_body = nested if isinstance(nested, dict) else data
            params = rpc_body.get("params") if rpc_body is not None else None
            if (
                rpc_body is None
                or rpc_body.get("method") != "NotifyStatus"
                or not str(rpc_body.get("src", "")).casefold().startswith("shelly")
                or not isinstance(params, dict)
                or not any(key in params for key in ("em:0", "emdata:0"))
            ):
                self._messages_filtered += 1
                return
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
        if accepted is False:
            self._messages_rejected_by_sink += 1
            self._messages_dropped += 1
            return
        self._messages_forwarded += 1

    def set_snapshot_requester(
        self,
        requester: LocalMqttSnapshotRequester,
    ) -> None:
        """Request one initial snapshot now and after real reconnects."""
        self._snapshot_requester = requester
        self._schedule_snapshot_request()

    def _schedule_snapshot_request(self) -> None:
        """Schedule one coalesced snapshot without a recurring timer."""
        if self._stopping or not self._connected or self._snapshot_requester is None:
            return
        if self._snapshot_task is not None and not self._snapshot_task.done():
            self._snapshot_request_pending = True
            return
        self._snapshot_request_pending = False
        self._snapshot_task = self._hass.async_create_background_task(
            self._async_request_snapshot(),
            name="jackery_local_mqtt_snapshot_request",
            eager_start=False,
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
        """Publish one JSON request through Home Assistant's MQTT connection."""
        text = json.dumps(payload, separators=(",", ":"))
        echo_record = self._register_self_publish_echo(topic, text.encode())
        try:
            await mqtt.async_publish(
                self._hass,
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
        subscribed_topics = {topic for topic, _unsubscribe in self._topic_unsubscribes}
        plural_default_topic = LOCAL_MQTT_DEFAULT_TOPIC.replace(
            "/device/",
            "/devices/",
        )
        singular_subscription_active = any(
            self._topic_filter_covers(topic, LOCAL_MQTT_DEFAULT_TOPIC)
            for topic in subscribed_topics
        )
        plural_subscription_active = any(
            self._topic_filter_covers(topic, plural_default_topic)
            for topic in subscribed_topics
        )
        return {
            "enabled": True,
            "transport": "homeassistant.components.mqtt",
            "library": "homeassistant.components.mqtt",
            "mqtt_integration_available": self._mqtt_integration_available,
            "subscribed": self.is_started,
            "connected": self._connected,
            # Compatibility alias consumed by the coordinator diagnostic
            # entity. Both describe the HA-owned broker connection.
            "broker_connected": self._connected,
            "started": self.is_started,
            "subscription_retry_active": self._retry_task is not None,
            "subscription_filter_count": len(self._topic_unsubscribes),
            "official_subscription_active": (
                singular_subscription_active and plural_subscription_active
            ),
            "official_singular_subscription_active": singular_subscription_active,
            "official_plural_subscription_active": plural_subscription_active,
            "snapshot_requester_installed": self._snapshot_requester is not None,
            # Kept as an explicit compatibility diagnostic: recurring writes
            # are intentionally disabled. ``snapshot_request_active`` is the
            # only adapter-owned outbound request state.
            "periodic_requests_active": False,
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
        self, topic_filters: tuple[str, ...], qos: MqttQos | None = None
    ) -> bool:
        """Whether the adapter already owns exactly this subscription."""
        return topic_filters == (self._topic_filter,) and (
            qos is None or qos == self._qos
        )

    @property
    def is_connected(self) -> bool:
        """Whether Home Assistant's shared broker is connected."""
        return self._connected

    @property
    def is_started(self) -> bool:
        """Whether the HA MQTT subscription is registered."""
        return self._subscription_active

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(UTC).isoformat()


_LOCAL_MQTT_RUNTIME_KEY = "local_mqtt_client"


def _local_mqtt_client(
    hass: HomeAssistant, entry: Any
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
