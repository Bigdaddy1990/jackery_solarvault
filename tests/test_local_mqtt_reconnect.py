"""Lifecycle regression tests for the Home Assistant MQTT receiver."""

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.client import local_mqtt
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)
from custom_components.jackery_solarvault.const import SHELLY_RPC_EVENT_TOPIC

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_OPAQUE_FRAME_COUNT = 10


@pytest.mark.parametrize("configured_topic", ["homeassistant", "jackery/+/state"])
@pytest.mark.asyncio
async def test_shared_client_subscribes_to_configured_and_shelly_topics(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    configured_topic: str,
) -> None:
    """A custom filter supplements both required Jackery topic families."""
    subscriptions: list[tuple[str, int, str | None]] = []
    unsubscribe_custom = MagicMock()
    unsubscribe_singular = MagicMock()
    unsubscribe_plural = MagicMock()
    unsubscribe_shelly = MagicMock()
    unsubscribes = iter((
        unsubscribe_custom,
        unsubscribe_singular,
        unsubscribe_plural,
        unsubscribe_shelly,
    ))
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
        return next(unsubscribes)

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

    assert subscriptions == [
        (configured_topic, 0, None),
        ("hb/device/#", 0, None),
        ("hb/devices/#", 0, None),
        (SHELLY_RPC_EVENT_TOPIC, 0, None),
    ]
    assert client.is_started
    assert client.is_connected
    assert client.diagnostics_snapshot()["transport"] == (
        "homeassistant.components.mqtt"
    )

    await client.async_stop()
    unsubscribe_custom.assert_called_once_with()
    unsubscribe_singular.assert_called_once_with()
    unsubscribe_plural.assert_called_once_with()
    unsubscribe_shelly.assert_called_once_with()
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
    assert subscribe.await_count == 4
    await client.async_stop()


