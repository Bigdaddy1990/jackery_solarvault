"""Sensor platform for the Jackery SolarVault integration.

This module is a thin entity layer. The data path is:

    Jackery API/MQTT --> coordinator (HTTP polling + MQTT push)
                     --> coordinator.data device payload
                     --> JackerySensor.native_value

The descriptions in ``SENSOR_DESCRIPTIONS`` and the period builders below
each carry inline references to the source-of-truth ``.md`` files
(``APP_POLLING_MQTT.md``, ``MQTT_PROTOCOL.md``, ``DATA_SOURCE_PRIORITY.md``,
``UNIQUE_ID_CONTRACT.md``) so the mapping from raw API field to HA entity
can be verified without re-reading the parser.

A consolidated entity-to-source-path table is auto-generated in
``docs/SENSOR_SOURCE_PATHS.md`` for fast lookup.

Conventions used in the per-sensor doc strings:

* ``HTTP:`` lines name the documented endpoint from
  ``APP_POLLING_MQTT.md`` (HTTP-Pfade table).
* ``MQTT:`` lines name the telemetry message and the field from
  ``MQTT_PROTOCOL.md`` (Telemetry messages section).
* ``Source-priority:`` follows ``DATA_SOURCE_PRIORITY.md``: live MQTT wins
  over HTTP property; period sensors use the documented app endpoint, with the
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
Sensor key suffix             HTTP endpoint (APP_POLLING_MQTT.md)                          Chart series (DATA_SOURCE_PRIORITY.md)
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
prefer ``/v1/device/stat/systemStatistic``. Per
``DATA_SOURCE_PRIORITY.md`` generation/carbon are guarded against broken
month-only cloud totals. ``total_revenue`` is additionally calculated from
year energy flows when available, because savings are self-consumed AC energy,
not raw PV generation revenue.

Unique IDs follow ``UNIQUE_ID_CONTRACT.md`` strictly:
``<device_id>_<stable_key_suffix>`` for the main device and
``<device_id>_battery_pack_<index>_<stable_key_suffix>`` for battery packs.
The ``key`` attribute of each ``JackerySensorDescription`` is the
``<stable_key_suffix>``; translation keys, names and any localized text
must never affect ``unique_id``.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
import logging
from typing import Any, Literal

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CURRENCY_EURO,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfEnergy,
    UnitOfMass,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    APP_CHART_BUCKET_BY_DATE_TYPE,
    APP_CHART_LABELS,
    APP_CHART_METRIC_KEY_BY_SECTION_PREFIX,
    APP_DEVICE_STAT_BATTERY_CHARGE,
    APP_DEVICE_STAT_BATTERY_DISCHARGE,
    APP_DEVICE_STAT_BATTERY_TO_GRID,
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
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_TODAY_BATTERY_CHARGE,
    APP_STAT_TODAY_BATTERY_DISCHARGE,
    APP_STAT_TODAY_GENERATION,
    APP_STAT_TODAY_LOAD,
    APP_STAT_TOTAL_CARBON,
    APP_STAT_TOTAL_CHARGE,
    APP_STAT_TOTAL_DISCHARGE,
    APP_STAT_TOTAL_GENERATION,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    APP_STAT_TOTAL_REVENUE,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_TOTAL_GUARD_META,
    APP_YEAR_BACKFILL_META,
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    CT_ATTRIBUTE_FIELDS,
    CT_NEGATIVE_PHASE_POWER_FIELDS,
    CT_POSITIVE_PHASE_POWER_FIELDS,
    CT_TOTAL_POWER_PAIR,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    DEFAULT_STORM_WARNING_MINUTES,
    DOMAIN,
    FIELD_ABILITY,
    FIELD_BAT_IN_PW,
    FIELD_BAT_NUM,
    FIELD_BAT_OUT_PW,
    FIELD_BAT_SOC,
    FIELD_BAT_STATE,
    FIELD_CELL_TEMP,
    FIELD_CHARGE_PLAN_PW,
    FIELD_COMM_MODE,
    FIELD_COMM_STATE,
    FIELD_CT_POWER,
    FIELD_CT_POWER1,
    FIELD_CT_POWER2,
    FIELD_CT_POWER3,
    FIELD_CT_STAT,
    FIELD_CT_STATE,
    FIELD_CURRENT_VERSION,
    FIELD_DEFAULT_PW,
    FIELD_DEV_SN,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_EC,
    FIELD_ENERGY_PLAN_PW,
    FIELD_ETH_PORT,
    FIELD_FOLLOW_METER,
    FIELD_FUNC_ENABLE,
    FIELD_GRID_IN_PW,
    FIELD_GRID_OUT_PW,
    FIELD_GRID_STAT,
    FIELD_GRID_STATE,
    FIELD_GRID_STATE_ALT,
    FIELD_HOME_LOAD_PW,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_IN_ONGRID_PW,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IS_AUTO_STANDBY,
    FIELD_IS_FIRMWARE_UPGRADE,
    FIELD_IS_FOLLOW_METER_PW,
    FIELD_IT,
    FIELD_LATITUDE,
    FIELD_LOAD_PW,
    FIELD_LONGITUDE,
    FIELD_MAC,
    FIELD_MAX_GRID_STD_PW,
    FIELD_MAX_INV_STD_PW,
    FIELD_MAX_IOT_NUM,
    FIELD_MAX_OUT_PW,
    FIELD_MAX_SYS_IN_PW,
    FIELD_MAX_SYS_OUT_PW,
    FIELD_MINS_INTERVAL,
    FIELD_MODEL,
    FIELD_MODEL_NAME,
    FIELD_OFF_GRID_AUTO_OFF_TIME,
    FIELD_OFF_GRID_DOWN,
    FIELD_OFF_GRID_DOWN_TIME,
    FIELD_OFF_GRID_TIME,
    FIELD_ON_GRID_STAT,
    FIELD_ONGRID_STAT,
    FIELD_OP,
    FIELD_OT,
    FIELD_OTHER_LOAD_PW,
    FIELD_OUT_GRID_SIDE_PW,
    FIELD_OUT_ONGRID_PW,
    FIELD_OUT_PW,
    FIELD_PV1,
    FIELD_PV2,
    FIELD_PV3,
    FIELD_PV4,
    FIELD_PV_PW,
    FIELD_RB,
    FIELD_REBOOT,
    FIELD_SCAN_NAME,
    FIELD_SINGLE_PRICE,
    FIELD_SN,
    FIELD_SOC,
    FIELD_SOC_CHARGE_LIMIT,
    FIELD_SOC_CHG_LIMIT,
    FIELD_SOC_DISCHARGE_LIMIT,
    FIELD_SOC_DISCHG_LIMIT,
    FIELD_STACK_IN_PW,
    FIELD_STACK_OUT_PW,
    FIELD_STANDBY_PW,
    FIELD_STAT,
    FIELD_STORM,
    FIELD_SW_EPS_IN_PW,
    FIELD_SW_EPS_OUT_PW,
    FIELD_SW_EPS_STATE,
    FIELD_TARGET_MODULE_VERSION,
    FIELD_TARGET_VERSION,
    FIELD_TEMP_UNIT,
    FIELD_TYPE_NAME,
    FIELD_UPDATE_CONTENT,
    FIELD_UPDATE_STATUS,
    FIELD_UPGRADE_TYPE,
    FIELD_VERSION,
    FIELD_WIP,
    FIELD_WNAME,
    FIELD_WORK_MODEL,
    FIELD_WPC,
    FIELD_WPS,
    FIELD_WSIG,
    MANUFACTURER,
    PAYLOAD_ALARM,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_BATTERY_TRENDS,
    PAYLOAD_CT_METER,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_OTA,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_PV_TRENDS,
    PAYLOAD_STATISTIC,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_WEATHER_PLAN,
    TASK_PLAN_BODY,
    TASK_PLAN_TASKS,
)
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import (
    HomeConsumptionPower,
    append_unique_entity,
    calculated_smart_meter_power,
    compact_json,
    directional_power_value,
    effective_period_total_value,
    effective_trend_series_values,
    first_power_value,
    jackery_corrected_home_consumption_power,
    jackery_grid_side_input_power,
    jackery_grid_side_output_power,
    safe_float,
    safe_int,
    signed_phase_power_values,
    smart_meter_net_power,
    task_plan_value,
    trend_series_has_value,
    trend_series_key,
    trend_series_total,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Value extraction helpers
# ---------------------------------------------------------------------------
def _path(props: dict[str, Any], *keys: str) -> Any:
    """Walk a nested path; return None on missing intermediate keys."""
    node: Any = props
    for k in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return node


def _div(divisor: float) -> Callable[[Any], float | None]:
    def _f(value: Any) -> float | None:
        try:
            return round(float(value) / divisor, 2)
        except (TypeError, ValueError):
            return None

    return _f


def _identity(value: Any) -> Any:
    return value


def _temp_unit_label(value: Any) -> str | None:
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
@dataclass(frozen=True, kw_only=True)
class JackerySensorDescription(SensorEntityDescription):
    """Sensor description with a getter callable for nested paths.

    `getter` reads the primary location (typically a property dict).
    `fallbacks` is an optional tuple of callables, each receiving the full
    device payload (so they can inspect properties, task_plan, weather_plan,
    price, etc.). The first non-None fallback wins. This avoids hardcoding
    sensor-key-string compares inside the JackerySensor.native_value method.
    """

    getter: Callable[[dict[str, Any]], Any]
    transform: Callable[[Any], Any] = _identity
    fallbacks: tuple[Callable[[dict[str, Any]], Any], ...] = ()


def _prop(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda props: props.get(key)


def _prop_any(*keys: str) -> Callable[[dict[str, Any]], Any]:
    def _getter(props: dict[str, Any]) -> Any:
        for key in keys:
            if key in props and props.get(key) is not None:
                return props.get(key)
        return None

    return _getter


def _payload_http_prop(key: str) -> Callable[[dict[str, Any]], Any]:
    """Read the latest HTTP property value before MQTT overlay values."""

    def _getter(payload: dict[str, Any]) -> Any:
        http_props = payload.get(PAYLOAD_HTTP_PROPERTIES) or {}
        if isinstance(http_props, dict) and http_props:
            return http_props.get(key)
        props = payload.get(PAYLOAD_PROPERTIES) or {}
        if isinstance(props, dict):
            return props.get(key)
        return None

    return _getter


def _nested(*keys: str) -> Callable[[dict[str, Any]], Any]:
    return lambda props: _path(props, *keys)


def _pv_channel_power(channel_key: str) -> Callable[[dict[str, Any]], Any]:
    """Read per-channel PV power and default to 0W when channel exists."""

    def _getter(props: dict[str, Any]) -> Any:
        channel = props.get(channel_key)
        if not isinstance(channel, dict):
            return None
        if channel.get(FIELD_PV_PW) is None:
            return 0
        return channel.get(FIELD_PV_PW)

    return _getter


SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    # --- State of charge ---------------------------------------------------
    JackerySensorDescription(
        key="soc",
        translation_key="battery_soc",
        getter=_prop(FIELD_SOC),
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    JackerySensorDescription(
        key="bat_soc",
        translation_key="battery_soc_internal",
        getter=_prop(FIELD_BAT_SOC),
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    # --- Temperatures ------------------------------------------------------
    JackerySensorDescription(
        key="cell_temperature",
        translation_key="cell_temperature",
        getter=_prop(FIELD_CELL_TEMP),
        transform=_div(10),
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    # --- Battery power -----------------------------------------------------
    JackerySensorDescription(
        key="battery_charge_power",
        translation_key="battery_charge_power",
        getter=_prop(FIELD_BAT_IN_PW),
        fallbacks=(_payload_http_prop(FIELD_BAT_IN_PW),),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:battery-arrow-up",
    ),
    JackerySensorDescription(
        key="battery_discharge_power",
        translation_key="battery_discharge_power",
        getter=_prop(FIELD_BAT_OUT_PW),
        fallbacks=(_payload_http_prop(FIELD_BAT_OUT_PW),),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:battery-arrow-down",
    ),
    # --- Solar / PV --------------------------------------------------------
    JackerySensorDescription(
        key="pv_power_total",
        translation_key="pv_power_total",
        getter=_prop(FIELD_PV_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:solar-power",
    ),
    JackerySensorDescription(
        key="pv1_power",
        translation_key="pv1_power",
        getter=_pv_channel_power(FIELD_PV1),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:solar-panel",
    ),
    JackerySensorDescription(
        key="pv2_power",
        translation_key="pv2_power",
        getter=_pv_channel_power(FIELD_PV2),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:solar-panel",
    ),
    JackerySensorDescription(
        key="pv3_power",
        translation_key="pv3_power",
        getter=_pv_channel_power(FIELD_PV3),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:solar-panel",
    ),
    JackerySensorDescription(
        key="pv4_power",
        translation_key="pv4_power",
        getter=_pv_channel_power(FIELD_PV4),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:solar-panel",
    ),
    # --- Grid --------------------------------------------------------------
    JackerySensorDescription(
        key="grid_in_power",
        translation_key="grid_in_power",
        getter=_prop_any(FIELD_IN_ONGRID_PW, FIELD_GRID_IN_PW, FIELD_IN_GRID_SIDE_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:transmission-tower-import",
    ),
    JackerySensorDescription(
        key="grid_out_power",
        translation_key="grid_out_power",
        getter=_prop_any(
            FIELD_OUT_ONGRID_PW, FIELD_GRID_OUT_PW, FIELD_OUT_GRID_SIDE_PW
        ),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:transmission-tower-export",
    ),
    # --- EPS (Emergency Power Supply, AC OUT) ------------------------------
    JackerySensorDescription(
        key="eps_in_power",
        translation_key="eps_in_power",
        getter=_prop(FIELD_SW_EPS_IN_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="eps_out_power",
        translation_key="eps_out_power",
        getter=_prop(FIELD_SW_EPS_OUT_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    # --- Stack (additional battery pack) -----------------------------------
    JackerySensorDescription(
        key="stack_in_power",
        translation_key="stack_in_power",
        getter=_prop(FIELD_STACK_IN_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="stack_out_power",
        translation_key="stack_out_power",
        getter=_prop(FIELD_STACK_OUT_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    # --- Network / diagnostics --------------------------------------------
    JackerySensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        getter=_prop(FIELD_WSIG),
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="wifi_name",
        translation_key="wifi_name",
        getter=_prop(FIELD_WNAME),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wifi",
    ),
    JackerySensorDescription(
        key="wifi_ip",
        translation_key="wifi_ip",
        getter=_prop(FIELD_WIP),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:ip-network",
    ),
    JackerySensorDescription(
        key="mac_address",
        translation_key="mac_address",
        getter=_prop(FIELD_MAC),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:lan",
    ),
    JackerySensorDescription(
        key="eth_port",
        translation_key="eth_port",
        getter=_prop(FIELD_ETH_PORT),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:ethernet",
    ),
    JackerySensorDescription(
        key="ability_bits",
        translation_key="ability_bits",
        getter=_prop(FIELD_ABILITY),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chip",
    ),
    JackerySensorDescription(
        key="max_iot_num",
        translation_key="max_iot_num",
        getter=_prop(FIELD_MAX_IOT_NUM),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:devices",
    ),
    JackerySensorDescription(
        key="eps_switch_state",
        translation_key="eps_switch_state",
        getter=_prop(FIELD_SW_EPS_STATE),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:power-plug-outline",
    ),
    JackerySensorDescription(
        key="reboot_flag",
        translation_key="reboot_flag",
        getter=_prop(FIELD_REBOOT),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:restart",
    ),
    # --- Configuration readouts ------------------------------------------
    JackerySensorDescription(
        key="soc_charge_limit",
        translation_key="soc_charge_limit",
        getter=_prop_any(FIELD_SOC_CHG_LIMIT, FIELD_SOC_CHARGE_LIMIT),
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-charging-high",
    ),
    JackerySensorDescription(
        key="soc_discharge_limit",
        translation_key="soc_discharge_limit",
        getter=_prop_any(FIELD_SOC_DISCHG_LIMIT, FIELD_SOC_DISCHARGE_LIMIT),
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-low",
    ),
    JackerySensorDescription(
        key="max_output_power",
        translation_key="max_output_power",
        getter=_prop(FIELD_MAX_OUT_PW),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="max_grid_power",
        translation_key="max_grid_power",
        getter=_prop(FIELD_MAX_GRID_STD_PW),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="max_inverter_power",
        translation_key="max_inverter_power",
        getter=_prop(FIELD_MAX_INV_STD_PW),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="battery_count",
        translation_key="battery_count",
        getter=_prop(FIELD_BAT_NUM),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-multiple",
    ),
    JackerySensorDescription(
        key="battery_state",
        translation_key="battery_state",
        getter=_prop(FIELD_BAT_STATE),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-sync",
    ),
    JackerySensorDescription(
        key="auto_standby",
        translation_key="auto_standby",
        getter=_prop(FIELD_IS_AUTO_STANDBY),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:power-sleep",
        fallbacks=(
            lambda pl: task_plan_value(
                pl.get(PAYLOAD_TASK_PLAN) or {}, FIELD_IS_AUTO_STANDBY
            ),
        ),
    ),
    JackerySensorDescription(
        key="system_state",
        translation_key="system_state",
        getter=_prop(FIELD_STAT),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:state-machine",
    ),
    JackerySensorDescription(
        key="ongrid_state",
        translation_key="ongrid_state",
        getter=_prop_any(FIELD_ONGRID_STAT, FIELD_ON_GRID_STAT),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:transmission-tower",
    ),
    JackerySensorDescription(
        key="ct_state",
        translation_key="ct_state",
        getter=_prop_any(FIELD_CT_STAT, FIELD_CT_STATE),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:counter",
    ),
    JackerySensorDescription(
        key="grid_state",
        translation_key="grid_state",
        getter=_prop_any(FIELD_GRID_STATE, FIELD_GRID_STATE_ALT, FIELD_GRID_STAT),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:transmission-tower-off",
    ),
    JackerySensorDescription(
        key="work_mode",
        translation_key="work_mode",
        getter=_prop(FIELD_WORK_MODEL),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:tune-variant",
        fallbacks=(
            lambda pl: task_plan_value(
                pl.get(PAYLOAD_TASK_PLAN) or {}, FIELD_WORK_MODEL
            ),
            lambda pl: (
                7
                if safe_int((pl.get(PAYLOAD_PRICE) or {}).get(FIELD_DYNAMIC_OR_SINGLE))
                == 1
                else None
            ),
        ),
    ),
    # Removed max_feed_grid sensor
    JackerySensorDescription(
        key="max_system_output_power",
        translation_key="max_system_output_power",
        getter=_prop(FIELD_MAX_SYS_OUT_PW),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="max_system_input_power",
        translation_key="max_system_input_power",
        getter=_prop(FIELD_MAX_SYS_IN_PW),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="off_grid_time",
        translation_key="off_grid_time",
        getter=_prop(FIELD_OFF_GRID_TIME),
        native_unit_of_measurement="min",
        icon="mdi:timer-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        fallbacks=(
            lambda pl: task_plan_value(
                pl.get(PAYLOAD_TASK_PLAN) or {},
                FIELD_OFF_GRID_TIME,
                FIELD_OFF_GRID_DOWN_TIME,
                FIELD_OFF_GRID_AUTO_OFF_TIME,
            ),
        ),
    ),
    JackerySensorDescription(
        key="default_power",
        translation_key="default_power",
        getter=_prop(FIELD_DEFAULT_PW),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="standby_power",
        translation_key="standby_power",
        getter=_prop(FIELD_STANDBY_PW),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="other_load_power",
        translation_key="other_load_power",
        getter=_prop(FIELD_OTHER_LOAD_PW),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="energy_plan_power",
        translation_key="energy_plan_power",
        getter=_prop(FIELD_ENERGY_PLAN_PW),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="charge_plan_power",
        translation_key="charge_plan_power",
        getter=_prop(FIELD_CHARGE_PLAN_PW),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Removed duplicate grid_side_in_power and grid_side_out_power sensors;
    # use grid_in_power and grid_out_power which read the same payload keys.
    JackerySensorDescription(
        key="follow_meter_state",
        translation_key="follow_meter_state",
        getter=_prop(FIELD_IS_FOLLOW_METER_PW),
        transform=safe_int,
        icon="mdi:gauge",
        entity_category=EntityCategory.DIAGNOSTIC,
        fallbacks=(
            lambda pl: task_plan_value(
                pl.get(PAYLOAD_TASK_PLAN) or {},
                FIELD_IS_FOLLOW_METER_PW,
                FIELD_FOLLOW_METER,
            ),
        ),
    ),
    JackerySensorDescription(
        key="off_grid_shutdown_state",
        translation_key="off_grid_shutdown_state",
        getter=_prop(FIELD_OFF_GRID_DOWN),
        transform=safe_int,
        icon="mdi:power-off",
        entity_category=EntityCategory.DIAGNOSTIC,
        fallbacks=(
            lambda pl: task_plan_value(
                pl.get(PAYLOAD_TASK_PLAN) or {}, FIELD_OFF_GRID_DOWN
            ),
        ),
    ),
    JackerySensorDescription(
        key="function_enable_flags",
        translation_key="function_enable_flags",
        getter=_prop(FIELD_FUNC_ENABLE),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chip",
    ),
    JackerySensorDescription(
        key="temp_unit",
        translation_key="temp_unit",
        getter=_prop(FIELD_TEMP_UNIT),
        transform=_temp_unit_label,
        icon="mdi:thermometer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="storm_warning_enabled",
        translation_key="storm_warning_enabled",
        getter=_prop(FIELD_WPS),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:weather-lightning-rainy",
        fallbacks=(
            lambda pl: (pl.get(PAYLOAD_WEATHER_PLAN) or {}).get(FIELD_WPS),
            lambda pl: task_plan_value(pl.get(PAYLOAD_TASK_PLAN) or {}, FIELD_WPS),
        ),
    ),
    JackerySensorDescription(
        key="storm_warning_minutes",
        translation_key="storm_warning_minutes",
        getter=_prop_any(FIELD_WPC, FIELD_MINS_INTERVAL),
        native_unit_of_measurement="min",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:timer-alert-outline",
        fallbacks=(
            lambda pl: _storm_minutes_from_plan(pl.get(PAYLOAD_WEATHER_PLAN) or {}),
            lambda pl: task_plan_value(
                pl.get(PAYLOAD_TASK_PLAN) or {},
                FIELD_WPC,
                FIELD_MINS_INTERVAL,
            ),
            lambda pl: _storm_minutes_fallback(
                pl.get(PAYLOAD_PROPERTIES) or {},
                pl.get(PAYLOAD_WEATHER_PLAN) or {},
                pl.get(PAYLOAD_TASK_PLAN) or {},
            ),
        ),
    ),
)

# ---------------------------------------------------------------------------
# Statistic sensors — sourced from _statistic section of payload
# ---------------------------------------------------------------------------
StatResetPeriod = Literal["day", "week", "month", "year"]


def _period_start(reset_period: StatResetPeriod) -> datetime:
    """Return the local start timestamp of the current statistic period."""
    now = dt_util.now()
    if reset_period == DATE_TYPE_DAY:
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if reset_period == DATE_TYPE_WEEK:
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if reset_period == DATE_TYPE_MONTH:
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True, kw_only=True)
class JackeryStatSensorDescription(SensorEntityDescription):
    """Sensor description sourcing from the statistic dict."""

    stat_key: str
    transform: Callable[[Any], Any] = _identity
    section: str = PAYLOAD_STATISTIC  # statistic | price | system
    fallback_sources: tuple[tuple[str, str], ...] = ()
    reset_period: StatResetPeriod | None = None


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


@dataclass(frozen=True, kw_only=True)
class JackeryBatteryPackSensorDescription(SensorEntityDescription):
    """Sensor description for one entry from battery_packs."""

    field: str
    transform: Callable[[Any], Any] = _identity


@dataclass(frozen=True, kw_only=True)
class JackerySmartMeterSensorDescription(SensorEntityDescription):
    """Sensor description for CT / smart-meter payloads."""

    field: str
    calculation: str | None = None
    aliases: tuple[str, ...] = ()
    negative_aliases: tuple[str, ...] = ()
    sum_fields: tuple[str, ...] = ()
    negative_sum_fields: tuple[str, ...] = ()
    transform: Callable[[Any], Any] = _identity


def _external_chart_metric_key(section: str, stat_key: str) -> str | None:
    """Return the external statistic metric key from const.py mapping."""
    for section_prefix, mapping in APP_CHART_METRIC_KEY_BY_SECTION_PREFIX.items():
        if section.startswith(section_prefix):
            return mapping.get(stat_key)
    return None


def _external_chart_bucket_key(section: str) -> str | None:
    """Return the HA external-statistics bucket for an app period section."""
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
    """Return True when a fetched app statistic section contains real values."""
    source = payload.get(section)
    if not isinstance(source, dict):
        return False
    if section.startswith(APP_SECTION_CT_STAT):
        return trend_series_has_value(source, section, stat_key)
    return any(key != APP_REQUEST_META for key in source)


def _stat_description_has_value(
    payload: dict[str, Any],
    description: JackeryStatSensorDescription,
) -> bool:
    """Return True when a stat entity has a usable app value now."""
    source = payload.get(description.section)
    if not isinstance(source, dict):
        return False
    if _trend_series_key(description.section, description.stat_key) is not None:
        if trend_series_has_value(source, description.section, description.stat_key):
            return True
        for section, stat_key in description.fallback_sources:
            fallback_source = payload.get(section)
            if isinstance(fallback_source, dict) and trend_series_has_value(
                fallback_source, section, stat_key
            ):
                return True
        return False
    if source.get(description.stat_key) is not None:
        return True
    for section, stat_key in description.fallback_sources:
        fallback_source = payload.get(section)
        if (
            isinstance(fallback_source, dict)
            and fallback_source.get(stat_key) is not None
        ):
            return True
    return False


STAT_DESCRIPTIONS: tuple[JackeryStatSensorDescription, ...] = (
    # Source: /v1/device/stat/systemStatistic field APP_STAT_TODAY_LOAD
    JackeryStatSensorDescription(
        key="today_load",
        translation_key="today_load",
        stat_key=APP_STAT_TODAY_LOAD,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Disabled by default since 2.4.0 — duplicates device_today_battery_charge
    # for single-device systems. The device-scoped sensor is the canonical one.
    # Source: /v1/device/stat/systemStatistic field APP_STAT_TODAY_BATTERY_CHARGE
    JackeryStatSensorDescription(
        key="today_battery_charge",
        translation_key="today_battery_charge",
        stat_key=APP_STAT_TODAY_BATTERY_CHARGE,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
    ),
    # Disabled by default since 2.4.0 — duplicates device_today_battery_discharge
    # for single-device systems. The device-scoped sensor is the canonical one.
    # Existing installs keep this entity enabled (HA convention preserves
    # registry_enabled state for already-known entities).
    # Source: /v1/device/stat/systemStatistic field APP_STAT_TODAY_BATTERY_DISCHARGE
    JackeryStatSensorDescription(
        key="today_battery_discharge",
        translation_key="today_battery_discharge",
        stat_key=APP_STAT_TODAY_BATTERY_DISCHARGE,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
    ),
    # Disabled by default since 2.4.0 — duplicates device_today_pv_energy
    # for single-device systems. The device-scoped sensor is the canonical one.
    # Source: /v1/device/stat/systemStatistic field APP_STAT_TODAY_GENERATION
    JackeryStatSensorDescription(
        key="today_generation",
        translation_key="today_generation",
        stat_key=APP_STAT_TODAY_GENERATION,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
    ),
    # Source: /v1/device/stat/systemStatistic field APP_STAT_TOTAL_GENERATION
    JackeryStatSensorDescription(
        key="total_generation",
        translation_key="total_generation",
        stat_key=APP_STAT_TOTAL_GENERATION,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: statistic_response.data.totalRevenue (lifetime cumulative
    # revenue from APP /v1/device/stat/systemStatistic).
    # state_class MUST be TOTAL (or None) — HA rejects TOTAL_INCREASING
    # on device_class=MONETARY. last_reset stays None (lifetime total),
    # which lets the recorder accept both rising and very-slightly-
    # falling cloud reports during the midnight transient as part of
    # a single running total instead of bucketed period totals.
    JackeryStatSensorDescription(
        key="total_revenue",
        translation_key="total_revenue",
        stat_key=APP_STAT_TOTAL_REVENUE,
        transform=safe_float,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        icon="mdi:currency-eur",
    ),
    # Source: /v1/device/stat/systemStatistic field APP_STAT_TOTAL_CARBON
    JackeryStatSensorDescription(
        key="total_carbon_saved",
        translation_key="total_carbon_saved",
        stat_key=APP_STAT_TOTAL_CARBON,
        transform=safe_float,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        icon="mdi:molecule-co2",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=week) field APP_STAT_TOTAL_SOLAR_ENERGY
    JackeryStatSensorDescription(
        key="pv_week_energy",
        translation_key="pv_week_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-power-variant",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=month) field APP_STAT_TOTAL_SOLAR_ENERGY
    JackeryStatSensorDescription(
        key="pv_month_energy",
        translation_key="pv_month_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-power-variant",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=year) field APP_STAT_TOTAL_SOLAR_ENERGY
    JackeryStatSensorDescription(
        key="pv_year_energy",
        translation_key="pv_year_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-power-variant",
    ),
    # --- APP_POLLING_MQTT.md: /v1/device/stat/pv per-channel totals -----
    # Source: /v1/device/stat/sys/pv (dateType=week) field APP_STAT_PV1_ENERGY
    JackeryStatSensorDescription(
        key="device_pv1_week_energy",
        translation_key="device_pv1_week_energy",
        stat_key=APP_STAT_PV1_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-panel",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=month) field APP_STAT_PV1_ENERGY
    JackeryStatSensorDescription(
        key="device_pv1_month_energy",
        translation_key="device_pv1_month_energy",
        stat_key=APP_STAT_PV1_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-panel",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=year) field APP_STAT_PV1_ENERGY
    JackeryStatSensorDescription(
        key="device_pv1_year_energy",
        translation_key="device_pv1_year_energy",
        stat_key=APP_STAT_PV1_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-panel",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=week) field APP_STAT_PV2_ENERGY
    JackeryStatSensorDescription(
        key="device_pv2_week_energy",
        translation_key="device_pv2_week_energy",
        stat_key=APP_STAT_PV2_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-panel",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=month) field APP_STAT_PV2_ENERGY
    JackeryStatSensorDescription(
        key="device_pv2_month_energy",
        translation_key="device_pv2_month_energy",
        stat_key=APP_STAT_PV2_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-panel",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=year) field APP_STAT_PV2_ENERGY
    JackeryStatSensorDescription(
        key="device_pv2_year_energy",
        translation_key="device_pv2_year_energy",
        stat_key=APP_STAT_PV2_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-panel",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=week) field APP_STAT_PV3_ENERGY
    JackeryStatSensorDescription(
        key="device_pv3_week_energy",
        translation_key="device_pv3_week_energy",
        stat_key=APP_STAT_PV3_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-panel",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=month) field APP_STAT_PV3_ENERGY
    JackeryStatSensorDescription(
        key="device_pv3_month_energy",
        translation_key="device_pv3_month_energy",
        stat_key=APP_STAT_PV3_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-panel",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=year) field APP_STAT_PV3_ENERGY
    JackeryStatSensorDescription(
        key="device_pv3_year_energy",
        translation_key="device_pv3_year_energy",
        stat_key=APP_STAT_PV3_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-panel",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=week) field APP_STAT_PV4_ENERGY
    JackeryStatSensorDescription(
        key="device_pv4_week_energy",
        translation_key="device_pv4_week_energy",
        stat_key=APP_STAT_PV4_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-panel",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=month) field APP_STAT_PV4_ENERGY
    JackeryStatSensorDescription(
        key="device_pv4_month_energy",
        translation_key="device_pv4_month_energy",
        stat_key=APP_STAT_PV4_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-panel",
    ),
    # Source: /v1/device/stat/sys/pv (dateType=year) field APP_STAT_PV4_ENERGY
    JackeryStatSensorDescription(
        key="device_pv4_year_energy",
        translation_key="device_pv4_year_energy",
        stat_key=APP_STAT_PV4_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:solar-panel",
    ),
    # System-level trend sensors (system_pv_*, system_home_*, system_battery_*).
    # These values largely duplicate the per-device and home sensors and were
    # removed to reduce redundancy in Home Assistant. Removing them here ensures
    # the integration only exposes one set of PV, home and battery statistics.
    # Source: section=f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_WEEK}" field APP_STAT_TOTAL_HOME_ENERGY
    JackeryStatSensorDescription(
        key="home_week_energy",
        translation_key="home_week_energy",
        stat_key=APP_STAT_TOTAL_HOME_ENERGY,
        section=f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:home-lightning-bolt",
    ),
    # Source: section=f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_MONTH}" field APP_STAT_TOTAL_HOME_ENERGY
    JackeryStatSensorDescription(
        key="home_month_energy",
        translation_key="home_month_energy",
        stat_key=APP_STAT_TOTAL_HOME_ENERGY,
        section=f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:home-lightning-bolt",
    ),
    # Source: section=f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_YEAR}" field APP_STAT_TOTAL_HOME_ENERGY
    JackeryStatSensorDescription(
        key="home_year_energy",
        translation_key="home_year_energy",
        stat_key=APP_STAT_TOTAL_HOME_ENERGY,
        section=f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:home-lightning-bolt",
    ),
    # --- APP_POLLING_MQTT.md: /v1/device/stat/onGrid --------------------
    # Jackery device grid-side input/output. This is NOT the public utility
    # meter, so never expose it as grid_import/grid_export.
    # Source: /v1/device/stat/sys/home (dateType=week) field APP_STAT_TOTAL_IN_GRID_ENERGY
    JackeryStatSensorDescription(
        key="device_ongrid_input_week_energy",
        translation_key="device_ongrid_input_week_energy",
        stat_key=APP_STAT_TOTAL_IN_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower-import",
    ),
    # Source: /v1/device/stat/sys/home (dateType=month) field APP_STAT_TOTAL_IN_GRID_ENERGY
    JackeryStatSensorDescription(
        key="device_ongrid_input_month_energy",
        translation_key="device_ongrid_input_month_energy",
        stat_key=APP_STAT_TOTAL_IN_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower-import",
    ),
    # Source: /v1/device/stat/sys/home (dateType=year) field APP_STAT_TOTAL_IN_GRID_ENERGY
    JackeryStatSensorDescription(
        key="device_ongrid_input_year_energy",
        translation_key="device_ongrid_input_year_energy",
        stat_key=APP_STAT_TOTAL_IN_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower-import",
    ),
    # Source: /v1/device/stat/sys/home (dateType=week) field APP_STAT_TOTAL_OUT_GRID_ENERGY
    JackeryStatSensorDescription(
        key="device_ongrid_output_week_energy",
        translation_key="device_ongrid_output_week_energy",
        stat_key=APP_STAT_TOTAL_OUT_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower-export",
    ),
    # Source: /v1/device/stat/sys/home (dateType=month) field APP_STAT_TOTAL_OUT_GRID_ENERGY
    JackeryStatSensorDescription(
        key="device_ongrid_output_month_energy",
        translation_key="device_ongrid_output_month_energy",
        stat_key=APP_STAT_TOTAL_OUT_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower-export",
    ),
    # Source: /v1/device/stat/sys/home (dateType=year) field APP_STAT_TOTAL_OUT_GRID_ENERGY
    JackeryStatSensorDescription(
        key="device_ongrid_output_year_energy",
        translation_key="device_ongrid_output_year_energy",
        stat_key=APP_STAT_TOTAL_OUT_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:transmission-tower-export",
    ),
    # APP_POLLING_MQTT.md keeps Smart-Meter/CT live values on MQTT `devType=3`.
    # Obsolete CT period entities are cleaned from the registry by __init__.py.
    # Source: /v1/device/stat/sys/battery (dateType=week) field APP_STAT_TOTAL_CHARGE
    JackeryStatSensorDescription(
        key="battery_charge_week_energy",
        translation_key="battery_charge_week_energy",
        stat_key=APP_STAT_TOTAL_CHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:battery-arrow-up",
    ),
    # Source: /v1/device/stat/sys/battery (dateType=month) field APP_STAT_TOTAL_CHARGE
    JackeryStatSensorDescription(
        key="battery_charge_month_energy",
        translation_key="battery_charge_month_energy",
        stat_key=APP_STAT_TOTAL_CHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:battery-arrow-up",
    ),
    # Source: /v1/device/stat/sys/battery (dateType=year) field APP_STAT_TOTAL_CHARGE
    JackeryStatSensorDescription(
        key="battery_charge_year_energy",
        translation_key="battery_charge_year_energy",
        stat_key=APP_STAT_TOTAL_CHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:battery-arrow-up",
    ),
    # Source: /v1/device/stat/sys/battery (dateType=week) field APP_STAT_TOTAL_DISCHARGE
    JackeryStatSensorDescription(
        key="battery_discharge_week_energy",
        translation_key="battery_discharge_week_energy",
        stat_key=APP_STAT_TOTAL_DISCHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:battery-arrow-down",
    ),
    # Source: /v1/device/stat/sys/battery (dateType=month) field APP_STAT_TOTAL_DISCHARGE
    JackeryStatSensorDescription(
        key="battery_discharge_month_energy",
        translation_key="battery_discharge_month_energy",
        stat_key=APP_STAT_TOTAL_DISCHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:battery-arrow-down",
    ),
    # Source: /v1/device/stat/sys/battery (dateType=year) field APP_STAT_TOTAL_DISCHARGE
    JackeryStatSensorDescription(
        key="battery_discharge_year_energy",
        translation_key="battery_discharge_year_energy",
        stat_key=APP_STAT_TOTAL_DISCHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        icon="mdi:battery-arrow-down",
    ),
    # Removed smart meter panel energy sensors (charging/discharging)
    # Single-tariff power price from powerPriceConfig
    # Source: /v1/device/stat/price field FIELD_SINGLE_PRICE
    JackeryStatSensorDescription(
        key="power_price",
        translation_key="power_price",
        stat_key=FIELD_SINGLE_PRICE,
        section=PAYLOAD_PRICE,
        transform=safe_float,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:currency-eur",
    ),
    # --- APP_POLLING_MQTT.md: /v1/device/stat/deviceStatistic ------------
    # The app endpoint name does not include a date range, but captures show
    # these values matching current-day totals. The dated app period endpoints
    # are fetched too and serve as backfill when deviceStatistic omits a field.
    # Source: /v1/device/stat/deviceStatistic field APP_DEVICE_STAT_PV_ENERGY
    JackeryStatSensorDescription(
        key="device_today_pv_energy",
        translation_key="device_today_pv_energy",
        stat_key=APP_DEVICE_STAT_PV_ENERGY,
        section=PAYLOAD_DEVICE_STATISTIC,
        fallback_sources=(
            (f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_SOLAR_ENERGY),
        ),
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: /v1/device/stat/deviceStatistic field APP_DEVICE_STAT_BATTERY_CHARGE
    JackeryStatSensorDescription(
        key="device_today_battery_charge",
        translation_key="device_today_battery_charge",
        stat_key=APP_DEVICE_STAT_BATTERY_CHARGE,
        section=PAYLOAD_DEVICE_STATISTIC,
        fallback_sources=(
            (f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_CHARGE),
        ),
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: /v1/device/stat/deviceStatistic field APP_DEVICE_STAT_BATTERY_DISCHARGE
    JackeryStatSensorDescription(
        key="device_today_battery_discharge",
        translation_key="device_today_battery_discharge",
        stat_key=APP_DEVICE_STAT_BATTERY_DISCHARGE,
        section=PAYLOAD_DEVICE_STATISTIC,
        fallback_sources=(
            (f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_DISCHARGE),
        ),
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: /v1/device/stat/deviceStatistic field APP_DEVICE_STAT_ONGRID_INPUT
    JackeryStatSensorDescription(
        key="device_today_ongrid_input",
        translation_key="device_today_ongrid_input",
        stat_key=APP_DEVICE_STAT_ONGRID_INPUT,
        section=PAYLOAD_DEVICE_STATISTIC,
        fallback_sources=(
            (f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_IN_GRID_ENERGY),
        ),
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: /v1/device/stat/deviceStatistic field APP_DEVICE_STAT_ONGRID_OUTPUT
    JackeryStatSensorDescription(
        key="device_today_ongrid_output",
        translation_key="device_today_ongrid_output",
        stat_key=APP_DEVICE_STAT_ONGRID_OUTPUT,
        section=PAYLOAD_DEVICE_STATISTIC,
        fallback_sources=(
            (
                f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}",
                APP_STAT_TOTAL_OUT_GRID_ENERGY,
            ),
        ),
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: /v1/device/stat/deviceStatistic field APP_DEVICE_STAT_ONGRID_TO_BATTERY
    JackeryStatSensorDescription(
        key="device_today_ongrid_to_battery",
        translation_key="device_today_ongrid_to_battery",
        stat_key=APP_DEVICE_STAT_ONGRID_TO_BATTERY,
        section=PAYLOAD_DEVICE_STATISTIC,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: /v1/device/stat/deviceStatistic field APP_DEVICE_STAT_PV_TO_BATTERY
    JackeryStatSensorDescription(
        key="device_today_pv_to_battery",
        translation_key="device_today_pv_to_battery",
        stat_key=APP_DEVICE_STAT_PV_TO_BATTERY,
        section=PAYLOAD_DEVICE_STATISTIC,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: /v1/device/stat/deviceStatistic field APP_DEVICE_STAT_BATTERY_TO_GRID
    JackeryStatSensorDescription(
        key="device_today_battery_to_ongrid",
        translation_key="device_today_battery_to_ongrid",
        stat_key=APP_DEVICE_STAT_BATTERY_TO_GRID,
        section=PAYLOAD_DEVICE_STATISTIC,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
)


BATTERY_PACK_SENSOR_DESCRIPTIONS: tuple[JackeryBatteryPackSensorDescription, ...] = (
    JackeryBatteryPackSensorDescription(
        key="soc",
        translation_key="battery_pack_soc",
        field=FIELD_BAT_SOC,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:battery",
    ),
    JackeryBatteryPackSensorDescription(
        key="cell_temperature",
        translation_key="battery_pack_cell_temperature",
        field=FIELD_CELL_TEMP,
        transform=_div(10),
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        icon="mdi:thermometer",
    ),
    JackeryBatteryPackSensorDescription(
        key="charge_power",
        translation_key="battery_pack_charge_power",
        field=FIELD_IN_PW,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:battery-arrow-up",
    ),
    JackeryBatteryPackSensorDescription(
        key="discharge_power",
        translation_key="battery_pack_discharge_power",
        field=FIELD_OUT_PW,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:battery-arrow-down",
    ),
    JackeryBatteryPackSensorDescription(
        key="firmware_version",
        translation_key="battery_pack_firmware_version",
        field=FIELD_VERSION,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:chip",
    ),
    JackeryBatteryPackSensorDescription(
        key="serial_number",
        translation_key="battery_pack_serial_number",
        field=FIELD_DEVICE_SN,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:barcode",
    ),
    JackeryBatteryPackSensorDescription(
        key="communication_state",
        translation_key="battery_pack_communication_state",
        field=FIELD_COMM_STATE,
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:access-point-network",
    ),
    JackeryBatteryPackSensorDescription(
        key="update_status",
        translation_key="battery_pack_update_status",
        field=FIELD_UPDATE_STATUS,
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:update",
    ),
)


SMART_METER_SENSOR_DESCRIPTIONS: tuple[JackerySmartMeterSensorDescription, ...] = (
    JackerySmartMeterSensorDescription(
        key="power",
        translation_key="smart_meter_power",
        field=FIELD_CT_POWER,
        aliases=(CT_TOTAL_POWER_PAIR[0],),
        negative_aliases=(CT_TOTAL_POWER_PAIR[1],),
        sum_fields=CT_POSITIVE_PHASE_POWER_FIELDS,
        negative_sum_fields=CT_NEGATIVE_PHASE_POWER_FIELDS,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:meter-electric-outline",
    ),
    JackerySmartMeterSensorDescription(
        key="net_import_power",
        translation_key="smart_meter_net_import_power",
        field=FIELD_CT_POWER,
        calculation="net_import",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:transmission-tower-import",
    ),
    JackerySmartMeterSensorDescription(
        key="net_export_power",
        translation_key="smart_meter_net_export_power",
        field=FIELD_CT_POWER,
        calculation="net_export",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:transmission-tower-export",
    ),
    JackerySmartMeterSensorDescription(
        key="gross_phase_import_power",
        translation_key="smart_meter_gross_phase_import_power",
        field=FIELD_CT_POWER,
        calculation="gross_import",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:transmission-tower-import",
    ),
    JackerySmartMeterSensorDescription(
        key="gross_phase_export_power",
        translation_key="smart_meter_gross_phase_export_power",
        field=FIELD_CT_POWER,
        calculation="gross_export",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:transmission-tower-export",
    ),
    JackerySmartMeterSensorDescription(
        key="gross_phase_flow_power",
        translation_key="smart_meter_gross_phase_flow_power",
        field=FIELD_CT_POWER,
        calculation="gross_flow",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:current-ac",
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_power",
        translation_key="smart_meter_phase_1_power",
        field=FIELD_CT_POWER1,
        aliases=(CT_POSITIVE_PHASE_POWER_FIELDS[0],),
        negative_aliases=(CT_NEGATIVE_PHASE_POWER_FIELDS[0],),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:meter-electric-outline",
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_power",
        translation_key="smart_meter_phase_2_power",
        field=FIELD_CT_POWER2,
        aliases=(CT_POSITIVE_PHASE_POWER_FIELDS[1],),
        negative_aliases=(CT_NEGATIVE_PHASE_POWER_FIELDS[1],),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:meter-electric-outline",
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_power",
        translation_key="smart_meter_phase_3_power",
        field=FIELD_CT_POWER3,
        aliases=(CT_POSITIVE_PHASE_POWER_FIELDS[2],),
        negative_aliases=(CT_NEGATIVE_PHASE_POWER_FIELDS[2],),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:meter-electric-outline",
    ),
)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the platform from a config entry."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    seen_unique_ids: set[str] = set()
    create_smart_meter_derived = bool(
        entry.options.get(
            CONF_CREATE_SMART_METER_DERIVED_SENSORS,
            entry.data.get(
                CONF_CREATE_SMART_METER_DERIVED_SENSORS,
                DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
            ),
        )
    )
    create_calculated_power = bool(
        entry.options.get(
            CONF_CREATE_CALCULATED_POWER_SENSORS,
            entry.data.get(
                CONF_CREATE_CALCULATED_POWER_SENSORS,
                DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
            ),
        )
    )

    def _append_unique(entity: SensorEntity) -> None:
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="sensor", logger=_LOGGER
        )

    for dev_id, payload in (coordinator.data or {}).items():
        props = payload.get(PAYLOAD_PROPERTIES) or {}

        # Add property-driven sensors. Do not suppress app/MQTT/Combine backed
        # fields at setup: several keys arrive after the first refresh and the
        # entity can stay unknown until the value is present.
        for desc in SENSOR_DESCRIPTIONS:
            _append_unique(JackerySensor(coordinator, dev_id, desc))

        # Statistic / price / device_statistic sensors. Create app statistic
        # entities only when the corresponding app payload contains a usable
        # value; this avoids permanent "unknown" entities from empty/unsupported
        # chart sections while still exposing every fetched app statistic.
        for stat_desc in STAT_DESCRIPTIONS:
            if not _stat_description_has_value(payload, stat_desc):
                continue
            _append_unique(JackeryStatSensor(coordinator, dev_id, stat_desc))

        if create_calculated_power:
            _append_unique(JackeryBatteryNetPowerSensor(coordinator, dev_id))
            _append_unique(JackeryBatteryStackNetPowerSensor(coordinator, dev_id))
            _append_unique(JackeryGridNetPowerSensor(coordinator, dev_id))

        # Alarm sensor (even if empty, useful to see "0 active alarms")
        if payload.get(PAYLOAD_ALARM) is not None:
            _append_unique(JackeryAlarmSensor(coordinator, dev_id))

        # Firmware version from APP_POLLING_MQTT.md /v1/device/ota/list
        if (payload.get(PAYLOAD_OTA) or {}).get(FIELD_CURRENT_VERSION):
            _append_unique(JackeryFirmwareSensor(coordinator, dev_id))

        # Add-on battery packs come from the app's MQTT BatteryPackSub model.
        # Create the complete pack entity set once a pack exists or batNum
        # announces it; individual values may arrive in later MQTT/OTA packets.
        packs = payload.get(PAYLOAD_BATTERY_PACKS) or []
        if isinstance(packs, list):
            valid_packs = [pack for pack in packs if isinstance(pack, dict)]
            bat_num = safe_int(props.get(FIELD_BAT_NUM))
            if bat_num is None:
                pack_count = min(5, len(valid_packs))
            else:
                # App model: main battery telemetry lives in HomeBody while
                # add-on battery cards use BatteryPackSub entries. `batNum`
                # is the expected pack/card count, not a reason to collapse
                # the first pack into the main device.
                pack_count = min(5, max(len(valid_packs), max(0, bat_num)))
            for index in range(1, pack_count + 1):
                valid_packs[index - 1] if index <= len(valid_packs) else {}
                for pack_desc in BATTERY_PACK_SENSOR_DESCRIPTIONS:
                    if pack_desc.field == FIELD_CELL_TEMP and not any(
                        FIELD_CELL_TEMP in item for item in valid_packs
                    ):
                        continue
                    _append_unique(
                        JackeryBatteryPackSensor(
                            coordinator,
                            dev_id,
                            pack_index=index,
                            description=pack_desc,
                            enabled_default=True,
                        )
                    )

        # Smart meter / CT values arrive through MQTT sub-device responses.
        # Create them when discovery confirms a meter accessory, or when a
        # CT payload was already received before entity setup.
        if coordinator._has_smart_meter_accessory(payload) or payload.get(
            PAYLOAD_CT_METER
        ):
            for ct_desc in SMART_METER_SENSOR_DESCRIPTIONS:
                if ct_desc.calculation and not create_smart_meter_derived:
                    continue
                _append_unique(JackerySmartMeterSensor(coordinator, dev_id, ct_desc))
            if create_smart_meter_derived:
                _append_unique(JackeryHomeConsumptionPowerSensor(coordinator, dev_id))
        elif create_smart_meter_derived and any(
            key in props and props.get(key) is not None
            for key in (FIELD_OTHER_LOAD_PW, FIELD_HOME_LOAD_PW, FIELD_LOAD_PW)
        ):
            # Some firmware/API responses expose the live app house-load value
            # directly before a CT payload has arrived. Keep the user-facing
            # Hausverbrauch sensor available instead of waiting for CT data.
            _append_unique(JackeryHomeConsumptionPowerSensor(coordinator, dev_id))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------
class JackerySensor(JackeryEntity, SensorEntity):
    """Jackery sensor for the Jackery SolarVault integration."""

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
        # App-backed values should not land in HA as disabled entities. The
        # integration now skips non-app diagnostics instead of disabling them.
        self._attr_entity_registry_enabled_default = True

    @property
    def native_value(self) -> Any:
        """Return the entity's current value."""
        raw = self.entity_description.getter(self._properties)
        if raw is None:
            for fallback in self.entity_description.fallbacks:
                raw = fallback(self._payload)
                if raw is not None:
                    break
        if raw is None:
            return None
        return self.entity_description.transform(raw)


