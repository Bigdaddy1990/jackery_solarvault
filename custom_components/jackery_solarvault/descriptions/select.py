"""Select descriptions for Jackery SolarVault integration."""

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, NoReturn

from homeassistant.components.select import SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..select import JackerySelect

from ..const import (
    ACTION_ID_PORTABLE_AC_OUTPUT_MODE,
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY,
    ACTION_ID_PORTABLE_SCREEN,
    ACTION_ID_PORTABLE_SETTING_BATTERY,
    ACTION_ID_PORTABLE_SETTING_CHARGE,
    ACTION_ID_PORTABLE_UPS_MODEL,
    ACTION_ID_PORTABLE_USE_POWER_MODE,
    AUTO_OFF_HOURS,
    DEFAULT_NULL_SEMANTICS,
    DEFAULT_STORM_WARNING_MINUTES,
    DOMAIN,
    FIELD_CID,
    FIELD_COMPANY_NAME,
    FIELD_COUNTRY,
    FIELD_DEVICE_SN,
    FIELD_DEV_SN,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_MINS_INTERVAL,
    FIELD_NAME,
    FIELD_OFF_GRID_AUTO_OFF_TIME,
    FIELD_OFF_GRID_DOWN_TIME,
    FIELD_OFF_GRID_TIME,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_PM,
    FIELD_PRICE_MODE,
    FIELD_SCHE_PHASE,
    FIELD_SINGLE_PRICE,
    FIELD_SLTB,
    FIELD_STORM,
    FIELD_SYSTEM_REGION,
    FIELD_TEMP_UNIT,
    FIELD_UPS,
    FIELD_WORK_MODEL,
    FIELD_WPC,
    FIELD_WPS,
    PAYLOAD_CT_METER,
    PAYLOAD_PRICE_SOURCES,
    PRICE_MODE_TO_OPTION,
    STORM_MINUTES_DEFAULT,
    STORM_MINUTES_MIN_VALID,
    TEMP_UNIT_TO_OPTION,
    WORK_MODE_READ_ALIASES,
    WORK_MODE_TO_OPTION,
)
from ..coordinator import (
    first_nonblank_source_name,
    normalized_company_id,
    normalized_region,
    normalized_source_regions,
)
from ..entity import HTTP_COMMAND_SOURCES, HTTP_DATA_SOURCES, LAYER5_COMMAND_SOURCES
from ..util import safe_int, task_plan_value


@dataclass(frozen=True, kw_only=True)
class JackerySelectDescription(SelectEntityDescription):
    """Describes a Jackery select entity."""

    value_fn: Callable[[JackerySelect], str | None]
    options: list[str] | None = None
    options_fn: Callable[[JackerySelect], list[str]] | None = None
    current_fn: Callable[[JackerySelect], str | None] | None = None
    select_fn: Callable[[JackerySelect, str], Awaitable[None]] | None = None
    warn_unknown_kind: str | None = None
    smali_field: str | None = None
    app_fields: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    command_sources: tuple[str, ...] = ()
    device_registry_role: str = "head"
    null_semantics: str = DEFAULT_NULL_SEMANTICS
    recorder_allowed: bool = True
    ha_derived: bool = False

    def __post_init__(self) -> None:
        """Resolve the explicit read and command capability registry."""
        app_fields = self.app_fields or ()
        if not app_fields and self.smali_field:
            app_fields = (self.smali_field,)
        object.__setattr__(self, "app_fields", app_fields)
        if not self.data_sources:
            object.__setattr__(self, "data_sources", HTTP_DATA_SOURCES)
        if not self.command_sources:
            command_sources: tuple[str, ...] = (
                LAYER5_COMMAND_SOURCES if self.select_fn else HTTP_COMMAND_SOURCES
            )
            object.__setattr__(self, "command_sources", command_sources)


# ---------------------------------------------------------------------------
# Option maps and select handlers (migrated from select.py)
# ---------------------------------------------------------------------------


_OPTION_TO_WORK_MODE = {v: k for k, v in WORK_MODE_TO_OPTION.items()}

_OPTION_TO_TEMP_UNIT = {v: k for k, v in TEMP_UNIT_TO_OPTION.items()}

_AUTO_OFF_OPTIONS = [f"h_{hours}" for hours in AUTO_OFF_HOURS]

