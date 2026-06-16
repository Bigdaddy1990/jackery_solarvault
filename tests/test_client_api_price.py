"""Tests for Jackery cloud price configuration calls."""

import asyncio
from typing import TYPE_CHECKING, Any, Self, cast

import pytest

from custom_components.jackery_solarvault.client.api import (
    JackeryApi,
    JackeryApiError,
    JackeryAuthError,
)
from custom_components.jackery_solarvault.const import (
    CODE_OK,
    FIELD_CODE,
    FIELD_CURRENCY,
    FIELD_DATA,
    FIELD_MSG,
    FIELD_SINGLE_PRICE,
    FIELD_SYSTEM_ID,
    FIELD_TOKEN,
    SAVE_SINGLE_MODE_PATH,
)

if TYPE_CHECKING:
    import aiohttp


def _api() -> JackeryApi:
    return JackeryApi(cast("aiohttp.ClientSession", object()), "account", "password")


@pytest.mark.asyncio()
async def test_set_single_mode_formats_numeric_price() -> None:
    """The fixed-price endpoint receives stable decimal text."""
    calls: list[tuple[str, dict[str, Any]]] = []
    api = _api()

    async def fake_post_form(path: str, fields: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        calls.append((path, fields))
        return {"data": True}

    api._post_form = fake_post_form  # type: ignore[method-assign]  # noqa: SLF001

    assert await api.async_set_single_mode(
        system_id=123,
        single_price="0.28000",
        currency=" EUR ",
    )

    assert calls == [
        (
            SAVE_SINGLE_MODE_PATH,
            {
                FIELD_SYSTEM_ID: "123",
                FIELD_SINGLE_PRICE: "0.28",
                FIELD_CURRENCY: "EUR",
            },
        ),
    ]


@pytest.mark.asyncio()
async def test_set_single_mode_rejects_negative_price() -> None:
    """Negative fixed prices are rejected before the API call."""
    api = _api()

    async def fake_post_form(path: str, fields: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        pytest.fail("negative prices must not be sent")

    api._post_form = fake_post_form  # type: ignore[method-assign]  # noqa: SLF001

    with pytest.raises(JackeryApiError, match="single_price must be >= 0"):
        await api.async_set_single_mode(
            system_id=123,
            single_price=-0.01,
            currency="EUR",
        )


@pytest.mark.asyncio()
async def test_set_single_mode_rejects_non_numeric_price() -> None:
    """Invalid price text raises JackeryApiError, not a raw ValueError."""
    api = _api()

    async def fake_post_form(path: str, fields: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        pytest.fail("invalid prices must not be sent")

    api._post_form = fake_post_form  # type: ignore[method-assign]  # noqa: SLF001

    with pytest.raises(JackeryApiError, match="single_price must be a valid number"):
        await api.async_set_single_mode(
            system_id=123,
            single_price="not-a-number",
            currency="EUR",
        )


@pytest.mark.asyncio()
async def test_set_single_mode_rejects_empty_currency() -> None:
    """Currency must be present before the API call."""
    api = _api()

    async def fake_post_form(path: str, fields: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        pytest.fail("empty currency must not be sent")

    api._post_form = fake_post_form  # type: ignore[method-assign]  # noqa: SLF001

    with pytest.raises(JackeryApiError, match="currency must be a non-empty string"):
        await api.async_set_single_mode(
            system_id=123,
            single_price=0.28,
            currency=" ",
        )


@pytest.mark.asyncio()
async def test_login_rejects_non_object_data_payload() -> None:
    """Login must reject backend data payloads that are not JSON objects."""

    class _Response:
        status = 200

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> dict[str, object]:  # noqa: PLR6301
            return {
                FIELD_CODE: CODE_OK,
                FIELD_TOKEN: "token",
                FIELD_DATA: ["not", "an", "object"],
            }

    class _Session:
        def post(self, *args: object, **kwargs: object) -> _Response:  # noqa: PLR6301
            return _Response()

    api = JackeryApi(cast("aiohttp.ClientSession", _Session()), "account", "password")

    with pytest.raises(JackeryApiError, match="Login returned data list"):
        await api.async_login()


@pytest.mark.asyncio()
async def test_login_rejection_does_not_update_last_success_response() -> None:
    """Rejected login payloads must not be stored as last successful login."""

    class _Response:
        status = 200

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> dict[str, object]:  # noqa: PLR6301
            return {FIELD_CODE: 401, FIELD_MSG: "invalid token"}

    class _Session:
        def post(self, *args: object, **kwargs: object) -> _Response:  # noqa: PLR6301
            return _Response()

    api = JackeryApi(cast("aiohttp.ClientSession", _Session()), "account", "password")

    with pytest.raises(JackeryAuthError):
        await api.async_login()

    assert api.last_login_response is None


@pytest.mark.asyncio()
async def test_get_json_rejects_unparseable_success_body() -> None:
    """A 200 response with non-JSON text must not be stored as successful data."""

    class _Response:
        status = 200

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def json(self, *, content_type: object = None) -> dict[str, object]:  # noqa: PLR6301
            msg = "not json"
            raise ValueError(msg)

        async def text(self) -> str:  # noqa: PLR6301
            return "<html>maintenance</html>"

    class _Session:
        def get(self, *args: object, **kwargs: object) -> _Response:  # noqa: PLR6301
            return _Response()

    api = JackeryApi(cast("aiohttp.ClientSession", _Session()), "account", "password")
    api._token = "token"  # noqa: SLF001

    with pytest.raises(JackeryApiError, match="returned invalid JSON"):
        await api.async_get_device_property("123")

    assert api.last_property_responses == {}
