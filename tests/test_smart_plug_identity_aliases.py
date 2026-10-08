"""Only explicit, unambiguous socket aliases may share entity identities."""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import binary_sensor, sensor, switch
from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FIELD_ACCESSORIES,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEV_ID,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_ID,
    FIELD_IN_PW,
    FIELD_SN,
    FIELD_SOCKET_PRIORITY,
    FIELD_SWITCH_STATE,
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
from homeassistant.helpers import device_registry as dr, entity_registry as er

_PARENT = "parent-1"
_SERIAL = "PLUG-A"
_CLOUD_ID = "cloud-a"


def _discovery(*plugs: dict[str, Any]) -> dict[str, Any]:
    """Wrap exact socket records in the root discovery source."""
    return {
        FIELD_ACCESSORIES: [
            {FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET, **plug} for plug in plugs
        ]
    }


@pytest.mark.parametrize("serial_field", [FIELD_DEVICE_SN, FIELD_DEV_SN, FIELD_SN])
@pytest.mark.parametrize("cloud_field", [FIELD_DEVICE_ID, FIELD_ID, FIELD_DEV_ID])
def test_explicit_aliases_merge_discovery_and_telemetry(
    serial_field: str, cloud_field: str
) -> None:
    """A record linking a serial and cloud ID establishes one physical socket."""
    payload = _discovery({
        serial_field: _SERIAL,
        cloud_field: _CLOUD_ID,
        FIELD_IN_PW: 5,
    })
    payload[PAYLOAD_SMART_PLUGS] = [{cloud_field: _CLOUD_ID, FIELD_IN_PW: 42}]
    before = deepcopy(payload)

    plugs = smart_plug_payloads(payload)

    assert len(plugs) == 1
    assert plugs[0][serial_field] == _SERIAL
    assert plugs[0][FIELD_IN_PW] == 42  # ruff: ignore[magic-value-comparison]
    assert smart_plug_payload(payload, _SERIAL) == plugs[0]
    assert smart_plug_payload(payload, _CLOUD_ID) == plugs[0]
    assert payload == before


def test_shared_cloud_alias_cannot_merge_distinct_serials() -> None:
    """Conflicting explicit cloud links leave physical sockets separate."""
    payload = _discovery(
        {FIELD_DEVICE_SN: _SERIAL, FIELD_DEVICE_ID: _CLOUD_ID, FIELD_IN_PW: 5},
        {FIELD_DEVICE_SN: "PLUG-B", FIELD_DEVICE_ID: _CLOUD_ID, FIELD_IN_PW: 8},
    )
    payload[PAYLOAD_SMART_PLUGS] = [{FIELD_DEVICE_ID: _CLOUD_ID, FIELD_IN_PW: 42}]

    plugs = smart_plug_payloads(payload)

    assert len(plugs) == 2  # ruff: ignore[magic-value-comparison]
    assert {plug[FIELD_IN_PW] for plug in plugs} == {5, 8}
    assert smart_plug_payload(payload, _CLOUD_ID) == {}


def test_unlinked_cloud_id_and_case_variants_do_not_imply_aliases() -> None:
    """Similar text and array position are not evidence of physical identity."""
    payload = _discovery({FIELD_DEVICE_SN: _SERIAL, FIELD_IN_PW: 5})
    payload[PAYLOAD_SMART_PLUGS] = [{FIELD_DEVICE_ID: _SERIAL.lower(), FIELD_IN_PW: 42}]

    assert len(smart_plug_payloads(payload)) == 2  # ruff: ignore[magic-value-comparison]
    assert smart_plug_payload(payload, _SERIAL)[FIELD_IN_PW] == 5  # ruff: ignore[magic-value-comparison]


