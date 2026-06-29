"""Negative entity-stat values must be rejected loudly, not silently.

AGENTS.md §2.2 rule-1 requires reject + WARNING + skip for anomalous values.
PV / generation / grid / battery energy buckets are always >= 0 (signed
battery/grid flow is handled on a separate path). A negative bucket in the
app-chart statistic-contribution path therefore signals data corruption or an
API error, and must be:

* rejected (never imported into the HA Recorder), AND
* surfaced at WARNING naming the metric, so a real upstream fault is visible
  rather than swallowed.

These tests lock down the WARNING-on-reject behaviour of the external
statistic contribution path
(:meth:`JackerySolarVaultCoordinator._async_add_app_chart_statistics`). They
feed only-negative points so the method short-circuits before touching the
recorder, isolating the reject-and-warn behaviour. The rejection itself
(the ``state < 0 -> continue``) must remain intact.
"""

# ruff: noqa: PLC0415, SLF001

from datetime import date
import logging
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from custom_components.jackery_solarvault.const import DOMAIN
from custom_components.jackery_solarvault.util import TrendStatisticPoint
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

if TYPE_CHECKING:
    from custom_components.jackery_solarvault.coordinator import (
        JackerySolarVaultCoordinator,
    )
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.asyncio

_DEVICE_ID = "DEV-NEG-WARN-1"
_METRIC_KEY = "pv_energy"
_BUCKET = "day_hourly"


async def _build_coordinator(
    hass: HomeAssistant,
) -> JackerySolarVaultCoordinator:
    """Set up a real config entry and return its live coordinator."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "pass"},
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_update_data",
            return_value={},
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_ensure_mqtt",
            return_value=None,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    return coordinator


async def test_negative_external_stat_value_logs_warning_and_is_not_imported(
    hass: HomeAssistant,
    mock_jackery_login: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A negative bucket must be rejected with a WARNING naming the metric."""
    coordinator = await _build_coordinator(hass)
    negative_points = [
        TrendStatisticPoint(start_date=date(2026, 5, 14), value=-5.0),
        TrendStatisticPoint(start_date=date(2026, 5, 15), value=-2.0),
    ]

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        ok, bucket_count = await coordinator._async_add_app_chart_statistics(
            device_id=_DEVICE_ID,
            name_prefix="PV",
            metric_key=_METRIC_KEY,
            label="PV energy",
            bucket=_BUCKET,
            bucket_label="day hourly",
            points=negative_points,
        )

    # Rejection preserved: no bucket imported, method short-circuits cleanly.
    assert bucket_count == 0
    assert ok is True

    combined_log = caplog.text.lower()
    assert "negative" in combined_log
    assert _METRIC_KEY in combined_log


async def test_non_negative_external_stat_values_log_no_negative_warning(
    hass: HomeAssistant,
    mock_jackery_login: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legitimate non-negative buckets must not emit the negative warning."""
    coordinator = await _build_coordinator(hass)
    positive_points = [
        TrendStatisticPoint(start_date=date(2026, 5, 14), value=4.0),
        TrendStatisticPoint(start_date=date(2026, 5, 15), value=0.0),
    ]

    caplog.clear()
    with (
        caplog.at_level(logging.WARNING),
        patch(
            "homeassistant.components.recorder.get_instance",
            side_effect=RuntimeError("recorder disabled in test"),
        ),
        patch(
            "homeassistant.components.recorder.statistics."
            "async_add_external_statistics",
        ),
        patch(
            "homeassistant.components.recorder.statistics.statistics_during_period",
            return_value={},
        ),
    ):
        await coordinator._async_add_app_chart_statistics(
            device_id=_DEVICE_ID,
            name_prefix="PV",
            metric_key=_METRIC_KEY,
            label="PV energy",
            bucket=_BUCKET,
            bucket_label="day hourly",
            points=positive_points,
        )

    assert "negative" not in caplog.text.lower()
