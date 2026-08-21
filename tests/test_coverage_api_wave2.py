"""Behavioral coverage for Jackery HTTP validation and endpoint wrappers."""

from collections.abc import Callable
from http import HTTPStatus
from typing import Any, Self, cast
from unittest.mock import AsyncMock, call, patch

import aiohttp
import pytest

from custom_components.jackery_solarvault import const
from custom_components.jackery_solarvault.client import api as api_module
from custom_components.jackery_solarvault.client.api import (
    JackeryApi,
    JackeryApiError,
    JackeryAuthError,
)


class _Response:
    """Minimal aiohttp response with configurable JSON decoding behavior."""

    def __init__(
        self,
        status: int,
        body: object = None,
        *,
        raw_text: str = "raw response",
        json_error: Exception | None = None,
    ) -> None:
        self.status = status
        self.headers = {"Content-Type": "application/json"}
        self.content = object()
        self._body = body
        self._raw_text = raw_text
        self._json_error = json_error

    async def json(self, *, content_type: str | None = None) -> object:
        """Return the scripted JSON value or raise the scripted decoder error."""
        if self._json_error is not None:
            raise self._json_error
        return self._body

    async def text(self) -> str:
        """Return the scripted raw response body."""
        return self._raw_text

    async def __aenter__(self) -> Self:
        """Enter the fake response context."""
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        """Leave the fake response context without suppressing exceptions."""
        return False


class _Session:
    """Scripted aiohttp boundary recording all HTTP method calls."""

    def __init__(self, responses: list[_Response | BaseException]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def _request(self, method: str, url: str, kwargs: dict[str, Any]) -> _Response:
        self.calls.append((method, url, kwargs))
        result = self._responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def get(self, url: str, **kwargs: Any) -> _Response:
        """Record one GET."""
        return self._request("GET", url, kwargs)

    def put(self, url: str, **kwargs: Any) -> _Response:
        """Record one PUT."""
        return self._request("PUT", url, kwargs)

    def post(self, url: str, **kwargs: Any) -> _Response:
        """Record one POST."""
        return self._request("POST", url, kwargs)

    def delete(self, url: str, **kwargs: Any) -> _Response:
        """Record one DELETE."""
        return self._request("DELETE", url, kwargs)


def _api(responses: list[_Response | BaseException] | None = None) -> JackeryApi:
    """Build a client with an authenticated, fully mocked HTTP boundary."""
    session = _Session(responses or [])
    client = JackeryApi(
        cast("aiohttp.ClientSession", session), "owner@example.com", "pw"
    )
    client._token = "token-1"
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ["status", "expected_error"],
    [
        [HTTPStatus.UNAUTHORIZED, JackeryAuthError],
        [HTTPStatus.FORBIDDEN, JackeryAuthError],
        [HTTPStatus.SERVICE_UNAVAILABLE, JackeryApiError],
    ],
)
async def test_login_response_rejects_non_ok_statuses(
    status: int,
    expected_error: type[Exception],
) -> None:
    """Login distinguishes credential rejection from other HTTP failures."""
    response = cast("aiohttp.ClientResponse", _Response(status))

    with pytest.raises(expected_error, match=f"Login HTTP {status}"):
        await JackeryApi._decode_login_response(response)


@pytest.mark.asyncio
async def test_login_response_reports_invalid_json_with_bounded_raw_text() -> None:
    """A successful login status still rejects malformed response JSON."""
    response = cast(
        "aiohttp.ClientResponse",
        _Response(
            HTTPStatus.OK,
            json_error=ValueError("bad json"),
            raw_text="not-json",
        ),
    )

    with pytest.raises(JackeryApiError, match=r"invalid JSON \(response redacted\)"):
        await JackeryApi._decode_login_response(response)


