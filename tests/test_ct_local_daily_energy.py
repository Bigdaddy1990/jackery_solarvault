"""Regression tests for CT day energy derived from live lifetime counters."""

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    APP_DEVICE_STAT_PV_ENERGY,
    DOMAIN,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    LOCAL_DAILY_LIFETIME_METRICS,
    PAYLOAD_CT_METER,
    PAYLOAD_LOCAL_DAILY_ENERGY,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    normalize_jackery_ct_energy_units,
)
from custom_components.jackery_solarvault.sensor import LOCAL_DAILY_METRIC_BY_SENSOR_KEY

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_DEVICE_ID = "device-1"
_TODAY = date(2026, 7, 23)


def _coordinator() -> JackerySolarVaultCoordinator:
    """Return a bare coordinator with a persisted same-day CT anchor."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator)._local_daily_snapshots = {
        _DEVICE_ID: {
            "day": _TODAY.isoformat(),
            "values": {
                FIELD_CT_TOTAL_PHASE_ENERGY: 77_000,
                FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 103_000,
            },
            "full_day_metrics": [
                FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
                FIELD_CT_TOTAL_PHASE_ENERGY,
            ],
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
    for period in ("week", "month", "year"):
        assert (
            LOCAL_DAILY_METRIC_BY_SENSOR_KEY[f"ct_input_{period}_energy"]
            == FIELD_CT_TOTAL_PHASE_ENERGY
        )
        assert (
            LOCAL_DAILY_METRIC_BY_SENSOR_KEY[f"ct_output_{period}_energy"]
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

    result = JackerySolarVaultCoordinator._local_daily_counter_properties(
        properties,
        payload,
    )

    assert result["batSoc"] == 50
    assert result[FIELD_CT_TOTAL_PHASE_ENERGY] == 77_913
    assert result[FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY] == 103_495


def test_ct_daily_deltas_are_reported_in_kwh() -> None:
    """Same-day CT lifetime growth becomes positive local import/export energy."""
    coordinator = _coordinator()
    properties = coordinator._local_daily_counter_properties(
        {},
        {
            PAYLOAD_CT_METER: {
                FIELD_CT_TOTAL_PHASE_ENERGY: 77_913,
                FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 103_495,
            },
        },
    )

    deltas = coordinator._refresh_local_daily_for_device(
        _DEVICE_ID,
        properties,
        today=_TODAY,
        allow_new_anchor_delta=False,
    )

    assert deltas[FIELD_CT_TOTAL_PHASE_ENERGY] == 913
    assert deltas[FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY] == 495
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


def test_ct_daily_delta_migrates_legacy_deciwh_baseline() -> None:
    """A pre-Wh CT anchor cannot turn a scale migration into daily energy."""
    coordinator = _coordinator()
    snapshot = coordinator._local_daily_snapshots[_DEVICE_ID]
    snapshot["values"] = {
        FIELD_CT_TOTAL_PHASE_ENERGY: 108_702,
        FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 136_318,
    }
    snapshot["last_deltas"] = {
        FIELD_CT_TOTAL_PHASE_ENERGY: 100,
        FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 99,
    }
    snapshot["completed_days"] = {
        "2026-07-22": {
            FIELD_CT_TOTAL_PHASE_ENERGY: 200,
            FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 50,
        },
    }
    properties = coordinator._local_daily_counter_properties(
        {},
        {
            PAYLOAD_CT_METER: normalize_jackery_ct_energy_units({
                FIELD_CT_TOTAL_PHASE_ENERGY: 108_935,
                FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 136_362,
            }),
        },
    )

    deltas = coordinator._refresh_local_daily_for_device(
        _DEVICE_ID,
        properties,
        today=_TODAY,
        allow_new_anchor_delta=False,
    )

    assert deltas[FIELD_CT_TOTAL_PHASE_ENERGY] == 2_330
    assert deltas[FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY] == 440
    migrated = coordinator._local_daily_snapshots[_DEVICE_ID]
    assert migrated["values"][FIELD_CT_TOTAL_PHASE_ENERGY] == 1_087_020
    assert migrated["values"][FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY] == 1_363_180
    assert migrated["last_deltas"][FIELD_CT_TOTAL_PHASE_ENERGY] == 2_330
    assert migrated["last_deltas"][FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY] == 440
    assert migrated["completed_days"]["2026-07-22"] == {
        FIELD_CT_TOTAL_PHASE_ENERGY: 2_000,
        FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 500,
    }


async def test_cache_load_migrates_legacy_ct_anchor_before_runtime_merge(
    hass: HomeAssistant,
) -> None:
    """A concurrent first refresh cannot reintroduce a legacy deciWh anchor."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="ct-cache-load-migration")
    entry.add_to_hass(hass)
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    entry.runtime_data = coordinator
    cast("Any", coordinator).hass = hass
    cast("Any", coordinator).entry = entry
    cast("Any", coordinator)._shutdown_started = False
    cast("Any", coordinator)._local_daily_snapshots = {}
    cast("Any", coordinator)._local_daily_cache_loaded = False
    cast("Any", coordinator)._persisted_local_daily_signature = ""
    cast("Any", coordinator)._schedule_background_once = MagicMock()
    cached = {
        _DEVICE_ID: {
            "day": _TODAY.isoformat(),
            "values": {
                FIELD_CT_TOTAL_PHASE_ENERGY: 108_702,
                FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 136_318,
            },
        },
    }
    current_values = {
        FIELD_CT_TOTAL_PHASE_ENERGY: 1_089_350,
        FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 1_363_620,
    }

    def load_with_concurrent_refresh(
        _hass: HomeAssistant,
        _entry_id: str,
    ) -> dict[str, dict[str, Any]]:
        coordinator._local_daily_snapshots = {
            _DEVICE_ID: {
                "day": _TODAY.isoformat(),
                "values": current_values,
            },
        }
        return cached

    with patch(
        "custom_components.jackery_solarvault.coordinator.async_load_daily_cache",
        side_effect=load_with_concurrent_refresh,
    ):
        assert await coordinator.async_load_local_daily_snapshots()

    loaded = coordinator._local_daily_snapshots[_DEVICE_ID]
    assert loaded["values"][FIELD_CT_TOTAL_PHASE_ENERGY] == 1_087_020
    assert loaded["values"][FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY] == 1_363_180
    deltas = coordinator._refresh_local_daily_for_device(
        _DEVICE_ID,
        current_values,
        today=_TODAY,
        allow_new_anchor_delta=False,
    )
    assert deltas == {}


