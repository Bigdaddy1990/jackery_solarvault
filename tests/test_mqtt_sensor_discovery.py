"""Native Jackery setup must preserve separately registered MQTT entities."""

import asyncio
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.client.mqtt_discovery import (
    JackeryMqttSensorPublisher,
    _async_clear_topics,  # ruff: ignore[import-private-name]
    _device_config,  # ruff: ignore[import-private-name]
    async_release_native_entity_ids,
    orphaned_mirror_config_topics,
)
from custom_components.jackery_solarvault.const import DOMAIN
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.descriptions.sensor import SENSOR_DESCRIPTIONS
from custom_components.jackery_solarvault.sensor import async_setup_entry
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers import device_registry as dr, entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.mark.parametrize(
    "key",
    ["main_battery_charge_energy_derived", "main_battery_discharge_energy_derived"],
)
def test_derived_main_battery_mqtt_discovery_has_energy_state_class(key: str) -> None:
    """MQTT mirrors keep the same Recorder class as native energy counters."""
    description = next(item for item in SENSOR_DESCRIPTIONS if item.key == key)
    entity = SimpleNamespace(
        name=key, device_class=description.device_class, state_class=None
    )
    config = JackeryMqttSensorPublisher._discovery_config(  # ruff: ignore[private-member-access]
        cast("Any", entity),
        cast("Any", description),
        unique_id=f"device_{key}",
        topics=("test/state", "test/availability"),
        device={"name": "SolarVault"},
    )
    assert config["device_class"] == "energy"
    assert config["state_class"] == "total_increasing"


@pytest.mark.asyncio()
async def test_native_setup_preserves_existing_mqtt_discovery(
    hass: HomeAssistant,
) -> None:
    """Setup must not send discovery tombstones for another integration's entity."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    mqtt_entry = MockConfigEntry(domain="mqtt", data={})
    mqtt_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    existing = registry.async_get_or_create(
        "sensor",
        "mqtt",
        "jackery_solarvault_mqtt_device_pack_1_soc",
        config_entry=mqtt_entry,
    )
    coordinator = JackerySolarVaultCoordinator(
        hass, entry, MagicMock(spec=JackeryApi), timedelta(seconds=15)
    )
    coordinator.data = {}
    entry.runtime_data = coordinator
    hass.config.components.add("mqtt")

    with (
        patch("homeassistant.components.mqtt.is_connected", return_value=True),
        patch("homeassistant.components.mqtt.async_subscribe_connection_status"),
        patch(
            "homeassistant.components.mqtt.async_publish",
            new=AsyncMock(),
        ) as async_publish,
    ):
        await async_setup_entry(hass, entry, MagicMock())
        await hass.async_block_till_done()
        await coordinator.async_shutdown()
        async_publish.assert_not_awaited()

    assert registry.async_get(existing.entity_id) is existing


@pytest.mark.asyncio()
async def test_sensor_setup_publishes_discovery_for_native_pack(
    hass: HomeAssistant,
) -> None:
    """The sensor platform must actually connect native packs to MQTT."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    coordinator = JackerySolarVaultCoordinator(
        hass, entry, MagicMock(spec=JackeryApi), timedelta(seconds=15)
    )
    coordinator.data = {}
    entry.runtime_data = coordinator
    hass.config.components.add("mqtt")
    entity = MagicMock()
    entity.unique_id = "device_battery_pack_1_soc"
    entity.device_info = {
        "identifiers": {(DOMAIN, "device_battery_pack_1")},
        "name": "SolarVault Zusatzbatterie 1",
    }
    entity.name = "Zusatzbatterie SOC"
    entity.available = True
    entity.native_value = 42
    entity.entity_description = None
    entity.device_class = None
    entity.entity_category = None
    entity.state_class = None
    entity.native_unit_of_measurement = None
    with (
        patch(
            "custom_components.jackery_solarvault.sensor._collect_sensor_entities",
            return_value=[entity],
        ),
        patch(
            "homeassistant.components.mqtt.async_subscribe_connection_status"
        ) as subscribe,
        patch("homeassistant.components.mqtt.is_connected", return_value=True),
        patch(
            "homeassistant.components.mqtt.async_publish", new=AsyncMock()
        ) as publish,
    ):
        await async_setup_entry(hass, entry, MagicMock())
        await asyncio.sleep(0)
        await hass.async_block_till_done()
        subscribe.call_args.args[1](True)
        await asyncio.sleep(0)
        await hass.async_block_till_done()
        await coordinator.async_shutdown()
    configs = [
        call
        for call in publish.await_args_list
        if call.args[1].endswith("/config") and call.args[2]
    ]
    assert len(configs) == 2  # ruff: ignore[magic-value-comparison]
    assert any(
        call.args[1].endswith("/config") and call.args[2]
        for call in publish.await_args_list
    )


