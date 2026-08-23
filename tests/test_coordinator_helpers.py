"""Behavioral unit tests for coordinator pure helper functions.

These exercise the safe enrichment, payload signature, slow-fetch log level,
control coercion, transport command parsing, and dict merging helpers
without any Home Assistant recorder dependency.
"""

from collections import deque
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import coordinator as coordinator_module
from custom_components.jackery_solarvault.client import JackeryAuthError, JackeryError
from custom_components.jackery_solarvault.const import (
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
)
from custom_components.jackery_solarvault.coordinator import (
    _SYSTEM_BUSY_API_CODE,
    BackfillStatus,
    JackerySolarVaultCoordinator,
    _backfill_period_is_closed,
    _is_system_busy_error,
    _merge_identified_dict_lists,
    _normalize_backfill_status,
    _safe_enrich,
    _slow_fetch_failure_log_level,
    _stable_payload_debug_signature,
    control_int,
    merge_missing_dict_values,
    merge_present_dict_values,
    transport_cmd,
)

# ---------------------------------------------------------------------------
# _safe_enrich
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safe_enrich_runs_enrichment_on_success() -> None:
    """Successful enrichment function is awaited."""
    mock_enrich = AsyncMock()
    dev_id = "test-device"
    entry = {"key": "value"}
    await _safe_enrich(dev_id, entry, mock_enrich, stale_ok=True)
    mock_enrich.assert_awaited_once_with(dev_id, entry, stale_ok=True)


@pytest.mark.asyncio
async def test_safe_enrich_catches_jackery_auth_error() -> None:
    """JackeryAuthError is caught and logged at DEBUG level."""
    auth_err = JackeryAuthError("unauthorized")
    mock_enrich = AsyncMock(side_effect=auth_err)
    dev_id = "test-device"
    entry = {"key": "value"}

    # Should not raise
    await _safe_enrich(dev_id, entry, mock_enrich, stale_ok=True)

    mock_enrich.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_enrich_catches_timeout_error() -> None:
    """TimeoutError is caught and logged at DEBUG level."""
    timeout_err = TimeoutError("timeout")
    mock_enrich = AsyncMock(side_effect=timeout_err)
    dev_id = "test-device"
    entry = {"key": "value"}

    await _safe_enrich(dev_id, entry, mock_enrich, stale_ok=True)

    mock_enrich.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_enrich_catches_jackery_error() -> None:
    """JackeryError is caught and logged at DEBUG level."""
    jackery_err = JackeryError("api error")
    mock_enrich = AsyncMock(side_effect=jackery_err)
    dev_id = "test-device"
    entry = {"key": "value"}

    await _safe_enrich(dev_id, entry, mock_enrich, stale_ok=True)

    mock_enrich.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_enrich_passes_stale_ok_false() -> None:
    """stale_ok=False is passed through to enrichment function."""
    mock_enrich = AsyncMock()
    dev_id = "test-device"
    entry = {"key": "value"}
    await _safe_enrich(dev_id, entry, mock_enrich, stale_ok=False)
    mock_enrich.assert_awaited_once_with(dev_id, entry, stale_ok=False)


# ---------------------------------------------------------------------------
# _stable_payload_debug_signature
# ---------------------------------------------------------------------------


def test_stable_payload_debug_signature_basic() -> None:
    """Basic event produces deterministic signature."""
    event = {
        "kind": "request",
        "topic": "/v1/device/stat/ct",
        "payload": {"messageType": "stat", "body": {"key": "value"}},
        "body_type": "json",
        "data_type": "ct_stat",
        "response_data_type": "json",
        "status": 200,
        "response": {"data": {"result": "ok"}},
    }
    sig = _stable_payload_debug_signature(event)
    assert isinstance(sig, str)
    assert len(sig) > 0


