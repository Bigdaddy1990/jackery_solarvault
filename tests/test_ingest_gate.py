"""Unit tests for the transport-neutral ingest gate."""

import importlib.util
from pathlib import Path
import sys
import types


def _load_ingest_module() -> types.ModuleType:
    package_dir = (
        Path(__file__).resolve().parents[1] / "custom_components" / "jackery_solarvault"
    )
    sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
    package = types.ModuleType("custom_components.jackery_solarvault")
    package.__path__ = [str(package_dir)]
    sys.modules.setdefault("custom_components.jackery_solarvault", package)

    const_spec = importlib.util.spec_from_file_location(
        "custom_components.jackery_solarvault.const",
        package_dir / "const.py",
    )
    assert const_spec is not None
    const_module = importlib.util.module_from_spec(const_spec)
    sys.modules[const_spec.name] = const_module
    assert const_spec.loader is not None
    const_spec.loader.exec_module(const_module)

    client_name = "custom_components.jackery_solarvault.client"
    client_package = types.ModuleType(client_name)
    client_package.__path__ = [str(package_dir / "client")]
    sys.modules.setdefault(client_name, client_package)
    ingest_name = "custom_components.jackery_solarvault.client.ingest"
    ingest_package = types.ModuleType(ingest_name)
    ingest_package.__path__ = [str(package_dir / "client" / "ingest")]
    sys.modules.setdefault(ingest_name, ingest_package)

    spec = importlib.util.spec_from_file_location(
        "custom_components.jackery_solarvault.client.ingest.ingest",
        package_dir / "client" / "ingest" / "ingest.py",
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ingest = _load_ingest_module()
const = sys.modules["custom_components.jackery_solarvault.const"]


def test_periodic_zero_payload_without_samples_is_filtered() -> None:
    """Cloud success with zero totals but no samples is treated as no data."""
    payload = {
        const.APP_STAT_TOTAL_CT_INPUT_ENERGY: "0",
        const.APP_STAT_TOTAL_CT_OUTPUT_ENERGY: "0",
        const.APP_STAT_UNIT: "kWh",
        const.APP_CHART_SERIES_Y: [],
        const.APP_CHART_SERIES_Y2: [],
    }

    assert (
        ingest.gate_payload_section(
            ingest.TransportSource.HTTP,
            f"{const.APP_SECTION_CT_STAT}_{const.DATE_TYPE_DAY}",
            payload,
        )
        == {}
    )


def test_today_energy_all_zero_payload_is_filtered_as_unconfirmed() -> None:
    """The compact today endpoint must not publish unverified all-zero KPIs."""
    payload = {"de": 0, "dg": 0, "dh": 0, "ds": 0}

    assert (
        ingest.gate_payload_section(
            ingest.TransportSource.HTTP,
            const.APP_SECTION_TODAY_ENERGY,
            payload,
        )
        == {}
    )


def test_periodic_zero_payload_with_samples_is_kept() -> None:
    """A populated period series can legitimately confirm a zero total."""
    payload = {
        const.APP_STAT_TOTAL_CT_INPUT_ENERGY: "0",
        const.APP_STAT_TOTAL_CT_OUTPUT_ENERGY: "0",
        const.APP_STAT_UNIT: "kWh",
        const.APP_CHART_SERIES_Y: [0.0, 0.0, 0.0],
        const.APP_CHART_SERIES_Y2: [0.0, 0.0, 0.0],
    }

    assert (
        ingest.gate_payload_section(
            ingest.TransportSource.HTTP,
            f"{const.APP_SECTION_CT_STAT}_{const.DATE_TYPE_DAY}",
            payload,
        )
        == payload
    )


def test_hierarchy_gate_drops_only_violating_period_section() -> None:
    """A period section exceeding its container is withheld from the recorder."""
    week_section = f"{const.APP_SECTION_PV_STAT}_{const.DATE_TYPE_WEEK}"
    year_section = f"{const.APP_SECTION_PV_STAT}_{const.DATE_TYPE_YEAR}"
    payload = {
        week_section: {const.APP_STAT_TOTAL_SOLAR_ENERGY: 99.0},
        year_section: {const.APP_STAT_TOTAL_SOLAR_ENERGY: 12.0},
        const.PAYLOAD_PROPERTIES: {"foo": 1},
    }

    gated = ingest.gate_period_hierarchy_for_recorder(
        payload,
        frozenset({week_section}),
    )

    assert week_section not in gated
    assert gated[year_section] == {const.APP_STAT_TOTAL_SOLAR_ENERGY: 12.0}
    assert gated[const.PAYLOAD_PROPERTIES] == {"foo": 1}
    # Source payload must not be mutated by the gate.
    assert week_section in payload


def test_hierarchy_gate_without_violations_returns_unchanged_copy() -> None:
    """An empty violation set yields an equal but independent payload copy."""
    payload = {
        f"{const.APP_SECTION_PV_STAT}_{const.DATE_TYPE_DAY}": {
            const.APP_STAT_TOTAL_SOLAR_ENERGY: 5.0
        },
    }

    gated = ingest.gate_period_hierarchy_for_recorder(payload, frozenset())

    assert gated == payload
    assert gated is not payload
