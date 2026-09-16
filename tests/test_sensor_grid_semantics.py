"""Regression tests for grid import/export vs inverter output semantics (B5).

Wire proof from ``logs/jackery_solarvault_payload_debug.jsonl`` (SystemBody
frames, 2026-06-25): ``otherLoadPw = gridOutPw - outGridSidePw +
inGridSidePw`` holds in every sample, so ``gridOutPw`` (== HomeBody
``outOngridPw``) is the inverter's total AC output (house share + export),
NOT the grid export. The real export/import measurement points are
``outGridSidePw``/``inGridSidePw`` (SystemBody only). The historical bug
surfaced the inverter output as "grid export", duplicating ~800 W as
export AND house load at the same time.
"""

from typing import TYPE_CHECKING, Any

from custom_components.jackery_solarvault.const import (
    FIELD_GRID_IN_PW,
    FIELD_GRID_OUT_PW,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_IN_ONGRID_PW,
    FIELD_OTHER_LOAD_PW,
    FIELD_OUT_GRID_SIDE_PW,
    FIELD_OUT_ONGRID_PW,
)
from custom_components.jackery_solarvault.sensor import SENSOR_DESCRIPTIONS
from custom_components.jackery_solarvault.util import jackery_grid_net_power

if TYPE_CHECKING:
    from collections.abc import Callable

_INVERTER_OUTPUT_W = 1094
_HOUSE_LOAD_W = 972
_TRUE_EXPORT_W = 122
_HOUSE_ONLY_OUTPUT_W = 950
_NIGHT_IMPORT_W = 344
_HOMEBODY_OUTPUT_W = 1095
_GRID_IN_ONLY_W = 250
_ONGRID_IN_ONLY_W = 180

# Real SystemBody frame 2026-06-25T18:28:02: inverter runs at 1094 W,
# house consumes 972 W, true export is 122 W.
_WIRE_EXPORTING: dict[str, Any] = {
    FIELD_GRID_OUT_PW: _INVERTER_OUTPUT_W,
    FIELD_OTHER_LOAD_PW: _HOUSE_LOAD_W,
    FIELD_OUT_GRID_SIDE_PW: _TRUE_EXPORT_W,
    FIELD_IN_GRID_SIDE_PW: 0,
    FIELD_GRID_IN_PW: 0,
}
# Real duplicate case (19:01:33): the full inverter output feeds the
# house, nothing is exported — the old sensor showed 950 W "export".
_WIRE_HOUSE_ONLY: dict[str, Any] = {
    FIELD_GRID_OUT_PW: _HOUSE_ONLY_OUTPUT_W,
    FIELD_OTHER_LOAD_PW: _HOUSE_ONLY_OUTPUT_W,
    FIELD_OUT_GRID_SIDE_PW: 0,
    FIELD_IN_GRID_SIDE_PW: 0,
    FIELD_GRID_IN_PW: 0,
}
# Night frame (2026-07-03 diagnostics): house load fully imported.
_WIRE_NIGHT_IMPORT: dict[str, Any] = {
    FIELD_GRID_OUT_PW: 0,
    FIELD_OTHER_LOAD_PW: _NIGHT_IMPORT_W,
    FIELD_OUT_GRID_SIDE_PW: 0,
    FIELD_IN_GRID_SIDE_PW: _NIGHT_IMPORT_W,
    FIELD_GRID_IN_PW: 0,
}
# HomeBody devices report only the on-grid (inverter) field family and
# have no grid-side measurement point at all.
_WIRE_HOMEBODY: dict[str, Any] = {
    FIELD_IN_ONGRID_PW: 0,
    FIELD_OUT_ONGRID_PW: _HOMEBODY_OUTPUT_W,
}


def _getter(key: str) -> Callable[[dict[str, Any]], object]:
    """Return the property getter of a SENSOR_DESCRIPTIONS entry."""
    return next(
        description for description in SENSOR_DESCRIPTIONS if description.key == key
    ).getter


def test_wire_samples_satisfy_the_systembody_power_identity() -> None:
    """Documented wire identity: otherLoadPw = gridOutPw - export + import."""
    for frame in (_WIRE_EXPORTING, _WIRE_HOUSE_ONLY, _WIRE_NIGHT_IMPORT):
        assert frame[FIELD_OTHER_LOAD_PW] == (
            frame[FIELD_GRID_OUT_PW]
            - frame[FIELD_OUT_GRID_SIDE_PW]
            + frame[FIELD_IN_GRID_SIDE_PW]
        )


def test_grid_out_power_reports_the_true_export_only() -> None:
    """``grid_out_power`` must read ``outGridSidePw``, never the inverter output."""
    getter = _getter("grid_out_power")

    assert getter(_WIRE_EXPORTING) == _TRUE_EXPORT_W
    assert getter(_WIRE_HOUSE_ONLY) == 0
    assert getter(_WIRE_NIGHT_IMPORT) == 0


def test_grid_in_power_reports_the_true_import_only() -> None:
    """``grid_in_power`` must read ``inGridSidePw``, never the inverter input."""
    getter = _getter("grid_in_power")

    assert getter(_WIRE_NIGHT_IMPORT) == _NIGHT_IMPORT_W
    assert getter(_WIRE_EXPORTING) == 0


def test_grid_sensors_stay_unknown_without_a_grid_side_measurement() -> None:
    """HomeBody frames have no export/import measurement point.

    Falling back to the on-grid (inverter) family here recreates the
    duplicate-export bug, so both sensors must report no value.
    """
    assert _getter("grid_out_power")(_WIRE_HOMEBODY) is None
    assert _getter("grid_in_power")(_WIRE_HOMEBODY) is None


def test_inverter_ac_output_power_exposes_the_former_reading() -> None:
    """The inverter AC output keeps the gridOutPw/outOngridPw signal visible."""
    getter = _getter("inverter_ac_output_power")

    assert getter(_WIRE_EXPORTING) == _INVERTER_OUTPUT_W
    assert getter(_WIRE_HOUSE_ONLY) == _HOUSE_ONLY_OUTPUT_W
    assert getter(_WIRE_HOMEBODY) == _HOMEBODY_OUTPUT_W


def test_inverter_ac_input_power_reads_the_inverter_family() -> None:
    """The inverter AC input mirrors gridInPw/inOngridPw, not the grid import."""
    getter = _getter("inverter_ac_input_power")

    assert getter(_WIRE_NIGHT_IMPORT) == 0
    assert getter({FIELD_GRID_IN_PW: _GRID_IN_ONLY_W}) == _GRID_IN_ONLY_W
    assert getter({FIELD_IN_ONGRID_PW: _ONGRID_IN_ONLY_W}) == _ONGRID_IN_ONLY_W


def test_grid_net_power_uses_pure_grid_side_fields() -> None:
    """Net grid power derives from the true import/export fields only.

    The historical helper preferred non-zero inverter-family values, so a
    house-only frame rendered as -950 W "Netzabgabe" while the real
    export was 0.
    """
    assert jackery_grid_net_power(_WIRE_EXPORTING) == -_TRUE_EXPORT_W
    assert jackery_grid_net_power(_WIRE_HOUSE_ONLY) == 0
    assert jackery_grid_net_power(_WIRE_NIGHT_IMPORT) == _NIGHT_IMPORT_W
    assert jackery_grid_net_power(_WIRE_HOMEBODY) is None
