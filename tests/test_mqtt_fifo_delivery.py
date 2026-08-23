"""Regression tests for ordered, lossless MQTT callback delivery."""

import asyncio
import contextlib
import inspect
import json
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.client import mqtt_push as mqtt_push_module
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)
from custom_components.jackery_solarvault.client.mqtt_push import JackeryMqttPushClient

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_cloud_mqtt_uses_one_fifo_consumer_for_a_b_a(
    hass: HomeAssistant,
) -> None:
    """A slow first frame cannot be overtaken by later cloud frames."""
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    completed: list[str] = []
    active = 0
    max_active = 0

    async def _callback(_topic: str, data: dict[str, Any]) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            sequence = str(data["body"]["seq"])
            if sequence == "A1":
                first_started.set()
                await release_first.wait()
            completed.append(sequence)
        finally:
            active -= 1

    client = JackeryMqttPushClient(hass, message_callback=_callback)
    for sequence in ("A1", "B", "A2"):
        client._handle_message(
            "device/property",
            json.dumps({"body": {"seq": sequence}}),
        )

    await first_started.wait()
    await asyncio.sleep(0)
    assert max_active == 1
    assert completed == []

    release_first.set()
    await client.async_wait_message_queue_idle()

    assert completed == ["A1", "B", "A2"]


async def test_cloud_mqtt_accepted_frame_survives_generation_change(
    hass: HomeAssistant,
) -> None:
    """Session turnover cannot discard a frame accepted into the FIFO."""
    delivered: list[int] = []

    async def _callback(_topic: str, data: dict[str, Any]) -> None:
        await asyncio.sleep(0)
        delivered.append(int(data["body"]["seq"]))

    client = JackeryMqttPushClient(hass, message_callback=_callback)
    client._session_generation = 7
    client._handle_message(
        "device/property",
        b'{"body":{"seq":1}}',
        generation=7,
    )
    client._session_generation = 8

    await client.async_wait_message_queue_idle()

    assert delivered == [1]


async def test_cloud_mqtt_stop_drains_accepted_frames_without_cancelling(
    hass: HomeAssistant,
) -> None:
    """Cloud stop waits for accepted frames instead of cancelling their callback."""
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    delivered: list[int] = []

    async def _callback(_topic: str, data: dict[str, Any]) -> None:
        sequence = int(data["body"]["seq"])
        if sequence == 1:
            first_started.set()
            await release_first.wait()
        delivered.append(sequence)

    client = JackeryMqttPushClient(hass, message_callback=_callback)
    client._handle_message("device/property", b'{"body":{"seq":1}}')
    client._handle_message("device/property", b'{"body":{"seq":2}}')
    await first_started.wait()

    stop_task = asyncio.create_task(client.async_stop())
    await asyncio.sleep(0)
    assert not stop_task.done()

    release_first.set()
    await asyncio.wait_for(stop_task, timeout=1.0)

    assert delivered == [1, 2]


async def test_cloud_mqtt_callback_error_is_visible_and_fifo_continues(
    hass: HomeAssistant,
    caplog: Any,
) -> None:
    """One bad callback is logged and cannot strand later accepted frames."""
    invoked: list[int] = []

    async def _callback(_topic: str, data: dict[str, Any]) -> None:
        await asyncio.sleep(0)
        sequence = int(data["body"]["seq"])
        invoked.append(sequence)
        if sequence == 2:
            raise RuntimeError("broken frame handler")

    client = JackeryMqttPushClient(hass, message_callback=_callback)
    with caplog.at_level(
        logging.ERROR,
        logger="custom_components.jackery_solarvault.client.mqtt_push",
    ):
        for sequence in (1, 2, 3):
            client._handle_message(
                "device/property",
                json.dumps({"body": {"seq": sequence}}),
            )
        await client.async_wait_message_queue_idle()

    assert invoked == [1, 2, 3]
    assert client.diagnostics_snapshot()["message_handler_errors"] == 1
    assert any(
        "message handler failed" in record.getMessage() for record in caplog.records
    )


async def test_cloud_mqtt_burst_has_one_consumer_and_unbounded_fifo(
    hass: HomeAssistant,
) -> None:
    """A burst creates one consumer and queues every remaining frame."""
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    delivered: list[int] = []

    async def _callback(_topic: str, data: dict[str, Any]) -> None:
        sequence = int(data["body"]["seq"])
        if sequence == 0:
            first_started.set()
            await release_first.wait()
        delivered.append(sequence)

    client = JackeryMqttPushClient(hass, message_callback=_callback)
    for sequence in range(50):
        client._handle_message(
            "device/property",
            json.dumps({"body": {"seq": sequence}}),
        )

    await first_started.wait()
    await asyncio.sleep(0)
    snapshot = client.diagnostics_snapshot()
    assert snapshot["pending_message_tasks"] == 1
    assert snapshot["message_queue_depth"] == 49
    assert snapshot["message_consumer_running"] is True
    assert snapshot["messages_dropped"] == 0

    release_first.set()
    await client.async_wait_message_queue_idle()
    assert delivered == list(range(50))


