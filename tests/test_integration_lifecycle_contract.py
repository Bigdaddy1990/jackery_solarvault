"""Source-level lifecycle contracts for the Jackery SolarVault integration."""

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "jackery_solarvault"
INIT = COMPONENT / "__init__.py"


def _read_init() -> str:
    return INIT.read_text(encoding="utf-8")


def _function_source(name: str, *, source_path: Path | None = None) -> str:
    path = source_path or INIT
    source = path.read_text(encoding="utf-8")
    match = re.search(
        rf"^async def {name}.*?(?=^async def |^def |^class |\Z)",
        source,
        re.S | re.M,
    )
    assert match is not None, f"{name} not found in {path.name}"
    return match.group(0)


def test_async_setup_does_not_mutate_brand_assets() -> None:
    """Brand assets are packaged, not copied into the integration at runtime."""
    init_source = _read_init()
    body = _function_source("async_setup")

    assert "_async_ensure_cached_brand_images" not in init_source
    assert "brand.py" not in init_source
    assert "async_setup_services(hass)" in body, body


def test_unload_keeps_coordinator_alive_when_platform_unload_fails() -> None:
    """A failed platform unload must not shut down the still-loaded entry."""
    body = _function_source("async_unload_entry")
    tree = ast.parse(body)
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                calls.append(func.attr)
    assert "async_unload_platforms" in calls, body
    assert "async_shutdown" in calls, body
    assert calls.index("async_unload_platforms") < calls.index("async_shutdown"), body
    assert "if not unload_ok" in body, body
    assert "return False" in body, body
