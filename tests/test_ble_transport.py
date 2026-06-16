"""Unit tests for Jackery BLE transport edge cases."""
# ruff: noqa: PLC0415, PLR6301, RUF029, SLF001, TRY003

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from custom_components.jackery_solarvault.client.ble_transport import JackeryBleListener

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _hass() -> HomeAssistant:
    """Return a lightweight object typed as HomeAssistant for transport unit tests."""
    return cast("HomeAssistant", object())


@pytest.mark.asyncio()
async def test_send_command_discards_pending_ack_when_cancelled() -> None:
    """Cancelled ACK waits must not leave stale pending-ACK records."""

    class _Client:
        async def write_gatt_char(
            self,
            *args: object,
            response: bool,
        ) -> None:
            await asyncio.sleep(0)

    async def _sink(*args: object) -> None:
        return None

    listener = JackeryBleListener(
        _hass(),
        _sink,
        key_resolver=lambda _device_id: b"0123456789abcdef",
        ble_address_resolver=lambda _device_id: "00:11:22:33:44:55",
    )
    listener._clients["dev1"] = _Client()

    task = asyncio.create_task(
        listener.async_send_command(
            "dev1",
            cmd=107,
            body=b"",
            wait_for_ack=True,
        ),
    )
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert listener._pending_acks == {}


@pytest.mark.asyncio()
async def test_stop_logs_and_continues_when_unregister_callback_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A bad HA bluetooth unregister callback must not block listener shutdown."""

    async def _sink(*args: object) -> None:
        return None

    listener = JackeryBleListener(
        _hass(),
        _sink,
        key_resolver=lambda _device_id: b"0123456789abcdef",
        ble_address_resolver=lambda _device_id: "00:11:22:33:44:55",
    )

    def _broken_unregister() -> None:
        raise RuntimeError("callback registry already closed")

    listener._unregister_callbacks.append(_broken_unregister)

    with caplog.at_level("DEBUG"):
        await listener.async_stop()

    assert listener._unregister_callbacks == []
    assert "callback unregister failed" in caplog.text


@pytest.mark.asyncio()
async def test_handle_notification_logs_sink_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing coordinator sink must not break BLE notification handling."""

    async def _sink(*args: object) -> None:
        raise RuntimeError("coordinator unavailable")

    listener = JackeryBleListener(
        _hass(),
        _sink,
        key_resolver=lambda _device_id: None,
        ble_address_resolver=lambda _device_id: "00:11:22:33:44:55",
    )

    with caplog.at_level("DEBUG"):
        await listener._handle_notification("dev1", b"raw-notify")

    stats = listener.stats_for("dev1")
    assert stats.frames_received == 1
    assert stats.frames_decode_failed == 1
    assert "sink raised" in caplog.text


@pytest.mark.asyncio()
async def test_connection_runner_records_unexpected_crash(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unexpected HA bluetooth lookup crashes are captured in diagnostics."""
    import sys
    import types

    async def _sink(*args: object) -> None:
        return None

    listener = JackeryBleListener(
        _hass(),
        _sink,
        key_resolver=lambda _device_id: b"0123456789abcdef",
        ble_address_resolver=lambda _device_id: "00:11:22:33:44:55",
    )
    listener._connections["dev1"] = asyncio.current_task()  # type: ignore[assignment]

    bleak_module = types.ModuleType("bleak")
    bleak_module.BleakClient = object
    bleak_exc_module = types.ModuleType("bleak.exc")

    class _BleakError(Exception):
        pass

    bleak_exc_module.BleakError = _BleakError
    retry_module = types.ModuleType("bleak_retry_connector")
    retry_module.BLEAK_RETRY_EXCEPTIONS = (_BleakError,)

    async def _establish_connection(*args: object, **kwargs: object) -> object:
        raise AssertionError("connect should not be reached")

    retry_module.establish_connection = _establish_connection

    bluetooth_module = types.ModuleType("homeassistant.components.bluetooth")

    def _raise_lookup(*args: object, **kwargs: object) -> object:
        raise RuntimeError("bluetooth backend crashed")

    bluetooth_module.async_ble_device_from_address = _raise_lookup
    components_module = types.ModuleType("homeassistant.components")
    components_module.bluetooth = bluetooth_module
    ha_module = types.ModuleType("homeassistant")
    ha_module.components = components_module

    monkeypatch.setitem(sys.modules, "bleak", bleak_module)
    monkeypatch.setitem(sys.modules, "bleak.exc", bleak_exc_module)
    monkeypatch.setitem(sys.modules, "bleak_retry_connector", retry_module)
    monkeypatch.setitem(sys.modules, "homeassistant", ha_module)
    monkeypatch.setitem(sys.modules, "homeassistant.components", components_module)
    monkeypatch.setitem(
        sys.modules,
        "homeassistant.components.bluetooth",
        bluetooth_module,
    )

    with caplog.at_level("ERROR"):
        await listener._async_run_connection("dev1", "00:11:22:33:44:55")

    assert listener.stats_for("dev1").last_error == "runner: bluetooth backend crashed"
    assert "connection runner crashed" in caplog.text
    assert "dev1" not in listener._connections
