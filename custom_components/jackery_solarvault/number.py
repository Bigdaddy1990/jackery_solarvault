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
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .client import JackeryAuthError
from .const import (
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
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    DOMAIN,
    FIELD_CURRENCY,
    FIELD_CURRENCY_CODE,
    FIELD_DEFAULT_PW,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_ID,
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
    FIELD_SYSTEM_ID,
    FIELD_THIRD_PARTY_MQTT_PORT,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
)
from .coordinator import ACTION_WRITE_ERRORS
from .entity import (
    HTTP_COMMAND_SOURCES,
    HTTP_DATA_SOURCES,
    LAYER5_COMMAND_SOURCES,
    LAYER5_DATA_SOURCES,
    JackeryEntity,
    payload_properties_for_sources,
    property_data_sources,
)
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


_HOME_PAYLOAD_EVIDENCE_KEYS = frozenset({
    "autoStandby",
    "batInPw",
    "batOutPw",
    "batSoc",
    "defaultPw",
    "gridInPw",
    "isAutoStandby",
    "isFollowMeterPw",
    "maxGridStdPw",
    "maxInvStdPw",
    "maxIotNum",
    "maxOutPw",
    "pvPw",
    "swEps",
    "tempUnit",
    "workModel",
})
_PAYLOAD_HTTP_PROPERTIES = "http_properties"


def _has_home_payload_evidence(props: dict[str, Any]) -> bool:
    """Return True when props carry Home/System-body-only fields."""
    return any(key in props for key in _HOME_PAYLOAD_EVIDENCE_KEYS)


def _payload_has_home_payload_evidence(
    payload: dict[str, Any],
    props: dict[str, Any] | None = None,
) -> bool:
    """Return True when merged or raw payload props identify a Home/System body."""
    if props is not None and _has_home_payload_evidence(props):
        return True
    if isinstance(payload.get(PAYLOAD_SYSTEM), dict) and payload[PAYLOAD_SYSTEM]:
        return True
    for section in (PAYLOAD_PROPERTIES, _PAYLOAD_HTTP_PROPERTIES):
        raw = payload.get(section) or {}
        if isinstance(raw, dict) and _has_home_payload_evidence(raw):
            return True
    return False


