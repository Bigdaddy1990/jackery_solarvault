"""Jackery SolarVault protocol client (HTTP API + MQTT push).

This sub-package holds the cloud-protocol implementation. The pure helpers and
constants live one level up in ``..util`` and ``..const`` so the integration
maintains a single source of truth — there is no separate, standalone copy.
"""

from __future__ import annotations

from importlib import import_module
import logging
from typing import TYPE_CHECKING, Final, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .api import (
    DevicePeriodQuery,
    JackeryApi,
    JackeryApiError,
    JackeryAuthError,
    JackeryError,
)
from .mqtt_push import JackeryMqttPushClient

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "DevicePeriodQuery",
    "JackeryApi",
    "JackeryApiError",
    "JackeryAuthError",
    "JackeryError",
    "JackeryMqttPushClient",
]


def __getattr__(name: str) -> type[JackeryMqttPushClient]:  # PEP 562 lazy re-export
    """Lazily resolve ``JackeryMqttPushClient`` on module attribute access.

    Parameters:
        name (str): The attribute name being requested from the module.

    Returns:
        The `JackeryMqttPushClient` class for its supported lazy export.

    Raises:
        AttributeError: If `name` is not a supported attribute.
    """
    if name == "JackeryMqttPushClient":
        module = import_module(f"{__name__}.mqtt_push")
        return cast("type[JackeryMqttPushClient]", module.JackeryMqttPushClient)
    raise AttributeError(name)


async def async_unload_entry(hass: HomeAssistant, entry: CustomConfigEntry) -> bool:
    """Entlädt einen ConfigEntry sauber."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
