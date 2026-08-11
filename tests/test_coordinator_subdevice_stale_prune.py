"""Regression tests for conservative non-battery accessory cleanup.

Partial MQTT/BLE/shadow updates prove presence only for identities they
contain. Removal therefore requires both a stale timestamp and absence from
the complete HTTP system-list discovery; silence by itself is never enough.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault import coordinator as coordinator_mod
from custom_components.jackery_solarvault.const import (
    BATTERY_PACK_STALE_THRESHOLD_SEC,
    DOMAIN,
    FIELD_ACCESSORIES,
    FIELD_DEVICE_SN,
    FIELD_PLUGS,
    FIELD_SUB_DEVICE,
    PACK_FIELD_LAST_SEEN_AT,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_SUBDEVICES,
    PAYLOAD_SYSTEM,
    SUBDEVICE_FIELD_LAST_SEEN_AT,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    drop_stale_subdevices,
    stamp_subdevice_last_seen,
    subdevice_serial,
)
from custom_components.jackery_solarvault.util import stable_subdevice_key
from tests._update_cycle_fixture import (  # ruff:ignore[banned-api]
    DEVICE_ID,
    setup_update_cycle_coordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_THRESHOLD_SEC = 100
_FRESH_SN = "plug-fresh"
_STALE_SN = "plug-stale"
_OTHER_SN = "plug-other"
_STALE_PACK_SN = "A-stale-pack"
_SURVIVOR_PACK_SN = "B-live-pack"
_FROZEN_PACK_SN = "frozen-registry-pack"


def _iso(seconds_ago: int) -> str:
    """Return an ISO timestamp ``seconds_ago`` seconds in the past."""
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


def _pack_coordinator() -> JackerySolarVaultCoordinator:
    """Build a real coordinator shell for stale-pack and reload policy."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    shell = cast("Any", coordinator)
    shell._pending_device_removals = []  # ruff: ignore[private-member-access]
    shell._pending_battery_pack_removal_serials = {}  # ruff: ignore[private-member-access]
    shell._battery_pack_identity_overrides = {}  # ruff: ignore[private-member-access]
    shell._stale_battery_packs_dropped = 0  # ruff: ignore[private-member-access]
    shell._battery_pack_topology_reload_required = False  # ruff: ignore[private-member-access]
    return coordinator


def _pack_identifier(serial: str, index: int) -> tuple[str, str]:
    """Return the registry identifier queued by stale-pack cleanup."""
    key = stable_subdevice_key("battery_pack", serial, index)
    return DOMAIN, f"{DEVICE_ID}_{key}"


# ---------------------------------------------------------------------------
# stamp_subdevice_last_seen
# ---------------------------------------------------------------------------


def test_stamp_only_touches_entries_mentioned_in_this_update() -> None:
    """Only serials present in ``touched_serials`` get a fresh timestamp."""
    merged: list[dict[str, Any]] = [
        {FIELD_DEVICE_SN: _FRESH_SN},
        {FIELD_DEVICE_SN: _OTHER_SN},
    ]

    result = stamp_subdevice_last_seen(merged, {_FRESH_SN}, subdevice_serial)

    assert isinstance(result[0][SUBDEVICE_FIELD_LAST_SEEN_AT], str)
    assert SUBDEVICE_FIELD_LAST_SEEN_AT not in result[1]


def test_stamp_refreshes_an_existing_timestamp() -> None:
    """A repeated touch bumps the timestamp forward instead of leaving it."""
    old_ts = _iso(500)
    merged = [{FIELD_DEVICE_SN: _FRESH_SN, SUBDEVICE_FIELD_LAST_SEEN_AT: old_ts}]

    result = stamp_subdevice_last_seen(merged, {_FRESH_SN}, subdevice_serial)

    assert result[0][SUBDEVICE_FIELD_LAST_SEEN_AT] != old_ts


def test_stamp_is_a_noop_when_nothing_was_touched() -> None:
    """An empty touched-serials set must not add a timestamp to anything."""
    merged = [{FIELD_DEVICE_SN: _FRESH_SN}]

    result = stamp_subdevice_last_seen(merged, set(), subdevice_serial)

    assert SUBDEVICE_FIELD_LAST_SEEN_AT not in result[0]


# ---------------------------------------------------------------------------
# drop_stale_subdevices
# ---------------------------------------------------------------------------


