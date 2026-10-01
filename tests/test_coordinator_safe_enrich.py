"""Behavior tests for the coordinator's current background enrichment path."""

import logging
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.client.api import (
    JackeryAuthError,
    JackeryError,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)


def _coordinator() -> JackerySolarVaultCoordinator:
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator.data = {"device-1": {"existing": 1}}
    cast("Any", coordinator)._push_partial_update = MagicMock()  # ruff: ignore[private-member-access]
    return coordinator  # pyrefly: ignore [no-any-return-implicit]


async def test_background_enrichment_publishes_both_results() -> None:
    """Supplementary values reach the snapshot after both queries finish."""
    coordinator = _coordinator()

    async def plug(_device_id: str, entry: dict[str, Any], *, stale_ok: bool) -> None:  # ruff: ignore[unused-async]
        assert stale_ok is False
        entry["plug"] = 2

    async def meter(_device_id: str, entry: dict[str, Any], *, stale_ok: bool) -> None:  # ruff: ignore[unused-async]
        assert stale_ok is False
        entry["meter"] = 3

    mutable = cast("Any", coordinator)
    mutable._async_enrich_smart_plug_statistics = plug  # ruff: ignore[private-member-access]
    mutable._async_enrich_meter_head_statistics = meter  # ruff: ignore[private-member-access]

    await coordinator._async_refresh_device_enrichments("device-1")  # ruff: ignore[private-member-access]

    mutable._push_partial_update.assert_called_once_with(  # ruff: ignore[private-member-access]
        {"device-1": {"existing": 1, "plug": 2, "meter": 3}}
    )


@pytest.mark.parametrize(
    ["error", "message"],
    [
        [JackeryAuthError("auth failed"), "auth-rejected"],
        [TimeoutError("timeout"), "failed"],
        [JackeryError("api error"), "failed"],
    ],
)
async def test_known_enrichment_failure_keeps_next_query(
    error: Exception, message: str, caplog: pytest.LogCaptureFixture
) -> None:
    """One failed supplementary request does not suppress the next result."""
    coordinator = _coordinator()
    caplog.set_level(logging.DEBUG)

    async def plug(_device_id: str, _entry: dict[str, Any], *, stale_ok: bool) -> None:  # ruff: ignore[unused-async]
        raise error

    async def meter(_device_id: str, entry: dict[str, Any], *, stale_ok: bool) -> None:  # ruff: ignore[unused-async]
        entry["meter"] = 3

    mutable = cast("Any", coordinator)
    mutable._async_enrich_smart_plug_statistics = plug  # ruff: ignore[private-member-access]
    mutable._async_enrich_meter_head_statistics = meter  # ruff: ignore[private-member-access]

    await coordinator._async_refresh_device_enrichments("device-1")  # ruff: ignore[private-member-access]

    assert message in caplog.text
    mutable._push_partial_update.assert_called_once_with(  # ruff: ignore[private-member-access]
        {"device-1": {"existing": 1, "meter": 3}}
    )


async def test_unexpected_enrichment_failure_propagates() -> None:
    """Programming errors remain visible to the background task owner."""
    coordinator = _coordinator()

    async def plug(_device_id: str, _entry: dict[str, Any], *, stale_ok: bool) -> None:  # ruff: ignore[unused-async]
        raise ValueError("unexpected")

    mutable = cast("Any", coordinator)
    mutable._async_enrich_smart_plug_statistics = plug  # ruff: ignore[private-member-access]
    mutable._async_enrich_meter_head_statistics = MagicMock()  # ruff: ignore[private-member-access]

    with pytest.raises(ValueError, match="unexpected"):
        await coordinator._async_refresh_device_enrichments("device-1")  # ruff: ignore[private-member-access]

    mutable._async_enrich_meter_head_statistics.assert_not_called()  # ruff: ignore[private-member-access]
    mutable._push_partial_update.assert_not_called()  # ruff: ignore[private-member-access]
