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

from __future__ import annotations  # noqa: TID251

import asyncio
import base64
import binascii
from collections.abc import Awaitable, Callable
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

try:
    from bleak.exc import BleakError
except ImportError:  # pragma: no cover - optional test dependency

    class BleakError(Exception):
        """Fallback used when bleak is unavailable during import-time tests."""


try:
    from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS, establish_connection
except ImportError:  # pragma: no cover - optional test dependency
    BLEAK_RETRY_EXCEPTIONS = (BleakError,)

    async def establish_connection(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401, RUF029
        """Raise a clear error when bleak-retry-connector is unavailable."""
        raise RuntimeError(  # noqa: TRY003
            "bleak-retry-connector is required for Jackery BLE transport"
        )


from homeassistant.components import bluetooth
from ..const import MQTT_CMD_QUERY_DEVICE_PROPERTY
from ..util import first_nonblank_int

from . import ble

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import (
        BluetoothCallbackMatcher,
        BluetoothServiceInfoBleak,
    )
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

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


def _coerce_ble_int(value: Any, field_name: str) -> int:  # noqa: ANN401
    """
    Parse and validate an integer BLE transport option.
    
    Parameters:
        value (Any): The input to parse as an integer.
        field_name (str): Field name included in the ValueError message when parsing fails.
    
    Returns:
        int: The parsed integer.
    
    Raises:
        ValueError: If `value` cannot be parsed as an integer.
    """
    parsed = first_nonblank_int(value)
    if parsed is None:
        raise ValueError(f"{field_name} must be an integer")  # noqa: TRY003
    return parsed


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
        """
        Initialize the Jackery BLE diagnostic listener.
        
        Parameters:
            hass (HomeAssistant): Home Assistant instance used for Bluetooth callbacks.
            sink (FrameSink): Async consumer called for each observed notification.
            key_resolver (Callable[[str], bytes | None]): Given a device_id, return the device's 16- or 32-byte AES key, or `None` if unknown.
            ble_address_resolver (Callable[[str], str | None]): Given a device_id, return the device's BLE MAC address, or `None` if unknown. Resolved addresses are cached and exposed via `address_for_device_id`.
            serial_resolver (Callable[[str], str | None] | None): Optional callable mapping a BLE advertisement serial to a device_id; if omitted, advertisements with unmapped serials cannot be resolved to device IDs.
        """
        self._hass = hass
        self._sink = sink
        self._key_resolver = key_resolver
        self._ble_address_resolver = ble_address_resolver
        self._serial_resolver = serial_resolver
        self._stats: dict[str, BleListenerStats] = {}
        self._unregister_callbacks: list[Callable[[], None]] = []
        self._connections: dict[str, asyncio.Task[None]] = {}
        self._stop_event = asyncio.Event()
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
        # One-shot diagnostic flags so a stale discovery_cache during a
        # cloud outage shows up at INFO level once per stable failure mode
        # — not on every advertisement / notify. Cleared the first time
        # the same device successfully resolves / decodes again.
        self._unmapped_serials_logged: set[str] = set()
        self._missing_key_logged: set[str] = set()

    def address_for_device_id(self, device_id: str) -> str | None:
        """
        Get the cached BLE MAC address for the given device.
        
        Returns:
            The cached BLE MAC address as a string for the specified device_id, or `None` if no address is known.
        """
        return self._device_addresses.get(device_id)

    async def async_ensure_connected(
        self,
        device_id: str,
        *,
        timeout_sec: float,
    ) -> bool:
        """
        Waits until a BLE client becomes available for the given device ID or the timeout elapses.
        
        If the listener knows or can resolve the device's BLE address, this method will ensure a background connection runner is started and poll until a client appears or the provided timeout passes.
        
        Parameters:
            device_id (str): Identifier of the target device.
            timeout_sec (float): Maximum number of seconds to wait for a client to become available.
        
        Returns:
            bool: `True` if a BLE client is available for the device, `False` otherwise.
        """
        if device_id in self._clients:
            return True
        address = self._device_addresses.get(device_id) or self._ble_address_resolver(
            device_id
        )
        if address is None:
            return False
        self._device_addresses.setdefault(device_id, address)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, timeout_sec)
        while not self._stop_event.is_set():
            if device_id in self._clients:
                return True
            task = self._connections.get(device_id)
            if task is None or task.done():
                self._connections[device_id] = self._hass.async_create_background_task(
                    self._async_run_connection(device_id, address),
                    name=f"jackery_ble_{device_id}",
                )
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.2, remaining))
        return device_id in self._clients

    # ------------------------------------------------------------------
    # Phase 3b: write path — send a command frame to the device
    # ------------------------------------------------------------------

    def _record_negotiated_mtu(
        self,
        device_id: str,
        client: Any,  # noqa: ANN401
    ) -> None:
        """
        Cache the negotiated GATT MTU for the given device after notification subscription.
        
        Checks common bleak client attributes (`mtu_size`, then `mtu`) and stores the first integer value greater than the BLE frame overhead into the listener's MTU cache for `device_id`. If no usable MTU is exposed, leaves the cache unset so writers will fall back to `ble.DEFAULT_BLE_MTU`.
        
        Parameters:
            device_id (str): Identifier of the Jackery device.
            client (Any): Bleak client-like object expected to expose `mtu_size` or `mtu` attributes.
        """
        for attr in ("mtu_size", "mtu"):
            value = getattr(client, attr, None)
            if (
                isinstance(value, int) and value > ble._BLE_FRAME_OVERHEAD  # noqa: SLF001
            ):
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
        """
        Get the negotiated BLE MTU for a device.
        
        Returns:
            int: Cached MTU for the given `device_id`, or `ble.DEFAULT_BLE_MTU` if no negotiated MTU is available.
        """
        return self._mtu.get(device_id, ble.DEFAULT_BLE_MTU)

    async def _async_keep_alive_loop(self, device_id: str) -> None:
        """
        Send periodic no-op query frames to keep a device's GATT session active.
        
        This background loop sends a minimal query (the module's query-device-property command) at the module's keep-alive interval to prevent the peripheral from closing idle BLE connections. The task is cancellable: Cancellation is propagated (CancelledError) while transient write errors are caught and logged so the loop continues.
        """
        try:  # noqa: PLW0717
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

    async def async_send_command(  # noqa: PLR0912, PLR0913
        self,
        device_id: str,
        *,
        cmd: int,
        body: bytes,
        flags: int = 0,
        timeout_sec: float = 10.0,
        wait_for_ack: bool = False,
        ack_timeout_sec: float = 5.0,
        ack_cmds: tuple[int, ...] | None = None,
        mtu_override: int | None = None,
    ) -> bool:
        """Build, encrypt and write a single command frame to char 0xEE01.

        Returns ``True`` when the GATT write completed (and, if
        ``wait_for_ack`` is set, the device echoed a decoded frame on the
        notify channel within ``ack_timeout_sec``). Returns ``False`` when
        no active client is available for the device — callers (e.g. the
        coordinator's BLE-first setter helper) use that to fall back to
        the cloud-MQTT pipeline.

        Raises ``ValueError`` for malformed inputs, ``RuntimeError`` for
        GATT-layer failures *or* for an ACK timeout when ``wait_for_ack``
        is enabled. The router catches the RuntimeError and falls back to
        MQTT; for SolarVault setters the duplicated write is idempotent.

        ``ack_cmds`` filters which notify frames count as the ACK. When
        omitted, any decoded frame on the same device within the window
        counts. The device typically echoes the same ``cmd`` back as a
        ``DevicePropertyChange``, but we have no firmware contract for
        that mapping yet, so the default stays permissive.

        The trailer field is left as four NUL bytes — see
        :class:`.ble.BleBinaryFrame` for the open question on its
        algorithm. If the device firmware rejects the write (no echo on
        the notify stream within a reasonable window), the trailer
        likely needs to be a real checksum derived from a Frida capture.
        """
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
            raise RuntimeError(  # noqa: TRY003
                f"no bluetoothKey available for device {device_id}"
            )
        # Resolve the effective MTU: explicit override wins (used by
        # tests and the service for diagnostics), then the per-device
        # cached negotiated value, then the Android-app default.
        if mtu_override is not None:
            mtu = _coerce_ble_int(mtu_override, "mtu_override")
        else:
            mtu = self.mtu_for_device(device_id)
        try:
            chunks = ble.split_body_for_mtu(body, mtu)
        except ValueError as err:
            raise RuntimeError(  # noqa: TRY003
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
        try:
            for idx, chunk in enumerate(chunks, start=1):
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
                        ble.BLE_WRITE_CHAR_UUID, blob, response=False
                    ),
                    timeout=timeout_sec,
                )
        except TimeoutError as err:
            if pending is not None:
                self._discard_pending_ack(device_id, pending)
            raise RuntimeError(  # noqa: TRY003
                f"BLE write to {device_id} timed out after {timeout_sec}s"
            ) from err
        except Exception as err:  # bleak surfaces BleakError + variants
            if pending is not None:
                self._discard_pending_ack(device_id, pending)
            raise RuntimeError(  # noqa: TRY003
                f"BLE write to {device_id} failed: {err}"
            ) from err
        if pending is not None:
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
                stats.last_error = f"ack timeout cmd={cmd}"
                raise RuntimeError(  # noqa: TRY003
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
        """
        Register a pending ACK watcher for the given device.
        
        If `ack_cmds` is provided, only frames whose `cmd` matches one of those values will satisfy the ACK; if `ack_cmds` is `None`, any decoded frame for the device can satisfy the ACK. The returned `_PendingAck` contains a `future` that will be completed with the matching decoded frame when it arrives.
        
        Parameters:
            device_id (str): Identifier of the device to watch for an ACK.
            ack_cmds (tuple[int, ...] | None): Sequence of command codes that qualify as an ACK, or `None` to accept any command.
        
        Returns:
            _PendingAck: The registered pending-ACK record whose `future` will be completed when a matching frame is received.
        """
        loop = asyncio.get_running_loop()
        pending = _PendingAck(
            expected_cmds=(
                frozenset(_coerce_ble_int(cmd, "ack_cmds") for cmd in ack_cmds)
                if ack_cmds
                else None
            ),
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
        """
        Start the BLE advertisement listener and register a callback for matching Jackery devices.
        
        Registers a Home Assistant Bluetooth advertisement callback that watches for the Jackery service UUID and manufacturer ID; connection tasks are created lazily when matching advertisements arrive. The provided device_ids list is used for startup logging and does not eagerly open connections.
         
        Parameters:
            device_ids (list[str]): Device identifiers for startup logging (no eager connection attempts are performed).
        """
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

    async def async_stop(self) -> None:
        """Shut the listener down. Safe to call from coordinator unload.

        Uses a hard timeout on the gather so a stuck GATT disconnect or
        radio hang cannot block HA shutdown beyond ``_STOP_TIMEOUT_SEC``.
        Any task that misses the timeout is left to be reaped by the
        event-loop during interpreter teardown.
        """
        self._stop_event.set()
        for unregister in self._unregister_callbacks:
            try:
                unregister()
            except Exception as err:  # noqa: BLE001
                # pragma: no cover - HA callback contract is sync
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
        change: Any,  # noqa: ANN401
    ) -> None:
        """
        Handle a Home Assistant Bluetooth advertisement by resolving the device and scheduling a connection task.
        
        This synchronous HA callback resolves the incoming advertisement to a Jackery device_id; if resolution succeeds it increments the device's advertisement counter and schedules a background connection runner for that device unless one is already active. The callback does not await any async work and returns immediately.
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
        """
        Resolve a BLE advertisement to a Jackery device id and cache the device's MAC address.
        
        Attempts resolution by (1) checking the cached device_id→MAC mapping, (2) extracting an ASCII serial from the manufacturer-data block for the Jackery company ID, and (3) using the injected serial resolver to map that serial to a device id. On first successful resolution for a device id, the function caches the device_id→MAC mapping. If no mapping is found for a discovered serial, a one-time informational log entry is emitted for that serial.
        
        Returns:
            str | None: The resolved `device_id` when found, `None` otherwise.
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
            if serial not in self._unmapped_serials_logged:
                self._unmapped_serials_logged.add(serial)
                _LOGGER.info(
                    "Jackery BLE: advertisement for serial %s @ %s has no "
                    "device-id mapping. Discovery_cache likely stale — a "
                    "successful cloud refresh (or a re-added device) is "
                    "needed to learn the serial-to-device-id mapping.",
                    serial,
                    address,
                )
            else:
                _LOGGER.debug(
                    "Jackery BLE: advertisement for serial %s @ %s — still "
                    "no device id mapping",
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
        # Clear the one-shot diagnostic flag now that this serial resolves.
        self._unmapped_serials_logged.discard(serial)
        return device_id

    async def _async_run_connection(  # noqa: PLR0915
        self, device_id: str, address: str
    ) -> None:
        """Maintain a GATT session for one device, reconnecting on drop."""
        stats = self.stats_for(device_id)
        try:  # noqa: PLW0717
            while not self._stop_event.is_set():
                ble_device = bluetooth.async_ble_device_from_address(
                    self._hass, address, connectable=True
                )
                if ble_device is None:
                    _LOGGER.debug(
                        "Jackery BLE %s: address %s not connectable right now, "
                        "waiting for next advertisement",
                        device_id,
                        address,
                    )
                    return
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
                    _LOGGER.debug(
                        "Jackery BLE %s connect failed: %s; will retry on next "
                        "advertisement",
                        device_id,
                        err,
                    )
                    return

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
                    _characteristic: Any,  # noqa: ANN401
                    data: bytearray,  # noqa: ANN401, RUF100
                ) -> None:
                    """
                    Handle a BLE notification and forward its payload to the listener's notification processor.
                    
                    Parameters:
                        data (bytearray): Raw notification payload received from the BLE characteristic; converted to bytes before processing.
                    """
                    await self._handle_notification(device_id, bytes(data))

                keep_alive_task: asyncio.Task[None] | None = None
                try:  # noqa: PLW0717
                    await client.start_notify(
                        ble.BLE_NOTIFY_CHAR_UUID, _notify_callback
                    )
                    stats.last_error = None
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
                    # Park the connection until the device drops it or we
                    # are asked to stop. ``client.is_connected`` is polled
                    # via the disconnect callback above.
                    while (  # noqa: ASYNC110
                        not self._stop_event.is_set() and client.is_connected
                    ):
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
                _LOGGER.debug(
                    "Jackery BLE %s: lost link, backoff %ss before retry",
                    device_id,
                    _RECONNECT_BACKOFF_SEC,
                )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=_RECONNECT_BACKOFF_SEC
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

    def _on_disconnect(self, device_id: str) -> None:
        """
        Record a disconnect event for the given device.
        
        Updates the device's statistics with the current disconnect timestamp and emits a debug log.
        Parameters:
        	device_id (str): Identifier of the Jackery device whose disconnect is being recorded.
        """
        stats = self.stats_for(device_id)
        stats.last_disconnect_at = datetime.now()
        _LOGGER.debug("Jackery BLE %s: peripheral disconnected", device_id)

    # ------------------------------------------------------------------
    # Notification handler
    # ------------------------------------------------------------------

    async def _handle_notification(self, device_id: str, raw: bytes) -> None:
        """Decrypt + parse a 0xEE02 notify frame and forward it to the sink.

        The wire format is the live-captured binary layout documented in
        :func:`.ble.decrypt_binary_notify` (``iv || ciphertext`` containing
        a DFED-prefixed binary header + body + 4-byte trailer). Both the
        raw bytes and the decoded :class:`.ble.BleBinaryFrame` are emitted
        so the diagnostics surface can still help if decode fails.
        """
        stats = self.stats_for(device_id)
        stats.frames_received += 1
        b64 = base64.b64encode(raw).decode("ascii")
        _LOGGER.debug("Jackery BLE %s notify: %d bytes", device_id, len(raw))

        parsed: ble.BleBinaryFrame | None = None
        decode_error: str | None = None

        key = self._key_resolver(device_id)
        if key is None:
            decode_error = "no bluetoothKey for device"
            if device_id not in self._missing_key_logged:
                self._missing_key_logged.add(device_id)
                _LOGGER.info(
                    "Jackery BLE %s: notify frame received but no "
                    "bluetoothKey is cached. The integration needs one "
                    "successful /v1/device/system/list call to capture "
                    "the per-device AES key — local BLE alone cannot "
                    "decrypt frames until that lands.",
                    device_id,
                )
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
                # Successful decode — clear the one-shot diagnostic flag so
                # a future key-loss can surface again at INFO.
                self._missing_key_logged.discard(device_id)

        observation = BleFrameObservation(
            received_at=datetime.now(),
            raw_bytes=raw,
            base64_encoded=b64,
            parsed=parsed,
            decode_error=decode_error,
        )
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
        try:
            await self._sink(device_id, observation)
        except Exception as err:  # pragma: no cover — sink misbehaviour  # noqa: BLE001
            _LOGGER.debug("Jackery BLE %s sink raised: %s", device_id, err)


__all__ = [
    "DEFAULT_BLE_CONNECT_TIMEOUT_SEC",
    "BleFrameObservation",
    "BleListenerStats",
    "FrameSink",
    "JackeryBleListener",
]
