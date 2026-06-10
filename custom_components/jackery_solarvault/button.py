"""Button platform for Jackery SolarVault."""

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .const import DOMAIN, FIELD_REBOOT, PAYLOAD_PROPERTIES
from .entity import JackeryEntity
from .util import append_unique_entity, coordinator_entity_signature

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import JackeryConfigEntry
    from .coordinator import JackerySolarVaultCoordinator

# Limit concurrent control-write/update calls. This is a setter platform:
# writes go to the cloud and to MQTT. Serializing keeps the queue depth on
# the broker bounded and prevents reordering of `DevicePropertyChange`
# commands per HA dev guidance for write-heavy platforms.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(  # noqa: RUF029
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Jackery SolarVault button entities for a config entry.

    Creates and registers reboot button entities for devices in the coordinator's data while avoiding duplicates by unique ID. Monitors a signature of the coordinator data and adds new entities when that signature changes; registers a listener so updates stop when the config entry is unloaded.
    """  # noqa: E501
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[ButtonEntity], entity: ButtonEntity) -> None:
        """Append the given button entity to `entities` if its unique ID has not been seen.

        Parameters:
            entities (list[ButtonEntity]): Target list to receive the entity when added.
            entity (ButtonEntity): Button entity to append; skipped if its unique ID is already tracked.
        """  # noqa: E501
        append_unique_entity(
            entities,
            seen_unique_ids,
            entity,
            platform="button",
            logger=_LOGGER,
        )

    def _collect_entities() -> list[ButtonEntity]:
        """Collect reboot button entities for devices managed by the coordinator.

        Creates a JackeryRebootButton for each device in coordinator.data where the device either reports support for advanced features or exposes the reboot field in its properties. Each entity is added at most once.

        Returns:
            list[ButtonEntity]: ButtonEntity instances corresponding to devices that expose or support the reboot action.
        """  # noqa: E501
        entities: list[ButtonEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload.get(PAYLOAD_PROPERTIES) or {}
            if coordinator.device_supports_advanced(dev_id) or FIELD_REBOOT in props:
                _append_unique(entities, JackeryRebootButton(coordinator, dev_id))
        return entities

    last_signature: tuple[Any, ...] = ()

    def _add_new_entities() -> None:
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
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the reboot button entity for a specific SolarVault device.

        Parameters:
            coordinator (JackerySolarVaultCoordinator): Coordinator that manages device state and actions.
            device_id (str): Unique identifier of the target device.
        """  # noqa: E501
        super().__init__(coordinator, device_id, "reboot_device")

    def _raise_action_error(self, error: object) -> None:
        """Raise a Home Assistant error with translation metadata for a failed entity action.

        Includes translation_domain, translation_key "entity_action_failed", and placeholders:
        - "entity": the entity key ("reboot_device")
        - "device_id": the target device identifier
        - "error": stringified representation of the original error

        Parameters:
            error (object): The original exception or error information to include in the translated message.
        """  # noqa: E501
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
        """Initiates a reboot of the associated device and requests a coordinator refresh.

        Attempts to reboot the device via the coordinator and then requests an immediate data refresh.
        If authentication has failed for the config entry, the original ConfigEntryAuthFailed is re-raised.
        If a HomeAssistantError with a translation_key is raised by the coordinator, it is re-raised unchanged.
        All other HomeAssistantError or generic Exception instances are wrapped and raised through the entity's
        _error translation helper.
        """  # noqa: E501
        try:
            await self.coordinator.async_reboot_device(self._device_id)
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:  # noqa: BLE001
            self._raise_action_error(err)