@pytest.mark.asyncio
async def test_connection_status_registration_failure_retries_until_available(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient status-listener gap follows the normal subscription retry."""
    status_attempts = 0
    unsubscribe_status = MagicMock()
    subscribe = AsyncMock(return_value=MagicMock())

    def _subscribe_status(*_args: Any) -> Any:
        nonlocal status_attempts
        status_attempts += 1
        if status_attempts == 1:
            raise RuntimeError("MQTT status registry not ready")
        return unsubscribe_status

    monkeypatch.setattr(local_mqtt, "LOCAL_MQTT_RECONNECT_INITIAL_SEC", 0.0)
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_wait_for_mqtt_client",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(local_mqtt.mqtt, "async_subscribe", subscribe)
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe_connection_status",
        _subscribe_status,
    )
    monkeypatch.setattr(local_mqtt.mqtt, "is_connected", lambda _hass: True)
    client = JackeryLocalMqttClient(hass)

    await client.async_start()
    retry_task = client._retry_task
    assert retry_task is not None
    await retry_task

    assert status_attempts == 2
    assert subscribe.await_count == 3
    assert client.is_started
    await client.async_stop()
    unsubscribe_status.assert_called_once_with()


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
async def test_stop_drains_inflight_message_sink(hass: HomeAssistant) -> None:
    """Entry unload waits for an accepted frame without cancelling its sink."""
    sink_started = asyncio.Event()
    release_sink = asyncio.Event()
    sink_cancelled = asyncio.Event()

    async def _sink(_topic: str, _data: dict[str, Any] | None, _raw: bytes) -> bool:
        sink_started.set()
        try:
            await release_sink.wait()
        except asyncio.CancelledError:
            sink_cancelled.set()
            raise
        return True

    client = JackeryLocalMqttClient(hass, sink=_sink, topic_filter="homeassistant")
    client._async_message_received(
        MagicMock(topic="homeassistant/device", payload=b"{}", retain=False)
    )
    await sink_started.wait()

    stop_task = asyncio.create_task(client.async_stop())
    await asyncio.sleep(0)
    assert not stop_task.done()

    release_sink.set()
    await asyncio.wait_for(stop_task, timeout=1.0)

    assert not sink_cancelled.is_set()
    assert client.diagnostics_snapshot()["messages_forwarded"] == 1


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


@pytest.mark.asyncio
async def test_unmatched_action_topic_reaches_sink(
    hass: HomeAssistant,
) -> None:
    """An action suffix alone is not proof that a live frame is a self-echo."""
    sink = AsyncMock(return_value=True)
    client = JackeryLocalMqttClient(hass, sink=sink, topic_filter="#")

    await client._handle_message(
        "vendor/device/action",
        b'{"body":{"seq":1}}',
    )

    sink.assert_awaited_once()
    assert client.diagnostics_snapshot()["messages_forwarded"] == 1


@pytest.mark.asyncio
async def test_confirmed_local_publish_echo_is_not_forwarded(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the exact bounded fingerprint of our own publish is suppressed."""
    publish = AsyncMock()
    sink = AsyncMock(return_value=True)
    monkeypatch.setattr(local_mqtt.mqtt, "async_publish", publish)
    client = JackeryLocalMqttClient(hass, sink=sink, topic_filter="#")
    payload = {"header": {"msgId": 3011}, "body": {"seq": 7}}

    await client.async_publish("hb/device/example/action", payload)
    publish_call = publish.await_args
    assert publish_call is not None
    serialized = publish_call.args[2]
    await client._handle_message("hb/device/example/action", serialized)

    sink.assert_not_awaited()
    diagnostics = client.diagnostics_snapshot()
    assert diagnostics["self_publish_echoes_ignored"] == 1
    assert diagnostics["pending_self_publish_echoes"] == 0


def test_connection_status_is_observational_only(hass: HomeAssistant) -> None:
    """Shared broker transitions change diagnostics, never other layers."""
    client = JackeryLocalMqttClient(hass, topic_filter="homeassistant")

    client._async_connection_status_changed(True)
    assert client.is_connected
    client._async_connection_status_changed(False)
    assert not client.is_connected
    assert client.diagnostics_snapshot()["last_disconnect_at"] is not None


@pytest.mark.asyncio
async def test_qos_wildcard_and_retained_semantics(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wildcard uses configured QoS and ignores stale retained telemetry."""
    sink = AsyncMock(return_value=True)
    subscribe = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(
        local_mqtt.mqtt, "async_wait_for_mqtt_client", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(local_mqtt.mqtt, "async_subscribe", subscribe)
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe_connection_status",
        lambda *_args: MagicMock(),
    )
    monkeypatch.setattr(local_mqtt.mqtt, "is_connected", lambda _hass: True)
    client = JackeryLocalMqttClient(
        hass, sink=sink, topic_filter="homeassistant/#", qos=2
    )

    await client.async_start()
    subscribe_call = subscribe.await_args
    assert subscribe_call is not None
    callback = subscribe_call.args[2]
    callback(
        MagicMock(topic="homeassistant/device", payload=b'{"batSoc": 1}', retain=True)
    )

    assert subscribe_call.kwargs["qos"] == 2
    sink.assert_not_awaited()
    assert client.diagnostics_snapshot()["retained_messages_dropped"] == 1
    await client.async_stop()


@pytest.mark.asyncio
async def test_repeated_start_stop_unsubscribes_each_topic_once_per_start(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Repeated lifecycle calls are idempotent without leaking listeners."""
    unsubscribe = MagicMock()
    unsubscribe_status = MagicMock()
    monkeypatch.setattr(
        local_mqtt.mqtt, "async_wait_for_mqtt_client", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        local_mqtt.mqtt, "async_subscribe", AsyncMock(return_value=unsubscribe)
    )
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe_connection_status",
        lambda *_args: unsubscribe_status,
    )
    monkeypatch.setattr(local_mqtt.mqtt, "is_connected", lambda _hass: True)
    client = JackeryLocalMqttClient(hass)

    await client.async_start()
    await client.async_start()
    await client.async_stop()
    await client.async_stop()

    assert unsubscribe.call_count == 3
    unsubscribe_status.assert_called_once_with()


@pytest.mark.asyncio
async def test_stop_overlapping_blocked_start_retries_partial_cleanup(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop owns every listener even when setup finishes after its ingress fence."""
    subscribe_entered = asyncio.Event()
    release_subscribe = asyncio.Event()
    unsubscribe_callbacks = [MagicMock() for _index in range(4)]
    unsubscribe_callbacks[2].side_effect = [RuntimeError("first cleanup failed"), None]
    unsubscribe_status = MagicMock()
    subscribe_calls = 0

    async def _subscribe(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal subscribe_calls
        index = subscribe_calls
        subscribe_calls += 1
        if index == 0:
            subscribe_entered.set()
            await release_subscribe.wait()
        return unsubscribe_callbacks[index]

    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_wait_for_mqtt_client",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(local_mqtt.mqtt, "async_subscribe", _subscribe)
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe_connection_status",
        lambda *_args: unsubscribe_status,
    )
    monkeypatch.setattr(local_mqtt.mqtt, "is_connected", lambda _hass: True)
    client = JackeryLocalMqttClient(hass, topic_filter="homeassistant")

    start_task = asyncio.create_task(client.async_start())
    await asyncio.wait_for(subscribe_entered.wait(), timeout=1)
    stop_task = asyncio.create_task(client.async_stop())
    await asyncio.sleep(0)
    assert not stop_task.done()
    release_subscribe.set()
    await asyncio.wait_for(asyncio.gather(start_task, stop_task), timeout=1)

    assert [callback.call_count for callback in unsubscribe_callbacks] == [1, 1, 2, 1]
    unsubscribe_status.assert_called_once_with()
    assert client._topic_unsubscribes == []
    assert client._retry_task is None
    assert not client.is_started


@pytest.mark.asyncio
async def test_partial_unsubscribe_failure_remains_retryable(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failed callback cannot orphan the remaining broker subscriptions."""
    callbacks = [MagicMock(name=f"unsubscribe_{index}") for index in range(4)]
    callbacks[1].side_effect = RuntimeError("temporary unsubscribe failure")
    unsubscribe_status = MagicMock()
    callback_iter = iter(callbacks)
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_wait_for_mqtt_client",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe",
        AsyncMock(side_effect=lambda *_args, **_kwargs: next(callback_iter)),
    )
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe_connection_status",
        lambda *_args: unsubscribe_status,
    )
    monkeypatch.setattr(local_mqtt.mqtt, "is_connected", lambda _hass: True)
    client = JackeryLocalMqttClient(hass, topic_filter="homeassistant")
    await client.async_start()

    with pytest.raises(RuntimeError, match="1 Local MQTT unsubscribe callback"):
        await client.async_stop()

    assert [callback.call_count for callback in callbacks] == [1, 1, 1, 1]
    assert len(client._topic_unsubscribes) == 1
    unsubscribe_status.assert_called_once_with()

    callbacks[1].side_effect = None
    await client.async_stop()

    assert [callback.call_count for callback in callbacks] == [1, 2, 1, 1]
    assert client._topic_unsubscribes == []


