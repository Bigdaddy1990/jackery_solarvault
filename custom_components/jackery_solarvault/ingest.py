"""Central payload ingestion gate for the Jackery SolarVault integration.

Every transport — HTTP/REST polling, cloud MQTT push, local MQTT and BLE —
funnels its decoded payloads through this single module so the data path is
identical regardless of how a frame arrived. The gate enforces two rules:

* **Live device-property fields** are merged with :func:`merge_live_properties`,
  which never blanks a populated field with an empty/``None`` value. A sparse
  push frame (MQTT/BLE) can refresh or add fields but can never wipe the live
  values another transport (the HTTP poll) already delivered. This is what keeps
  live state stable when MQTT/BLE are active.
* **Periodic long-term values** (cumulative energy stat/trend sections) are
  identified by section prefix so the coordinator routes them to the HA recorder
  instead of mixing them into live state. :func:`gate_payload_section` is the
  per-section quality gate that decides whether a decoded periodic section may
  reach live state or the recorder at all.

This module is also the ONLY place allowed to drop payload data, and only after
an explicit plausibility check. Any other silent drop — a bare ``except: pass``,
a ``contextlib.suppress`` around a decode, an exception swallowed on an ingress
path — is a bug, not a design choice: it makes data vanish with no trace and no
diagnosis (owner rule, restated 2026-07-17).

The gate holds no Home Assistant dependencies and performs no transport I/O; it
is pure data normalization so it stays unit-testable and reusable by every
transport layer.

Restored from the 0.1.0 baseline: this module had been deleted and only its
inert parts (``TransportSource``, ``is_periodic_section``) copied into
``coordinator.py``. :func:`merge_live_properties` — the actual protection rule,
and the one ``docs/DATA_SOURCE_PRIORITY.md`` declares mandatory for every merge
— was dropped entirely, leaving nothing to stop a sparse or empty frame from
overwriting good live values. The periodic quality gate itself was later
extracted back here so the filter logic lives with the ingest boundary it
protects rather than in the coordinator.
"""

from enum import StrEnum
import logging
import math
from typing import Any, Final

from .const import (
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


class TransportSource(StrEnum):
    """Origin transport of an ingested payload."""

    HTTP = "http"
    CLOUD_MQTT = "cloud_mqtt"
    LOCAL_MQTT = "local_mqtt"
    BLE = "ble"


#: Section-key prefixes that carry periodic (long-term) statistics/trends.
#: Everything else in a device payload is treated as live property state.
#: Superset of the 0.1.0 list: the EPS/socket/symmetry/statistic sections are
#: real additions and are kept.
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
    APP_SECTION_PV_TRENDS,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_BATTERY_TRENDS,
})


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


#: Periodic (kWh) sections whose chart ``y`` series MAY legitimately carry
#: negative samples and are therefore exempt from the energy-series negative
#: guard. The symmetry section's ``n``/``totalN`` branch is a documented
#: signed net-flow convention. The battery stat/trend sections carry a
#: combined signed charge/discharge curve on ``y1`` (positive = charge,
#: negative = discharge) that ``day_power_series_key``/
#: ``_is_signed_battery_energy_curve`` (util.py) detect by checking ``y1`` for
#: negative samples; scrubbing them here left that detector permanently
#: unreachable and degraded ``battery_discharge_energy`` day-stats to a single
#: flat bucket (F-SWEEP-7). The home-stat section (``device/stat/onGrid``)
#: carries the same convention: ``totalOutGridEnergy`` (energy exported back
#: to the grid) was observed arriving as negative on the owner's live device
#: — scrubbing it here drops real net-export samples. Every OTHER periodic
#: section reaching the ingest gate carries produced/consumed kWh energy,
#: where a negative bucket is always a bug (AGENTS.md §2.2 rule 1) and must
#: never reach the Recorder.
#:
#: CT_STAT (``device/stat/ct``) and EPS_STAT (``device/stat/eps``) were
#: investigated for the same exemption: both share the home-stat DTO shape
#: (separate ``totalIn*``/``totalOut*`` counters over an ``x``/``y``/``y1``/
#: ``y2`` chart — Jackery_2.1.1_Stats_und_Trends.md §2) and CT's
#: ``totalOutCtEnergy`` is documented as the same "public grid export"
#: quantity as home-stat's ``totalOutGridEnergy`` (DATA_SOURCE_PRIORITY.md).
#: That is circumstantial, not proof: unlike home-stat, no negative CT or EPS
#: sample has been observed in any captured payload/diagnostics dump for this
#: account (checked `logs/*.jsonl`, `logs/config_entry-*.json`). EPS is also
#: a switched backup-power circuit, not an inherently bidirectional grid tie,
#: so the CT reasoning does not transfer to it. Left scrubbed pending an
#: actual live-observed negative sample; do not add either prefix here
#: without one — see tests/test_ingest_negative_energy_guard.py.
_NEGATIVE_SERIES_EXEMPT_PREFIXES: frozenset[str] = frozenset({
    APP_SECTION_SYMMETRY_STAT,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_BATTERY_TRENDS,
    APP_SECTION_HOME_STAT,
})

