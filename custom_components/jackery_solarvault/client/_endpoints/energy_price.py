"""Energy price, tariff, and dynamic-pricing endpoint mixins."""

from typing import Any

from ...const import (
    BIND_CURRENCY_PATH,
    CANCEL_CONTRACT_PATH,
    CONTRACT_LIST_PATH,
    CURRENCY_LIST_PATH,
    DEVICE_CURRENCY_PATH,
    DYNAMIC_PRICE_LOGIN_URL_PATH,
    DYNAMIC_PRICE_PATH,
    FIELD_CURRENCY,
    FIELD_DEVICE_ID,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_SINGLE_PRICE,
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_REGION,
    POWER_PRICE_PATH,
    PRICE_HISTORY_CONFIG_PATH,
    PRICE_SOURCE_LIST_PATH,
    QUERY_TOU_PLAN_PATH,
    SAVE_CONTRACT_AUTH_PATH,
    SAVE_DYNAMIC_MODE_PATH,
    SAVE_LOCATION_ID_PATH,
    SAVE_SINGLE_MODE_PATH,
    SAVE_TOU_PLAN_PATH,
)
from .._http import BaseHTTPMixin, JackeryApiError, _write_accepted


class EnergyPriceEndpointMixin(BaseHTTPMixin):
    """Energy price, tariff, and dynamic-pricing endpoint methods."""

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
        """
        data = await self._get_json(
            PRICE_HISTORY_CONFIG_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        self.last_price_history_config_response = data
        return self._payload_dict(data, PRICE_HISTORY_CONFIG_PATH)

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
        """
        try:
            price = float(single_price)
        except ValueError as err:
            raise JackeryApiError(  # noqa: TRY003
                "single_price must be a valid number"
            ) from err
        except TypeError as err:
            raise JackeryApiError(  # noqa: TRY003
                "single_price must be a valid number"
            ) from err
        if not (price >= 0):
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
        return _write_accepted(data)

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
        try:
            company_id_float = float(platform_company_id)
        except (TypeError, ValueError) as err:
            raise JackeryApiError(  # noqa: TRY003
                "platform_company_id must be an integer"
            ) from err
        if not company_id_float.is_integer():
            raise JackeryApiError("platform_company_id must be an integer")  # noqa: TRY003
        company_id = int(company_id_float)
        region = str(system_region or "").strip()
        if not region:
            raise JackeryApiError("system_region must be a non-empty string")  # noqa: TRY003
        data = await self._post_form(
            SAVE_DYNAMIC_MODE_PATH,
            {
                FIELD_SYSTEM_ID: str(system_id),
                FIELD_PLATFORM_COMPANY_ID: company_id,
                FIELD_SYSTEM_REGION: region,
            },
        )
        return _write_accepted(data)

    # --- New energy price endpoints -----------------------------------------

    async def async_get_dynamic_price_login_url(
        self, *, platform_company_id: int, system_id: str | int
    ) -> dict[str, Any]:
        """Get login URL for dynamic price platform."""
        data = await self._get_json(
            DYNAMIC_PRICE_LOGIN_URL_PATH,
            {"platformCompanyId": platform_company_id, "systemId": str(system_id)},
        )
        return self._payload_dict(data, DYNAMIC_PRICE_LOGIN_URL_PATH)

    async def async_get_device_currency(self, device_id: str | int) -> dict[str, Any]:
        """Get the currency configuration for a device."""
        data = await self._get_json(
            DEVICE_CURRENCY_PATH, params={FIELD_DEVICE_ID: str(device_id)}
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
        """Save contract authorization for dynamic pricing."""
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
        self, *, customer_number: str, platform_company_id: int
    ) -> list[dict[str, Any]]:
        """List available contracts for dynamic pricing."""
        data = await self._get_json(
            CONTRACT_LIST_PATH,
            {
                "customerNumber": customer_number,
                "platformCompanyId": platform_company_id,
            },
        )
        return self._payload_list(data, CONTRACT_LIST_PATH)

    async def async_cancel_contract_auth(
        self, *, platform_company_id: int, system_id: str | int
    ) -> dict[str, Any]:
        """Cancel contract authorization."""
        return await self._post_json(
            CANCEL_CONTRACT_PATH,
            {
                "platformCompanyId": platform_company_id,
                "systemId": str(system_id),
            },
        )

    async def async_get_dynamic_price(self, system_id: str | int) -> dict[str, Any]:
        """Get dynamic price configuration."""
        data = await self._get_json(
            DYNAMIC_PRICE_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        return self._payload_dict(data, DYNAMIC_PRICE_PATH)

    async def async_save_location_id(self, *, connect_token: str) -> dict[str, Any]:
        """Save Flatpeak location ID."""
        return await self._post_json(
            SAVE_LOCATION_ID_PATH, {"connectToken": connect_token}
        )

    async def async_save_tou_plan(
        self, *, device_id: str | int, tasks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Save a TOU (Time-of-Use) schedule plan."""
        return await self._post_json(
            SAVE_TOU_PLAN_PATH,
            {"deviceId": str(device_id), "tasks": tasks},
        )

    async def async_query_tou_plan(self, *, device_id: str | int) -> dict[str, Any]:
        """Query the current TOU (Time-of-Use) schedule plan."""
        data = await self._get_json(
            QUERY_TOU_PLAN_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        return self._payload_dict(data, QUERY_TOU_PLAN_PATH)

    async def async_get_currency_list(self) -> list[dict[str, Any]]:
        """List available currencies."""
        data = await self._get_json(CURRENCY_LIST_PATH)
        return self._payload_list(data, CURRENCY_LIST_PATH)

    async def async_bind_currency(
        self, *, currency: str, device_id: str | int, system_id: str | int
    ) -> dict[str, Any]:
        """Bind a currency to a device/system."""
        return await self._post_json(
            BIND_CURRENCY_PATH,
            {
                "currency": currency,
                "deviceId": str(device_id),
                "systemId": str(system_id),
            },
        )
