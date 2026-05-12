"""Standalone Jackery SolarVault protocol client library."""

from typing import Any

from .api import JackeryApi, JackeryApiError, JackeryAuthError, JackeryError

__all__ = [
    "JackeryApi",
    "JackeryApiError",
    "JackeryAuthError",
    "JackeryError",
    "JackeryMqttPushClient",
]


def __getattr__(name: str) -> Any:
    if name == "JackeryMqttPushClient":
        from .mqtt_push import JackeryMqttPushClient

        return JackeryMqttPushClient
    raise AttributeError(name)
