"""Block regressions that instantiate dedicated aiohttp sessions."""

import ast
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_ROOT = REPO_ROOT / "custom_components" / "jackery_solarvault"
ALLOWED_FILES = {
    INTEGRATION_ROOT / "http_client.py",
}


def _iter_python_sources() -> Iterable[Path]:
    for path in INTEGRATION_ROOT.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        if path in ALLOWED_FILES:
            # ``http_client.py`` provides helpers that validate the shared
            # session and therefore needs to reference ``ClientSession``
            # directly. All other modules should rely on Home Assistant's
            # managed session.
            continue
        yield path


def _is_client_session_constructor(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "ClientSession"

    if isinstance(node, ast.Attribute):
        parts: list[str] = []
        current: ast.AST | None = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        dotted = ".".join(reversed(parts))
        return dotted.endswith("ClientSession")

    return False


def _find_violations(path: Path) -> list[str]:
    violations: list[str] = []
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_client_session_constructor(node.func):
            continue
        violations.append(f"{path}:{node.lineno}:{node.col_offset + 1}")

    return violations


def main() -> int:  # noqa: D103
    violations: list[str] = []

    for path in _iter_python_sources():
        violations.extend(_find_violations(path))

    if violations:
        print(
            "Detected aiohttp.ClientSession instantiations. "
            "Use hass.helpers.aiohttp_client.async_get_clientsession instead:",
        )
        for violation in violations:
            print(f"  - {violation}")
        return 1

    print("Shared session guard passed; no ClientSession constructors detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
