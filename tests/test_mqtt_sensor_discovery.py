"""Broker-facing MQTT sensor discovery regressions."""

import asyncio
import contextlib
from dataclasses import dataclass
from enum import StrEnum
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault import sensor as sensor_module
from custom_components.jackery_solarvault.client.mqtt_discovery import (
    JackeryMqttSensorPublisher,
    _LiveStateSnapshot,
    _device_config,
)
from custom_components.jackery_solarvault.const import (
    FIELD_CT_A_NEGATIVE_PHASE_POWER,
    FIELD_CT_A_PHASE_POWER,
    FIELD_CT_B_NEGATIVE_PHASE_POWER,
    FIELD_CT_B_PHASE_POWER,
    FIELD_CT_C_NEGATIVE_PHASE_POWER,
    FIELD_CT_C_PHASE_POWER,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER,
    FIELD_CT_TOTAL_PHASE_POWER,
    FIELD_SW_EPS_IN_PW,
    FIELD_SW_EPS_OUT_PW,
    PAYLOAD_CT_METER,
    PAYLOAD_DEVICE,
    PAYLOAD_PROPERTIES,
)
from custom_components.jackery_solarvault.sensor import (
    SENSOR_DESCRIPTIONS,
    SMART_METER_SENSOR_DESCRIPTIONS,
    JackerySensor,
    JackerySmartMeterSensor,
)
from homeassistant.components.mqtt.sensor import DISCOVERY_SCHEMA
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback


@dataclass
class _Description:
    """Minimal sensor-description surface used by the publisher."""

    key: str
    translation_key: str
    native_unit_of_measurement: str | None = None
    device_class: Any = None
    state_class: Any = None
    entity_category: Any = None
    entity_registry_enabled_default: bool = True


class _DeviceClass(StrEnum):
    """Representative Home Assistant string enum."""

    BATTERY = "battery"


class _StateClass(StrEnum):
    """Representative Home Assistant string enum."""

    MEASUREMENT = "measurement"


class _Sensor:
    """Mutable sensor double matching the native entity publication surface."""

    def __init__(
        self,
        unique_id: str,
        value: Any,
        description: _Description,
        *,
        name: str | None = None,
        entity_id: str | None = None,
    ) -> None:
        self.unique_id = unique_id
        self.native_value = value
        self.name = name
        self.entity_id = entity_id
        self.available = True
        self.entity_description = description
        self.device_info = {
            "identifiers": {("jackery_solarvault", "device-1")},
            "name": "SolarVault",
            "manufacturer": "Jackery",
            "model": "HomePower 2000 Ultra",
        }
        self._attr_entity_registry_enabled_default = True


def _event(data: dict[str, Any]) -> Any:
    """Build a minimal event double while keeping callback call sites typed."""
    return cast(Any, SimpleNamespace(data=data))


def test_mqtt_device_config_serializes_device_connections() -> None:
    """MQTT discovery reuses native HA device-registry connection keys."""
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="soc"),
    )
    sensor.device_info["connections"] = {
        (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:44:55:66")
    }

    _device_id, config = _device_config(sensor)

    assert config["connections"] == [["mac", "aa:bb:cc:44:55:66"]]


@pytest.mark.asyncio
async def test_scheduled_snapshot_is_a_tracked_finite_task() -> None:
    """Finite publication is tracked by HA and explicitly awaited on unload."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    create_task = MagicMock(side_effect=_create_task)
    create_background_task = MagicMock(side_effect=_create_task)
    hass = SimpleNamespace(
        async_create_task=create_task,
        async_create_background_task=create_background_task,
    )
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    publisher.track(
        _Sensor(
            "device-1_soc",
            62,
            _Description(key="soc", translation_key="state_of_charge"),
        )
    )

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ):
        publisher.async_schedule_publish()
        assert publisher._task is not None
        await publisher._task

    create_task.assert_called_once()
    create_background_task.assert_not_called()


@pytest.mark.asyncio
async def test_initial_snapshot_waits_for_homeassistant_started() -> None:
    """Retained discovery must not contend with HA's own startup subscriptions."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    started_callback: Any = None
    unsubscribe = MagicMock()

    def _listen_once(_event_type: str, callback: Any) -> MagicMock:
        nonlocal started_callback
        started_callback = callback
        return unsubscribe

    hass = SimpleNamespace(
        async_create_background_task=_create_task,
        is_running=False,
        bus=SimpleNamespace(async_listen_once=_listen_once),
    )
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    publisher.track(
        _Sensor(
            "device-1_soc",
            62,
            _Description(key="soc", translation_key="state_of_charge"),
        )
    )

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ):
        publisher.async_schedule_initial_publish()
        assert publisher._task is None
        assert started_callback is not None

        started_callback(MagicMock())
        assert publisher._task is not None
        await publisher._task

    # EventBus.async_listen_once removes its own listener before the callback.
    unsubscribe.assert_not_called()


