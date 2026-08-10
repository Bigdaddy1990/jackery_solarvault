"""Regression tests for CT day energy derived from live lifetime counters."""

from datetime import date
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    LOCAL_DAILY_LIFETIME_METRICS,
    PAYLOAD_CT_METER,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.sensor import LOCAL_DAILY_METRIC_BY_SENSOR_KEY

_DEVICE_ID = "device-1"
_TODAY = date(2026, 7, 23)


def _coordinator() -> JackerySolarVaultCoordinator:
    """Return a bare coordinator with a persisted same-day CT anchor."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator)._local_daily_snapshots = {  # ruff: ignore[private-member-access]
        _DEVICE_ID: {
            "day": _TODAY.isoformat(),
            "values": {
                FIELD_CT_TOTAL_PHASE_ENERGY: 77_000,
                FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 103_000,
            },
        },
    }
    return coordinator


def test_ct_lifetime_fields_are_tracked_for_local_day_deltas() -> None:
    """Both directional CT lifetime counters participate in daily snapshots."""
    assert FIELD_CT_TOTAL_PHASE_ENERGY in LOCAL_DAILY_LIFETIME_METRICS
    assert FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY in LOCAL_DAILY_LIFETIME_METRICS


def test_ct_day_sensor_keys_use_directional_lifetime_counters() -> None:
    """CT day entities resolve their local fallback to the matching Wh counter."""
    assert (
        LOCAL_DAILY_METRIC_BY_SENSOR_KEY["ct_input_day_energy"]
        == FIELD_CT_TOTAL_PHASE_ENERGY
    )
    assert (
        LOCAL_DAILY_METRIC_BY_SENSOR_KEY["ct_output_day_energy"]
        == FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY
    )


def test_ct_bucket_is_merged_into_local_daily_counter_properties() -> None:
    """The CT accessory bucket reaches the otherwise main-property-only cache."""
    properties = {"batSoc": 50}
    payload = {
        PAYLOAD_CT_METER: {
            FIELD_CT_TOTAL_PHASE_ENERGY: 77_913,
            FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 103_495,
        },
    }

    result = JackerySolarVaultCoordinator._local_daily_counter_properties(  # ruff: ignore[private-member-access]
        properties,
        payload,
    )

    assert result["batSoc"] == 50  # ruff: ignore[magic-value-comparison]
    assert result[FIELD_CT_TOTAL_PHASE_ENERGY] == 77_913  # ruff: ignore[magic-value-comparison]
    assert result[FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY] == 103_495  # ruff: ignore[magic-value-comparison]


def test_ct_daily_deltas_are_reported_in_kwh() -> None:
    """Same-day CT lifetime growth becomes positive local import/export energy."""
    coordinator = _coordinator()
    properties = coordinator._local_daily_counter_properties(  # ruff: ignore[private-member-access]
        {},
        {
            PAYLOAD_CT_METER: {
                FIELD_CT_TOTAL_PHASE_ENERGY: 77_913,
                FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 103_495,
            },
        },
    )

    deltas = coordinator._refresh_local_daily_for_device(  # ruff: ignore[private-member-access]
        _DEVICE_ID,
        properties,
        today=_TODAY,
        allow_new_anchor_delta=False,
    )

    assert deltas[FIELD_CT_TOTAL_PHASE_ENERGY] == 913  # ruff: ignore[magic-value-comparison]
    assert deltas[FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY] == 495  # ruff: ignore[magic-value-comparison]
    cast("Any", coordinator).data = {
        _DEVICE_ID: {"local_daily_energy": deltas},
    }
    assert coordinator.local_daily_energy_kwh(
        _DEVICE_ID,
        FIELD_CT_TOTAL_PHASE_ENERGY,
    ) == pytest.approx(0.913)
    assert coordinator.local_daily_energy_kwh(
        _DEVICE_ID,
        FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    ) == pytest.approx(0.495)


def test_cold_start_seeds_anchor_then_reports_same_day_growth() -> None:
    """A missing Store row must be seeded instead of staying empty forever."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator)._local_daily_snapshots = {}  # ruff: ignore[private-member-access]

    first = coordinator._refresh_local_daily_for_device(  # ruff: ignore[private-member-access]
        _DEVICE_ID,
        {FIELD_CT_TOTAL_PHASE_ENERGY: 77_000},
        today=_TODAY,
        allow_new_anchor_delta=False,
    )

    assert first == {}
    assert coordinator._local_daily_snapshots[_DEVICE_ID] == {  # ruff: ignore[private-member-access]
        "day": _TODAY.isoformat(),
        "values": {FIELD_CT_TOTAL_PHASE_ENERGY: 77_000},
    }

    second = coordinator._refresh_local_daily_for_device(  # ruff: ignore[private-member-access]
        _DEVICE_ID,
        {FIELD_CT_TOTAL_PHASE_ENERGY: 77_913},
        today=_TODAY,
        allow_new_anchor_delta=False,
    )

    assert second[FIELD_CT_TOTAL_PHASE_ENERGY] == 913  # ruff: ignore[magic-value-comparison]
