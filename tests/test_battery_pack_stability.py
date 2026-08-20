"""Battery-pack discovery + lifecycle tests.

Locks down the contract:

1. Online packs (commState=1) get tagged with PACK_FIELD_LAST_SEEN_AT
   on every merge.
2. Brief offline blips (<7 days) keep the pack in the list.
3. Permanently-removed packs (>7 days silent) are removed by
   ``_drop_stale_battery_packs``, freeing HA's device registry.
4. Pure unit-test coverage of the cleanup helper without HA fixtures.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re

from custom_components.jackery_solarvault.const import FIELD_BAT_NUM, PAYLOAD_PROPERTIES
from custom_components.jackery_solarvault.coordinator import battery_packs_need_query

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "jackery_solarvault"
PackRow = dict[str, object]


def _read(name: str) -> str:
    """Read a UTF-8 text file from the integration component directory.

    Parameters:
        name (str): Relative filename located inside the integration component directory.

    Returns:
        file_text (str): The file contents decoded as UTF-8.
    """  # noqa: E501, RUF105
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
        re.MULTILINE,
    )
    assert match is not None
    expr = match.group(1).strip()
    # Evaluate the literal expression (e.g. "7 * 24 * 3600")
    value = eval(expr, {"__builtins__": {}}, {})  # ruff: ignore[suspicious-eval-usage]
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

    def drop(packs: Sequence[PackRow]) -> tuple[list[PackRow], int]:
        """Filter packs by their `_last_seen_at` ISO 8601 timestamp, dropping those older than the configured threshold.

        Parameters:
            packs (Iterable[dict]): Sequence of pack dictionaries. Each pack may include an `_last_seen_at` value (ISO-formatted string). Packs with a missing, non-string, or unparsable `_last_seen_at` are retained.

        Returns:
            tuple[list[dict], int]: A pair (kept_packs, stale_count) where `kept_packs` is the list of packs retained and `stale_count` is the number of packs considered stale and dropped.
        """  # noqa: E501, RUF105
        kept: list[PackRow] = []
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

    packs: list[PackRow] = [
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
    pack: PackRow = {
        "deviceSn": "blip",
        "commState": "0",  # currently offline
        "_last_seen_at": (now - timedelta(hours=4)).isoformat(),
    }

    def drop(packs: Sequence[PackRow]) -> list[PackRow]:
        """Filter out battery packs whose `_last_seen_at` timestamp is older than the configured threshold.

        Parameters:
            packs (list[dict]): Iterable of battery-pack dictionaries to evaluate.

        Returns:
            list: The subset of `packs` retained — packs that do not have a string `_last_seen_at`, have an unparsable `_last_seen_at`, or whose parsed `_last_seen_at` is within `threshold_seconds` of `now`.
        """  # noqa: E501, RUF105
        kept: list[PackRow] = []
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


def test_battery_pack_query_need_accepts_text_count_and_rejects_bad_values() -> None:
    """BatNum is cloud payload data and must be parsed defensively.

    Text counts like "2.0" mean packs are expected; a bool or NaN batNum
    is a schema error and must not be treated as a pack count.
    """
    assert battery_packs_need_query({PAYLOAD_PROPERTIES: {FIELD_BAT_NUM: "2.0"}})
    assert not battery_packs_need_query({PAYLOAD_PROPERTIES: {FIELD_BAT_NUM: True}})
    assert not battery_packs_need_query({
        PAYLOAD_PROPERTIES: {FIELD_BAT_NUM: float("nan")}
    })
    rejections: list[str] = []
    assert not battery_packs_need_query(
        {PAYLOAD_PROPERTIES: {FIELD_BAT_NUM: True}},
        rejection_callback=rejections.append,
    )
    assert rejections == ["battery_pack_bat_num_type_error"]


def test_pack_ota_fetch_is_background_not_coordinator_blocking() -> None:
    """Slow pack OTA lookups must not block the 30s coordinator poll.

    Add-on battery live data is MQTT-first. The
    OTA endpoint only enriches firmware metadata and must therefore run in
    the background with cached values merged into the poll result.
    """
    src = _read("coordinator.py")

    assert "_battery_pack_ota_tasks" in src, src
    assert "def _schedule_battery_pack_ota_enrichment" in src, src
    assert "async def _async_refresh_battery_pack_ota" in src, src
    assert "def _merge_battery_pack_ota_lists" in src, src

    update_match = re.search(
        r"async def _async_update_data\(.*?return result",
        src,
        re.DOTALL,
    )
    assert update_match is not None
    update_body = update_match.group(0)
    assert "fetch_missing=False" in update_body, update_body
    assert "_schedule_battery_pack_ota_enrichment(dev_id)" in update_body, update_body

    handler_match = re.search(
        r"async def _async_handle_mqtt_message\(.*?(?=\n    def _resolve_device_id_from_mqtt)",  # noqa: E501, RUF105
        src,
        re.DOTALL,
    )
    assert handler_match is not None
    handler_body = handler_match.group(0)
    push_call = "self._push_partial_update("
    schedule_call = "_schedule_battery_pack_ota_enrichment(device_id)"
    assert push_call in handler_body, handler_body
    assert schedule_call in handler_body, handler_body
    assert handler_body.index(push_call) < handler_body.index(schedule_call)

    refresh_match = re.search(
        r"async def _async_refresh_battery_pack_ota\(.*?(?=\n    @staticmethod)",
        src,
        re.DOTALL,
    )
    assert refresh_match is not None
    refresh_body = refresh_match.group(0)
    assert "_merge_battery_pack_ota_lists" in refresh_body, refresh_body
    assert "_merge_battery_pack_lists" not in refresh_body, refresh_body