def test_stable_payload_debug_signature_excludes_per_message_ids() -> None:
    """id, timestamp, messageId, entry_id are excluded from signature."""
    event1 = {
        "kind": "request",
        "topic": "/v1/device/stat/ct",
        "id": "msg-1",
        "timestamp": "2024-01-01T00:00:00Z",
        "payload": {
            "messageType": "stat",
            "body": {"key": "value"},
            "messageId": "mid-1",
        },
        "entry_id": "entry-1",
        "status": 200,
    }
    event2 = {
        "kind": "request",
        "topic": "/v1/device/stat/ct",
        "id": "msg-2",
        "timestamp": "2024-01-01T00:00:01Z",
        "payload": {
            "messageType": "stat",
            "body": {"key": "value"},
            "messageId": "mid-2",
        },
        "entry_id": "entry-2",
        "status": 200,
    }
    # Same content, different per-message fields -> same signature
    assert _stable_payload_debug_signature(event1) == _stable_payload_debug_signature(
        event2
    )


def test_stable_payload_debug_signature_different_body_yields_different_sig() -> None:
    """Different body content produces different signature."""
    event1 = {
        "kind": "request",
        "topic": "/v1/device/stat/ct",
        "payload": {"messageType": "stat", "body": {"value": 1}},
        "status": 200,
    }
    event2 = {
        "kind": "request",
        "topic": "/v1/device/stat/ct",
        "payload": {"messageType": "stat", "body": {"value": 2}},
        "status": 200,
    }
    assert _stable_payload_debug_signature(event1) != _stable_payload_debug_signature(
        event2
    )


def test_stable_payload_debug_signature_handles_missing_fields() -> None:
    """Missing optional fields don't crash."""
    event = {"kind": "request", "topic": "/v1/test"}
    sig = _stable_payload_debug_signature(event)
    assert isinstance(sig, str)


def test_stable_payload_debug_signature_response_data_type_handling() -> None:
    """response.data_type is included in signature."""
    event1 = {
        "kind": "response",
        "topic": "/v1/test",
        "response_data_type": "json",
        "status": 200,
    }
    event2 = {
        "kind": "response",
        "topic": "/v1/test",
        "response_data_type": "text",
        "status": 200,
    }
    assert _stable_payload_debug_signature(event1) != _stable_payload_debug_signature(
        event2
    )


# ---------------------------------------------------------------------------
# _slow_fetch_failure_log_level
# ---------------------------------------------------------------------------


def test_slow_fetch_failure_log_level_shelly_realtime_returns_warning() -> None:
    """Shelly realtime fetch errors return WARNING level."""
    err = JackeryError("shelly_realtime fetch failed")
    level = _slow_fetch_failure_log_level(err, suppressed=True)
    assert level == 30  # logging.WARNING


def test_slow_fetch_failure_log_level_shelly_realtime_case_insensitive() -> None:
    """Shelly realtime detection is case-insensitive."""
    err = JackeryError("REALTIME-POWER error")
    level = _slow_fetch_failure_log_level(err, suppressed=True)
    assert level == 30  # logging.WARNING


def test_slow_fetch_failure_log_level_suppressed_returns_debug() -> None:
    """Suppressed non-Shelly errors return DEBUG level."""
    err = JackeryError("some other error")
    level = _slow_fetch_failure_log_level(err, suppressed=True)
    assert level == 10  # logging.DEBUG


def test_slow_fetch_failure_log_level_timeout_cause_returns_debug() -> None:
    """TimeoutError cause returns DEBUG level."""
    err = JackeryError("timeout")
    err.__cause__ = TimeoutError("timeout")
    level = _slow_fetch_failure_log_level(err, suppressed=False)
    assert level == 10  # logging.DEBUG


def test_slow_fetch_failure_log_level_non_suppressed_returns_warning() -> None:
    """Non-suppressed, non-timeout, non-Shelly errors return WARNING."""
    err = JackeryError("api error")
    level = _slow_fetch_failure_log_level(err, suppressed=False)
    assert level == 30  # logging.WARNING


# ---------------------------------------------------------------------------
# control_int
# ---------------------------------------------------------------------------


def test_control_int_valid_string() -> None:
    """Valid integer string returns int."""
    assert control_int("42", "test_field") == 42


def test_control_int_valid_int() -> None:
    """Valid integer returns same int."""
    assert control_int(42, "test_field") == 42


