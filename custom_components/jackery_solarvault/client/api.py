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
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.serialization import load_der_public_key

from jackery_solarvault.const import (
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
    FIELD_CODE,
    FIELD_COUNTRY_CODE,
    FIELD_CURRENCY,
    FIELD_CURRENT_VERSION,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEVICE_SN_LIST,
    FIELD_DEV_SN,
    FIELD_FUNCTION,
    FIELD_ID,
    FIELD_IS_FIRMWARE_UPGRADE,
    FIELD_LOGIN_TYPE,
    FIELD_MAC_ID,
    FIELD_MAX_POWER,
    FIELD_MODEL,
    FIELD_MQTT_PASSWORD,
    FIELD_MSG,
    FIELD_PASSWORD,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_RAW_TEXT,
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
    SHELLY_CONTROL_PATH,
    SHELLY_DEVICES_PATH,
    SHELLY_REALTIME_POWER_PATH,
    SYSTEM_LIST_PATH,
    SYSTEM_NAME_PATH,
    SYSTEM_STATISTIC_PATH,
    SYS_VERSION,
    USER_AGENT,
)
from jackery_solarvault.util import app_period_date_bounds, chart_series_debug

_LOGGER = logging.getLogger(__name__)
_HTTP_RETRY_ATTEMPTS = 3
_HTTP_RETRY_BACKOFF_SEC = (0.5, 2.0, 5.0)


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
        raise TypeError(
            f"Jackery login expects an RSA public key, got {type(public_key).__name__}"
        )
    return public_key.encrypt(data, asym_padding.PKCS1v15())


