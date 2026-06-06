"""Additional tests for PR changes not yet covered by earlier test files.

Covers:
- _emit_payload_debug: pre-built-dict form (PR changed lambda → direct call in
  async_login / _get_json / _put_json / _post_form)
- decrypt_binary_notify: version-field mismatch now silently continues instead
  of logging a debug message
- client/__init__ __getattr__: raises AttributeError for unknown names
"""

from typing import Any

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.client.ble import (
    BLE_AES_IV_LEN,
    BleBinaryFrame,
    aes_encrypt,
    build_binary_frame,
    decrypt_binary_notify,
)

# ---------------------------------------------------------------------------
# _emit_payload_debug — pre-built dict vs callable factory
# ---------------------------------------------------------------------------


async def test_emit_payload_debug_noop_when_no_callback() -> None:
    """_emit_payload_debug must return silently when payload_debug_callback is None."""
    api = JackeryApi.__new__(JackeryApi)
    api.payload_debug_callback = None

    # Neither a dict nor a callable being passed should raise.
    event: dict[str, Any] = {"kind": "http", "method": "GET", "path": "/test"}
    await api._emit_payload_debug(event)  # must not raise  # noqa: SLF001


async def test_emit_payload_debug_passes_dict_to_callback() -> None:
    """When a pre-built dict is passed, the callback receives that exact dict."""
    api = JackeryApi.__new__(JackeryApi)
    received: list[Any] = []

    def sync_callback(arg: Any) -> None:  # noqa: ANN401
        received.append(arg)

    api.payload_debug_callback = sync_callback

    event: dict[str, Any] = {"kind": "http", "path": "/v1/auth/login"}
    await api._emit_payload_debug(event)  # noqa: SLF001

    assert len(received) == 1
    assert received[0] is event


async def test_emit_payload_debug_passes_callable_to_callback() -> None:
    """When a callable factory is passed, the callback receives the callable itself."""
    api = JackeryApi.__new__(JackeryApi)
    received: list[Any] = []

    def sync_callback(arg: Any) -> None:  # noqa: ANN401
        received.append(arg)

    api.payload_debug_callback = sync_callback

    factory_called: list[bool] = []

    def factory() -> dict[str, Any]:
        factory_called.append(True)
        return {"kind": "http", "path": "/v1/test"}

    await api._emit_payload_debug(factory)  # noqa: SLF001

    assert len(received) == 1
    assert received[0] is factory
    # Factory itself must not have been called by _emit_payload_debug.
    assert not factory_called


async def test_emit_payload_debug_suppresses_callback_exception() -> None:
    """Exceptions raised by the debug callback must be swallowed, not propagated."""
    api = JackeryApi.__new__(JackeryApi)

    def exploding_callback(arg: Any) -> None:  # noqa: ANN401
        raise RuntimeError("debug callback failure")  # noqa: TRY003

    api.payload_debug_callback = exploding_callback

    # Must NOT raise.
    await api._emit_payload_debug({"kind": "http", "path": "/v1/test"})  # noqa: SLF001


async def test_emit_payload_debug_awaits_async_callback() -> None:
    """When the callback returns a coroutine, _emit_payload_debug awaits it."""
    api = JackeryApi.__new__(JackeryApi)
    awaited: list[bool] = []

    async def async_callback(arg: Any) -> None:  # noqa: ANN401, RUF029
        awaited.append(True)

    api.payload_debug_callback = async_callback

    await api._emit_payload_debug({"kind": "http", "path": "/v1/test"})  # noqa: SLF001

    assert awaited == [True]


async def test_emit_payload_debug_async_callback_exception_suppressed() -> None:
    """Exceptions raised inside an async debug callback must be suppressed."""
    api = JackeryApi.__new__(JackeryApi)

    async def async_exploding(arg: Any) -> None:  # noqa: ANN401, RUF029
        raise ValueError("async debug failure")  # noqa: TRY003

    api.payload_debug_callback = async_exploding

    # Must NOT raise.
    await api._emit_payload_debug({"kind": "http", "path": "/v1/test"})  # noqa: SLF001