@pytest.mark.asyncio
async def test_disconnected_publish_retries_complete_snapshot_after_reconnect() -> None:
    """A transient HA MQTT disconnect must preserve and retry the snapshot."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(
        data={},
        async_create_background_task=_create_task,
    )
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    publisher.track(
        _Sensor(
            "device-1_soc",
            62,
            _Description(key="soc", translation_key="state_of_charge"),
        )
    )
    connected = False
    reconnect_callback: Any = None
    unsubscribe = MagicMock()

    async def _publish(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(0)
        if not connected:
            raise HomeAssistantError(
                "Error talking to MQTT: The client is not currently connected."
            )

    def _subscribe(_hass: Any, callback: Any) -> Any:
        nonlocal reconnect_callback
        reconnect_callback = callback
        return unsubscribe

    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=AsyncMock(side_effect=_publish),
        ) as async_publish,
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_subscribe_connection_status",
            side_effect=_subscribe,
        ) as subscribe_connection,
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.is_connected",
            side_effect=lambda _hass: connected,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery._LOGGER.exception"
        ) as log_exception,
    ):
        publisher.async_schedule_publish()
        first_task = publisher._task
        assert first_task is not None
        await first_task

        assert publisher._pending is True
        assert async_publish.await_count == 1
        subscribe_connection.assert_called_once()
        log_exception.assert_not_called()

        connected = True
        reconnect_callback(True)
        retry_task = publisher._task
        assert retry_task is not None
        await retry_task

    assert publisher._pending is False
    assert async_publish.await_count == 4
    assert [call.args[1] for call in async_publish.await_args_list[1:]] == [
        "homeassistant/sensor/jackery_solarvault/device_1_soc/config",
        "jackery_solarvault/device-1/sensor/soc/state",
        "jackery_solarvault/device-1/sensor/soc/availability",
    ]
    unsubscribe.assert_called_once_with()


@pytest.mark.asyncio
async def test_native_state_event_publishes_only_the_changed_sensor() -> None:
    """Live state events must not rescan every broker-mirrored sensor."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(async_create_background_task=_create_task)
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    changed = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
        entity_id="sensor.solarvault_soc",
    )
    untouched = _Sensor(
        "device-1_power",
        300,
        _Description(key="power", translation_key="power"),
        entity_id="sensor.solarvault_power",
    )
    publisher.track(changed)
    publisher.track(untouched)

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ) as async_publish:
        await publisher.async_publish_pending()
        async_publish.reset_mock()
        publisher._async_state_changed(_event({"entity_id": "sensor.unrelated"}))
        assert publisher._task is None
        changed.native_value = 63
        publisher._async_state_changed(
            _event({"entity_id": "sensor.solarvault_soc"})
        )
        assert publisher._task is not None
        await publisher._task

    assert [call.args[1] for call in async_publish.await_args_list] == [
        "jackery_solarvault/device-1/sensor/soc/state",
    ]


@pytest.mark.asyncio
async def test_live_state_events_publish_each_captured_snapshot_in_order() -> None:
    """A busy publisher must preserve every event value instead of rereading latest."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(async_create_background_task=_create_task)
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
        entity_id="sensor.solarvault_soc",
    )
    publisher.track(sensor)
    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ):
        await publisher.async_publish_pending()

    first_state_started = asyncio.Event()
    release_first_state = asyncio.Event()
    block_first_state = True

    async def _publish(_hass: Any, topic: str, _payload: str, **_kwargs: Any) -> None:
        nonlocal block_first_state
        if topic.endswith("/state") and block_first_state:
            block_first_state = False
            first_state_started.set()
            await release_first_state.wait()

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(side_effect=_publish),
    ) as async_publish:
        sensor.native_value = 63
        publisher._async_state_changed(
            _event({
                "entity_id": "sensor.solarvault_soc",
                "new_state": SimpleNamespace(state="63"),
            })
        )
        first_task = publisher._task
        assert first_task is not None
        await asyncio.wait_for(first_state_started.wait(), timeout=1.0)

        sensor.native_value = 64
        publisher._async_state_changed(
            _event({
                "entity_id": "sensor.solarvault_soc",
                "new_state": SimpleNamespace(state="64"),
            })
        )
        sensor.native_value = 999
        release_first_state.set()
        await first_task
        if publisher._task is not None and publisher._task is not first_task:
            await publisher._task

    assert [
        call.args[2]
        for call in async_publish.await_args_list
        if call.args[1].endswith("/state")
    ] == ["63", "64"]


@pytest.mark.asyncio
async def test_disconnected_live_events_replay_every_snapshot_after_reconnect() -> None:
    """A disconnect keeps the live FIFO, rather than replacing it with latest state."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(data={}, async_create_background_task=_create_task)
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
        entity_id="sensor.solarvault_soc",
    )
    publisher.track(sensor)
    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ):
        await publisher.async_publish_pending()

    connected = False
    reconnect_callback: Any = None
    successful_states: list[str] = []

    async def _publish(_hass: Any, topic: str, payload: str, **_kwargs: Any) -> None:
        await asyncio.sleep(0)
        if not connected:
            raise HomeAssistantError(
                "Error talking to MQTT: The client is not currently connected."
            )
        if topic.endswith("/state"):
            successful_states.append(payload)

    def _subscribe(_hass: Any, callback: Any) -> MagicMock:
        nonlocal reconnect_callback
        reconnect_callback = callback
        return MagicMock()

    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=AsyncMock(side_effect=_publish),
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_subscribe_connection_status",
            side_effect=_subscribe,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.is_connected",
            side_effect=lambda _hass: connected,
        ),
    ):
        sensor.native_value = 63
        publisher._async_state_changed(
            _event({
                "entity_id": "sensor.solarvault_soc",
                "new_state": SimpleNamespace(state="63"),
            })
        )
        first_task = publisher._task
        assert first_task is not None
        sensor.native_value = 64
        publisher._async_state_changed(
            _event({
                "entity_id": "sensor.solarvault_soc",
                "new_state": SimpleNamespace(state="64"),
            })
        )
        await first_task
        assert reconnect_callback is not None

        connected = True
        reconnect_callback(True)
        retry_task = publisher._task
        assert retry_task is not None
        await retry_task

    assert successful_states == ["63", "64"]


@pytest.mark.asyncio
async def test_initial_snapshot_uses_serial_iteration_without_gather_fanout() -> None:
    """Serial broker writes must not allocate one waiting coroutine per sensor."""
    hass = SimpleNamespace(async_create_background_task=asyncio.create_task)
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    publisher.track(
        _Sensor("device-1_soc", 62, _Description(key="soc", translation_key="soc"))
    )
    publisher.track(
        _Sensor(
            "device-1_power",
            300,
            _Description(key="power", translation_key="power"),
        )
    )

    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.asyncio.gather",
            side_effect=AssertionError("snapshot must iterate serially"),
        ),
    ):
        await publisher.async_publish_pending()


