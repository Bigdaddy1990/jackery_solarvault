"""Text platform for Jackery SolarVault — editable system name."""

from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import JackeryError
from .const import (
    FIELD_DEVICE_NAME,
    FIELD_ID,
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_NAME,
    PAYLOAD_SYSTEM,
)
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import append_unique_entity

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
    entities: list[TextEntity] = []
    seen_unique_ids: set[str] = set()

    def _append_unique(entity: TextEntity) -> None:
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="text", logger=_LOGGER
        )

    for dev_id, payload in (coordinator.data or {}).items():
        system = payload.get(PAYLOAD_SYSTEM) or {}
        # The rename endpoint in APP_POLLING_MQTT.md needs the system id.
        if system.get(FIELD_ID) or system.get(FIELD_SYSTEM_ID):
            _append_unique(JackerySystemNameText(coordinator, dev_id))
    async_add_entities(entities)


class JackerySystemNameText(JackeryEntity, TextEntity):
    """Rename the SolarVault system using SYSTEM_NAME_PATH from const.py."""

    _attr_translation_key = "system_name"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:rename-box"
    _attr_native_min = 1
    _attr_native_max = 64
    _attr_pattern = r"^.{1,64}$"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "system_name")

    @property
    def native_value(self) -> str | None:
        """Return the entity's current value."""
        sys_data = self._system
        # systemName is the editable label; deviceName is the app product label.
        return sys_data.get(FIELD_SYSTEM_NAME) or sys_data.get(FIELD_DEVICE_NAME)

    async def async_set_value(self, value: str) -> None:
        """Forward a text write to the device."""
        sys_data = self._system
        system_id = sys_data.get(FIELD_ID) or sys_data.get(FIELD_SYSTEM_ID)
        if not system_id:
            raise HomeAssistantError("No systemId available to rename")

        new_name = (value or "").strip()
        if not new_name:
            raise ValueError("System name must not be empty")

        try:
            ok = await self.coordinator.api.async_set_system_name(system_id, new_name)
        except JackeryError as err:
            _LOGGER.error("Failed to rename system %s: %s", system_id, err)
            raise

        if ok:
            # Optimistic local update so the UI reflects the new name before
            # the next system/list refresh
            sys_data[FIELD_SYSTEM_NAME] = new_name
            self.async_write_ha_state()
            # Trigger a coordinator refresh so every dependent entity also
            # picks up the new name next cycle
            await self.coordinator.async_request_refresh()
