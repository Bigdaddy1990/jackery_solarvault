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
        # pyrefly: ignore [unexpected-keyword]
        key="soc",
        value_fn=lambda e: _get_prop(e, FIELD_SOC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_soc",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.BATTERY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BAT_SOC,),
        # pyrefly: ignore [unexpected-keyword]
        key="bat_soc",
        value_fn=lambda e: _get_prop(e, FIELD_BAT_SOC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_soc_internal",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.BATTERY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CELL_TEMP,),
        # pyrefly: ignore [unexpected-keyword]
        key="cell_temperature",
        value_fn=lambda e: _div(10)(_get_prop(e, FIELD_CELL_TEMP)),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="cell_temperature",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.TEMPERATURE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BAT_IN_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="battery_charge_power",
        value_fn=lambda e: (
            (_get_prop(e, FIELD_BAT_IN_PW))
            or (_get_payload_http_prop(e, FIELD_BAT_IN_PW))
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_charge_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BAT_OUT_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="battery_discharge_power",
        value_fn=lambda e: (
            (_get_prop(e, FIELD_BAT_OUT_PW))
            or (_get_payload_http_prop(e, FIELD_BAT_OUT_PW))
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_discharge_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PV_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="pv_power_total",
        value_fn=lambda e: _get_prop(e, FIELD_PV_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv_power_total",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PV1,),
        # pyrefly: ignore [unexpected-keyword]
        key="pv1_power",
        value_fn=lambda e: _get_pv_channel_power(e, FIELD_PV1),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv1_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PV2,),
        # pyrefly: ignore [unexpected-keyword]
        key="pv2_power",
        value_fn=lambda e: _get_pv_channel_power(e, FIELD_PV2),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv2_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PV3,),
        # pyrefly: ignore [unexpected-keyword]
        key="pv3_power",
        value_fn=lambda e: _get_pv_channel_power(e, FIELD_PV3),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv3_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PV4,),
        # pyrefly: ignore [unexpected-keyword]
        key="pv4_power",
        value_fn=lambda e: _get_pv_channel_power(e, FIELD_PV4),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv4_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IN_GRID_SIDE_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="grid_in_power",
        value_fn=lambda e: _get_prop(e, FIELD_IN_GRID_SIDE_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="grid_in_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OUT_GRID_SIDE_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="grid_out_power",
        value_fn=lambda e: _get_prop(e, FIELD_OUT_GRID_SIDE_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="grid_out_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="inverter_ac_input_power",
        app_fields=(FIELD_GRID_IN_PW, FIELD_IN_ONGRID_PW),
        getter=jackery_inverter_ac_input_power,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="inverter_ac_input_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="inverter_ac_output_power",
        app_fields=(FIELD_GRID_OUT_PW, FIELD_OUT_ONGRID_PW),
        getter=jackery_inverter_ac_output_power,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="inverter_ac_output_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SW_EPS_IN_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="eps_in_power",
        value_fn=lambda e: _get_prop(e, FIELD_SW_EPS_IN_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eps_in_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SW_EPS_OUT_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="eps_out_power",
        value_fn=lambda e: _get_prop(e, FIELD_SW_EPS_OUT_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eps_out_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_STACK_IN_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="stack_in_power",
        value_fn=lambda e: _get_prop(e, FIELD_STACK_IN_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="stack_in_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_STACK_OUT_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="stack_out_power",
        value_fn=lambda e: _get_prop(e, FIELD_STACK_OUT_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="stack_out_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WSIG,),
        # pyrefly: ignore [unexpected-keyword]
        key="wifi_signal",
        value_fn=lambda e: _get_prop(e, FIELD_WSIG),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="wifi_signal",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WNAME,),
        # pyrefly: ignore [unexpected-keyword]
        key="wifi_name",
        value_fn=lambda e: _get_prop(e, FIELD_WNAME),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="wifi_name",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="wifi_ip",
        value_fn=lambda e: _get_prop(e, FIELD_WIP),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="wifi_ip",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="mac_address",
        value_fn=lambda e: _get_prop(e, FIELD_MAC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="mac_address",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ETH_PORT,),
        # pyrefly: ignore [unexpected-keyword]
        key="eth_port",
        value_fn=lambda e: _get_prop(e, FIELD_ETH_PORT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eth_port",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ABILITY,),
        # pyrefly: ignore [unexpected-keyword]
        key="ability_bits",
        value_fn=lambda e: _get_prop(e, FIELD_ABILITY),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ability_bits",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAX_IOT_NUM,),
        # pyrefly: ignore [unexpected-keyword]
        key="max_iot_num",
        value_fn=lambda e: _get_prop(e, FIELD_MAX_IOT_NUM),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="max_iot_num",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SW_EPS_STATE,),
        # pyrefly: ignore [unexpected-keyword]
        key="eps_switch_state",
        value_fn=lambda e: _get_prop(e, FIELD_SW_EPS_STATE),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eps_switch_state",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_REBOOT,),
        # pyrefly: ignore [unexpected-keyword]
        key="reboot_flag",
        value_fn=lambda e: _get_prop(e, FIELD_REBOOT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="reboot_flag",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SOC_CHARGE_LIMIT, FIELD_SOC_CHG_LIMIT),
        # pyrefly: ignore [unexpected-keyword]
        key="soc_charge_limit",
        value_fn=lambda e: _get_prop_any(
            e, FIELD_SOC_CHG_LIMIT, FIELD_SOC_CHARGE_LIMIT
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="soc_charge_limit",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SOC_DISCHARGE_LIMIT, FIELD_SOC_DISCHG_LIMIT),
        # pyrefly: ignore [unexpected-keyword]
        key="soc_discharge_limit",
        value_fn=lambda e: _get_prop_any(
            e, FIELD_SOC_DISCHG_LIMIT, FIELD_SOC_DISCHARGE_LIMIT
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="soc_discharge_limit",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAX_OUT_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="max_output_power",
        value_fn=lambda e: _get_prop(e, FIELD_MAX_OUT_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="max_output_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAX_INV_STD_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="max_inverter_power",
        value_fn=lambda e: _get_prop(e, FIELD_MAX_INV_STD_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="max_inverter_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAX_GRID_STD_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="max_grid_standard_power",
        value_fn=lambda e: _get_prop(e, FIELD_MAX_GRID_STD_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="max_grid_standard_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_EIP,),
        # pyrefly: ignore [unexpected-keyword]
        key="ethernet_ip",
        value_fn=lambda e: _get_prop_or_disconnected(e, FIELD_EIP),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ethernet_ip",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ETH_PORT,),
        # pyrefly: ignore [unexpected-keyword]
        key="ethernet_port",
        value_fn=lambda e: _get_prop(e, FIELD_ETH_PORT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ethernet_port",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_EMAC,),
        # pyrefly: ignore [unexpected-keyword]
        key="ethernet_mac",
        value_fn=lambda e: _get_prop_or_disconnected(e, FIELD_EMAC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ethernet_mac",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="battery_count",
        value_fn=lambda e: _get_prop(e, FIELD_BAT_NUM),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_count",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BAT_STATE,),
        # pyrefly: ignore [unexpected-keyword]
        key="battery_state",
        value_fn=lambda e: _get_prop(e, FIELD_BAT_STATE),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_state",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_AUTO_STANDBY,),
        # pyrefly: ignore [unexpected-keyword]
        key="auto_standby",
        value_fn=lambda e: _get_prop(e, FIELD_AUTO_STANDBY),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="auto_standby",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_STAT,),
        # pyrefly: ignore [unexpected-keyword]
        key="system_state",
        value_fn=lambda e: _get_prop(e, FIELD_STAT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="system_state",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ONGRID_STAT, FIELD_ON_GRID_STAT),
        # pyrefly: ignore [unexpected-keyword]
        key="ongrid_state",
        value_fn=lambda e: _get_prop_any(e, FIELD_ONGRID_STAT, FIELD_ON_GRID_STAT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ongrid_state",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CT_STAT, FIELD_CT_STATE),
        # pyrefly: ignore [unexpected-keyword]
        key="ct_state",
        value_fn=lambda e: _get_prop_any(e, FIELD_CT_STAT, FIELD_CT_STATE),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ct_state",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_GRID_STAT, FIELD_GRID_STATE, FIELD_GRID_STATE_ALT),
        # pyrefly: ignore [unexpected-keyword]
        key="grid_state",
        value_fn=lambda e: _get_prop_any(
            e, FIELD_GRID_STATE, FIELD_GRID_STATE_ALT, FIELD_GRID_STAT
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="grid_state",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WORK_MODEL,),
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="work_mode",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAX_SYS_OUT_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="max_system_output_power",
        value_fn=lambda e: _get_prop(e, FIELD_MAX_SYS_OUT_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="max_system_output_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAX_SYS_IN_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="max_system_input_power",
        value_fn=lambda e: _get_prop(e, FIELD_MAX_SYS_IN_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="max_system_input_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OFF_GRID_TIME,),
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="off_grid_time",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTime.MINUTES,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_DEFAULT_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="default_power",
        value_fn=lambda e: _get_prop(e, FIELD_DEFAULT_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="default_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_STANDBY_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="standby_power",
        value_fn=lambda e: _get_prop(e, FIELD_STANDBY_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="standby_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OTHER_LOAD_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="other_load_power",
        value_fn=lambda e: _get_prop(e, FIELD_OTHER_LOAD_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="other_load_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ENERGY_PLAN_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="energy_plan_power",
        value_fn=lambda e: _get_prop(e, FIELD_ENERGY_PLAN_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="energy_plan_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CHARGE_PLAN_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="charge_plan_power",
        value_fn=lambda e: _get_prop(e, FIELD_CHARGE_PLAN_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="charge_plan_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IS_FOLLOW_METER_PW,),
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="follow_meter_state",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OFF_GRID_DOWN,),
        # pyrefly: ignore [unexpected-keyword]
        key="off_grid_shutdown_state",
        value_fn=lambda e: (
            (_get_prop(e, FIELD_OFF_GRID_DOWN))
            or (
                _task_plan_value(
                    (e.payload or {}).get(PAYLOAD_TASK_PLAN) or {}, FIELD_OFF_GRID_DOWN
                )
            )
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="off_grid_shutdown_state",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_FUNC_ENABLE,),
        # pyrefly: ignore [unexpected-keyword]
        key="function_enable_flags",
        value_fn=lambda e: _get_prop(e, FIELD_FUNC_ENABLE),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="function_enable_flags",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_TEMP_UNIT,),
        # pyrefly: ignore [unexpected-keyword]
        key="temp_unit",
        value_fn=lambda e: _get_prop(e, FIELD_TEMP_UNIT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="temp_unit",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WPS,),
        # pyrefly: ignore [unexpected-keyword]
        key="storm_warning_enabled",
        value_fn=_storm_warning_enabled_value,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="storm_warning_enabled",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MINS_INTERVAL, FIELD_WPC),
        # pyrefly: ignore [unexpected-keyword]
        key="storm_warning_minutes",
        value_fn=_storm_warning_minutes_value,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="storm_warning_minutes",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTime.MINUTES,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

STAT_DESCRIPTIONS: tuple[JackeryStatSensorDescription, ...] = (
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="today_load",
        stat_key=APP_STAT_TODAY_LOAD,
        section=PAYLOAD_STATISTIC,
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_STATISTIC, APP_STAT_TODAY_LOAD
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="today_load",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="total_generation",
        stat_key=APP_STAT_TOTAL_GENERATION,
        section=PAYLOAD_STATISTIC,
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_STATISTIC, APP_STAT_TOTAL_GENERATION
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="total_generation",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="total_revenue",
        stat_key=APP_STAT_TOTAL_REVENUE,
        section=PAYLOAD_STATISTIC,
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_STATISTIC, APP_STAT_TOTAL_REVENUE
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="total_revenue",
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=CURRENCY_EURO,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="total_carbon_saved",
        stat_key=APP_STAT_TOTAL_CARBON,
        section=PAYLOAD_STATISTIC,
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_STATISTIC, APP_STAT_TOTAL_CARBON
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="total_carbon_saved",
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="battery_charge_energy",
        stat_key=APP_DEVICE_STAT_BATTERY_CHARGE,
        section=PAYLOAD_PROPERTIES,
        value_fn=lambda e: _div(100)(
            _get_payload_section(e, PAYLOAD_PROPERTIES, APP_DEVICE_STAT_BATTERY_CHARGE)
        ),
        transform=_div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_charge_energy",
        data_sources=ALL_LIVE_DATA_SOURCES,
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="battery_discharge_energy",
        stat_key=APP_DEVICE_STAT_BATTERY_DISCHARGE,
        section=PAYLOAD_PROPERTIES,
        value_fn=lambda e: _div(100)(
            _get_payload_section(
                e, PAYLOAD_PROPERTIES, APP_DEVICE_STAT_BATTERY_DISCHARGE
            )
        ),
        transform=_div(100),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_discharge_energy",
        data_sources=ALL_LIVE_DATA_SOURCES,
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="main_battery_charge_energy",
        stat_key=APP_DEVICE_STAT_BATTERY_CHARGE,
        section=PAYLOAD_PROPERTIES,
        value_fn=lambda e: _div(100)(
            _get_payload_section(e, PAYLOAD_PROPERTIES, APP_DEVICE_STAT_BATTERY_CHARGE)
        ),
        transform=_div(100),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="main_battery_charge_energy",
        data_sources=ALL_LIVE_DATA_SOURCES,
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="main_battery_discharge_energy",
        stat_key=APP_DEVICE_STAT_BATTERY_DISCHARGE,
        section=PAYLOAD_PROPERTIES,
        value_fn=lambda e: _div(100)(
            _get_payload_section(
                e, PAYLOAD_PROPERTIES, APP_DEVICE_STAT_BATTERY_DISCHARGE
            )
        ),
        transform=_div(100),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="main_battery_discharge_energy",
        data_sources=ALL_LIVE_DATA_SOURCES,
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="pv_week_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_SOLAR_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="pv_month_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}", APP_STAT_TOTAL_SOLAR_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="pv_year_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_SOLAR_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="pv_revenue_day",
        stat_key=APP_STAT_TOTAL_SOLAR_REVENUE,
        section=PAYLOAD_PV_TRENDS,
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_PV_TRENDS, APP_STAT_TOTAL_SOLAR_REVENUE
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv_revenue_day",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.MONETARY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=CURRENCY_EURO,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv_revenue_week",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.MONETARY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=CURRENCY_EURO,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="pv_revenue_month",
        stat_key=APP_STAT_TOTAL_SOLAR_REVENUE,
        section=f"{APP_SECTION_PV_TRENDS}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_PV_TRENDS}_{DATE_TYPE_MONTH}",
            APP_STAT_TOTAL_SOLAR_REVENUE,
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv_revenue_month",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.MONETARY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=CURRENCY_EURO,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="pv_revenue_year",
        stat_key=APP_STAT_TOTAL_SOLAR_REVENUE,
        section=f"{APP_SECTION_PV_TRENDS}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_PV_TRENDS}_{DATE_TYPE_YEAR}",
            APP_STAT_TOTAL_SOLAR_REVENUE,
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv_revenue_year",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.MONETARY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=CURRENCY_EURO,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv1_day_energy",
        stat_key=APP_STAT_PV1_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}", APP_STAT_PV1_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv1_day_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv1_week_energy",
        stat_key=APP_STAT_PV1_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}", APP_STAT_PV1_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv1_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv1_month_energy",
        stat_key=APP_STAT_PV1_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}", APP_STAT_PV1_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv1_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv1_year_energy",
        stat_key=APP_STAT_PV1_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}", APP_STAT_PV1_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv1_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv2_day_energy",
        stat_key=APP_STAT_PV2_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}", APP_STAT_PV2_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv2_day_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv2_week_energy",
        stat_key=APP_STAT_PV2_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}", APP_STAT_PV2_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv2_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv2_month_energy",
        stat_key=APP_STAT_PV2_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}", APP_STAT_PV2_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv2_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv2_year_energy",
        stat_key=APP_STAT_PV2_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}", APP_STAT_PV2_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv2_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv3_day_energy",
        stat_key=APP_STAT_PV3_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}", APP_STAT_PV3_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv3_day_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv3_week_energy",
        stat_key=APP_STAT_PV3_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}", APP_STAT_PV3_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv3_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv3_month_energy",
        stat_key=APP_STAT_PV3_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}", APP_STAT_PV3_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv3_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv3_year_energy",
        stat_key=APP_STAT_PV3_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}", APP_STAT_PV3_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv3_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv4_day_energy",
        stat_key=APP_STAT_PV4_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}", APP_STAT_PV4_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv4_day_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv4_week_energy",
        stat_key=APP_STAT_PV4_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}", APP_STAT_PV4_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv4_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv4_month_energy",
        stat_key=APP_STAT_PV4_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}", APP_STAT_PV4_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv4_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_pv4_year_energy",
        stat_key=APP_STAT_PV4_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}", APP_STAT_PV4_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_pv4_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="home_day_energy",
        stat_key=APP_STAT_TOTAL_HOME_ENERGY,
        section=PAYLOAD_HOME_TRENDS,
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_HOME_TRENDS, APP_STAT_TOTAL_HOME_ENERGY
        ),
        transform=safe_float,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="home_day_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="home_week_energy",
        stat_key=APP_STAT_TOTAL_HOME_ENERGY,
        section=f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_HOME_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="home_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="home_month_energy",
        stat_key=APP_STAT_TOTAL_HOME_ENERGY,
        section=f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_MONTH}",
            APP_STAT_TOTAL_HOME_ENERGY,
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="home_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="home_year_energy",
        stat_key=APP_STAT_TOTAL_HOME_ENERGY,
        section=f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_HOME_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="home_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_ongrid_input_week_energy",
        stat_key=APP_STAT_TOTAL_IN_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_WEEK}",
            APP_STAT_TOTAL_IN_GRID_ENERGY,
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_ongrid_input_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_ongrid_input_month_energy",
        stat_key=APP_STAT_TOTAL_IN_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_MONTH}",
            APP_STAT_TOTAL_IN_GRID_ENERGY,
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_ongrid_input_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_ongrid_input_year_energy",
        stat_key=APP_STAT_TOTAL_IN_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_YEAR}",
            APP_STAT_TOTAL_IN_GRID_ENERGY,
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_ongrid_input_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_ongrid_output_week_energy",
        stat_key=APP_STAT_TOTAL_OUT_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_WEEK}",
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_ongrid_output_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_ongrid_output_month_energy",
        stat_key=APP_STAT_TOTAL_OUT_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_MONTH}",
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_ongrid_output_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_ongrid_output_year_energy",
        stat_key=APP_STAT_TOTAL_OUT_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_YEAR}",
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_ongrid_output_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ct_input_day_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ct_input_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ct_input_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ct_input_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ct_output_day_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ct_output_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ct_output_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ct_output_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="battery_charge_week_energy",
        stat_key=APP_STAT_TOTAL_CHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_CHARGE
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_charge_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="battery_charge_month_energy",
        stat_key=APP_STAT_TOTAL_CHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_MONTH}", APP_STAT_TOTAL_CHARGE
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_charge_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="battery_charge_year_energy",
        stat_key=APP_STAT_TOTAL_CHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_CHARGE
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_charge_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="battery_discharge_week_energy",
        stat_key=APP_STAT_TOTAL_DISCHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_DISCHARGE
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_discharge_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="battery_discharge_month_energy",
        stat_key=APP_STAT_TOTAL_DISCHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_MONTH}", APP_STAT_TOTAL_DISCHARGE
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_discharge_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="battery_discharge_year_energy",
        stat_key=APP_STAT_TOTAL_DISCHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_DISCHARGE
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_discharge_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="eps_input_day_energy",
        stat_key=APP_STAT_TOTAL_IN_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_IN_EPS_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eps_input_day_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="eps_input_week_energy",
        stat_key=APP_STAT_TOTAL_IN_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_IN_EPS_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eps_input_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="eps_input_month_energy",
        stat_key=APP_STAT_TOTAL_IN_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_MONTH}", APP_STAT_TOTAL_IN_EPS_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eps_input_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="eps_input_year_energy",
        stat_key=APP_STAT_TOTAL_IN_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_IN_EPS_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eps_input_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="eps_output_day_energy",
        stat_key=APP_STAT_TOTAL_OUT_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_OUT_EPS_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eps_output_day_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="eps_output_week_energy",
        stat_key=APP_STAT_TOTAL_OUT_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_WEEK}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_WEEK}", APP_STAT_TOTAL_OUT_EPS_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eps_output_week_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_WEEK,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="eps_output_month_energy",
        stat_key=APP_STAT_TOTAL_OUT_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_MONTH}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_MONTH}",
            APP_STAT_TOTAL_OUT_EPS_ENERGY,
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eps_output_month_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_MONTH,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="eps_output_year_energy",
        stat_key=APP_STAT_TOTAL_OUT_EPS_ENERGY,
        section=f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_YEAR}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_YEAR}", APP_STAT_TOTAL_OUT_EPS_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eps_output_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_YEAR,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="today_feed_in_energy",
        stat_key=APP_STAT_TODAY_SOLAR_ENERGY,
        section=APP_SECTION_TODAY_ENERGY,
        value_fn=lambda e: _get_payload_section(
            e, APP_SECTION_TODAY_ENERGY, APP_STAT_TODAY_SOLAR_ENERGY
        ),
        transform=safe_float,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="today_solar_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="today_grid_import_energy",
        stat_key=APP_STAT_TODAY_GRID_IMPORT_ENERGY,
        section=APP_SECTION_TODAY_ENERGY,
        value_fn=lambda e: _get_payload_section(
            e, APP_SECTION_TODAY_ENERGY, APP_STAT_TODAY_GRID_IMPORT_ENERGY
        ),
        transform=safe_float,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="today_grid_import_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="today_home_load_energy",
        stat_key=APP_STAT_TODAY_HOME_LOAD_ENERGY,
        section=APP_SECTION_TODAY_ENERGY,
        fallback_sources=((PAYLOAD_HOME_TRENDS, APP_STAT_TOTAL_HOME_ENERGY),),
        value_fn=lambda e: _get_payload_section(
            e, APP_SECTION_TODAY_ENERGY, APP_STAT_TODAY_HOME_LOAD_ENERGY
        ),
        transform=safe_float,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="today_home_load_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="today_battery_energy",
        stat_key=APP_STAT_TODAY_BATTERY_ENERGY,
        section=APP_SECTION_TODAY_ENERGY,
        fallback_sources=((PAYLOAD_STATISTIC, APP_STAT_TODAY_BATTERY_DISCHARGE),),
        value_fn=lambda e: _get_payload_section(
            e, APP_SECTION_TODAY_ENERGY, APP_STAT_TODAY_BATTERY_ENERGY
        ),
        transform=safe_float,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="today_battery_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="power_price",
        stat_key=FIELD_SINGLE_PRICE,
        section=PAYLOAD_PRICE,
        value_fn=lambda e: _get_payload_section(e, PAYLOAD_PRICE, FIELD_SINGLE_PRICE),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="power_price",
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=f"{CURRENCY_EURO}/kWh",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_today_pv_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_SOLAR_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_today_pv_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_today_battery_charge",
        stat_key=APP_STAT_TOTAL_CHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_CHARGE
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_today_battery_charge",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_today_battery_discharge",
        stat_key=APP_STAT_TOTAL_DISCHARGE,
        section=f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_DISCHARGE
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_today_battery_discharge",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_today_ongrid_input",
        stat_key=APP_STAT_TOTAL_IN_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}", APP_STAT_TOTAL_IN_GRID_ENERGY
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_today_ongrid_input",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="device_today_ongrid_output",
        stat_key=APP_STAT_TOTAL_OUT_GRID_ENERGY,
        section=f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e,
            f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}",
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_today_ongrid_output",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_today_ongrid_to_battery",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_today_pv_to_battery",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        translation_key="device_today_battery_to_ongrid",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="symmetry_total_positive",
        stat_key=FIELD_TOTAL_P,
        section=f"{APP_SECTION_SYMMETRY_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_SYMMETRY_STAT}_{DATE_TYPE_DAY}", FIELD_TOTAL_P
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="symmetry_total_positive",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
    JackeryStatSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="symmetry_total_negative",
        stat_key=FIELD_TOTAL_N,
        section=f"{APP_SECTION_SYMMETRY_STAT}_{DATE_TYPE_DAY}",
        value_fn=lambda e: _get_payload_section(
            e, f"{APP_SECTION_SYMMETRY_STAT}_{DATE_TYPE_DAY}", FIELD_TOTAL_N
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="symmetry_total_negative",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
    ),
)

