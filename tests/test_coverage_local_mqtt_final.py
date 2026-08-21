"""Behavioral edge coverage for the Home Assistant Local-MQTT adapter."""

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


@pytest.mark.asyncio
async def test_subscribe_failure_removes_status_callback_and_stays_retryable(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient HA subscription failure cannot leak its status callback."""
    unsubscribe_status = MagicMock()
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_wait_for_mqtt_client",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe",
        AsyncMock(side_effect=RuntimeError("subscription unavailable")),
    )
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe_connection_status",
        lambda _hass, _callback: unsubscribe_status,
    )
    client = JackeryLocalMqttClient(hass, topic_filter="jackery/#")

    await client.async_start()

    assert not client.is_started
    assert client.diagnostics_snapshot()["subscription_retry_active"]
    assert "RuntimeError" in cast_str(client.diagnostics_snapshot()["last_error"])
    unsubscribe_status.assert_called_once_with()
    await client.async_stop()


@pytest.mark.asyncio
async def test_cancelled_subscribe_removes_status_callback(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation propagates after removing the registered status callback."""
    unsubscribe_status = MagicMock()
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_wait_for_mqtt_client",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe",
        AsyncMock(side_effect=asyncio.CancelledError),
    )
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe_connection_status",
        lambda _hass, _callback: unsubscribe_status,
    )
    client = JackeryLocalMqttClient(hass, topic_filter="jackery/#")

    with pytest.raises(asyncio.CancelledError):
        await client._async_subscribe_once()

    unsubscribe_status.assert_called_once_with()