def test_constructor_rolls_back_listeners_and_runtime_owner_on_failure() -> None:
    """Partial bus-listener setup must not leave callbacks or a dead owner behind."""
    unsubscribe_state = MagicMock()
    calls = 0

    def _listen(_event_type: str, _callback: Any) -> MagicMock:
        nonlocal calls
        calls += 1
        if calls == 1:
            return unsubscribe_state
        raise RuntimeError("entity registry listener failed")

    hass = SimpleNamespace(data={}, bus=SimpleNamespace(async_listen=_listen))

    with pytest.raises(RuntimeError, match="entity registry listener failed"):
        JackeryMqttSensorPublisher(cast(HomeAssistant, hass), entry_id="entry-1")

    unsubscribe_state.assert_called_once_with()
    assert (
        hass.data["jackery_solarvault"]["entry-1"].get("mqtt_sensor_publisher") is None
    )


@pytest.mark.asyncio
async def test_entity_id_rename_preserves_targeted_live_mqtt_publish() -> None:
    """A registry rename moves the O(1) live-event index to the new entity ID."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(async_create_background_task=_create_task)
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
        entity_id="sensor.solarvault_soc",
    )
    publisher.track(sensor)

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ) as async_publish:
        await publisher.async_publish_pending()
        async_publish.reset_mock()
        sensor.entity_id = "sensor.pv_soc"
        publisher._async_entity_registry_updated(
            _event({
                "action": "update",
                "entity_id": "sensor.pv_soc",
                "old_entity_id": "sensor.solarvault_soc",
                "changes": {"entity_id": "sensor.solarvault_soc"},
            })
        )
        sensor.native_value = 63
        publisher._async_state_changed(_event({"entity_id": "sensor.pv_soc"}))
        task = publisher._task
        assert task is not None
        await task

    assert [call.args[1] for call in async_publish.await_args_list] == [
        "jackery_solarvault/device-1/sensor/soc/state",
    ]
    assert [call.args[2] for call in async_publish.await_args_list] == ["63"]


@pytest.mark.asyncio
async def test_initial_snapshot_serializes_broker_mids() -> None:
    """Discovery never keeps multiple broker acknowledgements in flight."""
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, SimpleNamespace()), entry_id="entry-1"
    )
    for index in range(12):
        publisher.track(
            _Sensor(
                f"device-1_value_{index}",
                index,
                _Description(
                    key=f"value_{index}",
                    translation_key=f"value_{index}",
                ),
            )
        )

    release = asyncio.Event()
    publish_started = asyncio.Event()
    active = 0
    max_active = 0

    async def _blocked_publish(*_args: Any, **_kwargs: Any) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        publish_started.set()
        await release.wait()
        active -= 1

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(side_effect=_blocked_publish),
    ):
        task = asyncio.create_task(publisher.async_publish_pending())
        await asyncio.wait_for(publish_started.wait(), timeout=0.2)
        await asyncio.sleep(0)
        release.set()
        await task

    assert max_active == 1


@pytest.mark.asyncio
async def test_value_sensor_publishes_retained_discovery_and_live_state() -> None:
    """Only discovery persists; current values are published immediately."""
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(
            key="soc",
            translation_key="state_of_charge",
            native_unit_of_measurement="%",
            device_class=_DeviceClass.BATTERY,
            state_class=_StateClass.MEASUREMENT,
        ),
    )
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, SimpleNamespace()), entry_id="entry-1"
    )
    publisher.track(sensor)

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ) as async_publish:
        await publisher.async_publish_pending()

    assert async_publish.await_count == 3
    calls_by_topic = {call.args[1]: call for call in async_publish.await_args_list}
    config_topic = "homeassistant/sensor/jackery_solarvault/device_1_soc/config"
    state_topic = "jackery_solarvault/device-1/sensor/soc/state"
    availability_topic = "jackery_solarvault/device-1/sensor/soc/availability"
    assert set(calls_by_topic) == {config_topic, state_topic, availability_topic}
    assert [call.args[1] for call in async_publish.await_args_list] == [
        config_topic,
        state_topic,
        availability_topic,
    ]

    config = json.loads(calls_by_topic[config_topic].args[2])
    assert config == {
        "availability_topic": availability_topic,
        "device": {
            "identifiers": ["jackery_solarvault:device-1"],
            "manufacturer": "Jackery",
            "model": "HomePower 2000 Ultra",
            "name": "SolarVault",
        },
        "device_class": "battery",
        "enabled_by_default": True,
        "name": "State Of Charge",
        "origin": {
            "name": "Jackery SolarVault",
            "support_url": "https://github.com/Bigdaddy1990/jackery_solarvault",
        },
        "payload_available": "online",
        "payload_not_available": "offline",
        "state_class": "measurement",
        "state_topic": state_topic,
        "unique_id": "jackery_solarvault_mqtt_device-1_soc",
        "unit_of_measurement": "%",
    }
    assert DISCOVERY_SCHEMA(config)["state_topic"] == state_topic
    assert calls_by_topic[config_topic].kwargs == {"qos": 0, "retain": True}
    assert calls_by_topic[state_topic].args[2] == "62"
    assert calls_by_topic[state_topic].kwargs == {"qos": 0, "retain": False}
    assert calls_by_topic[availability_topic].args[2] == "online"
    assert calls_by_topic[availability_topic].kwargs == {"qos": 0, "retain": False}


@pytest.mark.asyncio
async def test_discovery_name_matches_native_translated_entity_name() -> None:
    """MQTT discovery uses the same translated name as the native entity."""
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
        name="Ladezustand",
    )
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, SimpleNamespace()), entry_id="entry-1"
    )
    publisher.track(sensor)

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ) as async_publish:
        await publisher.async_publish_pending()

    config_call = next(
        call
        for call in async_publish.await_args_list
        if call.args[1].endswith("/config")
    )
    assert json.loads(config_call.args[2])["name"] == "Ladezustand"


@pytest.mark.asyncio
async def test_unknown_sensor_is_discovered_offline_and_unchanged_state_is_deduplicated() -> (
    None
):
    """Missing values stay mapped and visible without repeated broker writes."""
    known = _Sensor(
        "device-1_ct_phase_a",
        123.4,
        _Description(key="ct_phase_a", translation_key="ct_phase_a"),
    )
    unknown = _Sensor(
        "device-1_missing",
        None,
        _Description(key="missing", translation_key="missing"),
    )
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, SimpleNamespace()), entry_id="entry-1"
    )
    publisher.track(known)
    publisher.track(unknown)

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ) as async_publish:
        await publisher.async_publish_pending()
        first_count = async_publish.await_count
        await publisher.async_publish_pending()

    assert first_count == 5
    assert async_publish.await_count == first_count
    missing_calls = [
        call for call in async_publish.await_args_list if "missing" in call.args[1]
    ]
    assert len(missing_calls) == 2
    config_call = next(
        call for call in missing_calls if call.args[1].endswith("/config")
    )
    availability_call = next(
        call for call in missing_calls if call.args[1].endswith("/availability")
    )
    assert json.loads(config_call.args[2])["name"] == "Missing"
    assert availability_call.args[2] == "offline"


@pytest.mark.parametrize("loss_mode", ["unknown", "unavailable"])
@pytest.mark.asyncio
async def test_published_sensor_goes_offline_until_recovery(
    loss_mode: str,
) -> None:
    """A lost value immediately marks the live sensor offline."""
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
    )
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, SimpleNamespace()), entry_id="entry-1"
    )
    publisher.track(sensor)
    state_topic = "jackery_solarvault/device-1/sensor/soc/state"
    availability_topic = "jackery_solarvault/device-1/sensor/soc/availability"

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ) as async_publish:
        await publisher.async_publish_pending()
        async_publish.reset_mock()

        if loss_mode == "unknown":
            sensor.native_value = None
        else:
            sensor.available = False
        await publisher.async_publish_pending()
        assert [
            (call.args[1], call.args[2]) for call in async_publish.await_args_list
        ] == [
            (availability_topic, "offline"),
            (state_topic, ""),
        ]
        async_publish.reset_mock()

        sensor.native_value = 63
        sensor.available = True
        await publisher.async_publish_pending()

    assert [(call.args[1], call.args[2]) for call in async_publish.await_args_list] == [
        (state_topic, "63"),
        (availability_topic, "online"),
    ]


@pytest.mark.asyncio
async def test_real_ct_phase_and_eps_entities_are_exported() -> None:
    """The APP-facing A/B/C/T and EPS entities use the same broker export path."""
    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = {
        "device-1": {
            PAYLOAD_DEVICE: {"deviceName": "SolarVault"},
            PAYLOAD_PROPERTIES: {
                FIELD_SW_EPS_IN_PW: 321,
                FIELD_SW_EPS_OUT_PW: 287,
            },
            PAYLOAD_CT_METER: {
                "deviceSn": "CT123",
                FIELD_CT_A_PHASE_POWER: 110,
                FIELD_CT_A_NEGATIVE_PHASE_POWER: 10,
                FIELD_CT_B_PHASE_POWER: 220,
                FIELD_CT_B_NEGATIVE_PHASE_POWER: 20,
                FIELD_CT_C_PHASE_POWER: 330,
                FIELD_CT_C_NEGATIVE_PHASE_POWER: 30,
                FIELD_CT_TOTAL_PHASE_POWER: 660,
                FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER: 60,
            },
        }
    }
    entities: list[Any] = []
    for key in ("eps_in_power", "eps_out_power"):
        sensor_description = next(
            item for item in SENSOR_DESCRIPTIONS if item.key == key
        )
        entities.append(JackerySensor(coordinator, "device-1", sensor_description))
    for key in ("phase_1_power", "phase_2_power", "phase_3_power", "power"):
        smart_meter_description = next(
            item for item in SMART_METER_SENSOR_DESCRIPTIONS if item.key == key
        )
        entity = JackerySmartMeterSensor(
            coordinator, "device-1", smart_meter_description
        )
        entity._refresh_cache()
        entities.append(entity)

    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, SimpleNamespace()), entry_id="entry-1"
    )
    for entity in entities:
        publisher.track(entity)
    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ) as async_publish:
        await publisher.async_publish_pending()

    topics = {call.args[1] for call in async_publish.await_args_list}
    expected_state_suffixes = {
        "eps_in_power",
        "eps_out_power",
        "smart_meter_phase_1_power",
        "smart_meter_phase_2_power",
        "smart_meter_phase_3_power",
        "smart_meter_power",
    }
    assert {
        topic.rsplit("/", 2)[-2]
        for topic in topics
        if topic.startswith("jackery_solarvault/") and topic.endswith("/state")
    } == expected_state_suffixes
    assert sum(topic.endswith("/config") for topic in topics) == len(
        expected_state_suffixes
    )
    configs = {
        json.loads(call.args[2])["name"]
        for call in async_publish.await_args_list
        if call.args[1].endswith("/config")
    }
    assert {"CT Phase A", "CT Phase B", "CT Phase C", "CT Phase T"} <= configs


@pytest.mark.asyncio
async def test_shutdown_clears_retained_discovery_only() -> None:
    """Unload removes retained discovery, not non-retained live values."""
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
    )
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, SimpleNamespace()), entry_id="entry-1"
    )
    publisher.track(sensor)
    unsubscribe = MagicMock()
    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=AsyncMock(),
        ) as async_publish,
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_subscribe_connection_status",
            return_value=unsubscribe,
        ) as subscribe_connection,
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.is_connected",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery._CLEANUP_RETRY_DELAYS_SEC",
            (0.0,),
        ),
    ):
        await publisher.async_publish_pending()
        async_publish.reset_mock()
        await publisher.async_shutdown()

    assert {call.args[1] for call in async_publish.await_args_list} == {
        "homeassistant/sensor/jackery_solarvault/device_1_soc/config"
    }
    assert all(call.args[2] == "" for call in async_publish.await_args_list)
    assert all(
        call.kwargs == {"qos": 0, "retain": True}
        for call in async_publish.await_args_list
    )
    subscribe_connection.assert_called_once()
    unsubscribe.assert_called_once_with()


@pytest.mark.asyncio
async def test_shutdown_does_not_block_unload_when_broker_is_unavailable() -> None:
    """Failed retained cleanup is retried after the broker reconnects."""
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
    )
    hass = SimpleNamespace(
        async_create_task=lambda coro, **_kwargs: asyncio.create_task(coro),
        async_create_background_task=lambda coro, **_kwargs: asyncio.create_task(coro),
    )
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    publisher.track(sensor)
    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ):
        await publisher.async_publish_pending()
    unsubscribe = MagicMock()
    cleanup_started_after_subscription: list[bool] = []

    def _broker_unavailable(*_args: Any, **_kwargs: Any) -> None:
        cleanup_started_after_subscription.append(subscribe_connection.called)
        raise RuntimeError("broker unavailable")

    recovered_publish = AsyncMock(side_effect=_broker_unavailable)
    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=recovered_publish,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_subscribe_connection_status",
            return_value=unsubscribe,
        ) as subscribe_connection,
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.is_connected",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery._CLEANUP_RETRY_DELAYS_SEC",
            (0.0,),
        ),
    ):
        await publisher.async_shutdown()
        subscribe_connection.assert_called_once()
        assert cleanup_started_after_subscription == [True]
        reconnect_callback = subscribe_connection.call_args.args[1]
        recovered_publish.reset_mock(side_effect=True)
        reconnect_callback(True)
        assert publisher._cleanup_task is not None
        await publisher._cleanup_task

    assert recovered_publish.await_count == 1
    assert all(call.args[2] == "" for call in recovered_publish.await_args_list)
    unsubscribe.assert_called_once_with()


@pytest.mark.asyncio
async def test_reconnect_during_initial_cleanup_is_serialized() -> None:
    """A reconnect callback cannot run retained cleanup concurrently."""
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
    )
    hass = SimpleNamespace(
        async_create_task=lambda coro, **_kwargs: asyncio.create_task(coro),
        async_create_background_task=lambda coro, **_kwargs: asyncio.create_task(coro),
    )
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    publisher.track(sensor)
    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ):
        await publisher.async_publish_pending()

    active_publishes = 0
    max_active_publishes = 0
    publish_count = 0
    subscribe_connection = MagicMock(return_value=MagicMock())

    async def _cleanup_publish(*_args: Any, **_kwargs: Any) -> None:
        nonlocal active_publishes, max_active_publishes, publish_count
        active_publishes += 1
        max_active_publishes = max(max_active_publishes, active_publishes)
        publish_count += 1
        if publish_count == 1:
            reconnect_callback = subscribe_connection.call_args.args[1]
            reconnect_callback(True)
            await asyncio.sleep(0)
        active_publishes -= 1

    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=AsyncMock(side_effect=_cleanup_publish),
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_subscribe_connection_status",
            new=subscribe_connection,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.is_connected",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery._CLEANUP_RETRY_DELAYS_SEC",
            (0.0,),
        ),
    ):
        await publisher.async_shutdown()
        assert publisher._cleanup_task is not None
        await publisher._cleanup_task

    assert max_active_publishes == 1


@pytest.mark.asyncio
async def test_shutdown_cleanup_timeout_preserves_topics_for_reconnect() -> None:
    """A broker that never ACKs cannot block config-entry unload."""
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
    )
    hass = SimpleNamespace(
        async_create_task=lambda coro, **_kwargs: asyncio.create_task(coro),
        async_create_background_task=lambda coro, **_kwargs: asyncio.create_task(coro),
    )
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    publisher.track(sensor)
    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ):
        await publisher.async_publish_pending()

    never_ack = asyncio.Event()

    async def _never_ack(*_args: Any, **_kwargs: Any) -> None:
        await never_ack.wait()

    blocking_publish = AsyncMock(side_effect=_never_ack)
    unsubscribe = MagicMock()
    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=blocking_publish,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_subscribe_connection_status",
            return_value=unsubscribe,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.is_connected",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery._CLEANUP_TIMEOUT_SEC",
            0.01,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery._CLEANUP_RETRY_DELAYS_SEC",
            (0.0,),
            create=True,
        ),
    ):
        await asyncio.wait_for(publisher.async_shutdown(), timeout=0.2)
        blocking_publish.reset_mock(side_effect=True)
        assert publisher._cleanup_task is not None
        await publisher._cleanup_task

    assert blocking_publish.await_count == 1
    assert all(call.args[2] == "" for call in blocking_publish.await_args_list)
    unsubscribe.assert_called_once_with()


@pytest.mark.asyncio
async def test_shutdown_while_disconnected_defers_cleanup_until_reconnect() -> None:
    """A known-disconnected broker is not awaited during entry unload."""
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
    )
    hass = SimpleNamespace(
        async_create_task=lambda coro, **_kwargs: asyncio.create_task(coro),
        async_create_background_task=lambda coro, **_kwargs: asyncio.create_task(coro),
    )
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    publisher.track(sensor)
    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ):
        await publisher.async_publish_pending()

    cleanup_publish = AsyncMock()
    unsubscribe = MagicMock()
    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=cleanup_publish,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_subscribe_connection_status",
            return_value=unsubscribe,
        ) as subscribe_connection,
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.is_connected",
            side_effect=(False, True),
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery._CLEANUP_RETRY_DELAYS_SEC",
            (0.0,),
        ),
    ):
        await publisher.async_shutdown()
        cleanup_publish.assert_not_awaited()
        reconnect_callback = subscribe_connection.call_args.args[1]
        reconnect_callback(True)
        assert publisher._cleanup_task is not None
        await publisher._cleanup_task

    assert cleanup_publish.await_count == 1
    unsubscribe.assert_called_once_with()


@pytest.mark.asyncio
async def test_reload_cancels_old_inflight_publish() -> None:
    """A replacement publisher cannot resume a stale blocked snapshot."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(data={}, async_create_background_task=_create_task)
    old_publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    old_publisher.track(
        _Sensor(
            "device-1_soc",
            62,
            _Description(key="soc", translation_key="state_of_charge"),
        )
    )
    publish_started = asyncio.Event()
    release = asyncio.Event()

    async def _blocked_publish(*_args: Any, **_kwargs: Any) -> None:
        publish_started.set()
        await release.wait()

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(side_effect=_blocked_publish),
    ):
        old_publisher.async_schedule_publish()
        old_task = old_publisher._task
        assert old_task is not None
        await asyncio.wait_for(publish_started.wait(), timeout=0.2)

        replacement = JackeryMqttSensorPublisher(
            cast(HomeAssistant, hass), entry_id="entry-1"
        )

        assert replacement is not old_publisher
        assert old_publisher._stopping is True
        with contextlib.suppress(asyncio.CancelledError):
            await old_task

    assert old_task.cancelled()


