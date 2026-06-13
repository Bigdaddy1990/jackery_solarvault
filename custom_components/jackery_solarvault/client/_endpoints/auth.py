"""Auth, login, and MQTT credential endpoints."""

import base64
import binascii
import json
from typing import Any

import aiohttp

from ...const import (
    AES_KEY,
    BASE_URL,
    CANCEL_ACCOUNT_PATH,
    CHECK_VERIFY_CODE_PATH,
    CODE_OK,
    FIELD_ACCOUNT,
    FIELD_CODE,
    FIELD_DATA,
    FIELD_LOGIN_TYPE,
    FIELD_MAC_ID,
    FIELD_MQTT_PASSWORD,
    FIELD_MSG,
    FIELD_PASSWORD,
    FIELD_REGION_CODE,
    FIELD_REGISTER_APP_ID,
    FIELD_TOKEN,
    FIELD_USER_ID,
    HTTP_CONTENT_TYPE_FORM,
    HTTP_HEADER_CONTENT_TYPE,
    HTTP_METHOD_POST,
    HTTP_RAW_TEXT_LIMIT,
    LOGIN_PATH,
    LOGIN_TIMEOUT_SEC,
    LOGOUT_PATH,
    MODIFY_INFO_PATH,
    MQTT_CLIENT_ID_SUFFIX,
    MQTT_CREDENTIAL_CLIENT_ID,
    MQTT_CREDENTIAL_PASSWORD,
    MQTT_CREDENTIAL_USERNAME,
    MQTT_CREDENTIAL_USER_ID,
    MQTT_USERNAME_SEPARATOR,
    REGISTER_APP_ID,
    REGISTER_PATH,
    RESET_PASSWORD_PATH,
    RSA_PUBLIC_KEY_B64,
    UPDATE_REGISTER_ID_PATH,
    UPLOAD_HEADIMG_PATH,
    USER_INFO_PATH,
    VERIFY_CODE_PATH,
)
from .._crypto import _aes_cbc_encrypt, _aes_ecb_encrypt, _rsa_pkcs1v15_encrypt
from .._http import BaseHTTPMixin, JackeryApiError, JackeryAuthError


