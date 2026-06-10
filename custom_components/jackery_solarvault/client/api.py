"""Async API client for the Jackery SolarVault cloud (iot.jackeryapp.com).

Endpoint paths and polling rules are mirrored from PROTOCOL.md §2.
MQTT command details are documented separately in PROTOCOL.md §3.

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
import inspect
import json
import logging
import re
from typing import TYPE_CHECKING, Any
import uuid

import aiohttp
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.serialization import load_der_public_key

from ..const import (  # noqa: RUF100, TID252
    AES_KEY,
    ALARM_PATH,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_META,
    APP_VERSION,
    APP_VERSION_CODE,
    BASE_URL,
    BATTERY_PACK_PATH,
    BATTERY_TRENDS_PATH,
    CODE_OK,
    CODE_TOKEN_EXPIRED,
    DATE_TYPE_DAY,
    DEVICE_BATTERY_STAT_PATH,
    DEVICE_CT_STAT_PATH,
    DEVICE_EPS_STAT_PATH,
    DEVICE_HOME_STAT_PATH,
    DEVICE_LIST_PATH,
    DEVICE_METER_STAT_PATH,
    DEVICE_MODEL_HEADER,
    DEVICE_PROPERTY_PATH,
    DEVICE_PV_STAT_PATH,
    DEVICE_SOCKET_STATISTIC_PATH,
    DEVICE_SOCKET_STAT_PATH,
    DEVICE_STATISTIC_PATH,
    DEVICE_TODAY_ENERGY_PATH,
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
    HOME_TRENDS_PATH,
    HTTP_CONTENT_TYPE_FORM,
    HTTP_CONTENT_TYPE_JSON,
    HTTP_HEADER_CONTENT_TYPE,
    HTTP_METHOD_GET,
    HTTP_METHOD_POST,
    HTTP_METHOD_PUT,
    HTTP_RAW_TEXT_LIMIT,
    LOCATION_PATH,
    LOGIN_PATH,
    LOGIN_TIMEOUT_SEC,
    MAX_POWER_SAVE_PATH,
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
    OTA_LIST_PATH,
    PLATFORM_HEADER,
    POWER_PRICE_PATH,
    PRICE_HISTORY_CONFIG_PATH,
    PRICE_SOURCE_LIST_PATH,
    PV_TRENDS_PATH,
    REGISTER_APP_ID,
    REQUEST_TIMEOUT_SEC,
    RSA_PUBLIC_KEY_B64,
    SAVE_DYNAMIC_MODE_PATH,
    SAVE_SINGLE_MODE_PATH,
    SHELLY_AUTH_URL_PATH,
    SHELLY_BINDING_FAILURES_PATH,
    SHELLY_CONTROL_PATH,
    SHELLY_DEVICES_PATH,
    SHELLY_REALTIME_POWER_PATH,
    SHELLY_UNBIND_ACCOUNT_PATH,
    SHELLY_UNBIND_DEVICE_PATH,
    SYSTEM_LIST_PATH,
    SYSTEM_NAME_PATH,
    SYSTEM_STATISTIC_PATH,
    SYS_VERSION,
    USER_AGENT,
)
from ..util import (  # noqa: RUF100, TID252
    app_period_date_bounds,
    chart_series_debug,
    safe_bool,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_LOGGER = logging.getLogger(__name__)


class JackeryError(Exception):
    """Base exception."""


class JackeryAuthError(JackeryError):
    """Authentication failure."""


class JackeryApiError(JackeryError):
    """Generic API failure."""


# ---------------------------------------------------------------------------
# Crypto
# ---------------------------------------------------------------------------
def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _aes_cbc_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    """Encrypts plaintext using AES in CBC mode with PKCS7 padding.

    Parameters:
        plaintext (bytes): Data to be encrypted.
        key (bytes): AES key (16, 24, or 32 bytes).
        iv (bytes): Initialization vector (16 bytes).

    Returns:
        bytes: Ciphertext produced by AES-CBC encryption of the padded plaintext.
    """
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def encrypt_mqtt_body(body: dict[str, Any], bluetooth_key: bytes) -> str:
    """Encrypt an MQTT command body using AES-128-CBC with PKCS7 padding and return the ciphertext as Base64.

    Encrypts the compact JSON serialization of `body` using AES-128-CBC where the encryption key and IV are both the provided Bluetooth key, then Base64-encodes the ciphertext.

    Parameters:
        body (dict[str, Any]): Command body to serialize and encrypt.
        bluetooth_key (bytes): 16-byte Bluetooth key used as both AES key and IV.

    Returns:
        str: Base64-encoded ciphertext.

    Raises:
        ValueError: If `bluetooth_key` is not exactly 16 bytes.
    """  # noqa: E501
    if len(bluetooth_key) != 16:  # noqa: PLR2004
        raise ValueError(  # noqa: TRY003
            f"encrypt_mqtt_body: bluetoothKey must be 16 bytes, got {len(bluetooth_key)}"  # noqa: E501
        )
    plaintext = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )  # noqa: E501, RUF100
    ciphertext = _aes_cbc_encrypt(plaintext, bluetooth_key, bluetooth_key)
    return base64.b64encode(ciphertext).decode("ascii")


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
        raise TypeError(  # noqa: TRY003
            f"Jackery login expects an RSA public key, got {type(public_key).__name__}"
        )
    return public_key.encrypt(data, asym_padding.PKCS1v15())


def _generate_udid(seed: str) -> str:
    """Generate a deterministic MQTT UDID from a seed string.

    The result is the module's MQTT MAC ID prefix concatenated with a 32-character hexadecimal UUID (no dashes), produced deterministically from the provided seed so the same seed always yields the same UDID.

    Parameters:
        seed (str): Input seed used to deterministically derive the UDID.

    Returns:
        str: MQTT UDID string comprising `MQTT_MAC_ID_PREFIX` followed by 32 lowercase hex characters (no dashes).
    """  # noqa: E501
    md5_digest = hashlib.md5(seed.encode("utf-8")).digest()
    u = uuid.UUID(bytes=md5_digest, version=3)
    return MQTT_MAC_ID_PREFIX + str(u).replace("-", "")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class JackeryApi:  # noqa: PLR0904
    """Async client for the Jackery SolarVault cloud."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        account: str,
        password: str,
        mqtt_mac_id: str | None = None,
        region_code: str | None = None,
    ) -> None:
        """Initialize the JackeryApi client and prepare authentication state, MQTT placeholders, and diagnostic counters.

        Parameters:
            session (aiohttp.ClientSession): HTTP session used for API requests.
            account (str): Account identifier for login.
            password (str): Account password for login.
            mqtt_mac_id (str | None): Optional preconfigured MQTT MAC identifier to be validated/resolved later.
            region_code (str | None): Optional region/country code; stored uppercased or `None` when empty.
        """  # noqa: E501
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
            Callable[[dict[str, Any]], Awaitable[None] | None] | None
        ) = None

        # Transport counters for diagnostic sensors (reset on HA restart).
        self._requests_total = 0
        self._requests_failed = 0
        self._timeouts_total = 0
        self._auth_retries = 0

    def _maybe_learn_region_code(self, systems: list[dict[str, Any]]) -> None:
        """Set the API client's region code from the first system entry that provides a country code when no region is configured.

        If the client already has a region code set, this is a no-op. Otherwise, the method iterates the provided system dictionaries and, for the first item whose `FIELD_COUNTRY_CODE` yields a non-empty value, stores the trimmed uppercase country code on `self._region_code` and logs the inference.

        Parameters:
            systems (list[dict]): List of system metadata dictionaries returned by the system list API.
        """  # noqa: E501
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
                with_token (bool): If True and the client has an authentication token, include the auth token header.

        Returns:
                headers (dict[str, str]): Mapping of HTTP header names to values. Includes the auth token header when `with_token` is True and a token is present.
        """  # noqa: E501
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

        Returns the input trimmed and lowercased if it matches the expected format.

        Parameters:
            value (str): The input MAC identifier string to normalize.

        Returns:
            str: Normalized MAC identifier (33 lowercase hexadecimal characters).

        Raises:
            JackeryAuthError: If the normalized value does not match 33 lowercase hexadecimal characters.
        """  # noqa: E501
        mac_id = value.strip().lower()
        # App values are 33 hex chars (prefix 2/9 + 32-char UUID-no-dash).
        if not re.fullmatch(r"[0-9a-f]{33}", mac_id):
            raise JackeryAuthError(  # noqa: TRY003
                "Invalid mqtt_mac_id format. Expected 33 lowercase hex chars "
                "(example: 271c55f5731fa3d9ba1fe131e088946e0)."
            )
        return mac_id

    def _resolve_login_mac_id(self) -> str:
        """Resolve the MAC identifier used for login and MQTT username derivation.

        If a configured `mqtt_mac_id` is present and valid, that value is used and
        `self._mqtt_mac_id_source` is set to `"configured"`. If a configured value is
        present but invalid, a deterministic MAC ID derived from the account is returned
        and `self._mqtt_mac_id_source` is set to
        `"generated_fallback_invalid_config"`. If no configured value is provided, a
        deterministic MAC ID derived from the account is returned and
        `self._mqtt_mac_id_source` is set to `"generated"`.

        Returns:
            The resolved MQTT MAC ID string.
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

    async def async_login(self) -> str:
        """Perform the encrypted login flow and persist the returned session and MQTT credentials.

        Constructs an encrypted login payload, POSTs it to the login endpoint, validates the backend response code and JSON shape, and stores the JWT token and MQTT fields on the client instance.

        Returns:
            token (str): The JWT session token returned by the server.

        Raises:
            JackeryAuthError: If the backend rejects credentials, reports a non-OK code, or returns no token.
            JackeryApiError: For network errors, non-200 HTTP responses, or invalid/non-parsable JSON responses.
        """  # noqa: E501
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

        try:  # noqa: PLW0717
            async with self._session.post(
                url,
                data=form_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=LOGIN_TIMEOUT_SEC),
            ) as resp:
                if resp.status != 200:  # noqa: PLR2004
                    raise JackeryApiError(f"Login HTTP {resp.status}")  # noqa: TRY003
                try:
                    data = await resp.json(content_type=None)
                except (
                    aiohttp.ContentTypeError,
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                    ValueError,
                ) as err:
                    raw = (await resp.text())[:HTTP_RAW_TEXT_LIMIT]
                    raise JackeryApiError(  # noqa: TRY003
                        f"Login returned invalid JSON: {raw!r}"
                    ) from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise JackeryApiError(  # noqa: TRY003
                f"Login request failed: {type(err).__name__}: {err or '(no message)'}"
            ) from err

        self.last_login_response = dict(data)
        await self._emit_payload_debug(
            self._http_payload_debug(
                method=HTTP_METHOD_POST,
                path=LOGIN_PATH,
                body={"form_fields": sorted(form_body)},
                status=200,
                response=dict(data),
            )
        )

        if self._extract_code(data) != CODE_OK:
            raise JackeryAuthError(  # noqa: TRY003
                f"Login rejected (code={data.get(FIELD_CODE)}, msg={data.get(FIELD_MSG)})"  # noqa: E501
            )

        token = data.get(FIELD_TOKEN) or ""
        if not token:
            raise JackeryAuthError("Login succeeded but no token returned")  # noqa: TRY003

        self._token = token
        payload = data.get(FIELD_DATA) or {}
        self._mqtt_user_id = str(payload.get(FIELD_USER_ID) or "") or None
        self._mqtt_seed_b64 = payload.get(FIELD_MQTT_PASSWORD) or None
        self._mqtt_mac_id = mac_id
        return token

    async def async_get_mqtt_credentials(self) -> dict[str, str]:
        """Build MQTT client credentials from the active authenticated session.

        Returns:
            dict[str, str]: Mapping with keys:
                - `clientId`: MQTT client identifier.
                - `username`: MQTT username (constructed from the login `userId` and MAC id).
                - `password`: MQTT password: AES-CBC(encrypt(username)) encoded as base64 using the login-derived seed.
                - `userId`: MQTT user id from the login response.

        Raises:
            JackeryAuthError: If authentication has not provided required MQTT fields, if the stored
                MQTT seed is not valid base64, or if the decoded seed is not 32 bytes.
        """  # noqa: E501
        await self._ensure_token()
        if not self._mqtt_user_id or not self._mqtt_seed_b64 or not self._mqtt_mac_id:
            raise JackeryAuthError(  # noqa: TRY003
                "Login response missing MQTT fields (userId/mqttPassWord/macId)"
            )

        try:
            seed = base64.b64decode(self._mqtt_seed_b64, validate=True)
        except (binascii.Error, ValueError) as err:
            raise JackeryAuthError(  # noqa: TRY003
                "Invalid mqttPassWord base64 in login response"
            ) from err
        if len(seed) != 32:  # noqa: PLR2004
            raise JackeryAuthError(  # noqa: TRY003
                f"Unexpected mqttPassWord decoded length: {len(seed)} (expected 32)"
            )

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
        """Identify the source of the current MQTT MAC ID.

        Returns:
            str: One of:
                - "configured" — provided by configuration,
                - "generated" — derived deterministically from the account seed,
                - "generated_fallback_invalid_config" — a configured value existed but was invalid and a deterministic fallback was used.
        """  # noqa: E501
        return self._mqtt_mac_id_source

    @property
    def mqtt_mac_id(self) -> str | None:
        """Return the MQTT MAC ID assigned to the current MQTT session, if available.

        Returns:
            str: 33-character lowercase hexadecimal MQTT MAC ID when known, `None` otherwise.
        """  # noqa: E501
        return self._mqtt_mac_id

    @property
    def region_code(self) -> str | None:
        """Get the region code pinned for HTTP login calls.

        Returns:
            `str` region code if set, `None` otherwise.
        """
        return self._region_code

    async def _ensure_token(self) -> str:
        """Ensure the client holds a valid authentication token, triggering login if necessary.

        Returns:
            str: The current authentication token.

        Raises:
            JackeryAuthError: If a login attempt completes without providing a token.
        """  # noqa: E501
        if self._token is None:
            async with self._lock:
                if self._token is None:
                    await self.async_login()
        if self._token is None:
            raise JackeryAuthError("Login succeeded without returning a token")  # noqa: TRY003
        return self._token

    @staticmethod
    def _extract_code(data: object) -> int | None:  # noqa: PLR0911
        """Extracts the backend numeric `code` value from a parsed API response.

        Parameters:
            data (dict | Any): Parsed JSON response (expected dict) or any other value.

        Returns:
            int | None: The `code` parsed as an integer when present as an integer or a numeric string, `None` otherwise.
        """  # noqa: E501
        if not isinstance(data, dict):
            return None
        code = data.get(FIELD_CODE)
        if code is None:
            return None
        if isinstance(code, int):
            return code
        if isinstance(code, str):
            text = code.strip()
            if not text:
                return None
            try:
                return int(text)
            except ValueError:
                return None
        return None

    def _is_token_expired_response(self, status: int, data: object) -> bool:
        """Determine whether a parsed API response indicates the authentication token has expired.

        Inspects dict-shaped responses: returns `True` if the backend `code` equals the token-expired sentinel or if the response message contains common token-expiry phrases.

        Parameters:
            status (int): HTTP status code from the response (not used for detection but provided for context).
            data (object): Parsed response body (expected to be a dict).

        Returns:
            `True` if the response indicates token expiration, `False` otherwise.
        """  # noqa: E501
        if not isinstance(data, dict):
            return False
        code = self._extract_code(data)
        if code == CODE_TOKEN_EXPIRED:
            return True
        msg = str(data.get(FIELD_MSG) or "").lower()
        return "token expires" in msg or "token expired" in msg

    @staticmethod
    def _response_has_auth_failure_text(data: object) -> bool:
        """Detects whether a backend response contains text indicating an authentication or authorization failure.

        Scans common message fields (`msg`, `message`, `error`, raw text) for known auth-related markers such as "token expired", "unauthorized", "please login", and related terms.

        Returns:
            `True` if any auth-related marker is present in the response text, `False` otherwise.
        """  # noqa: E501
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
        """Detects whether an HTTP response indicates an authentication or authorization failure requiring re-authentication.

        Returns:
            True if the response indicates an auth/authz failure — specifically: HTTP 401 or 403, a token-expired response, a non-200 response containing auth-failure markers, or a 200 response with a backend `code` other than `CODE_OK` (and auth-failure markers); `False` otherwise.
        """  # noqa: E501
        if status in {401, 403}:
            return True
        if self._is_token_expired_response(status, data):
            return True
        if status != 200:  # noqa: PLR2004
            return self._response_has_auth_failure_text(data)
        code = self._extract_code(data)
        return code not in {CODE_OK, None} and self._response_has_auth_failure_text(
            data
        )

    @staticmethod
    def _auth_failure_message(method: str, path: str, status: int, data: dict) -> str:
        """Create a concise authorization-failure message for an HTTP request.

        @returns str: Compact message containing the HTTP method and path, the response status, backend code, and the backend message (suitable for logs without exposing secrets).
        """  # noqa: E501
        code = data.get(FIELD_CODE)
        msg = data.get(FIELD_MSG) or data.get("message") or data.get("error")
        return (
            f"{method} {path} authorization failed: HTTP {status} code={code} msg={msg}"
        )

    async def _emit_payload_debug(
        self,
        event_or_factory: dict[str, Any] | Callable[[], dict[str, Any]],
    ) -> None:
        """Forward a payload-debug event to the configured payload debug callback.

        If no callback is configured this is a no-op. Accepts either a prepared event dict
        or a zero-argument factory that produces the event dict when invoked. If the
        callback returns an awaitable it will be awaited. Exceptions raised by the
        callback are caught and logged at debug level.

        Parameters:
            event_or_factory (dict[str, Any] | Callable[[], dict[str, Any]]):
                Either a prepared event dictionary or a zero-argument factory that
                produces the event dictionary when invoked.
        """  # noqa: E501
        callback = self.payload_debug_callback
        if callback is None:
            return
        try:
            result = callback(event_or_factory)
            if inspect.isawaitable(result):
                await result
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Jackery payload debug logging failed: %s", err)

    @staticmethod
    def _http_payload_debug(  # noqa: PLR0913
        *,
        method: str,
        path: str,
        params: dict | None = None,
        body: dict | None = None,
        status: int | None = None,
        response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Constructs a structured HTTP debug event suitable for later redaction.

        The returned event includes these keys: "kind", "method", "path", "params", "request_body",
        "status", "response", and "response_data_type". If the response payload (extracted as
        response[FIELD_DATA] when `response` is a dict) produces chart-series debug via
        chart_series_debug(payload), the event will also include "chart_series_debug".

        Parameters:
            method (str): HTTP method name used for the request.
            path (str): Request path (API endpoint).
            params (dict | None): Query or path parameters sent with the request.
            body (dict | None): Request body that was sent.
            status (int | None): HTTP response status code, if available.
            response (dict[str, Any] | None): Parsed response object; when a dict, the function
                extracts payload = response[FIELD_DATA] to determine response_data_type and to
                produce optional chart_series_debug.

        Returns:
            dict[str, Any]: A dictionary representing the debug event, containing request/response
            fields and any chart-series debug data when available.
        """  # noqa: E501
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
    def _payload_dict(data: dict[str, Any], path: str) -> dict[str, Any]:
        """Normalize an API response `data` value into a dict payload.

        If `data[FIELD_DATA]` is a dict, that dict is returned. If it is null, an empty
        dict is returned. For any other shape a warning is emitted and an empty dict
        is returned to avoid breaking downstream payload merging.

        Parameters:
            data (dict): Full parsed response object potentially containing `FIELD_DATA`.
            path (str): Request path used for diagnostic logging.

        Returns:
            dict: The normalized dict payload or an empty dict when no suitable dict exists.
        """  # noqa: E501
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

        If the response's FIELD_DATA is a list, returns only the elements that are dictionary objects.
        If FIELD_DATA is None, returns an empty list. For any other shape, logs a warning and returns an empty list.

        Parameters:
            data (dict): Parsed JSON response expected to contain FIELD_DATA.
            path (str): Request path used in warning messages when the payload shape is unexpected.

        Returns:
            list: The items from FIELD_DATA when it is a list (filtered to dict items), or an empty list otherwise.
        """  # noqa: E501
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

    @staticmethod
    def _select_ota_item(
        items: list[dict[str, Any]],
        device_sn: str,
    ) -> dict[str, Any]:
        """Selects the OTA update entry that matches the given device serial number.

        Searches the provided list of OTA item dictionaries for the first item whose `FIELD_DEVICE_SN`
        stringified value equals `device_sn`. If no matching item is found, returns the first item
        in `items` as a fallback; returns an empty dict when `items` is empty.

        Parameters:
            items (list[dict[str, Any]]): List of OTA item dictionaries; each may contain `FIELD_DEVICE_SN`.
            device_sn (str): Device serial number to match (compared as a string).

        Returns:
            dict[str, Any]: The matching OTA item, the first item as a fallback, or `{}` if `items` is empty.
        """  # noqa: E501
        requested_sn = str(device_sn)
        for item in items:
            if str(item.get(FIELD_DEVICE_SN) or "") == requested_sn:
                return item
        return items[0] if items else {}

    # --- generic GET with auto re-login ------------------------------------
    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        """Perform an authenticated GET request to the API path and return the parsed response body.

        Parameters:
            path (str): API path (appended to the base URL).
            params (dict | None): Optional query parameters to include in the request.

        Returns:
            dict: Parsed JSON response body. If the response could not be parsed as JSON, returns a dict containing `FIELD_RAW_TEXT` with the truncated raw response text.

        Raises:
            JackeryAuthError: When the response indicates an authentication/authorization failure (token expiry triggers an automatic re-login attempt before this error is raised).
            JackeryApiError: For network/timeout errors, non-200 HTTP responses, or when the backend `code` is neither `CODE_OK` nor `None`.
        """  # noqa: E501
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        async def _do() -> tuple[int, dict]:
            """Perform an HTTP GET to the resolved URL and return the response status and parsed body.

            The body is the parsed JSON response when JSON decoding succeeds; otherwise the body is a dict containing `FIELD_RAW_TEXT` with the response text truncated to `HTTP_RAW_TEXT_LIMIT`. The returned tuple is `(status_code, body_dict)`.
            """  # noqa: E501
            async with self._session.get(
                url,
                params=params,
                headers=self._headers(with_token=True),
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
            status, data = await _do()
        except (TimeoutError, aiohttp.ClientError) as err:
            self._requests_failed += 1
            if isinstance(err, TimeoutError):
                self._timeouts_total += 1
            raise JackeryApiError(  # noqa: TRY003
                f"{HTTP_METHOD_GET} {path} request failed: "
                f"{type(err).__name__}: {err or '(no message)'}"
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info("Jackery token expired — re-login")
            self._token = None
            self._auth_retries += 1
            await self._ensure_token()
            try:
                status, data = await _do()
            except (TimeoutError, aiohttp.ClientError) as err:
                self._requests_failed += 1
                if isinstance(err, TimeoutError):
                    self._timeouts_total += 1
                raise JackeryApiError(  # noqa: TRY003
                    f"{HTTP_METHOD_GET} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or '(no message)'}"
                ) from err

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_GET, path, status, data)
            )
        if status != 200:  # noqa: PLR2004
            raise JackeryApiError(f"{HTTP_METHOD_GET} {path} HTTP {status}")  # noqa: TRY003
        code = self._extract_code(data)
        if code not in {CODE_OK, None}:
            raise JackeryApiError(  # noqa: TRY003
                f"{HTTP_METHOD_GET} {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)}"  # noqa: E501
            )
        await self._emit_payload_debug(
            self._http_payload_debug(
                method=HTTP_METHOD_GET,
                path=path,
                params=params,
                status=status,
                response=data,
            )
        )
        return data

    # --- confirmed endpoints -----------------------------------------------
    async def async_get_system_list(self) -> list[dict[str, Any]]:
        """Fetch the list of systems and their devices from the cloud.

        May update the client's region code by inferring it from the first system with a non-empty countryCode.

        Returns:
            list[dict]: A list of system objects as returned by the backend; each system typically contains fields such as `id`, `systemName`, `deviceName`, `countryCode`, `currency`, `timezone`, and a `devices` list of device objects.
        """  # noqa: E501
        data = await self._get_json(SYSTEM_LIST_PATH)
        self.last_system_list_response = data
        systems = self._payload_list(data, SYSTEM_LIST_PATH)
        self._maybe_learn_region_code(systems)
        return systems

    async def async_get_device_property(self, device_id: str | int) -> dict:
        """Fetches the device properties for the given device identifier.

        Parameters:
            device_id (str | int): Device identifier; will be stringified for the request.

        Returns:
            dict: The device properties dictionary extracted from the response. Returns an empty dict if the response `data` field is missing or is not a dict.
        """  # noqa: E501
        data = await self._get_json(
            DEVICE_PROPERTY_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_property_responses[str(device_id)] = data
        return self._payload_dict(data, DEVICE_PROPERTY_PATH)

    async def async_get_alarm(self, system_id: str | int) -> Any:  # noqa: ANN401  # parsed JSON response, indexed by callers
        """Fetches the alarm list for the specified system.

        Stores the raw parsed response in `self.last_alarm_response`.

        Returns:
            The backend `data` field (commonly a list of alarm dictionaries) or `None` if the field is absent.
        """  # noqa: E501
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
        """Return current-day energy-flow statistics for the specified device.

        The result is a mapping of metric keys to their values as numeric strings representing kilowatt-hours (kWh). Available keys vary by device and backend response; examples include `pvEgy`, `inEpsEgy`, `ongridOtBatEgy`, `pvOtBatEgy`, `inOngridEgy`, `outOngridEgy`, `batOtGridEgy`, `outEpsEgy`, `batDisChgEgy`, `acOtBatEgy`, `batOtAcEgy`, and `batChgEgy`.

        Parameters:
            device_id (str | int): Device identifier (deviceId) to query.

        Returns:
            dict: Mapping from metric key (str) to its value as a string in kWh.
        """  # noqa: E501
        data = await self._get_json(
            DEVICE_STATISTIC_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_device_statistic_responses[str(device_id)] = data
        return self._payload_dict(data, DEVICE_STATISTIC_PATH)

    async def _async_get_device_period_stat(  # noqa: PLR0913
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
        self.last_device_period_stat_responses[f"{path}:{device_id}:{date_type}"] = data
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
        """Fetch on-grid (home) statistics for the specified device and period.

        Returns:
            Normalized response payload dict containing chart/statistics data. When present, `APP_REQUEST_META` contains the request metadata for the query (excluding `deviceId`).
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
        """Retrieve CT (smart-meter) statistics for the given device and period.

        Parameters:
            device_id: Identifier sent as the `deviceId` query parameter.
            date_type: Period type for the chart (e.g., day or month).
            begin_date: Optional start date for the period (ISO-like string).
            end_date: Optional end date for the period (ISO-like string).

        Returns:
            A dictionary containing the parsed CT/smart-meter statistics payload. May include `APP_REQUEST_META` with the request parameters when a date range is supplied.
        """  # noqa: E501
        return await self._async_get_device_period_stat(
            DEVICE_CT_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_device_eps_stat(
        self,
        device_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve EPS (off-grid) energy input/output statistics for a device over a specified period.

        The returned payload includes aggregated totals (for example `totalInEpsEnergy`, `totalOutEpsEnergy`), time-series chart arrays (`x`, `y`, `y1`, `y2`), and may include an `APP_REQUEST_META` dict with the request parameters used.

        Returns:
            dict: Parsed backend response containing `data` with aggregates and series; may include `APP_REQUEST_META`.
        """  # noqa: E501
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
            device_sn (str): Device serial number; sent as the `deviceSn` query parameter.

        Returns:
            dict: Parsed JSON response containing KPI fields such as `de` (feed-in), `dg` (grid import), `dh` (home load), and `ds` (battery energy).
        """  # noqa: E501
        data = await self._get_json(
            DEVICE_TODAY_ENERGY_PATH,
            params={FIELD_DEVICE_SN: str(device_sn)},
        )
        return self._payload_dict(data, DEVICE_TODAY_ENERGY_PATH)

    async def async_get_device_meter_stat(
        self,
        device_id: str | int,
    ) -> dict[str, Any]:
        """Get smart-meter (CT accessory) panel totals for the specified device.

        Parameters:
            device_id (str | int): Smart-Meter / CT accessory `deviceId` (not the SolarVault main deviceId).

        Returns:
            dict: Parsed payload containing the meter panel totals returned by the device meter statistics endpoint.
        """  # noqa: E501
        data = await self._get_json(
            DEVICE_METER_STAT_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_device_period_stat_responses[
            f"{DEVICE_METER_STAT_PATH}:{device_id}:panel"
        ] = data
        return self._payload_dict(data, DEVICE_METER_STAT_PATH)

    async def async_get_device_socket_statistic(
        self,
        smart_socket_id: str | int,
    ) -> dict[str, Any]:
        """Get socket panel totals for the specified smart socket.

        Returns:
            The response `data` payload as a dict; an empty dict if the payload is missing or not a dict.
        """  # noqa: E501
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

        If the returned payload is non-empty, it will include `APP_REQUEST_META` containing the request parameters (`dateType`, `beginDate`, `endDate`) used to produce the chart (excluding `deviceId`/`systemId`).

        Returns:
            dict: The normalized `data` payload for the device socket chart.
        """  # noqa: E501
        return await self._async_get_device_period_stat(
            DEVICE_SOCKET_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_shelly_devices(self) -> list[dict[str, Any]]:
        """Retrieve a normalized list of Shelly devices linked to the account.

        Accepts multiple backend response shapes for the `data` field: a list of device dicts; a dict containing `boundDevices` or `devices` lists; or a single device dict identified by `deviceId`. Non-dict entries are ignored.

        Returns:
            A list of Shelly device objects; empty list if none are present.
        """  # noqa: E501
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
            dict: The response `data` object parsed as a dictionary (empty dict if the payload is missing or not a dict).
        """  # noqa: E501
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
        """Send a Shelly control command for the specified device.

        Parameters:
            device_id (str | int): Identifier of the Shelly device to control.
            action (str): Action name to perform (as provided by the app).
            function (str): Function name associated with the action (as provided by the app).
            control_allowed (bool): If `False`, the call will raise a `JackeryApiError` and no command will be sent.

        Returns:
            bool: `true` if the backend indicates the control request was accepted, `false` otherwise.

        Raises:
            JackeryApiError: If `control_allowed` is `False` or the API reports an authentication/authorization error.
        """  # noqa: E501
        if not control_allowed:
            raise JackeryApiError("Shelly control is not allowed for this device")  # noqa: TRY003
        data = await self._post_form(
            SHELLY_CONTROL_PATH,
            {
                FIELD_DEVICE_ID: str(device_id),
                FIELD_ACTION: str(action),
                FIELD_FUNCTION: str(function),
            },
        )
        return bool(data.get(FIELD_DATA, True))

    async def async_get_shelly_auth_url(self) -> dict[str, Any]:
        """Retrieve the Shelly OAuth authorization URL and accompanying state for the redirect flow.

        Returns:
            dict: Contains `authUrl` (str) and `state` (str) for the Shelly OAuth redirect flow.
        """  # noqa: E501
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
        return bool(data.get(FIELD_DATA, True))

    async def async_unbind_shelly_account(self) -> bool:
        """Unbinds the Shelly account associated with the current user.

        Returns:
            True if the account unbind succeeded, False otherwise.
        """
        data = await self._post_form(SHELLY_UNBIND_ACCOUNT_PATH, {})
        return bool(data.get(FIELD_DATA, True))

    async def async_get_shelly_binding_failures(
        self,
        state: str = "",
    ) -> dict[str, Any]:
        """Retrieve a summary of Shelly binding failures.

        Parameters:
            state (str): Optional state filter to narrow the binding failures query.

        Returns:
            dict: Response payload containing `bindCount` (int), `failedDeviceSns` (list[str]), and `successDeviceSns` (list[str]).
        """  # noqa: E501
        params: dict[str, str] = {}
        if state:
            params["state"] = state
        data = await self._get_json(SHELLY_BINDING_FAILURES_PATH, params=params)
        return self._payload_dict(data, SHELLY_BINDING_FAILURES_PATH)

    async def async_get_battery_pack_list(self, device_sn: str) -> list[dict[str, Any]]:
        """Get a normalized list of battery pack dictionaries for the given device serial number.

        The raw parsed API response is saved to self.last_battery_pack_responses[device_sn]. Handles multiple backend response shapes and returns an empty list when no pack data is found.

        Parameters:
            device_sn (str): Device serial number to query.

        Returns:
            list[dict]: Battery pack dictionaries extracted from the response; empty list if no packs are found or the response shape is unrecognized.
        """  # noqa: E501
        data = await self._get_json(
            BATTERY_PACK_PATH, params={FIELD_DEVICE_SN: str(device_sn)}
        )
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

    async def async_get_home_trends(
        self,
        system_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """GET /v1/device/stat/sys/home/trends — home consumption breakdown."""
        begin_date, end_date = app_period_date_bounds(
            date_type, begin_date=begin_date, end_date=end_date
        )
        params = {
            FIELD_SYSTEM_ID: str(system_id),
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        data = await self._get_json(HOME_TRENDS_PATH, params=params)
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
    ) -> dict:
        """Retrieve battery charge and discharge trends for the given system.

        If the returned payload is non-empty, attaches request metadata under `APP_REQUEST_META`
        containing the request's `dateType`, `beginDate`, and `endDate`.

        Returns:
            dict: Normalized payload dictionary extracted from the API response (may be empty).
        """  # noqa: E501
        begin_date, end_date = app_period_date_bounds(
            date_type, begin_date=begin_date, end_date=end_date
        )
        params = {
            FIELD_SYSTEM_ID: str(system_id),
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        data = await self._get_json(BATTERY_TRENDS_PATH, params=params)
        payload = self._payload_dict(data, BATTERY_TRENDS_PATH)
        if payload:
            payload.setdefault(
                APP_REQUEST_META,
                {k: v for k, v in params.items() if k != FIELD_SYSTEM_ID},
            )
        return payload

    async def async_get_ota_info(self, device_sn: str) -> dict:
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
        """Retrieve the GPS coordinates previously set for the specified device.

        Returns:
            dict: The API payload's `data` object containing location fields (e.g., `latitude`, `longitude`); an empty dict if `data` is missing or not a dict.
        """  # noqa: E501
        data = await self._get_json(
            LOCATION_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_location_responses[str(device_id)] = data
        return self._payload_dict(data, LOCATION_PATH)

    # --- HTTP write endpoints documented in PROTOCOL.md §2 --------------
    async def _put_json(self, path: str, payload: dict) -> dict:
        """Send a JSON PUT to the given API path, ensuring a valid auth token and retrying once after re-login if the token has expired.

        If the response body cannot be parsed as JSON, the returned dict contains `FIELD_RAW_TEXT` with truncated raw text.

        Returns:
            dict: Parsed response JSON, or a dict containing `FIELD_RAW_TEXT` when JSON parsing failed.

        Raises:
            JackeryAuthError: When the response indicates an authorization or authentication failure.
            JackeryApiError: On network/request failures, non-200 HTTP status, or backend `code` that is neither `CODE_OK` nor `None`.
        """  # noqa: E501
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        def _request_headers() -> dict[str, str]:
            """Builds HTTP request headers for JSON requests, including the authorization token when present.

            Returns:
                dict[str, str]: HTTP header mapping with Content-Type set to "application/json" and, if available, the authentication token header.
            """  # noqa: E501
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_JSON
            return headers

        async def _do() -> tuple[int, dict]:
            """Execute an HTTP PUT to the enclosing `url` with `payload` and return the response status and body.

            If the response contains valid JSON, `body` is the parsed JSON object. If JSON decoding or content-type parsing fails, `body` is a dict with a single key `FIELD_RAW_TEXT` whose value is the response text truncated to `HTTP_RAW_TEXT_LIMIT` characters.

            Returns:
                tuple[int, dict]: `(status, body)` where `status` is the HTTP status code and `body` is either the parsed response dict or a dict containing `FIELD_RAW_TEXT` on parse failure.
            """  # noqa: E501
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
            status, data = await _do()
        except (TimeoutError, aiohttp.ClientError) as err:
            self._requests_failed += 1
            if isinstance(err, TimeoutError):
                self._timeouts_total += 1
            raise JackeryApiError(  # noqa: TRY003
                f"{HTTP_METHOD_PUT} {path} request failed: "
                f"{type(err).__name__}: {err or '(no message)'}"
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info(
                "Jackery token expired — re-login for %s %s", HTTP_METHOD_PUT, path
            )
            self._token = None
            self._auth_retries += 1
            await self._ensure_token()
            try:
                status, data = await _do()
            except (TimeoutError, aiohttp.ClientError) as err:
                self._requests_failed += 1
                if isinstance(err, TimeoutError):
                    self._timeouts_total += 1
                raise JackeryApiError(  # noqa: TRY003
                    f"{HTTP_METHOD_PUT} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or '(no message)'}"
                ) from err

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_PUT, path, status, data)
            )
        if status != 200:  # noqa: PLR2004
            raise JackeryApiError(f"{HTTP_METHOD_PUT} {path} HTTP {status}")  # noqa: TRY003
        code = self._extract_code(data)
        if code not in {CODE_OK, None}:
            raise JackeryApiError(  # noqa: TRY003
                f"{HTTP_METHOD_PUT} {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)}"  # noqa: E501
            )
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
        if not system_name or not system_name.strip():
            raise JackeryApiError("system_name must be a non-empty string")  # noqa: TRY003
        data = await self._put_json(
            SYSTEM_NAME_PATH,
            {FIELD_SYSTEM_NAME: system_name.strip(), FIELD_ID: str(system_id)},
        )
        return bool(data.get(FIELD_DATA))

    # ------------------------------------------------------------------
    # Experimental app-captured writers
    # ------------------------------------------------------------------
    # These endpoints were discovered via PCAPdroid captures but only failed
    # responses have been seen so far. They're kept as best-effort helpers;
    # the integration surfaces the server's full error response so the user
    # can troubleshoot. See const.py for caveats.

    async def _post_form(self, path: str, fields: dict[str, Any]) -> dict:
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
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_FORM
            return headers

        body = {k: str(v) for k, v in fields.items()}

        async def _do() -> tuple[int, dict]:
            """Perform an HTTP POST and return the HTTP status and parsed response body.

            Returns:
                tuple[int, dict]: A tuple `(status_code, data)` where `status_code` is the HTTP status code and `data` is the parsed JSON response; if the response cannot be parsed as JSON, `data` is a dict containing `FIELD_RAW_TEXT` with the truncated raw response text.
            """  # noqa: E501
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

        self._requests_total += 1
        try:
            status, data = await _do()
        except (TimeoutError, aiohttp.ClientError) as err:
            self._requests_failed += 1
            if isinstance(err, TimeoutError):
                self._timeouts_total += 1
            raise JackeryApiError(  # noqa: TRY003
                f"{HTTP_METHOD_POST} {path} request failed: "
                f"{type(err).__name__}: {err or '(no message)'}"
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info(
                "Jackery token expired — re-login for %s %s", HTTP_METHOD_POST, path
            )
            self._token = None
            self._auth_retries += 1
            await self._ensure_token()
            try:
                status, data = await _do()
            except (TimeoutError, aiohttp.ClientError) as err:
                self._requests_failed += 1
                if isinstance(err, TimeoutError):
                    self._timeouts_total += 1
                raise JackeryApiError(  # noqa: TRY003
                    f"{HTTP_METHOD_POST} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or '(no message)'}"
                ) from err

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_POST, path, status, data)
            )
        if status != 200:  # noqa: PLR2004
            raise JackeryApiError(f"{HTTP_METHOD_POST} {path} HTTP {status}")  # noqa: TRY003
        code = self._extract_code(data)
        if code not in {CODE_OK, None}:
            # Surface the whole response so callers can show it to the user
            raise JackeryApiError(  # noqa: TRY003
                f"{HTTP_METHOD_POST} {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)!r} "  # noqa: E501
                f"data={data.get(FIELD_DATA)!r}"
            )
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
        """Set the device's maximum allowed power using the experimental max-power endpoint.

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
            raise JackeryApiError("max_power must be a non-negative integer")  # noqa: TRY003
        data = await self._post_form(
            MAX_POWER_SAVE_PATH,
            {FIELD_MAX_POWER: max_power, FIELD_DEVICE_ID: str(device_id)},
        )
        return bool(data.get(FIELD_DATA))

    async def async_set_single_mode(
        self,
        *,
        system_id: str | int,
        single_price: float | str,
        currency: str,
    ) -> bool:
        """Set the system's fixed electricity price used when the system is configured for single (fixed) pricing.

        Parameters:
            system_id (str | int): Identifier of the system to configure.
            single_price (float | str): Price value greater than or equal to 0; will be formatted to at most four decimal places before sending.
            currency (str): Non-empty currency code or label.

        Returns:
            `true` if the backend indicates the change was accepted, `false` otherwise.

        Raises:
            JackeryApiError: If `single_price` is negative or `currency` is empty, or when the API call fails.
        """  # noqa: E501
        price = float(single_price)
        if price < 0:
            raise JackeryApiError("single_price must be >= 0")  # noqa: TRY003
        cur = str(currency or "").strip()
        if not cur:
            raise JackeryApiError("currency must be a non-empty string")  # noqa: TRY003
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
        # Endpoint can return bool or null-like payload depending on backend.
        return bool(data.get(FIELD_DATA, True))

    async def async_set_dynamic_mode(
        self,
        *,
        system_id: str | int,
        platform_company_id: int,
        system_region: str,
    ) -> bool:
        """Enable or update dynamic pricing mode for a system.

        Parameters:
            system_id: Identifier of the target system.
            platform_company_id: Platform company identifier required by the API.
            system_region: Region code for the system; must be a non-empty string.

        Returns:
            True if the change was accepted by the server, False otherwise.

        Raises:
            JackeryApiError: If `system_region` is empty.
        """
        region = str(system_region or "").strip()
        if not region:
            raise JackeryApiError("system_region must be a non-empty string")  # noqa: TRY003
        data = await self._post_form(
            SAVE_DYNAMIC_MODE_PATH,
            {
                FIELD_SYSTEM_ID: str(system_id),
                FIELD_PLATFORM_COMPANY_ID: int(platform_company_id),
                FIELD_SYSTEM_REGION: region,
            },
        )
        return bool(data.get(FIELD_DATA, True))

    # --- legacy fallback ----------------------------------------------------
    async def async_list_devices_legacy(self) -> list[dict[str, Any]]:
        """Fetches the legacy device bind list used by Explorer-series devices.

        Propagates authentication failures so callers can handle re-authentication; for other API errors returns an empty list.

        Returns:
            list[dict[str, Any]]: Device objects parsed from the response, or an empty list if a non-auth `JackeryError` occurred.
        """  # noqa: E501
        try:
            data = await self._get_json(DEVICE_LIST_PATH)
        except JackeryAuthError:
            raise
        except JackeryError:
            return []
        return self._payload_list(data, DEVICE_LIST_PATH)

    # --- restored from 1\client\api.py ---
    def hydrate_mqtt_session(
        self,
        *,
        user_id: str,
        seed_b64: str,
        mac_id: str,
        mac_id_source: str | None = None,
    ) -> None:
        """Restore MQTT session fields from a cached login without a cloud call.

        The MQTT password is derived locally from ``user_id``, ``mac_id`` and
        ``seed_b64`` (the broker validates against the AES-derived ciphertext,
        not against the REST JWT). Hydrating these fields at setup time lets
        the coordinator (re-)open the broker session while the Jackery cloud is
        unavailable.
        """
        self._mqtt_user_id = user_id or None
        self._mqtt_seed_b64 = seed_b64 or None
        self._mqtt_mac_id = mac_id or None
        if mac_id_source:
            self._mqtt_mac_id_source = mac_id_source

    def mqtt_session_snapshot(self) -> dict[str, str] | None:
        """Provide a snapshot of the current MQTT session suitable for persistence.

        Returns:
            A dict with keys `userId`, `seed_b64`, `mac_id`, and `mac_id_source` when a complete session exists; `None` until the required `userId`, `seed_b64`, and `mac_id` fields are all populated.
        """  # noqa: E501
        if not (self._mqtt_user_id and self._mqtt_seed_b64 and self._mqtt_mac_id):
            return None
        return {
            MQTT_SESSION_USER_ID: self._mqtt_user_id,
            MQTT_SESSION_SEED_B64: self._mqtt_seed_b64,
            MQTT_SESSION_MAC_ID: self._mqtt_mac_id,
            MQTT_SESSION_MAC_ID_SOURCE: self._mqtt_mac_id_source,
        }

    def diagnostics_snapshot(self) -> dict[str, Any]:
        """Provide in-memory transport counters for the HTTP API diagnostic sensor.

        Counters are stored in memory and are reset when Home Assistant restarts (same lifecycle as the BLE transport sensor).

        Returns:
            dict[str, int]: Mapping with keys:
                - "requests_total": total requests attempted
                - "requests_failed": total requests that failed
                - "timeouts_total": total request timeouts
                - "auth_retries": total authentication retry attempts
        """  # noqa: E501
        return {
            "requests_total": self._requests_total,
            "requests_failed": self._requests_failed,
            "timeouts_total": self._timeouts_total,
            "auth_retries": self._auth_retries,
        }

    @staticmethod
    def _response_with_request_context(
        data: dict[str, Any],
        *,
        path: str,
        params: dict[str, str],
        payload_request: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Ensure the response contains request context metadata for diagnostics.

        If the top-level response lacks APP_REQUEST_META, this function sets it to a dict with `path` and a copy of `params`. If `FIELD_DATA` is a dict and `payload_request` is provided, the function ensures `FIELD_DATA[APP_REQUEST_META]` exists and contains a copy of `payload_request`.

        Parameters:
            data (dict[str, Any]): The raw response object (not modified).
            path (str): API request path to record.
            params (dict[str, str]): Query or form parameters to record.
            payload_request (dict[str, str] | None): Optional per-payload request metadata to embed inside `FIELD_DATA`.

        Returns:
            dict[str, Any]: A response dictionary mirroring `data` with ensured `APP_REQUEST_META` at the top level and, when applicable, inside `FIELD_DATA`.
        """  # noqa: E501
        response = dict(data)
        response.setdefault(APP_REQUEST_META, {"path": path, "params": dict(params)})
        payload = response.get(FIELD_DATA)
        if isinstance(payload, dict) and payload_request is not None:
            payload = dict(payload)
            payload.setdefault(APP_REQUEST_META, dict(payload_request))
            response[FIELD_DATA] = payload
        return response


def _write_accepted(data: dict[str, Any]) -> bool:
    """Indicates whether an API write response was accepted.

    Parameters:
        data (dict): Parsed JSON response from the API; the function inspects the response's `data` field.

    Returns:
        True if the response's `data` field is not explicitly `False`, False otherwise.
    """  # noqa: E501
    return safe_bool(data.get(FIELD_DATA)) is not False
