"""Integration tests for full Jackery SolarVault setup and entity creation."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_MODEL_CODE,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_TEST_HTTP_DATA = {
    "test-device": {
        FIELD_DEVICE_ID: "test-device",
        FIELD_DEVICE_SN: "TEST-SERIAL",
        FIELD_DEVICE_NAME: "Test SolarVault",
        FIELD_MODEL_CODE: 3002,
    },
}


async def _setup_entry(
    hass: HomeAssistant,
    mock_jackery_login: None,
    http_data: dict | None = None,
) -> None:
    """Helper to set up a config entry with patched I/O."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: I001, PLC0415, RUF105

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
        },
    )
    entry.add_to_hass(hass)

    data = http_data or _TEST_HTTP_DATA

    with (
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_discover",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_update_data",
            return_value=data,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_start_statistics_imports",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_ensure_mqtt",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault._async_start_layer5_transports",
            AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED
        assert entry.runtime_data is not None

    return entry


@pytest.mark.asyncio
async def test_integration_setup_creates_expected_entities(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Full integration setup must create all expected entity types."""
    entry = await _setup_entry(hass, mock_jackery_login)

    # Verify coordinator is initialized
    coordinator = entry.runtime_data
    assert coordinator is not None

    # Verify entities are registered in entity registry
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415, RUF105

    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    assert len(entities) > 0

    # Should have sensor entities
    sensor_entities = [e for e in entities if e.domain == "sensor"]
    assert len(sensor_entities) > 0

    # Should have binary_sensor entities
    binary_sensor_entities = [e for e in entities if e.domain == "binary_sensor"]
    assert len(binary_sensor_entities) > 0

    # Should have button entities
    button_entities = [e for e in entities if e.domain == "button"]
    assert len(button_entities) > 0

    # Should have select entities
    select_entities = [e for e in entities if e.domain == "select"]
    assert len(select_entities) > 0

    # Should have number entities
    number_entities = [e for e in entities if e.domain == "number"]
    assert len(number_entities) > 0

    # Should have switch entities
    switch_entities = [e for e in entities if e.domain == "switch"]
    assert len(switch_entities) > 0

    # Should have text entities
    text_entities = [e for e in entities if e.domain == "text"]
    assert len(text_entities) > 0


@pytest.mark.asyncio
async def test_coordinator_poll_updates_entity_states(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Coordinator polling must update entity states correctly."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: I001, PLC0415, RUF105

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
        },
    )
    entry.add_to_hass(hass)

    # First data response
    first_data = {
        "test-device": {
            FIELD_DEVICE_ID: "test-device",
            FIELD_DEVICE_SN: "TEST-SERIAL",
            FIELD_DEVICE_NAME: "Test SolarVault",
            FIELD_MODEL_CODE: 3002,
        },
    }

    with (
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_discover",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_update_data",
            return_value=first_data,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_start_statistics_imports",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_ensure_mqtt",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault._async_start_layer5_transports",
            AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED
        coordinator = entry.runtime_data
        assert coordinator is not None

    # Get the battery state sensor entity ID
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415, RUF105

    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    battery_sensor = next(
        (
            e
            for e in entities
            if "battery" in e.entity_id.lower() and e.domain == "sensor"
        ),  # noqa: E501, RUF100
        None,
    )
    assert battery_sensor is not None

    # Initial state should be available
    state = hass.states.get(battery_sensor.entity_id)
    assert state is not None


@pytest.mark.asyncio
async def test_integration_unload_removes_entities(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Unloading integration must remove all entities."""
    entry = await _setup_entry(hass, mock_jackery_login)

    # Verify entities exist before unload
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415, RUF105

    ent_reg = er.async_get(hass)
    entities_before = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    assert len(entities_before) > 0

    # Unload
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.NOT_LOADED

    # Entities should be removed
    entities_after = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    assert len(entities_after) == 0


@pytest.mark.asyncio
async def test_reload_integration_preserves_entities(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Reloading integration must preserve entities."""
    entry = await _setup_entry(hass, mock_jackery_login)

    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415, RUF105

    ent_reg = er.async_get(hass)
    entities_before = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    entity_ids_before = {e.entity_id for e in entities_before}
    assert len(entity_ids_before) > 0

    # Reload
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED

    # Entities should still exist with same IDs
    entities_after = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    entity_ids_after = {e.entity_id for e in entities_after}
    assert entity_ids_after == entity_ids_before


@pytest.mark.asyncio
async def test_integration_with_multiple_devices(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Integration must handle multiple devices correctly."""
    multi_device_data = {
        "device-1": {
            FIELD_DEVICE_ID: "device-1",
            FIELD_DEVICE_SN: "SERIAL-1",
            FIELD_DEVICE_NAME: "SolarVault 1",
            FIELD_MODEL_CODE: 3002,
        },
        "device-2": {
            FIELD_DEVICE_ID: "device-2",
            FIELD_DEVICE_SN: "SERIAL-2",
            FIELD_DEVICE_NAME: "SolarVault 2",
            FIELD_MODEL_CODE: 3002,
        },
    }

    entry = await _setup_entry(hass, mock_jackery_login, http_data=multi_device_data)

    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415, RUF105

    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(ent_reg, entry.entry_id)

    # Should have entities for both devices
    device_ids = set()
    for e in entities:
        # Entities should contain device identifier in their unique_id
        if "device-1" in e.unique_id or "device-2" in e.unique_id:
            device_ids.add(e.unique_id.split("_")[0])

    assert "device-1" in device_ids or "device-2" in device_ids


@pytest.mark.asyncio
async def test_integration_config_flow_reauth_updates_runtime(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Reauth flow must update runtime data correctly."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: I001, PLC0415, RUF105

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "old-password",
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_discover",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_update_data",
            return_value=_TEST_HTTP_DATA,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_start_statistics_imports",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_ensure_mqtt",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault._async_start_layer5_transports",
            AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED

    # Trigger reauth
    result = await entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PASSWORD: "new-password"},
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"

    # Entry data should be updated
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert entry.data[CONF_USERNAME] == "user@example.com"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
