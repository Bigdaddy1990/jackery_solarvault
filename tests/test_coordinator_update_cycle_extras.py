"""Behavioral tests for guarded update-cycle side paths.

These extend :mod:`tests.test_coordinator_update_cycle_body` with the
deterministic side branches of ``_async_update_data_guarded`` that the default
happy path never exercises: the midnight day-rollover cache wipe, the
cloud-activation repair-issue lifecycle, the price-override merge and currency
mirror, and the no-devices ``UpdateFailed`` guard.

Only the Jackery cloud ``api`` boundary is mocked (via the reusable
:mod:`tests._update_cycle_fixture`); all internal cache, merge, and repair
logic runs for real. Scenarios assert business outcomes (what ends up in the
coordinator result / caches / issue registry), never call order. Time is read
from the coordinator's own local clock so no wall-clock flakiness is possible.
"""

from datetime import date
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_CURRENCY,
    FIELD_SINGLE_CURRENCY,
    PAYLOAD_DEVICE,
    PAYLOAD_PRICE,
    PAYLOAD_STATISTIC,
    REPAIR_ISSUE_DEVICE_NOT_ACTIVATED,
)
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from tests._update_cycle_fixture import (  # ruff:ignore[banned-api]
    DEVICE_ID,
    SYSTEM_ID,
    make_update_cycle_api,
    setup_update_cycle_coordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def _teardown(hass: HomeAssistant, entry_id: str) -> None:
    """Unload the entry and drain background tasks."""
    await hass.config_entries.async_unload(entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio()
async def test_day_rollover_wipes_day_bounded_caches(
    hass: HomeAssistant,
) -> None:
    """A local-date rollover across a year boundary clears day-bounded caches.

    The cycle detects that ``_cached_date`` predates today and wipes the
    day/week/month/year-bounded slow caches plus the recorder stat-import
    dedup signatures, so post-midnight fetches are not served yesterday's
    final values. Crossing a year boundary exercises the week, month, and
    year clear branches together.
    """
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    today = coordinator._local_today()
    coordinator._cached_date = date(today.year - 1, 1, 1)
    # Seed a day-bounded slow-cache entry and a stat-import signature that must
    # both be evicted by the rollover.
    coordinator._slow_cache["some-system"] = {
        PAYLOAD_STATISTIC: (0.0, {"stale": "yesterday"}),
    }
    coordinator._stat_import_last_sig["dev"] = "yesterday-sig"

    await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    # The day-bounded statistic cache key was popped and the stat-import dedup
    # cache emptied; the cached date advanced to today.
    assert PAYLOAD_STATISTIC not in coordinator._slow_cache["some-system"]
    assert coordinator._stat_import_last_sig == {}
    assert coordinator._cached_date == today
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio()
async def test_activation_zero_creates_repair_issue(
    hass: HomeAssistant,
) -> None:
    """A cloud ``activated=0`` flag raises a fixable not-activated repair issue."""
    api = make_update_cycle_api()

    def _inactive(dev_id: str) -> dict[str, Any]:
        # No live telemetry: an ``activated=0`` flag with no contradicting
        # populated payload is treated as a genuine not-activated signal.
        return {PAYLOAD_DEVICE: {"deviceId": dev_id, "activated": 0}}

    api.async_get_device_property = AsyncMock(side_effect=_inactive)
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)

    result = await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    assert DEVICE_ID in result
    issue_id = f"{entry.entry_id}_{DEVICE_ID}_{REPAIR_ISSUE_DEVICE_NOT_ACTIVATED}"
    assert issue_id in coordinator._activation_issue_active
    assert ir.async_get(hass).async_get_issue(
        "jackery_solarvault",
        issue_id,
    )
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio()
async def test_activation_recovers_and_dismisses_repair_issue(
    hass: HomeAssistant,
) -> None:
    """When the device reports ``activated=1`` the repair issue is dismissed."""
    api = make_update_cycle_api()
    activated = {"value": 0}

    def _property(dev_id: str) -> dict[str, Any]:
        return {PAYLOAD_DEVICE: {"deviceId": dev_id, "activated": activated["value"]}}

    api.async_get_device_property = AsyncMock(side_effect=_property)
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)

    await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()
    issue_id = f"{entry.entry_id}_{DEVICE_ID}_{REPAIR_ISSUE_DEVICE_NOT_ACTIVATED}"
    assert issue_id in coordinator._activation_issue_active

    # The device comes back activated on the next cycle: the issue clears.
    activated["value"] = 1
    await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    assert issue_id not in coordinator._activation_issue_active
    assert ir.async_get(hass).async_get_issue("jackery_solarvault", issue_id) is None
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio()
async def test_currency_mirrored_from_price_section(
    hass: HomeAssistant,
) -> None:
    """The device meta currency is backfilled from the system price section."""
    import time

    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    # Pre-warm the per-system price cache: on the fast critical path the price
    # endpoint is served ``stale_ok`` and never re-fetched synchronously, so the
    # currency must already be in the slow cache for this cycle to mirror it.
    coordinator._slow_cache[SYSTEM_ID] = {
        PAYLOAD_PRICE: (time.monotonic(), {FIELD_SINGLE_CURRENCY: "EUR"}),
    }

    result = await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    device_meta = result[DEVICE_ID][PAYLOAD_DEVICE]
    assert device_meta[FIELD_CURRENCY] == "EUR"
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio()
async def test_price_override_merged_within_ttl(
    hass: HomeAssistant,
) -> None:
    """A fresh price override is merged into the device's price section."""
    import time

    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    coordinator._price_overrides[DEVICE_ID] = (
        time.monotonic(),
        {"overriddenRate": 42},
    )

    result = await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    price_section = result[DEVICE_ID].get(PAYLOAD_PRICE) or {}
    assert price_section.get("overriddenRate") == 42
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio()
async def test_expired_price_override_is_evicted(
    hass: HomeAssistant,
) -> None:
    """A stale price override past its TTL is dropped and never merged."""
    import time

    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    stale_ts = time.monotonic() - coordinator._PRICE_OVERRIDE_TTL_SEC - 1
    coordinator._price_overrides[DEVICE_ID] = (stale_ts, {"overriddenRate": 42})

    result = await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    price_section = result[DEVICE_ID].get(PAYLOAD_PRICE) or {}
    assert "overriddenRate" not in price_section
    assert DEVICE_ID not in coordinator._price_overrides
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio()
async def test_no_devices_discovered_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """An empty device index with empty discovery escalates to UpdateFailed."""
    api = make_update_cycle_api(
        async_get_system_list=AsyncMock(return_value=[]),
        async_list_devices_legacy=AsyncMock(return_value=[]),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(
        hass,
        api=api,
        discover=False,
    )
    coordinator._device_index = {}

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data_guarded()
    await _teardown(hass, entry.entry_id)
