"""Smart-plug entities keep their physical identity across payload sources."""

from copy import deepcopy
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import binary_sensor, button, sensor, switch
from custom_components.jackery_solarvault.const import (
    FIELD_ACCESSORIES,
    FIELD_CONTROL_ALLOWED,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEV_ID,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_ID,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IS_CLOUD,
    FIELD_OP,
    FIELD_OUT_PW,
    FIELD_SCAN_NAME,
    FIELD_SN,
    FIELD_SOCKET_PRIORITY,
    FIELD_SWITCH_STATE,
    FIELD_SYS_SWITCH,
    FIELD_TODAY_ENERGY,
    FIELD_TOTAL_ENERGY,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
    SUBDEVICE_DEV_TYPE_SOCKET,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.util import smart_plug_serial
from homeassistant.exceptions import HomeAssistantError

_DEVICE = "parent-1"
_SERIAL = "PLUG-A"
_KEY = "smart_plug_plug_a"
_SOURCES = ("bucket", "root", "system", "system_meta")
_SERIAL_FIELDS = (FIELD_DEVICE_SN, FIELD_DEV_SN, FIELD_SN)


def _payload(source: str, plugs: list[dict[str, Any]]) -> dict[str, Any]:
    """Place the same socket records in a supported coordinator source."""
    if source == "bucket":
        return {PAYLOAD_SMART_PLUGS: plugs}
    accessories = [
        {FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET, **plug} for plug in plugs
    ]
    if source == "root":
        return {FIELD_ACCESSORIES: accessories}
    section = PAYLOAD_SYSTEM if source == "system" else PAYLOAD_SYSTEM_META
    return {section: {FIELD_ACCESSORIES: accessories}}


def _plug(serial: str = _SERIAL, /, **fields: Any) -> dict[str, Any]:
    """Return a local socket record with explicit zero-valued states."""
    return {
        FIELD_DEVICE_SN: serial,
        FIELD_DEVICE_NAME: serial,
        FIELD_IN_PW: 12,
        FIELD_OUT_PW: 9,
        FIELD_TODAY_ENERGY: 1.5,
        FIELD_TOTAL_ENERGY: 8.5,
        FIELD_SWITCH_STATE: 0,
        FIELD_SOCKET_PRIORITY: 0,
        **fields,
    }


def _coordinator(payload: dict[str, Any]) -> MagicMock:
    """Mock transport boundaries while retaining real entity construction."""
    coordinator = MagicMock(name="smart-plug-coordinator")
    coordinator.data = {_DEVICE: payload}
    coordinator.last_update_success = True
    coordinator.config_entry = None
    coordinator.is_device_reachable.return_value = True
    coordinator.device_supports_advanced.return_value = False
    coordinator.async_add_listener.return_value = lambda: None
    coordinator.async_set_smart_plug_switch = AsyncMock()
    coordinator.async_set_smart_plug_priority = AsyncMock()
    coordinator.async_set_shelly_cloud_switch = AsyncMock()
    return coordinator


def _sensor(coordinator: Any, key: str) -> sensor.JackerySmartPlugSensor:
    """Construct a sensor with the production serial-bound identity contract."""
    description = next(
        item for item in sensor.SMART_PLUG_SENSOR_DESCRIPTIONS if item.key == key
    )
    return sensor.JackerySmartPlugSensor(
        coordinator,
        _DEVICE,
        identity=(1, _SERIAL, _KEY),
        description=description,
    )


def _switch(
    coordinator: Any, *, priority: bool = False
) -> switch.JackerySmartPlugSwitch:
    """Construct one relay or priority entity bound to the captured serial."""
    entity_type = (
        switch.JackerySmartPlugPrioritySwitch
        if priority
        else switch.JackerySmartPlugSwitch
    )
    return entity_type(
        coordinator,
        _DEVICE,
        plug_index=1,
        plug_sn=_SERIAL,
        plug_key=_KEY,
    )


def _binary(coordinator: Any) -> binary_sensor.JackerySmartPlugStateBinarySensor:
    """Construct the matching read-only power binary sensor."""
    return binary_sensor.JackerySmartPlugStateBinarySensor(
        coordinator,
        _DEVICE,
        plug_index=1,
        plug_sn=_SERIAL,
        plug_key=_KEY,
    )


@pytest.mark.parametrize("source", _SOURCES)
@pytest.mark.parametrize("serial_field", _SERIAL_FIELDS)
def test_runtime_source_changes_preserve_values_and_physical_identity(
    source: str, serial_field: str
) -> None:
    """Live values follow their serial through source and array-order changes."""
    initial = _plug()
    coordinator = _coordinator(_payload("bucket", [initial, _plug("PLUG-B")]))
    sensors = [
        _sensor(coordinator, key)
        for key in ("input_power", "output_power", "today_energy", "total_energy")
    ]
    switches = [
        _switch(coordinator),
        _switch(coordinator, priority=True),
        _binary(coordinator),
    ]
    entities = [*sensors, *switches]
    identities = [
        (entity.unique_id, deepcopy(entity.device_info)) for entity in entities
    ]

    for value in (25, 0, 37):
        live = {
            serial_field: _SERIAL,
            FIELD_IP: value,
            FIELD_OP: value,
            FIELD_TODAY_ENERGY: float(value),
            FIELD_TOTAL_ENERGY: 9.0 + value,
            FIELD_SYS_SWITCH: int(value != 0),
            FIELD_SOCKET_PRIORITY: int(value != 0),
        }
        coordinator.data[_DEVICE] = _payload(source, [_plug("PLUG-B"), live])
        for entity in sensors:
            entity._refresh_cache()  # ruff: ignore[private-member-access]
        assert [entity.native_value for entity in sensors[:3]] == [
            value,
            value,
            float(value),
        ]
        assert sensors[3].native_value == pytest.approx(max(34.0, 9.0 + value))
        assert [entity.is_on for entity in switches] == [value != 0] * 3
        assert [
            (entity.unique_id, entity.device_info) for entity in entities
        ] == identities

    coordinator.data[_DEVICE] = _payload("bucket", [initial])
    for entity in sensors:
        entity._refresh_cache()  # ruff: ignore[private-member-access]
    assert sensors[0].native_value == initial[FIELD_IN_PW]
    assert switches[0].is_on is False
    assert [(entity.unique_id, entity.device_info) for entity in entities] == identities


@pytest.mark.parametrize("missing_source", _SOURCES)
async def test_temporary_absence_is_unknown_and_never_rebinds_to_another_plug(
    missing_source: str,
) -> None:
    """Missing telemetry retains only lifetime energy and cannot target a neighbour."""
    coordinator = _coordinator(_payload("bucket", [_plug()]))
    measurement = _sensor(coordinator, "input_power")
    daily = _sensor(coordinator, "today_energy")
    total = _sensor(coordinator, "total_energy")
    relay = _switch(coordinator)
    priority = _switch(coordinator, priority=True)
    binary = _binary(coordinator)
    entities = [measurement, daily, total, relay, priority, binary]
    identities = [
        (entity.unique_id, deepcopy(entity.device_info)) for entity in entities
    ]
    for entity in (measurement, daily, total):
        entity._refresh_cache()  # ruff: ignore[private-member-access]

    coordinator.data[_DEVICE] = _payload(
        missing_source, [_plug("PLUG-B", **{FIELD_SWITCH_STATE: 1})]
    )
    for entity in (measurement, daily, total):
        entity._refresh_cache()  # ruff: ignore[private-member-access]
    assert measurement.native_value is None
    assert daily.native_value is None
    assert total.native_value == pytest.approx(8.5)
    assert relay.is_on is None
    assert priority.is_on is None
    assert binary.is_on is None
    for entity in (relay, priority):
        with pytest.raises(HomeAssistantError) as err:
            await entity.async_turn_on()
        assert err.value.translation_key == "entity_action_failed"
        assert err.value.translation_placeholders["error"] == "missing deviceSn"
    coordinator.async_set_smart_plug_switch.assert_not_awaited()
    coordinator.async_set_smart_plug_priority.assert_not_awaited()
    assert [(entity.unique_id, entity.device_info) for entity in entities] == identities

    coordinator.data[_DEVICE] = _payload(
        missing_source, [_plug(**{FIELD_IN_PW: 44, FIELD_SWITCH_STATE: 1})]
    )
    measurement._refresh_cache()  # ruff: ignore[private-member-access]
    assert measurement.native_value == 44  # ruff: ignore[magic-value-comparison]
    assert relay.is_on is True
    assert priority.is_on is False
    assert binary.is_on is True


@pytest.mark.parametrize("source", _SOURCES[1:])
async def test_discovery_unions_sources_without_duplicate_entities(source: str) -> None:
    """A populated transport bucket must not hide a different discovery socket."""
    payload = _payload(source, [_plug(), _plug(), _plug("PLUG-B")])
    payload[PAYLOAD_SMART_PLUGS] = [_plug("PLUG-B"), _plug("PLUG-B")]
    coordinator = _coordinator(payload)
    seen_sensors: set[str] = set()
    seen_switches: set[str] = set()
    sensors: list[Any] = []
    switches: list[Any] = []
    binaries: list[Any] = []
    buttons: list[Any] = []
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    await cast("Any", binary_sensor.async_setup_entry)(None, entry, binaries.extend)
    binary_listener = coordinator.async_add_listener.call_args.args[0]
    await cast("Any", button.async_setup_entry)(None, entry, buttons.extend)
    button_listener = coordinator.async_add_listener.call_args.args[0]

    for current_payload in (
        payload,
        _payload(source, [_plug("PLUG-B"), _plug()]),
        {},
        payload,
    ):
        coordinator.data[_DEVICE] = current_payload
        collection = sensor._SensorCollection(  # ruff: ignore[private-member-access]
            coordinator, seen_sensors, {}, False, False, False, []
        )
        sensor._collect_smart_plugs(collection, _DEVICE, current_payload)  # ruff: ignore[private-member-access]
        sensors.extend(collection.entities)
        switch._collect_smart_plug_switches(  # ruff: ignore[private-member-access]
            coordinator, _DEVICE, current_payload, switches, seen_switches
        )
        binary_listener()
        button_listener()
        _assert_plug_registration(sensors, switches, binaries, buttons)


def _assert_plug_registration(
    sensors: list[Any],
    switches: list[Any],
    binaries: list[Any],
    buttons: list[Any],
) -> None:
    """Check complete, unique registration at every discovery pass."""
    smart_binaries = [
        entity
        for entity in binaries
        if isinstance(entity, binary_sensor.JackerySmartPlugStateBinarySensor)
    ]
    smart_buttons = [
        entity
        for entity in buttons
        if entity.translation_key == "read_smart_plug_schedule"
    ]
    assert len(sensors) == 2 * len(sensor.SMART_PLUG_SENSOR_DESCRIPTIONS)
    assert len(switches) == 4  # ruff: ignore[magic-value-comparison]
    assert len(smart_binaries) == 2  # ruff: ignore[magic-value-comparison]
    assert len(smart_buttons) == 2  # ruff: ignore[magic-value-comparison]
    for entities in (sensors, switches, smart_binaries, smart_buttons):
        unique_ids = [entity.unique_id for entity in entities]
        assert len(unique_ids) == len(set(unique_ids))
        assert any(
            "plug_a" in unique_id.lower() or "plug-a" in unique_id.lower()
            for unique_id in unique_ids
        )
        assert any(
            "plug_b" in unique_id.lower() or "plug-b" in unique_id.lower()
            for unique_id in unique_ids
        )
    assert any(entity.device_info.get("serial_number") == _SERIAL for entity in sensors)


@pytest.mark.parametrize("source", _SOURCES[1:])
@pytest.mark.parametrize("serial_field", _SERIAL_FIELDS)
async def test_controls_keep_the_bound_serial_in_discovery_sources(
    source: str, serial_field: str
) -> None:
    """Relay and priority writes use the physical serial, never list position."""
    coordinator = _coordinator(_payload("bucket", [_plug()]))
    relay = _switch(coordinator)
    priority = _switch(coordinator, priority=True)
    payload = _payload(source, [{serial_field: _SERIAL, FIELD_SOCKET_PRIORITY: 1}])
    payload[PAYLOAD_SMART_PLUGS] = [_plug("PLUG-B")]
    coordinator.data[_DEVICE] = payload

    await relay.async_turn_on()
    await priority.async_turn_off()

    coordinator.async_set_smart_plug_switch.assert_awaited_once_with(
        _DEVICE, plug_sn=_SERIAL, on=True
    )
    coordinator.async_set_smart_plug_priority.assert_awaited_once_with(
        _DEVICE, plug_sn=_SERIAL, enabled=False
    )
    coordinator.async_set_shelly_cloud_switch.assert_not_awaited()


@pytest.mark.parametrize("source", _SOURCES[1:])
async def test_cloud_control_metadata_survives_partial_bucket(source: str) -> None:
    """Partial live data preserves Shelly routing and its explicit permission."""
    cloud = _plug(**{
        FIELD_DEVICE_ID: "shelly-device-a",
        FIELD_SCAN_NAME: "shellyplusplugs",
        FIELD_IS_CLOUD: 1,
        FIELD_CONTROL_ALLOWED: 1,
    })
    payload = _payload(source, [cloud])
    payload[PAYLOAD_SMART_PLUGS] = [{FIELD_DEVICE_SN: _SERIAL, FIELD_SWITCH_STATE: 1}]
    coordinator = _coordinator(payload)
    relay = _switch(coordinator)

    assert relay.is_on is True
    assert relay.device_info["serial_number"] == _SERIAL
    await relay.async_turn_off()
    coordinator.async_set_shelly_cloud_switch.assert_awaited_once_with(
        _DEVICE, shelly_device_id="shelly-device-a", on=False
    )
    coordinator.async_set_smart_plug_switch.assert_not_awaited()

    coordinator.async_set_shelly_cloud_switch.reset_mock()
    denied = _payload(source, [{**cloud, FIELD_CONTROL_ALLOWED: 0}])
    denied[PAYLOAD_SMART_PLUGS] = payload[PAYLOAD_SMART_PLUGS]
    coordinator.data[_DEVICE] = denied
    with pytest.raises(HomeAssistantError) as err:
        await relay.async_turn_on()
    assert err.value.translation_key == "entity_action_failed"
    assert (
        err.value.translation_placeholders["error"] == "Shelly control is not allowed"
    )
    coordinator.async_set_shelly_cloud_switch.assert_not_awaited()
    coordinator.async_set_smart_plug_switch.assert_not_awaited()


@pytest.mark.parametrize("source", _SOURCES)
def test_coordinator_local_patch_resolves_discovery_and_keeps_other_devices(
    source: str,
) -> None:
    """Optimistic writes update only the requested parent/socket, copy on write."""
    original = _payload(source, [_plug(), _plug("PLUG-B")])
    before = deepcopy(original)
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    mutable = cast("Any", coordinator)
    mutable.data = {_DEVICE: original, "parent-2": _payload("bucket", [_plug()])}
    neighbour = deepcopy(mutable.data["parent-2"])
    mutable._push_partial_update = MagicMock(  # ruff: ignore[private-member-access]
        side_effect=lambda data: setattr(mutable, "data", data)
    )

    coordinator._apply_local_smart_plug_switch_patch(_DEVICE, _SERIAL, True)  # ruff: ignore[private-member-access]
    coordinator._apply_local_smart_plug_patch(  # ruff: ignore[private-member-access]
        _DEVICE, _SERIAL, {FIELD_SOCKET_PRIORITY: 1}
    )

    mirrored = {
        smart_plug_serial(plug): plug
        for plug in mutable.data[_DEVICE].get(PAYLOAD_SMART_PLUGS, [])
    }
    assert mirrored[_SERIAL][FIELD_SWITCH_STATE] == 1
    assert mirrored[_SERIAL][FIELD_SYS_SWITCH] == 1
    assert mirrored[_SERIAL][FIELD_SOCKET_PRIORITY] == 1
    assert mirrored["PLUG-B"][FIELD_SWITCH_STATE] == 0
    assert mirrored["PLUG-B"][FIELD_SOCKET_PRIORITY] == 0
    assert mutable.data["parent-2"] == neighbour
    assert original == before


def test_missing_serial_does_not_guess_from_unrelated_device_id() -> None:
    """An ID-only record cannot be rebound to an unproven new serial."""
    coordinator = _coordinator(_payload("bucket", [_plug()]))
    relay = _switch(coordinator)
    payload = _payload(
        "system", [{FIELD_DEVICE_ID: "unrelated-id", FIELD_SYS_SWITCH: 1}]
    )
    payload[PAYLOAD_SMART_PLUGS] = [_plug("NEW-SERIAL", **{FIELD_SWITCH_STATE: 1})]
    coordinator.data[_DEVICE] = payload

    assert relay.is_on is None
    assert relay.device_info["serial_number"] == _SERIAL


def test_live_alias_values_override_discovery_canonical_fields() -> None:
    """The authoritative source wins across aliases, including false and zero."""
    for source in _SOURCES[1:]:
        payload = _payload(
            source,
            [_plug(**{FIELD_SWITCH_STATE: 1, FIELD_IN_PW: 100, FIELD_OUT_PW: 100})],
        )
        payload[PAYLOAD_SMART_PLUGS] = [
            {
                FIELD_DEVICE_SN: _SERIAL,
                FIELD_SYS_SWITCH: 0,
                FIELD_IP: 0,
                FIELD_OP: 20,
            }
        ]
        coordinator = _coordinator(payload)
        input_power = _sensor(coordinator, "input_power")
        output_power = _sensor(coordinator, "output_power")
        input_power._refresh_cache()  # ruff: ignore[private-member-access]
        output_power._refresh_cache()  # ruff: ignore[private-member-access]
        assert input_power.native_value == 0
        assert output_power.native_value == 20  # ruff: ignore[magic-value-comparison]
        assert _switch(coordinator).is_on is False
        assert _binary(coordinator).is_on is False

        # Within a single source, a present canonical field keeps precedence
        # over its alias rather than depending on dictionary iteration order.
        payload[PAYLOAD_SMART_PLUGS][0].update({
            FIELD_SWITCH_STATE: 1,
            FIELD_IN_PW: 7,
            FIELD_OUT_PW: 8,
        })
        input_power._refresh_cache()  # ruff: ignore[private-member-access]
        output_power._refresh_cache()  # ruff: ignore[private-member-access]
        assert input_power.native_value == 7  # ruff: ignore[magic-value-comparison]
        assert output_power.native_value == 8  # ruff: ignore[magic-value-comparison]
        assert _switch(coordinator).is_on is True


def test_local_patch_rejects_ambiguous_shared_cloud_target() -> None:
    """A cloud ID shared by two physical serials cannot switch both sockets."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    mutable = cast("Any", coordinator)
    mutable.data = {
        _DEVICE: _payload(
            "bucket",
            [
                _plug(**{FIELD_DEVICE_ID: "shared-cloud-id"}),
                _plug("PLUG-B", **{FIELD_DEVICE_ID: "shared-cloud-id"}),
            ],
        )
    }
    mutable._push_partial_update = MagicMock()  # ruff: ignore[private-member-access]
    before = deepcopy(mutable.data)

    coordinator._apply_local_smart_plug_switch_patch(_DEVICE, "shared-cloud-id", True)  # ruff: ignore[private-member-access]

    mutable._push_partial_update.assert_not_called()  # ruff: ignore[private-member-access]
    assert mutable.data == before


def test_local_patch_rejects_unrelated_metadata_and_case_folded_targets() -> None:
    """Binding metadata and case-folded text cannot replace an exact command ID."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    mutable = cast("Any", coordinator)
    mutable.data = {
        _DEVICE: _payload(
            "bucket",
            [
                _plug(bindId="binding-id", deviceCode="product-code"),
            ],
        )
    }
    mutable._push_partial_update = MagicMock()  # ruff: ignore[private-member-access]
    before = deepcopy(mutable.data)

    for target in ("binding-id", "product-code", _SERIAL.lower()):
        coordinator._apply_local_smart_plug_switch_patch(_DEVICE, target, True)  # ruff: ignore[private-member-access]

    mutable._push_partial_update.assert_not_called()  # ruff: ignore[private-member-access]
    assert mutable.data == before


async def test_socket_statistics_enrich_discovery_only_sources() -> None:
    """Statistics enrich each supported discovery source without changing identity."""
    for source in _SOURCES[1:]:
        entry = _payload(source, [_plug(**{FIELD_DEVICE_ID: "stat-id-a"})])
        before = deepcopy(entry)
        coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
        mutable = cast("Any", coordinator)
        mutable._slow_cache = {}  # ruff: ignore[private-member-access]
        mutable._slow_metrics_interval_sec = 60  # ruff: ignore[private-member-access]
        mutable.api = MagicMock()
        mutable._async_get_with_ttl_for = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={FIELD_TODAY_ENERGY: 2.5, FIELD_TOTAL_ENERGY: 9.5}
        )

        await coordinator._async_enrich_smart_plug_statistics(_DEVICE, entry)  # ruff: ignore[private-member-access]

        assert entry[PAYLOAD_SMART_PLUGS][0][FIELD_DEVICE_SN] == _SERIAL
        assert entry[PAYLOAD_SMART_PLUGS][0][FIELD_TODAY_ENERGY] == pytest.approx(2.5)
        assert entry[PAYLOAD_SMART_PLUGS][0][FIELD_TOTAL_ENERGY] == pytest.approx(9.5)
        mutable._async_get_with_ttl_for.assert_awaited_once()  # ruff: ignore[private-member-access]
        await_args = mutable._async_get_with_ttl_for.await_args  # ruff: ignore[private-member-access]
        assert await_args is not None
        assert await_args.args[1] == "smart_socket_statistic:stat-id-a"
        for key, value in before.items():
            assert entry[key] == value


