"""Local MQTT listener uses exactly one configured broker topic."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import (
    _async_start_local_mqtt,  # setup helper is the test subject  # ruff: ignore[import-private-name]
)
from custom_components.jackery_solarvault.const import (
    CONF_LOCAL_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_empty_filter_uses_exact_default_topic(
    hass: HomeAssistant,
) -> None:
    """An empty option uses the app-compatible exact topic."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
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
    coordinator.async_schedule_local_mqtt_device_config.assert_called_once_with()


async def test_homeassistant_topic_is_preserved_verbatim(
    hass: HomeAssistant,
) -> None:
    """The known device topic must never be rewritten to a wildcard sentinel."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
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
    coordinator.async_schedule_local_mqtt_device_config.assert_called_once_with()


async def test_explicit_scoped_local_topic_is_used_verbatim(
    hass: HomeAssistant,
) -> None:
    """A user-proven scoped topic is used verbatim."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.2.212",
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "jackery/device/telemetry",
        },
        entry_id="local-mqtt-topic-explicit",
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
    assert client_cls.call_args.kwargs["topic_filter"] == "jackery/device/telemetry"
    coordinator.async_schedule_local_mqtt_device_config.assert_called_once_with()
