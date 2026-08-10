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
from typing import TYPE_CHECKING

from custom_components.jackery_solarvault.const import (
    FIELD_GRID_IN_PW,
    FIELD_PV_PW,
    PAYLOAD_MQTT_LAST,
    PAYLOAD_PROPERTIES,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    import pytest

_NOW = 10_000.0
_LIVE_PV_W = 100
_HTTP_PV_W = 90
_LIVE_GRID_IN_PW = 42


def _bare_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> JackerySolarVaultCoordinator:
    """Create a coordinator shell for the live-merge policy without HA setup."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._property_overrides = {}  # ruff: ignore[private-member-access]
    coordinator._configured_update_interval = timedelta(seconds=15)  # ruff: ignore[private-member-access]
    coordinator._mqtt = None  # ruff: ignore[private-member-access]
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
    entry = {
        PAYLOAD_MQTT_LAST: {"received_at_monotonic": _NOW},
        PAYLOAD_PROPERTIES: {FIELD_PV_PW: _LIVE_PV_W},
    }

    live_guarded = coordinator._http_properties_with_live_overrides(  # ruff: ignore[private-member-access]
        entry,
        {FIELD_PV_PW: _HTTP_PV_W},
    )
    merged = coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
        "dev-1",
        entry[PAYLOAD_PROPERTIES],
        live_guarded,
    )

    assert live_guarded[FIELD_PV_PW] == _LIVE_PV_W
    assert merged[FIELD_PV_PW] == _LIVE_PV_W


def test_live_delivered_combine_field_survives_a_later_http_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live-only combine field is retained across the next HTTP-poll rebuild."""
    coordinator = _bare_coordinator(monkeypatch)
    prior_props = {FIELD_PV_PW: _HTTP_PV_W, FIELD_GRID_IN_PW: _LIVE_GRID_IN_PW}
    http_props = {FIELD_PV_PW: _HTTP_PV_W}

    merged = coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
        "dev-1",
        prior_props,
        http_props,
    )

    assert merged[FIELD_GRID_IN_PW] == _LIVE_GRID_IN_PW
    assert merged[FIELD_PV_PW] == _HTTP_PV_W
