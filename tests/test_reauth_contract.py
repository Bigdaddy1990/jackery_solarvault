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
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "jackery_solarvault"


def _read(name: str) -> str:
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


def test_reauth_handler_updates_existing_entry_and_reloads() -> None:
    """async_step_reauth_confirm must update the entry, not create a new one."""
    src = _read("config_flow.py")
    # Locate the reauth_confirm method body
    match = re.search(
        r"async def async_step_reauth_confirm.*?(?=\n    async def |\n    @|\nclass )",
        src,
        re.S,
    )
    assert match is not None, "async_step_reauth_confirm not found"
    body = match.group(0)
    # It must use update_entry + reload, not create_entry
    assert "async_update_entry" in body, body
    assert "async_reload" in body, body
    assert "async_create_entry" not in body, body
    # Password rotation: new password is written into entry.data
    assert "CONF_PASSWORD" in body
    # Successful reauth aborts the flow with reauth_successful
    assert "reauth_successful" in body or "FLOW_ABORT_REAUTH_SUCCESSFUL" in body


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


def test_reauth_step_uses_only_password_field_not_username() -> None:
    """Reauth must ask only for the new password.

    The username is the unique-id key — changing it would create a
    different entry. The reauth confirm form must therefore present a
    password-only schema and surface the existing username as a
    description placeholder.
    """
    src = _read("config_flow.py")
    match = re.search(
        r"async def async_step_reauth_confirm.*?(?=\n    async def |\n    @|\nclass )",
        src,
        re.S,
    )
    assert match is not None
    body = match.group(0)
    assert "CONF_PASSWORD" in body, body
    # Username appears only as placeholder, not as a Required form field
    schema_block = re.search(r"data_schema=vol\.Schema\(\{(.*?)\}\)", body, re.S)
    assert schema_block is not None, body
    schema_body = schema_block.group(1)
    assert "CONF_USERNAME" not in schema_body, schema_body
    # Username is rendered as a placeholder so the user knows which
    # account they're re-authenticating.
    assert "description_placeholders=" in body, body
    assert "username" in body, body
