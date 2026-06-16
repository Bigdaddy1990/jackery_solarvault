"""Select platform for Jackery SolarVault preset-style controls.

Description-driven entities; one generic class handles every selector. The
pattern mirrors number.py: each select is described by a frozen dataclass
that captures option set / option provider, the current-option getter and the
write action that pushes a new selection to the cloud / MQTT command path.

Heterogeneous behaviour (dynamic options, fallback payload sections, unknown
value warnings) lives as module-level helper functions so the description
registry stays declarative.
"""

from dataclasses import dataclass, field
import logging
import re
from typing import TYPE_CHECKING, Any, NoReturn

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .const import (
    AUTO_OFF_HOURS,
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
    FIELD_OFF_GRID_DOWN,
    FIELD_OFF_GRID_DOWN_TIME,
    FIELD_OFF_GRID_TIME,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_PM,
    FIELD_PRICE_MODE,
    FIELD_SCHE_PHASE,
    FIELD_SINGLE_PRICE,
    FIELD_STORM,
    FIELD_SYSTEM_REGION,
    FIELD_TEMP_UNIT,
    FIELD_UPS,
    FIELD_WORK_MODEL,
    FIELD_WPC,
    FIELD_WPS,
    PAYLOAD_CT_METER,
    PAYLOAD_PRICE,
    PAYLOAD_PRICE_SOURCES,
    PAYLOAD_PROPERTIES,
    PAYLOAD_WEATHER_PLAN,
    PRICE_MODE_TO_OPTION,
    STORM_MINUTES_DEFAULT,
    STORM_MINUTES_MIN_VALID,
    TEMP_UNIT_TO_OPTION,
    WORK_MODE_READ_ALIASES,
    WORK_MODE_TO_OPTION,
)
from .entity import JackeryEntity
from .entity_contract import DEFAULT_LIVE_SOURCES, DEFAULT_NULL_SEMANTICS
from .exceptions import ACTION_WRITE_ERRORS
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    safe_int,
    task_plan_value,
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

_OPTION_TO_WORK_MODE = {v: k for k, v in WORK_MODE_TO_OPTION.items()}
_OPTION_TO_TEMP_UNIT = {v: k for k, v in TEMP_UNIT_TO_OPTION.items()}
_AUTO_OFF_OPTIONS = [f"h_{hours}" for hours in AUTO_OFF_HOURS]
_HOURS_TO_AUTO_OFF_OPTION = {hours: f"h_{hours}" for hours in AUTO_OFF_HOURS}
_AUTO_OFF_OPTION_TO_HOURS = {f"h_{hours}": hours for hours in AUTO_OFF_HOURS}
_OPTION_TO_PRICE_MODE = {v: k for k, v in PRICE_MODE_TO_OPTION.items()}
_CT_PHASE_TO_OPTION = {
    1: "phase_1",
    2: "phase_2",
    3: "phase_3",
    # App schePhase=4 is not a fourth conductor; it means combined phases.
    4: "combined_phases",
}
_OPTION_TO_CT_PHASE = {value: key for key, value in _CT_PHASE_TO_OPTION.items()}
_OPTION_TO_CT_PHASE["phase_4"] = 4  # legacy option name kept for service callers


def _raise_select_action_error(
    entity: JackerySelect,
    translation_key: str,
    **placeholders: object,
) -> NoReturn:
    """Raise a translatable HA action error for a select entity."""
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders={
            "entity": entity.entity_description.key,
            "device_id": entity._device_id,  # noqa: SLF001
            **{key: str(value) for key, value in placeholders.items()},
        },
    )


# ---------------------------------------------------------------------------
# Storm-warning helpers
# ---------------------------------------------------------------------------


