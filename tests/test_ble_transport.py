"""Unit tests for Jackery BLE transport edge cases."""

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
        async def write_gatt_char(  # noqa: PLR6301
            self,
            *args: object,
            response: bool,
        ) -> None:
            await asyncio.sleep(0)

    async def _sink(*args: object) -> None:  # noqa: RUF029
        return None

    listener = JackeryBleListener(
        _hass(),
        _sink,
        key_resolver=lambda _device_id: b"0123456789abcdef",
        ble_address_resolver=lambda _device_id: "00:11:22:33:44:55",
    )
    listener._clients["dev1"] = _Client()  # noqa: SLF001

    task = asyncio.create_task(
        listener.async_send_command(
            "dev1",
            cmd=107,
            body=b"",
            wait_for_ack=True,
        )
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert listener._pending_acks == {}  # noqa: SLF001
