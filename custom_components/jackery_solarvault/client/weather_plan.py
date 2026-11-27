"""Select Home and Portable weather events without losing alert identity."""

import copy
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ..const import FIELD_ALERT_ID, FIELD_STATUS, FIELD_STORM
from ..ingest import ingest_observation
from ..util import safe_int

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..models import FieldProvenance, Observation, ProvenanceKey

_SNAPSHOT = "snapshot"
_ALERT_PREFIX = "alert:"
_DELETED_STATUS = 2


@dataclass(frozen=True, slots=True)
class WeatherPlanOwner:
    """Bind a weather plan to a device family and observed parent identity."""

    family: str
    model: int | None
    parent_binding: tuple[tuple[object, ...] | None, int, str | None]
    data_identity: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], bool] | None


@dataclass(frozen=True, slots=True)
class WeatherPlanState:
    """Retain the selected rows and their source markers for one device owner."""

    owner: WeatherPlanOwner
    rows: list[Any] | None
    provenance: dict[ProvenanceKey, FieldProvenance]


def _alert_id(row: object) -> str | None:
    if not isinstance(row, dict):
        return None
    value = row.get(FIELD_ALERT_ID)
    return value if isinstance(value, str) and value else None


def _marker(alert_id: str) -> str:
    return f"{_ALERT_PREFIX}{alert_id}"


def _event_identity(update: Mapping[str, Any]) -> tuple[str, int] | None:
    alert_id = update.get(FIELD_ALERT_ID)
    raw_status = update.get(FIELD_STATUS)
    if (
        not isinstance(alert_id, str)
        or not alert_id
        or isinstance(raw_status, bool)
        or not isinstance(raw_status, int | float)
    ):
        return None
    status = safe_int(raw_status)
    return (alert_id, status) if status is not None else None


def is_weather_plan_event(update: Mapping[str, Any]) -> bool:
    """Recognize an authoritative list or an identified numeric status update."""
    return (
        isinstance(update.get(FIELD_STORM), list) or _event_identity(update) is not None
    )


def _accept(
    observation: Observation,
    fields: set[str],
    previous: dict[ProvenanceKey, FieldProvenance],
    freshness_window_seconds: float,
) -> tuple[set[str], dict[ProvenanceKey, FieldProvenance]]:
    provenance = dict(previous)
    snapshot = provenance.get(_SNAPSHOT)
    if snapshot is not None:
        for field in fields:
            if field != _SNAPSHOT:
                provenance.setdefault(field, snapshot)
    result = ingest_observation(
        replace(observation, payload=dict.fromkeys(fields, True)),
        current={key: True for key in provenance if isinstance(key, str)},
        provenance=provenance,
        freshness_window_seconds=freshness_window_seconds,
    )
    return set(result.accepted_fields), dict(result.provenance)


def _snapshot_rows(
    old_rows: list[Any], incoming: list[Any], protected: set[str]
) -> list[Any]:
    previous: dict[str, list[Any]] = {}
    for row in old_rows:
        if (alert_id := _alert_id(row)) in protected:
            previous.setdefault(alert_id, []).append(row)
    result: list[Any] = []
    emitted: set[str] = set()
    for row in incoming:
        alert_id = _alert_id(row)
        if alert_id in protected:
            if alert_id not in emitted:
                result.extend(copy.deepcopy(previous.get(alert_id, [])))
                emitted.add(alert_id)
        else:
            result.append(copy.deepcopy(row))
    for row in old_rows:
        alert_id = _alert_id(row)
        if alert_id in protected and alert_id not in emitted:
            result.extend(copy.deepcopy(previous[alert_id]))
            emitted.add(alert_id)
    return result


def _apply_snapshot(
    state: WeatherPlanState,
    rows: list[Any],
    observation: Observation,
    freshness_window_seconds: float,
) -> WeatherPlanState | None:
    old_rows = state.rows or []
    ids = {
        alert_id
        for row in (*old_rows, *rows)
        if (alert_id := _alert_id(row)) is not None
    }
    ids.update(
        key.removeprefix(_ALERT_PREFIX)
        for key in state.provenance
        if isinstance(key, str) and key.startswith(_ALERT_PREFIX)
    )
    accepted, provenance = _accept(
        observation,
        {_SNAPSHOT, *(_marker(alert_id) for alert_id in ids)},
        state.provenance,
        freshness_window_seconds,
    )
    if _SNAPSHOT not in accepted:
        return None
    protected = {alert_id for alert_id in ids if _marker(alert_id) not in accepted}
    selected = _snapshot_rows(old_rows, rows, protected)
    present = {alert_id for row in selected if (alert_id := _alert_id(row)) is not None}
    for alert_id in ids - present - protected:
        provenance.pop(_marker(alert_id), None)
    return WeatherPlanState(
        owner=state.owner,
        rows=selected,
        provenance=provenance,
    )


def _apply_alert(
    state: WeatherPlanState,
    alert_id: str,
    status: int,
    observation: Observation,
    freshness_window_seconds: float,
) -> WeatherPlanState | None:
    rows = state.rows
    matched = rows is not None and any(_alert_id(row) == alert_id for row in rows)
    marker = _marker(alert_id)
    if not matched and (status != _DELETED_STATUS or marker not in state.provenance):
        return None
    accepted, provenance = _accept(
        observation, {marker}, state.provenance, freshness_window_seconds
    )
    if marker not in accepted:
        return None
    selected = copy.deepcopy(rows)
    if selected is not None and status == _DELETED_STATUS:
        selected = [row for row in selected if _alert_id(row) != alert_id]
    elif selected is not None:
        for row in selected:
            if _alert_id(row) == alert_id:
                row[FIELD_STATUS] = status
                break
    return WeatherPlanState(
        owner=state.owner,
        rows=selected,
        provenance=provenance,
    )


def reconcile_weather_plan(
    previous: WeatherPlanState | None,
    owner: WeatherPlanOwner,
    observation: Observation,
    *,
    freshness_window_seconds: float,
) -> WeatherPlanState | None:
    """Apply one App weather event to its own owner and accepted source markers."""
    state = (
        previous
        if previous is not None and previous.owner == owner
        else WeatherPlanState(owner, None, {})
    )
    update = observation.payload
    rows = update.get(FIELD_STORM)
    if isinstance(rows, list):
        return _apply_snapshot(state, rows, observation, freshness_window_seconds)
    identity = _event_identity(update)
    if identity is None:
        return None
    return _apply_alert(state, *identity, observation, freshness_window_seconds)
