"""Regression tests for the stale-source guards (follow-ups to the soc-freeze).

Three guards, one theme — a dead source must never masquerade as live:

* The CombineData system-info cache fills only HTTP-MISSING keys and
  expires after ``SYSTEM_INFO_CACHE_MAX_AGE_SEC`` (it used to overwrite
  fresh values unconditionally, forever).
* ``_mqtt_live_properties_are_fresh`` counts only actually received
  frames (the old connected-client fallback stayed True through
  reconnect loops without a single message).
* A byte-identical live-key HTTP body over ``CLOUD_PROPERTY_STALE_CYCLES``
  polls raises the ``cloud_property_body_stale`` diagnostic marker —
  a MARKER only, never a gate.
"""

from datetime import timedelta
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

from custom_components.jackery_solarvault.const import (
    CLOUD_PROPERTY_STALE_CYCLES,
    FIELD_BAT_SOC,
    FIELD_PV_PW,
    PAYLOAD_MQTT_LAST,
    PAYLOAD_PROPERTIES,
    SYSTEM_INFO_CACHE_MAX_AGE_SEC,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    import pytest

_NOW = 50_000.0
_DEVICE = "dev-1"
_CACHED_WORK_MODEL = 2
_FRESH_WORK_MODEL = 3
_PASSTHROUGH_SOC = 75


def _bare_coordinator(
    monkeypatch: pytest.MonkeyPatch | None,
) -> JackerySolarVaultCoordinator:
    """Create a coordinator shell for the guard helpers without HA setup."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._system_info_cache = {}  # ruff:ignore[private-member-access]
    coordinator._system_info_cache_monotonic = {}  # ruff:ignore[private-member-access]
    coordinator._property_body_signatures = {}  # ruff:ignore[private-member-access]
    coordinator._pv_body_signatures = {}  # ruff:ignore[private-member-access]
    coordinator._configured_update_interval = timedelta(seconds=15)  # ruff:ignore[private-member-access]
    coordinator._mqtt = cast(  # ruff:ignore[private-member-access]
        "Any",
        SimpleNamespace(
            diagnostics_snapshot=lambda: {
                "connected": True,
                "mqtt_silent_for_too_long": False,
            },
        ),
    )
    if monkeypatch is not None:
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.coordinator.time.monotonic",
            lambda: _NOW,
        )
    return coordinator


def test_system_info_cache_never_overwrites_a_delivered_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fill-only: a fresh workModel from HTTP/MQTT must survive the overlay."""
    coordinator = _bare_coordinator(monkeypatch)
    coordinator._system_info_cache[_DEVICE] = {"workModel": _CACHED_WORK_MODEL}  # ruff:ignore[private-member-access]
    coordinator._system_info_cache_monotonic[_DEVICE] = _NOW  # ruff:ignore[private-member-access]

    filled = coordinator._overlay_cached_system_info(  # ruff:ignore[private-member-access]
        _DEVICE,
        {"workModel": _FRESH_WORK_MODEL, "standbyPw": None},
    )

    assert filled["workModel"] == _FRESH_WORK_MODEL


def test_system_info_cache_fills_missing_keys_while_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache still bridges MQTT disconnects for keys HTTP never carries."""
    coordinator = _bare_coordinator(monkeypatch)
    coordinator._system_info_cache[_DEVICE] = {"workModel": _CACHED_WORK_MODEL}  # ruff:ignore[private-member-access]
    coordinator._system_info_cache_monotonic[_DEVICE] = _NOW - 10.0  # ruff:ignore[private-member-access]

    filled = coordinator._overlay_cached_system_info(  # ruff:ignore[private-member-access]
        _DEVICE,
        {"soc": _PASSTHROUGH_SOC},
    )

    assert filled["workModel"] == _CACHED_WORK_MODEL
    assert filled["soc"] == _PASSTHROUGH_SOC


def test_system_info_cache_expires_instead_of_lying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired cache stops filling — hours-old config is not current state."""
    coordinator = _bare_coordinator(monkeypatch)
    coordinator._system_info_cache[_DEVICE] = {"workModel": _CACHED_WORK_MODEL}  # ruff:ignore[private-member-access]
    coordinator._system_info_cache_monotonic[_DEVICE] = (  # ruff:ignore[private-member-access]
        _NOW - SYSTEM_INFO_CACHE_MAX_AGE_SEC - 1.0
    )

    filled = coordinator._overlay_cached_system_info(  # ruff:ignore[private-member-access]
        _DEVICE,
        {"soc": _PASSTHROUGH_SOC},
    )

    assert "workModel" not in filled


def test_connected_but_silent_mqtt_is_not_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reconnect-looping client without one received frame grants nothing."""
    coordinator = _bare_coordinator(monkeypatch)

    assert coordinator._mqtt_live_properties_are_fresh({}) is False  # ruff:ignore[private-member-access]
    assert (
        coordinator._mqtt_live_properties_are_fresh(  # ruff:ignore[private-member-access]
            {PAYLOAD_MQTT_LAST: {"received_at_monotonic": _NOW - 10_000.0}},
        )
        is False
    )
    assert (
        coordinator._mqtt_live_properties_are_fresh(  # ruff:ignore[private-member-access]
            {PAYLOAD_MQTT_LAST: {"received_at_monotonic": _NOW - 5.0}},
        )
        is True
    )


def test_frozen_pv_family_raises_the_pv_stale_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pv* keys frozen at generating values flag the pv marker (F7).

    The body-wide marker cannot see this case because grid/battery keys
    keep changing every cycle — only the pv projection is frozen.
    """
    coordinator = _bare_coordinator(monkeypatch)
    frozen_pv = {FIELD_PV_PW: 129, "pv1": {"pvPw": 38}, "outOngridPw": 0}

    results = []
    for cycle in range(CLOUD_PROPERTY_STALE_CYCLES):
        body = dict(frozen_pv)
        body["outOngridPw"] = cycle  # grid side keeps moving
        results.append(coordinator._note_pv_property_staleness(_DEVICE, body))  # ruff:ignore[private-member-access]

    assert results[-2] is False
    assert results[-1] is True


def test_resting_pv_resets_the_pv_stale_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PvPw == 0 is a legitimate night state and clears the counter."""
    coordinator = _bare_coordinator(monkeypatch)
    generating = {FIELD_PV_PW: 129}

    for _ in range(CLOUD_PROPERTY_STALE_CYCLES - 1):
        coordinator._note_pv_property_staleness(_DEVICE, dict(generating))  # ruff:ignore[private-member-access]

    assert coordinator._note_pv_property_staleness(_DEVICE, {FIELD_PV_PW: 0}) is False  # ruff:ignore[private-member-access]
    assert (
        coordinator._note_pv_property_staleness(_DEVICE, dict(generating)) is False  # ruff:ignore[private-member-access]
    ), "counter must restart after a resting cycle"


async def test_system_info_query_runs_ble_first_without_cloud_mqtt() -> None:
    """A dead cloud session no longer blocks the SystemBody query (F6).

    The SYSTEM_INFO fields have no HTTP source; with a live BLE transport
    the BLE-first query must run even while the broker bans the session.
    """
    coordinator = _bare_coordinator(None)
    coordinator._mqtt = None  # ruff:ignore[private-member-access]
    coordinator._ble_listener = SimpleNamespace()  # ruff:ignore[private-member-access]
    coordinator._system_info_query_interval_sec = 180  # ruff:ignore[private-member-access]
    coordinator._last_system_info_query = {  # ruff:ignore[private-member-access]
        _DEVICE: time.monotonic() - coordinator._system_info_query_interval_sec - 1  # ruff:ignore[private-member-access]
    }
    coordinator.data = {_DEVICE: {PAYLOAD_PROPERTIES: {}}}
    query_device_info = AsyncMock(return_value=None)
    query_system_info = AsyncMock(return_value=None)
    cast("Any", coordinator).async_query_device_info = query_device_info
    cast("Any", coordinator).async_query_system_info = query_system_info

    await coordinator._async_query_system_info_for_missing(ensure_mqtt=False)  # ruff:ignore[private-member-access]

    query_system_info.assert_awaited_once()


async def test_system_info_query_skips_without_any_command_transport() -> None:
    """No BLE and no connected cloud client: the query stays skipped."""
    coordinator = _bare_coordinator(None)
    coordinator._mqtt = None  # ruff:ignore[private-member-access]
    coordinator._ble_listener = None  # ruff:ignore[private-member-access]
    coordinator._last_system_info_query = {}  # ruff:ignore[private-member-access]
    coordinator._system_info_query_interval_sec = 180  # ruff:ignore[private-member-access]
    coordinator.data = {_DEVICE: {PAYLOAD_PROPERTIES: {}}}
    query_system_info = AsyncMock(return_value=None)
    cast("Any", coordinator).async_query_system_info = query_system_info

    await coordinator._async_query_system_info_for_missing(ensure_mqtt=False)  # ruff:ignore[private-member-access]

    query_system_info.assert_not_awaited()


def test_identical_cloud_bodies_raise_the_stale_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N identical live-key bodies flag a frozen cloud shadow; change resets."""
    coordinator = _bare_coordinator(monkeypatch)
    frozen_body = {FIELD_BAT_SOC: 75, FIELD_PV_PW: 0}

    results = [
        coordinator._note_property_body_signature(_DEVICE, dict(frozen_body))  # ruff:ignore[private-member-access]
        for _ in range(CLOUD_PROPERTY_STALE_CYCLES)
    ]

    assert results[-2] is False
    assert results[-1] is True

    changed = coordinator._note_property_body_signature(  # ruff:ignore[private-member-access]
        _DEVICE,
        {FIELD_BAT_SOC: 74, FIELD_PV_PW: 0},
    )

    assert changed is False