class JackeryStatSensor(JackeryEntity, SensorEntity):
    """Sensor sourced from the statistic / price section of the payload.

    Performance contract:

    Home Assistant evaluates ``native_value``, ``last_reset`` and
    ``extra_state_attributes`` on every state write. The previous
    implementation recomputed ``effective_trend_series_values`` and
    ``compact_json`` three times per state write, plus once for HA's
    state-attribute serializer pass. For year-period sensors the
    ``expanded_year_series_values`` cross-validation path was repeated
    redundantly, contributing to per-update timings >0.4 s on slower
    Home Assistant Pi/HAOS targets.

    The fix: a tiny per-coordinator-update cache populated in
    ``_handle_coordinator_update``. After the cache is filled, both
    ``native_value`` and ``extra_state_attributes`` become O(1) dict
    reads and HA never sees more than one trend-series materialization
    per refresh per entity.

    The cache is invalidated automatically because
    ``_handle_coordinator_update`` runs on every coordinator data update
    and ``async_added_to_hass`` performs the initial fill before HA's
    first state read. Untriggered property reads outside an update cycle
    are still safe — they return the last cached snapshot.
    """

    entity_description: JackeryStatSensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: JackeryStatSensorDescription,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description
        self._reset_period = _period_from_stat_description(description)
        if self._reset_period is not None:
            # Period totals reset at the app's day/week/month/year boundary.
            # Keep state_class explicit here as a runtime guard, but the
            # descriptions carry the same value so entity metadata stays
            # consistent and testable.
            self._attr_state_class = SensorStateClass.TOTAL
        # Per-update snapshot. Populated by ``_handle_coordinator_update``
        # and the first ``async_added_to_hass`` call. Only the cached
        # values are exposed via ``native_value``/``extra_state_attributes``.
        self._cached_native_value: Any = None
        self._cached_attrs: dict[str, Any] = {
            "source_section": description.section,
            "source_key": description.stat_key,
        }

    @property
    def last_reset(self) -> datetime | None:
        """Local reset boundary for app period-total statistics.

        Anchored to the request metadata of the data we actually have,
        NOT to the wall clock. Background:

        After 0:00 the wall clock immediately points at the new day,
        but the Jackery cloud still serves yesterday's period values
        until the next refresh tick. With wall-clock anchoring HA
        Recorder sees ``value=4.77 kWh, last_reset=today 00:00`` and
        treats yesterday's total as today's bucket — and once today's
        real (smaller) value arrives, it interprets the change as a
        loss instead of a reset. The energy dashboard then shows a
        sharp negative spike at the day boundary.

        By anchoring last_reset to the begin_date that travelled with
        the response payload, last_reset only advances when a fresh
        period's data actually exists.
        """
        if self._reset_period is None:
            return None
        # Prefer the begin_date stamped on the source by the coordinator
        # (`source[APP_REQUEST_META][APP_REQUEST_BEGIN_DATE]`), fall
        # back to wall-clock period start for sources that have no
        # request metadata (legacy code paths).
        begin_iso = self._period_begin_from_meta()
        if begin_iso is None:
            return _period_start(self._reset_period)
        try:
            begin_date = date.fromisoformat(begin_iso)
        except ValueError:
            return _period_start(self._reset_period)
        # Local midnight on the request's begin_date.
        return datetime(
            begin_date.year,
            begin_date.month,
            begin_date.day,
            tzinfo=dt_util.now().tzinfo,
        )

    def _period_begin_from_meta(self) -> str | None:
        """Read the begin_date stamped on this sensor's source by the API.

        Returns ``None`` when the source has no request metadata yet —
        which happens for cached / non-period sources and during the
        very first coordinator update before the period endpoint has
        been polled.
        """
        section = self.entity_description.section
        source = self._source_for_section(section)
        request = source.get(APP_REQUEST_META)
        if not isinstance(request, dict):
            return None
        begin = request.get(APP_REQUEST_BEGIN_DATE) or request.get(
            APP_REQUEST_BEGIN_DATE_ALT
        )
        if not isinstance(begin, str) or not begin:
            return None
        return begin

    def _is_period_data_stale(self) -> bool:
        """Detect whether the source data is from a previous period.

        Returns True when the wall-clock period (computed via
        ``_period_start``) is strictly newer than the period stamped
        on the source's request metadata. The boundary is conservative:
        if either side is missing, we treat the data as fresh and
        publish normally.
        """
        if self._reset_period is None:
            return False
        wall_clock_start = _period_start(self._reset_period)
        begin_iso = self._period_begin_from_meta()
        if begin_iso is None:
            return False
        try:
            data_begin = date.fromisoformat(begin_iso)
        except ValueError:
            return False
        return wall_clock_start.date() > data_begin

    def _source_for_section(self, section: str) -> dict[str, Any]:
        if section == PAYLOAD_PRICE:
            return self._price
        if section == PAYLOAD_DEVICE_STATISTIC:
            return self._device_statistic
        if section == PAYLOAD_PV_TRENDS:
            return self._pv_trends
        if section == PAYLOAD_HOME_TRENDS:
            return self._home_trends
        if section == PAYLOAD_BATTERY_TRENDS:
            return self._battery_trends
        if section != PAYLOAD_STATISTIC:
            source = self._payload.get(section) or {}
            return source if isinstance(source, dict) else {}
        return self._statistic

    def _resolve_period_value(
        self,
        source: dict[str, Any],
        section: str,
        stat_key: str,
    ) -> tuple[Any, list[float] | None, float | None, float | None]:
        """Materialize chart series, sum and server total in one pass.

        Replaces the previous triple call (``_trend_series_sum`` ->
        ``effective_period_total_value`` -> ``effective_trend_series_values``)
        in the per-update path. Each helper internally re-runs
        ``expanded_year_series_values`` for device-year sections, so calling
        them three times multiplied the cross-validation cost.
        """
        values = effective_trend_series_values(source, section, stat_key)
        chart_series_sum: float | None = None
        if isinstance(values, list):
            chart_series_sum = round(
                sum(value for value in values if value is not None), 2
            )
        # ``effective_period_total_value`` already uses
        # ``effective_trend_series_values`` internally for device-year
        # sections, so the cross-validation runs at most once more here.
        # For non-year sections it just reads the documented total field
        # via ``safe_float`` — that path is already O(1).
        server_total = effective_period_total_value(source, section, stat_key)
        return None, values, chart_series_sum, server_total

    def _refresh_cache(self) -> None:
        """Recompute native_value and extra_state_attributes once per update."""
        section = self.entity_description.section
        stat_key = self.entity_description.stat_key
        source = self._source_for_section(section)
        series_key = _trend_series_key(section, stat_key)

        if series_key:
            # ---- period sensor path -------------------------------------
            _, values, chart_series_sum, server_total = self._resolve_period_value(
                source, section, stat_key
            )
            raw: float | None = chart_series_sum
            if raw is None:
                raw = server_total
            if raw is None:
                # APP_POLLING_MQTT.md fallback — try documented alternate
                # source (e.g. deviceStatistic for today_* sensors).
                for fb_section, fb_stat_key in self.entity_description.fallback_sources:
                    fb_source = self._source_for_section(fb_section)
                    fb_total = effective_period_total_value(
                        fb_source, fb_section, fb_stat_key
                    )
                    if fb_total is None:
                        fb_total = _trend_series_sum(fb_source, fb_section, fb_stat_key)
                    if fb_total is not None:
                        raw = fb_total
                        break

            # Stale-period guard: if the wall clock has crossed into a
            # new period boundary but the source still carries the
            # previous period's begin_date, refuse to publish the
            # stale value as the new period's bucket. Returning None
            # makes HA Recorder write "unavailable" for that brief
            # window — much safer than letting yesterday's total
            # masquerade as today's reading.
            if self._reset_period and self._is_period_data_stale():
                self._cached_native_value = None
            else:
                self._cached_native_value = (
                    self.entity_description.transform(raw) if raw is not None else None
                )

            attrs: dict[str, Any] = {
                "source_section": section,
                "source_key": stat_key,
            }
            attrs["chart_series_key"] = series_key
            attrs["chart_series_sum"] = chart_series_sum
            attrs["server_total"] = server_total
            labels = source.get(APP_CHART_LABELS)
            if isinstance(labels, list):
                attrs["period_labels"] = labels
                attrs["period_labels_count"] = len(labels)
                attrs["period_labels_json"] = compact_json(labels)
            if isinstance(values, list):
                attrs["period_values_count"] = len(values)
                attrs["period_values_json"] = compact_json(values)
                if isinstance(labels, list):
                    attrs["period_values_by_label_json"] = compact_json({
                        str(label): values[index]
                        for index, label in enumerate(labels[: len(values)])
                    })
            year_backfill = source.get(APP_YEAR_BACKFILL_META)
            if isinstance(year_backfill, dict):
                attrs["year_month_backfill"] = year_backfill
            request = source.get(APP_REQUEST_META)
            if isinstance(request, dict):
                attrs["request"] = request
                # For year-period responses, expose cloud completeness
                # metrics so users can spot months the cloud reported
                # as zero. We never modify the published value; this
                # is read-only telemetry per STRICT_WORK_INSTRUCTIONS
                # rule 7.
                date_type = request.get("dateType") or request.get("date_type")
                if (
                    date_type == DATE_TYPE_YEAR
                    and isinstance(values, list)
                    and len(values) == 12
                ):
                    nonzero_months = [
                        i for i, v in enumerate(values) if v not in (0, None, 0.0)
                    ]
                    attrs["cloud_year_chart_nonzero_months"] = len(nonzero_months)
                    if nonzero_months:
                        # Use 1-based month index for human readability
                        attrs["cloud_year_chart_first_nonzero_month"] = (
                            nonzero_months[0] + 1
                        )
                        attrs["cloud_year_chart_last_nonzero_month"] = (
                            nonzero_months[-1] + 1
                        )
                        # Heuristic: the cloud is incomplete if the only
                        # non-zero month is past January AND the chart
                        # has fewer non-zero months than the calendar
                        # would suggest. We surface it; we do not act
                        # on it.
                        attrs["cloud_year_appears_incomplete"] = (
                            len(nonzero_months) == 1 and nonzero_months[0] > 0
                        )
            self._cached_attrs = attrs
            return

        # ---- non-period stat path (totalGeneration, todayLoad, price, ...)
        raw = source.get(stat_key)
        if raw is None:
            for fb_section, fb_stat_key in self.entity_description.fallback_sources:
                fb_source = self._source_for_section(fb_section)
                raw = fb_source.get(fb_stat_key)
                if raw is not None:
                    break
        self._cached_native_value = (
            self.entity_description.transform(raw) if raw is not None else None
        )
        # Non-period stats keep a minimal attribute set per
        # DATA_SOURCE_PRIORITY.md "Minimal entity diagnostic attributes".
        self._cached_attrs = {
            "source_section": section,
            "source_key": stat_key,
        }
        total_guard = source.get(APP_TOTAL_GUARD_META)
        if isinstance(total_guard, dict):
            corrected = total_guard.get("corrected")
            if isinstance(corrected, dict) and stat_key in corrected:
                self._cached_attrs["total_lower_bound_guard"] = total_guard
        savings = source.get(APP_SAVINGS_CALC_META)
        if stat_key == APP_STAT_TOTAL_REVENUE and isinstance(savings, dict):
            self._cached_attrs["savings_calculation"] = savings

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh the cache before HA writes the new state."""
        self._refresh_cache()
        super()._handle_coordinator_update()

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
        self._refresh_cache()
        await super().async_added_to_hass()

    @property
    def native_value(self) -> Any:
        """Return the entity's current value."""
        return self._cached_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the current state."""
        return self._cached_attrs


class JackeryBatteryPackSensor(JackeryEntity, SensorEntity):
    """Per battery-pack sensor from MQTT BatteryPackSub plus OTA metadata."""

    entity_description: JackeryBatteryPackSensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        pack_index: int,
        description: JackeryBatteryPackSensorDescription,
        enabled_default: bool = True,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(
            coordinator,
            device_id,
            f"battery_pack_{pack_index}_{description.key}",
        )
        self._pack_index = pack_index
        self.entity_description = description
        self._attr_translation_key = description.translation_key
        self._attr_icon = description.icon
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_entity_registry_enabled_default = enabled_default

    @property
    def _pack(self) -> dict[str, Any]:
        packs = self._payload.get(PAYLOAD_BATTERY_PACKS) or []
        if not isinstance(packs, list):
            return {}
        try:
            pack = packs[self._pack_index - 1]
        except IndexError:
            return {}
        return pack if isinstance(pack, dict) else {}

    @property
    def native_value(self) -> Any:
        """Return the entity's current value."""
        field = self.entity_description.field
        raw = self._pack.get(field)
        if raw is None:
            alias_map = {
                FIELD_BAT_SOC: FIELD_RB,
                FIELD_IN_PW: FIELD_IP,
                FIELD_OUT_PW: FIELD_OP,
            }
            alias = alias_map.get(field)
            if alias:
                raw = self._pack.get(alias)
        if raw is None and field == FIELD_VERSION:
            raw = self._pack.get(FIELD_CURRENT_VERSION)
        if raw is None and field == FIELD_UPDATE_STATUS:
            raw = self._pack.get(FIELD_IS_FIRMWARE_UPGRADE)
        if raw is None:
            return None
        return self.entity_description.transform(raw)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device-registry metadata for this entity."""
        base_name = (
            self._system.get(FIELD_DEVICE_NAME)
            or self._discovery.get(FIELD_DEVICE_NAME)
            or self._properties.get(FIELD_WNAME)
            or "SolarVault"
        )
        pack = self._pack
        sn = pack.get(FIELD_DEVICE_SN) or pack.get(FIELD_DEV_SN) or pack.get(FIELD_SN)
        model = (
            pack.get(FIELD_MODEL)
            or pack.get(FIELD_MODEL_NAME)
            or pack.get(FIELD_TYPE_NAME)
            or "SolarVault Zusatzbatterie"
        )
        version = pack.get(FIELD_VERSION) or pack.get(FIELD_CURRENT_VERSION)
        return DeviceInfo(
            identifiers={
                (DOMAIN, f"{self._device_id}_battery_pack_{self._pack_index}")
            },
            manufacturer=MANUFACTURER,
            name=f"{base_name} Zusatzbatterie {self._pack_index}",
            model=str(model),
            serial_number=str(sn) if sn else None,
            sw_version=str(version) if version else None,
            via_device=(DOMAIN, self._device_id),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the current state."""
        attrs: dict[str, Any] = {"pack_index": self._pack_index}
        for key in (
            FIELD_IS_FIRMWARE_UPGRADE,
            FIELD_COMM_STATE,
            FIELD_COMM_MODE,
            FIELD_EC,
            FIELD_IT,
            FIELD_OT,
        ):
            if key in self._pack:
                attrs[key] = self._pack.get(key)
        return attrs


