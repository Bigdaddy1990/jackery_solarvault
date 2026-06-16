"""Tests for the encrypt_mqtt_body function added to client/api.py for this integration.

Covers:
- Valid 16-byte key: returns a base64 string
- Key length validation: ValueError for keys != 16 bytes
- Round-trip: output decrypts back to original JSON
- Unicode body values are handled correctly
- Determinism: same inputs produce the same output
- Empty-body edge case
"""

import base64
import json

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
import pytest

from custom_components.jackery_solarvault.client.api import encrypt_mqtt_body

# ---------------------------------------------------------------------------
# Basic contract
# ---------------------------------------------------------------------------


def test_encrypt_mqtt_body_returns_string() -> None:
    """encrypt_mqtt_body must return a str, not bytes."""
    key = b"0123456789abcdef"  # 16 bytes
    result = encrypt_mqtt_body({"cmd": 1}, key)
    assert isinstance(result, str)  # noqa: S101


def test_encrypt_mqtt_body_returns_valid_base64() -> None:
    """Output must be valid standard base64."""
    key = b"0123456789abcdef"
    result = encrypt_mqtt_body({"cmd": 1}, key)
    # Should not raise
    decoded = base64.b64decode(result)
    assert len(decoded) > 0  # noqa: S101


def test_encrypt_mqtt_body_output_is_ascii() -> None:
    """Base64 output must contain only ASCII characters."""
    key = b"abcdefghijklmnop"  # 16 bytes
    result = encrypt_mqtt_body({"key": "value"}, key)
    result.encode("ascii")  # raises if non-ASCII


# ---------------------------------------------------------------------------
# Key length validation
# ---------------------------------------------------------------------------


def test_encrypt_mqtt_body_rejects_short_key() -> None:
    """A key shorter than 16 bytes must raise ValueError."""
    with pytest.raises(ValueError, match="16 bytes"):
        encrypt_mqtt_body({"cmd": 1}, b"tooshort")


def test_encrypt_mqtt_body_rejects_long_key() -> None:
    """A key longer than 16 bytes must raise ValueError."""
    with pytest.raises(ValueError, match="16 bytes"):
        encrypt_mqtt_body({"cmd": 1}, b"this-key-is-too-long-for-aes128!")


def test_encrypt_mqtt_body_rejects_empty_key() -> None:
    """An empty key must raise ValueError."""
    with pytest.raises(ValueError, match="16 bytes"):
        encrypt_mqtt_body({"cmd": 1}, b"")


def test_encrypt_mqtt_body_rejects_32_byte_key() -> None:
    """A 32-byte (AES-256) key must raise ValueError since AES-128 is required."""
    with pytest.raises(ValueError, match="16 bytes"):
        encrypt_mqtt_body({"cmd": 1}, b"0123456789abcdef0123456789abcdef")


def test_encrypt_mqtt_body_error_message_includes_actual_length() -> None:
    """ValueError message must state the actual key length received."""
    bad_key = b"too_short"  # 9 bytes
    with pytest.raises(ValueError) as exc_info:  # noqa: PT011
        encrypt_mqtt_body({"cmd": 1}, bad_key)
    assert str(len(bad_key)) in str(exc_info.value)  # noqa: S101


# ---------------------------------------------------------------------------
# Decryptability (round-trip)
# ---------------------------------------------------------------------------


def test_encrypt_mqtt_body_round_trip() -> None:
    """Decrypting the ciphertext must recover the original JSON body."""
    key = b"hr2c0hh361336138"  # 16-byte key from PROTOCOL.md §14 example
    body = {"cmd": 101, "sn": "SV12345", "action": "query"}

    ciphertext_b64 = encrypt_mqtt_body(body, key)
    ciphertext = base64.b64decode(ciphertext_b64)

    # Decrypt AES-128-CBC with IV = key
    cipher = Cipher(algorithms.AES(key), modes.CBC(key))
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()

    # Remove PKCS7 padding
    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()

    recovered_body = json.loads(plaintext.decode("utf-8"))
    assert recovered_body == body  # noqa: S101


# ---------------------------------------------------------------------------
# JSON serialization details
# ---------------------------------------------------------------------------


def test_encrypt_mqtt_body_uses_compact_json_separators() -> None:
    """The JSON must use compact separators (',', ':') to match app wire format."""
    key = b"0123456789abcdef"
    body = {"a": 1, "b": 2}

    ciphertext_b64 = encrypt_mqtt_body(body, key)
    ciphertext = base64.b64decode(ciphertext_b64)

    cipher = Cipher(algorithms.AES(key), modes.CBC(key))
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()

    # Compact JSON: no spaces around : or ,
    decoded_str = plaintext.decode("utf-8")
    assert " " not in decoded_str  # noqa: S101


def test_encrypt_mqtt_body_handles_unicode_values() -> None:
    """Unicode characters in body values must survive the round-trip."""
    key = b"0123456789abcdef"
    body = {"name": "日本語テスト", "emoji": "⚡"}

    ciphertext_b64 = encrypt_mqtt_body(body, key)
    ciphertext = base64.b64decode(ciphertext_b64)

    cipher = Cipher(algorithms.AES(key), modes.CBC(key))
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()

    recovered = json.loads(plaintext.decode("utf-8"))
    assert recovered == body  # noqa: S101


def test_encrypt_mqtt_body_empty_body_dict() -> None:
    """An empty dict body must encrypt without error and be decryptable."""
    key = b"0123456789abcdef"
    body: dict[str, object] = {}

    ciphertext_b64 = encrypt_mqtt_body(body, key)
    ciphertext = base64.b64decode(ciphertext_b64)

    cipher = Cipher(algorithms.AES(key), modes.CBC(key))
    decryptor = cipher.decryptor()
    padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    plaintext = unpadder.update(padded_plaintext) + unpadder.finalize()

    recovered = json.loads(plaintext.decode("utf-8"))
    assert recovered == body  # noqa: S101


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_encrypt_mqtt_body_is_deterministic() -> None:
    """Same key and body must always produce the same ciphertext (IV = key)."""
    key = b"0123456789abcdef"
    body = {"cmd": 42, "device": "SV999"}

    result1 = encrypt_mqtt_body(body, key)
    result2 = encrypt_mqtt_body(body, key)
    assert result1 == result2  # noqa: S101


def test_encrypt_mqtt_body_different_bodies_produce_different_ciphertext() -> None:
    """Different body dicts must yield different ciphertext."""
    key = b"0123456789abcdef"
    result1 = encrypt_mqtt_body({"cmd": 1}, key)
    result2 = encrypt_mqtt_body({"cmd": 2}, key)
    assert result1 != result2  # noqa: S101


def test_encrypt_mqtt_body_different_keys_produce_different_ciphertext() -> None:
    """Different keys must yield different ciphertext for the same body."""
    body = {"cmd": 1}
    result1 = encrypt_mqtt_body(body, b"0123456789abcdef")
    result2 = encrypt_mqtt_body(body, b"fedcba9876543210")
    assert result1 != result2  # noqa: S101
