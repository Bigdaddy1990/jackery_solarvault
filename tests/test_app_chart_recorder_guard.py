"""Recorder import guards for inconsistent Jackery app day charts."""

from datetime import UTC, date, datetime
from typing import Any, cast

from custom_components.jackery_solarvault.const import (
    APP_CHART_LABELS,
    APP_CHART_SERIES_Y,
    APP_DEVICE_STAT_PV_ENERGY,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_META,
    APP_SECTION_PV_STAT,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    DATE_TYPE_DAY,
    PAYLOAD_LOCAL_DAILY_ENERGY,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_DEVICE_ID = "dev-1"
_DAY_SECTION = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}"


def test_day_chart_recorder_import_rejects_lagging_cloud_total() -> None:
    """A smaller cloud day chart cannot create external Recorder buckets."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    mutable = cast("Any", coordinator)
    mutable.data = {
        _DEVICE_ID: {
            PAYLOAD_LOCAL_DAILY_ENERGY: {
                APP_DEVICE_STAT_PV_ENERGY: 2062,
            },
        },
    }
    today = date(2026, 8, 13)
    payload = {
        _DAY_SECTION: {
            APP_CHART_LABELS: ["12:00"],
            APP_CHART_SERIES_Y: [7800.0],
            APP_STAT_TOTAL_SOLAR_ENERGY: 0.65,
            "unit": "w",
            APP_REQUEST_META: {
                APP_REQUEST_BEGIN_DATE: today.isoformat(),
                APP_REQUEST_END_DATE: today.isoformat(),
            },
        },
    }

    points = coordinator._day_chart_points_for_metric(
        _DEVICE_ID,
        payload,
        APP_SECTION_PV_STAT,
        APP_STAT_TOTAL_SOLAR_ENERGY,
        "pv_energy",
        bucket_minutes=60,
        now=datetime(2026, 8, 13, 13, 0, tzinfo=UTC),
    )

    assert points == []
