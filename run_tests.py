"""Run pytest with Windows Home Assistant compatibility patches."""

import sys
from typing import Self
import unittest.mock

# Mock fcntl and resource on Windows
sys.modules["fcntl"] = unittest.mock.MagicMock()
sys.modules["resource"] = unittest.mock.MagicMock()

# Mock pytest_socket to prevent it from blocking sockets
try:
    import pytest_socket

    pytest_socket.disable_socket = lambda *args, **kwargs: None
    pytest_socket.enable_socket = lambda *args, **kwargs: None
except ImportError:
    pass

# Patch HomeAssistant instantiation for newer HA compatibility
import homeassistant.core as ha  # noqa: E402 - fcntl/resource patch must run first.

original_new = ha.HomeAssistant.__new__


def patched_new(cls: type[Self], *args: object, **kwargs: object) -> Self:
    """Provide default config_dir for HomeAssistant construction."""
    if not args and "config_dir" not in kwargs:
        kwargs["config_dir"] = "test-config"
    return original_new(cls, *args, **kwargs)


ha.HomeAssistant.__new__ = patched_new

original_init = ha.HomeAssistant.__init__


def patched_init(self: ha.HomeAssistant, *args: object, **kwargs: object) -> None:
    """Provide default config_dir and pre-initialize custom component data."""
    if not args and "config_dir" not in kwargs:
        kwargs["config_dir"] = "test-config"
    original_init(self, *args, **kwargs)
    from homeassistant import loader

    # Pre-initialize loader data keys needed by config flow tests.
    # guard with setdefault so async_test_home_assistant can override cleanly.
    self.data[loader.DATA_CUSTOM_COMPONENTS] = {}


ha.HomeAssistant.__init__ = patched_init


import pytest  # noqa: E402 - pytest starts after compatibility patches.

# Run with warning filter to ignore PytestAssertRewriteWarning for pytest_socket.
sys.exit(
    pytest.main([
        "-W",
        "ignore::pytest.PytestAssertRewriteWarning",
        *sys.argv[1:],
    ])
)
