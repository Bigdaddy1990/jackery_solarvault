"""Stats/trends ingestion gate for the Jackery SolarVault integration.

Live device-property fields must bypass ingest entirely: HTTP/API is the
primary live path, while MQTT/BLE frames are incomplete supplemental telemetry.
Only periodic long-term values (cumulative energy stat/trend sections) enter
this module so broken cloud totals, empty buckets, and impossible PV generation
samples can be withheld before recorder use.

The gate holds no Home Assistant dependencies and performs no transport I/O; it
is pure stats/trends normalization so it stays unit-testable.
"""

from enum import StrEnum
import logging
import math
from typing import Any, Final

from ...const import (  # noqa: RUF100, TID252
    APP_CHART_SERIES_Y,
    APP_CHART_SERIES_Y1,
    APP_CHART_SERIES_Y2,
    APP_CHART_SERIES_Y3,
    APP_CHART_SERIES_Y4,
    APP_CHART_SERIES_Y5,
    APP_CHART_SERIES_Y6,
    APP_DEVICE_STAT_BATTERY_CHARGE,
    APP_DEVICE_STAT_BATTERY_DISCHARGE,
    APP_DEVICE_STAT_PV_ENERGY,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_BATTERY_TRENDS,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
    APP_SECTION_SOCKET_STAT,
    APP_SECTION_SYMMETRY_STAT,
    APP_SECTION_TODAY_ENERGY,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_TODAY_BATTERY_CHARGE,
    APP_STAT_TODAY_BATTERY_DISCHARGE,
    APP_STAT_TODAY_GENERATION,
    APP_STAT_TOTAL_GENERATION,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_STATISTIC,
)

_LOGGER = logging.getLogger(__name__)

#: Scalar field keys that carry pure PV / solar *generation* (produced energy).
#: Grounded in docs/source-of-truth/Jackery_2.1.1_Stats_und_Trends.md §4 glossary
#: (``pvEgy`` = PV-Energie, ``totalSolarEnergy``/``totalGeneration`` = Erzeugung)
#: and AGENTS.md §2.2 rule 1 (interval values must be >= 0). These are produced
#: energy magnitudes that can never be physically negative — a negative is a BUG.
#: Battery charge/discharge, net grid (in/out), EPS and CT directional fields are
#: deliberately excluded: they are out of GENERATION scope and the symmetry
#: ``n``/``totalN`` branch is a documented negative convention.
GENERATION_SCALAR_FIELDS: frozenset[str] = frozenset({
    APP_DEVICE_STAT_PV_ENERGY,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_TOTAL_GENERATION,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
})


#: Section prefixes whose chart ``y``/``y1``..``y6`` series are PV *generation*
#: curves (solar produced energy). Only PV stat/trends qualify: per the
#: source-of-truth chart glossary the battery/onGrid/eps/ct ``y`` series are
#: directional charge/discharge / in/out-grid magnitudes (out of scope), and the
#: symmetry section carries a documented negative ``n`` branch.
GENERATION_SECTION_PREFIXES: frozenset[str] = frozenset({
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
})

#: Chart series keys scanned for negative generation samples within PV sections.
_CHART_SERIES_KEYS: frozenset[str] = frozenset({
    APP_CHART_SERIES_Y,
    APP_CHART_SERIES_Y1,
    APP_CHART_SERIES_Y2,
    APP_CHART_SERIES_Y3,
    APP_CHART_SERIES_Y4,
    APP_CHART_SERIES_Y5,
    APP_CHART_SERIES_Y6,
})


class TransportSource(StrEnum):
    """Origin transport of an ingested payload."""

    HTTP = "http"
    CLOUD_MQTT = "cloud_mqtt"
    LOCAL_MQTT = "local_mqtt"
    BLE = "ble"


#: Section-key prefixes that carry periodic (long-term) statistics/trends.
#: Everything else in a device payload is treated as live property state.
PERIODIC_SECTION_PREFIXES: frozenset[str] = frozenset({
    PAYLOAD_STATISTIC,
    PAYLOAD_DEVICE_STATISTIC,
    APP_SECTION_PV_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_SOCKET_STAT,
    APP_SECTION_SYMMETRY_STAT,
    APP_SECTION_TODAY_ENERGY,
    APP_SECTION_PV_TRENDS,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_BATTERY_TRENDS,
})


