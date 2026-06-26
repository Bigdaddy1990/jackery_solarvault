"""Cryptographic primitives for the Jackery SolarVault protocol.

AES-128-ECB/RSA-1024 hybrid login encryption (Layer A), AES-128-CBC/PKCS7
MQTT body encryption (Layer C), and deterministic UDID generation.
"""

import base64
import hashlib
import json
from typing import Any
import uuid

from cryptography.hazmat.primitives.asymmetric import padding as asym_padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.serialization import load_der_public_key

from ..const import MQTT_MAC_ID_PREFIX


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
    """Encrypt an MQTT command body using AES-128-CBC with PKCS7 padding and return the ciphertext as Base64.

    Encrypts the compact JSON serialization of `body` using AES-128-CBC where the encryption key and IV are both the provided Bluetooth key, then Base64-encodes the ciphertext.

    Parameters:
        body (dict[str, Any]): Command body to serialize and encrypt.
        bluetooth_key (bytes): 16-byte Bluetooth key used as both AES key and IV.

    Returns:
        str: Base64-encoded ciphertext.

    Raises:
        ValueError: If `bluetooth_key` is not exactly 16 bytes.
    """  # noqa: E501
    if len(bluetooth_key) != 16:  # noqa: PLR2004
        raise ValueError(  # noqa: TRY003
            f"encrypt_mqtt_body: bluetoothKey must be 16 bytes, got {len(bluetooth_key)}"  # noqa: E501
        )
    plaintext = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )  # noqa: E501, RUF100
    ciphertext = _aes_cbc_encrypt(plaintext, bluetooth_key, bluetooth_key)
    return base64.b64encode(ciphertext).decode("ascii")


def _rsa_pkcs1v15_encrypt(data: bytes, public_key_b64: str) -> bytes:
    """Encrypt `data` using RSA PKCS#1 v1.5 with a base64-encoded DER RSA public key.

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
        raise TypeError(  # noqa: TRY003
            f"Jackery login expects an RSA public key, got {type(public_key).__name__}"
        )
    return public_key.encrypt(data, asym_padding.PKCS1v15())


def _generate_udid(seed: str) -> str:
    """Generate a deterministic MQTT UDID from a seed string.

    The result is the module's MQTT MAC ID prefix concatenated with a 32-character hexadecimal UUID (no dashes), produced deterministically from the provided seed so the same seed always yields the same UDID.

    Parameters:
        seed (str): Input seed used to deterministically derive the UDID.

    Returns:
        str: MQTT UDID string comprising `MQTT_MAC_ID_PREFIX` followed by 32 lowercase hex characters (no dashes).
    """  # noqa: E501
    md5_digest = hashlib.md5(seed.encode("utf-8"), usedforsecurity=False).digest()
    u = uuid.UUID(bytes=md5_digest, version=3)
    return MQTT_MAC_ID_PREFIX + str(u).replace("-", "")
