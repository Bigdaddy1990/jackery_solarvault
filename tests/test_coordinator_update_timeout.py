"""A hung poll cycle becomes UpdateFailed instead of freezing the schedule.

Owner: recurring "polling festhängt / pausiert". HA's DataUpdateCoordinator
does not schedule the next refresh until ``_async_update_data`` returns, so a
single await that never returns freezes polling forever. The update is now
bounded by ``COORDINATOR_UPDATE_TIMEOUT_SEC``; a timeout raises UpdateFailed so
HA logs it and reschedules the next cycle.
"""

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

_MODULE = "custom_components.jackery_solarvault.coordinator"


def _bare_coordinator() -> JackerySolarVaultCoordinator:
    return JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)


@pytest.mark.asyncio()
async def test_normal_cycle_returns_guarded_result() -> None:
    """When the guarded update completes, its result passes straight through."""
    coordinator = _bare_coordinator()
    data: dict[str, dict[str, Any]] = {"dev-1": {"soc": 80}}
    cast("Any", coordinator)._async_update_data_guarded = AsyncMock(  # noqa: SLF001
        return_value=data,
    )

    result = await coordinator._async_update_data()  # noqa: SLF001

    assert result == data


@pytest.mark.asyncio()
async def test_hung_cycle_raises_update_failed() -> None:
    """A cycle that exceeds the ceiling is turned into UpdateFailed."""
    coordinator = _bare_coordinator()

    async def _hang() -> dict[str, dict[str, Any]]:
        await asyncio.sleep(1)
        return {}

    cast("Any", coordinator)._async_update_data_guarded = _hang  # noqa: SLF001

    with (
        patch(f"{_MODULE}.COORDINATOR_UPDATE_TIMEOUT_SEC", 0.01),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()  # noqa: SLF001


@pytest.mark.asyncio()
async def test_auth_failure_starts_reauth_but_keeps_polling() -> None:
    """A poll auth failure must trigger reauth WITHOUT stopping the coordinator.

    Raising ConfigEntryAuthFailed out of the coordinator makes HA stop polling
    until reauth completes (the "polling dead for minutes" symptom). The wrapper
    instead starts the reauth flow non-blockingly and raises UpdateFailed so HA
    keeps polling on the normal interval.
    """
    coordinator = _bare_coordinator()
    cast("Any", coordinator)._async_update_data_guarded = AsyncMock(  # noqa: SLF001
        side_effect=ConfigEntryAuthFailed("bad-credentials"),
    )
    entry = MagicMock()
    hass = MagicMock()
    cast("Any", coordinator).entry = entry
    cast("Any", coordinator).hass = hass

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()  # noqa: SLF001

    entry.async_start_reauth.assert_called_once_with(hass)
