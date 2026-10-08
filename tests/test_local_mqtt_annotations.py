"""Runtime annotation regressions for the coordinator's local MQTT client."""

from annotationlib import Format, get_annotations
import inspect
from types import FunctionType
from typing import Unpack, get_origin, get_type_hints
from unittest.mock import Mock, create_autospec

import pytest

from custom_components.jackery_solarvault.client import local_mqtt
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
    LocalMqttConnectionSettings,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant


def test_local_mqtt_constructor_resolves_concrete_runtime_types() -> None:
    """Inspection resolves HA classes and keeps the typed keyword surface."""
    signature = inspect.signature(JackeryLocalMqttClient)
    assert signature.parameters["hass"].annotation is HomeAssistant
    assert signature.parameters["config_entry"].annotation == ConfigEntry | None
    assert signature.parameters["settings"].annotation == (
        LocalMqttConnectionSettings | None
    )
    options = signature.parameters["connection_options"]
    assert options.kind is inspect.Parameter.VAR_KEYWORD
    assert get_origin(options.annotation) is Unpack
    hints = get_type_hints(JackeryLocalMqttClient.__init__)
    assert hints["hass"] is HomeAssistant
    assert hints["config_entry"] == ConfigEntry | None


def test_local_mqtt_declared_annotations_resolve_at_runtime() -> None:
    """Factory functions, owned methods, and properties support value hints."""
    functions = [
        function
        for function in vars(local_mqtt).values()
        if isinstance(function, FunctionType)
        and function.__module__ == local_mqtt.__name__
    ]
    for member in vars(JackeryLocalMqttClient).values():
        if isinstance(member, staticmethod | classmethod):
            functions.append(member.__func__)
        elif isinstance(member, property):
            functions.extend(
                function
                for function in (member.fget, member.fset, member.fdel)
                if function is not None
            )
        elif isinstance(member, FunctionType):
            functions.append(member)
    inspected = set()
    for function in functions:
        inspect.signature(function)
        annotations = get_annotations(function, format=Format.VALUE)
        hints = get_type_hints(function)
        assert annotations.keys() == hints.keys(), function.__name__
        inspected.add(function.__name__)
    assert "__init__" in inspected
    assert "_create_background_task" in inspected
    assert "is_connected" in inspected


def test_local_mqtt_mock_and_autospec_preserve_constructor_arity() -> None:
    """Spec mocks inspect runtime annotations while autospec validates calls."""
    client = Mock(spec=JackeryLocalMqttClient)
    assert isinstance(client, JackeryLocalMqttClient)
    constructor = create_autospec(JackeryLocalMqttClient)
    hass = object()
    settings = LocalMqttConnectionSettings(host="mqtt.example")
    constructor(hass, settings)
    constructor.assert_called_once_with(hass, settings)
    with pytest.raises(TypeError, match="missing a required argument"):
        constructor()
    with pytest.raises(TypeError, match="too many positional arguments"):
        constructor(hass, settings, object())
