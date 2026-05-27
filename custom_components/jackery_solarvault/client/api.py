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
from collections.abc import Awaitable, Callable
import hashlib
import inspect
import json
import logging
import re
from typing import Any
import uuid

import aiohttp
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.serialization import load_der_public_key

from ..const import (
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
    DEVICE_HOME_STAT_PATH,
    DEVICE_LIST_PATH,
    DEVICE_METER_STAT_PATH,
    DEVICE_MODEL_HEADER,
    DEVICE_PROPERTY_PATH,
    DEVICE_PV_STAT_PATH,
    DEVICE_SOCKET_STAT_PATH,
    DEVICE_SOCKET_STATISTIC_PATH,
    DEVICE_STATISTIC_PATH,
    FIELD_ACCOUNT,
    FIELD_BAT_SOC,
    FIELD_BATTERY_PACKS,
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
    MQTT_CREDENTIAL_USER_ID,
    MQTT_CREDENTIAL_USERNAME,
    MQTT_MAC_ID_PREFIX,
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
    SYS_VERSION,
    SYSTEM_LIST_PATH,
    SYSTEM_NAME_PATH,
    SYSTEM_STATISTIC_PATH,
    USER_AGENT,
)
from ..util import app_period_date_bounds, chart_series_debug

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
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def _rsa_pkcs1v15_encrypt(data: bytes, public_key_b64: str) -> bytes:
    der_bytes = base64.b64decode(public_key_b64)
    public_key = load_der_public_key(der_bytes)
    return public_key.encrypt(data, asym_padding.PKCS1v15())


