"""Miscellaneous endpoints: FAQ, feedback, privacy, alerts, offline stats."""

from typing import Any

from custom_components.jackery_solarvault.client._http import BaseHTTPMixin
from custom_components.jackery_solarvault.const import (
    ALARM_DETAIL_PATH,
    ALERT_SYNC_PATH,
    APP_VERSION_PATH,
    BANNER_LIST_PATH,
    FAQ_ANSWER_PATH,
    FAQ_LIST_PATH,
    FEEDBACK_PATH,
    GCS_LIST_PATH,
    INSTRUCTION_PATH,
    OFFLINE_STAT_PATH,
    POWER3_PATH,
    PRIVACY_CHECK_PATH,
    PRIVACY_CONSENT_PATH,
    ZONE_LIST_PATH,
)


class MiscEndpointMixin(BaseHTTPMixin):
    """Miscellaneous endpoint methods."""

    async def async_check_app_version(self) -> dict[str, Any]:
        """Check whether a newer app version is available.

        Returns:
            dict[str, Any]: Dictionary containing the backend's normalized version
            information.
        """
        data = await self._get_json(APP_VERSION_PATH)
        return self._payload_dict(data, APP_VERSION_PATH)

    async def async_get_banner_list(self) -> list[dict[str, Any]]:
        """Get the list of banner entries from the backend.

        Each item is a dictionary representing a banner and has been normalized via the
        client's payload parser.

        Returns:
            list[dict[str, Any]]: Banner entry dictionaries.
        """
        data = await self._get_json(BANNER_LIST_PATH)
        return self._payload_list(data, BANNER_LIST_PATH)

    async def async_submit_feedback(
        self,
        *,
        contact_info: str,
        content: str,
        device_sn: str = "",
        image: str = "",
    ) -> dict[str, Any]:
        """Send user feedback to the backend.

        Parameters:
            contact_info (str): Contact information to include with the feedback.
            content (str): Feedback message.
            device_sn (str): Device serial number to associate with the feedback, if
            any.
            image (str): Base64-encoded image to attach to the feedback, if any.

        Returns:
            dict: Response data returned by the backend.
        """
        fields: dict[str, Any] = {
            "contactInfo": contact_info,
            "content": content,
        }
        if device_sn:
            fields["deviceSn"] = device_sn
        if image:
            fields["image"] = image
        return await self._post_json(FEEDBACK_PATH, fields)

    async def async_get_faq_list(self) -> list[dict[str, Any]]:
        """Retrieve the list of FAQ entries.

        Returns:
            list[dict[str, Any]]: List of FAQ entry objects returned by the backend.
        """
        data = await self._get_json(FAQ_LIST_PATH)
        return self._payload_list(data, FAQ_LIST_PATH)

    async def async_get_faq_answer(self) -> list[dict[str, Any]]:
        """Retrieve FAQ answers from the backend.

        Returns:
            list[dict[str, Any]]: List of FAQ answer entries as dictionaries.
        """
        data = await self._get_json(FAQ_ANSWER_PATH)
        return self._payload_list(data, FAQ_ANSWER_PATH)

    async def async_agree_privacy_consent(
        self,
        *,
        pending_agree_version_ids: str,
    ) -> dict[str, Any]:
        """Record agreement to one or more privacy consent versions.

        Parameters:
            pending_agree_version_ids (str): Comma-separated privacy version IDs to
            agree to.

        Returns:
            dict: Response payload returned by the backend.
        """
        return await self._post_json(
            PRIVACY_CONSENT_PATH,
            {"pendingAgreeVersionIds": pending_agree_version_ids},
        )

    async def async_check_privacy_update(self) -> dict[str, Any]:
        """Determine whether the backend requires an updated privacy consent.

        Returns:
            dict: Server response containing privacy update information, including
            whether an update is required and any related metadata.
        """
        data = await self._get_json(PRIVACY_CHECK_PATH)
        return self._payload_dict(data, PRIVACY_CHECK_PATH)

    async def async_get_product_instruction(
        self,
        *,
        dev_sn: str,
        type: str = "",
    ) -> dict[str, Any]:
        """Retrieve product instructions for a given device.

        Parameters:
            dev_sn (str): Device serial number used to query instructions.
            type (str): Optional instruction type filter; when empty, no type filter is
            applied.

        Returns:
            dict[str, Any]: Normalized instruction payload returned by the backend.
        """
        params: dict[str, str] = {"devSn": dev_sn}
        if type:
            params["type"] = type
        data = await self._get_json(INSTRUCTION_PATH, params=params)
        return self._payload_dict(data, INSTRUCTION_PATH)

    async def async_get_zone_list(self) -> list[dict[str, Any]]:
        """Retrieve the list of country/zone entries for DIY devices.

        Returns:
            list[dict[str, Any]]: Zone entry dictionaries returned by the backend.
        """
        data = await self._get_json(ZONE_LIST_PATH)
        return self._payload_list(data, ZONE_LIST_PATH)

    async def async_get_gcs_list(self, *, country: str) -> list[dict[str, Any]]:
        """Retrieve the list of grid-connection (GCS) standards for the specified.

        country.

        Parameters:
            country (str): Country code.

        Returns:
            list[dict[str, Any]]: List of grid standard entries, each represented as a
            dictionary.
        """
        data = await self._get_json(GCS_LIST_PATH, params={"country": country})
        return self._payload_list(data, GCS_LIST_PATH)

    async def async_get_alarm_detail(self, *, alarm_key: str) -> dict[str, Any]:
        """Retrieve detailed information for a specific alarm.

        Parameters:
            alarm_key (str): Alarm identifier to fetch.

        Returns:
            alarm_detail (dict[str, Any]): Dictionary of alarm detail fields and their
            values.
        """
        data = await self._get_json(ALARM_DETAIL_PATH, params={"alarmKey": alarm_key})
        return self._payload_dict(data, ALARM_DETAIL_PATH)

    async def async_sync_alerts(self, *, content: str, id: str | int) -> dict[str, Any]:
        """Sync device faults and alarms.

        Parameters:
            content: Alert content (JSON).
            id: Device/system identifier.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            ALERT_SYNC_PATH,
            {"content": content, "id": str(id)},
        )

    async def async_get_offline_statistics(self) -> dict[str, Any]:
        """Retrieve offline statistics from the backend.

        Returns:
            dict[str, Any]: Offline statistics payload.
        """
        data = await self._get_json(OFFLINE_STAT_PATH)
        return self._payload_dict(data, OFFLINE_STAT_PATH)

    async def async_get_power3(
        self,
        *,
        device_sn: str,
        properties: str,
    ) -> dict[str, Any]:
        """Retrieve Power3 property values for a device.

        Parameters:
            device_sn (str): Device serial number.
            properties (str): Comma-separated property names to request.

        Returns:
            dict: Normalized Power3 property payload returned by the backend.
        """
        data = await self._get_json(
            POWER3_PATH,
            {"deviceSn": device_sn, "properties": properties},
        )
        return self._payload_dict(data, POWER3_PATH)
