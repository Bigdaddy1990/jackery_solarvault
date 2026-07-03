"""Async API client for the Jackery SolarVault cloud (iot.jackeryapp.com).

Endpoint paths and polling rules are mirrored from /docs/source-of-truth/

Domain-specific endpoint methods are organized into the integration's handlers
plus the client auth, ingest, and Shelly modules. This module composes them
into the unified ``JackeryApi`` facade.

Auth:     /v1/auth/login              (AES-128-ECB + RSA-1024 hybrid)
Systems:  /v1/device/system/list      (no params)
Device:   /v1/device/property         (?deviceId=<long>)
Alarms:   /v1/api/alarm               (?systemId=<long>)
Stats:    /v1/device/stat/systemStatistic     (?systemId=<long>)
Trends:   /v1/device/stat/sys/pv/trends       (?systemId&beginDate&endDate&dateType)
Price:    /v1/device/dynamic/powerPriceConfig (?systemId=<long>)
"""

import asyncio
import base64
import binascii
import hashlib
from http import HTTPStatus
import inspect
import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Final
import uuid

import aiohttp
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.serialization import load_der_public_key

from ..const import (  # noqa: RUF100, TID252
    ACCESSORIES_EXIST_PATH,
    ACCESSORIES_JACKERY_EXIST_PATH,
    ACCESSORIES_LIST_PATH,
    ACCESSORIES_NAME_PATH,
    ACCESSORIES_PATH,
    ACCESSORIES_SYNC_PATH,
    AES_KEY,
    ALARM_DETAIL_PATH,
    ALARM_PATH,
    ALERT_SYNC_PATH,
    APP_CHART_SERIES_Y,
    APP_CHART_SERIES_Y1,
    APP_CHART_SERIES_Y2,
    APP_CHART_SERIES_Y3,
    APP_CHART_SERIES_Y4,
    APP_CHART_SERIES_Y5,
    APP_CHART_SERIES_Y6,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_META,
    APP_VERSION,
    APP_VERSION_CODE,
    APP_VERSION_PATH,
    BANNER_LIST_PATH,
    BASE_URL,
    BATTERY_PACK_PATH,
    BATTERY_TRENDS_PATH,
    BIND_CURRENCY_PATH,
    BLE_OTA_LINK_PATH,
    BLE_OTA_VERSIONS_PATH,
    BOX_STAT_PATH,
    CANCEL_ACCOUNT_PATH,
    CANCEL_CONTRACT_PATH,
    CARBON_STAT_PATH,
    CHARGE_REPORT_PATH,
    CHECK_VERIFY_CODE_PATH,
    CODE_OK,
    CODE_TOKEN_EXPIRED,
    CONTRACT_LIST_PATH,
    CURRENCY_LIST_PATH,
    CUTOFF_STAT_PATH,
    DATE_TYPE_DAY,
    DEVICE_ACCEPT_BIND_PATH,
    DEVICE_BATTERY_STAT_PATH,
    DEVICE_BIND_PATH,
    DEVICE_CT_STAT_PATH,
    DEVICE_CURRENCY_PATH,
    DEVICE_EPS_STAT_PATH,
    DEVICE_HOME_STAT_PATH,
    DEVICE_LIST_PATH,
    DEVICE_METER_STAT_PATH,
    DEVICE_MODEL_HEADER,
    DEVICE_NICKNAME_PATH,
    DEVICE_PORTABLE_CT_STAT_PATH,
    DEVICE_PROPERTY_PATH,
    DEVICE_PV_STAT_PATH,
    DEVICE_QR_CODE_PATH,
    DEVICE_SHARED_LIST_PATH,
    DEVICE_SHARED_MANAGER_PATH,
    DEVICE_SHARED_REMOVE_ALL_PATH,
    DEVICE_SHARED_REMOVE_PATH,
    DEVICE_SOCKET_STATISTIC_PATH,
    DEVICE_SOCKET_STAT_PATH,
    DEVICE_STATISTIC_PATH,
    DEVICE_TODAY_ENERGY_PATH,
    DEVICE_UNBIND_PATH,
    DYNAMIC_PRICE_LOGIN_URL_PATH,
    DYNAMIC_PRICE_PATH,
    FAQ_ANSWER_PATH,
    FAQ_LIST_PATH,
    FEEDBACK_PATH,
    FIELD_ACCOUNT,
    FIELD_ACTION,
    FIELD_BATTERY_PACKS,
    FIELD_BAT_SOC,
    FIELD_BODY,
    FIELD_CELL_TEMP,
    FIELD_CODE,
    FIELD_COUNTRY_CODE,
    FIELD_CURRENCY,
    FIELD_CURRENT_VERSION,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEVICE_SN_LIST,
    FIELD_FUNCTION,
    FIELD_ID,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IS_FIRMWARE_UPGRADE,
    FIELD_LOGIN_TYPE,
    FIELD_MAC_ID,
    FIELD_MAX_POWER,
    FIELD_MODEL,
    FIELD_MQTT_PASSWORD,
    FIELD_MSG,
    FIELD_OP,
    FIELD_OUT_PW,
    FIELD_PASSWORD,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_RAW_TEXT,
    FIELD_RB,
    FIELD_REGION_CODE,
    FIELD_REGISTER_APP_ID,
    FIELD_SINGLE_PRICE,
    FIELD_SMART_SOCKET_ID,
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_NAME,
    FIELD_SYSTEM_REGION,
    FIELD_TARGET_MODULE_VERSION,
    FIELD_TARGET_VERSION,
    FIELD_TOKEN,
    FIELD_UPDATE_CONTENT,
    FIELD_UPDATE_STATUS,
    FIELD_UPGRADE_TYPE,
    FIELD_USER_ID,
    FIELD_VERSION,
    GCS_LIST_PATH,
    HOME_TRENDS_PATH,
    HTTP_CONTENT_TYPE_FORM,
    HTTP_CONTENT_TYPE_JSON,
    HTTP_HEADER_CONTENT_TYPE,
    HTTP_METHOD_GET,
    HTTP_METHOD_POST,
    HTTP_METHOD_PUT,
    HTTP_RAW_TEXT_LIMIT,
    HTTP_RETRY_ATTEMPTS,
    HTTP_RETRY_BACKOFF_SEC,
    INSTRUCTION_PATH,
    LOCATION_PATH,
    LOGIN_AES_KEY_LEN,
    LOGIN_AES_SEED_LEN,
    LOGIN_PATH,
    LOGIN_TIMEOUT_SEC,
    LOGOUT_PATH,
    MAX_POWER_SAVE_PATH,
    MODIFY_INFO_PATH,
    MQTT_CLIENT_ID_SUFFIX,
    MQTT_CREDENTIAL_CLIENT_ID,
    MQTT_CREDENTIAL_PASSWORD,
    MQTT_CREDENTIAL_USERNAME,
    MQTT_CREDENTIAL_USER_ID,
    MQTT_MAC_ID_PREFIX,
    MQTT_SESSION_MAC_ID,
    MQTT_SESSION_MAC_ID_SOURCE,
    MQTT_SESSION_SEED_B64,
    MQTT_SESSION_USER_ID,
    MQTT_USERNAME_SEPARATOR,
    NOTIFY_LIST_PATH,
    OFFLINE_STAT_PATH,
    OTA_LIST_PATH,
    OTA_UPDATE_PATH,
    PLATFORM_HEADER,
    POWER3_PATH,
    POWER_PRICE_PATH,
    PRICE_HISTORY_CONFIG_PATH,
    PRICE_SOURCE_LIST_PATH,
    PRIVACY_CHECK_PATH,
    PRIVACY_CONSENT_PATH,
    PROFIT_STAT_PATH,
    PUSH_CONFIG_GET_PATH,
    PUSH_CONFIG_SET_PATH,
    PV_NAME_PATH,
    PV_TRENDS_PATH,
    QUERY_TOU_PLAN_PATH,
    REDACTED_VALUE,
    REGISTER_APP_ID,
    REGISTER_PATH,
    REQUEST_TIMEOUT_SEC,
    RESET_PASSWORD_PATH,
    RSA_PUBLIC_KEY_B64,
    SAVE_CONTRACT_AUTH_PATH,
    SAVE_DYNAMIC_MODE_PATH,
    SAVE_LOCATION_ID_PATH,
    SAVE_SINGLE_MODE_PATH,
    SAVE_TOU_PLAN_PATH,
    SHELLY_AUTH_URL_PATH,
    SHELLY_BINDING_FAILURES_PATH,
    SHELLY_CONTROL_PATH,
    SHELLY_DEVICES_PATH,
    SHELLY_REALTIME_POWER_PATH,
    SHELLY_UNBIND_ACCOUNT_PATH,
    SHELLY_UNBIND_DEVICE_PATH,
    SLOW_ENDPOINT_TIMEOUT_SEC,
    SMART_MODE_CHECK_PATH,
    SMART_MODE_INFO_PATH,
    SMART_MODE_START_PATH,
    SMART_SCHEDULE_PATH,
    SOC_STAT_PATH,
    SUB_SHADOW_PATH,
    SYMMETRY_STAT_PATH,
    SYSTEM_CREATE_PATH,
    SYSTEM_DEVICE_NAME_PATH,
    SYSTEM_EXIST_PATH,
    SYSTEM_LIST_PATH,
    SYSTEM_NAME_PATH,
    SYSTEM_SHADOW_PATH,
    SYSTEM_STATISTIC_PATH,
    SYS_VERSION,
    UNREAD_COUNT_PATH,
    UPDATE_REGISTER_ID_PATH,
    UPLOAD_HEADIMG_PATH,
    USER_AGENT,
    USER_INFO_PATH,
    VERIFY_CODE_PATH,
    ZONE_LIST_PATH,
)
from ..util import (  # noqa: RUF100, TID252
    app_period_date_bounds,
    chart_series_debug,
    first_nonblank_int,
    safe_float,
)

_LOGGER = logging.getLogger(__name__)

# Expected byte length of the decoded MQTT password seed (AES-256 key material).
_MQTT_SEED_LEN: Final = 32

# Day-chart value series that carry per-bucket curve data (length-288 arrays).
# The x label series (APP_CHART_LABELS) and scalar totals are deliberately
# excluded — only these value series are coalesced when storing day responses.
_DAY_CHART_SERIES_KEYS: Final[tuple[str, ...]] = (
    APP_CHART_SERIES_Y,
    APP_CHART_SERIES_Y1,
    APP_CHART_SERIES_Y2,
    APP_CHART_SERIES_Y3,
    APP_CHART_SERIES_Y4,
    APP_CHART_SERIES_Y5,
    APP_CHART_SERIES_Y6,
)


# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------
type RandomBytesSource = Callable[[int], bytes]


def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _aes_cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def encrypt_mqtt_body(body: dict[str, Any], bluetooth_key: bytes) -> str:
    """Encrypt an MQTT command body with AES-128-CBC/PKCS7.

    The compact JSON body uses ``bluetooth_key`` as AES key and IV.

    Parameters:
        body (dict[str, Any]): Command body to serialize and encrypt.
        bluetooth_key (bytes): 16-byte Bluetooth key used as both AES key and IV.

    Returns:
        str: Base64-encoded ciphertext.

    Raises:
        ValueError: If `bluetooth_key` is not exactly 16 bytes.
    """
    if len(bluetooth_key) != 16:  # noqa: PLR2004
        msg = (
            "encrypt_mqtt_body: bluetoothKey must be "
            f"16 bytes, got {len(bluetooth_key)}"
        )
        raise ValueError(
            msg,
        )
    plaintext = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8",
    )  # noqa: E501, RUF100
    ciphertext = _aes_cbc_encrypt(plaintext, bluetooth_key, bluetooth_key)
    return base64.b64encode(ciphertext).decode("ascii")


def generate_login_aes_key(random_source: RandomBytesSource = os.urandom) -> bytes:
    """Return the app-compatible Layer A AES key bytes for one login request."""
    seed = random_source(LOGIN_AES_SEED_LEN)
    if len(seed) != LOGIN_AES_SEED_LEN:
        msg = f"Layer A login AES seed must be 16 bytes, got {len(seed)}"
        raise ValueError(
            msg,
        )
    return base64.b64encode(seed)


def build_login_crypto_fields(
    login_bean: dict[str, Any],
    *,
    aes_key: bytes | None = None,
    random_source: RandomBytesSource = os.urandom,
) -> dict[str, str]:
    """Build Jackery Layer A login form fields with an injectable AES key.

    Production calls omit ``aes_key`` so each login uses the app behavior from
    ``source-of-truth/jackery_auth.py``: 16 random bytes are Base64-encoded,
    then those ASCII bytes are used as the AES key and RSA-wrapped. Tests may
    inject ``aes_key`` or ``random_source`` for golden vectors.
    """
    login_aes_key = (
        aes_key if aes_key is not None else generate_login_aes_key(random_source)
    )
    if len(login_aes_key) != LOGIN_AES_KEY_LEN:
        msg = f"Layer A login AES key must be 24 bytes, got {len(login_aes_key)}"
        raise ValueError(
            msg,
        )
    plaintext = json.dumps(
        login_bean,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        "aesEncryptData": base64.b64encode(
            _aes_ecb_encrypt(plaintext, login_aes_key),
        ).decode("ascii"),
        "rsaForAesKey": base64.b64encode(
            _rsa_pkcs1v15_encrypt(login_aes_key, RSA_PUBLIC_KEY_B64),
        ).decode("ascii"),
    }


def _rsa_pkcs1v15_encrypt(data: bytes, public_key_b64: str) -> bytes:
    """Encrypt `data` using RSA PKCS#1 v1.5 with a base64-encoded DER RSA public key.

    Parameters:
        data (bytes): Plaintext bytes to encrypt.
        public_key_b64 (str): Base64-encoded DER representation of an RSA public key.

    Returns:
        bytes: Ciphertext produced by RSA PKCS#1 v1.5 encryption of `data`.

    Raises:
        TypeError: If the decoded public key is not an RSA public key.
    """
    der_bytes = base64.b64decode(public_key_b64)
    public_key = load_der_public_key(der_bytes)
    if not isinstance(public_key, rsa.RSAPublicKey):
        msg = (
            f"Jackery login expects an RSA public key, got {type(public_key).__name__}"
        )
        raise TypeError(msg)
    return public_key.encrypt(data, asym_padding.PKCS1v15())


def _generate_udid(seed: str) -> str:
    md5_digest = hashlib.md5(seed.encode("utf-8")).digest()
    u = uuid.UUID(bytes=md5_digest, version=3)
    return MQTT_MAC_ID_PREFIX + str(u).replace("-", "")


aes_cbc_encrypt = _aes_cbc_encrypt
aes_ecb_encrypt = _aes_ecb_encrypt
generate_udid = _generate_udid
rsa_pkcs1v15_encrypt = _rsa_pkcs1v15_encrypt


__all__ = [
    "LOGIN_AES_KEY_LEN",
    "LOGIN_AES_SEED_LEN",
    "RandomBytesSource",
    "_aes_cbc_encrypt",
    "_aes_ecb_encrypt",
    "_generate_udid",
    "_rsa_pkcs1v15_encrypt",
    "aes_cbc_encrypt",
    "aes_ecb_encrypt",
    "build_login_crypto_fields",
    "encrypt_mqtt_body",
    "generate_login_aes_key",
    "generate_udid",
    "rsa_pkcs1v15_encrypt",
]


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..coordinator import (  # noqa: RUF100, TID252
        JackerySolarVaultCoordinator,
    )


async def async_set_work_model(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    mode: int,
) -> None:
    """Set the work model through the characterized coordinator path."""
    await coordinator.async_set_work_model(device_id, mode)


