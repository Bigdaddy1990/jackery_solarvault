"""Tests for uncovered paths in number.py to increase coverage."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.const import PAYLOAD_PRICE
from custom_components.jackery_solarvault.entity import HTTP_COMMAND_SOURCES
from custom_components.jackery_solarvault.number import (
    JackeryNumber,
    JackeryNumberDescription,
    _max_feed_grid_allowed_values,  # noqa: PLC2701, RUF105
    _max_feed_grid_dynamic_max,  # noqa: PLC2701, RUF105
    _rounded_int,  # noqa: PLC2701, RUF105
    _single_tariff_dynamic_unit,  # noqa: PLC2701, RUF105
    _wire_float,  # noqa: PLC2701, RUF105
    _wire_int,  # noqa: PLC2701, RUF105
    async_setup_entry,
)
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfPower
from homeassistant.helpers.entity import EntityCategory


class TestRoundedInt:
    """Test _rounded_int helper function."""

    def test_int_returns_int(self) -> None:  # noqa: PLR6301, RUF105
        """Test integer input returns integer."""
        assert _rounded_int(5) == 5

    def test_float_rounds(self) -> None:  # noqa: PLR6301, RUF105
        """Test float input rounds to nearest int."""
        assert _rounded_int(5.4) == 5
        assert _rounded_int(5.5) == 6
        assert _rounded_int(5.6) == 6

    def test_string_number(self) -> None:  # noqa: PLR6301, RUF105
        """Test string number input."""
        assert _rounded_int("5") == 5
        assert _rounded_int("5.5") == 6

    def test_invalid_raises(self) -> None:  # noqa: PLR6301, RUF105
        """Test invalid input raises HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415, RUF105

        with pytest.raises(HomeAssistantError, match="invalid number value"):
            _rounded_int("invalid")


class TestWireInt:
    """Test _wire_int helper function."""

    def test_int_returns_int(self) -> None:  # noqa: PLR6301, RUF105
        """Test integer input returns integer."""
        assert _wire_int(5) == 5

    def test_string_number(self) -> None:  # noqa: PLR6301, RUF105
        """Test string number input."""
        assert _wire_int("5") == 5

    def test_invalid_raises(self) -> None:  # noqa: PLR6301, RUF105
        """Test invalid input raises HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415, RUF105

        with pytest.raises(HomeAssistantError, match="invalid number value"):
            _wire_int("invalid")


class TestWireFloat:
    """Test _wire_float helper function."""

    def test_float_returns_float(self) -> None:  # noqa: PLR6301, RUF105
        """Test float input returns float."""
        assert _wire_float(5.5) == 5.5  # noqa: RUF069, RUF105

    def test_int_returns_float(self) -> None:  # noqa: PLR6301, RUF105
        """Test integer input returns float."""
        assert _wire_float(5) == 5.0  # noqa: RUF069, RUF105

    def test_string_number(self) -> None:  # noqa: PLR6301, RUF105
        """Test string number input."""
        assert _wire_float("5.5") == 5.5  # noqa: RUF069, RUF105

    def test_invalid_raises(self) -> None:  # noqa: PLR6301, RUF105
        """Test invalid input raises HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415, RUF105

        with pytest.raises(HomeAssistantError, match="invalid number value"):
            _wire_float("invalid")


class TestMaxFeedGridDynamicMax:
    """Test _max_feed_grid_dynamic_max helper function."""

    def test_feed_limit_over_800(self) -> None:  # noqa: PLR6301, RUF105
        """Test feed limit over 800 returns 2500."""
        payload = {"properties": {"maxFeedGrid": 1000}}
        assert _max_feed_grid_dynamic_max(payload) == 2500.0  # noqa: RUF069, RUF105

    def test_max_grid_std_pw_over_800(self) -> None:  # noqa: PLR6301, RUF105
        """Test maxGridStdPw over 800 returns 2500."""
        payload = {"properties": {"maxGridStdPw": 1000}}
        assert _max_feed_grid_dynamic_max(payload) == 2500.0  # noqa: RUF069, RUF105

    def test_max_out_pw_over_800(self) -> None:  # noqa: PLR6301, RUF105
        """Test maxOutPw over 800 returns 2500."""
        payload = {"properties": {"maxOutPw": 1000}}
        assert _max_feed_grid_dynamic_max(payload) == 2500.0  # noqa: RUF069, RUF105

    def test_all_under_800(self) -> None:  # noqa: PLR6301, RUF105
        """Test all values under 800 returns 800."""
        payload = {
            "properties": {"maxFeedGrid": 600, "maxGridStdPw": 600, "maxOutPw": 600}
        }
        assert _max_feed_grid_dynamic_max(payload) == 800.0  # noqa: RUF069, RUF105

    def test_missing_values_defaults(self) -> None:  # noqa: PLR6301, RUF105
        """Test missing values use defaults."""
        payload = {"properties": {}}
        assert _max_feed_grid_dynamic_max(payload) == 2500.0  # noqa: RUF069, RUF105


