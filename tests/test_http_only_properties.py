"""HTTP property behavior without BLE or either MQTT transport."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.client.api import (
    JackeryApiError,
    JackeryAuthError,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)


def _coordinator_with_api(**api_methods: object) -> JackerySolarVaultCoordinator:
    """Build the smallest coordinator shell for HTTP shadow fetches."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator.api = cast("Any", SimpleNamespace(**api_methods))
    return coordinator


@pytest.mark.asyncio
async def test_http_system_shadow_fetch_is_transport_independent() -> None:
    """A system shadow is fetched directly through HTTP with no Layer 5 client."""
    get_shadow = AsyncMock(return_value={"soc": 73, "batState": 1})
    coordinator = _coordinator_with_api(async_get_system_shadow=get_shadow)

    result = await coordinator._async_fetch_system_shadow_body(
        "device-1",
        parent_sn="SN-1",
        system_id="system-1",
    )

    assert result == {"soc": 73, "batState": 1}
    get_shadow.assert_awaited_once_with(device_sn="SN-1", diy_sn="system-1")
    assert not hasattr(coordinator, "_ble_listener")
    assert not hasattr(coordinator, "_mqtt")
    assert not hasattr(coordinator, "_local_mqtt")


@pytest.mark.asyncio
async def test_http_system_shadow_auth_failure_is_not_downgraded_to_empty() -> None:
    """Credential rejection remains distinguishable from an optional empty body."""
    get_shadow = AsyncMock(side_effect=JackeryAuthError("token rejected"))
    coordinator = _coordinator_with_api(async_get_system_shadow=get_shadow)

    with pytest.raises(JackeryAuthError, match="token rejected"):
        await coordinator._async_fetch_system_shadow_body(
            "device-1",
            parent_sn="SN-1",
            system_id="system-1",
        )


@pytest.mark.asyncio
async def test_http_sub_shadow_auth_failure_is_not_downgraded_to_empty() -> None:
    """Accessory shadow authentication errors also reach the reauth boundary."""
    get_shadow = AsyncMock(side_effect=JackeryAuthError("token rejected"))
    coordinator = _coordinator_with_api(async_get_sub_shadow=get_shadow)

    with pytest.raises(JackeryAuthError, match="token rejected"):
        await coordinator._async_fetch_sub_shadow_body(
            "device-1",
            dev_type=4,
            parent_sn="SN-1",
            sub_device_sn="SUB-1",
        )


@pytest.mark.asyncio
async def test_http_optional_shadow_transport_failure_returns_no_replacement() -> None:
    """A temporary optional failure produces no value that could erase state."""
    get_shadow = AsyncMock(side_effect=JackeryApiError("temporarily unavailable"))
    coordinator = _coordinator_with_api(async_get_system_shadow=get_shadow)

    result = await coordinator._async_fetch_system_shadow_body(
        "device-1",
        parent_sn="SN-1",
        system_id="system-1",
    )

    assert result is None


@pytest.mark.asyncio
async def test_http_shadow_cancellation_propagates() -> None:
    """Shutdown cancellation is never converted into an optional empty body."""
    get_shadow = AsyncMock(side_effect=asyncio.CancelledError)
    coordinator = _coordinator_with_api(async_get_system_shadow=get_shadow)

    with pytest.raises(asyncio.CancelledError):
        await coordinator._async_fetch_system_shadow_body(
            "device-1",
            parent_sn="SN-1",
            system_id="system-1",
        )
