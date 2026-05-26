"""Monitor the vendored PyYAML release used by annotatedyaml.

This helper fetches the current PyPI release metadata and OSV vulnerability
records for PyYAML, compares them with the vendored version shipped under
``annotatedyaml/_vendor/yaml``, and reports drift or security issues. The
script is intentionally lightweight so it can run inside GitHub Actions on a
schedule without additional dependencies.
"""

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from packaging.version import InvalidVersion, Version
from pip._vendor import requests

ANNOTATEDYAML_INIT = Path("annotatedyaml/_vendor/yaml/__init__.py")
ANNOTATEDYAML_MODULE = Path("annotatedyaml/_vendor/yaml.py")
PYPI_URL = "https://pypi.org/pypi/PyYAML/json"
OSV_URL = "https://api.osv.dev/v1/query"

SEVERITY_ORDER: dict[str, int] = {
    "CRITICAL": 4,
    "HIGH": 3,
    "MODERATE": 2,
    "MEDIUM": 2,
    "LOW": 1,
}


class MonitoringError(RuntimeError):
    """Raised when the monitoring routine cannot complete."""


@dataclass
class VulnerabilityRecord:
    """Details about a vulnerability affecting the vendored release."""

    identifier: str
    summary: str
    severity: str | None
    affected_version_range: str | None
    references: list[str]


@dataclass
class WheelProfile:
    """Wheel combination that should be tracked for availability."""

    python_tag: str
    platform_fragment: str


@dataclass
class WheelMatch:
    """Details about an available wheel for a tracked profile."""

    profile: WheelProfile
    release: Version | None
    filename: str
    url: str


@dataclass
class MonitoringResult:
    """Aggregated outcome of the monitoring run."""

    vendor_version: Version
    latest_release: Version | None
    latest_release_files: list[dict[str, Any]]
    vulnerabilities: list[VulnerabilityRecord]
    wheel_matches: list[WheelMatch]


def _parse_wheel_profile(value: str) -> WheelProfile:
    """Return a wheel profile parsed from the CLI representation."""
    if ":" not in value:
        raise argparse.ArgumentTypeError(
            "Wheel profile must follow <python_tag>:<platform_fragment> format."
        )
    python_tag, platform_fragment = value.split(":", 1)
    python_tag = python_tag.strip()
    platform_fragment = platform_fragment.strip()
    if not python_tag or not platform_fragment:
        raise argparse.ArgumentTypeError(
            "Wheel profile requires both a python tag and platform fragment."
        )
    return WheelProfile(python_tag=python_tag, platform_fragment=platform_fragment)


DEFAULT_WHEEL_PROFILES = (
    WheelProfile(python_tag="cp313", platform_fragment="manylinux"),
    WheelProfile(python_tag="cp313", platform_fragment="musllinux"),
)


def _normalise_wheel_profiles(args: argparse.Namespace) -> list[WheelProfile]:
    """Derive the list of wheel profiles that should be monitored."""
    if args.wheel_profile:
        return [
            WheelProfile(
                python_tag=profile.python_tag,
                platform_fragment=profile.platform_fragment,
            )
            for profile in args.wheel_profile
        ]
    if args.target_python_tag is not None or args.target_platform_fragment is not None:
        python_tag = args.target_python_tag or "cp313"
        platform_fragment = args.target_platform_fragment or "manylinux"
        return [
            WheelProfile(python_tag=python_tag, platform_fragment=platform_fragment)
        ]
    return [
        WheelProfile(
            python_tag=profile.python_tag,
            platform_fragment=profile.platform_fragment,
        )
        for profile in DEFAULT_WHEEL_PROFILES
    ]


def parse_arguments() -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(
        description=(
            "Check the vendored PyYAML version against PyPI and OSV metadata."
        ),
    )
    parser.add_argument(
        "--fail-on-outdated",
        action="store_true",
        help=(
            "Exit with status 1 when a newer stable PyYAML release is available on PyPI."
        ),
    )
    parser.add_argument(
        "--fail-severity",
        default="HIGH",
        choices=sorted(SEVERITY_ORDER),
        help=(
            "Lowest severity that triggers a non-zero exit when vulnerabilities "
            "affect the vendored version (default: HIGH)."
        ),
    )
    parser.add_argument(
        "--target-python-tag",
        default=None,
        help=(
            "Deprecated: prefer --wheel-profile. When provided, combines with "
            "--target-platform-fragment to build a single tracked profile."
        ),
    )
    parser.add_argument(
        "--target-platform-fragment",
        default=None,
        help=(
            "Deprecated: prefer --wheel-profile. When provided, combines with "
            "--target-python-tag to build a single tracked profile."
        ),
    )
    parser.add_argument(
        "--wheel-profile",
        action="append",
        default=[],
        type=_parse_wheel_profile,
        metavar="<python_tag>:<platform>",
        help=(
            "Wheel profile to track (repeatable). Defaults to cp313:manylinux "
            "and cp313:musllinux to cover PEP 600 and PEP 656 wheels."
        ),
    )
    parser.add_argument(
        "--metadata-path",
        help=(
            "Optional path where the monitoring summary should be written as "
            "JSON for downstream automation."
        ),
    )
    return parser.parse_args()