async def test_statistics_never_borrow_the_only_other_socket_id() -> None:
    """A serial-bound cached socket cannot borrow a different discovery socket ID."""
    entry = _payload("system", [_plug("PLUG-B", **{FIELD_DEVICE_ID: "stat-id-b"})])
    entry[PAYLOAD_SMART_PLUGS] = [_plug()]
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    mutable = cast("Any", coordinator)
    mutable._slow_cache = {}  # ruff: ignore[private-member-access]
    mutable._slow_metrics_interval_sec = 60  # ruff: ignore[private-member-access]
    mutable.api = MagicMock()
    mutable._async_get_with_ttl_for = AsyncMock(  # ruff: ignore[private-member-access]
        return_value={FIELD_TODAY_ENERGY: 2.5, FIELD_TOTAL_ENERGY: 9.5}
    )

    await coordinator._async_enrich_smart_plug_statistics(_DEVICE, entry)  # ruff: ignore[private-member-access]

    enriched = {smart_plug_serial(plug): plug for plug in entry[PAYLOAD_SMART_PLUGS]}
    assert enriched[_SERIAL][FIELD_TOTAL_ENERGY] == pytest.approx(8.5)
    assert enriched["PLUG-B"][FIELD_TOTAL_ENERGY] == pytest.approx(9.5)
    mutable._async_get_with_ttl_for.assert_awaited_once()  # ruff: ignore[private-member-access]
    await_args = mutable._async_get_with_ttl_for.await_args  # ruff: ignore[private-member-access]
    assert await_args is not None
    assert await_args.args[1] == "smart_socket_statistic:stat-id-b"