@pytest.mark.asyncio()
async def test_obsolete_jackery_mirror_is_removed_from_mqtt(
    hass: HomeAssistant,
) -> None:
    """Only mirrors whose native sensor is gone get a retained tombstone.

    Live 2026-09-27: packs moved to serial keys, but the index mirrors kept
    frozen ``..._battery_pack_1_battery_pack_soc_2`` entities (SoC 6 vs 73).
    A pack without serial keeps its index key and must keep its mirror; a
    mirror of a device this entry does not own belongs to another instance.
    """
    mqtt_entry = MockConfigEntry(domain="mqtt", data={})
    mqtt_entry.add_to_hass(hass)
    native_entry = MockConfigEntry(domain=DOMAIN, data={})
    native_entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=native_entry.entry_id,
        identifiers={(DOMAIN, "573702884982521856")},
    )
    registry = er.async_get(hass)
    live = (
        "573702884982521856_battery_pack_2_soc",
        "573702884982521856_battery_pack_hq2c01400094hp3_soc",
        "573702884982521856_cloud_mqtt",
    )
    gone = (
        "573702884982521856_battery_pack_1_soc",
        "573702884982521856_smart_meter_grid_import_energy",
    )
    foreign = "999_smart_meter_power"
    for unique_id in live:
        registry.async_get_or_create(
            "sensor", DOMAIN, unique_id, config_entry=native_entry
        )
    for mirror_id in (
        *(f"jackery_solarvault_mqtt_{uid}" for uid in (*live, *gone, foreign)),
        "other_573702884982521856_cloud_mqtt",
    ):
        registry.async_get_or_create(
            "sensor", "mqtt", mirror_id, config_entry=mqtt_entry
        )
    pending = orphaned_mirror_config_topics(hass, native_entry.entry_id)
    topics = {
        f"homeassistant/sensor/jackery_solarvault/{unique_id}/config"
        for unique_id in gone
    }
    assert pending == topics
    with patch(
        "homeassistant.components.mqtt.async_publish", new=AsyncMock()
    ) as publish:
        assert await _async_clear_topics(hass, pending) == len(gone)
    assert {call.args[1] for call in publish.await_args_list} == topics
    assert not pending


def test_release_native_entity_ids_swaps_mirror_out_of_native_id(
    hass: HomeAssistant,
) -> None:
    """A mirror holding the native entity_id moves to the jackery_mqtt_ space."""
    mqtt_entry = MockConfigEntry(domain="mqtt", data={})
    mqtt_entry.add_to_hass(hass)
    native_entry = MockConfigEntry(domain=DOMAIN, data={})
    native_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor",
        "mqtt",
        "jackery_solarvault_mqtt_dev_cloud_mqtt",
        config_entry=mqtt_entry,
        suggested_object_id="solarvault_cloud_mqtt",
    )
    native = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "dev_cloud_mqtt",
        config_entry=native_entry,
        suggested_object_id="solarvault_cloud_mqtt",
    )
    untouched = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "dev_other",
        config_entry=native_entry,
        suggested_object_id="solarvault_other",
    )
    assert native.entity_id == "sensor.solarvault_cloud_mqtt_2"

    async_release_native_entity_ids(hass)
    async_release_native_entity_ids(hass)

    assert (
        registry.async_get_entity_id("sensor", DOMAIN, "dev_cloud_mqtt")
        == "sensor.solarvault_cloud_mqtt"
    )
    assert (
        registry.async_get_entity_id(
            "sensor", "mqtt", "jackery_solarvault_mqtt_dev_cloud_mqtt"
        )
        == "sensor.jackery_mqtt_solarvault_cloud_mqtt"
    )
    assert registry.async_get(untouched.entity_id) is not None


