"""Shared HA test fixtures for the Jackery SolarVault integration.

These fixtures follow the pytest-homeassistant-custom-component guidance
in docs/pytest-homeassistant-custom-component.md:

* ``enable_custom_integrations`` is a passthrough autouse fixture so the
  integration is loadable inside the HA test harness.
* ``snapshot`` is wired to :class:`HomeAssistantSnapshotExtension` so
  syrupy snapshots understand HA-specific objects (states, entity
  registry entries, device entries).
"""

from typing import TYPE_CHECKING

import pytest
from pytest_homeassistant_custom_component.syrupy import (
    HomeAssistantSnapshotExtension,
)

if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Load ``custom_components`` in every test.

    ``enable_custom_integrations`` is provided by
    pytest-homeassistant-custom-component. Depending on it here (autouse)
    makes the Jackery integration loadable via
    ``hass.config_entries.async_setup`` in all tests without each test
    re-declaring the dependency. No teardown is needed, so the fixture
    returns ``None`` rather than yielding.
    """


@pytest.fixture()
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Return a syrupy snapshot bound to the HA extension.

    The HA extension teaches syrupy how to serialise Home Assistant
    objects (``State``, registry entries, ...) so snapshot tests read
    cleanly instead of dumping opaque ``repr`` output.
    """
    return snapshot.use_extension(HomeAssistantSnapshotExtension)
