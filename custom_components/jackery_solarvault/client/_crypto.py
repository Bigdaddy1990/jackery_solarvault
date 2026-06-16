"""Cryptographic primitives for the Jackery SolarVault protocol.

AES-ECB/RSA-1024 hybrid login encryption (Layer A), AES-128-CBC/PKCS7
MQTT body encryption (Layer C), and deterministic UDID generation.
"""

import base64
from collections.abc import Callable
import hashlib
import json
import os
from typing import Any, Final
import uuid

from cryptography.hazmat.primitives.asymmetric import padding as asym_padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.serialization import load_der_public_key

from custom_components.jackery_solarvault.const import (
    MQTT_MAC_ID_PREFIX,
    RSA_PUBLIC_KEY_B64,
)

LOGIN_AES_SEED_LEN: Final = 16
LOGIN_AES_KEY_LEN: Final = 24
RandomBytesSource = Callable[[int], bytes]


def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt plaintext using AES-128 in ECB mode with PKCS7 padding.

    Parameters:
        plaintext (bytes): Data to be encrypted.
        key (bytes): AES key; must be 16 bytes for AES-128.

    Returns:
        bytes: Ciphertext resulting from AES-128-ECB encryption of the padded plaintext.
    """
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    # Jackery Layer A protocol requires ECB.
    cipher = Cipher(algorithms.AES(key), modes.ECB())  # nosec B305
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _aes_cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    """Encrypts plaintext using AES in CBC mode with PKCS7 padding.

    Parameters:
        plaintext (bytes): Data to be encrypted.
        key (bytes): AES key (16, 24, or 32 bytes).
        iv (bytes): Initialization vector (16 bytes).

    Returns:
        bytes: Ciphertext produced by AES-CBC encryption of the padded plaintext.
    """
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def encrypt_mqtt_body(body: dict[str, Any], bluetooth_key: bytes) -> str:
    """Encrypt an MQTT command body with AES-128-CBC/PKCS7.

    The compact JSON body uses ``bluetooth_key`` as AES key and IV.

    Parameters:
        body (dict[str, Any]): Command body to serialize and encrypt.
        bluetooth_key (bytes): 16-byte Bluetooth key used as both AES key and IV.

    Returns:
        str: Base64-encoded ciphertext.

    Raises:
        ValueError: If `bluetooth_key` is not exactly 16 bytes.
    """
    if len(bluetooth_key) != 16:  # noqa: PLR2004
        msg = (
            "encrypt_mqtt_body: bluetoothKey must be "
            f"16 bytes, got {len(bluetooth_key)}"
        )
        raise ValueError(
            msg,
        )
    plaintext = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8",
    )  # noqa: E501, RUF100
    ciphertext = _aes_cbc_encrypt(plaintext, bluetooth_key, bluetooth_key)
    return base64.b64encode(ciphertext).decode("ascii")


def generate_login_aes_key(random_source: RandomBytesSource = os.urandom) -> bytes:
    """Return the app-compatible Layer A AES key bytes for one login request."""
    seed = random_source(LOGIN_AES_SEED_LEN)
    if len(seed) != LOGIN_AES_SEED_LEN:
        msg = f"Layer A login AES seed must be 16 bytes, got {len(seed)}"
        raise ValueError(
            msg,
        )
    return base64.b64encode(seed)


def build_login_crypto_fields(
    login_bean: dict[str, Any],
    *,
    aes_key: bytes | None = None,
    random_source: RandomBytesSource = os.urandom,
) -> dict[str, str]:
    """Build Jackery Layer A login form fields with an injectable AES key.

    Production calls omit ``aes_key`` so each login uses the app behavior from
    ``source-of-truth/jackery_auth.py``: 16 random bytes are Base64-encoded,
    then those ASCII bytes are used as the AES key and RSA-wrapped. Tests may
    inject ``aes_key`` or ``random_source`` for golden vectors.
    """
    login_aes_key = (
        aes_key if aes_key is not None else generate_login_aes_key(random_source)
    )
    if len(login_aes_key) != LOGIN_AES_KEY_LEN:
        msg = f"Layer A login AES key must be 24 bytes, got {len(login_aes_key)}"
        raise ValueError(
            msg,
        )
    plaintext = json.dumps(
        login_bean,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        "aesEncryptData": base64.b64encode(
            _aes_ecb_encrypt(plaintext, login_aes_key),
        ).decode("ascii"),
        "rsaForAesKey": base64.b64encode(
            _rsa_pkcs1v15_encrypt(login_aes_key, RSA_PUBLIC_KEY_B64),
        ).decode("ascii"),
    }


def _rsa_pkcs1v15_encrypt(data: bytes, public_key_b64: str) -> bytes:
    """Encrypt ``data`` with RSA PKCS#1 v1.5 and a DER public key.

    Parameters:
        data (bytes): Plaintext bytes to encrypt.
        public_key_b64 (str): Base64-encoded DER representation of an RSA public key.

    Returns:
        bytes: Ciphertext produced by RSA PKCS#1 v1.5 encryption of `data`.

    Raises:
        TypeError: If the decoded public key is not an RSA public key.
    """
    der_bytes = base64.b64decode(public_key_b64)
    public_key = load_der_public_key(der_bytes)
    if not isinstance(public_key, rsa.RSAPublicKey):
        msg = (
            f"Jackery login expects an RSA public key, got {type(public_key).__name__}"
        )
        raise TypeError(
            msg,
        )
    return public_key.encrypt(data, asym_padding.PKCS1v15())


def _generate_udid(seed: str) -> str:
    """Generate a deterministic MQTT UDID from a seed string.

    The result is the MQTT MAC ID prefix plus a deterministic 32-character
    hexadecimal UUID without dashes.

    Parameters:
        seed (str): Input seed used to deterministically derive the UDID.

    Returns:
        str: MQTT UDID string with prefix and 32 lowercase hex characters.
    """
    md5_digest = hashlib.md5(seed.encode("utf-8"), usedforsecurity=False).digest()
    u = uuid.UUID(bytes=md5_digest, version=3)
    return MQTT_MAC_ID_PREFIX + str(u).replace("-", "")
