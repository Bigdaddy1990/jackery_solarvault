"""Shared fixtures for the Jackery SolarVault HA-fixture test suite.

The fixtures here are intentionally small. Heavy lifting is handled
by ``pytest-homeassistant-custom-component``; this file only
configures pytest-asyncio and provides a couple of helpers shared
across config-flow and entry-setup tests.
"""

from collections.abc import Generator
from unittest.mock import patch

import pytest


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


@pytest.fixture
def mock_jackery_login() -> Generator[None]:
    """Stub Jackery auth and discovery calls across the test.

    ``async_login`` normally stores a token that later discovery calls need.
    The fake keeps that side effect so tests can exercise setup without real
    cloud I/O.
    """

    async def _fake_login(api) -> str:
        api._token = "test-token"
        api._mqtt_user_id = "test-user"
        api._mqtt_seed_b64 = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="
        api._mqtt_mac_id = api._resolve_login_mac_id()
        return api._token

    with (
        patch(
            "custom_components.jackery_solarvault.api.JackeryApi.async_login",
            new=_fake_login,
        ),
        patch(
            "custom_components.jackery_solarvault.api.JackeryApi.async_get_system_list",
            return_value=[],
        ),
        patch(
            "custom_components.jackery_solarvault.api.JackeryApi.async_list_devices_legacy",
            return_value=[],
        ),
    ):
        yield