@pytest.mark.asyncio
async def test_reload_retires_old_background_cleanup_owner() -> None:
    """An old generation cannot delete retained topics republished on reload."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    create_background_task = MagicMock(side_effect=_create_task)
    hass = SimpleNamespace(
        data={},
        async_create_task=_create_task,
        async_create_background_task=create_background_task,
    )
    old_sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
    )
    old_publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    old_publisher.track(old_sensor)
    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ):
        await old_publisher.async_publish_pending()

    never_ack = asyncio.Event()

    async def _never_ack(*_args: Any, **_kwargs: Any) -> None:
        await never_ack.wait()

    broker_publish = AsyncMock(side_effect=_never_ack)
    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=broker_publish,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_subscribe_connection_status",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.is_connected",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery._CLEANUP_TIMEOUT_SEC",
            0.01,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery._CLEANUP_RETRY_DELAYS_SEC",
            (0.01,),
        ),
    ):
        await old_publisher.async_shutdown()
        old_cleanup_task = old_publisher._cleanup_task
        broker_publish.reset_mock(side_effect=True)

        new_sensor = _Sensor(
            "device-1_soc",
            63,
            _Description(key="soc", translation_key="state_of_charge"),
        )
        new_publisher = JackeryMqttSensorPublisher(
            cast(HomeAssistant, hass), entry_id="entry-1"
        )
        new_publisher.track(new_sensor)
        await new_publisher.async_publish_pending()
        new_publish_count = broker_publish.await_count

        if old_cleanup_task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await old_cleanup_task

    create_background_task.assert_called_once()
    assert all(
        call.args[2] != ""
        for call in broker_publish.await_args_list[new_publish_count:]
    )


@pytest.mark.asyncio
async def test_reload_transfers_stale_cleanup_without_retiring_replacement() -> None:
    """A replacement clears only orphaned retained discovery after its snapshot."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(data={}, async_create_background_task=_create_task)
    old_publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    reused_config = "homeassistant/sensor/jackery_solarvault/device_1_soc/config"
    orphaned_config = "homeassistant/sensor/jackery_solarvault/device_1_power/config"
    old_publisher._cleanup_topics.update((reused_config, orphaned_config))

    replacement = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    replacement.track(
        _Sensor(
            "device-1_soc",
            63,
            _Description(key="soc", translation_key="state_of_charge"),
        )
    )

    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=AsyncMock(),
        ) as async_publish,
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.is_connected",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery._CLEANUP_RETRY_DELAYS_SEC",
            (0.0,),
        ),
    ):
        replacement.async_schedule_publish()
        publish_task = replacement._task
        assert publish_task is not None
        await publish_task
        cleanup_task = replacement._cleanup_task
        assert cleanup_task is not None
        await cleanup_task

    empty_topics = {
        call.args[1] for call in async_publish.await_args_list if call.args[2] == ""
    }
    assert empty_topics == {orphaned_config}
    assert replacement._is_current_owner()
    assert replacement._published_configs == {"device-1_soc": reused_config}