_HOURS_TO_AUTO_OFF_OPTION = {hours: f"h_{hours}" for hours in AUTO_OFF_HOURS}

_AUTO_OFF_OPTION_TO_HOURS = {f"h_{hours}": hours for hours in AUTO_OFF_HOURS}

_OPTION_TO_PRICE_MODE = {v: k for k, v in PRICE_MODE_TO_OPTION.items()}
_PRICE_MODE_DYNAMIC = 1
_PRICE_MODE_SINGLE = 2
_WORK_MODE_DYNAMIC_PRICE = 7
_STORM_MINUTE_KEYS = (FIELD_WPC, FIELD_MINS_INTERVAL)

_CT_PHASE_TO_OPTION = {
    1: "phase_1",
    2: "phase_2",
    3: "phase_3",
    # App schePhase=4 is not a fourth conductor; it means combined phases.
    4: "combined_phases",
}

_OPTION_TO_CT_PHASE = {value: key for key, value in _CT_PHASE_TO_OPTION.items()}
_OPTION_TO_CT_PHASE.update({
    "phase_4": 4,
    "phase_a": 1,
    "phase_b": 2,
    "phase_c": 3,
    "combined_phase": 4,
    "phase_t": 4,
})


def _raise_select_action_error(
    entity: JackerySelect,
    translation_key: str,
    **placeholders: object,
) -> NoReturn:
    """Raise a translatable HA action error for a select entity."""
    error_type = (
        ServiceValidationError
        if translation_key == "invalid_select_option"
        else HomeAssistantError
    )
    raise error_type(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders={
            "entity": entity.entity_description.key,
            "device_id": entity._device_id,  # ruff:ignore[private-member-access]
            **{key: str(value) for key, value in placeholders.items()},
        },
    )


def _first_storm_minute_value(section: dict[str, object]) -> object | None:
    """Return the first present storm-minute field from one payload section."""
    for key in _STORM_MINUTE_KEYS:
        value = section.get(key)
        if value is not None:
            return value
    return None


def _storm_list_minute_value(weather_plan: dict[str, object]) -> object | None:
    """Return the first storm-minute field from a weather-plan alert list."""
    storm = weather_plan.get(FIELD_STORM)
    if not isinstance(storm, list):
        return None
    for item in storm:
        if (
            isinstance(item, dict)
            and (value := _first_storm_minute_value(item)) is not None
        ):
            return value
    return None


def _storm_minutes_value(
    properties: dict[str, object],
    weather_plan: dict[str, object],
    task_plan: dict[str, object],
) -> int | None:
    """Extract the storm-warning lead time in minutes from device payload sections.

    Searches for `FIELD_WPC` or `FIELD_MINS_INTERVAL` in `properties`, then
    `weather_plan`, then `task_plan`, and finally scans list entries in
    `weather_plan[FIELD_STORM]` (each entry must be a dict). Converts the first found
    raw value to an integer and returns it only when the parsed value is greater than
    or equal to `STORM_MINUTES_MIN_VALID`; otherwise returns `None`.

    Parameters:
        properties (dict[str, object]): The device `properties` payload section to
        inspect.
        weather_plan (dict[str, object]): The device `weather_plan` payload section to
        inspect.
        task_plan (dict[str, object]): The device `task_plan` payload section to
        inspect.

    Returns:
        int | None: The storm lead time in minutes when a valid value is found, or
        `None` if no valid value is present.
    """
    raw = _first_storm_minute_value(properties)
    if raw is None:
        raw = _first_storm_minute_value(weather_plan)
    if raw is None:
        raw = task_plan_value(task_plan, FIELD_WPC, FIELD_MINS_INTERVAL)
    if raw is None:
        raw = _storm_list_minute_value(weather_plan)
    value = safe_int(raw)
    # ``wpc``/``minsInterval`` below STORM_MINUTES_MIN_VALID are firmware
    # sentinels for "not set" — drop them so the select does not invent an
    # untranslated ``min_<value>`` option (e.g. ``min_1``).
    return value if value is not None and value >= STORM_MINUTES_MIN_VALID else None


