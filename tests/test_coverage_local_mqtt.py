"""Tests for local MQTT client helpers, markers, topic matching, and message handling."""

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
    _local_mqtt_client,  # ruff: ignore[import-private-name]
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.mark.asyncio
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
    assert diagnostics["transport"] == "homeassistant.components.mqtt"
    assert diagnostics["library"] == "homeassistant.components.mqtt"
    assert diagnostics["topic_filter"] == "**REDACTED**"
    assert diagnostics["mqtt_integration_available"] is False
    assert diagnostics["subscribed"] is False
    assert diagnostics["connected"] is False
    rendered = repr(diagnostics)
    assert "jackery/#" not in rendered


def test_local_mqtt_configuration_matching(hass: HomeAssistant) -> None:
    """The broker-selected topic is the receiver's complete configuration."""
    client = JackeryLocalMqttClient(
        hass,
        topic_filter="jackery/+/telemetry",
    )

    assert client.matches_configuration(("jackery/+/telemetry",))
    assert not client.matches_configuration(("jackery/#",))


@pytest.mark.asyncio
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
    assert diag["messages_forwarded"] == 2
    assert len(forwarded) == 2
    assert forwarded[1][1] == {"devSn": "12345", "batSoc": 95}

    # Oversized frames are counted for diagnostics but never filtered from the
    # broker-selected stream; semantic validation belongs to shared ingest.
    large_payload = b'{"batSoc": 100, "extra": "' + b"A" * (130 * 1024) + b'"}'
    await client._handle_message(  # ruff: ignore[private-member-access]
        "jackery/device1",
        large_payload,
    )
    diag = client.diagnostics_snapshot()
    assert diag["messages_dropped"] == 0
    assert diag["messages_oversized"] == 1
    assert diag["messages_forwarded"] == 3
    assert forwarded[2][2] == large_payload


@pytest.mark.asyncio
async def test_local_mqtt_start_stop(hass: HomeAssistant) -> None:
    """Start and stop own only HA MQTT subscriptions, never a broker client."""
    client = JackeryLocalMqttClient(
        hass,
        topic_filter="jackery/device/#",
    )
    unsubscribe = MagicMock()
    unsubscribe_status = MagicMock()

    with (
        patch(
            "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_wait_for_mqtt_client",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_subscribe",
            new=AsyncMock(return_value=unsubscribe),
        ) as async_subscribe,
        patch(
            "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_subscribe_connection_status",
            return_value=unsubscribe_status,
        ),
        patch(
            "custom_components.jackery_solarvault.client.local_mqtt.mqtt.is_connected",
            return_value=True,
        ),
    ):
        await client.async_start()

    async_subscribe.assert_awaited_once()
    subscribe_call = async_subscribe.await_args
    assert subscribe_call is not None
    assert subscribe_call.args[1] == "jackery/device/#"
    assert subscribe_call.kwargs == {"qos": 0, "encoding": None}
    assert client.is_started is True
    assert client.is_connected is True

    await client.async_stop()

    unsubscribe.assert_called_once_with()
    unsubscribe_status.assert_called_once_with()
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
