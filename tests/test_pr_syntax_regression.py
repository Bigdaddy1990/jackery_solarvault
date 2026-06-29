"""Syntax regression tests for modules changed in this PR.

These tests always run (no skipif) and catch import-time errors
in the changed modules. They are designed to fail fast and clearly
when a SyntaxError or ImportError is introduced into a module.
"""

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.client.ble import build_binary_frame
from custom_components.jackery_solarvault.client.mqtt.local_mqtt import (
    JackeryLocalMqttClient,
)


def test_client_local_mqtt_can_be_imported() -> None:
    """client/local_mqtt.py must be importable without SyntaxError.

    The PR adds ``except json.JSONDecodeError, ValueError:`` which is Python 2
    syntax and must be written as ``except (json.JSONDecodeError, ValueError):``
    in Python 3. A clean direct import fails loudly if that regression returns.
    """
    assert JackeryLocalMqttClient is not None


def test_client_api_can_be_imported() -> None:
    """client/api.py must be importable without error."""
    assert JackeryApi is not None


def test_client_ble_can_be_imported() -> None:
    """client/ble.py must be importable without error."""
    assert build_binary_frame is not None