#: Sections the ingest gate must pass through UNFILTERED (owner directive
#: 2026-07-25): CT values originate in the Shelly cloud, not in Jackery's
#: aggregation, and are delivered correct. Ingest has no business touching
#: them — no zero-drop, no series scrub, no hierarchy withholding. The
#: transport rule (HTTP owns periodic sections) still applies upstream.
_INGEST_EXEMPT_PREFIXES: frozenset[str] = frozenset({
    APP_SECTION_CT_STAT,
})

#: Chart series keys scanned for negative energy samples in periodic sections.
_CHART_SERIES_KEYS: frozenset[str] = frozenset({
    APP_CHART_SERIES_Y,
    APP_CHART_SERIES_Y1,
    APP_CHART_SERIES_Y2,
    APP_CHART_SERIES_Y3,
    APP_CHART_SERIES_Y4,
    APP_CHART_SERIES_Y5,
    APP_CHART_SERIES_Y6,
})


#: ``device_statistic`` day counters and the ``statistic``
#: (systemStatistic) today counters that can cross-confirm their zeros.
#: Source: types.py SystemStatistic DTO / /v1/device/stat/systemStatistic.
_DEVICE_STATISTIC_ZERO_CONFIRMATION: Final[dict[str, str]] = {
    APP_DEVICE_STAT_PV_ENERGY: APP_STAT_TODAY_GENERATION,
    APP_DEVICE_STAT_BATTERY_CHARGE: APP_STAT_TODAY_BATTERY_CHARGE,
    APP_DEVICE_STAT_BATTERY_DISCHARGE: APP_STAT_TODAY_BATTERY_DISCHARGE,
}


def is_periodic_section(section_key: str) -> bool:
    """Return True when a payload section holds periodic long-term values.

    Matches both the bare prefix and the ``{prefix}_{date_type}`` section keys
    (e.g. ``device_pv_stat_day``) the coordinator stores per period.

    Args:
        section_key: Payload section identifier to test.

    Returns:
        True when the section carries periodic data, False for live state.
    """
    return any(
        section_key == prefix or section_key.startswith(f"{prefix}_")
        for prefix in PERIODIC_SECTION_PREFIXES
    )


