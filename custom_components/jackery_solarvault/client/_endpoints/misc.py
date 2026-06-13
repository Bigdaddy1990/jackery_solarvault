"""Miscellaneous endpoints: FAQ, feedback, privacy, alerts, offline stats."""

from typing import Any

from jackery_solarvault.client._http import BaseHTTPMixin
from jackery_solarvault.const import (
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
        """Check for app version updates.

        Returns:
            dict: Version check result.
        """
        data = await self._get_json(APP_VERSION_PATH)
        return self._payload_dict(data, APP_VERSION_PATH)

    async def async_get_banner_list(self) -> list[dict[str, Any]]:
        """Get banner list.

        Returns:
            list: Banner entries.
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
        """Submit user feedback.

        Parameters:
            contact_info: Contact information.
            content: Feedback content.
            device_sn: Device serial number (optional).
            image: Base64-encoded image (optional).

        Returns:
            dict: Backend response data.
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
        """Get FAQ list.

        Returns:
            list: FAQ entries.
        """
        data = await self._get_json(FAQ_LIST_PATH)
        return self._payload_list(data, FAQ_LIST_PATH)

    async def async_get_faq_answer(self) -> list[dict[str, Any]]:
        """Get FAQ answers.

        Returns:
            list: FAQ answer entries.
        """
        data = await self._get_json(FAQ_ANSWER_PATH)
        return self._payload_list(data, FAQ_ANSWER_PATH)

    async def async_agree_privacy_consent(
        self, *, pending_agree_version_ids: str
    ) -> dict[str, Any]:
        """Agree to privacy/consent updates.

        Parameters:
            pending_agree_version_ids: Comma-separated version IDs to agree to.

        Returns:
            dict: Backend response data.
        """
        return await self._post_json(
            PRIVACY_CONSENT_PATH,
            {"pendingAgreeVersionIds": pending_agree_version_ids},
        )

    async def async_check_privacy_update(self) -> dict[str, Any]:
        """Check if privacy consent update is required.

        Returns:
            dict: Privacy update check result.
        """
        data = await self._get_json(PRIVACY_CHECK_PATH)
        return self._payload_dict(data, PRIVACY_CHECK_PATH)

    async def async_get_product_instruction(
        self, *, dev_sn: str, type: str = ""
    ) -> dict[str, Any]:
        """Get product instruction/documentation.

        Parameters:
            dev_sn: Device serial number.
            type: Instruction type filter.

        Returns:
            dict: Instruction data.
        """
        params: dict[str, str] = {"devSn": dev_sn}
        if type:
            params["type"] = type
        data = await self._get_json(INSTRUCTION_PATH, params=params)
        return self._payload_dict(data, INSTRUCTION_PATH)

    async def async_get_zone_list(self) -> list[dict[str, Any]]:
        """Get country/zone list for DIY devices.

        Returns:
            list: Zone entries.
        """
        data = await self._get_json(ZONE_LIST_PATH)
        return self._payload_list(data, ZONE_LIST_PATH)

    async def async_get_gcs_list(self, *, country: str) -> list[dict[str, Any]]:
        """Get grid standard/parallel-in standards list.

        Parameters:
            country: Country code.

        Returns:
            list: Grid standard entries.
        """
        data = await self._get_json(GCS_LIST_PATH, params={"country": country})
        return self._payload_list(data, GCS_LIST_PATH)

    async def async_get_alarm_detail(self, *, alarm_key: str) -> dict[str, Any]:
        """Get alarm detail.

        Parameters:
            alarm_key: Alarm identifier.

        Returns:
            dict: Alarm detail data.
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
            ALERT_SYNC_PATH, {"content": content, "id": str(id)}
        )

    async def async_get_offline_statistics(self) -> dict[str, Any]:
        """Get offline statistics data.

        Returns:
            dict: Offline statistics payload.
        """
        data = await self._get_json(OFFLINE_STAT_PATH)
        return self._payload_dict(data, OFFLINE_STAT_PATH)

    async def async_get_power3(
        self, *, device_sn: str, properties: str
    ) -> dict[str, Any]:
        """Get power3 property data.

        Parameters:
            device_sn: Device serial number.
            properties: Comma-separated property names.

        Returns:
            dict: Power3 data.
        """
        data = await self._get_json(
            POWER3_PATH,
            {"deviceSn": device_sn, "properties": properties},
        )
        return self._payload_dict(data, POWER3_PATH)
