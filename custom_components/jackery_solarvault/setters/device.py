"""Device-level setter facades."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from custom_components.jackery_solarvault._coordinator_legacy import (
        JackerySolarVaultCoordinator,
    )


async def async_set_work_model(
    coordinator: JackerySolarVaultCoordinator, device_id: str, mode: int
) -> None:
    """Set the work model through the characterized coordinator path."""
    await coordinator.async_set_work_model(device_id, mode)


async def async_reboot_device(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Reboot a device through the characterized coordinator path."""
    await coordinator.async_reboot_device(device_id)
