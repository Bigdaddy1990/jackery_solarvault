"""App-compatible codec for ThirdPartMQTTConfig credential fields."""

import base64

from .ble import BLE_AES_IV_LEN, aes_decrypt, aes_encrypt


def encode_third_party_mqtt_field(value: str, bluetooth_key: bytes) -> str:
    """
    Encode a credential value into the app-compatible ThirdPartMQTTConfig format.
    
    Encrypts the UTF-8 bytes of `value` using AES-CBC with PKCS7 padding, using `bluetooth_key` as both the AES key and IV, then Base64-encodes the resulting ciphertext without line breaks.
    
    Parameters:
        value (str): Plaintext credential to encode (e.g., username, password, or token).
        bluetooth_key (bytes): Decoded bluetoothKey used as AES key and IV; must be 16 bytes.
    
    Returns:
        str: ASCII Base64 string containing the encrypted ciphertext.
    
    Raises:
        ValueError: If `bluetooth_key` does not have length 16.
    """
    if len(bluetooth_key) != BLE_AES_IV_LEN:
        raise ValueError(
            "third-party MQTT codec requires a 16-byte decoded bluetoothKey "
            f"for bb/e.d(String), got {len(bluetooth_key)} bytes"
        )
    ciphertext = aes_encrypt(value.encode("utf-8"), bluetooth_key, bluetooth_key)
    return base64.b64encode(ciphertext).decode("ascii")


def decode_third_party_mqtt_field(value: str, bluetooth_key: bytes) -> str:
    """
    Decrypts an app-encoded third-party MQTT credential value.
    
    Parameters:
        value (str): Base64-encoded ciphertext produced by the app.
        bluetooth_key (bytes): Decoded bluetoothKey; must be 16 bytes and is used as both AES key and IV.
    
    Returns:
        str: Decrypted plaintext decoded from UTF-8.
    
    Raises:
        ValueError: If `bluetooth_key` is not 16 bytes, or if `value` is not valid base64/ciphertext or does not decode to valid UTF-8.
    """
    if len(bluetooth_key) != BLE_AES_IV_LEN:
        raise ValueError(
            "third-party MQTT codec requires a 16-byte decoded bluetoothKey "
            f"for bb/e.c(String), got {len(bluetooth_key)} bytes"
        )
    try:
        ciphertext = base64.b64decode(value)
        plaintext = aes_decrypt(ciphertext, bluetooth_key, bluetooth_key)
        return plaintext.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as err:
        raise ValueError("invalid app-encoded third-party MQTT field") from err
