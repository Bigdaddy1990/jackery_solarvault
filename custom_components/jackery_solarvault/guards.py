"""Value guards for the Jackery SolarVault integration.

Guards protect values that Home Assistant must never see move the wrong way
(for example a lifetime energy counter falling back). They are the only place
for such rules outside :mod:`.ingest`, which stays the statistics safety gate.
Guards hold no Home Assistant state and perform no I/O.
"""

from datetime import date
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from .const import (
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
    APP_STAT_TOTAL_CARBON,
    APP_STAT_TOTAL_GENERATION,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    DATE_TYPE_YEAR,
    PAYLOAD_STATISTIC,
)
from .util import effective_period_total_value, safe_float

if TYPE_CHECKING:
    from homeassistant.components.sensor import SensorEntityDescription
    from homeassistant.const import StateType

_MAX_CARBON_FACTOR: Final = 5
_LIFETIME_GUARD_KEY: Final = "_total_lower_bound_guard"


def _tolerance(*values: float | None) -> float:
    """Return a kWh tolerance large enough for app rounding noise."""
    magnitude = max((abs(value) for value in values if value is not None), default=0.0)
    return max(0.05, magnitude * 0.005)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _year_generation(payload: dict[str, Any]) -> float | None:
    """Return the app's current-year PV total (system chart first)."""
    for prefix in (APP_SECTION_PV_TRENDS, APP_SECTION_PV_STAT):
        section = f"{prefix}_{DATE_TYPE_YEAR}"
        pv_year = payload.get(section)
        if isinstance(pv_year, dict):
            return effective_period_total_value(
                pv_year, section, APP_STAT_TOTAL_SOLAR_ENERGY
            )
    return None


def guard_lifetime_totals(
    payload: dict[str, Any],
    *,
    previous_statistic: dict[str, Any] | None = None,
) -> None:
    """Keep lifetime ``totalGeneration`` at or above its verified lower bounds.

    PROTOCOL §7/§8.1: the lifetime total is never lowered by a smaller cloud
    value; the current-year PV total and the previously published lifetime
    total are lower bounds. ``totalCarbon`` follows with the app's own factor.
    The correction is recorded under ``_total_lower_bound_guard``.
    """
    statistic = payload.get(PAYLOAD_STATISTIC)
    if not isinstance(statistic, dict):
        return
    year_generation = _year_generation(payload)
    raw_generation = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
    previous_generation = (
        safe_float(previous_statistic.get(APP_STAT_TOTAL_GENERATION))
        if isinstance(previous_statistic, dict)
        else None
    )
    candidates = [
        value
        for value in (raw_generation, year_generation, previous_generation)
        if value is not None
    ]
    if not candidates:
        return
    corrected = max(candidates)
    if raw_generation is not None and corrected <= raw_generation + _tolerance(
        corrected, raw_generation
    ):
        return
    if raw_generation is None:
        if previous_generation is None:
            return
        corrected = previous_generation
        method = "previous_total_lower_bound"
    elif previous_generation is not None and previous_generation >= corrected - (
        _tolerance(previous_generation, corrected)
    ):
        method = "previous_total_lower_bound"
    else:
        method = "year_total_lower_bound"
    guarded = dict(statistic)
    guarded[APP_STAT_TOTAL_GENERATION] = round(corrected, 2)
    guarded[_LIFETIME_GUARD_KEY] = {
        "method": method,
        "corrected": {
            APP_STAT_TOTAL_GENERATION: {
                "raw_total": _rounded(raw_generation),
                "corrected_total": round(corrected, 2),
                "current_year_total": _rounded(year_generation),
                "previous_total": _rounded(previous_generation),
            }
        },
    }
    raw_carbon = safe_float(statistic.get(APP_STAT_TOTAL_CARBON))
    if raw_generation and raw_generation > 0 and raw_carbon is not None:
        factor = raw_carbon / raw_generation
        if 0 <= factor <= _MAX_CARBON_FACTOR:
            guarded[APP_STAT_TOTAL_CARBON] = round(corrected * factor, 2)
    payload[PAYLOAD_STATISTIC] = guarded


def guard_total_increasing_jitter(
    previous: StateType,
    current: StateType,
    description: SensorEntityDescription,
) -> StateType:
    """Hold lifetime energy counter regressions until a new entity is created."""
    if (
        description.device_class != SensorDeviceClass.ENERGY
        or description.state_class != SensorStateClass.TOTAL_INCREASING
    ):
        return current
    previous_number = safe_float(previous)
    current_number = safe_float(current)
    if previous_number is None:
        return current
    if current_number is None or current_number < previous_number:
        return previous
    return current


def period_data_offset(begin_iso: str | None, period_start: date) -> int:
    """Compare a cloud period's begin date with the local period start.

    Returns ``-1`` while the cloud still serves an earlier period (stale after
    a reset), ``1`` when it already serves a later one, and ``0`` when both
    match or the begin date is unknown. A period sensor must not publish a
    value of another period, or HA books it under the wrong ``last_reset``.
    """
    if begin_iso is None:
        return 0
    try:
        begin = date.fromisoformat(begin_iso)
    except ValueError:
        return 0
    return (begin > period_start) - (begin < period_start)
