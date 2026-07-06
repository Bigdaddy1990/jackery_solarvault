"""Regression tests for subdevice detection helpers."""

from custom_components.jackery_solarvault.const import (
    BATTERY_PACK_HINT_KEYS,
    FIELD_DEVICE_SN,
    FIELD_DEV_TYPE,
    FIELD_SUB_DEVICE,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
)
from custom_components.jackery_solarvault.handlers.detector import (
    battery_packs_from_source,
)


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
