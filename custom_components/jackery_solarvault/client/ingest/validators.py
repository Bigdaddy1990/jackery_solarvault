"""Statistics validation helpers extracted from coordinator-adjacent logic."""

import logging

_LOGGER = logging.getLogger(__name__)


def verify_and_backfill(
    cloud_value: float | None,
    local_value: float | None,
    *,
    tolerance: float = 0.1,
) -> float | None:
    """Merge cloud and local statistic values using Jackery hierarchy rules."""
    if cloud_value is None:
        return local_value
    if local_value is None:
        return cloud_value
    if cloud_value == 0 and local_value > 0:
        return local_value
    if cloud_value > 0 and abs(cloud_value - local_value) > tolerance * cloud_value:
        return min(cloud_value, local_value)
    return cloud_value
