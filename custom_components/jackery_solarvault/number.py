"""Number platform for Jackery SolarVault (experimental write support).

This file contains experimental setter entities whose underlying API
endpoints have been discovered via packet capture but not yet verified
with a successful call. They default to `disabled` and carry an
`experimental:` marker in their translation key.
"""
from __future__ import annotations

import logging

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfPower
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
    entities: list[NumberEntity] = []
    for dev_id, payload in (coordinator.data or {}).items():
        props = payload.get("properties") or {}
        # Only offer the setter when we've seen the read-side value in props
        if "maxOutPw" in props or "maxGridStdPw" in props:
            entities.append(JackeryMaxPowerNumber(coordinator, dev_id))
    async_add_entities(entities)


class JackeryMaxPowerNumber(JackeryEntity, NumberEntity):
    """Experimental: adjust the grid feed-in maximum power.

    ⚠️  Disabled by default. Only failed writes (code=10600) have been
    observed so far — the underlying endpoint may be an audit log rather
    than the live setter. Use at your own risk; the integration will
    surface any server error so you can report it back.
    """

    _attr_translation_key = "max_power_experimental"
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:flash-triangle"
    _attr_entity_registry_enabled_default = False
    # Safe envelope for a balcony power plant in Germany.
    # The hardware can output up to 2500W but feed-in is usually capped
    # at 800W (new German 2024 rules allow up to 800W without registration).
    _attr_native_min_value = 0
    _attr_native_max_value = 2500
    _attr_native_step = 10

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id, "max_power_experimental")

    @property
    def native_value(self) -> float | None:
        props = self._properties
        # Prefer maxOutPw (captured as 800 in diagnostics); fall back to
        # maxGridStdPw which may reflect the grid-standard limit
        val = props.get("maxOutPw")
        if val is None:
            val = props.get("maxGridStdPw")
        try:
            return float(val) if val is not None else None
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        new_watts = int(round(value))
        try:
            ok = await self.coordinator.api.async_set_max_power(
                self._device_id, new_watts
            )
        except JackeryError as err:
            _LOGGER.error(
                "Max-power write failed for device %s (value=%s): %s",
                self._device_id, new_watts, err,
            )
            # Re-raise so HA surfaces the error to the user in the UI
            raise

        if not ok:
            _LOGGER.warning(
                "Server returned data=false for max-power=%sW on device %s",
                new_watts, self._device_id,
            )

        # Regardless of outcome, trigger a refresh so the UI reflects the
        # server's actual state (which may still be the old value if the
        # write didn't take effect)
        await self.coordinator.async_request_refresh()
