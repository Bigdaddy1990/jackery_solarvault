"""Regression tests for sparse live payload merging outside the ingest gate."""

from typing import Any

from custom_components.jackery_solarvault.const import (
    FIELD_DEVICE_SN,
    PAYLOAD_SUBDEVICES,
)
from custom_components.jackery_solarvault.handlers.property_merge import (
    merge_present_dict_values,
)

_BASE_POWER = 10


def test_merge_present_dict_values_merges_identified_dict_lists() -> None:
    """Sparse child-device updates preserve existing siblings and static fields."""
    base: dict[str, Any] = {
        PAYLOAD_SUBDEVICES: [
            {FIELD_DEVICE_SN: "plug-1", "model": "Smart Plug", "power": _BASE_POWER},
            {FIELD_DEVICE_SN: "plug-2", "model": "Smart Plug", "power": 20},
        ],
        "modes": ["auto", "manual"],
    }
    update: dict[str, Any] = {
        PAYLOAD_SUBDEVICES: [
            {FIELD_DEVICE_SN: "plug-1", "power": 11, "model": None},
            {FIELD_DEVICE_SN: "plug-3", "model": "Smart Plug", "power": 30},
        ],
        "modes": ["eco"],
    }

    merged = merge_present_dict_values(base, update)

    assert merged[PAYLOAD_SUBDEVICES] == [
        {FIELD_DEVICE_SN: "plug-1", "model": "Smart Plug", "power": 11},
        {FIELD_DEVICE_SN: "plug-2", "model": "Smart Plug", "power": 20},
        {FIELD_DEVICE_SN: "plug-3", "model": "Smart Plug", "power": 30},
    ]
    assert merged["modes"] == ["eco"]
    assert base[PAYLOAD_SUBDEVICES][0]["power"] == _BASE_POWER


def test_merge_present_dict_values_keeps_existing_list_on_blank_update() -> None:
    """Sparse live updates must not erase populated list values."""
    merged = merge_present_dict_values(
        {"modes": ["auto", "manual"]},
        {"modes": []},
    )

    assert merged["modes"] == ["auto", "manual"]
