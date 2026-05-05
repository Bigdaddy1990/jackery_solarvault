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
    enable_custom_integrations: None,  # noqa: ARG001 - fixture from pytest-haccc
) -> None:
    """Auto-enable the custom_components dir for every HA fixture test.

    Without this, ``await async_setup_component`` cannot find the
    integration. The fixture itself comes from
    ``pytest-homeassistant-custom-component``; we just opt in for the
    whole HA suite by making it autouse.
    """


@pytest.fixture
def mock_jackery_login() -> Generator[None]:
    """Stub the JackeryApi.async_login call across the test.

    Avoids hitting the real cloud during config-flow tests and keeps
    them deterministic. Tests that need to drive specific API
    responses can override this fixture or patch other API methods
    on top of it.
    """
    with patch(
        "custom_components.jackery_solarvault.api.JackeryApi.async_login",
        return_value=None,
    ):
        yield