def test_drop_stale_keeps_entries_within_the_threshold() -> None:
    """An entry seen recently must not be pruned."""
    items = [{FIELD_DEVICE_SN: _FRESH_SN, SUBDEVICE_FIELD_LAST_SEEN_AT: _iso(1)}]

    kept, stale_count, dropped_indices = drop_stale_subdevices(
        items,
        confirmed_absent=lambda _item: True,
        threshold_seconds=_THRESHOLD_SEC,
    )

    assert kept == items
    assert stale_count == 0
    assert dropped_indices == []


def test_drop_stale_removes_entries_silent_past_the_threshold() -> None:
    """An entry silent longer than the threshold must be pruned."""
    items = [
        {
            FIELD_DEVICE_SN: _STALE_SN,
            SUBDEVICE_FIELD_LAST_SEEN_AT: _iso(_THRESHOLD_SEC + 1),
        },
    ]

    kept, stale_count, dropped_indices = drop_stale_subdevices(
        items,
        confirmed_absent=lambda _item: True,
        threshold_seconds=_THRESHOLD_SEC,
    )

    assert kept == []
    assert stale_count == 1
    assert dropped_indices == [0]


def test_drop_stale_establishes_baseline_for_unstamped_entry() -> None:
    """An unstamped entry is kept and receives a future expiry baseline."""
    items = [{FIELD_DEVICE_SN: _FRESH_SN}]

    kept, stale_count, _dropped = drop_stale_subdevices(
        items,
        confirmed_absent=lambda _item: True,
        threshold_seconds=_THRESHOLD_SEC,
    )

    assert kept[0][FIELD_DEVICE_SN] == _FRESH_SN
    assert isinstance(kept[0][SUBDEVICE_FIELD_LAST_SEEN_AT], str)
    assert stale_count == 0


def test_drop_stale_keeps_silent_entry_without_discovery_confirmation() -> None:
    """A stale timestamp alone must never remove a partially observed device."""
    items = [
        {
            FIELD_DEVICE_SN: _STALE_SN,
            SUBDEVICE_FIELD_LAST_SEEN_AT: _iso(_THRESHOLD_SEC + 1),
        },
    ]

    kept, stale_count, dropped_indices = drop_stale_subdevices(
        items,
        confirmed_absent=lambda _item: False,
        threshold_seconds=_THRESHOLD_SEC,
    )

    assert kept == items
    assert stale_count == 0
    assert dropped_indices == []


def test_drop_stale_returns_empty_list_unchanged() -> None:
    """An empty bucket is a no-op."""
    assert drop_stale_subdevices(
        [],
        confirmed_absent=lambda _item: True,
    ) == ([], 0, [])


# ---------------------------------------------------------------------------
# battery-pack stale identity and topology reload
# ---------------------------------------------------------------------------


def test_stale_pack_removal_uses_frozen_registry_identity() -> None:
    """Cleanup queues the frozen registry key instead of a conflicting live SN."""
    coordinator = _pack_coordinator()
    coordinator.set_battery_pack_identity_override(
        DEVICE_ID,
        1,
        _FROZEN_PACK_SN,
    )
    updated: dict[str, Any] = {
        PAYLOAD_BATTERY_PACKS: [
            {
                FIELD_DEVICE_SN: _STALE_PACK_SN,
                PACK_FIELD_LAST_SEEN_AT: _iso(
                    BATTERY_PACK_STALE_THRESHOLD_SEC + 1,
                ),
            },
        ],
    }

    stale_count = coordinator._prune_stale_battery_pack_bucket(  # ruff: ignore[private-member-access]
        updated,
        device_id=DEVICE_ID,
    )

    frozen_identifier = _pack_identifier(_FROZEN_PACK_SN, 1)
    assert stale_count == 1
    assert updated[PAYLOAD_BATTERY_PACKS] == []
    assert coordinator._pending_device_removals == [frozen_identifier]  # ruff: ignore[private-member-access]
    assert coordinator._pending_battery_pack_removal_serials == {  # ruff: ignore[private-member-access]
        frozen_identifier: _FROZEN_PACK_SN,
    }
    assert coordinator._battery_pack_topology_reload_required is True  # ruff: ignore[private-member-access]


