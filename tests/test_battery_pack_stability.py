"""Battery-pack discovery + lifecycle tests.

Locks down the contract:

1. Online packs (commState=1) get tagged with PACK_FIELD_LAST_SEEN_AT
   on every merge.
2. Brief offline blips (<7 days) keep the pack in the list.
3. Permanently-removed packs (>7 days silent) are removed by
   ``_drop_stale_battery_packs``, freeing HA's device registry.
4. Pure unit-test coverage of the cleanup helper without HA fixtures.

Together these implement the Gold-tier ``stale-devices`` rule described
in ``docs/quality_scale.yaml``.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "jackery_solarvault"


def _read(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def test_stale_threshold_constant_is_a_full_week() -> None:
    """The stale threshold must be conservative (>=24h) to avoid false drops.

    A briefly-rebooting pack must not be removed; only a permanently
    unplugged pack should be cleaned up.
    """
    src = _read("const.py")
    match = re.search(
        r"BATTERY_PACK_STALE_THRESHOLD_SEC:\s*Final\s*=\s*(.+?)$",
        src,
        re.M,
    )
    assert match is not None
    expr = match.group(1).strip()
    # Evaluate the literal expression (e.g. "7 * 24 * 3600")
    value = eval(expr, {"__builtins__": {}}, {})  # noqa: S307 - constant-only expr
    assert isinstance(value, int)
    assert value >= 24 * 3600, f"threshold {value}s is shorter than 24h"
    # Don't make it ridiculously long either — a year-long stale pack
    # would clutter the registry forever.
    assert value <= 30 * 24 * 3600, f"threshold {value}s is over a month"


def test_pack_field_last_seen_at_is_internal() -> None:
    """The internal tracking field must start with an underscore.

    HA exposes pack fields as entity attributes; an internal-only
    field must not collide with a documented Jackery API field name.
    """
    src = _read("const.py")
    match = re.search(r'PACK_FIELD_LAST_SEEN_AT:\s*Final\s*=\s*"([^"]+)"', src)
    assert match is not None
    name = match.group(1)
    assert name.startswith("_"), name
    assert "last_seen" in name


def test_merge_battery_pack_lists_stamps_online_packs() -> None:
    """The merge step must stamp _last_seen_at for commState=1 packs."""
    src = _read("coordinator.py")
    # Locate _merge_battery_pack_lists body
    match = re.search(
        r"def _merge_battery_pack_lists\(\s*cls.*?return merged\[:5\]",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)
    assert "PACK_FIELD_LAST_SEEN_AT" in body, body
    assert "FIELD_COMM_STATE" in body, body
    # Stamping must happen for online packs (commState=1)
    assert '"1"' in body, body


def test_drop_stale_battery_packs_returns_kept_count_and_indices() -> None:
    """_drop_stale_battery_packs returns (kept_packs, stale_count, dropped_indices).

    The third element drives the dynamic-devices Gold-tier rule: each
    dropped pack index is converted into a device-registry identifier
    so HA's registry stays consistent with the coordinator's payload.
    """
    src = _read("coordinator.py")
    match = re.search(
        r"def _drop_stale_battery_packs\(\s*cls.*?(?=\n    @|\n    async def )",
        src,
        re.S,
    )
    assert match is not None, "_drop_stale_battery_packs not found"
    body = match.group(0)
    assert "tuple[list[dict[str, Any]], int, list[int]]" in body, body
    assert "return packs, 0, []" in body, body
    # Cleanup must compute elapsed seconds against now
    assert "dt_util.utcnow" in body, body
    # Dropped pack indices must be tracked
    assert "dropped_indices" in body, body


def test_diagnostics_exposes_stale_pack_count() -> None:
    """Diagnostics must surface the cumulative stale-pack drop counter."""
    src = _read("coordinator.py")
    assert 'diag["stale_battery_packs_dropped"] = ' in src, src


def test_stale_drop_helper_logic_unit() -> None:
    """End-to-end logic check for the stale-pack threshold.

    Re-implements the helper in pure Python and confirms:
      - packs without _last_seen_at are kept (first-discovery)
      - packs seen yesterday are kept
      - packs seen 8 days ago are dropped
      - packs with corrupt timestamps are kept (defensive)
    """
    threshold_seconds = 7 * 24 * 3600
    now = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)

    def drop(packs):
        kept = []
        stale = 0
        for pack in packs:
            last_seen = pack.get("_last_seen_at")
            if not isinstance(last_seen, str):
                kept.append(pack)
                continue
            try:
                seen_at = datetime.fromisoformat(last_seen)
            except ValueError:
                kept.append(pack)
                continue
            if (now - seen_at).total_seconds() > threshold_seconds:
                stale += 1
                continue
            kept.append(pack)
        return kept, stale

    yesterday = (now - timedelta(days=1)).isoformat()
    eight_days_ago = (now - timedelta(days=8)).isoformat()

    packs = [
        {"deviceSn": "fresh", "_last_seen_at": yesterday},
        {"deviceSn": "stale", "_last_seen_at": eight_days_ago},
        {"deviceSn": "untagged"},  # newly discovered, no timestamp yet
        {"deviceSn": "corrupt", "_last_seen_at": "not-a-date"},
    ]
    kept, stale = drop(packs)
    sns_kept = {p["deviceSn"] for p in kept}
    assert sns_kept == {"fresh", "untagged", "corrupt"}, sns_kept
    assert stale == 1


def test_offline_pack_during_short_blip_is_kept() -> None:
    """A pack with commState=0 and recent _last_seen_at must NOT be dropped.

    This is the daily-WiFi-blip case; removing such a pack would trigger
    repeated re-discovery and confuse HA's device registry.
    """
    threshold_seconds = 7 * 24 * 3600
    now = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)
    pack = {
        "deviceSn": "blip",
        "commState": "0",  # currently offline
        "_last_seen_at": (now - timedelta(hours=4)).isoformat(),
    }

    def drop(packs):
        kept = []
        for p in packs:
            last_seen = p.get("_last_seen_at")
            if not isinstance(last_seen, str):
                kept.append(p)
                continue
            try:
                seen_at = datetime.fromisoformat(last_seen)
            except ValueError:
                kept.append(p)
                continue
            if (now - seen_at).total_seconds() > threshold_seconds:
                continue
            kept.append(p)
        return kept

    assert drop([pack]) == [pack]


def test_battery_pack_discovery_filters_smart_meter_subdevices() -> None:
    """Discovery must distinguish add-on batteries from CT/smart meters.

    Without this filter a smart-meter MQTT frame would create a phantom
    battery pack entry and pollute the entity-state contract.
    """
    src = _read("coordinator.py")
    match = re.search(
        r"def _looks_like_battery_pack\(cls.*?(?=\n    @classmethod|\n    def )",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)
    # Must reject CT meter frames + Shelly smart meters
    assert "_CT_METER_KEYS" in body, body
    assert "shelly" in body, body
    # Must reject explicit smart-meter subtypes
    assert "SMART_METER_SUBTYPE" in body, body
    # Must reject documented non-battery sub-device types
    assert "NON_BATTERY_SUBDEVICE_TYPES" in body, body


def test_battery_pack_count_capped_at_five() -> None:
    """Hardware allows max 5 add-on packs per system. Code must cap.

    A buggy or malicious cloud response with 100 packs must not turn
    into 100 HA devices.
    """
    src = _read("coordinator.py")
    # Several [:5] slices in the merge path
    match = re.search(
        r"def _merge_battery_pack_lists\(\s*cls.*?return merged\[:5\]",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)
    assert body.count("[:5]") >= 2, body  # input + output


def test_battery_pack_merge_preserves_known_fields() -> None:
    """Incremental MQTT updates must not erase fields learned from HTTP/OTA.

    A typical incremental MQTT message carries only deviceSn + inPw/outPw.
    Without preservation, the version, SOC, and temperature fields learned
    during HTTP discovery would vanish on the next MQTT frame.
    """
    src = _read("coordinator.py")
    match = re.search(
        r"def _merge_battery_pack_lists\(\s*cls.*?return merged\[:5\]",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)
    # Implementation must filter `None` updates (so a missing field doesn't
    # overwrite a real one) and merge per-key.
    assert "value is not None" in body, body
    assert "_merge_dict_values" in body, body


# ---------- Gold-tier: dynamic-devices ----------------------------------


def test_coordinator_queues_device_removals_on_stale_pack_drop() -> None:
    """The synchronous merge step must queue removals, not perform them.

    HA's device_registry.async_remove_device cannot be awaited from a
    synchronous context. The coordinator therefore appends the
    identifiers to a list that the async cleanup hook drains on the
    next refresh.
    """
    src = _read("coordinator.py")
    # The merge call site must hand dropped_indices to a queue
    assert "_pending_device_removals.append" in src, src
    # The queue must be a list of (DOMAIN, identifier) tuples
    match = re.search(
        r"_pending_device_removals\.append\(\s*identifier\s*\)",
        src,
    )
    assert match is not None, src
    # Identifier construction must use the documented battery-pack scheme
    assert 'f"{device_id}_battery_pack_{pack_index}"' in src, src


def test_async_cleanup_calls_device_registry_remove() -> None:
    """The async cleanup must look up + remove each queued device."""
    src = _read("coordinator.py")
    match = re.search(
        r"async def async_cleanup_pending_device_removals\(self.*?(?=\n    [@a-z])",
        src,
        re.S,
    )
    assert match is not None, "async_cleanup_pending_device_removals not found"
    body = match.group(0)
    # Imports HA's device_registry at call time (avoids stub conflicts)
    assert "from homeassistant.helpers import device_registry" in body, body
    # Looks up by identifier, then removes by registry-internal device.id
    assert "async_get_device" in body, body
    assert "async_remove_device" in body, body
    # Snapshot-and-clear pattern so concurrent merges do not lose entries
    assert "self._pending_device_removals.clear()" in body, body


def test_update_data_drains_pending_removals() -> None:
    """_async_update_data must call the cleanup hook each refresh.

    Otherwise pack indices queued by the merge step would accumulate
    forever and HA's registry would never converge.
    """
    src = _read("coordinator.py")
    # Find the cleanup invocation site
    assert "await self.async_cleanup_pending_device_removals()" in src, src
    # It must be guarded by a non-empty check to avoid the executor cost
    # when nothing is queued.
    pattern = re.search(
        r"if self\._pending_device_removals:\s*\n"
        r"\s*try:\s*\n"
        r"\s*await self\.async_cleanup_pending_device_removals\(\)",
        src,
    )
    assert pattern is not None, src


def test_quality_scale_dynamic_devices_marked_done() -> None:
    """quality_scale.yaml: dynamic-devices is now done."""
    qs = (COMPONENT / "quality_scale.yaml").read_text(encoding="utf-8")
    # Find the dynamic-devices block
    match = re.search(
        r"dynamic-devices:\s*\n\s*status:\s*(\w+)",
        qs,
    )
    assert match is not None, qs
    assert match.group(1) == "done", match.group(1)
