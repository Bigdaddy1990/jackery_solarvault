"""Options flow exposes a SINGLE MQTT-bridge mask (owner rule 2026-07-05).

The owner requires one input mask for the local MQTT bridge, not two. An
earlier revision added a duplicate ``local_mqtt_*`` field block on top of
the existing ``third_party_mqtt_*`` fields, so the options dialog showed
two host/port/credential sets for the same broker. The ``third_party_mqtt_*``
fields are the canonical mask (the device-control entities and the
ThirdPartMQTTConfig codec key off them); the local listener derives its
``local_mqtt_*`` values from them via ``_merge_local_mqtt_options``.

These tests pin:

* The options form exposes the third-party bridge fields and NO duplicate
  ``local_mqtt_*`` fields.
* Submitting the bridge fields persists them AND derives the ``local_mqtt_*``
  keys the coordinator/listener consume.
* A no-op submit preserves an existing bridge setup.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    DOMAIN,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_ACCOUNT = "tester@example.com"
_BRIDGE_PORT = 1885
# The duplicate local-MQTT field block the owner asked to remove. The options
# form must NOT expose these — the third-party fields are the single mask.
_DUPLICATE_LOCAL_FIELDS = (
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_LOCAL_MQTT_PASSWORD,
)


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


async def _async_setup_entry(
    hass: HomeAssistant,
    options: dict[str, object] | None = None,
) -> MockConfigEntry:
    """Run the real ``async_setup_entry`` with the API boundary stubbed.

    Returns:
        MockConfigEntry: The fully set-up config entry.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: _ACCOUNT, CONF_PASSWORD: "secret"},
        options=options or {},
        unique_id=_ACCOUNT,
        title="Jackery",
        entry_id="options-local-mqtt-entry",
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


async def test_options_form_has_single_bridge_mask(hass: HomeAssistant) -> None:
    """One bridge mask: third-party fields present, no duplicate local fields."""
    entry = await _async_setup_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema_keys = {str(key) for key in result["data_schema"].schema}

    assert CONF_THIRD_PARTY_MQTT_ENABLE in schema_keys
    assert CONF_THIRD_PARTY_MQTT_IP in schema_keys
    duplicates = [f for f in _DUPLICATE_LOCAL_FIELDS if f in schema_keys]
    assert not duplicates, f"options form still shows duplicate fields: {duplicates}"

    await _async_unload_entry(hass, entry)


async def test_bridge_submit_persists_and_derives_local(hass: HomeAssistant) -> None:
    """Submitting the single bridge mask drives the local listener too.

    The third-party fields are the one mask; the coordinator's local
    listener reads ``local_mqtt_*``, which ``_merge_local_mqtt_options``
    derives from the submitted third-party values.
    """
    entry = await _async_setup_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_THIRD_PARTY_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.2.212",
            CONF_THIRD_PARTY_MQTT_PORT: _BRIDGE_PORT,
            CONF_THIRD_PARTY_MQTT_USERNAME: "mqtt_user",
            CONF_THIRD_PARTY_MQTT_PASSWORD: "s3cret",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    # Canonical mask persisted.
    assert entry.options[CONF_THIRD_PARTY_MQTT_ENABLE] is True
    assert entry.options[CONF_THIRD_PARTY_MQTT_IP] == "192.168.2.212"
    assert entry.options[CONF_THIRD_PARTY_MQTT_PORT] == _BRIDGE_PORT
    # Local listener values derived from the single mask.
    assert entry.options[CONF_LOCAL_MQTT_ENABLE] is True
    assert entry.options[CONF_LOCAL_MQTT_HOST] == "192.168.2.212"
    assert entry.options[CONF_LOCAL_MQTT_PORT] == _BRIDGE_PORT

    await _async_unload_entry(hass, entry)


async def test_untouched_submit_preserves_bridge(hass: HomeAssistant) -> None:
    """A no-op options submit must not disable a stored bridge setup."""
    entry = await _async_setup_entry(
        hass,
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.2.212",
        },
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    assert entry.options[CONF_THIRD_PARTY_MQTT_ENABLE] is True
    assert entry.options[CONF_THIRD_PARTY_MQTT_IP] == "192.168.2.212"
    assert entry.options[CONF_LOCAL_MQTT_ENABLE] is True

    await _async_unload_entry(hass, entry)


async def test_untouched_submit_preserves_disabled_bridge(
    hass: HomeAssistant,
) -> None:
    """An explicitly disabled bridge must not be re-enabled by defaults."""
    entry = await _async_setup_entry(
        hass,
        options={CONF_THIRD_PARTY_MQTT_ENABLE: False},
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    assert entry.options[CONF_THIRD_PARTY_MQTT_ENABLE] is False
    assert entry.options[CONF_LOCAL_MQTT_ENABLE] is False

    await _async_unload_entry(hass, entry)
