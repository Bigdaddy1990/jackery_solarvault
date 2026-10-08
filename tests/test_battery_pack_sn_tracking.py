"""A battery-pack sensor tracks its pack by serial, not list position.

Owner: the add-on battery ("Zusatzbatterie") showed duplicate/Unbekannt sets.
Index-only resolution (``packs[pack_index - 1]``) meant a reordered
``battery_packs`` list made an entity read a sibling pack's values or flip to
Unknown. The entity now pins its pack's serial on first resolution and matches
by serial thereafter.
"""

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import PropertyMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.jackery_solarvault as _init_module
from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FIELD_BAT_NUM,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_PROPERTIES,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    battery_pack_serial,
    merge_battery_pack_lists,
)
from custom_components.jackery_solarvault.sensor import (
    JackeryBatteryPackSensor,
    _SensorCollection,  # ruff: ignore[import-private-name]
    _collect_battery_packs,  # ruff: ignore[import-private-name]
)
from custom_components.jackery_solarvault.util import stable_subdevice_key
from homeassistant.helpers import device_registry as dr, entity_registry as er

_async_migrate_battery_pack_identities = (
    _init_module._async_migrate_battery_pack_identities  # ruff: ignore[private-member-access]
)
_async_remove_phantom_battery_pack_devices = (
    _init_module._async_remove_phantom_battery_pack_devices  # ruff: ignore[private-member-access]
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_SN_A = "HQ2C01400955HP3"
_SN_B = "HQ2C09990000ZZ9"
_SN_BEFORE_A = "HQ2C00000000AA0"
_SOC_A = 50
_SOC_B = 10
_PARENT_ID = "device-1"


def test_pack_cell_temperature_is_registered_from_complete_payload() -> None:
    """A per-pack cellTemp must produce its own temperature entity."""
    pack = {"deviceSn": _SN_A, "batSoc": _SOC_A, "cellTemp": 259}
    coordinator = _coordinator([pack])
    collection = _SensorCollection(
        coordinator=coordinator,
        seen_unique_ids=set(),
        battery_pack_identities={},
        create_smart_meter_derived=False,
        create_calculated_power=False,
        create_savings_details=False,
        entities=[],
    )

    _collect_battery_packs(
        collection,
        _PARENT_ID,
        {PAYLOAD_BATTERY_PACKS: [pack]},
        {FIELD_BAT_NUM: 1},
    )

    temperature = next(
        entity
        for entity in collection.entities
        if entity.entity_description.key == "cell_temperature"
    )
    assert isinstance(temperature, JackeryBatteryPackSensor)
    assert temperature._value_from_pack(pack) == pytest.approx(25.9)  # ruff: ignore[private-member-access]


def test_pack_without_known_serial_gets_no_index_identity() -> None:
    """No serial yet means no pack entity yet.

    Live 2026-09-26: an index-based pack identity became serial-based on the
    next restart, so every pack entity was re-registered and the user's enabled
    diagnostic entities came back disabled.
    """
    coordinator = _coordinator([])
    collection = _SensorCollection(
        coordinator=coordinator,
        seen_unique_ids=set(),
        battery_pack_identities={},
        create_smart_meter_derived=False,
        create_calculated_power=False,
        create_savings_details=False,
        entities=[],
    )

    _collect_battery_packs(collection, _PARENT_ID, {}, {FIELD_BAT_NUM: 2})

    assert collection.entities == []
    assert collection.battery_pack_identities == {}


def test_sparse_cell_temperature_frame_uses_known_pack_serial() -> None:
    """A cmd 107 pack temperature must not become main-device temperature."""
    current = {PAYLOAD_BATTERY_PACKS: [{"deviceSn": _SN_A, "outEgy": 237}]}
    classify = JackerySolarVaultCoordinator._is_known_battery_pack_frame  # ruff: ignore[private-member-access]
    assert classify(current, {"deviceSn": _SN_A, "cellTemp": 251, "cmd": 107})
    assert not classify(current, {"deviceSn": _SN_B, "cellTemp": 251, "cmd": 107})
    assert not classify(current, {"deviceSn": _SN_A, "cmd": 107})
    # After a restart the pack list can be empty: a non-head serial is a pack,
    # the head unit's own serial is not.
    empty: dict[str, Any] = {PAYLOAD_BATTERY_PACKS: []}
    head = {"HEAD-SN"}
    assert classify(empty, {"deviceSn": _SN_B, "cellTemp": 251, "cmd": 107}, head)
    assert not classify(empty, {"deviceSn": "HEAD-SN", "cellTemp": 179}, head)
    merged = merge_battery_pack_lists(
        current[PAYLOAD_BATTERY_PACKS],
        [{"deviceSn": _SN_A, "cellTemp": 251, "cmd": 107}],
    )
    assert merged == [{"deviceSn": _SN_A, "outEgy": 237, "cellTemp": 251, "cmd": 107}]


def _coordinator(
    packs: list[dict[str, Any]] | None = None,
) -> JackerySolarVaultCoordinator:
    """Build a real coordinator shell for battery-pack identity methods."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    shell = cast("Any", coordinator)
    shell.data = {
        _PARENT_ID: {PAYLOAD_BATTERY_PACKS: list(packs or [])},
    }
    shell._battery_pack_identity_overrides = {}  # ruff: ignore[private-member-access]
    return coordinator  # pyrefly: ignore [no-any-return-implicit]


def _entry(
    hass: HomeAssistant,
    coordinator: JackerySolarVaultCoordinator,
) -> MockConfigEntry:
    """Create an integration entry whose runtime data is the coordinator shell."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-1")
    entry.add_to_hass(hass)
    entry.runtime_data = coordinator
    return entry