@pytest.mark.asyncio
async def test_sensor_platform_tracks_entities_when_local_mqtt_is_enabled() -> None:
    """Platform setup publishes an initial broker snapshot, not every poll."""
    coordinator = MagicMock()
    coordinator.data = {
        "device-1": {
            PAYLOAD_DEVICE: {"deviceName": "SolarVault"},
            PAYLOAD_PROPERTIES: {"soc": 62},
        }
    }
    coordinator.last_update_success = True
    coordinator.has_smart_meter_accessory.return_value = False
    coordinator.async_add_listener.side_effect = lambda _callback: lambda: None
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={},
        options={"third_party_mqtt_enable": True},
        runtime_data=coordinator,
        async_on_unload=MagicMock(),
    )
    hass = SimpleNamespace(config=SimpleNamespace(components={"mqtt"}))
    publisher = MagicMock()
    publisher.async_shutdown = AsyncMock()
    batches: list[list[Any]] = []

    with patch.object(
        sensor_module,
        "JackeryMqttSensorPublisher",
        return_value=publisher,
    ):
        await sensor_module.async_setup_entry(
            cast(HomeAssistant, hass),
            cast(ConfigEntry[Any], entry),
            cast(
                AddEntitiesCallback,
                lambda entities: batches.append(list(entities)),
            ),
        )

    assert batches
    assert publisher.track.call_count == len(batches[0])
    publisher.async_schedule_initial_publish.assert_called_once_with()
    assert all(
        call.args != (publisher.async_schedule_initial_publish,)
        for call in coordinator.async_add_listener.call_args_list
    )
    entry.async_on_unload.assert_any_call(publisher.async_shutdown)


