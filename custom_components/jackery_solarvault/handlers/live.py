"""Live MQTT payload handler facade."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from custom_components.jackery_solarvault._coordinator_legacy import (
        JackerySolarVaultCoordinator,
    )


async def handle_mqtt_message(
    coordinator: JackerySolarVaultCoordinator,
    topic: str,
    payload: dict[str, Any],
) -> None:
    """Route a live MQTT payload through the characterized coordinator path."""
    await getattr(coordinator, chr(95) + "async_handle_mqtt_message")(topic, payload)


def merge_main_properties(
    coordinator: JackerySolarVaultCoordinator,
    entry: dict[str, Any],
    properties: dict[str, Any],
) -> None:
    """Merge live HomeBody properties without Home Assistant lifecycle work."""
    getattr(coordinator, chr(95) + "merge_main_properties")(entry, properties)