def _parent_device(
    registry: dr.DeviceRegistry,
    entry: MockConfigEntry,
) -> dr.DeviceEntry:
    """Create the parent Jackery device for pack registry entries."""
    return registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, _PARENT_ID)},
        manufacturer="Jackery",
        name="Solar generator",
    )


def _pack_device(
    registry: dr.DeviceRegistry,
    entry: MockConfigEntry,
    identifier: str,
    *,
    serial_number: str | None = None,
) -> dr.DeviceEntry:
    """Create one battery-pack registry device under the test parent."""
    parent = registry.async_get_device_by_identifier(
        (DOMAIN, _PARENT_ID),
        entry.entry_id,
    )
    assert parent is not None, "parent device must be registered first"
    return registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, identifier)},
        name="Battery pack",
        serial_number=serial_number,
        via_device_id=parent.id,
    )


def _pack_entity(
    registry: er.EntityRegistry,
    entry: MockConfigEntry,
    device: dr.DeviceEntry,
    unique_id: str,
) -> er.RegistryEntry:
    """Create one sensor owned by a battery-pack registry device."""
    return registry.async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id,
        config_entry=entry,
        device_id=device.id,
        suggested_object_id="battery_pack_soc",
    )


def _serial_identifier(serial: str, index: int = 1) -> str:
    """Return the serial-backed device identifier used by production."""
    return f"{_PARENT_ID}_{stable_subdevice_key("battery_pack", serial, index)}"


def test_battery_pack_serial_resolves_common_fields() -> None:
    """The serial resolver accepts deviceSn/devSn/sn and rejects empties."""
    assert battery_pack_serial({"deviceSn": _SN_A}) == _SN_A
    assert battery_pack_serial({"sn": _SN_B}) == _SN_B
    assert battery_pack_serial({"batSoc": 5}) is None


def test_pack_firmware_version_updates_registered_device(
    hass: HomeAssistant,
) -> None:
    """Late BatteryPackSub version must reach device properties."""
    coordinator = _coordinator()
    entry = _entry(hass, coordinator)
    cast("Any", coordinator).config_entry = entry
    registry = dr.async_get(hass)
    _parent_device(registry, entry)
    pack_key = stable_subdevice_key("battery_pack", _SN_A, 1)
    device = _pack_device(
        registry,
        entry,
        f"{_PARENT_ID}_{pack_key}",
        serial_number=_SN_A,
    )
    sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
    sensor.hass = hass
    sensor._device_id = _PARENT_ID  # ruff: ignore[private-member-access]
    sensor._pack_key = pack_key  # ruff: ignore[private-member-access]
    sensor.coordinator = coordinator

    sensor._sync_device_version({"version": "1.4"})  # ruff: ignore[private-member-access]

    updated_device = registry.async_get(device.id)
    assert isinstance(updated_device, dr.DeviceEntry)
    assert updated_device.sw_version == "1.4"


def test_battery_pack_serial_prioritizes_device_sn_and_rejects_blank() -> None:
    """DeviceSn wins over devSn/sn, and a blank/whitespace-only value is None.

    A field that goes empty (e.g. ``""`` or all-whitespace) must not resolve
    to a falsy-but-truthy pack identity that would still pass an
    ``is not None`` check while breaking equality-based serial matching.
    """
    assert (
        battery_pack_serial({"deviceSn": _SN_A, "devSn": _SN_B, "sn": _SN_BEFORE_A})
        == _SN_A
    )
    assert battery_pack_serial({"devSn": _SN_B, "sn": _SN_BEFORE_A}) == _SN_B
    assert battery_pack_serial({"deviceSn": ""}) is None
    assert battery_pack_serial({"deviceSn": "   "}) is None
    assert battery_pack_serial({"deviceSn": f"  {_SN_A}  "}) == _SN_A


def test_pack_tracks_by_serial_after_sorted_position_shifts() -> None:
    """A newly inserted earlier serial cannot rebind the existing entity."""
    sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
    sensor._pack_index = 1  # ruff: ignore[private-member-access]
    sensor._pack_sn = None  # ruff: ignore[private-member-access]

    initial = {
        PAYLOAD_BATTERY_PACKS: [
            {"deviceSn": _SN_A, "batSoc": _SOC_A},
        ],
    }
    shifted = {
        PAYLOAD_BATTERY_PACKS: [
            {"deviceSn": _SN_A, "batSoc": _SOC_A},
            {"deviceSn": _SN_BEFORE_A, "batSoc": _SOC_B},
        ],
    }

    with patch.object(
        JackeryBatteryPackSensor,
        "_payload",
        new_callable=PropertyMock,
    ) as payload:
        payload.return_value = initial
        first: dict[str, Any] = sensor._pack  # ruff: ignore[private-member-access]
        assert first["deviceSn"] == _SN_A

        payload.return_value = shifted
        second: dict[str, Any] = sensor._pack  # ruff: ignore[private-member-access]
        assert second["deviceSn"] == _SN_A
        assert second["batSoc"] == _SOC_A