@pytest.mark.asyncio
async def test_get_json_uses_token_custom_timeout_and_emits_debug_event() -> None:
    """GET uses the active token, honors its timeout override, and emits metadata."""
    client = _api([
        _Response(HTTPStatus.OK, {const.FIELD_CODE: 0, const.FIELD_DATA: {"v": 1}})
    ])
    payload_debug = AsyncMock()
    client.payload_debug_callback = payload_debug

    result = await client._get_json(
        "/v1/example",
        {"deviceId": "42"},
        request_timeout=3,
    )

    assert result[const.FIELD_DATA] == {"v": 1}
    session = cast("_Session", client._session)
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url.endswith("/v1/example")
    assert kwargs["params"] == {"deviceId": "42"}
    assert kwargs["headers"][const.FIELD_TOKEN] == "token-1"
    assert kwargs["timeout"].total == 3
    payload_debug.assert_awaited_once()
    assert payload_debug.await_args is not None
    event_factory = payload_debug.await_args.args[0]
    assert callable(event_factory)
    assert event_factory()["path"] == "/v1/example"
    assert client.diagnostics_snapshot()["requests_total"] == 1


@pytest.mark.asyncio
async def test_get_json_rejects_invalid_success_body_and_counts_timeout() -> None:
    """Malformed success JSON and network timeout remain distinct API failures."""
    invalid = _api([
        _Response(
            HTTPStatus.OK,
            json_error=ValueError("bad json"),
            raw_text="broken",
        )
    ])
    with pytest.raises(JackeryApiError, match=r"invalid JSON \(redacted\)"):
        await invalid._get_json("/broken")

    timed_out = _api([TimeoutError()])
    with pytest.raises(JackeryApiError, match="GET /slow request failed: TimeoutError"):
        await timed_out._get_json("/slow")

    assert timed_out.diagnostics_snapshot() == {
        "requests_total": 1,
        "requests_failed": 1,
        "timeouts_total": 1,
        "auth_retries": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ["method_name", "http_method"],
    [
        ["_put_json", "PUT"],
        ["_post_json", "POST"],
        ["_delete_json", "DELETE"],
    ],
)
async def test_json_write_helpers_reject_invalid_success_json(
    method_name: str,
    http_method: str,
) -> None:
    """Every JSON writer rejects an HTTP-200 body that is not valid JSON."""
    client = _api([
        _Response(
            HTTPStatus.OK,
            json_error=ValueError("bad json"),
            raw_text="broken",
        )
    ])
    writer = cast(
        "Callable[[str, dict[str, Any]], Any]",
        getattr(client, method_name),
    )

    with pytest.raises(JackeryApiError, match=r"invalid JSON \(redacted\)"):
        await writer("/write", {"value": 1})


@pytest.mark.asyncio
async def test_non_json_error_response_is_classified_without_decoder_leak() -> None:
    """A malformed non-200 response is converted into a normal HTTP API error."""
    client = _api([
        _Response(
            HTTPStatus.BAD_GATEWAY,
            json_error=ValueError("bad json"),
            raw_text="upstream unavailable",
        )
    ])

    with pytest.raises(JackeryApiError, match="GET /upstream HTTP 502"):
        await client._get_json("/upstream")


def test_mqtt_session_cache_and_credentials_cover_invalid_and_valid_seeds() -> None:
    """Cache hydration remains side-effect-free across incomplete and valid seeds."""
    client = _api()
    assert client.mqtt_session_snapshot() is None
    assert client.get_cached_mqtt_credentials() is None

    client.hydrate_mqtt_session(
        user_id="user-1",
        seed_b64="not-base64",
        mac_id="2" + ("a" * 32),
        mac_id_source="cache",
    )
    assert client.get_cached_mqtt_credentials() is None
    assert client.mqtt_session_snapshot() is not None

    short_seed = api_module.base64.b64encode(b"short").decode()
    client.hydrate_mqtt_session(
        user_id="user-1",
        seed_b64=short_seed,
        mac_id="2" + ("a" * 32),
    )
    assert client.get_cached_mqtt_credentials() is None

    seed_b64 = api_module.base64.b64encode(bytes(range(32))).decode()
    client.hydrate_mqtt_session(
        user_id="user-1",
        seed_b64=seed_b64,
        mac_id="2" + ("a" * 32),
    )
    snapshot = client.mqtt_session_snapshot()
    credentials = client.get_cached_mqtt_credentials()

    assert snapshot is not None
    assert snapshot["user_id"] == "user-1"
    assert snapshot["mac_id_source"] == "cache"
    assert credentials is not None
    assert credentials[const.MQTT_CREDENTIAL_CLIENT_ID] == "user-1@APP"
    assert credentials[const.MQTT_CREDENTIAL_USERNAME] == f"user-1@{"2" + ("a" * 32)}"
    assert credentials[const.MQTT_CREDENTIAL_PASSWORD]

    client.invalidate_mqtt_session_for_http_refresh()
    assert client.mqtt_session_snapshot() is None
    assert client.get_cached_mqtt_credentials() is None


