"""scripts/sync_requirements.py.

Scannt alle Imports im Projekt und prüft die Laufzeit- und Testabhängigkeiten.
Das Manifest bestimmt die Laufzeitpakete; requirements-test.txt bestimmt die
Versionsangaben der Testpakete (auch nach Dependabot-Updates).

Usage:
    python -m scripts.sync_requirements           # Dry-run (zeigt Diff)
    python -m scripts.sync_requirements --write   # Spiegel/Laufzeitdatei aktualisieren
    python -m scripts.sync_requirements --write --force-runtime-from-manifest
                                                # Bewusst Laufzeitdatei ersetzen
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
STDLIB: set[str] = set(sys.stdlib_module_names)
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
# 4. Pakete die immer in requirements-test.txt stehen sollen
#    (Typ-Stubs, CI-Tools usw. ohne direkten Import). Versionen stehen nur in
#    requirements-test.txt, damit Dependabot sie aktualisieren kann.
# ---------------------------------------------------------------------------
ALWAYS_TEST: list[str] = [
    'aiohttp',
    'aiomqtt',
    'aiousbwatcher',
    'annotated-doc',
    'annotated-types',
    'annotatedyaml',
    'astroid',
    'attrs',
    'autotyping',
    'coverage[toml]',
    'homeassistant',
    'hypothesis',
    'ifaddr',
    'iniconfig',
    'jinja2',
    'multidict',
    'mypy',
    'packaging',
    'pip',
    'pre-commit',
    'pylint',
    'pyrefly',
    'pytest',
    'pytest-asyncio',
    'pytest-cov',
    'pytest-github-actions-annotate-failures',
    'pytest-homeassistant-custom-component',
    'pytest-mock',
    'pytest-socket',
    'python-dateutil',
    'python_discovery',
    'pyyaml',
    'ruff',
    'serialx',
    'smellcheck',
    'ty',
    'types-aiofiles',
    'types-atomicwrites',
    'types-caldav',
    'types-chardet',
    'types-croniter',
    'types-decorator',
    'types-pexpect',
    'types-protobuf',
    'types-psutil',
    'types-pyserial',
    'types-python-dateutil',
    'types-python-slugify',
    'types-pytz',
    'types-PyYAML',
    'types-requests',
    'types-xmltodict',
    'typing-extensions',
    'typing-inspection',
    'uv',
    'voluptuous',
    'voluptuous-serialize',
    'wrapt',
]


# ---------------------------------------------------------------------------
# 5. Import-Scanner
# ---------------------------------------------------------------------------
def scan_imports(files: list[pathlib.Path]) -> set[str]:  # noqa: D103
    imports: set[str] = set()
    for f in files:
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
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
    requirements = data.get("requirements", [])
    if not isinstance(requirements, list):
        return []
    return [item for item in requirements if isinstance(item, str)]


# ---------------------------------------------------------------------------
# 7. Diff-Anzeige
# ---------------------------------------------------------------------------
def show_diff(label: str, current: list[str], proposed: list[str]) -> bool:  # noqa: D103
    cur = {normalized for line in current if (normalized := line.split("#")[0].strip())}
    pro = {
        normalized
        for line in proposed
        if (normalized := line.split("#")[0].strip())
    }
    added = pro - cur
    removed = cur - pro
    if not added and not removed:
        return False
    for requirement in sorted(added):
        print(f"{label}: + {requirement}")
    for requirement in sorted(removed):
        print(f"{label}: - {requirement}")
    return True


def requirement_name(line: str) -> str:
    """Normalize a requirement's package name without comparing its version."""
    return Requirement(line.split("#", 1)[0].strip()).name.lower().replace("-", "_")


# ---------------------------------------------------------------------------
# 8. Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:  # noqa: D103
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Dateien schreiben")
    parser.add_argument(
        "--force-runtime-from-manifest",
        action="store_true",
        help="requirements.txt bewusst mit den Manifest-Werten überschreiben",
    )
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

    third_party(scan_imports(int_files))
    third_party(scan_imports(test_files))
    scr_tp = third_party(scan_imports(script_files))

    # -- requirements.txt (aus manifest.json) --------------------------------
    manifest_reqs = manifest_requirements()
    req_path = ROOT / "requirements.txt"
    current_req = (
        req_path.read_text(encoding="utf-8").splitlines() if req_path.exists() else []
    )

    changed_req = show_diff("requirements.txt", current_req, manifest_reqs)

    # -- requirements-test.txt ist die Quelle für Test-Versionen --------------
    req_test_path = ROOT / "requirements-test.txt"
    mirror_path = ROOT / "requirements_test.txt"
    current_test = (
        req_test_path.read_text(encoding="utf-8").splitlines()
        if req_test_path.exists()
        else []
    )

    # Unbekannte Script-Imports warnen
    declared_test_requirements = {requirement_name(item) for item in ALWAYS_TEST}
    (
        {
            Requirement(IMPORT_TO_PYPI.get(module, module).split("#")[0].strip())
            .name.lower()
            .replace("-", "_")
            for module in scr_tp
        }
        - declared_test_requirements
        - {"astroid"}
    )

    current_names = {
        requirement_name(line)
        for line in current_test
        if line.split("#", 1)[0].strip()
    }
    missing_test = [item for item in ALWAYS_TEST if requirement_name(item) not in current_names]
    unexpected_test = current_names - declared_test_requirements
    proposed_test = [
        line
        for line in current_test
        if not line.split("#", 1)[0].strip()
        or requirement_name(line) in declared_test_requirements
    ] + missing_test
    changed_test = show_diff(req_test_path.name, current_test, proposed_test)
    if mirror_path.exists():
        mirror_current = mirror_path.read_text(encoding="utf-8").splitlines()
        changed_test = show_diff(mirror_path.name, mirror_current, current_test) or changed_test

    any_changed = changed_req or changed_test

    if args.write:
        if missing_test or unexpected_test:
            print(
                "requirements-test.txt package inventory differs from ALWAYS_TEST. "
                "Reconcile package names and intentional version constraints before --write."
            )
            return 1
        if changed_req and req_path.exists() and not args.force_runtime_from_manifest:
            print(
                "requirements.txt differs from manifest.json; reconcile both files "
                "or use --force-runtime-from-manifest to intentionally replace "
                "requirements.txt"
            )
            return 1
        if not req_path.exists() or (changed_req and args.force_runtime_from_manifest):
            content = "\n".join(manifest_reqs) + "\n" if manifest_reqs else ""
            req_path.write_text(content, encoding="utf-8")

        # The canonical test file is edited by Dependabot or deliberately by a
        # maintainer. Only an optional legacy mirror is ever regenerated.
        if mirror_path.exists():
            mirror_path.write_text(req_test_path.read_text(encoding="utf-8"), encoding="utf-8")

    elif args.check and any_changed:
        # Use ASCII-only punctuation here: Windows consoles default to cp1252
        # and crash on U+2717 / U+2014 unless stdout is reconfigured. The
        # gate script captures stdout for diagnostics, so an encode error
        # would mask the actual drift report.
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
