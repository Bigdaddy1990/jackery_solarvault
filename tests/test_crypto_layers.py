"""Golden-vector tests for Jackery crypto layer separation."""

import asyncio
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from custom_components.jackery_solarvault.client.api import (
    JackeryApi,
    build_login_crypto_fields,
    encrypt_mqtt_body,
    generate_login_aes_key,
)
from custom_components.jackery_solarvault.const import (
    FIELD_ACCOUNT,
    FIELD_LOGIN_TYPE,
    FIELD_MAC_ID,
    FIELD_PASSWORD,
    FIELD_REGISTER_APP_ID,
    MQTT_CREDENTIAL_PASSWORD,
    REGISTER_APP_ID,
)

ROOT = Path(__file__).resolve().parents[1]


def _pkcs7_unpad(padded: bytes) -> bytes:
    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _aes_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()  # nosec B305  # noqa: S305
    return decryptor.update(ciphertext) + decryptor.finalize()


def _aes_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def test_layer_a_login_crypto_matches_app_fixed_vector() -> None:
    """Fixed Layer A seed gives a stable app-compatible golden vector."""
    login_bean = {
        FIELD_ACCOUNT: "user@example.invalid",
        FIELD_LOGIN_TYPE: 2,
        FIELD_MAC_ID: "02abcdef0123456789abcdef01234567",
        FIELD_PASSWORD: "secret",
        FIELD_REGISTER_APP_ID: REGISTER_APP_ID,
    }
    seed = b"1234567890123456"
    aes_key = base64.b64encode(seed)

    fields = build_login_crypto_fields(login_bean, random_source=lambda length: seed)

    expected = (
        "U6SuPAHg9+6MpQAmpIs6xdTV9p/TRKvSZkpjmLTebeoG3AtALBVAIqOxIzs+jV92"
        "TR8k/T7IKyZ6UANnTf4Brb6/vnQ2/T/kSsz1lZhqpbXCN2KWbWEwF+P0jMzobkgW"
        "/ZpRrelbQDPFvXwSvRSVuq0VDtRXdQe/fXnowEZjUyWCLX+ZSNXoCdzJNCsDtWYe"
        "1+yG1kbsxi4p49b+bdq49g=="
    )
    assert fields["aesEncryptData"] == expected  # noqa: S101
    plaintext = _pkcs7_unpad(
        _aes_ecb_decrypt(base64.b64decode(fields["aesEncryptData"]), aes_key),
    )
    assert json.loads(plaintext.decode("utf-8")) == login_bean  # noqa: S101
    assert base64.b64decode(fields["rsaForAesKey"])  # noqa: S101

    injected_fields = build_login_crypto_fields(login_bean, aes_key=aes_key)
    assert injected_fields["aesEncryptData"] == expected  # noqa: S101


def test_layer_a_login_key_uses_injectable_random_source() -> None:
    """Layer A production path can use random bytes without a real login."""
    calls: list[int] = []

    def fake_random(length: int) -> bytes:
        calls.append(length)
        return bytes(range(length))

    assert generate_login_aes_key(fake_random) == base64.b64encode(bytes(range(16)))  # noqa: S101
    assert calls == [16]  # noqa: S101


def test_layer_a_login_runtime_code_does_not_use_static_const_aes_key() -> None:
    """Login must not regress to the old static Layer A AES key."""
    auth_source = (
        ROOT / "custom_components/jackery_solarvault/client/_endpoints/auth.py"
    ).read_text(encoding="utf-8")

    assert "AES_KEY" not in auth_source  # noqa: S101
    assert "1234567890123456" not in auth_source  # noqa: S101
    assert "build_login_crypto_fields(login_bean)" in auth_source  # noqa: S101


def test_layer_c_mqtt_payload_crypto_uses_bluetooth_key_only() -> None:
    """MQTT/BLE payload crypto stays separate from MQTT connect password."""
    body = {"cmd": 107, "value": "on"}
    bluetooth_key = b"0123456789abcdef"

    ciphertext = base64.b64decode(encrypt_mqtt_body(body, bluetooth_key))
    plaintext = _pkcs7_unpad(_aes_cbc_decrypt(ciphertext, bluetooth_key, bluetooth_key))

    assert plaintext == b'{"cmd":107,"value":"on"}'  # noqa: S101


def test_mqtt_connect_password_is_not_layer_c_payload_crypto() -> None:
    """MQTT connect password derives from mqttPassWord seed, not bluetoothKey."""
    api = JackeryApi(object(), "account", "password")  # type: ignore[arg-type]
    api._token = "token"  # noqa: S105, SLF001
    api._mqtt_user_id = "user-1"  # noqa: SLF001
    api._mqtt_mac_id = "02abcdef0123456789abcdef01234567"  # noqa: SLF001
    seed = bytes(range(32))
    api._mqtt_seed_b64 = base64.b64encode(seed).decode("ascii")  # noqa: SLF001

    credentials = asyncio.run(api.async_get_mqtt_credentials())
    mqtt_password_ciphertext = base64.b64decode(credentials[MQTT_CREDENTIAL_PASSWORD])
    mqtt_plaintext = _pkcs7_unpad(
        _aes_cbc_decrypt(mqtt_password_ciphertext, seed, seed[:16]),
    )

    assert mqtt_plaintext == b"user-1@02abcdef0123456789abcdef01234567"  # noqa: S101
    assert credentials[MQTT_CREDENTIAL_PASSWORD] != encrypt_mqtt_body(  # noqa: S101
        {"cmd": 107},
        b"0123456789abcdef",
    )
