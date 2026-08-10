"""HA fixture tests for setup/unload of a Jackery SolarVault config entry."""

from typing import TYPE_CHECKING
from unittest.mock import patch

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

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.asyncio

_TEST_HTTP_DATA = {
    "test-device": {
        FIELD_DEVICE_ID: "test-device",
        FIELD_DEVICE_SN: "TEST-SERIAL",
        FIELD_DEVICE_NAME: "Test SolarVault",
        FIELD_MODEL_CODE: 3002,
    },
}


async def test_setup_and_unload_round_trip(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A clean setup followed by unload must leave HA without dangling state."""
    from pytest_homeassistant_custom_component.common import (  # ruff: ignore[import-outside-top-level]
        MockConfigEntry,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
        },
    )
    entry.add_to_hass(hass)

    # Keep the mandatory HTTP path active while isolating external I/O and
    # supplementary transports from this setup lifecycle test.
    with (
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_discover",
            return_value=True,
        ),
        # Patch the network/MQTT-touching paths to avoid real I/O
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_update_data",
            return_value={},
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
            "custom_components.jackery_solarvault._defer_layer5_start_task",
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
    from pytest_homeassistant_custom_component.common import MockConfigEntry  # ruff:ignore[unsorted-imports]  # ruff: ignore[import-outside-top-level]

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
            "JackerySolarVaultCoordinator._async_update_data",
            return_value={},
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_ensure_mqtt",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault._defer_layer5_start_task",
            return_value=None,
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    services = hass.services.async_services_for_domain(DOMAIN)
    assert "rename_system" in services
    assert "refresh_weather_plan" in services
    assert "delete_storm_alert" in services