def _generate_udid(seed: str) -> str:
    md5_digest = hashlib.md5(seed.encode("utf-8")).digest()
    u = uuid.UUID(bytes=md5_digest, version=3)
    return MQTT_MAC_ID_PREFIX + str(u).replace("-", "")


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
        """Initialise the entity from the coordinator and description."""
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
        self.auth_rejection_callback: Callable[[int, object], None] | None = None

    @staticmethod
    def _decode_response_json(body_bytes: bytes) -> Any:
        """Decode JSON from a response body that was read exactly once."""
        return json.loads(body_bytes)

    @staticmethod
    def _truncated_response_text(body_bytes: bytes) -> str:
        """Return bounded raw response text for diagnostics."""
        return body_bytes[:HTTP_RAW_TEXT_LIMIT].decode("utf-8", errors="replace")

    @staticmethod
    def _is_transient_http_status(status: int) -> bool:
        """Return True for server-side statuses that are safe to retry."""
        return 500 <= status < 600

    async def _request_json_with_retry(
        self,
        method: str,
        path: str,
        request: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
    ) -> tuple[int, dict[str, Any]]:
        """Run one JSON HTTP request with bounded transient retry/backoff."""
        for attempt in range(1, _HTTP_RETRY_ATTEMPTS + 1):
            try:
                status, data = await request()
            except (TimeoutError, aiohttp.ClientConnectionError) as err:
                if attempt >= _HTTP_RETRY_ATTEMPTS:
                    raise
                delay = _HTTP_RETRY_BACKOFF_SEC[attempt - 1]
                _LOGGER.debug(
                    "Jackery %s %s transient %s on attempt %d/%d; retrying in %.1fs",
                    method,
                    path,
                    type(err).__name__,
                    attempt,
                    _HTTP_RETRY_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            if not self._is_transient_http_status(status):
                return status, data
            if attempt >= _HTTP_RETRY_ATTEMPTS:
                return status, data
            delay = _HTTP_RETRY_BACKOFF_SEC[attempt - 1]
            _LOGGER.debug(
                "Jackery %s %s HTTP %d on attempt %d/%d; retrying in %.1fs",
                method,
                path,
                status,
                attempt,
                _HTTP_RETRY_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)
        raise JackeryApiError(f"{method} {path} retry loop exhausted")

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
                with_token (bool): If True and the client has an authentication token, include the auth token header.

        Returns:
                headers (dict[str, str]): Mapping of HTTP header names to values. Includes the auth token header when `with_token` is True and a token is present.
        """  # noqa: E501, RUF100
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
            JackeryAuthError: If the normalized value does not match 33 lowercase hexadecimal characters.
        """  # noqa: E501, RUF100
        mac_id = value.strip().lower()
        # App values are 33 hex chars (prefix 2/9 + 32-char UUID-no-dash).
        if not re.fullmatch(r"[0-9a-f]{33}", mac_id):
            raise JackeryAuthError(
                "Invalid mqtt_mac_id format. Expected 33 lowercase hex chars "
                "(example: 271c55f5731fa3d9ba1fe131e088946e0)."
            )
        return mac_id

    def _resolve_login_mac_id(self) -> str:
        """Resolve and return the MAC identifier used for login and MQTT username derivation.

        If a configured MQTT MAC ID is present and valid, that value is returned and
        `self._mqtt_mac_id_source` is set to `"configured"`. If the configured value is
        invalid, a deterministic MAC ID derived from the account is returned and
        `self._mqtt_mac_id_source` is set to `"generated_fallback_invalid_config"`.
        If no configured value is provided, a deterministic MAC ID derived from the
        account is returned and `self._mqtt_mac_id_source` is set to `"generated"`.

        Returns:
            str: The resolved MAC ID string.
        """  # noqa: E501, RUF100
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
        """Perform the encrypted login flow and store session and MQTT credentials.

        This sends the encrypted login form to the backend, validates the response code, and stores the returned JWT token and MQTT-related fields on the client instance.

        Returns:
            token (str): The JWT session token returned by the server.
        """  # noqa: E501, RUF100
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

        try:
            async with self._session.post(
                url,
                data=form_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=LOGIN_TIMEOUT_SEC),
            ) as resp:
                if resp.status != 200:
                    raise JackeryApiError(f"Login HTTP {resp.status}")
                body_bytes = await resp.read()
                try:
                    data = self._decode_response_json(body_bytes)
                except (
                    aiohttp.ContentTypeError,
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                    ValueError,
                ) as err:
                    raw = self._truncated_response_text(body_bytes)
                    raise JackeryApiError(
                        f"Login returned invalid JSON: {raw!r}"
                    ) from err
        except (TimeoutError, aiohttp.ClientError) as err:
            raise JackeryApiError(
                f"Login request failed: {type(err).__name__}: {err or '(no message)'}"
            ) from err

        if not isinstance(data, dict):
            raise JackeryApiError(
                f"Login returned JSON {type(data).__name__}, expected object"
            )

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
            raise JackeryAuthError(
                f"Login rejected (code={data.get(FIELD_CODE)}, msg={data.get(FIELD_MSG)})"  # noqa: E501, RUF100
            )

        token = data.get(FIELD_TOKEN) or ""
        if not token:
            raise JackeryAuthError("Login succeeded but no token returned")

        self._token = token
        payload = data.get(FIELD_DATA) or {}
        if not isinstance(payload, dict):
            raise JackeryApiError(
                f"Login returned {FIELD_DATA} {type(payload).__name__}, expected object"
            )
        self.last_login_response = dict(data)
        self._mqtt_user_id = str(payload.get(FIELD_USER_ID) or "") or None
        self._mqtt_seed_b64 = payload.get(FIELD_MQTT_PASSWORD) or None
        self._mqtt_mac_id = mac_id
        return token

    async def async_get_mqtt_credentials(self) -> dict[str, str]:
        """Build MQTT client credentials using the current authenticated session.

        Returns:
            dict[str, str]: Mapping with keys:
                - `clientId`: MQTT client identifier.
                - `username`: MQTT username.
                - `password`: MQTT password (base64-encoded).
                - `userId`: MQTT user id from the login response.

        Raises:
            JackeryAuthError: If the client is not logged in or required MQTT fields are missing,
                if `mqttPassWord` is not valid base64, or if the decoded seed is not 32 bytes.
        """  # noqa: E501, RUF100
        await self._ensure_token()
        if not self._mqtt_user_id or not self._mqtt_seed_b64 or not self._mqtt_mac_id:
            raise JackeryAuthError(
                "Login response missing MQTT fields (userId/mqttPassWord/macId)"
            )

        try:
            seed = base64.b64decode(self._mqtt_seed_b64, validate=True)
        except (binascii.Error, ValueError) as err:
            raise JackeryAuthError(
                "Invalid mqttPassWord base64 in login response"
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
        """Return the source identifier for the current MQTT MAC ID (login vs cached)."""  # noqa: E501, RUF100
        return self._mqtt_mac_id_source

    @property
    def mqtt_mac_id(self) -> str | None:
        """Return the MAC ID assigned to this MQTT session by login."""
        return self._mqtt_mac_id

    async def _ensure_token(self) -> str:
        if self._token is None:
            async with self._lock:
                if self._token is None:
                    await self.async_login()
        if self._token is None:
            raise JackeryAuthError("Login succeeded without returning a token")
        return self._token

    @staticmethod
    def _extract_code(data: object) -> int | None:
        """Extracts the backend numeric `code` value from a parsed API response.

        Parameters:
            data (dict | Any): Parsed JSON response (expected dict) or any other value.

        Returns:
            int | None: The `code` parsed as an integer when present as an integer or a numeric string, `None` otherwise.
        """  # noqa: E501, RUF100
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
        """Determine whether the parsed API response signals that the authentication token has expired.

        Only dict-shaped responses are inspected; non-dict responses always return False.

        Returns:
            True if the response indicates token expiration, False otherwise.
        """  # noqa: E501, RUF100
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
            if self.auth_rejection_callback is not None:
                self.auth_rejection_callback(status, data)
            return True
        if self._is_token_expired_response(status, data):
            if self.auth_rejection_callback is not None:
                self.auth_rejection_callback(status, data)
            return True
        if status != 200:
            is_auth_failure = self._response_has_auth_failure_text(data)
            if is_auth_failure and self.auth_rejection_callback is not None:
                self.auth_rejection_callback(status, data)
            return is_auth_failure
        code = self._extract_code(data)
        is_auth_failure = code not in {
            CODE_OK,
            None,
        } and self._response_has_auth_failure_text(data)
        if is_auth_failure and self.auth_rejection_callback is not None:
            self.auth_rejection_callback(status, data)
        return is_auth_failure

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
        """Forward one raw/parsed payload debug event to the coordinator.

        Accepts either a pre-built event dict or a zero-arg callable that
        returns one. The callable form is forwarded as-is to the
        coordinator, which itself only invokes it when the dedicated
        ``payload_debug`` logger is at DEBUG level — saving the
        ``redacted`` walk on hot paths when DEBUG is disabled.
        """
        callback = self.payload_debug_callback
        if callback is None:
            return
        try:
            event = (
                event_or_factory() if callable(event_or_factory) else event_or_factory
            )
            result = callback(event)
            if inspect.isawaitable(result):
                await result
        except Exception as err:
            _LOGGER.debug("Jackery payload debug logging failed: %s", err)

    @staticmethod
    def _http_payload_debug(
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
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
            raise JackeryApiError(f"Jackery {path} returned null data")
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
        """  # noqa: E501, RUF100
        payload = data.get(FIELD_DATA)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if payload is None:
            raise JackeryApiError(f"Jackery {path} returned null data")
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
        """  # noqa: E501, RUF100
        requested_sn = str(device_sn)
        for item in items:
            if str(item.get(FIELD_DEVICE_SN) or "") == requested_sn:
                return item
        return {}

    # --- generic GET with auto re-login ------------------------------------
    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        async def _do() -> tuple[int, dict[str, Any]]:
            async with self._session.get(
                url,
                params=params,
                headers=self._headers(with_token=True),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC),
            ) as resp:
                status = resp.status
                body_bytes = await resp.read()
                try:
                    body = self._decode_response_json(body_bytes)
                except (
                    aiohttp.ContentTypeError,
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                    ValueError,
                ) as err:
                    raw = self._truncated_response_text(body_bytes)
                    raise JackeryApiError(
                        f"{HTTP_METHOD_GET} {path} returned invalid JSON: {raw!r}"
                    ) from err
                if not isinstance(body, dict):
                    raise JackeryApiError(
                        f"{HTTP_METHOD_GET} {path} returned JSON "
                        f"{type(body).__name__}, expected object"
                    )
                return status, body

        try:
            status, data = await self._request_json_with_retry(
                HTTP_METHOD_GET, path, _do
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            raise JackeryApiError(
                f"{HTTP_METHOD_GET} {path} request failed: "
                f"{type(err).__name__}: {err or '(no message)'}"
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info("Jackery token expired — re-login")
            self._token = None
            await self._ensure_token()
            try:
                status, data = await self._request_json_with_retry(
                    HTTP_METHOD_GET, path, _do
                )
            except (TimeoutError, aiohttp.ClientError) as err:
                raise JackeryApiError(
                    f"{HTTP_METHOD_GET} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or '(no message)'}"
                ) from err
            if self._is_token_expired_response(status, data):
                raise JackeryApiError(
                    f"{HTTP_METHOD_GET} {path} token still expired after re-login"
                )

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_GET, path, status, data)
            )
        if status != 200:
            raise JackeryApiError(
                f"{HTTP_METHOD_GET} {path} HTTP {status} "
                f"code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)!r}"
            )
        code = self._extract_code(data)
        if code not in {CODE_OK, None}:
            raise JackeryApiError(
                f"{HTTP_METHOD_GET} {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)}"  # noqa: E501, RUF100
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

    async def async_get_device_property(self, device_id: str | int) -> dict[str, Any]:
        """GET /v1/device/property — device + properties dict."""
        data = await self._get_json(
            DEVICE_PROPERTY_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_property_responses[str(device_id)] = data
        return self._payload_dict(data, DEVICE_PROPERTY_PATH)

    async def async_get_alarm(
        self, system_id: str | int
    ) -> Any:  # parsed JSON response, indexed by callers  # noqa: ANN401, RUF100
        """GET /v1/api/alarm — alarm list for a system."""
        data = await self._get_json(
            ALARM_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        self.last_alarm_response = data
        return data.get(FIELD_DATA)

    async def async_get_system_statistic(self, system_id: str | int) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
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

    async def async_get_power_price(self, system_id: str | int) -> dict[str, Any]:
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
        """  # noqa: E501, RUF100
        data = await self._get_json(
            PRICE_HISTORY_CONFIG_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        self.last_price_history_config_response = data
        return self._payload_dict(data, PRICE_HISTORY_CONFIG_PATH)

    # --- Additional app-statistic endpoints from PROTOCOL.md §2 ----------
    async def async_get_device_statistic(self, device_id: str | int) -> dict[str, Any]:
        """Retrieve current-day energy flow statistics for the specified device.

        The returned dictionary maps metric keys (strings) to their values as numeric strings representing kilowatt-hours (kWh). Typical keys include: `pvEgy`, `inEpsEgy`, `ongridOtBatEgy`, `pvOtBatEgy`, `inOngridEgy`, `outOngridEgy`, `batOtGridEgy`, `outEpsEgy`, `batDisChgEgy`, `acOtBatEgy`, `batOtAcEgy`, and `batChgEgy`. Keys present may vary by device and backend response.

        Returns:
            dict: Mapping of statistic keys to their values as strings in kWh.
        """  # noqa: E501, RUF100
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
        """  # noqa: E501, RUF100
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
        """  # noqa: E501, RUF100
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
        """  # noqa: E501, RUF100
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
        """  # noqa: E501, RUF100
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
        """Retrieve EPS (off-grid) energy input/output statistics for a device for a specified period.

        Returns:
            dict: Payload containing aggregated totals (e.g., `totalInEpsEnergy`, `totalOutEpsEnergy`), chart series arrays (`x`, `y`, `y1`, `y2`), and, when present, an `APP_REQUEST_META` dict with the request parameters used.
        """  # noqa: E501, RUF100
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
        """  # noqa: E501, RUF100
        data = await self._get_json(
            DEVICE_TODAY_ENERGY_PATH,
            params={FIELD_DEVICE_SN: str(device_sn)},
        )
        return self._payload_dict(data, DEVICE_TODAY_ENERGY_PATH)

    async def async_get_device_meter_stat(
        self,
        device_id: str | int,
    ) -> dict[str, Any]:
        """Retrieve the Smart-Meter (CT accessory) panel totals for the given device.

        Parameters:
            device_id: Smart-Meter / CT accessory deviceId (not the SolarVault main deviceId).

        Returns:
            A dictionary containing the parsed payload with the meter panel totals from the device meter statistics endpoint.
        """  # noqa: E501, RUF100
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
        """Fetches socket panel totals for the given smart socket.

        Parameters:
            smart_socket_id (str | int): Smart-socket accessory identifier passed to the API.

        Returns:
            dict[str, Any]: The response `data` payload as a dictionary (empty if missing or not a dict).
        """  # noqa: E501, RUF100
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
        """Retrieve socket-chart statistics for a device for a given period.

        If the returned payload is non-empty, the payload will include `APP_REQUEST_META` with the request parameters (`dateType`, `beginDate`, `endDate`) used to produce the chart (excluding `deviceId`/`systemId`).

        Returns:
            dict: The normalized `data` payload for the device socket chart.
        """  # noqa: E501, RUF100
        return await self._async_get_device_period_stat(
            DEVICE_SOCKET_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_shelly_devices(self) -> list[dict[str, Any]]:
        """GET device/shelly/devices and return app-linked Shelly accessories."""
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
        """GET Shelly realtime-power for one app-linked accessory."""
        data = await self._get_json(
            SHELLY_REALTIME_POWER_PATH,
            params={FIELD_DEVICE_ID: str(device_id)},
        )
        return self._payload_dict(data, SHELLY_REALTIME_POWER_PATH)

    async def async_control_shelly_device(
        self,
        device_id: str | int,
        *,
        action: str | int | None,
        function: str | int | None,
        control_allowed: bool = True,
    ) -> bool:
        """POST Shelly control using app-derived action/function values."""
        if not control_allowed:
            raise JackeryApiError("Shelly control is not allowed for this device")
        if action is None or function is None:
            raise JackeryApiError("Shelly control requires action and function")
        data = await self._post_form(
            SHELLY_CONTROL_PATH,
            {
                FIELD_DEVICE_ID: str(device_id),
                FIELD_ACTION: str(action),
                FIELD_FUNCTION: str(function),
            },
        )
        return bool(data.get(FIELD_DATA, True))

    async def async_get_battery_pack_list(self, device_sn: str) -> list[dict[str, Any]]:
        """Get a normalized list of battery pack dictionaries for the given device serial number.

        The raw parsed API response is saved to self.last_battery_pack_responses[device_sn]. Handles multiple backend response shapes and returns an empty list when no pack data is found.

        Parameters:
            device_sn (str): Device serial number to query.

        Returns:
            list[dict]: Battery pack dictionaries extracted from the response; empty list if no packs are found or the response shape is unrecognized.
        """  # noqa: E501, RUF100
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
            # Require both batSoc (telemetry) and devSn (identity) to avoid
            # matching config payloads that carry version/updateStatus but no
            # real pack data.
            if FIELD_BAT_SOC in raw and FIELD_DEV_SN in raw:
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
    ) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        """Retrieve battery charge and discharge trends for the given system.

        If the returned payload is non-empty, attaches request metadata under `APP_REQUEST_META`
        containing the request's `dateType`, `beginDate`, and `endDate`.

        Returns:
            dict: Normalized payload dictionary extracted from the API response (may be empty).
        """  # noqa: E501, RUF100
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

    async def async_get_ota_info(self, device_sn: str) -> dict[str, Any]:
        """Retrieve OTA information for the device identified by `device_sn`.

        Normalizes multiple backend response shapes and selects the matching OTA item when available.

        Returns:
            dict: OTA information object for the device, or an empty dict if no suitable item is found.
        """  # noqa: E501, RUF100
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

    async def async_get_location(self, device_id: str | int) -> dict[str, Any]:
        """Retrieve the GPS coordinates previously set for the specified device.

        Returns:
            dict: The API payload's `data` object containing location fields (e.g., `latitude`, `longitude`); an empty dict if `data` is missing or not a dict.
        """  # noqa: E501, RUF100
        data = await self._get_json(
            LOCATION_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_location_responses[str(device_id)] = data
        return self._payload_dict(data, LOCATION_PATH)

    # --- HTTP write endpoints documented in PROTOCOL.md §2 --------------
    async def _put_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON PUT to the given API path, ensuring a valid auth token and retrying once after re-login if the token has expired.

        If the response body cannot be parsed as JSON, the returned dict contains `FIELD_RAW_TEXT` with truncated raw text.

        Returns:
            dict: Parsed response JSON, or a dict containing `FIELD_RAW_TEXT` when JSON parsing failed.

        Raises:
            JackeryAuthError: When the response indicates an authorization or authentication failure.
            JackeryApiError: On network/request failures, non-200 HTTP status, or backend `code` that is neither `CODE_OK` nor `None`.
        """  # noqa: E501, RUF100
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        def _request_headers() -> dict[str, str]:
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_JSON
            return headers

        async def _do() -> tuple[int, dict[str, Any]]:
            async with self._session.put(
                url,
                json=payload,
                headers=_request_headers(),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC),
            ) as resp:
                status = resp.status
                body_bytes = await resp.read()
                try:
                    body = self._decode_response_json(body_bytes)
                except (
                    aiohttp.ContentTypeError,
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                    ValueError,
                ):
                    body = {FIELD_RAW_TEXT: self._truncated_response_text(body_bytes)}
                return status, body

        try:
            status, data = await self._request_json_with_retry(
                HTTP_METHOD_PUT, path, _do
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            raise JackeryApiError(
                f"{HTTP_METHOD_PUT} {path} request failed: "
                f"{type(err).__name__}: {err or '(no message)'}"
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info(
                "Jackery token expired — re-login for %s %s", HTTP_METHOD_PUT, path
            )
            self._token = None
            await self._ensure_token()
            try:
                status, data = await self._request_json_with_retry(
                    HTTP_METHOD_PUT, path, _do
                )
            except (TimeoutError, aiohttp.ClientError) as err:
                raise JackeryApiError(
                    f"{HTTP_METHOD_PUT} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or '(no message)'}"
                ) from err
            if self._is_token_expired_response(status, data):
                raise JackeryApiError(
                    f"{HTTP_METHOD_PUT} {path} token still expired after re-login"
                )

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_PUT, path, status, data)
            )
        if status != 200:
            raise JackeryApiError(
                f"{HTTP_METHOD_PUT} {path} HTTP {status} "
                f"code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)!r}"
            )
        code = self._extract_code(data)
        if code not in {CODE_OK, None}:
            raise JackeryApiError(
                f"{HTTP_METHOD_PUT} {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)}"  # noqa: E501, RUF100
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
        """  # noqa: E501, RUF100
        if not system_name or not system_name.strip():
            raise JackeryApiError("system_name must be a non-empty string")
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

    async def _post_form(self, path: str, fields: dict[str, Any]) -> dict[str, Any]:
        """Send a form-urlencoded POST to the Jackery API, retrying once after automatic re-login if the token is expired.

        Parameters:
            path (str): API endpoint path appended to the base URL.
            fields (dict[str, Any]): Form fields to send; all values will be converted to strings.

        Returns:
            dict: Parsed JSON response from the API, or a dict containing raw truncated text under `FIELD_RAW_TEXT` if JSON decoding fails.

        Raises:
            JackeryApiError: On network/timeout failures, non-200 HTTP status, or when the response `code` indicates an error.
            JackeryAuthError: When the response indicates an authentication or authorization failure.
        """  # noqa: E501, RUF100
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        def _request_headers() -> dict[str, str]:
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_FORM
            return headers

        body = {k: str(v) for k, v in fields.items()}

        async def _do() -> tuple[int, dict[str, Any]]:
            async with self._session.post(
                url,
                data=body,
                headers=_request_headers(),
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SEC),
            ) as resp:
                status = resp.status
                body_bytes = await resp.read()
                try:
                    data = self._decode_response_json(body_bytes)
                except (
                    aiohttp.ContentTypeError,
                    json.JSONDecodeError,
                    UnicodeDecodeError,
                    ValueError,
                ):
                    data = {FIELD_RAW_TEXT: self._truncated_response_text(body_bytes)}
                return status, data

        try:
            status, data = await self._request_json_with_retry(
                HTTP_METHOD_POST, path, _do
            )
        except (TimeoutError, aiohttp.ClientError) as err:
            raise JackeryApiError(
                f"{HTTP_METHOD_POST} {path} request failed: "
                f"{type(err).__name__}: {err or '(no message)'}"
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info(
                "Jackery token expired — re-login for %s %s", HTTP_METHOD_POST, path
            )
            self._token = None
            await self._ensure_token()
            try:
                status, data = await self._request_json_with_retry(
                    HTTP_METHOD_POST, path, _do
                )
            except (TimeoutError, aiohttp.ClientError) as err:
                raise JackeryApiError(
                    f"{HTTP_METHOD_POST} {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or '(no message)'}"
                ) from err
            if self._is_token_expired_response(status, data):
                raise JackeryApiError(
                    f"{HTTP_METHOD_POST} {path} token still expired after re-login"
                )

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message(HTTP_METHOD_POST, path, status, data)
            )
        if status != 200:
            raise JackeryApiError(
                f"{HTTP_METHOD_POST} {path} HTTP {status} "
                f"code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)!r}"
            )
        code = self._extract_code(data)
        if code not in {CODE_OK, None}:
            # Surface the whole response so callers can show it to the user
            raise JackeryApiError(
                f"{HTTP_METHOD_POST} {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)!r} "  # noqa: E501, RUF100
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
        """  # noqa: E501, RUF100
        if not isinstance(max_power, int) or isinstance(max_power, bool) or max_power < 0:
            raise JackeryApiError("max_power must be a non-negative integer")
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
        """Set the system's fixed electricity price for dynamic pricing mode.

        Parameters:
            single_price (float | str): Price value that must be greater than or equal to 0; will be formatted to at most four decimal places before sending.
            currency (str): Non-empty currency code or label.

        Returns:
            `true` if the backend indicates the change was accepted, `false` otherwise.

        Raises:
            JackeryApiError: If `single_price` is invalid, negative, or `currency` is empty.
        """  # noqa: E501, RUF100
        try:
            price = float(single_price)
        except TypeError as err:
            raise JackeryApiError("single_price must be a valid number") from err
        except ValueError as err:
            raise JackeryApiError("single_price must be a valid number") from err
        if price < 0:
            raise JackeryApiError("single_price must be >= 0")
        cur = str(currency or "").strip()
        if not cur:
            raise JackeryApiError("currency must be a non-empty string")
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
            system_id (str | int): Identifier of the target system.
            platform_company_id (int): Platform company identifier required by the API.
            system_region (str): Region code for the system; must be a non-empty string.

        Returns:
            True if the server accepted the change, False otherwise.

        Raises:
            JackeryApiError: If `system_region` is empty.
        """
        region = str(system_region or "").strip()
        if not region:
            raise JackeryApiError("system_region must be a non-empty string")
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
        """  # noqa: E501, RUF100
        try:
            data = await self._get_json(DEVICE_LIST_PATH)
        except JackeryAuthError:
            raise
        except JackeryError:
            return []
        return self._payload_list(data, DEVICE_LIST_PATH)