def is_periodic_section(section_key: str) -> bool:
    """Determine whether a payload section contains periodic (long-term) data.

    Matches either a known periodic prefix exactly or keys that start with a recognized
    prefix followed by an underscore (for example, `device_pv_stat_day`).

    Parameters:
        section_key (str): Payload section identifier to test.

    Returns:
        `true` if the section_key represents a periodic section, `false` otherwise.
    """
    return any(
        section_key == prefix or section_key.startswith(f"{prefix}_")
        for prefix in PERIODIC_SECTION_PREFIXES
    )


def allow_periodic_section_from_source(
    source: TransportSource,
    section_key: str,
) -> bool:
    """Return whether a source may feed a periodic stat/trend section."""
    return not is_periodic_section(section_key) or source is TransportSource.HTTP


def _section_has_prefix(section_key: str, prefixes: frozenset[str]) -> bool:
    """Return whether a section key matches a prefix exactly or as ``prefix_*``."""
    return any(
        section_key == prefix or section_key.startswith(f"{prefix}_")
        for prefix in prefixes
    )


def _reject_negative_generation_section(
    section_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Drop negative PV/generation values from a periodic stat/trend section.

    Returns a new mapping with negative produced-energy values removed so they
    never reach the HA Recorder (AGENTS.md §1.1/§2.2 rule 1). Two field classes
    are filtered:

    * scalar :data:`GENERATION_SCALAR_FIELDS` (e.g. ``totalSolarEnergy``,
      ``pvEgy``) — a negative scalar is dropped entirely (the key is removed).
    * chart ``y``-series arrays, *only* in PV sections
      (:data:`GENERATION_SECTION_PREFIXES`) — individual negative samples are
      replaced with ``None`` so the position is preserved as a gap rather than
      a falsified magnitude, matching how sparse buckets already arrive.

    Battery/grid/EPS/CT directional fields and the symmetry ``n``/``totalN``
    branch are intentionally left untouched. Every rejection is logged at
    WARNING with the field and value.
    """
    is_generation_section = _section_has_prefix(
        section_key, GENERATION_SECTION_PREFIXES
    )
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if key in GENERATION_SCALAR_FIELDS:
            number = _numeric_value(value)
            if number is not None and number < 0:
                _LOGGER.warning(
                    "Rejecting negative generation value in section %s: %s=%r",
                    section_key,
                    key,
                    value,
                )
                continue
            sanitized[key] = value
            continue
        if (
            is_generation_section
            and key in _CHART_SERIES_KEYS
            and isinstance(value, list)
        ):
            sanitized[key] = _filter_negative_series_samples(section_key, key, value)
            continue
        sanitized[key] = value
    return sanitized


def _filter_negative_series_samples(
    section_key: str,
    series_key: str,
    series: list[Any],
) -> list[Any]:
    """Replace negative numeric samples in a PV chart series with ``None``."""
    cleaned: list[Any] = []
    for sample in series:
        number = _numeric_value(sample)
        if number is not None and number < 0:
            _LOGGER.warning(
                "Rejecting negative generation sample in section %s series %s: %r",
                section_key,
                series_key,
                sample,
            )
            cleaned.append(None)
            continue
        cleaned.append(sample)
    return cleaned


#: ``device_statistic`` day counters and the ``statistic``
#: (systemStatistic) today counters that can cross-confirm their zeros.
#: Source: types.py SystemStatistic DTO / /v1/device/stat/systemStatistic.
_DEVICE_STATISTIC_ZERO_CONFIRMATION: Final[dict[str, str]] = {
    APP_DEVICE_STAT_PV_ENERGY: APP_STAT_TODAY_GENERATION,
    APP_DEVICE_STAT_BATTERY_CHARGE: APP_STAT_TODAY_BATTERY_CHARGE,
    APP_DEVICE_STAT_BATTERY_DISCHARGE: APP_STAT_TODAY_BATTERY_DISCHARGE,
}


def _zero_period_payload_confirmed(
    section_key: str,
    payload: dict[str, Any],
    confirmation_source: dict[str, Any] | None,
) -> bool:
    """Return whether a zero-only period payload is confirmed by a sibling.

    AGENTS.md §2.2 rule 7: zero values are ignored unless confirmed by
    another source. A zero-only ``device_statistic`` payload is confirmed
    when EVERY numeric day counter it carries has a mapped sibling today
    counter in the ``statistic`` section of the same cycle that also
    reads zero (mutual validation is strict — one unmapped or non-zero
    sibling keeps the drop).
    """
    if section_key != PAYLOAD_DEVICE_STATISTIC or not confirmation_source:
        return False
    numeric_fields = [
        key for key, value in payload.items() if _numeric_value(value) is not None
    ]
    if not numeric_fields:
        return False
    for field in numeric_fields:
        sibling_key = _DEVICE_STATISTIC_ZERO_CONFIRMATION.get(field)
        if sibling_key is None:
            return False
        sibling_value = _numeric_value(confirmation_source.get(sibling_key))
        if sibling_value is None or not math.isclose(sibling_value, 0.0):
            return False
    return True


def gate_payload_section(
    source: TransportSource,
    section_key: str,
    payload: dict[str, Any],
    *,
    confirmation_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gate a decoded payload section before live-state or recorder use."""
    if not allow_periodic_section_from_source(source, section_key):
        return {}
    if _is_unconfirmed_zero_period_payload(section_key, payload):
        if not _zero_period_payload_confirmed(
            section_key, payload, confirmation_source
        ):
            _LOGGER.debug(
                "Dropping zero-only period payload for section %s "
                "(no sibling confirmation): %s",
                section_key,
                sorted(payload),
            )
            return {}
        _LOGGER.debug(
            "Zero-only period payload for section %s confirmed by sibling "
            "statistic today counters",
            section_key,
        )
    return _reject_negative_generation_section(section_key, dict(payload))


def gate_period_hierarchy_for_recorder(
    payload: dict[str, Any],
    violating_sections: frozenset[str],
) -> dict[str, Any]:
    """Drop period sections that break the AGENTS.md §2.2 period hierarchy.

    The cross-period monotonicity contract (``5min >= 0``, ``daily <= weekly``,
    ``weekly <= monthly``, ``monthly <= yearly``, ``yearly <= lifetime`` with
    ``yearly != 0`` and ``lifetime > 0``) can only be checked once every period
    section for a device is present, so it cannot be enforced by the
    per-section :func:`gate_payload_section`. This payload-level gate runs after
    the hierarchy has been evaluated upstream and removes the period sections
    whose total exceeds its legitimate longer-period container — the inflated /
    contradictory shorter period — so only validated period data reaches the HA
    Recorder.

    ``violating_sections`` are the section keys (for example
    ``device_pv_stat_week``) identified as exceeding their container. The input
    mapping is not mutated; a new mapping without those sections is returned. A
    section is matched exactly or as a recognized ``prefix_*`` period section so
    a single suspect total never leaks a falsified bucket curve into long-term
    statistics. When ``violating_sections`` is empty the payload is returned
    unchanged (shallow-copied).
    """
    if not violating_sections:
        return dict(payload)
    gated: dict[str, Any] = {}
    for section_key, value in payload.items():
        if section_key in violating_sections:
            _LOGGER.warning(
                "Withholding period section %s from recorder: violates the "
                "AGENTS.md §2.2 period hierarchy (shorter period exceeds its "
                "longer-period container)",
                section_key,
            )
            continue
        gated[section_key] = value
    return gated


def _numeric_value(value: object) -> float | None:
    """Return a finite numeric value when a payload item is number-like."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError as err:
            _LOGGER.debug("Non-numeric payload string %r: %s", value, err)
            return None
    else:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _walk_numeric_values(value: object) -> list[float]:
    """Collect finite numeric values from nested payload dictionaries."""
    number = _numeric_value(value)
    if number is not None:
        return [number]
    if isinstance(value, dict):
        values: list[float] = []
        for item in value.values():
            values.extend(_walk_numeric_values(item))
        return values
    return []


def _has_populated_series(value: object) -> bool:
    """Return whether a chart/list contains any finite numeric sample."""
    if isinstance(value, list):
        return any(_numeric_value(item) is not None for item in value)
    return False


def _is_unconfirmed_zero_period_payload(
    section_key: str,
    payload: dict[str, Any],
) -> bool:
    """Drop cloud success payloads that carry only unconfirmed zero totals."""
    if not is_periodic_section(section_key) or not payload:
        return False

    numbers: list[float] = []
    has_populated_series = False
    for value in payload.values():
        if isinstance(value, list):
            has_populated_series = has_populated_series or _has_populated_series(value)
            continue
        numbers.extend(_walk_numeric_values(value))

    if not numbers or any(not math.isclose(number, 0.0) for number in numbers):
        return False
    return not has_populated_series
