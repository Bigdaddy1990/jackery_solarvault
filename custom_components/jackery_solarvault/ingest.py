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
provenance protects fresh BLE live values from cloud fallback snapshots. Local
MQTT publishers are unauthenticated, so local and cloud MQTT share a tier and
update in arrival order; independent transports continue to publish.
"""

import copy
from dataclasses import dataclass
from datetime import UTC, datetime
import math
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
    APP_SECTION_TODAY_ENERGY,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    FIELD_PV1,
    FIELD_PV2,
    FIELD_PV3,
    FIELD_PV4,
    FIELD_PV_PW,
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_THIRD_PARTY_MQTT_IP,
    FIELD_THIRD_PARTY_MQTT_PASSWORD,
    FIELD_THIRD_PARTY_MQTT_PORT,
    FIELD_THIRD_PARTY_MQTT_TOKEN,
    FIELD_THIRD_PARTY_MQTT_USERNAME,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_STATISTIC,
    SUBDEVICE_ONLY_PROPERTY_KEYS,
)
from .models import DataSource, FieldProvenance, IngestResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .models import Observation, ProvenanceKey

# Compatibility name retained for existing transport decoders. New code should
# use ``DataSource`` so the source type is shared with Observation/IngestResult.
TransportSource = DataSource


_LIVE_SOURCE_TIER: Final[dict[DataSource, int]] = {
    DataSource.HTTP: 0,
    DataSource.CLOUD_MQTT: 1,
    DataSource.LOCAL_MQTT: 2,
    DataSource.BLE: 2,
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
_PV_CHANNEL_ENERGY_KEYS: Final = frozenset({
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
})
_PV_GENERATION_ENERGY_KEYS: Final = _PV_CHANNEL_ENERGY_KEYS | frozenset({
    APP_STAT_TOTAL_SOLAR_ENERGY,
})


def is_pv_generation_metric(section: str, stat_key: str) -> bool:
    """Identify physical PV generation, not signed grid or battery flows."""
    return stat_key in _PV_GENERATION_ENERGY_KEYS and (
        section in {APP_SECTION_PV_STAT, APP_SECTION_PV_TRENDS}
        or section.startswith((
            f"{APP_SECTION_PV_STAT}_",
            f"{APP_SECTION_PV_TRENDS}_",
        ))
    )


def pv_channel_exceeds_total(value: float | None, total: float | None) -> bool:
    """Identify a channel observation that cannot describe its whole PV period."""
    return (
        value is not None
        and total is not None
        and total >= 0
        and value > total + max(0.05, total * 0.05)
    )


def _period_energy_number(value: object) -> float | None:
    """Parse finite API energy without introducing a unit conversion."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, str) and "." not in value and value.count(",") == 1:
        value = value.replace(",", ".")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


# Canonical payload-to-entity projection. Complete, unredacted input is kept
# in coordinator.layer_source_payloads() BEFORE this projection is applied.
# Non-head fields are NOT discarded from the received observation: the owner
# sections (accessories, third-party config and periodic statistics) own them.
LIVE_PROPERTY_ALIAS_PAIRS: Final = (
    ("inPw", "inPower"),
    ("outPw", "outPower"),
    ("elecFreq", "frequency"),
    ("soc", "batterySoc"),
    ("standbyPw", "standbyw"),
)
_MAIN_NON_PROPERTY_KEYS: Final = SUBDEVICE_ONLY_PROPERTY_KEYS | {
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_THIRD_PARTY_MQTT_IP,
    FIELD_THIRD_PARTY_MQTT_PORT,
    FIELD_THIRD_PARTY_MQTT_USERNAME,
    FIELD_THIRD_PARTY_MQTT_PASSWORD,
    FIELD_THIRD_PARTY_MQTT_TOKEN,
}
_PV_PROPERTY_KEYS: Final = (FIELD_PV1, FIELD_PV2, FIELD_PV3, FIELD_PV4)


