"""Unload never hangs on a stuck transport teardown (owner live capture 2026-07-05).

The unload log showed ~78 s between "startup task cancelled during teardown"
and the next setup — the entry was wedged in ``coordinator.async_shutdown()``
(an un-bounded bleak GATT disconnect / aiomqtt close / getaddrinfo-stuck task).
Every options-reload therefore froze polling for that whole window. The
shutdown call is now time-bounded; a hang becomes a warning and the unload
proceeds.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault import async_unload_entry
from custom_components.jackery_solarvault.const import DOMAIN
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_MODULE = "custom_components.jackery_solarvault"


@pytest.mark.asyncio()
async def test_unload_bounds_a_hung_shutdown() -> None:
    """A shutdown that never returns must not block the unload."""
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)

    async def _hang() -> None:
        await asyncio.sleep(1)

    coordinator.async_shutdown = _hang

    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.runtime_data = coordinator

    hass = MagicMock()
    hass.data = {DOMAIN: {}}
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    with (
        patch(f"{_MODULE}.COORDINATOR_SHUTDOWN_TIMEOUT_SEC", 0.01),
        patch(f"{_MODULE}._async_cancel_startup_task", AsyncMock()),
    ):
        result = await async_unload_entry(hass, entry)

    assert result is True
    hass.config_entries.async_unload_platforms.assert_awaited_once()
