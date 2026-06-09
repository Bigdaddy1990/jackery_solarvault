"""scripts/sync_requirements.py.

Scannt alle Imports im Projekt, filtert Stdlib und HA-bereitgestellte Pakete
heraus und schreibt requirements.txt / requirements_test.txt neu.

Usage:
    python -m scripts.sync_requirements           # Dry-run (zeigt Diff)
    python -m scripts.sync_requirements --write   # Schreibt Dateien
    python -m scripts.sync_requirements --check   # CI-Modus: Exit 1 wenn Abweichung
"""

import argparse
import ast
import pathlib
import sys

from packaging.requirements import Requirement

ROOT = pathlib.Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# 1. Python-Stdlib-Module (3.11+)
# ---------------------------------------------------------------------------
STDLIB: set[str] = set(sys.stdlib_module_names)  # type: ignore[attr-defined]
# Ergänze bekannte Stdlib-Namen die in älteren Python-Versionen fehlen könnten
STDLIB |= {
    "abc",
    "argparse",
    "ast",
    "asyncio",
    "base64",
    "builtins",
    "calendar",
    "cgi",
    "cmath",
    "code",
    "collections",
    "compileall",
    "contextlib",
    "contextvars",
    "copy",
    "csv",
    "dataclasses",
    "datetime",
    "difflib",
    "dis",
    "email",
    "enum",
    "fnmatch",
    "fractions",
    "functools",
    "gc",
    "getpass",
    "gettext",
    "glob",
    "gzip",
    "hashlib",
    "hmac",
    "html",
    "http",
    "importlib",
    "inspect",
    "io",
    "itertools",
    "json",
    "linecache",
    "locale",
    "logging",
    "math",
    "mimetypes",
    "numbers",
    "operator",
    "os",
    "pathlib",
    "pickle",
    "platform",
    "posixpath",
    "pprint",
    "py_compile",
    "queue",
    "random",
    "re",
    "secrets",
    "shlex",
    "shutil",
    "signal",
    "socket",
    "sqlite3",
    "ssl",
    "stat",
    "statistics",
    "string",
    "struct",
    "subprocess",
    "sys",
    "tarfile",
    "tempfile",
    "textwrap",
    "threading",
    "time",
    "timeit",
    "tomllib",
    "traceback",
    "types",
    "typing",
    "unicodedata",
    "unittest",
    "urllib",
    "uuid",
    "venv",
    "warnings",
    "weakref",
    "xml",
    "xmlrpc",
    "zipfile",
    "zipimport",
    "zlib",
    "zoneinfo",
    "_thread",
    "__future__",
}

# ---------------------------------------------------------------------------
# 2. Von Home Assistant Core bereitgestellte Pakete
#    (werden NICHT in requirements.txt aufgenommen)
# ---------------------------------------------------------------------------
HA_PROVIDED: set[str] = {
    "aiohttp",
    "async_timeout",
    "attr",
    "attrs",
    "certifi",
    "charset_normalizer",
    "cryptography",
    "homeassistant",
    "httpx",
    "jinja2",
    "multidict",
    "orjson",
    "pydantic",
    "pyserial",
    "pytest_homeassistant_custom_component",
    "typing_extensions",
    "voluptuous",
    "yarl",
    "zeroconf",
    # Pytest-Stack wird vom plugin mitgebracht
    "pytest",
    "pytest_asyncio",
    "pytest_cov",
    "_pytest",
    # interne Packages
    "custom_components",
    "tests",
    "scripts",
}