def sync_property_aliases(
    values: dict[str, Any],
    alias_pairs: tuple[tuple[str, str], ...] | list[tuple[str, str]],
) -> dict[str, Any]:
    """Keep both observed names for documented equivalent property aliases."""
    result = dict(values)
    for left, right in alias_pairs:
        a, b = result.get(left), result.get(right)
        if a is None and b is not None:
            result[left] = b
        elif b is None and a is not None:
            result[right] = a
    return result


def sanitize_main_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """Project *already retained* raw input into main-head entity properties.

    This is NOT an ingress filter: raw, unredacted fields are captured before
    calling this function. Belonging to a period, accessory or MQTT broker
    does not authorize treating the field as a main device measurement.
    Unrecognized fields of the correct section remain intact.
    """
    projected = {
        key: value
        for key, value in properties.items()
        if key not in _MAIN_NON_PROPERTY_KEYS
        and not is_periodic_section(key)
        and key != APP_SECTION_TODAY_ENERGY
        and not key.startswith(f"{APP_SECTION_TODAY_ENERGY}_")
    }
    for key in _PV_PROPERTY_KEYS:
        power = projected.get(key)
        if (
            not isinstance(power, dict | bool | type(None))
            and _period_energy_number(power) is not None
        ):
            projected[key] = {FIELD_PV_PW: power}
    return sync_property_aliases(projected, LIVE_PROPERTY_ALIAS_PAIRS)


def pv_channel_scalar_is_lifetime_offset(
    source: Mapping[str, Any], section: str, stat_key: str
) -> bool:
    """Classify the documented carried PV offsets at the normalization boundary."""
    return (
        section.startswith(f"{APP_SECTION_PV_STAT}_")
        and stat_key in _PV_CHANNEL_ENERGY_KEYS
        and pv_channel_exceeds_total(
            _period_energy_number(source.get(stat_key)),
            _period_energy_number(source.get(APP_STAT_TOTAL_SOLAR_ENERGY)),
        )
    )


def normalize_pv_period_energy(
    source: Mapping[str, Any],
    section: str,
    stat_key: str,
    *,
    values: list[float | None] | None,
    total_values: list[float | None] | None,
) -> tuple[list[float | None] | None, float | None, float | None]:
    """Normalize channel-period energy, preserving the original wire mapping.

    The caller supplies calendar-aligned parsed series in the source unit.
    A carried scalar may be recovered only from a complete own series. A
    contradictory bucket stays unknown; it is never rescaled or filled with zero.
    """
    scalar = _period_energy_number(source.get(stat_key))
    pv_generation = is_pv_generation_metric(section, stat_key)
    if pv_generation and scalar is not None and scalar < 0:
        # Retain the original wire value in source. A negative production
        # total is invalid evidence, never a real 0-kWh observation.
        scalar = None
    invalid_negative_bucket = False
    if pv_generation and values is not None:
        invalid_negative_bucket = any(
            value is not None and value < 0 for value in values
        )
        values = [
            None if value is not None and value < 0 else value for value in values
        ]
    channel = (
        section.startswith(f"{APP_SECTION_PV_STAT}_")
        and stat_key in _PV_CHANNEL_ENERGY_KEYS
    )
    if (
        channel
        and values is not None
        and total_values is not None
        and len(values) == len(total_values)
    ):
        clean_values: list[float | None] = []
        for value, total in zip(values, total_values, strict=True):
            if pv_channel_exceeds_total(value, total):
                clean_values.append(None)
            else:
                clean_values.append(value)
        values = clean_values
    chart_total = (
        round(sum(value for value in values if value is not None), 2)
        if values is not None and not invalid_negative_bucket
        else None
    )
    if pv_channel_scalar_is_lifetime_offset(source, section, stat_key):
        scalar = None
        if not values or any(value is None for value in values):
            chart_total = None
    return values, scalar, chart_total


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