def test_pinned_pack_does_not_reassign_anonymous_rows() -> None:
    """Anonymous list positions cannot prove a serial-pinned pack's ownership."""
    sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
    sensor._pack_index = 2  # ruff: ignore[private-member-access]
    sensor._pack_sn = _SN_B  # ruff: ignore[private-member-access]
    with patch.object(
        JackeryBatteryPackSensor,
        "_payload",
        new_callable=PropertyMock,
        return_value={PAYLOAD_BATTERY_PACKS: [{"batSoc": _SOC_A}, {"batSoc": _SOC_B}]},
    ):
        assert sensor._pack == {}  # ruff: ignore[private-member-access]


def test_pinned_pack_does_not_bind_to_another_identified_pack() -> None:
    """A changed identified row cannot silently inherit a prior serial."""
    sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
    sensor._pack_index = 1  # ruff: ignore[private-member-access]
    sensor._pack_sn = _SN_A  # ruff: ignore[private-member-access]
    with patch.object(
        JackeryBatteryPackSensor,
        "_payload",
        new_callable=PropertyMock,
        return_value={PAYLOAD_BATTERY_PACKS: [{"deviceSn": _SN_B, "batSoc": _SOC_B}]},
    ):
        assert sensor._pack == {}  # ruff: ignore[private-member-access]


def test_battery_pack_index_binds_to_same_serial_across_restarts() -> None:
    """A registry-pinned serial still wins if live MQTT changes list order."""
    first_boot = {
        PAYLOAD_BATTERY_PACKS: [
            {"deviceSn": _SN_A, "batSoc": _SOC_A},
            {"deviceSn": _SN_B, "batSoc": _SOC_B},
        ],
    }
    # Same two packs, opposite arrival order -- simulating a later HA restart
    # where the vendor list came back in a different order.
    second_boot = {
        PAYLOAD_BATTERY_PACKS: [
            {"deviceSn": _SN_B, "batSoc": _SOC_B},
            {"deviceSn": _SN_A, "batSoc": _SOC_A},
        ],
    }

    def _resolve(
        pack_index: int, payload: dict[str, Any], trusted_serial: str
    ) -> str | None:
        sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
        sensor._pack_index = pack_index  # ruff: ignore[private-member-access]
        sensor._pack_sn = trusted_serial  # ruff: ignore[private-member-access]
        with patch.object(
            JackeryBatteryPackSensor,
            "_payload",
            new_callable=PropertyMock,
        ) as mock_payload:
            mock_payload.return_value = payload
            pack: dict[str, Any] = sensor._pack  # ruff: ignore[private-member-access]
            return battery_pack_serial(pack)

    assert _resolve(1, first_boot, _SN_A) == _SN_A
    assert _resolve(2, first_boot, _SN_B) == _SN_B
    assert _resolve(1, second_boot, _SN_A) == _SN_A
    assert _resolve(2, second_boot, _SN_B) == _SN_B


def test_first_pack_resolution_keeps_http_order_and_lifetime_owner() -> None:
    """Serial sorting must not move pack 3's lifetime counter to pack 1."""
    source = {
        PAYLOAD_BATTERY_PACKS: [
            {"deviceSn": "Z-PACK-1", "outEgy": 237},
            {"deviceSn": "Y-PACK-2", "outEgy": 219},
            {"deviceSn": "A-PACK-3", "outEgy": 33776},
        ],
    }
    for index, expected in ((1, 237), (2, 219), (3, 33776)):
        sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
        sensor._pack_index = index  # ruff: ignore[private-member-access]
        sensor._pack_sn = None  # ruff: ignore[private-member-access]
        with patch.object(
            JackeryBatteryPackSensor,
            "_payload",
            new_callable=PropertyMock,
            return_value=source,
        ):
            assert sensor._pack["outEgy"] == expected  # ruff: ignore[private-member-access]


def test_communication_state_derived_from_live_pack_presence() -> None:
    """A present, live pack reports a real communication_state, not "unknown".

    No transport (HTTP or BLE cmd=120) provides ``commState`` for this pack, so
    a raw field lookup yields ``None``. The sensor must instead derive the
    connected state (``commState == 1`` semantics) from the pack's live
    telemetry so the entity is not stuck on "unknown".
    """
    # ruff: ignore[import-outside-top-level]
    from custom_components.jackery_solarvault.sensor import (
        BATTERY_PACK_SENSOR_DESCRIPTIONS,
    )

    description = next(
        desc
        for desc in BATTERY_PACK_SENSOR_DESCRIPTIONS
        if desc.key == "communication_state"
    )
    sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
    sensor.entity_description = description

    fresh_pack = {"deviceSn": _SN_A, "batSoc": _SOC_A, "inPw": 286, "cellTemp": 259}
    assert sensor._value_from_pack(fresh_pack) == 1  # ruff: ignore[private-member-access]

    # An empty/absent pack (no live telemetry) stays unknown -> disconnected.
    assert sensor._value_from_pack({}) is None  # ruff: ignore[private-member-access]


