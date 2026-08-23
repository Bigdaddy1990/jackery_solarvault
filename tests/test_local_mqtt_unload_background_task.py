"""Local MQTT stops synchronously inside the bounded entry-unload path."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import jackery_solarvault as integration
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


async def test_unload_defers_local_mqtt_when_unsubscribe_fails(
    hass: HomeAssistant,
) -> None:
    """Failed unsubscribe remains tracked for bounded background cleanup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        entry_id="local-mqtt-unload-stop-failure",
    )
    entry.add_to_hass(hass)
    entry.runtime_data = None
    client = MagicMock(spec=JackeryLocalMqttClient)
    client.async_stop = AsyncMock(side_effect=RuntimeError("still subscribed"))
    bucket = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    bucket[_LOCAL_MQTT_RUNTIME_KEY] = client

    with (
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ),
        patch.object(integration, "_schedule_supplemental_cleanup") as cleanup,
    ):
        assert await async_unload_entry(hass, entry) is True

    assert _LOCAL_MQTT_RUNTIME_KEY not in bucket
    assert client in bucket[integration._SUPPLEMENTAL_LOCAL_MQTT_RUNTIME_KEY]
    cleanup.assert_called_once_with(hass, entry)

    # A fast reload must not create a second subscription before the deferred
    # unsubscribe has completed. Cleanup owns the eventual listener restart.
    coordinator = MagicMock()
    entry.runtime_data = coordinator
    hass.config_entries.async_update_entry(
        entry,
        options={"local_mqtt_enable": True},
    )
    with (
        patch.object(integration, "JackeryLocalMqttClient") as client_cls,
        patch.object(integration, "_schedule_supplemental_cleanup") as retry_cleanup,
    ):
        await integration._async_start_local_mqtt(hass, entry, coordinator)

    client_cls.assert_not_called()
    assert client.async_stop.await_count == 1
    assert bucket[integration._LOCAL_MQTT_RESTART_AFTER_CLEANUP_RUNTIME_KEY] is True
    retry_cleanup.assert_called_once_with(hass, entry)