SMART_MODE_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        app_fields=("isActive",),
        # pyrefly: ignore [unexpected-keyword]
        key="smart_mode_active",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_mode_active",
        value_fn=lambda e: _get_payload_section(e, PAYLOAD_SMART_MODE, "isActive"),
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=("timeDifference",),
        # pyrefly: ignore [unexpected-keyword]
        key="smart_mode_time_difference",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_mode_time_difference",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_SMART_MODE, "timeDifference"
        ),
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

SMART_SCHEDULE_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="smart_schedule_points",
        value_fn=lambda e: _get_first_list_count(
            e,
            PAYLOAD_SMART_SCHEDULE,
            "xList",
            "priceList",
            "pvPowerList",
            "homeList",
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_schedule_points",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=("profit",),
        # pyrefly: ignore [unexpected-keyword]
        key="smart_schedule_profit",
        value_fn=lambda e: _get_payload_section(e, PAYLOAD_SMART_SCHEDULE, "profit"),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_schedule_profit",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=("days",),
        # pyrefly: ignore [unexpected-keyword]
        key="smart_schedule_days",
        value_fn=lambda e: _get_payload_section(e, PAYLOAD_SMART_SCHEDULE, "days"),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_schedule_days",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=("currency",),
        # pyrefly: ignore [unexpected-keyword]
        key="smart_schedule_currency",
        value_fn=lambda e: _get_payload_section(e, PAYLOAD_SMART_SCHEDULE, "currency"),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_schedule_currency",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

DYNAMIC_PRICE_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        app_fields=(FIELD_TODAY_LOW,),
        # pyrefly: ignore [unexpected-keyword]
        key="dynamic_price_today_low",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_DYNAMIC_PRICE, FIELD_TODAY_LOW
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dynamic_price_today_low",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_TODAY_HIGH,),
        # pyrefly: ignore [unexpected-keyword]
        key="dynamic_price_today_high",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_DYNAMIC_PRICE, FIELD_TODAY_HIGH
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dynamic_price_today_high",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_NEXTDAY_LOW,),
        # pyrefly: ignore [unexpected-keyword]
        key="dynamic_price_nextday_low",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_DYNAMIC_PRICE, FIELD_NEXTDAY_LOW
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dynamic_price_nextday_low",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_NEXTDAY_HIGH,),
        # pyrefly: ignore [unexpected-keyword]
        key="dynamic_price_nextday_high",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_DYNAMIC_PRICE, FIELD_NEXTDAY_HIGH
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dynamic_price_nextday_high",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PRICE_COMPANY_NAME,),
        # pyrefly: ignore [unexpected-keyword]
        key="dynamic_price_provider",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_DYNAMIC_PRICE, FIELD_PRICE_COMPANY_NAME
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dynamic_price_provider",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IS_CONTRACT_AUTH,),
        # pyrefly: ignore [unexpected-keyword]
        key="dynamic_price_contract_auth",
        value_fn=lambda e: _get_payload_section(
            e, PAYLOAD_DYNAMIC_PRICE, FIELD_IS_CONTRACT_AUTH
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dynamic_price_contract_auth",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

TOU_PLAN_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="tou_plan_tasks",
        value_fn=lambda e: _get_first_list_count(e, PAYLOAD_TOU_SCHEDULE, "tasks"),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="tou_plan_tasks",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

PORTABLE_SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    JackerySensorDescription(
        app_fields=(FIELD_IAC,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_input_current",
        value_fn=lambda e: _get_prop(e, FIELD_IAC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_input_current",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.CURRENT,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IACPW,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_input_power",
        value_fn=lambda e: _get_prop(e, FIELD_IACPW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_input_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CIP,),
        # pyrefly: ignore [unexpected-keyword]
        key="charging_input_power",
        value_fn=lambda e: _get_prop(e, FIELD_CIP),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="charging_input_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACOV,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_output_voltage",
        value_fn=lambda e: _get_prop(e, FIELD_ACOV),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_output_voltage",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.VOLTAGE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACOHZ,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_output_frequency",
        value_fn=lambda e: _get_prop(e, FIELD_ACOHZ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_output_frequency",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.FREQUENCY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACPS,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_output_apparent_power",
        value_fn=lambda e: _get_prop(e, FIELD_ACPS),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_output_apparent_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.APPARENT_POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OAC, FIELD_OACPW),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_output_power",
        value_fn=lambda e: _get_prop_any(e, FIELD_OACPW, FIELD_OAC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_output_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OAC2,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_output_power_2",
        value_fn=lambda e: _get_prop(e, FIELD_OAC2),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_output_power_2",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACT,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_output_current",
        value_fn=lambda e: _get_prop(e, FIELD_OACT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_output_current",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.CURRENT,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACMODE,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_output_mode",
        value_fn=lambda e: _get_prop(e, FIELD_ACMODE),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_output_mode",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODC_PORT,),
        # pyrefly: ignore [unexpected-keyword]
        key="dc_output_power",
        value_fn=lambda e: _get_prop(e, FIELD_ODC_PORT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dc_output_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCC,),
        # pyrefly: ignore [unexpected-keyword]
        key="dc_output_current",
        value_fn=lambda e: _get_prop(e, FIELD_ODCC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dc_output_current",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.CURRENT,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCU,),
        # pyrefly: ignore [unexpected-keyword]
        key="dc_output_voltage",
        value_fn=lambda e: _get_prop(e, FIELD_ODCU),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dc_output_voltage",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.VOLTAGE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_USBA1,),
        # pyrefly: ignore [unexpected-keyword]
        key="usb_a1_power",
        value_fn=lambda e: _get_prop(e, FIELD_USBA1),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="usb_a1_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_USBA2,),
        # pyrefly: ignore [unexpected-keyword]
        key="usb_a2_power",
        value_fn=lambda e: _get_prop(e, FIELD_USBA2),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="usb_a2_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_USBC1,),
        # pyrefly: ignore [unexpected-keyword]
        key="usb_c1_power",
        value_fn=lambda e: _get_prop(e, FIELD_USBC1),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="usb_c1_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_USBC2,),
        # pyrefly: ignore [unexpected-keyword]
        key="usb_c2_power",
        value_fn=lambda e: _get_prop(e, FIELD_USBC2),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="usb_c2_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_USBA3,),
        # pyrefly: ignore [unexpected-keyword]
        key="usb_a3_power",
        value_fn=lambda e: _get_prop(e, FIELD_USBA3),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="usb_a3_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_USBC3,),
        # pyrefly: ignore [unexpected-keyword]
        key="usb_c3_power",
        value_fn=lambda e: _get_prop(e, FIELD_USBC3),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="usb_c3_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACL1,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_line1_current",
        value_fn=lambda e: _get_prop(e, FIELD_OACL1),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_line1_current",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.CURRENT,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACL1_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_line1_power",
        value_fn=lambda e: _get_prop(e, FIELD_OACL1_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_line1_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACL2,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_line2_current",
        value_fn=lambda e: _get_prop(e, FIELD_OACL2),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_line2_current",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.CURRENT,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACL2_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_line2_power",
        value_fn=lambda e: _get_prop(e, FIELD_OACL2_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_line2_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACT1,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_output_current_1",
        value_fn=lambda e: _get_prop(e, FIELD_OACT1),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_output_current_1",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.CURRENT,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACT2,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_output_current_2",
        value_fn=lambda e: _get_prop(e, FIELD_OACT2),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_output_current_2",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.CURRENT,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CIP,),
        # pyrefly: ignore [unexpected-keyword]
        key="charge_input_power_portable",
        value_fn=lambda e: _get_prop(e, FIELD_CIP),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="charge_input_power_portable",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CS,),
        # pyrefly: ignore [unexpected-keyword]
        key="charge_status",
        value_fn=lambda e: _get_prop(e, FIELD_CS),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="charge_status",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CSC,),
        # pyrefly: ignore [unexpected-keyword]
        key="charge_status_code",
        value_fn=lambda e: _get_prop(e, FIELD_CSC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="charge_status_code",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CSL,),
        # pyrefly: ignore [unexpected-keyword]
        key="charge_status_limit",
        value_fn=lambda e: _get_prop(e, FIELD_CSL),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="charge_status_limit",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CST,),
        # pyrefly: ignore [unexpected-keyword]
        key="charge_status_type",
        value_fn=lambda e: _get_prop(e, FIELD_CST),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="charge_status_type",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PC,),
        # pyrefly: ignore [unexpected-keyword]
        key="power_count",
        value_fn=lambda e: _get_prop(e, FIELD_PC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="power_count",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PM,),
        # pyrefly: ignore [unexpected-keyword]
        key="power_mode_portable",
        value_fn=lambda e: _get_prop(e, FIELD_PM),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="power_mode_portable",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PMB,),
        # pyrefly: ignore [unexpected-keyword]
        key="power_mode_battery",
        value_fn=lambda e: _get_prop(e, FIELD_PMB),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="power_mode_battery",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_DHG_RECALL,),
        # pyrefly: ignore [unexpected-keyword]
        key="dhg_recall",
        value_fn=lambda e: _get_prop(e, FIELD_DHG_RECALL),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dhg_recall",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IT,),
        # pyrefly: ignore [unexpected-keyword]
        key="input_temperature",
        value_fn=lambda e: _div(10)(_get_prop(e, FIELD_IT)),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="input_temperature",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.TEMPERATURE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OT,),
        # pyrefly: ignore [unexpected-keyword]
        key="output_temperature",
        value_fn=lambda e: _div(10)(_get_prop(e, FIELD_OT)),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="output_temperature",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.TEMPERATURE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BT,),
        # pyrefly: ignore [unexpected-keyword]
        key="battery_temperature",
        value_fn=lambda e: _div(10)(_get_prop(e, FIELD_BT)),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_temperature",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.TEMPERATURE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IACPW, FIELD_IP),
        # pyrefly: ignore [unexpected-keyword]
        key="input_power_portable",
        value_fn=lambda e: (
            (_get_prop(e, FIELD_IP))
            or (_get_payload_section(e, PAYLOAD_PROPERTIES, FIELD_IACPW))
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="input_power_portable",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OACPW, FIELD_OP),
        # pyrefly: ignore [unexpected-keyword]
        key="output_power_portable",
        value_fn=lambda e: (
            (_get_prop(e, FIELD_OP))
            or (_get_payload_section(e, PAYLOAD_PROPERTIES, FIELD_OACPW))
        ),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="output_power_portable",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_EC,),
        # pyrefly: ignore [unexpected-keyword]
        key="error_code",
        value_fn=lambda e: _get_prop(e, FIELD_EC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="error_code",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_RB,),
        # pyrefly: ignore [unexpected-keyword]
        key="remaining_runtime",
        value_fn=lambda e: _get_prop(e, FIELD_RB),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="remaining_runtime",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.DURATION,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTime.MINUTES,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BC,),
        # pyrefly: ignore [unexpected-keyword]
        key="battery_count",
        value_fn=lambda e: _get_prop(e, FIELD_BC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_count",
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BLS,),
        # pyrefly: ignore [unexpected-keyword]
        key="battery_low_state",
        value_fn=lambda e: _get_prop(e, FIELD_BLS),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_low_state",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_CL,),
        # pyrefly: ignore [unexpected-keyword]
        key="charge_limit",
        value_fn=lambda e: _get_prop(e, FIELD_CL),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="charge_limit",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_DL,),
        # pyrefly: ignore [unexpected-keyword]
        key="discharge_limit",
        value_fn=lambda e: _get_prop(e, FIELD_DL),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="discharge_limit",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PM,),
        # pyrefly: ignore [unexpected-keyword]
        key="power_mode",
        value_fn=lambda e: _get_prop(e, FIELD_PM),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="power_mode",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PSS,),
        # pyrefly: ignore [unexpected-keyword]
        key="power_source_selector",
        value_fn=lambda e: _get_prop(e, FIELD_PSS),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="power_source_selector",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_UPS,),
        # pyrefly: ignore [unexpected-keyword]
        key="ups_mode",
        value_fn=lambda e: _get_prop(e, FIELD_UPS),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ups_mode",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WSS,),
        # pyrefly: ignore [unexpected-keyword]
        key="wifi_switch_status",
        value_fn=lambda e: _get_prop(e, FIELD_WSS),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="wifi_switch_status",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_AST,),
        # pyrefly: ignore [unexpected-keyword]
        key="auto_standby_timer",
        value_fn=lambda e: _get_prop(e, FIELD_AST),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="auto_standby_timer",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTime.MINUTES,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IS_PACK_CONNECT,),
        # pyrefly: ignore [unexpected-keyword]
        key="external_pack_connected",
        value_fn=lambda e: _get_prop(e, FIELD_IS_PACK_CONNECT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="external_pack_connected",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WSIG,),
        # pyrefly: ignore [unexpected-keyword]
        key="wifi_signal_portable",
        value_fn=lambda e: _get_prop(e, FIELD_WSIG),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="wifi_signal",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WNAME,),
        # pyrefly: ignore [unexpected-keyword]
        key="wifi_ssid",
        value_fn=lambda e: _get_prop(e, FIELD_WNAME),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="wifi_ssid",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_WIP,),
        # pyrefly: ignore [unexpected-keyword]
        key="wifi_ip",
        value_fn=lambda e: _get_prop(e, FIELD_WIP),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="wifi_ip",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_MAC,),
        # pyrefly: ignore [unexpected-keyword]
        key="mac_address",
        value_fn=lambda e: _get_prop(e, FIELD_MAC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="mac_address",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OAC1_NAME,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac1_name",
        value_fn=lambda e: _get_prop(e, FIELD_OAC1_NAME),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac1_name",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OAC2_NAME,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac2_name",
        value_fn=lambda e: _get_prop(e, FIELD_OAC2_NAME),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac2_name",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCC,),
        # pyrefly: ignore [unexpected-keyword]
        key="dc_output_config",
        value_fn=lambda e: _get_prop(e, FIELD_ODCC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dc_output_config",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCCT,),
        # pyrefly: ignore [unexpected-keyword]
        key="dc_type",
        value_fn=lambda e: _get_prop(e, FIELD_ODCCT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dc_type",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCT,),
        # pyrefly: ignore [unexpected-keyword]
        key="dc_output_type",
        value_fn=lambda e: _get_prop(e, FIELD_ODCT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dc_output_type",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCU,),
        # pyrefly: ignore [unexpected-keyword]
        key="dc_usb_connected",
        value_fn=lambda e: _get_prop(e, FIELD_ODCU),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dc_usb_connected",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ODCUT,),
        # pyrefly: ignore [unexpected-keyword]
        key="dc_usb_type",
        value_fn=lambda e: _get_prop(e, FIELD_ODCUT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="dc_usb_type",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BPC,),
        # pyrefly: ignore [unexpected-keyword]
        key="battery_pack_count",
        value_fn=lambda e: _get_prop(e, FIELD_BPC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_pack_count",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_BOX,),
        # pyrefly: ignore [unexpected-keyword]
        key="box_mode",
        value_fn=lambda e: _get_prop(e, FIELD_BOX),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="box_mode",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_PAL,),
        # pyrefly: ignore [unexpected-keyword]
        key="light_sensor",
        value_fn=lambda e: _get_prop(e, FIELD_PAL),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="light_sensor",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SFC,),
        # pyrefly: ignore [unexpected-keyword]
        key="sleep_mode_flag",
        value_fn=lambda e: _get_prop(e, FIELD_SFC),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="sleep_mode_flag",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SLTB,),
        # pyrefly: ignore [unexpected-keyword]
        key="sltb_value",
        value_fn=lambda e: _get_prop(e, FIELD_SLTB),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="sltb_value",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_TA,),
        # pyrefly: ignore [unexpected-keyword]
        key="ambient_temperature",
        value_fn=lambda e: _get_prop(e, FIELD_TA),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ambient_temperature",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.TEMPERATURE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_TP,),
        # pyrefly: ignore [unexpected-keyword]
        key="panel_temperature",
        value_fn=lambda e: _get_prop(e, FIELD_TP),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="panel_temperature",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.TEMPERATURE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACCD,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_discharge_current",
        value_fn=lambda e: _get_prop(e, FIELD_ACCD),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_discharge_current",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.CURRENT,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACDT,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_discharge_time",
        value_fn=lambda e: _get_prop(e, FIELD_ACDT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_discharge_time",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.DURATION,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTime.MINUTES,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACIP,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_input_power_alt",
        value_fn=lambda e: _get_prop(e, FIELD_ACIP),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_input_power_alt",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACPSP,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_output_apparent_power_parallel",
        value_fn=lambda e: _get_prop(e, FIELD_ACPSP),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_output_apparent_power_parallel",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.APPARENT_POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACPSS,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_output_apparent_power_sum",
        value_fn=lambda e: _get_prop(e, FIELD_ACPSS),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_output_apparent_power_sum",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.APPARENT_POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_COP,),
        # pyrefly: ignore [unexpected-keyword]
        key="charge_output_power",
        value_fn=lambda e: _get_prop(e, FIELD_COP),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="charge_output_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_DT,),
        # pyrefly: ignore [unexpected-keyword]
        key="discharge_time",
        value_fn=lambda e: _get_prop(e, FIELD_DT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="discharge_time",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.DURATION,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTime.MINUTES,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_LM,),
        # pyrefly: ignore [unexpected-keyword]
        key="load_mode",
        value_fn=lambda e: _get_prop(e, FIELD_LM),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="load_mode",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_IPAL_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="input_power_allocation",
        value_fn=lambda e: _get_prop(e, FIELD_IPAL_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="input_power_allocation",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_OPAL_PW,),
        # pyrefly: ignore [unexpected-keyword]
        key="output_power_allocation",
        value_fn=lambda e: _get_prop(e, FIELD_OPAL_PW),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="output_power_allocation",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_SS,),
        # pyrefly: ignore [unexpected-keyword]
        key="system_status_switch",
        value_fn=lambda e: _get_prop(e, FIELD_SS),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="system_status_switch",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_ACOV1,),
        # pyrefly: ignore [unexpected-keyword]
        key="ac_output_voltage_2",
        value_fn=lambda e: _get_prop(e, FIELD_ACOV1),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="ac_output_voltage_2",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.VOLTAGE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        app_fields=(FIELD_TT,),
        # pyrefly: ignore [unexpected-keyword]
        key="total_time",
        value_fn=lambda e: _get_prop(e, FIELD_TT),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="total_time",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