def test_registry_migration_rekeys_pack_and_preserves_entity_id(
    hass: HomeAssistant,
) -> None:
    """A trustworthy live serial atomically replaces the positional identity."""
    coordinator = _coordinator([{"deviceSn": _SN_A}])
    entry = _entry(hass, coordinator)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    _parent_device(device_registry, entry)
    old_identifier = f"{_PARENT_ID}_battery_pack_1"
    pack = _pack_device(device_registry, entry, old_identifier)
    old_unique_id = f"{old_identifier}_state_of_charge"
    entity = _pack_entity(entity_registry, entry, pack, old_unique_id)
    entity_id = entity.entity_id

    _async_migrate_battery_pack_identities(hass, entry)

    new_identifier = _serial_identifier(_SN_A)
    migrated_pack = device_registry.async_get_device_by_identifier(
        (DOMAIN, new_identifier),
        entry.entry_id,
    )
    assert migrated_pack is not None
    assert migrated_pack.id == pack.id
    assert migrated_pack.serial_number == _SN_A
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, old_identifier),
            entry.entry_id,
        )
        is None
    )
    migrated_entity = entity_registry.async_get(entity_id)
    assert migrated_entity is not None
    assert migrated_entity.entity_id == entity_id
    assert migrated_entity.unique_id == f"{new_identifier}_state_of_charge"

    cast("Any", coordinator).data = {
        _PARENT_ID: {PAYLOAD_BATTERY_PACKS: [{"deviceSn": _SN_B}]},
    }
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 1) == _SN_A


@pytest.mark.parametrize("numeric_serial", [None, _SN_A])
def test_existing_serial_target_removes_only_duplicate_numeric_device(
    hass: HomeAssistant,
    numeric_serial: str | None,
) -> None:
    """A serial target keeps legacy entity ids while removing its fallback."""
    coordinator = _coordinator([{"deviceSn": _SN_A}])
    cast("Any", coordinator).data[_PARENT_ID][PAYLOAD_PROPERTIES] = {
        FIELD_BAT_NUM: 1,
    }
    entry = _entry(hass, coordinator)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    _parent_device(device_registry, entry)

    serial_identifier = _serial_identifier(_SN_A)
    serial_device = _pack_device(
        device_registry,
        entry,
        serial_identifier,
        serial_number=_SN_A,
    )
    numeric_identifier = f"{_PARENT_ID}_battery_pack_1"
    numeric_device = _pack_device(
        device_registry,
        entry,
        numeric_identifier,
        serial_number=numeric_serial,
    )
    numeric_entities = {
        key: _pack_entity(
            entity_registry,
            entry,
            numeric_device,
            f"{numeric_identifier}_{key}",
        )
        for key in (
            "state_of_charge",
            "lifetime_charge_energy",
            "lifetime_discharge_energy",
        )
    }
    duplicate_entity_ids = {
        key: _pack_entity(
            entity_registry,
            entry,
            serial_device,
            f"{serial_identifier}_{key}",
        ).entity_id
        for key in (
            "state_of_charge",
            "lifetime_charge_energy",
            "lifetime_discharge_energy",
        )
    }
    user_entity = entity_registry.async_get_or_create(
        "sensor",
        "manual",
        "user-owned-pack-note",
        config_entry=None,
        device_id=numeric_device.id,
        suggested_object_id="user_pack_note",
    )

    _async_migrate_battery_pack_identities(hass, entry)
    _async_remove_phantom_battery_pack_devices(hass, entry)

    assert device_registry.async_get(serial_device.id) is not None
    assert serial_device.config_entry_id == entry.entry_id
    removed_fallback = device_registry.async_get(numeric_device.id)
    assert (
        removed_fallback is None or removed_fallback.config_entry_id != entry.entry_id
    )
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 1) == _SN_A
    for key, legacy_entity in numeric_entities.items():
        preserved_entity = entity_registry.async_get(legacy_entity.entity_id)
        assert preserved_entity is not None
        assert preserved_entity.entity_id == legacy_entity.entity_id
        assert preserved_entity.unique_id == f"{serial_identifier}_{key}"
        assert preserved_entity.device_id == serial_device.id
    for duplicate_entity_id in duplicate_entity_ids.values():
        assert entity_registry.async_get(duplicate_entity_id) is None
    preserved_user_entity = entity_registry.async_get(user_entity.entity_id)
    assert preserved_user_entity is not None
    assert preserved_user_entity.entity_id == user_entity.entity_id
    assert preserved_user_entity.unique_id == user_entity.unique_id

    _async_migrate_battery_pack_identities(hass, entry)
    _async_remove_phantom_battery_pack_devices(hass, entry)

    assert device_registry.async_get(serial_device.id) is not None
    assert entity_registry.async_get(user_entity.entity_id) == preserved_user_entity