class AuthEndpointMixin(BaseHTTPMixin):
    """Auth, login, and MQTT credential methods."""

    async def async_login(self) -> str:
        """Perform the encrypted login flow and persist the returned session and MQTT credentials.

        Returns:
            token (str): The JWT session token returned by the server.

        Raises:
            JackeryAuthError: If the backend rejects credentials, reports a non-OK code, or returns no token.
            JackeryApiError: For network errors, non-200 HTTP responses, or invalid/non-parsable JSON responses.
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

        try:
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
                    if not isinstance(data, dict):
                        raw = (await resp.text())[:HTTP_RAW_TEXT_LIMIT]
                        raise JackeryApiError(  # noqa: TRY003
                            f"Login returned JSON {type(data).__name__}, expected object: {raw!r}"
                        )
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
                if not isinstance(data, dict):
                    raw = str(data)[:HTTP_RAW_TEXT_LIMIT]
                    raise JackeryApiError(  # noqa: TRY003
                        f"Login returned non-object JSON: {raw!r}"
                    )
        except (TimeoutError, aiohttp.ClientError) as err:
            raise JackeryApiError(  # noqa: TRY003
                f"Login request failed: {type(err).__name__}: {err or '(no message)'}"
            ) from err

        safe_response = dict(data)
        safe_response.pop(FIELD_TOKEN, None)
        inner = safe_response.get(FIELD_DATA)
        if isinstance(inner, dict):
            scrubbed_inner = dict(inner)
            scrubbed_inner.pop(FIELD_MQTT_PASSWORD, None)
            safe_response[FIELD_DATA] = scrubbed_inner

        await self._emit_payload_debug(
            self._http_payload_debug(
                method=HTTP_METHOD_POST,
                path=LOGIN_PATH,
                body={"form_fields": sorted(form_body)},
                status=200,
                response=safe_response,
            )
        )

        if self._extract_code(data) != CODE_OK:
            raise JackeryAuthError(  # noqa: TRY003
                f"Login rejected (code={data.get(FIELD_CODE)}, msg={data.get(FIELD_MSG)})"
            )

        self.last_login_response = dict(data)
        token = data.get(FIELD_TOKEN) or ""
        if not token:
            raise JackeryAuthError("Login succeeded but no token returned")  # noqa: TRY003

        self._token = token
        payload = data.get(FIELD_DATA) or {}
        if not isinstance(payload, dict):
            raise JackeryApiError(  # noqa: TRY003
                f"Login returned data {type(payload).__name__}, expected object"
            )
        self._mqtt_user_id = str(payload.get(FIELD_USER_ID) or "") or None
        self._mqtt_seed_b64 = payload.get(FIELD_MQTT_PASSWORD) or None
        self._mqtt_mac_id = mac_id
        return token

    async def async_get_mqtt_credentials(self) -> dict[str, str]:
        """Construct MQTT connection credentials from the active authenticated session.

        Returns:
            dict[str, str]: Mapping with keys:
                - ``clientId``: MQTT client identifier composed from the login user id and client suffix.
                - ``username``: MQTT username composed from the login user id and MAC id.
                - ``password``: Base64-encoded AES-CBC encryption of ``username`` using the login-provided seed.
                - ``userId``: MQTT user id from the login response.

        Raises:
            JackeryAuthError: If the session is missing required MQTT fields, if the MQTT seed is not valid base64, or if the decoded seed is not exactly 32 bytes.
        """
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
        """Current MQTT fingerprint identifying the session's MQTT credentials.

        Returns:
            tuple[str | None, str | None, str | None]: `(user_id, mac_id, seed_b64)` where
                `user_id` is the MQTT user identifier or `None`,
                `mac_id` is the MQTT MAC identifier or `None`,
                `seed_b64` is the base64-encoded MQTT seed or `None`.
        """
        return (self._mqtt_user_id, self._mqtt_mac_id, self._mqtt_seed_b64)

    @property
    def mqtt_mac_id_source(self) -> str:
        """Identify the source of the current MQTT MAC ID.

        Returns:
            source (str): A string describing how the MQTT MAC ID was obtained (for example, the provider or method).
        """
        return self._mqtt_mac_id_source

    @property
    def mqtt_mac_id(self) -> str | None:
        """Get the MQTT MAC ID for the current session.

        Returns:
            str | None: The MQTT MAC ID as a string, or None if no MAC ID is available.
        """
        return self._mqtt_mac_id

    @property
    def region_code(self) -> str | None:
        """Return the region code used for HTTP login calls.

        Returns:
            str | None: The pinned region code, or None if none is set.
        """
        return self._region_code

    # --- New auth endpoints (PROTOCOL.md §2) --------------------------------

    async def async_register(
        self,
        *,
        email: str,
        password: str,
        region_code: str,
        verification_code: str,
    ) -> dict[str, Any]:
        """Create a new Jackery account using the provided email, password, region code, and verification code.

        Parameters:
            email (str): Email address for the new account.
            password (str): Desired account password.
            region_code (str): Region or country code to register the account under.
            verification_code (str): Verification code sent to the email.

        Returns:
            dict[str, Any]: Decoded backend response payload.
        """
        return await self._post_json(
            REGISTER_PATH,
            {
                "email": email,
                "password": password,
                "regionCode": region_code,
                "registerAppId": REGISTER_APP_ID,
                "verificationCode": verification_code,
            },
        )

    async def async_logout(self) -> bool:
        """Log out the current session.

        Returns:
            bool: ``True`` if the server acknowledged the logout.
        """
        data = await self._post_json(LOGOUT_PATH, {})
        return self._extract_code(data) == CODE_OK

    async def async_send_verification_code(
        self,
        *,
        email: str,
        method: str = "email",
        phone: str = "",
    ) -> dict[str, Any]:
        """Request a verification code (email or SMS).

        Parameters:
            email: Email address to send the code to.
            method: ``"email"`` or ``"sms"``.
            phone: Phone number (required when method is ``"sms"``).

        Returns:
            dict: Backend response data.
        """
        payload: dict[str, Any] = {"email": email, "method": method}
        if phone:
            payload["phone"] = phone
        return await self._post_json(VERIFY_CODE_PATH, payload)

    async def async_check_verification_code(
        self,
        *,
        code: str,
        email: str,
        method: str = "email",
        phone: str = "",
    ) -> dict[str, Any]:
        """Verify a previously sent verification code.

        Parameters:
            code: The verification code received.
            email: Email address the code was sent to.
            method: ``"email"`` or ``"sms"``.
            phone: Phone number (required when method is ``"sms"``).

        Returns:
            dict: Backend response data.
        """
        payload: dict[str, Any] = {
            "code": code,
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
        """Reset account password using a verification code.

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

    async def async_update_user_info(self, *, nick_name: str) -> dict[str, Any]:
        """Update the user's display name.

        Parameters:
            nick_name (str): New display name; sent to the backend as the `nickName` field.

        Returns:
            dict[str, Any]: Decoded backend response data.
        """
        return await self._post_json(MODIFY_INFO_PATH, {"nickName": nick_name})

    async def async_upload_headimg(self, image: str) -> dict[str, Any]:
        """Upload a profile image (base64-encoded).

        Parameters:
            image: Base64-encoded image data.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(UPLOAD_HEADIMG_PATH, {"image": image})

    async def async_get_user_info(self) -> dict[str, Any]:
        """Return the decoded backend payload for the current user's profile.

        Returns:
            dict[str, Any]: User profile data as returned by the backend.
        """
        data = await self._get_json(USER_INFO_PATH)
        return self._payload_dict(data, USER_INFO_PATH)

    async def async_cancel_account(
        self, *, email: str, verification_code: str
    ) -> dict[str, Any]:
        """Cancel the authenticated user's account using a verification code.

        Returns:
            dict: Backend response payload.
        """
        return await self._post_json(
            CANCEL_ACCOUNT_PATH,
            {"email": email, "verificationCode": verification_code},
        )

    async def async_update_register_id(self, *, register_id: str) -> dict[str, Any]:
        """Update the push notification registration ID for the authenticated account.

        Parameters:
            register_id (str): Push notification registration token to associate with the account.

        Returns:
            dict: Backend response payload.
        """
        return await self._post_json(
            UPDATE_REGISTER_ID_PATH, {"registerId": register_id}
        )
