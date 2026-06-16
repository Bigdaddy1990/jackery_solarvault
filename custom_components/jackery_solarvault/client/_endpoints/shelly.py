"""Shelly device, control, auth, and binding endpoints."""

from typing import Any

from custom_components.jackery_solarvault.client._http import (
    BaseHTTPMixin,
    JackeryApiError,
)
from custom_components.jackery_solarvault.const import (
    FIELD_ACTION,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_FUNCTION,
    SHELLY_AUTH_URL_PATH,
    SHELLY_BINDING_FAILURES_PATH,
    SHELLY_CONTROL_PATH,
    SHELLY_DEVICES_PATH,
    SHELLY_REALTIME_POWER_PATH,
    SHELLY_UNBIND_ACCOUNT_PATH,
    SHELLY_UNBIND_DEVICE_PATH,
)


def _data_field_accepted(data: dict[str, Any]) -> bool:
    """Determine if a Shelly write response's data field signals acceptance.

    The backend signals acceptance in the top-level data field as boolean True
    or a truthy token ("true"/"1"/"ok", case-insensitive). Anything else, including
    a missing field, is treated as not accepted.

    Parameters:
        data (dict[str, Any]): Response data dictionary to check.

    Returns:
        bool: True if the data field signals acceptance, False otherwise.
    """
    val = data.get(FIELD_DATA)
    if val is True:
        return True
    if isinstance(val, (str, int)):
        return str(val).lower() in {"true", "1", "ok"}
    return False


def _data_field_accepted(data: dict[str, Any]) -> bool:
    """Determine if a Shelly write response's data field signals acceptance.

    The backend signals acceptance in the top-level data field as boolean True
    or a truthy token ("true"/"1"/"ok", case-insensitive). Anything else, including
    a missing field, is treated as not accepted.

    Parameters:
        data (dict[str, Any]): Response data dictionary to check.

    Returns:
        bool: True if the data field signals acceptance, False otherwise.
    """
    val = data.get(FIELD_DATA)
    if val is True:
        return True
    if isinstance(val, (str, int)):
        return str(val).lower() in {"true", "1", "ok"}
    return False


class ShellyEndpointMixin(BaseHTTPMixin):
    """Mixin providing Shelly-related cloud API endpoints."""

    async def async_get_shelly_devices(self) -> list[dict[str, Any]]:
        """Retrieve a normalized list of Shelly devices linked to the account.

        Accepts multiple backend response shapes for the `data` field: a list of device
        dicts; a dict containing `boundDevices` or `devices` lists; or a single device
        dict identified by `deviceId`. Non-dict entries are ignored.

        Returns:
            A list of Shelly device objects; empty list if none are present.
        """
        data = await self._get_json(SHELLY_DEVICES_PATH)
        raw = data.get(FIELD_DATA)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
        if isinstance(raw, dict):
            bound_devices = raw.get("boundDevices")
            if isinstance(bound_devices, list):
                return [item for item in bound_devices if isinstance(item, dict)]
            devices = raw.get("devices")
            if isinstance(devices, list):
                return [item for item in devices if isinstance(item, dict)]
            if raw.get(FIELD_DEVICE_ID) is not None:
                return [raw]
        return []

    async def async_get_shelly_realtime_power(
        self,
        device_id: str | int,
    ) -> dict[str, Any]:
        """Fetches realtime power metrics for a Shelly accessory linked to the account.

        Parameters:
            device_id (str | int): The Shelly device identifier.

        Returns:
            dict: The response `data` object parsed as a dictionary (empty dict if the
            payload is missing or not a dict).
        """
        data = await self._get_json(
            SHELLY_REALTIME_POWER_PATH,
            params={FIELD_DEVICE_ID: str(device_id)},
        )
        return self._payload_dict(data, SHELLY_REALTIME_POWER_PATH)

    async def async_control_shelly_device(
        self,
        device_id: str | int,
        *,
        action: str,
        function: str,
        control_allowed: bool = True,
    ) -> bool:
        """Send a control command to a Shelly device.

        Parameters:
            device_id (str | int): Identifier of the Shelly device to control.
            action (str): Action name to perform as provided by the Shelly app.
            function (str): Function name associated with the action as provided by the
            Shelly app.
            control_allowed (bool): If `False`, the call will raise a `JackeryApiError`
            and no request will be sent.

        Returns:
            bool: `true` if the backend indicates the control request was accepted,
            `false` otherwise.

        Raises:
            JackeryApiError: If `control_allowed` is `False`.
        """
        if not control_allowed:
            msg = "Shelly control is not allowed for this device"
            raise JackeryApiError(msg)
        data = await self._post_form(
            SHELLY_CONTROL_PATH,
            {
                FIELD_DEVICE_ID: str(device_id),
                FIELD_ACTION: str(action),
                FIELD_FUNCTION: str(function),
            },
        )
        return _data_field_accepted(data)

    async def async_get_shelly_auth_url(self) -> dict[str, Any]:
        """Retrieve the Shelly OAuth authorization URL and accompanying state for the.

        redirect flow.

        Returns:
            dict: Contains `authUrl` (str) and `state` (str) for the Shelly OAuth
            redirect flow.
        """
        data = await self._post_form(SHELLY_AUTH_URL_PATH, {})
        return self._payload_dict(data, SHELLY_AUTH_URL_PATH)

    async def async_unbind_shelly_device(
        self,
        binding_id: int | str,
        device_id: str | int,
    ) -> bool:
        """Unbind a Shelly device from the user's Shelly binding list.

        Parameters:
            binding_id (int | str): Binding identifier from the Shelly devices list.
            device_id (str | int): Shelly device identifier to unbind.

        Returns:
            bool: True if the backend accepted the unbind request, False otherwise.
        """
        data = await self._post_form(
            SHELLY_UNBIND_DEVICE_PATH,
            {
                "bindingId": str(binding_id),
                FIELD_DEVICE_ID: str(device_id),
            },
        )
        return _data_field_accepted(data)

    async def async_unbind_shelly_account(self) -> bool:
        """Unbinds the Shelly account associated with the current user.

        Returns:
            True if the account unbind succeeded, False otherwise.
        """
        data = await self._post_form(SHELLY_UNBIND_ACCOUNT_PATH, {})
        return _data_field_accepted(data)

    async def async_get_shelly_binding_failures(
        self,
        state: str = "",
    ) -> dict[str, Any]:
        """Retrieve a summary of Shelly binding failures.

        Parameters:
            state (str): Optional state filter to narrow the binding failures query.

        Returns:
            dict: Response payload containing `bindCount` (int), `failedDeviceSns`
            (list[str]), and `successDeviceSns` (list[str]).
        """
        params: dict[str, str] = {}
        if state:
            params["state"] = state
        data = await self._get_json(SHELLY_BINDING_FAILURES_PATH, params=params)
        return self._payload_dict(data, SHELLY_BINDING_FAILURES_PATH)
