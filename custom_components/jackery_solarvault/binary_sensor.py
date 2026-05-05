"""Binary sensor platform for Jackery SolarVault."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import JackerySolarVaultCoordinator
from .const import FIELD_ETH_PORT, FIELD_ONLINE_STATUS, FIELD_SW_EPS_STATE
from .entity import JackeryEntity
from .util import append_unique_entity, safe_bool

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class JackeryBinaryDescription(BinarySensorEntityDescription):
    getter: Callable[[dict[str, Any], dict[str, Any]], Any]


# Getter receives (properties, device_meta). Field constants mirror the app/API
# payload names documented in APP_POLLING_MQTT.md.
BINARY_DESCRIPTIONS: tuple[JackeryBinaryDescription, ...] = (
    JackeryBinaryDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        getter=lambda p, d: d.get(FIELD_ONLINE_STATUS),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryBinaryDescription(
        key="eps_active",
        translation_key="eps_active",
        device_class=BinarySensorDeviceClass.RUNNING,
        getter=lambda p, d: p.get(FIELD_SW_EPS_STATE),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryBinaryDescription(
        key="eth_connected",
        translation_key="eth_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        getter=lambda p, d: p.get(FIELD_ETH_PORT),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    seen_unique_ids: set[str] = set()

    def _append_unique(entity: BinarySensorEntity) -> None:
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="binary_sensor", logger=_LOGGER
        )

    for dev_id, payload in (coordinator.data or {}).items():
        for desc in BINARY_DESCRIPTIONS:
            _append_unique(JackeryBinarySensor(coordinator, dev_id, desc))
    async_add_entities(entities)


class JackeryBinarySensor(JackeryEntity, BinarySensorEntity):
    entity_description: JackeryBinaryDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: JackeryBinaryDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return safe_bool(
            self.entity_description.getter(self._properties, self._device_meta)
        )