class JackerySmartMeterSensor(JackeryEntity, SensorEntity):
    """CT / smart-meter live power sensor from MQTT sub-device payloads."""

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

    @staticmethod
    def _directional_value(
        ct: dict[str, Any],
        positive_keys: tuple[str, ...],
        negative_keys: tuple[str, ...],
    ) -> float | None:
        """Return positive-key sum minus negative-key sum if any value exists."""
        return directional_power_value(ct, positive_keys, negative_keys)

    @classmethod
    def _signed_phase_values(cls, ct: dict[str, Any]) -> list[float] | None:
        """Return signed phase powers; positive=grid import, negative=export."""
        return signed_phase_power_values(ct)

    @classmethod
    def _net_power(cls, ct: dict[str, Any]) -> float | None:
        """Return the app-reported CT total; phase sum is only fallback."""
        return smart_meter_net_power(ct)

    @classmethod
    def _calculated_power(cls, ct: dict[str, Any], calculation: str) -> float | None:
        """Calculate derived smart-meter powers from signed phase values."""
        return calculated_smart_meter_power(ct, calculation)

    @property
    def native_value(self) -> Any:
        """Return the entity's current value."""
        ct = self._payload.get(PAYLOAD_CT_METER) or {}
        if not isinstance(ct, dict):
            return None
        raw = None

        if self.entity_description.calculation:
            raw = self._calculated_power(ct, self.entity_description.calculation)

        # The Jackery app exposes the CT net value through tPhasePw/tnPhasePw.
        # Phase fields are still used for gross phase import/export/flow.
        if raw is None and (
            self.entity_description.aliases or self.entity_description.negative_aliases
        ):
            raw = self._directional_value(
                ct,
                self.entity_description.aliases,
                self.entity_description.negative_aliases,
            )
        if raw is None and (
            self.entity_description.sum_fields
            or self.entity_description.negative_sum_fields
        ):
            raw = self._directional_value(
                ct,
                self.entity_description.sum_fields,
                self.entity_description.negative_sum_fields,
            )
        if raw is None:
            raw = ct.get(self.entity_description.field)
        if raw is None:
            return None
        value = self.entity_description.transform(raw)
        return round(value, 2) if isinstance(value, float) else value

    @property
    def device_info(self) -> DeviceInfo:
        """Return device-registry metadata for this entity."""
        ct = self._payload.get(PAYLOAD_CT_METER) or {}
        if not isinstance(ct, dict):
            ct = {}
        base_name = (
            self._system.get(FIELD_DEVICE_NAME)
            or self._discovery.get(FIELD_DEVICE_NAME)
            or self._properties.get(FIELD_WNAME)
            or "SolarVault"
        )
        scan_name = str(ct.get(FIELD_SCAN_NAME) or "Smart Meter")
        manufacturer = "Shelly" if "shelly" in scan_name.lower() else MANUFACTURER
        model = scan_name if scan_name and scan_name != "Smart Meter" else "Smart Meter"
        sn = ct.get(FIELD_DEVICE_SN) or ct.get(FIELD_SN) or ct.get(FIELD_MAC)
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_smart_meter")},
            manufacturer=manufacturer,
            name=f"{base_name} Smart Meter",
            model=model,
            serial_number=str(sn) if sn else None,
            via_device=(DOMAIN, self._device_id),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the current state."""
        ct = self._payload.get(PAYLOAD_CT_METER) or {}
        if not isinstance(ct, dict):
            return {}
        attrs: dict[str, Any] = {}
        for key in CT_ATTRIBUTE_FIELDS:
            if key in ct:
                attrs[key] = ct.get(key)
        phases = self._signed_phase_values(ct)
        if phases is not None:
            attrs["phase_a_signed_power"] = phases[0]
            attrs["phase_b_signed_power"] = phases[1]
            attrs["phase_c_signed_power"] = phases[2]
            attrs["signed_phase_convention"] = (
                "positive=grid_import, negative=grid_export"
            )
        if self.entity_description.calculation:
            attrs["calculation"] = self.entity_description.calculation
            attrs["source"] = (
                "total_fields"
                if self.entity_description.calculation in {"net_import", "net_export"}
                else "phase_fields"
            )
            return attrs
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


class JackeryRawPropertiesSensor(JackeryEntity, SensorEntity):
    """Diagnostic: full properties JSON as state attributes."""

    _attr_translation_key = "raw_properties"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:code-json"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "raw_properties")

    @property
    def native_value(self) -> int:
        """Return the entity's current value."""
        return len(self._properties)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the current state."""
        attrs: dict[str, Any] = {}
        for k, v in self._properties.items():
            try:
                json.dumps(v)
                attrs[k] = v
            except (TypeError, ValueError):
                attrs[k] = str(v)
        return attrs


class JackeryWeatherPlanSensor(JackeryEntity, SensorEntity):
    """Diagnostic sensor exposing the weather/storm plan payload."""

    _attr_translation_key = "weather_plan"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:weather-lightning-rainy"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, PAYLOAD_WEATHER_PLAN)

    @property
    def native_value(self) -> int:
        """Return the entity's current value."""
        storm = self._weather_plan.get(FIELD_STORM)
        if isinstance(storm, list):
            return len(storm)
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the current state."""
        plan = self._weather_plan
        if isinstance(plan, dict):
            return dict(plan)
        return {}


class JackeryTaskPlanSensor(JackeryEntity, SensorEntity):
    """Diagnostic sensor exposing schedule/task payloads."""

    _attr_translation_key = "task_plan"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calendar-clock"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, PAYLOAD_TASK_PLAN)

    @property
    def native_value(self) -> int:
        """Return the entity's current value."""
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
        """Return diagnostic attributes for the current state."""
        plan = self._task_plan
        if isinstance(plan, dict):
            return dict(plan)
        return {}


