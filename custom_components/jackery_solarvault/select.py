"""Select platform for Jackery SolarVault preset-style controls."""

import logging
import re

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .const import (
    AUTO_OFF_HOURS,
    DEFAULT_STORM_WARNING_MINUTES,
    FIELD_CID,
    FIELD_COMPANY_NAME,
    FIELD_COUNTRY,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_MINS_INTERVAL,
    FIELD_NAME,
    FIELD_OFF_GRID_AUTO_OFF_TIME,
    FIELD_OFF_GRID_DOWN,
    FIELD_OFF_GRID_DOWN_TIME,
    FIELD_OFF_GRID_TIME,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_PRICE_MODE,
    FIELD_SINGLE_PRICE,
    FIELD_STORM,
    FIELD_SYSTEM_REGION,
    FIELD_TEMP_UNIT,
    FIELD_WORK_MODEL,
    FIELD_WPC,
    FIELD_WPS,
    PAYLOAD_PRICE,
    PAYLOAD_PRICE_SOURCES,
    PAYLOAD_PROPERTIES,
    PAYLOAD_WEATHER_PLAN,
    PRICE_MODE_TO_OPTION,
    STORM_MINUTES_DEFAULT,
    TEMP_UNIT_TO_OPTION,
    UNKNOWN_OPTION_PREFIX,
    WORK_MODE_READ_ALIASES,
    WORK_MODE_TO_OPTION,
)
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import append_unique_entity, safe_int, task_plan_value

# Limit concurrent control-write/update calls. This is a setter platform:
# writes go to the cloud and to MQTT. Serializing keeps the queue depth on
# the broker bounded and prevents reordering of `DevicePropertyChange`
# commands per HA dev guidance for write-heavy platforms.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)

_OPTION_TO_WORK_MODE = {v: k for k, v in WORK_MODE_TO_OPTION.items()}
_OPTION_TO_TEMP_UNIT = {v: k for k, v in TEMP_UNIT_TO_OPTION.items()}
_AUTO_OFF_OPTIONS = [f"h_{hours}" for hours in AUTO_OFF_HOURS]
_HOURS_TO_AUTO_OFF_OPTION = {hours: f"h_{hours}" for hours in AUTO_OFF_HOURS}
_AUTO_OFF_OPTION_TO_HOURS = {f"h_{hours}": hours for hours in AUTO_OFF_HOURS}
_OPTION_TO_PRICE_MODE = {v: k for k, v in PRICE_MODE_TO_OPTION.items()}


def _storm_minutes_value(
    properties: dict[str, object],
    weather_plan: dict[str, object],
    task_plan: dict[str, object],
) -> int | None:
    """Extract storm warning lead-time from known payload variants."""
    raw: object | None = None
    for key in (FIELD_WPC, FIELD_MINS_INTERVAL):
        raw = properties.get(key)
        if raw is not None:
            break
    if raw is None:
        for key in (FIELD_WPC, FIELD_MINS_INTERVAL):
            raw = weather_plan.get(key)
            if raw is not None:
                break
    if raw is None:
        raw = task_plan_value(task_plan, FIELD_WPC, FIELD_MINS_INTERVAL)
    if raw is None:
        storm = weather_plan.get(FIELD_STORM)
        if isinstance(storm, list):
            for item in storm:
                if not isinstance(item, dict):
                    continue
                raw = item.get(FIELD_WPC)
                if raw is None:
                    raw = item.get(FIELD_MINS_INTERVAL)
                if raw is not None:
                    break
    if raw is None:
        return None
    value = safe_int(raw)
    if value is None:
        return None
    return value if value > 0 else None


def _storm_minutes_fallback(
    properties: dict[str, object],
    weather_plan: dict[str, object],
    task_plan: dict[str, object],
) -> int | None:
    """Return a stable dropdown value when only storm enabled/disabled is known."""
    raw = properties.get(FIELD_WPS)
    if raw is None:
        raw = weather_plan.get(FIELD_WPS)
    if raw is None:
        raw = task_plan_value(task_plan, FIELD_WPS)
    if raw is not None:
        if safe_int(raw) is None:
            return None
        return DEFAULT_STORM_WARNING_MINUTES
    storm = weather_plan.get(FIELD_STORM)
    if isinstance(storm, list):
        return DEFAULT_STORM_WARNING_MINUTES
    return None


def _storm_minutes_label(minutes: int) -> str:
    """Return the technical option key for a minute value.

    Translation state keys "min_<value>" are valid HA identifiers and let
    each value in STORM_MINUTES_DEFAULT have its own localized label.
    """
    return f"min_{minutes}"


