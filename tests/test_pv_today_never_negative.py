"""Regression tests: PV / solar generation energy is never surfaced negative.

PV generation is a produced-energy magnitude and is physically ``>= 0``. The
owner observed ``PV-Ertrag heute = -18.14`` (an impossible value) after the
compact ``today`` endpoint was empty and the value fell back to a local
lifetime-delta computed against a stale midnight anchor. The local-daily
lifetime-delta returns ``None`` (not a negative delta) when the current
lifetime counter is below the stored midnight anchor. Periodic HTTP payloads
remain unmodified by the transport-neutral ingest layer.
"""

from datetime import date

from custom_components.jackery_solarvault.client.local_daily_cache import daily_delta
from custom_components.jackery_solarvault.const import APP_DEVICE_STAT_PV_ENERGY

_TODAY = date(2026, 7, 9)


def test_local_daily_pv_delta_is_none_when_counter_below_anchor() -> None:
    """A PV lifetime counter below its anchor yields None, never a negative."""
    snapshot = {
        "day": _TODAY.isoformat(),
        "values": {APP_DEVICE_STAT_PV_ENERGY: 20_000},
    }

    # Current lifetime reading dropped below the anchor (counter reset / bad
    # anchor). The naive delta would be -1_860 Wh (-18.14-ish kWh-scale).
    delta = daily_delta(
        snapshot,
        APP_DEVICE_STAT_PV_ENERGY,
        18_140,
        today=_TODAY,
    )

    assert delta is None


def test_local_daily_pv_delta_is_nonnegative_when_valid() -> None:
    """A normal PV counter above the anchor yields a non-negative delta."""
    snapshot = {
        "day": _TODAY.isoformat(),
        "values": {APP_DEVICE_STAT_PV_ENERGY: 18_000},
    }

    delta = daily_delta(
        snapshot,
        APP_DEVICE_STAT_PV_ENERGY,
        21_000,
        today=_TODAY,
    )

    assert delta is not None
    assert delta >= 0
