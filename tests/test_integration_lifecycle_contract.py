"""Source-level lifecycle contracts for the Jackery SolarVault integration."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "jackery_solarvault"
INIT = COMPONENT / "__init__.py"


def _read_init() -> str:
    """Read and return the UTF-8 text contents of the integration's __init__.py file.

    Returns:
        text (str): The UTF-8 decoded contents of the file referenced by `INIT`.
    """
    return INIT.read_text(encoding="utf-8")


def _function_source(name: str, *, source_path: Path | None = None) -> str:
    """Return the source-text block for a top-level `async def` function named `name` from the given file.

    Reads UTF-8 text from `source_path` (or the module-level `INIT` path when not provided) and returns the contiguous source snippet that begins with `async def {name}` up to the next top-level `async def`, `def`, `class`, or end of file.

    Parameters:
        name: The target async function name to extract.
        source_path: Optional path of the file to read; if omitted, uses `INIT`.

    Returns:
        The matched source code block as a string.

    Raises:
        AssertionError: If a matching `async def {name}` block is not found in the file.
    """  # ruff: ignore[line-too-long]
    path = source_path or INIT
    source = path.read_text(encoding="utf-8")
    match = re.search(
        rf"^async def {name}.*?(?=^async def |^def |^class |\Z)",
        source,
        re.DOTALL | re.MULTILINE,
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
