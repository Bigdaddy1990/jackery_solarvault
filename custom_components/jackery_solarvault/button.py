"""Button platform for Jackery SolarVault."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .const import FIELD_REBOOT, PAYLOAD_PROPERTIES
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
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the platform from a config entry."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    entities: list[ButtonEntity] = []
    seen_unique_ids: set[str] = set()

    def _append_unique(entity: ButtonEntity) -> None:
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="button", logger=_LOGGER
        )

    for dev_id, payload in (coordinator.data or {}).items():
        props = payload.get(PAYLOAD_PROPERTIES) or {}
        if coordinator.device_supports_advanced(dev_id) or FIELD_REBOOT in props:
            _append_unique(JackeryRebootButton(coordinator, dev_id))

    async_add_entities(entities)


class JackeryRebootButton(JackeryEntity, ButtonEntity):
    """Restart the SolarVault device via MQTT_PROTOCOL.md reboot command."""

    _attr_translation_key = "reboot_device"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "reboot_device")

    async def async_press(self) -> None:
        """Forward a button press to the device."""
        await self.coordinator.async_reboot_device(self._device_id)
        await self.coordinator.async_request_refresh()
