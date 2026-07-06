"""Options flow reload behaviour (HA 2026.12 update-listener deprecation).

Home Assistant deprecates config entries that keep a registered update
listener while flow helpers (``async_update_reload_and_abort`` /
``_abort_if_unique_id_configured``) schedule reloads on their behalf
("has an update listener and should use it for scheduling a reload",
breaks in 2026.12). The sanctioned pattern is ``OptionsFlowWithReload``:
the flow manager schedules a full entry reload whenever an options flow
finishes with changed options — and it raises ``ValueError`` if the entry
still has update listeners registered.

These tests pin the migrated behaviour:

* ``async_setup_entry`` registers no update listener.
* Submitting changed options schedules an entry reload.
* Re-submitting identical options does not schedule a redundant reload.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    CONF_ENABLE_BLE_TRANSPORT,
    DOMAIN,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_ACCOUNT = "tester@example.com"


def _make_api_stub() -> MagicMock:
    """Build a ``JackeryApi`` stub covering the entry-setup surface.

    Returns:
        MagicMock: Stub exposing the coroutine surface touched by
        ``async_setup_entry`` and coordinator teardown, with no real IO.
    """
    api = MagicMock(name="JackeryApi")
    api.async_login = AsyncMock(return_value=None)
    api.async_get_mqtt_credentials = AsyncMock(return_value={"user_id": "user-1"})
    api.async_get_system_list = AsyncMock(return_value=[])
    api.async_list_devices_legacy = AsyncMock(return_value=[])
    api.mqtt_session_snapshot = MagicMock(return_value=None)
    api.hydrate_mqtt_session = MagicMock(return_value=None)
    api.async_close = AsyncMock(return_value=None)
    api.payload_debug_callback = None
    api.auth_rejection_callback = None
    return api


async def _async_setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Run the real ``async_setup_entry`` with the API boundary stubbed.

    Returns:
        MockConfigEntry: The fully set-up config entry.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: _ACCOUNT, CONF_PASSWORD: "secret"},
        unique_id=_ACCOUNT,
        title="Jackery",
        entry_id="options-reload-entry",
    )
    entry.add_to_hass(hass)
    api = _make_api_stub()
    with (
        patch(
            "custom_components.jackery_solarvault.JackeryApi",
            return_value=api,
        ),
        patch(
            "custom_components.jackery_solarvault._async_finish_entry_startup",
            AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _async_unload_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Unload the entry so no runtime resources linger past the test."""
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_registers_no_update_listener(hass: HomeAssistant) -> None:
    """The entry must carry no update listener after a full setup.

    A registered update listener triggers the HA 2026.12 deprecation from
    reauth/reconfigure flow helpers and is rejected outright by
    ``OptionsFlowWithReload``.
    """
    entry = await _async_setup_entry(hass)

    assert entry.update_listeners == []

    await _async_unload_entry(hass, entry)


async def test_options_submit_reloads_entry_only_on_change(
    hass: HomeAssistant,
) -> None:
    """Changed options schedule a reload; a no-op re-submit does not."""
    entry = await _async_setup_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    with patch.object(hass.config_entries, "async_schedule_reload") as mock_reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_ENABLE_BLE_TRANSPORT: True},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
    mock_reload.assert_called_once_with(entry.entry_id)
    assert entry.options[CONF_ENABLE_BLE_TRANSPORT] is True

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    with patch.object(hass.config_entries, "async_schedule_reload") as mock_reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_ENABLE_BLE_TRANSPORT: True},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
    mock_reload.assert_not_called()

    await _async_unload_entry(hass, entry)