def load_vendor_version() -> Version:
    """Extract the vendored PyYAML version from annotatedyaml."""
    source_path = ANNOTATEDYAML_INIT
    if not source_path.exists():
        source_path = ANNOTATEDYAML_MODULE
    if not source_path.exists():
        raise MonitoringError(
            "annotatedyaml/_vendor/yaml/__init__.py and "
            "annotatedyaml/_vendor/yaml.py are missing; vendored PyYAML cannot "
            "be inspected."
        )
    content = source_path.read_text(encoding="utf-8")
    match = re.search(
        r"^\s*__version__\s*=\s*[\"']([^\"']+)[\"']",
        content,
        re.MULTILINE,
    )
    if match is None:
        raise MonitoringError(
            "Could not locate __version__ assignment in vendored PyYAML module."
        )
    try:
        return Version(match.group(1))
    except InvalidVersion as exc:
        raise MonitoringError(
            f"Vendored PyYAML version '{match.group(1)}' is not a valid version."
        ) from exc


def fetch_pypi_metadata() -> dict[str, Any]:
    """Load the PyPI JSON metadata for PyYAML."""
    try:
        response = requests.get(PYPI_URL, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise MonitoringError("Failed to fetch PyYAML metadata from PyPI") from exc
    data = response.json()
    if "releases" not in data:
        raise MonitoringError("PyPI response is missing release metadata.")
    return data


def select_latest_release(
    data: dict[str, Any],
) -> tuple[Version | None, list[dict[str, Any]]]:
    """Return the newest stable release and its file entries."""
    latest_version: Version | None = None
    latest_files: list[dict[str, Any]] = []
    for raw_version, files in data["releases"].items():
        try:
            parsed = Version(raw_version)
        except InvalidVersion:
            continue
        if parsed.is_prerelease or parsed.is_devrelease:
            continue
        if latest_version is None or parsed > latest_version:
            latest_version = parsed
            latest_files = files
    return latest_version, latest_files


def _convert_version(raw: str | None) -> Version | None:
    if raw in (None, "0"):
        return Version("0") if raw == "0" else None
    try:
        return Version(raw)
    except InvalidVersion:
        return None


def _range_contains_version(events: list[dict[str, str]], version: Version) -> bool:
    active_start: Version | None = None
    for event in events:
        if "introduced" in event:
            active_start = _convert_version(event.get("introduced"))
        elif "fixed" in event:
            if active_start is None:
                continue
            fixed_version = _convert_version(event.get("fixed"))
            if fixed_version is None:
                continue
            if active_start <= version < fixed_version:
                return True
            active_start = None
        elif "last_affected" in event:
            if active_start is None:
                continue
            last_version = _convert_version(event.get("last_affected"))
            if last_version is None:
                continue
            if active_start <= version <= last_version:
                return True
            active_start = None
    return bool(active_start is not None and active_start <= version)


def _format_range(events: list[dict[str, str]]) -> str | None:
    segments: list[str] = []
    active_start: str | None = None
    for event in events:
        if "introduced" in event:
            active_start = event["introduced"]
        elif "fixed" in event and active_start is not None:
            segments.append(f"[{active_start}, {event['fixed']})")
            active_start = None
        elif "last_affected" in event and active_start is not None:
            segments.append(f"[{active_start}, {event['last_affected']}]")
            active_start = None
    if not segments:
        return None
    return ", ".join(segments)


def _normalise_severity(raw: str | None) -> str | None:
    if raw is None:
        return None
    normalised = raw.strip().upper()
    if not normalised:
        return None
    if normalised == "MODERATE":
        normalised = "MEDIUM"
    return normalised if normalised in SEVERITY_ORDER else None


def _severity_from_cvss(score: float) -> str:
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MEDIUM"
    if score > 0:
        return "LOW"
    return "LOW"


def _derive_severity(entry: dict[str, Any]) -> str:
    database_specific = entry.get("database_specific", {})
    severity = _normalise_severity(database_specific.get("severity"))
    if severity is not None:
        return severity
    severity_entries = entry.get("severity") or []
    highest_score: float | None = None
    for record in severity_entries:
        score = record.get("score")
        numeric: float | None
        if isinstance(score, (int, float)):
            numeric = float(score)
        elif isinstance(score, str):
            try:
                numeric = float(score)
            except ValueError:
                numeric = None
        else:
            numeric = None
        if numeric is None:
            continue
        if highest_score is None or numeric > highest_score:
            highest_score = numeric
    if highest_score is not None:
        return _severity_from_cvss(highest_score)
    return "CRITICAL"


def query_osv(vendor_version: Version) -> list[VulnerabilityRecord]:
    """Return vulnerabilities that still affect the vendored version."""
    payload = {
        "package": {"name": "PyYAML", "ecosystem": "PyPI"},
        "version": str(vendor_version),
    }
    try:
        response = requests.post(OSV_URL, json=payload, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise MonitoringError("Failed to query OSV for PyYAML vulnerabilities") from exc
    data = response.json()
    entries: list[dict[str, Any]] = (
        data.get("vulns") or data.get("vulnerabilities") or []
    )
    results: list[VulnerabilityRecord] = []
    for entry in entries:
        affected = entry.get("affected", [])
        matches_version = False
        range_description: str | None = None
        for affected_entry in affected:
            for ranges in affected_entry.get("ranges", []):
                if ranges.get("type") != "ECOSYSTEM":
                    continue
                events: list[dict[str, str]] = ranges.get("events", [])
                if _range_contains_version(events, vendor_version):
                    matches_version = True
                    if range_description is None:
                        range_description = _format_range(events)
        if not matches_version:
            continue
        severity = _derive_severity(entry)
        references = [
            ref.get("url") for ref in entry.get("references", []) if ref.get("url")
        ]
        results.append(
            VulnerabilityRecord(
                identifier=entry.get("id", "unknown"),
                summary=entry.get("summary", "No summary provided."),
                severity=severity,
                affected_version_range=range_description,
                references=references,
            )
        )
    return results


def locate_target_wheel(
    data: dict[str, Any],
    *,
    python_tag: str,
    platform_fragment: str,
) -> tuple[Version | None, str, str]:
    """Find the newest release that ships a compatible wheel."""
    sorted_releases: list[tuple[Version, list[dict[str, Any]]]] = []
    for raw_version, files in data["releases"].items():
        try:
            parsed = Version(raw_version)
        except InvalidVersion:
            continue
        if parsed.is_prerelease or parsed.is_devrelease:
            continue
        sorted_releases.append((parsed, files))
    sorted_releases.sort(reverse=True)
    for version, files in sorted_releases:
        for file_entry in files:
            if file_entry.get("packagetype") != "bdist_wheel":
                continue
            filename = file_entry.get("filename", "")
            if (
                python_tag in filename
                and platform_fragment in filename
                and not file_entry.get("yanked", False)
            ):
                return version, filename, file_entry.get("url", "")
    return None, "", ""


def build_summary(result: MonitoringResult) -> str:
    """Compose a GitHub Actions step summary for the monitoring run."""
    latest_release_text = (
        f"`{result.latest_release}`" if result.latest_release is not None else "n/a"
    )
    summary_lines = [
        "## Vendored PyYAML status",
        "",
        f"* Vendored release: `{result.vendor_version}`",
        f"* Latest stable release on PyPI: {latest_release_text}",
    ]
    for match in result.wheel_matches:
        profile_label = (
            f"{match.profile.python_tag} ({match.profile.platform_fragment})"
        )
        if match.release is not None:
            summary_lines.append(
                "* ✅ Wheel for "
                f"`{profile_label}` discovered in PyYAML `{match.release}` - "
                "plan removal of the vendor copy."
            )
        else:
            summary_lines.append(
                "* ⚠️ No PyPI wheel matches the configured runner profile "
                f"`{profile_label}` yet; keep the vendor directory in place."
            )
    if result.vulnerabilities:
        summary_lines.append("*")
        summary_lines.append("* ⚠️ Vulnerabilities affecting the vendored release:")
        for vuln in result.vulnerabilities:
            severity = vuln.severity or "UNKNOWN"
            range_hint = (
                f" (affected range: {vuln.affected_version_range})"
                if vuln.affected_version_range
                else ""
            )
            summary_lines.append(
                f"  * `{vuln.identifier}` - {severity}{range_hint}: {vuln.summary}"
            )
            if vuln.references:
                summary_lines.append(
                    f"    * References: {', '.join(vuln.references[:3])}"
                )
    else:
        summary_lines.append(
            "* ✅ No published OSV vulnerabilities affect the vendored release."
        )
    summary_lines.append("")
    return "\n".join(summary_lines)


def build_metadata_document(result: MonitoringResult) -> dict[str, Any]:
    """Serialise the monitoring result into a JSON-serialisable mapping."""
    return {
        "vendor_version": str(result.vendor_version),
        "latest_release": str(result.latest_release)
        if result.latest_release is not None
        else None,
        "wheel_matches": [
            {
                "python_tag": match.profile.python_tag,
                "platform_fragment": match.profile.platform_fragment,
                "release": str(match.release) if match.release is not None else None,
                "filename": match.filename or None,
                "url": match.url or None,
            }
            for match in result.wheel_matches
        ],
        "vulnerabilities": [
            {
                "identifier": vuln.identifier,
                "summary": vuln.summary,
                "severity": vuln.severity,
                "affected_version_range": vuln.affected_version_range,
                "references": vuln.references,
            }
            for vuln in result.vulnerabilities
        ],
    }


def evaluate(
    *,
    fail_on_outdated: bool,
    fail_severity: str,
    wheel_profiles: list[WheelProfile],
) -> tuple[MonitoringResult, int]:
    """Run the monitoring routine and return the result plus exit code."""
    vendor_version = load_vendor_version()
    pypi_metadata = fetch_pypi_metadata()
    latest_release, latest_files = select_latest_release(pypi_metadata)
    vulnerabilities = query_osv(vendor_version)
    wheel_matches: list[WheelMatch] = []
    for profile in wheel_profiles:
        release, filename, url = locate_target_wheel(
            pypi_metadata,
            python_tag=profile.python_tag,
            platform_fragment=profile.platform_fragment,
        )
        wheel_matches.append(
            WheelMatch(profile=profile, release=release, filename=filename, url=url)
        )
    result = MonitoringResult(
        vendor_version=vendor_version,
        latest_release=latest_release,
        latest_release_files=latest_files,
        vulnerabilities=vulnerabilities,
        wheel_matches=wheel_matches,
    )

    exit_code = 0
    if vulnerabilities:
        highest_severity_value = max(
            SEVERITY_ORDER.get(vuln.severity or "", 0) for vuln in vulnerabilities
        )
        threshold_value = SEVERITY_ORDER.get(fail_severity.upper(), 3)
        if highest_severity_value >= threshold_value:
            print(
                "::error ::Vendored PyYAML is affected by at least one OSV advisory "
                f"with severity >= {fail_severity}."
            )
            exit_code = 1
        else:
            print(
                "::warning ::Vendored PyYAML is affected by OSV advisories, but the "
                f"severity stays below {fail_severity}."
            )
    else:
        print("::notice ::No OSV advisories currently affect the vendored PyYAML.")

    if latest_release is not None and latest_release > vendor_version:
        message = (
            "::warning ::Vendored PyYAML is older than the latest PyPI release "
            f"({vendor_version} < {latest_release})."
        )
        if fail_on_outdated:
            print(message.replace("::warning ::", "::error ::"))
            exit_code = 1
        else:
            print(message)
    else:
        print("::notice ::Vendored PyYAML matches the latest available release.")

    for match in wheel_matches:
        profile_label = (
            f"{match.profile.python_tag} ({match.profile.platform_fragment})"
        )
        if match.release is not None:
            release_url = f"https://pypi.org/project/PyYAML/{match.release}/"
            filename_hint = match.filename or "<unknown wheel>"
            print(
                "::notice ::Compatible PyYAML wheel discovered for "
                f"{profile_label} in release {match.release}: {filename_hint} - "
                "prepare vendor removal."
            )
            print(f"::notice ::Release notes: {release_url}")
            if match.url:
                print(f"::notice ::Download URL: {match.url}")
        else:
            print(
                "::notice ::No matching PyYAML wheel for the configured Home "
                f"Assistant runtime profile {profile_label} has been published yet."
            )
    return result, exit_code


def main() -> int:
    """Entry point for the monitoring script."""
    args = parse_arguments()
    wheel_profiles = _normalise_wheel_profiles(args)
    try:
        result, exit_code = evaluate(
            fail_on_outdated=args.fail_on_outdated,
            fail_severity=args.fail_severity,
            wheel_profiles=wheel_profiles,
        )
    except MonitoringError as exc:
        print(f"::error ::{exc}")
        return 2

    summary = build_summary(result)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(summary)
            if not summary.endswith("\n"):
                handle.write("\n")
    else:
        print(summary)
    if args.metadata_path:
        metadata = build_metadata_document(result)
        metadata_path = Path(args.metadata_path)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
