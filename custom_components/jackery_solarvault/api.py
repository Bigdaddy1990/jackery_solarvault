"""Async API client for the Jackery SolarVault cloud (iot.jackeryapp.com).

All endpoint paths in this file have been verified by capturing HTTPS
traffic from the official Jackery Android app v2.0.1. Each endpoint is
annotated with its confirmed shape.

Auth:     /v1/auth/login              (AES-128-ECB + RSA-1024 hybrid)
Systems:  /v1/device/system/list      (no params)
Device:   /v1/device/property         (?deviceId=<long>)
Alarms:   /v1/api/alarm               (?systemId=<long>)
Stats:    /v1/device/stat/systemStatistic     (?systemId=<long>)
Trends:   /v1/device/stat/sys/pv/trends       (?systemId&beginDate&endDate&dateType)
Price:    /v1/device/dynamic/powerPriceConfig (?systemId=<long>)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import uuid
from datetime import date
from typing import Any

import aiohttp

from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.serialization import load_der_public_key

from .const import (
    AES_KEY,
    ALARM_PATH,
    APP_VERSION,
    APP_VERSION_CODE,
    BASE_URL,
    BATTERY_TRENDS_PATH,
    CODE_OK,
    CODE_TOKEN_EXPIRED,
    DEVICE_LIST_PATH,
    DEVICE_MODEL_HEADER,
    DEVICE_PROPERTY_PATH,
    DEVICE_STATISTIC_PATH,
    HOME_TRENDS_PATH,
    LOCATION_PATH,
    LOGIN_PATH,
    OTA_LIST_PATH,
    PLATFORM_HEADER,
    POWER_PRICE_PATH,
    PV_TRENDS_PATH,
    REGISTER_APP_ID,
    RSA_PUBLIC_KEY_B64,
    SYS_VERSION,
    SYSTEM_LIST_PATH,
    SYSTEM_NAME_PATH,
    SYSTEM_STATISTIC_PATH,
    USER_AGENT,
)

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


def _rsa_pkcs1v15_encrypt(data: bytes, public_key_b64: str) -> bytes:
    der_bytes = base64.b64decode(public_key_b64)
    public_key = load_der_public_key(der_bytes)
    return public_key.encrypt(data, asym_padding.PKCS1v15())


def _generate_udid(seed: str) -> str:
    md5_digest = hashlib.md5(seed.encode("utf-8")).digest()
    u = uuid.UUID(bytes=md5_digest, version=3)
    return "2" + str(u).replace("-", "")


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
    ) -> None:
        self._session = session
        self._account = account
        self._password = password
        self._token: str | None = None
        self._lock = asyncio.Lock()

        # Diagnostics buffers
        self.last_login_response: dict[str, Any] | None = None
        self.last_system_list_response: dict[str, Any] | None = None
        self.last_property_responses: dict[str, dict[str, Any]] = {}
        self.last_alarm_response: dict[str, Any] | None = None
        self.last_statistic_response: dict[str, Any] | None = None
        self.last_price_response: dict[str, Any] | None = None
        self.last_device_statistic_responses: dict[str, dict[str, Any]] = {}
        self.last_ota_responses: dict[str, dict[str, Any]] = {}
        self.last_location_responses: dict[str, dict[str, Any]] = {}

    # --- headers ------------------------------------------------------------
    def _headers(self, *, with_token: bool = False) -> dict[str, str]:
        """Headers matching Jackery Android app v2.0.1 (April 2026)."""
        h = {
            "accept-encoding": "gzip",
            "accept-language": "de-DE",
            "app_version": APP_VERSION,
            "app_version_code": APP_VERSION_CODE,
            "connection": "Keep-Alive",
            "host": "iot.jackeryapp.com",
            "model": DEVICE_MODEL_HEADER,
            "network": "wifi",
            "platform": PLATFORM_HEADER,
            "sys_version": SYS_VERSION,
            "user-agent": USER_AGENT,
        }
        if with_token and self._token:
            h["token"] = self._token
        return h

    # --- auth ---------------------------------------------------------------
    async def async_login(self) -> str:
        """Encrypted login; stores the JWT session token."""
        login_bean = {
            "account": self._account,
            "loginType": 2,
            "macId": _generate_udid(self._account),
            "password": self._password,
            "phone": "",
            "registerAppId": REGISTER_APP_ID,
            "verificationCode": "",
        }

        plaintext = json.dumps(login_bean, ensure_ascii=False).encode("utf-8")
        aes_blob = base64.b64encode(_aes_ecb_encrypt(plaintext, AES_KEY)).decode("ascii")
        rsa_blob = base64.b64encode(
            _rsa_pkcs1v15_encrypt(AES_KEY, RSA_PUBLIC_KEY_B64)
        ).decode("ascii")

        url = f"{BASE_URL}{LOGIN_PATH}"

        # The Android app sends login params as form-urlencoded body, not as
        # query string. This matches the captured traffic byte-for-byte.
        headers = self._headers()
        headers["content-type"] = "application/x-www-form-urlencoded"
        form_body = {"aesEncryptData": aes_blob, "rsaForAesKey": rsa_blob}

        try:
            async with self._session.post(
                url, data=form_body, headers=headers, timeout=30,
            ) as resp:
                if resp.status != 200:
                    raise JackeryApiError(f"Login HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise JackeryApiError(f"Login request failed: {err}") from err

        # Store redacted version for diagnostics
        redacted = dict(data)
        if "token" in redacted:
            redacted["token"] = "**REDACTED**"
        if isinstance(redacted.get("data"), dict):
            inner = dict(redacted["data"])
            if "mqttPassWord" in inner:
                inner["mqttPassWord"] = "**REDACTED**"
            redacted["data"] = inner
        self.last_login_response = redacted

        if data.get("code") != CODE_OK:
            raise JackeryAuthError(
                f"Login rejected (code={data.get('code')}, msg={data.get('msg')})"
            )

        token = data.get("token") or ""
        if not token:
            raise JackeryAuthError("Login succeeded but no token returned")

        self._token = token
        return token

    async def _ensure_token(self) -> str:
        if self._token is None:
            async with self._lock:
                if self._token is None:
                    await self.async_login()
        assert self._token is not None
        return self._token

    # --- generic GET with auto re-login ------------------------------------
    async def _get_json(self, path: str, params: dict | None = None) -> dict:
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        async def _do() -> tuple[int, dict]:
            async with self._session.get(
                url, params=params, headers=self._headers(with_token=True),
                timeout=30,
            ) as resp:
                status = resp.status
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = {"_raw_text": (await resp.text())[:500]}
                return status, body

        status, data = await _do()
        if (
            status == 200
            and isinstance(data, dict)
            and data.get("code") == CODE_TOKEN_EXPIRED
        ):
            _LOGGER.info("Jackery token expired — re-login")
            self._token = None
            await self._ensure_token()
            status, data = await _do()

        if status != 200:
            raise JackeryApiError(f"GET {path} HTTP {status}")
        if isinstance(data, dict) and data.get("code") not in (CODE_OK, None):
            raise JackeryApiError(
                f"GET {path} code={data.get('code')} msg={data.get('msg')}"
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
                            "rb": <SOC>, "isCloud": false, ...}],
                ...
            }]}
        """
        data = await self._get_json(SYSTEM_LIST_PATH)
        self.last_system_list_response = data
        raw = data.get("data")
        if isinstance(raw, list):
            return raw
        _LOGGER.warning("system/list returned unexpected shape: %s", type(raw))
        return []

    async def async_get_device_property(self, device_id: str | int) -> dict:
        """GET /v1/device/property — device + properties dict."""
        data = await self._get_json(
            DEVICE_PROPERTY_PATH, params={"deviceId": str(device_id)}
        )
        self.last_property_responses[str(device_id)] = data
        return data.get("data") or {}

    async def async_get_alarm(self, system_id: str | int) -> Any:
        """GET /v1/api/alarm — alarm list for a system."""
        data = await self._get_json(
            ALARM_PATH, params={"systemId": str(system_id)}
        )
        self.last_alarm_response = data
        return data.get("data")

    async def async_get_system_statistic(self, system_id: str | int) -> dict:
        """GET /v1/device/stat/systemStatistic — today/total KPIs.

        Response keys (verified):
            todayLoad, todayBatteryDisChg, todayBatteryChg, todayGeneration,
            totalGeneration, totalRevenue, totalCarbon, isSetPrice
        """
        data = await self._get_json(
            SYSTEM_STATISTIC_PATH, params={"systemId": str(system_id)}
        )
        self.last_statistic_response = data
        return data.get("data") or {}

    async def async_get_pv_trends(
        self,
        system_id: str | int,
        *,
        date_type: str = "day",
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """GET /v1/device/stat/sys/pv/trends — historical curves."""
        today = date.today().isoformat()
        params = {
            "systemId": str(system_id),
            "dateType": date_type,
            "beginDate": begin_date or today,
            "endDate": end_date or today,
        }
        data = await self._get_json(PV_TRENDS_PATH, params=params)
        return data.get("data") or {}

    async def async_get_power_price(self, system_id: str | int) -> dict:
        """GET /v1/device/dynamic/powerPriceConfig — tariff config."""
        data = await self._get_json(
            POWER_PRICE_PATH, params={"systemId": str(system_id)}
        )
        self.last_price_response = data
        return data.get("data") or {}

    # --- v1.2.0 additions --------------------------------------------------
    async def async_get_device_statistic(self, device_id: str | int) -> dict:
        """GET /v1/device/stat/deviceStatistic — lifetime energy totals.

        Response keys (all strings in kWh):
            pvEgy, inEpsEgy, ongridOtBatEgy, pvOtBatEgy, inOngridEgy,
            outOngridEgy, batOtGridEgy, outEpsEgy, batDisChgEgy,
            acOtBatEgy, batOtAcEgy, batChgEgy
        """
        data = await self._get_json(
            DEVICE_STATISTIC_PATH, params={"deviceId": str(device_id)}
        )
        self.last_device_statistic_responses[str(device_id)] = data
        return data.get("data") or {}

    async def async_get_home_trends(
        self,
        system_id: str | int,
        *,
        date_type: str = "day",
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """GET /v1/device/stat/sys/home/trends — home consumption breakdown."""
        today = date.today().isoformat()
        params = {
            "systemId": str(system_id),
            "dateType": date_type,
            "beginDate": begin_date or today,
            "endDate": end_date or today,
        }
        data = await self._get_json(HOME_TRENDS_PATH, params=params)
        return data.get("data") or {}

    async def async_get_battery_trends(
        self,
        system_id: str | int,
        *,
        date_type: str = "day",
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """GET /v1/device/stat/sys/battery/trends — battery charge/discharge history."""
        today = date.today().isoformat()
        params = {
            "systemId": str(system_id),
            "dateType": date_type,
            "beginDate": begin_date or today,
            "endDate": end_date or today,
        }
        data = await self._get_json(BATTERY_TRENDS_PATH, params=params)
        return data.get("data") or {}

    async def async_get_ota_info(self, device_sn: str) -> dict:
        """GET /v1/device/ota/list — firmware version + available updates."""
        data = await self._get_json(OTA_LIST_PATH, params={"deviceSnList": device_sn})
        self.last_ota_responses[device_sn] = data
        payload = data.get("data")
        if isinstance(payload, list) and payload:
            return payload[0]
        return {}

    async def async_get_location(self, device_id: str | int) -> dict:
        """GET /v1/device/location — GPS coordinates set by the user."""
        data = await self._get_json(
            LOCATION_PATH, params={"deviceId": str(device_id)}
        )
        self.last_location_responses[str(device_id)] = data
        return data.get("data") or {}

    # --- writers (v1.2.0) ---------------------------------------------------
    async def _put_json(self, path: str, payload: dict) -> dict:
        """Generic JSON PUT helper with token re-login on expiry."""
        await self._ensure_token()
        url = f"{BASE_URL}{path}"
        headers = self._headers(with_token=True)
        headers["content-type"] = "application/json; charset=utf-8"

        async def _do() -> tuple[int, dict]:
            async with self._session.put(
                url, json=payload, headers=headers, timeout=30,
            ) as resp:
                status = resp.status
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = {"_raw_text": (await resp.text())[:500]}
                return status, body

        status, data = await _do()
        if (
            status == 200
            and isinstance(data, dict)
            and data.get("code") == CODE_TOKEN_EXPIRED
        ):
            _LOGGER.info("Jackery token expired — re-login for PUT %s", path)
            self._token = None
            await self._ensure_token()
            status, data = await _do()

        if status != 200:
            raise JackeryApiError(f"PUT {path} HTTP {status}")
        if isinstance(data, dict) and data.get("code") not in (CODE_OK, None):
            raise JackeryApiError(
                f"PUT {path} code={data.get('code')} msg={data.get('msg')}"
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
            {"systemName": system_name.strip(), "id": str(system_id)},
        )
        return bool(data.get("data"))

    # --- legacy fallback ----------------------------------------------------
    async def async_list_devices_legacy(self) -> list[dict[str, Any]]:
        """GET /v1/device/bind/list — Explorer-series only, kept for compat."""
        try:
            data = await self._get_json(DEVICE_LIST_PATH)
        except JackeryError:
            return []
        raw = data.get("data")
        return raw if isinstance(raw, list) else []
