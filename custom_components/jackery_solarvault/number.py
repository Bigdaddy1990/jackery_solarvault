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
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .client import JackeryAuthError
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
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    safe_float,
    safe_int,
)

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
    value_transform: Callable[[float], Any] = lambda v: round(v)
    validate_range: bool = False
    raise_on_setter_error: bool = True


# ---------------------------------------------------------------------------
# Setter helpers
# ---------------------------------------------------------------------------


async def _set_soc_charge(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """
    Set the state-of-charge (SOC) charge limit for the specified device.
    
    Parameters:
        coord (JackerySolarVaultCoordinator): Coordinator used to send the update.
        dev_id (str): Device identifier.
        value (float): Desired SOC charge limit; converted to an integer percentage before sending.
    """
    await coord.async_set_soc_limits(dev_id, charge_limit=int(value))


async def _set_soc_discharge(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the SOC discharge limit on a device."""
    await coord.async_set_soc_limits(dev_id, discharge_limit=int(value))


async def _set_max_feed_grid(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """Set the maximum grid feed-in power on a device."""
    await coord.async_set_max_feed_grid(dev_id, 800 if int(value) <= 800 else 2500)


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
    """
    Set the device's single-tariff electricity price.
    
    The price must be expressed in the device's current currency/unit and match the entity's unit of measurement.
    
    Parameters:
        value (float): Price to set, in the device's currency/unit.
    """
    await coord.async_set_single_price(dev_id, value)


async def _set_third_party_mqtt_port(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: float
) -> None:
    """
    Set the third-party MQTT broker port for a device.
    
    Parameters:
        coord (JackerySolarVaultCoordinator): Coordinator used to update the device configuration.
        dev_id (str): Identifier of the device to update.
        value (float): Port number to set; converted to an integer before writing.
    """
    await coord.async_update_third_party_mqtt_config(
        dev_id,
        {FIELD_THIRD_PARTY_MQTT_PORT: int(value)},
    )


# ---------------------------------------------------------------------------
# Dynamic-value helpers
# ---------------------------------------------------------------------------


def _max_feed_grid_dynamic_max(payload: dict[str, Any]) -> float:
    """
    Determine the device's maximum feed-in option (either 800.0 or 2500.0) from the coordinator payload.
    
    The function examines payload properties and metadata (model code and various feed/grid fields) to decide whether a higher-capacity feed-in option (2500.0) is supported; if no indicators of higher capacity are found it returns 800.0, using a fallback maximum-outlet value when present.
    
    Returns:
        `2500.0` when payload indicates a higher-capacity feed-in option, `800.0` otherwise.
    """
    props = payload.get(PAYLOAD_PROPERTIES) or {}
    for key in (FIELD_MAX_FEED_GRID, FIELD_MAX_GRID_STD_PW):
        feed_limit = safe_int(props.get(key))
        if feed_limit is not None and feed_limit > 800:
            return 2500.0
    for section in (PAYLOAD_DEVICE, PAYLOAD_DISCOVERY):
        meta = payload.get(section) or {}
        if str(meta.get(FIELD_MODEL_CODE) or "") == "3002":
            return 2500.0
    max_out_int = safe_int(props.get(FIELD_MAX_OUT_PW))
    if max_out_int is None:
        max_out_int = 2500
    return 800.0 if max_out_int <= 800 else 2500.0


def _max_feed_grid_allowed_values(payload: dict[str, Any]) -> tuple[float, ...]:
    """Jackery's app exposes feed-in as a binary 800/2500W selection."""
    if _max_feed_grid_dynamic_max(payload) <= 800:
        return (800.0,)
    return (800.0, 2500.0)


def _single_tariff_dynamic_unit(payload: dict[str, Any]) -> str:
    """
    Return the currency string used for the single-tariff price, falling back to "€" if no currency fields are present.
    
    Parameters:
        payload (dict[str, Any]): Coordinator payload that may contain a PAYLOAD_PRICE mapping.
    
    Returns:
        str: The currency/unit found in PAYLOAD_PRICE using FIELD_SINGLE_CURRENCY, FIELD_CURRENCY, FIELD_SINGLE_CURRENCY_CODE, or FIELD_CURRENCY_CODE, or "€" if none are present.
    """
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
        validate_range=True,
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
        """
        Initialize the JackeryNumber entity using the coordinator, device identifier, and entity description.
        
        Parameters:
            coordinator (JackerySolarVaultCoordinator): Coordinator providing device payloads and write helpers.
            device_id (str): Unique identifier of the device this entity represents.
            description (JackeryNumberDescription): Description that defines how the number entity reads, presents, and writes values.
        """
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    def _raise_action_error(self, translation_key: str, **placeholders: object) -> None:
        """
        Raise a translatable Home Assistant action error tied to this entity.
        
        Parameters:
            translation_key (str): Translation key used to look up the localized error message.
            **placeholders (object): Additional placeholder values included in the translation; each value is converted to a string.
        
        Raises:
            HomeAssistantError: Error with translation_domain set to DOMAIN and translation_placeholders containing:
                - "entity": the entity description key
                - "device_id": this entity's device id
                - any provided placeholders (stringified)
        """
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
        """
        Selects the payload section dictionary used as the source for this entity.
        
        If the description's source_section is the third-party MQTT config, the coordinator's plaintext MQTT config for the device is returned; otherwise the configured section from the cached payload is returned.
        
        Returns:
            dict[str, Any]: The section dictionary to read values from, or an empty dict if the section is missing or not a dict.
        """
        if self.entity_description.source_section == PAYLOAD_THIRD_PARTY_MQTT_CONFIG:
            section = self.coordinator.third_party_mqtt_config_plaintext(
                self._device_id
            )
        else:
            section = self._payload.get(self.entity_description.source_section) or {}
        return section if isinstance(section, dict) else {}

    @property
    def native_value(self) -> float | None:
        """
        Get the entity's current numeric value from its configured payload section.
        
        Looks up keys in the description's `source_keys` in order and returns the first non-`None` value converted to a float; if no key yields a value, returns the description's `none_fallback`.
        
        Returns:
            float: The converted value from payload, or the description's `none_fallback` (may be `None`).
        """
        section = self._section()
        for key in self.entity_description.source_keys:
            val = section.get(key)
            if val is not None:
                return safe_float(val)
        return self.entity_description.none_fallback

    @property
    def native_max_value(self) -> float:
        """
        Get the maximum writable value for the entity.
        
        If a dynamic maximum is configured on the description, that value is obtained from the current payload.
        Otherwise the statically configured maximum is returned; if the static maximum is unset, returns 0.0.
        
        Returns:
            float: Maximum writable value; 0.0 when no static maximum is configured.
        """
        if self.entity_description.dynamic_max is not None:
            return self.entity_description.dynamic_max(self._payload)
        max_value = self.entity_description.native_max_value
        if max_value is None:
            return 0.0
        return float(max_value)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """The entity's unit of measurement, using a dynamic unit computed from the current payload when available.

        Returns:
            The unit of measurement string, or None if no unit is configured.
        """
        if self.entity_description.dynamic_unit is not None:
            return self.entity_description.dynamic_unit(self._payload)
        return self.entity_description.native_unit_of_measurement

    def _allowed_values(self) -> tuple[float, ...]:
        """Get the discrete native values allowed for this number entity.

        If the description's `allowed_values` is None, returns an empty tuple. If it is a callable, returns the tuple produced by calling it with the current coordinator payload; otherwise returns the configured tuple directly.

        Returns:
            tuple[float, ...]: Allowed native float values, or an empty tuple when no discrete constraint is defined.
        """
        allowed = self.entity_description.allowed_values
        if allowed is None:
            return ()
        if callable(allowed):
            return allowed(self._payload)
        return allowed

    async def async_set_native_value(self, value: float) -> None:
        """Write the given native numeric value to the device, enforcing description-driven validation and invoking the configured setter.

        Validates the value against the description's min/max when `validate_range` is True and against discrete `allowed_values` when present. If a setter is configured, the native value is transformed with the description's `value_transform` and passed to the setter. Setter authentication failures are converted to `ConfigEntryAuthFailed`. If a `HomeAssistantError` raised by the setter already contains a `translation_key` it is re-raised; otherwise, the error is either raised as a translated action error when `raise_on_setter_error` is True or ignored. A coordinator refresh is always requested after the write attempt.

        Parameters:
            value (float): The native numeric value to write.

        Raises:
            ConfigEntryAuthFailed: If the setter reports an authentication failure.
            HomeAssistantError: For invalid range or allowed-value violations, or when `raise_on_setter_error` is True and the setter fails.
        """
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
        except Exception as err:
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


async def async_setup_entry(  # noqa: RUF029  # HA awaits this entry point
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create description-driven number entities."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append(entities: list[NumberEntity], entity: NumberEntity) -> None:
        """
        Append an entity to the list if its unique ID has not already been seen.
        
        Parameters:
        	entities (list[NumberEntity]): Target list of entities to append into.
        	entity (NumberEntity): Entity to conditionally add; will be skipped if its unique ID is already present.
        """
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="number", logger=_LOGGER
        )

    def _has_props(payload: dict[str, Any], *keys: str) -> bool:
        """
        Check whether any of the specified keys exist in the payload's properties section (PAYLOAD_PROPERTIES).
        
        Parameters:
            payload (dict[str, Any]): The device payload dictionary to inspect.
            *keys (str): One or more property keys to look for inside the payload's properties section.
        
        Returns:
            `true` if any of the provided keys are present in the properties section, `false` otherwise.
        """
        props = payload.get(PAYLOAD_PROPERTIES) or {}
        return any(k in props for k in keys)

    def _has_price_or_system(payload: dict[str, Any]) -> bool:
        """
        Determine whether the given device payload contains price information or system identifiers.
        
        Parameters:
            payload (dict[str, Any]): The device payload mapping potentially containing PAYLOAD_PRICE and PAYLOAD_SYSTEM sections.
        
        Returns:
            bool: `true` if `FIELD_SINGLE_PRICE` or `FIELD_DYNAMIC_OR_SINGLE` exists in the price section, or if `FIELD_ID` or `FIELD_SYSTEM_ID` is present in the system section; `false` otherwise.
        """
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
        "default_power_set": lambda p: _has_props(
            p, FIELD_DEFAULT_PW, FIELD_MAX_OUT_PW
        ),
        "single_tariff_price_set": _has_price_or_system,
        "third_party_mqtt_port": lambda _p: True,
    }

    def _collect_entities() -> list[NumberEntity]:
        """Collect JackeryNumber entities for devices whose payloads satisfy their gating predicates.

        Iterates coordinator data and instantiates a JackeryNumber for each entry in NUMBER_DESCRIPTIONS when the description has no predicate or its predicate returns True for the device payload.

        Returns:
            list[NumberEntity]: Instantiated number entities ready to be added to Home Assistant.
        """
        entities: list[NumberEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            for description in NUMBER_DESCRIPTIONS:
                predicate = gating.get(description.key)
                if predicate is None or predicate(payload):
                    _append(entities, JackeryNumber(coordinator, dev_id, description))
        return entities

    last_signature: tuple[Any, ...] = ()

    def _add_new_entities() -> None:
        """Rebuilds and adds number entities when the coordinator's entity signature changes.

        Computes the coordinator's entity signature and, if it differs from the last-seen signature, collects new entity instances and calls the platform's async_add_entities callback to register them; otherwise performs no action.
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
