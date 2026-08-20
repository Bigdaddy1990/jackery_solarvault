"""Regression tests for the reverted local-preferred live-property merge.

Slice-2 revert (2026-07-18): BLE/MQTT LIVE values must reach entities again,
matching the working ``backup_current`` coordinator and AGENTS.md §1.2
("Local (BLE/MQTT) is preferred for live values"). The prior 2026-07-17 FIX-A
machinery shadowed live keys while HTTP stayed fresh and rebuilt the display
bucket from an empty base, erasing live-delivered combine/CT fields. Both
regressions are asserted fixed here:

* a fresh live frame overrides HTTP for the approved live keys, and
* a live-only combine field survives the next HTTP-poll rebuild.
"""

from datetime import timedelta

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_GRID_IN_PW,
    FIELD_PV1,
    FIELD_PV_PW,
    FIELD_WNAME,
    FIELD_WORK_MODEL,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.ingest import TransportSource

_NOW = 10_000.0
_LIVE_PV_W = 100
_HTTP_PV_W = 90
_LIVE_GRID_IN_PW = 42


def _bare_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> JackerySolarVaultCoordinator:
    """Create a coordinator shell for the live-merge policy without HA setup."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._property_overrides = {}
    coordinator._property_source_state = {}
    coordinator._configured_update_interval = timedelta(seconds=15)
    coordinator._mqtt = None
    coordinator._last_property_push_monotonic = 123.0
    coordinator.data = {}
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.coordinator.time.monotonic",
        lambda: _NOW,
    )
    return coordinator


def test_fresh_live_frame_overrides_http_for_live_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh MQTT/BLE pvPw wins over the concurrent HTTP pvPw (live-preferred)."""
    coordinator = _bare_coordinator(monkeypatch)
    live = coordinator._merge_main_properties_for_device(
        "dev-1",
        {},
        {FIELD_PV_PW: _LIVE_PV_W},
        source=TransportSource.CLOUD_MQTT,
    )
    merged = coordinator._merge_main_properties_for_device(
        "dev-1",
        live,
        {FIELD_PV_PW: _HTTP_PV_W},
        source=TransportSource.HTTP,
    )

    assert merged[FIELD_PV_PW] == _LIVE_PV_W


def test_live_delivered_combine_field_survives_a_later_http_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live-only combine field is retained across the next HTTP-poll rebuild."""
    coordinator = _bare_coordinator(monkeypatch)
    live = coordinator._merge_main_properties_for_device(
        "dev-1",
        {},
        {FIELD_PV_PW: _LIVE_PV_W, FIELD_GRID_IN_PW: _LIVE_GRID_IN_PW},
        source=TransportSource.CLOUD_MQTT,
    )
    merged = coordinator._merge_main_properties_for_device(
        "dev-1",
        live,
        {FIELD_PV_PW: _HTTP_PV_W},
        source=TransportSource.HTTP,
    )

    assert merged[FIELD_GRID_IN_PW] == _LIVE_GRID_IN_PW
    assert merged[FIELD_PV_PW] == _LIVE_PV_W


def test_work_model_is_protected_as_live_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed HTTP snapshot must not overwrite a fresh pushed work mode."""
    coordinator = _bare_coordinator(monkeypatch)
    live = coordinator._merge_main_properties_for_device(
        "dev-1",
        {},
        {FIELD_WORK_MODEL: 7},
        source=TransportSource.CLOUD_MQTT,
    )
    merged = coordinator._merge_main_properties_for_device(
        "dev-1",
        live,
        {FIELD_WORK_MODEL: 2},
        source=TransportSource.HTTP,
    )

    assert merged[FIELD_WORK_MODEL] == 7


def test_config_only_push_does_not_refresh_live_push_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An MQTT config field must not masquerade as fresh live telemetry."""
    coordinator = _bare_coordinator(monkeypatch)

    coordinator._merge_main_properties_for_device(
        "dev-1",
        {FIELD_PV_PW: _HTTP_PV_W},
        {FIELD_WNAME: "new-wifi"},
        source=TransportSource.CLOUD_MQTT,
    )

    assert coordinator._last_property_push_monotonic == pytest.approx(123.0)


def test_sparse_pv_push_refreshes_live_push_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sparse nested PV frame is accepted even when its base has extra keys."""
    coordinator = _bare_coordinator(monkeypatch)

    merged = coordinator._merge_main_properties_for_device(
        "dev-1",
        {FIELD_PV1: {FIELD_PV_PW: 100, "name": "PV1"}},
        {FIELD_PV1: {FIELD_PV_PW: 200}},
        source=TransportSource.CLOUD_MQTT,
    )

    assert merged[FIELD_PV1] == {FIELD_PV_PW: 200, "name": "PV1"}
    assert coordinator._last_property_push_monotonic == pytest.approx(_NOW)
