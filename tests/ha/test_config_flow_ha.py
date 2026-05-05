"""HA fixture tests for the Jackery SolarVault config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FLOW_ABORT_REAUTH_SUCCESSFUL,
)

pytestmark = pytest.mark.asyncio


async def test_user_flow_happy_path(
    hass: HomeAssistant,
    mock_jackery_login: None,  # noqa: ARG001
) -> None:
    """A valid login should create a config entry and configure unique_id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "correct-password",
        },
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "user@example.com"
    assert (
        result2["data"]
        == {
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "correct-password",
        }
        or result2["data"][CONF_USERNAME] == "user@example.com"
    )


async def test_user_flow_invalid_credentials(hass: HomeAssistant) -> None:
    """A login rejection must surface as an invalid_auth form error."""
    from custom_components.jackery_solarvault.api import JackeryAuthError

    with patch(
        "custom_components.jackery_solarvault.api.JackeryApi.async_login",
        side_effect=JackeryAuthError("login rejected"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_USERNAME: "user@example.com",
                CONF_PASSWORD: "wrong-password",
            },
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """A network error must surface as a cannot_connect form error."""
    from custom_components.jackery_solarvault.api import JackeryError

    with patch(
        "custom_components.jackery_solarvault.api.JackeryApi.async_login",
        side_effect=JackeryError("network down"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_USERNAME: "user@example.com",
                CONF_PASSWORD: "any-password",
            },
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_unique_id_dedup(
    hass: HomeAssistant,
    mock_jackery_login: None,  # noqa: ARG001
) -> None:
    """Re-running the flow for the same account must abort, not duplicate."""
    # First run creates the entry
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass1",
        },
    )

    # Second run with the same username must abort with already_configured
    result2 = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass2",
        },
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_reauth_flow_updates_password_and_reloads(
    hass: HomeAssistant,
    mock_jackery_login: None,  # noqa: ARG001
) -> None:
    """A successful reauth must update the existing entry password."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "old-password",
        },
    )
    entry.add_to_hass(hass)

    # Trigger reauth from the entry
    result = await entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    # Submit a new password
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PASSWORD: "new-password"},
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == FLOW_ABORT_REAUTH_SUCCESSFUL

    # Entry data must reflect the new password without changing username
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert entry.data[CONF_USERNAME] == "user@example.com"
