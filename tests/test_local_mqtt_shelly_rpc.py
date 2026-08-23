"""Event-driven Shelly RPC ingestion for Jackery smart-meter entities."""

from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_CT_APPARENT_POWER,
    FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_A_NEGATIVE_PHASE_POWER,
    FIELD_CT_A_PHASE_ENERGY,
    FIELD_CT_A_PHASE_POWER,
    FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_B_NEGATIVE_PHASE_POWER,
    FIELD_CT_B_PHASE_ENERGY,
    FIELD_CT_B_PHASE_POWER,
    FIELD_CT_CURRENT,
    FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_C_NEGATIVE_PHASE_POWER,
    FIELD_CT_C_PHASE_ENERGY,
    FIELD_CT_C_PHASE_POWER,
    FIELD_CT_FREQUENCY,
    FIELD_CT_POWER,
    FIELD_CT_POWER1,
    FIELD_CT_POWER2,
    FIELD_CT_POWER3,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_CT_TOTAL_PHASE_POWER,
    FIELD_CT_VOLT1,
    FIELD_DEVICE_SN,
    FIELD_DEV_TYPE,
    FIELD_SCAN_NAME,
    PAYLOAD_CT_METER,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    shelly_rpc_ct_update,
)

_DEVICE_ID = "573702884982521856"
_SHELLY_MAC = "5c013b048e3c"


def _rpc_payload(component: str, status: dict[str, Any]) -> dict[str, Any]:
    return {
        "body": {
            "src": f"shellypro3em-{_SHELLY_MAC}",
            "method": "NotifyStatus",
            "params": {component: status, "ts": 1787286221.23},
        },
    }


def test_shelly_rpc_power_maps_signed_phases_and_electrical_fields() -> None:
    """One pushed em:0 frame becomes the complete live CT power surface."""
    parsed = shelly_rpc_ct_update(
        _rpc_payload(
            "em:0",
            {
                "a_act_power": 2.7,
                "b_act_power": -273.2,
                "c_act_power": 254.6,
                "total_act_power": -15.9,
                "a_aprt_power": 9.3,
                "b_aprt_power": 330.3,
                "c_aprt_power": 339.4,
                "total_aprt_power": 678.977,
                "a_current": 0.04,
                "b_current": 1.419,
                "c_current": 1.455,
                "total_current": 2.914,
                "a_freq": 50.0,
                "b_freq": 50.0,
                "c_freq": 50.0,
                "a_voltage": 231.9,
                "b_voltage": 232.9,
                "c_voltage": 233.3,
            },
        ),
    )

    assert parsed is not None
    mac, update = parsed
    assert mac == _SHELLY_MAC
    assert update[FIELD_DEVICE_SN] == _SHELLY_MAC
    assert update[FIELD_SCAN_NAME] == "shellypro3em"
    assert update[FIELD_DEV_TYPE] == 3
    assert update[FIELD_CT_POWER1] == pytest.approx(2.7)
    assert update[FIELD_CT_POWER2] == pytest.approx(-273.2)
    assert update[FIELD_CT_POWER3] == pytest.approx(254.6)
    assert update[FIELD_CT_POWER] == pytest.approx(-15.9)
    assert update[FIELD_CT_A_PHASE_POWER] == pytest.approx(2.7)
    assert update[FIELD_CT_A_NEGATIVE_PHASE_POWER] == 0
    assert update[FIELD_CT_B_PHASE_POWER] == 0
    assert update[FIELD_CT_B_NEGATIVE_PHASE_POWER] == pytest.approx(273.2)
    assert update[FIELD_CT_C_PHASE_POWER] == pytest.approx(254.6)
    assert update[FIELD_CT_C_NEGATIVE_PHASE_POWER] == 0
    assert update[FIELD_CT_TOTAL_PHASE_POWER] == 0
    assert update[FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER] == pytest.approx(15.9)
    assert update[FIELD_CT_VOLT1] == pytest.approx(231.9)
    assert update[FIELD_CT_CURRENT] == pytest.approx(2.914)
    assert update[FIELD_CT_FREQUENCY] == pytest.approx(50.0)
    assert update[FIELD_CT_APPARENT_POWER] == pytest.approx(678.977)


