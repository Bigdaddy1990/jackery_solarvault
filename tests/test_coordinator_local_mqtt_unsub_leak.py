"""Regression tests for local MQTT listener cleanup during shutdown.

``async_start_local_mqtt_listener()`` appends each ``mqtt.async_subscribe``
unsubscribe callback to ``_local_mqtt_unsubs``. The tests preserve the fix that
drains every callback from ``async_shutdown()`` so an options-driven reload does
not retain the prior coordinator's live HA-MQTT subscriptions.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)


def _bare_coordinator() -> JackerySolarVaultCoordinator:
    """Build a coordinator shell exercising only the shutdown teardown path."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._shutdown_started = False
    coordinator._base_shutdown_task = asyncio.create_task(asyncio.sleep(0))
    coordinator._poll_watchdog_unsub = None
    coordinator._statistics_import_task = None
    coordinator._slow_metrics_bg_task = None
    coordinator._mqtt_poll_task = None
    coordinator._shadow_fallback_task = None
    coordinator._battery_pack_ota_tasks = {}
    coordinator._ble_coalesce_tasks = {}
    coordinator._local_mqtt_message_tasks = set()
    coordinator._background_tasks = {}
    coordinator._ble_pending_updates = {}
    coordinator._active_http_update_tasks = set()
    coordinator._topology_reload_task = None
    coordinator._mqtt = None
    coordinator._ble_listener = None
    coordinator._local_mqtt_unsubs = []
    return coordinator


@pytest.mark.asyncio()
async def test_shutdown_drains_local_mqtt_unsubs() -> None:
    """Every registered HA-MQTT unsub callable must fire on shutdown."""
    coordinator = _bare_coordinator()
    unsub_one = MagicMock()
    unsub_two = MagicMock()
    coordinator._local_mqtt_unsubs = [unsub_one, unsub_two]

    await coordinator.async_shutdown()

    unsub_one.assert_called_once()
    unsub_two.assert_called_once()
    assert coordinator._local_mqtt_unsubs == []


@pytest.mark.asyncio()
async def test_shutdown_tolerates_a_raising_unsub_callable() -> None:
    """A bad unsub must not stop the others from being drained.

    Mirrors the existing subscribe-failure branch's
    ``contextlib.suppress(Exception)`` tolerance so one broken callable
    cannot leave the rest of the topics subscribed forever.
    """
    coordinator = _bare_coordinator()
    unsub_ok = MagicMock()
    unsub_bad = MagicMock(side_effect=RuntimeError("boom"))
    coordinator._local_mqtt_unsubs = [unsub_bad, unsub_ok]

    await coordinator.async_shutdown()

    unsub_ok.assert_called_once()
    assert coordinator._local_mqtt_unsubs == []


@pytest.mark.asyncio()
async def test_shutdown_is_a_noop_when_no_local_mqtt_subscriptions_exist() -> None:
    """Shutdown must not raise when the local MQTT listener never started."""
    coordinator = _bare_coordinator()

    await coordinator.async_shutdown()

    assert coordinator._local_mqtt_unsubs == []
