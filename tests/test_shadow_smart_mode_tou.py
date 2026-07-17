"""HTTP polls fill the smart-mode and TOU-schedule buckets (owner 2026-07-05).

The ``getSmartMode`` and ``queryTouPlan`` endpoints had API + coordinator
wrappers but no caller, so ``smart_mode_active`` / ``smart_mode_time_difference``
/ ``tou_plan_tasks`` stayed "Unbekannt" without cloud MQTT. HTTP is the
authoritative source, so the shadow cycle now polls them per device. The polls
are additive: they only fill their own bucket and skip quietly when the
required id cannot be resolved.
"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_DEVICE_ID,
    FIELD_SYSTEM_ID,
    PAYLOAD_DEVICE,
    PAYLOAD_SMART_MODE,
    PAYLOAD_SYSTEM_META,
    PAYLOAD_TOU_SCHEDULE,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_DEVICE_ID = "dev-1"
_SYSTEM_ID = "573702884982521856"
_NUMERIC_DEVICE_ID = "612119096645267456"
_TIME_DIFFERENCE = 42


def _bare_coordinator() -> JackerySolarVaultCoordinator:
    """Create a coordinator shell for the HTTP-poll helpers without HA setup."""
    return JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)


@pytest.mark.asyncio()
async def test_smart_mode_bucket_filled_from_http() -> None:
    """A resolvable system id fills the smart-mode bucket from getSmartMode."""
    coordinator = _bare_coordinator()
    body = {"isActive": 1, "timeDifference": _TIME_DIFFERENCE}
    get_smart_mode = AsyncMock(return_value=body)
    cast("Any", coordinator).api = SimpleNamespace(
        async_get_smart_mode_info=get_smart_mode,
    )
    working: dict[str, Any] = {PAYLOAD_SYSTEM_META: {FIELD_SYSTEM_ID: _SYSTEM_ID}}

    filled = await coordinator._async_apply_smart_mode(_DEVICE_ID, working)  # ruff:ignore[private-member-access]

    assert filled is True
    assert working[PAYLOAD_SMART_MODE]["isActive"] == 1
    assert working[PAYLOAD_SMART_MODE]["timeDifference"] == _TIME_DIFFERENCE
    get_smart_mode.assert_awaited_once_with(_SYSTEM_ID)


@pytest.mark.asyncio()
async def test_smart_mode_skipped_without_system_id() -> None:
    """No system id means no fetch and no bucket write."""
    coordinator = _bare_coordinator()
    get_smart_mode = AsyncMock()
    cast("Any", coordinator).api = SimpleNamespace(
        async_get_smart_mode_info=get_smart_mode,
    )
    working: dict[str, Any] = {}

    filled = await coordinator._async_apply_smart_mode(_DEVICE_ID, working)  # ruff:ignore[private-member-access]

    assert filled is False
    assert PAYLOAD_SMART_MODE not in working
    get_smart_mode.assert_not_awaited()


@pytest.mark.asyncio()
async def test_tou_bucket_filled_from_http() -> None:
    """A resolvable numeric device id fills the TOU bucket from queryTouPlan."""
    coordinator = _bare_coordinator()
    body = {"tasks": [{"start": "00:00", "end": "06:00"}]}
    query_tou = AsyncMock(return_value=body)
    cast("Any", coordinator).api = SimpleNamespace(
        async_query_tou_plan=query_tou,
    )
    working: dict[str, Any] = {PAYLOAD_DEVICE: {FIELD_DEVICE_ID: _NUMERIC_DEVICE_ID}}

    filled = await coordinator._async_apply_tou_plan(_DEVICE_ID, working)  # ruff:ignore[private-member-access]

    assert filled is True
    assert working[PAYLOAD_TOU_SCHEDULE]["tasks"] == body["tasks"]
    query_tou.assert_awaited_once_with(
        device_id=_NUMERIC_DEVICE_ID,
    )


@pytest.mark.asyncio()
async def test_tou_skipped_without_device_id() -> None:
    """No numeric device id means no fetch and no bucket write."""
    coordinator = _bare_coordinator()
    query_tou = AsyncMock()
    cast("Any", coordinator).api = SimpleNamespace(async_query_tou_plan=query_tou)
    working: dict[str, Any] = {}

    filled = await coordinator._async_apply_tou_plan(_DEVICE_ID, working)  # ruff:ignore[private-member-access]

    assert filled is False
    assert PAYLOAD_TOU_SCHEDULE not in working
    query_tou.assert_not_awaited()
