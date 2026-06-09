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
from collections.abc import Awaitable, Callable
import contextlib
from datetime import UTC, datetime
import json
import logging
from typing import TYPE_CHECKING, Any

import aiomqtt
from aiomqtt import MqttError
from aiomqtt.exceptions import MqttCodeError

from jackery_solarvault.const import (
    MQTT_CLIENT_LIBRARY,
    MQTT_CONNACK_REASONS,
    MQTT_KEEPALIVE_SEC,
    REDACTED_VALUE,
)

if TYPE_CHECKING:
    from aiomqtt import Client as MQTTClient

    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_AIOMQTT_LOGGER = logging.getLogger(f"{__name__}.aiomqtt")
logging.getLogger("aiomqtt").setLevel(logging.WARNING)
# The per-client aiomqtt logger can emit one DEBUG line per PUBLISH frame.
# Keep it at WARNING so enabling integration DEBUG does not create a
# packet-log storm on busy brokers.
_AIOMQTT_LOGGER.setLevel(logging.WARNING)

# Strict by default: no implicit wildcard subscription.
LOCAL_MQTT_DEFAULT_TOPIC: str = ""

# Track topic names with a sensible upper bound so a misconfigured broker
# (foreign neighbours publishing on the same LAN) cannot explode memory.
LOCAL_MQTT_MAX_TOPIC_NAMES: int = 256

# Guardrail for unexpectedly large broker payloads.
LOCAL_MQTT_MAX_PAYLOAD_BYTES: int = 128 * 1024

_HOME_ASSISTANT_EVENT_HEAD_BYTES: int = 1024

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


# Sink signature kept loose so the wiring layer can pass any async callable
# that accepts ``(topic, payload_dict_or_None, raw_payload_bytes)``. ``None``
# for the dict means the payload was not valid JSON; the raw bytes are still
# forwarded so a future binary protocol decoder can plug in without touching
# this module.
LocalMqttSink = Callable[[str, dict[str, Any] | None, bytes], Awaitable[None]]


