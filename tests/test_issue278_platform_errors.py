"""Behavioral regressions for platform exception handlers repaired in issue 278."""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import jackery_solarvault as integration
from custom_components.jackery_solarvault import sensor as sensor_module
from custom_components.jackery_solarvault.client.local_mqtt import (
    LocalMqttConnectionSettings,
)
from custom_components.jackery_solarvault.const import DOMAIN, PAYLOAD_DEVICE
from custom_components.jackery_solarvault.descriptions.sensor import (
    JackeryStatSensorDescription,
)
from custom_components.jackery_solarvault.entity import JackeryEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.mark.parametrize("native_value", ["not-a-number", 10**400])
async def test_restored_pack_power_rejects_failed_numeric_conversion(
    native_value: str | int,
) -> None:
    """Malformed and overflowing persisted power values must not abort restore."""
    entity = MagicMock()
    entity.async_get_last_sensor_data = AsyncMock(
        return_value=SimpleNamespace(
            native_value=native_value,
            native_unit_of_measurement="W",
        )
    )

    assert (
        await sensor_module._async_restored_pack_measurement_value(entity, "W")  # ruff: ignore[private-member-access]
        is None
    )
    entity.async_get_last_sensor_data.assert_awaited_once_with()


@pytest.mark.parametrize(
    "error", [RuntimeError("HA setup failed"), asyncio.CancelledError()]
)
async def test_stat_sensor_registration_failure_invalidates_refresh_work(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    """Both setup errors and cancellation invalidate the statistic batch state."""
    coordinator = MagicMock()
    entity = sensor_module.JackeryStatSensor(
        coordinator,
        "device-278",
        JackeryStatSensorDescription(key="daily_pv_energy", stat_key="pv"),
    )
    batch = sensor_module._stat_refresh_batch_for(coordinator)  # ruff: ignore[private-member-access]
    registration = AsyncMock(side_effect=error)
    monkeypatch.setattr(JackeryEntity, "async_added_to_hass", registration)

    with pytest.raises(type(error)) as caught:
        await entity.async_added_to_hass()

    assert caught.value is error
    registration.assert_awaited_once_with()
    assert entity._cache_refresh_active is False  # ruff: ignore[private-member-access]
    assert entity._cache_initializing is False  # ruff: ignore[private-member-access]
    assert entity._cache_generation == 1  # ruff: ignore[private-member-access]
    assert not batch._pending  # ruff: ignore[private-member-access]


@pytest.mark.parametrize("timestamp", [object(), "not-a-timestamp"])
def test_timestamp_sensor_rejects_malformed_device_metadata(timestamp: object) -> None:
    """Invalid millisecond timestamps yield an unknown state without raising."""
    coordinator = MagicMock()
    coordinator.data = {"device-278": {PAYLOAD_DEVICE: {"reportedAt": timestamp}}}
    entity = sensor_module.JackeryTimestampSensor(
        coordinator,
        "device-278",
        key="reported_at",
        translation_key="reported_at",
        source_key="reportedAt",
    )

    assert entity.native_value is None


@pytest.mark.parametrize(
    "error", [AttributeError("unavailable ID"), TypeError("invalid ID")]
)
def test_entry_identifier_conversion_failure_uses_stable_object_identity(
    error: Exception,
) -> None:
    """Broken mock identifiers retain a repeatable fallback for runtime buckets."""
    identifier = MagicMock()
    cast("MagicMock", identifier.__str__).side_effect = error
    entry = MagicMock(entry_id=identifier)

    assert integration._get_stable_entry_id(entry) == str(id(entry))  # ruff: ignore[private-member-access]
    assert integration._get_stable_entry_id(entry) == str(id(entry))  # ruff: ignore[private-member-access]


@pytest.mark.parametrize(
    "error", [RuntimeError("MQTT start failed"), asyncio.CancelledError()]
)
async def test_local_mqtt_start_failure_detaches_and_stops_the_client(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    """Failed or cancelled listener startup must clean up its attached runtime."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="mqtt-start-278")
    entry.add_to_hass(hass)
    coordinator = MagicMock(spec=["local_mqtt_client", "set_local_mqtt_client"])
    coordinator.local_mqtt_client = None
    coordinator.set_local_mqtt_client.side_effect = lambda client: setattr(
        coordinator, "local_mqtt_client", client
    )
    entry.runtime_data = coordinator
    client = MagicMock(spec=["async_start", "async_stop", "set_snapshot_requester"])
    client.async_start = AsyncMock(side_effect=error)
    client.async_stop = AsyncMock()
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(integration, "JackeryLocalMqttClient", factory)

    with pytest.raises(type(error)) as caught:
        await integration._async_start_new_local_mqtt_client(  # ruff: ignore[private-member-access]
            hass,
            entry,
            coordinator,
            LocalMqttConnectionSettings(host="mqtt.example.test"),
            snapshot_interval_sec=60,
        )

    assert caught.value is error
    client.async_start.assert_awaited_once_with()
    client.async_stop.assert_awaited_once_with()
    client.set_snapshot_requester.assert_not_called()
    assert coordinator.local_mqtt_client is None
    assert entry.runtime_data is coordinator
    bucket = hass.data[DOMAIN][entry.entry_id]
    assert integration._LOCAL_MQTT_RUNTIME_KEY not in bucket  # ruff: ignore[private-member-access]
