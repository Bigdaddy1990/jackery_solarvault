"""Local third-party MQTT subscriber for Jackery SolarVault.

Separate from :mod:`.mqtt_push` (cloud broker, TLS, fixed Jackery topics).
This client connects to the user's LAN broker — see PROTOCOL.md §5 /
docs/Markdown/APP_POLLING_MQTT.md — to capture telemetry that the
SolarVault publishes when the device-side third-party bridge is enabled.

Topics published by the firmware are not fully documented across all
SKUs, so the initial subscription is the broad wildcard ``#``: every
unique topic seen is INFO-logged once so the user (and a follow-up
agent) can narrow this down later. Decoded payloads are pushed to an
optional sink callback; we do NOT touch the coordinator from here so
the cloud parsing pipeline stays the single source of truth.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from datetime import UTC, datetime
import json
import logging
from typing import Any

import aiomqtt
from aiomqtt import Client as MQTTClient, MqttError
from aiomqtt.exceptions import MqttCodeError

from ..const import (
    MQTT_CLIENT_LIBRARY,
    MQTT_CONNACK_REASONS,
    MQTT_KEEPALIVE_SEC,
    REDACTED_VALUE,
)

_LOGGER = logging.getLogger(__name__)
_AIOMQTT_LOGGER = logging.getLogger(f"{__name__}.aiomqtt")
logging.getLogger("aiomqtt").setLevel(logging.WARNING)

# Broad capture so we can see what the firmware publishes on the LAN.
# Once topics are known the coordinator can subscribe more narrowly.
LOCAL_MQTT_DEFAULT_TOPIC: str = "#"

# Track topic names with a sensible upper bound so a misconfigured broker
# (foreign neighbours publishing on the same LAN) cannot explode memory.
LOCAL_MQTT_MAX_TOPIC_NAMES: int = 256


# Sink signature kept loose so the wiring layer can pass any async callable
# that accepts ``(topic, payload_dict_or_None, raw_payload_bytes)``. ``None``
# for the dict means the payload was not valid JSON; the raw bytes are still
# forwarded so a future binary protocol decoder can plug in without touching
# this module.
LocalMqttSink = Callable[[str, dict[str, Any] | None, bytes], Awaitable[None]]


class JackeryLocalMqttClient:
    """Async-native subscriber for the user's local MQTT broker."""

    def __init__(
        self,
        hass: Any,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        client_id: str,
        sink: LocalMqttSink | None = None,
        topic_filter: str = LOCAL_MQTT_DEFAULT_TOPIC,
    ) -> None:
        """Hold the local broker target without opening any sockets."""
        self._hass = hass
        self._host = host
        self._port = port
        self._username = username or None
        self._password = password or None
        self._client_id = client_id
        self._sink = sink
        self._topic_filter = topic_filter
        self._lock = asyncio.Lock()
        self._client: MQTTClient | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._connected_event = asyncio.Event()
        self._connected = False
        self._messages_received = 0
        self._messages_dropped = 0
        self._topics_seen: list[str] = []
        self._topics_seen_set: set[str] = set()
        self._topics_seen_truncated = False
        self._last_topic: str | None = None
        self._last_message_at: str | None = None
        self._last_connect_at: str | None = None
        self._last_disconnect_at: str | None = None
        self._last_error: str | None = None
        self._connect_attempts = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Open the broker session in a background task and wait briefly."""
        async with self._lock:
            if self._runner_task is not None and not self._runner_task.done():
                return
            self._connected_event.clear()
            self._connected = False
            self._last_error = None
            self._connect_attempts += 1
            _LOGGER.info(
                "Jackery local MQTT: connecting to %s:%s (topic filter=%r)",
                self._host,
                self._port,
                self._topic_filter,
            )
            self._runner_task = self._hass.async_create_background_task(
                self._async_run_session(),
                name="jackery_local_mqtt_runner",
            )
        # Surface the initial outcome to diagnostics within a bounded window
        # without keeping the start-lock open. Reconnection is handled by the
        # session task itself on next ``async_start`` cycle.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._connected_event.wait(), timeout=10.0)

    async def async_stop(self) -> None:
        """Cancel the runner task and release the broker session cleanly."""
        async with self._lock:
            task = self._runner_task
            self._runner_task = None
            self._client = None
            self._connected = False
            self._connected_event.clear()
            if task is None:
                return
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, MqttError, Exception):
                await task

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    async def _async_run_session(self) -> None:
        """Hold a single broker session for the lifetime of this task."""
        connected = False
        try:
            async with aiomqtt.Client(
                hostname=self._host,
                port=self._port,
                identifier=self._client_id,
                username=self._username,
                password=self._password,
                keepalive=MQTT_KEEPALIVE_SEC,
                clean_session=True,
                logger=_AIOMQTT_LOGGER,
            ) as client:
                self._client = client
                self._connected = True
                connected = True
                self._last_connect_at = self._utc_now_iso()
                self._last_error = None
                self._connected_event.set()
                _LOGGER.info(
                    "Jackery local MQTT connected to %s:%s; subscribing %r",
                    self._host,
                    self._port,
                    self._topic_filter,
                )
                try:
                    await client.subscribe(self._topic_filter, qos=0)
                except MqttError as err:
                    self._last_error = f"subscribe failed: {err}"
                    _LOGGER.warning(
                        "Jackery local MQTT subscribe failed for %r: %s",
                        self._topic_filter,
                        err,
                    )
                    return
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
            _LOGGER.debug("Jackery local MQTT connect setup failed: %s", err)
        finally:
            was_connected = connected
            self._client = None
            self._connected = False
            if was_connected:
                self._last_disconnect_at = self._utc_now_iso()
            # Make sure waiters in ``async_start`` cannot deadlock on a session
            # that exited before the broker accepted us.
            self._connected_event.set()

    def _handle_connect_failure(self, rc: int) -> None:
        """Map a CONNACK rejection to an actionable diagnostics string."""
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
        """Record a passive disconnect or aiomqtt I/O failure mid-session."""
        if was_connected:
            self._last_error = f"disconnect: {error}"
            _LOGGER.debug("Jackery local MQTT disconnected: %s", error)
        else:
            self._last_error = f"connect failed: {error}"
            _LOGGER.debug("Jackery local MQTT connect setup failed: %s", error)

    @staticmethod
    def _extract_mqtt_code(err: MqttCodeError) -> int:
        rc = getattr(err, "rc", None)
        if isinstance(rc, int):
            return rc
        value = getattr(rc, "value", None)
        if isinstance(value, int):
            return value
        return 0

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    def _handle_message(
        self,
        topic: str,
        payload: bytes | bytearray | str,
    ) -> None:
        """Record a message, log first-seen topics, dispatch to the sink."""
        if topic not in self._topics_seen_set:
            if len(self._topics_seen_set) < LOCAL_MQTT_MAX_TOPIC_NAMES:
                self._topics_seen_set.add(topic)
                self._topics_seen.append(topic)
                _LOGGER.info("Jackery local MQTT: first message on topic %r", topic)
            else:
                self._topics_seen_truncated = True
        self._messages_received += 1
        self._last_topic = topic
        self._last_message_at = self._utc_now_iso()

        raw_bytes: bytes
        if isinstance(payload, str):
            raw_bytes = payload.encode("utf-8", errors="replace")
            text: str | None = payload
        else:
            raw_bytes = bytes(payload)
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = None

        data: dict[str, Any] | None = None
        if text is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError, ValueError:
                parsed = None
            if isinstance(parsed, dict):
                data = parsed
            elif parsed is not None:
                # Non-object JSON (list/scalar) is unusual for these devices;
                # surface it via dropped counter so diagnostics shows the rate.
                self._messages_dropped += 1
        else:
            # Binary frame — leave ``data`` None; the sink can still inspect
            # the raw bytes if a future binary decoder is plugged in.
            self._messages_dropped += 1

        if self._sink is not None:
            self._schedule_coroutine(self._sink(topic, data, raw_bytes), label="sink")

    def _schedule_coroutine(self, coro: Awaitable[None], label: str) -> None:
        task = self._hass.async_create_task(coro, name=f"jackery_local_mqtt_{label}")

        def _log_task_result(done: asyncio.Task[None]) -> None:
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception as err:
                _LOGGER.error("Jackery local MQTT %s handler failed: %s", label, err)

        task.add_done_callback(_log_task_result)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def diagnostics_snapshot(self, *, redact: bool = True) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of the local-MQTT state."""
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
            "configured_target": target,
            "connected": self._connected,
            "started": self._runner_task is not None,
            "topic_filter": self._topic_filter,
            "topics_seen_count": len(self._topics_seen),
            "topics_seen": topics,
            "topics_seen_truncated": self._topics_seen_truncated,
            "messages_received": self._messages_received,
            "messages_dropped": self._messages_dropped,
            "last_topic": last_topic,
            "last_message_at": self._last_message_at,
            "last_connect_at": self._last_connect_at,
            "last_disconnect_at": self._last_disconnect_at,
            "last_error": self._last_error,
            "connect_attempts": self._connect_attempts,
            "library": MQTT_CLIENT_LIBRARY,
        }

    @property
    def is_connected(self) -> bool:
        """Return True when the broker session is currently open."""
        return self._connected

    @property
    def is_started(self) -> bool:
        """Return True once a session task has been spawned at least once."""
        return self._runner_task is not None

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(UTC).isoformat()
