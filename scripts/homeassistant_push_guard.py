"""Validate and auto-fix Home Assistant API migrations before pushing."""

import argparse
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class InvalidVersionError(ValueError):
    """Raised when a version string cannot be parsed."""


@dataclass(frozen=True, order=True)
class Version:
    """Small comparable version model for calendar-style Home Assistant versions."""

    major: int
    minor: int
    patch: int = 0

    @classmethod
    def parse(cls, value: str) -> Version:  # noqa: D102
        if not isinstance(value, str):
            raise InvalidVersionError(f"Expected version string, got {type(value)!r}")

        match = re.fullmatch(r"\s*(\d+)\.(\d+)(?:\.(\d+))?\s*", value)
        if not match:
            raise InvalidVersionError(f"Invalid version format: {value!r}")

        major, minor, patch = match.groups(default="0")
        return cls(int(major), int(minor), int(patch))

    def __str__(self) -> str:  # noqa: D105
        return f"{self.major}.{self.minor}.{self.patch}"


DEFAULT_RULES_PATH = (
    Path(__file__).resolve().parent / "homeassistant_upgrade_rules.json"
)
DEFAULT_SOURCE_ROOT = Path("custom_components/jackery_solarvault")
PYPI_HOMEASSISTANT_URL = "https://pypi.org/pypi/homeassistant/json"
REQUEST_TIMEOUT_SECONDS = 30

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpgradeRule:
    """Single codemod-style replacement rule."""

    id: str
    description: str
    pattern: str
    replacement: str
    introduced_in: Version
    file_globs: tuple[str, ...]


@dataclass(frozen=True)
class RuleSet:
    """Parsed rule set with version metadata."""

    min_covered_version: Version
    max_covered_version: Version
    rules: tuple[UpgradeRule, ...]


@dataclass(frozen=True)
class MatchResult:
    """Represents one matched rule occurrence."""

    rule_id: str
    path: Path
    count: int


def parse_args() -> argparse.Namespace:  # noqa: D103
    parser = argparse.ArgumentParser(
        description=(
            "Checks jackery_solarvault against Home Assistant migration rules and can "
            "auto-apply known API fixes."
        )
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=DEFAULT_RULES_PATH,
        help="Path to JSON rule file.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
        help="Source folder to scan.",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Apply replacements in-place.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not modify files, fail if matching deprecated patterns are found.",
    )
    return parser.parse_args()


def _parse_rule(index: int, entry: dict[str, Any]) -> UpgradeRule:
    required_fields = (
        "id",
        "description",
        "pattern",
        "replacement",
        "introduced_in",
    )
    for field_name in required_fields:
        if field_name not in entry:
            raise ValueError(
                f"Rule at index {index} is missing required field '{field_name}'."
            )

    pattern = entry["pattern"]
    if not isinstance(pattern, str):
        raise ValueError(f"Rule at index {index} has non-string pattern.")

    try:
        re.compile(pattern)
    except re.error as err:
        raise ValueError(
            f"Rule '{entry.get('id', index)}' has invalid regex pattern: {err}"
        ) from err

    raw_globs = entry.get("file_globs", ["**/*.py"])
    if not isinstance(raw_globs, list) or not all(
        isinstance(glob, str) for glob in raw_globs
    ):
        raise ValueError(
            f"Rule '{entry.get('id', index)}' has invalid file_globs value."
        )

    try:
        introduced_in = Version.parse(entry["introduced_in"])
    except InvalidVersionError as err:
        raise ValueError(
            f"Rule '{entry.get('id', index)}' has invalid introduced_in version."
        ) from err

    return UpgradeRule(
        id=entry["id"],
        description=entry["description"],
        pattern=pattern,
        replacement=entry["replacement"],
        introduced_in=introduced_in,
        file_globs=tuple(raw_globs),
    )


def load_rules(path: Path) -> RuleSet:  # noqa: D103
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise ValueError(f"Failed to read rule file '{path}': {err}") from err
    except json.JSONDecodeError as err:
        raise ValueError(f"Invalid JSON in rules file '{path}': {err}") from err

    if not isinstance(payload, dict):
        raise ValueError("Rules file must contain a JSON object.")

    for field_name in ("min_covered_version", "max_covered_version", "rules"):
        if field_name not in payload:
            raise ValueError(f"Rules file is missing required field '{field_name}'.")

    try:
        min_covered_version = Version.parse(payload["min_covered_version"])
        max_covered_version = Version.parse(payload["max_covered_version"])
    except InvalidVersionError as err:
        raise ValueError(
            "min_covered_version/max_covered_version must be valid versions."
        ) from err

    if min_covered_version > max_covered_version:
        raise ValueError(
            "min_covered_version cannot be newer than max_covered_version."
        )

    raw_rules = payload["rules"]
    if not isinstance(raw_rules, list):
        raise ValueError("rules must be a list.")

    rules: list[UpgradeRule] = []
    for index, entry in enumerate(raw_rules):
        if not isinstance(entry, dict):
            raise ValueError(f"Rule at index {index} must be a JSON object.")
        rules.append(_parse_rule(index, entry))

    return RuleSet(
        min_covered_version=min_covered_version,
        max_covered_version=max_covered_version,
        rules=tuple(rules),
    )