# ---------------------------------------------------------------------------
# 3. Mapping: Import-Name → PyPI-Paketname (wenn abweichend)
# ---------------------------------------------------------------------------
IMPORT_TO_PYPI: dict[str, str] = {
    "annotatedyaml": "annotatedyaml>=1.0.2",
    "aiofiles": "aiofiles>=25.1.0",
    "hypothesis": "hypothesis",
    "packaging": "packaging>=26.0",
    "pip": "pip>=26.0",
    "pylint": "pylint",
    "astroid": "astroid",  # kommt mit pylint
    "coverage": "coverage[toml]>=7.5.4",
    "pytest_homeassistant_custom_component": "pytest-homeassistant-custom-component",
    "yaml": "pyyaml",
    "pytest_cov": "pytest-cov",
    "voluptuous": "voluptuous>=0.15.2",
}

# ---------------------------------------------------------------------------
# 4. Pakete die immer in requirements_test.txt stehen sollen
#    (Typ-Stubs, CI-Tools usw. die kein direktes Import haben)
# ---------------------------------------------------------------------------
ALWAYS_TEST: list[str] = [
    "aiohttp>=3.13.3",
    "aiomqtt",
    "annotatedyaml>=1.0.2",
    "coverage>=7.10.6",
    "coverage[toml]>=7.5.4",
    "hypothesis",
    "jinja2>=3.1.6",
    "mypy>=1.19.1",
    "packaging>=26.0",
    "pip>=26.0",
    "pytest",
    "pytest-asyncio",
    "pytest-cov",
    "pytest-github-actions-annotate-failures>=0.3.0",
    "pytest-homeassistant-custom-component  # follows daily HA version",
    "pyyaml",
    "pylint",
    "ruff>=0.15.14",
    "types-aiofiles>=25.1.0.20251011",
    "types-atomicwrites>=1.4.5.1",
    "types-caldav>=1.3.0.20250516",
    "types-chardet>=0.1.5",
    "types-croniter>=6.0.0.20250809",
    "types-decorator>=5.2.0.20251101",
    "types-pexpect>=4.9.0.20250916",
    "types-protobuf>=6.30.2.20250914",
    "types-psutil>=7.1.1.20251122",
    "types-pyserial>=3.5.0.20251001",
    "types-python-dateutil>=2.9.0.20260124",
    "types-python-slugify>=8.0.2.20240310",
    "types-pytz>=2025.2.0.20251108",
    "types-PyYAML>=6.0.12.20250915",
    "types-requests>=2.32.4.20260107",
    "types-xmltodict>=1.0.1.20260113",
    "typing-extensions>=4.15.0,<5.0",
    "voluptuous>=0.15.2",
]


# ---------------------------------------------------------------------------
# 5. Import-Scanner
# ---------------------------------------------------------------------------
def scan_imports(files: list[pathlib.Path]) -> set[str]:  # noqa: D103
    imports: set[str] = set()
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  SKIP {f}: {exc}", file=sys.stderr)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imports.add(node.module.split(".")[0])
    return imports


def third_party(imports: set[str]) -> set[str]:  # noqa: D103
    return imports - STDLIB - HA_PROVIDED


# ---------------------------------------------------------------------------
# 6. requirements.txt aus manifest.json ableiten
# ---------------------------------------------------------------------------
def manifest_requirements() -> list[str]:
    """Liest requirements aus custom_components/jackery_solarvault/manifest.json."""
    import json

    manifest = ROOT / "custom_components" / "jackery_solarvault" / "manifest.json"
    if not manifest.exists():
        return []
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return data.get("requirements", [])


# ---------------------------------------------------------------------------
# 7. Diff-Anzeige
# ---------------------------------------------------------------------------
def show_diff(label: str, current: list[str], proposed: list[str]) -> bool:  # noqa: D103
    cur = {
        line.split("#")[0].strip()
        for line in current
        if line.strip() and not line.startswith("#")
    }
    pro = {
        line.split("#")[0].strip()
        for line in proposed
        if line.strip() and not line.startswith("#")
    }
    added = pro - cur
    removed = cur - pro
    if not added and not removed:
        print(f"  {label}: keine Änderungen")
        return False
    if added:
        for a in sorted(added):
            print(f"  + {label}: {a}")
    if removed:
        for r in sorted(removed):
            print(f"  - {label}: {r}")
    return True


