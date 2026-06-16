"""Text platform for Jackery SolarVault — editable system name."""

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .client import JackeryAuthError, JackeryError
from .const import (
    DOMAIN,
    FIELD_DEVICE_NAME,
    FIELD_GRID_STANDARD,
    FIELD_ID,
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_NAME,
    FIELD_THIRD_PARTY_MQTT_IP,
    FIELD_THIRD_PARTY_MQTT_PASSWORD,
    FIELD_THIRD_PARTY_MQTT_TOKEN,
    FIELD_THIRD_PARTY_MQTT_USERNAME,
    PAYLOAD_SYSTEM,
)
from .entity import JackeryEntity
from .exceptions import ACTION_WRITE_ERRORS
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

_THIRD_PARTY_MQTT_TEXT_FIELDS: tuple[
    tuple[str, str, str, TextMode, str | None],
    ...,
] = (
    (
        "third_party_mqtt_ip",
        "third_party_mqtt_ip",
        FIELD_THIRD_PARTY_MQTT_IP,
        TextMode.TEXT,
        None,
    ),
    (
        "third_party_mqtt_username",
        "third_party_mqtt_username",
        FIELD_THIRD_PARTY_MQTT_USERNAME,
        TextMode.TEXT,
        None,
    ),
    (
        "third_party_mqtt_password",
        "third_party_mqtt_password",
        FIELD_THIRD_PARTY_MQTT_PASSWORD,
        TextMode.PASSWORD,
        None,
    ),
    (
        "third_party_mqtt_token",
        "third_party_mqtt_token",
        FIELD_THIRD_PARTY_MQTT_TOKEN,
        TextMode.TEXT,
        r"^\d{0,9}$",
    ),
)


