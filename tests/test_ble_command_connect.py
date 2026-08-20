"""Independent BLE + Cloud MQTT command writes ensure BLE connection setup.

Owner live capture 2026-07-05: every button press logged "BLE command
unavailable". The command router now starts BLE and Cloud MQTT independently;
the BLE leg still must use a bounded connection wait before the GATT write.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.const import BLE_COMMAND_CONNECT_TIMEOUT_SEC
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.exceptions import HomeAssistantError

_DEVICE_ID = "573702884982521856"
_ACTION_ID = 3022
_CMD = 107


class _ImmediateBackgroundEntry:
    """Config-entry test double that schedules HA background tasks immediately."""

    @staticmethod
    def async_create_background_task(
        _hass: object,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str,
        eager_start: bool,
    ) -> asyncio.Task[Any]:
        """Schedule the given coroutine like Home Assistant's entry helper."""
        del eager_start
        return asyncio.create_task(coro, name=name)


def _ble_first_coordinator() -> JackerySolarVaultCoordinator:
    """Build a coordinator shell wired for ``_async_publish_command_ble_first``."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator).hass = object()
    cast("Any", coordinator).entry = _ImmediateBackgroundEntry()
    cast("Any", coordinator)._coerce_transport_cmd = MagicMock(return_value=_CMD)
    cast("Any", coordinator)._command_body_for_transport = MagicMock(
        return_value=b"body",
    )
    cast("Any", coordinator)._bind_cloud_command_attempt = MagicMock()
    cast("Any", coordinator)._record_successful_command_transports = MagicMock()
    cast("Any", coordinator)._record_independent_cloud_mqtt_result = MagicMock()
    return coordinator


@pytest.mark.asyncio
async def test_ble_first_ensures_connection_before_write() -> None:
    """The BLE leg passes a positive connect timeout while MQTT also starts."""
    coordinator = _ble_first_coordinator()
    send_ble = AsyncMock(return_value=True)
    cast("Any", coordinator).async_send_ble_command = send_ble
    publish_mqtt = AsyncMock()
    cast("Any", coordinator)._async_publish_command = publish_mqtt

    await coordinator._async_publish_command_ble_first(
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
    publish_mqtt.assert_awaited_once()


@pytest.mark.asyncio
async def test_ble_write_unavailable_does_not_block_mqtt() -> None:
    """A BLE write that reports "not sent" (False) does not block MQTT.

    ``async_send_ble_command`` returning ``False`` (no live client / write
    skipped) is not an error for the other transport. Cloud MQTT must still
    publish independently instead of being skipped by BLE availability.
    """
    coordinator = _ble_first_coordinator()
    cast("Any", coordinator).async_send_ble_command = AsyncMock(return_value=False)
    publish_mqtt = AsyncMock()
    cast("Any", coordinator)._async_publish_command = publish_mqtt

    await coordinator._async_publish_command_ble_first(
        _DEVICE_ID,
        message_type="DevicePropertyChange",
        action_id=_ACTION_ID,
        cmd=_CMD,
        body_fields={"foo": "bar"},
    )

    publish_mqtt.assert_awaited_once()
    assert publish_mqtt.await_args is not None
    assert publish_mqtt.await_args.args[:1] == (_DEVICE_ID,)
    assert publish_mqtt.await_args.kwargs["message_type"] == "DevicePropertyChange"
    assert publish_mqtt.await_args.kwargs["action_id"] == _ACTION_ID
    assert publish_mqtt.await_args.kwargs["cmd"] == _CMD
    assert publish_mqtt.await_args.kwargs["body_fields"] == {"foo": "bar"}
    assert publish_mqtt.await_args.kwargs["ensure_mqtt"] is True


@pytest.mark.asyncio
async def test_ble_error_and_mqtt_failure_both_raise() -> None:
    """When BLE and MQTT both fail, an error propagates after both are attempted.

    The command must not be silently swallowed when every supported transport
    fails. BLE is attempted first in the result list, so its error is the
    propagated first failure while MQTT is still awaited independently.
    """
    coordinator = _ble_first_coordinator()
    cast("Any", coordinator).async_send_ble_command = AsyncMock(
        side_effect=RuntimeError("ble write timed out"),
    )
    mqtt_error = HomeAssistantError("MQTT client not initialized")
    cast("Any", coordinator)._async_publish_command = AsyncMock(side_effect=mqtt_error)

    with pytest.raises(RuntimeError, match="ble write timed out"):
        await coordinator._async_publish_command_ble_first(
            _DEVICE_ID,
            message_type="DevicePropertyChange",
            action_id=_ACTION_ID,
            cmd=_CMD,
            body_fields={},
        )
    cast("Any", coordinator)._async_publish_command.assert_awaited_once()
