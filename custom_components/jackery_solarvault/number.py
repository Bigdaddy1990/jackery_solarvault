"""Number platform for Jackery SolarVault.

Description-driven entities; one generic class handles all sliders/boxes.
Specials (max-feed-grid dynamic max, default-power 0.0 fallback,
single-tariff dynamic currency, max-power error handling) live as
optional callables on the description.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .api import JackeryError
from .const import (
    FIELD_CURRENCY,
    FIELD_CURRENCY_CODE,
    FIELD_DEFAULT_PW,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_ID,
    FIELD_MAX_FEED_GRID,
    FIELD_MAX_GRID_STD_PW,
    FIELD_MAX_OUT_PW,
    FIELD_SINGLE_CURRENCY,
    FIELD_SINGLE_CURRENCY_CODE,
    FIELD_SINGLE_PRICE,
    FIELD_SOC_CHARGE_LIMIT,
    FIELD_SOC_CHG_LIMIT,
    FIELD_SOC_DISCHARGE_LIMIT,
    FIELD_SOC_DISCHG_LIMIT,
    FIELD_SYSTEM_ID,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
)
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import append_unique_entity, safe_float, safe_int

# Limit concurrent control-write/update calls. This is a setter platform:
# writes go to the cloud and to MQTT. Serializing keeps the queue depth on
# the broker bounded and prevents reordering of `DevicePropertyChange`
# commands per HA dev guidance for write-heavy platforms.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class JackeryNumberDescription(NumberEntityDescription):
    """Describes a Jackery number entity.

    The shape mirrors what every old hand-written class had to repeat:
      - source_keys: which payload field(s) hold the current value
      - setter: how to push a new value to the cloud

    Optional fields cover the few real outliers without a fan of subclasses.
    """

    source_keys: tuple[str, ...] = ()
    source_section: str = PAYLOAD_PROPERTIES
    none_fallback: float | None = None
    setter: (
        Callable[[JackerySolarVaultCoordinator, str, Any], Awaitable[None]] | None
    ) = None
    dynamic_max: Callable[[dict[str, Any]], float] | None = None
    dynamic_unit: Callable[[dict[str, Any]], str] | None = None
    value_transform: Callable[[float], Any] = lambda v: int(round(v))
    validate_range: bool = False
    raise_on_setter_error: bool = True


# ---------------------------------------------------------------------------
# Setter helpers
# ---------------------------------------------------------------------------


async def _set_soc_charge(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the SOC charge limit on a device."""
    await coord.async_set_soc_limits(dev_id, charge_limit=value)


