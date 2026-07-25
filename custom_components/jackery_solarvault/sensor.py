"""Sensor platform for the Jackery SolarVault integration.

This module is a thin entity layer. The data path is:

    Jackery API/MQTT --> coordinator (HTTP polling + MQTT push)
                     --> coordinator.data device payload
                     --> JackerySensor.native_value

The descriptions in ``SENSOR_DESCRIPTIONS`` and the period builders below
each carry inline references to the source-of-truth ``docs/PROTOCOL.md``
(§2 HTTP, §3-§5 MQTT, §8 data-source priority, §10 entity → source mapping,
§11 unique-ID contract) so the mapping from raw API field to HA entity can
be verified without re-reading the parser.

Conventions used in the per-sensor doc strings:

* ``HTTP:`` lines name the documented endpoint from PROTOCOL.md §2 (HTTP
  endpoints table).
* ``MQTT:`` lines name the telemetry message and the field from PROTOCOL.md
  §5 (telemetry messages).
* ``Source-priority:`` follows PROTOCOL.md §8: live MQTT wins over HTTP
  property; period sensors use the documented app endpoint, with the
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
prefer ``/v1/device/stat/systemStatistic``. Per
``PROTOCOL.md §8`` generation/carbon are guarded against broken
month-only cloud totals. ``total_revenue`` stays the raw Jackery app savings
KPI, while the separate ``_savings_calculation`` metadata and optional detail
sensor expose the locally calculated savings from self-consumed AC energy.

Unique IDs follow ``PROTOCOL.md §11`` strictly:
``<device_id>_<stable_key_suffix>`` for the main device and
``<device_id>_battery_pack_<serial-or-index>_<stable_key_suffix>`` for battery packs.
The ``key`` attribute of each ``JackerySensorDescription`` is the
``<stable_key_suffix>``; translation keys, names and any localized text
must never affect ``unique_id``.
"""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any, Final, Literal, cast
from weakref import WeakKeyDictionary

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    CURRENCY_EURO,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfMass,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util

