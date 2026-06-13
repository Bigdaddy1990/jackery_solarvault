"""Accessories, sub-device, and smart accessories endpoints."""

from typing import Any

from ...const import (
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
from .._http import BaseHTTPMixin


class AccessoriesEndpointMixin(BaseHTTPMixin):
    """Accessories, sub-device, and smart accessories endpoint methods."""

    async def async_get_accessories(
        self, *, devices: str, id: str | int, parent_device_id: str | int
    ) -> dict[str, Any]:
        """Get accessories for a device.

        Parameters:
            devices: Comma-separated device IDs.
            id: Parent device ID.
            parent_device_id: Parent device identifier.

        Returns:
            dict: Accessories data.
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
        """Check if accessories exist for the given devices.

        Parameters:
            devices: Comma-separated device IDs.

        Returns:
            dict: Existence check result.
        """
        data = await self._get_json(ACCESSORIES_EXIST_PATH, params={"devices": devices})
        return self._payload_dict(data, ACCESSORIES_EXIST_PATH)

    async def async_get_accessories_list(
        self, device_id: str | int
    ) -> list[dict[str, Any]]:
        """List accessories for a device.

        Parameters:
            device_id: Device identifier.

        Returns:
            list: Accessory entries.
        """
        data = await self._get_json(
            ACCESSORIES_LIST_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        return self._payload_list(data, ACCESSORIES_LIST_PATH)

    async def async_set_accessories_name(
        self, *, device_name: str, id: str | int
    ) -> dict[str, Any]:
        """Set the name of an accessory.

        Parameters:
            device_name: New accessory name.
            id: Accessory identifier.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            ACCESSORIES_NAME_PATH,
            {"deviceName": device_name, "id": str(id)},
        )

    async def async_check_jackery_accessories_exist(
        self, *, device_sn_infos: str
    ) -> dict[str, Any]:
        """Check if Jackery accessories exist for the given device SNs.

        Parameters:
            device_sn_infos: Device serial number info string.

        Returns:
            dict: Existence check result.
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
        """Get sub-device property shadow.

        Parameters:
            dev_type: Device type identifier.
            device_sn: Parent device serial number.
            sub_device_sn: Sub-device serial number.

        Returns:
            dict: Sub-device shadow data.
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
        """Get system property shadow.

        Parameters:
            device_sn: Device serial number.
            diy_sn: DIY device serial number.

        Returns:
            dict: System shadow data.
        """
        data = await self._get_json(
            SYSTEM_SHADOW_PATH,
            {"deviceSn": device_sn, "diySn": diy_sn},
        )
        return self._payload_dict(data, SYSTEM_SHADOW_PATH)
