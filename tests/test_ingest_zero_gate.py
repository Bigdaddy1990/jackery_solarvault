"""Regression tests for the zero-period confirmation gate (B4 2026-07-03).

At 03:44 the cloud served ``deviceStatistic = {batChgEgy: "0.00",
batDisChgEgy: "0.00"}`` while the sibling ``statistic`` (systemStatistic)
response confirmed the same today-zeros (``todayBatteryChg`` /
``todayBatteryDisChg`` = "0.00"). The zero gate silently dropped the
section anyway, so every ``device_today_*`` fallback read an empty dict.
AGENTS.md §2.2 rule 7 says zeros are ignored *unless confirmed by another
source* — with the sibling confirmation present, the zeros are real
(fresh day) and must reach live state. Unconfirmed drops must at least
log, never vanish silently.
"""

import logging
import math
from typing import TYPE_CHECKING

from custom_components.jackery_solarvault.client.ingest import ingest as ingest_gate
from custom_components.jackery_solarvault.client.ingest.ingest import (
    TransportSource,
    gate_payload_section,
    gate_period_hierarchy_for_recorder,
)
from custom_components.jackery_solarvault.const import (
    APP_CHART_SERIES_Y,
    APP_DEVICE_STAT_BATTERY_CHARGE,
    APP_DEVICE_STAT_BATTERY_DISCHARGE,
    APP_DEVICE_STAT_ONGRID_INPUT,
    APP_DEVICE_STAT_PV_ENERGY,
    APP_SECTION_HOME_STAT,
    APP_SECTION_PV_STAT,
    APP_STAT_TODAY_BATTERY_CHARGE,
    APP_STAT_TODAY_BATTERY_DISCHARGE,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    PAYLOAD_DEVICE_STATISTIC,
)

if TYPE_CHECKING:
    import pytest

_ZERO_DAY_COUNTERS = {
    APP_DEVICE_STAT_BATTERY_CHARGE: "0.00",
    APP_DEVICE_STAT_BATTERY_DISCHARGE: "0.00",
}
_FINAL_SERIES_SAMPLE = 3
_CONFIRMING_STATISTIC = {
    APP_STAT_TODAY_BATTERY_CHARGE: "0.00",
    APP_STAT_TODAY_BATTERY_DISCHARGE: "0.00",
    "totalPv": "1234.5",
}


def test_zero_day_counters_pass_when_sibling_statistic_confirms() -> None:
    """Sibling-confirmed today-zeros are real values, not cloud glitches."""
    result = gate_payload_section(
        TransportSource.HTTP,
        PAYLOAD_DEVICE_STATISTIC,
        dict(_ZERO_DAY_COUNTERS),
        confirmation_source=_CONFIRMING_STATISTIC,
    )

    assert result == _ZERO_DAY_COUNTERS


def test_zero_day_counters_stay_dropped_without_confirmation() -> None:
    """Without a sibling source the unconfirmed-zero rule keeps applying."""
    result = gate_payload_section(
        TransportSource.HTTP,
        PAYLOAD_DEVICE_STATISTIC,
        dict(_ZERO_DAY_COUNTERS),
    )

    assert result == {}


def test_zero_day_counters_stay_dropped_when_sibling_disagrees() -> None:
    """A non-zero sibling counter marks the zero payload as a cloud glitch."""
    result = gate_payload_section(
        TransportSource.HTTP,
        PAYLOAD_DEVICE_STATISTIC,
        dict(_ZERO_DAY_COUNTERS),
        confirmation_source={
            APP_STAT_TODAY_BATTERY_CHARGE: "1.50",
            APP_STAT_TODAY_BATTERY_DISCHARGE: "0.00",
        },
    )

    assert result == {}


def test_zero_payload_with_unmapped_counter_stays_dropped() -> None:
    """Mutual validation is strict: every zero must have a confirming sibling."""
    payload = dict(_ZERO_DAY_COUNTERS)
    payload[APP_DEVICE_STAT_ONGRID_INPUT] = "0.00"

    result = gate_payload_section(
        TransportSource.HTTP,
        PAYLOAD_DEVICE_STATISTIC,
        payload,
        confirmation_source=_CONFIRMING_STATISTIC,
    )

    assert result == {}


