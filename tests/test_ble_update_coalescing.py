"""BLE update-coalescing regression tests for coordinator partial updates."""

import re
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


def test_push_partial_update_is_noop_when_data_unchanged() -> None:
    """Avoid listener churn by skipping no-op partial updates."""
    src = _source()
    helper = re.search(
        r"def _push_partial_update\(.*?(?=\n    # --)",
        src,
        re.S,
    )
    assert helper is not None
    body = helper.group(0)
    assert "if self.data == new_data:" in body
    assert "self.async_set_updated_data(new_data)" in body
