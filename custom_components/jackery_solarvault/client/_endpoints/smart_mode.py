"""Smart mode and AI scheduling endpoints."""

from typing import Any

from jackery_solarvault.client._http import BaseHTTPMixin
from jackery_solarvault.const import (
    FIELD_DEVICE_ID,
    FIELD_SYSTEM_ID,
    SMART_MODE_CHECK_PATH,
    SMART_MODE_INFO_PATH,
    SMART_MODE_START_PATH,
)


class SmartModeEndpointMixin(BaseHTTPMixin):
    """Smart mode and AI scheduling endpoint methods."""

    async def async_check_smart_mode_set(
        self, *, device_id: str | int, system_id: str | int
    ) -> dict[str, Any]:
        """Check if smart mode is configured for a device/system.

        Parameters:
            device_id: Device identifier.
            system_id: System identifier.

        Returns:
            dict: Smart mode check result.
        """
        data = await self._get_json(
            SMART_MODE_CHECK_PATH,
            {FIELD_DEVICE_ID: str(device_id), FIELD_SYSTEM_ID: str(system_id)},
        )
        return self._payload_dict(data, SMART_MODE_CHECK_PATH)

    async def async_get_smart_mode_info(self, system_id: str | int) -> dict[str, Any]:
        """Get smart mode configuration.

        Parameters:
            system_id: System identifier.

        Returns:
            dict: Smart mode configuration.
        """
        data = await self._get_json(
            SMART_MODE_INFO_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        return self._payload_dict(data, SMART_MODE_INFO_PATH)

    async def async_start_smart_mode(self, system_id: str | int) -> dict[str, Any]:
        """Start or enable smart mode.

        Parameters:
            system_id: System identifier.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            SMART_MODE_START_PATH, {FIELD_SYSTEM_ID: str(system_id)}
        )
