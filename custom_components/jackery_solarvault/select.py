"""Select platform for Jackery SolarVault preset-style controls.

Description-driven entities; one generic class handles every selector. The
pattern mirrors number.py: each select is described by a frozen dataclass
that captures option set / option provider, the current-option getter and the
write action that pushes a new selection to the cloud / MQTT command path.

Heterogeneous behaviour (dynamic options, fallback payload sections, unknown
value warnings) lives as module-level helper functions so the description
registry stays declarative.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
import re
from typing import Any, NoReturn

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .const import (
    AUTO_OFF_HOURS,
    DEFAULT_STORM_WARNING_MINUTES,
    DOMAIN,
    FIELD_CID,
    FIELD_COMPANY_NAME,
    FIELD_COUNTRY,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_MINS_INTERVAL,
    FIELD_NAME,
    FIELD_OFF_GRID_AUTO_OFF_TIME,
    FIELD_OFF_GRID_DOWN,
    FIELD_OFF_GRID_DOWN_TIME,
    FIELD_OFF_GRID_TIME,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_PRICE_MODE,
    FIELD_SINGLE_PRICE,
    FIELD_STORM,
    FIELD_SYSTEM_REGION,
    FIELD_TEMP_UNIT,
    FIELD_WORK_MODEL,
    FIELD_WPC,
    FIELD_WPS,
    PAYLOAD_PRICE,
    PAYLOAD_PRICE_SOURCES,
    PAYLOAD_PROPERTIES,
    PAYLOAD_WEATHER_PLAN,
    PRICE_MODE_TO_OPTION,
    STORM_MINUTES_DEFAULT,
    TEMP_UNIT_TO_OPTION,
    WORK_MODE_READ_ALIASES,
    WORK_MODE_TO_OPTION,
)
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    safe_int,
    task_plan_value,
)

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


def _raise_select_action_error(
    entity: JackerySelect,
    translation_key: str,
    **placeholders: object,
) -> NoReturn:
    """Raise a HomeAssistantError with translation metadata for a JackerySelect entity.

    The raised error uses DOMAIN as `translation_domain`, the provided `translation_key`,
    and a `translation_placeholders` mapping that always includes:
    - `"entity"`: the entity description key,
    - `"device_id"`: the entity's device id,
    and any additional placeholder kwargs converted to strings.

    Parameters:
        entity (JackerySelect): The select entity related to the action.
        translation_key (str): Translation key identifying the error message.
        **placeholders: object: Additional translation placeholders; values will be cast to strings.

    Raises:
        HomeAssistantError: Always raised with the assembled translation metadata.
    """
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders={
            "entity": entity.entity_description.key,
            "device_id": entity._device_id,
            **{key: str(value) for key, value in placeholders.items()},
        },
    )


# ---------------------------------------------------------------------------
# Storm-warning helpers
# ---------------------------------------------------------------------------


def _storm_minutes_value(
    properties: dict[str, object],
    weather_plan: dict[str, object],
    task_plan: dict[str, object],
) -> int | None:
    """Extract storm warning lead-time from known payload variants."""
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
    return value if value > 0 else None


def _storm_minutes_fallback(
    properties: dict[str, object],
    weather_plan: dict[str, object],
    task_plan: dict[str, object],
) -> int | None:
    """Return a stable dropdown value when only storm enabled/disabled is known."""
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
    """Builds a human-readable label for a price provider source.

    Parameters:
        source (dict[str, object]): Provider data; expected keys include
            `FIELD_COMPANY_NAME`, `FIELD_NAME`, `FIELD_CID`, `FIELD_PLATFORM_COMPANY_ID`,
            `FIELD_COUNTRY`, and `FIELD_SYSTEM_REGION`. The function uses these fields
            (in that precedence order for the name) to compose the label.

    Returns:
        str: Label containing the provider name, the country/region in parentheses
        when available, and `#<company_id>` appended when `FIELD_PLATFORM_COMPANY_ID`
        is present and non-empty.
    """
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
    """Extracts a list of region parts from a price source's country or system region field.

    Parameters:
        source (dict[str, object]): A price source dictionary that may contain `FIELD_COUNTRY` or `FIELD_SYSTEM_REGION`.

    Returns:
        list[str]: A list of trimmed, non-empty region parts split on commas from the first available of `FIELD_COUNTRY` or `FIELD_SYSTEM_REGION`. Returns an empty list if neither field is present or is empty.
    """
    raw = source.get(FIELD_COUNTRY) or source.get(FIELD_SYSTEM_REGION)
    if raw in {None, ""}:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _price_source_matches_current(
    source: dict[str, object],
    company_id: object,
    region: object,
) -> bool:
    """Determine whether a price source corresponds to the given company id and (optionally) region.

    Parameters:
        source (dict[str, object]): Provider source payload containing at least a platform company id and optional region fields.
        company_id (object): Expected platform company id; compared to the source's platform company id using string equality.
        region (object): Optional region filter; when None or empty, only the company id is considered. Otherwise the string form of this value must be present in the source's derived regions.

    Returns:
        True if the source's platform company id equals `company_id` (string comparison) and `region` is empty or included in the source's regions, False otherwise.
    """
    if str(source.get(FIELD_PLATFORM_COMPANY_ID)) != str(company_id):
        return False
    if region in {None, ""}:
        return True
    return str(region) in _price_source_regions(source)


def _price_sources_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    """Extract valid electricity price provider entries from a payload.

    Parameters:
        payload (dict[str, object]): The device/coordinator payload that may contain a
            list of provider entries under the `PAYLOAD_PRICE_SOURCES` key.

    Returns:
        list[dict[str, object]]: A list of source dictionaries from `PAYLOAD_PRICE_SOURCES`
        where each entry is a dict and contains a non-empty `FIELD_PLATFORM_COMPANY_ID`
        and a non-empty country/region value (`FIELD_COUNTRY` or `FIELD_SYSTEM_REGION`).
    """
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
    """Determine whether dynamic electricity pricing is available for the given select entity.

    Returns:
        bool: `True` if dynamic pricing can be selected (the entity's price includes a non-empty provider id and a region, or the payload contains one or more valid price sources), `False` otherwise.
    """
    company_id = entity._price.get(FIELD_PLATFORM_COMPANY_ID)
    region = entity._price.get(FIELD_SYSTEM_REGION)
    if company_id not in {None, ""} and bool(region):
        return True
    return bool(_price_sources_from_payload(entity._payload))


def _price_mode_current_int(entity: JackerySelect) -> int | None:
    raw = entity._price.get(FIELD_DYNAMIC_OR_SINGLE)
    if raw is None:
        raw = task_plan_value(
            entity._task_plan,
            FIELD_DYNAMIC_OR_SINGLE,
            FIELD_PRICE_MODE,
        )
    if raw is None:
        work_mode = safe_int(entity._properties.get(FIELD_WORK_MODEL))
        if work_mode == 7:
            return 1
        if entity._price.get(FIELD_SINGLE_PRICE) is not None:
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
        """Send the chosen select option to the device coordinator and request a data refresh.

        Parameters:
            option (str): The option value to select.

        Raises:
            ConfigEntryAuthFailed: If authentication with the config entry has failed.
            HomeAssistantError: If the selection cannot be applied; preserved translation keys are re-raised unchanged, otherwise an actionable translation-keyed `HomeAssistantError` is raised.
        """
        try:
            await self.entity_description.select_fn(self, option)
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            _raise_select_action_error(self, "entity_action_failed", error=err)
        except Exception as err:
            _raise_select_action_error(self, "entity_action_failed", error=err)

    def _warn_unknown_once(self, value: Any) -> None:
        """Record and log an unmapped raw value once for this entity instance.

        If the entity description does not request unknown-value warnings or this value
        was already logged for this instance, this function does nothing. Otherwise it
        adds the value to the instance's warned set and emits a warning containing the
        description's warning kind and the raw value.

        Parameters:
            value (Any): The raw, unmapped value to record and report.
        """
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
    raw = entity._properties.get(FIELD_WORK_MODEL)
    if raw is None:
        raw = task_plan_value(entity._task_plan, FIELD_WORK_MODEL)
    if raw is None:
        mode_hint = safe_int(entity._price.get(FIELD_DYNAMIC_OR_SINGLE))
        if mode_hint == 1:
            return WORK_MODE_TO_OPTION[7]
        return None
    value = safe_int(raw)
    if value is None:
        return None
    option = WORK_MODE_TO_OPTION.get(value) or WORK_MODE_READ_ALIASES.get(value)
    if option is not None:
        return option
    entity._warn_unknown_once(value)
    return None


async def _work_mode_select(entity: JackerySelect, option: str) -> None:
    """Set the device's work mode based on a select option.

    Parameters:
        entity (JackerySelect): The select entity representing the device.
        option (str): The chosen select option label to map to a work mode.

    Raises:
        HomeAssistantError: If `option` is not a valid work-mode option (translation key `invalid_select_option`).
    """
    mode = _OPTION_TO_WORK_MODE.get(option)
    if mode is None:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_set_work_model(entity._device_id, mode)


def _temp_unit_current(entity: JackerySelect) -> str | None:
    val = safe_int(entity._properties.get(FIELD_TEMP_UNIT))
    if val is None:
        return None
    return TEMP_UNIT_TO_OPTION.get(val)


async def _temp_unit_select(entity: JackerySelect, option: str) -> None:
    """Set the device's temperature unit based on the selected option.

    Parameters:
        entity (JackerySelect): The select entity representing the device.
        option (str): One of the option keys defined in the entity's option map (mapped via `_OPTION_TO_TEMP_UNIT`).

    Raises:
        HomeAssistantError: If `option` is not a valid selection (translation key `invalid_select_option`).
    """
    if option not in _OPTION_TO_TEMP_UNIT:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    await entity.coordinator.async_set_temp_unit(
        entity._device_id,
        _OPTION_TO_TEMP_UNIT[option],
    )


def _island_auto_off_current(entity: JackerySelect) -> str | None:
    raw = entity._properties.get(FIELD_OFF_GRID_TIME)
    if raw is None:
        raw = task_plan_value(
            entity._task_plan,
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
    """Set the device's off-grid auto-off interval according to the chosen option.

    Parameters:
        entity (JackerySelect): The select entity representing the device.
        option (str): The selected option key; must be one of the module's auto-off option keys.

    Raises:
        HomeAssistantError: If `option` is not a valid auto-off option (`translation_key` = "invalid_select_option").

    Description:
        Converts the validated option into hours, then requests the coordinator to set the device's off-grid time in minutes (hours * 60).
    """
    if option not in _AUTO_OFF_OPTION_TO_HOURS:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    hours = _AUTO_OFF_OPTION_TO_HOURS[option]
    await entity.coordinator.async_set_off_grid_time(entity._device_id, hours * 60)


def _storm_minutes_current_value(entity: JackerySelect) -> int | None:
    current = _storm_minutes_value(
        entity._properties,
        entity._weather_plan,
        entity._task_plan,
    )
    if current is not None:
        return current
    return _storm_minutes_fallback(
        entity._properties,
        entity._weather_plan,
        entity._task_plan,
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
    """Set the storm-warning lead time from a select option of the form `min_<minutes>`.

    Parses `option`, which must match the pattern `min_<digits>`, converts the captured digits to an integer, and requests the coordinator to set that many storm-warning minutes for the entity's device.

    Parameters:
        entity (JackerySelect): The select entity instance.
        option (str): The chosen option string; must be `min_<minutes>` where `<minutes>` is a non-negative integer.

    Raises:
        HomeAssistantError: If `option` does not match the required `min_<minutes>` pattern (translation key `invalid_select_option`).
    """
    match = re.fullmatch(r"min_(\d+)", option)
    if not match:
        _raise_select_action_error(entity, "invalid_select_option", option=option)
    minutes = int(match.group(1))
    await entity.coordinator.async_set_storm_minutes(entity._device_id, minutes)


def _price_mode_current(entity: JackerySelect) -> str | None:
    mode = _price_mode_current_int(entity)
    if mode is None:
        return None
    option = PRICE_MODE_TO_OPTION.get(mode)
    if option is not None:
        return option
    entity._warn_unknown_once(mode)
    return None


async def _price_mode_select(entity: JackerySelect, option: str) -> None:
    """Handle a user selection to change the device's electricity price mode.

    Validates the chosen `option` and instructs the coordinator to set either dynamic or single-price mode. Raises a translatable selection error when the option is invalid or when dynamic pricing is unavailable for the device.

    Parameters:
        option (str): The selected price-mode option label.

    Raises:
        HomeAssistantError: with translation_key `"invalid_select_option"` if `option` is not a valid choice.
        HomeAssistantError: with translation_key `"dynamic_tariff_unavailable"` if the user selected dynamic pricing but dynamic pricing is not available for the device.
    """
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
        await entity.coordinator.async_set_price_mode_dynamic(entity._device_id)
    elif mode == 2:
        await entity.coordinator.async_set_price_mode_single(entity._device_id)


def _price_provider_options(entity: JackerySelect) -> list[str]:
    labels = [
        _price_source_label(source)
        for source in _price_sources_from_payload(entity._payload)
    ]
    current = entity.current_option
    if current and current not in labels:
        labels.append(current)
    return labels


def _price_provider_current(entity: JackerySelect) -> str | None:
    """Determine the human-readable label for the entity's currently selected electricity price provider.

    If the entity has no platform company id, returns `None`. If the current provider matches an entry in the payload, returns that entry's label; otherwise returns a fallback label constructed from the entity's stored company id, region, and company name.

    Returns:
        str | None: The provider label, or `None` when no company id is set.
    """
    company_id = entity._price.get(FIELD_PLATFORM_COMPANY_ID)
    region = entity._price.get(FIELD_SYSTEM_REGION)
    if company_id in {None, ""}:
        return None
    for source in _price_sources_from_payload(entity._payload):
        if _price_source_matches_current(source, company_id, region):
            return _price_source_label(source)
    return _price_source_label({
        FIELD_PLATFORM_COMPANY_ID: company_id,
        FIELD_COUNTRY: region,
        FIELD_COMPANY_NAME: entity._price.get(FIELD_COMPANY_NAME),
    })


async def _price_provider_select(entity: JackerySelect, option: str) -> None:
    """Select the electricity price provider that corresponds to the given option label and apply it to the device.

    Calls the coordinator to set the price source when `option` matches a provider label derived from the entity payload. If no matching provider is found, raises a translated `invalid_select_option` HomeAssistantError.

    Parameters:
        option (str): The provider label chosen from the entity's available options.
    """
    for source in _price_sources_from_payload(entity._payload):
        if _price_source_label(source) == option:
            await entity.coordinator.async_set_price_source(entity._device_id, source)
            return
    _raise_select_action_error(entity, "invalid_select_option", option=option)


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
)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create description-driven select entities."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[SelectEntity], entity: SelectEntity) -> None:
        """Append a select entity to the list if its unique ID has not already been added.

        Parameters:
            entities (list[SelectEntity]): Mutable list of select entities to potentially append to.
            entity (SelectEntity): Candidate select entity; will be appended only when its unique ID
                has not been seen. The function uses the module-level `seen_unique_ids`, the
                platform name `"select"`, and the module logger to enforce uniqueness.
        """
        append_unique_entity(
            entities,
            seen_unique_ids,
            entity,
            platform="select",
            logger=_LOGGER,
        )

    # Gating predicates per description key. Each predicate returns True when
    # the device is known to expose / accept the corresponding selector.
    def _gate(key: str, payload: dict[str, Any], supports_advanced: bool) -> bool:
        """Decide whether a selector (by key) should be exposed for a device based on its payload and advanced-support flag.

        Parameters:
            key (str): Selector identifier (e.g., "work_mode_select", "temp_unit_select", "auto_off_island_mode",
                "storm_warning_minutes_select", "electricity_price_mode", "electricity_price_provider").
            payload (dict[str, Any]): Device payload containing keys like properties, weather_plan, price, and price_sources.
            supports_advanced (bool): Whether the device reports advanced feature support.

        Returns:
            bool: `True` if the selector identified by `key` should be created for the device given the payload and
            `supports_advanced`, `False` otherwise.
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
        return False

    def _collect_entities() -> list[SelectEntity]:
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
