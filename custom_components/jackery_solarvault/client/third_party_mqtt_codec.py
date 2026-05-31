"""App-compatible codec for ThirdPartMQTTConfig credential fields."""

import base64

from .ble import BLE_AES_IV_LEN, aes_encrypt


def encode_third_party_mqtt_field(value: str, bluetooth_key: bytes) -> str:
    """Encode one ThirdPartMQTTConfig secret like ``bb/e.d(String)``.

    Smali source:
    ``HomeDeviceController.g1`` runs ``userName``, ``password`` and ``token``
    through ``Lbb/c;->d(String)``. For SolarVault home devices ``bb/e.d``
    performs AES/CBC/PKCS7 with the decoded ``bluetoothKey`` as AES key and IV,
    then Base64-encodes the ciphertext without line wrapping.
    """
    if len(bluetooth_key) != BLE_AES_IV_LEN:
        raise ValueError(
            "third-party MQTT codec requires a 16-byte decoded bluetoothKey "
            f"for bb/e.d(String), got {len(bluetooth_key)} bytes"
        )
    ciphertext = aes_encrypt(value.encode("utf-8"), bluetooth_key, bluetooth_key)
    return base64.b64encode(ciphertext).decode("ascii")