SAVINGS_DETAIL_SENSOR_DESCRIPTIONS: tuple[
    JackerySavingsDetailSensorDescription, ...
] = (
    JackerySavingsDetailSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="savings_calculated_total",
        path=("calculated_total",),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="savings_calculated_total",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.MONETARY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=CURRENCY_EURO,
        value_fn=lambda e: safe_float(e.get_savings_value("calculated_total")),
    ),
    JackerySavingsDetailSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="savings_energy",
        path=("energy_kwh",),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="savings_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: safe_float(e.get_savings_value("energy_kwh")),
    ),
    JackerySavingsDetailSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="savings_price",
        path=("price",),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="savings_price",
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=f"{CURRENCY_EURO}/kWh",
        value_fn=lambda e: safe_float(e.get_savings_value("price")),
    ),
    JackerySavingsDetailSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="savings_battery_loss_year_energy",
        path=("source_energy", "battery_charge_discharge_balance_year_kwh"),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="savings_battery_balance_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: safe_float(
            e.get_savings_value((
                "source_energy",
                "battery_charge_discharge_balance_year_kwh",
            ))
        ),
    ),
    JackerySavingsDetailSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="savings_conversion_loss_year_energy",
        path=("source_energy", "conversion_loss_year_kwh"),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="savings_conversion_loss_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: safe_float(
            e.get_savings_value(("source_energy", "conversion_loss_year_kwh"))
        ),
    ),
    JackerySavingsDetailSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="savings_pv_residual_year_energy",
        path=("source_energy", "pv_residual_after_self_consumption_year_kwh"),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="savings_pv_residual_year_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
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
        # pyrefly: ignore [unexpected-keyword]
        key="soc",
        field=FIELD_BAT_SOC,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_pack_soc",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.BATTERY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_BAT_SOC)),
    ),
    JackeryBatteryPackSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="cell_temperature",
        field=FIELD_CELL_TEMP,
        transform=_div(10),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_pack_cell_temperature",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.TEMPERATURE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda e: _div(10)(safe_float(_get_prop(e, FIELD_CELL_TEMP))),
    ),
    JackeryBatteryPackSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="charge_power",
        field=FIELD_IN_PW,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_pack_charge_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_IN_PW)),
    ),
    JackeryBatteryPackSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="discharge_power",
        field=FIELD_OUT_PW,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_pack_discharge_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_OUT_PW)),
    ),
    JackeryBatteryPackSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="firmware_version",
        field=FIELD_VERSION,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_pack_firmware_version",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _get_prop(e, FIELD_VERSION),
    ),
    JackeryBatteryPackSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="serial_number",
        field=FIELD_DEVICE_SN,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_pack_serial_number",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _get_prop(e, FIELD_DEVICE_SN),
    ),
    JackeryBatteryPackSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="communication_state",
        field=FIELD_COMM_STATE,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_pack_communication_state",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_COMM_STATE)),
    ),
    JackeryBatteryPackSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="update_status",
        field=FIELD_UPDATE_STATUS,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_pack_update_status",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_UPDATE_STATUS)),
    ),
    JackeryBatteryPackSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="lifetime_charge_energy",
        field=FIELD_IN_EGY,
        transform=_div(100),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_pack_lifetime_charge_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH)(
            safe_float(_get_prop(e, FIELD_IN_EGY))
        ),
    ),
    JackeryBatteryPackSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="lifetime_discharge_energy",
        field=FIELD_OUT_EGY,
        transform=_div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_pack_lifetime_discharge_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _div(JACKERY_LIVE_ENERGY_UNITS_PER_KWH)(
            safe_float(_get_prop(e, FIELD_OUT_EGY))
        ),
    ),
)

