"""Proven cloud aliases preserve one captured socket identity across HA reloads."""

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FIELD_ACCESSORIES,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEV_ID,
    FIELD_DEV_TYPE,
    FIELD_ID,
    FIELD_IN_PW,
    PAYLOAD_SMART_PLUGS,
    SUBDEVICE_DEV_TYPE_SOCKET,
)
from custom_components.jackery_solarvault.coordinator import (
    smart_plug_entity_identity,
    smart_plug_entity_key,
    smart_plug_entity_payload,
    smart_plug_payloads,
)
from homeassistant.helpers import device_registry as dr, entity_registry as er

_PARENT = "parent-1"
_SERIAL = "SERIAL-A"
_CLOUD = "CLOUD-A"
_POWER = 17
_CLOUD_FIELDS = (FIELD_DEVICE_ID, FIELD_ID, FIELD_DEV_ID)


def _discovery(plug: dict[str, Any]) -> dict[str, Any]:
    """Wrap one explicitly identified socket in its real discovery source."""
    return {FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET, **plug}]}


def _coordinator(
    hass: Any, entry: MockConfigEntry, payload: dict[str, Any]
) -> MagicMock:
    """Create a new coordinator runtime while keeping real Home Assistant registries."""
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.config_entry = entry
    coordinator.data = {_PARENT: payload}
    return coordinator


def _register_socket(
    hass: Any, entry: MockConfigEntry, plug: dict[str, Any]
) -> tuple[MagicMock, str, str, dr.DeviceEntry, er.RegistryEntry]:
    """Register a captured socket key before the production alias persistence runs."""
    coordinator = _coordinator(hass, entry, _discovery(plug))
    identity = smart_plug_entity_identity(coordinator, _PARENT, plug)
    assert identity is not None
    key = smart_plug_entity_key(coordinator, _PARENT, identity)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{_PARENT}_{key}")},
        serial_number=identity,
    )
    entity = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{_PARENT}_{key}_input_power",
        config_entry=entry,
        device_id=device.id,
    )
    assert smart_plug_entity_payload(coordinator, _PARENT, identity)
    registered = dr.async_get(hass).async_get(device.id)
    assert isinstance(registered, dr.DeviceEntry)
    return coordinator, identity, key, registered, entity


@pytest.mark.parametrize("cloud_field", _CLOUD_FIELDS)
async def test_serial_first_cloud_alias_survives_serialized_registry_restart(
    cloud_field: str, hass: Any
) -> None:
    """Cloud-only telemetry recovers the original serial-keyed device and entity."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    plug = {FIELD_DEVICE_SN: _SERIAL, cloud_field: _CLOUD, FIELD_IN_PW: 5}
    _, identity, key, device, entity = _register_socket(hass, entry, plug)
    snapshot = json.loads(
        json.dumps({
            "identifiers": sorted(device.identifiers),
            "serial_number": device.serial_number,
            "unique_id": entity.unique_id,
        })
    )
    dr.async_get(hass).async_remove_device(device.id)
    await hass.async_block_till_done()
    restored = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={tuple(identifier) for identifier in snapshot["identifiers"]},
        serial_number=snapshot["serial_number"],
    )
    restored_entity = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        snapshot["unique_id"],
        config_entry=entry,
        device_id=restored.id,
    )
    cloud = {cloud_field: _CLOUD, FIELD_IN_PW: _POWER}
    replacement = _coordinator(hass, entry, {PAYLOAD_SMART_PLUGS: [cloud]})

    assert smart_plug_entity_identity(replacement, _PARENT, cloud) == identity
    assert smart_plug_entity_key(replacement, _PARENT, identity) == key
    assert (
        smart_plug_entity_payload(replacement, _PARENT, identity)[FIELD_IN_PW] == _POWER
    )
    assert restored_entity.unique_id == entity.unique_id
    assert (DOMAIN, f"{_PARENT}_smart_plug_cloud:{_CLOUD}") in restored.identifiers
    identities = {
        value
        for domain, value in restored.identifiers
        if domain == DOMAIN and value.startswith(f"{_PARENT}_smart_plug_identity:")
    }
    assert identities == {f"{_PARENT}_smart_plug_identity:{identity}"}


def test_multiple_proven_cloud_aliases_recover_one_captured_identity(hass: Any) -> None:
    """Cloud aliases proven at different times retain the same captured serial key."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    first = {FIELD_DEVICE_SN: _SERIAL, FIELD_DEVICE_ID: _CLOUD, FIELD_IN_PW: 5}
    coordinator, identity, key, device, _ = _register_socket(hass, entry, first)
    second = {**first, FIELD_DEVICE_ID: "CLOUD-B"}
    coordinator.data[_PARENT] = _discovery(second)
    assert smart_plug_entity_payload(coordinator, _PARENT, identity)
    registered = dr.async_get(hass).async_get(device.id)
    assert registered is not None
    serialized = json.loads(json.dumps(sorted(registered.identifiers)))
    dr.async_get(hass).async_update_device(
        device.id, new_identifiers={tuple(identifier) for identifier in serialized}
    )

    for alias in (_CLOUD, "CLOUD-B"):
        cloud = {FIELD_DEVICE_ID: alias, FIELD_IN_PW: _POWER}
        replacement = _coordinator(hass, entry, {PAYLOAD_SMART_PLUGS: [cloud]})
        assert smart_plug_entity_identity(replacement, _PARENT, cloud) == identity
        assert smart_plug_entity_key(replacement, _PARENT, identity) == key
        assert (
            smart_plug_entity_payload(replacement, _PARENT, identity)[FIELD_IN_PW]
            == _POWER
        )


