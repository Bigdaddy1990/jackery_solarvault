"""Number platform for Jackery SolarVault.

Description-driven entities; one generic class handles all sliders/boxes.
Specials (max-feed-grid dynamic max, default-power 0.0 fallback,
single-tariff dynamic currency, max-power error handling) live as
optional callables on the description.
"""

from dataclasses import dataclass
from inspect import isawaitable
import logging
from math import isfinite
from typing import TYPE_CHECKING, Any, NoReturn

from homeassistant.components.number import NumberEntity
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)

from .client import JackeryAuthError
from .const import DOMAIN, PAYLOAD_THIRD_PARTY_MQTT_CONFIG
from .descriptions import (
    NUMBER_DESCRIPTIONS,
    JackeryNumberDescription as _JackeryNumberDescription,
)
from .descriptions.number import (
    _max_feed_grid_allowed_values,
    _max_feed_grid_dynamic_max,
    _set_default_power,
    _set_max_feed_grid,
    _set_max_output_power,
    _set_single_price,
    _set_soc_charge,
    _set_soc_discharge,
    _single_tariff_dynamic_unit,
    _wire_int,
)
from .entity import (
    HTTP_DATA_SOURCES,
    LAYER5_DATA_SOURCES,
    JackeryEntity,
    payload_properties_for_sources,
)
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    is_portable_payload as _is_portable_payload,
    safe_float,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import JackeryConfigEntry
    from .coordinator import JackerySolarVaultCoordinator

from homeassistant.core import callback


def _rounded_int(value: float | str | None) -> int:
    """Parse and round a finite Home Assistant number value."""
    parsed = safe_float(value)
    if parsed is None or not isfinite(parsed):
        msg = "invalid number value"
        raise ServiceValidationError(msg)
    return round(parsed)


def _wire_float(value: float | str | None) -> float:
    """Parse a finite floating-point value for a coordinator setter."""
    parsed = safe_float(value)
    if parsed is None or not isfinite(parsed):
        msg = "invalid number value"
        raise ServiceValidationError(msg)
    return parsed


@dataclass(frozen=True, kw_only=True)
class JackeryNumberDescription(_JackeryNumberDescription):
    """Compatibility export retaining the historical safe integer transform."""

    value_transform: Callable[[float], Any] = _rounded_int


def _migrated_float_number_description(
    *,
    key: str,
    value_transform: Callable[[float], Any],
) -> _JackeryNumberDescription:
    """Return a canonical migrated float description after parser validation."""
    description = next(item for item in NUMBER_DESCRIPTIONS if item.key == key)
    if description.value_transform(1.25) != value_transform(1.25):
        msg = f"Migrated float number description contract changed: {key}"
        raise RuntimeError(msg)
    return description


MIGRATED_FLOAT_NUMBER_DESCRIPTIONS = (
    _migrated_float_number_description(
        key="single_tariff_price_set",
        value_transform=_wire_float,
    ),
)


__all__ = [
    "HTTP_DATA_SOURCES",
    "LAYER5_DATA_SOURCES",
    "_max_feed_grid_allowed_values",
    "_max_feed_grid_dynamic_max",
    "_rounded_int",
    "_set_default_power",
    "_set_max_feed_grid",
    "_set_max_output_power",
    "_set_single_price",
    "_set_soc_charge",
    "_set_soc_discharge",
    "_single_tariff_dynamic_unit",
    "_wire_float",
    "_wire_int",
]

# Limit concurrent control-write/update calls. This is a setter platform:
# writes go to the cloud and to MQTT. Serializing keeps the queue depth on
# the broker bounded and prevents reordering of `DevicePropertyChange`
# commands per HA dev guidance for write-heavy platforms.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Generic entity
# ---------------------------------------------------------------------------


