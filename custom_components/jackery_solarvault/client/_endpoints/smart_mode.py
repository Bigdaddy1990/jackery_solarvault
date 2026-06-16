"""Smart mode and AI scheduling endpoints."""

from typing import Any

from custom_components.jackery_solarvault.client._http import BaseHTTPMixin
from custom_components.jackery_solarvault.const import (
    FIELD_DEVICE_ID,
    FIELD_SYSTEM_ID,
    SMART_MODE_CHECK_PATH,
    SMART_MODE_INFO_PATH,
    SMART_MODE_START_PATH,
)


class SmartModeEndpointMixin(BaseHTTPMixin):
    """Smart mode and AI scheduling endpoint methods."""

    async def async_check_smart_mode_set(
        self,
        *,
        device_id: str | int,
        system_id: str | int,
    ) -> dict[str, Any]:
        """Determine whether smart mode is configured for the given device and system.

        Parameters:
            device_id (str | int): Device identifier.
            system_id (str | int): System identifier.

        Returns:
            dict[str, Any]: Smart mode check result as a dictionary.
        """
        data = await self._post_json(
            SMART_MODE_CHECK_PATH,
            {FIELD_DEVICE_ID: str(device_id), FIELD_SYSTEM_ID: str(system_id)},
        )
        return self._payload_dict(data, SMART_MODE_CHECK_PATH)

    async def async_get_smart_mode_info(self, system_id: str | int) -> dict[str, Any]:
        """Retrieve smart mode configuration for the specified system.

        Parameters:
            system_id (str | int): Identifier of the system to fetch configuration for.

        Returns:
            dict[str, Any]: Dictionary containing the smart mode configuration for the
            system.
        """
        data = await self._get_json(
            SMART_MODE_INFO_PATH,
            params={FIELD_SYSTEM_ID: str(system_id)},
        )
        return self._payload_dict(data, SMART_MODE_INFO_PATH)

    async def async_start_smart_mode(self, system_id: str | int) -> dict[str, Any]:
        """Enable smart mode for the specified system.

        Parameters:
            system_id (str | int): Identifier of the target system.

        Returns:
            dict[str, Any]: Backend response data.
        """
        return await self._post_json(
            SMART_MODE_START_PATH,
            {FIELD_SYSTEM_ID: str(system_id)},
        )