@pytest.mark.asyncio()
async def test_unavailable_pack_keeps_retained_discovery(hass: HomeAssistant) -> None:
    """A missing pack value must not remove its MQTT entity or history."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    publisher = JackeryMqttSensorPublisher(hass, entry_id=entry.entry_id)
    entity = MagicMock()
    entity.unique_id = "device_battery_pack_1_soc"
    entity.device_info = {
        "identifiers": {(DOMAIN, "device_battery_pack_1")},
        "name": "SolarVault 3 Pro Max Zusatzbatterie 1",
    }
    entity.name = "Zusatzbatterie SOC"
    entity.available = False
    entity.native_value = None
    entity.entity_description = SimpleNamespace(
        translation_key="battery_pack_soc",
        key="battery_pack_soc",
        entity_registry_enabled_default=True,
        device_class=None,
        entity_category=None,
        state_class=None,
        native_unit_of_measurement=None,
    )
    entity.device_class = None
    entity.entity_category = None
    entity.state_class = None
    entity.native_unit_of_measurement = None
    with patch.object(publisher, "_async_publish", new=AsyncMock()) as publish:
        await publisher._async_publish_entity(  # ruff: ignore[private-member-access]
            entity.unique_id, entity
        )
    config_topic = (
        "homeassistant/sensor/jackery_solarvault/device_battery_pack_1_soc/config"
    )
    assert publish.await_args_list[0].args[0] == config_topic
    config = json.loads(publish.await_args_list[0].args[1])
    assert config["name"] == "Zusatzbatterie SOC"
    # Never a native-looking id: that pushed native sensors to "_2" (live
    # 2026-09-26: cloud_mqtt, ble_transport, ethernet_port, firmware_version).
    assert config["default_entity_id"] == (
        "sensor.jackery_mqtt_solarvault_3_pro_max_battery_pack_1_battery_pack_soc"
    )
    assert publish.await_args_list[1].args[1] == "offline"
    publisher.async_retire()


@pytest.mark.asyncio()
async def test_daily_mqtt_mirror_publishes_reset_with_each_state(
    hass: HomeAssistant,
) -> None:
    """HA must see a new reset when a mirrored daily total rolls over."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    publisher = JackeryMqttSensorPublisher(hass, entry_id=entry.entry_id)
    entity = SimpleNamespace(
        unique_id="device_today_ongrid_output",
        _device_id="device",
        device_info={"identifiers": {(DOMAIN, "device")}, "name": "SolarVault"},
        name="Netzseite Ausgang heute",
        available=True,
        native_value=11.22,
        last_reset=datetime(2026, 9, 24, tzinfo=UTC),
        entity_description=None,
        device_class="energy",
        entity_category=None,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement="kWh",
    )
    with patch.object(publisher, "_async_publish", new=AsyncMock()) as publish:
        await publisher._async_publish_entity(entity.unique_id, entity)  # ruff: ignore[private-member-access]
        config = json.loads(publish.await_args_list[0].args[1])
        assert config["value_template"] == "{{ value_json.value }}"
        assert config["last_reset_value_template"] == "{{ value_json.last_reset }}"
        assert json.loads(publish.await_args_list[1].args[1]) == {
            "value": "11.22",
            "last_reset": "2026-09-24T00:00:00+00:00",
        }
        publish.reset_mock()
        entity.native_value = 0.03
        entity.last_reset = datetime(2026, 9, 25, tzinfo=UTC)
        await publisher._async_publish_entity(entity.unique_id, entity)  # ruff: ignore[private-member-access]
        assert json.loads(publish.await_args_list[0].args[1]) == {
            "value": "0.03",
            "last_reset": "2026-09-25T00:00:00+00:00",
        }
        # Live 2026-09-26: an empty state payload made HA's value_json
        # templates fail on every unavailable mirrored daily total.
        publish.reset_mock()
        entity.native_value = None
        await publisher._async_publish_entity(entity.unique_id, entity)  # ruff: ignore[private-member-access]
        assert [call.args[1] for call in publish.await_args_list] == ["offline"]
    publisher.async_retire()


