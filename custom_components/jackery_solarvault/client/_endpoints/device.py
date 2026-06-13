"""Device, system, battery, OTA, and location endpoints."""

import logging
from typing import Any

from jackery_solarvault.client._http import (
    BaseHTTPMixin,
    JackeryApiError,
    JackeryAuthError,
    JackeryError,
)
from jackery_solarvault.const import (
    APP_REQUEST_META,
    BATTERY_PACK_PATH,
    BLE_OTA_LINK_PATH,
    BLE_OTA_VERSIONS_PATH,
    CHARGE_REPORT_PATH,
    DEVICE_ACCEPT_BIND_PATH,
    DEVICE_BIND_PATH,
    DEVICE_LIST_PATH,
    DEVICE_NICKNAME_PATH,
    DEVICE_PROPERTY_PATH,
    DEVICE_SHARED_LIST_PATH,
    DEVICE_SHARED_MANAGER_PATH,
    DEVICE_SHARED_REMOVE_ALL_PATH,
    DEVICE_SHARED_REMOVE_PATH,
    DEVICE_UNBIND_PATH,
    FIELD_BATTERY_PACKS,
    FIELD_BAT_SOC,
    FIELD_BODY,
    FIELD_CELL_TEMP,
    FIELD_CURRENT_VERSION,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEVICE_SN_LIST,
    FIELD_ID,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IS_FIRMWARE_UPGRADE,
    FIELD_OP,
    FIELD_OUT_PW,
    FIELD_RB,
    FIELD_SYSTEM_NAME,
    FIELD_TARGET_MODULE_VERSION,
    FIELD_TARGET_VERSION,
    FIELD_UPDATE_CONTENT,
    FIELD_UPDATE_STATUS,
    FIELD_UPGRADE_TYPE,
    FIELD_VERSION,
    LOCATION_PATH,
    OTA_LIST_PATH,
    OTA_UPDATE_PATH,
    PV_NAME_PATH,
    SYSTEM_CREATE_PATH,
    SYSTEM_DEVICE_NAME_PATH,
    SYSTEM_EXIST_PATH,
    SYSTEM_LIST_PATH,
    SYSTEM_NAME_PATH,
)

_LOGGER = logging.getLogger(__name__)


