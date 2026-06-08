"""App-compatible codec for ThirdPartMQTTConfig credential fields."""

import base64

from .ble import BLE_AES_IV_LEN, aes_decrypt, aes_encrypt


def encode_third_party_mqtt_field(value: str, bluetooth_key: bytes) -> str:
    """Encode one ThirdPartMQTTConfig secret like ``bb/e.d(String)``.

    Smali source:
    ``HomeDeviceController.g1`` runs ``userName``, ``password`` and ``token``
    through ``Lbb/c;->d(String)``. For SolarVault home devices ``bb/e.d``
    performs AES/CBC/PKCS7 with the decoded ``bluetoothKey`` as AES key and IV,
    then Base64-encodes the ciphertext without line wrapping.
    """
    if len(bluetooth_key) != BLE_AES_IV_LEN:
        raise ValueError(  # noqa: TRY003
            "third-party MQTT codec requires a 16-byte decoded bluetoothKey "
            f"for bb/e.d(String), got {len(bluetooth_key)} bytes"
        )
    ciphertext = aes_encrypt(value.encode("utf-8"), bluetooth_key, bluetooth_key)
    return base64.b64encode(ciphertext).decode("ascii")


def decode_third_party_mqtt_field(value: str, bluetooth_key: bytes) -> str:
    """Decode one ThirdPartMQTTConfig secret like ``bb/e.c(String)``."""
    if len(bluetooth_key) != BLE_AES_IV_LEN:
        raise ValueError(  # noqa: TRY003
            "third-party MQTT codec requires a 16-byte decoded bluetoothKey "
            f"for bb/e.c(String), got {len(bluetooth_key)} bytes"
        )
    try:
        ciphertext = base64.b64decode(value)
        plaintext = aes_decrypt(ciphertext, bluetooth_key, bluetooth_key)
        return plaintext.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as err:
        raise ValueError("invalid app-encoded third-party MQTT field") from err  # noqa: TRY003