def _storm_minutes_value(  # noqa: PLR0912
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
    raw: object | None = None
    for key in (FIELD_WPC, FIELD_MINS_INTERVAL):
        raw = properties.get(key)
        if raw is not None:
            break
    if raw is None:
        for key in (FIELD_WPC, FIELD_MINS_INTERVAL):
            raw = weather_plan.get(key)
            if raw is not None:
                break
    if raw is None:
        raw = task_plan_value(task_plan, FIELD_WPC, FIELD_MINS_INTERVAL)
    if raw is None:
        storm = weather_plan.get(FIELD_STORM)
        if isinstance(storm, list):
            for item in storm:
                if not isinstance(item, dict):
                    continue
                raw = item.get(FIELD_WPC)
                if raw is None:
                    raw = item.get(FIELD_MINS_INTERVAL)
                if raw is not None:
                    break
    if raw is None:
        return None
    value = safe_int(raw)
    if value is None:
        return None
    # ``wpc``/``minsInterval`` below STORM_MINUTES_MIN_VALID are firmware
    # sentinels for "not set" — drop them so the select does not invent an
    # untranslated ``min_<value>`` option (e.g. ``min_1``).
    return value if value >= STORM_MINUTES_MIN_VALID else None


def _storm_minutes_fallback(
    properties: dict[str, object],
    weather_plan: dict[str, object],
    task_plan: dict[str, object],
) -> int | None:
    """Return `DEFAULT_STORM_WARNING_MINUTES` when a storm-enabled indicator exists but.

    no explicit lead time.

    Checks for a storm-enabled marker (`FIELD_WPS`) in `properties`, then
    `weather_plan`, then `task_plan`; if the marker is present and parses to an
    integer, returns `DEFAULT_STORM_WARNING_MINUTES`. If `weather_plan[FIELD_STORM]` is
    a list, also returns `DEFAULT_STORM_WARNING_MINUTES`. Otherwise returns `None`.

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


# ---------------------------------------------------------------------------
# Electricity-price helpers
# ---------------------------------------------------------------------------


def _price_source_label(source: dict[str, object]) -> str:
    name = str(
        source.get(FIELD_COMPANY_NAME)
        or source.get(FIELD_NAME)
        or source.get(FIELD_CID)
        or source.get(FIELD_PLATFORM_COMPANY_ID)
        or "Provider",
    ).strip()
    country = str(
        source.get(FIELD_COUNTRY) or source.get(FIELD_SYSTEM_REGION) or "",
    ).strip()
    company_id = source.get(FIELD_PLATFORM_COMPANY_ID)
    label = f"{name} ({country})" if country else name
    if company_id not in {None, ""}:
        return f"{label} #{company_id}"
    return label


def _price_source_regions(source: dict[str, object]) -> list[str]:
    raw = source.get(FIELD_COUNTRY) or source.get(FIELD_SYSTEM_REGION)
    if raw in {None, ""}:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _price_source_matches_current(
    source: dict[str, object],
    company_id: object,
    region: object,
) -> bool:
    if str(source.get(FIELD_PLATFORM_COMPANY_ID)) != str(company_id):
        return False
    if region in {None, ""}:
        return True
    return str(region) in _price_source_regions(source)


def _price_sources_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    raw = payload.get(PAYLOAD_PRICE_SOURCES)
    if not isinstance(raw, list):
        return []
    out: list[dict[str, object]] = []
    for item in raw:
        if isinstance(item, dict):
            company_id = item.get(FIELD_PLATFORM_COMPANY_ID)
            country = item.get(FIELD_COUNTRY) or item.get(FIELD_SYSTEM_REGION)
            if company_id not in {None, ""} and country:
                out.append(item)
    return out


def _price_mode_dynamic_available(entity: JackerySelect) -> bool:
    company_id = entity._price.get(FIELD_PLATFORM_COMPANY_ID)  # noqa: SLF001
    region = entity._price.get(FIELD_SYSTEM_REGION)  # noqa: SLF001
    if company_id not in {None, ""} and bool(region):
        return True
    return bool(_price_sources_from_payload(entity._payload))  # noqa: SLF001


def _price_mode_current_int(entity: JackerySelect) -> int | None:
    raw = entity._price.get(FIELD_DYNAMIC_OR_SINGLE)  # noqa: SLF001
    if raw is None:
        raw = task_plan_value(
            entity._task_plan,  # noqa: SLF001
            FIELD_DYNAMIC_OR_SINGLE,
            FIELD_PRICE_MODE,  # noqa: RUF100, SLF001
        )
    if raw is None:
        work_mode = safe_int(entity._properties.get(FIELD_WORK_MODEL))  # noqa: SLF001
        if work_mode == 7:  # noqa: PLR2004
            return 1
        if entity._price.get(FIELD_SINGLE_PRICE) is not None:  # noqa: SLF001
            return 2
        return None
    return safe_int(raw)


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class JackerySelectDescription(SelectEntityDescription):
    """Describes a Jackery select entity.

    Either ``options`` (static list) or ``options_fn`` (dynamic callable that
    receives the entity instance) provides the dropdown values. ``current_fn``
    pulls the current option from the coordinator payload and is allowed to
    log unmapped raw values via ``entity._warn_unknown_once``. ``select_fn``
    pushes a new selection to the coordinator's cloud / MQTT command path.

    ``warn_unknown_kind`` enables the once-per-instance warn cache for cases
    where the cloud reports an integer that neither ``WORK_MODE_TO_OPTION``
    nor ``WORK_MODE_READ_ALIASES`` (etc.) covers.
    """

    options: list[str] | None = None
    options_fn: Callable[[JackerySelect], list[str]] | None = None
    current_fn: Callable[[JackerySelect], str | None]
    select_fn: Callable[[JackerySelect, str], Awaitable[None]]
    warn_unknown_kind: str | None = None
    smali_field: str | None = None
    data_sources: tuple[str, ...] = DEFAULT_LIVE_SOURCES
    null_semantics: str = DEFAULT_NULL_SEMANTICS
    recorder_allowed: bool = True
    ha_derived: bool = False


# ---------------------------------------------------------------------------
# Generic entity
# ---------------------------------------------------------------------------


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
        self._state = _SelectState()

    @property
    def options(self) -> list[str]:
        """Return the list of available options."""
        description = self.entity_description
        if description.options_fn is not None:
            return description.options_fn(self)
        return list(description.options or ())

    @property
    def current_option(self) -> str | None:
        """Return the currently-selected option."""
        return self.entity_description.current_fn(self)

    async def async_select_option(self, option: str) -> None:
        """Forward the chosen option to the coordinator."""
        try:
            await self.entity_description.select_fn(self, option)
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            _raise_select_action_error(self, "entity_action_failed", error=err)
        except ACTION_WRITE_ERRORS as err:
            _raise_select_action_error(self, "entity_action_failed", error=err)

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


# ---------------------------------------------------------------------------
# Per-description current/select callables
# ---------------------------------------------------------------------------


def _work_mode_current(entity: JackerySelect) -> str | None:
    raw = entity._properties.get(FIELD_WORK_MODEL)  # noqa: SLF001
    if raw is None:
        raw = task_plan_value(entity._task_plan, FIELD_WORK_MODEL)  # noqa: SLF001
    if raw is None:
        mode_hint = safe_int(entity._price.get(FIELD_DYNAMIC_OR_SINGLE))  # noqa: SLF001
        if mode_hint == 1:
            return WORK_MODE_TO_OPTION[7]
        return None
    value = safe_int(raw)
    if value is None:
        return None
    option = WORK_MODE_TO_OPTION.get(value) or WORK_MODE_READ_ALIASES.get(value)
    if option is not None:
        return option
    entity._warn_unknown_once(value)  # noqa: SLF001
    return None


async def _work_mode_select(entity: JackerySelect, option: str) -> None:
    mode = _OPTION_TO_WORK_MODE.get(option)
    if mode is None:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_set_work_model(entity._device_id, mode)  # noqa: SLF001


def _temp_unit_current(entity: JackerySelect) -> str | None:
    val = safe_int(entity._properties.get(FIELD_TEMP_UNIT))  # noqa: SLF001
    if val is None:
        return None
    return TEMP_UNIT_TO_OPTION.get(val)


async def _temp_unit_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_TEMP_UNIT:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_set_temp_unit(
        entity._device_id,  # noqa: SLF001
        _OPTION_TO_TEMP_UNIT[option],  # noqa: RUF100, SLF001
    )


def _island_auto_off_current(entity: JackerySelect) -> str | None:
    raw = entity._properties.get(FIELD_OFF_GRID_TIME)  # noqa: SLF001
    if raw is None:
        raw = task_plan_value(
            entity._task_plan,  # noqa: SLF001
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
    await entity.coordinator.async_set_off_grid_time(entity._device_id, hours * 60)  # noqa: SLF001


def _storm_minutes_current_value(entity: JackerySelect) -> int | None:
    current = _storm_minutes_value(
        entity._properties,  # noqa: SLF001
        entity._weather_plan,  # noqa: SLF001
        entity._task_plan,  # noqa: SLF001
    )
    if current is not None:
        return current
    return _storm_minutes_fallback(
        entity._properties,  # noqa: SLF001
        entity._weather_plan,  # noqa: SLF001
        entity._task_plan,  # noqa: SLF001
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
    await entity.coordinator.async_set_storm_minutes(entity._device_id, minutes)  # noqa: SLF001


def _price_mode_current(entity: JackerySelect) -> str | None:
    mode = _price_mode_current_int(entity)
    if mode is None:
        return None
    option = PRICE_MODE_TO_OPTION.get(mode)
    if option is not None:
        return option
    entity._warn_unknown_once(mode)  # noqa: SLF001
    return None


async def _price_mode_select(entity: JackerySelect, option: str) -> None:
    mode = _OPTION_TO_PRICE_MODE.get(option)
    if mode is None:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    if mode == 1:
        if (
            not _price_mode_dynamic_available(entity)
            and _price_mode_current_int(entity) != 1
        ):
            _raise_select_action_error(
                entity,
                "dynamic_tariff_unavailable",
                option=option,
            )
        await entity.coordinator.async_set_price_mode_dynamic(entity._device_id)  # noqa: SLF001
    elif mode == 2:  # noqa: PLR2004
        await entity.coordinator.async_set_price_mode_single(entity._device_id)  # noqa: SLF001


def _price_provider_options(entity: JackerySelect) -> list[str]:
    labels = [
        _price_source_label(source)
        for source in _price_sources_from_payload(entity._payload)  # noqa: SLF001
    ]
    current = entity.current_option
    if current and current not in labels:
        labels.append(current)
    return labels


def _price_provider_current(entity: JackerySelect) -> str | None:
    company_id = entity._price.get(FIELD_PLATFORM_COMPANY_ID)  # noqa: SLF001
    region = entity._price.get(FIELD_SYSTEM_REGION)  # noqa: SLF001
    if company_id in {None, ""}:
        return None
    for source in _price_sources_from_payload(entity._payload):  # noqa: SLF001
        if _price_source_matches_current(source, company_id, region):
            return _price_source_label(source)
    return _price_source_label({
        FIELD_PLATFORM_COMPANY_ID: company_id,
        FIELD_COUNTRY: region,
        FIELD_COMPANY_NAME: entity._price.get(FIELD_COMPANY_NAME),  # noqa: SLF001
    })


async def _price_provider_select(entity: JackerySelect, option: str) -> None:
    for source in _price_sources_from_payload(entity._payload):  # noqa: SLF001
        if _price_source_label(source) == option:
            await entity.coordinator.async_set_price_source(entity._device_id, source)  # noqa: SLF001
            return
    _raise_select_action_error(entity, "invalid_select_option", option=option)


def _ct_phase_current(entity: JackerySelect) -> str | None:
    ct = entity._payload.get(PAYLOAD_CT_METER) or {}  # noqa: SLF001
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
    ct = entity._payload.get(PAYLOAD_CT_METER) or {}  # noqa: SLF001
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
    await entity.coordinator.async_set_ct_phase(entity._device_id, ct_sn, phase)  # noqa: SLF001


# ---------------------------------------------------------------------------
# Portable / Explorer powerstation select helpers
# ---------------------------------------------------------------------------

# UPS model options (field "ups", msgId=24)
_UPS_MODEL_OPTIONS: dict[int, str] = {
    0: "standard",
    1: "lifepo4",
    2: "agm",
    3: "gel",
    4: "custom",
}
_OPTION_TO_UPS_MODEL: dict[str, int] = {v: k for k, v in _UPS_MODEL_OPTIONS.items()}


def _portable_ups_model_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get(FIELD_UPS))  # noqa: SLF001
    if raw is None:
        return None
    return _UPS_MODEL_OPTIONS.get(raw)


async def _portable_ups_model_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_UPS_MODEL:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # noqa: SLF001
        action_id=24,
        field=FIELD_UPS,
        value=_OPTION_TO_UPS_MODEL[option],
    )


# Power mode options (field "pm", msgId=32)
_POWER_MODE_OPTIONS: dict[int, str] = {
    0: "standard",
    1: "eco",
    2: "performance",
}
_OPTION_TO_POWER_MODE: dict[str, int] = {v: k for k, v in _POWER_MODE_OPTIONS.items()}


def _portable_power_mode_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get(FIELD_PM))  # noqa: SLF001
    if raw is None:
        return None
    return _POWER_MODE_OPTIONS.get(raw)


async def _portable_power_mode_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_POWER_MODE:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # noqa: SLF001
        action_id=32,
        field=FIELD_PM,
        value=_OPTION_TO_POWER_MODE[option],
    )


# AC output mode options (field "acmode", msgId=40)
_AC_OUTPUT_MODE_OPTIONS: dict[int, str] = {
    0: "normal",
    1: "quiet",
    2: "high-performance",
}
_OPTION_TO_AC_OUTPUT_MODE: dict[str, int] = {
    v: k for k, v in _AC_OUTPUT_MODE_OPTIONS.items()
}


def _portable_ac_output_mode_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get("acmode"))  # noqa: SLF001
    if raw is None:
        return None
    return _AC_OUTPUT_MODE_OPTIONS.get(raw)


async def _portable_ac_output_mode_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_AC_OUTPUT_MODE:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # noqa: SLF001
        action_id=40,
        field="acmode",
        value=_OPTION_TO_AC_OUTPUT_MODE[option],
    )


# Output priority options (field "outPrio", msgId=48)
_OUTPUT_PRIORITY_OPTIONS: dict[int, str] = {
    0: "battery-first",
    1: "grid-first",
    2: "solar-first",
}
_OPTION_TO_OUTPUT_PRIORITY: dict[str, int] = {
    v: k for k, v in _OUTPUT_PRIORITY_OPTIONS.items()
}


def _portable_output_priority_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get("outPrio"))  # noqa: SLF001
    if raw is None:
        return None
    return _OUTPUT_PRIORITY_OPTIONS.get(raw)


async def _portable_output_priority_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_OUTPUT_PRIORITY:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # noqa: SLF001
        action_id=48,
        field="outPrio",
        value=_OPTION_TO_OUTPUT_PRIORITY[option],
    )


def _portable_ac1_priority_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get("oac1Prio"))  # noqa: SLF001
    if raw is None:
        return None
    return _OUTPUT_PRIORITY_OPTIONS.get(raw)


async def _portable_ac1_priority_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_OUTPUT_PRIORITY:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # noqa: SLF001
        action_id=49,
        field="oac1Prio",
        value=_OPTION_TO_OUTPUT_PRIORITY[option],
    )


def _portable_ac2_priority_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get("oac2Prio"))  # noqa: SLF001
    if raw is None:
        return None
    return _OUTPUT_PRIORITY_OPTIONS.get(raw)


async def _portable_ac2_priority_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_OUTPUT_PRIORITY:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # noqa: SLF001
        action_id=49,
        field="oac2Prio",
        value=_OPTION_TO_OUTPUT_PRIORITY[option],
    )


def _portable_dc_priority_current(entity: JackerySelect) -> str | None:
    raw = safe_int(entity._properties.get("odcPrio"))  # noqa: SLF001
    if raw is None:
        return None
    return _OUTPUT_PRIORITY_OPTIONS.get(raw)


async def _portable_dc_priority_select(entity: JackerySelect, option: str) -> None:
    if option not in _OPTION_TO_OUTPUT_PRIORITY:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_portable_set_select(
        entity._device_id,  # noqa: SLF001
        action_id=49,
        field="odcPrio",
        value=_OPTION_TO_OUTPUT_PRIORITY[option],
    )


# ---------------------------------------------------------------------------
# Description registry
# ---------------------------------------------------------------------------

SELECT_DESCRIPTIONS: tuple[JackerySelectDescription, ...] = (
    JackerySelectDescription(
        key="work_mode_select",
        translation_key="work_mode_select",
        icon="mdi:tune-variant",
        options=list(_OPTION_TO_WORK_MODE.keys()),
        current_fn=_work_mode_current,
        select_fn=_work_mode_select,
        warn_unknown_kind="work mode",
    ),
    JackerySelectDescription(
        key="temp_unit_select",
        translation_key="temp_unit_select",
        icon="mdi:thermometer",
        options=list(_OPTION_TO_TEMP_UNIT.keys()),
        current_fn=_temp_unit_current,
        select_fn=_temp_unit_select,
    ),
    JackerySelectDescription(
        key="auto_off_island_mode",
        translation_key="auto_off_island_mode",
        icon="mdi:timer-cog-outline",
        options=list(_AUTO_OFF_OPTIONS),
        current_fn=_island_auto_off_current,
        select_fn=_island_auto_off_select,
    ),
    JackerySelectDescription(
        key="storm_warning_minutes_select",
        translation_key="storm_warning_minutes_select",
        icon="mdi:weather-lightning-rainy",
        options_fn=_storm_minutes_options,
        current_fn=_storm_minutes_current,
        select_fn=_storm_minutes_select,
    ),
    JackerySelectDescription(
        key="electricity_price_mode",
        translation_key="electricity_price_mode",
        icon="mdi:cash-multiple",
        options=[PRICE_MODE_TO_OPTION[1], PRICE_MODE_TO_OPTION[2]],
        current_fn=_price_mode_current,
        select_fn=_price_mode_select,
        warn_unknown_kind="electricity price mode",
    ),
    JackerySelectDescription(
        key="electricity_price_provider",
        translation_key="electricity_price_provider",
        icon="mdi:transmission-tower-import",
        options_fn=_price_provider_options,
        current_fn=_price_provider_current,
        select_fn=_price_provider_select,
    ),
    JackerySelectDescription(
        key="ct_phase_select",
        translation_key="ct_phase_select",
        icon="mdi:transmission-tower",
        options=list(_CT_PHASE_TO_OPTION.values()),
        current_fn=_ct_phase_current,
        select_fn=_ct_phase_select,
    ),
    # --- Portable / Explorer powerstation selects ---
    JackerySelectDescription(
        key="portable_ups_model",
        translation_key="portable_ups_model",
        icon="mdi:battery-charging-outline",
        options=list(_OPTION_TO_UPS_MODEL.keys()),
        current_fn=_portable_ups_model_current,
        select_fn=_portable_ups_model_select,
    ),
    JackerySelectDescription(
        key="portable_power_mode",
        translation_key="portable_power_mode",
        icon="mdi:flash",
        options=list(_OPTION_TO_POWER_MODE.keys()),
        current_fn=_portable_power_mode_current,
        select_fn=_portable_power_mode_select,
    ),
    JackerySelectDescription(
        key="portable_ac_output_mode",
        translation_key="portable_ac_output_mode",
        icon="mdi:current-ac",
        options=list(_OPTION_TO_AC_OUTPUT_MODE.keys()),
        current_fn=_portable_ac_output_mode_current,
        select_fn=_portable_ac_output_mode_select,
    ),
    JackerySelectDescription(
        key="portable_output_priority",
        translation_key="portable_output_priority",
        icon="mdi:sort-bool-descending",
        options=list(_OPTION_TO_OUTPUT_PRIORITY.keys()),
        current_fn=_portable_output_priority_current,
        select_fn=_portable_output_priority_select,
    ),
    JackerySelectDescription(
        key="portable_ac1_priority",
        translation_key="portable_ac1_priority",
        icon="mdi:current-ac",
        options=list(_OPTION_TO_OUTPUT_PRIORITY.keys()),
        current_fn=_portable_ac1_priority_current,
        select_fn=_portable_ac1_priority_select,
    ),
    JackerySelectDescription(
        key="portable_ac2_priority",
        translation_key="portable_ac2_priority",
        icon="mdi:current-ac",
        options=list(_OPTION_TO_OUTPUT_PRIORITY.keys()),
        current_fn=_portable_ac2_priority_current,
        select_fn=_portable_ac2_priority_select,
    ),
    JackerySelectDescription(
        key="portable_dc_priority",
        translation_key="portable_dc_priority",
        icon="mdi:current-dc",
        options=list(_OPTION_TO_OUTPUT_PRIORITY.keys()),
        current_fn=_portable_dc_priority_current,
        select_fn=_portable_dc_priority_select,
    ),
)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def async_setup_entry(  # noqa: RUF029  # HA awaits this entry point
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create select entities for devices in the config entry.

    Registers entities for each device based on device capabilities. Entities are added
    immediately and updated whenever the coordinator detects new or changed devices.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[SelectEntity], entity: SelectEntity) -> None:
        append_unique_entity(
            entities,
            seen_unique_ids,
            entity,
            platform="select",
            logger=_LOGGER,
        )

    # Gating predicates per description key. Each predicate returns True when
    # the device is known to expose / accept the corresponding selector.
    def _gate(key: str, payload: dict[str, Any], supports_advanced: bool) -> bool:  # noqa: PLR0911
        """Determine whether a select entity identified by `key` should be created for.

        a device described by `payload`.

        Checks device payload fields and the `supports_advanced` flag to decide if the
        given select type is applicable for the device.

        Parameters:
            key (str): Description key identifying the select entity type (e.g.,
            "work_mode_select").
            payload (dict[str, Any]): Device payload containing properties, price and
            weather-plan information.
            supports_advanced (bool): Whether the device advertises advanced feature
            support; enables selects that otherwise require specific payload fields.

        Returns:
            bool: `True` if the select entity for `key` is supported for this device,
            `False` otherwise.
        """
        props = payload.get(PAYLOAD_PROPERTIES) or {}
        weather_plan = payload.get(PAYLOAD_WEATHER_PLAN) or {}
        if key == "work_mode_select":
            return supports_advanced or FIELD_WORK_MODEL in props
        if key == "temp_unit_select":
            return supports_advanced or FIELD_TEMP_UNIT in props
        if key == "auto_off_island_mode":
            return (
                supports_advanced
                or FIELD_OFF_GRID_TIME in props
                or FIELD_OFF_GRID_DOWN in props
            )
        if key == "storm_warning_minutes_select":
            return (
                supports_advanced
                or FIELD_WPC in props
                or FIELD_MINS_INTERVAL in props
                or FIELD_WPC in weather_plan
                or FIELD_MINS_INTERVAL in weather_plan
            )
        if key == "electricity_price_mode":
            return True
        if key == "electricity_price_provider":
            current_company = (payload.get(PAYLOAD_PRICE) or {}).get(
                FIELD_PLATFORM_COMPANY_ID,
            )
            return bool(payload.get(PAYLOAD_PRICE_SOURCES)) or current_company not in {
                None,
                "",
            }
        if key == "ct_phase_select":
            return isinstance(payload.get(PAYLOAD_CT_METER), dict)
        return False

    def _collect_entities() -> list[SelectEntity]:
        """Collect JackerySelect entities for coordinator devices that meet the module.

        gating rules.

        Iterates coordinator.data and, for each device, instantiates a JackerySelect
        for each description whose key passes _gate(description.key, payload,
        supports_advanced). Ensures created entities have unique identifiers by
        filtering duplicates.

        Returns:
            list[SelectEntity]: Created JackerySelect instances for eligible devices.
        """
        entities: list[SelectEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            supports_advanced = coordinator.device_supports_advanced(dev_id)
            for description in SELECT_DESCRIPTIONS:
                if _gate(description.key, payload, supports_advanced):
                    _append_unique(
                        entities,
                        JackerySelect(coordinator, dev_id, description),
                    )
        return entities

    last_signature: tuple[Any, ...] = ()

    @callback
    def _add_new_entities() -> None:
        """Detect changes in the coordinator's device payloads and register any newly discovered select entities.

        When the computed signature of coordinator.data differs from the last-seen signature, collect eligible entities and pass them to the platform's async_add_entities callback, then update the cached signature. If the signature is unchanged, take no action.
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
