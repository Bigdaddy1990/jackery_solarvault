"""Behavioral tests for the coordinator live-property merge machinery.

HTTP remains the complete fallback, while cloud MQTT, local MQTT and BLE are
equal live-data peers. A fresh Layer-5 observation must not be reversed by an
immediate HTTP snapshot. Recent local setter writes (property overrides) beat
stale snapshots until their TTL lapses.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from custom_components.jackery_solarvault.const import (
    FIELD_CURRENT_VERSION,
    FIELD_DEVICE_SN,
    FIELD_GRID_STANDARD,
    PAYLOAD_CIRCUIT_PROPERTY,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SUBDEVICES,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
    PRESERVED_FAST_PAYLOAD_KEYS,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    changed_dict_values,
    merge_battery_pack_ota_lists,
    merge_dict_values,
    merge_sub_devices,
    merge_subdevice_lists_by_sn,
)
from custom_components.jackery_solarvault.ingest import TransportSource

if TYPE_CHECKING:
    import pytest

_HTTP_POWER = 100
_STALE_POWER = 5
_FILL_VALUE = 7


def _coordinator(data: dict[str, dict[str, Any]] | None = None) -> Any:
    """Build a bare coordinator exposing only merge-relevant state."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    shell = cast("Any", coordinator)
    shell.data = data
    shell._shutdown_started = False  # ruff: ignore[private-member-access]
    shell._property_overrides = {}  # ruff: ignore[private-member-access]
    shell._price_overrides = {}  # ruff: ignore[private-member-access]
    shell._listeners = {}  # ruff: ignore[private-member-access]
    shell._device_index = {}  # ruff: ignore[private-member-access]
    shell._ble_pending_updates = {}  # ruff: ignore[private-member-access]
    shell._device_registry_observer = None  # ruff: ignore[private-member-access]
    return shell


def test_property_value_present_rejects_empty_sentinels() -> None:
    """None, blank strings and empty containers do not count as present."""
    present = JackerySolarVaultCoordinator._property_value_present  # ruff: ignore[private-member-access]

    assert present(0) is True
    assert present("ok") is True
    assert present(None) is False
    assert present("   ") is False
    assert present({}) is False
    assert present([]) is False


def test_merge_dict_values_preserves_existing_nested_keys() -> None:
    """A sparse nested update keeps sibling keys the update omitted."""
    merged = merge_dict_values(
        {"outer": {"a": 1, "b": 2}},
        {"outer": {"b": 3}},
    )

    assert merged["outer"] == {"a": 1, "b": 3}


def test_changed_dict_values_returns_only_nested_live_delta() -> None:
    """Concurrent reconciliation must not replay unrelated stale HTTP fields."""
    assert changed_dict_values(
        {"properties": {"pvPw": 100, "soc": 50}, "stat": {"day": 1}},
        {"properties": {"pvPw": 200, "soc": 50}, "stat": {"day": 1}},
    ) == {"properties": {"pvPw": 200}}


def test_concurrent_live_push_is_reapplied_without_reverting_fresh_http() -> None:
    """A push received mid-poll wins only for fields changed since the baseline."""
    live_pv_power = 222
    live_soc = 51
    fresh_http_pv_power = 110
    baseline = {
        "dev-1": {
            PAYLOAD_PROPERTIES: {"pvPw": 100, "soc": 50},
            "stat": {"day": 1},
        }
    }
    fresh_http = {
        "dev-1": {
            PAYLOAD_PROPERTIES: {"pvPw": 110, "soc": 51},
            PAYLOAD_HTTP_PROPERTIES: {"pvPw": 110, "soc": 51},
            "stat": {"day": 2},
        }
    }
    coordinator = _coordinator({
        "dev-1": {
            PAYLOAD_PROPERTIES: {"pvPw": 222, "soc": 50},
            "stat": {"day": 1},
        }
    })

    merged = coordinator._merge_concurrent_coordinator_updates(  # ruff: ignore[private-member-access]
        baseline,
        fresh_http,
    )

    assert merged["dev-1"][PAYLOAD_PROPERTIES]["pvPw"] == live_pv_power
    assert merged["dev-1"][PAYLOAD_PROPERTIES]["soc"] == live_soc
    assert merged["dev-1"][PAYLOAD_HTTP_PROPERTIES]["pvPw"] == fresh_http_pv_power
    assert merged["dev-1"][PAYLOAD_HTTP_PROPERTIES]["soc"] == live_soc
    assert merged["dev-1"]["stat"] == {"day": 2}