from .const import (
    APP_CHART_BUCKET_BY_DATE_TYPE,
    APP_CHART_METRIC_KEY_BY_SECTION_PREFIX,
    APP_DEVICE_STAT_BATTERY_CHARGE,
    APP_DEVICE_STAT_BATTERY_DISCHARGE,
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
    APP_REQUEST_END_DATE,
    APP_REQUEST_END_DATE_ALT,
    APP_REQUEST_META,
    APP_SAVINGS_CALC_META,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_SECTION_SYMMETRY_STAT,
    APP_SECTION_TODAY_ENERGY,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_TODAY_BATTERY_ENERGY,
    APP_STAT_TODAY_FEED_IN_ENERGY,
    APP_STAT_TODAY_GRID_IMPORT_ENERGY,
    APP_STAT_TODAY_HOME_LOAD_ENERGY,
    APP_STAT_TODAY_LOAD,
    APP_STAT_TOTAL_CARBON,
    APP_STAT_TOTAL_CHARGE,
    APP_STAT_TOTAL_CT_INPUT_ENERGY,
    APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
    APP_STAT_TOTAL_DISCHARGE,
    APP_STAT_TOTAL_GENERATION,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_IN_EPS_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_OUT_EPS_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    APP_STAT_TOTAL_REVENUE,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_TOTAL_SOLAR_REVENUE,
    APP_STAT_UNIT,
    APP_TOTAL_GUARD_META,
    APP_UNIT_KWH,
    APP_YEAR_BACKFILL_META,
    CALCULATED_POWER_SENSOR_SUFFIXES,
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
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
    DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    DEFAULT_LIVE_SOURCES,
    DEFAULT_NULL_SEMANTICS,
    DEFAULT_STORM_WARNING_MINUTES,
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    DOMAIN,
    FIELD_ABILITY,
    FIELD_ACCD,
    FIELD_ACDT,
    FIELD_ACIP,
    FIELD_ACMODE,
    FIELD_ACOHZ,
    FIELD_ACOV,
    FIELD_ACOV1,
    FIELD_ACPS,
    FIELD_ACPSP,
    FIELD_ACPSS,
    FIELD_ALERT_COUNT,
    FIELD_AST,
    FIELD_BAT_IN_PW,
    FIELD_BAT_NUM,
    FIELD_BAT_OUT_PW,
    FIELD_BAT_SOC,
    FIELD_BAT_STATE,
    FIELD_BC,
    FIELD_BLS,
    FIELD_BOX,
    FIELD_BPC,
    FIELD_BT,
    FIELD_CELL_TEMP,
    FIELD_CHARGE_PLAN_PW,
    FIELD_CHARGING_ENERGY,
    FIELD_CIP,
    FIELD_CL,
    FIELD_COMM_MODE,
    FIELD_COMM_STATE,
    FIELD_COP,
    FIELD_CS,
    FIELD_CSC,
    FIELD_CSL,
    FIELD_CST,
    FIELD_CT_APPARENT_POWER,
    FIELD_CT_APPARENT_POWER1,
    FIELD_CT_APPARENT_POWER2,
    FIELD_CT_APPARENT_POWER3,
    FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_A_PHASE_ENERGY,
    FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_B_PHASE_ENERGY,
    FIELD_CT_CURRENT1,
    FIELD_CT_CURRENT2,
    FIELD_CT_CURRENT3,
    FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_C_PHASE_ENERGY,
    FIELD_CT_FREQUENCY,
    FIELD_CT_FUN_FORM,
    FIELD_CT_POWER,
    FIELD_CT_POWER1,
    FIELD_CT_POWER2,
    FIELD_CT_POWER3,
    FIELD_CT_POWER_FACTOR,
    FIELD_CT_POWER_FACTOR1,
    FIELD_CT_POWER_FACTOR2,
    FIELD_CT_POWER_FACTOR3,
    FIELD_CT_REACTIVE_POWER,
    FIELD_CT_REACTIVE_POWER1,
    FIELD_CT_REACTIVE_POWER2,
    FIELD_CT_REACTIVE_POWER3,
    FIELD_CT_STAT,
    FIELD_CT_STATE,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_CT_VOLT,
    FIELD_CT_VOLT1,
    FIELD_CT_VOLT2,
    FIELD_CT_VOLT3,
    FIELD_CURRENCY,
    FIELD_CURRENT_VERSION,
    FIELD_DEFAULT_PW,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_DHG_RECALL,
    FIELD_DISCHARGING_ENERGY,
    FIELD_DL,
    FIELD_DT,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_EC,
    FIELD_EIP,
    FIELD_EMAC,
    FIELD_ENERGY_PLAN_PW,
    FIELD_ETH_PORT,
    FIELD_FOLLOW_METER,
    FIELD_FUNC_ENABLE,
    FIELD_GRID_IN_PW,
    FIELD_GRID_OUT_PW,
    FIELD_GRID_STANDARD,
    FIELD_GRID_STAT,
    FIELD_GRID_STATE,
    FIELD_GRID_STATE_ALT,
    FIELD_IAC,
    FIELD_IACPW,
    FIELD_IDX,
    FIELD_IN_EGY,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_IN_ONGRID_PW,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IPAL_PW,
    FIELD_IS_AUTO_STANDBY,
    FIELD_IS_CONTRACT_AUTH,
    FIELD_IS_FIRMWARE_UPGRADE,
    FIELD_IS_FOLLOW_METER_PW,
    FIELD_IS_PACK_CONNECT,
    FIELD_IT,
    FIELD_LINK_TYPE,
    FIELD_LM,
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
    FIELD_NEXTDAY_HIGH,
    FIELD_NEXTDAY_LOW,
    FIELD_NM,
    FIELD_OAC,
    FIELD_OAC1_NAME,
    FIELD_OAC2,
    FIELD_OAC2_NAME,
    FIELD_OACL1,
    FIELD_OACL1_PW,
    FIELD_OACL2,
    FIELD_OACL2_PW,
    FIELD_OACPW,
    FIELD_OACT,
    FIELD_OACT1,
    FIELD_OACT2,
    FIELD_ODCC,
    FIELD_ODCCT,
    FIELD_ODCT,
    FIELD_ODCU,
    FIELD_ODCUT,
    FIELD_ODC_PORT,
    FIELD_OFF_GRID_AUTO_OFF_TIME,
    FIELD_OFF_GRID_DOWN,
    FIELD_OFF_GRID_DOWN_TIME,
    FIELD_OFF_GRID_TIME,
    FIELD_ONGRID_STAT,
    FIELD_ON_GRID_STAT,
    FIELD_OP,
    FIELD_OPAL_PW,
    FIELD_OT,
    FIELD_OTHER_LOAD_PW,
    FIELD_OUT_EGY,
    FIELD_OUT_GRID_SIDE_PW,
    FIELD_OUT_ONGRID_PW,
    FIELD_OUT_PW,
    FIELD_PAL,
    FIELD_PARAM,
    FIELD_PC,
    FIELD_PM,
    FIELD_PMB,
    FIELD_PR,
    FIELD_PRICE_COMPANY_NAME,
    FIELD_PSS,
    FIELD_PV1,
    FIELD_PV2,
    FIELD_PV3,
    FIELD_PV4,
    FIELD_PV_PW,
    FIELD_RB,
    FIELD_REBOOT,
    FIELD_SCAN_NAME,
    FIELD_SFC,
    FIELD_SINGLE_PRICE,
    FIELD_SLTB,
    FIELD_SN,
    FIELD_SOC,
    FIELD_SOCKET_LAST_UPDATE_TS,
    FIELD_SOCKET_PRIORITY,
    FIELD_SOCKET_SWITCH_CYCLE,
    FIELD_SOC_CHARGE_LIMIT,
    FIELD_SOC_CHG_LIMIT,
    FIELD_SOC_DISCHARGE_LIMIT,
    FIELD_SOC_DISCHG_LIMIT,
    FIELD_SPH,
    FIELD_SPH_PC,
    FIELD_SS,
    FIELD_STACK_IN_PW,
    FIELD_STACK_OUT_PW,
    FIELD_STANDBY_PW,
    FIELD_STAT,
    FIELD_STORM,
    FIELD_SUB_TYPE,
    FIELD_SW,
    FIELD_SWITCH_STATE,
    FIELD_SW_EPS_IN_PW,
    FIELD_SW_EPS_OUT_PW,
    FIELD_SW_EPS_STATE,
    FIELD_SYS_SWITCH,
    FIELD_TA,
    FIELD_TARGET_MODULE_VERSION,
    FIELD_TARGET_VERSION,
    FIELD_TEMP_UNIT,
    FIELD_TODAY_ENERGY,
    FIELD_TODAY_HIGH,
    FIELD_TODAY_LOW,
    FIELD_TOTAL_ENERGY,
    FIELD_TOTAL_N,
    FIELD_TOTAL_P,
    FIELD_TP,
    FIELD_TT,
    FIELD_TYPE_NAME,
    FIELD_UPDATE_CONTENT,
    FIELD_UPDATE_STATUS,
    FIELD_UPGRADE_TYPE,
    FIELD_UPS,
    FIELD_USBA1,
    FIELD_USBA2,
    FIELD_USBA3,
    FIELD_USBC1,
    FIELD_USBC2,
    FIELD_USBC3,
    FIELD_VERSION,
    FIELD_WIP,
    FIELD_WNAME,
    FIELD_WORK_MODEL,
    FIELD_WPC,
    FIELD_WPS,
    FIELD_WSIG,
    FIELD_WSS,
    MANUFACTURER,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_BATTERY_TRENDS,
    PAYLOAD_CIRCUIT_PROPERTY,
    PAYLOAD_CT_METER,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_DYNAMIC_PRICE,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_LOCAL_DAILY_ENERGY,
    PAYLOAD_METER_HEADS,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_PV_TRENDS,
    PAYLOAD_SMART_MODE,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_SMART_SCHEDULE,
    PAYLOAD_STATISTIC,
    PAYLOAD_SUBDEVICES,
    PAYLOAD_SYSTEM,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_TOU_SCHEDULE,
    PAYLOAD_WEATHER_PLAN,
    SAVINGS_DETAIL_SENSOR_SUFFIXES,
    SAVINGS_PRICE_PRECISION,
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
    sorted_battery_pack_payloads,
    subdevice_accessories,
)
from .entity import JackeryEntity, payload_properties_for_sources
from .util import (
    append_unique_entity,
    calculated_smart_meter_power,
    circuit_id,
    config_entry_bool_option,
    coordinator_entity_signature,
    day_power_energy_points,
    directional_power_value,
    effective_trend_series_values,
    first_nonblank_text,
    first_power_value,
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
    redacted_json_safe_payload,
    safe_bool,
    safe_float,
    safe_int,
    signed_phase_power_values,
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

if TYPE_CHECKING:
    from collections.abc import Callable  # ruff:ignore[unsorted-imports]
    from datetime import tzinfo

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import JackeryConfigEntry
    from .coordinator import JackerySolarVaultCoordinator
    from .util import (
        HomeConsumptionPower,
    )

# Coordinator-backed read-only platform: entities never perform their own
# refresh I/O, so disable per-entity parallel update scheduling.
PARALLEL_UPDATES = 0


_LOGGER = logging.getLogger(__name__)


_HOME_PAYLOAD_EVIDENCE_KEYS = frozenset({
    "autoStandby",
    "batInPw",
    "batOutPw",
    "batSoc",
    "defaultPw",
    "gridInPw",
    "isAutoStandby",
    "isFollowMeterPw",
    "maxGridStdPw",
    "maxInvStdPw",
    "maxIotNum",
    "maxOutPw",
    "pvPw",
    "swEps",
    "tempUnit",
    "workModel",
})
_PAYLOAD_HTTP_PROPERTIES = "http_properties"


def _has_home_payload_evidence(props: dict[str, Any]) -> bool:
    """Return True when props carry Home/System-body-only fields."""
    return any(key in props for key in _HOME_PAYLOAD_EVIDENCE_KEYS)


def _payload_has_home_payload_evidence(
    payload: dict[str, Any],
    props: dict[str, Any] | None = None,
) -> bool:
    """Return True when merged or raw payload props identify a Home/System body."""
    if props is not None and _has_home_payload_evidence(props):
        return True
    if isinstance(payload.get(PAYLOAD_SYSTEM), dict) and payload[PAYLOAD_SYSTEM]:
        return True
    for section in (PAYLOAD_PROPERTIES, _PAYLOAD_HTTP_PROPERTIES):
        raw = payload.get(section) or {}
        if isinstance(raw, dict) and _has_home_payload_evidence(raw):
            return True
    return False


def _is_portable_payload(
    payload: dict[str, Any],
    props: dict[str, Any] | None = None,
) -> bool:
    """Return True for Explorer/Portable payloads without Home/System evidence."""
    if _payload_has_home_payload_evidence(payload, props):
        return False
    for section in (PAYLOAD_DEVICE, PAYLOAD_DISCOVERY):
        meta = payload.get(section) or {}
        if (
            isinstance(meta, dict)
            and meta.get(PAYLOAD_DISCOVERY_SOURCE) == DISCOVERY_SOURCE_LEGACY_BIND_LIST
        ):
            return True
    return False


# Max number of per-bucket period values exposed as an attribute (days in a
# month). Larger series are chart-curve arrays, not month buckets.
_MAX_PERIOD_VALUES: Final = 31


# ---------------------------------------------------------------------------
# Value extraction helpers
# ---------------------------------------------------------------------------
LOCAL_DAILY_METRIC_BY_SENSOR_KEY: dict[str, str] = {
    "device_today_pv_energy": APP_DEVICE_STAT_PV_ENERGY,
    "device_pv1_day_energy": APP_STAT_PV1_ENERGY,
    "device_pv2_day_energy": APP_STAT_PV2_ENERGY,
    "device_pv3_day_energy": APP_STAT_PV3_ENERGY,
    "device_pv4_day_energy": APP_STAT_PV4_ENERGY,
    "device_today_battery_charge": APP_DEVICE_STAT_BATTERY_CHARGE,
    "device_today_battery_discharge": APP_DEVICE_STAT_BATTERY_DISCHARGE,
    "device_today_ongrid_input": APP_DEVICE_STAT_ONGRID_INPUT,
    "device_today_ongrid_output": APP_DEVICE_STAT_ONGRID_OUTPUT,
    "device_today_ongrid_to_battery": APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    "device_today_pv_to_battery": APP_DEVICE_STAT_PV_TO_BATTERY,
    "device_today_battery_to_ongrid": APP_DEVICE_STAT_BATTERY_TO_GRID,
    "eps_input_day_energy": APP_DEVICE_STAT_EPS_INPUT,
    "eps_output_day_energy": APP_DEVICE_STAT_EPS_OUTPUT,
    "ct_input_day_energy": FIELD_CT_TOTAL_PHASE_ENERGY,
    "ct_output_day_energy": FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
}


def _path(
    props: dict[str, Any],
    *keys: str,
) -> str | float | int | dict | list | None:
    """Walk a nested path; return None on missing intermediate keys."""
    node: object = props
    for k in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return cast("str | float | int | dict | list | None", node)


def _div(divisor: float) -> Callable[[Any], float | None]:
    """Create a transformer that divides an input value by a given divisor and rounds
    the result to 2 decimal places.

    Parameters:
        divisor (float): Value to divide the input by.

    Returns:
        Callable[[Any], float | None]: A function that accepts any value, returns the
        quotient rounded to 2 decimals when the value can be converted to float, or
        `None` when conversion fails.
    """

    def _f(value: Any) -> float | None:  # ruff:ignore[any-type]  # arbitrary payload value, coerced via float() at runtime
        try:
            return round(float(value) / divisor, 2)
        except TypeError, ValueError:
            return None

    return _f


_TOTAL_INCREASING_JITTER_BY_UNIT: Final[dict[str, float]] = {
    UnitOfEnergy.WATT_HOUR: 20.0,
    UnitOfEnergy.KILO_WATT_HOUR: 0.02,
}


def _guard_total_increasing_jitter(
    previous: Any,  # ruff:ignore[any-type]  # cached HA state can be non-numeric
    current: Any,  # ruff:ignore[any-type]  # transformed payload value
    description: SensorEntityDescription,
) -> Any:  # ruff:ignore[any-type]  # returns the original native-state type
    """Hold tiny energy-counter regressions until the source catches up."""
    if (
        description.device_class != SensorDeviceClass.ENERGY
        or description.state_class != SensorStateClass.TOTAL_INCREASING
    ):
        return current
    threshold = _TOTAL_INCREASING_JITTER_BY_UNIT.get(
        description.native_unit_of_measurement or "",
    )
    if threshold is None:
        return current
    previous_number = safe_float(previous)
    current_number = safe_float(current)
    if previous_number is None:
        return current
    if current_number is None:
        return previous
    held_regression = (
        current_number < previous_number
        and previous_number - current_number <= threshold
    )
    return previous if held_regression else current


def _signed_diff(merged_value: object, http_value: object) -> int | None:
    """Return ``merged - http`` as int when both inputs parse, else None.

    Used to surface MQTT-vs-HTTP drift in net-power sensor attributes so
    users (and the data-quality repair) can see when the two transports
    disagree on the same field.
    """
    merged_int = safe_int(merged_value)
    http_int = safe_int(http_value)
    if merged_int is None or http_int is None:
        return None
    return merged_int - http_int


def _identity[T](value: T) -> T:
    return value


def _system_meta_scalar_value(value: object) -> str | None:
    """Normalize a scalar system-metadata value for a diagnostic sensor."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    normalized = str(value).strip()
    return normalized or None


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
    value_map: dict[int, str] | None = None
    smali_field: str | None = None
    data_sources: tuple[str, ...] = DEFAULT_LIVE_SOURCES
    null_semantics: str = DEFAULT_NULL_SEMANTICS
    recorder_allowed: bool = True
    ha_derived: bool = False


# Shown for network address fields the device omits while that interface is
# down. ``eip``/``emac`` are real HomeBody fields (smali: HomeBody.eip/emac)
# that the device only sends while Ethernet is up (ethPort != 0); on a
# WLAN-only device they are simply absent. Render an explicit "not connected"
# dash instead of Unknown (owner directive 2026-07-05: "0 oder -").
_NETWORK_DISCONNECTED_PLACEHOLDER: Final = "—"


def _prop(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda props: props.get(key)


def _prop_or_disconnected(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda props: props.get(key) or _NETWORK_DISCONNECTED_PLACEHOLDER


def _no_property_value(_props: dict[str, Any]) -> None:
    """Marker getter for sensors whose value lives outside PAYLOAD_PROPERTIES."""
    return


def _payload_section_field(section: str, key: str) -> Callable[[dict[str, Any]], Any]:
    """Return a fallback getter for a top-level coordinator payload bucket."""

    def _f(payload: dict[str, Any]) -> Any:  # ruff:ignore[any-type]  # cloud payload value
        source = payload.get(section)
        if isinstance(source, dict):
            return source.get(key)
        return None

    return _f


def _payload_section_first_list_count(
    section: str,
    *keys: str,
) -> Callable[[dict[str, Any]], int | None]:
    """Return the length of the first list found in a payload bucket."""

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

    return _getter


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

    return _getter


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
    ),
    JackerySensorDescription(
        key="battery_discharge_power",
        translation_key="battery_discharge_power",
        getter=_prop(FIELD_BAT_OUT_PW),
        fallbacks=(_payload_http_prop(FIELD_BAT_OUT_PW),),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    # --- Solar / PV --------------------------------------------------------
    JackerySensorDescription(
        key="pv_power_total",
        translation_key="pv_power_total",
        getter=_prop(FIELD_PV_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="pv1_power",
        translation_key="pv1_power",
        getter=_pv_channel_power(FIELD_PV1),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="pv2_power",
        translation_key="pv2_power",
        getter=_pv_channel_power(FIELD_PV2),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="pv3_power",
        translation_key="pv3_power",
        getter=_pv_channel_power(FIELD_PV3),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="pv4_power",
        translation_key="pv4_power",
        getter=_pv_channel_power(FIELD_PV4),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    # --- Grid --------------------------------------------------------------
    # ``inGridSidePw``/``outGridSidePw`` are the only true grid
    # import/export measurement points. The on-grid family
    # (``gridIn/OutPw``, ``in/outOngridPw``) is the inverter's AC
    # input/output (house share + export) per the SystemBody identity
    # ``otherLoadPw = gridOutPw - outGridSidePw + inGridSidePw`` and is
    # exposed by the inverter_ac_* sensors below — never as an
    # import/export fallback (B5 duplicate-export bug).
    JackerySensorDescription(
        key="grid_in_power",
        translation_key="grid_in_power",
        getter=_prop(FIELD_IN_GRID_SIDE_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="grid_out_power",
        translation_key="grid_out_power",
        getter=_prop(FIELD_OUT_GRID_SIDE_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="inverter_ac_input_power",
        translation_key="inverter_ac_input_power",
        getter=jackery_inverter_ac_input_power,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="inverter_ac_output_power",
        translation_key="inverter_ac_output_power",
        getter=jackery_inverter_ac_output_power,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
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
    ),
    JackerySensorDescription(
        key="wifi_ip",
        translation_key="wifi_ip",
        getter=_prop(FIELD_WIP),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="mac_address",
        translation_key="mac_address",
        getter=_prop(FIELD_MAC),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="eth_port",
        translation_key="eth_port",
        getter=_prop(FIELD_ETH_PORT),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ability_bits",
        translation_key="ability_bits",
        getter=_prop(FIELD_ABILITY),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="max_iot_num",
        translation_key="max_iot_num",
        getter=_prop(FIELD_MAX_IOT_NUM),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="eps_switch_state",
        translation_key="eps_switch_state",
        getter=_prop(FIELD_SW_EPS_STATE),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="reboot_flag",
        translation_key="reboot_flag",
        getter=_prop(FIELD_REBOOT),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Configuration readouts ------------------------------------------
    JackerySensorDescription(
        key="soc_charge_limit",
        translation_key="soc_charge_limit",
        getter=_prop_any(FIELD_SOC_CHG_LIMIT, FIELD_SOC_CHARGE_LIMIT),
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="soc_discharge_limit",
        translation_key="soc_discharge_limit",
        getter=_prop_any(FIELD_SOC_DISCHG_LIMIT, FIELD_SOC_DISCHARGE_LIMIT),
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
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
        key="max_inverter_power",
        translation_key="max_inverter_power",
        getter=_prop(FIELD_MAX_INV_STD_PW),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="max_grid_standard_power",
        translation_key="max_grid_standard_power",
        getter=_prop(FIELD_MAX_GRID_STD_PW),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ethernet_ip",
        translation_key="ethernet_ip",
        getter=_prop_or_disconnected(FIELD_EIP),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ethernet_port",
        translation_key="ethernet_port",
        getter=_prop(FIELD_ETH_PORT),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ethernet_mac",
        translation_key="ethernet_mac",
        getter=_prop_or_disconnected(FIELD_EMAC),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="battery_count",
        translation_key="battery_count",
        getter=_prop(FIELD_BAT_NUM),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="battery_state",
        translation_key="battery_state",
        getter=_prop(FIELD_BAT_STATE),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="auto_standby",
        translation_key="auto_standby",
        getter=_prop(FIELD_IS_AUTO_STANDBY),
        entity_category=EntityCategory.DIAGNOSTIC,
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
    ),
    JackerySensorDescription(
        key="ongrid_state",
        translation_key="ongrid_state",
        getter=_prop_any(FIELD_ONGRID_STAT, FIELD_ON_GRID_STAT),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ct_state",
        translation_key="ct_state",
        getter=_prop_any(FIELD_CT_STAT, FIELD_CT_STATE),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="grid_state",
        translation_key="grid_state",
        getter=_prop_any(FIELD_GRID_STATE, FIELD_GRID_STATE_ALT, FIELD_GRID_STAT),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="work_mode",
        translation_key="work_mode",
        getter=_prop(FIELD_WORK_MODEL),
        entity_category=EntityCategory.DIAGNOSTIC,
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
        # ``defaultPw`` is a SystemBody field other models report; the
        # SolarVault 3 Pro Max never sends it, so keep the entity
        # default-disabled instead of permanently "unknown".
        entity_category=EntityCategory.DIAGNOSTIC,
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
        state_class=SensorStateClass.MEASUREMENT,
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
    # Removed duplicate grid-side entities; grid_in_power and grid_out_power
    # already cover both app key families via their fallback getters.
    JackerySensorDescription(
        key="follow_meter_state",
        translation_key="follow_meter_state",
        getter=_prop(FIELD_IS_FOLLOW_METER_PW),
        transform=safe_int,
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
    ),
    JackerySensorDescription(
        key="temp_unit",
        translation_key="temp_unit",
        getter=_prop(FIELD_TEMP_UNIT),
        transform=_temp_unit_label,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="storm_warning_enabled",
        translation_key="storm_warning_enabled",
        getter=_prop(FIELD_WPS),
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
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


def _period_start(
    reset_period: StatResetPeriod, timezone: tzinfo | None = None
) -> datetime:
    """Return the timezone-aware start datetime for the current statistic period.

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
    now = dt_util.now(timezone)
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
class JackerySmartPlugSensorDescription(SensorEntityDescription):
    """Sensor description for one entry from ``smart_plugs``.

    Smart-plug payloads come from ``UploadSubDeviceGroupProperty`` (cmd=110,
    actionId=3032) with the ``plugs`` array. Per-plug fields documented in
    PROTOCOL.md §2 "Smart-Plug-/Socket-Appmodell".
    """

    field: str
    transform: Callable[[Any], Any] = _identity
    reset_period: StatResetPeriod | None = None


@dataclass(frozen=True, kw_only=True)
class JackeryMeterHeadSensorDescription(SensorEntityDescription):
    """Sensor description for one entry from ``meter_heads``.

    Meter-head payloads come from ``UploadSubDeviceGroupProperty`` (cmd=110,
    actionId=3033) with the ``collectors`` array. Optional energy fields are
    read-only panel totals from ``/v1/device/stat/meter``.
    """

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
    fallback_fields: tuple[str, ...] = ()
    transform: Callable[[Any], Any] = _identity


@dataclass(frozen=True, kw_only=True)
class JackerySavingsDetailSensorDescription(SensorEntityDescription):
    """Sensor description for calculated savings detail values."""

    path: tuple[str, ...]
    transform: Callable[[Any], Any] = safe_float


@dataclass(frozen=True, kw_only=True)
class JackeryBreakerSensorDescription(SensorEntityDescription):
    """Sensor description for one entry from circuit-breaker payloads.

    Breaker payloads come from MQTT ``QueryCircuitProperty`` responses; each
    description maps a payload field to a per-circuit sensor.
    """

    field: str
    transform: Callable[[Any], Any] = _identity


@dataclass(frozen=True, kw_only=True)
class JackerySubdeviceAlarmSensorDescription(SensorEntityDescription):
    """Sensor description for one entry from subdevice alarm/event payloads."""

    field: str
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


def _day_section_prefix(section: str) -> str | None:
    """Return the prefix for a ``*_day`` app-period section."""
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
    """Return True when week/month/year charts prove a day sensor is supported."""
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


def _sensor_description_has_value(
    payload: dict[str, Any],
    description: JackerySensorDescription,
) -> bool:
    """Return True when a property sensor can produce a value from payload."""
    source_payload = dict(payload)
    source_payload[PAYLOAD_PROPERTIES] = payload_properties_for_sources(
        payload,
        description.data_sources,
    )
    props = source_payload.get(PAYLOAD_PROPERTIES) or {}
    raw = description.getter(props)
    if raw is None:
        for fallback in description.fallbacks:
            raw = fallback(source_payload)
            if raw is not None:
                break
    if raw is None:
        return False
    value = description.transform(raw)
    return value is not None and not (isinstance(value, str) and not value.strip())


def _smart_meter_description_value(
    ct: dict[str, Any],
    description: JackerySmartMeterSensorDescription,
) -> Any:  # ruff:ignore[any-type]  # HA sensor values may be numeric or textual
    """Return one calculable Smart-Meter value from the current CT payload."""
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
    return round(value, 2) if isinstance(value, float) else value


def _smart_meter_description_has_value(
    payload: dict[str, Any],
    description: JackerySmartMeterSensorDescription,
) -> bool:
    """Return True when the current CT payload supports this description."""
    ct = payload.get(PAYLOAD_CT_METER)
    return (
        isinstance(ct, dict)
        and _smart_meter_description_value(ct, description) is not None
    )


def _request_date(
    source: dict[str, Any],
    primary_key: str,
    alternate_key: str,
) -> date | None:
    """Parse one ISO date from a payload request metadata block."""
    request = source.get(APP_REQUEST_META)
    if not isinstance(request, dict):
        return None
    raw = request.get(primary_key) or request.get(alternate_key)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _chart_value_for_day(
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    today: date,
) -> float | None:
    """Return today's value from a week/month/year app chart payload."""
    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if unit and unit != APP_UNIT_KWH:
        return None
    begin = _request_date(source, APP_REQUEST_BEGIN_DATE, APP_REQUEST_BEGIN_DATE_ALT)
    if begin is None:
        return None
    end = _request_date(source, APP_REQUEST_END_DATE, APP_REQUEST_END_DATE_ALT)
    if today < begin or (end is not None and today > end):
        return None
    values = effective_trend_series_values(source, section, stat_key)
    if not isinstance(values, list):
        return None
    index = (today - begin).days
    if index < 0 or index >= len(values):
        return None
    return safe_float(values[index])


def _stat_description_has_value(  # ruff:ignore[too-many-return-statements]  # flat has-value guard chain over stat variants; clearest as-is
    payload: dict[str, Any],
    description: JackeryStatSensorDescription,
) -> bool:
    """Return True when a stat entity has a usable app value now."""
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
        return False
    reset_period = _period_from_stat_description(description)
    if _trend_series_key(description.section, description.stat_key) is not None:
        if trend_series_has_value(source, description.section, description.stat_key):
            return True
        for section, stat_key in description.fallback_sources:
            fallback_source = payload.get(section)
            if isinstance(fallback_source, dict) and trend_series_has_value(
                fallback_source, section, stat_key
            ):
                return True
        return bool(
            _day_period_sibling_has_value(
                payload,
                description.section,
                description.stat_key,
                reset_period=reset_period,
            )
        )
    if source.get(description.stat_key) is not None:
        return True
    for section, stat_key in description.fallback_sources:
        fallback_source = payload.get(section)
        if (
            isinstance(fallback_source, dict)
            and fallback_source.get(stat_key) is not None
        ):
            return True
        if _day_period_sibling_has_value(
            payload,
            section,
            stat_key,
            reset_period=reset_period,
        ):
            return True
    return bool(
        _day_period_sibling_has_value(
            payload,
            description.section,
            description.stat_key,
            reset_period=reset_period,
        )
    )


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
    # revenue / "App-Gesamtersparnis" from /v1/device/stat/systemStatistic).
    # state_class=TOTAL_INCREASING per the CHANGELOG "Three-part fix" /
    # Midnight race condition decision: this is a lifetime cumulative
    # counter the cloud reports as monotonically growing. TOTAL_INCREASING
    # lets the Recorder detect cloud-side resets and ignore them instead of
    # misreading the brief post-midnight transient as a real loss (which is
    # exactly what state_class=TOTAL did). No device_class=MONETARY is set
    # here, so the HA "MONETARY only allows TOTAL or None" validation does
    # not apply to this entity; only the currency unit is published.
    JackeryStatSensorDescription(
        key="total_revenue",
        translation_key="total_revenue",
        stat_key=APP_STAT_TOTAL_REVENUE,
        transform=safe_float,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=CURRENCY_EURO,
    ),
    # Source: /v1/device/stat/systemStatistic field APP_STAT_TOTAL_CARBON
    JackeryStatSensorDescription(
        key="total_carbon_saved",
        translation_key="total_carbon_saved",
        stat_key=APP_STAT_TOTAL_CARBON,
        transform=safe_float,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
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
    ),
    # --- PROTOCOL.md §2.2: /v1/device/stat/pv period PV revenue --------
    # Source: /v1/device/stat/sys/pv (dateType=week|month|year), field
    # APP_STAT_TOTAL_SOLAR_REVENUE (PvStatApi$Bean.totalSolarRevenue). These
    # surface the same period total the cloud reports for the energy charts,
    # but valued in the device's own currency (PvStatApi$Bean.currency, read
    # at runtime by JackeryStatSensor.native_unit_of_measurement — see there).
    #
    # state_class=TOTAL (NOT TOTAL_INCREASING): like the period-energy
    # sensors these are per-period totals (week = Mon-Sun, month/year =
    # calendar) that reset at the app boundary, not a lifetime cumulative
    # counter. device_class=MONETARY only permits TOTAL or None, so TOTAL is
    # the correct, HA-valid pairing here (CLAUDE.md "Period sensors are not
    # cumulative" gotcha). reset_period drives last_reset just like energy.
    # native_unit_of_measurement here is the EUR fallback; the live unit is
    # the per-device currency symbol resolved in the property override.
    JackeryStatSensorDescription(
        key="pv_revenue_day",
        translation_key="pv_revenue_day",
        stat_key=APP_STAT_TOTAL_SOLAR_REVENUE,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=CURRENCY_EURO,
    ),
    JackeryStatSensorDescription(
        key="pv_revenue_week",
        translation_key="pv_revenue_week",
        stat_key=APP_STAT_TOTAL_SOLAR_REVENUE,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=CURRENCY_EURO,
    ),
    JackeryStatSensorDescription(
        key="pv_revenue_month",
        translation_key="pv_revenue_month",
        stat_key=APP_STAT_TOTAL_SOLAR_REVENUE,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=CURRENCY_EURO,
    ),
    JackeryStatSensorDescription(
        key="pv_revenue_year",
        translation_key="pv_revenue_year",
        stat_key=APP_STAT_TOTAL_SOLAR_REVENUE,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=CURRENCY_EURO,
    ),
    # --- PROTOCOL.md §2: /v1/device/stat/pv per-channel totals -----
    # Source: /v1/device/stat/sys/pv (dateType=day) field APP_STAT_PV1_ENERGY
    JackeryStatSensorDescription(
        key="device_pv1_day_energy",
        translation_key="device_pv1_day_energy",
        stat_key=APP_STAT_PV1_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
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
    ),
    # Source: /v1/device/stat/sys/pv (dateType=day) field APP_STAT_PV2_ENERGY
    JackeryStatSensorDescription(
        key="device_pv2_day_energy",
        translation_key="device_pv2_day_energy",
        stat_key=APP_STAT_PV2_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
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
    ),
    # Source: /v1/device/stat/sys/pv (dateType=day) field APP_STAT_PV3_ENERGY
    JackeryStatSensorDescription(
        key="device_pv3_day_energy",
        translation_key="device_pv3_day_energy",
        stat_key=APP_STAT_PV3_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
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
    ),
    # Source: /v1/device/stat/sys/pv (dateType=day) field APP_STAT_PV4_ENERGY
    JackeryStatSensorDescription(
        key="device_pv4_day_energy",
        translation_key="device_pv4_day_energy",
        stat_key=APP_STAT_PV4_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
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
    ),
    # Historical system_pv_*/system_home_*/system_battery_* duplicates are not
    # exposed; the app-backed per-device and home period sensors below provide
    # the canonical Home Assistant entities.
    # Source: /v1/device/stat/onGrid (dateType=day) field APP_STAT_TOTAL_HOME_ENERGY
    JackeryStatSensorDescription(
        key="home_day_energy",
        translation_key="home_day_energy",
        stat_key=APP_STAT_TOTAL_HOME_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
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
    ),
    # --- PROTOCOL.md §2: /v1/device/stat/onGrid --------------------
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
    ),
    # Source: /v1/device/stat/sys/home (dateType=year) field APP_STAT_TOTAL_OUT_GRID_ENERGY
    # APP_STAT_TOTAL_OUT_GRID_ENERGY
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
    ),
    # ------------------------------------------------------------------
    # CT / Smart-Meter period totals (CtStatApi$Bean).
    # The endpoint is accessory-scoped (`devType=3` Smart Meter / Shelly Pro
    # 3EM), so the coordinator resolves the CT accessory id before polling.
    # ------------------------------------------------------------------
    JackeryStatSensorDescription(
        key="ct_input_day_energy",
        translation_key="ct_input_day_energy",
        stat_key=APP_STAT_TOTAL_CT_INPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="ct_input_week_energy",
        translation_key="ct_input_week_energy",
        stat_key=APP_STAT_TOTAL_CT_INPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="ct_input_month_energy",
        translation_key="ct_input_month_energy",
        stat_key=APP_STAT_TOTAL_CT_INPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="ct_input_year_energy",
        translation_key="ct_input_year_energy",
        stat_key=APP_STAT_TOTAL_CT_INPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="ct_output_day_energy",
        translation_key="ct_output_day_energy",
        stat_key=APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="ct_output_week_energy",
        translation_key="ct_output_week_energy",
        stat_key=APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="ct_output_month_energy",
        translation_key="ct_output_month_energy",
        stat_key=APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="ct_output_year_energy",
        translation_key="ct_output_year_energy",
        stat_key=APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
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
    ),
    # ------------------------------------------------------------------
    # EPS / off-grid period totals (EpsStatApi$Bean).
    # Polled by the coordinator under APP_SECTION_EPS_STAT for each dateType.
    #  The fields stay ``unknown`` on hardware that never
    # operates off-grid: that is correct HA behaviour. This installer's
    # SolarVault may not exercise EPS often, but the contract is in the
    # Smali docs (jackery_smali_home_assistant_report.html "Statistik-
    # Endpunkte") and we must mirror it so users with EPS-active setups
    # do not have to file feature requests later.
    # ------------------------------------------------------------------
    JackeryStatSensorDescription(
        key="eps_input_day_energy",
        translation_key="eps_input_day_energy",
        stat_key=APP_STAT_TOTAL_IN_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="eps_input_week_energy",
        translation_key="eps_input_week_energy",
        stat_key=APP_STAT_TOTAL_IN_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="eps_input_month_energy",
        translation_key="eps_input_month_energy",
        stat_key=APP_STAT_TOTAL_IN_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="eps_input_year_energy",
        translation_key="eps_input_year_energy",
        stat_key=APP_STAT_TOTAL_IN_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="eps_output_day_energy",
        translation_key="eps_output_day_energy",
        stat_key=APP_STAT_TOTAL_OUT_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="eps_output_week_energy",
        translation_key="eps_output_week_energy",
        stat_key=APP_STAT_TOTAL_OUT_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_WEEK}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_WEEK,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="eps_output_month_energy",
        translation_key="eps_output_month_energy",
        stat_key=APP_STAT_TOTAL_OUT_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_MONTH}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_MONTH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="eps_output_year_energy",
        translation_key="eps_output_year_energy",
        stat_key=APP_STAT_TOTAL_OUT_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_YEAR}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # ------------------------------------------------------------------
    # Today KPIs (PROTOCOL.md §2.4 + TodayEnergyApi$Bean). Flat bean
    # under coordinator.data[<dev>][APP_SECTION_TODAY_ENERGY]:
    # ``de`` feed-in, ``dg`` grid import, ``dh`` home load, ``ds``
    # battery energy — all kWh doubles. Polled per #14.
    # ------------------------------------------------------------------
    JackeryStatSensorDescription(
        key="today_feed_in_energy",
        translation_key="today_feed_in_energy",
        stat_key=APP_STAT_TODAY_FEED_IN_ENERGY,
        section=APP_SECTION_TODAY_ENERGY,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="today_grid_import_energy",
        translation_key="today_grid_import_energy",
        stat_key=APP_STAT_TODAY_GRID_IMPORT_ENERGY,
        section=APP_SECTION_TODAY_ENERGY,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="today_home_load_energy",
        translation_key="today_home_load_energy",
        stat_key=APP_STAT_TODAY_HOME_LOAD_ENERGY,
        section=APP_SECTION_TODAY_ENERGY,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="today_battery_energy",
        translation_key="today_battery_energy",
        stat_key=APP_STAT_TODAY_BATTERY_ENERGY,
        section=APP_SECTION_TODAY_ENERGY,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
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
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=f"{CURRENCY_EURO}/kWh",
    ),
    # --- PROTOCOL.md §2: dated day-period totals --------------------
    # ``deviceStatistic`` *Egy fields are monotone lifetime counters in Wh,
    # not daily kWh totals. Never use them directly here. When a dated day
    # endpoint is empty, JackeryStatSensor falls back to a validated current
    # week/month bucket and then to the persisted local lifetime-counter delta.
    # Source: /v1/device/stat/pv dateType=day field APP_STAT_TOTAL_SOLAR_ENERGY
    JackeryStatSensorDescription(
        key="device_today_pv_energy",
        translation_key="device_today_pv_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: /v1/device/stat/battery dateType=day field APP_STAT_TOTAL_CHARGE
    JackeryStatSensorDescription(
        key="device_today_battery_charge",
        translation_key="device_today_battery_charge",
        stat_key=APP_STAT_TOTAL_CHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: /v1/device/stat/battery dateType=day field APP_STAT_TOTAL_DISCHARGE.
    JackeryStatSensorDescription(
        key="device_today_battery_discharge",
        translation_key="device_today_battery_discharge",
        stat_key=APP_STAT_TOTAL_DISCHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: /v1/device/stat/onGrid dateType=day field APP_STAT_TOTAL_IN_GRID_ENERGY
    JackeryStatSensorDescription(
        key="device_today_ongrid_input",
        translation_key="device_today_ongrid_input",
        stat_key=APP_STAT_TOTAL_IN_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: /v1/device/stat/onGrid dateType=day field APP_STAT_TOTAL_OUT_GRID_ENERGY
    JackeryStatSensorDescription(
        key="device_today_ongrid_output",
        translation_key="device_today_ongrid_output",
        stat_key=APP_STAT_TOTAL_OUT_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: persisted daily delta of the deviceStatistic lifetime Wh counter.
    JackeryStatSensorDescription(
        key="device_today_ongrid_to_battery",
        translation_key="device_today_ongrid_to_battery",
        stat_key=APP_DEVICE_STAT_ONGRID_TO_BATTERY,
        section=PAYLOAD_LOCAL_DAILY_ENERGY,
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: persisted daily delta of the deviceStatistic lifetime Wh counter.
    JackeryStatSensorDescription(
        key="device_today_pv_to_battery",
        translation_key="device_today_pv_to_battery",
        stat_key=APP_DEVICE_STAT_PV_TO_BATTERY,
        section=PAYLOAD_LOCAL_DAILY_ENERGY,
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Source: persisted daily delta of the deviceStatistic lifetime Wh counter.
    JackeryStatSensorDescription(
        key="device_today_battery_to_ongrid",
        translation_key="device_today_battery_to_ongrid",
        stat_key=APP_DEVICE_STAT_BATTERY_TO_GRID,
        section=PAYLOAD_LOCAL_DAILY_ENERGY,
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # --- ATS / Symmetry Statistics ---
    JackeryStatSensorDescription(
        key="symmetry_total_positive",
        translation_key="symmetry_total_positive",
        stat_key=FIELD_TOTAL_P,
        section=f"{APP_SECTION_SYMMETRY_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="symmetry_total_negative",
        translation_key="symmetry_total_negative",
        stat_key=FIELD_TOTAL_N,
        section=f"{APP_SECTION_SYMMETRY_STAT}_{DATE_TYPE_DAY}",
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
)


# ---------------------------------------------------------------------------
# Smart Mode / AI Schedule / TOU Plan sensors
# ---------------------------------------------------------------------------

SMART_MODE_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        key="smart_mode_active",
        translation_key="smart_mode_active",
        getter=_no_property_value,
        transform=safe_int,
        fallbacks=(_payload_section_field(PAYLOAD_SMART_MODE, "isActive"),),
        value_map={0: "inactive", 1: "active"},
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="smart_mode_time_difference",
        translation_key="smart_mode_time_difference",
        getter=_no_property_value,
        fallbacks=(_payload_section_field(PAYLOAD_SMART_MODE, "timeDifference"),),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

SMART_SCHEDULE_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        key="smart_schedule_points",
        translation_key="smart_schedule_points",
        getter=_no_property_value,
        fallbacks=(
            _payload_section_first_list_count(
                PAYLOAD_SMART_SCHEDULE,
                "xList",
                "priceList",
                "pvPowerList",
                "homeList",
            ),
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="smart_schedule_profit",
        translation_key="smart_schedule_profit",
        getter=_no_property_value,
        transform=safe_float,
        fallbacks=(_payload_section_field(PAYLOAD_SMART_SCHEDULE, "profit"),),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="smart_schedule_days",
        translation_key="smart_schedule_days",
        getter=_no_property_value,
        transform=safe_int,
        fallbacks=(_payload_section_field(PAYLOAD_SMART_SCHEDULE, "days"),),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="smart_schedule_currency",
        translation_key="smart_schedule_currency",
        getter=_no_property_value,
        fallbacks=(_payload_section_field(PAYLOAD_SMART_SCHEDULE, "currency"),),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

DYNAMIC_PRICE_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        key="dynamic_price_today_low",
        translation_key="dynamic_price_today_low",
        getter=_no_property_value,
        transform=safe_float,
        fallbacks=(_payload_section_field(PAYLOAD_DYNAMIC_PRICE, FIELD_TODAY_LOW),),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="dynamic_price_today_high",
        translation_key="dynamic_price_today_high",
        getter=_no_property_value,
        transform=safe_float,
        fallbacks=(_payload_section_field(PAYLOAD_DYNAMIC_PRICE, FIELD_TODAY_HIGH),),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="dynamic_price_nextday_low",
        translation_key="dynamic_price_nextday_low",
        getter=_no_property_value,
        transform=safe_float,
        fallbacks=(_payload_section_field(PAYLOAD_DYNAMIC_PRICE, FIELD_NEXTDAY_LOW),),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="dynamic_price_nextday_high",
        translation_key="dynamic_price_nextday_high",
        getter=_no_property_value,
        transform=safe_float,
        fallbacks=(_payload_section_field(PAYLOAD_DYNAMIC_PRICE, FIELD_NEXTDAY_HIGH),),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="dynamic_price_provider",
        translation_key="dynamic_price_provider",
        getter=_no_property_value,
        fallbacks=(
            _payload_section_field(PAYLOAD_DYNAMIC_PRICE, FIELD_PRICE_COMPANY_NAME),
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="dynamic_price_contract_auth",
        translation_key="dynamic_price_contract_auth",
        getter=_no_property_value,
        transform=safe_bool,
        fallbacks=(
            _payload_section_field(PAYLOAD_DYNAMIC_PRICE, FIELD_IS_CONTRACT_AUTH),
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

TOU_PLAN_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        key="tou_plan_tasks",
        translation_key="tou_plan_tasks",
        getter=_no_property_value,
        fallbacks=(_payload_section_first_list_count(PAYLOAD_TOU_SCHEDULE, "tasks"),),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


PORTABLE_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    # --- AC Input ---
    JackerySensorDescription(
        key="ac_input_current",
        translation_key="ac_input_current",
        getter=_prop(FIELD_IAC),
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_input_power",
        translation_key="ac_input_power",
        getter=_prop(FIELD_IACPW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="charging_input_power",
        translation_key="charging_input_power",
        getter=_prop(FIELD_CIP),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    # --- AC Output ---
    JackerySensorDescription(
        key="ac_output_voltage",
        translation_key="ac_output_voltage",
        getter=_prop(FIELD_ACOV),
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_output_frequency",
        translation_key="ac_output_frequency",
        getter=_prop(FIELD_ACOHZ),
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_output_apparent_power",
        translation_key="ac_output_apparent_power",
        getter=_prop(FIELD_ACPS),
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_output_power",
        translation_key="ac_output_power",
        getter=_prop_any(FIELD_OACPW, FIELD_OAC),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="ac_output_power_2",
        translation_key="ac_output_power_2",
        getter=_prop(FIELD_OAC2),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_output_current",
        translation_key="ac_output_current",
        getter=_prop(FIELD_OACT),
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_output_mode",
        translation_key="ac_output_mode",
        getter=_prop(FIELD_ACMODE),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- DC Output ---
    JackerySensorDescription(
        key="dc_output_power",
        translation_key="dc_output_power",
        getter=_prop(FIELD_ODC_PORT),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="dc_output_current",
        translation_key="dc_output_current",
        getter=_prop(FIELD_ODCC),
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="dc_output_voltage",
        translation_key="dc_output_voltage",
        getter=_prop(FIELD_ODCU),
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- USB Ports ---
    JackerySensorDescription(
        key="usb_a1_power",
        translation_key="usb_a1_power",
        getter=_prop(FIELD_USBA1),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="usb_a2_power",
        translation_key="usb_a2_power",
        getter=_prop(FIELD_USBA2),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="usb_c1_power",
        translation_key="usb_c1_power",
        getter=_prop(FIELD_USBC1),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="usb_c2_power",
        translation_key="usb_c2_power",
        getter=_prop(FIELD_USBC2),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="usb_a3_power",
        translation_key="usb_a3_power",
        getter=_prop(FIELD_USBA3),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="usb_c3_power",
        translation_key="usb_c3_power",
        getter=_prop(FIELD_USBC3),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- AC Line Currents/Powers ---
    JackerySensorDescription(
        key="ac_line1_current",
        translation_key="ac_line1_current",
        getter=_prop(FIELD_OACL1),
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_line1_power",
        translation_key="ac_line1_power",
        getter=_prop(FIELD_OACL1_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_line2_current",
        translation_key="ac_line2_current",
        getter=_prop(FIELD_OACL2),
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_line2_power",
        translation_key="ac_line2_power",
        getter=_prop(FIELD_OACL2_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_output_current_1",
        translation_key="ac_output_current_1",
        getter=_prop(FIELD_OACT1),
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_output_current_2",
        translation_key="ac_output_current_2",
        getter=_prop(FIELD_OACT2),
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Charge Status ---
    JackerySensorDescription(
        key="charge_input_power_portable",
        translation_key="charge_input_power_portable",
        getter=_prop(FIELD_CIP),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="charge_status",
        translation_key="charge_status",
        getter=_prop(FIELD_CS),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="charge_status_code",
        translation_key="charge_status_code",
        getter=_prop(FIELD_CSC),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="charge_status_limit",
        translation_key="charge_status_limit",
        getter=_prop(FIELD_CSL),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="charge_status_type",
        translation_key="charge_status_type",
        getter=_prop(FIELD_CST),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Power / Config ---
    JackerySensorDescription(
        key="power_count",
        translation_key="power_count",
        getter=_prop(FIELD_PC),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="power_mode_portable",
        translation_key="power_mode_portable",
        getter=_prop(FIELD_PM),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="power_mode_battery",
        translation_key="power_mode_battery",
        getter=_prop(FIELD_PMB),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="dhg_recall",
        translation_key="dhg_recall",
        getter=_prop(FIELD_DHG_RECALL),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Temperatures ---
    JackerySensorDescription(
        key="input_temperature",
        translation_key="input_temperature",
        getter=_prop(FIELD_IT),
        transform=_div(10),
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    JackerySensorDescription(
        key="output_temperature",
        translation_key="output_temperature",
        getter=_prop(FIELD_OT),
        transform=_div(10),
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    JackerySensorDescription(
        key="battery_temperature",
        translation_key="battery_temperature",
        getter=_prop(FIELD_BT),
        transform=_div(10),
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    # --- Power / Status ---
    JackerySensorDescription(
        key="input_power_portable",
        translation_key="input_power_portable",
        getter=_prop(FIELD_IP),
        fallbacks=(_payload_section_field(PAYLOAD_PROPERTIES, FIELD_IACPW),),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="output_power_portable",
        translation_key="output_power_portable",
        getter=_prop(FIELD_OP),
        fallbacks=(_payload_section_field(PAYLOAD_PROPERTIES, FIELD_OACPW),),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="error_code",
        translation_key="error_code",
        getter=_prop(FIELD_EC),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="remaining_runtime",
        translation_key="remaining_runtime",
        getter=_prop(FIELD_RB),
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="battery_count",
        translation_key="battery_count",
        getter=_prop(FIELD_BC),
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="battery_low_state",
        translation_key="battery_low_state",
        getter=_prop(FIELD_BLS),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="charge_limit",
        translation_key="charge_limit",
        getter=_prop(FIELD_CL),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="discharge_limit",
        translation_key="discharge_limit",
        getter=_prop(FIELD_DL),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="power_mode",
        translation_key="power_mode",
        getter=_prop(FIELD_PM),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="power_source_selector",
        translation_key="power_source_selector",
        getter=_prop(FIELD_PSS),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ups_mode",
        translation_key="ups_mode",
        getter=_prop(FIELD_UPS),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="wifi_switch_status",
        translation_key="wifi_switch_status",
        getter=_prop(FIELD_WSS),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="auto_standby_timer",
        translation_key="auto_standby_timer",
        getter=_prop(FIELD_AST),
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="external_pack_connected",
        translation_key="external_pack_connected",
        getter=_prop(FIELD_IS_PACK_CONNECT),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Network (Diagnostic) ---
    JackerySensorDescription(
        key="wifi_signal_portable",
        translation_key="wifi_signal",
        getter=_prop(FIELD_WSIG),
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="wifi_ssid",
        translation_key="wifi_ssid",
        getter=_prop(FIELD_WNAME),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="wifi_ip",
        translation_key="wifi_ip",
        getter=_prop(FIELD_WIP),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="mac_address",
        translation_key="mac_address",
        getter=_prop(FIELD_MAC),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Portable AC Output Config ---
    JackerySensorDescription(
        key="ac1_name",
        translation_key="ac1_name",
        getter=_prop(FIELD_OAC1_NAME),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac2_name",
        translation_key="ac2_name",
        getter=_prop(FIELD_OAC2_NAME),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Portable DC Output Config ---
    JackerySensorDescription(
        key="dc_output_config",
        translation_key="dc_output_config",
        getter=_prop(FIELD_ODCC),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="dc_type",
        translation_key="dc_type",
        getter=_prop(FIELD_ODCCT),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="dc_output_type",
        translation_key="dc_output_type",
        getter=_prop(FIELD_ODCT),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="dc_usb_connected",
        translation_key="dc_usb_connected",
        getter=_prop(FIELD_ODCU),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="dc_usb_type",
        translation_key="dc_usb_type",
        getter=_prop(FIELD_ODCUT),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # --- Portable Status/Misc ---
    JackerySensorDescription(
        key="battery_pack_count",
        translation_key="battery_pack_count",
        getter=_prop(FIELD_BPC),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="box_mode",
        translation_key="box_mode",
        getter=_prop(FIELD_BOX),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="light_sensor",
        translation_key="light_sensor",
        getter=_prop(FIELD_PAL),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="sleep_mode_flag",
        translation_key="sleep_mode_flag",
        getter=_prop(FIELD_SFC),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="sltb_value",
        translation_key="sltb_value",
        getter=_prop(FIELD_SLTB),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ambient_temperature",
        translation_key="ambient_temperature",
        getter=_prop(FIELD_TA),
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="panel_temperature",
        translation_key="panel_temperature",
        getter=_prop(FIELD_TP),
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_discharge_current",
        translation_key="ac_discharge_current",
        getter=_prop(FIELD_ACCD),
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_discharge_time",
        translation_key="ac_discharge_time",
        getter=_prop(FIELD_ACDT),
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_input_power_alt",
        translation_key="ac_input_power_alt",
        getter=_prop(FIELD_ACIP),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_output_apparent_power_parallel",
        translation_key="ac_output_apparent_power_parallel",
        getter=_prop(FIELD_ACPSP),
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_output_apparent_power_sum",
        translation_key="ac_output_apparent_power_sum",
        getter=_prop(FIELD_ACPSS),
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="charge_output_power",
        translation_key="charge_output_power",
        getter=_prop(FIELD_COP),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="discharge_time",
        translation_key="discharge_time",
        getter=_prop(FIELD_DT),
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="load_mode",
        translation_key="load_mode",
        getter=_prop(FIELD_LM),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="input_power_allocation",
        translation_key="input_power_allocation",
        getter=_prop(FIELD_IPAL_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="output_power_allocation",
        translation_key="output_power_allocation",
        getter=_prop(FIELD_OPAL_PW),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="system_status_switch",
        translation_key="system_status_switch",
        getter=_prop(FIELD_SS),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="ac_output_voltage_2",
        translation_key="ac_output_voltage_2",
        getter=_prop(FIELD_ACOV1),
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="total_time",
        translation_key="total_time",
        getter=_prop(FIELD_TT),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


SAVINGS_DETAIL_SENSOR_DESCRIPTIONS: tuple[
    JackerySavingsDetailSensorDescription, ...
] = (
    JackerySavingsDetailSensorDescription(
        key="savings_calculated_total",
        translation_key="savings_calculated_total",
        path=("calculated_total",),
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
    ),
    JackerySavingsDetailSensorDescription(
        key="savings_energy",
        translation_key="savings_energy",
        path=("energy_kwh",),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackerySavingsDetailSensorDescription(
        key="savings_price",
        translation_key="savings_price",
        path=("price",),
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=f"{CURRENCY_EURO}/kWh",
    ),
    JackerySavingsDetailSensorDescription(
        key="savings_battery_loss_year_energy",
        translation_key="savings_battery_loss_year_energy",
        path=("source_energy", "battery_charge_discharge_gap_kwh"),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackerySavingsDetailSensorDescription(
        key="savings_conversion_loss_year_energy",
        translation_key="savings_conversion_loss_year_energy",
        path=("source_energy", "conversion_loss_year_kwh"),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackerySavingsDetailSensorDescription(
        key="savings_pv_residual_year_energy",
        translation_key="savings_pv_residual_year_energy",
        path=("source_energy", "pv_residual_after_self_consumption_year_kwh"),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
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
    ),
    JackeryBatteryPackSensorDescription(
        key="cell_temperature",
        translation_key="battery_pack_cell_temperature",
        field=FIELD_CELL_TEMP,
        transform=_div(10),
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    JackeryBatteryPackSensorDescription(
        key="charge_power",
        translation_key="battery_pack_charge_power",
        field=FIELD_IN_PW,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackeryBatteryPackSensorDescription(
        key="discharge_power",
        translation_key="battery_pack_discharge_power",
        field=FIELD_OUT_PW,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackeryBatteryPackSensorDescription(
        key="firmware_version",
        translation_key="battery_pack_firmware_version",
        field=FIELD_VERSION,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryBatteryPackSensorDescription(
        key="serial_number",
        translation_key="battery_pack_serial_number",
        field=FIELD_DEVICE_SN,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryBatteryPackSensorDescription(
        key="communication_state",
        translation_key="battery_pack_communication_state",
        field=FIELD_COMM_STATE,
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryBatteryPackSensorDescription(
        key="update_status",
        translation_key="battery_pack_update_status",
        field=FIELD_UPDATE_STATUS,
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Pack-level lifetime energy counters. Populated exclusively by the
    # BLE-sink cmd=120 path (HTTP /v1/device/battery/pack/list returns
    # data:null for SolarVault). Values arrive in Wh-int on the wire;
    # ``_div(1000)`` converts to kWh so HA Energy Dashboard can
    # consume them as TOTAL_INCREASING counters. Disabled by default
    # because they depend on the optional BLE transport.
    JackeryBatteryPackSensorDescription(
        key="lifetime_charge_energy",
        translation_key="battery_pack_lifetime_charge_energy",
        field=FIELD_IN_EGY,
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
    ),
    JackeryBatteryPackSensorDescription(
        key="lifetime_discharge_energy",
        translation_key="battery_pack_lifetime_discharge_energy",
        field=FIELD_OUT_EGY,
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
    ),
)


SMART_PLUG_SENSOR_DESCRIPTIONS: tuple[JackerySmartPlugSensorDescription, ...] = (
    JackerySmartPlugSensorDescription(
        key="input_power",
        translation_key="smart_plug_input_power",
        field=FIELD_IN_PW,
        transform=safe_int,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySmartPlugSensorDescription(
        key="output_power",
        translation_key="smart_plug_output_power",
        field=FIELD_OUT_PW,
        transform=safe_int,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySmartPlugSensorDescription(
        key="communication_state",
        translation_key="smart_plug_communication_state",
        field=FIELD_COMM_STATE,
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySmartPlugSensorDescription(
        key="priority",
        translation_key="smart_plug_priority",
        field=FIELD_SOCKET_PRIORITY,
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySmartPlugSensorDescription(
        key="firmware_version",
        translation_key="smart_plug_firmware_version",
        field=FIELD_VERSION,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySmartPlugSensorDescription(
        key="today_energy",
        translation_key="smart_plug_today_energy",
        field=FIELD_TODAY_ENERGY,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_DAY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackerySmartPlugSensorDescription(
        key="total_energy",
        translation_key="smart_plug_total_energy",
        field=FIELD_TOTAL_ENERGY,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    # Diagnostic identifiers. Default-disabled so they do not crowd the
    # device card; users can enable them when troubleshooting routing or
    # network reachability.
    JackerySmartPlugSensorDescription(
        key="communication_mode",
        translation_key="smart_plug_communication_mode",
        field=FIELD_COMM_MODE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartPlugSensorDescription(
        key="ip_address",
        translation_key="smart_plug_ip_address",
        field=FIELD_IP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartPlugSensorDescription(
        key="mac_address",
        translation_key="smart_plug_mac_address",
        field=FIELD_MAC,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # AccSocketBody short-keys ``sc`` / ``ts`` from the Smali doc table
    # (source-of-truth/jackery_smali_home_assistant_report.html). Not observed
    # in this installer's payload stream, but the Smali contract names
    # them so we expose them as default-disabled diagnostic sensors —
    # firmware versions that do emit them surface here without any
    # code change.
    JackerySmartPlugSensorDescription(
        key="switch_cycle",
        translation_key="smart_plug_switch_cycle",
        field=FIELD_SOCKET_SWITCH_CYCLE,
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartPlugSensorDescription(
        key="last_update_ts",
        translation_key="smart_plug_last_update_ts",
        field=FIELD_SOCKET_LAST_UPDATE_TS,
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)

BREAKER_SENSOR_DESCRIPTIONS: tuple[JackeryBreakerSensorDescription, ...] = (
    JackeryBreakerSensorDescription(
        key="pc",
        translation_key="breaker_pc",
        field=FIELD_PC,
        transform=safe_int,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackeryBreakerSensorDescription(
        key="pr",
        translation_key="breaker_pr",
        field=FIELD_PR,
        transform=safe_int,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackeryBreakerSensorDescription(
        key="sph",
        translation_key="breaker_sph",
        field=FIELD_SPH,
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryBreakerSensorDescription(
        key="sph_pc",
        translation_key="breaker_sph_pc",
        field=FIELD_SPH_PC,
        transform=safe_int,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
)


SUBDEVICE_ALARM_SENSOR_DESCRIPTIONS: tuple[
    JackerySubdeviceAlarmSensorDescription, ...
] = (
    JackerySubdeviceAlarmSensorDescription(
        key="alert_count",
        translation_key="subdevice_alert_count",
        field=FIELD_ALERT_COUNT,
        transform=safe_int,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

METER_HEAD_SENSOR_DESCRIPTIONS: tuple[JackeryMeterHeadSensorDescription, ...] = (
    JackeryMeterHeadSensorDescription(
        key="input_power",
        translation_key="meter_head_input_power",
        field=FIELD_IN_PW,
        transform=safe_int,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryMeterHeadSensorDescription(
        key="output_power",
        translation_key="meter_head_output_power",
        field=FIELD_OUT_PW,
        transform=safe_int,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryMeterHeadSensorDescription(
        key="communication_state",
        translation_key="meter_head_communication_state",
        field=FIELD_COMM_STATE,
        transform=safe_int,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryMeterHeadSensorDescription(
        key="charging_energy",
        translation_key="meter_head_charging_energy",
        field=FIELD_CHARGING_ENERGY,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryMeterHeadSensorDescription(
        key="discharging_energy",
        translation_key="meter_head_discharging_energy",
        field=FIELD_DISCHARGING_ENERGY,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Diagnostic identifiers for meter-head subdevices (default-disabled).
    JackeryMeterHeadSensorDescription(
        key="communication_mode",
        translation_key="meter_head_communication_mode",
        field=FIELD_COMM_MODE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackeryMeterHeadSensorDescription(
        key="ip_address",
        translation_key="meter_head_ip_address",
        field=FIELD_IP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackeryMeterHeadSensorDescription(
        key="mac_address",
        translation_key="meter_head_mac_address",
        field=FIELD_MAC,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
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
    ),
    JackerySmartMeterSensorDescription(
        key="net_import_power",
        translation_key="smart_meter_net_import_power",
        field=FIELD_CT_POWER,
        calculation="net_import",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySmartMeterSensorDescription(
        key="net_export_power",
        translation_key="smart_meter_net_export_power",
        field=FIELD_CT_POWER,
        calculation="net_export",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    # Cumulative grid import/export energy counters. Third-party CT meters
    # (e.g. Shelly Pro 3EM cloud2cloud) report these as the ``tPhaseEgy`` /
    # ``tnPhaseEgy`` phase-energy totals in the MQTT payload, while the Jackery
    # ``device/stat/meter`` ``chargingEnergy``/``dischargingEnergy`` panel
    # totals stay empty for non-native meters — so these are the only kWh source.
    JackerySmartMeterSensorDescription(
        key="grid_import_energy",
        translation_key="smart_meter_grid_import_energy",
        field=FIELD_CT_TOTAL_PHASE_ENERGY,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    ),
    JackerySmartMeterSensorDescription(
        key="grid_export_energy",
        translation_key="smart_meter_grid_export_energy",
        field=FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
    ),
    JackerySmartMeterSensorDescription(
        key="gross_phase_import_power",
        translation_key="smart_meter_gross_phase_import_power",
        field=FIELD_CT_POWER,
        calculation="gross_import",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySmartMeterSensorDescription(
        key="gross_phase_export_power",
        translation_key="smart_meter_gross_phase_export_power",
        field=FIELD_CT_POWER,
        calculation="gross_export",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySmartMeterSensorDescription(
        key="gross_phase_flow_power",
        translation_key="smart_meter_gross_phase_flow_power",
        field=FIELD_CT_POWER,
        calculation="gross_flow",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
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
    ),
    JackerySmartMeterSensorDescription(
        key="lifetime_import_energy",
        translation_key="smart_meter_lifetime_import_energy",
        # Prefer the meter's own reported total (``tPhaseEgy``); fall back to
        # the sum of the per-phase import energies (``aPhaseEgy`` +
        # ``bPhaseEgy`` + ``cPhaseEgy``) when a device reports only per-phase
        # counters. Both are Wh integers → kWh via ``_div(1000)``.
        field=FIELD_CT_TOTAL_PHASE_ENERGY,
        aliases=(FIELD_CT_TOTAL_PHASE_ENERGY,),
        sum_fields=(
            FIELD_CT_A_PHASE_ENERGY,
            FIELD_CT_B_PHASE_ENERGY,
            FIELD_CT_C_PHASE_ENERGY,
        ),
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackerySmartMeterSensorDescription(
        key="lifetime_export_energy",
        translation_key="smart_meter_lifetime_export_energy",
        # Prefer the reported negative total (``tnPhaseEgy``); fall back to the
        # sum of the per-phase export energies (``anPhaseEgy`` + ``bnPhaseEgy``
        # + ``cnPhaseEgy``).
        field=FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
        aliases=(FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,),
        sum_fields=(
            FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
            FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
            FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
        ),
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_lifetime_import_energy",
        translation_key="smart_meter_phase_1_lifetime_import_energy",
        field=FIELD_CT_A_PHASE_ENERGY,
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_lifetime_import_energy",
        translation_key="smart_meter_phase_2_lifetime_import_energy",
        field=FIELD_CT_B_PHASE_ENERGY,
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_lifetime_import_energy",
        translation_key="smart_meter_phase_3_lifetime_import_energy",
        field=FIELD_CT_C_PHASE_ENERGY,
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_lifetime_export_energy",
        translation_key="smart_meter_phase_1_lifetime_export_energy",
        field=FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_lifetime_export_energy",
        translation_key="smart_meter_phase_2_lifetime_export_energy",
        field=FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_lifetime_export_energy",
        translation_key="smart_meter_phase_3_lifetime_export_energy",
        field=FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
        transform=_div(1000),
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
    ),
    # ------------------------------------------------------------------
    # AccCTBody electrical measurements (PROTOCOL.md §3 +
    # source-of-truth/jackery_entity_field_candidates_v2.html). Per-phase
    # voltage / current / power-factor / apparent / reactive plus their
    # totals. Active power is already covered above.
    #
    # ALL of these are ``entity_registry_enabled_default=False``: the
    # SolarVault firmware only emits ``volt``/``curr``/``freq``/``fact``/
    # ``ap``/``rep`` when an external AccCT-class accessory is bound
    # (Shelly Pro EM-50 / 3EM / 3EM63 etc.). Installations with only the
    # built-in Jackery CT report ``aPhasePw``/``bPhasePw``/``cPhasePw``
    # (already mapped via the active-power entries above) but no
    # AccCTBody fields. Default-disabled keeps the smart-meter device
    # card free of ``unknown`` entities for the common case; users with
    # an external AccCT-class meter enable them in one click.
    # ------------------------------------------------------------------
    JackerySmartMeterSensorDescription(
        key="voltage",
        translation_key="smart_meter_voltage",
        field=FIELD_CT_VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_voltage",
        translation_key="smart_meter_phase_1_voltage",
        field=FIELD_CT_VOLT1,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_voltage",
        translation_key="smart_meter_phase_2_voltage",
        field=FIELD_CT_VOLT2,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_voltage",
        translation_key="smart_meter_phase_3_voltage",
        field=FIELD_CT_VOLT3,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_current",
        translation_key="smart_meter_phase_1_current",
        field=FIELD_CT_CURRENT1,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_current",
        translation_key="smart_meter_phase_2_current",
        field=FIELD_CT_CURRENT2,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_current",
        translation_key="smart_meter_phase_3_current",
        field=FIELD_CT_CURRENT3,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="frequency",
        translation_key="smart_meter_frequency",
        field=FIELD_CT_FREQUENCY,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="power_factor",
        translation_key="smart_meter_power_factor",
        field=FIELD_CT_POWER_FACTOR,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_power_factor",
        translation_key="smart_meter_phase_1_power_factor",
        field=FIELD_CT_POWER_FACTOR1,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_power_factor",
        translation_key="smart_meter_phase_2_power_factor",
        field=FIELD_CT_POWER_FACTOR2,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_power_factor",
        translation_key="smart_meter_phase_3_power_factor",
        field=FIELD_CT_POWER_FACTOR3,
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="apparent_power",
        translation_key="smart_meter_apparent_power",
        field=FIELD_CT_APPARENT_POWER,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_apparent_power",
        translation_key="smart_meter_phase_1_apparent_power",
        field=FIELD_CT_APPARENT_POWER1,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_apparent_power",
        translation_key="smart_meter_phase_2_apparent_power",
        field=FIELD_CT_APPARENT_POWER2,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_apparent_power",
        translation_key="smart_meter_phase_3_apparent_power",
        field=FIELD_CT_APPARENT_POWER3,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="reactive_power",
        translation_key="smart_meter_reactive_power",
        field=FIELD_CT_REACTIVE_POWER,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_reactive_power",
        translation_key="smart_meter_phase_1_reactive_power",
        field=FIELD_CT_REACTIVE_POWER1,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_reactive_power",
        translation_key="smart_meter_phase_2_reactive_power",
        field=FIELD_CT_REACTIVE_POWER2,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_reactive_power",
        translation_key="smart_meter_phase_3_reactive_power",
        field=FIELD_CT_REACTIVE_POWER3,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # Diagnostic identifiers for the CT/Smart-Meter subdevice
    # (default-disabled).
    JackerySmartMeterSensorDescription(
        key="communication_mode",
        translation_key="smart_meter_communication_mode",
        field=FIELD_COMM_MODE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="ip_address",
        translation_key="smart_meter_ip_address",
        field=FIELD_IP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    JackerySmartMeterSensorDescription(
        key="mac_address",
        translation_key="smart_meter_mac_address",
        # Third-party CT meters (e.g. Shelly Pro 3EM cloud2cloud) omit ``mac``
        # from the ``ct_meter`` block; their hardware id is the separator-less
        # MAC in ``deviceSn``/``deviceId`` (``properties.mac`` is the station
        # MAC, not the meter's). Fall back to those so the sensor resolves.
        field=FIELD_MAC,
        fallback_fields=(FIELD_DEVICE_SN, FIELD_DEVICE_ID),
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # CtSub.funForm per source-of-truth/jackery_entity_field_candidates_v2.html —
    # function-form / wiring-mode identifier. Diagnostic, default-disabled.
    JackerySmartMeterSensorDescription(
        key="fun_form",
        translation_key="smart_meter_fun_form",
        field=FIELD_CT_FUN_FORM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


class JackerySavingsDetailSensor(JackeryEntity, SensorEntity):
    """Expose one intermediate value from the total-savings calculation."""

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

    @property
    def _calculation(self) -> dict[str, Any]:
        savings = (self._statistic or {}).get(APP_SAVINGS_CALC_META)
        return savings if isinstance(savings, dict) else {}

    @property
    def native_value(self) -> float | int | str | None:
        """Return the selected calculated value."""  # ruff:ignore[property-docstring-starts-with-verb]
        raw: object = self._calculation
        for key in self.entity_description.path:
            if not isinstance(raw, dict):
                return None
            raw = raw.get(key)
        if raw is None:
            return None
        value = self.entity_description.transform(raw)
        if self.entity_description.key == "savings_price" and isinstance(value, float):
            return round(value, SAVINGS_PRICE_PRECISION)
        return cast("float | int | str | None", value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return calculation context for diagnostics."""  # ruff:ignore[property-docstring-starts-with-verb]
        calculation = self._calculation
        return {
            "source_section": PAYLOAD_STATISTIC,
            "source_key": APP_SAVINGS_CALC_META,
            "source_path": ".".join(self.entity_description.path),
            "method": calculation.get("method"),
            "price_source": calculation.get("price_source"),
            "published_value_source": calculation.get("published_value_source"),
            "decision": calculation.get("decision"),
        }


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
async def async_setup_entry(  # ruff:ignore[unused-async, complex-structure, too-many-statements]  # HA entry point; entity-build logic lives in the nested _collect_entities closure over coordinator/options
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
    seen_unique_ids: set[str] = set()
    battery_pack_identities: dict[tuple[str, int], tuple[str | None, str]] = {}

    def _entity_option_signature() -> tuple[bool, bool, bool]:
        """Return the current options that control sensor registration."""
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

    def _append_unique(entities: list[SensorEntity], entity: SensorEntity) -> None:
        append_unique_entity(entities, seen_unique_ids, entity)

    def _registration_eligibility() -> set[tuple[str, str, str]]:
        """Return value-gated entity keys supported by the latest payload."""
        eligible: set[tuple[str, str, str]] = set()
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload_properties_for_sources(payload)
            is_portable = _is_portable_payload(payload, props)
            system = payload.get(PAYLOAD_SYSTEM) or {}
            grid_standard = (
                _system_meta_scalar_value(system.get(FIELD_GRID_STANDARD))
                if isinstance(system, dict)
                else None
            )
            if not is_portable and grid_standard is not None:
                eligible.add((dev_id, "system_meta", "grid_standard"))
            description_groups = (
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
            for descriptions in description_groups:
                for description in descriptions:
                    if _sensor_description_has_value(payload, description):
                        eligible.add((dev_id, "property", description.key))
            for stat_description in STAT_DESCRIPTIONS:
                if _stat_description_has_value(payload, stat_description):
                    eligible.add((dev_id, "stat", stat_description.key))
            for smart_meter_description in SMART_METER_SENSOR_DESCRIPTIONS:
                if _smart_meter_description_has_value(payload, smart_meter_description):
                    eligible.add((dev_id, "smart_meter", smart_meter_description.key))
            ct = payload.get(PAYLOAD_CT_METER) or {}
            if not isinstance(ct, dict):
                ct = {}
            if jackery_corrected_home_consumption_power(ct, props) is not None:
                eligible.add((dev_id, "derived", "home_consumption_power"))
        return eligible

    def _collect_entities(  # ruff:ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]
        option_signature: tuple[bool, bool, bool],
    ) -> list[SensorEntity]:
        """Collect and instantiate all sensor entities for each device payload present
        in the coordinator.

        Builds sensors from property-driven descriptions, app/statistic charts, battery
        packs, smart plugs,
        meter heads, CT/smart-meter entries, and several calculated or diagnostic
        sensors based on
        integration options (calculated power, savings details, smart-meter derived
        sensors). Entities
        are created only when their source payloads or required values are present; many
        diagnostic
        entities are added disabled by default.

        Returns:
            list[SensorEntity]: A list of instantiated sensor entities ready for
            registration.
        """
        (
            create_smart_meter_derived,
            create_calculated_power,
            create_savings_details,
        ) = option_signature
        entities: list[SensorEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload_properties_for_sources(payload)

            is_portable = _is_portable_payload(payload, props)
            # Main-device entity identity is a device capability, not a snapshot
            # value.  HomeBody/SystemBody fields arrive independently over HTTP,
            # Cloud MQTT, local MQTT and BLE; gating registration on the first
            # payload left existing registry entities restored-only after a
            # restart whenever one supplemental transport was disabled.
            #
            # The 30.05 implementation already registered the complete home
            # family eagerly.  Keep value gating only when a portable payload is
            # being inspected so portable stations do not inherit SolarVault
            # home-only entities.
            for desc in SENSOR_DESCRIPTIONS:
                if is_portable and not _sensor_description_has_value(payload, desc):
                    continue
                _append_unique(entities, JackerySensor(coordinator, dev_id, desc))

            # PortableBody sensors — the portable family is stable once the
            # discovery/model evidence identifies the device as portable.
            # The grid_standard diagnostic sensor registers eagerly like the
            # rest of the home family (transport independence: no payload may
            # be required for registration). Only a PRESENT but non-scalar
            # value (list/dict) is malformed and must not spawn an entity.
            system_meta = payload.get(PAYLOAD_SYSTEM)
            raw_grid_standard = (
                system_meta.get(FIELD_GRID_STANDARD)
                if isinstance(system_meta, dict)
                else None
            )
            grid_standard_malformed = (
                raw_grid_standard is not None
                and _system_meta_scalar_value(raw_grid_standard) is None
            )
            if not is_portable and not grid_standard_malformed:
                _append_unique(
                    entities,
                    JackerySystemMetaSensor(
                        coordinator,
                        dev_id,
                        key="grid_standard",
                        translation_key="grid_standard",
                        source_key=FIELD_GRID_STANDARD,
                    ),
                )
            if is_portable:
                for desc in PORTABLE_SENSOR_DESCRIPTIONS:
                    _append_unique(entities, JackerySensor(coordinator, dev_id, desc))

            # Smart Mode / AI Schedule sensors (home systems)
            if not is_portable:
                for desc in SMART_MODE_SENSOR_DESCRIPTIONS:
                    _append_unique(entities, JackerySensor(coordinator, dev_id, desc))
                for desc in SMART_SCHEDULE_SENSOR_DESCRIPTIONS:
                    _append_unique(entities, JackerySensor(coordinator, dev_id, desc))
                for desc in DYNAMIC_PRICE_SENSOR_DESCRIPTIONS:
                    _append_unique(entities, JackerySensor(coordinator, dev_id, desc))

                # TOU Plan sensors (home systems)
                for desc in TOU_PLAN_SENSOR_DESCRIPTIONS:
                    _append_unique(entities, JackerySensor(coordinator, dev_id, desc))

            # Period/statistic entities are part of the stable SolarVault entity
            # family. Their cloud sections arrive independently and may be empty
            # during startup, an outage, or a background backfill. Do not remove
            # existing recorder identities merely because the current snapshot has
            # no value. Portable devices retain capability gating because they do
            # not implement the complete home-stat endpoint family.
            for stat_desc in STAT_DESCRIPTIONS:
                if is_portable and not _stat_description_has_value(payload, stat_desc):
                    continue
                _append_unique(
                    entities, JackeryStatSensor(coordinator, dev_id, stat_desc)
                )

            if create_calculated_power:
                _append_unique(
                    entities, JackeryBatteryNetPowerSensor(coordinator, dev_id)
                )
                _append_unique(
                    entities, JackeryBatteryStackNetPowerSensor(coordinator, dev_id)
                )
                _append_unique(entities, JackeryGridNetPowerSensor(coordinator, dev_id))

            if create_savings_details:
                for savings_desc in SAVINGS_DETAIL_SENSOR_DESCRIPTIONS:
                    _append_unique(
                        entities,
                        JackerySavingsDetailSensor(coordinator, dev_id, savings_desc),
                    )
                _append_unique(
                    entities, JackeryConversionLossPowerSensor(coordinator, dev_id)
                )

            # Alarm and firmware are stable diagnostics. Their source endpoints can
            # be temporarily empty without changing device capabilities.
            _append_unique(entities, JackeryAlarmSensor(coordinator, dev_id))

            # Firmware version from PROTOCOL.md §2 /v1/device/ota/list
            _append_unique(entities, JackeryFirmwareSensor(coordinator, dev_id))

            # Experimental BLE listener status (disabled by default; the
            # entity is only meaningful when the integration option
            # ``enable_ble_transport`` is on and shows zero otherwise).
            _append_unique(entities, JackeryBleTransportSensor(coordinator, dev_id))

            # Cloud transport diagnostic sensors (disabled by default).
            _append_unique(entities, JackeryHttpApiSensor(coordinator, dev_id))
            _append_unique(entities, JackeryCloudMqttSensor(coordinator, dev_id))

            # Local MQTT diagnostic sensor (disabled by default).
            _append_unique(entities, JackeryLocalMqttSensor(coordinator, dev_id))

            # Device activation diagnostic sensor (disabled by default).
            _append_unique(
                entities,
                JackeryDeviceActivationSensor(coordinator, dev_id),
            )

            # Add-on battery packs come from the app's MQTT BatteryPackSub model.
            # Create the complete pack entity set once a pack exists or batNum
            # announces it; individual values may arrive in later MQTT/OTA packets.
            packs = payload.get(PAYLOAD_BATTERY_PACKS) or []
            if isinstance(packs, list):
                valid_packs = [pack for pack in packs if isinstance(pack, dict)]
                discovery_packs = sorted_battery_pack_payloads(
                    subdevice_accessories(
                        payload,
                        dev_type=SUBDEVICE_DEV_TYPE_BATTERY_PACK,
                    )
                )
                registration_packs = valid_packs or discovery_packs
                bat_num = safe_int(props.get(FIELD_BAT_NUM))
                if bat_num is None:
                    pack_count = min(5, len(registration_packs))
                else:
                    # App model: main battery telemetry lives in HomeBody while
                    # add-on battery cards use BatteryPackSub entries. `batNum`
                    # is the expected pack/card count, not a reason to collapse
                    # the first pack into the main device.
                    pack_count = min(5, max(len(registration_packs), 0, bat_num))
                for index in range(1, pack_count + 1):
                    identity = (dev_id, index)
                    pack_identity = battery_pack_identities.get(identity)
                    if pack_identity is None:
                        pack_sn = (
                            battery_pack_serial(registration_packs[index - 1])
                            if index <= len(registration_packs)
                            else None
                        ) or coordinator.battery_pack_identity_serial(dev_id, index)
                        coordinator.set_battery_pack_identity_override(
                            dev_id, index, pack_sn
                        )
                        pack_key = stable_subdevice_key("battery_pack", pack_sn, index)
                        pack_identity = battery_pack_identities[identity] = (
                            pack_sn,
                            pack_key,
                        )
                    pack_sn, pack_key = pack_identity
                    for pack_desc in BATTERY_PACK_SENSOR_DESCRIPTIONS:
                        if pack_desc.field == FIELD_CELL_TEMP and not any(
                            FIELD_CELL_TEMP in item for item in valid_packs
                        ):
                            continue
                        _append_unique(
                            entities,
                            JackeryBatteryPackSensor(
                                coordinator,
                                dev_id,
                                pack_index=index,
                                pack_sn=pack_sn,
                                pack_key=pack_key,
                                description=pack_desc,
                                enabled_default=pack_desc.entity_category
                                != EntityCategory.DIAGNOSTIC,
                            ),
                        )

            # Smart plugs come from the app's MQTT PlugSub model:
            # QuerySubDeviceGroupProperty actionId=3032/devType=6 returns a
            # `plugs` array stored as `smart_plugs` in the coordinator.
            valid_plugs = sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS))
            if not valid_plugs:
                valid_plugs = sorted_smart_plugs(
                    subdevice_accessories(
                        payload,
                        dev_type=SUBDEVICE_DEV_TYPE_SOCKET,
                    )
                )
            for index, plug in enumerate(valid_plugs, start=1):
                plug_sn = smart_plug_serial(plug)
                if plug_sn is None:
                    continue
                plug_key = stable_subdevice_key("smart_plug", plug_sn, index)
                for plug_desc in SMART_PLUG_SENSOR_DESCRIPTIONS:
                    _append_unique(
                        entities,
                        JackerySmartPlugSensor(
                            coordinator,
                            dev_id,
                            plug_index=index,
                            plug_sn=plug_sn,
                            plug_key=plug_key,
                            description=plug_desc,
                        ),
                    )

            # Meter heads / collectors are app MQTT `CollectorSub` payloads.
            # Expose them as disabled-by-default diagnostics until real payloads
            # confirm whether their energy totals should be user-facing.
            valid_meter_heads = sorted_meter_heads(payload.get(PAYLOAD_METER_HEADS))
            if not valid_meter_heads:
                valid_meter_heads = sorted_meter_heads([
                    *subdevice_accessories(
                        payload,
                        dev_type=SUBDEVICE_DEV_TYPE_METER_HEAD,
                    ),
                    *subdevice_accessories(
                        payload,
                        dev_type=SUBDEVICE_DEV_TYPE_METER,
                    ),
                ])
            for index, meter_head in enumerate(valid_meter_heads, start=1):
                meter_head_sn = meter_head_serial(meter_head)
                if meter_head_sn is None:
                    continue
                meter_head_key = stable_subdevice_key(
                    "meter_head",
                    meter_head_sn,
                    index,
                )
                for meter_desc in METER_HEAD_SENSOR_DESCRIPTIONS:
                    _append_unique(
                        entities,
                        JackeryMeterHeadSensor(
                            coordinator,
                            dev_id,
                            meter_head_index=index,
                            meter_head_sn=meter_head_sn,
                            meter_head_key=meter_head_key,
                            description=meter_desc,
                        ),
                    )

            # Circuit breakers come from the app's MQTT BoxBody model:
            # QueryCircuitProperty actionId=3047? returns a `cir` array.
            valid_circuits = sorted_circuits(payload.get(PAYLOAD_CIRCUIT_PROPERTY))
            if not valid_circuits:
                valid_circuits = sorted_circuits(
                    subdevice_accessories(
                        payload,
                        dev_type=SUBDEVICE_DEV_TYPE_BREAKER,
                    )
                )
            for index, breaker in enumerate(valid_circuits, start=1):
                breaker_id = circuit_id(breaker)
                if breaker_id is None:
                    continue
                breaker_key = stable_subdevice_key("breaker", breaker_id, index)
                for breaker_desc in BREAKER_SENSOR_DESCRIPTIONS:
                    _append_unique(
                        entities,
                        JackeryBreakerSensor(
                            coordinator,
                            dev_id,
                            breaker_index=index,
                            breaker_id=breaker_id,
                            breaker_key=breaker_key,
                            description=breaker_desc,
                        ),
                    )

            # Sub-device alarms (Smoke, Leak, etc.) come from subDevices array.
            valid_sub_devices = sorted_sub_devices(payload.get(PAYLOAD_SUBDEVICES))
            if not valid_sub_devices:
                valid_sub_devices = sorted_sub_devices([
                    *subdevice_accessories(
                        payload,
                        dev_type=SUBDEVICE_DEV_TYPE_SMOKE,
                    ),
                    *subdevice_accessories(
                        payload,
                        dev_type=SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY,
                    ),
                    *subdevice_accessories(
                        payload,
                        dev_type=SUBDEVICE_DEV_TYPE_WATER_LEAK,
                    ),
                ])
            for index, sub_device in enumerate(valid_sub_devices, start=1):
                sub_device_sn = sub_device_serial(sub_device)
                if sub_device_sn is None:
                    continue
                sub_device_key = stable_subdevice_key(
                    "sub_device", sub_device_sn, index
                )
                for alarm_desc in SUBDEVICE_ALARM_SENSOR_DESCRIPTIONS:
                    _append_unique(
                        entities,
                        JackerySubdeviceAlarmSensor(
                            coordinator,
                            dev_id,
                            sub_device_index=index,
                            sub_device_sn=sub_device_sn,
                            sub_device_key=sub_device_key,
                            description=alarm_desc,
                        ),
                    )

            # Smart meter / CT values arrive through MQTT sub-device responses.
            # Create them when discovery confirms a meter accessory, or when a
            # CT payload was already received before entity setup.
            has_smart_meter = bool(
                coordinator._has_smart_meter_accessory(payload)  # ruff:ignore[private-member-access]  # same-package discovery helper
                or payload.get(PAYLOAD_CT_METER)
            )
            if has_smart_meter:
                for ct_desc in SMART_METER_SENSOR_DESCRIPTIONS:
                    if ct_desc.calculation and not create_smart_meter_derived:
                        continue
                    _append_unique(
                        entities,
                        JackerySmartMeterSensor(coordinator, dev_id, ct_desc),
                    )

            if create_smart_meter_derived and has_smart_meter:
                _append_unique(
                    entities,
                    JackeryHomeConsumptionPowerSensor(coordinator, dev_id),
                )
        return entities

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

    @callback
    def _add_new_entities() -> None:
        """Detects changes in the coordinator data signature and adds any newly
        discovered entities to Home Assistant.

        Compares the current coordinator entity signature with the previously stored
        signature; when different, updates the stored signature, collects entities to
        create, and calls the platform's entity adder for any discovered entities.
        """
        nonlocal last_option_signature, last_signature
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
            async_add_entities(entities)
        last_signature = sig
        last_option_signature = option_signature
        known_registration_eligibility.update(registration_eligibility)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


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
        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
            and description.entity_category != EntityCategory.DIAGNOSTIC
        )

    @property
    def native_value(self) -> Any:  # ruff:ignore[any-type]  # dynamically computed HA sensor state value
        """The entity's current value."""
        source_payload = self._payload_for_sources(self.entity_description.data_sources)
        props = source_payload.get(PAYLOAD_PROPERTIES) or {}
        raw = self.entity_description.getter(props)
        if raw is None:
            for fallback in self.entity_description.fallbacks:
                raw = fallback(source_payload)
                if raw is not None:
                    break
        if raw is None:
            return None
        value = self.entity_description.transform(raw)
        if self.entity_description.value_map is not None:
            mapped = self.entity_description.value_map.get(value)
            if mapped is not None:
                return mapped
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose this entity's source values unconditionally, for diagnostics.

        Every sensor carries the raw value it was built from: the merged value
        actually published and the HTTP-only value before any MQTT/BLE overlay.

        These are emitted ALWAYS, not only when the two disagree. The previous
        difference-only version hid the source in exactly the case that needs
        it most — a value that is quietly stale looks perfectly consistent
        (merged == http), so the attributes went empty and a live diagnosis via
        ``/api/states`` or the entity page became impossible. Diagnosing from
        the entity itself is the whole point; routing this to a throttled JSONL
        instead (one record per channel per minute) cannot answer "did this
        value move between polls?" for second-rate telemetry.
        """
        merged_raw = self.entity_description.getter(self._merged_properties)
        http_raw = self.entity_description.getter(self._http_properties)
        attrs: dict[str, Any] = {
            "merged_raw_value": merged_raw,
            "http_raw_value": http_raw,
        }
        if merged_raw is not None and http_raw is not None and merged_raw != http_raw:
            # A live transport is overriding the cloud value right now.
            attrs["live_source_overrides_http"] = True
        return attrs


_PeriodResolution = tuple[list[float] | None, float | None, float | None]
_PeriodResolutionCache = dict[tuple[int, str, str], _PeriodResolution]


@dataclass(frozen=True, slots=True)
class _StatRefreshContext:
    """Immutable event-loop snapshot consumed by the executor batch."""

    payload: dict[str, Any]
    local_timezone: tzinfo
    local_now: datetime
    local_today: date
    local_daily_raw: tuple[float, str] | None


@dataclass(frozen=True, slots=True)
class _StatCacheSnapshot:
    """Computed stat state applied atomically back on the event loop."""

    native_value: Any
    attrs: dict[str, Any]
    source_section: str


class JackeryStatSensor(JackeryEntity, SensorEntity):
    """Sensor sourced from the statistic / price section of the payload."""

    def _non_negative_period_raw(self, raw: Any) -> Any:  # ruff: ignore[any-type]
        """Clamp negative energy period totals to zero when applicable."""
        if getattr(self, "_reset_period", None) is None:
            return raw
        if (
            getattr(self.entity_description, "device_class", None)
            != SensorDeviceClass.ENERGY
        ):
            return raw
        parsed = safe_float(raw)
        if parsed is not None and parsed < 0:
            return 0.0
        return raw

    @staticmethod
    def _derived_home_energy_fallback_enabled() -> bool:
        """Return whether derived home-energy fallback is enabled."""
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
        """Initialize a JackeryStatSensor entity using the coordinator state and a
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
        self._cached_native_value: Any = None
        self._cached_attrs: dict[str, Any] = {
            "source_section": description.section,
            "source_key": description.stat_key,
        }
        self._cached_source_section = description.section
        self._cache_generation = 0
        self._cache_refresh_active = False
        self._cache_initializing = False

    @property
    def last_reset(self) -> datetime | None:
        """Return the local period boundary (last_reset) for the statistic based on the
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
        """  # ruff:ignore[property-docstring-starts-with-verb]
        # last_reset is only valid on a TOTAL sensor. Non-period sensors (no
        # reset period) and the week/month/year totals (state_class=None, since
        # the external ``jackery_solarvault:`` statistics own their long-term
        # series) must return None: HA raises ValueError in
        # SensorEntity.state_attributes for a non-TOTAL sensor that sets a
        # last_reset, which otherwise aborts every state write and leaves the
        # entity permanently unavailable.
        if (
            self._reset_period is None
            or self._attr_state_class != SensorStateClass.TOTAL
        ):
            return None
        if self._reset_period == DATE_TYPE_DAY and self._is_period_data_stale():
            return _period_start(self._reset_period, self._local_timezone())
        if self._is_period_data_future():
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
        # Local midnight on the request's begin_date.
        return datetime(
            begin_date.year,
            begin_date.month,
            begin_date.day,
            tzinfo=self._local_timezone(),
        )

    def _local_timezone(self) -> tzinfo:
        """Get the Home Assistant local timezone for period sensors.

        Returns:
            timezone (Any): Timezone object from Home Assistant configuration; falls
            back to Home Assistant's default timezone when the configured value is
            unavailable.
        """
        timezone = dt_util.get_time_zone(self.hass.config.time_zone)
        return timezone or dt_util.DEFAULT_TIME_ZONE

    def _local_today(self) -> date:
        """Get the current local date in the Home Assistant timezone for app chart
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

    def _is_period_data_stale(
        self,
        source_section: str | None = None,
        payload: dict[str, Any] | None = None,
        local_timezone: tzinfo | None = None,
    ) -> bool:
        """Determine whether the source period data is older than the current local
        period.

        If the sensor has no reset period or the request metadata begin date is missing
        or invalid, the data is treated as fresh.

        Returns:
            `true` if the source period begin date is before the current local period
            start date, `false` otherwise.
        """
        if self._reset_period is None:
            return False
        wall_clock_start = _period_start(
            self._reset_period,
            local_timezone or self._local_timezone(),
        )
        begin_iso = self._period_begin_from_meta(source_section, payload)
        if begin_iso is None:
            return False
        try:
            data_begin = date.fromisoformat(begin_iso)
        except ValueError:
            return False
        return wall_clock_start.date() > data_begin

    def _is_period_data_future(
        self,
        source_section: str | None = None,
        payload: dict[str, Any] | None = None,
        local_timezone: tzinfo | None = None,
    ) -> bool:
        """Determine whether the source period begin date from request metadata is later
        than the current local period start.

        Returns:
            True if the source period begin date is after the local period start for the
            sensor's reset period, False otherwise.
        """
        if self._reset_period is None:
            return False
        wall_clock_start = _period_start(
            self._reset_period,
            local_timezone or self._local_timezone(),
        )
        begin_iso = self._period_begin_from_meta(source_section, payload)
        if begin_iso is None:
            return False
        try:
            data_begin = date.fromisoformat(begin_iso)
        except ValueError:
            return False
        return data_begin > wall_clock_start.date()

    def _source_for_section(  # ruff:ignore[too-many-return-statements]
        self,
        section: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:  # flat section→source dispatch; clearest as-is
        """Return the coordinator source dictionary corresponding to a payload section
        name.

        Parameters:
                section (str): The payload section key to resolve (e.g., price,
                statistic, trends).

        Returns:
                dict[str, Any]: The dict storing data for the requested section, or an
                empty dict if no usable source is available.
        """
        if payload is not None:
            source = payload.get(section) or {}
            return source if isinstance(source, dict) else {}
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

    def _current_day_bucket_from_period_chart(
        self,
        section: str,
        stat_key: str,
        *,
        payload: dict[str, Any] | None = None,
        today: date | None = None,
    ) -> tuple[float, str, dict[str, Any]] | None:
        """Derive today's metric from a week or month chart when the day-period endpoint
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
            value = _chart_value_for_day(
                candidate_source,
                candidate_section,
                stat_key,
                today=today,
            )
            if value is not None:
                return value, candidate_section, candidate_source
        return None

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
        values = effective_trend_series_values(source, section, stat_key)
        chart_series_sum: float | None = None
        if isinstance(values, list):
            chart_series_sum = round(
                sum(value for value in values if value is not None), 2
            )
        server_total = (
            chart_series_sum
            if is_device_year_period_section(source, section) and values is not None
            else safe_float(source.get(stat_key))
        )
        resolution = values, chart_series_sum, server_total
        period_cache[cache_key] = resolution
        return resolution

    def _refresh_cache(  # ruff:ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]  # period vs non-period cache build with regression-critical stale/future guards; kept in one method to keep the guarded paths co-located
        self,
        context: _StatRefreshContext,
        period_cache: _PeriodResolutionCache,
    ) -> _StatCacheSnapshot:
        """Recompute native_value and extra_state_attributes once per update."""
        payload = context.payload
        section = self.entity_description.section
        stat_key = self.entity_description.stat_key
        cached_source_section = section
        source = self._source_for_section(section, payload)
        series_key = _trend_series_key(section, stat_key)

        if series_key:
            # ---- period sensor path -------------------------------------
            values, chart_series_sum, server_total = self._resolve_period_value(
                source,
                section,
                stat_key,
                period_cache,
            )
            # Day-period payloads carry an intraday POWER curve (5-minute
            # samples, unit "w"). It must be time-integrated, never summed as
            # energy. The cloud scalar remains useful, but it can lag the live
            # curve badly, so the current day uses the larger of the scalar and
            # the integrated curve.
            raw: float | None
            day_curve_total: float | None = None
            day_curve_fallback = False
            if is_day_period_payload(source, section):
                raw = server_total
                if str(source.get(APP_STAT_UNIT) or "").strip().lower() == "w":
                    day_points = day_power_energy_points(
                        source,
                        section,
                        stat_key,
                        bucket_minutes=60,
                        today=context.local_today,
                        now=context.local_now,
                    )
                    if day_points:
                        day_curve_total = round(
                            sum(point.value for point in day_points),
                            5,
                        )
                        if raw is not None and day_curve_total > raw:
                            raw = day_curve_total
                            day_curve_fallback = True
            else:
                raw = chart_series_sum
                if raw is None:
                    raw = server_total
            local_daily_metric: str | None = None

            def _use_local_daily_fallback() -> bool:
                nonlocal \
                    raw, \
                    section, \
                    stat_key, \
                    source, \
                    series_key, \
                    values, \
                    chart_series_sum, \
                    server_total, \
                    local_daily_metric, \
                    cached_source_section

                local_daily = context.local_daily_raw
                if local_daily is None:
                    return False
                raw, local_daily_metric = local_daily
                section = PAYLOAD_LOCAL_DAILY_ENERGY
                stat_key = local_daily_metric
                source = self._source_for_section(section, payload)
                series_key = None
                values = None
                chart_series_sum = None
                server_total = None
                cached_source_section = section
                return True

            if raw is None:
                # PROTOCOL.md §2 fallback — try documented alternate
                # source (e.g. deviceStatistic for today_* sensors).
                for fb_section, fb_stat_key in self.entity_description.fallback_sources:
                    fb_source = self._source_for_section(fb_section, payload)
                    fb_values, fb_chart_sum, fb_server_total = (
                        self._resolve_period_value(
                            fb_source,
                            fb_section,
                            fb_stat_key,
                            period_cache,
                        )
                    )
                    fb_total = fb_server_total
                    if fb_total is None and not is_day_period_payload(
                        fb_source, fb_section
                    ):
                        fb_total = fb_chart_sum
                    if fb_total is not None:
                        raw = fb_total
                        section = fb_section
                        stat_key = fb_stat_key
                        source = fb_source
                        series_key = _trend_series_key(section, stat_key)
                        values = fb_values
                        chart_series_sum = fb_chart_sum
                        server_total = fb_server_total
                        break
            day_bucket_fallback: str | None = None
            if raw is None:
                # The cloud answers ``data: null`` for every dateType=day
                # endpoint at night; derive today's value from the month/
                # week chart bucket exactly like the non-period path does.
                day_sources = (
                    (section, stat_key),
                    *self.entity_description.fallback_sources,
                )
                for candidate_section, candidate_stat_key in day_sources:
                    bucket = self._current_day_bucket_from_period_chart(
                        candidate_section,
                        candidate_stat_key,
                        payload=payload,
                        today=context.local_today,
                    )
                    if bucket is None:
                        continue
                    raw, bucket_section, bucket_source = bucket
                    section = bucket_section
                    stat_key = candidate_stat_key
                    source = bucket_source
                    day_bucket_fallback = f"current_day_bucket_from_{bucket_section}"
                    break
            if raw is None and day_curve_total is not None:
                raw = day_curve_total
                day_curve_fallback = True
            if raw is None:
                _use_local_daily_fallback()

            # Stale-period guard per CHANGELOG "Three-part fix" / Midnight
            # race. When the wall clock has crossed a period boundary but
            # the source data is still stamped with the previous period's
            # begin_date, native_value is set to None for ALL periods
            # (including DAY). HA Recorder writes "unavailable" for that
            # brief window and never sees an artificial spike+drop. DO
            # NOT reintroduce a DAY-only carve-out (raw=0) — that recreates
            # the midnight delta bug the three-part fix was designed to
            # prevent (observed regression on 2026-05-16 battery year
            # energy spike where the cloud briefly served 0 inside the
            # same period anchor and the Energy Dashboard rendered a
            # -X kWh delta).
            # A current-day bucket lifted from a month/week chart is
            # already indexed to today's date; do not compare the chart's
            # period begin (month/week start) to a daily reset boundary.
            cached_source_section = (
                self.entity_description.section
                if day_bucket_fallback is not None
                else section
            )
            stale_period = (
                False
                if day_bucket_fallback is not None
                else self._reset_period
                and self._is_period_data_stale(
                    cached_source_section,
                    payload,
                    context.local_timezone,
                )
            )
            future_period = (
                False
                if day_bucket_fallback is not None
                else self._reset_period
                and self._is_period_data_future(
                    cached_source_section,
                    payload,
                    context.local_timezone,
                )
            )
            if stale_period or future_period:
                raw = None
            if raw is None:
                _use_local_daily_fallback()
            cached_native_value = (
                self.entity_description.transform(raw) if raw is not None else None
            )

            # PROTOCOL.md §8 keeps period sensors' attributes lean: source
            # identification, parsed period values, request range and any
            # year/month backfill metadata. JSON-stringified duplicates and
            # cloud-shape heuristics belong in diagnostics/payload_debug, not
            # in the entity state.
            attrs: dict[str, Any] = {
                "source_section": section,
                "source_key": stat_key,
                "chart_series_key": series_key,
                "chart_series_sum": chart_series_sum,
                "server_total": server_total,
            }
            if day_curve_total is not None:
                attrs["integrated_power_curve_total"] = day_curve_total
            if day_curve_fallback:
                attrs["fallback"] = "integrated_current_day_power_curve"
            if day_bucket_fallback is not None:
                attrs["fallback"] = day_bucket_fallback
            if local_daily_metric is not None:
                attrs["fallback"] = "local_lifetime_delta"
                attrs["fallback_metric"] = local_daily_metric
            if isinstance(values, list) and len(values) <= _MAX_PERIOD_VALUES:
                attrs["period_values"] = values
            year_backfill = source.get(APP_YEAR_BACKFILL_META)
            if isinstance(year_backfill, dict):
                attrs["year_month_backfill"] = year_backfill
            request = source.get(APP_REQUEST_META)
            if isinstance(request, dict):
                attrs["request"] = request
            if stale_period:
                attrs["stale_period_data"] = True
                attrs["stale_period_begin_date"] = self._period_begin_from_meta(
                    cached_source_section,
                    payload,
                )
                attrs["stale_period_fallback"] = (
                    "local_lifetime_delta"
                    if local_daily_metric is not None
                    else "unknown_until_local_period"
                )
            if future_period:
                attrs["future_period_data"] = True
                attrs["future_period_begin_date"] = self._period_begin_from_meta(
                    cached_source_section,
                    payload,
                )
                attrs["future_period_fallback"] = (
                    "local_lifetime_delta"
                    if local_daily_metric is not None
                    else "unknown_until_local_period"
                )
            return _StatCacheSnapshot(
                cached_native_value,
                attrs,
                cached_source_section,
            )

        # ---- non-period stat path (totalGeneration, todayLoad, price, ...)
        raw = source.get(stat_key)
        day_bucket_fallback = None
        if raw is None:
            for fb_section, fb_stat_key in self.entity_description.fallback_sources:
                fb_source = self._source_for_section(fb_section, payload)
                raw = fb_source.get(fb_stat_key)
                if raw is not None:
                    section = fb_section
                    stat_key = fb_stat_key
                    source = fb_source
                    break
        if raw is None:
            day_sources = (
                (section, stat_key),
                *self.entity_description.fallback_sources,
            )
            for candidate_section, candidate_stat_key in day_sources:
                bucket = self._current_day_bucket_from_period_chart(
                    candidate_section,
                    candidate_stat_key,
                    payload=payload,
                    today=context.local_today,
                )
                if bucket is None:
                    continue
                raw, bucket_section, bucket_source = bucket
                section = bucket_section
                stat_key = candidate_stat_key
                source = bucket_source
                day_bucket_fallback = f"current_day_bucket_from_{bucket_section}"
                break
        # A current-day bucket lifted from a month/week chart is already
        # indexed to today's date; do not compare the chart's period begin
        # (month/week start) to a daily reset boundary.
        cached_source_section = (
            self.entity_description.section
            if day_bucket_fallback is not None
            else section
        )
        stale_period = (
            False
            if day_bucket_fallback is not None
            else self._reset_period
            and self._is_period_data_stale(
                cached_source_section,
                payload,
                context.local_timezone,
            )
        )
        future_period = (
            False
            if day_bucket_fallback is not None
            else self._reset_period
            and self._is_period_data_future(
                cached_source_section,
                payload,
                context.local_timezone,
            )
        )
        # Stale/future guard per CHANGELOG "Three-part fix" / Midnight
        # race: None for ALL periods (incl. DAY), HA Recorder writes
        # "unavailable" instead of a fake 0 that would clash with the
        # previous period's positive value at the same last_reset and
        # produce a negative Energy-Dashboard delta.
        if stale_period or future_period:
            raw = None
        cached_native_value = (
            self.entity_description.transform(raw) if raw is not None else None
        )
        # Non-period stats keep a minimal attribute set per
        # PROTOCOL.md §8 "Minimal entity diagnostic attributes".
        non_period_attrs: dict[str, Any] = {
            "source_section": section,
            "source_key": stat_key,
        }
        if day_bucket_fallback is not None:
            non_period_attrs["fallback"] = day_bucket_fallback
        if stale_period:
            non_period_attrs["stale_period_data"] = True
            non_period_attrs["stale_period_begin_date"] = self._period_begin_from_meta(
                cached_source_section,
                payload,
            )
            if self._reset_period == DATE_TYPE_DAY:
                non_period_attrs["stale_period_fallback"] = "zero_until_fresh_day_data"
        if future_period:
            non_period_attrs["future_period_data"] = True
            non_period_attrs["future_period_begin_date"] = self._period_begin_from_meta(
                cached_source_section,
                payload,
            )
            non_period_attrs["future_period_fallback"] = "unknown_until_local_period"
        total_guard = source.get(APP_TOTAL_GUARD_META)
        if isinstance(total_guard, dict):
            corrected = total_guard.get("corrected")
            if isinstance(corrected, dict) and stat_key in corrected:
                non_period_attrs["total_lower_bound_guard"] = total_guard
        savings = source.get(APP_SAVINGS_CALC_META)
        if stat_key == APP_STAT_TOTAL_REVENUE and isinstance(savings, dict):
            non_period_attrs["savings_calculation"] = savings
        # APP cloud quirk: ``todayLoad`` historically equals the inverter's
        # on-grid output for the day, not the real household consumption.
        # Flag the caveat in attributes so dashboards do not mistake it
        # for a smart-meter total. The smart_meter_derived sensors expose
        # the real home consumption when the option is enabled.
        if stat_key == APP_STAT_TODAY_LOAD:
            non_period_attrs["cloud_field"] = "todayLoad"
            non_period_attrs["cloud_caveat"] = (
                "Jackery cloud reports the inverter's on-grid output for "
                "today; this is not smart-meter home consumption. For "
                "actual consumption enable the smart_meter_derived option "
                "and use the home_consumption sensor."
            )
        return _StatCacheSnapshot(
            cached_native_value,
            non_period_attrs,
            cached_source_section,
        )

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
        )

    @callback
    def _apply_cache_snapshot(self, snapshot: _StatCacheSnapshot) -> None:
        """Apply a completed snapshot atomically on the event loop."""
        self._cached_native_value = snapshot.native_value
        self._cached_attrs = snapshot.attrs
        self._cached_source_section = snapshot.source_section

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
        batch = _stat_refresh_batch_for(self.coordinator)
        self._cache_refresh_active = True
        self._cache_initializing = True
        try:
            await super().async_added_to_hass()
        except Exception, asyncio.CancelledError:
            batch.discard(self)
            raise
        finally:
            self._cache_initializing = False
        # EntityPlatform adds entities sequentially. Waiting here would drain one
        # executor job per entity and block platform setup. Queue without waiting so
        # the batch's initial event-loop yield can collect every statistic entity.
        batch.request(self, write_state=True)

    async def async_will_remove_from_hass(self) -> None:
        """Invalidate queued work before the entity leaves Home Assistant."""
        _stat_refresh_batch_for(self.coordinator).discard(self)
        await super().async_will_remove_from_hass()

    @property
    def native_value(self) -> Any:  # ruff:ignore[any-type]  # dynamically computed HA sensor state value
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
        if self.entity_description.device_class != SensorDeviceClass.MONETARY:
            return self.entity_description.native_unit_of_measurement
        source = self._source_for_section(self._cached_source_section)
        currency = source.get(FIELD_CURRENCY)
        if isinstance(currency, str) and currency.strip():
            return currency
        return self.entity_description.native_unit_of_measurement

    # --- restored from 24.05\24.05\custom_components\jackery_solarvault\sensor.py ---
    def _local_daily_metric_key(self) -> str | None:
        """Return the local lifetime-counter metric for this DAY sensor."""
        if self._reset_period != DATE_TYPE_DAY:
            return None
        if self.entity_description.device_class != SensorDeviceClass.ENERGY:
            return None
        return LOCAL_DAILY_METRIC_BY_SENSOR_KEY.get(self.entity_description.key)

    def _local_daily_raw(self) -> tuple[float, str] | None:
        """Return today's local BLE/MQTT/HTTP delta in kWh for this sensor."""
        metric_key = self._local_daily_metric_key()
        if metric_key is None:
            return None
        value = self.coordinator.local_daily_energy_kwh(self._device_id, metric_key)
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


@dataclass(frozen=True, slots=True)
class _StatRefreshResult:
    """Executor result returned for event-loop application."""

    request: _StatRefreshRequest
    snapshot: _StatCacheSnapshot | None = None
    error: Exception | None = None


def _build_stat_refreshes(
    requests: tuple[_StatRefreshRequest, ...],
) -> tuple[_StatRefreshResult, ...]:
    """Compute one coordinator batch with shared period memoization."""
    period_cache: _PeriodResolutionCache = {}
    results: list[_StatRefreshResult] = []
    for request in requests:
        try:
            snapshot = request.entity._refresh_cache(request.context, period_cache)  # ruff:ignore[private-member-access]
        except Exception as err:  # ruff:ignore[blind-except]  # isolate one entity from the shared executor batch
            results.append(_StatRefreshResult(request=request, error=err))
        else:
            results.append(_StatRefreshResult(request=request, snapshot=snapshot))
    return tuple(results)


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

    @staticmethod
    @callback
    def _is_current(entity: JackeryStatSensor, generation: int) -> bool:
        """Return whether a result may still affect this live entity."""
        return entity._cache_refresh_active and entity._cache_generation == generation  # ruff:ignore[private-member-access]

    @callback
    def _ensure_task(self, entity: JackeryStatSensor) -> None:
        """Start an unload-managed drain task or fail queued refreshes."""
        if self._task is not None:
            return
        refresh_coro = self._async_run(entity.hass)
        try:
            self._task = entity.coordinator.entry.async_create_background_task(
                entity.hass,
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
        pending = self._pending.get(entity)
        self._pending[entity] = (
            entity._cache_generation,  # ruff:ignore[private-member-access]
            write_state or (pending is not None and pending[1]),
        )
        self._ensure_task(entity)

    @callback
    def discard(self, entity: JackeryStatSensor) -> None:
        """Discard all work for an entity and invalidate in-flight results."""
        entity._cache_refresh_active = False  # ruff:ignore[private-member-access]
        entity._cache_generation += 1  # ruff:ignore[private-member-access]
        self._pending.pop(entity, None)
        self._failure_signatures.pop(entity, None)

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
                context = entity._capture_refresh_context(payloads[device_id])  # ruff:ignore[private-member-access]
            except Exception as err:  # ruff:ignore[blind-except]  # isolate per-entity event-loop capture
                self._fail_entity(
                    entity,
                    generation,
                    err,
                    "capture statistic refresh context",
                )
                continue
            requests.append(
                _StatRefreshRequest(
                    entity=entity,
                    generation=generation,
                    context=context,
                    write_state=write_state,
                )
            )
        return tuple(requests)

    @callback
    def _apply_result(self, result: _StatRefreshResult) -> None:
        """Apply and write one result without affecting other entities."""
        request = result.request
        entity = request.entity
        if not self._is_current(entity, request.generation):
            return
        if result.error is not None:
            self._fail_entity(
                entity,
                request.generation,
                result.error,
                "compute statistic cache",
            )
            return
        snapshot = result.snapshot
        if snapshot is None:
            self._fail_entity(
                entity,
                request.generation,
                RuntimeError("Statistic refresh returned no cache snapshot"),
                "compute statistic cache",
            )
            return
        try:
            entity._apply_cache_snapshot(snapshot)  # ruff:ignore[private-member-access]
        except Exception as err:  # ruff:ignore[blind-except]  # apply failures are entity-local
            self._fail_entity(
                entity,
                request.generation,
                err,
                "apply statistic cache",
            )
            return
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
                return
        self._failure_signatures.pop(entity, None)

    @callback
    def _cancel_all(
        self,
        in_flight: dict[JackeryStatSensor, tuple[int, bool]],
    ) -> None:
        """Discard pending/in-flight work when the entry task is cancelled."""
        entities = set(in_flight) | set(self._pending)
        for entity in entities:
            self.discard(entity)

    async def _async_run(self, hass: HomeAssistant) -> None:  # ruff:ignore[too-many-branches]
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
                    results = await hass.async_add_executor_job(
                        _build_stat_refreshes,
                        requests,
                    )
                    if len(results) != len(requests) or any(
                        result.request is not request
                        for result, request in zip(results, requests, strict=True)
                    ):
                        raise RuntimeError(  # ruff:ignore[raise-vanilla-args, raise-within-try]
                            "Statistic refresh executor returned an incomplete batch"
                        )
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
                for result in results:
                    try:
                        self._apply_result(result)
                    except Exception as err:  # ruff:ignore[blind-except]  # isolate unexpected per-result failures
                        self._fail_entity(
                            result.request.entity,
                            result.request.generation,
                            err,
                            "apply statistic refresh result",
                        )
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


_STAT_REFRESH_BATCHES: WeakKeyDictionary[
    JackerySolarVaultCoordinator,
    _StatRefreshBatch,
] = WeakKeyDictionary()


@callback
def _stat_refresh_batch_for(
    coordinator: JackerySolarVaultCoordinator,
) -> _StatRefreshBatch:
    """Return the shared statistic refresh batch for a coordinator."""
    batch = _STAT_REFRESH_BATCHES.get(coordinator)
    if batch is None:
        batch = _StatRefreshBatch()
        _STAT_REFRESH_BATCHES[coordinator] = batch
    return batch


class JackeryBatteryPackSensor(JackeryEntity, SensorEntity):
    """Per battery-pack sensor from MQTT BatteryPackSub plus OTA metadata."""

    entity_description: JackeryBatteryPackSensorDescription

    def __init__(  # ruff:ignore[too-many-arguments]
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        pack_index: int,
        pack_sn: str | None,
        pack_key: str,
        description: JackeryBatteryPackSensorDescription,
        enabled_default: bool = True,
    ) -> None:
        """Create a battery-pack sensor entity for a specific device and pack index
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
        self._cached_native_value: Any = None
        self._cached_attrs: dict[str, Any] = {"pack_index": pack_index}

    @property
    def _pack(self) -> dict[str, Any]:
        """Return the battery pack dictionary for this entity's configured pack index.

        Selects the pack at the 1-based index stored on the entity from the payload's
        PAYLOAD_BATTERY_PACKS list. Returns an empty dict when the packs section is
        missing, not a list, the index is out of range, or the selected entry is not a
        dict.

        Returns:
            dict: The battery pack dictionary when available, otherwise an empty dict.
        """  # ruff:ignore[property-docstring-starts-with-verb]
        packs = self._payload.get(PAYLOAD_BATTERY_PACKS) or []
        # Sort by serial before any positional lookup: the cloud/MQTT list
        # order is not guaranteed, and indexing the raw list would let index N
        # bind to a different physical pack across HA restarts (a fresh
        # entity re-pins ``_pack_sn`` on its first resolution each session).
        pack_dicts = sorted_battery_pack_payloads(packs)
        # Prefer matching by the pack's own serial: the SN-keyed merge sink can
        # reorder the list between polls, and index-only lookup then made this
        # entity read a sibling pack's values or flip to Unknown.
        if self._pack_sn is not None:
            serial_key = stable_subdevice_key(
                "battery_pack", self._pack_sn, self._pack_index
            )
            for pack in pack_dicts:
                if (
                    stable_subdevice_key(
                        "battery_pack",
                        battery_pack_serial(pack),
                        self._pack_index,
                    )
                    == serial_key
                ):
                    return pack
            return {}
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

    def _value_from_pack(self, pack: dict[str, Any]) -> Any:  # ruff:ignore[any-type]  # dynamically computed HA sensor state value
        """Extracts the described battery-pack field from a battery-pack payload and
        applies the entity transform.

        Looks up the field named by the entity description in the provided pack dict. If
        the primary key is missing, checks a small set of known alias and alternate keys
        (including current firmware version, device serial candidates, and
        firmware-upgrade flag) before giving up.

        Parameters:
            pack (dict[str, Any]): Battery pack payload dictionary.

        Returns:
            The transformed field value when present, `None` if the field (and any
            fallbacks) are absent.
        """
        field = self.entity_description.field
        raw = pack.get(field)
        if raw is None:
            alias_map = {
                FIELD_BAT_SOC: FIELD_RB,
                FIELD_IN_PW: FIELD_IP,
                FIELD_OUT_PW: FIELD_OP,
            }
            alias = alias_map.get(field)
            if alias:
                raw = pack.get(alias)
        if raw is None and field == FIELD_VERSION:
            raw = pack.get(FIELD_CURRENT_VERSION)
        if raw is None and field == FIELD_DEVICE_SN:
            raw = pack.get(FIELD_DEV_SN) or pack.get(FIELD_SN)
        if raw is None and field == FIELD_UPDATE_STATUS:
            raw = pack.get(FIELD_IS_FIRMWARE_UPGRADE)
        if (
            raw is None
            and field == FIELD_COMM_STATE
            # No transport (neither HTTP nor BLE cmd=120) reports ``commState``
            # for this pack, so a raw lookup always yields ``None`` and the
            # sensor showed "unknown". Derive the state from presence + live
            # telemetry instead: a pack dict carrying live values (SOC / power /
            # cell temperature) is, by definition, currently communicating —
            # equivalent to ``commState == 1``. When the pack is absent or has
            # gone stale (no live fields) the value stays ``None`` (unknown),
            # which is the correct disconnected signal.
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
        return self.entity_description.transform(raw)

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

    def _refresh_cache(self) -> None:
        """Refresh the cached native value and extra state attributes from the current
        battery pack.

        This updates self._cached_native_value and self._cached_attrs using the current
        pack snapshot; intended to be run once per coordinator update.
        """
        pack = self._pack
        self._cached_native_value = _guard_total_increasing_jitter(
            self._cached_native_value,
            self._value_from_pack(pack),
            self.entity_description,
        )
        self._cached_attrs = self._attrs_from_pack(pack)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh cached BatteryPackSub values before HA writes state."""
        self._refresh_cache()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime the cache before CoordinatorEntity writes the initial state."""
        self._refresh_cache()
        await super().async_added_to_hass()

    @property
    def native_value(self) -> Any:  # ruff:ignore[any-type]  # dynamically computed HA sensor state value
        """The entity's last cached native value.

        Returns:
            The cached native value from the most recent coordinator update, or `None`
            if unavailable.
        """
        return self._cached_native_value

    @property
    def device_info(self) -> DeviceInfo:
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
            fallback="Jackery Zusatzbatterie",
        )
        version = first_nonblank_text(
            pack.get(FIELD_VERSION),
            pack.get(FIELD_CURRENT_VERSION),
        )
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{self._pack_key}")},
            manufacturer=MANUFACTURER,
            name=f"{base_name} Zusatzbatterie {self._pack_index}",
            model=str(model),
            serial_number=str(sn) if sn else None,
            sw_version=str(version) if version else None,
            via_device=(DOMAIN, self._device_id),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Provide the entity's extra state attributes intended for diagnostics.

        Returns:
            dict[str, Any]: Mapping of diagnostic attribute names to their values (may
            be empty).
        """
        return self._cached_attrs


class JackerySmartPlugSensor(JackeryEntity, SensorEntity):
    """Per smart-plug sensor from MQTT PlugSub payloads."""

    entity_description: JackerySmartPlugSensorDescription

    def __init__(  # ruff:ignore[too-many-arguments]  # entity constructor takes distinct plug-identity fields
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        plug_index: int,
        plug_sn: str,
        plug_key: str,
        description: JackerySmartPlugSensorDescription,
    ) -> None:
        """Initialize a smart-plug sensor entity for a specific plug (by index and
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
        # Build the per-plug device_info once at construction (see PROTOCOL §8
        # and binary_sensor.py for the rationale).
        self._attr_device_info = self._build_smart_plug_device_info(
            plug_index, self._plug, plug_key
        )

    @property
    def _plug(self) -> dict[str, Any]:
        # Look up by captured serial; cloud-side re-ordering of the plug
        # array must not switch this entity to a different physical plug.
        """Find the smart-plug payload that matches this entity's captured serial
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

    @property
    def native_value(self) -> Any:  # ruff:ignore[any-type]  # dynamically computed HA sensor state value
        """The smart plug entity's current sensor value from its plug payload.

        Reads the configured field from the plug data, falls back to known alias fields
        when the primary key is missing, and applies the entity description's transform.

        Returns:
            The transformed sensor value, or `None` if the value is not available.
        """
        field = self.entity_description.field
        raw = self._plug.get(field)
        if raw is None:
            alias_map = {
                FIELD_IN_PW: FIELD_IP,
                FIELD_OUT_PW: FIELD_OP,
                FIELD_SWITCH_STATE: FIELD_SYS_SWITCH,
            }
            alias = alias_map.get(field)
            if alias:
                raw = self._plug.get(alias)
        if raw is None:
            return None
        return self.entity_description.transform(raw)

    @property
    def last_reset(self) -> datetime | None:
        """Compute the start datetime of the configured reset period for this entity.

        Returns:
            datetime: The period start datetime for the configured reset period (local
            timezone), or `None` when no reset period is configured.
        """
        if self._reset_period is None:
            return None
        return _period_start(self._reset_period)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current smart plug.

        Returns:
            dict[str, Any]: Mapping of attribute names to values. Always includes
            `plug_index` and, when present on the plug payload, any of:
            device name, scan name, communication state, communication mode,
            switch state (both `FIELD_SWITCH_STATE` and `FIELD_SYS_SWITCH` variants),
            socket priority, today's energy, total energy, and version.
        """
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
            if key in self._plug:
                attrs[key] = self._plug.get(key)
        return attrs


class JackeryBreakerSensor(JackeryEntity, SensorEntity):
    """Per-circuit breaker sensor from MQTT QueryCircuitProperty payloads."""

    entity_description: JackeryBreakerSensorDescription

    def __init__(  # ruff:ignore[too-many-arguments]
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        breaker_index: int,
        breaker_id: str,
        breaker_key: str,
        description: JackeryBreakerSensorDescription,
    ) -> None:
        """Initialize a circuit breaker sensor entity."""
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
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{breaker_key}")},
            manufacturer=MANUFACTURER,
            name=f"{base_name} {name}",
            model="Jackery Sicherung",
            via_device=(DOMAIN, self._device_id),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic state attributes for the breaker.

        Returns:
            dict[str, Any]: Mapping of attribute names to their current values.
        """  # ruff:ignore[property-docstring-starts-with-verb]
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
    """Per-subdevice alarm/event sensor."""

    entity_description: JackerySubdeviceAlarmSensorDescription

    def __init__(  # ruff:ignore[too-many-arguments]
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        sub_device_index: int,
        sub_device_sn: str,
        sub_device_key: str,
        description: JackerySubdeviceAlarmSensorDescription,
    ) -> None:
        """Initialize a subdevice alarm sensor.

        Parameters:
            sub_device_index (int): 1-based index in the subdevice list.
            sub_device_sn (str): Serial number for stable identification.
            sub_device_key (str): Prebuilt stable key for unique ID.
        """
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
        type_name = "Zubehör"
        if dev_type == SUBDEVICE_DEV_TYPE_SMOKE:
            type_name = "Rauchmelder"
        elif dev_type == SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY:
            type_name = "Temperatursensor"
        elif dev_type == SUBDEVICE_DEV_TYPE_WATER_LEAK:
            type_name = "Wassersensor"

        model = item.get(FIELD_MODEL) or item.get(FIELD_TYPE_NAME) or "Jackery Zubehör"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{item_key}")},
            manufacturer=MANUFACTURER,
            name=f"{base_name} {type_name} {index}",
            model=str(model),
            serial_number=self._sub_device_sn,
            via_device=(DOMAIN, self._device_id),
        )


class JackeryMeterHeadSensor(JackeryEntity, SensorEntity):
    """Disabled-by-default diagnostic sensor for one meter-head entry."""

    entity_description: JackeryMeterHeadSensorDescription

    def __init__(  # ruff:ignore[too-many-arguments]  # entity constructor takes distinct meter-head identity fields
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        meter_head_index: int,
        meter_head_sn: str,
        meter_head_key: str,
        description: JackeryMeterHeadSensorDescription,
    ) -> None:
        """Initialize one diagnostic meter-head sensor."""
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
        """Return the meter-head entry corresponding to this entity's configured index.

        Returns:
            dict: The meter-head dictionary from payload's `PAYLOAD_METER_HEADS` at
            `self._meter_head_index` (1-based) when present and valid; otherwise an
            empty dict.
        """  # ruff:ignore[property-docstring-starts-with-verb]
        expected_sn = getattr(self, "_meter_head_sn", None)
        meter_heads = sorted_meter_heads(self._payload.get(PAYLOAD_METER_HEADS))
        for meter_head in meter_heads:
            if expected_sn is not None and meter_head_serial(meter_head) == expected_sn:
                return meter_head
        index = getattr(self, "_meter_head_index", 0)
        if 1 <= index <= len(meter_heads):
            return meter_heads[index - 1]
        return {}

    @property
    def native_value(self) -> Any:  # ruff:ignore[any-type]  # dynamically computed HA sensor state value
        """Provide the current value for this meter-head sensor.

        Returns:
            The transformed value of the meter head's configured field, or `None` if the
            field is absent.
        """
        raw = self._meter_head.get(self.entity_description.field)
        if raw is None:
            return None
        return self.entity_description.transform(raw)

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
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{stable_key}")},
            manufacturer=manufacturer_brand or MANUFACTURER,
            name=f"{base_name} {display_name}",
            model=str(model),
            serial_number=str(sn) if sn else None,
            sw_version=str(version) if version else None,
            via_device=(DOMAIN, self._device_id),
        )

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
        self._cached_native_value: Any = None
        self._cached_attrs: dict[str, Any] = {}

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

    def _value_from_ct(self, ct: dict[str, Any]) -> Any:  # ruff:ignore[any-type]  # dynamically computed HA sensor state value
        """Calculate the current value from a CT payload."""
        return _smart_meter_description_value(ct, self.entity_description)

    def _attrs_from_ct(self, ct: dict[str, Any]) -> dict[str, Any]:
        """Build diagnostic attributes from a CT (smart‑meter) payload.

        Returns a dictionary of diagnostic attributes derived from the provided CT
        payload. Possible keys:
        - "calculation": calculation mode when the entity description specifies a derived calculation.
        - "source": origin of the reported value (e.g., "total_fields", "phase_fields", "total_field", "phase_sum", "raw_field").
        - "phase_a_signed_power", "phase_b_signed_power", "phase_c_signed_power": signed per‑phase powers (positive = grid import, negative = grid export) when available.
        - "signed_phase_convention": string describing the sign convention for signed phase powers.
        - Any keys from CT_ATTRIBUTE_FIELDS that are present in the CT payload are copied through.
        - For the "power" entity: "phase_sum_power" and/or "total_field_power" when those computed directional sums are available.

        Returns:
            dict[str, Any]: Mapping of diagnostic attribute names to their values (may
            be empty if no diagnostics are available).
        """  # ruff:ignore[ambiguous-unicode-character-docstring]
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
        if phases is not None:
            attrs["phase_a_signed_power"] = phases[0]
            attrs["phase_b_signed_power"] = phases[1]
            attrs["phase_c_signed_power"] = phases[2]
            attrs["signed_phase_convention"] = (
                "positive=grid_import, negative=grid_export"
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

    def _refresh_cache(self) -> None:
        """Recompute state and attributes once per coordinator update."""
        ct = self._payload.get(PAYLOAD_CT_METER) or {}
        if not isinstance(ct, dict):
            self._cached_native_value = _guard_total_increasing_jitter(
                self._cached_native_value,
                None,
                self.entity_description,
            )
            self._cached_attrs = {}
            return
        self._cached_native_value = _guard_total_increasing_jitter(
            self._cached_native_value,
            self._value_from_ct(ct),
            self.entity_description,
        )
        self._cached_attrs = self._attrs_from_ct(ct)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh cached Smart-Meter values before HA writes the new state."""
        self._refresh_cache()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        """Prime the cache before CoordinatorEntity writes the initial state."""
        self._refresh_cache()
        await super().async_added_to_hass()

    @property
    def native_value(self) -> Any:  # ruff:ignore[any-type]  # dynamically computed HA sensor state value
        """Return the entity's current value."""  # ruff:ignore[property-docstring-starts-with-verb]
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
        sn = first_nonblank_text(
            ct.get(FIELD_DEVICE_SN),
            ct.get(FIELD_SN),
            ct.get(FIELD_MAC),
        )
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
        """Diagnostic attributes for the current state."""
        return self._cached_attrs


class JackeryRawPropertiesSensor(JackeryEntity, SensorEntity):
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
    _attr_state_class = SensorStateClass.MEASUREMENT

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
    """Diagnostic sensor exposing the HTTP API cloud transport health.

    Disabled by default. Shows request counters (total, failed,
    timeouts, auth retries) plus Cloud MQTT birth/retain status
    for a unified view of the cloud transport path.
    """

    _attr_translation_key = "http_api"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unrecorded_attributes = UNRECORDED_ATTRS_HTTP_API

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Create the HTTP API diagnostic entity for the given device."""
        super().__init__(coordinator, device_id, "http_api")

    def _observation(self) -> dict[str, Any]:
        """Retrieve the HTTP API observation record for this device."""
        observations = self.coordinator.http_api_observations()
        return observations if isinstance(observations, dict) else {}

    @property
    def native_value(self) -> int:
        """Total HTTP requests made since HA setup."""
        return int(self._observation().get("requests_total", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Provide HTTP API + Cloud MQTT counters."""
        return dict(self._observation())


class JackeryCloudMqttSensor(JackeryEntity, SensorEntity):
    """Diagnostic sensor exposing the Cloud MQTT push-client health.

    Disabled by default. Tracks message counts, birth/retain
    publishes, connection lifecycle, and TLS configuration.
    """

    _attr_translation_key = "cloud_mqtt"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unrecorded_attributes = UNRECORDED_ATTRS_CLOUD_MQTT

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Create the Cloud MQTT diagnostic entity for the given device."""
        super().__init__(coordinator, device_id, "cloud_mqtt")

    def _observation(self) -> dict[str, Any]:
        """Retrieve the Cloud MQTT observation record for this device."""
        observations = self.coordinator.cloud_mqtt_observations()
        return observations if isinstance(observations, dict) else {}

    @property
    def native_value(self) -> int:
        """Total MQTT messages received since HA setup."""
        return int(self._observation().get("messages_seen", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Provide Cloud MQTT counters and connection metadata."""
        return dict(self._observation())


class JackeryLocalMqttSensor(JackeryEntity, SensorEntity):
    """Diagnostic sensor exposing the Third-Party Local MQTT listener health.

    Disabled by default. Surfaces message counters, routing
    warnings, and connection lifecycle for the local broker bridge.
    """

    _attr_translation_key = "local_mqtt"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unrecorded_attributes = UNRECORDED_ATTRS_LOCAL_MQTT

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Create the Local MQTT diagnostic entity for the given device."""
        super().__init__(coordinator, device_id, "local_mqtt")

    def _observation(self) -> dict[str, Any]:
        """Retrieve the Local MQTT observation record for this device."""
        observations = self.coordinator.local_mqtt_observations()
        return observations if isinstance(observations, dict) else {}

    @property
    def native_value(self) -> int:
        """Jackery messages forwarded to the router since HA setup.

        Foreign broker traffic swept up by a shared topic filter is counted
        in the ``messages_received`` / ``messages_ignored_foreign`` attributes
        but must not inflate the layer's headline counter.
        """
        return int(self._observation().get("messages_forwarded", 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Provide Local MQTT counters and routing diagnostics."""
        attrs = dict(self._observation())
        attrs.pop("enabled", None)
        return attrs


class JackeryDeviceActivationSensor(JackeryEntity, SensorEntity):
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

    @property
    def native_value(self) -> int | None:
        """Return the cloud-activation state (0 = not activated, 1 = active)."""  # ruff:ignore[property-docstring-starts-with-verb]
        device = (
            (self.coordinator.data or {})
            .get(self._device_id, {})
            .get(PAYLOAD_DEVICE, {})
        )
        return safe_int(device.get("activated"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Provide isCloud and raw device payload for diagnostics."""
        device = (
            (self.coordinator.data or {})
            .get(self._device_id, {})
            .get(PAYLOAD_DEVICE, {})
        )
        return {
            "is_cloud": device.get("isCloud"),
            "activated": device.get("activated"),
            "online_status": device.get("onlineStatus"),
            "device_sn": device.get("sn"),
        }


class JackeryWeatherPlanSensor(JackeryEntity, SensorEntity):
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
    """Net complete battery-stack power from the main-device stack bus."""

    _attr_translation_key = "battery_stack_net_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT

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
            "source": "http_primary_main_device_stack_bus",
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
    """Net grid-side power: positive = input, negative = output."""

    _attr_translation_key = "grid_net_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT

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
            "source": "http_primary_grid_side_fields_only_no_inverter_fallback",
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

    _attr_translation_key = "home_consumption_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT

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
        """The entity's current value."""
        ct = self._payload.get(PAYLOAD_CT_METER) or {}
        if not isinstance(ct, dict):
            ct = {}
        result = self._home_consumption_power(ct, self._properties)
        if result is None:
            return None
        return round(result.value, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the current state."""
        ct = self._payload.get(PAYLOAD_CT_METER) or {}
        props = self._properties
        attrs: dict[str, Any] = {
            "formula": (
                "otherLoadPw if available, otherwise "
                "max(smart_meter_net_power - jackery_grid_side_input_power "
                "+ jackery_grid_side_output_power, 0)"
            ),
            "source": "http_primary_otherLoadPw_preferred_then_smart_meter_ct_plus_jackery_ac_grid_side_fields",
            "scope": (
                "Jackery-corrected home load; external non-Jackery generation"
                " must be measured separately"
            ),
        }
        if not isinstance(ct, dict):
            ct = {}

        result = self._home_consumption_power(ct, props)
        meter_net = JackerySmartMeterSensor._net_power(ct)  # ruff:ignore[private-member-access]  # reuse of sibling sensor's classmethod net-power helper (same module)
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
        return attrs


# ---------------------------------------------------------------------------
# Alarm sensor
# ---------------------------------------------------------------------------
class JackeryAlarmSensor(JackeryEntity, SensorEntity):
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
    def native_value(self) -> int:
        """The entity's current value."""
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
        """Convert a millisecond UTC timestamp from the entity's device metadata into a
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
        except TypeError, ValueError, OSError:
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
    def native_value(self) -> str | None:
        """The entity's current value."""
        return _system_meta_scalar_value(self._system.get(self._source_key))


# ---------------------------------------------------------------------------
# Firmware + location
# ---------------------------------------------------------------------------
class JackeryFirmwareSensor(JackeryEntity, SensorEntity):
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
        self._attr_native_unit_of_measurement = "°"

    @property
    def native_value(self) -> float | None:
        """The entity's current value."""
        return safe_float(self._location.get(self._axis))