SMART_PLUG_SENSOR_DESCRIPTIONS: tuple[JackerySmartPlugSensorDescription, ...] = (
    JackerySmartPlugSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="input_power",
        field=FIELD_IN_PW,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_plug_input_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_IN_PW)),
    ),
    JackerySmartPlugSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="output_power",
        field=FIELD_OUT_PW,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_plug_output_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_OUT_PW)),
    ),
    JackerySmartPlugSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="communication_state",
        field=FIELD_COMM_STATE,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_plug_communication_state",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_COMM_STATE)),
    ),
    JackerySmartPlugSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="priority",
        field=FIELD_SOCKET_PRIORITY,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_plug_priority",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_SOCKET_PRIORITY)),
    ),
    JackerySmartPlugSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="firmware_version",
        field=FIELD_VERSION,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_plug_firmware_version",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _get_prop(e, FIELD_VERSION),
    ),
    JackerySmartPlugSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="today_energy",
        field=FIELD_TODAY_ENERGY,
        transform=safe_float,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_plug_today_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        reset_period=DATE_TYPE_DAY,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_TODAY_ENERGY)),
    ),
    JackerySmartPlugSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="total_energy",
        field=FIELD_TOTAL_ENERGY,
        transform=safe_float,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_plug_total_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_TOTAL_ENERGY)),
    ),
    JackerySmartPlugSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="communication_mode",
        field=FIELD_COMM_MODE,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_plug_communication_mode",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_COMM_MODE),
    ),
    JackerySmartPlugSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="ip_address",
        field=FIELD_IP,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_plug_ip_address",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_IP),
    ),
    JackerySmartPlugSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="mac_address",
        field=FIELD_MAC,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_plug_mac_address",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_MAC),
    ),
    JackerySmartPlugSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="switch_cycle",
        field=FIELD_SOCKET_SWITCH_CYCLE,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_plug_switch_cycle",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_SOCKET_SWITCH_CYCLE)),
    ),
    JackerySmartPlugSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="last_update_ts",
        field=FIELD_SOCKET_LAST_UPDATE_TS,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_plug_last_update_ts",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_SOCKET_LAST_UPDATE_TS)),
    ),
)