def test_ct_daily_delta_keeps_small_wh_baseline() -> None:
    """A normal small Wh counter never receives the legacy migration."""
    coordinator = _coordinator()
    snapshot = coordinator._local_daily_snapshots[_DEVICE_ID]
    snapshot["values"] = {
        FIELD_CT_TOTAL_PHASE_ENERGY: 100,
        FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 200,
    }
    snapshot["completed_days"] = {
        "2026-07-22": {
            FIELD_CT_TOTAL_PHASE_ENERGY: 200,
            FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 50,
        },
    }
    properties = coordinator._local_daily_counter_properties(
        {},
        {
            PAYLOAD_CT_METER: normalize_jackery_ct_energy_units({
                FIELD_CT_TOTAL_PHASE_ENERGY: 150,
                FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 180,
            }),
        },
    )

    deltas = coordinator._refresh_local_daily_for_device(
        _DEVICE_ID,
        properties,
        today=_TODAY,
        allow_new_anchor_delta=False,
    )

    assert deltas[FIELD_CT_TOTAL_PHASE_ENERGY] == 1_400
    assert deltas[FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY] == 1_600
    assert coordinator._local_daily_snapshots[_DEVICE_ID]["completed_days"] == {
        "2026-07-22": {
            FIELD_CT_TOTAL_PHASE_ENERGY: 200,
            FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 50,
        },
    }


