"""MQTT pack wrappers must not hide serial-bound BatteryPackSub measurements."""

from copy import deepcopy
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    DOMAIN,
    MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    TransportSource,
    normalize_battery_pack_payload,
    normalize_local_mqtt_payload,
)
from custom_components.jackery_solarvault.sensor import (
    BATTERY_PACK_SENSOR_DESCRIPTIONS,
    JackeryBatteryPackSensor,
)
from custom_components.jackery_solarvault.util import stable_subdevice_key

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant

_DEVICE_ID = "device-1"
_HEAD_SN = "SV3PM123456"
_PACK_SN = "PACK-3"
_SIBLING_SN = "PACK-2"
_MEASUREMENTS = {
    "cellTemp": 274,
    "batSoc": 40,
    "inPw": 100,
    "outPw": 0,
    "inEgy": 36560,
    "outEgy": 34762,
    "vendorMeasurement": {"raw": [1, 2]},
}


def _wrapped_pack(wrappers: tuple[str, ...]) -> dict[str, Any]:
    """Wrap telemetry without changing the outer serial or original values."""
    fields = deepcopy(_MEASUREMENTS)
    for wrapper in reversed(wrappers):
        fields = {wrapper: fields}
    return {"deviceSn": _PACK_SN, **fields}


@pytest_asyncio.fixture()
async def coordinator(
    hass: HomeAssistant,
) -> AsyncGenerator[JackerySolarVaultCoordinator]:
    """Provide the real MQTT merge pipeline and shut down its polling timer."""
    entry = MockConfigEntry(domain=DOMAIN)
    instance = JackerySolarVaultCoordinator(
        hass, entry, MagicMock(), timedelta(seconds=15)
    )
    instance._device_index = {_DEVICE_ID: {"device_meta": {"deviceSn": _HEAD_SN}}}  # ruff: ignore[private-member-access]
    try:
        yield instance
    finally:
        await instance.async_shutdown()


@pytest.mark.parametrize(
    "transport", [TransportSource.CLOUD_MQTT, TransportSource.LOCAL_MQTT]
)
@pytest.mark.parametrize(
    "wrappers",
    [
        (),
        ("updates",),
        ("body", "updates"),
        ("properties", "updates"),
        ("properties", "body", "updates"),
        ("body", "properties"),
        ("updates", "body"),
    ],
)
def test_mqtt_nested_pack_measurements_reach_serial_bound_sensors(
    coordinator: JackerySolarVaultCoordinator,
    transport: TransportSource,
    wrappers: tuple[str, ...],
) -> None:
    """Both MQTT layers decode pack telemetry before source choice and sensing."""
    current = {
        "device": {"deviceSn": _HEAD_SN},
        "properties": {"cellTemp": 210},
        "battery_packs": [
            {"deviceSn": _SIBLING_SN, "cellTemp": 205, "outEgy": 1104},
            {"deviceSn": _PACK_SN},
        ],
    }
    coordinator.data = {_DEVICE_ID: current}
    payload = {
        "deviceSn": _HEAD_SN,
        "messageType": MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        "body": {"devType": 1, "batteryPacks": [_wrapped_pack(wrappers)]},
    }
    original = deepcopy(payload)
    routed = (
        normalize_local_mqtt_payload(payload)
        if transport == TransportSource.LOCAL_MQTT
        else payload
    )
    context = coordinator._mqtt_route_context(  # ruff: ignore[private-member-access]
        f"hb/device/{_HEAD_SN}/event", routed, transport
    )
    assert context is not None
    updated = deepcopy(current)
    assert coordinator._apply_mqtt_subdevice_update(context, updated)  # ruff: ignore[private-member-access]
    coordinator.data = {_DEVICE_ID: updated}
    description = next(
        description
        for description in BATTERY_PACK_SENSOR_DESCRIPTIONS
        if description.key == "cell_temperature"
    )
    sensor = JackeryBatteryPackSensor(
        coordinator,
        _DEVICE_ID,
        identity=(3, _PACK_SN, stable_subdevice_key("battery_pack", _PACK_SN, 3)),
        description=description,
    )
    sensor._refresh_cache()  # ruff: ignore[private-member-access]
    assert sensor.native_value == pytest.approx(27.4)
    assert all(sensor._pack[key] == value for key, value in _MEASUREMENTS.items())  # ruff: ignore[private-member-access]
    assert updated["properties"]["cellTemp"] == current["properties"]["cellTemp"]
    assert updated["battery_packs"][0] == current["battery_packs"][0]
    assert payload == original


def test_pack_normalizer_keeps_null_and_non_wrapper_fields() -> None:
    """Flatten only known wrappers, without interpreting opaque vendor fields."""
    pack = {
        "deviceSn": _PACK_SN,
        "cellTemp": 274,
        "body": {"updates": {"cellTemp": None, "outPw": 0}},
        "vendorData": {"cellTemp": 999},
    }
    original = deepcopy(pack)
    normalized = normalize_battery_pack_payload(pack)
    assert normalized["cellTemp"] is None
    assert normalized["outPw"] == 0
    assert normalized["vendorData"] == original["vendorData"]
    assert pack == original


@pytest.mark.parametrize("canonical_soc", [None, 55])
def test_pack_aliases_are_resolved_after_all_wrappers(
    canonical_soc: int | None,
) -> None:
    """An existing canonical field keeps priority over any nested alias."""
    pack = {
        "deviceSn": _PACK_SN,
        "batSoc": canonical_soc,
        "body": {"updates": {"rb": 40, "ip": 100, "op": 0}},
    }
    normalized = normalize_battery_pack_payload(pack)
    assert normalized["batSoc"] == (
        canonical_soc if canonical_soc is not None else _MEASUREMENTS["batSoc"]
    )
    assert normalized["inPw"] == _MEASUREMENTS["inPw"]
    assert normalized["outPw"] == _MEASUREMENTS["outPw"]


def test_pack_wrapper_precedence_and_raw_content_are_preserved() -> None:
    """Properties still win over body and updates, including nested wrappers."""
    pack = {
        "deviceSn": _PACK_SN,
        "cellTemp": 200,
        "updates": {"cellTemp": 210, "inPw": 100},
        "body": {"updates": {"cellTemp": 220, "outPw": 0}},
        "properties": {"body": {"updates": {"cellTemp": 274}}},
    }
    original = deepcopy(pack)
    normalized = normalize_battery_pack_payload(pack)
    assert normalized["cellTemp"] == _MEASUREMENTS["cellTemp"]
    assert normalized["inPw"] == _MEASUREMENTS["inPw"]
    assert normalized["outPw"] == _MEASUREMENTS["outPw"]
    assert normalized["properties"] == original["properties"]
    assert pack == original


def test_nested_wrapper_keeps_existing_identity_precedence() -> None:
    """Nested identity follows the same precedence as an existing flat wrapper."""
    nested = {
        "deviceSn": _SIBLING_SN,
        "body": {"updates": {"deviceSn": _PACK_SN, "cellTemp": 274}},
    }
    flat = {
        "deviceSn": _SIBLING_SN,
        "body": {"deviceSn": _PACK_SN, "cellTemp": 274},
    }
    assert (
        normalize_battery_pack_payload(nested)["deviceSn"]
        == normalize_battery_pack_payload(flat)["deviceSn"]
        == _PACK_SN
    )
