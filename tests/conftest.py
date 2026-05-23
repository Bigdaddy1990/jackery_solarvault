"""Pytest fallback helpers for source-only test runs.

Some CI checks deliberately run with ``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1``.
Those runs still read ``pyproject.toml``, so the repository must register the
``asyncio_mode`` option and handle simple async unit tests without relying on
pytest-asyncio autoloading. When pytest-asyncio is explicitly loaded, it remains
responsible for async tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import inspect
from typing import Any

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register pytest-asyncio's ini key for plugin-free source test runs."""
    parser.addini(
        "asyncio_mode",
        "asyncio mode for plugin-free source-only tests",
        default="strict",
    )


def _pytest_asyncio_loaded(config: pytest.Config) -> bool:
    """Return True when pytest-asyncio is active."""
    pluginmanager = config.pluginmanager
    return any(
        pluginmanager.hasplugin(name)
        for name in ("asyncio", "pytest_asyncio", "pytest_asyncio.plugin")
    )


def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Run plain async unit tests when pytest-asyncio is not loaded."""
    if _pytest_asyncio_loaded(pyfuncitem.config):
        return None
    test_func = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_func):
        return None
    fixture_names = pyfuncitem._fixtureinfo.argnames
    test_args: Mapping[str, Any] = {
        name: pyfuncitem.funcargs[name] for name in fixture_names
    }
    asyncio.run(test_func(**test_args))
    return True
