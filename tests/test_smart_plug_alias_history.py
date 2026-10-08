"""An observed physical replacement cannot regain an older socket's cloud alias."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_CONTROL_ALLOWED,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_IS_CLOUD,
    FIELD_SWITCH_STATE,
    PAYLOAD_SMART_PLUGS,
)
from custom_components.jackery_solarvault.coordinator import (
    smart_plug_entity_identity,
    smart_plug_entity_payload,
)
from custom_components.jackery_solarvault.switch import JackerySmartPlugSwitch
from homeassistant.exceptions import HomeAssistantError

_PARENT = "parent"
_ORIGINAL = "SERIAL-A"
_REPLACEMENT = "SERIAL-B"
_CLOUD = "cloud-C"


def _cloud_record(serial: str | None = None) -> dict[str, Any]:
    """Produce permitted cloud control with an optional explicit physical serial."""
    record: dict[str, Any] = {
        FIELD_DEVICE_ID: _CLOUD,
        FIELD_CONTROL_ALLOWED: 1,
        FIELD_IS_CLOUD: 1,
        FIELD_SWITCH_STATE: 0,
    }
    if serial is not None:
        record[FIELD_DEVICE_SN] = serial
    return record


def _coordinator() -> MagicMock:
    """Use production identity and switch logic with mocked transport boundaries."""
    coordinator = MagicMock(name="socket-replacement-coordinator")
    coordinator.config_entry = None
    coordinator.hass = None
    first = _cloud_record(_ORIGINAL)
    coordinator.data = {_PARENT: {PAYLOAD_SMART_PLUGS: [first]}}
    coordinator.async_set_shelly_cloud_switch = AsyncMock()
    coordinator.async_set_smart_plug_switch = AsyncMock()
    assert smart_plug_entity_identity(coordinator, _PARENT, first) == _ORIGINAL
    return coordinator


def _observe_replacement_then_sparse_telemetry(coordinator: MagicMock) -> None:
    """Capture a different physical owner before its next ID-only telemetry."""
    replacement = _cloud_record(_REPLACEMENT)
    coordinator.data[_PARENT] = {PAYLOAD_SMART_PLUGS: [replacement]}
    assert smart_plug_entity_payload(coordinator, _PARENT, _ORIGINAL) == {}
    assert smart_plug_entity_identity(coordinator, _PARENT, replacement) == _REPLACEMENT
    coordinator.data[_PARENT] = {PAYLOAD_SMART_PLUGS: [_cloud_record()]}


def test_id_only_telemetry_cannot_rebind_a_replaced_physical_entity() -> None:
    """A proven serial change remains fail-closed when later telemetry omits it."""
    coordinator = _coordinator()

    _observe_replacement_then_sparse_telemetry(coordinator)

    assert smart_plug_entity_payload(coordinator, _PARENT, _ORIGINAL) == {}


async def test_replaced_entity_cannot_switch_sparse_cloud_telemetry() -> None:
    """The older entity cannot send a permitted cloud command to its replacement."""
    coordinator = _coordinator()
    relay = JackerySmartPlugSwitch(
        coordinator,
        _PARENT,
        plug_index=1,
        plug_sn=_ORIGINAL,
        plug_key="smart_plug_serial_a",
    )
    await relay.async_turn_on()
    coordinator.async_set_shelly_cloud_switch.assert_awaited_once_with(
        _PARENT, shelly_device_id=_CLOUD, on=True
    )
    coordinator.async_set_shelly_cloud_switch.reset_mock()

    _observe_replacement_then_sparse_telemetry(coordinator)

    with pytest.raises(HomeAssistantError):
        await relay.async_turn_on()
    coordinator.async_set_shelly_cloud_switch.assert_not_awaited()
    coordinator.async_set_smart_plug_switch.assert_not_awaited()