def test_mqtt_pack_device_links_to_native_pack(hass: HomeAssistant) -> None:
    """Discovery's MQTT device joins the existing Jackery device hierarchy."""
    native_entry = MockConfigEntry(domain=DOMAIN, data={})
    native_entry.add_to_hass(hass)
    mqtt_entry = MockConfigEntry(domain="mqtt", data={})
    mqtt_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    native = registry.async_get_or_create(
        config_entry_id=native_entry.entry_id,
        identifiers={(DOMAIN, "device_battery_pack_1")},
    )
    publisher = JackeryMqttSensorPublisher(hass, entry_id=native_entry.entry_id)
    mirror = registry.async_get_or_create(
        config_entry_id=mqtt_entry.entry_id,
        identifiers={("mqtt", "jackery_solarvault:device_battery_pack_1")},
    )
    publisher._link_mqtt_device(mirror.id)  # ruff: ignore[private-member-access]
    assert registry.async_get(mirror.id).via_device_id == native.id
    publisher.async_retire()


def test_mqtt_mirror_of_child_pack_links_to_head_unit(hass: HomeAssistant) -> None:
    """HA 2026.9 rejects a child as via device; link to the head unit instead."""
    native_entry = MockConfigEntry(domain=DOMAIN, data={})
    native_entry.add_to_hass(hass)
    mqtt_entry = MockConfigEntry(domain="mqtt", data={})
    mqtt_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    head = registry.async_get_or_create(
        config_entry_id=native_entry.entry_id,
        identifiers={(DOMAIN, "device")},
    )
    pack = registry.async_get_or_create_child(
        config_entry_id=native_entry.entry_id,
        identifiers={(DOMAIN, "device_battery_pack_1")},
        parent_device_id=head.id,
    )
    publisher = JackeryMqttSensorPublisher(hass, entry_id=native_entry.entry_id)
    mirror = registry.async_get_or_create(
        config_entry_id=mqtt_entry.entry_id,
        identifiers={("mqtt", "jackery_solarvault:device_battery_pack_1")},
    )

    publisher._link_mqtt_device(mirror.id)  # ruff: ignore[private-member-access]
    publisher._link_mqtt_device(pack.id)  # ruff: ignore[private-member-access]

    assert registry.async_get(mirror.id).via_device_id == head.id
    publisher.async_retire()


