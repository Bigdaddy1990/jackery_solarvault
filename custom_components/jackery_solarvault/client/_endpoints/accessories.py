"""Accessories, sub-device, and smart accessories endpoints."""

from typing import Any

from jackery_solarvault.client._http import BaseHTTPMixin
from jackery_solarvault.const import (
    ACCESSORIES_EXIST_PATH,
    ACCESSORIES_JACKERY_EXIST_PATH,
    ACCESSORIES_LIST_PATH,
    ACCESSORIES_NAME_PATH,
    ACCESSORIES_PATH,
    ACCESSORIES_SYNC_PATH,
    FIELD_DEVICE_ID,
    SUB_SHADOW_PATH,
    SYSTEM_SHADOW_PATH,
)


class AccessoriesEndpointMixin(BaseHTTPMixin):
    """Accessories, sub-device, and smart accessories endpoint methods."""

    async def async_get_accessories(
        self, *, devices: str, id: str | int, parent_device_id: str | int
    ) -> dict[str, Any]:
        """
        Fetch accessories data for the specified device(s).
        
        Parameters:
            devices: Comma-separated device identifiers to include in the query.
            id: Identifier sent as the request `id` parameter.
            parent_device_id: Identifier sent as the request `parentDeviceId` parameter.
        
        Returns:
            dict: The accessories payload returned by the backend.
        """
        data = await self._get_json(
            ACCESSORIES_PATH,
            {
                "devices": devices,
                "id": str(id),
                "parentDeviceId": str(parent_device_id),
            },
        )
        return self._payload_dict(data, ACCESSORIES_PATH)

    async def async_check_accessories_exist(self, *, devices: str) -> dict[str, Any]:
        """
        Determine accessory existence for the specified device IDs.
        
        Parameters:
            devices (str): Comma-separated device IDs to check.
        
        Returns:
            dict[str, Any]: Mapping of each device ID to the existence information returned by the backend.
        """
        data = await self._get_json(ACCESSORIES_EXIST_PATH, params={"devices": devices})
        return self._payload_dict(data, ACCESSORIES_EXIST_PATH)

    async def async_get_accessories_list(
        self, device_id: str | int
    ) -> list[dict[str, Any]]:
        """
        List accessories for a device.
        
        Parameters:
            device_id (str | int): Identifier of the device whose accessories will be listed.
        
        Returns:
            list[dict[str, Any]]: List of accessory entries as dictionaries from the API response.
        """
        data = await self._get_json(
            ACCESSORIES_LIST_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        return self._payload_list(data, ACCESSORIES_LIST_PATH)

    async def async_set_accessories_name(
        self, *, device_name: str, id: str | int
    ) -> dict[str, Any]:
        """
        Set the display name for an accessory.
        
        Parameters:
            device_name (str): New accessory name.
            id (str | int): Accessory identifier; will be sent as a string.
        
        Returns:
            dict: JSON response from the backend.
        """
        return await self._post_json(
            ACCESSORIES_NAME_PATH,
            {"deviceName": device_name, "id": str(id)},
        )

    async def async_check_jackery_accessories_exist(
        self, *, device_sn_infos: str
    ) -> dict[str, Any]:
        """
        Determine whether Jackery accessories exist for the provided device serial numbers.
        
        Parameters:
            device_sn_infos (str): Device serial number info string as accepted by the API.
        
        Returns:
            dict: The API response payload for the existence check.
        """
        data = await self._get_json(
            ACCESSORIES_JACKERY_EXIST_PATH,
            params={"deviceSnInfos": device_sn_infos},
        )
        return self._payload_dict(data, ACCESSORIES_JACKERY_EXIST_PATH)

    async def async_sync_smart_accessories(self) -> dict[str, Any]:
        """Synchronize smart accessories data.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(ACCESSORIES_SYNC_PATH, {})

    async def async_get_sub_shadow(
        self, *, dev_type: str, device_sn: str, sub_device_sn: str
    ) -> dict[str, Any]:
        """
        Retrieve the property shadow for a sub-device.
        
        Parameters:
            dev_type (str): Device type identifier.
            device_sn (str): Parent device serial number.
            sub_device_sn (str): Sub-device serial number.
        
        Returns:
            dict[str, Any]: Shadow payload for the specified sub-device.
        """
        data = await self._get_json(
            SUB_SHADOW_PATH,
            {
                "devType": dev_type,
                "deviceSn": device_sn,
                "subDeviceSn": sub_device_sn,
            },
        )
        return self._payload_dict(data, SUB_SHADOW_PATH)

    async def async_get_system_shadow(
        self, *, device_sn: str, diy_sn: str
    ) -> dict[str, Any]:
        """
        Retrieve the system property shadow for a device.
        
        Parameters:
            device_sn (str): Device serial number.
            diy_sn (str): DIY device serial number.
        
        Returns:
            dict[str, Any]: System shadow data.
        """
        data = await self._get_json(
            SYSTEM_SHADOW_PATH,
            {"deviceSn": device_sn, "diySn": diy_sn},
        )
        return self._payload_dict(data, SYSTEM_SHADOW_PATH)
