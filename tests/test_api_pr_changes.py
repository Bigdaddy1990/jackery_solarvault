"""Tests for the PR changes to client/api.py.

Covers:
- _rsa_pkcs1v15_encrypt: new isinstance guard that raises TypeError for non-RSA keys
- _generate_udid: output format (prefix + UUID-no-dashes)
- async_get_device_eps_stat: new EPS statistics endpoint
- async_get_today_energy: new today-energy KPI endpoint
"""

from typing import Any

import pytest

from custom_components.jackery_solarvault.client.api import _generate_udid
from custom_components.jackery_solarvault.client.api import _rsa_pkcs1v15_encrypt
from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import APP_REQUEST_META
from custom_components.jackery_solarvault.const import DATE_TYPE_DAY
from custom_components.jackery_solarvault.const import DEVICE_EPS_STAT_PATH
from custom_components.jackery_solarvault.const import DEVICE_TODAY_ENERGY_PATH
from custom_components.jackery_solarvault.const import FIELD_CODE
from custom_components.jackery_solarvault.const import FIELD_DATA
from custom_components.jackery_solarvault.const import FIELD_DEVICE_ID
from custom_components.jackery_solarvault.const import FIELD_DEVICE_SN
from custom_components.jackery_solarvault.const import MQTT_MAC_ID_PREFIX

# ---------------------------------------------------------------------------
# _rsa_pkcs1v15_encrypt — new isinstance guard
# ---------------------------------------------------------------------------


def _make_rsa_public_key_b64() -> str:
    """Return a minimal DER-encoded RSA-1024 public key as base64.

    Generated with:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        k = rsa.generate_private_key(65537, 1024)
        der = k.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        print(base64.b64encode(der).decode())
    The value below is a real 1024-bit RSA SubjectPublicKeyInfo DER blob.
    """
    return (
        "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCtW7ln1ZQNCL9P9Gju+5brZZ1R"
        "wyXwrLY8iFbe1QK9YpPn14ZI2+csvW6+Sbm5UAObHVmD6gY+usoY0+qGShKbo/Dk"
        "hVm6sdKzDNFn/+ytdt2V5Yd08/RjaSxYdwkNGidCb2fygELR+7gpgK4N8C2MMeL9"
        "JIj3v6tkhjR7h5rflQIDAQAB"
    )


def _make_ec_public_key_b64() -> str:
    """Return a DER-encoded P-256 EC public key as base64 (not RSA, so TypeError expected).

    Generated with:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        k = ec.generate_private_key(ec.SECP256R1())
        der = k.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        print(base64.b64encode(der).decode())
    """
    # Real P-256 SubjectPublicKeyInfo DER, base64-encoded.
    return (
        "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAExi9GR8jfqTcSd+4R753arn4NjqQh"
        "xJeQw+2G0fvo2SV7YIa/ZBDBj+6mMOmuYBa7VHEI82Nr3RcfO1oqHFJf8A=="
    )


def test_rsa_pkcs1v15_encrypt_accepts_valid_rsa_key() -> None:
    """A genuine RSA public key must encrypt without raising."""
    key_b64 = _make_rsa_public_key_b64()
    plaintext = b"test payload"
    result = _rsa_pkcs1v15_encrypt(plaintext, key_b64)
    assert isinstance(result, bytes)
    # RSA-1024 ciphertext is always 128 bytes.
    assert len(result) == 128


def test_rsa_pkcs1v15_encrypt_rejects_non_rsa_key_with_type_error() -> None:
    """A non-RSA (EC) key must raise TypeError with a descriptive message."""
    ec_b64 = _make_ec_public_key_b64()
    with pytest.raises(TypeError, match="RSA public key"):
        _rsa_pkcs1v15_encrypt(b"data", ec_b64)


def test_rsa_pkcs1v15_encrypt_error_message_names_actual_type() -> None:
    """The TypeError message should name the received key type."""
    ec_b64 = _make_ec_public_key_b64()
    with pytest.raises(TypeError) as exc_info:
        _rsa_pkcs1v15_encrypt(b"data", ec_b64)
    assert "RSAPublicKey" in str(exc_info.value) or "RSA" in str(exc_info.value)


