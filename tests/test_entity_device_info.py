"""Regression: device_info falls back family-neutral, never to the literal "SolarVault".

HomePower is SolarVault's predecessor (same device family). A device whose
model/name source fields arrive blank must not be mislabeled with the literal
brand "SolarVault" in the Home Assistant device registry. The real reported
model/name always wins when present — only the fallback changes.
"""

from types import SimpleNamespace
from typing import Any, cast

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.binary_sensor import (
    JackerySubdeviceAlarmBinarySensor,
)
from custom_components.jackery_solarvault.const import (
    DEFAULT_DEVICE_MODEL_FALLBACK,
    DOMAIN,
    FIELD_DEVICE_SN,
    FIELD_DEV_ID,
    FIELD_DEV_MODEL,
    FIELD_MAC,
    PAYLOAD_CT_METER,
    PAYLOAD_DISCOVERY,
    PAYLOAD_PROPERTIES,
)
from custom_components.jackery_solarvault.descriptions.number import NUMBER_DESCRIPTIONS
from custom_components.jackery_solarvault.descriptions.sensor import (
    SENSOR_DESCRIPTIONS,
    STAT_DESCRIPTIONS,
)
from custom_components.jackery_solarvault.entity import JackeryEntity
import custom_components.jackery_solarvault.sensor as sensor_module
from custom_components.jackery_solarvault.sensor import (
    JackeryBatteryPackSensor,
    JackeryBreakerSensor,
    JackeryMeterHeadSensor,
    JackerySmartMeterSensor,
    JackerySubdeviceAlarmSensor,
)
from custom_components.jackery_solarvault.switch import JackeryBreakerSwitch
from custom_components.jackery_solarvault.util import stable_subdevice_key
from homeassistant.helpers import device_registry as dr, entity_registry as er

_DEVICE_ID = "home-power-3002"


def _entity(payload: dict[str, Any]) -> JackeryEntity:
    """Return a bare ``JackeryEntity`` bound to a single-device coordinator snapshot.

    Bypasses ``__init__`` (mirrors ``test_coordinator_homepower_discovery.py``) so
    ``device_info`` exercises only its real name/model resolution logic without
    hass wiring: every ``_system``/``_discovery``/``_device_meta`` property reads
    from ``coordinator.data[device_id]``.
    """
    entity = JackeryEntity.__new__(JackeryEntity)
    mutable = cast("Any", entity)
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.coordinator = SimpleNamespace(data={_DEVICE_ID: payload})
    # pyrefly: ignore [no-any-return-implicit]
    return entity


def _bound(cls: type[Any], payload: dict[str, Any], **extra: Any) -> Any:
    """Instantiate any ``JackeryEntity`` subclass bound to a coordinator snapshot.

    Mirrors ``_entity()`` above for the subdevice sensor/switch/binary_sensor
    subclasses, so their ``device_info``/``_build_*_device_info`` builders can be
    exercised without hass wiring or the real ``__init__``. ``extra`` sets the
    per-subclass identity attributes (e.g. ``_pack_index``, ``_sub_device_sn``)
    a given builder reads directly.
    """
    instance = cast("Any", cls).__new__(cls)
    mutable = cast("Any", instance)
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.coordinator = SimpleNamespace(data={_DEVICE_ID: payload})
    for name, value in extra.items():
        setattr(mutable, name, value)
    return instance


def test_device_info_model_falls_back_family_neutral() -> None:
    """Blank model source fields resolve to the neutral fallback, not "SolarVault"."""
    info: dict[str, Any] = dict(_entity({}).device_info)

    assert info["model"] == DEFAULT_DEVICE_MODEL_FALLBACK
    assert info["model"] != "SolarVault"


def test_device_info_model_uses_reported_model_when_present() -> None:
    """A real reported model always wins over the fallback."""
    payload = {PAYLOAD_DISCOVERY: {FIELD_DEV_MODEL: "HTH0132500A"}}

    assert dict(_entity(payload).device_info)["model"] == "HTH0132500A"


