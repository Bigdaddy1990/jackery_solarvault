"""Synchronise dependencies with the upstream Home Assistant repository."""

import argparse
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import tomllib

from packaging.requirements import Requirement
from packaging.specifiers import Specifier
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pip._vendor import requests
from scripts import check_vendor_pyyaml as vendor_monitor

# Paths relative to the Home Assistant core repository root.
HA_REQUIREMENTS = "requirements.txt"
HA_REQUIREMENTS_TEST = "requirements_test.txt"
HA_PACKAGE_CONSTRAINTS = "homeassistant/package_constraints.txt"
HA_PYPROJECT = "pyproject.toml"

VENDOR_ROOT = Path("annotatedyaml/_vendor")
VENDOR_LICENSE = VENDOR_ROOT / "PyYAML_LICENSE"
VENDOR_PACKAGE = VENDOR_ROOT / "yaml"
DEFAULT_METADATA_PATH = Path("generated/vendor_pyyaml_status.json")

# Default branch used when downloading reference files from GitHub.
DEFAULT_REFERENCE_URL = "https://raw.githubusercontent.com/home-assistant/core/dev"


@dataclass(frozen=True)
class ReferenceRequirement:
    """Reference requirement definition sourced from Home Assistant."""

    requirement: Requirement

    @property
    def specifier(self) -> str:  # noqa: D102
        return str(self.requirement.specifier)

    @property
    def marker(self) -> str | None:  # noqa: D102
        return str(self.requirement.marker) if self.requirement.marker else None

    @property
    def version_hint(self) -> Version | None:  # noqa: D102
        return highest_version(self.requirement.specifier)


class ReferenceLoader:
    """Load reference files from a local checkout or GitHub."""

    def __init__(self, *, root: Path | None, base_url: str) -> None:  # noqa: D107
        self._root = root
        self._base_url = base_url.rstrip("/")

    def load_text(self, relative: str) -> str:  # noqa: D102
        if self._root is not None:
            return (self._root / relative).read_text(encoding="utf-8")
        url = f"{self._base_url}/{relative}"
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        return response.text


class DependencySynchroniser:
    """Synchronise local dependency files against Home Assistant references."""

    def __init__(  # noqa: D107
        self,
        *,
        loader: ReferenceLoader,
        manifest_path: Path,
        requirements_files: Iterable[Path],
        metadata_path: Path,
    ) -> None:
        self._loader = loader
        self._manifest_path = manifest_path
        self._requirements_files = list(requirements_files)
        self._metadata_path = metadata_path

    def load_reference_requirements(self) -> dict[str, ReferenceRequirement]:
        """Return a mapping of canonical package names to reference specs."""
        mapping: dict[str, ReferenceRequirement] = {}
        for relative in (
            HA_REQUIREMENTS,
            HA_REQUIREMENTS_TEST,
            HA_PACKAGE_CONSTRAINTS,
        ):
            text = self._loader.load_text(relative)
            update_mapping_from_lines(mapping, text.splitlines())

        pyproject_text = self._loader.load_text(HA_PYPROJECT)
        project_data = tomllib.loads(pyproject_text)
        for requirement in project_data.get("project", {}).get("dependencies", []):
            update_mapping(mapping, Requirement(requirement))

        return mapping

    def update_manifest_requirements(
        self,
        *,
        reference: Mapping[str, ReferenceRequirement],
    ) -> bool:
        """Update manifest requirements to match Home Assistant versions."""
        data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        requirements: list[str] = data.get("requirements", [])
        updated = False
        new_requirements: list[str] = []
        for requirement_entry in requirements:
            requirement = Requirement(requirement_entry)
            canonical = canonicalize_name(requirement.name)
            reference_requirement = reference.get(canonical)
            if reference_requirement is None:
                new_requirements.append(requirement_entry)
                continue
            new_requirement = compose_requirement(requirement, reference_requirement)
            new_requirements.append(new_requirement)
            if new_requirement != requirement_entry:
                updated = True
        if updated:
            data["requirements"] = new_requirements
            self._manifest_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        return updated

    def update_requirement_files(
        self,
        *,
        reference: Mapping[str, ReferenceRequirement],
    ) -> bool:
        """Update local requirement files in place."""
        changed = False
        for path in self._requirements_files:
            file_changed = update_requirement_file(path, reference)
            changed = changed or file_changed
        return changed

    def sync_vendor_pyyaml(
        self,
        *,
        reference: Mapping[str, ReferenceRequirement],
        skip: bool,
        archive: Path | None,
    ) -> bool:
        """Synchronise the vendored PyYAML package with the reference version."""
        if skip:
            return False

        reference_requirement = reference.get("pyyaml")
        if reference_requirement is None or reference_requirement.version_hint is None:
            return False

        target_version = reference_requirement.version_hint
        try:
            current_version = vendor_monitor.load_vendor_version()
        except vendor_monitor.MonitoringError:
            current_version = None
        if current_version == target_version:
            return False

        if archive is None:
            archive = download_pyyaml_source(target_version)
        extract_pyyaml_archive(archive, target_version)
        return True

    def update_vendor_metadata(self) -> None:
        """Regenerate the vendored PyYAML metadata snapshot."""
        result, _ = vendor_monitor.evaluate(
            fail_on_outdated=False,
            fail_severity="HIGH",
            wheel_profiles=list(vendor_monitor.DEFAULT_WHEEL_PROFILES),
        )
        metadata = vendor_monitor.build_metadata_document(result)
        self._metadata_path.parent.mkdir(parents=True, exist_ok=True)
        self._metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def update_mapping(
    mapping: dict[str, ReferenceRequirement], requirement: Requirement
) -> None:
    """Insert or replace a requirement in the mapping based on specificity."""
    canonical = canonicalize_name(requirement.name)
    existing = mapping.get(canonical)
    candidate = ReferenceRequirement(requirement=requirement)
    if existing is None:
        mapping[canonical] = candidate
        return

    if requirement_priority(candidate.requirement) > requirement_priority(
        existing.requirement
    ):
        mapping[canonical] = candidate
        return

    existing_version = existing.version_hint
    candidate_version = candidate.version_hint
    if existing_version is None:
        if candidate_version is not None:
            mapping[canonical] = candidate
        return
    if candidate_version is None:
        return
    if candidate_version > existing_version:
        mapping[canonical] = candidate