def test_rsa_pkcs1v15_encrypt_rejects_invalid_base64() -> None:
    """Garbage base64 must raise before the key-type check.

    ``base64.b64decode`` raises ``binascii.Error`` for malformed input; the
    helper does not wrap it, so the test pins that specific exception type
    rather than the blind ``Exception`` (which Ruff B017 forbids).
    """
    import binascii

    with pytest.raises(binascii.Error):
        _rsa_pkcs1v15_encrypt(b"data", "not-valid-base64!!!")


# ---------------------------------------------------------------------------
# _generate_udid — output format verification
# ---------------------------------------------------------------------------


def test_generate_udid_has_correct_prefix() -> None:
    """Generated UDID must start with MQTT_MAC_ID_PREFIX ('2')."""
    result = _generate_udid("user@example.com")
    assert result.startswith(MQTT_MAC_ID_PREFIX)


def test_generate_udid_is_33_hex_chars() -> None:
    """Generated UDID must be exactly 33 lowercase hex characters (prefix + 32)."""
    result = _generate_udid("user@example.com")
    assert len(result) == 33
    assert result.isalnum()
    assert result == result.lower()


def test_generate_udid_contains_no_dashes() -> None:
    """Generated UDID must not contain UUID dashes."""
    result = _generate_udid("user@example.com")
    assert "-" not in result


def test_generate_udid_is_deterministic() -> None:
    """The same seed must always produce the same UDID."""
    seed = "stable-seed@example.com"
    assert _generate_udid(seed) == _generate_udid(seed)


def test_generate_udid_differs_for_different_seeds() -> None:
    """Different seeds must produce different UDIDs."""
    a = _generate_udid("user1@example.com")
    b = _generate_udid("user2@example.com")
    assert a != b


def test_generate_udid_handles_unicode_seed() -> None:
    """Non-ASCII seed characters must be encoded as UTF-8 without error."""
    result = _generate_udid("ユーザー@例.com")
    assert len(result) == 33
    assert result.startswith(MQTT_MAC_ID_PREFIX)


# ---------------------------------------------------------------------------
# async_get_device_eps_stat — new EPS statistics endpoint
# ---------------------------------------------------------------------------


async def test_async_get_device_eps_stat_calls_correct_path() -> None:
    """EPS stat helper must request DEVICE_EPS_STAT_PATH with the device id."""
    api = JackeryApi.__new__(JackeryApi)
    api.last_device_period_stat_responses = {}
    captured: dict[str, Any] = {}

    async def _get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
        captured["path"] = path
        captured["params"] = dict(params)
        return {FIELD_CODE: 0, FIELD_DATA: {"totalInEpsEnergy": "1.5"}}

    api._get_json = _get_json

    payload = await api.async_get_device_eps_stat(
        "dev1",
        date_type="day",
        begin_date="2026-05-27",
        end_date="2026-05-27",
    )

    assert captured["path"] == DEVICE_EPS_STAT_PATH
    assert captured["params"][FIELD_DEVICE_ID] == "dev1"
    assert "totalInEpsEnergy" in payload


async def test_async_get_device_eps_stat_stores_raw_response() -> None:
    """EPS stat response must be stored in last_device_period_stat_responses."""
    api = JackeryApi.__new__(JackeryApi)
    api.last_device_period_stat_responses = {}

    async def _get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
        return {FIELD_CODE: 0, FIELD_DATA: {"totalOutEpsEnergy": "0.5"}}

    api._get_json = _get_json

    await api.async_get_device_eps_stat(
        "dev1",
        date_type=DATE_TYPE_DAY,
        begin_date="2026-05-27",
        end_date="2026-05-27",
    )

    key = f"{DEVICE_EPS_STAT_PATH}:dev1:{DATE_TYPE_DAY}"
    assert key in api.last_device_period_stat_responses


async def test_async_get_device_eps_stat_returns_empty_dict_for_null_payload() -> None:
    """A null data payload must still return the APP_REQUEST_META dict."""
    api = JackeryApi.__new__(JackeryApi)
    api.last_device_period_stat_responses = {}

    async def _get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
        return {FIELD_CODE: 0, FIELD_DATA: None}

    api._get_json = _get_json

    payload = await api.async_get_device_eps_stat(
        "dev1",
        date_type=DATE_TYPE_DAY,
        begin_date="2026-05-27",
        end_date="2026-05-27",
    )

    # APP_REQUEST_META is always injected even for null payloads.
    assert APP_REQUEST_META in payload
    meta = payload[APP_REQUEST_META]
    assert meta["dateType"] == DATE_TYPE_DAY
    assert meta["beginDate"] == "2026-05-27"
    assert meta["endDate"] == "2026-05-27"