def test_parent_device_info_exposes_normalized_mac_connection() -> None:
    """The native and MQTT registry entries share the parent device MAC."""
    payload = {PAYLOAD_PROPERTIES: {FIELD_MAC: "AA-BB-CC-11-22-33"}}

    assert dict(_entity(payload).device_info)["connections"] == {
        (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:11:22:33")
    }


def test_main_battery_uses_child_device_without_changing_entity_identity(
    hass: Any,
) -> None:
    """Group internal-battery entities under one child while retaining unique IDs."""
    entry_id = "main-battery-entry"
    MockConfigEntry(domain=DOMAIN, entry_id=entry_id).add_to_hass(hass)
    parent = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry_id,
        identifiers={(DOMAIN, _DEVICE_ID)},
    )
    descriptions = (
        next(d for d in SENSOR_DESCRIPTIONS if d.key == "bat_soc"),
        next(
            d
            for d in SENSOR_DESCRIPTIONS
            if d.key == "main_battery_charge_energy_derived"
        ),
        next(d for d in NUMBER_DESCRIPTIONS if d.key == "soc_charge_limit_set"),
    )
    for description in descriptions:
        entity = _entity({})
        cast("Any", entity).coordinator = SimpleNamespace(
            data={_DEVICE_ID: {}},
            hass=hass,
            config_entry=SimpleNamespace(entry_id=entry_id),
        )
        entity.entity_description = description
        entity._attr_unique_id = f"{_DEVICE_ID}_{description.key}"  # ruff: ignore[private-member-access]
        info: dict[str, Any] = dict(entity.device_info)
        assert info["identifiers"] == {(DOMAIN, f"{_DEVICE_ID}_main_battery")}
        assert info["parent_device_id"] == parent.id
        assert entity.unique_id == f"{_DEVICE_ID}_{description.key}"
        child = dr.async_get(hass).async_get_or_create_child(
            config_entry_id=entry_id,
            identifiers=info["identifiers"],
            name=info["name"],
            parent_device_id=info["parent_device_id"],
        )
        assert child.parent_device_id == parent.id


def test_ct_statistics_join_existing_smart_meter_device(hass: Any) -> None:
    """CT period sensors keep their unique IDs while joining the CT device."""
    entry_id = "ct-stat-entry"
    MockConfigEntry(domain=DOMAIN, entry_id=entry_id).add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry_id,
        identifiers={(DOMAIN, _DEVICE_ID)},
    )
    ct = {"deviceSn": "CT-SERIAL-1"}
    entity = _entity({PAYLOAD_CT_METER: ct})
    cast("Any", entity).coordinator = SimpleNamespace(
        data={_DEVICE_ID: {PAYLOAD_CT_METER: ct}},
        hass=hass,
        config_entry=SimpleNamespace(entry_id=entry_id),
    )
    entity.entity_description = next(
        d for d in STAT_DESCRIPTIONS if d.key == "ct_input_day_energy"
    )
    entity._attr_unique_id = f"{_DEVICE_ID}_ct_input_day_energy"  # ruff: ignore[private-member-access]
    meter_key = stable_subdevice_key("smart_meter", ct["deviceSn"], 1)
    assert entity.device_info["identifiers"] == {(DOMAIN, f"{_DEVICE_ID}_{meter_key}")}
    assert entity.unique_id == f"{_DEVICE_ID}_ct_input_day_energy"
    meter = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry_id,
        identifiers=entity.device_info["identifiers"],
    )
    assert entity.unique_id is not None
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        entity.unique_id,
        config_entry=next(iter(hass.config_entries.async_entries(DOMAIN))),
        device_id=meter.id,
    )
    entity.coordinator.data[_DEVICE_ID] = {}
    assert entity.device_info["identifiers"] == {(DOMAIN, f"{_DEVICE_ID}_{meter_key}")}


def test_pv_channel_entities_share_child_only_when_channel_exists(hass: Any) -> None:
    """One present PV input gets one logical child and keeps old entity IDs."""
    entry_id = "pv-channel-entry"
    MockConfigEntry(domain=DOMAIN, entry_id=entry_id).add_to_hass(hass)
    parent = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry_id,
        identifiers={(DOMAIN, _DEVICE_ID)},
    )
    payload = {PAYLOAD_PROPERTIES: {"pv1": {"pvPw": 24}}}
    for description in (
        next(d for d in SENSOR_DESCRIPTIONS if d.key == "pv1_power"),
        next(d for d in STAT_DESCRIPTIONS if d.key == "device_pv1_day_energy"),
    ):
        entity = _entity(payload)
        cast("Any", entity).coordinator = SimpleNamespace(
            data={_DEVICE_ID: payload},
            hass=hass,
            config_entry=SimpleNamespace(entry_id=entry_id),
        )
        entity.entity_description = description
        entity._attr_unique_id = f"{_DEVICE_ID}_{description.key}"  # ruff: ignore[private-member-access]
        info: dict[str, Any] = dict(entity.device_info)
        assert info["identifiers"] == {(DOMAIN, f"{_DEVICE_ID}_pv_input_1")}
        assert info["parent_device_id"] == parent.id
        assert entity.unique_id == f"{_DEVICE_ID}_{description.key}"
        dr.async_get(hass).async_get_or_create_child(
            config_entry_id=entry_id,
            identifiers=info["identifiers"],
            name=info["name"],
            parent_device_id=info["parent_device_id"],
        )

    temporarily_missing = _entity({})
    cast("Any", temporarily_missing).coordinator = SimpleNamespace(
        data={_DEVICE_ID: {}},
        hass=hass,
        config_entry=SimpleNamespace(entry_id=entry_id),
    )
    temporarily_missing.entity_description = next(
        d for d in SENSOR_DESCRIPTIONS if d.key == "pv1_power"
    )
    assert temporarily_missing.device_info["identifiers"] == {
        (DOMAIN, f"{_DEVICE_ID}_pv_input_1")
    }

    absent = _entity(payload)
    absent.entity_description = next(
        d for d in SENSOR_DESCRIPTIONS if d.key == "pv2_power"
    )
    assert absent.device_info["identifiers"] == {(DOMAIN, _DEVICE_ID)}