def _price_source_label(source: dict[str, object]) -> str:
    name = str(
        source.get(FIELD_COMPANY_NAME)
        or source.get(FIELD_NAME)
        or source.get(FIELD_CID)
        or source.get(FIELD_PLATFORM_COMPANY_ID)
        or "Provider"
    ).strip()
    country = str(
        source.get(FIELD_COUNTRY) or source.get(FIELD_SYSTEM_REGION) or ""
    ).strip()
    company_id = source.get(FIELD_PLATFORM_COMPANY_ID)
    label = f"{name} ({country})" if country else name
    if company_id not in (None, ""):
        return f"{label} #{company_id}"
    return label


def _price_source_regions(source: dict[str, object]) -> list[str]:
    raw = source.get(FIELD_COUNTRY) or source.get(FIELD_SYSTEM_REGION)
    if raw in (None, ""):
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _price_source_matches_current(
    source: dict[str, object],
    company_id: object,
    region: object,
) -> bool:
    if str(source.get(FIELD_PLATFORM_COMPANY_ID)) != str(company_id):
        return False
    if region in (None, ""):
        return True
    return str(region) in _price_source_regions(source)


def _price_sources_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    raw = payload.get(PAYLOAD_PRICE_SOURCES)
    if not isinstance(raw, list):
        return []
    out: list[dict[str, object]] = []
    for item in raw:
        if isinstance(item, dict):
            company_id = item.get(FIELD_PLATFORM_COMPANY_ID)
            country = item.get(FIELD_COUNTRY) or item.get(FIELD_SYSTEM_REGION)
            if company_id not in (None, "") and country:
                out.append(item)
    return out


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the platform from a config entry."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    entities: list[SelectEntity] = []
    seen_unique_ids: set[str] = set()

    def _append_unique(entity: SelectEntity) -> None:
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="select", logger=_LOGGER
        )

    for dev_id, payload in (coordinator.data or {}).items():
        props = payload.get(PAYLOAD_PROPERTIES) or {}
        supports_advanced = coordinator.device_supports_advanced(dev_id)
        # App naming confirmed by user capture:
        # ai_smart / self_use / custom / tariff
        # (German labels in app: KI-Smart-Modus / Eigenverbrauch /
        # Benutzerdefinierter Modus / Tarifmodus — preserved in translations)
        if supports_advanced or FIELD_WORK_MODEL in props:
            _append_unique(JackeryWorkModeSelect(coordinator, dev_id))

        # Temperature unit can be model-dependent; keep it for SolarVault
        # profiles where advanced keys are present.
        if supports_advanced or FIELD_TEMP_UNIT in props:
            _append_unique(JackeryTempUnitSelect(coordinator, dev_id))

        # App UI:
        # - netzgekoppelter Betrieb: bool switch (isAutoStandby)
        # - Inselbetrieb: hours dropdown (offGridTime)
        if (
            supports_advanced
            or FIELD_OFF_GRID_TIME in props
            or FIELD_OFF_GRID_DOWN in props
        ):
            _append_unique(JackeryIslandAutoOffSelect(coordinator, dev_id))

        weather_plan = payload.get(PAYLOAD_WEATHER_PLAN) or {}
        if (
            supports_advanced
            or FIELD_WPC in props
            or FIELD_MINS_INTERVAL in props
            or FIELD_WPC in weather_plan
            or FIELD_MINS_INTERVAL in weather_plan
        ):
            _append_unique(JackeryStormWarningMinutesSelect(coordinator, dev_id))
        _append_unique(JackeryElectricityPriceModeSelect(coordinator, dev_id))
        if payload.get(PAYLOAD_PRICE_SOURCES) or (payload.get(PAYLOAD_PRICE) or {}).get(
            FIELD_PLATFORM_COMPANY_ID
        ) not in (None, ""):
            _append_unique(JackeryElectricityPriceSourceSelect(coordinator, dev_id))

    async_add_entities(entities)


