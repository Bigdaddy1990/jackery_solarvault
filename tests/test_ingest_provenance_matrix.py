"""Provenance and source/section contract tests for shared transport ingest."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.jackery_solarvault.const import (
    APP_SECTION_PV_STAT,
    PAYLOAD_PROPERTIES,
)
from custom_components.jackery_solarvault.ingest import ingest_observation
from custom_components.jackery_solarvault.models import (
    DataSource,
    IngestResult,
    Observation,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.jackery_solarvault.models import (
        FieldProvenance,
        ProvenanceKey,
    )

_DEVICE_ID = "device-1"
_SECTION = PAYLOAD_PROPERTIES
_FIELD = "pvPw"
_BASE_TIME = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
_NEW_VALUE = 120
_OLD_VALUE = 90


def test_observation_rejects_naive_wall_clock_time() -> None:
    """The ingest boundary rejects ambiguous local wall-clock timestamps."""
    with pytest.raises(ValueError, match="timezone-aware"):
        Observation(
            source=DataSource.HTTP,
            device_id=_DEVICE_ID,
            section=_SECTION,
            payload={_FIELD: 1},
            observed_at=datetime(2026, 7, 29, 10, 0),
        )


def test_ingest_result_reports_whether_fields_were_accepted() -> None:
    """Acceptance reflects the public accepted-fields result contract."""
    assert not IngestResult({}, {}, frozenset()).accepted
    assert IngestResult({}, {}, frozenset({_FIELD})).accepted


def _observation(
    source: DataSource,
    value: int,
    *,
    observed_at: datetime,
    section: str = _SECTION,
) -> Observation:
    """Build one timestamped field observation."""
    return Observation(
        source=source,
        device_id=_DEVICE_ID,
        section=section,
        payload={_FIELD: value},
        observed_at=observed_at,
    )


def _ingest(
    observation: Observation,
    *,
    current: dict[str, Any] | None = None,
    provenance: Mapping[ProvenanceKey, FieldProvenance] | None = None,
    received_at_monotonic: float = 100.0,
) -> IngestResult:
    """Ingest one observation with deterministic receive time."""
    return ingest_observation(
        observation,
        current=current or {},
        provenance=provenance or {},
        freshness_window_seconds=60.0,
        received_at_monotonic=received_at_monotonic,
    )


@pytest.mark.parametrize("source", list(DataSource))
def test_first_observation_from_every_transport_is_accepted(
    source: DataSource,
) -> None:
    """Every supported transport can independently populate live properties."""
    result = _ingest(_observation(source, 10, observed_at=_BASE_TIME))

    assert result.accepted
    assert result.payload == {_FIELD: 10}
    assert result.accepted_fields == frozenset({_FIELD})
    assert result.provenance[_FIELD].source is source


def test_older_http_poll_cannot_overwrite_newer_ble_value() -> None:
    """An explicitly older HTTP snapshot cannot reverse newer local telemetry."""
    newer = _ingest(
        _observation(
            DataSource.BLE,
            _NEW_VALUE,
            observed_at=_BASE_TIME + timedelta(seconds=5),
        ),
    )

    older = _ingest(
        _observation(DataSource.HTTP, _OLD_VALUE, observed_at=_BASE_TIME),
        current=newer.payload,
        provenance=newer.provenance,
        received_at_monotonic=101.0,
    )

    assert older.payload[_FIELD] == _NEW_VALUE
    assert older.accepted_fields == frozenset()
    assert older.provenance[_FIELD].source is DataSource.BLE


def test_newer_http_poll_cannot_replace_fresh_ble_value() -> None:
    """HTTP remains a fallback while a Layer-5 value is still fresh."""
    older = _ingest(_observation(DataSource.BLE, _OLD_VALUE, observed_at=_BASE_TIME))

    newer = _ingest(
        _observation(
            DataSource.HTTP,
            _NEW_VALUE,
            observed_at=_BASE_TIME + timedelta(seconds=5),
        ),
        current=older.payload,
        provenance=older.provenance,
        received_at_monotonic=101.0,
    )

    assert newer.payload[_FIELD] == _OLD_VALUE
    assert newer.accepted_fields == frozenset()
    assert newer.provenance[_FIELD].source is DataSource.BLE


def test_http_poll_replaces_expired_ble_value() -> None:
    """HTTP fallback may refresh a stale Layer-5 value after the freshness window."""
    older = _ingest(_observation(DataSource.BLE, _OLD_VALUE, observed_at=_BASE_TIME))

    newer = _ingest(
        _observation(
            DataSource.HTTP,
            _NEW_VALUE,
            observed_at=_BASE_TIME + timedelta(seconds=5),
        ),
        current=older.payload,
        provenance=older.provenance,
        received_at_monotonic=161.0,
    )

    assert newer.payload[_FIELD] == _NEW_VALUE
    assert newer.provenance[_FIELD].source is DataSource.HTTP


def test_equal_timestamp_uses_explicit_live_source_priority() -> None:
    """Equal-time conflicts prefer local MQTT over HTTP deterministically."""
    http = _ingest(_observation(DataSource.HTTP, _OLD_VALUE, observed_at=_BASE_TIME))

    local = _ingest(
        _observation(DataSource.LOCAL_MQTT, _NEW_VALUE, observed_at=_BASE_TIME),
        current=http.payload,
        provenance=http.provenance,
        received_at_monotonic=101.0,
    )
    stale_http = _ingest(
        _observation(DataSource.HTTP, 80, observed_at=_BASE_TIME),
        current=local.payload,
        provenance=local.provenance,
        received_at_monotonic=102.0,
    )

    assert local.payload[_FIELD] == _NEW_VALUE
    assert stale_http.payload[_FIELD] == _NEW_VALUE
    assert stale_http.accepted_fields == frozenset()


@pytest.mark.parametrize(
    ["first_source", "second_source"],
    [
        [DataSource.CLOUD_MQTT, DataSource.BLE],
        [DataSource.BLE, DataSource.CLOUD_MQTT],
        [DataSource.CLOUD_MQTT, DataSource.LOCAL_MQTT],
        [DataSource.LOCAL_MQTT, DataSource.CLOUD_MQTT],
        [DataSource.BLE, DataSource.LOCAL_MQTT],
        [DataSource.LOCAL_MQTT, DataSource.BLE],
    ],
)
def test_layer5_peers_update_in_arrival_order(
    first_source: DataSource,
    second_source: DataSource,
) -> None:
    """No Layer-5 connection may freshness-block another Layer-5 peer."""
    first = _ingest(_observation(first_source, _OLD_VALUE, observed_at=_BASE_TIME))

    second = _ingest(
        _observation(
            second_source,
            _NEW_VALUE,
            observed_at=_BASE_TIME + timedelta(seconds=1),
        ),
        current=first.payload,
        provenance=first.provenance,
        received_at_monotonic=101.0,
    )

    assert second.payload[_FIELD] == _NEW_VALUE
    assert second.accepted_fields == frozenset({_FIELD})
    assert second.provenance[_FIELD].source is second_source


@pytest.mark.parametrize(
    ["newer_source", "replayed_source"],
    [
        [DataSource.CLOUD_MQTT, DataSource.BLE],
        [DataSource.BLE, DataSource.CLOUD_MQTT],
        [DataSource.CLOUD_MQTT, DataSource.LOCAL_MQTT],
        [DataSource.LOCAL_MQTT, DataSource.CLOUD_MQTT],
        [DataSource.BLE, DataSource.LOCAL_MQTT],
        [DataSource.LOCAL_MQTT, DataSource.BLE],
    ],
)
def test_timestamped_layer5_replay_cannot_reverse_newer_peer(
    newer_source: DataSource,
    replayed_source: DataSource,
) -> None:
    """A retained old Layer-5 frame cannot reverse newer PV or SOC state."""
    newer = _ingest(
        _observation(
            newer_source,
            _NEW_VALUE,
            observed_at=_BASE_TIME + timedelta(hours=6),
        ),
    )

    replayed = _ingest(
        _observation(replayed_source, _OLD_VALUE, observed_at=_BASE_TIME),
        current=newer.payload,
        provenance=newer.provenance,
        received_at_monotonic=101.0,
    )

    assert replayed.payload[_FIELD] == _NEW_VALUE
    assert replayed.accepted_fields == frozenset()
    assert replayed.provenance[_FIELD].source is newer_source


def test_same_field_name_in_different_sections_has_independent_provenance() -> None:
    """One section's timestamp must never block another section."""
    properties = _ingest(
        _observation(
            DataSource.BLE,
            1,
            observed_at=_BASE_TIME + timedelta(minutes=1),
        ),
    )
    alarm = _ingest(
        _observation(
            DataSource.HTTP,
            2,
            observed_at=_BASE_TIME,
            section="alarm",
        ),
        current={},
        provenance=properties.provenance,
        received_at_monotonic=101.0,
    )

    assert alarm.accepted
    assert alarm.payload == {_FIELD: 2}
    assert alarm.provenance[_FIELD].section == "alarm"