def _is_blankable(value: object) -> bool:
    """Return True for values that must never overwrite a populated field."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return isinstance(value, (list, dict)) and not value


def merge_live_properties(
    base: dict[str, Any],
    update: dict[str, Any],
) -> dict[str, Any]:
    """Merge live device-property fields without ever blanking populated keys.

    Recursive, update-wins merge with one rule a plain dict merge lacks: an
    empty/``None`` value in ``update`` never replaces a populated value in
    ``base``. A sparse MQTT/BLE frame (omitting or nulling fields the device did
    not report this tick) therefore cannot wipe the live picture delivered by
    another transport such as the HTTP property poll.

    Args:
        base: The live properties already held for the device.
        update: The newly ingested frame's properties.

    Returns:
        A new merged mapping; neither input is modified.
    """
    merged: dict[str, Any] = dict(base)
    for key, value in update.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_live_properties(current, value)
        elif _is_blankable(value) and not _is_blankable(current):
            continue
        else:
            merged[key] = value
    return merged


def allow_periodic_section_from_source(
    source: TransportSource,
    section_key: str,
) -> bool:
    """Return whether a source may feed a periodic stat/trend section.

    Live device-property sections are intentionally not accepted here; they
    bypass ingest entirely and are merged/displayed directly.
    """
    return is_periodic_section(section_key) and source is TransportSource.HTTP


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
    * chart ``y``-series arrays in every periodic section except the ones
      listed in :data:`_NEGATIVE_SERIES_EXEMPT_PREFIXES` — individual negative
      samples are replaced with ``None`` so the position is preserved as a gap
      rather than a falsified magnitude, matching how sparse buckets already
      arrive.

    EPS/CT directional fields are not exempt: their ``y``-series still carry
    energy magnitudes, so a negative sample there is a bug. The symmetry
    ``n``/``totalN`` branch, the battery charge/discharge signed curve, and
    the home-stat/onGrid signed net-export curve
    (:data:`_NEGATIVE_SERIES_EXEMPT_PREFIXES`) are documented signed
    conventions and are intentionally left untouched. Rejections stay silent
    in this hot path so persistent bad cloud data cannot flood HA logs.
    """
    # The chart-series guard applies to every periodic (kWh) section except
    # those in _NEGATIVE_SERIES_EXEMPT_PREFIXES: in the ingest path all other
    # series carry energy magnitudes, so a negative bucket is a bug regardless
    # of whether it is a PV, EPS or CT stat/trend curve.
    # Previously only PV sections were filtered, so negative grid/CT energy
    # samples leaked into the Recorder as negative statistics.
    filter_energy_series = not _section_has_prefix(
        section_key, _NEGATIVE_SERIES_EXEMPT_PREFIXES
    )
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if key in GENERATION_SCALAR_FIELDS:
            number = _numeric_value(value)
            if number is not None and number < 0:
                continue
            sanitized[key] = value
            continue
        if (
            filter_energy_series
            and key in _CHART_SERIES_KEYS
            and isinstance(value, list)
        ):
            sanitized[key] = _filter_negative_series_samples(value)
            continue
        sanitized[key] = value
    return sanitized


def _filter_negative_series_samples(series: list[Any]) -> list[Any]:
    """Replace negative numeric samples in a PV chart series with ``None``."""
    cleaned: list[Any] = []
    for sample in series:
        number = _numeric_value(sample)
        if number is not None and number < 0:
            cleaned.append(None)
            continue
        cleaned.append(sample)
    return cleaned


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
    for_recorder: bool = True,
) -> dict[str, Any]:
    """Gate a decoded periodic payload section before state/recorder use.

    The unconfirmed-zero drop (AGENTS.md §2.2 rule 7) protects the Recorder
    and statistics import from zero-wipes. Live entity ingest passes
    ``for_recorder=False`` so genuine cloud zeros still reach the sensors —
    live values fire directly and are never withheld by this gate. CT
    sections (:data:`_INGEST_EXEMPT_PREFIXES`) pass through unfiltered.
    """
    if not allow_periodic_section_from_source(source, section_key):
        return {}
    if _section_has_prefix(section_key, _INGEST_EXEMPT_PREFIXES):
        return dict(payload)
    if for_recorder and _is_unconfirmed_zero_period_payload(  # ruff:ignore[collapsible-if]
        section_key, payload
    ):
        if not _zero_period_payload_confirmed(
            section_key, payload, confirmation_source
        ):
            _LOGGER.debug(
                "Dropping unconfirmed zero-only period payload for section %s "
                "(no sibling confirmation)",
                section_key,
            )
            return {}
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
        if _section_has_prefix(section_key, _INGEST_EXEMPT_PREFIXES):
            gated[section_key] = value
            continue
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
        except ValueError:
            return None
    else:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _walk_numeric_values(value: object) -> list[float]:
    """Collect finite numeric values from nested payload containers."""
    number = _numeric_value(value)
    if number is not None:
        return [number]
    if isinstance(value, dict | list):
        values: list[float] = []
        items = value.values() if isinstance(value, dict) else value
        for item in items:
            values.extend(_walk_numeric_values(item))
        return values
    return []


def _has_populated_series(value: object) -> bool:
    """Return whether a chart/list contains a finite non-zero sample."""
    if isinstance(value, list):
        return any(
            (number := _numeric_value(item)) is not None
            and not math.isclose(number, 0.0)
            for item in value
        )
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
        numbers.extend(_walk_numeric_values(value))

    if not numbers or any(not math.isclose(number, 0.0) for number in numbers):
        return False
    return not has_populated_series
