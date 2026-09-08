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

The exact live and period field mappings are declared once in the typed
description registries imported below. ``docs/PROTOCOL.md`` remains the
protocol source; duplicating its wide tables here previously drifted from the
executable mappings.

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
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import logging
from math import isfinite, sqrt
from operator import itemgetter
from typing import TYPE_CHECKING, Any, Final, Literal, cast
from weakref import WeakKeyDictionary

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import DEGREE, EntityCategory, UnitOfEnergy, UnitOfPower
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
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
    APP_SECTION_CT_STAT,
    APP_SECTION_TODAY_ENERGY,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_TODAY_LOAD,
    APP_STAT_TOTAL_REVENUE,
    APP_STAT_UNIT,
    APP_TODAY_ENERGY_SOURCE_META,
    APP_TOTAL_GUARD_META,
    APP_UNIT_KWH,
    APP_YEAR_BACKFILL_META,
    CALCULATED_POWER_SENSOR_SUFFIXES,
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    CT_ATTRIBUTE_FIELDS,
    CT_LIVE_ENERGY_UNITS_PER_KWH,
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
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_DISCHARGING_ENERGY,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_EC,
    FIELD_GRID_IN_PW,
    FIELD_GRID_OUT_PW,
    FIELD_GRID_STANDARD,
    FIELD_IDX,
    FIELD_IN_EGY,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_IN_ONGRID_PW,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IS_FIRMWARE_UPGRADE,
    FIELD_IT,
    FIELD_LINK_TYPE,
    FIELD_MAC,
    FIELD_MINS_INTERVAL,
    FIELD_MODEL,
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
    FIELD_SYS_SWITCH,
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
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_BATTERY_TRENDS,
    PAYLOAD_CIRCUIT_PROPERTY,
    PAYLOAD_CT_METER,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_LOCAL_DAILY_ENERGY,
    PAYLOAD_METER_HEADS,
    PAYLOAD_PRICE,
    PAYLOAD_PV_TRENDS,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_STATISTIC,
    PAYLOAD_SUBDEVICES,
    PAYLOAD_SYSTEM,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_VERIFIED_DAY_STATISTICS,
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
from .descriptions import (
    BATTERY_PACK_SENSOR_DESCRIPTIONS,
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
from .entity import JackeryEntity, payload_properties_for_sources
from .util import (
    app_energy_unit_scale,
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
    is_portable_payload as _is_portable_payload,
    jackery_corrected_home_consumption_power,
    jackery_grid_net_power,
    jackery_grid_side_input_power,
    jackery_grid_side_output_power,
    jackery_inverter_ac_input_power,
    jackery_inverter_ac_output_power,
    meter_head_serial,
    nonblank_text,
    normalize_mac_address,
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

    from homeassistant.components.sensor import SensorEntityDescription
    from homeassistant.const import StateType
    from homeassistant.core import HomeAssistant
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
    "device_today_pv_to_battery": APP_DEVICE_STAT_PV_TO_BATTERY,
    "device_today_battery_to_ongrid": APP_DEVICE_STAT_BATTERY_TO_GRID,
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


def _guard_total_increasing_jitter(
    previous: StateType,
    current: StateType,
    description: SensorEntityDescription,
) -> StateType:
    """Hold lifetime energy counter regressions until a new entity is created."""
    if (
        description.device_class != SensorDeviceClass.ENERGY
        or description.state_class != SensorStateClass.TOTAL_INCREASING
    ):
        return current
    previous_number = safe_float(previous)
    current_number = safe_float(current)
    if previous_number is None:
        return current
    if current_number is None or current_number < previous_number:
        return previous
    return current


async def _async_restored_lifetime_energy_value(
    entity: RestoreSensor,
    expected_unit: str | None,
) -> float | None:
    """Return a validated native lifetime-energy value from HA storage."""
    stored = await entity.async_get_last_sensor_data()
    if stored is None or isinstance(stored.native_value, bool):
        return None
    native_value = stored.native_value
    if not isinstance(native_value, (int, float, str)):
        return None
    try:
        value = float(native_value)
    except TypeError, ValueError, OverflowError:
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


def _identity[T](value: T) -> T:
    return value


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


def _no_property_value(_props: dict[str, Any]) -> None:
    """Marker getter for sensors whose value lives outside PAYLOAD_PROPERTIES."""
    return


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


def _sensor_description_has_value(
    payload: dict[str, Any],
    description: JackerySensorDescription,
) -> bool:
    """The True when a property sensor can produce a value from payload.

    Gating happens on ``app_fields`` — the app-side payload keys a description
    reads. A description without that metadata cannot be gated here; it is
    registered and ``value_fn`` decides at runtime.
    """
    app_fields = description.app_fields
    if not app_fields:
        return True
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
    raw = pack.get(field)
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
    if raw is None and field == FIELD_UPDATE_STATUS:
        raw = pack.get(FIELD_IS_FIRMWARE_UPGRADE)
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


def _smart_meter_description_value(
    ct: dict[str, Any],
    description: JackerySmartMeterSensorDescription,
) -> StateType:
    """The one calculable Smart-Meter value from the current CT payload."""
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
    """The today's value from a week/month/year app chart payload."""
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


def _chart_sum_for_date_range(
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    start: date,
    end: date,
) -> float | None:
    """Sum explicit kWh chart buckets for one fully covered date range."""
    unit_scale = app_energy_unit_scale(source)
    if end < start or unit_scale is None:
        return None
    request_begin = _request_date(
        source,
        APP_REQUEST_BEGIN_DATE,
        APP_REQUEST_BEGIN_DATE_ALT,
    )
    request_end = _request_date(
        source,
        APP_REQUEST_END_DATE,
        APP_REQUEST_END_DATE_ALT,
    )
    if (
        request_begin is None
        or request_begin > start
        or (request_end is not None and request_end < end)
    ):
        return None
    series_key = _trend_series_key(section, stat_key)
    raw_series = source.get(series_key) if series_key is not None else None
    if not isinstance(raw_series, list) or not raw_series:
        return None
    first = (start - request_begin).days
    last = (end - request_begin).days
    if first < 0 or last >= len(raw_series):
        return None
    values: list[float] = []
    for raw_value in raw_series[first : last + 1]:
        value = safe_float(raw_value)
        if value is None or value < 0:
            return None
        values.append(value * unit_scale)
    return round(sum(values), 5)


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
        return False
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
            and fallback_source.get(stat_key) is not None
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
        source.get(description.stat_key) is not None
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
        if self.entity_description.key == "savings_price" and isinstance(value, float):
            return round(value, SAVINGS_PRICE_PRECISION)
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


def _sensor_registration_eligibility(
    coordinator: JackerySolarVaultCoordinator,
) -> set[tuple[str, str, str]]:
    """Return value-gated entity keys supported by the latest payload."""
    eligible: set[tuple[str, str, str]] = set()
    for dev_id, payload in (coordinator.data or {}).items():
        props = payload_properties_for_sources(payload)
        is_portable = _is_portable_payload(payload, props)
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
                if _sensor_description_has_value(payload, property_description):
                    eligible.add((dev_id, "property", property_description.key))
        for stat_description in STAT_DESCRIPTIONS:
            if _stat_description_has_value(payload, stat_description):
                eligible.add((dev_id, "stat", stat_description.key))
        for meter_description in SMART_METER_SENSOR_DESCRIPTIONS:
            if _smart_meter_description_has_value(payload, meter_description):
                eligible.add((dev_id, "smart_meter", meter_description.key))
        ct = payload.get(PAYLOAD_CT_METER)
        if (
            jackery_corrected_home_consumption_power(
                ct if isinstance(ct, dict) else {},
                props,
            )
            is not None
        ):
            eligible.add((dev_id, "derived", "home_consumption_power"))
    return eligible


def _collect_property_entities(
    collection: _SensorCollection,
    dev_id: str,
    payload: dict[str, Any],
    props: dict[str, Any],
    *,
    is_portable: bool,
) -> None:
    """Collect main, portable, and home-mode property sensors."""
    coordinator = collection.coordinator
    for description in SENSOR_DESCRIPTIONS:
        if is_portable and not _sensor_description_has_value(payload, description):
            continue
        collection.add(JackerySensor(coordinator, dev_id, description))
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
        for description in PORTABLE_SENSOR_DESCRIPTIONS:
            collection.add(JackerySensor(coordinator, dev_id, description))
        return
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
    *,
    is_portable: bool,
) -> None:
    """Collect period, calculated, savings, and stable diagnostics."""
    coordinator = collection.coordinator
    for stat_description in STAT_DESCRIPTIONS:
        if (
            is_portable or stat_description.key.startswith("symmetry_")
        ) and not _stat_description_has_value(payload, stat_description):
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


def _collect_battery_packs(
    collection: _SensorCollection,
    dev_id: str,
    payload: dict[str, Any],
    props: dict[str, Any],
) -> None:
    """Collect add-on battery sensors with session-frozen identities."""
    packs = payload.get(PAYLOAD_BATTERY_PACKS)
    valid_packs = (
        [pack for pack in packs if isinstance(pack, dict)]
        if isinstance(packs, list)
        else []
    )
    discovered = sorted_battery_pack_payloads(
        subdevice_accessories(payload, dev_type=SUBDEVICE_DEV_TYPE_BATTERY_PACK)
    )
    registration_packs = valid_packs or discovered
    bat_num = safe_int(props.get(FIELD_BAT_NUM))
    count = (
        min(5, len(registration_packs))
        if bat_num is None
        else min(5, max(len(registration_packs), 0, bat_num))
    )
    coordinator = collection.coordinator
    for index in range(1, count + 1):
        identity_key = (dev_id, index)
        identity = collection.battery_pack_identities.get(identity_key)
        if identity is None:
            serial = (
                battery_pack_serial(registration_packs[index - 1])
                if index <= len(registration_packs)
                else None
            ) or coordinator.battery_pack_identity_serial(dev_id, index)
            coordinator.set_battery_pack_identity_override(dev_id, index, serial)
            stable_key = stable_subdevice_key("battery_pack", serial, index)
            identity = collection.battery_pack_identities[identity_key] = (
                serial,
                stable_key,
            )
        serial, stable_key = identity
        for description in BATTERY_PACK_SENSOR_DESCRIPTIONS:
            if description.field == FIELD_CELL_TEMP and not any(
                FIELD_CELL_TEMP in item for item in valid_packs
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
    for description in SMART_METER_SENSOR_DESCRIPTIONS:
        if description.calculation and not collection.create_smart_meter_derived:
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
        props = payload_properties_for_sources(payload)
        is_portable = _is_portable_payload(payload, props)
        _collect_property_entities(
            collection,
            dev_id,
            payload,
            props,
            is_portable=is_portable,
        )
        _collect_stat_and_diagnostics(
            collection,
            dev_id,
            payload,
            is_portable=is_portable,
        )
        _collect_battery_packs(collection, dev_id, payload, props)
        _collect_smart_plugs(collection, dev_id, payload)
        _collect_meter_heads(collection, dev_id, payload)
        _collect_breakers(collection, dev_id, payload)
        _collect_subdevice_alarms(collection, dev_id, payload)
        _collect_smart_meter_entities(collection, dev_id, payload)
    return collection.entities


async def async_setup_entry(  # ruff:ignore[unused-async]
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
        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
            and description.entity_category != EntityCategory.DIAGNOSTIC
        )
        self._cached_native_value: StateType = None
        self._cached_attrs: dict[str, Any] = {}
        self._cache_refresh_active = False

    def _refresh_cache(self) -> None:
        """Prepare one coherent value-and-attributes state-write snapshot."""
        self._cached_native_value = self.entity_description.value_fn(self)
        self._cached_attrs = self._source_attributes()

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
        return self.entity_description.value_fn(self)

    def _source_attributes(self) -> dict[str, Any]:
        """Build source diagnostics from the same coordinator snapshot as the value."""
        getter = self.entity_description.getter
        if getter is not None:
            merged_raw = getter(self._merged_properties)
            http_raw = getter(self._http_properties or {})
            attrs: dict[str, Any] = {
                "merged_raw_value": merged_raw,
                "http_raw_value": http_raw,
            }
            if merged_raw is not None and merged_raw != http_raw:
                attrs["live_source_overrides_http"] = True
            return attrs
        try:
            return {"merged_raw_value": self.entity_description.value_fn(self)}
        except Exception:
            _LOGGER.exception(
                "Unable to calculate merged diagnostic value for %s",
                self.entity_id,
            )
            return {}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose source diagnostics from the prepared state-write snapshot."""
        if self._cache_refresh_active:
            return self._cached_attrs
        return self._source_attributes()


_PeriodResolution = tuple[list[float | None] | None, float | None, float | None]
_PeriodResolutionCache = dict[tuple[int, str, str], _PeriodResolution]


@dataclass(frozen=True, slots=True)
class _StatRefreshContext:
    """Immutable event-loop snapshot consumed by the executor batch."""

    payload: dict[str, Any]
    local_now: datetime
    local_today: date
    local_daily_raw: tuple[float, str] | None
    local_period_raw: tuple[float, str] | None
    local_timezone: tzinfo = dt_util.DEFAULT_TIME_ZONE


@dataclass(frozen=True, slots=True)
class _StatCacheSnapshot:
    """Computed stat state applied atomically back on the event loop."""

    native_value: StateType
    attrs: dict[str, Any]
    source_section: str
    last_reset: datetime | None = None


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


class JackeryStatSensor(JackeryEntity, RestoreSensor):
    """Sensor sourced from the statistic / price section of the payload."""

    def _non_negative_period_raw(self, raw: StateType) -> StateType:
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
        self._cache_refresh_active = False
        self._cache_initializing = False
        self._restored_lifetime_value: float | None = None

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
        timezone = dt_util.get_time_zone(self.hass.config.time_zone)
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

    def _is_period_data_stale(
        self,
        source_section: str | None = None,
        payload: dict[str, Any] | None = None,
        local_timezone: tzinfo | None = None,
        local_now: datetime | None = None,
    ) -> bool:
        """Implementation details.

        Determine whether the source period data is older than the current local
        period.

        If the sensor has no reset period or the request metadata begin date is missing
        or invalid, the data is treated as fresh.

        Returns:
            `true` if the source period begin date is before the current local period
            start date, `false` otherwise.
        """
        if self._reset_period is None:
            return False
        wall_clock_start = (
            _period_start_at(self._reset_period, local_now)
            if local_now is not None
            else _period_start(
                self._reset_period,
                local_timezone or self._local_timezone(),
            )
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
        local_now: datetime | None = None,
    ) -> bool:
        """Implementation details.

        Determine whether the source period begin date from request metadata is later
        than the current local period start.

        Returns:
            True if the source period begin date is after the local period start for the
            sensor's reset period, False otherwise.
        """
        if self._reset_period is None:
            return False
        wall_clock_start = (
            _period_start_at(self._reset_period, local_now)
            if local_now is not None
            else _period_start(
                self._reset_period,
                local_timezone or self._local_timezone(),
            )
        )
        begin_iso = self._period_begin_from_meta(source_section, payload)
        if begin_iso is None:
            return False
        try:
            data_begin = date.fromisoformat(begin_iso)
        except ValueError:
            return False
        return data_begin > wall_clock_start.date()

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
        return source if isinstance(source, dict) else {}

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
            value = _chart_value_for_day(
                candidate_source,
                candidate_section,
                stat_key,
                today=today,
            )
            if value is not None:
                return value, candidate_section, candidate_source
        return None

    def _current_open_week_from_month_chart(  # ruff: ignore[too-many-locals, too-many-arguments] - period and current-day evidence use the same explicit clock
        self,
        section: str,
        stat_key: str,
        *,
        payload: dict[str, Any],
        today: date,
        local_daily_raw: tuple[float, str] | None,
        now: datetime | None = None,
    ) -> tuple[float, str, dict[str, Any]] | None:
        """Build the current open week from corroborated daily kWh buckets.

        Jackery can return a positive but stale week total, not only an empty
        placeholder.  Cross-check the week and month views day by day, use a
        verified historical day where available, and let the same-metric local
        lifetime delta replace only today's lagging bucket.  A zero needs two
        distinct period/day observations before it becomes entity data.
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
        local_value = local_daily_raw[0] if local_daily_raw is not None else None
        verified_days = self._source_for_section(
            PAYLOAD_VERIFIED_DAY_STATISTICS,
            payload,
        )
        prefix = section[: -len(suffix)]
        for day_section in (prefix, f"{prefix}_{DATE_TYPE_DAY}"):
            day_source = self._source_for_section(day_section, payload)
            points = day_power_energy_points(
                day_source,
                day_section,
                stat_key,
                bucket_minutes=60,
                today=today,
                now=now,
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
            if points:
                measured = round(sum(point.value for point in points), 5)
                local_value = (
                    measured if local_value is None else max(local_value, measured)
                )
                break
        total = 0.0
        day = week_start
        while day <= today:
            week_value = _chart_value_for_day(
                week_source,
                section,
                stat_key,
                today=day,
            )
            month_value = _chart_value_for_day(
                month_source,
                month_section,
                stat_key,
                today=day,
            )
            day_sources = verified_days.get(day.isoformat())
            prefix_totals = (
                day_sources.get(prefix) if isinstance(day_sources, dict) else None
            )
            verified_value = (
                safe_float(prefix_totals.get(stat_key))
                if isinstance(prefix_totals, dict)
                else None
            )
            observations = [
                value
                for value in (week_value, month_value, verified_value)
                if value is not None and value >= 0
            ]
            if day == today and local_value is not None and local_value >= 0:
                observations.append(local_value)
            if not observations:
                return None
            positive_observations = [value for value in observations if value > 0]
            if positive_observations:
                value = max(positive_observations)
            elif len(observations) >= _MIN_ZERO_CORROBORATION_SOURCES:
                value = 0.0
            else:
                # One zero from one request is still Jackery's known no-data
                # placeholder shape, not proof of a genuine zero-energy day.
                return None
            total += value
            day += timedelta(days=1)

        total = round(total, 5)
        return total, section, week_source

    def _open_month_total_with_local_day(
        self,
        section: str,
        stat_key: str,
        *,
        payload: dict[str, Any],
        today: date,
        local_value: float,
    ) -> tuple[float, dict[str, Any]] | None:
        """Replace only today's month-chart bucket with a newer local total."""
        month_source = self._source_for_section(section, payload)
        month_total = _chart_sum_for_date_range(
            month_source,
            section,
            stat_key,
            start=today.replace(day=1),
            end=today,
        )
        day_bucket = _chart_value_for_day(
            month_source,
            section,
            stat_key,
            today=today,
        )
        if month_total is None or day_bucket is None:
            return None
        reconciled = round(
            month_total - day_bucket + max(day_bucket, local_value),
            5,
        )
        return reconciled, month_source

    def _current_open_month_with_local_day(
        self,
        section: str,
        stat_key: str,
        *,
        context: _StatRefreshContext,
        local_value: float,
        cloud_total: float,
    ) -> tuple[float, str, dict[str, Any], str] | None:
        """Reconcile the open month bucket with today's local total."""
        suffix = f"_{DATE_TYPE_MONTH}"
        if not section.endswith(suffix):
            return None
        month = self._open_month_total_with_local_day(
            section,
            stat_key,
            payload=context.payload,
            today=context.local_today,
            local_value=local_value,
        )
        if month is None:
            return None
        reconciled, month_source = month
        return (
            max(cloud_total, reconciled),
            section,
            month_source,
            "current_open_month_with_local_day",
        )

    def _current_open_year_with_local_day(
        self,
        section: str,
        stat_key: str,
        *,
        context: _StatRefreshContext,
        local_value: float,
        cloud_total: float,
    ) -> tuple[float, str, dict[str, Any], str] | None:
        """Reconcile the open year bucket with the current local month."""
        suffix = f"_{DATE_TYPE_YEAR}"
        if not section.endswith(suffix):
            return None
        prefix = section[: -len(suffix)]
        month_section = f"{prefix}_{DATE_TYPE_MONTH}"
        month = self._open_month_total_with_local_day(
            month_section,
            stat_key,
            payload=context.payload,
            today=context.local_today,
            local_value=local_value,
        )
        year_source = self._source_for_section(section, context.payload)
        year_values = effective_trend_series_values(year_source, section, stat_key)
        unit_scale = app_energy_unit_scale(year_source)
        month_index = context.local_today.month - 1
        if (
            month is None
            or not isinstance(year_values, list)
            or month_index >= len(year_values)
            or unit_scale is None
        ):
            return None
        year_month_bucket = safe_float(year_values[month_index])
        if year_month_bucket is None or year_month_bucket < 0:
            return None
        year_month_bucket *= unit_scale
        reconciled_month, _month_source = month
        reconciled = round(
            cloud_total - year_month_bucket + max(year_month_bucket, reconciled_month),
            5,
        )
        return (
            max(cloud_total, reconciled),
            section,
            year_source,
            "current_open_year_with_local_month",
        )

    def _current_open_month_or_year_with_local_day(
        self,
        section: str,
        stat_key: str,
        *,
        context: _StatRefreshContext,
        cloud_total: float | None,
    ) -> tuple[float, str, dict[str, Any], str] | None:
        """Reconcile an open month/year bucket with today's local total."""
        local_daily_raw = context.local_daily_raw
        if local_daily_raw is None or cloud_total is None or local_daily_raw[0] < 0:
            return None
        if self._reset_period == DATE_TYPE_MONTH:
            return self._current_open_month_with_local_day(
                section,
                stat_key,
                context=context,
                local_value=local_daily_raw[0],
                cloud_total=cloud_total,
            )
        if self._reset_period == DATE_TYPE_YEAR:
            return self._current_open_year_with_local_day(
                section,
                stat_key,
                context=context,
                local_value=local_daily_raw[0],
                cloud_total=cloud_total,
            )
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
        scalar_total = safe_float(source.get(stat_key))
        server_total = scalar_total
        if is_device_year_period_section(source, section) and values is not None:
            server_total = chart_series_sum
            if (
                (chart_series_sum is None or chart_series_sum == 0)
                and scalar_total is not None
                and scalar_total > 0
            ):
                server_total = scalar_total
        resolution = values, chart_series_sum, server_total
        period_cache[cache_key] = resolution
        return resolution

    def _pv_revenue_day_snapshot(
        self,
        context: _StatRefreshContext,
    ) -> _StatCacheSnapshot | None:
        """Derive current-day revenue from observed local PV and single tariff."""
        description = self.entity_description
        source = self._source_for_section(description.section, context.payload)
        cloud_revenue = safe_float(source.get(description.stat_key))
        if cloud_revenue is not None and cloud_revenue > 0:
            return None

        local = self._source_for_section(
            PAYLOAD_LOCAL_DAILY_ENERGY,
            context.payload,
        )
        local_units = safe_float(local.get(APP_DEVICE_STAT_PV_ENERGY))
        attrs: dict[str, Any] = {
            "source_section": description.section,
            "source_key": description.stat_key,
        }
        if local_units is None or local_units < 0:
            return _StatCacheSnapshot(
                (
                    cloud_revenue
                    if cloud_revenue is not None and cloud_revenue >= 0
                    else None
                ),
                attrs,
                description.section,
                self.last_reset,
            )
        if local_units == 0:
            attrs["fallback"] = "derived_observed_zero_revenue"
            return _StatCacheSnapshot(
                0.0,
                attrs,
                description.section,
                self.last_reset,
            )

        energy_kwh = round(
            local_units / JACKERY_LIVE_ENERGY_UNITS_PER_KWH,
            5,
        )
        price = self._source_for_section(PAYLOAD_PRICE, context.payload)
        price_mode = safe_int(price.get(FIELD_DYNAMIC_OR_SINGLE))
        single_price = safe_float(price.get(FIELD_SINGLE_PRICE))
        if (
            price_mode != _PRICE_MODE_SINGLE
            or single_price is None
            or single_price <= 0
        ):
            return _StatCacheSnapshot(
                None,
                attrs,
                description.section,
                self.last_reset,
            )

        currency = first_nonblank_text(
            price.get(FIELD_SINGLE_CURRENCY),
            price.get(FIELD_CURRENCY),
        )
        attrs = {
            "source_section": PAYLOAD_PRICE,
            "source_key": description.stat_key,
            "fallback": "derived_single_tariff_revenue",
            "revenue_derivation": {
                "energy_kwh": energy_kwh,
                "energy_source": PAYLOAD_LOCAL_DAILY_ENERGY,
                "price_per_kwh": single_price,
                "price_source": f"{PAYLOAD_PRICE}.{FIELD_SINGLE_PRICE}",
                "currency": currency,
            },
        }
        return _StatCacheSnapshot(
            round(energy_kwh * single_price, 2),
            attrs,
            PAYLOAD_PRICE,
            self.last_reset,
        )

    def _compact_today_snapshot(  # ruff:ignore[too-many-locals]
        self,
        context: _StatRefreshContext,
    ) -> _StatCacheSnapshot:
        """Resolve one reconciled compact-today value with source provenance."""
        description = self.entity_description
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
                if valid_provenance is not None
                else compact_source
            )
            observations.append((
                source_section,
                source_key,
                compact_value,
                observation_source,
            ))

        for fallback_section, fallback_key in description.fallback_sources:
            fallback_source = self._source_for_section(
                fallback_section,
                context.payload,
            )
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
                },
                APP_SECTION_TODAY_ENERGY,
                self.last_reset,
            )

        source_section, source_key, raw, selected_source = selected
        attrs: dict[str, Any] = {
            "source_section": source_section,
            "source_key": source_key,
        }
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
                    (observation for observation in zeroes if observation[3]), None
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
        if (
            self.entity_description.key == "pv_revenue_day"
            and (snapshot := self._pv_revenue_day_snapshot(context)) is not None
        ):
            return snapshot
        if section == APP_SECTION_TODAY_ENERGY and self._reset_period == DATE_TYPE_DAY:
            return self._compact_today_snapshot(context)
        if self.entity_description.key in {
            "device_today_ongrid_to_battery",
            "device_today_pv_to_battery",
            "device_today_battery_to_ongrid",
        }:
            return self._local_flow_snapshot(context)
        if _trend_series_key(section, stat_key) is not None:
            return self._refresh_period_cache(context, period_cache)
        return self._refresh_non_period_cache(context)

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
        values, chart_sum, server_total = self._resolve_period_value(
            source,
            section,
            stat_key,
            period_cache,
        )
        raw = server_total if is_day_period_payload(source, section) else chart_sum
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
        if local_daily is None:
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
        if state.raw is not None and not empty_ct_zero:
            return
        for section, stat_key in self.entity_description.fallback_sources:
            source = self._source_for_section(section, context.payload)
            values, chart_sum, server_total = self._resolve_period_value(
                source,
                section,
                stat_key,
                period_cache,
            )
            total = server_total
            if total is None and not is_day_period_payload(source, section):
                total = chart_sum
            if total == 0:
                state.period_zero_sources.add(section)
                if len(state.period_zero_sources) < _MIN_ZERO_CORROBORATION_SOURCES:
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
            return

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
            value, section, source = week
            current = safe_float(state.raw)
            if current is None or value > current:
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
        )
        if period is None:
            return
        value, section, source, fallback = period
        current = safe_float(state.raw)
        if current is None or value > current:
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
        if self._reset_period == DATE_TYPE_DAY or context.local_period_raw is None:
            return
        local_value, local_metric = context.local_period_raw
        current = safe_float(state.raw)
        if current is not None and current > 0 and local_value <= current:
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
            and self._is_period_data_stale(
                state.cached_source_section,
                context.payload,
                context.local_timezone,
                context.local_now,
            )
        )
        future = bool(
            not state.day_bucket_fallback
            and self._reset_period
            and self._is_period_data_future(
                state.cached_source_section,
                context.payload,
                context.local_timezone,
                context.local_now,
            )
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
        }
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
        if (
            native_value is not None
            and isinstance(state.values, list)
            and len(state.values) <= _MAX_PERIOD_VALUES
        ):
            attrs["period_values"] = state.values
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
            {key: value for key, value in attrs.items() if value is not None},
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
        raw = source.get(stat_key)
        if raw is None:
            for (
                fallback_section,
                fallback_key,
            ) in self.entity_description.fallback_sources:
                fallback_source = self._source_for_section(
                    fallback_section,
                    context.payload,
                )
                raw = fallback_source.get(fallback_key)
                if raw is not None:
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
            state.stale = self._is_period_data_stale(
                state.cached_source_section,
                context.payload,
                context.local_timezone,
            )
            state.future = self._is_period_data_future(
                state.cached_source_section,
                context.payload,
                context.local_timezone,
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
        )

    @callback
    def _apply_cache_snapshot(self, snapshot: _StatCacheSnapshot) -> None:
        """Apply a completed snapshot atomically on the event loop."""
        candidate = snapshot.native_value
        attrs = snapshot.attrs
        restored_lifetime_value = getattr(self, "_restored_lifetime_value", None)
        if candidate is None and restored_lifetime_value is not None:
            candidate = restored_lifetime_value
            attrs = {
                **attrs,
                "restored": True,
                "restore_reason": "lifetime_counter_source_unavailable",
            }
        guarded = _guard_total_increasing_jitter(
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
        if (
            self.entity_description.key in _RESTORABLE_LIFETIME_STAT_SENSOR_KEYS
            and self._cached_native_value is None
        ):
            self._restored_lifetime_value = await _async_restored_lifetime_energy_value(
                self,
                self.entity_description.native_unit_of_measurement,
            )
        # EntityPlatform adds entities sequentially. Waiting here would drain one
        # executor job per entity and block platform setup. Queue without waiting so
        # the batch's initial event-loop yield can collect every statistic entity.
        batch.request(self, write_state=True)

    async def async_will_remove_from_hass(self) -> None:
        """Invalidate queued work before the entity leaves Home Assistant."""
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
        if self.entity_description.device_class != SensorDeviceClass.MONETARY:
            return self.entity_description.native_unit_of_measurement
        source = self._source_for_section(self._cached_source_section)
        currency = source.get(FIELD_CURRENCY)
        if isinstance(currency, str) and currency.strip():
            return currency
        return self.entity_description.native_unit_of_measurement

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
            local_daily = self._payload.get(PAYLOAD_LOCAL_DAILY_ENERGY)
            raw = (
                safe_float(local_daily.get(metric_key))
                if isinstance(local_daily, dict)
                else None
            )
            if raw is None:
                return None
            divisor = (
                CT_LIVE_ENERGY_UNITS_PER_KWH
                if metric_key
                in {
                    FIELD_CT_TOTAL_PHASE_ENERGY,
                    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
                }
                else JACKERY_LIVE_ENERGY_UNITS_PER_KWH
            )
            value = round(raw / divisor, 5)
        return value, metric_key

    def _local_period_raw(self, today: date) -> tuple[float, str] | None:
        """Return a fully covered local period delta in kWh for this sensor."""
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
            entity._refresh_availability_cache()  # ruff:ignore[private-member-access]
            if request.write_state:
                entity._write_cached_state()  # ruff:ignore[private-member-access]
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
            entity._refresh_availability_cache()  # ruff:ignore[private-member-access]
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
        self._restored_lifetime_value: float | None = None

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

    def _value_from_pack(self, pack: dict[str, Any]) -> StateType:
        """Extract and transform this entity's value from a battery-pack payload."""
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
        """Implementation details.

        Refresh the cached native value and extra state attributes from the current
        battery pack.

        This updates self._cached_native_value and self._cached_attrs using the current
        pack snapshot; intended to be run once per coordinator update.
        """
        pack = self._pack
        live_value = self._value_from_pack(pack)
        candidate = live_value
        attrs = self._attrs_from_pack(pack)
        if live_value is None and self._restored_lifetime_value is not None:
            candidate = self._restored_lifetime_value
            attrs = {
                **attrs,
                "restored": True,
                "restore_reason": "lifetime_counter_source_unavailable",
            }
        guarded = _guard_total_increasing_jitter(
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
        """Refresh cached BatteryPackSub values before HA writes state."""
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

    @property
    def native_value(self) -> StateType:
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
            fallback="Jackery battery pack",
        )
        version = first_nonblank_text(
            pack.get(FIELD_VERSION),
            pack.get(FIELD_CURRENT_VERSION),
        )
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{self._pack_key}")},
            manufacturer=MANUFACTURER,
            name=f"{base_name} Battery pack {self._pack_index}",
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
        # Look up by captured serial; cloud-side re-ordering of the plug
        # array must not switch this entity to a different physical plug.
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

    def _refresh_cache(self) -> None:
        """Refresh cached plug state and retain a lifetime total across gaps."""
        plug = self._plug
        live_value = self._value_from_plug(plug)
        candidate = live_value
        attrs = self._attrs_from_plug(plug)
        if live_value is None and self._restored_lifetime_value is not None:
            candidate = self._restored_lifetime_value
            attrs = {
                **attrs,
                "restored": True,
                "restore_reason": "lifetime_counter_source_unavailable",
            }
        guarded = _guard_total_increasing_jitter(
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
        if self._reset_period is None:
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
        for meter_head in meter_heads:
            if expected_sn is not None and meter_head_serial(meter_head) == expected_sn:
                return meter_head
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
        if hass is None:
            return
        registry = dr.async_get(hass)
        device = registry.async_get_device_by_identifier(
            self._smart_meter_identifier(ct),
            self.coordinator.config_entry.entry_id,
        )
        if device is None:
            return
        # The package is already initialized here; defer this import to avoid
        # a module-load cycle while reconciling identities learned after setup.
        from . import _async_migrate_smart_meter_identity  # ruff: ignore[import-outside-top-level]

        entry = self.coordinator.config_entry
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
        ct = self._payload.get(PAYLOAD_CT_METER) or {}
        if not isinstance(ct, dict):
            ct = {}
        self._sync_device_mac_connection(ct)
        live_value = self._value_from_ct(ct)
        candidate = live_value
        attrs = self._attrs_from_ct(ct)
        if live_value is None and self._restored_lifetime_value is not None:
            candidate = self._restored_lifetime_value
            attrs = {
                **attrs,
                "restored": True,
                "restore_reason": "lifetime_counter_source_unavailable",
            }
        guarded = _guard_total_increasing_jitter(
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
    _attr_unrecorded_attributes = UNRECORDED_ATTRS_HTTP_API

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
    """Expose local MQTT diagnostics."""

    _attr_has_entity_name = True
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
# These values are calculated from multiple live fields and may change sign. They
# intentionally keep device_class/unit for normal graphs but do not set
# state_class so Home Assistant does not build long-term statistics metadata for
# entity IDs that historically existed without a compatible recorder unit.
# ---------------------------------------------------------------------------
class JackeryBatteryNetPowerSensor(JackeryEntity, SensorEntity):
    """Expose main-battery net power."""

    _attr_has_entity_name = True
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
    """Expose battery-stack net power."""

    _attr_has_entity_name = True
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
    """Expose signed grid net power."""

    _attr_has_entity_name = True
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
    """Expose derived home-consumption power."""

    _attr_has_entity_name = True
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
            "source": (
                "http_primary_otherLoadPw_preferred_then_smart_meter_ct_plus_"
                "jackery_ac_grid_side_fields"
            ),
            "scope": (
                "Jackery-corrected home load; external non-Jackery generation"
                " must be measured separately"
            ),
        }
        if not isinstance(ct, dict):
            ct = {}

        result = self._home_consumption_power(ct, props)
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
        return attrs


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
        except TypeError, ValueError, OSError:
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
