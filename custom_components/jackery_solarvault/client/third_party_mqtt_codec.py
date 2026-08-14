"""App-compatible codec for ThirdPartMQTTConfig credential fields."""

import base64
import logging
import secrets
from typing import Any

from ..const import (
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    DEFAULT_LOCAL_MQTT_ENABLE,
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
from ..util import safe_bool
from .ble import BLE_AES_IV_LEN, aes_decrypt, aes_encrypt

_LOGGER = logging.getLogger(__name__)

_THIRD_PARTY_MQTT_TOKEN_LEN = 9


def generate_third_party_mqtt_token() -> str:
    """Generate the App's nine-digit Third-Party MQTT fallback token."""
    return "".join(
        str(secrets.randbelow(10)) for _ in range(_THIRD_PARTY_MQTT_TOKEN_LEN)
    )


def _is_app_third_party_mqtt_token(value: str) -> bool:
    """Return whether ``value`` matches the App's nine ASCII digit token."""
    return (
        len(value) == _THIRD_PARTY_MQTT_TOKEN_LEN
        and value.isascii()
        and value.isdecimal()
    )


def stable_third_party_mqtt_token(
    token: object,
    prior_generated: object,
) -> tuple[str, bool, str | None]:
    """Resolve the effective third-party MQTT token to publish.

    Accepts a token from options and an optional prior token read back from
    the device. Returns a tuple of ``(token, use_cached, new)``:

    - ``token``: the value that should be sent to the device.
    - ``use_cached``: ``True`` if the token came from prior device/readback
      state, ``False`` if it came from options.
    - ``new``: the newly generated App-compatible fallback, otherwise ``None``.

    The token must be exactly nine decimal digits. Empty/whitespace-only input
    falls back to ``prior_generated`` if valid; otherwise a token is generated
    exactly like App 2.4.0 ``MqttMsgActivity`` (nine ``Random.nextInt(10)``
    digits). The caller persists ``new`` so reconnects reuse the same token.
    """
    raw_token = "" if token is None else str(token).strip()

    if raw_token:
        if not _is_app_third_party_mqtt_token(raw_token):
            msg = (
                "third-party MQTT token must be a "
                f"{_THIRD_PARTY_MQTT_TOKEN_LEN}-digit decimal string"
            )
            raise ValueError(msg)
        prior = "" if prior_generated is None else str(prior_generated).strip()
        if prior and prior == raw_token:
            return (raw_token, True, None)
        return (raw_token, False, None)

    prior = "" if prior_generated is None else str(prior_generated).strip()
    if prior and _is_app_third_party_mqtt_token(prior):
        return (prior, True, None)

    new_token = generate_third_party_mqtt_token()
    return (new_token, True, new_token)


def resolve_third_party_mqtt_token(
    options: dict[str, Any],
    prior_generated: object = None,
) -> tuple[str, bool]:
    """Resolve the third-party MQTT token from options or device readback.

    This is the public helper for code paths that need to push config to the
    device (e.g. local MQTT bridge setup). It reads the token from ``options``
    and falls back to ``prior_generated`` via ``stable_third_party_mqtt_token``.
    Returns a tuple of ``(token, newly_generated)``. When neither options nor
    device readback contains a usable value, this mirrors the App's fallback
    generation and reports ``newly_generated=True``.

    Parameters:
        options (dict[str, Any]): Home Assistant config-entry options.
        prior_generated (object | None): Token previously read from the device;
            reused when ``options`` lacks a usable token.

    Returns:
        tuple[str, bool]: The resolved token and whether it was newly generated.
    """
    raw_token = options.get(CONF_THIRD_PARTY_MQTT_TOKEN, DEFAULT_THIRD_PARTY_MQTT_TOKEN)
    token, _, new = stable_third_party_mqtt_token(raw_token, prior_generated)
    return (token, new is not None)


def encode_third_party_mqtt_field(value: str, bluetooth_key: bytes) -> str:
    """Encode one ThirdPartMQTTConfig secret like ``bb/e.d(String)``.

    Smali source:
    ``HomeDeviceController.g1`` runs ``userName``, ``password`` and ``token``
    through ``Lbb/c;->d(String)``. For SolarVault home devices ``bb/e.d``
    performs AES/CBC/PKCS7 with the decoded ``bluetoothKey`` as AES key and IV,
    then Base64-encodes the ciphertext without line wrapping.
    """
    if len(bluetooth_key) != BLE_AES_IV_LEN:
        msg = (
            "third-party MQTT codec requires a 16-byte decoded bluetoothKey "
            f"for bb/e.d(String), got {len(bluetooth_key)} bytes"
        )
        raise ValueError(msg)
    ciphertext = aes_encrypt(value.encode("utf-8"), bluetooth_key, bluetooth_key)
    return base64.b64encode(ciphertext).decode("ascii")


def decode_third_party_mqtt_field(value: str, bluetooth_key: bytes) -> str:
    """Decode one ThirdPartMQTTConfig secret like ``bb/e.c(String)``."""
    if len(bluetooth_key) != BLE_AES_IV_LEN:
        msg = (
            "third-party MQTT codec requires a 16-byte decoded bluetoothKey "
            f"for bb/e.c(String), got {len(bluetooth_key)} bytes"
        )
        raise ValueError(msg)
    try:
        ciphertext = base64.b64decode(value)
        plaintext = aes_decrypt(ciphertext, bluetooth_key, bluetooth_key)
        return plaintext.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as err:
        msg = "invalid app-encoded third-party MQTT field"
        raise ValueError(msg) from err


def third_party_mqtt_config_from_options(
    options: dict[str, Any],
    prior_generated: object = None,
) -> dict[str, Any]:
    """Build a device-ready app field mapping for ThirdPartMQTTConfig from Home.

    Assistant options.

    Selects the token from options, falling back to ``prior_generated`` (the
    token previously decoded from device readback) when the configured token is
    blank. Local listener options (``local_mqtt_*``) are preferred for host
    credentials and the legacy ``third_party_mqtt_*`` values are used as
    fallback. Maps option values into app fields:
    - enable: `1` if configured truthy, else `0`
    - ip: string (empty if absent/falsey)
    - port: integer (defaults if absent/falsey)
    - username/password: strings (empty if absent/falsey)
    - token: resolved via ``stable_third_party_mqtt_token``

    Parameters:
        options (dict[str, Any]): Home Assistant config-entry options.
        prior_generated (object | None): Token previously decoded from the
            device; reused when ``options`` lacks a usable token.

    Returns:
        dict[str, Any]: Mapping of app field constants to values ready for publishing
        to the device.
    """
    raw_token = options.get(CONF_THIRD_PARTY_MQTT_TOKEN, DEFAULT_THIRD_PARTY_MQTT_TOKEN)
    token, _use_generated, _new = stable_third_party_mqtt_token(
        raw_token, prior_generated
    )
    if CONF_LOCAL_MQTT_ENABLE in options:
        enabled_value = options.get(CONF_LOCAL_MQTT_ENABLE)
        enabled_default = DEFAULT_LOCAL_MQTT_ENABLE
    else:
        enabled_value = options.get(
            CONF_THIRD_PARTY_MQTT_ENABLE,
            DEFAULT_THIRD_PARTY_MQTT_ENABLE,
        )
        enabled_default = DEFAULT_THIRD_PARTY_MQTT_ENABLE
    parsed_enabled = safe_bool(enabled_value)
    enabled = enabled_default if parsed_enabled is None else parsed_enabled
    return {
        FIELD_THIRD_PARTY_MQTT_ENABLE: 1 if enabled else 0,
        FIELD_THIRD_PARTY_MQTT_IP: str(
            options.get(CONF_LOCAL_MQTT_HOST)
            or options.get(CONF_THIRD_PARTY_MQTT_IP, DEFAULT_THIRD_PARTY_MQTT_IP)
            or "",
        ),
        FIELD_THIRD_PARTY_MQTT_PORT: int(
            options.get(CONF_LOCAL_MQTT_PORT)
            or options.get(CONF_THIRD_PARTY_MQTT_PORT, DEFAULT_THIRD_PARTY_MQTT_PORT)
            or DEFAULT_THIRD_PARTY_MQTT_PORT,
        ),
        FIELD_THIRD_PARTY_MQTT_USERNAME: str(
            options.get(CONF_LOCAL_MQTT_USERNAME)
            or options.get(
                CONF_THIRD_PARTY_MQTT_USERNAME, DEFAULT_THIRD_PARTY_MQTT_USERNAME
            )
            or "",
        ),
        FIELD_THIRD_PARTY_MQTT_PASSWORD: str(
            options.get(CONF_LOCAL_MQTT_PASSWORD)
            or options.get(
                CONF_THIRD_PARTY_MQTT_PASSWORD, DEFAULT_THIRD_PARTY_MQTT_PASSWORD
            )
            or "",
        ),
        FIELD_THIRD_PARTY_MQTT_TOKEN: token,
    }


def decode_third_party_mqtt_config_body(
    body: dict[str, Any],
    bluetooth_key: bytes | None,
) -> dict[str, Any]:
    """Decode encrypted credential fields in a ThirdPartMQTTConfig body and return a.

    new dict containing plaintext values and decode metadata.

    When `bluetooth_key` is None the returned dict will have `_ha_plaintext = False`
    and `_decode_error = "missing_bluetooth_key"`.
    If `bluetooth_key` is provided, the function attempts to decode the username,
    password, and token fields (when present as non-empty strings). Successfully
    decoded fields replace the original values; fields that fail to decode are listed
    in `_decode_failed_fields`. The `_ha_plaintext` flag is `True` only when at least
    one field was decoded and no field failed decoding.

    Parameters:
        body (dict[str, Any]): The input config/body to decode; not mutated.
        bluetooth_key (bytes | None): 16-byte AES key/IV used to decode fields, or
        `None` to indicate decoding cannot be performed.

    Returns:
        dict[str, Any]: A new dict copying `body` with decoded credential fields (when
        decoded) and metadata keys `_ha_plaintext`, and either `_decode_failed_fields`
        or `_decode_error` as described above.
    """
    config = dict(body)
    if bluetooth_key is None:
        config["_ha_plaintext"] = False
        config["_decode_error"] = "missing_bluetooth_key"
        return config

    decoded_fields: set[str] = set()
    failed_fields: list[str] = []
    credential_keys = (
        FIELD_THIRD_PARTY_MQTT_USERNAME,
        FIELD_THIRD_PARTY_MQTT_PASSWORD,
        FIELD_THIRD_PARTY_MQTT_TOKEN,
    )
    for key in credential_keys:
        value = body.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            config[key] = decode_third_party_mqtt_field(value, bluetooth_key)
        except ValueError as err:
            _LOGGER.debug(
                "failed to decode third-party MQTT credential field %s: %s", key, err
            )
            failed_fields.append(key)
            continue
        decoded_fields.add(key)

    config["_ha_plaintext"] = bool(decoded_fields) and not failed_fields
    if decoded_fields:
        config["_decoded_fields"] = sorted(decoded_fields)
    if failed_fields:
        config["_decode_failed_fields"] = failed_fields
    return config


def third_party_mqtt_config_plaintext(
    options: dict[str, Any],
    prior_generated: object,
    device_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a plaintext ThirdPartMQTTConfig dictionary by merging HA options with.

    device-reported values.

    Starts from the config derived from `options`. If `device_data` contains a
    `PAYLOAD_THIRD_PARTY_MQTT_CONFIG` mapping, values for
    enable, IP, and port present in the device payload overwrite the corresponding
    entries. If that device payload has `_ha_plaintext` set to `True`, present
    credential fields (username, password, token) also overwrite the config. Inputs are
    not mutated.

    Parameters:
        options (dict[str, Any]): Home Assistant option values used to build the base
        config.
        prior_generated (object | None): Token previously decoded from the device;
        forwarded to ``third_party_mqtt_config_from_options`` so the device/app-owned
        token can be reused across sessions.
        device_data (dict[str, Any] | None): Device GET payload that may contain the
        current ThirdPartMQTTConfig.

    Returns:
        dict[str, Any]: The merged plaintext ThirdPartMQTTConfig ready for entity
        setters.
    """
    config = third_party_mqtt_config_from_options(options, prior_generated)
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
            decoded_fields = set(current.get("_decoded_fields") or ())
            failed_fields = set(current.get("_decode_failed_fields") or ())
            if current.get("_ha_plaintext") is True or decoded_fields:
                for key in (
                    FIELD_THIRD_PARTY_MQTT_USERNAME,
                    FIELD_THIRD_PARTY_MQTT_PASSWORD,
                    FIELD_THIRD_PARTY_MQTT_TOKEN,
                ):
                    if key in failed_fields:
                        continue
                    if decoded_fields and key not in decoded_fields:
                        continue
                    if current.get(key) is not None:
                        config[key] = current[key]
    return config