def _validated_https_url(url: str) -> str:
    """Return a validated HTTPS URL for outbound requests."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"Only absolute HTTPS URLs are allowed, got: {url!r}")
    return url


def fetch_latest_homeassistant_version() -> Version:  # noqa: D103
    request = Request(
        _validated_https_url(PYPI_HOMEASSISTANT_URL),
        headers={
            "User-Agent": "jackery_solarvault-bot/1.0",
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # nosec B310 - request URL is restricted by _validated_https_url.
            status = getattr(response, "status", 200)
            if status != 200:
                raise ValueError(f"PyPI API returned unexpected status {status}")
            payload = json.loads(response.read().decode("utf-8"))
        return Version.parse(payload["info"]["version"])
    except (HTTPError, URLError, TimeoutError, OSError) as err:
        raise RuntimeError(
            "Failed to fetch latest Home Assistant version from PyPI."
        ) from err
    except (json.JSONDecodeError, KeyError, ValueError, InvalidVersionError) as err:
        raise RuntimeError(
            "PyPI returned malformed Home Assistant version data."
        ) from err


def applicable_rules(  # noqa: D103
    rule_set: RuleSet, latest_version: Version
) -> tuple[UpgradeRule, ...]:
    return tuple(
        rule for rule in rule_set.rules if latest_version >= rule.introduced_in
    )


def collect_python_files(root: Path, globs: tuple[str, ...]) -> list[Path]:  # noqa: D103
    files: set[Path] = set()
    for pattern in globs:
        files.update(root.glob(pattern))
    return sorted(path for path in files if path.is_file())


def _write_file_atomically(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as tmp_file:
        tmp_file.write(content)
        tmp_path = Path(tmp_file.name)

    try:
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def apply_rule(path: Path, rule: UpgradeRule, *, fix: bool) -> int:  # noqa: D103
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as err:
        _LOGGER.warning("Unable to read %s: %s", path, err)
        return 0

    updated, count = re.subn(rule.pattern, rule.replacement, content)

    if not (fix and count):
        return count

    backup_path = path.with_suffix(f"{path.suffix}.bak")
    backup_created = False
    try:
        shutil.copy2(path, backup_path)
        backup_created = True
        _write_file_atomically(path, updated)
    except OSError as err:
        _LOGGER.warning("Failed to update %s safely: %s", path, err)
        if backup_created:
            try:
                shutil.copy2(backup_path, path)
            except OSError as restore_err:
                _LOGGER.critical(
                    "Failed to restore %s from backup %s: %s",
                    path,
                    backup_path,
                    restore_err,
                )
        return 0
    finally:
        if backup_created and backup_path.exists():
            try:
                backup_path.unlink()
            except OSError as err:
                _LOGGER.warning("Failed to remove backup file %s: %s", backup_path, err)

    return count


def run(  # noqa: D103
    root: Path, rule_set: RuleSet, *, fix: bool
) -> tuple[list[MatchResult], Version]:
    latest_version = fetch_latest_homeassistant_version()
    rules = applicable_rules(rule_set, latest_version)

    findings: list[MatchResult] = []
    for rule in rules:
        for path in collect_python_files(root, rule.file_globs):
            count = apply_rule(path, rule, fix=fix)
            if count:
                findings.append(MatchResult(rule_id=rule.id, path=path, count=count))

    return findings, latest_version


def print_findings(findings: list[MatchResult]) -> None:  # noqa: D103
    grouped: dict[str, list[MatchResult]] = {}
    for finding in findings:
        grouped.setdefault(finding.rule_id, []).append(finding)

    for rule_id, results in grouped.items():
        print(f"- Rule {rule_id}:")
        for result in results:
            print(f"  * {result.path}: {result.count} matches")


def validate_coverage(rule_set: RuleSet, latest_version: Version) -> bool:  # noqa: D103
    if latest_version <= rule_set.max_covered_version:
        return True
    print(
        "WARNING: The rule definition covers Home Assistant versions only up to "
        f"{rule_set.max_covered_version}, but found {latest_version}."
    )
    print(
        "Please extend scripts/homeassistant_upgrade_rules.json so new breaking "
        "changes can be fixed automatically."
    )
    return False


def main() -> int:  # noqa: D103
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = parse_args()
    if args.fix and args.check:
        print("Please use either --fix or --check.")
        return 2

    mode_fix = args.fix and not args.check
    rule_set = load_rules(args.rules)
    try:
        findings, latest_version = run(args.root, rule_set, fix=mode_fix)
    except RuntimeError as err:
        _LOGGER.error("Failed to run push guard: %s", err, exc_info=True)
        return 1

    coverage_ok = validate_coverage(rule_set, latest_version)

    print(f"Latest Home Assistant version from PyPI: {latest_version}")
    if findings:
        print_findings(findings)

    if args.check:
        if findings:
            print("Error: Deprecated patterns found. Use --fix for automatic repairs.")
            return 1
        if not coverage_ok:
            return 1
        print("OK: No known deprecated Home Assistant patterns found.")
        return 0

    if mode_fix:
        if findings:
            print("Automatic migrations were applied.")
        else:
            print("No automatic migrations were needed.")
        return 0 if coverage_ok else 1

    # Default mode behaves like check in CI-safe mode.
    if findings or not coverage_ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
