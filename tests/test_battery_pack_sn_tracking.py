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
from custom_components.jackery_solarvault.const import DOMAIN, PAYLOAD_BATTERY_PACKS
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    battery_pack_serial,
)
from custom_components.jackery_solarvault.sensor import JackeryBatteryPackSensor
from custom_components.jackery_solarvault.util import stable_subdevice_key
from homeassistant.helpers import device_registry as dr, entity_registry as er

_async_migrate_battery_pack_identities = (
    _init_module._async_migrate_battery_pack_identities
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_SN_A = "HQ2C01400955HP3"
_SN_B = "HQ2C09990000ZZ9"
_SN_BEFORE_A = "HQ2C00000000AA0"
_SOC_A = 50
_SOC_B = 10
_PARENT_ID = "device-1"


def _coordinator(
    packs: list[dict[str, Any]] | None = None,
) -> JackerySolarVaultCoordinator:
    """Build a real coordinator shell for battery-pack identity methods."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    shell = cast("Any", coordinator)
    shell.data = {
        _PARENT_ID: {PAYLOAD_BATTERY_PACKS: list(packs or [])},
    }
    shell._battery_pack_identity_overrides = {}
    return coordinator


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
    return registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, identifier)},
        name="Battery pack",
        serial_number=serial_number,
        via_device=(DOMAIN, _PARENT_ID),
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
    sensor._pack_index = 1
    sensor._pack_sn = None

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
        first: dict[str, Any] = sensor._pack
        assert first["deviceSn"] == _SN_A

        payload.return_value = shifted
        second: dict[str, Any] = sensor._pack
        assert second["deviceSn"] == _SN_A
        assert second["batSoc"] == _SOC_A


def test_battery_pack_index_binds_to_same_serial_across_restarts() -> None:
    """Index N binds to the same physical serial regardless of list order.

    Each HA restart recreates the entity with ``_pack_sn=None``, so the
    within-session serial pin (see ``test_pack_tracks_by_serial_after_reorder``)
    cannot protect against reordering that happens *between* restarts: a fresh
    entity's first positional resolution (``pack_dicts[pack_index - 1]``)
    would otherwise pin to whichever pack the cloud/MQTT list happens to put
    at that position this session, silently rebinding ``battery_pack_N``'s
    stable unique_id to a different physical serial and contaminating
    Recorder history with no indication.
    """
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

    def _resolve(pack_index: int, payload: dict[str, Any]) -> str | None:
        sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
        sensor._pack_index = pack_index
        sensor._pack_sn = None  # fresh entity, as after a restart
        with patch.object(
            JackeryBatteryPackSensor,
            "_payload",
            new_callable=PropertyMock,
        ) as mock_payload:
            mock_payload.return_value = payload
            pack: dict[str, Any] = sensor._pack
            return battery_pack_serial(pack)

    assert _resolve(1, first_boot) == _SN_A
    assert _resolve(2, first_boot) == _SN_B
    assert _resolve(1, second_boot) == _SN_A
    assert _resolve(2, second_boot) == _SN_B


def test_communication_state_derived_from_live_pack_presence() -> None:
    """A present, live pack reports a real communication_state, not "unknown".

    No transport (HTTP or BLE cmd=120) provides ``commState`` for this pack, so
    a raw field lookup yields ``None``. The sensor must instead derive the
    connected state (``commState == 1`` semantics) from the pack's live
    telemetry so the entity is not stuck on "unknown".
    """
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
    assert sensor._value_from_pack(fresh_pack) == 1

    # An empty/absent pack (no live telemetry) stays unknown -> disconnected.
    assert sensor._value_from_pack({}) is None


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
    migrated_pack = device_registry.async_get_device(
        identifiers={(DOMAIN, new_identifier)},
    )
    assert migrated_pack is not None
    assert migrated_pack.id == pack.id
    assert migrated_pack.serial_number == _SN_A
    assert (
        device_registry.async_get_device(identifiers={(DOMAIN, old_identifier)}) is None
    )
    migrated_entity = entity_registry.async_get(entity_id)
    assert migrated_entity is not None
    assert migrated_entity.entity_id == entity_id
    assert migrated_entity.unique_id == f"{new_identifier}_state_of_charge"

    cast("Any", coordinator).data = {
        _PARENT_ID: {PAYLOAD_BATTERY_PACKS: [{"deviceSn": _SN_B}]},
    }
    assert coordinator.battery_pack_identity_serial(_PARENT_ID, 1) == _SN_A


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

    preserved_pack = device_registry.async_get_device(
        identifiers={(DOMAIN, old_identifier)},
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

    preserved_pack = device_registry.async_get_device(
        identifiers={(DOMAIN, old_identifier)},
    )
    assert preserved_pack is not None
    assert preserved_pack.id == pack.id
    assert (
        device_registry.async_get_device(identifiers={(DOMAIN, new_identifier)}) is None
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

    first_device = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{_PARENT_ID}_battery_pack_1")},
    )
    second_device = device_registry.async_get_device(
        identifiers={(DOMAIN, f"{_PARENT_ID}_battery_pack_2")},
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