def is_blank(value: object) -> bool:
    """Return True for values that must never overwrite a populated field."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return isinstance(value, (list, dict)) and not value


_DICT_LIST_ID_KEYS = frozenset({"devId", "deviceId", "id", "idx"})
_DICT_LIST_SERIAL_KEYS = frozenset({"devSn", "deviceSn", "sn"})


def _dict_list_identity_values(item: dict[str, Any]) -> frozenset[str]:
    """Return stable identity tokens for an incremental dict-list item."""
    identities: set[str] = set()
    for key in _DICT_LIST_SERIAL_KEYS:
        value = item.get(key)
        if not is_blank(value):
            identities.add(f"serial:{str(value).strip().casefold()}")
    for key in _DICT_LIST_ID_KEYS:
        value = item.get(key)
        if not is_blank(value):
            # Jackery's devId/deviceId are aliases for one device identifier.
            # Generic id and slot idx must retain their separate meanings.
            name = "device-id" if key in {"devId", "deviceId"} else key
            identities.add(f"{name}:{value}")
    return frozenset(identities)


def _clean_dict_list_update(update: dict[str, Any]) -> dict[str, Any]:
    """Drop blank values before appending a new sparse list item."""
    return {key: value for key, value in update.items() if not is_blank(value)}


def _without_reused_dict_list_ids(
    update: dict[str, Any],
    merged: list[dict[str, Any]],
    incoming_serials: set[str],
) -> dict[str, Any] | None:
    """Prevent serial/ID collisions without assigning unproven pack ownership."""
    result = dict(update)
    for item in merged:
        tokens = _dict_list_identity_values(item)
        serials = {token for token in tokens if token.startswith("serial:")}
        if len(serials) > 1 and incoming_serials & serials:
            # Ambiguous stored ownership cannot validate either serial alias.
            return None
        reused = {
            key
            for key in _DICT_LIST_ID_KEYS
            if key in result and _dict_list_identity_values({key: result[key]}) & tokens
        }
        if not reused:
            continue
        if not incoming_serials:
            # An ID-only push must not create a ghost beside a known pack.
            if serials:
                return None
            continue
        if incoming_serials & serials:
            continue
        # Even an old *unbound* row cannot certify a new serial's numeric ID.
        for key in reused:
            result.pop(key)
    return result


def _dict_list_update_candidates(
    current: list[Any],
    updates: list[Any],
) -> list[tuple[dict[str, Any], frozenset[str]]] | None:
    """Accept only identifiable sparse rows when current rows have known owners."""
    if not all(isinstance(item, dict) for item in current) or not all(
        isinstance(item, dict) for item in updates
    ):
        return None
    candidates = [
        (item, _dict_list_identity_values(item))
        for item in updates
        if isinstance(item, dict)
    ]
    if not candidates:
        return None
    if any(
        token.startswith("serial:")
        for item in current
        for token in _dict_list_identity_values(item)
    ):
        # A serial-owned physical-device list is never extended by ID-only
        # telemetry. Keep such frames in their original source diagnostics.
        return [
            (item, identities)
            for item, identities in candidates
            if any(token.startswith("serial:") for token in identities)
        ]
    if all(identities for _item, identities in candidates):
        return candidates
    # Anonymous generic lists are complete replacements, not sparse
    # physical-device messages. Only serial-owned lists above are protected.
    return None


def _merge_identified_dict_lists(
    current: list[Any],
    updates: list[Any],
) -> list[dict[str, Any]] | None:
    """Merge sparse identified rows without erasing established physical owners."""
    candidates = _dict_list_update_candidates(current, updates)
    if candidates is None:
        return None

    merged = [dict(item) for item in current if isinstance(item, dict)]
    for raw_update, original_tokens in candidates:
        # Physical serials take priority over a reused slot/device ID.
        incoming_serials = {
            token for token in original_tokens if token.startswith("serial:")
        }
        if len(incoming_serials) > 1:
            # Contradictory serial aliases must not update either physical pack.
            continue
        update = _without_reused_dict_list_ids(raw_update, merged, incoming_serials)
        if update is None:
            continue
        identities = _dict_list_identity_values(update)
        serial_matches: list[int] = []
        id_matches: list[int] = []
        for idx, item in enumerate(merged):
            existing = _dict_list_identity_values(item)
            if not identities & existing:
                continue
            existing_serials = {
                token for token in existing if token.startswith("serial:")
            }
            if incoming_serials or existing_serials:
                # A numeric ID cannot prove ownership of a serial-bound row.
                # Do not import legacy unbound values into a newly known pack.
                if incoming_serials & existing_serials:
                    serial_matches.append(idx)
                continue
            id_matches.append(idx)
        target_idx = (
            serial_matches[0]
            if serial_matches
            else id_matches[0]
            if id_matches
            else None
        )
        if target_idx is None:
            cleaned = _clean_dict_list_update(update)
            if cleaned:
                merged.append(cleaned)
            continue
        merged[target_idx] = merge_present_dict_values(
            merged[target_idx],
            update,
        )
    return merged


def merge_dict_values(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge nested dictionaries while preserving old keys."""
    merged = dict(base)
    for key, value in updates.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_dict_values(current, value)
        else:
            merged[key] = value
    return merged