def test_shelly_rpc_energy_maps_wh_import_and_return_counters() -> None:
    """The pushed emdata:0 counters feed every lifetime CT energy sensor."""
    parsed = shelly_rpc_ct_update(
        _rpc_payload(
            "emdata:0",
            {
                "a_total_act_energy": 18121.24,
                "a_total_act_ret_energy": 0.01,
                "b_total_act_energy": 73442.96,
                "b_total_act_ret_energy": 1347973.9,
                "c_total_act_energy": 989772.63,
                "c_total_act_ret_energy": 7779.83,
                "total_act": 1081336.83,
                "total_act_ret": 1355753.74,
            },
        ),
    )

    assert parsed is not None
    _, update = parsed
    assert update[FIELD_CT_A_PHASE_ENERGY] == pytest.approx(18121.24)
    assert update[FIELD_CT_A_NEGATIVE_PHASE_ENERGY] == pytest.approx(0.01)
    assert update[FIELD_CT_B_PHASE_ENERGY] == pytest.approx(73442.96)
    assert update[FIELD_CT_B_NEGATIVE_PHASE_ENERGY] == pytest.approx(1347973.9)
    assert update[FIELD_CT_C_PHASE_ENERGY] == pytest.approx(989772.63)
    assert update[FIELD_CT_C_NEGATIVE_PHASE_ENERGY] == pytest.approx(7779.83)
    assert update[FIELD_CT_TOTAL_PHASE_ENERGY] == pytest.approx(1081336.83)
    assert update[FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY] == pytest.approx(1355753.74)


def test_non_shelly_or_non_status_rpc_is_not_claimed() -> None:
    """Unrelated broker traffic remains available to the normal MQTT parser."""
    assert shelly_rpc_ct_update({"body": {"src": "homeassistant"}}) is None
    payload = _rpc_payload("em:0", {"total_act_power": 10})
    payload["body"]["method"] = "NotifyEvent"
    assert shelly_rpc_ct_update(payload) is None


def test_direct_shelly_rpc_payload_is_supported() -> None:
    """Native broker payloads without an integration wrapper remain push-compatible."""
    wrapped = _rpc_payload("em:0", {"total_act_power": 10.5})

    parsed = shelly_rpc_ct_update(wrapped["body"])

    assert parsed is not None
    assert parsed[1][FIELD_CT_POWER] == pytest.approx(10.5)


@pytest.mark.asyncio
async def test_shelly_rpc_frame_pushes_coordinator_update_immediately() -> None:
    """A broker callback updates the matching CT without a refresh request."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator).data = {
        _DEVICE_ID: {
            PAYLOAD_CT_METER: {
                FIELD_DEVICE_SN: "5C:01:3B:04:8E:3C",
                FIELD_SCAN_NAME: "shellypro3em",
                FIELD_DEV_TYPE: 3,
            },
        },
    }
    cast("Any", coordinator)._shutdown_started = False
    cast("Any", coordinator)._listeners = set()
    cast("Any", coordinator)._device_registry_observer = None
    cast("Any", coordinator)._live_ct_received_monotonic = {}
    cast("Any", coordinator)._local_mqtt_last_device_message_monotonic = {}
    cast("Any", coordinator)._local_mqtt_device_traffic_observed_ids = set()
    cast("Any", coordinator)._local_mqtt_device_traffic_observed = False
    cast("Any", coordinator)._local_mqtt_any_traffic_observed_ids = set()
    cast("Any", coordinator)._local_mqtt_head_traffic_observed_ids = set()
    cast("Any", coordinator)._local_mqtt_lifetime_traffic_observed_ids = set()
    cast("Any", coordinator)._local_mqtt_last_message_monotonic = float("-inf")

    accepted = await coordinator.async_handle_local_mqtt_message(
        "homeassistant/events/rpc",
        _rpc_payload("em:0", {"total_act_power": 42.5}),
    )

    assert accepted is True
    ct = cast("dict[str, Any]", coordinator.data[_DEVICE_ID][PAYLOAD_CT_METER])
    assert ct[FIELD_CT_POWER] == pytest.approx(42.5)
    assert ct[FIELD_CT_TOTAL_PHASE_POWER] == pytest.approx(42.5)
    assert coordinator._live_ct_received_monotonic[_DEVICE_ID] > 0
    assert coordinator._local_mqtt_any_traffic_observed_ids == {_DEVICE_ID}
    assert coordinator._local_mqtt_head_traffic_observed_ids == set()
    assert coordinator._local_mqtt_lifetime_traffic_observed_ids == set()
    assert coordinator._local_mqtt_device_traffic_observed is False
