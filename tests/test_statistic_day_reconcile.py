"""Behaviour of the closed-day reconcile planner for daily energy statistics."""

import itertools
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.jackery_solarvault.util import (
    StatisticRow,
    plan_statistic_day_reconcile,
)

if TYPE_CHECKING:
    from custom_components.jackery_solarvault.util import StatisticDayReconcile

_H = 3600.0
_DAY = 24 * _H  # the reconciled day spans hours 24..47
_HOURS = [_DAY + h * _H for h in range(24)]


def _day_reset(hour: float) -> float:
    return _DAY if hour >= _DAY else 0.0


def _merged(
    existing: dict[float, StatisticRow], plan: StatisticDayReconcile
) -> dict[float, float]:
    """Return every hour's sum after the plan and HA's adjust are applied."""
    merged = {start: row.sum for start, row in existing.items()}
    if plan.shift_start is not None:
        for key in [t for t in merged if t >= plan.shift_start]:
            merged[key] += plan.shift
    for row in plan.rows:
        merged[row["start"].timestamp()] = row["sum"]
    return merged


def _increments(merged: dict[float, float]) -> dict[float, float]:
    ordered = sorted(merged)
    return {b: merged[b] - merged[a] for a, b in itertools.pairwise(ordered)}


def test_overcounted_day_is_rewritten_to_the_curve_and_later_rows_shift() -> None:
    """Live 2026-09-25: a stored +11.86 kWh hour inflated the day to 25.4 kWh."""
    existing = {
        23 * _H: StatisticRow(100.0, 9.0, 0.0),
        **{hour: StatisticRow(100.0, None, _DAY) for hour in _HOURS[:9]},
        _HOURS[9]: StatisticRow(103.12, None, _DAY),
        _HOURS[10]: StatisticRow(114.98, None, _DAY),
        **{hour: StatisticRow(117.0, 6.6, _DAY) for hour in _HOURS[11:]},
        48 * _H: StatisticRow(117.5, 0.5, 48 * _H),
    }
    curve = {_HOURS[h]: 1.0 for h in range(9, 15)}  # 6 kWh day

    plan = plan_statistic_day_reconcile(_HOURS, curve, existing, _day_reset)
    merged = _merged(existing, plan)
    steps = _increments(merged)

    assert merged[_HOURS[-1]] == pytest.approx(106.0)
    assert [steps[_HOURS[h]] for h in range(9, 15)] == pytest.approx([1.0] * 6)
    assert steps[48 * _H] == pytest.approx(0.5)  # the next day keeps its increase
    assert plan.shift == pytest.approx(-11.0)
    assert all(step >= 0 for step in steps.values())


def test_missing_day_is_filled_and_later_rows_move_up() -> None:
    """A day the recorder never stored adds its curve and shifts later sums."""
    existing = {
        23 * _H: StatisticRow(50.0, 4.0, 0.0),
        48 * _H: StatisticRow(50.5, 0.5, 48 * _H),
    }
    curve = {_HOURS[12]: 2.0, _HOURS[13]: 1.0}

    plan = plan_statistic_day_reconcile(_HOURS, curve, existing, _day_reset)
    merged = _merged(existing, plan)

    assert len(plan.rows) == len(_HOURS)
    assert merged[_HOURS[-1]] == pytest.approx(53.0)
    assert merged[48 * _H] == pytest.approx(53.5)
    assert plan.rows[-1]["state"] == pytest.approx(3.0)


def test_matching_day_writes_nothing() -> None:
    """A day that already equals the curve is neither rewritten nor shifted."""
    existing = {23 * _H: StatisticRow(10.0, 1.0, 0.0)}
    total = 10.0
    for hour in _HOURS:
        total += 0.5 if hour == _HOURS[12] else 0.0
        existing[hour] = StatisticRow(total, total - 10.0, _DAY)
    existing[48 * _H] = StatisticRow(total, 0.0, 48 * _H)

    plan = plan_statistic_day_reconcile(
        _HOURS, {_HOURS[12]: 0.5}, existing, _day_reset
    )

    assert plan.rows == []
    assert plan.shift_start is None


def test_day_before_history_is_anchored_backwards_without_shift() -> None:
    """Without an earlier row the day ends where the first later row starts."""
    existing: dict[float, Any] = {48 * _H: StatisticRow(20.0, 0.0, 48 * _H)}

    plan = plan_statistic_day_reconcile(
        _HOURS, {_HOURS[10]: 4.0}, existing, _day_reset
    )

    assert plan.shift_start is None
    assert plan.rows[-1]["sum"] == pytest.approx(20.0)
    assert plan.rows[0]["sum"] == pytest.approx(16.0)


def test_no_stored_rows_at_all_plans_nothing() -> None:
    """Without any stored row there is nothing to anchor the day on."""
    plan = plan_statistic_day_reconcile(_HOURS, {_HOURS[3]: 1.0}, {}, _day_reset)

    assert plan.rows == []
    assert plan.shift_start is None