def _storm_minutes_fallback(
    properties: dict[str, object],
    weather_plan: dict[str, object],
    task_plan: dict[str, object],
) -> int | None:
    """Selector metadata.

    Return `DEFAULT_STORM_WARNING_MINUTES` when a storm-enabled indicator exists but
    no explicit lead time.

    Checks for a storm-enabled marker (`FIELD_WPS`) in `properties`, then
    `weather_plan`, then `task_plan`; if the marker is present and parses to an integer,
    returns `DEFAULT_STORM_WARNING_MINUTES`. If `weather_plan[FIELD_STORM]` is a list,
    also returns `DEFAULT_STORM_WARNING_MINUTES`. Otherwise returns `None`.

    Returns:
        int | None: `DEFAULT_STORM_WARNING_MINUTES` when a fallback is appropriate,
        `None` otherwise.
    """
    raw = properties.get(FIELD_WPS)
    if raw is None:
        raw = weather_plan.get(FIELD_WPS)
    if raw is None:
        raw = task_plan_value(task_plan, FIELD_WPS)
    if raw is not None:
        if safe_int(raw) is None:
            return None
        return DEFAULT_STORM_WARNING_MINUTES
    storm = weather_plan.get(FIELD_STORM)
    if isinstance(storm, list):
        return DEFAULT_STORM_WARNING_MINUTES
    return None


def _storm_minutes_label(minutes: int) -> str:
    """Return the technical option key for a minute value.

    Translation state keys "min_<value>" are valid HA identifiers and let
    each value in STORM_MINUTES_DEFAULT have its own localized label.
    """
    return f"min_{minutes}"


def _price_source_label(source: dict[str, object]) -> str:
    company_id = normalized_company_id(source.get(FIELD_PLATFORM_COMPANY_ID))
    name = (
        first_nonblank_source_name(
            source,
            FIELD_COMPANY_NAME,
            FIELD_NAME,
            FIELD_CID,
            FIELD_PLATFORM_COMPANY_ID,
        )
        or "Provider"
    )
    country = ", ".join(normalized_source_regions(source))
    label = f"{name} ({country})" if country else name
    if company_id is not None:
        return f"{label} #{company_id}"
    return label


def _price_source_regions(source: dict[str, object]) -> list[str]:
    return normalized_source_regions(source)


def _price_source_matches_current(
    source: dict[str, object],
    company_id: object,
    region: object,
) -> bool:
    if normalized_company_id(source.get(FIELD_PLATFORM_COMPANY_ID)) != (
        normalized_company_id(company_id)
    ):
        return False
    normalized_current_region = normalized_region(region)
    if normalized_current_region is None:
        return True
    return normalized_current_region in _price_source_regions(source)


def _price_sources_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    raw = payload.get(PAYLOAD_PRICE_SOURCES)
    if not isinstance(raw, list):
        return []
    out: list[dict[str, object]] = [
        item
        for item in raw
        if isinstance(item, dict)
        and normalized_company_id(item.get(FIELD_PLATFORM_COMPANY_ID)) is not None
        and _price_source_regions(item)
    ]
    return out


def _price_mode_dynamic_available(entity: JackerySelect) -> bool:
    company_id = entity._price.get(FIELD_PLATFORM_COMPANY_ID)  # ruff:ignore[private-member-access]
    region = entity._price.get(FIELD_SYSTEM_REGION)  # ruff:ignore[private-member-access]
    if normalized_company_id(company_id) is not None and normalized_region(region):
        return True
    return bool(_price_sources_from_payload(entity._payload))  # ruff:ignore[private-member-access]


def _price_mode_current_int(entity: JackerySelect) -> int | None:
    raw = entity._price.get(FIELD_DYNAMIC_OR_SINGLE)  # ruff:ignore[private-member-access]
    if raw is None:
        raw = task_plan_value(
            entity._task_plan,  # ruff:ignore[private-member-access]
            FIELD_DYNAMIC_OR_SINGLE,
            FIELD_PRICE_MODE,
        )
    if raw is None:
        work_mode = safe_int(entity._properties.get(FIELD_WORK_MODEL))  # ruff:ignore[private-member-access]
        if work_mode == _WORK_MODE_DYNAMIC_PRICE:
            return _PRICE_MODE_DYNAMIC
        if entity._price.get(FIELD_SINGLE_PRICE) is not None:  # ruff:ignore[private-member-access]
            return _PRICE_MODE_SINGLE
        return None
    return safe_int(raw)


