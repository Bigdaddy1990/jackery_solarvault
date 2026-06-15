"""Core command dispatch facades."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from custom_components.jackery_solarvault._coordinator_legacy import (
        JackerySolarVaultCoordinator,
    )


async def async_publish_command(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    cmd: int,
    body: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Publish a command through the characterized coordinator transport stack."""
    await getattr(coordinator, chr(95) + "async_publish_command")(
        device_id, cmd, body, **kwargs
    )


async def async_publish_command_ble_first(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    cmd: int,
    body: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """Publish a command through BLE first, then MQTT fallback."""
    await getattr(coordinator, chr(95) + "async_publish_command_ble_first")(
        device_id, cmd, body, **kwargs
    )
