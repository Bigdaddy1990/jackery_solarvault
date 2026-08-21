"""High-risk contracts extracted from the Jackery 2.4.0 App evidence.

These values intentionally do not import integration code.  They are the
independent side of contract tests and may only be changed when newer
authoritative App evidence under ``docs/source-of-truth/APP`` proves a change.
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class CommandContract:
    """Transport identifiers for one App-proven command."""

    action_id: int
    ble_message_type: int
    mqtt_command: int


@dataclass(frozen=True, slots=True)
class AppFieldExposureContract:
    """Expected integration treatment for one App-proven model field."""

    model: str
    field: str
    classification: str
    platform: str | None
    entity_key: str | None
    source_path: str
    sources: frozenset[str]
    rationale: str = ""


REST_ENDPOINTS: Final = MappingProxyType({
    "pv_trends": "/v1/device/stat/sys/pv/trends",
    "dynamic_price": "/v1/device/dynamic/dynamicPrice",
})

# No App 2.4.0 evidence identifies a Jackery WebSocket or generic REST device
# control channel.  HTTP setters are limited to the explicit REST operations
# catalogued separately; live device commands use BLE or cloud MQTT.
REALTIME_CONTROL_TRANSPORTS: Final = frozenset({"ble", "cloud_mqtt"})
HTTP_SETTER_FAMILIES: Final = frozenset({"system_name", "dynamic_price", "tariff"})

SYSTEM_BODY_FIELDS: Final = frozenset({"soc", "batState"})

STAT_FIELD_OWNERS: Final = MappingProxyType({
    "home_grid_import": ("HomeStat", "totalInGridEnergy"),
    "home_grid_export": ("HomeStat", "totalOutGridEnergy"),
    "home_energy": ("SysHomeStat", "totalHomeEgy"),
})

HOME_COMMANDS: Final = MappingProxyType({
    "write_wifi_info": CommandContract(3002, 2, 2),
    "fault_alarm_report": CommandContract(3042, 122, 122),
    "set_third_party_mqtt": CommandContract(3046, 113, 113),
    "query_third_party_mqtt": CommandContract(3047, 114, 114),
})

PORTABLE_COMMANDS: Final = MappingProxyType({
    "write_wifi_info": CommandContract(7, 2, 2),
    "setting_energy_saving": CommandContract(20, 4, 4),
    "set_peaks_troughs": CommandContract(42, 130, 130),
})


ALL_LIVE_DATA_SOURCES: Final = frozenset({"http", "cloud_mqtt", "local_mqtt", "ble"})

_ACC_CT_ENTITY_KEYS: Final = MappingProxyType({
    "ap": "apparent_power",
    "ap1": "phase_1_apparent_power",
    "ap2": "phase_2_apparent_power",
    "ap3": "phase_3_apparent_power",
    "curr": "current",
    "curr1": "phase_1_current",
    "curr2": "phase_2_current",
    "curr3": "phase_3_current",
    "fact": "power_factor",
    "fact1": "phase_1_power_factor",
    "fact2": "phase_2_power_factor",
    "fact3": "phase_3_power_factor",
    "freq": "frequency",
    "power": "power",
    "power1": "phase_1_power",
    "power2": "phase_2_power",
    "power3": "phase_3_power",
    "rep": "reactive_power",
    "rep1": "phase_1_reactive_power",
    "rep2": "phase_2_reactive_power",
    "rep3": "phase_3_reactive_power",
    "volt": "voltage",
    "volt1": "phase_1_voltage",
    "volt2": "phase_2_voltage",
    "volt3": "phase_3_voltage",
})

_CT_ENTITY_KEYS: Final = MappingProxyType({
    "aPhasePw": "phase_1_power",
    "anPhasePw": "phase_1_power",
    "bPhasePw": "phase_2_power",
    "bnPhasePw": "phase_2_power",
    "cPhasePw": "phase_3_power",
    "cnPhasePw": "phase_3_power",
    "tPhasePw": "power",
    "tnPhasePw": "power",
    "aPhaseEgy": "phase_1_lifetime_import_energy",
    "anPhaseEgy": "phase_1_lifetime_export_energy",
    "bPhaseEgy": "phase_2_lifetime_import_energy",
    "bnPhaseEgy": "phase_2_lifetime_export_energy",
    "cPhaseEgy": "phase_3_lifetime_import_energy",
    "cnPhaseEgy": "phase_3_lifetime_export_energy",
    "tPhaseEgy": "lifetime_import_energy",
    "tnPhaseEgy": "lifetime_export_energy",
    "funForm": "fun_form",
})

APP_FIELD_EXPOSURE_CONTRACTS: Final = (
    AppFieldExposureContract(
        "SystemBody",
        "batState",
        "entity",
        "sensor",
        "battery_state",
        "properties",
        ALL_LIVE_DATA_SOURCES,
    ),
    AppFieldExposureContract(
        "SystemBody",
        "maxFeedGrid",
        "entity",
        "number",
        "max_feed_grid",
        "properties",
        ALL_LIVE_DATA_SOURCES,
    ),
    AppFieldExposureContract(
        "HomeBody",
        "maxGridStdPw",
        "entity",
        "sensor",
        "max_grid_standard_power",
        "properties",
        ALL_LIVE_DATA_SOURCES,
    ),
    AppFieldExposureContract(
        "SystemBody",
        "offGridTime",
        "entity",
        "sensor",
        "off_grid_time",
        "properties",
        ALL_LIVE_DATA_SOURCES,
    ),
    AppFieldExposureContract(
        "SystemBody",
        "offGridDown",
        "entity",
        "switch",
        "off_grid_shutdown",
        "properties",
        ALL_LIVE_DATA_SOURCES,
    ),
    AppFieldExposureContract(
        "CtSub",
        "schePhase",
        "entity",
        "select",
        "ct_phase_select",
        "ct_meter",
        ALL_LIVE_DATA_SOURCES,
    ),
    AppFieldExposureContract(
        "HomeAlarmBody",
        "sysAlertCount",
        "entity",
        "alarm_sensor",
        "alarm_count",
        "alarm",
        ALL_LIVE_DATA_SOURCES,
    ),
    AppFieldExposureContract(
        "SubAlarm",
        "alertCount",
        "entity",
        "binary_sensor",
        "alarm",
        "subdevices",
        ALL_LIVE_DATA_SOURCES,
    ),
    AppFieldExposureContract(
        "HomeAlarmBody",
        "alarmId",
        "internal",
        None,
        None,
        "alarm",
        ALL_LIVE_DATA_SOURCES,
        "Retained in alarm attributes as the stable event identifier.",
    ),
    AppFieldExposureContract(
        "HomeAlarmBody",
        "subDevice",
        "internal",
        None,
        None,
        "alarm",
        ALL_LIVE_DATA_SOURCES,
        "Container for per-accessory alarm entities, not a scalar state.",
    ),
    AppFieldExposureContract(
        "BindSmartBean",
        "linkType",
        "internal",
        None,
        None,
        "subdevices",
        ALL_LIVE_DATA_SOURCES,
        "Topology metadata retained as a device attribute, not a user measurement.",
    ),
    AppFieldExposureContract(
        "CtSub",
        "wip",
        "internal",
        None,
        None,
        "ct_meter",
        ALL_LIVE_DATA_SOURCES,
        "Protocol working-state metadata without a stable user-facing unit.",
    ),
    *(
        AppFieldExposureContract(
            "AccCTBody",
            field,
            "entity",
            "smart_meter_sensor",
            entity_key,
            "ct_meter",
            ALL_LIVE_DATA_SOURCES,
        )
        for field, entity_key in _ACC_CT_ENTITY_KEYS.items()
    ),
    *(
        AppFieldExposureContract(
            "CtSub",
            field,
            "entity",
            "smart_meter_sensor",
            entity_key,
            "ct_meter",
            ALL_LIVE_DATA_SOURCES,
        )
        for field, entity_key in _CT_ENTITY_KEYS.items()
    ),
)
