"""Shelly Cloud device normalization and merge helpers.

Pure functions extracted from coordinator.py (Phase 6). These handle
Shelly Cloud DeviceItem/RealData payload normalization, identity matching,
and merge into CT/socket/meter-head buckets — all without touching
coordinator state.

Source: coordinator.py lines 3131-3296 (Phase 6 extraction).
"""

from typing import TYPE_CHECKING, Any

from jackery_solarvault.const import (
    FIELD_CONTROL_ALLOWED,
    FIELD_DEVICE_CODE,
    FIELD_DEV_TYPE,
    FIELD_HOST,
    FIELD_ICON,
    FIELD_ICON_PATH,
    FIELD_INTEGRATOR_ENABLED,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IS_CLOUD,
    FIELD_ONLINE,
    FIELD_ONLINE_STATUS,
    FIELD_OP,
    FIELD_OUT_PW,
    FIELD_POWER_BODY,
    FIELD_SCAN_NAME,
    FIELD_SWITCH,
    FIELD_SWITCH_STATE,
    FIELD_SYS_SWITCH,
    PAYLOAD_CT_METER,
    PAYLOAD_METER_HEADS,
    PAYLOAD_SMART_PLUGS,
    SUBDEVICE_DEV_TYPE_CT,
    SUBDEVICE_DEV_TYPE_METER_HEAD,
    SUBDEVICE_DEV_TYPE_SOCKET,
    SUBDEVICE_SCAN_NAME_DEV_TYPES,
)
from jackery_solarvault.handlers.mqtt_handlers import (
    merge_smart_plug_lists,
    merge_subdevice_list_by_identity,
    merge_subdevice_lists_by_sn,
)
from jackery_solarvault.subdevices.detector import (
    entry_subdevice_candidates,
    subdevice_dev_type,
    subdevice_id,
    subdevice_identity_values,
    subdevice_serial,
)

from .property_merge import merge_dict_values

if TYPE_CHECKING:
    from collections.abc import Mapping


def shelly_cloud_api_device_id(item: dict[str, Any]) -> str | None:
    """
    Determine the native Shelly Cloud device identifier for a device payload.
    
    Returns the Shelly-native device id used by Shelly Cloud realtime/control APIs when the provided item represents a Shelly Cloud–related payload (e.g., identified as Shelly, marked as cloud, or containing host/device code). Selection preference:
    - a non-empty, non-numeric deviceId when present,
    - otherwise a subdevice serial-based id if available,
    - otherwise a generic subdevice id.
    Returns:
        str: Device id suitable for Shelly Cloud APIs, or `None` if the item does not indicate a Shelly Cloud payload.
    """
    scan_name = str(item.get(FIELD_SCAN_NAME) or "").lower()
    is_shelly = scan_name.startswith("shelly")
    if not (
        is_shelly
        or str(item.get(FIELD_IS_CLOUD)).lower() in {"1", "true"}
        or item.get(FIELD_HOST) is not None
        or item.get(FIELD_DEVICE_CODE) is not None
    ):
        return None

    direct_id = item.get("deviceId")
    if is_shelly:
        # System-list accessories use a numeric Jackery accessory id in
        # deviceId, while Shelly Cloud realtime/control expects the native
        # Shelly device id (`5c...`). The app-linked boundDevices payload
        # exposes that id either as deviceId or, in system-list, deviceSn.
        if direct_id not in {None, ""} and not str(direct_id).isdecimal():
            return str(direct_id)
        serial = subdevice_serial(item)
        if serial:
            return serial

    return subdevice_id(item)


