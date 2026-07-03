"""Regression tests for Jackery power-flow helper semantics."""

from custom_components.jackery_solarvault.const import (
    FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER,
    FIELD_CT_TOTAL_PHASE_POWER,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_OTHER_LOAD_PW,
    FIELD_OUT_GRID_SIDE_PW,
)
from custom_components.jackery_solarvault.util import (
    jackery_corrected_home_consumption_power,
)

_REPORTED_OTHER_LOAD = 123
_METER_IMPORT = 500
_JACKERY_INPUT = 40
_JACKERY_OUTPUT = 700
_FALLBACK_HOME_LOAD = 1160
_METER_EXPORT = 800
_HIGH_JACKERY_INPUT = 400


def test_home_consumption_prefers_reported_other_load_power() -> None:
    """SystemBody otherLoadPw is the app's direct home-load signal."""
    result = jackery_corrected_home_consumption_power(
        {
            FIELD_CT_TOTAL_PHASE_POWER: _METER_IMPORT,
            FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER: 0,
        },
        {
            FIELD_OTHER_LOAD_PW: _REPORTED_OTHER_LOAD,
            FIELD_IN_GRID_SIDE_PW: _JACKERY_INPUT,
            FIELD_OUT_GRID_SIDE_PW: _JACKERY_OUTPUT,
        },
    )

    assert result is not None
    assert result.value == _REPORTED_OTHER_LOAD
    assert result.source == FIELD_OTHER_LOAD_PW


def test_home_consumption_fallback_uses_meter_net_minus_input_plus_output() -> None:
    """When otherLoadPw is absent, use the documented CT/Jackery correction."""
    result = jackery_corrected_home_consumption_power(
        {
            FIELD_CT_TOTAL_PHASE_POWER: _METER_IMPORT,
            FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER: 0,
        },
        {
            FIELD_IN_GRID_SIDE_PW: _JACKERY_INPUT,
            FIELD_OUT_GRID_SIDE_PW: _JACKERY_OUTPUT,
        },
    )

    assert result is not None
    assert result.value == _FALLBACK_HOME_LOAD
    assert result.smart_meter_net_power == _METER_IMPORT
    assert result.jackery_input_power == _JACKERY_INPUT
    assert result.jackery_output_power == _JACKERY_OUTPUT
    assert result.source == "smart_meter_net_minus_input_plus_output"


def test_home_consumption_fallback_clamps_negative_result() -> None:
    """The fallback never reports negative home consumption."""
    result = jackery_corrected_home_consumption_power(
        {
            FIELD_CT_TOTAL_PHASE_POWER: 0,
            FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER: _METER_EXPORT,
        },
        {
            FIELD_IN_GRID_SIDE_PW: _HIGH_JACKERY_INPUT,
            FIELD_OUT_GRID_SIDE_PW: 0,
        },
    )

    assert result is not None
    assert result.value == 0