class JackeryWorkModeSelect(JackeryEntity, SelectEntity):
    """Work mode selector with enum-style options."""

    _attr_translation_key = "work_mode_select"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:tune-variant"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "work_mode_select")

    @property
    def options(self) -> list[str]:
        """Return the list of available options."""
        opts = list(_OPTION_TO_WORK_MODE.keys())
        current = self.current_option
        if current and current not in opts:
            opts.append(current)
        return opts

    @property
    def current_option(self) -> str | None:
        """Return the currently-selected option."""
        raw = self._properties.get(FIELD_WORK_MODEL)
        if raw is None:
            raw = task_plan_value(self._task_plan, FIELD_WORK_MODEL)
        if raw is None:
            mode_hint = safe_int(self._price.get(FIELD_DYNAMIC_OR_SINGLE))
            if mode_hint == 1:
                return WORK_MODE_TO_OPTION[7]
            return None
        value = safe_int(raw)
        if value is None:
            return None
        option = WORK_MODE_TO_OPTION.get(value)
        if option is None:
            option = WORK_MODE_READ_ALIASES.get(value)
        if option is not None:
            return option
        return f"{UNKNOWN_OPTION_PREFIX}{value}"

    async def async_select_option(self, option: str) -> None:
        """Select option."""
        if option in _OPTION_TO_WORK_MODE:
            mode = _OPTION_TO_WORK_MODE[option]
        else:
            match = re.fullmatch(rf"{UNKNOWN_OPTION_PREFIX}([-+]?\d+)", option)
            if not match:
                raise ValueError(f"Invalid work mode option: {option}")
            mode = int(match.group(1))
        await self.coordinator.async_set_work_model(self._device_id, mode)
        await self.coordinator.async_request_refresh()


class JackeryTempUnitSelect(JackeryEntity, SelectEntity):
    """Temperature-unit selector (Celsius/Fahrenheit)."""

    _attr_translation_key = "temp_unit_select"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:thermometer"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "temp_unit_select")
        self._attr_options = list(_OPTION_TO_TEMP_UNIT.keys())

    @property
    def current_option(self) -> str | None:
        """Return the currently-selected option."""
        val = safe_int(self._properties.get(FIELD_TEMP_UNIT))
        if val is None:
            return None
        return TEMP_UNIT_TO_OPTION.get(val)

    async def async_select_option(self, option: str) -> None:
        """Select option."""
        if option not in _OPTION_TO_TEMP_UNIT:
            raise ValueError(f"Invalid temperature unit option: {option}")
        await self.coordinator.async_set_temp_unit(
            self._device_id, _OPTION_TO_TEMP_UNIT[option]
        )
        await self.coordinator.async_request_refresh()