class TestMaxFeedGridAllowedValues:
    """Test _max_feed_grid_allowed_values helper function."""

    def test_under_800_returns_single(self) -> None:  # noqa: PLR6301, RUF105
        """Test dynamic max under 800 returns single value."""
        payload = {
            "properties": {"maxFeedGrid": 600, "maxGridStdPw": 600, "maxOutPw": 600}
        }  # noqa: E501, RUF100
        assert _max_feed_grid_allowed_values(payload) == (800.0,)

    def test_over_800_returns_both(self) -> None:  # noqa: PLR6301, RUF105
        """Test dynamic max over 800 returns both values."""
        payload = {"properties": {"maxFeedGrid": 1000}}
        assert _max_feed_grid_allowed_values(payload) == (800.0, 2500.0)


class TestSingleTariffDynamicUnit:
    """Test _single_tariff_dynamic_unit helper function."""

    def test_single_currency(self) -> None:  # noqa: PLR6301, RUF105
        """Test single currency from price section."""
        payload = {"price": {"singleCurrency": "$"}}
        assert _single_tariff_dynamic_unit(payload) == "$"

    def test_currency(self) -> None:  # noqa: PLR6301, RUF105
        """Test currency from price section."""
        payload = {"price": {"currency": "€"}}
        assert _single_tariff_dynamic_unit(payload) == "€"

    def test_single_currency_code(self) -> None:  # noqa: PLR6301, RUF105
        """Test single currency code from price section."""
        payload = {"price": {"singleCurrencyCode": "USD"}}
        assert _single_tariff_dynamic_unit(payload) == "USD"

    def test_currency_code(self) -> None:  # noqa: PLR6301, RUF105
        """Test currency code from price section."""
        payload = {"price": {"currencyCode": "EUR"}}
        assert _single_tariff_dynamic_unit(payload) == "EUR"

    def test_default_euro(self) -> None:  # noqa: PLR6301, RUF105
        """Test default euro when no currency found."""
        payload = {"price": {}}
        assert _single_tariff_dynamic_unit(payload) == "€"


