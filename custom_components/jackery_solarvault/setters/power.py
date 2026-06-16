"""Power setter facades."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from custom_components.jackery_solarvault._coordinator_legacy import (
        JackerySolarVaultCoordinator,
    )


async def async_set_eps(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    enabled: bool,
) -> None:
    """Set EPS through the characterized coordinator path."""
    await coordinator.async_set_eps(device_id, enabled)


async def async_set_max_output_power(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    watts: int,
) -> None:
    """Set max output power through the characterized coordinator path."""
    await coordinator.async_set_max_output_power(device_id, watts)
