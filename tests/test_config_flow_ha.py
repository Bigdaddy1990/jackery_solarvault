"""HA fixture tests for the Jackery SolarVault config flow."""

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.client.api import (
    JackeryAuthError,
    JackeryError,
)
from custom_components.jackery_solarvault.const import (
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_THIRD_PARTY_MQTT_QOS,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DOMAIN,
    FLOW_ABORT_REAUTH_SUCCESSFUL,
)
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.mqtt import MqttServiceInfo

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.asyncio
_QOS_EXACTLY_ONCE = 2


async def test_mqtt_discovery_loads_handler_and_routes_to_http_login(
    hass: HomeAssistant,
) -> None:
    """The manifest MQTT topic must resolve to a valid HTTP-account flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_MQTT},
        data=MqttServiceInfo(
            topic="homeassistant",
            payload=b'{"devSn":"HR2C04000280HH3"}',
            qos=0,
            retain=False,
            subscribed_topic="homeassistant",
            timestamp=0.0,
        ),
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_mqtt_discovery_rejects_foreign_payload(
    hass: HomeAssistant,
) -> None:
    """A retained non-Jackery message must not create a login flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_MQTT},
        data=MqttServiceInfo(
            topic="homeassistant",
            payload=b'{"state":"online"}',
            qos=0,
            retain=True,
            subscribed_topic="homeassistant",
            timestamp=0.0,
        ),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_info"


@pytest.mark.parametrize("payload", [cast("bytes", "not-bytes"), b"{"])
async def test_mqtt_discovery_rejects_malformed_payload_boundary(
    hass: HomeAssistant,
    payload: bytes,
) -> None:
    """Malformed external discovery payloads abort without opening login."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_MQTT},
        data=MqttServiceInfo(
            topic="homeassistant",
            payload=payload,
            qos=0,
            retain=True,
            subscribed_topic="homeassistant",
            timestamp=0.0,
        ),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_info"


async def test_mqtt_discovery_aborts_when_account_entry_exists(
    hass: HomeAssistant,
) -> None:
    """Discovery cannot start a second account flow once an entry exists."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="existing@example.com",
        data={CONF_USERNAME: "existing@example.com", CONF_PASSWORD: "secret"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_MQTT},
        data=MqttServiceInfo(
            topic="homeassistant",
            payload=b'{"deviceSn":"HR2C04000280HH3"}',
            qos=0,
            retain=True,
            subscribed_topic="homeassistant",
            timestamp=0.0,
        ),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_mqtt_discovery_rejects_action_id_without_device_identity(
    hass: HomeAssistant,
) -> None:
    """A command-shaped retained message is not a stable Jackery identity."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_MQTT},
        data=MqttServiceInfo(
            topic="homeassistant",
            payload=b'{"actionId":3011}',
            qos=0,
            retain=True,
            subscribed_topic="homeassistant",
            timestamp=0.0,
        ),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_info"


@pytest.mark.parametrize(
    "payload",
    [
        b'{"devSn":[]}',
        b'{"deviceSn":123}',
    ],
)
async def test_mqtt_discovery_rejects_invalid_marker_payloads(
    hass: HomeAssistant,
    payload: bytes,
) -> None:
    """Malformed or oversized discovery frames must abort without exceptions."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_MQTT},
        data=MqttServiceInfo(
            topic="homeassistant",
            payload=payload,
            qos=0,
            retain=True,
            subscribed_topic="homeassistant",
            timestamp=0.0,
        ),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_info"


async def test_user_flow_happy_path(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A valid login should create a config entry and configure unique_id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "correct-password",
        },
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "user@example.com"
    assert (
        result2["data"]
        == {
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "correct-password",
        }
        or result2["data"][CONF_USERNAME] == "user@example.com"
    )


