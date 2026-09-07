"""Jackery SolarVault protocol client (HTTP API + MQTT push).

This sub-package holds the cloud-protocol implementation. The pure helpers and
constants live one level up in ``..util`` and ``..const`` so the integration
maintains a single source of truth — there is no separate, standalone copy.
"""

from importlib import import_module
from typing import TYPE_CHECKING, cast

from .api import (
    DevicePeriodQuery,
    JackeryApi,
    JackeryApiError,
    JackeryAuthError,
    JackeryError,
)

if TYPE_CHECKING:
    from .mqtt_push import JackeryMqttPushClient

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