@pytest.mark.parametrize(
    "malformed_bucket",
    [None, {}, "invalid", [None, {}, {FIELD_DEVICE_SN: " "}, {FIELD_DEVICE_ID: None}]],
)
def test_malformed_bucket_cannot_hide_valid_discovery_socket(
    malformed_bucket: object,
) -> None:
    """Invalid containers and blank identities cannot shadow a known socket."""
    for source in _SOURCES[1:]:
        payload = _payload(
            source, [_plug(), {}, {FIELD_DEVICE_SN: " "}, {FIELD_DEVICE_ID: None}]
        )
        payload[PAYLOAD_SMART_PLUGS] = malformed_bucket
        coordinator = _coordinator(payload)
        measurement = _sensor(coordinator, "input_power")
        measurement._refresh_cache()  # ruff: ignore[private-member-access]
        assert measurement.native_value == 12  # ruff: ignore[magic-value-comparison]
        assert _switch(coordinator).is_on is False
        assert _binary(coordinator).is_on is False


def test_system_meta_socket_remains_visible_with_other_nonempty_system_data() -> None:
    """Empty root accessories and unrelated system metadata cannot mask system_meta."""
    payload = _payload("system_meta", [_plug(**{FIELD_SWITCH_STATE: 1})])
    payload[FIELD_ACCESSORIES] = []
    payload[PAYLOAD_SYSTEM] = {FIELD_DEVICE_NAME: "Parent system"}
    coordinator = _coordinator(payload)
    measurement = _sensor(coordinator, "input_power")

    measurement._refresh_cache()  # ruff: ignore[private-member-access]

    assert measurement.native_value == 12  # ruff: ignore[magic-value-comparison]
    assert _switch(coordinator).is_on is True
    assert _binary(coordinator).is_on is True
    assert measurement.device_info["serial_number"] == _SERIAL


