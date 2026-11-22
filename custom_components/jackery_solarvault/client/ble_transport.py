"""GATT runtime for the Jackery SolarVault BLE channel.

This module is the I/O layer on top of :mod:`.ble`. It uses Home Assistant's
``bluetooth`` integration for device discovery and ``bleak-retry-connector``
for resilient GATT connection management — both are the conventional
choices for custom integrations that talk to BLE peripherals.

Goal of this first revision: read-only diagnostic listener. The listener:

* Watches for advertisements that carry the Jackery service UUID
  ``0000bdee-...`` (see :data:`.ble.BLE_SERVICE_UUID`) and that match
  one of the device MAC addresses we already know.
* Opens a GATT connection on demand.
* Subscribes to notifications on characteristic
  ``0000ee02-...`` (:data:`.ble.BLE_NOTIFY_CHAR_UUID`).
* For every notification:

  1. Logs frame sizes and parse metadata without exposing raw payload bytes.
  2. Tries to base64-decode → AES-decrypt → CRC-validate → parse the
     frame, using the per-device ``bluetoothKey`` from the HTTP
     ``/v1/device/system/list`` response.
  3. Calls a coordinator-provided sink with the parsed frame (or with
     the raw bytes when decryption fails) so the integration can expose
     last-seen metadata in diagnostics.

The setter side (chunked writes to ``0xEE01``) is intentionally out of
scope here. Once Phase 3a has shown the listener decodes real frames
correctly, the same chunking/encrypt path from :mod:`.ble` will be
plumbed into :meth:`async_write_frames`.

Crypto assumptions follow implementation notes §14 and the reverse-engineered
``bb/a`` smali. Without a Frida-captured frame the layout is best-effort
— that is why diagnostics retain the last raw frame behind redaction.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
import hashlib
import importlib
from itertools import starmap
import json
import logging
import random
import time
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from bleak import BleakClient
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS, establish_connection

from homeassistant.core import callback

from ..const import (
    BOX_BLE_WRITE_PAIRS,
    DEFAULT_BLE_ACK_TIMEOUT_SEC,
    MQTT_CMD_DEVICE_GET_OTA_PAGE_DATA,
    MQTT_CMD_NOTIFY_DEVICE_OTA_TOTAL_PAGE,
)
from ..models import BleProcessDisposition
from . import ble
from .ble_ota import BoxOtaTransfer

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from bleak.backends.device import BLEDevice

    from homeassistant.components.bluetooth import (
        BluetoothCallbackMatcher,
        BluetoothChange,
        BluetoothScanningMode as HABluetoothScanningMode,
        BluetoothServiceInfoBleak,
    )
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


class _BluetoothModule(Protocol):
    """Typed surface of the optional Home Assistant Bluetooth module."""

    BluetoothScanningMode: type[HABluetoothScanningMode]

    def async_register_callback(
        self,
        hass: HomeAssistant,
        callback: Callable[[BluetoothServiceInfoBleak, BluetoothChange], None],
        matcher: BluetoothCallbackMatcher,
        mode: HABluetoothScanningMode,
    ) -> Callable[[], None]: ...

    def async_ble_device_from_address(
        self,
        hass: HomeAssistant,
        address: str,
        *,
        connectable: bool,
    ) -> BLEDevice | None: ...


_LOGGER = logging.getLogger(__name__)
_BLE_NOTIFICATION_LOG_SAMPLE_EVERY = 256
_MIN_WRITE_MTU = 61


def _should_log_ble_notification(frame_count: int) -> bool:
    """Return whether one notification should emit sparse DEBUG progress."""
    return frame_count == 1 or frame_count % _BLE_NOTIFICATION_LOG_SAMPLE_EVERY == 0


def _body_is_complete_json_object(body: bytes) -> bool:
    """Return whether one BLE page already carries a complete JSON object.

    App 2.4.0 distinguishes its pagination format from byte-fragment
    transport framing. Live SolarVault notifications confirm that each
    numbered page can contain an independently decodable command body, while
    later page numbers are not guaranteed to arrive. A complete nested JSON
    object can also be one fragment of a larger body; pending byte assemblies
    must therefore take precedence over this standalone-page fast path.
    """
    try:
        return isinstance(json.loads(body.decode("utf-8")), dict)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False


# bleak / HA-bluetooth imports are deferred to the methods that need them so
# the module can be imported on systems without BlueZ during tests.

#: Default timeout for the GATT connect + notify-subscribe handshake.
DEFAULT_BLE_CONNECT_TIMEOUT_SEC: float = 20.0

#: Minimum time between (re)connect attempts when the device drops the link.
_RECONNECT_BACKOFF_SEC: float = 30.0

#: Hard timeout for ``async_stop()`` to wait for in-flight connection
#: runners to honour cancellation. HA's shutdown sequence reports tasks
#: that exceed its own per-integration timeout (typically 30 s for
#: ``async_unload_entry``) — keep this well below that so the listener
#: never becomes the reason a shutdown logs "tasks still pending".
_STOP_TIMEOUT_SEC: float = 5.0

#: How often to write a no-op query frame to keep the GATT session
#: warm. The SolarVault peripheral closes idle GATT sessions after
#: roughly 20 s (observed 2026-05-17 production log: BLE disconnects
#: every 6-20 s without traffic). 15 s sits comfortably below that and
#: doubles as a property-refresh — the device answers each ``cmd=106``
#: with a ``DevicePropertyChange`` notify that the sink merges into
#: ``coordinator.data`` via the existing cmd=107 path.
_KEEPALIVE_INTERVAL_SEC: float = 15.0

# --- restored from Clause/live lineage (backup) ---  # noqa: E303, RUF100, RUF105
_LOST_LINK_BACKOFF_SEC: float = 8.0
_MAX_CONNECTION_RETRIES: int = 50
_MAX_BACKOFF_SEC: float = 300.0
# bleak / HA-bluetooth imports are deferred to the methods that need them so
# the module can be imported on systems without BlueZ during tests.

#: Minimum time between (re)connect attempts when the device drops the link.

#: A GATT link must keep notify ownership this long before it resets the
#: escalating ESPHome-proxy protection backoff.
_STABLE_SESSION_SEC: float = 60.0
_COOPERATIVE_STOP_GRACE_SEC: float = 0.25
_SINK_RETRY_INITIAL_SEC: float = 0.01
_SINK_RETRY_MAX_SEC: float = 1.0

# Notify chunks are ordered on a GATT connection, but an interrupted link may
# leave an incomplete logical message behind. Bound both lifetime and memory so
# supplemental BLE traffic can never grow coordinator-owned state indefinitely.
_REASSEMBLY_TIMEOUT_SEC: float = 10.0
_MIN_MULTI_CHUNK_COUNT: int = 2
_REASSEMBLY_MAX_CHUNKS: int = 128
_REASSEMBLY_MAX_BODY_BYTES: int = 256 * 1024
_REASSEMBLY_MAX_MESSAGES_PER_DEVICE: int = 8
_NOTIFY_QUEUE_WARN_FRAMES: int = _REASSEMBLY_MAX_CHUNKS
_UINT16_MAX: int = 0xFFFF
_BOX_OTA_INSTALL_TIMEOUT_SEC: float = 900.0
_BOX_OTA_RESTART_TIMEOUT_SEC: float = 120.0
_BOX_OTA_STATUS_CMD: int = 104
_BOX_OTA_INSTALLING: int = 2
_BOX_OTA_RESTARTING: int = 3
_ASCII_SERIAL_MIN: int = 32
_ASCII_SERIAL_MAX: int = 126
_OtaFamily = Literal["box", "home", "rc4", "aes_fixed", "aes_random"]


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BleFrameObservation:
    """One frame observed on the notify characteristic.

    Carries both the parsed view and the original raw bytes so the
    diagnostics surface can show what we received even if decoding failed.
    ``parsed`` is set when AES decrypt + header parse succeeded; otherwise
    ``decode_error`` carries the reason and ``parsed`` is None.
    """

    received_at: datetime
    raw_bytes: bytes
    base64_encoded: str
    parsed: ble.BleBinaryFrame | None
    decode_error: str | None = None
    session_generation: int | None = None
    notify_sequence: int | None = None
    delivery_id: str | None = None
    is_fragment: bool = False
    rejected_frame: ble.BleBinaryFrame | None = None
    protocol_status: int | None = None
    key_fingerprint: bytes | None = field(default=None, repr=False, compare=False)
    first_notify_sequence: int | None = None
    device_identity: tuple[int, str] | None = field(default=None, repr=False)
    session_key_fingerprint: bytes | None = field(default=None, repr=False)
    received_at_monotonic: float | None = None
    safety_received_at: datetime | None = None


@dataclass(slots=True)
class BleListenerStats:
    """Counters surfaced via diagnostics for transport health."""

    advertisements_seen: int = 0
    connect_attempts: int = 0
    connect_failures: int = 0
    frames_received: int = 0
    frames_decoded: int = 0
    frames_decode_failed: int = 0
    acks_received: int = 0
    acks_timed_out: int = 0

    multi_chunk_frames_buffered: int = 0
    multi_chunk_messages_assembled: int = 0
    multi_chunk_assemblies_dropped: int = 0
    multi_chunk_drop_reasons: dict[str, int] = field(default_factory=dict)
    multi_chunk_expired_by_cmd: dict[int, int] = field(default_factory=dict)
    multi_chunk_owner_rejected_by_cmd: dict[int, int] = field(default_factory=dict)
    multi_chunk_final_incomplete_by_cmd: dict[int, int] = field(default_factory=dict)
    last_multi_chunk_final_incomplete_by_cmd: dict[int, dict[str, Any]] = field(
        default_factory=dict
    )
    notify_frames_dropped: int = 0
    notify_queue_depth: int = 0
    notify_queue_high_watermark: int = 0
    notify_queue_bytes: int = 0
    notify_queue_high_watermark_bytes: int = 0
    notify_queue_oldest_age_sec: float = 0.0
    last_error: str | None = None
    last_decode_error: str | None = None
    last_sink_error: str | None = None
    last_connect_at: datetime | None = None
    last_disconnect_at: datetime | None = None
    last_ack_at: datetime | None = None
    last_frame: BleFrameObservation | None = field(default=None, repr=False)
    # Per-cmd counters for frames the sink decoded but did not route.
    # Exposed via diagnostics so the maintainer can see at a glance
    # how much BLE telemetry is still unconsumed (cmd=120 system /
    # per-device / CT variants currently — see coordinator sink).
    unrouted_frames_by_cmd: dict[int, int] = field(default_factory=dict)
    # Per-cmd counters for frames the sink recognized. This keeps
    # unchanged-but-routed frames (for example battery-pack lifetime
    # snapshots) separate from genuinely unsupported BLE payloads.
    routed_frames_by_cmd: dict[int, int] = field(default_factory=dict)


@dataclass(slots=True)
class _PendingAck:
    """Internal record tracking a write that is waiting for a notify echo.

    ``expected_cmds`` is ``None`` when *any* decoded notify frame on the
    same device counts as the ACK (defensive default: we know the device
    streams a property-change frame promptly after most setters but we
    cannot guarantee the exact echo cmd without firmware spec). When set,
    only frames whose ``cmd`` is in the set are accepted.
    """

    expected_msg_id: int
    expected_ble_msg_type: int
    registered_notify_sequence: int
    future: asyncio.Future[ble.BleBinaryFrame]
    session: _GattSession
    require_msg_id: bool = False
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _BleCommand:
    """One validated logical write and its optional ACK contract."""

    msg_id: int
    ble_msg_type: int
    body: bytes
    timeout_sec: float
    wait_for_ack: bool
    ack_timeout_sec: float
    mtu_override: int | None
    expected_profile: str | None
    is_safe: Callable[[], bool] | None


@dataclass(slots=True)
class _PendingFrameAssembly:
    """Bounded set of decoded chunks for one logical BLE message."""

    chunk_count: int
    frames: dict[int, ble.BleBinaryFrame]
    updated_at: float
    first_notify_sequence: int | None
    ota_key: bytes | None = field(default=None, repr=False)
    first_received_at: datetime | None = None
    first_received_monotonic: float | None = None


@dataclass(slots=True)
class _GattSession:
    """Ownership token for one per-device GATT connection."""

    generation: int
    client: BleakClient
    device_identity: tuple[int, str] | None = field(default=None, repr=False)
    key_fingerprint: bytes | None = field(default=None, repr=False)
    active: bool = True
    identity_refresh_requested: bool = False
    accepting_notifications: bool = True
    notify_started: bool = False
    write_mtu: int | None = None
    last_ota_reply_sequence: int = 0
    ota_page_request: BleFrameObservation | None = None
    ota_page_key: bytes | None = field(default=None, repr=False)
    ota_count_request: BleFrameObservation | None = None
    ota_count_key: bytes | None = field(default=None, repr=False)
    ota_start_consumed: bool = False
    ota_completed: bool = False
    ota_start_sequence: int | None = None
    ota_events: asyncio.Queue[BleFrameObservation | None] | None = field(
        default=None, repr=False
    )
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    uncertain_ack_pairs: set[tuple[int, int]] = field(default_factory=set)
    notify_sequence: int = 0
    # Bleak's notification callback is synchronous and therefore cannot apply
    # backpressure. Keep accepted frames in a lossless FIFO; queue depth and its
    # high-water mark are surfaced in diagnostics instead of deleting telemetry.
    notify_queue: asyncio.Queue[tuple[int, bytes, datetime]] = field(
        default_factory=asyncio.Queue
    )
    notify_pending_metadata: deque[tuple[float, int]] = field(default_factory=deque)
    notify_pending_bytes: int = 0
    notify_inflight: tuple[int, bytes, datetime] | None = None
    notify_task: asyncio.Task[None] | None = None
    notify_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    teardown_task: asyncio.Task[None] | None = None


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------


FrameSink = Callable[
    [str, BleFrameObservation], Awaitable[bool | BleProcessDisposition]
]
# Async sink called for every observed frame.

# ``device_id`` is the Jackery numeric device id (matches coordinator state).
# ``observation`` carries the decoded or raw frame.


class JackeryBleListener:  # ruff: ignore[too-many-public-methods]
    """Connects to one or more SolarVault devices via BLE and forwards frames.

    The listener owns one ``BleakClient`` per device. It re-attempts the
    GATT connect with capped exponential backoff after a drop, and stops
    gracefully on integration unload.

    Instantiate one listener per coordinator. Call :meth:`async_start`
    after the integration knows the per-device ``bluetoothKey`` (i.e.
    after the first successful HTTP discovery).
    """

    # Injected resolvers keep coordinator state out of the transport boundary.
    def __init__(  # ruff: ignore[too-many-arguments]
        self,
        hass: HomeAssistant,
        sink: FrameSink,
        *,
        key_resolver: Callable[[str], bytes | None],
        ble_address_resolver: Callable[[str], str | None],
        connect_backoff_remaining: Callable[[str, float], float],
        connect_backoff_note_failure: Callable[[str, float], float],
        connect_backoff_note_success: Callable[[str], None],
        serial_resolver: Callable[[str], str | None] | None = None,
        device_identity_resolver: Callable[[str], tuple[int, str] | None] | None = None,
        profile_resolver: Callable[[str], ble.PortableBleProfile | str | None]
        | None = None,
        ota_pending_resolver: Callable[[str], bool] | None = None,
        disconnect_callback: Callable[[str, int], None] | None = None,
        reassembly_gap_callback: Callable[[str, int, int], None] | None = None,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Construct a Jackery BLE notification listener.

        The listener observes notifications and forwards parsed frames to a sink.

        Parameters:
            hass (HomeAssistant): Home Assistant instance used for bluetooth callbacks
            and background tasks.
            sink (FrameSink): Async callable invoked with (device_id,
            BleFrameObservation) for each observed frame.
            key_resolver (Callable[[str], bytes | None]): Returns the 16- or 32-byte AES
            key for a given device_id, or None if unavailable.
            ble_address_resolver (Callable[[str], str | None]): Returns the BLE MAC for
            a given device_id, or None; the listener also caches discovered addresses
            and exposes them via address_for_device_id.
            serial_resolver (Callable[[str], str | None] | None): Maps a BLE-broadcast
            serial string to a Jackery device_id; when omitted incoming advertisements
            with unmapped serials are logged but not associated.
        """
        self._hass = hass
        self._config_entry = config_entry
        self._sink = sink
        self._disconnect_callback = disconnect_callback
        self._on_reassembly_gap = reassembly_gap_callback
        self._key_resolver = key_resolver
        self._ble_address_resolver = ble_address_resolver
        self._connect_backoff_remaining = connect_backoff_remaining
        self._connect_backoff_note_failure = connect_backoff_note_failure
        self._connect_backoff_note_success = connect_backoff_note_success
        self._serial_resolver = serial_resolver
        self._device_identity_resolver = device_identity_resolver
        self._profile_resolver = profile_resolver
        self._ota_pending_resolver = ota_pending_resolver
        self._ha_bluetooth: _BluetoothModule | None = None
        self._stats: dict[str, BleListenerStats] = {}
        self._unregister_callbacks: list[Callable[[], None]] = []
        self._connections: dict[str, asyncio.Task[None]] = {}
        self._session_generations: dict[str, int] = {}
        self._sessions: dict[str, _GattSession] = {}
        self._notify_tasks: set[asyncio.Task[None]] = set()
        self._stop_event = asyncio.Event()
        self._stop_task: asyncio.Task[None] | None = None
        self._unavailable_connections: set[str] = set()
        self._delivery_namespace = f"{random.getrandbits(128):032x}"
        # Cache of (device_id -> BLE MAC) populated on first matching
        # advertisement. The coordinator's ``_ble_address_for_device``
        # reads back through :meth:`address_for_device_id`.
        self._device_addresses: dict[str, str] = {}
        # Device ids accepted for this listener instance. An advertisement
        # that resolves outside this immutable cache-owned set is rejected
        # before a GATT connection can deliver a frame under the wrong device.
        self._configured_device_ids: frozenset[str] = frozenset()
        # Active GATT clients per device id, populated by the connection
        # runner. ``async_send_command`` reads from this dict to write to
        # the open session without re-establishing the connect.
        self._clients: dict[str, BleakClient] = {}
        self._identity_refresh_seen: set[str] = set()
        # Pending ACK registrations per device id. ``_handle_notification``
        # resolves the matching futures when a decoded frame arrives. Each
        # device can have multiple in-flight writes (rare in practice for
        # SolarVault, but the data structure makes the contract explicit).
        self._pending_acks: dict[str, list[_PendingAck]] = {}
        # Home frames carry msgId in "flags" and bleMsgType in "cmd".
        # Keep assemblies separate by that exact source-backed pair so different
        # commands may safely interleave.
        self._frame_assemblies: dict[
            str,
            dict[tuple[int, int], _PendingFrameAssembly],
        ] = {}
        self._frame_assembly_owners: dict[str, _GattSession] = {}
        # Per-device negotiated MTU, populated after connect. Used by
        # ``async_send_command`` to size the per-frame body budget. Falls
        # back to :data:`ble.DEFAULT_BLE_MTU` (matches the Android app)
        # when bleak hasn't exposed a value yet.
        self._mtu: dict[str, int] = {}
        self._mtu_owners: dict[str, _GattSession] = {}

    def _next_session_generation(self, device_id: str) -> int:
        """Return the next ownership generation for a device session."""
        generation = self._session_generations.get(device_id, 0) + 1
        self._session_generations[device_id] = generation
        return generation

    def _session_is_current(self, device_id: str, session: _GattSession) -> bool:
        """Return whether a session still owns the active device connection."""
        current = self._sessions.get(device_id)
        return (
            not self._stop_event.is_set()
            and session.active
            and current is session
            and current.generation == session.generation
            and current.client is session.client
            and self._clients.get(device_id) is session.client
        )

    def _notification_session_owns_connection(
        self,
        device_id: str,
        session: _GattSession,
    ) -> bool:
        """Return whether a callback still belongs to the installed GATT owner.

        Listener shutdown deliberately does not participate in this check. Bleak
        may synchronously invoke the callback until stop_notify or disconnect has
        completed; every frame received before that physical cutoff remains owned.
        """
        current = self._sessions.get(device_id)
        return (
            current is session
            and current.generation == session.generation
            and current.client is session.client
            and self._clients.get(device_id) is session.client
        )

    def _create_owned_task(
        self,
        target: Coroutine[Any, Any, None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        """Create listener work through Home Assistant's tracked task factory."""
        try:
            if self._config_entry is not None:
                return cast(  # ty: ignore[redundant-cast]
                    "asyncio.Task[None]",
                    self._config_entry.async_create_background_task(
                        self._hass,
                        target,
                        name=name,
                    ),
                )
            return cast(  # ty: ignore[redundant-cast]
                "asyncio.Task[None]",
                self._hass.async_create_background_task(
                    target,
                    name=name,
                ),
            )
        except Exception:
            target.close()
            raise

    def _accepted_notification_session_owns_connection(
        self,
        device_id: str,
        session: _GattSession,
    ) -> bool:
        """Return whether an accepted FIFO item still belongs to this session.

        Physical disconnect and listener shutdown fence new callbacks by changing
        ``active``/``accepting_notifications``.  They must not invalidate bytes
        that the synchronous Bleak callback already appended to the session FIFO.
        Map identity remains the ownership fence until teardown drains the FIFO.
        """
        current = self._sessions.get(device_id)
        return (
            current is session
            and current.generation == session.generation
            and current.client is session.client
            and self._clients.get(device_id) is session.client
        )

    def _reset_backoff_after_stable_session(
        self,
        device_id: str,
        *,
        started_at: float,
        now: float,
    ) -> bool:
        """Reset connect escalation only after a stable notify session."""
        if now - started_at < _STABLE_SESSION_SEC:
            return False
        self._connect_backoff_note_success(device_id)
        self._identity_refresh_seen.discard(device_id)
        return True

    def _log_connection_unavailable(
        self,
        device_id: str,
        message: str,
        *args: object,
    ) -> None:
        """Log one unavailable warning per BLE outage."""
        first_failure = device_id not in self._unavailable_connections
        self._unavailable_connections.add(device_id)
        logger = _LOGGER.warning if first_failure else _LOGGER.debug
        logger(message, *args)

    def _log_connection_available(
        self,
        device_id: str,
        notify_characteristic: str,
    ) -> None:
        """Log initial BLE availability or recovery from one outage."""
        recovered = device_id in self._unavailable_connections
        self._unavailable_connections.discard(device_id)
        if recovered:
            _LOGGER.info(
                "Jackery BLE %s connection restored; subscribing to notify %s",
                device_id,
                notify_characteristic,
            )
            return
        _LOGGER.info(
            "Jackery BLE %s: connected; subscribing to notify %s",
            device_id,
            notify_characteristic,
        )

    def _connection_is_current(
        self,
        device_id: str,
        runner_task: asyncio.Task[None] | None,
    ) -> bool:
        """Return whether the connection slot is unclaimed or owned by this runner."""
        current = self._connections.get(device_id)
        # Direct coroutine invocation is useful for callers that exercise the
        # runner without registering it in the advertisement map. Once a map
        # entry exists, only that exact task owns the device runner.
        return current is None or current is runner_task

    def _install_session(
        self,
        device_id: str,
        client: BleakClient,
        generation: int,
    ) -> _GattSession:
        """Install a GATT session only after prior ownership was released."""
        previous = self._sessions.get(device_id)
        if previous is not None:
            msg = (
                f"Jackery BLE {device_id}: generation {previous.generation} "
                "still owns the GATT session"
            )
            raise RuntimeError(msg)
        identity_resolver = getattr(self, "_device_identity_resolver", None)
        identity = (
            identity_resolver(device_id) if identity_resolver is not None else None
        )
        key = self._key_resolver(device_id)
        session = _GattSession(
            generation=generation,
            client=client,
            device_identity=identity,
            key_fingerprint=hashlib.sha256(key).digest() if key is not None else None,
        )
        self._sessions[device_id] = session
        self._clients[device_id] = client
        return session

    def _clear_frame_assemblies(
        self,
        device_id: str,
        session: _GattSession | None = None,
        *,
        reason: str | None = None,
    ) -> int:
        """Clear owned frame assemblies and return the number discarded."""
        owner = self._frame_assembly_owners.get(device_id)
        if session is not None and owner is not session:
            return 0
        assemblies = self._frame_assemblies.pop(device_id, None)
        if assemblies and reason is not None:
            stats = self.stats_for(device_id)
            for cmd, flags in assemblies:
                self._record_multi_chunk_drop_reason(stats, cmd, reason)
                if reason == "session_closed":
                    recover = getattr(self, "_on_reassembly_gap", None)
                    if callable(recover):
                        try:
                            recover(device_id, cmd, flags)
                        except Exception:
                            _LOGGER.exception(
                                "BLE reconnect recovery scheduling failed"
                            )
        if owner is None or session is None or owner is session:
            self._frame_assembly_owners.pop(device_id, None)
        return len(assemblies) if assemblies is not None else 0

    async def _cancel_notify_tasks(self, session: _GattSession) -> None:
        """Release completed notification tasks after their FIFO was drained."""
        tasks = tuple(session.notify_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        session.notify_tasks.difference_update(tasks)
        self._notify_tasks.difference_update(tasks)
        session.notify_task = None

    async def _async_wait_for_notification_drain(
        self,
        device_id: str,
        session: _GattSession,
    ) -> None:
        """Wait until every notification accepted by ``session`` reached its sink."""
        if session.notify_inflight is not None or not session.notify_queue.empty():
            self._start_notification_drain(device_id, session)
        await session.notify_queue.join()
        current = session.notify_task
        if current is not None and not current.done():
            await asyncio.shield(current)

    async def async_wait_for_notification_drain(
        self, device_id: str, generation: int
    ) -> bool:
        """Wait for accepted notifications without changing session ownership."""
        session = self._sessions.get(device_id)
        if (
            session is None
            or session.generation != generation
            or not self._session_is_current(device_id, session)
        ):
            return False
        try:
            await asyncio.wait_for(
                self._async_wait_for_notification_drain(device_id, session), timeout=2.0
            )
        except TimeoutError:
            return False
        return self._session_is_current(device_id, session)

    @staticmethod
    def _signal_ota_session_end(session: _GattSession) -> None:
        """Wake an OTA transfer after accepted notifications have drained."""
        if (
            session.ota_events is not None
            and session.notify_inflight is None
            and session.notify_queue.empty()
        ):
            session.ota_events.put_nowait(None)

    async def _async_teardown_session_impl(
        self,
        device_id: str,
        session: _GattSession,
    ) -> None:
        """Quiesce ingress, drain accepted frames, then release GATT ownership."""
        client = session.client
        disconnected = not bool(getattr(client, "is_connected", False))
        if not disconnected and session.notify_started:
            stop_notify = getattr(client, "stop_notify", None)
            if callable(stop_notify):
                try:
                    await asyncio.wait_for(
                        stop_notify(ble.BLE_NOTIFY_CHAR_UUID),
                        timeout=5.0,
                    )
                except Exception:  # ruff: ignore[blind-except] -- disconnect is the transport fallback
                    if bool(getattr(client, "is_connected", False)):
                        await asyncio.wait_for(client.disconnect(), timeout=5.0)
                    disconnected = True
                else:
                    session.notify_started = False
            else:
                await asyncio.wait_for(client.disconnect(), timeout=5.0)
                disconnected = True
        elif not disconnected:
            await asyncio.wait_for(client.disconnect(), timeout=5.0)
            disconnected = True

        # The physical notify source is now quiescent. Only at this confirmed
        # cutoff may synchronous Bleak callbacks be rejected.
        session.notify_started = False
        session.accepting_notifications = False
        await self._async_wait_for_notification_drain(device_id, session)

        if not disconnected and bool(getattr(client, "is_connected", False)):
            await asyncio.wait_for(client.disconnect(), timeout=5.0)

        session.active = False
        self._signal_ota_session_end(session)
        if self._sessions.get(device_id) is session:
            self._sessions.pop(device_id, None)
            if self._clients.get(device_id) is session.client:
                self._clients.pop(device_id, None)
            if self._mtu_owners.get(device_id) is session:
                self._mtu_owners.pop(device_id, None)
                self._mtu.pop(device_id, None)
        dropped = self._clear_frame_assemblies(
            device_id, session, reason="session_closed"
        )
        if dropped:
            self.stats_for(device_id).multi_chunk_assemblies_dropped += dropped
        self._cancel_session_pending_acks(device_id, session)
        await self._cancel_notify_tasks(session)

    async def _teardown_session(
        self,
        device_id: str,
        session: _GattSession,
    ) -> None:
        """Finish a session teardown even when its connection runner is cancelled."""
        cleanup = session.teardown_task
        if (
            cleanup is not None
            and cleanup.done()
            and (cleanup.cancelled() or cleanup.exception() is not None)
        ):
            cleanup = None
        if cleanup is None:
            cleanup = self._create_owned_task(
                self._async_teardown_session_impl(device_id, session),
                name=f"jackery_ble_teardown_{device_id}_{session.generation}",
            )
            session.teardown_task = cleanup
        cancellation_requested = False
        while True:
            try:
                await asyncio.shield(cleanup)
                break
            except asyncio.CancelledError:
                if cleanup.done():
                    if cleanup.cancelled():
                        raise
                    cleanup.result()
                    break
                cancellation_requested = True
                current_task = asyncio.current_task()
                if current_task is not None:
                    current_task.uncancel()
        if cancellation_requested:
            raise asyncio.CancelledError

    async def _async_wait_for_notification_delivery(
        self,
        device_id: str,
        delivery: asyncio.Task[None],
    ) -> bool:
        """Shield one delivery and report whether its FIFO owner was cancelled."""
        cancellation_requested = False
        while True:
            try:
                await asyncio.shield(delivery)
            except asyncio.CancelledError:
                cancellation_requested = True
                current_task = asyncio.current_task()
                if current_task is not None:
                    current_task.uncancel()
                if not delivery.done():
                    continue
                if delivery.cancelled():
                    error = (
                        "notification delivery task was cancelled before the "
                        "accepted frame reached its sink"
                    )
                    stats = self.stats_for(device_id)
                    stats.last_sink_error = error
                    stats.last_error = error
                    raise RuntimeError(error) from None
                delivery.result()
                return cancellation_requested
            else:
                return cancellation_requested

    async def _drain_notifications(
        self,
        device_id: str,
        session: _GattSession,
    ) -> None:
        """Process queued notifications in connection order for one session."""
        cancellation_requested = False
        while True:
            if session.notify_inflight is None:
                try:
                    session.notify_inflight = session.notify_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            notify_sequence, raw, received_at = session.notify_inflight
            delivery_coro = self._handle_notification(
                device_id,
                raw,
                session=session,
                notify_sequence=notify_sequence,
                received_at=received_at,
                received_monotonic=(
                    session.notify_pending_metadata[0][0]
                    if session.notify_pending_metadata
                    else None
                ),
                accepted=True,
            )
            try:
                delivery = self._create_owned_task(
                    delivery_coro,
                    name=(
                        f"jackery_ble_notify_delivery_{device_id}_"
                        f"{session.generation}_{notify_sequence}"
                    ),
                )
            except Exception:
                delivery_coro.close()
                raise
            delivered = False
            try:
                cancellation_requested |= (
                    await self._async_wait_for_notification_delivery(
                        device_id,
                        delivery,
                    )
                )
                delivered = True
            finally:
                if delivered:
                    session.notify_queue.task_done()
                    session.notify_inflight = None
                    if session.notify_pending_metadata:
                        _, delivered_bytes = session.notify_pending_metadata.popleft()
                        session.notify_pending_bytes = max(
                            0,
                            session.notify_pending_bytes - delivered_bytes,
                        )
                    self._refresh_notification_queue_stats(device_id)
        if cancellation_requested:
            raise asyncio.CancelledError
        if not session.accepting_notifications:
            self._signal_ota_session_end(session)

    def _start_notification_drain(
        self,
        device_id: str,
        session: _GattSession,
    ) -> None:
        """Ensure exactly one ordered consumer owns a session notification FIFO."""
        current = session.notify_task
        if current is not None and not current.done():
            return
        drain_coro = self._drain_notifications(device_id, session)
        try:
            task = self._create_owned_task(
                drain_coro,
                name=f"jackery_ble_notify_{device_id}_{session.generation}",
            )
        except Exception:
            drain_coro.close()
            raise
        session.notify_task = task
        session.notify_tasks.add(task)
        self._notify_tasks.add(task)

        def _task_done(completed: asyncio.Task[None]) -> None:
            """Drop a completed notification task from listener ownership."""
            if session.notify_task is completed:
                session.notify_task = None
            session.notify_tasks.discard(completed)
            self._notify_tasks.discard(completed)
            try:
                notify_err = completed.exception()
            except asyncio.CancelledError:
                notify_err = None
            if notify_err is not None:
                _LOGGER.warning(
                    "Jackery BLE notify task %s failed: %s",
                    completed.get_name(),
                    notify_err,
                )
                if (
                    session.notify_inflight is not None
                    or not session.notify_queue.empty()
                ):
                    asyncio.get_running_loop().call_soon(
                        self._start_notification_drain,
                        device_id,
                        session,
                    )

        task.add_done_callback(_task_done)

    def _schedule_notification(
        self,
        device_id: str,
        session: _GattSession,
        raw: bytes,
    ) -> None:
        """Queue a notification for ordered session-owned processing."""
        if (
            not session.accepting_notifications
            or not self._notification_session_owns_connection(device_id, session)
        ):
            return
        session.notify_sequence += 1
        queued_notification = (session.notify_sequence, raw, datetime.now(UTC))
        session.notify_pending_metadata.append((time.monotonic(), len(raw)))
        session.notify_pending_bytes += len(raw)
        session.notify_queue.put_nowait(queued_notification)
        stats = self.stats_for(device_id)
        self._refresh_notification_queue_stats(device_id, stats)
        depth = stats.notify_queue_depth
        if depth > stats.notify_queue_high_watermark:
            stats.notify_queue_high_watermark = depth
            if depth == _NOTIFY_QUEUE_WARN_FRAMES:
                _LOGGER.warning(
                    "Jackery BLE %s notification FIFO reached %d pending frames; "
                    "preserving all accepted telemetry",
                    device_id,
                    depth,
                )
        stats.notify_queue_high_watermark_bytes = max(
            stats.notify_queue_high_watermark_bytes,
            stats.notify_queue_bytes,
        )
        self._start_notification_drain(device_id, session)

    def address_for_device_id(self, device_id: str) -> str | None:
        """Get the cached BLE MAC address for the given device id.

        Returns:
            The MAC address string for the device, or `None` if no cached address
            exists.
        """
        return self._device_addresses.get(device_id)

    # ------------------------------------------------------------------
    # Phase 3b: write path — send a command frame to the device
    # ------------------------------------------------------------------

    def _record_negotiated_mtu(
        self,
        device_id: str,
        client: BleakClient,
        *,
        session: _GattSession | None = None,
    ) -> None:
        """Cache the negotiated GATT MTU after ``start_notify`` returns.

        Different bleak backends expose the MTU under different attribute
        names, and at different points in the connect lifecycle. We try
        the well-known ones in order and keep the cache empty if none
        produce an integer. Unknown capacity never grants write readiness.
        The write characteristic is authoritative for write-without-response;
        it can also establish capacity when BlueZ reports the MTU sentinel 23.
        """
        if session is not None and not self._session_is_current(device_id, session):
            return
        mtu = 0
        for attr in ("mtu_size", "mtu"):
            value = getattr(client, attr, None)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                _LOGGER.debug(
                    "Jackery BLE %s: backend reported %s=%r (%s), not an int",
                    device_id,
                    attr,
                    value,
                    type(value).__name__,
                )
                continue
            mtu = max(value, 0)
            break
        capacity = self._write_capacity(client)
        if capacity is not None:
            # ATT writes use MTU minus the three-byte ATT header. Preserve
            # the app's separate 60-byte frame budget for chunk construction.
            characteristic_mtu = capacity + 3
            mtu = (
                min(mtu, characteristic_mtu)
                if mtu >= _MIN_WRITE_MTU
                else characteristic_mtu
            )
        self._mtu[device_id] = mtu
        if session is not None:
            self._mtu_owners[device_id] = session
            session.write_mtu = mtu
        _LOGGER.debug("Jackery BLE %s: effective write MTU=%d", device_id, mtu)

    @staticmethod
    def _write_capacity(client: BleakClient) -> int | None:
        """Return the current characteristic's proven no-response write limit."""
        services = getattr(client, "services", None)
        if services is None:
            return None
        characteristic = services.get_characteristic(ble.BLE_WRITE_CHAR_UUID)
        capacity = getattr(characteristic, "max_write_without_response_size", None)
        return (
            capacity
            if isinstance(capacity, int)
            and not isinstance(capacity, bool)
            and capacity > 0
            else None
        )

    def mtu_for_device(self, device_id: str) -> int:
        """Return the cached negotiated MTU for ``device_id`` or the default."""
        return self._mtu.get(device_id, ble.DEFAULT_BLE_MTU)

    def current_session_generation(self, device_id: str) -> int | None:
        """Return the generation of a connected, notification-ready session."""
        session = self._sessions.get(device_id)
        if (
            session is None
            or not session.notify_started
            or not getattr(session.client, "is_connected", False)
            or not self._session_is_current(device_id, session)
        ):
            return None
        return session.generation

    @callback
    def request_identity_refresh(self, device_id: str, generation: int) -> None:
        """Retire only the session whose device identity was invalidated."""
        session = self._sessions.get(device_id)
        if session is not None and session.generation == generation:
            session.identity_refresh_requested = True
            session.active = False

    def current_notification_marker(self, device_id: str) -> tuple[int, int] | None:
        """Return the installed session and last accepted notification sequence."""
        session = self._sessions.get(device_id)
        if session is None:
            return None
        return session.generation, session.notify_sequence

    def owns_current_observation(
        self, device_id: str, observation: BleFrameObservation
    ) -> bool:
        """Verify the listener and current session that accepted an observation."""
        return self.owns_current_delivery(
            device_id,
            observation.session_generation,
            observation.notify_sequence,
            observation.delivery_id,
        )

    def owns_current_delivery(
        self,
        device_id: str,
        generation: int | None,
        sequence: int | None,
        delivery_id: str | None,
    ) -> bool:
        """Verify a retained delivery still belongs to this listener session."""
        if generation is None or sequence is None:
            return False
        return self.current_session_generation(
            device_id
        ) == generation and self.owns_delivery(
            device_id, generation, sequence, delivery_id
        )

    def owns_delivery(
        self, device_id: str, generation: int, sequence: int, delivery_id: str | None
    ) -> bool:
        """Verify a delivery's listener identity without requiring a connection."""
        return delivery_id == (
            f"{self._delivery_namespace}:{device_id}:{generation}:{sequence}"
        )

    def box_ota_started(self, device_id: str, generation: int) -> bool:
        """Return whether this session has started its one OTA exchange."""
        session = self._sessions.get(device_id)
        return bool(
            session is not None
            and session.generation == generation
            and self._session_is_current(device_id, session)
            and session.ota_start_consumed
        )

    def release_ota_recovery(self, device_id: str, generation: int) -> None:
        """Release a session-local interlock after external version confirmation."""
        session = self._sessions.get(device_id)
        if (
            session is None
            or session.generation != generation
            or not self._session_is_current(device_id, session)
            or session.ota_events is not None
        ):
            msg = "BLE OTA recovery session is no longer current"
            raise RuntimeError(msg)
        session.ota_completed = True

    def session_is_current(
        self, device_id: str, generation: int, through_sequence: int
    ) -> bool:
        """Return whether the requested device still owns this BLE session."""
        session = self._sessions.get(device_id)
        return bool(
            session is not None
            and session.generation == generation
            and session.notify_started
            and getattr(session.client, "is_connected", False)
            and self._session_is_current(device_id, session)
            and session.notify_queue.empty()
            and (
                session.notify_inflight is None
                or session.notify_inflight[0] <= through_sequence
            )
        )

    async def async_ensure_connected(
        self,
        device_id: str,
        *,
        timeout_sec: float = DEFAULT_BLE_CONNECT_TIMEOUT_SEC,
    ) -> bool:
        """Wait for a current BLE session ready for notifications and writes."""
        if self._stop_event.is_set() or self._hass.is_stopping:
            return False
        client = self._clients.get(device_id)
        session = self._sessions.get(device_id)
        if (
            client is not None
            and getattr(client, "is_connected", False)
            and session is not None
            and session.notify_started
            and self._session_is_current(device_id, session)
        ):
            self._record_negotiated_mtu(device_id, client, session=session)
            return session.write_mtu is not None and session.write_mtu >= _MIN_WRITE_MTU
        address = self._device_addresses.get(device_id)
        if address is None:
            return False
        task = self._connections.get(device_id)
        if task is None or task.done():
            # Respect the per-device connect pacing: after a failed
            # connect (or a fresh link loss) no new runner is spawned in
            # the same coordinator cycle — callers fall back to the next
            # transport instead of hammering the BT-proxy again.
            wait_sec = self._connect_backoff_remaining(
                device_id,
                asyncio.get_running_loop().time(),
            )
            if wait_sec > 0:
                _LOGGER.debug(
                    "Jackery BLE %s: connect backoff active (%.1fs left); "
                    "not reconnecting this cycle",
                    device_id,
                    wait_sec,
                )
                return False
            self._spawn_connection_if_ready(device_id)
        if timeout_sec <= 0 or device_id not in self._connections:
            return False
        deadline = asyncio.get_running_loop().time() + timeout_sec
        connected = False
        while not self._stop_event.is_set() and not self._hass.is_stopping:
            client = self._clients.get(device_id)
            session = self._sessions.get(device_id)
            if (
                client is not None
                and getattr(client, "is_connected", False)
                and session is not None
                and session.notify_started
                and self._session_is_current(device_id, session)
            ):
                self._record_negotiated_mtu(device_id, client, session=session)
                connected = (
                    session.write_mtu is not None
                    and session.write_mtu >= _MIN_WRITE_MTU
                )
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.25, remaining))
        return connected

    async def _async_stop_requested_within(self, delay: float) -> bool:
        """Return whether listener shutdown is requested before ``delay`` expires."""
        if self._hass.is_stopping:
            return True
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except TimeoutError:
            return False
        return True

    async def _async_write_command_chunks(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        session: _GattSession,
        chunks: list[bytes],
        *,
        key: bytes,
        command: _BleCommand,
        profile: str | None,
    ) -> None:
        """Encrypt and write every chunk for one current GATT session."""
        client = session.client
        chunk_count = len(chunks)
        for idx, chunk in enumerate(chunks, start=1):
            if not self._session_is_current(device_id, session):
                msg = f"BLE session for {device_id} changed during write"
                raise RuntimeError(msg)
            if command.is_safe is not None and command.is_safe() is not True:
                msg = f"BLE command is no longer valid for device {device_id}"
                raise RuntimeError(msg)
            if profile in {"rc4", "aes_fixed"}:
                plain = ble.build_portable_legacy_frame(
                    action_id=command.msg_id,
                    ble_cmd=command.ble_msg_type,
                    body=chunk,
                    frame_index=idx,
                    chunk_count=chunk_count,
                )
                blob = ble.encrypt_portable_legacy_frame(plain, key, cipher=profile)
            else:
                plain = ble.build_binary_frame(
                    cmd=command.ble_msg_type,
                    body=chunk,
                    flags=command.msg_id,
                    frame_index=idx,
                    chunk_count=chunk_count,
                )
                blob = (
                    ble.encrypt_box_frame(plain, key)
                    if profile == "box_fixed"
                    else ble.encrypt_binary_notify(plain, key)
                )
            capacity = self._write_capacity(client)
            if capacity is not None and len(blob) > capacity:
                msg = (
                    f"BLE encrypted frame exceeds write capacity for {device_id}: "
                    f"{len(blob)} > {capacity}"
                )
                raise RuntimeError(msg)
            await asyncio.wait_for(
                client.write_gatt_char(ble.BLE_WRITE_CHAR_UUID, blob, response=False),
                timeout=command.timeout_sec,
            )

    async def async_query_box_ota_version(
        self,
        device_id: str,
        *,
        session_generation: int,
        is_safe: Callable[[], bool],
        family: _OtaFamily = "box",
    ) -> int:
        """Write a Box version query and return its own notification barrier."""
        session = self._sessions.get(device_id)
        key = self._key_resolver(device_id)
        if session is None or session.generation != session_generation or key is None:
            msg = "Box OTA version query has no current session"
            raise RuntimeError(msg)
        expected_profile = (
            "box_fixed" if family == "box" else None if family == "home" else family
        )
        async with session.write_lock:
            if (
                not self._session_is_current(device_id, session)  # ruff: ignore[too-many-boolean-expressions]
                or not getattr(session.client, "is_connected", False)
                or not session.notify_started
                or session.write_mtu is None
                or session.ota_events is not None
                or self._profile_resolver is None
                or self._profile_resolver(device_id) != expected_profile
                or self._key_resolver(device_id) != key
                or is_safe() is not True
            ):
                msg = "Box OTA version query is no longer authorized"
                raise RuntimeError(msg)
            mtu = (
                min(session.write_mtu, 315)
                if family in {"rc4", "aes_fixed"}
                else session.write_mtu
            )
            chunks = ble.split_body_for_mtu(b'{"cmd":100}', mtu)
            barrier = session.notify_sequence
            for index, chunk in enumerate(chunks, start=1):
                if (
                    not self._session_is_current(device_id, session)  # ruff: ignore[too-many-boolean-expressions]
                    or not getattr(session.client, "is_connected", False)
                    or self._profile_resolver is None
                    or self._profile_resolver(device_id) != expected_profile
                    or self._key_resolver(device_id) != key
                    or is_safe() is not True
                ):
                    msg = "Box OTA version query was interrupted"
                    raise RuntimeError(msg)
                action = 248 if family == "box" else 3006 if family == "home" else 2
                if family in {"rc4", "aes_fixed"}:
                    plain = ble.build_portable_legacy_frame(
                        action_id=action,
                        ble_cmd=100,
                        frame_index=index,
                        chunk_count=len(chunks),
                        body=chunk,
                    )
                    encrypted = ble.encrypt_portable_legacy_frame(
                        plain, key, cipher=family
                    )
                else:
                    plain = ble.build_binary_frame(
                        flags=action,
                        cmd=100,
                        frame_index=index,
                        chunk_count=len(chunks),
                        body=chunk,
                    )
                    encrypted = (
                        ble.encrypt_box_frame(plain, key)
                        if family == "box"
                        else ble.encrypt_binary_notify(plain, key)
                    )
                capacity = self._write_capacity(session.client)
                if capacity is not None and len(encrypted) > capacity:
                    msg = "Box OTA version query exceeds the current GATT capacity"
                    raise RuntimeError(msg)
                if index == len(chunks):
                    barrier = session.notify_sequence
                await asyncio.wait_for(
                    session.client.write_gatt_char(
                        ble.BLE_WRITE_CHAR_UUID, encrypted, response=False
                    ),
                    timeout=10.0,
                )
            if not self._session_is_current(device_id, session):
                msg = "Box OTA session changed during version query"
                raise RuntimeError(msg)
            return barrier

    async def async_run_home_ota(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        *,
        target_sn: str,
        firmware: dict[str, bytes],
        session_generation: int,
        is_safe: Callable[[str], bool],
        is_current: Callable[[str], bool],
        timeout_sec: float,
    ) -> str:
        """Follow Home firmware requests on one current GATT session."""
        return await self.async_run_box_ota(
            device_id,
            target_sn=target_sn,
            firmware=firmware,
            session_generation=session_generation,
            is_safe=is_safe,
            is_current=is_current,
            timeout_sec=timeout_sec,
            family="home",
        )

    async def async_run_portable_ota(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        *,
        target_sn: str,
        firmware: dict[str, bytes],
        session_generation: int,
        is_safe: Callable[[str], bool],
        is_current: Callable[[str], bool],
        timeout_sec: float,
    ) -> str:
        """Follow Portable firmware requests using the selected model codec."""
        profile = self._profile_resolver(device_id) if self._profile_resolver else None
        if profile not in {"rc4", "aes_fixed", "aes_random"}:
            msg = "Portable OTA model profile is not available"
            raise RuntimeError(msg)
        return await self.async_run_box_ota(
            device_id,
            target_sn=target_sn,
            firmware=firmware,
            session_generation=session_generation,
            is_safe=is_safe,
            is_current=is_current,
            timeout_sec=timeout_sec,
            family=cast("_OtaFamily", profile),
        )

    async def async_run_box_ota(  # ruff: ignore[complex-structure, too-many-arguments, too-many-branches, too-many-statements]
        self,
        device_id: str,
        *,
        target_sn: str,
        firmware: dict[str, bytes],
        session_generation: int,
        is_safe: Callable[[str], bool],
        is_current: Callable[[str], bool],
        timeout_sec: float,
        family: _OtaFamily = "box",
    ) -> str:
        """Follow Box firmware requests on one current GATT session."""
        session = self._sessions.get(device_id)
        if (
            session is None  # ruff: ignore[too-many-boolean-expressions]
            or session.generation != session_generation
            or not self._session_is_current(device_id, session)
            or session.write_mtu is None
            or session.ota_events is not None
            or timeout_sec <= 0
        ):
            msg = "Box OTA session is not ready"
            raise RuntimeError(msg)
        key = self._key_resolver(device_id)
        if key is None:
            msg = "Box OTA session has no key"
            raise RuntimeError(msg)
        transfer = BoxOtaTransfer(
            target_sn,
            firmware,
            mtu=min(session.write_mtu, 315)
            if family in {"rc4", "aes_fixed"}
            else session.write_mtu,
            generation=session_generation,
            family="box" if family == "box" else "home",
        )
        announcement = transfer.announcement()
        events: asyncio.Queue[BleFrameObservation | None] = asyncio.Queue()
        session.ota_events = events
        try:
            if not await self.async_send_box_ota_start(
                device_id,
                target_sn=announcement["sn"],
                firmware_types=tuple(firmware),
                session_generation=session_generation,
                is_safe=is_safe,
                family=family,
            ):
                msg = "Box OTA announcement was not sent"
                raise RuntimeError(msg)
            transfer.response_sent()
            event_timeout = timeout_sec
            restarting = False
            while True:
                observation = await asyncio.wait_for(
                    events.get(), timeout=event_timeout
                )
                if observation is None:
                    if restarting:
                        return "reconnect_required"
                    msg = "Box OTA connection was lost"
                    raise RuntimeError(msg)
                if (
                    self._key_resolver(device_id) != key  # ruff: ignore[too-many-boolean-expressions]
                    or observation.session_generation != session_generation
                    or observation.decode_error is not None
                    or observation.parsed is None
                    or observation.notify_sequence is None
                    or observation.first_notify_sequence is None
                    or observation.protocol_status is None
                    or (
                        family != "box"
                        and observation.parsed.checksum_valid is not True
                    )
                    or session.ota_start_sequence is None
                    or observation.first_notify_sequence <= session.ota_start_sequence
                    or observation.key_fingerprint != hashlib.sha256(key).digest()
                ):
                    msg = "Box OTA observation lost its session or could not be decoded"
                    raise RuntimeError(msg)
                try:
                    body = json.loads(observation.parsed.body or b"{}")
                except json.JSONDecodeError as err:
                    msg = "Box OTA request body is invalid"
                    raise RuntimeError(msg) from err
                if not isinstance(body, dict):
                    msg = "Box OTA request body is not an object"
                    raise RuntimeError(msg)  # ruff: ignore[type-check-without-type-error]
                if not self._session_is_current(device_id, session):
                    if (
                        observation.parsed.cmd == _BOX_OTA_STATUS_CMD
                        and body.get("code") == _BOX_OTA_RESTARTING
                        and is_current(target_sn) is True
                    ):
                        transfer.receive(
                            observation.parsed.cmd,
                            body,
                            status=observation.protocol_status,
                            generation=session_generation,
                            sequence=observation.notify_sequence,
                        )
                        return "reconnect_required"
                    msg = "Box OTA connection was lost"
                    raise RuntimeError(msg)
                await asyncio.wait_for(session.notify_queue.join(), timeout=timeout_sec)
                authorized = (
                    is_current(target_sn)
                    if observation.parsed.cmd == _BOX_OTA_STATUS_CMD
                    else is_safe(target_sn)
                )
                if (
                    not self._session_is_current(device_id, session)
                    or self._key_resolver(device_id) != key
                    or authorized is not True
                ):
                    msg = "Box OTA target or safety state changed"
                    raise RuntimeError(msg)
                response = transfer.receive(
                    observation.parsed.cmd,
                    body,
                    status=observation.protocol_status,
                    generation=session_generation,
                    sequence=observation.notify_sequence,
                )
                if response is None:
                    if transfer.state == "success":
                        session.ota_completed = True
                        return "success"
                    if transfer.state == "failed":
                        code = body.get("code")
                        messages = {
                            1: "Box OTA firmware invalid",
                            5: "Box OTA installation failed",
                        }
                        msg = "Box OTA unknown failure status"
                        if isinstance(code, int) and not isinstance(code, bool):
                            msg = messages.get(code, msg)
                        elif isinstance(code, float) and code.is_integer():
                            msg = messages.get(int(code), msg)
                        raise RuntimeError(msg)
                    if observation.parsed.cmd == _BOX_OTA_STATUS_CMD:
                        if body.get("code") == _BOX_OTA_INSTALLING:
                            event_timeout = _BOX_OTA_INSTALL_TIMEOUT_SEC
                        elif body.get("code") == _BOX_OTA_RESTARTING:
                            event_timeout = _BOX_OTA_RESTART_TIMEOUT_SEC
                            restarting = True
                    continue
                if response["cmd"] == MQTT_CMD_NOTIFY_DEVICE_OTA_TOTAL_PAGE:
                    sent = await self.async_send_box_ota_count(
                        device_id,
                        request=observation,
                        target_sn=target_sn,
                        firmware_type=response["f"],
                        page_count=response["p"],
                        firmware_size=response["s"],
                        session_generation=session_generation,
                        is_safe=lambda: is_safe(target_sn),
                        family=family,
                    )
                else:
                    sent = await self.async_send_box_ota_page(
                        device_id,
                        response["pages"],
                        request=observation,
                        firmware_type=response["f"],
                        page_index=response["p"],
                        session_generation=session_generation,
                        is_safe=lambda: is_safe(target_sn),
                        family=family,
                    )
                if not sent:
                    msg = "Box OTA request could not be answered safely"
                    raise RuntimeError(msg)
                transfer.response_sent()
        finally:
            if session.ota_events is events:
                session.ota_events = None
                session.ota_count_request = None
                session.ota_count_key = None
                session.ota_page_request = None
                session.ota_page_key = None

    async def async_send_box_ota_start(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        *,
        target_sn: str,
        firmware_types: tuple[str, ...],
        session_generation: int,
        is_safe: Callable[[str], bool],
        family: _OtaFamily = "box",
    ) -> bool:
        """Send one firmware announcement through the current Box session."""
        session = self._sessions.get(device_id)
        if (
            session is None  # ruff: ignore[too-many-boolean-expressions]
            or isinstance(session_generation, bool)
            or session.generation != session_generation
            or not isinstance(target_sn, str)
            or not target_sn.strip()
            or not firmware_types
            or any(
                not isinstance(name, str) or not name or "," in name
                for name in firmware_types
            )
            or len(set(firmware_types)) != len(firmware_types)
        ):
            return False
        key = self._key_resolver(device_id)
        if key is None:
            return False
        expected_profile = (
            "box_fixed" if family == "box" else None if family == "home" else family
        )
        async with session.write_lock:
            if (
                not self._session_is_current(device_id, session)  # ruff: ignore[too-many-boolean-expressions]
                or not getattr(session.client, "is_connected", False)
                or not session.notify_started
                or session.write_mtu is None
                or self._profile_resolver is None
                or self._profile_resolver(device_id) != expected_profile
                or self._key_resolver(device_id) != key
                or is_safe(target_sn) is not True
                or session.ota_start_consumed
                or not session.notify_queue.empty()
            ):
                return False
            body = json.dumps(
                {"cmd": 101, "sn": target_sn, "f": ",".join(firmware_types)},
                separators=(",", ":"),
            ).encode()
            mtu = (
                min(session.write_mtu, 315)
                if family in {"rc4", "aes_fixed"}
                else session.write_mtu
            )
            chunks = ble.split_body_for_mtu(body, mtu)
            for index, chunk in enumerate(chunks, start=1):
                if (
                    not self._session_is_current(device_id, session)  # ruff: ignore[too-many-boolean-expressions]
                    or not getattr(session.client, "is_connected", False)
                    or self._profile_resolver is None
                    or self._profile_resolver(device_id) != expected_profile
                    or self._key_resolver(device_id) != key
                    or is_safe(target_sn) is not True
                    or not session.notify_queue.empty()
                ):
                    if index > 1:
                        msg = "Box OTA announcement interrupted"
                        raise RuntimeError(msg)
                    return False
                action = 247 if family == "box" else 3007 if family == "home" else 3
                if family in {"rc4", "aes_fixed"}:
                    plain = ble.build_portable_legacy_frame(
                        action_id=action,
                        ble_cmd=101,
                        frame_index=index,
                        chunk_count=len(chunks),
                        body=chunk,
                    )
                    encrypted = ble.encrypt_portable_legacy_frame(
                        plain, key, cipher=family
                    )
                else:
                    plain = ble.build_binary_frame(
                        flags=action,
                        cmd=101,
                        frame_index=index,
                        chunk_count=len(chunks),
                        body=chunk,
                    )
                    encrypted = (
                        ble.encrypt_box_frame(plain, key)
                        if family == "box"
                        else ble.encrypt_binary_notify(plain, key)
                    )
                capacity = self._write_capacity(session.client)
                if capacity is not None and len(encrypted) > capacity:
                    msg = "Box OTA announcement exceeds the current GATT write capacity"
                    raise RuntimeError(msg)
                if index == len(chunks):
                    session.ota_start_sequence = session.notify_sequence
                if not session.ota_start_consumed:
                    session.ota_start_consumed = True
                await asyncio.wait_for(
                    session.client.write_gatt_char(
                        ble.BLE_WRITE_CHAR_UUID, encrypted, response=False
                    ),
                    timeout=10.0,
                )
        return True

    async def async_send_box_ota_count(  # ruff: ignore[too-many-arguments, too-many-return-statements, too-many-branches]
        self,
        device_id: str,
        *,
        request: BleFrameObservation,
        target_sn: str,
        firmware_type: str,
        page_count: int,
        firmware_size: int,
        session_generation: int,
        is_safe: Callable[[], bool],
        family: _OtaFamily = "box",
    ) -> bool:
        """Answer one current Box firmware page-count request."""
        session = self._sessions.get(device_id)
        frame = request.parsed
        if (
            session is None  # ruff: ignore[too-many-boolean-expressions]
            or frame is None
            or (session.ota_start_consumed and session.ota_events is None)
            or frame.cmd != MQTT_CMD_NOTIFY_DEVICE_OTA_TOTAL_PAGE
            or request.is_fragment
            or request.decode_error is not None
            or request.protocol_status != 0
        ):
            return False
        try:
            request_body = json.loads(frame.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if (
            not isinstance(request_body, dict)  # ruff: ignore[too-many-boolean-expressions]
            or request_body.get("f") != firmware_type
            or not isinstance(firmware_type, str)
            or not firmware_type
            or not isinstance(target_sn, str)
            or not target_sn
            or isinstance(page_count, bool)
            or not isinstance(page_count, int)
            or not 1 <= page_count <= _UINT16_MAX
            or isinstance(firmware_size, bool)
            or not isinstance(firmware_size, int)
            or firmware_size <= 0
            or isinstance(session_generation, bool)
            or session.generation != session_generation
            or request.session_generation != session_generation
        ):
            return False
        key = self._key_resolver(device_id)
        if key is None or session.ota_count_key != key:
            return False
        expected_profile = (
            "box_fixed" if family == "box" else None if family == "home" else family
        )
        async with session.write_lock:
            if (
                not self._session_is_current(device_id, session)  # ruff: ignore[too-many-boolean-expressions]
                or not getattr(session.client, "is_connected", False)
                or not session.notify_started
                or session.write_mtu is None
                or session.write_mtu <= (50 if family == "box" else 60)
                or self._profile_resolver is None
                or self._profile_resolver(device_id) != expected_profile
                or self._key_resolver(device_id) != key
                or is_safe() is not True
                or session.ota_count_request is not request
                or request.notify_sequence is None
                or not session.notify_queue.empty()
                or (
                    session.notify_inflight is not None
                    and session.notify_inflight[0] > request.notify_sequence
                )
                or session.last_ota_reply_sequence >= request.notify_sequence
            ):
                return False
            mtu = (
                min(session.write_mtu, 315)
                if family in {"rc4", "aes_fixed"}
                else session.write_mtu
            )
            page_size = mtu - (50 if family == "box" else 60)
            if (firmware_size + page_size - 1) // page_size != page_count:
                return False
            body_fields = {
                "cmd": 102,
                "sn": target_sn,
                "f": firmware_type,
                "p": page_count,
            }
            if family in {"box", "home"}:
                body_fields["s"] = firmware_size
            body = json.dumps(body_fields, separators=(",", ":")).encode()
            chunks = ble.split_body_for_mtu(body, mtu)
            for index, chunk in enumerate(chunks, start=1):
                if (
                    not self._session_is_current(device_id, session)  # ruff: ignore[too-many-boolean-expressions]
                    or not getattr(session.client, "is_connected", False)
                    or self._profile_resolver is None
                    or self._profile_resolver(device_id) != expected_profile
                    or self._key_resolver(device_id) != key
                    or is_safe() is not True
                    or session.ota_count_request is not request
                    or not session.notify_queue.empty()
                    or (
                        session.notify_inflight is not None
                        and session.notify_inflight[0] > request.notify_sequence
                    )
                ):
                    if index > 1:
                        msg = "Box OTA count response interrupted"
                        raise RuntimeError(msg)
                    return False
                action = 246 if family == "box" else 3008 if family == "home" else 4
                if family in {"rc4", "aes_fixed"}:
                    plain = ble.build_portable_legacy_frame(
                        action_id=action,
                        ble_cmd=102,
                        frame_index=index,
                        chunk_count=len(chunks),
                        body=chunk,
                    )
                    encrypted = ble.encrypt_portable_legacy_frame(
                        plain, key, cipher=family
                    )
                else:
                    plain = ble.build_binary_frame(
                        flags=action,
                        cmd=102,
                        frame_index=index,
                        chunk_count=len(chunks),
                        body=chunk,
                    )
                    encrypted = (
                        ble.encrypt_box_frame(plain, key)
                        if family == "box"
                        else ble.encrypt_binary_notify(plain, key)
                    )
                capacity = self._write_capacity(session.client)
                if capacity is not None and len(encrypted) > capacity:
                    msg = "Box OTA count exceeds the current GATT write capacity"
                    raise RuntimeError(msg)
                session.last_ota_reply_sequence = request.notify_sequence
                await asyncio.wait_for(
                    session.client.write_gatt_char(
                        ble.BLE_WRITE_CHAR_UUID, encrypted, response=False
                    ),
                    timeout=10.0,
                )
        return True

    async def async_send_box_ota_page(  # ruff: ignore[too-many-arguments, too-many-return-statements]
        self,
        device_id: str,
        pages: tuple[bytes, ...],
        *,
        request: BleFrameObservation,
        firmware_type: str,
        page_index: int,
        session_generation: int,
        is_safe: Callable[[], bool],
        family: _OtaFamily = "box",
    ) -> bool:
        """Write a requested Box firmware page only through its owning session."""
        session = self._sessions.get(device_id)
        frame = request.parsed
        if (
            frame is None
            or frame.cmd != MQTT_CMD_DEVICE_GET_OTA_PAGE_DATA
            or request.is_fragment
            or request.decode_error is not None
            or request.protocol_status != 0
        ):
            return False
        try:
            request_body = json.loads(frame.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if (
            not isinstance(request_body, dict)
            or request_body.get("f") != firmware_type
            or isinstance(request_body.get("p"), bool)
            or not isinstance(request_body.get("p"), int)
            or request_body.get("p") != page_index
        ):
            return False
        if (
            isinstance(session_generation, bool)  # ruff: ignore[too-many-boolean-expressions]
            or not isinstance(session_generation, int)
            or session is None
            or (session.ota_start_consumed and session.ota_events is None)
            or session.generation != session_generation
            or not session.notify_started
            or session.write_mtu is None
            or not self._session_is_current(device_id, session)
            or request.session_generation != session_generation
        ):
            return False
        key = self._key_resolver(device_id)
        if key is None or session.ota_page_key != key:
            return False
        expected_profile = (
            "box_fixed" if family == "box" else None if family == "home" else family
        )
        async with session.write_lock:
            if (
                not self._session_is_current(device_id, session)  # ruff: ignore[too-many-boolean-expressions]
                or not getattr(session.client, "is_connected", False)
                or not session.notify_started
                or session.write_mtu is None
                or self._profile_resolver is None
                or self._profile_resolver(device_id) != expected_profile
                or self._key_resolver(device_id) != key
                or is_safe() is not True
            ):
                return False
            mtu = (
                min(session.write_mtu, 315)
                if family in {"rc4", "aes_fixed"}
                else session.write_mtu
            )
            if any(len(page) > mtu - (50 if family == "box" else 60) for page in pages):
                msg = "Box OTA page exceeds the current session MTU"
                raise ValueError(msg)
            encrypted = (
                ble.encrypt_box_ota_page(pages, page_index=page_index, key=key)
                if family == "box"
                else ble.encrypt_home_ota_page(pages, page_index=page_index, key=key)
                if family == "home"
                else ble.encrypt_portable_ota_page(
                    pages, page_index=page_index, key=key, profile=family
                )
            )
            capacity = self._write_capacity(session.client)
            if capacity is not None and len(encrypted) > capacity:
                msg = "Box OTA page exceeds the current GATT write capacity"
                raise RuntimeError(msg)

            async def write_if_current() -> bool:
                if (
                    not self._session_is_current(device_id, session)  # ruff: ignore[too-many-boolean-expressions]
                    or not getattr(session.client, "is_connected", False)
                    or self._profile_resolver is None
                    or self._profile_resolver(device_id) != expected_profile
                    or self._key_resolver(device_id) != key
                    or is_safe() is not True
                    or session.ota_page_request is not request
                    or request.notify_sequence is None
                    or not session.notify_queue.empty()
                    or (
                        session.notify_inflight is not None
                        and session.notify_inflight[0] > request.notify_sequence
                    )
                    or session.last_ota_reply_sequence >= request.notify_sequence
                ):
                    return False
                session.last_ota_reply_sequence = request.notify_sequence
                await session.client.write_gatt_char(
                    ble.BLE_WRITE_CHAR_UUID, encrypted, response=False
                )
                return True

            return await asyncio.wait_for(write_if_current(), timeout=10.0)

    async def async_send_command(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        *,
        msg_id: int,
        ble_msg_type: int,
        body: bytes,
        timeout_sec: float = 10.0,
        wait_for_ack: bool = False,
        ack_timeout_sec: float = DEFAULT_BLE_ACK_TIMEOUT_SEC,
        mtu_override: int | None = None,
        expected_profile: str | None = None,
        is_safe: Callable[[], bool] | None = None,
    ) -> bool:
        """Send one logical command frame over BLE.

        The call can optionally wait for a matching acknowledgement.

        Parameters:
            device_id (str): Target device identifier.
            msg_id (int): App action identifier stored in the frame flags.
            ble_msg_type (int): BLE command identifier stored in the frame command.
            body (bytes): Command payload bytes.
            timeout_sec (float): Per-GATT-write timeout in seconds.
            wait_for_ack (bool): If True, wait for a matching decoded notify frame
            before returning.
            ack_timeout_sec (float): Timeout in seconds to wait for the ACK when
            `wait_for_ack` is True.
            mtu_override (int | None): Optional MTU to use instead of the negotiated or
            default MTU (used for tests/diagnostics).
            is_safe (Callable[[], bool] | None): Optional guard checked inside the
            session lock and before each GATT chunk.

        Returns:
            bool: `True` if the GATT write completed (and, when requested, a matching
            ACK was received); `False` if no active BLE client exists for the device.

        Raises:
            RuntimeError: When the payload cannot be chunked for the selected MTU, on
            GATT-layer failures (including write timeouts), or when an ACK wait times
            out.
        """
        command = _BleCommand(
            msg_id=msg_id,
            ble_msg_type=ble_msg_type,
            body=body,
            timeout_sec=timeout_sec,
            wait_for_ack=wait_for_ack,
            ack_timeout_sec=ack_timeout_sec,
            mtu_override=mtu_override,
            expected_profile=expected_profile,
            is_safe=is_safe,
        )
        self._validate_command(command)
        return await self._async_send_command(device_id, command)

    @staticmethod
    def _validate_command(command: _BleCommand) -> None:
        """Validate one command before any GATT or ACK state is touched."""
        if (
            isinstance(command.msg_id, bool)
            or not isinstance(command.msg_id, int)
            or not 1 <= command.msg_id <= _UINT16_MAX
        ):
            msg = "msg_id must be an integer in range 1..65535"
            raise ValueError(msg)
        if (
            isinstance(command.ble_msg_type, bool)
            or not isinstance(command.ble_msg_type, int)
            or not 0 <= command.ble_msg_type <= _UINT16_MAX
        ):
            msg = "ble_msg_type must be an integer in range 0..65535"
            raise ValueError(msg)
        if command.mtu_override is not None and (
            isinstance(command.mtu_override, bool)
            or not isinstance(command.mtu_override, int)
        ):
            msg = "mtu_override must be an integer"
            raise ValueError(msg)

    async def _async_send_command(  # ruff: ignore[too-many-branches, too-many-statements, complex-structure]
        self,
        device_id: str,
        command: _BleCommand,
    ) -> bool:
        """Write one validated command through the current session owner."""
        session = self._sessions.get(device_id)
        if (
            session is None
            or not session.notify_started
            or session.write_mtu is None
            or session.write_mtu < _MIN_WRITE_MTU
            or not self._session_is_current(device_id, session)
        ):
            return False
        client = session.client
        if not getattr(client, "is_connected", False):
            return False
        mtu = (
            min(command.mtu_override, session.write_mtu)
            if command.mtu_override is not None
            else session.write_mtu
        )
        # The protocol has no transaction id. Keep one logical write (all chunks
        # plus its optional explicit ACK wait) under the session lock so concurrent
        # service calls cannot interleave.
        async with session.write_lock:
            if (
                not self._session_is_current(device_id, session)  # ruff: ignore[too-many-boolean-expressions]
                or not getattr(client, "is_connected", False)
                or session.ota_events is not None
                or (session.ota_start_consumed and not session.ota_completed)
                or (
                    self._ota_pending_resolver is not None
                    and self._ota_pending_resolver(device_id)
                )
                or (command.is_safe is not None and command.is_safe() is not True)
            ):
                return False
            profile = (
                self._profile_resolver(device_id) if self._profile_resolver else None
            )
            if profile not in {None, "rc4", "aes_fixed", "aes_random", "box_fixed"}:
                msg = f"unknown BLE protocol for device {device_id}"
                raise RuntimeError(msg)
            if command.expected_profile is not None and command.expected_profile != (
                profile or "home"
            ):
                msg = f"BLE device profile changed before write for {device_id}"
                raise RuntimeError(msg)
            if (
                profile == "box_fixed"
                and (
                    command.msg_id,
                    command.ble_msg_type,
                )
                in session.uncertain_ack_pairs
            ):
                msg = (
                    f"previous Box ack is uncertain for {device_id}; reconnect required"
                )
                raise RuntimeError(msg)
            if (
                profile == "box_fixed"
                and (command.msg_id, command.ble_msg_type) in BOX_BLE_WRITE_PAIRS
                and not command.wait_for_ack
            ):
                msg = f"Box write requires an ack for {device_id}"
                raise RuntimeError(msg)
            key = self._key_resolver(device_id)
            if key is None:
                msg = f"no bluetoothKey available for device {device_id}"
                raise RuntimeError(msg)
            body = command.body
            if profile in {"rc4", "aes_fixed"}:
                mtu = min(mtu, 315)
                if body == b"{}":
                    body = b""
            try:
                chunks = ble.split_body_for_mtu(body, mtu)
            except ValueError as err:
                msg = f"BLE MTU {mtu} too small to fit any body for {device_id}: {err}"
                raise RuntimeError(msg) from err
            # Register the ACK *before* the write — otherwise a fast-echoing
            # peripheral could deliver the notify before the future exists.
            pending: _PendingAck | None = None
            if command.wait_for_ack:
                pending = self._register_pending_ack(
                    device_id,
                    session,
                    command.msg_id,
                    command.ble_msg_type,
                    require_msg_id=profile is not None,
                )
            try:
                await self._async_write_command_chunks(
                    device_id,
                    session,
                    chunks,
                    key=key,
                    command=command,
                    profile=profile,
                )
            except TimeoutError as err:
                if profile == "box_fixed" and pending is not None:
                    session.uncertain_ack_pairs.add((
                        command.msg_id,
                        command.ble_msg_type,
                    ))
                self._discard_optional_pending_ack(device_id, pending)
                msg = f"BLE write to {device_id} timed out after {command.timeout_sec}s"
                raise RuntimeError(msg) from err
            except asyncio.CancelledError:
                if profile == "box_fixed" and pending is not None:
                    session.uncertain_ack_pairs.add((
                        command.msg_id,
                        command.ble_msg_type,
                    ))
                self._discard_optional_pending_ack(device_id, pending)
                raise
            except Exception as err:  # bleak surfaces BleakError + variants
                if profile == "box_fixed" and pending is not None:
                    session.uncertain_ack_pairs.add((
                        command.msg_id,
                        command.ble_msg_type,
                    ))
                self._discard_optional_pending_ack(device_id, pending)
                msg = f"BLE write to {device_id} failed: {err}"
                raise RuntimeError(msg) from err
            if pending is not None:
                await self._await_pending_ack(
                    device_id,
                    pending,
                    msg_id=command.msg_id,
                    ble_msg_type=command.ble_msg_type,
                    ack_timeout_sec=command.ack_timeout_sec,
                    quarantine_on_timeout=profile == "box_fixed",
                )
            if command.is_safe is not None and command.is_safe() is not True:
                if profile == "box_fixed" and pending is not None:
                    session.uncertain_ack_pairs.add((
                        command.msg_id,
                        command.ble_msg_type,
                    ))
                msg = f"BLE identity or session changed after write for {device_id}"
                raise RuntimeError(msg)
        return True

    async def _await_pending_ack(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        pending: _PendingAck,
        *,
        msg_id: int,
        ble_msg_type: int,
        ack_timeout_sec: float,
        quarantine_on_timeout: bool = False,
    ) -> None:
        """Wait for a registered ACK future, updating stats and cleanup.

        Parameters:
            device_id (str): Device the ACK is expected from.
            pending (_PendingAck): The pending ACK record to await.
            msg_id (int): Home action message identifier.
            ble_msg_type (int): Home BLE message type paired with the action.
            ack_timeout_sec (float): Timeout in seconds to wait for the ACK.

        Raises:
            RuntimeError: When the ACK wait times out.
        """
        stats = self.stats_for(device_id)
        try:
            # ``shield`` keeps the future alive even if ``wait_for``
            # cancels the inner wait on timeout — we want to discard
            # it ourselves so the notify handler can observe the
            # removal cleanly.
            await asyncio.wait_for(
                asyncio.shield(pending.future), timeout=ack_timeout_sec
            )
        except TimeoutError as err:
            if quarantine_on_timeout:
                pending.session.uncertain_ack_pairs.add((msg_id, ble_msg_type))
            self._discard_pending_ack(device_id, pending)
            stats.acks_timed_out += 1
            stats.last_error = f"ack timeout msgId={msg_id} bleMsgType={ble_msg_type}"
            msg = (
                f"BLE ack timeout for msgId={msg_id} bleMsgType={ble_msg_type} "
                f"on {device_id} after {ack_timeout_sec}s"
            )
            raise RuntimeError(msg) from err
        except asyncio.CancelledError as err:
            if quarantine_on_timeout:
                pending.session.uncertain_ack_pairs.add((msg_id, ble_msg_type))
            self._discard_pending_ack(device_id, pending)
            task = asyncio.current_task()
            if pending.future.cancelled() and (task is None or task.cancelling() == 0):
                failure_reason = pending.failure_reason or (
                    f"BLE session closed while awaiting ack msgId={msg_id} "
                    f"bleMsgType={ble_msg_type} on {device_id}"
                )
                stats.last_error = failure_reason
                raise RuntimeError(failure_reason) from err
            raise
        stats.acks_received += 1
        stats.last_ack_at = datetime.now(UTC)

    # ------------------------------------------------------------------
    # ACK registry (internal)
    # ------------------------------------------------------------------

    def _register_pending_ack(
        self,
        device_id: str,
        session: _GattSession,
        msg_id: int,
        ble_msg_type: int,
        *,
        require_msg_id: bool = False,
    ) -> _PendingAck:
        """Register a pending ACK wait record for the given device.

        Parameters:
            device_id (str): Identifier of the device the ACK is expected from.
            ack_cmds (tuple[int, ...] | None): Optional sequence of acceptable command
            IDs that will satisfy the ACK.
                If `None`, any decoded frame will satisfy the pending ACK.

        Returns:
            _PendingAck: A record containing `expected_cmds` (a `frozenset` of the
            provided command IDs or `None`)
                and `future`, an `asyncio.Future` that will be resolved with the
                matching `ble.BleBinaryFrame`.
        """
        loop = asyncio.get_running_loop()
        pending = _PendingAck(
            expected_msg_id=msg_id,
            expected_ble_msg_type=ble_msg_type,
            registered_notify_sequence=session.notify_sequence,
            future=loop.create_future(),
            session=session,
            require_msg_id=require_msg_id,
        )
        self._pending_acks.setdefault(device_id, []).append(pending)
        return pending

    def _discard_pending_ack(self, device_id: str, pending: _PendingAck) -> None:
        """Remove a pending-ACK record (called on timeout or write failure)."""
        bucket = self._pending_acks.get(device_id)
        if not bucket:
            return
        try:
            bucket.remove(pending)
        except ValueError:
            return
        if not bucket:
            self._pending_acks.pop(device_id, None)
        if not pending.future.done():
            pending.future.cancel()

    def _discard_optional_pending_ack(
        self,
        device_id: str,
        pending: _PendingAck | None,
    ) -> None:
        """Discard an ACK registration when the command created one."""
        if pending is not None:
            self._discard_pending_ack(device_id, pending)

    def _cancel_session_pending_acks(
        self,
        device_id: str,
        session: _GattSession,
        *,
        reason: str | None = None,
    ) -> None:
        """Cancel ACK waits owned by a session and preserve the failure reason."""
        bucket = self._pending_acks.get(device_id)
        if not bucket:
            return
        remaining: list[_PendingAck] = []
        for pending in bucket:
            if pending.session is not session:
                remaining.append(pending)
                continue
            if not pending.future.done():
                pending.failure_reason = reason
                pending.future.cancel()
        if remaining:
            self._pending_acks[device_id] = remaining
        else:
            self._pending_acks.pop(device_id, None)

    def _resolve_pending_acks(
        self,
        device_id: str,
        session: _GattSession | None,
        frame: ble.BleBinaryFrame,
        *,
        notify_sequence: int | None,
        error: str | None = None,
    ) -> None:
        """Fulfil every pending ACK on ``device_id`` matched by ``frame``.

        A pending record with ``expected_cmds=None`` matches any frame.
        Matched records are removed from the registry as they fire so a
        single notify cannot accidentally fulfil the same future twice.
        """
        bucket = self._pending_acks.get(device_id)
        if not bucket or session is None or notify_sequence is None:
            return
        remaining: list[_PendingAck] = []
        for pending in bucket:
            if pending.future.done():
                continue
            if pending.session is not session:
                remaining.append(pending)
                continue
            if notify_sequence <= pending.registered_notify_sequence:
                remaining.append(pending)
                continue
            if frame.cmd == pending.expected_ble_msg_type and (
                not pending.require_msg_id or frame.flags == pending.expected_msg_id
            ):
                if error is None:
                    pending.future.set_result(frame)
                else:
                    pending.future.set_exception(RuntimeError(error))
                continue
            remaining.append(pending)
        if remaining:
            self._pending_acks[device_id] = remaining
        else:
            self._pending_acks.pop(device_id, None)

    # ------------------------------------------------------------------
    # Stats / diagnostics
    # ------------------------------------------------------------------

    def stats_for(self, device_id: str) -> BleListenerStats:
        """Return — and create on demand — the stats record for a device."""
        stats = self._stats.get(device_id)
        if stats is None:
            stats = BleListenerStats()
            self._stats[device_id] = stats
        self._refresh_notification_queue_stats(device_id, stats)
        return stats

    def all_stats(self) -> dict[str, BleListenerStats]:
        """Return the per-device stats map (mutating it is undefined)."""
        for device_id in self._sessions:
            self.stats_for(device_id)
        return self._stats

    def _refresh_notification_queue_stats(
        self,
        device_id: str,
        stats: BleListenerStats | None = None,
    ) -> None:
        """Refresh lossless notification backlog pressure diagnostics."""
        if stats is None:
            stats = self._stats.get(device_id)
            if stats is None:
                return
        session = self._sessions.get(device_id)
        if session is None:
            stats.notify_queue_depth = 0
            stats.notify_queue_bytes = 0
            stats.notify_queue_oldest_age_sec = 0.0
            return
        metadata = session.notify_pending_metadata
        stats.notify_queue_depth = len(metadata)
        stats.notify_queue_bytes = session.notify_pending_bytes
        stats.notify_queue_oldest_age_sec = (
            max(0.0, time.monotonic() - metadata[0][0]) if metadata else 0.0
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self, device_ids: list[str]) -> None:
        """Start BLE advertisement monitoring.

        Registered callbacks spawn per-device connection runners when a
        matching advertisement is observed.

        Parameters:
            device_ids (list[str]): Device IDs to monitor; a background connection task
            will be created lazily for a device the first time an advertisement matching
            the listener's BLE matcher is seen.
        """
        if self._hass.is_stopping:
            return
        stop_task = self._stop_task
        if stop_task is not None and not stop_task.done():
            msg = "Jackery BLE listener teardown is still in progress"
            raise RuntimeError(msg)
        if stop_task is not None:
            if stop_task.cancelled():
                msg = "Jackery BLE listener teardown was cancelled"
                raise RuntimeError(msg)
            stop_task.result()
            self._stop_task = None
        self._stop_event.clear()
        self._configured_device_ids = frozenset(
            normalized
            for device_id in device_ids
            if (normalized := str(device_id).strip())
        )

        try:
            bluetooth_module = cast(
                "_BluetoothModule",
                importlib.import_module("homeassistant.components.bluetooth"),
            )
        except ImportError as err:
            msg = "Home Assistant Bluetooth is not available"
            raise RuntimeError(msg) from err
        self._ha_bluetooth = bluetooth_module

        matcher: BluetoothCallbackMatcher = {
            "service_uuid": ble.BLE_SERVICE_UUID,
        }
        unregister = bluetooth_module.async_register_callback(
            self._hass,
            self._on_advertisement,
            matcher,
            bluetooth_module.BluetoothScanningMode.ACTIVE,
        )
        self._unregister_callbacks.append(unregister)

        # Bind any cache-provided address first. The resolver is optional in
        # practice because HTTP discovery usually provides identity + key, not
        # a stable adapter address, but using it here makes the contract real
        # instead of leaving a constructor dependency unused.
        for device_id in self._configured_device_ids:
            address = self._ble_address_resolver(device_id)
            if isinstance(address, str) and address.strip():
                self._bind_device_address(device_id, address)

        _LOGGER.info(
            "Jackery BLE listener started for %d device(s); waiting for "
            "advertisements with service %s",
            len(device_ids),
            ble.BLE_SERVICE_UUID,
        )
        # Surface the exact matcher tuple so the user can cross-check it
        # against ``bluetooth.async_scanner_devices_by_address`` /
        # ``bluetooth.async_discovered_service_info`` output in the
        # logbook. Without this line a silent listener (zero further
        # callbacks) is indistinguishable from a misconfigured matcher.
        _LOGGER.info(
            "Jackery BLE: matcher registered (service_uuid=%s) "
            "for %d device(s); awaiting advertisements",
            ble.BLE_SERVICE_UUID,
            len(device_ids),
        )

    def _bind_device_address(self, device_id: str, address: str) -> bool:
        """Bind one address to one configured cached identity without ambiguity."""
        normalized_address = str(address).strip()
        if not normalized_address or (
            self._configured_device_ids and device_id not in self._configured_device_ids
        ):
            return False
        for bound_device_id, bound_address in self._device_addresses.items():
            if (
                bound_device_id != device_id
                and bound_address.casefold() == normalized_address.casefold()
            ):
                _LOGGER.warning(
                    "Jackery BLE address matched multiple cached devices; "
                    "rejecting the ambiguous binding",
                )
                return False
        self._device_addresses[device_id] = normalized_address
        return True

    def _spawn_connection_if_ready(self, device_id: str) -> None:
        """Start one non-blocking runner when identity and address are known."""
        address = self._device_addresses.get(device_id)
        if address is None or self._stop_event.is_set() or self._hass.is_stopping:
            return
        existing = self._connections.get(device_id)
        if existing is not None and not existing.done():
            return
        runner = self._async_run_connection(device_id, address)
        try:
            task = self._create_owned_task(
                runner,
                name=f"jackery_ble_{device_id}",
            )
        except Exception as err:  # ruff: ignore[blind-except]
            error = f"runner task creation: {err}"
            stats = self.stats_for(device_id)
            stats.last_error = error
            _LOGGER.warning(
                "Jackery BLE %s connection runner was not accepted by Home "
                "Assistant: %s",
                device_id,
                err,
            )
            return
        self._connections[device_id] = task

    async def async_stop(self) -> None:
        """Stop the listener through one cancellation-safe teardown owner."""
        stop_task = self._stop_task
        if (
            stop_task is not None
            and stop_task.done()
            and (stop_task.cancelled() or stop_task.exception() is not None)
        ):
            stop_task = None
        if stop_task is None:
            stop_task = self._create_owned_task(
                self._async_stop_impl(),
                name="jackery_ble_listener_stop",
            )
            self._stop_task = stop_task
        await asyncio.shield(stop_task)

    async def _async_stop_impl(self) -> None:
        """Stop the BLE listener and release its resources.

        Signals the listener to stop, unregisters Bluetooth advertisement callbacks,
        cancels active connection runner tasks and waits up to _STOP_TIMEOUT_SEC for
        them to exit, clears connection state, and cancels any pending ACK futures so
        callers waiting for acknowledgements do not hang. Logs the listener shutdown.
        """
        self._stop_event.set()
        session_items: tuple[tuple[str, _GattSession], ...] = tuple(
            self._sessions.items(),
        )
        if self._disconnect_callback is not None:
            for device_id, session in session_items:
                self._disconnect_callback(device_id, session.generation)
        self._fence_callbacks_and_ack_waiters()
        await self._async_teardown_sessions(session_items)
        connection_items = await self._async_stop_connections()
        self._discard_stopped_state(session_items, connection_items)
        _LOGGER.info("Jackery BLE listener stopped")

    def _fence_callbacks_and_ack_waiters(self) -> None:
        """Reject new callbacks and release every pending command waiter."""
        for unregister in self._unregister_callbacks:
            try:
                unregister()
            except Exception as err:  # pragma: no cover — HA callback contract is sync
                _LOGGER.debug(
                    "Jackery BLE: callback unregister failed: %s", err, exc_info=True
                )
        self._unregister_callbacks.clear()
        for bucket in self._pending_acks.values():
            for pending in bucket:
                if not pending.future.done():
                    pending.future.cancel()
        self._pending_acks.clear()

    async def _async_teardown_sessions(
        self,
        session_items: tuple[tuple[str, _GattSession], ...],
    ) -> None:
        """Finish every accepted notification stream before stopping runners."""
        if not session_items:
            return
        results = await asyncio.gather(
            *starmap(self._teardown_session, session_items),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if not errors:
            return
        cancelled = next(
            (error for error in errors if isinstance(error, asyncio.CancelledError)),
            None,
        )
        if cancelled is not None:
            raise cancelled
        detail = "; ".join(str(error) for error in errors)
        msg = f"Jackery BLE session teardown failed: {detail}"
        raise RuntimeError(msg)

    async def _async_stop_connections(
        self,
    ) -> list[tuple[str, asyncio.Task[None]]]:
        """Stop runner ownership and retain it when bounded teardown cannot finish."""
        current_task = asyncio.current_task()
        connection_items = list(self._connections.items())
        tasks = list(
            dict.fromkeys(
                task
                for _, task in connection_items
                if not task.done() and task is not current_task
            )
        )
        current_task_owned = current_task is not None and any(
            task is current_task for _, task in connection_items
        )
        still_pending = await self._async_stop_connection_tasks(tasks)
        if not still_pending and not current_task_owned:
            return connection_items
        remaining_count = len(still_pending) + int(current_task_owned)
        _LOGGER.warning(
            "Jackery BLE: %d transport task(s) remain after %ss; retaining "
            "listener ownership for a later teardown retry",
            remaining_count,
            _STOP_TIMEOUT_SEC,
        )
        msg = (
            f"{remaining_count} Jackery BLE transport task(s) did not complete teardown"
        )
        raise RuntimeError(msg)

    def _discard_stopped_state(
        self,
        session_items: tuple[tuple[str, _GattSession], ...],
        connection_items: list[tuple[str, asyncio.Task[None]]],
    ) -> None:
        """Discard state only after physical session and runner ownership ended."""
        for device_id, task in connection_items:
            if self._connections.get(device_id) is task:
                self._connections.pop(device_id, None)
        for device_id, session in session_items:
            session.active = False
            self._signal_ota_session_end(session)
            dropped = self._clear_frame_assemblies(
                device_id, session, reason="transport_shutdown"
            )
            if dropped:
                self.stats_for(device_id).multi_chunk_assemblies_dropped += dropped
            if self._sessions.get(device_id) is not session:
                continue
            self._sessions.pop(device_id, None)
            if self._clients.get(device_id) is session.client:
                self._clients.pop(device_id, None)
            if self._mtu_owners.get(device_id) is session:
                self._mtu_owners.pop(device_id, None)
                self._mtu.pop(device_id, None)
        for device_id, owner in list(self._frame_assembly_owners.items()):
            dropped = self._clear_frame_assemblies(
                device_id, owner, reason="transport_shutdown"
            )
            if dropped:
                self.stats_for(device_id).multi_chunk_assemblies_dropped += dropped
        for device_id in list(self._frame_assemblies):
            if device_id in self._frame_assembly_owners:
                continue
            dropped = self._clear_frame_assemblies(
                device_id, reason="transport_shutdown"
            )
            if dropped:
                self.stats_for(device_id).multi_chunk_assemblies_dropped += dropped

    @staticmethod
    async def _async_stop_connection_tasks(
        tasks: list[asyncio.Task[None]],
    ) -> set[asyncio.Task[None]]:
        """Let runners stop cooperatively, then cancel only those still stuck."""
        if not tasks:
            return set()
        completed, pending = await asyncio.wait(
            tasks,
            timeout=_COOPERATIVE_STOP_GRACE_SEC,
        )
        if pending:
            for task in pending:
                task.cancel()
            cancelled_done, pending = await asyncio.wait(
                pending,
                timeout=_STOP_TIMEOUT_SEC - _COOPERATIVE_STOP_GRACE_SEC,
            )
            completed.update(cancelled_done)
        for task in completed:
            try:
                task.result()
            except asyncio.CancelledError:
                continue
            except Exception as err:  # ruff: ignore[blind-except]
                _LOGGER.warning(
                    "Jackery BLE task %s failed during stop: %s",
                    task.get_name(),
                    err,
                )
        return pending

    # ------------------------------------------------------------------
    # Advertisement -> connect orchestration
    # ------------------------------------------------------------------

    def _on_advertisement(
        self,
        service_info: BluetoothServiceInfoBleak,
        change: BluetoothChange,
    ) -> None:
        """HA bluetooth-callback. Triggers a connect task on first match.

        Per HA's bluetooth-integration contract this is a synchronous
        callback. We may not await anything here; instead we spawn an
        asyncio task on the loop.
        """
        device_id = self._device_id_from_service_info(service_info)
        if device_id is None:
            return
        stats = self.stats_for(device_id)
        stats.advertisements_seen += 1
        # Spawn a *background* task — HA's bluetooth callback contract is
        # strictly sync, so we cannot await the connect here. The helper applies
        # the same backoff/ownership gate used during cache-first startup.
        self._spawn_connection_if_ready(device_id)

    def _device_id_from_service_info(
        self,
        service_info: BluetoothServiceInfoBleak,
    ) -> str | None:
        """Resolve an advertisement to a known Jackery device ID.

        The resolved device's BLE MAC address is cached.

        If the advertisement corresponds to a device the integration knows about, the
        function records device_id -> address in the internal cache on first match so
        future advertisements skip resolution. It returns the mapped device id when
        found, or `None` if no mapping could be determined.

        Returns:
            `device_id` if the advertisement maps to a known device, `None` otherwise.
        """
        address = service_info.address
        # Step 1: address cache hit.
        for cached_id, cached_mac in self._device_addresses.items():
            if cached_mac.upper() == address.upper():
                return cached_id
        # App manufacturer keys encode the category and the first serial byte.
        matches: set[str] = set()
        for manufacturer_id, mfr_data in (service_info.manufacturer_data or {}).items():
            prefix, category = divmod(manufacturer_id, 256)
            if category not in {1, 2, 8, 9} or not (
                _ASCII_SERIAL_MIN <= prefix <= _ASCII_SERIAL_MAX
            ):
                continue
            try:
                serial = mfr_data.decode("ascii").strip()
            except UnicodeDecodeError:
                continue
            if not serial or self._serial_resolver is None:
                continue
            for candidate in (chr(prefix) + serial, serial):
                device_id = self._serial_resolver(candidate)
                if device_id is not None:
                    matches.add(device_id)
        if len(matches) != 1:
            return None
        device_id = next(iter(matches))
        # Step 4: bind only an unambiguous configured identity. Raw serials and
        # adapter addresses are intentionally absent from support logs.
        if not self._bind_device_address(device_id, address):
            return None
        if self.stats_for(device_id).advertisements_seen == 0:
            _LOGGER.info(
                "Jackery BLE: matched an advertisement to a cached device identity",
            )
        return device_id

    # One cancellation boundary must own connect, notify, teardown and retry.
    async def _async_run_connection(  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches, too-many-statements]
        self,
        device_id: str,
        address: str,
    ) -> None:
        """Maintain a persistent BLE GATT session for one device.

        The session subscribes to notifications and reconnects on link loss.

        This coroutine opens and publishes a Bleak client for the given address,
        subscribes to the notify characteristic, monitors the link, and tears down and
        retries the session on disconnect until the listener stops or the task cancels.

        Raises:
            asyncio.CancelledError: if the task is cancelled during shutdown.
        """
        stats = self.stats_for(device_id)
        runner_task = asyncio.current_task()
        try:  # ruff: ignore[too-many-statements-in-try-clause] - the try deliberately owns the runner lifecycle
            while not self._stop_event.is_set() and not self._hass.is_stopping:
                retained_session = self._sessions.get(device_id)
                if retained_session is not None:
                    try:
                        await self._teardown_session(device_id, retained_session)
                    except asyncio.CancelledError:
                        raise
                    except Exception as err:  # ruff: ignore[blind-except]
                        stats.last_error = f"retained teardown: {err}"
                        delay = self._connect_backoff_note_failure(
                            device_id,
                            asyncio.get_running_loop().time(),
                        )
                        self._log_connection_unavailable(
                            device_id,
                            "Jackery BLE %s retained GATT teardown failed: %s; "
                            "retrying in %ss",
                            device_id,
                            err,
                            delay,
                        )
                        if self._stop_event.is_set() or (
                            await self._async_stop_requested_within(delay)
                        ):
                            return
                        continue
                remaining = self._connect_backoff_remaining(
                    device_id,
                    asyncio.get_running_loop().time(),
                )
                if remaining > 0:
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=remaining,
                        )
                    except TimeoutError:
                        pass
                    else:
                        return
                if self._stop_event.is_set() or self._hass.is_stopping:
                    return
                bluetooth_module = self._ha_bluetooth
                if bluetooth_module is None:
                    stats.last_error = "Home Assistant Bluetooth is not loaded"
                    return
                ble_device = bluetooth_module.async_ble_device_from_address(
                    self._hass, address, connectable=True
                )
                if ble_device is None:
                    # PROTOCOL.md §4: the SolarVault peripheral typically
                    # stops advertising once a central is connected. After
                    # a drop, the HA bluetooth manager may therefore not
                    # hold a fresh ``BLEDevice`` for the cached MAC for a
                    # while — yet a new advertisement is what would
                    # otherwise be needed to spawn a new connection task.
                    # Returning here would kill the runner and require an
                    # external trigger (new advertisement) to reconnect,
                    # which can take minutes or never happen. Instead we
                    # wait on the per-device backoff and look the address up
                    # again. ``async_ble_device_from_address``
                    # is cheap and idempotent; the matcher callback in
                    # parallel still works, and ``_stop_event`` aborts the
                    # wait cleanly on integration unload.
                    delay = self._connect_backoff_note_failure(
                        device_id, asyncio.get_running_loop().time()
                    )
                    self._log_connection_unavailable(
                        device_id,
                        "Jackery BLE %s: cached peripheral is not connectable right "
                        "now; retrying in %ss",
                        device_id,
                        delay,
                    )
                    if await self._async_stop_requested_within(delay):
                        return
                    continue
                stats.connect_attempts += 1
                generation = self._next_session_generation(device_id)

                def _disconnected_callback(
                    disconnected_client: BleakClient,
                    _generation: int = generation,
                ) -> None:
                    """Record a disconnect for this session generation."""
                    self._on_disconnect(
                        device_id,
                        generation=_generation,
                        client=disconnected_client,
                    )

                try:
                    client = await establish_connection(
                        client_class=BleakClient,
                        device=ble_device,
                        name=f"jackery-{device_id}",
                        disconnected_callback=_disconnected_callback,
                        max_attempts=1,
                    )
                except BLEAK_RETRY_EXCEPTIONS as err:
                    if self._stop_event.is_set() or self._hass.is_stopping:
                        return
                    stats.connect_failures += 1
                    stats.last_error = f"connect: {err}"
                    # PROTOCOL.md §4: the peripheral may stop advertising
                    # while paired with another central, so a one-shot
                    # ``return`` here would leave the runner dead until a
                    # fresh advertisement arrived. Back off and try again.
                    # One library-level attempt keeps the integration's own
                    # proxy-safe backoff in control of the retry cadence.
                    delay = self._connect_backoff_note_failure(
                        device_id, asyncio.get_running_loop().time()
                    )
                    self._log_connection_unavailable(
                        device_id,
                        "Jackery BLE %s connect failed: %s; retrying in %ss",
                        device_id,
                        err,
                        delay,
                    )
                    if await self._async_stop_requested_within(delay):
                        return
                    continue

                if (
                    self._stop_event.is_set()
                    or self._hass.is_stopping
                    or not self._connection_is_current(device_id, runner_task)
                ):
                    try:
                        await asyncio.wait_for(client.disconnect(), timeout=5.0)
                    except Exception as err:  # ruff: ignore[blind-except]
                        _LOGGER.warning(
                            "Jackery BLE: disconnect of superseded connection "
                            "to %s failed: %s",
                            device_id,
                            err,
                        )
                    return

                try:
                    session = self._install_session(device_id, client, generation)
                except BaseException:
                    # The physical connection exists, but ownership was never
                    # published. Release that exact client before propagating.
                    try:
                        await asyncio.wait_for(client.disconnect(), timeout=5.0)
                    except Exception as disconnect_err:  # ruff: ignore[blind-except]
                        _LOGGER.warning(
                            "Jackery BLE: disconnect of unowned connection to %s "
                            "failed: %s",
                            device_id,
                            disconnect_err,
                        )
                    raise
                stats.last_connect_at = datetime.now(UTC)

                def _notify_callback(
                    _characteristic: object,
                    data: bytearray,
                    _session: _GattSession = session,
                ) -> None:
                    """Copy a Bleak notification into the ordered session queue."""
                    self._schedule_notification(device_id, _session, bytes(data))

                stable_session_started_at: float | None = None
                backoff_reset = False
                try:  # ruff: ignore[too-many-statements-in-try-clause] - this owns subscribe, monitor, and teardown state
                    await client.start_notify(
                        ble.BLE_NOTIFY_CHAR_UUID, _notify_callback
                    )
                    session.notify_started = True
                    self._log_connection_available(
                        device_id,
                        ble.BLE_NOTIFY_CHAR_UUID,
                    )
                    stable_session_started_at = asyncio.get_running_loop().time()
                    # Cache the negotiated MTU so ``async_send_command``
                    # can size per-frame bodies correctly. Different
                    # bleak backends expose this via ``mtu_size`` (int)
                    # or sometimes ``mtu``; both are best-effort.
                    self._record_negotiated_mtu(device_id, client, session=session)
                    # Park the connection until the device drops it or we are
                    # asked to stop. We poll ``client.is_connected`` (a bleak
                    # property with no awaitable) alongside the stop event: the
                    # disconnect callback only records stats, and bleak backends
                    # do not all fire it reliably, so the 1s poll is a deliberate
                    # robustness net that a single ``Event.wait()`` cannot replace
                    # (hence ASYNC110 is suppressed below).
                    while (
                        not self._stop_event.is_set()
                        and not self._hass.is_stopping
                        and client.is_connected
                        and self._session_is_current(device_id, session)
                    ):
                        if not backoff_reset:
                            backoff_reset = self._reset_backoff_after_stable_session(
                                device_id,
                                started_at=stable_session_started_at,
                                now=asyncio.get_running_loop().time(),
                            )
                        await asyncio.sleep(1.0)
                except BLEAK_RETRY_EXCEPTIONS as err:
                    stats.last_error = f"notify: {err}"
                    _LOGGER.debug(
                        "Jackery BLE %s notify subscribe failed: %s",
                        device_id,
                        err,
                    )
                finally:
                    if stable_session_started_at is not None and not backoff_reset:
                        self._reset_backoff_after_stable_session(
                            device_id,
                            started_at=stable_session_started_at,
                            now=asyncio.get_running_loop().time(),
                        )
                    await self._teardown_session(device_id, session)
                    stats.last_disconnect_at = datetime.now(UTC)

                if self._stop_event.is_set() or self._hass.is_stopping:
                    return
                if (
                    session.identity_refresh_requested
                    and device_id not in self._identity_refresh_seen
                ):
                    self._identity_refresh_seen.add(device_id)
                    continue
                # PROTOCOL.md §4: BLE peripherals routinely drop idle
                # sessions; the surrounding ``while not self._stop_event``
                # loop reconnects after this backoff. Logged at INFO so
                # the user sees the reconnect cadence in default logs
                # (previously DEBUG, which made the silence invisible).
                delay = self._connect_backoff_note_failure(
                    device_id,
                    asyncio.get_running_loop().time(),
                )
                self._log_connection_unavailable(
                    device_id,
                    (
                        "Jackery BLE %s: repeated identity changes, "
                        "backoff %ss before retry"
                        if session.identity_refresh_requested
                        else "Jackery BLE %s: lost link, backoff %ss before retry"
                    ),
                    device_id,
                    delay,
                )
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            # HA shutdown / coordinator unload. Re-raise so the gather()
            # in async_stop() observes the cancellation cleanly. Do NOT
            # call client.disconnect() again — the inner ``finally`` above
            # already handled it, and another await would race against
            # the event loop tearing down.
            _LOGGER.debug(
                "Jackery BLE %s: connection runner cancelled (shutdown)",
                device_id,
            )
            raise
        except Exception as err:  # pragma: no cover — defensive
            stats.last_error = f"runner: {err}"
            _LOGGER.exception("Jackery BLE %s: connection runner crashed", device_id)
        finally:
            if self._connections.get(device_id) is runner_task:
                self._connections.pop(device_id, None)
            # PROTOCOL.md §4: the runner is the only thing keeping the
            # device's GATT session alive. Any exit path (stop event,
            # cancel, unhandled exception) means no further notifies will
            # arrive until a new advertisement spawns a new task via
            # ``_on_advertisement``. Log at INFO so a silent integration
            # has a discoverable cause in the default log level — without
            # this line the user reproduces the "BLE doesn't reconnect"
            # symptom with no trace of the runner ever having existed.
            _LOGGER.info(
                "Jackery BLE %s: connection runner exited "
                "(stop_event=%s); awaiting next advertisement to respawn",
                device_id,
                self._stop_event.is_set(),
            )

    def _on_disconnect(
        self,
        device_id: str,
        *,
        generation: int | None = None,
        client: BleakClient | None = None,
    ) -> None:
        """Handle a peripheral disconnect for the given device.

        Updates the device's `BleListenerStats.last_disconnect_at` to the current time
        and emits an info-level log indicating the device disconnected.

        Parameters:
            device_id (str): Identifier of the device whose disconnect is being
            recorded.
        """
        if generation is not None or client is not None:
            session = self._sessions.get(device_id)
            if (
                session is None
                or session.generation != generation
                or session.client is not client
            ):
                return
            session.active = False
            session.accepting_notifications = False
            session.notify_started = False
            self._signal_ota_session_end(session)
            if generation is not None and self._disconnect_callback is not None:
                self._disconnect_callback(device_id, generation)
        stats = self.stats_for(device_id)
        stats.last_disconnect_at = datetime.now(UTC)
        # Promoted from DEBUG to INFO: peripheral disconnects are the
        # primary symptom of BLE silence and must be visible in default
        # HA logs so the user can correlate them with reconnect backoff.
        _LOGGER.info("Jackery BLE %s: peripheral disconnected", device_id)

    # ------------------------------------------------------------------
    # Notification handler
    # ------------------------------------------------------------------

    @staticmethod
    def _record_multi_chunk_drop_reason(
        stats: BleListenerStats, cmd: int, reason: str
    ) -> None:
        """Classify an assembly loss without retaining payload data."""
        key = f"cmd{cmd}:{reason}"
        stats.multi_chunk_drop_reasons[key] = (
            stats.multi_chunk_drop_reasons.get(key, 0) + 1
        )

    # Splitting this bounded state machine would fragment its ownership transaction.
    def _reassemble_frame(  # ruff: ignore[complex-structure, too-many-locals, too-many-branches, too-many-statements, too-many-arguments]
        self,
        device_id: str,
        frame: ble.BleBinaryFrame,
        *,
        session: _GattSession | None = None,
        notify_sequence: int | None = None,
        received_monotonic: float | None = None,
        received_at: datetime | None = None,
        accepted: bool = False,
        notification_key: bytes | None = None,
    ) -> tuple[ble.BleBinaryFrame | None, int | None]:
        """Return a complete frame and its earliest queued notification sequence."""
        stats = self.stats_for(device_id)
        if session is not None:
            owns_session = (
                self._accepted_notification_session_owns_connection(
                    device_id,
                    session,
                )
                if accepted
                else self._session_is_current(device_id, session)
            )
            if not owns_session:
                stats.multi_chunk_owner_rejected_by_cmd[frame.cmd] = (
                    stats.multi_chunk_owner_rejected_by_cmd.get(frame.cmd, 0) + 1
                )
                if frame.chunk_count > 1 and frame.frame_index == frame.chunk_count:
                    current = self._sessions.get(device_id)
                    stats.last_multi_chunk_final_incomplete_by_cmd[frame.cmd] = {
                        "reason": "session_not_owner",
                        "flags": frame.flags,
                        "present_indices": [],
                        "session_generation": session.generation,
                        "current_generation": (
                            current.generation if current is not None else None
                        ),
                        "notify_sequence": notify_sequence,
                    }
                return None, None
        # Queue delivery latency must not count as a gap between received chunks.
        now = (
            received_monotonic
            if received_monotonic is not None
            else self._hass.loop.time()
        )
        if session is not None:
            owner = self._frame_assembly_owners.get(device_id)
            if owner is not session:
                for cmd, _flags in self._frame_assemblies.get(device_id, {}):
                    self._record_multi_chunk_drop_reason(stats, cmd, "owner_switch")
                if owner is not None:
                    dropped = self._clear_frame_assemblies(device_id, owner)
                    if dropped:
                        stats.multi_chunk_assemblies_dropped += dropped
                elif self._frame_assemblies.get(device_id):
                    dropped = self._clear_frame_assemblies(device_id)
                    if dropped:
                        stats.multi_chunk_assemblies_dropped += dropped
                self._frame_assembly_owners[device_id] = session
        assemblies = self._frame_assemblies.get(device_id)
        if assemblies:
            expired = [
                key
                for key, assembly in assemblies.items()
                if now - assembly.updated_at > _REASSEMBLY_TIMEOUT_SEC
            ]
            for key in expired:
                assemblies.pop(key, None)
                cmd = key[0]
                self._record_multi_chunk_drop_reason(stats, cmd, "expired")
                stats.multi_chunk_expired_by_cmd[cmd] = (
                    stats.multi_chunk_expired_by_cmd.get(cmd, 0) + 1
                )
                recover = getattr(self, "_on_reassembly_gap", None)
                if callable(recover):
                    try:
                        recover(device_id, cmd, key[1])
                    except Exception:
                        # A failed retry scheduler must never discard another frame.
                        _LOGGER.exception(
                            "BLE missing-chunk recovery scheduling failed"
                        )
            stats.multi_chunk_assemblies_dropped += len(expired)
            if not assemblies:
                self._clear_frame_assemblies(device_id, session)
                assemblies = None

        if frame.chunk_count == 1:
            if frame.frame_index != 1:
                msg = (
                    "single-chunk BLE frame has invalid frame_index "
                    f"{frame.frame_index}"
                )
                raise ValueError(msg)
            return frame, notify_sequence
        if not _MIN_MULTI_CHUNK_COUNT <= frame.chunk_count <= _REASSEMBLY_MAX_CHUNKS:
            msg = f"BLE chunk_count {frame.chunk_count} is outside the supported range"
            raise ValueError(msg)
        if not 1 <= frame.frame_index <= frame.chunk_count:
            msg = (
                f"BLE frame_index {frame.frame_index} is outside 1..{frame.chunk_count}"
            )
            raise ValueError(msg)

        assemblies = self._frame_assemblies.setdefault(device_id, {})
        if session is not None:
            self._frame_assembly_owners[device_id] = session

        key = (frame.cmd, frame.flags)
        assembly = assemblies.get(key)
        if (
            (
                frame.cmd in {100, 101, 102, 103, 104}
                or (frame.flags, frame.cmd) in {(3011, 106), (6, 3)}
                or (frame.flags, frame.cmd) in {(252, 9), (248, 100)}
            )
            and assembly is not None
            and assembly.ota_key != notification_key
        ):
            assemblies.pop(key, None)
            self._record_multi_chunk_drop_reason(stats, frame.cmd, "ota_key_changed")
            stats.multi_chunk_assemblies_dropped += 1
            msg = "OTA fragment key changed during reassembly"
            raise ValueError(msg)
        if assembly is not None and assembly.chunk_count != frame.chunk_count:
            assemblies.pop(key, None)
            self._record_multi_chunk_drop_reason(
                stats, frame.cmd, "chunk_count_changed"
            )
            stats.multi_chunk_assemblies_dropped += 1
            assembly = None
        if assembly is not None and frame.frame_index == 1:
            first = assembly.frames.get(1)
            if first is not None and (
                first.body != frame.body or first.trailer != frame.trailer
            ):
                assemblies.pop(key, None)
                self._record_multi_chunk_drop_reason(
                    stats, frame.cmd, "first_chunk_changed"
                )
                stats.multi_chunk_assemblies_dropped += 1
                recover = getattr(self, "_on_reassembly_gap", None)
                if callable(recover):
                    try:
                        recover(device_id, frame.cmd, frame.flags)
                    except Exception:
                        # A failed retry scheduler must never stop fragment ingress.
                        _LOGGER.exception(
                            "BLE changed-prefix recovery scheduling failed"
                        )
                assembly = None
        if assembly is None:
            if len(assemblies) >= _REASSEMBLY_MAX_MESSAGES_PER_DEVICE:
                oldest_key = min(
                    assemblies,
                    key=lambda candidate: assemblies[candidate].updated_at,
                )
                assemblies.pop(oldest_key, None)
                self._record_multi_chunk_drop_reason(stats, oldest_key[0], "capacity")
                stats.multi_chunk_assemblies_dropped += 1
            assembly = _PendingFrameAssembly(
                chunk_count=frame.chunk_count,
                frames={},
                updated_at=now,
                first_notify_sequence=notify_sequence,
                ota_key=notification_key,
                first_received_at=received_at,
                first_received_monotonic=received_monotonic,
            )
            assemblies[key] = assembly
        elif assembly.first_notify_sequence is None or notify_sequence is None:
            assembly.first_notify_sequence = None
        else:
            assembly.first_notify_sequence = min(
                assembly.first_notify_sequence,
                notify_sequence,
            )

        prior = assembly.frames.get(frame.frame_index)
        if prior is not None:
            if prior.body != frame.body or prior.trailer != frame.trailer:
                assemblies.pop(key, None)
                self._record_multi_chunk_drop_reason(
                    stats, frame.cmd, "duplicate_changed"
                )
                stats.multi_chunk_assemblies_dropped += 1
                assembly = _PendingFrameAssembly(
                    chunk_count=frame.chunk_count,
                    frames={frame.frame_index: frame},
                    updated_at=now,
                    first_notify_sequence=notify_sequence,
                    ota_key=notification_key,
                    first_received_at=received_at,
                    first_received_monotonic=received_monotonic,
                )
                assemblies[key] = assembly
                stats.multi_chunk_frames_buffered += 1
        else:
            assembly.frames[frame.frame_index] = frame
            stats.multi_chunk_frames_buffered += 1
        assembly.updated_at = now

        body_size = sum(len(chunk.body) for chunk in assembly.frames.values())
        if body_size > _REASSEMBLY_MAX_BODY_BYTES:
            assemblies.pop(key, None)
            self._record_multi_chunk_drop_reason(stats, frame.cmd, "body_too_large")
            stats.multi_chunk_assemblies_dropped += 1
            msg = f"assembled BLE body exceeds {_REASSEMBLY_MAX_BODY_BYTES} bytes"
            raise ValueError(msg)
        if len(assembly.frames) < assembly.chunk_count:
            if frame.frame_index == frame.chunk_count:
                stats.multi_chunk_final_incomplete_by_cmd[frame.cmd] = (
                    stats.multi_chunk_final_incomplete_by_cmd.get(frame.cmd, 0) + 1
                )
                stats.last_multi_chunk_final_incomplete_by_cmd[frame.cmd] = {
                    "reason": "missing_prior_chunks",
                    "flags": frame.flags,
                    "present_indices": sorted(assembly.frames),
                    "session_generation": (
                        session.generation if session is not None else None
                    ),
                    "current_generation": (
                        self._sessions[device_id].generation
                        if device_id in self._sessions
                        else None
                    ),
                    "notify_sequence": notify_sequence,
                }
            return None, None
        missing = [
            index
            for index in range(1, assembly.chunk_count + 1)
            if index not in assembly.frames
        ]
        if missing:
            return None, None

        last_frame = assembly.frames[assembly.chunk_count]
        combined = ble.BleBinaryFrame(
            frame_index=1,
            chunk_count=assembly.chunk_count,
            flags=frame.flags,
            cmd=frame.cmd,
            body=b"".join(
                assembly.frames[index].body
                for index in range(1, assembly.chunk_count + 1)
            ),
            trailer=last_frame.trailer,
            checksum_valid=all(
                chunk.checksum_valid is True for chunk in assembly.frames.values()
            ),
        )
        first_notify_sequence = assembly.first_notify_sequence
        assemblies.pop(key, None)
        if not assemblies:
            self._clear_frame_assemblies(device_id, session)
        stats.multi_chunk_messages_assembled += 1
        return combined, first_notify_sequence

    @staticmethod
    def _record_sink_failure(
        device_id: str,
        stats: BleListenerStats,
        detail: str,
    ) -> None:
        """Record a transient sink failure without declaring the frame delivered."""
        error = f"sink failed: {detail}"
        if stats.last_sink_error is None:
            _LOGGER.warning("Jackery BLE %s: %s", device_id, error)
        stats.last_sink_error = error
        stats.last_error = error

    async def _async_deliver_observation(
        self,
        device_id: str,
        observation: BleFrameObservation,
        stats: BleListenerStats,
    ) -> bool:
        """Deliver one accepted observation losslessly with bounded retry pacing."""
        retry_delay = _SINK_RETRY_INITIAL_SEC
        while True:
            try:
                sink_processed = await self._sink(device_id, observation)
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is None or current_task.cancelling():
                    raise
                self._record_sink_failure(
                    device_id,
                    stats,
                    "callback raised CancelledError before accepting frame",
                )
            except Exception as err:  # ruff: ignore[blind-except]
                self._record_sink_failure(device_id, stats, str(err))
            else:
                if sink_processed is BleProcessDisposition.RETRY:
                    self._record_sink_failure(
                        device_id, stats, "coordinator requested retry"
                    )
                else:
                    previous_sink_error = stats.last_sink_error
                    if sink_processed and previous_sink_error is not None:
                        stats.last_sink_error = None
                        if stats.last_error == previous_sink_error:
                            stats.last_error = stats.last_decode_error
                        _LOGGER.info(
                            "Jackery BLE %s: coordinator sink recovered",
                            device_id,
                        )
                    return bool(sink_processed)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, _SINK_RETRY_MAX_SEC)

    def _notification_session_is_valid(
        self,
        device_id: str,
        session: _GattSession | None,
        *,
        accepted: bool,
    ) -> bool:
        """Return whether a notification may still use this session owner."""
        return (
            session is None or accepted or self._session_is_current(device_id, session)
        )

    def _decode_notification_payload(  # ruff: ignore[too-many-return-statements]
        self,
        device_id: str,
        raw: bytes,
    ) -> tuple[ble.BleBinaryFrame | None, str | None, int | None]:
        """Decode raw or proxy-base64-wrapped bytes without discarding either path."""
        key = self._key_resolver(device_id)
        if key is None:
            return None, "no bluetoothKey for device", None
        profile = self._profile_resolver(device_id) if self._profile_resolver else None
        if profile not in {None, "rc4", "aes_fixed", "aes_random", "box_fixed"}:
            return None, "unknown BLE protocol for device", None
        if profile == "box_fixed":
            try:
                status, frame = ble.decode_box_notify(raw, key)
            except ValueError as err:
                return None, str(err), None
            if status:
                return frame, f"box notification status {status}", status
            return frame, None, status
        if profile in {"rc4", "aes_fixed"}:
            try:
                status, frame = ble.decode_portable_legacy_notify(
                    raw, key, cipher=profile
                )
            except ValueError as err:
                return None, str(err), None
            if status:
                return frame, f"portable notification status {status}", status
            return frame, None, status
        try:
            status, frame = ble.decode_binary_notify(raw, key)
        except ValueError as first_error:
            try:
                decoded = base64.b64decode(raw, validate=False)
                status, frame = ble.decode_binary_notify(decoded, key)
            except (ValueError, binascii.Error):
                return None, str(first_error), None
        if status:
            return frame, f"notification status {status}", status
        return frame, None, status

    @staticmethod
    def _record_decode_recovery(
        device_id: str,
        stats: BleListenerStats,
    ) -> None:
        """Count a decoded frame and clear only the matching stale error."""
        stats.frames_decoded += 1
        previous_error = stats.last_decode_error
        if previous_error is None:
            return
        stats.last_decode_error = None
        if stats.last_error == previous_error:
            stats.last_error = stats.last_sink_error
        _LOGGER.info("Jackery BLE %s: notification decoding recovered", device_id)

    @staticmethod
    def _record_decode_failure(
        device_id: str,
        stats: BleListenerStats,
        decode_error: str | None,
    ) -> None:
        """Count a failed frame and log only the first consecutive failure."""
        stats.frames_decode_failed += 1
        if decode_error is None:
            return
        error = f"notify: {decode_error}"
        if stats.last_decode_error is None:
            _LOGGER.warning(
                "Jackery BLE %s notification decode failed: %s",
                device_id,
                decode_error,
            )
        stats.last_decode_error = error
        stats.last_error = error

    def _continues_pending_assembly(
        self,
        device_id: str,
        frame: ble.BleBinaryFrame,
        session: _GattSession | None,
        received_monotonic: float | None,
    ) -> bool:
        """Recognize an owned, unexpired continuation before treating JSON as a page."""
        assembly = self._frame_assemblies.get(device_id, {}).get((
            frame.cmd,
            frame.flags,
        ))
        if (
            frame.frame_index <= 1
            or assembly is None
            or assembly.chunk_count != frame.chunk_count
            or 1 not in assembly.frames
        ):
            return False
        if session is not None and (
            self._frame_assembly_owners.get(device_id) is not session
        ):
            return False
        now = (
            received_monotonic
            if received_monotonic is not None
            else self._hass.loop.time()
        )
        return now - assembly.updated_at <= _REASSEMBLY_TIMEOUT_SEC

    def _reset_assembly_for_standalone_page(
        self,
        device_id: str,
        frame: ble.BleBinaryFrame,
        session: _GattSession | None,
    ) -> None:
        """Release only the owned stale prefix superseded by a new page one."""
        if frame.frame_index != 1 or (
            session is not None
            and self._frame_assembly_owners.get(device_id) is not session
        ):
            return
        pending = self._frame_assemblies.get(device_id, {})
        if pending.pop((frame.cmd, frame.flags), None) is None:
            return
        stats = self.stats_for(device_id)
        self._record_multi_chunk_drop_reason(
            stats, frame.cmd, "standalone_page_restart"
        )
        stats.multi_chunk_assemblies_dropped += 1
        if not pending:
            self._clear_frame_assemblies(device_id, session)

    async def _handle_notification(  # ruff: ignore[too-many-arguments, too-many-branches, too-many-locals, too-many-statements, complex-structure]
        self,
        device_id: str,
        raw: bytes,
        *,
        session: _GattSession | None = None,
        notify_sequence: int | None = None,
        received_at: datetime | None = None,
        received_monotonic: float | None = None,
        accepted: bool = False,
    ) -> None:
        """Process one BLE notification.

        Decode it into a BleFrameObservation, update per-device statistics,
        resolve pending ACK waiters for a successfully parsed frame, and
        forward the observation to the configured async sink.

        If a per-device AES key is available the method attempts to decrypt the raw
        payload and, on decryption failure, performs a single fallback try after
        base64-decoding the payload. A BleFrameObservation (containing the original
        bytes, base64 encoding, the parsed frame when decoding succeeds, or a
        human-readable decode error) is always created and forwarded to the sink. When a
        frame is parsed successfully this method resolves matching pending ACK futures
        and increments decode-related counters; when parsing fails it increments the
        decode-failure counter.
        """
        if not self._notification_session_is_valid(
            device_id,
            session,
            accepted=accepted,
        ):
            return
        observed_at = received_at or datetime.now(UTC)
        session_generation = session.generation if session is not None else None
        delivery_id = (
            f"{self._delivery_namespace}:{device_id}:"
            f"{session_generation}:{notify_sequence}"
            if session_generation is not None and notify_sequence is not None
            else None
        )
        stats = self.stats_for(device_id)
        stats.frames_received += 1
        b64 = base64.b64encode(raw).decode("ascii")
        if _should_log_ble_notification(stats.frames_received):
            _LOGGER.debug(
                "Jackery BLE %s notifications: frames=%d latest_bytes=%d",
                device_id,
                stats.frames_received,
                len(raw),
            )

        notification_key = self._key_resolver(device_id)
        parsed, decode_error, protocol_status = self._decode_notification_payload(
            device_id, raw
        )
        if session is not None and (
            decode_error is not None
            or (parsed is not None and parsed.cmd in {101, 102, 103, 104})
        ):
            session.ota_page_request = None
            session.ota_page_key = None
            session.ota_count_request = None
            session.ota_count_key = None
        if (
            session is not None
            and decode_error is not None
            and self._frame_assembly_owners.get(device_id) is session
        ):
            assemblies = self._frame_assemblies.get(device_id, {})
            ota_keys = [
                key
                for key in assemblies
                if key[0] in {101, 102, 103, 104} or key in {(9, 252), (100, 248)}
            ]
            for ota_key in ota_keys:
                assemblies.pop(ota_key, None)
                self._record_multi_chunk_drop_reason(stats, ota_key[0], "decode_error")
            stats.multi_chunk_assemblies_dropped += len(ota_keys)
            if not assemblies:
                self._clear_frame_assemblies(device_id, session)
        rejected_frame = None
        ack_notify_sequence = notify_sequence
        safety_received_at: datetime | None = observed_at
        safety_received_monotonic = received_monotonic
        if parsed is not None and decode_error is not None:
            self._resolve_pending_acks(
                device_id,
                session,
                parsed,
                notify_sequence=notify_sequence,
                error=decode_error,
            )
            rejected_frame = parsed
            parsed = None

        if parsed is not None:
            assembled: ble.BleBinaryFrame | None
            previous_assembly = None
            profile = (
                self._profile_resolver(device_id) if self._profile_resolver else None
            )
            portable_paged = parsed.chunk_count > 1 and (
                profile in {"rc4", "aes_fixed", "aes_random", "box_fixed"}
                or parsed.cmd in {100, 101, 102, 103, 104}
            )
            if (
                not portable_paged
                and _body_is_complete_json_object(parsed.body)
                and not self._continues_pending_assembly(
                    device_id, parsed, session, received_monotonic
                )
            ):
                self._reset_assembly_for_standalone_page(device_id, parsed, session)
                assembled = parsed
                if parsed.chunk_count > 1 and (parsed.flags, parsed.cmd) == (3011, 106):
                    assembled = replace(parsed, checksum_valid=False)
            else:
                previous_assembly = self._frame_assemblies.get(device_id, {}).get((
                    parsed.cmd,
                    parsed.flags,
                ))
                try:
                    assembled, ack_notify_sequence = self._reassemble_frame(
                        device_id,
                        parsed,
                        session=session,
                        notify_sequence=notify_sequence,
                        received_monotonic=received_monotonic,
                        received_at=observed_at,
                        accepted=accepted,
                        notification_key=notification_key,
                    )
                except ValueError as err:
                    parsed = None
                    assembled = None
                    decode_error = f"reassembly: {err}"
                    if session is not None:
                        session.ota_page_request = None
                        session.ota_page_key = None
                        session.ota_count_request = None
                        session.ota_count_key = None
            if parsed is not None:
                self._record_decode_recovery(device_id, stats)
                if assembled is None:
                    observation = BleFrameObservation(
                        received_at=observed_at,
                        raw_bytes=raw,
                        base64_encoded=b64,
                        parsed=parsed,
                        session_generation=session_generation,
                        notify_sequence=notify_sequence,
                        delivery_id=delivery_id,
                        is_fragment=True,
                        protocol_status=protocol_status,
                        device_identity=session.device_identity
                        if session is not None
                        else None,
                        session_key_fingerprint=session.key_fingerprint
                        if session is not None
                        else None,
                        received_at_monotonic=received_monotonic,
                        key_fingerprint=(
                            hashlib.sha256(notification_key).digest()
                            if notification_key is not None
                            else None
                        ),
                    )
                    stats.last_frame = observation
                    # A fragment reaches diagnostics immediately but resolves no
                    # ACK until the complete body has been assembled.
                    _LOGGER.debug(
                        "Jackery BLE %s buffered: cmd=%d frame=%d/%d "
                        "(awaiting remaining chunk(s))",
                        device_id,
                        parsed.cmd,
                        parsed.frame_index,
                        parsed.chunk_count,
                    )
                    await self._async_deliver_observation(
                        device_id,
                        observation,
                        stats,
                    )
                    return
                if assembled.chunk_count > 1:
                    if (
                        previous_assembly is None
                        or ack_notify_sequence
                        != previous_assembly.first_notify_sequence
                        or previous_assembly.first_received_at is None
                        or previous_assembly.first_received_monotonic is None
                    ):
                        safety_received_at = None
                        safety_received_monotonic = None
                    else:
                        safety_received_at = previous_assembly.first_received_at
                        safety_received_monotonic = (
                            previous_assembly.first_received_monotonic
                        )
                parsed = assembled

        observation = BleFrameObservation(
            received_at=observed_at,
            raw_bytes=raw,
            base64_encoded=b64,
            parsed=parsed,
            decode_error=decode_error,
            session_generation=session_generation,
            notify_sequence=notify_sequence,
            first_notify_sequence=ack_notify_sequence,
            delivery_id=delivery_id,
            rejected_frame=rejected_frame,
            protocol_status=protocol_status,
            device_identity=session.device_identity if session is not None else None,
            session_key_fingerprint=session.key_fingerprint
            if session is not None
            else None,
            received_at_monotonic=safety_received_monotonic,
            safety_received_at=safety_received_at,
            key_fingerprint=(
                hashlib.sha256(notification_key).digest()
                if notification_key is not None
                else None
            ),
        )
        if (
            session is not None
            and parsed is not None
            and not (session.ota_start_consumed and session.ota_events is None)
            and parsed.cmd == MQTT_CMD_DEVICE_GET_OTA_PAGE_DATA
        ):
            session.ota_page_request = observation
            session.ota_page_key = notification_key
        if (
            session is not None
            and parsed is not None
            and not (session.ota_start_consumed and session.ota_events is None)
            and parsed.cmd == MQTT_CMD_NOTIFY_DEVICE_OTA_TOTAL_PAGE
        ):
            session.ota_count_request = observation
            session.ota_count_key = notification_key
        if (
            session is not None
            and session.ota_events is not None
            and (
                decode_error is not None
                or (parsed is not None and parsed.cmd in {101, 102, 103, 104})
            )
        ):
            session.ota_events.put_nowait(observation)
        stats.last_frame = observation
        if parsed is not None:
            if _should_log_ble_notification(stats.frames_decoded):
                _LOGGER.debug(
                    "Jackery BLE %s decoded: frames=%d cmd=%d chunks=%d body_bytes=%d",
                    device_id,
                    stats.frames_decoded,
                    parsed.cmd,
                    parsed.chunk_count,
                    len(parsed.body),
                )
            # Wake any in-flight writer waiting on an ACK echo for this
            # device. Done before the sink fires so callers observing the
            # ACK never race against the merge-into-coordinator step.
            self._resolve_pending_acks(
                device_id,
                session,
                parsed,
                notify_sequence=ack_notify_sequence,
            )
        else:
            self._record_decode_failure(device_id, stats, decode_error)
        if not self._notification_session_is_valid(
            device_id,
            session,
            accepted=accepted,
        ):
            return
        await self._async_deliver_observation(device_id, observation, stats)


__all__ = [
    "DEFAULT_BLE_CONNECT_TIMEOUT_SEC",
    "BleFrameObservation",
    "BleListenerStats",
    "FrameSink",
    "JackeryBleListener",
]
