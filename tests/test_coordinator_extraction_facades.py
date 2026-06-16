"""Characterization tests for coordinator extraction facades."""
# ruff: noqa: PLR6301

from typing import Any

import pytest

from custom_components.jackery_solarvault._coordinator_legacy import (  # noqa: PLC2701
    JackerySolarVaultCoordinator as LegacyCoordinator,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.handlers.live import merge_main_properties
from custom_components.jackery_solarvault.setters.core import async_publish_command
from custom_components.jackery_solarvault.stats.validators import verify_and_backfill


def test_coordinator_facade_exports_characterized_class() -> None:
    """Facade preserves the public coordinator class identity."""
    assert JackerySolarVaultCoordinator is LegacyCoordinator  # noqa: S101


def test_live_merge_facade_delegates_existing_behavior() -> None:
    """Live facade delegates to the existing merge implementation."""

    class CoordinatorStub:
        """Minimal merge target."""

        def _merge_main_properties(
            self,
            entry: dict[str, Any],
            properties: dict[str, Any],
        ) -> None:
            entry.setdefault("properties", {}).update(properties)

    entry: dict[str, object] = {}
    merge_main_properties(CoordinatorStub(), entry, {"soc": 80})  # type: ignore[arg-type]

    assert entry == {"properties": {"soc": 80}}  # noqa: S101


@pytest.mark.parametrize(
    ["cloud", "local", "expected"],
    [
        [None, None, None],
        [12.0, None, 12.0],
        [None, 11.0, 11.0],
        [0.0, 8.0, 8.0],
        [100.0, 70.0, 70.0],
        [100.0, 95.0, 100.0],
    ],
)
def test_verify_and_backfill_matches_documented_hierarchy(
    cloud: float | None,
    local: float | None,
    expected: float | None,
) -> None:
    """Stats validator preserves documented cloud/local hierarchy."""
    assert verify_and_backfill(cloud, local) == expected  # noqa: S101


async def test_command_facade_delegates_publish_command() -> None:
    """Command facade delegates to existing publish transport."""
    calls: list[tuple[str, int, dict[str, object] | None, dict[str, object]]] = []

    class CoordinatorStub:
        """Minimal command target."""

        async def _async_publish_command(
            self,
            device_id: str,
            cmd: int,
            body: dict[str, object] | None = None,
            **kwargs: object,
        ) -> None:
            calls.append((device_id, cmd, body, kwargs))

    await async_publish_command(
        CoordinatorStub(),
        "dev-1",
        123,
        {"maxOutPw": 800},
        qos=1,  # type: ignore[arg-type]
    )

    assert calls == [("dev-1", 123, {"maxOutPw": 800}, {"qos": 1})]  # noqa: S101
