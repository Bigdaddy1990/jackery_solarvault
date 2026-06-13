"""MQTT/BLE payload normalization and list-merge helpers.

Pure functions extracted from coordinator.py (Phase 5). These handle
MQTT envelope normalization, property sanitization, battery-pack list
merging, subdevice list merging, stale-pack cleanup, and OTA metadata
merging — all without touching coordinator state.

Source: coordinator.py lines 2389-3421 (Phase 5 extraction).
"""

from datetime import UTC, datetime
from typing import Any

from ..const import (
    BATTERY_PACK_STALE_THRESHOLD_SEC,
    DEVICE_LIFETIME_COUNTER_KEYS,
    FIELD_ACTION_ID,
    FIELD_BATTERY_PACKS,
    FIELD_BODY,
    FIELD_COMM_STATE,
    FIELD_CURRENT_VERSION,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEV_ID,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_IN_EGY,
    FIELD_IS_FIRMWARE_UPGRADE,
    FIELD_MESSAGE_TYPE,
    FIELD_OUT_EGY,
    FIELD_PV1,
    FIELD_PV2,
    FIELD_PV3,
    FIELD_PV4,
    FIELD_PV_PW,
    FIELD_SN,
    FIELD_SUB_TYPE,
    FIELD_TARGET_MODULE_VERSION,
    FIELD_TARGET_VERSION,
    FIELD_UPDATE_CONTENT,
    FIELD_UPDATE_STATUS,
    FIELD_UPGRADE_TYPE,
    FIELD_VERSION,
    PACK_FIELD_LAST_SEEN_AT,
    PAYLOAD_BATTERY_PACKS,
    SUBDEVICE_ONLY_PROPERTY_KEYS,
)
from ..models.property_merge import merge_dict_values, sync_property_aliases
from ..subdevices.detector import subdevice_identity_values
from ..util import safe_float

# ---------------------------------------------------------------------------
# MQTT envelope normalization
# ---------------------------------------------------------------------------


