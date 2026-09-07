"""Switch descriptions for Jackery SolarVault integration."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntityDescription
from homeassistant.const import EntityCategory

from ..const import (
    ACTION_ID_PORTABLE_DISCHARGE_MEMORY,
    ACTION_ID_PORTABLE_LIGHT,
    ACTION_ID_PORTABLE_OUTPUT_AC,
    ACTION_ID_PORTABLE_OUTPUT_AC240,
    ACTION_ID_PORTABLE_OUTPUT_DC,
    ACTION_ID_PORTABLE_OUTPUT_DC_CAR,
    ACTION_ID_PORTABLE_OUTPUT_DC_USB,
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SWITCH,
    ACTION_ID_PORTABLE_SUPER_CHARGE,
    DEFAULT_NULL_SEMANTICS,
    FIELD_AUTO_STANDBY,
    FIELD_FOLLOW_METER,
    FIELD_IS_AUTO_STANDBY,
    FIELD_IS_FOLLOW_METER_PW,
    FIELD_OFF_GRID_DOWN,
    FIELD_SW_EPS,
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_WPS,
    PAYLOAD_PROPERTIES,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
    PAYLOAD_WEATHER_PLAN,
)
from ..entity import (
    HTTP_DATA_SOURCES,
    LAYER5_COMMAND_SOURCES,
    LAYER5_DATA_SOURCES,
    payload_properties_for_sources,
    property_data_sources,
)
from ..util import first_nonblank_int, safe_bool, task_plan_value

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..coordinator import JackerySolarVaultCoordinator
    from ..switch import JackerySwitch as JackerySwitchEntity


def _standby_is_on(raw: bool | float | str | None) -> bool | None:
    """Convert a raw autoStandby payload value into an on/off state."""
    if raw is None:
        return None
    parsed = first_nonblank_int(raw)
    if parsed is None:
        return safe_bool(raw)
    return parsed == 1


def _default_switch_value(entity: JackerySwitchEntity) -> bool | None:
    """Evaluate the historical section/fallback switch-state contract."""
    description = entity.entity_description
    payload = entity._payload  # ruff:ignore[private-member-access]  # legacy section fallback contract needs the complete device payload

    def _section(name: str) -> dict[str, Any]:
        if name == PAYLOAD_PROPERTIES:
            return payload_properties_for_sources(payload, description.data_sources)
        value = payload.get(name) or {}
        return value if isinstance(value, dict) else {}

    raw = None
    for key in description.source_keys:
        raw = _section(description.source_section).get(key)
        if raw is not None:
            break
    if raw is None and description.fallback_section:
        fallback = _section(description.fallback_section)
        for key in description.source_keys:
            raw = fallback.get(key)
            if raw is not None:
                break
    if raw is None and description.use_task_plan_fallback:
        task_plan = payload.get(PAYLOAD_TASK_PLAN) or {}
        if isinstance(task_plan, dict):
            for key in description.source_keys:
                raw = task_plan_value(task_plan, key)
                if raw is not None:
                    break
    return description.is_on_transform(raw)


@dataclass(frozen=True, kw_only=True)
class JackerySwitchDescription(SwitchEntityDescription):
    """Describes a Jackery switch with current and legacy call surfaces."""

    _attr_has_entity_name = True

    value_fn: Callable[[JackerySwitchEntity], bool | None] = _default_switch_value
    setter_fn: (
        Callable[[JackerySolarVaultCoordinator, str, bool], Awaitable[None]] | None
    ) = None
    is_on_fn: Callable[[JackerySwitchEntity], bool | None] | None = None
    source_keys: tuple[str, ...] = ()
    source_section: str = PAYLOAD_PROPERTIES
    fallback_section: str | None = None
    use_task_plan_fallback: bool = False
    setter: (
        Callable[[JackerySolarVaultCoordinator, str, bool], Awaitable[None]] | None
    ) = None
    is_on_transform: Callable[[Any], bool | None] = safe_bool
    smali_field: str | None = None
    app_fields: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    command_sources: tuple[str, ...] = ()
    device_registry_role: str = "head"
    null_semantics: str = DEFAULT_NULL_SEMANTICS
    recorder_allowed: bool = True
    ha_derived: bool = False

    def __post_init__(self) -> None:
        """Resolve compatibility aliases and source/command capabilities."""
        setter = self.setter_fn or self.setter
        object.__setattr__(self, "setter_fn", setter)
        object.__setattr__(self, "setter", setter)
        app_fields = self.app_fields or self.source_keys
        if not app_fields and self.smali_field:
            app_fields = (self.smali_field,)
        object.__setattr__(self, "app_fields", app_fields)
        if not self.data_sources:
            if self.source_section == PAYLOAD_PROPERTIES:
                sources = property_data_sources(
                    *app_fields,
                    layer5_proven=setter is not None,
                )
            elif self.source_section == PAYLOAD_THIRD_PARTY_MQTT_CONFIG:
                sources = LAYER5_DATA_SOURCES
            else:
                sources = HTTP_DATA_SOURCES
            object.__setattr__(self, "data_sources", sources)
        if not self.command_sources and setter is not None:
            object.__setattr__(self, "command_sources", LAYER5_COMMAND_SOURCES)


# ---------------------------------------------------------------------------
# Setter helpers (migrated from switch.py)
# ---------------------------------------------------------------------------


async def _set_eps(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle the EPS output on a device."""
    await coord.async_set_eps(dev_id, value)


