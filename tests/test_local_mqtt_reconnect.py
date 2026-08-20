"""Lifecycle regression tests for the Home Assistant MQTT receiver."""

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.client import local_mqtt
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_OPAQUE_FRAME_COUNT = 10


@pytest.mark.parametrize("configured_topic", ["homeassistant", "jackery/+/state"])
@pytest.mark.asyncio
async def test_shared_client_subscribes_once_to_exact_configured_topic(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    configured_topic: str,
) -> None:
    """The receiver uses one exact HA MQTT subscription and no private broker."""
    subscriptions: list[tuple[str, int, str | None]] = []
    unsubscribe = MagicMock()
    unsubscribe_status = MagicMock()

    async def _subscribe(
        _hass: HomeAssistant,
        topic: str,
        _callback: Any,
        *,
        qos: int,
        encoding: str | None,
    ) -> Any:
        await asyncio.sleep(0)
        subscriptions.append((topic, qos, encoding))
        return unsubscribe

    monkeypatch.setattr(
        local_mqtt.mqtt, "async_wait_for_mqtt_client", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(local_mqtt.mqtt, "async_subscribe", _subscribe)
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe_connection_status",
        lambda _hass, _callback: unsubscribe_status,
    )
    monkeypatch.setattr(local_mqtt.mqtt, "is_connected", lambda _hass: True)
    client = JackeryLocalMqttClient(hass, topic_filter=configured_topic)

    await client.async_start()
    await client.async_start()

    assert subscriptions == [(configured_topic, 0, None)]
    assert client.is_started
    assert client.is_connected
    assert client.diagnostics_snapshot()["transport"] == (
        "homeassistant.components.mqtt"
    )

    await client.async_stop()
    unsubscribe.assert_called_once_with()
    unsubscribe_status.assert_called_once_with()


@pytest.mark.asyncio
async def test_unavailable_ha_mqtt_does_not_create_a_private_connection(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable shared transport remains retryable without aiomqtt."""
    subscribe = AsyncMock()
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_wait_for_mqtt_client",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(local_mqtt.mqtt, "async_subscribe", subscribe)
    client = JackeryLocalMqttClient(hass, topic_filter="homeassistant")

    await client.async_start()

    subscribe.assert_not_awaited()
    assert not client.is_started
    assert not client.is_connected
    assert client.diagnostics_snapshot()["connect_attempts"] == 1
    assert client.diagnostics_snapshot()["subscription_retry_active"]
    await client.async_stop()


@pytest.mark.asyncio
async def test_unavailable_ha_mqtt_subscription_retries_until_available(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient HA-MQTT startup gap cannot leave the receiver stopped."""
    wait_for_client = AsyncMock(side_effect=(False, True))
    subscribe = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(local_mqtt, "LOCAL_MQTT_RECONNECT_INITIAL_SEC", 0.0)
    monkeypatch.setattr(local_mqtt.mqtt, "async_wait_for_mqtt_client", wait_for_client)
    monkeypatch.setattr(local_mqtt.mqtt, "async_subscribe", subscribe)
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe_connection_status",
        lambda _hass, _callback: MagicMock(),
    )
    monkeypatch.setattr(local_mqtt.mqtt, "is_connected", lambda _hass: True)
    client = JackeryLocalMqttClient(hass, topic_filter="homeassistant")

    await client.async_start()
    retry_task = client._retry_task
    assert retry_task is not None
    await retry_task

    assert client.is_started
    assert client.is_connected
    assert wait_for_client.await_count == 2
    subscribe.assert_awaited_once()
    await client.async_stop()


@pytest.mark.asyncio
async def test_stop_cancels_retry_wait_before_it_can_subscribe(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entry unload cancels a blocked HA-MQTT wait without a ghost subscription."""
    wait_started = asyncio.Event()
    wait_calls = 0

    async def _wait(_hass: HomeAssistant) -> bool:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            return False
        wait_started.set()
        await asyncio.Event().wait()
        return True

    subscribe = AsyncMock()
    monkeypatch.setattr(local_mqtt, "LOCAL_MQTT_RECONNECT_INITIAL_SEC", 0.0)
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_wait_for_mqtt_client",
        _wait,
    )
    monkeypatch.setattr(local_mqtt.mqtt, "async_subscribe", subscribe)
    client = JackeryLocalMqttClient(hass, topic_filter="homeassistant")

    await client.async_start()
    await wait_started.wait()
    await asyncio.wait_for(client.async_stop(), timeout=1.0)

    subscribe.assert_not_awaited()
    assert not client.diagnostics_snapshot()["subscription_retry_active"]


@pytest.mark.asyncio
async def test_stop_cancels_inflight_message_sink(hass: HomeAssistant) -> None:
    """Entry unload quiesces a callback already waiting in the old sink."""
    sink_started = asyncio.Event()
    sink_cancelled = asyncio.Event()

    async def _sink(_topic: str, _data: dict[str, Any] | None, _raw: bytes) -> bool:
        sink_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sink_cancelled.set()
            raise
        return True

    client = JackeryLocalMqttClient(hass, sink=_sink, topic_filter="homeassistant")
    message_task = asyncio.create_task(
        client._async_message_received(
            MagicMock(topic="homeassistant/device", payload=b"{}"),
        )
    )
    await sink_started.wait()

    await asyncio.wait_for(client.async_stop(), timeout=1.0)

    assert sink_cancelled.is_set()
    assert message_task.cancelled()


@pytest.mark.asyncio
async def test_broker_selected_payloads_reach_sink_without_content_filtering(
    hass: HomeAssistant,
) -> None:
    """JSON and opaque frames selected by the broker reach shared ingest."""
    received: list[tuple[str, dict[str, Any] | None, bytes]] = []

    async def _sink(
        topic: str,
        data: dict[str, Any] | None,
        raw: bytes,
    ) -> bool:
        await asyncio.sleep(0)
        received.append((topic, data, raw))
        return True

    client = JackeryLocalMqttClient(hass, sink=_sink, topic_filter="#")
    await client._handle_message(
        "homeassistant/device",
        b'{"batSoc":80}',
    )
    for index in range(_OPAQUE_FRAME_COUNT):
        await client._handle_message(
            "jackery/device",
            b"\xff" + bytes([index]),
        )

    diagnostics = client.diagnostics_snapshot()
    assert diagnostics["messages_received"] == _OPAQUE_FRAME_COUNT + 1
    assert diagnostics["messages_dropped"] == 0
    assert diagnostics["messages_forwarded"] == _OPAQUE_FRAME_COUNT + 1
    assert received[0][1] == {"batSoc": 80}
    assert all(item[1] is None for item in received[1:])


def test_connection_status_is_observational_only(hass: HomeAssistant) -> None:
    """Shared broker transitions change diagnostics, never other layers."""
    client = JackeryLocalMqttClient(hass, topic_filter="homeassistant")

    client._async_connection_status_changed(True)
    assert client.is_connected
    client._async_connection_status_changed(False)
    assert not client.is_connected
    assert client.diagnostics_snapshot()["last_disconnect_at"] is not None
