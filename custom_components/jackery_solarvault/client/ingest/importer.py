"""Statistics importer facades."""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...coordinator import JackerySolarVaultCoordinator  # noqa: RUF100, TID252

_LOGGER = logging.getLogger(__name__)


def schedule_statistics_import(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
) -> None:
    """Schedule the characterized statistics import job for a device."""
    getattr(coordinator, chr(95) + "schedule_statistics_import")(device_id)