def normalize_local_mqtt_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Wrap a body-only LAN MQTT payload into the expected cloud-MQTT envelope when necessary.

    When the payload already contains any of FIELD_BODY, FIELD_DATA, FIELD_MESSAGE_TYPE, or FIELD_ACTION_ID, the payload is returned unchanged. Otherwise the original payload is placed under FIELD_BODY and any present device identifier keys (FIELD_DEVICE_ID, FIELD_DEV_ID, FIELD_DEVICE_SN, FIELD_DEV_SN, FIELD_SN) are copied to the envelope.

    Returns:
        dict: The original payload (if already envelope-like) or a new envelope containing the payload under FIELD_BODY with copied device identifier keys when present.
    """
    if any(
        key in payload
        for key in (
            FIELD_BODY,
            FIELD_DATA,
            FIELD_MESSAGE_TYPE,
            FIELD_ACTION_ID,
    FIELD_BATTERY_PACKS,
        )
    ):
        return payload
    body = dict(payload)
    envelope: dict[str, Any] = {FIELD_BODY: body}
    for key in (
        FIELD_DEVICE_ID,
        FIELD_DEV_ID,
        FIELD_DEVICE_SN,
        FIELD_DEV_SN,
        FIELD_SN,
    ):
        if body.get(key) is not None:
            envelope[key] = body[key]
    return envelope


# ---------------------------------------------------------------------------
# Property sanitization and normalization
# ---------------------------------------------------------------------------

_MAIN_PROPERTY_ALIAS_PAIRS = (
    ("inPw", "inPower"),
    ("outPw", "outPower"),
    ("elecFreq", "frequency"),
    ("soc", "batterySoc"),
    ("batSoc", "soc"),
)


def sanitize_main_properties(props: dict[str, Any]) -> dict[str, Any]:
    """Remove accessory-only properties and normalize PV channel fields for a main device properties mapping.

    This returns a new properties dictionary with keys listed in SUBDEVICE_ONLY_PROPERTY_KEYS removed. For each PV channel key (FIELD_PV1..FIELD_PV4), if the value is a numeric scalar it is replaced by a dict containing FIELD_PV_PW set to that numeric value; existing dicts or None values for PV channels are left unchanged. Finally, main-property aliases are synchronized via _MAIN_PROPERTY_ALIAS_PAIRS before returning.

    Returns:
        dict[str, Any]: The cleaned and alias-normalized properties mapping.
    """
    clean = {
        key: value
        for key, value in dict(props).items()
        if key not in SUBDEVICE_ONLY_PROPERTY_KEYS
    }
    for channel_key in (FIELD_PV1, FIELD_PV2, FIELD_PV3, FIELD_PV4):
        channel_value = clean.get(channel_key)
        if isinstance(channel_value, dict) or channel_value is None:
            continue
        if safe_float(channel_value) is not None:
            clean[channel_key] = {FIELD_PV_PW: channel_value}
    return sync_property_aliases(clean, _MAIN_PROPERTY_ALIAS_PAIRS)


def normalize_ble_main_lifetime_counters(
    source: dict[str, Any],
) -> dict[str, Any]:
    """Convert BLE main-device lifetime energy counters from watt-hours to kilowatt-hours.

    Parameters:
        source (dict[str, Any]): Source property mapping that may contain lifetime energy counter keys in watt-hours.

    Returns:
        dict[str, Any]: A shallow copy of `source` where any keys listed in DEVICE_LIFETIME_COUNTER_KEYS that contain numeric values are converted from Wh to kWh and rounded to five decimal places; other keys are unchanged.
    """
    normalized = dict(source)
    for key in DEVICE_LIFETIME_COUNTER_KEYS:
        value = safe_float(normalized.get(key))
        if value is not None:
            normalized[key] = round(value / 1000, 5)
    return normalized


# ---------------------------------------------------------------------------
# Battery-pack list merging
# ---------------------------------------------------------------------------


def merge_battery_pack_lists(
    current: Any,  # noqa: ANN401  # loose prior-state list, duck-typed via `current or []`
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge incremental battery-pack telemetry into an existing pack list while preserving learned and static fields.

    Overlay non-null fields from up to the first five update entries onto up to the first five existing dict items, matching by device SN (FIELD_DEVICE_SN / FIELD_DEV_SN / FIELD_SN) and falling back to list position when no SN match exists. Non-dict and None entries from the prior list are ignored; the result is capped to five items. The function updates a pack's PACK_FIELD_LAST_SEEN_AT timestamp only when its commState transitions to "1".

    Returns:
        Merged list of battery pack dictionaries (up to five items).
    """
    merged: list[dict[str, Any]] = [
        dict(item) for item in current or [] if isinstance(item, dict)
    ][:5]
    index_by_sn: dict[str, int] = {}
    previous_comm_state_by_sn: dict[str, str] = {}
    previous_comm_state_by_index: dict[int, str] = {}
    for idx, item in enumerate(merged):
        sn = item.get(FIELD_DEVICE_SN) or item.get(FIELD_DEV_SN) or item.get(FIELD_SN)
        if sn:
            index_by_sn[str(sn)] = idx
            previous_comm_state_by_sn[str(sn)] = str(item.get(FIELD_COMM_STATE) or "")
        previous_comm_state_by_index[idx] = str(item.get(FIELD_COMM_STATE) or "")

    for update_idx, raw_update in enumerate(updates[:5]):
        update = {key: value for key, value in raw_update.items() if value is not None}
        sn = (
            update.get(FIELD_DEVICE_SN)
            or update.get(FIELD_DEV_SN)
            or update.get(FIELD_SN)
        )
        target_idx = index_by_sn.get(str(sn)) if sn else None
        if target_idx is None and update_idx < len(merged):
            target_idx = update_idx

        if target_idx is None:
            merged.append(dict(update))
            if sn:
                index_by_sn[str(sn)] = len(merged) - 1
        else:
            merged[target_idx] = merge_dict_values(merged[target_idx], update)
            if sn:
                index_by_sn[str(sn)] = target_idx

    # Only update the online timestamp on transitions to commState=1.
    # This avoids rewriting _last_seen_at on every incremental packet.
    now_iso = datetime.now(UTC).isoformat()
    for idx, pack in enumerate(merged):
        comm_state = str(pack.get(FIELD_COMM_STATE) or "")
        sn = pack.get(FIELD_DEVICE_SN) or pack.get(FIELD_DEV_SN) or pack.get(FIELD_SN)
        if sn:
            previous_comm_state = previous_comm_state_by_sn.get(str(sn), "")
        else:
            previous_comm_state = previous_comm_state_by_index.get(idx, "")
        if comm_state == "1" and previous_comm_state != "1":
            pack[PACK_FIELD_LAST_SEEN_AT] = now_iso

    return merged[:5]


