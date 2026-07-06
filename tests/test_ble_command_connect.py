"""BLE-first command writes ensure a connection for their own device_id.

Owner live capture 2026-07-05: every button press logged "BLE command
unavailable" and fell back to MQTT. Root cause: the BLE-first router called
``async_send_ble_command`` without ``connect_timeout_sec``, so
``async_ensure_connected`` was skipped and a write whose device_id had no live
client in the transport's ``_clients`` returned False. The router must ensure a
connection (bounded wait) before the GATT write.
"""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.const import (
    BLE_COMMAND_CONNECT_TIMEOUT_SEC,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_DEVICE_ID = "573702884982521856"
_ACTION_ID = 3022
_CMD = 107


@pytest.mark.asyncio()
async def test_ble_first_ensures_connection_before_write() -> None:
    """The BLE-first router passes a positive connect timeout to the write."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator)._coerce_transport_cmd = MagicMock(return_value=_CMD)  # noqa: SLF001
    cast("Any", coordinator)._command_body_for_transport = MagicMock(  # noqa: SLF001
        return_value=b"body",
    )
    send_ble = AsyncMock(return_value=True)
    cast("Any", coordinator).async_send_ble_command = send_ble

    await coordinator._async_publish_command_ble_first(  # noqa: SLF001
        _DEVICE_ID,
        message_type="DevicePropertyChange",
        action_id=_ACTION_ID,
        cmd=_CMD,
        body_fields={},
    )

    send_ble.assert_awaited_once()
    await_args = send_ble.await_args
    assert await_args is not None
    kwargs = await_args.kwargs
    assert kwargs["connect_timeout_sec"] == BLE_COMMAND_CONNECT_TIMEOUT_SEC
    assert kwargs["connect_timeout_sec"] > 0