@pytest.mark.parametrize("platform_name", ["sensor", "switch", "binary_sensor"])
async def test_late_serial_alias_preserves_registered_cloud_identity(
    platform_name: str, hass: Any
) -> None:
    """Cloud-first entities retain their IDs and follow later serial-only data."""
    platform = {
        "sensor": sensor,
        "switch": switch,
        "binary_sensor": binary_sensor,
    }[platform_name]
    cloud = {
        FIELD_DEVICE_ID: _CLOUD_ID,
        FIELD_IN_PW: 5,
        FIELD_SWITCH_STATE: 0,
        FIELD_SOCKET_PRIORITY: 0,
    }
    coordinator = MagicMock(name="alias-coordinator")
    coordinator.data = {_PARENT: {PAYLOAD_SMART_PLUGS: [cloud]}}
    coordinator.last_update_success = True
    coordinator.config_entry = None
    coordinator.is_device_reachable.return_value = True
    coordinator.device_supports_advanced.return_value = False
    coordinator.async_add_listener.return_value = lambda: None
    entry = SimpleNamespace(
        runtime_data=coordinator,
        options={},
        data={},
        entry_id="alias-entry",
        async_on_unload=MagicMock(),
    )
    added: list[Any] = []
    await cast("Any", platform.async_setup_entry)(hass, entry, added.extend)
    listener = coordinator.async_add_listener.call_args.args[0]
    entity_type = {
        "sensor": sensor.JackerySmartPlugSensor,
        "switch": switch.JackerySmartPlugSwitch,
        "binary_sensor": binary_sensor.JackerySmartPlugStateBinarySensor,
    }[platform_name]
    entities = [entity for entity in added if isinstance(entity, entity_type)]
    assert entities
    identities = [
        (entity.unique_id, deepcopy(entity.device_info)) for entity in entities
    ]
    linked = _discovery({**cloud, FIELD_DEVICE_SN: _SERIAL})
    linked[PAYLOAD_SMART_PLUGS] = [{FIELD_DEVICE_ID: _CLOUD_ID, FIELD_IN_PW: 42}]

    for payload in (
        linked,
        {
            PAYLOAD_SMART_PLUGS: [
                {FIELD_DEVICE_SN: _SERIAL, FIELD_IN_PW: 42, FIELD_SWITCH_STATE: 1}
            ]
        },
    ):
        coordinator.data[_PARENT] = payload
        listener()
        current = [entity for entity in added if isinstance(entity, entity_type)]
        assert [
            (entity.unique_id, entity.device_info) for entity in current
        ] == identities
        for entity in current:
            assert entity._plug[FIELD_IN_PW] == 42  # ruff: ignore[private-member-access,magic-value-comparison]


def test_reload_recovers_exact_cloud_identity_from_home_assistant_registries(
    hass: Any,
) -> None:
    """A new coordinator reuses the registered cloud-first ID after explicit linking."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    key = "smart_plug_cloud_a"
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{_PARENT}_{key}")},
        serial_number=_CLOUD_ID,
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{_PARENT}_{key}_input_power",
        config_entry=entry,
        device_id=device.id,
    )
    linked = {FIELD_DEVICE_SN: _SERIAL, FIELD_DEVICE_ID: _CLOUD_ID, FIELD_IN_PW: 42}
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.config_entry = entry
    coordinator.data = {_PARENT: _discovery(linked)}

    assert smart_plug_entity_identity(coordinator, _PARENT, linked) == _CLOUD_ID
    assert smart_plug_entity_payload(coordinator, _PARENT, _CLOUD_ID)[FIELD_IN_PW] == 42  # ruff: ignore[magic-value-comparison]
    replacement = MagicMock()
    replacement.hass = hass
    replacement.config_entry = entry
    replacement.data = coordinator.data
    assert smart_plug_entity_identity(replacement, _PARENT, linked) == _CLOUD_ID


def test_retained_cloud_alias_cannot_rebind_to_a_replacement_serial() -> None:
    """A formerly linked cloud ID does not move a physical entity to another serial."""
    first = {FIELD_DEVICE_SN: _SERIAL, FIELD_DEVICE_ID: _CLOUD_ID, FIELD_IN_PW: 5}
    coordinator = MagicMock()
    coordinator.config_entry = None
    coordinator.data = {_PARENT: _discovery(first)}
    assert smart_plug_entity_identity(coordinator, _PARENT, first) == _SERIAL
    replacement = {
        FIELD_DEVICE_SN: "PLUG-B",
        FIELD_DEVICE_ID: _CLOUD_ID,
        FIELD_IN_PW: 42,
    }
    coordinator.data[_PARENT] = _discovery(replacement)

    assert smart_plug_entity_payload(coordinator, _PARENT, _SERIAL) == {}
    assert smart_plug_entity_identity(coordinator, _PARENT, replacement) == "PLUG-B"


@pytest.mark.parametrize("serial_only", [True, False])
def test_proven_serial_alias_survives_registry_reload_after_metadata_changes(
    serial_only: bool,
    hass: Any,
) -> None:
    """Persist a proven link on its existing HA device before metadata disappears."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    key = "smart_plug_cloud_a"
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{_PARENT}_{key}")},
        serial_number=_CLOUD_ID,
    )
    registered = er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{_PARENT}_{key}_input_power",
        config_entry=entry,
        device_id=device.id,
    )
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.config_entry = entry
    cloud = {FIELD_DEVICE_ID: _CLOUD_ID, FIELD_IN_PW: 5}
    coordinator.data = {_PARENT: _discovery(cloud)}
    assert smart_plug_entity_identity(coordinator, _PARENT, cloud) == _CLOUD_ID
    linked = {**cloud, FIELD_DEVICE_SN: _SERIAL}
    coordinator.data[_PARENT] = _discovery(linked)
    assert (
        smart_plug_entity_payload(coordinator, _PARENT, _CLOUD_ID)[FIELD_DEVICE_SN]
        == _SERIAL
    )

    replacement = MagicMock()
    replacement.hass = hass
    replacement.config_entry = entry
    reloaded = {
        (FIELD_DEVICE_SN if serial_only else FIELD_DEVICE_ID): (
            _SERIAL if serial_only else _CLOUD_ID
        ),
        FIELD_IN_PW: 42,
    }
    replacement.data = {_PARENT: _discovery(reloaded)}
    assert smart_plug_entity_identity(replacement, _PARENT, reloaded) == _CLOUD_ID
    assert smart_plug_entity_key(replacement, _PARENT, _CLOUD_ID) == key
    assert smart_plug_entity_payload(replacement, _PARENT, _CLOUD_ID)[FIELD_IN_PW] == 42  # ruff: ignore[magic-value-comparison]
    restored_entity = er.async_get(hass).async_get(registered.entity_id)
    assert restored_entity is not None
    assert restored_entity.unique_id == registered.unique_id
    reused = {FIELD_DEVICE_SN: "PLUG-B", FIELD_DEVICE_ID: _CLOUD_ID}
    replacement.data[_PARENT] = _discovery(reused)
    assert smart_plug_entity_identity(replacement, _PARENT, reused) == "PLUG-B"
    assert smart_plug_entity_payload(replacement, _PARENT, _CLOUD_ID) == {}


