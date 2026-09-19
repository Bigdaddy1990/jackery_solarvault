"""Additional ingest tests to close the remaining branch gaps in ingest.py.

Covers:
- _merge_nested_with_provenance nested dict recursion (lines 223-234)
- _merge_nested_with_provenance new dict creation over a scalar (lines 245-257)
- _merge_nested_with_provenance scalar replacing a dict (lines 258-262)
- ingest_observation keep_current supplementation path (lines 333-349)

Provenance semantics (documented in ingest.py's module docstring): an accepted
field means the frame took ownership of that field path — even when the stored
value is unchanged, because the owning source may flip.
"""

from typing import TYPE_CHECKING, Any

from custom_components.jackery_solarvault.ingest import ingest_observation
from custom_components.jackery_solarvault.models import (
    DataSource,
    FieldProvenance,
    Observation,
)

if TYPE_CHECKING:
    from custom_components.jackery_solarvault.models import ProvenanceKey

_SCALAR_REPLACEMENT = 3


def _make_obs(payload: dict[str, Any]) -> Observation:
    """Build a minimal HTTP observation for the given payload."""
    return Observation(
        source=DataSource.HTTP,
        device_id="TEST001",
        section="properties",
        payload=payload,
        request_id="req-1",
    )


def _ble_provenance(
    received_at_monotonic: float,
) -> FieldProvenance:
    """Build a BLE provenance entry stamped at the given monotonic time."""
    return FieldProvenance(
        source=DataSource.BLE,
        section="properties",
        observed_at=None,
        received_at_monotonic=received_at_monotonic,
    )


# -----------------------------------------------------------------------
# Lines 223-234: nested dict-valued key merged into dict current
# -----------------------------------------------------------------------


def test_doubly_nested_new_key_is_accepted() -> None:
    """A new key inside a doubly nested dict is merged and takes ownership."""
    current: dict[str, Any] = {"outer": {"inner": {"known": 1}}}
    observation = _make_obs({"outer": {"inner": {"added": 2}}})

    result = ingest_observation(
        observation,
        current=current,
        provenance={},
        received_at_monotonic=1000.0,
    )

    assert result.payload["outer"]["inner"] == {"known": 1, "added": 2}
    assert "outer" in result.accepted_fields


# -----------------------------------------------------------------------
# Lines 245-257: dict value arriving over a scalar current
# -----------------------------------------------------------------------


def test_dict_replaces_scalar_in_nested_mapping() -> None:
    """A dict arriving where a scalar was stored creates the nested dict."""
    current: dict[str, Any] = {"outer": {"metric": 5}}
    observation = _make_obs({"outer": {"metric": {"deep": 7}}})

    result = ingest_observation(
        observation,
        current=current,
        provenance={},
        received_at_monotonic=1000.0,
    )

    assert result.payload["outer"]["metric"] == {"deep": 7}
    assert ("outer", "metric") in result.provenance
    assert "outer" in result.accepted_fields


# -----------------------------------------------------------------------
# Lines 258-262: scalar value replacing a dict current
# -----------------------------------------------------------------------


def test_scalar_replaces_nested_dict_and_prunes_provenance() -> None:
    """Scalar over dict shape change replaces the value and drops descendants."""
    current: dict[str, Any] = {"outer": {"metric": {"deep": 7}}}
    observation = _make_obs({"outer": {"metric": _SCALAR_REPLACEMENT}})

    result = ingest_observation(
        observation,
        current=current,
        provenance={},
        received_at_monotonic=1000.0,
    )

    assert result.payload["outer"]["metric"] == _SCALAR_REPLACEMENT
    assert ("outer", "metric", "deep") not in result.provenance
    assert "outer" in result.accepted_fields


# -----------------------------------------------------------------------
# Lines 333-349: keep_current supplementation
# -----------------------------------------------------------------------


def test_http_supplements_sparse_fresh_ble_dict() -> None:
    """A fresh BLE-owned dict keeps its values but HTTP adds missing keys."""
    current: dict[str, Any] = {"status": {"owned_by_ble": 1}}
    provenance: dict[ProvenanceKey, FieldProvenance] = {
        "status": _ble_provenance(999.0),
        ("status", "owned_by_ble"): _ble_provenance(999.0),
    }
    observation = _make_obs({"status": {"fresh_from_http": 2}})

    result = ingest_observation(
        observation,
        current=current,
        provenance=provenance,
        received_at_monotonic=1000.0,
        freshness_window_seconds=30.0,
    )

    # BLE-owned key survives; HTTP-only key is filled in.
    assert result.payload["status"] == {
        "owned_by_ble": 1,
        "fresh_from_http": 2,
    }
    assert "status" in result.accepted_fields
    # Ownership of the pre-existing key did not flip to HTTP.
    assert result.provenance["status", "owned_by_ble"].source is DataSource.BLE
    assert result.provenance["status", "fresh_from_http"].source is DataSource.HTTP


def test_http_with_nothing_to_add_leaves_field_unaccepted() -> None:
    """Keep-current with zero missing keys: no supplementation accepted."""
    current: dict[str, Any] = {"status": {"key": 1}}
    provenance: dict[ProvenanceKey, FieldProvenance] = {
        "status": _ble_provenance(999.0),
        ("status", "key"): _ble_provenance(999.0),
    }
    observation = _make_obs({"status": {"key": 99}})

    result = ingest_observation(
        observation,
        current=current,
        provenance=provenance,
        received_at_monotonic=1000.0,
        freshness_window_seconds=30.0,
    )

    # Fresh BLE value is protected; HTTP cannot reverse it, adds nothing.
    assert result.payload["status"] == {"key": 1}
    assert "status" not in result.accepted_fields


def test_expired_window_lets_http_take_over() -> None:
    """Once the freshness window passes, HTTP owns the field again."""
    current: dict[str, Any] = {"status": {"key": 1}}
    provenance: dict[ProvenanceKey, FieldProvenance] = {
        "status": _ble_provenance(0.0),
    }
    observation = _make_obs({"status": {"key": 2}})

    result = ingest_observation(
        observation,
        current=current,
        provenance=provenance,
        received_at_monotonic=1000.0,
        freshness_window_seconds=30.0,
    )

    assert result.payload["status"] == {"key": 2}
    assert "status" in result.accepted_fields
    assert result.provenance["status", "key"].source is DataSource.HTTP


# -----------------------------------------------------------------------
# Line 231 false branch: nested recursion protects every key
# -----------------------------------------------------------------------


def test_nested_http_frame_rejected_when_all_keys_protected() -> None:
    """Top-level merge runs, but the nested dict accepts nothing."""
    current: dict[str, Any] = {"outer": {"inner": {"protected": 1}}}
    provenance: dict[ProvenanceKey, FieldProvenance] = {
        ("outer", "inner"): _ble_provenance(999.0),
        ("outer", "inner", "protected"): _ble_provenance(999.0),
    }
    observation = _make_obs({"outer": {"inner": {"protected": 99}}})

    result = ingest_observation(
        observation,
        current=current,
        provenance=provenance,
        received_at_monotonic=1000.0,
        freshness_window_seconds=30.0,
    )

    # The fresh BLE value survives at every level; nothing was accepted.
    assert result.payload["outer"]["inner"] == {"protected": 1}
    assert "outer" not in result.accepted_fields