@pytest.mark.parametrize("identity_field", [FIELD_DEVICE_ID, FIELD_ID, FIELD_DEV_ID])
async def test_id_only_cloud_identity_remains_stable_across_source_aliases(
    identity_field: str,
) -> None:
    """Known ID-only cloud records retain their captured identity and cloud target."""
    metadata = {
        FIELD_SCAN_NAME: "shellyplusplugs",
        FIELD_IS_CLOUD: 1,
        FIELD_CONTROL_ALLOWED: 1,
    }
    coordinator = _coordinator(
        _payload("bucket", [{FIELD_DEVICE_ID: _SERIAL, **metadata}])
    )
    relay = _switch(coordinator)
    power = _sensor(coordinator, "input_power")
    identities = [
        (entity.unique_id, deepcopy(entity.device_info)) for entity in (relay, power)
    ]
    for source in _SOURCES[1:]:
        coordinator.data[_DEVICE] = _payload(
            source,
            [{identity_field: _SERIAL, FIELD_SYS_SWITCH: 1, FIELD_IP: 42, **metadata}],
        )
        power._refresh_cache()  # ruff: ignore[private-member-access]
        assert power.native_value == 42  # ruff: ignore[magic-value-comparison]
        assert relay.is_on is True
        assert [
            (entity.unique_id, entity.device_info) for entity in (relay, power)
        ] == identities
        coordinator.async_set_shelly_cloud_switch.reset_mock()
        await relay.async_turn_off()
        coordinator.async_set_shelly_cloud_switch.assert_awaited_once_with(
            _DEVICE, shelly_device_id=_SERIAL, on=False
        )
    coordinator.async_set_smart_plug_switch.assert_not_awaited()


