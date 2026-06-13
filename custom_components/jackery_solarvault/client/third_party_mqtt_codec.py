"""App-compatible codec for ThirdPartMQTTConfig credential fields."""

import base64
import secrets
from typing import Any

from jackery_solarvault.const import (
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    DEFAULT_THIRD_PARTY_MQTT_ENABLE,
    DEFAULT_THIRD_PARTY_MQTT_IP,
    DEFAULT_THIRD_PARTY_MQTT_PASSWORD,
    DEFAULT_THIRD_PARTY_MQTT_PORT,
    DEFAULT_THIRD_PARTY_MQTT_TOKEN,
    DEFAULT_THIRD_PARTY_MQTT_USERNAME,
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_THIRD_PARTY_MQTT_IP,
    FIELD_THIRD_PARTY_MQTT_PASSWORD,
    FIELD_THIRD_PARTY_MQTT_PORT,
    FIELD_THIRD_PARTY_MQTT_TOKEN,
    FIELD_THIRD_PARTY_MQTT_USERNAME,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
)

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


def generate_third_party_mqtt_token() -> str:
    """Generate a 9-digit numeric token matching app fallback behavior."""
    return "".join(str(secrets.randbelow(10)) for _ in range(9))


def third_party_mqtt_config_from_options(
    options: dict[str, Any],
    generated_token: str | None,
) -> dict[str, Any]:
    """Return HA-configured third-party MQTT settings as app fields.

    Pure function — reads config-entry options dict and returns the field dict
    suitable for publishing to the device.
    """
    token = str(
        options.get(CONF_THIRD_PARTY_MQTT_TOKEN, DEFAULT_THIRD_PARTY_MQTT_TOKEN) or ""
    ).strip()
    if not token and generated_token is not None:
        token = generated_token
    return {
        FIELD_THIRD_PARTY_MQTT_ENABLE: 1
        if bool(
            options.get(CONF_THIRD_PARTY_MQTT_ENABLE, DEFAULT_THIRD_PARTY_MQTT_ENABLE)
        )
        else 0,
        FIELD_THIRD_PARTY_MQTT_IP: str(
            options.get(CONF_THIRD_PARTY_MQTT_IP, DEFAULT_THIRD_PARTY_MQTT_IP) or ""
        ),
        FIELD_THIRD_PARTY_MQTT_PORT: int(
            options.get(CONF_THIRD_PARTY_MQTT_PORT, DEFAULT_THIRD_PARTY_MQTT_PORT)
            or DEFAULT_THIRD_PARTY_MQTT_PORT
        ),
        FIELD_THIRD_PARTY_MQTT_USERNAME: str(
            options.get(
                CONF_THIRD_PARTY_MQTT_USERNAME, DEFAULT_THIRD_PARTY_MQTT_USERNAME
            )
            or ""
        ),
        FIELD_THIRD_PARTY_MQTT_PASSWORD: str(
            options.get(
                CONF_THIRD_PARTY_MQTT_PASSWORD, DEFAULT_THIRD_PARTY_MQTT_PASSWORD
            )
            or ""
        ),
        FIELD_THIRD_PARTY_MQTT_TOKEN: token,
    }


def stable_third_party_mqtt_token(
    token: str,
    generated_token: str | None,
) -> tuple[str, bool, str | None]:
    """Return a valid app-style token, whether HA generated it, and new generated token if created.

    Pure function — returns (token, use_generated, new_generated_token_or_none).
    Raises ValueError if the provided token is not a valid 9-digit decimal.
    """
    raw_token = str(token).strip()
    if raw_token:
        if len(raw_token) != 9 or not raw_token.isdecimal():  # noqa: PLR2004
            raise ValueError(  # noqa: TRY003
                "Third-party MQTT token must be a separate 9-digit decimal "
                "value; topic belongs in the topic filter option"
            )
        if raw_token == generated_token:
            return raw_token, True, None
        return raw_token, False, None

    if generated_token is None:
        new_token = generate_third_party_mqtt_token()
        return new_token, True, new_token
    return generated_token, True, None


def decode_third_party_mqtt_config_body(
    body: dict[str, Any],
    bluetooth_key: bytes | None,
) -> dict[str, Any]:
    """Decode app-encoded ThirdPartMQTTConfig credential fields.

    Pure function — returns a new dict with decoded credential fields and
    metadata flags ``_ha_plaintext`` and ``_decode_failed_fields``.
    """
    config = dict(body)
    if bluetooth_key is None:
        config["_ha_plaintext"] = False
        config["_decode_error"] = "missing_bluetooth_key"
        return config

    decoded_any = False
    failed_fields: list[str] = []
    for key in (
        FIELD_THIRD_PARTY_MQTT_USERNAME,
        FIELD_THIRD_PARTY_MQTT_PASSWORD,
        FIELD_THIRD_PARTY_MQTT_TOKEN,
    ):
        value = body.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            config[key] = decode_third_party_mqtt_field(value, bluetooth_key)
        except ValueError:
            failed_fields.append(key)
            continue
        decoded_any = True

    config["_ha_plaintext"] = decoded_any
    if failed_fields:
        config["_decode_failed_fields"] = failed_fields
    return config


def third_party_mqtt_config_plaintext(
    options: dict[str, Any],
    generated_token: str | None,
    device_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return plaintext third-party MQTT config for HA entities.

    Pure function — merges HA-configured options with the latest device data
    (if available) to produce a plaintext config dict suitable for entity
    setters. Device GET responses may contain app-encoded credential fields;
    only locally configured or locally patched plaintext credentials are used
    for writes.
    """
    config = third_party_mqtt_config_from_options(options, generated_token)
    if isinstance(device_data, dict):
        current = device_data.get(PAYLOAD_THIRD_PARTY_MQTT_CONFIG)
        if isinstance(current, dict):
            for key in (
                FIELD_THIRD_PARTY_MQTT_ENABLE,
                FIELD_THIRD_PARTY_MQTT_IP,
                FIELD_THIRD_PARTY_MQTT_PORT,
            ):
                if current.get(key) is not None:
                    config[key] = current[key]
            if current.get("_ha_plaintext") is True:
                for key in (
                    FIELD_THIRD_PARTY_MQTT_USERNAME,
                    FIELD_THIRD_PARTY_MQTT_PASSWORD,
                    FIELD_THIRD_PARTY_MQTT_TOKEN,
                ):
                    if current.get(key) is not None:
                        config[key] = current[key]
    return config
