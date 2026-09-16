"""Characterisation tests locking in HA-native lifetime statistics.

The lifetime sensors (``total_generation``, ``total_revenue``,
``total_carbon_saved``) never write custom long-term statistics -- Home
Assistant's own ``compile_statistics`` picks them up automatically because
their descriptions are ``state_class=TOTAL_INCREASING`` with a valid unit
and no ``reset_period``, which keeps ``last_reset`` at ``None`` and stops
``JackeryStatSensor.__init__`` from downgrading ``state_class`` to
``TOTAL`` (the treatment reserved for period totals). That shape had zero
test coverage; these tests pin it, plus the monotonicity guard that feeds
the generation total, so a future edit can't silently break lifetime
statistics.
"""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.const import (
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
    APP_STAT_TOTAL_GENERATION,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    PAYLOAD_STATISTIC,
)
from custom_components.jackery_solarvault.sensor import (
    STAT_DESCRIPTIONS,
    JackeryStatSensor,
)
from custom_components.jackery_solarvault.util import guard_statistic_totals_from_year
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import CURRENCY_EURO, UnitOfEnergy, UnitOfMass

_DEVICE_ID = "dev-1"
_LIFETIME_UNITS = {
    "total_generation": UnitOfEnergy.KILO_WATT_HOUR,
    "total_revenue": CURRENCY_EURO,
    "total_carbon_saved": UnitOfMass.KILOGRAMS,
}
_PREVIOUS_GENERATION = 100.0
_RAW_GENERATION_NO_DIP = 100.0
_RAW_GENERATION_BIG_DIP = 90.0
_RAW_GENERATION_SMALL_DIP = 99.7


def _lifetime_sensor(key: str) -> JackeryStatSensor:
    """Build a lifetime stat sensor through its real ``__init__``.

    Unlike ``test_stat_entity_values.py``'s ``.__new__()`` bypass, this
    exercises the ``__init__`` conditional that decides whether
    ``_attr_state_class`` gets set -- that decision is exactly what keeps
    lifetime sensors on ``TOTAL_INCREASING`` instead of being downgraded to
    ``TOTAL``.
    """
    description = next(desc for desc in STAT_DESCRIPTIONS if desc.key == key)
    return JackeryStatSensor(
        coordinator=cast("Any", SimpleNamespace()),
        device_id=_DEVICE_ID,
        description=description,
    )


def test_lifetime_stat_descriptions_are_total_increasing_with_no_reset_period() -> None:
    """Lifetime descriptions carry the shape HA needs for native long-term stats."""
    descriptions = {description.key: description for description in STAT_DESCRIPTIONS}

    for key, unit in _LIFETIME_UNITS.items():
        description = descriptions[key]
        assert description.state_class == SensorStateClass.TOTAL_INCREASING
        assert description.native_unit_of_measurement == unit
        assert description.reset_period is None


def test_lifetime_stat_sensor_keeps_total_increasing_and_no_last_reset() -> None:
    """The entity never gets the period-total treatment (TOTAL + last_reset)."""
    for key in _LIFETIME_UNITS:
        sensor = _lifetime_sensor(key)

        assert sensor.state_class == SensorStateClass.TOTAL_INCREASING
        assert sensor.last_reset is None


def _statistic_payload(raw_generation: float) -> dict[str, Any]:
    return {PAYLOAD_STATISTIC: {APP_STAT_TOTAL_GENERATION: raw_generation}}


def _previous_statistic(previous_generation: float) -> dict[str, Any]:
    return {APP_STAT_TOTAL_GENERATION: previous_generation}


def test_guard_leaves_generation_unchanged_when_raw_meets_previous_floor() -> None:
    """No dip: the cloud-reported total already satisfies the previous-value floor."""
    payload = _statistic_payload(_RAW_GENERATION_NO_DIP)

    guard_statistic_totals_from_year(
        payload,
        previous_statistic=_previous_statistic(_PREVIOUS_GENERATION),
    )

    assert (
        payload[PAYLOAD_STATISTIC][APP_STAT_TOTAL_GENERATION] == _RAW_GENERATION_NO_DIP
    )


def test_guard_raises_generation_to_previous_floor_when_dip_exceeds_tolerance() -> None:
    """A real regression (dip beyond rounding noise) is floored, never left lowered."""
    payload = _statistic_payload(_RAW_GENERATION_BIG_DIP)

    guard_statistic_totals_from_year(
        payload,
        previous_statistic=_previous_statistic(_PREVIOUS_GENERATION),
    )

    assert payload[PAYLOAD_STATISTIC][APP_STAT_TOTAL_GENERATION] == _PREVIOUS_GENERATION


def test_guard_leaves_generation_unchanged_when_dip_is_within_tolerance() -> None:
    """A dip inside the rounding-noise tolerance passes through uncorrected."""
    payload = _statistic_payload(_RAW_GENERATION_SMALL_DIP)

    guard_statistic_totals_from_year(
        payload,
        previous_statistic=_previous_statistic(_PREVIOUS_GENERATION),
    )

    assert (
        payload[PAYLOAD_STATISTIC][APP_STAT_TOTAL_GENERATION]
        == _RAW_GENERATION_SMALL_DIP
    )


def test_guard_prefers_app_system_pv_year_total_over_device_pv_total() -> None:
    """The lifetime floor follows the system PV chart shown by the app."""
    payload = {
        PAYLOAD_STATISTIC: {APP_STAT_TOTAL_GENERATION: 960.0},
        f"{APP_SECTION_PV_STAT}_year": {APP_STAT_TOTAL_SOLAR_ENERGY: 957.8},
        f"{APP_SECTION_PV_TRENDS}_year": {APP_STAT_TOTAL_SOLAR_ENERGY: 967.89},
    }

    guard_statistic_totals_from_year(payload)

    assert payload[PAYLOAD_STATISTIC][APP_STAT_TOTAL_GENERATION] == pytest.approx(
        967.89
    )
    assert payload[PAYLOAD_STATISTIC]["_total_lower_bound_guard"]["corrected"][
        APP_STAT_TOTAL_GENERATION
    ]["current_year_total"] == pytest.approx(967.89)
