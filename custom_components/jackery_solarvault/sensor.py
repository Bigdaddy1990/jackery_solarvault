"""Sensor platform for Jackery SolarVault."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity

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


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class JackerySensorDescription(SensorEntityDescription):
    """Sensor description with a getter callable for nested paths."""

    getter: Callable[[dict[str, Any]], Any]
    transform: Callable[[Any], Any] = _identity


def _prop(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda props: props.get(key)


def _nested(*keys: str) -> Callable[[dict[str, Any]], Any]:
    return lambda props: _path(props, *keys)


SENSOR_DESCRIPTIONS: tuple[JackerySensorDescription, ...] = (
    # --- State of charge ---------------------------------------------------
    JackerySensorDescription(
        key="soc",
        translation_key="battery_soc",
        getter=_prop("soc"),
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    JackerySensorDescription(
        key="bat_soc",
        translation_key="battery_soc_internal",
        getter=_prop("batSoc"),
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),

    # --- Temperatures ------------------------------------------------------
    JackerySensorDescription(
        key="cell_temperature",
        translation_key="cell_temperature",
        getter=_prop("cellTemp"),
        transform=_div(10),
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),

    # --- Battery power -----------------------------------------------------
    JackerySensorDescription(
        key="battery_charge_power",
        translation_key="battery_charge_power",
        getter=_prop("batInPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:battery-arrow-up",
    ),
    JackerySensorDescription(
        key="battery_discharge_power",
        translation_key="battery_discharge_power",
        getter=_prop("batOutPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:battery-arrow-down",
    ),

    # --- Solar / PV --------------------------------------------------------
    JackerySensorDescription(
        key="pv_power_total",
        translation_key="pv_power_total",
        getter=_prop("pvPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:solar-power",
    ),
    JackerySensorDescription(
        key="pv1_power",
        translation_key="pv1_power",
        getter=_nested("pv1", "pvPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:solar-panel",
    ),
    JackerySensorDescription(
        key="pv2_power",
        translation_key="pv2_power",
        getter=_nested("pv2", "pvPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:solar-panel",
    ),
    JackerySensorDescription(
        key="pv3_power",
        translation_key="pv3_power",
        getter=_nested("pv3", "pvPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:solar-panel",
    ),
    JackerySensorDescription(
        key="pv4_power",
        translation_key="pv4_power",
        getter=_nested("pv4", "pvPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:solar-panel",
    ),

    # --- Grid --------------------------------------------------------------
    JackerySensorDescription(
        key="grid_in_power",
        translation_key="grid_in_power",
        getter=_prop("inOngridPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:transmission-tower-import",
    ),
    JackerySensorDescription(
        key="grid_out_power",
        translation_key="grid_out_power",
        getter=_prop("outOngridPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon="mdi:transmission-tower-export",
    ),

    # --- EPS (Emergency Power Supply, AC OUT) ------------------------------
    JackerySensorDescription(
        key="eps_in_power",
        translation_key="eps_in_power",
        getter=_prop("swEpsInPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_registry_enabled_default=False,
    ),
    JackerySensorDescription(
        key="eps_out_power",
        translation_key="eps_out_power",
        getter=_prop("swEpsOutPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_registry_enabled_default=False,
    ),

    # --- Stack (additional battery pack) -----------------------------------
    JackerySensorDescription(
        key="stack_in_power",
        translation_key="stack_in_power",
        getter=_prop("stackInPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_registry_enabled_default=False,
    ),
    JackerySensorDescription(
        key="stack_out_power",
        translation_key="stack_out_power",
        getter=_prop("stackOutPw"),
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_registry_enabled_default=False,
    ),

    # --- Network / diagnostics --------------------------------------------
    JackerySensorDescription(
        key="wifi_signal",
        translation_key="wifi_signal",
        getter=_prop("wsig"),
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="wifi_name",
        translation_key="wifi_name",
        getter=_prop("wname"),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:wifi",
    ),
    JackerySensorDescription(
        key="wifi_ip",
        translation_key="wifi_ip",
        getter=_prop("wip"),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:ip-network",
    ),

    # --- Configuration readouts (diagnostic category) ---------------------
    JackerySensorDescription(
        key="soc_charge_limit",
        translation_key="soc_charge_limit",
        getter=_prop("socChgLimit"),
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-charging-high",
    ),
    JackerySensorDescription(
        key="soc_discharge_limit",
        translation_key="soc_discharge_limit",
        getter=_prop("socDischgLimit"),
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-low",
    ),
    JackerySensorDescription(
        key="max_output_power",
        translation_key="max_output_power",
        getter=_prop("maxOutPw"),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="max_grid_power",
        translation_key="max_grid_power",
        getter=_prop("maxGridStdPw"),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="max_inverter_power",
        translation_key="max_inverter_power",
        getter=_prop("maxInvStdPw"),
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackerySensorDescription(
        key="battery_count",
        translation_key="battery_count",
        getter=_prop("batNum"),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-multiple",
    ),
    JackerySensorDescription(
        key="battery_state",
        translation_key="battery_state",
        getter=_prop("batState"),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-sync",
    ),
    JackerySensorDescription(
        key="auto_standby",
        translation_key="auto_standby",
        getter=_prop("autoStandby"),
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:power-sleep",
    ),
)


# ---------------------------------------------------------------------------
# Statistic sensors — sourced from _statistic section of payload
# ---------------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class JackeryStatSensorDescription(SensorEntityDescription):
    """Sensor description sourcing from the statistic dict."""

    stat_key: str
    transform: Callable[[Any], Any] = _identity
    section: str = "statistic"   # "statistic" | "price" | "system"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


STAT_DESCRIPTIONS: tuple[JackeryStatSensorDescription, ...] = (
    JackeryStatSensorDescription(
        key="today_load",
        translation_key="today_load",
        stat_key="todayLoad",
        transform=_to_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="today_battery_charge",
        translation_key="today_battery_charge",
        stat_key="todayBatteryChg",
        transform=_to_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="today_battery_discharge",
        translation_key="today_battery_discharge",
        stat_key="todayBatteryDisChg",
        transform=_to_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="today_generation",
        translation_key="today_generation",
        stat_key="todayGeneration",
        transform=_to_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="total_generation",
        translation_key="total_generation",
        stat_key="totalGeneration",
        transform=_to_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    ),
    JackeryStatSensorDescription(
        key="total_revenue",
        translation_key="total_revenue",
        stat_key="totalRevenue",
        transform=_to_float,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY_EURO,
        icon="mdi:currency-eur",
    ),
    JackeryStatSensorDescription(
        key="total_carbon_saved",
        translation_key="total_carbon_saved",
        stat_key="totalCarbon",
        transform=_to_float,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        icon="mdi:molecule-co2",
    ),
    # Single-tariff power price from powerPriceConfig
    JackeryStatSensorDescription(
        key="power_price",
        translation_key="power_price",
        stat_key="singlePrice",
        section="price",
        transform=_to_float,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:currency-eur",
        entity_category=EntityCategory.DIAGNOSTIC,
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
    coordinator: JackerySolarVaultCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for dev_id, payload in (coordinator.data or {}).items():
        props = payload.get("properties") or {}
        stat = payload.get("statistic") or {}
        price = payload.get("price") or {}
        meta = payload.get("device") or {}
        system = payload.get("system") or {}

        # Property-driven sensors — only add if the key exists
        for desc in SENSOR_DESCRIPTIONS:
            if desc.getter(props) is not None:
                entities.append(JackerySensor(coordinator, dev_id, desc))

        # Statistic / price sensors
        for stat_desc in STAT_DESCRIPTIONS:
            source = stat if stat_desc.section == "statistic" else price
            if stat_desc.stat_key in source:
                entities.append(JackeryStatSensor(coordinator, dev_id, stat_desc))

        # Derived net-flow sensors (always useful if the source keys exist)
        if "batInPw" in props and "batOutPw" in props:
            entities.append(JackeryBatteryNetPowerSensor(coordinator, dev_id))
        if "inOngridPw" in props and "outOngridPw" in props:
            entities.append(JackeryGridNetPowerSensor(coordinator, dev_id))

        # Alarm sensor (even if empty, useful to see "0 active alarms")
        if payload.get("alarm") is not None:
            entities.append(JackeryAlarmSensor(coordinator, dev_id))

        # Timestamp diagnostic sensors from device meta
        if meta.get("onlineTime"):
            entities.append(
                JackeryTimestampSensor(
                    coordinator, dev_id,
                    key="last_online",
                    translation_key="last_online",
                    source_key="onlineTime",
                )
            )
        if meta.get("offlineTime"):
            entities.append(
                JackeryTimestampSensor(
                    coordinator, dev_id,
                    key="last_offline",
                    translation_key="last_offline",
                    source_key="offlineTime",
                )
            )
        if meta.get("updateTime"):
            entities.append(
                JackeryTimestampSensor(
                    coordinator, dev_id,
                    key="last_update",
                    translation_key="last_update",
                    source_key="updateTime",
                )
            )
        if meta.get("createTime"):
            entities.append(
                JackeryTimestampSensor(
                    coordinator, dev_id,
                    key="activation_date",
                    translation_key="activation_date",
                    source_key="createTime",
                )
            )

        # System-meta diagnostic sensors
        if system.get("gridStandard"):
            entities.append(
                JackerySystemMetaSensor(
                    coordinator, dev_id,
                    key="grid_standard",
                    translation_key="grid_standard",
                    source_key="gridStandard",
                )
            )
        if system.get("countryCode"):
            entities.append(
                JackerySystemMetaSensor(
                    coordinator, dev_id,
                    key="country_code",
                    translation_key="country_code",
                    source_key="countryCode",
                )
            )
        if system.get("timezone"):
            entities.append(
                JackerySystemMetaSensor(
                    coordinator, dev_id,
                    key="timezone",
                    translation_key="timezone",
                    source_key="timezone",
                )
            )

        # Today PV energy from pv/trends
        trends = payload.get("pv_trends") or {}
        if "totalSolarEnergy" in trends or "totalSolarRevenue" in trends:
            entities.append(JackeryPvTrendsTodaySensor(coordinator, dev_id))

        # Raw diagnostic dump
        entities.append(JackeryRawPropertiesSensor(coordinator, dev_id))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------
class JackerySensor(JackeryEntity, SensorEntity):
    entity_description: JackerySensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: JackerySensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        raw = self.entity_description.getter(self._properties)
        if raw is None:
            return None
        return self.entity_description.transform(raw)


class JackeryStatSensor(JackeryEntity, SensorEntity):
    """Sensor sourced from the statistic / price section of the payload."""

    entity_description: JackeryStatSensorDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: JackeryStatSensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if self.entity_description.section == "price":
            source = self._price
        else:
            source = self._statistic
        raw = source.get(self.entity_description.stat_key)
        if raw is None:
            return None
        return self.entity_description.transform(raw)


class JackeryRawPropertiesSensor(JackeryEntity, SensorEntity):
    """Diagnostic: full properties JSON as state attributes."""

    _attr_translation_key = "raw_properties"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:code-json"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id, "raw_properties")

    @property
    def native_value(self) -> int:
        return len(self._properties)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        for k, v in self._properties.items():
            try:
                json.dumps(v)
                attrs[k] = v
            except (TypeError, ValueError):
                attrs[k] = str(v)
        return attrs


# ---------------------------------------------------------------------------
# Derived sensors: signed net power flows (Energy-Dashboard-friendly)
# ---------------------------------------------------------------------------
class JackeryBatteryNetPowerSensor(JackeryEntity, SensorEntity):
    """Net battery power: positive when charging, negative when discharging.

    Computed as `batInPw - batOutPw`. Having both directions as a single
    signed sensor makes it trivial to use in dashboards and templates
    without combining two entities.
    """

    _attr_translation_key = "battery_net_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:battery-sync"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id, "battery_net_power")

    @property
    def native_value(self) -> int | None:
        props = self._properties
        in_pw = props.get("batInPw")
        out_pw = props.get("batOutPw")
        if in_pw is None or out_pw is None:
            return None
        try:
            return int(in_pw) - int(out_pw)
        except (TypeError, ValueError):
            return None


class JackeryGridNetPowerSensor(JackeryEntity, SensorEntity):
    """Net grid power: positive = import, negative = export."""

    _attr_translation_key = "grid_net_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_icon = "mdi:transmission-tower"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id, "grid_net_power")

    @property
    def native_value(self) -> int | None:
        props = self._properties
        in_pw = props.get("inOngridPw")
        out_pw = props.get("outOngridPw")
        if in_pw is None or out_pw is None:
            return None
        try:
            return int(in_pw) - int(out_pw)
        except (TypeError, ValueError):
            return None


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
        super().__init__(coordinator, device_id, "alarm_count")

    @property
    def native_value(self) -> int:
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
        alarms = self._alarm
        if isinstance(alarms, list):
            return {"alarms": alarms}
        if isinstance(alarms, dict):
            return dict(alarms)
        return {}


# ---------------------------------------------------------------------------
# PV trends today
# ---------------------------------------------------------------------------
class JackeryPvTrendsTodaySensor(JackeryEntity, SensorEntity):
    """Today's PV energy from /v1/device/stat/sys/pv/trends."""

    _attr_translation_key = "pv_today_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:solar-power-variant"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id, "pv_today_energy")

    @property
    def native_value(self) -> float | None:
        raw = self._pv_trends.get("totalSolarEnergy")
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self._pv_trends
        attrs: dict[str, Any] = {}
        if (rev := t.get("totalSolarRevenue")) is not None:
            attrs["revenue"] = rev
        if (currency := t.get("currency")) is not None:
            attrs["currency"] = currency
        return attrs


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
        super().__init__(coordinator, device_id, key)
        self._attr_translation_key = translation_key
        self._source_key = source_key

    @property
    def native_value(self) -> datetime | None:
        ts_ms = self._device_meta.get(self._source_key)
        if not ts_ms:
            return None
        try:
            return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)
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
        super().__init__(coordinator, device_id, key)
        self._attr_translation_key = translation_key
        self._source_key = source_key

    @property
    def native_value(self) -> Any:
        return self._system.get(self._source_key)