def update_mapping_from_lines(
    mapping: dict[str, ReferenceRequirement], lines: Iterable[str]
) -> None:
    """Update the mapping with requirement lines from a text file."""
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r"):
            continue
        try:
            requirement = Requirement(line)
        except Exception:
            continue
        update_mapping(mapping, requirement)


def highest_version(specifier_set: Iterable[Specifier]) -> Version | None:
    """Return the highest version referenced by the specifier set."""
    highest: Version | None = None
    for specifier in specifier_set:
        try:
            version = Version(specifier.version)
        except InvalidVersion, TypeError:
            continue
        if highest is None or version > highest:
            highest = version
    return highest


def requirement_priority(requirement: Requirement) -> int:
    """Return a numeric priority score for a requirement specifier."""
    if not requirement.specifier:
        return 0
    priority = 0
    for specifier in requirement.specifier:
        if specifier.operator in {"==", "==="}:
            priority = max(priority, 4)
        elif specifier.operator == ">=":
            priority = max(priority, 3)
        elif specifier.operator == "~=":
            priority = max(priority, 2)
        else:
            priority = max(priority, 1)
    return priority


def compose_requirement(
    requirement: Requirement, reference: ReferenceRequirement
) -> str:
    """Compose a requirement string using the reference specifier."""
    extras = sorted(requirement.extras)
    pieces: list[str] = [requirement.name]
    if extras:
        pieces[0] += f"[{','.join(extras)}]"
    specifier = reference.specifier or str(requirement.specifier)
    if specifier:
        pieces[0] += specifier
    marker = reference.marker or (str(requirement.marker) if requirement.marker else "")
    if marker:
        pieces[0] += f"; {marker}"
    return pieces[0]


def split_comment(line: str) -> tuple[str, str]:
    """Split a requirement line into requirement text and trailing comment."""
    hash_index = line.find("#")
    if hash_index == -1:
        return line.rstrip(), ""
    return line[:hash_index].rstrip(), line[hash_index:]


def update_requirement_file(
    path: Path, reference: Mapping[str, ReferenceRequirement]
) -> bool:
    """Rewrite a requirements file based on the reference mapping."""
    lines = path.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    changed = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-r")):
            new_lines.append(line)
            continue
        requirement_part, comment = split_comment(line)
        try:
            requirement = Requirement(requirement_part)
        except Exception:
            new_lines.append(line)
            continue
        canonical = canonicalize_name(requirement.name)
        reference_requirement = reference.get(canonical)
        if reference_requirement is None:
            new_lines.append(line)
            continue
        updated_requirement = compose_requirement(requirement, reference_requirement)
        if comment:
            updated_requirement = f"{updated_requirement}  {comment.strip()}"
        if updated_requirement != line:
            changed = True
        new_lines.append(updated_requirement)
    if changed:
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return changed


