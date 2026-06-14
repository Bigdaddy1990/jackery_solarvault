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
    return coordinator._ble_writes_enabled_impl()  # noqa: SLF001


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
    """Send one BLE command through the coordinator-owned listener."""
    return await coordinator._async_send_ble_command_impl(  # noqa: SLF001
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
    """Return BLE diagnostic observations from the coordinator listener."""
    return coordinator._ble_observations_impl()  # noqa: SLF001


async def async_start_ble_transport(
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Start the coordinator-owned BLE listener."""
    await coordinator._async_start_ble_transport_impl()  # noqa: SLF001
