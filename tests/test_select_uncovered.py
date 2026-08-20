"""Tests for uncovered paths in select.py to increase coverage."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.select import (
    JackerySelect,
    JackerySelectDescription,
    _ct_phase_current,  # noqa: PLC2701, RUF105
    _island_auto_off_current,  # noqa: PLC2701, RUF105
    _portable_ac1_priority_current,  # noqa: PLC2701, RUF105
    _portable_ac2_priority_current,  # noqa: PLC2701, RUF105
    _portable_ac_output_mode_current,  # noqa: PLC2701, RUF105
    _portable_battery_mode_current,  # noqa: PLC2701, RUF105
    _portable_charge_mode_current,  # noqa: PLC2701, RUF105
    _portable_dc_priority_current,  # noqa: PLC2701, RUF105
    _portable_output_priority_current,  # noqa: PLC2701, RUF105
    _portable_power_mode_current,  # noqa: PLC2701, RUF105
    _portable_screen_current,  # noqa: PLC2701, RUF105
    _portable_ups_model_current,  # noqa: PLC2701, RUF105
    _price_mode_current,  # noqa: PLC2701, RUF105
    _price_mode_dynamic_available,  # noqa: PLC2701, RUF105
    _price_provider_current,  # noqa: PLC2701, RUF105
    _price_provider_options,  # noqa: PLC2701, RUF105
    _price_source_label,  # noqa: PLC2701, RUF105
    _price_source_matches_current,  # noqa: PLC2701, RUF105
    _price_source_regions,  # noqa: PLC2701, RUF105
    _price_sources_from_payload,  # noqa: PLC2701, RUF105
    _storm_minutes_current,  # noqa: PLC2701, RUF105
    _storm_minutes_current_value,  # noqa: PLC2701, RUF105
    _storm_minutes_fallback,  # noqa: PLC2701, RUF105
    _storm_minutes_label,  # noqa: PLC2701, RUF105
    _storm_minutes_options,  # noqa: PLC2701, RUF105
    _storm_minutes_value,  # noqa: PLC2701, RUF105
    _temp_unit_current,  # noqa: PLC2701, RUF105
    _work_mode_current,  # noqa: PLC2701, RUF105
    async_setup_entry,
)


class TestStormMinutesHelpers:
    """Test storm minutes helper functions."""

    def test_storm_minutes_value_from_properties(self) -> None:  # noqa: PLR6301, RUF105
        """Test storm minutes from properties."""
        properties = {"wpc": 60}
        weather_plan = {}
        task_plan = {}
        assert _storm_minutes_value(properties, weather_plan, task_plan) == 60

    def test_storm_minutes_value_from_weather_plan(self) -> None:  # noqa: PLR6301, RUF105
        """Test storm minutes from weather plan."""
        properties = {}
        weather_plan = {"minsInterval": 120}
        task_plan = {}
        assert _storm_minutes_value(properties, weather_plan, task_plan) == 120

    def test_storm_minutes_value_below_min_valid(self) -> None:  # noqa: PLR6301, RUF105
        """Test storm minutes below minimum valid returns None."""
        properties = {"wpc": 1}
        weather_plan = {}
        task_plan = {}
        assert _storm_minutes_value(properties, weather_plan, task_plan) is None

    def test_storm_minutes_fallback_with_wps(self) -> None:  # noqa: PLR6301, RUF105
        """Test fallback with WPS enabled."""
        properties = {"wps": 1}
        weather_plan = {}
        task_plan = {}
        from custom_components.jackery_solarvault.const import (  # noqa: PLC0415, RUF105
            DEFAULT_STORM_WARNING_MINUTES,
        )

        assert (
            _storm_minutes_fallback(properties, weather_plan, task_plan)
            == DEFAULT_STORM_WARNING_MINUTES
        )

    def test_storm_minutes_label(self) -> None:  # noqa: PLR6301, RUF105
        """Test storm minutes label format."""
        assert _storm_minutes_label(10) == "min_10"
        assert _storm_minutes_label(30) == "min_30"


class TestPriceSourceHelpers:
    """Test price source helper functions."""

    def test_price_source_label(self) -> None:  # noqa: PLR6301, RUF105
        """Test price source label generation."""
        source = {
            "platformCompanyId": "123",
            "companyName": "Test Provider",
            "country": "DE",
        }
        label = _price_source_label(source)
        assert "Test Provider" in label
        assert "DE" in label
        assert "#123" in label

    def test_price_source_regions(self) -> None:  # noqa: PLR6301, RUF105
        """Test price source regions extraction."""
        source = {"country": "DE,FR"}
        regions = _price_source_regions(source)
        assert "DE" in regions or "FR" in regions

    def test_price_source_matches_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test price source matches current."""
        source = {"platformCompanyId": "123", "country": "DE"}
        assert _price_source_matches_current(source, "123", "DE") is True
        assert _price_source_matches_current(source, "456", "DE") is False

    def test_price_sources_from_payload(self) -> None:  # noqa: PLR6301, RUF105
        """Test price sources from payload."""
        payload = {
            "price_sources": [
                {
                    "platformCompanyId": "123",
                    "companyName": "Provider1",
                    "country": "DE",
                },
                {
                    "platformCompanyId": "456",
                    "companyName": "Provider2",
                    "country": "FR",
                },
            ]
        }
        sources = _price_sources_from_payload(payload)
        assert len(sources) == 2

    def test_price_mode_dynamic_available(self) -> None:  # noqa: PLR6301, RUF105
        """Test price mode dynamic available."""
        entity = MagicMock()
        entity._price = {"platformCompanyId": "123", "systemRegion": "DE"}  # noqa: RUF105, SLF001
        entity._payload = {}  # noqa: RUF105, SLF001
        assert _price_mode_dynamic_available(entity) is True


