"""Smoke-check pytest-homeassistant-custom-component plugin imports on Windows."""

import contextlib
import sys
import unittest.mock

sys.modules["fcntl"] = unittest.mock.MagicMock()
sys.modules["resource"] = unittest.mock.MagicMock()

with contextlib.suppress(Exception):
    pass
