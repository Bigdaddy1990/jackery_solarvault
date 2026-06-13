"""Push notification and configuration endpoints."""

from typing import Any

from jackery_solarvault.client._http import BaseHTTPMixin
from jackery_solarvault.const import (
    NOTIFY_LIST_PATH,
    PUSH_CONFIG_GET_PATH,
    PUSH_CONFIG_SET_PATH,
    UNREAD_COUNT_PATH,
)


class PushEndpointMixin(BaseHTTPMixin):
    """Push notification and configuration endpoint methods."""

    async def async_get_notify_list(
        self,
        *,
        current_time: int = 0,
        device_sn: str = "",
        page_no: int = 1,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Retrieve a paginated list of push notifications.
        
        Parameters:
            current_time (int): Current timestamp in milliseconds (Unix ms) used for server-side filtering.
            device_sn (str): Device serial number to filter notifications; empty string for no filtering.
            page_no (int): 1-based page number to retrieve.
            page_size (int): Number of items per page.
        
        Returns:
            list[dict[str, Any]]: List of notification entries represented as dictionaries.
        """
        params: dict[str, Any] = {
            "currentTime": current_time,
            "deviceSn": device_sn,
            "pageNo": page_no,
            "pageSize": page_size,
        }
        data = await self._get_json(NOTIFY_LIST_PATH, params=params)
        return self._payload_list(data, NOTIFY_LIST_PATH)

    async def async_get_unread_count(self) -> dict[str, Any]:
        """
        Retrieve unread notification counts.
        
        Returns:
            dict[str, Any]: Mapping of unread count fields from the response (for example, total unread count and related metadata).
        """
        data = await self._get_json(UNREAD_COUNT_PATH)
        return self._payload_dict(data, UNREAD_COUNT_PATH)

    async def async_set_push_config(self, *, set: str) -> dict[str, Any]:
        """
        Set the device's push configuration on the server.
        
        Parameters:
            set (str): Configuration payload string to apply.
        
        Returns:
            dict[str, Any]: Response data returned by the backend.
        """
        return await self._post_json(PUSH_CONFIG_SET_PATH, {"set": set})

    async def async_get_push_config(self) -> dict[str, Any]:
        """Get push notification configuration.

        Returns:
            dict: Push configuration data.
        """
        data = await self._get_json(PUSH_CONFIG_GET_PATH)
        return self._payload_dict(data, PUSH_CONFIG_GET_PATH)
