"""Regression coverage for Python 3.14 coordinator annotation introspection."""

from annotationlib import Format, get_annotations
from datetime import timedelta
from importlib import import_module
import inspect
import subprocess
import sys
from types import FunctionType
from typing import get_type_hints
from unittest.mock import Mock, create_autospec

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


def test_constructor_signature_resolves_concrete_runtime_types() -> None:
    """Default signature inspection resolves every constructor parameter."""
    signature = inspect.signature(JackerySolarVaultCoordinator)
    expected = {
        "hass": HomeAssistant,
        "entry": ConfigEntry,
        "api": JackeryApi,
        "update_interval": timedelta,
    }
    assert {
        name: parameter.annotation for name, parameter in signature.parameters.items()
    } == expected
    assert get_type_hints(JackerySolarVaultCoordinator.__init__) == {
        **expected,
        "return": type(None),
    }


def test_declared_coordinator_annotations_resolve_at_runtime() -> None:
    """All owned methods and class fields support value and hint inspection."""
    class_annotations = get_annotations(
        JackerySolarVaultCoordinator, format=Format.VALUE
    )
    class_hints = get_type_hints(JackerySolarVaultCoordinator)
    assert class_annotations
    assert all(name in class_hints for name in class_annotations)

    inspected = set()
    for name, member in vars(JackerySolarVaultCoordinator).items():
        if isinstance(member, staticmethod | classmethod):
            functions = (member.__func__,)
        elif isinstance(member, property):
            functions = tuple(
                function
                for function in (member.fget, member.fset, member.fdel)
                if function is not None
            )
        elif isinstance(member, FunctionType):
            functions = (member,)
        else:
            continue
        for function in functions:
            inspect.signature(function)
            annotations = get_annotations(function, format=Format.VALUE)
            hints = get_type_hints(function)
            assert annotations.keys() == hints.keys(), name
        inspected.add(name)
    assert "__init__" in inspected
    assert "_async_ingest_ble_observation" in inspected
    assert "_schedule_background_once" in inspected
    assert "_local_timezone" in inspected


def test_coordinator_supports_mock_spec() -> None:
    """Mock spec creation can inspect the real coordinator constructor."""
    coordinator = Mock(spec=JackerySolarVaultCoordinator)
    assert isinstance(coordinator, JackerySolarVaultCoordinator)
    assert coordinator.async_refresh is not None


def test_coordinator_autospec_preserves_constructor_arity() -> None:
    """Autospec keeps real parameter validation without annotation failures."""
    constructor = create_autospec(JackerySolarVaultCoordinator)
    arguments = {
        "hass": object(),
        "entry": object(),
        "api": object(),
        "update_interval": timedelta(seconds=30),
    }
    constructor(**arguments)
    constructor.assert_called_once_with(**arguments)
    with pytest.raises(TypeError, match="missing a required argument"):
        constructor()
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        constructor(**arguments, unsupported=True)


def test_runtime_introspection_keeps_optional_ble_transport_lazy() -> None:
    """A fresh interpreter can inspect the coordinator without loading BLE I/O."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import inspect
import sys
from unittest.mock import Mock

from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

transport = 'custom_components.jackery_solarvault.client.ble_transport'
assert transport not in sys.modules
inspect.signature(JackerySolarVaultCoordinator)
Mock(spec=JackerySolarVaultCoordinator)
assert transport not in sys.modules
""",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ble_observation_reexport_preserves_identity() -> None:
    """Existing transport imports retain the canonical observation class."""
    ble = import_module("custom_components.jackery_solarvault.client.ble")
    transport = import_module(
        "custom_components.jackery_solarvault.client.ble_transport"
    )
    assert transport.BleFrameObservation is ble.BleFrameObservation