@pytest.mark.asyncio
async def test_read_endpoint_wrappers_normalize_shapes_and_request_fields() -> None:
    """Thin GET wrappers preserve app field names and normalize response shapes."""
    client = _api()
    get_json = AsyncMock(
        side_effect=[
            {const.FIELD_DATA: {"currency": "EUR"}},
            {const.FIELD_DATA: [{"currency": "EUR"}, "bad"]},
            {const.FIELD_DATA: [{"id": 1}, None]},
            {const.FIELD_DATA: {"latest": "2.4.1"}},
            {const.FIELD_DATA: {"alarm": "detail"}},
            {const.FIELD_DATA: {"offline": 3}},
        ]
    )

    with patch.object(client, "_get_json", get_json):
        currency = await client.async_get_device_currency(42)
        currencies = await client.async_get_currency_list()
        notifications = await client.async_get_notify_list(
            current_time=123,
            device_sn="SN-1",
            page_no=2,
            page_size=5,
        )
        version = await client.async_check_app_version(
            type="android",
            version_name="2.4.0",
        )
        alarm = await client.async_get_alarm_detail(alarm_key="A-1")
        offline = await client.async_get_offline_statistics()

    assert currency == {"currency": "EUR"}
    assert currencies == [{"currency": "EUR"}]
    assert notifications == [{"id": 1}]
    assert version == {"latest": "2.4.1"}
    assert alarm == {"alarm": "detail"}
    assert offline == {"offline": 3}
    assert get_json.await_args_list == [
        call(
            const.DEVICE_CURRENCY_PATH,
            params={const.FIELD_DEVICE_ID: "42"},
        ),
        call(const.CURRENCY_LIST_PATH),
        call(
            const.NOTIFY_LIST_PATH,
            params={
                "currentTime": 123,
                "deviceSn": "SN-1",
                "pageNo": 2,
                "pageSize": 5,
            },
        ),
        call(
            const.APP_VERSION_PATH,
            params={"type": "android", "versionName": "2.4.0"},
        ),
        call(const.ALARM_DETAIL_PATH, params={"alarmKey": "A-1"}),
        call(const.OFFLINE_STAT_PATH),
    ]


@pytest.mark.asyncio
async def test_write_wrappers_validate_and_preserve_app_payloads() -> None:
    """Public writers reject invalid values and serialize IDs at their boundary."""
    client = _api()
    put_json = AsyncMock(return_value={const.FIELD_DATA: True})
    post_form = AsyncMock(return_value={const.FIELD_DATA: True})
    post_json = AsyncMock(return_value={const.FIELD_DATA: {"ok": True}})

    with (
        patch.object(client, "_put_json", put_json),
        patch.object(client, "_post_form", post_form),
        patch.object(client, "_post_json", post_json),
    ):
        assert await client.async_set_system_name(7, "  Home  ") is True
        assert await client.async_set_max_power(8, 2500) is True
        privacy = await client.async_agree_privacy_consent(
            pending_agree_version_ids=(1, 2),
        )

        with pytest.raises(JackeryApiError, match="system_name"):
            await client.async_set_system_name(7, "  ")
        with pytest.raises(JackeryApiError, match="max_power"):
            await client.async_set_max_power(8, -1)

    assert privacy == {const.FIELD_DATA: {"ok": True}}
    put_json.assert_awaited_once_with(
        const.SYSTEM_NAME_PATH,
        {const.FIELD_SYSTEM_NAME: "Home", const.FIELD_ID: "7"},
    )
    post_form.assert_awaited_once_with(
        const.MAX_POWER_SAVE_PATH,
        {const.FIELD_MAX_POWER: 2500, const.FIELD_DEVICE_ID: "8"},
    )
    post_json.assert_awaited_once_with(
        const.PRIVACY_CONSENT_PATH,
        {"pendingAgreeVersionIds": [1, 2]},
    )
