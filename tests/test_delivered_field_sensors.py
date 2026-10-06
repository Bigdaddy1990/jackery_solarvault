"""Entities for payload fields every layer delivers but nothing read before."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.descriptions.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
)
from custom_components.jackery_solarvault.descriptions.sensor import SENSOR_DESCRIPTIONS
from custom_components.jackery_solarvault.filters import sanitize_main_properties
from custom_components.jackery_solarvault.sensor import JackeryAlarmSensor
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

_SENSORS = {d.key: d for d in SENSOR_DESCRIPTIONS}
_BINARY = {d.key: d for d in BINARY_SENSOR_DESCRIPTIONS}
_PV_HOME, _PV_BATTERY, _BATTERY_FROM_PV, _STANDBY_W = 45, 33, 91, 25
_BMS3_FAULT = 7
_TODAY_SHARE_COUNT = 13


def _entity(
    properties: dict[str, Any], sections: dict[str, Any], description: Any = None
) -> Any:
    return SimpleNamespace(
        entity_description=description,
        merged_properties=properties,
        _payload={"properties": properties, **sections},
        payload_section_for_sources=lambda section: sections.get(section) or {},
    )


@pytest.mark.parametrize(
    ["key", "field", "raw", "kwh"],
    [
        ["pv_to_grid_side_lifetime_energy", "pvOtOngridEgy", 97357, 973.57],
        ["battery_to_ac_lifetime_energy", "batOtAcEgy", 1372, 13.72],
        ["grid_side_to_ac_load_lifetime_energy", "ongridOtAcLoadEgy", 178, 1.78],
        ["pv_to_ac_lifetime_energy", "pvOtAcEgy", 750, 7.5],
        ["ac_to_battery_lifetime_energy", "acOtBatEgy", 0, 0.0],
    ],
)
def test_lifetime_counter_is_published_in_kwh(
    key: str, field: str, raw: int, kwh: float
) -> None:
    """Device lifetime counters arrive in 0.01 kWh units on BLE/local MQTT."""
    description = _SENSORS[key]
    entity = _entity({field: raw}, {}, description)

    assert description.value_fn(cast("Any", entity)) == pytest.approx(kwh)


def test_today_energy_flow_shares_come_from_the_day_trend_sections() -> None:
    """App flow percentages are nested objects inside the day trend responses."""
    entity = _entity(
        {},
        {
            "pv_trends": {
                "pvUsage": {"home": _PV_HOME, "battery": _PV_BATTERY, "ac": 14}
            },
            "battery_trends": {
                "batterySources": {"pv": _BATTERY_FROM_PV, "home": 3, "ac": 0}
            },
        },
    )

    assert (
        _SENSORS["today_pv_usage_home_share"].value_fn(cast("Any", entity)) == _PV_HOME
    )
    assert (
        _SENSORS["today_pv_usage_battery_share"].value_fn(cast("Any", entity))
        == _PV_BATTERY
    )
    assert (
        _SENSORS["today_battery_source_pv_share"].value_fn(cast("Any", entity))
        == _BATTERY_FROM_PV
    )
    assert _SENSORS["today_home_source_pv_share"].value_fn(cast("Any", entity)) is None


@pytest.mark.parametrize(["state", "expected"], [[1, True], [0, False]])
def test_pv_channel_connectivity_follows_comm_state(state: int, expected: bool) -> None:
    """Each MPPT channel's nested commState drives its connectivity sensor."""
    entity = _entity({"pv2": {"pvPw": 312, "commState": state}}, {})

    assert _BINARY["pv2_connected"].value_fn(cast("Any", entity)) is expected
    assert _BINARY["pv3_connected"].value_fn(cast("Any", entity)) is None


def test_ble_standby_typo_fills_the_standby_power_field() -> None:
    """BLE reports ``standbyw``; the alias keeps the standby sensor fed."""
    assert sanitize_main_properties({"standbyw": _STANDBY_W})["standbyPw"] == _STANDBY_W