@pytest.mark.parametrize("collision_owner", ["other_entry", "other_device"])
def test_serial_collision_outside_target_preserves_all_entities(
    hass: HomeAssistant,
    collision_owner: str,
) -> None:
    """An unrelated serial entity prevents the entire numeric pack migration."""
    coordinator = _coordinator([{"deviceSn": _SN_A}])
    cast("Any", coordinator).data[_PARENT_ID][PAYLOAD_PROPERTIES] = {
        FIELD_BAT_NUM: 1,
    }
    entry = _entry(hass, coordinator)
    other_entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-2")
    other_entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    parent = _parent_device(device_registry, entry)
    serial_identifier = _serial_identifier(_SN_A)
    serial_device = _pack_device(
        device_registry, entry, serial_identifier, serial_number=_SN_A
    )
    numeric_identifier = f"{_PARENT_ID}_battery_pack_1"
    numeric_device = _pack_device(
        device_registry, entry, numeric_identifier, serial_number=_SN_A
    )
    numeric_entities = [
        _pack_entity(
            entity_registry, entry, numeric_device, f"{numeric_identifier}_{key}"
        )
        for key in ("state_of_charge", "lifetime_charge_energy")
    ]
    serial_entity = _pack_entity(
        entity_registry, entry, serial_device, f"{serial_identifier}_state_of_charge"
    )
    collision = _pack_entity(
        entity_registry,
        other_entry if collision_owner == "other_entry" else entry,
        parent if collision_owner == "other_device" else serial_device,
        f"{serial_identifier}_lifetime_charge_energy",
    )

    for _ in range(2):
        _async_migrate_battery_pack_identities(hass, entry)
        _async_remove_phantom_battery_pack_devices(hass, entry)

        assert device_registry.async_get(numeric_device.id) == numeric_device
        assert device_registry.async_get(serial_device.id) == serial_device
        for entity in [*numeric_entities, serial_entity, collision]:
            assert entity_registry.async_get(entity.entity_id) == entity


def test_duplicate_legacy_serial_targets_keep_one_canonical_pack(
    hass: HomeAssistant,
) -> None:
    """Two old numeric entries for one serial collapse to one serial device."""
    coordinator = _coordinator([{"deviceSn": _SN_A}, {"deviceSn": _SN_A}])
    cast("Any", coordinator).data[_PARENT_ID][PAYLOAD_PROPERTIES] = {
        FIELD_BAT_NUM: 2,
    }
    entry = _entry(hass, coordinator)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    _parent_device(device_registry, entry)

    first_identifier = f"{_PARENT_ID}_battery_pack_1"
    second_identifier = f"{_PARENT_ID}_battery_pack_2"
    first_device = _pack_device(
        device_registry, entry, first_identifier, serial_number=_SN_A
    )
    second_device = _pack_device(
        device_registry, entry, second_identifier, serial_number=_SN_A
    )
    first_entity = _pack_entity(
        entity_registry, entry, first_device, f"{first_identifier}_state_of_charge"
    )
    second_entity = _pack_entity(
        entity_registry, entry, second_device, f"{second_identifier}_state_of_charge"
    )

    _async_migrate_battery_pack_identities(hass, entry)
    _async_remove_phantom_battery_pack_devices(hass, entry)

    serial_identifier = _serial_identifier(_SN_A)
    serial_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, serial_identifier),
        entry.entry_id,
    )
    assert serial_device is not None
    assert serial_device.id in {first_device.id, second_device.id}
    serial_entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{serial_identifier}_state_of_charge"
    )
    assert serial_entity_id in {first_entity.entity_id, second_entity.entity_id}
    duplicate_id = ({first_device.id, second_device.id} - {serial_device.id}).pop()
    duplicate = device_registry.async_get(duplicate_id)
    assert duplicate is None or duplicate.config_entry_id != entry.entry_id


def test_registry_migration_skips_stored_live_serial_conflict(
    hass: HomeAssistant,
) -> None:
    """Conflicting stored and live serials keep the complete old identity."""
    coordinator = _coordinator([{"deviceSn": _SN_B}])
    entry = _entry(hass, coordinator)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    _parent_device(device_registry, entry)
    old_identifier = f"{_PARENT_ID}_battery_pack_1"
    pack = _pack_device(
        device_registry,
        entry,
        old_identifier,
        serial_number=_SN_A,
    )
    old_unique_id = f"{old_identifier}_state_of_charge"
    entity = _pack_entity(entity_registry, entry, pack, old_unique_id)

    _async_migrate_battery_pack_identities(hass, entry)

    preserved_pack = device_registry.async_get_device_by_identifier(
        (DOMAIN, old_identifier),
        entry.entry_id,
    )
    assert preserved_pack is not None
    assert preserved_pack.id == pack.id
    assert preserved_pack.serial_number == _SN_A
    preserved_entity = entity_registry.async_get(entity.entity_id)
    assert preserved_entity is not None
    assert preserved_entity.unique_id == old_unique_id
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 1) is None


def test_registry_migration_entity_collision_has_no_partial_writes(
    hass: HomeAssistant,
) -> None:
    """A target unique-ID collision leaves device and entity identities intact."""
    coordinator = _coordinator([{"deviceSn": _SN_A}])
    entry = _entry(hass, coordinator)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    parent = _parent_device(device_registry, entry)
    old_identifier = f"{_PARENT_ID}_battery_pack_1"
    pack = _pack_device(device_registry, entry, old_identifier)
    old_unique_id = f"{old_identifier}_state_of_charge"
    entity = _pack_entity(entity_registry, entry, pack, old_unique_id)
    new_identifier = _serial_identifier(_SN_A)
    collision = _pack_entity(
        entity_registry,
        entry,
        parent,
        f"{new_identifier}_state_of_charge",
    )

    _async_migrate_battery_pack_identities(hass, entry)

    preserved_pack = device_registry.async_get_device_by_identifier(
        (DOMAIN, old_identifier),
        entry.entry_id,
    )
    assert preserved_pack is not None
    assert preserved_pack.id == pack.id
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, new_identifier),
            entry.entry_id,
        )
        is None
    )
    preserved_entity = entity_registry.async_get(entity.entity_id)
    assert preserved_entity is not None
    assert preserved_entity.unique_id == old_unique_id
    assert entity_registry.async_get(collision.entity_id) is not None
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 1) is None


