"""Unit tests for BLE cmd=120 frame routing decisions (Finding E).

These tests do NOT spin up a full coordinator. They verify the routing
predicate expressed in coordinator.py:1314-1316 — that only devType=1
(battery-pack) frames with a deviceSn are routed, and that the three
unrouted variants (system-level, per-device non-pack, CT-phase lifetime)
are explicitly NOT routed.

When the firmware wire format for those variants is confirmed, the test
that asserts they are unrouted should be updated alongside the routing
logic. This file is the audit trail that makes that future change visible
in code review.
"""


CMD_QUERY_COMBINE_DATA = 120  # MQTT_CMD_QUERY_COMBINE_DATA
SUBDEVICE_DEV_TYPE_BATTERY_PACK = 1  # devType=1 identifies add-on battery packs


def _should_route(payload: dict) -> bool:
    """Mirror the coordinator's routing predicate for cmd=120 frames."""
    return (
        payload.get("cmd") == CMD_QUERY_COMBINE_DATA
        and payload.get("devType") == SUBDEVICE_DEV_TYPE_BATTERY_PACK
        and bool(payload.get("deviceSn"))
    )


# ---------------------------------------------------------------------------
# Routed variant
# ---------------------------------------------------------------------------

def test_battery_pack_frame_is_routed() -> None:
    """devType=1 + deviceSn → routed to _merge_battery_pack_lifetime_from_ble."""
    payload = {
        "cmd": 120,
        "devType": 1,
        "deviceSn": "HR2C04000280HH3",
        "inEgy": 45000,
        "outEgy": 38000,
    }
    assert _should_route(payload), "battery-pack cmd=120 frame must be routed"


# ---------------------------------------------------------------------------
# Unrouted variants — intentionally not routed (Finding E)
# ---------------------------------------------------------------------------

def test_system_level_frame_is_not_routed() -> None:
    """System-level cmd=120 (no devType) → unrouted.

    These frames carry system-wide lifetime counters already available
    via HTTP and are intentionally kept in the unrouted counter.
    Update this assertion when the wire format is confirmed and routing
    is added to coordinator.py.
    """
    payload = {
        "cmd": 120,
        "pvEgy": 350370,
        "batChgEgy": 99140,
        "batDisChgEgy": 85590,
    }
    assert not _should_route(payload), "system-level cmd=120 frame must NOT be routed"


def test_per_device_non_pack_frame_is_not_routed() -> None:
    """Per-device (devType != 1) cmd=120 → unrouted.

    These carry the main device's lifetime counters which HTTP already
    delivers authoritatively. Update when HTTP authority is revisited.
    """
    payload = {
        "cmd": 120,
        "devType": 0,  # main device, not a battery pack
        "deviceSn": "MAIN_DEVICE_SN",
        "pvEgy": 350370,
    }
    assert not _should_route(payload), "per-device non-pack cmd=120 must NOT be routed"


def test_ct_phase_lifetime_frame_is_not_routed() -> None:
    """CT-phase lifetime cmd=120 → unrouted.

    CT-phase lifetime energy (aPhaseEgy etc.) is delivered via HTTP
    and MQTT property frames. Until the BLE variant's field mapping is
    confirmed in the protocol spec, it stays unrouted.
    """
    payload = {
        "cmd": 120,
        "devType": 3,  # CT meter / Shelly 3EM
        "deviceSn": "5c013b048e3c",
        "aPhaseEgy": 29276,
        "tPhaseEgy": 29276,
    }
    assert not _should_route(payload), "CT-phase cmd=120 frame must NOT be routed"


def test_battery_pack_frame_without_sn_is_not_routed() -> None:
    """devType=1 but missing deviceSn → unrouted (can't identify target pack)."""
    payload = {
        "cmd": 120,
        "devType": 1,
        "inEgy": 45000,
        "outEgy": 38000,
    }
    assert not _should_route(payload), "battery-pack frame with no SN must NOT be routed"
