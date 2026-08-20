"""Pytest fallback helpers for source-only test runs.

Some CI checks deliberately run with ``PYTEST_DISABLE_PLUGIN_AUTOLOAD=1``.
Those runs still read ``pyproject.toml``, so the repository must register the
``asyncio_mode`` option and handle simple async unit tests without relying on
pytest-asyncio autoloading. When pytest-asyncio is explicitly loaded, it remains
responsible for async tests.

Shared fixtures for the Jackery SolarVault HA-fixture test suite.

The fixtures here are intentionally small. Heavy lifting is handled
by ``pytest-homeassistant-custom-component``; this file only
configures pytest-asyncio and provides a couple of helpers shared
across config-flow and entry-setup tests.
"""

import asyncio
import inspect
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the `asyncio_mode` ini option used for running async tests when pytest plugins are not autoloaded.

    Registers the ini option named `asyncio_mode` with a default value of `"strict"`, so source-only test runs (when `pytest-asyncio` is not auto-loaded) still expose the configuration key.
    """
    parser.addini(
        "asyncio_mode",
        "asyncio mode for plugin-free source-only tests",
        default="strict",
    )


def _pytest_asyncio_loaded(config: pytest.Config) -> bool:
    """Detect whether pytest-asyncio is active in the current pytest run.

    Parameters:
        config (pytest.Config): Pytest `Config` instance whose plugin manager will be inspected.

    Returns:
        bool: `True` if the pytest-asyncio plugin is present, `False` otherwise.
    """
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
    fixture_names = pyfuncitem._fixtureinfo.argnames  # ruff: ignore[private-member-access]
    test_args: Mapping[str, Any] = {
        name: pyfuncitem.funcargs[name] for name in fixture_names
    }
    asyncio.run(test_func(**test_args))
    return True


try:
    from pytest_homeassistant_custom_component.common import (  # type: ignore[import-not-found]
        enable_custom_integrations as _enable_custom_integrations,
    )

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(
        enable_custom_integrations: None,
    ) -> None:
        """Auto-enable the custom_components dir for every HA fixture test.

        Without this, ``await async_setup_component`` cannot find the
        integration. The fixture itself comes from
        ``pytest-homeassistant-custom-component``; we just opt in for the
        whole HA suite by making it autouse.
        """
except ImportError:
    # pytest-homeassistant-custom-component not available (e.g., on Windows without fcntl)
    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations() -> None:
        """No-op when HA test plugin is not available."""
        return


@pytest.fixture
def mock_jackery_login() -> Generator[None]:
    """Stub Jackery auth and discovery calls across the test.

    ``async_login`` normally stores a token that later discovery calls need.
    The fake keeps that side effect so tests can exercise setup without real
    cloud I/O.
    """

    async def _fake_login(api: Any) -> str:  # ruff: ignore[unused-async]
        """Set test authentication and MQTT attributes on a Jackery API instance and return the assigned token.

        Parameters:
            api: The Jackery API client instance whose internal authentication and MQTT-related attributes will be populated for testing.

        Returns:
            str: The authentication token assigned to the API instance.
        """
        api._token = "test-token"  # ruff: ignore[private-member-access]
        api._mqtt_user_id = "test-user"  # ruff: ignore[private-member-access]
        api._mqtt_seed_b64 = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # ruff: ignore[private-member-access]
        api._mqtt_mac_id = api._resolve_login_mac_id()  # ruff: ignore[private-member-access]
        return "test-token"

    with (
        patch(
            "custom_components.jackery_solarvault.client.api.JackeryApi.async_login",
            new=_fake_login,
        ),
        patch(
            "custom_components.jackery_solarvault.client.api.JackeryApi.async_get_system_list",
            return_value=[],
        ),
        patch(
            "custom_components.jackery_solarvault.client.api.JackeryApi.async_list_devices_legacy",
            return_value=[],
        ),
    ):
        yield
