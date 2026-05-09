"""Source-level lifecycle contracts for the Jackery SolarVault integration."""

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "jackery_solarvault"
INIT = COMPONENT / "__init__.py"
BRAND = COMPONENT / "brand.py"


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


def test_cached_brand_sync_is_non_blocking_best_effort() -> None:
    """Brand-cache filesystem errors must not block integration setup."""
    body = _function_source("_async_ensure_cached_brand_images", source_path=BRAND)
    assert "except OSError" in body, body
    assert "return" in body, body


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
