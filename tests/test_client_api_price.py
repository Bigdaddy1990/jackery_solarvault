"""Tests for Jackery cloud price configuration calls."""

import asyncio
from typing import Any, cast

import aiohttp
import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi, JackeryApiError
from custom_components.jackery_solarvault.const import (
    FIELD_CURRENCY,
    FIELD_SINGLE_PRICE,
    FIELD_SYSTEM_ID,
    SAVE_SINGLE_MODE_PATH,
)


def _api() -> JackeryApi:
    return JackeryApi(cast(aiohttp.ClientSession, object()), "account", "password")


@pytest.mark.asyncio
async def test_set_single_mode_formats_numeric_price() -> None:
    """The fixed-price endpoint receives stable decimal text."""
    calls: list[tuple[str, dict[str, Any]]] = []
    api = _api()

    async def fake_post_form(path: str, fields: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        calls.append((path, fields))
        return {"data": True}

    api._post_form = fake_post_form  # type: ignore[method-assign]

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
        )
    ]


@pytest.mark.asyncio
async def test_set_single_mode_rejects_negative_price() -> None:
    """Negative fixed prices are rejected before the API call."""
    api = _api()

    async def fake_post_form(path: str, fields: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        pytest.fail("negative prices must not be sent")

    api._post_form = fake_post_form  # type: ignore[method-assign]

    with pytest.raises(JackeryApiError, match="single_price must be >= 0"):
        await api.async_set_single_mode(
            system_id=123,
            single_price=-0.01,
            currency="EUR",
        )


@pytest.mark.asyncio
async def test_set_single_mode_rejects_non_numeric_price() -> None:
    """Invalid price text raises JackeryApiError, not a raw ValueError."""
    api = _api()

    async def fake_post_form(path: str, fields: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        pytest.fail("invalid prices must not be sent")

    api._post_form = fake_post_form  # type: ignore[method-assign]

    with pytest.raises(JackeryApiError, match="single_price must be a valid number"):
        await api.async_set_single_mode(
            system_id=123,
            single_price="not-a-number",
            currency="EUR",
        )


@pytest.mark.asyncio
async def test_set_single_mode_rejects_empty_currency() -> None:
    """Currency must be present before the API call."""
    api = _api()

    async def fake_post_form(path: str, fields: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        pytest.fail("empty currency must not be sent")

    api._post_form = fake_post_form  # type: ignore[method-assign]

    with pytest.raises(JackeryApiError, match="currency must be a non-empty string"):
        await api.async_set_single_mode(
            system_id=123,
            single_price=0.28,
            currency=" ",
        )