class TestWorkModeCurrent:
    """Test work mode current function."""

    def test_work_mode_current_from_properties(self) -> None:  # noqa: PLR6301, RUF105
        """Test work mode from properties."""
        entity = MagicMock()
        entity._properties = {"workModel": 7}  # noqa: RUF105, SLF001
        entity._task_plan = {}  # noqa: RUF105, SLF001
        entity._price = {}  # noqa: RUF105, SLF001
        entity._warn_unknown_once = MagicMock()  # noqa: RUF105, SLF001
        result = _work_mode_current(entity)
        assert result is not None

    def test_work_mode_current_unknown_logs_warning(self) -> None:  # noqa: PLR6301, RUF105
        """Test unknown work mode logs warning."""
        entity = MagicMock()
        entity._properties = {"workModel": 99}  # noqa: RUF105, SLF001
        entity._task_plan = {}  # noqa: RUF105, SLF001
        entity._price = {}  # noqa: RUF105, SLF001
        entity._warn_unknown_once = MagicMock()  # noqa: RUF105, SLF001
        _work_mode_current(entity)
        entity._warn_unknown_once.assert_called_once_with(99)  # noqa: RUF105, SLF001


class TestTempUnitCurrent:
    """Test temp unit current function."""

    def test_temp_unit_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test temp unit current."""
        entity = MagicMock()
        entity._properties = {"tempUnit": 0}  # noqa: RUF105, SLF001
        result = _temp_unit_current(entity)
        assert result == "celsius"


class TestIslandAutoOffCurrent:
    """Test island auto off current function."""

    def test_island_auto_off_current_from_properties(self) -> None:  # noqa: PLR6301, RUF105
        """Test island auto off from properties."""
        entity = MagicMock()
        entity._properties = {"offGridTime": 2}  # noqa: RUF105, SLF001
        entity._task_plan = {}  # noqa: RUF105, SLF001
        result = _island_auto_off_current(entity)
        assert result == "h_2"

    def test_island_auto_off_current_from_task_plan(self) -> None:  # noqa: PLR6301, RUF105
        """Test island auto off from task plan."""
        entity = MagicMock()
        entity._properties = {}  # noqa: RUF105, SLF001
        entity._task_plan = {"offGridDownTime": 8}  # noqa: RUF105, SLF001
        result = _island_auto_off_current(entity)
        assert result == "h_8"


class TestStormMinutesCurrent:
    """Test storm minutes current functions."""

    def test_storm_minutes_current_value(self) -> None:  # noqa: PLR6301, RUF105
        """Test storm minutes current value."""
        entity = MagicMock()
        entity._properties = {"wpc": 120}  # noqa: RUF105, SLF001
        entity._weather_plan = {}  # noqa: RUF105, SLF001
        entity._task_plan = {}  # noqa: RUF105, SLF001
        result = _storm_minutes_current_value(entity)
        assert result == 120

    def test_storm_minutes_options(self) -> None:  # noqa: PLR6301, RUF105
        """Test storm minutes options."""
        entity = MagicMock()
        entity._properties = {"wpc": 120}  # noqa: RUF105, SLF001
        entity._weather_plan = {}  # noqa: RUF105, SLF001
        entity._task_plan = {}  # noqa: RUF105, SLF001
        options = _storm_minutes_options(entity)
        # Default options include 60 (1h) to 1440 (24h) in 60-min increments
        assert "min_60" in options
        assert "min_120" in options
        assert "min_1440" in options

    def test_storm_minutes_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test storm minutes current."""
        entity = MagicMock()
        entity._properties = {"wpc": 120}  # noqa: RUF105, SLF001
        entity._weather_plan = {}  # noqa: RUF105, SLF001
        entity._task_plan = {}  # noqa: RUF105, SLF001
        result = _storm_minutes_current(entity)
        assert result == "min_120"


