"""Binary sensor platform for Jackery SolarVault."""
from __future__ import annotations

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

from .const import DOMAIN
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity


@dataclass(frozen=True, kw_only=True)
class JackeryBinaryDescription(BinarySensorEntityDescription):
    getter: Callable[[dict[str, Any], dict[str, Any]], Any]


# getter receives (properties, device_meta) — that way we can expose both
# device-level flags (onlineStatus) and property-level flags (swEps, swEpsState)
BINARY_DESCRIPTIONS: tuple[JackeryBinaryDescription, ...] = (
    JackeryBinaryDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        getter=lambda p, d: d.get("onlineStatus"),
    ),
    JackeryBinaryDescription(
        key="eps_enabled",
        translation_key="eps_enabled",
        device_class=BinarySensorDeviceClass.POWER,
        getter=lambda p, d: p.get("swEps"),
    ),
    JackeryBinaryDescription(
        key="eps_active",
        translation_key="eps_active",
        device_class=BinarySensorDeviceClass.RUNNING,
        getter=lambda p, d: p.get("swEpsState"),
    ),
    JackeryBinaryDescription(
        key="eth_connected",
        translation_key="eth_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        getter=lambda p, d: p.get("ethPort"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: JackerySolarVaultCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    for dev_id, payload in (coordinator.data or {}).items():
        props = payload.get("properties") or {}
        meta = payload.get("device") or {}
        for desc in BINARY_DESCRIPTIONS:
            val = desc.getter(props, meta)
            if val is not None:
                entities.append(JackeryBinarySensor(coordinator, dev_id, desc))
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
        raw = self.entity_description.getter(self._properties, self._device_meta)
        if raw is None:
            return None
        try:
            return int(raw) == 1
        except (TypeError, ValueError):
            return bool(raw)
