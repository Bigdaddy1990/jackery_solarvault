"""Tests for client discovery cache and mqtt session cache."""

import pytest
from homeassistant.core import HomeAssistant

from custom_components.jackery_solarvault.client.discovery_cache import (
    async_load_discovery_cache,
    async_save_discovery_cache,
)
from custom_components.jackery_solarvault.client.mqtt_session_cache import (
    async_clear_mqtt_session,
    async_load_mqtt_session,
    async_save_mqtt_session,
)
from custom_components.jackery_solarvault.const import (
    MQTT_SESSION_MAC_ID,
    MQTT_SESSION_MAC_ID_SOURCE,
    MQTT_SESSION_SEED_B64,
    MQTT_SESSION_USER_ID,
)


@pytest.mark.asyncio
async def test_discovery_cache_flow(hass: HomeAssistant) -> None:
    """Test loading and saving discovery cache."""
    # Loading empty store
    cache = await async_load_discovery_cache(hass, "entry_123")
    assert cache == {}

    # Save discovery cache
    device_index = {"dev_1": {"model": "Explorer 2000 Pro", "serial": "12345"}}
    await async_save_discovery_cache(hass, "entry_123", device_index)

    # Load back
    loaded = await async_load_discovery_cache(hass, "entry_123")
    assert loaded == device_index

    # Load non-existent entry
    loaded_other = await async_load_discovery_cache(hass, "entry_other")
    assert loaded_other == {}


@pytest.mark.asyncio
async def test_mqtt_session_cache_flow(hass: HomeAssistant) -> None:
    """Test save, load, and clear MQTT session cache."""
    # Initially None
    sess = await async_load_mqtt_session(hass, "entry_mqtt")
    assert sess is None

    # Save session
    await async_save_mqtt_session(
        hass,
        "entry_mqtt",
        user_id="user_abc",
        seed_b64="c2VlZF9iNjQ=",
        mac_id="AA:BB:CC:DD:EE:FF",
        mac_id_source="cloud",
        cached_at=1700000000.0,
    )

    # Load session
    loaded = await async_load_mqtt_session(hass, "entry_mqtt")
    assert loaded is not None
    assert loaded[MQTT_SESSION_USER_ID] == "user_abc"
    assert loaded[MQTT_SESSION_SEED_B64] == "c2VlZF9iNjQ="
    assert loaded[MQTT_SESSION_MAC_ID] == "AA:BB:CC:DD:EE:FF"
    assert loaded[MQTT_SESSION_MAC_ID_SOURCE] == "cloud"

    # Clear session
    await async_clear_mqtt_session(hass, "entry_mqtt")
    cleared = await async_load_mqtt_session(hass, "entry_mqtt")
    assert cleared is None

    # Clearing non-existent entry is safe
    await async_clear_mqtt_session(hass, "entry_non_existent")