@pytest.mark.parametrize(
    ["configured_topic", "expected_topics"],
    [
        ["#", ["#"]],
        [
            "hb/#",
            ["hb/#", SHELLY_RPC_EVENT_TOPIC],
        ],
        [
            "homeassistant/#",
            ["homeassistant/#", "hb/device/#", "hb/devices/#"],
        ],
    ],
)
async def test_overlapping_filters_are_registered_only_once(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    configured_topic: str,
    expected_topics: list[str],
) -> None:
    """A broad custom filter must not duplicate delivery through narrower ones."""
    subscribe = AsyncMock(side_effect=lambda *_args, **_kwargs: MagicMock())
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_wait_for_mqtt_client",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(local_mqtt.mqtt, "async_subscribe", subscribe)
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe_connection_status",
        lambda *_args: MagicMock(),
    )
    monkeypatch.setattr(local_mqtt.mqtt, "is_connected", lambda _hass: True)
    client = JackeryLocalMqttClient(hass, topic_filter=configured_topic)

    await client.async_start()

    assert [call.args[1] for call in subscribe.await_args_list] == expected_topics
    diagnostics = client.diagnostics_snapshot(redact=False)
    assert diagnostics["official_subscription_active"] is True
    assert diagnostics["official_singular_subscription_active"] is True
    assert diagnostics["official_plural_subscription_active"] is True
    await client.async_stop()


async def test_failed_subscription_rollback_runs_every_cleanup_callback(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rollback failure cannot strand earlier subscriptions or status listener."""
    unsubscribe_first = MagicMock(name="unsubscribe_first")
    unsubscribe_second = MagicMock(
        name="unsubscribe_second",
        side_effect=RuntimeError("unsubscribe failed"),
    )
    unsubscribe_status = MagicMock(name="unsubscribe_status")
    subscribe_calls = 0

    async def _subscribe(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal subscribe_calls
        await asyncio.sleep(0)
        subscribe_calls += 1
        if subscribe_calls == 1:
            return unsubscribe_first
        if subscribe_calls == 2:
            return unsubscribe_second
        raise RuntimeError("third subscribe failed")

    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_wait_for_mqtt_client",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(local_mqtt.mqtt, "async_subscribe", _subscribe)
    monkeypatch.setattr(
        local_mqtt.mqtt,
        "async_subscribe_connection_status",
        lambda *_args: unsubscribe_status,
    )
    client = JackeryLocalMqttClient(hass, topic_filter="homeassistant")

    await client.async_start()

    unsubscribe_first.assert_called_once_with()
    unsubscribe_second.assert_called_once_with()
    unsubscribe_status.assert_called_once_with()
    assert client.diagnostics_snapshot()["subscription_retry_active"]
    unsubscribe_second.side_effect = None
    await client.async_stop()
    assert unsubscribe_second.call_count == 2