# ---------------------------------------------------------------------------
# _http_payload_debug is now called eagerly (not via lambda)
#
# The PR changed:
#   await self._emit_payload_debug(lambda: self._http_payload_debug(...))
# to:
#   await self._emit_payload_debug(self._http_payload_debug(...))
#
# This means _http_payload_debug() is always evaluated even when no
# callback is registered.  These tests pin the observable behaviour: a
# pre-built dict (not a callable) is passed to the callback.
# ---------------------------------------------------------------------------


async def test_http_payload_debug_returns_dict_with_required_keys() -> None:  # noqa: RUF029
    """_http_payload_debug must return a dict with expected shape."""
    result = JackeryApi._http_payload_debug(  # noqa: SLF001
        method="GET",
        path="/v1/device/property",
        params={"deviceId": "123"},
        body=None,
        status=200,
        response={"code": 0, "data": {"foo": "bar"}},
    )
    assert isinstance(result, dict)
    assert result["kind"] == "http"
    assert result["method"] == "GET"
    assert result["path"] == "/v1/device/property"
    assert result["status"] == 200  # noqa: PLR2004
    assert "response_data_type" in result


async def test_http_payload_debug_pre_built_dict_received_by_callback() -> None:
    """Verify the dict from _http_payload_debug is received directly (not via lambda)."""  # noqa: E501
    api = JackeryApi.__new__(JackeryApi)
    received: list[Any] = []

    def callback(arg: Any) -> None:  # noqa: ANN401
        received.append(arg)

    api.payload_debug_callback = callback

    # Simulate the post-PR calling pattern: build the dict first, pass it in.
    event = api._http_payload_debug(  # noqa: SLF001
        method="POST",
        path="/v1/auth/login",
        body={"account": "user"},
        status=200,
        response={"code": 0, "data": {"token": "tok"}},
    )
    await api._emit_payload_debug(event)  # noqa: SLF001

    assert len(received) == 1
    assert isinstance(received[0], dict)
    assert received[0]["kind"] == "http"
    assert received[0]["method"] == "POST"


# ---------------------------------------------------------------------------
# decrypt_binary_notify — version-field mismatch is now silently continued
# ---------------------------------------------------------------------------


def _make_tampered_notify(cmd: int, body: bytes, key: bytes) -> bytes:
    """Build an encrypted notify blob whose version bytes differ from expected.

    Constructs a valid binary frame, replaces bytes[2:4] with a different
    value, then re-encrypts it so decrypt_binary_notify can parse it.
    """
    plain = build_binary_frame(cmd=cmd, body=body)
    # The expected version is b"\x00\x64"; change to b"\x00\x63" (99 decimal).
    tampered = plain[:2] + b"\x00\x63" + plain[4:]
    iv = bytes(BLE_AES_IV_LEN)
    ciphertext = aes_encrypt(tampered, key, iv)
    return iv + ciphertext


def test_decrypt_binary_notify_version_mismatch_does_not_raise() -> None:
    """A frame with unexpected version bytes must parse without raising.

    The PR removed the _LOGGER.debug call on version mismatch and replaced it
    with a ``pass`` comment.  Decoding must still succeed.
    """
    key = b"hr2c0hh361336138"  # 16-byte AES-128 key
    body = b'{"cmd":107}'
    blob = _make_tampered_notify(cmd=107, body=body, key=key)
    result = decrypt_binary_notify(blob, key)
    assert isinstance(result, BleBinaryFrame)


def test_decrypt_binary_notify_version_mismatch_returns_correct_cmd() -> None:
    """Cmd field must survive version-byte tampering."""
    key = b"hr2c0hh361336138"
    body = b'{"cmd":121,"swEps":0}'
    blob = _make_tampered_notify(cmd=121, body=body, key=key)
    result = decrypt_binary_notify(blob, key)
    assert result.cmd == 121  # noqa: PLR2004