def _is_portable_payload(
    payload: dict[str, Any],
    props: dict[str, Any] | None = None,
) -> bool:
    """Return True for Explorer/Portable payloads without Home/System evidence."""
    if _payload_has_home_payload_evidence(payload, props):
        return False
    for section in (PAYLOAD_DEVICE, PAYLOAD_DISCOVERY):
        meta = payload.get(section) or {}
        if (
            isinstance(meta, dict)
            and meta.get(PAYLOAD_DISCOVERY_SOURCE) == DISCOVERY_SOURCE_LEGACY_BIND_LIST
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


def _rounded_int(value: Any) -> int:  # noqa: ANN401, RUF105
    """Round a value accepted by Home Assistant as a number to the nearest integer.

    Parameters:
        value (Any): Input to parse as a numeric value.

    Returns:
        int: The input rounded to the nearest integer.

    Raises:
        HomeAssistantError: If the input cannot be parsed as a numeric value (message
        "invalid number value").
    """
    parsed = safe_float(value)
    if parsed is None:
        msg = "invalid number value"
        raise HomeAssistantError(msg)
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
    display_precision: int | None = None
    smali_field: str | None = None
    app_fields: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    command_sources: tuple[str, ...] = ()
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


# ---------------------------------------------------------------------------
# Setter helpers
# ---------------------------------------------------------------------------


def _wire_int(value: Any) -> int:  # noqa: ANN401, RUF105
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


def _wire_float(value: Any) -> float:  # noqa: ANN401, RUF105
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
    await coord.async_set_max_feed_grid(dev_id, 800 if parsed <= 800 else 2500)  # ruff:ignore[magic-value-comparison]


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
        if feed_limit is not None and feed_limit > 800:  # ruff:ignore[magic-value-comparison]
            return 2500.0
    max_out_int = safe_int(props.get(FIELD_MAX_OUT_PW))
    if max_out_int is None:
        max_out_int = 2500
    return 800.0 if max_out_int <= 800 else 2500.0  # ruff:ignore[magic-value-comparison]


def _max_feed_grid_allowed_values(payload: dict[str, Any]) -> tuple[float, ...]:
    """Jackery's app exposes feed-in as a binary 800/2500W selection."""
    if _max_feed_grid_dynamic_max(payload) <= 800:  # ruff:ignore[magic-value-comparison]
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
        native_min_value=0,
        native_max_value=10,
        native_step=0.01,
        source_keys=(FIELD_SINGLE_PRICE,),
        source_section=PAYLOAD_PRICE,
        setter=_set_single_price,
        command_sources=HTTP_COMMAND_SOURCES,
        dynamic_unit=_single_tariff_dynamic_unit,
        value_transform=_wire_float,
    ),
    JackeryNumberDescription(
        key="third_party_mqtt_port",
        translation_key="third_party_mqtt_port",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        native_min_value=1,
        native_max_value=65535,
        native_step=1,
        source_keys=(FIELD_THIRD_PARTY_MQTT_PORT,),
        source_section=PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
        setter=_set_third_party_mqtt_port,
        integer_value=True,
        display_precision=0,
    ),
    # --- Portable / Explorer powerstation numbers ---
    JackeryNumberDescription(
        key="portable_charge_power",
        translation_key="portable_charge_power",
        device_class=NumberDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=2000,
        native_step=100,
        source_keys=("csc",),
        setter=_set_portable_charge_power,
    ),
    JackeryNumberDescription(
        key="portable_energy_storage_charge_limit",
        translation_key="portable_energy_storage_charge_limit",
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        source_keys=("dt",),
        setter=_set_portable_energy_storage_charge_limit,
    ),
    JackeryNumberDescription(
        key="portable_auto_shutdown_time",
        translation_key="portable_auto_shutdown_time",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
        source_keys=("ast",),
        setter=_set_portable_auto_shutdown_time,
    ),
    JackeryNumberDescription(
        key="portable_ac_countdown",
        translation_key="portable_ac_countdown",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
        source_keys=("oact",),
        setter=_set_portable_ac_countdown,
    ),
    JackeryNumberDescription(
        key="portable_ac_output_delay",
        translation_key="portable_ac_output_delay",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=86340,
        native_step=60,
        source_keys=("acdt",),
        setter=_set_portable_ac_output_delay,
    ),
    JackeryNumberDescription(
        key="portable_custom_use_discharge_limit",
        translation_key="portable_custom_use_discharge_limit",
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        source_keys=("dl",),
        setter=_set_portable_custom_use_discharge_limit,
    ),
    JackeryNumberDescription(
        key="portable_custom_use_charge_limit",
        translation_key="portable_custom_use_charge_limit",
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        source_keys=("cl",),
        setter=_set_portable_custom_use_charge_limit,
    ),
    JackeryNumberDescription(
        key="portable_dc_countdown",
        translation_key="portable_dc_countdown",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
        source_keys=("odct",),
        setter=_set_portable_dc_countdown,
    ),
    JackeryNumberDescription(
        key="portable_dc_usb_countdown",
        translation_key="portable_dc_usb_countdown",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
        source_keys=("odcut",),
        setter=_set_portable_dc_usb_countdown,
    ),
    JackeryNumberDescription(
        key="portable_dc_car_countdown",
        translation_key="portable_dc_car_countdown",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
        source_keys=("odcct",),
        setter=_set_portable_dc_car_countdown,
    ),
    JackeryNumberDescription(
        key="portable_ac1_priority_soc",
        translation_key="portable_ac1_priority_soc",
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        source_keys=("oac1PrioSoc",),
        setter=_set_portable_ac1_priority_soc,
    ),
    JackeryNumberDescription(
        key="portable_ac2_priority_soc",
        translation_key="portable_ac2_priority_soc",
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        source_keys=("oac2PrioSoc",),
        setter=_set_portable_ac2_priority_soc,
    ),
    JackeryNumberDescription(
        key="portable_dc_priority_soc",
        translation_key="portable_dc_priority_soc",
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        source_keys=("odcPrioSoc",),
        setter=_set_portable_dc_priority_soc,
    ),
    JackeryNumberDescription(
        key="portable_bluetooth_sleep",
        translation_key="portable_bluetooth_sleep",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
        source_keys=("tmt",),
        setter=_set_portable_bluetooth_sleep,
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
        """Return the entity's current value."""  # noqa: D421, RUF105
        section = self._section()
        for key in self.entity_description.source_keys:
            val = section.get(key)
            if val is not None:
                fval = safe_float(val)
                if fval is not None and self.entity_description.integer_value:
                    return round(fval)
                return fval
        return self.entity_description.none_fallback

    @property
    def native_max_value(self) -> float:
        """Return the highest value the user can write."""  # noqa: D421, RUF105
        if self.entity_description.dynamic_max is not None:
            return self.entity_description.dynamic_max(
                self._payload_for_sources(self.entity_description.data_sources)
            )
        if self.entity_description.native_max_value is not None:
            return float(self.entity_description.native_max_value)
        return 0.0

    @property
    def native_unit_of_measurement(self) -> str | None:
        """The entity's unit of measurement, using a dynamic unit computed from the
        current payload when available.

        Returns:
            The unit of measurement string, or None if no unit is configured.
        """  # noqa: D205, RUF105
        if self.entity_description.dynamic_unit is not None:
            return self.entity_description.dynamic_unit(
                self._payload_for_sources(self.entity_description.data_sources)
            )
        return self.entity_description.native_unit_of_measurement

    @property
    def suggested_display_precision(self) -> int | None:
        """Return the suggested number of decimal places for display."""  # noqa: D421, RUF105
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
            return tuple(
                allowed(self._payload_for_sources(self.entity_description.data_sources))
            )
        return tuple(allowed)

    async def async_set_native_value(self, value: float) -> None:
        """Write the given native numeric value to the device, enforcing
        description-driven validation and invoking the configured setter.

        Validates the value against the description's min/max when `validate_range` is
        True and against discrete `allowed_values` when present. If a setter is
        configured, the native value is transformed with the description's
        `value_transform` and passed to the setter. Setter authentication failures are
        converted to `ConfigEntryAuthFailed`. If a `HomeAssistantError` raised by the
        setter already contains a `translation_key` it is re-raised; otherwise, the
        error is either raised as a translated action error when `raise_on_setter_error`
        is True or ignored. A coordinator refresh is always requested after the write
        attempt.

        Parameters:
            value (float): The native numeric value to write.

        Raises:
            ConfigEntryAuthFailed: If the setter reports an authentication failure.
            HomeAssistantError: For invalid range or allowed-value violations, or when
            `raise_on_setter_error` is True and the setter fails.
        """  # noqa: D205, RUF105
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
        except ACTION_WRITE_ERRORS as err:
            if self.entity_description.raise_on_setter_error:
                self._raise_action_error("entity_action_failed", error=err)
            _LOGGER.debug(
                "Ignoring optional Jackery number setter failure for %s/%s: %s",
                self._device_id,
                self.entity_description.key,
                err,
            )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def async_setup_entry(  # ruff:ignore[unused-async]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create description-driven number entities."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append(entities: list[NumberEntity], entity: NumberEntity) -> None:
        """Add a NumberEntity to the collection if its unique ID has not already been.

        seen.

        Parameters:
            entities (list[NumberEntity]): List to append the entity to when unique.
            entity (NumberEntity): The entity to add; duplicates (by unique ID) are
            ignored.
        """
        append_unique_entity(
            entities,
            seen_unique_ids,
            entity,
        )

    def _has_props(payload: dict[str, Any], *keys: str) -> bool:
        """Determine whether any of the given keys exist in the payload's properties.

        section.

        Parameters:
            payload (dict[str, Any]): Full device payload containing payload sections.
            *keys (str): Property keys to check for presence inside the
            `PAYLOAD_PROPERTIES` section.

        Returns:
            bool: `True` if at least one key is present in the properties section,
            `False` otherwise.
        """
        props = payload_properties_for_sources(payload)
        return any(k in props for k in keys)

    def _has_price_or_system(payload: dict[str, Any]) -> bool:
        """Determine whether the given device payload contains single-price data or a.

        system identifier.

        Parameters:
            payload (dict[str, Any]): Full device payload to inspect.

        Returns:
            bool: `true` if the payload's price section contains `FIELD_SINGLE_PRICE`
            or `FIELD_DYNAMIC_OR_SINGLE`, or the system section contains `FIELD_ID` or
            `FIELD_SYSTEM_ID`; `false` otherwise.
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
            p,
            FIELD_MAX_FEED_GRID,
            FIELD_MAX_GRID_STD_PW,
            FIELD_MAX_OUT_PW,
        ),
        "default_power_set": lambda p: _has_props(p, FIELD_MAX_OUT_PW),
        "single_tariff_price_set": _has_price_or_system,
        "third_party_mqtt_port": lambda _p: True,
    }

    def _collect_entities() -> list[NumberEntity]:
        """Collect JackeryNumber entities for devices whose payloads satisfy their
        gating predicates.

        Iterates coordinator data and instantiates a JackeryNumber for each entry in
        NUMBER_DESCRIPTIONS when the description has no predicate or its predicate
        returns True for the device payload.

        Returns:
            list[NumberEntity]: Instantiated number entities ready to be added to Home
            Assistant.
        """  # noqa: D205, RUF105
        entities: list[NumberEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload_properties_for_sources(payload)
            is_portable = _is_portable_payload(payload, props)
            for description in NUMBER_DESCRIPTIONS:
                description_is_portable = description.key.startswith("portable_")
                if description_is_portable != is_portable:
                    continue
                if description_is_portable:
                    _append(entities, JackeryNumber(coordinator, dev_id, description))
                    continue
                predicate = gating.get(description.key)
                if predicate is None or predicate(payload):
                    _append(entities, JackeryNumber(coordinator, dev_id, description))
        return entities

    last_signature: tuple[Any, ...] = ()

    @callback
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