class JackeryLocalMqttClient:
    """Async-native subscriber for the user's local MQTT broker."""

    def __init__(  # noqa: PLR0913
        self,
        hass: HomeAssistant,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        client_id: str,
        sink: LocalMqttSink | None = None,
        topic_filter: str = LOCAL_MQTT_DEFAULT_TOPIC,
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
        """  # noqa: E501
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
        self._messages_forwarded = 0
        self._topics_seen: list[str] = []
        self._topics_seen_set: set[str] = set()
        self._topics_seen_truncated = False
        self._last_topic: str | None = None
        self._last_message_at: str | None = None
        self._last_connect_at: str | None = None
        self._last_disconnect_at: str | None = None
        self._last_error: str | None = None
        self._connect_attempts = 0
        self._blocked_by_filter_count = 0
        self._payload_too_large_count = 0
        self._home_assistant_event_count = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Start the background MQTT session runner and trigger an initial connection attempt.

        If a runner is already active, this call is a no-op. Schedules the session task as a Home Assistant background task, resets connection state, increments the internal connect attempt counter, and waits up to 10 seconds for the initial connection result so diagnostics reflect the attempt.
        """  # noqa: E501
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
        """Stop the background MQTT session task and reset internal connection state.

        If a background session task exists it will be cancelled and awaited; cancellation, MQTT, and other finalization errors are suppressed. The client's connection state and stored client reference are cleared.
        """  # noqa: E501
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
        """Run one MQTT session: connect to the configured broker, subscribe to the topic filter, and consume incoming messages until the session ends.

        On successful connection, update connection state and timestamps and begin dispatching received messages to the client's message handler. Record subscription, connect, and disconnect errors for diagnostics. Always set the internal connected event before exiting to ensure callers waiting in startup cannot deadlock.
        """  # noqa: E501
        connected = False
        try:  # noqa: PLW0717
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
        except Exception as err:  # noqa: BLE001
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
        """Mark the client as disconnected after a broker CONNACK refusal and update diagnostic state.

        Parameters:
            rc (int): MQTT CONNACK return code provided by the broker indicating the reason for refusal.
        """  # noqa: E501
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
        """  # noqa: E501
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
        """  # noqa: E501
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

    def _handle_message(  # noqa: PLR0912, PLR0915
        self,
        topic: str,
        payload: bytes | bytearray | str,
    ) -> None:
        """Process a received MQTT message: record diagnostics, decode the payload, and forward a parsed JSON object (if any) to the configured sink.

        If the payload is UTF-8 text and parses as a JSON object, that dict is delivered to the sink as `data`. If the payload is binary or cannot be decoded as UTF-8, the message is counted as dropped and `data` is `None`. If UTF-8 text parses as JSON but the result is not an object (e.g., a list or scalar), the message is counted as dropped and `data` is `None`. If JSON parsing fails (invalid JSON), `data` remains `None` but the dropped counter is not incremented. First-seen topic names are recorded up to LOCAL_MQTT_MAX_TOPIC_NAMES for diagnostics.

        Parameters:
            topic (str): MQTT topic name of the message.
            payload (bytes | bytearray | str): Raw message payload; may be a string or bytes-like object.
        """  # noqa: E501
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
        if not self._topic_matches(self._topic_filter, topic):
            self._blocked_by_filter_count += 1
            return
        if self._should_drop_broad_noise_topic(topic):
            self._blocked_by_filter_count += 1
            return

        raw_bytes: bytes
        decoded_text_hint: str | None
        if isinstance(payload, str):
            raw_bytes = payload.encode("utf-8", errors="replace")
            decoded_text_hint = payload
        elif isinstance(payload, bytes):
            raw_bytes = payload
            decoded_text_hint = None
        else:
            raw_bytes = bytes(payload)
            decoded_text_hint = None

        if len(raw_bytes) > LOCAL_MQTT_MAX_PAYLOAD_BYTES:
            self._payload_too_large_count += 1
            self._messages_dropped += 1
            return
        if self._looks_like_home_assistant_state_event_payload(raw_bytes):
            self._home_assistant_event_count += 1
            self._messages_dropped += 1
            return
        # Hot-path CPU guard: when no sink is configured, parsing JSON and
        # UTF-8 decoding / callback scheduling is unnecessary work.
        if self._sink is None:
            return

        if decoded_text_hint is not None:
            text = decoded_text_hint
        else:
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = None

        data: dict[str, Any] | None = None
        if text is not None:
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                data = self._extract_local_jackery_payload(parsed)
                if data is None:
                    self._home_assistant_event_count += 1
                    self._messages_dropped += 1
                    return
            elif parsed is not None:
                # Non-object JSON (list/scalar) is unusual for these devices;
                # surface it via dropped counter so diagnostics shows the rate.
                self._messages_dropped += 1
        else:
            # Binary frame — leave ``data`` None; the sink can still inspect
            # the raw bytes if a future binary decoder is plugged in.
            self._messages_dropped += 1

        if self._sink is not None:
            self._messages_forwarded += 1
            self._schedule_coroutine(self._sink(topic, data, raw_bytes), label="sink")

    def _should_drop_broad_noise_topic(self, topic: str) -> bool:
        """Return True for known high-volume non-device topics."""
        if topic.startswith("$SYS/"):
            return True
        if not self._is_broad_topic_filter():
            return False
        return topic.startswith("homeassistant/")

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

    @staticmethod
    def _topic_matches(topic_filter: str, topic: str) -> bool:
        """Evaluate MQTT wildcard matching for one topic filter."""
        if not topic_filter:
            return False
        if topic_filter == "#":
            return True
        filter_levels = topic_filter.split("/")
        topic_levels = topic.split("/")
        for index, level in enumerate(filter_levels):
            if level == "#":
                return index == len(filter_levels) - 1
            if index >= len(topic_levels):
                return False
            if level in {"+", topic_levels[index]}:
                continue
            return False
        return len(filter_levels) == len(topic_levels)

    def _schedule_coroutine(self, coro: Awaitable[None], label: str) -> None:
        """Schedule an awaitable as a Home Assistant background task and log any non-cancellation exceptions from it.

        Parameters:
            coro (Awaitable[None]): Coroutine to run as a background task.
            label (str): Short label used to name the task (`jackery_local_mqtt_{label}`) and included in error logs.
        """  # noqa: E501

        async def _runner() -> None:
            await coro

        task = self._hass.async_create_task(
            _runner(), name=f"jackery_local_mqtt_{label}"
        )

        def _log_task_result(done: asyncio.Task[None]) -> None:
            """Log any non-cancellation exception from a completed asyncio Task.

            Retrieves the task result to surface exceptions raised by the task; ignores CancelledError. Logs other exceptions at error level with context identifying the handler label.

            Parameters:
                done (asyncio.Task[None]): Completed task whose exception, if raised, will be logged.
            """  # noqa: E501
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception as err:
                _LOGGER.exception(
                    "Jackery local MQTT %s handler failed: %s",
                    label,
                    err,  # noqa: TRY401
                )  # noqa: E501, RUF100, TRY401

        task.add_done_callback(_log_task_result)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def diagnostics_snapshot(self, *, redact: bool = True) -> dict[str, Any]:
        """Produce a JSON-serializable snapshot of the client's runtime state for diagnostics.

        Parameters:
            redact (bool): If True, redact sensitive fields (host, port, and topic names); if False, include real host, port, and topic names.

        Returns:
            dict[str, Any]: A snapshot containing connection/configuration flags, topic diagnostics, message counters, last-seen timestamps/errors, connect attempts, and the MQTT client library identifier.
        """  # noqa: E501
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
        routing_warning = None
        if (
            self._messages_received > 0
            and self._messages_forwarded == 0
            and self._home_assistant_event_count == self._messages_received
        ):
            routing_warning = "topic_receives_home_assistant_event_stream_only"
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
            "messages_forwarded": self._messages_forwarded,
            "last_topic": last_topic,
            "last_message_at": self._last_message_at,
            "last_connect_at": self._last_connect_at,
            "last_disconnect_at": self._last_disconnect_at,
            "last_error": self._last_error,
            "connect_attempts": self._connect_attempts,
            "blocked_by_filter_count": self._blocked_by_filter_count,
            "payload_too_large_count": self._payload_too_large_count,
            "home_assistant_event_count": self._home_assistant_event_count,
            "routing_warning": routing_warning,
            "library": MQTT_CLIENT_LIBRARY,
        }

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
        return self._runner_task is not None

    @staticmethod
    def _utc_now_iso() -> str:
        """Return the current UTC time as an ISO 8601 string including the timezone offset.

        Returns:
            iso_timestamp (str): ISO 8601 formatted UTC timestamp including timezone offset (e.g. "2026-05-27T12:34:56+00:00").
        """  # noqa: E501
        return datetime.now(UTC).isoformat()

    # --- restored from 01.06\custom_components\jackery_solarvault\client\local_mqtt.py ---  # noqa: E501
    @staticmethod
    def _looks_like_home_assistant_event_payload(payload: bytes) -> bool:
        """Return True for HA event-stream JSON published on a shared topic."""
        head = payload[:_HOME_ASSISTANT_EVENT_HEAD_BYTES]
        return b'"event_type"' in head and b'"event_data"' in head