@pytest.mark.parametrize(
    "source",
    [DataSource.CLOUD_MQTT, DataSource.BLE, DataSource.LOCAL_MQTT],
)
def test_app_proven_layer5_status_fields_are_live_properties(
    source: DataSource,
) -> None:
    """App 2.4.0 SystemBody status integers are valid Layer-5 live telemetry."""
    observation = Observation(
        source=source,
        device_id=_DEVICE_ID,
        section=PAYLOAD_PROPERTIES,
        payload={"stat": 1, "ctStat": 2, "ongridStat": 3},
        observed_at=_BASE_TIME,
    )

    result = _ingest(observation)

    assert result.accepted
    assert result.payload == {"stat": 1, "ctStat": 2, "ongridStat": 3}


@pytest.mark.parametrize(
    "source",
    [DataSource.CLOUD_MQTT, DataSource.BLE, DataSource.LOCAL_MQTT],
)
def test_rest_periodic_section_is_accepted_for_later_routing_on_layer5(
    source: DataSource,
) -> None:
    """Decoded periodic sections are kept for coordinator/recorder routing."""
    result = _ingest(
        _observation(
            source,
            10,
            observed_at=_BASE_TIME,
            section=f"{APP_SECTION_PV_STAT}_day",
        ),
    )

    assert result.accepted
    assert result.payload == {_FIELD: 10}
    assert result.provenance[_FIELD].source is source


def test_http_periodic_section_remains_available_without_layer5() -> None:
    """HTTP-only mode retains the App-proven REST statistics path."""
    result = _ingest(
        _observation(
            DataSource.HTTP,
            10,
            observed_at=_BASE_TIME,
            section=f"{APP_SECTION_PV_STAT}_day",
        ),
    )

    assert result.accepted
    assert result.payload == {_FIELD: 10}


def test_provenance_metadata_never_leaks_into_entity_payload() -> None:
    """Source timestamps stay outside coordinator/entity-visible state."""
    result = _ingest(
        Observation(
            source=DataSource.CLOUD_MQTT,
            device_id=_DEVICE_ID,
            section=PAYLOAD_PROPERTIES,
            payload={_FIELD: 10, "soc": 75},
            observed_at=_BASE_TIME,
            request_id="mqtt-42",
        ),
    )

    assert result.payload == {_FIELD: 10, "soc": 75}
    assert "source" not in result.payload
    assert "observed_at" not in result.payload
    assert "request_id" not in result.payload
    assert result.provenance[_FIELD].request_id == "mqtt-42"
