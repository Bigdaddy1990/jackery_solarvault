"""Malformed endpoint and historical metadata must keep safe retry behavior."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from custom_components.jackery_solarvault import coordinator as coordinator_module
from custom_components.jackery_solarvault.client.api import JackeryApiError
from custom_components.jackery_solarvault.const import APP_SECTION_PV_STAT
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    _HttpDayBackfillCandidate,  # ruff: ignore[import-private-name]
    _HttpDayBackfillProgress,  # ruff: ignore[import-private-name]
)

_DEVICE_ID = "history-device"
_TODAY = date(2026, 10, 7)
_RECORDED_DAY = date(2025, 12, 1)


def _coordinator() -> JackerySolarVaultCoordinator:
    """Initialize only the real state used by the synchronous policy helpers."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator).hass = SimpleNamespace(
        config=SimpleNamespace(time_zone="UTC")
    )
    coordinator._endpoint_backoff = {}  # ruff: ignore[private-member-access]
    coordinator._statistics_backfill_state = {  # ruff: ignore[private-member-access]
        "devices": {}
    }
    return coordinator


def test_excessively_long_error_code_does_not_create_endpoint_backoff() -> None:
    """A digit-only code rejected by Python's conversion limit is ignored safely."""
    coordinator = _coordinator()
    long_code = 5000 * "9"
    error = JackeryApiError(f"cloud error code={long_code}")

    recorded = coordinator._endpoint_backoff_note_failure(  # ruff: ignore[private-member-access]
        "dev:history-device:pv_stat:day", error
    )

    assert recorded is False
    assert coordinator._endpoint_backoff == {}  # ruff: ignore[private-member-access]


@pytest.mark.parametrize("status", ["empty", "unmapped"])
@pytest.mark.parametrize(
    "last_attempt",
    [{}, {"last_attempt_at": None}, {"last_attempt_at": "invalid"}],
)
def test_malformed_checked_day_attempt_does_not_repeat_cloud_request(
    status: str, last_attempt: dict[str, Any]
) -> None:
    """Missing, non-string and invalid timestamps preserve a checked day's status."""
    coordinator = _coordinator()
    day_state = {"status": status, "checked_date": _TODAY.isoformat(), **last_attempt}
    candidate = _HttpDayBackfillCandidate(
        priority=1,
        attempted=1,
        last_attempt="",
        target_day=date(2026, 10, 6),
        attempts=1,
        device_id=_DEVICE_ID,
        section_prefix=APP_SECTION_PV_STAT,
        payload={},
        day_state=day_state,
        days_state={},
    )
    progress = _HttpDayBackfillProgress(
        target_days=[candidate.target_day],
        force=False,
        window_days=1,
        include_current_year=True,
        now_monotonic=0.0,
    )

    selected = coordinator._http_day_backfill_candidate(  # ruff: ignore[private-member-access]
        progress,
        candidate,
        week_start=date(2026, 10, 5),
        today=_TODAY,
        now_epoch=datetime(2026, 10, 7, 4, tzinfo=UTC).timestamp(),
    )

    assert selected is None
    assert day_state == {
        "status": status,
        "checked_date": _TODAY.isoformat(),
        **last_attempt,
    }
    assert progress.pending_sources == 0
    assert progress.state_changed is False


@pytest.mark.parametrize("created", [None, [], "not-a-date", float("inf")])
def test_malformed_creation_metadata_retains_imported_history(created: Any) -> None:
    """Broken optional creation fields cannot erase valid previous-year imports."""
    coordinator = _coordinator()
    state = coordinator._statistics_backfill_device_state(_DEVICE_ID)  # ruff: ignore[private-member-access]
    state["http_day_backfill"] = {
        "sources": {
            APP_SECTION_PV_STAT: {
                "days": {_RECORDED_DAY.isoformat(): {"status": "imported"}}
            }
        }
    }

    start = coordinator._statistics_history_start(  # ruff: ignore[private-member-access]
        {_DEVICE_ID: {"device": {"createTime": created}}}, _TODAY
    )

    assert start == _RECORDED_DAY


@pytest.mark.parametrize("error_type", [TypeError, ValueError, OverflowError, OSError])
def test_creation_parser_failure_retains_valid_system_creation_date(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    """All supported parser failures leave independent valid system metadata usable."""
    coordinator = _coordinator()
    parser = Mock(
        side_effect=[
            error_type("invalid device timestamp"),
            datetime(2025, 12, 1, tzinfo=UTC),
        ]
    )
    monkeypatch.setattr(coordinator_module, "parse_utc_datetime", parser)
    snapshot = {
        _DEVICE_ID: {
            "device": {"createTime": "broken"},
            "system": {"createTime": "2025-12-01T00:00:00Z"},
        }
    }

    start = coordinator._statistics_history_start(snapshot, _TODAY)  # ruff: ignore[private-member-access]

    assert start == _RECORDED_DAY
    assert parser.call_count == 2  # ruff: ignore[magic-value-comparison]


@pytest.mark.parametrize("bad_key", [None, 123, "invalid-date"])
@pytest.mark.parametrize("queue_name", ["http_day_backfill", "http_period_backfill"])
def test_malformed_imported_date_does_not_discard_valid_history(
    bad_key: Any, queue_name: str
) -> None:
    """Invalid legacy date keys do not prevent finding an independent valid import."""
    coordinator = _coordinator()
    state = coordinator._statistics_backfill_device_state(_DEVICE_ID)  # ruff: ignore[private-member-access]
    state[queue_name] = {
        "sources": {
            APP_SECTION_PV_STAT: {
                "days": {
                    bad_key: {"status": "imported"},
                    _RECORDED_DAY.isoformat(): {"status": "imported"},
                    "2024-01-01": {"status": "pending"},
                }
            }
        }
    }

    start = coordinator._statistics_history_start({_DEVICE_ID: {}}, _TODAY)  # ruff: ignore[private-member-access]

    assert start == _RECORDED_DAY