def test_unconfirmed_zero_drop_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The gate never drops a section silently (B4 finding: 0 log hits)."""
    with caplog.at_level(
        logging.DEBUG,
        logger="custom_components.jackery_solarvault.client.ingest.ingest",
    ):
        gate_payload_section(
            TransportSource.HTTP,
            PAYLOAD_DEVICE_STATISTIC,
            dict(_ZERO_DAY_COUNTERS),
        )

    assert any(
        "zero-only period payload" in record.message.lower()
        for record in caplog.records
    )


def test_cloud_mqtt_periodic_section_is_blocked() -> None:
    """MQTT/BLE must never feed periodic stats; HTTP owns stats/trends ingest."""
    result = gate_payload_section(
        TransportSource.CLOUD_MQTT,
        PAYLOAD_DEVICE_STATISTIC,
        {APP_DEVICE_STAT_PV_ENERGY: "12.3"},
    )

    assert result == {}


def test_non_periodic_live_payload_bypasses_zero_gate() -> None:
    """Live payloads are not stats/trends and must not be eaten by ingest."""
    result = gate_payload_section(
        TransportSource.LOCAL_MQTT,
        "device_live_property",
        {"power": 0, "nested": {"voltage": 0}},
    )

    assert result == {"power": 0, "nested": {"voltage": 0}}


def test_negative_generation_scalars_are_removed_only_for_generation_fields() -> None:
    """Impossible negative PV generation is filtered without touching other fields."""
    result = gate_payload_section(
        TransportSource.HTTP,
        APP_SECTION_HOME_STAT,
        {
            APP_STAT_TOTAL_SOLAR_ENERGY: "-1.5",
            APP_DEVICE_STAT_ONGRID_INPUT: "-2.0",
            "non_numeric": "bad",
            "flag": True,
            "nan_value": "nan",
            "inf_value": "inf",
        },
    )

    assert APP_STAT_TOTAL_SOLAR_ENERGY not in result
    assert result[APP_DEVICE_STAT_ONGRID_INPUT] == "-2.0"
    assert result["non_numeric"] == "bad"
    assert result["flag"] is True
    assert result["nan_value"] == "nan"
    assert result["inf_value"] == "inf"


def test_negative_pv_chart_samples_become_gaps() -> None:
    """PV chart arrays preserve bucket positions while withholding bad samples."""
    result = gate_payload_section(
        TransportSource.HTTP,
        f"{APP_SECTION_PV_STAT}_day",
        {
            APP_CHART_SERIES_Y: [
                1,
                "-2.5",
                True,
                "bad",
                float("nan"),
                _FINAL_SERIES_SAMPLE,
            ],
            "other_series": [-4],
        },
    )

    cleaned_series = result[APP_CHART_SERIES_Y]
    assert cleaned_series[:4] == [1, None, True, "bad"]
    assert math.isnan(cleaned_series[4])
    assert cleaned_series[5] == _FINAL_SERIES_SAMPLE
    assert result["other_series"] == [-4]


def test_non_numeric_periodic_payload_is_not_zero_only() -> None:
    """A text-only periodic payload is not a zero-only cloud glitch."""
    result = gate_payload_section(
        TransportSource.HTTP,
        PAYLOAD_DEVICE_STATISTIC,
        {"text": "not-a-number"},
        confirmation_source=_CONFIRMING_STATISTIC,
    )

    assert result == {"text": "not-a-number"}


def test_nested_zero_periodic_payload_is_dropped_without_confirmation() -> None:
    """Nested numeric zeros still count as zero-only period payloads."""
    result = gate_payload_section(
        TransportSource.HTTP,
        PAYLOAD_DEVICE_STATISTIC,
        {"nested": {APP_DEVICE_STAT_PV_ENERGY: "0.00"}},
    )

    assert result == {}


def test_series_helper_rejects_non_list_values() -> None:
    """Only chart/list values can mark a zero-period payload as populated."""
    assert ingest_gate._has_populated_series("0.1") is False  # ruff:ignore[private-member-access]


def test_zero_confirmation_requires_numeric_fields() -> None:
    """A zero confirmation needs actual numeric day counters to validate."""
    result = ingest_gate._zero_period_payload_confirmed(  # ruff:ignore[private-member-access]
        PAYLOAD_DEVICE_STATISTIC,
        {"text": "not-a-number"},
        _CONFIRMING_STATISTIC,
    )

    assert result is False


def test_populated_series_keeps_zero_payload() -> None:
    """A periodic chart with populated samples is not a zero-only cloud glitch."""
    result = gate_payload_section(
        TransportSource.HTTP,
        APP_SECTION_PV_STAT,
        {APP_CHART_SERIES_Y: [None, "0.1"], APP_STAT_TOTAL_SOLAR_ENERGY: "0.00"},
    )

    assert result == {
        APP_CHART_SERIES_Y: [None, "0.1"],
        APP_STAT_TOTAL_SOLAR_ENERGY: "0.00",
    }


def test_period_hierarchy_gate_removes_only_violating_sections() -> None:
    """Recorder withholding keeps unrelated sections intact."""
    payload = {
        "device_pv_stat_day": {"totalSolarEnergy": "10"},
        "device_pv_stat_week": {"totalSolarEnergy": "8"},
        "device_home_stat_day": {"homeLoad": "1"},
    }

    result = gate_period_hierarchy_for_recorder(
        payload,
        frozenset({"device_pv_stat_day"}),
    )

    assert result == {
        "device_pv_stat_week": {"totalSolarEnergy": "8"},
        "device_home_stat_day": {"homeLoad": "1"},
    }
    assert gate_period_hierarchy_for_recorder(payload, frozenset()) == payload
