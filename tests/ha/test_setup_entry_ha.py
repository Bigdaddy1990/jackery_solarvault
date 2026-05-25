"""HA fixture tests for setup/unload of a Jackery SolarVault config entry."""

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest

from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FIELD_BLUETOOTH_KEY,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_SYSTEM_ID,
    PAYLOAD_DEVICE_META,
    PAYLOAD_SYSTEM_META,
)

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


async def test_setup_uses_cached_discovery_when_cloud_is_unavailable(
    hass: HomeAssistant,
) -> None:
    """Cached discovery lets setup finish so local BLE can start offline."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.jackery_solarvault.client.api import JackeryError

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass",
        },
    )
    entry.add_to_hass(hass)

    cached_device_index = {
        "573702884982521856": {
            FIELD_SYSTEM_ID: "system-1",
            PAYLOAD_SYSTEM_META: {
                FIELD_SYSTEM_ID: "system-1",
                FIELD_BLUETOOTH_KEY: "MDEyMzQ1Njc4OWFiY2RlZg==",
            },
            PAYLOAD_DEVICE_META: {
                FIELD_DEVICE_ID: "573702884982521856",
                FIELD_DEVICE_SN: "HR2C04000280HH3",
            },
        }
    }

    with (
        patch(
            "custom_components.jackery_solarvault.client.api.JackeryApi.async_login",
            side_effect=JackeryError("network down"),
        ),
        patch(
            "custom_components.jackery_solarvault.client.api.JackeryApi.async_get_system_list",
            side_effect=JackeryError("network down"),
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator.async_load_discovery_cache",
            return_value=cached_device_index,
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
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_start_ble_transport",
            return_value=None,
        ) as start_ble,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.LOADED
    assert entry.runtime_data.device_bluetooth_key("573702884982521856") == (
        b"0123456789abcdef"
    )
    start_ble.assert_awaited_once()


async def test_setup_keeps_local_transports_when_first_refresh_times_out(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A cached device must still load BLE/local transports after HTTP timeout."""
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

    cached_device_index = {
        "573702884982521856": {
            FIELD_SYSTEM_ID: "system-1",
            PAYLOAD_SYSTEM_META: {
                FIELD_SYSTEM_ID: "system-1",
                FIELD_BLUETOOTH_KEY: "MDEyMzQ1Njc4OWFiY2RlZg==",
            },
            PAYLOAD_DEVICE_META: {
                FIELD_DEVICE_ID: "573702884982521856",
                FIELD_DEVICE_SN: "HR2C04000280HH3",
            },
        }
    }

    with (
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_discover",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.cached_discovery_snapshot",
            return_value={
                "573702884982521856": {
                    "properties": {},
                    "device": cached_device_index["573702884982521856"][
                        PAYLOAD_DEVICE_META
                    ],
                    "discovery": cached_device_index["573702884982521856"][
                        PAYLOAD_DEVICE_META
                    ],
                    "system": cached_device_index["573702884982521856"][
                        PAYLOAD_SYSTEM_META
                    ],
                }
            },
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_config_entry_first_refresh",
            side_effect=UpdateFailed("property timeout"),
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_ensure_mqtt",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_start_ble_transport",
            return_value=None,
        ) as start_ble,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state == ConfigEntryState.LOADED
    assert entry.runtime_data is not None
    start_ble.assert_awaited_once()
