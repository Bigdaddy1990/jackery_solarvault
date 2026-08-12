"""Regression tests for the single SolarVault main-device registry model."""

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import jackery_solarvault as integration
from custom_components.jackery_solarvault.const import DOMAIN, FIELD_ID, PAYLOAD_SYSTEM
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import device_registry as dr

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_DEVICE_ID = "573702884982521856"
_SYSTEM_ID = "595364183558991872"


def test_obsolete_system_parent_is_removed_and_head_is_detached(
    hass: HomeAssistant,
) -> None:
    """Setup migrates the temporary two-main-device layout back to one head."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    parent_identifier = (DOMAIN, f"system_{_SYSTEM_ID}")
    parent = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={parent_identifier},
        name="obsolete system parent",
    )
    head = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, _DEVICE_ID)},
        name="SolarVault 3 Pro Max",
        via_device=parent_identifier,
    )
    assert head.via_device_id == parent.id

    integration._async_remove_legacy_system_parent_devices(  # ruff: ignore[private-member-access]
        hass,
        entry,
    )

    migrated_head = registry.async_get(head.id)
    assert migrated_head is not None
    assert migrated_head.via_device_id is None
    obsolete_parent = registry.async_get_device(identifiers={parent_identifier})
    assert obsolete_parent is None or entry.entry_id not in obsolete_parent.config_entries


async def test_layer5_start_is_scheduled_after_platform_registry_setup(
    hass: HomeAssistant,
) -> None:
    """Optional transports start after platforms without creating a system parent."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: "tester@example.com", CONF_PASSWORD: "secret"},
        entry_id="system-order-entry",
    )
    entry.add_to_hass(hass)
    api = MagicMock(name="JackeryApi")
    api.async_close = AsyncMock(return_value=None)
    api.payload_debug_callback = None
    api.auth_rejection_callback = None
    events: list[str] = []

    async def _prepare_http(
        _hass: HomeAssistant,
        _entry: MockConfigEntry,
        coordinator: Any,
    ) -> None:
        await asyncio.sleep(0)
        coordinator.data = {}

    async def _forward_platforms(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(0)
        events.append("platforms")

    def _schedule_layer5(*_args: Any, **_kwargs: Any) -> None:
        events.append("layer5")

    with (
        patch.object(integration, "JackeryApi", return_value=api),
        patch.object(
            integration,
            "_async_load_entry_caches",
            AsyncMock(return_value=False),
        ),
        patch.object(
            integration,
            "_async_prepare_primary_http",
            side_effect=_prepare_http,
        ),
        patch.object(integration, "_async_clean_legacy_entities"),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=_forward_platforms,
        ),
        patch.object(
            integration,
            "_schedule_layer5_start_if_ready",
            side_effect=_schedule_layer5,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_start_statistics_imports",
            return_value=None,
        ),
    ):
        assert await integration.async_setup_entry(hass, entry)

    assert events == ["platforms", "layer5"]
    coordinator = entry.runtime_data
    runtime_system_id = "runtime-system-1"
    coordinator._push_partial_update({  # ruff: ignore[private-member-access]
        _DEVICE_ID: {PAYLOAD_SYSTEM: {FIELD_ID: runtime_system_id}}
    })
    assert (
        dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, f"system_{runtime_system_id}")}
        )
        is None
    )

    http_system_id = "http-system-2"
    with patch.object(
        coordinator,
        "_async_update_data_guarded",
        AsyncMock(
            return_value={
                "http-device-2": {PAYLOAD_SYSTEM: {FIELD_ID: http_system_id}}
            }
        ),
    ):
        result = await coordinator._async_update_data_with_timeout()  # ruff: ignore[private-member-access]
    assert "http-device-2" in result
    assert (
        dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, f"system_{http_system_id}")}
        )
        is None
    )

    await coordinator.async_shutdown()
