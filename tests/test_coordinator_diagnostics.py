"""Behavioral tests for coordinator diagnostics and repair-decision helpers.

These exercise the redaction-safe diagnostics snapshots, the endpoint-backoff
bookkeeping (energy/stat keys must never be suppressed), the statistics-repair
start-date decision tree, and the discovery-source markers used when falling
back to cached discovery. All are pure lookups over in-memory coordinator state
— no I/O — so they assert real branch behavior directly.
"""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import coordinator as co
from custom_components.jackery_solarvault.const import (
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    DISCOVERY_SOURCE_SYSTEM_LIST,
    FIELD_DEVICE_SN,
    FIELD_SYSTEM_ID,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_META,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_Coordinator = JackerySolarVaultCoordinator


def _bare() -> Any:
    """Return an uninitialised coordinator shell for attribute injection."""
    return cast("Any", _Coordinator.__new__(_Coordinator))


# ---------------------------------------------------------------------------
# endpoint backoff diagnostics
# ---------------------------------------------------------------------------


def test_endpoint_backoff_is_energy_key_matches_stat_endpoints() -> None:
    """Stat/energy endpoint keys are recognised so they are never suppressed."""
    assert _Coordinator._endpoint_backoff_is_energy_key("pv_stat") is True  # ruff: ignore[private-member-access]
    assert _Coordinator._endpoint_backoff_is_energy_key("today_energy") is True  # ruff: ignore[private-member-access]
    assert _Coordinator._endpoint_backoff_is_energy_key("device_list") is False  # ruff: ignore[private-member-access]


def test_endpoint_backoff_diagnostics_reports_only_active_non_energy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active windows exclude energy keys and already-elapsed windows."""
    monkeypatch.setattr(co.time, "monotonic", lambda: 1_000.0)
    coordinator = _bare()
    coordinator._endpoint_backoff = {  # ruff: ignore[private-member-access]
        "device_list": {"until": 1_030.0, "code": 500, "level": 2},
        "pv_stat": {"until": 1_030.0, "code": 500, "level": 2},
        "expired": {"until": 900.0, "code": 500, "level": 1},
    }

    diagnostics = coordinator.endpoint_backoff_diagnostics()

    assert diagnostics["active_count"] == 1
    assert diagnostics["active"]["device_list"] == {
        "code": 500,
        "level": 2,
        "remaining_seconds": 30,
    }
    assert "pv_stat" not in diagnostics["active"]
    assert "expired" not in diagnostics["active"]


def test_endpoint_backoff_active_count_ignores_energy_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only non-energy keys with a future window count as active."""
    monkeypatch.setattr(co.time, "monotonic", lambda: 500.0)
    coordinator = _bare()
    coordinator._endpoint_backoff = {  # ruff: ignore[private-member-access]
        "device_list": {"until": 600.0},
        "home_stat": {"until": 600.0},
        "stale": {"until": 400.0},
    }

    assert coordinator._endpoint_backoff_active_count() == 1  # ruff: ignore[private-member-access]


def test_endpoint_backoff_delays_for_key_uses_energy_ladder() -> None:
    """Energy keys use the short capped ladder; others use the long one."""
    energy = _Coordinator._endpoint_backoff_delays_for_key("pv_stat")  # ruff: ignore[private-member-access]
    other = _Coordinator._endpoint_backoff_delays_for_key("device_list")  # ruff: ignore[private-member-access]

    assert energy == co._ENDPOINT_BACKOFF_ENERGY_DELAYS_SEC  # ruff: ignore[private-member-access]
    assert other == co._ENDPOINT_BACKOFF_DELAYS_SEC  # ruff: ignore[private-member-access]


# ---------------------------------------------------------------------------
# statistics backfill diagnostics / device-state
# ---------------------------------------------------------------------------


def test_statistics_backfill_diagnostics_redacts_device_ids() -> None:
    """Device ids are replaced by stable ordinal labels in diagnostics."""
    coordinator = _bare()
    coordinator._statistics_backfill_state_loaded = True  # ruff: ignore[private-member-access]
    coordinator._statistics_backfill_state = {  # ruff: ignore[private-member-access]
        co._STATISTICS_BACKFILL_STORE_DEVICES: {  # ruff: ignore[private-member-access]
            "SN-B": {"last_repair_date": "2026-01-02"},
            "SN-A": {"last_repair_date": "2026-01-01"},
        },
    }

    diagnostics = coordinator.statistics_backfill_diagnostics

    assert diagnostics["loaded"] is True
    assert diagnostics["tracked_devices"] == 2
    # Sorted by device id: SN-A first.
    assert diagnostics["devices"]["device_1"] == {"last_repair_date": "2026-01-01"}
    assert diagnostics["devices"]["device_2"] == {"last_repair_date": "2026-01-02"}


def test_statistics_backfill_diagnostics_tolerates_missing_devices() -> None:
    """A malformed store yields an empty, non-raising diagnostics block."""
    coordinator = _bare()
    coordinator._statistics_backfill_state_loaded = False  # ruff: ignore[private-member-access]
    coordinator._statistics_backfill_state = {}  # ruff: ignore[private-member-access]

    diagnostics = coordinator.statistics_backfill_diagnostics

    assert diagnostics == {
        "loaded": False,
        "tracked_devices": 0,
        "devices": {},
    }


@pytest.mark.asyncio
async def test_statistics_import_job_awaits_repair_wrapper() -> None:
    """The current import job awaits current import and schedules backfill."""
    coordinator = _bare()
    coordinator._statistics_import_task = None  # ruff: ignore[private-member-access]
    repair = AsyncMock()
    coordinator._async_import_and_repair_app_chart_statistics = repair  # ruff: ignore[private-member-access]
    scheduler = MagicMock()
    coordinator._schedule_statistics_backfill = scheduler  # ruff: ignore[private-member-access]

    snapshot: dict[str, dict[str, Any]] = {"dev-1": {}}
    await coordinator._async_statistics_import_job(snapshot)  # ruff: ignore[private-member-access]

    repair.assert_awaited_once_with(snapshot)
    scheduler.assert_called_once_with(snapshot)


def test_statistics_backfill_device_state_creates_nested_state() -> None:
    """A first read seeds an empty per-device dict inside the store."""
    coordinator = _bare()
    coordinator._statistics_backfill_state = {}  # ruff: ignore[private-member-access]

    state = coordinator._statistics_backfill_device_state("dev-1")  # ruff: ignore[private-member-access]
    state["marker"] = 1

    store = coordinator._statistics_backfill_state[  # ruff: ignore[private-member-access]
        co._STATISTICS_BACKFILL_STORE_DEVICES  # ruff: ignore[private-member-access]
    ]
    assert store["dev-1"] == {"marker": 1}


# ---------------------------------------------------------------------------
# discovery-source markers / cached snapshot
# ---------------------------------------------------------------------------


def test_discovery_source_marker_system_list_when_system_context() -> None:
    """A record carrying system context is marked as a system-list source."""
    marked = _Coordinator._with_discovery_source_marker({  # ruff: ignore[private-member-access]
        FIELD_SYSTEM_ID: "sys-1",
    })

    assert (
        marked[PAYLOAD_DEVICE_META][PAYLOAD_DISCOVERY_SOURCE]
        == DISCOVERY_SOURCE_SYSTEM_LIST
    )


def test_discovery_source_marker_legacy_when_no_system_context() -> None:
    """A bare record with no system context is marked legacy-bind-list."""
    marked = _Coordinator._with_discovery_source_marker({})  # ruff: ignore[private-member-access]

    assert (
        marked[PAYLOAD_DEVICE_META][PAYLOAD_DISCOVERY_SOURCE]
        == DISCOVERY_SOURCE_LEGACY_BIND_LIST
    )


def test_discovery_source_marker_preserves_existing_source() -> None:
    """An explicit existing marker is left untouched."""
    marked = _Coordinator._with_discovery_source_marker({  # ruff: ignore[private-member-access]
        PAYLOAD_DEVICE_META: {PAYLOAD_DISCOVERY_SOURCE: "manual"},
        FIELD_SYSTEM_ID: "sys-1",
    })

    assert marked[PAYLOAD_DEVICE_META][PAYLOAD_DISCOVERY_SOURCE] == "manual"


def test_cached_discovery_snapshot_builds_minimal_payload() -> None:
    """Cached discovery yields empty properties plus device/system metadata."""
    coordinator = _bare()
    coordinator._device_index = {  # ruff: ignore[private-member-access]
        "dev-1": {
            PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: "SN-A"},
            PAYLOAD_SYSTEM_META: {FIELD_SYSTEM_ID: "sys-1"},
        },
    }

    snapshot = coordinator.cached_discovery_snapshot()

    device = snapshot["dev-1"]
    assert device[PAYLOAD_PROPERTIES] == {}
    assert device[PAYLOAD_DEVICE] == {FIELD_DEVICE_SN: "SN-A"}
    assert device[PAYLOAD_DISCOVERY] == {FIELD_DEVICE_SN: "SN-A"}
    assert device[PAYLOAD_SYSTEM] == {FIELD_SYSTEM_ID: "sys-1"}


