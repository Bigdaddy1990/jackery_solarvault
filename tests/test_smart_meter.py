"""Unit tests for CT smart-meter power helpers (Finding C).

Loads util.py directly — no HA test harness required.
Coverage goals:
  (a) tPhasePw (total) wins when both total and per-phase values present.
  (b) Per-phase sum used when total pair absent.
  (c) Keys present with value 0 → 0.0 (device reports 0 W, not unavailable).
  (d) Keys absent → None (no data at all).
  (e) One phase missing from 3-phase payload → phases fallback blocked,
      smart_meter_net_power falls back to per-phase sum only if total absent.
"""

import importlib.util
from pathlib import Path
import sys
import types

import pytest


def _load_util_module() -> types.ModuleType:
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
    assert const_spec is not None  # noqa: S101
    const_module = importlib.util.module_from_spec(const_spec)
    sys.modules[const_spec.name] = const_module
    assert const_spec.loader is not None  # noqa: S101
    const_spec.loader.exec_module(const_module)

    spec = importlib.util.spec_from_file_location(
        "custom_components.jackery_solarvault.util",
        package_dir / "util.py",
    )
    assert spec is not None  # noqa: S101
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None  # noqa: S101
    spec.loader.exec_module(module)
    return module


util = _load_util_module()

# ---------------------------------------------------------------------------
# Snapshot from the diagnostics log (2026-06-03):
#   tPhasePw=10, aPhasePw=2, anPhasePw=0, bPhasePw=0, bnPhasePw=254,
#   cPhasePw=235, cnPhasePw=0
# tPhasePw - tnPhasePw = 10 - 0 = +10 W (net import)
# per-phase net = (2-0) + (0-254) + (235-0) = -17 W
# The drift (+27 W) is within the Shelly 3EM 5 % spec at 500 W range.
# ---------------------------------------------------------------------------

SHELLY_SNAPSHOT = {
    "tPhasePw": 10,
    "tnPhasePw": 0,
    "aPhasePw": 2,
    "anPhasePw": 0,
    "bPhasePw": 0,
    "bnPhasePw": 254,
    "cPhasePw": 235,
    "cnPhasePw": 0,
}


def test_smart_meter_net_power_prefers_total() -> None:
    """tPhasePw/tnPhasePw wins over per-phase sum when both are present."""
    result = util.smart_meter_net_power(SHELLY_SNAPSHOT)
    assert result == 10, f"expected 10 W (tPhasePw net), got {result}"  # noqa: PLR2004, S101


def test_smart_meter_net_power_falls_back_to_phases() -> None:
    """Per-phase sum is used when total pair is absent."""
    ct = {
        k: v for k, v in SHELLY_SNAPSHOT.items() if k not in {"tPhasePw", "tnPhasePw"}
    }
    result = util.smart_meter_net_power(ct)
    # (2-0) + (0-254) + (235-0) = -17
    assert result == -17, f"expected -17 W (phase sum fallback), got {result}"  # noqa: PLR2004, S101


def test_smart_meter_net_power_all_zero_returns_zero_not_none() -> None:
    """Keys present with value 0 → 0.0 (device physically reports 0 W)."""
    ct = {
        "tPhasePw": 0,
        "tnPhasePw": 0,
        "aPhasePw": 0,
        "anPhasePw": 0,
        "bPhasePw": 0,
        "bnPhasePw": 0,
        "cPhasePw": 0,
        "cnPhasePw": 0,
    }
    result = util.smart_meter_net_power(ct)
    assert result == pytest.approx(0.0), f"expected 0.0 W, got {result}"  # noqa: S101
    assert result is not None, "0.0 W reading must not be None (that means unavailable)"  # noqa: S101


def test_smart_meter_net_power_empty_dict_returns_none() -> None:
    """Empty CT dict → None (no data, sensor should be unavailable)."""
    assert util.smart_meter_net_power({}) is None  # noqa: S101


def test_smart_meter_net_power_missing_one_phase_uses_total() -> None:
    """One phase missing → signed_phase_power_values returns None, total still used."""
    ct = {k: v for k, v in SHELLY_SNAPSHOT.items() if k != "aPhasePw"}
    # total pair still present → should return total
    result = util.smart_meter_net_power(ct)
    assert result == 10, f"expected 10 W from total pair, got {result}"  # noqa: PLR2004, S101


def test_smart_meter_net_power_missing_full_phase_no_total_returns_none() -> None:
    """Both keys of phase A absent and no total pair return None.

    Note: removing only aPhasePw while anPhasePw is still present still produces
    0.0 for that phase (negative key found, positive absent=0). Both keys of the
    pair must be absent for directional_power_value to return None.
    """
    ct = {
        k: v
        for k, v in SHELLY_SNAPSHOT.items()
        if k not in {"tPhasePw", "tnPhasePw", "aPhasePw", "anPhasePw"}
    }
    result = util.smart_meter_net_power(ct)
    assert result is None, (  # noqa: S101
        f"expected None with full phase-A absent + no total, got {result}"
    )


def test_directional_power_value_keys_absent_returns_none() -> None:
    """directional_power_value returns None when no keys are present."""
    assert util.directional_power_value({}, ("tPhasePw",), ("tnPhasePw",)) is None  # noqa: S101


def test_directional_power_value_zero_values_return_zero() -> None:
    """directional_power_value with present-but-zero keys returns 0.0, not None."""
    result = util.directional_power_value(
        {"tPhasePw": 0, "tnPhasePw": 0},
        ("tPhasePw",),
        ("tnPhasePw",),
    )
    assert result == pytest.approx(0.0)  # noqa: S101
