"""Central payload ingestion gate for the Jackery SolarVault integration.

Every transport — HTTP/REST polling, cloud MQTT push, local MQTT and BLE —
funnels its decoded payloads through this single module so the data path is
identical regardless of how a frame arrived. The ingest path enforces one
merge rule:

* **Live device-property fields** are merged with :func:`merge_live_properties`,
  which never blanks a populated field with an empty/``None`` value. A sparse
  push frame (MQTT/BLE) can refresh or add fields but can never wipe the live
  values another transport (the HTTP poll) already delivered. This is what keeps
  live state stable when MQTT/BLE are active.
Periodic long-term values are identified by section prefix so the coordinator
can route them to the HA recorder without mixing them into live state. The
classification never authorizes dropping a decoded section or field.

The gate holds no Home Assistant dependencies and performs no transport I/O; it
is pure data normalization so it stays unit-testable and reusable by every
transport layer.

The non-blank merge rule is deliberately connection-neutral: HTTP, cloud MQTT,
local MQTT and BLE may all update or fill the same live field. Per-field source
provenance only protects a fresh local BLE/MQTT live value from being reversed
by an immediate cloud fallback snapshot; it never stops independent transports
from continuing to publish.
"""

import logging
import time
from typing import TYPE_CHECKING, Any, Final

from .const import (
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
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_STATISTIC,
)
from .models import DataSource, FieldProvenance, IngestResult, ProvenanceKey

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .models import Observation

_LOGGER = logging.getLogger(__name__)

# Compatibility name retained for existing transport decoders. New code should
# use ``DataSource`` so the source type is shared with Observation/IngestResult.
TransportSource = DataSource


# docs/AGENTS.md §1.2 keeps HTTP cloud data active as the complete fallback,
# treats every Layer-5 connection as an independent, equal live-data peer.
_LIVE_SOURCE_TIER: Final[dict[DataSource, int]] = {
    DataSource.HTTP: 0,
    DataSource.CLOUD_MQTT: 1,
    DataSource.LOCAL_MQTT: 1,
    DataSource.BLE: 1,
}


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

_PERIOD_TOTAL_TOLERANCE_KWH: Final = 0.00001


def local_period_total_supersedes_cloud(
    cloud_total: float | None,
    local_total: float | None,
    *,
    tolerance: float = _PERIOD_TOTAL_TOLERANCE_KWH,
) -> bool:
    """Return whether a verified local period delta exposes a lagging cloud total.

    Jackery's current-period HTTP/App statistics can remain on an earlier partial
    snapshot while the device lifetime counters already contain the complete
    same-period energy. A positive local delta may therefore replace a missing,
    zero or strictly smaller cloud total. It never lowers a cloud value and it
    never turns an unconfirmed local zero into observed energy.

    Both values must already use the same unit (kWh at every current caller).
    """
    if local_total is None or local_total <= 0:
        return False
    if cloud_total is None:
        return True
    return local_total > cloud_total + max(0.0, tolerance)


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


def _provenance_keeps_current(
    current_value: object,
    current_provenance: FieldProvenance | None,
    incoming: FieldProvenance,
    *,
    received_at: float,
    freshness_window_seconds: float,
) -> bool:
    """Return whether provenance protects one populated live value."""
    if current_provenance is None or _is_blankable(current_value):
        return False
    current_tier = _LIVE_SOURCE_TIER[current_provenance.source]
    incoming_tier = _LIVE_SOURCE_TIER[incoming.source]
    current_age = max(
        0.0,
        received_at - current_provenance.received_at_monotonic,
    )
    incoming_is_older = (
        incoming.observed_at is not None
        and current_provenance.observed_at is not None
        and incoming.observed_at < current_provenance.observed_at
    )
    return (
        incoming_tier < current_tier and current_age < freshness_window_seconds
    ) or incoming_is_older


def _merge_nested_with_provenance(
    current: dict[str, Any],
    update: dict[str, Any],
    *,
    path: tuple[str, ...],
    fallback_provenance: FieldProvenance | None,
    provenance: dict[ProvenanceKey, FieldProvenance],
    incoming: FieldProvenance,
    received_at: float,
    freshness_window_seconds: float,
) -> tuple[dict[str, Any], bool]:
    """Merge a nested live mapping while retaining ownership per field path."""
    merged = dict(current)
    accepted = False
    for key, value in update.items():
        field_path = (*path, key)
        current_value = merged.get(key)
        field_provenance = provenance.get(field_path, fallback_provenance)
        if isinstance(current_value, dict) and isinstance(value, dict):
            nested, nested_accepted = _merge_nested_with_provenance(
                current_value,
                value,
                path=field_path,
                fallback_provenance=field_provenance,
                provenance=provenance,
                incoming=incoming,
                received_at=received_at,
                freshness_window_seconds=freshness_window_seconds,
            )
            if nested_accepted:
                merged[key] = nested
                accepted = True
            continue
        if _is_blankable(value) and not _is_blankable(current_value):
            continue
        if _provenance_keeps_current(
            current_value,
            field_provenance,
            incoming,
            received_at=received_at,
            freshness_window_seconds=freshness_window_seconds,
        ):
            continue
        if isinstance(value, dict):
            _drop_descendant_provenance(provenance, field_path)
            nested, _ = _merge_nested_with_provenance(
                {},
                value,
                path=field_path,
                fallback_provenance=None,
                provenance=provenance,
                incoming=incoming,
                received_at=received_at,
                freshness_window_seconds=freshness_window_seconds,
            )
            merged[key] = nested
            provenance[field_path] = incoming
            accepted = True
            continue
        if isinstance(current_value, dict):
            _drop_descendant_provenance(provenance, field_path)
        merged[key] = value
        provenance[field_path] = incoming
        accepted = True
    return merged, accepted