def test_serial_identified_survivor_is_not_queued_after_earlier_drop() -> None:
    """Dropping pack one never late-deletes a serial-backed surviving pack."""
    coordinator = _pack_coordinator()
    updated: dict[str, Any] = {
        PAYLOAD_BATTERY_PACKS: [
            {
                FIELD_DEVICE_SN: _STALE_PACK_SN,
                PACK_FIELD_LAST_SEEN_AT: _iso(
                    BATTERY_PACK_STALE_THRESHOLD_SEC + 1,
                ),
            },
            {
                FIELD_DEVICE_SN: _SURVIVOR_PACK_SN,
                PACK_FIELD_LAST_SEEN_AT: _iso(1),
            },
        ],
    }

    stale_count = coordinator._prune_stale_battery_pack_bucket(  # ruff: ignore[private-member-access]
        updated,
        device_id=DEVICE_ID,
    )

    stale_identifier = _pack_identifier(_STALE_PACK_SN, 1)
    survivor_identifier = _pack_identifier(_SURVIVOR_PACK_SN, 2)
    assert stale_count == 1
    assert [pack[FIELD_DEVICE_SN] for pack in updated[PAYLOAD_BATTERY_PACKS]] == [
        _SURVIVOR_PACK_SN
    ]
    assert coordinator._pending_device_removals == [stale_identifier]  # ruff: ignore[private-member-access]
    assert survivor_identifier not in coordinator._pending_device_removals  # ruff: ignore[private-member-access]