class TestPriceModeCurrent:
    """Test price mode current function."""

    def test_price_mode_current_dynamic(self) -> None:  # noqa: PLR6301, RUF105
        """Test price mode current dynamic."""
        entity = MagicMock()
        entity._price = {"dynamicOrSingle": 1}  # noqa: RUF105, SLF001
        entity._task_plan = {}  # noqa: RUF105, SLF001
        entity._properties = {}  # noqa: RUF105, SLF001
        entity._warn_unknown_once = MagicMock()  # noqa: RUF105, SLF001
        result = _price_mode_current(entity)
        assert result == "dynamic"


class TestPriceProviderHelpers:
    """Test price provider helper functions."""

    def test_price_provider_options(self) -> None:  # noqa: PLR6301, RUF105
        """Test price provider options."""
        entity = MagicMock()
        entity._payload = {  # noqa: RUF105, SLF001
            "price_sources": [
                {
                    "platformCompanyId": "123",
                    "companyName": "Provider1",
                    "country": "DE",
                },
            ]
        }
        entity.current_option = None
        options = _price_provider_options(entity)
        assert len(options) == 1
        assert "Provider1" in options[0]
        assert "DE" in options[0]
        assert "#123" in options[0]

    def test_price_provider_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test price provider current."""
        entity = MagicMock()
        entity._price = {  # noqa: RUF105, SLF001
            "platformCompanyId": "123",
            "systemRegion": "DE",
            "companyName": "Test",
        }
        entity._payload = {  # noqa: RUF105, SLF001
            "price_sources": [
                {
                    "platformCompanyId": "123",
                    "companyName": "Provider1",
                    "country": "DE",
                },
            ]
        }
        result = _price_provider_current(entity)
        assert "Provider1" in result


class TestCtPhaseCurrent:
    """Test CT phase current function."""

    def test_ct_phase_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test CT phase current."""
        from custom_components.jackery_solarvault.const import PAYLOAD_CT_METER  # noqa: I001, PLC0415, RUF105

        entity = MagicMock()
        entity._payload = {PAYLOAD_CT_METER: {"schePhase": 1}}  # noqa: RUF105, SLF001
        result = _ct_phase_current(entity)
        assert result == "phase_1"


