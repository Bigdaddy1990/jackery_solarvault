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
import requests

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
    """Parse a CLI wheel profile string into a WheelProfile.

    Parameters:
        value (str): CLI value in the format "<python_tag>:<platform_fragment>" (both sides may include surrounding whitespace).

    Returns:
        WheelProfile: Parsed profile with `python_tag` and `platform_fragment` extracted and stripped.

    Raises:
        argparse.ArgumentTypeError: If `value` does not contain ":" or either side is empty after stripping.
    """
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
    WheelProfile(python_tag="cp314", platform_fragment="manylinux"),
    WheelProfile(python_tag="cp314", platform_fragment="musllinux"),
)


def _normalise_wheel_profiles(args: argparse.Namespace) -> list[WheelProfile]:
    """Determine which wheel profiles should be monitored based on parsed CLI arguments.

    Selects profiles in this precedence order: the repeatable `--wheel-profile` values if present; otherwise the deprecated `--target-python-tag`/`--target-platform-fragment` pair (using `cp313` and `manylinux` as defaults for missing sides) if either is set; otherwise the module `DEFAULT_WHEEL_PROFILES`.

    Parameters:
        args (argparse.Namespace): Parsed CLI namespace with attributes `wheel_profile` (list[WheelProfile] | None),
            `target_python_tag` (str | None), and `target_platform_fragment` (str | None).

    Returns:
        list[WheelProfile]: The list of resolved wheel profiles to track.
    """
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
    """Parse and validate command-line arguments for the monitoring script.

    Recognizes options to control failure behaviour and tracked wheel profiles:
    - --fail-on-outdated: treat a newer stable PyYAML on PyPI as a failure.
    - --fail-severity: minimum OSV severity that triggers a non-zero exit.
    - Deprecated --target-python-tag / --target-platform-fragment: legacy single-profile inputs.
    - --wheel-profile: repeatable `<python_tag>:<platform>` values to track specific wheel compatibility.
    - --metadata-path: optional file path to write a JSON summary for downstream automation.

    Returns:
        argparse.Namespace: The parsed CLI arguments.
    """
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
    """Locate and parse the vendored PyYAML __version__ from the annotatedyaml package.

    Searches for annotatedyaml/_vendor/yaml/__init__.py (falling back to annotatedyaml/_vendor/yaml.py),
    extracts the `__version__` assignment, and returns it as a `packaging.version.Version`.

    Returns:
        Version: The parsed vendored PyYAML version.

    Raises:
        MonitoringError: If neither vendored module file is present, if a `__version__` assignment
            cannot be found, or if the extracted version string is not a valid version.
    """
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
    """Fetch the PyPI JSON metadata for the PyYAML project.

    Returns:
        data (dict[str, Any]): Decoded JSON object from PyPI containing release metadata (must include a "releases" key).

    Raises:
        MonitoringError: If the HTTP request fails or the response is missing the expected "releases" metadata.
    """
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
    """Selects the newest stable (non-prerelease, non-dev) release from PyPI metadata and returns its parsed version and associated file entries.

    Parameters:
        data (dict[str, Any]): Decoded PyPI JSON object expected to contain a "releases" mapping from version strings to lists of file metadata.

    Returns:
        tuple[Version | None, list[dict[str, Any]]]: A pair where the first element is the newest stable parsed `Version` (or `None` if none found) and the second element is the list of file entries for that release (empty if none).
    """
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
    """Convert a raw version string into a packaging Version, treating the literal "0" as Version("0") and returning None for absent or unparsable inputs.

    Parameters:
        raw (str | None): A version string to convert, or None.

    Returns:
        Version | None: A `Version` instance for valid inputs (including the literal `"0"`), or `None` if `raw` is `None` or cannot be parsed as a valid version.
    """
    if raw in (None, "0"):
        return Version("0") if raw == "0" else None
    try:
        return Version(raw)
    except InvalidVersion:
        return None


def _range_contains_version(events: list[dict[str, str]], version: Version) -> bool:
    """Determine whether a given version falls within any affected interval described by an OSV-style sequence of range events.

    The `events` list is a sequence of dictionaries representing range events with one of the keys: `introduced`, `fixed`, or `last_affected`. Intervals are interpreted as:
    - `introduced` followed by `fixed` => [introduced, fixed)
    - `introduced` followed by `last_affected` => [introduced, last_affected]
    If an `introduced` event has no subsequent `fixed` or `last_affected`, the interval is treated as open-ended (introduced ≤ version).

    Parameters:
        events (list[dict[str, str]]): OSV range event objects in chronological order.
        version (Version): The version to check against the described intervals.

    Returns:
        bool: `True` if `version` is contained in any described interval, `False` otherwise.
    """
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
    """Format OSV range events into human-readable interval segments.

    Converts a sequence of OSV `affected.ranges` events into interval strings such as
    `[introduced, fixed)` or `[introduced, last_affected]`. Multiple intervals are
    joined with `, `. Returns `None` when no complete interval segments can be
    produced.

    Parameters:
        events (list[dict[str, str]]): OSV range event objects containing keys
            like `"introduced"`, `"fixed"`, or `"last_affected"` mapping to version
            strings; events are processed in order.

    Returns:
        str | None: A comma-separated string of interval segments (for example
        `"[1.0, 1.2), [2.0, 2.1]"`), or `None` if no segments were produced.
    """
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
    """Normalize a raw severity label into a canonical severity name used by this module.

    Parameters:
        raw (str | None): Severity string to normalise (e.g., from OSV or database-specific fields).

    Returns:
        str | None: Uppercased canonical severity present in SEVERITY_ORDER (with "MODERATE" mapped to "MEDIUM"), or `None` if the input is empty, None, or not recognised.
    """
    if raw is None:
        return None
    normalised = raw.strip().upper()
    if not normalised:
        return None
    if normalised == "MODERATE":
        normalised = "MEDIUM"
    return normalised if normalised in SEVERITY_ORDER else None


