"""Lifecycle regression tests for the direct local MQTT subscriber."""

import asyncio
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)

_FAILURE_ATTEMPTS = 7
_RESET_ATTEMPTS = 3


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

    cast("Any", client)._async_run_session = _session
    cast("Any", client)._async_reconnect_sleep = _sleep

    with pytest.raises(asyncio.CancelledError):
        await client._async_run_forever()

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

    cast("Any", client)._async_run_session = _session
    cast("Any", client)._async_reconnect_sleep = _sleep

    with pytest.raises(asyncio.CancelledError):
        await client._async_run_forever()

    assert delays == [5.0, 10.0, 5.0]


def test_async_start_owns_the_reconnecting_runner() -> None:
    """Startup schedules the persistent runner rather than a one-shot session."""
    source_name = JackeryLocalMqttClient.async_start.__code__.co_names

    assert "_async_run_forever" in source_name


def test_concrete_local_topic_subscribes_to_exact_and_descendant_topics() -> None:
    """The device may publish below its configured local topic prefix."""
    client = _client()

    assert client._topic_filters == ("homeassistant", "homeassistant/#")
    assert any(
        client._topic_matches(topic_filter, "homeassistant")
        for topic_filter in client._topic_filters
    )
    assert any(
        client._topic_matches(topic_filter, "homeassistant/jackery/live")
        for topic_filter in client._topic_filters
    )


def test_explicit_wildcard_local_topic_is_not_broadened() -> None:
    """An explicit wildcard remains exactly the user's configured scope."""
    assert JackeryLocalMqttClient._expanded_topic_filters("jackery/+/state") == (
        "jackery/+/state",
    )