BREAKER_SENSOR_DESCRIPTIONS: tuple[JackeryBreakerSensorDescription, ...] = (
    JackeryBreakerSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="pc",
        field=FIELD_PC,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="breaker_pc",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_PC)),
    ),
    JackeryBreakerSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="pr",
        field=FIELD_PR,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="breaker_pr",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_PR)),
    ),
    JackeryBreakerSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="sph",
        field=FIELD_SPH,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="breaker_sph",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_SPH)),
    ),
    JackeryBreakerSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="sph_pc",
        field=FIELD_SPH_PC,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="breaker_sph_pc",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_SPH_PC)),
    ),
)

SUBDEVICE_ALARM_SENSOR_DESCRIPTIONS: tuple[
    JackerySubdeviceAlarmSensorDescription, ...
] = (
    JackerySubdeviceAlarmSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="alert_count",
        field=FIELD_ALERT_COUNT,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="subdevice_alert_count",
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_ALERT_COUNT)),
    ),
)

METER_HEAD_SENSOR_DESCRIPTIONS: tuple[JackeryMeterHeadSensorDescription, ...] = (
    JackeryMeterHeadSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="input_power",
        field=FIELD_IN_PW,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="meter_head_input_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_IN_PW)),
    ),
    JackeryMeterHeadSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="output_power",
        field=FIELD_OUT_PW,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="meter_head_output_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_OUT_PW)),
    ),
    JackeryMeterHeadSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="communication_state",
        field=FIELD_COMM_STATE,
        transform=safe_int,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="meter_head_communication_state",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_int(_get_prop(e, FIELD_COMM_STATE)),
    ),
    JackeryMeterHeadSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="charging_energy",
        field=FIELD_CHARGING_ENERGY,
        transform=safe_float,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="meter_head_charging_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_CHARGING_ENERGY)),
    ),
    JackeryMeterHeadSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="discharging_energy",
        field=FIELD_DISCHARGING_ENERGY,
        transform=safe_float,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="meter_head_discharging_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: safe_float(_get_prop(e, FIELD_DISCHARGING_ENERGY)),
    ),
    JackeryMeterHeadSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="communication_mode",
        field=FIELD_COMM_MODE,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="meter_head_communication_mode",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_COMM_MODE),
    ),
    JackeryMeterHeadSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="ip_address",
        field=FIELD_IP,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="meter_head_ip_address",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_IP),
    ),
    JackeryMeterHeadSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="mac_address",
        field=FIELD_MAC,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="meter_head_mac_address",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_MAC),
    ),
)