@pytest.mark.asyncio
async def test_sensor_platform_publishes_when_local_mqtt_is_disabled() -> None:
    """Mapped broker sensors do not depend on the raw Local MQTT receiver."""
    coordinator = MagicMock()
    coordinator.data = {
        "device-1": {
            PAYLOAD_DEVICE: {"deviceName": "SolarVault"},
            PAYLOAD_PROPERTIES: {"soc": 62},
        }
    }
    coordinator.last_update_success = True
    coordinator.has_smart_meter_accessory.return_value = False
    coordinator.async_add_listener.side_effect = lambda _callback: lambda: None
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={},
        options={"third_party_mqtt_enable": False},
        runtime_data=coordinator,
        async_on_unload=MagicMock(),
    )
    hass = SimpleNamespace(config=SimpleNamespace(components={"mqtt"}))
    publisher = MagicMock()
    publisher.async_shutdown = AsyncMock()
    batches: list[list[Any]] = []

    with patch.object(
        sensor_module,
        "JackeryMqttSensorPublisher",
        return_value=publisher,
    ):
        await sensor_module.async_setup_entry(
            cast(HomeAssistant, hass),
            cast(ConfigEntry[Any], entry),
            cast(
                AddEntitiesCallback,
                lambda entities: batches.append(list(entities)),
            ),
        )

    assert batches
    assert publisher.track.call_count == len(batches[0])
    publisher.async_schedule_initial_publish.assert_called_once_with()
    entry.async_on_unload.assert_any_call(publisher.async_shutdown)


