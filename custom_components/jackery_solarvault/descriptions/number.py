"""Number descriptions for Jackery SolarVault integration."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..coordinator import JackerySolarVaultCoordinator
    from ..number import JackeryNumber as JackeryNumberEntity

from homeassistant.components.number import NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.exceptions import HomeAssistantError

from ..const import (
    ACTION_ID_PORTABLE_AC_COUNTDOWN,
    ACTION_ID_PORTABLE_AC_OUTPUT_DELAY,
    ACTION_ID_PORTABLE_AUTO_SHUTDOWN_TIME,
    ACTION_ID_PORTABLE_BLUETOOTH_SLEEP,
    ACTION_ID_PORTABLE_DC_CAR_COUNTDOWN,
    ACTION_ID_PORTABLE_DC_COUNTDOWN,
    ACTION_ID_PORTABLE_DC_USB_COUNTDOWN,
    ACTION_ID_PORTABLE_ENERGY_STORAGE_CHARGE_LIMIT,
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SOC,
    ACTION_ID_PORTABLE_SET_CHARGE_POWER,
    DEFAULT_NULL_SEMANTICS,
    FIELD_CURRENCY,
    FIELD_CURRENCY_CODE,
    FIELD_DEFAULT_PW,
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
    FIELD_THIRD_PARTY_MQTT_PORT,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
)
from ..entity import (
    HTTP_COMMAND_SOURCES,
    HTTP_DATA_SOURCES,
    LAYER5_COMMAND_SOURCES,
    LAYER5_DATA_SOURCES,
    payload_properties_for_sources,
)
from ..util import safe_int

_LOW_FEED_GRID_LIMIT_W = 800
_HIGH_FEED_GRID_LIMIT_W = 2500


def _default_number_value(entity: JackeryNumberEntity) -> float | None:
    """Read the first configured source key for a compatibility description."""
    description = entity.entity_description
    for key in description.source_keys:
        raw = entity.section.get(key)
        if raw is not None:
            return safe_float(raw)
    return description.none_fallback


@dataclass(frozen=True, kw_only=True)
class JackeryNumberDescription(NumberEntityDescription):
    """Describes a Jackery number entity."""

    value_fn: Callable[[JackeryNumberEntity], float | None] = _default_number_value
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
    integer_value: bool = False
    display_precision: int | None = None
    smali_field: str | None = None
    app_fields: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    command_sources: tuple[str, ...] = ()
    device_registry_role: str = "head"
    null_semantics: str = DEFAULT_NULL_SEMANTICS
    recorder_allowed: bool = True
    ha_derived: bool = False

    def __post_init__(self) -> None:
        """Resolve field, read-source and command-source capabilities."""
        app_fields = self.app_fields or self.source_keys
        if not app_fields and self.smali_field:
            app_fields = (self.smali_field,)
        object.__setattr__(self, "app_fields", app_fields)
        if not self.data_sources:
            if self.source_section == PAYLOAD_PROPERTIES:
                sources = property_data_sources(
                    *app_fields,
                    layer5_proven=self.setter is not None,
                )
            elif self.source_section == PAYLOAD_THIRD_PARTY_MQTT_CONFIG:
                sources = LAYER5_DATA_SOURCES
            else:
                sources = HTTP_DATA_SOURCES
            object.__setattr__(self, "data_sources", sources)
        if not self.command_sources and self.setter is not None:
            object.__setattr__(self, "command_sources", LAYER5_COMMAND_SOURCES)


def first_nonblank_int(value: object) -> int | None:
    """Convert the first non-blank numeric value to an integer."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(float(s))
        except TypeError, ValueError:
            return None
    return None


def property_data_sources(*fields: str, layer5_proven: bool = False) -> tuple[str, ...]:
    """Build property-source descriptors for the supplied fields."""
    sources = []
    for field in fields:
        if layer5_proven:
            sources.append(f"layer5:{field}")
        else:
            sources.append(f"http:{field}")
    return tuple(sources)


