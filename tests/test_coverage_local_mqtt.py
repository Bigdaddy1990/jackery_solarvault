"""Tests for local MQTT client helpers, markers, topic matching, and message handling."""

from typing import TYPE_CHECKING

import pytest

from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
    _local_mqtt_client,
    payload_has_jackery_marker,
)
from custom_components.jackery_solarvault.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def test_payload_has_jackery_marker() -> None:
    """Test payload marker detection across strings, bytes, and bytearrays."""
    assert payload_has_jackery_marker('{"batSoc": 80}') is True
    assert payload_has_jackery_marker(b'{"deviceSn": "12345"}') is True
    assert payload_has_jackery_marker(bytearray(b'{"gridInPw": 500}')) is True
    assert payload_has_jackery_marker('{"otherKey": "unrelated"}') is False
    assert payload_has_jackery_marker(b"raw non-json binary data") is False


@pytest.mark.asyncio()
async def test_local_mqtt_client_initialization_and_diagnostics(
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

    diag_redacted = client.diagnostics_snapshot(redact=True)
    assert diag_redacted["configured_target"]["host"] == "**REDACTED**"
    assert diag_redacted["connected"] is False

    diag_unredacted = client.diagnostics_snapshot(redact=False)
    assert diag_unredacted["configured_target"]["host"] == "192.168.1.100"
    assert diag_unredacted["configured_target"]["port"] == 1883


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
        client._topic_matches("jackery/+/telemetry", "jackery/e2000/telemetry") is True
    )
    assert client._topic_matches("jackery/+/telemetry", "jackery/e2000/status") is False
    assert client._topic_matches("jackery/#", "jackery/sub/topic") is True
    assert client._topic_matches("jackery/#", "other/topic") is False


@pytest.mark.asyncio()
async def test_local_mqtt_message_handling(hass: HomeAssistant) -> None:
    """Test message parsing, marker checks, and dropping logic."""
    forwarded = []

    async def mock_sink(topic: str, data: dict | None, raw: bytes) -> None:
        forwarded.append((topic, data, raw))

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

    # 1. Foreign message without marker -> ignored
    client._handle_message("jackery/device1", b'{"temperature": 25}')
    diag = client.diagnostics_snapshot()
    assert diag["messages_ignored_foreign"] == 1
    assert len(forwarded) == 0

    # 2. Message with Jackery marker -> forwarded
    valid_payload = b'{"devSn": "12345", "batSoc": 95}'
    client._handle_message("jackery/device1", valid_payload)
    await hass.async_block_till_done()
    diag = client.diagnostics_snapshot()
    assert diag["messages_forwarded"] == 1
    assert len(forwarded) == 1
    assert forwarded[0][1] == {"devSn": "12345", "batSoc": 95}

    # 3. Payload too large -> dropped
    large_payload = b'{"batSoc": 100, "extra": "' + b"A" * (130 * 1024) + b'"}'
    client._handle_message("jackery/device1", large_payload)
    diag = client.diagnostics_snapshot()
    assert diag["messages_dropped"] > 0


@pytest.mark.asyncio()
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
