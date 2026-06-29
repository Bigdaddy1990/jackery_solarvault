"""Coordinator endpoint-backoff unit tests."""

import time

from custom_components.jackery_solarvault.client.api import JackeryError
from custom_components.jackery_solarvault.coordinator import (
    _ENDPOINT_BACKOFF_DELAYS_SEC,  # noqa: PLC2701
    JackerySolarVaultCoordinator,
)


def _coordinator_stub() -> JackerySolarVaultCoordinator:
    """Create a lightweight coordinator instance for pure helper-method tests."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._endpoint_backoff = {}  # noqa: SLF001
    return coordinator


def test_endpoint_backoff_is_recorded_for_known_error_codes() -> None:
    """Error code 10422 should enter endpoint backoff with level 0."""
    coordinator = _coordinator_stub()
    err = JackeryError("request failed code=10422")

    handled = coordinator._endpoint_backoff_note_failure("dev:1:battery_stat", err)  # noqa: SLF001

    assert handled is True
    state = coordinator._endpoint_backoff["dev:1:battery_stat"]  # noqa: SLF001
    assert state["code"] == 10422  # noqa: PLR2004
    assert state["level"] == 0
    assert coordinator._endpoint_backoff_active(  # noqa: SLF001
        "dev:1:battery_stat",
        time.monotonic(),
    )


def test_endpoint_backoff_escalates_and_resets_on_success() -> None:
    """Repeated failures escalate delay; success clears the backoff key."""
    coordinator = _coordinator_stub()
    err = JackeryError("request failed code=10432")
    key = "dev:1:eps_stat"

    assert coordinator._endpoint_backoff_note_failure(key, err) is True  # noqa: SLF001
    first = dict(coordinator._endpoint_backoff[key])  # noqa: SLF001
    assert first["level"] == 0

    assert coordinator._endpoint_backoff_note_failure(key, err) is True  # noqa: SLF001
    second = dict(coordinator._endpoint_backoff[key])  # noqa: SLF001
    assert second["level"] == 1
    assert (
        _ENDPOINT_BACKOFF_DELAYS_SEC[second["level"]]
        > _ENDPOINT_BACKOFF_DELAYS_SEC[first["level"]]
    )

    coordinator._endpoint_backoff_note_success(key)  # noqa: SLF001
    assert key not in coordinator._endpoint_backoff  # noqa: SLF001


def test_endpoint_backoff_ignores_unrelated_errors() -> None:
    """Unknown error codes must not start endpoint backoff."""
    coordinator = _coordinator_stub()
    err = JackeryError("request failed code=40001")

    handled = coordinator._endpoint_backoff_note_failure("dev:1:home_stat", err)  # noqa: SLF001

    assert handled is False
    assert coordinator._endpoint_backoff == {}  # noqa: SLF001