def test_control_int_valid_integral_float() -> None:
    """Valid integral float converts to int."""
    assert control_int(42.0, "test_field") == 42


def test_control_int_non_integral_float_raises() -> None:
    """Non-integral float raises UpdateFailed."""
    from custom_components.jackery_solarvault.coordinator import UpdateFailed

    with pytest.raises(UpdateFailed, match="Invalid test_field"):
        control_int(42.7, "test_field")


def test_control_int_bool_raises() -> None:
    """Boolean raises UpdateFailed."""
    from custom_components.jackery_solarvault.coordinator import UpdateFailed

    with pytest.raises(UpdateFailed, match="Invalid test_field"):
        control_int(True, "test_field")
    with pytest.raises(UpdateFailed, match="Invalid test_field"):
        control_int(False, "test_field")


def test_control_int_none_raises() -> None:
    """None raises UpdateFailed."""
    from custom_components.jackery_solarvault.coordinator import UpdateFailed

    with pytest.raises(UpdateFailed, match="Invalid test_field"):
        control_int(None, "test_field")


def test_control_int_invalid_string_raises() -> None:
    """Invalid string raises UpdateFailed."""
    from custom_components.jackery_solarvault.coordinator import UpdateFailed

    with pytest.raises(UpdateFailed, match="Invalid test_field"):
        control_int("not-an-int", "test_field")


# ---------------------------------------------------------------------------
# transport_cmd
# ---------------------------------------------------------------------------


def test_transport_cmd_valid_string() -> None:
    """Valid command string returns int."""
    assert transport_cmd("113") == 113


def test_transport_cmd_valid_int() -> None:
    """Valid integer returns same int."""
    assert transport_cmd(113) == 113


def test_transport_cmd_invalid_string_raises() -> None:
    """Invalid string raises ValueError."""
    with pytest.raises(ValueError, match="cmd must be an integer"):
        transport_cmd("not-a-cmd")


def test_transport_cmd_none_raises() -> None:
    """None raises ValueError."""
    with pytest.raises(ValueError, match="cmd must be an integer"):
        transport_cmd(None)


# ---------------------------------------------------------------------------
# _merge_identified_dict_lists
# ---------------------------------------------------------------------------


def test_merge_identified_dict_lists_basic() -> None:
    """Basic merge of identified dict lists."""
    current = [
        {"id": "1", "name": "A", "value": 10},
        {"id": "2", "name": "B", "value": 20},
    ]
    updates = [
        {"id": "1", "value": 15},  # update existing
        {"id": "3", "name": "C", "value": 30},  # add new
    ]
    result = _merge_identified_dict_lists(current, updates)
    assert result is not None
    assert len(result) == 3
    # Find updated item
    item1 = next(item for item in result if item["id"] == "1")
    assert item1["value"] == 15
    assert item1["name"] == "A"  # preserved
    # Find new item
    item3 = next(item for item in result if item["id"] == "3")
    assert item3["name"] == "C"
    assert item3["value"] == 30


def test_merge_identified_dict_lists_non_dict_current_returns_none() -> None:
    """Non-dict items in current returns None."""
    current = [{"id": "1"}, "not-a-dict"]
    updates = [{"id": "1"}]
    assert _merge_identified_dict_lists(current, updates) is None


def test_merge_identified_dict_lists_non_dict_updates_returns_none() -> None:
    """Non-dict items in updates returns None."""
    current = [{"id": "1"}]
    updates = [{"id": "1"}, "not-a-dict"]
    assert _merge_identified_dict_lists(current, updates) is None


def test_merge_identified_dict_lists_empty_updates_returns_none() -> None:
    """Empty updates returns None."""
    current = [{"id": "1"}]
    updates = []
    assert _merge_identified_dict_lists(current, updates) is None


def test_merge_identified_dict_lists_missing_identity_returns_none() -> None:
    """Update without identity returns None."""
    current = [{"id": "1", "value": 10}]
    updates = [{"name": "no-id"}]  # no serial or id key
    assert _merge_identified_dict_lists(current, updates) is None