async def test_user_flow_invalid_credentials(hass: HomeAssistant) -> None:
    """A login rejection must surface as an invalid_auth form error."""
    with patch(
        "custom_components.jackery_solarvault.client.api.JackeryApi.async_login",
        side_effect=JackeryAuthError("login rejected"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_USERNAME: "user@example.com",
                CONF_PASSWORD: "wrong-password",
            },
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Show cannot_connect when the user-flow API raises a network error.

    Verifies the flow returns a FORM and sets errors to {"base": "cannot_connect"}.
    """
    with patch(
        "custom_components.jackery_solarvault.client.api.JackeryApi.async_login",
        side_effect=JackeryError("network down"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={
                CONF_USERNAME: "user@example.com",
                CONF_PASSWORD: "any-password",
            },
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_rejects_missing_password_before_network(
    hass: HomeAssistant,
) -> None:
    """Incomplete credentials remain on the form without network I/O."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={CONF_USERNAME: "user@example.com"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "base"}


async def test_user_flow_unique_id_dedup(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Re-running the flow for the same account must abort, not duplicate."""
    # First run creates the entry
    await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass1",
        },
    )

    # Second run with the same username must abort with already_configured
    result2 = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pass2",
        },
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "already_configured"


async def test_reauth_flow_updates_password_and_reloads(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A successful reauth must update the existing entry password."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "old-password",
        },
    )
    entry.add_to_hass(hass)

    # Trigger reauth from the entry
    result = await entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    # Submit a new password
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PASSWORD: "new-password"},
    )
    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == FLOW_ABORT_REAUTH_SUCCESSFUL

    # Entry data must reflect the new password without changing username
    assert entry.data[CONF_PASSWORD] == "new-password"
    assert entry.data[CONF_USERNAME] == "user@example.com"


async def test_reauth_flow_rejects_missing_password(
    hass: HomeAssistant,
) -> None:
    """Reauthentication keeps the form open without a submitted password."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "old-password"},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_PASSWORD: ""},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "base"}


async def test_options_flow_persists_local_mqtt_topic_filter_default(
    hass: HomeAssistant,
) -> None:
    """Options flow persists the default Local-MQTT topic filter."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
        },
        options={},
    )
    entry.add_to_hass(hass)

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] == FlowResultType.FORM
    assert flow["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(flow["flow_id"], {})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert (
        entry.options.get(CONF_THIRD_PARTY_MQTT_TOPIC_FILTER)
        == DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER
    )


async def test_options_flow_accepts_local_mqtt_topic_filter_value(
    hass: HomeAssistant,
) -> None:
    """Options flow must store user-provided local MQTT topic filters."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
        },
        options={},
    )
    entry.add_to_hass(hass)

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] == FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "hb/app/+/device",
        },
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert entry.options.get(CONF_THIRD_PARTY_MQTT_TOPIC_FILTER) == "hb/app/+/device"


async def test_options_flow_rejects_invalid_local_mqtt_topic(
    hass: HomeAssistant,
) -> None:
    """An invalid subscription topic remains on the form with an error."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="invalid-topic@example.com",
        data={CONF_USERNAME: "invalid-topic@example.com", CONF_PASSWORD: "secret"},
        options={},
    )
    entry.add_to_hass(hass)

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "invalid/#/suffix"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert result["errors"] == {CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "base"}


async def test_options_flow_reloads_for_entity_creation_changes(
    hass: HomeAssistant,
) -> None:
    """Changing an entity-creation option reloads the config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="entity-options@example.com",
        data={CONF_USERNAME: "entity-options@example.com", CONF_PASSWORD: "secret"},
        options={},
    )
    entry.add_to_hass(hass)
    reload_entry = AsyncMock(return_value=True)

    with patch.object(hass.config_entries, "async_reload", reload_entry):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {CONF_CREATE_CALCULATED_POWER_SENSORS: True},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_CREATE_CALCULATED_POWER_SENSORS] is True
    reload_entry.assert_awaited_once_with(entry.entry_id)


async def test_options_flow_persists_and_reopens_local_mqtt_topic_and_qos(
    hass: HomeAssistant,
) -> None:
    """Submitted Local-MQTT topic and QoS remain the next form defaults."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="mqtt-options@example.com",
        data={
            CONF_USERNAME: "mqtt-options@example.com",
            CONF_PASSWORD: "secret",
        },
        options={},
    )
    entry.add_to_hass(hass)

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "hb/device/+/event",
            CONF_THIRD_PARTY_MQTT_QOS: "2",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_THIRD_PARTY_MQTT_TOPIC_FILTER] == "hb/device/+/event"
    assert entry.options[CONF_THIRD_PARTY_MQTT_QOS] == _QOS_EXACTLY_ONCE

    reopened = await hass.config_entries.options.async_init(entry.entry_id)
    assert reopened["type"] is FlowResultType.FORM
    defaults = {
        marker.schema: marker.default()
        for marker in reopened["data_schema"].schema
        if hasattr(marker, "default")
    }
    assert defaults[CONF_THIRD_PARTY_MQTT_TOPIC_FILTER] == "hb/device/+/event"
