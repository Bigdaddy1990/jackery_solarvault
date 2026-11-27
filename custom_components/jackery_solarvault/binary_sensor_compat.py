"""Resolve Home Assistant's binary-sensor device class across supported versions."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
else:
    try:
        from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
    except ModuleNotFoundError as err:
        if err.name != "homeassistant.components.binary_sensor.const":
            raise
        from homeassistant.components.binary_sensor import BinarySensorDeviceClass

__all__ = ["BinarySensorDeviceClass"]
