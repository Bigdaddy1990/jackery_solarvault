"""App-compatible CT phase naming without changing stable entity keys."""

import json
from pathlib import Path

from custom_components.jackery_solarvault.select import (
    _CT_PHASE_TO_OPTION,
    _OPTION_TO_CT_PHASE,
)

_COMPONENT = Path(__file__).parents[1] / "custom_components" / "jackery_solarvault"


def _translation(filename: str) -> dict:
    return json.loads((_COMPONENT / filename).read_text(encoding="utf-8"))


def test_ct_phase_select_keeps_stable_options_and_accepts_app_aliases() -> None:
    """App A/B/C/combined aliases map to schePhase 1/2/3/4."""
    assert _CT_PHASE_TO_OPTION == {
        1: "phase_1",
        2: "phase_2",
        3: "phase_3",
        4: "combined_phases",
    }
    assert _OPTION_TO_CT_PHASE["phase_a"] == 1
    assert _OPTION_TO_CT_PHASE["phase_b"] == 2
    assert _OPTION_TO_CT_PHASE["phase_c"] == 3
    assert _OPTION_TO_CT_PHASE["combined_phase"] == 4
    assert _OPTION_TO_CT_PHASE["phase_t"] == 4


def test_ct_phase_ui_uses_app_a_b_c_and_total_t_labels() -> None:
    """Canonical strings expose the App phase letters and CT total T."""
    strings = _translation("strings.json")
    select_states = strings["entity"]["select"]["ct_phase_select"]["state"]
    assert select_states["phase_1"] == "Phase A"
    assert select_states["phase_2"] == "Phase B"
    assert select_states["phase_3"] == "Phase C"
    assert select_states["combined_phases"] == "Combined phase"

    sensors = strings["entity"]["sensor"]
    assert sensors["smart_meter_phase_1_power"]["name"] == "CT phase A power"
    assert sensors["smart_meter_phase_2_power"]["name"] == "CT phase B power"
    assert sensors["smart_meter_phase_3_power"]["name"] == "CT phase C power"
    assert sensors["smart_meter_power"]["name"] == "CT phase T power"


def test_german_ct_phase_ui_uses_a_b_c_and_total_t_labels() -> None:
    """German UI mirrors the App phase-letter terminology."""
    german = _translation("translations/de.json")
    select_states = german["entity"]["select"]["ct_phase_select"]["state"]
    assert select_states["phase_1"] == "Phase A"
    assert select_states["phase_2"] == "Phase B"
    assert select_states["phase_3"] == "Phase C"
    assert select_states["combined_phases"] == "Kombinierte Phase"

    sensors = german["entity"]["sensor"]
    assert sensors["smart_meter_phase_1_power"]["name"] == "CT-Phase A Leistung"
    assert sensors["smart_meter_phase_2_power"]["name"] == "CT-Phase B Leistung"
    assert sensors["smart_meter_phase_3_power"]["name"] == "CT-Phase C Leistung"
    assert sensors["smart_meter_power"]["name"] == "CT-Phase T Leistung"
