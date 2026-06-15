"""Domain setter facades."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from custom_components.jackery_solarvault._coordinator_legacy import (
        JackerySolarVaultCoordinator,
    )


async def call(
    coordinator: JackerySolarVaultCoordinator,
    method: str,
    *args: Any,
    **kwargs: Any,
) -> object:
    """Call a characterized coordinator setter by name."""
    return await getattr(coordinator, method)(*args, **kwargs)