def normalize_shelly_cloud_payload(
    source: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Normalize and flatten a Shelly Cloud DeviceItem or RealData payload into subdevice fields.
    
    Creates a shallow copy of all keys from `source` whose values are not None, merges any dictionary found under FIELD_POWER_BODY into the top-level result, and sets several fallback fields when their canonical counterparts are present:
    - copies FIELD_SWITCH into FIELD_SWITCH_STATE and FIELD_SYS_SWITCH if those keys are missing,
    - copies FIELD_OP -> FIELD_OUT_PW, FIELD_IP -> FIELD_IN_PW, and FIELD_ONLINE -> FIELD_ONLINE_STATUS when missing.
    If FIELD_SCAN_NAME is present and (lowercased) matches an entry in SUBDEVICE_SCAN_NAME_DEV_TYPES, rewrites FIELD_SCAN_NAME to the lowercased value and sets FIELD_DEV_TYPE from the mapping when missing.
    
    Parameters:
        source (Mapping[str, Any]): Original Shelly Cloud payload (DeviceItem or RealData).
    
    Returns:
        dict[str, Any]: A normalized, flattened dictionary suitable for subdevice merging.
    """
    normalized = {key: value for key, value in source.items() if value is not None}
    power_body = normalized.get(FIELD_POWER_BODY)
    if isinstance(power_body, dict):
        normalized = merge_dict_values(normalized, power_body)
    if FIELD_SWITCH in normalized:
        switch_state = normalized[FIELD_SWITCH]
        normalized.setdefault(FIELD_SWITCH_STATE, switch_state)
        normalized.setdefault(FIELD_SYS_SWITCH, switch_state)
    if FIELD_OP in normalized:
        normalized.setdefault(FIELD_OUT_PW, normalized[FIELD_OP])
    if FIELD_IP in normalized:
        normalized.setdefault(FIELD_IN_PW, normalized[FIELD_IP])
    if FIELD_ONLINE in normalized:
        normalized.setdefault(FIELD_ONLINE_STATUS, normalized[FIELD_ONLINE])
    scan_name = str(normalized.get(FIELD_SCAN_NAME) or "").lower()
    if scan_name and scan_name in SUBDEVICE_SCAN_NAME_DEV_TYPES:
        normalized[FIELD_SCAN_NAME] = scan_name
        normalized.setdefault(
            FIELD_DEV_TYPE,
            SUBDEVICE_SCAN_NAME_DEV_TYPES[scan_name],
        )
    return normalized


def shelly_cloud_device_matches_entry(
    entry: dict[str, Any],
    shelly_device: Mapping[str, Any],
) -> bool:
    """
    Determine whether the given Shelly Cloud device belongs to the provided entry.
    
    Checks for any overlap between the set of subdevice identity values derived from `shelly_device` and the identity sets of the entry's subdevice candidates.
    
    Returns:
        `True` if any subdevice identity intersects, `False` otherwise.
    """
    shelly_ids = subdevice_identity_values(shelly_device)
    if not shelly_ids:
        return False
    return any(
        shelly_ids & subdevice_identity_values(candidate)
        for candidate in entry_subdevice_candidates(entry)
    )


def merge_shelly_cloud_item(  # noqa: PLR0911
    entry: dict[str, Any],
    source: Mapping[str, Any],
) -> bool:
    """
    Merge a normalized Shelly Cloud device/realtime payload into the appropriate buckets of an entry.
    
    Normalizes the provided payload, marks it as a cloud-sourced item when relevant keys are present, and attempts to merge the normalized subdevice into one of the entry's buckets (CT meter, smart plugs, or meter heads). If a specific device type is determined, the payload is merged into the corresponding bucket; otherwise an identity-based fallback attempts to merge into any matching bucket. The function mutates `entry` in place.
    
    Parameters:
        entry (dict[str, Any]): The entry to update; updated in place when a merge occurs.
        source (Mapping[str, Any]): The raw Shelly Cloud device or realtime payload to normalize and merge.
    
    Returns:
        bool: `True` if `entry` was modified by the merge, `False` otherwise.
    """
    normalized = normalize_shelly_cloud_payload(source)
    if any(
        key in source
        for key in (
            FIELD_CONTROL_ALLOWED,
            FIELD_DEVICE_CODE,
            FIELD_HOST,
            FIELD_ICON,
            FIELD_ICON_PATH,
            FIELD_INTEGRATOR_ENABLED,
            FIELD_POWER_BODY,
        )
    ):
        normalized.setdefault(FIELD_IS_CLOUD, True)
    item_ids = subdevice_identity_values(normalized)
    dev_type = subdevice_dev_type(normalized)
    if dev_type == SUBDEVICE_DEV_TYPE_CT:
        current = entry.get(PAYLOAD_CT_METER)
        current_dict = current if isinstance(current, dict) else {}
        merged_ct = merge_dict_values(current_dict, normalized)
        if merged_ct != current_dict:
            entry[PAYLOAD_CT_METER] = merged_ct
            return True
        return False
    if dev_type == SUBDEVICE_DEV_TYPE_SOCKET:
        current = entry.get(PAYLOAD_SMART_PLUGS)
        merged_plugs = merge_subdevice_list_by_identity(current, normalized)
        if merged_plugs != current:
            entry[PAYLOAD_SMART_PLUGS] = merged_plugs
            return True
        return False
    if dev_type == SUBDEVICE_DEV_TYPE_METER_HEAD:
        current = entry.get(PAYLOAD_METER_HEADS)
        merged_meter_heads = merge_subdevice_list_by_identity(current, normalized)
        if merged_meter_heads != current:
            entry[PAYLOAD_METER_HEADS] = merged_meter_heads
            return True
        return False

    if not item_ids:
        return False
    ct = entry.get(PAYLOAD_CT_METER)
    if isinstance(ct, dict) and item_ids & subdevice_identity_values(ct):
        entry[PAYLOAD_CT_METER] = merge_dict_values(ct, normalized)
        return True
    for bucket, merger in (
        (PAYLOAD_SMART_PLUGS, merge_smart_plug_lists),
        (PAYLOAD_METER_HEADS, merge_subdevice_lists_by_sn),
    ):
        items = entry.get(bucket)
        if not isinstance(items, list):
            continue
        if any(
            isinstance(item, dict) and item_ids & subdevice_identity_values(item)
            for item in items
        ):
            entry[bucket] = merger(items, [normalized])
            return True
    return False


def shelly_cloud_device_ids(entry: dict[str, Any]) -> list[str]:
    """
    Collects known Shelly Cloud device identifiers associated with an entry.
    
    Parameters:
    	entry (dict[str, Any]): Entry dictionary containing stored device/subdevice descriptors.
    
    Returns:
    	list[str]: Deduplicated list of Shelly Cloud device IDs discovered for the given entry.
    """
    ids: list[str] = []
    for candidate in entry_subdevice_candidates(entry):
        dev_id = shelly_cloud_api_device_id(candidate)
        if dev_id and dev_id not in ids:
            ids.append(dev_id)
    return ids
