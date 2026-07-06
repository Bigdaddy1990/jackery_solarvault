"""Config-flow coverage for Jackery Shelly cloud pairing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault.client.api import (
    JackeryAuthError,
    JackeryError,
)
from custom_components.jackery_solarvault.config_flow import JackeryConfigFlow
from custom_components.jackery_solarvault.const import (
    FLOW_ABORT_SHELLY_AUTH_URL_FAILED,
    FLOW_ABORT_SHELLY_NO_DEVICES,
    FLOW_ABORT_SHELLY_REAUTH_REQUIRED,
    FLOW_ABORT_SHELLY_SUCCESSFUL,
    FLOW_STEP_SHELLY,
    FLOW_STEP_SHELLY_FINISH,
)
from homeassistant.const import CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType


def _flow_with_entry(api: object) -> tuple[JackeryConfigFlow, SimpleNamespace]:
    """Build a Shelly pairing flow bound to a fake configured entry."""
    flow = JackeryConfigFlow()
    entry = SimpleNamespace(
        data={CONF_USERNAME: "owner@example.com"},
        runtime_data=SimpleNamespace(api=api),
    )
    return flow, entry


@pytest.mark.asyncio()
async def test_shelly_step_opens_external_auth_url() -> None:
    """Shelly pairing starts by opening the Jackery-owned OAuth URL."""
    api = SimpleNamespace(
        async_get_shelly_auth_url=AsyncMock(
            return_value={"authUrl": "https://shelly.example/authorize"}
        )
    )
    flow, entry = _flow_with_entry(api)

    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_shelly()

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["step_id"] == FLOW_STEP_SHELLY
    assert result["url"] == "https://shelly.example/authorize"
    api.async_get_shelly_auth_url.assert_awaited_once_with()


@pytest.mark.asyncio()
async def test_shelly_step_resume_completes_external_step() -> None:
    """Returning from the external page advances to the finish step."""
    flow, entry = _flow_with_entry(SimpleNamespace())

    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_shelly({})

    assert result["type"] is FlowResultType.EXTERNAL_STEP_DONE
    assert result["step_id"] == FLOW_STEP_SHELLY_FINISH


@pytest.mark.asyncio()
async def test_shelly_step_aborts_on_auth_failure() -> None:
    """Credential rejection does not keep the reconfigure flow open."""
    api = SimpleNamespace(
        async_get_shelly_auth_url=AsyncMock(side_effect=JackeryAuthError("bad auth"))
    )
    flow, entry = _flow_with_entry(api)

    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_shelly()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_SHELLY_REAUTH_REQUIRED


@pytest.mark.asyncio()
async def test_shelly_step_aborts_without_auth_url() -> None:
    """An empty cloud auth URL is reported as a pairing-start failure."""
    api = SimpleNamespace(async_get_shelly_auth_url=AsyncMock(return_value={}))
    flow, entry = _flow_with_entry(api)

    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_shelly()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_SHELLY_AUTH_URL_FAILED


@pytest.mark.asyncio()
async def test_shelly_finish_reloads_when_devices_are_bound() -> None:
    """Successful Shelly authorization reloads the entry after devices appear."""
    api = SimpleNamespace(
        async_get_shelly_devices=AsyncMock(return_value=[{"deviceId": "shelly-1"}])
    )
    flow, entry = _flow_with_entry(api)
    reload_result = {
        "type": FlowResultType.ABORT,
        "reason": FLOW_ABORT_SHELLY_SUCCESSFUL,
    }

    with (
        patch.object(flow, "_get_reconfigure_entry", return_value=entry),
        patch.object(
            flow,
            "async_update_reload_and_abort",
            Mock(return_value=reload_result),
        ) as update_reload,
    ):
        result = await flow.async_step_shelly_finish()

    assert result == reload_result
    api.async_get_shelly_devices.assert_awaited_once_with()
    update_reload.assert_called_once_with(
        entry,
        reason=FLOW_ABORT_SHELLY_SUCCESSFUL,
    )


@pytest.mark.asyncio()
async def test_shelly_finish_aborts_without_bound_devices() -> None:
    """Finishing Shelly pairing without cloud devices stays explicit."""
    api = SimpleNamespace(async_get_shelly_devices=AsyncMock(return_value=[]))
    flow, entry = _flow_with_entry(api)

    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_shelly_finish()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_SHELLY_NO_DEVICES


@pytest.mark.asyncio()
async def test_shelly_finish_aborts_backend_errors_as_no_devices() -> None:
    """Cloud confirmation errors do not create a false success state."""
    api = SimpleNamespace(
        async_get_shelly_devices=AsyncMock(side_effect=JackeryError("not ready"))
    )
    flow, entry = _flow_with_entry(api)

    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_shelly_finish()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_SHELLY_NO_DEVICES
