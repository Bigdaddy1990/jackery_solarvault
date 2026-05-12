"""Standalone Jackery SolarVault protocol client library."""

from typing import TYPE_CHECKING, Any

from .api import JackeryApi, JackeryApiError, JackeryAuthError, JackeryError

if TYPE_CHECKING:
    from .mqtt_push import JackeryMqttPushClient
else:
    JackeryMqttPushClient: Any

__all__ = [
    "JackeryApi",
    "JackeryApiError",
    "JackeryAuthError",
    "JackeryError",
    "JackeryMqttPushClient",
]


def __getattr__(name: str) -> Any:
    if name == "JackeryMqttPushClient":
        from .mqtt_push import JackeryMqttPushClient as _JackeryMqttPushClient

        return _JackeryMqttPushClient
    raise AttributeError(name)
