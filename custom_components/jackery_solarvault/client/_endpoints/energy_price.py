"""Energy price, tariff, and dynamic-pricing endpoint mixins."""

from typing import Any

from jackery_solarvault.client._http import (
    BaseHTTPMixin,
    JackeryApiError,
    _write_accepted,
)
from jackery_solarvault.const import (
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


class EnergyPriceEndpointMixin(BaseHTTPMixin):
    """Energy price, tariff, and dynamic-pricing endpoint methods."""

    async def async_get_power_price(self, system_id: str | int) -> dict:
        """Retrieve the power price (tariff) configuration for a given system.

        Parameters:
            system_id (str | int): Identifier of the system to query.

        Returns:
            dict: The payload dictionary containing the power price / tariff configuration.
        """
        data = await self._get_json(
            POWER_PRICE_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        self.last_price_response = data
        return self._payload_dict(data, POWER_PRICE_PATH)

    async def async_get_price_sources(
        self, system_id: str | int
    ) -> list[dict[str, Any]]:
        """Retrieve available dynamic-price providers for a system.

        Parameters:
            system_id (str | int): System identifier sent as the `systemId` query parameter.

        Returns:
            list[dict[str, Any]]: A list of provider objects. Each object contains `platformCompanyId`, `cid`, `country`, `companyName`, and `loginAllowed`.

        Side effects:
            Stores the raw HTTP response on `self.last_price_sources_response`.
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
            platform_company_id: Platform company identifier required by the API; must be an integer-valued number.
            system_region: Region code for the system; must be a non-empty string.

        Returns:
            `true` if the change was accepted by the server, `false` otherwise.

        Raises:
            JackeryApiError: If `platform_company_id` is not an integer-valued number or if `system_region` is empty.
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
        """Save a contract authorization record for dynamic pricing.

        Parameters:
            contract_id (str): Identifier of the contract to save.
            custom_id (str): Client/custom identifier associated with the contract.
            platform_company_id (int): Platform company identifier used by the dynamic-pricing service.
            system_id (str | int): System identifier; will be converted to a string in the request payload.

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
        self, *, customer_number: str, platform_company_id: int
    ) -> list[dict[str, Any]]:
        """Retrieve the list of available dynamic-pricing contracts for a customer.

        Parameters:
            customer_number (str): Customer number used to query contracts.
            platform_company_id (int): Platform company identifier that scopes the contract list.

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
        self, *, platform_company_id: int, system_id: str | int
    ) -> dict[str, Any]:
        """Cancel an existing contract authorization for the given platform company and system.

        Parameters:
            platform_company_id (int): Identifier of the platform company owning the contract.
            system_id (str | int): Identifier of the target system; will be sent as a string.

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
            dict: The dynamic pricing configuration payload extracted from the service response.
        """
        data = await self._get_json(
            DYNAMIC_PRICE_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        return self._payload_dict(data, DYNAMIC_PRICE_PATH)

    async def async_save_location_id(self, *, connect_token: str) -> dict[str, Any]:
        """Save Flatpeak location ID using the provided connect token.

        Parameters:
            connect_token (str): Flatpeak connect token used to save/associate the location ID.

        Returns:
            dict[str, Any]: Parsed JSON response from the API.
        """
        return await self._post_json(
            SAVE_LOCATION_ID_PATH, {"connectToken": connect_token}
        )

    async def async_save_tou_plan(
        self, *, device_id: str | int, tasks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Save a Time-of-Use (TOU) schedule for a device.

        Parameters:
            device_id (str | int): Device identifier to which the TOU plan will be applied.
            tasks (list[dict[str, Any]]): List of TOU task objects formatted for the API.

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
            QUERY_TOU_PLAN_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        return self._payload_dict(data, QUERY_TOU_PLAN_PATH)

    async def async_get_currency_list(self) -> list[dict[str, Any]]:
        """Retrieve the list of available currencies.

        Returns:
            list[dict[str, Any]]: The list of currency records extracted from the API payload.
        """
        data = await self._get_json(CURRENCY_LIST_PATH)
        return self._payload_list(data, CURRENCY_LIST_PATH)

    async def async_bind_currency(
        self, *, currency: str, device_id: str | int, system_id: str | int
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
