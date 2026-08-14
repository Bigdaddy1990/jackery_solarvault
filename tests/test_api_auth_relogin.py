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

import asyncio
from typing import Any, Self, cast
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.jackery_solarvault.client import api as api_module
from custom_components.jackery_solarvault.client.api import JackeryApi, JackeryAuthError
from custom_components.jackery_solarvault.const import (
    CODE_OK,
    CODE_TOKEN_EXPIRED,
    FIELD_CODE,
    FIELD_DATA,
    FIELD_MSG,
)

_UNAUTHORIZED_BODY = {FIELD_MSG: "Unauthorized"}
_OK_BODY = {FIELD_CODE: CODE_OK, FIELD_MSG: "success", FIELD_DATA: {"ok": True}}
_TOKEN_EXPIRED_BODY = {FIELD_CODE: CODE_TOKEN_EXPIRED, FIELD_MSG: "token expired"}


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
    api._token = "token-1"  # seed an active session without real login IO  # ruff: ignore[private-member-access]
    return api


def _login_mock(api: JackeryApi) -> AsyncMock:
    """Successful full re-login boundary mock that rotates the session token."""

    def _login() -> str:
        api._token = "token-2"  # ruff: ignore[private-member-access]
        return "token-2"

    return AsyncMock(side_effect=_login)


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_401_relogin_blocked_just_before_cooldown_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejection one instant before the 60s cooldown elapses stays blocked.

    Pins the exact ``>=`` boundary in ``_auto_relogin_allowed``: only once the
    cooldown has *fully* elapsed is a new automatic re-login allowed.
    """
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
        clock.now += 59.999
        with pytest.raises(JackeryAuthError):
            await api.async_get_user_info()

    assert first == {"ok": True}
    login.assert_awaited_once()
    assert session.request_count == _BURST_TOTAL_REQUESTS


@pytest.mark.asyncio
async def test_cancelled_relogin_restores_cooldown_state() -> None:
    """A cancelled re-login restores the prior cooldown instead of consuming it.

    If ``async_login`` is cancelled mid-flight (e.g. HA shutdown), the retry
    path must roll back ``_last_auto_relogin_monotonic`` to its previous value
    before re-raising ``CancelledError`` so a genuinely valid credential is
    not locked out of a fresh automatic re-login attempt for a full cooldown
    window just because a cancellation happened to land during the login call.
    """
    session = _FakeSession([_FakeResponse(401, _UNAUTHORIZED_BODY)])
    api = _make_api(session)
    login = AsyncMock(side_effect=asyncio.CancelledError)

    assert api._last_auto_relogin_monotonic is None  # ruff: ignore[private-member-access]

    with (
        patch.object(api, "async_login", login),
        pytest.raises(asyncio.CancelledError),
    ):
        await api.async_get_user_info()

    login.assert_awaited_once()
    assert api._last_auto_relogin_monotonic is None  # ruff: ignore[private-member-access]


@pytest.mark.asyncio
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


class _ConcurrentFakeResponse:
    """Response stand-in with real event-loop checkpoints for concurrency tests.

    Mirrors ``_FakeResponse`` but awaits ``asyncio.sleep(0)`` at each async
    boundary so two in-flight requests genuinely interleave the way real
    network I/O would — without a checkpoint, cooperative scheduling would
    run one fake request to completion before the other even starts.
    """

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self._body = body

    async def json(self, content_type: str | None = None) -> dict[str, Any]:
        await asyncio.sleep(0)
        return self._body

    async def text(self) -> str:
        return str(self._body)

    async def __aenter__(self) -> Self:
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _ConcurrentFakeSession:
    """HTTP boundary mock for interleaved concurrent-request tests."""

    def __init__(self, responses: list[_ConcurrentFakeResponse]) -> None:
        self._responses = responses
        self.request_count = 0

    def get(self, url: str, **kwargs: object) -> _ConcurrentFakeResponse:
        self.request_count += 1
        return self._responses.pop(0)


def _make_concurrent_api(session: _ConcurrentFakeSession) -> JackeryApi:
    """Build a logged-in API client backed by the concurrency-aware fake session."""
    api = JackeryApi(cast("Any", session), "tester@example.com", "secret")
    api._token = "token-1"  # seed an active session without real login IO  # ruff: ignore[private-member-access]
    return api


@pytest.mark.asyncio
async def test_concurrent_token_expired_requests_relogin_only_once() -> None:
    """Two requests racing an expired shared token re-login exactly once (F-SW2-2).

    Multi-device polling fans out several requests sharing one ``JackeryApi``
    instance. Without a double-checked lock (mirroring ``_ensure_token``),
    every request that independently observes ``CODE_TOKEN_EXPIRED`` would
    unconditionally nuke ``self._token`` and re-login, rotating the
    single-session backend token out from under an in-flight sibling and
    risking a spurious ``ConfigEntryAuthFailed`` reauth on valid credentials.
    """
    session = _ConcurrentFakeSession([
        _ConcurrentFakeResponse(200, _TOKEN_EXPIRED_BODY),
        _ConcurrentFakeResponse(200, _TOKEN_EXPIRED_BODY),
        _ConcurrentFakeResponse(200, _OK_BODY),
        _ConcurrentFakeResponse(200, _OK_BODY),
    ])
    api = _make_concurrent_api(session)
    login = _login_mock(api)

    with patch.object(api, "async_login", login):
        results = await asyncio.gather(
            api.async_get_user_info(),
            api.async_get_user_info(),
        )

    assert [*results] == [{"ok": True}, {"ok": True}]
    login.assert_awaited_once()
