"""Async API client for the Jackery SolarVault cloud (iot.jackeryapp.com).

Endpoint paths and polling rules are mirrored from APP_POLLING_MQTT.md.
MQTT command details are documented separately in MQTT_PROTOCOL.md.

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

from .const import (
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
    DEVICE_STATISTIC_PATH,
    FIELD_ACCOUNT,
    FIELD_BAT_SOC,
    FIELD_BATTERY_PACKS,
    FIELD_BODY,
    FIELD_CELL_TEMP,
    FIELD_CODE,
    FIELD_COUNTRY_CODE,
    FIELD_CURRENCY,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEVICE_SN_LIST,
    FIELD_ID,
    FIELD_IN_PW,
    FIELD_IP,
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
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_NAME,
    FIELD_SYSTEM_REGION,
    FIELD_TOKEN,
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
    REDACTED_VALUE,
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
from .util import app_period_date_bounds, chart_series_debug

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
        """Headers matching the Android app values documented in APP_POLLING_MQTT.md."""
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
        """Normalize and validate the app-style macId token."""
        mac_id = value.strip().lower()
        # App values are 33 hex chars (prefix 2/9 + 32-char UUID-no-dash).
        if not re.fullmatch(r"[0-9a-f]{33}", mac_id):
            raise JackeryAuthError(
                "Invalid mqtt_mac_id format. Expected 33 lowercase hex chars "
                "(example: 271c55f5731fa3d9ba1fe131e088946e0)."
            )
        return mac_id

    def _resolve_login_mac_id(self) -> str:
        """Resolve the macId used in login and MQTT username derivation."""
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
        """Encrypted login; stores the JWT session token."""
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
            raise JackeryApiError(f"Login request failed: {err}") from err

        # Store redacted version for diagnostics
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

        if self._extract_code(data) != CODE_OK:
            raise JackeryAuthError(
                f"Login rejected (code={data.get(FIELD_CODE)}, msg={data.get(FIELD_MSG)})"
            )

        token = data.get(FIELD_TOKEN) or ""
        if not token:
            raise JackeryAuthError("Login succeeded but no token returned")

        self._token = token
        payload = data.get(FIELD_DATA) or {}
        self._mqtt_user_id = str(payload.get(FIELD_USER_ID) or "") or None
        self._mqtt_seed_b64 = payload.get(FIELD_MQTT_PASSWORD) or None
        self._mqtt_mac_id = mac_id
        return token

    async def async_get_mqtt_credentials(self) -> dict[str, str]:
        """Return MQTT credentials for the active REST login session.

        Runtime-verified app algorithm from MQTT_PROTOCOL.md:
            clientId = f"{userId}@APP"
            username = f"{userId}@{macId}"
            seed     = base64_decode(mqttPassWord)   # 32 bytes
            key      = seed                           # AES-256 key
            iv       = seed[:16]
            password = base64(AES-256-CBC-PKCS5(username_utf8, key, iv))
        """
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
        """Return the source identifier for the current MQTT MAC ID (login vs cached)."""
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
        """Detect token-expired responses across backend variants."""
        if status != 200 or not isinstance(data, dict):
            return False
        code = self._extract_code(data)
        if code == CODE_TOKEN_EXPIRED:
            return True
        msg = str(data.get(FIELD_MSG) or "").lower()
        return "token expires" in msg or "token expired" in msg

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
            result = callback(event_or_factory)
            if inspect.isawaitable(result):
                await result
        except Exception as err:
            _LOGGER.debug("Jackery payload debug logging failed: %s", err)

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
            return {}
        _LOGGER.warning(
            "Jackery %s returned unexpected data shape for dict payload: %s",
            path,
            type(payload).__name__,
        )
        return {}

    @staticmethod
    def _payload_list(data: dict[str, Any], path: str) -> list[dict[str, Any]]:
        """Return a list of dict payload items or an empty list."""
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
    async def _get_json(self, path: str, params: dict | None = None) -> dict:
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
                f"{HTTP_METHOD_GET} {path} request failed: {err}"
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info("Jackery token expired — re-login")
            self._token = None
            await self._ensure_token()
            try:
                status, data = await _do()
            except (TimeoutError, aiohttp.ClientError) as err:
                raise JackeryApiError(
                    f"{HTTP_METHOD_GET} {path} request failed after re-login: {err}"
                ) from err

        if status != 200:
            raise JackeryApiError(f"{HTTP_METHOD_GET} {path} HTTP {status}")
        code = self._extract_code(data)
        if code not in (CODE_OK, None):
            raise JackeryApiError(
                f"{HTTP_METHOD_GET} {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)}"
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

    # --- Additional app-statistic endpoints from APP_POLLING_MQTT.md ----------
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
        # APP_POLLING_MQTT.md: Periodenabfragen use explicit full ranges.
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
        if payload:
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

    async def async_get_battery_pack_list(self, device_sn: str) -> list[dict[str, Any]]:
        """GET /v1/device/battery/pack/list — sub-battery pack status.

        App decompile (BatteryPackApi + BatteryPackSub):
            request: deviceSn
            fields: batSoc, cellTemp, inPw, outPw, version, isFirmwareUpgrade
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
        items = self._payload_list(data, OTA_LIST_PATH)
        return items[0] if items else {}

    async def async_get_location(self, device_id: str | int) -> dict:
        """GET /v1/device/location — GPS coordinates set by the user."""
        data = await self._get_json(
            LOCATION_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_location_responses[str(device_id)] = data
        return self._payload_dict(data, LOCATION_PATH)

    # --- HTTP write endpoints documented in APP_POLLING_MQTT.md --------------
    async def _put_json(self, path: str, payload: dict) -> dict:
        """Generic JSON PUT helper with token re-login on expiry."""
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
                f"{HTTP_METHOD_PUT} {path} request failed: {err}"
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info(
                "Jackery token expired — re-login for %s %s", HTTP_METHOD_PUT, path
            )
            self._token = None
            await self._ensure_token()
            try:
                status, data = await _do()
            except (TimeoutError, aiohttp.ClientError) as err:
                raise JackeryApiError(
                    f"{HTTP_METHOD_PUT} {path} request failed after re-login: {err}"
                ) from err

        if status != 200:
            raise JackeryApiError(f"{HTTP_METHOD_PUT} {path} HTTP {status}")
        code = self._extract_code(data)
        if code not in (CODE_OK, None):
            raise JackeryApiError(
                f"{HTTP_METHOD_PUT} {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)}"
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
        """PUT /v1/device/system/name — rename a system.

        Captured body: {"systemName": "SolarVault", "id": "<systemId>"}
        Response payload is a boolean: `data: true`.
        """
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

    async def _post_form(self, path: str, fields: dict[str, Any]) -> dict:
        """Generic form-urlencoded POST with auto re-login on expiry."""
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
                f"{HTTP_METHOD_POST} {path} request failed: {err}"
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
                raise JackeryApiError(
                    f"{HTTP_METHOD_POST} {path} request failed after re-login: {err}"
                ) from err

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
        """
        if not isinstance(max_power, int) or max_power < 0:
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
        """POST /v1/device/dynamic/saveSingleMode."""
        price = float(single_price)
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
        """POST /v1/device/dynamic/saveDynamicMode."""
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
        """GET /v1/device/bind/list — Explorer-series only, kept for compat."""
        try:
            data = await self._get_json(DEVICE_LIST_PATH)
        except JackeryError:
            return []
        return self._payload_list(data, DEVICE_LIST_PATH)