def _work_mode_current(entity: JackerySelect) -> str | None:
    raw = entity._properties.get(FIELD_WORK_MODEL)  # ruff:ignore[private-member-access]
    if raw is None:
        raw = task_plan_value(entity._task_plan, FIELD_WORK_MODEL)  # ruff:ignore[private-member-access]
    if raw is None:
        mode_hint = safe_int(entity._price.get(FIELD_DYNAMIC_OR_SINGLE))  # ruff:ignore[private-member-access]
        if mode_hint == 1:
            return WORK_MODE_TO_OPTION[7]
        return None
    value = safe_int(raw)
    if value is None:
        return None
    option = WORK_MODE_TO_OPTION.get(value) or WORK_MODE_READ_ALIASES.get(value)
    if option is not None:
        return option
    entity._warn_unknown_once(value)  # ruff:ignore[private-member-access]
    return None


async def _work_mode_select(entity: JackerySelect, option: str) -> None:
    mode = _OPTION_TO_WORK_MODE.get(option)
    if mode is None:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_set_work_model(entity._device_id, mode)  # ruff:ignore[private-member-access]


def _temp_unit_current(entity: JackerySelect) -> str | None:
    val = safe_int(entity._properties.get(FIELD_TEMP_UNIT))  # ruff:ignore[private-member-access]
    if val is None:
        return None
    return TEMP_UNIT_TO_OPTION.get(val)


async def _temp_unit_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_TEMP_UNIT:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_set_temp_unit(
        entity._device_id,  # ruff:ignore[private-member-access]
        _OPTION_TO_TEMP_UNIT[option],
    )


