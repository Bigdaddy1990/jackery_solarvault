"""Regression tests for subdevice detection helpers."""

import pytest

from custom_components.jackery_solarvault.const import (
    BATTERY_PACK_HINT_KEYS,
    FIELD_DEVICE_SN,
    FIELD_DEV_TYPE,
    FIELD_OUT_PW,
    FIELD_SUB_DEVICE,
    PAYLOAD_DEVICE,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    _SubdeviceMergeContext,  # ruff: ignore[import-private-name]
    battery_packs_from_source,
    merge_battery_pack_lists,
)
from custom_components.jackery_solarvault.descriptions.sensor import (
    BATTERY_PACK_SENSOR_DESCRIPTIONS,
)
from custom_components.jackery_solarvault.ingest import TransportSource
from custom_components.jackery_solarvault.sensor import JackeryBatteryPackSensor


def test_battery_pack_dev_type_detects_identity_only_cmd110_payload() -> None:
    """cmd=110/devType=1 BatteryPackSub frames may arrive before live fields."""
    source = {
        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK,
        FIELD_SUB_DEVICE: [
            {
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK,
                FIELD_DEVICE_SN: "pack-1",
                "commState": 1,
            },
        ],
    }

    assert battery_packs_from_source(
        source,
        frozenset(),
        BATTERY_PACK_HINT_KEYS,
    ) == [
        {
            FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK,
            FIELD_DEVICE_SN: "pack-1",
            "commState": 1,
        },
    ]


def test_battery_pack_request_selector_is_not_a_pack() -> None:
    """A cmd=110 request selector has no pack identity or telemetry."""
    assert (
        battery_packs_from_source(
            {FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK},
            frozenset(),
            BATTERY_PACK_HINT_KEYS,
        )
        is None
    )


def test_battery_pack_merge_prunes_persisted_request_selector() -> None:
    """A previously accepted devType-only selector cannot remain as a pack."""
    real_pack = {
        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK,
        FIELD_DEVICE_SN: "pack-1",
        FIELD_OUT_PW: 353,
    }

    assert merge_battery_pack_lists(
        [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK}, real_pack],
        [real_pack],
    ) == [real_pack]


def test_parent_serial_repeated_on_mqtt_pack_rows_keeps_all_packs() -> None:
    """The parent serial on each row is not a unique pack identity."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    rows = [
        {FIELD_DEVICE_SN: "head", FIELD_OUT_PW: power}
        for power in (107, 99, 128)
    ]
    normalized = coordinator._drop_head_unit_packs(  # ruff: ignore[private-member-access]
        rows,
        None,
        {PAYLOAD_DEVICE: {FIELD_DEVICE_SN: "head"}},
    )
    assert len(normalized) == len(rows)
    assert all(FIELD_DEVICE_SN not in row for row in normalized)
    assert [row[FIELD_OUT_PW] for row in merge_battery_pack_lists([], normalized)] == [
        107, 99, 128
    ]
    assert [row[FIELD_OUT_PW] for row in merge_battery_pack_lists(
        [{FIELD_DEVICE_SN: f"pack-{index}", FIELD_OUT_PW: 0} for index in range(3)],
        normalized,
    )] == [107, 99, 128]

    context = _SubdeviceMergeContext(
        updated={PAYLOAD_DEVICE: {FIELD_DEVICE_SN: "head"}},
        device_id=None,
        source_transport=TransportSource.LOCAL_MQTT,
        observed_at=None,
    )
    coordinator._merge_battery_pack_updates(  # ruff: ignore[private-member-access]
        context, rows
    )
    assert [pack[FIELD_OUT_PW] for pack in context.updated["battery_packs"]] == [
        107, 99, 128
    ]


def test_battery_pack_temperature_is_also_a_diagnostic_attribute() -> None:
    """The firmware diagnostics retain the measured pack temperature."""
    diagnostic = next(
        description
        for description in BATTERY_PACK_SENSOR_DESCRIPTIONS
        if description.key == "firmware_version"
    )
    sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
    sensor._pack_index = 1  # ruff: ignore[private-member-access]
    sensor.entity_description = diagnostic
    attrs = sensor._attrs_from_pack({"cellTemp": 259})  # ruff: ignore[private-member-access]
    assert attrs["cell_temperature"] == pytest.approx(25.9)


def test_battery_pack_extraction_still_ignores_unknown_subdevice_wrappers() -> None:
    """Non-pack wrappers without BatteryPackSub hints stay out of pack entities."""
    source = {
        FIELD_SUB_DEVICE: [
            {
                FIELD_DEVICE_SN: "unknown-1",
                "commState": 1,
            },
        ],
    }

    assert (
        battery_packs_from_source(
            source,
            frozenset(),
            BATTERY_PACK_HINT_KEYS,
        )
        is None
    )