def test_concurrent_reapply_preserves_each_layer5_field_timestamp() -> None:
    """Mixed BLE/MQTT deltas retain per-field ordering across an HTTP await."""
    coordinator = _coordinator()
    mqtt_time = datetime.now(UTC) - timedelta(seconds=2)
    ble_time = mqtt_time + timedelta(seconds=1)
    properties = coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
        "dev-1",
        {},
        {"soc": 76},
        source=TransportSource.CLOUD_MQTT,
        observed_at=mqtt_time,
    )
    properties = coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
        "dev-1",
        properties,
        {"pvPw": 20_620},
        source=TransportSource.BLE,
        observed_at=ble_time,
    )
    coordinator.data = {"dev-1": {PAYLOAD_PROPERTIES: properties}}
    baseline = {"dev-1": {PAYLOAD_PROPERTIES: {"soc": 75, "pvPw": 600}}}
    http_result = {
        "dev-1": {
            PAYLOAD_PROPERTIES: {"soc": 75, "pvPw": 650},
            PAYLOAD_HTTP_PROPERTIES: {"soc": 75, "pvPw": 650},
        },
    }

    merged = coordinator._merge_concurrent_coordinator_updates(  # ruff: ignore[private-member-access]
        baseline,
        http_result,
    )

    assert merged["dev-1"][PAYLOAD_PROPERTIES]["soc"] == 76
    assert merged["dev-1"][PAYLOAD_PROPERTIES]["pvPw"] == 20_620
    provenance = coordinator._property_source_state["dev-1"]  # ruff: ignore[private-member-access]
    assert provenance["soc"].source is TransportSource.CLOUD_MQTT
    assert provenance["soc"].observed_at == mqtt_time
    assert provenance["pvPw"].source is TransportSource.BLE
    assert provenance["pvPw"].observed_at == ble_time

    stale = coordinator._merge_partial_device_update(  # ruff: ignore[private-member-access]
        "dev-1",
        merged["dev-1"],
        {PAYLOAD_PROPERTIES: {"pvPw": 500}},
        source=TransportSource.LOCAL_MQTT,
        observed_at=mqtt_time - timedelta(hours=5),
    )
    assert stale[PAYLOAD_PROPERTIES]["pvPw"] == 20_620


def test_http_rebuild_preserves_circuit_and_generic_subdevice_buckets() -> None:
    """Transport-only entity payloads must survive every fast HTTP refresh."""
    assert PAYLOAD_CIRCUIT_PROPERTY in PRESERVED_FAST_PAYLOAD_KEYS
    assert PAYLOAD_SUBDEVICES in PRESERVED_FAST_PAYLOAD_KEYS


def test_local_system_patch_updates_http_rebuild_index() -> None:
    """Accepted live grid metadata must not revert on the next HTTP rebuild."""
    coordinator = _coordinator({"dev-1": {PAYLOAD_SYSTEM: {}}})
    coordinator._device_index = {  # ruff: ignore[private-member-access]
        "dev-1": {PAYLOAD_SYSTEM_META: {"timezone": "Europe/Berlin"}}
    }

    coordinator._apply_local_system_patch(  # ruff: ignore[private-member-access]
        "dev-1",
        {FIELD_GRID_STANDARD: "VDE-AR-N 4105"},
    )

    assert (
        coordinator._device_index["dev-1"][PAYLOAD_SYSTEM_META][FIELD_GRID_STANDARD]  # ruff: ignore[private-member-access]
        == "VDE-AR-N 4105"
    )


def test_ble_coalescer_uses_pending_snapshot_as_next_frame_base() -> None:
    """Two BLE frames in one coalescing window must accumulate."""
    coordinator = _coordinator({"dev-1": {PAYLOAD_PROPERTIES: {"pvPw": 100}}})
    pending = {PAYLOAD_PROPERTIES: {"pvPw": 200, "soc": 50}}
    coordinator._ble_pending_updates["dev-1"] = pending  # ruff: ignore[private-member-access]

    assert coordinator._ble_partial_update_base("dev-1") is pending  # ruff: ignore[private-member-access]


