"""Push notification and configuration endpoints."""

from typing import Any

from ...const import (
    NOTIFY_LIST_PATH,
    PUSH_CONFIG_GET_PATH,
    PUSH_CONFIG_SET_PATH,
    UNREAD_COUNT_PATH,
)
from .._http import BaseHTTPMixin


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
        """List push notifications.

        Parameters:
            current_time: Current timestamp (Unix ms).
            device_sn: Device serial number filter.
            page_no: Page number (1-based).
            page_size: Items per page.

        Returns:
            list: Notification entries.
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
        """Get unread notification count.

        Returns:
            dict: Unread count data.
        """
        data = await self._get_json(UNREAD_COUNT_PATH)
        return self._payload_dict(data, UNREAD_COUNT_PATH)

    async def async_set_push_config(self, *, set: str) -> dict[str, Any]:
        """Set push notification configuration.

        Parameters:
            set: Configuration payload string.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(PUSH_CONFIG_SET_PATH, {"set": set})

    async def async_get_push_config(self) -> dict[str, Any]:
        """Get push notification configuration.

        Returns:
            dict: Push configuration data.
        """
        data = await self._get_json(PUSH_CONFIG_GET_PATH)
        return self._payload_dict(data, PUSH_CONFIG_GET_PATH)
