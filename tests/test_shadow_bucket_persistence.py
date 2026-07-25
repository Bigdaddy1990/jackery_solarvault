"""Regression: HTTP shadow config buckets survive an empty poll cycle.

The smart-mode / smart-schedule / TOU diagnostic buckets are filled
best-effort from HTTP endpoints that transiently fail (e.g. dynamicPrice
code=10600) or return empty. Without carry-forward the diagnostic sensors
(TOU-Plan-Aufgaben etc.) flickered Unknown -> value -> Unknown every cycle.
"""

from typing import Any, cast

from custom_components.jackery_solarvault.const import (
    PAYLOAD_SMART_MODE,
    PAYLOAD_TOU_SCHEDULE,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_DEVICE = "dev-1"


def _coordinator(previous: dict[str, Any]) -> Any:  # ruff:ignore[any-type]
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator).data = {_DEVICE: previous}
    return coordinator


def test_empty_cycle_keeps_last_known_tou_bucket() -> None:
    """A cycle that did not refill TOU keeps the previous non-empty bucket."""
    coordinator = _coordinator({PAYLOAD_TOU_SCHEDULE: {"tasks": [{"month": "1"}]}})
    working: dict[str, Any] = {}

    coordinator._carry_forward_shadow_buckets(_DEVICE, working)

    assert working[PAYLOAD_TOU_SCHEDULE] == {"tasks": [{"month": "1"}]}


def test_fresh_bucket_is_not_overwritten() -> None:
    """A bucket filled this cycle is left untouched by the carry-forward."""
    coordinator = _coordinator({PAYLOAD_SMART_MODE: {"isActive": 0}})
    working: dict[str, Any] = {PAYLOAD_SMART_MODE: {"isActive": 1}}

    coordinator._carry_forward_shadow_buckets(_DEVICE, working)

    assert working[PAYLOAD_SMART_MODE] == {"isActive": 1}


def test_missing_previous_bucket_is_left_absent() -> None:
    """No previous value means the bucket stays absent (still Unknown)."""
    coordinator = _coordinator({})
    working: dict[str, Any] = {}

    coordinator._carry_forward_shadow_buckets(_DEVICE, working)

    assert PAYLOAD_TOU_SCHEDULE not in working


def test_empty_previous_bucket_is_not_carried() -> None:
    """An empty previous bucket is not carried forward (nothing to preserve)."""
    coordinator = _coordinator({PAYLOAD_TOU_SCHEDULE: {}})
    working: dict[str, Any] = {}

    coordinator._carry_forward_shadow_buckets(_DEVICE, working)

    assert PAYLOAD_TOU_SCHEDULE not in working
