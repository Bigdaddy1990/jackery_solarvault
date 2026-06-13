"""Subdevice detection and identification helpers.

Pure functions extracted from coordinator.py (Phase 2a).
These detect battery packs, CT meters, smart plugs, and Shelly accessories
without touching coordinator state.

Reference: jackery_entity_field_candidates_v2.json, hbxn_model_fields.html
"""

import contextlib
from typing import TYPE_CHECKING, Any

from ..const import (
    FIELD_ACCESSORIES,
    FIELD_ACTION_ID,
    FIELD_BATTERIES,
    FIELD_BATTERY_PACK,
    FIELD_BATTERY_PACKS,
    FIELD_BATTERY_PACK_LIST,
    FIELD_BAT_NUM,
    FIELD_BAT_SOC,
    FIELD_BIND_ID,
    FIELD_BODY,
    FIELD_DEVICE_CODE,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEVICE_TYPE,
    FIELD_DEV_ID,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_ID,
    FIELD_IN_EGY,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_MESSAGE_TYPE,
    FIELD_OP,
    FIELD_OUT_EGY,
    FIELD_OUT_PW,
    FIELD_PACK_LIST,
    FIELD_PRODUCT_MODEL,
    FIELD_RB,
    FIELD_SCAN_NAME,
    FIELD_SN,
    FIELD_SUB_TYPE,
    FIELD_TYPE_NAME,
    FIELD_UPDATES,
    MQTT_ACTION_IDS_SUBDEVICE,
    NON_BATTERY_SUBDEVICE_TYPES,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_CT_METER,
    PAYLOAD_METER_HEADS,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
    SMART_METER_SUBTYPE,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_METER_HEAD,
    SUBDEVICE_DEV_TYPE_SOCKET,
    SUBDEVICE_SCAN_NAME_DEV_TYPES,
    SUBDEVICE_TYPE_SMART_METER,
)
from ..models.property_merge import find_list_for_key, merge_dict_values
from ..util import safe_float, safe_int

if TYPE_CHECKING:
    from collections.abc import Mapping


def is_subdevice_payload(
    payload: dict[str, Any],
    body: dict[str, Any],
    subdevice_hint_keys: frozenset[str],
    battery_pack_hint_keys: frozenset[str],
    subdevice_dev_type_strings: frozenset[str],
) -> bool:
    """Identify MQTT accessory payloads mixed into the app device topic."""
    msg_type = str(payload.get(FIELD_MESSAGE_TYPE) or "")
    if "SubDevice" in msg_type:
        return True
    action_id = payload.get(FIELD_ACTION_ID)
    action_id_int = safe_float(action_id)
    if action_id_int is not None and int(action_id_int) in MQTT_ACTION_IDS_SUBDEVICE:
        return True
    updates = body.get(FIELD_UPDATES)
    if isinstance(updates, dict) and any(
        key in updates for key in subdevice_hint_keys | battery_pack_hint_keys
    ):
        return True
    dev_type = body.get(FIELD_DEV_TYPE) or body.get(FIELD_DEVICE_TYPE)
    if dev_type is not None and str(dev_type) in subdevice_dev_type_strings:
        return True
    return any(key in body for key in subdevice_hint_keys)


def normalize_battery_pack_payload(item: object) -> dict[str, Any]:
    """Flatten Jackery battery-pack payloads to BatteryPackSub fields.

    The Android app parses add-on battery updates from BatteryPackSub. In
    live MQTT frames the actual values can sit below an ``updates`` object,
    while the top level only carries deviceSn/inPw/outPw metadata.
    """
    if not isinstance(item, dict):
        return {}
    normalized = dict(item)
    for nested_key in (FIELD_UPDATES, FIELD_BODY, PAYLOAD_PROPERTIES):
        nested = normalized.get(nested_key)
        if isinstance(nested, dict):
            normalized = merge_dict_values(normalized, nested)
    aliases = {
        FIELD_RB: FIELD_BAT_SOC,
        FIELD_IP: FIELD_IN_PW,
        FIELD_OP: FIELD_OUT_PW,
    }
    for source_key, target_key in aliases.items():
        if (
            normalized.get(target_key) is None
            and normalized.get(source_key) is not None
        ):
            normalized[target_key] = normalized[source_key]
    return normalized


def looks_like_battery_pack(
    item: object,
    ct_meter_keys: frozenset[str],
    battery_pack_hint_keys: frozenset[str],
) -> bool:
    """Return True for add-on battery pack dicts, not CT/smart meters."""
    if not isinstance(item, dict):
        return False
    if any(key in item for key in ct_meter_keys):
        return False
    if (
        str(item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE) or "")
        in NON_BATTERY_SUBDEVICE_TYPES
    ):
        return False
    scan_name = str(item.get(FIELD_SCAN_NAME) or "").lower()
    if "shelly" in scan_name or "3em" in scan_name:
        return False
    if str(item.get(FIELD_SUB_TYPE) or "") == SMART_METER_SUBTYPE:
        return False
    return any(key in item for key in battery_pack_hint_keys)


