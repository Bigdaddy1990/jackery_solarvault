"""Native Jackery setup must preserve separately registered MQTT entities."""

from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import DOMAIN
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.sensor import async_setup_entry
from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.mark.asyncio()
async def test_native_setup_preserves_existing_mqtt_discovery(
    hass: HomeAssistant,
) -> None:
    """Setup must not send discovery tombstones for another integration's entity."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    mqtt_entry = MockConfigEntry(domain="mqtt", data={})
    mqtt_entry.add_to_hass(hass)
    registry = er.async_get(hass)
    existing = registry.async_get_or_create(
        "sensor",
        "mqtt",
        "jackery_solarvault_mqtt_device_pack_1_soc",
        config_entry=mqtt_entry,
    )
    coordinator = JackerySolarVaultCoordinator(
        hass, entry, MagicMock(spec=JackeryApi), timedelta(seconds=15)
    )
    coordinator.data = {}
    entry.runtime_data = coordinator
    hass.config.components.add("mqtt")

    with (
        patch("homeassistant.components.mqtt.is_connected", return_value=True),
        patch("homeassistant.components.mqtt.async_subscribe_connection_status"),
        patch(
            "homeassistant.components.mqtt.async_publish",
            new=AsyncMock(),
        ) as async_publish,
    ):
        await async_setup_entry(hass, entry, MagicMock())
        await hass.async_block_till_done()
        await coordinator.async_shutdown()
        async_publish.assert_not_awaited()

    assert registry.async_get(existing.entity_id) is existing