# ---------------------------------------------------------------------------
# polling / import diagnostics copy semantics
# ---------------------------------------------------------------------------


def test_polling_diagnostics_returns_defensive_copy() -> None:
    """The polling diagnostics accessor never leaks the internal dict."""
    coordinator = _bare()
    internal = {"last_cycle_seconds": 12}
    coordinator._polling_diagnostics = internal  # ruff: ignore[private-member-access]

    exported = coordinator.polling_diagnostics
    exported["last_cycle_seconds"] = 999

    assert internal["last_cycle_seconds"] == 12


def test_statistics_import_diagnostics_returns_defensive_copy() -> None:
    """The import diagnostics accessor returns an isolated copy."""
    coordinator = _bare()
    internal = {"last_import_device_count": 3}
    coordinator._statistics_import_diagnostics = internal  # ruff: ignore[private-member-access]

    exported = coordinator.statistics_import_diagnostics
    exported["last_import_device_count"] = 0

    assert internal["last_import_device_count"] == 3


# ---------------------------------------------------------------------------
# source-candidate ordering helpers
# ---------------------------------------------------------------------------


def test_day_chart_source_candidates_prefers_device_stat_for_pv() -> None:
    """PV day imports use the complete device-stat curve, not sparse trends."""
    coordinator = _bare()

    candidates = coordinator._day_chart_source_candidates(  # ruff: ignore[private-member-access]
        "pv",
        "pvEnergy",
        "pv_energy",
    )

    assert candidates == [(f"pv_{co.DATE_TYPE_DAY}", "pvEnergy")]


def test_day_chart_source_candidates_without_trend_source() -> None:
    """A metric with no trend source yields only its day section candidate."""
    coordinator = _bare()

    candidates = coordinator._day_chart_source_candidates(  # ruff: ignore[private-member-access]
        "misc",
        "miscStat",
        "unmapped_metric",
    )

    assert candidates == [(f"misc_{co.DATE_TYPE_DAY}", "miscStat")]


def test_metric_source_candidates_dedupes_and_keeps_primary_first() -> None:
    """The primary section leads and duplicate fallbacks are removed."""
    coordinator = _bare()

    candidates = coordinator._metric_source_candidates(  # ruff: ignore[private-member-access]
        "pv",
        "pvStat",
        "unmapped_metric",
    )

    assert candidates[0] == ("pv", "pvStat")
    assert len(candidates) == len(set(candidates))