SMART_METER_SENSOR_DESCRIPTIONS: tuple[JackerySmartMeterSensorDescription, ...] = (
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="power",
        field=FIELD_CT_POWER,
        aliases=(CT_TOTAL_POWER_PAIR[0],),
        negative_aliases=(CT_TOTAL_POWER_PAIR[1],),
        sum_fields=CT_POSITIVE_PHASE_POWER_FIELDS,
        negative_sum_fields=CT_NEGATIVE_PHASE_POWER_FIELDS,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="net_import_power",
        field=FIELD_CT_POWER,
        calculation="net_import",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_net_import_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="net_export_power",
        field=FIELD_CT_POWER,
        calculation="net_export",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_net_export_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="grid_import_energy",
        field=FIELD_CT_TOTAL_PHASE_ENERGY,
        sum_fields=(
            FIELD_CT_A_PHASE_ENERGY,
            FIELD_CT_B_PHASE_ENERGY,
            FIELD_CT_C_PHASE_ENERGY,
        ),
        transform=_div(1000),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_grid_import_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="grid_export_energy",
        field=FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
        sum_fields=(
            FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
            FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
            FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
        ),
        transform=_div(1000),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_grid_export_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="gross_phase_import_power",
        field=FIELD_CT_POWER,
        calculation="gross_import",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_gross_phase_import_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="gross_phase_export_power",
        field=FIELD_CT_POWER,
        calculation="gross_export",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_gross_phase_export_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="gross_phase_flow_power",
        field=FIELD_CT_POWER,
        calculation="gross_flow",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_gross_phase_flow_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_1_power",
        field=FIELD_CT_POWER1,
        aliases=(CT_POSITIVE_PHASE_POWER_FIELDS[0],),
        negative_aliases=(CT_NEGATIVE_PHASE_POWER_FIELDS[0],),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_1_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_2_power",
        field=FIELD_CT_POWER2,
        aliases=(CT_POSITIVE_PHASE_POWER_FIELDS[1],),
        negative_aliases=(CT_NEGATIVE_PHASE_POWER_FIELDS[1],),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_2_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_3_power",
        field=FIELD_CT_POWER3,
        aliases=(CT_POSITIVE_PHASE_POWER_FIELDS[2],),
        negative_aliases=(CT_NEGATIVE_PHASE_POWER_FIELDS[2],),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_3_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="lifetime_import_energy",
        field=FIELD_CT_TOTAL_PHASE_ENERGY,
        aliases=(FIELD_CT_TOTAL_PHASE_ENERGY,),
        sum_fields=(
            FIELD_CT_A_PHASE_ENERGY,
            FIELD_CT_B_PHASE_ENERGY,
            FIELD_CT_C_PHASE_ENERGY,
        ),
        transform=_div(1000),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_lifetime_import_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="lifetime_export_energy",
        field=FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
        aliases=(FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,),
        sum_fields=(
            FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
            FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
            FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
        ),
        transform=_div(1000),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_lifetime_export_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_1_lifetime_import_energy",
        field=FIELD_CT_A_PHASE_ENERGY,
        transform=_div(1000),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_1_lifetime_import_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_2_lifetime_import_energy",
        field=FIELD_CT_B_PHASE_ENERGY,
        transform=_div(1000),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_2_lifetime_import_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_3_lifetime_import_energy",
        field=FIELD_CT_C_PHASE_ENERGY,
        transform=_div(1000),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_3_lifetime_import_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_1_lifetime_export_energy",
        field=FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
        transform=_div(1000),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_1_lifetime_export_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_2_lifetime_export_energy",
        field=FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
        transform=_div(1000),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_2_lifetime_export_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_3_lifetime_export_energy",
        field=FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
        transform=_div(1000),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_3_lifetime_export_energy",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.ENERGY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.TOTAL_INCREASING,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="voltage",
        field=FIELD_CT_VOLT,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_voltage",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.VOLTAGE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_1_voltage",
        field=FIELD_CT_VOLT1,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_1_voltage",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.VOLTAGE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_2_voltage",
        field=FIELD_CT_VOLT2,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_2_voltage",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.VOLTAGE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_3_voltage",
        field=FIELD_CT_VOLT3,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_3_voltage",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.VOLTAGE,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_1_current",
        field=FIELD_CT_CURRENT1,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_1_current",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.CURRENT,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_2_current",
        field=FIELD_CT_CURRENT2,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_2_current",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.CURRENT,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_3_current",
        field=FIELD_CT_CURRENT3,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_3_current",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.CURRENT,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="frequency",
        field=FIELD_CT_FREQUENCY,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_frequency",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.FREQUENCY,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="power_factor",
        field=FIELD_CT_POWER_FACTOR,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_power_factor",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER_FACTOR,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_1_power_factor",
        field=FIELD_CT_POWER_FACTOR1,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_1_power_factor",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER_FACTOR,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_2_power_factor",
        field=FIELD_CT_POWER_FACTOR2,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_2_power_factor",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER_FACTOR,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_3_power_factor",
        field=FIELD_CT_POWER_FACTOR3,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_3_power_factor",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.POWER_FACTOR,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="apparent_power",
        field=FIELD_CT_APPARENT_POWER,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_apparent_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.APPARENT_POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_1_apparent_power",
        field=FIELD_CT_APPARENT_POWER1,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_1_apparent_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.APPARENT_POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_2_apparent_power",
        field=FIELD_CT_APPARENT_POWER2,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_2_apparent_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.APPARENT_POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_3_apparent_power",
        field=FIELD_CT_APPARENT_POWER3,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_3_apparent_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.APPARENT_POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="reactive_power",
        field=FIELD_CT_REACTIVE_POWER,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_reactive_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_1_reactive_power",
        field=FIELD_CT_REACTIVE_POWER1,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_1_reactive_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_2_reactive_power",
        field=FIELD_CT_REACTIVE_POWER2,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_2_reactive_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="phase_3_reactive_power",
        field=FIELD_CT_REACTIVE_POWER3,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_phase_3_reactive_power",
        # pyrefly: ignore [unexpected-keyword]
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # pyrefly: ignore [unexpected-keyword]
        state_class=SensorStateClass.MEASUREMENT,
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="communication_mode",
        field=FIELD_COMM_MODE,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_communication_mode",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_COMM_MODE),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="ip_address",
        field=FIELD_IP,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_ip_address",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop(e, FIELD_IP),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="mac_address",
        field=FIELD_MAC,
        fallback_fields=(FIELD_DEVICE_SN, FIELD_DEVICE_ID),
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_mac_address",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _get_prop_any(
            e, FIELD_MAC, FIELD_DEVICE_SN, FIELD_DEVICE_ID
        ),
    ),
    JackerySmartMeterSensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="fun_form",
        field=FIELD_CT_FUN_FORM,
        # pyrefly: ignore [unexpected-keyword]
        translation_key="smart_meter_fun_form",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: _smart_meter_value_fn(e, e.entity_description),
    ),
)