# ---------------------------------------------------------------------------
# Derived live-power sensors.
#
# These values are calculated from multiple live fields and may change sign. They
# intentionally keep device_class/unit for normal graphs but do not set
# state_class so Home Assistant does not build long-term statistics metadata for
# entity IDs that historically existed without a compatible recorder unit.
# ---------------------------------------------------------------------------
class JackeryBatteryNetPowerSensor(JackeryEntity, SensorEntity):
    """Net app-reported battery power: positive discharge, negative charge."""

    _attr_translation_key = "battery_net_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:battery-sync"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "battery_net_power")

    @property
    def native_value(self) -> int | None:
        """Return the entity's current value."""
        props = self._properties
        in_pw = safe_int(props.get(FIELD_BAT_IN_PW))
        out_pw = safe_int(props.get(FIELD_BAT_OUT_PW))
        if in_pw is None or out_pw is None:
            return None
        return out_pw - in_pw

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the current state."""
        http_props = self._http_properties
        props = http_props or self._properties
        merged = self._properties
        return {
            "formula": "batOutPw - batInPw",
            "source": "merged_property_fields",
            "positive": "battery discharge",
            "negative": "battery charge",
            "batOutPw": merged.get(FIELD_BAT_OUT_PW),
            "batInPw": merged.get(FIELD_BAT_IN_PW),
            "http_batOutPw": props.get(FIELD_BAT_OUT_PW),
            "http_batInPw": props.get(FIELD_BAT_IN_PW),
            "merged_batOutPw": merged.get(FIELD_BAT_OUT_PW),
            "merged_batInPw": merged.get(FIELD_BAT_IN_PW),
            "stackOutPw": merged.get(FIELD_STACK_OUT_PW),
            "stackInPw": merged.get(FIELD_STACK_IN_PW),
        }


class JackeryBatteryStackNetPowerSensor(JackeryEntity, SensorEntity):
    """Net complete battery-stack power from the main-device stack bus."""

    _attr_translation_key = "battery_stack_net_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:battery-sync"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "battery_stack_net_power")

    @property
    def native_value(self) -> int | None:
        """Return the entity's current value."""
        props = self._properties
        in_pw = safe_int(props.get(FIELD_STACK_IN_PW))
        out_pw = safe_int(props.get(FIELD_STACK_OUT_PW))
        if in_pw is None or out_pw is None:
            return None
        return out_pw - in_pw

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the current state."""
        props = self._properties
        http_props = self._http_properties
        return {
            "formula": "stackOutPw - stackInPw",
            "source": "main_device_stack_bus",
            "positive": "complete battery stack discharge",
            "negative": "complete battery stack charge",
            "stackOutPw": props.get(FIELD_STACK_OUT_PW),
            "stackInPw": props.get(FIELD_STACK_IN_PW),
            "merged_batOutPw": props.get(FIELD_BAT_OUT_PW),
            "merged_batInPw": props.get(FIELD_BAT_IN_PW),
            "http_batOutPw": http_props.get(FIELD_BAT_OUT_PW),
            "http_batInPw": http_props.get(FIELD_BAT_IN_PW),
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
    """Net grid-side power: positive = input, negative = output."""

    _attr_translation_key = "grid_net_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:transmission-tower"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "grid_net_power")

    @property
    def native_value(self) -> int | None:
        """Return the entity's current value."""
        props = self._properties
        in_pw = safe_int(jackery_grid_side_input_power(props))
        out_pw = safe_int(jackery_grid_side_output_power(props))
        if in_pw is None or out_pw is None:
            return None
        return in_pw - out_pw

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the current state."""
        props = self._properties
        return {
            "formula": "inOngridPw/gridInPw/inGridSidePw - outOngridPw/gridOutPw/outGridSidePw",
            "source": "on-grid_fields_preferred_then_grid-side_fallback",
            "positive": "grid-side input exceeds output",
            "negative": "grid-side output exceeds input",
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

    _attr_translation_key = "home_consumption_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:home-lightning-bolt"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "home_consumption_power")

    @staticmethod
    def _first_power(props: dict[str, Any], *keys: str) -> float | None:
        """Return the first available numeric power value for the given keys."""
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
        """Return home consumption and its components."""
        return jackery_corrected_home_consumption_power(ct, props)

    @property
    def native_value(self) -> float | None:
        """Return the entity's current value."""
        ct = self._payload.get(PAYLOAD_CT_METER) or {}
        if not isinstance(ct, dict):
            ct = {}
        result = self._home_consumption_power(ct, self._properties)
        if result is None:
            return None
        return round(result.value, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the current state."""
        ct = self._payload.get(PAYLOAD_CT_METER) or {}
        props = self._properties
        attrs: dict[str, Any] = {
            "formula": (
                "otherLoadPw if available, otherwise "
                "max(smart_meter_net_power - jackery_grid_side_input_power "
                "+ jackery_grid_side_output_power, 0)"
            ),
            "source": "otherLoadPw_preferred_then_smart_meter_ct_plus_jackery_ac_grid_side_fields",
            "scope": "Jackery-corrected home load; external non-Jackery generation must be measured separately",
        }
        if not isinstance(ct, dict):
            ct = {}

        result = self._home_consumption_power(ct, props)
        meter_net = JackerySmartMeterSensor._net_power(ct)
        input_available = self._grid_side_input_power(props) is not None
        output_available = self._grid_side_output_power(props) is not None
        reported_load_available = (
            self._first_power(
                props, FIELD_OTHER_LOAD_PW, FIELD_HOME_LOAD_PW, FIELD_LOAD_PW
            )
            is not None
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

        phases = JackerySmartMeterSensor._signed_phase_values(ct)
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
        return attrs


# ---------------------------------------------------------------------------
# Alarm sensor
# ---------------------------------------------------------------------------
class JackeryAlarmSensor(JackeryEntity, SensorEntity):
    """Count of active alarms; full alarm list exposed as attributes."""

    _attr_translation_key = "alarm_count"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:alert-circle-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "alarm_count")

    @property
    def native_value(self) -> int:
        """Return the entity's current value."""
        alarms = self._alarm
        if isinstance(alarms, list):
            return len(alarms)
        if isinstance(alarms, dict):
            # Some API variants wrap the list in a dict
            for key in ("list", "records", "alarms"):
                val = alarms.get(key)
                if isinstance(val, list):
                    return len(val)
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the current state."""
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
        """Return the entity's current value."""
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
    def native_value(self) -> Any:
        """Return the entity's current value."""
        return self._system.get(self._source_key)


# ---------------------------------------------------------------------------
# Firmware + location
# ---------------------------------------------------------------------------
class JackeryFirmwareSensor(JackeryEntity, SensorEntity):
    """Current firmware version with update info as attributes."""

    _attr_translation_key = "firmware_version"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chip"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "firmware_version")

    @property
    def native_value(self) -> str | None:
        """Return the entity's current value."""
        return self._ota.get(FIELD_CURRENT_VERSION)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the current state."""
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
        return attrs


class JackeryLocationSensor(JackeryEntity, SensorEntity):
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
        if axis == FIELD_LATITUDE:
            self._attr_icon = "mdi:latitude"
        elif axis == FIELD_LONGITUDE:
            self._attr_icon = "mdi:longitude"
        else:
            self._attr_icon = "mdi:map-marker"
        self._attr_native_unit_of_measurement = "°"

    @property
    def native_value(self) -> float | None:
        """Return the entity's current value."""
        return safe_float(self._location.get(self._axis))
