"""App-proven HTTP start of a device-side MQTT firmware update."""

from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError

if TYPE_CHECKING:
    from ..coordinator import JackerySolarVaultCoordinator


def _ota_identifier(value: object) -> str | None:
    """Project Gson String identifiers without losing integer precision."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 0 else None
    if isinstance(value, str) and value.strip() and "," not in value:
        return value.strip()
    return None


def _ota_candidate_ids(candidate: object, target_sn: str) -> tuple[str, str] | None:
    """Validate a fresh app OTA-list item for exactly one physical target."""
    if not isinstance(candidate, dict) or candidate.get("deviceSn") != target_sn:
        return None
    if type(candidate.get("updateStatus")) is not int or candidate["updateStatus"] != 0:
        return None
    version_id = _ota_identifier(candidate.get("targetVersionId"))
    modules = candidate.get("targetModuleVersion")
    if version_id is None or not isinstance(modules, list):
        return None
    module_ids = [_ota_identifier(module) for module in modules]
    if any(module is None for module in module_ids):
        return None
    return version_id, ",".join(module for module in module_ids if module is not None)


async def _async_start_cloud_ota_locked(  # ruff: ignore[too-many-branches]
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    *,
    target_serial: str | None = None,
    requested_version: str | None = None,
) -> dict[str, Any]:
    """Start the selected Home/pack update through the App's HTTP endpoint.

    The endpoint starts a device-side MQTT OTA job. Local HA MQTT and BLE are
    neither command routes nor prerequisites for this operation.
    """
    if coordinator.device_family(device_id) != "home":
        raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
            "Cloud OTA preflight is not established for this device family"
        )
    binding = coordinator._http_parent_binding(device_id)  # ruff: ignore[private-member-access]
    parent_sn = binding[2]
    if not isinstance(parent_sn, str) or not parent_sn.strip():
        raise HomeAssistantError("Cloud OTA requires the current device identity")  # ruff: ignore[raise-vanilla-args]
    target = target_serial or parent_sn
    pack_revision = (
        coordinator.battery_pack_identity_revision(device_id, target)
        if target != parent_sn
        else None
    )

    def identity_current() -> bool:
        if not coordinator._http_parent_binding_is_current(device_id, binding):  # ruff: ignore[private-member-access]
            return False
        if target == parent_sn:
            return True
        payload = (coordinator.data or {}).get(device_id, {})
        packs = payload.get("battery_packs") if isinstance(payload, dict) else None
        if not isinstance(packs, list):
            return False
        matching = [
            pack
            for pack in packs
            if isinstance(pack, dict) and pack.get("deviceSn") == target
        ]
        return (
            len(matching) == 1
            and coordinator.battery_pack_identity_revision(device_id, target)
            == pack_revision
            and any(
                coordinator.battery_pack_identity_serial(device_id, index) == target
                for index in range(1, 6)
            )
        )

    if not identity_current():
        raise HomeAssistantError("Cloud OTA target identity is not current")  # ruff: ignore[raise-vanilla-args]
    candidate = await coordinator.api.async_get_ota_info(target)
    if not identity_current():
        raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
            "Cloud OTA target identity changed during firmware selection"
        )
    ids = _ota_candidate_ids(candidate, target)
    if ids is None:
        raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
            "Cloud OTA requires a current update candidate for the selected device"
        )
    target_version = candidate.get("targetVersion")
    if requested_version is not None and requested_version != target_version:
        raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
            "Requested firmware version does not match the current Cloud OTA target"
        )
    if target == parent_sn:
        response = await coordinator.api.async_get_device_property(device_id)
        if not identity_current():
            raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
                "Cloud OTA device identity changed during preflight"
            )
        device = response.get("device") if isinstance(response, dict) else None
        properties = response.get("properties") if isinstance(response, dict) else None
        if (
            not isinstance(device, dict)
            or device.get("deviceSn") != parent_sn
            or (device.get("id") is not None and str(device["id"]) != str(device_id))
        ):
            raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
                "Cloud OTA requires a matching current HTTP device response"
            )
        soc = properties.get("batSoc") if isinstance(properties, dict) else None
        if (
            isinstance(soc, bool)
            or not isinstance(soc, (int, float))
            or not 10 < soc <= 100  # ruff: ignore[magic-value-comparison]
        ):
            raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
                "Cloud OTA requires current internal battery charge above 10%"
            )
    version_id, module_ids = ids
    result = await coordinator.api.async_start_ota_update(
        device_sn=parent_sn,
        sub_device_sn="" if target == parent_sn else target,
        target_firmware_ids=module_ids,
        target_version_id=version_id,
    )
    if not isinstance(result, dict):
        raise HomeAssistantError("Cloud OTA start returned an invalid response")  # ruff: ignore[raise-vanilla-args]
    accepted = result.get("data")
    if (
        accepted is False
        or (type(accepted) is int and accepted == 0)
        or (isinstance(accepted, str) and accepted.strip().casefold() == "false")
    ):
        raise HomeAssistantError("Cloud OTA start was rejected by the server")  # ruff: ignore[raise-vanilla-args]
    if not identity_current():
        raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
            "Cloud OTA may have started, but the selected device identity changed"
        )
    return result


async def async_start_cloud_ota(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    *,
    target_serial: str | None = None,
    requested_version: str | None = None,
) -> dict[str, Any]:
    """Reject overlapping starts for one physical parent device."""
    active = getattr(coordinator, "_cloud_ota_starting_devices", None)
    if not isinstance(active, set):
        active = coordinator._cloud_ota_starting_devices = set()  # ruff: ignore[private-member-access]
    if device_id in active:
        raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
            "A Cloud OTA start is already in progress for this device"
        )
    active.add(device_id)
    try:
        return await _async_start_cloud_ota_locked(
            coordinator,
            device_id,
            target_serial=target_serial,
            requested_version=requested_version,
        )
    finally:
        active.discard(device_id)
