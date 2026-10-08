"""Socket serial and cloud namespaces require an explicit physical link."""

from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_ACCESSORIES,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEV_ID,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_ID,
    FIELD_IN_PW,
    FIELD_SN,
    PAYLOAD_SMART_PLUGS,
    SUBDEVICE_DEV_TYPE_SOCKET,
)
from custom_components.jackery_solarvault.coordinator import (
    smart_plug_entity_identity,
    smart_plug_entity_key,
    smart_plug_entity_payload,
    smart_plug_payload,
    smart_plug_payloads,
)

_PARENT = "parent-1"
_IDENTITY = "SERIAL-A"
_SOCKET_COUNT = 2
_DISCOVERY_POWER = 5
_TELEMETRY_POWER = 99
_SERIAL_FIELDS = (FIELD_DEVICE_SN, FIELD_DEV_SN, FIELD_SN)
_CLOUD_FIELDS = (FIELD_DEVICE_ID, FIELD_ID, FIELD_DEV_ID)


@pytest.mark.parametrize("serial_field", _SERIAL_FIELDS)
@pytest.mark.parametrize("cloud_field", _CLOUD_FIELDS)
def test_equal_text_without_an_explicit_link_keeps_namespaces_separate(
    serial_field: str, cloud_field: str
) -> None:
    """A cloud ID cannot replace an unrelated physical serial's measurement."""
    payload = {
        FIELD_ACCESSORIES: [
            {
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                serial_field: _IDENTITY,
                FIELD_IN_PW: _DISCOVERY_POWER,
            }
        ],
        PAYLOAD_SMART_PLUGS: [{cloud_field: _IDENTITY, FIELD_IN_PW: _TELEMETRY_POWER}],
    }
    before = deepcopy(payload)

    plugs = smart_plug_payloads(payload)

    assert len(plugs) == _SOCKET_COUNT
    physical = next(plug for plug in plugs if serial_field in plug)
    cloud = next(plug for plug in plugs if cloud_field in plug)
    assert physical[FIELD_IN_PW] == _DISCOVERY_POWER
    assert cloud[FIELD_IN_PW] == _TELEMETRY_POWER
    assert smart_plug_payload(payload, _IDENTITY) == {}
    coordinator = MagicMock()
    coordinator.config_entry = None
    coordinator.data = {_PARENT: payload}
    assert all(
        smart_plug_entity_identity(coordinator, _PARENT, plug) is None for plug in plugs
    )
    assert payload == before


@pytest.mark.parametrize("serial_field", _SERIAL_FIELDS)
@pytest.mark.parametrize("cloud_field", _CLOUD_FIELDS)
def test_same_text_explicit_link_still_merges_cloud_telemetry(
    serial_field: str, cloud_field: str
) -> None:
    """A record explicitly linking both namespaces remains a valid alias proof."""
    payload = {
        FIELD_ACCESSORIES: [
            {
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                serial_field: _IDENTITY,
                cloud_field: _IDENTITY,
                FIELD_IN_PW: _DISCOVERY_POWER,
            }
        ],
        PAYLOAD_SMART_PLUGS: [{cloud_field: _IDENTITY, FIELD_IN_PW: _TELEMETRY_POWER}],
    }

    plugs = smart_plug_payloads(payload)

    assert len(plugs) == 1
    assert smart_plug_payload(payload, _IDENTITY)[FIELD_IN_PW] == _TELEMETRY_POWER
    coordinator = MagicMock()
    coordinator.config_entry = None
    coordinator.data = {_PARENT: payload}
    assert smart_plug_entity_identity(coordinator, _PARENT, plugs[0]) == _IDENTITY


def test_shared_cloud_link_cannot_fuse_two_distinct_serials() -> None:
    """An explicitly shared cloud ID remains ambiguous across physical sockets."""
    payload = {
        FIELD_ACCESSORIES: [
            {
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                FIELD_DEVICE_SN: serial,
                FIELD_DEVICE_ID: "SHARED",
                FIELD_IN_PW: value,
            }
            for serial, value in (("SERIAL-A", _DISCOVERY_POWER), ("SERIAL-B", 8))
        ],
        PAYLOAD_SMART_PLUGS: [
            {FIELD_DEVICE_ID: "SHARED", FIELD_IN_PW: _TELEMETRY_POWER}
        ],
    }

    plugs = smart_plug_payloads(payload)

    assert len(plugs) == _SOCKET_COUNT
    assert {plug[FIELD_IN_PW] for plug in plugs} == {_DISCOVERY_POWER, 8}
    assert smart_plug_payload(payload, "SHARED") == {}
    coordinator = MagicMock()
    coordinator.config_entry = None
    coordinator.data = {_PARENT: payload}
    assert {
        smart_plug_entity_identity(coordinator, _PARENT, plug) for plug in plugs
    } == {"SERIAL-A", "SERIAL-B"}


def test_ambiguous_cross_namespace_record_cannot_overwrite_captured_registry_key() -> (
    None
):
    """Previously captured entity IDs stay intact while ambiguous data is withheld."""
    physical = {
        FIELD_DEVICE_SN: _IDENTITY,
        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
        FIELD_IN_PW: _DISCOVERY_POWER,
    }
    coordinator = MagicMock()
    coordinator.config_entry = None
    coordinator.data = {_PARENT: {FIELD_ACCESSORIES: [physical]}}
    assert smart_plug_entity_identity(coordinator, _PARENT, physical) == _IDENTITY
    key = smart_plug_entity_key(coordinator, _PARENT, _IDENTITY)
    payload = {
        FIELD_ACCESSORIES: [physical],
        PAYLOAD_SMART_PLUGS: [
            {FIELD_DEVICE_ID: _IDENTITY, FIELD_IN_PW: _TELEMETRY_POWER}
        ],
    }
    coordinator.data[_PARENT] = payload

    for plug in smart_plug_payloads(payload):
        assert smart_plug_entity_identity(coordinator, _PARENT, plug) is None
    assert smart_plug_entity_key(coordinator, _PARENT, _IDENTITY) == key
    assert smart_plug_entity_payload(coordinator, _PARENT, _IDENTITY) == {}


def test_unlinked_serial_cannot_reuse_captured_cloud_key_via_its_preferred_text() -> (
    None
):
    """Another safe alias does not authorize reusing a conflicting captured key."""
    cloud = {FIELD_DEVICE_ID: _IDENTITY, FIELD_IN_PW: _TELEMETRY_POWER}
    coordinator = MagicMock()
    coordinator.config_entry = None
    coordinator.data = {_PARENT: {PAYLOAD_SMART_PLUGS: [cloud]}}
    assert smart_plug_entity_identity(coordinator, _PARENT, cloud) == _IDENTITY
    key = smart_plug_entity_key(coordinator, _PARENT, _IDENTITY)
    physical = {
        FIELD_DEVICE_SN: _IDENTITY,
        FIELD_DEVICE_ID: "OTHER-CLOUD",
        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
        FIELD_IN_PW: _DISCOVERY_POWER,
    }
    payload = {
        FIELD_ACCESSORIES: [physical],
        PAYLOAD_SMART_PLUGS: [cloud],
    }
    coordinator.data[_PARENT] = payload

    for plug in smart_plug_payloads(payload):
        assert smart_plug_entity_identity(coordinator, _PARENT, plug) is None
    assert smart_plug_entity_key(coordinator, _PARENT, _IDENTITY) == key
    assert smart_plug_entity_payload(coordinator, _PARENT, _IDENTITY) == {}
