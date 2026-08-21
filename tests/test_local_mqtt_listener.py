"""Behavioral tests for the Home Assistant-owned local MQTT listener."""

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import _async_start_local_mqtt
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)
from custom_components.jackery_solarvault.const import (
    CONF_LOCAL_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_local_mqtt_listener_disabled_by_option(
    hass: HomeAssistant,
) -> None:
    """A disabled entry does not create an MQTT adapter."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={CONF_LOCAL_MQTT_ENABLE: False},
        entry_id="local-mqtt-disabled",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    entry.runtime_data = coordinator

    with patch(
        "custom_components.jackery_solarvault.JackeryLocalMqttClient"
    ) as client_cls:
        await _async_start_local_mqtt(hass, entry, coordinator)

    client_cls.assert_not_called()
    coordinator.set_local_mqtt_client.assert_called_once_with(None)


async def test_local_mqtt_listener_uses_home_assistant_adapter(
    hass: HomeAssistant,
) -> None:
    """An enabled entry starts one HA-owned adapter and schedules device config."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.2.212",
        },
        entry_id="local-mqtt-enabled",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    entry.runtime_data = coordinator
    client = MagicMock()
    client.async_start = AsyncMock()

    with patch(
        "custom_components.jackery_solarvault.JackeryLocalMqttClient",
        return_value=client,
    ) as client_cls:
        await _async_start_local_mqtt(hass, entry, coordinator)

    client_cls.assert_called_once()
    assert "host" not in client_cls.call_args.kwargs
    assert "port" not in client_cls.call_args.kwargs
    client.async_start.assert_awaited_once()
    coordinator.set_local_mqtt_client.assert_called_once_with(client)
    coordinator.async_schedule_local_mqtt_device_config.assert_called_once_with()


async def test_listener_subscribes_once_to_configured_filter(
    hass: HomeAssistant,
    monkeypatch,
) -> None:
    """The adapter registers one binary HA MQTT subscription."""
    unsubscribe = MagicMock()
    unsubscribe_status = MagicMock()
    wait_for_client = AsyncMock(return_value=True)
    subscribe = AsyncMock(return_value=unsubscribe)
    subscribe_status = MagicMock(return_value=unsubscribe_status)
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_wait_for_mqtt_client",
        wait_for_client,
    )
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_subscribe",
        subscribe,
    )
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_subscribe_connection_status",
        subscribe_status,
    )
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.mqtt.is_connected",
        MagicMock(return_value=True),
    )
    client = JackeryLocalMqttClient(
        hass,
        topic_filter="jackery/device/#",
        qos=1,
    )

    await client.async_start()
    await client.async_start()

    wait_for_client.assert_awaited_once_with(hass)
    subscribe.assert_awaited_once_with(
        hass,
        "jackery/device/#",
        client._async_message_received,
        qos=1,
        encoding=None,
    )
    subscribe_status.assert_called_once_with(
        hass,
        client._async_connection_status_changed,
    )
    assert client.is_started is True
    assert client.is_connected is True

    await client.async_stop()
    unsubscribe.assert_called_once_with()
    unsubscribe_status.assert_called_once_with()


async def test_listener_cleans_status_subscription_when_message_subscribe_fails(
    hass: HomeAssistant,
    monkeypatch,
) -> None:
    """A partial HA subscription is released after a broker-side failure."""
    unsubscribe_status = MagicMock()
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_wait_for_mqtt_client",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_subscribe_connection_status",
        MagicMock(return_value=unsubscribe_status),
    )
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_subscribe",
        AsyncMock(side_effect=RuntimeError("broker down")),
    )
    client = JackeryLocalMqttClient(hass, topic_filter="jackery/device/#")

    assert await client._async_subscribe_once() is False

    unsubscribe_status.assert_called_once_with()
    assert client.is_started is False
    assert client.diagnostics_snapshot(redact=False)["last_error"] == (
        "RuntimeError: broker down"
    )


async def test_listener_forwards_json_and_raw_payloads(
    hass: HomeAssistant,
) -> None:
    """Broker-selected JSON and opaque frames both reach shared ingest."""
    received: list[tuple[str, dict[str, Any] | None, bytes]] = []

    async def sink(
        topic: str,
        data: dict[str, Any] | None,
        raw: bytes,
    ) -> bool:
        await asyncio.sleep(0)
        received.append((topic, data, raw))
        return True

    client = JackeryLocalMqttClient(hass, sink=sink, topic_filter="jackery/#")

    await client._handle_message("jackery/json", b'{"batSoc": 50}')
    await client._handle_message("jackery/raw", b"\xff\x00")
    await client._handle_message("jackery/array", b"[]")

    assert received == [
        ("jackery/json", {"batSoc": 50}, b'{"batSoc": 50}'),
        ("jackery/raw", None, b"\xff\x00"),
        ("jackery/array", None, b"[]"),
    ]
    diagnostics = client.diagnostics_snapshot(redact=False)
    assert diagnostics["messages_received"] == 3
    assert diagnostics["messages_forwarded"] == 3
    assert diagnostics["topics_seen"] == [
        "jackery/json",
        "jackery/raw",
        "jackery/array",
    ]


async def test_listener_publishes_protocol_json_through_ha_mqtt(
    hass: HomeAssistant,
    monkeypatch,
) -> None:
    """Official action requests reuse Home Assistant's broker connection."""
    publish = AsyncMock()
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_publish",
        publish,
    )
    client = JackeryLocalMqttClient(hass)

    await client.async_publish(
        "hb/device/SERIAL/action",
        {"type": 25, "token": "123456789", "body": None},
    )

    publish.assert_awaited_once()
    assert publish.await_args.args[:2] == (hass, "hb/device/SERIAL/action")
    assert json.loads(publish.await_args.args[2]) == {
        "type": 25,
        "token": "123456789",
        "body": None,
    }
    assert client.diagnostics_snapshot(redact=False)["messages_published"] == 1


async def test_listener_ignores_its_own_action_requests(
    hass: HomeAssistant,
) -> None:
    """A broad official subscription does not count outbound request echoes."""
    sink = AsyncMock(return_value=True)
    client = JackeryLocalMqttClient(hass, sink=sink)

    await client._handle_message(
        "hb/device/SERIAL/action",
        b'{"type":25,"token":"123456789","body":null}',
    )

    sink.assert_not_awaited()
    diagnostics = client.diagnostics_snapshot(redact=False)
    assert diagnostics["messages_received"] == 0
    assert diagnostics["messages_rejected_by_sink"] == 0
    assert diagnostics["messages_dropped"] == 0


async def test_periodic_protocol_requests_are_cancelled_on_stop(
    hass: HomeAssistant,
) -> None:
    """The entry-owned request loop cannot survive adapter unload."""
    called = asyncio.Event()

    async def poller() -> int:
        await asyncio.sleep(0)
        called.set()
        return 5

    client = JackeryLocalMqttClient(hass)
    client._connected = True
    client.start_periodic_requests(poller)
    await called.wait()

    await client.async_stop()

    assert client._poll_task is None
    assert (
        client.diagnostics_snapshot(redact=False)["periodic_requests_active"] is False
    )
