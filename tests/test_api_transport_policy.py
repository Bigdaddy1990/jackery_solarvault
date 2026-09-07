"""HTTP transport policy regression tests."""

import asyncio
from json import JSONDecodeError
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.client.api import (
    HttpProfile,
    JackeryApi,
    JackeryApiError,
)
from custom_components.jackery_solarvault.const import REDACTED_VALUE


class _Chunks:
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _size: int):  # ruff: ignore[missing-return-type-private-function]
        for chunk in self._chunks:
            await asyncio.sleep(0)
            yield chunk


def _response(content_type: str, *, json_value: object = None):  # ruff: ignore[missing-return-type-private-function]
    return SimpleNamespace(
        headers={"content-type": content_type},
        content=SimpleNamespace(),
        content_length=None,
        json=AsyncMock(return_value=json_value),
    )


async def test_json_decoder_rejects_wrong_content_type() -> None:
    """Reject JSON decoding when the response advertises a non-JSON type."""
    with pytest.raises(JackeryApiError, match="Content-Type"):
        await JackeryApi._decode_json_response(_response("text/html"))  # ruff: ignore[private-member-access]


async def test_limited_reader_rejects_declared_and_streamed_oversize() -> None:
    """Enforce the response-size limit for declared and streamed bodies."""
    declared = SimpleNamespace(content_length=11, content=_Chunks(b"ok"))
    with pytest.raises(JackeryApiError, match="exceeds"):
        await JackeryApi._read_limited_bytes(declared, limit=10)  # ruff: ignore[private-member-access]

    streamed = SimpleNamespace(
        content_length=None, content=_Chunks(b"12345", b"678901")
    )
    with pytest.raises(JackeryApiError, match="exceeds"):
        await JackeryApi._read_limited_bytes(streamed, limit=10)  # ruff: ignore[private-member-access]


async def test_json_decoder_redacts_non_json_failure() -> None:
    """Keep malformed JSON details out of the surfaced API error."""
    response = _response("application/json")
    response.json.side_effect = JSONDecodeError("secret-token", "secret-token", 0)
    with pytest.raises(JackeryApiError, match="redacted") as raised:
        await JackeryApi._decode_json_response(response)  # ruff: ignore[private-member-access]
    assert "secret-token" not in str(raised.value)


def test_http_diagnostic_redacts_credentials_and_bounds_values() -> None:
    """Redact credentials and bound oversized HTTP diagnostic values."""
    event = JackeryApi._http_payload_debug(  # ruff: ignore[private-member-access]
        request=("POST", "/test"),
        body={"account": "owner@example.test", "nested": {"apiToken": "secret"}},
        response={"data": {"mqttPassWord": "credential", "value": "x" * 20_000}},
    )
    assert event["request_body"]["account"] == REDACTED_VALUE
    assert event["request_body"]["nested"]["apiToken"] == REDACTED_VALUE
    assert event["response"]["data"]["mqttPassWord"] == REDACTED_VALUE
    assert len(event["response"]["data"]["value"]) < 5_000  # ruff: ignore[magic-value-comparison]


def test_fast_profile_is_policy_only() -> None:
    """Keep FASTHTTP as a bounded policy on the shared HTTP transport."""
    assert (
        JackeryApi._policy(HttpProfile.FAST).max_payload_bytes  # ruff: ignore[private-member-access]
        < JackeryApi._policy().max_payload_bytes  # ruff: ignore[private-member-access]
    )
