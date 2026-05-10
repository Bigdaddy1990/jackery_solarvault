"""Button platform for Jackery SolarVault."""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .const import DOMAIN, FIELD_REBOOT, PAYLOAD_PROPERTIES
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
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[ButtonEntity], entity: ButtonEntity) -> None:
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="button", logger=_LOGGER
        )

    def _collect_entities() -> list[ButtonEntity]:
        entities: list[ButtonEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload.get(PAYLOAD_PROPERTIES) or {}
            if coordinator.device_supports_advanced(dev_id) or FIELD_REBOOT in props:
                _append_unique(entities, JackeryRebootButton(coordinator, dev_id))
        return entities

    def _add_new_entities() -> None:
        entities = _collect_entities()
        if entities:
            async_add_entities(entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


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
