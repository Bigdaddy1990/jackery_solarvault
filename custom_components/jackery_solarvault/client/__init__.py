"""Standalone Jackery SolarVault protocol client library."""

from .api import JackeryApi, JackeryApiError, JackeryAuthError, JackeryError

__all__ = [
    "JackeryApi",
    "JackeryApiError",
    "JackeryAuthError",
    "JackeryError",
    "JackeryMqttPushClient",  # noqa: F822
]


def __getattr__(name: str):
    if name == "JackeryMqttPushClient":
        from .mqtt_push import JackeryMqttPushClient

        return JackeryMqttPushClient
    raise AttributeError(name)
