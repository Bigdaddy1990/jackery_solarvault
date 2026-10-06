"""Keep runtime requirements compatible with supported Home Assistant pins."""

from importlib.metadata import requires
from importlib.resources import files
import json
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
import pytest
from scripts.sync_ha_test_baseline import check_baseline

from homeassistant.loader import async_get_integration

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("baseline", ["baseline", "installed"])
def test_manifest_accepts_home_assistant_core_pins(baseline: str) -> None:
    """A test environment must not hide a Core runtime installation conflict."""
    manifest = json.loads(
        (_ROOT / "custom_components/jackery_solarvault/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    requirements: dict[str, Requirement] = {}
    for value in manifest["requirements"]:
        requirement = Requirement(value)
        marker = requirement.marker
        if marker is None or marker.evaluate():
            requirements[canonicalize_name(requirement.name)] = requirement
    baseline_version = (_ROOT / ".HA_VERSION").read_text(encoding="utf-8").strip()
    constraints = (
        files("homeassistant")
        .joinpath("package_constraints.txt")
        .read_text(encoding="utf-8")
        if baseline == "installed"
        else (
            _ROOT / "tests/fixtures" / f"ha-{baseline_version}-package-constraints.txt"
        ).read_text(encoding="utf-8")
    )
    conflicts: list[str] = []
    for line in constraints.splitlines():
        value = line.partition("#")[0].strip()
        if not value:
            continue
        constraint = Requirement(value)
        runtime_requirement = requirements.get(canonicalize_name(constraint.name))
        if runtime_requirement is None or (
            constraint.marker is not None and not constraint.marker.evaluate()
        ):
            continue
        conflicts.extend(
            f"{runtime_requirement} conflicts with {constraint}"
            for pin in constraint.specifier
            if pin.operator == "=="
            and "*" not in pin.version
            and not runtime_requirement.specifier.contains(
                pin.version, prereleases=True
            )
        )
    assert not conflicts, f"Home Assistant {baseline}: " + "; ".join(conflicts)


@pytest.mark.parametrize("field", ["dependencies", "after_dependencies"])
async def test_manifest_dependencies_exist_in_home_assistant(
    hass: HomeAssistant, field: str
) -> None:
    """Optional ordering dependencies must name real Home Assistant integrations."""
    manifest = json.loads(
        (_ROOT / "custom_components/jackery_solarvault/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    for domain in manifest.get(field, []):
        integration = await async_get_integration(hass, domain)
        assert integration.domain == domain


def test_manifest_does_not_redeclare_home_assistant_core_requirements() -> None:
    """Core-owned libraries must remain managed by Home Assistant itself."""
    manifest = json.loads(
        (_ROOT / "custom_components/jackery_solarvault/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    core_requirements: dict[str, Requirement] = {}
    for value in requires("homeassistant") or []:
        requirement = Requirement(value)
        marker = requirement.marker
        if marker is None or marker.evaluate():
            core_requirements[canonicalize_name(requirement.name)] = requirement
    declared_names = {
        canonicalize_name(Requirement(value).name) for value in manifest["requirements"]
    }
    duplicates = declared_names.intersection(core_requirements)
    assert not duplicates, f"Requirements already managed by HA Core: {duplicates}"


def test_optional_network_features_keep_their_core_domains() -> None:
    """Keep mDNS discovery and HA frontend WebSocket support optional."""
    manifest = json.loads(
        (_ROOT / "custom_components/jackery_solarvault/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert {"zeroconf", "websocket_api"} <= set(manifest["after_dependencies"])
    assert not {"zeroconf", "websocket_api"}.intersection(
        manifest.get("dependencies", [])
    )


def test_hacs_does_not_duplicate_the_pytest_plugin_baseline() -> None:
    """HACS must leave the HA version to the pytest plugin stack."""
    hacs = json.loads((_ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert "homeassistant" not in hacs


def test_ha_baseline_matches_pytest_plugin_metadata() -> None:
    """The installed pytest plugin determines the tested HA release."""
    assert check_baseline(_ROOT) == []