@pytest.mark.asyncio
async def test_successful_subscription_can_begin_disconnected(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HA may register the subscription before its broker session is online."""
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_wait_for_mqtt_client",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe",
        AsyncMock(return_value=MagicMock()),
    )
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe_connection_status",
        lambda _hass, _callback: MagicMock(),
    )
    monkeypatch.setattr(local_mqtt.mqtt, "is_connected", lambda _hass: False)
    client = JackeryLocalMqttClient(hass, topic_filter="jackery/#")

    await client.async_start()

    assert client.is_started
    assert not client.is_connected
    assert client.diagnostics_snapshot()["last_connect_at"] is None
    await client.async_stop()


@pytest.mark.asyncio
async def test_retry_supervisor_backs_off_and_clears_its_own_task(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated registration failures use capped backoff then terminate cleanly."""
    sleeps = AsyncMock()
    retry_once = AsyncMock(side_effect=(False, False, True))
    monkeypatch.setattr(local_mqtt.asyncio, "sleep", sleeps)
    monkeypatch.setattr(local_mqtt, "LOCAL_MQTT_RECONNECT_INITIAL_SEC", 1.0)
    monkeypatch.setattr(local_mqtt, "LOCAL_MQTT_RECONNECT_FACTOR", 2.0)
    monkeypatch.setattr(local_mqtt, "LOCAL_MQTT_RECONNECT_MAX_SEC", 3.0)
    client = JackeryLocalMqttClient(hass, topic_filter="jackery/#")
    monkeypatch.setattr(client, "_async_retry_subscription_once", retry_once)
    task = asyncio.create_task(client._async_retry_subscription())
    client._retry_task = task

    await task

    assert [call.args[0] for call in sleeps.await_args_list] == [1.0, 2.0, 3.0]
    assert client._retry_task is None


@pytest.mark.asyncio
async def test_stopped_retry_supervisor_exits_without_attempt(
    hass: HomeAssistant,
) -> None:
    """A supervisor started during teardown exits through its finalizer."""
    client = JackeryLocalMqttClient(hass, topic_filter="jackery/#")
    client._stopping = True

    await client._async_retry_subscription()

    assert client._retry_task is None


@pytest.mark.asyncio
async def test_start_ignores_an_active_subscription_retry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated starts do not create competing retry supervisors."""
    subscribe_once = AsyncMock(return_value=True)
    client = JackeryLocalMqttClient(hass, topic_filter="jackery/#")

    async def _wait_forever() -> None:
        await asyncio.Event().wait()

    blocker = asyncio.create_task(_wait_forever())
    client._retry_task = blocker
    monkeypatch.setattr(client, "_async_subscribe_once", subscribe_once)

    await client.async_start()

    subscribe_once.assert_not_awaited()
    await client.async_stop()
    assert blocker.cancelled()


@pytest.mark.asyncio
async def test_retry_once_stops_when_client_is_stopping_or_subscribed(
    hass: HomeAssistant,
) -> None:
    """A late retry never creates a ghost subscription after stop/success."""
    client = JackeryLocalMqttClient(hass, topic_filter="jackery/#")
    client._stopping = True
    assert await client._async_retry_subscription_once()

    client._stopping = False
    client._unsubscribe = MagicMock()
    assert await client._async_retry_subscription_once()


def test_duplicate_connection_status_is_a_noop(hass: HomeAssistant) -> None:
    """Repeated broker status callbacks do not rewrite timestamps."""
    client = JackeryLocalMqttClient(hass, topic_filter="jackery/#")

    client._async_connection_status_changed(False)

    assert client.diagnostics_snapshot()["last_disconnect_at"] is None


@pytest.mark.asyncio
async def test_message_wrapper_handles_text_and_bytearray_payloads(
    hass: HomeAssistant,
) -> None:
    """HA payload variants are normalized without filtering before the sink."""
    received: list[tuple[dict[str, Any] | None, bytes]] = []

    async def _sink(
        _topic: str,
        data: dict[str, Any] | None,
        raw: bytes,
    ) -> bool:
        await asyncio.sleep(0)
        received.append((data, raw))
        return True

    client = JackeryLocalMqttClient(hass, sink=_sink, topic_filter="jackery/#")
    await client._async_message_received(
        MagicMock(topic="jackery/device", payload='{"soc":80}', retain=False),
    )
    await client._async_message_received(
        MagicMock(
            topic="jackery/device",
            payload=bytearray(b"not-json"),
            retain=False,
        ),
    )

    assert received == [({"soc": 80}, b'{"soc":80}'), (None, b"not-json")]


@pytest.mark.asyncio
async def test_stopping_message_wrapper_and_handler_are_noops(
    hass: HomeAssistant,
) -> None:
    """Unload barriers prevent queued callbacks from mutating diagnostics."""
    sink = AsyncMock(return_value=True)
    client = JackeryLocalMqttClient(hass, sink=sink, topic_filter="jackery/#")
    client._stopping = True

    await client._async_message_received(
        MagicMock(topic="jackery/device", payload=b"{}"),
    )
    await client._handle_message("jackery/device", b"{}")

    sink.assert_not_awaited()
    assert client.diagnostics_snapshot()["messages_received"] == 0


@pytest.mark.asyncio
async def test_payload_without_sink_is_counted_as_dropped(
    hass: HomeAssistant,
) -> None:
    """Observation-only construction exposes an explicit dropped-frame count."""
    client = JackeryLocalMqttClient(hass, topic_filter="jackery/#")

    await client._handle_message("jackery/device", "[]")

    snapshot = client.diagnostics_snapshot(redact=False)
    assert snapshot["messages_received"] == 1
    assert snapshot["messages_dropped"] == 1
    assert snapshot["last_topic"] == "jackery/device"


@pytest.mark.asyncio
@pytest.mark.parametrize("sink_result", [False, RuntimeError("decoder failed")])
async def test_rejected_or_failed_sink_is_diagnosed(
    hass: HomeAssistant,
    sink_result: bool | RuntimeError,
) -> None:
    """Coordinator rejection and sink failure remain distinguishable."""
    if isinstance(sink_result, RuntimeError):
        sink = AsyncMock(side_effect=sink_result)
    else:
        sink = AsyncMock(return_value=sink_result)
    client = JackeryLocalMqttClient(hass, sink=sink, topic_filter="jackery/#")

    await client._handle_message("jackery/device", b"{}")

    snapshot = client.diagnostics_snapshot()
    assert snapshot["messages_dropped"] == 1
    if isinstance(sink_result, RuntimeError):
        assert snapshot["sink_errors"] == 1
        assert "RuntimeError" in cast_str(snapshot["last_sink_error"])
    else:
        assert snapshot["messages_rejected_by_sink"] == 1


@pytest.mark.asyncio
async def test_sink_cancellation_propagates(hass: HomeAssistant) -> None:
    """HA cancellation is never converted into a dropped payload."""
    sink = AsyncMock(side_effect=asyncio.CancelledError)
    client = JackeryLocalMqttClient(hass, sink=sink, topic_filter="jackery/#")

    with pytest.raises(asyncio.CancelledError):
        await client._handle_message("jackery/device", b"{}")

    assert client.diagnostics_snapshot()["messages_dropped"] == 0


@pytest.mark.asyncio
async def test_topic_tracking_truncation_does_not_drop_payload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A diagnostic topic-name cap never acts as a receive filter."""
    sink = AsyncMock(return_value=True)
    monkeypatch.setattr(local_mqtt, "LOCAL_MQTT_MAX_TOPIC_NAMES", 0)
    client = JackeryLocalMqttClient(hass, sink=sink, topic_filter="#")

    await client._handle_message("future/device", b"{}")

    snapshot = client.diagnostics_snapshot(redact=False)
    assert snapshot["topics_seen_truncated"] is True
    assert snapshot["messages_forwarded"] == 1


@pytest.mark.asyncio
async def test_oversized_payload_is_rejected_before_decode(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The receive limit must protect both the JSON decoder and coordinator."""
    sink = AsyncMock(return_value=True)
    monkeypatch.setattr(local_mqtt, "LOCAL_MQTT_MAX_PAYLOAD_BYTES", 2)
    client = JackeryLocalMqttClient(hass, sink=sink, topic_filter="#")

    await client._handle_message("jackery/device", b"{} ")

    sink.assert_not_awaited()
    snapshot = client.diagnostics_snapshot(redact=False)
    assert snapshot["messages_received"] == 1
    assert snapshot["messages_dropped"] == 1
    assert snapshot["messages_oversized"] == 1
    assert "exceeds 2 byte limit" in cast_str(snapshot["last_error"])


def cast_str(value: object) -> str:
    """Return a diagnostic value as text for an explicit substring assertion."""
    return str(value)
