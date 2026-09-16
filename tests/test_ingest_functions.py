"""Coverage tests for ingest.py helper functions and remaining branches.

Targets the missing lines that test_ingest_provenance_matrix.py and
test_ingest_merge.py do not exercise: local_period_total_supersedes_cloud,
is_periodic_section, _is_blankable, merge_live_properties, nested provenance
merge paths inside ingest_observation, _drop_descendant_provenance, and
allow_periodic_section_from_source.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custom_components.jackery_solarvault.const import (
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_SECTION_SOCKET_STAT,
    APP_SECTION_SYMMETRY_STAT,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_PROPERTIES,
    PAYLOAD_STATISTIC,
)
from custom_components.jackery_solarvault.ingest import (
    allow_periodic_section_from_source,
    ingest_observation,
    is_periodic_section,
    local_period_total_supersedes_cloud,
    merge_live_properties,
)
from custom_components.jackery_solarvault.models import DataSource, Observation

_BASE_TIME = datetime(2026, 7, 29, 10, 0, tzinfo=UTC)
_DEVICE_ID = "device-1"


# =============================================================================
# local_period_total_supersedes_cloud
# =============================================================================


class TestLocalPeriodTotalSupersedesCloud:
    """Cover every branch of the tolerance-aware comparison."""

    def test_local_none_returns_false(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert local_period_total_supersedes_cloud(10.0, None) is False

    def test_local_zero_returns_false(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert local_period_total_supersedes_cloud(10.0, 0.0) is False

    def test_local_negative_returns_false(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert local_period_total_supersedes_cloud(10.0, -1.0) is False

    def test_cloud_none_with_positive_local_returns_true(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert local_period_total_supersedes_cloud(None, 5.0) is True

    def test_local_exceeds_cloud_plus_tolerance(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert local_period_total_supersedes_cloud(10.0, 10.5, tolerance=0.1) is True

    def test_local_within_tolerance_returns_false(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert (
            local_period_total_supersedes_cloud(10.0, 10.005, tolerance=0.01) is False
        )

    def test_local_equals_cloud_returns_false(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert local_period_total_supersedes_cloud(10.0, 10.0) is False

    def test_negative_tolerance_clamped_to_zero(self) -> None:  # ruff: ignore[no-self-use]
        """max(0.0, tolerance) prevents negative tolerance from inflating cloud."""
        assert local_period_total_supersedes_cloud(10.0, 10.0, tolerance=-5.0) is False


# =============================================================================
# is_periodic_section
# =============================================================================


class TestIsPeriodicSection:
    """Cover bare prefix and date_type suffix matching."""

    @pytest.mark.parametrize(
        "section_key",
        [
            PAYLOAD_STATISTIC,
            PAYLOAD_DEVICE_STATISTIC,
            APP_SECTION_PV_STAT,
            APP_SECTION_HOME_STAT,
            APP_SECTION_BATTERY_STAT,
            APP_SECTION_CT_STAT,
            APP_SECTION_EPS_STAT,
            APP_SECTION_SOCKET_STAT,
            APP_SECTION_SYMMETRY_STAT,
        ],
    )
    def test_bare_prefixes_are_periodic(self, section_key: str) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert is_periodic_section(section_key) is True

    def test_date_type_suffix_is_periodic(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert is_periodic_section(f"{APP_SECTION_PV_STAT}_day") is True
        assert is_periodic_section(f"{APP_SECTION_HOME_TRENDS}_week") is True

    def test_properties_is_not_periodic(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert is_periodic_section(PAYLOAD_PROPERTIES) is False

    def test_unrelated_key_is_not_periodic(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert is_periodic_section("alarm") is False
        assert is_periodic_section("") is False


# =============================================================================
# merge_live_properties
# =============================================================================


class TestMergeLiveProperties:
    """Cover blankable guards, recursion, and update-wins semantics."""

    def test_update_wins_for_populated_values(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        base = {"pvPw": 100, "soc": 50}
        update = {"pvPw": 120}
        merged = merge_live_properties(base, update)
        assert merged["pvPw"] == 120  # ruff: ignore[magic-value-comparison]
        assert merged["soc"] == 50  # ruff: ignore[magic-value-comparison]

    def test_none_does_not_blank_populated_value(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        base = {"pvPw": 100}
        update = {"pvPw": None}
        assert merge_live_properties(base, update)["pvPw"] == 100  # ruff: ignore[magic-value-comparison]

    def test_empty_string_does_not_blank_populated_value(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        base = {"name": "SolarVault"}
        update = {"name": "   "}
        assert merge_live_properties(base, update)["name"] == "SolarVault"

    def test_empty_list_does_not_blank_populated_value(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        base = {"modes": ["auto"]}
        update = {"modes": []}
        assert merge_live_properties(base, update)["modes"] == ["auto"]

    def test_empty_dict_does_not_blank_populated_value(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        base = {"config": {"key": "val"}}
        update = {"config": {}}
        assert merge_live_properties(base, update)["config"] == {"key": "val"}

    def test_none_can_fill_previously_none_field(self) -> None:  # ruff: ignore[no-self-use]
        """When current is also blankable, None from update is accepted."""
        base: dict[str, Any] = {"pvPw": None}
        update: dict[str, Any] = {"pvPw": None}
        assert merge_live_properties(base, update)["pvPw"] is None

    def test_recursive_nested_merge(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        base = {"nested": {"a": 1, "b": 2}}
        update = {"nested": {"a": 10, "c": 3}}
        merged = merge_live_properties(base, update)
        assert merged["nested"] == {"a": 10, "b": 2, "c": 3}

    def test_recursive_none_in_nested_does_not_blank(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        base = {"nested": {"a": 1}}
        update = {"nested": {"a": None}}
        assert merge_live_properties(base, update)["nested"]["a"] == 1

    def test_base_is_not_mutated(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        base = {"x": 1}
        merge_live_properties(base, {"x": 2})
        assert base["x"] == 1

    def test_new_fields_are_added(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert merge_live_properties({}, {"new": 42}) == {"new": 42}


# =============================================================================
# ingest_observation — nested provenance merge and drop-descendant paths
# =============================================================================


def _obs(
    source: DataSource,
    payload: dict[str, Any],
    *,
    section: str = PAYLOAD_PROPERTIES,
    observed_at: datetime | None = None,
) -> Observation:
    return Observation(
        source=source,
        device_id=_DEVICE_ID,
        section=section,
        payload=payload,
        observed_at=observed_at or _BASE_TIME,
    )


class TestIngestNestedProvenancePaths:
    """Cover the keep_current + supplement, nested dict replace, and
    _drop_descendant_provenance branches inside ingest_observation.
    """  # ruff: ignore[missing-blank-line-after-summary]

    def test_keep_current_supplements_sparse_dict(self) -> None:  # ruff: ignore[no-self-use]
        """HTTP fills fields a sparse L5 frame omitted while keeping live owner."""
        ble = ingest_observation(
            _obs(DataSource.BLE, {"nested": {"a": 1}}, observed_at=_BASE_TIME),
            current={},
            provenance={},
            received_at_monotonic=100.0,
            freshness_window_seconds=60.0,
        )
        # HTTP arrives with older timestamp but within freshness window →
        # _provenance_keeps_current returns True, then supplement path runs.
        http = ingest_observation(
            _obs(
                DataSource.HTTP,
                {"nested": {"a": 99, "b": 2}},
                observed_at=_BASE_TIME - timedelta(seconds=5),
            ),
            current=ble.payload,
            provenance=ble.provenance,
            received_at_monotonic=101.0,
            freshness_window_seconds=60.0,
        )
        # BLE value kept for "a", HTTP fills "b" via supplement.
        assert http.payload["nested"]["a"] == 1
        assert http.payload["nested"]["b"] == 2  # ruff: ignore[magic-value-comparison]

    def test_scalar_replaces_dict_value(self) -> None:  # ruff: ignore[no-self-use]
        """A scalar update replaces a previous dict value and drops descendants."""
        first = ingest_observation(
            _obs(DataSource.HTTP, {"field": {"a": 1, "b": 2}}),
            current={},
            provenance={},
            received_at_monotonic=100.0,
        )
        second = ingest_observation(
            _obs(DataSource.HTTP, {"field": 42}),
            current=first.payload,
            provenance=first.provenance,
            received_at_monotonic=101.0,
        )
        assert second.payload["field"] == 42  # ruff: ignore[magic-value-comparison]
        # Descendant provenance keys ("field", "a") etc. must be dropped.
        assert ("field", "a") not in second.provenance
        assert ("field", "b") not in second.provenance
        assert second.provenance["field"].source is DataSource.HTTP

    def test_dict_replaces_scalar_value(self) -> None:  # ruff: ignore[no-self-use]
        """A dict update replaces a previous scalar and sets nested provenance."""
        first = ingest_observation(
            _obs(DataSource.HTTP, {"field": 42}),
            current={},
            provenance={},
            received_at_monotonic=100.0,
        )
        second = ingest_observation(
            _obs(DataSource.HTTP, {"field": {"nested": 1}}),
            current=first.payload,
            provenance=first.provenance,
            received_at_monotonic=101.0,
        )
        assert second.payload["field"] == {"nested": 1}
        assert ("field", "nested") in second.provenance

    def test_drop_descendant_provenance_on_shape_change(self) -> None:  # ruff: ignore[no-self-use]
        """When a field changes from dict to scalar, descendant prov is removed."""
        first = ingest_observation(
            _obs(DataSource.BLE, {"x": {"y": 1, "z": 2}}, observed_at=_BASE_TIME),
            current={},
            provenance={},
            received_at_monotonic=100.0,
        )
        assert ("x", "y") in first.provenance
        assert ("x", "z") in first.provenance

        second = ingest_observation(
            _obs(
                DataSource.BLE,
                {"x": "flat"},
                observed_at=_BASE_TIME + timedelta(seconds=1),
            ),
            current=first.payload,
            provenance=first.provenance,
            received_at_monotonic=101.0,
        )
        assert second.payload["x"] == "flat"
        assert ("x", "y") not in second.provenance
        assert ("x", "z") not in second.provenance

    def test_nested_accepted_propagates_up(self) -> None:  # ruff: ignore[no-self-use]
        """When a nested merge accepts a field, the parent key is marked accepted."""
        first = ingest_observation(
            _obs(DataSource.HTTP, {"top": {"inner": 1}}),
            current={},
            provenance={},
            received_at_monotonic=100.0,
        )
        second = ingest_observation(
            _obs(
                DataSource.HTTP,
                {"top": {"inner": 2}},
                observed_at=_BASE_TIME + timedelta(seconds=1),
            ),
            current=first.payload,
            provenance=first.provenance,
            received_at_monotonic=101.0,
        )
        assert "top" in second.accepted_fields
        assert second.payload["top"]["inner"] == 2  # ruff: ignore[magic-value-comparison]

    def test_nested_not_accepted_skips_parent_update(self) -> None:  # ruff: ignore[no-self-use]
        """When nested merge rejects all fields (blankable update), parent stays."""
        first = ingest_observation(
            _obs(DataSource.HTTP, {"top": {"inner": 1}}),
            current={},
            provenance={},
            received_at_monotonic=100.0,
        )
        second = ingest_observation(
            _obs(
                DataSource.HTTP,
                {"top": {"inner": None}},
                observed_at=_BASE_TIME + timedelta(seconds=1),
            ),
            current=first.payload,
            provenance=first.provenance,
            received_at_monotonic=101.0,
        )
        # inner=None is blankable, current=1 is not → rejected → top not updated.
        assert second.payload["top"]["inner"] == 1

    def test_received_at_falls_back_to_observation(self) -> None:  # ruff: ignore[no-self-use]
        """When received_at_monotonic is None, observation's value is used."""
        obs = Observation(
            source=DataSource.HTTP,
            device_id=_DEVICE_ID,
            section=PAYLOAD_PROPERTIES,
            payload={"v": 1},
            observed_at=_BASE_TIME,
            received_at_monotonic=200.0,
        )
        result = ingest_observation(
            obs,
            current={},
            provenance={},
            received_at_monotonic=None,
        )
        assert result.provenance["v"].received_at_monotonic == 200.0  # ruff: ignore[magic-value-comparison, float-equality-comparison]

    def test_received_at_falls_back_to_time_monotonic(self) -> None:  # ruff: ignore[no-self-use]
        """When both received_at values are None, time.monotonic() is called."""
        obs = Observation(
            source=DataSource.HTTP,
            device_id=_DEVICE_ID,
            section=PAYLOAD_PROPERTIES,
            payload={"v": 1},
            observed_at=_BASE_TIME,
            received_at_monotonic=None,
        )
        result = ingest_observation(
            obs,
            current={},
            provenance={},
            received_at_monotonic=None,
        )
        # Just verify it got some positive float from time.monotonic().
        assert result.provenance["v"].received_at_monotonic > 0


# =============================================================================
# allow_periodic_section_from_source
# =============================================================================


class TestAllowPeriodicSectionFromSource:
    """Cover the thin wrapper around is_periodic_section."""

    @pytest.mark.parametrize("source", list(DataSource))
    def test_all_sources_allowed_for_periodic_section(self, source: DataSource) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert allow_periodic_section_from_source(source, APP_SECTION_PV_STAT) is True
        assert (
            allow_periodic_section_from_source(source, f"{APP_SECTION_PV_STAT}_day")
            is True
        )

    @pytest.mark.parametrize("source", list(DataSource))
    def test_non_periodic_section_rejected(self, source: DataSource) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert allow_periodic_section_from_source(source, PAYLOAD_PROPERTIES) is False
