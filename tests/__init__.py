"""HA fixture-based tests for Jackery SolarVault.

These tests exercise the integration against a real Home Assistant
test environment via ``pytest-homeassistant-custom-component``. They
complement the source-only unit tests in ``tests/test_*.py`` by
verifying actual runtime behaviour: config flow steps, entry setup
and unload, service registration, and reauth.

Run with::

    pytest -c pytest-ha.ini tests/ha

The pure unit tests under ``tests/`` do NOT require this stack and
remain runnable in the lightweight CI matrix.
"""
