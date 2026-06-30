"""Base HTTP infrastructure for the Jackery SolarVault cloud API.

Provides the shared HTTP primitives (GET/PUT/POST with auto-relogin),
token management, header construction, response normalization, and
diagnostic counters.  Domain-specific endpoint methods live in the
``_endpoints/`` sub-package mixins.
"""

import inspect
import json
import logging
import re
from typing import TYPE_CHECKING, Any

import aiohttp

from ..const import (
    APP_REQUEST_META,
    APP_VERSION,
    APP_VERSION_CODE,
    BASE_URL,
    CODE_OK,
    CODE_TOKEN_EXPIRED,
    DEVICE_MODEL_HEADER,
    FIELD_CODE,
    FIELD_DATA,
    FIELD_DEVICE_SN,
    FIELD_MSG,
    FIELD_RAW_TEXT,
    FIELD_TOKEN,
    HTTP_CONTENT_TYPE_FORM,
    HTTP_CONTENT_TYPE_JSON,
    HTTP_HEADER_CONTENT_TYPE,
    HTTP_METHOD_GET,
    HTTP_METHOD_POST,
    HTTP_METHOD_PUT,
    HTTP_RAW_TEXT_LIMIT,
    MQTT_SESSION_MAC_ID,
    MQTT_SESSION_MAC_ID_SOURCE,
    MQTT_SESSION_SEED_B64,
    MQTT_SESSION_USER_ID,
    PLATFORM_HEADER,
    REQUEST_TIMEOUT_SEC,
    SYS_VERSION,
    USER_AGENT,
)
from ..util import chart_series_debug
from ._crypto import _generate_udid

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class JackeryError(Exception):
    """Base exception."""


class JackeryAuthError(JackeryError):
    """Authentication failure."""