def _severity_from_cvss(score: float) -> str:
    """Map a numeric CVSS score to an OSV severity label.

    Parameters:
        score (float): CVSS numeric score.

    Returns:
        severity (str): One of "CRITICAL", "HIGH", "MEDIUM", or "LOW" according to thresholds:
            - >= 9.0 => "CRITICAL"
            - >= 7.0 => "HIGH"
            - >= 4.0 => "MEDIUM"
            - > 0   => "LOW"
            - otherwise => "LOW"
    """
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
    """Determine the severity level for an OSV vulnerability entry.

    Checks `entry["database_specific"]["severity"]` first and returns a normalized value if present.
    If absent or invalid, extracts numeric CVSS scores from `entry["severity"]` (if any), uses the highest numeric score and maps it to a severity category. If no usable severity information is found, returns `"CRITICAL"`.

    Parameters:
        entry (dict[str, Any]): An OSV vulnerability entry (decoded JSON) to inspect.

    Returns:
        str: Severity name (`CRITICAL`, `HIGH`, `MEDIUM`, or `LOW`).
    """
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
    """Find OSV advisories that affect the provided vendored PyYAML version.

    Queries the OSV API for PyYAML and returns VulnerabilityRecord entries for advisories whose affected ranges include the given version.

    Returns:
        list[VulnerabilityRecord]: Vulnerabilities from OSV that still affect the vendored version; empty list if none.
    """
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
    """Locate the newest PyPI release that includes a non-yanked wheel whose filename contains the given python tag and platform fragment.

    Parameters:
        data (dict[str, Any]): Decoded PyPI JSON metadata for the package (expects a "releases" mapping).
        python_tag (str): Substring expected in the wheel filename identifying the Python ABI/tag (e.g., "cp313").
        platform_fragment (str): Substring expected in the wheel filename identifying the target platform (e.g., "manylinux").

    Returns:
        tuple[Version | None, str, str]: A tuple of (version, filename, url):
            - version: the newest stable release Version that has a matching wheel, or `None` if no match was found.
            - filename: the matching wheel filename, or an empty string if none found.
            - url: the file download URL for the matching wheel, or an empty string if none found.
    """
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
    """Build a Markdown-formatted GitHub Actions step summary describing the monitoring run.

    Parameters:
        result (MonitoringResult): Aggregated monitoring output containing the vendored
            version, latest PyPI release information, discovered wheel matches, and
            any affecting vulnerabilities.

    Returns:
        summary (str): A multi-line Markdown string suitable for a GitHub Actions
        step summary that reports the vendored PyYAML version, latest stable PyPI
        release, per-profile wheel availability hints, and a listed summary of any
        vulnerabilities that affect the vendored release.
    """
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
    """Serialize a MonitoringResult into a JSON-serializable mapping.

    Returns:
        mapping (dict): A JSON-serializable mapping with the following keys:
            - vendor_version (str): Vendored package version as a string.
            - latest_release (str | None): Latest stable PyPI release version string, or None if unavailable.
            - wheel_matches (list[dict]): List of wheel-match summaries, each with:
                - python_tag (str): Tracked Python tag for the profile.
                - platform_fragment (str): Tracked platform fragment for the profile.
                - release (str | None): PyPI release version string that provided the match, or None.
                - filename (str | None): Wheel filename, or None.
                - url (str | None): Wheel download URL, or None.
            - vulnerabilities (list[dict]): List of vulnerability summaries, each with:
                - identifier (str): OSV or advisory identifier.
                - summary (str): Short description of the vulnerability.
                - severity (str): Normalized severity string (e.g., "CRITICAL", "HIGH").
                - affected_version_range (str | None): Human-readable affected range, or None.
                - references (list[str]): List of reference URLs.
    """
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
    """Run the monitoring routine that inspects the vendored PyYAML, queries PyPI and OSV, checks wheel availability for configured profiles, and computes an exit code.

    Parameters:
        fail_on_outdated (bool): If true, consider the vendored release being older than the latest stable PyPI release a failure (sets the exit code to 1).
        fail_severity (str): Minimum OSV severity name (e.g., "HIGH") that triggers failure when an affecting advisory has equal or greater severity.
        wheel_profiles (list[WheelProfile]): Wheel profiles to probe on PyPI for compatible prebuilt wheels.

    Returns:
        tuple[MonitoringResult, int]: A MonitoringResult containing gathered metadata, vulnerabilities, and wheel matches, and an exit code (0 for success, 1 when policy conditions are met that require failure).
    """
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
    """Run the monitoring workflow for the vendored PyYAML and return an appropriate process exit code.

    Parses command-line arguments, performs the monitoring checks (vendored version extraction, PyPI and OSV queries, and wheel availability scans), emits a GitHub Actions step summary (appending to the file pointed to by GITHUB_STEP_SUMMARY when set, otherwise printing to stdout), and optionally writes a JSON metadata document when a metadata path is provided.

    Returns:
        int: Process exit code — `0` on success, `1` when checks fail due to configured thresholds (e.g., vulnerability or outdated failures), `2` when a monitoring error prevents completion.
    """
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
