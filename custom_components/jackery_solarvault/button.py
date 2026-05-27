"""Button platform for Jackery SolarVault."""

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .const import DOMAIN, FIELD_REBOOT, PAYLOAD_PROPERTIES
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import append_unique_entity, coordinator_entity_signature

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
    """
    Set up reboot Button entities for devices in the config entry.
    
    Create a JackeryRebootButton for each coordinator-managed device that either reports support for advanced features or exposes the reboot property, avoid registering duplicate entities, and only add entities when the coordinator-derived device signature changes. Registers a coordinator listener to update discovery when the signature changes.
    
    Parameters:
        entry (JackeryConfigEntry): Config entry whose runtime_data contains the integration coordinator.
        async_add_entities (AddEntitiesCallback): Callback to register new ButtonEntity instances with Home Assistant.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[ButtonEntity], entity: ButtonEntity) -> None:
        """
        Append a ButtonEntity to the list if its unique identifier has not been recorded, and record it to prevent duplicate button entities.
        
        Parameters:
            entities (list[ButtonEntity]): Target list to append the entity to when it is unique.
            entity (ButtonEntity): Button entity to append if its unique identifier has not been seen.
        """
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="button", logger=_LOGGER
        )

    def _collect_entities() -> list[ButtonEntity]:
        """
        Collect reboot button entities for devices managed by the coordinator.
        
        Create a JackeryRebootButton for each device that either supports advanced features or exposes the reboot property; duplicate entities are omitted.
        
        Returns:
            list[ButtonEntity]: Unique `ButtonEntity` instances representing reboot actions for matching devices.
        """
        entities: list[ButtonEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload.get(PAYLOAD_PROPERTIES) or {}
            if coordinator.device_supports_advanced(dev_id) or FIELD_REBOOT in props:
                _append_unique(entities, JackeryRebootButton(coordinator, dev_id))
        return entities

    last_signature: tuple[Any, ...] = ()

    def _add_new_entities() -> None:
        """
        Register newly discovered reboot button entities when the coordinator's device signature changes.
        
        If the coordinator-derived signature differs from the last cached signature, update the cache, collect new entities, and add them via `async_add_entities`.
        """
        nonlocal last_signature
        sig = coordinator_entity_signature(coordinator.data)
        if sig == last_signature:
            return
        last_signature = sig
        entities = _collect_entities()
        if entities:
            async_add_entities(entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class JackeryRebootButton(JackeryEntity, ButtonEntity):
    """Restart the SolarVault device via PROTOCOL.md §4 reboot command."""

    _attr_translation_key = "reboot_device"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "reboot_device")

    def _raise_action_error(self, error: object) -> None:
        """Raise a translatable HA action error for this button."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": "reboot_device",
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def async_press(self) -> None:
        """Forward a button press to the device."""
        try:
            await self.coordinator.async_reboot_device(self._device_id)
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:
            self._raise_action_error(err)
