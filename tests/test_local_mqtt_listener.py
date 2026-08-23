"""Behavioral tests for the Home Assistant-owned local MQTT listener."""

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, call, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import _async_start_local_mqtt
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)
from custom_components.jackery_solarvault.const import (
    CONF_LOCAL_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    DOMAIN,
    LOCAL_MQTT_DEFAULT_TOPIC,
    SHELLY_RPC_EVENT_TOPIC,
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
    """An enabled entry starts one HA-owned adapter without duplicate config work."""
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
    coordinator.async_schedule_local_mqtt_device_config.assert_not_called()


async def test_listener_subscribes_to_jackery_and_exact_shelly_rpc_topics(
    hass: HomeAssistant,
    monkeypatch,
) -> None:
    """The custom filter supplements both official trees and exact Shelly RPC."""
    unsubscribe_jackery = MagicMock()
    unsubscribe_singular = MagicMock()
    unsubscribe_plural = MagicMock()
    unsubscribe_shelly = MagicMock()
    unsubscribe_status = MagicMock()
    wait_for_client = AsyncMock(return_value=True)
    subscribe = AsyncMock(
        side_effect=(
            unsubscribe_jackery,
            unsubscribe_singular,
            unsubscribe_plural,
            unsubscribe_shelly,
        )
    )
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
    assert subscribe.await_args_list == [
        call(
            hass,
            "jackery/device/#",
            client._async_message_received,
            qos=1,
            encoding=None,
        ),
        call(
            hass,
            LOCAL_MQTT_DEFAULT_TOPIC,
            client._async_message_received,
            qos=1,
            encoding=None,
        ),
        call(
            hass,
            "hb/devices/#",
            client._async_message_received,
            qos=1,
            encoding=None,
        ),
        call(
            hass,
            SHELLY_RPC_EVENT_TOPIC,
            client._async_message_received,
            qos=1,
            encoding=None,
        ),
    ]
    subscribe_status.assert_called_once_with(
        hass,
        client._async_connection_status_changed,
    )
    assert client.is_started is True
    assert client.is_connected is True

    await client.async_stop()
    unsubscribe_jackery.assert_called_once_with()
    unsubscribe_singular.assert_called_once_with()
    unsubscribe_plural.assert_called_once_with()
    unsubscribe_shelly.assert_called_once_with()
    unsubscribe_status.assert_called_once_with()


async def test_listener_subscribes_to_both_official_topic_families(
    hass: HomeAssistant,
    monkeypatch,
) -> None:
    """The built-in filter receives singular and plural Jackery firmware trees."""
    unsubscribe_singular = MagicMock()
    unsubscribe_plural = MagicMock()
    unsubscribe_shelly = MagicMock()
    unsubscribe_status = MagicMock()
    subscribe = AsyncMock(
        side_effect=(unsubscribe_singular, unsubscribe_plural, unsubscribe_shelly)
    )
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_wait_for_mqtt_client",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_subscribe",
        subscribe,
    )
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.mqtt.async_subscribe_connection_status",
        MagicMock(return_value=unsubscribe_status),
    )
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.client.local_mqtt.mqtt.is_connected",
        MagicMock(return_value=True),
    )
    client = JackeryLocalMqttClient(hass)

    await client.async_start()

    assert [item.args[1] for item in subscribe.await_args_list] == [
        LOCAL_MQTT_DEFAULT_TOPIC,
        "hb/devices/#",
        SHELLY_RPC_EVENT_TOPIC,
    ]

    await client.async_stop()
    unsubscribe_singular.assert_called_once_with()
    unsubscribe_plural.assert_called_once_with()
    unsubscribe_shelly.assert_called_once_with()
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


async def test_listener_cleans_primary_subscription_when_shelly_subscribe_fails(
    hass: HomeAssistant,
    monkeypatch,
) -> None:
    """A failed supplemental topic cannot leak the primary Jackery callback."""
    unsubscribe_status = MagicMock()
    unsubscribe_jackery = MagicMock()
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
        AsyncMock(
            side_effect=(unsubscribe_jackery, RuntimeError("Shelly topic unavailable"))
        ),
    )
    client = JackeryLocalMqttClient(hass, topic_filter="jackery/device/#")

    assert await client._async_subscribe_once() is False

    unsubscribe_jackery.assert_called_once_with()
    unsubscribe_status.assert_called_once_with()
    assert client.is_started is False


