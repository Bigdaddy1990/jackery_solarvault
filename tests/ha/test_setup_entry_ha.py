"""HA fixture tests for setup/unload of a Jackery SolarVault config entry."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
import pytest

from custom_components.jackery_solarvault.const import DOMAIN

pytestmark = pytest.mark.asyncio


async def test_setup_and_unload_round_trip(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A clean setup followed by unload must leave HA without dangling state."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
        },
    )
    entry.add_to_hass(hass)

    # Patch the network/MQTT-touching paths to avoid real I/O
    with (
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_update_data",
            return_value={},
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_ensure_mqtt",
            return_value=None,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED

        # runtime_data is populated with the coordinator instance
        assert entry.runtime_data is not None

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.NOT_LOADED


async def test_services_register_on_setup(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """The three integration services must be registered after setup."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_update_data",
            return_value={},
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_ensure_mqtt",
            return_value=None,
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    services = hass.services.async_services_for_domain(DOMAIN)
    assert "rename_system" in services
    assert "refresh_weather_plan" in services
    assert "delete_storm_alert" in services
