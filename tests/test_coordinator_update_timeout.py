"""A hung poll cycle becomes UpdateFailed instead of freezing the schedule.

Owner: recurring "polling festhängt / pausiert". HA's DataUpdateCoordinator
does not schedule the next refresh until ``_async_update_data`` returns, so a
single await that never returns freezes polling forever. The update is now
bounded by ``COORDINATOR_UPDATE_TIMEOUT_SEC``; a timeout raises UpdateFailed so
HA logs it and reschedules the next cycle.
"""

import asyncio
from datetime import timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

_MODULE = "custom_components.jackery_solarvault.coordinator"
_POLL_INTERVAL_SEC = 15.0
_BOOTSTRAP_TIMEOUT_SEC = 90.0
_SHORT_CYCLE_ELAPSED_SEC = 2.0
_HA_MAX_SCHEDULER_STAGGER_SEC = 0.5
_MAX_SHORT_CYCLE_FOLLOWUP_DELAY_SEC = _POLL_INTERVAL_SEC - _SHORT_CYCLE_ELAPSED_SEC


def _bare_coordinator() -> JackerySolarVaultCoordinator:
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    # HA's DataUpdateCoordinator.__init__ seeds ``data`` to ``None`` before the
    # first successful cycle; the guard reads ``self.data`` on its failure paths,
    # so a cold coordinator must expose that same initial state.
    obj = cast("Any", coordinator)
    obj.data = None
    obj._shutdown_started = False
    obj._active_http_update_tasks = set()
    obj._configured_update_interval = timedelta(seconds=_POLL_INTERVAL_SEC)
    obj._last_http_cycle_started_monotonic = float("-inf")
    obj._polling_diagnostics = {}
    obj._device_index = {}
    obj._device_registry_observer = None
    return coordinator


@pytest.mark.asyncio
async def test_normal_cycle_returns_guarded_result() -> None:
    """When the guarded update completes, its result passes straight through."""
    coordinator = _bare_coordinator()
    data: dict[str, dict[str, Any]] = {"dev-1": {"soc": 80}}
    cast("Any", coordinator)._async_update_data_guarded = AsyncMock(
        return_value=data,
    )

    result = await coordinator._async_update_data()

    assert result == data


@pytest.mark.asyncio
async def test_hung_cycle_raises_update_failed() -> None:
    """A cycle that exceeds the ceiling is turned into UpdateFailed."""
    coordinator = _bare_coordinator()

    async def _hang() -> dict[str, dict[str, Any]]:
        await asyncio.sleep(1)
        return {}

    cast("Any", coordinator)._async_update_data_guarded = _hang

    with (
        patch(f"{_MODULE}.COORDINATOR_UPDATE_TIMEOUT_SEC", 0.01),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_cold_auth_failure_starts_reauth_and_propagates() -> None:
    """A cold coordinator propagates an auth failure after starting reauth.

    A running coordinator with data keeps its last state during reauth, but the
    first refresh has no usable state. Home Assistant requires the auth error to
    propagate so config-entry setup opens the reauthentication flow.
    """
    coordinator = _bare_coordinator()
    cast("Any", coordinator)._async_update_data_guarded = AsyncMock(
        side_effect=ConfigEntryAuthFailed("bad-credentials"),
    )
    entry = MagicMock()
    hass = MagicMock()
    cast("Any", coordinator).entry = entry
    cast("Any", coordinator).hass = hass

    with pytest.raises(ConfigEntryAuthFailed, match="bad-credentials"):
        await coordinator._async_update_data()

    entry.async_start_reauth.assert_called_once_with(hass)


def test_completed_cycle_shortens_followup_delay_to_keep_start_cadence() -> None:
    """The next HA interval consumes only the unused part of the 15 s budget."""
    coordinator = _bare_coordinator()

    coordinator._set_next_poll_delay(
        100.0,
        100.0 + _SHORT_CYCLE_ELAPSED_SEC,
    )

    assert coordinator.update_interval is not None
    assert (
        0
        < coordinator.update_interval.total_seconds()
        < _MAX_SHORT_CYCLE_FOLLOWUP_DELAY_SEC
    )
    diagnostics = coordinator.polling_diagnostics
    assert diagnostics["last_total_cycle_elapsed_sec"] == pytest.approx(
        _SHORT_CYCLE_ELAPSED_SEC
    )
    assert diagnostics["next_poll_delay_sec"] < _MAX_SHORT_CYCLE_FOLLOWUP_DELAY_SEC


def test_cold_first_refresh_keeps_bootstrap_timeout() -> None:
    """Initial login/discovery may use the bootstrap ceiling before cadence exists."""
    coordinator = _bare_coordinator()

    assert coordinator._poll_cycle_timeout_seconds() == pytest.approx(
        _BOOTSTRAP_TIMEOUT_SEC
    )
