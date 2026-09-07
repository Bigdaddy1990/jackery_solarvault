"""Local MQTT listener uses the device-side local topic verbatim.

The ThirdPartyMqtt device bridge publishes below ``hb/device/<serial>``.
Configured filters remain exact and broker-wide ``#`` stays blocked.
"""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import (
    _async_migrate_legacy_local_mqtt_options,  # ruff: ignore[import-private-name]
    _async_start_local_mqtt,  # ruff: ignore[import-private-name]
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
        client_cls.call_args.args[1].topic_filter
        == DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER
    )
    client.async_start.assert_awaited_once()


async def test_configured_homeassistant_topic_is_preserved_before_start(
    hass: HomeAssistant,
) -> None:
    """A valid user topic is never silently replaced during setup."""
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
    _async_migrate_legacy_local_mqtt_options(hass, entry)
    assert entry.options[CONF_THIRD_PARTY_MQTT_TOPIC_FILTER] == "homeassistant"
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
    assert client_cls.call_args.args[1].topic_filter == "homeassistant"
