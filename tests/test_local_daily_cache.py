"""Characterisation tests for the local midnight daily-energy cache.

These pin the "today's energy" delta backfill that anchors lifetime Wh
counters at local midnight and derives per-day deltas without the cloud
day-statistics endpoint, plus the persistence load/save cleaning rules.
"""

from datetime import date
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.jackery_solarvault.client import local_daily_cache as cache
from homeassistant.core import HomeAssistant

_TODAY = date(2024, 5, 20)
_TODAY_ISO = "2024-05-20"


# --- daily_delta ---------------------------------------------------------


def test_daily_delta_returns_difference_from_anchor() -> None:
    """A same-day snapshot yields current minus the midnight anchor."""
    snap = {
        "day": _TODAY_ISO,
        "values": {"pvEgy": 1000},
        "full_day_metrics": ["pvEgy"],
    }

    assert cache.daily_delta(snap, "pvEgy", 1250, today=_TODAY) == 250


@pytest.mark.parametrize(
    "snapshot",
    [
        None,
        "not-a-dict",
        {"day": "2024-05-19", "values": {"pvEgy": 1000}},  # stale day
        {"day": _TODAY_ISO, "values": "bad"},  # non-dict values
        {"day": _TODAY_ISO, "values": {}},  # missing anchor
        {"day": _TODAY_ISO, "values": {"pvEgy": "x"}},  # non-int anchor
        {"day": _TODAY_ISO, "values": {"pvEgy": 1000}},  # partial legacy row
    ],
)
def test_daily_delta_returns_none_for_unusable_snapshot(snapshot: Any) -> None:
    """Missing/stale/malformed snapshots disable the delta."""
    assert cache.daily_delta(snapshot, "pvEgy", 1250, today=_TODAY) is None


def test_daily_delta_none_when_current_missing_or_non_numeric() -> None:
    """A missing or non-numeric current counter disables the delta."""
    snap = {
        "day": _TODAY_ISO,
        "values": {"pvEgy": 1000},
        "full_day_metrics": ["pvEgy"],
    }

    assert cache.daily_delta(snap, "pvEgy", None, today=_TODAY) is None
    assert cache.daily_delta(
        snap,
        "pvEgy",
        cast("Any", "nope"),
        today=_TODAY,
    ) is None


def test_daily_delta_none_when_counter_below_anchor() -> None:
    """A counter below the anchor (reset) does not produce a negative delta."""
    snap = {
        "day": _TODAY_ISO,
        "values": {"pvEgy": 1000},
        "full_day_metrics": ["pvEgy"],
    }

    assert cache.daily_delta(snap, "pvEgy", 900, today=_TODAY) is None


# --- refresh_snapshot ----------------------------------------------------


def test_refresh_snapshot_creates_fresh_anchor_on_new_day() -> None:
    """A missing/stale snapshot re-anchors all numeric current values."""
    result = cache.refresh_snapshot(
        None,
        today=_TODAY,
        current_values=cast(
            "dict[str, int | float | None]",
            {"pvEgy": 1000, "batChgEgy": None, "bad": "x"},
        ),
    )

    assert result == {"day": _TODAY_ISO, "values": {"pvEgy": 1000}}


def test_refresh_snapshot_preserves_existing_and_adds_missing() -> None:
    """Same-day refresh keeps existing anchors and only adds new metrics."""
    snap = {"day": _TODAY_ISO, "values": {"pvEgy": 1000, "bad": "x"}}

    result = cache.refresh_snapshot(
        snap,
        today=_TODAY,
        current_values={"pvEgy": 9999, "batChgEgy": 50, "skip": None},
    )

    assert result == {"day": _TODAY_ISO, "values": {"pvEgy": 1000, "batChgEgy": 50}}


def test_refresh_snapshot_same_day_with_non_dict_values() -> None:
    """A same-day snapshot whose values are malformed rebuilds from current."""
    snap = {"day": _TODAY_ISO, "values": "corrupt"}

    result = cache.refresh_snapshot(
        snap,
        today=_TODAY,
        current_values=cast(
            "dict[str, int | float | None]",
            {"pvEgy": 500, "bad": "x", "none": None},
        ),
    )

    assert result == {"day": _TODAY_ISO, "values": {"pvEgy": 500}}


def test_refresh_snapshot_same_day_skips_non_str_and_non_int() -> None:
    """Non-string existing keys and non-int current values are dropped."""
    snap = {"day": _TODAY_ISO, "values": {"pvEgy": 1000, 5: 7}}

    result = cache.refresh_snapshot(
        snap,
        today=_TODAY,
        current_values=cast("dict[str, int | float | None]", {"batChgEgy": "x"}),
    )

    assert result == {"day": _TODAY_ISO, "values": {"pvEgy": 1000}}


