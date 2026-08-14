"""Behavioral edge coverage for shared ingest and local-MQTT diagnostics."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.const import (
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    DOMAIN,
    LOCAL_MQTT_RUNTIME_KEY,
    PAYLOAD_PROPERTIES,
    REDACTED_VALUE,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.diagnostics import (
    _local_mqtt_diagnostics,  # ruff: ignore[import-private-name]
)
from custom_components.jackery_solarvault.ingest import (
    allow_periodic_section_from_source,
    ingest_observation,
    is_periodic_section,
    local_period_total_supersedes_cloud,
    merge_live_properties,
)
from custom_components.jackery_solarvault.models import (
    DataSource,
    FieldProvenance,
    Observation,
    ProvenanceKey,
)

_DEVICE = "device-1"
_NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("local_total", [None, 0.0, -1.0])
def test_nonpositive_local_period_total_never_supersedes_cloud(
    local_total: float | None,
) -> None:
    """Missing or nonpositive local deltas are not energy evidence."""
    assert not local_period_total_supersedes_cloud(1.0, local_total)


def test_positive_local_period_total_supersedes_missing_or_lagging_cloud() -> None:
    """A verified positive delta fills a missing or strictly smaller total."""
    assert local_period_total_supersedes_cloud(None, 1.0)
    assert local_period_total_supersedes_cloud(1.0, 1.1)
    assert not local_period_total_supersedes_cloud(1.0, 1.000001)
    assert local_period_total_supersedes_cloud(1.0, 1.01, tolerance=-1.0)


def test_periodic_section_accepts_bare_and_suffixed_keys_only() -> None:
    """Period classification accepts documented prefixes, not live sections."""
    assert is_periodic_section("device_pv_stat")
    assert is_periodic_section("device_pv_stat_week")
    assert not is_periodic_section(PAYLOAD_PROPERTIES)
    assert allow_periodic_section_from_source(
        DataSource.LOCAL_MQTT,
        "device_pv_stat_day",
    )
    assert not allow_periodic_section_from_source(DataSource.BLE, PAYLOAD_PROPERTIES)


def test_live_merge_recurses_and_preserves_populated_values() -> None:
    """Nested sparse frames fill fields without blanking existing telemetry."""
    base = {"pv": {"power": 100, "name": "Roof"}, "modes": ["auto"]}
    update = {"pv": {"power": None, "voltage": 40}, "modes": [], "new": ""}

    merged = merge_live_properties(base, update)

    assert merged == {
        "pv": {"power": 100, "name": "Roof", "voltage": 40},
        "modes": ["auto"],
        "new": "",
    }
    assert base == {"pv": {"power": 100, "name": "Roof"}, "modes": ["auto"]}


def test_ingest_uses_observation_receive_timestamp() -> None:
    """An observation-owned receive timestamp is retained in provenance."""
    observation = Observation(
        source=DataSource.CLOUD_MQTT,
        device_id=_DEVICE,
        section=PAYLOAD_PROPERTIES,
        payload={"soc": 75},
        observed_at=_NOW,
        received_at_monotonic=123.0,
    )

    result = ingest_observation(observation, current={}, provenance={})

    assert result.provenance["soc"].received_at_monotonic == pytest.approx(123.0)


def test_ingest_falls_back_to_monotonic_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frames without either receive timestamp use the host monotonic clock."""
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.ingest.time.monotonic",
        lambda: 456.0,
    )
    observation = Observation(
        source=DataSource.BLE,
        device_id=_DEVICE,
        section=PAYLOAD_PROPERTIES,
        payload={"soc": 76},
        observed_at=_NOW,
    )

    result = ingest_observation(observation, current={}, provenance={})

    assert result.provenance["soc"].received_at_monotonic == pytest.approx(456.0)


