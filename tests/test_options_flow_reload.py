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
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_SCAN_INTERVAL,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DEFAULT_SCAN_INTERVAL_SEC,
    DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DOMAIN,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult
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
            "custom_components.jackery_solarvault._async_prepare_primary_http",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_start_statistics_imports",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault._async_start_layer5_transports",
            AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _async_unload_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Unload the entry so no runtime resources linger past the test."""
    registered_services = dict(hass.services.async_services()[DOMAIN])
    assert registered_services
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.async_services()[DOMAIN] == registered_services


async def _async_submit_options(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    user_input: dict[str, object],
) -> ConfigFlowResult:
    """Submit one real options flow and return its terminal result."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input=user_input,
    )


async def test_setup_registers_no_config_entry_update_listener(
    hass: HomeAssistant,
) -> None:
    """Flow-managed reloads must not race a config-entry update listener."""
    entry = await _async_setup_entry(hass)
    try:
        assert not entry.update_listeners
    finally:
        await _async_unload_entry(hass, entry)


async def test_scan_interval_option_is_applied_without_reload(
    hass: HomeAssistant,
) -> None:
    """Ordinary options update the running coordinator without pausing it."""
    entry = await _async_setup_entry(hass)
    try:
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            result = await _async_submit_options(
                hass,
                entry,
                {
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_SEC + 1,
                    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: (
                        DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER
                    ),
                },
            )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        reload.assert_not_called()
        assert entry.options[CONF_SCAN_INTERVAL] == DEFAULT_SCAN_INTERVAL_SEC + 1
        assert (
            entry.runtime_data.configured_update_interval.total_seconds()
            == DEFAULT_SCAN_INTERVAL_SEC + 1
        )
    finally:
        await _async_unload_entry(hass, entry)


async def test_entity_creating_option_schedules_exactly_one_reload(
    hass: HomeAssistant,
) -> None:
    """Options requiring new entities use Core's single automatic reload."""
    entry = await _async_setup_entry(hass)
    try:
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            result = await _async_submit_options(
                hass,
                entry,
                {
                    CONF_CREATE_CALCULATED_POWER_SENSORS: True,
                    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: (
                        DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER
                    ),
                },
            )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        reload.assert_called_once_with(entry.entry_id)
    finally:
        await _async_unload_entry(hass, entry)


async def test_identical_semantic_options_do_not_reload(
    hass: HomeAssistant,
) -> None:
    """Persisting implicit defaults must not pause an already running entry."""
    entry = await _async_setup_entry(hass)
    try:
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            result = await _async_submit_options(
                hass,
                entry,
                {
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_SEC,
                    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: (
                        DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER
                    ),
                },
            )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        reload.assert_not_called()
    finally:
        await _async_unload_entry(hass, entry)