def test_merge_identified_dict_lists_serial_key() -> None:
    """Merge uses serial key for identity."""
    current = [{"devSn": "sn-1", "value": 10}]
    updates = [{"devSn": "sn-1", "value": 20}]
    result = _merge_identified_dict_lists(current, updates)
    assert result is not None
    assert result[0]["value"] == 20


# ---------------------------------------------------------------------------
# merge_present_dict_values (additional tests)
# ---------------------------------------------------------------------------


def test_merge_present_dict_values_nested_dict() -> None:
    """Nested dicts are merged recursively."""
    base = {"config": {"mode": "auto", "threshold": 100}}
    update = {"config": {"threshold": 200}}
    merged = merge_present_dict_values(base, update)
    assert merged["config"]["mode"] == "auto"  # preserved
    assert merged["config"]["threshold"] == 200  # updated


def test_merge_present_dict_values_blank_value_preserved() -> None:
    """Blank update value preserves existing value."""
    base = {"name": "device", "value": 42}
    update = {"value": None, "name": ""}  # blank values
    merged = merge_present_dict_values(base, update)
    assert merged["name"] == "device"
    assert merged["value"] == 42


def test_merge_present_dict_values_list_merge() -> None:
    """List of dicts with identity is merged."""
    base = {"devices": [{"id": "1", "power": 10}]}
    update = {"devices": [{"id": "1", "power": 20}, {"id": "2", "power": 30}]}
    merged = merge_present_dict_values(base, update)
    assert len(merged["devices"]) == 2
    assert merged["devices"][0]["power"] == 20
    assert merged["devices"][1]["power"] == 30


# ---------------------------------------------------------------------------
# merge_missing_dict_values
# ---------------------------------------------------------------------------


def test_merge_missing_dict_values_fills_absent() -> None:
    """Absent keys are filled from updates."""
    base = {"existing": "value"}
    update = {"new_key": "new_value", "also_new": 123}
    merged = merge_missing_dict_values(base, update)
    assert merged["existing"] == "value"
    assert merged["new_key"] == "new_value"
    assert merged["also_new"] == 123


def test_merge_missing_dict_values_empty_string_filled() -> None:
    """Empty string current value is filled from update."""
    base = {"name": "", "value": 42}
    update = {"name": "filled", "value": 100}
    merged = merge_missing_dict_values(base, update)
    assert merged["name"] == "filled"
    assert merged["value"] == 42  # 42 is not blank, preserved


def test_merge_missing_dict_values_none_filled() -> None:
    """None current value is filled from update."""
    base = {"name": None, "value": 42}
    update = {"name": "filled", "value": 100}
    merged = merge_missing_dict_values(base, update)
    assert merged["name"] == "filled"
    assert merged["value"] == 42


def test_merge_missing_dict_values_empty_list_filled() -> None:
    """Empty list current value is filled from update."""
    base = {"items": [], "value": 42}
    update = {"items": [1, 2, 3], "value": 100}
    merged = merge_missing_dict_values(base, update)
    assert merged["items"] == [1, 2, 3]
    assert merged["value"] == 42


def test_merge_missing_dict_values_populated_preserved() -> None:
    """Populated current value is preserved."""
    base = {"name": "original", "value": 100}
    update = {"name": "update", "value": 200}
    merged = merge_missing_dict_values(base, update)
    assert merged["name"] == "original"
    assert merged["value"] == 100


def test_merge_missing_dict_values_nested_dict() -> None:
    """Nested dicts are merged recursively."""
    base = {"config": {"mode": "auto"}}
    update = {"config": {"threshold": 50}}
    merged = merge_missing_dict_values(base, update)
    assert merged["config"]["mode"] == "auto"
    assert merged["config"]["threshold"] == 50


def test_merge_missing_dict_values_deepcopy() -> None:
    """Update values are deep copied."""
    base = {}
    update = {"list": [1, 2, 3]}
    merged = merge_missing_dict_values(base, update)
    update["list"].append(4)
    assert merged["list"] == [1, 2, 3]  # not affected by mutation


