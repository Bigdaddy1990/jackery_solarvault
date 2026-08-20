"""Regression: per-device state must not leak across sibling devices in one cycle.

Both fixes narrow a per-device seam that was accidentally shared across every
device on the account:

* **F-SW2-6** — the ATS/symmetry period-stat endpoint-backoff key omitted the
  device id, unlike every sibling per-device backoff key (pv/battery/home/ct/
  eps/today_energy stat), which are namespaced ``f"dev:{dev_id}:<name>"``
  sharing the flat ``self._endpoint_backoff`` dict. A structurally
  unsupported symmetry endpoint on one device (code=10600, no ATS hardware)
  opened a coordinator-wide backoff window that silently suppressed a
  *different* device's working symmetry fetches too.
* **F-SW2-7** — the per-device ``DEVICE_NOT_ACTIVATED`` repair-issue cleanup
  deleted *any* not-activated issue whose id differed from the device
  currently being processed, instead of only issues for devices no longer in
  ``device_items``. With two devices unactivated in the same cycle, finishing
  device B's iteration tore down device A's still-valid issue (and the next
  cycle did the reverse) — issues churned every poll instead of tracking real
  activation state.

Only the Jackery cloud ``api`` boundary is mocked (via the reusable
:mod:`tests._update_cycle_fixture`); all internal merge/backoff/repair logic
runs for real.
"""

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApiError
from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FIELD_BIND_KEY,
    FIELD_DEVICES,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_ID,
    FIELD_MODEL_CODE,
    FIELD_SYSTEM_ID,
    PAYLOAD_DEVICE,
    REPAIR_ISSUE_DEVICE_NOT_ACTIVATED,
)
from homeassistant.helpers import issue_registry as ir
from tests._update_cycle_fixture import (  # ruff:ignore[banned-api]
    MODEL_CODE,
    make_update_cycle_api,
    setup_update_cycle_coordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_SYSTEM_ID = "512000000000099101"
_DEVICE_A_ID = "symmetry-device-a"
_DEVICE_A_SN = "HTB000000000101"
_DEVICE_B_ID = "symmetry-device-b"
_DEVICE_B_SN = "HTB000000000102"
_UNSUPPORTED_ERROR = JackeryApiError("cloud says code=10600 unsupported")


def _two_device_system_list() -> list[dict[str, Any]]:
    """Return a ``/v1/device/system/list`` response with two sibling devices."""

    def _entry(dev_id: str, dev_sn: str) -> dict[str, Any]:
        return {
            FIELD_DEVICE_ID: dev_id,
            FIELD_DEVICE_SN: dev_sn,
            FIELD_MODEL_CODE: MODEL_CODE,
            FIELD_BIND_KEY: 1,
            "devModel": "SolarVault HTB2000",
        }

    return [
        {
            FIELD_ID: _SYSTEM_ID,
            FIELD_SYSTEM_ID: _SYSTEM_ID,
            "systemName": "Home",
            FIELD_DEVICES: [
                _entry(_DEVICE_A_ID, _DEVICE_A_SN),
                _entry(_DEVICE_B_ID, _DEVICE_B_SN),
            ],
        },
    ]


def _healthy_property(dev_id: str) -> dict[str, Any]:
    """Return a minimal activated ``/v1/device/property`` response."""
    return {PAYLOAD_DEVICE: {"deviceId": dev_id, "activated": 1, "online": 1}}


def _capture_named_background_task(captured: dict[str, Any], wanted_prefix: str) -> Any:
    """Return a background-task stub that captures one coroutine, closes the rest.

    Mirrors ``_consume_background_task`` in ``test_coordinator_update_cycle.py``
    for tasks the test does not care about, so they never warn about being
    un-awaited, while the wanted coroutine is captured for the test to await
    directly and deterministically — this avoids racing
    ``hass.async_block_till_done`` against the fire-and-forget scheduling of
    ``hass.async_create_background_task``.
    """

    def _stub(
        coro: Any,
        name: str,
        *,
        eager_start: bool = True,
    ) -> asyncio.Task[None]:
        del eager_start
        if name.startswith(wanted_prefix):
            captured["task"] = coro
        else:
            coro.close()
        return asyncio.create_task(asyncio.sleep(0))

    return _stub


# --- F-SW2-6: symmetry endpoint backoff must be per-device -----------------


def _symmetry_side_effect(*, device_sn: str, **_kwargs: Any) -> dict[str, Any]:
    """Fail every call attributed to device A, succeed for device B."""
    if device_sn == _DEVICE_A_ID:
        raise _UNSUPPORTED_ERROR
    return {}


@pytest.mark.asyncio
async def test_symmetry_backoff_does_not_suppress_sibling_device(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Device A's structural symmetry failure must not block device B's fetch."""
    api = make_update_cycle_api(
        async_get_system_list=AsyncMock(return_value=_two_device_system_list()),
        async_get_device_property=AsyncMock(side_effect=_healthy_property),
        async_get_symmetry_stat=AsyncMock(side_effect=_symmetry_side_effect),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)
    # ``self.data`` only needs the device keys to exist so the symmetry
    # fetcher's ``self.data[dev_id]`` lookup does not raise; the real
    # per-device snapshot has no top-level FIELD_DEVICE_SN key either (it
    # lives nested under PAYLOAD_DEVICE), so production already falls back to
    # ``dev_id`` here — the mock's side effect above matches that.
    coordinator.data = {_DEVICE_A_ID: {}, _DEVICE_B_ID: {}}

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        coordinator.hass,
        "async_create_background_task",
        _capture_named_background_task(captured, "jackery_slow_metrics_bg_"),
    )
    # The background refresh requests a follow-up coordinator refresh on
    # success; that second cycle is orthogonal to this test (which only
    # cares about the one cycle's symmetry-fetch isolation), so it is
    # stubbed out rather than left to schedule a real, unawaited task.
    monkeypatch.setattr(coordinator, "async_request_refresh", AsyncMock())

    await coordinator._async_update_data_guarded()
    assert "task" in captured, "the slow-metrics background refresh was not launched"
    await captured["task"]

    a_calls = [
        call
        for call in api.async_get_symmetry_stat.await_args_list
        if call.kwargs.get("device_sn") == _DEVICE_A_ID
    ]
    b_calls = [
        call
        for call in api.async_get_symmetry_stat.await_args_list
        if call.kwargs.get("device_sn") == _DEVICE_B_ID
    ]
    assert a_calls, "sanity: device A's symmetry endpoint must have been attempted"
    assert b_calls, (
        "device B's symmetry endpoint must still be fetched after device A's "
        "structural failure — the backoff key must be scoped per device"
    )
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# --- F-SW2-7: not-activated repair issues must not cross-delete ------------


@pytest.mark.asyncio
async def test_two_unactivated_devices_both_keep_repair_issues(
    hass: HomeAssistant,
) -> None:
    """Two devices with ``activated=0`` in one cycle must both keep their issue."""
    api = make_update_cycle_api(
        async_get_system_list=AsyncMock(return_value=_two_device_system_list()),
    )

    def _inactive(dev_id: str) -> dict[str, Any]:
        return {PAYLOAD_DEVICE: {"deviceId": dev_id, "activated": 0}}

    api.async_get_device_property = AsyncMock(side_effect=_inactive)
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)

    await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    issue_id_a = f"{entry.entry_id}_{_DEVICE_A_ID}_{REPAIR_ISSUE_DEVICE_NOT_ACTIVATED}"
    issue_id_b = f"{entry.entry_id}_{_DEVICE_B_ID}_{REPAIR_ISSUE_DEVICE_NOT_ACTIVATED}"
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, issue_id_a) is not None
    assert registry.async_get_issue(DOMAIN, issue_id_b) is not None
    assert issue_id_a in coordinator._activation_issue_active
    assert issue_id_b in coordinator._activation_issue_active
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