def test_device_alert_frames_feed_alert_and_fault_code_sensors() -> None:
    """BLE cmd 122 fault codes and cloud alert counters share one alert section."""
    entity = _entity(
        {},
        {"device_alert": {"sysAlertCount": 0, "alarmId": "402270", "bms3": 7}},
    )

    assert "alert_count" not in _SENSORS
    assert _SENSORS["last_alarm_id"].value_fn(cast("Any", entity)) == "402270"
    assert _SENSORS["bms3_fault_code"].value_fn(cast("Any", entity)) == _BMS3_FAULT
    assert _SENSORS["pcs1_fault_code"].value_fn(cast("Any", entity)) is None


def test_alarm_count_uses_documented_alert_counter_with_http_fallback() -> None:
    """Prefer HomeAlarmBody; keep a missing count unknown without HTTP data."""
    expected_count = 2
    coordinator = SimpleNamespace(
        data={
            "device": {
                "device_alert": {"sysAlertCount": expected_count},
                "alarm": [],
            }
        }
    )
    sensor = JackeryAlarmSensor(cast("Any", coordinator), "device")
    assert sensor.native_value == expected_count

    coordinator.data["device"].pop("device_alert")
    assert sensor.native_value == 0

    coordinator.data["device"].pop("alarm")
    assert sensor.native_value is None


def test_head_lifetime_identity_never_uses_stack_or_subtracted_counters() -> None:
    """Historical head IDs survive without claiming an unproven energy source."""
    properties = {"batNum": 3, "batChgEgy": 76514, "batDisChgEgy": 72468}
    packs = [
        {"inEgy": 1019, "outEgy": 939},
        {"inEgy": 943, "outEgy": 871},
        {"inEgy": 36322, "outEgy": 34484},
    ]
    entity = SimpleNamespace(
        payload={"battery_packs": packs},
        payload_section_for_sources=lambda section: (
            properties if section == "properties" else {}
        ),
    )
    for key in (
        "main_battery_charge_energy",
        "main_battery_discharge_energy",
        "main_battery_charge_energy_derived",
        "main_battery_discharge_energy_derived",
    ):
        description = _SENSORS[key]
        assert description.device_registry_role == "main_battery"
        assert description.value_fn(cast("Any", entity)) is None


def test_calculated_main_battery_energy_has_valid_ha_metadata() -> None:
    """Complete zero-start pack counters preserve a cumulative head counter."""
    for key in (
        "main_battery_charge_energy_derived",
        "main_battery_discharge_energy_derived",
    ):
        description = _SENSORS[key]
        assert description.device_class is SensorDeviceClass.ENERGY
        assert description.state_class is SensorStateClass.TOTAL_INCREASING


def test_period_flow_share_reads_its_own_period_section() -> None:
    """Week/month/year shares read the matching trend period, not today's."""
    entity = _entity({}, {"pv_trends_week": {"pvUsage": {"home": _PV_HOME}}})

    assert (
        _SENSORS["week_pv_usage_home_share"].value_fn(cast("Any", entity)) == _PV_HOME
    )
    assert _SENSORS["today_pv_usage_home_share"].value_fn(cast("Any", entity)) is None


def test_last_online_is_a_utc_timestamp_from_epoch_milliseconds() -> None:
    """Cloud device meta reports online/offline times as epoch milliseconds."""
    entity = SimpleNamespace(device_meta={"onlineTime": 1787963325000})

    value = _SENSORS["last_online"].value_fn(cast("Any", entity))

    assert value == datetime(2026, 8, 29, 0, 28, 45, tzinfo=UTC)
    assert _SENSORS["last_offline"].value_fn(cast("Any", entity)) is None


def test_today_flow_shares_are_user_facing_not_diagnostic() -> None:
    """Today's App flow shares are enabled by default (not diagnostic)."""
    shares = [d for d in SENSOR_DESCRIPTIONS if d.key.startswith("today_")]

    assert len(shares) == _TODAY_SHARE_COUNT
    assert all(d.entity_category is None for d in shares if d.key.endswith("_share"))
