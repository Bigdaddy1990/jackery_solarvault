"""Unit tests for zero-guard fix in trend_series helpers.

These tests verify that zero values are accepted for CT/EPS stat sections
where the device legitimately reports zero energy for a period.
"""


from custom_components.jackery_solarvault import util
from custom_components.jackery_solarvault.const import (
    APP_CHART_SERIES_Y,
    APP_CHART_SERIES_Y1,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_STAT_TOTAL_CT_INPUT_ENERGY,
    APP_STAT_TOTAL_IN_EPS_ENERGY,
    APP_STAT_TOTAL_OUT_EPS_ENERGY,
    APP_STAT_UNIT,
    APP_UNIT_KWH,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
)


def test_trend_series_total_ct_day_zero_accepted() -> None:
    """CT day period with zero total should return 0.0, not None."""
    section = f"{APP_SECTION_CT_STAT}_{DATE_TYPE_DAY}"
    source = {APP_STAT_TOTAL_CT_INPUT_ENERGY: "0"}
    assert util.trend_series_total(source, section, APP_STAT_TOTAL_CT_INPUT_ENERGY) == 0.0


def test_trend_series_total_ct_day_negative_dropped() -> None:
    """CT day period with negative total should return None."""
    section = f"{APP_SECTION_CT_STAT}_{DATE_TYPE_DAY}"
    source = {APP_STAT_TOTAL_CT_INPUT_ENERGY: "-1.5"}
    assert util.trend_series_total(source, section, APP_STAT_TOTAL_CT_INPUT_ENERGY) is None


def test_trend_series_total_eps_day_zero_accepted() -> None:
    """EPS day period with zero total should return 0.0, not None."""
    section = f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}"
    source = {APP_STAT_TOTAL_IN_EPS_ENERGY: "0"}
    assert util.trend_series_total(source, section, APP_STAT_TOTAL_IN_EPS_ENERGY) == 0.0


def test_trend_series_total_eps_day_negative_dropped() -> None:
    """EPS day period with negative total should return None."""
    section = f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}"
    source = {APP_STAT_TOTAL_IN_EPS_ENERGY: "-0.5"}
    assert util.trend_series_total(source, section, APP_STAT_TOTAL_IN_EPS_ENERGY) is None


def test_trend_series_total_ct_month_zero_accepted() -> None:
    """CT month period with zero total should return 0.0, not None."""
    section = f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}"
    source = {APP_STAT_TOTAL_CT_INPUT_ENERGY: "0"}
    assert util.trend_series_total(source, section, APP_STAT_TOTAL_CT_INPUT_ENERGY) == 0.0


def test_trend_series_total_ct_month_negative_dropped() -> None:
    """CT month period with negative total should return None."""
    section = f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}"
    source = {APP_STAT_TOTAL_CT_INPUT_ENERGY: "-1.5"}
    assert util.trend_series_total(source, section, APP_STAT_TOTAL_CT_INPUT_ENERGY) is None


def test_trend_series_total_eps_week_zero_accepted() -> None:
    """EPS week period with zero total should return 0.0, not None."""
    section = f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_WEEK}"
    source = {APP_STAT_TOTAL_OUT_EPS_ENERGY: "0"}
    assert util.trend_series_total(source, section, APP_STAT_TOTAL_OUT_EPS_ENERGY) == 0.0


def test_trend_series_total_eps_year_zero_accepted() -> None:
    """EPS year period with zero total should return 0.0, not None."""
    section = f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_YEAR}"
    source = {APP_STAT_TOTAL_OUT_EPS_ENERGY: "0"}
    assert util.trend_series_total(source, section, APP_STAT_TOTAL_OUT_EPS_ENERGY) == 0.0


def test_trend_series_has_value_ct_day_zero_accepted() -> None:
    """CT day period with zero total and valid unit should return True."""
    section = f"{APP_SECTION_CT_STAT}_{DATE_TYPE_DAY}"
    source = {APP_STAT_TOTAL_CT_INPUT_ENERGY: "0", APP_STAT_UNIT: APP_UNIT_KWH}
    assert util.trend_series_has_value(source, section, APP_STAT_TOTAL_CT_INPUT_ENERGY) is True


def test_trend_series_has_value_eps_day_zero_accepted() -> None:
    """EPS day period with zero total and valid unit should return True."""
    section = f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_DAY}"
    source = {APP_STAT_TOTAL_IN_EPS_ENERGY: "0", APP_STAT_UNIT: APP_UNIT_KWH}
    assert util.trend_series_has_value(source, section, APP_STAT_TOTAL_IN_EPS_ENERGY) is True


def test_trend_series_has_value_ct_month_zero_accepted() -> None:
    """CT month period with zero total and valid unit should return True."""
    section = f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}"
    source = {APP_STAT_TOTAL_CT_INPUT_ENERGY: "0", APP_STAT_UNIT: APP_UNIT_KWH}
    assert util.trend_series_has_value(source, section, APP_STAT_TOTAL_CT_INPUT_ENERGY) is True


def test_trend_series_has_value_eps_month_zero_accepted() -> None:
    """EPS month period with zero total and valid unit should return True."""
    section = f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_MONTH}"
    source = {APP_STAT_TOTAL_OUT_EPS_ENERGY: "0", APP_STAT_UNIT: APP_UNIT_KWH}
    assert util.trend_series_has_value(source, section, APP_STAT_TOTAL_OUT_EPS_ENERGY) is True


def test_trend_series_has_value_ct_no_unit_zero_total_accepted() -> None:
    """CT section with missing unit but zero server total should return True."""
    section = f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}"
    source = {APP_STAT_TOTAL_CT_INPUT_ENERGY: "0"}
    assert util.trend_series_has_value(source, section, APP_STAT_TOTAL_CT_INPUT_ENERGY) is True


def test_trend_series_has_value_eps_no_unit_zero_total_accepted() -> None:
    """EPS section with missing unit but zero server total should return True."""
    section = f"{APP_SECTION_EPS_STAT}_{DATE_TYPE_MONTH}"
    source = {APP_STAT_TOTAL_OUT_EPS_ENERGY: "0"}
    assert util.trend_series_has_value(source, section, APP_STAT_TOTAL_OUT_EPS_ENERGY) is True


def test_trend_series_has_value_ct_no_unit_empty_series_zero_total() -> None:
    """CT section with missing unit, empty series, and zero total should return True."""
    section = f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}"
    source = {APP_CHART_SERIES_Y: [], APP_STAT_TOTAL_CT_INPUT_ENERGY: "0"}
    assert util.trend_series_has_value(source, section, APP_STAT_TOTAL_CT_INPUT_ENERGY) is True


def test_trend_series_has_value_ct_negative_total_rejected() -> None:
    """CT section with negative total should return False."""
    section = f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}"
    source = {APP_STAT_TOTAL_CT_INPUT_ENERGY: "-1.5"}
    assert util.trend_series_has_value(source, section, APP_STAT_TOTAL_CT_INPUT_ENERGY) is False


def test_trend_series_total_ct_series_sum_zero() -> None:
    """CT section with series summing to zero should return 0.0."""
    section = f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}"
    source = {
        APP_CHART_SERIES_Y1: [0.0, 0.0, 0.0],
        APP_STAT_UNIT: APP_UNIT_KWH,
    }
    assert util.trend_series_total(source, section, APP_STAT_TOTAL_CT_INPUT_ENERGY) == 0.0