@pytest.mark.parametrize("other_entry", [False, True])
def test_registry_recovery_requires_exact_alias_and_own_config_entry(
    other_entry: bool, hass: Any
) -> None:
    """Sanitized key collisions and devices from another entry prove no alias."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    owner = MockConfigEntry(domain=DOMAIN) if other_entry else entry
    if other_entry:
        owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={(DOMAIN, f"{_PARENT}_smart_plug_cloud_a")},
        serial_number=_CLOUD_ID,
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{_PARENT}_smart_plug_cloud_a_input_power",
        config_entry=owner,
        device_id=device.id,
    )
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.config_entry = entry
    unmatched = _CLOUD_ID if other_entry else _CLOUD_ID.upper()
    plug = {FIELD_DEVICE_ID: unmatched}
    coordinator.data = {_PARENT: _discovery(plug)}

    assert smart_plug_entity_identity(coordinator, _PARENT, plug) == unmatched


@pytest.mark.parametrize("platform_name", ["sensor", "switch", "binary_sensor"])
@pytest.mark.parametrize("cloud_id", ["plug-a", "plug_a"])
async def test_distinct_case_and_punctuation_identities_keep_both_entity_keys(
    platform_name: str, cloud_id: str, hass: Any
) -> None:
    """A new colliding slug never overwrites or hides the first registered socket."""
    platform = {
        "sensor": sensor,
        "switch": switch,
        "binary_sensor": binary_sensor,
    }[platform_name]
    cloud = {FIELD_DEVICE_ID: cloud_id, FIELD_SWITCH_STATE: 0, FIELD_IN_PW: 5}
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.config_entry = None
    coordinator.last_update_success = True
    coordinator.is_device_reachable.return_value = True
    coordinator.device_supports_advanced.return_value = False
    coordinator.async_add_listener.return_value = lambda: None
    coordinator.data = {_PARENT: _discovery(cloud)}
    entry = SimpleNamespace(
        runtime_data=coordinator,
        options={},
        data={},
        entry_id="collision-entry",
        async_on_unload=MagicMock(),
    )
    added: list[Any] = []
    await cast("Any", platform.async_setup_entry)(hass, entry, added.extend)
    listener = coordinator.async_add_listener.call_args.args[0]
    entity_type = {
        "sensor": sensor.JackerySmartPlugSensor,
        "switch": switch.JackerySmartPlugSwitch,
        "binary_sensor": binary_sensor.JackerySmartPlugStateBinarySensor,
    }[platform_name]
    initial = [entity for entity in added if isinstance(entity, entity_type)]
    identities = [entity.unique_id for entity in initial]
    physical = {FIELD_DEVICE_SN: _SERIAL, FIELD_SWITCH_STATE: 1, FIELD_IN_PW: 42}
    both = _discovery(physical, cloud)
    for payload in (both, _discovery(cloud), _discovery(cloud, physical)):
        coordinator.data[_PARENT] = payload
        listener()
        current = [entity for entity in added if isinstance(entity, entity_type)]
        assert len(current) == 2 * len(initial)
        assert [entity.unique_id for entity in current[: len(initial)]] == identities
        assert len({entity.unique_id for entity in current}) == len(current)
    assert all(entity._plug[FIELD_IN_PW] == 5 for entity in initial)  # ruff: ignore[private-member-access,magic-value-comparison]
    assert all(entity._plug[FIELD_IN_PW] == 42 for entity in current[len(initial) :])  # ruff: ignore[private-member-access,magic-value-comparison]


def test_collision_keys_and_exact_aliases_survive_real_registry_reload(
    hass: Any,
) -> None:
    """Registry persistence restores the original and disambiguated socket keys."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    cloud = {FIELD_DEVICE_ID: "plug-a", FIELD_IN_PW: 5}
    physical = {FIELD_DEVICE_SN: _SERIAL, FIELD_IN_PW: 42}
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.config_entry = entry
    coordinator.data = {_PARENT: _discovery(cloud)}
    first = smart_plug_entity_identity(coordinator, _PARENT, cloud)
    assert first is not None
    coordinator.data[_PARENT] = _discovery(physical, cloud)
    second = smart_plug_entity_identity(coordinator, _PARENT, physical)
    assert second is not None
    keys = {
        identity: smart_plug_entity_key(coordinator, _PARENT, identity)
        for identity in (first, second)
    }
    assert len(set(keys.values())) == 2  # ruff: ignore[magic-value-comparison]
    assert keys[first] == "smart_plug_plug_a"
    for identity, key in keys.items():
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{_PARENT}_{key}")},
            serial_number=identity,
        )
        er.async_get(hass).async_get_or_create(
            "sensor",
            DOMAIN,
            f"{_PARENT}_{key}_input_power",
            config_entry=entry,
            device_id=device.id,
        )
        assert smart_plug_entity_payload(coordinator, _PARENT, identity)
    replacement = MagicMock()
    replacement.hass = hass
    replacement.config_entry = entry
    replacement.data = coordinator.data
    for plug, identity in ((physical, second), (cloud, first)):
        assert smart_plug_entity_identity(replacement, _PARENT, plug) == identity
        assert smart_plug_entity_key(replacement, _PARENT, identity) == keys[identity]


