"""Number platform for Jackery SolarVault.

Description-driven entities; one generic class handles all sliders/boxes.
Specials (max-feed-grid dynamic max, default-power 0.0 fallback,
single-tariff dynamic currency, max-power error handling) live as
optional callables on the description.
"""

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .api import JackeryAuthError
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
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
)
from .entity import JackeryEntity
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
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
    value_transform: Callable[[float], Any] = round
    validate_range: bool = False
    raise_on_setter_error: bool = True


# ---------------------------------------------------------------------------
# Setter helpers
# ---------------------------------------------------------------------------


async def _set_soc_charge(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the SOC charge limit on a device."""
    await coord.async_set_soc_limits(dev_id, charge_limit=value)


async def _set_soc_discharge(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the SOC discharge limit on a device."""
    await coord.async_set_soc_limits(dev_id, discharge_limit=int(value))


async def _set_max_feed_grid(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the maximum grid feed-in power on a device."""
    await coord.async_set_max_feed_grid(dev_id, 800 if int(value) <= 800 else 2500)  # noqa: PLR2004


async def _set_max_output_power(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the maximum output power on a device."""
    await coord.async_set_max_output_power(dev_id, int(value))


async def _set_default_power(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the default-load power preference on a device."""
    await coord.async_set_default_power(dev_id, int(value))


async def _set_single_price(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the single-tariff electricity price on a device."""
    await coord.async_set_single_price(dev_id, value)


# ---------------------------------------------------------------------------
# Dynamic-value helpers
# ---------------------------------------------------------------------------


def _max_feed_grid_dynamic_max(payload: dict[str, Any]) -> float:
    """Determine the maximum allowed feed-in power for the device based on its coordinator payload.

    Checks the payload properties and metadata to decide whether the app exposes a low (800 W) or high (2500 W) feed-in option.

    Parameters:
        payload (dict[str, Any]): Coordinator payload containing sections like `properties`, `device`, and `discovery`.

    Returns:
        float: `800.0` if the device reports a maximum output power of 800 W or lower and no indicators of higher capability are present; `2500.0` otherwise.
    """  # noqa: E501
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
        or "€",
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
        value_transform=float,
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
        """Raise a Home Assistant error that includes translation metadata and entity-specific placeholders.

        Parameters:
            translation_key (str): Translation key identifying the error message.
            **placeholders (object): Additional placeholder values to include in the translation; each value will be stringified.

        Raises:
            HomeAssistantError: Error populated with `translation_domain=DOMAIN`, the provided `translation_key`, and `translation_placeholders` containing the entity key (`"entity"`), device id (`"device_id"`), and the provided placeholders (stringified).
        """  # noqa: E501
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

    def _allowed_values(self) -> tuple[float, ...]:
        """Return exact values accepted by a discrete Jackery number."""
        allowed = self.entity_description.allowed_values
        if allowed is None:
            return ()
        if callable(allowed):
            return allowed(self._payload)
        return allowed

    async def async_set_native_value(self, value: float) -> None:
        """Set the entity's native numeric value on the device, enforcing configured range and allowed-values constraints.

        Parameters:
            value (float): The new native (Home Assistant) value to write to the device.

        Raises:
            ConfigEntryAuthFailed: If the underlying setter reports an authentication failure.
            HomeAssistantError: If the value is outside the configured min/max or not in the allowed-values set (translation keys `invalid_number_range` or `invalid_number_allowed_values`), or if the setter fails and the description requests errors be raised (`entity_action_failed`).
        """  # noqa: E501
        if self.entity_description.validate_range and (
            value < self.native_min_value or value > self.native_max_value
        ):
            self._raise_action_error(
                "invalid_number_range",
                min=f"{self.native_min_value:.0f}",
                max=f"{self.native_max_value:.0f}",
            )
        allowed = self._allowed_values()
        if allowed and round(value) not in {round(v) for v in allowed}:
            allowed_text = ", ".join(f"{int(v)}" for v in allowed)
            self._raise_action_error(
                "invalid_number_allowed_values",
                allowed_values=allowed_text,
            )
        if self.entity_description.setter is None:
            return
        wire_value = self.entity_description.value_transform(value)
        try:
            await self.entity_description.setter(
                self.coordinator,
                self._device_id,
                wire_value,
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
    """Create and add Jackery number entities for devices present in the coordinator.

    Scans the coordinator payload to instantiate description-driven NumberEntity objects (using NUMBER_DESCRIPTIONS) for each device when their required payload fields are present, adds them via the provided async_add_entities callback while preventing duplicate unique IDs, and registers a listener to add new entities when the coordinator data signature changes.
    """  # noqa: E501
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append(entities: list[NumberEntity], entity: NumberEntity) -> None:
        """Append a NumberEntity to the provided list if its unique ID has not already been seen.

        Mutates the `entities` list by adding `entity` when its unique identifier is new, and records that identifier to prevent duplicate additions. Uses the integration's "number" platform and module logger for uniqueness tracking and diagnostics.

        Parameters:
            entities (list[NumberEntity]): List to which the entity will be appended when unique.
            entity (NumberEntity): The entity to append.
        """  # noqa: E501
        append_unique_entity(
            entities,
            seen_unique_ids,
            entity,
            platform="number",
            logger=_LOGGER,
        )

    def _has_props(payload: dict[str, Any], *keys: str) -> bool:
        """Check whether any of the given property keys exist in the payload's properties section.

        Parameters:
            payload (dict[str, Any]): The full payload containing sections such as properties.
            *keys (str): One or more property key names to look for in the payload's properties.

        Returns:
            bool: `True` if any of the provided keys are present in the payload's properties section, `False` otherwise.
        """  # noqa: E501
        props = payload.get(PAYLOAD_PROPERTIES) or {}
        return any(k in props for k in keys)

    def _has_price_or_system(payload: dict[str, Any]) -> bool:
        """Determine whether the payload includes single-price data or a system identifier.

        Parameters:
            payload (dict[str, Any]): Device payload, expected to contain `PAYLOAD_PRICE`
                and/or `PAYLOAD_SYSTEM` sections.

        Returns:
            `true` if `FIELD_SINGLE_PRICE` or `FIELD_DYNAMIC_OR_SINGLE` is present in the
            price section, or if `FIELD_ID` or `FIELD_SYSTEM_ID` is present in the system
            section; `false` otherwise.
        """  # noqa: E501
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
            p,
            FIELD_MAX_FEED_GRID,
            FIELD_MAX_GRID_STD_PW,
            FIELD_MAX_OUT_PW,
        ),
        "default_power_set": lambda p: _has_props(p, FIELD_MAX_OUT_PW),
        "single_tariff_price_set": _has_price_or_system,
    }

    def _collect_entities() -> list[NumberEntity]:
        """Builds a list of JackeryNumber entities for devices whose payloads satisfy the configured gating predicates.

        Returns:
            list[NumberEntity]: NumberEntity instances created for each device and description where the device payload is present and the optional gating predicate permits creation.
        """  # noqa: E501
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