@pytest.mark.parametrize("explicit_cloud_link", [False, True])
def test_same_text_cloud_reload_requires_a_persisted_explicit_namespace_link(
    explicit_cloud_link: bool, hass: Any
) -> None:
    """Serial markers alone never authorize an equal-text cloud identity on reload."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    physical = {FIELD_DEVICE_SN: _SERIAL, FIELD_IN_PW: 5}
    if explicit_cloud_link:
        physical[FIELD_DEVICE_ID] = _SERIAL
    _, identity, key, device, _ = _register_socket(hass, entry, physical)
    cloud = {FIELD_DEVICE_ID: _SERIAL, FIELD_IN_PW: _POWER}
    replacement = _coordinator(hass, entry, {PAYLOAD_SMART_PLUGS: [cloud]})

    if explicit_cloud_link:
        assert smart_plug_entity_identity(replacement, _PARENT, cloud) == identity
        assert smart_plug_entity_key(replacement, _PARENT, identity) == key
        assert (DOMAIN, f"{_PARENT}_smart_plug_cloud:{_SERIAL}") in device.identifiers
    else:
        assert smart_plug_entity_identity(replacement, _PARENT, cloud) is None
        assert smart_plug_entity_payload(replacement, _PARENT, identity) == {}


def test_reused_cloud_alias_cannot_capture_a_different_registered_serial(
    hass: Any,
) -> None:
    """An old device's persisted cloud link cannot replace its proven serial."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    first = {FIELD_DEVICE_SN: _SERIAL, FIELD_DEVICE_ID: _CLOUD, FIELD_IN_PW: 5}
    _, old_identity, old_key, _, _ = _register_socket(hass, entry, first)
    reused = {FIELD_DEVICE_SN: "SERIAL-B", FIELD_DEVICE_ID: _CLOUD, FIELD_IN_PW: _POWER}
    replacement = _coordinator(hass, entry, _discovery(reused))

    assert smart_plug_entity_identity(replacement, _PARENT, reused) == "SERIAL-B"
    assert smart_plug_entity_key(replacement, _PARENT, "SERIAL-B") != old_key
    assert smart_plug_entity_payload(replacement, _PARENT, old_identity) == {}


def test_shared_cloud_alias_is_not_persisted_for_two_serial_owners(hass: Any) -> None:
    """Ambiguous current cloud metadata cannot create a registry alias proof."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    first = {FIELD_DEVICE_SN: _SERIAL, FIELD_IN_PW: 5}
    coordinator, identity, _, device, _ = _register_socket(hass, entry, first)
    coordinator.data[_PARENT] = {
        FIELD_ACCESSORIES: [
            {
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                FIELD_DEVICE_SN: serial,
                FIELD_DEVICE_ID: _CLOUD,
            }
            for serial in (_SERIAL, "SERIAL-B")
        ]
    }
    for plug in smart_plug_payloads(coordinator.data[_PARENT]):
        smart_plug_entity_identity(coordinator, _PARENT, plug)

    registered = dr.async_get(hass).async_get(device.id)
    assert registered is not None
    assert (
        DOMAIN,
        f"{_PARENT}_smart_plug_cloud:{_CLOUD}",
    ) not in registered.identifiers
    assert (
        DOMAIN,
        f"{_PARENT}_smart_plug_identity:{identity}",
    ) in registered.identifiers


def test_foreign_cloud_marker_cannot_merge_or_route_a_same_entry_socket(
    hass: Any,
) -> None:
    """Global compatibility metadata cannot override entry-scoped alias ownership."""
    foreign_entry = MockConfigEntry(domain=DOMAIN)
    foreign_entry.add_to_hass(hass)
    cloud_marker = (DOMAIN, f"{_PARENT}_smart_plug_cloud:{_CLOUD}")
    foreign = dr.async_get(hass).async_get_or_create(
        config_entry_id=foreign_entry.entry_id,
        identifiers={(DOMAIN, f"{_PARENT}_foreign_socket"), cloud_marker},
        serial_number="FOREIGN-SERIAL",
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{_PARENT}_foreign_socket_input_power",
        config_entry=foreign_entry,
        device_id=foreign.id,
    )
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    plug = {FIELD_DEVICE_SN: _SERIAL, FIELD_DEVICE_ID: _CLOUD, FIELD_IN_PW: 5}
    _, identity, key, own, entity = _register_socket(hass, entry, plug)

    assert own.id != foreign.id
    assert cloud_marker not in own.identifiers
    assert (
        DOMAIN,
        f"{_PARENT}_smart_plug_alias:{entry.entry_id}:{_CLOUD}",
    ) in own.identifiers
    unchanged = dr.async_get(hass).async_get(foreign.id)
    assert unchanged is not None
    assert unchanged.identifiers == foreign.identifiers
    assert unchanged.config_entry_id == foreign_entry.entry_id
    cloud = {FIELD_DEVICE_ID: _CLOUD, FIELD_IN_PW: _POWER}
    replacement = _coordinator(hass, entry, {PAYLOAD_SMART_PLUGS: [cloud]})

    assert smart_plug_entity_identity(replacement, _PARENT, cloud) == identity
    assert smart_plug_entity_key(replacement, _PARENT, identity) == key
    assert (
        smart_plug_entity_payload(replacement, _PARENT, identity)[FIELD_IN_PW] == _POWER
    )
    assert er.async_get(hass).async_get(entity.entity_id) == entity
