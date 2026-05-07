"""Switch platform for Jackery SolarVault writable controls."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    FIELD_AUTO_STANDBY,
    FIELD_FOLLOW_METER,
    FIELD_IS_AUTO_STANDBY,
    FIELD_IS_FOLLOW_METER_PW,
    FIELD_OFF_GRID_DOWN,
    FIELD_SW_EPS,
    FIELD_WPS,
    PAYLOAD_PROPERTIES,
)
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import append_unique_entity, safe_bool, task_plan_value

# Limit concurrent control-write/update calls. This is a setter platform:
# writes go to the cloud and to MQTT. Serializing keeps the queue depth on
# the broker bounded and prevents reordering of `DevicePropertyChange`
# commands per HA dev guidance for write-heavy platforms.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the platform from a config entry."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    entities: list[SwitchEntity] = []
    seen_unique_ids: set[str] = set()

    def _append_unique(entity: SwitchEntity) -> None:
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="switch", logger=_LOGGER
        )

    for dev_id, payload in (coordinator.data or {}).items():
        props = payload.get(PAYLOAD_PROPERTIES) or {}
        supports_advanced = coordinator.device_supports_advanced(dev_id)
        if FIELD_SW_EPS in props:
            _append_unique(JackeryEpsSwitch(coordinator, dev_id))
        # APP_POLLING_MQTT.md documents SolarVault advanced controls as app
        # state plus MQTT command paths. Create the entities eagerly for known
        # SolarVault devices; otherwise gate them by the observed property keys.
        if supports_advanced:
            _append_unique(JackeryStandbySwitch(coordinator, dev_id))
            _append_unique(JackeryAutoStandbySwitch(coordinator, dev_id))
            _append_unique(JackeryFollowMeterSwitch(coordinator, dev_id))
            _append_unique(JackeryOffGridShutdownSwitch(coordinator, dev_id))
            _append_unique(JackeryStormWarningSwitch(coordinator, dev_id))
        else:
            if FIELD_AUTO_STANDBY in props:
                _append_unique(JackeryStandbySwitch(coordinator, dev_id))
            if FIELD_IS_AUTO_STANDBY in props or FIELD_AUTO_STANDBY in props:
                _append_unique(JackeryAutoStandbySwitch(coordinator, dev_id))
            if FIELD_IS_FOLLOW_METER_PW in props:
                _append_unique(JackeryFollowMeterSwitch(coordinator, dev_id))
            if FIELD_OFF_GRID_DOWN in props:
                _append_unique(JackeryOffGridShutdownSwitch(coordinator, dev_id))
            if FIELD_WPS in props:
                _append_unique(JackeryStormWarningSwitch(coordinator, dev_id))
    async_add_entities(entities)


class JackeryEpsSwitch(JackeryEntity, SwitchEntity):
    """EPS output enable/disable switch (MQTT cmd=107, actionId=3023)."""

    _attr_translation_key = "eps_output"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:power-plug"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "eps_output")

    @property
    def is_on(self) -> bool | None:
        """Return True when the entity is on."""
        raw = self._properties.get(FIELD_SW_EPS)
        return safe_bool(raw)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        await self.coordinator.async_set_eps(self._device_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        await self.coordinator.async_set_eps(self._device_id, False)
        await self.coordinator.async_request_refresh()


class JackeryAutoStandbySwitch(JackeryEntity, SwitchEntity):
    """Auto-standby switch (MQTT cmd=121, actionId=3021)."""

    _attr_translation_key = "auto_standby_set"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:power-sleep"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "auto_standby_set")

    @property
    def is_on(self) -> bool | None:
        """Return True when the entity is on."""
        raw = self._properties.get(FIELD_IS_AUTO_STANDBY)
        if raw is None:
            raw = task_plan_value(self._task_plan, FIELD_IS_AUTO_STANDBY)
        if raw is None:
            return None
        return safe_bool(raw)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        await self.coordinator.async_set_auto_standby(self._device_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        await self.coordinator.async_set_auto_standby(self._device_id, False)
        await self.coordinator.async_request_refresh()


class JackeryStandbySwitch(JackeryEntity, SwitchEntity):
    """Manual standby switch (MQTT cmd=107, actionId=3023)."""

    _attr_translation_key = "standby"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:power-sleep"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "standby")

    @property
    def is_on(self) -> bool | None:
        """Return True when the entity is on."""
        raw = self._properties.get(FIELD_AUTO_STANDBY)
        if raw is None:
            return None
        try:
            value = int(raw)
            return value == 1
        except TypeError, ValueError:
            return safe_bool(raw)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        await self.coordinator.async_set_standby(self._device_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        await self.coordinator.async_set_standby(self._device_id, False)
        await self.coordinator.async_request_refresh()


class JackeryFollowMeterSwitch(JackeryEntity, SwitchEntity):
    """Smart-meter following switch (MQTT cmd=121, actionId=3044)."""

    _attr_translation_key = "follow_meter"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:gauge"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "follow_meter")

    @property
    def is_on(self) -> bool | None:
        """Return True when the entity is on."""
        raw = self._properties.get(FIELD_IS_FOLLOW_METER_PW)
        if raw is None:
            raw = task_plan_value(
                self._task_plan, FIELD_IS_FOLLOW_METER_PW, FIELD_FOLLOW_METER
            )
        return safe_bool(raw)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        await self.coordinator.async_set_follow_meter(self._device_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        await self.coordinator.async_set_follow_meter(self._device_id, False)
        await self.coordinator.async_request_refresh()


class JackeryOffGridShutdownSwitch(JackeryEntity, SwitchEntity):
    """Off-grid shutdown switch (MQTT cmd=121, actionId=3039)."""

    _attr_translation_key = "off_grid_shutdown"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:power-off"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "off_grid_shutdown")

    @property
    def is_on(self) -> bool | None:
        """Return True when the entity is on."""
        raw = self._properties.get(FIELD_OFF_GRID_DOWN)
        if raw is None:
            raw = task_plan_value(self._task_plan, FIELD_OFF_GRID_DOWN)
        return safe_bool(raw)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        await self.coordinator.async_set_off_grid_shutdown(self._device_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        await self.coordinator.async_set_off_grid_shutdown(self._device_id, False)
        await self.coordinator.async_request_refresh()


class JackeryStormWarningSwitch(JackeryEntity, SwitchEntity):
    """Storm warning switch (MQTT cmd=0, actionId=3036)."""

    _attr_translation_key = "storm_warning"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:weather-lightning-rainy"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "storm_warning")

    @property
    def is_on(self) -> bool | None:
        """Return True when the entity is on."""
        raw = self._properties.get(FIELD_WPS)
        if raw is None:
            raw = self._weather_plan.get(FIELD_WPS)
        if raw is None:
            raw = task_plan_value(self._task_plan, FIELD_WPS)
        return safe_bool(raw)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the entity on."""
        await self.coordinator.async_set_storm_warning(self._device_id, True)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the entity off."""
        await self.coordinator.async_set_storm_warning(self._device_id, False)
        await self.coordinator.async_request_refresh()
