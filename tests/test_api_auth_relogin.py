"""Behavior tests: one automatic re-login when a live session is rejected.

Live problem: token rotation on single-session Jackery accounts returns
HTTP 401 (without the backend "token expired" code 10402), which previously
skipped every auto re-login path and escalated straight to HA reauth even
though the stored credentials were still valid.

Contract under test (owner directive):
* 401/credential rejection during operation -> exactly ONE automatic full
  ``async_login`` + request retry before ``JackeryAuthError`` propagates.
* Loop protection: at most one automatic re-login per 60s per API client.
"""

from typing import Any, Self, cast
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.jackery_solarvault.client import api as api_module
from custom_components.jackery_solarvault.client.api import (
    JackeryApi,
    JackeryAuthError,
)
from custom_components.jackery_solarvault.const import (
    CODE_OK,
    FIELD_CODE,
    FIELD_DATA,
    FIELD_MSG,
)

_UNAUTHORIZED_BODY = {FIELD_MSG: "Unauthorized"}
_OK_BODY = {FIELD_CODE: CODE_OK, FIELD_MSG: "success", FIELD_DATA: {"ok": True}}


class _FakeResponse:
    """Minimal aiohttp response stand-in usable as an async context manager."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self._body = body

    async def json(self, content_type: str | None = None) -> dict[str, Any]:
        return self._body

    async def text(self) -> str:
        return str(self._body)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeSession:
    """HTTP boundary mock returning scripted responses in order."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.request_count = 0

    def get(self, url: str, **kwargs: object) -> _FakeResponse:
        self.request_count += 1
        return self._responses.pop(0)


_RETRY_AFTER_RELOGIN_REQUESTS = 2
_BURST_TOTAL_REQUESTS = 3
_TWO_RELOGINS = 2
_TWO_RECOVERED_CYCLES_REQUESTS = 4


class _FakeClock:
    """Deterministic replacement for the ``time`` module inside api.py."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now


def _make_api(session: _FakeSession) -> JackeryApi:
    """Build a logged-in API client whose transport boundary is scripted."""
    api = JackeryApi(cast("Any", session), "tester@example.com", "secret")
    api._token = "token-1"  # noqa: SLF001  # seed an active session without real login IO
    return api


def _login_mock(api: JackeryApi) -> AsyncMock:
    """Successful full re-login boundary mock that rotates the session token."""

    def _login() -> str:
        api._token = "token-2"  # noqa: SLF001
        return "token-2"

    return AsyncMock(side_effect=_login)


@pytest.mark.asyncio()
async def test_401_triggers_one_relogin_then_retry_succeeds() -> None:
    """A rejected session recovers via one automatic re-login + retry."""
    session = _FakeSession([
        _FakeResponse(401, _UNAUTHORIZED_BODY),
        _FakeResponse(200, _OK_BODY),
    ])
    api = _make_api(session)
    login = _login_mock(api)

    with patch.object(api, "async_login", login):
        result = await api.async_get_user_info()

    assert result == {"ok": True}
    login.assert_awaited_once()
    assert session.request_count == _RETRY_AFTER_RELOGIN_REQUESTS


@pytest.mark.asyncio()
async def test_401_then_failed_relogin_propagates_auth_error() -> None:
    """When the one automatic re-login fails, JackeryAuthError propagates."""
    session = _FakeSession([_FakeResponse(401, _UNAUTHORIZED_BODY)])
    api = _make_api(session)
    login = AsyncMock(side_effect=JackeryAuthError("Login rejected (code=10401)"))

    with (
        patch.object(api, "async_login", login),
        pytest.raises(JackeryAuthError),
    ):
        await api.async_get_user_info()

    login.assert_awaited_once()
    assert session.request_count == 1


@pytest.mark.asyncio()
async def test_second_401_burst_within_cooldown_does_not_relogin_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two 401 bursts inside 60s trigger exactly ONE automatic re-login."""
    clock = _FakeClock()
    monkeypatch.setattr(api_module, "time", clock)
    session = _FakeSession([
        _FakeResponse(401, _UNAUTHORIZED_BODY),
        _FakeResponse(200, _OK_BODY),
        _FakeResponse(401, _UNAUTHORIZED_BODY),
    ])
    api = _make_api(session)
    login = _login_mock(api)

    with patch.object(api, "async_login", login):
        first = await api.async_get_user_info()
        clock.now += 30.0
        with pytest.raises(JackeryAuthError):
            await api.async_get_user_info()

    assert first == {"ok": True}
    login.assert_awaited_once()
    assert session.request_count == _BURST_TOTAL_REQUESTS


@pytest.mark.asyncio()
async def test_401_after_cooldown_expiry_allows_new_relogin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the 60s cooldown has elapsed a new rejection may re-login again."""
    clock = _FakeClock()
    monkeypatch.setattr(api_module, "time", clock)
    session = _FakeSession([
        _FakeResponse(401, _UNAUTHORIZED_BODY),
        _FakeResponse(200, _OK_BODY),
        _FakeResponse(401, _UNAUTHORIZED_BODY),
        _FakeResponse(200, _OK_BODY),
    ])
    api = _make_api(session)
    login = _login_mock(api)

    with patch.object(api, "async_login", login):
        first = await api.async_get_user_info()
        clock.now += 60.0
        second = await api.async_get_user_info()

    assert first == {"ok": True}
    assert second == {"ok": True}
    assert login.await_count == _TWO_RELOGINS
    assert session.request_count == _TWO_RECOVERED_CYCLES_REQUESTS
