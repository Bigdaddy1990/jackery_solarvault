"""Setup/reload login tolerates a transient rejection before reauth.

Owner escalation 2026-07-05: reauth kept pausing polling. Root cause in our
code: ``OptionsFlowWithReload`` reloads the whole entry on every options
change, and each reload re-runs ``api.async_login()``. On the single-session
Jackery account a reload that races the mobile app (or a burst of option
toggles) gets a transient JackeryAuthError / rate-limit, which escalated
straight to ``ConfigEntryAuthFailed`` → reauth → polling paused. The poll path
already tolerates one transient 401; the setup login must retry too and only
reauth on a persistent rejection.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault import (
    _async_authenticate_api_layer,  # noqa: PLC2701  # test drives the module-private setup login helper
)
from custom_components.jackery_solarvault.client.api import JackeryAuthError
from custom_components.jackery_solarvault.const import SETUP_LOGIN_MAX_ATTEMPTS
from homeassistant.exceptions import ConfigEntryAuthFailed

_MODULE = "custom_components.jackery_solarvault"
_TRANSIENT_THEN_SUCCESS_ATTEMPTS = 2


def _api(login: AsyncMock) -> MagicMock:
    api = MagicMock(name="JackeryApi")
    api.async_login = login
    api.mqtt_session_snapshot = MagicMock(return_value=None)
    return api


@pytest.mark.asyncio()
async def test_transient_rejection_retries_without_reauth() -> None:
    """One transient rejection is retried and setup succeeds — no reauth."""
    api = _api(AsyncMock(side_effect=[JackeryAuthError("transient"), None]))
    entry = MagicMock()
    entry.entry_id = "entry-1"

    with (
        patch(f"{_MODULE}.async_load_mqtt_session", AsyncMock(return_value=None)),
        patch(
            f"{_MODULE}._async_prime_entry_bootstrap_mqtt_session",
            AsyncMock(return_value=None),
        ),
        patch(f"{_MODULE}.asyncio.sleep", AsyncMock()),
    ):
        await _async_authenticate_api_layer(MagicMock(), entry, api)

    # One transient rejection + one success = exactly two login attempts.
    assert api.async_login.await_count == _TRANSIENT_THEN_SUCCESS_ATTEMPTS


@pytest.mark.asyncio()
async def test_persistent_rejection_triggers_reauth() -> None:
    """A rejection that persists across all attempts still reauths."""
    api = _api(AsyncMock(side_effect=JackeryAuthError("bad-credentials")))
    entry = MagicMock()
    entry.entry_id = "entry-1"

    with (
        patch(f"{_MODULE}.async_load_mqtt_session", AsyncMock(return_value=None)),
        patch(
            f"{_MODULE}._async_prime_entry_bootstrap_mqtt_session",
            AsyncMock(return_value=None),
        ),
        patch(f"{_MODULE}.asyncio.sleep", AsyncMock()),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await _async_authenticate_api_layer(MagicMock(), entry, api)

    assert api.async_login.await_count == SETUP_LOGIN_MAX_ATTEMPTS
