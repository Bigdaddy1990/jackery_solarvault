"""App-2.4.0 regression tests for the HTTP login crypto envelope."""

import base64
from typing import TYPE_CHECKING, Any

from custom_components.jackery_solarvault.client import api as api_module
from custom_components.jackery_solarvault.client.api import (
    build_login_crypto_fields,
    generate_login_aes_key,
)
from custom_components.jackery_solarvault.const import (
    LOGIN_AES_KEY_LEN,
    LOGIN_AES_SEED_LEN,
)

if TYPE_CHECKING:
    import pytest


def test_login_aes_key_matches_app_random_base64_contract() -> None:
    """App 2.4.0 generates 16 random bytes and uses their Base64 ASCII bytes."""
    requested_sizes: list[int] = []

    def _random_source(size: int) -> bytes:
        requested_sizes.append(size)
        return bytes(range(size))

    key = generate_login_aes_key(_random_source)

    assert requested_sizes == [LOGIN_AES_SEED_LEN]
    assert key == base64.b64encode(bytes(range(LOGIN_AES_SEED_LEN)))
    assert len(key) == LOGIN_AES_KEY_LEN


def test_login_crypto_uses_same_fresh_key_for_aes_and_rsa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-login key encrypts the bean and is RSA-wrapped; it is not cached."""
    captured: dict[str, Any] = {}

    def _fake_aes(plaintext: bytes, key: bytes) -> bytes:
        captured["plaintext"] = plaintext
        captured["aes_key"] = key
        return b"aes-ciphertext"

    def _fake_rsa(data: bytes, public_key_b64: str | None = None) -> bytes:
        captured["rsa_key"] = data
        captured["public_key"] = public_key_b64
        return b"rsa-ciphertext"

    monkeypatch.setattr(api_module, "_aes_ecb_encrypt", _fake_aes)
    monkeypatch.setattr(api_module, "_rsa_pkcs1v15_encrypt", _fake_rsa)
    expected_key = base64.b64encode(b"\xa5" * LOGIN_AES_SEED_LEN)

    fields = build_login_crypto_fields(
        {"account": "test@example.com"},
        random_source=lambda size: b"\xa5" * size,
    )

    assert captured["aes_key"] == expected_key
    assert captured["rsa_key"] == expected_key
    # Production code calls _rsa_pkcs1v15_encrypt without public_key_b64,
    # so the mock receives None. The bundled key is used internally.
    assert captured["public_key"] is None
    assert fields == {
        "aesEncryptData": base64.b64encode(b"aes-ciphertext").decode("ascii"),
        "rsaForAesKey": base64.b64encode(b"rsa-ciphertext").decode("ascii"),
    }