@pytest.mark.asyncio()
async def test_mirror_device_uses_native_registry_name_when_info_has_none(
    hass: HomeAssistant,
) -> None:
    """Live 2026-09-27: CT period mirrors appeared as "Jackery <raw identifier>"."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "dev_smart_meter_mac")},
        name="SolarVault 3 Pro Max Smart Meter",
    )
    publisher = JackeryMqttSensorPublisher(hass, entry_id=entry.entry_id)
    entity = SimpleNamespace(
        unique_id="dev_ct_input_day_energy",
        _device_id="dev",
        device_info={"identifiers": {(DOMAIN, "dev_smart_meter_mac")}},
        name="CT-Netzbezug heute",
        available=True,
        native_value=5.78,
        entity_description=None,
        device_class="energy",
        entity_category=None,
        state_class="measurement",
        native_unit_of_measurement="kWh",
    )
    with patch.object(publisher, "_async_publish", new=AsyncMock()) as publish:
        await publisher._async_publish_entity(  # ruff: ignore[private-member-access]
            entity.unique_id, cast("Any", entity)
        )
    config = json.loads(publish.await_args_list[0].args[1])
    assert config["device"]["name"] == "SolarVault 3 Pro Max Smart Meter"
    publisher.async_retire()


@pytest.mark.asyncio()
async def test_mirror_topics_follow_registered_device_not_payload(
    hass: HomeAssistant,
) -> None:
    """Live 2026-09-28: every CT mirror froze after a restart.

    The CT payload briefly lacked its identity, so device_info fell back to
    ``<device>_smart_meter_1``. The config went out once with those topics and
    every later state landed on MAC-keyed topics no mirror subscribed to.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "dev_smart_meter_mac")},
        name="SolarVault 3 Pro Max Smart Meter",
    )
    publisher = JackeryMqttSensorPublisher(hass, entry_id=entry.entry_id)
    entity = SimpleNamespace(
        unique_id="dev_smart_meter_power",
        _device_id="dev",
        device_info={"identifiers": {(DOMAIN, "dev_smart_meter_1")}},
        device_entry=device,
        name="CT-Phase T Leistung",
        available=True,
        native_value=-2.0,
        entity_description=None,
        device_class="power",
        entity_category=None,
        state_class="measurement",
        native_unit_of_measurement="W",
    )
    state_topic = (
        "jackery_solarvault/dev_smart_meter_mac/sensor/smart_meter_power/state"
    )
    with patch.object(publisher, "_async_publish", new=AsyncMock()) as publish:
        await publisher._async_publish_entity(  # ruff: ignore[private-member-access]
            entity.unique_id, cast("Any", entity)
        )
        config = json.loads(publish.await_args_list[0].args[1])
        assert config["device"]["identifiers"] == [
            "jackery_solarvault:dev_smart_meter_mac"
        ]
        assert config["state_topic"] == state_topic

        entity.device_info = {"identifiers": {(DOMAIN, "dev_smart_meter_mac")}}
        entity.native_value = 5.0
        publish.reset_mock()
        await publisher._async_publish_entity(  # ruff: ignore[private-member-access]
            entity.unique_id, cast("Any", entity)
        )
        assert [call.args for call in publish.await_args_list] == [(state_topic, "5.0")]

        # A renamed device republishes the discovery document exactly once.
        entity.device_entry = registry.async_update_device(
            device.id, name_by_user="Stromzähler"
        )
        publish.reset_mock()
        await publisher._async_publish_entity(  # ruff: ignore[private-member-access]
            entity.unique_id, cast("Any", entity)
        )
        assert len(publish.await_args_list) == 1
        config = json.loads(publish.await_args_list[0].args[1])
        assert config["device"]["name"] == "Stromzähler"
    publisher.async_retire()


def test_child_pack_mirror_never_reads_device_entry_only_fields(
    hass: HomeAssistant,
) -> None:
    """Live 2026-09-28: HA warned about ChildDeviceEntry.manufacturer (2027.9)."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    head = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "dev")}
    )
    pack = registry.async_get_or_create_child(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "dev_battery_pack_hq2c")},
        parent_device_id=head.id,
        name="Zusatzbatterie 1",
    )
    entity = SimpleNamespace(
        unique_id="dev_battery_pack_hq2c_soc",
        device_info={
            "identifiers": {(DOMAIN, "dev_battery_pack_hq2c")},
            "model": "Jackery battery pack",
        },
        device_entry=pack,
    )
    with patch.object(
        dr.ChildDeviceEntry, "__getattr__", side_effect=AttributeError, create=True
    ) as shim:
        device_id, device = _device_config(cast("Any", entity))

    shim.assert_not_called()
    assert device_id == "dev_battery_pack_hq2c"
    assert device["name"] == "Zusatzbatterie 1"
    assert device["model"] == "Jackery battery pack"