class TestPortableSelectCurrent:
    """Test portable select current functions."""

    def test_portable_ups_model_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test portable UPS model current."""
        entity = MagicMock()
        entity._properties = {"ups": 1}  # noqa: RUF105, SLF001
        result = _portable_ups_model_current(entity)
        assert result == "lifepo4"

    def test_portable_ups_model_current_standard(self) -> None:  # noqa: PLR6301, RUF105
        """Test portable UPS model current standard."""
        entity = MagicMock()
        entity._properties = {"ups": 0}  # noqa: RUF105, SLF001
        result = _portable_ups_model_current(entity)
        assert result == "standard"

    def test_portable_battery_mode_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test portable battery mode current."""
        entity = MagicMock()
        entity._properties = {"lps": 0}  # noqa: RUF105, SLF001
        result = _portable_battery_mode_current(entity)
        assert result == "normal"

    def test_portable_charge_mode_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test portable charge mode current."""
        entity = MagicMock()
        entity._properties = {"cs": 0}  # noqa: RUF105, SLF001
        result = _portable_charge_mode_current(entity)
        assert result == "fast"

    def test_portable_power_mode_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test portable power mode current."""
        entity = MagicMock()
        entity._properties = {"pm": 1}  # noqa: RUF105, SLF001
        result = _portable_power_mode_current(entity)
        assert result == "eco"

    def test_portable_screen_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test portable screen current."""
        entity = MagicMock()
        entity._properties = {"sltb": 2}  # noqa: RUF105, SLF001
        result = _portable_screen_current(entity)
        assert result == "2min"

    def test_portable_ac_output_mode_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test portable AC output mode current."""
        entity = MagicMock()
        entity._properties = {"acmode": 1}  # noqa: RUF105, SLF001
        result = _portable_ac_output_mode_current(entity)
        assert result == "quiet"

    def test_portable_output_priority_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test portable output priority current."""
        entity = MagicMock()
        entity._properties = {"outPrio": 1}  # noqa: RUF105, SLF001
        result = _portable_output_priority_current(entity)
        assert result == "grid-first"

    def test_portable_ac1_priority_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test portable AC1 priority current."""
        entity = MagicMock()
        entity._properties = {"oac1Prio": 1}  # noqa: RUF105, SLF001
        result = _portable_ac1_priority_current(entity)
        assert result == "grid-first"

    def test_portable_ac2_priority_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test portable AC2 priority current."""
        entity = MagicMock()
        entity._properties = {"oac2Prio": 1}  # noqa: RUF105, SLF001
        result = _portable_ac2_priority_current(entity)
        assert result == "grid-first"

    def test_portable_dc_priority_current(self) -> None:  # noqa: PLR6301, RUF105
        """Test portable DC priority current."""
        entity = MagicMock()
        entity._properties = {"odcPrio": 1}  # noqa: RUF105, SLF001
        result = _portable_dc_priority_current(entity)
        assert result == "grid-first"


class TestJackerySelect:
    """Test JackerySelect class."""

    def _create_coordinator(self, data=None):  # noqa: PLR6301, RUF105
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        coordinator.device_supports_advanced = MagicMock(return_value=True)
        coordinator.async_set_work_model = AsyncMock()
        coordinator.async_set_temp_unit = AsyncMock()
        coordinator.async_set_off_grid_time = AsyncMock()
        coordinator.async_set_storm_minutes = AsyncMock()
        coordinator.async_set_price_mode_dynamic = AsyncMock()
        coordinator.async_set_price_mode_single = AsyncMock()
        coordinator.async_set_price_source = AsyncMock()
        coordinator.async_set_ct_phase = AsyncMock()
        coordinator.async_portable_set_select = AsyncMock()
        coordinator.async_set_storm_warning = AsyncMock()
        return coordinator

    def _create_select(self, coordinator, key="work_mode_select"):  # noqa: PLR6301, RUF105
        """Create a select instance for testing."""
        # Use an actual description from the module
        from custom_components.jackery_solarvault.select import SELECT_DESCRIPTIONS  # noqa: I001, PLC0415, RUF105

        desc = next((d for d in SELECT_DESCRIPTIONS if d.key == key), None)
        if desc is None:
            # Fallback to a simple description
            desc = JackerySelectDescription(
                key=key,
                translation_key=key,
                options=["option1", "option2"],
                current_fn=lambda e: "option1",
                select_fn=lambda e, o: None,
            )
        return JackerySelect(
            coordinator=coordinator, device_id="test_device", description=desc
        )

    def test_creation(self) -> None:
        """Test select creation."""
        coordinator = self._create_coordinator()
        sensor = self._create_select(coordinator)
        assert sensor is not None

    def test_options_property(self) -> None:
        """Test options property."""
        coordinator = self._create_coordinator()
        sensor = self._create_select(coordinator, "temp_unit_select")
        assert "celsius" in sensor.options

    def test_current_option(self) -> None:
        """Test current_option property."""
        coordinator = self._create_coordinator({
            "test_device": {"properties": {"workModel": 7}}
        })
        sensor = self._create_select(coordinator, "work_mode_select")
        result = sensor.current_option
        assert result is not None

    @pytest.mark.asyncio
    async def test_async_select_option_valid(self) -> None:
        """Test async_select_option with valid option."""
        coordinator = self._create_coordinator()
        sensor = self._create_select(coordinator, "temp_unit_select")

        await sensor.async_select_option("celsius")
        coordinator.async_set_temp_unit.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_select_option_invalid(self) -> None:
        """Test async_select_option with invalid option."""
        from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415, RUF105

        coordinator = self._create_coordinator()
        sensor = self._create_select(coordinator, "temp_unit_select")

        with pytest.raises(HomeAssistantError, match="invalid_select_option"):
            await sensor.async_select_option("invalid_option")


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self) -> None:  # noqa: PLR6301, RUF105
        """Test async_setup_entry creates select entities."""
        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.async_on_unload = MagicMock()

        async_add_entities = MagicMock()

        # Mock coordinator (entry.runtime_data IS the coordinator)
        coordinator = MagicMock()
        coordinator.data = {
            "test_device": {
                "properties": {"workModel": 7, "tempUnit": 0},
            }
        }
        coordinator.device_supports_advanced = MagicMock(return_value=True)
        coordinator.async_set_work_model = AsyncMock()
        coordinator.async_set_temp_unit = AsyncMock()
        coordinator.async_set_off_grid_time = AsyncMock()
        coordinator.async_set_storm_minutes = AsyncMock()
        coordinator.async_set_price_mode_dynamic = AsyncMock()
        coordinator.async_set_price_mode_single = AsyncMock()
        coordinator.async_set_price_source = AsyncMock()
        coordinator.async_set_ct_phase = AsyncMock()
        coordinator.async_portable_set_select = AsyncMock()
        coordinator.async_set_storm_warning = AsyncMock()
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
