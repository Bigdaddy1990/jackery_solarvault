"""Sensor descriptions for Jackery SolarVault integration.

All descriptions follow HA-standard ``SensorEntityDescription`` with
``value_fn(entity) -> StateType``.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from homeassistant.components.sensor import (
    SensorDeviceClass,
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

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.helpers.typing import StateType

    from ..entity import JackeryEntity
    from ..sensor import (
        JackeryBatteryPackSensor,
        JackeryBreakerSensor,
        JackeryMeterHeadSensor,
        JackerySavingsDetailSensor,
        JackerySensor as JackerySensorEntity,
        JackerySmartMeterSensor,
        JackerySmartPlugSensor,
        JackeryStatSensor,
        JackerySubdeviceAlarmSensor,
    )

from ..const import (
    APP_DEVICE_STAT_BATTERY_CHARGE,
    APP_DEVICE_STAT_BATTERY_DISCHARGE,
    APP_DEVICE_STAT_BATTERY_TO_GRID,
    APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    APP_DEVICE_STAT_PV_TO_BATTERY,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
    APP_SECTION_SYMMETRY_STAT,
    APP_SECTION_TODAY_ENERGY,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_TODAY_BATTERY_DISCHARGE,
    APP_STAT_TODAY_BATTERY_ENERGY,
    APP_STAT_TODAY_GRID_IMPORT_ENERGY,
    APP_STAT_TODAY_HOME_LOAD_ENERGY,
    APP_STAT_TODAY_LOAD,
    APP_STAT_TODAY_SOLAR_ENERGY,
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
    CT_NEGATIVE_PHASE_POWER_FIELDS,
    CT_POSITIVE_PHASE_POWER_FIELDS,
    CT_TOTAL_POWER_PAIR,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DEFAULT_NULL_SEMANTICS,
    DEFAULT_STORM_WARNING_MINUTES,
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
    FIELD_AUTO_STANDBY,
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
    FIELD_DEFAULT_PW,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
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
    FIELD_GRID_STAT,
    FIELD_GRID_STATE,
    FIELD_GRID_STATE_ALT,
    FIELD_IAC,
    FIELD_IACPW,
    FIELD_IN_EGY,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_IN_ONGRID_PW,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IPAL_PW,
    FIELD_IS_CONTRACT_AUTH,
    FIELD_IS_FOLLOW_METER_PW,
    FIELD_IS_PACK_CONNECT,
    FIELD_IT,
    FIELD_LM,
    FIELD_MAC,
    FIELD_MAX_GRID_STD_PW,
    FIELD_MAX_INV_STD_PW,
    FIELD_MAX_IOT_NUM,
    FIELD_MAX_OUT_PW,
    FIELD_MAX_SYS_IN_PW,
    FIELD_MAX_SYS_OUT_PW,
    FIELD_MINS_INTERVAL,
    FIELD_NEXTDAY_HIGH,
    FIELD_NEXTDAY_LOW,
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
    FIELD_SFC,
    FIELD_SINGLE_PRICE,
    FIELD_SLTB,
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
    FIELD_SW_EPS_IN_PW,
    FIELD_SW_EPS_OUT_PW,
    FIELD_SW_EPS_STATE,
    FIELD_TA,
    FIELD_TEMP_UNIT,
    FIELD_TODAY_ENERGY,
    FIELD_TODAY_HIGH,
    FIELD_TODAY_LOW,
    FIELD_TOTAL_ENERGY,
    FIELD_TOTAL_N,
    FIELD_TOTAL_P,
    FIELD_TP,
    FIELD_TT,
    FIELD_UPDATE_STATUS,
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
    JACKERY_LIVE_ENERGY_UNITS_PER_KWH,
    PAYLOAD_CT_METER,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_DYNAMIC_PRICE,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_LOCAL_DAILY_ENERGY,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_PV_TRENDS,
    PAYLOAD_SMART_MODE,
    PAYLOAD_SMART_SCHEDULE,
    PAYLOAD_STATISTIC,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_TOU_SCHEDULE,
    PAYLOAD_WEATHER_PLAN,
)
from ..entity import ALL_LIVE_DATA_SOURCES, HTTP_DATA_SOURCES, LAYER5_DATA_SOURCES
from ..util import (
    calculated_smart_meter_power,
    directional_power_value,
    jackery_inverter_ac_input_power,
    jackery_inverter_ac_output_power,
)

# ---------------------------------------------------------------------------
# Helper functions used by value_fns
# ---------------------------------------------------------------------------


def _state_value(value: object) -> StateType:
    """Return only scalar values accepted by Home Assistant's state machine."""
    return value if value is None or isinstance(value, (str, int, float)) else None


def _identity(x: object) -> StateType:
    return _state_value(x)


def _div(divisor: float) -> Callable[[object], float | None]:
    def _f(v: object) -> float | None:
        parsed = safe_float(v)
        return parsed / divisor if parsed is not None else None

    return _f


def _flag_int(v: object) -> int | None:
    return safe_int(v)


def safe_int(v: object) -> int | None:
    """Convert a value to int safely."""
    if not isinstance(v, (bool, int, float, str)):
        return None
    try:
        return int(v)
    except TypeError, ValueError:
        return None


def safe_float(v: object) -> float | None:
    """Convert a value to float safely."""
    if not isinstance(v, (bool, int, float, str)):
        return None
    try:
        return float(v)
    except TypeError, ValueError:
        return None


def safe_bool(v: object) -> bool | None:
    """Convert a value to bool safely."""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.lower() in {"1", "true", "yes", "on"}
    return None