def test_subdevice_merge_appends_new_identified_serial() -> None:
    """A new serial must not overwrite a different device at the same index."""
    current = [{FIELD_DEVICE_SN: "plug-a", "power": 1}]
    updates = [{FIELD_DEVICE_SN: "plug-b", "power": 2}]

    assert merge_subdevice_lists_by_sn(current, updates) == [
        {FIELD_DEVICE_SN: "plug-a", "power": 1},
        {FIELD_DEVICE_SN: "plug-b", "power": 2},
    ]
    assert merge_sub_devices(current, updates) == [
        {FIELD_DEVICE_SN: "plug-a", "power": 1},
        {FIELD_DEVICE_SN: "plug-b", "power": 2},
    ]


def test_subdevice_merge_uses_position_only_without_serial() -> None:
    """An identity-less partial update may still enrich its list position."""
    current = [{FIELD_DEVICE_SN: "plug-a", "power": 1}]

    assert merge_subdevice_lists_by_sn(current, [{"power": 2}]) == [
        {FIELD_DEVICE_SN: "plug-a", "power": 2},
    ]


def test_pack_ota_never_cross_assigns_an_unknown_serial() -> None:
    """OTA metadata for an unknown serial must not mutate another pack."""
    current = [{FIELD_DEVICE_SN: "pack-a", FIELD_CURRENT_VERSION: "1.0.0"}]
    updates = [{FIELD_DEVICE_SN: "pack-b", FIELD_CURRENT_VERSION: "2.0.0"}]

    assert merge_battery_pack_ota_lists(current, updates) == current


def test_active_property_overrides_expire_after_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local write beats snapshots until the override TTL elapses."""
    coordinator = _coordinator({"dev-1": {}})
    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.coordinator.time.monotonic",
        lambda: clock["now"],
    )
    coordinator._property_overrides["dev-1"] = (  # ruff: ignore[private-member-access]
        clock["now"],
        {"workModel": 3},
    )

    assert coordinator._active_property_overrides("dev-1") == {"workModel": 3}  # ruff: ignore[private-member-access]

    clock["now"] += JackerySolarVaultCoordinator._PROPERTY_OVERRIDE_TTL_SEC + 1  # ruff: ignore[private-member-access]

    assert coordinator._active_property_overrides("dev-1") == {}  # ruff: ignore[private-member-access]
    assert "dev-1" not in coordinator._property_overrides  # ruff: ignore[private-member-access]


def test_merge_partial_update_live_push_wins() -> None:
    """Slice-2 revert: a live BLE/MQTT push wins for live keys on the push path.

    AGENTS.md §1.2 — local transports are preferred for live values. The
    2026-07-17 fill-only strip that shielded present HTTP keys is gone, so an
    incoming live value overrides the current snapshot, still adds new keys,
    and leaves HTTP-only keys untouched.
    """
    coordinator = _coordinator()
    current = {
        PAYLOAD_HTTP_PROPERTIES: {"pvPw": _HTTP_POWER},
        PAYLOAD_PROPERTIES: {"pvPw": _HTTP_POWER, "keepme": 1},
    }
    incoming = {
        PAYLOAD_PROPERTIES: {"pvPw": _STALE_POWER, "extra": _FILL_VALUE},
    }

    merged = coordinator._merge_partial_device_update(  # ruff: ignore[private-member-access]
        "dev-1",
        current,
        incoming,
    )

    props = merged[PAYLOAD_PROPERTIES]
    assert props["pvPw"] == _STALE_POWER
    assert props["extra"] == _FILL_VALUE
    assert props["keepme"] == 1


def test_background_http_partial_cannot_reverse_fresh_layer5_live_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow HTTP payload cannot reset PV/SOC immediately after a BLE frame."""
    coordinator = _coordinator()
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.coordinator.time.monotonic",
        lambda: 1_000.0,
    )
    layer5_values = {"pvPw": 20_620, "soc": 76}
    current = {
        PAYLOAD_PROPERTIES: coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
            "dev-1",
            {},
            layer5_values,
            source=TransportSource.BLE,
        ),
        PAYLOAD_HTTP_PROPERTIES: {"pvPw": 650, "soc": 70},
    }

    merged = coordinator._merge_partial_device_update(  # ruff: ignore[private-member-access]
        "dev-1",
        current,
        {
            PAYLOAD_PROPERTIES: {"pvPw": 650, "soc": 70},
            PAYLOAD_HTTP_PROPERTIES: {"pvPw": 650, "soc": 70},
        },
        source=TransportSource.HTTP,
    )

    assert merged[PAYLOAD_PROPERTIES]["pvPw"] == layer5_values["pvPw"]
    assert merged[PAYLOAD_PROPERTIES]["soc"] == layer5_values["soc"]
    assert merged[PAYLOAD_HTTP_PROPERTIES]["pvPw"] == 650
    assert merged[PAYLOAD_HTTP_PROPERTIES]["soc"] == 70