async def async_reboot_device(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Reboot a device through the characterized coordinator path."""
    await coordinator.async_reboot_device(device_id)


def _data_field_accepted(data: dict[str, Any]) -> bool:
    """Return whether a Shelly write response's ``data`` field signals acceptance.

    The backend signals acceptance in the top-level ``data`` field as boolean
    ``True`` or a truthy token (``"true"``/``"1"``/``"ok"``, case-insensitive).
    Anything else — including a missing field — is treated as not accepted.
    """
    val = data.get(FIELD_DATA)
    if val is True:
        return True
    if isinstance(val, (str, int)):
        return str(val).lower() in {"true", "1", "ok"}
    return False


if TYPE_CHECKING:
    from ..types import MqttSessionSnapshot  # noqa: RUF100, TID252


class JackeryError(Exception):
    """Base exception."""


class JackeryAuthError(JackeryError):
    """Authentication failure."""


class JackeryApiError(JackeryError):
    """Generic API failure."""


def _write_accepted(data: dict[str, Any]) -> bool:
    """Determines whether a write response from the API should be treated as accepted.

    Parameters:
        data (dict): Parsed JSON response; inspected for the top-level `data` field.

    Returns:
        `True` if the response's `data` field is not explicitly `False`, `False`
        otherwise.
    """
    from ..util import safe_bool  # noqa: PLC0415, RUF100, TID252

    return safe_bool(data.get(FIELD_DATA)) is not False


write_accepted = _write_accepted


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class JackeryApi:  # noqa: PLR0904
    """Async client for the Jackery SolarVault cloud (full monolith)."""

    def __init__(  # noqa: PLR0913, PLR0917  # constructor takes distinct client-config values; a params object adds no clarity
        self,
        session: aiohttp.ClientSession,
        account: str,
        password: str,
        mqtt_mac_id: str | None = None,
        region_code: str | None = None,
        ca_path: str | None = None,
    ) -> None:
        """Initialise the Jackery API client.

        Parameters:
            session: aiohttp ClientSession used for HTTP requests.
            account: Account identifier used for authentication.
            password: Account password used for authentication.
            mqtt_mac_id: Optional preconfigured MQTT MAC identifier; if omitted a MAC
                will be generated.
            region_code: Optional region or country code; whitespace is stripped and
                the value is normalized to uppercase.
            ca_path: Optional path to the Jackery CA certificate file.
        """
        self._session = session
        self._account = account
        self._password = password
        self._region_code = (region_code or "").strip().upper() or None
        self._mqtt_mac_id_configured = mqtt_mac_id
        self._mqtt_mac_id_source = "generated"
        self._token: str | None = None
        self._lock = asyncio.Lock()
        self._mqtt_user_id: str | None = None
        self._mqtt_seed_b64: str | None = None
        self._mqtt_mac_id: str | None = None

        # Diagnostics buffers
        self.last_login_response: dict[str, Any] | None = None
        self.last_system_list_response: dict[str, Any] | None = None
        self.last_property_responses: dict[str, dict[str, Any]] = {}
        self.last_alarm_response: dict[str, Any] | None = None
        self.last_statistic_response: dict[str, Any] | None = None
        self.last_price_response: dict[str, Any] | None = None
        self.last_price_sources_response: dict[str, Any] | None = None
        self.last_price_history_config_response: dict[str, Any] | None = None
        self.last_device_statistic_responses: dict[str, dict[str, Any]] = {}
        self.last_device_period_stat_responses: dict[str, dict[str, Any]] = {}
        self.last_battery_pack_responses: dict[str, dict[str, Any]] = {}
        self.last_ota_responses: dict[str, dict[str, Any]] = {}
        self.last_location_responses: dict[str, dict[str, Any]] = {}
        self.payload_debug_callback: (
            Callable[
                [dict[str, Any] | Callable[[], dict[str, Any]]],
                Awaitable[None] | None,
            ]
            | None
        ) = None
        self.auth_rejection_callback: (
            Callable[[int, object], Awaitable[None] | None] | None
        ) = None
        # Transport counters for diagnostic sensors (reset on HA restart).
        self._requests_total = 0
        self._requests_failed = 0
        self._timeouts_total = 0
        self._auth_retries = 0

    @property
    def region_code(self) -> str | None:
        """The configured or learned region/country code (or None)."""
        return self._region_code

    def _maybe_learn_region_code(self, systems: list[dict[str, Any]]) -> None:
        """Learn region code from system metadata when not configured manually."""
        if self._region_code:
            return
        for item in systems:
            country = str(item.get(FIELD_COUNTRY_CODE) or "").strip().upper()
            if not country:
                continue
            self._region_code = country
            _LOGGER.debug(
                "Jackery: inferred regionCode=%s from /v1/device/system/list",
                country,
            )
            return

    # --- headers ------------------------------------------------------------
    def _headers(self, *, with_token: bool = False) -> dict[str, str]:
        """Builds HTTP headers emulating the Android client for API requests.

        Parameters:
            with_token (bool): If True and the client has an authentication
                token, include the auth token header.

        Returns:
            headers (dict[str, str]): Mapping of HTTP header names to values.
                Includes the auth token header when `with_token` is True and a
                token is present.
        """
        h = {
            "accept-encoding": "gzip",
            "accept-language": "de-DE",
            "app_version": APP_VERSION,
            "app_version_code": APP_VERSION_CODE,
            "connection": "Keep-Alive",
            "host": "iot.jackeryapp.com",
            FIELD_MODEL: DEVICE_MODEL_HEADER,
            "network": "wifi",
            "platform": PLATFORM_HEADER,
            "sys_version": SYS_VERSION,
            "user-agent": USER_AGENT,
        }
        if with_token and self._token:
            h[FIELD_TOKEN] = self._token
        return h

    # --- auth ---------------------------------------------------------------
    @staticmethod
    def _normalize_mqtt_mac_id(value: str) -> str:
        """Normalize and validate an app-style MQTT MAC identifier.

        Parameters:
            value (str): The input MAC identifier string to normalize.

        Returns:
            str: The normalized MAC identifier (lowercase, stripped).

        Raises:
            JackeryAuthError: If the normalized value does not match 33
                lowercase hexadecimal characters.
        """
        mac_id = value.strip().lower()
        # App values are 33 hex chars (prefix 2/9 + 32-char UUID-no-dash).
        if not re.fullmatch(r"[0-9a-f]{33}", mac_id):
            msg = (
                "Invalid mqtt_mac_id format. Expected 33 lowercase hex chars "
                "(example: 271c55f5731fa3d9ba1fe131e088946e0)."
            )
            raise JackeryAuthError(msg)
        return mac_id

    def _resolve_login_mac_id(self) -> str:
        """Resolve the MAC identifier used for login and MQTT username derivation.

        If a configured MQTT MAC ID is present and valid, that value is returned and
        `self._mqtt_mac_id_source` is set to `"configured"`. If the configured value is
        invalid, a deterministic MAC ID derived from the account is returned and
        `self._mqtt_mac_id_source` is set to `"generated_fallback_invalid_config"`.
        If no configured value is provided, a deterministic MAC ID derived from the
        account is returned and `self._mqtt_mac_id_source` is set to `"generated"`.

        Returns:
            str: The resolved MAC ID string.
        """
        configured = self._mqtt_mac_id_configured
        if configured:
            try:
                mac_id = self._normalize_mqtt_mac_id(configured)
            except JackeryAuthError as err:
                _LOGGER.warning(
                    "Ignoring invalid configured mqtt_mac_id (%s); "
                    "falling back to generated value",
                    err,
                )
                self._mqtt_mac_id_source = "generated_fallback_invalid_config"
            else:
                self._mqtt_mac_id_source = "configured"
                return mac_id
        # Fallback for headless environments without Android ID access.
        self._mqtt_mac_id_source = "generated"
        return _generate_udid(self._account)

    async def _post_login_request(
        self,
        url: str,
        form_body: dict[str, str],
        headers: dict[str, str],
    ) -> Any:  # noqa: ANN401  # decoded JSON is arbitrary; callers use dict .get accessors
        """POST the encrypted login form and return the decoded JSON response.

        Parameters:
            url (str): Fully-qualified login endpoint URL.
            form_body (dict[str, str]): Form-urlencoded login fields.
            headers (dict[str, str]): Request headers.

        Returns:
            Any: The decoded JSON response body.

        Raises:
            JackeryApiError: On non-OK HTTP status, invalid JSON, or transport failure.
        """
        try:
            async with self._session.post(
                url,
                data=form_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=LOGIN_TIMEOUT_SEC),
            ) as resp:
                return await self._decode_login_response(resp)
        except (TimeoutError, aiohttp.ClientError) as err:
            msg = f"Login request failed: {err}"
            raise JackeryApiError(msg) from err

    @staticmethod
    async def _decode_login_response(resp: aiohttp.ClientResponse) -> Any:  # noqa: ANN401  # decoded JSON is arbitrary; callers use dict .get accessors
        """Validate the login HTTP response and return its decoded JSON body.

        Parameters:
            resp (aiohttp.ClientResponse): The open login response.

        Returns:
            Any: The decoded JSON response body.

        Raises:
            JackeryApiError: On non-OK HTTP status or invalid JSON.
        """
        if resp.status != HTTPStatus.OK:
            msg = f"Login HTTP {resp.status}"
            raise JackeryApiError(msg)
        try:
            return await resp.json(content_type=None)
        except (
            aiohttp.ContentTypeError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValueError,
        ) as err:
            raw = (await resp.text())[:HTTP_RAW_TEXT_LIMIT]
            msg = f"Login returned invalid JSON: {raw!r}"
            raise JackeryApiError(msg) from err

    async def async_login(self) -> str:
        """Perform the encrypted login flow and store session and MQTT credentials.

        This sends the encrypted login form to the backend, validates the
        response code, and stores the returned JWT token and MQTT-related
        fields on the client instance.

        Returns:
            token (str): The JWT session token returned by the server.
        """
        mac_id = self._resolve_login_mac_id()
        login_bean = {
            FIELD_ACCOUNT: self._account,
            FIELD_LOGIN_TYPE: 2,
            FIELD_MAC_ID: mac_id,
            FIELD_PASSWORD: self._password,
            FIELD_REGISTER_APP_ID: REGISTER_APP_ID,
        }
        if self._region_code:
            login_bean[FIELD_REGION_CODE] = self._region_code

        plaintext = json.dumps(login_bean, ensure_ascii=False).encode("utf-8")
        aes_blob = base64.b64encode(_aes_ecb_encrypt(plaintext, AES_KEY)).decode(
            "ascii"
        )
        rsa_blob = base64.b64encode(
            _rsa_pkcs1v15_encrypt(AES_KEY, RSA_PUBLIC_KEY_B64)
        ).decode("ascii")

        url = f"{BASE_URL}{LOGIN_PATH}"

        # The Android app sends login params as form-urlencoded body, not as
        # query string. This matches the captured traffic byte-for-byte.
        headers = self._headers()
        headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_FORM
        form_body = {"aesEncryptData": aes_blob, "rsaForAesKey": rsa_blob}

        data = await self._post_login_request(url, form_body, headers)

        if self._extract_code(data) != CODE_OK:
            msg = (
                f"Login rejected (code={data.get(FIELD_CODE)}, "
                f"msg={data.get(FIELD_MSG)})"
            )
            raise JackeryAuthError(msg)

        token = data.get(FIELD_TOKEN) or ""
        if not token:
            msg = "Login succeeded but no token returned"
            raise JackeryAuthError(msg)

        raw_payload = data.get(FIELD_DATA)
        if raw_payload is not None and not isinstance(raw_payload, dict):
            msg = f"Login returned data {type(raw_payload).__name__} instead of object"
            raise JackeryApiError(msg)
        payload = raw_payload or {}

        # Store redacted version for diagnostics (only on successful login)
        redacted = dict(data)
        if FIELD_TOKEN in redacted:
            redacted[FIELD_TOKEN] = REDACTED_VALUE
        if isinstance(redacted.get(FIELD_DATA), dict):
            inner = dict(redacted[FIELD_DATA])
            if FIELD_MQTT_PASSWORD in inner:
                inner[FIELD_MQTT_PASSWORD] = REDACTED_VALUE
            redacted[FIELD_DATA] = inner
        self.last_login_response = redacted
        await self._emit_payload_debug(
            self._http_payload_debug(
                method=HTTP_METHOD_POST,
                path=LOGIN_PATH,
                body={"form_fields": sorted(form_body)},
                status=200,
                response=redacted,
            )
        )

        self._token = token
        self._mqtt_user_id = str(payload.get(FIELD_USER_ID) or "") or None
        self._mqtt_seed_b64 = payload.get(FIELD_MQTT_PASSWORD) or None
        self._mqtt_mac_id = mac_id
        return token

    async def async_get_mqtt_credentials(self) -> dict[str, str]:
        """Return MQTT credentials for the active REST login session.

        Runtime-verified app algorithm from MQTT_PROTOCOL.md::

            clientId = f"{userId}@APP"
            username = f"{userId}@{macId}"
            seed = base64_decode(mqttPassWord)  # 32 bytes
            key = seed  # AES-256 key
            iv = seed[:16]
            password = base64(AES - 256 - CBC - PKCS5(username_utf8, key, iv))
            # userId: MQTT user id from the login response.

        Raises:
            JackeryAuthError: If the client is not logged in or required MQTT
                fields are missing, if `mqttPassWord` is not valid base64, or
                if the decoded seed is not 32 bytes.
        """
        await self._ensure_token()
        if not self._mqtt_user_id or not self._mqtt_seed_b64 or not self._mqtt_mac_id:
            msg = "Login response missing MQTT fields (userId/mqttPassWord/macId)"
            raise JackeryAuthError(msg)

        try:
            seed = base64.b64decode(self._mqtt_seed_b64, validate=True)
        except (binascii.Error, ValueError) as err:
            msg = "Invalid mqttPassWord base64 in login response"
            raise JackeryAuthError(msg) from err
        if len(seed) != _MQTT_SEED_LEN:
            msg = (
                f"Unexpected mqttPassWord decoded length: {len(seed)} "
                f"(expected {_MQTT_SEED_LEN})"
            )
            raise JackeryAuthError(msg)

        client_id = (
            f"{self._mqtt_user_id}{MQTT_USERNAME_SEPARATOR}{MQTT_CLIENT_ID_SUFFIX}"
        )
        username = f"{self._mqtt_user_id}{MQTT_USERNAME_SEPARATOR}{self._mqtt_mac_id}"
        encrypted = _aes_cbc_encrypt(
            username.encode("utf-8"),
            key=seed,
            iv=seed[:16],
        )
        password = base64.b64encode(encrypted).decode("ascii")
        return {
            MQTT_CREDENTIAL_CLIENT_ID: client_id,
            MQTT_CREDENTIAL_USERNAME: username,
            MQTT_CREDENTIAL_PASSWORD: password,
            MQTT_CREDENTIAL_USER_ID: self._mqtt_user_id,
        }

    @property
    def mqtt_fingerprint(self) -> tuple[str | None, str | None, str | None]:
        """Tuple that changes whenever a new login session rotates MQTT seed."""
        return (self._mqtt_user_id, self._mqtt_mac_id, self._mqtt_seed_b64)

    @property
    def mqtt_mac_id_source(self) -> str:
        """The source identifier for the current MQTT MAC ID (login vs cached)."""
        return self._mqtt_mac_id_source

    @property
    def mqtt_mac_id(self) -> str | None:
        """The MAC ID assigned to this MQTT session by login."""
        return self._mqtt_mac_id

    async def _ensure_token(self) -> str:
        if self._token is None:
            async with self._lock:
                if self._token is None:
                    await self.async_login()
        if self._token is None:
            msg = "Login succeeded without returning a token"
            raise JackeryAuthError(msg)
        return self._token

    @staticmethod
    def _extract_code(data: object) -> int | None:
        """Extract the backend numeric `code` value from a parsed API response.

        Parameters:
            data (dict | Any): Parsed JSON response (expected dict) or any
                other value.

        Returns:
            int | None: The `code` parsed as an integer when present as an
                integer or a numeric string, `None` otherwise.
        """
        if not isinstance(data, dict):
            return None
        return first_nonblank_int(data.get(FIELD_CODE))

    def _is_token_expired_response(self, status: int, data: object) -> bool:
        """Determine whether the parsed API response signals token expiry.

        Only dict-shaped responses are inspected; non-dict responses always
        return False.

        Returns:
            True if the response indicates token expiration, False otherwise.
        """
        if not isinstance(data, dict):
            return False
        code = self._extract_code(data)
        if code == CODE_TOKEN_EXPIRED:
            return True
        msg = str(data.get(FIELD_MSG) or "").lower()
        return "token expires" in msg or "token expired" in msg

    @staticmethod
    def _response_has_auth_failure_text(data: object) -> bool:
        """Return True when a backend error payload looks authorization-related."""
        if not isinstance(data, dict):
            return False
        parts = [
            data.get(FIELD_MSG),
            data.get("message"),
            data.get("error"),
            data.get(FIELD_RAW_TEXT),
        ]
        text = " ".join(str(part) for part in parts if part not in {None, ""}).lower()
        if not text:
            return False
        return any(
            marker in text
            for marker in (
                "unauthorized",
                "unauthorised",
                "not authorized",
                "not authorised",
                "forbidden",
                "invalid token",
                "token invalid",
                "token expires",
                "token expired",
                "login",
                "log in",
                "please login",
                "please log in",
                "auth",
                "authentication",
                "authorization",
                "credential",
            )
        )

    def _is_auth_failure_response(self, status: int, data: object) -> bool:
        """Classify HTTP/API authorization failures for HA reauth handling."""
        if status in {401, 403}:
            return True
        if self._is_token_expired_response(status, data):
            return True
        if status != HTTPStatus.OK:
            return self._response_has_auth_failure_text(data)
        code = self._extract_code(data)
        return code not in {CODE_OK, None} and self._response_has_auth_failure_text(
            data
        )

    @staticmethod
    def _auth_failure_message(method: str, path: str, status: int, data: object) -> str:
        """Build a compact auth-failure message without exposing secrets."""
        if isinstance(data, dict):
            code = data.get(FIELD_CODE)
            msg = data.get(FIELD_MSG) or data.get("message") or data.get("error")
        else:
            code = None
            msg = data
        return (
            f"{method} {path} authorization failed: HTTP {status} code={code} msg={msg}"
        )

    async def _emit_payload_debug(
        self,
        event_or_factory: dict[str, Any] | Callable[[], dict[str, Any]],
    ) -> None:
        """Forward one raw/parsed payload debug event to the coordinator.  # noqa: E226.

        Accepts either a pre-built event dict or a zero-arg callable that  # noqa: E226
        returns one. The callable form is forwarded as-is to the  # noqa: E225, E275
        coordinator, which itself only invokes it when the dedicated
        ``payload_debug`` logger is at DEBUG level — saving the
        ``redacted`` walk on hot paths when DEBUG is disabled.
        """
        callback = self.payload_debug_callback
        if callback is None:
            return
        try:
            result = callback(event_or_factory)
            if inspect.isawaitable(result):
                await result
        except Exception as err:  # noqa: BLE001  # best-effort debug logging must never break the API path
            _LOGGER.debug("Jackery payload debug logging failed: %s", err)

    @staticmethod
    def _http_payload_debug(  # noqa: PLR0913  # keyword-only builder for distinct debug-event fields
        *,
        method: str,
        path: str,
        params: dict | None = None,
        body: dict | None = None,
        status: int | None = None,
        response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a redacted-later HTTP payload debug event."""
        payload = response.get(FIELD_DATA) if isinstance(response, dict) else None
        event: dict[str, Any] = {
            "kind": "http",
            "method": method,
            "path": path,
            "params": params or {},
            "request_body": body or {},
            "status": status,
            "response": response or {},
            "response_data_type": type(payload).__name__,
        }
        series_debug = chart_series_debug(payload)
        if series_debug:
            event["chart_series_debug"] = series_debug
        return event

    @staticmethod
    def _coalesced_day_stat_copy(
        data: dict[str, Any], date_type: str
    ) -> dict[str, Any]:
        """Return a copy of a day-stat response with null y-buckets set to 0.0.

        Only the stored diagnostics copy is coalesced; the original ``data``
        (and the payload returned to the coordinator) is left untouched, so the
        live day-curve recorder import (``day_power_energy_points`` trailing
        zero-fill) is unaffected. Only day chart value series (``y``,
        ``y1``..``y6``) are coalesced; labels (``x``), scalar totals, request
        meta and week/month/year responses pass through. A ``null``/unparseable
        bucket becomes ``0.0`` (matching the parsed-path default for missing
        buckets); real values, including negatives, are preserved. ``data`` is
        returned unchanged when there is nothing to coalesce.

        Args:
            data: Parsed ``{code,msg,data}`` response envelope.
            date_type: Period granularity; only ``DATE_TYPE_DAY`` is coalesced.

        Returns:
            A shallow copy with coalesced day series, or ``data`` unchanged.
        """
        if date_type != DATE_TYPE_DAY:
            return data
        section = data.get(FIELD_DATA)
        if not isinstance(section, dict):
            return data
        coalesced_series: dict[str, list[Any]] = {}
        for series_key in _DAY_CHART_SERIES_KEYS:
            series = section.get(series_key)
            if not isinstance(series, list):
                continue
            coalesced = [0.0 if safe_float(raw) is None else raw for raw in series]
            if coalesced != series:
                coalesced_series[series_key] = coalesced
        if not coalesced_series:
            return data
        return {**data, FIELD_DATA: {**section, **coalesced_series}}

    @staticmethod
    def _payload_dict(data: dict[str, Any], path: str) -> dict[str, Any]:
        """Return a dict payload or an empty dict with one diagnostic warning.

        Several Jackery endpoints return `{code,msg,data}` but backend variants
        sometimes change `data` to null, list or string. Returning those shapes
        to the coordinator would break payload merging and sensor creation.
        """
        payload = data.get(FIELD_DATA)
        if isinstance(payload, dict):
            return payload
        if payload is None:
            return {}
        _LOGGER.warning(
            "Jackery %s returned unexpected data shape for dict payload: %s",
            path,
            type(payload).__name__,
        )
        return {}

    @staticmethod
    def _payload_list(data: dict[str, Any], path: str) -> list[dict[str, Any]]:
        """Extract the list payload from a parsed API response.

        If the response's FIELD_DATA is a list, returns only the elements that
        are dictionary objects. If FIELD_DATA is None, returns an empty list.
        For any other shape, logs a warning and returns an empty list.

        Parameters:
            data (dict): Parsed JSON response expected to contain FIELD_DATA.
            path (str): Request path used in warning messages when the payload
                shape is unexpected.

        Returns:
            list: The items from FIELD_DATA when it is a list (filtered to dict
                items), or an empty list otherwise.
        """
        payload = data.get(FIELD_DATA)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if payload is None:
            return []
        _LOGGER.warning(
            "Jackery %s returned unexpected data shape for list payload: %s",
            path,
            type(payload).__name__,
        )
        return []

    # --- generic GET with auto re-login ------------------------------------

    async def async_check_verification_code(
        self,
        *,
        email: str,
        method: str = "email",
        phone: str | None = None,
    ) -> dict[str, Any]:
        """Check a verification code for the given email.

        Parameters:
            email: Account email address.
            method: ``"email"`` or ``"sms"``.
            phone: Phone number (required when method is ``"sms"``).

        Returns:
            dict: Backend response data.
        """
        payload = {
            "email": email,
            "method": method,
        }
        if phone:
            payload["phone"] = phone
        return await self._post_json(CHECK_VERIFY_CODE_PATH, payload)

    async def async_reset_password(
        self,
        *,
        email: str,
        password: str,
        confirm_password: str,
        verification_code: str,
    ) -> dict[str, Any]:
        """Reset account password using a verification code.  # noqa: E501.

        Parameters:
            email: Account email address.
            password: New password.
            confirm_password: Confirmation of the new password.
            verification_code: Email verification code.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            RESET_PASSWORD_PATH,
            {
                "email": email,
                "password": password,
                "confirmPassword": confirm_password,
                "verificationCode": verification_code,
            },
        )

    async def async_upload_headimg(self, image: str) -> dict[str, Any]:
        """Upload a profile image (base64-encoded).  # noqa: E211, E226.

        Parameters:
            image: Base64-encoded image data.  # noqa: E226

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(UPLOAD_HEADIMG_PATH, {"image": image})

    async def async_cancel_account(
        self,
        *,
        email: str,
        verification_code: str,
    ) -> dict[str, Any]:
        """Cancel the authenticated user's account using a verification code.

        Returns:
            dict: Backend response payload.
        """
        return await self._post_json(
            CANCEL_ACCOUNT_PATH,
            {"email": email, "verificationCode": verification_code},
        )

    async def async_logout(self) -> dict[str, Any]:
        """POST /v1/auth/loginOut — invalidate the current session token.

        Mirrors ``LoginOutApi`` (source-of-truth ``auth/loginOut``), which takes
        no request body — the backend identifies the session from the bearer
        token on the request. API-surface completeness only: Home Assistant runs
        on a long-lived dedicated account and never triggers logout during normal
        operation, so this wrapper is provided for parity, not UI-wired.

        Returns:
            dict: Backend response payload (typically empty on success).
        """
        data = await self._post_json(LOGOUT_PATH, {})
        return self._payload_dict(data, LOGOUT_PATH)

    async def async_register(
        self,
        *,
        email: str,
        password: str,
        verification_code: str,
        region_code: str | None = None,
    ) -> dict[str, Any]:
        """POST /v1/auth/register — create a new Jackery cloud account.

        Mirrors ``RegisterAccountApi`` (source-of-truth ``auth/register``) with
        request fields ``email``, ``password``, ``regionCode``,
        ``registerAppId`` and ``verificationCode``. ``registerAppId`` is fixed to
        the app identifier; ``regionCode`` falls back to this client's learned
        region when not supplied. API-surface completeness only: Home Assistant
        uses an existing account, so this wrapper is provided for parity with the
        source-of-truth catalog and is not UI-wired.

        Parameters:
            email: Account email address.
            password: Account password.
            verification_code: Email verification code from
                :meth:`async_send_verification_code`.
            region_code: Optional region/country code; falls back to the client's
                configured region when omitted.

        Returns:
            dict: Backend response payload.
        """
        params = {
            "email": email,
            "password": password,
            "verificationCode": verification_code,
            FIELD_REGISTER_APP_ID: REGISTER_APP_ID,
        }
        resolved_region = region_code or self._region_code
        if resolved_region:
            params[FIELD_REGION_CODE] = resolved_region
        data = await self._post_json(REGISTER_PATH, params)
        return self._payload_dict(data, REGISTER_PATH)

    async def async_send_verification_code(
        self,
        *,
        email: str,
        method: str = "email",
        phone: str | None = None,
    ) -> dict[str, Any]:
        """GET /v1/auth/verificationCode — request a verification code.

        Mirrors ``GetVerificationCodeApi`` (source-of-truth
        ``auth/verificationCode``) with request fields ``email``, ``method`` and
        ``phone``. This is the code-issuing counterpart to
        :meth:`async_check_verification_code`. API-surface completeness only:
        Home Assistant uses an existing account, so this wrapper is provided for
        parity with the source-of-truth catalog and is not UI-wired.

        Parameters:
            email: Account email address.
            method: ``"email"`` or ``"sms"``.
            phone: Phone number (required when method is ``"sms"``).

        Returns:
            dict: Backend response payload.
        """
        params = {"email": email, "method": method}
        if phone:
            params["phone"] = phone
        data = await self._get_json(VERIFY_CODE_PATH, params=params)
        return self._payload_dict(data, VERIFY_CODE_PATH)

    # --- confirmed endpoints -----------------------------------------------
    async def async_get_system_list(self) -> list[dict[str, Any]]:
        """GET /v1/device/system/list — all systems + their devices.  # noqa: E226.

        Response shape (verified):  # noqa: E211
            {"code":0, "data":[{  # noqa: E231
                "id": <systemId>, "systemName": "SolarVault",  # noqa: E225
                "deviceName": "SolarVault 3 Pro Max",
                "countryCode": "DE", "currency": "€", "timezone": "Europe/Berlin",
                "gridStandard": "103", "onlineState": 1, "bindKey": 1,
                "devices":[{"deviceId": <long>, "deviceSn": "...",  # noqa: E225, E231
                            "devModel": "HTH...", "modelCode": 3002,
                            FIELD_RB: <SOC>, "isCloud": false, ...}],  # noqa: E225
                ...
            }]}

        Returns:
            list[dict]: System objects returned by the backend (each typically
            includes the account's devices, region/currency metadata, etc).
        """
        data = await self._get_json(SYSTEM_LIST_PATH)
        self.last_system_list_response = data
        systems = self._payload_list(data, SYSTEM_LIST_PATH)
        self._maybe_learn_region_code(systems)
        return systems

    async def async_get_device_property(self, device_id: str | int) -> dict:
        """GET /v1/device/property — device + properties dict.

        Parameters:
            device_id (str | int): Device identifier; it will be converted to a string
            for the request.

        Returns:
            dict: Device properties dictionary extracted from the response; an empty
            dict if the response payload is missing or not a dict.
        """
        data = await self._get_json(
            DEVICE_PROPERTY_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_property_responses[str(device_id)] = data
        return self._payload_dict(data, DEVICE_PROPERTY_PATH)

    async def async_get_alarm(self, system_id: str | int) -> Any:  # noqa: ANN401  # parsed JSON response, indexed by callers
        """GET /v1/api/alarm — alarm list for a system."""
        data = await self._get_json(
            ALARM_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        self.last_alarm_response = data
        return data.get(FIELD_DATA)

    async def async_get_system_statistic(self, system_id: str | int) -> dict:
        """GET /v1/device/stat/systemStatistic — today/total KPIs.

        Response keys (verified):
            todayLoad, todayBatteryDisChg, todayBatteryChg, todayGeneration,
            totalGeneration, totalRevenue, totalCarbon, isSetPrice
        """
        data = await self._get_json(
            SYSTEM_STATISTIC_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        self.last_statistic_response = data
        return self._payload_dict(data, SYSTEM_STATISTIC_PATH)

    async def async_get_pv_trends(
        self,
        system_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """GET /v1/device/stat/sys/pv/trends — historical curves."""
        begin_date, end_date = app_period_date_bounds(
            date_type, begin_date=begin_date, end_date=end_date
        )
        params = {
            FIELD_SYSTEM_ID: str(system_id),
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        data = await self._get_json(PV_TRENDS_PATH, params=params)
        payload = self._payload_dict(data, PV_TRENDS_PATH)
        if payload:
            payload.setdefault(
                APP_REQUEST_META,
                {k: v for k, v in params.items() if k != FIELD_SYSTEM_ID},
            )
        return payload

    async def async_get_power_price(self, system_id: str | int) -> dict:
        """GET /v1/device/dynamic/powerPriceConfig — tariff config."""
        data = await self._get_json(
            POWER_PRICE_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        self.last_price_response = data
        return self._payload_dict(data, POWER_PRICE_PATH)

    async def async_get_price_sources(
        self, system_id: str | int
    ) -> list[dict[str, Any]]:
        """GET /v1/device/dynamic/priceCompany — dynamic-price providers.

        App decompile (ElePriceSourceListApi):
            path: device/dynamic/priceCompany
            params: systemId
            item fields: platformCompanyId, cid, country, companyName, loginAllowed
        """
        data = await self._get_json(
            PRICE_SOURCE_LIST_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        self.last_price_sources_response = data
        return self._payload_list(data, PRICE_SOURCE_LIST_PATH)

    async def async_get_price_history_config(
        self, system_id: str | int
    ) -> dict[str, Any]:
        """Retrieve the price history configuration for the specified system.

        Stores the raw parsed API response in self.last_price_history_config_response.

        Returns:
            dict: The response `data` payload as a dict; empty dict if the payload is missing or not a dict.
        """  # noqa: E501
        data = await self._get_json(
            PRICE_HISTORY_CONFIG_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        self.last_price_history_config_response = data
        return self._payload_dict(data, PRICE_HISTORY_CONFIG_PATH)

    # --- Additional app-statistic endpoints from PROTOCOL.md §2 ----------
    async def async_get_device_statistic(self, device_id: str | int) -> dict:
        """Retrieve current-day energy flow statistics for the specified device.

        The returned dictionary maps metric keys (strings) to their values as numeric strings representing kilowatt-hours (kWh). Typical keys include: `pvEgy`, `inEpsEgy`, `ongridOtBatEgy`, `pvOtBatEgy`, `inOngridEgy`, `outOngridEgy`, `batOtGridEgy`, `outEpsEgy`, `batDisChgEgy`, `acOtBatEgy`, `batOtAcEgy`, and `batChgEgy`. Keys present may vary by device and backend response.

        Returns:
            dict: Mapping of statistic keys to their values as strings in kWh.
        """  # noqa: E501
        data = await self._get_json(
            DEVICE_STATISTIC_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_device_statistic_responses[str(device_id)] = data
        return self._payload_dict(data, DEVICE_STATISTIC_PATH)

    async def _async_get_device_period_stat(  # noqa: PLR0913  # keyword-only query params for a period-stat endpoint
        self,
        path: str,
        *,
        device_id: str | int,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
        system_id: str | int | None = None,
    ) -> dict[str, Any]:
        """Fetch period-based chart data for a specific device and date range.

        The returned value is the endpoint's `data` object normalized to a dict. If absent, an empty dict is returned. An `APP_REQUEST_META` entry is added (when missing) containing the request parameters used to fetch the data, excluding `deviceId` and `systemId`, so callers can correlate the payload with the requested period.

        Parameters:
            path (str): Endpoint path to query.
            device_id (str | int): Device identifier to request data for.
            date_type (str): Period granularity (e.g., day, month, year). `begin_date`/`end_date` are computed if omitted.
            begin_date (str | None): Start date for the period (computed if None).
            end_date (str | None): End date for the period (computed if None).
            system_id (str | int | None): Optional system identifier included in the request.

        Returns:
            dict[str, Any]: Normalized payload dict from the endpoint's `data` field, augmented with `APP_REQUEST_META`.
        """  # noqa: E501
        # PROTOCOL.md §2: Periodenabfragen use explicit full ranges.
        # month/year with today..today can return day-like partial totals.
        begin_date, end_date = app_period_date_bounds(
            date_type, begin_date=begin_date, end_date=end_date
        )
        params: dict[str, str] = {
            FIELD_DEVICE_ID: str(device_id),
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        if system_id is not None:
            params[FIELD_SYSTEM_ID] = str(system_id)
        data = await self._get_json(path, params=params)
        stored = data
        if isinstance(data, dict):
            data[APP_REQUEST_META] = {"path": path, "params": dict(params)}
            stored = self._coalesced_day_stat_copy(data, date_type)
        period_cache_key = f"{path}:{device_id}:{date_type}"
        self.last_device_period_stat_responses[period_cache_key] = stored
        payload = self._payload_dict(data, path)
        payload.setdefault(
            APP_REQUEST_META,
            {
                k: v
                for k, v in params.items()
                if k not in {FIELD_DEVICE_ID, FIELD_SYSTEM_ID}
            },
        )
        return payload

    async def async_get_device_pv_stat(
        self,
        device_id: str | int,
        system_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve photovoltaic (PV) statistics for a single device within a system.

        Parameters:
            device_id (str | int): Device identifier.
            system_id (str | int): System identifier that the device belongs to.
            date_type (str): Period granularity (e.g., day, month); defaults to DATE_TYPE_DAY.
            begin_date (str | None): Inclusive start date for the period (format depends on API); when omitted the API's default period bounds are used.
            end_date (str | None): Inclusive end date for the period (format depends on API); when omitted the API's default period bounds are used.

        Returns:
            dict: Parsed response payload from the endpoint, typically containing chart series and related metadata.
        """  # noqa: E501
        return await self._async_get_device_period_stat(
            DEVICE_PV_STAT_PATH,
            device_id=device_id,
            system_id=system_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_device_battery_stat(
        self,
        device_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """GET /v1/device/stat/battery — app battery statistics for one device."""
        return await self._async_get_device_period_stat(
            DEVICE_BATTERY_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_device_home_stat(
        self,
        device_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Fetches on-grid (home) statistics for the given device over the specified date range.

        Returns:
            payload (dict): Normalized response payload containing chart/statistics data. When available, includes `APP_REQUEST_META` with request metadata (excluding `deviceId`).
        """  # noqa: E501
        return await self._async_get_device_period_stat(
            DEVICE_HOME_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_device_ct_stat(
        self,
        device_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve CT (smart-meter) statistics for a device.

        Parameters:
                device_id (str | int): Device identifier passed as `deviceId` to the API.
                date_type (str): Period type for the chart (e.g., day, month); defaults to DATE_TYPE_DAY.
                begin_date (str | None): Optional start date for the period (ISO-like string).
                end_date (str | None): Optional end date for the period (ISO-like string).

        Returns:
                dict[str, Any]: Parsed payload dictionary containing CT/smart-meter statistics. The payload may include request metadata (APP_REQUEST_META) when a date range was provided.
        """  # noqa: E501
        return await self._async_get_device_period_stat(
            DEVICE_CT_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_device_meter_stat(
        self,
        device_id: str | int,
    ) -> dict[str, Any]:
        """GET /v1/device/stat/meter — app Smart-Meter panel totals.

        The Android app calls this with the Smart-Meter/CT accessory deviceId,
        not the SolarVault main deviceId.
        """
        """Retrieve the Smart-Meter (CT accessory) panel totals for the given device.

        Parameters:
            device_id: Smart-Meter / CT accessory deviceId (not the SolarVault main deviceId).

        Returns:
            A dictionary containing the parsed payload with the meter panel totals from the device meter statistics endpoint.
        """  # noqa: E501
        data = await self._get_json(
            DEVICE_METER_STAT_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_device_period_stat_responses[
            f"{DEVICE_METER_STAT_PATH}:{device_id}:panel"
        ] = data
        return self._payload_dict(data, DEVICE_METER_STAT_PATH)

    async def async_get_battery_pack_list(self, device_sn: str) -> list[dict[str, Any]]:
        """Get a normalized list of battery pack dictionaries for the given device serial number.

        The raw parsed API response is saved to self.last_battery_pack_responses[device_sn]. Handles multiple backend response shapes and returns an empty list when no pack data is found.

        Parameters:
            device_sn (str): Device serial number to query.

        Returns:
            list[dict]: Battery pack dictionaries extracted from the response; empty list if no packs are found or the response shape is unrecognized.
        """  # noqa: E501
        """GET /v1/device/battery/pack/list — sub-battery pack status.

        App decompile (BatteryPackApi + BatteryPackSub):
            request: deviceSn
            fields: batSoc, cellTemp, inPw, outPw, version, isFirmwareUpgrade
        """
        params = {FIELD_DEVICE_SN: str(device_sn)}
        data = await self._get_json(BATTERY_PACK_PATH, params=params)
        if isinstance(data, dict):
            data[APP_REQUEST_META] = {
                "path": BATTERY_PACK_PATH,
                "params": dict(params),
            }
        self.last_battery_pack_responses[str(device_sn)] = data
        raw = data.get(FIELD_DATA)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            raw_body = raw.get(FIELD_BODY)
            candidates = [
                raw.get(FIELD_BATTERY_PACKS),
                raw_body if isinstance(raw_body, list) else None,
                raw_body.get(FIELD_BATTERY_PACKS)
                if isinstance(raw_body, dict)
                else None,
            ]
            for candidate in candidates:
                if isinstance(candidate, list):
                    return [item for item in candidate if isinstance(item, dict)]
            # Some API variants return a single body object directly.
            if any(
                key in raw
                for key in (
                    FIELD_BAT_SOC,
                    FIELD_CELL_TEMP,
                    FIELD_IN_PW,
                    FIELD_OUT_PW,
                    FIELD_RB,
                    FIELD_IP,
                    FIELD_OP,
                    FIELD_VERSION,
                    FIELD_CURRENT_VERSION,
                    FIELD_IS_FIRMWARE_UPGRADE,
                    FIELD_UPDATE_STATUS,
                )
            ):
                return [raw]
        if raw is not None:
            _LOGGER.warning(
                "Jackery %s returned unexpected data shape for battery packs: %s",
                BATTERY_PACK_PATH,
                type(raw).__name__,
            )
        return []

    async def async_get_ota_info(self, device_sn: str) -> dict:
        """GET /v1/device/ota/list — firmware version + available updates."""
        """Retrieve OTA information for the device identified by `device_sn`.

        Normalizes multiple backend response shapes and selects the matching OTA item when available.

        Returns:
            dict: OTA information object for the device, or an empty dict if no suitable item is found.
        """  # noqa: E501
        data = await self._get_json(
            OTA_LIST_PATH, params={FIELD_DEVICE_SN_LIST: device_sn}
        )
        self.last_ota_responses[device_sn] = data
        raw = data.get(FIELD_DATA)
        if isinstance(raw, list):
            items = self._payload_list(data, OTA_LIST_PATH)
            if items:
                return self._select_ota_item(items, device_sn)
        if isinstance(raw, dict):
            raw_body = raw.get(FIELD_BODY)
            if isinstance(raw_body, list):
                body_items = [item for item in raw_body if isinstance(item, dict)]
                selected = self._select_ota_item(body_items, device_sn)
                if selected:
                    return selected
            candidates: list[Any] = [
                raw_body if isinstance(raw_body, dict) else None,
                raw,
            ]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                if any(
                    key in candidate
                    for key in (
                        FIELD_CURRENT_VERSION,
                        FIELD_VERSION,
                        FIELD_TARGET_VERSION,
                        FIELD_TARGET_MODULE_VERSION,
                        FIELD_UPDATE_STATUS,
                        FIELD_UPDATE_CONTENT,
                        FIELD_IS_FIRMWARE_UPGRADE,
                        FIELD_UPGRADE_TYPE,
                    )
                ):
                    return candidate
        items = self._payload_list(data, OTA_LIST_PATH)
        if items:
            return self._select_ota_item(items, device_sn)
        return {}

    async def async_get_location(self, device_id: str | int) -> dict:
        """GET /v1/device/location — GPS coordinates set by the user.

        Returns:
            dict: The API payload's `data` object containing location fields
            (e.g., `latitude`, `longitude`); an empty dict if `data` is missing
            or not a dict.
        """
        data = await self._get_json(
            LOCATION_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_location_responses[str(device_id)] = data
        return self._payload_dict(data, LOCATION_PATH)

    # --- HTTP write endpoints documented in APP_POLLING_MQTT.md --------------
    async def async_bind_device(
        self,
        *,
        bind_key: str,
        dev_id: str | int,
        guid: str,
        timezone_offset: int,
    ) -> dict[str, Any]:
        """POST /v1/device/bind — bind a device to the account.

        Known limitation (own-hardware onboarding, RE-blocked): binding a
        brand-new *unprovisioned* unit requires ``bind_key`` and ``guid``, which
        the vendor app derives from a Nordic BLE Wi-Fi-provisioning exchange.
        That provisioning protocol — the GATT service/characteristic UUIDs, the
        ``WRITE_WIFI_INFO``/``READ_WIFI_LIST`` payload schema, and the origin of
        ``guid`` plus its bootstrap crypto — is absent from source-of-truth and
        only observable with a physical unprovisioned device and a capture rig
        (nRF Connect GATT dump + Frida). Until those captures exist there is no
        HA config-flow onboarding for a factory-fresh unit: users pair once in
        the Jackery app and HA then discovers the bound device. This method is
        wired and correct; it is simply unreachable without ``guid``. See the
        deferred BLE-provisioning epic in the pairing workstream plan.
        """
        return await self._post_json(
            DEVICE_BIND_PATH,
            {
                "bindKey": bind_key,
                "devId": dev_id,
                "guid": guid,
                "timezoneOffset": timezone_offset,
            },
        )

    async def async_unbind_device(self, device_id: str | int) -> dict[str, Any]:
        """Unbind a device from the account.

        Parameters:
            device_id (str | int): Identifier of the device to unbind.

        Returns:
            dict[str, Any]: Backend response data.
        """
        return await self._post_json(DEVICE_UNBIND_PATH, {"deviceId": str(device_id)})

    async def async_set_device_nickname(
        self,
        device_id: str | int,
        nickname: str,
    ) -> dict[str, Any]:
        """Set a custom nickname for a device.

        Parameters:
            device_id: Device identifier.
            nickname: Display name for the device.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            DEVICE_NICKNAME_PATH,
            {"deviceId": str(device_id), "nickname": nickname},
        )

    async def async_accept_shared_device(
        self,
        *,
        dev_id: str,
        qr_code_id: str,
    ) -> dict[str, Any]:
        """Accept a shared device invitation.

        Parameters:
            dev_id: Device identifier from the sharing invitation.
            qr_code_id: QR code identifier from the invitation.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            DEVICE_ACCEPT_BIND_PATH,
            {"devId": dev_id, "qrCodeId": qr_code_id},
        )

    async def async_get_device_shared_list(self) -> list[dict[str, Any]]:
        """Return the list of devices shared with the current account.

        Returns:
            list[dict[str, Any]]: Shared device entries as extracted from the backend
            payload.
        """
        data = await self._get_json(DEVICE_SHARED_LIST_PATH)
        return self._payload_list(data, DEVICE_SHARED_LIST_PATH)

    async def async_get_qr_code(self) -> dict[str, Any]:
        """Return the share QR code for the current account.

        Returns:
            dict[str, Any]: QR code payload (``qrCodeId``, ``userId``) extracted from
            the backend response.
        """
        data = await self._get_json(DEVICE_QR_CODE_PATH)
        return self._payload_dict(data, DEVICE_QR_CODE_PATH)

    async def async_get_device_shared_managers(
        self,
        *,
        bind_user_id: str,
        level: int = 0,
    ) -> list[dict[str, Any]]:
        """Return the list of managers for a shared device binding.

        Parameters:
            bind_user_id (str): User ID that owns the binding.
            level (int): Share level filter; only managers at this level are returned.

        Returns:
            list[dict[str, Any]]: List of manager entries as dictionaries.
        """
        data = await self._get_json(
            DEVICE_SHARED_MANAGER_PATH,
            {"bindUserId": bind_user_id, "level": level},
        )
        return self._payload_list(data, DEVICE_SHARED_MANAGER_PATH)

    async def async_remove_shared_access(
        self,
        *,
        bind_user_id: str,
        device_id: str | int,
    ) -> dict[str, Any]:
        """Remove a single shared device access.

        Parameters:
            bind_user_id: User ID whose access is being removed.
            device_id: Device identifier.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            DEVICE_SHARED_REMOVE_PATH,
            {"bindUserId": bind_user_id, "deviceId": str(device_id)},
        )

    async def async_remove_all_shared_access(
        self,
        *,
        bind_user_id: str,
        level: int = 0,
    ) -> dict[str, Any]:
        """Remove all shared access entries for a user at the specified share level.

        Parameters:
            bind_user_id (str): ID of the user whose shared access entries will be
            removed.
            level (int): Share level to remove (defaults to 0).

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            DEVICE_SHARED_REMOVE_ALL_PATH,
            {"bindUserId": bind_user_id, "level": level},
        )

    async def async_check_system_bound(
        self,
        *,
        bind_key: str,
        device_sn: str,
        guid: str,
    ) -> dict[str, Any]:
        """Determine whether a system identified by the provided bind key, serial.

        number, and GUID is already bound.

        Parameters:
            bind_key (str): Device bind key.
            device_sn (str): Device serial number.
            guid (str): Device GUID.

        Returns:
            dict: Backend response data from the system existence endpoint.
        """
        return await self._get_json(
            SYSTEM_EXIST_PATH,
            {"bindKey": bind_key, "deviceSn": device_sn, "guid": guid},
        )

    async def async_create_system(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        """Create or configure a system using backend-provided parameters.

        Parameters:
            **kwargs: Arbitrary keyword arguments forwarded directly to the backend API
            as the system creation/configuration payload.

        Returns:
            dict[str, Any]: The backend response data.
        """
        return await self._post_json(SYSTEM_CREATE_PATH, kwargs)

    async def async_modify_device_name(
        self,
        *,
        device_name: str,
        id: str | int,
    ) -> dict[str, Any]:
        """Set the device's display name.

        Parameters:
            device_name (str): New device name.
            id (str | int): Device identifier; converted to string for the request.

        Returns:
            dict[str, Any]: Response data from the backend.
        """
        return await self._post_json(
            SYSTEM_DEVICE_NAME_PATH,
            {"deviceName": device_name, "id": str(id)},
        )

    async def async_modify_pv_name(
        self,
        *,
        device_sn: str,
        index: int,
        name: str,
    ) -> dict[str, Any]:
        """Rename a PV input.

        Parameters:
            device_sn: Device serial number.
            index: PV input index (0-based).
            name: New PV name.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            PV_NAME_PATH,
            {"deviceSn": device_sn, "index": index, "name": name},
        )

    async def async_get_ble_ota_link(
        self,
        *,
        device_sn: str,
        sub_device_sn: str,
        target_firmware_ids: str,
        target_version_id: str,
    ) -> dict[str, Any]:
        """Query BLE OTA link for a sub-device.

        Parameters:
            device_sn: Parent device serial number.
            sub_device_sn: Sub-device serial number.
            target_firmware_ids: Target firmware IDs.
            target_version_id: Target version ID.

        Returns:
            dict: Backend response data.
        """
        return await self._get_json(
            BLE_OTA_LINK_PATH,
            {
                "deviceSn": device_sn,
                "subDeviceSn": sub_device_sn,
                "targetFirmwareIds": target_firmware_ids,
                "targetVersionId": target_version_id,
            },
        )

    async def async_get_ble_ota_versions(self, version_list: str) -> dict[str, Any]:
        """Retrieve available BLE OTA versions for the specified version list.

        Parameters:
            version_list (str): Version list query parameter as a raw string.

        Returns:
            dict[str, Any]: Backend response data containing OTA version information.
        """
        return await self._post_json(BLE_OTA_VERSIONS_PATH, {"list": version_list})

    async def async_start_ota_update(
        self,
        *,
        device_sn: str,
        sub_device_sn: str,
        target_firmware_ids: str,
        target_version_id: str,
    ) -> dict[str, Any]:
        """Initiates an OTA firmware update for a device or its sub-device.

        Parameters:
            device_sn (str): Device serial number.
            sub_device_sn (str): Sub-device serial number; use an empty string for the
            main device.
            target_firmware_ids (str): Comma-separated target firmware IDs or
            identifier accepted by the backend.
            target_version_id (str): Target firmware version ID.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            OTA_UPDATE_PATH,
            {
                "deviceSn": device_sn,
                "subDeviceSn": sub_device_sn,
                "targetFirmwareIds": target_firmware_ids,
                "targetVersionId": target_version_id,
            },
        )

    async def async_get_charge_report(
        self,
        *,
        device_sn: str,
        page_index: int = 1,
    ) -> dict[str, Any]:
        """Fetch charge report history for a device.

        Parameters:
            device_sn: Device serial number.
            page_index: Page number, starting at 1.

        Returns:
            dict: Charge report payload for the requested page, or an empty dict if no
            payload is present.
        """
        data = await self._get_json(
            CHARGE_REPORT_PATH,
            {"deviceSn": device_sn, "pageIndex": page_index},
        )
        return self._payload_dict(data, CHARGE_REPORT_PATH)

    async def async_get_device_eps_stat(
        self,
        device_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve EPS (off-grid) energy input/output statistics for a device over a.

        specified period.

        Parameters:
            device_id (str | int): Device identifier (id or serial) to query.
            date_type (str): Period granularity (e.g., "day"); defaults to
            DATE_TYPE_DAY.
            begin_date (str | None): Optional ISO date string for period start.
            end_date (str | None): Optional ISO date string for period end.

        Returns:
            dict: Parsed backend payload containing aggregates (e.g.,
            `totalInEpsEnergy`, `totalOutEpsEnergy`) and time-series arrays (`x`, `y`,
            `y1`, `y2`); may include an `APP_REQUEST_META` dict with request parameters.
        """
        return await self._async_get_device_period_stat(
            DEVICE_EPS_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_today_energy(self, device_sn: str) -> dict[str, Any]:
        """Retrieve today's compact energy KPIs for a device.

        Parameters:
            device_sn (str): Device serial number; sent as the `deviceSn` query
            parameter.

        Returns:
            dict: Parsed JSON response containing KPI fields such as `de` (feed-in),
            `dg` (grid import), `dh` (home load), and `ds` (battery energy).
        """
        data = await self._get_json(
            DEVICE_TODAY_ENERGY_PATH,
            params={FIELD_DEVICE_SN: str(device_sn)},
        )
        return self._payload_dict(data, DEVICE_TODAY_ENERGY_PATH)

    async def async_get_portable_ct_stat(
        self,
        device_id: str | int,
    ) -> dict[str, Any]:
        """GET /v1/device/stat/ct/statics — portable device CT phase totals.

        Parameters:
            device_id (str | int): Portable device identifier.

        Returns:
            dict: Parsed payload with phase totals (l1, l2, total).
        """
        data = await self._get_json(
            DEVICE_PORTABLE_CT_STAT_PATH,
            params={FIELD_DEVICE_ID: str(device_id)},
        )
        return self._payload_dict(data, DEVICE_PORTABLE_CT_STAT_PATH)

    async def async_get_device_socket_statistic(
        self,
        smart_socket_id: str | int,
    ) -> dict[str, Any]:
        """Get socket panel totals for the specified smart socket.

        Returns:
            The response `data` payload as a dict; an empty dict if the payload is
            missing or not a dict.
        """
        data = await self._get_json(
            DEVICE_SOCKET_STATISTIC_PATH,
            params={FIELD_SMART_SOCKET_ID: str(smart_socket_id)},
        )
        self.last_device_period_stat_responses[
            f"{DEVICE_SOCKET_STATISTIC_PATH}:{smart_socket_id}:panel"
        ] = data
        return self._payload_dict(data, DEVICE_SOCKET_STATISTIC_PATH)

    async def async_get_device_socket_stat(
        self,
        device_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve socket-chart statistics for a device over a specified period.

        If the returned payload is non-empty, it will include `APP_REQUEST_META`
        containing the request parameters (`dateType`, `beginDate`, `endDate`) used to
        produce the chart (excluding `deviceId`/`systemId`).

        Returns:
            dict: The normalized `data` payload for the device socket chart.
        """
        return await self._async_get_device_period_stat(
            DEVICE_SOCKET_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_home_trends(
        self,
        system_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve the home energy consumption breakdown for a system over a specified.

        period.

        Parameters:
            system_id (str | int): Identifier of the system to query.
            date_type (str): Period granularity (e.g., day, month).
            begin_date (str | None): Start date for the period (optional).
            end_date (str | None): End date for the period (optional).

        Returns:
            dict: Normalized payload containing the home consumption breakdown; may be
            empty if no data is available.
        """
        begin_date, end_date = app_period_date_bounds(
            date_type,
            begin_date=begin_date,
            end_date=end_date,
        )
        params = {
            FIELD_SYSTEM_ID: str(system_id),
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        data = await self._get_json(
            HOME_TRENDS_PATH,
            params=params,
            request_timeout=SLOW_ENDPOINT_TIMEOUT_SEC,
        )
        payload = self._payload_dict(data, HOME_TRENDS_PATH)
        if payload:
            payload.setdefault(
                APP_REQUEST_META,
                {k: v for k, v in params.items() if k != FIELD_SYSTEM_ID},
            )
        return payload

    async def async_get_battery_trends(
        self,
        system_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """GET /v1/device/stat/sys/battery/trends — battery charge/discharge history.

        If the returned payload is non-empty, attaches request metadata under
        `APP_REQUEST_META` containing the request's `dateType`, `beginDate`, and
        `endDate`.

        Returns:
            dict: Normalized payload dictionary extracted from the API response (may be
            empty).
        """
        begin_date, end_date = app_period_date_bounds(
            date_type,
            begin_date=begin_date,
            end_date=end_date,
        )
        params = {
            FIELD_SYSTEM_ID: str(system_id),
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        data = await self._get_json(
            BATTERY_TRENDS_PATH,
            params=params,
            request_timeout=SLOW_ENDPOINT_TIMEOUT_SEC,
        )
        payload = self._payload_dict(data, BATTERY_TRENDS_PATH)
        if payload:
            payload.setdefault(
                APP_REQUEST_META,
                {k: v for k, v in params.items() if k != FIELD_SYSTEM_ID},
            )
        return payload

    async def _async_get_period_stat(
        self,
        path: str,
        *,
        device_sn: str,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Fetch period-based chart data for a device keyed by serial number.

        Sibling of :meth:`_async_get_device_period_stat` for endpoints that key
        on ``deviceSn`` instead of ``deviceId``.
        """
        begin_date, end_date = app_period_date_bounds(
            date_type, begin_date=begin_date, end_date=end_date
        )
        params: dict[str, str] = {
            FIELD_DEVICE_SN: str(device_sn),
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        data = await self._get_json(path, params=params)
        payload = self._payload_dict(data, path)
        payload.setdefault(
            APP_REQUEST_META,
            {k: v for k, v in params.items() if k != FIELD_DEVICE_SN},
        )
        return payload

    async def async_get_symmetry_stat(
        self,
        *,
        device_sn: str,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve charge and discharge symmetry statistics for a device.

        Parameters:
            device_sn (str): Device serial number.
            date_type (str): Period granularity (e.g., "day").
            begin_date (str | None): Start date for the period; computed if None.
            end_date (str | None): End date for the period; computed if None.

        Returns:
            dict[str, Any]: Normalized symmetry statistics payload.
        """
        begin, end = app_period_date_bounds(
            date_type,
            begin_date=begin_date,
            end_date=end_date,
        )
        data = await self._get_json(
            SYMMETRY_STAT_PATH,
            params={
                APP_REQUEST_BEGIN_DATE: str(begin),
                APP_REQUEST_DATE_TYPE: date_type,
                FIELD_DEVICE_SN: str(device_sn),
                APP_REQUEST_END_DATE: str(end),
                "negative": "1",
                "positive": "1",
            },
        )
        return self._payload_dict(data, SYMMETRY_STAT_PATH)

    async def async_get_cutoff_stat(
        self,
        *,
        device_sn: str,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve cutoff (power outage) statistics for a device over a period.

        Parameters:
            device_sn (str): Device serial number.
            begin_date (str | None): Start date for the period; computed if None.
            end_date (str | None): End date for the period; computed if None.

        Returns:
            dict[str, Any]: Dictionary containing cutoff statistics for the requested
            period.
        """
        return await self._async_get_period_stat(
            CUTOFF_STAT_PATH,
            device_sn=device_sn,
            date_type="day",
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_soc_stat(
        self,
        *,
        device_id: str | int,
    ) -> dict[str, Any]:
        """Fetches state-of-charge (SOC) statistics for a device.

        Parameters:
            device_id (str | int): Device identifier.

        Returns:
            dict[str, Any]: Normalized SOC statistics payload.
        """
        data = await self._get_json(
            SOC_STAT_PATH,
            params={FIELD_DEVICE_ID: str(device_id)},
        )
        return self._payload_dict(data, SOC_STAT_PATH)

    async def async_get_carbon_stat(
        self,
        *,
        device_sn: str,
    ) -> dict[str, Any]:
        """Retrieve carbon offset statistics for the specified device.

        Parameters:
            device_sn (str): Device serial number.

        Returns:
            dict[str, Any]: Normalized carbon statistics payload.
        """
        data = await self._get_json(
            CARBON_STAT_PATH,
            params={FIELD_DEVICE_SN: str(device_sn)},
        )
        return self._payload_dict(data, CARBON_STAT_PATH)

    async def async_get_profit_stat(
        self,
        *,
        device_id: str | int,
    ) -> dict[str, Any]:
        """Retrieve profit and revenue statistics for a device.

        Parameters:
            device_id (str | int): Device identifier.

        Returns:
            dict[str, Any]: Profit statistics payload.
        """
        data = await self._get_json(
            PROFIT_STAT_PATH,
            params={FIELD_DEVICE_ID: str(device_id)},
        )
        return self._payload_dict(data, PROFIT_STAT_PATH)

    async def async_get_box_stat(
        self,
        *,
        device_sn: str,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
        key: str = "",
    ) -> dict[str, Any]:
        """Fetch box electricity statistics for a device.

        ``device/stat`` is the app's generic ``key``-selector electricity
        endpoint; its ``total``/``unit``/``x``/``y`` payload duplicates the typed
        per-metric period stats (pv/battery/home/eps/ct) already surfaced as
        sensors, so it is intentionally NOT wired as an entity — retained and
        covered by an API contract test for source-of-truth completeness.

        Parameters:
            device_sn (str): Device serial number.
            date_type (str): Period granularity (e.g., "day", "month").
            begin_date (str | None): Start date; computed if None.
            end_date (str | None): End date; computed if None.
            key (str): Optional stat key filter; included in the request only if
            non-empty.

        Returns:
            dict[str, Any]: Normalized payload containing the box statistics.
        """
        begin_date, end_date = app_period_date_bounds(
            date_type,
            begin_date=begin_date,
            end_date=end_date,
        )
        params: dict[str, str] = {
            "deviceSn": device_sn,
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        if key:
            params["key"] = key
        data = await self._get_json(BOX_STAT_PATH, params=params)
        return self._payload_dict(data, BOX_STAT_PATH)

    async def async_get_smart_schedule_prediction(
        self,
        *,
        system_id: str | int,
    ) -> dict[str, Any]:
        """Retrieve the AI smart schedule prediction for the specified system.

        Parameters:
            system_id: Identifier of the system to retrieve the prediction for.

        Returns:
            dict: Smart schedule prediction payload returned by the backend.
        """
        data = await self._get_json(
            SMART_SCHEDULE_PATH,
            params={FIELD_SYSTEM_ID: str(system_id)},
        )
        return self._payload_dict(data, SMART_SCHEDULE_PATH)

    async def async_set_single_mode(
        self,
        *,
        system_id: str | int,
        single_price: float | str,
        currency: str,
    ) -> bool:
        """Set the system's fixed electricity price used when the system is configured.

        for single (fixed) pricing.

        Parameters:
            system_id (str | int): Identifier of the system to configure.
            single_price (float | str): Price value greater than or equal to 0; will be
            formatted to at most four decimal places before sending.
            currency (str): Non-empty currency code or label.

        Returns:
            `true` if the backend indicates the change was accepted, `false` otherwise.

        Raises:
            JackeryAuthError: When the response indicates an authorization or authentication failure.
            JackeryApiError: On network/request failures, non-200 HTTP status, or backend `code` that is neither `CODE_OK` nor `None`.
        """  # noqa: E501
        try:
            price = float(single_price)
        except ValueError as err:
            msg = "single_price must be a valid number"
            raise JackeryApiError(
                msg,
            ) from err
        except TypeError as err:
            msg = "single_price must be a valid number"
            raise JackeryApiError(
                msg,
            ) from err
        if not (price >= 0):
            msg = "single_price must be >= 0"
            raise JackeryApiError(msg)
        cur = str(currency or "").strip()
        if not cur:
            msg = "currency must be a non-empty string"
            raise JackeryApiError(msg)
        # Keep stable decimal formatting for backend parsing.
        price_text = f"{price:.4f}".rstrip("0").rstrip(".")
        data = await self._post_form(
            SAVE_SINGLE_MODE_PATH,
            {
                FIELD_SYSTEM_ID: str(system_id),
                FIELD_SINGLE_PRICE: price_text,
                FIELD_CURRENCY: cur,
            },
        )
        return write_accepted(data)

    async def async_set_dynamic_mode(
        self,
        *,
        system_id: str | int,
        platform_company_id: int | str,
        system_region: str,
    ) -> bool:
        """Enable or update dynamic pricing mode for a system.

        Parameters:
            system_id: Identifier of the target system.
            platform_company_id: Platform company identifier required by the API; must
            be an integer-valued number.
            system_region: Region code for the system; must be a non-empty string.

        Returns:
            `true` if the change was accepted by the server, `false` otherwise.

        Raises:
            JackeryApiError: If `platform_company_id` is not an integer-valued number
            or if `system_region` is empty.
        """
        company_id = first_nonblank_int(platform_company_id)
        if company_id is None:
            msg = "platform_company_id must be an integer"
            raise JackeryApiError(msg)
        region = str(system_region or "").strip()
        if not region:
            msg = "system_region must be a non-empty string"
            raise JackeryApiError(msg)
        data = await self._post_form(
            SAVE_DYNAMIC_MODE_PATH,
            {
                FIELD_SYSTEM_ID: str(system_id),
                FIELD_PLATFORM_COMPANY_ID: company_id,
                FIELD_SYSTEM_REGION: region,
            },
        )
        return write_accepted(data)

    async def async_get_dynamic_price_login_url(
        self,
        *,
        platform_company_id: int,
        system_id: str | int,
    ) -> dict[str, Any]:
        """Return the login URL payload for the dynamic pricing platform.

        Returns:
            dict: Payload containing the login URL and related metadata.
        """
        data = await self._get_json(
            DYNAMIC_PRICE_LOGIN_URL_PATH,
            {"platformCompanyId": platform_company_id, "systemId": str(system_id)},
        )
        return self._payload_dict(data, DYNAMIC_PRICE_LOGIN_URL_PATH)

    async def async_get_device_currency(self, device_id: str | int) -> dict[str, Any]:
        """Get the currency configuration for a device.

        Returns:
            dict[str, Any]: The currency configuration payload for the device.
        """
        data = await self._get_json(
            DEVICE_CURRENCY_PATH,
            params={FIELD_DEVICE_ID: str(device_id)},
        )
        return self._payload_dict(data, DEVICE_CURRENCY_PATH)

    async def async_save_contract_auth(
        self,
        *,
        contract_id: str,
        custom_id: str,
        platform_company_id: int,
        system_id: str | int,
    ) -> dict[str, Any]:
        """Save a contract authorization record for dynamic pricing.

        Parameters:
            contract_id (str): Identifier of the contract to save.
            custom_id (str): Client/custom identifier associated with the contract.
            platform_company_id (int): Platform company identifier used by the
            dynamic-pricing service.
            system_id (str | int): System identifier; will be converted to a string in
            the request payload.

        Returns:
            dict[str, Any]: The JSON response returned by the API.
        """
        return await self._post_json(
            SAVE_CONTRACT_AUTH_PATH,
            {
                "contractId": contract_id,
                "customId": custom_id,
                "platformCompanyId": platform_company_id,
                "systemId": str(system_id),
            },
        )

    async def async_get_contract_list(
        self,
        *,
        customer_number: str,
        platform_company_id: int,
    ) -> list[dict[str, Any]]:
        """Retrieve the list of available dynamic-pricing contracts for a customer.

        Parameters:
            customer_number (str): Customer number used to query contracts.
            platform_company_id (int): Platform company identifier that scopes the
            contract list.

        Returns:
            list[dict[str, Any]]: Contract objects extracted from the response payload.
        """
        data = await self._get_json(
            CONTRACT_LIST_PATH,
            {
                "customerNumber": customer_number,
                "platformCompanyId": platform_company_id,
            },
        )
        return self._payload_list(data, CONTRACT_LIST_PATH)

    async def async_cancel_contract_auth(
        self,
        *,
        platform_company_id: int,
        system_id: str | int,
    ) -> dict[str, Any]:
        """Cancel an existing contract authorization for the given platform company and.

        system.

        Parameters:
            platform_company_id (int): Identifier of the platform company owning the
            contract.
            system_id (str | int): Identifier of the target system; will be sent as a
            string.

        Returns:
            dict[str, Any]: Parsed JSON response from the cancel contract API.
        """
        return await self._post_json(
            CANCEL_CONTRACT_PATH,
            {
                "platformCompanyId": platform_company_id,
                "systemId": str(system_id),
            },
        )

    async def async_get_dynamic_price(self, system_id: str | int) -> dict[str, Any]:
        """Fetch dynamic pricing configuration for the given system.

        Returns:
            dict: The dynamic pricing configuration payload extracted from the service
            response.
        """
        data = await self._get_json(
            DYNAMIC_PRICE_PATH,
            params={FIELD_SYSTEM_ID: str(system_id)},
        )
        return self._payload_dict(data, DYNAMIC_PRICE_PATH)

    async def async_save_location_id(self, *, connect_token: str) -> dict[str, Any]:
        """Save Flatpeak location ID using the provided connect token.

        Parameters:
            connect_token (str): Flatpeak connect token used to save/associate the
            location ID.

        Returns:
            dict[str, Any]: Parsed JSON response from the API.
        """
        return await self._post_json(
            SAVE_LOCATION_ID_PATH,
            {"connectToken": connect_token},
        )

    async def async_save_tou_plan(
        self,
        *,
        device_id: str | int,
        tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Save a Time-of-Use (TOU) schedule for a device.

        Parameters:
            device_id (str | int): Device identifier to which the TOU plan will be
            applied.
            tasks (list[dict[str, Any]]): List of TOU task objects formatted for the
            API.

        Returns:
            dict[str, Any]: Parsed JSON response from the API.
        """
        return await self._post_json(
            SAVE_TOU_PLAN_PATH,
            {"deviceId": str(device_id), "tasks": tasks},
        )

    async def async_query_tou_plan(self, *, device_id: str | int) -> dict[str, Any]:
        """Retrieve the Time-of-Use (TOU) schedule for the given device.

        Returns:
            dict: The TOU schedule payload returned by the API.
        """
        data = await self._get_json(
            QUERY_TOU_PLAN_PATH,
            params={FIELD_DEVICE_ID: str(device_id)},
        )
        return self._payload_dict(data, QUERY_TOU_PLAN_PATH)

    async def async_get_currency_list(self) -> list[dict[str, Any]]:
        """Retrieve the list of available currencies.

        Returns:
            list[dict[str, Any]]: The list of currency records extracted from the API
            payload.
        """
        data = await self._get_json(CURRENCY_LIST_PATH)
        return self._payload_list(data, CURRENCY_LIST_PATH)

    async def async_bind_currency(
        self,
        *,
        currency: str,
        device_id: str | int,
        system_id: str | int,
    ) -> dict[str, Any]:
        """Bind a currency to a specific device and system.

        Parameters:
            currency (str): Currency code or identifier to bind.
            device_id (str | int): Device identifier; will be sent as a string.
            system_id (str | int): System identifier; will be sent as a string.

        Returns:
            dict: Server response JSON.
        """
        return await self._post_json(
            BIND_CURRENCY_PATH,
            {
                "currency": currency,
                "deviceId": str(device_id),
                "systemId": str(system_id),
            },
        )

    async def async_get_shelly_devices(self) -> list[dict[str, Any]]:
        """Retrieve a normalized list of Shelly devices linked to the account.

        Accepts multiple backend response shapes for the `data` field: a list of device
        dicts; a dict containing `boundDevices` or `devices` lists; or a single device
        dict identified by `deviceId`. Non-dict entries are ignored.

        Returns:
            A list of Shelly device objects; empty list if none are present.
        """
        data = await self._get_json(SHELLY_DEVICES_PATH)
        raw = data.get(FIELD_DATA)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            bound_devices = raw.get("boundDevices")
            if isinstance(bound_devices, list):
                return [item for item in bound_devices if isinstance(item, dict)]
            devices = raw.get("devices")
            if isinstance(devices, list):
                return [item for item in devices if isinstance(item, dict)]
            if raw.get(FIELD_DEVICE_ID) is not None:
                return [raw]
        return []

    async def async_get_shelly_realtime_power(
        self,
        device_id: str | int,
    ) -> dict[str, Any]:
        """Fetches realtime power metrics for a Shelly accessory linked to the account.

        Parameters:
            device_id (str | int): The Shelly device identifier.

        Returns:
            dict: The response `data` object parsed as a dictionary (empty dict if the
            payload is missing or not a dict).
        """
        data = await self._get_json(
            SHELLY_REALTIME_POWER_PATH,
            params={FIELD_DEVICE_ID: str(device_id)},
        )
        return self._payload_dict(data, SHELLY_REALTIME_POWER_PATH)

    async def async_control_shelly_device(
        self,
        device_id: str | int,
        *,
        action: str,
        function: str,
        control_allowed: bool = True,
    ) -> bool:
        """Send a control command to a Shelly device.

        Parameters:
            device_id (str | int): Identifier of the Shelly device to control.
            action (str): Action name to perform as provided by the Shelly app.
            function (str): Function name associated with the action as provided by the
            Shelly app.
            control_allowed (bool): If `False`, the call will raise a `JackeryApiError`
            and no request will be sent.

        Returns:
            bool: `true` if the backend indicates the control request was accepted,
            `false` otherwise.

        Raises:
            JackeryApiError: If `control_allowed` is `False`.
        """
        if not control_allowed:
            msg = "Shelly control is not allowed for this device"
            raise JackeryApiError(msg)
        data = await self._post_form(
            SHELLY_CONTROL_PATH,
            {
                FIELD_DEVICE_ID: str(device_id),
                FIELD_ACTION: str(action),
                FIELD_FUNCTION: str(function),
            },
        )
        return _data_field_accepted(data)

    async def async_get_shelly_auth_url(self) -> dict[str, Any]:
        """Retrieve the Shelly OAuth authorization URL and accompanying state for the.

        redirect flow.

        Returns:
            dict: Contains `authUrl` (str) and `state` (str) for the Shelly OAuth
            redirect flow.
        """
        data = await self._post_form(SHELLY_AUTH_URL_PATH, {})
        return self._payload_dict(data, SHELLY_AUTH_URL_PATH)

    async def async_unbind_shelly_device(
        self,
        binding_id: int | str,
        device_id: str | int,
    ) -> bool:
        """Unbind a Shelly device from the user's Shelly binding list.

        Parameters:
            binding_id (int | str): Binding identifier from the Shelly devices list.
            device_id (str | int): Shelly device identifier to unbind.

        Returns:
            bool: True if the backend accepted the unbind request, False otherwise.
        """
        data = await self._post_form(
            SHELLY_UNBIND_DEVICE_PATH,
            {
                "bindingId": str(binding_id),
                FIELD_DEVICE_ID: str(device_id),
            },
        )
        return _data_field_accepted(data)

    async def async_unbind_shelly_account(self) -> bool:
        """Unbinds the Shelly account associated with the current user.

        Returns:
            True if the account unbind succeeded, False otherwise.
        """
        data = await self._post_form(SHELLY_UNBIND_ACCOUNT_PATH, {})
        return _data_field_accepted(data)

    async def async_get_shelly_binding_failures(
        self,
        state: str = "",
    ) -> dict[str, Any]:
        """Retrieve a summary of Shelly binding failures.

        Parameters:
            state (str): Optional state filter to narrow the binding failures query.

        Returns:
            dict: Response payload containing `bindCount` (int), `failedDeviceSns`
            (list[str]), and `successDeviceSns` (list[str]).
        """
        params: dict[str, str] = {}
        if state:
            params["state"] = state
        data = await self._get_json(SHELLY_BINDING_FAILURES_PATH, params=params)
        return self._payload_dict(data, SHELLY_BINDING_FAILURES_PATH)

    async def async_get_accessories(
        self,
        *,
        devices: str,
        id: str | int,
        parent_device_id: str | int,
    ) -> dict[str, Any]:
        """Fetch accessories data for the specified device(s).

        Parameters:
            devices: Comma-separated device identifiers to include in the query.
            id: Identifier sent as the request `id` parameter.
            parent_device_id: Identifier sent as the request `parentDeviceId` parameter.

        Returns:
            dict: The accessories payload returned by the backend.

        Note:
            Orphaned accessory-catalog endpoint; covered by a contract test rather
            than surfaced as an entity (no SolarVault accessory model is exposed).
        """
        data = await self._get_json(
            ACCESSORIES_PATH,
            {
                "devices": devices,
                "id": str(id),
                "parentDeviceId": str(parent_device_id),
            },
        )
        return self._payload_dict(data, ACCESSORIES_PATH)

    async def async_check_accessories_exist(self, *, devices: str) -> dict[str, Any]:
        """Determine accessory existence for the specified device IDs.

        Parameters:
            devices (str): Comma-separated device IDs to check.

        Returns:
            dict[str, Any]: Mapping of each device ID to the existence information
            returned by the backend.

        Note:
            Orphaned accessory-existence endpoint; covered by a contract test rather
            than surfaced as an entity.
        """
        data = await self._get_json(ACCESSORIES_EXIST_PATH, params={"devices": devices})
        return self._payload_dict(data, ACCESSORIES_EXIST_PATH)

    async def async_get_accessories_list(
        self,
        device_id: str | int,
    ) -> list[dict[str, Any]]:
        """List accessories for a device.

        Parameters:
            device_id (str | int): Identifier of the device whose accessories will be
            listed.

        Returns:
            list[dict[str, Any]]: List of accessory entries as dictionaries from the
            API response.
        """
        data = await self._get_json(
            ACCESSORIES_LIST_PATH,
            params={FIELD_DEVICE_ID: str(device_id)},
        )
        return self._payload_list(data, ACCESSORIES_LIST_PATH)

    async def async_set_accessories_name(
        self,
        *,
        device_name: str,
        id: str | int,
    ) -> dict[str, Any]:
        """Set the display name for an accessory.

        Parameters:
            device_name (str): New accessory name.
            id (str | int): Accessory identifier; will be sent as a string.

        Returns:
            dict: JSON response from the backend.
        """
        return await self._post_json(
            ACCESSORIES_NAME_PATH,
            {"deviceName": device_name, "id": str(id)},
        )

    async def async_check_jackery_accessories_exist(
        self,
        *,
        device_sn_infos: str,
    ) -> dict[str, Any]:
        """Determine whether Jackery accessories exist for the provided device serial.

        numbers.

        Parameters:
            device_sn_infos (str): Device serial number info string as accepted by the
            API.

        Returns:
            dict: The API response payload for the existence check.

        Note:
            Orphaned Jackery-accessory-existence endpoint; covered by a contract test
            rather than surfaced as an entity.
        """
        data = await self._get_json(
            ACCESSORIES_JACKERY_EXIST_PATH,
            params={"deviceSnInfos": device_sn_infos},
        )
        return self._payload_dict(data, ACCESSORIES_JACKERY_EXIST_PATH)

    async def async_sync_smart_accessories(self) -> dict[str, Any]:
        """Synchronize smart accessories data.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(ACCESSORIES_SYNC_PATH, {})

    async def async_get_sub_shadow(
        self,
        *,
        dev_type: str,
        device_sn: str,
        sub_device_sn: str,
    ) -> dict[str, Any]:
        """Retrieve the property shadow for a sub-device.

        Parameters:
            dev_type (str): Device type identifier.
            device_sn (str): Parent device serial number.
            sub_device_sn (str): Sub-device serial number.

        Returns:
            dict[str, Any]: Shadow payload for the specified sub-device.
        """
        data = await self._get_json(
            SUB_SHADOW_PATH,
            {
                "devType": dev_type,
                "deviceSn": device_sn,
                "subDeviceSn": sub_device_sn,
            },
        )
        return self._payload_dict(data, SUB_SHADOW_PATH)

    async def async_get_system_shadow(
        self,
        *,
        device_sn: str,
        diy_sn: str,
    ) -> dict[str, Any]:
        """Retrieve the system property shadow for a device.

        Parameters:
            device_sn (str): Device serial number.
            diy_sn (str): DIY device serial number.

        Returns:
            dict[str, Any]: System shadow data.
        """
        data = await self._get_json(
            SYSTEM_SHADOW_PATH,
            {"deviceSn": device_sn, "diySn": diy_sn},
        )
        return self._payload_dict(data, SYSTEM_SHADOW_PATH)

    async def async_get_notify_list(
        self,
        *,
        current_time: int = 0,
        device_sn: str = "",
        page_no: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """Retrieve a paginated list of push notifications.

        Parameters:
            current_time (int): Current timestamp in milliseconds (Unix ms) used for
            server-side filtering.
            device_sn (str): Device serial number to filter notifications; empty string
            for no filtering.
            page_no (int): 1-based page number to retrieve.
            page_size (int): Number of items per page.

        Returns:
            list[dict[str, Any]]: List of notification entries represented as
            dictionaries.
        """
        params: dict[str, Any] = {
            "currentTime": current_time,
            "deviceSn": device_sn,
            "pageNo": page_no,
            "pageSize": page_size,
        }
        data = await self._get_json(NOTIFY_LIST_PATH, params=params)
        return self._payload_list(data, NOTIFY_LIST_PATH)

    async def async_get_unread_count(self) -> dict[str, Any]:
        """Retrieve unread notification counts.

        Returns:
            dict[str, Any]: Mapping of unread count fields from the response (for
            example, total unread count and related metadata).
        """
        data = await self._get_json(UNREAD_COUNT_PATH)
        return self._payload_dict(data, UNREAD_COUNT_PATH)

    async def async_set_push_config(self, *, set: str) -> dict[str, Any]:
        """Set the device's push configuration on the server.

        Parameters:
            set (str): Configuration payload string to apply.

        Returns:
            dict[str, Any]: Response data returned by the backend.
        """
        return await self._post_json(PUSH_CONFIG_SET_PATH, {"set": set})

    async def async_get_push_config(self) -> dict[str, Any]:
        """Get push notification configuration.

        Returns:
            dict: Push configuration data.
        """
        data = await self._get_json(PUSH_CONFIG_GET_PATH)
        return self._payload_dict(data, PUSH_CONFIG_GET_PATH)

    async def async_check_smart_mode_set(
        self,
        *,
        device_id: str | int,
        system_id: str | int,
    ) -> dict[str, Any]:
        """Determine whether smart mode is configured for the given device and system.

        Parameters:
            device_id (str | int): Device identifier.
            system_id (str | int): System identifier.

        Returns:
            dict[str, Any]: Smart mode check result as a dictionary.
        """
        data = await self._post_json(
            SMART_MODE_CHECK_PATH,
            {FIELD_DEVICE_ID: str(device_id), FIELD_SYSTEM_ID: str(system_id)},
        )
        return self._payload_dict(data, SMART_MODE_CHECK_PATH)

    async def async_get_smart_mode_info(self, system_id: str | int) -> dict[str, Any]:
        """Retrieve smart mode configuration for the specified system.

        Parameters:
            system_id (str | int): Identifier of the system to fetch configuration for.

        Returns:
            dict[str, Any]: Dictionary containing the smart mode configuration for the
            system.
        """
        data = await self._get_json(
            SMART_MODE_INFO_PATH,
            params={FIELD_SYSTEM_ID: str(system_id)},
        )
        return self._payload_dict(data, SMART_MODE_INFO_PATH)

    async def async_start_smart_mode(self, system_id: str | int) -> dict[str, Any]:
        """Enable smart mode for the specified system.

        Parameters:
            system_id (str | int): Identifier of the target system.

        Returns:
            dict[str, Any]: Backend response data.
        """
        return await self._post_json(
            SMART_MODE_START_PATH,
            {FIELD_SYSTEM_ID: str(system_id)},
        )

    async def async_check_app_version(self) -> dict[str, Any]:
        """Check whether a newer app version is available.

        Returns:
            dict[str, Any]: Dictionary containing the backend's normalized version
            information.
        """
        data = await self._get_json(APP_VERSION_PATH)
        return self._payload_dict(data, APP_VERSION_PATH)

    async def async_get_banner_list(self) -> list[dict[str, Any]]:
        """Get the list of banner entries from the backend.

        Each item is a dictionary representing a banner and has been normalized via the
        client's payload parser.

        Returns:
            list[dict[str, Any]]: Banner entry dictionaries.
        """
        data = await self._get_json(BANNER_LIST_PATH)
        return self._payload_list(data, BANNER_LIST_PATH)

    async def async_submit_feedback(
        self,
        *,
        contact_info: str,
        content: str,
        device_sn: str = "",
        image: str = "",
    ) -> dict[str, Any]:
        """Send user feedback to the backend.

        Parameters:
            contact_info (str): Contact information to include with the feedback.
            content (str): Feedback message.
            device_sn (str): Device serial number to associate with the feedback, if
            any.
            image (str): Base64-encoded image to attach to the feedback, if any.

        Returns:
            dict: Response data returned by the backend.
        """
        fields: dict[str, Any] = {
            "contactInfo": contact_info,
            "content": content,
        }
        if device_sn:
            fields["deviceSn"] = device_sn
        if image:
            fields["image"] = image
        return await self._post_json(FEEDBACK_PATH, fields)

    async def async_get_faq_list(self) -> list[dict[str, Any]]:
        """Retrieve the list of FAQ entries.

        Returns:
            list[dict[str, Any]]: List of FAQ entry objects returned by the backend.
        """
        data = await self._get_json(FAQ_LIST_PATH)
        return self._payload_list(data, FAQ_LIST_PATH)

    async def async_get_faq_answer(self) -> list[dict[str, Any]]:
        """Retrieve FAQ answers from the backend.

        Returns:
            list[dict[str, Any]]: List of FAQ answer entries as dictionaries.
        """
        data = await self._get_json(FAQ_ANSWER_PATH)
        return self._payload_list(data, FAQ_ANSWER_PATH)

    async def async_agree_privacy_consent(
        self,
        *,
        pending_agree_version_ids: str,
    ) -> dict[str, Any]:
        """Record agreement to one or more privacy consent versions.

        Parameters:
            pending_agree_version_ids (str): Comma-separated privacy version IDs to
            agree to.

        Returns:
            dict: Response payload returned by the backend.
        """
        return await self._post_json(
            PRIVACY_CONSENT_PATH,
            {"pendingAgreeVersionIds": pending_agree_version_ids},
        )

    async def async_check_privacy_update(self) -> dict[str, Any]:
        """Determine whether the backend requires an updated privacy consent.

        Returns:
            dict: Server response containing privacy update information, including
            whether an update is required and any related metadata.
        """
        data = await self._get_json(PRIVACY_CHECK_PATH)
        return self._payload_dict(data, PRIVACY_CHECK_PATH)

    async def async_get_product_instruction(
        self,
        *,
        dev_sn: str,
        type: str = "",
    ) -> dict[str, Any]:
        """Retrieve product instructions for a given device.

        Parameters:
            dev_sn (str): Device serial number used to query instructions.
            type (str): Optional instruction type filter; when empty, no type filter is
            applied.

        Returns:
            dict[str, Any]: Normalized instruction payload returned by the backend.
        """
        params: dict[str, str] = {"devSn": dev_sn}
        if type:
            params["type"] = type
        data = await self._get_json(INSTRUCTION_PATH, params=params)
        return self._payload_dict(data, INSTRUCTION_PATH)

    async def async_get_zone_list(self) -> list[dict[str, Any]]:
        """Retrieve the list of country/zone entries for DIY devices.

        Returns:
            list[dict[str, Any]]: Zone entry dictionaries returned by the backend.
        """
        data = await self._get_json(ZONE_LIST_PATH)
        return self._payload_list(data, ZONE_LIST_PATH)

    async def async_get_gcs_list(self, *, country: str) -> list[dict[str, Any]]:
        """Retrieve the list of grid-connection (GCS) standards for the specified.

        country.

        Parameters:
            country (str): Country code.

        Returns:
            list[dict[str, Any]]: List of grid standard entries, each represented as a
            dictionary.
        """
        data = await self._get_json(GCS_LIST_PATH, params={"country": country})
        return self._payload_list(data, GCS_LIST_PATH)

    async def async_get_alarm_detail(self, *, alarm_key: str) -> dict[str, Any]:
        """Retrieve detailed information for a specific alarm.

        Parameters:
            alarm_key (str): Alarm identifier to fetch.

        Returns:
            alarm_detail (dict[str, Any]): Dictionary of alarm detail fields and their
            values.
        """
        data = await self._get_json(ALARM_DETAIL_PATH, params={"alarmKey": alarm_key})
        return self._payload_dict(data, ALARM_DETAIL_PATH)

    async def async_sync_alerts(self, *, content: str, id: str | int) -> dict[str, Any]:
        """Sync device faults and alarms.

        Parameters:
            content: Alert content (JSON).
            id: Device/system identifier.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            ALERT_SYNC_PATH,
            {"content": content, "id": str(id)},
        )

    async def async_get_offline_statistics(self) -> dict[str, Any]:
        """Retrieve offline statistics from the backend.

        Returns:
            dict[str, Any]: Offline statistics payload.
        """
        data = await self._get_json(OFFLINE_STAT_PATH)
        return self._payload_dict(data, OFFLINE_STAT_PATH)

    async def async_get_power3(
        self,
        *,
        device_sn: str,
        properties: str,
    ) -> dict[str, Any]:
        """Retrieve Power3 property values for a device.

        Parameters:
            device_sn (str): Device serial number.
            properties (str): Comma-separated property names to request.

        Returns:
            dict: Normalized Power3 property payload returned by the backend.
        """
        data = await self._get_json(
            POWER3_PATH,
            {"deviceSn": device_sn, "properties": properties},
        )
        return self._payload_dict(data, POWER3_PATH)

    @staticmethod
    def _select_ota_item(
        items: list[dict[str, Any]],
        device_sn: str,
    ) -> dict[str, Any]:
        """Select the OTA update entry matching the given device serial number.

        Parameters:
            items (list[dict[str, Any]]): List of OTA item dictionaries.
            device_sn (str): Device serial number to match.

        Returns:
            dict[str, Any]: The matching item; if none match returns the first item
            when available, otherwise an empty dict.
        """
        requested_sn = str(device_sn)
        for item in items:
            if str(item.get(FIELD_DEVICE_SN) or "") == requested_sn:
                return item
        return items[0] if items else {}

    @staticmethod
    def _response_with_request_context(
        data: dict[str, Any],
        *,
        path: str,
        params: dict[str, str],
        payload_request: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Annotate a response copy with request metadata for diagnostics.

        Parameters:
            data (dict[str, Any]): Parsed response object to copy and annotate.
            path (str): Request path to record under request metadata.
            params (dict[str, str]): Request query parameters to record under request
            metadata.
            payload_request (dict[str, str] | None): Optional metadata to insert into
            the payload object (the response's `FIELD_DATA`) if it is a dict.

        Returns:
            dict[str, Any]: A copy of `data` with `APP_REQUEST_META` ensured at the top
            level (containing `path` and `params`), and with `APP_REQUEST_META` added
            to the payload dict when `payload_request` is provided.
        """
        response = dict(data)
        response.setdefault(APP_REQUEST_META, {"path": path, "params": dict(params)})
        payload = response.get(FIELD_DATA)
        if isinstance(payload, dict) and payload_request is not None:
            payload = dict(payload)
            payload.setdefault(APP_REQUEST_META, dict(payload_request))
            response[FIELD_DATA] = payload
        return response

    @staticmethod
    def _is_transient_http_status(status: int) -> bool:
        """Return True for server-side statuses that are safe to retry."""
        return 500 <= status < 600  # noqa: PLR2004

    async def _request_json_with_retry(
        self,
        method: str,
        path: str,
        request: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
    ) -> tuple[int, dict[str, Any]]:
        """Run one JSON HTTP request with bounded transient retry/backoff.

        Wraps a request callable (typically the per-method ``_do`` closure) so
        that transient server errors (HTTP 5xx) and connection-level failures
        (``TimeoutError`` / ``aiohttp.ClientConnectionError``) are retried with
        the backoff schedule in :data:`HTTP_RETRY_BACKOFF_SEC`. Token-expiry
        re-login is handled separately by the caller, so this layer only adds
        resilience against flaky transport without changing auth semantics.
        """
        for attempt in range(1, HTTP_RETRY_ATTEMPTS + 1):
            try:
                status, data = await request()
            except (TimeoutError, aiohttp.ClientConnectionError) as err:
                if attempt >= HTTP_RETRY_ATTEMPTS:
                    raise
                delay = HTTP_RETRY_BACKOFF_SEC[attempt - 1]
                _LOGGER.debug(
                    "Jackery %s %s transient %s on attempt %d/%d; retrying in %.1fs",
                    method,
                    path,
                    type(err).__name__,
                    attempt,
                    HTTP_RETRY_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            if not self._is_transient_http_status(status):
                return status, data
            if attempt >= HTTP_RETRY_ATTEMPTS:
                return status, data
            delay = HTTP_RETRY_BACKOFF_SEC[attempt - 1]
            _LOGGER.debug(
                "Jackery %s %s HTTP %d on attempt %d/%d; retrying in %.1fs",
                method,
                path,
                status,
                attempt,
                HTTP_RETRY_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)
        msg = f"{method} {path} retry loop exhausted"
        raise JackeryApiError(msg)

    async def _get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        request_timeout: int | None = None,
    ) -> dict[str, Any]:
        """Perform an authenticated GET request to the API path and return the parsed.

        response body.

        Parameters:
            path (str): API path (appended to the base URL).
            params (dict | None): Optional query parameters to include in the request.
            request_timeout (int | None): Override the default request timeout in
            seconds.

        Returns:
            dict: Parsed JSON response body.

        Raises:
            JackeryAuthError: When the response indicates an
            authentication/authorization failure.
            JackeryApiError: For network/timeout errors, non-200 HTTP responses, or
            backend errors.
        """
        await self._ensure_token()
        url = f"{BASE_URL}{path}"
        effective_timeout = request_timeout or REQUEST_TIMEOUT_SEC

        def _request_headers() -> dict[str, str]:
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_JSON
            return headers

        async def _do() -> tuple[int, dict]:
            async with self._session.get(
                url,
                params=params,
                headers=_request_headers(),
                timeout=aiohttp.ClientTimeout(total=effective_timeout),
            ) as resp:
                status = resp.status
                try:
                    body = await resp.json(content_type=None)
                except (
                    aiohttp.ContentTypeError,
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                    ValueError,
                ) as err:
                    raw_text = (await resp.text())[:HTTP_RAW_TEXT_LIMIT]
                    msg = (
                        f"{HTTP_METHOD_GET} {path} returned invalid JSON: {raw_text!r}"
                    )
                    raise JackeryApiError(msg) from err
                return status, body

        self._requests_total += 1
        try:
            status, data = await self._request_json_with_retry(
                HTTP_METHOD_GET,
                path,
                _do,
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            self._requests_failed += 1
            if isinstance(err, TimeoutError):
                self._timeouts_total += 1
            msg = (
                f"{HTTP_METHOD_GET} {path} request failed: "
                f"{type(err).__name__}: {err or "(no message)"}"
            )
            raise JackeryApiError(
                msg,
            ) from err

        if self._is_token_expired_response(status, data):
            _LOGGER.info("Jackery token expired — re-login for GET %s", path)
            self._auth_retries += 1
            async with self._lock:
                self._token = None
                await self.async_login()
            try:
                status, data = await self._request_json_with_retry(
                    HTTP_METHOD_GET,
                    path,
                    _do,
                )
            except (TimeoutError, aiohttp.ClientError) as err:
                self._requests_failed += 1
                if isinstance(err, TimeoutError):
                    self._timeouts_total += 1
                msg = (
                    f"{HTTP_METHOD_GET} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or "(no message)"}"
                )
                raise JackeryApiError(
                    msg,
                ) from err

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_GET, path, status, data),
            )
        if status != HTTPStatus.OK:
            msg = f"{HTTP_METHOD_GET} {path} HTTP {status}"
            raise JackeryApiError(msg)
        code = self._extract_code(data)
        if code not in {CODE_OK, None}:
            msg = (
                f"{HTTP_METHOD_GET} {path}"
                f" code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)}"
            )
            raise JackeryApiError(
                msg,
            )
        await self._emit_payload_debug(
            self._http_payload_debug(
                method=HTTP_METHOD_GET,
                path=path,
                params=params,
                status=status,
                response=data,
            ),
        )
        return data

    async def _put_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON PUT request to the given API path and return the parsed.

        response; if the token is expired the method will re-authenticate and retry the
        request once.

        Returns:
            dict: Parsed JSON response from the API.

        Raises:
            JackeryAuthError: When the response indicates an authentication or
            authorization failure.
            JackeryApiError: On network/request failures, timeouts, non-200 HTTP
            status, or backend error codes.
        """
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        def _request_headers() -> dict[str, str]:
            """Build HTTP headers for JSON requests, including authentication headers.

            when available.

            Returns:
                dict[str, str]: Mapping of HTTP header names to values with the
                content-type set to JSON and token headers included when present.
            """
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_JSON
            return headers

        async def _do() -> tuple[int, dict[str, Any]]:
            """Perform an HTTP PUT to the prepared URL and return the response status.

            and parsed body.

            Returns:
                A tuple (status, body) where `status` is the HTTP status code and
                `body` is the parsed JSON response when available; if the response
                cannot be decoded as JSON, `body` is a dict containing `FIELD_RAW_TEXT`
                with the response text truncated to HTTP_RAW_TEXT_LIMIT.
            """
            async with self._session.put(
                url,
                json=payload,
                headers=_request_headers(),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC),
            ) as resp:
                status = resp.status
                try:
                    body = await resp.json(content_type=None)
                except (
                    aiohttp.ContentTypeError,
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                    ValueError,
                ):
                    body = {FIELD_RAW_TEXT: (await resp.text())[:HTTP_RAW_TEXT_LIMIT]}
                return status, body

        self._requests_total += 1
        try:
            status, data = await self._request_json_with_retry(
                HTTP_METHOD_PUT,
                path,
                _do,
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            self._requests_failed += 1
            if isinstance(err, TimeoutError):
                self._timeouts_total += 1
            msg = (
                f"{HTTP_METHOD_PUT} {path} request failed: "
                f"{type(err).__name__}: {err or "(no message)"}"
            )
            raise JackeryApiError(
                msg,
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info(
                "Jackery token expired — re-login for %s %s",
                HTTP_METHOD_PUT,
                path,
            )
            self._auth_retries += 1
            async with self._lock:
                self._token = None
                await self.async_login()
            try:
                status, data = await self._request_json_with_retry(
                    HTTP_METHOD_PUT,
                    path,
                    _do,
                )
            except (TimeoutError, aiohttp.ClientError) as err:
                self._requests_failed += 1
                if isinstance(err, TimeoutError):
                    self._timeouts_total += 1
                msg = (
                    f"{HTTP_METHOD_PUT} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or "(no message)"}"
                )
                raise JackeryApiError(
                    msg,
                ) from err

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_PUT, path, status, data)
            )
        if status != HTTPStatus.OK:
            msg_0 = f"{HTTP_METHOD_PUT} {path} HTTP {status}"
            raise JackeryApiError(msg_0)
        code = self._extract_code(data)
        if code not in {CODE_OK, None}:
            msg = (
                f"{HTTP_METHOD_PUT} {path}"
                f" code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)}"
            )
            raise JackeryApiError(msg)
        await self._emit_payload_debug(
            self._http_payload_debug(
                method=HTTP_METHOD_PUT,
                path=path,
                body=payload,
                status=status,
                response=data,
            )
        )
        return data

    async def async_set_system_name(
        self, system_id: str | int, system_name: str
    ) -> bool:
        """Rename the specified system to the given name.

        Parameters:
            system_id (str | int): Identifier of the system to rename.
            system_name (str): New name for the system; must be a non-empty string.

        Returns:
            bool: `true` if the server acknowledged the rename, `false` otherwise.

        Raises:
            JackeryApiError: If `system_name` is empty after trimming or if the API request fails.
        """  # noqa: E501
        """PUT /v1/device/system/name — rename a system.

        Captured body: {"systemName": "SolarVault", "id": "<systemId>"}

        Response payload is a boolean: `data: true`.
        """
        if not system_name or not system_name.strip():
            msg = "system_name must be a non-empty string"
            raise JackeryApiError(msg)
        data = await self._put_json(
            SYSTEM_NAME_PATH,
            {FIELD_SYSTEM_NAME: system_name.strip(), FIELD_ID: str(system_id)},
        )
        return data.get(FIELD_DATA) is True

    # ------------------------------------------------------------------
    # Experimental app-captured writers
    # ------------------------------------------------------------------
    # These endpoints were discovered via PCAPdroid captures but only failed
    # responses have been seen so far. They're kept as best-effort helpers;
    # the integration surfaces the server's full error response so the user
    # can troubleshoot. See const.py for caveats.

    async def _post_form(self, path: str, fields: dict[str, Any]) -> dict:
        """Generic form-urlencoded POST with auto re-login on expiry."""
        """Send a form-urlencoded POST to the Jackery API, retrying once after automatic re-login if the token is expired.

        Parameters:
            path (str): API endpoint path appended to the base URL.
            fields (dict[str, Any]): Form fields to send; all values will be converted to strings.

        Returns:
            dict: Parsed JSON response from the API, or a dict containing raw truncated text under `FIELD_RAW_TEXT` if JSON decoding fails.

        Raises:
            JackeryApiError: On network/timeout failures, non-200 HTTP status, or when the response `code` indicates an error.
            JackeryAuthError: When the response indicates an authentication or authorization failure.
        """  # noqa: E501
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        def _request_headers() -> dict[str, str]:
            """Build HTTP headers for a form-encoded POST, including the auth token."""
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_FORM
            return headers

        body = {k: str(v) for k, v in fields.items()}

        async def _do() -> tuple[int, dict]:
            async with self._session.post(
                url,
                data=body,
                headers=_request_headers(),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC),
            ) as resp:
                status = resp.status
                try:
                    data = await resp.json(content_type=None)
                except (
                    aiohttp.ContentTypeError,
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                    ValueError,
                ):
                    data = {FIELD_RAW_TEXT: (await resp.text())[:HTTP_RAW_TEXT_LIMIT]}
                return status, data

        try:
            status, data = await _do()
        except (TimeoutError, aiohttp.ClientError) as err:
            if isinstance(err, TimeoutError):
                self._timeouts_total += 1
            msg = (
                f"{HTTP_METHOD_POST} {path} request failed: "
                f"{type(err).__name__}: {err or "(no message)"}"
            )
            raise JackeryApiError(
                msg,
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info(
                "Jackery token expired — re-login for %s %s", HTTP_METHOD_POST, path
            )
            self._token = None
            await self._ensure_token()
            try:
                status, data = await _do()
            except (TimeoutError, aiohttp.ClientError) as err:
                if isinstance(err, TimeoutError):
                    self._timeouts_total += 1
                msg = (
                    f"{HTTP_METHOD_POST} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or "(no message)"}"
                )
                raise JackeryApiError(
                    msg,
                ) from err

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_POST, path, status, data)
            )
        if status != HTTPStatus.OK:
            msg_0 = f"{HTTP_METHOD_POST} {path} HTTP {status}"
            raise JackeryApiError(msg_0)
        code = self._extract_code(data)
        if code not in {CODE_OK, None}:
            # Surface the whole response so callers can show it to the user
            msg = (
                f"{HTTP_METHOD_POST} {path}"
                f" code={data.get(FIELD_CODE)}"
                f" msg={data.get(FIELD_MSG)!r}"
                f" data={data.get(FIELD_DATA)!r}"
            )
            msg_0 = (
                f"{HTTP_METHOD_POST} {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)!r} "  # noqa: E501
                f"data={data.get(FIELD_DATA)!r}"
            )
            raise JackeryApiError(msg_0)
        await self._emit_payload_debug(
            self._http_payload_debug(
                method=HTTP_METHOD_POST,
                path=path,
                body=body,
                status=status,
                response=data,
            )
        )
        return data

    async def async_set_max_power(self, device_id: str | int, max_power: int) -> bool:
        """POST /v1/device/deviceMaxPowerRecord/saveRecord (experimental).

        Captured body: `maxPower=<int>&deviceId=<long>` as form-urlencoded.

        ⚠️  Only failed responses (code=10600) have been observed so far.
        The endpoint name ("saveRecord") suggests this might be a history
        log, not the live setter. May require specific value ranges or
        additional fields we haven't identified yet.
        Set the device's maximum allowed power using the experimental max-power endpoint.

        Validates that `max_power` is an integer greater than or equal to 0 before sending the request.

        Parameters:
            device_id (str | int): Device identifier (serial or numeric id) used by the backend.
            max_power (int): Desired maximum power in watts; must be an integer greater than or equal to 0.

        Returns:
            bool: `True` if the backend acknowledged success (truthy `FIELD_DATA`), `False` otherwise.

        Raises:
            JackeryApiError: If `max_power` is invalid or the API call fails.
        """  # noqa: E501
        if not isinstance(max_power, int) or max_power < 0:
            msg = "max_power must be a non-negative integer"
            raise JackeryApiError(msg)
        data = await self._post_form(
            MAX_POWER_SAVE_PATH,
            {FIELD_MAX_POWER: max_power, FIELD_DEVICE_ID: str(device_id)},
        )
        return bool(data.get(FIELD_DATA))

    def diagnostics_snapshot(self) -> dict[str, Any]:
        """Provide in-memory transport counters for the HTTP API diagnostic sensor.

        Returns:
            dict[str, int]: Mapping with keys: requests_total, requests_failed,
            timeouts_total, auth_retries.
        """
        return {
            "requests_total": self._requests_total,
            "requests_failed": self._requests_failed,
            "timeouts_total": self._timeouts_total,
            "auth_retries": self._auth_retries,
        }

    def hydrate_mqtt_session(
        self,
        *,
        user_id: str,
        seed_b64: str,
        mac_id: str,
        mac_id_source: str | None = None,
    ) -> None:
        """Restore MQTT session fields from cached values.

        Parameters:
            user_id (str): MQTT user identifier; falsy values are stored as None.
            seed_b64 (str): Base64-encoded MQTT seed; falsy values are stored as None.
            mac_id (str): MQTT MAC identifier; falsy values are stored as None.
            mac_id_source (str | None): Optional source descriptor for the MAC
            identifier; if provided, sets the MAC ID source.
        """
        self._mqtt_user_id = user_id or None
        self._mqtt_seed_b64 = seed_b64 or None
        self._mqtt_mac_id = mac_id or None
        if mac_id_source:
            self._mqtt_mac_id_source = mac_id_source

    def mqtt_session_snapshot(self) -> MqttSessionSnapshot | None:
        """Return a serializable snapshot of the current MQTT session.

        Returns:
            MqttSessionSnapshot if all required fields are present, or None if
            incomplete.
        """
        if not (self._mqtt_user_id and self._mqtt_seed_b64 and self._mqtt_mac_id):
            return None
        return {
            MQTT_SESSION_USER_ID: self._mqtt_user_id,
            MQTT_SESSION_SEED_B64: self._mqtt_seed_b64,
            MQTT_SESSION_MAC_ID: self._mqtt_mac_id,
            MQTT_SESSION_MAC_ID_SOURCE: self._mqtt_mac_id_source,
        }

    async def async_get_user_info(self) -> dict[str, Any]:
        """Retrieve the authenticated user's profile information.

        Returns:
            dict[str, Any]: User profile data as returned by the backend.
        """
        data = await self._get_json(USER_INFO_PATH)
        return self._payload_dict(data, USER_INFO_PATH)

    async def async_update_register_id(self, register_id: str) -> dict[str, Any]:
        """POST /v1/auth/updateRegisterId — update push notification registration ID."""
        return await self._post_json(
            UPDATE_REGISTER_ID_PATH,
            {"registerId": register_id},
        )

    # --- legacy fallback ----------------------------------------------------
    async def async_list_devices_legacy(self) -> list[dict[str, Any]]:
        """GET /v1/device/bind/list — Explorer-series only, kept for compat.

        Propagates authentication failures so callers can handle
        re-authentication; for other API errors returns an empty list.

        Returns:
            list[dict[str, Any]]: Device objects parsed from the response, or
            an empty list if a non-auth `JackeryError` occurred.
        """
        try:
            data = await self._get_json(DEVICE_LIST_PATH)
            return self._payload_list(data, DEVICE_LIST_PATH)
        except JackeryAuthError:
            raise
        except JackeryError:
            return []

    async def async_update_user_info(self, *, nick_name: str) -> dict[str, Any]:
        """Update the user's display name.

        Parameters:
            nick_name (str): New display name; sent to the backend as the `nickName`
            field.

        Returns:
            dict[str, Any]: Decoded backend response data.
        """
        return await self._post_json(MODIFY_INFO_PATH, {"nickName": nick_name})

    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Generic JSON-body POST with auto re-login on expiry."""
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        def _request_headers() -> dict[str, str]:
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_JSON
            return headers

        async def _do() -> tuple[int, dict[str, Any]]:
            async with self._session.post(
                url,
                json=payload,
                headers=_request_headers(),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC),
            ) as resp:
                status = resp.status
                try:
                    data = await resp.json(content_type=None)
                except (
                    aiohttp.ContentTypeError,
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                    ValueError,
                ):
                    data = {FIELD_RAW_TEXT: (await resp.text())[:HTTP_RAW_TEXT_LIMIT]}
                return status, data

        self._requests_total += 1
        try:
            status, data = await self._request_json_with_retry(
                HTTP_METHOD_POST,
                path,
                _do,
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            self._requests_failed += 1
            if isinstance(err, TimeoutError):
                self._timeouts_total += 1
            msg = (
                f"{HTTP_METHOD_POST} {path} request failed: "
                f"{type(err).__name__}: {err or "(no message)"}"
            )
            raise JackeryApiError(
                msg,
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info(
                "Jackery token expired — re-login for %s %s",
                HTTP_METHOD_POST,
                path,
            )
            self._auth_retries += 1
            async with self._lock:
                self._token = None
                await self.async_login()
            try:
                status, data = await self._request_json_with_retry(
                    HTTP_METHOD_POST,
                    path,
                    _do,
                )
            except (TimeoutError, aiohttp.ClientError) as err:
                self._requests_failed += 1
                if isinstance(err, TimeoutError):
                    self._timeouts_total += 1
                msg = (
                    f"{HTTP_METHOD_POST} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or "(no message)"}"
                )
                raise JackeryApiError(
                    msg,
                ) from err

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_POST, path, status, data)
            )
        if status != HTTPStatus.OK:
            msg_0 = f"{HTTP_METHOD_POST} {path} HTTP {status}"
            raise JackeryApiError(msg_0)
        code = self._extract_code(data)
        if code not in {CODE_OK, None}:
            msg = (
                f"{HTTP_METHOD_POST} {path}"
                f" code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)!r}"
            )
            raise JackeryApiError(
                msg,
            )
        await self._emit_payload_debug(
            self._http_payload_debug(
                method=HTTP_METHOD_POST,
                path=path,
                body=payload,
                status=status,
                response=data,
            ),
        )
        return data
