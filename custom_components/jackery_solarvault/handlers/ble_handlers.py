"""BLE coordinator handler delegates.

The transport implementation stays in ``client.ble_transport``; this module
contains coordinator-adjacent BLE lifecycle and command helpers so the
coordinator can keep orchestration-only wrappers.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from custom_components.jackery_solarvault.coordinator import (
        JackerySolarVaultCoordinator,
    )


def ble_writes_enabled(coordinator: JackerySolarVaultCoordinator) -> bool:
    """Return whether BLE writes are enabled for a coordinator entry."""
    return coordinator._ble_writes_enabled()  # noqa: SLF001


async def async_send_ble_command(  # noqa: PLR0913
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    *,
    cmd: int,
    body: dict[str, Any] | bytes,
    flags: int = 0,
    wait_for_ack: bool = False,
    ack_timeout_sec: float = 5.0,
    ack_cmds: tuple[int, ...] | None = None,
    mtu_override: int | None = None,
) -> bool:
    """Send a BLE command to a device.

    Parameters:
        body (dict or bytes): Command payload.
        ack_cmds (tuple of int, optional): Command IDs to recognize as acknowledgments.

    Returns:
        True if the command was sent successfully, False otherwise.
    """
    return await coordinator.async_send_ble_command(
        device_id,
        cmd=cmd,
        body=body,
        flags=flags,
        wait_for_ack=wait_for_ack,
        ack_timeout_sec=ack_timeout_sec,
        ack_cmds=ack_cmds,
        mtu_override=mtu_override,
    )


def ble_observations(coordinator: JackerySolarVaultCoordinator) -> dict[str, Any]:
    """Retrieve BLE diagnostic observations from the coordinator listener.

    Returns:
        dict[str, Any]: A dictionary containing BLE diagnostic observations.
    """
    return coordinator.ble_observations()


async def async_start_ble_transport(
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Start the coordinator-owned BLE listener."""
    await coordinator.async_start_ble_transport()
