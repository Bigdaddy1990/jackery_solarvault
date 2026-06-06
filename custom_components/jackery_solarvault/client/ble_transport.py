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
from collections.abc import Awaitable, Callable
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

from jackery_solarvault.const import DEFAULT_BLE_ACK_TIMEOUT_SEC

from . import ble

if TYPE_CHECKING:
    from bleak import BleakClient

    from homeassistant.components.bluetooth import (
        BluetoothCallbackMatcher,
        BluetoothChange,
        BluetoothServiceInfoBleak,
    )
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# bleak / HA-bluetooth imports are deferred to the methods that need them so
# the module can be imported on systems without BlueZ during tests.

#: Default timeout for the GATT connect + notify-subscribe handshake.
DEFAULT_BLE_CONNECT_TIMEOUT_SEC: float = 20.0

#: Backoff after a failed connect *attempt* (device unreachable / GATT
#: connect raised). Long, so we don't hammer a device that is off or out of
#: range.
_RECONNECT_BACKOFF_SEC: float = 30.0

#: Backoff after a *clean* lost link (we were connected and the peripheral
#: recycled its idle GATT session — normal for SolarVault every ~17 s). The
#: device is present and advertising, so reconnect quickly instead of leaving
#: BLE dark for 30 s. Using the long backoff here was capping BLE uptime at
#: ~36 % (≈17 s up per ≈47 s cycle); a short retry roughly doubles it.
_LOST_LINK_BACKOFF_SEC: float = 8.0

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

#: Maximum consecutive connection failures before the runner gives up and
#: exits rather than spinning forever against an out-of-range device.
_MAX_CONNECTION_RETRIES: int = 50

#: Exponential-backoff ceiling — retries never wait longer than this.
_MAX_BACKOFF_SEC: float = 300.0


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
    source_started_at: datetime | None = None


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
    writes_with_response_failed: int = 0
    last_error: str | None = None
    last_connect_at: datetime | None = None
    last_disconnect_at: datetime | None = None
    last_ack_at: datetime | None = None
    last_frame: BleFrameObservation | None = field(default=None, repr=False)
    # Per-cmd counters for frames the sink decoded but did not route.
    # Exposed via diagnostics so the maintainer can see at a glance
    # how much BLE telemetry is still unconsumed (cmd=120 system /
    # per-device / CT variants currently — see coordinator sink).
    unrouted_frames_by_cmd: dict[int, int] = field(default_factory=dict)


@dataclass(slots=True)
class _PendingAck:
    """Internal record tracking a write that is waiting for a notify echo.

    ``expected_cmds`` is ``None`` when *any* decoded notify frame on the
    same device counts as the ACK (defensive default: we know the device
    streams a property-change frame promptly after most setters but we
    cannot guarantee the exact echo cmd without firmware spec). When set,
    only frames whose ``cmd`` is in the set are accepted.
    """

    expected_cmds: frozenset[int] | None
    future: asyncio.Future[ble.BleBinaryFrame]


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------


