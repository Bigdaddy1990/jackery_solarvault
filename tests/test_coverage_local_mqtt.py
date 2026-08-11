"""Tests for local MQTT client helpers, markers, topic matching, and message handling."""

from typing import TYPE_CHECKING, Any

import pytest

from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
    _local_mqtt_client,  # ruff: ignore[import-private-name]
)
from custom_components.jackery_solarvault.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.mark.asyncio
async def test_local_mqtt_client_initialization_and_diagnostics(  # ruff: ignore[unused-async]
    hass: HomeAssistant,
) -> None:
    """Test client initialization, properties, and diagnostic dictionary output."""
    client = JackeryLocalMqttClient(
        hass,
        host="192.168.1.100",
        port=1883,
        username="admin",
        password="secret_password",
        client_id="jackery_local_test",
        topic_filter="jackery/#",
    )

    assert client.is_connected is False
    assert client.is_started is False

    diagnostics = client.diagnostics_snapshot()
    assert diagnostics["configured_target"]["host"] == "**REDACTED**"
    assert diagnostics["configured_target"]["port"] == "**REDACTED**"
    assert diagnostics["topic_filter"] == "**REDACTED**"
    assert diagnostics["connected"] is False
    rendered = repr(diagnostics)
    assert "192.168.1.100" not in rendered
    assert "secret_password" not in rendered
    assert "jackery/#" not in rendered


def test_local_mqtt_topic_matching(hass: HomeAssistant) -> None:
    """Test topic matching rules (wildcards +, #, exact matches)."""
    client = JackeryLocalMqttClient(
        hass,
        host="localhost",
        port=1883,
        username=None,
        password=None,
        client_id="test",
        topic_filter="jackery/+/telemetry",
    )

    assert (
        client._topic_matches("jackery/+/telemetry", "jackery/e2000/telemetry") is True  # ruff: ignore[private-member-access]
    )
    assert client._topic_matches("jackery/+/telemetry", "jackery/e2000/status") is False  # ruff: ignore[private-member-access]
    assert client._topic_matches("jackery/#", "jackery/sub/topic") is True  # ruff: ignore[private-member-access]
    assert client._topic_matches("jackery/#", "other/topic") is False  # ruff: ignore[private-member-access]


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
        host="localhost",
        port=1883,
        username=None,
        password=None,
        client_id="test",
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
    assert diag["messages_ignored_foreign"] == 0
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

    # Oversized payloads remain bounded before decoding.
    large_payload = b'{"batSoc": 100, "extra": "' + b"A" * (130 * 1024) + b'"}'
    await client._handle_message(  # ruff: ignore[private-member-access]
        "jackery/device1",
        large_payload,
    )
    diag = client.diagnostics_snapshot()
    assert diag["messages_dropped"] > 0


@pytest.mark.asyncio
async def test_local_mqtt_start_stop(hass: HomeAssistant) -> None:
    """Test start and stop lifecycle without real connection."""
    client = JackeryLocalMqttClient(
        hass,
        host="127.0.0.1",
        port=18883,
        username=None,
        password=None,
        client_id="test_start_stop",
    )

    # Stop when not started is safe
    await client.async_stop()
    assert client.is_connected is False


def test_local_mqtt_client_lookup_helper(hass: HomeAssistant) -> None:
    """Test _local_mqtt_client helper function."""

    class DummyEntry:
        entry_id = "test_entry_id"

    entry = DummyEntry()
    assert _local_mqtt_client(hass, entry) is None

    client = JackeryLocalMqttClient(
        hass,
        host="127.0.0.1",
        port=1883,
        username=None,
        password=None,
        client_id="test_lookup",
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"local_mqtt_client": client}
    assert _local_mqtt_client(hass, entry) is client