# ---------------------------------------------------------------------------
# _backfill_period_is_closed
# ---------------------------------------------------------------------------


def test_backfill_period_is_closed_day_before_today() -> None:
    """Day bucket before today is closed."""
    today = date(2024, 6, 15)
    assert (
        _backfill_period_is_closed(DATE_TYPE_DAY, date(2024, 6, 14), today=today)
        is True
    )
    assert (
        _backfill_period_is_closed(DATE_TYPE_DAY, date(2024, 6, 15), today=today)
        is False
    )
    assert (
        _backfill_period_is_closed(DATE_TYPE_DAY, date(2024, 6, 16), today=today)
        is False
    )


def test_backfill_period_is_closed_week_before_today() -> None:
    """Week bucket before today is closed."""
    today = date(2024, 6, 15)  # Saturday
    # Week starting June 9 ends June 15
    assert (
        _backfill_period_is_closed(DATE_TYPE_WEEK, date(2024, 6, 9), today=today)
        is False
    )
    # Week starting June 2 ends June 8
    assert (
        _backfill_period_is_closed(DATE_TYPE_WEEK, date(2024, 6, 2), today=today)
        is True
    )


def test_backfill_period_is_closed_month_before_today() -> None:
    """Month bucket before today is closed."""
    today = date(2024, 6, 15)
    assert (
        _backfill_period_is_closed(DATE_TYPE_MONTH, date(2024, 5, 1), today=today)
        is True
    )
    assert (
        _backfill_period_is_closed(DATE_TYPE_MONTH, date(2024, 6, 1), today=today)
        is False
    )


def test_backfill_period_is_closed_year_before_today() -> None:
    """Year bucket before today is closed."""
    today = date(2024, 6, 15)
    assert (
        _backfill_period_is_closed(DATE_TYPE_YEAR, date(2023, 1, 1), today=today)
        is True
    )
    assert (
        _backfill_period_is_closed(DATE_TYPE_YEAR, date(2024, 1, 1), today=today)
        is False
    )


def test_backfill_period_is_closed_unknown_type_returns_false() -> None:
    """Unknown date type returns False (no closure assumed)."""
    today = date(2024, 6, 15)
    assert _backfill_period_is_closed("unknown", date(2024, 1, 1), today=today) is False


# ---------------------------------------------------------------------------
# _normalize_backfill_status
# ---------------------------------------------------------------------------


def test_normalize_backfill_status_valid_enum() -> None:
    """Valid BackfillStatus string maps to enum."""
    assert _normalize_backfill_status("pending", closed=False) == BackfillStatus.PENDING
    assert (
        _normalize_backfill_status("retryable", closed=False)
        == BackfillStatus.RETRYABLE
    )
    # "imported" on open bucket becomes RETRYABLE per line 900-901
    assert (
        _normalize_backfill_status("imported", closed=False) == BackfillStatus.RETRYABLE
    )


def test_normalize_backfill_status_legacy_values_map_to_retryable() -> None:
    """Legacy cache values map to RETRYABLE."""
    for legacy in [
        "auth_error",
        "deferred",
        "empty_ambiguous",
        "fetched",
        "recorder_error",
        "transport_error",
        "unavailable",
    ]:
        assert (
            _normalize_backfill_status(legacy, closed=False) == BackfillStatus.RETRYABLE
        )


def test_normalize_backfill_status_unknown_value_maps_to_pending() -> None:
    """Unknown string maps to PENDING."""
    assert (
        _normalize_backfill_status("completely_unknown", closed=False)
        == BackfillStatus.PENDING
    )


def test_normalize_backfill_status_imported_on_closed_returns_imported() -> None:
    """IMPORTED on closed bucket stays IMPORTED (line 900-902)."""
    assert (
        _normalize_backfill_status("imported", closed=True) == BackfillStatus.IMPORTED
    )


def test_normalize_backfill_status_imported_on_open_returns_retryable() -> None:
    """IMPORTED on open bucket becomes RETRYABLE (line 900-901)."""
    assert (
        _normalize_backfill_status("imported", closed=False) == BackfillStatus.RETRYABLE
    )


