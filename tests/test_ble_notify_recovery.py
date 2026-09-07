"""BLE recovery logging follows successful notification subscription."""

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock

from bleak.exc import BleakError

from custom_components.jackery_solarvault.client import ble_transport
from homeassistant.components import bluetooth

if TYPE_CHECKING:
    import pytest

    from homeassistant.core import HomeAssistant


async def test_failed_notify_retries_do_not_report_recovery(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two failed subscriptions remain one outage and clean up on stop."""
    failed_twice = asyncio.Event()
    failed_attempts = 2
    clients: list[Mock] = []
    unregister = Mock()

    def fail_notify(*_args: object) -> None:
        if len(clients) >= failed_attempts:
            failed_twice.set()
        message = "notification subscription unavailable"
        raise BleakError(message)

    def connect(**_kwargs: object) -> Mock:
        client = Mock(is_connected=True, mtu_size=247)
        client.start_notify = AsyncMock(side_effect=fail_notify)
        client.disconnect = AsyncMock()
        clients.append(client)
        return client

    monkeypatch.setattr(
        bluetooth, "async_register_callback", Mock(return_value=unregister)
    )
    monkeypatch.setattr(
        bluetooth, "async_ble_device_from_address", Mock(return_value=Mock())
    )
    monkeypatch.setattr(
        ble_transport, "establish_connection", AsyncMock(side_effect=connect)
    )
    listener = ble_transport.JackeryBleListener(
        hass,
        AsyncMock(),
        key_resolver=lambda _device: bytes(16),
        ble_address_resolver=lambda _device: "aa:bb:cc:dd:ee:ff",
        connect_backoff_remaining=lambda _device, _now: 0,
        connect_backoff_note_failure=lambda _device, _now: 0,
        connect_backoff_note_success=lambda _device: None,
    )
    caplog.set_level(logging.INFO, logger=ble_transport.__name__)
    await listener.async_start(["device-1"])
    try:
        await listener.async_ensure_connected("device-1", timeout_sec=0)
        async with asyncio.timeout(5):
            await failed_twice.wait()
    finally:
        await listener.async_stop()

    assert "connection restored" not in caplog.text
    assert ": connected; subscribing" not in caplog.text
    assert caplog.text.count("lost link, backoff") == 1
    unregister.assert_called_once_with()
    for client in clients:
        client.disconnect.assert_awaited()


async def test_connection_is_ready_only_after_notify_subscription(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connected GATT link alone must not release a waiting command."""
    notify_entered = asyncio.Event()
    release_notify = asyncio.Event()

    async def subscribe(*_args: object) -> None:
        notify_entered.set()
        await release_notify.wait()

    client = Mock(is_connected=True, mtu_size=247)
    client.start_notify = AsyncMock(side_effect=subscribe)
    client.write_gatt_char = AsyncMock()
    client.stop_notify = AsyncMock()
    client.disconnect = AsyncMock()
    monkeypatch.setattr(bluetooth, "async_register_callback", Mock(return_value=Mock()))
    monkeypatch.setattr(
        bluetooth, "async_ble_device_from_address", Mock(return_value=Mock())
    )
    monkeypatch.setattr(
        ble_transport, "establish_connection", AsyncMock(return_value=client)
    )
    listener = ble_transport.JackeryBleListener(
        hass,
        AsyncMock(),
        key_resolver=lambda _device: bytes(16),
        ble_address_resolver=lambda _device: "aa:bb:cc:dd:ee:ff",
        connect_backoff_remaining=lambda _device, _now: 0,
        connect_backoff_note_failure=lambda _device, _now: 0,
        connect_backoff_note_success=lambda _device: None,
    )
    await listener.async_start(["device-1"])
    try:
        await listener.async_ensure_connected("device-1", timeout_sec=0)
        async with asyncio.timeout(5):
            await notify_entered.wait()
        assert not await listener.async_ensure_connected("device-1", timeout_sec=0.01)
        assert not await listener.async_send_command(
            "device-1", msg_id=1, ble_msg_type=106, body=b"{}", wait_for_ack=True
        )
        client.write_gatt_char.assert_not_awaited()
        release_notify.set()
        assert await listener.async_ensure_connected("device-1", timeout_sec=1)
        assert await listener.async_send_command(
            "device-1", msg_id=1, ble_msg_type=106, body=b"{}"
        )
        client.write_gatt_char.assert_awaited()
    finally:
        release_notify.set()
        await listener.async_stop()
    client.stop_notify.assert_awaited_once()
    client.disconnect.assert_awaited()