def merge_present_dict_values(
    base: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Merge sparse live payloads without blanking populated existing values."""
    merged = dict(base)
    for key, value in updates.items():
        current = merged.get(key)
        if is_blank(value) and not is_blank(current):
            continue
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_present_dict_values(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            identified = _merge_identified_dict_lists(current, value)
            merged[key] = value if identified is None else identified
        else:
            merged[key] = value
    return merged


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
        elif is_blank(value) and not is_blank(current):
            continue
        else:
            merged[key] = value
    return merged


def _incoming_is_stale(
    incoming: FieldProvenance,
    freshness_window_seconds: float,
) -> bool:
    """Return whether a timestamped observation is older than the window."""
    # Observation guarantees a timezone-aware ``observed_at``.
    observed_at = incoming.observed_at
    if observed_at is None or freshness_window_seconds <= 0:
        return False
    age = (datetime.now(UTC) - observed_at).total_seconds()
    return age > freshness_window_seconds


def _priority_keeps_current(
    current: FieldProvenance,
    incoming: FieldProvenance,
    *,
    received_at: float,
    freshness_window_seconds: float,
) -> bool:
    """Compare accepted source histories at the later original receipt time."""
    current_age = max(0.0, received_at - current.received_at_monotonic)
    incoming_is_older = (
        incoming.observed_at is not None
        and current.observed_at is not None
        and incoming.observed_at < current.observed_at
    )
    return (
        _LIVE_SOURCE_TIER[incoming.source] < _LIVE_SOURCE_TIER[current.source]
        and current_age < freshness_window_seconds
    ) or incoming_is_older


def merge_selected_state(
    current: IngestResult,
    incoming: IngestResult,
    *,
    freshness_window_seconds: float,
) -> IngestResult:
    """Combine selected values and provenance without refreshing observations."""
    missing = object()
    provenance: dict[ProvenanceKey, FieldProvenance] = {}

    def provenance_key(path: tuple[str, ...]) -> ProvenanceKey:
        return path[0] if len(path) == 1 else path

    def incoming_wins(
        current_value: object,
        incoming_value: object,
        current_source: FieldProvenance | None,
        incoming_source: FieldProvenance | None,
    ) -> bool:
        if is_blank(incoming_value):
            return False
        if is_blank(current_value) or current_source is None:
            return True
        if incoming_source is None:
            return False
        current_time = current_source.received_at_monotonic
        incoming_time = incoming_source.received_at_monotonic
        if current_time == incoming_time:
            return not _priority_keeps_current(
                current_source,
                incoming_source,
                received_at=current_time,
                freshness_window_seconds=freshness_window_seconds,
            ) and _priority_keeps_current(
                incoming_source,
                current_source,
                received_at=current_time,
                freshness_window_seconds=freshness_window_seconds,
            )
        if incoming_time > current_time:
            return not _priority_keeps_current(
                current_source,
                incoming_source,
                received_at=incoming_time,
                freshness_window_seconds=freshness_window_seconds,
            )
        return _priority_keeps_current(
            incoming_source,
            current_source,
            received_at=current_time,
            freshness_window_seconds=freshness_window_seconds,
        )

    def retain(
        value: object,
        path: tuple[str, ...],
        state: IngestResult,
        inherited: FieldProvenance | None,
    ) -> object:
        key = provenance_key(path)
        source = state.provenance.get(key, inherited)
        if source is not None:
            provenance[key] = source
        if isinstance(value, dict):
            return {
                name: retain(child, (*path, name), state, source)
                for name, child in value.items()
            }
        return copy.deepcopy(value)

    def merge(
        current_value: object,
        incoming_value: object,
        path: tuple[str, ...],
        current_parent: FieldProvenance | None,
        incoming_parent: FieldProvenance | None,
    ) -> object:
        key = provenance_key(path)
        current_source = current.provenance.get(key, current_parent)
        incoming_source = incoming.provenance.get(key, incoming_parent)
        if current_value is missing:
            return retain(incoming_value, path, incoming, incoming_parent)
        if incoming_value is missing:
            return retain(current_value, path, current, current_parent)
        use_incoming = incoming_wins(
            current_value, incoming_value, current_source, incoming_source
        )
        if isinstance(current_value, dict) and isinstance(incoming_value, dict):
            source = incoming_source if use_incoming else current_source
            if source is not None:
                provenance[key] = source
            return {
                name: merge(
                    current_value.get(name, missing),
                    incoming_value.get(name, missing),
                    (*path, name),
                    current_source,
                    incoming_source,
                )
                for name in dict.fromkeys((*current_value, *incoming_value))
            }
        if use_incoming:
            return retain(incoming_value, path, incoming, incoming_parent)
        return retain(current_value, path, current, current_parent)

    payload = {
        name: merge(
            current.payload.get(name, missing),
            incoming.payload.get(name, missing),
            (name,),
            None,
            None,
        )
        for name in dict.fromkeys((*current.payload, *incoming.payload))
    }
    return IngestResult(
        payload=payload, provenance=provenance, accepted_fields=frozenset()
    )


def _provenance_keeps_current(
    current_value: object,
    current_provenance: FieldProvenance | None,
    incoming: FieldProvenance,
    *,
    received_at: float,
    freshness_window_seconds: float,
) -> bool:
    """Return whether provenance protects one populated live value.

    A timestamped live observation older than the freshness window (e.g. a
    retained broker frame from hours ago) never overwrites a populated value,
    but it still fills blank fields. Historical periodic sections are exempt.
    The decision is per field, never a whole-frame pre-filter.
    """
    if is_blank(current_value):
        return False
    if not is_periodic_section(incoming.section) and _incoming_is_stale(
        incoming, freshness_window_seconds
    ):
        return True
    if current_provenance is None:
        return False
    if (
        current_provenance.family in {"box", "portable", "home"}
        and incoming.family in {"box", "portable", "home"}
        and current_provenance.family != incoming.family
    ):
        return incoming.received_at_monotonic < current_provenance.received_at_monotonic
    return _priority_keeps_current(
        current_provenance,
        incoming,
        received_at=received_at,
        freshness_window_seconds=freshness_window_seconds,
    )


@dataclass(frozen=True, slots=True)
class _NestedMergeContext:
    """Shared ownership state for one recursive nested merge."""

    provenance: dict[ProvenanceKey, FieldProvenance]
    incoming: FieldProvenance
    received_at: float
    freshness_window_seconds: float


def _merge_nested_with_provenance(
    current: dict[str, Any],
    update: dict[str, Any],
    *,
    path: tuple[str, ...],
    fallback_provenance: FieldProvenance | None,
    context: _NestedMergeContext,
) -> tuple[dict[str, Any], bool]:
    """Merge a nested live mapping while retaining ownership per field path."""
    merged = dict(current)
    accepted = False
    for key, value in update.items():
        field_path = (*path, key)
        current_value = merged.get(key)
        field_provenance = context.provenance.get(field_path, fallback_provenance)
        if isinstance(current_value, dict) and isinstance(value, dict):
            nested, nested_accepted = _merge_nested_with_provenance(
                current_value,
                value,
                path=field_path,
                fallback_provenance=field_provenance,
                context=context,
            )
            if nested_accepted:
                merged[key] = nested
                accepted = True
            continue
        if is_blank(value) and not is_blank(current_value):
            continue
        if _provenance_keeps_current(
            current_value,
            field_provenance,
            context.incoming,
            received_at=context.received_at,
            freshness_window_seconds=context.freshness_window_seconds,
        ):
            continue
        if isinstance(value, dict):
            _drop_descendant_provenance(context.provenance, field_path)
            nested, _ = _merge_nested_with_provenance(
                {},
                value,
                path=field_path,
                fallback_provenance=None,
                context=context,
            )
            merged[key] = nested
            context.provenance[field_path] = context.incoming
            accepted = True
            continue
        if isinstance(current_value, dict):
            _drop_descendant_provenance(context.provenance, field_path)
        merged[key] = value
        context.provenance[field_path] = context.incoming
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


def ingest_observation(  # ruff: ignore[too-many-arguments]
    observation: Observation,
    *,
    current: Mapping[str, Any],
    provenance: Mapping[ProvenanceKey, FieldProvenance],
    received_at_monotonic: float | None = None,
    freshness_window_seconds: float = 0.0,
    authoritative_empty_fields: frozenset[str] = frozenset(),
    evaluation_monotonic: float | None = None,
) -> IngestResult:
    """Apply one decoded observation with per-field provenance.

    The returned payload contains protocol data only. Source, timestamps and
    request identifiers are retained solely in the parallel ``provenance``
    mapping, so Home Assistant entities never expose ingest bookkeeping.
    Neither input mapping is mutated. Fresh local MQTT and BLE fields take priority
    over cloud MQTT and HTTP for ``freshness_window_seconds``. Local peers retain
    their distinct transport identities. Equal-tier transports
    update in arrival order when no protocol timestamp is available. When both
    observations carry trustworthy timestamps, an older frame can never reverse
    a newer live value, regardless of which independent transport delivered it.
    Sparse lower-tier dictionaries may fill missing nested fields but cannot
    reverse a fresh live value.
    Cached dictionaries need not have source metadata: a stale first observation
    preserves their populated fields and may still supply missing nested values.
    Explicit None values in ``authoritative_empty_fields`` retain source priority.
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
            family=observation.family,
            device_identity=observation.device_identity,
            safety_owner=observation.safety_owner,
            safety_received_at=observation.safety_received_at,
            safety_received_monotonic=observation.safety_received_monotonic,
            safety_mqtt_delivery=observation.safety_mqtt_delivery,
        )
        merge_context = _NestedMergeContext(
            provenance=updated_provenance,
            incoming=incoming,
            received_at=(
                received_at if evaluation_monotonic is None else evaluation_monotonic
            ),
            freshness_window_seconds=freshness_window_seconds,
        )
        current_value = merged.get(field)
        current_provenance = updated_provenance.get(field)
        current_for_priority = (
            True
            if field in authoritative_empty_fields
            and field in current
            and current_value is None
            and current_provenance is not None
            else current_value
        )
        keep_current = _provenance_keeps_current(
            current_for_priority,
            current_provenance,
            incoming,
            received_at=(
                received_at if evaluation_monotonic is None else evaluation_monotonic
            ),
            freshness_window_seconds=freshness_window_seconds,
        )

        if keep_current:
            if isinstance(current_value, dict) and isinstance(value, dict):
                # HTTP remains a complete independent fallback: while a fresh
                # L5 dictionary owns overlapping live keys, HTTP may still fill
                # fields that the sparse L5 frame did not contain.
                supplemented, supplemented_fields = _merge_nested_with_provenance(
                    current_value,
                    value,
                    path=(field,),
                    fallback_provenance=current_provenance,
                    context=merge_context,
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
                context=merge_context,
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
                context=merge_context,
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
