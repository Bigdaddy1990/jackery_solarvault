# ruff: noqa: E501, SLF001
"""Tests for the current-day local lifetime-counter anchor."""

from datetime import date
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.jackery_solarvault.client import daily_energy as cache
from homeassistant.core import HomeAssistant

_TODAY = date(2024, 5, 20)
_TODAY_ISO = "2024-05-20"


def test_daily_delta_uses_only_verified_full_day_anchor() -> None:
    """Only a counter observed from local midnight yields today's delta."""
    complete = {
        "day": _TODAY_ISO,
        "values": {"pvEgy": 1000},
        "full_day_metrics": ["pvEgy"],
    }
    partial = {"day": _TODAY_ISO, "values": {"pvEgy": 1000}}

    assert cache.daily_delta(complete, "pvEgy", 1250, today=_TODAY) == 250
    assert cache.daily_delta(partial, "pvEgy", 1250, today=_TODAY) is None
    assert cache.daily_delta(complete, "pvEgy", 900, today=_TODAY) is None
    assert cache.daily_delta(complete, "pvEgy", None, today=_TODAY) is None


def test_refresh_snapshot_keeps_only_current_day() -> None:
    """Rollover drops legacy history and creates one new full-day anchor."""
    previous = {
        "day": "2024-05-19",
        "values": {"pvEgy": 1000},
        "full_day_metrics": ["pvEgy"],
        "completed_days": {"2024-05-18": {"pvEgy": 250}},
        "complete_days": ["2024-05-18"],
        "last_deltas": {"pvEgy": 250},
    }

    assert cache.refresh_snapshot(
        previous,
        today=_TODAY,
        current_values={"pvEgy": 1300},
        baseline_covers_full_day=True,
    ) == {
        "day": _TODAY_ISO,
        "values": {"pvEgy": 1300},
        "full_day_metrics": ["pvEgy"],
    }


def test_same_day_late_metric_stays_partial() -> None:
    """A metric first seen after midnight cannot claim full-day coverage."""
    snapshot = cache.refresh_snapshot(
        {"day": _TODAY_ISO, "values": {"pvEgy": 1000}, "full_day_metrics": ["pvEgy"]},
        today=_TODAY,
        current_values={"pvEgy": 1100, "batChgEgy": 50},
    )

    assert snapshot["values"] == {"pvEgy": 1000, "batChgEgy": 50}
    assert snapshot["full_day_metrics"] == ["pvEgy"]
    assert cache.daily_delta(snapshot, "batChgEgy", 70, today=_TODAY) is None


def test_reauth_merge_uses_earliest_same_day_anchor() -> None:
    """Same-device reauth rows merge without inventing period history."""
    merged = cache._merge_snapshots(  # ruff: ignore[private-member-access]
        {
            "day": _TODAY_ISO,
            "values": {"pvEgy": 1100},
            "full_day_metrics": ["pvEgy"],
        },
        {"day": _TODAY_ISO, "values": {"pvEgy": 1000, "batChgEgy": 40}},
    )

    assert merged == {
        "day": _TODAY_ISO,
        "values": {"pvEgy": 1000, "batChgEgy": 40},
        "full_day_metrics": ["pvEgy"],
    }


def test_local_daily_signature_is_stable() -> None:
    """Equal anchor mappings have an order-independent signature."""
    first = cache.local_daily_signature({"a": {"day": _TODAY_ISO}, "b": {}})
    second = cache.local_daily_signature({"b": {}, "a": {"day": _TODAY_ISO}})

    assert first == second


def _fake_store(loaded: Any) -> Any:
    store = type("_Store", (), {})()
    store.async_load = AsyncMock(return_value=loaded)
    store.async_save = AsyncMock()
    return store


@pytest.mark.asyncio
async def test_load_strips_legacy_history(hass: HomeAssistant) -> None:
    """Legacy completed periods never return to the coordinator runtime."""
    stored = {
        "entries": {
            "entry": {
                "dev": {
                    "day": _TODAY_ISO,
                    "values": {"pvEgy": "1000", "bad": "x"},
                    "full_day_metrics": ["pvEgy", "missing"],
                    "completed_days": {"2024-05-19": {"pvEgy": 250}},
                    "complete_days": ["2024-05-19"],
                    "last_deltas": {"pvEgy": 250},
                }
            }
        }
    }
    with patch.object(cache, "Store", return_value=_fake_store(stored)):
        result = await cache.async_load_daily_cache(hass, "entry")

    assert result == {
        "dev": {
            "day": _TODAY_ISO,
            "values": {"pvEgy": 1000},
            "full_day_metrics": ["pvEgy"],
        }
    }


@pytest.mark.asyncio
async def test_save_persists_anchor_only(hass: HomeAssistant) -> None:
    """Saving removes historical fields while preserving other entry rows."""
    store = _fake_store({"entries": {"other": {"keep": {}}}})
    snapshots = {
        "dev": {
            "day": _TODAY_ISO,
            "values": {"pvEgy": 1000},
            "full_day_metrics": ["pvEgy"],
            "completed_days": {"2024-05-19": {"pvEgy": 250}},
            "complete_days": ["2024-05-19"],
            "last_deltas": {"pvEgy": 250},
        }
    }
    with patch.object(cache, "Store", return_value=store):
        await cache.async_save_daily_cache(hass, "entry", snapshots=snapshots)

    saved = store.async_save.await_args.args[0]
    assert saved["entries"]["entry"] == {
        "dev": {
            "day": _TODAY_ISO,
            "values": {"pvEgy": 1000},
            "full_day_metrics": ["pvEgy"],
        }
    }
    assert saved["entries"]["other"] == {"keep": {}}


@pytest.mark.asyncio
async def test_anchor_round_trip_survives_runtime_lock_recreation(
    hass: HomeAssistant,
) -> None:
    """The runtime lock is disposable while the HA Store remains durable."""
    snapshots = {
        "dev": {
            "day": _TODAY_ISO,
            "values": {"pvEgy": 1234},
            "full_day_metrics": ["pvEgy"],
        }
    }

    await cache.async_save_daily_cache(hass, "entry", snapshots=snapshots)
    hass.data.pop(cache._LOCK_KEY, None)  # ruff: ignore[private-member-access]

    assert await cache.async_load_daily_cache(hass, "entry") == snapshots


def test_invalid_values_are_ignored() -> None:
    """Malformed values cannot create a numeric daily delta."""
    snapshot = cache.refresh_snapshot(
        None,
        today=_TODAY,
        current_values=cast(
            "dict[str, int | float | None]",
            {"pvEgy": 1000, "bad": "x", "missing": None},
        ),
    )

    assert snapshot == {"day": _TODAY_ISO, "values": {"pvEgy": 1000}}
