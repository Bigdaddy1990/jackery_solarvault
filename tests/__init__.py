r"""HA fixture-based tests for Jackery SolarVault.

These tests exercise the integration against a real Home Assistant
test environment via ``pytest-homeassistant-custom-component``. They
complement the source-only unit tests in ``tests/test_*.py`` by
verifying actual runtime behaviour: config flow steps, entry setup
and unload, service registration, and reauth.

Run through the repository's configured Home Assistant workflow::

    scripts / hass.bat

The equivalent WSL gate is orchestrated by ``scripts/gate_wsl.sh``. Both
paths preserve the repository-wide pytest and pre-commit configuration;
this suite is not intended to be run through an ad-hoc partial command.

Source-only unit tests that do not request Home Assistant fixtures remain
runnable in the lightweight CI matrix.
"""