class JackeryNumber(JackeryEntity, NumberEntity):
    """Generic description-driven number entity for Jackery."""

    entity_description: _JackeryNumberDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: _JackeryNumberDescription,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description
        self.device_registry_role = description.device_registry_role

    def _raise_action_error(
        self,
        translation_key: str,
        **placeholders: object,
    ) -> NoReturn:
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

    def _raise_validation_error(
        self,
        translation_key: str,
        **placeholders: object,
    ) -> NoReturn:
        """Raise a translatable validation error for user-provided input."""
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders={
                "entity": self.entity_description.key,
                "device_id": self._device_id,
                **{key: str(value) for key, value in placeholders.items()},
            },
        )

    @property
    def section(self) -> dict[str, Any]:
        """The number entity's writable source section."""
        return self._section()

    def _section(self) -> dict[str, Any]:
        """Read the configured payload section (properties/price/...)."""
        if self.entity_description.source_section == PAYLOAD_THIRD_PARTY_MQTT_CONFIG:
            section = self.coordinator.third_party_mqtt_config_plaintext(
                self._device_id
            )
            return section if isinstance(section, dict) else {}
        return self._payload_section_for_sources(
            self.entity_description.source_section,
            self.entity_description.data_sources,
        )

    @property
    def native_value(self) -> float | None:
        """The entity's current value - delegates to description value_fn."""
        return self.entity_description.value_fn(self)

    @property
    def native_max_value(self) -> float:
        """The highest value the user can write."""
        if self.entity_description.dynamic_max is not None:
            return self.entity_description.dynamic_max(
                self._payload_for_sources(self.entity_description.data_sources)
            )
        if self.entity_description.native_max_value is not None:
            return float(self.entity_description.native_max_value)
        return 0.0

    @property
    def native_unit_of_measurement(self) -> str | None:
        """The entity's unit of measurement.

        Use a dynamic unit computed from the current payload when available.

        Returns:
            The unit of measurement string, or None if no unit is configured.
        """
        if self.entity_description.dynamic_unit is not None:
            return self.entity_description.dynamic_unit(
                self._payload_for_sources(self.entity_description.data_sources)
            )
        # pyrefly: ignore [no-any-return-implicit]
        return self.entity_description.native_unit_of_measurement

    @property
    def suggested_display_precision(self) -> int | None:
        """The suggested number of decimal places for display."""
        return self.entity_description.display_precision

    def _allowed_values(self) -> tuple[float, ...]:
        """Get the discrete native values allowed for this number entity.

        If the description's `allowed_values` is None, returns an empty tuple. If it is
        a callable, returns the tuple produced by calling it with the current
        coordinator payload; otherwise returns the configured tuple directly.

        Returns:
            tuple[float, ...]: Allowed native float values, or an empty tuple when no
            discrete constraint is defined.
        """
        allowed = self.entity_description.allowed_values
        if allowed is None:
            return ()
        if callable(allowed):
            # `callable()` should narrow the tuple-member out of the union, but
            # ty's narrowing of `callable()` over a union that includes a tuple
            # type falls back to `Top[(...) -> object]`; mypy narrows correctly.
            return tuple(
                allowed(  # ty: ignore[call-top-callable, invalid-argument-type]
                    self._payload_for_sources(self.entity_description.data_sources)
                )
            )
        return tuple(allowed)

    async def async_set_native_value(self, value: float) -> None:
        """Write a validated native numeric value to the device.

        Enforce description-driven validation and invoke the configured setter.

        Validates the value against the description's min/max when `validate_range` is
        True and against discrete `allowed_values` when present. If a setter is
        configured, the native value is transformed with the description's
        `value_transform` and passed to the setter. Setter authentication failures are
        converted to `ConfigEntryAuthFailed`. If a `HomeAssistantError` raised by the
        setter already contains a `translation_key` it is re-raised; otherwise, the
        error is raised as a translated action error. A coordinator refresh is always
        requested after the write attempt.

        Parameters:
            value (float): The native numeric value to write.

        Raises:
            ConfigEntryAuthFailed: If the setter reports an authentication failure.
            ServiceValidationError: For invalid range or allowed-value violations.
            HomeAssistantError: If the setter fails.
        """
        parsed_value = safe_float(value)
        if (
            parsed_value is None
            or not isfinite(parsed_value)
            or parsed_value < self.native_min_value
            or parsed_value > self.native_max_value
        ):
            self._raise_validation_error(
                "invalid_number_range",
                min=f"{self.native_min_value:.0f}",
                max=f"{self.native_max_value:.0f}",
            )
        value = parsed_value

        # Discrete allowed values validation
        allowed = self._allowed_values()
        if allowed and _rounded_int(value) not in {
            _rounded_int(candidate) for candidate in allowed
        }:
            self._raise_validation_error(
                "invalid_number_allowed_values",
                allowed=", ".join(str(_rounded_int(v)) for v in allowed),
            )

        if self.entity_description.setter is None:
            self._raise_action_error(
                "entity_action_failed",
                error="entity is not writable",
            )

        transformed_value = self.entity_description.value_transform(value)

        try:
            await self.entity_description.setter(
                self.coordinator, self._device_id, transformed_value
            )
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(
                "setter_failed",
                error=str(err),
            )
        except Exception as err:  # ruff: ignore[blind-except]
            self._raise_action_error(
                "setter_failed",
                error=str(err),
            )
        finally:
            refresh = self.coordinator.async_request_refresh()
            if isawaitable(refresh):
                await refresh


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
# Home Assistant invokes platform setup as an awaitable callback.
async def async_setup_entry(  # ruff:ignore[unused-async]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Jackery number entities from a config entry."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[NumberEntity], entity: NumberEntity) -> None:
        append_unique_entity(entities, seen_unique_ids, entity)

    def _collect_entities() -> list[NumberEntity]:
        """Collect and instantiate all number entities for each device payload."""
        entities: list[NumberEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload_properties_for_sources(payload)
            is_portable = _is_portable_payload(payload, props)
            for description in NUMBER_DESCRIPTIONS:
                if description.key == "third_party_mqtt_port" and not payload.get(
                    PAYLOAD_THIRD_PARTY_MQTT_CONFIG
                ):
                    continue
                if description.key.startswith("portable_") != is_portable:
                    continue
                if description.key == "single_tariff_price_set" and is_portable:
                    continue

                _append_unique(
                    entities, JackeryNumber(coordinator, dev_id, description)
                )
        return entities

    last_signature: tuple[Any, ...] = ()

    @callback
    def _add_new_entities() -> None:
        """Add number entities discovered after an entity-signature change."""
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
