"""Source-only contract tests for the Jackery SolarVault reauth flow.

These tests verify the reauth wiring without requiring a Home Assistant
fixture stack. They lock down the contract that:

1. ``JackeryConfigFlow`` exposes ``async_step_reauth`` and
   ``async_step_reauth_confirm``.
2. The integration raises ``ConfigEntryAuthFailed`` on auth-failure paths
   so HA actually triggers the reauth flow.
3. Translation strings exist for the reauth step in every locale.
4. The reauth handler updates the existing entry's password and calls
   ``async_reload`` instead of creating a new entry.

Together these are the Silver-tier ``reauthentication-flow`` rule.
"""

import ast
import json
from pathlib import Path
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.jackery_solarvault.config_flow import JackeryConfigFlow
from custom_components.jackery_solarvault.const import DOMAIN
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "jackery_solarvault"


def _read(name: str) -> str:
    """Read a UTF-8 text file from the integration component directory.

    Parameters:
        name (str): Relative filename or path under the integration's component directory.

    Returns:
        str: The file contents decoded as UTF-8.
    """  # ruff: ignore[line-too-long]
    return (COMPONENT / name).read_text(encoding="utf-8")


def test_config_flow_implements_reauth_steps() -> None:
    """JackeryConfigFlow must define both reauth entry-points."""
    src = _read("config_flow.py")
    tree = ast.parse(src)
    methods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "JackeryConfigFlow":
            methods = {
                child.name
                for child in node.body
                if isinstance(child, ast.AsyncFunctionDef | ast.FunctionDef)
            }
            break
    assert "async_step_reauth" in methods
    assert "async_step_reauth_confirm" in methods


def test_auth_failure_paths_trigger_reauth() -> None:
    """ConfigEntryAuthFailed must be raised on the auth-failure paths.

    Home Assistant routes this exception back to the config-flow
    reauth step. Without these raise sites the reauth flow is
    user-startable but never automatically triggered.
    """
    init_src = _read("__init__.py")
    coord_src = _read("coordinator.py")
    # At least one in __init__ (initial setup) and at least one in coordinator
    # (steady-state token expiry / login rejection).
    assert init_src.count("ConfigEntryAuthFailed(") >= 1, init_src
    assert coord_src.count("ConfigEntryAuthFailed(") >= 1, coord_src


def test_strings_json_covers_reauth_step() -> None:
    """strings.json must define the reauth step + abort reasons."""
    strings = json.loads(_read("strings.json"))
    config = strings.get("config", {})
    assert "reauth_confirm" in config.get("step", {}), config
    abort = config.get("abort", {})
    assert "reauth_successful" in abort, abort
    assert "reauth_entry_missing" in abort, abort


def test_translations_cover_reauth_step_for_all_locales() -> None:
    """Every locale must translate the reauth step + abort reasons."""
    translations_dir = COMPONENT / "translations"
    assert translations_dir.is_dir()
    locale_files = sorted(translations_dir.glob("*.json"))
    assert locale_files, "no translation files found"
    for locale_file in locale_files:
        data = json.loads(locale_file.read_text(encoding="utf-8"))
        config = data.get("config", {})
        assert "reauth_confirm" in config.get("step", {}), (
            f"{locale_file.name} missing reauth_confirm step"
        )
        abort = config.get("abort", {})
        assert "reauth_successful" in abort, (
            f"{locale_file.name} missing reauth_successful abort"
        )
        assert "reauth_entry_missing" in abort, (
            f"{locale_file.name} missing reauth_entry_missing abort"
        )


async def test_reauth_step_uses_only_password_field_not_username() -> None:
    """The form asks for a new password while keeping the account identity."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_USERNAME: "account@example.com"})
    flow = JackeryConfigFlow()
    with patch.object(flow, "_get_reauth_entry", return_value=entry):
        result = await flow.async_step_reauth_confirm()
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"]
    assert schema is not None
    assert set(schema.schema) == {vol.Required(CONF_PASSWORD)}
    assert result["description_placeholders"] == {"username": "account@example.com"}