async def async_setup_entry(  # noqa: RUF029  # HA awaits this entry point
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Register and keep updated text configuration entities for Jackery SolarVault devices.
    
    Retrieves the coordinator from the entry and creates text entities for each device
    based on supported configuration fields: system name (if device has a system identifier),
    grid standard (if present), and third-party MQTT fields (if device supports advanced
    features or Bluetooth). Prevents duplicate entity registrations by tracking unique IDs,
    and uses a signature of coordinator data to detect when the set of devices or
    configuration changes. Registers a coordinator listener that automatically adds new
    entities on subsequent data changes, with cleanup handled on entry unload.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[TextEntity], entity: TextEntity) -> None:
        """Append a TextEntity to the provided list if its unique identifier has not.

        been registered.

        Modifies the `entities` list by appending `entity` when its unique id is new,
        and records that id to prevent duplicate entities from being added.

        Parameters:
            entities (list[TextEntity]): Target list to which the entity will be
            appended if allowed.
            entity (TextEntity): Candidate text entity to append.
        """
        append_unique_entity(
            entities,
            seen_unique_ids,
            entity,
            platform="text",
            logger=_LOGGER,
        )

    def _collect_entities() -> list[TextEntity]:
        """Collects text entities for devices that expose a system identifier.

        Creates a JackerySystemNameText for each coordinator data entry whose system
        contains either `FIELD_ID` or `FIELD_SYSTEM_ID`.

        Returns:
            list[TextEntity]: TextEntity instances created for devices that support
            renaming their system.
        """
        entities: list[TextEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            system = payload.get(PAYLOAD_SYSTEM) or {}
            # The rename endpoint in PROTOCOL.md §2 needs the system id.
            if system.get(FIELD_ID) or system.get(FIELD_SYSTEM_ID):
                _append_unique(entities, JackerySystemNameText(coordinator, dev_id))
            if isinstance(system, dict) and FIELD_GRID_STANDARD in system:
                _append_unique(entities, JackeryGridStandardText(coordinator, dev_id))
            if coordinator.device_supports_advanced(
                dev_id,
            ) or coordinator.device_bluetooth_key(dev_id):
                for (
                    key_suffix,
                    translation_key,
                    field,
                    mode,
                    pattern,
                ) in _THIRD_PARTY_MQTT_TEXT_FIELDS:
                    _append_unique(
                        entities,
                        JackeryThirdPartyMqttText(
                            coordinator,
                            dev_id,
                            key_suffix=key_suffix,
                            translation_key=translation_key,
                            field=field,
                            mode=mode,
                            pattern=pattern,
                        ),
                    )
        return entities

    last_signature: tuple[Any, ...] = ()

    @callback
    def _add_new_entities() -> None:
        """
        Register text entities when the coordinator's device data changes.
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
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "system_name")

    @property
    def native_value(self) -> str | None:
        """Return the current editable system name for the device.

        Prefers the stored system name (FIELD_SYSTEM_NAME); falls back to the device
        product name (FIELD_DEVICE_NAME). Returns None if neither value is available.

        Returns:
            The editable system name, the device product name, or None.
        """
        sys_data = self._system
        # systemName is the editable label; deviceName is the app product label.
        return sys_data.get(FIELD_SYSTEM_NAME) or sys_data.get(FIELD_DEVICE_NAME)

    async def async_set_value(self, value: str) -> None:
        """Rename the remote system and update local state so the change appears in the.

        UI.

        Trims leading and trailing whitespace from `value`, sends the rename request to
        the Jackery API, applies an optimistic local update on success, and requests a
        coordinator refresh so dependent entities reflect the new name.

        Parameters:
            value (str): New system name; leading and trailing whitespace will be
            removed.

        Raises:
            ConfigEntryAuthFailed: If the API rejects credentials and re-authentication
            is required.
            HomeAssistantError: If the system identifier is missing, the trimmed name
            is empty, or the remote API reports a failure.
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
            msg = (
                "Jackery credentials were rejected while renaming a system. "
                "Re-authentication is required."
            )
            raise ConfigEntryAuthFailed(
                msg,
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


class JackeryGridStandardText(JackeryEntity, TextEntity):
    """Write the app grid-standard code via SYNC_GRID_STANDARD."""

    _attr_translation_key = "grid_standard"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:transmission-tower"
    _attr_native_min = 1
    _attr_native_max = 8
    _attr_pattern = r"^\d{1,8}$"

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Initialise the grid-standard text entity."""
        super().__init__(coordinator, device_id, "grid_standard")

    @property
    def native_value(self) -> str | None:
        """Return the current app grid-standard code."""
        raw = self._system.get(FIELD_GRID_STANDARD)
        if raw in {None, ""}:
            return None
        return str(raw)

    async def async_set_value(self, value: str) -> None:
        """Write the grid-standard code using the app's safety/unbind body."""
        new_value = str(value or "").strip()
        if not new_value.isdecimal():
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_text_value",
                translation_placeholders={
                    "entity": "grid_standard",
                    "device_id": self._device_id,
                },
            )
        try:
            await self.coordinator.async_sync_grid_standard(
                self._device_id,
                int(new_value),
            )
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="entity_action_failed",
                translation_placeholders={
                    "entity": "grid_standard",
                    "device_id": self._device_id,
                    "error": str(err),
                },
            ) from err
        except ACTION_WRITE_ERRORS as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="entity_action_failed",
                translation_placeholders={
                    "entity": "grid_standard",
                    "device_id": self._device_id,
                    "error": str(err),
                },
            ) from err


class JackeryThirdPartyMqttText(JackeryEntity, TextEntity):
    """Editable ThirdPartMQTTConfig string field."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min = 0
    _attr_native_max = 128

    def __init__(  # noqa: PLR0913
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        key_suffix: str,
        translation_key: str,
        field: str,
        mode: TextMode,
        pattern: str | None,
    ) -> None:
        """Initialise the Third-Party MQTT text field."""
        super().__init__(coordinator, device_id, key_suffix)
        self._field = field
        self._attr_translation_key = translation_key
        self._attr_mode = mode
        if pattern is not None:
            self._attr_pattern = pattern

    @property
    def native_value(self) -> str | None:
        """Return the current plaintext value used for writes."""
        value = self.coordinator.third_party_mqtt_config_plaintext(self._device_id).get(
            self._field,
        )
        if value is None:
            return None
        return str(value)

    def _raise_action_error(self, error: object) -> None:
        """Raise a translatable HA action error for this text entity."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": str(self._attr_translation_key),
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def async_set_value(self, value: str) -> None:
        """Write this ThirdPartMQTTConfig string field."""
        new_value = str(value or "").strip()
        try:
            await self.coordinator.async_update_third_party_mqtt_config(
                self._device_id,
                {self._field: new_value},
            )
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except ACTION_WRITE_ERRORS as err:
            self._raise_action_error(err)
