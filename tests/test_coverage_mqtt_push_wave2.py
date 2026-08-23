"""Behavioral coverage for the independent Jackery Cloud-MQTT client."""

import asyncio
from collections.abc import AsyncIterator
import contextlib
import ssl
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Self, cast, override
from unittest.mock import AsyncMock

from aiomqtt import MqttError
import pytest

from custom_components.jackery_solarvault.client import mqtt_push
from custom_components.jackery_solarvault.client.mqtt_push import (
    JACKERY_MQTT_SUPPORTS_LWT,
    JACKERY_MQTT_SUPPORTS_RETAINED_PRESENCE,
    MQTT_MESSAGE_SPECS,
    JackeryMqttPushClient,
    JackeryMqttTransportError,
    MqttSessionState,
)
from custom_components.jackery_solarvault.const import (
    FIELD_BODY,
    MQTT_KEEPALIVE_SEC,
    MQTT_TOPIC_PREFIX,
    MQTT_TOPIC_SUFFIXES,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class _Messages:
    """Finite asynchronous broker-message stream."""

    def __init__(
        self,
        messages: list[object],
        finish_event: asyncio.Event | None = None,
    ) -> None:
        self._messages = iter(messages)
        self._finish_event = finish_event

    def __aiter__(self) -> AsyncIterator[object]:
        return self

    async def __anext__(self) -> object:
        try:
            return next(self._messages)
        except StopIteration as err:
            if self._finish_event is not None:
                await self._finish_event.wait()
            raise StopAsyncIteration from err


class _BrokerClient:
    """Minimal aiomqtt client double with observable broker operations."""

    def __init__(
        self,
        messages: list[object] | None = None,
        *,
        subscribe_error: MqttError | None = None,
        enter_error: MqttError | None = None,
        finish_event: asyncio.Event | None = None,
    ) -> None:
        self.messages = _Messages(messages or [], finish_event)
        self.subscribe_error = subscribe_error
        self.enter_error = enter_error
        self.subscriptions: list[tuple[str, int]] = []
        self.publishes: list[tuple[str, str, int, bool]] = []

    async def __aenter__(self) -> Self:
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def subscribe(self, topic: str, *, qos: int) -> None:
        self.subscriptions.append((topic, qos))
        if self.subscribe_error is not None:
            raise self.subscribe_error

    async def publish(
        self, topic: str, payload: str, *, qos: int, retain: bool
    ) -> None:
        self.publishes.append((topic, payload, qos, retain))


def _client(
    hass: HomeAssistant,
    message_callback: AsyncMock | None = None,
    *,
    connect_callback: AsyncMock | None = None,
    disconnect_callback: AsyncMock | None = None,
) -> JackeryMqttPushClient:
    """Build a Cloud-MQTT client with async callback doubles."""
    return JackeryMqttPushClient(
        hass,
        message_callback=message_callback or AsyncMock(),
        connect_callback=connect_callback,
        disconnect_callback=disconnect_callback,
    )


async def _run_owned_session(
    client: JackeryMqttPushClient,
    *,
    topics: tuple[str, ...],
) -> None:
    """Run one broker session while making its task the current owner."""
    generation = client._session_generation
    task = asyncio.create_task(
        client._async_run_session(
            client_id="cloud-client",
            username="cloud-user",
            password="cloud-password",
            ssl_context=ssl.create_default_context(),
            topics=topics,
            generation=generation,
        )
    )
    client._runner_task = task
    await task


async def test_cloud_session_subscribes_and_delivers_every_payload(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Cloud session subscribes all app topics and forwards a valid frame."""
    message_callback = AsyncMock()
    disconnect_callback = AsyncMock()
    client = _client(
        hass,
        message_callback,
        disconnect_callback=disconnect_callback,
    )
    frame = SimpleNamespace(topic="hb/app/user/device", payload=b'{"data":{"soc":73}}')
    finish_event = asyncio.Event()
    broker = _BrokerClient([frame], finish_event=finish_event)
    constructor_kwargs: dict[str, Any] = {}

    def _make_broker(**kwargs: Any) -> _BrokerClient:
        constructor_kwargs.update(kwargs)
        return broker

    monkeypatch.setattr(mqtt_push.aiomqtt, "Client", _make_broker)
    topics = tuple(
        f"{MQTT_TOPIC_PREFIX}/user/{suffix}" for suffix in MQTT_TOPIC_SUFFIXES
    )

    session = asyncio.create_task(_run_owned_session(client, topics=topics))
    for _ in range(10):
        if message_callback.await_count:
            break
        await asyncio.sleep(0)
    finish_event.set()
    await session
    await hass.async_block_till_done()

    assert broker.subscriptions == [(topic, 0) for topic in topics]
    assert constructor_kwargs["identifier"] == "cloud-client"
    assert constructor_kwargs["keepalive"] == MQTT_KEEPALIVE_SEC
    message_callback.assert_awaited_once_with(
        "hb/app/user/device",
        {"data": {"soc": 73}, FIELD_BODY: {"soc": 73}},
    )
    disconnect_callback.assert_awaited_once_with()
    assert client.diagnostics_snapshot()["messages_seen"] == 1


async def test_cloud_subscription_failure_is_reported_and_wakes_waiters(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed Cloud subscription records the topic and ends the session cleanly."""
    client = _client(hass)
    broker = _BrokerClient(subscribe_error=MqttError("denied"))
    monkeypatch.setattr(mqtt_push.aiomqtt, "Client", lambda **_kwargs: broker)

    await _run_owned_session(client, topics=("hb/app/user/device",))

    assert client.is_connected is False
    last_error = client.diagnostics_snapshot()["last_error"]
    assert last_error == (
        "disconnect: subscribe failed for hb/app/user/device: MqttError: **REDACTED**"
    )
    assert "denied" not in last_error
    assert client._connected_event.is_set()


async def test_cloud_connect_failure_is_reported_without_local_retry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broker-entry failure ends this Cloud session without an internal retry loop."""
    client = _client(hass)
    broker = _BrokerClient(enter_error=MqttError("network down"))
    calls = 0

    def _make_broker(**_kwargs: Any) -> _BrokerClient:
        nonlocal calls
        calls += 1
        return broker

    monkeypatch.setattr(mqtt_push.aiomqtt, "Client", _make_broker)

    await _run_owned_session(client, topics=("hb/app/user/device",))

    assert calls == 1
    last_error = client.diagnostics_snapshot()["last_error"]
    assert last_error == "connect failed: MqttError: **REDACTED**"
    assert "network down" not in last_error
    assert client._connected_event.is_set()


async def test_publish_uses_compact_unicode_json_and_tracks_success(
    hass: HomeAssistant,
) -> None:
    """Cloud publish preserves Unicode and records only the current session's write."""
    client = _client(hass)
    broker = _BrokerClient()
    client._client = cast("Any", broker)
    client._connected = True

    await client.async_publish_json(
        "hb/app/user/action",
        {"name": "Süd", "enabled": True},
        qos=1,
        retain=True,
    )

    assert broker.publishes == [
        ("hb/app/user/action", '{"name":"Süd","enabled":true}', 1, True)
    ]
    snapshot = client.diagnostics_snapshot()
    assert snapshot["last_published_topic"] == "hb/app/**REDACTED**/action"
    assert snapshot["last_publish_at"] is not None


async def test_publish_error_invalidates_only_current_cloud_session(
    hass: HomeAssistant,
) -> None:
    """A Cloud publish error marks that session disconnected and remains actionable."""

    class _FailingPublisher(_BrokerClient):
        @override
        async def publish(
            self, topic: str, payload: str, *, qos: int, retain: bool
        ) -> None:
            raise MqttError("socket lost")

    client = _client(hass)
    client._client = cast("Any", _FailingPublisher())
    client._connected = True
    client._connected_event.set()

    with pytest.raises(RuntimeError, match="MQTT publish failed: socket lost"):
        await client.async_publish_json("hb/app/user/action", {"cmd": 110})

    assert client.is_connected is False
    assert not client._connected_event.is_set()
    assert client.diagnostics_snapshot()["last_error"] == "publish failed: socket lost"


async def test_publish_rejects_session_generation_change_while_waiting(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed publish cannot leak into a replacement Cloud session."""
    client = _client(hass)

    async def _replace_session(timeout_sec: float) -> None:
        await asyncio.sleep(0)
        assert timeout_sec == pytest.approx(30.0)
        client._session_generation += 1

    monkeypatch.setattr(client, "_async_wait_connected", _replace_session)

    with pytest.raises(RuntimeError, match="ownership changed before publish"):
        await client.async_publish_json("hb/app/user/action", {"cmd": 110})


async def test_response_correlation_keeps_normal_ingest_callback(
    hass: HomeAssistant,
) -> None:
    """Getter correlation resolves its waiter without consuming the Cloud frame."""
    message_callback = AsyncMock()
    client = _client(hass, message_callback)
    waiter = asyncio.create_task(client._wait_for_response(42, 1.0))
    await asyncio.sleep(0)

    client._handle_message(
        "hb/app/user/action",
        '{"request_id":42,"body":{"soc":88}}',
    )
    response = await waiter
    await hass.async_block_till_done()

    assert response == {"request_id": 42, "body": {"soc": 88}}
    assert client.responses_correlated == 1
    message_callback.assert_awaited_once_with("hb/app/user/action", response)


async def test_response_correlation_accepts_app_envelope_id(
    hass: HomeAssistant,
) -> None:
    """App responses correlate via envelope ``id`` plus ``actionId``."""
    client = _client(hass)
    waiter = asyncio.create_task(
        client._wait_for_response(1776548805134, 1.0, expected_response_type=3047)
    )
    await asyncio.sleep(0)

    response = {
        "id": 1776548805134,
        "actionId": 3047,
        "body": {"enable": 1},
    }
    client._resolve_pending_response(response)

    assert await waiter == response
    assert client.responses_correlated == 1


async def test_response_correlation_normalizes_numeric_string_id(
    hass: HomeAssistant,
) -> None:
    """A JSON string ID still resolves the integer-keyed RPC waiter."""
    client = _client(hass)
    waiter = asyncio.create_task(
        client._wait_for_response(42, 1.0, expected_response_type=3047)
    )
    await asyncio.sleep(0)

    response = {"id": "42", "actionId": "3047", "body": {"enable": 1}}
    client._resolve_pending_response(response)

    assert await asyncio.wait_for(waiter, timeout=0.1) == response
    assert client.responses_correlated == 1


async def test_response_timeout_expires_and_removes_waiter(
    hass: HomeAssistant,
) -> None:
    """A missing Cloud response expires without leaving unbounded session state."""
    client = _client(hass)

    with pytest.raises(TimeoutError):
        await client._wait_for_response(7, 0.001)

    assert client.responses_expired == 1
    assert client._pending_responses == {}


async def test_rpc_disconnect_fails_waiter_with_transport_error(
    hass: HomeAssistant,
) -> None:
    """Disconnect completes every open RPC rather than leaking its future."""
    client = _client(hass)
    waiter = asyncio.create_task(client._wait_for_response(8))
    await asyncio.sleep(0)

    client._fail_pending_responses("connection lost")

    with pytest.raises(JackeryMqttTransportError, match="connection lost"):
        await waiter
    assert client._pending_responses == {}


async def test_late_or_wrong_kind_response_cannot_cross_session(
    hass: HomeAssistant,
) -> None:
    """RPC matching includes generation, request ID and response kind."""
    client = _client(hass)
    waiter = asyncio.create_task(
        client._wait_for_response(9, expected_response_type=3031)
    )
    await asyncio.sleep(0)

    client._resolve_pending_response({"request_id": 9, "actionId": 3032})
    assert not waiter.done()
    client._session_generation += 1
    client._resolve_pending_response({"request_id": 9, "actionId": 3031})
    assert not waiter.done()
    client._fail_pending_responses("new session")
    with pytest.raises(JackeryMqttTransportError):
        await waiter


def test_protocol_delivery_contract_disables_presence_features() -> None:
    """Jackery explicitly uses QoS 0 without retained presence or an LWT."""
    assert JACKERY_MQTT_SUPPORTS_LWT is False
    assert JACKERY_MQTT_SUPPORTS_RETAINED_PRESENCE is False
    assert all(spec.qos == 0 for spec in MQTT_MESSAGE_SPECS.values())
    assert all(spec.retain is False for spec in MQTT_MESSAGE_SPECS.values())


async def test_birth_once_per_generation_and_rebirth_next_generation(
    hass: HomeAssistant,
) -> None:
    """Birth succeeds once in each fully subscribed broker generation."""
    birth = AsyncMock()
    client = _client(hass)
    client._connected = True
    client._session_state = MqttSessionState.SUBSCRIBED

    client._schedule_birth_snapshot(birth, generation=0)
    client._schedule_birth_snapshot(birth, generation=0)
    await hass.async_block_till_done()
    assert birth.await_count == 1
    assert client.diagnostics_snapshot()["birth_publishes"] == 1

    client._session_generation = 1
    client._schedule_birth_snapshot(birth, generation=1)
    await hass.async_block_till_done()
    assert birth.await_count == 2
    assert client.diagnostics_snapshot()["session_state"] == "online"


async def test_stop_cancels_lifecycle_but_drains_messages_and_clears_cloud_state(
    hass: HomeAssistant,
) -> None:
    """Stopping Cloud MQTT cancels lifecycle work but drains accepted frames."""
    message_started = asyncio.Event()
    release_message = asyncio.Event()
    delivered: list[int] = []

    async def _message_callback(_topic: str, data: dict[str, Any]) -> None:
        message_started.set()
        await release_message.wait()
        delivered.append(int(data[FIELD_BODY]["seq"]))

    client = _client(hass, AsyncMock(side_effect=_message_callback))
    runner = asyncio.create_task(asyncio.sleep(60))
    lifecycle_task = asyncio.create_task(asyncio.sleep(60))
    client._runner_task = runner
    client._client = cast("Any", _BrokerClient())
    client._connected = True
    client._fingerprint = "secret-free-hash"
    client._lifecycle_tasks[lifecycle_task] = object()
    client._handle_message("device/property", b'{"body":{"seq":1}}')
    message_task = client._message_consumer_task
    assert message_task is not None
    await message_started.wait()

    stop_task = asyncio.create_task(client.async_stop())
    await asyncio.sleep(0)
    assert not stop_task.done()
    assert not message_task.cancelled()

    release_message.set()
    await stop_task

    await asyncio.sleep(0)
    assert runner.cancelled()
    assert message_task.done()
    assert not message_task.cancelled()
    assert lifecycle_task.cancelled()
    assert delivered == [1]
    assert client.is_started is False
    assert client.is_connected is False
    snapshot = client.diagnostics_snapshot()
    assert snapshot["topics"] == []
    assert snapshot["subscribed_topics"] == []

    for task in (runner, message_task, lifecycle_task):
        with contextlib.suppress(asyncio.CancelledError):
            await task
