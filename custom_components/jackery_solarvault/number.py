"""Number platform for Jackery SolarVault.

Description-driven entities; one generic class handles all sliders/boxes.
Specials (max-feed-grid dynamic max, default-power 0.0 fallback,
single-tariff dynamic currency, max-power error handling) live as
optional callables on the description.
"""

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .client.api import JackeryAuthError
from .const import (
    DOMAIN,
    FIELD_CURRENCY,
    FIELD_CURRENCY_CODE,
    FIELD_DEFAULT_PW,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_ID,
    FIELD_MAX_FEED_GRID,
    FIELD_MAX_GRID_STD_PW,
    FIELD_MAX_OUT_PW,
    FIELD_MODEL_CODE,
    FIELD_SINGLE_CURRENCY,
    FIELD_SINGLE_CURRENCY_CODE,
    FIELD_SINGLE_PRICE,
    FIELD_SOC_CHARGE_LIMIT,
    FIELD_SOC_CHG_LIMIT,
    FIELD_SOC_DISCHARGE_LIMIT,
    FIELD_SOC_DISCHG_LIMIT,
    FIELD_SYSTEM_ID,
    FIELD_THIRD_PARTY_MQTT_PORT,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
)
from .entity import JackeryEntity
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    first_nonblank_int,
    safe_float,
    safe_int,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


def _rounded_int(value: Any) -> int:  # noqa: ANN401
    """Return a rounded integer for number values already accepted by HA."""
    parsed = safe_float(value)
    if parsed is None:
        raise HomeAssistantError("invalid number value")  # noqa: TRY003
    return round(parsed)


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
    allowed_values: (
        Callable[[dict[str, Any]], tuple[float, ...]] | tuple[float, ...] | None
    ) = None
    value_transform: Callable[[float], Any] = _rounded_int
    raise_on_setter_error: bool = True
    integer_value: bool = False


# ---------------------------------------------------------------------------
# Setter helpers
# ---------------------------------------------------------------------------


def _wire_int(value: Any) -> int:  # noqa: ANN401
    """Return an integer value prepared for coordinator setter calls."""
    parsed = first_nonblank_int(value)
    if parsed is None:
        raise HomeAssistantError("invalid number value")  # noqa: TRY003
    return parsed


def _wire_float(value: Any) -> float:  # noqa: ANN401
    """Return a float value prepared for coordinator setter calls."""
    parsed = safe_float(value)
    if parsed is None:
        raise HomeAssistantError("invalid number value")  # noqa: TRY003
    return parsed


