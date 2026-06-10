"""App-compatible codec for ThirdPartMQTTConfig credential fields."""

import base64

from .ble import BLE_AES_IV_LEN, aes_decrypt, aes_encrypt


def encode_third_party_mqtt_field(value: str, bluetooth_key: bytes) -> str:
    """Encrypt a plaintext secret into the app-compatible Base64 ciphertext used by ThirdPartMQTTConfig.

    The value is encrypted with AES/CBC/PKCS7 using `bluetooth_key` as both AES key and IV, then Base64-encoded without line wrapping.

    Parameters:
        value (str): Plaintext secret to encode (e.g., username, password, token).
        bluetooth_key (bytes): Decoded Bluetooth key used as AES key and IV; must be 16 bytes (BLE_AES_IV_LEN).

    Returns:
        str: ASCII Base64 string of the ciphertext.

    Raises:
        ValueError: If `bluetooth_key` length is not 16 bytes.
    """
    if len(bluetooth_key) != BLE_AES_IV_LEN:
        raise ValueError(  # noqa: TRY003
            "third-party MQTT codec requires a 16-byte decoded bluetoothKey "
            f"for bb/e.d(String), got {len(bluetooth_key)} bytes"
        )
    ciphertext = aes_encrypt(value.encode("utf-8"), bluetooth_key, bluetooth_key)
    return base64.b64encode(ciphertext).decode("ascii")


def decode_third_party_mqtt_field(value: str, bluetooth_key: bytes) -> str:
    """Decode an app-encoded ThirdPartMQTTConfig secret into its UTF-8 plaintext.

    Parameters:
        value (str): Base64-encoded ciphertext produced by the app for a credential field.
        bluetooth_key (bytes): Raw 16-byte decoded bluetoothKey used as both AES key and IV.

    Returns:
        str: The decrypted plaintext decoded as UTF-8.

    Raises:
        ValueError: If `bluetooth_key` does not have length 16, or if `value` is not a valid app-encoded field (invalid Base64 or decryption/UTF-8 decoding failure).
    """
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