@pytest.mark.parametrize(
    "platform_name", ["sensor", "switch", "binary_sensor", "button"]
)
async def test_discovery_listener_detects_late_socket_in_system_meta(
    platform_name: str,
    hass: Any,
) -> None:
    """Platform listeners notice a new socket even while other system data exists."""
    platform = {
        "sensor": sensor,
        "switch": switch,
        "binary_sensor": binary_sensor,
        "button": button,
    }[platform_name]
    initial = {PAYLOAD_SYSTEM: {FIELD_DEVICE_NAME: "Parent system"}}
    coordinator = _coordinator(initial)
    entry = SimpleNamespace(
        runtime_data=coordinator,
        options={},
        data={},
        entry_id="smart-plug-entry",
        async_on_unload=MagicMock(),
    )
    added: list[Any] = []
    await cast("Any", platform.async_setup_entry)(hass, entry, added.extend)
    listener = coordinator.async_add_listener.call_args.args[0]
    prefixes = (f"{_DEVICE}_{_KEY}_", f"{_DEVICE}_smart_plug_{_SERIAL}_")
    assert not any(entity.unique_id.startswith(prefixes) for entity in added)

    next_payload = {**initial, **_payload("system_meta", [_plug()])}
    coordinator.data[_DEVICE] = next_payload
    listener()
    plugs = [entity for entity in added if entity.unique_id.startswith(prefixes)]
    expected = {
        "sensor": len(sensor.SMART_PLUG_SENSOR_DESCRIPTIONS),
        "switch": 2,
        "binary_sensor": 1,
        "button": 1,
    }
    assert len(plugs) == expected[platform_name]
    identities = [entity.unique_id for entity in plugs]
    assert len(identities) == len(set(identities))

    coordinator.data[_DEVICE] = initial
    listener()
    coordinator.data[_DEVICE] = next_payload
    listener()
    assert [
        entity.unique_id for entity in added if entity.unique_id.startswith(prefixes)
    ] == identities
