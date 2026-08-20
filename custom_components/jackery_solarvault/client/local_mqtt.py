"""Home Assistant MQTT adapter for local Jackery telemetry.

The integration deliberately shares Home Assistant's MQTT connection.  Owning a
second broker client here duplicates credentials, reconnect handling and network
resources, and can race Home Assistant during shutdown.
"""

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from datetime import UTC, datetime
import json
import logging
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.components import mqtt
from homeassistant.components.mqtt.util import valid_subscribe_topic

from ..const import (
    DOMAIN,
    LOCAL_MQTT_MAX_PAYLOAD_BYTES,
    LOCAL_MQTT_MAX_TOPIC_NAMES,
    LOCAL_MQTT_RECONNECT_FACTOR,
    LOCAL_MQTT_RECONNECT_INITIAL_SEC,
    LOCAL_MQTT_RECONNECT_MAX_SEC,
    REDACTED_VALUE,
)

if TYPE_CHECKING:
    from homeassistant.components.mqtt.models import ReceiveMessage
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

LOCAL_MQTT_DEFAULT_TOPIC = "homeassistant/#"
MqttQos = Literal[0, 1, 2]
LocalMqttSink = Callable[[str, dict[str, Any] | None, bytes], Awaitable[bool | None]]


class JackeryLocalMqttClient:
    """Receive local Jackery frames through Home Assistant's MQTT client."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        sink: LocalMqttSink | None = None,
        topic_filter: str = LOCAL_MQTT_DEFAULT_TOPIC,
        qos: MqttQos = 0,
    ) -> None:
        """Initialize an adapter without opening a network connection."""
        self._hass = hass
        self._sink = sink
        self._topic_filter = topic_filter
        if qos not in (0, 1, 2):
            raise ValueError("MQTT QoS must be 0, 1, or 2")
        self._qos = qos
        self._lifecycle_lock = asyncio.Lock()
        self._unsubscribe: Callable[[], None] | None = None
        self._unsubscribe_status: Callable[[], None] | None = None
        self._retry_task: asyncio.Task[None] | None = None
        self._message_tasks: set[asyncio.Task[Any]] = set()
        self._stopping = False
        self._mqtt_integration_available = False
        self._connected = False
        self._messages_received = 0
        self._messages_dropped = 0
        self._messages_forwarded = 0
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

    async def async_start(self) -> None:
        """Register the subscription, retrying transient MQTT startup gaps."""
        async with self._lifecycle_lock:
            self._stopping = False
            if self._unsubscribe is not None or (
                self._retry_task is not None and not self._retry_task.done()
            ):
                return
            if not await self._async_subscribe_once():
                self._retry_task = self._hass.async_create_background_task(
                    self._async_retry_subscription(),
                    name="jackery_local_mqtt_subscription_retry",
                    eager_start=False,
                )

    async def async_stop(self) -> None:
        """Remove subscriptions and quiesce callbacks during entry unload."""
        self._stopping = True
        retry_task = self._retry_task
        self._retry_task = None
        if retry_task is not None and retry_task is not asyncio.current_task():
            retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await retry_task
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._unsubscribe_status is not None:
            self._unsubscribe_status()
            self._unsubscribe_status = None
        current_task = asyncio.current_task()
        tasks = tuple(
            task for task in self._message_tasks if task is not current_task
        )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._connected = False

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
        unsubscribe: Callable[[], None] | None = None
        try:
            unsubscribe_status = mqtt.async_subscribe_connection_status(
                self._hass, self._async_connection_status_changed
            )
            unsubscribe = await mqtt.async_subscribe(
                self._hass,
                self._topic_filter,
                self._async_message_received,
                qos=self._qos,
                encoding=None,
            )
        except asyncio.CancelledError:
            if unsubscribe is not None:
                unsubscribe()
            if unsubscribe_status is not None:
                unsubscribe_status()
            raise
        except Exception as err:  # ruff: ignore[blind-except]
            if unsubscribe is not None:
                unsubscribe()
            if unsubscribe_status is not None:
                unsubscribe_status()
            self._last_error = f"{type(err).__name__}: {err}"
            _LOGGER.warning("Unable to subscribe to local Jackery MQTT: %s", err)
            return False
        assert unsubscribe is not None
        assert unsubscribe_status is not None
        if self._stopping:
            unsubscribe()
            unsubscribe_status()
            return False
        self._unsubscribe = unsubscribe
        self._unsubscribe_status = unsubscribe_status
        self._connected = mqtt.is_connected(self._hass)
        if self._connected:
            self._last_connect_at = self._utc_now_iso()
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
            self._retry_task = None

    async def _async_retry_subscription_once(self) -> bool:
        """Return whether retry supervision should stop."""
        if self._stopping or self._unsubscribe is not None:
            return True
        return await self._async_subscribe_once()

    def _async_connection_status_changed(self, connected: bool) -> None:
        """Observe the shared broker status without controlling it."""
        if connected == self._connected:
            return
        self._connected = connected
        if connected:
            self._last_connect_at = self._utc_now_iso()
        else:
            self._last_disconnect_at = self._utc_now_iso()

    async def _async_message_received(self, message: ReceiveMessage) -> None:
        """Normalize an HA MQTT message and track the in-flight callback."""
        if self._stopping:
            return
        task = asyncio.current_task()
        if task is not None:
            self._message_tasks.add(task)
        try:
            if message.retain:
                # Jackery publishes live telemetry with retain=False. Replayed
                # retained frames are stale broker state and must not update devices.
                self._retained_messages_dropped += 1
                self._messages_dropped += 1
                return
            await self._handle_message(str(message.topic), message.payload)
        finally:
            if task is not None:
                self._message_tasks.discard(task)

    async def _handle_message(
        self, topic: str, payload: bytes | bytearray | str
    ) -> None:
        """Decode JSON when possible and forward every broker-selected frame."""
        if self._stopping:
            return
        if topic not in self._topics_seen_set:
            if len(self._topics_seen_set) < LOCAL_MQTT_MAX_TOPIC_NAMES:
                self._topics_seen_set.add(topic)
                self._topics_seen.append(topic)
            else:
                self._topics_seen_truncated = True
        raw = (
            payload.encode(errors="replace")
            if isinstance(payload, str)
            else bytes(payload)
        )
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

    def diagnostics_snapshot(self, *, redact: bool = True) -> dict[str, Any]:
        """Return privacy-safe transport diagnostics."""
        topics = (
            [REDACTED_VALUE] * len(self._topics_seen)
            if redact
            else list(self._topics_seen)
        )
        return {
            "enabled": True,
            "transport": "homeassistant.components.mqtt",
            "library": "homeassistant.components.mqtt",
            "mqtt_integration_available": self._mqtt_integration_available,
            "subscribed": self.is_started,
            "connected": self._connected,
            "started": self.is_started,
            "subscription_retry_active": self._retry_task is not None,
            "topic_filter": REDACTED_VALUE if redact else self._topic_filter,
            "qos": self._qos,
            "retained_messages_dropped": self._retained_messages_dropped,
            "topics_seen_count": len(self._topics_seen),
            "topics_seen": topics,
            "topics_seen_truncated": self._topics_seen_truncated,
            "messages_received": self._messages_received,
            "messages_dropped": self._messages_dropped,
            "messages_forwarded": self._messages_forwarded,
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
        return self._unsubscribe is not None

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