def _drop_descendant_provenance(
    provenance: dict[ProvenanceKey, FieldProvenance],
    field: ProvenanceKey,
) -> None:
    """Remove nested ownership after a payload shape change."""
    prefix = field if isinstance(field, tuple) else (field,)
    for key in tuple(provenance):
        if isinstance(key, tuple) and key[: len(prefix)] == prefix:
            provenance.pop(key, None)


def ingest_observation(
    observation: Observation,
    *,
    current: Mapping[str, Any],
    provenance: Mapping[ProvenanceKey, FieldProvenance],
    received_at_monotonic: float | None = None,
    freshness_window_seconds: float = 0.0,
) -> IngestResult:
    """Apply one decoded observation with per-field provenance.

    The returned payload contains protocol data only. Source, timestamps and
    request identifiers are retained solely in the parallel ``provenance``
    mapping, so Home Assistant entities never expose ingest bookkeeping.
    Neither input mapping is mutated. Layer-5 live fields remain authoritative
    over the HTTP fallback for ``freshness_window_seconds``. Equal-tier
    transports update in arrival order when no protocol timestamp is available,
    so BLE, cloud MQTT and local MQTT remain independent peers. When both
    observations carry trustworthy timestamps, an older frame can never reverse
    a newer live value, regardless of which independent transport delivered it.
    Sparse lower-tier dictionaries may fill missing nested fields but cannot
    reverse a fresh live value.
    """
    received_at = received_at_monotonic
    if received_at is None:
        received_at = observation.received_at_monotonic
    if received_at is None:
        received_at = time.monotonic()

    merged = dict(current)
    updated_provenance = dict(provenance)
    accepted_fields: set[str] = set()

    for field, value in observation.payload.items():
        incoming = FieldProvenance(
            source=observation.source,
            section=observation.section,
            observed_at=observation.observed_at,
            received_at_monotonic=received_at,
            request_id=observation.request_id,
        )
        current_value = merged.get(field)
        current_provenance = updated_provenance.get(field)
        keep_current = _provenance_keeps_current(
            current_value,
            current_provenance,
            incoming,
            received_at=received_at,
            freshness_window_seconds=freshness_window_seconds,
        )

        if keep_current:
            if isinstance(current_value, dict) and isinstance(value, dict):
                assert current_provenance is not None
                # HTTP remains a complete independent fallback: while a fresh
                # L5 dictionary owns overlapping live keys, HTTP may still fill
                # fields that the sparse L5 frame did not contain.
                supplemented, supplemented_fields = _merge_nested_with_provenance(
                    current_value,
                    value,
                    path=(field,),
                    fallback_provenance=current_provenance,
                    provenance=updated_provenance,
                    incoming=incoming,
                    received_at=received_at,
                    freshness_window_seconds=freshness_window_seconds,
                )
                if supplemented_fields:
                    merged[field] = supplemented
                    accepted_fields.add(field)
            continue
        if isinstance(current_value, dict) and isinstance(value, dict):
            merged_value, nested_accepted = _merge_nested_with_provenance(
                current_value,
                value,
                path=(field,),
                fallback_provenance=current_provenance,
                provenance=updated_provenance,
                incoming=incoming,
                received_at=received_at,
                freshness_window_seconds=freshness_window_seconds,
            )
            if not nested_accepted:
                continue
            merged[field] = merged_value
        elif isinstance(value, dict):
            _drop_descendant_provenance(updated_provenance, field)
            merged_value, _ = _merge_nested_with_provenance(
                {},
                value,
                path=(field,),
                fallback_provenance=None,
                provenance=updated_provenance,
                incoming=incoming,
                received_at=received_at,
                freshness_window_seconds=freshness_window_seconds,
            )
            merged[field] = merged_value
        else:
            _drop_descendant_provenance(updated_provenance, field)
            merged[field] = value
        updated_provenance[field] = incoming
        accepted_fields.add(field)

    return IngestResult(
        payload=merged,
        provenance=updated_provenance,
        accepted_fields=frozenset(accepted_fields),
    )


def allow_periodic_section_from_source(
    source: TransportSource,
    section_key: str,
) -> bool:
    """Return whether a source may feed a periodic stat/trend section.

    The source argument is retained for decoder-call compatibility. A decoded
    periodic section is not discarded merely because of its transport.
    """
    del source
    return is_periodic_section(section_key)