async def test_async_get_device_eps_stat_integer_device_id_is_stringified() -> None:
    """Integer device_id must be converted to string before the HTTP call."""
    api = JackeryApi.__new__(JackeryApi)
    api.last_device_period_stat_responses = {}
    captured: dict[str, Any] = {}

    async def _get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
        captured["params"] = dict(params)
        return {FIELD_CODE: 0, FIELD_DATA: None}

    api._get_json = _get_json

    await api.async_get_device_eps_stat(
        12345,
        date_type=DATE_TYPE_DAY,
        begin_date="2026-05-27",
        end_date="2026-05-27",
    )

    assert captured["params"][FIELD_DEVICE_ID] == "12345"


# ---------------------------------------------------------------------------
# async_get_today_energy — new today-energy KPI endpoint
# ---------------------------------------------------------------------------


async def test_async_get_today_energy_calls_correct_path_with_device_sn() -> None:
    """today_energy must call DEVICE_TODAY_ENERGY_PATH with deviceSn param."""
    api = JackeryApi.__new__(JackeryApi)
    captured: dict[str, Any] = {}
    expected_response = {FIELD_CODE: 0, FIELD_DATA: {"de": "0.5", "dg": "1.2"}}

    async def _get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
        captured["path"] = path
        captured["params"] = dict(params)
        return expected_response

    api._get_json = _get_json

    result = await api.async_get_today_energy("SN123456")

    assert captured["path"] == DEVICE_TODAY_ENERGY_PATH
    assert captured["params"][FIELD_DEVICE_SN] == "SN123456"
    # The raw JSON response is returned as-is (no _payload_dict wrapping).
    assert result == expected_response


async def test_async_get_today_energy_device_sn_is_stringified() -> None:
    """Numeric device_sn must be converted to string for the query parameter."""
    api = JackeryApi.__new__(JackeryApi)
    captured: dict[str, Any] = {}

    async def _get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
        captured["params"] = dict(params)
        return {}

    api._get_json = _get_json

    await api.async_get_today_energy("98765")  # type: ignore[arg-type]

    assert captured["params"][FIELD_DEVICE_SN] == "98765"


async def test_async_get_today_energy_returns_raw_response() -> None:
    """today_energy returns the full _get_json response, not just data field."""
    api = JackeryApi.__new__(JackeryApi)
    full_response = {
        FIELD_CODE: 0,
        FIELD_DATA: {"de": "2.1", "dg": "0.0", "dh": "3.5", "ds": "1.1"},
    }

    async def _get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
        return full_response

    api._get_json = _get_json

    result = await api.async_get_today_energy("HR2C12345")
    # Unlike _async_get_device_period_stat methods, this returns the full dict.
    assert result is full_response


async def test_async_get_today_energy_forwards_empty_response() -> None:
    """Empty response from _get_json is forwarded without modification."""
    api = JackeryApi.__new__(JackeryApi)

    async def _get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    api._get_json = _get_json

    result = await api.async_get_today_energy("SN000")
    assert result == {}


# ---------------------------------------------------------------------------
# Regression: eps stat does NOT include system_id in its request
# ---------------------------------------------------------------------------


async def test_async_get_device_eps_stat_does_not_include_system_id() -> None:
    """EPS stat takes no system_id parameter — it must not appear in params."""
    api = JackeryApi.__new__(JackeryApi)
    api.last_device_period_stat_responses = {}
    captured: dict[str, Any] = {}

    async def _get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
        captured["params"] = dict(params)
        return {FIELD_CODE: 0, FIELD_DATA: None}

    api._get_json = _get_json

    await api.async_get_device_eps_stat(
        "dev1",
        date_type=DATE_TYPE_DAY,
        begin_date="2026-05-01",
        end_date="2026-05-31",
    )

    # FIELD_SYSTEM_ID / "systemId" must not appear in params since the EPS method
    # does not accept a system_id argument.
    assert "systemId" not in captured["params"]