@pytest.mark.parametrize("platform_name", ["sensor", "switch", "binary_sensor"])
@pytest.mark.parametrize("cloud_field", [FIELD_DEVICE_ID, FIELD_ID, FIELD_DEV_ID])
async def test_serial_first_entities_recover_every_cloud_alias_after_reload(
    platform_name: str,
    cloud_field: str,
    hass: Any,
) -> None:
    """Serial-first registrations retain all explicitly linked cloud aliases."""
    platform = {"sensor": sensor, "switch": switch, "binary_sensor": binary_sensor}[
        platform_name
    ]
    entity_type = {
        "sensor": sensor.JackerySmartPlugSensor,
        "switch": switch.JackerySmartPlugSwitch,
        "binary_sensor": binary_sensor.JackerySmartPlugStateBinarySensor,
    }[platform_name]
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    linked = {
        FIELD_DEVICE_SN: _SERIAL,
        FIELD_DEV_SN: "exact-serial-alias",
        FIELD_SN: "EXACT-SN",
        FIELD_DEVICE_ID: _CLOUD_ID,
        FIELD_ID: "exact-cloud-id",
        FIELD_DEV_ID: "EXACT-CLOUD-ID",
        FIELD_IN_PW: 5,
        FIELD_SWITCH_STATE: 0,
    }
    original: list[Any] = []
    recovered: list[Any] = []
    for payload, added in (
        (_discovery(linked), original),
        (
            _discovery({
                cloud_field: linked[cloud_field],
                FIELD_IN_PW: 42,
                FIELD_SWITCH_STATE: 1,
            }),
            recovered,
        ),
    ):
        coordinator = MagicMock()
        coordinator.hass = hass
        coordinator.config_entry = entry
        coordinator.data = {_PARENT: payload}
        coordinator.last_update_success = True
        coordinator.is_device_reachable.return_value = True
        coordinator.device_supports_advanced.return_value = False
        coordinator.async_add_listener.return_value = lambda: None
        entry.runtime_data = coordinator
        await cast("Any", platform.async_setup_entry)(hass, entry, added.extend)
        entities = [entity for entity in added if isinstance(entity, entity_type)]
        assert entities
        if added is original:
            info = entities[0].device_info
            device = dr.async_get(hass).async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers=info["identifiers"],
                serial_number=info["serial_number"],
            )
            for entity in entities:
                assert entity.unique_id is not None
                er.async_get(hass).async_get_or_create(
                    platform_name,
                    DOMAIN,
                    entity.unique_id,
                    config_entry=entry,
                    device_id=device.id,
                )
                assert entity._plug[FIELD_DEVICE_SN] == _SERIAL  # ruff: ignore[private-member-access]
        else:
            prior = [entity for entity in original if isinstance(entity, entity_type)]
            assert [entity.unique_id for entity in entities] == [
                entity.unique_id for entity in prior
            ]
            assert all(entity._plug[FIELD_IN_PW] == 42 for entity in entities)  # ruff: ignore[private-member-access,magic-value-comparison]


