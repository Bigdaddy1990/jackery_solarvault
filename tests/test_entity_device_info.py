"""Regression: device_info falls back family-neutral, never to the literal "SolarVault".

HomePower is SolarVault's predecessor (same device family). A device whose
model/name source fields arrive blank must not be mislabeled with the literal
brand "SolarVault" in the Home Assistant device registry. The real reported
model/name always wins when present — only the fallback changes.
"""

from types import SimpleNamespace
from typing import Any, cast

from custom_components.jackery_solarvault.binary_sensor import (
    JackerySubdeviceAlarmBinarySensor,
)
from custom_components.jackery_solarvault.const import (
    DEFAULT_DEVICE_MODEL_FALLBACK,
    FIELD_DEV_MODEL,
    PAYLOAD_DISCOVERY,
)
from custom_components.jackery_solarvault.entity import JackeryEntity
from custom_components.jackery_solarvault.sensor import (
    JackeryBatteryPackSensor,
    JackeryBreakerSensor,
    JackeryMeterHeadSensor,
    JackerySmartMeterSensor,
    JackerySubdeviceAlarmSensor,
)
from custom_components.jackery_solarvault.switch import JackeryBreakerSwitch
from custom_components.jackery_solarvault.util import stable_subdevice_key

_DEVICE_ID = "home-power-3002"


def _entity(payload: dict[str, Any]) -> JackeryEntity:
    """Return a bare ``JackeryEntity`` bound to a single-device coordinator snapshot.

    Bypasses ``__init__`` (mirrors ``test_coordinator_homepower_discovery.py``) so
    ``device_info`` exercises only its real name/model resolution logic without
    hass wiring: every ``_system``/``_discovery``/``_device_meta`` property reads
    from ``coordinator.data[device_id]``.
    """
    entity = JackeryEntity.__new__(JackeryEntity)
    mutable = cast("Any", entity)
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.coordinator = SimpleNamespace(data={_DEVICE_ID: payload})
    return entity


def _bound(cls: type[Any], payload: dict[str, Any], **extra: Any) -> Any:
    """Instantiate any ``JackeryEntity`` subclass bound to a coordinator snapshot.

    Mirrors ``_entity()`` above for the subdevice sensor/switch/binary_sensor
    subclasses, so their ``device_info``/``_build_*_device_info`` builders can be
    exercised without hass wiring or the real ``__init__``. ``extra`` sets the
    per-subclass identity attributes (e.g. ``_pack_index``, ``_sub_device_sn``)
    a given builder reads directly.
    """
    instance = cls.__new__(cls)
    mutable = cast("Any", instance)
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.coordinator = SimpleNamespace(data={_DEVICE_ID: payload})
    for name, value in extra.items():
        setattr(mutable, name, value)
    return instance


def test_device_info_model_falls_back_family_neutral() -> None:
    """Blank model source fields resolve to the neutral fallback, not "SolarVault"."""
    info = _entity({}).device_info

    assert info["model"] == DEFAULT_DEVICE_MODEL_FALLBACK
    assert info["model"] != "SolarVault"


def test_device_info_model_uses_reported_model_when_present() -> None:
    """A real reported model always wins over the fallback."""
    payload = {PAYLOAD_DISCOVERY: {FIELD_DEV_MODEL: "HTH0132500A"}}

    assert _entity(payload).device_info["model"] == "HTH0132500A"


def test_smart_plug_base_name_falls_back_to_jackery_device_id() -> None:
    """Blank name fields yield a "Jackery {device_id}" prefix, not "SolarVault"."""
    info = _entity({})._build_smart_plug_device_info(1, {})  # ruff: ignore[private-member-access]

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert "SolarVault" not in info["name"]


def test_breaker_switch_falls_back_to_jackery() -> None:
    """A HomePower breaker with blank name fields is never labelled "SolarVault"."""
    entity = _bound(JackeryBreakerSwitch, {})
    info = entity._build_breaker_device_info(0, {}, "breaker_0")  # ruff: ignore[private-member-access]

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert info["model"] == "Jackery Sicherung"
    assert "SolarVault" not in info["name"]
    assert "SolarVault" not in str(info["model"])


def test_subdevice_alarm_binary_sensor_falls_back_to_jackery() -> None:
    """A HomePower accessory with blank name/model fields is never "SolarVault"."""
    entity = _bound(JackerySubdeviceAlarmBinarySensor, {}, _sub_device_sn="SN1")
    info = entity._build_sub_device_device_info(0, {}, "sub_0")  # ruff: ignore[private-member-access]

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert info["model"] == "Jackery Zubehör"
    assert "SolarVault" not in info["name"]
    assert "SolarVault" not in str(info["model"])


def test_battery_pack_sensor_falls_back_to_jackery() -> None:
    """A HomePower battery pack with blank name/model fields is never "SolarVault"."""
    entity = _bound(
        JackeryBatteryPackSensor,
        {},
        _pack_index=1,
        _pack_sn=None,
        _pack_key=stable_subdevice_key("battery_pack", None, 1),
    )
    info = entity.device_info

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert info["model"] == "Jackery Zusatzbatterie"
    assert "SolarVault" not in info["name"]
    assert "SolarVault" not in str(info["model"])


def test_breaker_sensor_falls_back_to_jackery() -> None:
    """A HomePower breaker sensor with blank name fields is never "SolarVault"."""
    entity = _bound(JackeryBreakerSensor, {})
    info = entity._build_breaker_device_info(0, {}, "breaker_0")  # ruff: ignore[private-member-access]

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert info["model"] == "Jackery Sicherung"
    assert "SolarVault" not in info["name"]
    assert "SolarVault" not in str(info["model"])


def test_subdevice_alarm_sensor_falls_back_to_jackery() -> None:
    """A HomePower accessory sensor with blank fields is never "SolarVault"."""
    entity = _bound(JackerySubdeviceAlarmSensor, {}, _sub_device_sn="SN1")
    info = entity._build_sub_device_device_info(0, {}, "sub_0")  # ruff: ignore[private-member-access]

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert info["model"] == "Jackery Zubehör"
    assert "SolarVault" not in info["name"]
    assert "SolarVault" not in str(info["model"])


def test_meter_head_sensor_base_name_falls_back_to_jackery() -> None:
    """A HomePower meter head with blank name fields is never "SolarVault"."""
    entity = _bound(
        JackeryMeterHeadSensor,
        {},
        _meter_head_index=1,
        _meter_head_sn=None,
        _meter_head_key=None,
    )
    info = entity.device_info

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert "SolarVault" not in info["name"]


def test_smart_meter_sensor_base_name_falls_back_to_jackery() -> None:
    """A HomePower CT/smart-meter with blank name fields is never "SolarVault"."""
    entity = _bound(JackerySmartMeterSensor, {})
    info = entity.device_info

    assert info["name"].startswith(f"Jackery {_DEVICE_ID}")
    assert "SolarVault" not in info["name"]
