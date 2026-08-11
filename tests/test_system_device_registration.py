"""Regression tests for SolarVault parent-system registry ordering."""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import jackery_solarvault as integration
from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FIELD_ID,
    FIELD_SYSTEM_NAME,
    FIELD_SYSTEM_SN,
    PAYLOAD_SYSTEM,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import device_registry as dr

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_DEVICE_ID = "573702884982521856"
_SYSTEM_ID = "595364183558991872"
_SYSTEM_SN = "SYSTEM-SN"


def test_system_parent_is_registered_before_platform_setup(
    hass: HomeAssistant,
) -> None:
    """Central setup creates the exact parent later referenced by via_device."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    coordinator = SimpleNamespace(
        data={
            _DEVICE_ID: {
                PAYLOAD_SYSTEM: {
                    FIELD_ID: _SYSTEM_ID,
                    FIELD_SYSTEM_NAME: "SolarVault",
                    FIELD_SYSTEM_SN: _SYSTEM_SN,
                }
            }
        }
    )

    integration._async_register_system_devices(  # ruff: ignore[private-member-access]
        hass,
        entry,
        cast("Any", coordinator),
    )

    parent_identifier = (DOMAIN, f"system_{_SYSTEM_ID}")
    parent = dr.async_get(hass).async_get_device(identifiers={parent_identifier})
    assert parent is not None
    assert parent.config_entries == {entry.entry_id}
    assert parent.serial_number == _SYSTEM_SN
    assert parent_identifier in parent.identifiers


async def test_layer5_start_is_scheduled_after_platform_registry_setup(
    hass: HomeAssistant,
) -> None:
    """Optional transports cannot change parent ids during entity creation."""
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
    listener_saw_parent = False

    def _listener() -> None:
        nonlocal listener_saw_parent
        parent = dr.async_get(hass).async_get_device(
            identifiers={(DOMAIN, f"system_{runtime_system_id}")}
        )
        listener_saw_parent = parent is not None

    unsubscribe = coordinator.async_add_listener(_listener)
    coordinator._push_partial_update({  # ruff: ignore[private-member-access]
        _DEVICE_ID: {PAYLOAD_SYSTEM: {FIELD_ID: runtime_system_id}}
    })
    unsubscribe()
    assert listener_saw_parent

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
        is not None
    )

    await coordinator.async_shutdown()
