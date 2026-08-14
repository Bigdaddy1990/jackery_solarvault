"""Local third-party MQTT receiver for Jackery SolarVault.

This module is independent from :mod:`.mqtt_push`, which owns Jackery's cloud
broker connection and fixed ``hb/app/<userId>/...`` topics. Local MQTT reuses
Home Assistant's configured MQTT integration and subscribes exactly the topic
filter selected by the user. It never opens a second broker connection and it
never publishes Jackery commands to the local broker.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import json
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import mqtt
from homeassistant.components.mqtt.models import ReceiveMessage
from homeassistant.core import callback

from ..const import (
    LOCAL_MQTT_DEFAULT_TOPIC,
    LOCAL_MQTT_MAX_PAYLOAD_BYTES,
    LOCAL_MQTT_MAX_TOPIC_NAMES,
    LOCAL_MQTT_RECONNECT_FACTOR,
    LOCAL_MQTT_RECONNECT_INITIAL_SEC,
    LOCAL_MQTT_RECONNECT_MAX_SEC,
    REDACTED_VALUE,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .. import JackeryConfigEntry

_LOGGER = logging.getLogger(__name__)

LocalMqttSink = Callable[
    [str, dict[str, Any] | None, bytes],
    Awaitable[bool | None],
]

# Only the receiver topic belongs to the HA MQTT subscription. Broker address
# and credentials remain device-side 3046/3047 settings and are deliberately
# not part of this transport's identity.
type LocalMqttConfiguration = tuple[str]


class JackeryLocalMqttClient:
    """Receive local Jackery frames through Home Assistant's MQTT client."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        sink: LocalMqttSink | None = None,
        topic_filter: str = LOCAL_MQTT_DEFAULT_TOPIC,
    ) -> None:
        """Initialize an inactive Home Assistant MQTT subscription."""
        self._hass = hass
        self._sink = sink
        self._topic_filter = topic_filter
        self._lock = asyncio.Lock()
        self._unsubscribe: Callable[[], None] | None = None
        self._unsubscribe_connection_status: Callable[[], None] | None = None
        self._retry_task: asyncio.Task[None] | None = None
        self._message_tasks: set[asyncio.Task[Any]] = set()
        self._stopping = False
        self._mqtt_available = False
        self._connected = False
        self._subscribed = False
        self._messages_received = 0
        self._messages_dropped = 0
        self._messages_forwarded = 0
        self._messages_rejected_by_sink = 0
        self._messages_oversized = 0
        self._sink_errors = 0
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

    def matches_configuration(self, configuration: LocalMqttConfiguration) -> bool:
        """Return whether this receiver already uses the requested topic."""
        return configuration == (self._topic_filter,)

    async def async_start(self) -> None:
        """Subscribe through Home Assistant's configured MQTT integration."""
        async with self._lock:
            if self._unsubscribe is not None:
                return
            if self._retry_task is not None and not self._retry_task.done():
                return
            self._stopping = False
            if await self._async_subscribe_once():
                return
            self._retry_task = self._hass.async_create_background_task(
                self._async_retry_subscription(),
                name="jackery_local_mqtt_subscription_retry",
                eager_start=False,
            )

    async def _async_subscribe_once(self) -> bool:
        """Try once to register the shared HA MQTT subscription."""
        self._connect_attempts += 1
        self._last_error = None
        if not await mqtt.async_wait_for_mqtt_client(self._hass):
            self._mqtt_available = False
            self._connected = False
            self._subscribed = False
            self._last_error = "Home Assistant MQTT client is unavailable"
            _LOGGER.warning(
                "Jackery local MQTT cannot subscribe to %r because the Home "
                "Assistant MQTT integration is unavailable; retrying independently",
                self._topic_filter,
            )
            return False

        self._mqtt_available = True
        self._unsubscribe_connection_status = mqtt.async_subscribe_connection_status(
            self._hass,
            self._async_connection_status_changed,
        )
        try:
            self._unsubscribe = await mqtt.async_subscribe(
                self._hass,
                self._topic_filter,
                self._async_message_received,
                qos=0,
                encoding=None,
            )
        except asyncio.CancelledError:
            self._remove_connection_status_subscription()
            raise
        except Exception as err:  # ruff: ignore[blind-except]
            self._remove_connection_status_subscription()
            self._subscribed = False
            self._last_error = f"subscribe failed: {type(err).__name__}: {err}"
            _LOGGER.warning(
                "Jackery local MQTT subscribe failed for %r: %s; retrying independently",
                self._topic_filter,
                err,
            )
            return False

        self._subscribed = True
        self._connected = mqtt.is_connected(self._hass)
        if self._connected:
            self._last_connect_at = self._utc_now_iso()
        _LOGGER.info(
            "Jackery local MQTT subscribed through Home Assistant to %r",
            self._topic_filter,
        )
        return True

    async def _async_retry_subscription(self) -> None:
        """Retry only subscription registration; HA owns broker reconnects."""
        delay = LOCAL_MQTT_RECONNECT_INITIAL_SEC
        try:
            while not self._stopping:
                await asyncio.sleep(delay)
                if await self._async_retry_subscription_once():
                    return
                delay = min(
                    delay * LOCAL_MQTT_RECONNECT_FACTOR,
                    LOCAL_MQTT_RECONNECT_MAX_SEC,
                )
        finally:
            if self._retry_task is asyncio.current_task():
                self._retry_task = None

    async def _async_retry_subscription_once(self) -> bool:
        """Return whether retrying should stop after one locked attempt."""
        async with self._lock:
            if self._stopping or self._unsubscribe is not None:
                return True
            return await self._async_subscribe_once()

    async def async_stop(self) -> None:
        """Remove the Home Assistant MQTT and connection-state subscriptions."""
        self._stopping = True
        retry_task = self._retry_task
        self._retry_task = None
        if retry_task is not None and retry_task is not asyncio.current_task():
            retry_task.cancel()
            await asyncio.gather(retry_task, return_exceptions=True)
        async with self._lock:
            unsubscribe = self._unsubscribe
            self._unsubscribe = None
            if unsubscribe is not None:
                unsubscribe()
            self._remove_connection_status_subscription()
            if self._connected:
                self._last_disconnect_at = self._utc_now_iso()
            self._mqtt_available = False
            self._connected = False
            self._subscribed = False
        message_tasks = tuple(
            task for task in self._message_tasks if task is not asyncio.current_task()
        )
        for task in message_tasks:
            task.cancel()
        if message_tasks:
            await asyncio.gather(*message_tasks, return_exceptions=True)

    @callback
    def _async_connection_status_changed(self, connected: bool) -> None:
        """Track the shared Home Assistant MQTT connection state."""
        if connected == self._connected:
            return
        self._connected = connected
        if connected:
            self._last_connect_at = self._utc_now_iso()
            self._last_error = None
        else:
            self._last_disconnect_at = self._utc_now_iso()

    @callback
    def _remove_connection_status_subscription(self) -> None:
        """Remove the shared-client connection-state callback once."""
        unsubscribe = self._unsubscribe_connection_status
        self._unsubscribe_connection_status = None
        if unsubscribe is not None:
            unsubscribe()

    async def _async_message_received(self, message: ReceiveMessage) -> None:
        """Forward one Home Assistant MQTT message to the local sink."""
        if self._stopping:
            return
        current_task = asyncio.current_task()
        if current_task is not None:
            self._message_tasks.add(current_task)
        payload = message.payload
        if isinstance(payload, str):
            candidate: bytes | bytearray | str = payload
        elif isinstance(payload, bytes):
            candidate = payload
        else:
            candidate = bytes(payload)
        try:
            await self._handle_message(message.topic, candidate)
        finally:
            if current_task is not None:
                self._message_tasks.discard(current_task)

    async def _handle_message(
        self,
        topic: str,
        payload: bytes | bytearray | str,
    ) -> None:
        """Record and forward every broker-selected payload without filtering."""
        if self._stopping:
            return
        if topic not in self._topics_seen_set:
            if len(self._topics_seen_set) < LOCAL_MQTT_MAX_TOPIC_NAMES:
                self._topics_seen_set.add(topic)
                self._topics_seen.append(topic)
                _LOGGER.debug("Jackery local MQTT: first message on topic %r", topic)
            else:
                self._topics_seen_truncated = True
        self._messages_received += 1
        self._last_topic = topic
        self._last_message_at = self._utc_now_iso()

        if isinstance(payload, str):
            raw_bytes = payload.encode("utf-8", errors="replace")
            text: str | None = payload
        else:
            raw_bytes = payload if isinstance(payload, bytes) else bytes(payload)
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = None

        if len(raw_bytes) > LOCAL_MQTT_MAX_PAYLOAD_BYTES:
            # Retain the diagnostic signal, but still forward the untouched
            # frame. The receiver must not silently discard broker-selected
            # device data; semantic validation belongs to shared ingest.
            self._messages_oversized += 1

        data: dict[str, Any] | None = None
        if text is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError, ValueError:
                pass
            else:
                if isinstance(parsed, dict):
                    data = parsed

        if self._sink is None:
            self._messages_dropped += 1
            return
        try:
            accepted = await self._sink(topic, data, raw_bytes)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # sink is the integration boundary
            self._sink_errors += 1
            self._messages_dropped += 1
            self._last_sink_error = f"{type(err).__name__}: {err}"
            _LOGGER.warning(
                "Jackery local MQTT sink failed for a received frame: %s",
                self._last_sink_error,
                exc_info=True,
            )
            return
        if accepted is False:
            self._messages_rejected_by_sink += 1
            self._messages_dropped += 1
            return
        self._messages_forwarded += 1

    def diagnostics_snapshot(self, *, redact: bool = True) -> dict[str, Any]:
        """Return the local MQTT receiver state for diagnostics."""
        last_topic = (
            REDACTED_VALUE
            if redact and self._last_topic is not None
            else self._last_topic
        )
        topics = (
            [REDACTED_VALUE for _ in self._topics_seen]
            if redact
            else list(self._topics_seen)
        )
        return {
            "enabled": True,
            "transport": "homeassistant.components.mqtt",
            "mqtt_integration_available": self._mqtt_available,
            "connected": self._connected,
            "broker_connected": self._connected,
            "subscribed": self._subscribed,
            "started": self.is_started,
            "topic_filter": REDACTED_VALUE if redact else self._topic_filter,
            "subscription_filter_count": 1 if self._subscribed else 0,
            "device_traffic_observed": self._messages_received > 0,
            "topics_seen_count": len(self._topics_seen),
            "topics_seen": topics,
            "topics_seen_truncated": self._topics_seen_truncated,
            "messages_received": self._messages_received,
            "messages_dropped": self._messages_dropped,
            "messages_forwarded": self._messages_forwarded,
            "messages_rejected_by_sink": self._messages_rejected_by_sink,
            "messages_oversized": self._messages_oversized,
            "sink_errors": self._sink_errors,
            "last_topic": last_topic,
            "last_message_at": self._last_message_at,
            "last_connect_at": self._last_connect_at,
            "last_disconnect_at": self._last_disconnect_at,
            "last_error": self._last_error,
            "last_sink_error": self._last_sink_error,
            "connect_attempts": self._connect_attempts,
            "subscription_retry_active": self._retry_task is not None
            and not self._retry_task.done(),
            "library": "homeassistant.components.mqtt",
        }

    @property
    def is_connected(self) -> bool:
        """Whether Home Assistant's shared MQTT client is connected."""
        return self._connected

    @property
    def is_started(self) -> bool:
        """Whether the receiver owns an active MQTT subscription."""
        return self._unsubscribe is not None

    @staticmethod
    def _utc_now_iso() -> str:
        """Return the current UTC time as an ISO 8601 string."""
        return datetime.now(UTC).isoformat()


def _local_mqtt_client(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> JackeryLocalMqttClient | None:
    """Return the entry-owned local MQTT receiver, if present."""
    from ..coordinator import (  # ruff: ignore[import-outside-top-level]
        JackerySolarVaultCoordinator,
    )

    coordinator: object = getattr(entry, "runtime_data", None)
    if not isinstance(coordinator, JackerySolarVaultCoordinator):
        return None
    return coordinator.local_mqtt_client
