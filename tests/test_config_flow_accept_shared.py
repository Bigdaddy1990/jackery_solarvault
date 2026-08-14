"""Config-flow coverage for accepting Jackery shared-device invitations."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault.client.api import (
    JackeryAuthError,
    JackeryError,
)
from custom_components.jackery_solarvault.config_flow import JackeryConfigFlow
from custom_components.jackery_solarvault.const import (
    CONF_SHARED_DEV_ID,
    CONF_SHARED_QR_CODE_ID,
    FLOW_ABORT_ACCEPT_SHARED_REAUTH_REQUIRED,
    FLOW_ABORT_ACCEPT_SHARED_SUCCESSFUL,
    FLOW_ABORT_RECONFIGURE_ENTRY_MISSING,
    FLOW_ERROR_ACCEPT_SHARED_FAILED,
    FLOW_ERROR_BASE,
    FLOW_STEP_ACCEPT_SHARED,
)
from homeassistant.const import CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType


def _flow_with_entry(coordinator: object) -> tuple[JackeryConfigFlow, SimpleNamespace]:
    """Build an accept-shared flow bound to a fake configured entry.

    The accept-shared step routes through ``coordinator.async_accept_shared_device``
    (the Coordinator-routed design), so ``runtime_data`` IS the coordinator
    surface rather than a nested ``api`` attribute.
    """
    flow = JackeryConfigFlow()
    entry = SimpleNamespace(
        data={CONF_USERNAME: "owner@example.com"},
        runtime_data=coordinator,
    )
    return flow, entry


@pytest.mark.asyncio
async def test_accept_shared_step_shows_invitation_form() -> None:
    """Accept-shared reconfigure step asks for app invitation identifiers."""
    flow, entry = _flow_with_entry(SimpleNamespace())

    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_accept_shared()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == FLOW_STEP_ACCEPT_SHARED
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_accept_shared_step_calls_cloud_and_reloads_entry() -> None:
    """Submitted invitation data is forwarded to the app accept-bind endpoint.

    On success the flow aborts and schedules a coordinator discovery
    refresh instead of reloading the whole entry — the new shared device
    appears on the next discovery pass without tearing the entry down.
    """
    coordinator = SimpleNamespace(
        async_accept_shared_device=AsyncMock(return_value={}),
        async_schedule_discovery_refresh=Mock(),
    )
    flow, entry = _flow_with_entry(coordinator)

    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_accept_shared({
            CONF_SHARED_DEV_ID: "dev-123",
            CONF_SHARED_QR_CODE_ID: "qr-456",
        })

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_ACCEPT_SHARED_SUCCESSFUL
    coordinator.async_accept_shared_device.assert_awaited_once_with(
        dev_id="dev-123",
        qr_code_id="qr-456",
    )
    coordinator.async_schedule_discovery_refresh.assert_called_once_with()


@pytest.mark.asyncio
async def test_accept_shared_step_aborts_on_auth_failure() -> None:
    """Credential rejection does not pause live data through this flow."""
    api = SimpleNamespace(
        async_accept_shared_device=AsyncMock(side_effect=JackeryAuthError("bad auth"))
    )
    flow, entry = _flow_with_entry(api)

    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_accept_shared({
            CONF_SHARED_DEV_ID: "dev-123",
            CONF_SHARED_QR_CODE_ID: "qr-456",
        })

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_ACCEPT_SHARED_REAUTH_REQUIRED


@pytest.mark.asyncio
async def test_accept_shared_step_aborts_when_runtime_data_missing() -> None:
    """Submitting without a live coordinator aborts instead of crashing.

    ``runtime_data`` is unset while the entry is unloaded, so a submitted
    invitation cannot reach the accept-bind endpoint and the flow aborts.
    """
    flow, entry = _flow_with_entry(None)

    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_accept_shared({
            CONF_SHARED_DEV_ID: "dev-123",
            CONF_SHARED_QR_CODE_ID: "qr-456",
        })

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_RECONFIGURE_ENTRY_MISSING


@pytest.mark.asyncio
async def test_accept_shared_step_keeps_form_on_backend_error() -> None:
    """Non-auth backend failures keep the form open with a localized error."""
    api = SimpleNamespace(
        async_accept_shared_device=AsyncMock(side_effect=JackeryError("bad qr"))
    )
    flow, entry = _flow_with_entry(api)

    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_accept_shared({
            CONF_SHARED_DEV_ID: "dev-123",
            CONF_SHARED_QR_CODE_ID: "qr-456",
        })

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == FLOW_STEP_ACCEPT_SHARED
    assert result["errors"] == {FLOW_ERROR_BASE: FLOW_ERROR_ACCEPT_SHARED_FAILED}
