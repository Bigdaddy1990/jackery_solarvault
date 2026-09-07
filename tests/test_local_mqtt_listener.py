"""Behavioral regressions for the direct local-broker MQTT transport."""

import asyncio
import json
from typing import TYPE_CHECKING, Any, Self
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import _async_start_local_mqtt  # ruff: ignore[import-private-name]
from custom_components.jackery_solarvault.client import local_mqtt
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
    LocalMqttConnectionSettings,
)
from custom_components.jackery_solarvault.const import (
    CONF_LOCAL_MQTT_ENABLE,
    CONF_SCAN_INTERVAL,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PORT,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class _BlockingMessages:
    def __aiter__(self) -> _BlockingMessages:
        return self

    async def __anext__(self) -> Any:  # ruff: ignore[any-type]
        await asyncio.Event().wait()
        raise StopAsyncIteration


class _FakeMqttClient:
    instances: list[_FakeMqttClient] = []  # ruff: ignore[mutable-class-default]

    def __init__(self, **kwargs: Any) -> None:  # ruff: ignore[any-type]
        self.kwargs = kwargs
        self.messages = _BlockingMessages()
        self.subscriptions: list[tuple[str, int]] = []
        self.publishes: list[tuple[str, str, int, bool]] = []
        self.instances.append(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def subscribe(self, topic: str, *, qos: int) -> None:
        self.subscriptions.append((topic, qos))

    async def publish(
        self, topic: str, payload: str, *, qos: int, retain: bool
    ) -> None:
        self.publishes.append((topic, payload, qos, retain))


async def test_local_mqtt_listener_disabled_by_option(hass: HomeAssistant) -> None:
    """A disabled entry must not create a broker client."""
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


async def test_entry_wires_the_configured_direct_broker(hass: HomeAssistant) -> None:
    """Configured broker coordinates must reach the direct client."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_SCAN_INTERVAL: 15,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.2.212",
            CONF_THIRD_PARTY_MQTT_PORT: 1884,
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

    settings = client_cls.call_args.args[1]
    assert isinstance(settings, LocalMqttConnectionSettings)
    assert settings.host == "192.168.2.212"
    assert settings.port == 1884  # ruff: ignore[magic-value-comparison]
    client.async_start.assert_awaited_once()
    client.set_snapshot_requester.assert_called_once()
    assert client.set_snapshot_requester.call_args.kwargs["interval_sec"] == 15  # ruff: ignore[magic-value-comparison]


async def test_direct_client_subscribes_and_publishes(
    hass: HomeAssistant,
    monkeypatch,  # ruff: ignore[missing-type-function-argument]
) -> None:
    """The direct session owns subscriptions and action publication."""
    _FakeMqttClient.instances.clear()
    monkeypatch.setattr(local_mqtt, "MqttClient", _FakeMqttClient)
    client = JackeryLocalMqttClient(
        hass,
        LocalMqttConnectionSettings(
            host="192.0.2.10",
            client_id="test-client",
            topic_filter="jackery/device/#",
            qos=1,
        ),
    )

    await client.async_start()
    broker = _FakeMqttClient.instances[-1]
    assert broker.kwargs["hostname"] == "192.0.2.10"
    assert broker.subscriptions == [("jackery/device/#", 1)]

    payload = {"type": 25, "token": "123456789", "body": None}
    await client.async_publish("hb/device/SERIAL/action", payload, qos=1)
    topic, encoded, qos, retained = broker.publishes[-1]
    assert topic == "hb/device/SERIAL/action"
    assert json.loads(encoded) == payload
    assert (qos, retained) == (1, False)

    await client.async_stop()
    assert not client.is_started


async def test_periodic_snapshot_requests_keep_counters_live(
    hass: HomeAssistant,
    monkeypatch,  # ruff: ignore[missing-type-function-argument]
) -> None:
    """A connected broker requests fresh counter snapshots repeatedly."""
    _FakeMqttClient.instances.clear()
    monkeypatch.setattr(local_mqtt, "MqttClient", _FakeMqttClient)
    requester = AsyncMock(return_value=6)
    client = JackeryLocalMqttClient(hass, host="192.0.2.10")
    client.set_snapshot_requester(requester, interval_sec=1)

    await client.async_start()
    async with asyncio.timeout(1.5):
        while requester.await_count < 2:  # ruff: ignore[async-busy-wait, magic-value-comparison]
            await asyncio.sleep(0.02)

    assert client.diagnostics_snapshot(redact=False)["periodic_requests_active"]
    await client.async_stop()
    assert client._periodic_snapshot_task is None  # ruff: ignore[private-member-access]


async def test_listener_forwards_every_size_valid_frame(
    hass: HomeAssistant,
) -> None:
    """Every size-valid frame reaches the sink, parsed or opaque.

    ``docs/AGENTS.md`` §1.1 Data Integrity First: live MQTT ingress is not
    filtered or dropped merely because a field is unknown or incomplete. A
    content gate keyed on known field names would also silently swallow any
    field a firmware update adds, so scoping stays the topic filter's job.
    Undecodable payloads are handed over with ``data=None`` instead of being
    discarded.
    """
    received: list[tuple[str, dict[str, Any] | None, bytes]] = []

    async def sink(
        topic: str,
        data: dict[str, Any] | None,
        raw: bytes,
    ) -> bool:
        await asyncio.sleep(0)
        received.append((topic, data, raw))
        return True

    client = JackeryLocalMqttClient(hass, host="192.0.2.10", sink=sink)
    await client._handle_message("jackery/json", b'{"batSoc":50}')  # ruff: ignore[private-member-access]
    await client._handle_message("jackery/raw", b"\xff\x00")  # ruff: ignore[private-member-access]

    expected_forwarded = 2
    assert received == [
        ("jackery/json", {"batSoc": 50}, b'{"batSoc":50}'),
        ("jackery/raw", None, b"\xff\x00"),
    ]
    diagnostics = client.diagnostics_snapshot(redact=False)
    assert diagnostics["messages_forwarded"] == expected_forwarded
    assert diagnostics["messages_filtered"] == 0
    assert diagnostics["messages_dropped"] == 0


async def test_inflight_snapshot_request_is_cancelled_on_stop(
    hass: HomeAssistant,
) -> None:
    """No request task survives config-entry unload."""
    started = asyncio.Event()

    async def requester() -> int:
        started.set()
        await asyncio.Event().wait()
        return 0

    client = JackeryLocalMqttClient(hass, host="192.0.2.10")
    client._connected = True  # ruff: ignore[private-member-access]
    client.set_snapshot_requester(requester, interval_sec=15)
    await started.wait()

    await client.async_stop()

    assert client._snapshot_task is None  # ruff: ignore[private-member-access]
    assert client._periodic_snapshot_task is None  # ruff: ignore[private-member-access]