class JackeryApiError(JackeryError):
    """Generic API failure."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_accepted(data: dict[str, Any]) -> bool:
    """Determines whether a write response from the API should be treated as accepted.

    Parameters:
        data (dict): Parsed JSON response; inspected for the top-level `data` field.

    Returns:
        `True` if the response's `data` field is not explicitly `False`, `False` otherwise.
    """  # noqa: E501
    from ..util import safe_bool  # noqa: PLC0415

    return safe_bool(data.get(FIELD_DATA)) is not False


# ---------------------------------------------------------------------------
# BaseHTTPMixin
# ---------------------------------------------------------------------------


class BaseHTTPMixin:
    """Shared HTTP infrastructure: token management, request primitives, debug, diagnostics.

    Subclasses (domain mixins) use ``self._get_json``, ``self._put_json``,
    ``self._post_form`` without worrying about auth, retries, or counters.
    """  # noqa: E501

    # Declared here so BaseHTTPMixin methods (e.g. ``_get_token``) can call
    # ``self.async_login()`` even though the implementation lives in
    # ``AuthEndpointMixin``.  The concrete class ``JackeryApi`` always
    # combines both via multiple inheritance.
    async def async_login(self) -> str:
        """Perform an authentication flow and produce a session token for use in API requests.

        Returns:
            token (str): Session token string to include in authenticated request headers.
        """  # noqa: E501
        raise NotImplementedError

    # --- __init__ state (set by JackeryApi.__init__) -----------------------
    _session: aiohttp.ClientSession
    _account: str
    _password: str
    _region_code: str | None
    _mqtt_mac_id_configured: str | None
    _mqtt_mac_id_source: str
    _token: str | None
    _lock: asyncio.Lock
    _mqtt_user_id: str | None
    _mqtt_seed_b64: str | None
    _mqtt_mac_id: str | None

    # Diagnostics buffers
    last_login_response: dict[str, Any] | None
    last_system_list_response: dict[str, Any] | None
    last_property_responses: dict[str, dict[str, Any]]
    last_alarm_response: dict[str, Any] | None
    last_statistic_response: dict[str, Any] | None
    last_price_response: dict[str, Any] | None
    last_price_sources_response: dict[str, Any] | None
    last_price_history_config_response: dict[str, Any] | None
    last_device_statistic_responses: dict[str, dict[str, Any]]
    last_device_period_stat_responses: dict[str, dict[str, Any]]
    last_battery_pack_responses: dict[str, dict[str, Any]]
    last_ota_responses: dict[str, dict[str, Any]]
    last_location_responses: dict[str, dict[str, Any]]
    payload_debug_callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None

    # Transport counters
    _requests_total: int
    _requests_failed: int
    _timeouts_total: int
    _auth_retries: int

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
            "model": DEVICE_MODEL_HEADER,
            "network": "wifi",
            "platform": PLATFORM_HEADER,
            "sys_version": SYS_VERSION,
            "user-agent": USER_AGENT,
        }
        if with_token and self._token:
            h[FIELD_TOKEN] = self._token
        return h

    # --- auth helpers -------------------------------------------------------
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

        If a configured ``mqtt_mac_id`` is present and valid, that value is used and
        ``self._mqtt_mac_id_source`` is set to ``"configured"``.  Otherwise a
        deterministic MAC ID derived from the account is returned.

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

    def _maybe_learn_region_code(self, systems: list[dict[str, Any]]) -> None:
        """Set the API client's region code from the first system entry that provides a country code when no region is configured.

        If the client already has a region code set, this is a no-op. Otherwise, the method iterates the provided system dictionaries and, for the first item whose ``FIELD_COUNTRY_CODE`` yields a non-empty value, stores the trimmed uppercase country code on ``self._region_code`` and logs the inference.

        Parameters:
            systems (list[dict]): List of system metadata dictionaries returned by the system list API.
        """  # noqa: E501
        if self._region_code:
            return
        for item in systems:
            country = str(item.get("countryCode") or "").strip().upper()
            if not country:
                continue
            self._region_code = country
            _LOGGER.debug(
                "Jackery: inferred regionCode=%s from /v1/device/system/list",
                country,
            )
            return

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
        """Extracts the backend numeric ``code`` value from a parsed API response.

        Parameters:
            data (dict | Any): Parsed JSON response (expected dict) or any other value.

        Returns:
            int | None: The ``code`` parsed as an integer when present as an integer or a numeric string, ``None`` otherwise.
        """  # noqa: E501
        if not isinstance(data, dict):
            return None
        code = data.get(FIELD_CODE)
        if code is None:
            return None
        if isinstance(code, bool):
            return None
        if isinstance(code, int):
            return code
        if isinstance(code, str):
            text = code.strip()
            if not text:
                return None
            try:
                code_float = float(text)
            except ValueError:
                return None
            if code_float.is_integer():
                return int(code_float)
        return None

    def _is_token_expired_response(self, status: int, data: object) -> bool:
        """Detects whether an API response indicates the authentication token has expired.

        Parameters:
            status (int): HTTP status code from the response.
            data (object): Parsed response body; expected to be a dict containing backend fields like `code` or `msg`.

        Returns:
            True if the response indicates the token has expired, False otherwise.
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

        Returns:
            ``True`` if any auth-related marker is present in the response text, ``False`` otherwise.
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
        """Determine whether an HTTP response indicates an authentication or authorization failure that should trigger re-authentication.

        Parameters:
            status (int): HTTP status code of the response.
            data (object): Parsed response payload (typically a dict) or raw response content.

        Returns:
            bool: `True` if the response indicates an authentication or authorization failure, `False` otherwise.
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
        """Builds a concise authorization-failure message for an HTTP request.

        Returns:
            str: Formatted message containing the HTTP method, request path, status code,
            backend `code` value, and the backend message/error text.
        """  # noqa: E501
        code = data.get(FIELD_CODE)
        msg = data.get(FIELD_MSG) or data.get("message") or data.get("error")
        return (
            f"{method} {path} authorization failed: HTTP {status} code={code} msg={msg}"
        )

    # --- debug --------------------------------------------------------------
    async def _emit_payload_debug(
        self,
        event_or_factory: dict[str, Any] | Callable[[], dict[str, Any]],
    ) -> None:
        """Forward a payload-debug event to the configured payload debug callback.

        If no callback is configured this is a no-op. Accepts either a prepared event dict
        or a zero-argument factory that produces the event dict when invoked. If the
        callback returns an awaitable it will be awaited. Exceptions raised by the
        callback are caught and logged at debug level.
        """  # noqa: E501
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
        """Construct a structured debug event describing an HTTP request/response for payload debugging.

        The event contains method, path, params, request_body, status, response, and response_data_type; when the response payload supports chart debugging, includes a `chart_series_debug` entry.

        Returns:
            dict[str, Any]: A mapping representing the debug event suitable for redaction and emission.
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

    # --- payload normalization -----------------------------------------------
    @staticmethod
    def _payload_dict(data: dict[str, Any], path: str) -> dict[str, Any]:
        """Normalize the API response's `data` field into a dict payload.

        If `FIELD_DATA` contains a dict that dict is returned. If `FIELD_DATA` is `None`, an empty dict is returned. For any other value an empty dict is returned and a warning is emitted.

        Returns:
            dict: The payload dict from `FIELD_DATA` when present and a dict, otherwise an empty dict.
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
        """Return the list of dictionary items contained in the response payload.

        If the response's FIELD_DATA is a list, returns only elements that are dictionaries.
        If FIELD_DATA is None, returns an empty list. For any other payload shape, logs a warning
        including `path` and returns an empty list.

        Parameters:
            data (dict[str, Any]): Parsed API response object.
            path (str): Request path used in warning messages when the payload shape is unexpected.

        Returns:
            list[dict[str, Any]]: The list of dictionary items from the payload, or an empty list.
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
        """Select the OTA update entry matching the given device serial number.

        Parameters:
            items (list[dict[str, Any]]): List of OTA item dictionaries.
            device_sn (str): Device serial number to match.

        Returns:
            dict[str, Any]: The matching item; if none match returns the first item when available, otherwise an empty dict.
        """  # noqa: E501
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
        """Add request context metadata to an API response for diagnostics.

        Parameters:
            data (dict[str, Any]): Parsed response object to copy and annotate.
            path (str): Request path to record under request metadata.
            params (dict[str, str]): Request query parameters to record under request metadata.
            payload_request (dict[str, str] | None): Optional metadata to insert into the payload object (the response's `FIELD_DATA`) if it is a dict.

        Returns:
            dict[str, Any]: A copy of `data` with `APP_REQUEST_META` ensured at the top level (containing `path` and `params`), and with `APP_REQUEST_META` added to the payload dict when `payload_request` is provided.
        """  # noqa: E501
        response = dict(data)
        response.setdefault(APP_REQUEST_META, {"path": path, "params": dict(params)})
        payload = response.get(FIELD_DATA)
        if isinstance(payload, dict) and payload_request is not None:
            payload = dict(payload)
            payload.setdefault(APP_REQUEST_META, dict(payload_request))
            response[FIELD_DATA] = payload
        return response

    # --- generic GET with auto re-login ------------------------------------
    async def _get_json(
        self,
        path: str,
        params: dict | None = None,
        *,
        request_timeout: int | None = None,
    ) -> dict:
        """Perform an authenticated GET request to the API path and return the parsed response body.

        Parameters:
            path (str): API path (appended to the base URL).
            params (dict | None): Optional query parameters to include in the request.
            request_timeout (int | None): Override the default request timeout in seconds.

        Returns:
            dict: Parsed JSON response body.

        Raises:
            JackeryAuthError: When the response indicates an authentication/authorization failure.
            JackeryApiError: For network/timeout errors, non-200 HTTP responses, or backend errors.
        """  # noqa: E501
        await self._ensure_token()
        url = f"{BASE_URL}{path}"
        effective_timeout = request_timeout or REQUEST_TIMEOUT_SEC

        async def _do() -> tuple[int, dict]:
            """Perform an HTTP GET request and parse the response into a status code and response body.

            Attempts to decode the response as JSON; if decoding fails, captures the response text truncated to HTTP_RAW_TEXT_LIMIT under FIELD_RAW_TEXT.

            Returns:
                tuple[int, dict]: A pair of the HTTP status code and the parsed response body (JSON object or a dict containing `FIELD_RAW_TEXT` on decode failure).
            """  # noqa: E501
            async with self._session.get(
                url,
                params=params,
                headers=self._headers(with_token=True),
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
            _LOGGER.info("Jackery token expired — re-login for GET %s", path)
            self._auth_retries += 1
            async with self._lock:
                self._token = None
                await self.async_login()
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

        if FIELD_RAW_TEXT in data:
            raise JackeryApiError(  # noqa: TRY003
                f"{HTTP_METHOD_GET} {path} returned invalid JSON: "
                f"{data[FIELD_RAW_TEXT]!r}"
            )
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

    # --- generic PUT with auto re-login ------------------------------------
    async def _put_json(self, path: str, payload: dict) -> dict:
        """Send a JSON PUT request to the given API path and return the parsed response; if the token is expired the method will re-authenticate and retry the request once.

        Returns:
            dict: Parsed JSON response from the API.

        Raises:
            JackeryAuthError: When the response indicates an authentication or authorization failure.
            JackeryApiError: On network/request failures, timeouts, non-200 HTTP status, or backend error codes.
        """  # noqa: E501
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        def _request_headers() -> dict[str, str]:
            """Build HTTP headers for JSON requests, including authentication headers when available.

            Returns:
                dict[str, str]: Mapping of HTTP header names to values with the content-type set to JSON and token headers included when present.
            """  # noqa: E501
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_JSON
            return headers

        async def _do() -> tuple[int, dict]:
            """Perform an HTTP PUT to the prepared URL and return the response status and parsed body.

            Returns:
                A tuple (status, body) where `status` is the HTTP status code and `body` is the parsed JSON response when available; if the response cannot be decoded as JSON, `body` is a dict containing `FIELD_RAW_TEXT` with the response text truncated to HTTP_RAW_TEXT_LIMIT.
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
            self._auth_retries += 1
            async with self._lock:
                self._token = None
                await self.async_login()
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

    # --- generic POST with auto re-login -----------------------------------
    async def _post_form(self, path: str, fields: dict[str, Any]) -> dict:
        """Send a form-urlencoded POST to the Jackery API, retrying once after automatic re-login if the token is expired.

        Parameters:
            path (str): API endpoint path appended to the base URL.
            fields (dict[str, Any]): Form fields to send; all values will be converted to strings.

        Returns:
            dict: Parsed JSON response from the API.

        Raises:
            JackeryApiError: On network/timeout failures, non-200 HTTP status, or backend errors.
            JackeryAuthError: When the response indicates an authentication or authorization failure.
        """  # noqa: E501
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        def _request_headers() -> dict[str, str]:
            """Constructs HTTP request headers for form-encoded requests, including the form Content-Type and the current authentication token when available.

            Returns:
                dict[str, str]: Mapping of header names to values for a form POST request; includes the authentication token header if the client has one.
            """  # noqa: E501
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_FORM
            return headers

        body = {k: str(v) for k, v in fields.items()}

        async def _do() -> tuple[int, dict]:
            """Send a POST request to the prepared URL and return the HTTP status and parsed response payload.

            Attempts to parse the response body as JSON. If JSON parsing or content-type decoding fails, returns a dictionary containing the response text truncated to HTTP_RAW_TEXT_LIMIT under the key `FIELD_RAW_TEXT`.

            Returns:
                tuple[int, dict]: A pair of (HTTP status code, parsed response). The response dict is either the decoded JSON object or `{FIELD_RAW_TEXT: <truncated text>}` on parse failure.
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
            self._auth_retries += 1
            async with self._lock:
                self._token = None
                await self.async_login()
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

    # --- JSON POST with auto re-login --------------------------------------
    async def _post_json(self, path: str, payload: dict[str, Any]) -> dict:
        """Send a JSON POST to the Jackery API and return the parsed response, retrying once after an automatic re-login if the token is expired.

        Parameters:
            path (str): Request path appended to the base API URL.
            payload (dict[str, Any]): JSON-serializable request body.

        Returns:
            response (dict): Parsed response object from the API.
        """  # noqa: E501
        await self._ensure_token()
        url = f"{BASE_URL}{path}"

        def _request_headers() -> dict[str, str]:
            """Build HTTP headers for JSON requests, including authentication headers when available.

            Returns:
                dict[str, str]: Mapping of HTTP header names to values with the content-type set to JSON and token headers included when present.
            """  # noqa: E501
            headers = self._headers(with_token=True)
            headers[HTTP_HEADER_CONTENT_TYPE] = HTTP_CONTENT_TYPE_JSON
            return headers

        async def _do() -> tuple[int, dict]:
            """Send the POST request to the given URL with the prepared JSON payload and return the HTTP status and parsed response.

            Returns:
                tuple[int, dict]: A pair of (status, data) where `status` is the HTTP status code and `data` is the parsed JSON response when parsing succeeds. If the response cannot be parsed as JSON, `data` is a dict containing `FIELD_RAW_TEXT` with the response text truncated to `HTTP_RAW_TEXT_LIMIT`.
            """  # noqa: E501
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
            status, data = await _do()
        except (TimeoutError, aiohttp.ClientError) as err:
            self._requests_failed += 1
            if isinstance(err, TimeoutError):
                self._timeouts_total += 1
            raise JackeryApiError(  # noqa: TRY003
                f"POST {path} request failed: "
                f"{type(err).__name__}: {err or '(no message)'}"
            ) from err
        if self._is_token_expired_response(status, data):
            _LOGGER.info("Jackery token expired — re-login for POST %s", path)
            self._auth_retries += 1
            async with self._lock:
                self._token = None
                await self.async_login()
            try:
                status, data = await _do()
            except (TimeoutError, aiohttp.ClientError) as err:
                self._requests_failed += 1
                if isinstance(err, TimeoutError):
                    self._timeouts_total += 1
                raise JackeryApiError(  # noqa: TRY003
                    f"POST {path} request failed after re-login: "
                    f"{type(err).__name__}: {err or '(no message)'}"
                ) from err

        if self._is_auth_failure_response(status, data):
            raise JackeryAuthError(
                self._auth_failure_message("POST", path, status, data)
            )
        if status != 200:  # noqa: PLR2004
            raise JackeryApiError(f"POST {path} HTTP {status}")  # noqa: TRY003
        code = self._extract_code(data)
        if code not in {CODE_OK, None}:
            raise JackeryApiError(  # noqa: TRY003
                f"POST {path} code={data.get(FIELD_CODE)} msg={data.get(FIELD_MSG)!r} "
                f"data={data.get(FIELD_DATA)!r}"
            )
        await self._emit_payload_debug(
            self._http_payload_debug(
                method="POST",
                path=path,
                body=payload,
                status=status,
                response=data,
            )
        )
        return data

    # --- diagnostics --------------------------------------------------------
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

    # --- MQTT session persistence -------------------------------------------
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
            mac_id_source (str | None): Optional source descriptor for the MAC identifier; if provided, sets the MAC ID source.
        """  # noqa: E501
        self._mqtt_user_id = user_id or None
        self._mqtt_seed_b64 = seed_b64 or None
        self._mqtt_mac_id = mac_id or None
        if mac_id_source:
            self._mqtt_mac_id_source = mac_id_source

    def mqtt_session_snapshot(self) -> dict[str, str] | None:
        """Return a serializable snapshot of the current MQTT session for persistence or None if incomplete.

        Returns:
            snapshot (dict[str, str] | None): A dict containing `MQTT_SESSION_USER_ID`, `MQTT_SESSION_SEED_B64`, `MQTT_SESSION_MAC_ID`, and `MQTT_SESSION_MAC_ID_SOURCE` when all required fields are present, otherwise `None`.
        """  # noqa: E501
        if not (self._mqtt_user_id and self._mqtt_seed_b64 and self._mqtt_mac_id):
            return None
        return {
            MQTT_SESSION_USER_ID: self._mqtt_user_id,
            MQTT_SESSION_SEED_B64: self._mqtt_seed_b64,
            MQTT_SESSION_MAC_ID: self._mqtt_mac_id,
            MQTT_SESSION_MAC_ID_SOURCE: self._mqtt_mac_id_source,
        }