async def _set_soc_charge(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the SOC charge limit on a device."""
    await coord.async_set_soc_limits(dev_id, charge_limit=_wire_int(value))


async def _set_soc_discharge(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the SOC discharge limit on a device."""
    await coord.async_set_soc_limits(dev_id, discharge_limit=_wire_int(value))


async def _set_max_feed_grid(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the maximum grid feed-in power on a device."""
    parsed = _wire_int(value)
    await coord.async_set_max_feed_grid(dev_id, 800 if parsed <= 800 else 2500)  # noqa: PLR2004


async def _set_max_output_power(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the maximum output power on a device."""
    await coord.async_set_max_output_power(dev_id, _wire_int(value))


async def _set_default_power(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the default-load power preference on a device."""
    await coord.async_set_default_power(dev_id, _wire_int(value))


async def _set_single_price(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the single-tariff electricity price on a device."""
    await coord.async_set_single_price(dev_id, value)


async def _set_third_party_mqtt_port(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the Third-Party MQTT broker port."""
    await coord.async_update_third_party_mqtt_config(
        dev_id,
        {FIELD_THIRD_PARTY_MQTT_PORT: int(value)},
    )


# ---------------------------------------------------------------------------
# Dynamic-value helpers
# ---------------------------------------------------------------------------


def _max_feed_grid_dynamic_max(payload: dict[str, Any]) -> float:
    """Return the feed-in choices exposed by the SolarVault app."""
    props = payload.get(PAYLOAD_PROPERTIES) or {}
    for key in (FIELD_MAX_FEED_GRID, FIELD_MAX_GRID_STD_PW):
        feed_limit = safe_int(props.get(key))
        if feed_limit is not None and feed_limit > 800:  # noqa: PLR2004
            return 2500.0
    for section in (PAYLOAD_DEVICE, PAYLOAD_DISCOVERY):
        meta = payload.get(section) or {}
        if str(meta.get(FIELD_MODEL_CODE) or "") == "3002":
            return 2500.0
    max_out_int = safe_int(props.get(FIELD_MAX_OUT_PW))
    if max_out_int is None:
        max_out_int = 2500
    return 800.0 if max_out_int <= 800 else 2500.0  # noqa: PLR2004


def _max_feed_grid_allowed_values(payload: dict[str, Any]) -> tuple[float, ...]:
    """Jackery's app exposes feed-in as a binary 800/2500W selection."""
    if _max_feed_grid_dynamic_max(payload) <= 800:  # noqa: PLR2004
        return (800.0,)
    return (800.0, 2500.0)


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
        native_min_value=800,
        native_max_value=2500,
        native_step=1700,
        source_keys=(FIELD_MAX_FEED_GRID, FIELD_MAX_GRID_STD_PW),
        setter=_set_max_feed_grid,
        dynamic_max=_max_feed_grid_dynamic_max,
        allowed_values=_max_feed_grid_allowed_values,
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
        value_transform=_wire_float,
    ),
    JackeryNumberDescription(
        key="third_party_mqtt_port",
        translation_key="third_party_mqtt_port",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:numeric",
        native_min_value=1,
        native_max_value=65535,
        native_step=1,
        source_keys=(FIELD_THIRD_PARTY_MQTT_PORT,),
        source_section=PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
        setter=_set_third_party_mqtt_port,
        integer_value=True,
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

    def _raise_action_error(self, translation_key: str, **placeholders: object) -> None:
        """Raise a translatable HA action error for this entity."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders={
                "entity": self.entity_description.key,
                "device_id": self._device_id,
                **{key: str(value) for key, value in placeholders.items()},
            },
        )

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
                fval = safe_float(val)
                if fval is not None and self.entity_description.integer_value:
                    return float(round(fval))
                return fval
        return self.entity_description.none_fallback

    @property
    def native_max_value(self) -> float:
        """Return the highest value the user can write."""
        if self.entity_description.dynamic_max is not None:
            return self.entity_description.dynamic_max(self._payload)
        if self.entity_description.native_max_value is not None:
            return float(self.entity_description.native_max_value)
        return 0.0

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the entity's unit of measurement."""
        if self.entity_description.dynamic_unit is not None:
            return self.entity_description.dynamic_unit(self._payload)
        return self.entity_description.native_unit_of_measurement

    def _allowed_values(self) -> tuple[float, ...]:
        """Return exact values accepted by a discrete Jackery number."""
        allowed = self.entity_description.allowed_values
        if allowed is None:
            return ()
        if callable(allowed):
            return tuple(cast("tuple[float, ...]", allowed(self._payload)))
        return tuple(allowed)

    async def async_set_native_value(self, value: float) -> None:
        """Forward a numeric write to the device."""
        parsed_value = safe_float(value)
        if parsed_value is None:
            self._raise_action_error(
                "invalid_number_range",
                min=f"{self.native_min_value:.0f}",
                max=f"{self.native_max_value:.0f}",
            )
            return
        value = parsed_value
        if value < self.native_min_value or value > self.native_max_value:
            self._raise_action_error(
                "invalid_number_range",
                min=f"{self.native_min_value:.0f}",
                max=f"{self.native_max_value:.0f}",
            )
        allowed = self._allowed_values()
        if allowed and _rounded_int(value) not in {_rounded_int(v) for v in allowed}:
            allowed_text = ", ".join(str(_rounded_int(v)) for v in allowed)
            self._raise_action_error(
                "invalid_number_allowed_values",
                allowed_values=allowed_text,
            )
        if self.entity_description.setter is None:
            return
        wire_value = self.entity_description.value_transform(value)
        try:
            await self.entity_description.setter(
                self.coordinator, self._device_id, wire_value
            )
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            if self.entity_description.raise_on_setter_error:
                self._raise_action_error("entity_action_failed", error=err)
            _LOGGER.debug(
                "Ignoring optional Jackery number setter failure for %s/%s: %s",
                self._device_id,
                self.entity_description.key,
                err,
            )
        except Exception as err:  # noqa: BLE001
            if self.entity_description.raise_on_setter_error:
                self._raise_action_error("entity_action_failed", error=err)
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


async def async_setup_entry(  # noqa: RUF029
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create description-driven number entities."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append(entities: list[NumberEntity], entity: NumberEntity) -> None:
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
        "third_party_mqtt_port": lambda p: (
            FIELD_THIRD_PARTY_MQTT_PORT
            in (p.get(PAYLOAD_THIRD_PARTY_MQTT_CONFIG) or {})
        ),
    }

    def _collect_entities() -> list[NumberEntity]:
        entities: list[NumberEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            for description in NUMBER_DESCRIPTIONS:
                predicate = gating.get(description.key)
                if predicate is None or predicate(payload):
                    _append(entities, JackeryNumber(coordinator, dev_id, description))
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