@pytest.mark.asyncio
async def test_live_event_cannot_cancel_pending_full_snapshot() -> None:
    """A startup snapshot remains pending while a newer live event is delivered."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(async_create_task=_create_task)
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    changed = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
        entity_id="sensor.solarvault_soc",
    )
    untouched = _Sensor(
        "device-1_power",
        300,
        _Description(key="power", translation_key="power"),
        entity_id="sensor.solarvault_power",
    )
    publisher.track(changed)
    publisher.track(untouched)

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ) as async_publish:
        publisher.async_schedule_publish()
        changed.native_value = 63
        publisher._async_state_changed(
            cast(
                Any,
                SimpleNamespace(
                    data={
                        "entity_id": "sensor.solarvault_soc",
                        "new_state": SimpleNamespace(state="63"),
                    }
                ),
            )
        )
        task = publisher._task
        assert task is not None
        await task

    config_topics = {
        call.args[1]
        for call in async_publish.await_args_list
        if call.args[1].endswith("/config")
    }
    assert config_topics == {
        "homeassistant/sensor/jackery_solarvault/device_1_soc/config",
        "homeassistant/sensor/jackery_solarvault/device_1_power/config",
    }


@pytest.mark.asyncio
async def test_entity_registry_create_indexes_entity_added_after_track() -> None:
    """The registry create event links an entity ID assigned after ``track``."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(async_create_task=_create_task)
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
    )
    publisher.track(sensor)
    sensor.entity_id = "sensor.solarvault_soc"
    publisher._async_entity_registry_updated(
        cast(
            Any,
            SimpleNamespace(
                data={
                    "action": "create",
                    "entity_id": "sensor.solarvault_soc",
                }
            ),
        )
    )

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ) as async_publish:
        publisher._async_state_changed(
            cast(
                Any,
                SimpleNamespace(
                    data={
                        "entity_id": "sensor.solarvault_soc",
                        "new_state": SimpleNamespace(state="63"),
                    }
                ),
            )
        )
        task = publisher._task
        assert task is not None
        await task

    assert any(call.args[2] == "63" for call in async_publish.await_args_list)


@pytest.mark.asyncio
async def test_reconciliation_never_overtakes_live_events_that_race_with_config() -> None:
    """A mutable snapshot cannot publish ahead of events queued during config I/O."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(async_create_task=_create_task)
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
        entity_id="sensor.solarvault_soc",
    )
    publisher.track(sensor)
    config_started = asyncio.Event()
    release_config = asyncio.Event()

    async def _publish(_hass: Any, topic: str, _payload: str, **_kwargs: Any) -> None:
        if topic.endswith("/config"):
            config_started.set()
            await release_config.wait()

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(side_effect=_publish),
    ) as async_publish:
        publisher.async_schedule_publish()
        task = publisher._task
        assert task is not None
        await asyncio.wait_for(config_started.wait(), timeout=1.0)
        for value in (63, 64):
            sensor.native_value = value
            publisher._async_state_changed(
                cast(
                    Any,
                    SimpleNamespace(
                        data={
                            "entity_id": "sensor.solarvault_soc",
                            "new_state": SimpleNamespace(state=str(value)),
                        }
                    ),
                )
            )
        release_config.set()
        await task
        if publisher._task is not None and publisher._task is not task:
            await publisher._task

    assert [
        call.args[2]
        for call in async_publish.await_args_list
        if call.args[1].endswith("/state")
    ] == ["63", "64"]


@pytest.mark.asyncio
async def test_connected_transient_publish_error_retries_without_new_event() -> None:
    """A propagated broker error restores snapshot work and retries with backoff."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(async_create_task=_create_task)
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    publisher.track(
        _Sensor(
            "device-1_soc",
            62,
            _Description(key="soc", translation_key="state_of_charge"),
        )
    )
    calls = 0

    async def _publish(*_args: Any, **_kwargs: Any) -> None:
        nonlocal calls
        await asyncio.sleep(0)
        calls += 1
        if calls == 1:
            raise HomeAssistantError("broker acknowledged connection but is busy")

    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=AsyncMock(side_effect=_publish),
        ) as async_publish,
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.is_connected",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery._PUBLISH_RETRY_DELAYS_SEC",
            (0.0,),
            create=True,
        ),
    ):
        publisher.async_schedule_publish()
        task = publisher._task
        assert task is not None
        await task

    assert async_publish.await_count == 4
    assert publisher._pending is False