def safe_float(v: object) -> float | None:
    """Convert one value to float, returning None when conversion fails."""
    if v is None:
        return None
    try:
        return float(v) if isinstance(v, (bool, int, float, str)) else None
    except TypeError, ValueError:
        return None


# ---------------------------------------------------------------------------
# Setter and dynamic-value helpers (migrated from number.py)
# ---------------------------------------------------------------------------


def _wire_int(value: object) -> int:
    """Parse the given value into an integer for coordinator setter calls.

    Parameters:
        value: Input to parse; may be an int, numeric string, or other value that can
        represent an integer.

    Returns:
        int: The parsed integer.

    Raises:
        HomeAssistantError: If the input cannot be interpreted as an integer (error
        message "invalid number value").
    """
    parsed = first_nonblank_int(value)
    if parsed is None:
        msg = "invalid number value"
        raise HomeAssistantError(msg)
    return parsed


def _wire_float(value: object) -> float:
    """Parse an arbitrary input into a float suitable for coordinator setter calls.

    Parameters:
        value (Any): The input value to parse (e.g., numeric types or numeric strings).

    Returns:
        float: The parsed floating-point value.

    Raises:
        HomeAssistantError: If the input cannot be parsed as a float (error message:
        "invalid number value").
    """
    parsed = safe_float(value)
    if parsed is None:
        msg = "invalid number value"
        raise HomeAssistantError(msg)
    return parsed


