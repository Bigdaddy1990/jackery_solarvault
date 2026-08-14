"""Behavioral tests for endpoint-backoff policy and local-MQTT liveness.

These extend :mod:`tests.test_coordinator_local_first_mqtt`. The governing
invariant is that the HTTP/Layer-3 path stays primary: energy/stat endpoints
that feed the recorder are *tracked* in a capped backoff window but their next
scheduled HTTP attempt is never suppressed, while static diagnostic endpoints
keep the long escalating ladder. Local MQTT counts as "live" on message
freshness only — a merely-connected broker with no frames must not pause the
cloud channel.
"""

import time
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApiError
from custom_components.jackery_solarvault.const import MQTT_LIVE_THRESHOLD_SEC
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from tests._update_cycle_fixture import (  # ruff:ignore[banned-api]
    SYSTEM_ID,
    make_update_cycle_api,
    setup_update_cycle_coordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_NOW = 5_000.0
_ENERGY_KEY = "dev:device-1:pv_stat:day"
_DIAG_KEY = "diagnostic:static_model_metadata"
_BACKOFF_ERROR = JackeryApiError("cloud says code=10422")
_OTHER_CODE_ERROR = JackeryApiError("cloud says code=200 ok-ish")


def _bare_coordinator() -> JackerySolarVaultCoordinator:
    """Create a coordinator shell for backoff/liveness policy helpers."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._endpoint_backoff = {}  # ruff: ignore[private-member-access]
    coordinator._local_mqtt_last_message_monotonic = float("-inf")  # ruff: ignore[private-member-access]
    return coordinator


def _freeze(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.coordinator.time.monotonic",
        lambda: value,
    )


# --- endpoint backoff active suppression -----------------------------------


def test_energy_key_is_never_suppressed_even_while_windowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An energy key with a live window must still report not-active."""
    _freeze(monkeypatch, _NOW)
    coordinator = _bare_coordinator()
    coordinator._endpoint_backoff_note_failure(_ENERGY_KEY, _BACKOFF_ERROR)  # ruff: ignore[private-member-access]

    assert coordinator._endpoint_backoff_active(_ENERGY_KEY, _NOW) is False  # ruff: ignore[private-member-access]


def test_diagnostic_key_suppressed_only_inside_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A diagnostic key is active while windowed and clears once elapsed."""
    _freeze(monkeypatch, _NOW)
    coordinator = _bare_coordinator()
    coordinator._endpoint_backoff_note_failure(_DIAG_KEY, _BACKOFF_ERROR)  # ruff: ignore[private-member-access]

    assert coordinator._endpoint_backoff_active(_DIAG_KEY, _NOW) is True  # ruff: ignore[private-member-access]
    assert coordinator._endpoint_backoff_active(_DIAG_KEY, _NOW + 10_000.0) is False  # ruff: ignore[private-member-access]


def test_active_returns_false_for_unknown_key() -> None:
    """A key with no recorded window is not active."""
    coordinator = _bare_coordinator()

    assert coordinator._endpoint_backoff_active("never-seen", _NOW) is False  # ruff: ignore[private-member-access]


def test_active_count_excludes_energy_and_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only in-window diagnostic keys are counted as active."""
    _freeze(monkeypatch, _NOW)
    coordinator = _bare_coordinator()
    coordinator._endpoint_backoff_note_failure(_DIAG_KEY, _BACKOFF_ERROR)  # ruff: ignore[private-member-access]
    coordinator._endpoint_backoff_note_failure(_ENERGY_KEY, _BACKOFF_ERROR)  # ruff: ignore[private-member-access]

    assert coordinator._endpoint_backoff_active_count(_NOW) == 1  # ruff: ignore[private-member-access]
    assert coordinator._endpoint_backoff_active_count(_NOW + 10_000.0) == 0  # ruff: ignore[private-member-access]


# --- endpoint backoff note failure / success -------------------------------


def test_non_backoff_code_is_not_recorded() -> None:
    """A cloud error whose code is not a backoff code records no window."""
    coordinator = _bare_coordinator()

    assert (
        coordinator._endpoint_backoff_note_failure(_DIAG_KEY, _OTHER_CODE_ERROR)  # ruff: ignore[private-member-access]
        is False
    )
    assert _DIAG_KEY not in coordinator._endpoint_backoff  # ruff: ignore[private-member-access]


def test_new_failure_signature_resets_ladder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A different failure code restarts the diagnostic ladder at level zero."""
    _freeze(monkeypatch, _NOW)
    coordinator = _bare_coordinator()
    coordinator._endpoint_backoff_note_failure(_DIAG_KEY, _BACKOFF_ERROR)  # ruff: ignore[private-member-access]
    coordinator._endpoint_backoff_note_failure(_DIAG_KEY, _BACKOFF_ERROR)  # ruff: ignore[private-member-access]

    coordinator._endpoint_backoff_note_failure(  # ruff: ignore[private-member-access]
        _DIAG_KEY,
        JackeryApiError("cloud says code=10432"),
    )

    assert coordinator._endpoint_backoff[_DIAG_KEY]["level"] == 0  # ruff: ignore[private-member-access]


def test_note_success_clears_recorded_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful fetch drops the recorded backoff window."""
    _freeze(monkeypatch, _NOW)
    coordinator = _bare_coordinator()
    coordinator._endpoint_backoff_note_failure(_DIAG_KEY, _BACKOFF_ERROR)  # ruff: ignore[private-member-access]

    coordinator._endpoint_backoff_note_success(_DIAG_KEY)  # ruff: ignore[private-member-access]

    assert _DIAG_KEY not in coordinator._endpoint_backoff  # ruff: ignore[private-member-access]


def test_diagnostics_reports_only_live_diagnostic_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The diagnostics payload omits energy keys and expired windows."""
    _freeze(monkeypatch, _NOW)
    coordinator = _bare_coordinator()
    coordinator._endpoint_backoff_note_failure(_DIAG_KEY, _BACKOFF_ERROR)  # ruff: ignore[private-member-access]
    coordinator._endpoint_backoff_note_failure(_ENERGY_KEY, _BACKOFF_ERROR)  # ruff: ignore[private-member-access]

    payload = coordinator.endpoint_backoff_diagnostics()

    assert payload["active_count"] == 1
    assert _DIAG_KEY in payload["active"]
    assert _ENERGY_KEY not in payload["active"]
    assert payload["active"][_DIAG_KEY]["code"] == 10422


# --- local MQTT liveness ---------------------------------------------------


def test_local_mqtt_active_on_fresh_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message inside the live threshold marks local MQTT active."""
    _freeze(monkeypatch, _NOW)
    coordinator = _bare_coordinator()
    coordinator._local_mqtt_last_message_monotonic = _NOW - (  # ruff: ignore[private-member-access]
        MQTT_LIVE_THRESHOLD_SEC - 1
    )

    assert coordinator._local_mqtt_is_active() is True  # ruff: ignore[private-member-access]


def test_local_mqtt_inactive_on_stale_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message older than the live threshold is not active."""
    _freeze(monkeypatch, _NOW)
    coordinator = _bare_coordinator()
    coordinator._local_mqtt_last_message_monotonic = _NOW - (  # ruff: ignore[private-member-access]
        MQTT_LIVE_THRESHOLD_SEC + 5
    )

    assert coordinator._local_mqtt_is_active() is False  # ruff: ignore[private-member-access]


def test_local_mqtt_inactive_without_any_message() -> None:
    """The default (-inf) freshness reads as inactive."""
    coordinator = _bare_coordinator()

    assert coordinator._local_mqtt_is_active(time.monotonic()) is False  # ruff: ignore[private-member-access]


def test_direct_client_connected_false_without_runtime() -> None:
    """Missing hass/entry runtime means no direct local client session."""
    coordinator = _bare_coordinator()

    assert cast("Any", coordinator)._local_mqtt_direct_client_connected() is False  # ruff: ignore[private-member-access]


# --- unsupported endpoints (code=10600) back off instead of spamming ---------

_UNSUPPORTED_ERROR = JackeryApiError("cloud says code=10600 unsupported")
_SYMMETRY_KEY = "device_symmetry_stat:day"
_DYNAMIC_PRICE_KEY = "dynamic_price:system-1"


def test_symmetry_key_is_not_treated_as_energy() -> None:
    """The ATS/symmetry section must be suppressible, not a recorder feed.

    It returns code=10600 (unsupported) every cycle for the shipped hardware,
    so it must be eligible for real backoff rather than re-fetched forever.
    """
    assert (
        JackerySolarVaultCoordinator._endpoint_backoff_is_energy_key(_SYMMETRY_KEY)  # ruff: ignore[private-member-access]
        is False
    )


def test_symmetry_backs_off_on_unsupported_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A code=10600 symmetry failure enters a suppressing backoff window."""
    _freeze(monkeypatch, _NOW)
    coordinator = _bare_coordinator()

    recorded = coordinator._endpoint_backoff_note_failure(  # ruff: ignore[private-member-access]
        _SYMMETRY_KEY,
        _UNSUPPORTED_ERROR,
    )

    assert recorded is True
    assert coordinator._endpoint_backoff_active(_SYMMETRY_KEY, _NOW) is True  # ruff: ignore[private-member-access]


def test_dynamic_price_backs_off_on_unsupported_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A code=10600 dynamic-price failure enters a suppressing backoff window."""
    _freeze(monkeypatch, _NOW)
    coordinator = _bare_coordinator()

    recorded = coordinator._endpoint_backoff_note_failure(  # ruff: ignore[private-member-access]
        _DYNAMIC_PRICE_KEY,
        _UNSUPPORTED_ERROR,
    )

    assert recorded is True
    assert coordinator._endpoint_backoff_active(_DYNAMIC_PRICE_KEY, _NOW) is True  # ruff: ignore[private-member-access]


# --- dynamic_price backoff must also gate the background slow-metric refresh ---
#
# dynamic_price is fetched from two places: the foreground per-cycle fetch
# (``_fetch_system``) and the background slow-metric refresh
# (``_launch_background_slow_refresh``) that re-fetches stale per-system
# caches off the critical path once their TTL is stale. Only the foreground
# call passed ``backoff_key``; the background call was missing it, so a
# device with no dynamic-price contract (permanent code=10600) never entered
# backoff on that path and re-logged the "fetch failed" warning on every
# background refresh cycle forever, since a permanently-failing endpoint
# never advances its cache timestamp and so is always "stale" again next
# cycle.


@pytest.mark.asyncio
async def test_dynamic_price_backoff_suppresses_background_refresh_retry(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cold post-boot dynamic-price failure backs off in the background."""
    # Keep monotonic time below the price TTL. A cold-cache timestamp of 0.0
    # must still trigger the off-path refresh immediately after HA starts.
    _freeze(monkeypatch, 10.0)
    api = make_update_cycle_api()
    api.async_get_dynamic_price = AsyncMock(
        side_effect=JackeryApiError(
            "GET /v1/device/dynamic/v2/dynamicPrice code=10600 msg=unsupported",
        ),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)

    await coordinator._async_update_data_guarded()  # ruff: ignore[private-member-access]
    # The background slow-metric refresh runs as an ``async_create_background_task``
    # (excluded from the default ``async_block_till_done`` wait set), so it must
    # be waited for explicitly here.
    slow_metrics_task = coordinator._slow_metrics_bg_task  # ruff: ignore[private-member-access]
    assert slow_metrics_task is not None
    await slow_metrics_task
    await hass.async_block_till_done(wait_background_tasks=True)

    # The cold slow-metric cache is served stale_ok on the foreground path
    # (no fetch attempted there), so the failure above only came from the
    # background refresh — proving its call now carries the backoff key too.
    assert f"dynamic_price:{SYSTEM_ID}" in coordinator._endpoint_backoff  # ruff: ignore[private-member-access]
    calls_after_first_cycle = api.async_get_dynamic_price.call_count
    assert calls_after_first_cycle >= 1

    await coordinator._async_update_data_guarded()  # ruff: ignore[private-member-access]
    slow_metrics_task = coordinator._slow_metrics_bg_task  # ruff: ignore[private-member-access]
    assert slow_metrics_task is not None
    await slow_metrics_task
    await hass.async_block_till_done(wait_background_tasks=True)

    # The system's slow-metric cache never advances its timestamp on a
    # permanent failure, so it re-enters "needs refresh" every cycle. Without
    # the backoff key on the background call this re-invokes (and re-logs)
    # the endpoint every cycle; with it, the open backoff window suppresses
    # the retry.
    assert api.async_get_dynamic_price.call_count == calls_after_first_cycle

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


# --- Shelly realtime timeout backoff (codeless failure must still back off) ---
#
# A Shelly Cloud realtime-power fetch that times out raises a JackeryApiError
# with NO ``code=`` token, so ``_endpoint_backoff_note_failure`` records nothing
# and the background refresh re-issued the 8s request at an unreachable device
# every cycle forever. A dedicated timeout ladder, scoped strictly to the
# ``shelly_realtime:`` key, now opens a suppressing window so the storm stops.

_SHELLY_REALTIME_KEY = "shelly_realtime:5c013b048e3c"


def _shelly_timeout_error() -> JackeryApiError:
    """Build the codeless timeout error the Shelly realtime fetcher raises."""
    err = JackeryApiError(
        "Shelly realtime-power fetch timed out after 8s for 5c013b048e3c",
    )
    err.__cause__ = TimeoutError()
    return err


def test_shelly_realtime_timeout_is_backoffable() -> None:
    """A Shelly realtime key that timed out is eligible for timeout backoff."""
    assert (
        JackerySolarVaultCoordinator._is_backoffable_timeout(  # ruff: ignore[private-member-access]
            _SHELLY_REALTIME_KEY,
            _shelly_timeout_error(),
        )
        is True
    )


def test_non_shelly_timeout_is_not_backoffable() -> None:
    """A non-Shelly key that timed out keeps its normal retry cadence."""
    assert (
        JackerySolarVaultCoordinator._is_backoffable_timeout(  # ruff: ignore[private-member-access]
            _DIAG_KEY,
            _shelly_timeout_error(),
        )
        is False
    )


def test_shelly_non_timeout_error_is_not_backoffable() -> None:
    """A Shelly failure without a TimeoutError cause is not timeout-backed-off."""
    assert (
        JackerySolarVaultCoordinator._is_backoffable_timeout(  # ruff: ignore[private-member-access]
            _SHELLY_REALTIME_KEY,
            _BACKOFF_ERROR,
        )
        is False
    )


def test_missing_backoff_key_is_not_backoffable() -> None:
    """A ``None`` backoff key is never timeout-backed-off."""
    assert (
        JackerySolarVaultCoordinator._is_backoffable_timeout(  # ruff: ignore[private-member-access]
            None,
            _shelly_timeout_error(),
        )
        is False
    )


def test_shelly_timeout_opens_suppressing_window_and_escalates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recorded Shelly timeout suppresses the next fetch and escalates on repeat."""
    _freeze(monkeypatch, _NOW)
    coordinator = _bare_coordinator()

    coordinator._endpoint_backoff_note_timeout(_SHELLY_REALTIME_KEY)  # ruff: ignore[private-member-access]

    assert coordinator._endpoint_backoff_active(_SHELLY_REALTIME_KEY, _NOW) is True  # ruff: ignore[private-member-access]
    first_until = coordinator._endpoint_backoff[_SHELLY_REALTIME_KEY]["until"]  # ruff: ignore[private-member-access]

    coordinator._endpoint_backoff_note_timeout(_SHELLY_REALTIME_KEY)  # ruff: ignore[private-member-access]

    assert coordinator._endpoint_backoff[_SHELLY_REALTIME_KEY]["until"] > first_until  # ruff: ignore[private-member-access]


def test_shelly_timeout_window_clears_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recovered fetch drops the timeout backoff window."""
    _freeze(monkeypatch, _NOW)
    coordinator = _bare_coordinator()
    coordinator._endpoint_backoff_note_timeout(_SHELLY_REALTIME_KEY)  # ruff: ignore[private-member-access]

    coordinator._endpoint_backoff_note_success(_SHELLY_REALTIME_KEY)  # ruff: ignore[private-member-access]

    assert _SHELLY_REALTIME_KEY not in coordinator._endpoint_backoff  # ruff: ignore[private-member-access]