def test_parent_attached_serialless_packs_keep_distinct_index_devices(
    hass: HomeAssistant,
) -> None:
    """Identical serial-less packs use stable index identities, not metadata hashes."""
    coordinator = _coordinator([
        {"modelName": "same", "version": "1.0"},
        {"modelName": "same", "version": "1.0"},
    ])
    entry = _entry(hass, coordinator)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    parent = _parent_device(device_registry, entry)
    first = _pack_entity(
        entity_registry,
        entry,
        parent,
        f"{_PARENT_ID}_battery_pack_1_state_of_charge",
    )
    second = _pack_entity(
        entity_registry,
        entry,
        parent,
        f"{_PARENT_ID}_battery_pack_2_state_of_charge",
    )

    _async_migrate_battery_pack_identities(hass, entry)

    first_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{_PARENT_ID}_battery_pack_1"),
        entry.entry_id,
    )
    second_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{_PARENT_ID}_battery_pack_2"),
        entry.entry_id,
    )
    assert first_device is not None
    assert second_device is not None
    assert first_device.id != second_device.id
    assert first_device.serial_number is None
    assert second_device.serial_number is None
    migrated_first = entity_registry.async_get(first.entity_id)
    migrated_second = entity_registry.async_get(second.entity_id)
    assert migrated_first is not None
    assert migrated_second is not None
    assert migrated_first.device_id == first_device.id
    assert migrated_second.device_id == second_device.id


def test_registry_seed_matches_live_pack_before_offline_records(
    hass: HomeAssistant,
) -> None:
    """Live matches win while observed and protected indices remain reserved.

    The migration only seeds observed-serial lookups for packs present in the
    payload. Registry devices without a matching live or stored serial do not
    bind any coordinator identity slot — they keep their original registry
    identifiers and stay invisible to ``battery_pack_identity_serial`` until a
    future payload snapshot re-resolves them.
    """
    coordinator = _coordinator([{"deviceSn": _SN_A}])
    entry = _entry(hass, coordinator)
    device_registry = dr.async_get(hass)
    _parent_device(device_registry, entry)
    _pack_device(
        device_registry,
        entry,
        _serial_identifier(_SN_A),
        serial_number=_SN_A,
    )
    _pack_device(
        device_registry,
        entry,
        _serial_identifier(_SN_B),
        serial_number=_SN_B,
    )
    _pack_device(
        device_registry,
        entry,
        f"{_PARENT_ID}_battery_pack_2",
    )

    _async_migrate_battery_pack_identities(hass, entry)

    # Only pack index 1 has a live payload match; orphan registry devices do
    # not bind a coordinator identity slot and resolve to None.
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 1) == _SN_A
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 2) is None
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 3) is None


@pytest.mark.parametrize(
    ["frozen_serial", "live_serial", "expected"],
    [
        pytest.param(None, _SN_A, None, id="frozen-unknown"),
        pytest.param(_SN_A, _SN_B, _SN_A, id="frozen-serial"),
    ],
)
def test_frozen_registry_identity_overrides_live_payload(
    frozen_serial: str | None,
    live_serial: str,
    expected: str | None,
) -> None:
    """A session override, including None, wins over later live serial data."""
    coordinator = _coordinator([{"deviceSn": live_serial}])
    coordinator.set_battery_pack_identity_override(
        _PARENT_ID,
        1,
        frozen_serial,
    )

    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 1) == expected


def test_collection_preserves_explicit_unknown_identity() -> None:
    """Registration must not undo an ambiguity guard by rereading a raw serial."""
    pack = {"deviceSn": _SN_A, "inEgy": 36541}
    coordinator = _coordinator([pack])
    coordinator.set_battery_pack_identity_override(_PARENT_ID, 1, None)
    collection = _SensorCollection(
        coordinator=coordinator,
        seen_unique_ids=set(),
        battery_pack_identities={},
        create_smart_meter_derived=False,
        create_calculated_power=False,
        create_savings_details=False,
        entities=[],
    )
    _collect_battery_packs(
        collection,
        _PARENT_ID,
        {PAYLOAD_BATTERY_PACKS: [pack]},
        {FIELD_BAT_NUM: 1},
    )
    assert collection.entities == []
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 1) is None


