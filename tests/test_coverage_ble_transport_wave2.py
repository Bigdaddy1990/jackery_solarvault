"""Behavioral coverage for the BLE transport state machine and boundaries."""

import asyncio
import base64
from collections.abc import Awaitable, Callable, Coroutine
import inspect
import sys
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.client import (
    ble,
    ble_transport as ble_transport_module,
)
from custom_components.jackery_solarvault.client.ble_transport import (
    BleFrameObservation,
    JackeryBleListener,
    _GattSession,
    _body_is_complete_json_object,
)


class _HassStub:
    """Minimal Home Assistant task boundary used by the listener."""

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        """The active event loop."""
        return asyncio.get_running_loop()

    def async_create_background_task(  # ruff: ignore[no-self-use]
        self,
        target: Coroutine[Any, Any, None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        """Create a named task like Home Assistant."""
        return asyncio.create_task(target, name=name)


def _listener(
    *,
    key: bytes | None = b"k" * 16,
    sink: Callable[[str, BleFrameObservation], Awaitable[bool]] | None = None,
    address: str | None = None,
) -> JackeryBleListener:
    """Build a listener with deterministic, in-memory boundaries."""

    async def _accepted_sink(  # ruff: ignore[unused-async]
        _device_id: str,
        _observation: BleFrameObservation,
    ) -> bool:
        return True

    return JackeryBleListener(
        cast("Any", _HassStub()),
        sink or _accepted_sink,
        key_resolver=lambda _device_id: key,
        ble_address_resolver=lambda _device_id: address,
        connect_backoff_remaining=lambda _device_id, _now: 0.0,
        connect_backoff_note_failure=lambda _device_id, _now: 1.0,
        connect_backoff_note_success=lambda _device_id: None,
        keep_alive_msg_id=None,
        keep_alive_ble_msg_type=None,
    )


def _attach_session(
    listener: JackeryBleListener,
    device_id: str,
    client: object,
) -> _GattSession:
    """Install a current fake GATT session."""
    return listener._install_session(
        device_id,
        client,
        listener._next_session_generation(device_id),
    )


def _frame(
    *,
    index: int = 1,
    count: int = 1,
    flags: int = 3022,
    cmd: int = 107,
    body: bytes = b"{}",
    trailer: bytes = b"\x00\x00\x00\x00",
) -> ble.BleBinaryFrame:
    """Build an already-decoded BLE frame for state-machine tests."""
    return ble.BleBinaryFrame(
        frame_index=index,
        chunk_count=count,
        flags=flags,
        cmd=cmd,
        body=body,
        trailer=trailer,
    )


@pytest.mark.parametrize(
    ["body", "expected"],
    [
        [b'{"cmd":107}', True],
        [b'  {"cmd":107}\n', True],
        [b"[]", False],
        [b"{fragment", False],
        [b"", False],
    ],
)
def test_complete_json_object_detection(body: bytes, expected: bool) -> None:
    """Only complete JSON objects bypass multi-chunk assembly."""
    assert _body_is_complete_json_object(body) is expected


@pytest.mark.asyncio
async def test_async_start_registers_matcher_without_eager_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup binds cached identities but waits for a fresh advertisement."""
    registered: dict[str, object] = {}
    spawned: list[str] = []

    def _register(
        _hass: object,
        callback: object,
        matcher: object,
        scanning_mode: object,
    ) -> Callable[[], None]:
        registered.update(
            callback=callback,
            matcher=matcher,
            scanning_mode=scanning_mode,
        )
        return lambda: registered.update(unregistered=True)

    bluetooth_module = SimpleNamespace(
        BluetoothScanningMode=SimpleNamespace(ACTIVE="active"),
        async_register_callback=_register,
        async_discovered_service_info=lambda _hass, **_kwargs: (),
    )
    monkeypatch.setitem(
        sys.modules, "homeassistant.components.bluetooth", bluetooth_module
    )
    listener = _listener(address="AA:BB:CC:DD:EE:FF")
    monkeypatch.setattr(
        listener,
        "_spawn_connection_if_ready",
        spawned.append,
    )

    await listener.async_start([" dev ", "", "dev"])

    assert listener.address_for_device_id("dev") == "AA:BB:CC:DD:EE:FF"
    assert spawned == []
    assert registered["matcher"] == {
        "service_uuid": ble.BLE_SERVICE_UUID,
        "manufacturer_id": ble.BLE_MANUFACTURER_ID,
    }
    assert registered["scanning_mode"] == "active"
    await listener.async_stop()
    assert registered["unregistered"] is True


def test_connection_runner_does_not_schedule_periodic_ble_writes() -> None:
    """A notify subscription stays read-only until an explicit command arrives."""
    source = inspect.getsource(JackeryBleListener._async_run_connection)

    assert "_async_keep_alive_loop" not in source


def test_address_binding_rejects_foreign_and_ambiguous_devices() -> None:
    """One adapter address cannot silently bind to two cached device identities."""
    listener = _listener()
    listener._configured_device_ids = frozenset({"dev-a", "dev-b"})

    assert listener._bind_device_address("foreign", "AA") is False
    assert listener._bind_device_address("dev-a", "  ") is False
    assert listener._bind_device_address("dev-a", "AA:BB") is True
    assert listener._bind_device_address("dev-b", "aa:bb") is False


def test_negotiated_mtu_is_owned_by_current_session() -> None:
    """A stale GATT session cannot overwrite the current session's MTU."""

    class _Client:
        is_connected = True
        mtu_size = "invalid"
        mtu = 247

    listener = _listener()
    client = _Client()
    current = _attach_session(listener, "dev", client)
    stale = _GattSession(generation=0, client=client)

    listener._record_negotiated_mtu(
        "dev",
        cast("Any", client),
        session=stale,
    )
    assert listener.mtu_for_device("dev") == ble.DEFAULT_BLE_MTU

    listener._record_negotiated_mtu(
        "dev",
        cast("Any", client),
        session=current,
    )
    assert listener.mtu_for_device("dev") == 247
    assert listener._mtu_owners["dev"] is current


@pytest.mark.asyncio
async def test_reassembly_accepts_out_of_order_chunks_and_keeps_first_sequence() -> (
    None
):
    """Interleaving order does not corrupt a logical multi-chunk frame."""
    await asyncio.sleep(0)
    listener = _listener()

    incomplete, sequence = listener._reassemble_frame(
        "dev",
        _frame(index=2, count=2, body=b"world}"),
        notify_sequence=12,
    )
    assert incomplete is None
    assert sequence is None

    assembled, sequence = listener._reassemble_frame(
        "dev",
        _frame(index=1, count=2, body=b'{"hello":"'),
        notify_sequence=11,
    )
    assert assembled is not None
    assert assembled.body == b'{"hello":"world}'
    assert assembled.trailer == b"\x00\x00\x00\x00"
    assert sequence == 11
    assert listener.stats_for("dev").multi_chunk_messages_assembled == 1


@pytest.mark.asyncio
async def test_reassembly_restarts_after_conflicting_duplicate_chunk() -> None:
    """A replaced fragment can complete a new out-of-order message."""
    await asyncio.sleep(0)
    listener = _listener()
    listener._reassemble_frame(
        "dev",
        _frame(index=2, count=3, body=b"old-2"),
    )

    incomplete, sequence = listener._reassemble_frame(
        "dev",
        _frame(index=2, count=3, body=b"new-2"),
    )

    assert incomplete is None
    assert sequence is None
    listener._reassemble_frame(
        "dev",
        _frame(index=1, count=3, body=b"new-1"),
    )
    assembled, sequence = listener._reassemble_frame(
        "dev",
        _frame(index=3, count=3, body=b"new-3"),
    )

    assert assembled is not None
    assert assembled.body == b"new-1new-2new-3"
    assert sequence is None
    assert listener.stats_for("dev").multi_chunk_assemblies_dropped == 1


@pytest.mark.asyncio
async def test_new_client_is_disconnected_when_session_installation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connected client never leaks when its ownership token is rejected."""

    class _Client:
        is_connected = True

        def __init__(self) -> None:
            self.disconnect_calls = 0

        async def disconnect(self) -> None:
            self.disconnect_calls += 1
            self.is_connected = False

    client = _Client()

    async def _establish_connection(**_kwargs: object) -> _Client:
        return client

    listener = _listener()
    listener._ha_bluetooth = SimpleNamespace(
        async_ble_device_from_address=lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        ble_transport_module,
        "establish_connection",
        _establish_connection,
    )

    def _reject_install(
        _device_id: str,
        _client: object,
        _generation: int,
    ) -> _GattSession:
        raise RuntimeError("retained session still owns the device")

    monkeypatch.setattr(listener, "_install_session", _reject_install)

    await listener._async_run_connection("dev", "AA:BB:CC:DD:EE:FF")

    assert client.disconnect_calls == 1
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_connection_task_factory_rejection_closes_runner_and_stays_local() -> (
    None
):
    """A synchronous advertisement callback cannot leak or raise a coroutine."""
    rejected: Coroutine[Any, Any, None] | None = None

    class _RejectingHass(_HassStub):
        def async_create_background_task(
            self,
            target: Coroutine[Any, Any, None],
            *,
            name: str,
        ) -> asyncio.Task[None]:
            del name
            nonlocal rejected
            rejected = target
            raise RuntimeError("task factory rejected")

    listener = _listener()
    listener._hass = cast("Any", _RejectingHass())
    listener._device_addresses["dev"] = "AA:BB:CC:DD:EE:FF"

    listener._spawn_connection_if_ready("dev")

    assert "dev" not in listener._connections
    assert rejected is not None
    assert getattr(rejected, "cr_frame", object()) is None
    assert "task factory rejected" in str(listener.stats_for("dev").last_error)
    assert "dev" not in listener._frame_assemblies


@pytest.mark.parametrize(
    "frame",
    [
        _frame(index=2, count=1),
        _frame(index=1, count=0),
        _frame(index=3, count=2),
    ],
)
@pytest.mark.asyncio
async def test_reassembly_rejects_impossible_chunk_headers(
    frame: ble.BleBinaryFrame,
) -> None:
    """Malformed chunk headers are rejected before buffering state."""
    await asyncio.sleep(0)
    listener = _listener()
    with pytest.raises(ValueError):
        listener._reassemble_frame("dev", frame)


@pytest.mark.asyncio
async def test_notification_base64_fallback_forwards_decoded_frame() -> None:
    """A proxy's base64-wrapped encrypted notify still reaches the sink decoded."""
    key = b"k" * 16
    observations: list[BleFrameObservation] = []

    async def _sink(  # ruff: ignore[unused-async]
        _device_id: str,
        observation: BleFrameObservation,
    ) -> bool:
        observations.append(observation)
        return True

    listener = _listener(key=key, sink=_sink)
    plaintext = ble.build_binary_frame(
        cmd=107,
        flags=3022,
        body=b'{"cmd":107}',
    )
    encrypted = ble.encrypt_binary_notify(plaintext, key, iv=bytes(16))

    await listener._handle_notification(
        "dev",
        base64.b64encode(encrypted),
    )

    assert len(observations) == 1
    assert observations[0].parsed is not None
    assert observations[0].parsed.body == b'{"cmd":107}'
    assert observations[0].decode_error is None
    stats = listener.stats_for("dev")
    assert stats.frames_received == 1
    assert stats.frames_decoded == 1
    assert stats.frames_decode_failed == 0


@pytest.mark.asyncio
async def test_notification_without_key_records_decode_failure_and_forwards_raw() -> (
    None
):
    """Missing cached HTTP key is observable but never discards the raw notify."""
    observations: list[BleFrameObservation] = []

    async def _sink(  # ruff: ignore[unused-async]
        _device_id: str,
        observation: BleFrameObservation,
    ) -> bool:
        observations.append(observation)
        return True

    listener = _listener(key=None, sink=_sink)
    await listener._handle_notification("dev", b"opaque")

    assert observations[0].raw_bytes == b"opaque"
    assert observations[0].parsed is None
    assert observations[0].decode_error == "no bluetoothKey for device"
    stats = listener.stats_for("dev")
    assert stats.frames_decode_failed == 1
    assert stats.last_decode_error == "notify: no bluetoothKey for device"


@pytest.mark.asyncio
async def test_sink_failure_is_recorded_and_successful_frame_clears_it() -> None:
    """A transient sink error retries the same frame and clears after processing."""
    attempts = 0

    async def _sink(
        _device_id: str,
        _observation: BleFrameObservation,
    ) -> bool:
        nonlocal attempts
        await asyncio.sleep(0)
        attempts += 1
        if attempts == 1:
            raise RuntimeError("merge failed")
        return True

    key = b"k" * 16
    listener = _listener(key=key, sink=_sink)
    raw = ble.encrypt_binary_notify(
        ble.build_binary_frame(cmd=107, body=b'{"cmd":107}'),
        key,
        iv=bytes(16),
    )

    await listener._handle_notification("dev", raw)
    stats = listener.stats_for("dev")
    assert attempts == 2
    assert stats.last_sink_error is None
    assert stats.last_error is None


@pytest.mark.asyncio
async def test_sink_originated_cancelled_error_retries_without_spinning() -> None:
    """A callback-originated cancellation retries the observation exactly once."""
    attempts = 0

    async def _sink(
        _device_id: str,
        _observation: BleFrameObservation,
    ) -> bool:
        nonlocal attempts
        await asyncio.sleep(0)
        attempts += 1
        if attempts == 1:
            raise asyncio.CancelledError
        return True

    listener = _listener(key=None, sink=_sink)
    await asyncio.wait_for(listener._handle_notification("dev", b"opaque"), 1.0)

    assert attempts == 2
    assert listener.stats_for("dev").last_sink_error is None


@pytest.mark.asyncio
async def test_stale_session_notification_is_ignored_before_stats_and_sink() -> None:
    """Late notifications from an replaced GATT generation cannot alter live data."""
    sink = AsyncMock(return_value=True)
    listener = _listener(sink=sink)
    current_client = SimpleNamespace(is_connected=True)
    stale_client = SimpleNamespace(is_connected=True)
    _attach_session(listener, "dev", current_client)
    stale = _GattSession(generation=0, client=stale_client)

    await listener._handle_notification(
        "dev",
        b"late",
        session=stale,
        notify_sequence=1,
    )

    sink.assert_not_awaited()
    assert listener.stats_for("dev").frames_received == 0


@pytest.mark.asyncio
async def test_write_timeout_becomes_transport_error_without_stranding_ack() -> None:
    """A timed-out GATT write releases its ACK registration for fallback routing."""

    class _Client:
        is_connected = True

        async def write_gatt_char(  # ruff: ignore[no-self-use]
            self,
            _uuid: str,
            _blob: bytes,
            *,
            response: bool,
        ) -> None:
            assert response is False
            await asyncio.Event().wait()

    listener = _listener()
    _attach_session(listener, "dev", _Client())

    with pytest.raises(RuntimeError, match="BLE write to dev timed out"):
        await listener.async_send_command(
            "dev",
            msg_id=3022,
            ble_msg_type=107,
            body=b'{"cmd":107}',
            timeout_sec=0.001,
            wait_for_ack=True,
        )

    assert "dev" not in listener._pending_acks


@pytest.mark.asyncio
async def test_stop_clears_session_owned_state_and_pending_ack() -> None:
    """Unload invalidates sessions, MTU, assemblies, and ACK waiters atomically."""
    listener = _listener()

    class _ConnectedClient:
        is_connected = True

        async def disconnect(self) -> None:
            self.is_connected = False

    client = _ConnectedClient()
    session = _attach_session(listener, "dev", client)
    listener._mtu["dev"] = 247
    listener._mtu_owners["dev"] = session
    listener._reassemble_frame(
        "dev",
        _frame(index=1, count=2, body=b"partial"),
        session=session,
        notify_sequence=1,
    )
    pending = listener._register_pending_ack(
        "dev",
        session,
        3022,
        107,
    )

    await listener.async_stop()

    assert pending.future.cancelled()
    assert "dev" not in listener._sessions
    assert "dev" not in listener._clients
    assert "dev" not in listener._mtu
    assert "dev" not in listener._frame_assemblies
    assert listener.stats_for("dev").multi_chunk_assemblies_dropped == 1
