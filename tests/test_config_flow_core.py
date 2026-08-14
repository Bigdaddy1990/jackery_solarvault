"""Core config-flow behavior for Jackery SolarVault."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault.client.api import (
    JackeryAuthError,
    JackeryError,
)
from custom_components.jackery_solarvault.config_flow import JackeryConfigFlow
from custom_components.jackery_solarvault.const import (
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    FLOW_ABORT_REAUTH_ENTRY_MISSING,
    FLOW_ABORT_REAUTH_SUCCESSFUL,
    FLOW_ABORT_RECONFIGURE_ACCOUNT_MISMATCH,
    FLOW_ABORT_RECONFIGURE_ENTRY_MISSING,
    FLOW_ERROR_ACCOUNT_REQUIRED,
    FLOW_ERROR_BASE,
    FLOW_ERROR_CANNOT_CONNECT,
    FLOW_ERROR_INVALID_AUTH,
    FLOW_STEP_REAUTH_CONFIRM,
    FLOW_STEP_RECONFIGURE_CREDENTIALS,
    FLOW_STEP_USER,
)
from homeassistant.config_entries import UnknownEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import AbortFlow, FlowResultType

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_ACCOUNT = "owner@example.com"
_PASSWORD = "secret"


def _flow(hass: HomeAssistant | None = None) -> JackeryConfigFlow:
    """Create a config-flow instance with an optional fake hass binding."""
    flow = JackeryConfigFlow()
    flow.context = {}
    if hass is not None:
        flow.hass = hass
    return flow


@pytest.mark.asyncio
async def test_discovery_steps_abort_duplicate_or_route_to_user() -> None:
    """Discovery transports share the duplicate guard before user setup."""
    flow = _flow()
    abort_result = {"type": FlowResultType.ABORT, "reason": "already_configured"}

    with patch.object(
        flow,
        "_async_abort_duplicate_discovery",
        return_value=abort_result,
    ):
        assert (
            await flow.async_step_bluetooth(
                cast(
                    "Any",
                    SimpleNamespace(
                        name="Jackery BLE",
                        address="AA:BB:CC:DD:EE:FF",
                    ),
                )
            )
            == abort_result
        )

    duplicate_infos = {
        "async_step_dhcp": SimpleNamespace(
            hostname="jackery.local",
            ip="192.0.2.10",
        ),
        "async_step_zeroconf": SimpleNamespace(
            name="Jackery SolarVault",
            hostname="jackery.local",
            host="192.0.2.10",
        ),
    }
    for method_name, discovery_info in duplicate_infos.items():
        flow = _flow()
        with patch.object(
            flow,
            "_async_abort_duplicate_discovery",
            return_value=abort_result,
        ):
            result = await getattr(flow, method_name)(
                cast("Any", discovery_info),
            )

        assert result == abort_result

    discovery_infos = {
        "async_step_bluetooth": SimpleNamespace(
            name="Jackery BLE",
            address="AA:BB:CC:DD:EE:FF",
        ),
        "async_step_dhcp": SimpleNamespace(
            hostname="jackery.local",
            ip="192.0.2.10",
        ),
        "async_step_zeroconf": SimpleNamespace(
            name="Jackery SolarVault",
            hostname="jackery.local",
            host="192.0.2.10",
        ),
    }
    for method_name, discovery_info in discovery_infos.items():
        flow = _flow()
        user_result = {"type": FlowResultType.FORM, "step_id": FLOW_STEP_USER}
        with (
            patch.object(
                flow,
                "_async_abort_duplicate_discovery",
                return_value=None,
            ),
            patch.object(
                flow,
                "_async_handle_discovery_without_unique_id",
                AsyncMock(return_value=None),
            ),
            patch.object(flow, "async_step_user", AsyncMock(return_value=user_result)),
        ):
            result = await getattr(flow, method_name)(cast("Any", discovery_info))

        assert result == user_result


@pytest.mark.asyncio
async def test_route_discovery_to_user_sets_title_and_delegates() -> None:
    """The shared router pre-fills the display name and hands off to the user step.

    ``_async_route_discovery_to_user`` backs every discovery transport
    (Bluetooth/DHCP/Zeroconf); this pins its own contract directly so a
    regression here is caught even if a transport's fallback-name test still
    mocks the router itself away.
    """
    flow = _flow()
    user_result = {"type": FlowResultType.FORM, "step_id": FLOW_STEP_USER}

    with (
        patch.object(
            flow,
            "_async_abort_duplicate_discovery",
            return_value=None,
        ) as abort_guard,
        patch.object(
            flow,
            "_async_handle_discovery_without_unique_id",
            AsyncMock(return_value=None),
        ) as handle_discovery,
        patch.object(
            flow,
            "async_step_user",
            AsyncMock(return_value=user_result),
        ) as user_step,
    ):
        result = await flow._async_route_discovery_to_user("Jackery Device")  # ruff: ignore[private-member-access]

    assert result == user_result
    abort_guard.assert_called_once_with()
    assert flow.context["title_placeholders"] == {"name": "Jackery Device"}
    handle_discovery.assert_awaited_once_with()
    user_step.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_route_discovery_to_user_short_circuits_on_duplicate() -> None:
    """A duplicate discovery aborts before pre-filling the name or handling it."""
    flow = _flow()
    abort_result = {"type": FlowResultType.ABORT, "reason": "already_configured"}

    with (
        patch.object(
            flow,
            "_async_abort_duplicate_discovery",
            return_value=abort_result,
        ),
        patch.object(
            flow,
            "_async_handle_discovery_without_unique_id",
            AsyncMock(return_value=None),
        ) as handle_discovery,
        patch.object(
            flow,
            "async_step_user",
            AsyncMock(return_value={"type": FlowResultType.FORM}),
        ) as user_step,
    ):
        result = await flow._async_route_discovery_to_user("Jackery Device")  # ruff: ignore[private-member-access]

    assert result == abort_result
    assert "title_placeholders" not in flow.context
    handle_discovery.assert_not_awaited()
    user_step.assert_not_awaited()


async def _discovered_name(method_name: str, discovery_info: SimpleNamespace) -> str:
    """Drive one discovery step and capture the name it hands to the router."""
    flow = _flow()
    with patch.object(
        flow,
        "_async_route_discovery_to_user",
        AsyncMock(return_value={"type": FlowResultType.FORM}),
    ) as router:
        await getattr(flow, method_name)(discovery_info)

    await_args = router.await_args
    assert await_args is not None
    return cast("str", await_args.args[0])


@pytest.mark.asyncio
async def test_bluetooth_discovery_falls_back_to_address_without_name() -> None:
    """Bluetooth discovery prefers the advertised name, else the MAC address."""
    named = SimpleNamespace(name="Jackery BLE", address="AA:BB:CC:DD:EE:FF")
    assert await _discovered_name("async_step_bluetooth", named) == "Jackery BLE"

    unnamed = SimpleNamespace(name="", address="AA:BB:CC:DD:EE:FF")
    assert (
        await _discovered_name("async_step_bluetooth", unnamed) == "AA:BB:CC:DD:EE:FF"
    )


@pytest.mark.asyncio
async def test_dhcp_discovery_falls_back_to_ip_without_hostname() -> None:
    """DHCP discovery prefers the hostname, else the IP address."""
    named = SimpleNamespace(hostname="jackery.local", ip="192.0.2.10")
    assert await _discovered_name("async_step_dhcp", named) == "jackery.local"

    unnamed = SimpleNamespace(hostname="", ip="192.0.2.10")
    assert await _discovered_name("async_step_dhcp", unnamed) == "192.0.2.10"


@pytest.mark.asyncio
async def test_zeroconf_discovery_prefers_name_then_hostname_then_host() -> None:
    """Zeroconf discovery falls back through name -> hostname -> host in order."""
    full = SimpleNamespace(
        name="Jackery SolarVault",
        hostname="jackery.local",
        host="192.0.2.10",
    )
    assert await _discovered_name("async_step_zeroconf", full) == "Jackery SolarVault"

    no_name = SimpleNamespace(name="", hostname="jackery.local", host="192.0.2.10")
    assert await _discovered_name("async_step_zeroconf", no_name) == "jackery.local"

    host_only = SimpleNamespace(name="", hostname="", host="192.0.2.10")
    assert await _discovered_name("async_step_zeroconf", host_only) == "192.0.2.10"


def test_duplicate_discovery_guard_reports_current_entries() -> None:
    """The discovery guard aborts configured and in-progress duplicates."""
    flow = _flow()

    with (
        patch.object(flow, "_async_current_entries", return_value=[object()]),
        patch.object(flow, "_async_in_progress", return_value=[]),
    ):
        result = flow._async_abort_duplicate_discovery()  # ruff: ignore[private-member-access]

    assert result is not None
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"

    flow = _flow()
    with (
        patch.object(flow, "_async_current_entries", return_value=[]),
        patch.object(flow, "_async_in_progress", return_value=[object()]),
    ):
        result = flow._async_abort_duplicate_discovery()  # ruff: ignore[private-member-access]

    assert result is not None
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_in_progress"

    flow = _flow()
    with (
        patch.object(flow, "_async_current_entries", return_value=[]),
        patch.object(flow, "_async_in_progress", return_value=[]),
    ):
        assert flow._async_abort_duplicate_discovery() is None  # ruff: ignore[private-member-access]


@pytest.mark.asyncio
async def test_user_step_rejects_empty_account() -> None:
    """Empty usernames stay on the user form with a field-level error."""
    flow = _flow()

    result = await flow.async_step_user({
        CONF_USERNAME: " ",
        CONF_PASSWORD: _PASSWORD,
    })

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == FLOW_STEP_USER
    assert result["errors"] == {CONF_USERNAME: FLOW_ERROR_ACCOUNT_REQUIRED}


@pytest.mark.asyncio
async def test_user_step_without_input_shows_form() -> None:
    """The user step renders the login form before submission."""
    flow = _flow()

    result = await flow.async_step_user()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == FLOW_STEP_USER
    assert result["errors"] == {}


@pytest.mark.asyncio
async def test_user_step_aborts_when_entry_already_exists(
    hass: HomeAssistant,
) -> None:
    """The same account (unique_id) must not create a second entry.

    Duplicate protection is account-scoped via ``async_set_unique_id`` +
    ``_abort_if_unique_id_configured`` (multi-account setups stay
    possible); the abort must fire before any login attempt.
    """
    flow = _flow(hass)

    with (
        patch.object(
            flow,
            "async_set_unique_id",
            AsyncMock(return_value=None),
        ) as set_unique_id,
        patch.object(
            flow,
            "_abort_if_unique_id_configured",
            Mock(side_effect=AbortFlow("already_configured")),
        ),
        pytest.raises(AbortFlow) as abort,
    ):
        await flow.async_step_user({
            CONF_USERNAME: _ACCOUNT,
            CONF_PASSWORD: _PASSWORD,
        })

    assert abort.value.reason == "already_configured"
    set_unique_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_user_step_maps_auth_and_connect_errors(hass: HomeAssistant) -> None:
    """Login errors stay on the user form with the correct base error."""
    for side_effect, expected in (
        (JackeryAuthError("bad"), FLOW_ERROR_INVALID_AUTH),
        (JackeryError("offline"), FLOW_ERROR_CANNOT_CONNECT),
    ):
        flow = _flow(hass)
        api = SimpleNamespace(async_login=AsyncMock(side_effect=side_effect))

        with (
            patch.object(flow, "_async_current_entries", return_value=[]),
            patch.object(flow, "async_set_unique_id", AsyncMock(return_value=None)),
            patch.object(flow, "_abort_if_unique_id_configured", Mock()),
            patch(
                "custom_components.jackery_solarvault.config_flow.JackeryApi",
                return_value=api,
            ),
        ):
            result = await flow.async_step_user({
                CONF_USERNAME: _ACCOUNT,
                CONF_PASSWORD: _PASSWORD,
            })

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == FLOW_STEP_USER
        assert result["errors"] == {FLOW_ERROR_BASE: expected}


@pytest.mark.asyncio
async def test_user_step_creates_entry_with_options(hass: HomeAssistant) -> None:
    """Successful setup persists credentials and submitted option values."""
    flow = _flow(hass)
    api = SimpleNamespace(
        async_login=AsyncMock(return_value=None),
        region_code="",
        mqtt_session_snapshot=Mock(return_value=None),
    )

    with (
        patch.object(flow, "_async_current_entries", return_value=[]),
        patch.object(flow, "async_set_unique_id", AsyncMock(return_value=None)),
        patch.object(flow, "_abort_if_unique_id_configured", Mock()),
        patch(
            "custom_components.jackery_solarvault.config_flow.JackeryApi",
            return_value=api,
        ),
    ):
        result = await flow.async_step_user({
            CONF_USERNAME: f" {_ACCOUNT} ",
            CONF_PASSWORD: _PASSWORD,
            CONF_CREATE_CALCULATED_POWER_SENSORS: True,
        })

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == _ACCOUNT
    assert result["data"] == {CONF_USERNAME: _ACCOUNT, CONF_PASSWORD: _PASSWORD}
    assert result["options"][CONF_CREATE_CALCULATED_POWER_SENSORS] is True


@pytest.mark.asyncio
async def test_reconfigure_steps_abort_when_entry_missing() -> None:
    """Reconfigure entry points fail explicitly when HA no longer has the entry.

    ``_get_reconfigure_entry`` raises ``UnknownEntry`` when the entry was
    removed and ``ValueError`` when the flow source does not match
    ``SOURCE_RECONFIGURE`` (see ``ConfigFlow.async_get_known_entry`` /
    ``_reconfigure_entry_id``) -- neither is a ``KeyError``/``RuntimeError``.
    """
    for exc in (UnknownEntry("entry_id"), ValueError("wrong source")):
        for method_name in (
            "async_step_reconfigure",
            "async_step_reconfigure_credentials",
        ):
            flow = _flow()
            with patch.object(flow, "_get_reconfigure_entry", side_effect=exc):
                result = await getattr(flow, method_name)()

            assert result["type"] is FlowResultType.ABORT
            assert result["reason"] == FLOW_ABORT_RECONFIGURE_ENTRY_MISSING


@pytest.mark.asyncio
async def test_reconfigure_credentials_validates_account_before_login(
    hass: HomeAssistant,
) -> None:
    """Credential reconfigure rejects empty and mismatched accounts before API work."""
    entry = SimpleNamespace(
        data={CONF_USERNAME: _ACCOUNT},
        options={},
        unique_id=_ACCOUNT,
    )
    flow = _flow(hass)
    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_reconfigure_credentials({
            CONF_USERNAME: " ",
            CONF_PASSWORD: _PASSWORD,
        })

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == FLOW_STEP_RECONFIGURE_CREDENTIALS
    assert result["errors"] == {CONF_USERNAME: FLOW_ERROR_ACCOUNT_REQUIRED}

    flow = _flow(hass)
    with patch.object(flow, "_get_reconfigure_entry", return_value=entry):
        result = await flow.async_step_reconfigure_credentials({
            CONF_USERNAME: "other@example.com",
            CONF_PASSWORD: _PASSWORD,
        })

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_RECONFIGURE_ACCOUNT_MISMATCH


@pytest.mark.asyncio
async def test_reconfigure_credentials_maps_login_errors(
    hass: HomeAssistant,
) -> None:
    """Reconfigure login failures keep the credentials form open."""
    for side_effect, expected in (
        (JackeryAuthError("bad"), FLOW_ERROR_INVALID_AUTH),
        (JackeryError("offline"), FLOW_ERROR_CANNOT_CONNECT),
    ):
        entry = SimpleNamespace(
            data={CONF_USERNAME: _ACCOUNT},
            options={},
            unique_id=_ACCOUNT,
        )
        flow = _flow(hass)
        api = SimpleNamespace(async_login=AsyncMock(side_effect=side_effect))

        with (
            patch.object(flow, "_get_reconfigure_entry", return_value=entry),
            patch.object(flow, "async_set_unique_id", AsyncMock(return_value=None)),
            patch.object(flow, "_abort_if_unique_id_mismatch", Mock()),
            patch(
                "custom_components.jackery_solarvault.config_flow.JackeryApi",
                return_value=api,
            ),
        ):
            result = await flow.async_step_reconfigure_credentials({
                CONF_USERNAME: _ACCOUNT,
                CONF_PASSWORD: _PASSWORD,
            })

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == FLOW_STEP_RECONFIGURE_CREDENTIALS
        assert result["errors"] == {FLOW_ERROR_BASE: expected}


@pytest.mark.asyncio
async def test_reauth_confirm_missing_entry_and_empty_username() -> None:
    """Reauth aborts explicitly when the target entry cannot be used.

    ``_get_reauth_entry`` raises ``UnknownEntry`` when the entry was removed
    and ``ValueError`` when the flow source does not match ``SOURCE_REAUTH``
    -- neither is a ``KeyError``/``RuntimeError``.
    """
    for exc in (UnknownEntry("entry_id"), ValueError("wrong source")):
        flow = _flow()
        with patch.object(flow, "_get_reauth_entry", side_effect=exc):
            result = await flow.async_step_reauth_confirm({CONF_PASSWORD: _PASSWORD})

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == FLOW_ABORT_REAUTH_ENTRY_MISSING

    flow = _flow()
    entry = SimpleNamespace(data={CONF_USERNAME: ""})
    with patch.object(flow, "_get_reauth_entry", return_value=entry):
        result = await flow.async_step_reauth_confirm({CONF_PASSWORD: _PASSWORD})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_REAUTH_ENTRY_MISSING


@pytest.mark.asyncio
async def test_reauth_confirm_form_and_success(hass: HomeAssistant) -> None:
    """Reauth shows the password form and updates the entry after valid login."""
    flow = _flow()
    entry = SimpleNamespace(data={CONF_USERNAME: _ACCOUNT}, entry_id="entry-1")

    with patch.object(flow, "_get_reauth_entry", return_value=entry):
        result = await flow.async_step_reauth_confirm()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == FLOW_STEP_REAUTH_CONFIRM

    flow = _flow(hass)
    api = SimpleNamespace(
        async_login=AsyncMock(return_value=None),
        region_code="",
        mqtt_session_snapshot=Mock(return_value=None),
    )
    reload_result = {
        "type": FlowResultType.ABORT,
        "reason": FLOW_ABORT_REAUTH_SUCCESSFUL,
    }
    with (
        patch.object(flow, "_get_reauth_entry", return_value=entry),
        patch(
            "custom_components.jackery_solarvault.config_flow.JackeryApi",
            return_value=api,
        ),
        patch.object(
            flow,
            "async_update_and_abort",
            Mock(return_value=reload_result),
        ) as update_abort,
    ):
        result = await flow.async_step_reauth_confirm({CONF_PASSWORD: _PASSWORD})

    assert result == reload_result
    # Reauth persists the full login-data mapping via _entry_data_from_api_login
    # (shared with the user/reconfigure steps): the unchanged username plus the
    # new password, and region/MQTT bootstrap fields when the API surfaces them.
    # The flow uses async_update_and_abort — no entry reload is required for a
    # credential refresh.
    update_abort.assert_called_once_with(
        entry,
        data_updates={CONF_USERNAME: _ACCOUNT, CONF_PASSWORD: _PASSWORD},
        reason=FLOW_ABORT_REAUTH_SUCCESSFUL,
    )


@pytest.mark.asyncio
async def test_reauth_confirm_maps_login_errors(hass: HomeAssistant) -> None:
    """Reauth login errors keep the password form open."""
    for side_effect, expected in (
        (JackeryAuthError("bad"), FLOW_ERROR_INVALID_AUTH),
        (JackeryError("offline"), FLOW_ERROR_CANNOT_CONNECT),
    ):
        flow = _flow(hass)
        entry = SimpleNamespace(data={CONF_USERNAME: _ACCOUNT})
        api = SimpleNamespace(async_login=AsyncMock(side_effect=side_effect))

        with (
            patch.object(flow, "_get_reauth_entry", return_value=entry),
            patch(
                "custom_components.jackery_solarvault.config_flow.JackeryApi",
                return_value=api,
            ),
        ):
            result = await flow.async_step_reauth_confirm({CONF_PASSWORD: _PASSWORD})

        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == FLOW_STEP_REAUTH_CONFIRM
        assert result["errors"] == {FLOW_ERROR_BASE: expected}


@pytest.mark.asyncio
async def test_reauth_step_delegates_to_confirm() -> None:
    """The HA reauth entry point immediately shows the confirm step."""
    flow = _flow()

    with patch.object(
        flow,
        "async_step_reauth_confirm",
        AsyncMock(return_value={"type": FlowResultType.FORM}),
    ) as confirm:
        result = await flow.async_step_reauth({})

    assert result["type"] is FlowResultType.FORM
    confirm.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_reconfigure_subflows_abort_when_entry_missing() -> None:
    """The accept-shared subflow does not continue without an entry.

    ``_get_reconfigure_entry`` raises ``UnknownEntry``/``ValueError``, not
    ``KeyError``/``RuntimeError`` -- see
    ``test_reconfigure_steps_abort_when_entry_missing``.
    """
    for exc in (UnknownEntry("entry_id"), ValueError("wrong source")):
        flow = _flow()
        with patch.object(flow, "_get_reconfigure_entry", side_effect=exc):
            result = await flow.async_step_accept_shared()

        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == FLOW_ABORT_RECONFIGURE_ENTRY_MISSING