class TestJackeryNumber:
    """Test JackeryNumber class."""

    def _create_coordinator(self, data=None):  # noqa: PLR6301, RUF105
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        coordinator.third_party_mqtt_config_plaintext = MagicMock(return_value={})
        coordinator.async_set_soc_limits = AsyncMock()
        coordinator.async_set_max_feed_grid = AsyncMock()
        coordinator.async_set_max_output_power = AsyncMock()
        coordinator.async_set_default_power = AsyncMock()
        coordinator.async_set_single_price = AsyncMock()
        coordinator.async_update_third_party_mqtt_config = AsyncMock()
        coordinator.async_portable_set_number = AsyncMock()
        coordinator.async_portable_set_custom_use_battery = AsyncMock()
        return coordinator

    def _create_number(self, coordinator, key="soc_charge_limit_set"):  # noqa: PLR6301, RUF105
        """Create a number instance for testing."""
        description = JackeryNumberDescription(
            key=key,
            translation_key=key,
            native_unit_of_measurement=PERCENTAGE,
            mode=NumberMode.SLIDER,
            entity_category=EntityCategory.CONFIG,
            native_min_value=0,
            native_max_value=100,
            native_step=1,
            source_keys=("socChgLimit", "socChargeLimit"),
            setter=lambda c, d, v: None,
        )
        return JackeryNumber(
            coordinator=coordinator, device_id="test_device", description=description
        )

    def test_creation(self) -> None:
        """Test number creation."""
        coordinator = self._create_coordinator()
        sensor = self._create_number(coordinator)
        assert sensor is not None
        assert sensor.entity_description.key == "soc_charge_limit_set"

    def test_native_value_with_data(self) -> None:
        """Test native_value property with data."""
        coordinator = self._create_coordinator({
            "test_device": {"properties": {"socChgLimit": 80}}
        })
        sensor = self._create_number(coordinator)
        assert sensor.native_value == 80.0  # noqa: RUF069, RUF105

    def test_native_value_none_when_missing(self) -> None:
        """Test native_value property when key is missing."""
        coordinator = self._create_coordinator({"test_device": {"properties": {}}})
        sensor = self._create_number(coordinator)
        assert sensor.native_value is None

    def test_native_max_value_dynamic(self) -> None:
        """Test native_max_value property with dynamic max."""
        coordinator = self._create_coordinator({
            "test_device": {"properties": {"maxFeedGrid": 1000}}
        })
        description = JackeryNumberDescription(
            key="max_feed_grid",
            translation_key="max_feed_grid",
            device_class=NumberDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            mode=NumberMode.SLIDER,
            entity_category=EntityCategory.CONFIG,
            native_min_value=800,
            native_max_value=2500,
            native_step=1700,
            source_keys=("maxFeedGrid", "maxGridStdPw"),
            setter=lambda c, d, v: None,
            dynamic_max=_max_feed_grid_dynamic_max,
        )
        sensor = JackeryNumber(
            coordinator=coordinator, device_id="test_device", description=description
        )
        assert sensor.native_max_value == 2500.0  # noqa: RUF069, RUF105

    def test_native_unit_of_measurement_dynamic(self) -> None:
        """Test native_unit_of_measurement property with dynamic unit."""
        coordinator = self._create_coordinator({
            "test_device": {"price": {"singleCurrency": "$"}}
        })
        description = JackeryNumberDescription(
            key="single_tariff_price_set",
            translation_key="single_tariff_price_set",
            mode=NumberMode.BOX,
            entity_category=EntityCategory.CONFIG,
            native_min_value=0,
            native_max_value=10,
            native_step=0.01,
            source_keys=("singlePrice",),
            source_section=PAYLOAD_PRICE,
            setter=lambda c, d, v: None,
            command_sources=HTTP_COMMAND_SOURCES,
            device_registry_role="system",
            dynamic_unit=_single_tariff_dynamic_unit,
            value_transform=_wire_float,
        )
        sensor = JackeryNumber(
            coordinator=coordinator, device_id="test_device", description=description
        )
        assert sensor.native_unit_of_measurement == "$"

    @pytest.mark.asyncio
    async def test_async_set_native_value_valid(self) -> None:
        """Test async_set_native_value with valid value."""
        coordinator = self._create_coordinator()
        mock_setter = AsyncMock()
        description = JackeryNumberDescription(
            key="soc_charge_limit_set",
            translation_key="soc_charge_limit_set",
            native_unit_of_measurement=PERCENTAGE,
            mode=NumberMode.SLIDER,
            entity_category=EntityCategory.CONFIG,
            native_min_value=0,
            native_max_value=100,
            native_step=1,
            source_keys=("socChgLimit", "socChargeLimit"),
            setter=mock_setter,
        )
        sensor = JackeryNumber(
            coordinator=coordinator, device_id="test_device", description=description
        )

        await sensor.async_set_native_value(75)
        mock_setter.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_set_native_value_invalid_range(self) -> None:
        """Test async_set_native_value with invalid range."""
        from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415, RUF105

        coordinator = self._create_coordinator()
        sensor = self._create_number(coordinator)

        with pytest.raises(HomeAssistantError, match="invalid_number_range"):
            await sensor.async_set_native_value(150)


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self) -> None:  # noqa: PLR6301, RUF105
        """Test async_setup_entry creates number entities."""
        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.async_on_unload = MagicMock()

        async_add_entities = MagicMock()

        # Mock coordinator (entry.runtime_data IS the coordinator)
        coordinator = MagicMock()
        coordinator.data = {
            "test_device": {
                "properties": {
                    "socChgLimit": 80,
                    "socDischgLimit": 20,
                    "maxOutPw": 2000,
                },
            }
        }
        coordinator.third_party_mqtt_config_plaintext = MagicMock(return_value={})
        coordinator.async_add_listener = MagicMock(return_value=lambda: None)
        config_entry.runtime_data = coordinator

        await async_setup_entry(hass, config_entry, async_add_entities)

        # Verify async_add_entities was called
        assert async_add_entities.called
        args = async_add_entities.call_args
        sensors = args[0][0]
        assert len(sensors) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
