"""Text platform for Jackery SolarVault — editable system name."""
from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import JackeryError
from .const import DOMAIN
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JackerySolarVaultCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[TextEntity] = []
    for dev_id, payload in (coordinator.data or {}).items():
        system = payload.get("system") or {}
        # Only add the editor if we know the system id to target
        if system.get("id") or system.get("systemId"):
            entities.append(JackerySystemNameText(coordinator, dev_id))
    async_add_entities(entities)


class JackerySystemNameText(JackeryEntity, TextEntity):
    """Rename the SolarVault system (PUT /v1/device/system/name)."""

    _attr_translation_key = "system_name"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:rename-box"
    _attr_native_min = 1
    _attr_native_max = 64
    _attr_pattern = r"^.{1,64}$"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id, "system_name")

    @property
    def native_value(self) -> str | None:
        sys_data = self._system
        # systemName is the editable label; deviceName is the product name
        return sys_data.get("systemName") or sys_data.get("deviceName")

    async def async_set_value(self, value: str) -> None:
        sys_data = self._system
        system_id = sys_data.get("id") or sys_data.get("systemId")
        if not system_id:
            raise RuntimeError("No systemId available to rename")

        new_name = (value or "").strip()
        if not new_name:
            raise ValueError("System name must not be empty")

        try:
            ok = await self.coordinator.api.async_set_system_name(
                system_id, new_name
            )
        except JackeryError as err:
            _LOGGER.error("Failed to rename system %s: %s", system_id, err)
            raise

        if ok:
            # Optimistic local update so the UI reflects the new name before
            # the next system/list refresh
            sys_data["systemName"] = new_name
            self.async_write_ha_state()
            # Trigger a coordinator refresh so every dependent entity also
            # picks up the new name next cycle
            await self.coordinator.async_request_refresh()
