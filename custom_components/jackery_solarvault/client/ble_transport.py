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

Crypto assumptions follow PROTOCOL.md §14 and the reverse-engineered
``bb/a`` smali. Without a Frida-captured frame the layout is best-effort
— that is why diagnostics retain the last raw frame behind redaction.
"""

import asyncio
import base64
import binascii
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import starmap
import json
import logging
import random
import sys
import time
from typing import TYPE_CHECKING, Any, cast

from bleak import BleakClient
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS, establish_connection

from ..const import DEFAULT_BLE_ACK_TIMEOUT_SEC
from . import ble

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import (
        BluetoothCallbackMatcher,
        BluetoothChange,
        BluetoothServiceInfoBleak,
    )
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_BLE_NOTIFICATION_LOG_SAMPLE_EVERY = 256


def _should_log_ble_notification(frame_count: int) -> bool:
    """Return whether one notification should emit sparse DEBUG progress."""
    return frame_count == 1 or frame_count % _BLE_NOTIFICATION_LOG_SAMPLE_EVERY == 0


def _body_is_complete_json_object(body: bytes) -> bool:
    """Return whether one BLE page already carries a complete JSON object.

    App 2.4.0 distinguishes its pagination format from byte-fragment
    transport framing. Live SolarVault notifications confirm that each
    numbered page can contain an independently decodable command body, while
    later page numbers are not guaranteed to arrive. Only bodies that are not
    complete JSON objects need byte reassembly.
    """
    try:
        return isinstance(json.loads(body.decode("utf-8")), dict)
    except UnicodeDecodeError, json.JSONDecodeError:
        return False


# bleak / HA-bluetooth imports are deferred to the methods that need them so
# the module can be imported on systems without BlueZ during tests.

#: Default timeout for the GATT connect + notify-subscribe handshake.
DEFAULT_BLE_CONNECT_TIMEOUT_SEC: float = 20.0

#: Minimum time between (re)connect attempts when the device drops the link.
_RECONNECT_BACKOFF_SEC: float = 30.0

#: A GATT link must keep notify ownership this long before it resets the
#: escalating ESPHome-proxy protection backoff.
_STABLE_SESSION_SEC: float = 60.0

#: Hard timeout for ``async_stop()`` to wait for in-flight connection
#: runners to honour cancellation. HA's shutdown sequence reports tasks
#: that exceed its own per-integration timeout (typically 30 s for
#: ``async_unload_entry``) — keep this well below that so the listener
#: never becomes the reason a shutdown logs "tasks still pending".
_STOP_TIMEOUT_SEC: float = 5.0
_COOPERATIVE_STOP_GRACE_SEC: float = 0.25
_SINK_RETRY_INITIAL_SEC: float = 0.01
_SINK_RETRY_MAX_SEC: float = 1.0

#: Legacy keep-alive interval retained for diagnostics compatibility. The
#: connection runner does not schedule this writer; live notifications remain
#: read-only and writes occur only for explicit coordinator commands.
#: How often the legacy helper would write a no-op query frame to keep the GATT
#: session warm.
#: warm. The SolarVault peripheral closes idle GATT sessions after
#: roughly 20 s (observed 2026-05-17 production log: BLE disconnects
#: every 6-20 s without traffic). 15 s sits comfortably below that and
#: doubles as a property-refresh — the device answers each ``cmd=106``
#: with a ``DevicePropertyChange`` notify that the sink merges into
#: ``coordinator.data`` via the existing cmd=107 path.
_KEEPALIVE_INTERVAL_SEC: float = 15.0

# Notify chunks are ordered on a GATT connection, but an interrupted link may
# leave an incomplete logical message behind. Bound both lifetime and memory so
# supplemental BLE traffic can never grow coordinator-owned state indefinitely.
_REASSEMBLY_TIMEOUT_SEC: float = 10.0
_REASSEMBLY_MAX_CHUNKS: int = 128
_REASSEMBLY_MAX_BODY_BYTES: int = 256 * 1024
_REASSEMBLY_MAX_MESSAGES_PER_DEVICE: int = 8
_NOTIFY_QUEUE_WARN_FRAMES: int = _REASSEMBLY_MAX_CHUNKS
_UINT16_MAX: int = 0xFFFF


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


@dataclass(slots=True)
class BleListenerStats:
    """Counters surfaced via diagnostics for transport health."""

    advertisements_seen: int = 0
    connect_attempts: int = 0
    connect_failures: int = 0
    frames_received: int = 0
    frames_decoded: int = 0
    frames_decode_failed: int = 0
    multi_chunk_frames_buffered: int = 0
    multi_chunk_messages_assembled: int = 0
    multi_chunk_assemblies_dropped: int = 0
    notify_frames_dropped: int = 0
    notify_queue_depth: int = 0
    notify_queue_high_watermark: int = 0
    notify_queue_bytes: int = 0
    notify_queue_high_watermark_bytes: int = 0
    notify_queue_oldest_age_sec: float = 0.0
    acks_received: int = 0
    acks_timed_out: int = 0
    last_error: str | None = None
    last_keep_alive_error: str | None = None
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
    # Keep-alive health counters (P3-3).
    keep_alive_writes_attempted: int = 0
    keep_alive_writes_succeeded: int = 0
    keep_alive_writes_failed: int = 0
    consecutive_keep_alive_failures: int = 0


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
    failure_reason: str | None = None


@dataclass(slots=True)
class _PendingFrameAssembly:
    """Bounded set of decoded chunks for one logical BLE message."""

    chunk_count: int
    frames: dict[int, ble.BleBinaryFrame]
    updated_at: float
    first_notify_sequence: int | None


@dataclass(slots=True)
class _GattSession:
    """Ownership token for one per-device GATT connection."""

    generation: int
    client: Any
    active: bool = True
    accepting_notifications: bool = True
    notify_started: bool = False
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
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


FrameSink = Callable[[str, BleFrameObservation], Awaitable[bool]]
# Async sink called for every observed frame.

# ``device_id`` is the Jackery numeric device id (matches coordinator state).
# ``observation`` carries the decoded or raw frame.


class JackeryBleListener:
    """Connects to one or more SolarVault devices via BLE and forwards frames.

    The listener owns one ``BleakClient`` per device. It re-attempts the
    GATT connect with capped exponential backoff after a drop, and stops
    gracefully on integration unload.

    Instantiate one listener per coordinator. Call :meth:`async_start`
    after the integration knows the per-device ``bluetoothKey`` (i.e.
    after the first successful HTTP discovery).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        sink: FrameSink,
        *,
        key_resolver: Callable[[str], bytes | None],
        ble_address_resolver: Callable[[str], str | None],
        connect_backoff_remaining: Callable[[str, float], float],
        connect_backoff_note_failure: Callable[[str, float], float],
        connect_backoff_note_success: Callable[[str], None],
        keep_alive_msg_id: int | None,
        keep_alive_ble_msg_type: int | None,
        serial_resolver: Callable[[str], str | None] | None = None,
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
        self._sink = sink
        self._key_resolver = key_resolver
        self._ble_address_resolver = ble_address_resolver
        self._connect_backoff_remaining = connect_backoff_remaining
        self._connect_backoff_note_failure = connect_backoff_note_failure
        self._connect_backoff_note_success = connect_backoff_note_success
        self._keep_alive_msg_id = keep_alive_msg_id
        self._keep_alive_ble_msg_type = keep_alive_ble_msg_type
        self._serial_resolver = serial_resolver
        self._ha_bluetooth: Any | None = None
        self._stats: dict[str, BleListenerStats] = {}
        self._unregister_callbacks: list[Callable[[], None]] = []
        self._connections: dict[str, asyncio.Task[None]] = {}
        self._session_generations: dict[str, int] = {}
        self._sessions: dict[str, _GattSession] = {}
        self._notify_tasks: set[asyncio.Task[None]] = set()
        self._stop_event = asyncio.Event()
        self._stop_task: asyncio.Task[None] | None = None
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
        self._clients: dict[str, Any] = {}
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
        create_task = getattr(self._hass, "async_create_background_task", None)
        try:
            if callable(create_task):
                return cast("asyncio.Task[None]", create_task(target, name=name))
            return asyncio.create_task(target, name=name)
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
        return True

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
        client: Any,
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
        session = _GattSession(generation=generation, client=client)
        self._sessions[device_id] = session
        self._clients[device_id] = client
        return session

    def _clear_frame_assemblies(
        self,
        device_id: str,
        session: _GattSession | None = None,
    ) -> int:
        """Clear owned frame assemblies and return the number discarded."""
        owner = self._frame_assembly_owners.get(device_id)
        if session is not None and owner is not session:
            return 0
        assemblies = self._frame_assemblies.pop(device_id, None)
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

    async def _async_wait_for_all_notification_drains(
        self,
        session_items: tuple[tuple[str, _GattSession], ...],
    ) -> None:
        """Drain every fenced session concurrently within its shutdown budget."""
        if session_items:
            await asyncio.gather(
                *starmap(self._async_wait_for_notification_drain, session_items)
            )

    @staticmethod
    async def _async_cancel_keep_alive(
        device_id: str,
        keep_alive_task: asyncio.Task[None] | None,
    ) -> None:
        """Cancel the keep-alive task and surface any failure it carried."""
        if keep_alive_task is None or keep_alive_task.done():
            return
        keep_alive_task.cancel()
        try:
            await keep_alive_task
        except asyncio.CancelledError:
            return
        except Exception as err:  # ruff: ignore[blind-except]
            _LOGGER.warning(
                "Jackery BLE keep-alive task for %s failed: %s",
                device_id,
                err,
            )

    def _invalidate_session(self, device_id: str) -> None:
        """Invalidate the current session for a device to force reconnection.

        Called by the keep-alive loop when consecutive failures exceed the
        threshold. This wakes the connection runner which will back off and
        re-establish a fresh GATT session.
        """
        session = self._sessions.get(device_id)
        if session is not None:
            session.active = False
        # Note: we do NOT cancel notify tasks here - the connection runner
        # owns the session lifecycle. Notifications remain acceptable until
        # stop_notify/disconnect establishes the physical cutoff; teardown then
        # drains accepted callbacks before releasing map/assembly ownership.

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
        if self._sessions.get(device_id) is session:
            self._sessions.pop(device_id, None)
            if self._clients.get(device_id) is session.client:
                self._clients.pop(device_id, None)
            if self._mtu_owners.get(device_id) is session:
                self._mtu_owners.pop(device_id, None)
                self._mtu.pop(device_id, None)
        dropped = self._clear_frame_assemblies(device_id, session)
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
        if cleanup is not None and cleanup.done():
            if cleanup.cancelled() or cleanup.exception() is not None:
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
        produce a usable integer — the writer then falls back to
        :data:`ble.DEFAULT_BLE_MTU`.
        """
        if session is not None and not self._session_is_current(device_id, session):
            return
        for attr in ("mtu_size", "mtu"):
            value = getattr(client, attr, None)
            if value is None:
                continue
            if not isinstance(value, int):
                _LOGGER.debug(
                    "Jackery BLE %s: backend reported %s=%r (%s), not an int",
                    device_id,
                    attr,
                    value,
                    type(value).__name__,
                )
                continue
            try:
                payload_size = ble.chunk_size_for_mtu(value)
            except ValueError as err:
                _LOGGER.warning(
                    "Jackery BLE %s: unusable negotiated %s=%d: %s",
                    device_id,
                    attr,
                    value,
                    err,
                )
                continue
            else:
                self._mtu[device_id] = value
                if session is not None:
                    self._mtu_owners[device_id] = session
                _LOGGER.debug(
                    "Jackery BLE %s: negotiated MTU=%d (%d body bytes/frame)",
                    device_id,
                    value,
                    payload_size,
                )
                return
        _LOGGER.debug(
            "Jackery BLE %s: bleak did not expose mtu_size yet, will assume "
            "%d on the next write",
            device_id,
            ble.DEFAULT_BLE_MTU,
        )

    def mtu_for_device(self, device_id: str) -> int:
        """Return the cached negotiated MTU for ``device_id`` or the default."""
        return self._mtu.get(device_id, ble.DEFAULT_BLE_MTU)

    async def async_ensure_connected(  # flat transport guard chain; the connect-backoff gate is the 7th early exit
        self,
        device_id: str,
        *,
        timeout_sec: float = DEFAULT_BLE_CONNECT_TIMEOUT_SEC,
    ) -> bool:
        """Wait for an active BLE client, starting a reconnect when possible."""
        if self._stop_event.is_set():
            return False
        client = self._clients.get(device_id)
        session = self._sessions.get(device_id)
        if (
            client is not None
            and getattr(client, "is_connected", False)
            and (session is None or self._session_is_current(device_id, session))
        ):
            return True
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
            if device_id not in self._connections:
                return False
        if timeout_sec <= 0:
            return False
        deadline = asyncio.get_running_loop().time() + timeout_sec
        while not self._stop_event.is_set():
            client = self._clients.get(device_id)
            if client is not None and getattr(client, "is_connected", False):
                return True
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.25, remaining))
        return False

    async def _async_keep_alive_loop(
        self,
        device_id: str,
        session: _GattSession | None = None,
    ) -> None:
        """Periodically write a no-op query frame to keep the GATT session warm.

        The SolarVault peripheral closes idle GATT sessions after
        roughly 20 s (observed 2026-05-17 production log: BLE
        disconnects every 6-20 s without traffic). Sending a ``cmd=106``
        :data:`.const.MQTT_CMD_QUERY_DEVICE_PROPERTY` query at
        :data:`_KEEPALIVE_INTERVAL_SEC` keeps the session warm and
        yields a fresh ``DevicePropertyChange`` notify response, which
        the sink merges into ``coordinator.data`` via the normal
        ``cmd=107`` path.

        Robustness: consecutive keep-alive failures trigger a session
        invalidation so the connection runner reconnects. This prevents
        silent session death when the peripheral stops acknowledging writes.

        Cancellation contract: the parent connection runner cancels
        this task in its ``finally`` block on disconnect / shutdown.
        ``CancelledError`` propagates so the cancel sees a clean exit;
        write errors are caught and DEBUG-logged so a single missed
        keep-alive does not abort the loop.
        """
        if self._keep_alive_msg_id is None or self._keep_alive_ble_msg_type is None:
            return

        # Track consecutive keep-alive failures for this device.
        # 3 consecutive failures -> invalidate session to force reconnect.
        max_keepalive_failures = 3
        consecutive_failures = 0

        while not self._stop_event.is_set():
            # Add ±2s jitter to desynchronize keep-alives across devices.
            jittered_interval = _KEEPALIVE_INTERVAL_SEC + random.uniform(-2.0, 2.0)
            if await self._async_stop_requested_within(jittered_interval):
                return
            if session is not None:
                if not self._session_is_current(device_id, session):
                    return
            elif device_id not in self._clients:
                return
            stats = self.stats_for(device_id)
            try:
                # HomeControlFormat injects bleMsgType as "cmd" in the JSON body.
                stats.keep_alive_writes_attempted += 1
                sent = await self.async_send_command(
                    device_id,
                    msg_id=self._keep_alive_msg_id,
                    ble_msg_type=self._keep_alive_ble_msg_type,
                    body=(f'{{"cmd":{self._keep_alive_ble_msg_type}}}'.encode()),
                    wait_for_ack=False,
                )
            except (RuntimeError, ValueError) as err:
                self._record_keep_alive_error(stats, device_id, str(err))
                stats.keep_alive_writes_failed += 1
                stats.consecutive_keep_alive_failures += 1
                consecutive_failures += 1
                if consecutive_failures >= max_keepalive_failures:
                    _LOGGER.warning(
                        "Jackery BLE %s: %d consecutive keep-alive failures; "
                        "invalidating session to force reconnect",
                        device_id,
                        consecutive_failures,
                    )
                    self._invalidate_session(device_id)
                    return
                continue
            if not sent:
                self._record_keep_alive_error(
                    stats, device_id, "no current connected GATT session"
                )
                stats.keep_alive_writes_failed += 1
                stats.consecutive_keep_alive_failures += 1
                consecutive_failures += 1
                if consecutive_failures >= max_keepalive_failures:
                    _LOGGER.warning(
                        "Jackery BLE %s: %d consecutive keep-alive failures; "
                        "invalidating session to force reconnect",
                        device_id,
                        consecutive_failures,
                    )
                    self._invalidate_session(device_id)
                    return
                continue
            # Success: reset consecutive failure counter and clear error.
            stats.keep_alive_writes_succeeded += 1
            stats.consecutive_keep_alive_failures = 0
            consecutive_failures = 0
            previous_error = stats.last_keep_alive_error
            if previous_error is not None:
                stats.last_keep_alive_error = None
                if stats.last_error == previous_error:
                    stats.last_error = stats.last_sink_error or stats.last_decode_error
                _LOGGER.info("Jackery BLE %s: keep-alive writes recovered", device_id)

    async def _async_stop_requested_within(self, delay: float) -> bool:
        """Return whether listener shutdown is requested before ``delay`` expires."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except TimeoutError:
            return False
        return True

    @staticmethod
    def _record_keep_alive_error(
        stats: BleListenerStats, device_id: str, detail: str
    ) -> None:
        """Record one keep-alive failure without aborting the connection runner."""
        error = f"keep-alive write failed: {detail}"
        if stats.last_keep_alive_error is None:
            _LOGGER.warning("Jackery BLE %s: %s", device_id, error)
        stats.last_keep_alive_error = error
        stats.last_error = error

    async def _async_write_command_chunks(
        self,
        device_id: str,
        session: _GattSession,
        client: BleakClient,
        chunks: list[bytes],
        *,
        key: bytes,
        msg_id: int,
        ble_msg_type: int,
        timeout_sec: float,
    ) -> None:
        """Encrypt and write every chunk for one current GATT session."""
        chunk_count = len(chunks)
        for idx, chunk in enumerate(chunks, start=1):
            if not self._session_is_current(device_id, session):
                msg = f"BLE session for {device_id} changed during write"
                raise RuntimeError(msg)
            plain = ble.build_binary_frame(
                cmd=ble_msg_type,
                body=chunk,
                flags=msg_id,
                frame_index=idx,
                chunk_count=chunk_count,
            )
            blob = ble.encrypt_binary_notify(plain, key)
            await asyncio.wait_for(
                client.write_gatt_char(ble.BLE_WRITE_CHAR_UUID, blob, response=False),
                timeout=timeout_sec,
            )

    async def async_send_command(
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
    ) -> bool:
        """Send one logical command frame over BLE.

        The call can optionally wait for a matching acknowledgement.

        Parameters:
            device_id (str): Target device identifier.
            cmd (int): Logical command identifier to send.
            body (bytes): Command payload bytes.
            flags (int): Frame flags included in the sent binary frame.
            timeout_sec (float): Per-GATT-write timeout in seconds.
            wait_for_ack (bool): If True, wait for a matching decoded notify frame
            before returning.
            ack_timeout_sec (float): Timeout in seconds to wait for the ACK when
            `wait_for_ack` is True.
            ack_cmds (tuple[int, ...] | None): Optional set of `cmd` values that qualify
            as the ACK; when omitted, any decoded frame from the same device within the
            window qualifies.
            mtu_override (int | None): Optional MTU to use instead of the negotiated or
            default MTU (used for tests/diagnostics).

        Returns:
            bool: `True` if the GATT write completed (and, when requested, a matching
            ACK was received); `False` if no active BLE client exists for the device.

        Raises:
            RuntimeError: When the payload cannot be chunked for the selected MTU, on
            GATT-layer failures (including write timeouts), or when an ACK wait times
            out.
        """
        if (
            isinstance(msg_id, bool)
            or not isinstance(msg_id, int)
            or not 1 <= msg_id <= _UINT16_MAX
        ):
            msg = "msg_id must be an integer in range 1..65535"
            raise ValueError(msg)
        if (
            isinstance(ble_msg_type, bool)
            or not isinstance(ble_msg_type, int)
            or not 0 <= ble_msg_type <= _UINT16_MAX
        ):
            msg = "ble_msg_type must be an integer in range 0..65535"
            raise ValueError(msg)
        session = self._sessions.get(device_id)
        if session is None or not self._session_is_current(device_id, session):
            return False
        client = session.client
        if not getattr(client, "is_connected", False):
            return False
        key = self._key_resolver(device_id)
        if key is None:
            msg = f"no bluetoothKey available for device {device_id}"
            raise RuntimeError(msg)
        # Resolve the effective MTU: explicit override wins (used by
        # tests and the service for diagnostics), then the per-device
        # cached negotiated value, then the Android-app default.
        if mtu_override is not None:
            if isinstance(mtu_override, bool) or not isinstance(mtu_override, int):
                msg = "mtu_override must be an integer"
                raise ValueError(msg)
            mtu = mtu_override
        else:
            mtu = self.mtu_for_device(device_id)
        try:
            chunks = ble.split_body_for_mtu(body, mtu)
        except ValueError as err:
            msg = f"BLE MTU {mtu} too small to fit any body for {device_id}: {err}"
            raise RuntimeError(msg) from err
        # The protocol has no transaction id. Keep one logical write (all chunks
        # plus its optional explicit ACK wait) under the session lock so concurrent
        # service calls and keep-alives cannot interleave.
        async with session.write_lock:
            if not self._session_is_current(device_id, session) or not getattr(
                client, "is_connected", False
            ):
                return False
            # Register the ACK *before* the write — otherwise a fast-echoing
            # peripheral could deliver the notify before the future exists.
            pending: _PendingAck | None = None
            if wait_for_ack:
                pending = self._register_pending_ack(
                    device_id, session, msg_id, ble_msg_type
                )
            try:
                await self._async_write_command_chunks(
                    device_id,
                    session,
                    client,
                    chunks,
                    key=key,
                    msg_id=msg_id,
                    ble_msg_type=ble_msg_type,
                    timeout_sec=timeout_sec,
                )
            except TimeoutError as err:
                if pending is not None:
                    self._discard_pending_ack(device_id, pending)
                msg = f"BLE write to {device_id} timed out after {timeout_sec}s"
                raise RuntimeError(msg) from err
            except asyncio.CancelledError:
                if pending is not None:
                    self._discard_pending_ack(device_id, pending)
                raise
            except Exception as err:  # bleak surfaces BleakError + variants
                if pending is not None:
                    self._discard_pending_ack(device_id, pending)
                msg = f"BLE write to {device_id} failed: {err}"
                raise RuntimeError(msg) from err
            if pending is not None:
                await self._await_pending_ack(
                    device_id,
                    pending,
                    msg_id=msg_id,
                    ble_msg_type=ble_msg_type,
                    ack_timeout_sec=ack_timeout_sec,
                )
        return True

    async def _await_pending_ack(
        self,
        device_id: str,
        pending: _PendingAck,
        *,
        msg_id: int,
        ble_msg_type: int,
        ack_timeout_sec: float,
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
            self._discard_pending_ack(device_id, pending)
            stats.acks_timed_out += 1
            stats.last_error = f"ack timeout msgId={msg_id} bleMsgType={ble_msg_type}"
            msg = (
                f"BLE ack timeout for msgId={msg_id} bleMsgType={ble_msg_type} "
                f"on {device_id} after {ack_timeout_sec}s"
            )
            raise RuntimeError(msg) from err
        except asyncio.CancelledError as err:
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
            if frame.cmd == pending.expected_ble_msg_type:
                pending.future.set_result(frame)
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

        bluetooth_module: Any = sys.modules.get(
            "homeassistant.components.bluetooth",
        )
        if bluetooth_module is None:
            msg = "Home Assistant Bluetooth is not loaded"
            raise RuntimeError(msg)
        self._ha_bluetooth = bluetooth_module

        matcher: BluetoothCallbackMatcher = {
            "service_uuid": ble.BLE_SERVICE_UUID,
            "manufacturer_id": ble.BLE_MANUFACTURER_ID,
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
            "Jackery BLE: matcher registered (service_uuid=%s, "
            "manufacturer_id=%#x) for %d device(s); awaiting advertisements",
            ble.BLE_SERVICE_UUID,
            ble.BLE_MANUFACTURER_ID,
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
        if address is None or self._stop_event.is_set():
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
        if stop_task is not None and stop_task.done():
            if stop_task.cancelled() or stop_task.exception() is not None:
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
        for unregister in self._unregister_callbacks:
            try:
                unregister()
            except Exception as err:  # pragma: no cover — HA callback contract is sync
                _LOGGER.debug(
                    "Jackery BLE: callback unregister failed: %s", err, exc_info=True
                )
        self._unregister_callbacks.clear()
        # Inactive sessions cannot resolve writes. Cancel ACK waiters before the
        # bounded task drain so outer cancellation cannot strand callers.
        pending_acks: dict[str, list[_PendingAck]] = getattr(self, "_pending_acks", {})
        for bucket in pending_acks.values():
            for pending in bucket:
                if not pending.future.done():
                    pending.future.cancel()
        pending_acks.clear()
        if session_items:
            teardown_results = await asyncio.gather(
                *starmap(self._teardown_session, session_items),
                return_exceptions=True,
            )
            teardown_errors = [
                result
                for result in teardown_results
                if isinstance(result, BaseException)
            ]
            if teardown_errors:
                cancelled = next(
                    (
                        error
                        for error in teardown_errors
                        if isinstance(error, asyncio.CancelledError)
                    ),
                    None,
                )
                if cancelled is not None:
                    raise cancelled
                detail = "; ".join(str(error) for error in teardown_errors)
                raise RuntimeError(f"Jackery BLE session teardown failed: {detail}")
        # Notification consumers have completed. Give connection runners a short
        # cooperative window to finish an already-started GATT disconnect before
        # cancellation is used to break genuinely stuck reconnect/backoff waits.
        current_task = asyncio.current_task()
        connection_items = list(self._connections.items())
        connection_tasks = [
            task
            for _, task in connection_items
            if not task.done() and task is not current_task
        ]
        current_task_owned = current_task is not None and any(
            task is current_task for _, task in connection_items
        )
        tasks = list(dict.fromkeys(connection_tasks))
        still_pending = await self._async_stop_connection_tasks(tasks)
        if still_pending or current_task_owned:
            remaining_count = len(still_pending) + int(current_task_owned)
            _LOGGER.warning(
                "Jackery BLE: %d transport task(s) remain after %ss; retaining "
                "listener ownership for a later teardown retry",
                remaining_count,
                _STOP_TIMEOUT_SEC,
            )
            msg = (
                f"{remaining_count} Jackery BLE transport task(s) did not complete "
                "teardown"
            )
            raise RuntimeError(msg)
        for device_id, task in connection_items:
            if self._connections.get(device_id) is task:
                self._connections.pop(device_id, None)
        for device_id, session in session_items:
            session.active = False
            dropped = self._clear_frame_assemblies(device_id, session)
            if dropped:
                self.stats_for(device_id).multi_chunk_assemblies_dropped += dropped
            if self._sessions.get(device_id) is session:
                self._sessions.pop(device_id, None)
                if self._clients.get(device_id) is session.client:
                    self._clients.pop(device_id, None)
                if self._mtu_owners.get(device_id) is session:
                    self._mtu_owners.pop(device_id, None)
                    self._mtu.pop(device_id, None)
        for device_id, owner in list(self._frame_assembly_owners.items()):
            dropped = self._clear_frame_assemblies(device_id, owner)
            if dropped:
                self.stats_for(device_id).multi_chunk_assemblies_dropped += dropped
        # Assemblies created by direct diagnostic calls have no session owner;
        # they are safe to discard only after the listener-wide stop guard is set.
        for device_id in list(self._frame_assemblies):
            if device_id not in self._frame_assembly_owners:
                dropped = self._clear_frame_assemblies(device_id)
                if dropped:
                    self.stats_for(device_id).multi_chunk_assemblies_dropped += dropped
        _LOGGER.info("Jackery BLE listener stopped")

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
        # Step 2: decode the serial from manufacturer data.
        mfr_data = (service_info.manufacturer_data or {}).get(ble.BLE_MANUFACTURER_ID)
        serial: str | None = None
        if isinstance(mfr_data, bytes):
            try:
                serial = mfr_data.decode("ascii").strip()
            except UnicodeDecodeError:
                serial = None
        if serial is None:
            return None
        # Step 3: ask the coordinator for the matching device id.
        device_id: str | None = None
        if self._serial_resolver is not None:
            device_id = self._serial_resolver(serial)
        if device_id is None:
            return None
        # Step 4: bind only an unambiguous configured identity. Raw serials and
        # adapter addresses are intentionally absent from support logs.
        if not self._bind_device_address(device_id, address):
            return None
        if self.stats_for(device_id).advertisements_seen == 0:
            _LOGGER.info(
                "Jackery BLE: matched an advertisement to a cached device identity",
            )
        return device_id

    async def _async_run_connection(  # ruff: ignore[complex-structure] - one loop owns the full GATT session lifecycle
        self,
        device_id: str,
        address: str,
    ) -> None:
        """Maintain a persistent BLE GATT session for one device.

        The session subscribes to notifications and reconnects on link loss.

        This coroutine opens and publishes a Bleak client for the given address,
        subscribes to the notify characteristic, runs a keep-alive while connected, and
        tears down and retries the session on disconnect until the listener is stopped
        or the task is cancelled.

        Raises:
            asyncio.CancelledError: if the task is cancelled during shutdown.
        """
        stats = self.stats_for(device_id)
        runner_task = asyncio.current_task()
        try:  # ruff: ignore[too-many-statements-in-try-clause] - the try deliberately owns the runner lifecycle
            while not self._stop_event.is_set():
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
                        _LOGGER.warning(
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
                    _LOGGER.info(
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
                    disconnected_client: Any,
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
                    _LOGGER.info(
                        "Jackery BLE %s connect failed: %s; retrying in %ss",
                        device_id,
                        err,
                        delay,
                    )
                    if await self._async_stop_requested_within(delay):
                        return
                    continue

                if self._stop_event.is_set() or not self._connection_is_current(
                    device_id, runner_task
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
                _LOGGER.info(
                    "Jackery BLE %s: connected; subscribing to notify %s",
                    device_id,
                    ble.BLE_NOTIFY_CHAR_UUID,
                )

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

                if self._stop_event.is_set():
                    return
                # PROTOCOL.md §4: BLE peripherals routinely drop idle
                # sessions; the surrounding ``while not self._stop_event``
                # loop reconnects after this backoff. Logged at INFO so
                # the user sees the reconnect cadence in default logs
                # (previously DEBUG, which made the silence invisible).
                delay = self._connect_backoff_note_failure(
                    device_id,
                    asyncio.get_running_loop().time(),
                )
                _LOGGER.info(
                    "Jackery BLE %s: lost link, backoff %ss before retry",
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
        client: Any | None = None,
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
        stats = self.stats_for(device_id)
        stats.last_disconnect_at = datetime.now(UTC)
        # Promoted from DEBUG to INFO: peripheral disconnects are the
        # primary symptom of BLE silence and must be visible in default
        # HA logs so the user can correlate them with the keep-alive /
        # reconnect-backoff timing in PROTOCOL.md §4.
        _LOGGER.info("Jackery BLE %s: peripheral disconnected", device_id)

    # ------------------------------------------------------------------
    # Notification handler
    # ------------------------------------------------------------------

    def _reassemble_frame(  # ruff: ignore[complex-structure, too-many-locals] - bounded protocol state machine
        self,
        device_id: str,
        frame: ble.BleBinaryFrame,
        *,
        session: _GattSession | None = None,
        notify_sequence: int | None = None,
        accepted: bool = False,
    ) -> tuple[ble.BleBinaryFrame | None, int | None]:
        """Return a complete frame and its earliest queued notification sequence."""
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
                return None, None
        stats = self.stats_for(device_id)
        now = self._hass.loop.time()
        if session is not None:
            owner = self._frame_assembly_owners.get(device_id)
            if owner is not session:
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
        if not 2 <= frame.chunk_count <= _REASSEMBLY_MAX_CHUNKS:
            msg = f"BLE chunk_count {frame.chunk_count} is outside the supported range"
            raise ValueError(msg)
        if not 1 <= frame.frame_index <= frame.chunk_count:
            msg = (
                f"BLE frame_index {frame.frame_index} is outside 1..{frame.chunk_count}"
            )
            raise ValueError(msg)

        assemblies = self._frame_assemblies.setdefault(device_id, {})

        key = (frame.cmd, frame.flags)
        assembly = assemblies.get(key)
        if assembly is not None and assembly.chunk_count != frame.chunk_count:
            assemblies.pop(key, None)
            stats.multi_chunk_assemblies_dropped += 1
            assembly = None
        if assembly is not None and frame.frame_index == 1:
            first = assembly.frames.get(1)
            if first is not None and (
                first.body != frame.body or first.trailer != frame.trailer
            ):
                assemblies.pop(key, None)
                stats.multi_chunk_assemblies_dropped += 1
                assembly = None
        if assembly is None:
            if len(assemblies) >= _REASSEMBLY_MAX_MESSAGES_PER_DEVICE:
                oldest_key = min(
                    assemblies,
                    key=lambda candidate: assemblies[candidate].updated_at,
                )
                assemblies.pop(oldest_key, None)
                stats.multi_chunk_assemblies_dropped += 1
            assembly = _PendingFrameAssembly(
                chunk_count=frame.chunk_count,
                frames={},
                updated_at=now,
                first_notify_sequence=notify_sequence,
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
                stats.multi_chunk_assemblies_dropped += 1
                assembly = _PendingFrameAssembly(
                    chunk_count=frame.chunk_count,
                    frames={frame.frame_index: frame},
                    updated_at=now,
                    first_notify_sequence=notify_sequence,
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
            stats.multi_chunk_assemblies_dropped += 1
            msg = f"assembled BLE body exceeds {_REASSEMBLY_MAX_BODY_BYTES} bytes"
            raise ValueError(msg)
        if len(assembly.frames) < assembly.chunk_count:
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
                previous_sink_error = stats.last_sink_error
                if sink_processed and previous_sink_error is not None:
                    stats.last_sink_error = None
                    if stats.last_error == previous_sink_error:
                        stats.last_error = (
                            stats.last_decode_error or stats.last_keep_alive_error
                        )
                    _LOGGER.info(
                        "Jackery BLE %s: coordinator sink recovered",
                        device_id,
                    )
                return sink_processed
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, _SINK_RETRY_MAX_SEC)

    async def _handle_notification(
        self,
        device_id: str,
        raw: bytes,
        *,
        session: _GattSession | None = None,
        notify_sequence: int | None = None,
        received_at: datetime | None = None,
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
        if (
            session is not None
            and not accepted
            and not self._session_is_current(device_id, session)
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

        parsed: ble.BleBinaryFrame | None = None
        decode_error: str | None = None
        ack_notify_sequence = notify_sequence

        key = self._key_resolver(device_id)
        if key is None:
            decode_error = "no bluetoothKey for device"
        else:
            try:
                parsed = ble.decrypt_binary_notify(raw, key)
            except ValueError as err:
                decode_error = str(err)
                # Fallback: maybe the peripheral wrapped the wire payload
                # in base64 (some BLE proxies do). Try once more with the
                # base64-decoded blob before giving up.
                with contextlib.suppress(ValueError, binascii.Error):
                    decoded = base64.b64decode(raw, validate=False)
                    parsed = ble.decrypt_binary_notify(decoded, key)
                    decode_error = None

        if parsed is not None:
            assembled: ble.BleBinaryFrame | None
            if _body_is_complete_json_object(parsed.body):
                assembled = parsed
            else:
                try:
                    assembled, ack_notify_sequence = self._reassemble_frame(
                        device_id,
                        parsed,
                        session=session,
                        notify_sequence=notify_sequence,
                        accepted=accepted,
                    )
                except ValueError as err:
                    parsed = None
                    assembled = None
                    decode_error = f"reassembly: {err}"
            if parsed is not None:
                stats.frames_decoded += 1
                previous_decode_error = stats.last_decode_error
                if previous_decode_error is not None:
                    stats.last_decode_error = None
                    if stats.last_error == previous_decode_error:
                        stats.last_error = (
                            stats.last_sink_error or stats.last_keep_alive_error
                        )
                    _LOGGER.info(
                        "Jackery BLE %s: notification decoding recovered",
                        device_id,
                    )
                if assembled is None:
                    observation = BleFrameObservation(
                        received_at=observed_at,
                        raw_bytes=raw,
                        base64_encoded=b64,
                        parsed=parsed,
                        session_generation=session_generation,
                        notify_sequence=notify_sequence,
                        delivery_id=delivery_id,
                    )
                    stats.last_frame = observation
                    # A true byte fragment that never completes leaves no
                    # other trace: it resolves no ACK and reaches no sink.
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
                parsed = assembled

        observation = BleFrameObservation(
            received_at=observed_at,
            raw_bytes=raw,
            base64_encoded=b64,
            parsed=parsed,
            decode_error=decode_error,
            session_generation=session_generation,
            notify_sequence=notify_sequence,
            delivery_id=delivery_id,
        )
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
            stats.frames_decode_failed += 1
            if decode_error is not None:
                error = f"notify: {decode_error}"
                if stats.last_decode_error is None:
                    _LOGGER.warning(
                        "Jackery BLE %s notification decode failed: %s",
                        device_id,
                        decode_error,
                    )
                stats.last_decode_error = error
                stats.last_error = error
        if (
            session is not None
            and not accepted
            and not self._session_is_current(device_id, session)
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