def test_late_pv_channel_moves_existing_sensors_to_child(hass: Any) -> None:
    """A newly observed input rehomes old period sensors without new IDs."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="late-pv-entry")
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    parent = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, _DEVICE_ID)},
    )
    pv_ids = (f"{_DEVICE_ID}_pv1_power", f"{_DEVICE_ID}_device_pv1_day_energy")
    for unique_id in (*pv_ids, f"{_DEVICE_ID}_bat_soc"):
        entities.async_get_or_create(
            "sensor", DOMAIN, unique_id, config_entry=entry, device_id=parent.id
        )
    seen: set[tuple[str, int]] = set()
    coordinator = SimpleNamespace(
        data={_DEVICE_ID: {PAYLOAD_PROPERTIES: {"pv1": {"pvPw": 24}}}}
    )

    sensor_module._reconcile_pv_input_devices(  # ruff: ignore[private-member-access]
        hass, entry, cast("Any", coordinator), seen
    )

    child = devices.async_get_child_device_by_identifier(
        (DOMAIN, f"{_DEVICE_ID}_pv_input_1"), entry.entry_id
    )
    assert child is not None
    assert child.parent_device_id == parent.id
    for unique_id in pv_ids:
        entity_id = entities.async_get_entity_id("sensor", DOMAIN, unique_id)
        assert entity_id is not None
        registered = entities.async_get(entity_id)
        assert registered is not None
        assert registered.device_id == child.id
        assert registered.unique_id == unique_id
    head_id = entities.async_get_entity_id("sensor", DOMAIN, f"{_DEVICE_ID}_bat_soc")
    assert head_id is not None
    head = entities.async_get(head_id)
    assert head is not None
    assert head.device_id == parent.id
    assert seen == {(_DEVICE_ID, 1)}


def test_channel_and_ct_period_descriptions_share_their_device_roles() -> None:
    """Every period variant joins its matching PV input or Smart Meter."""
    roles = {d.key: d.device_registry_role for d in STAT_DESCRIPTIONS}
    for channel in range(1, 5):
        for period in ("day", "week", "month", "year"):
            assert roles[f"device_pv{channel}_{period}_energy"] == (
                f"pv_input_{channel}"
            )
    for direction in ("input", "output"):
        for period in ("day", "week", "month", "year"):
            assert roles[f"ct_{direction}_{period}_energy"] == "smart_meter"


def test_smart_plug_base_name_falls_back_to_jackery_device_id() -> None:
    """Blank name fields yield a "Jackery {device_id}" prefix, not "SolarVault"."""
    info = _entity({})._build_smart_plug_device_info(1, {})  # ruff: ignore[private-member-access]

    name = info["name"]
    assert name is not None
    assert name.startswith(f"Jackery {_DEVICE_ID}")
    assert "SolarVault" not in name


def test_smart_plug_device_info_exposes_mac_connection() -> None:
    """Accessory MACs are HA device-registry connections, not just names."""
    info = _entity({})._build_smart_plug_device_info(  # ruff: ignore[private-member-access]
        1,
        {FIELD_MAC: "AA-BB-CC-11-22-33"},
    )

    assert info["connections"] == {(dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:11:22:33")}


def test_breaker_switch_falls_back_to_jackery() -> None:
    """A HomePower breaker with blank name fields is never labelled "SolarVault"."""
    entity = _bound(JackeryBreakerSwitch, {})
    info = entity._build_breaker_device_info(0, {}, "breaker_0")  # ruff: ignore[private-member-access]

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert info["model"] == "Jackery Sicherung"
    assert "SolarVault" not in info["name"]
    assert "SolarVault" not in str(info["model"])


def test_subdevice_alarm_binary_sensor_falls_back_to_jackery() -> None:
    """A HomePower accessory with blank name/model fields is never "SolarVault"."""
    entity = _bound(JackerySubdeviceAlarmBinarySensor, {}, _sub_device_sn="SN1")
    info = entity._build_sub_device_device_info(0, {}, "sub_0")  # ruff: ignore[private-member-access]

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert info["model"] == "Jackery accessory"
    assert "SolarVault" not in info["name"]
    assert "SolarVault" not in str(info["model"])


def test_battery_pack_sensor_falls_back_to_jackery() -> None:
    """A HomePower battery pack with blank name/model fields is never "SolarVault"."""
    entity = _bound(
        JackeryBatteryPackSensor,
        {},
        _pack_index=1,
        _pack_sn=None,
        _pack_key=stable_subdevice_key("battery_pack", None, 1),
    )
    info: dict[str, Any] = dict(entity.device_info)

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert info["model"] == "Jackery battery pack"
    assert "SolarVault" not in info["name"]
    assert "SolarVault" not in str(info["model"])


def test_breaker_sensor_falls_back_to_jackery() -> None:
    """A HomePower breaker sensor with blank name fields is never "SolarVault"."""
    entity = _bound(JackeryBreakerSensor, {})
    info = entity._build_breaker_device_info(0, {}, "breaker_0")  # ruff: ignore[private-member-access]

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert info["model"] == "Jackery Sicherung"
    assert "SolarVault" not in info["name"]
    assert "SolarVault" not in str(info["model"])


def test_subdevice_alarm_sensor_falls_back_to_jackery() -> None:
    """A HomePower accessory sensor with blank fields is never "SolarVault"."""
    entity = _bound(JackerySubdeviceAlarmSensor, {}, _sub_device_sn="SN1")
    info = entity._build_sub_device_device_info(0, {}, "sub_0")  # ruff: ignore[private-member-access]

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert info["model"] == "Jackery accessory"
    assert "SolarVault" not in info["name"]
    assert "SolarVault" not in str(info["model"])


def test_meter_head_sensor_base_name_falls_back_to_jackery() -> None:
    """A HomePower meter head with blank name fields is never "SolarVault"."""
    entity = _bound(
        JackeryMeterHeadSensor,
        {},
        _meter_head_index=1,
        _meter_head_sn=None,
        _meter_head_key=None,
    )
    info: dict[str, Any] = dict(entity.device_info)

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert "SolarVault" not in info["name"]


def test_smart_meter_sensor_base_name_falls_back_to_jackery() -> None:
    """A HomePower CT/smart-meter with blank name fields is never "SolarVault"."""
    entity = _bound(JackerySmartMeterSensor, {})
    info: dict[str, Any] = dict(entity.device_info)

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert "SolarVault" not in info["name"]


def test_smart_meter_device_info_exposes_mac_connection() -> None:
    """A Shelly/CT MAC lets HA merge the Jackery accessory device entry."""
    entity = _bound(
        JackerySmartMeterSensor,
        {PAYLOAD_CT_METER: {FIELD_MAC: "AABBCC445566"}},
    )

    assert dict(entity.device_info)["connections"] == {
        (dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:44:55:66")
    }


def test_smart_meter_device_info_uses_device_sn_as_mac_fallback() -> None:
    """A MAC-shaped deviceSn identifies current CT payloads without FIELD_MAC."""
    entity = _bound(
        JackerySmartMeterSensor,
        {PAYLOAD_CT_METER: {FIELD_DEVICE_SN: "5c013b048e3c"}},
    )

    assert dict(entity.device_info)["connections"] == {
        (dr.CONNECTION_NETWORK_MAC, "5c:01:3b:04:8e:3c")
    }


def test_smart_meter_device_info_uses_dev_id_as_mac_fallback() -> None:
    """A MAC-shaped devId must identify a CT without deviceSn or mac."""
    dev_id = "5c013b048e3c"
    entity = _bound(
        JackerySmartMeterSensor,
        {PAYLOAD_CT_METER: {FIELD_DEV_ID: dev_id}},
    )

    assert dict(entity.device_info)["connections"] == {
        (dr.CONNECTION_NETWORK_MAC, "5c:01:3b:04:8e:3c")
    }
    # The identifier is keyed on the accessory identity and must match the
    # target built by ``async_migrate_smart_meter_devices``. The constant
    # ``_smart_meter`` suffix is the legacy form that migration deletes, so
    # emitting it here would drop and re-create the device on every start.
    expected_key = stable_subdevice_key("smart_meter", dev_id, 1)
    assert entity.device_info["identifiers"] == {
        (DOMAIN, f"{_DEVICE_ID}_{expected_key}")
    }


def test_smart_meter_device_info_rejects_non_mac_serial_fallback() -> None:
    """An ordinary device serial must not be registered as a MAC connection."""
    entity = _bound(
        JackerySmartMeterSensor,
        {PAYLOAD_CT_METER: {FIELD_DEVICE_SN: "CT-SERIAL-123"}},
    )

    assert "connections" not in entity.device_info