def test_normalize_backfill_status_other_statuses_unaffected_by_closed() -> None:
    """Non-IMPORTED statuses are unaffected by closed flag."""
    assert _normalize_backfill_status("pending", closed=True) == BackfillStatus.PENDING
    assert (
        _normalize_backfill_status("retryable", closed=True) == BackfillStatus.RETRYABLE
    )


def test_normalize_backfill_status_non_string_input() -> None:
    """Non-string input (TypeError) maps to PENDING."""
    assert _normalize_backfill_status(None, closed=False) == BackfillStatus.PENDING
    assert _normalize_backfill_status(123, closed=False) == BackfillStatus.PENDING


# ---------------------------------------------------------------------------
# _is_system_busy_error
# ---------------------------------------------------------------------------


def test_is_system_busy_error_matches_code() -> None:
    """Error containing system busy code returns True."""
    err = JackeryError(f"code={_SYSTEM_BUSY_API_CODE} some message")
    assert _is_system_busy_error(err) is True


def test_is_system_busy_error_no_match_returns_false() -> None:
    """Error without system busy code returns False."""
    err = JackeryError("code=10422 some message")
    assert _is_system_busy_error(err) is False


def test_is_system_busy_error_non_jackery_error() -> None:
    """Non-JackeryError returns False."""
    err = TimeoutError("timeout")
    assert _is_system_busy_error(err) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


@pytest.mark.asyncio
async def test_mqtt_payload_debug_is_enqueued_without_waiting_for_disk() -> None:
    """A live MQTT frame must enqueue diagnostics instead of awaiting disk I/O."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    schedule = MagicMock()
    direct_writer = AsyncMock()
    coordinator._schedule_payload_debug_event = schedule
    coordinator._async_payload_debug_event = direct_writer
    coordinator._resolve_device_id_from_mqtt = MagicMock(return_value=None)

    raw_handler = JackerySolarVaultCoordinator._async_handle_mqtt_message.__wrapped__
    await raw_handler(coordinator, "hb/app/test", {})

    schedule.assert_called_once()
    direct_writer.assert_not_awaited()
    event = schedule.call_args.args[0]()
    assert event["kind"] == "cloud_mqtt"
    assert event["topic"] == "hb/app/test"


@pytest.mark.asyncio
async def test_payload_debug_shutdown_flush_writes_pending_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Shutdown must flush queued debug diagnostics before other tasks stop."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    executor_job = AsyncMock()
    coordinator.hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda filename: str(tmp_path / filename)),
        async_add_executor_job=executor_job,
    )
    coordinator.entry = SimpleNamespace(entry_id="test-entry")
    coordinator._background_tasks = {}
    coordinator._payload_debug_pending_events = deque([{"sequence": 1}])

    monkeypatch.setattr(
        coordinator_module,
        "_payload_debug_capture_enabled",
        lambda _entry: True,
    )

    await coordinator._async_flush_payload_debug_events()

    executor_job.assert_awaited_once()
    assert not coordinator._payload_debug_pending_events


@pytest.mark.asyncio
async def test_payload_debug_drain_uses_one_executor_batch_for_pending_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A burst must retain all debug events without one disk job per frame."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    executor_job = AsyncMock()
    coordinator.hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda filename: str(tmp_path / filename)),
        async_add_executor_job=executor_job,
    )
    coordinator.entry = SimpleNamespace(entry_id="test-entry")
    coordinator._payload_debug_pending_events = deque([
        {"sequence": 1},
        {"sequence": 2},
    ])

    monkeypatch.setattr(
        coordinator_module,
        "_payload_debug_capture_enabled",
        lambda _entry: True,
    )

    await coordinator._async_drain_payload_debug_events()

    executor_job.assert_awaited_once()
    writer, _path, events = executor_job.await_args.args
    assert writer.__name__ == "append_payload_debug_lines"
    assert [event["sequence"] for event in events] == [1, 2]
    assert all(event["entry_id"] == "test-entry" for event in events)