def test_fresh_layer5_nested_value_keeps_owner_and_accepts_http_supplement() -> None:
    """HTTP may fill a nested gap without reversing fresh Layer-5 fields."""
    current = {"battery": {"soc": 80}}
    provenance: dict[ProvenanceKey, FieldProvenance] = {
        "battery": FieldProvenance(
            source=DataSource.BLE,
            section=PAYLOAD_PROPERTIES,
            observed_at=_NOW,
            received_at_monotonic=100.0,
        ),
    }
    observation = Observation(
        source=DataSource.HTTP,
        device_id=_DEVICE,
        section=PAYLOAD_PROPERTIES,
        payload={"battery": {"soc": 20, "temperature": 31}},
        observed_at=_NOW,
    )

    result = ingest_observation(
        observation,
        current=current,
        provenance=provenance,
        received_at_monotonic=101.0,
        freshness_window_seconds=60.0,
    )

    assert result.payload["battery"] == {"soc": 80, "temperature": 31}
    assert result.accepted_fields == frozenset({"battery"})
    assert result.provenance["battery"].source is DataSource.BLE
    assert result.provenance["battery", "temperature"].source is DataSource.HTTP

    refreshed = ingest_observation(
        Observation(
            source=DataSource.HTTP,
            device_id=_DEVICE,
            section=PAYLOAD_PROPERTIES,
            payload={"battery": {"soc": 20, "temperature": 32}},
            observed_at=_NOW,
        ),
        current=result.payload,
        provenance=result.provenance,
        received_at_monotonic=102.0,
        freshness_window_seconds=60.0,
    )

    assert refreshed.payload["battery"] == {"soc": 80, "temperature": 32}
    assert refreshed.provenance["battery"].source is DataSource.BLE
    assert refreshed.provenance["battery", "temperature"].source is DataSource.HTTP

    layer5 = ingest_observation(
        Observation(
            source=DataSource.BLE,
            device_id=_DEVICE,
            section=PAYLOAD_PROPERTIES,
            payload={"battery": {"temperature": 40}},
            observed_at=_NOW,
        ),
        current=refreshed.payload,
        provenance=refreshed.provenance,
        received_at_monotonic=103.0,
        freshness_window_seconds=60.0,
    )
    stale_http = ingest_observation(
        Observation(
            source=DataSource.HTTP,
            device_id=_DEVICE,
            section=PAYLOAD_PROPERTIES,
            payload={"battery": {"soc": 20, "temperature": 32}},
            observed_at=_NOW,
        ),
        current=layer5.payload,
        provenance=layer5.provenance,
        received_at_monotonic=104.0,
        freshness_window_seconds=60.0,
    )

    assert stale_http.payload["battery"] == {"soc": 80, "temperature": 40}
    assert stale_http.provenance["battery", "temperature"].source is DataSource.BLE


def test_nested_provenance_paths_cannot_collide_with_dotted_top_level_keys() -> None:
    """Tuple paths distinguish nested fields from opaque dotted payload keys."""
    result = ingest_observation(
        Observation(
            source=DataSource.LOCAL_MQTT,
            device_id=_DEVICE,
            section=PAYLOAD_PROPERTIES,
            payload={
                "battery.temperature": 99,
                "battery": {"temperature": 40},
            },
            observed_at=_NOW,
        ),
        current={},
        provenance={},
        received_at_monotonic=100.0,
    )

    assert result.provenance["battery.temperature"].source is DataSource.LOCAL_MQTT
    assert result.provenance["battery", "temperature"].source is DataSource.LOCAL_MQTT


def test_nested_update_without_fresh_owner_uses_recursive_merge() -> None:
    """A normal nested update replaces reported fields and retains omitted ones."""
    observation = Observation(
        source=DataSource.CLOUD_MQTT,
        device_id=_DEVICE,
        section=PAYLOAD_PROPERTIES,
        payload={"battery": {"soc": 81}},
        observed_at=_NOW,
    )

    result = ingest_observation(
        observation,
        current={"battery": {"soc": 80, "temperature": 31}},
        provenance={},
        received_at_monotonic=101.0,
    )

    assert result.payload["battery"] == {"soc": 81, "temperature": 31}
    assert result.provenance["battery"].source is DataSource.CLOUD_MQTT


