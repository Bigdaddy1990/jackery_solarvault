"""Local MQTT listener uses a scoped default topic when none is configured.

Owner escalation 2026-07-05: the local listener delivered zero frames
because the direct ``JackeryLocalMqttClient`` refused to start with an
empty topic filter, while the UI told users to leave it empty. The device
mirrors its cloud topics onto the local broker under the Jackery prefix
``hb/app/<userId>/{device,alert,config,notice}`` (MQTT_PROTOCOL.md), so an
empty filter must fall back to that scoped prefix subscription rather than
disabling the listener. Broker-wide ``#`` stays blocked for CPU safety.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import (
    _async_start_local_mqtt,  # ruff:ignore[import-private-name]  # tests exercise the module-private setup helper directly
)
from custom_components.jackery_solarvault.const import (
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DOMAIN,
    LOCAL_MQTT_DEFAULT_TOPIC_FILTER,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_empty_filter_falls_back_to_scoped_default(
    hass: HomeAssistant,
) -> None:
    """An empty topic filter must start the listener on the Jackery prefix."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.2.212",
        },
        entry_id="local-mqtt-topic-default",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    client = MagicMock()
    client.async_start = AsyncMock()

    with patch(
        "custom_components.jackery_solarvault.JackeryLocalMqttClient",
        return_value=client,
    ) as client_cls:
        await _async_start_local_mqtt(hass, entry, coordinator)

    client_cls.assert_called_once()
    assert (
        client_cls.call_args.kwargs["topic_filter"] == LOCAL_MQTT_DEFAULT_TOPIC_FILTER
    )
    client.async_start.assert_awaited_once()


async def test_non_jackery_filter_redirects_to_scoped_default(
    hass: HomeAssistant,
) -> None:
    """A stored non-Jackery filter (the "homeassistant" default) is redirected.

    Owner live capture 2026-07-05: the shipped default topic filter was
    "homeassistant", which only matches HA's own event stream, so the direct
    client subscribed to a topic the device never publishes and delivered
    zero frames. Any filter not under the Jackery ``hb/app`` prefix must fall
    back to the scoped ``hb/app/#`` subscription.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.2.212",
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "homeassistant",
        },
        entry_id="local-mqtt-topic-homeassistant",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    client = MagicMock()
    client.async_start = AsyncMock()

    with patch(
        "custom_components.jackery_solarvault.JackeryLocalMqttClient",
        return_value=client,
    ) as client_cls:
        await _async_start_local_mqtt(hass, entry, coordinator)

    client_cls.assert_called_once()
    assert (
        client_cls.call_args.kwargs["topic_filter"] == LOCAL_MQTT_DEFAULT_TOPIC_FILTER
    )
