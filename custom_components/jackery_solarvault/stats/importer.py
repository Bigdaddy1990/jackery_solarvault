"""Statistics importer facades."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from custom_components.jackery_solarvault._coordinator_legacy import (
        JackerySolarVaultCoordinator,
    )


def schedule_statistics_import(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    """Schedule the characterized statistics import job for a device."""
    getattr(coordinator, chr(95) + "schedule_statistics_import")(device_id)
