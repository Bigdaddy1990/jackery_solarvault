"""Behavioral tests for MQTT-only payload sections surviving an HTTP rebuild.

``_async_update_data_guarded`` rebuilds each device's payload from scratch on
every poll (a fresh ``entry = {...}`` dict) and then restores any key listed
in ``PRESERVED_FAST_PAYLOAD_KEYS`` from the previous cycle's cached data
before returning. A payload section that is populated *only* by an MQTT push
(no HTTP endpoint ever returns it) must be listed there, or the very next
HTTP poll silently drops it. Only the Jackery cloud ``api`` boundary is
mocked (via the reusable ``tests._update_cycle_fixture``); the coordinator's
real preserve step runs unmocked.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApiError
from custom_components.jackery_solarvault.const import (
    PAYLOAD_ALARM,
    PAYLOAD_BATTERY_BOUNDARY,
    PAYLOAD_ELECTRICITY_STRATEGY,
    PAYLOAD_MQTT_CONNECT_INFO,
    PAYLOAD_SMART_MODE,
    PAYLOAD_SMART_SCHEDULE,
    PAYLOAD_TOU_SCHEDULE,
    PAYLOAD_WIFI_CONFIG,
    PAYLOAD_WIFI_LIST,
)
from tests._update_cycle_fixture import (  # ruff:ignore[banned-api]
    DEVICE_ID,
    make_update_cycle_api,
    setup_update_cycle_coordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def _teardown(hass: HomeAssistant, entry_id: str) -> None:
    """Unload the entry and drain background tasks."""
    await hass.config_entries.async_unload(entry_id)
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# F-SW2-4: MQTT-only sections wiped by the next HTTP poll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_mqtt_only_sections_survive_http_rebuild(hass: HomeAssistant) -> None:
    """WiFi/MQTT-connect/electricity-strategy/battery-boundary sections survive.

    These sections are only ever written by ``_async_handle_mqtt_message``
    (coordinator.py) — no HTTP endpoint ever populates them — so the fresh
    per-device ``entry`` dict built on every HTTP poll never sets them itself.
    Unless they are preserved from the previous cycle's cached data, they
    vanish from the coordinator's exposed data on the very next poll.
    """
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    mqtt_only_sections = {
        PAYLOAD_WIFI_CONFIG: {"ssid": "solar-net"},
        PAYLOAD_WIFI_LIST: [{"ssid": "solar-net"}],
        PAYLOAD_MQTT_CONNECT_INFO: {"host": "emqx.jackeryapp.com"},
        PAYLOAD_ELECTRICITY_STRATEGY: {"mode": "peak-shaving"},
        PAYLOAD_BATTERY_BOUNDARY: {"minSoc": 20, "maxSoc": 90},
    }
    coordinator.data = {DEVICE_ID: dict(mqtt_only_sections)}

    result = await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    for key, value in mqtt_only_sections.items():
        assert result[DEVICE_ID][key] == value
    await _teardown(hass, entry.entry_id)


# ---------------------------------------------------------------------------
# F-SW2-3: a freshly MQTT-pushed alarm is overwritten by the next HTTP poll
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_mqtt_pushed_alarm_survives_http_rebuild(hass: HomeAssistant) -> None:
    """An MQTT-pushed alarm section must survive the next HTTP poll cycle."""
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    coordinator.data = {DEVICE_ID: {PAYLOAD_ALARM: {"pushed": "via-mqtt"}}}

    result = await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    assert result[DEVICE_ID][PAYLOAD_ALARM] == {"pushed": "via-mqtt"}
    await _teardown(hass, entry.entry_id)


# ---------------------------------------------------------------------------
# Shadow-config buckets flip to "unknown" when a shadow fetch fails/skips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_shadow_config_buckets_survive_rebuild_on_failed_fetch(
    hass: HomeAssistant,
) -> None:
    """smart_mode/smart_schedule/tou_schedule survive a rebuild + failed shadow fetch.

    The fast HTTP poll rebuilds each device entry from scratch and copies only
    ``PRESERVED_FAST_PAYLOAD_KEYS`` from the previous cycle. These shadow-config
    buckets are filled solely by the background ``getSmartMode`` /
    ``getSmartSchedulePrediction`` fallback (TOU via MQTT). When that fetch
    fails, returns empty, or is cadence-skipped — the exact transient case the
    carry-forward is meant to cover — ``self.data`` has already been rebuilt, so
    without preservation the buckets vanish and the diagnostic sensors flip to
    "unknown". Failing the shadow fetches here proves survival comes from
    preservation, not a same-cycle refetch.
    """
    api = make_update_cycle_api()
    api.async_get_smart_mode_info = AsyncMock(
        side_effect=JackeryApiError("transient getSmartMode failure"),
    )
    api.async_get_smart_schedule_prediction = AsyncMock(
        side_effect=JackeryApiError("transient getSmartSchedulePrediction failure"),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)
    shadow_buckets = {
        PAYLOAD_SMART_MODE: {"isActive": True, "timeDifference": 42},
        PAYLOAD_SMART_SCHEDULE: {"profit": "1.23", "days": 7},
        PAYLOAD_TOU_SCHEDULE: {"tasks": [{"start": "00:00", "end": "06:00"}]},
    }
    coordinator.data = {DEVICE_ID: dict(shadow_buckets)}

    result = await coordinator._async_update_data_guarded()
    await hass.async_block_till_done(wait_background_tasks=True)

    for key, value in shadow_buckets.items():
        assert result[DEVICE_ID][key] == value
    await _teardown(hass, entry.entry_id)