def test_nested_shape_changes_clear_stale_descendant_provenance() -> None:
    """A nested dict-scalar-dict cycle cannot retain obsolete ownership."""
    initial = ingest_observation(
        Observation(
            source=DataSource.HTTP,
            device_id=_DEVICE,
            section=PAYLOAD_PROPERTIES,
            payload={"battery": {"details": {"temperature": 31}}},
            observed_at=_NOW,
        ),
        current={},
        provenance={},
        received_at_monotonic=100.0,
    )
    scalar = ingest_observation(
        Observation(
            source=DataSource.BLE,
            device_id=_DEVICE,
            section=PAYLOAD_PROPERTIES,
            payload={"battery": {"details": "unavailable"}},
            observed_at=_NOW,
        ),
        current=initial.payload,
        provenance=initial.provenance,
        received_at_monotonic=101.0,
        freshness_window_seconds=60.0,
    )
    assert ("battery", "details", "temperature") not in scalar.provenance

    restored = ingest_observation(
        Observation(
            source=DataSource.BLE,
            device_id=_DEVICE,
            section=PAYLOAD_PROPERTIES,
            payload={"battery": {"details": {"temperature": 40}}},
            observed_at=_NOW,
        ),
        current=scalar.payload,
        provenance=scalar.provenance,
        received_at_monotonic=102.0,
        freshness_window_seconds=60.0,
    )
    stale_http = ingest_observation(
        Observation(
            source=DataSource.HTTP,
            device_id=_DEVICE,
            section=PAYLOAD_PROPERTIES,
            payload={"battery": {"details": {"temperature": 32}}},
            observed_at=_NOW,
        ),
        current=restored.payload,
        provenance=restored.provenance,
        received_at_monotonic=103.0,
        freshness_window_seconds=60.0,
    )

    assert stale_http.payload["battery"]["details"] == {"temperature": 40}
    assert (
        stale_http.provenance["battery", "details", "temperature"].source
        is DataSource.BLE
    )


def test_fresh_layer5_nested_value_rejects_redundant_http_snapshot() -> None:
    """An HTTP dictionary with no missing keys causes no accepted-field event."""
    current = {"battery": {"soc": 80}}
    provenance: dict[ProvenanceKey, FieldProvenance] = {
        "battery": FieldProvenance(
            source=DataSource.LOCAL_MQTT,
            section=PAYLOAD_PROPERTIES,
            observed_at=_NOW,
            received_at_monotonic=100.0,
        ),
    }
    observation = Observation(
        source=DataSource.HTTP,
        device_id=_DEVICE,
        section=PAYLOAD_PROPERTIES,
        payload={"battery": {"soc": 20}},
        observed_at=_NOW,
    )

    result = ingest_observation(
        observation,
        current=current,
        provenance=provenance,
        received_at_monotonic=101.0,
        freshness_window_seconds=60.0,
    )

    assert result.payload == current
    assert result.accepted_fields == frozenset()


def _entry(runtime_data: object, options: dict[str, Any] | None = None) -> Any:
    """Return the minimal config-entry surface used by diagnostics."""
    return SimpleNamespace(
        data={},
        options=options or {},
        runtime_data=runtime_data,
        entry_id="entry-1",
    )


def test_local_mqtt_diagnostics_reports_coordinator_not_ready() -> None:
    """A failed setup exports redacted configuration instead of raising."""
    hass = SimpleNamespace(data={})
    result = _local_mqtt_diagnostics(
        cast("Any", hass),
        cast(
            "Any",
            _entry(
                object(),
                {CONF_LOCAL_MQTT_ENABLE: True, CONF_LOCAL_MQTT_HOST: "broker"},
            ),
        ),
    )

    assert result["disabled_reason"] == "coordinator_not_ready"
    assert result["configured_local_mqtt"]["host"] == REDACTED_VALUE


def test_local_mqtt_diagnostics_uses_runtime_bucket_fallback() -> None:
    """A partial coordinator finds the entry-owned HA-MQTT adapter runtime."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    client = SimpleNamespace(diagnostics_snapshot=lambda: {"connected": True})
    hass = SimpleNamespace(
        data={DOMAIN: {"entry-1": {LOCAL_MQTT_RUNTIME_KEY: client}}},
    )

    result = _local_mqtt_diagnostics(
        cast("Any", hass),
        cast("Any", _entry(coordinator, {CONF_LOCAL_MQTT_ENABLE: True})),
    )

    assert result == {"connected": True}


@pytest.mark.parametrize("runtime_bucket", [{}, []])
def test_local_mqtt_diagnostics_reports_missing_started_client(
    runtime_bucket: object,
) -> None:
    """Empty or malformed runtime buckets stay exportable and explicit."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    hass = SimpleNamespace(data={DOMAIN: {"entry-1": runtime_bucket}})

    result = _local_mqtt_diagnostics(
        cast("Any", hass),
        cast(
            "Any",
            _entry(
                coordinator,
                {CONF_LOCAL_MQTT_ENABLE: True, CONF_LOCAL_MQTT_HOST: "broker"},
            ),
        ),
    )

    assert result["disabled_reason"] == "client_not_started"
