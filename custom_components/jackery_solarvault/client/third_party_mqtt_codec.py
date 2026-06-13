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
    """Generate a 9-digit numeric token used as the app fallback token.

    Returns:
        str: A 9-character string consisting only of decimal digits (0–9).
    """
    return "".join(str(secrets.randbelow(10)) for _ in range(9))


def third_party_mqtt_config_from_options(
    options: dict[str, Any],
    generated_token: str | None,
) -> dict[str, Any]:
    """Build a device-ready app field mapping for ThirdPartMQTTConfig from Home Assistant options.

    Selects the token from options (trimmed); if that token is empty and `generated_token`
    is provided, uses `generated_token`. Maps option values into app fields:
    - enable: `1` if configured truthy, else `0`
    - ip: string (empty if absent/falsey)
    - port: integer (defaults if absent/falsey)
    - username/password: strings (empty if absent/falsey)
    - token: selected token

    Parameters:
        options (dict[str, Any]): Home Assistant config-entry options.
        generated_token (str | None): Fallback token to use when the configured token is empty.

    Returns:
        dict[str, Any]: Mapping of app field constants to values ready for publishing to the device.
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
    """Normalize and validate a ThirdParty MQTT token and determine whether a generated token should be used.

    Parameters:
    	token (str): Candidate token value; will be coerced to string and stripped of surrounding whitespace.
    	generated_token (str | None): Previously generated 9-digit token, or `None` if none exists.

    Returns:
    	(token_str (str), use_generated (bool), new_generated_token (str | None)):
    		- token_str: The 9-digit token to use.
    		- use_generated: `True` if the chosen token is (or should be treated as) a generated token, `False` if it is a valid user-provided token.
    		- new_generated_token: The newly generated token when one was created, otherwise `None`.

    Raises:
    	ValueError: If a provided non-empty token is not exactly nine decimal digits.
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
    """Decode encrypted credential fields in a ThirdPartMQTTConfig body and return a new dict containing plaintext values and decode metadata.

    When `bluetooth_key` is None the returned dict will have `_ha_plaintext = False` and `_decode_error = "missing_bluetooth_key"`.
    If `bluetooth_key` is provided, the function attempts to decode the username, password, and token fields (when present as non-empty strings). Successfully decoded fields replace the original values; fields that fail to decode are listed in `_decode_failed_fields`. The `_ha_plaintext` flag is `True` if any field was decoded, `False` otherwise.

    Parameters:
        body (dict[str, Any]): The input config/body to decode; not mutated.
        bluetooth_key (bytes | None): 16-byte AES key/IV used to decode fields, or `None` to indicate decoding cannot be performed.

    Returns:
        dict[str, Any]: A new dict copying `body` with decoded credential fields (when decoded) and metadata keys `_ha_plaintext`, and either `_decode_failed_fields` or `_decode_error` as described above.
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
    """Build a plaintext ThirdPartMQTTConfig dictionary by merging HA options with device-reported values.

    Starts from the config derived from `options` and `generated_token`. If `device_data` contains a `PAYLOAD_THIRD_PARTY_MQTT_CONFIG` mapping, values for enable, IP, and port present in the device payload overwrite the corresponding entries. If that device payload has `_ha_plaintext` set to `True`, present credential fields (username, password, token) also overwrite the config. Inputs are not mutated.

    Parameters:
        options (dict[str, Any]): Home Assistant option values used to build the base config.
        generated_token (str | None): A pre-generated 9-digit token to use when the options token is empty.
        device_data (dict[str, Any] | None): Device GET payload that may contain the current ThirdPartMQTTConfig.

    Returns:
        dict[str, Any]: The merged plaintext ThirdPartMQTTConfig ready for entity setters.
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