async def _set_soc_charge(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the SOC charge limit on a device."""
    await coord.async_set_soc_limits(dev_id, charge_limit=_wire_int(value))


async def _set_soc_discharge(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the SOC discharge limit on a device."""
    await coord.async_set_soc_limits(dev_id, discharge_limit=_wire_int(value))


async def _set_max_feed_grid(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the maximum grid feed-in power on a device."""
    parsed = _wire_int(value)
    await coord.async_set_max_feed_grid(
        dev_id,
        _LOW_FEED_GRID_LIMIT_W
        if parsed <= _LOW_FEED_GRID_LIMIT_W
        else _HIGH_FEED_GRID_LIMIT_W,
    )


async def _set_max_output_power(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the maximum output power on a device."""
    await coord.async_set_max_output_power(dev_id, _wire_int(value))


async def _set_default_power(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the default-load power preference on a device."""
    await coord.async_set_default_power(dev_id, _wire_int(value))


async def _set_single_price(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the device's single-tariff electricity price.

    The price value must be expressed in the device's current currency/unit and match
    the entity's unit of measurement.

    Parameters:
        value (float): Price to set, in the device's currency/unit.
    """
    await coord.async_set_single_price(dev_id, value)


async def _set_third_party_mqtt_port(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Update the device's third-party MQTT broker port in the coordinator's.

    configuration.

    Parameters:
        coord (JackerySolarVaultCoordinator): Coordinator used to apply the
        configuration change.
        dev_id (str): Identifier of the target device.
        value (float): Port number; converted to `int` before being written.
    """
    await coord.async_update_third_party_mqtt_config(
        dev_id,
        {FIELD_THIRD_PARTY_MQTT_PORT: int(value)},
    )


# --- Portable / Explorer powerstation number setters ---


async def _async_portable_set_number(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    *,
    action_id: int,
    field: str,
    value: float,
) -> None:
    """Push an integer field write to a portable Explorer device."""
    await coord.async_portable_set_number(
        dev_id,
        action_id=action_id,
        field=field,
        value=int(value),
    )


async def _set_portable_charge_power(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the charge power limit on a portable Explorer device (msgId=38)."""
    await _async_portable_set_number(
        coord,
        dev_id,
        action_id=ACTION_ID_PORTABLE_SET_CHARGE_POWER,
        field="csc",
        value=value,
    )


async def _set_portable_energy_storage_charge_limit(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the energy storage charge limit on a portable Explorer device (msgId=31)."""
    await _async_portable_set_number(
        coord,
        dev_id,
        action_id=ACTION_ID_PORTABLE_ENERGY_STORAGE_CHARGE_LIMIT,
        field="dt",
        value=value,
    )


async def _set_portable_auto_shutdown_time(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the auto-shutdown time on a portable Explorer device (msgId=19)."""
    await _async_portable_set_number(
        coord,
        dev_id,
        action_id=ACTION_ID_PORTABLE_AUTO_SHUTDOWN_TIME,
        field="ast",
        value=value,
    )


async def _set_portable_ac_countdown(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the AC output countdown on a portable Explorer device (msgId=34)."""
    await _async_portable_set_number(
        coord,
        dev_id,
        action_id=ACTION_ID_PORTABLE_AC_COUNTDOWN,
        field="oact",
        value=value,
    )


async def _set_portable_ac_output_delay(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the AC output delay-open time on a portable device (msgId=41).

    App stores ``acdt`` as seconds (HH:MM picker, hour*3600 + minute*60), so the
    value is a whole number of seconds in ``[0, 86340]`` with a 60-second step.
    """
    await _async_portable_set_number(
        coord,
        dev_id,
        action_id=ACTION_ID_PORTABLE_AC_OUTPUT_DELAY,
        field="acdt",
        value=value,
    )


async def _set_portable_custom_use_discharge_limit(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the custom-use lower (discharge) bound ``dl`` on a portable device.

    Ships together with the upper bound and derived back-off (msgId=33).
    """
    await coord.async_portable_set_custom_use_battery(
        dev_id,
        discharge_limit=int(value),
    )


async def _set_portable_custom_use_charge_limit(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the custom-use upper (charge) bound ``cl`` on a portable device.

    Ships together with the lower bound and derived back-off (msgId=33).
    """
    await coord.async_portable_set_custom_use_battery(
        dev_id,
        charge_limit=int(value),
    )


async def _set_portable_dc_countdown(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the DC output countdown on a portable Explorer device (msgId=35)."""
    await _async_portable_set_number(
        coord,
        dev_id,
        action_id=ACTION_ID_PORTABLE_DC_COUNTDOWN,
        field="odct",
        value=value,
    )


async def _set_portable_dc_usb_countdown(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the DC USB output countdown on a portable Explorer device (msgId=36)."""
    await _async_portable_set_number(
        coord,
        dev_id,
        action_id=ACTION_ID_PORTABLE_DC_USB_COUNTDOWN,
        field="odcut",
        value=value,
    )


async def _set_portable_dc_car_countdown(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the DC car output countdown on a portable Explorer device (msgId=37)."""
    await _async_portable_set_number(
        coord,
        dev_id,
        action_id=ACTION_ID_PORTABLE_DC_CAR_COUNTDOWN,
        field="odcct",
        value=value,
    )


async def _set_portable_ac1_priority_soc(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the AC1 output-priority SOC threshold on a portable device (msgId=49)."""
    await _async_portable_set_number(
        coord,
        dev_id,
        action_id=ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SOC,
        field="oac1PrioSoc",
        value=value,
    )


async def _set_portable_ac2_priority_soc(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the AC2 output-priority SOC threshold on a portable device (msgId=49)."""
    await _async_portable_set_number(
        coord,
        dev_id,
        action_id=ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SOC,
        field="oac2PrioSoc",
        value=value,
    )


async def _set_portable_dc_priority_soc(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the DC output-priority SOC threshold on a portable device (msgId=49)."""
    await _async_portable_set_number(
        coord,
        dev_id,
        action_id=ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SOC,
        field="odcPrioSoc",
        value=value,
    )


async def _set_portable_bluetooth_sleep(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the Bluetooth module sleep time on a portable Explorer device (msgId=44)."""
    await _async_portable_set_number(
        coord,
        dev_id,
        action_id=ACTION_ID_PORTABLE_BLUETOOTH_SLEEP,
        field="tmt",
        value=value,
    )


# ---------------------------------------------------------------------------
# Dynamic-value helpers
# ---------------------------------------------------------------------------


def _max_feed_grid_dynamic_max(payload: dict[str, Any]) -> float:
    """Return the feed-in choices exposed by the SolarVault app."""
    props = payload_properties_for_sources(payload)
    for key in (FIELD_MAX_FEED_GRID, FIELD_MAX_GRID_STD_PW):
        feed_limit = safe_int(props.get(key))
        if feed_limit is not None and feed_limit > _LOW_FEED_GRID_LIMIT_W:
            return float(_HIGH_FEED_GRID_LIMIT_W)
    max_out_int = safe_int(props.get(FIELD_MAX_OUT_PW))
    if max_out_int is None:
        max_out_int = _HIGH_FEED_GRID_LIMIT_W
    return (
        float(_LOW_FEED_GRID_LIMIT_W)
        if max_out_int <= _LOW_FEED_GRID_LIMIT_W
        else float(_HIGH_FEED_GRID_LIMIT_W)
    )


def _max_feed_grid_allowed_values(payload: dict[str, Any]) -> tuple[float, ...]:
    """Jackery's app exposes feed-in as a binary 800/2500W selection."""
    low = float(_LOW_FEED_GRID_LIMIT_W)
    if _max_feed_grid_dynamic_max(payload) <= low:
        return (low,)
    return (low, float(_HIGH_FEED_GRID_LIMIT_W))


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
# Number description tuples (migrated from number.py)
# ---------------------------------------------------------------------------

NUMBER_DESCRIPTIONS: tuple[JackeryNumberDescription, ...] = (
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="soc_charge_limit_set",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="soc_charge_limit_set",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.SLIDER,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=100,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(
            lambda e: (
                section := e.section,
                safe_float(section.get(FIELD_SOC_CHG_LIMIT))
                or safe_float(section.get(FIELD_SOC_CHARGE_LIMIT)),
            )[1]
        ),
        setter=_set_soc_charge,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="soc_discharge_limit_set",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="soc_discharge_limit_set",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.SLIDER,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=100,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(
            lambda e: (
                section := e.section,
                safe_float(section.get(FIELD_SOC_DISCHG_LIMIT))
                or safe_float(section.get(FIELD_SOC_DISCHARGE_LIMIT)),
            )[1]
        ),
        setter=_set_soc_discharge,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="max_output_power_set",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="max_output_power_set",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.SLIDER,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=2500,
        # pyrefly: ignore [unexpected-keyword]
        native_step=10,
        value_fn=(
            lambda e: (
                section := e.section,
                safe_float(section.get(FIELD_MAX_OUT_PW)),
            )[1]
        ),
        setter=_set_max_output_power,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="max_feed_grid",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="max_feed_grid",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.SLIDER,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=800,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=2500,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1700,
        value_fn=(
            lambda e: (
                section := e.section,
                safe_float(section.get(FIELD_MAX_FEED_GRID))
                or safe_float(section.get(FIELD_MAX_GRID_STD_PW)),
            )[1]
        ),
        setter=_set_max_feed_grid,
        dynamic_max=_max_feed_grid_dynamic_max,
        allowed_values=_max_feed_grid_allowed_values,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="default_power_set",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="default_power_set",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.SLIDER,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=200,
        # pyrefly: ignore [unexpected-keyword]
        native_step=10,
        value_fn=(
            lambda e: (
                section := e.section,
                (safe_float(section.get(FIELD_DEFAULT_PW))) or 0.0,
            )[1]
        ),
        setter=_set_default_power,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="single_tariff_price_set",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="single_tariff_price_set",
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.BOX,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=10,
        # pyrefly: ignore [unexpected-keyword]
        native_step=0.01,
        device_registry_role="system",
        value_fn=(
            lambda e: (
                section := e.section,
                safe_float(section.get(FIELD_SINGLE_PRICE)),
            )[1]
        ),
        setter=_set_single_price,
        dynamic_unit=_single_tariff_dynamic_unit,
        value_transform=_wire_float,
        command_sources=HTTP_COMMAND_SOURCES,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="third_party_mqtt_port",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="third_party_mqtt_port",
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.BOX,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=1,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=65535,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        integer_value=True,
        display_precision=0,
        value_fn=lambda e: (
            config := e.coordinator.third_party_mqtt_config_plaintext(e.device_id),
            value := safe_float(config.get(FIELD_THIRD_PARTY_MQTT_PORT)),
            round(value) if value is not None else None,
        )[2],
        setter=_set_third_party_mqtt_port,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_charge_power",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_charge_power",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfPower.WATT,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.SLIDER,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=2000,
        # pyrefly: ignore [unexpected-keyword]
        native_step=100,
        value_fn=(lambda e: (section := e.section, safe_float(section.get("csc")))[1]),
        setter=_set_portable_charge_power,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_energy_storage_charge_limit",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_energy_storage_charge_limit",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.SLIDER,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=100,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(lambda e: (section := e.section, safe_float(section.get("dt")))[1]),
        setter=_set_portable_energy_storage_charge_limit,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_auto_shutdown_time",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_auto_shutdown_time",
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.BOX,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=1440,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(lambda e: (section := e.section, safe_float(section.get("ast")))[1]),
        setter=_set_portable_auto_shutdown_time,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_ac_countdown",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_ac_countdown",
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.BOX,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=1440,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(lambda e: (section := e.section, safe_float(section.get("oact")))[1]),
        setter=_set_portable_ac_countdown,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_ac_output_delay",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_ac_output_delay",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=UnitOfTime.SECONDS,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.BOX,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=86340,
        # pyrefly: ignore [unexpected-keyword]
        native_step=60,
        value_fn=(lambda e: (section := e.section, safe_float(section.get("acdt")))[1]),
        setter=_set_portable_ac_output_delay,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_custom_use_discharge_limit",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_custom_use_discharge_limit",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.SLIDER,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=100,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(lambda e: (section := e.section, safe_float(section.get("dl")))[1]),
        setter=_set_portable_custom_use_discharge_limit,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_custom_use_charge_limit",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_custom_use_charge_limit",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.SLIDER,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=100,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(lambda e: (section := e.section, safe_float(section.get("cl")))[1]),
        setter=_set_portable_custom_use_charge_limit,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_dc_countdown",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_dc_countdown",
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.BOX,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=1440,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(lambda e: (section := e.section, safe_float(section.get("odct")))[1]),
        setter=_set_portable_dc_countdown,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_dc_usb_countdown",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_dc_usb_countdown",
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.BOX,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=1440,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(
            lambda e: (section := e.section, safe_float(section.get("odcut")))[1]
        ),
        setter=_set_portable_dc_usb_countdown,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_dc_car_countdown",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_dc_car_countdown",
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.BOX,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=1440,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(
            lambda e: (section := e.section, safe_float(section.get("odcct")))[1]
        ),
        setter=_set_portable_dc_car_countdown,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_ac1_priority_soc",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_ac1_priority_soc",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.SLIDER,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=100,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(
            lambda e: (section := e.section, safe_float(section.get("oac1PrioSoc")))[1]
        ),
        setter=_set_portable_ac1_priority_soc,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_ac2_priority_soc",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_ac2_priority_soc",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.SLIDER,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=100,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(
            lambda e: (section := e.section, safe_float(section.get("oac2PrioSoc")))[1]
        ),
        setter=_set_portable_ac2_priority_soc,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_dc_priority_soc",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_dc_priority_soc",
        # pyrefly: ignore [unexpected-keyword]
        native_unit_of_measurement=PERCENTAGE,
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.SLIDER,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=100,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(
            lambda e: (section := e.section, safe_float(section.get("odcPrioSoc")))[1]
        ),
        setter=_set_portable_dc_priority_soc,
    ),
    JackeryNumberDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="portable_bluetooth_sleep",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="portable_bluetooth_sleep",
        # pyrefly: ignore [unexpected-keyword]
        mode=NumberMode.BOX,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.CONFIG,
        # pyrefly: ignore [unexpected-keyword]
        native_min_value=0,
        # pyrefly: ignore [unexpected-keyword]
        native_max_value=1440,
        # pyrefly: ignore [unexpected-keyword]
        native_step=1,
        value_fn=(lambda e: (section := e.section, safe_float(section.get("tmt")))[1]),
        setter=_set_portable_bluetooth_sleep,
    ),
)