async def test_local_mqtt_stop_drains_fifo_without_cancelling_sink(
    hass: HomeAssistant,
) -> None:
    """Local MQTT preserves broker order and drains accepted frames on stop."""
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    completed: list[int] = []
    active = 0
    max_active = 0

    async def _sink(
        _topic: str,
        data: dict[str, Any] | None,
        _raw: bytes,
    ) -> bool:
        nonlocal active, max_active
        assert data is not None
        active += 1
        max_active = max(max_active, active)
        try:
            sequence = int(data["seq"])
            if sequence == 1:
                first_started.set()
                await release_first.wait()
            completed.append(sequence)
        finally:
            active -= 1
        return True

    client = JackeryLocalMqttClient(hass, sink=_sink, topic_filter="hb/device/#")
    assert not inspect.iscoroutinefunction(client._async_message_received)
    for sequence in (1, 2, 3):
        client._async_message_received(
            MagicMock(
                topic="hb/device/example/event",
                payload=json.dumps({"seq": sequence}).encode(),
                retain=False,
            )
        )
    await first_started.wait()

    stop_task = asyncio.create_task(client.async_stop())
    await asyncio.sleep(0)
    assert not stop_task.done()
    assert max_active == 1
    assert completed == []

    release_first.set()
    await asyncio.wait_for(stop_task, timeout=1.0)

    assert completed == [1, 2, 3]
    snapshot = client.diagnostics_snapshot()
    assert snapshot["messages_forwarded"] == 3
    assert snapshot["messages_dropped"] == 0
    assert snapshot["message_queue_depth"] == 0
    assert snapshot["message_consumer_running"] is False


async def test_cloud_mqtt_stop_follows_replacement_consumer_after_cancellation(
    hass: HomeAssistant,
) -> None:
    """A cancelled actor cannot let stop return before accepted delivery drains."""
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    entered: list[int] = []
    completed: list[int] = []

    async def _callback(_topic: str, data: dict[str, Any]) -> None:
        sequence = int(data["body"]["seq"])
        entered.append(sequence)
        if sequence == 1:
            first_started.set()
            await release_first.wait()
        completed.append(sequence)

    client = JackeryMqttPushClient(hass, message_callback=_callback)
    client._handle_message("device/property", b'{"body":{"seq":1}}')
    client._handle_message("device/property", b'{"body":{"seq":2}}')
    await first_started.wait()
    original_consumer = client._message_consumer_task
    assert original_consumer is not None

    stop_task = asyncio.create_task(client.async_stop())
    await asyncio.sleep(0)
    original_consumer.cancel()
    await asyncio.sleep(0)

    assert not stop_task.done()
    release_first.set()
    await asyncio.wait_for(stop_task, timeout=1.0)

    assert entered == [1, 2]
    assert completed == [1, 2]
    assert client.diagnostics_snapshot()["message_queue_depth"] == 0
    assert not client._message_tasks


async def test_local_mqtt_stop_follows_replacement_consumer_after_cancellation(
    hass: HomeAssistant,
) -> None:
    """Local stop owns the stable drain even if its original actor is cancelled."""
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    entered: list[int] = []
    completed: list[int] = []

    async def _sink(
        _topic: str,
        data: dict[str, Any] | None,
        _raw: bytes,
    ) -> bool:
        assert data is not None
        sequence = int(data["seq"])
        entered.append(sequence)
        if sequence == 1:
            first_started.set()
            await release_first.wait()
        completed.append(sequence)
        return True

    client = JackeryLocalMqttClient(hass, sink=_sink)
    assert not inspect.iscoroutinefunction(client._async_message_received)
    for sequence in (1, 2):
        client._async_message_received(
            MagicMock(
                topic="hb/device/example/event",
                payload=json.dumps({"seq": sequence}).encode(),
                retain=False,
            )
        )
    await first_started.wait()
    original_consumer = client._message_consumer_task
    assert original_consumer is not None

    stop_task = asyncio.create_task(client.async_stop())
    await asyncio.sleep(0)
    original_consumer.cancel()
    await asyncio.sleep(0)

    assert not stop_task.done()
    release_first.set()
    await asyncio.wait_for(stop_task, timeout=1.0)

    assert entered == [1, 2]
    assert completed == [1, 2]
    assert client.diagnostics_snapshot()["message_queue_depth"] == 0
    assert not client._message_tasks


async def test_cloud_mqtt_delivery_cancellation_does_not_repeat_callback(
    hass: HomeAssistant,
) -> None:
    """Cancelling the delivery owner cannot cancel or replay its callback."""
    entered: list[int] = []
    completed: list[int] = []
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()

    async def _callback(_topic: str, data: dict[str, Any]) -> None:
        sequence = int(data["body"]["seq"])
        entered.append(sequence)
        callback_started.set()
        await release_callback.wait()
        completed.append(sequence)

    client = JackeryMqttPushClient(hass, message_callback=_callback)
    client._handle_message("device/property", b'{"body":{"seq":1}}')
    await callback_started.wait()
    delivery = client._message_delivery_task
    assert delivery is not None

    delivery.cancel()
    await asyncio.sleep(0)
    release_callback.set()
    await asyncio.wait_for(client.async_wait_message_queue_idle(), timeout=1.0)

    assert entered == [1]
    assert completed == [1]