# ---------------------------------------------------------------------------
# 8. Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:  # noqa: D103
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Dateien schreiben")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 bei Abweichung (CI)",
    )
    args = parser.parse_args(argv)

    # -- Scan ----------------------------------------------------------------
    int_files = list((ROOT / "custom_components" / "jackery_solarvault").rglob("*.py"))
    test_files = list((ROOT / "tests").rglob("*.py"))
    script_files = list((ROOT / "scripts").rglob("*.py"))

    int_tp = third_party(scan_imports(int_files))
    tst_tp = third_party(scan_imports(test_files))
    scr_tp = third_party(scan_imports(script_files))

    print(f"  Integration drittanbieter-Imports: {sorted(int_tp)}")
    print(f"  Test drittanbieter-Imports:        {sorted(tst_tp)}")
    print(f"  Script drittanbieter-Imports:      {sorted(scr_tp)}")

    # -- requirements.txt (aus manifest.json) --------------------------------
    manifest_reqs = manifest_requirements()
    req_path = ROOT / "requirements.txt"
    current_req = (
        req_path.read_text(encoding="utf-8").splitlines() if req_path.exists() else []
    )

    print("\n[requirements.txt]")
    changed_req = show_diff("requirements.txt", current_req, manifest_reqs)

    # -- requirements_test.txt / requirements-test.txt ------------------------
    req_test_paths = (
        ROOT / "requirements_test.txt",
        ROOT / "requirements-test.txt",
    )
    req_test_path = req_test_paths[0]
    current_test = (
        req_test_path.read_text(encoding="utf-8").splitlines()
        if req_test_path.exists()
        else []
    )

    # Unbekannte Script-Imports warnen
    declared_test_requirements = {
        Requirement(requirement.split("#")[0].strip()).name.lower().replace("-", "_")
        for requirement in ALWAYS_TEST
        if requirement.split("#")[0].strip()
    }
    unknown = (
        {
            Requirement(IMPORT_TO_PYPI.get(module, module).split("#")[0].strip())
            .name.lower()
            .replace("-", "_")
            for module in scr_tp
        }
        - declared_test_requirements
        - {"astroid"}
    )
    if unknown:
        print(f"\n  WARN Undeklarierte Script-Imports: {sorted(unknown)}")

    print("\n[requirements_test.txt]")
    changed_test = show_diff("requirements_test.txt", current_test, ALWAYS_TEST)
    for mirror_path in req_test_paths[1:]:
        mirror_current = (
            mirror_path.read_text(encoding="utf-8").splitlines()
            if mirror_path.exists()
            else []
        )
        changed_test = (
            show_diff(mirror_path.name, mirror_current, ALWAYS_TEST) or changed_test
        )

    any_changed = changed_req or changed_test

    if args.write:
        # requirements.txt
        content = "\n".join(manifest_reqs) + "\n" if manifest_reqs else ""
        req_path.write_text(content, encoding="utf-8")
        print(f"\n  OK {req_path} geschrieben ({len(manifest_reqs)} Eintraege)")

        # requirements_test.txt / requirements-test.txt
        test_content = "\n".join(ALWAYS_TEST) + "\n"
        for path in req_test_paths:
            path.write_text(test_content, encoding="utf-8")
            print(f"  OK {path} geschrieben ({len(ALWAYS_TEST)} Eintraege)")

    elif args.check and any_changed:
        # Use ASCII-only punctuation here: Windows consoles default to cp1252
        # and crash on U+2717 / U+2014 unless stdout is reconfigured. The
        # gate script captures stdout for diagnostics, so an encode error
        # would mask the actual drift report.
        print(
            "\n  [DRIFT] requirements sind nicht synchron -- "
            "bitte `python -m scripts.sync_requirements --write` ausfuehren"
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