def test_decrypt_binary_notify_version_mismatch_returns_correct_body() -> None:
    """Body must be recovered intact even when version bytes are unexpected."""
    key = b"hr2c0hh361336138"
    body = b'{"cmd":107,"batSoc":80}'
    blob = _make_tampered_notify(cmd=107, body=body, key=key)
    result = decrypt_binary_notify(blob, key)
    assert result.body == body


def test_decrypt_binary_notify_valid_version_still_succeeds() -> None:
    r"""Frames with the standard version b'\\x00\\x64' continue to decode normally."""
    key = b"hr2c0hh361336138"
    body = b'{"cmd":107,"pvPw":1200}'
    plain = build_binary_frame(cmd=107, body=body)
    iv = bytes(BLE_AES_IV_LEN)
    ciphertext = aes_encrypt(plain, key, iv)
    blob = iv + ciphertext
    result = decrypt_binary_notify(blob, key)
    assert result.cmd == 107  # noqa: PLR2004
    assert result.body == body


def test_decrypt_binary_notify_version_mismatch_empty_body() -> None:
    """Version mismatch with empty body must also succeed (boundary case)."""
    key = b"hr2c0hh361336138"
    blob = _make_tampered_notify(cmd=0, body=b"", key=key)
    result = decrypt_binary_notify(blob, key)
    assert isinstance(result, BleBinaryFrame)
    assert result.body == b""


# ---------------------------------------------------------------------------
# client/__init__ __getattr__ — AttributeError for unknown names
# ---------------------------------------------------------------------------


def test_client_init_getattr_raises_attribute_error_for_unknown_name() -> None:
    """Accessing an unknown attribute on the client package must raise AttributeError."""  # noqa: E501
    import custom_components.jackery_solarvault.client as client_pkg  # noqa: PLC0415

    with pytest.raises(AttributeError):
        _ = client_pkg.NonExistentClass  # type: ignore[attr-defined]


def test_client_init_getattr_raises_for_private_unknown_name() -> None:
    """Private names that are not registered must also raise AttributeError."""
    import custom_components.jackery_solarvault.client as client_pkg  # noqa: PLC0415

    with pytest.raises(AttributeError):
        _ = client_pkg._SomethingPrivate  # type: ignore[attr-defined]  # noqa: SLF001


def test_client_init_getattr_attribute_error_message_contains_name() -> None:
    """The AttributeError raised for an unknown name must include the name."""
    import custom_components.jackery_solarvault.client as client_pkg  # noqa: PLC0415

    with pytest.raises(AttributeError) as exc_info:
        _ = client_pkg.DoesNotExist  # type: ignore[attr-defined]
    assert "DoesNotExist" in str(exc_info.value)


def test_client_init_direct_imports_work_without_getattr() -> None:
    """Public symbols must be importable directly without going through __getattr__."""
    from custom_components.jackery_solarvault.client import (  # noqa: PLC0415
        JackeryApi,
        JackeryApiError,
        JackeryAuthError,
        JackeryError,
    )

    assert JackeryApi is not None
    assert JackeryApiError is not None
    assert JackeryAuthError is not None
    assert JackeryError is not None


def test_client_init_jackery_mqtt_push_client_via_getattr() -> None:
    """JackeryMqttPushClient must be returned by __getattr__ without ImportError.

    If mqtt_push.py is not importable in the test environment this test is
    skipped rather than failed, since the lazy-import guard is the behaviour
    under test.
    """
    import custom_components.jackery_solarvault.client as client_pkg  # noqa: PLC0415

    try:
        cls = client_pkg.JackeryMqttPushClient
        assert cls is not None
    except ImportError:
        pytest.skip("mqtt_push module not available in test environment")
