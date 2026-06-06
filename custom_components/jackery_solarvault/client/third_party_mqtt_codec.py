"""App-compatible codec for ThirdPartMQTTConfig credential fields."""

import base64
import binascii
import secrets

from .ble import BLE_AES_IV_LEN, aes_decrypt, aes_encrypt


def _split_iv_envelope(envelope: bytes) -> tuple[bytes, bytes]:
    """Split ``iv || ciphertext`` and validate the envelope shape."""
    if len(envelope) <= BLE_AES_IV_LEN:
        raise ValueError("missing third-party MQTT IV envelope")  # noqa: TRY003
    return envelope[:BLE_AES_IV_LEN], envelope[BLE_AES_IV_LEN:]


def encode_third_party_mqtt_field(value: str, bluetooth_key: bytes) -> str:
    """Encode one ThirdPartMQTTConfig secret like ``bb/e.d(String)``.

    The wire envelope is ``iv || ciphertext`` encoded with Base64. A fresh IV
    is generated per call so repeated secrets do not produce repeated ciphertext.
    """
    if len(bluetooth_key) != BLE_AES_IV_LEN:
        raise ValueError(  # noqa: TRY003
            "third-party MQTT codec requires a 16-byte decoded bluetoothKey "
            f"for bb/e.d(String), got {len(bluetooth_key)} bytes"
        )
    iv = secrets.token_bytes(BLE_AES_IV_LEN)
    ciphertext = aes_encrypt(value.encode("utf-8"), bluetooth_key, iv)
    return base64.b64encode(iv + ciphertext).decode("ascii")


def decode_third_party_mqtt_field(value: str, bluetooth_key: bytes) -> str:
    """Decode one ThirdPartMQTTConfig secret like ``bb/e.c(String)``."""
    if len(bluetooth_key) != BLE_AES_IV_LEN:
        raise ValueError(  # noqa: TRY003
            "third-party MQTT codec requires a 16-byte decoded bluetoothKey "
            f"for bb/e.c(String), got {len(bluetooth_key)} bytes"
        )
    try:
        envelope = base64.b64decode(value, validate=True)
        iv, ciphertext = _split_iv_envelope(envelope)
        plaintext = aes_decrypt(ciphertext, bluetooth_key, iv)
        return plaintext.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError) as err:
        raise ValueError("invalid app-encoded third-party MQTT field") from err  # noqa: TRY003
