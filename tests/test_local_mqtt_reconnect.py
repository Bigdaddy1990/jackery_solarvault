"""White-box lifecycle tests for the direct local MQTT subscriber."""

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from aiomqtt import MqttError
import pytest

from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)

_FAILURE_ATTEMPTS = 7
_RESET_ATTEMPTS = 3


def _client(topic_filter: str = "hb/device/#") -> JackeryLocalMqttClient:
    """Build a client without opening a broker connection."""
    return JackeryLocalMqttClient(
        MagicMock(),
        host="192.0.2.10",
        port=1883,
        username=None,
        password=None,
        client_id="test-client",
        sink=None,
        topic_filter=topic_filter,
    )


@pytest.mark.asyncio()
async def test_runner_retries_and_caps_exponential_delay() -> None:
    """Repeated setup failures retry without a CPU loop or unbounded delay."""
    client = _client()
    attempts = 0
    delays: list[float] = []

    async def _session() -> bool:
        nonlocal attempts
        await asyncio.sleep(0)
        attempts += 1
        return False

    async def _sleep(delay: float) -> None:
        await asyncio.sleep(0)
        delays.append(delay)
        if len(delays) == _FAILURE_ATTEMPTS:
            raise asyncio.CancelledError

    cast("Any", client)._async_run_session = _session  # ruff: ignore[private-member-access]
    cast("Any", client)._async_reconnect_sleep = _sleep  # ruff: ignore[private-member-access]

    with pytest.raises(asyncio.CancelledError):
        await client._async_run_forever()  # ruff: ignore[private-member-access]

    assert attempts == len(delays)
    assert delays == [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 60.0]


@pytest.mark.asyncio()
async def test_successful_session_resets_reconnect_delay() -> None:
    """A previously connected session restarts the retry ladder at five seconds."""
    client = _client()
    outcomes = iter((False, False, True))
    delays: list[float] = []

    async def _session() -> bool:
        await asyncio.sleep(0)
        return next(outcomes)

    async def _sleep(delay: float) -> None:
        await asyncio.sleep(0)
        delays.append(delay)
        if len(delays) == _RESET_ATTEMPTS:
            raise asyncio.CancelledError

    cast("Any", client)._async_run_session = _session  # ruff: ignore[private-member-access]
    cast("Any", client)._async_reconnect_sleep = _sleep  # ruff: ignore[private-member-access]

    with pytest.raises(asyncio.CancelledError):
        await client._async_run_forever()  # ruff: ignore[private-member-access]

    assert delays == [5.0, 10.0, 5.0]


def test_async_start_owns_the_reconnecting_runner() -> None:
    """Startup schedules the persistent runner rather than a one-shot session."""
    source_name = JackeryLocalMqttClient.async_start.__code__.co_names

    assert "_async_run_forever" in source_name


@pytest.mark.asyncio()
async def test_broker_refused_subscription_is_not_reported_as_healthy() -> None:
    """A SUBACK failure code must abort the session instead of looking fine.

    ``aiomqtt.subscribe`` returns broker refusals instead of raising, so an
    ACL-denied filter previously left ``connected``/``subscribed`` true while
    no frame was ever delivered — not even the client's own publishes.
    """
    client = _client("homeassistant/#")
    subscribe = AsyncMock(return_value=(0x80,))
    broker = cast("Any", MagicMock())
    broker.subscribe = subscribe

    with pytest.raises(MqttError, match="refused the subscription"):
        await client._async_consume_session(broker, ["homeassistant/#"])  # ruff: ignore[private-member-access]

    assert client._subscription_active is False  # ruff: ignore[private-member-access]
    assert client._connected is False  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_broker_acl_refusal_marks_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SUBACK refusal is a configuration error, not a transient failure.

    The session reports ``False`` with the configuration flag set so the
    reconnect supervisor stops instead of retrying an ACL denial forever.
    """
    client = _client("homeassistant/#")
    broker = MagicMock()
    broker.__aenter__ = AsyncMock(
        side_effect=MqttError("broker refused the subscription to 'x'")
    )
    broker.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.MqttClient",
        MagicMock(return_value=broker),
    )

    connected = await client._async_run_session()  # ruff: ignore[private-member-access]

    assert connected is False
    assert client._configuration_error is True  # ruff: ignore[private-member-access]
    assert str(client._last_error).startswith("CONFIG_ERROR")  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_configuration_error_breaks_reconnect_loop() -> None:
    """The supervisor stops after a session flagged a configuration error."""
    client = _client()
    client._configuration_error = True  # ruff: ignore[private-member-access]
    calls = 0

    async def _session() -> bool:
        nonlocal calls
        await asyncio.sleep(0)
        calls += 1
        return False

    cast("Any", client)._async_run_session = _session  # ruff: ignore[private-member-access]

    await client._async_run_forever()  # ruff: ignore[private-member-access]

    assert calls == 1


@pytest.mark.asyncio()
async def test_granted_subscription_starts_the_session() -> None:
    """A normal SUBACK (granted QoS 0) keeps the session running."""
    client = _client("homeassistant/#")
    broker = cast("Any", MagicMock())
    broker.subscribe = AsyncMock(return_value=(0,))
    broker.messages = _EmptyMessages()
    cast("Any", client)._schedule_snapshot_request = MagicMock()  # ruff: ignore[private-member-access]
    cast("Any", client)._ensure_periodic_snapshot = MagicMock()  # ruff: ignore[private-member-access]

    await client._async_consume_session(broker, ["homeassistant/#"])  # ruff: ignore[private-member-access]

    assert client._subscription_active is True  # ruff: ignore[private-member-access]


class _EmptyMessages:
    """Async iterator standing in for an idle broker message stream."""

    def __aiter__(self) -> _EmptyMessages:
        return self

    async def __anext__(self) -> object:
        raise StopAsyncIteration


@pytest.mark.parametrize("topic", ["solarvault", "hb/device/device-1/event"])
def test_concrete_local_topic_is_not_silently_broadened(topic: str) -> None:
    """A concrete user filter remains an exact MQTT subscription."""
    client = _client(topic)

    assert client._topic_filters == (topic,)  # ruff: ignore[private-member-access]


@pytest.mark.parametrize("topic", ["homeassistant", "homeassistant/#"])
def test_discovery_prefix_also_receives_official_jackery_device_topics(
    topic: str,
) -> None:
    """Discovery traffic must not be the only subscribed protocol tree."""
    client = _client(topic)
    assert client._topic_filters == ("homeassistant/#", "hb/device/#")


def test_explicit_wildcard_local_topic_is_not_broadened() -> None:
    """An explicit wildcard remains exactly the user's configured scope."""
    client = _client("hb/device/+/status")

    assert client._topic_filters == ("hb/device/+/status",)  # ruff: ignore[private-member-access]
