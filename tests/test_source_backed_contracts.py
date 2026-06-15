"""Contracts pinned directly to files in source-of-truth/."""

# ruff: noqa: PLC2701, PLR2004, PLR6301, RUF012, RUF029, SIM102, SLF001

import ast
import asyncio
import base64
import csv
import importlib
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from custom_components.jackery_solarvault.client._crypto import (
    _aes_ecb_encrypt,
    encrypt_mqtt_body,
)
from custom_components.jackery_solarvault.client._endpoints.auth import (
    AuthEndpointMixin,
)
from custom_components.jackery_solarvault.client.mqtt_command import (
    publish_mqtt_command,
)
from custom_components.jackery_solarvault.const import (
    FIELD_ACTION_ID,
    FIELD_BODY,
    FIELD_DEVICE_SN,
    FIELD_MESSAGE_TYPE,
    FIELD_TIMESTAMP,
    FIELD_VERSION,
    MQTT_CREDENTIAL_CLIENT_ID,
    MQTT_CREDENTIAL_PASSWORD,
    MQTT_CREDENTIAL_USERNAME,
    MQTT_CREDENTIAL_USER_ID,
    MQTT_MESSAGE_CONTROL_COMBINE,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "source-of-truth"


def _read(path: str) -> str:
    return (SOURCE_ROOT / path).read_text(encoding="utf-8")


def _const_strings() -> set[str]:
    tree = ast.parse(
        (ROOT / "custom_components/jackery_solarvault/const.py").read_text()
    )
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                values.add(node.value.value)
    return values


def _decrypt_aes_cbc(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def test_all_source_truth_http_endpoints_exist_as_constants() -> None:
    """Every path in jackery_http_api_endpoints_v2.csv must be pinned in const.py."""
    const_values = _const_strings()
    with (SOURCE_ROOT / "jackery_http_api_endpoints_v2.csv").open(
        encoding="utf-8"
    ) as f:
        rows = list(csv.DictReader(f))

    expected_paths = {f"/v1/{row['path']}" for row in rows}
    assert expected_paths <= const_values


def test_http_endpoint_request_fields_are_not_doc_copies() -> None:
    """CSV request fields remain the endpoint-field authority, independent of docs/."""
    with (SOURCE_ROOT / "jackery_http_api_endpoints_v2.csv").open(
        encoding="utf-8"
    ) as f:
        by_path = {
            row["path"]: row["request_fields"].split(",") for row in csv.DictReader(f)
        }

    assert by_path["auth/login"] == ["aesEncryptData", "rsaForAesKey"]
    assert by_path["device/bind/list"] == [""]
    assert by_path["device/property"] == ["deviceId"]
    assert by_path["device/dynamic/saveSingleMode"] == [
        "currency",
        "singlePrice",
        "systemId",
    ]
    assert by_path["device/accessories"] == ["devices", "id", "parentDeviceId"]


async def test_mqtt_command_envelope_matches_re_supplement() -> None:
    """Command publish creates the exact source-of-truth MQTT envelope."""
    supplement = _read("Jackery_2.1.1_RE_Supplement.md")
    for key in (
        "deviceSn",
        "id",
        "version",
        "messageType",
        "actionId",
        "timestamp",
        "body",
    ):
        assert f'"{key}"' in supplement
    assert "hb/app/<sn>/command" in supplement

    published: dict[str, Any] = {}

    class Mqtt:
        is_connected = True
        diagnostics: dict[str, str] = {}

        async def async_publish_json(
            self, topic: str, payload: dict[str, Any], *, qos: int, retain: bool
        ) -> None:
            published.update({
                "topic": topic,
                "payload": payload,
                "qos": qos,
                "retain": retain,
            })

    class Api:
        async def async_get_mqtt_credentials(self) -> dict[str, str]:
            return {MQTT_CREDENTIAL_USER_ID: "user1"}

    async def noop() -> None:
        return None

    await publish_mqtt_command(
        mqtt=Mqtt(),
        api=Api(),
        device_id="dev1",
        device_sn="sn1",
        bt_key=None,
        message_type=MQTT_MESSAGE_CONTROL_COMBINE,
        action_id=3027,
        cmd=121,
        body_fields={"workModel": 1},
        ensure_mqtt_cb=noop,
        relogin_cb=noop,
        stop_mqtt_cb=noop,
    )

    assert published["topic"] == "hb/app/user1/command"
    assert set(published["payload"]) == {
        "id",
        FIELD_VERSION,
        FIELD_MESSAGE_TYPE,
        FIELD_ACTION_ID,
        FIELD_TIMESTAMP,
        FIELD_BODY,
        FIELD_DEVICE_SN,
    }
    assert published["payload"][FIELD_DEVICE_SN] == "sn1"
    assert published["payload"][FIELD_MESSAGE_TYPE] == "ControlCombine"
    assert published["payload"][FIELD_ACTION_ID] == 3027
    assert json.loads(published["payload"][FIELD_BODY]) == {"workModel": 1, "cmd": 121}


def test_crypto_layer_a_login_request_uses_source_truth_algorithms() -> None:
    """Layer A: AES/ECB body and RSA-wrapped AES key constants are source-backed."""
    crypto = _read("Jackery_2.1.1_RE_Crypto_and_DTOs.md")
    assert "AES/ECB/PKCS5" in crypto
    assert "RSA/ECB/PKCS1" in crypto
    assert "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCV" in crypto

    plaintext = b'{"account":"u","password":"p"}'
    key = b"1234567890123456"
    ciphertext = _aes_ecb_encrypt(plaintext, key)
    assert ciphertext != plaintext
    assert len(ciphertext) % 16 == 0


def test_crypto_layer_b_mqtt_password_contract() -> None:
    """Layer B: password is AES-256-CBC(username, key=mqttPassWord, iv=key[:16])."""
    crypto = _read("Jackery_2.1.1_RE_Crypto_and_DTOs.md")
    assert "AES-256-CBC/PKCS5" in crypto
    assert "iv=key[:16]" in crypto

    api = AuthEndpointMixin.__new__(AuthEndpointMixin)
    api._mqtt_user_id = "12345"
    api._mqtt_mac_id = "2" + "a" * 32
    seed = b"0" * 32
    api._mqtt_seed_b64 = base64.b64encode(seed).decode("ascii")

    async def _ensure_token() -> None:
        return None

    api._ensure_token = _ensure_token
    creds = importlib.import_module(
        "custom_components.jackery_solarvault.client._endpoints.auth"
    )

    # Use the production method, then decrypt to the source-truth username.
    result = asyncio.run(api.async_get_mqtt_credentials())
    ciphertext = base64.b64decode(result[MQTT_CREDENTIAL_PASSWORD], validate=True)
    username = f"12345@{api._mqtt_mac_id}".encode()
    assert _decrypt_aes_cbc(ciphertext, seed, seed[:16]) == username
    assert result[MQTT_CREDENTIAL_CLIENT_ID] == "12345@APP"
    assert result[MQTT_CREDENTIAL_USERNAME] == username.decode()
    assert creds is not None


def test_crypto_layer_c_mqtt_body_contract() -> None:
    """Layer C: MQTT body is Base64(AES-128-CBC(JSON), key=iv=bluetoothKey)."""
    crypto = _read("Jackery_2.1.1_RE_Crypto_and_DTOs.md")
    assert "AES-128-CBC/PKCS7" in crypto
    assert "iv=K" in crypto
    assert "Base64-String" in crypto

    key = b"0123456789abcdef"
    body = {"cmd": 121, "workModel": 1}
    encrypted = encrypt_mqtt_body(body, key)
    ciphertext = base64.b64decode(encrypted, validate=True)
    assert json.loads(_decrypt_aes_cbc(ciphertext, key, key)) == body


def test_entity_field_candidates_source_truth_shape() -> None:
    """DTO/entity field candidates are pinned to the extracted JSON catalog."""
    catalog = json.loads(
        (SOURCE_ROOT / "jackery_entity_field_candidates_v2.json").read_text()
    )
    by_class = {entry["class"]: set(entry["fields"]) for entry in catalog}

    assert by_class["home/SystemBody"] >= {
        "soc",
        "batInPw",
        "batOutPw",
        "gridInPw",
        "workModel",
    }
    assert by_class["accessory/AccCTBody"] >= {"volt1", "curr1", "power1", "freq"}
    assert by_class["home/UserSystemListApi$Bean"] >= {
        "bluetoothKey",
        "deviceSn",
        "systemSn",
        "region",
    }
    assert by_class["DeviceDetailApi$DeviceInfo"] >= {
        "deviceSn",
        "modelName",
        "onlineStatus",
    }