def battery_packs_from_source(
    source: object,
    ct_meter_keys: frozenset[str],
    battery_pack_hint_keys: frozenset[str],
) -> list[dict[str, Any]] | None:
    """Extract up to five add-on battery pack payloads from known shapes."""
    for key in (
        FIELD_BATTERY_PACKS,
        FIELD_BATTERY_PACK,
        FIELD_BATTERY_PACK_LIST,
        FIELD_BATTERIES,
        FIELD_PACK_LIST,
    ):
        packs = find_list_for_key(source, key)
        if packs:
            normalized = [normalize_battery_pack_payload(item) for item in packs]
            filtered = [
                item
                for item in normalized
                if looks_like_battery_pack(item, ct_meter_keys, battery_pack_hint_keys)
            ]
            return filtered[:5] if filtered else normalized[:5]
    if isinstance(source, list):
        normalized = [normalize_battery_pack_payload(item) for item in source]
        packs = [
            item
            for item in normalized
            if looks_like_battery_pack(item, ct_meter_keys, battery_pack_hint_keys)
        ]
        return packs[:5] if packs else None
    normalized_source = normalize_battery_pack_payload(source)
    if looks_like_battery_pack(
        normalized_source, ct_meter_keys, battery_pack_hint_keys
    ):
        return [normalized_source]
    return None


def subdevice_serial(item: dict[str, Any]) -> str | None:
    """Return the stable serial field used by app subdevice payloads."""
    serial = item.get(FIELD_DEVICE_SN) or item.get(FIELD_DEV_SN) or item.get(FIELD_SN)
    return str(serial) if serial else None


def subdevice_id(item: dict[str, Any]) -> str | None:
    """Return the cloud id field used by accessory HTTP statistic APIs."""
    dev_id = item.get(FIELD_DEVICE_ID) or item.get(FIELD_ID) or item.get(FIELD_DEV_ID)
    return str(dev_id) if dev_id else None


def subdevice_identity_values(item: Mapping[str, Any]) -> set[str]:
    """Return matching identities used across system-list and Shelly APIs."""
    values: set[str] = set()
    for key in (
        FIELD_DEVICE_ID,
        FIELD_ID,
        FIELD_DEV_ID,
        FIELD_DEVICE_SN,
        FIELD_DEV_SN,
        FIELD_SN,
        FIELD_BIND_ID,
        FIELD_DEVICE_CODE,
    ):
        value = item.get(key)
        if value not in {None, ""}:
            values.add(str(value))
    return values


def subdevice_dev_type(item: Mapping[str, Any]) -> int | None:
    """Return the documented subdevice devType, including Shelly scan names."""
    raw_type = item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE)
    if raw_type not in {None, ""}:
        with contextlib.suppress(TypeError, ValueError):
            return int(str(raw_type))
    scan_name = str(item.get(FIELD_SCAN_NAME) or "").lower()
    return SUBDEVICE_SCAN_NAME_DEV_TYPES.get(scan_name)


def is_smart_meter_accessory(item: dict[str, Any]) -> bool:
    """Return True for the CT/Smart-Meter accessory entry used by the app."""
    if (
        str(item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE) or "")
        == SUBDEVICE_TYPE_SMART_METER
    ):
        return True
    text = " ".join(
        str(item.get(key) or "")
        for key in (
            FIELD_SCAN_NAME,
            FIELD_TYPE_NAME,
            FIELD_DEVICE_NAME,
            FIELD_PRODUCT_MODEL,
        )
    ).lower()
    if "shelly" in text or "3em" in text or "meter" in text or "ct" in text:
        return True
    return str(item.get(FIELD_SUB_TYPE) or "") == SMART_METER_SUBTYPE


def smart_meter_accessories(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Return Smart-Meter accessory metadata from coordinator payload or index."""
    accessories: Any = source.get(FIELD_ACCESSORIES)
    if not isinstance(accessories, list):
        system = source.get(PAYLOAD_SYSTEM) or source.get(PAYLOAD_SYSTEM_META) or {}
        accessories = system.get(FIELD_ACCESSORIES) if isinstance(system, dict) else []
    if not isinstance(accessories, list):
        return []
    return [
        item
        for item in accessories
        if isinstance(item, dict) and is_smart_meter_accessory(item)
    ]


def smart_meter_accessory_device_id(source: dict[str, Any]) -> str | None:
    """Return the app's subDeviceId for CT statistic endpoints."""
    for item in smart_meter_accessories(source):
        dev_id = (
            item.get(FIELD_DEVICE_ID) or item.get(FIELD_ID) or item.get(FIELD_DEV_ID)
        )
        if dev_id is not None:
            return str(dev_id)

    ct = source.get(PAYLOAD_CT_METER) or {}
    if isinstance(ct, dict):
        dev_id = ct.get(FIELD_DEVICE_ID) or ct.get(FIELD_ID) or ct.get(FIELD_DEV_ID)
        if dev_id is not None:
            return str(dev_id)
    return None


def has_smart_meter_accessory(payload: dict[str, Any]) -> bool:
    """Return True when discovery metadata contains a CT/smart meter accessory."""
    return bool(smart_meter_accessories(payload))


def has_subdevice_accessory_or_bucket(
    payload: dict[str, Any],
    *,
    dev_type: int,
    bucket: str,
) -> bool:
    """Return True when discovery or a cached bucket mentions a subdevice."""
    target_type = str(dev_type)
    system = payload.get(PAYLOAD_SYSTEM) or payload.get(PAYLOAD_SYSTEM_META) or {}
    accessories: Any = payload.get(FIELD_ACCESSORIES)
    if not isinstance(accessories, list) and isinstance(system, dict):
        accessories = system.get(FIELD_ACCESSORIES)
    if isinstance(accessories, list):
        for item in accessories:
            if not isinstance(item, dict):
                continue
            item_type = item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE)
            if str(item_type) == target_type:
                return True
    items = payload.get(bucket)
    return isinstance(items, list) and any(isinstance(item, dict) for item in items)