def _island_auto_off_current(entity: JackerySelect) -> str | None:
    raw = entity._properties.get(FIELD_OFF_GRID_TIME)  # ruff:ignore[private-member-access]
    if raw is None:
        raw = task_plan_value(
            entity._task_plan,  # ruff:ignore[private-member-access]
            FIELD_OFF_GRID_TIME,
            FIELD_OFF_GRID_DOWN_TIME,
            FIELD_OFF_GRID_AUTO_OFF_TIME,
        )
    if raw is None:
        return None
    value = safe_int(raw)
    if value is None:
        return None
    if value in _HOURS_TO_AUTO_OFF_OPTION:
        return _HOURS_TO_AUTO_OFF_OPTION[value]
    if value % 60 == 0 and (value // 60) in _HOURS_TO_AUTO_OFF_OPTION:
        return _HOURS_TO_AUTO_OFF_OPTION[value // 60]
    return None


async def _island_auto_off_select(entity: JackerySelect, option: str) -> None:
    if option not in _AUTO_OFF_OPTION_TO_HOURS:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    hours = _AUTO_OFF_OPTION_TO_HOURS[option]
    await entity.coordinator.async_set_off_grid_time(entity._device_id, hours * 60)  # ruff:ignore[private-member-access]


def _storm_minutes_current_value(entity: JackerySelect) -> int | None:
    current = _storm_minutes_value(
        entity._properties,  # ruff:ignore[private-member-access]
        entity._weather_plan,  # ruff:ignore[private-member-access]
        entity._task_plan,  # ruff:ignore[private-member-access]
    )
    if current is not None:
        return current
    return _storm_minutes_fallback(
        entity._properties,  # ruff:ignore[private-member-access]
        entity._weather_plan,  # ruff:ignore[private-member-access]
        entity._task_plan,  # ruff:ignore[private-member-access]
    )


def _storm_minutes_options(entity: JackerySelect) -> list[str]:
    values = set(STORM_MINUTES_DEFAULT)
    current_minutes = _storm_minutes_current_value(entity)
    if current_minutes is not None and current_minutes > 0:
        values.add(current_minutes)
    return [_storm_minutes_label(m) for m in sorted(values)]


def _storm_minutes_current(entity: JackerySelect) -> str | None:
    current_minutes = _storm_minutes_current_value(entity)
    if current_minutes is None:
        return None
    return _storm_minutes_label(current_minutes)


async def _storm_minutes_select(entity: JackerySelect, option: str) -> None:
    match = re.fullmatch(r"min_(\d+)", option)
    if not match:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    minutes = int(match.group(1))
    await entity.coordinator.async_set_storm_minutes(entity._device_id, minutes)  # ruff:ignore[private-member-access]


def _price_mode_current(entity: JackerySelect) -> str | None:
    mode = _price_mode_current_int(entity)
    if mode is None:
        return None
    option = PRICE_MODE_TO_OPTION.get(mode)
    if option is not None:
        return option
    entity._warn_unknown_once(mode)  # ruff:ignore[private-member-access]
    return None


async def _price_mode_select(entity: JackerySelect, option: str) -> None:
    mode = _OPTION_TO_PRICE_MODE.get(option)
    if mode is None:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    if mode == _PRICE_MODE_DYNAMIC:
        if (
            not _price_mode_dynamic_available(entity)
            and _price_mode_current_int(entity) != _PRICE_MODE_DYNAMIC
        ):
            _raise_select_action_error(
                entity,
                "dynamic_tariff_unavailable",
                option=option,
            )
        await entity.coordinator.async_set_price_mode_dynamic(entity._device_id)  # ruff:ignore[private-member-access]
    elif mode == _PRICE_MODE_SINGLE:
        await entity.coordinator.async_set_price_mode_single(entity._device_id)  # ruff:ignore[private-member-access]
    else:
        _raise_select_action_error(entity, "invalid_select_option", option=option)


def _price_provider_options(entity: JackerySelect) -> list[str]:
    labels = [
        _price_source_label(source)
        for source in _price_sources_from_payload(entity._payload)  # ruff:ignore[private-member-access]
    ]
    current = entity.current_option
    if current and current not in labels:
        labels.append(current)
    return labels


def _price_provider_current(entity: JackerySelect) -> str | None:
    company_id = entity._price.get(FIELD_PLATFORM_COMPANY_ID)  # ruff:ignore[private-member-access]
    region = entity._price.get(FIELD_SYSTEM_REGION)  # ruff:ignore[private-member-access]
    company_id = normalized_company_id(company_id)
    if company_id is None:
        return None
    for source in _price_sources_from_payload(entity._payload):  # ruff:ignore[private-member-access]
        if _price_source_matches_current(source, company_id, region):
            return _price_source_label(source)
    return _price_source_label({
        FIELD_PLATFORM_COMPANY_ID: company_id,
        FIELD_COUNTRY: region,
        FIELD_COMPANY_NAME: entity._price.get(FIELD_COMPANY_NAME),  # ruff:ignore[private-member-access]
    })


async def _price_provider_select(entity: JackerySelect, option: str) -> None:
    for source in _price_sources_from_payload(entity._payload):  # ruff:ignore[private-member-access]
        if _price_source_label(source) == option:
            await entity.coordinator.async_set_price_source(entity._device_id, source)  # ruff:ignore[private-member-access]
            return
    _raise_select_action_error(entity, "invalid_select_option", option=option)


def _ct_phase_current(entity: JackerySelect) -> str | None:
    ct = entity._payload.get(PAYLOAD_CT_METER) or {}  # ruff:ignore[private-member-access]
    if not isinstance(ct, dict):
        return None
    raw_phase = safe_int(ct.get(FIELD_SCHE_PHASE))
    if raw_phase is None:
        return None
    return _CT_PHASE_TO_OPTION.get(raw_phase)


async def _ct_phase_select(entity: JackerySelect, option: str) -> None:
    phase = _OPTION_TO_CT_PHASE.get(option)
    if phase is None:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    ct = entity._payload.get(PAYLOAD_CT_METER) or {}  # ruff:ignore[private-member-access]
    if not isinstance(ct, dict):
        _raise_select_action_error(
            entity,
            "entity_action_failed",
            error="ct meter payload missing",
        )
    ct_sn = str(
        ct.get(FIELD_DEVICE_SN) or ct.get(FIELD_DEV_SN) or ct.get("deviceSn") or "",
    ).strip()
    if not ct_sn:
        _raise_select_action_error(
            entity,
            "entity_action_failed",
            error="ct meter serial missing",
        )
    await entity.coordinator.async_set_ct_phase(entity._device_id, ct_sn, phase)  # ruff:ignore[private-member-access]


_UPS_MODEL_OPTIONS: dict[int, str] = {
    5: "unknown",
    0: "standard",
    1: "lifepo4",
    2: "agm",
    3: "gel",
    4: "custom",
    6: "reserved",  # added placeholder for future values
}

_OPTION_TO_UPS_MODEL: dict[str, int] = {v: k for k, v in _UPS_MODEL_OPTIONS.items()}


def _portable_ups_model_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get(FIELD_UPS))  # ruff:ignore[private-member-access]
    if raw is None:
        return None
    return _UPS_MODEL_OPTIONS.get(raw)


async def _portable_ups_model_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_UPS_MODEL:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # ruff:ignore[private-member-access]
        action_id=ACTION_ID_PORTABLE_UPS_MODEL,
        field=FIELD_UPS,
        value=_OPTION_TO_UPS_MODEL[option],
    )


