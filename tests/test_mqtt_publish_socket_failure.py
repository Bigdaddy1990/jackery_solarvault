"""Cloud MQTT publish failures must not cause duplicate device commands."""

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from aiomqtt import MqttError
import pytest

from custom_components.jackery_solarvault.client.mqtt_push import (
    JackeryMqttPushClient,
    MqttPublishOutcomeUnknownError,
    MqttSessionState,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.exceptions import HomeAssistantError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.mark.parametrize(
    ["error", "transport_lost"],
    [
        [OSError("connection reset by peer"), True],
        [TimeoutError("publish operation timed out"), False],
        [MqttError("Operation timed out"), False],
        [RuntimeError("message ID already pending"), True],
    ],
    ids=[
        "connection-reset",
        "socket-timeout",
        "aiomqtt-callback-timeout",
        "message-id-race",
    ],
)
async def test_publish_failure_preserves_transport_when_result_is_uncertain(
    hass: HomeAssistant, error: OSError | MqttError | RuntimeError, transport_lost: bool
) -> None:
    """A publish timeout does not prove that the live receive session was lost."""
    client = JackeryMqttPushClient(hass, message_callback=AsyncMock())
    broker = MagicMock()
    broker.publish = AsyncMock(side_effect=error)
    client._client = broker  # ruff: ignore[private-member-access]
    client._connected = True  # ruff: ignore[private-member-access]
    client._connected_event.set()  # ruff: ignore[private-member-access]
    client._session_state = MqttSessionState.ONLINE  # ruff: ignore[private-member-access]

    with pytest.raises(RuntimeError, match="MQTT publish failed"):
        await client.async_publish_json("test/command", {"cmd": 110})

    broker.publish.assert_awaited_once()
    assert client.is_connected is not transport_lost
    assert client._connected_event.is_set() is not transport_lost  # ruff: ignore[private-member-access]
    expected_state = (
        MqttSessionState.RECONNECTING if transport_lost else MqttSessionState.ONLINE
    )
    assert client.session_state is expected_state
    assert "publish failed:" in str(client.diagnostics.get("last_error"))


@pytest.mark.parametrize(
    "error",
    [
        OSError("connection reset by peer"),
        MqttError("Operation timed out"),
        RuntimeError("message ID already pending"),
    ],
    ids=["broken-socket", "uncertain-callback", "message-id-race"],
)
async def test_unknown_cloud_publish_outcome_does_not_retry_device_command(
    hass: HomeAssistant, error: OSError | MqttError | RuntimeError
) -> None:
    """A timed-out QoS-0 publish may have reached the broker: never resend it."""
    mqtt = JackeryMqttPushClient(hass, message_callback=AsyncMock())
    broker = MagicMock()
    broker.publish = AsyncMock(side_effect=error)
    mqtt._client = broker  # ruff: ignore[private-member-access]
    mqtt._connected = True  # ruff: ignore[private-member-access]
    mqtt._connected_event.set()  # ruff: ignore[private-member-access]
    mqtt._session_state = MqttSessionState.ONLINE  # ruff: ignore[private-member-access]
    owner = MagicMock()
    owner._mqtt = mqtt  # ruff: ignore[private-member-access]
    owner._async_ensure_mqtt = AsyncMock()  # ruff: ignore[private-member-access]

    with patch.object(mqtt, "async_stop", new_callable=AsyncMock) as stop:
        with pytest.raises(HomeAssistantError) as caught:
            await JackerySolarVaultCoordinator._async_publish_with_retry(  # ruff: ignore[private-member-access]
                owner, "device-1", "test/command", {"cmd": 110}, command_cmd=110
            )

        broker.publish.assert_awaited_once()
        stop.assert_not_awaited()
        assert isinstance(caught.value.__cause__, MqttPublishOutcomeUnknownError)


async def test_session_change_after_publish_does_not_report_success(
    hass: HomeAssistant,
) -> None:
    """A retired MQTT runner must not claim an in-flight publish succeeded."""
    client = JackeryMqttPushClient(hass, message_callback=AsyncMock())
    broker = MagicMock()

    def _retire_session(*_args: object, **_kwargs: object) -> None:
        client._session_generation += 1  # ruff: ignore[private-member-access]

    broker.publish = AsyncMock(side_effect=_retire_session)
    client._client = broker  # ruff: ignore[private-member-access]
    client._connected = True  # ruff: ignore[private-member-access]
    client._connected_event.set()  # ruff: ignore[private-member-access]

    with pytest.raises(MqttPublishOutcomeUnknownError, match="session changed"):
        await client.async_publish_json("test/command", {"cmd": 110})

    broker.publish.assert_awaited_once()
    assert client.diagnostics["last_published_topic"] is None


async def test_broken_socket_retires_owned_mqtt_runner(
    hass: HomeAssistant,
) -> None:
    """An actual socket failure releases its owned runner for reconnect."""
    client = JackeryMqttPushClient(hass, message_callback=AsyncMock())
    runner = asyncio.create_task(asyncio.Event().wait())
    broker = MagicMock()
    broker.publish = AsyncMock(side_effect=ConnectionResetError("socket reset"))
    client._runner_task = runner  # ruff: ignore[private-member-access]
    client._client = broker  # ruff: ignore[private-member-access]
    client._connected = True  # ruff: ignore[private-member-access]
    client._connected_event.set()  # ruff: ignore[private-member-access]
    try:
        with pytest.raises(MqttPublishOutcomeUnknownError):
            await client.async_publish_json("test/command", {"cmd": 110})
        await asyncio.sleep(0)
        assert runner.cancelled()
    finally:
        if not runner.done():
            runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
