"""Shared fixtures for the Jackery SolarVault HA-fixture test suite.

The fixtures here are intentionally small. Heavy lifting is handled
by ``pytest-homeassistant-custom-component``; this file only
configures pytest-asyncio and provides a couple of helpers shared
across config-flow and entry-setup tests.
"""

import asyncio
from collections.abc import Generator
import sys
from unittest.mock import MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from syrupy.assertion import SnapshotAssertion

from homeassistant.core import HomeAssistant


# Test initialization must ensure custom_components are enabled
# but we can't autouse a simple fixture for that since the recorder
# need to be initialized first
@pytest.fixture(autouse=True)
def auto_enable(request: pytest.FixtureRequest) -> Generator[None]:
    """Enable custom components after recorder fixture initialization."""
    if "recorder_mock" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
    hass = request.getfixturevalue("hass")
    hass.data.pop("custom_components")
    yield


# Mock fcntl for Windows compatibility
if sys.platform == "win32":
    sys.modules["fcntl"] = MagicMock()

# Create alias custom_components.jackery_solarvault.api -> custom_components.jackery_solarvault.client.api
import custom_components.jackery_solarvault.client.api as client_api  # noqa: E402

sys.modules["custom_components.jackery_solarvault.api"] = client_api


# HA 2026.6+ compatibility: ConfigEntries._entries changed from list to ConfigEntryItems.
# MockConfigEntry.add_to_hass (from pytest_homeassistant_custom_component) writes
# directly to _entries; patch it to ensure the type is correct first.
try:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from homeassistant.config_entries import ConfigEntryItems as _ConfigEntryItems

    _orig_add = MockConfigEntry.add_to_hass

    def _patched_add_to_hass(self: object, hass: HomeAssistant) -> None:
        ce = hass.config_entries
        if not isinstance(ce._entries, _ConfigEntryItems):
            items = _ConfigEntryItems(hass)
            src = ce._entries
            if isinstance(src, dict):
                for k, v in src.items():
                    items.data[k] = v
            elif isinstance(src, list):
                for v in src:
                    if hasattr(v, "entry_id"):
                        items.data[v.entry_id] = v
            ce._entries = items
        _orig_add(self, hass)

    MockConfigEntry.add_to_hass = _patched_add_to_hass
except Exception:
    pass
# Patch get_entries_for_domain on list objects as a fallback for HA 2026.6+ compat
try:
    import homeassistant.config_entries as _ce
    from homeassistant.config_entries import (
        ConfigEntryItems as _ConfigEntryItemsFallback,
    )

    _orig_has_entries = _ce.ConfigEntries.async_has_entries

    def _patched_async_has_entries(
        self: object,
        domain: str,
        include_ignore: bool = True,
        include_disabled: bool = True,
    ) -> bool:
        # If _entries is a list or dict (old format), convert to ConfigEntryItems safely
        if not isinstance(self._entries, _ConfigEntryItemsFallback):
            try:
                items = _ConfigEntryItemsFallback(self.hass)
                src = self._entries
                if isinstance(src, dict):
                    for k, v in src.items():
                        items.data[k] = v
                elif isinstance(src, list):
                    for v in src:
                        if hasattr(v, "entry_id"):
                            items.data[v.entry_id] = v
                self._entries = items
            except Exception:
                # hass not yet initialized; return safe default
                return False
        return _orig_has_entries(
            self,
            domain,
            include_ignore=include_ignore,
            include_disabled=include_disabled,
        )

    _ce.ConfigEntries.async_has_entries = _patched_async_has_entries
except Exception:
    pass


@pytest.fixture
def event_loop() -> Generator[asyncio.AbstractEventLoop]:
    """Create an instance of the default event loop."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def loop(event_loop: asyncio.AbstractEventLoop) -> asyncio.AbstractEventLoop:
    """Alias event_loop to loop."""
    return event_loop


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Return snapshot assertion fixture with the Home Assistant extension."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: object,
) -> Generator[None]:
    """Auto-enable the custom_components dir for every HA fixture test.

    Without this, ``await async_setup_component`` cannot find the
    integration. The fixture itself comes from
    ``pytest-homeassistant-custom-component``; we just opt in for the
    whole HA suite by making it autouse.
    """
    yield


@pytest.fixture
def mock_jackery_login() -> Generator[None]:
    """Stub Jackery auth and discovery calls across the test.

    ``async_login`` normally stores a token that later discovery calls need.
    The fake keeps that side effect so tests can exercise setup without real
    cloud I/O.
    """

    async def _fake_login(api: object) -> str:  # noqa: RUF029 - patches async_login.
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