# ---------------------------------------------------------------------------
# Subdevice list merging
# ---------------------------------------------------------------------------


def merge_subdevice_lists_by_sn(
    current: Any,  # noqa: ANN401  # loose prior-state list, duck-typed via `current or []`
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge a list of subdevice telemetry entries with incoming updates, matching by device serial number when available.

    This returns a new list of subdevice dicts produced by:
    - copying dict items from `current` (non-dict entries are ignored),
    - removing keys with `None` values from each update before applying,
    - attempting to match each update to an existing item by serial number (checked in order: `deviceSn`, `devSn`, `sn`),
    - if no serial-number match exists, falling back to the update's positional index when that index exists in the current list,
    - appending the update as a new item when neither a serial match nor a positional fallback is available,
    - overlaying update keys onto the matched item (existing keys are preserved when not present in the update).

    Parameters:
        current: Prior list-like state (may be None); only dict items are considered and copied.
        updates: Sequence of update dicts to merge; update keys with value `None` are ignored.

    Returns:
        list[dict[str, Any]]: The merged list of subdevice dictionaries.
    """
    merged: list[dict[str, Any]] = [
        dict(item) for item in current or [] if isinstance(item, dict)
    ]
    index_by_sn: dict[str, int] = {}
    for idx, item in enumerate(merged):
        sn = item.get(FIELD_DEVICE_SN) or item.get(FIELD_DEV_SN) or item.get(FIELD_SN)
        if sn:
            index_by_sn[str(sn)] = idx

    for update_idx, raw_update in enumerate(updates):
        update = {key: value for key, value in raw_update.items() if value is not None}
        sn = (
            update.get(FIELD_DEVICE_SN)
            or update.get(FIELD_DEV_SN)
            or update.get(FIELD_SN)
        )
        target_idx = index_by_sn.get(str(sn)) if sn else None
        if target_idx is None and update_idx < len(merged):
            target_idx = update_idx

        if target_idx is None:
            merged.append(dict(update))
            if sn:
                index_by_sn[str(sn)] = len(merged) - 1
        else:
            merged[target_idx] = merge_dict_values(merged[target_idx], update)
            if sn:
                index_by_sn[str(sn)] = target_idx
    return merged


def merge_subdevice_list_by_identity(
    current: Any,  # noqa: ANN401  # loose prior-state list, duck-typed via `current or []`
    update: dict[str, Any],
) -> list[dict[str, Any]]:
    """Merge Shelly Cloud accessory data by stable identity values and return an updated list.

    Builds a working copy of `current` (ignoring non-dict entries), removes keys with `None` from `update`, and computes identity values via `subdevice_identity_values`. If any existing item's identity set intersects the update's identity set, overlays the update onto that first matching item (using `merge_dict_values`) and returns the merged list. If no match is found and the cleaned update has identity values, appends the cleaned update. Non-dict entries in `current` are ignored in the resulting list.

    Parameters:
        current (Any): Prior list-like state; dict items are copied and non-dict entries are ignored.
        update (dict[str, Any]): Incoming accessory data; keys with `None` are discarded before matching.

    Returns:
        list[dict[str, Any]]: New list of subdevice dicts with the update merged into a matching identity entry or appended when no match exists.
    """
    cleaned = {key: value for key, value in update.items() if value is not None}
    merged: list[dict[str, Any]] = [
        dict(item) for item in current or [] if isinstance(item, dict)
    ]
    update_ids = subdevice_identity_values(cleaned)
    for idx, item in enumerate(merged):
        if update_ids and update_ids & subdevice_identity_values(item):
            merged[idx] = merge_dict_values(item, cleaned)
            return merged
    if cleaned and update_ids:
        merged.append(cleaned)
    return merged


def merge_smart_plug_lists(
    current: Any,  # noqa: ANN401  # loose prior-state list, duck-typed via `current or []`
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge incremental smart-plug telemetry entries using device serial numbers to align updates with existing entries.

    Parameters:
        current (Any): Prior list-like smart-plug state; may be None and may contain non-dict items — only dictionary items are considered when merging.
        updates (list[dict[str, Any]]): List of update dictionaries; update entries have `None` values removed before being merged.

    Returns:
        list[dict[str, Any]]: Merged list of smart-plug dictionaries where updates are overlaid onto existing entries matched by device serial number (`deviceSn`/`devSn`/`sn`) or, when no serial match exists, merged by positional fallback or appended.
    """
    return merge_subdevice_lists_by_sn(current, updates)


# ---------------------------------------------------------------------------
# Stale battery-pack cleanup
# ---------------------------------------------------------------------------


def drop_stale_battery_packs(
    packs: list[dict[str, Any]],
    *,
    threshold_seconds: int = BATTERY_PACK_STALE_THRESHOLD_SEC,
) -> tuple[list[dict[str, Any]], int, list[int]]:
    """Remove packs that have been silent past the stale threshold.

    Returns a tuple of ``(kept_packs, stale_count, dropped_indices)``
    where ``dropped_indices`` is the list of original positions of
    the dropped packs (used by the coordinator to build the matching
    ``device_registry`` identifiers and call ``async_remove_device``).

    Cleanup is deliberately conservative: a pack must have been
    silent for the full threshold (default 30 days) before it is
    dropped, so daily WiFi blips or manual reboots do not trigger
    spurious removal.
    """
    if not packs:
        return packs, 0, []
    now = datetime.now(UTC)
    kept: list[dict[str, Any]] = []
    stale = 0
    dropped_indices: list[int] = []
    for index, pack in enumerate(packs):
        last_seen = pack.get(PACK_FIELD_LAST_SEEN_AT)
        if not isinstance(last_seen, str):
            # No timestamp yet -- keep, the next merge will tag it.
            kept.append(pack)
            continue
        try:
            seen_at = datetime.fromisoformat(last_seen)
            if seen_at.tzinfo is None:
                seen_at = seen_at.replace(tzinfo=UTC)
        except ValueError:
            # Corrupt timestamp; keep but rewrite so future passes
            # have a clean baseline.
            fixed = dict(pack)
            fixed[PACK_FIELD_LAST_SEEN_AT] = now.isoformat()
            kept.append(fixed)
            continue
        elapsed = (now - seen_at).total_seconds()
        if elapsed > threshold_seconds:
            stale += 1
            dropped_indices.append(index)
            continue
        kept.append(pack)
    return kept, stale, dropped_indices


def resolve_device_id_from_payload(payload: dict[str, Any]) -> str | None:
    """Extract the parent device identifier from a payload slice.

    Searches top-level keys in order: "deviceId", "device_id", then "id". If none are present or valid, and the payload contains a "properties" dict, searches "deviceId" and "device_id" there. Accepts string or integer values and returns the value as a stripped string.

    Returns:
        device_id (str | None): The extracted device identifier as a stripped string if found, `None` otherwise.
    """
    for key in ("deviceId", "device_id", "id"):
        value = payload.get(key)
        if isinstance(value, str | int) and str(value).strip():
            return str(value).strip()
    props = payload.get("properties")
    if isinstance(props, dict):
        for key in ("deviceId", "device_id"):
            value = props.get(key)
            if isinstance(value, str | int) and str(value).strip():
                return str(value).strip()
    return None


# ---------------------------------------------------------------------------
# BLE lifetime counter merging
# ---------------------------------------------------------------------------


def merge_battery_pack_lifetime_from_ble(  # noqa: PLR0911, PLR0912
    updated: dict[str, Any],
    body: dict[str, Any],
) -> bool:
    """Merge lifetime energy counters from a BLE payload into the matching battery pack entry.

    Updates the `updated["batteryPacks"]` list when a pack with a matching serial number (from the payload) has its `inEgy` or `outEgy` changed, or when no matching pack exists (a minimal pack is appended containing the counters and identifying fields). Does nothing and returns `False` if the payload lacks a device serial, `batteryPacks` is not a list, or neither `inEgy` nor `outEgy` are present.

    Returns:
        `True` if `updated["batteryPacks"]` was modified (existing pack fields changed or a new minimal pack appended), `False` otherwise.
    """
    sn = body.get(FIELD_DEVICE_SN)
    if not sn:
        return False
    sn_str = str(sn).strip()
    if not sn_str:
        return False
    packs_key = FIELD_BATTERY_PACKS
    packs = updated.get(packs_key)
    if not isinstance(packs, list):
        packs_key = PAYLOAD_BATTERY_PACKS
        packs = updated.get(packs_key)
    if not isinstance(packs, list):
        return False
    in_egy = body.get(FIELD_IN_EGY)
    out_egy = body.get(FIELD_OUT_EGY)
    if in_egy is None and out_egy is None:
        return False
    # Match by deviceSn. Pack lists are short (<=5 packs) so a
    # linear scan is fine.
    touched = False
    matched = False
    merged_packs: list[Any] = []
    for pack in packs:
        if not isinstance(pack, dict):
            merged_packs.append(pack)
            continue
        pack_sn_raw = (
            pack.get(FIELD_DEVICE_SN) or pack.get(FIELD_DEV_SN) or pack.get(FIELD_SN)
        )
        pack_sn = str(pack_sn_raw).strip() if pack_sn_raw is not None else None
        if pack_sn != sn_str:
            merged_packs.append(pack)
            continue
        matched = True
        new_pack = dict(pack)
        if in_egy is not None and new_pack.get(FIELD_IN_EGY) != in_egy:
            new_pack[FIELD_IN_EGY] = in_egy
            touched = True
        if out_egy is not None and new_pack.get(FIELD_OUT_EGY) != out_egy:
            new_pack[FIELD_OUT_EGY] = out_egy
            touched = True
        merged_packs.append(new_pack)
    if touched:
        updated[packs_key] = merged_packs
        return True
    if matched:
        return False

    # BLE can report lifetime counters for packs that are not yet
    # present in the MQTT/HTTP pack list. Create a minimal pack so
    # lifetime entities do not stay unrouted forever.
    minimal_pack: dict[str, Any] = {
        FIELD_DEVICE_SN: sn_str,
        FIELD_DEV_TYPE: body.get(FIELD_DEV_TYPE),
        FIELD_SUB_TYPE: body.get(FIELD_SUB_TYPE),
        PACK_FIELD_LAST_SEEN_AT: datetime.now(UTC).isoformat(),
    }
    if in_egy is not None:
        minimal_pack[FIELD_IN_EGY] = in_egy
    if out_egy is not None:
        minimal_pack[FIELD_OUT_EGY] = out_egy
    merged_packs.append(minimal_pack)
    updated[packs_key] = merged_packs
    return True


# ---------------------------------------------------------------------------
# OTA metadata merging
# ---------------------------------------------------------------------------


def merge_pack_ota(pack: dict[str, Any], ota: dict[str, Any]) -> None:
    """Merge OTA metadata into a battery pack dictionary in place.

    Copies the OTA version (from `currentVersion` or `version`) into both `version` and `currentVersion` on the pack. For each OTA key (isFirmwareUpgrade, targetVersion, targetModuleVersion, updateStatus, updateContent, upgradeType), if the key exists in `ota` and its value is not None, writes that key/value into `pack`.

    Parameters:
        pack (dict[str, Any]): Battery pack object to update in-place.
        ota (dict[str, Any]): OTA metadata source whose fields will be merged into `pack`.
    """
    current_version = ota.get(FIELD_CURRENT_VERSION) or ota.get(FIELD_VERSION)
    if current_version is not None:
        pack[FIELD_VERSION] = current_version
        pack[FIELD_CURRENT_VERSION] = current_version
    for key in (
        FIELD_IS_FIRMWARE_UPGRADE,
        FIELD_TARGET_VERSION,
        FIELD_TARGET_MODULE_VERSION,
        FIELD_UPDATE_STATUS,
        FIELD_UPDATE_CONTENT,
        FIELD_UPGRADE_TYPE,
    ):
        if key in ota and ota.get(key) is not None:
            pack[key] = ota.get(key)


def merge_battery_pack_ota_lists(
    current: Any,  # noqa: ANN401  # loose prior-state list, duck-typed via `current or []`
    ota_updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge OTA metadata into an existing battery-pack list by matching serial numbers and return an updated list capped to five entries.

    The function copies up to the first five dict items from `current`, then overlays OTA-related fields from `ota_updates` onto matching packs. Matching prefers serial-number keys (`deviceSn`, `devSn`, `sn`) and falls back to the update's position when no SN match exists. Only OTA keys that are present in an update and not `None` are applied. The function does not modify last-seen timestamps and always returns at most five pack dicts.

    Parameters:
        current (Any): Prior pack list (may be None or a heterogeneous sequence); only dict items are considered.
        ota_updates (list[dict[str, Any]]): Sequence of OTA update dicts; each may include serial-number keys and OTA fields.

    Returns:
        list[dict[str, Any]]: Updated list of battery pack dicts (maximum length 5) with OTA fields merged where applicable.
    """
    merged: list[dict[str, Any]] = [
        dict(item) for item in current or [] if isinstance(item, dict)
    ][:5]
    index_by_sn: dict[str, int] = {}
    for idx, item in enumerate(merged):
        sn = item.get(FIELD_DEVICE_SN) or item.get(FIELD_DEV_SN) or item.get(FIELD_SN)
        if sn:
            index_by_sn[str(sn)] = idx

    ota_keys = (
        FIELD_VERSION,
        FIELD_CURRENT_VERSION,
        FIELD_IS_FIRMWARE_UPGRADE,
        FIELD_TARGET_VERSION,
        FIELD_TARGET_MODULE_VERSION,
        FIELD_UPDATE_STATUS,
        FIELD_UPDATE_CONTENT,
        FIELD_UPGRADE_TYPE,
    )
    for update_idx, raw_update in enumerate(ota_updates[:5]):
        sn = (
            raw_update.get(FIELD_DEVICE_SN)
            or raw_update.get(FIELD_DEV_SN)
            or raw_update.get(FIELD_SN)
        )
        target_idx = index_by_sn.get(str(sn)) if sn else None
        if target_idx is None and update_idx < len(merged):
            target_idx = update_idx
        if target_idx is None:
            continue
        for key in ota_keys:
            if key in raw_update and raw_update.get(key) is not None:
                merged[target_idx][key] = raw_update.get(key)
    return merged[:5]


# ---------------------------------------------------------------------------
# Misc pure helpers
# ---------------------------------------------------------------------------


def app_period_section(prefix: str, date_type: str) -> str:
    """Builds a normalized key for an app period section by joining prefix and date_type with an underscore.

    Returns:
        section_key (str): The combined key in the form "<prefix>_<date_type>".
    """
    return f"{prefix}_{date_type}"