class DeviceEndpointMixin(BaseHTTPMixin):
    """Device, system, battery, OTA, and location endpoint methods."""

    async def async_get_system_list(self) -> list[dict[str, Any]]:
        """Fetch the list of systems and their devices from the cloud.

        May update the client's region code by inferring it from the first system with a non-empty countryCode.

        Returns:
            list[dict]: A list of system objects as returned by the backend; each system typically contains fields such as `id`, `systemName`, `deviceName`, `countryCode`, `currency`, `timezone`, and a `devices` list of device objects.
        """
        data = await self._get_json(SYSTEM_LIST_PATH)
        self.last_system_list_response = data
        systems = self._payload_list(data, SYSTEM_LIST_PATH)
        self._maybe_learn_region_code(systems)
        return systems

    async def async_get_device_property(self, device_id: str | int) -> dict:
        """Fetches the device properties for the given device identifier.

        Parameters:
            device_id (str | int): Device identifier; will be stringified for the request.

        Returns:
            dict: The device properties dictionary extracted from the response. Returns an empty dict if the response `data` field is missing or is not a dict.
        """
        data = await self._get_json(
            DEVICE_PROPERTY_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_property_responses[str(device_id)] = data
        return self._payload_dict(data, DEVICE_PROPERTY_PATH)

    async def async_set_system_name(
        self, system_id: str | int, system_name: str
    ) -> bool:
        """Rename the specified system to the given name.

        Parameters:
            system_id (str | int): Identifier of the system to rename.
            system_name (str): New name for the system; must be a non-empty string.

        Returns:
            bool: `true` if the server acknowledged the rename, `false` otherwise.

        Raises:
            JackeryApiError: If `system_name` is empty after trimming or if the API request fails.
        """
        if not system_name or not system_name.strip():
            raise JackeryApiError("system_name must be a non-empty string")  # noqa: TRY003
        data = await self._put_json(
            SYSTEM_NAME_PATH,
            {FIELD_SYSTEM_NAME: system_name.strip(), FIELD_ID: str(system_id)},
        )
        return _write_accepted(data)

    async def async_get_location(self, device_id: str | int) -> dict:
        """Retrieve the GPS coordinates previously set for the specified device.

        Returns:
            dict: The API payload's `data` object containing location fields (e.g., `latitude`, `longitude`); an empty dict if `data` is missing or not a dict.
        """
        data = await self._get_json(
            LOCATION_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_location_responses[str(device_id)] = data
        return self._payload_dict(data, LOCATION_PATH)

    async def async_get_battery_pack_list(self, device_sn: str) -> list[dict[str, Any]]:
        """Get a normalized list of battery pack dictionaries for the given device serial number.

        The raw parsed API response is saved to self.last_battery_pack_responses[device_sn]. Handles multiple backend response shapes and returns an empty list when no pack data is found.

        Parameters:
            device_sn (str): Device serial number to query.

        Returns:
            list[dict]: Battery pack dictionaries extracted from the response; empty list if no packs are found or the response shape is unrecognized.
        """
        params = {FIELD_DEVICE_SN: str(device_sn)}
        data = await self._get_json(BATTERY_PACK_PATH, params=params)
        data.setdefault(
            APP_REQUEST_META, {"path": BATTERY_PACK_PATH, "params": dict(params)}
        )
        self.last_battery_pack_responses[str(device_sn)] = data
        raw = data.get(FIELD_DATA)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            raw_body = raw.get(FIELD_BODY)
            candidates = [
                raw.get(FIELD_BATTERY_PACKS),
                raw_body if isinstance(raw_body, list) else None,
                raw_body.get(FIELD_BATTERY_PACKS)
                if isinstance(raw_body, dict)
                else None,
            ]
            for candidate in candidates:
                if isinstance(candidate, list):
                    return [item for item in candidate if isinstance(item, dict)]
            # Some API variants return a single body object directly.
            if any(
                key in raw
                for key in (
                    FIELD_BAT_SOC,
                    FIELD_CELL_TEMP,
                    FIELD_IN_PW,
                    FIELD_OUT_PW,
                    FIELD_RB,
                    FIELD_IP,
                    FIELD_OP,
                    FIELD_VERSION,
                    FIELD_CURRENT_VERSION,
                    FIELD_IS_FIRMWARE_UPGRADE,
                    FIELD_UPDATE_STATUS,
                )
            ):
                return [raw]
        if raw is not None:
            _LOGGER.warning(
                "Jackery %s returned unexpected data shape for battery packs: %s",
                BATTERY_PACK_PATH,
                type(raw).__name__,
            )
        return []

    async def async_get_ota_info(self, device_sn: str) -> dict:
        """Retrieve OTA information for the device identified by `device_sn`.

        Normalizes multiple backend response shapes and selects the matching OTA item when available.

        Returns:
            dict: OTA information object for the device, or an empty dict if no suitable item is found.
        """
        data = await self._get_json(
            OTA_LIST_PATH, params={FIELD_DEVICE_SN_LIST: device_sn}
        )
        self.last_ota_responses[device_sn] = data
        raw = data.get(FIELD_DATA)
        if isinstance(raw, list):
            items = self._payload_list(data, OTA_LIST_PATH)
            if items:
                return self._select_ota_item(items, device_sn)
        if isinstance(raw, dict):
            raw_body = raw.get(FIELD_BODY)
            if isinstance(raw_body, list):
                body_items = [item for item in raw_body if isinstance(item, dict)]
                selected = self._select_ota_item(body_items, device_sn)
                if selected:
                    return selected
            candidates: list[Any] = [
                raw_body if isinstance(raw_body, dict) else None,
                raw,
            ]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                if any(
                    key in candidate
                    for key in (
                        FIELD_CURRENT_VERSION,
                        FIELD_VERSION,
                        FIELD_TARGET_VERSION,
                        FIELD_TARGET_MODULE_VERSION,
                        FIELD_UPDATE_STATUS,
                        FIELD_UPDATE_CONTENT,
                        FIELD_IS_FIRMWARE_UPGRADE,
                        FIELD_UPGRADE_TYPE,
                    )
                ):
                    return candidate
        items = self._payload_list(data, OTA_LIST_PATH)
        if items:
            return self._select_ota_item(items, device_sn)
        return {}

    async def async_list_devices_legacy(self) -> list[dict[str, Any]]:
        """Fetches the legacy device bind list used by Explorer-series devices.

        Propagates authentication failures so callers can handle re-authentication; for other API errors returns an empty list.

        Returns:
            list[dict[str, Any]]: Device objects parsed from the response, or an empty list if a non-auth `JackeryError` occurred.
        """
        try:
            data = await self._get_json(DEVICE_LIST_PATH)
        except JackeryAuthError:
            raise
        except JackeryError:
            return []
        return self._payload_list(data, DEVICE_LIST_PATH)

    # --- New device management endpoints -------------------------------------

    async def async_bind_device(
        self,
        *,
        bind_key: str,
        dev_id: str,
        guid: str,
        timezone_offset: int = 0,
    ) -> dict[str, Any]:
        """Bind a device to the account.

        Parameters:
            bind_key: Device bind key (from QR code or sticker).
            dev_id: Device identifier.
            guid: Unique device GUID.
            timezone_offset: Timezone offset in seconds.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            DEVICE_BIND_PATH,
            {
                "bindKey": bind_key,
                "devId": dev_id,
                "guid": guid,
                "timezoneOffset": timezone_offset,
            },
        )

    async def async_unbind_device(self, device_id: str | int) -> dict[str, Any]:
        """Unbind a device from the account.

        Parameters:
            device_id: Device identifier to unbind.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(DEVICE_UNBIND_PATH, {"deviceId": str(device_id)})

    async def async_set_device_nickname(
        self, device_id: str | int, nickname: str
    ) -> dict[str, Any]:
        """Set a custom nickname for a device.

        Parameters:
            device_id: Device identifier.
            nickname: Display name for the device.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            DEVICE_NICKNAME_PATH,
            {"deviceId": str(device_id), "nickname": nickname},
        )

    async def async_accept_shared_device(
        self, *, dev_id: str, qr_code_id: str
    ) -> dict[str, Any]:
        """Accept a shared device invitation.

        Parameters:
            dev_id: Device identifier from the sharing invitation.
            qr_code_id: QR code identifier from the invitation.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            DEVICE_ACCEPT_BIND_PATH,
            {"devId": dev_id, "qrCodeId": qr_code_id},
        )

    async def async_get_device_shared_list(self) -> list[dict[str, Any]]:
        """List devices shared with the current account.

        Returns:
            list: Shared device entries.
        """
        data = await self._get_json(DEVICE_SHARED_LIST_PATH)
        return self._payload_list(data, DEVICE_SHARED_LIST_PATH)

    async def async_get_device_shared_managers(
        self, *, bind_user_id: str, level: int = 0
    ) -> list[dict[str, Any]]:
        """List share managers for a binding.

        Parameters:
            bind_user_id: User ID of the binding owner.
            level: Share level filter.

        Returns:
            list: Manager entries.
        """
        data = await self._get_json(
            DEVICE_SHARED_MANAGER_PATH,
            {"bindUserId": bind_user_id, "level": level},
        )
        return self._payload_list(data, DEVICE_SHARED_MANAGER_PATH)

    async def async_remove_shared_access(
        self, *, bind_user_id: str, device_id: str | int
    ) -> dict[str, Any]:
        """Remove a single shared device access.

        Parameters:
            bind_user_id: User ID whose access is being removed.
            device_id: Device identifier.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            DEVICE_SHARED_REMOVE_PATH,
            {"bindUserId": bind_user_id, "deviceId": str(device_id)},
        )

    async def async_remove_all_shared_access(
        self, *, bind_user_id: str, level: int = 0
    ) -> dict[str, Any]:
        """Remove all shared access for a user.

        Parameters:
            bind_user_id: User ID whose access is being removed.
            level: Share level filter.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            DEVICE_SHARED_REMOVE_ALL_PATH,
            {"bindUserId": bind_user_id, "level": level},
        )

    async def async_check_system_bound(
        self, *, bind_key: str, device_sn: str, guid: str
    ) -> dict[str, Any]:
        """Check if a system is already bound.

        Parameters:
            bind_key: Device bind key.
            device_sn: Device serial number.
            guid: Device GUID.

        Returns:
            dict: Backend response data.
        """
        return await self._get_json(
            SYSTEM_EXIST_PATH,
            {"bindKey": bind_key, "deviceSn": device_sn, "guid": guid},
        )

    async def async_create_system(self, **kwargs: Any) -> dict[str, Any]:
        """Create or configure a system.

        Parameters are passed directly to the backend.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(SYSTEM_CREATE_PATH, kwargs)

    async def async_modify_device_name(
        self, *, device_name: str, id: str | int
    ) -> dict[str, Any]:
        """Rename a device.

        Parameters:
            device_name: New device name.
            id: Device identifier.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            SYSTEM_DEVICE_NAME_PATH,
            {"deviceName": device_name, "id": str(id)},
        )

    async def async_modify_pv_name(
        self, *, device_sn: str, index: int, name: str
    ) -> dict[str, Any]:
        """Rename a PV input.

        Parameters:
            device_sn: Device serial number.
            index: PV input index (0-based).
            name: New PV name.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            PV_NAME_PATH,
            {"deviceSn": device_sn, "index": index, "name": name},
        )

    # --- OTA endpoints (additional) ------------------------------------------

    async def async_get_ble_ota_link(
        self,
        *,
        device_sn: str,
        sub_device_sn: str,
        target_firmware_ids: str,
        target_version_id: str,
    ) -> dict[str, Any]:
        """Query BLE OTA link for a sub-device.

        Parameters:
            device_sn: Parent device serial number.
            sub_device_sn: Sub-device serial number.
            target_firmware_ids: Target firmware IDs.
            target_version_id: Target version ID.

        Returns:
            dict: Backend response data.
        """
        return await self._get_json(
            BLE_OTA_LINK_PATH,
            {
                "deviceSn": device_sn,
                "subDeviceSn": sub_device_sn,
                "targetFirmwareIds": target_firmware_ids,
                "targetVersionId": target_version_id,
            },
        )

    async def async_get_ble_ota_versions(self, version_list: str) -> dict[str, Any]:
        """List available BLE OTA versions.

        Parameters:
            version_list: Version list query parameter.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(BLE_OTA_VERSIONS_PATH, {"list": version_list})

    async def async_start_ota_update(
        self,
        *,
        device_sn: str,
        sub_device_sn: str,
        target_firmware_ids: str,
        target_version_id: str,
    ) -> dict[str, Any]:
        """Start an OTA firmware update.

        Parameters:
            device_sn: Device serial number.
            sub_device_sn: Sub-device serial number (empty for main device).
            target_firmware_ids: Target firmware IDs.
            target_version_id: Target version ID.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            OTA_UPDATE_PATH,
            {
                "deviceSn": device_sn,
                "subDeviceSn": sub_device_sn,
                "targetFirmwareIds": target_firmware_ids,
                "targetVersionId": target_version_id,
            },
        )

    async def async_get_charge_report(
        self, *, device_sn: str, page_index: int = 1
    ) -> dict[str, Any]:
        """Fetch charge report history.

        Parameters:
            device_sn: Device serial number.
            page_index: Page number (1-based).

        Returns:
            dict: Backend response data.
        """
        data = await self._get_json(
            CHARGE_REPORT_PATH,
            {"deviceSn": device_sn, "pageIndex": page_index},
        )
        return self._payload_dict(data, CHARGE_REPORT_PATH)