def first_nonblank_int(v: object) -> int | None:
    """Get the first non-blank int from a value."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            return int(float(s))
        except TypeError, ValueError:
            return None
    return None


def property_data_sources(*fields: str, layer5_proven: bool = False) -> tuple[str, ...]:
    """Return the canonical source family for property-model fields."""
    return ALL_LIVE_DATA_SOURCES if layer5_proven and fields else HTTP_DATA_SOURCES


def _get_prop(entity: JackeryEntity, key: str) -> StateType:
    """Get property from merged properties."""
    props = entity.merged_properties or {}
    return _state_value(props.get(key))


def _get_payload_section(
    entity: JackeryEntity,
    section: str,
    key: str,
) -> StateType:
    """Get value from a payload section."""
    return _state_value(entity.payload_section_for_sources(section).get(key))


def _get_ct_meter(entity: JackeryEntity) -> dict[str, Any]:
    """Get CT meter payload from entity."""
    return entity.payload.get(PAYLOAD_CT_METER) or {}


def _smart_meter_value_fn(
    entity: JackeryEntity, description: JackerySmartMeterSensorDescription
) -> StateType:
    """Get smart meter value from CT meter payload using description logic."""
    ct = _get_ct_meter(entity)
    # Inline the logic from _smart_meter_description_value to avoid circular import
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
    result = round(value, 2) if isinstance(value, float) else value
    return _state_value(result)


def _get_payload_http_prop(entity: JackeryEntity, key: str) -> StateType:
    """Get property from HTTP properties payload section."""
    payload = entity.payload.get("http_properties")
    if isinstance(payload, dict):
        return _state_value(payload.get(key))
    return None


def _get_first_list_count(
    entity: JackeryEntity, section: str, *keys: str
) -> int | None:
    """Get count of first list in payload section."""
    payload = entity.payload.get(section)
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def _task_plan_value(task_plan: dict[str, Any], *keys: str) -> StateType:
    """Get value from task plan trying multiple keys."""
    for key in keys:
        if key in task_plan:
            return _state_value(task_plan[key])
    return None


def _first_non_none[T](*values: T | None) -> T | None:
    """Return the first value that is present, preserving false and zero."""
    return next((value for value in values if value is not None), None)


def _storm_minutes_from_plan(weather_plan: dict[str, Any]) -> int | None:
    """Extract storm warning minutes from weather-plan root or row variants."""
    for key in (FIELD_WPC, FIELD_MINS_INTERVAL):
        value = safe_int(weather_plan.get(key))
        if value is not None and value >= 0:
            return value

    storm = weather_plan.get(FIELD_STORM)
    if isinstance(storm, list):
        for row in storm:
            if not isinstance(row, dict):
                continue
            for key in (FIELD_WPC, FIELD_MINS_INTERVAL):
                value = safe_int(row.get(key))
                if value is not None and value >= 0:
                    return value
        return DEFAULT_STORM_WARNING_MINUTES if storm else 0
    return None


def _storm_minutes_fallback(
    props: dict[str, Any],
    weather_plan: dict[str, Any],
    task_plan: dict[str, Any],
) -> int | None:
    """Derive warning minutes when only the enabled state is available."""
    enabled = _first_non_none(
        props.get(FIELD_WPS),
        weather_plan.get(FIELD_WPS),
        _task_plan_value(task_plan, FIELD_WPS),
    )
    if enabled is not None:
        value = safe_int(enabled)
        if value is None:
            return None
        return DEFAULT_STORM_WARNING_MINUTES if value else 0

    storm = weather_plan.get(FIELD_STORM)
    if isinstance(storm, list):
        return DEFAULT_STORM_WARNING_MINUTES if storm else 0
    return None


def _storm_warning_enabled_value(entity: JackeryEntity) -> int | None:
    """Read the enabled marker without treating zero as missing."""
    payload = entity.payload or {}
    weather_plan = payload.get(PAYLOAD_WEATHER_PLAN) or {}
    task_plan = payload.get(PAYLOAD_TASK_PLAN) or {}
    return _first_non_none(
        _flag_int(_get_prop(entity, FIELD_WPS)),
        _flag_int(weather_plan.get(FIELD_WPS)),
        _flag_int(_task_plan_value(task_plan, FIELD_WPS)),
    )


def _storm_warning_minutes_value(entity: JackeryEntity) -> int | None:
    """Read warning minutes across migrated payload variants, preserving zero."""
    payload = entity.payload or {}
    weather_plan = payload.get(PAYLOAD_WEATHER_PLAN) or {}
    task_plan = payload.get(PAYLOAD_TASK_PLAN) or {}
    return _first_non_none(
        safe_int(_get_prop_any(entity, FIELD_WPC, FIELD_MINS_INTERVAL)),
        _storm_minutes_from_plan(weather_plan),
        safe_int(_task_plan_value(task_plan, FIELD_WPC, FIELD_MINS_INTERVAL)),
        _storm_minutes_fallback(
            payload.get(PAYLOAD_PROPERTIES) or {},
            weather_plan,
            task_plan,
        ),
    )


def _get_pv_channel_power(
    entity: JackeryEntity,
    channel_key: str,
) -> StateType:
    """Read per-channel PV power from merged properties."""
    props = entity.merged_properties or {}
    channel = props.get(channel_key)
    if not isinstance(channel, dict):
        return None
    return _state_value(channel.get(FIELD_PV_PW))


def _get_prop_any(entity: JackeryEntity, *keys: str) -> StateType:
    """Get first non-None property from merged properties."""
    props = entity.merged_properties or {}
    for key in keys:
        if key in props and props.get(key) is not None:
            return _state_value(props.get(key))
    return None


def _get_prop_or_disconnected(entity: JackeryEntity, key: str) -> StateType:
    """Get property or return disconnected placeholder."""
    props = entity.merged_properties or {}
    value = props.get(key)
    return _state_value(value) if value is not None else "—"


# ---------------------------------------------------------------------------
# Description classes
# ---------------------------------------------------------------------------


def _default_sensor_value(entity: JackerySensorEntity) -> StateType:
    """Evaluate the historical getter/transform contract for compatibility."""
    description = entity.entity_description
    getter = description.getter
    if getter is None:
        return None
    raw = getter(entity.merged_properties or {})
    if raw is None:
        for fallback in description.fallbacks:
            raw = fallback(entity._payload)  # ruff:ignore[private-member-access]  # legacy fallback contract receives the complete device payload
            if raw is not None:
                break
    value = description.transform(raw)
    if description.value_map is not None:
        try:
            return cast("StateType", description.value_map.get(value, value))
        except TypeError:
            return cast("StateType", value)
    return cast("StateType", value)


@dataclass(frozen=True, kw_only=True)
class _JackerySensorEntityDescription(SensorEntityDescription):
    """Apply integration-wide entity-registry defaults to sensor metadata."""

    def __post_init__(self) -> None:
        """Disable diagnostic entities until a user explicitly enables them."""
        if self.entity_category is EntityCategory.DIAGNOSTIC:
            object.__setattr__(self, "entity_registry_enabled_default", False)


@dataclass(frozen=True, kw_only=True)
class JackerySensorDescription(_JackerySensorEntityDescription):
    """Sensor description supporting entity value_fn and legacy raw getters."""

    value_fn: Callable[[JackerySensorEntity], StateType] = _default_sensor_value
    getter: Callable[[dict[str, Any]], Any] | None = None
    transform: Callable[[Any], Any] = _identity
    fallbacks: tuple[Callable[[dict[str, Any]], Any], ...] = ()
    value_map: dict[int, str] | None = None
    smali_field: str | None = None
    app_fields: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    device_registry_role: str = "head"
    null_semantics: str = DEFAULT_NULL_SEMANTICS
    recorder_allowed: bool = True
    ha_derived: bool = False
    reset_period: Literal["day", "week", "month", "year"] | None = None

    def __post_init__(self) -> None:
        """Resolve getter fields, App fields, and their proven source family."""
        super().__post_init__()
        getter_fields = tuple(getattr(self.getter, "app_fields", ()))
        app_fields = self.app_fields or getter_fields
        if not app_fields and self.smali_field:
            app_fields = (self.smali_field,)
        object.__setattr__(self, "app_fields", app_fields)
        if self.getter is None:
            fields = app_fields

            def _get(properties: dict[str, Any]) -> StateType:
                for field_name in fields:
                    value = properties.get(field_name)
                    if value is not None:
                        return _state_value(value)
                return None

            object.__setattr__(self, "getter", _get)
        if not self.data_sources:
            object.__setattr__(
                self,
                "data_sources",
                property_data_sources(
                    *app_fields,
                    layer5_proven=bool(
                        getattr(self.getter, "layer5_data_source", False)
                    ),
                ),
            )


def _default_stat_value(entity: JackeryStatSensor) -> StateType:
    """Evaluate the historical statistic section/key contract."""
    description = entity.entity_description
    payload = entity.payload or {}
    source = payload.get(description.section)
    raw = source.get(description.stat_key) if isinstance(source, dict) else None
    if raw is None:
        for section, stat_key in description.fallback_sources:
            fallback = payload.get(section)
            if isinstance(fallback, dict) and fallback.get(stat_key) is not None:
                raw = fallback.get(stat_key)
                break
    return cast("StateType", description.transform(raw))


@dataclass(frozen=True, kw_only=True)
class JackeryStatSensorDescription(_JackerySensorEntityDescription):
    """Sensor description for statistics with value_fn."""

    _attr_has_entity_name = True

    value_fn: Callable[[JackeryStatSensor], StateType] = _default_stat_value
    stat_key: str
    section: str = PAYLOAD_STATISTIC
    fallback_sources: tuple[tuple[str, str], ...] = ()
    reset_period: Literal["day", "week", "month", "year"] | None = None
    data_sources: tuple[str, ...] = HTTP_DATA_SOURCES
    transform: Callable[[Any], Any] = _identity


@dataclass(frozen=True, kw_only=True)
class JackeryBatteryPackSensorDescription(_JackerySensorEntityDescription):
    """Sensor description for battery pack entries."""

    value_fn: Callable[[JackeryBatteryPackSensor], StateType]
    field: str
    transform: Callable[[Any], Any] = _identity
    data_sources: tuple[str, ...] = ALL_LIVE_DATA_SOURCES
    reset_period: Literal["day", "week", "month", "year"] | None = None


@dataclass(frozen=True, kw_only=True)
class JackerySmartPlugSensorDescription(_JackerySensorEntityDescription):
    """Sensor description for smart plug entries."""

    value_fn: Callable[[JackerySmartPlugSensor], StateType]
    field: str
    transform: Callable[[Any], Any] = _identity
    reset_period: Literal["day", "week", "month", "year"] | None = None
    data_sources: tuple[str, ...] = ALL_LIVE_DATA_SOURCES


@dataclass(frozen=True, kw_only=True)
class JackeryMeterHeadSensorDescription(_JackerySensorEntityDescription):
    """Sensor description for meter head entries."""

    value_fn: Callable[[JackeryMeterHeadSensor], StateType]
    field: str
    transform: Callable[[Any], Any] = _identity
    data_sources: tuple[str, ...] = ALL_LIVE_DATA_SOURCES
    reset_period: Literal["day", "week", "month", "year"] | None = None


@dataclass(frozen=True, kw_only=True)
class JackerySmartMeterSensorDescription(_JackerySensorEntityDescription):
    """Sensor description for CT / smart-meter payloads."""

    value_fn: Callable[[JackerySmartMeterSensor], StateType]
    field: str
    calculation: str | None = None
    aliases: tuple[str, ...] = ()
    negative_aliases: tuple[str, ...] = ()
    sum_fields: tuple[str, ...] = ()
    negative_sum_fields: tuple[str, ...] = ()
    fallback_fields: tuple[str, ...] = ()
    transform: Callable[[Any], Any] = _identity
    data_sources: tuple[str, ...] = ALL_LIVE_DATA_SOURCES
    reset_period: Literal["day", "week", "month", "year"] | None = None


def _default_savings_detail_value(
    entity: JackerySavingsDetailSensor,
) -> StateType:
    """Evaluate the historical nested savings path contract."""
    description = entity.entity_description
    return cast(
        "StateType",
        description.transform(entity.get_savings_value(description.path)),
    )


@dataclass(frozen=True, kw_only=True)
class JackerySavingsDetailSensorDescription(_JackerySensorEntityDescription):
    """Sensor description for calculated savings detail values."""

    value_fn: Callable[[JackerySavingsDetailSensor], StateType] = (
        _default_savings_detail_value
    )
    path: tuple[str, ...]
    transform: Callable[[Any], Any] = safe_float
    data_sources: tuple[str, ...] = HTTP_DATA_SOURCES
    reset_period: Literal["day", "week", "month", "year"] | None = None


@dataclass(frozen=True, kw_only=True)
class JackeryBreakerSensorDescription(_JackerySensorEntityDescription):
    """Sensor description for circuit breaker entries."""

    value_fn: Callable[[JackeryBreakerSensor], StateType]
    field: str
    transform: Callable[[Any], Any] = _identity
    data_sources: tuple[str, ...] = LAYER5_DATA_SOURCES


@dataclass(frozen=True, kw_only=True)
class JackerySubdeviceAlarmSensorDescription(_JackerySensorEntityDescription):
    """Sensor description for subdevice alarm entries."""

    value_fn: Callable[[JackerySubdeviceAlarmSensor], StateType]
    field: str
    transform: Callable[[Any], Any] = _identity
    data_sources: tuple[str, ...] = ALL_LIVE_DATA_SOURCES


# ---------------------------------------------------------------------------
# Sensor description tuples (migrated from sensor.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sensor description tuples (migrated from sensor.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sensor description tuples (migrated from sensor.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sensor description tuples (migrated from sensor.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sensor description tuples (migrated from sensor.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sensor description tuples (migrated from sensor.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sensor description tuples (migrated from sensor.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sensor description tuples (migrated from sensor.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sensor description tuples (migrated from sensor.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sensor description tuples (migrated from sensor.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sensor description tuples (migrated from sensor.py)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Sensor description tuples (migrated from sensor.py)
# ---------------------------------------------------------------------------

SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        app_fields=(FIELD_SOC,),
        key="soc",
        value_fn=lambda e: _get_prop(e, FIELD_SOC),
        translation_key="battery_soc",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BAT_SOC,),
        key="bat_soc",
        value_fn=lambda e: _get_prop(e, FIELD_BAT_SOC),
        translation_key="battery_soc_internal",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CELL_TEMP,),
        key="cell_temperature",
        value_fn=lambda e: _div(10)(_get_prop(e, FIELD_CELL_TEMP)),
        translation_key="cell_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BAT_IN_PW,),
        key="battery_charge_power",
        value_fn=lambda e: (
            (_get_prop(e, FIELD_BAT_IN_PW))
            or (_get_payload_http_prop(e, FIELD_BAT_IN_PW))
        ),
        translation_key="battery_charge_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BAT_OUT_PW,),
        key="battery_discharge_power",
        value_fn=lambda e: (
            (_get_prop(e, FIELD_BAT_OUT_PW))
            or (_get_payload_http_prop(e, FIELD_BAT_OUT_PW))
        ),
        translation_key="battery_discharge_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PV_PW,),
        key="pv_power_total",
        value_fn=lambda e: _get_prop(e, FIELD_PV_PW),
        translation_key="pv_power_total",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PV1,),
        key="pv1_power",
        value_fn=lambda e: _get_pv_channel_power(e, FIELD_PV1),
        translation_key="pv1_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PV2,),
        key="pv2_power",
        value_fn=lambda e: _get_pv_channel_power(e, FIELD_PV2),
        translation_key="pv2_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PV3,),
        key="pv3_power",
        value_fn=lambda e: _get_pv_channel_power(e, FIELD_PV3),
        translation_key="pv3_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PV4,),
        key="pv4_power",
        value_fn=lambda e: _get_pv_channel_power(e, FIELD_PV4),
        translation_key="pv4_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IN_GRID_SIDE_PW,),
        key="grid_in_power",
        value_fn=lambda e: _get_prop(e, FIELD_IN_GRID_SIDE_PW),
        translation_key="grid_in_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OUT_GRID_SIDE_PW,),
        key="grid_out_power",
        value_fn=lambda e: _get_prop(e, FIELD_OUT_GRID_SIDE_PW),
        translation_key="grid_out_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="inverter_ac_input_power",
        app_fields=(FIELD_GRID_IN_PW, FIELD_IN_ONGRID_PW),
        getter=jackery_inverter_ac_input_power,
        translation_key="inverter_ac_input_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        key="inverter_ac_output_power",
        app_fields=(FIELD_GRID_OUT_PW, FIELD_OUT_ONGRID_PW),
        getter=jackery_inverter_ac_output_power,
        translation_key="inverter_ac_output_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SW_EPS_IN_PW,),
        key="eps_in_power",
        value_fn=lambda e: _get_prop(e, FIELD_SW_EPS_IN_PW),
        translation_key="eps_in_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SW_EPS_OUT_PW,),
        key="eps_out_power",
        value_fn=lambda e: _get_prop(e, FIELD_SW_EPS_OUT_PW),
        translation_key="eps_out_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_STACK_IN_PW,),
        key="stack_in_power",
        value_fn=lambda e: _get_prop(e, FIELD_STACK_IN_PW),
        translation_key="stack_in_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_STACK_OUT_PW,),
        key="stack_out_power",
        value_fn=lambda e: _get_prop(e, FIELD_STACK_OUT_PW),
        translation_key="stack_out_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WSIG,),
        key="wifi_signal",
        value_fn=lambda e: _get_prop(e, FIELD_WSIG),
        translation_key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WNAME,),
        key="wifi_name",
        value_fn=lambda e: _get_prop(e, FIELD_WNAME),
        translation_key="wifi_name",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="wifi_ip",
        value_fn=lambda e: _get_prop(e, FIELD_WIP),
        translation_key="wifi_ip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="mac_address",
        value_fn=lambda e: _get_prop(e, FIELD_MAC),
        translation_key="mac_address",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ETH_PORT,),
        key="eth_port",
        value_fn=lambda e: _get_prop(e, FIELD_ETH_PORT),
        translation_key="eth_port",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ABILITY,),
        key="ability_bits",
        value_fn=lambda e: _get_prop(e, FIELD_ABILITY),
        translation_key="ability_bits",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAX_IOT_NUM,),
        key="max_iot_num",
        value_fn=lambda e: _get_prop(e, FIELD_MAX_IOT_NUM),
        translation_key="max_iot_num",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SW_EPS_STATE,),
        key="eps_switch_state",
        value_fn=lambda e: _get_prop(e, FIELD_SW_EPS_STATE),
        translation_key="eps_switch_state",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_REBOOT,),
        key="reboot_flag",
        value_fn=lambda e: _get_prop(e, FIELD_REBOOT),
        translation_key="reboot_flag",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SOC_CHARGE_LIMIT, FIELD_SOC_CHG_LIMIT),
        key="soc_charge_limit",
        value_fn=lambda e: _get_prop_any(
            e, FIELD_SOC_CHG_LIMIT, FIELD_SOC_CHARGE_LIMIT
        ),
        translation_key="soc_charge_limit",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SOC_DISCHARGE_LIMIT, FIELD_SOC_DISCHG_LIMIT),
        key="soc_discharge_limit",
        value_fn=lambda e: _get_prop_any(
            e, FIELD_SOC_DISCHG_LIMIT, FIELD_SOC_DISCHARGE_LIMIT
        ),
        translation_key="soc_discharge_limit",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAX_OUT_PW,),
        key="max_output_power",
        value_fn=lambda e: _get_prop(e, FIELD_MAX_OUT_PW),
        translation_key="max_output_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAX_INV_STD_PW,),
        key="max_inverter_power",
        value_fn=lambda e: _get_prop(e, FIELD_MAX_INV_STD_PW),
        translation_key="max_inverter_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAX_GRID_STD_PW,),
        key="max_grid_standard_power",
        value_fn=lambda e: _get_prop(e, FIELD_MAX_GRID_STD_PW),
        translation_key="max_grid_standard_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_EIP,),
        key="ethernet_ip",
        value_fn=lambda e: _get_prop_or_disconnected(e, FIELD_EIP),
        translation_key="ethernet_ip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ETH_PORT,),
        key="ethernet_port",
        value_fn=lambda e: _get_prop(e, FIELD_ETH_PORT),
        translation_key="ethernet_port",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_EMAC,),
        key="ethernet_mac",
        value_fn=lambda e: _get_prop_or_disconnected(e, FIELD_EMAC),
        translation_key="ethernet_mac",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="battery_count",
        value_fn=lambda e: _get_prop(e, FIELD_BAT_NUM),
        translation_key="battery_count",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BAT_STATE,),
        key="battery_state",
        value_fn=lambda e: _get_prop(e, FIELD_BAT_STATE),
        translation_key="battery_state",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_AUTO_STANDBY,),
        key="auto_standby",
        value_fn=lambda e: _get_prop(e, FIELD_AUTO_STANDBY),
        translation_key="auto_standby",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_STAT,),
        key="system_state",
        value_fn=lambda e: _get_prop(e, FIELD_STAT),
        translation_key="system_state",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ONGRID_STAT, FIELD_ON_GRID_STAT),
        key="ongrid_state",
        value_fn=lambda e: _get_prop_any(e, FIELD_ONGRID_STAT, FIELD_ON_GRID_STAT),
        translation_key="ongrid_state",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CT_STAT, FIELD_CT_STATE),
        key="ct_state",
        value_fn=lambda e: _get_prop_any(e, FIELD_CT_STAT, FIELD_CT_STATE),
        translation_key="ct_state",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_GRID_STAT, FIELD_GRID_STATE, FIELD_GRID_STATE_ALT),
        key="grid_state",
        value_fn=lambda e: _get_prop_any(
            e, FIELD_GRID_STATE, FIELD_GRID_STATE_ALT, FIELD_GRID_STAT
        ),
        translation_key="grid_state",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WORK_MODEL,),
        key="work_mode",
        value_fn=lambda e: (
            (
                (_get_prop(e, FIELD_WORK_MODEL))
                or (
                    _task_plan_value(
                        (e.payload or {}).get(PAYLOAD_TASK_PLAN) or {},
                        FIELD_WORK_MODEL,
                    )
                )
            )
            or (
                7
                if safe_int(
                    ((e.payload or {}).get(PAYLOAD_PRICE) or {}).get(
                        FIELD_DYNAMIC_OR_SINGLE
                    )
                )
                == 1
                else None
            )
        ),
        translation_key="work_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAX_SYS_OUT_PW,),
        key="max_system_output_power",
        value_fn=lambda e: _get_prop(e, FIELD_MAX_SYS_OUT_PW),
        translation_key="max_system_output_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAX_SYS_IN_PW,),
        key="max_system_input_power",
        value_fn=lambda e: _get_prop(e, FIELD_MAX_SYS_IN_PW),
        translation_key="max_system_input_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OFF_GRID_TIME,),
        key="off_grid_time",
        value_fn=lambda e: (
            (_get_prop(e, FIELD_OFF_GRID_TIME))
            or (
                _task_plan_value(
                    (e.payload or {}).get(PAYLOAD_TASK_PLAN) or {},
                    FIELD_OFF_GRID_TIME,
                    FIELD_OFF_GRID_DOWN_TIME,
                    FIELD_OFF_GRID_AUTO_OFF_TIME,
                )
            )
        ),
        translation_key="off_grid_time",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_DEFAULT_PW,),
        key="default_power",
        value_fn=lambda e: _get_prop(e, FIELD_DEFAULT_PW),
        translation_key="default_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_STANDBY_PW,),
        key="standby_power",
        value_fn=lambda e: _get_prop(e, FIELD_STANDBY_PW),
        translation_key="standby_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OTHER_LOAD_PW,),
        key="other_load_power",
        value_fn=lambda e: _get_prop(e, FIELD_OTHER_LOAD_PW),
        translation_key="other_load_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ENERGY_PLAN_PW,),
        key="energy_plan_power",
        value_fn=lambda e: _get_prop(e, FIELD_ENERGY_PLAN_PW),
        translation_key="energy_plan_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CHARGE_PLAN_PW,),
        key="charge_plan_power",
        value_fn=lambda e: _get_prop(e, FIELD_CHARGE_PLAN_PW),
        translation_key="charge_plan_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IS_FOLLOW_METER_PW,),
        key="follow_meter_state",
        value_fn=lambda e: (
            (_get_prop(e, FIELD_IS_FOLLOW_METER_PW))
            or (
                _task_plan_value(
                    (e.payload or {}).get(PAYLOAD_TASK_PLAN) or {},
                    FIELD_IS_FOLLOW_METER_PW,
                    FIELD_FOLLOW_METER,
                )
            )
        ),
        translation_key="follow_meter_state",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OFF_GRID_DOWN,),
        key="off_grid_shutdown_state",
        value_fn=lambda e: (
            (_get_prop(e, FIELD_OFF_GRID_DOWN))
            or (
                _task_plan_value(
                    (e.payload or {}).get(PAYLOAD_TASK_PLAN) or {}, FIELD_OFF_GRID_DOWN
                )
            )
        ),
        translation_key="off_grid_shutdown_state",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_FUNC_ENABLE,),
        key="function_enable_flags",
        value_fn=lambda e: _get_prop(e, FIELD_FUNC_ENABLE),
        translation_key="function_enable_flags",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_TEMP_UNIT,),
        key="temp_unit",
        value_fn=lambda e: _get_prop(e, FIELD_TEMP_UNIT),
        translation_key="temp_unit",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WPS,),
        key="storm_warning_enabled",
        value_fn=_storm_warning_enabled_value,
        translation_key="storm_warning_enabled",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MINS_INTERVAL, FIELD_WPC),
        key="storm_warning_minutes",
        value_fn=_storm_warning_minutes_value,
        translation_key="storm_warning_minutes",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

STAT_DESCRIPTIONS: tuple[JackeryStatSensorDescription, ...] = (
    JackeryStatSensorDescription(
        key="today_load",
        stat_key=APP_STAT_TODAY_LOAD,
        section=PAYLOAD_STATISTIC,
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_STATISTIC, APP_STAT_TODAY_LOAD
        ),
        translation_key="today_load",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="total_generation",
        stat_key=APP_STAT_TOTAL_GENERATION,
        section=PAYLOAD_STATISTIC,
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_STATISTIC, APP_STAT_TOTAL_GENERATION
        ),
        translation_key="total_generation",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="total_revenue",
        stat_key=APP_STAT_TOTAL_REVENUE,
        section=PAYLOAD_STATISTIC,
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_STATISTIC, APP_STAT_TOTAL_REVENUE
        ),
        translation_key="total_revenue",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=CURRENCY_EURO,
    ),
    JackeryStatSensorDescription(
        key="total_carbon_saved",
        stat_key=APP_STAT_TOTAL_CARBON,
        section=PAYLOAD_STATISTIC,
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_STATISTIC, APP_STAT_TOTAL_CARBON
        ),
        translation_key="total_carbon_saved",
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
    ),
    JackeryStatSensorDescription(
        key="battery_charge_energy",
        stat_key=APP_DEVICE_STAT_BATTERY_CHARGE,
        section=PAYLOAD_PROPERTIES,
        value_fn=lambda e: _div(100)(
            _get_payload_section(e, PAYLOAD_PROPERTIES, APP_DEVICE_STAT_BATTERY_CHARGE)
        ),
        transform=_div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH),
        translation_key="battery_charge_energy",
        data_sources=ALL_LIVE_DATA_SOURCES,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="battery_discharge_energy",
        stat_key=APP_DEVICE_STAT_BATTERY_DISCHARGE,
        section=PAYLOAD_PROPERTIES,
        value_fn=lambda e: _div(100)(
            _get_payload_section(
                e, PAYLOAD_PROPERTIES, APP_DEVICE_STAT_BATTERY_DISCHARGE
            )
        ),
        transform=_div(100),
        translation_key="battery_discharge_energy",
        data_sources=ALL_LIVE_DATA_SOURCES,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="main_battery_charge_energy",
        stat_key=APP_DEVICE_STAT_BATTERY_CHARGE,
        section=PAYLOAD_PROPERTIES,
        value_fn=lambda e: _div(100)(
            _get_payload_section(e, PAYLOAD_PROPERTIES, APP_DEVICE_STAT_BATTERY_CHARGE)
        ),
        transform=_div(100),
        translation_key="main_battery_charge_energy",
        data_sources=ALL_LIVE_DATA_SOURCES,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="main_battery_discharge_energy",
        stat_key=APP_DEVICE_STAT_BATTERY_DISCHARGE,
        section=PAYLOAD_PROPERTIES,
        value_fn=lambda e: _div(100)(
            _get_payload_section(
                e, PAYLOAD_PROPERTIES, APP_DEVICE_STAT_BATTERY_DISCHARGE
            )
        ),
        transform=_div(100),
        translation_key="main_battery_discharge_energy",
        data_sources=ALL_LIVE_DATA_SOURCES,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="pv_week_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_SOLAR_ENERGY
        ),
        translation_key="pv_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="pv_month_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}", APP_STAT_TOTAL_SOLAR_ENERGY
        ),
        translation_key="pv_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="pv_year_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_SOLAR_ENERGY
        ),
        translation_key="pv_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="pv_revenue_day",
        stat_key=APP_STAT_TOTAL_SOLAR_REVENUE,
        section=PAYLOAD_PV_TRENDS,
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_PV_TRENDS, APP_STAT_TOTAL_SOLAR_REVENUE
        ),
        translation_key="pv_revenue_day",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="pv_revenue_week",
        stat_key=APP_STAT_TOTAL_SOLAR_REVENUE,
        transform=safe_float,
        section=f"{APP_SECTION_PV_TRENDS}_{DATE_TYPE_WEEK}",
        fallback_sources=(
            (
                f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
                APP_STAT_TOTAL_SOLAR_REVENUE,
            ),
        ),
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_PV_TRENDS}_{DATE_TYPE_WEEK}",
            APP_STAT_TOTAL_SOLAR_REVENUE,
        ),
        translation_key="pv_revenue_week",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="pv_revenue_month",
        stat_key=APP_STAT_TOTAL_SOLAR_REVENUE,
        section=f"{APP_SECTION_PV_TRENDS}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_PV_TRENDS}_{DATE_TYPE_MONTH}",
            APP_STAT_TOTAL_SOLAR_REVENUE,
        ),
        translation_key="pv_revenue_month",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="pv_revenue_year",
        stat_key=APP_STAT_TOTAL_SOLAR_REVENUE,
        section=f"{APP_SECTION_PV_TRENDS}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_PV_TRENDS}_{DATE_TYPE_YEAR}",
            APP_STAT_TOTAL_SOLAR_REVENUE,
        ),
        translation_key="pv_revenue_year",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="device_pv1_day_energy",
        stat_key=APP_STAT_PV1_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}", APP_STAT_PV1_ENERGY
        ),
        translation_key="device_pv1_day_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="device_pv1_week_energy",
        stat_key=APP_STAT_PV1_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}", APP_STAT_PV1_ENERGY
        ),
        translation_key="device_pv1_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="device_pv1_month_energy",
        stat_key=APP_STAT_PV1_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}", APP_STAT_PV1_ENERGY
        ),
        translation_key="device_pv1_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="device_pv1_year_energy",
        stat_key=APP_STAT_PV1_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}", APP_STAT_PV1_ENERGY
        ),
        translation_key="device_pv1_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="device_pv2_day_energy",
        stat_key=APP_STAT_PV2_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}", APP_STAT_PV2_ENERGY
        ),
        translation_key="device_pv2_day_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="device_pv2_week_energy",
        stat_key=APP_STAT_PV2_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}", APP_STAT_PV2_ENERGY
        ),
        translation_key="device_pv2_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="device_pv2_month_energy",
        stat_key=APP_STAT_PV2_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}", APP_STAT_PV2_ENERGY
        ),
        translation_key="device_pv2_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="device_pv2_year_energy",
        stat_key=APP_STAT_PV2_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}", APP_STAT_PV2_ENERGY
        ),
        translation_key="device_pv2_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="device_pv3_day_energy",
        stat_key=APP_STAT_PV3_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}", APP_STAT_PV3_ENERGY
        ),
        translation_key="device_pv3_day_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="device_pv3_week_energy",
        stat_key=APP_STAT_PV3_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}", APP_STAT_PV3_ENERGY
        ),
        translation_key="device_pv3_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="device_pv3_month_energy",
        stat_key=APP_STAT_PV3_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}", APP_STAT_PV3_ENERGY
        ),
        translation_key="device_pv3_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="device_pv3_year_energy",
        stat_key=APP_STAT_PV3_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}", APP_STAT_PV3_ENERGY
        ),
        translation_key="device_pv3_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="device_pv4_day_energy",
        stat_key=APP_STAT_PV4_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}", APP_STAT_PV4_ENERGY
        ),
        translation_key="device_pv4_day_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="device_pv4_week_energy",
        stat_key=APP_STAT_PV4_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}", APP_STAT_PV4_ENERGY
        ),
        translation_key="device_pv4_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="device_pv4_month_energy",
        stat_key=APP_STAT_PV4_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}", APP_STAT_PV4_ENERGY
        ),
        translation_key="device_pv4_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="device_pv4_year_energy",
        stat_key=APP_STAT_PV4_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}", APP_STAT_PV4_ENERGY
        ),
        translation_key="device_pv4_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="home_day_energy",
        stat_key=APP_STAT_TOTAL_HOME_ENERGY,
        section=PAYLOAD_HOME_TRENDS,
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_HOME_TRENDS, APP_STAT_TOTAL_HOME_ENERGY
        ),
        transform=safe_float,
        translation_key="home_day_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="home_week_energy",
        stat_key=APP_STAT_TOTAL_HOME_ENERGY,
        section=f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_HOME_ENERGY
        ),
        translation_key="home_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="home_month_energy",
        stat_key=APP_STAT_TOTAL_HOME_ENERGY,
        section=f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_MONTH}",
            APP_STAT_TOTAL_HOME_ENERGY,
        ),
        translation_key="home_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="home_year_energy",
        stat_key=APP_STAT_TOTAL_HOME_ENERGY,
        section=f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_HOME_ENERGY
        ),
        translation_key="home_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="device_ongrid_input_week_energy",
        stat_key=APP_STAT_TOTAL_IN_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_WEEK}",
            APP_STAT_TOTAL_IN_GRID_ENERGY,
        ),
        translation_key="device_ongrid_input_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="device_ongrid_input_month_energy",
        stat_key=APP_STAT_TOTAL_IN_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_MONTH}",
            APP_STAT_TOTAL_IN_GRID_ENERGY,
        ),
        translation_key="device_ongrid_input_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="device_ongrid_input_year_energy",
        stat_key=APP_STAT_TOTAL_IN_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_YEAR}",
            APP_STAT_TOTAL_IN_GRID_ENERGY,
        ),
        translation_key="device_ongrid_input_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="device_ongrid_output_week_energy",
        stat_key=APP_STAT_TOTAL_OUT_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_WEEK}",
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
        ),
        translation_key="device_ongrid_output_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="device_ongrid_output_month_energy",
        stat_key=APP_STAT_TOTAL_OUT_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_MONTH}",
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
        ),
        translation_key="device_ongrid_output_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="device_ongrid_output_year_energy",
        stat_key=APP_STAT_TOTAL_OUT_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_YEAR}",
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
        ),
        translation_key="device_ongrid_output_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="ct_input_day_energy",
        stat_key=APP_STAT_TOTAL_CT_INPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_DAY}",
        fallback_sources=(
            (
                f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}",
                APP_STAT_TOTAL_IN_GRID_ENERGY,
            ),
        ),
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_CT_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_CT_INPUT_ENERGY
        ),
        translation_key="ct_input_day_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="ct_input_week_energy",
        stat_key=APP_STAT_TOTAL_CT_INPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_WEEK}",
        fallback_sources=(
            (
                f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_WEEK}",
                APP_STAT_TOTAL_IN_GRID_ENERGY,
            ),
        ),
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_CT_STAT}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_CT_INPUT_ENERGY
        ),
        translation_key="ct_input_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="ct_input_month_energy",
        stat_key=APP_STAT_TOTAL_CT_INPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}",
        fallback_sources=(
            (
                f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_MONTH}",
                APP_STAT_TOTAL_IN_GRID_ENERGY,
            ),
        ),
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}",
            APP_STAT_TOTAL_CT_INPUT_ENERGY,
        ),
        translation_key="ct_input_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="ct_input_year_energy",
        stat_key=APP_STAT_TOTAL_CT_INPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_YEAR}",
        fallback_sources=(
            (
                f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_YEAR}",
                APP_STAT_TOTAL_IN_GRID_ENERGY,
            ),
        ),
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_CT_STAT}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_CT_INPUT_ENERGY
        ),
        translation_key="ct_input_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="ct_output_day_energy",
        stat_key=APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_DAY}",
        fallback_sources=(
            (
                f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}",
                APP_STAT_TOTAL_OUT_GRID_ENERGY,
            ),
        ),
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_CT_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_CT_OUTPUT_ENERGY
        ),
        translation_key="ct_output_day_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="ct_output_week_energy",
        stat_key=APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_WEEK}",
        fallback_sources=(
            (
                f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_WEEK}",
                APP_STAT_TOTAL_OUT_GRID_ENERGY,
            ),
        ),
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_CT_STAT}_{DATE_TYPE_WEEK}",
            APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        ),
        translation_key="ct_output_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="ct_output_month_energy",
        stat_key=APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}",
        fallback_sources=(
            (
                f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_MONTH}",
                APP_STAT_TOTAL_OUT_GRID_ENERGY,
            ),
        ),
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}",
            APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        ),
        translation_key="ct_output_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="ct_output_year_energy",
        stat_key=APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        section=f"{APP_SECTION_CT_STAT}_{DATE_TYPE_YEAR}",
        fallback_sources=(
            (
                f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_YEAR}",
                APP_STAT_TOTAL_OUT_GRID_ENERGY,
            ),
        ),
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_CT_STAT}_{DATE_TYPE_YEAR}",
            APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        ),
        translation_key="ct_output_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="battery_charge_week_energy",
        stat_key=APP_STAT_TOTAL_CHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_CHARGE
        ),
        translation_key="battery_charge_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="battery_charge_month_energy",
        stat_key=APP_STAT_TOTAL_CHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_MONTH}", APP_STAT_TOTAL_CHARGE
        ),
        translation_key="battery_charge_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="battery_charge_year_energy",
        stat_key=APP_STAT_TOTAL_CHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_CHARGE
        ),
        translation_key="battery_charge_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="battery_discharge_week_energy",
        stat_key=APP_STAT_TOTAL_DISCHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_DISCHARGE
        ),
        translation_key="battery_discharge_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="battery_discharge_month_energy",
        stat_key=APP_STAT_TOTAL_DISCHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_MONTH}", APP_STAT_TOTAL_DISCHARGE
        ),
        translation_key="battery_discharge_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="battery_discharge_year_energy",
        stat_key=APP_STAT_TOTAL_DISCHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_DISCHARGE
        ),
        translation_key="battery_discharge_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="eps_input_day_energy",
        stat_key=APP_STAT_TOTAL_IN_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_IN_EPS_ENERGY
        ),
        translation_key="eps_input_day_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="eps_input_week_energy",
        stat_key=APP_STAT_TOTAL_IN_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_IN_EPS_ENERGY
        ),
        translation_key="eps_input_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="eps_input_month_energy",
        stat_key=APP_STAT_TOTAL_IN_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_MONTH}", APP_STAT_TOTAL_IN_EPS_ENERGY
        ),
        translation_key="eps_input_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="eps_input_year_energy",
        stat_key=APP_STAT_TOTAL_IN_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_IN_EPS_ENERGY
        ),
        translation_key="eps_input_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="eps_output_day_energy",
        stat_key=APP_STAT_TOTAL_OUT_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_OUT_EPS_ENERGY
        ),
        translation_key="eps_output_day_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="eps_output_week_energy",
        stat_key=APP_STAT_TOTAL_OUT_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_OUT_EPS_ENERGY
        ),
        translation_key="eps_output_week_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        key="eps_output_month_energy",
        stat_key=APP_STAT_TOTAL_OUT_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_MONTH}",
            APP_STAT_TOTAL_OUT_EPS_ENERGY,
        ),
        translation_key="eps_output_month_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        key="eps_output_year_energy",
        stat_key=APP_STAT_TOTAL_OUT_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_OUT_EPS_ENERGY
        ),
        translation_key="eps_output_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        key="today_feed_in_energy",
        stat_key=APP_STAT_TODAY_SOLAR_ENERGY,
        section=APP_SECTION_TODAY_ENERGY,
        value_fn=lambda e: _get_payload_section(
            e, APP_SECTION_TODAY_ENERGY, APP_STAT_TODAY_SOLAR_ENERGY
        ),
        transform=safe_float,
        translation_key="today_solar_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="today_grid_import_energy",
        stat_key=APP_STAT_TODAY_GRID_IMPORT_ENERGY,
        section=APP_SECTION_TODAY_ENERGY,
        value_fn=lambda e: _get_payload_section(
            e, APP_SECTION_TODAY_ENERGY, APP_STAT_TODAY_GRID_IMPORT_ENERGY
        ),
        transform=safe_float,
        translation_key="today_grid_import_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="today_home_load_energy",
        stat_key=APP_STAT_TODAY_HOME_LOAD_ENERGY,
        section=APP_SECTION_TODAY_ENERGY,
        fallback_sources=((PAYLOAD_HOME_TRENDS, APP_STAT_TOTAL_HOME_ENERGY),),
        value_fn=lambda e: _get_payload_section(
            e, APP_SECTION_TODAY_ENERGY, APP_STAT_TODAY_HOME_LOAD_ENERGY
        ),
        transform=safe_float,
        translation_key="today_home_load_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="today_battery_energy",
        stat_key=APP_STAT_TODAY_BATTERY_ENERGY,
        section=APP_SECTION_TODAY_ENERGY,
        fallback_sources=((PAYLOAD_STATISTIC, APP_STAT_TODAY_BATTERY_DISCHARGE),),
        value_fn=lambda e: _get_payload_section(
            e, APP_SECTION_TODAY_ENERGY, APP_STAT_TODAY_BATTERY_ENERGY
        ),
        transform=safe_float,
        translation_key="today_battery_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="power_price",
        stat_key=FIELD_SINGLE_PRICE,
        section=PAYLOAD_PRICE,
        value_fn=lambda e: _get_payload_section(e, PAYLOAD_PRICE, FIELD_SINGLE_PRICE),
        translation_key="power_price",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=f"{CURRENCY_EURO}/kWh",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryStatSensorDescription(
        key="device_today_pv_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_SOLAR_ENERGY
        ),
        translation_key="device_today_pv_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="device_today_battery_charge",
        stat_key=APP_STAT_TOTAL_CHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_CHARGE
        ),
        translation_key="device_today_battery_charge",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="device_today_battery_discharge",
        stat_key=APP_STAT_TOTAL_DISCHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_DISCHARGE
        ),
        translation_key="device_today_battery_discharge",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="device_today_ongrid_input",
        stat_key=APP_STAT_TOTAL_IN_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_IN_GRID_ENERGY
        ),
        translation_key="device_today_ongrid_input",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="device_today_ongrid_output",
        stat_key=APP_STAT_TOTAL_OUT_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}",
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
        ),
        translation_key="device_today_ongrid_output",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="device_today_ongrid_to_battery",
        stat_key=APP_DEVICE_STAT_ONGRID_TO_BATTERY,
        section=PAYLOAD_LOCAL_DAILY_ENERGY,
        fallback_sources=(
            (PAYLOAD_DEVICE_STATISTIC, APP_DEVICE_STAT_ONGRID_TO_BATTERY),
            (
                f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}",
                APP_DEVICE_STAT_ONGRID_TO_BATTERY,
            ),
        ),
        value_fn=lambda e: _div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH)(
            _get_payload_section(
                e, PAYLOAD_LOCAL_DAILY_ENERGY, APP_DEVICE_STAT_ONGRID_TO_BATTERY
            )
        ),
        transform=_div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH),
        data_sources=ALL_LIVE_DATA_SOURCES,
        translation_key="device_today_ongrid_to_battery",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="device_today_pv_to_battery",
        stat_key=APP_DEVICE_STAT_PV_TO_BATTERY,
        section=PAYLOAD_LOCAL_DAILY_ENERGY,
        fallback_sources=(
            (PAYLOAD_DEVICE_STATISTIC, APP_DEVICE_STAT_PV_TO_BATTERY),
            (
                f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}",
                APP_DEVICE_STAT_PV_TO_BATTERY,
            ),
        ),
        value_fn=lambda e: _div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH)(
            _get_payload_section(
                e, PAYLOAD_LOCAL_DAILY_ENERGY, APP_DEVICE_STAT_PV_TO_BATTERY
            )
        ),
        transform=_div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH),
        data_sources=ALL_LIVE_DATA_SOURCES,
        translation_key="device_today_pv_to_battery",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="device_today_battery_to_ongrid",
        stat_key=APP_DEVICE_STAT_BATTERY_TO_GRID,
        section=PAYLOAD_LOCAL_DAILY_ENERGY,
        fallback_sources=(
            (PAYLOAD_DEVICE_STATISTIC, APP_DEVICE_STAT_BATTERY_TO_GRID),
            (
                f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}",
                APP_DEVICE_STAT_BATTERY_TO_GRID,
            ),
        ),
        value_fn=lambda e: _div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH)(
            _get_payload_section(
                e, PAYLOAD_LOCAL_DAILY_ENERGY, APP_DEVICE_STAT_BATTERY_TO_GRID
            )
        ),
        transform=_div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH),
        data_sources=ALL_LIVE_DATA_SOURCES,
        translation_key="device_today_battery_to_ongrid",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="symmetry_total_positive",
        stat_key=FIELD_TOTAL_P,
        section=f"{APP_SECTION_SYMMETRY_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_SYMMETRY_STAT}_{DATE_TYPE_DAY}", FIELD_TOTAL_P
        ),
        translation_key="symmetry_total_positive",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        key="symmetry_total_negative",
        stat_key=FIELD_TOTAL_N,
        section=f"{APP_SECTION_SYMMETRY_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_SYMMETRY_STAT}_{DATE_TYPE_DAY}", FIELD_TOTAL_N
        ),
        translation_key="symmetry_total_negative",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
)

SMART_MODE_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        app_fields=("isActive",),
        key="smart_mode_active",
        translation_key="smart_mode_active",
        value_fn=lambda e: _get_payload_section(e, PAYLOAD_SMART_MODE, "isActive"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=("timeDifference",),
        key="smart_mode_time_difference",
        translation_key="smart_mode_time_difference",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_SMART_MODE, "timeDifference"
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

SMART_SCHEDULE_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        key="smart_schedule_points",
        value_fn=lambda e: _get_first_list_count(
            e,
            PAYLOAD_SMART_SCHEDULE,
            "xList",
            "priceList",
            "pvPowerList",
            "homeList",
        ),
        translation_key="smart_schedule_points",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=("profit",),
        key="smart_schedule_profit",
        value_fn=lambda e: _get_payload_section(e, PAYLOAD_SMART_SCHEDULE, "profit"),
        translation_key="smart_schedule_profit",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=("days",),
        key="smart_schedule_days",
        value_fn=lambda e: _get_payload_section(e, PAYLOAD_SMART_SCHEDULE, "days"),
        translation_key="smart_schedule_days",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=("currency",),
        key="smart_schedule_currency",
        value_fn=lambda e: _get_payload_section(e, PAYLOAD_SMART_SCHEDULE, "currency"),
        translation_key="smart_schedule_currency",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

DYNAMIC_PRICE_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        app_fields=(FIELD_TODAY_LOW,),
        key="dynamic_price_today_low",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_DYNAMIC_PRICE, FIELD_TODAY_LOW
        ),
        translation_key="dynamic_price_today_low",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_TODAY_HIGH,),
        key="dynamic_price_today_high",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_DYNAMIC_PRICE, FIELD_TODAY_HIGH
        ),
        translation_key="dynamic_price_today_high",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_NEXTDAY_LOW,),
        key="dynamic_price_nextday_low",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_DYNAMIC_PRICE, FIELD_NEXTDAY_LOW
        ),
        translation_key="dynamic_price_nextday_low",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_NEXTDAY_HIGH,),
        key="dynamic_price_nextday_high",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_DYNAMIC_PRICE, FIELD_NEXTDAY_HIGH
        ),
        translation_key="dynamic_price_nextday_high",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PRICE_COMPANY_NAME,),
        key="dynamic_price_provider",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_DYNAMIC_PRICE, FIELD_PRICE_COMPANY_NAME
        ),
        translation_key="dynamic_price_provider",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IS_CONTRACT_AUTH,),
        key="dynamic_price_contract_auth",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_DYNAMIC_PRICE, FIELD_IS_CONTRACT_AUTH
        ),
        translation_key="dynamic_price_contract_auth",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

TOU_PLAN_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        key="tou_plan_tasks",
        value_fn=lambda e: _get_first_list_count(e, PAYLOAD_TOU_SCHEDULE, "tasks"),
        translation_key="tou_plan_tasks",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

PORTABLE_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        app_fields=(FIELD_IAC,),
        key="ac_input_current",
        value_fn=lambda e: _get_prop(e, FIELD_IAC),
        translation_key="ac_input_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IACPW,),
        key="ac_input_power",
        value_fn=lambda e: _get_prop(e, FIELD_IACPW),
        translation_key="ac_input_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CIP,),
        key="charging_input_power",
        value_fn=lambda e: _get_prop(e, FIELD_CIP),
        translation_key="charging_input_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACOV,),
        key="ac_output_voltage",
        value_fn=lambda e: _get_prop(e, FIELD_ACOV),
        translation_key="ac_output_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACOHZ,),
        key="ac_output_frequency",
        value_fn=lambda e: _get_prop(e, FIELD_ACOHZ),
        translation_key="ac_output_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACPS,),
        key="ac_output_apparent_power",
        value_fn=lambda e: _get_prop(e, FIELD_ACPS),
        translation_key="ac_output_apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OAC, FIELD_OACPW),
        key="ac_output_power",
        value_fn=lambda e: _get_prop_any(e, FIELD_OACPW, FIELD_OAC),
        translation_key="ac_output_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OAC2,),
        key="ac_output_power_2",
        value_fn=lambda e: _get_prop(e, FIELD_OAC2),
        translation_key="ac_output_power_2",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACT,),
        key="ac_output_current",
        value_fn=lambda e: _get_prop(e, FIELD_OACT),
        translation_key="ac_output_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACMODE,),
        key="ac_output_mode",
        value_fn=lambda e: _get_prop(e, FIELD_ACMODE),
        translation_key="ac_output_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODC_PORT,),
        key="dc_output_power",
        value_fn=lambda e: _get_prop(e, FIELD_ODC_PORT),
        translation_key="dc_output_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCC,),
        key="dc_output_current",
        value_fn=lambda e: _get_prop(e, FIELD_ODCC),
        translation_key="dc_output_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCU,),
        key="dc_output_voltage",
        value_fn=lambda e: _get_prop(e, FIELD_ODCU),
        translation_key="dc_output_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_USBA1,),
        key="usb_a1_power",
        value_fn=lambda e: _get_prop(e, FIELD_USBA1),
        translation_key="usb_a1_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_USBA2,),
        key="usb_a2_power",
        value_fn=lambda e: _get_prop(e, FIELD_USBA2),
        translation_key="usb_a2_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_USBC1,),
        key="usb_c1_power",
        value_fn=lambda e: _get_prop(e, FIELD_USBC1),
        translation_key="usb_c1_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_USBC2,),
        key="usb_c2_power",
        value_fn=lambda e: _get_prop(e, FIELD_USBC2),
        translation_key="usb_c2_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_USBA3,),
        key="usb_a3_power",
        value_fn=lambda e: _get_prop(e, FIELD_USBA3),
        translation_key="usb_a3_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_USBC3,),
        key="usb_c3_power",
        value_fn=lambda e: _get_prop(e, FIELD_USBC3),
        translation_key="usb_c3_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACL1,),
        key="ac_line1_current",
        value_fn=lambda e: _get_prop(e, FIELD_OACL1),
        translation_key="ac_line1_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACL1_PW,),
        key="ac_line1_power",
        value_fn=lambda e: _get_prop(e, FIELD_OACL1_PW),
        translation_key="ac_line1_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACL2,),
        key="ac_line2_current",
        value_fn=lambda e: _get_prop(e, FIELD_OACL2),
        translation_key="ac_line2_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACL2_PW,),
        key="ac_line2_power",
        value_fn=lambda e: _get_prop(e, FIELD_OACL2_PW),
        translation_key="ac_line2_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACT1,),
        key="ac_output_current_1",
        value_fn=lambda e: _get_prop(e, FIELD_OACT1),
        translation_key="ac_output_current_1",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACT2,),
        key="ac_output_current_2",
        value_fn=lambda e: _get_prop(e, FIELD_OACT2),
        translation_key="ac_output_current_2",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CIP,),
        key="charge_input_power_portable",
        value_fn=lambda e: _get_prop(e, FIELD_CIP),
        translation_key="charge_input_power_portable",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CS,),
        key="charge_status",
        value_fn=lambda e: _get_prop(e, FIELD_CS),
        translation_key="charge_status",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CSC,),
        key="charge_status_code",
        value_fn=lambda e: _get_prop(e, FIELD_CSC),
        translation_key="charge_status_code",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CSL,),
        key="charge_status_limit",
        value_fn=lambda e: _get_prop(e, FIELD_CSL),
        translation_key="charge_status_limit",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CST,),
        key="charge_status_type",
        value_fn=lambda e: _get_prop(e, FIELD_CST),
        translation_key="charge_status_type",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PC,),
        key="power_count",
        value_fn=lambda e: _get_prop(e, FIELD_PC),
        translation_key="power_count",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PM,),
        key="power_mode_portable",
        value_fn=lambda e: _get_prop(e, FIELD_PM),
        translation_key="power_mode_portable",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PMB,),
        key="power_mode_battery",
        value_fn=lambda e: _get_prop(e, FIELD_PMB),
        translation_key="power_mode_battery",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_DHG_RECALL,),
        key="dhg_recall",
        value_fn=lambda e: _get_prop(e, FIELD_DHG_RECALL),
        translation_key="dhg_recall",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IT,),
        key="input_temperature",
        value_fn=lambda e: _div(10)(_get_prop(e, FIELD_IT)),
        translation_key="input_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OT,),
        key="output_temperature",
        value_fn=lambda e: _div(10)(_get_prop(e, FIELD_OT)),
        translation_key="output_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BT,),
        key="battery_temperature",
        value_fn=lambda e: _div(10)(_get_prop(e, FIELD_BT)),
        translation_key="battery_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IACPW, FIELD_IP),
        key="input_power_portable",
        value_fn=lambda e: (
            (_get_prop(e, FIELD_IP))
            or (_get_payload_section(e, PAYLOAD_PROPERTIES, FIELD_IACPW))
        ),
        translation_key="input_power_portable",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACPW, FIELD_OP),
        key="output_power_portable",
        value_fn=lambda e: (
            (_get_prop(e, FIELD_OP))
            or (_get_payload_section(e, PAYLOAD_PROPERTIES, FIELD_OACPW))
        ),
        translation_key="output_power_portable",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_EC,),
        key="error_code",
        value_fn=lambda e: _get_prop(e, FIELD_EC),
        translation_key="error_code",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_RB,),
        key="remaining_runtime",
        value_fn=lambda e: _get_prop(e, FIELD_RB),
        translation_key="remaining_runtime",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BC,),
        key="battery_count",
        value_fn=lambda e: _get_prop(e, FIELD_BC),
        translation_key="battery_count",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BLS,),
        key="battery_low_state",
        value_fn=lambda e: _get_prop(e, FIELD_BLS),
        translation_key="battery_low_state",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CL,),
        key="charge_limit",
        value_fn=lambda e: _get_prop(e, FIELD_CL),
        translation_key="charge_limit",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_DL,),
        key="discharge_limit",
        value_fn=lambda e: _get_prop(e, FIELD_DL),
        translation_key="discharge_limit",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PM,),
        key="power_mode",
        value_fn=lambda e: _get_prop(e, FIELD_PM),
        translation_key="power_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PSS,),
        key="power_source_selector",
        value_fn=lambda e: _get_prop(e, FIELD_PSS),
        translation_key="power_source_selector",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_UPS,),
        key="ups_mode",
        value_fn=lambda e: _get_prop(e, FIELD_UPS),
        translation_key="ups_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WSS,),
        key="wifi_switch_status",
        value_fn=lambda e: _get_prop(e, FIELD_WSS),
        translation_key="wifi_switch_status",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_AST,),
        key="auto_standby_timer",
        value_fn=lambda e: _get_prop(e, FIELD_AST),
        translation_key="auto_standby_timer",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IS_PACK_CONNECT,),
        key="external_pack_connected",
        value_fn=lambda e: _get_prop(e, FIELD_IS_PACK_CONNECT),
        translation_key="external_pack_connected",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WSIG,),
        key="wifi_signal_portable",
        value_fn=lambda e: _get_prop(e, FIELD_WSIG),
        translation_key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WNAME,),
        key="wifi_ssid",
        value_fn=lambda e: _get_prop(e, FIELD_WNAME),
        translation_key="wifi_ssid",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WIP,),
        key="wifi_ip",
        value_fn=lambda e: _get_prop(e, FIELD_WIP),
        translation_key="wifi_ip",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAC,),
        key="mac_address",
        value_fn=lambda e: _get_prop(e, FIELD_MAC),
        translation_key="mac_address",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OAC1_NAME,),
        key="ac1_name",
        value_fn=lambda e: _get_prop(e, FIELD_OAC1_NAME),
        translation_key="ac1_name",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OAC2_NAME,),
        key="ac2_name",
        value_fn=lambda e: _get_prop(e, FIELD_OAC2_NAME),
        translation_key="ac2_name",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCC,),
        key="dc_output_config",
        value_fn=lambda e: _get_prop(e, FIELD_ODCC),
        translation_key="dc_output_config",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCCT,),
        key="dc_type",
        value_fn=lambda e: _get_prop(e, FIELD_ODCCT),
        translation_key="dc_type",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCT,),
        key="dc_output_type",
        value_fn=lambda e: _get_prop(e, FIELD_ODCT),
        translation_key="dc_output_type",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCU,),
        key="dc_usb_connected",
        value_fn=lambda e: _get_prop(e, FIELD_ODCU),
        translation_key="dc_usb_connected",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCUT,),
        key="dc_usb_type",
        value_fn=lambda e: _get_prop(e, FIELD_ODCUT),
        translation_key="dc_usb_type",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BPC,),
        key="battery_pack_count",
        value_fn=lambda e: _get_prop(e, FIELD_BPC),
        translation_key="battery_pack_count",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BOX,),
        key="box_mode",
        value_fn=lambda e: _get_prop(e, FIELD_BOX),
        translation_key="box_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PAL,),
        key="light_sensor",
        value_fn=lambda e: _get_prop(e, FIELD_PAL),
        translation_key="light_sensor",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SFC,),
        key="sleep_mode_flag",
        value_fn=lambda e: _get_prop(e, FIELD_SFC),
        translation_key="sleep_mode_flag",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SLTB,),
        key="sltb_value",
        value_fn=lambda e: _get_prop(e, FIELD_SLTB),
        translation_key="sltb_value",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_TA,),
        key="ambient_temperature",
        value_fn=lambda e: _get_prop(e, FIELD_TA),
        translation_key="ambient_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_TP,),
        key="panel_temperature",
        value_fn=lambda e: _get_prop(e, FIELD_TP),
        translation_key="panel_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACCD,),
        key="ac_discharge_current",
        value_fn=lambda e: _get_prop(e, FIELD_ACCD),
        translation_key="ac_discharge_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACDT,),
        key="ac_discharge_time",
        value_fn=lambda e: _get_prop(e, FIELD_ACDT),
        translation_key="ac_discharge_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACIP,),
        key="ac_input_power_alt",
        value_fn=lambda e: _get_prop(e, FIELD_ACIP),
        translation_key="ac_input_power_alt",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACPSP,),
        key="ac_output_apparent_power_parallel",
        value_fn=lambda e: _get_prop(e, FIELD_ACPSP),
        translation_key="ac_output_apparent_power_parallel",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACPSS,),
        key="ac_output_apparent_power_sum",
        value_fn=lambda e: _get_prop(e, FIELD_ACPSS),
        translation_key="ac_output_apparent_power_sum",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_COP,),
        key="charge_output_power",
        value_fn=lambda e: _get_prop(e, FIELD_COP),
        translation_key="charge_output_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_DT,),
        key="discharge_time",
        value_fn=lambda e: _get_prop(e, FIELD_DT),
        translation_key="discharge_time",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_LM,),
        key="load_mode",
        value_fn=lambda e: _get_prop(e, FIELD_LM),
        translation_key="load_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IPAL_PW,),
        key="input_power_allocation",
        value_fn=lambda e: _get_prop(e, FIELD_IPAL_PW),
        translation_key="input_power_allocation",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OPAL_PW,),
        key="output_power_allocation",
        value_fn=lambda e: _get_prop(e, FIELD_OPAL_PW),
        translation_key="output_power_allocation",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SS,),
        key="system_status_switch",
        value_fn=lambda e: _get_prop(e, FIELD_SS),
        translation_key="system_status_switch",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACOV1,),
        key="ac_output_voltage_2",
        value_fn=lambda e: _get_prop(e, FIELD_ACOV1),
        translation_key="ac_output_voltage_2",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_TT,),
        key="total_time",
        value_fn=lambda e: _get_prop(e, FIELD_TT),
        translation_key="total_time",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

SAVINGS_DETAIL_SENSOR_DESCRIPTIONS: tuple[
    JackerySavingsDetailSensorDescription, ...
] = (
    JackerySavingsDetailSensorDescription(
        key="savings_calculated_total",
        path=("calculated_total",),
        translation_key="savings_calculated_total",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        value_fn=lambda e: safe_float(e.get_savings_value("calculated_total")),
    ),
    JackerySavingsDetailSensorDescription(
        key="savings_energy",
        path=("energy_kwh",),
        translation_key="savings_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: safe_float(e.get_savings_value("energy_kwh")),
    ),
    JackerySavingsDetailSensorDescription(
        key="savings_price",
        path=("price",),
        translation_key="savings_price",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=f"{CURRENCY_EURO}/kWh",
        value_fn=lambda e: safe_float(e.get_savings_value("price")),
    ),
    JackerySavingsDetailSensorDescription(
        key="savings_battery_loss_year_energy",
        path=("source_energy", "battery_charge_discharge_balance_year_kwh"),
        translation_key="savings_battery_balance_year_energy",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: safe_float(
            e.get_savings_value((
                "source_energy",
                "battery_charge_discharge_balance_year_kwh",
            ))
        ),
    ),
    JackerySavingsDetailSensorDescription(
        key="savings_conversion_loss_year_energy",
        path=("source_energy", "conversion_loss_year_kwh"),
        translation_key="savings_conversion_loss_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: safe_float(
            e.get_savings_value(("source_energy", "conversion_loss_year_kwh"))
        ),
    ),
    JackerySavingsDetailSensorDescription(
        key="savings_pv_residual_year_energy",
        path=("source_energy", "pv_residual_after_self_consumption_year_kwh"),
        translation_key="savings_pv_residual_year_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: safe_float(
            e.get_savings_value((
                "source_energy",
                "pv_residual_after_self_consumption_year_kwh",
            ))
        ),
    ),
)

BATTERY_PACK_SENSOR_DESCRIPTIONS: tuple[JackeryBatteryPackSensorDescription, ...] = (
    JackeryBatteryPackSensorDescription(
        key="soc",
        field=FIELD_BAT_SOC,
        translation_key="battery_pack_soc",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_BAT_SOC)),
    ),
    JackeryBatteryPackSensorDescription(
        key="cell_temperature",
        field=FIELD_CELL_TEMP,
        transform=_div(10),
        translation_key="battery_pack_cell_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda e: _div(10)(safe_float(_get_prop(e, FIELD_CELL_TEMP))),
    ),
    JackeryBatteryPackSensorDescription(
        key="charge_power",
        field=FIELD_IN_PW,
        translation_key="battery_pack_charge_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_IN_PW)),
    ),
    JackeryBatteryPackSensorDescription(
        key="discharge_power",
        field=FIELD_OUT_PW,
        translation_key="battery_pack_discharge_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_OUT_PW)),
    ),
    JackeryBatteryPackSensorDescription(
        key="firmware_version",
        field=FIELD_VERSION,
        translation_key="battery_pack_firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _get_prop(e, FIELD_VERSION),
    ),
    JackeryBatteryPackSensorDescription(
        key="serial_number",
        field=FIELD_DEVICE_SN,
        translation_key="battery_pack_serial_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _get_prop(e, FIELD_DEVICE_SN),
    ),
    JackeryBatteryPackSensorDescription(
        key="communication_state",
        field=FIELD_COMM_STATE,
        transform=safe_int,
        translation_key="battery_pack_communication_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_COMM_STATE)),
    ),
    JackeryBatteryPackSensorDescription(
        key="update_status",
        field=FIELD_UPDATE_STATUS,
        transform=safe_int,
        translation_key="battery_pack_update_status",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_UPDATE_STATUS)),
    ),
    JackeryBatteryPackSensorDescription(
        key="lifetime_charge_energy",
        field=FIELD_IN_EGY,
        transform=_div(100),
        translation_key="battery_pack_lifetime_charge_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH)(
            safe_float(_get_prop(e, FIELD_IN_EGY))
        ),
    ),
    JackeryBatteryPackSensorDescription(
        key="lifetime_discharge_energy",
        field=FIELD_OUT_EGY,
        transform=_div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH),
        translation_key="battery_pack_lifetime_discharge_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH)(
            safe_float(_get_prop(e, FIELD_OUT_EGY))
        ),
    ),
)

SMART_PLUG_SENSOR_DESCRIPTIONS: tuple[JackerySmartPlugSensorDescription, ...] = (
    JackerySmartPlugSensorDescription(
        key="input_power",
        field=FIELD_IN_PW,
        transform=safe_int,
        translation_key="smart_plug_input_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_IN_PW)),
    ),
    JackerySmartPlugSensorDescription(
        key="output_power",
        field=FIELD_OUT_PW,
        transform=safe_int,
        translation_key="smart_plug_output_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_OUT_PW)),
    ),
    JackerySmartPlugSensorDescription(
        key="communication_state",
        field=FIELD_COMM_STATE,
        transform=safe_int,
        translation_key="smart_plug_communication_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_COMM_STATE)),
    ),
    JackerySmartPlugSensorDescription(
        key="priority",
        field=FIELD_SOCKET_PRIORITY,
        transform=safe_int,
        translation_key="smart_plug_priority",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_SOCKET_PRIORITY)),
    ),
    JackerySmartPlugSensorDescription(
        key="firmware_version",
        field=FIELD_VERSION,
        translation_key="smart_plug_firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _get_prop(e, FIELD_VERSION),
    ),
    JackerySmartPlugSensorDescription(
        key="today_energy",
        field=FIELD_TODAY_ENERGY,
        transform=safe_float,
        translation_key="smart_plug_today_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_TODAY_ENERGY)),
    ),
    JackerySmartPlugSensorDescription(
        key="total_energy",
        field=FIELD_TOTAL_ENERGY,
        transform=safe_float,
        translation_key="smart_plug_total_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_TOTAL_ENERGY)),
    ),
    JackerySmartPlugSensorDescription(
        key="communication_mode",
        field=FIELD_COMM_MODE,
        translation_key="smart_plug_communication_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_COMM_MODE),
    ),
    JackerySmartPlugSensorDescription(
        key="ip_address",
        field=FIELD_IP,
        translation_key="smart_plug_ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_IP),
    ),
    JackerySmartPlugSensorDescription(
        key="mac_address",
        field=FIELD_MAC,
        translation_key="smart_plug_mac_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_MAC),
    ),
    JackerySmartPlugSensorDescription(
        key="switch_cycle",
        field=FIELD_SOCKET_SWITCH_CYCLE,
        transform=safe_int,
        translation_key="smart_plug_switch_cycle",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_SOCKET_SWITCH_CYCLE)),
    ),
    JackerySmartPlugSensorDescription(
        key="last_update_ts",
        field=FIELD_SOCKET_LAST_UPDATE_TS,
        transform=safe_int,
        translation_key="smart_plug_last_update_ts",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_SOCKET_LAST_UPDATE_TS)),
    ),
)

BREAKER_SENSOR_DESCRIPTIONS: tuple[JackeryBreakerSensorDescription, ...] = (
    JackeryBreakerSensorDescription(
        key="pc",
        field=FIELD_PC,
        transform=safe_int,
        translation_key="breaker_pc",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_PC)),
    ),
    JackeryBreakerSensorDescription(
        key="pr",
        field=FIELD_PR,
        transform=safe_int,
        translation_key="breaker_pr",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_PR)),
    ),
    JackeryBreakerSensorDescription(
        key="sph",
        field=FIELD_SPH,
        transform=safe_int,
        translation_key="breaker_sph",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_SPH)),
    ),
    JackeryBreakerSensorDescription(
        key="sph_pc",
        field=FIELD_SPH_PC,
        transform=safe_int,
        translation_key="breaker_sph_pc",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_SPH_PC)),
    ),
)

SUBDEVICE_ALARM_SENSOR_DESCRIPTIONS: tuple[
    JackerySubdeviceAlarmSensorDescription, ...
] = (
    JackerySubdeviceAlarmSensorDescription(
        key="alert_count",
        field=FIELD_ALERT_COUNT,
        transform=safe_int,
        translation_key="subdevice_alert_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_ALERT_COUNT)),
    ),
)

METER_HEAD_SENSOR_DESCRIPTIONS: tuple[JackeryMeterHeadSensorDescription, ...] = (
    JackeryMeterHeadSensorDescription(
        key="input_power",
        field=FIELD_IN_PW,
        transform=safe_int,
        translation_key="meter_head_input_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_IN_PW)),
    ),
    JackeryMeterHeadSensorDescription(
        key="output_power",
        field=FIELD_OUT_PW,
        transform=safe_int,
        translation_key="meter_head_output_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_OUT_PW)),
    ),
    JackeryMeterHeadSensorDescription(
        key="communication_state",
        field=FIELD_COMM_STATE,
        transform=safe_int,
        translation_key="meter_head_communication_state",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_COMM_STATE)),
    ),
    JackeryMeterHeadSensorDescription(
        key="charging_energy",
        field=FIELD_CHARGING_ENERGY,
        transform=safe_float,
        translation_key="meter_head_charging_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_CHARGING_ENERGY)),
    ),
    JackeryMeterHeadSensorDescription(
        key="discharging_energy",
        field=FIELD_DISCHARGING_ENERGY,
        transform=safe_float,
        translation_key="meter_head_discharging_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_DISCHARGING_ENERGY)),
    ),
    JackeryMeterHeadSensorDescription(
        key="communication_mode",
        field=FIELD_COMM_MODE,
        translation_key="meter_head_communication_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_COMM_MODE),
    ),
    JackeryMeterHeadSensorDescription(
        key="ip_address",
        field=FIELD_IP,
        translation_key="meter_head_ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_IP),
    ),
    JackeryMeterHeadSensorDescription(
        key="mac_address",
        field=FIELD_MAC,
        translation_key="meter_head_mac_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_MAC),
    ),
)

SMART_METER_SENSOR_DESCRIPTIONS: tuple[JackerySmartMeterSensorDescription, ...] = (
    JackerySmartMeterSensorDescription(
        key="power",
        field=FIELD_CT_POWER,
        aliases=(CT_TOTAL_POWER_PAIR[0],),
        negative_aliases=(CT_TOTAL_POWER_PAIR[1],),
        sum_fields=CT_POSITIVE_PHASE_POWER_FIELDS,
        negative_sum_fields=CT_NEGATIVE_PHASE_POWER_FIELDS,
        translation_key="smart_meter_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="net_import_power",
        field=FIELD_CT_POWER,
        calculation="net_import",
        translation_key="smart_meter_net_import_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="net_export_power",
        field=FIELD_CT_POWER,
        calculation="net_export",
        translation_key="smart_meter_net_export_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="grid_import_energy",
        field=FIELD_CT_TOTAL_PHASE_ENERGY,
        sum_fields=(
            FIELD_CT_A_PHASE_ENERGY,
            FIELD_CT_B_PHASE_ENERGY,
            FIELD_CT_C_PHASE_ENERGY,
        ),
        transform=_div(1000),
        translation_key="smart_meter_grid_import_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="grid_export_energy",
        field=FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
        sum_fields=(
            FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
            FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
            FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
        ),
        transform=_div(1000),
        translation_key="smart_meter_grid_export_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="gross_phase_import_power",
        field=FIELD_CT_POWER,
        calculation="gross_import",
        translation_key="smart_meter_gross_phase_import_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="gross_phase_export_power",
        field=FIELD_CT_POWER,
        calculation="gross_export",
        translation_key="smart_meter_gross_phase_export_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="gross_phase_flow_power",
        field=FIELD_CT_POWER,
        calculation="gross_flow",
        translation_key="smart_meter_gross_phase_flow_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_power",
        field=FIELD_CT_POWER1,
        aliases=(CT_POSITIVE_PHASE_POWER_FIELDS[0],),
        negative_aliases=(CT_NEGATIVE_PHASE_POWER_FIELDS[0],),
        translation_key="smart_meter_phase_1_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_power",
        field=FIELD_CT_POWER2,
        aliases=(CT_POSITIVE_PHASE_POWER_FIELDS[1],),
        negative_aliases=(CT_NEGATIVE_PHASE_POWER_FIELDS[1],),
        translation_key="smart_meter_phase_2_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_power",
        field=FIELD_CT_POWER3,
        aliases=(CT_POSITIVE_PHASE_POWER_FIELDS[2],),
        negative_aliases=(CT_NEGATIVE_PHASE_POWER_FIELDS[2],),
        translation_key="smart_meter_phase_3_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="lifetime_import_energy",
        field=FIELD_CT_TOTAL_PHASE_ENERGY,
        aliases=(FIELD_CT_TOTAL_PHASE_ENERGY,),
        sum_fields=(
            FIELD_CT_A_PHASE_ENERGY,
            FIELD_CT_B_PHASE_ENERGY,
            FIELD_CT_C_PHASE_ENERGY,
        ),
        transform=_div(1000),
        translation_key="smart_meter_lifetime_import_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="lifetime_export_energy",
        field=FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
        aliases=(FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,),
        sum_fields=(
            FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
            FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
            FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
        ),
        transform=_div(1000),
        translation_key="smart_meter_lifetime_export_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_lifetime_import_energy",
        field=FIELD_CT_A_PHASE_ENERGY,
        transform=_div(1000),
        translation_key="smart_meter_phase_1_lifetime_import_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_lifetime_import_energy",
        field=FIELD_CT_B_PHASE_ENERGY,
        transform=_div(1000),
        translation_key="smart_meter_phase_2_lifetime_import_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_lifetime_import_energy",
        field=FIELD_CT_C_PHASE_ENERGY,
        transform=_div(1000),
        translation_key="smart_meter_phase_3_lifetime_import_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_lifetime_export_energy",
        field=FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
        transform=_div(1000),
        translation_key="smart_meter_phase_1_lifetime_export_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_lifetime_export_energy",
        field=FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
        transform=_div(1000),
        translation_key="smart_meter_phase_2_lifetime_export_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_lifetime_export_energy",
        field=FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
        transform=_div(1000),
        translation_key="smart_meter_phase_3_lifetime_export_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="voltage",
        field=FIELD_CT_VOLT,
        translation_key="smart_meter_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_voltage",
        field=FIELD_CT_VOLT1,
        translation_key="smart_meter_phase_1_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_voltage",
        field=FIELD_CT_VOLT2,
        translation_key="smart_meter_phase_2_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_voltage",
        field=FIELD_CT_VOLT3,
        translation_key="smart_meter_phase_3_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_current",
        field=FIELD_CT_CURRENT1,
        translation_key="smart_meter_phase_1_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_current",
        field=FIELD_CT_CURRENT2,
        translation_key="smart_meter_phase_2_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_current",
        field=FIELD_CT_CURRENT3,
        translation_key="smart_meter_phase_3_current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="frequency",
        field=FIELD_CT_FREQUENCY,
        translation_key="smart_meter_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="power_factor",
        field=FIELD_CT_POWER_FACTOR,
        translation_key="smart_meter_power_factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_power_factor",
        field=FIELD_CT_POWER_FACTOR1,
        translation_key="smart_meter_phase_1_power_factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_power_factor",
        field=FIELD_CT_POWER_FACTOR2,
        translation_key="smart_meter_phase_2_power_factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_power_factor",
        field=FIELD_CT_POWER_FACTOR3,
        translation_key="smart_meter_phase_3_power_factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="apparent_power",
        field=FIELD_CT_APPARENT_POWER,
        translation_key="smart_meter_apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_apparent_power",
        field=FIELD_CT_APPARENT_POWER1,
        translation_key="smart_meter_phase_1_apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_apparent_power",
        field=FIELD_CT_APPARENT_POWER2,
        translation_key="smart_meter_phase_2_apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_apparent_power",
        field=FIELD_CT_APPARENT_POWER3,
        translation_key="smart_meter_phase_3_apparent_power",
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="reactive_power",
        field=FIELD_CT_REACTIVE_POWER,
        translation_key="smart_meter_reactive_power",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_1_reactive_power",
        field=FIELD_CT_REACTIVE_POWER1,
        translation_key="smart_meter_phase_1_reactive_power",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_2_reactive_power",
        field=FIELD_CT_REACTIVE_POWER2,
        translation_key="smart_meter_phase_2_reactive_power",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="phase_3_reactive_power",
        field=FIELD_CT_REACTIVE_POWER3,
        translation_key="smart_meter_phase_3_reactive_power",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        key="communication_mode",
        field=FIELD_COMM_MODE,
        translation_key="smart_meter_communication_mode",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_COMM_MODE),
    ),
    JackerySmartMeterSensorDescription(
        key="ip_address",
        field=FIELD_IP,
        translation_key="smart_meter_ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_IP),
    ),
    JackerySmartMeterSensorDescription(
        key="mac_address",
        field=FIELD_MAC,
        fallback_fields=(FIELD_DEVICE_SN, FIELD_DEVICE_ID),
        translation_key="smart_meter_mac_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop_any(
            e, FIELD_MAC, FIELD_DEVICE_SN, FIELD_DEVICE_ID
        ),
    ),
    JackerySmartMeterSensorDescription(
        key="fun_form",
        field=FIELD_CT_FUN_FORM,
        translation_key="smart_meter_fun_form",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
)
