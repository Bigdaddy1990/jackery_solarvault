"""Regression tests: the HTTP shadow poll is unconditional (full-wire rule).

Owner escalation 2026-07-04: every shadow-only field (CT electrical
detail, Smart-Meter comm/funForm/IP/MAC, SystemBody config keys such as
workModel/tempUnit/standbyPw, pack comm state) was populated in the
pre-MQTT full-wire builds and went permanently "Unbekannt" afterwards.
Root cause: the MQTT era wrapped the HTTP shadow poll in four gates —
skip while MQTT frames look fresh, require enumerated accessories,
skip accessories whose bucket already holds any frame, and fetch the
system-level shadow only for COMBO accessories. MQTT must never gate a
primary HTTP path; the per-device cadence is the only allowed limiter.
"""

import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_DEVICE_SN,
    PAYLOAD_DEVICE,
    PAYLOAD_MQTT_LAST,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_DEVICE_ID = "dev-1"
_PARENT_SN = "SN-PARENT-1"
_SYSTEM_ID = "573702884982521856"


def _bare_coordinator() -> JackerySolarVaultCoordinator:
    """Create a coordinator shell for the shadow-poll policy without HA setup."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._last_shadow_query = {}  # ruff:ignore[private-member-access]
    coordinator._subdevice_query_interval_sec = 0  # ruff:ignore[private-member-access]
    return coordinator


@pytest.mark.asyncio()
async def test_shadow_poll_runs_despite_fresh_mqtt_and_no_accessories() -> None:
    """Fresh MQTT frames and an empty accessory list must not skip the poll.

    The old gates silently starved the system-level shadow (the only HTTP
    source of the SystemBody config keys) whenever MQTT was alive or the
    accessory enumeration was empty.
    """
    coordinator = _bare_coordinator()
    snapshot = {
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {FIELD_DEVICE_SN: _PARENT_SN},
            PAYLOAD_MQTT_LAST: {"received_at_monotonic": time.monotonic()},
        },
    }

    with patch.object(
        coordinator,
        "_async_apply_shadows_for_entry",
        AsyncMock(return_value=False),
    ) as apply_shadows:
        await coordinator._async_shadow_fallback_for_missing(snapshot)  # ruff:ignore[private-member-access]

    apply_shadows.assert_awaited_once()
    await_args = apply_shadows.await_args
    assert await_args is not None
    assert await_args.kwargs["parent_sn"] == _PARENT_SN
    assert await_args.args[2] == []  # no accessories needed


@pytest.mark.asyncio()
async def test_system_shadow_is_fetched_without_a_combo_accessory() -> None:
    """A resolvable system id alone triggers the system-shadow fetch."""
    coordinator = _bare_coordinator()
    body = {"deviceSn": _PARENT_SN, "workModel": 2}
    get_system_shadow = AsyncMock(return_value=body)
    cast("Any", coordinator).api = SimpleNamespace(
        async_get_system_shadow=get_system_shadow,
    )

    result = await coordinator._async_fetch_system_shadow_body(  # ruff:ignore[private-member-access]
        _DEVICE_ID,
        parent_sn=_PARENT_SN,
        system_id=_SYSTEM_ID,
    )

    assert result == body
    get_system_shadow.assert_awaited_once_with(
        device_sn=_PARENT_SN,
        diy_sn=_SYSTEM_ID,
    )


@pytest.mark.asyncio()
async def test_system_shadow_skips_only_without_system_id() -> None:
    """No system id is the single remaining reason to skip the fetch."""
    coordinator = _bare_coordinator()
    get_system_shadow = AsyncMock()
    cast("Any", coordinator).api = SimpleNamespace(
        async_get_system_shadow=get_system_shadow,
    )

    result = await coordinator._async_fetch_system_shadow_body(  # ruff:ignore[private-member-access]
        _DEVICE_ID,
        parent_sn=_PARENT_SN,
        system_id=None,
    )

    assert result is None
    get_system_shadow.assert_not_awaited()
