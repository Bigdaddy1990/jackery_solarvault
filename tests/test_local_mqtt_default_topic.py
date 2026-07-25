"""Local MQTT listener uses the device-side local topic verbatim.

The ThirdPartyMqtt device bridge publishes to its configured local topic
(``homeassistant`` by default). ``hb/app/...`` belongs to Jackery Cloud MQTT
and must not replace the local topic. Broker-wide ``#`` stays blocked for CPU
safety.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import (
    _async_start_local_mqtt,  # tests exercise the module-private setup helper directly
)
from custom_components.jackery_solarvault.const import (
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_empty_filter_falls_back_to_local_device_default(
    hass: HomeAssistant,
) -> None:
    """An empty topic filter starts the listener on the local device default."""
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
    entry.runtime_data = coordinator
    client = MagicMock()
    client.async_start = AsyncMock()

    with patch(
        "custom_components.jackery_solarvault.JackeryLocalMqttClient",
        return_value=client,
    ) as client_cls:
        await _async_start_local_mqtt(hass, entry, coordinator)

    client_cls.assert_called_once()
    assert (
        client_cls.call_args.kwargs["topic_filter"]
        == DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER
    )
    client.async_start.assert_awaited_once()


async def test_local_device_topic_is_used_verbatim(
    hass: HomeAssistant,
) -> None:
    """The local ``homeassistant`` topic is not rewritten to a cloud namespace."""
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
    entry.runtime_data = coordinator
    client = MagicMock()
    client.async_start = AsyncMock()

    with patch(
        "custom_components.jackery_solarvault.JackeryLocalMqttClient",
        return_value=client,
    ) as client_cls:
        await _async_start_local_mqtt(hass, entry, coordinator)

    client_cls.assert_called_once()
    assert client_cls.call_args.kwargs["topic_filter"] == "homeassistant"
