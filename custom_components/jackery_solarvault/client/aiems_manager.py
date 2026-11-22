"""AI EMS, smart mode, TOU, and external API manager mixin.

Provides auxiliary coordinator capabilities for JackerySolarVaultCoordinator.
"""

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.update_coordinator import UpdateFailed

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .api import JackeryApi


class JackeryAiemsManagerMixin:  # ruff: ignore[too-many-public-methods]
    """Mixin providing AI EMS, smart mode, and auxiliary operations."""

    if TYPE_CHECKING:
        api: JackeryApi
        data: dict[str, dict[str, Any]]

        async def async_request_refresh(self) -> None:
            """Request a coordinator refresh."""
            raise NotImplementedError

        def _require_home_config_context(self, device_id: str, action: str) -> None:
            raise NotImplementedError

        def _resolve_system_id(self, device_id: str) -> str | None:
            raise NotImplementedError

        @staticmethod
        def _shadow_system_id(entry: dict[str, Any]) -> str | None:
            raise NotImplementedError

        @classmethod
        def _shadow_has_home_config_context(cls, entry: dict[str, Any]) -> bool:
            raise NotImplementedError

    async def async_check_smart_mode(
        self,
        device_id: str,
        system_id: str,
    ) -> dict[str, Any]:
        """Check if smart mode is configured for a device/system.

        Calls ``/v1/device/smartMode/checkIfSet`` (POST).
        Returns ``SmartConditionData``.
        """
        self._require_home_config_context(device_id, "check smart mode")
        return await self.api.async_check_smart_mode_set(
            device_id=device_id,
            system_id=system_id,
        )

    async def async_get_smart_mode_info(self, system_id: str) -> dict[str, Any]:
        """Get smart mode configuration for a system.

        Calls ``/v1/device/smartMode/getSmartMode`` (GET).
        Returns ``SmartModeInfoData``.
        """
        return await self.api.async_get_smart_mode_info(system_id)

    async def async_get_smart_schedule_prediction(
        self,
        device_id: str,
    ) -> dict[str, Any]:
        """Get AI smart-schedule prediction for a configured device.

        Calls ``/v1/device/stat/getSmartSchedulePrediction`` (GET) using the
        systemId resolved from the coordinator payload.
        """
        payload = (self.data or {}).get(device_id) or {}
        if not self._shadow_has_home_config_context(payload):
            msg = f"smart schedule is not a Home/System feature for {device_id}"
            raise LookupError(msg)
        system_id = self._shadow_system_id(payload)
        if system_id is None:
            msg = f"missing systemId for {device_id}"
            raise LookupError(msg)
        return await self.api.async_get_smart_schedule_prediction(
            system_id=system_id,
        )

    async def async_start_smart_mode(self, system_id: str) -> None:
        """Start or enable smart mode for a system.

        Calls ``/v1/device/smartMode/startSmartMode`` (POST).
        """
        await self.api.async_start_smart_mode(system_id)

    # ------------------------------------------------------------------
    # TOU (Time-of-Use) Plan
    # ------------------------------------------------------------------

    async def async_query_tou_plan(self, device_id: str) -> dict[str, Any]:
        """Query the current TOU schedule plan for a device.

        Calls ``/v1/device/tou/queryTouPlan`` (GET).
        """
        return await self.api.async_query_tou_plan(device_id=device_id)

    async def async_save_tou_plan(
        self,
        device_id: str,
        tasks: list[dict[str, Any]],
    ) -> None:
        """Save a TOU schedule plan for a device.

        Calls ``/v1/device/tou/saveTouPlan`` (POST).
        """
        await self.api.async_save_tou_plan(device_id=device_id, tasks=tasks)
        await self.async_request_refresh()

    async def async_list_currencies(self) -> list[dict[str, Any]]:
        """List currencies supported by the Jackery HTTP API."""
        return await self.api.async_get_currency_list()

    async def async_get_device_currency(self, device_id: str) -> dict[str, Any]:
        """Get the currency payload for a configured device."""
        self._require_home_config_context(device_id, "get device currency")
        return await self.api.async_get_device_currency(device_id)

    async def async_bind_currency(self, device_id: str, currency: str) -> None:
        """Bind a currency to a configured device and refresh."""
        self._require_home_config_context(device_id, "bind device currency")
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = f"Cannot bind currency for {device_id}: missing systemId"
            raise UpdateFailed(msg)
        await self.api.async_bind_currency(
            currency=currency,
            device_id=device_id,
            system_id=system_id,
        )
        await self.async_request_refresh()

    async def async_list_accessories(self, device_id: str) -> list[dict[str, Any]]:
        """List smart accessories for a configured parent device."""
        self._require_home_config_context(device_id, "list accessories")
        return await self.api.async_get_accessories_list(device_id)

    async def async_check_accessories_exist(
        self,
        *,
        devices: str,
        context_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Check whether account accessories exist for a device list payload."""
        if context_device_id is not None:
            self._require_home_config_context(context_device_id, "check accessories")
        return await self.api.async_check_accessories_exist(devices=devices)

    async def async_check_jackery_accessories_exist(
        self,
        *,
        device_sn_infos: str,
        context_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Check Jackery accessory existence for serialized device SN info."""
        if context_device_id is not None:
            self._require_home_config_context(
                context_device_id, "check Jackery accessories"
            )
        return await self.api.async_check_jackery_accessories_exist(
            device_sn_infos=device_sn_infos,
        )

    async def async_set_accessory_name(
        self,
        *,
        accessory_id: str,
        nickname: str,
        context_device_id: str | None = None,
    ) -> None:
        """Rename a smart accessory through the Jackery HTTP API and refresh."""
        if context_device_id is not None:
            self._require_home_config_context(context_device_id, "rename accessory")
        await self.api.async_set_accessories_name(
            device_name=nickname,
            id=accessory_id,
        )
        await self.async_request_refresh()

    async def async_get_dynamic_price_login_url(
        self,
        device_id: str,
        platform_company_id: int,
    ) -> dict[str, Any]:
        """Return a dynamic-price provider login URL for a configured device."""
        self._require_home_config_context(device_id, "get dynamic price login URL")
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = (
                f"Cannot get dynamic price login URL for {device_id}: missing systemId"
            )
            raise UpdateFailed(msg)
        return await self.api.async_get_dynamic_price_login_url(
            platform_company_id=platform_company_id,
            system_id=system_id,
        )

    async def async_list_dynamic_price_contracts(
        self,
        *,
        customer_number: str,
        platform_company_id: int,
    ) -> list[dict[str, Any]]:
        """List dynamic-price contracts for a customer and platform."""
        return await self.api.async_get_contract_list(
            customer_number=customer_number,
            platform_company_id=platform_company_id,
        )

    async def async_save_dynamic_price_contract_auth(
        self,
        device_id: str,
        *,
        contract_id: str,
        custom_id: str,
        platform_company_id: int,
    ) -> dict[str, Any]:
        """Save dynamic-price contract authorization and refresh."""
        self._require_home_config_context(device_id, "save dynamic price contract auth")
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = (
                f"Cannot save dynamic price contract auth for {device_id}: "
                "missing systemId"
            )
            raise UpdateFailed(msg)
        result = await self.api.async_save_contract_auth(
            contract_id=contract_id,
            custom_id=custom_id,
            platform_company_id=platform_company_id,
            system_id=system_id,
        )
        await self.async_request_refresh()
        return result

    async def async_cancel_dynamic_price_contract_auth(
        self,
        device_id: str,
        platform_company_id: int,
    ) -> dict[str, Any]:
        """Cancel dynamic-price contract authorization and refresh."""
        self._require_home_config_context(
            device_id, "cancel dynamic price contract auth"
        )
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = (
                f"Cannot cancel dynamic price contract auth for {device_id}: "
                "missing systemId"
            )
            raise UpdateFailed(msg)
        result = await self.api.async_cancel_contract_auth(
            platform_company_id=platform_company_id,
            system_id=system_id,
        )
        await self.async_request_refresh()
        return result

    async def async_save_dynamic_price_location_id(
        self,
        *,
        connect_token: str,
    ) -> dict[str, Any]:
        """Save a Flatpeak dynamic-price location token and refresh."""
        result = await self.api.async_save_location_id(connect_token=connect_token)
        await self.async_request_refresh()
        return result

    async def async_query_socket_stat(
        self,
        target_device_id: str,
        *,
        date_type: str,
        begin_date: str,
        end_date: str,
        context_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Query app socket chart statistics for an accessory device id."""
        if context_device_id is not None:
            self._require_home_config_context(
                context_device_id, "query socket statistics"
            )
        return await self.api.async_get_device_socket_stat(
            target_device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_list_country_zones(
        self,
    ) -> list[dict[str, Any]]:
        """List country/zone entries used by DIY device setup."""
        return await self.api.async_get_zone_list()

    async def async_list_grid_standards(
        self,
        *,
        country: str,
    ) -> list[dict[str, Any]]:
        """List grid-connection standards for a country code."""
        return await self.api.async_get_gcs_list(country=country)

    async def async_check_system_bound(
        self,
        *,
        bind_key: str,
        device_sn: str,
        guid: str,
    ) -> dict[str, Any]:
        """Check whether a system is already bound for bind-key/SN/GUID."""
        return await self.api.async_check_system_bound(
            bind_key=bind_key,
            device_sn=device_sn,
            guid=guid,
        )

    async def async_get_device_bluetooth_key_payload(
        self,
        *,
        device_sn: str,
        guid: str,
    ) -> dict[str, Any]:
        """Fetch the HTTP-owned Bluetooth-key payload for pairing/bootstrap code.

        The returned payload is sensitive and intentionally not exposed as a HA
        service response. Layer-5 transports only consume cached/known keys; they
        must not call this themselves.
        """
        return await self.api.async_get_device_bluetooth_key(
            device_sn=device_sn,
            guid=guid,
        )

    async def async_get_alarm_detail(
        self,
        alarm_key: str,
        *,
        context_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Return detailed alarm information for an alarm key."""
        if context_device_id is not None:
            self._require_home_config_context(context_device_id, "get alarm detail")
        return await self.api.async_get_alarm_detail(alarm_key=alarm_key)

    async def async_list_notifications(
        self,
        *,
        current_time: int,
        device_sn: str,
        page_no: int,
        page_size: int,
    ) -> list[dict[str, Any]]:
        """List Jackery push notifications for the selected account."""
        return await self.api.async_get_notify_list(
            current_time=current_time,
            device_sn=device_sn,
            page_no=page_no,
            page_size=page_size,
        )

    async def async_get_unread_count(self) -> dict[str, Any]:
        """Return unread notification counts for the selected account."""
        return await self.api.async_get_unread_count()

    async def async_get_push_config(self) -> dict[str, Any]:
        """Return push-notification configuration for the selected account."""
        return await self.api.async_get_push_config()

    async def async_set_push_config(self, set_value: int) -> dict[str, Any]:
        """Set push-notification configuration for the selected account."""
        return await self.api.async_set_push_config(set=set_value)

    async def async_sync_alerts(
        self,
        *,
        content: str,
        id: str,
        context_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Sync faults and alarms through the Jackery HTTP API."""
        if context_device_id is not None:
            self._require_home_config_context(context_device_id, "sync alerts")
        result = await self.api.async_sync_alerts(content=content, id=id)
        await self.async_request_refresh()
        return result

    async def async_sync_offline_statistics(
        self,
        body: Mapping[str, Any],
        *,
        context_device_id: str,
    ) -> bool:
        """Upload the supplied original device body using its owning account."""
        self._require_home_config_context(context_device_id, "sync offline statistics")
        return await self.api.async_sync_offline_statistics(body)

    async def async_query_charge_report(
        self,
        *,
        device_sn: str,
        page_index: int,
    ) -> dict[str, Any]:
        """Return charge-report history for a device serial number."""
        return await self.api.async_get_charge_report(
            device_sn=device_sn,
            page_index=page_index,
        )

    async def async_query_cutoff_stat(
        self,
        *,
        device_sn: str,
        begin_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        """Return cutoff/power-outage statistics for a device serial number."""
        return await self.api.async_get_cutoff_stat(
            device_sn=device_sn,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_query_soc_stat(self, device_id: str) -> dict[str, Any]:
        """Return SOC statistics for a configured device id."""
        return await self.api.async_get_soc_stat(device_id=device_id)

    async def async_query_carbon_stat(self, device_sn: str) -> dict[str, Any]:
        """Return carbon-offset statistics for a device serial number."""
        return await self.api.async_get_carbon_stat(device_sn=device_sn)

    async def async_query_profit_stat(self, device_id: str) -> dict[str, Any]:
        """Return profit/revenue statistics for a configured device id."""
        return await self.api.async_get_profit_stat(device_id=device_id)

    async def async_query_box_stat(
        self,
        *,
        device_sn: str,
        date_type: str,
        begin_date: str,
        end_date: str,
        key: str,
    ) -> dict[str, Any]:
        """Return generic box electricity statistics for a device serial number."""
        return await self.api.async_get_box_stat(
            device_sn=device_sn,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
            key=key,
        )

    async def async_check_app_version(
        self,
        *,
        type: str,
        version_name: str,
    ) -> dict[str, Any]:
        """Return Jackery app-version metadata for the selected account."""
        return await self.api.async_check_app_version(
            type=type,
            version_name=version_name,
        )

    async def async_list_banners(self) -> list[dict[str, Any]]:
        """Return Jackery app banner entries for the selected account."""
        return await self.api.async_get_banner_list()

    async def async_submit_feedback(
        self,
        *,
        contact_info: str,
        content: str,
        device_sn: str,
    ) -> dict[str, Any]:
        """Submit user feedback through the Jackery HTTP API."""
        return await self.api.async_submit_feedback(
            contact_info=contact_info,
            content=content,
            device_sn=device_sn,
        )

    async def async_list_faqs(self) -> list[dict[str, Any]]:
        """Return Jackery FAQ entries for the selected account."""
        return await self.api.async_get_faq_list()

    async def async_list_faq_answers(self) -> list[dict[str, Any]]:
        """Return Jackery FAQ answer entries for the selected account."""
        return await self.api.async_get_faq_answer()

    async def async_check_privacy_update(self) -> dict[str, Any]:
        """Return whether updated privacy consent is required."""
        return await self.api.async_check_privacy_update()

    async def async_agree_privacy_consent(
        self,
        *,
        pending_agree_version_ids: list[int],
    ) -> dict[str, Any]:
        """Record privacy consent agreement through the Jackery HTTP API."""
        return await self.api.async_agree_privacy_consent(
            pending_agree_version_ids=pending_agree_version_ids,
        )

    async def async_get_product_instruction(
        self,
        *,
        device_sn: str,
        type: str,
    ) -> dict[str, Any]:
        """Return product-instruction payload for a device serial number."""
        return await self.api.async_get_product_instruction(
            dev_sn=device_sn,
            type=type,
        )

    async def async_get_user_info(self) -> dict[str, Any]:
        """Return authenticated-user profile information for the selected account."""
        return await self.api.async_get_user_info()

    async def async_update_user_info(self, nick_name: str) -> None:
        """Update the account display nickname via the primary HTTP API.

        The nickname is account-scoped and never ingested into coordinator data,
        so there is no local patch to apply.
        """
        await self.api.async_update_user_info(nick_name=nick_name)
