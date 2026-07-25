"""Regression: every services.yaml selector must satisfy HA's selector schema.

hassfest does not validate ``NumberSelector`` step bounds, so a step below HA's
``1e-3`` minimum (e.g. ``0.000001`` on the ``set_storm_alert_location``
latitude/longitude fields) passed hassfest yet broke service registration at
runtime with ``not a valid value for dictionary value @
data['set_storm_alert_location']['fields']['latitude']['selector']['step']``.

This loads ``services.yaml`` and runs every declared selector through
:func:`homeassistant.helpers.selector.selector`, which is the exact validation
Home Assistant performs when it registers the integration's services.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

import voluptuous as vol
import yaml

from homeassistant.helpers import selector as sel

if TYPE_CHECKING:
    from collections.abc import Iterator

_SERVICES_YAML = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "jackery_solarvault"
    / "services.yaml"
)


def _iter_selectors(service: str, fields: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    """Yield ``(dotted_field, selector_config)`` for every field with a selector."""
    for field_name, field_spec in (fields or {}).items():
        if not isinstance(field_spec, dict):
            continue
        if "fields" in field_spec:
            yield from _iter_selectors(f"{service}.{field_name}", field_spec["fields"])
        if field_spec.get("selector") is not None:
            yield f"{service}.{field_name}", field_spec["selector"]


def _all_selectors() -> Iterator[tuple[str, Any]]:
    data = yaml.safe_load(_SERVICES_YAML.read_text(encoding="utf-8")) or {}
    for service, spec in data.items():
        if isinstance(spec, dict):
            yield from _iter_selectors(service, spec.get("fields") or {})


def test_all_services_yaml_selectors_load_under_ha_schema() -> None:
    """Every services.yaml selector validates against HA's selector schema."""
    failures: list[str] = []
    for field, config in _all_selectors():
        try:
            sel.selector(config)
        except vol.Invalid as err:
            failures.append(f"{field}: {err}")

    assert not failures, "Invalid services.yaml selectors:\n" + "\n".join(failures)