_POWER_MODE_OPTIONS: dict[int, str] = {
    0: "standard",
    1: "eco",
    2: "performance",
}

_OPTION_TO_POWER_MODE: dict[str, int] = {v: k for k, v in _POWER_MODE_OPTIONS.items()}

_SCREEN_TIMEOUT_OPTIONS: dict[int, str] = {1: "off", 2: "2min", 3: "2h"}

_OPTION_TO_SCREEN_TIMEOUT: dict[str, tuple[int, int]] = {
    "off": (0, 1),
    "2min": (2, 2),
    "2h": (120, 3),
}

_BATTERY_MODE_OPTIONS: dict[int, str] = {
    0: "normal",
    1: "preset",
    2: "custom",
}

_OPTION_TO_BATTERY_MODE: dict[str, int] = {
    v: k for k, v in _BATTERY_MODE_OPTIONS.items()
}


def _portable_battery_mode_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get("lps"))  # ruff:ignore[private-member-access]
    if raw is None:
        return None
    return _BATTERY_MODE_OPTIONS.get(raw)


async def _portable_battery_mode_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_BATTERY_MODE:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # ruff:ignore[private-member-access]
        action_id=ACTION_ID_PORTABLE_SETTING_BATTERY,
        field="lps",
        value=_OPTION_TO_BATTERY_MODE[option],
    )


_CHARGE_MODE_OPTIONS: dict[int, str] = {
    0: "fast",
    1: "rush",
    2: "custom",
}

_OPTION_TO_CHARGE_MODE: dict[str, int] = {v: k for k, v in _CHARGE_MODE_OPTIONS.items()}


def _portable_charge_mode_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get("cs"))  # ruff:ignore[private-member-access]
    if raw is None:
        return None
    return _CHARGE_MODE_OPTIONS.get(raw)


async def _portable_charge_mode_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_CHARGE_MODE:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # ruff:ignore[private-member-access]
        action_id=ACTION_ID_PORTABLE_SETTING_CHARGE,
        field="cs",
        value=_OPTION_TO_CHARGE_MODE[option],
    )


def _portable_power_mode_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get(FIELD_PM))  # ruff:ignore[private-member-access]
    if raw is None:
        return None
    return _POWER_MODE_OPTIONS.get(raw)


async def _portable_power_mode_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_POWER_MODE:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # ruff:ignore[private-member-access]
        action_id=ACTION_ID_PORTABLE_USE_POWER_MODE,
        field=FIELD_PM,
        value=_OPTION_TO_POWER_MODE[option],
    )


def _portable_screen_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get(FIELD_SLTB))  # ruff:ignore[private-member-access]
    if raw is None:
        return None
    return _SCREEN_TIMEOUT_OPTIONS.get(raw)


async def _portable_screen_select(entity: JackerySelect, option: str) -> None:
    values = _OPTION_TO_SCREEN_TIMEOUT.get(option)
    if values is None:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    command_value, state_value = values
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # ruff:ignore[private-member-access]
        action_id=ACTION_ID_PORTABLE_SCREEN,
        field="slt",
        value=command_value,
        local_patch={FIELD_SLTB: state_value},
    )


_AC_OUTPUT_MODE_OPTIONS: dict[int, str] = {
    0: "normal",
    1: "quiet",
    2: "high-performance",
}

_OPTION_TO_AC_OUTPUT_MODE: dict[str, int] = {
    v: k for k, v in _AC_OUTPUT_MODE_OPTIONS.items()
}


def _portable_ac_output_mode_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get("acmode"))  # ruff:ignore[private-member-access]
    if raw is None:
        return None
    return _AC_OUTPUT_MODE_OPTIONS.get(raw)