FrameSink = Callable[[str, BleFrameObservation], Awaitable[None]]
"""Async sink called for every observed frame.

``device_id`` is the Jackery numeric device id (matches coordinator state).
``observation`` carries the decoded or raw frame.
"""


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
        serial_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        """Construct a Jackery BLE listener that observes notifications and forwards parsed frames to a sink.

        Parameters:
            hass (HomeAssistant): Home Assistant instance used for bluetooth callbacks and background tasks.
            sink (FrameSink): Async callable invoked with (device_id, BleFrameObservation) for each observed frame.
            key_resolver (Callable[[str], bytes | None]): Returns the 16- or 32-byte AES key for a given device_id, or None if unavailable.
            ble_address_resolver (Callable[[str], str | None]): Returns the BLE MAC for a given device_id, or None; the listener also caches discovered addresses and exposes them via address_for_device_id.
            serial_resolver (Callable[[str], str | None] | None): Maps a BLE-broadcast serial string to a Jackery device_id; when omitted incoming advertisements with unmapped serials are logged but not associated.
        """  # noqa: E501, RUF100
        self._hass = hass
        self._sink = sink
        self._key_resolver = key_resolver
        self._ble_address_resolver = ble_address_resolver
        self._serial_resolver = serial_resolver
        self._stats: dict[str, BleListenerStats] = {}
        self._unregister_callbacks: list[Callable[[], None]] = []
        self._connections: dict[str, asyncio.Task[None]] = {}
        self._stop_event = asyncio.Event()
        self._aborted = False
        # Cache of (device_id -> BLE MAC) populated on first matching
        # advertisement. The coordinator's ``_ble_address_for_device``
        # reads back through :meth:`address_for_device_id`.
        self._device_addresses: dict[str, str] = {}
        # Active GATT clients per device id, populated by the connection
        # runner. ``async_send_command`` reads from this dict to write to
        # the open session without re-establishing the connect.
        self._clients: dict[str, Any] = {}
        # Pending ACK registrations per device id. ``_handle_notification``
        # resolves the matching futures when a decoded frame arrives. Each
        # device can have multiple in-flight writes (rare in practice for
        # SolarVault, but the data structure makes the contract explicit).
        self._pending_acks: dict[str, list[_PendingAck]] = {}
        # Per-device negotiated MTU, populated after connect. Used by
        # ``async_send_command`` to size the per-frame body budget. Falls
        # back to :data:`ble.DEFAULT_BLE_MTU` (matches the Android app)
        # when bleak hasn't exposed a value yet.
        self._mtu: dict[str, int] = {}
        self._pending_property_query_starts: dict[str, deque[datetime]] = {}

    def address_for_device_id(self, device_id: str) -> str | None:
        """Get the cached BLE MAC address for the given device id.

        Returns:
            The MAC address string for the device, or `None` if no cached address exists.
        """  # noqa: E501, RUF100
        return self._device_addresses.get(device_id)

    # ------------------------------------------------------------------
    # Phase 3b: write path — send a command frame to the device
    # ------------------------------------------------------------------

    def _record_negotiated_mtu(self, device_id: str, client: BleakClient) -> None:
        """Cache the negotiated GATT MTU after ``start_notify`` returns.

        Different bleak backends expose the MTU under different attribute
        names, and at different points in the connect lifecycle. We try
        the well-known ones in order and keep the cache empty if none
        produce a usable integer — the writer then falls back to
        :data:`ble.DEFAULT_BLE_MTU`.
        """
        for attr in ("mtu_size", "mtu"):
            value = getattr(client, attr, None)
            if isinstance(value, int) and value > ble._BLE_FRAME_OVERHEAD:
                self._mtu[device_id] = value
                _LOGGER.debug(
                    "Jackery BLE %s: negotiated MTU=%d (%d body bytes/frame)",
                    device_id,
                    value,
                    ble.chunk_size_for_mtu(value),
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

    def _mark_property_query_started(
        self,
        device_id: str,
        cmd: int,
        current_started_at: datetime | None,
    ) -> datetime | None:
        """Record the request timestamp for BLE property-query replies."""
        if cmd != 106 or current_started_at is not None:
            return current_started_at
        started_at = datetime.now()
        self._pending_property_query_starts.setdefault(
            device_id, deque(maxlen=4)
        ).append(started_at)
        return started_at

    async def _async_keep_alive_loop(self, device_id: str) -> None:
        """Periodically write a no-op query frame to keep the GATT session warm.

        The SolarVault peripheral closes idle GATT sessions after
        roughly 20 s (observed 2026-05-17 production log: BLE
        disconnects every 6-20 s without traffic). Sending a ``cmd=106``
        :data:`.const.MQTT_CMD_QUERY_DEVICE_PROPERTY` query at
        :data:`_KEEPALIVE_INTERVAL_SEC` keeps the session warm and
        yields a fresh ``DevicePropertyChange`` notify response, which
        the sink merges into ``coordinator.data`` via the normal
        ``cmd=107`` path.

        Cancellation contract: the parent connection runner cancels
        this task in its ``finally`` block on disconnect / shutdown.
        ``CancelledError`` propagates so the cancel sees a clean exit;
        write errors are caught and DEBUG-logged so a single missed
        keep-alive does not abort the loop.
        """
        # Avoid an import cycle by deferring the ``MQTT_CMD_QUERY_DEVICE_PROPERTY``
        # lookup — ``ble_transport`` is imported during
        # ``async_start_ble_transport`` from the coordinator, but the
        # const module is already loaded at that point.
        from jackery_solarvault.const import MQTT_CMD_QUERY_DEVICE_PROPERTY

        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=_KEEPALIVE_INTERVAL_SEC,
                    )
                    return  # stop_event fired  # noqa: TRY300
                except TimeoutError:
                    pass
                if device_id not in self._clients:
                    return  # connection went away while we slept
                try:
                    # Empty JSON body keeps the wire payload minimal
                    # (2 bytes plaintext). ``wait_for_ack=False`` so this
                    # never times out on the notify echo — the response
                    # arrives through the normal notify path and is
                    # routed by the sink at cmd=107.
                    await self.async_send_command(
                        device_id,
                        cmd=MQTT_CMD_QUERY_DEVICE_PROPERTY,
                        body=b"{}",
                        wait_for_ack=False,
                    )
                except (RuntimeError, ValueError) as err:
                    _LOGGER.debug(
                        "Jackery BLE %s keep-alive write failed: %s",
                        device_id,
                        err,
                    )
        except asyncio.CancelledError:  # noqa: TRY203
            raise

    async def async_send_command(
        self,
        device_id: str,
        *,
        cmd: int,
        body: bytes,
        flags: int = 0,
        timeout_sec: float = 10.0,
        wait_for_ack: bool = False,
        ack_timeout_sec: float = DEFAULT_BLE_ACK_TIMEOUT_SEC,
        ack_cmds: tuple[int, ...] | None = None,
        mtu_override: int | None = None,
    ) -> bool:
        """Send a logical command frame to the device over BLE and optionally wait for a matching acknowledgement.

        Parameters:
            device_id (str): Target device identifier.
            cmd (int): Logical command identifier to send.
            body (bytes): Command payload bytes.
            flags (int): Frame flags included in the sent binary frame.
            timeout_sec (float): Per-GATT-write timeout in seconds.
            wait_for_ack (bool): If True, wait for a matching decoded notify frame before returning.
            ack_timeout_sec (float): Timeout in seconds to wait for the ACK when `wait_for_ack` is True.
            ack_cmds (tuple[int, ...] | None): Optional set of `cmd` values that qualify as the ACK; when omitted, any decoded frame from the same device within the window qualifies.
            mtu_override (int | None): Optional MTU to use instead of the negotiated or default MTU (used for tests/diagnostics).

        Returns:
            bool: `True` if the GATT write completed (and, when requested, a matching ACK was received); `False` if no active BLE client exists for the device.

        Raises:
            RuntimeError: When the payload cannot be chunked for the selected MTU, on GATT-layer failures (including write timeouts), or when an ACK wait times out.
        """  # noqa: E501, RUF100
        client = self._clients.get(device_id)
        if client is None:
            _LOGGER.debug(
                "Jackery BLE %s: cannot send cmd=%d — no active client",
                device_id,
                cmd,
            )
            return False
        key = self._key_resolver(device_id)
        if key is None:
            raise RuntimeError(f"no bluetoothKey available for device {device_id}")
        # Resolve the effective MTU: explicit override wins (used by
        # tests and the service for diagnostics), then the per-device
        # cached negotiated value, then the Android-app default.
        if mtu_override is not None:
            if isinstance(mtu_override, bool) or not isinstance(mtu_override, int):
                raise TypeError("mtu_override must be an integer")
            mtu = mtu_override
        else:
            mtu = self.mtu_for_device(device_id)
        try:
            chunks = ble.split_body_for_mtu(body, mtu)
        except ValueError as err:
            raise RuntimeError(
                f"BLE MTU {mtu} too small to fit any body for {device_id}: {err}"
            ) from err
        chunk_count = len(chunks)
        _LOGGER.debug(
            "Jackery BLE %s: writing cmd=%d body=%d bytes across %d frame(s) at mtu=%d",
            device_id,
            cmd,
            len(body),
            chunk_count,
            mtu,
        )
        # Register the ACK *before* the write — otherwise a fast-echoing
        # peripheral could deliver the notify before the future exists,
        # and we would time out spuriously. One pending ack covers the
        # whole logical message (all chunks) because we have no firmware
        # evidence the device echoes per-chunk.
        pending: _PendingAck | None = None
        if wait_for_ack:
            pending = self._register_pending_ack(device_id, ack_cmds)
        stats = self.stats_for(device_id)
        property_query_started_at: datetime | None = None
        try:
            for idx, chunk in enumerate(chunks, start=1):
                property_query_started_at = self._mark_property_query_started(
                    device_id,
                    cmd,
                    property_query_started_at,
                )
                plain = ble.build_binary_frame(
                    cmd=cmd,
                    body=chunk,
                    flags=flags,
                    frame_index=idx,
                    chunk_count=chunk_count,
                )
                blob = ble.encrypt_binary_notify(plain, key)
                await asyncio.wait_for(
                    client.write_gatt_char(
                        ble.BLE_WRITE_CHAR_UUID, blob, response=True
                    ),
                    timeout=timeout_sec,
                )
        except asyncio.CancelledError:
            if pending is not None:
                self._discard_pending_ack(device_id, pending)
            raise
        except TimeoutError as err:
            stats.writes_with_response_failed += 1
            if pending is not None:
                self._discard_pending_ack(device_id, pending)
            raise RuntimeError(
                f"BLE write to {device_id} timed out after {timeout_sec}s"
            ) from err
        except Exception as err:  # bleak surfaces BleakError + variants
            stats.writes_with_response_failed += 1
            if pending is not None:
                self._discard_pending_ack(device_id, pending)
            raise RuntimeError(f"BLE write to {device_id} failed: {err}") from err
        if pending is not None:
            try:
                # ``shield`` keeps the future alive even if ``wait_for``
                # cancels the inner wait on timeout — we want to discard
                # it ourselves so the notify handler can observe the
                # removal cleanly.
                await asyncio.wait_for(
                    asyncio.shield(pending.future), timeout=ack_timeout_sec
                )
            except asyncio.CancelledError:
                self._discard_pending_ack(device_id, pending)
                raise
            except TimeoutError as err:
                self._discard_pending_ack(device_id, pending)
                stats.acks_timed_out += 1
                stats.last_error = f"ack timeout cmd={cmd}"
                raise RuntimeError(
                    f"BLE ack timeout for cmd={cmd} on {device_id} after "
                    f"{ack_timeout_sec}s"
                ) from err
            stats.acks_received += 1
            stats.last_ack_at = datetime.now()
        return True

    # ------------------------------------------------------------------
    # ACK registry (internal)
    # ------------------------------------------------------------------

    def _register_pending_ack(
        self, device_id: str, ack_cmds: tuple[int, ...] | None
    ) -> _PendingAck:
        """Register a pending ACK wait record for the given device.

        Parameters:
            device_id (str): Identifier of the device the ACK is expected from.
            ack_cmds (tuple[int, ...] | None): Optional sequence of acceptable command IDs that will satisfy the ACK.
                If `None`, any decoded frame will satisfy the pending ACK.

        Returns:
            _PendingAck: A record containing `expected_cmds` (a `frozenset` of the provided command IDs or `None`)
                and `future`, an `asyncio.Future` that will be resolved with the matching `ble.BleBinaryFrame`.
        """  # noqa: E501, RUF100
        loop = asyncio.get_running_loop()
        expected_cmds: frozenset[int] | None = None
        if ack_cmds:
            parsed_cmds: set[int] = set()
            for ack_cmd in ack_cmds:
                if isinstance(ack_cmd, bool) or not isinstance(ack_cmd, int):
                    raise ValueError("ack_cmds must be an integer")  # noqa: TRY004
                parsed_cmds.add(ack_cmd)
            expected_cmds = frozenset(parsed_cmds)
        pending = _PendingAck(
            expected_cmds=expected_cmds,
            future=loop.create_future(),
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

    def _resolve_pending_acks(self, device_id: str, frame: ble.BleBinaryFrame) -> None:
        """Fulfil every pending ACK on ``device_id`` matched by ``frame``.

        A pending record with ``expected_cmds=None`` matches any frame.
        Matched records are removed from the registry as they fire so a
        single notify cannot accidentally fulfil the same future twice.
        """
        bucket = self._pending_acks.get(device_id)
        if not bucket:
            return
        remaining: list[_PendingAck] = []
        for pending in bucket:
            if pending.future.done():
                continue
            if pending.expected_cmds is None or frame.cmd in pending.expected_cmds:
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
        return stats

    def all_stats(self) -> dict[str, BleListenerStats]:
        """Return the per-device stats map (mutating it is undefined)."""
        return self._stats

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self, device_ids: list[str]) -> None:
        """Start BLE advertisement monitoring and register callbacks that spawn per-device connection runners when a matching advertisement is observed.

        Parameters:
            device_ids (list[str]): Device IDs to monitor; a background connection task will be created lazily for a device the first time an advertisement matching the listener's BLE matcher is seen.
        """  # noqa: E501, RUF100
        # Deferred imports keep this module importable in unit-test
        # environments without BlueZ / bleak.
        from homeassistant.components import bluetooth

        self._stop_event.clear()

        matcher: BluetoothCallbackMatcher = {
            "service_uuid": ble.BLE_SERVICE_UUID,
            "manufacturer_id": ble.BLE_MANUFACTURER_ID,
        }
        unregister = bluetooth.async_register_callback(
            self._hass,
            self._on_advertisement,
            matcher,
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
        self._unregister_callbacks.append(unregister)
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

    async def async_stop(self) -> None:
        """Stop the BLE listener and release its resources.

        Signals the listener to stop, unregisters Bluetooth advertisement callbacks, cancels active connection runner tasks and waits up to _STOP_TIMEOUT_SEC for them to exit, clears connection state, and cancels any pending ACK futures so callers waiting for acknowledgements do not hang. Logs the listener shutdown.
        """  # noqa: E501, RUF100
        self._aborted = True
        self._stop_event.set()
        for unregister in self._unregister_callbacks:
            try:
                unregister()
            except Exception as err:  # pragma: no cover — HA callback contract is sync
                _LOGGER.debug("Jackery BLE: callback unregister failed: %s", err)
        self._unregister_callbacks.clear()
        # Cancel any running connection tasks.
        tasks = [task for task in self._connections.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=_STOP_TIMEOUT_SEC,
                )
            except TimeoutError:
                _LOGGER.warning(
                    "Jackery BLE: %d connection task(s) did not exit within %ss; "
                    "leaving them to the event loop",
                    sum(1 for t in tasks if not t.done()),
                    _STOP_TIMEOUT_SEC,
                )
        self._connections.clear()
        # Fail any pending ACKs so callers awaiting a write don't hang.
        # Use getattr to stay safe against stubbed instances that may have
        # been constructed via ``__new__`` in tests without going through
        # ``__init__``.
        pending_acks: dict[str, list[_PendingAck]] = getattr(self, "_pending_acks", {})
        for bucket in pending_acks.values():
            for pending in bucket:
                if not pending.future.done():
                    pending.future.cancel()
        pending_acks.clear()
        _LOGGER.info("Jackery BLE listener stopped")

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
        if device_id in self._connections and not self._connections[device_id].done():
            return
        # Spawn a *background* task — HA's bluetooth callback contract is
        # strictly sync, so we cannot await the connect here. Using
        # ``async_create_background_task`` keeps the runner out of HA's
        # setup-task tracker, so a long-running BLE backoff cannot cause
        # ``Setup timed out for bootstrap`` warnings on startup.
        self._connections[device_id] = self._hass.async_create_background_task(
            self._async_run_connection(device_id, service_info.address),
            name=f"jackery_ble_{device_id}",
        )

    def _device_id_from_service_info(
        self,
        service_info: BluetoothServiceInfoBleak,
    ) -> str | None:
        """Resolve a BLE advertisement to a known Jackery device id and cache the device's BLE MAC address.

        If the advertisement corresponds to a device the integration knows about, the function records device_id -> address in the internal cache on first match so future advertisements skip resolution. It returns the mapped device id when found, or `None` if no mapping could be determined.

        Returns:
            `device_id` if the advertisement maps to a known device, `None` otherwise.
        """  # noqa: E501, RUF100
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
            _LOGGER.debug(
                "Jackery BLE: advertisement for serial %s @ %s — no device "
                "id mapping yet",
                serial,
                address,
            )
            return None
        # Step 4: cache + log on first match for this device id.
        if device_id not in self._device_addresses:
            self._device_addresses[device_id] = address
            _LOGGER.info(
                "Jackery BLE: matched serial %s @ %s to device %s",
                serial,
                address,
                device_id,
            )
        return device_id

    async def _async_run_connection(self, device_id: str, address: str) -> None:
        """Maintain a persistent BLE GATT session for the given device, subscribing to notifications and reconnecting on link loss.

        This coroutine opens and publishes a Bleak client for the given address, subscribes to the notify characteristic, runs a keep-alive while connected, and tears down and retries the session on disconnect until the listener is stopped or the task is cancelled.

        Raises:
            asyncio.CancelledError: if the task is cancelled during shutdown.
        """  # noqa: E501, RUF100
        from bleak.exc import BleakError
        from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS, establish_connection

        from homeassistant.components import bluetooth

        stats = self.stats_for(device_id)
        consecutive_failures = 0
        try:
            while not self._stop_event.is_set() and not self._aborted:
                if consecutive_failures >= _MAX_CONNECTION_RETRIES:
                    _LOGGER.warning(
                        "Jackery BLE %s: giving up after %d consecutive failures",
                        device_id,
                        consecutive_failures,
                    )
                    return
                backoff = min(
                    _RECONNECT_BACKOFF_SEC * (2**consecutive_failures),
                    _MAX_BACKOFF_SEC,
                )
                ble_device = bluetooth.async_ble_device_from_address(
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
                    # wait one backoff window and look the address up again.
                    # ``async_ble_device_from_address`` is cheap and
                    # idempotent; the matcher callback in parallel still
                    # works, and ``_stop_event`` aborts the wait cleanly on
                    # integration unload.
                    _LOGGER.info(
                        "Jackery BLE %s: address %s not connectable right "
                        "now; retrying in %.0fs",
                        device_id,
                        address,
                        backoff,
                    )
                    consecutive_failures += 1
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=backoff,
                        )
                        return  # stop_event fired during the wait  # noqa: TRY300
                    except TimeoutError:
                        continue
                stats.connect_attempts += 1
                try:
                    client = await establish_connection(
                        client_class=__import__("bleak").BleakClient,
                        device=ble_device,
                        name=f"jackery-{device_id}",
                        disconnected_callback=lambda _client: self._on_disconnect(
                            device_id
                        ),
                        max_attempts=3,
                    )
                except BLEAK_RETRY_EXCEPTIONS as err:
                    stats.connect_failures += 1
                    stats.last_error = f"connect: {err}"
                    # PROTOCOL.md §4: the peripheral may stop advertising
                    # while paired with another central, so a one-shot
                    # ``return`` here would leave the runner dead until a
                    # fresh advertisement arrived. Back off and try again;
                    # ``establish_connection`` already retries internally
                    # ``max_attempts=3`` times, so the outer retry only
                    # kicks in on hard failures (radio gone, peripheral
                    # power-cycled).
                    consecutive_failures += 1
                    _LOGGER.info(
                        "Jackery BLE %s connect failed: %s; retrying in %.0fs",
                        device_id,
                        err,
                        backoff,
                    )
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=backoff,
                        )
                        return  # stop_event fired during the wait  # noqa: TRY300
                    except TimeoutError:
                        continue

                stats.last_connect_at = datetime.now()
                # Publish the live client so async_send_command can use
                # this session without re-establishing its own connect.
                self._clients[device_id] = client
                _LOGGER.info(
                    "Jackery BLE %s: connected to %s; subscribing to notify %s",
                    device_id,
                    address,
                    ble.BLE_NOTIFY_CHAR_UUID,
                )

                async def _notify_callback(
                    _characteristic: object, data: bytearray
                ) -> None:
                    await self._handle_notification(device_id, bytes(data))

                keep_alive_task: asyncio.Task[None] | None = None
                try:
                    await client.start_notify(
                        ble.BLE_NOTIFY_CHAR_UUID, _notify_callback
                    )
                    consecutive_failures = 0
                    # Cache the negotiated MTU so ``async_send_command``
                    # can size per-frame bodies correctly. Different
                    # bleak backends expose this via ``mtu_size`` (int)
                    # or sometimes ``mtu``; both are best-effort.
                    self._record_negotiated_mtu(device_id, client)
                    # Start the keep-alive heartbeat. The SolarVault
                    # peripheral closes idle GATT sessions after ~20s.
                    # ``_async_keep_alive_loop`` writes a no-op cmd=106
                    # query every ``_KEEPALIVE_INTERVAL_SEC`` so the
                    # session stays warm.
                    keep_alive_task = self._hass.async_create_background_task(
                        self._async_keep_alive_loop(device_id),
                        name=f"jackery_ble_keepalive_{device_id}",
                    )
                    # Park the connection until the device drops it or we are
                    # asked to stop. We poll ``client.is_connected`` (a bleak
                    # property with no awaitable) alongside the stop event: the
                    # disconnect callback only records stats, and bleak backends
                    # do not all fire it reliably, so the 1s poll is a deliberate
                    # robustness net that a single ``Event.wait()`` cannot replace
                    # (hence ASYNC110 is suppressed below).
                    while not self._stop_event.is_set() and client.is_connected:  # noqa: ASYNC110, RUF100
                        await asyncio.sleep(1.0)
                except BleakError as err:
                    stats.last_error = f"notify: {err}"
                    _LOGGER.debug(
                        "Jackery BLE %s notify subscribe failed: %s",
                        device_id,
                        err,
                    )
                finally:
                    if keep_alive_task is not None and not keep_alive_task.done():
                        keep_alive_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await keep_alive_task
                    self._clients.pop(device_id, None)
                    # MTU is a per-session property; drop it so the next
                    # reconnect re-learns whatever the new link
                    # negotiates instead of carrying a stale value.
                    self._mtu.pop(device_id, None)
                    # Bound the disconnect attempt — a stuck radio /
                    # peripheral that never ACKs a DISCONNECT must not
                    # block HA shutdown. ``bleak``'s default disconnect
                    # has no timeout of its own.
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(client.disconnect(), timeout=5.0)
                    stats.last_disconnect_at = datetime.now()

                if self._stop_event.is_set():
                    return
                # PROTOCOL.md §4: BLE peripherals routinely drop idle
                # sessions; the surrounding ``while not self._stop_event``
                # loop reconnects after this backoff. Logged at INFO so
                # the user sees the reconnect cadence in default logs
                # (previously DEBUG, which made the silence invisible).
                _LOGGER.info(
                    "Jackery BLE %s: lost link, backoff %ss before retry",
                    device_id,
                    _LOST_LINK_BACKOFF_SEC,
                )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=_LOST_LINK_BACKOFF_SEC
                    )
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

    def _on_disconnect(self, device_id: str) -> None:
        """Handle a peripheral disconnect for the given device.

        Updates the device's `BleListenerStats.last_disconnect_at` to the current time and emits an info-level log indicating the device disconnected.

        Parameters:
            device_id (str): Identifier of the device whose disconnect is being recorded.
        """  # noqa: E501, RUF100
        stats = self.stats_for(device_id)
        stats.last_disconnect_at = datetime.now()
        # Promoted from DEBUG to INFO: peripheral disconnects are the
        # primary symptom of BLE silence and must be visible in default
        # HA logs so the user can correlate them with the keep-alive /
        # reconnect-backoff timing in PROTOCOL.md §4.
        _LOGGER.info("Jackery BLE %s: peripheral disconnected", device_id)

    # ------------------------------------------------------------------
    # Notification handler
    # ------------------------------------------------------------------

    async def _handle_notification(self, device_id: str, raw: bytes) -> None:
        """Process a BLE notification: decode into a BleFrameObservation, update per-device statistics, resolve any pending ACK waiters for a successfully parsed frame, and forward the observation to the configured async sink.

        If a per-device AES key is available the method attempts to decrypt the raw payload and, on decryption failure, performs a single fallback try after base64-decoding the payload. A BleFrameObservation (containing the original bytes, base64 encoding, the parsed frame when decoding succeeds, or a human-readable decode error) is always created and forwarded to the sink. When a frame is parsed successfully this method resolves matching pending ACK futures and increments decode-related counters; when parsing fails it increments the decode-failure counter.
        """  # noqa: E501, RUF100
        stats = self.stats_for(device_id)
        stats.frames_received += 1
        b64 = base64.b64encode(raw).decode("ascii")
        _LOGGER.debug("Jackery BLE %s notify: %d bytes", device_id, len(raw))

        parsed: ble.BleBinaryFrame | None = None
        decode_error: str | None = None

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

        observation = BleFrameObservation(
            received_at=datetime.now(),
            raw_bytes=raw,
            base64_encoded=b64,
            parsed=parsed,
            decode_error=decode_error,
        )
        if parsed is not None and parsed.cmd == 107:
            q = self._pending_property_query_starts.get(device_id)
            observation.source_started_at = q.popleft() if q else None
        stats.last_frame = observation
        if parsed is not None:
            stats.frames_decoded += 1
            stats.last_error = None
            _LOGGER.debug(
                "Jackery BLE %s decoded: cmd=%d body=%d bytes",
                device_id,
                parsed.cmd,
                len(parsed.body),
            )
            # Wake any in-flight writer waiting on an ACK echo for this
            # device. Done before the sink fires so callers observing the
            # ACK never race against the merge-into-coordinator step.
            self._resolve_pending_acks(device_id, parsed)
        else:
            stats.frames_decode_failed += 1
            if decode_error is not None:
                stats.last_error = f"notify: {decode_error}"
        try:
            await self._sink(device_id, observation)
        except Exception as err:  # pragma: no cover — sink misbehaviour
            _LOGGER.debug("Jackery BLE %s sink raised: %s", device_id, err)


__all__ = [
    "DEFAULT_BLE_CONNECT_TIMEOUT_SEC",
    "BleFrameObservation",
    "BleListenerStats",
    "FrameSink",
    "JackeryBleListener",
]
