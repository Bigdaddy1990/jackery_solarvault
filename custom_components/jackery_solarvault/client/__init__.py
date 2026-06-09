"""Jackery SolarVault protocol client (HTTP API + MQTT push).

This sub-package holds the cloud-protocol implementation. The pure helpers and
constants live one level up in ``..util`` and ``..const`` so the integration
maintains a single source of truth — there is no separate, standalone copy.
"""

from importlib import import_module
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


def __getattr__(name: str) -> Any:  # noqa: ANN401  # PEP 562 lazy re-export
    """Lazily resolves and returns the JackeryMqttPushClient symbol when accessed as a module attribute.

    Parameters:
        name (str): The attribute name being requested from the module.

    Returns:
        Any: The `JackeryMqttPushClient` class when `name` is `"JackeryMqttPushClient"`.

    Raises:
        AttributeError: If `name` is not a supported attribute.
    """  # noqa: E501
    if name == "JackeryMqttPushClient":
        module = import_module(f"{__name__}.mqtt_push")
        return module.JackeryMqttPushClient
    raise AttributeError(name)
