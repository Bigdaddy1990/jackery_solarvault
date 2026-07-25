"""Local MQTT stops synchronously inside the bounded entry-unload path."""

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import (
    _LOCAL_MQTT_RUNTIME_KEY,
    JackeryLocalMqttClient,
    async_unload_entry,
)
from custom_components.jackery_solarvault.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_unload_stops_and_removes_local_mqtt_client(
    hass: HomeAssistant,
) -> None:
    """Entry unload awaits the local client and clears its runtime slot."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        entry_id="local-mqtt-unload-entry-task",
    )
    entry.add_to_hass(hass)
    entry.runtime_data = None
    client = MagicMock(spec=JackeryLocalMqttClient)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        _LOCAL_MQTT_RUNTIME_KEY: client,
    }

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        return_value=True,
    ):
        assert await async_unload_entry(hass, entry) is True

    # Unload moves the client into the independent supplemental-cleanup
    # bucket (Layer 5 must never block unload); the scheduled background
    # task then awaits the actual stop.
    assert _LOCAL_MQTT_RUNTIME_KEY not in hass.data[DOMAIN][entry.entry_id]
    await hass.async_block_till_done(wait_background_tasks=True)
    client.async_stop.assert_awaited_once()
