"""BLE update-coalescing regression tests for coordinator partial updates."""

from pathlib import Path


def _source() -> str:
    return Path("custom_components/jackery_solarvault/coordinator.py").read_text(
        encoding="utf-8",
    )


def test_ble_partial_updates_use_coalescing_queue() -> None:
    """Coordinator should queue BLE partial updates and flush them in bursts."""
    src = _source()
    assert "_BLE_PARTIAL_UPDATE_COALESCE_SEC = " in src
    assert "def _schedule_ble_partial_update(" in src
    assert "async def _async_flush_ble_partial_update(" in src
    assert "await asyncio.sleep(_BLE_PARTIAL_UPDATE_COALESCE_SEC)" in src


def test_ble_sink_schedules_coalesced_partial_update() -> None:
    """BLE sink path should call the coalescing scheduler instead of direct push."""
    src = _source()
    assert "_schedule_ble_partial_update(device_id, updated)" in src
