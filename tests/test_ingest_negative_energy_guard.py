"""Regression: the ingest negative-energy guard covers all kWh sections.

The symmetry section, the battery charge/discharge stat/trend sections, and
the home/onGrid stat section keep their documented signed conventions; every
other periodic (kWh) stat/trend section must have negative chart samples
dropped so they never reach the Recorder as negative statistics. Previously
only PV sections were filtered, so negative grid/CT energy leaked in.

Battery is exempt because its ``y1`` series is a combined signed
charge/discharge curve on some accounts (positive = charge, negative =
discharge): scrubbing the sign here made ``_is_signed_battery_energy_curve``
(util.py) and ``day_power_series_key`` unable to ever see the raw negative
samples they detect on, degrading ``battery_discharge_energy`` day-stats to a
single flat bucket (F-SWEEP-7).

Home-stat (``device_home_stat`` / ``HomeStatApi`` / ``device/stat/onGrid``)
is exempt for the same reason: ``totalOutGridEnergy`` (exporting surplus
solar/battery energy back to the grid) was observed arriving as a negative
value on the owner's live device. Scrubbing it here drops real net-export
samples.

EPS_STAT (``device/stat/eps``) is deliberately NOT exempt: no negative EPS
sample has been observed for this account, and EPS is a switched
backup-power circuit, not an inherently bidirectional grid tie. CT_STAT
(``device/stat/ct``) bypasses the ingest gate entirely (owner directive
2026-07-25: CT values come from the Shelly cloud and are delivered
correct), so the scrub never runs for CT sections.
"""

from custom_components.jackery_solarvault.const import (
    APP_CHART_SERIES_Y,
    APP_CHART_SERIES_Y1,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_PV_STAT,
    APP_SECTION_SYMMETRY_STAT,
    APP_STAT_TOTAL_DISCHARGE,
    DATE_TYPE_DAY,
)
from custom_components.jackery_solarvault.ingest import (
    TransportSource,
    _reject_negative_generation_section,  # ruff: ignore[import-private-name]
    gate_payload_section,
)


def _section(prefix: str) -> str:
    return f"{prefix}_day"


def test_battery_section_keeps_signed_series() -> None:
    """Battery's signed charge/discharge convention is preserved (F-SWEEP-7)."""
    section = _section(APP_SECTION_BATTERY_STAT)

    result = _reject_negative_generation_section(
        section, {APP_CHART_SERIES_Y: [1.0, -2.0, 3.0]}
    )

    assert result[APP_CHART_SERIES_Y] == [1.0, -2.0, 3.0]


def test_ct_section_passes_the_gate_unfiltered() -> None:
    """CT sections bypass the ingest gate entirely, negatives included.

    Owner directive 2026-07-25: CT values originate in the Shelly cloud
    and are delivered correct — ingest never touches them.
    """
    section = _section(APP_SECTION_CT_STAT)

    result = gate_payload_section(
        TransportSource.HTTP,
        section,
        {APP_CHART_SERIES_Y: [-5.0, 0.0]},
    )

    assert result[APP_CHART_SERIES_Y] == [-5.0, 0.0]


def test_eps_energy_negative_sample_dropped() -> None:
    """A negative EPS energy chart sample becomes a None gap.

    Unlike home-stat, no negative ``totalOutEpsEnergy`` sample has been
    observed for this account, and EPS is a switched backup-power circuit
    rather than a bidirectional grid tie, so it is not exempted (see
    coordinator.py ``_NEGATIVE_SERIES_EXEMPT_PREFIXES``).
    """
    section = _section(APP_SECTION_EPS_STAT)

    result = _reject_negative_generation_section(
        section, {APP_CHART_SERIES_Y: [-5.0, 0.0]}
    )

    assert result[APP_CHART_SERIES_Y] == [None, 0.0]


def test_pv_energy_negative_sample_still_dropped() -> None:
    """PV sections keep their existing negative-sample filtering."""
    section = _section(APP_SECTION_PV_STAT)

    result = _reject_negative_generation_section(
        section, {APP_CHART_SERIES_Y: [-1.0, 4.0]}
    )

    assert result[APP_CHART_SERIES_Y] == [None, 4.0]


def test_symmetry_section_keeps_signed_series() -> None:
    """The symmetry section's documented signed net convention is preserved."""
    section = _section(APP_SECTION_SYMMETRY_STAT)

    result = _reject_negative_generation_section(
        section, {APP_CHART_SERIES_Y: [1.0, -2.0, 3.0]}
    )

    assert result[APP_CHART_SERIES_Y] == [1.0, -2.0, 3.0]


def test_home_stat_section_keeps_signed_series() -> None:
    """The onGrid/home-stat section's signed net-flow convention is preserved.

    ``totalOutGridEnergy`` (energy exported to the grid) was observed
    arriving as negative on the owner's live device.
    """
    section = _section(APP_SECTION_HOME_STAT)

    result = _reject_negative_generation_section(
        section, {APP_CHART_SERIES_Y: [1.0, -2.0, 3.0]}
    )

    assert result[APP_CHART_SERIES_Y] == [1.0, -2.0, 3.0]


def test_gate_payload_section_keeps_signed_battery_discharge_curve() -> None:
    """The full gate keeps ``y1`` negatives reachable for signed-curve detection.

    ``day_power_series_key``/``_is_signed_battery_energy_curve`` (util.py)
    detect the combined signed charge/discharge convention by checking ``y1``
    for negative samples. If the ingest gate nulls them first, that detector
    is permanently unreachable and ``battery_discharge_energy`` degrades to a
    single flat day bucket (F-SWEEP-7).
    """
    section = f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}"

    result = gate_payload_section(
        TransportSource.HTTP,
        section,
        {
            APP_STAT_TOTAL_DISCHARGE: "4.0",
            APP_CHART_SERIES_Y1: [1.0, -2.0, 3.0],
        },
    )

    assert result[APP_CHART_SERIES_Y1] == [1.0, -2.0, 3.0]