async def _portable_ac_output_mode_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_AC_OUTPUT_MODE:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # ruff:ignore[private-member-access]
        action_id=ACTION_ID_PORTABLE_AC_OUTPUT_MODE,
        field="acmode",
        value=_OPTION_TO_AC_OUTPUT_MODE[option],
    )


_OUTPUT_PRIORITY_OPTIONS: dict[int, str] = {
    0: "battery-first",
    1: "grid-first",
    2: "solar-first",
}

_OPTION_TO_OUTPUT_PRIORITY: dict[str, int] = {
    v: k for k, v in _OUTPUT_PRIORITY_OPTIONS.items()
}


def _portable_output_priority_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get("outPrio"))  # ruff:ignore[private-member-access]
    if raw is None:
        return None
    return _OUTPUT_PRIORITY_OPTIONS.get(raw)


async def _portable_output_priority_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_OUTPUT_PRIORITY:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # ruff:ignore[private-member-access]
        action_id=ACTION_ID_PORTABLE_OUTPUT_PRIORITY,
        field="outPrio",
        value=_OPTION_TO_OUTPUT_PRIORITY[option],
    )


def _portable_ac1_priority_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get("oac1Prio"))  # ruff:ignore[private-member-access]
    if raw is None:
        return None
    return _OUTPUT_PRIORITY_OPTIONS.get(raw)


async def _portable_ac1_priority_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_OUTPUT_PRIORITY:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # ruff:ignore[private-member-access]
        action_id=ACTION_ID_PORTABLE_OUTPUT_PRIORITY,
        field="oac1Prio",
        value=_OPTION_TO_OUTPUT_PRIORITY[option],
    )


def _portable_ac2_priority_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get("oac2Prio"))  # ruff:ignore[private-member-access]
    if raw is None:
        return None
    return _OUTPUT_PRIORITY_OPTIONS.get(raw)


async def _portable_ac2_priority_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_OUTPUT_PRIORITY:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # ruff:ignore[private-member-access]
        action_id=ACTION_ID_PORTABLE_OUTPUT_PRIORITY,
        field="oac2Prio",
        value=_OPTION_TO_OUTPUT_PRIORITY[option],
    )


def _portable_dc_priority_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get("odcPrio"))  # ruff:ignore[private-member-access]
    if raw is None:
        return None
    return _OUTPUT_PRIORITY_OPTIONS.get(raw)


async def _portable_dc_priority_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_OUTPUT_PRIORITY:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # ruff:ignore[private-member-access]
        action_id=ACTION_ID_PORTABLE_OUTPUT_PRIORITY,
        field="odcPrio",
        value=_OPTION_TO_OUTPUT_PRIORITY[option],
    )


# ---------------------------------------------------------------------------
# Select description tuples (migrated from select.py)
# ---------------------------------------------------------------------------


