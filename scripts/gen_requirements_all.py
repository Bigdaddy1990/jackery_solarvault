"""Minimal implementation of Home Assistant's requirement tooling for tests."""

from collections.abc import Mapping

EXCLUDED_REQUIREMENTS_ALL = {
    "env-canada",
    "pytest",
}

INCLUDED_REQUIREMENTS_WHEELS = {
    "pytest",
}

OVERRIDDEN_REQUIREMENTS_ACTIONS: dict[str, Mapping[str, Mapping[str, set[str]]]] = {
    "env-canada": {
        "exclude": {"env-canada"},
        "include": {"env-canada"},
        "markers": {"env-canada": "python_version<'3.13'"},
    }
}


def _normalize_package_name(name: str) -> str:
    """Return a normalized package name following pip's canonical form."""
    return name.replace("_", "-").lower()


def process_action_requirement(requirement: str, integration: str) -> str:
    """Apply override markers for a given requirement if configured."""
    overrides = OVERRIDDEN_REQUIREMENTS_ACTIONS.get(integration)
    if not overrides:
        return requirement

    package = requirement.split(";", 1)[0].split("==", 1)[0]
    markers = overrides.get("markers", {})
    marker = markers.get(package)
    if not marker:
        return requirement

    return f"{requirement};{marker}"