def test_jackery_main_daily_delta_uses_hundredths_of_kwh() -> None:
    """A captured pvEgy delta of 1817 represents 18.17 kWh, not 1.817."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator).data = {
        _DEVICE_ID: {
            "local_daily_energy": {APP_DEVICE_STAT_PV_ENERGY: 1817},
        },
    }

    assert coordinator.local_daily_energy_kwh(
        _DEVICE_ID,
        APP_DEVICE_STAT_PV_ENERGY,
    ) == pytest.approx(18.17)


def test_cold_start_stays_partial_until_observed_day_rollover() -> None:
    """A mid-day anchor stays silent until the next observed local day."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator)._local_daily_snapshots = {}

    first = coordinator._refresh_local_daily_for_device(
        _DEVICE_ID,
        {FIELD_CT_TOTAL_PHASE_ENERGY: 77_000},
        today=_TODAY,
        allow_new_anchor_delta=False,
    )

    assert first == {}
    assert coordinator._local_daily_snapshots[_DEVICE_ID] == {
        "day": _TODAY.isoformat(),
        "values": {FIELD_CT_TOTAL_PHASE_ENERGY: 77_000},
    }

    second = coordinator._refresh_local_daily_for_device(
        _DEVICE_ID,
        {FIELD_CT_TOTAL_PHASE_ENERGY: 77_913},
        today=_TODAY,
        allow_new_anchor_delta=False,
    )

    assert second == {}

    rollover_day = _TODAY + timedelta(days=1)
    rollover = coordinator._refresh_local_daily_for_device(
        _DEVICE_ID,
        {FIELD_CT_TOTAL_PHASE_ENERGY: 78_000},
        today=rollover_day,
        allow_new_anchor_delta=True,
    )
    assert rollover[FIELD_CT_TOTAL_PHASE_ENERGY] == 0

    after_rollover = coordinator._refresh_local_daily_for_device(
        _DEVICE_ID,
        {FIELD_CT_TOTAL_PHASE_ENERGY: 78_500},
        today=rollover_day,
        allow_new_anchor_delta=False,
    )
    assert after_rollover[FIELD_CT_TOTAL_PHASE_ENERGY] == 500


def test_ct_week_delta_uses_persisted_complete_days_and_wh_scaling() -> None:
    """A fully covered local CT week is summed in Wh and exposed in kWh."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    today = date(2026, 7, 23)
    cast("Any", coordinator)._local_daily_snapshots = {
        _DEVICE_ID: {
            "day": today.isoformat(),
            "values": {FIELD_CT_TOTAL_PHASE_ENERGY: 90_000},
            "completed_days": {
                "2026-07-20": {FIELD_CT_TOTAL_PHASE_ENERGY: 1000},
                "2026-07-21": {FIELD_CT_TOTAL_PHASE_ENERGY: 2000},
                "2026-07-22": {FIELD_CT_TOTAL_PHASE_ENERGY: 3000},
            },
            "complete_days": [
                "2026-07-20",
                "2026-07-21",
                "2026-07-22",
            ],
        },
    }
    cast("Any", coordinator).data = {
        _DEVICE_ID: {
            PAYLOAD_LOCAL_DAILY_ENERGY: {FIELD_CT_TOTAL_PHASE_ENERGY: 500},
        },
    }

    assert coordinator.local_period_energy_kwh(
        _DEVICE_ID,
        FIELD_CT_TOTAL_PHASE_ENERGY,
        period="week",
        today=today,
    ) == pytest.approx(6.5)
    assert (
        coordinator.local_period_energy_kwh(
            _DEVICE_ID,
            FIELD_CT_TOTAL_PHASE_ENERGY,
            period="month",
            today=today,
        )
        is None
    )


def test_local_jackery_ct_deciwh_normalization_matches_ble_and_http_wh() -> None:
    """Local Jackery event counters must share the BLE/HTTP Wh scale."""
    normalized = normalize_jackery_ct_energy_units({
        FIELD_CT_TOTAL_PHASE_ENERGY: 108_702,
        FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: 122_740,
    })

    assert normalized[FIELD_CT_TOTAL_PHASE_ENERGY] == 1_087_020
    assert normalized[FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY] == 1_227_400
