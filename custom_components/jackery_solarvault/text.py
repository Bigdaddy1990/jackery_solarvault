"""Text platform for Jackery SolarVault — editable system name."""

import logging
from typing import Any

from homeassistant.components.text import TextEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .api import JackeryAuthError
from .api import JackeryError
from .const import DOMAIN
from .const import FIELD_DEVICE_NAME
from .const import FIELD_ID
from .const import FIELD_SYSTEM_ID
from .const import FIELD_SYSTEM_NAME
from .const import PAYLOAD_SYSTEM
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import append_unique_entity
from .util import coordinator_entity_signature

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
    """Set up text entities for Jackery SolarVault systems from a config entry.

    Collects and adds JackerySystemNameText entities for each system that exposes a system identifier, ensuring each unique entity is added only once (deduplicated via a seen-unique-id set and append_unique_entity with platform="text"). The function avoids re-adding entities by tracking a coordinator data signature and only calls async_add_entities when the signature changes. It also registers a listener on the coordinator to repeat this process when coordinator data updates.

    Parameters:
        hass: Home Assistant core instance; the coordinator is read from `entry.runtime_data`.
        entry: The integration config entry; provides `runtime_data` containing the JackerySolarVaultCoordinator.
        async_add_entities: Callback used to add new TextEntity instances to Home Assistant.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[TextEntity], entity: TextEntity) -> None:
        """Add the given TextEntity to the entities list if its unique ID has not been seen before for the text platform.

        Parameters:
            entities (list[TextEntity]): Mutable list of entities to append to.
            entity (TextEntity): Entity to append; its unique ID will be recorded to prevent duplicates.
        """
        append_unique_entity(
            entities,
            seen_unique_ids,
            entity,
            platform="text",
            logger=_LOGGER,
        )

    def _collect_entities() -> list[TextEntity]:
        """Collect text entities for systems that support renaming.

        Creates a JackerySystemNameText entity for each device in the coordinator data whose system payload contains `FIELD_ID` or `FIELD_SYSTEM_ID`, and returns the list of those entities.

        Returns:
            list[TextEntity]: List of created text entities (empty if none).
        """
        entities: list[TextEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            system = payload.get(PAYLOAD_SYSTEM) or {}
            # The rename endpoint in PROTOCOL.md §2 needs the system id.
            if system.get(FIELD_ID) or system.get(FIELD_SYSTEM_ID):
                _append_unique(entities, JackerySystemNameText(coordinator, dev_id))
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


class JackerySystemNameText(JackeryEntity, TextEntity):
    """Rename the SolarVault system using SYSTEM_NAME_PATH from const.py."""

    _attr_translation_key = "system_name"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:rename-box"
    _attr_native_min = 1
    _attr_native_max = 64
    _attr_pattern = r"^.{1,64}$"

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the text entity for a specific Jackery system using the coordinator.

        Parameters:
            coordinator (JackerySolarVaultCoordinator): Coordinator providing system data and API.
            device_id (str): Identifier of the system whose name this entity exposes.
        """
        super().__init__(coordinator, device_id, "system_name")

    @property
    def native_value(self) -> str | None:
        """Provide the current display name for the system, preferring the editable system name.

        Returns:
            str | None: The system's editable name if present, otherwise the device's product name, or `None` if neither is available.
        """
        sys_data = self._system
        # systemName is the editable label; deviceName is the app product label.
        return sys_data.get(FIELD_SYSTEM_NAME) or sys_data.get(FIELD_DEVICE_NAME)

    async def async_set_value(self, value: str) -> None:
        """Set the system's editable name in the cloud and update local state optimistically.

        Validates and normalizes the provided text, sends a rename request to the remote API, applies the new name locally so the UI updates immediately, and requests a coordinator refresh so other entities observe the change on the next update cycle.

        Parameters:
            value (str): The new system name to apply.

        Raises:
            HomeAssistantError: If the device lacks a resolvable system id or the provided name is empty/invalid, or if the remote service reports failure.
            ConfigEntryAuthFailed: If authentication to the Jackery API fails and re-authentication is required.
        """
        sys_data = self._system
        system_id = sys_data.get(FIELD_ID) or sys_data.get(FIELD_SYSTEM_ID)
        if not system_id:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="missing_system_id",
                translation_placeholders={"device_id": self._device_id},
            )

        new_name = (value or "").strip()
        if not new_name:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_text_value",
                translation_placeholders={
                    "entity": "system_name",
                    "device_id": self._device_id,
                },
            )

        try:
            ok = await self.coordinator.api.async_set_system_name(system_id, new_name)
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed(
                "Jackery credentials were rejected while renaming a system. "
                "Re-authentication is required.",
            ) from err
        except JackeryError as err:
            _LOGGER.debug("Failed to rename system %s: %s", system_id, err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="rename_system_failed",
                translation_placeholders={
                    "system_id": str(system_id),
                    "error": str(err),
                },
            ) from err

        if not ok:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="rename_system_failed",
                translation_placeholders={
                    "system_id": str(system_id),
                    "error": "server returned false",
                },
            )

        # Optimistic local update so the UI reflects the new name before
        # the next system/list refresh
        sys_data[FIELD_SYSTEM_NAME] = new_name
        self.async_write_ha_state()
        # Trigger a coordinator refresh so every dependent entity also
        # picks up the new name next cycle
        await self.coordinator.async_request_refresh()