class JackeryIslandAutoOffSelect(JackeryEntity, SelectEntity):
    """Auto-off duration for off-grid mode."""

    _attr_translation_key = "auto_off_island_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:timer-cog-outline"
    _attr_options = list(_AUTO_OFF_OPTIONS)

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "auto_off_island_mode")

    @property
    def current_option(self) -> str | None:
        """Return the currently-selected option."""
        raw = self._properties.get(FIELD_OFF_GRID_TIME)
        if raw is None:
            raw = task_plan_value(
                self._task_plan,
                FIELD_OFF_GRID_TIME,
                FIELD_OFF_GRID_DOWN_TIME,
                FIELD_OFF_GRID_AUTO_OFF_TIME,
            )
        if raw is None:
            return None
        value = safe_int(raw)
        if value is None:
            return None
        if value in _HOURS_TO_AUTO_OFF_OPTION:
            return _HOURS_TO_AUTO_OFF_OPTION[value]
        if value % 60 == 0 and (value // 60) in _HOURS_TO_AUTO_OFF_OPTION:
            return _HOURS_TO_AUTO_OFF_OPTION[value // 60]
        return None

    async def async_select_option(self, option: str) -> None:
        """Select option."""
        if option not in _AUTO_OFF_OPTION_TO_HOURS:
            raise ValueError(f"Invalid island auto-off option: {option}")
        hours = _AUTO_OFF_OPTION_TO_HOURS[option]
        await self.coordinator.async_set_off_grid_time(self._device_id, hours * 60)
        await self.coordinator.async_request_refresh()


class JackeryStormWarningMinutesSelect(JackeryEntity, SelectEntity):
    """Storm warning lead-time selector."""

    _attr_translation_key = "storm_warning_minutes_select"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:weather-lightning-rainy"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "storm_warning_minutes_select")

    @property
    def options(self) -> list[str]:
        """Return the list of available options."""
        values = set(STORM_MINUTES_DEFAULT)
        current_minutes = self._current_minutes()
        if current_minutes is not None and current_minutes > 0:
            values.add(current_minutes)
        return [_storm_minutes_label(m) for m in sorted(values)]

    @property
    def current_option(self) -> str | None:
        """Return the currently-selected option."""
        current_minutes = self._current_minutes()
        if current_minutes is None:
            return None
        return _storm_minutes_label(current_minutes)

    def _current_minutes(self) -> int | None:
        current = _storm_minutes_value(
            self._properties, self._weather_plan, self._task_plan
        )
        if current is not None:
            return current
        return _storm_minutes_fallback(
            self._properties, self._weather_plan, self._task_plan
        )

    async def async_select_option(self, option: str) -> None:
        """Select option."""
        match = re.fullmatch(r"min_(\d+)", option)
        if not match:
            raise ValueError(f"Invalid storm warning lead-time option: {option}")
        minutes = int(match.group(1))
        await self.coordinator.async_set_storm_minutes(self._device_id, minutes)
        await self.coordinator.async_request_refresh()


class JackeryElectricityPriceModeSelect(JackeryEntity, SelectEntity):
    """Electricity price mode: dynamic provider vs single tariff."""

    _attr_translation_key = "electricity_price_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:cash-multiple"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "electricity_price_mode")

    def _current_mode(self) -> int | None:
        raw = self._price.get(FIELD_DYNAMIC_OR_SINGLE)
        if raw is None:
            raw = task_plan_value(
                self._task_plan, FIELD_DYNAMIC_OR_SINGLE, FIELD_PRICE_MODE
            )
        if raw is None:
            work_mode = safe_int(self._properties.get(FIELD_WORK_MODEL))
            if work_mode == 7:
                return 1
            if self._price.get(FIELD_SINGLE_PRICE) is not None:
                return 2
            return None
        return safe_int(raw)

    def _dynamic_mode_available(self) -> bool:
        company_id = self._price.get(FIELD_PLATFORM_COMPANY_ID)
        region = self._price.get(FIELD_SYSTEM_REGION)
        if company_id not in (None, "") and bool(region):
            return True
        return bool(_price_sources_from_payload(self._payload))

    @property
    def options(self) -> list[str]:
        """Return the list of available options."""
        opts: list[str] = [
            PRICE_MODE_TO_OPTION[1],
            PRICE_MODE_TO_OPTION[2],
        ]
        current = self.current_option
        if current and current not in opts:
            opts.append(current)
        return opts

    @property
    def current_option(self) -> str | None:
        """Return the currently-selected option."""
        mode = self._current_mode()
        if mode is None:
            return None
        option = PRICE_MODE_TO_OPTION.get(mode)
        if option is not None:
            return option
        return f"{UNKNOWN_OPTION_PREFIX}{mode}"

    async def async_select_option(self, option: str) -> None:
        """Select option."""
        mode = _OPTION_TO_PRICE_MODE.get(option)
        if mode is None:
            raise ValueError(f"Invalid electricity price mode option: {option}")
        if mode == 1:
            if not self._dynamic_mode_available() and self._current_mode() != 1:
                raise HomeAssistantError(
                    "Dynamic tariff provider is not available yet. Wait for the "
                    "next refresh or use the electricity price provider entity."
                )
            await self.coordinator.async_set_price_mode_dynamic(self._device_id)
        elif mode == 2:
            await self.coordinator.async_set_price_mode_single(self._device_id)
        await self.coordinator.async_request_refresh()


class JackeryElectricityPriceSourceSelect(JackeryEntity, SelectEntity):
    """Dynamic-price provider selector (device/dynamic/priceCompany)."""

    _attr_translation_key = "electricity_price_provider"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "electricity_price_provider")

    @property
    def options(self) -> list[str]:
        """Return the list of available options."""
        labels = [_price_source_label(source) for source in self._sources()]
        current = self.current_option
        if current and current not in labels:
            labels.append(current)
        return labels

    @property
    def current_option(self) -> str | None:
        """Return the currently-selected option."""
        company_id = self._price.get(FIELD_PLATFORM_COMPANY_ID)
        region = self._price.get(FIELD_SYSTEM_REGION)
        if company_id in (None, ""):
            return None
        for source in self._sources():
            if _price_source_matches_current(source, company_id, region):
                return _price_source_label(source)
        return _price_source_label({
            FIELD_PLATFORM_COMPANY_ID: company_id,
            FIELD_COUNTRY: region,
            FIELD_COMPANY_NAME: self._price.get(FIELD_COMPANY_NAME),
        })

    def _sources(self) -> list[dict[str, object]]:
        return _price_sources_from_payload(self._payload)

    async def async_select_option(self, option: str) -> None:
        """Select option."""
        for source in self._sources():
            if _price_source_label(source) == option:
                await self.coordinator.async_set_price_source(
                    self._device_id,
                    source,
                )
                await self.coordinator.async_request_refresh()
                return
        raise ValueError(f"Invalid electricity price provider option: {option}")
