"""Regression: CT/Smart-Meter period stats must use the accessory deviceId.

Background
----------
The CT/Smart-Meter is a sub-device (``devType=3``) with its own ``deviceId``
in the system ``accessories`` list. Per docs/Markdown/APP_POLLING_MQTT.md the
``/v1/device/stat/ct`` endpoint keys on that accessory id; calling it with the
main device id returns empty, leaving ``device_ct_stat_*`` (and the CT
statistic sensors) without values.

These tests lock down two things:

1. ``_smart_meter_accessory_device_id`` resolves the accessory id from a
   discovery-index entry (and falls back to the live ``ct_meter`` block).
2. The slow-metrics fetch is actually wired to use that resolved id for the
   CT-stat call (source-level guard, so it works without a HA runtime).
"""

from pathlib import Path
import re
from typing import Any

from custom_components.jackery_solarvault.const import (
    FIELD_ACCESSORIES,
    FIELD_SYSTEM_ID,
    PAYLOAD_CT_METER,
    PAYLOAD_SYSTEM_META,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

ROOT = Path(__file__).resolve().parents[1]
COORDINATOR_PATH = ROOT / "custom_components" / "jackery_solarvault" / "coordinator.py"


# ---------------------------------------------------------------------------
# Unit: accessory-id resolution (pure classmethod, no HA runtime needed)
# ---------------------------------------------------------------------------


def test_accessory_id_resolved_from_system_accessories() -> None:
    """A devType=3 accessory's deviceId is returned for the CT-stat call."""
    idx: dict[str, Any] = {
        FIELD_SYSTEM_ID: "595364183558991872",
        PAYLOAD_SYSTEM_META: {
            FIELD_ACCESSORIES: [
                {
                    "devType": 3,
                    "subType": 2,
                    "typeName": "Shelly Pro 3EM",
                    "deviceId": 2057219036232777730,
                    "deviceSn": "5c013b048e3c",
                },
            ],
        },
    }

    assert (  # noqa: S101
        JackerySolarVaultCoordinator._smart_meter_accessory_device_id(idx)  # noqa: SLF001
        == "2057219036232777730"
    )


def test_accessory_id_none_without_smart_meter() -> None:
    """No CT accessory present → None (caller then falls back to main id)."""
    idx: dict[str, Any] = {PAYLOAD_SYSTEM_META: {FIELD_ACCESSORIES: []}}
    assert JackerySolarVaultCoordinator._smart_meter_accessory_device_id(idx) is None  # noqa: S101, SLF001


def test_accessory_id_falls_back_to_ct_meter_block() -> None:
    """When no accessory metadata exists, the live ct_meter id is used."""
    source = {PAYLOAD_CT_METER: {"devType": 3, "deviceId": 2057219036232777730}}
    assert (  # noqa: S101
        JackerySolarVaultCoordinator._smart_meter_accessory_device_id(source)  # noqa: SLF001
        == "2057219036232777730"
    )


# ---------------------------------------------------------------------------
# Wiring guard: the CT-stat fetch must use the resolved accessory id
# (source-level so it runs without the HA test harness)
# ---------------------------------------------------------------------------


def _coordinator_src() -> str:
    return COORDINATOR_PATH.read_text(encoding="utf-8")


def test_ct_stat_call_uses_accessory_id_not_main_device_id() -> None:
    """The /v1/device/stat/ct call must pass ct_stat_device_id, not dev_id."""
    src = _coordinator_src()
    # The CT-stat call resolves to the accessory-scoped id.
    assert re.search(r"async_get_device_ct_stat\(\s*ct_stat_device_id", src), (  # noqa: S101
        "CT-stat call must use ct_stat_device_id (accessory id), not the main dev_id"
    )
    # And that id is derived with a fallback to the main id.
    assert "ct_stat_device_id = ct_dev_id or dev_id" in src  # noqa: S101


def test_extras_fetch_is_wired_to_accessory_resolver() -> None:
    """The slow-metrics caller must resolve and pass the CT accessory id."""
    src = _coordinator_src()
    assert "_smart_meter_accessory_device_id(idx)" in src, (  # noqa: S101
        "caller must resolve the CT accessory deviceId from the discovery index"
    )
    # _fetch_device_extras must accept the resolved id.
    assert re.search(r"_fetch_device_extras\([\s\S]{0,200}ct_dev_id", src), (  # noqa: S101
        "_fetch_device_extras must accept ct_dev_id"
    )