def test_refresh_snapshot_archives_latest_delta_on_day_rollover() -> None:
    """Only an actually observed last same-day delta becomes completed history."""
    previous = {
        "day": "2024-05-20",
        "values": {"pvEgy": 1000},
        "full_day_metrics": ["pvEgy"],
        "complete_days": ["2024-05-20"],
        "last_deltas": {"pvEgy": 250},
    }

    result = cache.refresh_snapshot(
        previous,
        today=date(2024, 5, 21),
        current_values={"pvEgy": 1300},
        baseline_covers_full_day=True,
    )

    assert result == {
        "day": "2024-05-21",
        "values": {"pvEgy": 1300},
        "full_day_metrics": ["pvEgy"],
        "completed_days": {"2024-05-20": {"pvEgy": 250}},
        "complete_days": ["2024-05-20", "2024-05-21"],
    }


def test_refresh_snapshot_does_not_archive_partial_cold_start_day() -> None:
    """A midday anchor cannot become a fabricated complete day at rollover."""
    partial = cache.refresh_snapshot(
        None,
        today=date(2024, 5, 20),
        current_values={"pvEgy": 1000},
    )
    partial = cache.record_latest_deltas(partial, {"pvEgy": 250})

    result = cache.refresh_snapshot(
        partial,
        today=date(2024, 5, 21),
        current_values={"pvEgy": 1300},
        baseline_covers_full_day=True,
    )

    assert result == {
        "day": "2024-05-21",
        "values": {"pvEgy": 1300},
        "full_day_metrics": ["pvEgy"],
        "complete_days": ["2024-05-21"],
    }
    assert (
        cache.period_delta(
            result,
            "pvEgy",
            0,
            today=date(2024, 5, 21),
            period="week",
        )
        is None
    )


def test_period_delta_requires_every_elapsed_calendar_day() -> None:
    """A period total is returned only with complete elapsed-day coverage."""
    snapshot = {
        "day": "2024-05-22",
        "values": {"pvEgy": 1500},
        "completed_days": {
            "2024-05-20": {"pvEgy": 100},
            "2024-05-21": {"pvEgy": 200},
        },
        "complete_days": ["2024-05-20", "2024-05-21"],
    }

    assert cache.period_delta(
        snapshot,
        "pvEgy",
        300,
        today=date(2024, 5, 22),
        period="week",
    ) == 600

    del cast("dict[str, dict[str, int]]", snapshot["completed_days"])["2024-05-21"]
    assert (
        cache.period_delta(
            snapshot,
            "pvEgy",
            300,
            today=date(2024, 5, 22),
            period="week",
        )
        is None
    )


def test_period_delta_rejects_unmarked_legacy_completed_days() -> None:
    """Legacy history has no proof that its rows cover complete local days."""
    legacy_snapshot = {
        "day": "2024-05-21",
        "values": {"pvEgy": 1300},
        "completed_days": {"2024-05-20": {"pvEgy": 250}},
    }

    assert (
        cache.period_delta(
            legacy_snapshot,
            "pvEgy",
            100,
            today=date(2024, 5, 21),
            period="week",
        )
        is None
    )


def test_record_latest_deltas_preserves_temporarily_missing_metrics() -> None:
    """A partial transport frame cannot erase another metric's last day sample."""
    snapshot = {
        "day": _TODAY_ISO,
        "values": {"pvEgy": 1000, "batChgEgy": 200},
        "full_day_metrics": ["pvEgy", "batChgEgy"],
        "last_deltas": {"pvEgy": 250, "batChgEgy": 50},
    }

    result = cache.record_latest_deltas(snapshot, {"pvEgy": 300})

    assert result["last_deltas"] == {"pvEgy": 300, "batChgEgy": 50}


def test_late_metric_stays_partial_until_next_observed_rollover() -> None:
    """A same-day late counter cannot fabricate energy before first sighting."""
    partial = cache.refresh_snapshot(
        None,
        today=_TODAY,
        current_values={"pvEgy": 1000},
    )
    partial = cache.refresh_snapshot(
        partial,
        today=_TODAY,
        current_values={"pvEgy": 1100, "batChgEgy": 50},
    )

    assert partial == {
        "day": _TODAY_ISO,
        "values": {"pvEgy": 1000, "batChgEgy": 50},
    }
    assert cache.daily_delta(partial, "pvEgy", 1100, today=_TODAY) is None
    assert cache.daily_delta(partial, "batChgEgy", 70, today=_TODAY) is None

    rollover = cache.refresh_snapshot(
        partial,
        today=date(2024, 5, 21),
        current_values={"pvEgy": 1100, "batChgEgy": 70},
        baseline_covers_full_day=True,
    )

    assert rollover["full_day_metrics"] == ["batChgEgy", "pvEgy"]
    assert cache.daily_delta(
        rollover,
        "batChgEgy",
        95,
        today=date(2024, 5, 21),
    ) == 25


