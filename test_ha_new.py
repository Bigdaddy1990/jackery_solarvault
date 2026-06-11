"""Smoke-check HomeAssistant construction with Windows compatibility patches."""

import sys
from typing import Self
import unittest.mock

sys.modules["fcntl"] = unittest.mock.MagicMock()
sys.modules["resource"] = unittest.mock.MagicMock()

import contextlib  # noqa: E402

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
    """Provide default config_dir for HomeAssistant construction."""
    if not args and "config_dir" not in kwargs:
        kwargs["config_dir"] = "test-config"
    original_init(self, *args, **kwargs)


ha.HomeAssistant.__init__ = patched_init

with contextlib.suppress(Exception):
    h = ha.HomeAssistant()
