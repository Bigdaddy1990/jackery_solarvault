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
    """Normalize body-only LAN MQTT payloads into the cloud-MQTT envelope."""
    if any(
        key in payload
        for key in (
            FIELD_BODY,
            FIELD_DATA,
            FIELD_MESSAGE_TYPE,
            FIELD_ACTION_ID,
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
    """Remove accessory-only fields from main device properties."""
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
    """Convert BLE main-device energy counters from Wh wire units to kWh."""
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
    """Merge incremental pack telemetry without dropping static fields.

    Jackery's MQTT sub-device packets often contain only inPw/outPw plus
    deviceSn. Replacing the full pack list with those packets removes
    fields learned from HTTP/OTA (version, SOC, temperature). Keep known
    fields and overlay the latest non-null telemetry by SN, falling back
    to list position.
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
    """Merge generic subdevice telemetry by ``deviceSn`` when available."""
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
    """Merge Shelly Cloud accessory data by stable ids, never by index."""
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
    """Merge incremental smart-plug telemetry by ``deviceSn``.

    Mirrors the battery-pack merge contract but without the 5-pack cap
    and without stale-eviction (plug presence is driven by the system
    accessories list, not by silence).
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
    """Pick the parent device id from a coordinator payload slice.

    Used by the stale-pack cleanup to construct the ``device_registry``
    identifier. The coordinator data is keyed by ``device_id`` at the
    top level, but nested payload slices passed into the merge step
    do not carry that key. Best-effort fallback: read ``deviceId``,
    ``device_id`` or ``id`` from the merged props.
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


def merge_battery_pack_lifetime_from_ble(
    updated: dict[str, Any],
    body: dict[str, Any],
) -> bool:
    """Merge BLE-sourced lifetime ``inEgy``/``outEgy`` into a battery pack.

    BLE ``cmd=120`` for ``devType=1`` carries lifetime cumulative
    energy counters per pack. Values are in Wh-int (BLE wire format).
    HTTP ``/v1/device/battery/pack/list`` returns ``data: null`` for
    SolarVault, so BLE is the only source for these per-pack
    lifetime counters. Returns ``True`` when a matching pack was found
    and updated, ``False`` otherwise.

    We deliberately do NOT create a new pack entry from BLE alone:
    the pack list authority remains the MQTT
    ``UploadSubDeviceGroupProperty`` actionId=3014 stream.
    """
    sn = body.get(FIELD_DEVICE_SN)
    if not sn:
        return False
    packs = updated.get("batteryPacks")
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
        pack_sn = (
            pack.get(FIELD_DEVICE_SN) or pack.get(FIELD_DEV_SN) or pack.get(FIELD_SN)
        )
        if pack_sn != sn:
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
        updated["batteryPacks"] = merged_packs
        return True
    if matched:
        return False

    # BLE can report lifetime counters for packs that are not yet
    # present in the MQTT/HTTP pack list. Create a minimal pack so
    # lifetime entities do not stay unrouted forever.
    minimal_pack: dict[str, Any] = {
        FIELD_DEVICE_SN: sn,
        FIELD_DEV_TYPE: body.get(FIELD_DEV_TYPE),
        FIELD_SUB_TYPE: body.get(FIELD_SUB_TYPE),
        PACK_FIELD_LAST_SEEN_AT: datetime.now(UTC).isoformat(),
    }
    if in_egy is not None:
        minimal_pack[FIELD_IN_EGY] = in_egy
    if out_egy is not None:
        minimal_pack[FIELD_OUT_EGY] = out_egy
    merged_packs.append(minimal_pack)
    updated["batteryPacks"] = merged_packs
    return True


# ---------------------------------------------------------------------------
# OTA metadata merging
# ---------------------------------------------------------------------------


def merge_pack_ota(pack: dict[str, Any], ota: dict[str, Any]) -> None:
    """Merge OTA metadata fields into a battery pack dict in-place."""
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
    """Merge static OTA fields into packs without touching last-seen state."""
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
    """Return the normalized payload key for documented app period sections."""
    return f"{prefix}_{date_type}"
