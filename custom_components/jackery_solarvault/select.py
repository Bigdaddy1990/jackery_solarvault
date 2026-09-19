"""Select platform for Jackery SolarVault preset-style controls.

Description-driven entities using central descriptions package with HA-standard
value_fn delegation. Inline helpers removed; all current/select logic lives in
descriptions/select.py.
"""

from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .client import JackeryAuthError
from .const import (
    DOMAIN,
    FIELD_MINS_INTERVAL,
    FIELD_OFF_GRID_DOWN,
    FIELD_OFF_GRID_TIME,
    FIELD_TEMP_UNIT,
    FIELD_WORK_MODEL,
    FIELD_WPC,
    PAYLOAD_CT_METER,
    PAYLOAD_PRICE,
    PAYLOAD_PRICE_SOURCES,
    PAYLOAD_WEATHER_PLAN,
)
from .coordinator import (
    ACTION_WRITE_ERRORS,
    normalized_company_id,
    normalized_source_regions,
)
from .descriptions import SELECT_DESCRIPTIONS, JackerySelectDescription
from .descriptions.select import (
    _CT_PHASE_TO_OPTION,
    _HOURS_TO_AUTO_OFF_OPTION,
    _OPTION_TO_CT_PHASE,
    _ct_phase_current,
    _island_auto_off_current,
    _portable_ac1_priority_current,
    _portable_ac2_priority_current,
    _portable_ac_output_mode_current,
    _portable_battery_mode_current,
    _portable_charge_mode_current,
    _portable_dc_priority_current,
    _portable_output_priority_current,
    _portable_power_mode_current,
    _portable_screen_current,
    _portable_ups_model_current,
    _price_mode_current,
    _price_mode_dynamic_available,
    _price_provider_current,
    _price_provider_options,
    _price_source_label,
    _price_source_matches_current,
    _price_source_regions,
    _storm_minutes_current,
    _storm_minutes_current_value,
    _storm_minutes_fallback,
    _storm_minutes_label,
    _storm_minutes_options,
    _storm_minutes_value,
    _temp_unit_current,
    _work_mode_current,
)
from .entity import JackeryEntity, payload_properties_for_sources
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    is_portable_payload as _is_portable_payload,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import JackeryConfigEntry
    from .coordinator import JackerySolarVaultCoordinator

__all__ = [
    "_CT_PHASE_TO_OPTION",
    "_HOURS_TO_AUTO_OFF_OPTION",
    "_OPTION_TO_CT_PHASE",
    "JackerySelectDescription",
    "_ct_phase_current",
    "_island_auto_off_current",
    "_portable_ac1_priority_current",
    "_portable_ac2_priority_current",
    "_portable_ac_output_mode_current",
    "_portable_battery_mode_current",
    "_portable_charge_mode_current",
    "_portable_dc_priority_current",
    "_portable_output_priority_current",
    "_portable_power_mode_current",
    "_portable_screen_current",
    "_portable_ups_model_current",
    "_price_mode_current",
    "_price_mode_dynamic_available",
    "_price_provider_current",
    "_price_provider_options",
    "_price_source_label",
    "_price_source_matches_current",
    "_price_source_regions",
    "_storm_minutes_current",
    "_storm_minutes_current_value",
    "_storm_minutes_fallback",
    "_storm_minutes_label",
    "_storm_minutes_options",
    "_storm_minutes_value",
    "_temp_unit_current",
    "_work_mode_current",
]

# Limit concurrent control-write/update calls. This is a setter platform:
# writes go to the cloud and to MQTT. Serializing keeps the queue depth on
# the broker bounded and prevents reordering of `DevicePropertyChange`
# commands per HA dev guidance for write-heavy platforms.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


def _raise_select_action_error(
    entity: JackerySelect,
    translation_key: str,
    **placeholders: object,
) -> None:
    """Raise a translatable HA action error for a select entity."""
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders={
            "entity": entity.entity_description.key,
            "device_id": entity._device_id,  # ruff: ignore[private-member-access]
            **{key: str(value) for key, value in placeholders.items()},
        },
    )


def _price_sources_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    raw = payload.get(PAYLOAD_PRICE_SOURCES)
    if not isinstance(raw, list):
        return []
    out: list[dict[str, object]] = [
        item
        for item in raw
        if isinstance(item, dict)
        and normalized_company_id(item.get("platformCompanyId")) is not None
        and normalized_source_regions(item)
    ]
    return out


