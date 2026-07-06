"""Regression tests for the poll-cadence watchdog (P6 stall, 2026-07-03).

Live finding: the coordinator went 152 s (and once 42 s) without a single
refresh while the event loop stayed healthy — the scheduled interval
timer was lost together with the BLE-coupled
``_background_refresh -> async_request_refresh`` chain, and no existing
guard noticed. The cloud HTTP path must never silently stop polling
(AGENTS.md §1.2), so an independent time-tracked watchdog has to detect
the stall and force a refresh.
"""

from datetime import timedelta
import logging
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.jackery_solarvault.const import DOMAIN
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant

_STALL_AGE_SEC = 300.0
_FRESH_AGE_SEC = 10.0
_WATCHDOG_TICK = timedelta(seconds=31)


def _make_api_stub() -> MagicMock:
    """Build a ``JackeryApi`` stub that mocks only the network boundary.

    Returns:
        MagicMock: Stub exposing the coroutine surface the coordinator
        touches during setup, with no real IO.
    """
    api = MagicMock(name="JackeryApi")
    api.async_login = AsyncMock(return_value=None)
    api.async_get_mqtt_credentials = AsyncMock(return_value={"user_id": "user-1"})
    api.async_get_system_list = AsyncMock(return_value=[])
    api.async_list_devices_legacy = AsyncMock(return_value=[])
    api.mqtt_session_snapshot = MagicMock(return_value=None)
    api.hydrate_mqtt_session = MagicMock(return_value=None)
    api.async_close = AsyncMock(return_value=None)
    api.payload_debug_callback = None
    api.auth_rejection_callback = None
    return api


@pytest.fixture()
async def watchdog_setup(
    hass: HomeAssistant,
) -> AsyncGenerator[MockConfigEntry]:
    """Set up the integration with a stubbed API.

    Yields:
        MockConfigEntry: The configured entry.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: "tester@example.com", CONF_PASSWORD: "secret"},
        title="Jackery Home",
        entry_id="home-entry",
    )
    entry.add_to_hass(hass)

    api = _make_api_stub()
    with (
        patch(
            "custom_components.jackery_solarvault.JackeryApi",
            return_value=api,
        ),
        patch(
            "custom_components.jackery_solarvault._async_finish_entry_startup",
            AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    coordinator._async_update_data = AsyncMock(return_value={})  # noqa: SLF001

    yield entry

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _tick_watchdog(hass: HomeAssistant) -> None:
    """Advance wall clock past the watchdog check interval."""
    async_fire_time_changed(hass, dt_util.utcnow() + _WATCHDOG_TICK)
    await hass.async_block_till_done()


async def test_watchdog_forces_refresh_after_poll_stall(
    hass: HomeAssistant,
    watchdog_setup: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A silent poll stall triggers a warning and a forced refresh."""
    coordinator = watchdog_setup.runtime_data
    coordinator._last_http_refresh_completed_monotonic = (  # noqa: SLF001
        time.monotonic() - _STALL_AGE_SEC
    )

    with (
        caplog.at_level(
            logging.WARNING,
            logger="custom_components.jackery_solarvault.coordinator",
        ),
        patch.object(
            coordinator,
            "async_refresh",
            AsyncMock(return_value=None),
        ) as forced_refresh,
    ):
        await _tick_watchdog(hass)
        coordinator._last_http_refresh_completed_monotonic = time.monotonic()  # noqa: SLF001

    assert any(
        "poll watchdog" in record.getMessage().lower() for record in caplog.records
    ), "stalled cadence must be surfaced as a warning"
    forced_refresh.assert_awaited()


async def test_watchdog_stays_silent_while_polling_is_healthy(
    hass: HomeAssistant,
    watchdog_setup: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A recent completed refresh must not trigger the watchdog."""
    coordinator = watchdog_setup.runtime_data
    coordinator._last_http_refresh_completed_monotonic = (  # noqa: SLF001
        time.monotonic() - _FRESH_AGE_SEC
    )

    with caplog.at_level(
        logging.WARNING,
        logger="custom_components.jackery_solarvault.coordinator",
    ):
        await _tick_watchdog(hass)

    assert not any(
        "poll watchdog" in record.getMessage().lower() for record in caplog.records
    )