async def test_topology_reload_transfers_removals_not_position_overrides(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reload hands off cleanup queues but clears positional session identity."""
    source = _pack_coordinator()
    replacement = _pack_coordinator()
    identifier = _pack_identifier(_FROZEN_PACK_SN, 1)
    source._pending_device_removals.append(identifier)  # ruff: ignore[private-member-access]
    source._pending_battery_pack_removal_serials[identifier] = _FROZEN_PACK_SN  # ruff: ignore[private-member-access]
    source.set_battery_pack_identity_override(
        DEVICE_ID,
        1,
        _FROZEN_PACK_SN,
    )
    source._battery_pack_topology_reload_required = True  # ruff: ignore[private-member-access]
    source._shutdown_started = False  # ruff: ignore[private-member-access]
    source._topology_reload_task = None  # ruff: ignore[private-member-access]
    entry = SimpleNamespace(entry_id="entry-1", runtime_data=source)
    source.entry = entry
    source.hass = hass

    original_asyncio = coordinator_mod.asyncio

    async def _sleep_without_delay(_delay: float) -> None:
        await original_asyncio.sleep(0)

    asyncio_proxy = SimpleNamespace(
        sleep=_sleep_without_delay,
        CancelledError=original_asyncio.CancelledError,
    )

    def _reload(_entry_id: str) -> bool:
        entry.runtime_data = replacement
        return True

    reload_mock = AsyncMock(side_effect=_reload)
    monkeypatch.setattr(coordinator_mod, "asyncio", asyncio_proxy)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload_mock)

    source._schedule_topology_reload()  # ruff: ignore[private-member-access]
    reload_task = source._topology_reload_task  # ruff: ignore[private-member-access]
    assert reload_task is not None
    await reload_task

    reload_mock.assert_awaited_once_with(entry.entry_id)
    assert replacement._pending_device_removals == [identifier]  # ruff: ignore[private-member-access]
    assert replacement._pending_battery_pack_removal_serials == {  # ruff: ignore[private-member-access]
        identifier: _FROZEN_PACK_SN,
    }
    assert replacement._battery_pack_identity_overrides == {}  # ruff: ignore[private-member-access]
    assert source._pending_device_removals == []  # ruff: ignore[private-member-access]
    assert source._pending_battery_pack_removal_serials == {}  # ruff: ignore[private-member-access]
    assert source._battery_pack_identity_overrides == {}  # ruff: ignore[private-member-access]
    assert source._battery_pack_topology_reload_required is False  # ruff: ignore[private-member-access]


# ---------------------------------------------------------------------------
# _merge_subdevice_data integration: smart plugs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_smart_plug_absent_from_one_partial_push_is_not_dropped(
    hass: HomeAssistant,
) -> None:
    """A plug missing from one partial push remains discovery-confirmed."""
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    updated: dict[str, Any] = {
        PAYLOAD_SYSTEM: {
            FIELD_ACCESSORIES: [
                {FIELD_DEVICE_SN: _FRESH_SN},
                {FIELD_DEVICE_SN: _OTHER_SN},
            ],
        },
        PAYLOAD_SMART_PLUGS: [
            {FIELD_DEVICE_SN: _FRESH_SN, "power": 1},
            {
                FIELD_DEVICE_SN: _OTHER_SN,
                "power": 5,
                SUBDEVICE_FIELD_LAST_SEEN_AT: _iso(
                    BATTERY_PACK_STALE_THRESHOLD_SEC + 1,
                ),
            },
        ],
    }

    coordinator._merge_subdevice_data(  # ruff: ignore[private-member-access]
        updated,
        {FIELD_PLUGS: [{FIELD_DEVICE_SN: _FRESH_SN, "power": 10}]},
        device_id=DEVICE_ID,
    )

    plug_sns = {item[FIELD_DEVICE_SN] for item in updated[PAYLOAD_SMART_PLUGS]}
    assert plug_sns == {_OTHER_SN, _FRESH_SN}
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_smart_plug_silent_past_the_threshold_is_dropped(
    hass: HomeAssistant,
) -> None:
    """A plug the app unbound eventually ages out of ``coordinator.data``."""
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    updated: dict[str, Any] = {
        PAYLOAD_SYSTEM: {
            FIELD_ACCESSORIES: [{FIELD_DEVICE_SN: _FRESH_SN}],
        },
        PAYLOAD_SMART_PLUGS: [
            {FIELD_DEVICE_SN: _FRESH_SN, "power": 1},
            {
                FIELD_DEVICE_SN: _STALE_SN,
                SUBDEVICE_FIELD_LAST_SEEN_AT: _iso(
                    BATTERY_PACK_STALE_THRESHOLD_SEC + 1,
                ),
            },
        ],
    }

    coordinator._merge_subdevice_data(  # ruff: ignore[private-member-access]
        updated,
        {FIELD_PLUGS: [{FIELD_DEVICE_SN: _FRESH_SN, "power": 10}]},
        device_id=DEVICE_ID,
    )

    plug_sns = {item[FIELD_DEVICE_SN] for item in updated[PAYLOAD_SMART_PLUGS]}
    assert plug_sns == {_FRESH_SN}
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# _merge_subdevice_data integration: generic sub-devices
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generic_sub_device_absent_from_one_partial_push_is_not_dropped(
    hass: HomeAssistant,
) -> None:
    """A subdevice missing from one partial push remains discovery-confirmed."""
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    updated: dict[str, Any] = {
        PAYLOAD_SYSTEM: {
            FIELD_ACCESSORIES: [
                {FIELD_DEVICE_SN: _FRESH_SN},
                {FIELD_DEVICE_SN: _OTHER_SN},
            ],
        },
        PAYLOAD_SUBDEVICES: [
            {FIELD_DEVICE_SN: _FRESH_SN, "model": "Smoke Sensor"},
            {
                FIELD_DEVICE_SN: _OTHER_SN,
                "model": "Water Leak Sensor",
                SUBDEVICE_FIELD_LAST_SEEN_AT: _iso(
                    BATTERY_PACK_STALE_THRESHOLD_SEC + 1,
                ),
            },
        ],
    }

    coordinator._merge_subdevice_data(  # ruff: ignore[private-member-access]
        updated,
        {
            FIELD_SUB_DEVICE: [
                {FIELD_DEVICE_SN: _FRESH_SN, "model": "Smoke Sensor"},
            ],
        },
        device_id=DEVICE_ID,
    )

    sns = {item[FIELD_DEVICE_SN] for item in updated[PAYLOAD_SUBDEVICES]}
    assert sns == {_OTHER_SN, _FRESH_SN}
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_generic_sub_device_silent_past_the_threshold_is_dropped(
    hass: HomeAssistant,
) -> None:
    """An unbound smoke/water-leak/temp-humidity accessory ages out."""
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    updated: dict[str, Any] = {
        PAYLOAD_SYSTEM: {
            FIELD_ACCESSORIES: [{FIELD_DEVICE_SN: _FRESH_SN}],
        },
        PAYLOAD_SUBDEVICES: [
            {FIELD_DEVICE_SN: _FRESH_SN, "model": "Smoke Sensor"},
            {
                FIELD_DEVICE_SN: _STALE_SN,
                "model": "Water Leak Sensor",
                SUBDEVICE_FIELD_LAST_SEEN_AT: _iso(
                    BATTERY_PACK_STALE_THRESHOLD_SEC + 1,
                ),
            },
        ],
    }

    coordinator._merge_subdevice_data(  # ruff: ignore[private-member-access]
        updated,
        {
            FIELD_SUB_DEVICE: [
                {FIELD_DEVICE_SN: _FRESH_SN, "model": "Smoke Sensor"},
            ],
        },
        device_id=DEVICE_ID,
    )

    sns = {item[FIELD_DEVICE_SN] for item in updated[PAYLOAD_SUBDEVICES]}
    assert sns == {_FRESH_SN}
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
