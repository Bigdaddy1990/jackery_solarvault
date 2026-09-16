"""Regression tests for less common local MQTT adapter branches."""

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
    _local_mqtt_client,
)
from custom_components.jackery_solarvault.const import (
    DOMAIN,
    LOCAL_MQTT_MAX_PAYLOAD_BYTES,
    REDACTED_VALUE,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def test_constructor_and_diagnostics_use_direct_broker_transport(
    hass: HomeAssistant,
) -> None:
    """The direct client matches and redacts its complete broker configuration."""
    client = JackeryLocalMqttClient(
        hass,
        host="192.0.2.10",
        port=1884,
        username="user",
        password="secret",
        topic_filter="jackery/device/#",
        qos=2,
    )

    assert client.is_connected is False
    assert client.is_started is False
    configuration = {
        "host": "192.0.2.10",
        "port": 1884,
        "username": "user",
        "password": "secret",
        "topic_filter": "jackery/device/#",
        "qos": 2,
    }
    assert client.matches_configuration(**configuration)
    assert not client.matches_configuration(**(configuration | {"host": "other"}))
    redacted = client.diagnostics_snapshot()
    plain = client.diagnostics_snapshot(redact=False)
    assert redacted["transport"] == "direct_mqtt"
    assert redacted["configured_target"]["host"] == REDACTED_VALUE
    assert plain["configured_target"] == {"host": "192.0.2.10", "port": 1884}
    assert redacted["topic_filter"] == REDACTED_VALUE
    assert plain["topic_filter"] == "jackery/device/#"
    assert plain["qos"] == 2
    assert plain["broker_connected"] is plain["connected"]


def test_coordinator_reports_consistent_local_mqtt_connection(
    hass: HomeAssistant,
) -> None:
    """Coordinator diagnostics preserve connection and filtered-message counters."""
    client = JackeryLocalMqttClient(hass)
    client._connected = True
    client._messages_filtered = 7
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._local_mqtt_client = client
    coordinator._local_mqtt_device_traffic_observed = False

    observations = coordinator.local_mqtt_observations()

    assert observations["connected"] is True
    assert observations["broker_connected"] is True
    assert observations["messages_filtered"] == 7


@pytest.mark.parametrize("qos", [-1, 3])
def test_constructor_rejects_invalid_qos(
    hass: HomeAssistant,
    qos: int,
) -> None:
    """Only MQTT QoS levels supported by HA are accepted."""
    with pytest.raises(ValueError, match="QoS"):
        JackeryLocalMqttClient(hass, qos=qos)  # type: ignore[arg-type]


async def test_message_without_sink_is_counted_as_dropped(
    hass: HomeAssistant,
) -> None:
    """Missing shared ingest cannot be reported as a forwarded frame."""
    client = JackeryLocalMqttClient(hass, topic_filter="jackery/#")

    await client._handle_message("jackery/device", b"{}")

    diagnostics = client.diagnostics_snapshot(redact=False)
    assert diagnostics["messages_received"] == 1
    assert diagnostics["messages_dropped"] == 1
    assert diagnostics["messages_forwarded"] == 0


async def test_sink_rejection_and_failure_are_distinguished(
    hass: HomeAssistant,
) -> None:
    """Semantic rejection and sink exceptions keep separate diagnostics."""
    rejecting_sink = AsyncMock(return_value=False)
    rejected = JackeryLocalMqttClient(hass, sink=rejecting_sink, topic_filter="#")
    await rejected._handle_message("foreign/topic", b'{"id": 1}')
    rejected_diagnostics = rejected.diagnostics_snapshot(redact=False)
    assert rejected_diagnostics["messages_rejected_by_sink"] == 1
    assert rejected_diagnostics["messages_dropped"] == 1

    failing_sink = AsyncMock(side_effect=RuntimeError("bad frame"))
    failed = JackeryLocalMqttClient(hass, sink=failing_sink, topic_filter="#")
    await failed._handle_message("jackery/topic", b"opaque")
    failed_diagnostics = failed.diagnostics_snapshot(redact=False)
    assert failed_diagnostics["sink_errors"] == 1
    assert failed_diagnostics["last_sink_error"] == "RuntimeError: bad frame"
    assert failed_diagnostics["messages_dropped"] == 1


async def test_oversized_is_dropped_but_retained_payload_reaches_sink(
    hass: HomeAssistant,
) -> None:
    """Broker-selected retained telemetry follows the same no-drop FIFO."""
    sink = AsyncMock(return_value=True)
    client = JackeryLocalMqttClient(hass, sink=sink, topic_filter="#")

    await client._handle_message(
        "jackery/oversized",
        b"x" * (LOCAL_MQTT_MAX_PAYLOAD_BYTES + 1),
    )
    retained = MagicMock(retain=True, topic="jackery/retained", payload=b"{}")

    async def messages():
        await asyncio.sleep(0)
        yield retained

    broker = MagicMock(messages=messages())
    broker.subscribe = AsyncMock()
    await client._async_consume_session(broker, ["#"])
    await client.async_wait_message_queue_idle()

    sink.assert_awaited_once_with("jackery/retained", {}, b"{}")
    diagnostics = client.diagnostics_snapshot(redact=False)
    assert diagnostics["payload_too_large_count"] == 1
    assert diagnostics["retained_messages_dropped"] == 0
    assert diagnostics["messages_dropped"] == 1
    assert diagnostics["messages_forwarded"] == 1


async def test_stop_cancels_direct_broker_reconnect_supervisor(
    hass: HomeAssistant,
) -> None:
    """Entry unload cancels the sole direct-broker reconnect supervisor."""
    client = JackeryLocalMqttClient(hass)
    runner_started = asyncio.Event()

    async def runner() -> None:
        runner_started.set()
        await asyncio.Event().wait()

    runner_task = asyncio.create_task(runner())
    client._runner_task = runner_task
    await runner_started.wait()

    await client.async_stop()

    assert runner_task.cancelled()
    assert client._runner_task is None
    assert client.is_connected is False


def test_local_mqtt_client_prefers_runtime_then_entry_bucket(
    hass: HomeAssistant,
) -> None:
    """Lookup follows the coordinator-owned runtime before the HA data fallback."""
    runtime_client = JackeryLocalMqttClient(hass, topic_filter="runtime/#")
    bucket_client = JackeryLocalMqttClient(hass, topic_filter="bucket/#")
    entry = MagicMock(entry_id="test-entry")
    entry.runtime_data = MagicMock(local_mqtt_client=runtime_client)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "local_mqtt_client": bucket_client,
    }

    assert _local_mqtt_client(hass, entry) is runtime_client

    entry.runtime_data.local_mqtt_client = None
    assert _local_mqtt_client(hass, entry) is bucket_client

    hass.data[DOMAIN][entry.entry_id]["local_mqtt_client"] = "not-a-client"
    assert _local_mqtt_client(hass, entry) is None


def test_utc_timestamp_is_timezone_aware(hass: HomeAssistant) -> None:
    """Diagnostics timestamps are ISO-8601 UTC values."""
    client = JackeryLocalMqttClient(hass)

    value = client._utc_now_iso()

    assert "T" in value
    assert value.endswith("+00:00")