SELECT_DESCRIPTIONS: tuple[JackerySelectDescription, ...] = (
    JackerySelectDescription(
        key="work_mode_select",
        translation_key="work_mode_select",
        options=list(_OPTION_TO_WORK_MODE.keys()),
        value_fn=_work_mode_current,
        current_fn=_work_mode_current,
        select_fn=_work_mode_select,
        warn_unknown_kind="work mode",
    ),
    JackerySelectDescription(
        key="temp_unit_select",
        translation_key="temp_unit_select",
        options=list(_OPTION_TO_TEMP_UNIT.keys()),
        value_fn=_temp_unit_current,
        current_fn=_temp_unit_current,
        select_fn=_temp_unit_select,
    ),
    JackerySelectDescription(
        key="auto_off_island_mode",
        translation_key="auto_off_island_mode",
        options=list(_AUTO_OFF_OPTIONS),
        value_fn=_island_auto_off_current,
        current_fn=_island_auto_off_current,
        select_fn=_island_auto_off_select,
    ),
    JackerySelectDescription(
        key="storm_warning_minutes_select",
        translation_key="storm_warning_minutes_select",
        device_registry_role="system",
        options_fn=_storm_minutes_options,
        value_fn=_storm_minutes_current,
        current_fn=_storm_minutes_current,
        select_fn=_storm_minutes_select,
    ),
    JackerySelectDescription(
        key="electricity_price_mode",
        translation_key="electricity_price_mode",
        device_registry_role="system",
        options=[PRICE_MODE_TO_OPTION[1], PRICE_MODE_TO_OPTION[2]],
        value_fn=_price_mode_current,
        current_fn=_price_mode_current,
        select_fn=_price_mode_select,
        warn_unknown_kind="electricity price mode",
    ),
    JackerySelectDescription(
        key="electricity_price_provider",
        translation_key="electricity_price_provider",
        device_registry_role="system",
        options_fn=_price_provider_options,
        value_fn=_price_provider_current,
        current_fn=_price_provider_current,
        select_fn=_price_provider_select,
    ),
    JackerySelectDescription(
        key="ct_phase_select",
        translation_key="ct_phase_select",
        options=list(_CT_PHASE_TO_OPTION.values()),
        value_fn=_ct_phase_current,
        current_fn=_ct_phase_current,
        select_fn=_ct_phase_select,
    ),
    JackerySelectDescription(
        key="portable_ups_model",
        translation_key="portable_ups_model",
        options=list(_OPTION_TO_UPS_MODEL.keys()),
        value_fn=_portable_ups_model_current,
        current_fn=_portable_ups_model_current,
        select_fn=_portable_ups_model_select,
    ),
    JackerySelectDescription(
        key="portable_power_mode",
        translation_key="portable_power_mode",
        options=list(_OPTION_TO_POWER_MODE.keys()),
        value_fn=_portable_power_mode_current,
        current_fn=_portable_power_mode_current,
        select_fn=_portable_power_mode_select,
    ),
    JackerySelectDescription(
        key="portable_screen",
        translation_key="portable_screen",
        entity_category=EntityCategory.CONFIG,
        options=list(_OPTION_TO_SCREEN_TIMEOUT.keys()),
        value_fn=_portable_screen_current,
        current_fn=_portable_screen_current,
        select_fn=_portable_screen_select,
    ),
    JackerySelectDescription(
        key="portable_battery_mode",
        translation_key="portable_battery_mode",
        options=list(_OPTION_TO_BATTERY_MODE.keys()),
        value_fn=_portable_battery_mode_current,
        current_fn=_portable_battery_mode_current,
        select_fn=_portable_battery_mode_select,
    ),
    JackerySelectDescription(
        key="portable_charge_mode",
        translation_key="portable_charge_mode",
        options=list(_OPTION_TO_CHARGE_MODE.keys()),
        value_fn=_portable_charge_mode_current,
        current_fn=_portable_charge_mode_current,
        select_fn=_portable_charge_mode_select,
    ),
    JackerySelectDescription(
        key="portable_ac_output_mode",
        translation_key="portable_ac_output_mode",
        options=list(_OPTION_TO_AC_OUTPUT_MODE.keys()),
        value_fn=_portable_ac_output_mode_current,
        current_fn=_portable_ac_output_mode_current,
        select_fn=_portable_ac_output_mode_select,
    ),
    JackerySelectDescription(
        key="portable_output_priority",
        translation_key="portable_output_priority",
        options=list(_OPTION_TO_OUTPUT_PRIORITY.keys()),
        value_fn=_portable_output_priority_current,
        current_fn=_portable_output_priority_current,
        select_fn=_portable_output_priority_select,
    ),
    JackerySelectDescription(
        key="portable_ac1_priority",
        translation_key="portable_ac1_priority",
        options=list(_OPTION_TO_OUTPUT_PRIORITY.keys()),
        value_fn=_portable_ac1_priority_current,
        current_fn=_portable_ac1_priority_current,
        select_fn=_portable_ac1_priority_select,
    ),
    JackerySelectDescription(
        key="portable_ac2_priority",
        translation_key="portable_ac2_priority",
        options=list(_OPTION_TO_OUTPUT_PRIORITY.keys()),
        value_fn=_portable_ac2_priority_current,
        current_fn=_portable_ac2_priority_current,
        select_fn=_portable_ac2_priority_select,
    ),
    JackerySelectDescription(
        key="portable_dc_priority",
        translation_key="portable_dc_priority",
        options=list(_OPTION_TO_OUTPUT_PRIORITY.keys()),
        value_fn=_portable_dc_priority_current,
        current_fn=_portable_dc_priority_current,
        select_fn=_portable_dc_priority_select,
    ),
)