@pytest.mark.parametrize("initial_indices", [[], [0], [1], [2], [2, 1, 0]])
@pytest.mark.parametrize("reported_count", [None, 3])
def test_registered_pack_numbers_survive_incomplete_reload(
    hass: HomeAssistant,
    initial_indices: list[int],
    reported_count: int | None,
) -> None:
    """Partial reload snapshots preserve serial-owned histories and pack numbers."""
    serials = ["HQ2C01400094HP3", "HQ2C01600246HP3", "HQ2C01400955HP3"]
    counters = [1232, 1127, 36541]
    packs = [
        {"deviceSn": serial, "inEgy": counter}
        for serial, counter in zip(serials, counters, strict=True)
    ]
    initial_packs = [packs[index] for index in initial_indices]
    coordinator = _coordinator(initial_packs)
    props = {} if reported_count is None else {FIELD_BAT_NUM: reported_count}
    entry = _entry(hass, coordinator)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    _parent_device(devices, entry)
    for index, serial in enumerate(serials, start=1):
        device = _pack_device(
            devices, entry, _serial_identifier(serial), serial_number=serial
        )
        registered = _pack_entity(
            entities,
            entry,
            device,
            f"{_serial_identifier(serial)}_lifetime_charge_energy",
        )
        entities.async_update_entity(
            registered.entity_id,
            new_entity_id=(
                f"sensor.solarvault_3_pro_max_zusatzbatterie_{index}_"
                "zusatzbatterie_lebensenergie_geladen"
            ),
        )

    _async_migrate_battery_pack_identities(hass, entry)
    for index, serial in enumerate(serials, start=1):
        assert coordinator.battery_pack_identity_serial(_PARENT_ID, index) == serial
    collection = _SensorCollection(
        coordinator=coordinator,
        seen_unique_ids=set(),
        battery_pack_identities={},
        create_smart_meter_derived=False,
        create_calculated_power=False,
        create_savings_details=False,
        entities=[],
    )
    _collect_battery_packs(
        collection,
        _PARENT_ID,
        {PAYLOAD_BATTERY_PACKS: initial_packs},
        props,
    )
    for index, serial in enumerate(serials):
        sensor = next(
            entity
            for entity in collection.entities
            if entity.unique_id
            == f"{_serial_identifier(serial)}_lifetime_charge_energy"
        )
        assert isinstance(sensor, JackeryBatteryPackSensor)
        value = sensor._value_from_pack(sensor._pack)  # ruff: ignore[private-member-access]
        if index in initial_indices:
            assert value == pytest.approx(counters[index] / 100)
        else:
            assert value is None
    cast("Any", coordinator).data[_PARENT_ID][PAYLOAD_BATTERY_PACKS] = list(
        reversed(packs)
    )
    _collect_battery_packs(
        collection,
        _PARENT_ID,
        {PAYLOAD_BATTERY_PACKS: list(reversed(packs))},
        props,
    )

    for index, (serial, counter) in enumerate(
        zip(serials, counters, strict=True), start=1
    ):
        assert collection.battery_pack_identities[_PARENT_ID, index][0] == serial
        sensor = next(
            entity
            for entity in collection.entities
            if entity.unique_id
            == f"{_serial_identifier(serial)}_lifetime_charge_energy"
        )
        assert isinstance(sensor, JackeryBatteryPackSensor)
        assert sensor._value_from_pack(sensor._pack) == pytest.approx(counter / 100)  # ruff: ignore[private-member-access]
        assert sensor.device_info["serial_number"] == serial
        device_name = sensor.device_info["name"]
        assert device_name is not None
        assert device_name.endswith(f" {index}")


@pytest.mark.parametrize("frozen_parent", [_PARENT_ID, "device-2"])
@pytest.mark.parametrize("frozen_serial", [_SN_A, _SN_A.lower()])
def test_frozen_pack_serial_does_not_claim_another_live_index(
    frozen_parent: str,
    frozen_serial: str,
) -> None:
    """A partial roster cannot register a known serial at another display index."""
    coordinator = _coordinator([{"deviceSn": _SN_A, "inEgy": 36541}])
    coordinator.set_battery_pack_identity_override(frozen_parent, 3, frozen_serial)

    expected = None if frozen_parent == _PARENT_ID else _SN_A
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 1) == expected
    assert coordinator.battery_pack_identity_serial(frozen_parent, 3) == frozen_serial


@pytest.mark.parametrize("legacy_index", [2, 4])
async def test_numeric_pack_cleanup_preserves_history_after_roster_reordering(
    hass: HomeAssistant, legacy_index: int
) -> None:
    """A known live serial remains valid when its legacy index no longer matches."""
    coordinator = _coordinator([
        {"deviceSn": _SN_A},
        {"deviceSn": _SN_B},
        {"deviceSn": _SN_BEFORE_A},
    ])
    cast("Any", coordinator).data[_PARENT_ID][PAYLOAD_PROPERTIES] = {FIELD_BAT_NUM: 3}
    entry = _entry(hass, coordinator)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    _parent_device(devices, entry)
    serial_identifier = _serial_identifier(_SN_A)
    target = _pack_device(devices, entry, serial_identifier, serial_number=_SN_A)
    old_identifier = f"{_PARENT_ID}_battery_pack_{legacy_index}"
    legacy = _pack_device(devices, entry, old_identifier, serial_number=_SN_A)
    original_ids: dict[str, str] = {}
    for key, label in (
        ("state_of_charge", "soc"),
        ("charge_power", "ladeleistung"),
        ("discharge_power", "entladeleistung"),
    ):
        registered = _pack_entity(entities, entry, legacy, f"{old_identifier}_{key}")
        entity_id = (
            f"sensor.solarvault_3_pro_max_zusatzbatterie_{legacy_index}_"
            f"zusatzbatterie_{label}"
        )
        entities.async_update_entity(registered.entity_id, new_entity_id=entity_id)
        original_ids[key] = entity_id

    for _ in range(2):
        _async_migrate_battery_pack_identities(hass, entry)
        _async_remove_phantom_battery_pack_devices(hass, entry)
        await hass.async_block_till_done()
        for key, entity_id in original_ids.items():
            preserved = entities.async_get(entity_id)
            assert preserved is not None, f"Lost history owner {entity_id}"
            assert preserved.unique_id == f"{serial_identifier}_{key}"
            assert preserved.device_id == target.id


