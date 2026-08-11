"""Lifecycle regression tests for the direct local MQTT subscriber."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_FAILURE_ATTEMPTS = 7
_RESET_ATTEMPTS = 3
_FOREIGN_FRAME_COUNT = 10


def _client() -> JackeryLocalMqttClient:
    """Build a client without opening a broker connection."""
    return JackeryLocalMqttClient(
        MagicMock(),
        host="192.0.2.10",
        port=1883,
        username=None,
        password=None,
        client_id="test-client",
        sink=None,
        topic_filter="homeassistant",
    )


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.parametrize(
    "configured_topic",
    ("homeassistant", "jackery/+/state"),  # ruff: ignore[pytest-parametrize-values-wrong-type]
)
@pytest.mark.asyncio
async def test_direct_client_subscribes_once_to_exact_configured_topic(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    configured_topic: str,
) -> None:
    """The direct client owns one subscription and never broadens its filter."""
    subscriptions: list[tuple[str, int]] = []

    class _NoMessages:
        def __aiter__(self) -> _NoMessages:
            return self

        async def __anext__(self) -> Any:
            raise StopAsyncIteration

    class _BrokerClient:
        messages = _NoMessages()

        async def __aenter__(self) -> _BrokerClient:  # ruff: ignore[non-self-return-type]
            return self

        async def __aexit__(self, *_args: Any) -> None:  # ruff: ignore[bad-exit-annotation]
            return None

        async def subscribe(self, topic: str, *, qos: int) -> None:  # ruff: ignore[no-self-use]
            subscriptions.append((topic, qos))

    broker_client = _BrokerClient()
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.aiomqtt.Client",
        lambda **_kwargs: broker_client,
    )
    client = JackeryLocalMqttClient(
        hass,
        host="192.0.2.10",
        port=1883,
        username=None,
        password=None,
        client_id="single-topic",
        topic_filter=configured_topic,
    )

    assert await client._async_run_session() is True  # ruff: ignore[private-member-access]
    # The client expands concrete topics to both exact and descendant subscriptions
    if "+" in configured_topic or "#" in configured_topic:
        assert subscriptions == [(configured_topic, 0)]
    else:
        expected = [
            (configured_topic, 0),
            (f"{configured_topic.rstrip("/")}/#", 0),
        ]
        assert subscriptions == expected


def test_broad_filter_still_blocks_home_assistant_noise(
    hass: HomeAssistant,
) -> None:
    """A broad filter must not feed unrelated HA namespace traffic to the sink."""
    client = JackeryLocalMqttClient(
        hass,
        host="192.0.2.10",
        port=1883,
        username=None,
        password=None,
        client_id="broad-filter",
        topic_filter="#",
    )

    client._handle_message(  # ruff: ignore[private-member-access]
        "homeassistant/sensor/foreign/config",
        b'{"batSoc":80}',
    )

    diagnostics = client.diagnostics_snapshot()
    assert diagnostics["blocked_by_filter_count"] == 1
    assert diagnostics["messages_forwarded"] == 0


@pytest.mark.asyncio
async def test_foreign_frames_are_ignored_before_sink(  # noqa: RUF029, RUF105
    hass: HomeAssistant,
) -> None:
    """Foreign broker frames without Jackery markers are ignored and
    never reach the sink.
    """  # noqa: D205, RUF105
    sink_calls = 0

    async def _blocking_sink(  # noqa: RUF029, RUF105
        _topic: str,
        _data: dict[str, Any] | None,
        _raw: bytes,
    ) -> None:
        nonlocal sink_calls
        sink_calls += 1

    client = JackeryLocalMqttClient(
        hass,
        host="192.0.2.10",
        port=1883,
        username=None,
        password=None,
        client_id="bounded-sink",
        sink=_blocking_sink,
        topic_filter="homeassistant",
    )

    # Binary frames without Jackery markers should be ignored by the
    # foreign-traffic gate and never reach the sink.
    for index in range(_FOREIGN_FRAME_COUNT):
        client._handle_message("homeassistant", b"\xff" + bytes([index % 256]))  # ruff: ignore[private-member-access]

    assert (
        client.diagnostics_snapshot()["messages_ignored_foreign"]
        == _FOREIGN_FRAME_COUNT
    )
    assert client.diagnostics_snapshot()["messages_dropped"] == 0
    assert client.diagnostics_snapshot()["messages_forwarded"] == 0
    assert sink_calls == 0


def test_transient_initial_broker_refusal_is_debug_not_warning(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A normal broker startup race must not create a HA warning on repeated failures."""  # noqa: RUF105
    client = JackeryLocalMqttClient(
        hass,
        host="192.0.2.10",
        port=1883,
        username=None,
        password=None,
        client_id="startup-race",
        topic_filter="homeassistant",
    )

    # First failure logs at WARNING (no previous error to compare against)
    with caplog.at_level(
        logging.DEBUG,
        logger="custom_components.jackery_solarvault.client.local_mqtt",
    ):
        client._handle_disconnect_error("[Errno 111] Connection refused", False)  # ruff: ignore[private-member-access]

    assert "connect setup failed" in caplog.text
    assert any(record.levelno == logging.WARNING for record in caplog.records)
    sum(1 for r in caplog.records if r.levelno == logging.WARNING)

    # Second identical failure logs at DEBUG (same error as last_error)
    caplog.clear()
    with caplog.at_level(
        logging.DEBUG,
        logger="custom_components.jackery_solarvault.client.local_mqtt",
    ):
        client._handle_disconnect_error("[Errno 111] Connection refused", False)  # ruff: ignore[private-member-access]

    assert "connect setup failed" in caplog.text
    assert not any(record.levelno >= logging.WARNING for record in caplog.records)
    assert any(record.levelno == logging.DEBUG for record in caplog.records)