def test_unchanged_http_partial_does_not_relabel_layer5_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stats-only HTTP refresh leaves unchanged Layer-5 ownership intact."""
    coordinator = _coordinator()
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.coordinator.time.monotonic",
        lambda: 1_000.0,
    )
    live_values = {"pvPw": 20_620, "soc": 76}
    current = {
        PAYLOAD_PROPERTIES: coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
            "dev-1",
            {},
            live_values,
            source=TransportSource.LOCAL_MQTT,
        ),
    }

    merged = coordinator._merge_partial_device_update(  # ruff: ignore[private-member-access]
        "dev-1",
        current,
        {
            PAYLOAD_PROPERTIES: dict(live_values),
            PAYLOAD_HTTP_PROPERTIES: dict(live_values),
            "device_pv_stat_day": {"totalSolarEnergy": 0.65},
        },
        source=TransportSource.HTTP,
    )

    assert merged[PAYLOAD_PROPERTIES]["pvPw"] == live_values["pvPw"]
    assert merged[PAYLOAD_PROPERTIES]["soc"] == live_values["soc"]
    assert (
        coordinator._property_source_state["dev-1"]["pvPw"].source  # ruff: ignore[private-member-access]
        is TransportSource.LOCAL_MQTT
    )


def test_merge_main_properties_for_device_live_updates_win() -> None:
    """Reverted policy: a live (MQTT/BLE) frame merges straight and wins.

    Slice-2 revert (AGENTS.md §1.2: local is preferred for live values). The
    2026-07-17 fill-only gate that shielded present HTTP keys is gone, so a
    supplemental update now overrides the base value and still adds new keys.
    """
    coordinator = _coordinator(
        {"dev-1": {PAYLOAD_HTTP_PROPERTIES: {"workModel": _HTTP_POWER}}},
    )

    merged = coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
        "dev-1",
        {"workModel": _HTTP_POWER},
        {"workModel": _STALE_POWER, "extra": _FILL_VALUE},
    )

    assert merged["workModel"] == _STALE_POWER
    assert merged["extra"] == _FILL_VALUE


def test_merge_main_properties_for_device_overrides_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh local override outranks both base and incoming values."""
    coordinator = _coordinator({"dev-1": {}})
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.coordinator.time.monotonic",
        lambda: 1_000.0,
    )
    coordinator._property_overrides["dev-1"] = (1_000.0, {"workModel": 9})  # ruff: ignore[private-member-access]

    merged = coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
        "dev-1",
        {"workModel": 1},
        {"workModel": 2},
    )

    assert merged["workModel"] == 9


def test_apply_local_property_patch_updates_data_and_records_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A setter's optimistic patch mutates data and stamps an override."""
    coordinator = _coordinator(
        {"dev-1": {PAYLOAD_PROPERTIES: {"pvPw": _HTTP_POWER}}},
    )
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.coordinator.time.monotonic",
        lambda: 2_000.0,
    )

    coordinator._apply_local_property_patch("dev-1", {"workModel": 4})  # ruff: ignore[private-member-access]

    assert coordinator.data["dev-1"][PAYLOAD_PROPERTIES]["workModel"] == 4
    assert coordinator.data["dev-1"][PAYLOAD_PROPERTIES]["pvPw"] == _HTTP_POWER
    assert coordinator._property_overrides["dev-1"][1]["workModel"] == 4  # ruff: ignore[private-member-access]


def test_apply_local_property_patch_is_noop_for_unknown_device() -> None:
    """Patching a device absent from data leaves coordinator state untouched."""
    coordinator = _coordinator({"dev-1": {PAYLOAD_PROPERTIES: {}}})

    coordinator._apply_local_property_patch("ghost", {"workModel": 4})  # ruff: ignore[private-member-access]

    assert coordinator.data["dev-1"][PAYLOAD_PROPERTIES] == {}
    assert "ghost" not in coordinator.data