def test_same_day_late_metric_is_not_added_to_full_day_coverage() -> None:
    """Only counters present at rollover receive full-day coverage."""
    rollover = cache.refresh_snapshot(
        {"day": "2024-05-20", "values": {"pvEgy": 900}},
        today=date(2024, 5, 21),
        current_values={"pvEgy": 1000},
        baseline_covers_full_day=True,
    )
    with_late_metric = cache.refresh_snapshot(
        rollover,
        today=date(2024, 5, 21),
        current_values={"pvEgy": 1100, "batChgEgy": 50},
        baseline_covers_full_day=True,
    )

    assert with_late_metric["full_day_metrics"] == ["pvEgy"]
    assert cache.daily_delta(
        with_late_metric,
        "batChgEgy",
        70,
        today=date(2024, 5, 21),
    ) is None


def test_reauth_merge_never_invents_full_day_metric_coverage() -> None:
    """Only explicit markers survive a same-day reauth merge."""
    merged = cache._merge_snapshots(
        {
            "day": _TODAY_ISO,
            "values": {"pvEgy": 1000},
            "full_day_metrics": ["pvEgy"],
        },
        {
            "day": _TODAY_ISO,
            "values": {"pvEgy": 1100, "batChgEgy": 50},
        },
    )

    assert merged["values"] == {"pvEgy": 1000, "batChgEgy": 50}
    assert merged["full_day_metrics"] == ["pvEgy"]


# --- is_new_day / snapshot_day / signature -------------------------------


def test_is_new_day() -> None:
    """A non-dict or different-day snapshot counts as a new day."""
    assert cache.is_new_day(None, _TODAY) is True
    assert cache.is_new_day({"day": "2024-05-19"}, _TODAY) is True
    assert cache.is_new_day({"day": _TODAY_ISO}, _TODAY) is False


def test_snapshot_day() -> None:
    """The day accessor returns the ISO string only when present and a str."""
    assert cache.snapshot_day({"day": _TODAY_ISO}) == _TODAY_ISO
    assert cache.snapshot_day({"day": 20240520}) is None
    assert cache.snapshot_day(None) is None


def test_local_daily_signature_is_stable() -> None:
    """The signature is order-independent for equal content."""
    a = cache.local_daily_signature({"d1": {"day": _TODAY_ISO}, "d2": {}})
    b = cache.local_daily_signature({"d2": {}, "d1": {"day": _TODAY_ISO}})

    assert a == b


# --- async load / save ---------------------------------------------------


def _fake_store(loaded: Any) -> Any:
    store = type("_S", (), {})()
    store.async_load = AsyncMock(return_value=loaded)
    store.async_save = AsyncMock()
    return store


@pytest.mark.asyncio
async def test_async_load_daily_cache_cleans_and_filters(
    hass: HomeAssistant,
) -> None:
    """Load returns only well-formed snapshots with int-coercible values."""
    stored = {
        "entries": {
            "entry-1": {
                "dev-a": {"day": _TODAY_ISO, "values": {"pvEgy": "1000", "bad": "x"}},
                "dev-b": "not-a-dict",
                "dev-c": {"day": 123, "values": {}},
            },
        },
    }
    with patch.object(cache, "Store", return_value=_fake_store(stored)):
        result = await cache.async_load_daily_cache(hass, "entry-1")

    assert result == {"dev-a": {"day": _TODAY_ISO, "values": {"pvEgy": 1000}}}


@pytest.mark.asyncio
async def test_async_load_daily_cache_does_not_archive_unobserved_reauth_delta(
    hass: HomeAssistant,
) -> None:
    """A reauth row merge cannot pretend that it observed midnight rollover."""
    stored = {
        "entries": {
            "new-entry": {
                "dev-a": {"day": "2024-05-21", "values": {"pvEgy": 1300}},
            },
            "old-entry": {
                "dev-a": {
                    "day": "2024-05-20",
                    "values": {"pvEgy": 1000},
                    "last_deltas": {"pvEgy": 250},
                },
            },
        },
    }
    with patch.object(cache, "Store", return_value=_fake_store(stored)):
        result = await cache.async_load_daily_cache(hass, "new-entry")

    assert result == {
        "dev-a": {
            "day": "2024-05-21",
            "values": {"pvEgy": 1300},
        },
    }


