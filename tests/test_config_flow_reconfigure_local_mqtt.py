"""Regression tests: credentials-reconfigure must not wipe local-MQTT options.

P8 finding 2026-07-03: the success path of ``reconfigure_credentials``
stored ``options=_flow_options(...)``, which only emits the boolean
sensor/statistic toggle keys. ``async_update_reload_and_abort`` replaces
``entry.options`` completely, so every credentials submit silently
deleted a previously working ``local_mqtt_*`` / ``third_party_mqtt_*``
configuration — and the local-MQTT fields shown on the very same form
were discarded. Additionally the broker IP field rendered as ``bool``,
making a hostname impossible to enter.
"""

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DOMAIN,
    FLOW_ABORT_RECONFIGURE_SUCCESSFUL,
    FLOW_STEP_RECONFIGURE_CREDENTIALS,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_ACCOUNT = "tester@example.com"


def _make_api_stub() -> MagicMock:
    """Build a ``JackeryApi`` stub for flow validation and entry reload.

    Returns:
        MagicMock: Stub exposing the coroutine surface touched by the
        reconfigure login check and the subsequent entry setup.
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


async def _submit_reconfigure_credentials(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    user_input: dict[str, Any],
) -> dict[str, Any]:
    """Drive the real reconfigure menu + credentials form to completion.

    Returns:
        dict[str, Any]: The final flow result.
    """
    api = _make_api_stub()
    with (
        patch(
            "custom_components.jackery_solarvault.config_flow.JackeryApi",
            return_value=api,
        ),
        patch(
            "custom_components.jackery_solarvault.JackeryApi",
            return_value=api,
        ),
        patch(
            "custom_components.jackery_solarvault._async_finish_entry_startup",
            AsyncMock(return_value=None),
        ),
    ):
        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] is FlowResultType.MENU
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"next_step_id": FLOW_STEP_RECONFIGURE_CREDENTIALS},
        )
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input,
        )
        await hass.async_block_till_done()
    return result


async def test_reconfigure_credentials_preserves_local_mqtt_options(
    hass: HomeAssistant,
) -> None:
    """A pure credentials update keeps an existing local-MQTT setup alive."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: _ACCOUNT, CONF_PASSWORD: "old-secret"},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "192.168.1.10",
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "jackery/#",
        },
        unique_id=_ACCOUNT,
        title="Jackery",
    )
    entry.add_to_hass(hass)

    result = await _submit_reconfigure_credentials(
        hass,
        entry,
        {CONF_USERNAME: _ACCOUNT, CONF_PASSWORD: "new-secret"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_RECONFIGURE_SUCCESSFUL
    assert entry.data[CONF_PASSWORD] == "new-secret"
    assert entry.options[CONF_LOCAL_MQTT_ENABLE] is True
    assert entry.options[CONF_LOCAL_MQTT_HOST] == "192.168.1.10"
    assert entry.options[CONF_THIRD_PARTY_MQTT_TOPIC_FILTER] == "jackery/#"


async def test_reconfigure_credentials_can_enable_local_mqtt(
    hass: HomeAssistant,
) -> None:
    """The local-MQTT fields on the reconfigure form actually take effect.

    Also pins the broker IP field accepting a hostname string — it
    historically rendered as ``bool``, so no host could be entered.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: _ACCOUNT, CONF_PASSWORD: "secret"},
        unique_id=_ACCOUNT,
        title="Jackery",
    )
    entry.add_to_hass(hass)

    result = await _submit_reconfigure_credentials(
        hass,
        entry,
        {
            CONF_USERNAME: _ACCOUNT,
            CONF_PASSWORD: "secret",
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "10.0.0.5",
            CONF_THIRD_PARTY_MQTT_IP: "192.168.1.2",
        },
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == FLOW_ABORT_RECONFIGURE_SUCCESSFUL
    assert entry.options[CONF_LOCAL_MQTT_ENABLE] is True
    assert entry.options[CONF_LOCAL_MQTT_HOST] == "10.0.0.5"