async def test_listener_forwards_only_shelly_status_rpc_events(
    hass: HomeAssistant,
) -> None:
    """High-rate Shelly BLE scan events never enter Jackery shared ingest."""
    sink = AsyncMock(return_value=True)
    client = JackeryLocalMqttClient(hass, sink=sink)
    notify_event = {
        "body": {
            "src": "shellypro3em-5c013b048e3c",
            "method": "NotifyEvent",
            "params": {"events": [{"event": "ble.scan_result"}]},
        },
    }
    notify_status = {
        "body": {
            "src": "shellypro3em-5c013b048e3c",
            "method": "NotifyStatus",
            "params": {"em:0": {"total_act_power": 42.0}},
        },
    }
    lnm_status = {
        "body": {
            "src": "shellypro3em-5c013b048e3c",
            "method": "NotifyStatus",
            "params": {"lnm:200": {"stats": {"rx_msgs": 0, "tx_msgs": 39755}}},
        },
    }
    foreign_status = {
        "body": {
            "src": "other-device-123",
            "method": "NotifyStatus",
            "params": {"power": 999},
        },
    }

    await client._handle_message(SHELLY_RPC_EVENT_TOPIC, json.dumps(notify_event))
    await client._handle_message(SHELLY_RPC_EVENT_TOPIC, json.dumps(foreign_status))
    await client._handle_message(SHELLY_RPC_EVENT_TOPIC, json.dumps(lnm_status))
    await client._handle_message(SHELLY_RPC_EVENT_TOPIC, json.dumps(notify_status))

    sink.assert_awaited_once()
    assert sink.await_args.args[0] == SHELLY_RPC_EVENT_TOPIC
    assert sink.await_args.args[1] == notify_status

    direct_status = notify_status["body"]
    await client._handle_message(SHELLY_RPC_EVENT_TOPIC, json.dumps(direct_status))
    assert sink.await_count == 2
    diagnostics = client.diagnostics_snapshot(redact=False)
    assert diagnostics["messages_filtered"] == 3
    assert diagnostics["messages_rejected_by_sink"] == 0
    assert diagnostics["messages_dropped"] == 0


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


async def test_snapshot_request_runs_once_without_periodic_repeats(
    hass: HomeAssistant,
) -> None:
    """Connected startup requests one snapshot and never starts a timer loop."""
    called = asyncio.Event()
    call_count = 0

    async def requester() -> int:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0)
        called.set()
        return 5

    client = JackeryLocalMqttClient(hass)
    client._connected = True
    client.set_snapshot_requester(requester)
    await called.wait()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert call_count == 1
    diagnostics = client.diagnostics_snapshot(redact=False)
    assert diagnostics["snapshot_request_active"] is False
    assert diagnostics["periodic_requests_active"] is False

    await client.async_stop()


async def test_snapshot_request_runs_once_per_real_reconnect(
    hass: HomeAssistant,
) -> None:
    """Only a disconnected-to-connected edge requests another snapshot."""
    requested = asyncio.Event()
    call_count = 0

    async def requester() -> int:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0)
        requested.set()
        return 5

    client = JackeryLocalMqttClient(hass)
    client.set_snapshot_requester(requester)
    await asyncio.sleep(0)
    assert call_count == 0

    client._async_connection_status_changed(True)
    await requested.wait()
    await asyncio.sleep(0)
    assert call_count == 1

    requested.clear()
    client._async_connection_status_changed(True)
    await asyncio.sleep(0)
    assert call_count == 1

    client._async_connection_status_changed(False)
    client._async_connection_status_changed(True)
    await requested.wait()
    await asyncio.sleep(0)
    assert call_count == 2

    await client.async_stop()


async def test_inflight_snapshot_request_is_cancelled_on_stop(
    hass: HomeAssistant,
) -> None:
    """The one-shot request cannot survive adapter unload."""
    started = asyncio.Event()

    async def requester() -> int:
        started.set()
        await asyncio.Event().wait()
        return 0

    client = JackeryLocalMqttClient(hass)
    client._connected = True
    client.set_snapshot_requester(requester)
    await started.wait()

    await client.async_stop()

    assert client._snapshot_task is None
    diagnostics = client.diagnostics_snapshot(redact=False)
    assert diagnostics["snapshot_request_active"] is False
    assert diagnostics["periodic_requests_active"] is False
