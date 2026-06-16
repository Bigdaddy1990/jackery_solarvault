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

from .client.api import JackeryAuthError
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
from .entity import JackeryEntity
from .entity_contract import DEFAULT_LIVE_SOURCES, DEFAULT_NULL_SEMANTICS
from .exceptions import ACTION_WRITE_ERRORS
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


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


def _rounded_int(value: Any) -> int:  # noqa: ANN401
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
    data_sources: tuple[str, ...] = DEFAULT_LIVE_SOURCES
    null_semantics: str = DEFAULT_NULL_SEMANTICS
    recorder_allowed: bool = True
    ha_derived: bool = False


# ---------------------------------------------------------------------------
# Setter helpers
# ---------------------------------------------------------------------------


def _wire_int(value: Any) -> int:  # noqa: ANN401
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


def _wire_float(value: Any) -> float:  # noqa: ANN401
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
    """Set the state-of-charge (SOC) charge limit for the specified device.

    Parameters:
        dev_id (str): Identifier of the target device.
        value (float): Desired SOC charge limit as a percentage; converted to an
        integer before sending to the coordinator.
    """
    await coord.async_set_soc_limits(dev_id, charge_limit=_wire_int(value))


async def _set_soc_discharge(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the state-of-charge discharge limit (percentage) for the specified device.

    Parameters:
        dev_id (str): Identifier of the target device.
        value (float): Desired discharge limit as a percentage; values are converted to
        an integer before being sent.
    """
    await coord.async_set_soc_limits(dev_id, discharge_limit=_wire_int(value))


async def _set_max_feed_grid(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the device maximum grid feed-in to either 800 W or 2500 W based on the.

    provided value.

    Parameters:
        value (float): Requested feed-in indicator; values less than or equal to 800
        select 800, otherwise 2500.
    """
    parsed = _wire_int(value)
    await coord.async_set_max_feed_grid(dev_id, 800 if parsed <= 800 else 2500)  # noqa: PLR2004


async def _set_max_output_power(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the device's maximum output power via the coordinator.

    Parameters:
        coord (JackerySolarVaultCoordinator): Coordinator responsible for device
        communication.
        dev_id (str): Device identifier.
        value (float): Desired maximum output power in watts; will be converted to an
        integer before sending.

    Raises:
        HomeAssistantError: If `value` cannot be parsed as a valid number.
    """
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

    Parameters:
        coord (JackerySolarVaultCoordinator): Coordinator that performs the write
        operation.
        dev_id (str): Identifier of the target device.
        value (float): Price value to write, expressed in the device's configured
        currency unit.
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


async def _set_portable_charge_power(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the charge power limit on a portable Explorer device (msgId=38)."""
    await coord.async_portable_set_number(
        dev_id,
        action_id=38,
        field="rc",
        value=int(value),
    )


async def _set_portable_energy_storage_charge_limit(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the energy storage charge limit on a portable Explorer device (msgId=31)."""
    await coord.async_portable_set_number(
        dev_id,
        action_id=31,
        field="cl",
        value=int(value),
    )


async def _set_portable_auto_shutdown_time(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the auto-shutdown time on a portable Explorer device (msgId=19)."""
    await coord.async_portable_set_number(
        dev_id,
        action_id=19,
        field="dt",
        value=int(value),
    )


async def _set_portable_ac_countdown(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the AC output countdown on a portable Explorer device (msgId=34)."""
    await coord.async_portable_set_number(
        dev_id,
        action_id=34,
        field="acdt",
        value=int(value),
    )


async def _set_portable_dc_countdown(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the DC output countdown on a portable Explorer device (msgId=35)."""
    await coord.async_portable_set_number(
        dev_id,
        action_id=35,
        field="odct",
        value=int(value),
    )


async def _set_portable_dc_usb_countdown(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the DC USB output countdown on a portable Explorer device (msgId=36)."""
    await coord.async_portable_set_number(
        dev_id,
        action_id=36,
        field="odcu",
        value=int(value),
    )


async def _set_portable_dc_car_countdown(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the DC car output countdown on a portable Explorer device (msgId=37)."""
    await coord.async_portable_set_number(
        dev_id,
        action_id=37,
        field="idc",
        value=int(value),
    )


async def _set_portable_output_priority_soc(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the output priority SOC threshold on a portable Explorer device.

    (msgId=49).
    """
    await coord.async_portable_set_number(
        dev_id,
        action_id=49,
        field="pss",
        value=int(value),
    )


async def _set_portable_bluetooth_sleep(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the Bluetooth module sleep time on a portable Explorer device (msgId=44)."""
    await coord.async_portable_set_number(
        dev_id,
        action_id=44,
        field="ast",
        value=int(value),
    )


async def _set_portable_output_priority(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: float,
) -> None:
    """Set the output priority mode on a portable Explorer device (msgId=48)."""
    await coord.async_portable_set_number(
        dev_id,
        action_id=48,
        field="outPrio",
        value=int(value),
    )


# ---------------------------------------------------------------------------
# Dynamic-value helpers
# ---------------------------------------------------------------------------


def _max_feed_grid_dynamic_max(payload: dict[str, Any]) -> float:
    """Compute the dynamic maximum feed-grid power value for a device payload.

    Determines whether the device should expose a maximum feed-in of 800.0 or 2500.0
    watts.
    Priority:
    - If either `FIELD_MAX_FEED_GRID` or `FIELD_MAX_GRID_STD_PW` in payload properties
    is greater than 800, returns 2500.0.
    - If the device model code equals "3002" in `PAYLOAD_DEVICE` or
    `PAYLOAD_DISCOVERY`, returns 2500.0.
    - Otherwise, uses `FIELD_MAX_OUT_PW` from properties (defaults to 2500 when
    missing): returns 800.0 if that value is less than or equal to 800, otherwise
    2500.0.

    Parameters:
        payload (dict[str, Any]): Full device payload as received from the coordinator.

    Returns:
        float: Either 800.0 or 2500.0 representing the dynamic maximum feed-grid power.
    """
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
    """Return the discrete allowed feed-in wattage choices supported by the device.

    Parameters:
        payload (dict[str, Any]): Full device payload used to derive device
        capabilities.

    Returns:
        tuple[float, ...]: Allowed feed-in values; either `(800.0,)` when the device's
        dynamic max is 800 or less, otherwise `(800.0, 2500.0)`.
    """
    if _max_feed_grid_dynamic_max(payload) <= 800:  # noqa: PLR2004
        return (800.0,)
    return (800.0, 2500.0)


def _single_tariff_dynamic_unit(payload: dict[str, Any]) -> str:
    """Determine the currency symbol or currency code to use for the single-tariff.

    price.

    Checks the payload's price section and returns the first available value from
    FIELD_SINGLE_CURRENCY, FIELD_CURRENCY, FIELD_SINGLE_CURRENCY_CODE, or
    FIELD_CURRENCY_CODE. Defaults to "€" when none are present.

    Parameters:
        payload (dict[str, Any]): Device payload containing the price subsection.

    Returns:
        str: Currency symbol or code for the single-tariff price (defaults to "€").
    """
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
        value_transform=_wire_float,
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
        icon="mdi:battery-charging-wireless",
        native_min_value=0,
        native_max_value=2000,
        native_step=100,
        source_keys=("rc",),
        setter=_set_portable_charge_power,
    ),
    JackeryNumberDescription(
        key="portable_energy_storage_charge_limit",
        translation_key="portable_energy_storage_charge_limit",
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:battery-arrow-up",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        source_keys=("cl",),
        setter=_set_portable_energy_storage_charge_limit,
    ),
    JackeryNumberDescription(
        key="portable_auto_shutdown_time",
        translation_key="portable_auto_shutdown_time",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer-cog",
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
        source_keys=("dt",),
        setter=_set_portable_auto_shutdown_time,
    ),
    JackeryNumberDescription(
        key="portable_ac_countdown",
        translation_key="portable_ac_countdown",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer",
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
        source_keys=("acdt",),
        setter=_set_portable_ac_countdown,
    ),
    JackeryNumberDescription(
        key="portable_dc_countdown",
        translation_key="portable_dc_countdown",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer",
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
        icon="mdi:timer",
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
        source_keys=("odcu",),
        setter=_set_portable_dc_usb_countdown,
    ),
    JackeryNumberDescription(
        key="portable_dc_car_countdown",
        translation_key="portable_dc_car_countdown",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer",
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
        source_keys=("idc",),
        setter=_set_portable_dc_car_countdown,
    ),
    JackeryNumberDescription(
        key="portable_output_priority_soc",
        translation_key="portable_output_priority_soc",
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:battery-arrow-down",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        source_keys=("pss",),
        setter=_set_portable_output_priority_soc,
    ),
    JackeryNumberDescription(
        key="portable_bluetooth_sleep",
        translation_key="portable_bluetooth_sleep",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:bluetooth",
        native_min_value=0,
        native_max_value=1440,
        native_step=1,
        source_keys=("ast",),
        setter=_set_portable_bluetooth_sleep,
    ),
    JackeryNumberDescription(
        key="portable_output_priority",
        translation_key="portable_output_priority",
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:sort-bool-descending",
        native_min_value=0,
        native_max_value=10,
        native_step=1,
        source_keys=("outPrio",),
        setter=_set_portable_output_priority,
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
        """Construct and raise a localized HomeAssistantError tied to this entity.

        Parameters:
            translation_key (str): Translation key used to look up the localized error
            message.
            **placeholders (object): Additional placeholder values to include in the
            translation placeholders; each value will be converted to a string.

        Raises:
            HomeAssistantError: An error with `translation_domain`, `translation_key`,
            and `translation_placeholders` populated (includes `entity` and `device_id`
            plus provided placeholders).
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
        """Read the configured payload section (properties/price/...)."""
        return self._payload.get(self.entity_description.source_section) or {}

    @property
    def native_value(self) -> float | None:
        """Determine the entity's current native value by scanning configured source.

        keys in the selected payload section.

        Checks each key in `entity_description.source_keys` in order and parses the
        first non-`None` value with `safe_float`. If `entity_description.integer_value`
        is true, the parsed value is rounded to the nearest integer and returned as a
        `float`. If no source key yields a usable value, returns
        `entity_description.none_fallback`.

        Returns:
            The found native value as a `float` (rounded to an integer when
            configured), or `entity_description.none_fallback` (which may be `None`).
        """
        section = self._section()
        for key in self.entity_description.source_keys:
            val = section.get(key)
            if val is not None:
                fval = safe_float(val)
                if fval is not None and self.entity_description.integer_value:
                    return float(round(fval))
                return fval
        return self.entity_description.none_fallback

    @property
    def native_max_value(self) -> float:
        """Determine the maximum writable native value for this entity.

        Returns:
            The maximum native value allowed for writes as a float.
        """
        if self.entity_description.dynamic_max is not None:
            return self.entity_description.dynamic_max(self._payload)
        if self.entity_description.native_max_value is not None:
            return float(self.entity_description.native_max_value)
        return 0.0

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Get the unit of measurement for this entity.

        If the description provides a dynamic_unit callable, its result for the current
        payload is returned; otherwise the description's static
        native_unit_of_measurement is returned.

        Returns:
            str | None: The unit of measurement, or `None` if not set.
        """
        if self.entity_description.dynamic_unit is not None:
            return self.entity_description.dynamic_unit(self._payload)
        return self.entity_description.native_unit_of_measurement

    @property
    def suggested_display_precision(self) -> int | None:
        """Return the suggested number of decimal places for display."""
        return self.entity_description.display_precision

    def _allowed_values(self) -> tuple[float, ...]:
        """Determine the discrete native values allowed for this entity based on its.

        description and current payload.

        Returns:
                A tuple of allowed native values. Returns an empty tuple when there is
                no restriction.
        """
        allowed = self.entity_description.allowed_values
        if allowed is None:
            return ()
        if callable(allowed):
            return tuple(allowed(self._payload))
        return tuple(allowed)

    async def async_set_native_value(self, value: float) -> None:
        """Validate a native numeric value, forward it to the device via the.

        description's setter, and refresh coordinator data.

        Performs parsing and range checks against the entity's native min/max, enforces
        discrete allowed values when present, applies the description's
        value_transform, and invokes the description.setter to write the prepared
        value. On parsing or range failure it raises an entity action error with
        translation key "invalid_number_range"; when the value is not one of the
        allowed discrete choices it raises "invalid_number_allowed_values".
        Authentication failures raised by the setter are propagated as
        ConfigEntryAuthFailed. HomeAssistantError exceptions that already carry
        translation metadata are re-raised; other setter failures are either converted
        to an entity action error with translation key "entity_action_failed" (if the
        description requests raising) or are logged and ignored. The coordinator is
        always asked to refresh after the write attempt.

        Parameters:
            value (float): The target native value to set for the entity.
        """
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
        await self.coordinator.async_request_refresh()


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def async_setup_entry(  # noqa: RUF029
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
            platform="number",
            logger=_LOGGER,
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
        props = payload.get(PAYLOAD_PROPERTIES) or {}
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
        "third_party_mqtt_port": lambda p: (
            FIELD_THIRD_PARTY_MQTT_PORT
            in (p.get(PAYLOAD_THIRD_PARTY_MQTT_CONFIG) or {})
        ),
    }

    def _collect_entities() -> list[NumberEntity]:
        """Build a list of number entities for devices whose payloads satisfy their.

        gating predicates.

        Iterates coordinator.data and, for each device and each description in
        NUMBER_DESCRIPTIONS, creates a JackeryNumber when no gating predicate is
        present or the predicate returns true.

        Returns:
            list[NumberEntity]: NumberEntity instances ready to be added to Home
            Assistant.
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