async def test_local_mqtt_delivery_cancellation_does_not_repeat_sink(
    hass: HomeAssistant,
) -> None:
    """Cancelling the delivery owner cannot cancel or replay its sink."""
    entered: list[int] = []
    completed: list[int] = []
    sink_started = asyncio.Event()
    release_sink = asyncio.Event()

    async def _sink(
        _topic: str,
        data: dict[str, Any] | None,
        _raw: bytes,
    ) -> bool:
        assert data is not None
        sequence = int(data["seq"])
        entered.append(sequence)
        sink_started.set()
        await release_sink.wait()
        completed.append(sequence)
        return True

    client = JackeryLocalMqttClient(hass, sink=_sink)
    client._async_message_received(
        MagicMock(
            topic="hb/device/example/event",
            payload=b'{"seq":1}',
            retain=False,
        )
    )
    await sink_started.wait()
    delivery = client._message_delivery_task
    assert delivery is not None

    delivery.cancel()
    await asyncio.sleep(0)
    release_sink.set()
    await asyncio.wait_for(client.async_wait_message_queue_idle(), timeout=1.0)

    assert entered == [1]
    assert completed == [1]


async def test_cloud_callback_cancelled_error_is_not_retried(
    hass: HomeAssistant,
) -> None:
    """A callback-originated cancellation is visible and cannot hot-loop."""
    calls = 0

    async def _callback(_topic: str, _data: dict[str, Any]) -> None:
        nonlocal calls
        await asyncio.sleep(0)
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError

    client = JackeryMqttPushClient(hass, message_callback=_callback)
    client._handle_message("device/property", b'{"body":{"seq":1}}')
    await asyncio.wait_for(client.async_wait_message_queue_idle(), timeout=1.0)

    assert calls == 1
    assert client.diagnostics_snapshot()["message_handler_errors"] == 1


async def test_local_sink_cancelled_error_is_not_retried(
    hass: HomeAssistant,
) -> None:
    """A sink-originated cancellation is visible and cannot hot-loop."""
    calls = 0

    async def _sink(
        _topic: str,
        _data: dict[str, Any] | None,
        _raw: bytes,
    ) -> bool:
        nonlocal calls
        await asyncio.sleep(0)
        calls += 1
        if calls == 1:
            raise asyncio.CancelledError
        return True

    client = JackeryLocalMqttClient(hass, sink=_sink)
    client._async_message_received(
        MagicMock(topic="hb/device/example/event", payload=b"{}", retain=False)
    )
    await asyncio.wait_for(client.async_wait_message_queue_idle(), timeout=1.0)

    assert calls == 1
    diagnostics = client.diagnostics_snapshot()
    assert diagnostics["sink_errors"] == 1
    assert diagnostics["messages_dropped"] == 1


async def test_cloud_stop_timeout_reports_real_accepted_backlog(
    hass: HomeAssistant,
    monkeypatch: Any,
) -> None:
    """A runner timeout reports queued plus in-flight accepted frames."""
    callback_started = asyncio.Event()
    release_callback = asyncio.Event()
    never_connected = asyncio.Event()

    async def _callback(_topic: str, _data: dict[str, Any]) -> None:
        callback_started.set()
        await release_callback.wait()

    monkeypatch.setattr(mqtt_push_module, "_MQTT_STOP_TIMEOUT_SEC", 0.01)
    client = JackeryMqttPushClient(hass, message_callback=_callback)
    runner = asyncio.create_task(never_connected.wait())
    client._runner_task = runner
    client._handle_message("device/property", b'{"body":{"seq":1}}')
    client._handle_message("device/property", b'{"body":{"seq":2}}')
    await callback_started.wait()

    try:
        with pytest.raises(RuntimeError, match=r"messages=2\)"):
            await client.async_stop()
    finally:
        release_callback.set()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner
        client._runner_task = None
        await client.async_wait_message_queue_idle()


async def test_cloud_mqtt_rejects_new_ingress_after_stop(
    hass: HomeAssistant,
) -> None:
    """The generation-less helper cannot cross the closed ingress boundary."""
    delivered: list[int] = []

    async def _callback(_topic: str, data: dict[str, Any]) -> None:
        await asyncio.sleep(0)
        delivered.append(int(data["body"]["seq"]))

    client = JackeryMqttPushClient(hass, message_callback=_callback)
    await client.async_stop()
    before = client.diagnostics_snapshot()

    client._handle_message("device/property", b'{"body":{"seq":1}}')
    await asyncio.sleep(0)

    after = client.diagnostics_snapshot()
    assert delivered == []
    assert after["messages_seen"] == before["messages_seen"]
    assert after["message_queue_depth"] == 0