@pytest.mark.parametrize("loaded", [None, {"entries": "bad"}, {"entries": {}}])
@pytest.mark.asyncio
async def test_async_load_daily_cache_empty_for_missing_store(
    hass: HomeAssistant, loaded: Any
) -> None:
    """A missing or malformed store loads as an empty mapping."""
    with patch.object(cache, "Store", return_value=_fake_store(loaded)):
        result = await cache.async_load_daily_cache(hass, "entry-1")

    assert result == {}


@pytest.mark.asyncio
async def test_async_save_daily_cache_persists_cleaned_snapshots(
    hass: HomeAssistant,
) -> None:
    """Save writes only cleaned snapshots and preserves other entries."""
    store = _fake_store({"entries": {"other": {"keep": {}}}})
    snapshots = {
        "dev-a": {
            "day": _TODAY_ISO,
            "values": {"pvEgy": 1000, "bad": "x"},
            "full_day_metrics": ["pvEgy", "pvEgy", "missing", 7],
            "completed_days": {"2024-05-19": {"pvEgy": "250", "bad": "x"}},
            "complete_days": ["2024-05-19", "bad", 7],
            "last_deltas": {"pvEgy": "300", "bad": "x"},
        },
        "dev-b": "not-a-dict",
    }
    with patch.object(cache, "Store", return_value=store):
        await cache.async_save_daily_cache(hass, "entry-1", snapshots=snapshots)

    saved = store.async_save.await_args.args[0]
    assert saved["entries"]["entry-1"] == {
        "dev-a": {
            "day": _TODAY_ISO,
            "values": {"pvEgy": 1000},
            "full_day_metrics": ["pvEgy"],
            "completed_days": {"2024-05-19": {"pvEgy": 250}},
            "complete_days": ["2024-05-19"],
            "last_deltas": {"pvEgy": 300},
        },
    }
    assert saved["entries"]["other"] == {"keep": {}}


@pytest.mark.asyncio
async def test_async_save_daily_cache_drops_malformed_fields(
    hass: HomeAssistant,
) -> None:
    """Non-str day, non-dict values and non-str metric keys are all dropped."""
    store = _fake_store(None)
    snapshots = {
        "dev-bad-day": {"day": 20240520, "values": {"pvEgy": 1}},
        "dev-bad-values": {"day": _TODAY_ISO, "values": "x"},
        "dev-ok": {"day": _TODAY_ISO, "values": {"pvEgy": 5, 9: 9}},
    }
    with patch.object(cache, "Store", return_value=store):
        await cache.async_save_daily_cache(hass, "e", snapshots=snapshots)

    assert store.async_save.await_args.args[0]["entries"]["e"] == {
        "dev-ok": {"day": _TODAY_ISO, "values": {"pvEgy": 5}},
    }


@pytest.mark.asyncio
async def test_async_load_daily_cache_drops_non_str_metric(
    hass: HomeAssistant,
) -> None:
    """A non-string metric key inside a snapshot's values is skipped on load."""
    stored = {"entries": {"e": {"dev": {"day": _TODAY_ISO, "values": {"pvEgy": 3, 7: 7}}}}}
    with patch.object(cache, "Store", return_value=_fake_store(stored)):
        result = await cache.async_load_daily_cache(hass, "e")

    assert result == {"dev": {"day": _TODAY_ISO, "values": {"pvEgy": 3}}}


@pytest.mark.asyncio
async def test_async_load_daily_cache_rejects_invalid_day_and_negative_anchor(
    hass: HomeAssistant,
) -> None:
    """Malformed dates and impossible negative lifetime anchors cannot bootstrap."""
    stored = {
        "entries": {
            "e": {
                "bad-day": {"day": "20-05-2024", "values": {"pvEgy": 3}},
                "negative": {"day": _TODAY_ISO, "values": {"pvEgy": -1}},
                "valid": {"day": _TODAY_ISO, "values": {"pvEgy": 4}},
            },
        },
    }
    with patch.object(cache, "Store", return_value=_fake_store(stored)):
        result = await cache.async_load_daily_cache(hass, "e")

    assert result == {
        "valid": {"day": _TODAY_ISO, "values": {"pvEgy": 4}},
    }


@pytest.mark.asyncio
async def test_daily_cache_survives_runtime_lock_recreation(
    hass: HomeAssistant,
) -> None:
    """The runtime lock is disposable; Store data survives a simulated reboot."""
    snapshots = {
        "dev": {"day": _TODAY_ISO, "values": {"pvEgy": 1234}},
    }

    await cache.async_save_daily_cache(hass, "restart-entry", snapshots=snapshots)
    hass.data.pop(cache._LOCK_KEY, None)  # ruff: ignore[private-member-access]

    assert await cache.async_load_daily_cache(hass, "restart-entry") == snapshots