def has_meter_head_accessory(payload: dict[str, Any]) -> bool:
    """Return True when discovery or a prior MQTT reply mentions a meter head."""
    return has_subdevice_accessory_or_bucket(
        payload,
        dev_type=SUBDEVICE_DEV_TYPE_METER_HEAD,
        bucket=PAYLOAD_METER_HEADS,
    )


def has_smart_plug_accessory(payload: dict[str, Any]) -> bool:
    """Return True when discovery or a prior MQTT reply mentions a smart plug."""
    return has_subdevice_accessory_or_bucket(
        payload,
        dev_type=SUBDEVICE_DEV_TYPE_SOCKET,
        bucket=PAYLOAD_SMART_PLUGS,
    )


def subdevice_accessories(
    payload: dict[str, Any],
    *,
    dev_type: int,
) -> list[dict[str, Any]]:
    """Return discovery accessories matching a HomeSubDeviceType value."""
    target_type = str(dev_type)
    system = payload.get(PAYLOAD_SYSTEM) or payload.get(PAYLOAD_SYSTEM_META) or {}
    accessories: Any = payload.get(FIELD_ACCESSORIES)
    if not isinstance(accessories, list) and isinstance(system, dict):
        accessories = system.get(FIELD_ACCESSORIES)
    if not isinstance(accessories, list):
        return []
    return [
        item
        for item in accessories
        if isinstance(item, dict)
        and str(item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE) or "")
        == target_type
    ]


def subdevice_stat_id(
    payload: dict[str, Any],
    subdevice: dict[str, Any],
    *,
    dev_type: int,
) -> str | None:
    """Resolve the accessory id needed by app statistic endpoints."""
    direct_id = subdevice_id(subdevice)
    if direct_id:
        return direct_id
    serial = subdevice_serial(subdevice)
    candidates = subdevice_accessories(payload, dev_type=dev_type)
    if serial:
        for item in candidates:
            if subdevice_serial(item) == serial:
                return subdevice_id(item)
    if len(candidates) == 1:
        return subdevice_id(candidates[0])
    return None


def entry_subdevice_candidates(
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return known accessory dictionaries for one coordinator entry."""
    candidates: list[dict[str, Any]] = []
    system = entry.get(PAYLOAD_SYSTEM) or entry.get(PAYLOAD_SYSTEM_META) or {}
    accessories = system.get(FIELD_ACCESSORIES) if isinstance(system, dict) else []
    if isinstance(accessories, list):
        candidates.extend(item for item in accessories if isinstance(item, dict))
    ct = entry.get(PAYLOAD_CT_METER)
    if isinstance(ct, dict):
        candidates.append(ct)
    for bucket in (PAYLOAD_SMART_PLUGS, PAYLOAD_METER_HEADS):
        items = entry.get(bucket)
        if isinstance(items, list):
            candidates.extend(item for item in items if isinstance(item, dict))
    return candidates


def battery_packs_need_query(payload: dict[str, Any]) -> bool:
    """Return True when add-on packs exist or are expected.

    The Android app polls BatteryPackSub over MQTT. The HTTP
    battery-pack endpoint can return data:null for this product/account,
    so stopping the MQTT query after the first SOC value leaves addon
    batteries stale.
    """
    props = payload.get(PAYLOAD_PROPERTIES) or {}
    try:
        expected = max(0, int(props.get(FIELD_BAT_NUM) or 0))
    except (TypeError, ValueError):
        expected = 0
    packs = payload.get(PAYLOAD_BATTERY_PACKS)
    if not isinstance(packs, list):
        return expected > 0
    if expected > 0:
        return True
    return bool(packs)


def is_battery_pack_lifetime_ble_payload(body: dict[str, Any]) -> bool:
    """Return whether a BLE cmd=120 body carries pack lifetime counters."""
    if not body.get(FIELD_DEVICE_SN):
        return False
    if body.get(FIELD_IN_EGY) is None and body.get(FIELD_OUT_EGY) is None:
        return False
    dev_type = safe_int(body.get(FIELD_DEV_TYPE))
    return dev_type in {None, SUBDEVICE_DEV_TYPE_BATTERY_PACK}
