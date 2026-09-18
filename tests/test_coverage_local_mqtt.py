"""Tests for local MQTT client helpers, markers, topic matching, and message handling."""  # ruff: ignore[line-too-long]

import asyncio
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import pytest

from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
    _local_mqtt_client,  # ruff: ignore[import-private-name]
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.mark.asyncio()
async def test_local_mqtt_client_initialization_and_diagnostics(  # ruff: ignore[unused-async]
    hass: HomeAssistant,
) -> None:
    """Test client initialization, properties, and diagnostic dictionary output."""
    client = JackeryLocalMqttClient(
        hass,
        topic_filter="jackery/#",
    )

    assert client.is_connected is False
    assert client.is_started is False

    diagnostics = client.diagnostics_snapshot()
    assert diagnostics["transport"] == "direct_mqtt"
    assert diagnostics["library"] == "aiomqtt"
    assert diagnostics["topic_filter"] == "**REDACTED**"
    assert diagnostics["configured_target"]["host"] == "**REDACTED**"
    assert diagnostics["subscribed"] is False
    assert diagnostics["connected"] is False
    rendered = repr(diagnostics)
    assert "jackery/#" not in rendered


def test_local_mqtt_configuration_matching(hass: HomeAssistant) -> None:
    """The broker-selected topic is the receiver's complete configuration."""
    client = JackeryLocalMqttClient(
        hass,
        host="broker.local",
        port=1884,
        username="user",
        password="secret",
        topic_filter="jackery/+/telemetry",
        qos=1,
    )

    assert client.matches_configuration(
        host="broker.local",
        port=1884,
        username="user",
        password="secret",
        topic_filter="jackery/+/telemetry",
        qos=1,
    )
    assert not client.matches_configuration(
        host="other.local",
        port=1884,
        username="user",
        password="secret",
        topic_filter="jackery/+/telemetry",
        qos=1,
    )


@pytest.mark.asyncio()
async def test_local_mqtt_message_handling(hass: HomeAssistant) -> None:
    """A bounded device topic forwards known and future payload fields."""
    forwarded: list[tuple[str, dict[str, Any] | None, bytes]] = []

    async def mock_sink(  # ruff: ignore[unused-async]
        topic: str,
        data: dict[str, Any] | None,
        raw: bytes,
    ) -> bool | None:
        forwarded.append((topic, data, raw))
        return None

    client = JackeryLocalMqttClient(
        hass,
        sink=mock_sink,
        topic_filter="jackery/#",
    )

    # A bounded, explicitly configured device topic is the routing boundary.
    # Unknown fields must reach the shared decoder so new firmware payloads are
    # not silently lost merely because their keys are not in a static marker set.
    await client._handle_message(  # ruff: ignore[private-member-access]
        "jackery/device1",
        b'{"temperature": 25}',
    )
    diag = client.diagnostics_snapshot()
    assert diag["messages_forwarded"] == 1
    assert len(forwarded) == 1
    assert forwarded[0][1] == {"temperature": 25}

    # Known Jackery fields follow the same independent async path.
    valid_payload = b'{"devSn": "12345", "batSoc": 95}'
    await client._handle_message(  # ruff: ignore[private-member-access]
        "jackery/device1",
        valid_payload,
    )
    diag = client.diagnostics_snapshot()
    assert diag["messages_forwarded"] == 2  # ruff: ignore[magic-value-comparison]
    assert len(forwarded) == 2  # ruff: ignore[magic-value-comparison]
    assert forwarded[1][1] == {"devSn": "12345", "batSoc": 95}

    # Oversized frames are rejected at the transport boundary before JSON
    # decoding so a broker cannot force unbounded memory/CPU work.
    large_payload = b'{"batSoc": 100, "extra": "' + b"A" * (130 * 1024) + b'"}'
    await client._handle_message(  # ruff: ignore[private-member-access]
        "jackery/device1",
        large_payload,
    )
    diag = client.diagnostics_snapshot()
    assert diag["messages_dropped"] == 1
    assert diag["messages_oversized"] == 1
    assert diag["messages_forwarded"] == 2  # ruff: ignore[magic-value-comparison]
    assert len(forwarded) == 2  # ruff: ignore[magic-value-comparison]


@pytest.mark.asyncio()
async def test_local_mqtt_start_stop(hass: HomeAssistant) -> None:
    """Start and stop the direct broker reconnect supervisor."""
    client = JackeryLocalMqttClient(
        hass,
        host="broker.local",
        topic_filter="jackery/device/#",
    )

    async def _wait_until_cancelled() -> None:
        client._connected_event.set()  # ruff: ignore[private-member-access]
        await asyncio.Event().wait()

    with patch.object(client, "_async_run_forever", _wait_until_cancelled):
        await client.async_start()

    assert client.is_started is True
    assert client.diagnostics_snapshot()["reconnect_supervisor_active"] is True

    await client.async_stop()

    assert client.is_started is False
    assert client.is_connected is False


def test_local_mqtt_client_lookup_helper(hass: HomeAssistant) -> None:
    """Test _local_mqtt_client helper function."""

    class DummyEntry:
        entry_id = "test_entry_id"
        runtime_data: object | None = None

    entry = DummyEntry()
    assert _local_mqtt_client(hass, cast("Any", entry)) is None

    client = JackeryLocalMqttClient(
        hass,
        topic_filter="jackery/#",
    )

    class DummyCoordinator:
        local_mqtt_client = client

    entry.runtime_data = DummyCoordinator()
    with patch(
        "custom_components.jackery_solarvault.coordinator.JackerySolarVaultCoordinator",
        DummyCoordinator,
    ):
        assert _local_mqtt_client(hass, cast("Any", entry)) is client