def test_historical_cloud_reuse_cannot_feed_two_physical_entities() -> None:
    """ID-only telemetry cannot revive both physical owners of a reused cloud ID."""
    coordinator = MagicMock()
    coordinator.config_entry = None
    for serial in (_SERIAL, "PLUG-B"):
        linked = {FIELD_DEVICE_SN: serial, FIELD_DEVICE_ID: _CLOUD_ID}
        coordinator.data = {_PARENT: _discovery(linked)}
        assert smart_plug_entity_identity(coordinator, _PARENT, linked) == serial
    coordinator.data = {
        _PARENT: _discovery({FIELD_DEVICE_ID: _CLOUD_ID, FIELD_IN_PW: 42})
    }

    assert smart_plug_entity_payload(coordinator, _PARENT, _SERIAL) == {}
    assert smart_plug_entity_payload(coordinator, _PARENT, "PLUG-B") == {}
    assert smart_plug_entity_payload(coordinator, _PARENT, _CLOUD_ID) == {}
    assert (
        smart_plug_entity_identity(coordinator, _PARENT, {FIELD_DEVICE_ID: _CLOUD_ID})
        is None
    )


@pytest.mark.parametrize("shared_snapshot", [False, True])
def test_shared_persisted_cloud_alias_stays_unavailable_after_reload(
    shared_snapshot: bool,
    hass: Any,
) -> None:
    """Persist a cloud ambiguity without merging devices or assigning an old owner."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.config_entry = entry
    first = {FIELD_DEVICE_SN: _SERIAL, FIELD_DEVICE_ID: _CLOUD_ID}
    second = {FIELD_DEVICE_SN: "PLUG-B", FIELD_DEVICE_ID: _CLOUD_ID}
    original_device_ids: list[str] = []
    for linked in (first, second):
        coordinator.data = {_PARENT: _discovery(linked)}
        if linked is second and shared_snapshot:
            coordinator.data[_PARENT] = _discovery(first, second)
        identity = smart_plug_entity_identity(coordinator, _PARENT, linked)
        assert identity is not None
        assert identity == linked[FIELD_DEVICE_SN]
        key = smart_plug_entity_key(coordinator, _PARENT, identity)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{_PARENT}_{key}")},
            serial_number=identity,
        )
        original_device_ids.append(device.id)
        er.async_get(hass).async_get_or_create(
            "sensor",
            DOMAIN,
            f"{_PARENT}_{key}_input_power",
            config_entry=entry,
            device_id=device.id,
        )
        assert smart_plug_entity_payload(coordinator, _PARENT, identity)
    cloud = {FIELD_DEVICE_ID: _CLOUD_ID}
    coordinator.data = {_PARENT: _discovery(cloud)}
    replacement = MagicMock()
    replacement.hass = hass
    replacement.config_entry = entry
    replacement.data = coordinator.data

    assert smart_plug_entity_identity(replacement, _PARENT, cloud) is None
    assert smart_plug_entity_payload(replacement, _PARENT, _CLOUD_ID) == {}
    assert smart_plug_entity_payload(coordinator, _PARENT, _SERIAL) == {}
    assert smart_plug_entity_payload(coordinator, _PARENT, "PLUG-B") == {}
    assert {
        device.id
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
    } == set(original_device_ids)