async def _set_auto_standby(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle auto-standby on a device."""
    await coord.async_set_auto_standby(dev_id, value)


async def _set_standby(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle manual standby on a device."""
    await coord.async_set_standby(dev_id, value)


async def _set_follow_meter(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle smart-meter following on a device."""
    await coord.async_set_follow_meter(dev_id, value)


async def _set_off_grid_shutdown(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle off-grid shutdown on a device."""
    await coord.async_set_off_grid_shutdown(dev_id, value)


async def _set_storm_warning(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Set the storm warning enabled state for the device identified by dev_id."""
    await coord.async_set_storm_warning(dev_id, value)


async def _set_third_party_mqtt_enabled(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle the app third-party MQTT bridge using the current config fields."""
    await coord.async_update_third_party_mqtt_config(
        dev_id,
        {FIELD_THIRD_PARTY_MQTT_ENABLE: 1 if value else 0},
    )


# --- Portable / Explorer powerstation switch setters -------------------------


async def _set_portable_dc_output(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle the DC output on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id,
        action_id=ACTION_ID_PORTABLE_OUTPUT_DC,
        field="odc",
        enabled=value,
    )


async def _set_portable_dc_usb_output(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle the USB output on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id,
        action_id=ACTION_ID_PORTABLE_OUTPUT_DC_USB,
        field="odcu",
        enabled=value,
    )


async def _set_portable_dc_car_output(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle the car (DC cigarette) output on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id,
        action_id=ACTION_ID_PORTABLE_OUTPUT_DC_CAR,
        field="odcc",
        enabled=value,
    )


async def _set_portable_ac_output(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle the AC output on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id,
        action_id=ACTION_ID_PORTABLE_OUTPUT_AC,
        field="oac",
        enabled=value,
    )


async def _set_portable_ac240_output(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle the AC240 output on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id,
        action_id=ACTION_ID_PORTABLE_OUTPUT_AC240,
        field="oac2",
        enabled=value,
    )


async def _set_portable_light(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle the LED light on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id,
        action_id=ACTION_ID_PORTABLE_LIGHT,
        field="lm",
        enabled=value,
    )


async def _set_portable_super_charge(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Enable/disable super charge mode on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id,
        action_id=ACTION_ID_PORTABLE_SUPER_CHARGE,
        field="sfc",
        enabled=value,
    )


async def _set_portable_output_priority_switch(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle the output priority switch on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id,
        action_id=ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SWITCH,
        field="outPrio",
        enabled=value,
    )


async def _set_portable_discharge_memory(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Enable/disable discharge memory on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id,
        action_id=ACTION_ID_PORTABLE_DISCHARGE_MEMORY,
        field="dhg_recall",
        enabled=value,
    )


# ---------------------------------------------------------------------------
# Switch description tuples (migrated from switch.py)
# ---------------------------------------------------------------------------


SWITCH_DESCRIPTIONS: tuple[JackerySwitchDescription, ...] = (
    JackerySwitchDescription(
        key="eps_output",
        translation_key="eps_output",
        entity_category=EntityCategory.CONFIG,
        source_keys=(FIELD_SW_EPS,),
        setter_fn=_set_eps,
    ),
    JackerySwitchDescription(
        key="auto_standby_set",
        translation_key="auto_standby_set",
        entity_category=EntityCategory.CONFIG,
        source_keys=(FIELD_IS_AUTO_STANDBY,),
        use_task_plan_fallback=True,
        setter_fn=_set_auto_standby,
    ),
    JackerySwitchDescription(
        key="standby",
        translation_key="standby",
        entity_category=EntityCategory.CONFIG,
        source_keys=(FIELD_AUTO_STANDBY,),
        is_on_transform=_standby_is_on,
        setter_fn=_set_standby,
    ),
    JackerySwitchDescription(
        key="follow_meter",
        translation_key="follow_meter",
        entity_category=EntityCategory.CONFIG,
        source_keys=(FIELD_IS_FOLLOW_METER_PW, FIELD_FOLLOW_METER),
        use_task_plan_fallback=True,
        setter_fn=_set_follow_meter,
    ),
    JackerySwitchDescription(
        key="off_grid_shutdown",
        translation_key="off_grid_shutdown",
        entity_category=EntityCategory.CONFIG,
        source_keys=(FIELD_OFF_GRID_DOWN,),
        use_task_plan_fallback=True,
        setter_fn=_set_off_grid_shutdown,
    ),
    JackerySwitchDescription(
        key="storm_warning",
        translation_key="storm_warning",
        entity_category=EntityCategory.CONFIG,
        device_registry_role="system",
        source_keys=(FIELD_WPS,),
        fallback_section=PAYLOAD_WEATHER_PLAN,
        use_task_plan_fallback=True,
        setter_fn=_set_storm_warning,
    ),
    JackerySwitchDescription(
        key="third_party_mqtt_enable",
        translation_key="third_party_mqtt_enable",
        entity_category=EntityCategory.CONFIG,
        source_keys=(FIELD_THIRD_PARTY_MQTT_ENABLE,),
        source_section=PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
        setter_fn=_set_third_party_mqtt_enabled,
    ),
    # --- Portable / Explorer powerstation switches ---
    JackerySwitchDescription(
        key="portable_dc_output",
        translation_key="portable_dc_output",
        entity_category=EntityCategory.CONFIG,
        source_keys=("odc",),
        setter_fn=_set_portable_dc_output,
    ),
    JackerySwitchDescription(
        key="portable_usb_output",
        translation_key="portable_usb_output",
        entity_category=EntityCategory.CONFIG,
        source_keys=("odcu",),
        setter_fn=_set_portable_dc_usb_output,
    ),
    JackerySwitchDescription(
        key="portable_car_output",
        translation_key="portable_car_output",
        entity_category=EntityCategory.CONFIG,
        source_keys=("odcc",),
        setter_fn=_set_portable_dc_car_output,
    ),
    JackerySwitchDescription(
        key="portable_ac_output",
        translation_key="portable_ac_output",
        entity_category=EntityCategory.CONFIG,
        source_keys=("oac",),
        setter_fn=_set_portable_ac_output,
    ),
    JackerySwitchDescription(
        key="portable_ac240_output",
        translation_key="portable_ac240_output",
        entity_category=EntityCategory.CONFIG,
        source_keys=("oac2",),
        setter_fn=_set_portable_ac240_output,
    ),
    JackerySwitchDescription(
        key="portable_light",
        translation_key="portable_light",
        entity_category=EntityCategory.CONFIG,
        source_keys=("lm",),
        setter_fn=_set_portable_light,
    ),
    JackerySwitchDescription(
        key="portable_super_charge",
        translation_key="portable_super_charge",
        entity_category=EntityCategory.CONFIG,
        source_keys=("sfc",),
        setter_fn=_set_portable_super_charge,
    ),
    JackerySwitchDescription(
        key="portable_output_priority_switch",
        translation_key="portable_output_priority_switch",
        entity_category=EntityCategory.CONFIG,
        source_keys=("outPrio",),
        setter_fn=_set_portable_output_priority_switch,
    ),
    JackerySwitchDescription(
        key="portable_discharge_memory",
        translation_key="portable_discharge_memory",
        entity_category=EntityCategory.CONFIG,
        source_keys=("dhg_recall",),
        setter_fn=_set_portable_discharge_memory,
    ),
)