def download_pyyaml_source(version: Version) -> Path:
    """Download the PyYAML source distribution for the given version."""
    metadata = vendor_monitor.fetch_pypi_metadata()
    release_files = metadata.get("releases", {}).get(str(version), [])
    for entry in release_files:
        if entry.get("packagetype") != "sdist":
            continue
        url = entry.get("url")
        if not url:
            continue
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        suffix = ".tar.gz" if not url.endswith(".zip") else ".zip"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(response.content)
            return Path(handle.name)
    raise RuntimeError(f"Unable to locate PyYAML {version} source distribution")


def _safe_tar_members(handle: tarfile.TarFile) -> Iterable[tarfile.TarInfo]:
    """Yield safe tar members, rejecting traversal and special file entries."""
    for member in handle.getmembers():
        member_path = PurePosixPath(member.name)
        if member_path.is_absolute() or any(part == ".." for part in member_path.parts):
            raise RuntimeError(
                f"Refusing to extract unsafe archive member: {member.name}",
            )
        if not (member.isfile() or member.isdir()):
            continue
        yield member


def extract_pyyaml_archive(archive: Path, version: Version) -> None:
    """Extract a PyYAML archive into the vendored directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        with tarfile.open(archive, "r:gz") as handle:
            handle.extractall(
                path=temp_dir,
                members=_safe_tar_members(handle),
                filter="data",
            )
        candidates = list(Path(temp_dir).glob("PyYAML-*")) + list(
            Path(temp_dir).glob("pyyaml-*")
        )
        if not candidates:
            raise RuntimeError("PyYAML archive did not contain an expected root folder")
        extracted_root = candidates[0]
        yaml_source = extracted_root / "lib" / "yaml"
        if not yaml_source.exists():
            yaml_source = extracted_root / "yaml"
        if not yaml_source.exists():
            raise RuntimeError("PyYAML archive did not contain the yaml package")
        if VENDOR_PACKAGE.exists():
            shutil.rmtree(VENDOR_PACKAGE)
        shutil.copytree(yaml_source, VENDOR_PACKAGE)
        license_file = extracted_root / "LICENSE"
        if license_file.exists():
            shutil.copyfile(license_file, VENDOR_LICENSE)
    archive.unlink(missing_ok=True)


def parse_arguments() -> argparse.Namespace:
    """Parse CLI arguments for the synchronisation routine."""
    parser = argparse.ArgumentParser(
        description="Synchronise local dependency versions with Home Assistant core",
    )
    parser.add_argument(
        "--home-assistant-root",
        type=Path,
        default=None,
        help="Optional path to a Home Assistant core checkout for offline syncing.",
    )
    parser.add_argument(
        "--reference-url",
        default=DEFAULT_REFERENCE_URL,
        help="Base URL for fetching Home Assistant reference files.",
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=Path("custom_components/jackery_solarvault/manifest.json"),
        help="Path to the integration manifest to update.",
    )
    parser.add_argument(
        "--requirements-file",
        action="append",
        type=Path,
        default=[],
        help="Additional requirements files to synchronise (repeatable).",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=DEFAULT_METADATA_PATH,
        help="Path for the generated vendor metadata snapshot.",
    )
    parser.add_argument(
        "--skip-pyyaml-sync",
        action="store_true",
        help="Skip synchronising the vendored PyYAML package.",
    )
    parser.add_argument(
        "--pyyaml-archive",
        type=Path,
        default=None,
        help="Use a pre-downloaded PyYAML source archive instead of downloading via pip.",
    )
    parser.add_argument(
        "--skip-pyyaml-metadata",
        action="store_true",
        help="Skip regenerating the vendor PyYAML status metadata.",
    )
    return parser.parse_args()


def main() -> int:
    """CLI entry point for the dependency synchroniser."""
    args = parse_arguments()
    requirements_files = args.requirements_file or [
        Path("requirements.txt"),
        Path("requirements_test.txt"),
    ]

    loader = ReferenceLoader(root=args.home_assistant_root, base_url=args.reference_url)
    synchroniser = DependencySynchroniser(
        loader=loader,
        manifest_path=args.manifest_path,
        requirements_files=requirements_files,
        metadata_path=args.metadata_path,
    )
    reference_map = synchroniser.load_reference_requirements()

    manifest_changed = synchroniser.update_manifest_requirements(
        reference=reference_map
    )
    requirements_changed = synchroniser.update_requirement_files(
        reference=reference_map
    )
    vendor_changed = synchroniser.sync_vendor_pyyaml(
        reference=reference_map,
        skip=args.skip_pyyaml_sync,
        archive=args.pyyaml_archive,
    )
    if not args.skip_pyyaml_metadata:
        synchroniser.update_vendor_metadata()

    if any((manifest_changed, requirements_changed, vendor_changed)):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
