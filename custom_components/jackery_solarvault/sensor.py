"""Sensor platform for the Jackery SolarVault integration.

This module is a thin entity layer. The data path is:

    Jackery API/MQTT --> coordinator (HTTP polling + MQTT push)
                     --> coordinator.data device payload
                     --> JackerySensor.native_value

Conventions used in the per-sensor doc strings:

* ``HTTP:`` lines name the documented endpoint(HTTP endpoints table).
* ``MQTT:`` lines name the telemetry message and the field (telemetry messages).
* period sensors use the documented app endpoint, with the
  documented same-endpoint month backfill for broken year payloads.

Field-to-source mapping (consolidated reference for live entities):

============================  ==========================================  ====================================================
Sensor key                    HTTP source / endpoint                       MQTT source (telemetry messageType / field)
============================  ==========================================  ====================================================
soc                           /v1/device/property -> ``soc``              UploadCombineData / DevicePropertyChange ``soc``
bat_soc                       /v1/device/property -> ``batSoc``           DevicePropertyChange ``batSoc``
cell_temperature              /v1/device/property -> ``cellTemp``/10      DevicePropertyChange ``cellTemp``
battery_charge_power          /v1/device/property -> ``batInPw``          UploadCombineData ``batInPw``
battery_discharge_power       /v1/device/property -> ``batOutPw``         UploadCombineData ``batOutPw``
pv_power_total                /v1/device/property -> ``pvPw``             UploadCombineData ``pvPw``
pv1..pv4_power                /v1/device/property -> ``pv1..pv4.pvPw``    DevicePropertyChange ``pv1..pv4``
grid_in_power                 /v1/device/property -> ``inOngridPw``       UploadCombineData ``gridInPw`` / ``inOngridPw``
grid_out_power                /v1/device/property -> ``outOngridPw``      UploadCombineData ``gridOutPw`` / ``outOngridPw``
eps_in_power / eps_out_power  /v1/device/property -> ``swEpsInPw/Out``    DevicePropertyChange ``swEpsInPw``/``swEpsOutPw``
stack_in_power / stack_out    /v1/device/property -> ``stackInPw/Out``    DevicePropertyChange ``stackInPw``/``stackOutPw``
smart_meter_phase_a/b/c       n/a (MQTT only)                              UploadSubDeviceIncrementalProperty ``aPhasePw`` etc.
============================  ==========================================  ====================================================

Field-to-source mapping (period / energy entities):

============================  ==========================================================  ==================
Sensor key suffix             HTTP endpoint (PROTOCOL.md §2)                          Chart series (PROTOCOL.md §8)
============================  ==========================================================  ==================
pv_energy_*                   /v1/device/stat/pv (device_pv_stat_*)                        ``y`` (totalSolarEnergy)
pv1..pv4_energy_*             /v1/device/stat/pv (device_pv_stat_*)                        ``y1..y4`` (pvNEgy)
battery_charge_energy_*       /v1/device/stat/battery (device_battery_stat_*)              ``y1`` (totalCharge)
battery_discharge_energy_*    /v1/device/stat/battery (device_battery_stat_*)              ``y2`` (totalDischarge)
device_ongrid_input_*         /v1/device/stat/onGrid (device_home_stat_*)                  ``y1`` (totalInGridEnergy)
device_ongrid_output_*        /v1/device/stat/onGrid (device_home_stat_*)                  ``y2`` (totalOutGridEnergy)
home_energy_*                 /v1/device/stat/sys/home/trends (home_trends_*)              ``y`` (totalHomeEgy)
============================  ==========================================================  ==================

Lifetime totals (``total_generation``, ``total_revenue``, ``total_carbon``)
prefer ``/v1/device/stat/systemStatistic``. Per generation/carbon are guarded against broken
month-only cloud totals. ``total_revenue`` stays the raw Jackery app savings
KPI, while the separate ``_savings_calculation`` metadata and optional detail
sensor expose the locally calculated savings from self-consumed AC energy.

Unique IDs follow strictly:
``<device_id>_<stable_key_suffix>`` for the main device and
``<device_id>_battery_pack_<index>_<stable_key_suffix>`` for battery packs.
The ``key`` attribute of each ``JackerySensorDescription`` is the
``<stable_key_suffix>``; translation keys, names and any localized text
must never affect ``unique_id``.
"""  # ruff: ignore[line-too-long]

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from functools import cached_property
import json
import logging
from math import isfinite, sqrt
from operator import itemgetter
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, cast
from weakref import WeakKeyDictionary, WeakSet

from homeassistant.components.recorder import history
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    DEGREE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.recorder import get_instance as get_recorder_instance
from homeassistant.util import dt as dt_util

from .client.mqtt_discovery import (
    JackeryMqttSensorPublisher,
    async_release_native_entity_ids,
    async_start_mqtt_discovery_cleanup,
    subscribe_connection_status,
)
from .const import (
    ACC_CT_BODY_MEASUREMENT_FIELDS,
    ACC_CT_L2_POWER_FIELDS,
    ACC_CT_L2_RAW_FIELDS,
    APP_CHART_BUCKET_BY_DATE_TYPE,
    APP_CHART_METRIC_KEY_BY_SECTION_PREFIX,
    APP_DEVICE_STAT_BATTERY_CHARGE,
    APP_DEVICE_STAT_BATTERY_DISCHARGE,
    APP_DEVICE_STAT_BATTERY_TO_AC,
    APP_DEVICE_STAT_BATTERY_TO_GRID,
    APP_DEVICE_STAT_EPS_INPUT,
    APP_DEVICE_STAT_EPS_OUTPUT,
    APP_DEVICE_STAT_ONGRID_INPUT,
    APP_DEVICE_STAT_ONGRID_OUTPUT,
    APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    APP_DEVICE_STAT_PV_ENERGY,
    APP_DEVICE_STAT_PV_TO_BATTERY,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_REQUEST_META,
    APP_SAVINGS_CALC_META,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_CT_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_PV_STAT,
    APP_SECTION_TODAY_ENERGY,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_TODAY_LOAD,
    APP_STAT_TOTAL_CHARGE,
    APP_STAT_TOTAL_DISCHARGE,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_REVENUE,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_UNIT,
    APP_TODAY_ENERGY_SOURCE_META,
    APP_TOTAL_GUARD_META,
    APP_YEAR_BACKFILL_META,
    CALCULATED_POWER_SENSOR_SUFFIXES,
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    CT_ATTRIBUTE_FIELDS,
    CT_THIRD_PV_POWER_FIELDS,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    DEFAULT_STORM_WARNING_MINUTES,
    DOMAIN,
    FIELD_BAT_IN_PW,
    FIELD_BAT_NUM,
    FIELD_BAT_OUT_PW,
    FIELD_BAT_SOC,
    FIELD_CELL_TEMP,
    FIELD_CHARGING_ENERGY,
    FIELD_COMM_MODE,
    FIELD_COMM_STATE,
    FIELD_CT_APPARENT_POWER,
    FIELD_CT_APPARENT_POWER1,
    FIELD_CT_APPARENT_POWER2,
    FIELD_CT_APPARENT_POWER3,
    FIELD_CT_POWER,
    FIELD_CT_POWER1,
    FIELD_CT_POWER2,
    FIELD_CT_POWER3,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_CURRENCY,
    FIELD_CURRENT_VERSION,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEV_ID,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_DISCHARGING_ENERGY,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_EC,
    FIELD_GRID_IN_PW,
    FIELD_GRID_OUT_PW,
    FIELD_GRID_STANDARD,
    FIELD_ID,
    FIELD_IDX,
    FIELD_IN_EGY,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_IN_ONGRID_PW,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IS_CLOUD,
    FIELD_IS_FIRMWARE_UPGRADE,
    FIELD_IT,
    FIELD_LINK_TYPE,
    FIELD_MAC,
    FIELD_MINS_INTERVAL,
    FIELD_MODEL,
    FIELD_MODEL_CODE,
    FIELD_MODEL_NAME,
    FIELD_NM,
    FIELD_OP,
    FIELD_OT,
    FIELD_OTHER_LOAD_PW,
    FIELD_OUT_EGY,
    FIELD_OUT_GRID_SIDE_PW,
    FIELD_OUT_ONGRID_PW,
    FIELD_OUT_PW,
    FIELD_PARAM,
    FIELD_PC,
    FIELD_PR,
    FIELD_PV_PW,
    FIELD_RB,
    FIELD_SCAN_NAME,
    FIELD_SINGLE_CURRENCY,
    FIELD_SINGLE_PRICE,
    FIELD_SN,
    FIELD_SOCKET_PRIORITY,
    FIELD_SPH,
    FIELD_SPH_PC,
    FIELD_STACK_IN_PW,
    FIELD_STACK_OUT_PW,
    FIELD_STORM,
    FIELD_SUB_TYPE,
    FIELD_SW,
    FIELD_SWITCH_STATE,
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_SN,
    FIELD_SYS_ALERT_COUNT,
    FIELD_SYS_SWITCH,
    FIELD_SYS_TOTAL_PV_PW,
    FIELD_TARGET_MODULE_VERSION,
    FIELD_TARGET_VERSION,
    FIELD_TODAY_ENERGY,
    FIELD_TOTAL_ENERGY,
    FIELD_TYPE_NAME,
    FIELD_UPDATE_CONTENT,
    FIELD_UPDATE_STATUS,
    FIELD_UPGRADE_TYPE,
    FIELD_VERSION,
    FIELD_WNAME,
    FIELD_WPC,
    FIELD_WPS,
    JACKERY_LIVE_ENERGY_UNITS_PER_KWH,
    MANUFACTURER,
    PAYLOAD_AIEMS_ENERGY_PREDICTION,
    PAYLOAD_AIEMS_MODE_STATUS,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_BATTERY_TRENDS,
    PAYLOAD_BLE_OTA_EVENTS,
    PAYLOAD_BLE_OTA_VERSIONS,
    PAYLOAD_CIRCUIT_PROPERTY,
    PAYLOAD_CT_METER,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_ALERT,
    PAYLOAD_DEVICE_META,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_DISCOVERY,
    PAYLOAD_ELECTRICITY_STRATEGY,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_LOCAL_DAILY_ENERGY,
    PAYLOAD_METER_HEADS,
    PAYLOAD_OBSERVED_DAY_STATISTICS,
    PAYLOAD_PORTABLE_CT_STATISTICS,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_PV_TRENDS,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_STATISTIC,
    PAYLOAD_SUBDEVICES,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_VERIFIED_DAY_STATISTICS,
    PAYLOAD_WEATHER_PLAN,
    PORTABLE_RESERVE_MODELS,
    SAVINGS_DETAIL_SENSOR_SUFFIXES,
    SMART_METER_DERIVED_SENSOR_SUFFIXES,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_BREAKER,
    SUBDEVICE_DEV_TYPE_METER,
    SUBDEVICE_DEV_TYPE_METER_HEAD,
    SUBDEVICE_DEV_TYPE_SMOKE,
    SUBDEVICE_DEV_TYPE_SOCKET,
    SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY,
    SUBDEVICE_DEV_TYPE_WATER_LEAK,
    TASK_PLAN_BODY,
    TASK_PLAN_TASKS,
    UNRECORDED_ATTRS_CLOUD_MQTT,
    UNRECORDED_ATTRS_HTTP_API,
    UNRECORDED_ATTRS_LOCAL_MQTT,
)
from .coordinator import (
    battery_pack_serial,
    smart_meter_accessories,
    sorted_battery_pack_payloads,
    subdevice_accessories,
)
from .descriptions import (
    BATTERY_PACK_SENSOR_DESCRIPTIONS,
    BOX_SENSOR_DESCRIPTIONS,
    BREAKER_SENSOR_DESCRIPTIONS,
    DYNAMIC_PRICE_SENSOR_DESCRIPTIONS,
    METER_HEAD_SENSOR_DESCRIPTIONS,
    PORTABLE_SENSOR_DESCRIPTIONS,
    SAVINGS_DETAIL_SENSOR_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    SMART_METER_SENSOR_DESCRIPTIONS,
    SMART_MODE_SENSOR_DESCRIPTIONS,
    SMART_PLUG_SENSOR_DESCRIPTIONS,
    SMART_SCHEDULE_SENSOR_DESCRIPTIONS,
    STAT_DESCRIPTIONS,
    SUBDEVICE_ALARM_SENSOR_DESCRIPTIONS,
    TOU_PLAN_SENSOR_DESCRIPTIONS,
)
from .descriptions.sensor import (
    AIEMS_PREDICTION_FIELDS,
    AIEMS_PREDICTION_SENSOR_DESCRIPTIONS,
    AIEMS_STATUS_FIELDS,
    AIEMS_STATUS_SENSOR_DESCRIPTIONS,
    PORTABLE_CT_STATISTICS_DESCRIPTIONS,
    _portable_wifi_names,
)
from .entity import ALL_LIVE_DATA_SOURCES, JackeryEntity, payload_properties_for_sources
from .firmware import firmware_upgrade_available
from .ingest import (
    _PV_CHANNEL_ENERGY_KEYS,
    is_pv_generation_metric,
    normalize_pv_period_energy,
)
from .util import (
    accessory_ip_address,
    app_energy_unit_scale,
    append_unique_entity,
    calculated_smart_meter_power,
    circuit_id,
    complete_year_series_total,
    config_entry_bool_option,
    coordinator_entity_signature,
    current_day_http_source,
    current_day_http_values,
    dated_daily_chart_values,
    day_power_energy_points,
    directional_power_value,
    effective_trend_series_values,
    first_nonblank_text,
    first_power_value,
    guard_total_increasing_jitter,
    is_day_period_payload,
    is_device_year_period_section,
    jackery_corrected_home_consumption_power,
    jackery_grid_net_power,
    jackery_grid_side_input_power,
    jackery_grid_side_output_power,
    jackery_inverter_ac_input_power,
    jackery_inverter_ac_output_power,
    meter_head_serial,
    nonblank_text,
    normalize_mac_address,
    observed_period_energy_total,
    period_data_offset,
    pv_channel_scalar_is_lifetime_offset,
    rebuild_period_energy_total,
    redacted_json_safe_payload,
    safe_float,
    safe_int,
    signed_phase_power_values,
    smart_meter_identity,
    smart_meter_net_power,
    smart_plug_serial,
    sorted_circuits,
    sorted_meter_heads,
    sorted_smart_plugs,
    sorted_sub_devices,
    stable_subdevice_key,
    sub_device_serial,
    subdevice_branding,
    task_plan_value,
    trend_series_has_value,
    trend_series_key,
    trend_series_total,
)

# Source-level compatibility keys retained after description-module migration.
METER_HEAD_CHARGING_ENERGY_KEY: Final = "meter_head_charging_energy"
METER_HEAD_DISCHARGING_ENERGY_KEY: Final = "meter_head_discharging_energy"

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import tzinfo

    from homeassistant.const import StateType
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import ChildDeviceInfo
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import JackeryConfigEntry
    from .coordinator import JackerySolarVaultCoordinator
    from .descriptions import (
        JackeryBatteryPackSensorDescription,
        JackeryBreakerSensorDescription,
        JackeryMeterHeadSensorDescription,
        JackerySavingsDetailSensorDescription,
        JackerySensorDescription,
        JackerySmartMeterSensorDescription,
        JackerySmartPlugSensorDescription,
        JackeryStatSensorDescription,
        JackerySubdeviceAlarmSensorDescription,
    )
    from .util import HomeConsumptionPower

# Coordinator-backed read-only platform: entities never perform their own
# refresh I/O, so disable per-entity parallel update scheduling.
PARALLEL_UPDATES = 0


_LOGGER = logging.getLogger(__name__)

type _IndexedEntityIdentity = tuple[int, str, str]
type _BatteryPackEntityIdentity = tuple[int, str | None, str]


# Max number of per-bucket period values exposed as an attribute (days in a
# month). Larger series are chart-curve arrays, not month buckets.
_MAX_PERIOD_VALUES: Final = 31


# ---------------------------------------------------------------------------
# Value extraction helpers
# ---------------------------------------------------------------------------
LOCAL_DAILY_METRIC_BY_SENSOR_KEY: dict[str, str] = {
    "battery_to_ac_day_energy": APP_DEVICE_STAT_BATTERY_TO_AC,
    "battery_to_ac_week_energy": APP_DEVICE_STAT_BATTERY_TO_AC,
    "battery_to_ac_month_energy": APP_DEVICE_STAT_BATTERY_TO_AC,
    "battery_to_ac_year_energy": APP_DEVICE_STAT_BATTERY_TO_AC,
    "today_load": APP_DEVICE_STAT_ONGRID_OUTPUT,
    "today_feed_in_energy": APP_DEVICE_STAT_PV_ENERGY,
    "today_grid_import_energy": APP_DEVICE_STAT_ONGRID_INPUT,
    "today_battery_energy": APP_DEVICE_STAT_BATTERY_DISCHARGE,
    "device_today_pv_energy": APP_DEVICE_STAT_PV_ENERGY,
    "pv_week_energy": APP_DEVICE_STAT_PV_ENERGY,
    "pv_month_energy": APP_DEVICE_STAT_PV_ENERGY,
    "pv_year_energy": APP_DEVICE_STAT_PV_ENERGY,
    "device_pv1_day_energy": APP_STAT_PV1_ENERGY,
    "device_pv1_week_energy": APP_STAT_PV1_ENERGY,
    "device_pv1_month_energy": APP_STAT_PV1_ENERGY,
    "device_pv1_year_energy": APP_STAT_PV1_ENERGY,
    "device_pv2_day_energy": APP_STAT_PV2_ENERGY,
    "device_pv2_week_energy": APP_STAT_PV2_ENERGY,
    "device_pv2_month_energy": APP_STAT_PV2_ENERGY,
    "device_pv2_year_energy": APP_STAT_PV2_ENERGY,
    "device_pv3_day_energy": APP_STAT_PV3_ENERGY,
    "device_pv3_week_energy": APP_STAT_PV3_ENERGY,
    "device_pv3_month_energy": APP_STAT_PV3_ENERGY,
    "device_pv3_year_energy": APP_STAT_PV3_ENERGY,
    "device_pv4_day_energy": APP_STAT_PV4_ENERGY,
    "device_pv4_week_energy": APP_STAT_PV4_ENERGY,
    "device_pv4_month_energy": APP_STAT_PV4_ENERGY,
    "device_pv4_year_energy": APP_STAT_PV4_ENERGY,
    "device_today_battery_charge": APP_DEVICE_STAT_BATTERY_CHARGE,
    "battery_charge_week_energy": APP_DEVICE_STAT_BATTERY_CHARGE,
    "battery_charge_month_energy": APP_DEVICE_STAT_BATTERY_CHARGE,
    "battery_charge_year_energy": APP_DEVICE_STAT_BATTERY_CHARGE,
    "device_today_battery_discharge": APP_DEVICE_STAT_BATTERY_DISCHARGE,
    "battery_discharge_week_energy": APP_DEVICE_STAT_BATTERY_DISCHARGE,
    "battery_discharge_month_energy": APP_DEVICE_STAT_BATTERY_DISCHARGE,
    "battery_discharge_year_energy": APP_DEVICE_STAT_BATTERY_DISCHARGE,
    "device_today_ongrid_input": APP_DEVICE_STAT_ONGRID_INPUT,
    "device_ongrid_input_week_energy": APP_DEVICE_STAT_ONGRID_INPUT,
    "device_ongrid_input_month_energy": APP_DEVICE_STAT_ONGRID_INPUT,
    "device_ongrid_input_year_energy": APP_DEVICE_STAT_ONGRID_INPUT,
    "device_today_ongrid_output": APP_DEVICE_STAT_ONGRID_OUTPUT,
    "device_ongrid_output_week_energy": APP_DEVICE_STAT_ONGRID_OUTPUT,
    "device_ongrid_output_month_energy": APP_DEVICE_STAT_ONGRID_OUTPUT,
    "device_ongrid_output_year_energy": APP_DEVICE_STAT_ONGRID_OUTPUT,
    "device_today_ongrid_to_battery": APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    "grid_to_battery_week_energy": APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    "grid_to_battery_month_energy": APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    "grid_to_battery_year_energy": APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    "device_today_pv_to_battery": APP_DEVICE_STAT_PV_TO_BATTERY,
    "device_today_battery_to_ongrid": APP_DEVICE_STAT_BATTERY_TO_GRID,
    "battery_to_grid_week_energy": APP_DEVICE_STAT_BATTERY_TO_GRID,
    "battery_to_grid_month_energy": APP_DEVICE_STAT_BATTERY_TO_GRID,
    "battery_to_grid_year_energy": APP_DEVICE_STAT_BATTERY_TO_GRID,
    "eps_input_day_energy": APP_DEVICE_STAT_EPS_INPUT,
    "eps_input_week_energy": APP_DEVICE_STAT_EPS_INPUT,
    "eps_input_month_energy": APP_DEVICE_STAT_EPS_INPUT,
    "eps_input_year_energy": APP_DEVICE_STAT_EPS_INPUT,
    "eps_output_day_energy": APP_DEVICE_STAT_EPS_OUTPUT,
    "eps_output_week_energy": APP_DEVICE_STAT_EPS_OUTPUT,
    "eps_output_month_energy": APP_DEVICE_STAT_EPS_OUTPUT,
    "eps_output_year_energy": APP_DEVICE_STAT_EPS_OUTPUT,
    "ct_input_day_energy": FIELD_CT_TOTAL_PHASE_ENERGY,
    "ct_input_week_energy": FIELD_CT_TOTAL_PHASE_ENERGY,
    "ct_input_month_energy": FIELD_CT_TOTAL_PHASE_ENERGY,
    "ct_input_year_energy": FIELD_CT_TOTAL_PHASE_ENERGY,
    "ct_output_day_energy": FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    "ct_output_week_energy": FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    "ct_output_month_energy": FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    "ct_output_year_energy": FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
}


def _path(
    props: dict[str, Any],
    *keys: str,
) -> str | float | int | dict[str, Any] | list[Any] | None:
    """Walk a nested path; return None on missing intermediate keys."""
    node: object = props
    for k in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return cast("str | float | int | dict[str, Any] | list[Any] | None", node)


def _sensor_state_value(value: object) -> StateType:
    """Return only scalar values accepted by Home Assistant's state machine."""
    return value if value is None or isinstance(value, (str, int, float)) else None


def _float_payload_value(value: object) -> float | None:
    """Parse one scalar payload value as a finite float candidate."""
    return safe_float(value) if isinstance(value, (int, float, str)) else None


def _div(divisor: float) -> Callable[[object], float | None]:
    """Implementation details.

    Create a transformer that divides an input value by a given divisor and rounds
    the result to 2 decimal places.

    Parameters:
        divisor (float): Value to divide the input by.

    Returns:
        A function that accepts a payload value and returns the
        quotient rounded to 2 decimals when the value can be converted to float, or
        `None` when conversion fails.
    """

    def _f(
        value: object,
    ) -> float | None:
        parsed = _float_payload_value(value)
        return round(parsed / divisor, 2) if parsed is not None else None

    return _f


def _migrated_stat_description(
    *,
    key: str,
    translation_key: str,
    state_class: SensorStateClass,
) -> JackeryStatSensorDescription:
    """Return a canonical migrated statistic description after validation."""
    description = next(item for item in STAT_DESCRIPTIONS if item.key == key)
    if (
        description.translation_key != translation_key
        or description.state_class != state_class
        or description.device_class is not None
    ):
        msg = f"Migrated statistic description contract changed: {key}"
        raise RuntimeError(msg)
    return description


type _BatteryPackDescriptionContract = tuple[
    str,
    str,
    SensorStateClass,
    bool,
]


def _migrated_battery_pack_description(
    *,
    key: str,
    contract: _BatteryPackDescriptionContract,
    transform: Callable[[object], float | None],
) -> JackeryBatteryPackSensorDescription:
    """Return a canonical migrated battery-pack description after validation."""
    translation_key, field, state_class, enabled_default = contract
    description = next(
        item for item in BATTERY_PACK_SENSOR_DESCRIPTIONS if item.key == key
    )
    if (
        description.translation_key != translation_key
        or description.field != field
        or description.state_class != state_class
        or description.entity_registry_enabled_default != enabled_default
        or description.transform(JACKERY_LIVE_ENERGY_UNITS_PER_KWH)
        != transform(JACKERY_LIVE_ENERGY_UNITS_PER_KWH)
    ):
        msg = f"Migrated battery-pack description contract changed: {key}"
        raise RuntimeError(msg)
    return description


MIGRATED_STAT_SENSOR_DESCRIPTIONS = (
    _migrated_stat_description(
        key="total_revenue",
        translation_key="total_revenue",
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
)
MIGRATED_BATTERY_PACK_SENSOR_DESCRIPTIONS = (
    _migrated_battery_pack_description(
        key="lifetime_charge_energy",
        contract=(
            "battery_pack_lifetime_charge_energy",
            FIELD_IN_EGY,
            SensorStateClass.TOTAL_INCREASING,
            False,
        ),
        transform=_div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH),
    ),
    _migrated_battery_pack_description(
        key="lifetime_discharge_energy",
        contract=(
            "battery_pack_lifetime_discharge_energy",
            FIELD_OUT_EGY,
            SensorStateClass.TOTAL_INCREASING,
            False,
        ),
        transform=_div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH),
    ),
)


_RESTORABLE_LIFETIME_STAT_SENSOR_KEYS: Final = frozenset({
    "battery_charge_energy",
    "battery_discharge_energy",
    "main_battery_charge_energy",
    "main_battery_discharge_energy",
    "grid_import_energy",
    "grid_export_energy",
})


def _pack_restore_identity_matches(entity: RestoreSensor, state: Any) -> bool:  # ruff: ignore[any-type]
    """Accept a saved pack state only when it belongs to this physical pack."""
    serial = getattr(entity, "_pack_sn", None)
    attributes = getattr(state, "attributes", None)
    if (
        not isinstance(serial, str)
        or not serial.strip()
        or not isinstance(attributes, Mapping)
    ):
        return False
    saved_serial = attributes.get("pack_serial_number")
    if saved_serial is not None:
        return (
            isinstance(saved_serial, str)
            and saved_serial.strip().casefold() == serial.strip().casefold()
        )
    # Older states did not store the serial separately. Accept them only if
    # their persisted source ID and the entity key both encode this serial.
    source_id = attributes.get("source_unique_id")
    unique_id = getattr(entity, "unique_id", None)
    return (
        isinstance(source_id, str)
        and source_id == unique_id
        and getattr(entity, "_pack_key", None)
        == stable_subdevice_key(
            "battery_pack", serial, getattr(entity, "_pack_index", 0)
        )
    )


async def _async_restored_lifetime_energy_value(  # ruff: ignore[too-many-return-statements, too-many-branches]
    entity: RestoreSensor,
    expected_unit: str | None,
) -> float | None:
    """Return a validated native lifetime-energy value from HA storage."""
    pack_entity = hasattr(entity, "_pack_sn")
    restore_owner = None
    restore_leaf = None
    restored_state = None
    if pack_entity:
        if not _pack_restore_identity_matches(
            entity, await entity.async_get_last_state()
        ):
            return None
    else:
        _invalidate_own_lifetime_parent_cache(entity)
        restore_owner = getattr(entity, "_own_lifetime_parent_token", None)
        restore_leaf = getattr(entity, "_own_lifetime_leaf_serial", None)
        restored_state = await entity.async_get_last_state()
        if not _energy_restore_identity_matches(entity, restored_state):
            return None
    stored = await entity.async_get_last_sensor_data()
    if not pack_entity:
        _invalidate_own_lifetime_parent_cache(entity)
        if (
            not _same_energy_owner_tokens(
                restore_owner, getattr(entity, "_own_lifetime_parent_token", None)
            )
            or restore_leaf != getattr(entity, "_own_lifetime_leaf_serial", None)
            or not _energy_restore_identity_matches(entity, restored_state)
        ):
            return None
    if stored is None or isinstance(stored.native_value, bool):
        return None
    native_value = stored.native_value
    if not isinstance(native_value, (int, float, str)):
        return None
    try:
        value = float(native_value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isfinite(value) or value < 0:
        return None

    stored_unit = stored.native_unit_of_measurement
    if stored_unit in {None, expected_unit}:
        scale = 1.0
    elif (
        stored_unit == UnitOfEnergy.WATT_HOUR
        and expected_unit == UnitOfEnergy.KILO_WATT_HOUR
    ):
        scale = 0.001
    elif (
        stored_unit == UnitOfEnergy.KILO_WATT_HOUR
        and expected_unit == UnitOfEnergy.WATT_HOUR
    ):
        scale = 1000.0
    else:
        return None
    return value * scale


_PACK_ON_CHANGE_FIELDS: Final = frozenset({FIELD_IN_PW, FIELD_OUT_PW, FIELD_CELL_TEMP})


async def _async_recorded_pack_temperature(
    entity: RestoreSensor,
    expected_unit: str | None,
) -> float | None:
    """Read the same pack entity's last finite temperature after storage loss.

    Returns:
        float | None: A compatible recorded measurement, never a head value.
    """
    hass = getattr(entity, "hass", None)
    entity_id = getattr(entity, "entity_id", None)
    description = getattr(entity, "entity_description", None)
    if (
        hass is None
        or not entity_id
        or expected_unit is None
        or getattr(description, "field", None) != FIELD_CELL_TEMP
        or "recorder" not in hass.data
    ):
        return None
    recorder = get_recorder_instance(hass)

    def _read_temperature() -> float | None:
        # The old 32-state cutoff can lose a sparse cmd107 cellTemp behind
        # many newer unknown/unavailable states. HA also warns that its
        # get_last_state_changes(count > 1) query can be unstable.
        # Read all Recorder-retained state changes for this *one* entity,
        # newest-first. Physical pack SN + temperature unit are still required.
        state_changes = history.state_changes_during_period(
            hass,
            datetime(1970, 1, 1, tzinfo=UTC),
            entity_id=entity_id,
            descending=True,
            include_start_time_state=False,
        )
        states = (
            state_changes.get(entity_id.lower()) or state_changes.get(entity_id) or []
        )
        for state in states:
            if not _pack_restore_identity_matches(entity, state):
                continue
            if state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE, "", None}:
                continue
            try:
                value = float(state.state)
            except (ValueError, TypeError, OverflowError):
                continue
            if not isfinite(value):
                continue
            if state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) != expected_unit:
                continue
            return value
        return None

    try:
        return await recorder.async_add_executor_job(_read_temperature)
    except Exception as err:  # ruff: ignore[blind-except]
        _LOGGER.warning(
            "Could not read recorded temperature for %s: %s", entity_id, err
        )
        return None


async def _async_restored_pack_measurement_value(
    entity: RestoreSensor,
    expected_unit: str | None,
) -> float | None:
    """Return the last stored pack measurement from HA storage.

    Packs stop repeating ``inPw``/``outPw`` once unchanged (cloud 3014
    frames after 2026-09-25 08:09:23 carried only batSoc/commState).
    Pack-specific ``cellTemp`` is also sparse; restored values are labelled
    until an actual BatteryPackSub measurement replaces them.
    """
    state = await entity.async_get_last_state()
    stored = (
        await entity.async_get_last_sensor_data()
        if _pack_restore_identity_matches(entity, state)
        else None
    )
    if (
        stored is not None
        and not isinstance(stored.native_value, bool)
        and stored.native_unit_of_measurement in {None, expected_unit}
        and isinstance(stored.native_value, (int, float, str))
    ):
        try:
            value = float(stored.native_value)
        except (TypeError, ValueError, OverflowError):
            pass
        else:
            if isfinite(value):
                return value
    return await _async_recorded_pack_temperature(entity, expected_unit)


def _signed_diff(merged_value: object, http_value: object) -> int | None:
    """The ``merged - http`` as int when both inputs parse, else None.

    Used to surface MQTT-vs-HTTP drift in net-power sensor attributes so
    users (and the data-quality repair) can see when the two transports
    disagree on the same field.
    """
    merged_int = safe_int(merged_value)
    http_int = safe_int(http_value)
    if merged_int is None or http_int is None:
        return None
    return merged_int - http_int


def _system_meta_scalar_value(value: object) -> str | None:
    """Normalize a scalar system-metadata value for a diagnostic sensor."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    normalized = str(value).strip()
    return normalized or None


def _flag_int(value: object) -> int | None:
    """Convert a flag-like payload value to an integer when possible."""
    if isinstance(value, bool):
        return int(value)
    return safe_int(value)


def _temp_unit_label(value: object) -> str | None:
    unit = safe_int(value)
    if unit is None:
        return None
    return "F" if unit == 1 else "C"


def _storm_minutes_from_plan(plan: dict[str, Any]) -> int | None:
    """Extract storm lead-time minutes from weather-plan payload variants."""
    for key in (FIELD_WPC, FIELD_MINS_INTERVAL):
        val = safe_int(plan.get(key))
        if val is not None and val > 0:
            return val
    storm = plan.get(FIELD_STORM)
    if isinstance(storm, list):
        for item in storm:
            if not isinstance(item, dict):
                continue
            for key in (FIELD_WPC, FIELD_MINS_INTERVAL):
                val = safe_int(item.get(key))
                if val is not None and val > 0:
                    return val
    return None


def _storm_minutes_fallback(
    properties: dict[str, Any],
    weather_plan: dict[str, Any],
    task_plan: dict[str, Any],
) -> int | None:
    """Avoid unknown storm lead-time when the app only reports the switch state."""
    raw = properties.get(FIELD_WPS)
    if raw is None:
        raw = weather_plan.get(FIELD_WPS)
    if raw is None:
        raw = task_plan_value(task_plan, FIELD_WPS)
    if raw is not None:
        val = safe_int(raw)
        if val is None:
            return None
        return DEFAULT_STORM_WARNING_MINUTES if val else 0
    storm = weather_plan.get(FIELD_STORM)
    if isinstance(storm, list):
        return DEFAULT_STORM_WARNING_MINUTES if storm else 0
    return None


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------
# Shown for network address fields the device omits while that interface is
# down. ``eip``/``emac`` are real HomeBody fields (smali: HomeBody.eip/emac)
# that the device only sends while Ethernet is up (ethPort != 0); on a
# WLAN-only device they are simply absent. Render an explicit "not connected"
# dash instead of Unknown (owner directive 2026-07-05: "0 oder -").
_NETWORK_DISCONNECTED_PLACEHOLDER: Final = "—"
_MIN_ZERO_CORROBORATION_SOURCES: Final = 2
_PRICE_MODE_SINGLE: Final = 2


def _with_app_fields(
    getter: Callable[[dict[str, Any]], Any],
    *fields: str,
) -> Callable[[dict[str, Any]], Any]:
    """Attach App-field provenance to a compatibility getter."""
    metadata_getter = cast("Any", getter)
    metadata_getter.app_fields = tuple(fields)
    metadata_getter.layer5_data_source = True
    return getter


def _payload_section_field(
    section: str,
    key: str,
) -> Callable[[dict[str, Any]], StateType]:
    """The a fallback getter for a top-level coordinator payload bucket."""

    def _f(payload: dict[str, Any]) -> StateType:
        source = payload.get(section)
        if isinstance(source, dict):
            return _sensor_state_value(source.get(key))
        return None

    return _f


def _payload_section_first_list_count(
    section: str,
    *keys: str,
) -> Callable[[dict[str, Any]], int | None]:
    """The the length of the first list found in a payload bucket."""

    def _f(payload: dict[str, Any]) -> int | None:
        source = payload.get(section)
        if not isinstance(source, dict):
            return None
        for key in keys:
            value = source.get(key)
            if isinstance(value, list):
                return len(value)
        return None

    return _f


def _prop_any(*keys: str) -> Callable[[dict[str, Any]], Any]:
    def _getter(props: dict[str, Any]) -> object:
        for key in keys:
            if key in props and props.get(key) is not None:
                return props.get(key)
        return None

    return _with_app_fields(_getter, *keys)


def _prop_power_any(*keys: str) -> Callable[[dict[str, Any]], Any]:
    def _getter(props: dict[str, Any]) -> object:
        first_zero: float | None = None
        for key in keys:
            if key not in props or props.get(key) is None:
                continue
            value = safe_float(props.get(key))
            if value is None:
                continue
            if value != 0:
                return value
            if first_zero is None:
                first_zero = value
        return first_zero

    return _with_app_fields(_getter, *keys)


def _payload_http_prop(key: str) -> Callable[[dict[str, Any]], Any]:
    """Read the latest HTTP property value before MQTT overlay values."""

    def _getter(payload: dict[str, Any]) -> object:
        http_props = payload.get(PAYLOAD_HTTP_PROPERTIES) or {}
        if not isinstance(http_props, dict):
            return None
        return http_props.get(key)

    return _getter


def _nested(*keys: str) -> Callable[[dict[str, Any]], Any]:
    return lambda props: _path(props, *keys)


def _pv_channel_power(channel_key: str) -> Callable[[dict[str, Any]], Any]:
    """Read per-channel PV power and default to 0W when channel exists."""

    def _getter(props: dict[str, Any]) -> object:
        channel = props.get(channel_key)
        if not isinstance(channel, dict):
            return None
        return channel.get(FIELD_PV_PW)

    return _getter


# ---------------------------------------------------------------------------
# Statistic sensors — sourced from _statistic section of payload
# ---------------------------------------------------------------------------
StatResetPeriod = Literal["day", "week", "month", "year"]


def _period_start_at(reset_period: StatResetPeriod, now: datetime) -> datetime:
    """Return the start of one statistic period containing the supplied time."""
    if reset_period == DATE_TYPE_DAY:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if reset_period == DATE_TYPE_WEEK:
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if reset_period == DATE_TYPE_MONTH:
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _period_start(
    reset_period: StatResetPeriod, timezone: tzinfo | None = None
) -> datetime:
    """The the timezone-aware start datetime for the current statistic period.

    Computes the local period boundary for the given `reset_period`. Supported
    periods: day, week, month, year. Week boundaries start on Monday. The
    returned datetime is localized to `timezone` (or the Home Assistant local
    timezone when `timezone` is None) and has time components set to midnight
    at the period start.

    Parameters:
        reset_period (StatResetPeriod): Period identifier (e.g., `DATE_TYPE_DAY`,
            `DATE_TYPE_WEEK`, `DATE_TYPE_MONTH`, or year default).
        timezone (Any | None): Timezone to use for computing the boundary; when
            None the Home Assistant local timezone is used.

    Returns:
        datetime: Timezone-aware datetime at 00:00:00 representing the start of
        the current period.
    """
    return _period_start_at(reset_period, dt_util.now(timezone))


def _period_from_stat_description(
    description: JackeryStatSensorDescription,
) -> StatResetPeriod | None:
    """Infer reset period for app period stats when older descriptions omit it."""
    if description.reset_period is not None:
        return description.reset_period
    key = description.key
    if key.endswith("_week_energy"):
        return DATE_TYPE_WEEK
    if key.endswith("_month_energy"):
        return DATE_TYPE_MONTH
    if key.endswith("_year_energy"):
        return DATE_TYPE_YEAR
    return None


def _external_chart_metric_key(section: str, stat_key: str) -> str | None:
    """The the external statistic metric key from const.py mapping."""
    for section_prefix, mapping in APP_CHART_METRIC_KEY_BY_SECTION_PREFIX.items():
        if section.startswith(section_prefix):
            return mapping.get(stat_key)
    return None


def _external_chart_bucket_key(section: str) -> str | None:
    """The the HA external-statistics bucket for an app period section."""
    for date_type, bucket in APP_CHART_BUCKET_BY_DATE_TYPE.items():
        if section.endswith(f"_{date_type}"):
            return bucket
    return None


def _trend_series_key(section: str, stat_key: str) -> str | None:
    """Compatibility wrapper around util.trend_series_key."""
    return trend_series_key(section, stat_key)


def _trend_series_sum(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> float | None:
    """Compatibility wrapper around util.trend_series_total."""
    return trend_series_total(source, section, stat_key)


def _stat_section_has_values(
    payload: dict[str, Any],
    section: str,
    stat_key: str,
) -> bool:
    """The True when a fetched app statistic section contains real values."""
    source = payload.get(section)
    if not isinstance(source, dict):
        return False
    if section.startswith(APP_SECTION_CT_STAT):
        return trend_series_has_value(source, section, stat_key)
    return any(key != APP_REQUEST_META for key in source)


def _day_section_prefix(section: str) -> str | None:
    """The the prefix for a ``*_day`` app-period section."""
    suffix = f"_{DATE_TYPE_DAY}"
    if not section.endswith(suffix):
        return None
    return section[: -len(suffix)]


def _day_period_sibling_has_value(
    payload: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    reset_period: StatResetPeriod | None,
) -> bool:
    """The True when week/month/year charts prove a day sensor is supported."""
    if reset_period != DATE_TYPE_DAY:
        return False
    prefix = _day_section_prefix(section)
    if prefix is None:
        return False
    for date_type in (DATE_TYPE_MONTH, DATE_TYPE_WEEK, DATE_TYPE_YEAR):
        sibling_section = f"{prefix}_{date_type}"
        sibling_source = payload.get(sibling_section)
        if isinstance(sibling_source, dict) and trend_series_has_value(
            sibling_source,
            sibling_section,
            stat_key,
        ):
            return True
    return False


def _sensor_description_has_value(  # ruff: ignore[too-many-return-statements]
    payload: dict[str, Any],
    description: JackerySensorDescription,
) -> bool:
    """The True when a property sensor can produce a value from payload.

    Gating happens on ``app_fields`` — the app-side payload keys a description
    reads. A description without that metadata cannot be gated here; it is
    registered and ``value_fn`` decides at runtime.
    """
    app_fields = description.app_fields
    if description.key == "aiems_mode_status":
        status = payload.get(PAYLOAD_AIEMS_MODE_STATUS)
        return isinstance(status, dict) and bool(AIEMS_STATUS_FIELDS & status.keys())
    if description.key == "aiems_energy_prediction":
        prediction = payload.get(PAYLOAD_AIEMS_ENERGY_PREDICTION)
        return isinstance(prediction, dict) and bool(prediction.get("timestamps"))
    if description.key in {
        "box_strategy_plans",
        "box_current_strategy",
        "portable_strategy_plans",
        "portable_current_strategy",
    }:
        strategy = payload.get(PAYLOAD_ELECTRICITY_STRATEGY)
        field = "cds" if description.key.endswith("strategy_plans") else "cep"
        if not isinstance(strategy, dict) or field not in strategy:
            return False
        value = strategy[field]
        return (
            isinstance(value, list)
            if field == "cds"
            else (value is None or isinstance(value, dict))
        )
    if description.key == "box_wifi_scan":
        return _portable_wifi_names(payload) is not None
    if not app_fields:
        return True
    if description.key.startswith("box_fault_"):
        properties = payload_properties_for_sources(payload, description.data_sources)
        fault = properties.get("fz")
        field = description.family_fields[0].split(".")[-1]
        return isinstance(fault, dict) and fault.get(field) is not None
    if description.key == "portable_wifi_scan":
        return _portable_wifi_names(payload) is not None
    if description.key in {
        "portable_bs_raw",
        "portable_ground_accessory_serial",
        "portable_idc_raw",
        "box_de_raw",
        "box_dg_raw",
        "box_dh_raw",
        "box_ds_raw",
    }:
        properties = payload_properties_for_sources(payload, description.data_sources)
        return any(properties.get(field) is not None for field in app_fields)
    sections: list[dict[str, Any]] = [
        payload_properties_for_sources(payload, description.data_sources),
    ]
    sections.extend(
        section for section in payload.values() if isinstance(section, dict)
    )
    return any(
        section.get(field) is not None for section in sections for field in app_fields
    )


def _battery_pack_description_value(
    pack: dict[str, Any],
    description: JackeryBatteryPackSensorDescription,
) -> StateType:
    """Return one app-backed battery-pack value from the current pack payload."""
    field = description.field
    raw = (
        firmware_upgrade_available(pack)
        if field == FIELD_IS_FIRMWARE_UPGRADE
        else pack.get(field)
    )
    if raw is None:
        alias = {
            FIELD_BAT_SOC: FIELD_RB,
            FIELD_IN_PW: FIELD_IP,
            FIELD_OUT_PW: FIELD_OP,
        }.get(field)
        if alias is not None:
            raw = pack.get(alias)
    if raw is None and field == FIELD_VERSION:
        raw = pack.get(FIELD_CURRENT_VERSION)
    if raw is None and field == FIELD_DEVICE_SN:
        raw = pack.get(FIELD_DEV_SN) or pack.get(FIELD_SN)
    if (
        raw is None
        and field == FIELD_COMM_STATE
        and any(
            pack.get(key) is not None
            for key in (
                FIELD_BAT_SOC,
                FIELD_IN_PW,
                FIELD_OUT_PW,
                FIELD_CELL_TEMP,
            )
        )
    ):
        raw = 1
    if raw is None:
        return None
    value = description.transform(raw)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _sensor_state_value(value)


def _acc_ct_l2_capable(ct: dict[str, Any]) -> bool:
    """Recognize the App's standalone 907B L2-capable meter."""
    raw_model_code = ct.get(FIELD_MODEL_CODE)
    model_code = safe_int(raw_model_code)
    if raw_model_code is not None and model_code is None:
        return False
    names = {
        str(ct.get(key) or "").strip().upper() for key in (FIELD_SCAN_NAME, FIELD_MODEL)
    }
    if model_code is not None and model_code != 4008:  # ruff: ignore[magic-value-comparison]
        return False
    if names & {"HTO907A", "ACC_CT_907", "ACC_CT_907A"}:
        return False
    return model_code == 4008 or bool(names & {"HTO907B", "ACC_CT_907B"})  # ruff: ignore[magic-value-comparison]


def _smart_meter_description_value(  # ruff: ignore[too-many-branches]
    ct: dict[str, Any],
    description: JackerySmartMeterSensorDescription,
) -> StateType:
    """The one calculable Smart-Meter value from the current CT payload."""
    if (
        description.field in ACC_CT_L2_POWER_FIELDS | ACC_CT_L2_RAW_FIELDS
        and not _acc_ct_l2_capable(ct)
    ):
        return None
    if description.key == "ip_address":
        return accessory_ip_address(ct)
    raw = None
    if description.calculation:
        raw = calculated_smart_meter_power(ct, description.calculation)
    if raw is None and (description.aliases or description.negative_aliases):
        raw = directional_power_value(
            ct,
            description.aliases,
            description.negative_aliases,
        )
    if raw is None and (description.sum_fields or description.negative_sum_fields):
        raw = directional_power_value(
            ct,
            description.sum_fields,
            description.negative_sum_fields,
        )
    if raw is None:
        raw = ct.get(description.field)
    if raw is None and description.key in {
        "reactive_power",
        "phase_1_reactive_power",
        "phase_2_reactive_power",
        "phase_3_reactive_power",
    }:
        apparent_key, active_key = {
            "reactive_power": (FIELD_CT_APPARENT_POWER, FIELD_CT_POWER),
            "phase_1_reactive_power": (
                FIELD_CT_APPARENT_POWER1,
                FIELD_CT_POWER1,
            ),
            "phase_2_reactive_power": (
                FIELD_CT_APPARENT_POWER2,
                FIELD_CT_POWER2,
            ),
            "phase_3_reactive_power": (
                FIELD_CT_APPARENT_POWER3,
                FIELD_CT_POWER3,
            ),
        }[description.key]
        apparent = safe_float(ct.get(apparent_key))
        active = safe_float(ct.get(active_key))
        if apparent is not None and active is not None:
            raw = sqrt(max(0.0, apparent * apparent - active * active))
    if raw is None:
        for fallback in description.fallback_fields:
            raw = ct.get(fallback)
            if raw is not None:
                break
    if raw is None:
        return None
    value = description.transform(raw)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    result = round(value, 2) if isinstance(value, float) else value
    return _sensor_state_value(result)


def _smart_meter_description_has_value(
    payload: dict[str, Any],
    description: JackerySmartMeterSensorDescription,
) -> bool:
    """The True when the current CT payload supports this description."""
    ct = payload.get(PAYLOAD_CT_METER)
    return (
        isinstance(ct, dict)
        and _smart_meter_description_value(ct, description) is not None
    )


# Flat has-value guard chain over stat variants; clearest as-is.
def _stat_description_has_value(
    payload: dict[str, Any],
    description: JackeryStatSensorDescription,
) -> bool:
    """The True when a stat entity has a usable app value now."""
    local_daily_metric = LOCAL_DAILY_METRIC_BY_SENSOR_KEY.get(description.key)
    local_daily = payload.get(PAYLOAD_LOCAL_DAILY_ENERGY)
    if (
        local_daily_metric is not None
        and isinstance(local_daily, dict)
        and safe_float(local_daily.get(local_daily_metric)) is not None
    ):
        return True
    source = payload.get(description.section)
    if not isinstance(source, dict):
        source = {}
    reset_period = _period_from_stat_description(description)
    if _trend_series_key(description.section, description.stat_key) is not None:
        fallback_has_value = any(
            isinstance(fallback_source := payload.get(section), dict)
            and trend_series_has_value(fallback_source, section, stat_key)
            for section, stat_key in description.fallback_sources
        )
        return (
            trend_series_has_value(
                source,
                description.section,
                description.stat_key,
            )
            or fallback_has_value
            or _day_period_sibling_has_value(
                payload,
                description.section,
                description.stat_key,
                reset_period=reset_period,
            )
        )
    fallback_has_value = any(
        (
            isinstance(fallback_source := payload.get(section), dict)
            and safe_float(fallback_source.get(stat_key)) is not None
        )
        or _day_period_sibling_has_value(
            payload,
            section,
            stat_key,
            reset_period=reset_period,
        )
        for section, stat_key in description.fallback_sources
    )
    return (
        safe_float(source.get(description.stat_key)) is not None
        or fallback_has_value
        or _day_period_sibling_has_value(
            payload,
            description.section,
            description.stat_key,
            reset_period=reset_period,
        )
    )


# ---------------------------------------------------------------------------
# Smart Mode / AI Schedule / TOU Plan sensors
# ---------------------------------------------------------------------------


class JackerySavingsDetailSensor(JackeryEntity, SensorEntity):
    """Expose one intermediate value from the total-savings calculation."""

    _attr_has_entity_name = True
    entity_description: JackerySavingsDetailSensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: JackerySavingsDetailSensorDescription,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description
        self._attr_translation_key = description.translation_key
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._cached_native_value: float | int | str | None = None
        self._cached_attrs: dict[str, Any] = {}
        self._cache_refresh_active = False

    @property
    def _calculation(self) -> dict[str, Any]:
        savings = (self._statistic or {}).get(APP_SAVINGS_CALC_META)
        return savings if isinstance(savings, dict) else {}

    def get_savings_value(self, path: str | tuple[str, ...]) -> StateType:
        """The a savings-calculation value at one scalar or nested path."""
        keys = (path,) if isinstance(path, str) else path
        raw: object = self._calculation
        for key in keys:
            if not isinstance(raw, dict):
                return None
            raw = raw.get(key)
        return _sensor_state_value(raw)

    def _value_from_calculation(
        self,
        calculation: dict[str, Any],
    ) -> float | int | str | None:
        """Resolve the selected value from one calculation snapshot."""
        raw: object = calculation
        for key in self.entity_description.path:
            if not isinstance(raw, dict):
                return None
            raw = raw.get(key)
        if raw is None:
            return None
        value = self.entity_description.transform(raw)

        return cast("float | int | str | None", value)

    def _attrs_from_calculation(
        self,
        calculation: dict[str, Any],
    ) -> dict[str, Any]:
        """Build diagnostics from one calculation snapshot."""
        return {
            "source_section": PAYLOAD_STATISTIC,
            "source_key": APP_SAVINGS_CALC_META,
            "source_path": ".".join(self.entity_description.path),
            "method": calculation.get("method"),
            "price_source": calculation.get("price_source"),
            "published_value_source": calculation.get("published_value_source"),
            "decision": calculation.get("decision"),
            "battery_year_basis": calculation.get("battery_year_basis"),
        }

    def _refresh_cache(self) -> None:
        """Prepare a coherent savings value-and-attributes snapshot."""
        calculation = self._calculation
        self._cached_native_value = self._value_from_calculation(calculation)
        self._cached_attrs = self._attrs_from_calculation(calculation)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh the prepared snapshot before writing state."""
        if self._cache_refresh_active:
            self._refresh_cache()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime and activate the prepared savings snapshot."""
        self._refresh_cache()
        self._cache_refresh_active = True
        try:
            await super().async_added_to_hass()
        except Exception:
            self._cache_refresh_active = False
            raise

    async def async_will_remove_from_hass(self) -> None:
        """Stop serving the prepared savings snapshot after removal."""
        self._cache_refresh_active = False
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> float | int | str | None:
        """The selected calculated value."""
        if self._cache_refresh_active:
            return self._cached_native_value
        return self._value_from_calculation(self._calculation)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Calculation diagnostics from the prepared snapshot."""
        if self._cache_refresh_active:
            return self._cached_attrs
        return self._attrs_from_calculation(self._calculation)


class JackeryConversionLossPowerSensor(JackeryEntity, SensorEntity):
    """Live calculated unassigned conversion/loss power from the power balance."""

    _attr_translation_key = "conversion_loss_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "conversion_loss_power")

    def _battery_power_components(self) -> tuple[float | None, float | None, str]:
        props = self._properties
        stack_in = safe_float(props.get(FIELD_STACK_IN_PW))
        stack_out = safe_float(props.get(FIELD_STACK_OUT_PW))
        if stack_in is not None and stack_out is not None:
            return stack_in, stack_out, "stackInPw/stackOutPw"
        return (
            safe_float(props.get(FIELD_BAT_IN_PW)),
            safe_float(props.get(FIELD_BAT_OUT_PW)),
            "batInPw/batOutPw",
        )

    def _components(self) -> dict[str, float | None]:
        # Power balance at the INVERTER boundary. The AC side must use the
        # inverter's total AC output (gridOutPw/outOngridPw = house share
        # + export) — using only the grid-side export (outGridSidePw)
        # omitted the house-fed share and inflated the "loss" by the whole
        # household consumption (live finding 2026-07-03: 1995 W "loss").
        props = self._properties
        battery_charge_power, battery_discharge_power, _source = (
            self._battery_power_components()
        )
        return {
            "pv_power": safe_float(props.get(FIELD_PV_PW)),
            "battery_charge_power": battery_charge_power,
            "battery_discharge_power": battery_discharge_power,
            "inverter_ac_input_power": safe_float(
                jackery_inverter_ac_input_power(props)
            ),
            "inverter_ac_output_power": safe_float(
                jackery_inverter_ac_output_power(props)
            ),
        }

    @property
    def native_value(self) -> float | None:
        """Calculated positive residual power."""
        c = self._components()
        if any(value is None for value in c.values()):
            return None
        pv_power = safe_float(c.get("pv_power"))
        battery_discharge_power = safe_float(c.get("battery_discharge_power"))
        inverter_ac_input_power = safe_float(c.get("inverter_ac_input_power"))
        battery_charge_power = safe_float(c.get("battery_charge_power"))
        inverter_ac_output_power = safe_float(c.get("inverter_ac_output_power"))
        if (
            pv_power is None
            or battery_discharge_power is None
            or inverter_ac_input_power is None
            or battery_charge_power is None
            or inverter_ac_output_power is None
        ):
            return None
        produced = pv_power + battery_discharge_power + inverter_ac_input_power
        consumed = battery_charge_power + inverter_ac_output_power
        return round(max(0.0, produced - consumed), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Formula and source components."""
        battery_charge_power, battery_discharge_power, battery_source = (
            self._battery_power_components()
        )
        return {
            "formula": (
                "max(pv_power + battery_discharge_power + inverter_ac_input_power "
                "- battery_charge_power - inverter_ac_output_power, 0)"
            ),
            "scope": (
                "calculated residual at the inverter boundary; "
                "inverter_ac_output_power = house share + grid export "
                "(gridOutPw/outOngridPw)"
            ),
            "battery_power_source": battery_source,
            "stackInPw": self._properties.get(FIELD_STACK_IN_PW),
            "stackOutPw": self._properties.get(FIELD_STACK_OUT_PW),
            "batInPw": self._properties.get(FIELD_BAT_IN_PW),
            "batOutPw": self._properties.get(FIELD_BAT_OUT_PW),
            "selected_battery_charge_power": battery_charge_power,
            "selected_battery_discharge_power": battery_discharge_power,
            **self._components(),
        }


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _SensorCollection:
    """State shared by one idempotent sensor collection pass."""

    coordinator: JackerySolarVaultCoordinator
    seen_unique_ids: set[str]
    battery_pack_identities: dict[tuple[str, int], tuple[str | None, str]]
    create_smart_meter_derived: bool
    create_calculated_power: bool
    create_savings_details: bool
    entities: list[SensorEntity]

    def add(self, entity: SensorEntity) -> None:
        """Append only a previously unseen entity unique ID."""
        append_unique_entity(self.entities, self.seen_unique_ids, entity)


def _sensor_entity_option_signature(
    entry: JackeryConfigEntry,
) -> tuple[bool, bool, bool]:
    """Return options that alter the sensor entity set."""
    return (
        config_entry_bool_option(
            entry,
            CONF_CREATE_SMART_METER_DERIVED_SENSORS,
            DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
        ),
        config_entry_bool_option(
            entry,
            CONF_CREATE_CALCULATED_POWER_SENSORS,
            DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
        ),
        config_entry_bool_option(
            entry,
            CONF_CREATE_SAVINGS_DETAIL_SENSORS,
            DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
        ),
    )


_BOX_NON_ATS_MODEL_CODE = 2001
_BOX_ATS_MODEL_CODE = 2002


def _app_sensor_matches_model(
    coordinator: JackerySolarVaultCoordinator, device_id: str, key: str
) -> bool:
    if key == "box_reserve_threshold":
        return coordinator.device_model_code(device_id) == _BOX_NON_ATS_MODEL_CODE
    if key == "box_ats_solar_power":
        return coordinator.device_model_code(device_id) == _BOX_ATS_MODEL_CODE
    if key == "portable_reserve_restore_threshold":
        return coordinator.device_model_code(device_id) in PORTABLE_RESERVE_MODELS
    return True


def _sensor_registration_eligibility(  # ruff: ignore[too-many-branches, complex-structure]
    coordinator: JackerySolarVaultCoordinator,
) -> set[tuple[str, str, str]]:
    """Return value-gated entity keys supported by the latest payload."""
    eligible: set[tuple[str, str, str]] = set()
    for dev_id, payload in (coordinator.data or {}).items():
        ct_statistics_current = getattr(
            coordinator, "portable_ct_statistics_is_current", None
        )
        if callable(ct_statistics_current) and ct_statistics_current(dev_id) is True:
            eligible.add((dev_id, "portable_ct", "portable_ct_statistics"))
        props = payload_properties_for_sources(payload)
        family = coordinator.device_family(dev_id)
        if family == "unknown":
            continue
        is_portable = family == "portable"
        eligible.update(
            (dev_id, "battery_pack_temperature", serial)
            for serial in _battery_pack_temperature_serials(
                _battery_pack_registration_rows(payload)
            )
        )
        system = payload.get(PAYLOAD_SYSTEM)
        grid_standard = (
            _system_meta_scalar_value(system.get(FIELD_GRID_STANDARD))
            if isinstance(system, dict)
            else None
        )
        if not is_portable and grid_standard is not None:
            eligible.add((dev_id, "system_meta", "grid_standard"))
        groups = (
            (SENSOR_DESCRIPTIONS, PORTABLE_SENSOR_DESCRIPTIONS)
            if is_portable
            else (
                SENSOR_DESCRIPTIONS,
                SMART_MODE_SENSOR_DESCRIPTIONS,
                SMART_SCHEDULE_SENSOR_DESCRIPTIONS,
                DYNAMIC_PRICE_SENSOR_DESCRIPTIONS,
                TOU_PLAN_SENSOR_DESCRIPTIONS,
            )
        )
        for descriptions in groups:
            for property_description in descriptions:
                if not _app_sensor_matches_model(
                    coordinator, dev_id, property_description.key
                ):
                    continue
                if (
                    property_description.key == "system_reported_pv_power"
                    and family != "home"
                ):
                    continue
                if _sensor_description_has_value(payload, property_description):
                    eligible.add((dev_id, "property", property_description.key))
        if family == "box":
            for description in BOX_SENSOR_DESCRIPTIONS:
                if not _app_sensor_matches_model(coordinator, dev_id, description.key):
                    continue
                if _sensor_description_has_value(payload, description):
                    eligible.add((dev_id, "property", description.key))
        if family == "home":
            for description in (
                *AIEMS_STATUS_SENSOR_DESCRIPTIONS,
                *AIEMS_PREDICTION_SENSOR_DESCRIPTIONS,
            ):
                if _sensor_description_has_value(payload, description):
                    eligible.add((dev_id, "property", description.key))
        for stat_description in STAT_DESCRIPTIONS:
            if _stat_description_has_value(payload, stat_description):
                eligible.add((dev_id, "stat", stat_description.key))
        ct = payload.get(PAYLOAD_CT_METER)
        for meter_description in SMART_METER_SENSOR_DESCRIPTIONS:
            if (
                isinstance(ct, dict)
                and meter_description.field
                in CT_THIRD_PV_POWER_FIELDS
                | ACC_CT_L2_POWER_FIELDS
                | ACC_CT_L2_RAW_FIELDS
                and coordinator.ct_model_conflicted(dev_id, ct) is True
            ):
                continue
            if _smart_meter_description_has_value(payload, meter_description):
                eligible.add((dev_id, "smart_meter", meter_description.key))
        if (
            jackery_corrected_home_consumption_power(
                ct if isinstance(ct, dict) else {},
                props,
            )
            is not None
        ):
            eligible.add((dev_id, "derived", "home_consumption_power"))
    return eligible


def _reconcile_pv_input_devices(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    reconciled: set[tuple[str, int]],
) -> None:
    """Move PV sensors registered before their input channel appeared."""
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    for device_id, payload in (coordinator.data or {}).items():
        properties = payload_properties_for_sources(payload)
        parent = (
            devices.async_get_device_by_identifier((DOMAIN, device_id), entry.entry_id)
            if hasattr(devices, "async_get_device_by_identifier")
            else devices.async_get_device(identifiers={(DOMAIN, device_id)})
        )
        if parent is None:
            continue
        for index in range(1, 5):
            channel = (device_id, index)
            if channel in reconciled or not isinstance(
                properties.get(f"pv{index}"), dict
            ):
                continue
            identifier = (DOMAIN, f"{device_id}_pv_input_{index}")
            child = (
                devices.async_get_child_device_by_identifier(identifier, entry.entry_id)
                if hasattr(devices, "async_get_child_device_by_identifier")
                else devices.async_get_device(identifiers={identifier})
            ) or (
                devices.async_get_or_create_child(
                    config_entry_id=entry.entry_id,
                    identifiers={identifier},
                    name=f"{parent.name or device_id} PV {index}",
                    parent_device_id=parent.id,
                )
                if hasattr(devices, "async_get_or_create_child")
                else devices.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={identifier},
                    name=f"{parent.name or device_id} PV {index}",
                    via_device=(DOMAIN, device_id),
                )
            )
            unique_ids = {f"{device_id}_pv{index}_power"} | {
                f"{device_id}_device_pv{index}_{period}_energy"
                for period in ("day", "week", "month", "year")
            }
            for entity in er.async_entries_for_device(
                entities, parent.id, include_disabled_entities=True
            ):
                if (
                    entity.config_entry_id == entry.entry_id
                    and entity.domain == "sensor"
                    and entity.platform == DOMAIN
                    and entity.unique_id in unique_ids
                ):
                    entities.async_update_entity(entity.entity_id, device_id=child.id)
            reconciled.add(channel)


def _collect_portable_property_entities(
    collection: _SensorCollection, dev_id: str, payload: dict[str, Any]
) -> None:
    """Collect Portable-specific property entities."""
    coordinator = collection.coordinator
    for description in PORTABLE_SENSOR_DESCRIPTIONS:
        if description.key in {"portable_strategy_plans", "portable_current_strategy"}:
            if _sensor_description_has_value(payload, description):
                collection.add(
                    JackeryPortableStrategySensor(coordinator, dev_id, description)
                )
            continue
        if description.key == "portable_wifi_scan":
            if _sensor_description_has_value(payload, description):
                collection.add(
                    JackeryPortableWifiScanSensor(coordinator, dev_id, description)
                )
            continue
        if description.key not in {
            "output_temperature",
            "portable_reserve_restore_threshold",
            "portable_ac_delay_remaining",
            "portable_ac_delay_setting",
            "portable_idc_raw",
            "portable_bs_raw",
            "portable_ground_accessory_serial",
        }:
            collection.add(JackerySensor(coordinator, dev_id, description))
            continue
        if _app_sensor_matches_model(
            coordinator, dev_id, description.key
        ) and _sensor_description_has_value(payload, description):
            collection.add(JackeryPortableSensor(coordinator, dev_id, description))


def _pv_channel_supported(props: dict[str, Any], channel: int) -> bool:
    """Return True if the hardware PV channel is supported by the device."""
    if channel in {1, 2}:
        return True
    return (
        isinstance(props.get(f"pv{channel}"), dict)
        or props.get(f"pv{channel}_power") is not None
        or props.get(f"pv{channel}_voltage") is not None
    )


def _collect_property_entities(  # ruff: ignore[too-many-branches]  # ruff: ignore[complex-structure]
    collection: _SensorCollection,
    dev_id: str,
    payload: dict[str, Any],
    props: dict[str, Any],
    *,
    family: Literal["home", "box", "portable"],
) -> None:
    """Collect main, portable, and home-mode property sensors."""
    coordinator = collection.coordinator
    is_portable = family == "portable"
    for description in SENSOR_DESCRIPTIONS:
        if description.key == "system_reported_pv_power" and (
            family != "home" or props.get(FIELD_SYS_TOTAL_PV_PW) is None
        ):
            continue
        if is_portable and description.key in {
            "main_battery_charge_energy",
            "main_battery_discharge_energy",
        }:
            continue
        if is_portable and not _sensor_description_has_value(payload, description):
            continue
        if description.key == "pv3_power" and not _pv_channel_supported(props, 3):
            continue
        if description.key == "pv4_power" and not _pv_channel_supported(props, 4):
            continue
        sensor_type = (
            JackeryHomeSensor
            if description.key == "system_reported_pv_power"
            else JackerySensor
        )
        collection.add(sensor_type(coordinator, dev_id, description))
    system = payload.get(PAYLOAD_SYSTEM)
    raw_grid_standard = (
        system.get(FIELD_GRID_STANDARD) if isinstance(system, dict) else None
    )
    malformed_grid_standard = (
        raw_grid_standard is not None
        and _system_meta_scalar_value(raw_grid_standard) is None
    )
    if not is_portable and not malformed_grid_standard:
        collection.add(
            JackerySystemMetaSensor(
                coordinator,
                dev_id,
                key="grid_standard",
                translation_key="grid_standard",
                source_key=FIELD_GRID_STANDARD,
            )
        )
    if is_portable:
        _collect_portable_property_entities(collection, dev_id, payload)
        return
    if coordinator.device_family(dev_id) == "box":
        for description in BOX_SENSOR_DESCRIPTIONS:
            if not _app_sensor_matches_model(coordinator, dev_id, description.key):
                continue
            if _sensor_description_has_value(payload, description):
                sensor_type = (
                    JackeryBoxWifiScanSensor
                    if description.key == "box_wifi_scan"
                    else JackeryBoxStrategySensor
                    if description.key in {"box_strategy_plans", "box_current_strategy"}
                    else JackeryBoxSensor
                )
                collection.add(sensor_type(coordinator, dev_id, description))
    if coordinator.device_family(dev_id) == "home":
        for description in AIEMS_STATUS_SENSOR_DESCRIPTIONS:
            if _sensor_description_has_value(payload, description):
                collection.add(
                    JackeryAiEmsStatusSensor(coordinator, dev_id, description)
                )
        for description in AIEMS_PREDICTION_SENSOR_DESCRIPTIONS:
            if _sensor_description_has_value(payload, description):
                collection.add(
                    JackeryAiEmsPredictionSensor(coordinator, dev_id, description)
                )
    for descriptions in (
        SMART_MODE_SENSOR_DESCRIPTIONS,
        SMART_SCHEDULE_SENSOR_DESCRIPTIONS,
    ):
        for description in descriptions:
            collection.add(JackerySensor(coordinator, dev_id, description))
    for description in DYNAMIC_PRICE_SENSOR_DESCRIPTIONS:
        if _sensor_description_has_value(payload, description):
            collection.add(JackerySensor(coordinator, dev_id, description))
    for description in TOU_PLAN_SENSOR_DESCRIPTIONS:
        collection.add(JackerySensor(coordinator, dev_id, description))


def _collect_stat_and_diagnostics(
    collection: _SensorCollection,
    dev_id: str,
    payload: dict[str, Any],
    props: dict[str, Any] | None = None,
    *,
    is_portable: bool,
) -> None:
    """Collect period, calculated, savings, and stable diagnostics."""
    coordinator = collection.coordinator
    if props is None:
        props = payload_properties_for_sources(payload)
    for stat_description in STAT_DESCRIPTIONS:
        if (
            is_portable or stat_description.key.startswith("symmetry_")
        ) and not _stat_description_has_value(payload, stat_description):
            continue
        if "device_pv3_" in stat_description.key and not _pv_channel_supported(
            props, 3
        ):
            continue
        if "device_pv4_" in stat_description.key and not _pv_channel_supported(
            props, 4
        ):
            continue
        collection.add(JackeryStatSensor(coordinator, dev_id, stat_description))
    if collection.create_calculated_power:
        collection.add(JackeryBatteryNetPowerSensor(coordinator, dev_id))
        collection.add(JackeryBatteryStackNetPowerSensor(coordinator, dev_id))
        collection.add(JackeryGridNetPowerSensor(coordinator, dev_id))
    if collection.create_savings_details:
        for savings_description in SAVINGS_DETAIL_SENSOR_DESCRIPTIONS:
            collection.add(
                JackerySavingsDetailSensor(
                    coordinator,
                    dev_id,
                    savings_description,
                )
            )
        collection.add(JackeryConversionLossPowerSensor(coordinator, dev_id))
    collection.add(JackeryAlarmSensor(coordinator, dev_id))
    collection.add(JackeryFirmwareSensor(coordinator, dev_id))
    collection.add(JackeryBleTransportSensor(coordinator, dev_id))
    collection.add(JackeryHttpApiSensor(coordinator, dev_id))
    collection.add(JackeryCloudMqttSensor(coordinator, dev_id))
    collection.add(JackeryLocalMqttSensor(coordinator, dev_id))
    collection.add(JackeryDeviceActivationSensor(coordinator, dev_id))


def _battery_pack_registration_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the pack rows used to register add-on battery entities."""
    packs = payload.get(PAYLOAD_BATTERY_PACKS)
    valid_packs = (
        [pack for pack in packs if isinstance(pack, dict)]
        if isinstance(packs, list)
        else []
    )
    return valid_packs or sorted_battery_pack_payloads(
        subdevice_accessories(payload, dev_type=SUBDEVICE_DEV_TYPE_BATTERY_PACK)
    )


def _battery_pack_temperature_serials(packs: list[dict[str, Any]]) -> set[str]:
    """Find unambiguous packs with an actual pack-bound temperature."""
    by_serial: dict[str, list[dict[str, Any]]] = {}
    for pack in packs:
        if (serial := battery_pack_serial(pack)) is not None:
            by_serial.setdefault(serial, []).append(pack)
    description = next(
        description
        for description in BATTERY_PACK_SENSOR_DESCRIPTIONS
        if description.field == FIELD_CELL_TEMP
    )
    return {
        serial
        for serial, rows in by_serial.items()
        if len(rows) == 1
        and _battery_pack_description_value(rows[0], description) is not None
    }


def _collect_battery_packs(  # ruff: ignore[too-many-locals]
    collection: _SensorCollection,
    dev_id: str,
    payload: dict[str, Any],
    props: dict[str, Any],
) -> None:
    """Collect add-on battery sensors with session-frozen identities."""
    registration_packs = _battery_pack_registration_rows(payload)
    bat_num = safe_int(props.get(FIELD_BAT_NUM))
    # A received internal-pack row can be present alongside all five add-ons.
    count = max(len(registration_packs), min(5, max(0, bat_num or 0)))
    coordinator = collection.coordinator
    resolve_serial = getattr(coordinator, "_resolve_device_sn", None)
    head_serial = resolve_serial(dev_id) if callable(resolve_serial) else None
    head_key = head_serial.strip().casefold() if isinstance(head_serial, str) else None
    count = max(
        count,
        max(
            (
                index
                for index in range(1, 6)
                if coordinator.battery_pack_identity_serial(dev_id, index) is not None
            ),
            default=0,
        ),
    )
    for index in range(1, count + 1):
        identity_key = (dev_id, index)
        identity = collection.battery_pack_identities.get(identity_key)
        if identity is None:
            serial = coordinator.battery_pack_identity_serial(dev_id, index)
            if serial is None:
                # An index-based identity flips to a serial-based unique_id on a
                # later restart, which re-registers every pack entity and drops the
                # user's enable state; the pack appears once its serial is known
                # (the entity signature includes pack serials).
                continue
            if serial.strip().casefold() == head_key:
                continue
            coordinator.set_battery_pack_identity_override(dev_id, index, serial)
            stable_key = stable_subdevice_key("battery_pack", serial, index)
            identity = collection.battery_pack_identities[identity_key] = (
                serial,
                stable_key,
            )
        serial, stable_key = identity
        if serial is not None and serial.strip().casefold() == head_key:
            continue
        for description in BATTERY_PACK_SENSOR_DESCRIPTIONS:
            # Sparse cellTemp must not prevent creating a serial-owned entity.
            # Never claim an entity registered under a different config entry.
            if description.field == FIELD_CELL_TEMP:
                hass = getattr(coordinator, "hass", None)
                entry = getattr(coordinator, "config_entry", None) or getattr(
                    coordinator, "entry", None
                )
                if hass is not None and entry is not None:
                    registry = er.async_get(hass)
                    unique_id = f"{dev_id}_{stable_key}_{description.key}"
                    entity_id = registry.async_get_entity_id(
                        "sensor", DOMAIN, unique_id
                    )
                    existing = registry.async_get(entity_id) if entity_id else None
                    if (
                        existing is not None
                        and existing.config_entry_id != entry.entry_id
                    ):
                        continue
            collection.add(
                JackeryBatteryPackSensor(
                    coordinator,
                    dev_id,
                    identity=(index, serial, stable_key),
                    description=description,
                    enabled_default=description.entity_category
                    != EntityCategory.DIAGNOSTIC,
                )
            )


def _collect_smart_plugs(
    collection: _SensorCollection,
    dev_id: str,
    payload: dict[str, Any],
) -> None:
    """Collect smart-plug accessory sensors."""
    plugs = sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS))
    if not plugs:
        plugs = sorted_smart_plugs(
            subdevice_accessories(payload, dev_type=SUBDEVICE_DEV_TYPE_SOCKET)
        )
    for index, plug in enumerate(plugs, start=1):
        serial = smart_plug_serial(plug)
        if serial is None:
            continue
        identity = (index, serial, stable_subdevice_key("smart_plug", serial, index))
        for description in SMART_PLUG_SENSOR_DESCRIPTIONS:
            collection.add(
                JackerySmartPlugSensor(
                    collection.coordinator,
                    dev_id,
                    identity=identity,
                    description=description,
                )
            )


def _collect_meter_heads(
    collection: _SensorCollection,
    dev_id: str,
    payload: dict[str, Any],
) -> None:
    """Collect disabled-by-default meter-head diagnostics."""
    meter_heads = sorted_meter_heads(payload.get(PAYLOAD_METER_HEADS))
    if not meter_heads:
        meter_heads = sorted_meter_heads([
            *subdevice_accessories(payload, dev_type=SUBDEVICE_DEV_TYPE_METER_HEAD),
            *subdevice_accessories(payload, dev_type=SUBDEVICE_DEV_TYPE_METER),
        ])
    for index, meter_head in enumerate(meter_heads, start=1):
        serial = meter_head_serial(meter_head)
        if serial is None:
            continue
        identity = (index, serial, stable_subdevice_key("meter_head", serial, index))
        for description in METER_HEAD_SENSOR_DESCRIPTIONS:
            collection.add(
                JackeryMeterHeadSensor(
                    collection.coordinator,
                    dev_id,
                    identity=identity,
                    description=description,
                )
            )


def _collect_breakers(
    collection: _SensorCollection,
    dev_id: str,
    payload: dict[str, Any],
) -> None:
    """Collect circuit-breaker accessory sensors."""
    breakers = sorted_circuits(payload.get(PAYLOAD_CIRCUIT_PROPERTY))
    if not breakers:
        breakers = sorted_circuits(
            subdevice_accessories(payload, dev_type=SUBDEVICE_DEV_TYPE_BREAKER)
        )
    for index, breaker in enumerate(breakers, start=1):
        breaker_id = circuit_id(breaker)
        if breaker_id is None:
            continue
        identity = (
            index,
            breaker_id,
            stable_subdevice_key("breaker", breaker_id, index),
        )
        for description in BREAKER_SENSOR_DESCRIPTIONS:
            collection.add(
                JackeryBreakerSensor(
                    collection.coordinator,
                    dev_id,
                    identity=identity,
                    description=description,
                )
            )


def _collect_subdevice_alarms(
    collection: _SensorCollection,
    dev_id: str,
    payload: dict[str, Any],
) -> None:
    """Collect smoke, leak, and temperature accessory alarm sensors."""
    sub_devices = sorted_sub_devices(payload.get(PAYLOAD_SUBDEVICES))
    if not sub_devices:
        sub_devices = sorted_sub_devices([
            *subdevice_accessories(payload, dev_type=SUBDEVICE_DEV_TYPE_SMOKE),
            *subdevice_accessories(
                payload,
                dev_type=SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY,
            ),
            *subdevice_accessories(payload, dev_type=SUBDEVICE_DEV_TYPE_WATER_LEAK),
        ])
    for index, sub_device in enumerate(sub_devices, start=1):
        serial = sub_device_serial(sub_device)
        if serial is None:
            continue
        identity = (index, serial, stable_subdevice_key("sub_device", serial, index))
        for description in SUBDEVICE_ALARM_SENSOR_DESCRIPTIONS:
            collection.add(
                JackerySubdeviceAlarmSensor(
                    collection.coordinator,
                    dev_id,
                    identity=identity,
                    description=description,
                )
            )


def _collect_smart_meter_entities(
    collection: _SensorCollection,
    dev_id: str,
    payload: dict[str, Any],
) -> None:
    """Collect CT sensors and optional derived home consumption."""
    coordinator = collection.coordinator
    present = bool(
        coordinator.has_smart_meter_accessory(payload) or payload.get(PAYLOAD_CT_METER)
    )
    if not present:
        return
    ct = payload.get(PAYLOAD_CT_METER)
    cloud_meter = any(
        item.get(FIELD_IS_CLOUD) is True
        for item in (
            ct if isinstance(ct, dict) else {},
            *smart_meter_accessories(payload),
        )
    )
    hass = getattr(coordinator, "hass", None)
    registry = er.async_get(hass) if cloud_meter and hass is not None else None
    for description in SMART_METER_SENSOR_DESCRIPTIONS:
        if (
            isinstance(ct, dict)
            and description.field
            in CT_THIRD_PV_POWER_FIELDS | ACC_CT_L2_POWER_FIELDS | ACC_CT_L2_RAW_FIELDS
            and coordinator.ct_model_conflicted(dev_id, ct) is True
        ):
            continue
        # Match the current meter itself, never borrow another accessory's
        # capability. Unobserved optional fields must not create ghost entities.
        if description.field in CT_THIRD_PV_POWER_FIELDS and (
            not isinstance(ct, dict)
            or not any(
                str(ct.get(key) or "").upper() in {"HTO907B", "ACC_CT_907B"}
                for key in (FIELD_SCAN_NAME, FIELD_MODEL)
            )
            or _smart_meter_description_value(ct, description) is None
        ):
            continue
        if description.field in ACC_CT_L2_POWER_FIELDS | ACC_CT_L2_RAW_FIELDS and (
            not isinstance(ct, dict)
            or _smart_meter_description_value(ct, description) is None
        ):
            continue
        if description.calculation and not collection.create_smart_meter_derived:
            continue
        if cloud_meter and description.field in ACC_CT_BODY_MEASUREMENT_FIELDS:
            # Preserve existing identities and any measurement actually supplied
            # by this meter; cloud ownership alone is not a field rejection.
            unique_id = f"{dev_id}_smart_meter_{description.key}"
            registered = registry is not None and registry.async_get_entity_id(
                "sensor", DOMAIN, unique_id
            )
            if (
                not registered
                and _smart_meter_description_value(
                    ct if isinstance(ct, dict) else {}, description
                )
                is None
            ):
                continue
        collection.add(JackerySmartMeterSensor(coordinator, dev_id, description))
    if collection.create_smart_meter_derived:
        collection.add(JackeryHomeConsumptionPowerSensor(coordinator, dev_id))


def _collect_sensor_entities(
    coordinator: JackerySolarVaultCoordinator,
    option_signature: tuple[bool, bool, bool],
    seen_unique_ids: set[str],
    battery_pack_identities: dict[tuple[str, int], tuple[str | None, str]],
) -> list[SensorEntity]:
    """Collect every supported sensor family for the current payload."""
    collection = _SensorCollection(
        coordinator,
        seen_unique_ids,
        battery_pack_identities,
        *option_signature,
        [],
    )
    for dev_id, payload in (coordinator.data or {}).items():
        ct_statistics_current = getattr(
            coordinator, "portable_ct_statistics_is_current", None
        )
        if callable(ct_statistics_current) and ct_statistics_current(dev_id) is True:
            for description in PORTABLE_CT_STATISTICS_DESCRIPTIONS:
                collection.add(
                    JackeryPortableCtStatisticsSensor(coordinator, dev_id, description)
                )
        props = payload_properties_for_sources(payload)
        family = coordinator.device_family(dev_id)
        if family == "unknown":
            continue
        is_portable = family == "portable"
        _collect_property_entities(
            collection,
            dev_id,
            payload,
            props,
            family=family,
        )
        _collect_stat_and_diagnostics(
            collection,
            dev_id,
            payload,
            props=props,
            is_portable=is_portable,
        )
        _collect_battery_packs(collection, dev_id, payload, props)
        _collect_smart_plugs(collection, dev_id, payload)
        _collect_meter_heads(collection, dev_id, payload)
        _collect_breakers(collection, dev_id, payload)
        _collect_subdevice_alarms(collection, dev_id, payload)
        _collect_smart_meter_entities(collection, dev_id, payload)
    return collection.entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up and register sensor entities for a Jackery SolarVault config entry.

    Builds the sensor entity set from the coordinator payloads and the integration
    options:
    - Inspects each device payload and creates property-driven sensors, statistic/price
    sensors,
      battery-pack, smart-plug, meter-head, smart-meter (CT) and derived/calculated
      sensors
      according to the available data and user options.
    - Honors user options to enable creation of smart-meter-derived sensors, calculated
    power
      sensors, and savings-detail sensors.
    - Deduplicates entities by unique_id and skips sensors that would be permanently
    unknown
      (e.g., absent statistic sections).
    - Registers a listener that rebuilds the entity set only when the coordinator data
    signature
      changes, and primes the initial entity creation immediately.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    mqtt_publisher = (
        JackeryMqttSensorPublisher(hass, entry_id=entry.entry_id)
        if "mqtt" in hass.config.components
        else None
    )
    if mqtt_publisher is not None:
        entry.async_on_unload(mqtt_publisher.async_retire)
        await async_release_native_entity_ids(hass)
        entry.async_on_unload(async_start_mqtt_discovery_cleanup(hass, entry))
        entry.async_on_unload(
            subscribe_connection_status(
                hass,
                lambda connected: (
                    mqtt_publisher.async_broker_connected() if connected else None
                ),
                entry.entry_id,
            )
        )
        entry.async_on_unload(
            coordinator.async_add_listener(mqtt_publisher.async_schedule_publish)
        )
    seen_unique_ids: set[str] = set()
    battery_pack_identities: dict[tuple[str, int], tuple[str | None, str]] = {}

    def _entity_option_signature() -> tuple[bool, bool, bool]:
        """Return options that control sensor registration."""
        return _sensor_entity_option_signature(entry)

    def _registration_eligibility() -> set[tuple[str, str, str]]:
        """Return value-gated keys supported by the latest payload."""
        return _sensor_registration_eligibility(coordinator)

    def _collect_entities(
        option_signature: tuple[bool, bool, bool],
    ) -> list[SensorEntity]:
        """Collect entities from the current coordinator payload."""
        return _collect_sensor_entities(
            coordinator,
            option_signature,
            seen_unique_ids,
            battery_pack_identities,
        )

    # Gate the listener with ``coordinator_entity_signature`` so routine
    # MQTT pushes (which leave the entity-set unchanged) don't rebuild
    # every JackeryEntity and emit a dedup-DEBUG entry for every known
    # unique_id. Live entity-state updates flow through each entity's
    # own CoordinatorEntity listener — independent of this gate
    # (verified in the 2026-05-16 production audit).
    last_signature: tuple[Any, ...] = ()
    last_option_signature: tuple[bool, bool, bool] | None = None
    option_suffix_groups = (
        SMART_METER_DERIVED_SENSOR_SUFFIXES,
        CALCULATED_POWER_SENSOR_SUFFIXES,
        SAVINGS_DETAIL_SENSOR_SUFFIXES,
    )
    known_registration_eligibility: set[tuple[str, str, str]] = set()
    reconciled_pv_channels: set[tuple[str, int]] = set()

    @callback
    def _add_new_entities() -> None:
        """Implementation details.

        Detects changes in the coordinator data signature and adds any newly
        discovered entities to Home Assistant.

        Compares the current coordinator entity signature with the previously stored
        signature; when different, updates the stored signature, collects entities to
        create, and calls the platform's entity adder for any discovered entities.
        """
        nonlocal last_option_signature, last_signature
        _reconcile_pv_input_devices(hass, entry, coordinator, reconciled_pv_channels)
        sig = coordinator_entity_signature(coordinator.data)
        option_signature = _entity_option_signature()
        registration_eligibility = _registration_eligibility()
        if (
            sig == last_signature
            and option_signature == last_option_signature
            and registration_eligibility <= known_registration_eligibility
        ):
            return
        if last_option_signature is not None:
            changed_suffixes: set[str] = set()
            for old_value, new_value, suffixes in zip(
                last_option_signature,
                option_signature,
                option_suffix_groups,
                strict=True,
            ):
                if old_value != new_value:
                    changed_suffixes.update(suffixes)
            suffix_tuple = tuple(changed_suffixes)
            if suffix_tuple:
                seen_unique_ids.difference_update({
                    unique_id
                    for unique_id in seen_unique_ids
                    if unique_id.endswith(suffix_tuple)
                })
        entities = _collect_entities(option_signature)
        if entities:
            if mqtt_publisher is not None:
                for entity in entities:
                    mqtt_publisher.track_when_native_ready(entity)
            async_add_entities(entities)
        last_signature = sig
        last_option_signature = option_signature
        known_registration_eligibility.update(registration_eligibility)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------
class _LifetimeCacheOwner(Protocol):
    """Mutable lifetime cache attributes shared by Jackery sensor variants."""

    _own_lifetime_leaf_serial: str | None
    _own_lifetime_parent_token: tuple[object, ...] | None
    _cached_native_value: object
    _cached_attrs: dict[str, object]
    _restored_lifetime_value: object
    _observed_period_memo: object


def _invalidate_own_lifetime_parent_cache(entity: object) -> None:  # ruff: ignore[too-many-locals, too-many-statements]
    """Keep a lifetime floor only while its physical parent binding is unchanged."""
    coordinator = getattr(entity, "coordinator", None)
    device_id = getattr(entity, "_device_id", None)
    index = getattr(coordinator, "_device_index", {})
    record = index.get(device_id) if isinstance(index, dict) else None
    record = record if isinstance(record, dict) else {}

    def identity(
        metadata: object, keys: tuple[str, ...], *, serial: bool = False
    ) -> str | None:
        if not isinstance(metadata, dict):
            return None
        for key in keys:
            value = metadata.get(key)
            if isinstance(value, bool) or not isinstance(value, str | int):
                continue
            normalized = str(value).strip()
            if normalized:
                return normalized.casefold() if serial else normalized
        return None

    device = record.get(PAYLOAD_DEVICE_META)
    system = record.get(PAYLOAD_SYSTEM_META)
    resolver = getattr(coordinator, "_resolve_device_sn", None)
    if callable(resolver):
        resolved = resolver(device_id)
        serial = (
            resolved.strip().casefold()
            if isinstance(resolved, str) and resolved.strip()
            else None
        )
    else:
        # Lightweight test/legacy coordinators may not expose the resolver.
        serial = identity(
            device, (FIELD_DEVICE_SN, FIELD_DEV_SN, FIELD_SN), serial=True
        )
    section = getattr(getattr(entity, "entity_description", None), "section", "")
    ct_required = (
        getattr(entity, "_ct_energy_source", False)
        or section == APP_SECTION_CT_STAT
        or section.startswith(f"{APP_SECTION_CT_STAT}_")
    )
    ct_id = ct_serial = None
    if ct_required:
        owner_reader = getattr(coordinator, "_observed_day_source_owner", None)
        owner = (
            owner_reader(device_id, APP_SECTION_CT_STAT)
            if callable(owner_reader)
            else None
        )
        if isinstance(owner, tuple) and len(owner) == 4:  # ruff: ignore[magic-value-comparison]
            if isinstance(owner[2], str | int) and not isinstance(owner[2], bool):
                ct_id = str(owner[2]).strip() or None
            if isinstance(owner[3], str):
                ct_serial = owner[3].strip().casefold() or None
    token: tuple[object, ...] = (
        coordinator,
        getattr(coordinator, "entry", None),
        device_id,
        getattr(coordinator, "_http_parent_identity_epochs", {}).get(device_id),
        getattr(coordinator, "_system_binding_epochs", {}).get(device_id),
        serial,
        identity(record, (FIELD_SYSTEM_ID,))
        or identity(system, (FIELD_SYSTEM_ID, FIELD_ID)),
        identity(system, (FIELD_SYSTEM_SN, FIELD_SN), serial=True),
        ct_id,
        ct_serial,
    )
    leaf_required = hasattr(entity, "_plug_sn")
    raw_leaf = getattr(entity, "_plug_sn", None)
    leaf = (
        raw_leaf.strip().casefold()
        if isinstance(raw_leaf, str) and raw_leaf.strip()
        else None
    )
    previous_leaf = getattr(entity, "_own_lifetime_leaf_serial", leaf)
    cache_owner = cast("_LifetimeCacheOwner", entity)
    cache_owner._own_lifetime_leaf_serial = leaf  # ruff: ignore[private-member-access]
    previous = getattr(entity, "_own_lifetime_parent_token", None)
    changed = (leaf_required and previous_leaf != leaf) or (
        previous is not None
        and (
            previous[0] is not token[0]
            or previous[1] is not token[1]
            or previous[2:5] != token[2:5]
            or (ct_required and previous[8] != token[8])
            or any(
                old is not None and new is not None and old != new
                for old, new in zip(previous[5:], token[5:], strict=True)
            )
        )
    )
    # Optional metadata may be enriched or omitted without changing hardware.
    # Retain the last known canonical identity through such sparse updates;
    # binding/parent epochs still reject a physical A -> B -> A transition.
    if previous is not None and not changed:
        token = (
            *token[:5],
            *(
                old if new is None else new
                for old, new in zip(previous[5:], token[5:], strict=True)
            ),
        )
    cache_owner._own_lifetime_parent_token = token  # ruff: ignore[private-member-access]
    if not changed:
        return
    cache_owner._cached_native_value = None  # ruff: ignore[private-member-access]
    cache_owner._cached_attrs = {}  # ruff: ignore[private-member-access]
    if hasattr(entity, "_restored_lifetime_value"):
        cache_owner._restored_lifetime_value = None  # ruff: ignore[private-member-access]
    if hasattr(entity, "_observed_period_memo"):
        cache_owner._observed_period_memo = None  # ruff: ignore[private-member-access]
    for attribute in (
        "_local_day_snapshot",
        "_local_day_snapshot_date",
        "_period_energy_snapshot",
    ):
        if hasattr(entity, attribute):
            setattr(entity, attribute, None)


def _same_energy_owner_tokens(before: object, after: object) -> bool:
    """Compare runtime owners without equating distinct coordinators or entries."""
    return (
        isinstance(before, tuple)
        and isinstance(after, tuple)
        and len(before) == len(after) == 10  # ruff: ignore[magic-value-comparison]
        and before[0] is after[0]
        and before[1] is after[1]
        and before[2:5] == after[2:5]
        and before[8] == after[8]
        and all(
            old is None or new is None or old == new
            for old, new in zip(before[5:], after[5:], strict=True)
        )
    )


def _energy_owner_identity(entity: object) -> dict[str, str] | None:
    """Return a persistent physical owner, retaining optional sparse metadata."""
    _invalidate_own_lifetime_parent_cache(entity)
    token = getattr(entity, "_own_lifetime_parent_token", None)
    if not isinstance(token, tuple) or len(token) != 10 or not token[5]:  # ruff: ignore[magic-value-comparison]
        return None
    section = getattr(getattr(entity, "entity_description", None), "section", "")
    if (
        getattr(entity, "_ct_energy_source", False)
        or section == APP_SECTION_CT_STAT
        or section.startswith(f"{APP_SECTION_CT_STAT}_")
    ) and not token[8]:
        return None
    owner = {
        key: value
        for key, value in zip(
            (
                "parent_serial",
                "system_id",
                "system_serial",
                "ct_device_id",
                "ct_serial",
            ),
            token[5:],
            strict=True,
        )
        if isinstance(value, str) and value
    }
    if hasattr(entity, "_plug_sn"):
        leaf = getattr(entity, "_own_lifetime_leaf_serial", None)
        if leaf is None:
            return None
        owner["plug_serial"] = leaf
    return owner


def _energy_owner_attributes(
    entity: object,
    *,
    owner_context: tuple[dict[str, str] | None] | None = None,
) -> dict[str, Any]:
    """Persist ownership, optionally sharing this synchronous apply's validation."""
    owner = (
        _energy_owner_identity(entity) if owner_context is None else owner_context[0]
    )
    if owner is None:
        return {}
    attrs = {
        "energy_owner": owner,
        "source_unique_id": getattr(entity, "unique_id", None),
    }
    if (
        hasattr(entity, "_plug_sn")
        and getattr(entity, "_reset_period", None) == DATE_TYPE_DAY
    ):
        attrs["energy_day"] = _period_start(DATE_TYPE_DAY).date().isoformat()
    return attrs


def _energy_restore_identity_matches(entity: object, state: object) -> bool:
    """Reject unidentified saved energy or energy from another physical owner."""
    attrs = getattr(state, "attributes", None)
    current = _energy_owner_identity(entity)
    if not isinstance(attrs, Mapping) or current is None:
        return False
    saved = attrs.get("energy_owner")
    if (
        not isinstance(saved, Mapping)
        or saved.get("parent_serial") != current["parent_serial"]
        or attrs.get("source_unique_id") != getattr(entity, "unique_id", None)
        or saved.get("ct_device_id") != current.get("ct_device_id")
        or saved.get("plug_serial") != current.get("plug_serial")
    ):
        return False
    if (
        hasattr(entity, "_plug_sn")
        and getattr(entity, "_reset_period", None) == DATE_TYPE_DAY
        and attrs.get("energy_day") != _period_start(DATE_TYPE_DAY).date().isoformat()
    ):
        return False
    for key in ("system_id", "system_serial", "ct_serial"):
        previous = saved.get(key)
        if previous is not None and (not isinstance(previous, str) or not previous):
            return False
        if previous is not None and key in current and previous != current[key]:
            return False
    return True


def _main_battery_pack_lifetime(
    entity: JackerySensor,
    direction: str,
) -> float | None:
    """Preserve the head's lifetime identity until its own source is verified.

    A matching serial in the accessory pack list is not proof that inEgy/outEgy
    measures the independent internal battery. Aggregate/subtracted values
    would corrupt the existing TOTAL_INCREASING Recorder identity.
    """
    del entity, direction
    return None


class JackerySensor(JackeryEntity, SensorEntity):
    """Jackery sensor for the Jackery SolarVault integration."""

    _attr_has_entity_name = True
    entity_description: JackerySensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: JackerySensorDescription,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description
        self._portable_description = any(
            description is candidate for candidate in PORTABLE_SENSOR_DESCRIPTIONS
        )
        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
            and description.entity_category != EntityCategory.DIAGNOSTIC
        )
        self._cached_native_value: StateType = None
        self._cached_attrs: dict[str, Any] = {}
        self._cache_refresh_active = False

    @property
    def available(self) -> bool:
        """Keep Portable-only descriptions bound to their current family."""
        if self.entity_description.key in {"mac_address", "wifi_ip"} and not (
            self.coordinator.property_fields_match_family(
                self.device_id, self.entity_description.app_fields
            )
        ):
            return False
        if getattr(self, "_portable_description", False) and (
            self.coordinator.device_family(self.device_id) != "portable"
            or not self.coordinator.property_fields_match_family(
                self.device_id, self.entity_description.app_fields
            )
            or not _app_sensor_matches_model(
                self.coordinator, self.device_id, self.entity_description.key
            )
        ):
            return False
        return super().available

    def _refresh_cache(self) -> None:  # ruff: ignore[too-many-branches]
        """Prepare one coherent value-and-attributes state-write snapshot."""
        main_direction = {
            "main_battery_charge_energy": "charge",
            "main_battery_charge_energy_derived": "charge",
            "main_battery_discharge_energy": "discharge",
            "main_battery_discharge_energy_derived": "discharge",
        }.get(self.entity_description.key)
        if self.entity_description.device_class == SensorDeviceClass.ENERGY:
            _invalidate_own_lifetime_parent_cache(self)
        candidate = self.entity_description.value_fn(self)
        previous_value = (
            None
            if self.entity_description.key == "battery_to_ac_lifetime_energy"
            and self._cached_attrs.get("fallback") == "own_observed_energy_aggregation"
            else self._cached_native_value
        )
        invalid_pv_generation = (
            self.entity_description.key
            in {"pv_to_ac_lifetime_energy", "pv_to_grid_side_lifetime_energy"}
            and (pv_value := safe_float(candidate)) is not None
            and pv_value < 0
        )
        if invalid_pv_generation:
            # Preserve the raw property receipt; never publish a negative
            # physical PV lifetime counter as generated energy.
            candidate = None
        # Recorder power integration begins at first observation, not at the
        # battery's installation date. It cannot be the lifetime sensor state.
        # Clear even a previously published (and jitter-retained) estimate.
        if main_direction is not None:
            self._cached_native_value = (
                None
                if self.entity_description.key.endswith("_derived")
                else _main_battery_pack_lifetime(self, main_direction)
            )
            retained = False
        else:
            self._cached_native_value = guard_total_increasing_jitter(
                previous_value, candidate, self.entity_description
            )
            retained = self._cached_native_value is not candidate
        self._cached_attrs = self._source_attributes()
        if invalid_pv_generation:
            self._cached_attrs["invalid_negative_pv_generation"] = True
            if self._cached_native_value is None:
                self._cached_attrs["unknown_reason"] = "invalid_negative_pv_generation"
        if self.entity_description.device_class == SensorDeviceClass.ENERGY:
            self._cached_attrs.update(_energy_owner_attributes(self))
        if main_direction is not None:
            payload = self._payload
            section = (
                payload.get("main_battery_observed_energy")
                if isinstance(payload, dict)
                else None
            )
            row = section.get(main_direction) if isinstance(section, dict) else None
            if self._cached_native_value is None:
                self._cached_attrs["unknown_reason"] = (
                    "independent_head_source_unresolved"
                )
            if candidate is not None and isinstance(row, dict):
                # Preserve the valid *partial* evidence as diagnostic metadata,
                # never as a TOTAL_INCREASING lifetime since installation.
                self._cached_attrs.update({
                    "observed_partial_energy_kwh": candidate,
                    "estimated": True,
                    "partial_aggregation": True,
                    "estimate_method": "recorder_own_power_intervals",
                    "source_metric": row["source_metric"],
                    "source_entity_id": row["source_entity_id"],
                    "observed_since": row["observed_since"],
                    "observed_until": row["observed_until"],
                    "observed_seconds": row["observed_seconds"],
                })
        if self.entity_description.key == "battery_to_ac_lifetime_energy":
            # This exact directional lifetime identity has its own battery-stat
            # curve. Other head/pack/flow counters cannot borrow this evidence.
            observed = observed_period_energy_total(
                self._payload,
                f"{APP_SECTION_BATTERY_STAT}_total",
                APP_DEVICE_STAT_BATTERY_TO_AC,
                today=dt_util.now().date(),
            )
            if observed is not None:
                own_value, coverage = observed
                # These intervals start at their first observation, not at
                # installation. Never publish their sum as lifetime energy.
                self._cached_attrs.update({
                    "observed_partial_energy_kwh": own_value,
                    "partial_aggregation": True,
                    "estimate_method": "own_observed_energy_intervals",
                    "observed_coverage": coverage,
                })
        if retained:
            self._cached_attrs["lifetime_value_retained"] = True

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh the prepared value before the common entity update path."""
        if self._cache_refresh_active:
            self._refresh_cache()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime and activate the prepared sensor value cache."""
        self._refresh_cache()
        self._cache_refresh_active = True
        try:
            await super().async_added_to_hass()
        except Exception:
            self._cache_refresh_active = False
            raise

    async def async_will_remove_from_hass(self) -> None:
        """Stop serving the prepared cache after entity removal."""
        self._cache_refresh_active = False
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> StateType:
        """The entity's current value - delegates to description value_fn."""
        if getattr(self, "_cache_refresh_active", False):
            return getattr(self, "_cached_native_value", None)
        key = self.entity_description.key
        if key == "main_battery_charge_energy":
            return _main_battery_pack_lifetime(self, "charge")
        if key == "main_battery_discharge_energy":
            return _main_battery_pack_lifetime(self, "discharge")
        if key in {
            "main_battery_charge_energy_derived",
            "main_battery_discharge_energy_derived",
        }:
            # Recorder estimates describe partial observations only.
            return None
        return self.entity_description.value_fn(self)

    def _source_attributes(self) -> dict[str, Any]:
        """Build source diagnostics from the same coordinator snapshot as the value."""
        if (
            self.entity_description.key
            in {
                "main_battery_charge_energy",
                "main_battery_discharge_energy",
                "main_battery_charge_energy_derived",
                "main_battery_discharge_energy_derived",
            }
            and self.native_value is None
        ):
            return {
                "merged_raw_value": None,
                "http_raw_value": None,
                "unknown_reason": "independent_head_source_unresolved",
            }
        if self.entity_description.key in {
            "main_battery_charge_energy",
            "main_battery_discharge_energy",
        }:
            return {
                "source_section": PAYLOAD_BATTERY_PACKS,
                "source_key": (
                    FIELD_IN_EGY
                    if self.entity_description.key == "main_battery_charge_energy"
                    else FIELD_OUT_EGY
                ),
            }
        getter = self.entity_description.getter
        if getter is not None:
            merged_raw = getter(self.merged_properties)
            http_raw = getter(self._http_properties or {})
            attrs: dict[str, Any] = {
                "merged_raw_value": merged_raw,
                "http_raw_value": http_raw,
            }
            source_reader = getattr(self.coordinator, "layer_source_payloads", None)
            fields = self.entity_description.app_fields
            if callable(source_reader) and fields:
                layers = source_reader(
                    self._device_id, PAYLOAD_PROPERTIES, fields=fields
                )
                if isinstance(layers, dict) and layers:
                    attrs["source_unique_id"] = self.unique_id
                    attrs.update(
                        self.layer_source_attributes(
                            PAYLOAD_PROPERTIES, *fields, receipts=layers
                        )
                    )
                    attrs["layer_raw_values"] = {
                        source: getter(receipt["values"])
                        for source, receipt in layers.items()
                    }
                    # HTTP SystemBody/shadow fields have a valid HTTP receipt
                    # even when /device/property omits that same field.
                    receipt_http = attrs["layer_raw_values"].get("http")
                    if receipt_http is not None:
                        http_raw = receipt_http
                        attrs["http_raw_value"] = receipt_http
            if merged_raw is not None and merged_raw != http_raw:
                attrs["live_source_overrides_http"] = True
            return self._with_portable_solar_icon(attrs, merged_raw)
        try:
            value = self.entity_description.value_fn(self)
            return self._with_portable_solar_icon({"merged_raw_value": value}, value)
        except Exception:
            _LOGGER.exception(
                "Unable to calculate merged diagnostic value for %s",
                self.entity_id,
            )
            return {}

    def _with_portable_solar_icon(
        self, attrs: dict[str, Any], value: object
    ) -> dict[str, Any]:
        if (
            self.entity_description.key == "system_status_switch"
            and self.coordinator.device_family(self.device_id) == "portable"
            and self.coordinator.property_fields_match_family(
                self.device_id, self.entity_description.app_fields
            )
            and isinstance(value, int)
            and not isinstance(value, bool)
        ):
            attrs["portable_solar_voltage_icon"] = {
                1: "high",
                2: "low",
                3: "high_and_low",
            }.get(value, "hidden")
        return attrs

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose source diagnostics from the prepared state-write snapshot."""
        if self._cache_refresh_active:
            return self._cached_attrs
        return self._source_attributes()


class JackeryHomeSensor(JackerySensor):
    """Sensor restricted to devices identified as Home."""

    @property
    def available(self) -> bool:
        """Hide stale Home values if the device family changes."""
        return (
            self.coordinator.device_family(self.device_id) == "home"
            and super().available
        )


class JackeryAiEmsStatusSensor(JackeryHomeSensor):
    """System-owned AIEMS status without exposing navigation URLs."""

    _unrecorded_attributes = AIEMS_STATUS_FIELDS
    _BOOLEAN_FIELDS = frozenset({
        "hasCt",
        "isCtOnline",
        "isDataAuthorized",
        "isAiModeAvailable",
        "isManualOverride",
        "isSetLocation",
        "isPriceError",
    })
    _INTEGER_FIELDS = AIEMS_STATUS_FIELDS - _BOOLEAN_FIELDS - {"aiModeSocLimits"}

    @property
    def available(self) -> bool:
        """Reject status that belongs to an older system association."""
        return (
            self.coordinator.aiems_status_is_current(self.device_id)
            and super().available
        )

    def _source_attributes(self) -> dict[str, Any]:
        if not self.coordinator.aiems_status_is_current(self.device_id):
            return {}
        status = self.payload.get(PAYLOAD_AIEMS_MODE_STATUS)
        if not isinstance(status, dict):
            return {}
        attributes: dict[str, Any] = {}
        for status_field in self._BOOLEAN_FIELDS:
            value = status.get(status_field)
            if status_field in status and (value is None or isinstance(value, bool)):
                attributes[status_field] = value
        for status_field in self._INTEGER_FIELDS:
            value = status.get(status_field)
            if status_field in status and (
                value is None
                or (isinstance(value, int) and not isinstance(value, bool))
            ):
                attributes[status_field] = value
        limits = status.get("aiModeSocLimits")
        if "aiModeSocLimits" in status and limits is None:
            attributes["aiModeSocLimits"] = None
        elif isinstance(limits, dict):
            clean_limits: dict[str, Any] = {}
            for mode in ("balanced", "arbitrage", "envProtection"):
                value = limits.get(mode)
                if mode in limits and value is None:
                    clean_limits[mode] = None
                elif isinstance(value, dict):
                    clean_limits[mode] = {
                        key: value[key]
                        for key in ("min", "max")
                        if key in value
                        and (
                            value[key] is None
                            or (
                                isinstance(value[key], int)
                                and not isinstance(value[key], bool)
                            )
                        )
                    }
            attributes["aiModeSocLimits"] = clean_limits
        return attributes


class JackeryAiEmsPredictionSensor(JackeryHomeSensor):
    """System-owned AIEMS forecast and operation plan."""

    _unrecorded_attributes = AIEMS_PREDICTION_FIELDS
    _NUMERIC_SERIES = ("loadData", "pvData", "priceData", "socData")
    _PLAN_STRINGS = (
        "startTime",
        "endTime",
        "primaryLabel",
        "secondaryLabel",
        "reasonText",
        "segmentType",
    )
    _PLAN_INTEGERS = ("startEpochSecond", "endEpochSecond", "mode")

    @property
    def available(self) -> bool:
        """Show forecasts only for their currently bound system."""
        return (
            self.coordinator.aiems_prediction_is_current(self.device_id)
            and super().available
        )

    def _source_attributes(self) -> dict[str, Any]:
        if not self.coordinator.aiems_prediction_is_current(self.device_id):
            return {}
        prediction = self.payload.get(PAYLOAD_AIEMS_ENERGY_PREDICTION)
        if not isinstance(prediction, dict):
            return {}
        attributes: dict[str, Any] = {}
        timestamps = prediction.get("timestamps")
        if isinstance(timestamps, list) and all(
            isinstance(item, str) for item in timestamps
        ):
            attributes["timestamps"] = list(timestamps)
        currency = prediction.get("currency")
        if "currency" in prediction and (currency is None or isinstance(currency, str)):
            attributes["currency"] = currency
        for field_name in self._NUMERIC_SERIES:
            values = prediction.get(field_name)
            if values is None and field_name in prediction:
                attributes[field_name] = None
            elif isinstance(values, list) and all(
                value is None
                or (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and safe_float(value) is not None
                )
                for value in values
            ):
                attributes[field_name] = list(values)
        plan = prediction.get("operationPlan")
        if plan is None and "operationPlan" in prediction:
            attributes["operationPlan"] = None
        elif isinstance(plan, list):
            clean_plan: list[dict[str, Any]] = []
            for item in plan:
                if not isinstance(item, dict):
                    continue
                clean = {
                    field: item[field]
                    for field in self._PLAN_STRINGS
                    if field in item
                    and (item[field] is None or isinstance(item[field], str))
                }
                clean.update({
                    field: item[field]
                    for field in self._PLAN_INTEGERS
                    if field in item
                    and (
                        item[field] is None
                        or (
                            isinstance(item[field], int)
                            and not isinstance(item[field], bool)
                        )
                    )
                })
                power = item.get("powerW")
                if "powerW" in item and (
                    power is None
                    or (
                        isinstance(power, (int, float))
                        and not isinstance(power, bool)
                        and safe_float(power) is not None
                    )
                ):
                    clean["powerW"] = power
                clean_plan.append(clean)
            attributes["operationPlan"] = clean_plan
        return attributes


class JackeryPortableCtStatisticsSensor(JackerySensor):
    """App-labelled statistics for one independently bound Portable CT."""

    _unrecorded_attributes = frozenset({"total", "l1", "l2", "app_unit"})

    @property
    def available(self) -> bool:
        """Expose statistics only for the currently bound Portable CT."""
        return (
            self.coordinator.portable_ct_statistics_is_current(self.device_id)
            and super().available
        )

    def _source_attributes(self) -> dict[str, Any]:
        if not self.coordinator.portable_ct_statistics_is_current(self.device_id):
            return {}
        statistics = self.payload.get(PAYLOAD_PORTABLE_CT_STATISTICS)
        if not isinstance(statistics, dict):
            return {}
        return {
            **{
                field: statistics[field]
                for field in ("total", "l1", "l2")
                if field in statistics
            },
            "app_unit": "kWh",
        }


class JackeryPortableSensor(JackerySensor):
    """Sensor restricted to its supported Portable models."""

    @property
    def available(self) -> bool:
        """Hide a stale value after the device family or model changes."""
        return (
            self.coordinator.device_family(self.device_id) == "portable"
            and self.coordinator.property_fields_match_family(
                self.device_id, self.entity_description.app_fields
            )
            and _app_sensor_matches_model(
                self.coordinator, self.device_id, self.entity_description.key
            )
            and super().available
        )


class JackeryPortableStrategySensor(JackeryPortableSensor):
    """Opt-in Portable plan data bound to the reporting device."""

    _unrecorded_attributes = frozenset({"plans", "current_plan"})

    @property
    def _strategy_field(self) -> str:
        return (
            "cds" if self.entity_description.key == "portable_strategy_plans" else "cep"
        )

    @property
    def available(self) -> bool:
        """Reject plans without a current device receipt."""
        return (
            self.coordinator.strategy_field_is_current(
                self.device_id, self._strategy_field
            )
            and super().available
        )

    def _source_attributes(self) -> dict[str, Any]:
        if not self.coordinator.strategy_field_is_current(
            self.device_id, self._strategy_field
        ):
            return {}
        strategy = self.payload.get(PAYLOAD_ELECTRICITY_STRATEGY)
        if not isinstance(strategy, dict) or self._strategy_field not in strategy:
            return {}
        key = "plans" if self._strategy_field == "cds" else "current_plan"
        return {key: deepcopy(strategy[self._strategy_field])}


class JackeryPortableWifiScanSensor(JackeryPortableSensor):
    """Opt-in Portable Wi-Fi scan results without publishing names as state."""

    _unrecorded_attributes = frozenset({"networks"})

    def _source_attributes(self) -> dict[str, Any]:
        names = _portable_wifi_names(self.payload)
        return {"networks": names} if names is not None else {}


class JackeryBoxSensor(JackerySensor):
    """Sensor restricted to devices identified as Box."""

    @property
    def available(self) -> bool:
        """Hide stale Box values if the device family changes."""
        return (
            self.coordinator.device_family(self.device_id) == "box"
            and (
                not self.entity_description.key.startswith("box_fault_")
                or self.coordinator.box_fault_field_is_current(
                    self.device_id,
                    self.entity_description.family_fields[0].split(".")[-1],
                )
            )
            and self.coordinator.property_fields_match_family(
                self.device_id,
                self.entity_description.family_fields
                or self.entity_description.app_fields,
            )
            and _app_sensor_matches_model(
                self.coordinator, self.device_id, self.entity_description.key
            )
            and super().available
        )

    def _source_attributes(self) -> dict[str, Any]:
        """Expose the battery list under its own AC module."""
        attributes = super()._source_attributes()
        if self.entity_description.key in {"box_ac1_packs", "box_ac2_packs"}:
            channel = "ac1" if self.entity_description.key == "box_ac1_packs" else "ac2"
            ac = self.merged_properties.get(channel)
            if isinstance(ac, dict) and safe_int(ac.get("bi")) not in {None, 0}:
                attributes["raw_ac_fields"] = deepcopy(ac)
                packs = ac.get("bp")
                if isinstance(packs, list):
                    attributes["battery_packs"] = deepcopy(packs)
        return attributes


class JackeryBoxWifiScanSensor(JackeryBoxSensor):
    """Opt-in Box scan results bound to their owning BLE session."""

    _unrecorded_attributes = frozenset({"networks"})

    @property
    def available(self) -> bool:
        """Reject scans from another Box identity or BLE session."""
        return (
            self.coordinator.box_wifi_scan_is_current(self.device_id)
            and super().available
        )

    def _source_attributes(self) -> dict[str, Any]:
        if not self.coordinator.box_wifi_scan_is_current(self.device_id):
            return {}
        names = _portable_wifi_names(self.payload)
        return {"networks": names} if names is not None else {}


class JackeryBoxStrategySensor(JackeryBoxSensor):
    """Opt-in Box plan data bound to the reporting device identity."""

    _unrecorded_attributes = frozenset({"plans", "current_plan"})

    @property
    def _strategy_field(self) -> str:
        return "cds" if self.entity_description.key == "box_strategy_plans" else "cep"

    @property
    def available(self) -> bool:
        """Reject plans without a current Box identity receipt."""
        return (
            self.coordinator.strategy_field_is_current(
                self.device_id, self._strategy_field
            )
            and super().available
        )

    def _source_attributes(self) -> dict[str, Any]:
        if not self.coordinator.strategy_field_is_current(
            self.device_id, self._strategy_field
        ):
            return {}
        strategy = self.payload.get(PAYLOAD_ELECTRICITY_STRATEGY)
        if not isinstance(strategy, dict) or self._strategy_field not in strategy:
            return {}
        key = "plans" if self._strategy_field == "cds" else "current_plan"
        return {key: deepcopy(strategy[self._strategy_field])}


_PeriodResolution = tuple[list[float | None] | None, float | None, float | None]
_PeriodResolutionCache = dict[tuple[int, str, str], _PeriodResolution]


@dataclass(frozen=True, slots=True)
class _CachedPeriodResolution:
    """Retain a pure result without sharing mutable values across batches."""

    signature: str
    values: tuple[float | None, ...] | None
    chart_series_sum: float | None
    server_total: float | None


def _period_source_signature(source: dict[str, Any]) -> str | None:
    """Reuse only sources whose JSON round trip preserves their structure."""
    try:
        signature = json.dumps(source, sort_keys=True, separators=(",", ":"))
        return signature if json.loads(signature) == source else None
    except (TypeError, ValueError, RecursionError):
        return None


@dataclass(frozen=True, slots=True)
class _StatRefreshContext:
    """Immutable event-loop snapshot consumed by the executor batch."""

    payload: dict[str, Any]
    local_now: datetime
    local_today: date
    local_daily_raw: tuple[float, str] | None
    local_period_raw: tuple[float, str] | None
    local_timezone: tzinfo = dt_util.DEFAULT_TIME_ZONE
    local_period_attributes: dict[str, Any] = field(default_factory=dict)
    comparison_cache: _StatInputComparisonCache | None = field(
        default=None, compare=False, repr=False
    )


@dataclass(frozen=True, slots=True)
class _StatCacheSnapshot:
    """Computed stat state applied atomically back on the event loop."""

    native_value: StateType
    attrs: dict[str, Any]
    source_section: str
    last_reset: datetime | None = None


def _legacy_ct_day_only_period_value(
    section: str, attributes: Mapping[str, Any]
) -> bool:
    """Reject old CT period fallbacks proven only by a single daily counter."""
    coverage = attributes.get("observed_coverage")
    return (
        section.startswith(f"{APP_SECTION_CT_STAT}_")
        and attributes.get("fallback") == "own_observed_energy_aggregation"
        and isinstance(coverage, Mapping)
        and coverage.get("sources") == [PAYLOAD_LOCAL_DAILY_ENERGY]
    )


@dataclass(slots=True)
class _PeriodRefreshState:
    """Mutable period-source selection shared by ordered fallback phases."""

    section: str
    stat_key: str
    source: dict[str, Any]
    series_key: str | None
    values: list[float | None] | None
    chart_series_sum: float | None
    server_total: float | None
    raw: float | None
    cached_source_section: str
    snapshot_last_reset: datetime | None
    day_curve_total: float | None = None
    day_curve_fallback: bool = False
    period_zero_sources: set[str] = field(default_factory=set)
    local_daily_metric: str | None = None
    day_bucket_fallback: str | None = None
    open_week_fallback: str | None = None
    open_period_fallback: str | None = None


@dataclass(slots=True)
class _NonPeriodRefreshState:
    """Mutable scalar-stat selection and boundary status."""

    section: str
    stat_key: str
    source: dict[str, Any]
    raw: object
    day_bucket_fallback: str | None = None
    cached_source_section: str = ""
    stale: bool = False
    future: bool = False


_NATIVE_OWN_ENERGY_SOURCES: Final = {
    "total_generation": (APP_SECTION_PV_STAT, APP_STAT_TOTAL_SOLAR_ENERGY),
    "battery_charge_energy": (APP_SECTION_BATTERY_STAT, APP_STAT_TOTAL_CHARGE),
    "battery_discharge_energy": (APP_SECTION_BATTERY_STAT, APP_STAT_TOTAL_DISCHARGE),
    "today_feed_in_energy": (APP_SECTION_PV_STAT, APP_STAT_TOTAL_SOLAR_ENERGY),
    "today_grid_import_energy": (APP_SECTION_HOME_STAT, APP_STAT_TOTAL_IN_GRID_ENERGY),
    "today_home_load_energy": (PAYLOAD_HOME_TRENDS, APP_STAT_TOTAL_HOME_ENERGY),
    "today_battery_energy": (APP_SECTION_BATTERY_STAT, APP_STAT_TOTAL_DISCHARGE),
    "today_battery_charge_energy": (APP_SECTION_BATTERY_STAT, APP_STAT_TOTAL_CHARGE),
    "device_today_ongrid_to_battery": (
        APP_SECTION_BATTERY_STAT,
        APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    ),
    "device_today_pv_to_battery": (
        APP_SECTION_BATTERY_STAT,
        APP_DEVICE_STAT_PV_TO_BATTERY,
    ),
    "device_today_battery_to_ongrid": (
        APP_SECTION_BATTERY_STAT,
        APP_DEVICE_STAT_BATTERY_TO_GRID,
    ),
}


class JackeryStatSensor(JackeryEntity, RestoreSensor):
    """Sensor sourced from the statistic / price section of the payload."""

    @staticmethod
    def _derived_home_energy_fallback_enabled() -> bool:
        """The whether derived home-energy fallback is enabled."""
        return True

    # Performance contract: Home Assistant evaluates native_value, last_reset
    # and extra_state_attributes on every state write.

    entity_description: JackeryStatSensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: JackeryStatSensorDescription,
    ) -> None:
        """Implementation details.

        Initialize a JackeryStatSensor entity using the coordinator state and a
        statistic description.

        Sets entity registry enablement, infers the reset period (day/week/month/year)
        and enforces TOTAL state class for period totals, and prepares per-update caches
        and initial source metadata exposed by native_value and extra_state_attributes.

        Parameters:
            coordinator (JackerySolarVaultCoordinator): Coordinator providing device
            payloads and update callbacks.
            device_id (str): Unique device identifier used to scope entity unique_id
            and device registry linkage.
            description (JackeryStatSensorDescription): Sensor description that
            supplies stat key, source section, transforms, and optional reset_period.
        """
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description
        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
            and description.entity_category != EntityCategory.DIAGNOSTIC
        )
        self._reset_period = _period_from_stat_description(description)
        if self._reset_period is not None:
            # All period totals (day/week/month/year) reset at their period
            # boundary and carry a matching ``last_reset``, so TOTAL is correct:
            # HA's recorder compiles a clean long-term statistic under the
            # ``sensor.xxx`` id. This no longer collides with the external
            # ``jackery_solarvault:`` statistics — the integration stopped
            # writing ``sensor.xxx`` stats itself (coordinator ~11308), so the
            # recorder-compiled ``sensor.xxx`` series and the external
            # ``jackery_solarvault:`` series are independent. Reverted
            # 2026-07-18: an earlier state_class=None on week/month/year stripped
            # long-term statistics from these sensors (HA repair: "no longer has
            # a state class"); backup_current shipped these as TOTAL.
            self._attr_state_class = SensorStateClass.TOTAL
        # Per-update snapshot. The first async-added callback queues the same
        # shared background batch used by later coordinator updates.
        self._cached_native_value: StateType = None
        self._cached_attrs: dict[str, Any] = {
            "source_section": description.section,
            "source_key": description.stat_key,
        }
        self._cached_source_section = description.section
        self._cache_generation = 0
        self._cache_lifecycle_epoch = 0
        self._cache_refresh_active = False
        self._cache_initializing = False
        self._passive_cache_unsubscribe: Callable[[], None] | None = None
        self._passive_cache_callback: Callable[[], None] | None = None
        self._restored_lifetime_value: float | None = None

        self._local_day_snapshot: _StatCacheSnapshot | None = None

    @property
    def last_reset(self) -> datetime | None:
        """Implementation details.

        Return the local period boundary (last_reset) for the statistic based on the
        source's request begin date.

        When a reset period is set, use the source section's request metadata
        `begin_date` to compute the timezone-aware local midnight that marks the period
        start. If the source data is stale or from the future, or if no valid
        `begin_date` is available or parseable, fall back to the local period start
        computed from the current wall clock. This ensures the entity's `last_reset`
        only advances when the server-side period data is actually present.

        Returns:
            datetime | None: Timezone-aware local midnight for the period start, or
            `None` when no reset period is configured.
        """
        # last_reset is only valid on a TOTAL sensor. Non-period sensors (no
        # reset period) and the week/month/year totals (state_class=None, since
        # the external ``jackery_solarvault:`` statistics own their long-term
        # series) must return None: HA raises ValueError in
        # SensorEntity.state_attributes for a non-TOTAL sensor that sets a
        # last_reset, which otherwise aborts every state write and leaves the
        # entity permanently unavailable.
        # Check both the instance attribute and the description's state_class
        # (for cases where __init__ wasn't called, e.g., in tests).
        state_class = getattr(self, "_attr_state_class", None)
        if state_class is None:
            state_class = getattr(self.entity_description, "state_class", None)
        if self._reset_period is None or state_class != SensorStateClass.TOTAL:
            return None
        if self._reset_period == DATE_TYPE_DAY and self._period_data_offset() < 0:
            return _period_start(self._reset_period, self._local_timezone())
        if self._period_data_offset() > 0:
            return _period_start(self._reset_period, self._local_timezone())
        # Prefer the begin_date stamped on the source by the coordinator
        # (`source[APP_REQUEST_META][APP_REQUEST_BEGIN_DATE]`), fall
        # back to wall-clock period start for sources that have no
        # request metadata (legacy code paths).
        begin_iso = self._period_begin_from_meta()
        if begin_iso is None:
            return _period_start(self._reset_period, self._local_timezone())
        try:
            begin_date = date.fromisoformat(begin_iso)
        except ValueError:
            return _period_start(self._reset_period, self._local_timezone())
        # Snap the request's begin_date to the boundary of the period that
        # contains it. Jackery sometimes answers a week/month/year request with
        # ``beginDate = endDate = today`` (docs/DATA_SOURCE_PRIORITY.md warns
        # about exactly that shape). Using the raw date then moved last_reset
        # every single day, so Home Assistant started a fresh period daily and
        # the week total appeared to reset. A true boundary is idempotent here.
        return _period_start_at(
            self._reset_period,
            datetime(
                begin_date.year,
                begin_date.month,
                begin_date.day,
                tzinfo=self._local_timezone(),
            ),
        )

    def _compute_period_start(self, reset_period: StatResetPeriod) -> datetime:
        """Compute the period start for a given reset period.

        This method is used by tests to precompute period boundaries.
        Delegates to the module-level _period_start function.
        """
        return _period_start(reset_period, self._local_timezone())

    def _local_timezone(self) -> tzinfo:
        """Get the Home Assistant local timezone for period sensors.

        Returns:
            timezone (Any): Timezone object from Home Assistant configuration; falls
            back to Home Assistant's default timezone when the configured value is
            unavailable.
        """
        hass = getattr(self, "hass", None) or self.coordinator.hass
        timezone = dt_util.get_time_zone(hass.config.time_zone)
        return timezone or dt_util.DEFAULT_TIME_ZONE

    def _local_today(self) -> date:
        """Implementation details.

        Get the current local date in the Home Assistant timezone for app chart
        lookups.

        Returns:
            date: Local date in the configured Home Assistant timezone.
        """
        return dt_util.now(self._local_timezone()).date()

    def _period_begin_from_meta(
        self,
        source_section: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str | None:
        """Get the API-request `begin_date` stamped on the sensor's source.

        Returns:
            str: The `begin_date` string from the source's request metadata when present
            and valid, or `None` if the metadata is missing, not a dict, or the begin
            date is absent/invalid.
        """
        section = (
            self._cached_source_section if source_section is None else source_section
        )
        source = self._source_for_section(section, payload)
        request = source.get(APP_REQUEST_META)
        if not isinstance(request, dict):
            return None
        begin = request.get(APP_REQUEST_BEGIN_DATE) or request.get(
            APP_REQUEST_BEGIN_DATE_ALT
        )
        if not isinstance(begin, str) or not begin:
            return None
        return begin

    def _period_data_offset(
        self,
        source_section: str | None = None,
        payload: dict[str, Any] | None = None,
        local_timezone: tzinfo | None = None,
        local_now: datetime | None = None,
    ) -> int:
        """Return :func:`guards.period_data_offset` for this sensor's period."""
        if self._reset_period is None:
            return 0
        wall_clock_start = (
            _period_start_at(self._reset_period, local_now)
            if local_now is not None
            else _period_start(
                self._reset_period,
                local_timezone or self._local_timezone(),
            )
        )
        return period_data_offset(
            self._period_begin_from_meta(source_section, payload),
            wall_clock_start.date(),
        )

    def _source_for_section(
        self,
        section: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:  # flat section→source dispatch; clearest as-is
        """Implementation details.

        Return the coordinator source dictionary corresponding to a payload section
        name.

        Parameters:
                section (str): The payload section key to resolve (e.g., price,
                statistic, trends).

        Returns:
                dict[str, Any]: The dict storing data for the requested section, or an
                empty dict if no usable source is available.
        """
        if payload is not None:
            source = payload.get(section)
        else:
            known_sources = {
                PAYLOAD_PRICE: self._price,
                PAYLOAD_DEVICE_STATISTIC: self._device_statistic,
                PAYLOAD_PV_TRENDS: self._pv_trends,
                PAYLOAD_HOME_TRENDS: self._home_trends,
                PAYLOAD_BATTERY_TRENDS: self._battery_trends,
                PAYLOAD_STATISTIC: self._statistic,
            }
            source = known_sources.get(section)
            if source is None:
                source = self._payload.get(section)
        if not isinstance(source, dict):
            return {}
        if section == PAYLOAD_STATISTIC and isinstance(
            source.get("_http_day_receipt"), dict
        ):
            owner_guard = getattr(
                self.coordinator, "_http_day_source_owner_is_current", None
            )
            if not callable(owner_guard) or not owner_guard(
                self._device_id, section, source
            ):
                return {}
        day_source = (
            getattr(self, "_reset_period", None) == DATE_TYPE_DAY
            or section == PAYLOAD_DEVICE_STATISTIC
            or (
                section == PAYLOAD_STATISTIC
                and self.entity_description.stat_key
                in {
                    "todayLoad",
                    "todayGeneration",
                    "todayBatteryChg",
                    "todayBatteryDisChg",
                }
            )
        )
        if day_source and section != APP_SECTION_TODAY_ENERGY:
            today = getattr(
                self.coordinator, "_local_today", lambda: dt_util.now().date()
            )()
            if not current_day_http_source(source, section, today):
                return {}
            if section in {PAYLOAD_DEVICE_STATISTIC, PAYLOAD_STATISTIC}:
                validator = getattr(
                    self.coordinator, "_http_day_source_is_current", None
                )
                if not callable(validator) or not validator(
                    self._device_id, section, source
                ):
                    return {}
            source = current_day_http_values(source, section, today)
        return source

    def _current_day_bucket_from_period_chart(
        self,
        section: str,
        stat_key: str,
        *,
        payload: dict[str, Any] | None = None,
        today: date | None = None,
    ) -> tuple[float, str, dict[str, Any]] | None:
        """Implementation details.

        Derive today's metric from a week or month chart when the day-period endpoint
        has no data.

        Returns:
            tuple: `(value, source_section, source_dict)` where `value` is the day's
            numeric metric,
            `source_section` is the chart section used (e.g., `"<prefix>_week"`), and
            `source_dict` is the
            corresponding source payload dictionary; `None` when the function is not
            applicable or no
            suitable week/month bucket contains today's value.
        """
        if self._reset_period != DATE_TYPE_DAY:
            return None
        prefix = _day_section_prefix(section)
        if prefix is None:
            return None
        today = today or self._local_today()
        for date_type in (DATE_TYPE_MONTH, DATE_TYPE_WEEK):
            candidate_section = f"{prefix}_{date_type}"
            candidate_source = self._source_for_section(candidate_section, payload)
            value = self._dated_chart_values(
                candidate_source,
                candidate_section,
                stat_key,
            ).get(today)
            if value is not None:
                return value, candidate_section, candidate_source
        return None

    @cached_property
    def _day_curve_energy_memo(
        self,
    ) -> dict[tuple[str, str], tuple[tuple[object, ...], float | None]]:
        """Keep only the latest pure curve total for each declared source/key."""
        return {}

    def _current_day_energy_with_curve(  # ruff: ignore[too-many-arguments] - own metric and explicit clock keep period boundaries intact
        self,
        prefix: str,
        stat_key: str,
        *,
        payload: dict[str, Any],
        today: date,
        local_daily_raw: tuple[float, str] | None,
        now: datetime | None,
    ) -> float | None:
        """Retain the larger live counter or unchanged same-minute curve total."""
        value = local_daily_raw[0] if local_daily_raw is not None else None
        if stat_key in {
            APP_DEVICE_STAT_ONGRID_TO_BATTERY,
            APP_DEVICE_STAT_BATTERY_TO_GRID,
        }:
            # These directional energy totals use their verified own counter;
            # a W chart is not a daily kWh counter for this contract.
            return (
                value
                if local_daily_raw is not None and local_daily_raw[1] == stat_key
                else None
            )
        for day_section in (prefix, f"{prefix}_{DATE_TYPE_DAY}"):
            source = self._source_for_section(day_section, payload)
            signature = _period_source_signature(source) if now is not None else None
            # Curve integration clips samples by local minute. Include the full
            # source and calendar/zone boundary; never memoize a live counter.
            token = (
                signature,
                today,
                now.replace(second=0, microsecond=0).isoformat() if now else None,
                now.fold if now else None,
                now.tzinfo if now else None,
            )
            memo_key = (day_section, stat_key)
            cached = self._day_curve_energy_memo.get(memo_key)
            if signature is not None and cached is not None and cached[0] == token:
                measured = cached[1]
            else:
                points = day_power_energy_points(
                    source,
                    day_section,
                    stat_key,
                    bucket_minutes=60,
                    today=today,
                    now=now,
                    keep_curve_over_smaller_scalar=True,
                )
                points = [
                    point
                    for point in points
                    if (
                        point.start_date.date()
                        if isinstance(point.start_date, datetime)
                        else point.start_date
                    )
                    == today
                ]
                measured = (
                    round(sum(point.value for point in points), 5) if points else None
                )
                if signature is not None:
                    self._day_curve_energy_memo[memo_key] = (token, measured)
            if measured is not None:
                return measured if value is None else max(value, measured)
        return value

    def _current_open_week_from_month_chart(  # ruff: ignore[too-many-locals, too-many-arguments] - period and current-day evidence use the same explicit clock
        self,
        section: str,
        stat_key: str,
        *,
        payload: dict[str, Any],
        today: date,
        local_daily_raw: tuple[float, str] | None,
        now: datetime | None = None,
    ) -> tuple[float, str, dict[str, Any], bool] | None:
        """Build the current open week from corroborated daily kWh buckets.

        Jackery can return a positive but stale week total, not only an empty
        placeholder.  Cross-check the week and month views day by day, use a
        verified historical day where available, and let the same-metric local
        lifetime delta replace only today's lagging bucket.  Without a verified
        complete day, a zero needs two distinct period/day observations.
        """
        if self._reset_period != DATE_TYPE_WEEK:
            return None
        suffix = f"_{DATE_TYPE_WEEK}"
        if not section.endswith(suffix):
            return None
        month_section = f"{section[: -len(suffix)]}_{DATE_TYPE_MONTH}"
        week_source = self._source_for_section(section, payload)
        month_source = self._source_for_section(month_section, payload)
        week_start = today - timedelta(days=today.weekday())
        verified_days = self._source_for_section(
            PAYLOAD_VERIFIED_DAY_STATISTICS,
            payload,
        )
        prefix = section[: -len(suffix)]
        local_value = self._current_day_energy_with_curve(
            prefix,
            stat_key,
            payload=payload,
            today=today,
            local_daily_raw=local_daily_raw,
            now=now,
        )
        week_values = self._dated_chart_values(week_source, section, stat_key)
        month_values = self._dated_chart_values(month_source, month_section, stat_key)
        total = 0.0
        skipped_days = False
        completed_days_verified = week_start < today
        day = week_start
        while day <= today:
            week_value = week_values.get(day)
            month_value = month_values.get(day)
            day_sources = verified_days.get(day.isoformat())
            prefix_totals = (
                day_sources.get(prefix) if isinstance(day_sources, dict) else None
            )
            verified_value = (
                safe_float(prefix_totals.get(stat_key))
                if isinstance(prefix_totals, dict)
                else None
            )
            if day < today and verified_value is not None and verified_value >= 0:
                total += verified_value
                day += timedelta(days=1)
                continue
            if day < today:
                completed_days_verified = False
            observations = [
                value
                for value in (week_value, month_value)
                if value is not None and value >= 0
            ]
            if day == today and local_value is not None and local_value >= 0:
                observations.append(local_value)
            positive_observations = [value for value in observations if value > 0]
            if positive_observations:
                value = max(positive_observations)
            elif len(observations) >= _MIN_ZERO_CORROBORATION_SOURCES or (
                day == today
                and local_daily_raw is not None
                and local_daily_raw[0] == 0
                and local_value == 0
            ):
                # The local daily counter is admitted only with a full-day,
                # same-owner anchor; its measured zero is not a cloud placeholder.
                value = 0.0
            else:
                # Keep the known lower bound; one placeholder cannot certify
                # this day or erase another day's verified energy.
                skipped_days = True
                completed_days_verified = False
                day += timedelta(days=1)
                continue
            total += value
            day += timedelta(days=1)

        total = round(total, 5)
        return (
            None
            if skipped_days and total <= (local_value or 0)
            else (total, section, week_source, completed_days_verified)
        )

    def _current_open_month_or_year_with_local_day(
        self,
        section: str,
        stat_key: str,
        *,
        context: _StatRefreshContext,
        cloud_total: float | None,
        chart_series_sum: float | None = None,
    ) -> tuple[float, str, dict[str, Any], str, bool] | None:
        """Reconcile period buckets; lower only with complete verified past days."""
        period = self._reset_period
        if period not in {DATE_TYPE_MONTH, DATE_TYPE_YEAR}:
            return None
        suffix = f"_{period}"
        if not section.endswith(suffix):
            return None
        prefix = section[: -len(suffix)]
        source = self._source_for_section(section, context.payload)
        evidence_payload = {
            **context.payload,
            **{
                key: self._source_for_section(key, context.payload)
                for key in (
                    section,
                    f"{prefix}_{DATE_TYPE_MONTH}",
                    f"{prefix}_{DATE_TYPE_WEEK}",
                    PAYLOAD_VERIFIED_DAY_STATISTICS,
                )
            },
        }
        current_day_energy = self._current_day_energy_with_curve(
            prefix,
            stat_key,
            payload=context.payload,
            today=context.local_today,
            local_daily_raw=context.local_daily_raw,
            now=context.local_now,
        )
        rebuilt = rebuild_period_energy_total(
            evidence_payload,
            section,
            stat_key,
            today=context.local_today,
            local_day_kwh=current_day_energy,
        )
        if rebuilt is None:
            return None
        total, completed_days_verified = rebuilt
        # A previous day's verified PV energy is not proof of an open
        # month/year total on its own. Require either today's own observation,
        # a complete dated period, or the valid app period already selected.
        if (
            stat_key in _PV_CHANNEL_ENERGY_KEYS
            and current_day_energy is None
            and not completed_days_verified
            and cloud_total is None
            and not (chart_series_sum is not None and chart_series_sum > 0)
        ):
            # An invalid year scalar can still have independently valid
            # elapsed-month buckets. Rebuild already requires every invalid
            # elapsed month to be filled from its certified own days. Preserve
            # that measured lower bound, but never promote a lone old day.
            past_year_values = (
                effective_trend_series_values(source, section, stat_key)
                if period == DATE_TYPE_YEAR
                and is_device_year_period_section(source, section)
                else None
            )
            if not any(
                value is not None and value > 0
                for value in (past_year_values or [])[: context.local_today.month - 1]
            ):
                return None
        fallback = (
            "current_open_month_from_daily_buckets"
            if period == DATE_TYPE_MONTH
            else "current_open_year_from_month_and_daily_buckets"
        )
        if cloud_total is not None and not completed_days_verified:
            total = max(cloud_total, total)
        return total, section, source, fallback, completed_days_verified

    @cached_property
    def _period_resolution_memo(
        self,
    ) -> dict[tuple[str, str], _CachedPeriodResolution]:
        """Keep the latest decoded result for each declared source/key."""
        return {}

    def _resolve_cached_period_value(
        self,
        source: dict[str, Any],
        section: str,
        stat_key: str,
        period_cache: _PeriodResolutionCache,
    ) -> _PeriodResolution:
        """Reuse unchanged pure decoding while retaining full snapshot refreshes."""
        cache_key = (id(source), section, stat_key)
        if cache_key in period_cache:
            return period_cache[cache_key]
        series_key = _trend_series_key(section, stat_key)
        if (
            self._reset_period not in {None, DATE_TYPE_DAY}
            and series_key is not None
            and app_energy_unit_scale(source) is None
        ):
            period_cache[cache_key] = (None, None, None)
            return period_cache[cache_key]
        if (
            is_day_period_payload(source, section)
            or series_key is None
            or not isinstance(source.get(series_key), list)
        ):
            return self._resolve_period_value(source, section, stat_key, period_cache)
        signature = _period_source_signature(source)
        memo_key = (section, stat_key)
        cached = self._period_resolution_memo.get(memo_key)
        if (
            signature is not None
            and cached is not None
            and cached.signature == signature
        ):
            resolution = (
                list(cached.values) if cached.values is not None else None,
                cached.chart_series_sum,
                cached.server_total,
            )
        else:
            resolution = self._resolve_period_value(
                source, section, stat_key, period_cache
            )
            if signature is not None:
                values, chart_series_sum, server_total = resolution
                self._period_resolution_memo[memo_key] = _CachedPeriodResolution(
                    signature=signature,
                    values=tuple(values) if values is not None else None,
                    chart_series_sum=chart_series_sum,
                    server_total=server_total,
                )
        period_cache[cache_key] = resolution
        return resolution

    @cached_property
    def _dated_chart_memo(
        self,
    ) -> dict[tuple[str, str], tuple[str, Mapping[date, float]]]:
        """Keep the latest decoded dated daily buckets for each source/key."""
        return {}

    def _dated_chart_values(
        self,
        source: dict[str, Any],
        section: str,
        stat_key: str,
    ) -> Mapping[date, float]:
        """Reuse unchanged dated-bucket decoding; callers keep the calendar live."""
        series_key = _trend_series_key(section, stat_key)
        signature = (
            _period_source_signature(source)
            if series_key is not None and isinstance(source.get(series_key), list)
            else None
        )
        memo_key = (section, stat_key)
        cached = self._dated_chart_memo.get(memo_key)
        if signature is not None and cached is not None and cached[0] == signature:
            return cached[1]
        values = MappingProxyType(dated_daily_chart_values(source, section, stat_key))
        if signature is not None:
            self._dated_chart_memo[memo_key] = (signature, values)
        return values

    @staticmethod
    def _resolve_period_value(
        source: dict[str, Any],
        section: str,
        stat_key: str,
        period_cache: _PeriodResolutionCache,
    ) -> _PeriodResolution:
        """Materialize chart series, sum and server total in one pass.

        Replaces the previous triple call (``_trend_series_sum`` ->
        ``effective_period_total_value`` -> ``effective_trend_series_values``)
        in the per-update path. Each helper internally re-runs
        ``expanded_year_series_values`` for device-year sections, so calling
        them three times multiplied the cross-validation cost.
        """
        cache_key = (id(source), section, stat_key)
        if cache_key in period_cache:
            return period_cache[cache_key]
        if (
            _trend_series_key(section, stat_key) is not None
            and not is_day_period_payload(source, section)
            and app_energy_unit_scale(source) is None
        ):
            period_cache[cache_key] = (None, None, None)
            return period_cache[cache_key]
        values = effective_trend_series_values(source, section, stat_key)
        total_values = (
            effective_trend_series_values(source, section, APP_STAT_TOTAL_SOLAR_ENERGY)
            if stat_key in _PV_CHANNEL_ENERGY_KEYS
            else None
        )
        values, scalar_total, chart_series_sum = normalize_pv_period_energy(
            source, section, stat_key, values=values, total_values=total_values
        )
        server_total = scalar_total
        if is_device_year_period_section(source, section) and values is not None:
            server_total = complete_year_series_total(values, scalar_total)
        if _trend_series_key(section, stat_key) is not None:
            # Period arrays and scalars share their declared energy unit.
            # W-day scalars are already kWh; only their curve is integrated.
            scale = app_energy_unit_scale(source)
            if scale is not None:
                chart_series_sum = (
                    chart_series_sum * scale if chart_series_sum is not None else None
                )
                server_total = (
                    server_total * scale if server_total is not None else None
                )
        resolution = values, chart_series_sum, server_total
        period_cache[cache_key] = resolution
        return resolution

    def _pv_revenue_day_snapshot(
        self,
        context: _StatRefreshContext,
    ) -> _StatCacheSnapshot:
        """Keep App revenue separate from the current-tariff day estimate."""
        snapshot = self._refresh_non_period_cache(context)
        snapshot.attrs["revenue_basis"] = "app_reported"
        local = self._source_for_section(PAYLOAD_LOCAL_DAILY_ENERGY, context.payload)
        local_units = safe_float(local.get(APP_DEVICE_STAT_PV_ENERGY))
        if local_units is None or local_units < 0:
            return snapshot
        price = self._source_for_section(PAYLOAD_PRICE, context.payload)
        single_price = safe_float(price.get(FIELD_SINGLE_PRICE))
        tariff = (
            single_price
            if safe_int(price.get(FIELD_DYNAMIC_OR_SINGLE)) == _PRICE_MODE_SINGLE
            and single_price is not None
            and single_price > 0
            else None
        )
        energy_kwh = round(local_units / JACKERY_LIVE_ENERGY_UNITS_PER_KWH, 5)
        if local_units == 0 or tariff is not None:
            snapshot.attrs["revenue_derivation"] = {
                "estimated_value": round(energy_kwh * tariff, 2)
                if tariff is not None
                else 0.0,
                "energy_kwh": energy_kwh,
                "energy_source": PAYLOAD_LOCAL_DAILY_ENERGY,
                "price_per_kwh": tariff,
                "price_source": f"{PAYLOAD_PRICE}.{FIELD_SINGLE_PRICE}",
                "currency": first_nonblank_text(
                    price.get(FIELD_SINGLE_CURRENCY), price.get(FIELD_CURRENCY)
                ),
                "historical_tariff_verified": False,
            }
        return snapshot

    def _compact_today_snapshot(  # ruff:ignore[too-many-locals]  # ruff: ignore[too-many-branches, too-many-statements]
        self,
        context: _StatRefreshContext,
    ) -> _StatCacheSnapshot:
        """Resolve one reconciled compact-today value with source provenance."""
        description = self.entity_description

        def today_source(section: str) -> dict[str, Any]:
            source = self._source_for_section(section, context.payload)
            if not current_day_http_source(source, section, context.local_today):
                return {}
            if section in {PAYLOAD_DEVICE_STATISTIC, PAYLOAD_STATISTIC}:
                validator = getattr(
                    self.coordinator, "_http_day_source_is_current", None
                )
                if not callable(validator) or not validator(
                    self._device_id, section, source
                ):
                    return {}
            return current_day_http_values(source, section, context.local_today)

        compact_source = self._source_for_section(
            APP_SECTION_TODAY_ENERGY,
            context.payload,
        )
        provenance_map = compact_source.get(APP_TODAY_ENERGY_SOURCE_META)
        provenance = (
            provenance_map.get(description.stat_key)
            if isinstance(provenance_map, dict)
            else None
        )
        valid_provenance = (
            provenance
            if isinstance(provenance, dict)
            and isinstance(provenance.get("source_section"), str)
            and isinstance(provenance.get("source_key"), str)
            else None
        )

        observations: list[tuple[str, str, float, dict[str, Any]]] = []
        compact_value = safe_float(compact_source.get(description.stat_key))
        if compact_value is not None and compact_value >= 0:
            source_section = (
                cast("str", valid_provenance["source_section"])
                if valid_provenance is not None
                else APP_SECTION_TODAY_ENERGY
            )
            source_key = (
                cast("str", valid_provenance["source_key"])
                if valid_provenance is not None
                else description.stat_key
            )
            observation_source = (
                self._source_for_section(source_section, context.payload)
                if source_section == PAYLOAD_LOCAL_DAILY_ENERGY
                else today_source(source_section)
                if valid_provenance is not None
                and source_section != APP_SECTION_TODAY_ENERGY
                else compact_source
            )
            if source_section == APP_SECTION_TODAY_ENERGY:
                validator = getattr(
                    self.coordinator, "_http_day_source_is_current", None
                )
                usable = (
                    current_day_http_source(
                        compact_source, source_section, context.local_today
                    )
                    and callable(validator)
                    and validator(self._device_id, source_section, compact_source)
                    and description.stat_key
                    in current_day_http_values(
                        compact_source, source_section, context.local_today
                    )
                )
            elif source_section == PAYLOAD_LOCAL_DAILY_ENERGY:
                # The transport-neutral lifetime delta is not a dated
                # HTTP statistic. Validate its own raw counter in 0.01-kWh
                # units instead of rejecting it for missing HTTP metadata.
                raw_counter = safe_float(observation_source.get(source_key))
                usable = (
                    source_key == self._local_daily_metric_key()
                    and raw_counter is not None
                    and raw_counter >= 0
                    and round(raw_counter / JACKERY_LIVE_ENERGY_UNITS_PER_KWH, 5)
                    == round(compact_value, 5)
                )
            else:
                usable = bool(observation_source)
                if source_section in {PAYLOAD_DEVICE_STATISTIC, PAYLOAD_STATISTIC}:
                    usable = (
                        source_key in observation_source
                        and safe_float(observation_source[source_key]) == compact_value
                    )
            if usable:
                observations.append((
                    source_section,
                    source_key,
                    compact_value,
                    observation_source,
                ))

        for fallback_section, fallback_key in description.fallback_sources:
            fallback_source = today_source(fallback_section)
            fallback_value = safe_float(fallback_source.get(fallback_key))
            if fallback_value is None or fallback_value < 0:
                continue
            observations.append((
                fallback_section,
                fallback_key,
                fallback_value,
                fallback_source,
            ))

        selected: tuple[str, str, float, dict[str, Any]] | None = None
        positives = [item for item in observations if item[2] > 0]
        if positives:
            selected = max(positives, key=itemgetter(2))
        else:
            zeroes = [item for item in observations if item[2] == 0]
            if valid_provenance is not None and zeroes:
                selected = zeroes[0]
            elif len({item[0] for item in zeroes}) >= _MIN_ZERO_CORROBORATION_SOURCES:
                selected = zeroes[-1]

        if selected is None:
            return _StatCacheSnapshot(
                None,
                {
                    "source_section": APP_SECTION_TODAY_ENERGY,
                    "source_key": description.stat_key,
                    "local_counter_available": context.local_daily_raw is not None,
                },
                APP_SECTION_TODAY_ENERGY,
                self.last_reset,
            )

        source_section, source_key, raw, selected_source = selected
        attrs: dict[str, Any] = {
            "source_section": source_section,
            "source_key": source_key,
        }
        attrs["local_counter_available"] = (
            context.local_daily_raw is not None
            or source_section == PAYLOAD_LOCAL_DAILY_ENERGY
        )
        if valid_provenance is not None and selected == observations[0]:
            fallback = valid_provenance.get("fallback")
            if isinstance(fallback, str):
                attrs["fallback"] = fallback
                if source_section == PAYLOAD_LOCAL_DAILY_ENERGY:
                    attrs["fallback_metric"] = source_key
        request = selected_source.get(APP_REQUEST_META)
        if isinstance(request, dict):
            attrs["request"] = request
        return _StatCacheSnapshot(
            raw,
            attrs,
            source_section,
            self.last_reset,
        )

    def _local_flow_snapshot(  # ruff:ignore[too-many-locals]
        self,
        context: _StatRefreshContext,
    ) -> _StatCacheSnapshot:
        """Resolve local raw and documented HTTP day-flow observations in kWh."""
        description = self.entity_description
        observations: list[tuple[str, str, float, bool, dict[str, Any]]] = []
        seen_sources: set[str] = set()
        if context.local_daily_raw is not None:
            local_value, local_metric = context.local_daily_raw
            if local_value >= 0:
                local_source = self._source_for_section(
                    PAYLOAD_LOCAL_DAILY_ENERGY,
                    context.payload,
                )
                observations.append((
                    PAYLOAD_LOCAL_DAILY_ENERGY,
                    local_metric,
                    local_value,
                    True,
                    local_source,
                ))
                seen_sources.add(PAYLOAD_LOCAL_DAILY_ENERGY)
        source_specs = (
            (PAYLOAD_LOCAL_DAILY_ENERGY, description.stat_key, True),
            (PAYLOAD_DEVICE_STATISTIC, description.stat_key, False),
            *((section, key, False) for section, key in description.fallback_sources),
        )
        for source_section, source_key, is_local in source_specs:
            if source_section in seen_sources:
                continue
            seen_sources.add(source_section)
            source = self._source_for_section(source_section, context.payload)
            value = safe_float(source.get(source_key))
            if value is None or value < 0:
                continue
            if is_local:
                value = round(value / JACKERY_LIVE_ENERGY_UNITS_PER_KWH, 5)
            observations.append((source_section, source_key, value, is_local, source))

        positives = [observation for observation in observations if observation[2] > 0]
        selected = max(positives, key=itemgetter(2), default=None)
        if selected is None:
            zeroes = [
                observation for observation in observations if observation[2] == 0
            ]
            if (
                len({observation[0] for observation in zeroes})
                >= _MIN_ZERO_CORROBORATION_SOURCES
            ):
                selected = zeroes[-1]
            else:
                selected = next(
                    (
                        observation
                        for observation in zeroes
                        if observation[3] or observation[0] == PAYLOAD_DEVICE_STATISTIC
                    ),
                    None,
                )

        if selected is None:
            return _StatCacheSnapshot(
                None,
                {
                    "source_section": description.section,
                    "source_key": description.stat_key,
                },
                description.section,
                self.last_reset,
            )

        source_section, source_key, value, is_local, source = selected
        attrs: dict[str, Any] = {
            "source_section": source_section,
            "source_key": source_key,
            "fallback": (
                "local_lifetime_delta" if is_local else "documented_http_day_fallback"
            ),
        }
        if is_local:
            attrs["fallback_metric"] = source_key
        request = source.get(APP_REQUEST_META)
        if isinstance(request, dict):
            attrs["request"] = request
        return _StatCacheSnapshot(
            value,
            attrs,
            source_section,
            self.last_reset,
        )

    def _refresh_cache(
        self,
        context: _StatRefreshContext,
        period_cache: _PeriodResolutionCache,
    ) -> _StatCacheSnapshot:
        """Recompute one stat snapshot through its dedicated value path."""
        section = self.entity_description.section
        stat_key = self.entity_description.stat_key
        if self.entity_description.key == "pv_revenue_day":
            return self._pv_revenue_day_snapshot(context)
        if self.entity_description.key in {
            "pv_revenue_week",
            "pv_revenue_month",
            "pv_revenue_year",
        }:
            snapshot = self._refresh_non_period_cache(context)
            snapshot.attrs["revenue_basis"] = "app_reported"
            return snapshot
        if section == APP_SECTION_TODAY_ENERGY and self._reset_period == DATE_TYPE_DAY:
            return self._apply_observed_energy_floor(
                self._compact_today_snapshot(context), context
            )
        if self.entity_description.key in {
            "device_today_ongrid_to_battery",
            "device_today_pv_to_battery",
            "device_today_battery_to_ongrid",
        }:
            return self._apply_observed_energy_floor(
                self._local_flow_snapshot(context), context
            )
        if (
            _trend_series_key(section, stat_key) is not None
            or self.entity_description.key == "today_battery_charge_energy"
        ):
            snapshot = self._refresh_period_cache(context, period_cache)
        else:
            snapshot = self._refresh_non_period_cache(context)
        return self._apply_observed_energy_floor(snapshot, context)

    def _apply_observed_energy_floor(  # ruff: ignore[too-many-branches, too-many-locals, too-many-statements, too-many-return-statements]
        self, snapshot: _StatCacheSnapshot, context: _StatRefreshContext
    ) -> _StatCacheSnapshot:
        """Publish measured own lower-stage energy with explicit partial coverage."""
        description = self.entity_description
        if description.device_class != SensorDeviceClass.ENERGY:
            return snapshot
        identity = _NATIVE_OWN_ENERGY_SOURCES.get(description.key)
        if identity is None:
            prefix, _, period = description.section.rpartition("_")
            if period not in {
                DATE_TYPE_DAY,
                DATE_TYPE_WEEK,
                DATE_TYPE_MONTH,
                DATE_TYPE_YEAR,
            }:
                return snapshot
            stat_key = description.stat_key
        else:
            prefix, stat_key = identity
            period = self._reset_period or "total"
        own_metric = self._local_daily_metric_key()
        local_day = (
            context.local_daily_raw[0]
            if context.local_daily_raw is not None
            and context.local_daily_raw[1] == own_metric
            else None
        )
        own_period = context.local_period_raw
        if own_period is not None and own_period[1] != own_metric:
            own_period = None
        if (
            period == DATE_TYPE_YEAR
            and own_period is None
            and self._period_data_offset(
                description.section,
                context.payload,
                context.local_timezone,
                context.local_now,
            )
            != 0
        ):
            # A month bucket from the current year cannot validate a stale
            # or future-year API response. Keep independent native evidence
            # only when its own year baseline has been confirmed.
            return snapshot
        # Context payloads are detached once per device/batch. Retain only
        # this metric's detached evidence, not 55 full device-history copies.
        coordinator = getattr(self, "coordinator", None)
        device_id = getattr(self, "_device_id", None)
        owner_token = (
            coordinator,
            description,
            getattr(self, "_cache_lifecycle_epoch", 0),
            getattr(coordinator, "_system_binding_epochs", {}).get(device_id),
            getattr(coordinator, "_http_parent_identity_epochs", {}).get(device_id),
            description.key,
            prefix,
            period,
            stat_key,
            own_metric,
        )
        clock_token = (
            context.local_today,
            context.local_now.tzinfo,
            context.local_now.fold,
            getattr(context, "local_timezone", None),
        )
        local_inputs = (
            context.local_daily_raw,
            context.local_period_raw,
            context.local_period_attributes,
        )
        memo = getattr(self, "_observed_period_memo", None)
        reusable = False
        if (
            memo is not None
            and all(
                old is new
                for old, new in zip(memo[0][:2], owner_token[:2], strict=True)
            )
            and memo[0][2:] == owner_token[2:]
        ):
            try:
                reusable = (
                    memo[1] == clock_token
                    and _stat_inputs_equal(memo[2], local_inputs)
                    and _stat_period_evidence_equal(
                        self,
                        memo[3],
                        context.payload,
                        comparison_cache=getattr(context, "comparison_cache", None),
                    )
                )
            except _StatFreshnessUnavailableError:
                reusable = False
        if reusable and memo is not None:
            observed = memo[4]
        else:
            # This is a floor of independently measured energy. The vendor
            # period scalar/chart has already been reconciled in snapshot;
            # consuming it again would undo a certified daily correction.
            own_evidence: dict[str, Any] = {
                key: context.payload[key]
                for key in (
                    PAYLOAD_OBSERVED_DAY_STATISTICS,
                    PAYLOAD_VERIFIED_DAY_STATISTICS,
                )
                if key in context.payload
            }
            observed = observed_period_energy_total(
                own_evidence,
                f"{prefix}_{period}",
                stat_key,
                today=context.local_today,
                # A verified PV-channel day is a valid partial week floor.
                # A day alone cannot certify CT or month/year totals.
                local_day_kwh=(
                    local_day
                    if period == DATE_TYPE_DAY
                    or (
                        period == DATE_TYPE_WEEK
                        and prefix == APP_SECTION_PV_STAT
                        and stat_key in _PV_CHANNEL_ENERGY_KEYS
                    )
                    else None
                ),
                local_period_kwh=own_period[0] if own_period is not None else None,
                local_period_attributes=context.local_period_attributes,
                local_period_metric=own_metric,
            )
            sections = {
                prefix,
                description.section,
                PAYLOAD_LOCAL_DAILY_ENERGY,
                *_PV_OWNER_SECTIONS,
            }
            sections.update(
                f"{prefix}_{item}"
                for item in (
                    DATE_TYPE_DAY,
                    DATE_TYPE_WEEK,
                    DATE_TYPE_MONTH,
                    DATE_TYPE_YEAR,
                )
            )
            sections.update(source for source, _ in description.fallback_sources)
            projection = {
                key: context.payload[key] for key in sections if key in context.payload
            }
            for history_key in (
                PAYLOAD_VERIFIED_DAY_STATISTICS,
                PAYLOAD_OBSERVED_DAY_STATISTICS,
            ):
                history = context.payload.get(history_key)
                if not isinstance(history, dict):
                    if history_key in context.payload:
                        projection[history_key] = history
                    continue
                metric_history = {}
                for day, families in history.items():
                    family = (
                        families.get(prefix) if isinstance(families, dict) else None
                    )
                    if isinstance(family, dict) and stat_key in family:
                        metric_history[day] = {prefix: {stat_key: family[stat_key]}}
                projection[history_key] = metric_history
            self._observed_period_memo = (
                owner_token,
                clock_token,
                local_inputs,
                projection,
                observed,
            )
        if observed is None and own_period is None:
            return snapshot
        value, coverage = observed if observed is not None else (None, {})
        if (
            own_period is not None
            and context.local_period_attributes.get("baseline_source")
            != "own_observed_counter"
            and (value is None or own_period[0] > value)
        ):
            value = own_period[0]
            coverage = {
                "partial": True,
                "period": period,
                "unit": "kWh",
                "sources": [PAYLOAD_LOCAL_DAILY_ENERGY],
                "counter_baseline": context.local_period_attributes,
            }
        needs_complete_pv_days = (
            snapshot.native_value is None
            and own_period is None
            and prefix == APP_SECTION_PV_STAT
            and stat_key in _PV_CHANNEL_ENERGY_KEYS
            and period in {DATE_TYPE_MONTH, DATE_TYPE_YEAR}
        )
        # A verified historical day alone cannot certify an open PV
        # month/year. Actual five-minute observations are different: their
        # explicitly partial measured energy is still a valid own lower
        # bound, even if a full-day Recorder certificate cannot be issued.
        # Never replace an invalid vendor scalar with an inferred 0 or a
        # cross-period total.
        observed_sources = coverage.get("sources")
        own_partial_curve = (
            isinstance(coverage.get("observed_samples"), int)
            and coverage["observed_samples"] > 0
            and isinstance(observed_sources, list)
            and PAYLOAD_OBSERVED_DAY_STATISTICS in observed_sources
        )
        if value is None or (
            needs_complete_pv_days
            and not own_partial_curve
            and (
                not isinstance(coverage.get("covered_days"), int)
                or coverage["covered_days"] != coverage.get("expected_days")
            )
        ):
            return snapshot
        previous = safe_float(snapshot.native_value)
        # A current, native battery-to-AC cloud year value outranks
        # partial local energy observations, including for a true zero.
        if (
            description.key == "battery_to_ac_year_energy"
            and previous is not None
            and snapshot.attrs.get("source_section") == description.section
            and self._period_data_offset(
                description.section,
                context.payload,
                context.local_timezone,
                context.local_now,
            )
            == 0
        ):
            return snapshot
        if previous is not None and previous > value:
            return snapshot
        attrs = {
            **snapshot.attrs,
            "partial_aggregation": coverage["partial"],
            "observed_coverage": coverage,
            "observed_energy_floor": value,
        }
        if previous is None or previous < value:
            attrs.pop("unknown_reason", None)
            attrs.update({
                "source_section": description.section,
                "source_key": description.stat_key,
                "fallback": "own_observed_energy_aggregation",
            })
        return _StatCacheSnapshot(
            value, attrs, snapshot.source_section, snapshot.last_reset
        )

    def _initial_period_state(
        self,
        context: _StatRefreshContext,
        period_cache: _PeriodResolutionCache,
    ) -> _PeriodRefreshState:
        """Resolve the primary app period source before ordered fallbacks."""
        section = self.entity_description.section
        stat_key = self.entity_description.stat_key
        source = self._source_for_section(section, context.payload)
        series_key = _trend_series_key(section, stat_key)
        values, chart_sum, server_total = self._resolve_cached_period_value(
            source,
            section,
            stat_key,
            period_cache,
        )
        raw = (
            server_total
            if (
                is_day_period_payload(source, section)
                or is_device_year_period_section(source, section)
            )
            and server_total is not None
            else chart_sum
        )
        if not is_day_period_payload(source, section) and (
            raw is None or (raw == 0 and server_total is not None and server_total > 0)
        ):
            raw = server_total
        state = _PeriodRefreshState(
            section=section,
            stat_key=stat_key,
            source=source,
            series_key=series_key,
            values=values,
            chart_series_sum=chart_sum,
            server_total=server_total,
            raw=raw,
            cached_source_section=section,
            snapshot_last_reset=(
                _period_start_at(self._reset_period, context.local_now)
                if (
                    self._reset_period is not None
                    and self.entity_description.state_class == SensorStateClass.TOTAL
                )
                else None
            ),
        )
        if (
            is_day_period_payload(source, section)
            and str(source.get(APP_STAT_UNIT) or "").strip().lower() == "w"
        ):
            points = day_power_energy_points(
                source,
                section,
                stat_key,
                bucket_minutes=60,
                today=context.local_today,
                now=context.local_now,
                # Keep the measured curve: a lagging smaller cloud scalar must
                # not shrink it, or "curve wins when larger" never fires.
                keep_curve_over_smaller_scalar=True,
            )
            if points:
                state.day_curve_total = round(sum(point.value for point in points), 5)
                if state.raw is not None and state.day_curve_total > state.raw:
                    state.raw = state.day_curve_total
                    state.day_curve_fallback = True
        zero_observed = bool(
            server_total == 0
            or (
                isinstance(values, list)
                and any(value is not None for value in values)
                and not any((value or 0) > 0 for value in values)
            )
        )
        if zero_observed:
            state.period_zero_sources.add(section)
        return state

    def _use_local_daily_fallback(
        self,
        state: _PeriodRefreshState,
        context: _StatRefreshContext,
    ) -> bool:
        """Replace the selected source with a corroborated local lifetime delta."""
        local_daily = context.local_daily_raw
        # Today's delta is a day value only: as a week/month/year total it
        # showed 5.78 kWh for every CT period (live 2026-09-27).
        if local_daily is None or self._reset_period != DATE_TYPE_DAY:
            return False
        local_value, local_metric = local_daily
        if local_value == 0:
            state.period_zero_sources.add(PAYLOAD_LOCAL_DAILY_ENERGY)
            if len(state.period_zero_sources) < _MIN_ZERO_CORROBORATION_SOURCES:
                return False
        state.raw = local_value
        state.local_daily_metric = local_metric
        state.section = PAYLOAD_LOCAL_DAILY_ENERGY
        state.stat_key = local_metric
        state.source = self._source_for_section(state.section, context.payload)
        state.series_key = None
        state.values = None
        state.chart_series_sum = None
        state.server_total = None
        state.cached_source_section = state.section
        return True

    def _apply_documented_period_fallback(
        self,
        state: _PeriodRefreshState,
        context: _StatRefreshContext,
        period_cache: _PeriodResolutionCache,
    ) -> None:
        """Try each documented alternate period source in declaration order."""
        empty_ct_zero = bool(
            state.raw == 0
            and state.section.startswith(APP_SECTION_CT_STAT)
            and not (
                isinstance(state.values, list)
                and any(value is not None for value in state.values)
            )
            and self.entity_description.fallback_sources
        )
        empty_day_zero = bool(
            state.raw == 0
            and self._reset_period == DATE_TYPE_DAY
            and self.entity_description.fallback_sources
        )
        if state.raw is not None and not (empty_ct_zero or empty_day_zero):
            return
        for section, stat_key in self.entity_description.fallback_sources:
            source = self._source_for_section(section, context.payload)
            values, chart_sum, server_total = self._resolve_cached_period_value(
                source,
                section,
                stat_key,
                period_cache,
            )
            total = server_total
            if (
                total is None
                and not is_day_period_payload(source, section)
                and not is_device_year_period_section(source, section)
            ):
                total = chart_sum
            curve_total = None
            if (
                empty_day_zero
                and is_day_period_payload(source, section)
                and str(source.get(APP_STAT_UNIT) or "").strip().lower() == "w"
            ):
                points = day_power_energy_points(
                    source,
                    section,
                    stat_key,
                    bucket_minutes=60,
                    today=context.local_today,
                    now=context.local_now,
                    keep_curve_over_smaller_scalar=True,
                )
                if points:
                    curve_total = round(sum(point.value for point in points), 5)
                    if curve_total > (safe_float(total) or 0):
                        total = curve_total
            if total == 0:
                state.period_zero_sources.add(section)
                if (
                    empty_day_zero
                    or len(state.period_zero_sources) < _MIN_ZERO_CORROBORATION_SOURCES
                ):
                    continue
            if total is None:
                continue
            state.raw = total
            state.section = section
            state.stat_key = stat_key
            state.source = source
            state.series_key = _trend_series_key(section, stat_key)
            state.values = values
            state.chart_series_sum = chart_sum
            state.server_total = server_total
            if curve_total is not None and total == curve_total:
                state.day_curve_total = curve_total
                state.day_curve_fallback = True
            return
        if (
            empty_day_zero
            and len(state.period_zero_sources) < _MIN_ZERO_CORROBORATION_SOURCES
        ):
            state.raw = None

    def _apply_day_bucket_fallback(
        self,
        state: _PeriodRefreshState,
        context: _StatRefreshContext,
    ) -> None:
        """Use today's month/week chart bucket when day endpoints are empty."""
        if state.raw is not None:
            return
        sources = (
            (state.section, state.stat_key),
            *self.entity_description.fallback_sources,
        )
        for section, stat_key in sources:
            bucket = self._current_day_bucket_from_period_chart(
                section,
                stat_key,
                payload=context.payload,
                today=context.local_today,
            )
            if bucket is None:
                continue
            value, bucket_section, bucket_source = bucket
            if value == 0:
                state.period_zero_sources.add(bucket_section)
                if len(state.period_zero_sources) < _MIN_ZERO_CORROBORATION_SOURCES:
                    continue
            state.raw = value
            state.section = bucket_section
            state.stat_key = stat_key
            state.source = bucket_source
            state.day_bucket_fallback = f"current_day_bucket_from_{bucket_section}"
            return
        if state.day_curve_total is not None:
            state.raw = state.day_curve_total
            state.day_curve_fallback = True

    def _apply_open_period_fallbacks(
        self,
        state: _PeriodRefreshState,
        context: _StatRefreshContext,
    ) -> None:
        """Reconcile current open week/month/year buckets with local observations."""
        week = self._current_open_week_from_month_chart(
            state.section,
            state.stat_key,
            payload=context.payload,
            today=context.local_today,
            local_daily_raw=context.local_daily_raw,
            now=context.local_now,
        )
        if week is not None:
            value, section, source, completed_days_verified = week
            current = safe_float(state.raw)
            if (
                current is None
                or value > current
                or (completed_days_verified and value != current)
            ):
                state.raw = value
                state.section = section
                state.source = source
                state.series_key = _trend_series_key(section, state.stat_key)
                state.values = None
                state.chart_series_sum = None
                state.server_total = None
                state.open_week_fallback = "current_open_week_from_daily_buckets"
        period = self._current_open_month_or_year_with_local_day(
            state.section,
            state.stat_key,
            context=context,
            cloud_total=safe_float(state.raw),
            chart_series_sum=state.chart_series_sum,
        )
        if period is None:
            return
        value, section, source, fallback, completed_days_verified = period
        current = safe_float(state.raw)
        if (
            current is None
            or value > current
            or (completed_days_verified and value != current)
        ):
            state.raw = value
            state.section = section
            state.source = source
            state.series_key = _trend_series_key(section, state.stat_key)
            state.values = None
            state.chart_series_sum = None
            state.server_total = None
            state.open_period_fallback = fallback

    def _apply_local_period_fallbacks(
        self,
        state: _PeriodRefreshState,
        context: _StatRefreshContext,
    ) -> None:
        """Apply local day/period values only when newer than cloud selection."""
        if state.raw is None:
            self._use_local_daily_fallback(state, context)
        if self._reset_period == DATE_TYPE_DAY and context.local_daily_raw is not None:
            local_value, _metric = context.local_daily_raw
            current = safe_float(state.raw)
            if current is None or current <= 0 or local_value > current:
                self._use_local_daily_fallback(state, context)
        if context.local_period_raw is None:
            return
        local_value, local_metric = context.local_period_raw
        current = safe_float(state.raw)
        current_cloud_period = (
            current is not None
            and self._period_begin_from_meta(
                self.entity_description.section, context.payload
            )
            is not None
            and self._period_data_offset(
                self.entity_description.section,
                context.payload,
                context.local_timezone,
                context.local_now,
            )
            == 0
        )
        if current_cloud_period and (
            self._reset_period != DATE_TYPE_DAY
            or (current is not None and current > 0 and local_value <= current)
        ):
            return
        state.raw = local_value
        state.local_daily_metric = local_metric
        state.section = PAYLOAD_LOCAL_DAILY_ENERGY
        state.stat_key = local_metric
        state.source = self._source_for_section(state.section, context.payload)
        state.series_key = None
        state.values = None
        state.chart_series_sum = None
        state.server_total = None
        state.cached_source_section = state.section

    def _diagnostic_period_scalar(
        self,
        state: _PeriodRefreshState,
        context: _StatRefreshContext,
    ) -> float | None:
        """Classify rejected negative PV values without publishing them."""
        reported = safe_float(state.source.get(state.stat_key))
        if (
            reported is None
            and state.section == self.entity_description.section
            and state.stat_key == self.entity_description.stat_key
            and is_pv_generation_metric(state.section, state.stat_key)
        ):
            original_section = context.payload.get(state.section)
            if isinstance(original_section, dict):
                return safe_float(original_section.get(state.stat_key))
        return reported

    def _finalize_period_snapshot(
        self,
        state: _PeriodRefreshState,
        context: _StatRefreshContext,
    ) -> _StatCacheSnapshot:
        """Apply boundary guards and build the final period state/attributes."""
        fallback_selected = any((
            state.day_bucket_fallback,
            state.open_week_fallback,
            state.open_period_fallback,
        ))
        state.cached_source_section = (
            self.entity_description.section if fallback_selected else state.section
        )
        stale = bool(
            not state.day_bucket_fallback
            and self._reset_period
            and self._period_data_offset(
                state.cached_source_section,
                context.payload,
                context.local_timezone,
                context.local_now,
            )
            < 0
        )
        future = bool(
            not state.day_bucket_fallback
            and self._reset_period
            and self._period_data_offset(
                state.cached_source_section,
                context.payload,
                context.local_timezone,
                context.local_now,
            )
            > 0
        )
        if stale or future:
            state.raw = None
        if state.raw is None:
            self._use_local_daily_fallback(state, context)
        native_value = (
            _sensor_state_value(self.entity_description.transform(state.raw))
            if state.raw is not None
            else None
        )
        attrs: dict[str, Any] = {
            "source_section": state.section,
            "source_key": state.stat_key,
            "chart_series_key": state.series_key,
            "chart_series_sum": state.chart_series_sum,
            "server_total": state.server_total,
            **(
                {"local_counter_available": context.local_daily_raw is not None}
                if self._reset_period == DATE_TYPE_DAY
                else {}
            ),
        }
        if native_value is None:
            reported_scalar = self._diagnostic_period_scalar(state, context)
            invalid_negative_pv = (
                is_pv_generation_metric(state.section, state.stat_key)
                and reported_scalar is not None
                and reported_scalar < 0
            )
            attrs["unknown_reason"] = next(
                (
                    reason
                    for rejected, reason in (
                        (stale, "stale_period_data"),
                        (future, "future_period_data"),
                        (
                            invalid_negative_pv,
                            "invalid_negative_pv_generation",
                        ),
                        (
                            pv_channel_scalar_is_lifetime_offset(
                                state.source, state.section, state.stat_key
                            ),
                            "channel_exceeds_total",
                        ),
                        (
                            isinstance(state.values, list)
                            and any(value is None for value in state.values),
                            "incomplete_period_evidence",
                        ),
                        (state.source.get(state.stat_key) is None, "source_missing"),
                    )
                    if rejected
                ),
                "source_value_unavailable",
            )
        if state.day_curve_total is not None:
            attrs["integrated_power_curve_total"] = state.day_curve_total
        fallback = (
            state.open_period_fallback
            or state.open_week_fallback
            or state.day_bucket_fallback
            or (
                "integrated_current_day_power_curve"
                if state.day_curve_fallback
                else None
            )
        )
        if fallback is not None:
            attrs["fallback"] = fallback
        if state.local_daily_metric is not None:
            attrs["fallback"] = "local_lifetime_delta"
            attrs["fallback_metric"] = state.local_daily_metric
            if context.local_period_raw is not None:
                attrs.update(context.local_period_attributes)
        if (
            native_value is not None
            and isinstance(state.values, list)
            and len(state.values) <= _MAX_PERIOD_VALUES
        ):
            attrs["period_values"] = state.values
        # Audit note: year totals repaired from same-endpoint month payloads
        # (documented in the module header); the correction metadata stays
        # visible here instead of silently replacing the cloud value.
        year_backfill = state.source.get(APP_YEAR_BACKFILL_META)
        if isinstance(year_backfill, dict):
            attrs["year_month_backfill"] = year_backfill
        request = state.source.get(APP_REQUEST_META)
        if isinstance(request, dict):
            attrs["request"] = request
        if stale:
            attrs["stale_period_data"] = True
            attrs["stale_period_begin_date"] = self._period_begin_from_meta(
                state.cached_source_section,
                context.payload,
            )
            attrs["stale_period_fallback"] = (
                "local_lifetime_delta"
                if state.local_daily_metric is not None
                else "unknown_until_local_period"
            )
        if future:
            attrs["future_period_data"] = True
            attrs["future_period_begin_date"] = self._period_begin_from_meta(
                state.cached_source_section,
                context.payload,
            )
            attrs["future_period_fallback"] = (
                "local_lifetime_delta"
                if state.local_daily_metric is not None
                else "unknown_until_local_period"
            )
        return _StatCacheSnapshot(
            native_value,
            {
                key: value
                for key, value in attrs.items()
                if value is not None or key == "baseline_date"
            },
            state.cached_source_section,
            state.snapshot_last_reset,
        )

    def _refresh_period_cache(
        self,
        context: _StatRefreshContext,
        period_cache: _PeriodResolutionCache,
    ) -> _StatCacheSnapshot:
        """Build one period-stat snapshot through ordered fallback phases."""
        state = self._initial_period_state(context, period_cache)
        self._apply_documented_period_fallback(state, context, period_cache)
        self._apply_day_bucket_fallback(state, context)
        self._apply_open_period_fallbacks(state, context)
        self._apply_local_period_fallbacks(state, context)
        return self._finalize_period_snapshot(state, context)

    def _initial_non_period_state(
        self,
        context: _StatRefreshContext,
    ) -> _NonPeriodRefreshState:
        """Resolve the first scalar source or current-day chart fallback."""
        section = self.entity_description.section
        stat_key = self.entity_description.stat_key
        source = self._source_for_section(section, context.payload)
        raw = safe_float(source.get(stat_key))
        if (
            self.entity_description.key == "total_generation"
            and raw is not None
            and raw < 0
        ):
            # Invalid generated energy is unknown, never a true zero.
            raw = None
        # A zero scalar is only a placeholder when a documented fallback already
        # holds a positive value: the cloud's system aggregation (systemStatistic)
        # stalls for whole days while deviceStatistic keeps counting.
        if raw is None or safe_float(raw) == 0:
            for (
                fallback_section,
                fallback_key,
            ) in self.entity_description.fallback_sources:
                fallback_source = self._source_for_section(
                    fallback_section,
                    context.payload,
                )
                candidate = fallback_source.get(fallback_key)
                if candidate is None or (
                    raw is not None and not (safe_float(candidate) or 0) > 0
                ):
                    continue
                raw = candidate
                section = fallback_section
                stat_key = fallback_key
                source = fallback_source
                break
        day_fallback: str | None = None
        if raw is None:
            sources = ((section, stat_key), *self.entity_description.fallback_sources)
            for candidate_section, candidate_key in sources:
                bucket = self._current_day_bucket_from_period_chart(
                    candidate_section,
                    candidate_key,
                    payload=context.payload,
                    today=context.local_today,
                )
                if bucket is None:
                    continue
                raw, bucket_section, source = bucket
                section = bucket_section
                stat_key = candidate_key
                day_fallback = f"current_day_bucket_from_{bucket_section}"
                break
        return _NonPeriodRefreshState(
            section=section,
            stat_key=stat_key,
            source=source,
            raw=raw,
            day_bucket_fallback=day_fallback,
            cached_source_section=(
                self.entity_description.section if day_fallback else section
            ),
        )

    def _non_period_attributes(
        self,
        state: _NonPeriodRefreshState,
        context: _StatRefreshContext,
    ) -> dict[str, Any]:
        """Build minimal scalar-stat diagnostics after boundary evaluation."""
        attrs: dict[str, Any] = {
            "source_section": state.section,
            "source_key": state.stat_key,
        }
        source_number = safe_float(state.source.get(state.stat_key))
        if (
            self.entity_description.key == "total_generation"
            and state.raw is None
            and source_number is not None
            and source_number < 0
        ):
            attrs["unknown_reason"] = "invalid_negative_pv_generation"
        request = state.source.get(APP_REQUEST_META)
        if isinstance(request, dict):
            attrs["request"] = request
        if state.day_bucket_fallback is not None:
            attrs["fallback"] = state.day_bucket_fallback
        if state.stale:
            attrs["stale_period_data"] = True
            attrs["stale_period_begin_date"] = self._period_begin_from_meta(
                state.cached_source_section,
                context.payload,
            )
            if self._reset_period == DATE_TYPE_DAY:
                attrs["stale_period_fallback"] = "zero_until_fresh_day_data"
        if state.future:
            attrs["future_period_data"] = True
            attrs["future_period_begin_date"] = self._period_begin_from_meta(
                state.cached_source_section,
                context.payload,
            )
            attrs["future_period_fallback"] = "unknown_until_local_period"
        total_guard = state.source.get(APP_TOTAL_GUARD_META)
        if isinstance(total_guard, dict):
            corrected = total_guard.get("corrected")
            if isinstance(corrected, dict) and state.stat_key in corrected:
                attrs["total_lower_bound_guard"] = total_guard
        savings = state.source.get(APP_SAVINGS_CALC_META)
        if state.stat_key == APP_STAT_TOTAL_REVENUE and isinstance(savings, dict):
            attrs["savings_calculation"] = savings
        if state.stat_key == APP_STAT_TODAY_LOAD:
            attrs["cloud_field"] = "todayLoad"
            attrs["cloud_caveat"] = (
                "Jackery cloud reports the inverter's on-grid output for today; "
                "this is not smart-meter home consumption. Enable the derived "
                "home-consumption sensor for measured household load."
            )
        return attrs

    def _finalize_non_period_snapshot(
        self,
        state: _NonPeriodRefreshState,
        context: _StatRefreshContext,
    ) -> _StatCacheSnapshot:
        """Apply stale/future guards and transform one scalar statistic."""
        if state.day_bucket_fallback is None and self._reset_period:
            state.stale = (
                self._period_data_offset(
                    state.cached_source_section,
                    context.payload,
                    context.local_timezone,
                )
                < 0
            )
            state.future = (
                self._period_data_offset(
                    state.cached_source_section,
                    context.payload,
                    context.local_timezone,
                )
                > 0
            )
        if state.stale or state.future:
            state.raw = None
        native_value = (
            _sensor_state_value(self.entity_description.transform(state.raw))
            if state.raw is not None
            else None
        )
        return _StatCacheSnapshot(
            native_value,
            self._non_period_attributes(state, context),
            state.cached_source_section,
            self.last_reset,
        )

    def _refresh_non_period_cache(
        self,
        context: _StatRefreshContext,
    ) -> _StatCacheSnapshot:
        """Build one scalar/non-period statistic snapshot."""
        state = self._initial_non_period_state(context)
        return self._finalize_non_period_snapshot(state, context)

    @callback
    def _capture_refresh_context(
        self,
        payload: dict[str, Any],
    ) -> _StatRefreshContext:
        """Capture event-loop inputs around a detached device payload."""
        local_timezone = self._local_timezone()
        local_now = dt_util.now(local_timezone)
        return _StatRefreshContext(
            payload=payload,
            local_timezone=local_timezone,
            local_now=local_now,
            local_today=local_now.date(),
            local_daily_raw=self._local_daily_raw(),
            local_period_raw=self._local_period_raw(local_now.date()),
            local_period_attributes=self._local_period_baseline_attributes(),
        )

    @callback
    def _apply_cache_snapshot(self, snapshot: _StatCacheSnapshot) -> None:
        """Apply a completed snapshot atomically on the event loop."""
        # This apply has no await or external callbacks. Share its validated
        # physical owner across retention and attributes; the batch separately
        # revalidates ownership after executor work and before calling us.
        # A one-item context distinguishes a validated unknown owner from no
        # context, so standalone helpers still resolve their current owner.
        owner_context = (
            (_energy_owner_identity(self),)
            if self.entity_description.device_class == SensorDeviceClass.ENERGY
            else None
        )
        snapshot = self._retain_compact_local_day(snapshot, owner_context=owner_context)
        snapshot = self._retain_period_energy(snapshot, owner_context=owner_context)
        candidate = snapshot.native_value
        attrs = dict(snapshot.attrs)
        attrs.pop("energy_owner", None)
        if self.entity_description.device_class == SensorDeviceClass.ENERGY:
            attrs.update(_energy_owner_attributes(self, owner_context=owner_context))
        restored_lifetime_value = getattr(self, "_restored_lifetime_value", None)
        if candidate is None and restored_lifetime_value is not None:
            candidate = restored_lifetime_value
            attrs = {
                **attrs,
                "value_restored": True,
                "restore_reason": "lifetime_counter_source_unavailable",
            }
        guarded = guard_total_increasing_jitter(
            self._cached_native_value,
            candidate,
            self.entity_description,
        )
        if guarded is not candidate:
            attrs = {**attrs, "lifetime_value_retained": True}
        self._cached_native_value = guarded
        self._cached_attrs = attrs
        if snapshot.native_value is not None:
            self._restored_lifetime_value = None
        self._cached_source_section = snapshot.source_section

    def _retain_compact_local_day(
        self,
        snapshot: _StatCacheSnapshot,
        *,
        owner_context: tuple[dict[str, str] | None] | None = None,
    ) -> _StatCacheSnapshot:
        """Keep a measured local day while a lagging HTTP fallback has no counter."""
        if owner_context is None:
            owner_context = (_energy_owner_identity(self),)
        metric = self._local_daily_metric_key()
        if self._reset_period != DATE_TYPE_DAY or metric is None:
            return snapshot
        if owner_context[0] is None:
            self._local_day_snapshot = None
            self._local_day_snapshot_date = None
            return snapshot
        retained = getattr(self, "_local_day_snapshot", None)
        today = self._local_today()
        retained_date = getattr(self, "_local_day_snapshot_date", None)
        if retained is not None and (
            retained.last_reset != snapshot.last_reset
            or (retained_date is not None and retained_date != today)
        ):
            self._local_day_snapshot = None
            self._local_day_snapshot_date = None
            retained = None
        if snapshot.attrs.get("local_counter_available") is True:
            self._local_day_snapshot = None
            self._local_day_snapshot_date = None
            retained = None
        candidate = safe_float(snapshot.native_value)
        if (
            candidate is not None
            and candidate > 0
            and snapshot.attrs.get("source_section") == PAYLOAD_LOCAL_DAILY_ENERGY
            and snapshot.attrs.get("fallback") == "local_lifetime_delta"
            and snapshot.attrs.get("source_key") == metric
        ):
            self._local_day_snapshot = snapshot
            self._local_day_snapshot_date = today
            return snapshot
        retained_value = safe_float(retained.native_value) if retained else None
        if (
            retained is None
            or retained_value is None
            or snapshot.attrs.get("unknown_reason")
            not in {None, "source_missing", "source_value_unavailable"}
        ):
            return snapshot
        own_http_fallback = (
            snapshot.attrs.get("source_section") == self.entity_description.section
            and snapshot.attrs.get("source_key") == self.entity_description.stat_key
            and snapshot.attrs.get("fallback")
            in {None, "direct_http_today_total", "integrated_current_day_power_curve"}
        )
        if candidate is None or (
            0 <= candidate < retained_value
            and own_http_fallback
            and snapshot.attrs.get("local_counter_available") is False
        ):
            return _StatCacheSnapshot(
                retained.native_value,
                {
                    **retained.attrs,
                    "local_counter_available": snapshot.attrs.get(
                        "local_counter_available", False
                    ),
                    "day_value_retained": True,
                    "retention_reason": "awaiting_local_counter",
                    "http_candidate": snapshot.native_value,
                },
                retained.source_section,
                retained.last_reset,
            )
        return snapshot

    def _retain_period_energy(  # ruff: ignore[too-many-return-statements]
        self,
        snapshot: _StatCacheSnapshot,
        *,
        owner_context: tuple[dict[str, str] | None] | None = None,
    ) -> _StatCacheSnapshot:
        """Bridge a missing source only with this sensor's same-period value."""
        if owner_context is None:
            owner_context = (_energy_owner_identity(self),)
        if (
            self._reset_period not in {DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR}
            or self.entity_description.device_class != SensorDeviceClass.ENERGY
            or self.entity_description.state_class != SensorStateClass.TOTAL
        ):
            return snapshot
        if owner_context[0] is None:
            self._period_energy_snapshot = None
            return snapshot
        retained = getattr(self, "_period_energy_snapshot", None)
        if retained is not None and (
            retained.last_reset != snapshot.last_reset
            or _legacy_ct_day_only_period_value(
                self.entity_description.section, retained.attrs
            )
        ):
            self._period_energy_snapshot = None
            retained = None
        if snapshot.native_value is not None:
            retained_value = safe_float(retained.native_value) if retained else None
            candidate = safe_float(snapshot.native_value)
            weekly_battery = (
                self._reset_period == DATE_TYPE_WEEK
                and self.entity_description.key
                in {"battery_charge_week_energy", "battery_discharge_week_energy"}
            )
            if (
                weekly_battery
                and retained is not None
                and retained_value is not None
                and candidate is not None
                and 0 <= candidate < retained_value
            ):
                return _StatCacheSnapshot(
                    retained.native_value,
                    {
                        **retained.attrs,
                        "period_candidate": snapshot.native_value,
                        "period_candidate_source": snapshot.attrs,
                        "retention_reason": "same_week_energy_decrease",
                    },
                    retained.source_section,
                    retained.last_reset,
                )
            self._period_energy_snapshot = snapshot
            return snapshot
        if retained is None:
            return snapshot
        if snapshot.attrs.get("unknown_reason") not in {
            None,
            "source_missing",
            "source_value_unavailable",
        }:
            return snapshot
        return _StatCacheSnapshot(
            retained.native_value,
            {
                **retained.attrs,
                "value_restored": True,
                "restore_reason": "same_period_source_unavailable",
            },
            retained.source_section,
            retained.last_reset,
        )

    async def _async_restore_period_energy(self) -> None:
        """Restore own validated energy, source and reset after a cold restart."""
        if (
            self._reset_period not in {DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR}
            or self.entity_description.device_class != SensorDeviceClass.ENERGY
            or self.entity_description.state_class != SensorStateClass.TOTAL
        ):
            return
        state = await self.async_get_last_state()
        if (
            state is None
            or not _energy_restore_identity_matches(self, state)
            or state.attributes.get("source_section") != self.entity_description.section
            or state.attributes.get("source_key") != self.entity_description.stat_key
            or _legacy_ct_day_only_period_value(
                self.entity_description.section, state.attributes
            )
        ):
            return
        reset = state.attributes.get("last_reset")
        reset_time = dt_util.parse_datetime(reset) if isinstance(reset, str) else None
        if reset_time != self._compute_period_start(self._reset_period):
            return
        value = await _async_restored_lifetime_energy_value(
            self, self.entity_description.native_unit_of_measurement
        )
        if value is not None and getattr(self, "_period_energy_snapshot", None) is None:
            self._period_energy_snapshot = _StatCacheSnapshot(
                value,
                {
                    "source_section": self.entity_description.section,
                    "source_key": self.entity_description.stat_key,
                },
                self.entity_description.section,
                reset_time,
            )
            if self._cached_native_value is None:
                self._cached_native_value = value
                self._cached_attrs = {
                    "source_section": self.entity_description.section,
                    "source_key": self.entity_description.stat_key,
                    "value_restored": True,
                    "restore_reason": "period_energy_restored",
                    **_energy_owner_attributes(self),
                }

    async def _async_restore_compact_local_day(self) -> None:
        """Restore only this sensor's measured local total within the same day."""
        if self._reset_period != DATE_TYPE_DAY:
            return
        metric = self._local_daily_metric_key()
        if metric is None:
            return
        state = await self.async_get_last_state()
        if (
            state is None
            or not _energy_restore_identity_matches(self, state)
            or state.attributes.get("source_section") != PAYLOAD_LOCAL_DAILY_ENERGY
            or state.attributes.get("fallback") != "local_lifetime_delta"
            or state.attributes.get("source_key") != metric
        ):
            return
        reset = state.attributes.get("last_reset")
        reset_time = dt_util.parse_datetime(reset) if isinstance(reset, str) else None
        if reset_time != self._compute_period_start(DATE_TYPE_DAY):
            return
        value = await _async_restored_lifetime_energy_value(
            self, self.entity_description.native_unit_of_measurement
        )
        if value is None or value <= 0:
            return
        if getattr(self, "_local_day_snapshot", None) is None:
            self._local_day_snapshot = _StatCacheSnapshot(
                value,
                {
                    "source_section": PAYLOAD_LOCAL_DAILY_ENERGY,
                    "source_key": state.attributes["source_key"],
                    "fallback": "local_lifetime_delta",
                    "value_restored": True,
                    "restore_reason": "local_day_counter_source_unavailable",
                },
                PAYLOAD_LOCAL_DAILY_ENERGY,
                reset_time,
            )

    @callback
    def _write_cached_state(self) -> None:
        """Write the state after the asynchronous cache refresh completes."""
        if not self._cache_refresh_active or self._cache_initializing:
            return
        super()._handle_coordinator_update()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh the cache before HA writes the new state."""
        if not self._cache_refresh_active:
            return
        _stat_refresh_batch_for(self.coordinator).request(self, write_state=True)

    @callback
    def async_start_passive_cache(
        self, on_update: Callable[[], None]
    ) -> Callable[[], None] | None:
        """Prepare disabled-entity values without registering a native HA state."""
        if self._cache_refresh_active:
            return None
        self._cache_refresh_active = True
        self._passive_cache_callback = on_update
        self._passive_cache_unsubscribe = self.coordinator.async_add_listener(
            self._request_passive_cache
        )
        self._request_passive_cache()
        return self._stop_passive_cache

    @callback
    def _request_passive_cache(self) -> None:
        _stat_refresh_batch_for(self.coordinator).request(self, write_state=False)

    @callback
    def _stop_passive_cache(self) -> None:
        unsubscribe = self._passive_cache_unsubscribe
        if unsubscribe is None:
            return
        self._passive_cache_unsubscribe = None
        self._passive_cache_callback = None
        unsubscribe()
        _stat_refresh_batch_for(self.coordinator).discard(self)

    async def async_added_to_hass(self) -> None:
        """Prime the cache so the first state read sees real values.

        IMPORTANT: the refresh runs BEFORE super().async_added_to_hass()
        because CoordinatorEntity's super().async_added_to_hass() writes
        the initial state to HA — and that initial write reads
        `native_value` and `extra_state_attributes`. Filling the
        cache after super() means the very first state write hits the
        cold-cache path, costing ~400ms per period sensor on slower
        Pi/HAOS hosts (visible in logs as
        "Updating state for sensor... took 0.446 seconds").
        """
        self._stop_passive_cache()
        batch = _stat_refresh_batch_for(self.coordinator)
        self._cache_refresh_active = True
        self._cache_initializing = True
        try:
            await super().async_added_to_hass()
        except (Exception, asyncio.CancelledError):
            batch.discard(self)
            raise
        finally:
            self._cache_initializing = False
        if (
            self.entity_description.key in _RESTORABLE_LIFETIME_STAT_SENSOR_KEYS
            and self._cached_native_value is None
        ):
            self._restored_lifetime_value = await _async_restored_lifetime_energy_value(
                self,
                self.entity_description.native_unit_of_measurement,
            )
            if self._restored_lifetime_value is not None:
                self._cached_native_value = self._restored_lifetime_value
                self._cached_attrs.update(_energy_owner_attributes(self))
                self.async_write_ha_state()
        # EntityPlatform adds entities sequentially. Waiting here would drain one
        # executor job per entity and block platform setup. Queue without waiting so
        # the batch's initial event-loop yield can collect every statistic entity.
        await self._async_restore_compact_local_day()
        await self._async_restore_period_energy()
        batch.request(self, write_state=True)

    async def async_will_remove_from_hass(self) -> None:
        """Invalidate queued work before the entity leaves Home Assistant."""
        self._stop_passive_cache()
        _stat_refresh_batch_for(self.coordinator).discard(self)
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> StateType:
        """The entity's current value."""
        return self._cached_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current state."""
        return self._cached_attrs

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Unit of measurement, using the device currency for MONETARY revenue.

        Period PV-revenue sensors (device_class=MONETARY) are valued in the
        device's own currency, carried per period section as
        ``PvStatApi$Bean.currency`` (e.g. ``"€"``, ``"$"``). HA renders a
        MONETARY entity with whatever native unit it publishes, so the live
        currency symbol is surfaced here rather than baked into the static
        description. Non-monetary stats and revenue payloads without a
        currency field fall back to the description's configured unit
        (CURRENCY_EURO for revenue), so the unit is never empty.
        """
        native_unit: str | None = self.entity_description.native_unit_of_measurement
        if self.entity_description.device_class != SensorDeviceClass.MONETARY:
            return native_unit
        source = self._source_for_section(self._cached_source_section)
        currency = source.get(FIELD_CURRENCY)
        if isinstance(currency, str) and currency.strip():
            return currency
        return native_unit

    # --- restored from 24.05\24.05\custom_components\jackery_solarvault\sensor.py ---
    def _local_daily_metric_key(self) -> str | None:
        """The the local lifetime-counter metric for this DAY sensor."""
        if self._reset_period not in {
            DATE_TYPE_DAY,
            DATE_TYPE_WEEK,
            DATE_TYPE_MONTH,
            DATE_TYPE_YEAR,
        }:
            return None
        if self.entity_description.device_class != SensorDeviceClass.ENERGY:
            return None
        return LOCAL_DAILY_METRIC_BY_SENSOR_KEY.get(self.entity_description.key)

    def _local_daily_raw(self) -> tuple[float, str] | None:
        """Return today's local transport-neutral delta in kWh."""
        metric_key = self._local_daily_metric_key()
        if metric_key is None:
            return None
        value = self.coordinator.local_daily_energy_kwh(self._device_id, metric_key)
        if value is None:
            return None
        return value, metric_key

    def _local_period_baseline_attributes(self) -> dict[str, Any]:
        """Capture own-counter provenance before the executor consumes the snapshot."""
        metric_key = self._local_daily_metric_key()
        getter = getattr(self.coordinator, "local_period_baseline_attributes", None)
        if metric_key is None or self._reset_period is None or not callable(getter):
            return {}
        attributes = getter(self._device_id, metric_key, period=self._reset_period)
        return dict(attributes) if isinstance(attributes, dict) else {}

    def _local_period_raw(self, today: date) -> tuple[float, str] | None:
        """Return own period energy with its declared Recorder/install baseline."""
        metric_key = self._local_daily_metric_key()
        if metric_key is None or self._reset_period is None:
            return None
        getter = getattr(self.coordinator, "local_period_energy_kwh", None)
        if not callable(getter):
            return (
                self._local_daily_raw() if self._reset_period == DATE_TYPE_DAY else None
            )
        value = getter(
            self._device_id,
            metric_key,
            period=self._reset_period,
            today=today,
        )
        if value is None:
            return None
        return value, metric_key


@dataclass(frozen=True, slots=True)
class _StatRefreshRequest:
    """One versioned entity computation in a shared coordinator batch."""

    entity: JackeryStatSensor
    generation: int
    context: _StatRefreshContext
    write_state: bool
    pv_fence: _PvPeriodFence | None = None
    energy_owner_token: tuple[object, ...] | None = None


@dataclass(frozen=True, slots=True)
class _PvPeriodFence:
    """Capture ownership and entity state for a narrowly scoped rebase."""

    coordinator: object
    entry: object
    index: object
    record: object
    device_meta: object
    system_meta: object
    owner_fields: tuple[object, ...]
    binding_epoch: int
    source_owner_epoch: int
    lifecycle_epoch: int
    description: object
    retained: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _StatRefreshResult:
    """Executor result returned for event-loop application."""

    request: _StatRefreshRequest
    snapshot: _StatCacheSnapshot | None = None
    error: Exception | None = None


_PV_PERIOD_FRESHNESS_KEYS: Final = {
    "device_pv1_month_energy": APP_STAT_PV1_ENERGY,
    "device_pv2_month_energy": APP_STAT_PV2_ENERGY,
    "device_pv3_month_energy": APP_STAT_PV3_ENERGY,
    "device_pv4_month_energy": APP_STAT_PV4_ENERGY,
    "device_pv1_year_energy": APP_STAT_PV1_ENERGY,
    "device_pv2_year_energy": APP_STAT_PV2_ENERGY,
    "device_pv3_year_energy": APP_STAT_PV3_ENERGY,
    "device_pv4_year_energy": APP_STAT_PV4_ENERGY,
}
_PV_MONTH_FRESHNESS_SECTIONS: Final = (
    APP_SECTION_PV_STAT,
    f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
    f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
    f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
    f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
    PAYLOAD_VERIFIED_DAY_STATISTICS,
    PAYLOAD_OBSERVED_DAY_STATISTICS,
    PAYLOAD_LOCAL_DAILY_ENERGY,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
)
# Keep the original narrowly validated identities; other canonical energy
# periods qualify through _stat_rebase_supported with the same owner fences.
_STAT_PERIOD_REBASE_KEYS: Final = frozenset(_PV_PERIOD_FRESHNESS_KEYS) | {
    "battery_to_ac_month_energy"
}
_BATTERY_MONTH_FRESHNESS_SECTIONS: Final = (
    APP_SECTION_BATTERY_STAT,
    f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}",
    f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_WEEK}",
    f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_MONTH}",
    f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_YEAR}",
    PAYLOAD_VERIFIED_DAY_STATISTICS,
    PAYLOAD_OBSERVED_DAY_STATISTICS,
    PAYLOAD_LOCAL_DAILY_ENERGY,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
)
_PV_OWNER_SECTIONS: Final = (
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
)
_PV_OWNER_FIELDS: Final = (
    FIELD_ID,
    FIELD_DEVICE_ID,
    FIELD_DEV_ID,
    FIELD_DEVICE_SN,
    FIELD_DEV_SN,
    FIELD_SN,
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_SN,
    FIELD_MODEL_CODE,
)
_STAT_FRESHNESS_MAX_ITEMS: Final = 4096
_STAT_FRESHNESS_MAX_DEPTH: Final = 32
_STAT_FRESHNESS_MAX_STRING: Final = 8192
_STAT_FRESHNESS_MAX_TEXT: Final = 131072
_STAT_FRESHNESS_MAX_LOGGED_CAUSES: Final = 8


class _StatFreshnessUnavailableError(Exception):
    """Evidence cannot be compared within the event-loop budget."""


def _stat_inputs_equal(left: object, right: object) -> bool:  # ruff: ignore[complex-structure]
    """Compare bounded supported evidence without coercing bools to numbers."""
    remaining = _STAT_FRESHNESS_MAX_ITEMS
    remaining_text = _STAT_FRESHNESS_MAX_TEXT

    def equal(first: object, second: object, depth: int) -> bool:  # ruff: ignore[too-many-return-statements]  # Each supported type keeps its fail-closed branch.  # ruff: ignore[too-many-branches, too-many-locals]
        nonlocal remaining, remaining_text
        remaining -= 1
        if remaining < 0 or depth > _STAT_FRESHNESS_MAX_DEPTH:
            raise _StatFreshnessUnavailableError
        kind = type(first)
        if kind is not type(second):
            return False
        if kind is str:
            length = len(cast("str", first))
            remaining_text -= length
            if length > _STAT_FRESHNESS_MAX_STRING or remaining_text < 0:
                raise _StatFreshnessUnavailableError
            return bool(first == second)
        if (
            kind is int
            or kind is float
            or kind is bool
            or kind is type(None)
            or kind is date
        ):
            return bool(first == second)
        if kind is datetime:
            first_time = cast("datetime", first)
            second_time = cast("datetime", second)
            return bool(
                first_time.isoformat() == second_time.isoformat()
                and first_time.fold == second_time.fold
                and first_time.tzinfo == second_time.tzinfo
            )
        if kind is dict:
            first_dict = cast("dict[object, object]", first)
            second_dict = cast("dict[object, object]", second)
            if len(first_dict) != len(second_dict):
                return False
            for (first_key, first_value), (second_key, second_value) in zip(
                first_dict.items(), second_dict.items(), strict=True
            ):
                if not equal(first_key, second_key, depth + 1) or not equal(
                    first_value, second_value, depth + 1
                ):
                    return False
            return True
        if kind is list or kind is tuple:
            first_items = cast("list[object] | tuple[object, ...]", first)
            second_items = cast("list[object] | tuple[object, ...]", second)
            count = len(first_items)
            if count != len(second_items):
                return False
            if (
                count >= 8  # ruff: ignore[magic-value-comparison]
                and count <= remaining
                and depth < _STAT_FRESHNESS_MAX_DEPTH
            ):
                # Compare flat chart vectors in C after exact leaf-type and
                # budget checks; every nested/unsupported value keeps the
                # bounded recursive path below.
                # IDs compare exact class identity without invoking equality
                # or hashing on an unsupported value's custom metaclass.
                first_kinds = tuple(map(id, map(type, first_items)))
                second_kinds = tuple(map(id, map(type, second_items)))
                if first_kinds == second_kinds:
                    kinds = set(first_kinds)
                    if kinds == {id(str)}:
                        strings = cast("list[str] | tuple[str, ...]", first_items)
                        lengths = tuple(map(len, strings))
                        text = sum(lengths)
                        if (
                            max(lengths) <= _STAT_FRESHNESS_MAX_STRING
                            and text <= remaining_text
                        ):
                            remaining -= count
                            remaining_text -= text
                            return first_items == second_items
                        # Keep early-mismatch behavior before a later oversized
                        # string by retaining the original recursive path.
                    elif kinds <= {id(int), id(float), id(bool), id(type(None))}:
                        remaining -= count
                        return (
                            first_items == second_items
                            # Sequence equality treats identical NaN objects as
                            # equal; reject those as the scalar path does.
                            and (
                                id(float) not in kinds
                                or all(item == item for item in first_items)  # ruff: ignore[comparison-with-itself]
                            )
                        )
            for item, other in zip(first_items, second_items, strict=True):
                if not equal(item, other, depth + 1):
                    return False
            return True
        raise _StatFreshnessUnavailableError

    return equal(left, right, 0)


class _StatInputComparisonCache:
    """Reuse complete comparisons only within one stable synchronous pass."""

    def __init__(self) -> None:
        self._results: dict[tuple[object, ...], tuple[object, object, bool]] = {}

    def _compare(self, key: tuple[object, ...], left: object, right: object) -> bool:
        # Cache whole calls with their original item/depth/text budget. Never
        # reuse recursive subresults, which would change the remaining budget
        # or the order in which an early mismatch precedes unsupported data.
        key = (
            _STAT_FRESHNESS_MAX_ITEMS,
            _STAT_FRESHNESS_MAX_DEPTH,
            _STAT_FRESHNESS_MAX_STRING,
            _STAT_FRESHNESS_MAX_TEXT,
            *key,
        )
        cached = self._results.get(key)
        if cached is not None:
            return cached[2]
        result = _stat_inputs_equal(left, right)
        if len(self._results) < _STAT_FRESHNESS_MAX_ITEMS:
            # Strong references prevent object-id reuse during this batch.
            # Exceptions retain no entry and must fail closed on every call.
            self._results[key] = left, right, result
        return result

    def fields_equal(
        self, left: tuple[object, object], right: tuple[object, object]
    ) -> bool:
        """Compare one present/absent field, even across new metric wrappers."""
        if type(left[0]) is not bool or type(right[0]) is not bool:
            return _stat_inputs_equal(left, right)
        return self._compare(
            ("field", left[0], id(left[1]), right[0], id(right[1])), left, right
        )

    def sections_equal(
        self,
        left: tuple[tuple[bool, object], ...],
        right: tuple[tuple[bool, object], ...],
    ) -> bool:
        """Compare the entire ordered section projection under one budget."""
        if any(type(present) is not bool for present, _value in (*left, *right)):
            return _stat_inputs_equal(left, right)
        return self._compare(
            (
                "sections",
                tuple((present, id(value)) for present, value in left),
                tuple((present, id(value)) for present, value in right),
            ),
            left,
            right,
        )


def _pv_retention_fence(entity: JackeryStatSensor) -> tuple[object, ...]:
    """Detach mutable restored and retained state from later changes."""
    snapshots = (
        getattr(entity, "_local_day_snapshot", None),
        getattr(entity, "_period_energy_snapshot", None),
    )
    values: list[object] = []
    for snapshot in snapshots:
        if snapshot is None:
            values.append(None)
        elif isinstance(snapshot, _StatCacheSnapshot):
            values.append((
                snapshot.native_value,
                snapshot.attrs,
                snapshot.source_section,
                snapshot.last_reset,
            ))
        else:
            return (object(),)
    values.extend((
        getattr(entity, "_restored_lifetime_value", None),
        getattr(entity, "_cached_native_value", None),
        getattr(entity, "_cache_initializing", False),
    ))
    value = tuple(values)
    try:
        supported = _stat_inputs_equal(value, value)
    except _StatFreshnessUnavailableError:
        return (object(),)
    if not supported:
        return (object(),)
    return deepcopy(value)


def _pv_payload_owner(entity: JackeryStatSensor) -> tuple[object, ...] | None:
    """Detach the small identity projection observed by this sensor."""
    payload = entity._payload  # ruff: ignore[private-member-access]
    if type(payload) is not dict:
        raise _StatFreshnessUnavailableError
    fields: list[object] = []
    for section in _PV_OWNER_SECTIONS:
        value = payload.get(section)
        if section not in payload:
            fields.append((False, None))
        elif value is None:
            fields.append((True, None))
        elif type(value) is dict:
            fields.append((
                True,
                tuple((key in value, value.get(key)) for key in _PV_OWNER_FIELDS),
            ))
        else:
            raise _StatFreshnessUnavailableError
    result = tuple(fields)
    if not _stat_inputs_equal(result, result):
        raise _StatFreshnessUnavailableError
    return deepcopy(result)


def _capture_pv_period_fence(  # Independent ownership checks fail closed.
    entity: JackeryStatSensor,
    source_owner_epoch: int,
) -> _PvPeriodFence | None:
    """Bind a candidate to its canonical sensor and current discovery owner."""
    if type(entity) is not JackeryStatSensor:
        return None
    description = entity.entity_description
    stat_key = _PV_PERIOD_FRESHNESS_KEYS.get(description.key)
    period = (
        DATE_TYPE_YEAR if description.key.endswith("_year_energy") else DATE_TYPE_MONTH
    )
    if (
        stat_key is None
        or description.stat_key != stat_key
        or description.section != f"{APP_SECTION_PV_STAT}_{period}"
        or description.fallback_sources
        or not any(description is candidate for candidate in STAT_DESCRIPTIONS)
    ):
        return None
    return _capture_stat_owner_fence(entity, source_owner_epoch)


def _capture_battery_period_fence(
    entity: JackeryStatSensor,
    source_owner_epoch: int,
) -> _PvPeriodFence | None:
    """Allow the canonical battery-to-AC month flow, with the same owner guards."""
    if type(entity) is not JackeryStatSensor:
        return None
    description = entity.entity_description
    if (
        description.key != "battery_to_ac_month_energy"  # ruff: ignore[too-many-boolean-expressions]
        or description.stat_key != APP_DEVICE_STAT_BATTERY_TO_AC
        or description.section != f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_MONTH}"
        or description.fallback_sources
        or description.reset_period != DATE_TYPE_MONTH
        or entity._reset_period != DATE_TYPE_MONTH  # ruff: ignore[private-member-access]
        or not any(description is candidate for candidate in STAT_DESCRIPTIONS)
    ):
        return None
    return _capture_stat_owner_fence(entity, source_owner_epoch)


def _stat_rebase_supported(entity: JackeryStatSensor) -> bool:
    """Limit additional native rebases to canonical own energy descriptors."""
    if entity.entity_description.key in _STAT_PERIOD_REBASE_KEYS:
        return True
    description = entity.entity_description
    return (
        type(entity) is JackeryStatSensor
        and getattr(description, "device_class", None) == SensorDeviceClass.ENERGY
        and any(description is candidate for candidate in STAT_DESCRIPTIONS)
        and (
            description.key in _NATIVE_OWN_ENERGY_SOURCES
            or description.section.rpartition("_")[2]
            in {DATE_TYPE_DAY, DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR}
        )
    )


def _capture_stat_period_fence(
    entity: JackeryStatSensor,
    source_owner_epoch: int,
) -> _PvPeriodFence | None:
    """Keep existing PV/battery fences and bind other canonical energy sources."""
    if entity.entity_description.key == "battery_to_ac_month_energy":
        return _capture_battery_period_fence(entity, source_owner_epoch)
    if entity.entity_description.key in _PV_PERIOD_FRESHNESS_KEYS:
        return _capture_pv_period_fence(entity, source_owner_epoch)
    if not _stat_rebase_supported(entity):
        return None
    return _capture_stat_owner_fence(entity, source_owner_epoch)


def _capture_stat_owner_fence(  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches, too-many-locals, too-many-statements]
    entity: JackeryStatSensor,
    source_owner_epoch: int,
) -> _PvPeriodFence | None:
    """Capture the unchanged ownership/lifecycle fence for validated sensors."""
    description = entity.entity_description
    coordinator = entity.coordinator
    entry = getattr(coordinator, "entry", None)
    index = getattr(coordinator, "_device_index", None)
    device_id = entity._device_id  # ruff: ignore[private-member-access]
    if (
        entry is None
        or getattr(entry, "runtime_data", None) is not coordinator
        or getattr(coordinator, "_shutdown_started", True)
        or type(index) is not dict
    ):
        return None
    record = index.get(device_id)
    if type(record) is not dict:
        return None
    device_meta = record.get(PAYLOAD_DEVICE_META)
    system_meta = record.get(PAYLOAD_SYSTEM_META)
    if type(device_meta) is not dict or type(system_meta) is not dict:
        return None
    serial = (
        device_meta.get(FIELD_DEVICE_SN)
        or device_meta.get(FIELD_DEV_SN)
        or device_meta.get(FIELD_SN)
    )
    system_id = record.get(FIELD_SYSTEM_ID)
    device_ids = (
        device_meta.get(FIELD_DEVICE_ID),
        device_meta.get(FIELD_DEV_ID),
        device_meta.get(FIELD_ID),
    )
    system_ids = (system_meta.get(FIELD_SYSTEM_ID), system_meta.get(FIELD_ID))
    serials = (
        device_meta.get(FIELD_DEVICE_SN),
        device_meta.get(FIELD_DEV_SN),
        device_meta.get(FIELD_SN),
    )
    if not isinstance(serial, str) or not serial.strip():
        return None
    if not isinstance(system_id, str | int) or isinstance(system_id, bool):
        return None
    if not str(system_id).strip() or not any(value is not None for value in device_ids):
        return None
    if any(
        value is not None and (isinstance(value, bool) or str(value) != device_id)
        for value in device_ids
    ):
        return None
    if any(
        value is not None and str(value) != str(system_id) for value in system_ids
    ) or any(value is not None and value != serial for value in serials):
        return None
    payload_owner = _pv_payload_owner(entity)
    if payload_owner is None:
        return None
    payload = entity._payload  # ruff: ignore[private-member-access]
    observed_serial = False
    for section in (PAYLOAD_DEVICE, PAYLOAD_DISCOVERY):
        value = payload.get(section)
        if type(value) is not dict:
            continue
        for key in (FIELD_DEVICE_SN, FIELD_DEV_SN, FIELD_SN):
            observed = value.get(key)
            if observed is not None:
                if observed != serial:
                    return None
                observed_serial = True
        for key in (FIELD_DEVICE_ID, FIELD_DEV_ID, FIELD_ID):
            observed = value.get(key)
            if observed is not None and str(observed) != device_id:
                return None
    if not observed_serial:
        return None
    for section in (PAYLOAD_SYSTEM, PAYLOAD_SYSTEM_META):
        value = payload.get(section)
        if type(value) is not dict:
            continue
        for key in (FIELD_SYSTEM_ID, FIELD_ID):
            observed = value.get(key)
            if observed is not None and str(observed) != str(system_id):
                return None
        for key in (FIELD_DEVICE_ID, FIELD_DEV_ID):
            observed = value.get(key)
            if observed is not None and str(observed) != device_id:
                return None
        for key in (FIELD_DEVICE_SN, FIELD_DEV_SN):
            observed = value.get(key)
            if observed is not None and observed != serial:
                return None
    fields = (
        system_id,
        device_meta.get(FIELD_DEVICE_ID),
        device_meta.get(FIELD_DEV_ID),
        device_meta.get(FIELD_ID),
        device_meta.get(FIELD_DEVICE_SN),
        device_meta.get(FIELD_DEV_SN),
        device_meta.get(FIELD_SN),
        device_meta.get(FIELD_MODEL_CODE),
        system_meta.get(FIELD_SYSTEM_ID),
        system_meta.get(FIELD_ID),
        system_meta.get(FIELD_SYSTEM_SN),
    )
    try:
        supported = _stat_inputs_equal(fields, fields)
    except _StatFreshnessUnavailableError:
        return None
    if not supported:
        return None
    epochs = getattr(coordinator, "_system_binding_epochs", {})
    epoch = epochs.get(device_id, 0) if isinstance(epochs, dict) else None
    if type(epoch) is not int:
        return None
    return _PvPeriodFence(
        coordinator=coordinator,
        entry=entry,
        index=index,
        record=record,
        device_meta=device_meta,
        system_meta=system_meta,
        owner_fields=deepcopy((*fields, payload_owner)),
        binding_epoch=epoch,
        source_owner_epoch=source_owner_epoch,
        lifecycle_epoch=getattr(entity, "_cache_lifecycle_epoch", 0),
        description=description,
        retained=_pv_retention_fence(entity),
    )


def _pv_fences_equal(old: _PvPeriodFence, new: _PvPeriodFence) -> bool:
    """Reject owner replacement, lifecycle changes and mutated retained data."""
    return (
        old.coordinator is new.coordinator
        and old.entry is new.entry
        and old.index is new.index
        and old.record is new.record
        and old.device_meta is new.device_meta
        and old.system_meta is new.system_meta
        and old.description is new.description
        and old.binding_epoch == new.binding_epoch
        and old.source_owner_epoch == new.source_owner_epoch
        and old.lifecycle_epoch == new.lifecycle_epoch
        and _stat_inputs_equal(old.owner_fields, new.owner_fields)
        and _stat_inputs_equal(old.retained, new.retained)
    )


def _pv_contexts_equal(old: _StatRefreshContext, new: _StatRefreshContext) -> bool:
    """Check local inputs and the wall-clock precision used by PV day curves."""
    first = old.local_now
    second = new.local_now
    return (
        first.replace(second=0, microsecond=0).isoformat()
        == second.replace(second=0, microsecond=0).isoformat()
        and first.fold == second.fold
        and first.tzinfo == second.tzinfo
        and old.local_timezone == new.local_timezone
        and old.local_today == new.local_today
        and _stat_inputs_equal(old.local_daily_raw, new.local_daily_raw)
        and _stat_inputs_equal(old.local_period_raw, new.local_period_raw)
        and _stat_inputs_equal(old.local_period_attributes, new.local_period_attributes)
    )


def _pv_evidence_equal(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    is_year: bool = False,
    comparison_cache: _StatInputComparisonCache | None = None,
) -> bool:
    """Compare the sections used by the selected PV-period resolver."""
    previous = tuple(
        (section in old, old.get(section))
        for section in _PV_MONTH_FRESHNESS_SECTIONS
        if section
        not in {PAYLOAD_VERIFIED_DAY_STATISTICS, PAYLOAD_OBSERVED_DAY_STATISTICS}
    )
    current = tuple(
        (section in new, new.get(section))
        for section in _PV_MONTH_FRESHNESS_SECTIONS
        if section
        not in {PAYLOAD_VERIFIED_DAY_STATISTICS, PAYLOAD_OBSERVED_DAY_STATISTICS}
    )
    compare_sections = (
        _stat_inputs_equal
        if comparison_cache is None
        else comparison_cache.sections_equal
    )
    if not compare_sections(previous, current):
        return False
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}"
    compare_fields = (
        _stat_inputs_equal
        if comparison_cache is None
        else comparison_cache.fields_equal
    )
    return not is_year or compare_fields(
        (section in old, old.get(section)), (section in new, new.get(section))
    )


def _battery_period_evidence_equal(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    comparison_cache: _StatInputComparisonCache | None = None,
) -> bool:
    """Keep every own battery period, local counter and verified-day input fresh."""
    compare = (
        _stat_inputs_equal
        if comparison_cache is None
        else comparison_cache.sections_equal
    )
    return compare(
        tuple(
            (section in old, old.get(section))
            for section in _BATTERY_MONTH_FRESHNESS_SECTIONS
            if section
            not in {PAYLOAD_VERIFIED_DAY_STATISTICS, PAYLOAD_OBSERVED_DAY_STATISTICS}
        ),
        tuple(
            (section in new, new.get(section))
            for section in _BATTERY_MONTH_FRESHNESS_SECTIONS
            if section
            not in {PAYLOAD_VERIFIED_DAY_STATISTICS, PAYLOAD_OBSERVED_DAY_STATISTICS}
        ),
    )


def _stat_period_evidence_equal(  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches, too-many-locals, too-many-statements]
    entity: JackeryStatSensor,
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    comparison_cache: _StatInputComparisonCache | None = None,
) -> bool:
    """Compare own energy sources without unrelated metrics or unbounded history."""
    description = entity.entity_description
    compare = (
        _stat_inputs_equal
        if comparison_cache is None
        else comparison_cache.fields_equal
    )
    if description.key == "battery_to_ac_month_energy":
        unchanged = _battery_period_evidence_equal(
            old, new, comparison_cache=comparison_cache
        )
    elif description.key in _PV_PERIOD_FRESHNESS_KEYS:
        unchanged = _pv_evidence_equal(
            old,
            new,
            is_year=description.section == f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
            comparison_cache=comparison_cache,
        )
    elif _stat_rebase_supported(entity):
        identity = _NATIVE_OWN_ENERGY_SOURCES.get(description.key)
        prefix = (
            identity[0]
            if identity is not None
            else description.section.rpartition("_")[0]
        )
        sections = {
            prefix,
            description.section,
            PAYLOAD_LOCAL_DAILY_ENERGY,
            *_PV_OWNER_SECTIONS,
        }
        sections.update(
            f"{prefix}_{period}"
            for period in (
                DATE_TYPE_DAY,
                DATE_TYPE_WEEK,
                DATE_TYPE_MONTH,
                DATE_TYPE_YEAR,
            )
        )
        sections.update(section for section, _ in description.fallback_sources)
        unchanged = True
        for section in sections:
            first = old.get(section)
            second = new.get(section)
            # Live property dictionaries also carry unrelated power updates;
            # this lifetime descriptor depends only on its declared own field.
            if section == PAYLOAD_PROPERTIES:
                if type(first) is not dict or type(second) is not dict:
                    unchanged = compare(
                        (section in old, first), (section in new, second)
                    )
                else:
                    key = description.stat_key
                    unchanged = compare(
                        (key in first, first.get(key)), (key in second, second.get(key))
                    )
            else:
                unchanged = compare((section in old, first), (section in new, second))
            if not unchanged:
                break
    else:
        return False
    if not unchanged:
        return False
    identity = _NATIVE_OWN_ENERGY_SOURCES.get(description.key)
    prefix, metric_key = (
        identity
        if identity is not None
        else (description.section.rpartition("_")[0], description.stat_key)
    )
    local_today = getattr(entity, "_local_today", None)
    today = local_today() if callable(local_today) else None
    period = getattr(entity, "_reset_period", None)
    begin = None
    if isinstance(today, date):
        if period == DATE_TYPE_DAY:
            begin = today
        elif period == DATE_TYPE_WEEK:
            begin = today - timedelta(days=today.weekday())
        elif period == DATE_TYPE_MONTH:
            begin = today.replace(day=1)
        elif period == DATE_TYPE_YEAR:
            begin = today.replace(month=1, day=1)
    # Check one own record at a time. 120 days x all families exceeds the
    # generic 4096-leaf budget even though a single sensor uses one metric.
    for history_key in (
        PAYLOAD_VERIFIED_DAY_STATISTICS,
        PAYLOAD_OBSERVED_DAY_STATISTICS,
    ):
        first = old.get(history_key, {})
        second = new.get(history_key, {})
        if type(first) is not dict or type(second) is not dict:
            return False
        for day in first.keys() | second.keys():
            if type(day) is not str:
                return False
            if isinstance(today, date) and begin is not None:
                try:
                    evidence_day = date.fromisoformat(day)
                except ValueError:
                    return False
                if not begin <= evidence_day <= today:
                    continue
            first_families = first.get(day, {})
            second_families = second.get(day, {})
            if type(first_families) is not dict or type(second_families) is not dict:
                return False
            first_family = first_families.get(prefix, {})
            second_family = second_families.get(prefix, {})
            if type(first_family) is not dict or type(second_family) is not dict:
                return False
            key = metric_key
            if not compare(
                (key in first_family, first_family.get(key)),
                (key in second_family, second_family.get(key)),
            ):
                return False
    return True


def _build_stat_refreshes(
    requests: tuple[_StatRefreshRequest, ...],
) -> tuple[_StatRefreshResult, ...]:
    """Compute one coordinator batch with shared period memoization."""
    period_cache: _PeriodResolutionCache = {}
    comparison_cache = _StatInputComparisonCache()
    results: list[_StatRefreshResult] = []
    for request in requests:
        try:
            # These payloads were detached together before the executor await.
            # Keep the cache out of the original request and returned result;
            # the live rebase path must compare freshly after that boundary.
            context = replace(request.context, comparison_cache=comparison_cache)
            snapshot = request.entity._refresh_cache(context, period_cache)  # ruff:ignore[private-member-access]
        except Exception as err:  # ruff:ignore[blind-except]  # isolate one entity from the shared executor batch
            results.append(_StatRefreshResult(request=request, error=err))
        else:
            results.append(_StatRefreshResult(request=request, snapshot=snapshot))
    return tuple(results)


class _IncompleteStatRefreshBatchError(RuntimeError):
    """Executor result did not preserve the submitted request batch."""

    def __init__(self) -> None:
        """Build a stable internal batch-contract error."""
        super().__init__("Statistic refresh executor returned an incomplete batch")


async def _async_execute_stat_refreshes(
    hass: HomeAssistant,
    requests: tuple[_StatRefreshRequest, ...],
) -> tuple[_StatRefreshResult, ...]:
    """Execute and validate one stat refresh batch outside the drain loop."""
    results = await hass.async_add_executor_job(_build_stat_refreshes, requests)
    if len(results) != len(requests) or any(
        result.request is not request
        for result, request in zip(results, requests, strict=True)
    ):
        raise _IncompleteStatRefreshBatchError
    return results


class _StatRefreshBatch:
    """Coalesce statistic entities into one executor job per coordinator update."""

    def __init__(self) -> None:
        """Initialize the pending refresh collection."""
        self._pending: dict[JackeryStatSensor, tuple[int, bool]] = {}
        self._task: asyncio.Task[None] | None = None
        self._failure_signatures: dict[
            JackeryStatSensor,
            tuple[str, type[BaseException], str],
        ] = {}
        self._pv_freshness_failures: WeakKeyDictionary[
            JackeryStatSensor, set[tuple[str, str]]
        ] = WeakKeyDictionary()
        self._pv_active_entities: dict[str, WeakSet[JackeryStatSensor]] = {}
        self._pv_owner_observations: dict[str, tuple[object, ...]] = {}
        self._pv_owner_epochs: dict[str, int] = {}

    @staticmethod
    @callback
    def _is_current(entity: JackeryStatSensor, generation: int) -> bool:
        """The whether a result may still affect this live entity."""
        return entity._cache_refresh_active and entity._cache_generation == generation  # ruff:ignore[private-member-access]

    @callback
    def _ensure_task(self, entity: JackeryStatSensor) -> None:
        """Start an unload-managed drain task or fail queued refreshes."""
        if self._task is not None:
            return
        hass = getattr(entity, "hass", None) or entity.coordinator.hass
        refresh_coro = self._async_run(hass)
        try:
            self._task = entity.coordinator.entry.async_create_background_task(
                hass,
                refresh_coro,
                "Jackery statistic sensor cache refresh",
            )
        except Exception as err:  # ruff:ignore[blind-except]  # task creation failure must be visible per entity
            refresh_coro.close()
            pending = self._pending
            self._pending = {}
            self._fail_pending(pending, err, "start statistic refresh batch")

    @callback
    def request(self, entity: JackeryStatSensor, *, write_state: bool) -> None:
        """Queue the latest entity generation and start one shared task."""
        if not entity._cache_refresh_active:  # ruff:ignore[private-member-access]
            return
        entity._cache_generation += 1  # ruff:ignore[private-member-access]
        if _stat_rebase_supported(entity):
            self._pv_active_entities.setdefault(
                entity._device_id,  # ruff: ignore[private-member-access]
                WeakSet(),
            ).add(entity)
        self._observe_pv_owner(entity)
        pending = self._pending.get(entity)
        self._pending[entity] = (
            entity._cache_generation,  # ruff:ignore[private-member-access]
            write_state or (pending is not None and pending[1]),
        )
        self._ensure_task(entity)

    @callback
    def _invalidate_pv_owner_observation(self, device_id: str) -> None:
        self._pv_owner_epochs[device_id] = self._pv_owner_epochs.get(device_id, 0) + 1
        self._pv_owner_observations.pop(device_id, None)

    @callback
    def _observe_pv_owner(self, entity: JackeryStatSensor) -> None:
        """Remember observed owner transitions, including an A-to-B-to-A update."""
        if not _stat_rebase_supported(entity):
            return
        device_id = entity._device_id  # ruff: ignore[private-member-access]
        coordinator = entity.coordinator
        index = getattr(coordinator, "_device_index", None)
        record = index.get(device_id) if type(index) is dict else None
        try:
            payload_owner = _pv_payload_owner(entity)
        except _StatFreshnessUnavailableError as err:
            self._invalidate_pv_owner_observation(device_id)
            self._fail_pv_freshness(
                entity,
                entity._cache_generation,  # ruff: ignore[private-member-access]
                "capture statistic freshness fence",
                err,
            )
            return
        if type(record) is not dict or payload_owner is None:
            self._invalidate_pv_owner_observation(device_id)
            return
        device_meta = record.get(PAYLOAD_DEVICE_META)
        system_meta = record.get(PAYLOAD_SYSTEM_META)
        if type(device_meta) is not dict or type(system_meta) is not dict:
            self._invalidate_pv_owner_observation(device_id)
            return
        observed = (
            (FIELD_SYSTEM_ID in record, record.get(FIELD_SYSTEM_ID)),
            *(
                (field in device_meta, device_meta.get(field))
                for field in _PV_OWNER_FIELDS
            ),
            *(
                (field in system_meta, system_meta.get(field))
                for field in _PV_OWNER_FIELDS
            ),
            payload_owner,
        )
        try:  # ruff: ignore[too-many-statements-in-try-clause]  # Owner evidence must be evaluated atomically.
            previous = self._pv_owner_observations.get(device_id)
            if not _stat_inputs_equal(observed, observed):
                self._invalidate_pv_owner_observation(device_id)
                self._fail_pv_freshness(
                    entity,
                    entity._cache_generation,  # ruff: ignore[private-member-access]
                    "capture statistic freshness fence",
                    _StatFreshnessUnavailableError(),
                )
                return
            if previous is None or not _stat_inputs_equal(previous, observed):
                self._pv_owner_epochs[device_id] = (
                    self._pv_owner_epochs.get(device_id, 0) + 1
                )
                self._pv_owner_observations[device_id] = deepcopy(observed)
        except _StatFreshnessUnavailableError as err:
            self._invalidate_pv_owner_observation(device_id)
            self._fail_pv_freshness(
                entity,
                entity._cache_generation,  # ruff: ignore[private-member-access]
                "capture statistic freshness fence",
                err,
            )

    @callback
    def discard(self, entity: JackeryStatSensor) -> None:
        """Discard all work for an entity and invalidate in-flight results."""
        entity._cache_refresh_active = False  # ruff:ignore[private-member-access]
        entity._cache_generation += 1  # ruff:ignore[private-member-access]
        entity._cache_lifecycle_epoch = (  # ruff:ignore[private-member-access]
            getattr(entity, "_cache_lifecycle_epoch", 0) + 1
        )
        self._pending.pop(entity, None)
        self._failure_signatures.pop(entity, None)
        self._pv_freshness_failures.pop(entity, None)
        device_id = entity._device_id  # ruff: ignore[private-member-access]
        active = self._pv_active_entities.get(device_id)
        if active is not None:
            active.discard(entity)
            if not active:
                self._clear_pv_owner(device_id)

    def _clear_pv_owner(self, device_id: str) -> None:
        self._pv_active_entities.pop(device_id, None)
        self._pv_owner_observations.pop(device_id, None)
        self._pv_owner_epochs.pop(device_id, None)

    def _clear_inactive_pv_owners(self) -> None:
        for device_id, active in tuple(self._pv_active_entities.items()):
            if not active:
                self._clear_pv_owner(device_id)

    @callback
    def _fail_pv_freshness(
        self, entity: JackeryStatSensor, generation: int, stage: str, err: Exception
    ) -> None:
        """Log bounded freshness failures without including evidence values."""
        if not self._is_current(entity, generation):
            return
        signature = (stage, type(err).__name__)
        seen = self._pv_freshness_failures.setdefault(entity, set())
        if signature in seen:
            return
        if len(seen) >= _STAT_FRESHNESS_MAX_LOGGED_CAUSES:
            signature = ("compare additional freshness evidence", "Other")
            if signature in seen:
                return
        seen.add(signature)
        _LOGGER.warning(
            "Unable to %s for statistic sensor %s (%s)",
            signature[0],
            entity.unique_id,
            signature[1],
        )

    @staticmethod
    def _log_failure(
        entity: JackeryStatSensor,
        stage: str,
        error: BaseException,
    ) -> None:
        """Log one entity-scoped failure without aborting its batch peers."""
        _LOGGER.error(
            "Failed to %s for statistic sensor %s",
            stage,
            entity.unique_id,
            exc_info=(type(error), error, error.__traceback__),
        )

    @callback
    def _fail_entity(
        self,
        entity: JackeryStatSensor,
        generation: int,
        error: BaseException,
        stage: str,
    ) -> None:
        """Log failures only when no newer generation superseded the error."""
        if not self._is_current(entity, generation):
            return
        signature = (stage, type(error), str(error))
        if self._failure_signatures.get(entity) == signature:
            return
        self._failure_signatures[entity] = signature
        self._log_failure(entity, stage, error)

    @callback
    def _fail_pending(
        self,
        pending: dict[JackeryStatSensor, tuple[int, bool]],
        error: BaseException,
        stage: str,
    ) -> None:
        """Deterministically fail every current entity in a drained batch."""
        for entity, (generation, _write_state) in pending.items():
            self._fail_entity(entity, generation, error, stage)

    @callback
    def _capture_requests(
        self,
        pending: dict[JackeryStatSensor, tuple[int, bool]],
    ) -> tuple[_StatRefreshRequest, ...]:
        """Deep-copy each device payload once and capture entity contexts."""
        payloads: dict[str, dict[str, Any]] = {}
        payload_errors: dict[str, Exception] = {}
        requests: list[_StatRefreshRequest] = []
        for entity, (generation, write_state) in pending.items():
            if not self._is_current(entity, generation):
                continue
            device_id = entity._device_id  # ruff:ignore[private-member-access]
            if device_id not in payloads and device_id not in payload_errors:
                try:
                    payloads[device_id] = deepcopy(entity._payload)  # ruff:ignore[private-member-access]
                except Exception as err:  # ruff:ignore[blind-except]  # one failed device snapshot must not strand peers
                    payload_errors[device_id] = err
            if (error := payload_errors.get(device_id)) is not None:
                self._fail_entity(
                    entity,
                    generation,
                    error,
                    "capture statistic device payload",
                )
                continue
            try:
                energy_owner_token = None
                if entity.entity_description.device_class == SensorDeviceClass.ENERGY:
                    _invalidate_own_lifetime_parent_cache(entity)
                    energy_owner_token = getattr(
                        entity, "_own_lifetime_parent_token", None
                    )
                context = entity._capture_refresh_context(payloads[device_id])  # ruff:ignore[private-member-access]
            except Exception as err:  # ruff:ignore[blind-except]  # isolate per-entity event-loop capture
                self._fail_entity(
                    entity,
                    generation,
                    err,
                    "capture statistic refresh context",
                )
                continue
            try:
                pv_fence = _capture_stat_period_fence(
                    entity, self._pv_owner_epochs.get(device_id, 0)
                )
            except Exception as err:  # ruff: ignore[blind-except]  # No rebase without a complete owner fence.
                pv_fence = None
                self._fail_pv_freshness(
                    entity,
                    generation,
                    "capture statistic freshness fence",
                    err,
                )
            requests.append(
                _StatRefreshRequest(
                    entity=entity,
                    generation=generation,
                    context=context,
                    write_state=write_state,
                    pv_fence=pv_fence,
                    energy_owner_token=energy_owner_token,
                )
            )
        return tuple(requests)

    @callback
    def _pv_period_rebases(
        self, results: tuple[_StatRefreshResult, ...]
    ) -> dict[JackeryStatSensor, tuple[tuple[int, bool], _PvPeriodFence]]:
        """Compare shared period evidence once per device before any state writes."""
        comparisons: dict[tuple[int, int, str], bool] = {}
        # This synchronous preflight has no state writes. Every result
        # still compares uncached immediately before its own apply.
        source_comparisons = _StatInputComparisonCache()
        allowed: dict[JackeryStatSensor, tuple[tuple[int, bool], _PvPeriodFence]] = {}
        for result in results:
            request = result.request
            entity = request.entity
            pending = self._pending.get(entity)
            old_fence = request.pv_fence
            if (
                old_fence is None
                or pending is None
                or self._is_current(entity, request.generation)
                or not self._is_current(entity, pending[0])
            ):
                continue
            try:  # ruff: ignore[too-many-statements-in-try-clause]  # All evidence must share one synchronous snapshot.
                current_fence = _capture_stat_period_fence(
                    entity,
                    self._pv_owner_epochs.get(entity._device_id, 0),  # ruff: ignore[private-member-access]
                )
                if current_fence is None or not _pv_fences_equal(
                    old_fence, current_fence
                ):
                    continue
                payload = entity._payload  # ruff: ignore[private-member-access]
                current = entity._capture_refresh_context(payload)  # ruff: ignore[private-member-access]
                if not _pv_contexts_equal(request.context, current):
                    continue
                key = (
                    id(request.context.payload),
                    id(payload),
                    entity.entity_description.section
                    + ":"
                    + entity.entity_description.stat_key,
                )
                if key not in comparisons:
                    comparisons[key] = _stat_period_evidence_equal(
                        entity,
                        request.context.payload,
                        payload,
                        comparison_cache=source_comparisons,
                    )
                if comparisons[key]:
                    allowed[entity] = (pending, current_fence)
            except Exception as err:  # ruff: ignore[blind-except]  # Unsafe evidence must use the normal next batch.
                self._fail_pv_freshness(
                    entity,
                    pending[0],
                    "compare statistic freshness evidence",
                    err,
                )
                continue
        return allowed

    @callback
    def _apply_result(self, result: _StatRefreshResult) -> bool:  # ruff: ignore[too-many-return-statements]
        """Apply and write one result without affecting other entities."""
        request = result.request
        entity = request.entity
        if not self._is_current(entity, request.generation):
            return False
        if request.energy_owner_token is not None:
            _invalidate_own_lifetime_parent_cache(entity)
            if not _same_energy_owner_tokens(
                request.energy_owner_token,
                getattr(entity, "_own_lifetime_parent_token", None),
            ):
                self.request(entity, write_state=request.write_state)
                return False
        if result.error is not None:
            self._fail_entity(
                entity,
                request.generation,
                result.error,
                "compute statistic cache",
            )
            entity._refresh_availability_cache()  # ruff:ignore[private-member-access]
            if request.write_state:
                entity._write_cached_state()  # ruff:ignore[private-member-access]
            return False
        snapshot = result.snapshot
        if snapshot is None:
            self._fail_entity(
                entity,
                request.generation,
                RuntimeError("Statistic refresh returned no cache snapshot"),
                "compute statistic cache",
            )
            return False
        passive_callback = getattr(entity, "_passive_cache_callback", None)
        previous = (
            (entity.native_value, entity._cached_source_section)  # ruff: ignore[private-member-access] - owning batch compares cached output
            if not request.write_state and passive_callback is not None
            else None
        )
        try:
            entity._apply_cache_snapshot(snapshot)  # ruff:ignore[private-member-access]
            entity._refresh_availability_cache()  # ruff:ignore[private-member-access]
        except Exception as err:  # ruff:ignore[blind-except]  # apply failures are entity-local
            self._fail_entity(
                entity,
                request.generation,
                err,
                "apply statistic cache",
            )
            return False
        if request.write_state:
            try:
                entity._write_cached_state()  # ruff:ignore[private-member-access]
            except Exception as err:  # ruff:ignore[blind-except]  # state writes must not abort peer entities
                self._fail_entity(
                    entity,
                    request.generation,
                    err,
                    "write statistic state",
                )
                return False
        elif (
            passive_callback is not None
            and previous != (entity.native_value, entity._cached_source_section)  # ruff: ignore[private-member-access]
        ):
            passive_callback()
        self._failure_signatures.pop(entity, None)
        return True

    @callback
    def _cancel_all(
        self,
        in_flight: dict[JackeryStatSensor, tuple[int, bool]],
    ) -> None:
        """Discard pending/in-flight work when the entry task is cancelled."""
        entities = set(in_flight) | set(self._pending)
        for entity in entities:
            self.discard(entity)

    @callback
    def _apply_batch_result(
        self,
        result: _StatRefreshResult,
        rebase: tuple[tuple[int, bool], _PvPeriodFence] | None,
    ) -> None:
        """Recheck reentrant changes before applying one batch result."""
        pending: tuple[int, bool] | None = None
        try:  # ruff: ignore[too-many-statements-in-try-clause]  # Keep revalidation and application in one failure boundary.
            entity = result.request.entity
            if rebase is not None:
                pending, fence = rebase
                current_fence = _capture_stat_period_fence(
                    entity,
                    self._pv_owner_epochs.get(entity._device_id, 0),  # ruff: ignore[private-member-access]
                )
                current_context = entity._capture_refresh_context(  # ruff: ignore[private-member-access]
                    entity._payload  # ruff: ignore[private-member-access]
                )
                if self._pending.get(entity) is not pending:
                    return
                if not self._is_current(entity, pending[0]) or current_fence is None:
                    return
                if not _pv_fences_equal(fence, current_fence):
                    return
                if not _pv_contexts_equal(result.request.context, current_context):
                    return
                if not _stat_period_evidence_equal(
                    entity,
                    result.request.context.payload,
                    current_context.payload,
                ):
                    return
                result = replace(
                    result,
                    request=replace(
                        result.request,
                        generation=pending[0],
                        write_state=result.request.write_state or pending[1],
                    ),
                )
            applied = self._apply_result(result)
            if applied and pending is not None:
                if self._pending.get(entity) is pending:
                    self._pending.pop(entity)
                self._pv_freshness_failures.pop(entity, None)
        except Exception as err:  # ruff: ignore[blind-except]  # Isolate each result.
            if pending is not None:
                self._fail_pv_freshness(
                    result.request.entity,
                    pending[0],
                    "apply statistic freshness evidence",
                    err,
                )
            else:
                self._fail_entity(
                    result.request.entity,
                    result.request.generation,
                    err,
                    "apply statistic refresh result",
                )

    async def _async_run(self, hass: HomeAssistant) -> None:
        """Drain coalesced generations through HA's managed executor."""
        in_flight: dict[JackeryStatSensor, tuple[int, bool]] = {}
        try:  # ruff:ignore[too-many-statements-in-try-clause]
            await asyncio.sleep(0)
            while self._pending:
                in_flight = self._pending
                self._pending = {}
                try:
                    requests = self._capture_requests(in_flight)
                except Exception as err:  # ruff:ignore[blind-except]  # batch capture failure must be entity-scoped
                    self._fail_pending(
                        in_flight,
                        err,
                        "capture statistic refresh batch",
                    )
                    in_flight = {}
                    continue
                if not requests:
                    in_flight = {}
                    continue
                try:
                    results = await _async_execute_stat_refreshes(hass, requests)
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # ruff:ignore[blind-except]  # executor failure affects the whole drained batch
                    for request in requests:
                        self._fail_entity(
                            request.entity,
                            request.generation,
                            err,
                            "execute statistic refresh batch",
                        )
                    in_flight = {}
                    continue
                rebases = self._pv_period_rebases(results)
                for result in results:
                    self._apply_batch_result(result, rebases.get(result.request.entity))
                in_flight = {}
        except asyncio.CancelledError:
            self._cancel_all(in_flight)
            raise
        except Exception as err:  # ruff:ignore[blind-except]  # report every affected entity on batch failure
            affected = dict(in_flight)
            affected.update(self._pending)
            self._pending = {}
            self._fail_pending(affected, err, "run statistic refresh batch")
        finally:
            self._task = None
            if self._pending:
                self._ensure_task(next(iter(self._pending)))
            self._clear_inactive_pv_owners()


_STAT_REFRESH_BATCHES: WeakKeyDictionary[
    JackerySolarVaultCoordinator,
    _StatRefreshBatch,
] = WeakKeyDictionary()


@callback
def _stat_refresh_batch_for(
    coordinator: JackerySolarVaultCoordinator,
) -> _StatRefreshBatch:
    """The the shared statistic refresh batch for a coordinator."""
    batch = _STAT_REFRESH_BATCHES.get(coordinator)
    if batch is None:
        batch = _StatRefreshBatch()
        _STAT_REFRESH_BATCHES[coordinator] = batch
    return batch


class JackeryBatteryPackSensor(JackeryEntity, RestoreSensor):
    """Per battery-pack sensor from MQTT BatteryPackSub plus OTA metadata."""

    entity_description: JackeryBatteryPackSensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        identity: _BatteryPackEntityIdentity,
        description: JackeryBatteryPackSensorDescription,
        enabled_default: bool = True,
    ) -> None:
        """Implementation details.

        Create a battery-pack sensor entity for a specific device and pack index
        based on the provided sensor description.

        Parameters:
            coordinator (JackerySolarVaultCoordinator): Coordinator providing
            polling/MQTT data and device payloads.
            device_id (str): Unique identifier for the parent Jackery device.
            pack_index (int): 1-based index of the battery pack within the device's
            battery pack list.
            pack_sn (str | None): Trusted pack serial used to pin physical identity.
            pack_key (str): Session-frozen stable unique/device ID suffix.
            description (JackeryBatteryPackSensorDescription): Metadata describing which
            pack field to expose and how to transform it.
            enabled_default (bool): Whether the entity should be enabled by default in
            the entity registry.
        """
        pack_index, pack_sn, pack_key = identity
        super().__init__(
            coordinator,
            device_id,
            f"{pack_key}_{description.key}",
        )
        self._pack_index = pack_index
        # A registry-migrated serial is pinned immediately. Anonymous packs pin
        # their first later serial without changing this session's unique ID.
        self._pack_sn = pack_sn
        self._pack_key = pack_key
        self.entity_description = description
        self._attr_translation_key = description.translation_key
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_entity_registry_enabled_default = enabled_default
        self._cached_native_value: StateType = None
        self._cached_attrs: dict[str, Any] = {"pack_index": pack_index}
        self._cached_pack_sn: str | None = pack_sn
        self._cached_pack_slot: tuple[str, str, int, int] | None = None
        self._pack_identity_uncertain = False
        self._pack_identity_generation = 0
        self._restored_lifetime_value: float | None = None
        self._cache_refresh_active = False
        self._passive_cache_unsubscribe: Callable[[], None] | None = None
        self._passive_cache_callback: Callable[[], None] | None = None

    @property
    def _pack(self) -> dict[str, Any]:
        """The the battery pack dictionary for this entity's configured pack index.

        Selects the pack at the 1-based index stored on the entity from the payload's
        PAYLOAD_BATTERY_PACKS list. Returns an empty dict when the packs section is
        missing, not a list, the index is out of range, or the selected entry is not a
        dict.

        Returns:
            dict: The battery pack dictionary when available, otherwise an empty dict.
        """
        packs = self._payload.get(PAYLOAD_BATTERY_PACKS) or []
        # Keep the physical HTTP pack order for the initial positional lookup.
        # Once known, the pack serial pins later values across MQTT reordering.
        pack_dicts = sorted_battery_pack_payloads(packs)
        # Prefer matching by the pack's own serial: the SN-keyed merge sink can
        # reorder the list between polls, and index-only lookup then made this
        # entity read a sibling pack's values or flip to Unknown.
        if self._pack_sn is not None:
            matches = [
                pack
                for pack in pack_dicts
                if battery_pack_serial(pack) == self._pack_sn
            ]
            return matches[0] if len(matches) == 1 else {}
        try:
            pack = pack_dicts[self._pack_index - 1]
        except IndexError:
            return {}
        # Pin the serial on first resolution so subsequent polls track by SN.
        sn = battery_pack_serial(pack)
        serial_keys = [
            stable_subdevice_key("battery_pack", candidate_sn, self._pack_index)
            for candidate in pack_dicts
            if (candidate_sn := battery_pack_serial(candidate)) is not None
        ]
        if (
            sn is not None
            and serial_keys.count(
                stable_subdevice_key("battery_pack", sn, self._pack_index)
            )
            == 1
        ):
            self._pack_sn = sn
        return pack

    def _value_from_pack(self, pack: dict[str, Any]) -> StateType:
        """Extract and transform this entity's value from a battery-pack payload."""
        # A head/stack cell temperature cannot establish this pack's own value.
        return _battery_pack_description_value(pack, self.entity_description)

    def _attrs_from_pack(self, pack: dict[str, Any]) -> dict[str, Any]:
        """Build a dictionary of state attributes derived from a battery pack payload.

        Parameters:
            pack (dict[str, Any]): The battery pack payload dictionary.

        Returns:
            dict[str, Any]: Attribute mapping that always includes `pack_index` and
            conditionally
            includes communication fields (`FIELD_COMM_STATE`, `FIELD_COMM_MODE`) for
            normal sensors.
            For diagnostic-category entities, includes a larger set of
            update/version/communication/diagnostic
            keys when present in the payload.
        """
        attrs: dict[str, Any] = {"pack_index": self._pack_index}
        if isinstance(slot := pack.get("_jackery_unbound_group_slot"), dict):
            attrs.update({
                "identity_uncertain": True,
                "identity_resolution": "source_group_position",
                "group_source": slot.get("source"),
                "group_index": slot.get("index"),
                "group_count": slot.get("count"),
            })
        if serial := battery_pack_serial(pack) or self._pack_sn:
            attrs["pack_serial_number"] = serial
            attrs.update(
                self.layer_source_attributes(
                    PAYLOAD_BATTERY_PACKS,
                    self.entity_description.field,
                    identity=serial,
                )
            )
        if self.entity_description.entity_category == EntityCategory.DIAGNOSTIC:
            cell_temperature = _div(10)(safe_float(pack.get(FIELD_CELL_TEMP)))
            if cell_temperature is not None:
                attrs["cell_temperature"] = cell_temperature
        if self.entity_description.entity_category != EntityCategory.DIAGNOSTIC:
            for key in (FIELD_COMM_STATE, FIELD_COMM_MODE):
                if key in pack:
                    attrs[key] = pack.get(key)
            return attrs
        for key in (
            FIELD_IS_FIRMWARE_UPGRADE,
            FIELD_VERSION,
            FIELD_CURRENT_VERSION,
            FIELD_UPDATE_STATUS,
            FIELD_TARGET_VERSION,
            FIELD_TARGET_MODULE_VERSION,
            FIELD_UPDATE_CONTENT,
            FIELD_UPGRADE_TYPE,
            FIELD_COMM_STATE,
            FIELD_COMM_MODE,
            FIELD_EC,
            FIELD_IT,
            FIELD_OT,
        ):
            if key in pack:
                attrs[key] = pack.get(key)
        return attrs

    def _sync_device_version(self, pack: dict[str, Any]) -> None:
        """Update firmware metadata that arrived after entity registration."""
        version = first_nonblank_text(
            pack.get(FIELD_VERSION),
            pack.get(FIELD_CURRENT_VERSION),
        )
        hass = getattr(self, "hass", None)
        entry = getattr(self.coordinator, "config_entry", None)
        if version is None or hass is None or entry is None:
            return
        registry = dr.async_get(hass)
        device = (
            registry.async_get_device_by_identifier(
                (DOMAIN, f"{self._device_id}_{self._pack_key}"),
                entry.entry_id,
            )
            if hasattr(registry, "async_get_device_by_identifier")
            else registry.async_get_device(
                identifiers={(DOMAIN, f"{self._device_id}_{self._pack_key}")}
            )
        )
        if device is not None and device.sw_version != version:
            registry.async_update_device(device.id, sw_version=version)

    def _refresh_cache(self) -> None:  # ruff: ignore[too-many-branches]
        """Implementation details.

        Refresh the cached native value and extra state attributes from the current
        battery pack.

        This updates self._cached_native_value and self._cached_attrs using the current
        pack snapshot; intended to be run once per coordinator update.
        """
        pack = self._pack
        slot = pack.get("_jackery_unbound_group_slot")
        slot_token = None
        if slot is not None:
            if (
                isinstance(slot, dict)  # ruff: ignore[too-many-boolean-expressions]
                and isinstance(slot.get("parent_sn"), str)
                and slot["parent_sn"].strip()
                and isinstance(slot.get("source"), str)
                and slot["source"].strip()
                and type(slot.get("index")) is int
                and type(slot.get("count")) is int
                and 1 <= slot["index"] <= slot["count"]
            ):
                slot_token = (
                    slot["parent_sn"],
                    slot["source"],
                    slot["index"],
                    slot["count"],
                )
            else:
                # A malformed internal slot cannot prove ownership of its values.
                pack = {}
        previous_slot = getattr(self, "_cached_pack_slot", None)
        if (
            getattr(self, "_cached_pack_sn", self._pack_sn) != self._pack_sn
            or previous_slot != slot_token
        ):
            # Neither a serial nor an uncertain group slot transfers old values.
            self._pack_identity_generation = (
                getattr(self, "_pack_identity_generation", 0) + 1
            )
            self._cached_native_value = None
            self._restored_lifetime_value = None
        self._cached_pack_sn = self._pack_sn
        self._cached_pack_slot = slot_token
        # A grouped index is not a physical battery identity: its row can
        # change packs without changing the token. Keep it uncertain through
        # source outages until a real serial is confirmed.
        identity_uncertain = slot_token is not None or (
            getattr(self, "_pack_identity_uncertain", False) and self._pack_sn is None
        )
        self._pack_identity_uncertain = identity_uncertain
        self._attr_state_class = (
            None if identity_uncertain else self.entity_description.state_class
        )
        self._sync_device_version(pack)
        live_value = self._value_from_pack(pack)
        candidate = live_value
        attrs = self._attrs_from_pack(pack)
        if identity_uncertain and "identity_uncertain" not in attrs:
            attrs.update({"identity_uncertain": True, "identity_resolution": "unbound"})
        if (
            not identity_uncertain
            and live_value is None
            and getattr(self, "_restored_lifetime_value", None) is not None
        ):
            candidate = self._restored_lifetime_value
            attrs = {
                **attrs,
                "value_restored": True,
                "restore_reason": (
                    "pack_reports_on_change_only"
                    if self.entity_description.field in _PACK_ON_CHANGE_FIELDS
                    else "lifetime_counter_source_unavailable"
                ),
            }
        elif (
            live_value is None  # ruff: ignore[too-many-boolean-expressions]
            and self._cached_native_value is not None
            and self.entity_description.field in _PACK_ON_CHANGE_FIELDS
            and not identity_uncertain
            and (bool(pack) or self._pack_sn is not None)
        ):
            candidate = self._cached_native_value
            attrs = {
                **(self._cached_attrs if not pack else {}),
                **attrs,
                "value_retained_on_change": True,
            }
            if not pack:
                attrs["retained_source_missing"] = True
        guarded = (
            candidate
            if identity_uncertain
            else guard_total_increasing_jitter(
                self._cached_native_value,
                candidate,
                self.entity_description,
            )
        )
        if guarded is not candidate:
            attrs = {**attrs, "lifetime_value_retained": True}
        self._cached_native_value = guarded
        self._cached_attrs = attrs
        if live_value is not None:
            self._restored_lifetime_value = None
        if guarded is None:
            if not pack:
                attrs["unknown_reason"] = "pack_source_missing"
            elif (
                self.entity_description.field in _PACK_ON_CHANGE_FIELDS
                and self.entity_description.field not in pack
            ):
                attrs["unknown_reason"] = "awaiting_change_frame"
            else:
                attrs["unknown_reason"] = "source_value_unavailable"

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh cached BatteryPackSub values before HA writes state."""
        self._refresh_cache()
        super()._handle_coordinator_update()

    @callback
    def async_start_passive_cache(
        self, on_update: Callable[[], None]
    ) -> Callable[[], None] | None:
        """Maintain disabled pack values without writing a native HA state."""
        if self._cache_refresh_active:
            return None
        self._cache_refresh_active = True
        self._passive_cache_callback = on_update
        self._passive_cache_unsubscribe = self.coordinator.async_add_listener(
            self._refresh_passive_cache
        )
        try:
            self._refresh_cache()
        except Exception:
            self._stop_passive_cache()
            raise
        return self._stop_passive_cache

    @callback
    def _refresh_passive_cache(self) -> None:
        previous = self._cached_native_value
        self._refresh_cache()
        if (
            self._passive_cache_callback is not None
            and previous != self._cached_native_value
        ):
            self._passive_cache_callback()

    @callback
    def _stop_passive_cache(self) -> None:
        unsubscribe = self._passive_cache_unsubscribe
        if unsubscribe is None:
            return
        self._passive_cache_unsubscribe = None
        self._passive_cache_callback = None
        self._cache_refresh_active = False
        unsubscribe()

    async def async_added_to_hass(self) -> None:
        """Prime the cache before CoordinatorEntity writes the initial state."""
        self._stop_passive_cache()
        self._refresh_cache()
        self._cache_refresh_active = True
        restore_identity = (
            self._pack_sn,
            getattr(self, "_pack_identity_generation", 0),
        )
        try:
            await super().async_added_to_hass()
        except (Exception, asyncio.CancelledError):
            self._cache_refresh_active = False
            raise
        if not self._cache_refresh_active or restore_identity != (
            self._pack_sn,
            getattr(self, "_pack_identity_generation", 0),
        ):
            return
        if self._pack_sn is None:
            # An index can be reused for a different physical battery, even
            # after restart. Restore requires a confirmed pack serial.
            return
        if (
            self.entity_description.state_class is SensorStateClass.TOTAL_INCREASING
            and self.entity_description.device_class is SensorDeviceClass.ENERGY
            and self._cached_native_value is None
        ):
            restored = await _async_restored_lifetime_energy_value(
                self,
                self.entity_description.native_unit_of_measurement,
            )
        elif (
            self.entity_description.field in _PACK_ON_CHANGE_FIELDS
            and self._cached_native_value is None
        ):
            restored = await _async_restored_pack_measurement_value(
                self,
                self.entity_description.native_unit_of_measurement,
            )
        else:
            return
        if (
            not self._cache_refresh_active
            or restore_identity
            != (
                self._pack_sn,
                getattr(self, "_pack_identity_generation", 0),
            )
            or self._cached_native_value is not None
        ):
            return
        self._restored_lifetime_value = restored
        self._refresh_cache()
        if self.native_value is not None:
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Stop serving the prepared cache after native entity removal."""
        self._stop_passive_cache()
        self._cache_refresh_active = False
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> StateType:
        """The entity's last cached native value.

        Returns:
            The cached native value from the most recent coordinator update, or `None`
            if unavailable.
        """
        return self._cached_native_value

    @property
    def device_info(self) -> DeviceInfo | ChildDeviceInfo:
        """Builds device registry metadata for this battery-pack entity.

        Returns:
            DeviceInfo: Device registry metadata containing:
                - identifiers: unique (DOMAIN, "<device_id>_battery_pack_<index>") tuple
                - manufacturer: constant manufacturer string
                - name: human-readable device name including pack index
                - model: pack model or fallback string
                - serial_number: pack serial when available
                - sw_version: firmware/version when available
                - via_device: tuple linking this pack to the main device
        """
        base_name = first_nonblank_text(
            self._system.get(FIELD_DEVICE_NAME),
            self._discovery.get(FIELD_DEVICE_NAME),
            self._properties.get(FIELD_WNAME),
            fallback=f"Jackery {self._device_id}",
        )
        pack = self._pack
        sn = first_nonblank_text(
            pack.get(FIELD_DEVICE_SN),
            pack.get(FIELD_DEV_SN),
            pack.get(FIELD_SN),
            self._pack_sn,
        )
        model = first_nonblank_text(
            pack.get(FIELD_MODEL),
            pack.get(FIELD_MODEL_NAME),
            pack.get(FIELD_TYPE_NAME),
            fallback="Jackery battery pack",
        )
        version = first_nonblank_text(
            pack.get(FIELD_VERSION),
            pack.get(FIELD_CURRENT_VERSION),
        )
        hass = getattr(self, "hass", None)
        language = getattr(getattr(hass, "config", None), "language", "en")
        pack_label = "Zusatzbatterie" if language.startswith("de") else "Battery pack"
        name = f"{base_name} {pack_label} {self._pack_index}"
        child = self._pack_child_device_info(name=name)
        if child is not None:
            return child
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{self._pack_key}")},
            manufacturer=MANUFACTURER,
            name=name,
            model=str(model),
            serial_number=str(sn) if sn else None,
            sw_version=str(version) if version else None,
        )
        self._apply_via_device(info)
        return info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Provide the entity's extra state attributes intended for diagnostics.

        Returns:
            dict[str, Any]: Mapping of diagnostic attribute names to their values (may
            be empty).
        """
        return self._cached_attrs


class JackerySmartPlugSensor(JackeryEntity, RestoreSensor):
    """Per smart-plug sensor from MQTT PlugSub payloads."""

    entity_description: JackerySmartPlugSensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        identity: _IndexedEntityIdentity,
        description: JackerySmartPlugSensorDescription,
    ) -> None:
        """Implementation details.

        Initialize a smart-plug sensor entity for a specific plug (by index and
        serial) using the provided sensor description.

        Parameters:
            device_id (str): Identifier of the parent Jackery device.
            plug_index (int): 1-based index of the plug within the device's smart_plugs
            array.
            plug_sn (str): Serial number of the physical smart plug; used to bind the
            entity to the correct plug when array order changes.
            description (JackerySmartPlugSensorDescription): Sensor description that
            provides keys, units, device/class metadata, and transforms.

        Notes:
            Builds and caches the per-plug `device_info` at construction time from the
            current plug payload.
        """
        plug_index, plug_sn, plug_key = identity
        super().__init__(
            coordinator,
            device_id,
            f"{plug_key}_{description.key}",
        )
        self._plug_index = plug_index
        self._plug_sn = plug_sn
        self._plug_key = plug_key
        self.entity_description = description
        self._attr_translation_key = description.translation_key
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_entity_registry_enabled_default = (
            description.entity_category != EntityCategory.DIAGNOSTIC
        )
        self._reset_period = description.reset_period
        self._cached_native_value: StateType = None
        self._cached_attrs: dict[str, Any] = {"plug_index": plug_index}
        self._restored_lifetime_value: float | None = None
        # Build the per-plug device_info once at construction (see PROTOCOL §8
        # and binary_sensor.py for the rationale).
        self._attr_device_info = self._build_smart_plug_device_info(
            plug_index, self._plug, plug_key
        )

    @property
    def _plug(self) -> dict[str, Any]:
        # Look the plug up by its captured serial so cloud-side re-ordering of
        # the plug array cannot reassign this entity to a different device.
        """Implementation details.

        Find the smart-plug payload that matches this entity's captured serial
        number.

        Searches the payload's smart plug list (sorted for stable ordering) and returns
        the plug dictionary whose serial equals the entity's stored plug serial.

        Returns:
            dict: The matching plug payload dictionary, or an empty dict if no match is
            found.
        """
        for plug in sorted_smart_plugs(self._payload.get(PAYLOAD_SMART_PLUGS)):
            if smart_plug_serial(plug) == self._plug_sn:
                return plug
        return {}

    def _value_from_plug(self, plug: dict[str, Any]) -> StateType:
        """Return the transformed sensor value from one plug payload."""
        if self.entity_description.key == "ip_address":
            return accessory_ip_address(plug)
        field = self.entity_description.field
        raw = plug.get(field)
        if raw is None:
            alias_map = {
                FIELD_IN_PW: FIELD_IP,
                FIELD_OUT_PW: FIELD_OP,
                FIELD_SWITCH_STATE: FIELD_SYS_SWITCH,
            }
            alias = alias_map.get(field)
            if alias:
                raw = plug.get(alias)
        if raw is None:
            return None
        return _sensor_state_value(self.entity_description.transform(raw))

    def _attrs_from_plug(self, plug: dict[str, Any]) -> dict[str, Any]:
        """Return non-sensitive diagnostic attributes for one plug payload."""
        attrs: dict[str, Any] = {"plug_index": self._plug_index}
        for key in (
            FIELD_DEVICE_NAME,
            FIELD_SCAN_NAME,
            FIELD_COMM_STATE,
            FIELD_COMM_MODE,
            FIELD_SWITCH_STATE,
            FIELD_SYS_SWITCH,
            FIELD_SOCKET_PRIORITY,
            FIELD_TODAY_ENERGY,
            FIELD_TOTAL_ENERGY,
            FIELD_VERSION,
            FIELD_SUB_TYPE,
            FIELD_DEV_TYPE,
            FIELD_PARAM,
            FIELD_LINK_TYPE,
        ):
            if key in plug:
                attrs[key] = plug.get(key)
        return attrs

    def _daily_energy_receipt_matches(
        self, plug: dict[str, Any], value: StateType
    ) -> bool:
        """Require the daily field's own dated receipt, independent of transport."""
        receipt = plug.get("_jackery_http_today_energy_receipt")
        owner = _energy_owner_identity(self)
        if not isinstance(receipt, dict) or owner is None or value is None:
            return False
        recorded = receipt.get("value")
        numeric = safe_float(value)
        if (
            numeric is None
            or isinstance(recorded, bool)
            or safe_float(recorded) != numeric
        ):
            return False
        if (
            receipt.get("field") != FIELD_TODAY_ENERGY  # ruff: ignore[too-many-boolean-expressions]
            or receipt.get("day") != _period_start(DATE_TYPE_DAY).date().isoformat()
            or receipt.get("parent_serial") != owner.get("parent_serial")
            or receipt.get("parent_epoch")
            != getattr(self.coordinator, "_http_parent_identity_epochs", {}).get(
                self._device_id, 0
            )
            or receipt.get("plug_serial") != owner.get("plug_serial")
            or not receipt.get("stat_id")
        ):
            return False
        system_id = receipt.get("system_id")
        if (
            system_id is not None
            and owner.get("system_id") is not None
            and system_id != owner["system_id"]
        ):
            return False
        resolver = getattr(self.coordinator, "_subdevice_stat_id", None)
        return (
            not callable(resolver)
            or resolver(self._payload, plug, dev_type=SUBDEVICE_DEV_TYPE_SOCKET)
            == receipt["stat_id"]
        )

    def _refresh_cache(self) -> None:
        """Refresh cached plug state and retain a lifetime total across gaps."""
        energy = self.entity_description.device_class == SensorDeviceClass.ENERGY
        if energy:
            _invalidate_own_lifetime_parent_cache(self)
            if self._reset_period == DATE_TYPE_DAY:
                day = _period_start(DATE_TYPE_DAY).date().isoformat()
                previous_day = getattr(self, "_plug_energy_snapshot_day", day)
                if previous_day != day:
                    self._cached_native_value = None
                    self._cached_attrs = {}
                    self._restored_lifetime_value = None
                self._plug_energy_snapshot_day = day
        plug = self._plug
        live_value = self._value_from_plug(plug)
        if (
            self._reset_period == DATE_TYPE_DAY
            and not self._daily_energy_receipt_matches(plug, live_value)
        ):
            live_value = None
        candidate = live_value
        attrs = self._attrs_from_plug(plug)
        if energy:
            owner_attrs = _energy_owner_attributes(self)
            attrs.update(owner_attrs)
            if not owner_attrs:
                live_value = candidate = None
        if live_value is None and self._restored_lifetime_value is not None:
            candidate = self._restored_lifetime_value
            attrs = {
                **attrs,
                "value_restored": True,
                "restore_reason": "lifetime_counter_source_unavailable",
            }
        guarded = guard_total_increasing_jitter(
            self._cached_native_value,
            candidate,
            self.entity_description,
        )
        if guarded is not candidate:
            attrs = {**attrs, "lifetime_value_retained": True}
        self._cached_native_value = guarded
        if guarded is None:
            attrs.pop("energy_day", None)
        self._cached_attrs = attrs
        if live_value is not None:
            self._restored_lifetime_value = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh cached smart-plug values before HA writes state."""
        self._refresh_cache()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime the cache and restore an unavailable lifetime counter."""
        self._refresh_cache()
        await super().async_added_to_hass()
        if (
            self.entity_description.state_class is SensorStateClass.TOTAL_INCREASING
            and self.entity_description.device_class is SensorDeviceClass.ENERGY
            and self._cached_native_value is None
        ):
            self._restored_lifetime_value = await _async_restored_lifetime_energy_value(
                self,
                self.entity_description.native_unit_of_measurement,
            )
            self._refresh_cache()
            if self.native_value is not None:
                self.async_write_ha_state()

    @property
    def native_value(self) -> StateType:
        """The cached value from the latest coordinator update."""
        return self._cached_native_value

    @property
    def last_reset(self) -> datetime | None:
        """Compute the start datetime of the configured reset period for this entity.

        Returns:
            datetime: The period start datetime for the configured reset period (local
            timezone), or `None` when no reset period is configured.
        """
        if (
            self.entity_description.state_class != SensorStateClass.TOTAL
            or self._reset_period is None
        ):
            return None
        return _period_start(self._reset_period)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Cached diagnostic attributes for the current smart plug."""
        return self._cached_attrs


class JackeryBreakerSensor(JackeryEntity, SensorEntity):
    """Expose one breaker-channel sensor."""

    _attr_has_entity_name = True
    """Per-circuit breaker sensor from MQTT QueryCircuitProperty payloads."""

    entity_description: JackeryBreakerSensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        identity: _IndexedEntityIdentity,
        description: JackeryBreakerSensorDescription,
    ) -> None:
        """Initialize a circuit breaker sensor entity."""
        breaker_index, breaker_id, breaker_key = identity
        super().__init__(coordinator, device_id, f"{breaker_key}_{description.key}")
        self.entity_description = description
        self._breaker_index = breaker_index
        self._breaker_id = breaker_id
        self._breaker_key = breaker_key
        # Build the per-breaker device_info once at construction.
        self._attr_device_info = self._build_breaker_device_info(
            breaker_index,
            self._breaker,
            breaker_key,
        )

    @property
    def _breaker(self) -> dict[str, Any]:
        """Find the breaker payload that matches this entity's captured index.

        Returns:
            dict[str, Any]: The payload dictionary for the matching breaker, or an
            empty dict if no matching breaker is found.
        """
        for breaker in sorted_circuits(self._payload.get(PAYLOAD_CIRCUIT_PROPERTY)):
            if circuit_id(breaker) == self._breaker_id:
                return breaker
        return {}

    @property
    def native_value(self) -> float | int | str | None:
        """Value of the described field from the breaker payload.

        Returns:
            The transformed field value, or `None` if the field is absent.
        """
        raw = self._breaker.get(self.entity_description.field)
        if raw is None:
            return None
        return cast("float | int | str | None", self.entity_description.transform(raw))

    def _build_breaker_device_info(
        self,
        index: int,
        breaker: dict[str, Any],
        breaker_key: str,
    ) -> DeviceInfo:
        """Build device registry metadata for one circuit breaker.

        Returns:
            DeviceInfo: Registry info linking the breaker to the parent device.
        """
        base_name = first_nonblank_text(
            self._system.get(FIELD_DEVICE_NAME),
            self._discovery.get(FIELD_DEVICE_NAME),
            self._properties.get(FIELD_WNAME),
            fallback=f"Jackery {self._device_id}",
        )
        name = breaker.get(FIELD_NM) or f"Sicherung {index}"
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{breaker_key}")},
            manufacturer=MANUFACTURER,
            name=f"{base_name} {name}",
            model="Jackery Sicherung",
        )
        self._apply_via_device(info)
        return info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The diagnostic state attributes for the breaker.

        Returns:
            dict[str, Any]: Mapping of attribute names to their current values.
        """
        attrs: dict[str, Any] = {"breaker_index": self._breaker_index}
        for key in (
            FIELD_NM,
            FIELD_IDX,
            FIELD_PC,
            FIELD_PR,
            FIELD_SPH,
            FIELD_SPH_PC,
            FIELD_SW,
        ):
            if key in self._breaker:
                attrs[key] = self._breaker.get(key)
        return attrs


class JackerySubdeviceAlarmSensor(JackeryEntity, SensorEntity):
    """Expose one subdevice alarm sensor."""

    _attr_has_entity_name = True
    """Per-subdevice alarm/event sensor."""

    entity_description: JackerySubdeviceAlarmSensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        identity: _IndexedEntityIdentity,
        description: JackerySubdeviceAlarmSensorDescription,
    ) -> None:
        """Initialize a subdevice alarm sensor.

        Parameters:
            sub_device_index (int): 1-based index in the subdevice list.
            sub_device_sn (str): Serial number for stable identification.
            sub_device_key (str): Prebuilt stable key for unique ID.
        """
        sub_device_index, sub_device_sn, sub_device_key = identity
        super().__init__(coordinator, device_id, f"{sub_device_key}_{description.key}")
        self.entity_description = description
        self._sub_device_index = sub_device_index
        self._sub_device_sn = sub_device_sn
        self._sub_device_key = sub_device_key
        self._attr_device_info = self._build_sub_device_device_info(
            sub_device_index,
            self._sub_device,
            sub_device_key,
        )

    @property
    def _sub_device(self) -> dict[str, Any]:
        """Find the sub-device payload that matches this entity's serial.

        Returns:
            dict[str, Any]: The payload dictionary for the matching subdevice.
        """
        for item in sorted_sub_devices(self._payload.get(PAYLOAD_SUBDEVICES)):
            if sub_device_serial(item) == self._sub_device_sn:
                return item
        return {}

    @property
    def native_value(self) -> float | int | str | None:
        """Value of the described field from the sub-device payload.

        Returns:
            The transformed field value, or `None` if the field is absent.
        """
        raw = self._sub_device.get(self.entity_description.field)
        if raw is None:
            return None
        return cast("float | int | str | None", self.entity_description.transform(raw))

    def _build_sub_device_device_info(
        self,
        index: int,
        item: dict[str, Any],
        item_key: str,
    ) -> DeviceInfo:
        """Build device registry metadata for one subdevice.

        Returns:
            DeviceInfo: Registry info for the alarm subdevice.
        """
        base_name = first_nonblank_text(
            self._system.get(FIELD_DEVICE_NAME),
            self._discovery.get(FIELD_DEVICE_NAME),
            self._properties.get(FIELD_WNAME),
            fallback=f"Jackery {self._device_id}",
        )
        dev_type = safe_int(item.get(FIELD_DEV_TYPE))
        type_name = "Accessory"
        if dev_type == SUBDEVICE_DEV_TYPE_SMOKE:
            type_name = "Smoke alarm"
        elif dev_type == SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY:
            type_name = "Temperature sensor"
        elif dev_type == SUBDEVICE_DEV_TYPE_WATER_LEAK:
            type_name = "Water leak sensor"

        model = (
            item.get(FIELD_MODEL) or item.get(FIELD_TYPE_NAME) or "Jackery accessory"
        )
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{item_key}")},
            manufacturer=MANUFACTURER,
            name=f"{base_name} {type_name} {index}",
            model=str(model),
            serial_number=self._sub_device_sn,
        )
        self._apply_via_device(info)
        return info


class JackeryMeterHeadSensor(JackeryEntity, SensorEntity):
    """Expose one smart-meter head sensor."""

    _attr_has_entity_name = True
    """Disabled-by-default diagnostic sensor for one meter-head entry."""

    entity_description: JackeryMeterHeadSensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        identity: _IndexedEntityIdentity,
        description: JackeryMeterHeadSensorDescription,
    ) -> None:
        """Initialize one diagnostic meter-head sensor."""
        meter_head_index, meter_head_sn, meter_head_key = identity
        super().__init__(
            coordinator,
            device_id,
            f"{meter_head_key}_{description.key}",
        )
        self._meter_head_index = meter_head_index
        self._meter_head_sn = meter_head_sn
        self._meter_head_key = meter_head_key
        self.entity_description = description
        self._attr_translation_key = description.translation_key
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_entity_registry_enabled_default = False

    @property
    def _meter_head(self) -> dict[str, Any]:
        """The the meter-head entry corresponding to this entity's configured index.

        Returns:
            dict: The meter-head dictionary from payload's `PAYLOAD_METER_HEADS` at
            `self._meter_head_index` (1-based) when present and valid; otherwise an
            empty dict.
        """
        expected_sn = getattr(self, "_meter_head_sn", None)
        meter_heads = sorted_meter_heads(self._payload.get(PAYLOAD_METER_HEADS))
        discovered = sorted_meter_heads([
            *subdevice_accessories(
                self._payload, dev_type=SUBDEVICE_DEV_TYPE_METER_HEAD
            ),
            *subdevice_accessories(self._payload, dev_type=SUBDEVICE_DEV_TYPE_METER),
        ])
        if expected_sn is not None:
            return next(
                (
                    meter_head
                    for meter_head in (*meter_heads, *discovered)
                    if meter_head_serial(meter_head) == expected_sn
                ),
                {},
            )
        if not meter_heads:
            meter_heads = discovered
        index = getattr(self, "_meter_head_index", 0)
        if 1 <= index <= len(meter_heads):
            return meter_heads[index - 1]
        return {}

    @property
    def native_value(self) -> StateType:
        """Provide the current value for this meter-head sensor.

        Returns:
            The transformed value of the meter head's configured field, or `None` if the
            field is absent.
        """
        if self.entity_description.key == "ip_address":
            return accessory_ip_address(self._meter_head)
        raw = self._meter_head.get(self.entity_description.field)
        if raw is None:
            return None
        return _sensor_state_value(self.entity_description.transform(raw))

    @property
    def device_info(self) -> DeviceInfo:
        """Provide device registry metadata for this meter head.

        Returns:
            DeviceInfo: Device registry information including unique identifier
            (per-device meter-head id),
            manufacturer, display name, model, serial number when available, software
            version when available,
            and a `via_device` tuple referencing the parent device.
        """
        base_name = first_nonblank_text(
            self._system.get(FIELD_DEVICE_NAME),
            self._discovery.get(FIELD_DEVICE_NAME),
            self._properties.get(FIELD_WNAME),
            fallback=f"Jackery {self._device_id}",
        )
        meter_head = self._meter_head
        sn = first_nonblank_text(
            meter_head.get(FIELD_DEVICE_SN),
            meter_head.get(FIELD_DEV_SN),
            meter_head.get(FIELD_SN),
        )
        # Branding lookup against the documented accessory catalog so the
        # UI shows "EcoTracker P1/R1" / "P1 Meter" / "Homey Energy Dongle"
        # / "Jackery HTO892A (Meter Head)" instead of the raw scanName
        # (PROTOCOL §3 + source-of-truth scanName table, devType=4).
        manufacturer_brand, model_label = subdevice_branding(
            meter_head.get(FIELD_SCAN_NAME),
        )
        display_name = first_nonblank_text(
            meter_head.get(FIELD_DEVICE_NAME),
            model_label,
            meter_head.get(FIELD_SCAN_NAME),
            fallback=f"Meter Head {self._meter_head_index}",
        )
        model = first_nonblank_text(
            model_label,
            meter_head.get(FIELD_MODEL),
            meter_head.get(FIELD_MODEL_NAME),
            meter_head.get(FIELD_TYPE_NAME),
            fallback="Meter Head",
        )
        version = first_nonblank_text(
            meter_head.get(FIELD_VERSION),
            meter_head.get(FIELD_CURRENT_VERSION),
        )
        stable_key = getattr(self, "_meter_head_key", None) or stable_subdevice_key(
            "meter_head",
            sn,
            self._meter_head_index,
        )
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{stable_key}")},
            manufacturer=manufacturer_brand or MANUFACTURER,
            name=f"{base_name} {display_name}",
            model=str(model),
            serial_number=str(sn) if sn else None,
            sw_version=str(version) if version else None,
        )
        self._apply_via_device(info)
        return info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current meter head.

        Includes the meter_head_index and any of the following keys present on the
        meter-head payload:
        meter-head payload:
        FIELD_DEVICE_NAME, FIELD_SCAN_NAME, FIELD_COMM_STATE, FIELD_COMM_MODE,
        FIELD_IN_PW, FIELD_OUT_PW, FIELD_CHARGING_ENERGY, FIELD_DISCHARGING_ENERGY,
        and FIELD_VERSION.

        Returns:
            dict[str, Any]: Mapping of attribute names to values.
        """
        attrs: dict[str, Any] = {
            "meter_head_index": self._meter_head_index,
            "meter_head_id": self._meter_head_sn,
        }
        for key in (
            FIELD_DEVICE_NAME,
            FIELD_SCAN_NAME,
            FIELD_COMM_STATE,
            FIELD_COMM_MODE,
            FIELD_IN_PW,
            FIELD_OUT_PW,
            FIELD_CHARGING_ENERGY,
            FIELD_DISCHARGING_ENERGY,
            FIELD_VERSION,
            FIELD_SUB_TYPE,
            FIELD_DEV_TYPE,
            FIELD_PARAM,
            FIELD_LINK_TYPE,
        ):
            if key in self._meter_head:
                attrs[key] = self._meter_head.get(key)
        return attrs


class JackerySmartMeterSensor(JackeryEntity, RestoreSensor):
    """CT / smart-meter live power sensor from MQTT sub-device payloads."""

    _ct_energy_source = True

    entity_description: JackerySmartMeterSensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: JackerySmartMeterSensorDescription,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, f"smart_meter_{description.key}")
        self.entity_description = description
        self._cached_native_value: StateType = None
        self._cached_attrs: dict[str, Any] = {}
        self._restored_lifetime_value: float | None = None
        self._registered_identity: tuple[str | None, str | None] | None = None

    @staticmethod
    def _directional_value(
        ct: dict[str, Any],
        positive_keys: tuple[str, ...],
        negative_keys: tuple[str, ...],
    ) -> float | None:
        """The positive-key sum minus negative-key sum if any value exists."""
        return directional_power_value(ct, positive_keys, negative_keys)

    @classmethod
    def _signed_phase_values(cls, ct: dict[str, Any]) -> list[float] | None:
        """The signed phase powers; positive=grid import, negative=export."""
        return signed_phase_power_values(ct)

    @classmethod
    def _net_power(cls, ct: dict[str, Any]) -> float | None:
        """The the app-reported CT total; phase sum is only fallback."""
        return smart_meter_net_power(ct)

    def _value_from_ct(self, ct: dict[str, Any]) -> StateType:
        """Calculate the current value from a CT payload."""
        if (
            self.entity_description.field
            in CT_THIRD_PV_POWER_FIELDS | ACC_CT_L2_POWER_FIELDS | ACC_CT_L2_RAW_FIELDS
            and self.coordinator.ct_model_conflicted(self.device_id, ct) is True
        ):
            return None
        return _smart_meter_description_value(ct, self.entity_description)

    def _attrs_from_ct(self, ct: dict[str, Any]) -> dict[str, Any]:
        """Build diagnostic attributes from a CT (smart‑meter) payload.

        Returns a dictionary of diagnostic attributes derived from the provided CT
        payload. Possible keys:
        - ``calculation``: configured derived calculation mode.
        - ``source``: total, phase, raw-field, or calculated origin.
        - Signed phase powers when the CT payload provides them.
        - ``signed_phase_convention`` describing the sign convention.
        - Present ``CT_ATTRIBUTE_FIELDS`` copied from the CT payload.
        - For power: calculated phase sum and reported total when available.

        Returns:
            dict[str, Any]: Mapping of diagnostic attribute names to their values (may
            be empty if no diagnostics are available).
        """  # ruff:ignore[ambiguous-unicode-character-docstring]
        if self.entity_description.field in CT_THIRD_PV_POWER_FIELDS:
            reported = (
                safe_float(self._merged_properties.get(FIELD_SYS_TOTAL_PV_PW))
                if self.entity_description.key == "additional_pv_net_power"
                else None
            )
            return {
                "power_path": "additional_pv",
                "signed_phase_convention": (
                    "positive=additional_pv_input, negative=additional_pv_output"
                ),
                **(
                    {"system_reported_additional_pv_net_power": reported}
                    if reported is not None
                    else {}
                ),
            }
        if self.entity_description.calculation:
            return {
                "calculation": self.entity_description.calculation,
                "source": (
                    "total_fields"
                    if self.entity_description.calculation
                    in {"net_import", "net_export"}
                    else "phase_fields"
                ),
            }
        phase_attr_names = {
            "phase_1_power": "phase_a_signed_power",
            "phase_2_power": "phase_b_signed_power",
            "phase_3_power": "phase_c_signed_power",
        }
        if self.entity_description.key in phase_attr_names:
            phases = self._signed_phase_values(ct)
            if phases is None:
                return {}
            phase_index = ("phase_1_power", "phase_2_power", "phase_3_power").index(
                self.entity_description.key
            )
            return {
                phase_attr_names[self.entity_description.key]: phases[phase_index],
                "signed_phase_convention": (
                    "positive=grid_import, negative=grid_export"
                ),
                "source": "phase_fields",
            }

        attrs: dict[str, Any] = {}
        for key in CT_ATTRIBUTE_FIELDS:
            if key in ct:
                attrs[key] = ct.get(key)
        phases = self._signed_phase_values(ct)
        signed_total = smart_meter_net_power(ct)
        if signed_total is not None:
            attrs["phase_t_signed_power"] = signed_total
        if phases is not None:
            attrs["phase_a_signed_power"] = phases[0]
            attrs["phase_b_signed_power"] = phases[1]
            attrs["phase_c_signed_power"] = phases[2]
            attrs["signed_phase_convention"] = (
                "positive=grid_import, negative=grid_export"
            )
        if self.entity_description.key in {
            "reactive_power",
            "phase_1_reactive_power",
            "phase_2_reactive_power",
            "phase_3_reactive_power",
        }:
            attrs["source"] = (
                "raw_field"
                if ct.get(self.entity_description.field) is not None
                else "derived_apparent_minus_active"
            )
        if self.entity_description.key == "power":
            phase_sum = self._directional_value(
                ct,
                self.entity_description.sum_fields,
                self.entity_description.negative_sum_fields,
            )
            total_field = self._directional_value(
                ct,
                self.entity_description.aliases,
                self.entity_description.negative_aliases,
            )
            if phase_sum is not None:
                attrs["phase_sum_power"] = phase_sum
            if total_field is not None:
                attrs["total_field_power"] = total_field
            attrs["source"] = (
                "total_field"
                if total_field is not None
                else "phase_sum"
                if phase_sum is not None
                else "raw_field"
            )
        return attrs

    def _smart_meter_identifier(self, ct: dict[str, Any]) -> tuple[str, str]:
        """Return the registry identifier for this CT accessory.

        Must match the target key built by ``async_migrate_smart_meter_devices``
        in ``__init__.py``; the migration deletes the legacy constant-suffix
        identifiers, so both sides have to agree on the keyed form.
        """
        key = stable_subdevice_key("smart_meter", smart_meter_identity(ct), 1)
        return (DOMAIN, f"{self._device_id}_{key}")

    def _sync_device_mac_connection(self, ct: dict[str, Any]) -> None:
        """Attach CT identity metadata when it arrives after registration.

        ``device_info`` is only read when the entity is first registered. The
        CT payload usually arrives after that, so a device created during an
        early poll keeps an empty ``connections`` set forever. Merge the MAC in
        as soon as it is known instead of waiting for a fresh registration.
        """
        serial = smart_meter_identity(ct)
        mac = normalize_mac_address(ct.get(FIELD_MAC)) or normalize_mac_address(serial)
        identity = (serial, mac)
        if identity in {(None, None), self._registered_identity}:
            return
        hass = getattr(self, "hass", None)
        entry = getattr(self.coordinator, "config_entry", None)
        if hass is None or entry is None:
            return
        registry = dr.async_get(hass)
        device = (
            registry.async_get_device_by_identifier(
                self._smart_meter_identifier(ct),
                entry.entry_id,
            )
            if hasattr(registry, "async_get_device_by_identifier")
            else registry.async_get_device(
                identifiers={self._smart_meter_identifier(ct)}
            )
        )
        if device is None:
            return
        # The package is already initialized here; defer this import to avoid
        # a module-load cycle while reconciling identities learned after setup.
        from . import _async_migrate_smart_meter_identity  # ruff: ignore[import-outside-top-level]

        entry = getattr(self.coordinator, "config_entry", None)
        if entry is not None:
            _async_migrate_smart_meter_identity(hass, entry)
        connections = {(dr.CONNECTION_NETWORK_MAC, mac)} if mac else set()
        if (serial and serial != device.serial_number) or not connections.issubset(
            device.connections
        ):
            registry.async_update_device(
                device.id,
                serial_number=serial or device.serial_number,
                new_connections=set(device.connections) | connections,
            )
        self._registered_identity = identity

    def _refresh_cache(self) -> None:
        """Recompute state and attributes once per coordinator update."""
        energy = self.entity_description.device_class == SensorDeviceClass.ENERGY
        if energy:
            _invalidate_own_lifetime_parent_cache(self)
        ct = self._payload.get(PAYLOAD_CT_METER) or {}
        if not isinstance(ct, dict):
            ct = {}
        self._sync_device_mac_connection(ct)
        live_value = self._value_from_ct(ct)
        candidate = live_value
        attrs = self._attrs_from_ct(ct)
        if energy:
            owner_attrs = _energy_owner_attributes(self)
            attrs.update(owner_attrs)
            if not owner_attrs:
                live_value = candidate = None
        if live_value is None and self._restored_lifetime_value is not None:
            candidate = self._restored_lifetime_value
            attrs = {
                **attrs,
                "value_restored": True,
                "restore_reason": "lifetime_counter_source_unavailable",
            }
        guarded = guard_total_increasing_jitter(
            self._cached_native_value,
            candidate,
            self.entity_description,
        )
        if guarded is not candidate:
            attrs = {**attrs, "lifetime_value_retained": True}
        self._cached_native_value = guarded
        self._cached_attrs = attrs
        if live_value is not None:
            self._restored_lifetime_value = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh cached Smart-Meter values before HA writes the new state."""
        self._refresh_cache()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime the cache before CoordinatorEntity writes the initial state."""
        self._refresh_cache()
        await super().async_added_to_hass()
        if (
            self.entity_description.state_class is SensorStateClass.TOTAL_INCREASING
            and self.entity_description.device_class is SensorDeviceClass.ENERGY
            and self._cached_native_value is None
        ):
            self._restored_lifetime_value = await _async_restored_lifetime_energy_value(
                self,
                self.entity_description.native_unit_of_measurement,
            )
            self._refresh_cache()
            if self.native_value is not None:
                self.async_write_ha_state()

    @property
    def native_value(self) -> StateType:
        """The the entity's current value."""
        return self._cached_native_value

    @property
    def device_info(self) -> DeviceInfo:
        """Provide device registry metadata for the smart-meter entity.

        Returns:
            DeviceInfo: Device registry information used to register the associated
            smart-meter (identifiers, manufacturer, model, name, serial_number, and
            via_device).
        """
        ct = self._payload.get(PAYLOAD_CT_METER) or {}
        if not isinstance(ct, dict):
            ct = {}
        base_name = first_nonblank_text(
            self._system.get(FIELD_DEVICE_NAME),
            self._discovery.get(FIELD_DEVICE_NAME),
            self._properties.get(FIELD_WNAME),
            fallback=f"Jackery {self._device_id}",
        )
        # Branding lookup against the documented accessory catalog
        # (PROTOCOL §3 + source-of-truth scanName table, devType=3 = CT). The
        # old "shelly in name.lower()" substring heuristic missed branded
        # units like ``ecotracker`` / ``p1meter`` / ``homey_energy_dongle``
        # and Jackery's own ``HTO906A``/``HTO907A`` CT-type accessories;
        # the lookup now covers all 14 documented scanNames.
        raw_scan_name = ct.get(FIELD_SCAN_NAME)
        manufacturer_brand, model_label = subdevice_branding(raw_scan_name)
        scan_name = nonblank_text(raw_scan_name) or "Smart Meter"
        manufacturer = manufacturer_brand or (
            "Shelly" if "shelly" in scan_name.lower() else MANUFACTURER
        )
        model = model_label or (
            scan_name if scan_name and scan_name != "Smart Meter" else "Smart Meter"
        )
        sn = smart_meter_identity(ct)
        # The accessory identity keys the device. A constant ``_smart_meter``
        # suffix is the *legacy* form that ``async_migrate_smart_meter_devices``
        # deletes once the keyed device exists — emitting it here would make HA
        # drop and re-create the device on every start, which is the duplicate
        # CT entry users see.
        info = DeviceInfo(
            identifiers={self._smart_meter_identifier(ct)},
            manufacturer=manufacturer,
            name=f"{base_name} Smart Meter",
            model=model,
            serial_number=str(sn) if sn else None,
        )
        self._apply_via_device(info)
        explicit_mac = ct.get(FIELD_MAC)
        if mac := (normalize_mac_address(explicit_mac) or normalize_mac_address(sn)):
            info["connections"] = {(dr.CONNECTION_NETWORK_MAC, mac)}
        return info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current state."""
        return self._cached_attrs


class JackeryRawPropertiesSensor(JackeryEntity, SensorEntity):
    """Expose raw property diagnostics."""

    _attr_has_entity_name = True
    """Diagnostic: redacted properties JSON as state attributes."""

    _attr_translation_key = "raw_properties"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "raw_properties")

    @property
    def native_value(self) -> int:
        """The entity's current value."""
        return len(self._merged_properties)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes derived from the redacted device properties payload.

        Returns:
            dict[str, Any]: A dictionary of redacted diagnostic attributes when the
            redaction yields a mapping, otherwise an empty dictionary.
        """
        redacted = redacted_json_safe_payload(self._merged_properties)
        return redacted if isinstance(redacted, dict) else {}


class JackeryBleTransportSensor(JackeryEntity, SensorEntity):
    """Expose BLE transport diagnostics."""

    _attr_has_entity_name = True
    """Diagnostic sensor exposing the experimental BLE listener state.

    Disabled by default. When the integration option
    ``enable_ble_transport`` is on, the sensor surfaces the latest decoded
    frame metadata and per-device counters (advertisements, connect
    attempts, frames received/decoded). The state itself is the count of
    successfully decoded frames so changes are easy to graph or trigger
    automations on.
    """

    _attr_translation_key = "ble_transport"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialize the device's BLE transport diagnostic sensor.

        This entity exposes BLE listener decode statistics and last-frame metadata for
        the specified device.
        """
        super().__init__(coordinator, device_id, "ble_transport")

    def _observation(self) -> dict[str, Any]:
        """Retrieve the BLE observation record for this device.

        Fetches the coordinator's BLE observations and returns the entry keyed by this
        entity's device id.

        Returns:
            dict[str, Any]: Observation data for this device, or an empty dict if no
            valid record exists.
        """
        observations = self.coordinator.ble_observations()
        result = observations.get(self._device_id)
        return result if isinstance(result, dict) else {}

    @property
    def native_value(self) -> int:
        """Number of BLE frames decoded for this device since setup.

        Returns:
            int: Count of frames successfully decoded; 0 when none.
        """
        return int(self._observation().get("frames_decoded", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Provide BLE listener counters and sanitized last-frame metadata.

        The returned dictionary is a copy of the current BLE observation for this
        device.
        If a `last_frame` entry is present and is a mapping, the following sensitive
        fields
        are removed from the returned structure:
        - `raw_hex` (removed from `last_frame`)
        - `body_preview` and `trailer_hex` (removed from `last_frame['parsed']` if
        present)

        Returns:
            attrs (dict[str, Any]): Observation dictionary with `last_frame` sanitized.
        """
        attrs = dict(self._observation())
        attrs.pop("unrouted_frames_by_cmd", None)
        attrs.pop("sample_unrouted_frames", None)
        last_frame = attrs.get("last_frame")
        if not isinstance(last_frame, dict):
            return attrs

        frame_attrs = dict(last_frame)
        frame_attrs.pop("raw_hex", None)
        parsed = frame_attrs.get("parsed")
        if isinstance(parsed, dict):
            parsed_attrs = dict(parsed)
            parsed_attrs.pop("body_preview", None)
            parsed_attrs.pop("trailer_hex", None)
            frame_attrs["parsed"] = parsed_attrs
        attrs["last_frame"] = frame_attrs
        return attrs


class JackeryHttpApiSensor(JackeryEntity, SensorEntity):
    """Expose HTTP API diagnostics."""

    _attr_has_entity_name = True
    """Diagnostic sensor exposing the HTTP API cloud transport health.

    Disabled by default. Shows request counters (total, failed,
    timeouts, auth retries) plus Cloud MQTT birth/retain status
    for a unified view of the cloud transport path.
    """

    _attr_translation_key = "http_api"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = UNRECORDED_ATTRS_HTTP_API

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Create the HTTP API diagnostic entity for the given device."""
        super().__init__(coordinator, device_id, "http_api")
        self._cached_observation: dict[str, Any] = {}
        self._cache_refresh_active = False

    def _observation(self) -> dict[str, Any]:
        """Retrieve the HTTP API observation record for this device."""
        observations = self.coordinator.http_api_observations()
        return observations if isinstance(observations, dict) else {}

    def _refresh_cache(self) -> None:
        """Prepare one copied HTTP observation for value and attributes."""
        self._cached_observation = dict(self._observation())

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh the prepared HTTP observation before writing state."""
        if self._cache_refresh_active:
            self._refresh_cache()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime and activate the prepared HTTP observation."""
        self._refresh_cache()
        self._cache_refresh_active = True
        try:
            await super().async_added_to_hass()
        except Exception:
            self._cache_refresh_active = False
            raise

    async def async_will_remove_from_hass(self) -> None:
        """Stop serving the prepared HTTP observation after removal."""
        self._cache_refresh_active = False
        await super().async_will_remove_from_hass()

    def _prepared_observation(self) -> dict[str, Any]:
        """Return the prepared observation while state caching is active."""
        if self._cache_refresh_active:
            return self._cached_observation
        return self._observation()

    @property
    def native_value(self) -> int:
        """Total HTTP requests made since HA setup."""
        return int(self._prepared_observation().get("requests_total", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Provide HTTP API + Cloud MQTT counters."""
        return dict(self._prepared_observation())


class JackeryCloudMqttSensor(JackeryEntity, SensorEntity):
    """Expose cloud MQTT diagnostics."""

    _attr_has_entity_name = True
    """Diagnostic sensor exposing the Cloud MQTT push-client health.

    Disabled by default. Tracks message counts, birth/retain
    publishes, connection lifecycle, and TLS configuration.
    """

    _attr_translation_key = "cloud_mqtt"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _unrecorded_attributes = UNRECORDED_ATTRS_CLOUD_MQTT

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Create the Cloud MQTT diagnostic entity for the given device."""
        super().__init__(coordinator, device_id, "cloud_mqtt")
        self._cached_observation: dict[str, Any] = {}
        self._cache_refresh_active = False

    def _observation(self) -> dict[str, Any]:
        """Retrieve the Cloud MQTT observation record for this device."""
        observations = self.coordinator.cloud_mqtt_observations()
        return observations if isinstance(observations, dict) else {}

    def _refresh_cache(self) -> None:
        """Prepare the complete observation once per coordinator update."""
        self._cached_observation = dict(self._observation())

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh diagnostics before Home Assistant writes the state."""
        if self._cache_refresh_active:
            self._refresh_cache()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime and activate the complete diagnostic observation."""
        self._refresh_cache()
        self._cache_refresh_active = True
        try:
            await super().async_added_to_hass()
        except (Exception, asyncio.CancelledError):
            self._cache_refresh_active = False
            raise

    async def async_will_remove_from_hass(self) -> None:
        """Stop serving cached diagnostics when the entity is removed."""
        self._cache_refresh_active = False
        await super().async_will_remove_from_hass()

    def _prepared_observation(self) -> dict[str, Any]:
        """Return one complete snapshot throughout a state update."""
        if self._cache_refresh_active:
            return self._cached_observation
        return self._observation()

    @property
    def native_value(self) -> int:
        """Total MQTT messages received since HA setup."""
        return int(self._prepared_observation().get("messages_seen", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Provide Cloud MQTT counters and connection metadata."""
        return dict(self._prepared_observation())


class JackeryLocalMqttSensor(JackeryEntity, SensorEntity):
    """Expose local MQTT diagnostics."""

    _attr_has_entity_name = True
    """Diagnostic sensor exposing the Third-Party Local MQTT listener health.

    Disabled by default. Surfaces message counters, routing
    warnings, and connection lifecycle for the local broker bridge.
    """

    _attr_translation_key = "local_mqtt"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _unrecorded_attributes = UNRECORDED_ATTRS_LOCAL_MQTT

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Create the Local MQTT diagnostic entity for the given device."""
        super().__init__(coordinator, device_id, "local_mqtt")
        self._cached_observation: dict[str, Any] = {}
        self._cache_refresh_active = False

    def _observation(self) -> dict[str, Any]:
        """Retrieve the Local MQTT observation record for this device."""
        observations = self.coordinator.local_mqtt_observations(self._device_id)
        return observations if isinstance(observations, dict) else {}

    def _refresh_cache(self) -> None:
        """Prepare the complete observation once per coordinator update."""
        self._cached_observation = dict(self._observation())

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh the observation before Home Assistant writes the state."""
        if self._cache_refresh_active:
            self._refresh_cache()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime and activate the complete diagnostic observation."""
        self._refresh_cache()
        self._cache_refresh_active = True
        try:
            await super().async_added_to_hass()
        except (Exception, asyncio.CancelledError):
            self._cache_refresh_active = False
            raise

    async def async_will_remove_from_hass(self) -> None:
        """Stop serving cached diagnostics when the entity is removed."""
        self._cache_refresh_active = False
        await super().async_will_remove_from_hass()

    def _prepared_observation(self) -> dict[str, Any]:
        """Return one complete snapshot throughout a state update."""
        if self._cache_refresh_active:
            return self._cached_observation
        return self._observation()

    @property
    def native_value(self) -> int:
        """Accepted messages for this device since HA setup."""
        return int(self._prepared_observation().get("device_messages_accepted", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Provide Local MQTT counters and routing diagnostics."""
        attrs = dict(self._prepared_observation())
        attrs.pop("enabled", None)
        return attrs


class JackeryDeviceActivationSensor(JackeryEntity, SensorEntity):
    """Expose device activation status."""

    _attr_has_entity_name = True
    """Diagnostic sensor exposing the device cloud-activation state.

    Disabled by default.  Shows ``activated`` (0/1) as the state
    value and ``isCloud`` plus the raw device payload as extra
    attributes so users can report to Jackery support when cloud
    trend/stat endpoints return empty data.
    """

    _attr_translation_key = "device_activation"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Create the device-activation diagnostic entity."""
        super().__init__(coordinator, device_id, "device_activation")
        self._cached_native_value: int | None = None
        self._cached_attrs: dict[str, Any] = {}
        self._cache_refresh_active = False

    def _device_snapshot(self) -> dict[str, Any]:
        """Return the current device metadata mapping."""
        device = (
            (self.coordinator.data or {})
            .get(self._device_id, {})
            .get(PAYLOAD_DEVICE, {})
        )
        return device if isinstance(device, dict) else {}

    @staticmethod
    def _attrs_from_device(device: dict[str, Any]) -> dict[str, Any]:
        """Build activation diagnostics from one device snapshot."""
        return {
            "is_cloud": device.get("isCloud"),
            "activated": device.get("activated"),
            "online_status": device.get("onlineStatus"),
            "device_sn": device.get("sn"),
        }

    def _refresh_cache(self) -> None:
        """Prepare one coherent activation value-and-attributes snapshot."""
        device = self._device_snapshot()
        self._cached_native_value = safe_int(device.get("activated"))
        self._cached_attrs = self._attrs_from_device(device)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh the prepared activation snapshot before writing state."""
        if self._cache_refresh_active:
            self._refresh_cache()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime and activate the prepared activation snapshot."""
        self._refresh_cache()
        self._cache_refresh_active = True
        try:
            await super().async_added_to_hass()
        except Exception:
            self._cache_refresh_active = False
            raise

    async def async_will_remove_from_hass(self) -> None:
        """Stop serving the prepared activation snapshot after removal."""
        self._cache_refresh_active = False
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> int | None:
        """The cloud-activation state (0 = not activated, 1 = active)."""
        if self._cache_refresh_active:
            return self._cached_native_value
        return safe_int(self._device_snapshot().get("activated"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Provide cloud-activation diagnostics."""
        if self._cache_refresh_active:
            return self._cached_attrs
        return self._attrs_from_device(self._device_snapshot())


class JackeryWeatherPlanSensor(JackeryEntity, SensorEntity):
    """Expose weather-plan diagnostics."""

    _attr_has_entity_name = True
    """Diagnostic sensor exposing the weather/storm plan payload."""

    _attr_translation_key = "weather_plan"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, PAYLOAD_WEATHER_PLAN)

    @property
    def native_value(self) -> int:
        """The entity's current value."""
        storm = self._weather_plan.get(FIELD_STORM)
        if isinstance(storm, list):
            return len(storm)
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current state."""
        return dict(self._weather_plan)


class JackeryTaskPlanSensor(JackeryEntity, SensorEntity):
    """Expose task-plan diagnostics."""

    _attr_has_entity_name = True
    """Diagnostic sensor exposing schedule/task payloads."""

    _attr_translation_key = "task_plan"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, PAYLOAD_TASK_PLAN)

    @property
    def native_value(self) -> int:
        """The entity's current value."""
        plan = self._task_plan
        tasks = None
        if isinstance(plan, dict):
            tasks = plan.get(TASK_PLAN_TASKS)
            if tasks is None and isinstance(plan.get(TASK_PLAN_BODY), dict):
                tasks = plan[TASK_PLAN_BODY].get(TASK_PLAN_TASKS)
        if isinstance(tasks, list):
            return len(tasks)
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current state."""
        return dict(self._task_plan)


# ---------------------------------------------------------------------------
# Derived live-power sensors.
#
# These values are signed instantaneous measurements in watts. Keep MEASUREMENT
# so Home Assistant records their means and preserves existing power statistics;
# signed power is not a cumulative energy counter.
# ---------------------------------------------------------------------------
class JackeryBatteryNetPowerSensor(JackeryEntity, SensorEntity):
    """Expose main-battery net power (positive discharge, negative charge)."""

    _attr_has_entity_name = True
    device_registry_role = "main_battery"
    _attr_translation_key = "battery_net_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_data_sources = ALL_LIVE_DATA_SOURCES

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "battery_net_power")

    @property
    def native_value(self) -> int | None:
        """The entity's current value."""
        props = self._properties
        in_pw = safe_int(props.get(FIELD_BAT_IN_PW))
        out_pw = safe_int(props.get(FIELD_BAT_OUT_PW))
        if in_pw is None or out_pw is None:
            return None
        return out_pw - in_pw

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current state."""
        http_props = self._http_properties or {}
        props = self._properties
        merged = self._merged_properties
        return {
            "formula": "batOutPw - batInPw",
            "source": "http_primary_property_fields",
            "positive": "battery discharge",
            "negative": "battery charge",
            "batOutPw": props.get(FIELD_BAT_OUT_PW),
            "batInPw": props.get(FIELD_BAT_IN_PW),
            "merged_batOutPw": merged.get(FIELD_BAT_OUT_PW),
            "merged_batInPw": merged.get(FIELD_BAT_IN_PW),
            "http_batOutPw": http_props.get(FIELD_BAT_OUT_PW),
            "http_batInPw": http_props.get(FIELD_BAT_IN_PW),
            "mqtt_minus_http_batInPw": _signed_diff(
                merged.get(FIELD_BAT_IN_PW), http_props.get(FIELD_BAT_IN_PW)
            ),
            "mqtt_minus_http_batOutPw": _signed_diff(
                merged.get(FIELD_BAT_OUT_PW), http_props.get(FIELD_BAT_OUT_PW)
            ),
            "stackOutPw": merged.get(FIELD_STACK_OUT_PW),
            "stackInPw": merged.get(FIELD_STACK_IN_PW),
        }


class JackeryBatteryStackNetPowerSensor(JackeryEntity, SensorEntity):
    """Expose complete battery-stack power from the main-device stack bus."""

    _attr_has_entity_name = True
    _attr_translation_key = "battery_stack_net_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_data_sources = ALL_LIVE_DATA_SOURCES

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "battery_stack_net_power")

    @property
    def native_value(self) -> int | None:
        """The entity's current value."""
        props = self._properties
        in_pw = safe_int(props.get(FIELD_STACK_IN_PW))
        out_pw = safe_int(props.get(FIELD_STACK_OUT_PW))
        if in_pw is None or out_pw is None:
            return None
        return out_pw - in_pw

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current state."""
        props = self._properties
        merged = self._merged_properties
        http_props = self._http_properties or {}
        return {
            "formula": "stackOutPw - stackInPw",
            "source": "coordinator_resolved_main_device_stack_bus",
            "positive": "complete battery stack discharge",
            "negative": "complete battery stack charge",
            "stackOutPw": props.get(FIELD_STACK_OUT_PW),
            "stackInPw": props.get(FIELD_STACK_IN_PW),
            "merged_stackOutPw": merged.get(FIELD_STACK_OUT_PW),
            "merged_stackInPw": merged.get(FIELD_STACK_IN_PW),
            "http_stackOutPw": http_props.get(FIELD_STACK_OUT_PW),
            "http_stackInPw": http_props.get(FIELD_STACK_IN_PW),
            "mqtt_minus_http_stackInPw": _signed_diff(
                merged.get(FIELD_STACK_IN_PW),
                http_props.get(FIELD_STACK_IN_PW),
            ),
            "mqtt_minus_http_stackOutPw": _signed_diff(
                merged.get(FIELD_STACK_OUT_PW),
                http_props.get(FIELD_STACK_OUT_PW),
            ),
            "battery_pack_outPw_sum": sum(
                safe_int(pack.get(FIELD_OUT_PW)) or 0
                for pack in (self._payload.get(PAYLOAD_BATTERY_PACKS) or [])
                if isinstance(pack, dict)
            ),
            "battery_pack_inPw_sum": sum(
                safe_int(pack.get(FIELD_IN_PW)) or 0
                for pack in (self._payload.get(PAYLOAD_BATTERY_PACKS) or [])
                if isinstance(pack, dict)
            ),
        }


class JackeryGridNetPowerSensor(JackeryEntity, SensorEntity):
    """Expose signed grid net power (positive = input, negative = output)."""

    _attr_has_entity_name = True
    _attr_translation_key = "grid_net_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_data_sources = ALL_LIVE_DATA_SOURCES

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "grid_net_power")

    @property
    def native_value(self) -> int | None:
        """The entity's current value."""
        return jackery_grid_net_power(self._properties)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current state."""
        props = self._properties
        return {
            "formula": "inGridSidePw - outGridSidePw",
            "source": "selected_grid_side_fields_only_no_inverter_fallback",
            **self.layer_source_attributes(
                PAYLOAD_PROPERTIES, FIELD_IN_GRID_SIDE_PW, FIELD_OUT_GRID_SIDE_PW
            ),
            "positive": "grid import exceeds export",
            "negative": "grid export exceeds import",
            FIELD_IN_GRID_SIDE_PW: props.get(FIELD_IN_GRID_SIDE_PW),
            FIELD_OUT_GRID_SIDE_PW: props.get(FIELD_OUT_GRID_SIDE_PW),
            FIELD_IN_ONGRID_PW: props.get(FIELD_IN_ONGRID_PW),
            FIELD_OUT_ONGRID_PW: props.get(FIELD_OUT_ONGRID_PW),
            FIELD_GRID_IN_PW: props.get(FIELD_GRID_IN_PW),
            FIELD_GRID_OUT_PW: props.get(FIELD_GRID_OUT_PW),
            "batOutPw": props.get(FIELD_BAT_OUT_PW),
            "batInPw": props.get(FIELD_BAT_IN_PW),
            FIELD_OTHER_LOAD_PW: props.get(FIELD_OTHER_LOAD_PW),
            "stackOutPw": props.get(FIELD_STACK_OUT_PW),
            "stackInPw": props.get(FIELD_STACK_IN_PW),
        }


class JackeryHomeConsumptionPowerSensor(JackeryEntity, SensorEntity):
    """Live home consumption corrected for Jackery AC input/output."""

    _attr_has_entity_name = True
    _attr_translation_key = "home_consumption_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_data_sources = ALL_LIVE_DATA_SOURCES

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "home_consumption_power")
        self._cached_native_value: float | None = None
        self._cached_attrs: dict[str, Any] = {}

    @staticmethod
    def _first_power(props: dict[str, Any], *keys: str) -> float | None:
        """The the first available numeric power value for the given keys."""
        return first_power_value(props, *keys)

    @classmethod
    def _grid_side_input_power(cls, props: dict[str, Any]) -> float | None:
        """AC power drawn by the Jackery system from the grid/home side."""
        return jackery_grid_side_input_power(props)

    @classmethod
    def _grid_side_output_power(cls, props: dict[str, Any]) -> float | None:
        """AC power supplied by the Jackery system to the grid/home side."""
        return jackery_grid_side_output_power(props)

    @classmethod
    def _home_consumption_power(
        cls, ct: dict[str, Any], props: dict[str, Any]
    ) -> HomeConsumptionPower | None:
        """The home consumption and its components."""
        return jackery_corrected_home_consumption_power(ct, props)

    def _refresh_cache(self) -> None:
        """Compute home consumption once per update to keep state writes instant."""
        ct = self._payload.get(PAYLOAD_CT_METER) or {}
        if not isinstance(ct, dict):
            ct = {}
        props = self._properties
        result = self._home_consumption_power(ct, props)
        self._cached_native_value = (
            round(result.value, 2) if result is not None else None
        )

        attrs: dict[str, Any] = {
            "formula": (
                "otherLoadPw if available, otherwise "
                "max(smart_meter_net_power - jackery_grid_side_input_power "
                "+ jackery_grid_side_output_power, 0)"
            ),
            "source": (
                "selected_otherLoadPw_preferred_then_smart_meter_ct_plus_"
                "jackery_ac_grid_side_fields"
            ),
            **self.layer_source_attributes(
                PAYLOAD_PROPERTIES,
                FIELD_OTHER_LOAD_PW,
                FIELD_IN_GRID_SIDE_PW,
                FIELD_OUT_GRID_SIDE_PW,
            ),
            "scope": (
                "Jackery-corrected home load; external non-Jackery generation"
                " must be measured separately"
            ),
        }

        meter_net = JackerySmartMeterSensor._net_power(  # ruff:ignore[private-member-access]
            ct
        )
        input_available = self._grid_side_input_power(props) is not None
        output_available = self._grid_side_output_power(props) is not None
        reported_load_available = (
            self._first_power(props, FIELD_OTHER_LOAD_PW) is not None
        )
        attrs["calculation_confidence"] = (
            "direct_app_value"
            if reported_load_available and result is not None
            else "fallback_complete"
            if input_available and output_available and result is not None
            else "fallback_partial"
            if result is not None
            else "unavailable"
        )
        attrs["reported_home_load_available"] = reported_load_available
        attrs["jackery_grid_side_input_available"] = input_available
        attrs["jackery_grid_side_output_available"] = output_available
        attrs["smart_meter_net_power_available"] = meter_net is not None
        if result is not None:
            attrs["home_consumption_source"] = result.source
            if result.smart_meter_net_power is not None:
                attrs["smart_meter_net_power"] = round(result.smart_meter_net_power, 2)
            attrs["jackery_grid_side_input_power"] = round(
                result.jackery_input_power, 2
            )
            attrs["jackery_grid_side_output_power"] = round(
                result.jackery_output_power, 2
            )

        phases = JackerySmartMeterSensor._signed_phase_values(ct)  # ruff:ignore[private-member-access]  # reuse of sibling sensor's classmethod phase helper (same module)
        if phases is not None:
            attrs["phase_a_signed_power"] = round(phases[0], 2)
            attrs["phase_b_signed_power"] = round(phases[1], 2)
            attrs["phase_c_signed_power"] = round(phases[2], 2)
            attrs["signed_phase_convention"] = (
                "positive=grid_import, negative=grid_export"
            )

        for key in (
            FIELD_IN_GRID_SIDE_PW,
            FIELD_OUT_GRID_SIDE_PW,
            FIELD_IN_ONGRID_PW,
            FIELD_OUT_ONGRID_PW,
            FIELD_GRID_IN_PW,
            FIELD_GRID_OUT_PW,
            FIELD_OTHER_LOAD_PW,
        ):
            if key in props:
                attrs[key] = props.get(key)
        self._cached_attrs = attrs

    @property
    def native_value(self) -> float | None:
        """The entity's current value."""
        if self._cached_native_value is None and not self._cached_attrs:
            self._refresh_cache()
        return self._cached_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current state."""
        if not self._cached_attrs:
            self._refresh_cache()
        return self._cached_attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh cached consumption before writing state to Home Assistant."""
        self._refresh_cache()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime cache before first state write."""
        self._refresh_cache()
        await super().async_added_to_hass()


# ---------------------------------------------------------------------------
# Alarm sensor
# ---------------------------------------------------------------------------
class JackeryAlarmSensor(JackeryEntity, SensorEntity):
    """Expose alarm-list diagnostics."""

    _attr_has_entity_name = True
    """Count of active alarms; full alarm list exposed as attributes."""

    _attr_translation_key = "alarm_count"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "alarm_count")

    @property
    def native_value(self) -> int | None:
        """The entity's current value."""
        alert = self._payload.get(PAYLOAD_DEVICE_ALERT)
        if isinstance(alert, dict):
            count = alert.get(FIELD_SYS_ALERT_COUNT)
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                return count
        alarms = self._alarm
        if isinstance(alarms, list):
            return len(alarms)
        if isinstance(alarms, dict):
            # Some API variants wrap the list in a dict
            for key in ("list", "records", "alarms"):
                val = alarms.get(key)
                if isinstance(val, list):
                    return len(val)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current state."""
        alarms = self._alarm
        if isinstance(alarms, list):
            return {"alarms": alarms}
        if isinstance(alarms, dict):
            return dict(alarms)
        return {}


# ---------------------------------------------------------------------------
# Generic timestamp sensor — reads Unix-millis from a device-meta key
# ---------------------------------------------------------------------------
class JackeryTimestampSensor(JackeryEntity, SensorEntity):
    """Expose a timestamp value."""

    _attr_has_entity_name = True
    """Read a millisecond Unix timestamp from the device meta section."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        key: str,
        translation_key: str,
        source_key: str,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, key)
        self._attr_translation_key = translation_key
        self._source_key = source_key

    @property
    def native_value(self) -> datetime | None:
        """Implementation details.

        Convert a millisecond UTC timestamp from the entity's device metadata into a
        UTC datetime.

        Reads the milliseconds value from self._device_meta[self._source_key] and
        interprets it as epoch milliseconds.

        Returns:
            datetime: Timezone-aware UTC datetime parsed from the milliseconds value, or
            `None` if the value is missing or cannot be parsed.
        """
        ts_ms = self._device_meta.get(self._source_key)
        if not ts_ms:
            return None
        try:
            return datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC)
        except (TypeError, ValueError, OSError):
            return None


# ---------------------------------------------------------------------------
# Generic system-meta sensor — reads a string/scalar from system metadata
# ---------------------------------------------------------------------------
class JackerySystemMetaSensor(JackeryEntity, SensorEntity):
    """Expose scalar system metadata."""

    """Expose a static system-level field (grid standard, country, tz)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        key: str,
        translation_key: str,
        source_key: str,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, key)
        self._attr_translation_key = translation_key
        self._source_key = source_key

    @property
    def native_value(self) -> str | None:
        """The entity's current value."""
        return _system_meta_scalar_value(self._system.get(self._source_key))


# ---------------------------------------------------------------------------
# Firmware + location
# ---------------------------------------------------------------------------
class JackeryFirmwareSensor(JackeryEntity, SensorEntity):
    """Expose firmware metadata."""

    _attr_has_entity_name = True
    """Current firmware version with update info as attributes."""

    _attr_translation_key = "firmware_version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "firmware_version")

    @property
    def native_value(self) -> str | None:
        """The entity's current value."""
        value = self._ota.get(FIELD_CURRENT_VERSION)
        return value if isinstance(value, str) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current state."""
        ota = self._ota
        attrs: dict[str, Any] = {}
        # Surface only fields that are actually populated (many are null)
        for key in (
            FIELD_UPDATE_STATUS,
            FIELD_TARGET_VERSION,
            FIELD_TARGET_MODULE_VERSION,
            FIELD_UPDATE_CONTENT,
            FIELD_UPGRADE_TYPE,
        ):
            val = ota.get(key)
            if val is not None:
                attrs[key] = val
        ble_versions = self._payload.get(PAYLOAD_BLE_OTA_VERSIONS)
        if isinstance(ble_versions, list):
            attrs[PAYLOAD_BLE_OTA_VERSIONS] = deepcopy(ble_versions)
        ble_events = self._payload.get(PAYLOAD_BLE_OTA_EVENTS)
        if isinstance(ble_events, dict):
            attrs[PAYLOAD_BLE_OTA_EVENTS] = deepcopy(ble_events)
        return attrs


class JackeryLocationSensor(JackeryEntity, SensorEntity):
    """Expose device location metadata."""

    _attr_has_entity_name = True
    """Single axis of the configured GPS location (lat or lng).

    Disabled by default for privacy reasons; the coordinates come from
    whatever the user set in the Jackery app during device commissioning.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        key: str,
        axis: str,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, key)
        self._axis = axis
        self._attr_translation_key = key
        self._attr_native_unit_of_measurement = DEGREE

    @property
    def native_value(self) -> float | None:
        """The entity's current value."""
        return safe_float(self._location.get(self._axis))
