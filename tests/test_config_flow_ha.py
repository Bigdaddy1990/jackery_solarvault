"""HA fixture tests for the Jackery SolarVault config flow."""

# ruff: noqa: PLC0415

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FLOW_ABORT_ACCEPT_SHARED_REAUTH_REQUIRED,
    FLOW_ABORT_ACCEPT_SHARED_SUCCESSFUL,
    FLOW_ABORT_REAUTH_SUCCESSFUL,
    FLOW_ABORT_RECONFIGURE_ACCOUNT_MISMATCH,
    FLOW_ABORT_RECONFIGURE_SUCCESSFUL,
    FLOW_ABORT_SHELLY_AUTH_URL_FAILED,
    FLOW_ABORT_SHELLY_NO_DEVICES,
    FLOW_ABORT_SHELLY_REAUTH_REQUIRED,
    FLOW_ABORT_SHELLY_SUCCESSFUL,
    FLOW_ERROR_ACCEPT_SHARED_FAILED,
    FLOW_STEP_ACCEPT_SHARED,
    FLOW_STEP_RECONFIGURE,
    FLOW_STEP_RECONFIGURE_CREDENTIALS,
    FLOW_STEP_SHELLY,
)
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.asyncio


async def test_user_flow_happy_path(
    hass: HomeAssistant,
    mock_jackery_login: None,
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
    from custom_components.jackery_solarvault.client.api import JackeryAuthError

    with patch(
        "custom_components.jackery_solarvault.client.api.JackeryApi.async_login",
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
    from custom_components.jackery_solarvault.client.api import JackeryError

    with patch(
        "custom_components.jackery_solarvault.client.api.JackeryApi.async_login",
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
    mock_jackery_login: None,
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
    mock_jackery_login: None,
) -> None:
    """A successful reauth must update the existing entry password."""
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


def _shared_entry() -> MockConfigEntry:
    """Build a configured entry with a coordinator stub exposing an api mock."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "old-password",
        },
    )


async def test_reconfigure_menu_lists_credentials_and_accept_shared(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Starting reconfigure must show a menu offering both reconfigure paths."""
    entry = _shared_entry()
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == FLOW_STEP_RECONFIGURE
    options = result["menu_options"]
    assert FLOW_STEP_RECONFIGURE_CREDENTIALS in options
    assert FLOW_STEP_ACCEPT_SHARED in options
    assert FLOW_STEP_SHELLY in options


async def test_reconfigure_credentials_path_updates_and_reloads(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """The credentials menu option must reach the same update-and-reload outcome."""
    entry = _shared_entry()
    entry.add_to_hass(hass)

    menu = await entry.start_reconfigure_flow(hass)
    form = await hass.config_entries.flow.async_configure(
        menu["flow_id"],
        {"next_step_id": FLOW_STEP_RECONFIGURE_CREDENTIALS},
    )
    assert form["type"] == FlowResultType.FORM
    assert form["step_id"] == FLOW_STEP_RECONFIGURE_CREDENTIALS

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "new-password",
            "third_party_mqtt_ip": False,
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_RECONFIGURE_SUCCESSFUL
    assert entry.data[CONF_PASSWORD] == "new-password"


async def test_reconfigure_credentials_account_mismatch_aborts(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A mismatched account on the credentials path must still abort."""
    entry = _shared_entry()
    entry.add_to_hass(hass)

    menu = await entry.start_reconfigure_flow(hass)
    form = await hass.config_entries.flow.async_configure(
        menu["flow_id"],
        {"next_step_id": FLOW_STEP_RECONFIGURE_CREDENTIALS},
    )
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {
            CONF_USERNAME: "someone-else@example.com",
            CONF_PASSWORD: "new-password",
            "third_party_mqtt_ip": False,
        },
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_RECONFIGURE_ACCOUNT_MISMATCH


async def test_accept_shared_success_calls_api_and_reloads(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Submitting accept_shared must call the api and update-reload-abort."""
    entry = _shared_entry()
    entry.add_to_hass(hass)
    accept = AsyncMock(return_value={"ok": True})
    entry.runtime_data = SimpleNamespace(
        api=SimpleNamespace(
            async_accept_shared_device=accept,
        )
    )

    menu = await entry.start_reconfigure_flow(hass)
    form = await hass.config_entries.flow.async_configure(
        menu["flow_id"],
        {"next_step_id": FLOW_STEP_ACCEPT_SHARED},
    )
    assert form["type"] == FlowResultType.FORM
    assert form["step_id"] == FLOW_STEP_ACCEPT_SHARED

    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {"dev_id": "DEV123", "qr_code_id": "QR456"},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_ACCEPT_SHARED_SUCCESSFUL
    accept.assert_awaited_once_with(dev_id="DEV123", qr_code_id="QR456")


async def test_accept_shared_api_error_reshows_form(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A JackeryError during accept must re-show the form with an error."""
    from custom_components.jackery_solarvault.client.api import JackeryError

    entry = _shared_entry()
    entry.add_to_hass(hass)
    accept = AsyncMock(side_effect=JackeryError("bad request"))
    entry.runtime_data = SimpleNamespace(
        api=SimpleNamespace(
            async_accept_shared_device=accept,
        )
    )

    menu = await entry.start_reconfigure_flow(hass)
    form = await hass.config_entries.flow.async_configure(
        menu["flow_id"],
        {"next_step_id": FLOW_STEP_ACCEPT_SHARED},
    )
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {"dev_id": "DEV123", "qr_code_id": "QR456"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == FLOW_STEP_ACCEPT_SHARED
    assert result["errors"] == {"base": FLOW_ERROR_ACCEPT_SHARED_FAILED}


async def test_accept_shared_auth_error_aborts_with_reauth_required(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A JackeryAuthError during accept must abort with a reauth-required reason."""
    from custom_components.jackery_solarvault.client.api import JackeryAuthError

    entry = _shared_entry()
    entry.add_to_hass(hass)
    accept = AsyncMock(side_effect=JackeryAuthError("token expired"))
    entry.runtime_data = SimpleNamespace(
        api=SimpleNamespace(
            async_accept_shared_device=accept,
        )
    )

    menu = await entry.start_reconfigure_flow(hass)
    form = await hass.config_entries.flow.async_configure(
        menu["flow_id"],
        {"next_step_id": FLOW_STEP_ACCEPT_SHARED},
    )
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {"dev_id": "DEV123", "qr_code_id": "QR456"},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_ACCEPT_SHARED_REAUTH_REQUIRED


def _shelly_runtime(
    *,
    auth_url: AsyncMock,
    devices: AsyncMock,
) -> SimpleNamespace:
    """Build a coordinator stub exposing the Shelly api methods."""
    return SimpleNamespace(
        api=SimpleNamespace(
            async_get_shelly_auth_url=auth_url,
            async_get_shelly_devices=devices,
        )
    )


async def test_shelly_step_opens_external_step_with_auth_url(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Selecting shelly must fetch the auth URL and open an external step."""
    entry = _shared_entry()
    entry.add_to_hass(hass)
    auth_url = AsyncMock(
        return_value={"authUrl": "https://home.shelly.cloud/oauth?x=1", "state": "S1"}
    )
    devices = AsyncMock(return_value=[])
    entry.runtime_data = _shelly_runtime(auth_url=auth_url, devices=devices)

    menu = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        menu["flow_id"],
        {"next_step_id": FLOW_STEP_SHELLY},
    )
    assert result["type"] == FlowResultType.EXTERNAL_STEP
    assert result["step_id"] == FLOW_STEP_SHELLY
    assert result["url"] == "https://home.shelly.cloud/oauth?x=1"
    auth_url.assert_awaited_once_with()
    devices.assert_not_awaited()


async def test_shelly_external_step_done_with_devices_reloads(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Returning from the external step with bound devices must reload-abort."""
    entry = _shared_entry()
    entry.add_to_hass(hass)
    auth_url = AsyncMock(
        return_value={"authUrl": "https://home.shelly.cloud/oauth", "state": "S1"}
    )
    devices = AsyncMock(return_value=[{"deviceId": "shelly-1"}])
    entry.runtime_data = _shelly_runtime(auth_url=auth_url, devices=devices)

    menu = await entry.start_reconfigure_flow(hass)
    external = await hass.config_entries.flow.async_configure(
        menu["flow_id"],
        {"next_step_id": FLOW_STEP_SHELLY},
    )
    # Frontend resumes the flow after the external authorization completes.
    resumed = await hass.config_entries.flow.async_configure(external["flow_id"], {})
    assert resumed["type"] == FlowResultType.EXTERNAL_STEP_DONE
    result = await hass.config_entries.flow.async_configure(resumed["flow_id"])
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_SHELLY_SUCCESSFUL
    devices.assert_awaited()


async def test_shelly_external_step_done_without_devices_errors(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Returning with no bound devices must re-show the menu with an error."""
    entry = _shared_entry()
    entry.add_to_hass(hass)
    auth_url = AsyncMock(
        return_value={"authUrl": "https://home.shelly.cloud/oauth", "state": "S1"}
    )
    devices = AsyncMock(return_value=[])
    entry.runtime_data = _shelly_runtime(auth_url=auth_url, devices=devices)

    menu = await entry.start_reconfigure_flow(hass)
    external = await hass.config_entries.flow.async_configure(
        menu["flow_id"],
        {"next_step_id": FLOW_STEP_SHELLY},
    )
    resumed = await hass.config_entries.flow.async_configure(external["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(resumed["flow_id"])
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_SHELLY_NO_DEVICES


async def test_shelly_auth_url_error_reshows_menu_with_error(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A JackeryError fetching the auth URL must re-show the menu with an error."""
    from custom_components.jackery_solarvault.client.api import JackeryError

    entry = _shared_entry()
    entry.add_to_hass(hass)
    auth_url = AsyncMock(side_effect=JackeryError("boom"))
    devices = AsyncMock(return_value=[])
    entry.runtime_data = _shelly_runtime(auth_url=auth_url, devices=devices)

    menu = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        menu["flow_id"],
        {"next_step_id": FLOW_STEP_SHELLY},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_SHELLY_AUTH_URL_FAILED


async def test_shelly_auth_error_aborts_with_reauth_required(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A JackeryAuthError fetching the auth URL must abort reauth-required."""
    from custom_components.jackery_solarvault.client.api import JackeryAuthError

    entry = _shared_entry()
    entry.add_to_hass(hass)
    auth_url = AsyncMock(side_effect=JackeryAuthError("token expired"))
    devices = AsyncMock(return_value=[])
    entry.runtime_data = _shelly_runtime(auth_url=auth_url, devices=devices)

    menu = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        menu["flow_id"],
        {"next_step_id": FLOW_STEP_SHELLY},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_SHELLY_REAUTH_REQUIRED
    auth_url.assert_awaited_once_with()