async def _set_soc_discharge(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the SOC discharge limit on a device."""
    await coord.async_set_soc_limits(dev_id, discharge_limit=int(value))


async def _set_max_feed_grid(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the maximum grid feed-in power on a device."""
    await coord.async_set_max_feed_grid(dev_id, int(value))


async def _set_max_output_power(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the maximum output power on a device."""
    await coord.async_set_max_output_power(dev_id, int(value))


async def _set_default_power(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the default-load power preference on a device."""
    await coord.async_set_default_power(dev_id, int(value))


async def _set_single_price(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the single-tariff electricity price on a device."""
    await coord.async_set_single_price(dev_id, value)


async def _set_max_power_experimental(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> bool:
    """Direct API call for experimental max-power setter."""
    try:
        ok = await coord.api.async_set_max_power(dev_id, value)
    except JackeryError as err:
        _LOGGER.error(
            "Max-power write failed for device %s (value=%s): %s",
            dev_id,
            value,
            err,
        )
        raise
    if not ok:
        _LOGGER.warning(
            "Server returned data=false for max-power=%sW on device %s",
            value,
            dev_id,
        )
    return ok


# ---------------------------------------------------------------------------
# Dynamic-value helpers
# ---------------------------------------------------------------------------


def _max_feed_grid_dynamic_max(payload: dict[str, Any]) -> float:
    """800W if device max-out is ≤800W, else 2500W (German balcony rule)."""
    props = payload.get(PAYLOAD_PROPERTIES) or {}
    max_out_int = safe_int(props.get(FIELD_MAX_OUT_PW))
    if max_out_int is None:
        max_out_int = 2500
    return 800.0 if max_out_int <= 800 else 2500.0


def _single_tariff_dynamic_unit(payload: dict[str, Any]) -> str:
    """Currency of the single-tariff price; defaults to '€'."""
    price = payload.get(PAYLOAD_PRICE) or {}
    return str(
        price.get(FIELD_SINGLE_CURRENCY)
        or price.get(FIELD_CURRENCY)
        or price.get(FIELD_SINGLE_CURRENCY_CODE)
        or price.get(FIELD_CURRENCY_CODE)
        or "€"
    )


# ---------------------------------------------------------------------------
# Description registry
# ---------------------------------------------------------------------------

NUMBER_DESCRIPTIONS: tuple[JackeryNumberDescription, ...] = (
    JackeryNumberDescription(
        key="soc_charge_limit_set",
        translation_key="soc_charge_limit_set",
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:battery-charging-high",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        source_keys=(FIELD_SOC_CHG_LIMIT, FIELD_SOC_CHARGE_LIMIT),
        setter=_set_soc_charge,
    ),
    JackeryNumberDescription(
        key="soc_discharge_limit_set",
        translation_key="soc_discharge_limit_set",
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:battery-low",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        source_keys=(FIELD_SOC_DISCHG_LIMIT, FIELD_SOC_DISCHARGE_LIMIT),
        setter=_set_soc_discharge,
    ),
    JackeryNumberDescription(
        key="max_output_power_set",
        translation_key="max_output_power_set",
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:flash",
        native_min_value=0,
        native_max_value=2500,
        native_step=10,
        source_keys=(FIELD_MAX_OUT_PW,),
        setter=_set_max_output_power,
    ),
    JackeryNumberDescription(
        key="max_feed_grid",
        translation_key="max_feed_grid",
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:transmission-tower-export",
        native_min_value=0,
        native_max_value=2500,
        native_step=10,
        source_keys=(FIELD_MAX_FEED_GRID, FIELD_MAX_GRID_STD_PW),
        setter=_set_max_feed_grid,
        dynamic_max=_max_feed_grid_dynamic_max,
        validate_range=True,
    ),
    JackeryNumberDescription(
        key="default_power_set",
        translation_key="default_power_set",
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:flash-outline",
        native_min_value=0,
        native_max_value=200,
        native_step=10,
        source_keys=(FIELD_DEFAULT_PW,),
        setter=_set_default_power,
        # Some firmware only sends defaultPw after first change. Keep slider
        # usable instead of exposing an unknown value.
        none_fallback=0.0,
    ),
    JackeryNumberDescription(
        key="single_tariff_price_set",
        translation_key="single_tariff_price_set",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:currency-eur",
        native_min_value=0,
        native_max_value=10,
        native_step=0.01,
        source_keys=(FIELD_SINGLE_PRICE,),
        source_section=PAYLOAD_PRICE,
        setter=_set_single_price,
        dynamic_unit=_single_tariff_dynamic_unit,
        value_transform=lambda v: float(v),
    ),
)


# ---------------------------------------------------------------------------
# Generic entity
# ---------------------------------------------------------------------------


class JackeryNumber(JackeryEntity, NumberEntity):
    """Generic description-driven number entity for Jackery."""

    entity_description: JackeryNumberDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: JackeryNumberDescription,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    def _section(self) -> dict[str, Any]:
        """Read the configured payload section (properties/price/...)."""
        return self._payload.get(self.entity_description.source_section) or {}

    @property
    def native_value(self) -> float | None:
        """Return the entity's current value."""
        section = self._section()
        for key in self.entity_description.source_keys:
            val = section.get(key)
            if val is not None:
                return safe_float(val)
        return self.entity_description.none_fallback

    @property
    def native_max_value(self) -> float:
        """Return the highest value the user can write."""
        if self.entity_description.dynamic_max is not None:
            return self.entity_description.dynamic_max(self._payload)
        return float(self.entity_description.native_max_value)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the entity's unit of measurement."""
        if self.entity_description.dynamic_unit is not None:
            return self.entity_description.dynamic_unit(self._payload)
        return self.entity_description.native_unit_of_measurement

    async def async_set_native_value(self, value: float) -> None:
        """Forward a numeric write to the device."""
        if self.entity_description.validate_range and (
            value < self.native_min_value or value > self.native_max_value
        ):
            raise ValueError(
                f"{self.entity_description.key} must be between "
                f"{self.native_min_value:.0f} and "
                f"{self.native_max_value:.0f}"
            )
        if self.entity_description.setter is None:
            return
        wire_value = self.entity_description.value_transform(value)
        try:
            await self.entity_description.setter(
                self.coordinator, self._device_id, wire_value
            )
        except Exception as err:
            if self.entity_description.raise_on_setter_error:
                raise
            _LOGGER.debug(
                "Ignoring optional Jackery number setter failure for %s/%s: %s",
                self._device_id,
                self.entity_description.key,
                err,
            )
        await self.coordinator.async_request_refresh()


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create description-driven number entities."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    entities: list[NumberEntity] = []
    seen_unique_ids: set[str] = set()

    def _append(entity: NumberEntity) -> None:
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="number", logger=_LOGGER
        )

    def _has_props(payload: dict[str, Any], *keys: str) -> bool:
        props = payload.get(PAYLOAD_PROPERTIES) or {}
        return any(k in props for k in keys)

    def _has_price_or_system(payload: dict[str, Any]) -> bool:
        price = payload.get(PAYLOAD_PRICE) or {}
        system = payload.get(PAYLOAD_SYSTEM) or {}
        return (
            FIELD_SINGLE_PRICE in price
            or FIELD_DYNAMIC_OR_SINGLE in price
            or system.get(FIELD_ID) is not None
            or system.get(FIELD_SYSTEM_ID) is not None
        )

    gating: dict[str, Callable[[dict[str, Any]], bool]] = {
        "soc_charge_limit_set": lambda p: _has_props(p, FIELD_SOC_CHG_LIMIT),
        "soc_discharge_limit_set": lambda p: _has_props(p, FIELD_SOC_DISCHG_LIMIT),
        "max_output_power_set": lambda p: _has_props(p, FIELD_MAX_OUT_PW),
        "max_feed_grid": lambda p: _has_props(
            p, FIELD_MAX_FEED_GRID, FIELD_MAX_GRID_STD_PW, FIELD_MAX_OUT_PW
        ),
        "default_power_set": lambda p: _has_props(p, FIELD_MAX_OUT_PW),
        "single_tariff_price_set": _has_price_or_system,
    }

    for dev_id, payload in (coordinator.data or {}).items():
        for description in NUMBER_DESCRIPTIONS:
            predicate = gating.get(description.key)
            if predicate is None or predicate(payload):
                _append(JackeryNumber(coordinator, dev_id, description))

    async_add_entities(entities)