@pytest.mark.asyncio
async def test_disconnect_stream_waits_for_one_reconnect_without_publish_hotloop() -> None:
    """Events accumulate FIFO-exactly without repeated writes while disconnected."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(data={}, async_create_task=_create_task)
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
        entity_id="sensor.solarvault_soc",
    )
    publisher.track(sensor)
    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ):
        await publisher.async_publish_pending()

    connected = False
    reconnect_callback: Any = None
    successful_states: list[str] = []

    async def _publish(_hass: Any, topic: str, payload: str, **_kwargs: Any) -> None:
        await asyncio.sleep(0)
        if not connected:
            raise HomeAssistantError("broker connection lost")
        if topic.endswith("/state"):
            successful_states.append(payload)

    def _subscribe(_hass: Any, callback: Any) -> MagicMock:
        nonlocal reconnect_callback
        reconnect_callback = callback
        return MagicMock()

    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=AsyncMock(side_effect=_publish),
        ) as async_publish,
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_subscribe_connection_status",
            side_effect=_subscribe,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.is_connected",
            side_effect=lambda _hass: connected,
        ),
    ):
        for value in (63, 64, 65):
            sensor.native_value = value
            publisher._async_state_changed(
                cast(
                    Any,
                    SimpleNamespace(
                        data={
                            "entity_id": "sensor.solarvault_soc",
                            "new_state": SimpleNamespace(state=str(value)),
                        }
                    ),
                )
            )
            task = publisher._task
            if value == 63:
                assert task is not None
                await task
            else:
                await asyncio.sleep(0)

        assert async_publish.await_count == 1
        assert reconnect_callback is not None
        connected = True
        reconnect_callback(True)
        retry_task = publisher._task
        assert retry_task is not None
        await retry_task

    assert successful_states == ["63", "64", "65"]


@pytest.mark.asyncio
async def test_concurrent_public_publish_calls_share_fifo_lock() -> None:
    """Two callers cannot publish or dequeue the same accepted live snapshot."""
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, SimpleNamespace()), entry_id="entry-1"
    )
    publisher.track(
        _Sensor(
            "device-1_soc",
            62,
            _Description(key="soc", translation_key="state_of_charge"),
        )
    )
    publisher._live_state_events.append(
        _LiveStateSnapshot("device-1_soc", True, "63")
    )
    first_state_started = asyncio.Event()
    release_first_state = asyncio.Event()

    async def _publish(_hass: Any, topic: str, _payload: str, **_kwargs: Any) -> None:
        if topic.endswith("/state") and not first_state_started.is_set():
            first_state_started.set()
            await release_first_state.wait()

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(side_effect=_publish),
    ) as async_publish:
        first = asyncio.create_task(publisher.async_publish_pending())
        await asyncio.wait_for(first_state_started.wait(), timeout=1.0)
        second = asyncio.create_task(publisher.async_publish_pending())
        await asyncio.sleep(0)
        release_first_state.set()
        await asyncio.gather(first, second)

    state_payloads = [
        call.args[2]
        for call in async_publish.await_args_list
        if call.args[1].endswith("/state")
    ]
    assert state_payloads.count("63") == 1
    assert not publisher._live_state_events


@pytest.mark.asyncio
async def test_untracked_handoff_event_waits_until_replacement_tracks_entity() -> None:
    """An accepted reload event remains queued until its native entity exists."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(async_create_task=_create_task)
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, hass), entry_id="entry-1"
    )
    publisher._live_state_events.append(
        _LiveStateSnapshot("device-1_soc", True, "63")
    )
    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ) as async_publish:
        publisher._async_start_publish_worker()
        first_task = publisher._task
        assert first_task is not None
        await first_task
        assert len(publisher._live_state_events) == 1

        publisher.track(
            _Sensor(
                "device-1_soc",
                63,
                _Description(key="soc", translation_key="state_of_charge"),
            )
        )
        retry_task = publisher._task
        assert retry_task is not None
        await retry_task

    assert not publisher._live_state_events
    assert any(call.args[2] == "63" for call in async_publish.await_args_list)


@pytest.mark.asyncio
async def test_explicit_none_state_event_publishes_offline_tombstone() -> None:
    """Entity removal cannot republish the stale mutable native value as online."""
    publisher = JackeryMqttSensorPublisher(
        cast(HomeAssistant, SimpleNamespace()), entry_id="entry-1"
    )
    sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
        entity_id="sensor.solarvault_soc",
    )
    publisher.track(sensor)
    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ):
        await publisher.async_publish_pending()

    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ) as async_publish:
        publisher._async_state_changed(
            cast(
                Any,
                SimpleNamespace(
                    data={
                        "entity_id": "sensor.solarvault_soc",
                        "new_state": None,
                    }
                ),
            )
        )
        task = publisher._task
        assert task is not None
        await task

    assert [(call.args[1], call.args[2]) for call in async_publish.await_args_list] == [
        ("jackery_solarvault/device-1/sensor/soc/availability", "offline"),
        ("jackery_solarvault/device-1/sensor/soc/state", ""),
    ]


@pytest.mark.asyncio
async def test_fast_shutdown_reload_hands_off_fifo_without_config_delete_burst() -> None:
    """Normal sequential reload preserves FIFO and retained publication state."""

    def _create_task(coro: Any, **_kwargs: Any) -> asyncio.Task[Any]:
        return asyncio.create_task(coro)

    hass = SimpleNamespace(
        data={},
        async_create_task=_create_task,
        async_create_background_task=_create_task,
    )
    old = JackeryMqttSensorPublisher(cast(HomeAssistant, hass), entry_id="entry-1")
    old_sensor = _Sensor(
        "device-1_soc",
        62,
        _Description(key="soc", translation_key="state_of_charge"),
    )
    old.track(old_sensor)
    with patch(
        "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
        new=AsyncMock(),
    ):
        await old.async_publish_pending()
    old._live_state_events.append(
        _LiveStateSnapshot("device-1_soc", True, "63")
    )

    with (
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.async_publish",
            new=AsyncMock(),
        ) as async_publish,
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery.mqtt.is_connected",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.client.mqtt_discovery._RELOAD_HANDOFF_GRACE_SEC",
            60.0,
            create=True,
        ),
    ):
        await old.async_shutdown()
        replacement = JackeryMqttSensorPublisher(
            cast(HomeAssistant, hass), entry_id="entry-1"
        )
        replacement.track(
            _Sensor(
                "device-1_soc",
                63,
                _Description(key="soc", translation_key="state_of_charge"),
            )
        )
        replacement.async_schedule_publish()
        task = replacement._task
        assert task is not None
        await task
        replacement.async_retire()

    assert [
        call.args[2]
        for call in async_publish.await_args_list
        if call.args[1].endswith("/state")
    ] == ["63"]
    assert not any(call.args[2] == "" for call in async_publish.await_args_list)
    assert not any(
        call.args[1].endswith("/config") for call in async_publish.await_args_list
    )
