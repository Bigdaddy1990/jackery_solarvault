"""Jackery SolarVault protocol client (HTTP API + MQTT push).

This sub-package holds the cloud-protocol implementation. The pure helpers and
constants live one level up in ``..util`` and ``..const`` so the integration
maintains a single source of truth — there is no separate, standalone copy.
"""

from typing import Any
from typing import TYPE_CHECKING

from .api import JackeryApi
from .api import JackeryApiError
from .api import JackeryAuthError
from .api import JackeryError

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
    """Lazily import and return the `JackeryMqttPushClient` class when accessed as a module attribute.

    Parameters:
        name (str): Name of the requested module attribute.

    Returns:
        type: The `JackeryMqttPushClient` class.

    Raises:
        AttributeError: If `name` is not `"JackeryMqttPushClient"`.
    """
    if name == "JackeryMqttPushClient":
        from .mqtt_push import JackeryMqttPushClient as _JackeryMqttPushClient

        return _JackeryMqttPushClient
    raise AttributeError(name)