def _generate_udid(seed: str) -> str:
    """Derives a deterministic MQTT MAC identifier from the given seed.

    Parameters:
        seed (str): Input seed string used to derive the identifier.

    Returns:
        mqtt_mac_id (str): The MQTT MAC id formed by concatenating `MQTT_MAC_ID_PREFIX` with a UUIDv3 generated from the MD5 digest of `seed`, with dashes removed.
    """
    md5_digest = hashlib.md5(seed.encode('utf-8')).digest()
    u = uuid.UUID(bytes=md5_digest, version=3)
    return MQTT_MAC_ID_PREFIX + str(u).replace('-', '')


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class JackeryApi:
    """Async client for the Jackery SolarVault cloud."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        account: str,
        password: str,
        mqtt_mac_id: str | None = None,
        region_code: str | None = None,
    ) -> None:
        """Initialize the JackeryApi client with a pre-existing aiohttp session and account credentials.

        Parameters:
            session (aiohttp.ClientSession): Reused HTTP session for all requests.
            account (str): Account identifier (used for login and UDID generation when MQTT MAC id is not configured).
            password (str): Account password used for login.
            mqtt_mac_id (str | None): Optional configured MQTT MAC id; when omitted a UDID-based MAC id will be generated during login. The provided value is validated later.
            region_code (str | None): Optional region/country code; stored as an uppercase string or `None` when empty.

        Description:
            Stores the provided inputs, initializes token/mqtt-related caches and an asyncio.Lock for serialized login, and creates diagnostic response buffers and an optional payload debug callback slot.
        """
        self._session = session
        self._account = account
        self._password = password
        self._region_code = (region_code or '').strip().upper() or None
        self._mqtt_mac_id_configured = mqtt_mac_id
        self._mqtt_mac_id_source = 'generated'
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

    def _maybe_learn_region_code(self, systems: list[dict[str, Any]]) -> None:
        """Set the client's region code from the first system entry that provides a country code when not already configured.

        If the client already has a region code configured, this function does nothing. Otherwise it iterates the provided system dictionaries, looks for a non-empty `FIELD_COUNTRY_CODE`, normalizes it to uppercase, assigns it to `self._region_code`, logs the inference at debug level, and stops after the first match.

        Parameters:
            systems (list[dict[str, Any]]): List of system metadata dictionaries; expected to contain `FIELD_COUNTRY_CODE`.
        """
        if self._region_code:
            return
        for item in systems:
            country = str(item.get(FIELD_COUNTRY_CODE) or '').strip().upper()
            if not country:
                continue
            self._region_code = country
            _LOGGER.debug(
                'Jackery: inferred regionCode=%s from /v1/device/system/list',
                country,
            )
            return

    # --- headers ------------------------------------------------------------
    def _headers(self, *, with_token: bool = False) -> dict[str, str]:
        """Build request headers that mirror the Android app's expected HTTP headers.

        Parameters:
            with_token (bool): If True and a token is cached on the client, include the authentication token header.

        Returns:
            dict[str, str]: Mapping of header names to header values to be sent with HTTP requests.
        """
        h = {
            'accept-encoding': 'gzip',
            'accept-language': 'de-DE',
            'app_version': APP_VERSION,
            'app_version_code': APP_VERSION_CODE,
            'connection': 'Keep-Alive',
            'host': 'iot.jackeryapp.com',
            FIELD_MODEL: DEVICE_MODEL_HEADER,
            'network': 'wifi',
            'platform': PLATFORM_HEADER,
            'sys_version': SYS_VERSION,
            'user-agent': USER_AGENT,
        }
        if with_token and self._token:
            h[FIELD_TOKEN] = self._token
        return h

    # --- auth ---------------------------------------------------------------
    @staticmethod
    def _normalize_mqtt_mac_id(value: str) -> str:
        """Normalize and validate an app-style MQTT macId token.

        Strips surrounding whitespace, lowercases the input, and ensures it is exactly 33 lowercase hexadecimal characters (a 1-hex prefix plus a 32-character UUID with no dashes).

        Parameters:
            value (str): The macId string to normalize.

        Returns:
            str: Normalized macId (33 lowercase hex characters).

        Raises:
            JackeryAuthError: If the input does not match the required 33-character lowercase hex format.
        """
        mac_id = value.strip().lower()
        # App values are 33 hex chars (prefix 2/9 + 32-char UUID-no-dash).
        if not re.fullmatch(r'[0-9a-f]{33}', mac_id):
            raise JackeryAuthError(
                'Invalid mqtt_mac_id format. Expected 33 lowercase hex chars '
                '(example: 271c55f5731fa3d9ba1fe131e088946e0).'
            )
        return mac_id

    def _resolve_login_mac_id(self) -> str:
        """Resolve the MQTT MAC identifier to use for login and MQTT username derivation.

        Sets self._mqtt_mac_id_source to indicate whether the returned value came from the configured
        setting ("configured"), was generated because no configured value was provided ("generated"),
        or was generated after ignoring an invalid configured value ("generated_fallback_invalid_config").

        Returns:
            str: The normalized configured MAC id if valid, otherwise a generated UDID string.
        """
        configured = self._mqtt_mac_id_configured
        if configured:
            try:
                mac_id = self._normalize_mqtt_mac_id(configured)
            except JackeryAuthError as err:
                _LOGGER.warning(
                    'Ignoring invalid configured mqtt_mac_id (%s); '
                    'falling back to generated value',
                    err,
                )
                self._mqtt_mac_id_source = 'generated_fallback_invalid_config'
            else:
                self._mqtt_mac_id_source = 'configured'
                return mac_id
        # Fallback for headless environments without Android ID access.
        self._mqtt_mac_id_source = 'generated'
        return _generate_udid(self._account)

    async def async_login(self) -> str:
        """Authenticate with the Jackery API using the app's AES/RSA hybrid login flow and cache session credentials.

        Performs an encrypted login request, validates the response, caches the returned JWT token and MQTT-related fields (user id, seed, and mac id), and returns the token.

        Returns:
            str: JWT session token.

        Raises:
            JackeryApiError: On HTTP failures, request/timeout errors, non-200 responses, or invalid JSON payloads.
            JackeryAuthError: When the server rejects authentication or no token is returned.
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

        plaintext = json.dumps(login_bean, ensure_ascii=False).encode('utf-8')
        aes_blob = base64.b64encode(_aes_ecb_encrypt(plaintext, AES_KEY)).decode(
            'ascii'
        )
        rsa_blob = base64.b64encode(
            _rsa_pkcs1v15_encrypt(AES_KEY, RSA_PUBLIC_KEY_B64)
        ).decode('ascii')

        url = f"{BASE_URL}{LOGIN_PATH}"

        # The Android app sends login params as form-urlencoded body, not as
        # query string. This matches the captured traffic byte-for-byte.
        headers = self._headers()
        headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_FORM
        form_body = {'aesEncryptData': aes_blob, 'rsaForAesKey': rsa_blob}

        try:
            async with self._session.post(
                url,
                data=form_body,
                headers=headers,
                timeout=LOGIN_TIMEOUT_SEC,
            ) as resp:
                if resp.status != 200:
                    raise JackeryApiError(f"Login HTTP {resp.status}")
                try:
                    data = await resp.json(content_type=None)
                except (
                    aiohttp.ContentTypeError,
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                    ValueError,
                ) as err:
                    raw = (await resp.text())[:HTTP_RAW_TEXT_LIMIT]
                    raise JackeryApiError(
                        f"Login returned invalid JSON: {raw!r}"
                    ) from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise JackeryApiError(
                f"Login request failed: {type(err).__name__}: {err or '(no message)'}"
            ) from err

        self.last_login_response = dict(data)
        await self._emit_payload_debug(
            lambda: self._http_payload_debug(
                method=HTTP_METHOD_POST,
                path=LOGIN_PATH,
                body={'form_fields': sorted(form_body)},
                status=200,
                response=dict(data),
            )
        )

        if self._extract_code(data) != CODE_OK:
            raise JackeryAuthError(
                f"Login rejected (code={data.get(FIELD_CODE)}, msg={data.get(FIELD_MSG)})"
            )

        token = data.get(FIELD_TOKEN) or ''
        if not token:
            raise JackeryAuthError('Login succeeded but no token returned')

        self._token = token
        payload = data.get(FIELD_DATA) or {}
        self._mqtt_user_id = str(payload.get(FIELD_USER_ID) or '') or None
        self._mqtt_seed_b64 = payload.get(FIELD_MQTT_PASSWORD) or None
        self._mqtt_mac_id = mac_id
        return token

    async def async_get_mqtt_credentials(self) -> dict[str, str]:
        """Builds MQTT client credentials from the active REST login session.

        Validates required login-derived fields, decodes and verifies the MQTT seed, and derives the MQTT client id, username, and MQTT password.

        Raises:
            JackeryAuthError: If required MQTT fields are missing, the seed is not valid base64, or the decoded seed length is not 32 bytes.

        Returns:
            dict[str, str]: A mapping containing:
                - clientId: MQTT client identifier.
                - username: MQTT username.
                - password: MQTT password (base64-encoded ciphertext).
                - userId: The MQTT user id from the login response.
        """
        await self._ensure_token()
        if not self._mqtt_user_id or not self._mqtt_seed_b64 or not self._mqtt_mac_id:
            raise JackeryAuthError(
                'Login response missing MQTT fields (userId/mqttPassWord/macId)'
            )

        try:
            seed = base64.b64decode(self._mqtt_seed_b64, validate=True)
        except (binascii.Error, ValueError) as err:
            raise JackeryAuthError(
                'Invalid mqttPassWord base64 in login response'
            ) from err
        if len(seed) != 32:
            raise JackeryAuthError(
                f"Unexpected mqttPassWord decoded length: {len(seed)} (expected 32)"
            )

        client_id = (
            f"{self._mqtt_user_id}{MQTT_USERNAME_SEPARATOR}{MQTT_CLIENT_ID_SUFFIX}"
        )
        username = f"{self._mqtt_user_id}{MQTT_USERNAME_SEPARATOR}{self._mqtt_mac_id}"
        encrypted = _aes_cbc_encrypt(
            username.encode('utf-8'),
            key=seed,
            iv=seed[:16],
        )
        password = base64.b64encode(encrypted).decode('ascii')
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
        """Return the source identifier for the current MQTT MAC ID (login vs cached)."""
        return self._mqtt_mac_id_source

    @property
    def mqtt_mac_id(self) -> str | None:
        """Return the MAC ID assigned to this MQTT session by login."""
        return self._mqtt_mac_id

    async def _ensure_token(self) -> str:
        """Ensure a valid authentication token is available, triggering a login if necessary.

        If no token is present, acquires the client lock and calls `async_login()` to obtain one. Raises an error when login completes without producing a token.

        Returns:
            The active JWT authentication token.

        Raises:
            JackeryAuthError: If no token is available after attempting login.
        """
        if self._token is None:
            async with self._lock:
                if self._token is None:
                    await self.async_login()
        if self._token is None:
            raise JackeryAuthError('Login succeeded without returning a token')
        return self._token

    @staticmethod
    def _extract_code(data: dict[str, Any] | Any) -> int | None:
        """Normalize API `code` to int when possible."""
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

    def _is_token_expired_response(
        self, status: int, data: dict[str, Any] | Any
    ) -> bool:
        """Determine whether a response indicates an expired authentication token.

        Parameters:
            status (int): HTTP status code returned by the backend.
            data (dict | Any): Parsed response payload (typically a dict) or raw value; function treats non-dict values as not indicating token expiry.

        Returns:
            bool: `True` if the response payload signals that the authentication token has expired, `False` otherwise.
        """
        if not isinstance(data, dict):
            return False
        code = self._extract_code(data)
        if code == CODE_TOKEN_EXPIRED:
            return True
        msg = str(data.get(FIELD_MSG) or '').lower()
        return 'token expires' in msg or 'token expired' in msg

    @staticmethod
    def _response_has_auth_failure_text(data: dict[str, Any] | Any) -> bool:
        """Detects whether a response payload contains text suggesting an authentication or authorization failure.

        Parameters:
            data (dict | Any): Response body or parsed JSON; typically a dict containing message fields.

        Returns:
            bool: `True` if common text fields contain markers indicating authentication/authorization issues (for example: unauthorized, token expired, login, auth), `False` otherwise.
        """
        if not isinstance(data, dict):
            return False
        parts = [
            data.get(FIELD_MSG),
            data.get('message'),
            data.get('error'),
            data.get(FIELD_RAW_TEXT),
        ]
        text = ' '.join(str(part) for part in parts if part not in (None, '')).lower()
        if not text:
            return False
        return any(
            marker in text
            for marker in (
                'unauthorized',
                'unauthorised',
                'not authorized',
                'not authorised',
                'forbidden',
                'invalid token',
                'token invalid',
                'token expires',
                'token expired',
                'login',
                'log in',
                'please login',
                'please log in',
                'auth',
                'authentication',
                'authorization',
                'credential',
            )
        )

    def _is_auth_failure_response(
        self, status: int, data: dict[str, Any] | Any
    ) -> bool:
        """Classify HTTP/API authorization failures for HA reauth handling."""
        if status in (401, 403):
            return True
        if self._is_token_expired_response(status, data):
            return True
        if status != 200:
            return self._response_has_auth_failure_text(data)
        code = self._extract_code(data)
        return code not in (CODE_OK, None) and self._response_has_auth_failure_text(
            data
        )

    @staticmethod
    def _auth_failure_message(method: str, path: str, status: int, data: dict) -> str:
        """Create a compact authorization-failure message for logging.

        Parameters:
            method (str): HTTP method used for the request (e.g., "GET", "POST").
            path (str): Request path or endpoint.
            status (int): HTTP status code from the response.
            data (dict): Response JSON-like dictionary; `FIELD_CODE` and one of `FIELD_MSG`/`"message"`/`"error"` may be read.

        Returns:
            str: Single-line message containing the method, path, HTTP status, response code (if present), and response message (if present).
        """
        code = data.get(FIELD_CODE)
        msg = data.get(FIELD_MSG) or data.get('message') or data.get('error')
        return (
            f"{method} {path} authorization failed: HTTP {status} code={code} msg={msg}"
        )

    async def _emit_payload_debug(
        self,
        event_or_factory: dict[str, Any] | Callable[[], dict[str, Any]],
    ) -> None:
        """Emit a payload debug event to the configured debug callback.

        If `payload_debug_callback` is set, forwards either the provided event dict or the zero-argument
        callable that produces one to that callback. Exceptions raised by the callback are caught and
        logged at debug level; this function never raises.

        Parameters:
            event_or_factory (dict[str, Any] | Callable[[], dict[str, Any]]):
                Either a debug event dictionary or a zero-argument callable that returns such a dictionary.
        """
        callback = self.payload_debug_callback
        if callback is None:
            return
        try:
            result = callback(event_or_factory)
            if inspect.isawaitable(result):
                await result
        except Exception as err:
            _LOGGER.debug('Jackery payload debug logging failed: %s', err)

    @staticmethod
    def _http_payload_debug(
        *,
        method: str,
        path: str,
        params: dict | None = None,
        body: dict | None = None,
        status: int | None = None,
        response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a redacted-later debug event describing an HTTP request and its response.

        Returns a dict containing:
            kind (str): Fixed value "http".
            method (str): HTTP method used for the request.
            path (str): Request path.
            params (dict): Query parameters (empty dict when None).
            request_body (dict): Request JSON/body (empty dict when None).
            status (int|None): HTTP response status code or None.
            response (dict): Raw parsed response (empty dict when None).
            response_data_type (str): The Python type name of the response `data` field (e.g., "dict", "list", "NoneType").
            chart_series_debug (Any, optional): Added when `chart_series_debug(payload)` returns debug metadata for chart-like payloads.
        """
        payload = response.get(FIELD_DATA) if isinstance(response, dict) else None
        event: dict[str, Any] = {
            'kind': 'http',
            'method': method,
            'path': path,
            'params': params or {},
            'request_body': body or {},
            'status': status,
            'response': response or {},
            'response_data_type': type(payload).__name__,
        }
        series_debug = chart_series_debug(payload)
        if series_debug:
            event['chart_series_debug'] = series_debug
        return event

    @staticmethod
    def _payload_dict(data: dict[str, Any], path: str) -> dict[str, Any]:
        """Normalize the API response `data` payload to a dictionary suitable for consumers.

        If `data[FIELD_DATA]` is a dict, that dict is returned. If it is `None` or missing, an empty dict is returned. If it exists but is not a dict, an empty dict is returned and a warning is logged indicating the unexpected shape.

        Parameters:
            data (dict[str, Any]): Raw parsed JSON response from the API.
            path (str): Request path used for diagnostic logging.

        Returns:
            dict[str, Any]: The payload dictionary from `data[FIELD_DATA]` or an empty dict.
        """
        payload = data.get(FIELD_DATA)
        if isinstance(payload, dict):
            return payload
        if payload is None:
            return {}
        _LOGGER.warning(
            'Jackery %s returned unexpected data shape for dict payload: %s',
            path,
            type(payload).__name__,
        )
        return {}

    @staticmethod
    def _payload_list(data: dict[str, Any], path: str) -> list[dict[str, Any]]:
        """Extract dictionary items from the response `FIELD_DATA` list and return them.

        If `FIELD_DATA` is a list, returns a new list containing only elements that are dicts. If `FIELD_DATA` is missing or not a list, an empty list is returned and a warning is logged.

        Returns:
            List of dict items from `FIELD_DATA`; empty list when `FIELD_DATA` is absent or has an unexpected shape.
        """
        payload = data.get(FIELD_DATA)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if payload is None:
            return []
        _LOGGER.warning(
            'Jackery %s returned unexpected data shape for list payload: %s',
            path,
            type(payload).__name__,
        )
        return []

    @staticmethod
    def _select_ota_item(
        items: list[dict[str, Any]],
        device_sn: str,
    ) -> dict[str, Any]:
        """Selects the OTA item whose device serial equals the requested serial.

        Parameters:
            items (list[dict]): Candidate OTA item dictionaries to search.
            device_sn (str): Device serial to match (compared as strings).

        Returns:
            dict: The matching OTA item if found; otherwise the first item in `items` if present; or an empty dict when `items` is empty.
        """
        requested_sn = str(device_sn)
        for item in items:
            if str(item.get(FIELD_DEVICE_SN) or '') == requested_sn:
                return item
        return items[0] if items else {}

    # --- generic GET with auto re-login ------------------------------------
    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        """Perform an authenticated GET request to the given API path and return the parsed response payload.

        Parameters:
            path (str): API path (appended to the base URL) to request.
            params (dict | None): Query parameters to include in the request.

        Returns:
            dict: The parsed JSON response object (or a dict containing raw truncated text under `FIELD_RAW_TEXT` when the response is not valid JSON).

        Raises:
            JackeryAuthError: When the response indicates an authentication/authorization failure.
            JackeryApiError: For HTTP errors, request failures, token-expiry/retry failures, or when the API returns a non-OK application code.
        """
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        async def _do() -> tuple[int, dict]:
            async with self._session.get(
                url,
                params=params,
                headers=self._headers(with_token=True),
                timeout=REQUEST_TIMEOUT_SEC,
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

        try:
            status, data = await _do()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise JackeryApiError(
                f"{HTTP_METHOD_GET} {path} request failed: "
                f"{type(err).__name__}: {err or '(no message)'}"
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info('Jackery token expired — re-login')
            self._token = None
            await self._ensure_token()
            try:
                status, data = await _do()
            except (TimeoutError, aiohttp.ClientError) as err:
                raise JackeryApiError(
                    f"{HTTP_METHOD_GET} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or '(no message)'}"
                ) from err

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_GET, path, status, data)
            )
        if status != 200:
            raise JackeryApiError(f"{HTTP_METHOD_GET} {path} HTTP {status}")
        code = self._extract_code(data)
        if code not in (CODE_OK, None):
            raise JackeryApiError(
                f"{HTTP_METHOD_GET} {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)}"
            )
        await self._emit_payload_debug(
            lambda: self._http_payload_debug(
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
        """GET /v1/device/system/list — all systems + their devices.

        Response shape (verified):
            {"code":0, "data":[{
                "id": <systemId>, "systemName": "SolarVault",
                "deviceName": "SolarVault 3 Pro Max",
                "countryCode": "DE", "currency": "€", "timezone": "Europe/Berlin",
                "gridStandard": "103", "onlineState": 1, "bindKey": 1,
                "devices":[{"deviceId": <long>, "deviceSn": "...",
                            "devModel": "HTH...", "modelCode": 3002,
                            FIELD_RB: <SOC>, "isCloud": false, ...}],
                ...
            }]}
        """
        data = await self._get_json(SYSTEM_LIST_PATH)
        self.last_system_list_response = data
        systems = self._payload_list(data, SYSTEM_LIST_PATH)
        self._maybe_learn_region_code(systems)
        return systems

    async def async_get_device_property(self, device_id: str | int) -> dict:
        """GET /v1/device/property — device + properties dict."""
        data = await self._get_json(
            DEVICE_PROPERTY_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_property_responses[str(device_id)] = data
        return self._payload_dict(data, DEVICE_PROPERTY_PATH)

    async def async_get_alarm(self, system_id: str | int) -> Any:
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
        """GET /v1/device/dynamic/historyConfig — provider auth/status metadata."""
        data = await self._get_json(
            PRICE_HISTORY_CONFIG_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        self.last_price_history_config_response = data
        return self._payload_dict(data, PRICE_HISTORY_CONFIG_PATH)

    # --- Additional app-statistic endpoints from PROTOCOL.md §2 ----------
    async def async_get_device_statistic(self, device_id: str | int) -> dict:
        """GET /v1/device/stat/deviceStatistic — current-day device energy flows.

        Response keys (all strings in kWh):
            pvEgy, inEpsEgy, ongridOtBatEgy, pvOtBatEgy, inOngridEgy,
            outOngridEgy, batOtGridEgy, outEpsEgy, batDisChgEgy,
            acOtBatEgy, batOtAcEgy, batChgEgy
        """
        data = await self._get_json(
            DEVICE_STATISTIC_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_device_statistic_responses[str(device_id)] = data
        return self._payload_dict(data, DEVICE_STATISTIC_PATH)

    async def _async_get_device_period_stat(
        self,
        path: str,
        *,
        device_id: str | int,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
        system_id: str | int | None = None,
    ) -> dict[str, Any]:
        """GET a device-level app chart endpoint.

        The Android app uses these device endpoints for the PV/battery/home/CT
        statistic pages, while the older ``sys/*/trends`` endpoints are system
        summaries. Keep request metadata on the payload for diagnostics.
        """
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
        """GET /v1/device/stat/pv — app PV statistics for one device."""
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
        """GET /v1/device/stat/onGrid — app on-grid/home statistics."""
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
        """GET /v1/device/stat/ct — app CT/smart-meter statistics."""
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
        """GET /v1/device/stat/smartSocketStatistic — socket panel totals.

        The Android app calls this with the smart socket accessory id and
        expects fields like ``todayEgy`` and ``totalEgy``.
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
        """GET /v1/device/stat/socket — app socket chart statistics."""
        return await self._async_get_device_period_stat(
            DEVICE_SOCKET_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_battery_pack_list(self, device_sn: str) -> list[dict[str, Any]]:
        """Retrieve sub-battery pack status for the given device serial number.

        Sends a request to the battery pack list endpoint and returns a list of battery-pack objects found in the response. The raw response is cached in `self.last_battery_pack_responses[device_sn]`.

        Parameters:
                device_sn (str): Device serial number to query.

        Returns:
                list[dict[str, Any]]: A list of battery pack dictionaries; an empty list if no packs are present or the response shape is not recognized.
        """
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
                'Jackery %s returned unexpected data shape for battery packs: %s',
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
        """GET /v1/device/stat/sys/battery/trends — battery charge/discharge history."""
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
        """GET /v1/device/ota/list — firmware version + available updates."""
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
        """GET /v1/device/location — GPS coordinates set by the user."""
        data = await self._get_json(
            LOCATION_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_location_responses[str(device_id)] = data
        return self._payload_dict(data, LOCATION_PATH)

    # --- HTTP write endpoints documented in PROTOCOL.md §2 --------------
    async def _put_json(self, path: str, payload: dict) -> dict:
        """Send a JSON PUT request to the API and return the parsed response.

        Automatically ensures a valid auth token, attempts a re-login once if the server indicates the token expired, and validates the HTTP status and application-level response code.

        Parameters:
            path (str): API path to append to the base URL.
            payload (dict): JSON-serializable body to send.

        Returns:
            dict: The parsed JSON response object. If the response cannot be parsed as JSON, returns a dict containing `FIELD_RAW_TEXT` with truncated raw text.

        Raises:
            JackeryAuthError: When the response indicates an authentication or authorization failure.
            JackeryApiError: On HTTP errors, request failures, or when the application-level response code is not OK.
        """
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        def _request_headers() -> dict[str, str]:
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_JSON
            return headers

        async def _do() -> tuple[int, dict]:
            async with self._session.put(
                url,
                json=payload,
                headers=_request_headers(),
                timeout=REQUEST_TIMEOUT_SEC,
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

        try:
            status, data = await _do()
        except (TimeoutError, aiohttp.ClientError) as err:
            raise JackeryApiError(
                f"{HTTP_METHOD_PUT} {path} request failed: "
                f"{type(err).__name__}: {err or '(no message)'}"
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info(
                'Jackery token expired — re-login for %s %s', HTTP_METHOD_PUT, path
            )
            self._token = None
            await self._ensure_token()
            try:
                status, data = await _do()
            except (TimeoutError, aiohttp.ClientError) as err:
                raise JackeryApiError(
                    f"{HTTP_METHOD_PUT} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or '(no message)'}"
                ) from err

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_PUT, path, status, data)
            )
        if status != 200:
            raise JackeryApiError(f"{HTTP_METHOD_PUT} {path} HTTP {status}")
        code = self._extract_code(data)
        if code not in (CODE_OK, None):
            raise JackeryApiError(
                f"{HTTP_METHOD_PUT} {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)}"
            )
        await self._emit_payload_debug(
            lambda: self._http_payload_debug(
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
        """Rename a system on the remote API.

        Parameters:
            system_id (str | int): Identifier of the system to rename.
            system_name (str): New name for the system; must be non-empty after stripping.

        Returns:
            bool: `True` if the backend reported success (`data` truthy), `False` otherwise.

        Raises:
            JackeryApiError: If `system_name` is empty or only whitespace.
        """
        if not system_name or not system_name.strip():
            raise JackeryApiError('system_name must be a non-empty string')
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
        """Send a form-urlencoded POST to the API, handling token refresh on expiry.

        Parameters:
                path (str): API path appended to the base URL.
                fields (dict[str, Any]): Form fields; values will be converted to strings.

        Returns:
                dict: Parsed JSON response from the server, or a dict containing the raw truncated response text under FIELD_RAW_TEXT when JSON parsing fails.

        Raises:
                JackeryAuthError: When the response indicates an authentication/authorization failure.
                JackeryApiError: On network/request failures, non-200 HTTP status, or when the API returns an application error code.
        """
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        def _request_headers() -> dict[str, str]:
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_FORM
            return headers

        body = {k: str(v) for k, v in fields.items()}

        async def _do() -> tuple[int, dict]:
            async with self._session.post(
                url,
                data=body,
                headers=_request_headers(),
                timeout=REQUEST_TIMEOUT_SEC,
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
            raise JackeryApiError(
                f"{HTTP_METHOD_POST} {path} request failed: "
                f"{type(err).__name__}: {err or '(no message)'}"
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info(
                'Jackery token expired — re-login for %s %s', HTTP_METHOD_POST, path
            )
            self._token = None
            await self._ensure_token()
            try:
                status, data = await _do()
            except (TimeoutError, aiohttp.ClientError) as err:
                raise JackeryApiError(
                    f"{HTTP_METHOD_POST} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or '(no message)'}"
                ) from err

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_POST, path, status, data)
            )
        if status != 200:
            raise JackeryApiError(f"{HTTP_METHOD_POST} {path} HTTP {status}")
        code = self._extract_code(data)
        if code not in (CODE_OK, None):
            # Surface the whole response so callers can show it to the user
            raise JackeryApiError(
                f"{HTTP_METHOD_POST} {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)!r} "
                f"data={data.get(FIELD_DATA)!r}"
            )
        await self._emit_payload_debug(
            lambda: self._http_payload_debug(
                method=HTTP_METHOD_POST,
                path=path,
                body=body,
                status=status,
                response=data,
            )
        )
        return data

    async def async_set_max_power(self, device_id: str | int, max_power: int) -> bool:
        """Request the device's maximum allowed power using the experimental saveRecord endpoint.

        This validates that `max_power` is a non-negative integer and submits a form-encoded request with `deviceId` and `maxPower`; the backend may treat this as a historical record rather than an immediate live setting.

        Parameters:
            device_id (str | int): Device identifier to apply the max power to.
            max_power (int): Maximum power value in watts; must be greater than or equal to 0.

        Returns:
            bool: `true` if the response `data` field is truthy, `false` otherwise.

        Raises:
            JackeryApiError: If `max_power` validation fails or the API call reports an error.
        """
        if not isinstance(max_power, int) or max_power < 0:
            raise JackeryApiError('max_power must be a non-negative integer')
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
        """Set the system to single-price mode using the provided price and currency.

        Parameters:
            system_id (str | int): Identifier of the target system.
            single_price (float | str): Price per unit; must be greater than or equal to 0. Values are formatted with up to four decimal places (trailing zeros and a trailing decimal point are removed).
            currency (str): Currency code or symbol; must be a non-empty string.

        Returns:
            bool: `True` if the backend indicates the request was accepted, `False` otherwise.

        Raises:
            JackeryApiError: If `single_price` is negative or `currency` is empty.
        """
        price = float(single_price)
        if price < 0:
            raise JackeryApiError('single_price must be >= 0')
        cur = str(currency or '').strip()
        if not cur:
            raise JackeryApiError('currency must be a non-empty string')
        # Keep stable decimal formatting for backend parsing.
        price_text = f"{price:.4f}".rstrip('0').rstrip('.')
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
        """Set the system to dynamic pricing mode on the server.

        Parameters:
            system_id (str | int): Identifier of the system to update.
            platform_company_id (int): Platform company identifier required by the backend.
            system_region (str): Non-empty region code/name associated with the system.

        Returns:
            `True` if the server acknowledged the change, `False` otherwise.

        Raises:
            JackeryApiError: If `system_region` is empty or the API request fails.
        """
        region = str(system_region or '').strip()
        if not region:
            raise JackeryApiError('system_region must be a non-empty string')
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
        """GET /v1/device/bind/list — Explorer-series only, kept for compat.

        Authentication failures must propagate so HA can open the reauth
        flow; only generic API failures (endpoint not exposed for this
        account, transient backend errors) are swallowed into an empty list
        because this is a best-effort fallback path.
        """
        try:
            data = await self._get_json(DEVICE_LIST_PATH)
        except JackeryAuthError:
            raise
        except JackeryError:
            return []
        return self._payload_list(data, DEVICE_LIST_PATH)
