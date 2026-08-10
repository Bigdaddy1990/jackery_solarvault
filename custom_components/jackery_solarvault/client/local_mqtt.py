"""Local third-party MQTT subscriber for Jackery SolarVault.

Separate from :mod:`.mqtt_push` (cloud broker, TLS, fixed Jackery topics).
This client connects to the user's LAN broker — see PROTOCOL.md §5 /
docs/Markdown/APP_POLLING_MQTT.md — to capture telemetry that the
SolarVault publishes when the device-side third-party bridge is enabled.

Topics published by the firmware are not fully documented across all
SKUs, so users must configure the subscription filter explicitly in the
options flow. Decoded payloads are pushed to an optional sink callback;
the transport client does not reach into the coordinator directly. The
integration wiring sends both decoded and opaque frames through the shared,
transport-neutral coordinator decoder and ingest path.
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

from ..const import (
    DOMAIN,
    LOCAL_MQTT_MAX_PAYLOAD_BYTES,
    LOCAL_MQTT_MAX_TOPIC_NAMES,
    MQTT_CLIENT_LIBRARY,
    MQTT_CONNACK_REASONS,
    MQTT_KEEPALIVE_SEC,
    REDACTED_VALUE,
    _BINARY_FRAME_MAGIC_BE,
    _BINARY_FRAME_VERSION_BE,
)

if TYPE_CHECKING:
    from aiomqtt import Client as MQTTClient

    from homeassistant.core import HomeAssistant

type LocalMqttConfiguration = tuple[
    str,  # host
    int,  # port
    str | None,  # username
    str | None,  # password
    str,  # client_id
    str,  # topic_filter
]

_LOGGER = logging.getLogger(__name__)
_AIOMQTT_LOGGER = logging.getLogger(f"{__name__}.aiomqtt")
# The per-client aiomqtt logger can emit one DEBUG line per PUBLISH frame.
# Inherit from the integration logger (NOTSET) so HA controls the level;
# do not override the external ``aiomqtt`` library logger at import time.
# The logger name is namespaced under the integration so HA's logging
# configuration naturally targets it.

# Strict by default: no implicit wildcard subscription.
LOCAL_MQTT_DEFAULT_TOPIC: str = ""

_HOME_ASSISTANT_EVENT_HEAD_BYTES: int = 1024
_LOCAL_MQTT_RECONNECT_INITIAL_SEC: float = 5.0
_LOCAL_MQTT_RECONNECT_MAX_SEC: float = 60.0

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
# that accepts ``(topic, payload_dict_or_None, raw_payload_bytes)``. A concrete
# user-configured topic is already the routing boundary: every bounded payload
# received there reaches the coordinator, including body-only JSON whose field
# set is not known yet and opaque payloads that require transport decoding.
# Marker/header filtering is reserved for explicit wildcard subscriptions.
LocalMqttSink = Callable[
    [str, dict[str, Any] | None, bytes],
    Awaitable[bool | None],
]


class JackeryLocalMqttClient:
    """Async-native subscriber for the user's local MQTT broker."""

    def __init__(  # ruff:ignore[too-many-arguments]
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
        """Initialize the local MQTT client configuration and internal runtime state
        without connecting to the broker.

        Parameters:
            hass: Home Assistant instance (passed for creating tasks and logging; not
            used to open network connections here).
            host (str): MQTT broker hostname or IP address.
            port (int): MQTT broker TCP port.
            username (str | None): Optional username for broker authentication.
            password (str | None): Optional password for broker authentication.
            client_id (str): MQTT client identifier to use when connecting.
            sink (LocalMqttSink | None): Optional async callback invoked for each
            received message as (topic, parsed_dict_or_None, raw_bytes).
            topic_filter (str): MQTT subscription topic filter to use when the client
            connects.
        """  # noqa: D205, RUF105
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
        self._subscribed = False
        self._messages_received = 0
        self._messages_ignored_foreign = 0
        self._messages_dropped = 0
        self._messages_forwarded = 0
        self._messages_rejected_by_sink = 0
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

        # Store configuration for comparison
        self._configuration: LocalMqttConfiguration = (
            host,
            port,
            username or None,
            password or None,
            client_id,
            topic_filter,
        )

    def matches_configuration(self, other: LocalMqttConfiguration) -> bool:
        """Check if the given configuration matches this client's configuration."""
        return self._configuration == other

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """Start the background MQTT session runner and trigger an initial connection
        attempt.

        If a runner is already active, this call is a no-op. Schedules the session task
        as a Home Assistant background task, resets connection state, increments the
        internal connect attempt counter, and waits up to 10 seconds for the initial
        connection result so diagnostics reflect the attempt.
        """  # noqa: D205, RUF105
        async with self._lock:
            if self._runner_task is not None and not self._runner_task.done():
                return
            self._connected_event.clear()
            self._connected = False
            self._subscribed = False
            self._last_error = None
            _LOGGER.info(
                "Jackery local MQTT: connecting to %s:%s (topic=%r)",
                self._host,
                self._port,
                self._topic_filter,
            )
            self._runner_task = self._hass.async_create_background_task(
                self._async_run_forever(),
                name="jackery_local_mqtt_runner",
            )
        # Surface the initial outcome to diagnostics within a bounded window
        # without keeping the start-lock open. The persistent runner handles
        # later reconnects independently.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._connected_event.wait(), timeout=10.0)

    async def async_stop(self) -> None:
        """Stop the background MQTT session task and reset internal connection state.

        If a background session task exists it will be cancelled and awaited;
        expected task cancellation is suppressed. Other finalization errors remain
        visible to the caller. The client's connection state and stored client
        reference are cleared.
        """
        async with self._lock:
            task = self._runner_task
            self._runner_task = None
            self._client = None
            self._connected = False
            self._subscribed = False
            self._connected_event.clear()
            if task is None:
                return
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    @staticmethod
    async def _async_reconnect_sleep(delay: float) -> None:
        """Sleep between broker attempts without blocking Home Assistant."""
        await asyncio.sleep(delay)

    async def _async_run_forever(self) -> None:
        """Reconnect indefinitely with a bounded exponential delay."""
        reconnect_delay = _LOCAL_MQTT_RECONNECT_INITIAL_SEC
        while True:
            self._connect_attempts += 1
            connected = await self._async_run_session()
            if connected:
                reconnect_delay = _LOCAL_MQTT_RECONNECT_INITIAL_SEC
            await self._async_reconnect_sleep(reconnect_delay)
            if not connected:
                reconnect_delay = min(
                    reconnect_delay * 2,
                    _LOCAL_MQTT_RECONNECT_MAX_SEC,
                )

    async def _async_run_session(self) -> bool:
        """Run one MQTT session: connect to the configured broker, subscribe to the
        topic filter, and consume incoming messages until the session ends.

        On successful connection, update connection state and timestamps and begin
        dispatching received messages to the client's message handler. Record
        subscription, connect, and disconnect errors for diagnostics. Always set the
        internal connected event before exiting to ensure callers waiting in startup
        cannot deadlock.
        """  # noqa: D205, RUF105
        connected = False
        try:  # ruff:ignore[too-many-statements-in-try-clause]
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
                _LOGGER.info(
                    "Jackery local MQTT connected to %s:%s; subscribing to %r",
                    self._host,
                    self._port,
                    self._topic_filter,
                )
                try:
                    await client.subscribe(self._topic_filter, qos=0)
                except MqttError as err:
                    error = f"subscribe failed: {err}"
                    log = (
                        _LOGGER.warning if self._last_error != error else _LOGGER.debug
                    )
                    self._last_error = error
                    log(
                        "Jackery local MQTT subscribe failed for %r: %s",
                        self._topic_filter,
                        err,
                    )
                    self._connected_event.set()
                    return False
                self._subscribed = True
                self._connected_event.set()
                async for message in client.messages:
                    await self._handle_message(str(message.topic), message.payload)
        except MqttCodeError as err:
            self._handle_connect_failure(self._extract_mqtt_code(err))
        except MqttError as err:
            self._handle_disconnect_error(str(err), connected)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001, RUF105
            self._handle_disconnect_error(str(err), connected)
        finally:
            was_connected = connected
            self._client = None
            self._connected = False
            self._subscribed = False
            if was_connected:
                self._last_disconnect_at = self._utc_now_iso()
            # Make sure waiters in ``async_start`` cannot deadlock on a session
            # that exited before the broker accepted us.
            self._connected_event.set()
        return connected

    def _handle_connect_failure(self, rc: int) -> None:
        """Mark the client as disconnected after a broker CONNACK refusal and update
        diagnostic state.

        Parameters:
            rc (int): MQTT CONNACK return code provided by the broker indicating the
            reason for refusal.
        """  # noqa: D205, RUF105
        self._connected = False
        self._subscribed = False
        reason = MQTT_CONNACK_REASONS.get(rc, "unknown")
        error = f"connect rc={rc} ({reason})"
        log = _LOGGER.warning if self._last_error != error else _LOGGER.debug
        self._last_error = error
        self._connected_event.set()
        log(
            "Jackery local MQTT connect rejected by %s:%s — %s",
            self._host,
            self._port,
            self._last_error,
        )

    def _handle_disconnect_error(self, error: str, was_connected: bool) -> None:
        """Record a connection setup failure or a disconnect and update the client's
        last-error state.

        Parameters:
            error (str): Human-readable error message to record.
            was_connected (bool): True if the client had already established a
            connection when the error occurred; False if the failure happened while
            attempting to connect.
        """  # noqa: D205, RUF105
        if was_connected:
            self._last_error = f"disconnect: {error}"
            _LOGGER.debug("Jackery local MQTT disconnected: %s", error)
        else:
            connection_error = f"connect failed: {error}"
            log = (
                _LOGGER.warning
                if self._last_error != connection_error
                else _LOGGER.debug
            )
            self._last_error = connection_error
            log(
                "Jackery local MQTT connect setup failed for %s:%s: %s",
                self._host,
                self._port,
                error,
            )

    @staticmethod
    def _extract_mqtt_code(err: MqttCodeError) -> int:
        """Extract the numeric MQTT return code from a MqttCodeError instance.

        Parameters:
            err (MqttCodeError): The exception object that may contain an `rc` attribute
            or an `rc.value` holding the numeric code.

        Returns:
            int: The MQTT return code if present, otherwise `0`.
        """
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

    async def _handle_message(  # ruff:ignore[too-many-branches, too-many-statements, too-many-return-statements]  # flat hot-path guard chain; every reject is an early return
        self,
        topic: str,
        payload: bytes | bytearray | str,
    ) -> None:
        """Process a received MQTT message: record diagnostics, decode the payload, and
        forward a parsed JSON object (if any) to the configured sink.

        A UTF-8 JSON object is delivered as `data`; opaque or invalid-JSON payloads
        on an exact configured topic are delivered as raw bytes for the shared binary
        decoder. Non-object JSON and Home Assistant event-stream wrappers are dropped.
        Explicit wildcard subscriptions additionally require a Jackery marker or
        binary header so unrelated broker traffic cannot enter the ingest path.
        First-seen topic names are recorded up to LOCAL_MQTT_MAX_TOPIC_NAMES for
        diagnostics.

        Parameters:
            topic (str): MQTT topic name of the message.
            payload (bytes | bytearray | str): Raw message payload; may be a string or
            bytes-like object.
        """  # noqa: D205, RUF105
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
        # Hot-path CPU guard: when no sink is configured, parsing JSON and
        # UTF-8 decoding / callback scheduling is unnecessary work.
        if self._sink is None:
            return

        broad_filter = self._is_broad_topic_filter()

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
            except json.JSONDecodeError, ValueError:
                parsed = None
            if isinstance(parsed, dict):
                if self._looks_like_home_assistant_state_event_payload(raw_bytes):
                    self._home_assistant_event_count += 1
                    self._messages_dropped += 1
                    return
                data = self._extract_local_jackery_payload(parsed)
                if data is None:
                    self._home_assistant_event_count += 1
                    self._messages_dropped += 1
                    return
                if broad_filter and not _LOCAL_MQTT_JACKERY_MARKER_KEYS.intersection(
                    data
                ):
                    self._messages_ignored_foreign += 1
                    return
            elif parsed is not None:
                # Non-object JSON (list/scalar) is unusual for these devices;
                # surface it via dropped counter so diagnostics shows the rate.
                self._messages_dropped += 1
                return
        if (
            data is None
            and broad_filter
            and not self._is_jackery_binary_frame(raw_bytes)
        ):
            self._messages_ignored_foreign += 1
            return

        try:
            accepted = await self._sink(topic, data, raw_bytes)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._messages_dropped += 1
            _LOGGER.exception(
                "Jackery local MQTT sink failed for topic %r",
                topic,
            )
            return
        if accepted is False:
            self._messages_rejected_by_sink += 1
            _LOGGER.debug(
                "Jackery local MQTT payload on topic %r was not accepted by "
                "the shared device decoder",
                topic,
            )
            return
        self._messages_forwarded += 1

    def _should_drop_broad_noise_topic(self, topic: str) -> bool:
        """Return True for known high-volume non-device topics."""
        if topic.startswith("$SYS/"):
            return True
        if not self._is_broad_topic_filter():
            return False
        return topic.startswith("homeassistant/")

    @staticmethod
    def _looks_like_home_assistant_state_event_payload(payload: bytes) -> bool:
        """Detect whether a payload appears to be a Home Assistant `state_changed` event
        wrapper.

        Scans the first bytes of the payload for Home Assistant event markers commonly
        present in `state_changed` events.

        Returns:
            `True` if the payload prefix contains all of `"event_type"`,
            `"state_changed"`, `"event_data"`, `"old_state"`, and `"new_state"`,
            `False` otherwise.
        """  # noqa: D205, RUF105
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
        """Extract the Jackery payload dictionary from a parsed MQTT JSON object or its
        Home Assistant event wrapper.

        If `payload` is a Home Assistant event wrapper (contains `event_type` and
        `event_data`), inspects `event_data["payload"]`, `event_data["body"]`,
        `event_data["data"]`, and `event_data` itself and returns the first dict
        candidate whose keys intersect the Jackery marker keys. If `payload` is
        not an HA wrapper, returns it unchanged.

        Parameters:
            payload (dict): Parsed JSON object from an MQTT message, possibly an HA
            event wrapper.

        Returns:
            dict | None: The extracted Jackery payload dict if found; the original
            `payload` if it is not an HA event wrapper; `None` if a wrapper is present
            but no Jackery payload can be extracted.
        """  # noqa: D205, RUF105
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

    @staticmethod
    def _is_jackery_binary_frame(payload: bytes) -> bool:
        """Check if a binary payload is a Jackery encrypted frame (Crypto Layer C).

        Jackery binary frames have magic 0xDFED at offset 0
        and version 0x0064 at offset 2.
        """
        min_binary_frame_header = 4  # magic (2) + version (2)
        return (
            len(payload) >= min_binary_frame_header
            and payload[0:2] == _BINARY_FRAME_MAGIC_BE
            and payload[2:4] == _BINARY_FRAME_VERSION_BE
        )

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

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def diagnostics_snapshot(self, *, redact: bool = True) -> dict[str, Any]:
        """Produce a JSON-serializable snapshot of the client's runtime state for
        diagnostics.

        Parameters:
            redact (bool): If True, redact sensitive fields (host, port, and topic
            names); if False, include real host, port, and topic names.

        Returns:
            dict[str, Any]: A snapshot containing connection/configuration flags, topic
            diagnostics, message counters, last-seen timestamps/errors, connect
            attempts, and the MQTT client library identifier.
        """  # noqa: D205, RUF105
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
            topic_filter = REDACTED_VALUE
        else:
            target = {"host": self._host, "port": self._port}
            last_topic = self._last_topic
            topics = list(self._topics_seen)
            topic_filter = self._topic_filter
        routing_warning = None
        if (
            self._messages_received > 0
            and self._messages_forwarded == 0
            and self._home_assistant_event_count == self._messages_received
        ):
            routing_warning = "topic_receives_home_assistant_event_stream_only"
        elif (
            self._messages_received > 0
            and self._messages_forwarded == 0
            and self._messages_rejected_by_sink > 0
        ):
            routing_warning = "topic_payloads_rejected_by_device_decoder"
        elif (
            self._messages_received > 0
            and self._messages_forwarded == 0
            and self._messages_ignored_foreign > 0
        ):
            routing_warning = "topic_receives_foreign_traffic_only"
        return {
            "enabled": True,
            "configured_target": target,
            "connected": self.is_connected,
            "broker_connected": self._connected,
            "subscribed": self._subscribed,
            "started": self.is_started,
            "topic_filter": topic_filter,
            "subscription_filter_count": int(bool(self._topic_filter)),
            "topics_seen_count": len(self._topics_seen),
            "topics_seen": topics,
            "topics_seen_truncated": self._topics_seen_truncated,
            "messages_received": self._messages_received,
            "messages_dropped": self._messages_dropped,
            "messages_forwarded": self._messages_forwarded,
            "messages_rejected_by_sink": self._messages_rejected_by_sink,
            "messages_ignored_foreign": self._messages_ignored_foreign,
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
        """Indicate whether the broker session and subscription are active.

        Returns:
            True only after the broker accepted the configured subscription.
        """
        return self._connected and self._subscribed

    @property
    def is_started(self) -> bool:
        """Report whether the background MQTT session runner task is still alive.

        A task object that has already finished — because ``_async_run_session``
        raised outside ``async_stop`` — is not a running runner. Checking only for
        its existence reported ``started`` forever and made a dead listener look
        healthy while ``messages_received`` stayed at zero.

        Returns:
            True if the runner task exists and has not finished, False otherwise.
        """
        return self._runner_task is not None and not self._runner_task.done()

    @staticmethod
    def _utc_now_iso() -> str:
        """Return the current UTC time as an ISO 8601 string including the timezone
        offset.

        Returns:
            iso_timestamp (str): ISO 8601 formatted UTC timestamp including timezone
            offset (e.g. "2026-05-27T12:34:56+00:00").
        """  # noqa: D205, RUF105
        return datetime.now(UTC).isoformat()


_LOCAL_MQTT_RUNTIME_KEY = "local_mqtt_client"


def _local_mqtt_client(
    hass: HomeAssistant,
    entry: Any,  # noqa: ANN401, RUF105
) -> JackeryLocalMqttClient | None:
    """Retrieve the JackeryLocalMqttClient for a config entry from hass.data.

    Parameters:
        hass (HomeAssistant): Home Assistant runtime container.
        entry: Config entry-like object with an `entry_id` attribute used to locate the
        per-entry bucket.

    Returns:
        JackeryLocalMqttClient | None: The client instance for the entry, or `None` if
        not found or the stored value has a different type.
    """
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(bucket, dict):
        return None
    client = bucket.get(_LOCAL_MQTT_RUNTIME_KEY)
    return client if isinstance(client, JackeryLocalMqttClient) else None
