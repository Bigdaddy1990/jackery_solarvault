"""Pure interpretation of the app's OTA availability evidence."""

from collections.abc import Mapping

from .const import FIELD_IS_FIRMWARE_UPGRADE, FIELD_UPDATE_STATUS


def firmware_upgrade_available(payload: object) -> bool | None:
    """Prefer an explicit boolean; otherwise accept documented OTA status 0/6."""
    if not isinstance(payload, Mapping):
        return None
    flag = payload.get(FIELD_IS_FIRMWARE_UPGRADE)
    if isinstance(flag, bool):
        return flag
    status = payload.get(FIELD_UPDATE_STATUS)
    if type(status) is not int:
        return None
    # DeviceMqttOTASelectApi's device/ota/list response feeds
    # BaseMqttOTAActivity.updateView: 0 -> CAN_UPDATE, 6 -> NO_NEED_UPDATE.
    if status == 0:
        return True
    if status == 6:  # ruff: ignore[magic-value-comparison]
        return False
    return None
