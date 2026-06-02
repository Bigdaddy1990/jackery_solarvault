"""Syntax regression tests for modules changed in this PR.

These tests always run (no skipif) and catch import-time errors
in the changed modules. They are designed to fail fast and clearly
when a SyntaxError or ImportError is introduced into a module.
"""

import importlib
import sys


def test_client_local_mqtt_can_be_imported() -> None:
    """client/local_mqtt.py must be importable without SyntaxError.

    The PR adds ``except json.JSONDecodeError, ValueError:`` which is Python 2
    syntax and must be written as ``except (json.JSONDecodeError, ValueError):``
    in Python 3. This test catches that class of regression.
    """
    # Reload to bypass any prior cached import.
    mod_name = "custom_components.jackery_solarvault.client.local_mqtt"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    try:
        mod = importlib.import_module(mod_name)
    except SyntaxError as err:
        raise AssertionError(
            f"client/local_mqtt.py has a SyntaxError — fix the except clause: {err}",
        ) from err
    except ImportError as err:
        raise AssertionError(
            f"client/local_mqtt.py raised ImportError — check dependencies: {err}",
        ) from err

    assert hasattr(mod, "JackeryLocalMqttClient"), (
        "JackeryLocalMqttClient must be exported from client/local_mqtt.py"
    )


def test_client_api_can_be_imported() -> None:
    """client/api.py must be importable without error."""
    mod_name = "custom_components.jackery_solarvault.client.api"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    mod = importlib.import_module(mod_name)
    assert hasattr(mod, "JackeryApi")


def test_client_ble_can_be_imported() -> None:
    """client/ble.py must be importable without error."""
    mod_name = "custom_components.jackery_solarvault.client.ble"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    mod = importlib.import_module(mod_name)
    assert hasattr(mod, "build_binary_frame")