@dataclass
class _SelectState:
    """Mutable per-instance state. Kept off the description, which is frozen."""

    warned_unknown_values: set[Any] = field(default_factory=set)


class JackerySelect(JackeryEntity, SelectEntity):
    """Generic description-driven Jackery select."""

    entity_description: JackerySelectDescription
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: JackerySelectDescription,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description
        self.device_registry_role = description.device_registry_role
        self._state = _SelectState()

    def _warn_unknown_once(self, value: object) -> None:
        """Log an unmapped raw value once per instance / value combination."""
        kind = self.entity_description.warn_unknown_kind
        if kind is None or value in self._state.warned_unknown_values:
            return
        self._state.warned_unknown_values.add(value)
        _LOGGER.warning(
            "Jackery %s value %s is not mapped to a translated option; "
            "reporting as unknown",
            kind,
            value,
        )

    @property
    def options(self) -> list[str]:
        """The list of available options."""
        description = self.entity_description
        if description.options_fn is not None:
            return description.options_fn(self)
        return list(description.options or ())

    @property
    def current_option(self) -> str | None:
        """The currently selected option - delegates to description value_fn."""
        return self.entity_description.value_fn(self)

    async def async_select_option(self, option: str) -> None:
        """Forward the chosen option to the coordinator."""
        select_fn = self.entity_description.select_fn
        if select_fn is None:
            _raise_select_action_error(
                self,
                "entity_action_failed",
                error="entity is not writable",
            )
            return
        try:
            await select_fn(self, option)
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            _raise_select_action_error(self, "entity_action_failed", error=err)
        except ACTION_WRITE_ERRORS as err:
            _raise_select_action_error(self, "entity_action_failed", error=err)


# Home Assistant invokes platform setup as an awaitable callback.
async def async_setup_entry(  # ruff:ignore[unused-async]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create and register description-driven select entities."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[SelectEntity], entity: SelectEntity) -> None:
        append_unique_entity(entities, seen_unique_ids, entity)

    # Gating predicates per description key. Each predicate returns True when
    # the device is known to expose / accept the corresponding selector.
    def _gate(key: str, payload: dict[str, Any], supports_advanced: bool) -> bool:
        """Determine whether a keyed select entity applies to a device."""
        props = payload_properties_for_sources(payload)
        weather_plan = payload.get(PAYLOAD_WEATHER_PLAN) or {}
        if key == "work_mode_select":
            applies = supports_advanced or FIELD_WORK_MODEL in props
        elif key == "temp_unit_select":
            applies = supports_advanced or FIELD_TEMP_UNIT in props
        elif key == "auto_off_island_mode":
            applies = (
                supports_advanced
                or FIELD_OFF_GRID_TIME in props
                or FIELD_OFF_GRID_DOWN in props
            )
        elif key == "storm_warning_minutes_select":
            applies = (
                supports_advanced
                or FIELD_WPC in props
                or FIELD_MINS_INTERVAL in props
                or FIELD_WPC in weather_plan
                or FIELD_MINS_INTERVAL in weather_plan
            )
        elif key == "electricity_price_mode":
            applies = True
        elif key == "electricity_price_provider":
            current_company = (payload.get(PAYLOAD_PRICE) or {}).get(
                "platformCompanyId",
            )
            applies = bool(
                _price_sources_from_payload(payload)
            ) or current_company not in {None, ""}
        elif key == "ct_phase_select":
            applies = isinstance(payload.get(PAYLOAD_CT_METER), dict)
        else:
            applies = False
        return applies

    def _collect_entities() -> list[SelectEntity]:
        """Collect select entities for devices that meet the gating rules."""
        entities: list[SelectEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload_properties_for_sources(payload)
            is_portable = _is_portable_payload(payload, props)
            supports_advanced = coordinator.device_supports_advanced(dev_id)
            for description in SELECT_DESCRIPTIONS:
                description_is_portable = description.key.startswith("portable_")
                if description_is_portable != is_portable:
                    continue
                if description_is_portable or _gate(
                    description.key,
                    payload,
                    supports_advanced,
                ):
                    _append_unique(
                        entities,
                        JackerySelect(coordinator, dev_id, description),
                    )
        return entities

    last_signature: tuple[Any, ...] = ()

    @callback
    def _add_new_entities() -> None:
        """Register selects discovered after a payload-signature change."""
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
