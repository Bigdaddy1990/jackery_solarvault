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
    FLOW_ERROR_ACCEPT_SHARED_FAILED,
    FLOW_ERROR_BASE,
    FLOW_STEP_ACCEPT_SHARED,
)
from homeassistant.const import CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType


def _flow_with_entry(api: object) -> tuple[JackeryConfigFlow, SimpleNamespace]:
    """Build an accept-shared flow bound to a fake configured entry."""
    flow = JackeryConfigFlow()
    entry = SimpleNamespace(
        data={CONF_USERNAME: "owner@example.com"},
        runtime_data=SimpleNamespace(api=api),
    )
    return flow, entry


@pytest.mark.asyncio()
async def test_accept_shared_step_shows_invitation_form() -> None:
    """Accept-shared reconfigure step asks for app invitation identifiers."""
    flow, entry = _flow_with_entry(SimpleNamespace())

    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_accept_shared()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == FLOW_STEP_ACCEPT_SHARED
    assert result["errors"] == {}


@pytest.mark.asyncio()
async def test_accept_shared_step_calls_cloud_and_reloads_entry() -> None:
    """Submitted invitation data is forwarded to the app accept-bind endpoint."""
    api = SimpleNamespace(async_accept_shared_device=AsyncMock(return_value={}))
    flow, entry = _flow_with_entry(api)
    reload_result = {
        "type": FlowResultType.ABORT,
        "reason": FLOW_ABORT_ACCEPT_SHARED_SUCCESSFUL,
    }

    with (
        patch.object(flow, "_get_reconfigure_entry", return_value=entry),
        patch.object(
            flow,
            "async_update_reload_and_abort",
            Mock(return_value=reload_result),
        ) as update_reload,
    ):
        result = await flow.async_step_accept_shared({
            CONF_SHARED_DEV_ID: "dev-123",
            CONF_SHARED_QR_CODE_ID: "qr-456",
        })

    assert result == reload_result
    api.async_accept_shared_device.assert_awaited_once_with(
        dev_id="dev-123",
        qr_code_id="qr-456",
    )
    update_reload.assert_called_once_with(
        entry,
        reason=FLOW_ABORT_ACCEPT_SHARED_SUCCESSFUL,
    )


@pytest.mark.asyncio()
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


@pytest.mark.asyncio()
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
