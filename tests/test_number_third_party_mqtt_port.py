"""The third-party MQTT port number must present as an integer.

Live bug: the entity rendered as ``1883,0`` in the UI because
``JackeryNumber.native_value`` returned ``float(round(...))`` for
``integer_value`` descriptions, so the state string was ``"1883.0"``.
A TCP port (and any count-like value) has no fractional component: the
state must be ``"1883"`` with a step of ``1``.

The test drives the real number platform through a real HA instance —
only the ``JackeryApi`` network boundary is stubbed.
"""

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FIELD_DEVICE_SN,
    FIELD_THIRD_PARTY_MQTT_PORT,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_PROPERTIES,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_DEVICE_ID = "dev-vault-1"
_DEVICE_SN = "SN-VAULT-0001"


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


_PORT_MAX = 65535


def _device_payload_with_port(port: float | str) -> dict[str, dict[str, Any]]:
    """Build a coordinator snapshot with a third-party MQTT config section.

    Parameters:
        port (Any): The wire value reported for the broker port.

    Returns:
        dict[str, dict[str, Any]]: ``coordinator.data`` mapping that gates
        the ``third_party_mqtt_port`` number entity into existence.
    """
    return {
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {FIELD_DEVICE_SN: _DEVICE_SN},
            PAYLOAD_DISCOVERY: {FIELD_DEVICE_SN: _DEVICE_SN},
            PAYLOAD_PROPERTIES: {"soc": 55},
            PAYLOAD_THIRD_PARTY_MQTT_CONFIG: {FIELD_THIRD_PARTY_MQTT_PORT: port},
        },
    }


async def test_third_party_mqtt_port_state_is_integer(
    hass: HomeAssistant,
) -> None:
    """The port entity reports ``1883`` (not ``1883.0``) with step ``1``."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: "tester@example.com", CONF_PASSWORD: "secret"},
        title="Jackery",
        entry_id="port-number-entry",
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

    coordinator = entry.runtime_data
    coordinator.async_set_updated_data(_device_payload_with_port(1883))
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "number",
        DOMAIN,
        f"{_DEVICE_ID}_third_party_mqtt_port",
    )
    assert entity_id is not None, "third_party_mqtt_port entity was not created"

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "1883"
    assert state.attributes["step"] == 1
    assert state.attributes["min"] == 1
    assert state.attributes["max"] == _PORT_MAX

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
