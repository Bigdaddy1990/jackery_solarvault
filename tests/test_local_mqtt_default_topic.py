"""Local MQTT listener uses exactly one configured broker topic."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import (
    _async_migrate_legacy_local_mqtt_options,
    _async_start_local_mqtt,  # setup helper is the test subject
)
from custom_components.jackery_solarvault.const import (
    CONF_LOCAL_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_QOS,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def test_canonical_listener_values_override_retired_empty_aliases(
    hass: HomeAssistant,
) -> None:
    """Current option keys remain authoritative over retired empty aliases."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            "local_mqtt_username": "",
            "local_mqtt_password": "",
            "local_mqtt_topic": "",
            CONF_THIRD_PARTY_MQTT_USERNAME: "stale-user",
            CONF_THIRD_PARTY_MQTT_PASSWORD: "stale-secret",
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "stale/device/#",
        },
        entry_id="local-mqtt-empty-legacy-migration",
    )
    entry.add_to_hass(hass)

    _async_migrate_legacy_local_mqtt_options(hass, entry)

    assert "local_mqtt_username" not in entry.options
    assert "local_mqtt_password" not in entry.options
    assert "local_mqtt_topic" not in entry.options
    assert entry.options[CONF_THIRD_PARTY_MQTT_USERNAME] == "stale-user"
    assert entry.options[CONF_THIRD_PARTY_MQTT_PASSWORD] == "stale-secret"
    assert entry.options[CONF_THIRD_PARTY_MQTT_TOPIC_FILTER] == "stale/device/#"


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
    client.set_snapshot_requester.assert_called_once()
    coordinator.set_local_mqtt_client.assert_called_once_with(client)
    coordinator.async_schedule_local_mqtt_device_config.assert_not_called()


async def test_whitespace_filter_uses_exact_default_topic(
    hass: HomeAssistant,
) -> None:
    """A whitespace-only stored option cannot replace the default with empty text."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "   ",
        },
        entry_id="local-mqtt-topic-whitespace",
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

    assert (
        client_cls.call_args.kwargs["topic_filter"]
        == DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER
    )


async def test_reconfigure_stop_failure_keeps_existing_subscriber(
    hass: HomeAssistant,
) -> None:
    """A failed unsubscribe cannot be followed by a duplicate replacement client."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "hb/devices/#",
        },
        entry_id="local-mqtt-stop-failure",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    entry.runtime_data = coordinator
    existing = MagicMock()
    existing.async_stop = AsyncMock(side_effect=RuntimeError("still subscribed"))
    coordinator.local_mqtt_client = existing
    bucket = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    bucket["local_mqtt_client"] = existing

    with (
        patch(
            "custom_components.jackery_solarvault._local_mqtt_client",
            return_value=existing,
        ),
        patch(
            "custom_components.jackery_solarvault.JackeryLocalMqttClient"
        ) as client_cls,
        patch(
            "custom_components.jackery_solarvault._schedule_supplemental_cleanup"
        ) as cleanup,
    ):
        await _async_start_local_mqtt(hass, entry, coordinator)

    client_cls.assert_not_called()
    assert "local_mqtt_client" not in bucket
    assert existing in bucket["supplemental_local_mqtt_clients"]
    assert bucket["local_mqtt_restart_after_cleanup"] is True
    coordinator.set_local_mqtt_client.assert_called_once_with(None)
    cleanup.assert_called_once_with(hass, entry)


async def test_plural_official_filter_uses_plural_snapshot_action_tree(
    hass: HomeAssistant,
) -> None:
    """Plural status/event subscriptions request snapshots on plural action topics."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "hb/devices/#",
        },
        entry_id="local-mqtt-topic-plural",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.async_poll_local_mqtt_devices = AsyncMock(return_value=6)
    entry.runtime_data = coordinator
    client = MagicMock()
    client.async_start = AsyncMock()

    with patch(
        "custom_components.jackery_solarvault.JackeryLocalMqttClient",
        return_value=client,
    ):
        await _async_start_local_mqtt(hass, entry, coordinator)

    client.set_snapshot_requester.assert_called_once()
    requester = client.set_snapshot_requester.call_args.args[0]
    assert await requester() == 6
    coordinator.async_poll_local_mqtt_devices.assert_awaited_once_with(
        "hb",
        device_topic_segment="devices",
    )


async def test_explicit_homeassistant_topic_is_preserved_verbatim(
    hass: HomeAssistant,
) -> None:
    """A valid explicitly stored filter must never be silently replaced."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.2.212",
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "homeassistant/#",
            CONF_THIRD_PARTY_MQTT_QOS: 2,
        },
        entry_id="local-mqtt-topic-homeassistant",
    )
    entry.add_to_hass(hass)
    _async_migrate_legacy_local_mqtt_options(hass, entry)
    coordinator = MagicMock()
    coordinator.async_poll_local_mqtt_devices = AsyncMock(return_value=6)
    entry.runtime_data = coordinator
    client = MagicMock()
    client.async_start = AsyncMock()

    with patch(
        "custom_components.jackery_solarvault.JackeryLocalMqttClient",
        return_value=client,
    ) as client_cls:
        await _async_start_local_mqtt(hass, entry, coordinator)

    client_cls.assert_called_once()
    assert client_cls.call_args.kwargs["topic_filter"] == "homeassistant/#"
    assert client_cls.call_args.kwargs["qos"] == 2
    assert entry.options[CONF_THIRD_PARTY_MQTT_TOPIC_FILTER] == "homeassistant/#"
    coordinator.set_local_mqtt_client.assert_called_once_with(client)
    client.set_snapshot_requester.assert_called_once()
    requester = client.set_snapshot_requester.call_args.args[0]
    assert await requester() == 6
    coordinator.async_poll_local_mqtt_devices.assert_awaited_once_with(
        "hb",
        device_topic_segment="device",
    )
    coordinator.async_schedule_local_mqtt_device_config.assert_not_called()


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
    client.set_snapshot_requester.assert_called_once()
    coordinator.set_local_mqtt_client.assert_called_once_with(client)
    coordinator.async_schedule_local_mqtt_device_config.assert_not_called()
