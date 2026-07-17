"""A battery-pack sensor tracks its pack by serial, not list position.

Owner: the add-on battery ("Zusatzbatterie") showed duplicate/Unbekannt sets.
Index-only resolution (``packs[pack_index - 1]``) meant a reordered
``battery_packs`` list made an entity read a sibling pack's values or flip to
Unknown. The entity now pins its pack's serial on first resolution and matches
by serial thereafter.
"""

from typing import Any
from unittest.mock import PropertyMock, patch

from custom_components.jackery_solarvault.const import PAYLOAD_BATTERY_PACKS
from custom_components.jackery_solarvault.sensor import (
    JackeryBatteryPackSensor,
    _battery_pack_serial,  # ruff:ignore[import-private-name]  # test drives the module-private serial resolver
)

_SN_A = "HQ2C01400955HP3"
_SN_B = "HQ2C09990000ZZ9"
_SOC_A = 50
_SOC_B = 10


def test_battery_pack_serial_resolves_common_fields() -> None:
    """The serial resolver accepts deviceSn/devSn/sn and rejects empties."""
    assert _battery_pack_serial({"deviceSn": _SN_A}) == _SN_A
    assert _battery_pack_serial({"sn": _SN_B}) == _SN_B
    assert _battery_pack_serial({"batSoc": 5}) is None


def test_pack_tracks_by_serial_after_reorder() -> None:
    """After the list reorders, the entity still reads its own pack by serial."""
    sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
    sensor._pack_index = 1  # ruff:ignore[private-member-access]
    sensor._pack_sn = None  # ruff:ignore[private-member-access]

    ordered = {
        PAYLOAD_BATTERY_PACKS: [
            {"deviceSn": _SN_A, "batSoc": _SOC_A},
            {"deviceSn": _SN_B, "batSoc": _SOC_B},
        ],
    }
    reordered = {
        PAYLOAD_BATTERY_PACKS: [
            {"deviceSn": _SN_B, "batSoc": _SOC_B},
            {"deviceSn": _SN_A, "batSoc": _SOC_A},
        ],
    }

    with patch.object(
        JackeryBatteryPackSensor,
        "_payload",
        new_callable=PropertyMock,
    ) as payload:
        payload.return_value = ordered
        first: dict[str, Any] = sensor._pack  # ruff:ignore[private-member-access]
        assert first["deviceSn"] == _SN_A  # index 1 -> pack A, pins its serial

        payload.return_value = reordered  # pack A now sits at index 2
        second: dict[str, Any] = sensor._pack  # ruff:ignore[private-member-access]
        assert second["deviceSn"] == _SN_A  # still pack A by serial, not index-1 (B)
        assert second["batSoc"] == _SOC_A