@pytest.mark.parametrize(
    "serial",
    [
        "\x02 8f\x05 \x01",
        "PACK\x00SN",
        "PACK\x7fSN",
        {"sn": "PACK"},
        ["PACK"],
        True,
        12,
    ],
)
def test_pack_identity_never_coerces_corrupt_or_structured_serial(
    serial: object,
) -> None:
    """Malformed identifiers cannot create phantom hardware identities."""
    payload = {"deviceSn": serial, "devType": 1, "batSoc": 45}
    assert battery_pack_serial(payload) is None
    assert payload["deviceSn"] is serial


def test_corrupt_fourth_pack_keeps_raw_row_without_registering_phantom() -> None:
    """Preserve diagnostic evidence without assigning corrupt telemetry to hardware."""
    packs = [
        {"deviceSn": _SN_A, "batSoc": 33},
        {"deviceSn": _SN_B, "batSoc": 29},
        {"deviceSn": _SN_BEFORE_A, "batSoc": 38},
        {"deviceSn": "\x02 8f\x05 \x01", "devType": 1, "batSoc": 45, "outPw": 21},
    ]
    coordinator = _coordinator(packs)
    collection = _SensorCollection(
        coordinator=coordinator,
        seen_unique_ids=set(),
        battery_pack_identities={},
        create_smart_meter_derived=False,
        create_calculated_power=False,
        create_savings_details=False,
        entities=[],
    )
    _collect_battery_packs(
        collection, _PARENT_ID, {PAYLOAD_BATTERY_PACKS: packs}, {FIELD_BAT_NUM: 3}
    )
    assert set(collection.battery_pack_identities) == {
        (_PARENT_ID, 1),
        (_PARENT_ID, 2),
        (_PARENT_ID, 3),
    }
    assert coordinator.data[_PARENT_ID][PAYLOAD_BATTERY_PACKS] == packs


@pytest.mark.parametrize(
    "serial", ["\x02 8f\x05 \x01", {"sn": "PACK"}, ["PACK"], False, 0, [], {}]
)
def test_corrupt_pack_updates_never_overwrite_an_identified_pack(
    serial: object,
) -> None:
    """Keep opaque raw evidence separate and stable across repeated updates."""
    original = {"deviceSn": _SN_A, "batSoc": 33}
    incoming = {"deviceSn": serial, "batSoc": 45}
    first = merge_battery_pack_lists([original], [incoming])
    second = merge_battery_pack_lists(first, [{**incoming, "batSoc": 46}])
    assert first[0] == original
    assert second == [original, {"deviceSn": serial, "batSoc": 46}]
    assert incoming == {"deviceSn": serial, "batSoc": 45}


def test_corrupt_frozen_identity_cannot_register_a_phantom_pack() -> None:
    """Previously persisted corrupt serials are not trusted as hardware IDs."""
    coordinator = _coordinator()
    coordinator.set_battery_pack_identity_override(_PARENT_ID, 4, "\x02 8f\x05 \x01")
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 4) is None


@pytest.mark.parametrize("identity", [False, 0, [], {}])
def test_corrupt_primary_serial_does_not_fall_back_to_an_alias(
    identity: object,
) -> None:
    """A malformed explicit identity is not evidence for a different field owner."""
    assert battery_pack_serial({"deviceSn": identity, "devSn": _SN_A}) is None


def test_complete_topology_does_not_recreate_a_stale_registered_pack(
    hass: HomeAssistant,
) -> None:
    """A removed pack's stored entity index cannot override complete live data."""
    coordinator = _coordinator([{"deviceSn": _SN_A}])
    assert coordinator.data is not None
    coordinator.data[_PARENT_ID][PAYLOAD_PROPERTIES] = {FIELD_BAT_NUM: 1}
    entry = _entry(hass, coordinator)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    _parent_device(devices, entry)
    for index, serial in enumerate([_SN_A, _SN_B], start=1):
        device = _pack_device(
            devices, entry, _serial_identifier(serial), serial_number=serial
        )
        registered = _pack_entity(
            entities,
            entry,
            device,
            f"{_serial_identifier(serial)}_lifetime_charge_energy",
        )
        entities.async_update_entity(
            registered.entity_id,
            new_entity_id=f"sensor.zusatzbatterie_{index}_lebensenergie_geladen",
        )
    _async_migrate_battery_pack_identities(hass, entry)
    _async_remove_phantom_battery_pack_devices(hass, entry)
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 1) == _SN_A
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 2) is None
    collection = _SensorCollection(
        coordinator=coordinator,
        seen_unique_ids=set(),
        battery_pack_identities={},
        create_smart_meter_derived=False,
        create_calculated_power=False,
        create_savings_details=False,
        entities=[],
    )
    _collect_battery_packs(
        collection, _PARENT_ID, coordinator.data[_PARENT_ID], {FIELD_BAT_NUM: 1}
    )
    assert all(
        entity.unique_id is not None and _SN_B.lower() not in entity.unique_id
        for entity in collection.entities
    )
