"""Shared helpers for Jackery SolarVault entities."""

import asyncio
from bisect import bisect_right
import calendar
from collections.abc import Mapping
import contextlib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from http import HTTPStatus
import json
import logging
import math
import operator
import os
from pathlib import Path
import re
import threading
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    NamedTuple,
    SupportsFloat,
    SupportsIndex,
    cast,
)
from urllib.parse import quote, unquote

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    APP_CHART_LABELS,
    APP_CHART_SERIES_Y,
    APP_CHART_SERIES_Y1,
    APP_CHART_SERIES_Y2,
    APP_CHART_SERIES_Y3,
    APP_CHART_SERIES_Y4,
    APP_CHART_SERIES_Y5,
    APP_CHART_SERIES_Y6,
    APP_CHART_STAT_PERIODS,
    APP_DEVICE_STAT_BATTERY_CHARGE,
    APP_DEVICE_STAT_BATTERY_DISCHARGE,
    APP_DEVICE_STAT_BATTERY_TO_AC,
    APP_DEVICE_STAT_BATTERY_TO_GRID,
    APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    APP_HOME_GRID_SERIES_KEYS,
    APP_PERIOD_DATE_TYPES,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_DATE_TYPE_ALT,
    APP_REQUEST_END_DATE,
    APP_REQUEST_END_DATE_ALT,
    APP_REQUEST_META,
    APP_REQUEST_STAT_TYPE,
    APP_SAVINGS_CALC_META,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_BATTERY_TRENDS,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_PV_PROFIT,
    APP_STAT_TOTAL_CARBON,
    APP_STAT_TOTAL_CHARGE,
    APP_STAT_TOTAL_CT_INPUT_ENERGY,
    APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
    APP_STAT_TOTAL_DISCHARGE,
    APP_STAT_TOTAL_GENERATION,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_IN_EPS_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_OUT_EPS_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    APP_STAT_TOTAL_REVENUE,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_TOTAL_SOLAR_REVENUE,
    APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
    APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
    APP_STAT_UNIT,
    APP_UNIT_KWH,
    APP_UNIT_WH,
    APP_YEAR_BACKFILL_META,
    CHECK_VERIFY_CODE_PATH,
    CONF_ENABLE_UNREDACTED_DEBUG,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CT_PHASE_POWER_PAIRS,
    CT_TOTAL_POWER_PAIR,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DEFAULT_THIRD_PARTY_MQTT_ENABLE,
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    DOMAIN,
    FIELD_ACCESSORIES,
    FIELD_CURRENT_VERSION,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEVICE_TYPE,
    FIELD_DEV_ID,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_GRID_IN_PW,
    FIELD_GRID_OUT_PW,
    FIELD_ID,
    FIELD_IDX,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_IN_ONGRID_PW,
    FIELD_IP,
    FIELD_MAC,
    FIELD_OTHER_LOAD_PW,
    FIELD_OUT_GRID_SIDE_PW,
    FIELD_OUT_ONGRID_PW,
    FIELD_SINGLE_PRICE,
    FIELD_SN,
    FIELD_WIP,
    FIELD_WNAME,
    JACKERY_LIVE_ENERGY_UNITS_PER_KWH,
    MQTT_SENSOR_PUBLISHER_RUNTIME_KEY,
    PAYLOAD_ALARM,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_CIRCUIT_PROPERTY,
    PAYLOAD_CT_METER,
    PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX,
    PAYLOAD_DEBUG_LOG_MAX_BYTES,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_LOCAL_DAILY_ENERGY,
    PAYLOAD_METER_HEADS,
    PAYLOAD_OBSERVED_DAY_STATISTICS,
    PAYLOAD_OTA,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_STATISTIC,
    PAYLOAD_SUBDEVICES,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
    PAYLOAD_VERIFIED_DAY_STATISTICS,
    REDACTED_VALUE,
    REDACT_KEYS,
    SUBDEVICE_SCAN_NAME_LABELS,
    SUBDEVICE_SCAN_NAME_MANUFACTURERS,
    TASK_PLAN_BODY,
    TASK_PLAN_TASKS,
    VERIFICATION_REQUEST_PATHS,
)
from .ingest import (
    is_pv_generation_metric,
    normalize_pv_period_energy,
    pv_channel_scalar_is_lifetime_offset as _ingest_pv_scalar_is_offset,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence

    from homeassistant.components.sensor import SensorEntityDescription
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import StateType
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
_DEV_MODE_ENV: Final = "JACKERY_DEV_MODE"
_DEV_MODE_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})

# CPU-Optimierung: Regex auf Modulebene kompilieren, nicht pro Schleifendurchlauf
_DAY_CHART_MINUTE_RE = re.compile(r"\s*(\d{1,2}):(\d{2})\s*")
_MAX_COMPACT_YEAR_VALUE_TEXT_LENGTH = 64
_SUBDEVICE_ID_RE = re.compile(r"[^A-Za-z0-9_]+")
_REDACT_KEYS_CASEFOLD: Final[frozenset[str]] = frozenset(
    key.casefold() for key in REDACT_KEYS
)
_MIN_REDACT_LITERAL_LENGTH: Final = 4

# Calendar / time bounds used in validation guards.
_MONTHS_PER_YEAR: Final = 12
_MAC_HEX_LENGTH: Final = 12
_HOURS_PER_DAY: Final = 24
_MAX_HOUR: Final = 23
_MAX_MINUTE: Final = 59
# A POSIX timestamp at/above this magnitude is in milliseconds, not seconds.
_MILLIS_TIMESTAMP_THRESHOLD: Final = 100_000_000_000
# Treat |value| below this as numerically zero for chart-bucket presence checks.
_NEAR_ZERO_EPSILON: Final = 0.00001
# Plausible upper bound for a single electricity price / per-kWh derived rate.
_MAX_PRICE_PER_KWH: Final = 10
# Plausible upper bound for a carbon-per-generation correction factor.
_MAX_CARBON_FACTOR: Final = 5
_APP_UNIT_WATT: Final = "w"
_SOC_TRACE_MIN_SAMPLES: Final = 2
_SOC_PERCENT_MAX: Final = 100
_SOC_TRACE_POWER_MIN_W: Final = 100
_DAY_POWER_SAMPLE_MINUTES: Final = 5
_MINUTES_PER_HOUR: Final = 60
_MINUTES_PER_DAY: Final = _HOURS_PER_DAY * _MINUTES_PER_HOUR
_WATTS_PER_KILOWATT: Final = 1000
WHOLE_INT_TEXT_RE = re.compile(r"[+-]?\d+(?:\.0+)?\Z")


@callback
def async_setup_entity_discovery(
    entry: ConfigEntry,
    coordinator: DataUpdateCoordinator[dict[str, Any]],
    collect_entities: Callable[[], Sequence[Entity]],
    async_add_entities: AddEntitiesCallback,
    *,
    eligibility_signature: Callable[[], object] | None = None,
) -> None:
    """Register ordinary platform discovery and bind its listener to entry unload."""
    last_signature: tuple[Any, ...] = ()

    @callback
    def _add_new_entities() -> None:
        nonlocal last_signature
        signature = coordinator_entity_signature(coordinator.data)
        if eligibility_signature is not None:
            signature = (*signature, (eligibility_signature(),))
        if signature == last_signature:
            return
        last_signature = signature
        entities = collect_entities()
        if entities:
            async_add_entities(entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


def get_store_lock(hass: HomeAssistant, key: str) -> asyncio.Lock:
    """Reuse one runtime lock per Store key without coupling independent stores."""
    lock = hass.data.get(key)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        hass.data[key] = lock
    return lock


@callback
def async_create_message_task(
    hass: HomeAssistant,
    entry: ConfigEntry | None,
    operation: Coroutine[Any, Any, None],
    *,
    name: str,
    eager_start: bool = False,
) -> asyncio.Task[None]:
    """Let the client drain accepted MQTT work beyond config-entry teardown."""
    del entry
    return hass.async_create_background_task(
        operation, name=name, eager_start=eager_start
    )


def standby_is_on(raw: bool | float | str | None) -> bool | None:
    """Interpret manual standby mode one without treating other modes as true."""
    if raw is None:
        return None
    parsed = first_nonblank_int(raw)
    if parsed is None:
        return safe_bool(raw)
    return parsed == 1


def jackery_dev_mode_enabled(entry: object | None = None) -> bool:
    """Return whether process or per-entry unredacted debugging is enabled.

    The environment switch applies process-wide. The explicit options-flow
    switch applies only to the provided config entry. Both expose complete
    diagnostics and payload logs; never share those exports publicly.
    """
    return os.environ.get(
        _DEV_MODE_ENV, ""
    ).strip().casefold() in _DEV_MODE_TRUE_VALUES or (
        entry is not None
        and config_entry_bool_option(entry, CONF_ENABLE_UNREDACTED_DEBUG, False)
    )


def accessory_ip_address(payload: Mapping[str, Any]) -> str | None:
    """Read own-device IP metadata; legacy numeric ``ip`` is input power."""
    return first_nonblank_text(
        *(
            value if isinstance(value := payload.get(key), str) else None
            for key in (FIELD_IP, FIELD_WIP)
        )
    )


def pv_channel_scalar_is_lifetime_offset(
    source: dict[str, Any], section: str, stat_key: str
) -> bool:
    """Check whether the vendor's PV-channel scalar exceeds the whole period."""
    return _ingest_pv_scalar_is_offset(source, section, stat_key)


def request_date(
    source: dict[str, Any],
    primary_key: str,
    alternate_key: str,
) -> date | None:
    """Parse one ISO date from a payload request metadata block."""
    request = source.get(APP_REQUEST_META)
    if not isinstance(request, dict):
        return None
    raw = request.get(primary_key) or request.get(alternate_key)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _dated_energy_chart_window(
    source: dict[str, Any], period: str
) -> tuple[date, date] | None:
    """Validate a chart's resolution and bounds without replacing its metadata."""
    request = source.get(APP_REQUEST_META)
    unit = source.get(APP_STAT_UNIT)
    if not isinstance(request, dict) or (
        unit is not None
        and (
            not isinstance(unit, str)
            or unit.strip().lower() not in {"", APP_UNIT_KWH, APP_UNIT_WH}
        )
    ):
        return None
    if any(
        key in request
        and (
            not isinstance(request[key], str) or request[key].strip().lower() != period
        )
        for key in (APP_REQUEST_DATE_TYPE, APP_REQUEST_DATE_TYPE_ALT)
    ):
        return None
    begin = request_date(source, APP_REQUEST_BEGIN_DATE, APP_REQUEST_BEGIN_DATE_ALT)
    if begin is None:
        return None
    calendar_begin, calendar_end = app_period_range(period, today=begin)
    if period == DATE_TYPE_YEAR and begin != calendar_begin:
        # The year-series consumer indexes January as its first month.
        return None
    end = request_date(source, APP_REQUEST_END_DATE, APP_REQUEST_END_DATE_ALT)
    if end is None:
        if any(
            key in request for key in (APP_REQUEST_END_DATE, APP_REQUEST_END_DATE_ALT)
        ):
            return None
        # Legacy metadata omitted the end; the declared period still bounds it.
        end = calendar_end
    return (begin, end) if begin <= end <= calendar_end else None


def current_day_http_source(source: dict[str, Any], section: str, today: date) -> bool:
    """Require a dated observation before consuming an HTTP source as today."""
    if section in {"device_today_energy", "device_statistic", PAYLOAD_STATISTIC}:
        receipt = source.get("_http_day_receipt")
        return bool(
            isinstance(receipt, dict)
            and receipt.get("day") == today.isoformat()
            and receipt.get("section") == section
            and isinstance(receipt.get("owner"), str)
            and receipt["owner"]
            and type(receipt.get("generation")) is int
            and receipt["generation"] > 0
            and isinstance(receipt.get("fields"), dict)
        )
    if section.endswith(f"_{DATE_TYPE_DAY}") or section in {
        "pv_trends",
        PAYLOAD_HOME_TRENDS,
        "battery_trends",
    }:
        request = source.get(APP_REQUEST_META)
        if not isinstance(request, dict) or any(
            key in request and request[key] != DATE_TYPE_DAY
            for key in (APP_REQUEST_DATE_TYPE, APP_REQUEST_DATE_TYPE_ALT)
        ):
            return False
        return (
            request_date(source, APP_REQUEST_BEGIN_DATE, APP_REQUEST_BEGIN_DATE_ALT)
            == today
            and request_date(source, APP_REQUEST_END_DATE, APP_REQUEST_END_DATE_ALT)
            == today
        )
    return True


def current_day_http_values(
    source: dict[str, Any], section: str, today: date
) -> dict[str, Any]:
    """Keep only fields actually measured by the qualifying implicit-day reply."""
    if not current_day_http_source(source, section, today):
        return {}
    if section not in {"device_today_energy", "device_statistic", PAYLOAD_STATISTIC}:
        return source
    fields = source["_http_day_receipt"]["fields"]
    return {
        key: value
        for key, value in source.items()
        if key.startswith("_")
        or (
            key in fields
            and safe_float(value) is not None
            and not isinstance(fields[key], bool)
            and safe_float(fields[key]) == safe_float(value)
        )
    }


def dated_daily_chart_values(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> dict[date, float]:
    """Resolve a daily chart once, retaining its declared dates and units."""
    period = section.rpartition("_")[2]
    if period not in {DATE_TYPE_WEEK, DATE_TYPE_MONTH}:
        return {}
    window = _dated_energy_chart_window(source, period)
    if window is None:
        return {}
    begin, end = window
    energy_scale = app_energy_unit_scale(source)
    if energy_scale is None:
        return {}
    values = effective_trend_series_values(source, section, stat_key)
    if not isinstance(values, list):
        return {}
    return {
        begin + timedelta(days=index): value * energy_scale
        for index, raw in enumerate(values[: (end - begin).days + 1])
        if (value := safe_float(raw)) is not None
    }


def chart_value_for_day(
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    today: date,
) -> float | None:
    """Return one dated daily bucket from a matching week/month energy chart."""
    return dated_daily_chart_values(source, section, stat_key).get(today)


def complete_year_series_total(
    values: list[float | None], scalar: float | None
) -> float | None:
    """Sum twelve known months; an incomplete chart needs a separate scalar."""
    if len(values) != _MONTHS_PER_YEAR or any(value is None for value in values):
        return scalar
    total = round(sum(value for value in values if value is not None), 2)
    return scalar if total == 0 and scalar is not None and scalar > 0 else total


def _observed_energy_number(value: object) -> float | None:
    """Accept real, nonnegative own energy without manufacturing missing zeroes."""
    if isinstance(value, bool):
        return None
    parsed = safe_float(value)
    return (
        parsed if parsed is not None and math.isfinite(parsed) and parsed >= 0 else None
    )


def _observed_pv_total_bound(
    source: dict[str, Any], section: str, *, today: date
) -> float | None:
    """Return a comparable own-period PV bound in kWh, including dated buckets.

    The cloud's positive year or month scalar can lag the same response's
    actual aggregate buckets. In that case it cannot bound one channel more
    tightly than those aggregate measurements do.
    """
    total = _observed_energy_number(source.get(APP_STAT_TOTAL_SOLAR_ENERGY))
    scale = app_energy_unit_scale(source)
    if total is None or total <= 0 or scale is None:
        return None
    bound = total * scale
    period = section.rpartition("_")[2]
    if period in {DATE_TYPE_WEEK, DATE_TYPE_MONTH}:
        aggregate = dated_daily_chart_values(
            source, section, APP_STAT_TOTAL_SOLAR_ENERGY
        )
        bucket_sum = sum(
            value
            for day, value in aggregate.items()
            if day <= today and math.isfinite(value) and value >= 0
        )
    elif period == DATE_TYPE_YEAR:
        window = _dated_energy_chart_window(source, DATE_TYPE_YEAR)
        values = effective_trend_series_values(
            source, section, APP_STAT_TOTAL_SOLAR_ENERGY
        )
        if window is None or not isinstance(values, list):
            return bound
        bucket_sum = sum(
            value * scale
            for index, raw in enumerate(values[:12])
            if date(window[0].year, index + 1, 1) <= min(today, window[1])
            if (value := _observed_energy_number(raw)) is not None
        )
    else:
        return bound
    return max(bound, bucket_sum)


def observed_period_energy_total(  # ruff: ignore[complex-structure, too-many-branches, too-many-arguments, too-many-locals, too-many-statements]
    payload: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    today: date,
    local_day_kwh: float | None = None,
    local_period_kwh: float | None = None,
    local_period_attributes: dict[str, Any] | None = None,
    local_period_metric: str | None = None,
) -> tuple[float, dict[str, Any]] | None:
    """Aggregate own measured intervals for native states, including partial ones.

    This deliberately does not grant Recorder's complete-day certificate. Each
    date has one own metric, and overlapping day/month/period alternatives are
    selected rather than added. Existing chart decoders retain their unit,
    calendar and PV lifetime-offset guards.
    """
    prefix, _, period = section.rpartition("_")
    if period not in {
        DATE_TYPE_DAY,
        DATE_TYPE_WEEK,
        DATE_TYPE_MONTH,
        DATE_TYPE_YEAR,
        "total",
    }:
        return None
    begin = date.min if period == "total" else app_period_range(period, today=today)[0]
    stop = today + timedelta(days=1)
    # date -> (energy, provenance). A certified full day overrides partials.
    days: dict[date, tuple[float, dict[str, Any]]] = {}
    zero_charts: dict[date, set[str]] = {}
    is_pv_channel = prefix == APP_SECTION_PV_STAT and stat_key in {
        APP_STAT_PV1_ENERGY,
        APP_STAT_PV2_ENERGY,
        APP_STAT_PV3_ENERGY,
        APP_STAT_PV4_ENERGY,
    }

    def add_day(  # ruff: ignore[too-many-arguments]
        day: date,
        value: object,
        source: str,
        *,
        certified: bool = False,
        samples: int = 0,
        complete: bool = False,
    ) -> None:
        parsed = _observed_energy_number(value)
        if parsed is None or not begin <= day < stop:
            return
        previous = days.get(day)
        metadata = {
            "source": source,
            "certified": certified,
            "samples": samples,
            "complete": complete,
        }
        if (
            previous is None
            or (certified and not previous[1]["certified"])
            or (not previous[1]["certified"] and parsed > previous[0])
        ):
            days[day] = parsed, metadata

    source_periods = (
        (DATE_TYPE_WEEK, DATE_TYPE_MONTH)
        if period == DATE_TYPE_DAY
        else (DATE_TYPE_WEEK,)
        if period == DATE_TYPE_WEEK
        else (DATE_TYPE_MONTH,)
        if period in {DATE_TYPE_MONTH, DATE_TYPE_YEAR}
        else ()
    )
    for source_period in source_periods:
        source_section = f"{prefix}_{source_period}"
        source = payload.get(source_section)
        if isinstance(source, dict):
            total = (
                _observed_pv_total_bound(source, source_section, today=today)
                if is_pv_channel
                else None
            )
            for day, value in dated_daily_chart_values(
                source, source_section, stat_key
            ).items():
                if total is not None and pv_channel_scalar_is_lifetime_offset(
                    {stat_key: value, APP_STAT_TOTAL_SOLAR_ENERGY: total},
                    source_section,
                    stat_key,
                ):
                    continue
                if value == 0:
                    zero_charts.setdefault(day, set()).add(source_section)
                else:
                    add_day(day, value, source_section, complete=day < today)

    for payload_key in (
        PAYLOAD_OBSERVED_DAY_STATISTICS,
        PAYLOAD_VERIFIED_DAY_STATISTICS,
    ):
        history = payload.get(payload_key)
        if not isinstance(history, dict):
            continue
        for day_text, families in history.items():
            if not isinstance(day_text, str) or not isinstance(families, dict):
                continue
            try:
                day = date.fromisoformat(day_text)
            except ValueError:
                continue
            family = families.get(prefix)
            if not isinstance(family, dict):
                continue
            evidence = family.get(stat_key)
            if payload_key == PAYLOAD_VERIFIED_DAY_STATISTICS:
                if day < today:
                    add_day(
                        day,
                        evidence,
                        payload_key,
                        certified=True,
                        samples=288,
                        complete=True,
                    )
                continue
            if not isinstance(evidence, dict):
                continue
            samples = evidence.get("observed_samples")
            if (
                type(samples) is not int
                or not 0 < samples <= 288  # ruff: ignore[magic-value-comparison]
                or evidence.get("expected_samples") != 288  # ruff: ignore[magic-value-comparison]
                or evidence.get("source") != "five_minute_power_curve"
                or str(evidence.get("unit", APP_UNIT_KWH)).strip().lower()
                != APP_UNIT_KWH
            ):
                continue
            add_day(
                day,
                evidence.get("value"),
                payload_key,
                samples=samples,
                complete=evidence.get("complete") is True
                and samples == 288  # ruff: ignore[magic-value-comparison]
                and day < today,
            )
    add_day(today, local_day_kwh, PAYLOAD_LOCAL_DAILY_ENERGY)
    # A lone zero in an HTTP chart can be a padded bucket. Preserve the
    # existing two-source zero contract; explicit measured samples/counters
    # and certified zeroes above already constitute their own real evidence.
    for day, sources in zero_charts.items():
        if len(sources) >= 2:  # ruff: ignore[magic-value-comparison]
            add_day(day, 0, "+".join(sorted(sources)), complete=day < today)

    # (start, exclusive end, value, metadata). Coarse buckets never get fake
    # five-minute coverage and cannot cross the target calendar boundary.
    candidates: list[tuple[date, date, float, dict[str, Any]]] = [
        (day, day + timedelta(days=1), value, {**metadata, "day": day})
        for day, (value, metadata) in days.items()
    ]
    counter_value = _observed_energy_number(local_period_kwh)
    attributes = local_period_attributes or {}
    if (
        counter_value is not None  # ruff: ignore[too-many-boolean-expressions]
        and attributes.get("baseline_source") == "own_observed_counter"
        and str(attributes.get("period_value_unit", APP_UNIT_KWH)).lower()
        == APP_UNIT_KWH
        and attributes.get("source_metric", local_period_metric or stat_key)
        == (local_period_metric or stat_key)
        and (
            "period_start" not in attributes
            or period == "total"
            or attributes["period_start"] == begin.isoformat()
        )
    ):
        try:
            first = datetime.fromisoformat(attributes["observed_since"])
            last = datetime.fromisoformat(attributes["observed_until"])
            valid_counter = (
                first.tzinfo is not None and last.tzinfo is not None and first <= last
            )
        except (KeyError, TypeError, ValueError):
            valid_counter = False
        if valid_counter and begin <= first.date() <= last.date() < stop:
            candidates.append((
                first.date(),
                last.date() + timedelta(days=1),
                counter_value,
                {
                    "source": "own_observed_counter",
                    "complete": False,
                    "counter_baseline": attributes,
                },
            ))
    if period == DATE_TYPE_YEAR:
        source_section = f"{prefix}_{DATE_TYPE_YEAR}"
        source = payload.get(source_section)
        if isinstance(source, dict) and (
            window := _dated_energy_chart_window(source, DATE_TYPE_YEAR)
        ):
            values = effective_trend_series_values(source, source_section, stat_key)
            year_scale = app_energy_unit_scale(source)
            total = (
                _observed_pv_total_bound(source, source_section, today=today)
                if is_pv_channel
                else None
            )
            if isinstance(values, list) and year_scale is not None:
                for index, raw_year_value in enumerate(values[:12]):
                    month_begin = date(window[0].year, index + 1, 1)
                    month_end = date(
                        window[0].year + (index + 1 == calendar.DECEMBER),
                        (index + 1) % 12 + 1,
                        1,
                    )
                    parsed = _observed_energy_number(raw_year_value)
                    if (
                        parsed is None
                        or parsed == 0
                        or month_begin < begin
                        or month_begin >= stop
                        or month_begin > window[1]
                    ):
                        continue
                    if total is not None and pv_channel_scalar_is_lifetime_offset(
                        {
                            stat_key: parsed * year_scale,
                            APP_STAT_TOTAL_SOLAR_ENERGY: total,
                        },
                        source_section,
                        stat_key,
                    ):
                        continue
                    candidates.append((
                        month_begin,
                        min(month_end, stop, window[1] + timedelta(days=1)),
                        parsed * year_scale,
                        {
                            "source": source_section,
                            "complete": month_end
                            <= min(stop, window[1] + timedelta(days=1)),
                        },
                    ))
    # Never clip a longer period's scalar to a shorter calendar interval.
    # Only explicit same-endpoint months may guard a broken annual value.
    scalar_periods = (
        (DATE_TYPE_WEEK,)
        if period == DATE_TYPE_WEEK
        else (DATE_TYPE_MONTH,)
        if period == DATE_TYPE_MONTH
        else (DATE_TYPE_MONTH, DATE_TYPE_YEAR)
        if period == DATE_TYPE_YEAR
        else ()
    )
    for source_period in scalar_periods:
        source_section = f"{prefix}_{source_period}"
        source = payload.get(source_section)
        if not isinstance(source, dict):
            continue
        window = _dated_energy_chart_window(source, source_period)
        if window is None or window != app_period_range(source_period, today=window[0]):
            continue
        interval_begin, interval_end = window
        if interval_begin < begin or interval_begin >= stop:
            continue
        # A cross-boundary week must be represented by its own dated buckets.
        if period == DATE_TYPE_MONTH and interval_end.month != begin.month:
            continue
        if period == DATE_TYPE_YEAR and interval_end.year != begin.year:
            continue
        period_scale = app_energy_unit_scale(source)
        period_value = _observed_energy_number(source.get(stat_key))
        if period_scale is None or period_value is None or period_value == 0:
            continue
        bound = (
            _observed_pv_total_bound(source, source_section, today=today)
            if is_pv_channel
            else None
        )
        if not is_pv_channel and pv_channel_scalar_is_lifetime_offset(
            source, source_section, stat_key
        ):
            continue
        if bound is not None and pv_channel_scalar_is_lifetime_offset(
            {stat_key: period_value * period_scale, APP_STAT_TOTAL_SOLAR_ENERGY: bound},
            source_section,
            stat_key,
        ):
            continue
        candidates.append((
            interval_begin,
            min(interval_end + timedelta(days=1), stop),
            period_value * period_scale,
            {"source": source_section, "complete": interval_end < today},
        ))
    if not candidates:
        return None
    candidates.sort(key=operator.itemgetter(1, 0))
    ends = [item[1] for item in candidates]
    # Weighted interval scheduling: retain the strongest non-overlapping own
    # evidence, never month + its days or week + its overlapping days.
    best: list[tuple[float, int]] = [(0.0, 0)]
    parents = [0]
    taken = [False]
    for index, (start, end, value, _) in enumerate(candidates):
        previous_index = bisect_right(ends, start, hi=index)
        previous = best[previous_index]
        selected = (previous[0] + value, previous[1] + (end - start).days)
        take = selected >= best[-1]
        best.append(selected if take else best[-1])
        parents.append(previous_index if take else index)
        taken.append(take)
    indices: list[int] = []
    cursor = len(candidates)
    while cursor:
        if taken[cursor]:
            indices.append(cursor - 1)
        cursor = parents[cursor]
    chosen = [candidates[index] for index in reversed(indices)]
    observed_dates = sorted(
        item[3]["day"].isoformat() for item in chosen if "day" in item[3]
    )
    expected_days = (stop - begin).days if period != "total" else None
    covered_days = sum((end - start).days for start, end, _, _ in chosen)
    complete = all(item[3].get("complete", False) for item in chosen)
    coverage = {
        "partial": period == "total" or covered_days != expected_days or not complete,
        "period": period,
        "unit": APP_UNIT_KWH,
        "observed_days": len(observed_dates),
        "observed_dates": observed_dates,
        "covered_days": covered_days,
        "expected_days": expected_days,
        "certified_days": sum(bool(item[3].get("certified")) for item in chosen),
        "observed_samples": sum(item[3].get("samples", 0) for item in chosen),
        "expected_samples": 288 * len(observed_dates),
        "sources": sorted({item[3]["source"] for item in chosen}),
        "counter_intervals": [
            item[3]["counter_baseline"]
            for item in chosen
            if "counter_baseline" in item[3]
        ],
    }
    return round(best[-1][0], 5), coverage


def month_total_from_daily_evidence(  # ruff: ignore[too-many-arguments]
    payload: dict[str, Any],
    prefix: str,
    stat_key: str,
    *,
    window: tuple[date, date],
    local_day_kwh: float | None = None,
    daily_values: tuple[Mapping[date, float], Mapping[date, float]] | None = None,
) -> tuple[float, bool] | None:
    """Sum own month and certified day buckets without cross-period repair."""
    if daily_values is None:
        month_section = f"{prefix}_{DATE_TYPE_MONTH}"
        month_source = payload.get(month_section)
        if not isinstance(month_source, dict):
            month_source = {}
        month_values: Mapping[date, float] = dated_daily_chart_values(
            month_source, month_section, stat_key
        )
    else:
        # The legacy tuple can include a cached week chart. It is not valid
        # evidence for repairing a month/year value (DATA_SOURCE_PRIORITY).
        month_values = daily_values[0]
    verified_days = payload.get(PAYLOAD_VERIFIED_DAY_STATISTICS)
    if not isinstance(verified_days, dict):
        verified_days = {}
    last_day = min(
        (window[0].replace(day=28) + timedelta(days=4)).replace(day=1)
        - timedelta(days=1),
        window[1],
    )
    completed_days_verified = window[0] < window[1]
    total = 0.0
    day = window[0]
    observed: list[date] = []
    while day <= last_day:
        value = month_values.get(day)
        day_sources = verified_days.get(day.isoformat())
        day_sources = day_sources.get(prefix) if isinstance(day_sources, dict) else None
        verified = (
            safe_float(day_sources.get(stat_key))
            if day < window[1] and isinstance(day_sources, dict)
            else None
        )
        if verified is not None and verified >= 0:
            value = verified
        elif day < window[1]:
            completed_days_verified = False
        if day == window[1] and local_day_kwh is not None and local_day_kwh >= 0:
            value = local_day_kwh if value is None else max(value, local_day_kwh)
        if value is None or value < 0:
            if stat_key in {
                APP_STAT_PV1_ENERGY,
                APP_STAT_PV2_ENERGY,
                APP_STAT_PV3_ENERGY,
                APP_STAT_PV4_ENERGY,
            }:
                return None
            completed_days_verified = False
            day += timedelta(days=1)
            continue
        observed.append(day)
        total += value
        day += timedelta(days=1)
    if not observed or (observed[0] == window[1] and window[0] < window[1]):
        return None
    return round(total, 5), completed_days_verified


def rebuild_period_energy_total(  # ruff: ignore[too-many-locals]
    payload: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    today: date,
    local_day_kwh: float | None = None,
) -> tuple[float, bool] | None:
    """Reconstruct a current month/year on one shared dated evidence basis."""
    prefix, _, period = section.rpartition("_")
    if period not in {DATE_TYPE_MONTH, DATE_TYPE_YEAR}:
        return None
    source = payload.get(section)
    if not isinstance(source, dict):
        source = {}
    if period == DATE_TYPE_MONTH:
        return month_total_from_daily_evidence(
            payload,
            prefix,
            stat_key,
            window=(today.replace(day=1), today),
            local_day_kwh=local_day_kwh,
        )
    begin = request_date(source, APP_REQUEST_BEGIN_DATE, APP_REQUEST_BEGIN_DATE_ALT)
    if begin is not None and begin.year != today.year:
        return None
    chart_window = _dated_energy_chart_window(source, DATE_TYPE_YEAR)
    values = (
        effective_trend_series_values(source, section, stat_key)
        if chart_window is not None
        else None
    )
    scale = app_energy_unit_scale(source) if chart_window is not None else None
    # All months consume the same immutable snapshot. Decode only the
    # matching month chart; weekly observations cannot repair year totals.
    month_section = f"{prefix}_{DATE_TYPE_MONTH}"
    month_chart = payload.get(month_section)
    daily_values: tuple[Mapping[date, float], Mapping[date, float]] = (
        dated_daily_chart_values(
            month_chart if isinstance(month_chart, dict) else {},
            month_section,
            stat_key,
        ),
        {},
    )
    total = 0.0
    completed_days_verified = today.replace(month=1, day=1) < today
    for month in range(1, today.month + 1):
        month_start = today.replace(month=month, day=1)
        value = (
            safe_float(values[month - 1])
            if (
                values
                and month <= len(values)
                and chart_window is not None
                and month_start <= chart_window[1]
            )
            else None
        )
        value = (
            value * scale
            if value is not None and value >= 0 and scale is not None
            else None
        )
        rebuilt = month_total_from_daily_evidence(
            payload,
            prefix,
            stat_key,
            window=(month_start, today),
            local_day_kwh=local_day_kwh,
            daily_values=(daily_values[0], daily_values[1]),
        )
        if rebuilt is not None:
            rebuilt_value, month_verified = rebuilt
            completed_days_verified &= month_verified or month_start == today
            value = (
                rebuilt_value
                if month_verified or value is None
                else max(value, rebuilt_value)
            )
        else:
            completed_days_verified = False
        if value is None:
            return None
        total += value
    return round(total, 5), completed_days_verified


def local_mqtt_opt_in(entry: object) -> bool:
    """Return whether the local MQTT receiver is opted in for this config entry.

    Single source for the opt-in so the options flow and the runtime start path
    cannot disagree. ``config_flow._current_local_mqtt_options`` documents the
    same precedence. The canonical ``third_party_mqtt_enable`` option is the
    only form value; retired ``local_mqtt_enable`` copies are ignored.

    With no explicit choice, the receiver stays disabled exactly like the
    options flow's canonical third-party MQTT switch.
    """
    # Kein ``isinstance(..., Mapping)``: ``Mapping`` ist hier nur ein
    # TYPE_CHECKING-Import. ``in`` funktioniert fuer jedes Mapping, auch fuer
    # das ``MappingProxyType`` von ``ConfigEntry.options``.
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}

    # Current options are authoritative. In particular, a canonical False may
    # never be overturned by a stale legacy True left in the same mapping.
    if CONF_THIRD_PARTY_MQTT_ENABLE in options:
        parsed = safe_bool(options[CONF_THIRD_PARTY_MQTT_ENABLE])
        if parsed is not None:
            return parsed

    # Config-entry data is older than options, but its canonical key still wins
    # over the retired alias within that storage layer.
    if CONF_THIRD_PARTY_MQTT_ENABLE in data:
        parsed = safe_bool(data[CONF_THIRD_PARTY_MQTT_ENABLE])
        if parsed is not None:
            return parsed

    return DEFAULT_THIRD_PARTY_MQTT_ENABLE


def is_mirror_sensor_topic(hass: HomeAssistant | None, topic: str) -> bool:
    """Recognize canonical state topics with proven Jackery mirror ownership."""
    parts = topic.split("/")
    if (
        len(parts) != 5  # ruff: ignore[magic-value-comparison]
        or parts[0] != DOMAIN
        or parts[2] != "sensor"
        or parts[4] not in {"state", "availability"}
    ):
        return False
    unique_id = unquote(parts[3])
    if quote(unique_id, safe="") != parts[3]:
        return False
    if hass is None:
        return False
    runtime = hass.data.get(DOMAIN, {})
    if isinstance(runtime, dict):
        for bucket in runtime.values():
            publisher = (
                bucket.get(MQTT_SENSOR_PUBLISHER_RUNTIME_KEY)
                if isinstance(bucket, dict)
                else None
            )
            owns_topic = getattr(publisher, "owns_sensor_topic", None)
            if callable(owns_topic) and owns_topic(topic):
                return True
    entry = hass.config_entries.async_get_entry(parts[1])
    if entry is None or entry.domain != DOMAIN:
        return False
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    entity = registry.async_get(entity_id) if entity_id is not None else None
    return entity is not None and entity.config_entry_id == entry.entry_id


def is_mirror_update_topic(hass: HomeAssistant | None, topic: str) -> bool:
    """Recognize only update mirror topics owned by a registered native entity."""
    parts = topic.split("/")
    if (
        len(parts) != 5  # ruff: ignore[magic-value-comparison]
        or parts[0] != DOMAIN
        or parts[2] != "update"
        or parts[4] not in {"state", "availability", "set", "attributes"}
        or hass is None
    ):
        return False
    unique_id = unquote(parts[3])
    if quote(unique_id, safe="") != parts[3]:
        return False
    entry = hass.config_entries.async_get_entry(parts[1])
    if entry is None or entry.domain != DOMAIN:
        return False
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("update", DOMAIN, unique_id)
    entity = registry.async_get(entity_id) if entity_id is not None else None
    return entity is not None and entity.config_entry_id == entry.entry_id


def app_energy_unit_scale(source: Mapping[str, Any]) -> float | None:
    """Return the factor that converts a stat payload's values into kWh.

    The app stat bodies carry their own ``unit`` field (default ``"kWh"``, see
    ``CtStatApi.Bean``/``EpsStatApi.Bean``). Treating every non-kWh answer as
    unusable silently dropped valid Wh payloads, while passing them through
    unscaled would inflate them by 1000.

    Missing/unknown units default to kWh (1.0) to match App behavior —
    the App treats absent unit as kWh. This fixes CT/EPS week/month/year
    energy sensors that were silently dropped due to missing unit field.

    "W" (watts) is explicitly NOT an energy unit - it's a power unit.
    Returning None signals the payload is invalid for energy purposes.

    Returns:
        float | None: ``1.0`` for kWh (or missing unit), ``0.001`` for Wh,
        ``None`` for invalid units like "W".
    """
    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if not unit or unit == APP_UNIT_KWH:
        return 1.0
    if unit == APP_UNIT_WH:
        return 1 / _WATTS_PER_KILOWATT
    # "W" is a power unit, not an energy unit - explicitly invalid
    if unit == "w":
        return None
    # Unknown unit → assume kWh (App default), don't discard data
    _LOGGER.debug("Unknown energy unit %r, assuming kWh", unit)
    return 1.0


def config_entry_bool_option(entry: object, key: str, default: bool) -> bool:
    """Resolve a boolean configuration option.

    Fall back to legacy entry data when options are absent.

    Parameters:
        entry (Any): Config entry-like object with optional `options` and legacy `data`
        mappings.
        key (str): Option name to look up.
        default (bool): Value to return when the option is not present or cannot be
        parsed.

    Returns:
        bool: The resolved boolean value (`true` or `false`), or `default` if the value
        is missing or not parseable.
    """
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    # Check if key exists in either options or data (explicitly set)
    if key in options:
        value = options[key]
    elif key in data:
        value = data[key]
    else:
        # Key not present anywhere - return default
        return default
    parsed = safe_bool(value)
    return default if parsed is None else parsed


def config_entry_str_option(entry: object, key: str, default: str) -> str:
    """Resolve a string configuration option from a config entry.

    Fall back to legacy entry data and a provided default.

    Looks up `key` first in `entry.options`, then in `entry.data`, and returns the
    resolved value coerced to `str`. If the resolved value is `None`, returns `default`.

    Parameters:
        entry (Any): Configuration entry object that may have `.options` and `.data`
        mappings.
        key (str): Option key to look up.
        default (str): Default string to return when the option is not set or resolves
        to `None`.

    Returns:
        str: The resolved option value coerced to `str`, or `default` when unset.
    """
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    value = options.get(key)
    if value is None:
        value = data.get(key, default)
    if value is None:
        return default
    return str(value)


def config_entry_int_option(entry: object, key: str, default: int) -> int:
    """Retrieve an integer option from a config entry.

    Fall back to legacy setup data when the option is absent.

    Parameters:
        entry (Any): Config entry-like object with optional `options` and `data`
        mappings.
        key (str): Option key to read from `entry.options` or `entry.data`.
        default (int): Value to return when the option is missing or cannot be converted
        to an int.

    Returns:
        int: The resolved integer option or `default` if not present or not convertible.
    """
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    value = options.get(key)
    if value is None:
        value = data.get(key, default)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def subdevice_branding(scan_name: object) -> tuple[str | None, str | None]:
    """Return manufacturer and model label for a documented subdevice `scan_name`.

    Looks up `scan_name` in the internal accessory catalog and returns a tuple
    (manufacturer, model_label). If `scan_name` is not a non-empty string or
    is not found in the catalog, returns `(None, None)` so callers may fall
    back to other payload fields.

    Returns:
        tuple[str | None, str | None]: `(manufacturer, model_label)` or
        `(None, None)` when unknown or invalid.
    """
    if not isinstance(scan_name, str) or not scan_name:
        return None, None
    canonical = next(
        (
            name
            for name in SUBDEVICE_SCAN_NAME_LABELS
            if name.casefold() == scan_name.casefold()
        ),
        None,
    )
    if canonical is None:
        return None, None
    manufacturer = SUBDEVICE_SCAN_NAME_MANUFACTURERS.get(canonical)
    label = SUBDEVICE_SCAN_NAME_LABELS.get(canonical)
    return manufacturer, label


def utc_now() -> datetime:
    """Get the current UTC time as a timezone-aware datetime.

    Returns:
        The current UTC datetime with tzinfo set to UTC.
    """
    return datetime.now(UTC)


def _datetime_from_timestamp(value: float | str) -> datetime:
    """Return one finite seconds-or-milliseconds timestamp in UTC."""
    try:
        timestamp = float(value)
    except (OverflowError, ValueError) as err:
        msg = f"invalid UTC timestamp: {value!r}"
        raise ValueError(msg) from err
    if not math.isfinite(timestamp):
        msg = f"invalid UTC timestamp: {value!r}"
        raise ValueError(msg)
    if abs(timestamp) >= _MILLIS_TIMESTAMP_THRESHOLD:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, UTC)
    except (OSError, OverflowError, ValueError) as err:
        msg = f"invalid UTC timestamp: {value!r}"
        raise ValueError(msg) from err


def parse_utc_datetime(
    value: float | str | datetime,
) -> datetime:  # parsed timestamp types
    """Parse timestamp representations into a timezone-aware UTC datetime.

    Parameters:
        value: Datetime, seconds/milliseconds timestamp, numeric string or ISO-8601
            string. A trailing ``Z`` is accepted.

    Returns:
        datetime: The parsed datetime normalized to UTC with tzinfo set.

    Raises:
        ValueError: If the input is an empty string, an unsupported type, or an invalid
        timestamp/ISO string.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = _datetime_from_timestamp(value)
    elif isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            msg = "timestamp must not be empty"
            raise ValueError(msg)
        try:
            parsed = _datetime_from_timestamp(normalized)
        except ValueError:
            if normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError as err:
                msg = f"invalid UTC timestamp: {value!r}"
                raise ValueError(msg) from err
    else:
        msg = f"unsupported UTC timestamp: {value!r}"
        raise TypeError(msg)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def coordinator_entity_signature(  # ruff: ignore[too-many-locals] - one field per entity-shape component
    coordinator_data: dict[str, Any] | None,
) -> tuple[tuple[Any, ...], ...]:
    """Produce a deterministic coordinator-payload shape signature.

    The lightweight signature is used during entity setup.

    Parameters:
        coordinator_data (dict[str, Any] | None): Mapping of device IDs to their
        coordinator payloads; may be None.

    Returns:
        tuple[tuple[Any, ...], ...]: A tuple of per-device signature tuples. Each entry
        preserves the device ID and includes,
        in order: a tuple of smart-plug serials, battery pack count, meter head count, a
        boolean indicating presence of an
        alarm payload, a boolean indicating presence of an OTA current version, a
        boolean indicating presence of a CT meter. **Live property keys and stat
        section usability are intentionally excluded** so routine poll updates do not
        re-trigger dynamic entity setup (which causes entity_registry spam).
    """
    if not coordinator_data:
        return ()
    if hasattr(coordinator_data, "data"):
        coordinator_data = coordinator_data.data
    if not isinstance(coordinator_data, Mapping):
        return ()

    def _present_fields(item: dict[str, Any]) -> tuple[str, ...]:
        return tuple(sorted(str(key) for key in item if not str(key).startswith("_")))

    sig: list[Any] = []
    for dev_id in sorted(coordinator_data):
        payload = coordinator_data.get(dev_id) or {}
        discovery_source = ""
        for section_name in (PAYLOAD_DEVICE, PAYLOAD_DISCOVERY):
            section = payload.get(section_name) or {}
            if not isinstance(section, dict):
                continue
            raw_source = section.get(PAYLOAD_DISCOVERY_SOURCE)
            if raw_source not in {None, ""}:
                discovery_source = str(raw_source)
                break
        system = payload.get(PAYLOAD_SYSTEM) or payload.get(PAYLOAD_SYSTEM_META) or {}
        accessories = payload.get(FIELD_ACCESSORIES)
        if not isinstance(accessories, list) and isinstance(system, dict):
            accessories = system.get(FIELD_ACCESSORIES)
        valid_accessories = (
            [item for item in accessories if isinstance(item, dict)]
            if isinstance(accessories, list)
            else []
        )
        accessory_keys = tuple(
            sorted(
                (
                    str(
                        accessory.get(FIELD_DEV_TYPE)
                        or accessory.get(FIELD_DEVICE_TYPE)
                        or ""
                    ),
                    first_nonblank_text(
                        accessory.get(FIELD_DEVICE_SN),
                        accessory.get(FIELD_DEV_SN),
                        accessory.get(FIELD_SN),
                        accessory.get(FIELD_DEVICE_ID),
                        accessory.get(FIELD_ID),
                        accessory.get(FIELD_DEV_ID),
                        fallback=f"index_{index}",
                    ),
                    _present_fields(accessory),
                )
                for index, accessory in enumerate(valid_accessories, start=1)
            )
        )
        plugs = sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS))
        plug_keys = tuple(
            (smart_plug_serial(plug), _present_fields(plug)) for plug in plugs
        )
        packs = payload.get(PAYLOAD_BATTERY_PACKS) or []
        valid_packs = (
            [item for item in packs if isinstance(item, dict)]
            if isinstance(packs, list)
            else []
        )
        pack_keys = tuple(
            sorted(
                (
                    first_nonblank_text(
                        pack.get(FIELD_DEVICE_SN),
                        pack.get(FIELD_DEV_SN),
                        pack.get(FIELD_SN),
                        fallback=f"index_{index}",
                    ),
                    _present_fields(pack),
                )
                for index, pack in enumerate(valid_packs, start=1)
            )
        )
        meter_heads = sorted_meter_heads(payload.get(PAYLOAD_METER_HEADS))
        meter_keys = tuple(
            (meter_head_serial(meter_head), _present_fields(meter_head))
            for meter_head in meter_heads
        )
        circuit_keys = tuple(
            (
                stable_subdevice_key("breaker", circuit_id(circuit), index),
                _present_fields(circuit),
            )
            for index, circuit in enumerate(
                sorted_circuits(payload.get(PAYLOAD_CIRCUIT_PROPERTY)),
                start=1,
            )
        )
        subdevice_keys = tuple(
            (
                stable_subdevice_key(
                    "sub_device",
                    sub_device_serial(sub_device),
                    index,
                ),
                _present_fields(sub_device),
            )
            for index, sub_device in enumerate(
                sorted_sub_devices(payload.get(PAYLOAD_SUBDEVICES)),
                start=1,
            )
        )

        sig.append((
            dev_id,
            discovery_source,
            accessory_keys,
            plug_keys,
            pack_keys,
            meter_keys,
            circuit_keys,
            subdevice_keys,
            payload.get(PAYLOAD_ALARM) is not None,
            bool((payload.get(PAYLOAD_OTA) or {}).get(FIELD_CURRENT_VERSION)),
            payload.get(PAYLOAD_CT_METER) is not None,
            bool(payload.get(PAYLOAD_THIRD_PARTY_MQTT_CONFIG)),
        ))
    return tuple(sig)


def append_unique_entity[EntityT](
    entities: list[EntityT],
    seen_unique_ids: set[str],
    entity: EntityT,
) -> bool:
    """Add an entity only when its unique ID has not been seen.

    Otherwise skip it.

    If the entity has a `unique_id` that already exists in `seen_unique_ids`, the entity
    is not appended and a debug message is emitted.

    Returns:
        `True` if the entity was appended, `False` if it was skipped due to a duplicate
        `unique_id`.
    """
    uid = getattr(entity, "unique_id", None)
    if uid and uid in seen_unique_ids:
        return False
    if uid:
        seen_unique_ids.add(uid)
    entities.append(entity)
    return True


def validate_app_period_date_type(date_type: str) -> str:
    """Return a supported Jackery app period type or raise ValueError."""
    if date_type not in APP_PERIOD_DATE_TYPES:
        msg = f"Unsupported Jackery app period dateType: {date_type!r}"
        raise ValueError(msg)
    return date_type


def app_period_range(date_type: str, *, today: date | None = None) -> tuple[date, date]:
    """Compute the Jackery app's inclusive period bounds.

    The bounds correspond to the given period type.

    Parameters:
        date_type (str): One of the documented app period types (day, week, month,
        year).
        today (date | None): Reference date used to compute the period; defaults to the
        current local date.

    Returns:
        tuple[date, date]: (begin_date, end_date) for the requested period, inclusive.
    """
    date_type = validate_app_period_date_type(date_type)
    if today is None:
        today = datetime.now(UTC).astimezone().date()
    if date_type == DATE_TYPE_DAY:
        return today, today
    if date_type == DATE_TYPE_WEEK:
        begin = today - timedelta(days=today.weekday())
        return begin, begin + timedelta(days=6)
    if date_type == DATE_TYPE_MONTH:
        begin = today.replace(day=1)
        end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        return begin, end
    return today.replace(month=1, day=1), today.replace(month=12, day=31)


def _app_period_bound_to_date(value: str | date, *, field_name: str) -> date:
    """Return a validated ISO date bound for a Jackery app period request."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    normalized = str(value).strip()
    if not normalized:
        msg = f"Jackery app period {field_name} must not be empty"
        raise ValueError(msg)
    try:
        return date.fromisoformat(normalized)
    except ValueError as err:
        msg = (
            f"Jackery app period {field_name} must be an ISO date (YYYY-MM-DD): "
            f"{value!r}"
        )
        raise ValueError(msg) from err


def app_period_date_bounds(
    date_type: str,
    *,
    begin_date: str | date | None = None,
    end_date: str | date | None = None,
    today: date | None = None,
) -> tuple[str, str]:
    """Produce validated ISO-formatted app-period bounds.

    Return begin and end date strings for the specified period.

    Parameters:
        date_type (str): App period type (must be one of the module's supported date
        types).
        begin_date (str | date | None): Optional begin bound (ISO date string or date).
        When None, the period default begin is used.
        end_date (str | date | None): Optional end bound (ISO date string or date). When
        None, the period default end is used.
        today (date | None): Optional reference date used to compute period defaults
        when begin/end are omitted.

    Returns:
        tuple[str, str]: A pair of ISO date strings (begin_iso, end_iso).

    Raises:
        ValueError: If inputs are invalid for a date bound or if the resolved begin date
        is after the resolved end date.
    """
    default_begin, default_end = app_period_range(date_type, today=today)
    begin = _app_period_bound_to_date(
        default_begin if begin_date is None else begin_date,
        field_name=APP_REQUEST_BEGIN_DATE,
    )
    end = _app_period_bound_to_date(
        default_end if end_date is None else end_date,
        field_name=APP_REQUEST_END_DATE,
    )
    if begin > end:
        msg = (
            "Jackery app period beginDate must be before or equal to endDate: "
            f"{begin.isoformat()} > {end.isoformat()}"
        )
        raise ValueError(msg)
    return begin.isoformat(), end.isoformat()


def app_period_request_kwargs(
    date_type: str, *, today: date | None = None
) -> dict[str, str]:
    """Return method kwargs for documented app-period API calls."""
    begin, end = app_period_date_bounds(date_type, today=today)
    return {
        APP_REQUEST_DATE_TYPE_ALT: date_type,
        APP_REQUEST_BEGIN_DATE_ALT: begin,
        APP_REQUEST_END_DATE_ALT: end,
    }


def app_month_request_kwargs(year: int, month: int) -> dict[str, str]:
    """Return method kwargs for one explicit calendar-month app request."""
    if month < 1 or month > _MONTHS_PER_YEAR:
        msg = f"Unsupported Jackery app month: {month!r}"
        raise ValueError(msg)
    first = date(year, month, 1)
    last = first.replace(day=calendar.monthrange(year, month)[1])
    begin, end = app_period_date_bounds(
        DATE_TYPE_MONTH,
        begin_date=first,
        end_date=last,
    )
    return {
        APP_REQUEST_DATE_TYPE_ALT: DATE_TYPE_MONTH,
        APP_REQUEST_BEGIN_DATE_ALT: begin,
        APP_REQUEST_END_DATE_ALT: end,
    }


def app_year_request_kwargs(year: int) -> dict[str, str]:
    """Return method kwargs for one explicit calendar-year app request."""
    first = date(year, 1, 1)
    last = date(year, 12, 31)
    begin, end = app_period_date_bounds(
        DATE_TYPE_YEAR,
        begin_date=first,
        end_date=last,
    )
    return {
        APP_REQUEST_DATE_TYPE_ALT: DATE_TYPE_YEAR,
        APP_REQUEST_BEGIN_DATE_ALT: begin,
        APP_REQUEST_END_DATE_ALT: end,
    }


def statistics_http_backfill_dates(
    today: date,
    *,
    window_days: int,
    include_current_year: bool = False,
) -> list[date]:
    """Return completed local days covered by the automatic HTTP backfill.

    The automatic day-chart backfill repairs recently completed days by
    re-fetching their hourly app-stat curves over HTTP. Only fully
    *completed* local days qualify: ``today`` itself is always excluded
    because its buckets are still accumulating and are covered by the
    live current-day import path instead.

    Args:
        today: The current local calendar day (the running, incomplete day).
        window_days: How many recent completed days to cover when
            ``include_current_year`` is ``False``. Values ``<= 0`` yield an
            empty window.
        include_current_year: When ``False`` (the default) the window starts
            ``window_days`` days before ``today`` (a rolling recent window).
            When ``True`` the window instead starts at January 1st of
            ``today``'s calendar year, covering every completed day so far
            this year.

    Returns:
        Completed local days in ascending order, from the window start
        through ``today - 1`` (``today`` itself is always excluded).
    """
    end_day = today - timedelta(days=1)
    if include_current_year:
        start_day = today.replace(month=1, day=1)
    else:
        start_day = today - timedelta(days=max(0, window_days))
    if start_day > end_day:
        return []
    return [
        start_day + timedelta(days=offset)
        for offset in range((end_day - start_day).days + 1)
    ]


def historical_day_payload_from_sources(
    section_sources: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Convert fetched section-source dicts into the normal day payload shape.

    ``section_sources`` is keyed by the app-stat *section prefix* (for
    example ``device_battery_stat`` or ``home_trends``) as produced by the
    historical day-chart fetch. The day-chart importer, however, looks each
    source up under the same section keys the live day payload uses: a
    per-metric ``"{prefix}_day"`` key (for example ``device_battery_stat_day``)
    for regular stat sections, and the trend section name for home trends.
    This remaps each prefix onto that payload key so downstream chart
    conversion resolves the source unchanged.

    Args:
        section_sources: Gated section sources keyed by section prefix.

    Returns:
        The same sources re-keyed into normal day-payload section keys.
        Empty input (or entries with empty values) yields empty output.
    """
    payload: dict[str, dict[str, Any]] = {}
    for section_prefix, source in section_sources.items():
        if not source:
            continue
        section = (
            PAYLOAD_HOME_TRENDS
            if section_prefix == APP_SECTION_HOME_TRENDS
            else f"{section_prefix}_{DATE_TYPE_DAY}"
        )
        payload[section] = source
    return payload


def filter_completed_app_points(
    points: list[Any],
    date_type: str,
    reset_period: str,
    today: date,
) -> list[Any]:
    """Filter app points to completed buckets for entity-stat imports.

    Day points are always included. For longer periods, only points
    whose bucket date is strictly before today are included.
    """
    if date_type == "day":
        return points
    completed: list[Any] = []
    for point in points:
        start = point.start_date
        point_date = start.date() if isinstance(start, datetime) else start
        if not isinstance(point_date, date):
            continue
        if reset_period in {"day", "week", "month"}:
            if point_date >= today:
                continue
        elif reset_period == "year" and (
            point_date.year,
            point_date.month,
        ) >= (today.year, today.month):
            continue
        completed.append(point)
    return completed


def parse_statistics_backfill_date(value: object) -> date | None:
    """Parse a persisted ISO date for statistics repair decisions."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def statistics_current_year_recovery_needed(
    *,
    last_success: date,
    last_repair: date | None,
    failed_bucket_count: int,
    today: date,
) -> bool:
    """Return True when an old success marker may have skipped history.

    Older builds could persist ``last_successful_import_date`` from the
    current snapshot while the historical month/year repair never ran
    because a live MQTT window returned early. In that state the normal
    month-boundary branch would never revisit elapsed months of the same
    calendar year. Use ``last_repair_date`` as the recovery marker: once
    a repair has run in the same month as ``last_success``, the one-time
    current-year recovery is complete.
    """
    if today.month == 1:
        return False
    if last_success.year != today.year:
        return False
    if failed_bucket_count > 0:
        return last_repair is None or last_repair < today
    if last_repair is None:
        return True
    last_success_month = last_success.replace(day=1)
    return last_repair < last_success_month


def iter_calendar_months(start_date: date, end_date: date) -> list[date]:
    """Return first-of-month dates intersecting an inclusive date range."""
    cursor = start_date.replace(day=1)
    end_month = end_date.replace(day=1)
    months: list[date] = []
    while cursor <= end_month:
        months.append(cursor)
        if cursor.month == _MONTHS_PER_YEAR:
            cursor = cursor.replace(year=cursor.year + 1, month=1)
        else:
            cursor = cursor.replace(month=cursor.month + 1)
    return months


def iter_calendar_weeks(start_date: date, end_date: date) -> list[date]:
    """Return Monday week starts intersecting an inclusive date range."""
    cursor = start_date - timedelta(days=start_date.weekday())
    end_week = end_date - timedelta(days=end_date.weekday())
    weeks: list[date] = []
    while cursor <= end_week:
        weeks.append(cursor)
        cursor += timedelta(days=7)
    return weeks


def iter_calendar_years(start_date: date, end_date: date) -> list[int]:
    """Return calendar years intersecting an inclusive date range."""
    return list(range(start_date.year, end_date.year + 1))


def app_chart_period_meta(date_type: str) -> tuple[str, str] | None:
    """Return the external bucket id and label for an app chart period."""
    for period_date_type, bucket, bucket_label in APP_CHART_STAT_PERIODS:
        if period_date_type == date_type:
            return bucket, bucket_label
    return None


def app_chart_name_prefix(device_id: str, payload: dict[str, Any]) -> str:
    """Return a stable, user-readable app chart statistic name prefix."""
    return (
        (payload.get(PAYLOAD_SYSTEM) or {}).get(FIELD_DEVICE_NAME)
        or (payload.get(PAYLOAD_DISCOVERY) or {}).get(FIELD_DEVICE_NAME)
        or (payload.get(PAYLOAD_PROPERTIES) or {}).get(FIELD_WNAME)
        or f"Jackery {device_id}"
    )


def _explicit_day_curve_minutes(labels: object, sample_count: int) -> list[int] | None:
    """Resolve an explicit axis without inventing missing or damaged timestamps."""
    if not isinstance(labels, list) or len(labels) != sample_count:
        return None
    day_samples = _MINUTES_PER_DAY // _DAY_POWER_SAMPLE_MINUTES
    minutes: list[int] = []
    seen: set[int] = set()
    for index, label in enumerate(labels):
        minute = _parse_day_chart_minute(label)
        if (
            label == "24:00"
            and sample_count == day_samples
            and index == day_samples - 1
        ):
            minute = index * _DAY_POWER_SAMPLE_MINUTES
        if minute is None or minute in seen:
            return None
        seen.add(minute)
        minutes.append(minute)
    return minutes


def _is_day_curve_series_key(key: str) -> bool:
    """Identify one chart vector without confusing period scalar totals."""
    return key == APP_CHART_SERIES_Y or (key.startswith("y") and key[1:].isdigit())


def _day_curve_sample_axis(payload: dict[str, Any]) -> list[int] | None:
    """Use explicit five-minute timestamps or an entire implicit 288-slot day."""
    values = [
        series
        for key, series in payload.items()
        if _is_day_curve_series_key(key)
        and isinstance(series, list)
        # Empty/null-only vectors have no independent timestamp evidence;
        # do not let them invalidate a measured sibling channel's axis.
        and any(safe_float(value) is not None for value in series)
    ]
    if not values:
        return None
    labels = payload.get(APP_CHART_LABELS)
    if isinstance(labels, list):
        axis = _explicit_day_curve_minutes(labels, len(labels))
    elif labels is None and all(
        len(series) == _MINUTES_PER_DAY // _DAY_POWER_SAMPLE_MINUTES
        for series in values
    ):
        axis = list(range(0, _MINUTES_PER_DAY, _DAY_POWER_SAMPLE_MINUTES))
    else:
        return None
    if (
        axis is None
        or any(minute % _DAY_POWER_SAMPLE_MINUTES for minute in axis)
        or any(len(series) != len(axis) for series in values)
    ):
        return None
    return axis


def supplement_day_curve_samples(  # ruff: ignore[too-many-return-statements, too-many-branches, too-many-locals]
    cloud: dict[str, Any],
    local: dict[str, Any],
    *,
    series_keys: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Fill only missing cloud five-minute positions from a matching source.

    Retain every observed cloud value (including zero) and its original
    metadata. This pure transformation does not edit either input or invent
    missing samples. Actual sources are retained independently by the ingest
    layer. Unit, date, owner and five-minute axis must agree first.
    """
    cloud_unit = str(cloud.get(APP_STAT_UNIT) or "W").strip().lower()
    local_unit = str(local.get(APP_STAT_UNIT) or "W").strip().lower()
    if cloud_unit != local_unit:
        return cloud
    for owner in ("deviceId", "deviceSn", "systemId", "systemSn"):
        if owner in cloud and owner in local and cloud[owner] != local[owner]:
            return cloud
    cloud_request = cloud.get(APP_REQUEST_META)
    local_request = local.get(APP_REQUEST_META)
    if not isinstance(cloud_request, dict) or not isinstance(local_request, dict):
        return cloud
    for field in (APP_REQUEST_DATE_TYPE, APP_REQUEST_BEGIN_DATE, APP_REQUEST_END_DATE):
        if cloud_request.get(field) != local_request.get(field):
            return cloud
    axis_cloud = _day_curve_sample_axis(cloud)
    axis_local = _day_curve_sample_axis(local)
    if axis_cloud is None or axis_local is None:
        return cloud
    cloud_minutes = set(axis_cloud)
    local_minutes = set(axis_local)
    all_minutes = sorted(cloud_minutes | local_minutes)
    if series_keys is None:
        keys = tuple(
            key
            for key in sorted(cloud.keys() | local.keys())
            if _is_day_curve_series_key(key)
        )
    else:
        keys = series_keys
    merged: dict[str, Any] = dict(cloud)
    changed = False
    for key in keys:
        own_series = cloud.get(key)
        extra_series = local.get(key)
        if not isinstance(extra_series, list):
            continue
        own = (
            dict(zip(axis_cloud, own_series, strict=True))
            if isinstance(own_series, list) and len(own_series) == len(axis_cloud)
            else {}
        )
        extra = dict(zip(axis_local, extra_series, strict=True))
        filled = dict(own)
        for minute, value in extra.items():
            old = filled.get(minute)
            source_missing = old is None or (isinstance(old, str) and not old.strip())
            extra_observed = value is not None and not (
                isinstance(value, str) and not value.strip()
            )
            if source_missing and extra_observed:
                filled[minute] = value
                changed = True
        if filled:
            merged[key] = [filled.get(minute) for minute in all_minutes]
    if not changed:
        return cloud
    if all_minutes != axis_cloud:
        merged[APP_CHART_LABELS] = [
            f"{minute // 60:02}:{minute % 60:02}" for minute in all_minutes
        ]
        # Preserve the remaining cloud series on the expanded timestamp axis.
        for key, series in cloud.items():
            if (
                key not in keys
                and _is_day_curve_series_key(key)
                and isinstance(series, list)
                and len(series) == len(axis_cloud)
            ):
                own = dict(zip(axis_cloud, series, strict=True))
                merged[key] = [own.get(minute) for minute in all_minutes]
    return merged


def supplement_pv_day_curve(
    source: dict[str, Any], fallback: dict[str, Any]
) -> dict[str, Any]:
    """Only supplement missing aggregate PV samples, never replace channels."""
    return supplement_day_curve_samples(
        source, fallback, series_keys=(APP_CHART_SERIES_Y,)
    )


# Planner and Recorder verification must agree on an unchanged aggregate.
STATISTICS_AGGREGATE_MATCH_TOLERANCE_KWH: Final = 0.0005
_GAP_FILL_TOLERANCE_KWH: Final = STATISTICS_AGGREGATE_MATCH_TOLERANCE_KWH


_STATISTIC_FLOAT_RESIDUE_KWH: Final = 0.000001


_SECONDS_PER_HOUR: Final = 3600


class StatisticRow(NamedTuple):
    """One stored hourly statistic row of an energy sensor."""

    sum: float
    state: float | None
    last_reset_ts: float | None


@dataclass(frozen=True, slots=True)
class StatisticDayReconcile:
    """Hourly rows for one closed day plus the sum shift for all later rows."""

    rows: list[dict[str, Any]]
    shift_start: float | None
    shift: float


def _statistic_counter_contains_energy(
    following: StatisticRow, rebuilt: StatisticRow
) -> bool:
    """Require both counter state and sum to corroborate outage energy."""
    return (
        following.last_reset_ts == rebuilt.last_reset_ts
        and following.state is not None
        and rebuilt.state is not None
        and following.state >= rebuilt.state
        and following.sum >= rebuilt.sum
    )


def _statistic_day_energies(
    hours: list[float],
    hourly_energy: Mapping[float, float],
    existing: Mapping[float, StatisticRow],
) -> list[float] | None:
    """Resolve observed energy or a consecutive recorded increment for each hour.

    Returns:
        list[float] | None: Resolved energies, or None for an unresolved gap.
    """
    energies: list[float] = []
    for hour in hours:
        energy = hourly_energy.get(hour)
        if energy is None:
            stored = existing.get(hour)
            prior = existing.get(hour - _SECONDS_PER_HOUR)
            if stored is None or prior is None:
                return None
            energy = stored.sum - prior.sum
        if not math.isfinite(energy) or energy < -_GAP_FILL_TOLERANCE_KWH:
            return None
        energies.append(max(0.0, energy))
    return energies


def plan_statistic_partial_day_reconcile(
    hours: list[float],
    hourly_energy: Mapping[float, float],
    existing: Mapping[float, StatisticRow],
    reset_start: Callable[[float], float],
) -> StatisticDayReconcile:
    """Add only measured hours whose sum has an adjacent native Recorder anchor.

    An incomplete five-minute curve is evidence for its *observed* full hours,
    not for absent hours. Require a stored predecessor (or a new row derived
    from one), and refuse any insert contradicting an already recorded later
    sum/state. Never shift future statistics or infer a missing sum baseline.
    The caller must keep the unfinished historical day pending.
    """
    known = dict(existing)
    rows: list[dict[str, Any]] = []
    for hour in hours:
        if hour in existing:
            continue
        observed = hourly_energy.get(hour)
        if (
            observed is None
            or isinstance(observed, bool)
            or not math.isfinite(observed)
            or observed < 0
        ):
            continue
        prior = known.get(hour - _SECONDS_PER_HOUR)
        if prior is None:
            continue
        amount = round(observed, 5)
        total = round(prior.sum + amount, 5)
        period_start = reset_start(hour)
        if prior.last_reset_ts == period_start:
            state = round(prior.state + amount, 5) if prior.state is not None else None
        else:
            state = amount
        next_hour = min((at for at in known if at > hour), default=None)
        if next_hour is not None:
            following = known[next_hour]
            if total > following.sum + _GAP_FILL_TOLERANCE_KWH:
                continue
            if (
                state is not None
                and following.state is not None
                and following.last_reset_ts == period_start
                and state > following.state + _GAP_FILL_TOLERANCE_KWH
            ):
                continue
        known[hour] = StatisticRow(total, state, period_start)
        rows.append({
            "start": datetime.fromtimestamp(hour, UTC),
            "sum": total,
            "state": state,
            "last_reset": datetime.fromtimestamp(period_start, UTC),
        })
    return StatisticDayReconcile(rows=rows, shift_start=None, shift=0.0)


def plan_statistic_day_reconcile(
    hours: list[float],
    hourly_energy: Mapping[float, float],
    existing: Mapping[float, StatisticRow],
    reset_start: Callable[[float], float],
    *,
    initialize_empty: bool = False,
) -> StatisticDayReconcile:
    """Plan a closed calendar bucket's hourly rows for a native energy sensor.

    ``hours`` are all hour starts (UTC seconds) of the closed local bucket;
    ``hourly_energy`` maps observed hours to kWh. Missing hours retain their
    recorded increment when consecutive stored rows establish it; without that
    evidence the bucket remains unresolved and no rewrite is planned.
    ``existing`` holds the sensor's stored rows of that day plus the nearest
    row before and after it.

    Every hour of the day is rewritten so its increase equals the curve,
    starting from the sum stored before the day; stored rows that already
    match are not rewritten. Later rows shift by the difference between the
    new and the stored end-of-day sum, so every later hour keeps its own
    increase (HA's adjust API, the same as "Adjust sum" in developer tools).
    Without a row before the day, the day is anchored backwards on the first
    later row and nothing shifts.
    A following counter in the same reset period can already contain the missing
    energy; its corroborating state and cumulative sum are then preserved.
    """
    day_start, day_end = hours[0], hours[-1] + _SECONDS_PER_HOUR
    energies = _statistic_day_energies(hours, hourly_energy, existing)
    if energies is None:
        return StatisticDayReconcile(rows=[], shift_start=None, shift=0.0)
    total = sum(energies)
    before = max((t for t in existing if t < day_start), default=None)
    after = min((t for t in existing if t >= day_end), default=None)
    if before is not None:
        base = existing[before].sum
    elif after is not None:
        base = existing[after].sum - total
    elif initialize_empty and not existing:
        base = 0.0
    else:
        return StatisticDayReconcile(rows=[], shift_start=None, shift=0.0)
    previous = existing.get(before) if before is not None else None
    cumulative = base
    rows: list[dict[str, Any]] = []
    for hour, energy in zip(hours, energies, strict=True):
        period_start = reset_start(hour)
        state = (
            previous.state or 0.0
            if previous is not None and previous.last_reset_ts == period_start
            else 0.0
        )
        cumulative += energy
        previous = StatisticRow(
            round(cumulative, 5), round(state + energy, 5), period_start
        )
        stored = existing.get(hour)
        sum_changed = (
            stored is None or abs(stored.sum - previous.sum) > _GAP_FILL_TOLERANCE_KWH
        )
        if stored is not None and not sum_changed and stored.state != previous.state:
            _LOGGER.debug(
                "Statistic hour %s matches sum; preserving sensor state %s (curve %s)",
                hour,
                stored.state,
                previous.state,
            )
        if (
            sum_changed
            or (
                stored is not None
                and 0.0 < abs(stored.sum - previous.sum) < _STATISTIC_FLOAT_RESIDUE_KWH
            )
            or (stored is not None and stored.last_reset_ts != period_start)
        ):
            rows.append({
                "start": datetime.fromtimestamp(hour, UTC),
                "sum": previous.sum,
                "state": stored.state
                if stored is not None and not sum_changed
                else previous.state,
                "last_reset": datetime.fromtimestamp(period_start, UTC),
            })
    shift = 0.0
    if before is not None and after is not None and previous is not None:
        shift = round(
            base + total - existing[max(t for t in existing if t < day_end)].sum, 5
        )
        # A period counter after an outage already includes the missing buckets.
        # Preserve that cumulative sum when its same-period state corroborates it.
        if shift > 0 and _statistic_counter_contains_energy(existing[after], previous):
            shift = 0.0
        if abs(shift) <= _GAP_FILL_TOLERANCE_KWH:
            shift = 0.0
    return StatisticDayReconcile(
        rows=rows,
        shift_start=after if shift else None,
        shift=shift,
    )


def plan_statistic_month_reconcile(
    hours: list[float],
    hourly_energy: Mapping[float, float],
    existing: Mapping[float, StatisticRow],
    reset_start: Callable[[float], float],
    *,
    initialize_empty: bool = False,
) -> StatisticDayReconcile:
    """Fill a closed month's aggregate residual without flattening measured hours.

    A matching recorded total needs no rewrite, including intermediate gaps.
    A positive residual is booked at the last missing hour; measured increments
    and sensor states survive. Conflicts without a gap require separate evidence.
    """
    total = sum(hourly_energy.values())
    bucket = {hour: existing[hour] for hour in hours if hour in existing}
    energy = {hours[-1]: total}
    if bucket:
        before = max((hour for hour in existing if hour < hours[0]), default=None)
        if before is None:
            return StatisticDayReconcile(rows=[], shift_start=None, shift=0.0)
        measured = existing[before].sum
        residual = total - (bucket[max(bucket)].sum - measured)
        missing = [hour for hour in hours if hour not in bucket]
        if abs(residual) <= _GAP_FILL_TOLERANCE_KWH:
            return StatisticDayReconcile(rows=[], shift_start=None, shift=0.0)
        if residual < 0 or not missing:
            _LOGGER.debug(
                "Month aggregate conflicts with recorded distribution; deferred"
            )
            return StatisticDayReconcile(rows=[], shift_start=None, shift=0.0)
        energy = {missing[-1]: residual}
        for hour, stored in bucket.items():
            if stored.sum < measured - _GAP_FILL_TOLERANCE_KWH:
                _LOGGER.debug("Month distribution has falling sums; deferred")
                return StatisticDayReconcile(rows=[], shift_start=None, shift=0.0)
            energy[hour] = max(0.0, stored.sum - measured)
            measured = stored.sum
    plan = plan_statistic_day_reconcile(
        hours,
        {hour: energy.get(hour, 0.0) for hour in hours},
        existing,
        reset_start,
        initialize_empty=initialize_empty,
    )
    for row in plan.rows:
        stored_row = bucket.get(row["start"].timestamp())
        if stored_row is not None:
            row["state"] = stored_row.state
    return plan


def stat_row_start(row: Mapping[str, Any]) -> float | None:
    """Return a statistics row start timestamp in seconds."""
    start = row.get("start")
    if isinstance(start, datetime):
        return start.timestamp()
    return safe_float(start)


def safe_float(
    value: object,
) -> float | None:  # numeric payload value
    """Parse a payload value into a Python float.

    Return None when the value cannot be interpreted.

    Parameters:
        value (Any): Input value from a Jackery payload. Accepts numbers, None, or
        strings (including numeric strings,
            optionally using a single comma as the decimal separator when no dot is
            present). Empty or malformed strings
            and unsupported types produce `None`.

    Returns:
        float_value (float | None): The parsed float on success, or `None` if `value` is
        `None` or cannot be converted.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return _parse_float_string(value)
    try:
        parsed = float(
            cast("SupportsFloat | SupportsIndex | str | bytes | bytearray", value)
        )
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_float_string(value: str) -> float | None:
    """Parse a payload string into a float, tolerating a single comma decimal.

    Parameters:
        value (str): Candidate numeric string.

    Returns:
        float | None: The parsed float, or None for empty/malformed strings.
    """
    candidate = value.strip()
    if not candidate:
        return None
    if "," in candidate and "." not in candidate:
        if candidate.count(",") != 1:
            return None
        candidate = candidate.replace(",", ".")
    try:
        parsed = float(candidate)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def safe_int(value: object) -> int | None:  # integral payload value
    """Convert a value to an integer when possible.

    Returns None for a None input or when the value cannot be converted to an integer.

    Returns:
        int: The converted integer if successful, `None` otherwise.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        finite_integral = math.isfinite(value) and value.is_integer()
        return int(value) if finite_integral else None
    if isinstance(value, str):
        candidate = value.strip()
        try:
            return int(candidate)
        except ValueError:
            return None
    return None


def _is_wifi_scan_envelope(value: dict[Any, Any]) -> bool:
    """Recognize App Wi-Fi-list envelopes without conflating OTA field `f`."""
    action = value.get("flags", value.get("actionId"))
    return value.get("cmd") == 1 and (
        action in {5, 254, 3001} or value.get("kind") == "ble"
    )


def _payload_debug_redacted(  # ruff: ignore[too-many-return-statements]
    value: object,
    *,
    unredacted: bool = False,
    wifi_scan: bool = False,
) -> object:  # recursive JSON walker over payload
    """Create a JSON-serializable copy of `value` with sensitive fields redacted.

    Default redaction is case-insensitive. Explicit raw capture preserves values.
    Otherwise keys in `REDACT_KEYS` are replaced recursively with `REDACTED_VALUE`;
    tuples become lists so the resulting structure can be serialized as JSON.

    Parameters:
        value (Any): The input payload to redact.

    Returns:
        Any: A redacted, JSON-serializable representation of `value`.
    """
    if unredacted:
        # Raw diagnostics still need detached containers and JSON-compatible
        # keys/tuples, but do not need per-dictionary Wi-Fi/redaction scanning.
        if isinstance(value, dict):
            return {
                str(key): _payload_debug_redacted(item, unredacted=True)
                for key, item in value.items()
            }
        if isinstance(value, list | tuple):
            return [_payload_debug_redacted(item, unredacted=True) for item in value]
        return value
    if isinstance(value, dict):
        scan_frame = _is_wifi_scan_envelope(value)
        scan_context = wifi_scan or scan_frame or value.get("section") == "wifi_list"
        return {
            str(key): REDACTED_VALUE
            if not unredacted
            and (
                str(key).casefold() in _REDACT_KEYS_CASEFOLD
                or (scan_context and str(key) == "f" and isinstance(item, list))
                or (scan_frame and str(key) in {"body_hex", "raw_hex"})
            )
            else _payload_debug_redacted(
                item,
                unredacted=unredacted,
                wifi_scan=scan_context or str(key) == "wifi_list",
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _payload_debug_redacted(item, unredacted=unredacted, wifi_scan=wifi_scan)
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            _payload_debug_redacted(item, unredacted=unredacted, wifi_scan=wifi_scan)
            for item in value
        ]
    if isinstance(value, str) and not unredacted:
        return re.sub(
            r"(https?://[^\s<>\"'?]+)\?[^\s<>\"']+",
            lambda match: f"{match.group(1)}?{REDACTED_VALUE}",
            value,
            flags=re.IGNORECASE,
        )
    return value


def _sensitive_text_values(
    value: object, *, min_length: int = _MIN_REDACT_LITERAL_LENGTH
) -> frozenset[str]:
    """Collect non-trivial scalar values stored below sensitive keys."""
    sensitive_values: set[str] = set()

    def _collect_scalar(item: object) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                _collect_scalar(nested)
            return
        if isinstance(item, list | tuple):
            for nested in item:
                _collect_scalar(nested)
            return
        if isinstance(item, bool) or item is None:
            return
        if isinstance(item, str | int | float):
            text = str(item)
            if len(text) >= min_length and text != REDACTED_VALUE:
                sensitive_values.add(text)

    def _walk(item: object, *, wifi_scan: bool = False) -> None:
        if isinstance(item, dict):
            scan_context = (
                wifi_scan
                or _is_wifi_scan_envelope(item)
                or item.get("section") == "wifi_list"
            )
            for key, nested in item.items():
                if str(key).casefold() in _REDACT_KEYS_CASEFOLD or (
                    scan_context and str(key) == "f" and isinstance(nested, list)
                ):
                    _collect_scalar(nested)
                else:
                    _walk(nested, wifi_scan=scan_context or str(key) == "wifi_list")
            return
        if isinstance(item, list | tuple):
            for nested in item:
                _walk(nested, wifi_scan=wifi_scan)

    _walk(value)
    return frozenset(sensitive_values)


def _redact_sensitive_text_values(
    value: object,
    sensitive_values: frozenset[str],
) -> object:
    """Scrub known sensitive literals echoed under otherwise safe keys."""
    if isinstance(value, dict):
        original_keys = {str(key) for key in value}
        result: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            target = name
            if name.casefold() not in (
                _VERIFICATION_IDENTITY_KEYS
                | _VERIFICATION_STRUCTURAL_KEYS
                | _REDACTION_EVENT_KEYS
            ):
                for sensitive in sorted(sensitive_values, key=len, reverse=True):
                    target = REDACTED_VALUE.join(
                        part.replace(sensitive, REDACTED_VALUE)
                        for part in target.split(REDACTED_VALUE)
                    )
                if target != name:
                    base = target
                    suffix = 2
                    while target in original_keys or target in result:
                        target = f"{base}#{suffix}"
                        suffix += 1
            result[target] = _redact_sensitive_text_values(item, sensitive_values)
        return result
    if isinstance(value, list | tuple):
        return [_redact_sensitive_text_values(item, sensitive_values) for item in value]
    if isinstance(value, str):
        redacted = value
        for sensitive in sorted(sensitive_values, key=len, reverse=True):
            redacted = REDACTED_VALUE.join(
                part.replace(sensitive, REDACTED_VALUE)
                for part in redacted.split(REDACTED_VALUE)
            )
        return redacted
    if (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and str(value) in sensitive_values
    ):
        return REDACTED_VALUE
    return value


_VERIFICATION_IDENTITY_KEYS: Final = frozenset({
    "id",
    "deviceid",
    "devicesn",
    "devsn",
    "sn",
    "systemid",
    "serial_number",
    "unique_id",
    "entry_id",
    "request_id",
    "timestamp",
    "topic",
})
_VERIFICATION_STRUCTURAL_KEYS: Final = frozenset({
    "code",
    "data",
    "msg",
    "message",
    "status",
})
_REDACTION_EVENT_KEYS: Final = frozenset({
    "kind",
    "path",
    "params",
    "request_body",
    "response",
})


def _redact_verification_echo(
    value: object, secret: str, *, root: bool = True
) -> object:
    """Remove a verification code from response text without changing IDs or numbers."""
    if isinstance(value, dict):
        original_keys = {str(key) for key in value}
        result: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            key_kind = name.casefold()
            target = name
            if key_kind not in (
                _VERIFICATION_IDENTITY_KEYS | _VERIFICATION_STRUCTURAL_KEYS
            ):
                target = REDACTED_VALUE.join(
                    part.replace(secret, REDACTED_VALUE)
                    for part in name.split(REDACTED_VALUE)
                )
                if target != name:
                    base = target
                    suffix = 2
                    while target in original_keys or target in result:
                        target = f"{base}#{suffix}"
                        suffix += 1
            preserve = key_kind in _VERIFICATION_IDENTITY_KEYS or (
                root
                and key_kind == "code"
                and isinstance(item, str)
                and item.isdecimal()
            )
            result[target] = (
                item
                if preserve
                else _redact_verification_echo(item, secret, root=False)
            )
        return result
    if isinstance(value, list | tuple):
        return [_redact_verification_echo(item, secret, root=False) for item in value]
    if isinstance(value, str) and value != REDACTED_VALUE:
        return REDACTED_VALUE.join(
            part.replace(secret, REDACTED_VALUE) for part in value.split(REDACTED_VALUE)
        )
    return value


def _redact_http_short_secret_messages(
    value: object, secrets: frozenset[str]
) -> object:
    """Protect short secrets in diagnostic messages without changing data fields."""
    if isinstance(value, dict):
        return {
            key: _redact_short_message(item, secrets)
            if str(key).casefold()
            in {"msg", "message", "error", "error_description", "detail"}
            else _redact_http_short_secret_messages(item, secrets)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact_http_short_secret_messages(item, secrets) for item in value]
    return value


def _redact_short_message(value: object, secrets: frozenset[str]) -> object:
    """Redact short known credentials from diagnostic content."""
    if isinstance(value, dict):
        return _redact_http_short_secret_messages(value, secrets)
    if isinstance(value, list | tuple):
        return [_redact_short_message(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    result: object = value
    for secret in sorted(secrets, key=len, reverse=True):
        result = _redact_verification_echo(result, secret)
    return result


def _redact_http_short_response(
    response: object, secrets: frozenset[str], status: object
) -> object:
    """Redact short credentials in HTTP diagnostics and error-only text bodies."""
    if isinstance(response, str):
        return _redact_short_message(response, secrets)
    code = (
        first_nonblank_int(response.get("code")) if isinstance(response, dict) else None
    )
    rejected = (
        (isinstance(status, int) and status >= HTTPStatus.BAD_REQUEST)
        or isinstance(response, list)
        or (code is not None and code != 0)
    )
    if not rejected:
        if not isinstance(response, dict):
            return response
        return {
            key: _redact_short_message(value, secrets)
            if str(key).casefold() in {"msg", "message"} and isinstance(value, str)
            else value
            for key, value in response.items()
        }
    result = _redact_http_short_secret_messages(response, secrets)
    if isinstance(result, list):
        return _redact_short_message(result, secrets)
    if isinstance(result, dict) and isinstance(result.get("data"), str | list):
        result["data"] = _redact_short_message(result["data"], secrets)
    return result


def redacted_json_safe_payload(
    value: object,
    *,
    sensitive_sources: tuple[object, ...] = (),
    unredacted: bool | None = None,
    entry: object | None = None,
) -> object:  # recursive JSON walker over payload
    """Produce a JSON-serializable payload with known sensitive Jackery fields redacted.

    The redaction is applied recursively to nested dicts/lists/tuples. Known
    sensitive values are also scrubbed when a transport error echoes them under
    a non-sensitive key such as ``last_error``. Additional raw sources may only
    add values to the redaction set. Explicit developer mode permits raw capture;
    `unredacted=False` always forces redaction, including in developer mode.

    Returns:
        Any: The input value converted into a JSON-safe structure with sensitive fields
        replaced by the module's redaction marker.
    """
    if unredacted is True or (unredacted is None and jackery_dev_mode_enabled(entry)):
        return _payload_debug_redacted(value, unredacted=True)
    sensitive_values = set(_sensitive_text_values(value))
    for source in sensitive_sources:
        sensitive_values.update(_sensitive_text_values(source))
    redacted = _payload_debug_redacted(value)
    original_event = value if isinstance(value, dict) else {}
    redacted_event = redacted if isinstance(redacted, dict) else {}
    request_body = original_event.get("request_body")
    redacted_body = redacted_event.get("request_body")
    if (
        original_event.get("kind") == "http"
        and original_event.get("path") in VERIFICATION_REQUEST_PATHS
        and isinstance(request_body, dict)
        and isinstance(redacted_body, dict)
    ):
        code_key = (
            "code"
            if original_event.get("path") == CHECK_VERIFY_CODE_PATH
            else "verificationCode"
        )
        verification_code = request_body.get(code_key)
        if (
            code_key in request_body
            and verification_code is not None
            and (not isinstance(verification_code, str) or verification_code)
        ):
            redacted_body[code_key] = REDACTED_VALUE
        if isinstance(verification_code, str) and verification_code:
            redacted_event["response"] = _redact_verification_echo(
                redacted_event.get("response"), verification_code
            )
    result = _redact_sensitive_text_values(
        redacted,
        frozenset(sensitive_values),
    )
    if (
        original_event.get("kind") == "http"
        and isinstance(result, dict)
        and "response" in result
    ):
        short_secrets = frozenset({
            secret
            for source in (
                *sensitive_sources,
                request_body,
                original_event.get("params"),
            )
            for secret in _sensitive_text_values(source, min_length=1)
            if len(secret) < _MIN_REDACT_LITERAL_LENGTH
        })
        if short_secrets:
            result["response"] = _redact_http_short_response(
                result["response"], short_secrets, original_event.get("status")
            )
    original_response = original_event.get("response")
    if (
        original_event.get("kind") == "http"
        and original_event.get("path") in VERIFICATION_REQUEST_PATHS
        and isinstance(original_response, dict)
        and isinstance(result, dict)
        and isinstance(result.get("response"), dict)
    ):
        status = original_response.get("code")
        if (isinstance(status, int) and not isinstance(status, bool)) or (
            isinstance(status, str) and status.isdecimal()
        ):
            result["response"]["code"] = status
    return result


def active_redact_keys(entry: object | None = None) -> frozenset[str]:
    """Keep default redaction unless this capture explicitly enables developer mode."""
    return frozenset() if jackery_dev_mode_enabled(entry) else REDACT_KEYS


def chart_series_debug(source: object) -> dict[str, Any]:
    """Produce diagnostics for chart-series arrays in an app payload.

    Parses each chart-series list found under the keys `APP_CHART_SERIES_Y`,
    `APP_CHART_SERIES_Y1`…`APP_CHART_SERIES_Y6`
    and records per-series diagnostics. For each series the diagnostics include the
    number of raw entries (`raw_count`),
    the sum of successfully parsed numeric values rounded to 5 decimals (`parsed_sum`,
    or `None` if no numeric values),
    and an `items` list describing each element with `index`, `raw`, `raw_type`, and
    `parsed_float`.

    Returns:
        dict[str, Any]: Mapping of chart-series keys to diagnostics objects as described
        above.
        When present in the source, includes top-level `labels` (from
        `APP_CHART_LABELS`) and `request`
        (from `APP_REQUEST_META`) entries.
    """
    if not isinstance(source, dict):
        return {}
    result: dict[str, Any] = {}
    for key in (
        APP_CHART_SERIES_Y,
        APP_CHART_SERIES_Y1,
        APP_CHART_SERIES_Y2,
        APP_CHART_SERIES_Y3,
        APP_CHART_SERIES_Y4,
        APP_CHART_SERIES_Y5,
        APP_CHART_SERIES_Y6,
    ):
        series = source.get(key)
        if not isinstance(series, list):
            continue
        parsed_items: list[dict[str, Any]] = []
        total = 0.0
        found = False
        for index, raw in enumerate(series):
            parsed = safe_float(raw)
            parsed_items.append({
                "index": index,
                "raw": raw,
                "raw_type": type(raw).__name__,
                "parsed_float": parsed,
            })
            if parsed is not None:
                total += parsed
                found = True
        result[key] = {
            "raw_count": len(series),
            "parsed_sum": round(total, 5) if found else None,
            "items": parsed_items,
        }
    if isinstance(source.get(APP_CHART_LABELS), list):
        result["labels"] = source.get(APP_CHART_LABELS)
    if isinstance(source.get(APP_REQUEST_META), dict):
        result["request"] = source.get(APP_REQUEST_META)
    return result


_PAYLOAD_DEBUG_WRITE_LOCK = threading.Lock()


class PayloadDebugEvent(dict[str, Any]):  # ruff: ignore[subclass-builtin] - dict callback compatibility
    """Keep capture policy and sensitive context out of the serialized event."""

    def __init__(
        self,
        values: dict[str, Any] | None = None,
        *,
        sensitive_sources: tuple[object, ...] = (),
        capture_enabled: bool | None = None,
        capture_unredacted: bool | None = None,
        capture_timestamp: str | None = None,
    ) -> None:
        """Retain private capture context without adding serialized fields."""
        super().__init__(values or {})
        self.sensitive_sources = sensitive_sources
        self.capture_enabled = capture_enabled
        self.capture_unredacted = capture_unredacted
        self.capture_timestamp = capture_timestamp


class PayloadDebugWriteError(OSError):
    """Report the committed record prefix of an interrupted capture write."""

    def __init__(self, error: OSError, records_written: int) -> None:
        """Retain the original I/O error and confirmed complete-record count."""
        super().__init__(*error.args)
        self.records_written = records_written


def _rotate_payload_debug_segment(path: Path) -> None:
    """Move the complete active log into the single previous-file slot."""
    destination = path.with_suffix(f"{PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX}{path.suffix}")
    path.replace(destination)


def _append_payload_debug_record(
    path: Path,
    event: dict[str, Any],
    size: int,
    unredacted: bool | None,
) -> int:
    """Append a complete record, restoring the old boundary on write failure."""
    capture_unredacted = getattr(event, "capture_unredacted", None)
    encoded = (
        json.dumps(
            redacted_json_safe_payload(
                event,
                sensitive_sources=getattr(event, "sensitive_sources", ()),
                unredacted=(
                    capture_unredacted if capture_unredacted is not None else unredacted
                ),
            ),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        + "\n"
    ).encode("utf-8")
    if size and size + len(encoded) > PAYLOAD_DEBUG_LOG_MAX_BYTES:
        _rotate_payload_debug_segment(path)
        size = 0
    with path.open("ab", buffering=0) as file:
        try:
            written = file.write(encoded)
        except OSError:
            file.truncate(size)
            raise
        if written != len(encoded):
            file.truncate(size)
            msg = "Short payload debug write"
            raise OSError(msg)
    return size + len(encoded)


def append_payload_debug_lines(
    path: str | Path,
    events: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    unredacted: bool | None = None,
) -> None:
    """Split capture files at the existing limit while retaining complete events."""
    if not events:
        return
    debug_path = Path(path)
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    with _PAYLOAD_DEBUG_WRITE_LOCK:
        size = debug_path.stat().st_size if debug_path.exists() else 0
        records_written = 0
        try:
            for event in events:
                size = _append_payload_debug_record(debug_path, event, size, unredacted)
                records_written += 1
        except OSError as err:
            raise PayloadDebugWriteError(err, records_written) from err


def append_payload_debug_line(
    path: str | Path,
    event: dict[str, Any],
) -> None:
    """Append one event with default redaction or explicitly enabled developer mode."""
    append_payload_debug_lines(path, (event,))


def safe_bool(
    value: object,
) -> bool | None:
    """Interpret a payload value as a boolean.

    Returns:
        `True` if the value represents a true state, `False` if it represents a false
        state, `None` if the value is `None` or cannot be interpreted.
    """
    result: bool | None = None
    if isinstance(value, bool):
        result = value
    elif isinstance(value, int):
        result = value != 0
    elif isinstance(value, float):
        result = int(value) != 0 if math.isfinite(value) else None
    elif isinstance(value, str):
        val = value.strip().lower()
        if val in {"1", "true", "on", "yes"}:
            result = True
        elif val in {"0", "false", "off", "no"}:
            result = False
        else:
            with contextlib.suppress(ValueError):
                result = int(value) != 0
    return result


def smart_plug_serial(plug: object) -> str | None:
    """Extract the serial number from a smart-plug subdevice payload.

    Parameters:
        plug (Any): A subdevice payload, expected to be a dict containing one of the
        serial fields.

    Returns:
        serial (str | None): The trimmed serial string from `FIELD_DEVICE_SN`,
        `FIELD_DEV_SN`, or `FIELD_SN`, or `None` if the input is not a dict or no serial
        is present.
    """
    if not isinstance(plug, dict):
        return None
    return first_nonblank_text(
        plug.get(FIELD_DEVICE_SN),
        plug.get(FIELD_DEV_SN),
        plug.get(FIELD_SN),
        plug.get(FIELD_DEVICE_ID),
        plug.get(FIELD_ID),
        plug.get(FIELD_DEV_ID),
    )


def normalize_mac_address(value: object) -> str | None:
    """Return a lowercase colon-separated MAC address, if the value is one."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    compact = re.sub(r"[^0-9a-f]", "", text)
    if len(compact) != _MAC_HEX_LENGTH:
        return None
    return ":".join(
        compact[index : index + 2] for index in range(0, _MAC_HEX_LENGTH, 2)
    )


def sorted_smart_plugs(plugs: object) -> list[dict[str, Any]]:
    """Return plug entries sorted by their serial numbers.

    Parameters:
        plugs (Any): Iterable expected to be a list of plug payloads; if not a list, an
        empty list is returned.

    Returns:
        list[dict[str, Any]]: The input entries that contain a serial (as determined by
        `smart_plug_serial`), sorted ascending by serial. Entries without a serial are
        omitted.
    """
    if not isinstance(plugs, list):
        return []
    entries: list[tuple[str, dict[str, Any]]] = []
    for entry in plugs:
        sn = smart_plug_serial(entry)
        if sn is None:
            continue
        entries.append((sn, entry))
    entries.sort(key=operator.itemgetter(0))
    return [entry for _, entry in entries]


def meter_head_serial(meter_head: object) -> str | None:
    """Extract the stable identity from a meter-head/collector payload."""
    if not isinstance(meter_head, dict):
        return None
    return first_nonblank_text(
        meter_head.get(FIELD_DEVICE_SN),
        meter_head.get(FIELD_DEV_SN),
        meter_head.get(FIELD_SN),
        meter_head.get(FIELD_DEVICE_ID),
        meter_head.get(FIELD_ID),
        meter_head.get(FIELD_DEV_ID),
    )


def smart_meter_identity(smart_meter: object) -> str | None:
    """Extract a stable serial/id identity from a CT smart-meter payload."""
    if not isinstance(smart_meter, dict):
        return None
    return first_nonblank_text(
        smart_meter.get(FIELD_DEVICE_SN),
        smart_meter.get(FIELD_SN),
        smart_meter.get(FIELD_MAC),
        smart_meter.get(FIELD_DEVICE_ID),
        smart_meter.get(FIELD_ID),
        smart_meter.get(FIELD_DEV_ID),
    )


def sorted_meter_heads(meter_heads: object) -> list[dict[str, Any]]:
    """Return meter-head entries sorted by stable serial/id values."""
    if not isinstance(meter_heads, list):
        return []
    entries: list[tuple[str, dict[str, Any]]] = []
    for entry in meter_heads:
        sn = meter_head_serial(entry)
        if sn is None:
            continue
        entries.append((sn, entry))
    entries.sort(key=operator.itemgetter(0))
    return [entry for _, entry in entries]


def circuit_id(circuit: object) -> str | None:
    """Extract the stable index identity from a circuit/breaker payload.

    Circuit entries come from MQTT ``QueryCircuitProperty`` payloads and are
    identified by their ``idx`` (circuit index). ``idx`` may be ``0``, so the
    presence test uses ``is None`` rather than truthiness.
    """
    if not isinstance(circuit, dict):
        return None
    return first_nonblank_text(
        circuit.get(FIELD_IDX),
        circuit.get(FIELD_ID),
    )


def sorted_circuits(circuits: object) -> list[dict[str, Any]]:
    """Return circuit/breaker entries sorted by stable index identity."""
    if not isinstance(circuits, list):
        return []
    entries: list[tuple[str, dict[str, Any]]] = []
    for entry in circuits:
        cid = circuit_id(entry)
        if cid is None:
            continue
        entries.append((cid, entry))
    entries.sort(key=operator.itemgetter(0))
    return [entry for _, entry in entries]


def sub_device_serial(sub_device: object) -> str | None:
    """Extract the app subdevice serial without substituting a cloud ID."""
    if not isinstance(sub_device, dict):
        return None
    return first_nonblank_text(
        sub_device.get(FIELD_DEVICE_SN),
        sub_device.get(FIELD_DEV_SN),
        sub_device.get(FIELD_SN),
    )


def sorted_sub_devices(sub_devices: object) -> list[dict[str, Any]]:
    """Return subdevice entries sorted by stable serial identity."""
    if not isinstance(sub_devices, list):
        return []
    entries: list[tuple[str, dict[str, Any]]] = []
    for entry in sub_devices:
        sn = sub_device_serial(entry)
        if sn is None:
            continue
        entries.append((sn, entry))
    entries.sort(key=operator.itemgetter(0))
    return [entry for _, entry in entries]


def stable_subdevice_key(prefix: str, identity: str | None, fallback_index: int) -> str:
    """Build a safe stable suffix for a subdevice unique/device id."""
    raw = str(identity or "").strip() or str(fallback_index)
    if prefix == "smart_meter" and (mac := normalize_mac_address(raw)) is not None:
        raw = mac.replace(":", "")
    normalized = _SUBDEVICE_ID_RE.sub("_", raw).strip("_").lower()
    return f"{prefix}_{normalized or fallback_index}"


def nonblank_text(value: object) -> str | None:
    """Return trimmed text for non-empty payload metadata."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def first_nonblank_text(*values: object, fallback: str | None = None) -> str | None:
    """Return the first non-empty metadata field, optionally with fallback."""
    for value in values:
        text = nonblank_text(value)
        if text is not None:
            return text
    return fallback


def jackery_online_state(value: object) -> bool | None:
    """Determine whether a Jackery online/offline marker indicates the device is online.

    Recognizes common string markers for online and offline states; for other types or
    unrecognized strings, falls back to generic boolean parsing.

    Returns:
        True if the marker indicates online, False if it indicates offline, None when
        the value cannot be interpreted.
    """
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"online", "connected", "available"}:
            return True
        if normalized in {"offline", "disconnected", "unavailable"}:
            return False
    if isinstance(value, (bool, int, float, str, type(None))):
        return safe_bool(value)
    return None


# ---------------------------------------------------------------------------
# App trend/statistic chart helpers
# ---------------------------------------------------------------------------
class TrendStatisticPoint(NamedTuple):
    """One app chart bucket converted to a dated statistic point."""

    start_date: date | datetime
    value: float


def statistic_id_part(value: object) -> str:
    """Return a Home-Assistant-safe external statistic id component."""
    text = str(value or "").strip()
    # Replace any non-alnum (except underscore) with underscore
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", text)
    # Collapse multiple underscores
    text = re.sub(r"_+", "_", text)
    # Strip leading/trailing underscores
    text = text.strip("_")
    return text.lower() or "unknown"


def external_trend_statistic_id(
    domain: str,
    device_id: str,
    metric_key: str,
    bucket: str,
) -> str:
    """Construct an external statistic id for importing app chart data.

    Parameters:
        domain (str): Statistic domain (e.g., ``jackery_solarvault``).
        device_id (str): Device identifier to include in the id.
        metric_key (str): Metric key or name to include in the id.
        bucket (str): Bucket suffix (e.g., hour/day/month) to include in the id.

    Returns:
        str: A statistic id in the HA external-statistics form
        ``<domain>:<object_id>`` where ``<object_id>`` joins the normalised
        device id, metric key and bucket. ``async_add_external_statistics``
        rejects ``sensor.*`` entity-style ids (``valid_statistic_id`` requires
        a ``:`` separator), so the Energy Dashboard correlates via the
        ``statistic_id`` suffix rather than an entity domain prefix.
    """
    return (
        f"{statistic_id_part(domain)}:"
        f"{statistic_id_part(device_id)}_"
        f"{statistic_id_part(metric_key)}_"
        f"{statistic_id_part(bucket)}"
    )


def _parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _trend_date_type(section: str, source: dict[str, Any]) -> str | None:
    """Determine the app period date type for a trend section.

    Use an explicit request override when present or infer it from the section suffix.

    Parameters:
        section (str): The chart/section key (e.g., ending with `_day`, `_week`,
        `_month`, or `_year`).
        source (dict[str, Any]): Payload that may include `APP_REQUEST_META` with
        `APP_REQUEST_DATE_TYPE` or `APP_REQUEST_DATE_TYPE_ALT` to explicitly specify the
        date type.

    Returns:
        str | None: One of the `DATE_TYPE_*` suffix values when found, `None` if no date
        type can be determined.
    """
    request = source.get(APP_REQUEST_META)
    if isinstance(request, dict):
        date_type = request.get(APP_REQUEST_DATE_TYPE) or request.get(
            APP_REQUEST_DATE_TYPE_ALT,
        )
        if isinstance(date_type, str):
            return date_type
    for suffix in (DATE_TYPE_DAY, DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR):
        if section.endswith(f"_{suffix}"):
            return suffix
    return None


def is_day_period_payload(source: dict[str, Any], section: str) -> bool:
    """Determine whether the given trend payload corresponds to a day-period request."""
    if section.endswith(f"_{DATE_TYPE_DAY}"):
        return True
    req = source.get(APP_REQUEST_META)
    return isinstance(req, dict) and (
        req.get(APP_REQUEST_STAT_TYPE) == DATE_TYPE_DAY
        or req.get(APP_REQUEST_DATE_TYPE) == DATE_TYPE_DAY
        or req.get(APP_REQUEST_DATE_TYPE_ALT) == DATE_TYPE_DAY
    )


def _is_non_day_period(section: str) -> bool:
    return section.endswith((
        f"_{DATE_TYPE_WEEK}",
        f"_{DATE_TYPE_MONTH}",
        f"_{DATE_TYPE_YEAR}",
    ))


def is_device_year_period_section(source: dict[str, Any], section: str) -> bool:
    """Determine whether a section represents a device-level "year" period statistic.

    Returns:
        `true` if the section's request dateType is year and the section name starts
        with a device statistic prefix (PV, home, battery, or CT), `false` otherwise.
    """
    return _trend_date_type(section, source) == DATE_TYPE_YEAR and section.startswith((
        APP_SECTION_PV_STAT,
        APP_SECTION_HOME_STAT,
        APP_SECTION_BATTERY_STAT,
        APP_SECTION_CT_STAT,
    ))


def _compact_year_parts(value: object) -> tuple[float, float] | None:
    """Parse one year-chart slot into (previous-month, current-month) parts.

    The device year chart can pack two adjacent *integer* month totals into
    one slot as text: ``"40,96"`` / ``"13.26"`` means previous month ``40``
    (``13``) plus current month ``96`` (``26``) — the text after the
    separator is the current-month integer, NOT a decimal fraction. The
    documented scalar total anchors whether that reading applies (see
    :func:`_disambiguate_year_series`); a plain value stays in its own slot
    with a zero previous part so the expanded series remains complete.

    Returns:
        tuple[float, float] | None: ``(previous_part, current_part)`` or
        ``None`` for missing/boolean/unparsable input.
    """
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, (int, float, str))
    ):
        return None
    text = str(value).strip()
    if not text or len(text) > _MAX_COMPACT_YEAR_VALUE_TEXT_LENGTH:
        return None
    sign = 1.0
    if text.startswith("-"):
        sign = -1.0
        text = text[1:]
    whole_text, separator, fraction_text = text.replace(",", ".", 1).partition(".")
    if not whole_text.isdigit():
        return None
    # "13.0" / "13,000" is a plain decimal, not a packed pair.
    packed = bool(separator) and fraction_text.isdigit() and int(fraction_text) != 0
    if packed:
        return sign * float(whole_text), sign * float(fraction_text)
    parsed = safe_float(value)
    if parsed is None or math.isnan(parsed) or math.isinf(parsed):
        return None
    return 0.0, parsed


def _prefer_raw_year_series_for_real_payload(
    section: str,
    raw_values: list[float | None],
    direct_total: float | None,
    tolerance: float,
) -> bool:
    """Keep known app PV year snapshots raw when the scalar total matches exactly."""
    if (
        not section.startswith(APP_SECTION_PV_STAT)
        or direct_total is None
        or len(raw_values) < _MONTHS_PER_YEAR
    ):
        return False
    numeric_values = [value for value in raw_values if value is not None]
    nonzero = [value for value in numeric_values if abs(value) > _NEAR_ZERO_EPSILON]
    return len(nonzero) == 1 and abs(sum(numeric_values) - direct_total) <= tolerance


def expanded_year_series_values(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> list[float | None] | None:
    """Expand compact Jackery device-year chart buckets.

    Produce a full per-bucket monthly value list.

    Parameters:
        source (dict[str, Any]): Payload containing chart series and stat fields.
        section (str): Section key used to locate the trend/chart series.
        stat_key (str): Statistic key whose documented total may anchor expansion.

    Returns:
        list[float | None] | None: Per-bucket numeric values/placeholders when a
        chart series is present.
            - With a documented scalar total, returns the expanded list only when
              its sum matches.
              the documented total within a small tolerance; otherwise returns the raw
              series values.
            - If the series key is missing or the series is not a list, returns `None`.
    """
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None
    series = source.get(series_key)
    if not isinstance(series, list):
        return None

    raw_values = [
        None if (value := safe_float(item)) is None else round(value, 5)
        for item in series
    ]
    direct_total = safe_float(source.get(stat_key))

    expanded: list[float | None] = [
        None if value is None else 0.0 for value in raw_values
    ]
    has_compact_bucket = False
    for index, raw_value in enumerate(series):
        parts = _compact_year_parts(raw_value)
        if parts is None:
            continue
        previous_value, current_value = parts
        if previous_value:
            has_compact_bucket = True
            target = index - 1 if index > 0 else index
            expanded[target] = (expanded[target] or 0.0) + previous_value
        if current_value:
            expanded[index] = (expanded[index] or 0.0) + current_value

    expanded = [None if value is None else round(value, 5) for value in expanded]

    if direct_total is None:
        return raw_values
    return _disambiguate_year_series(
        section,
        raw_values,
        expanded,
        direct_total,
        has_compact_bucket=has_compact_bucket,
    )


def _disambiguate_year_series(
    section: str,
    raw_values: list[float | None],
    expanded: list[float | None],
    direct_total: float,
    *,
    has_compact_bucket: bool,
) -> list[float | None]:
    """Pick raw vs. compact-expanded year buckets against the documented total.

    When compact encoding is in effect the expanded series is authoritative even
    if the raw series happens to sum to the same documented total -- the raw
    layout misattributes a completed month's energy to the wrong bucket (e.g.
    ``"13.26"`` -> April=13, May=0.26 rather than a lone May=13.26). Known
    single-month PV snapshots stay raw via
    :func:`_prefer_raw_year_series_for_real_payload`.

    Returns:
        list[float | None]: The chosen series (expanded when it reconciles the
        total and compact encoding applies, otherwise raw).
    """
    tolerance = max(0.05, abs(direct_total) * 0.005)
    raw_total = sum(value for value in raw_values if value is not None)
    expanded_total = sum(value for value in expanded if value is not None)
    raw_matches = abs(round(raw_total, 2) - direct_total) <= tolerance
    expanded_matches = abs(round(expanded_total, 2) - direct_total) <= tolerance
    prefer_expanded = (
        has_compact_bucket
        and expanded_matches
        and not _prefer_raw_year_series_for_real_payload(
            section,
            raw_values,
            direct_total,
            tolerance,
        )
    )
    if prefer_expanded:
        return expanded
    if raw_matches:
        return raw_values
    if has_compact_bucket and expanded_matches:
        return expanded
    return raw_values


def _is_pv_channel_period(section: str, stat_key: str) -> bool:
    """Identify PV-channel energy fields in period statistic sections."""
    return section.startswith(f"{APP_SECTION_PV_STAT}_") and stat_key in {
        APP_STAT_PV1_ENERGY,
        APP_STAT_PV2_ENERGY,
        APP_STAT_PV3_ENERGY,
        APP_STAT_PV4_ENERGY,
    }


def effective_trend_series_values(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> list[float | None] | None:
    """Return normalized numeric values for a statistic series.

    Resolve the series for the given section and statistic key.

    For device-year payloads, returns expanded year-series values when expansion is
    applicable; for other payloads, returns a list where numeric entries are
    rounded to 5 decimal places and non-parsable placeholders remain ``None``.

    Parameters:
        source (dict[str, Any]): Payload containing chart series and metadata.
        section (str): Payload section key (e.g., "pv_year", "home_month").
        stat_key (str): Statistic key within the section to locate the chart series.

    Returns:
        list[float | None] | None: Normalized numeric values/placeholders, or
        ``None`` if the chart series key is not applicable or the series value
        is not a list.
    """
    # dateType=day arrays are W power curves (served through
    # day_power_series_key), never energy chart buckets — their energy
    # totals live in the scalar fields.
    if is_day_period_payload(source, section):
        return None
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None
    series = source.get(series_key)
    if not isinstance(series, list):
        return None
    if is_device_year_period_section(source, section):
        expansion_source = source
        if pv_channel_scalar_is_lifetime_offset(source, section, stat_key):
            # An invalid scalar must not disambiguate compact month encoding:
            # its large tolerance can shift an offset into the preceding month.
            expansion_source = {
                key: value for key, value in source.items() if key != stat_key
            }
        values = expanded_year_series_values(expansion_source, section, stat_key)
    else:
        values = [
            None if (val := safe_float(raw)) is None else round(val, 5)
            for raw in series
        ]
    if values is None or not _is_pv_channel_period(section, stat_key):
        return values
    total_values = effective_trend_series_values(
        source, section, APP_STAT_TOTAL_SOLAR_ENERGY
    )
    if total_values is None or len(total_values) != len(values):
        return values
    normalized, _scalar, _total = normalize_pv_period_energy(
        source, section, stat_key, values=values, total_values=total_values
    )
    return normalized


def effective_period_total_value(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> float | None:
    """Determine the effective statistic total for an app period.

    Resolve the total within the given period section.

    Device-year charts are summed only with twelve known monthly values. An
    incomplete chart uses only an independent scalar; a complete zero-placeholder
    chart retains the existing positive-scalar fallback. Other periods return the
    parsed scalar value found at ``stat_key``.

    Returns:
        float: The period total rounded to 2 decimals when available, `None` if no value
        can be determined.
    """
    if pv_channel_scalar_is_lifetime_offset(source, section, stat_key):
        values = effective_trend_series_values(source, section, stat_key)
        if not values or (
            _trend_date_type(section, source) == DATE_TYPE_YEAR
            and len(values) != _MONTHS_PER_YEAR
        ):
            return None
        if any(value is None for value in values):
            return None
        return round(sum(value for value in values if value is not None), 2)
    if is_device_year_period_section(source, section):
        values = effective_trend_series_values(source, section, stat_key)
        if values is not None:
            direct_total = safe_float(source.get(stat_key))
            series_total = complete_year_series_total(values, direct_total)
            if series_total or direct_total is None:
                return series_total
            return direct_total if direct_total > 0 else series_total
    return safe_float(source.get(stat_key))


def _tolerance_for_values(*values: float | None) -> float:
    """Return a kWh/EUR tolerance large enough for app rounding noise."""
    magnitude = max((abs(value) for value in values if value is not None), default=0.0)
    return max(0.05, magnitude * 0.005)


def _period_section(prefix: str, date_type: str) -> str:
    return f"{prefix}_{date_type}"


def _nonzero_months(values: list[float | None]) -> list[int]:
    """Return one-based month numbers with non-zero app values."""
    return [
        index + 1
        for index, value in enumerate(values[:12])
        if abs(safe_float(value) or 0.0) > _NEAR_ZERO_EPSILON
    ]


def year_payload_appears_current_month_only(
    source: dict[str, Any],
    section: str,
    stat_keys: tuple[str, ...],
    *,
    current_month: int,
) -> bool:
    """Detect the app's year-period month-only bug.

    Check whether a payload contains non-zero values only for the current month.

    Check the requested year series only for kWh/unspecified units and when the
    current month is after January.

    Parameters:
        source (dict[str, Any]): The payload section containing chart series and
        metadata.
        section (str): The year-section key to inspect (e.g., `"pv_stat_year"`).
        stat_keys (tuple[str, ...]): Statistic keys to examine within the section.
        current_month (int): One-based current month index (1-12) used to detect a
        month-only pattern.

    Returns:
        bool: `True` if any inspected series has non-zero values only for
        `current_month`, `False` otherwise.
    """
    if current_month <= 1:
        return False
    if app_energy_unit_scale(source) is None:
        return False
    for stat_key in stat_keys:
        values = effective_trend_series_values(source, section, stat_key)
        if not isinstance(values, list) or len(values) < current_month:
            continue
        nonzero = _nonzero_months(values)
        if not nonzero or set(nonzero).issubset({current_month}):
            return True
    return False


def _month_value(
    month_source: dict[str, Any],
    month_section: str,
    stat_key: str,
) -> float | None:
    """Use a complete monthly curve or an independently valid monthly scalar."""
    unit = str(month_source.get(APP_STAT_UNIT) or "").strip().lower()
    if unit not in {"", APP_UNIT_KWH, APP_UNIT_WH} or (
        APP_REQUEST_META in month_source
        and _dated_energy_chart_window(month_source, DATE_TYPE_MONTH) is None
    ):
        return None
    unit_scale = app_energy_unit_scale(month_source)
    if unit_scale is None:
        return None
    values = effective_trend_series_values(month_source, month_section, stat_key)
    if values is not None and any(value is None for value in values):
        scalar = effective_period_total_value(month_source, month_section, stat_key)
        return scalar * unit_scale if scalar is not None else None
    value = trend_series_total(month_source, month_section, stat_key)
    if value is not None:
        return value
    scalar = effective_period_total_value(month_source, month_section, stat_key)
    return scalar * unit_scale if scalar is not None else None


def _pv_revenue_value(source: dict[str, Any]) -> float | None:
    revenue = safe_float(source.get(APP_STAT_TOTAL_SOLAR_REVENUE))
    if revenue is not None:
        return revenue
    profit = safe_float(source.get(APP_STAT_PV_PROFIT))
    if profit is None:
        return None
    return round(profit / 10_000_000, 5)


def _savings_energy_unit_scale(source: dict[str, Any]) -> float | None:
    """Use only documented energy units for the monetary estimate."""
    unit = source.get(APP_STAT_UNIT)
    if unit is not None and (
        not isinstance(unit, str)
        or unit.strip().lower() not in {"", APP_UNIT_KWH, APP_UNIT_WH}
    ):
        return None
    return app_energy_unit_scale(source)


def _savings_year_source_is_valid(source: dict[str, Any], *, today: date) -> bool:
    """Reject foreign request periods while retaining metadata-free legacy years."""
    if _savings_energy_unit_scale(source) is None:
        return False
    if APP_REQUEST_META not in source:
        return True
    window = _dated_energy_chart_window(source, DATE_TYPE_YEAR)
    return window is not None and window[0].year == today.year


def _period_total_from_payload(
    payload: dict[str, Any],
    section_prefix: str,
    stat_key: str,
    *,
    today: date,
) -> float | None:
    section = _period_section(section_prefix, DATE_TYPE_YEAR)
    source = payload.get(section)
    if not isinstance(source, dict) or not _savings_year_source_is_valid(
        source, today=today
    ):
        return None
    value = effective_period_total_value(source, section, stat_key)
    scale = _savings_energy_unit_scale(source)
    return value * scale if value is not None and scale is not None else None


def _round_stat_value(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _configured_or_derived_price(
    payload: dict[str, Any],
    *,
    year_generation: float | None,
    year_revenue: float | None,
) -> tuple[float | None, str | None]:
    price_source = payload.get(PAYLOAD_PRICE)
    if isinstance(price_source, dict):
        configured = safe_float(price_source.get(FIELD_SINGLE_PRICE))
        if (
            safe_int(price_source.get(FIELD_DYNAMIC_OR_SINGLE)) != 1
            and configured is not None
            and 0 <= configured <= _MAX_PRICE_PER_KWH
        ):
            return configured, f"{PAYLOAD_PRICE}.{FIELD_SINGLE_PRICE}"

    if year_generation is not None and year_generation > 0 and year_revenue is not None:
        derived = year_revenue / year_generation
        if 0 <= derived <= _MAX_PRICE_PER_KWH:
            return round(derived, 5), "pv_year_revenue_per_kwh"
    return None, None


def _pv_revenue_candidates(
    pv_year: dict[str, Any],
    *,
    year_revenue: float | None,
    raw_generation: float | None,
    price: float | None,
) -> list[float]:
    candidates: list[float] = []
    if year_revenue is not None:
        candidates.append(round(year_revenue, 2))
    if raw_generation is not None and price is not None:
        candidates.append(round(raw_generation * price, 2))

    backfill = pv_year.get(APP_YEAR_BACKFILL_META)
    if isinstance(backfill, dict):
        corrected = backfill.get("corrected")
        if isinstance(corrected, dict):
            revenue_meta = corrected.get("totalSolarRevenue")
            if isinstance(revenue_meta, dict):
                for key in ("raw_total", "corrected_total"):
                    value = safe_float(revenue_meta.get(key))
                    if value is not None:
                        candidates.append(round(value, 2))

    unique: list[float] = []
    for value in candidates:
        if not any(
            abs(value - existing) <= _tolerance_for_values(value, existing)
            for existing in unique
        ):
            unique.append(value)
    return unique


def resolve_battery_year_energy(
    payload: dict[str, Any], stat_key: str, *, today: date
) -> tuple[float | None, dict[str, Any]]:
    """Resolve annual battery energy and retain raw/effective source provenance."""
    section = f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_YEAR}"
    source = payload.get(section)
    if not isinstance(source, dict):
        source = {}
    raw = effective_period_total_value(source, section, stat_key)
    scale = _savings_energy_unit_scale(source)
    app_scalar = safe_float(source.get(stat_key))
    raw = raw * scale if raw is not None and scale is not None else None
    local = payload.get(PAYLOAD_LOCAL_DAILY_ENERGY)
    metric = {
        APP_STAT_TOTAL_CHARGE: APP_DEVICE_STAT_BATTERY_CHARGE,
        APP_STAT_TOTAL_DISCHARGE: APP_DEVICE_STAT_BATTERY_DISCHARGE,
    }[stat_key]
    local = safe_float(local.get(metric)) if isinstance(local, dict) else None
    rebuilt = rebuild_period_energy_total(
        payload,
        section,
        stat_key,
        today=today,
        local_day_kwh=local / JACKERY_LIVE_ENERGY_UNITS_PER_KWH
        if local is not None
        else None,
    )
    begin = request_date(source, APP_REQUEST_BEGIN_DATE, APP_REQUEST_BEGIN_DATE_ALT)
    current_year = begin is None or begin.year == today.year
    valid_source = _savings_year_source_is_valid(source, today=today)
    value = raw if valid_source else None
    if rebuilt is not None and valid_source:
        value = rebuilt[0] if raw is None or rebuilt[1] else max(raw, rebuilt[0])
    return value, {
        "source_section": section,
        "source_key": stat_key,
        "raw_app_kwh": app_scalar * scale
        if app_scalar is not None and scale is not None
        else None,
        "app_year_total_kwh": raw,
        "effective_kwh": value,
        "method": "year_request_outside_current_year"
        if not current_year
        else "invalid_year_source"
        if not valid_source
        else "current_open_year_from_month_and_daily_buckets"
        if rebuilt is not None
        else "app_year_total",
        "completed_days_verified": rebuilt[1] if rebuilt is not None else False,
        "as_of": today.isoformat(),
    }


def _calculated_savings_from_year(  # ruff: ignore[too-many-locals] - cohesive savings estimate with named totals
    payload: dict[str, Any],
    *,
    year_generation: float | None,
    year_revenue: float | None,
    today: date,
) -> dict[str, Any] | None:
    """Estimate annual PV savings and an energy breakdown.

    Base the result on the app payload and optional year totals.

    Parameters:
        payload (dict): App payload used to derive period totals and chart-series
        values.
        year_generation (float | None): Documented year PV generation in kWh, or None if
        unavailable.
        year_revenue (float | None): Documented year PV revenue (currency units) or None
        if unavailable.

    Returns:
        dict: Mapping with keys:
            - `method` (str): Descriptor of how savings were computed.
            - `calculated_total`: Savings monetary total rounded to 2 decimals.
            - `energy_kwh` (float): Savings energy in kWh (rounded to 2 decimals).
            - `price` (float): Price used per kWh (rounded to 5 decimals).
            - `price_source` (str): Source label for the price (configured or derived).
            - `source_energy`: Rounded kWh diagnostics for PV, grid, home, CT,
              battery, conversion loss and residual PV.
        None: If required inputs are missing (no usable device/home/CT totals or no
        configured/derivable price).
    """
    device_output = _period_total_from_payload(
        payload,
        APP_SECTION_HOME_STAT,
        APP_STAT_TOTAL_OUT_GRID_ENERGY,
        today=today,
    )
    if device_output is None:
        return None
    device_input = _period_total_from_payload(
        payload,
        APP_SECTION_HOME_STAT,
        APP_STAT_TOTAL_IN_GRID_ENERGY,
        today=today,
    )

    home_consumption = _period_total_from_payload(
        payload,
        APP_SECTION_HOME_TRENDS,
        APP_STAT_TOTAL_HOME_ENERGY,
        today=today,
    )
    public_export = _period_total_from_payload(
        payload,
        APP_SECTION_CT_STAT,
        APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        today=today,
    )
    public_export_present = public_export is not None
    if home_consumption is None and public_export is None:
        return None

    price, price_source = _configured_or_derived_price(
        payload,
        year_generation=year_generation,
        year_revenue=year_revenue,
    )
    if price is None:
        return None

    delivered_ac = max(0.0, device_output)
    method_prefix = "device_grid_side_output"
    if device_input is not None:
        delivered_ac = max(0.0, delivered_ac - max(0.0, device_input))
        method_prefix = "device_grid_side_net_output"
    net_device_output = delivered_ac
    if public_export is not None:
        delivered_ac = max(0.0, delivered_ac - max(0.0, public_export))
        method_prefix = f"{method_prefix}_minus_ct_export"
    if home_consumption is not None:
        savings_energy = min(max(0.0, home_consumption), delivered_ac)
        method = f"{method_prefix}_bounded_by_home"
    else:
        savings_energy = delivered_ac
        method = method_prefix

    battery_year_basis = {
        key: resolve_battery_year_energy(payload, key, today=today)
        for key in (APP_STAT_TOTAL_CHARGE, APP_STAT_TOTAL_DISCHARGE)
    }
    battery_charge = battery_year_basis[APP_STAT_TOTAL_CHARGE][0]
    battery_discharge = battery_year_basis[APP_STAT_TOTAL_DISCHARGE][0]
    battery_balance = None
    if battery_charge is not None and battery_discharge is not None:
        battery_balance = battery_charge - battery_discharge

    conversion_loss_energy = None
    conversion_loss_energy_signed = None
    if (
        year_generation is not None
        and battery_charge is not None
        and battery_discharge is not None
    ):
        conversion_loss_energy_signed = (
            max(0.0, year_generation)
            + max(0.0, device_input or 0.0)
            + max(0.0, battery_discharge)
            - max(0.0, device_output)
            - max(0.0, battery_charge)
        )
        conversion_loss_energy = max(0.0, conversion_loss_energy_signed)

    pv_residual_after_self_consumption_energy = None
    if year_generation is not None:
        pv_residual_after_self_consumption_energy = max(
            0.0, year_generation - savings_energy
        )

    calculated_total = round(savings_energy * price, 2)
    return {
        "method": method,
        "calculated_total": calculated_total,
        "energy_kwh": round(savings_energy, 2),
        "price": round(price, 5),
        "price_source": price_source,
        "battery_year_basis": {
            key: evidence for key, (_value, evidence) in battery_year_basis.items()
        },
        "source_energy": {
            "pv_year_kwh": _round_stat_value(year_generation),
            "device_grid_side_input_year_kwh": _round_stat_value(device_input),
            "device_grid_side_output_year_kwh": _round_stat_value(device_output),
            "device_grid_side_net_output_year_kwh": _round_stat_value(
                net_device_output,
            ),
            "savings_basis_ac_year_kwh": _round_stat_value(delivered_ac),
            "home_consumption_year_kwh": _round_stat_value(home_consumption),
            "ct_public_export_year_kwh": _round_stat_value(public_export),
            "ct_public_export_year_kwh_present": public_export_present,
            "battery_charge_year_kwh": _round_stat_value(battery_charge, digits=5),
            "battery_discharge_year_kwh": _round_stat_value(
                battery_discharge, digits=5
            ),
            "battery_charge_discharge_balance_year_kwh": _round_stat_value(
                battery_balance, digits=5
            ),
            "conversion_loss_year_kwh": _round_stat_value(conversion_loss_energy),
            "conversion_loss_year_kwh_signed": _round_stat_value(
                conversion_loss_energy_signed,
            ),
            "pv_residual_after_self_consumption_year_kwh": _round_stat_value(
                pv_residual_after_self_consumption_energy,
            ),
            "pv_not_savings_ac_energy_kwh": _round_stat_value(
                pv_residual_after_self_consumption_energy,
            ),
        },
    }


def _backfill_pv_revenue(
    out: dict[str, Any],
    year_source: dict[str, Any],
    month_sources: dict[int, dict[str, Any]],
    meta: dict[str, Any],
) -> None:
    """Backfill yearly PV revenue fields from monthly values.

    Keep known yearly revenue buckets and fill only observed monthly values.
    Unreported months remain ``None``. Replace annual revenue and profit only when
    all twelve buckets are known and their sum exceeds the independent year total.

    Parameters:
        out (dict[str, Any]): Mutable output payload to update with corrected yearly PV
        revenue fields.
        year_source (dict[str, Any]): Original year-level payload used to read the
        existing yearly PV revenue.
        month_sources (dict[int, dict[str, Any]]): Mapping of 1-based month index to
        month payloads used to derive monthly revenue values; months outside 1-12 are
        ignored.
        meta: Mutable correction metadata. Revenue correction records raw total,
            corrected total and contributing months.

    Side effects:
        - May set total revenue, PV profit and chart series ``y6``.
        - May add correction details under `meta["corrected"]["totalSolarRevenue"]`.
    """
    raw_series = year_source.get(APP_CHART_SERIES_Y6)
    raw_values = (
        [
            None if (value := safe_float(raw)) is None else value / 10_000_000
            for raw in raw_series[:12]
        ]
        if isinstance(raw_series, list)
        else []
    )
    revenue_values = raw_values + [None] * (12 - len(raw_values))
    found_months: list[int] = []
    for month, month_source in sorted(month_sources.items()):
        if not 1 <= month <= _MONTHS_PER_YEAR or not _month_source_matches_year_slot(
            month_source, year_source, month
        ):
            continue
        revenue = _pv_revenue_value(month_source)
        if revenue is None:
            continue
        previous = revenue_values[month - 1]
        revenue_values[month - 1] = round(
            revenue if previous is None else max(previous, revenue), 5
        )
        found_months.append(month)
    if not found_months:
        return

    raw_total = _pv_revenue_value(year_source)
    missing_months = [
        index + 1 for index, value in enumerate(revenue_values) if value is None
    ]
    monthly_total = round(
        sum(value for value in revenue_values if value is not None), 2
    )
    replace_total = not missing_months and (
        raw_total is None
        or monthly_total > raw_total + _tolerance_for_values(raw_total, monthly_total)
    )
    if not replace_total and revenue_values == raw_values:
        return

    if replace_total:
        out["totalSolarRevenue"] = monthly_total
        out["pvProfit"] = round(monthly_total * 10_000_000, 1)
    out[APP_CHART_SERIES_Y6] = [
        None if value is None else round(value * 10_000_000, 1)
        for value in revenue_values
    ]
    meta.setdefault("corrected", {})["totalSolarRevenue"] = {
        "raw_total": raw_total,
        "corrected_total": monthly_total if replace_total else raw_total,
        "months": found_months,
        "missing_months": missing_months,
    }


def _month_source_matches_year_slot(
    month_source: dict[str, Any], year_source: dict[str, Any], month: int
) -> bool:
    """Bind observed month metadata to its requested year and month."""
    begin = request_date(
        month_source, APP_REQUEST_BEGIN_DATE, APP_REQUEST_BEGIN_DATE_ALT
    )
    year_begin = request_date(
        year_source, APP_REQUEST_BEGIN_DATE, APP_REQUEST_BEGIN_DATE_ALT
    )
    return begin is None or (
        begin.month == month and (year_begin is None or begin.year == year_begin.year)
    )


def _year_backfill_total_baseline(
    source: dict[str, Any], section: str, stat_key: str
) -> float | None:
    """Protect existing annual scalars without changing chart-authoritative reads."""
    total = effective_period_total_value(source, section, stat_key)
    scalar = safe_float(source.get(stat_key))
    if scalar is None or pv_channel_scalar_is_lifetime_offset(
        source, section, stat_key
    ):
        return total
    return max(total, scalar) if total is not None else scalar


def backfill_year_payload_from_months(  # ruff: ignore[too-many-branches]
    year_source: dict[str, Any],
    section_prefix: str,
    stat_keys: tuple[str, ...],
    month_sources: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Build a corrected year-period payload from monthly payloads.

    Apply corrections when monthly data show incomplete or inconsistent year totals.

    For each requested statistic key this function:
    - Fills reported months while retaining known yearly buckets and missing values.
    - Replaces a year total only with twelve known buckets exceeding the independent
      scalar beyond tolerance; partial chart repairs retain the scalar unchanged.
    - Adds aliases for corrected PV, grid and discharge totals.

    Behavior notes:
    - Returns the original payload when no reliable monthly correction is proven.
    - Corrected payloads include method, periods, totals, series key and months.
    - PV sections also attempt a revenue correction from the same month sources.

    Parameters:
        year_source: The original year-period payload (expected dictionary shape).
        section_prefix: Prefix identifying the section (e.g., PV/home/battery) used to
        form period keys.
        stat_keys: Tuple of statistic keys to attempt backfill for.
        month_sources: Mapping from 1-12 month index to that month's payload dictionary.

    Returns:
        A dictionary payload: either the unchanged `year_source` or a modified copy with
        corrected series/stat fields and `APP_YEAR_BACKFILL_META` when corrections were
        applied.
    """
    if not isinstance(year_source, dict) or not month_sources:
        return year_source

    year_section = _period_section(section_prefix, DATE_TYPE_YEAR)
    month_section = _period_section(section_prefix, DATE_TYPE_MONTH)
    unit_scale = app_energy_unit_scale(year_source)
    if unit_scale is None:
        return year_source

    out = dict(year_source)
    out.setdefault(APP_CHART_LABELS, [str(month) for month in range(1, 13)])
    meta: dict[str, Any] = {
        "method": "same_endpoint_month_sum",
        "source_period": DATE_TYPE_MONTH,
        "target_period": DATE_TYPE_YEAR,
    }

    for stat_key in stat_keys:
        series_key = trend_series_key(year_section, stat_key)
        if not series_key:
            continue

        raw_values = effective_trend_series_values(year_source, year_section, stat_key)
        monthly_values = list(raw_values[:12]) if isinstance(raw_values, list) else []
        monthly_values.extend([None] * (12 - len(monthly_values)))
        found_months: list[int] = []
        for month, month_source in sorted(month_sources.items()):
            if (
                not 1 <= month <= _MONTHS_PER_YEAR
                or not _month_source_matches_year_slot(month_source, year_source, month)
            ):
                continue
            value = _month_value(month_source, month_section, stat_key)
            if value is None:
                continue
            value /= unit_scale
            previous = monthly_values[month - 1]
            monthly_values[month - 1] = round(
                value if previous is None else max(previous, value), 5
            )
            found_months.append(month)
        if not found_months:
            continue

        monthly_total = round(
            sum(value for value in monthly_values if value is not None), 2
        )
        raw_total = _year_backfill_total_baseline(year_source, year_section, stat_key)
        missing_months = [
            index + 1 for index, value in enumerate(monthly_values) if value is None
        ]
        replace_total = not missing_months and (
            raw_total is None
            or monthly_total
            > raw_total + _tolerance_for_values(raw_total, monthly_total)
        )
        if not replace_total and monthly_values == raw_values:
            continue

        out[series_key] = monthly_values
        if replace_total:
            out[stat_key] = monthly_total
            if stat_key == APP_STAT_TOTAL_SOLAR_ENERGY:
                out["pvEgy"] = monthly_total
            elif stat_key == APP_STAT_TOTAL_IN_GRID_ENERGY:
                out["inOngridEgy"] = monthly_total
            elif stat_key == APP_STAT_TOTAL_OUT_GRID_ENERGY:
                out["outOngridEgy"] = monthly_total
            elif stat_key == APP_STAT_TOTAL_DISCHARGE:
                out["batOtGridEgy"] = monthly_total

        meta.setdefault("corrected", {})[stat_key] = {
            "raw_total": raw_total,
            "corrected_total": monthly_total if replace_total else raw_total,
            "series_key": series_key,
            "months": found_months,
            "missing_months": missing_months,
        }

    if section_prefix in {APP_SECTION_PV_STAT, APP_SECTION_PV_TRENDS}:
        _backfill_pv_revenue(out, year_source, month_sources, meta)

    if "corrected" not in meta:
        return year_source
    out[APP_YEAR_BACKFILL_META] = meta
    return out


def year_payload_omits_earlier_months(
    source: dict[str, Any],
    section: str,
    stat_keys: tuple[str, ...],
    *,
    current_month: int,
) -> bool:
    """Flag leading zero year buckets for same-endpoint month verification.

    A partially populated year can omit an earlier active month even when
    several later months are nonzero. A zero bucket alone remains unproven;
    the month response decides whether the year needs correction.
    """
    if current_month <= 1 or app_energy_unit_scale(source) is None:
        return False
    for stat_key in stat_keys:
        values = effective_trend_series_values(source, section, stat_key)
        if not isinstance(values, list) or len(values) < current_month:
            continue
        nonzero = _nonzero_months(values[:current_month])
        if nonzero and nonzero[0] > 1:
            return True
    return False


def apply_year_month_backfill(
    payload: dict[str, Any],
    month_history: dict[str, dict[int, dict[str, Any]]],
) -> None:
    """Backfill year-period payloads from month histories.

    Process the known statistic sections.

    This mutates `payload` in-place, replacing year-section entries with corrected year
    payloads when monthly data are available and a backfill is performed.

    Parameters:
        payload: App payload whose ``<prefix>_year`` sections may be replaced.
        month_history (dict[str, dict[int, dict[str, Any]]]): Mapping from section
        prefix to a mapping of 1-based month index -> month payload dict used to
        reconstruct year-series values.
    """
    section_metrics: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            APP_SECTION_PV_STAT,
            (
                APP_STAT_TOTAL_SOLAR_ENERGY,
                APP_STAT_PV1_ENERGY,
                APP_STAT_PV2_ENERGY,
                APP_STAT_PV3_ENERGY,
                APP_STAT_PV4_ENERGY,
            ),
        ),
        (
            APP_SECTION_HOME_STAT,
            (APP_STAT_TOTAL_IN_GRID_ENERGY, APP_STAT_TOTAL_OUT_GRID_ENERGY),
        ),
        (APP_SECTION_BATTERY_STAT, (APP_STAT_TOTAL_CHARGE, APP_STAT_TOTAL_DISCHARGE)),
        (APP_SECTION_HOME_TRENDS, (APP_STAT_TOTAL_HOME_ENERGY,)),
        (APP_SECTION_PV_TRENDS, (APP_STAT_TOTAL_SOLAR_ENERGY,)),
        (
            APP_SECTION_BATTERY_TRENDS,
            (APP_STAT_TOTAL_TREND_CHARGE_ENERGY, APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY),
        ),
    )

    for section_prefix, stat_keys in section_metrics:
        year_section = _period_section(section_prefix, DATE_TYPE_YEAR)
        year_source = payload.get(year_section)
        months = month_history.get(section_prefix)
        if not isinstance(year_source, dict) or not isinstance(months, dict):
            continue
        payload[year_section] = backfill_year_payload_from_months(
            year_source,
            section_prefix,
            stat_keys,
            months,
        )


def attach_calculated_savings_metadata(
    payload: dict[str, Any], *, today: date | None = None
) -> None:
    """Attach calculated-savings details without changing cloud KPI values.

    The app's ``totalGeneration``, ``totalRevenue`` and ``totalCarbon`` fields
    remain byte-for-byte owned by their HTTP payload. Calculated values are
    published only through the separate savings-detail sensors.
    """
    statistic = payload.get(PAYLOAD_STATISTIC)
    if not isinstance(statistic, dict):
        return
    if APP_SAVINGS_CALC_META in statistic:
        statistic = {
            key: value
            for key, value in statistic.items()
            if key != APP_SAVINGS_CALC_META
        }
        payload[PAYLOAD_STATISTIC] = statistic
    local_today = today if today is not None else dt_util.now().date()
    raw_generation = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
    pv_year_section = _period_section(APP_SECTION_PV_TRENDS, DATE_TYPE_YEAR)
    pv_year = payload.get(pv_year_section)
    if not isinstance(pv_year, dict) or not _savings_year_source_is_valid(
        pv_year, today=local_today
    ):
        # Older/non-system devices may only expose PvStatApi. Keep that endpoint
        # as a compatibility fallback, but SysPvStatApi is the value rendered by
        # the current App's PV chart and therefore the primary savings basis.
        pv_year_section = _period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR)
        pv_year = payload.get(pv_year_section)
    if not isinstance(pv_year, dict) or not _savings_year_source_is_valid(
        pv_year, today=local_today
    ):
        return
    year_generation = _period_total_from_payload(
        payload,
        pv_year_section.removesuffix(f"_{DATE_TYPE_YEAR}"),
        APP_STAT_TOTAL_SOLAR_ENERGY,
        today=local_today,
    )
    # totalSolarRevenue is the annual HTTP model field. A live pvProfit alias
    # alone cannot prove annual revenue for a dynamic-price fallback.
    year_revenue = safe_float(pv_year.get(APP_STAT_TOTAL_SOLAR_REVENUE))
    savings = _calculated_savings_from_year(
        payload,
        year_generation=year_generation,
        year_revenue=year_revenue,
        today=local_today,
    )
    if savings is None:
        return
    raw_revenue = safe_float(statistic.get(APP_STAT_TOTAL_REVENUE))
    savings.update({
        "raw_cloud_total": raw_revenue,
        "pv_revenue_candidates": _pv_revenue_candidates(
            pv_year,
            year_revenue=year_revenue,
            raw_generation=raw_generation,
            price=safe_float(savings.get("price")),
        ),
        "decision": "calculated_separate_from_cloud_kpi",
        "published_value": safe_float(savings.get("calculated_total")),
        "published_value_source": "calculated_detail_sensor",
        "price_basis": {
            "source_section": pv_year_section,
            "revenue_key": APP_STAT_TOTAL_SOLAR_REVENUE,
            "revenue": year_revenue,
            "generation_key": APP_STAT_TOTAL_SOLAR_ENERGY,
            "generation_kwh": year_generation,
            "request": pv_year.get(APP_REQUEST_META),
            "as_of": local_today.isoformat(),
        }
        if savings.get("price_source") == "pv_year_revenue_per_kwh"
        else {
            "source_section": PAYLOAD_PRICE,
            "source_key": FIELD_SINGLE_PRICE,
        },
    })
    payload[PAYLOAD_STATISTIC] = {
        **statistic,
        APP_SAVINGS_CALC_META: savings,
    }


# One canonical lifetime-value and period-boundary policy; never modify raw
# transport observations. Diagnostics keep the unmodified wire snapshot.
_LIFETIME_GUARD_KEY: Final = "_total_lower_bound_guard"


def _tolerance(*values: float | None) -> float:
    """Return a kWh tolerance large enough for app rounding noise."""
    magnitude = max((abs(value) for value in values if value is not None), default=0.0)
    return max(0.05, magnitude * 0.005)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _year_generation(payload: dict[str, Any], today: date | None) -> float | None:
    """Return a dated current-year total in kWh (system chart first).

    App stat unit contract: Wh * 0.001, kWh/missing unit * 1; reuse the
    documented CtStatApi/EpsStatApi conversion, never scale lifetime values.
    """
    if today is None:
        return None
    for prefix in (APP_SECTION_PV_TRENDS, APP_SECTION_PV_STAT):
        section = f"{prefix}_{DATE_TYPE_YEAR}"
        pv_year = payload.get(section)
        if isinstance(pv_year, dict):
            request = pv_year.get(APP_REQUEST_META)
            begin = request_date(
                pv_year, APP_REQUEST_BEGIN_DATE, APP_REQUEST_BEGIN_DATE_ALT
            )
            end = request_date(pv_year, APP_REQUEST_END_DATE, APP_REQUEST_END_DATE_ALT)
            if (
                not isinstance(request, dict)
                or request.get(APP_REQUEST_DATE_TYPE) != DATE_TYPE_YEAR
                or begin is None
                or end is None
            ):
                continue
            if begin.year != today.year or end.year != today.year or end < begin:
                continue
            scale = app_energy_unit_scale(pv_year)
            value = effective_period_total_value(
                pv_year, section, APP_STAT_TOTAL_SOLAR_ENERGY
            )
            if value is not None and scale is not None:
                return value * scale
    return None


def guard_lifetime_totals(
    payload: dict[str, Any],
    *,
    previous_statistic: dict[str, Any] | None = None,
    today: date | None = None,
) -> None:
    """Keep lifetime ``totalGeneration`` at or above its verified lower bounds.

    PROTOCOL §7/§8.1: the lifetime total is never lowered by a smaller cloud
    value; the current-year PV total and the previously published lifetime
    total are lower bounds. ``totalCarbon`` follows with the app's own factor.
    The correction is recorded under ``_total_lower_bound_guard``.
    """
    statistic = payload.get(PAYLOAD_STATISTIC)
    if not isinstance(statistic, dict):
        return
    year_generation = _year_generation(payload, today)
    raw_generation = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
    previous_generation = (
        safe_float(previous_statistic.get(APP_STAT_TOTAL_GENERATION))
        if isinstance(previous_statistic, dict)
        else None
    )
    candidates = [
        value
        for value in (raw_generation, year_generation, previous_generation)
        if value is not None
    ]
    if not candidates:
        return
    corrected = max(candidates)
    if raw_generation is not None and corrected <= raw_generation + _tolerance(
        corrected, raw_generation
    ):
        return
    if raw_generation is None:
        if previous_generation is None:
            return
        corrected = previous_generation
        method = "previous_total_lower_bound"
    elif previous_generation is not None and previous_generation >= corrected - (
        _tolerance(previous_generation, corrected)
    ):
        method = "previous_total_lower_bound"
    else:
        method = "year_total_lower_bound"
    guarded = dict(statistic)
    guarded[APP_STAT_TOTAL_GENERATION] = round(corrected, 2)
    guarded[_LIFETIME_GUARD_KEY] = {
        "method": method,
        "corrected": {
            APP_STAT_TOTAL_GENERATION: {
                "raw_total": _rounded(raw_generation),
                "corrected_total": round(corrected, 2),
                "current_year_total": _rounded(year_generation),
                "previous_total": _rounded(previous_generation),
            }
        },
    }
    raw_carbon = safe_float(statistic.get(APP_STAT_TOTAL_CARBON))
    if raw_generation and raw_generation > 0 and raw_carbon is not None:
        factor = raw_carbon / raw_generation
        if 0 <= factor <= _MAX_CARBON_FACTOR:
            guarded[APP_STAT_TOTAL_CARBON] = round(corrected * factor, 2)
    payload[PAYLOAD_STATISTIC] = guarded


def guard_total_increasing_jitter(
    previous: StateType,
    current: StateType,
    description: SensorEntityDescription,
) -> StateType:
    """Hold lifetime energy counter regressions until a new entity is created."""
    if (
        description.device_class != SensorDeviceClass.ENERGY
        or description.state_class != SensorStateClass.TOTAL_INCREASING
        or getattr(description, "reset_period", None) is not None
    ):
        # A period sensor must reset at its calendar boundary. Never carry
        # yesterday's measurement into a new day as a lifetime floor.
        return current
    previous_number = safe_float(previous)
    current_number = safe_float(current)
    if previous_number is None:
        return current
    if current_number is None:
        return previous
    if current_number < previous_number:
        return previous
    return current


def period_data_offset(begin_iso: str | None, period_start: date) -> int:
    """Compare a cloud period's begin date with the local period start.

    Returns ``-1`` while the cloud still serves an earlier period (stale after
    a reset), ``1`` when it already serves a later one, and ``0`` when both
    match or the begin date is unknown. A period sensor must not publish a
    value of another period, or HA books it under the wrong ``last_reset``.
    """
    if begin_iso is None:
        return 0
    try:
        begin = date.fromisoformat(begin_iso)
    except ValueError:
        return 0
    return (begin > period_start) - (begin < period_start)


def _trend_bucket_start(begin: date, date_type: str | None, index: int) -> date | None:
    """Map one app series index to its calendar bucket."""
    if date_type == DATE_TYPE_YEAR:
        month = index + 1
        if month <= _MONTHS_PER_YEAR:
            return begin.replace(month=month, day=1)
    elif date_type in {DATE_TYPE_WEEK, DATE_TYPE_MONTH}:
        return begin + timedelta(days=index)
    return None


def trend_series_points(
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    today: date | None = None,
) -> list[TrendStatisticPoint]:
    """Convert an app chart series into dated TrendStatisticPoint buckets.

    Parameters:
        source (dict): App payload containing chart series and optional request meta.
        section (str): Payload section key used to locate the chart series.
        stat_key (str): Statistic key used to resolve the specific series within the
        section.
        today (date | None): Optional upper bound for returned points; defaults to today
        when None.

    Returns:
        list[TrendStatisticPoint]: Points for each valid series bucket with the bucket
        start date and the value rounded to 5 decimals. Empty list when the series is
        missing, not kWh, out of range, or cannot be mapped to dates.
    """
    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return []
    unit_scale = app_energy_unit_scale(source)
    if unit_scale is None:
        return []
    series = effective_trend_series_values(source, section, stat_key)
    if not isinstance(series, list) or not series:
        return []
    series_values = cast("list[Any]", series)

    request = source.get(APP_REQUEST_META)
    begin = None
    end = None
    if isinstance(request, dict):
        begin = _parse_iso_date(
            request.get(APP_REQUEST_BEGIN_DATE)
            or request.get(APP_REQUEST_BEGIN_DATE_ALT)
        )
        end = _parse_iso_date(
            request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT)
        )

    date_type = _trend_date_type(section, source)
    if begin is None:
        return []
    if today is None:
        today = datetime.now(UTC).astimezone().date()

    points: list[TrendStatisticPoint] = []
    for index, value in enumerate(series_values):
        if value is None:
            continue
        bucket_start = _trend_bucket_start(begin, date_type, index)
        if bucket_start is None:
            continue

        if (end is not None and bucket_start > end) or bucket_start > today:
            continue
        value_float = safe_float(value)
        if value_float is None:
            continue
        points.append(
            TrendStatisticPoint(bucket_start, round(value_float * unit_scale, 5))
        )
    return points


def _parse_day_chart_minute(value: object) -> int | None:
    """Parse an app day-chart label into minutes after local midnight.

    Parameters:
        value (Any): Label expected as an H:MM-style string (hours and minutes).

    Returns:
        int: Minutes after local midnight for a valid label (0-1439).
        None: If the input is not a valid H:MM label or represents the disallowed
        `24:00` end marker.
    """
    if not isinstance(value, str):
        return None
    match = _DAY_CHART_MINUTE_RE.fullmatch(value)
    if match is None:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour == _HOURS_PER_DAY and minute == 0:
        return None
    if 0 <= hour <= _MAX_HOUR and 0 <= minute <= _MAX_MINUTE:
        return hour * 60 + minute
    return None


def _day_power_sample_minute(
    labels: list[Any] | None,
    index: int,
) -> int | None:
    """Determine the minute of day for a power-curve sample.

    Use an optional label list before falling back to the sample index.

    Parameters:
        labels: Optional sample labels such as ``H:MM``.
        index (int): Zero-based sample index; used as a fallback to compute minute =
        index * 5.

    Returns:
        minute_of_day (int | None): Minutes after local midnight (0-1439) for the
        sample, or `None` if the computed minute is outside the day range or no valid
        label/index mapping exists.
    """
    if labels is not None and index < len(labels):
        minute = _parse_day_chart_minute(labels[index])
        if minute is not None:
            return minute
    minute = index * 5
    return minute if 0 <= minute < 24 * 60 else None


def _day_power_sample_energy_value(
    raw: object,
    section: str,
    stat_key: str,
    series_key: str | None,
) -> float | None:
    """Return the directional app day-curve sample value to integrate."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    value = safe_float(raw) if isinstance(raw, (int, float, str)) else None
    # An impossible PV value leaves a gap rather than a forged zero.
    # The full original HTTP/MQTT curve remains in the raw payload.
    if value is None or (is_pv_generation_metric(section, stat_key) and value < 0):
        return None
    if section.startswith((APP_SECTION_BATTERY_STAT, APP_SECTION_BATTERY_TRENDS)):
        if stat_key in {
            APP_STAT_TOTAL_CHARGE,
            APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
        }:
            return max(value, 0.0)
        if stat_key in {
            APP_STAT_TOTAL_DISCHARGE,
            APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
        }:
            if series_key in {APP_CHART_SERIES_Y, APP_CHART_SERIES_Y1}:
                # Some accounts return one combined signed y/y1 curve: positive
                # samples are charging and negative samples are discharging.
                return abs(value) if value < 0 else 0.0
            # The documented/App-2.4.0 split payload uses y1 for charge and y2
            # for discharge. Those y2 samples are positive magnitudes.
            return max(value, 0.0)
    return max(value, 0.0)


def _reconcile_rounded_day_values(
    rounded_values: list[float], scalar_total: float
) -> list[float]:
    """Reconcile rounded buckets exactly to a non-negative scalar total.

    Parameters:
        rounded_values (list[float]): Per-bucket kWh values already rounded.
        scalar_total (float): Target period total the buckets should sum to.

    Returns:
        list[float]: The reconciled values (a new list; input is not mutated).
    """
    if not rounded_values:
        return rounded_values
    precision = 100_000
    adjusted = [
        max(0, round(bucket_value * precision)) for bucket_value in rounded_values
    ]
    target = max(0, round(scalar_total * precision))
    difference = target - sum(adjusted)
    if difference >= 0:
        target_index = next(
            (idx for idx in range(len(adjusted) - 1, -1, -1) if adjusted[idx] > 0),
            len(adjusted) - 1,
        )
        adjusted[target_index] += difference
    else:
        remaining = -difference
        for idx in range(len(adjusted) - 1, -1, -1):
            removable = min(adjusted[idx], remaining)
            adjusted[idx] -= removable
            remaining -= removable
            if remaining == 0:
                break
    return [round(bucket_value / precision, 5) for bucket_value in adjusted]


def _resolve_day_request_window(
    source: dict[str, Any],
    *,
    today: date | None,
    now: datetime | None,
) -> tuple[date, datetime] | None:
    """Resolve and validate the request day window for a day-power payload.

    Parameters:
        source (dict[str, Any]): App payload containing request meta.
        today (date | None): Reference date for "today" comparisons; defaults to
            the current local date.
        now (datetime | None): Reference time; defaults to current time.

    Returns:
        tuple[date, datetime] | None: The resolved ``(begin, now)`` pair, or
            ``None`` when the request dates are missing. Invalid explicit dates
            raise ``ValueError`` so they cannot use today's compatibility path.
    """
    request = source.get(APP_REQUEST_META)
    if not isinstance(request, dict) or not any(
        key in request
        for key in (
            APP_REQUEST_BEGIN_DATE,
            APP_REQUEST_BEGIN_DATE_ALT,
            APP_REQUEST_END_DATE,
            APP_REQUEST_END_DATE_ALT,
        )
    ):
        return None
    begin = None
    end = None
    if isinstance(request, dict):
        begin = _parse_iso_date(
            request.get(APP_REQUEST_BEGIN_DATE)
            or request.get(APP_REQUEST_BEGIN_DATE_ALT)
        )
        end = _parse_iso_date(
            request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT)
        )

    if begin is None or (end is not None and begin > end):
        msg = "Invalid explicit day request window"
        raise ValueError(msg)
    if end is None and any(
        key in request for key in (APP_REQUEST_END_DATE, APP_REQUEST_END_DATE_ALT)
    ):
        msg = "Invalid explicit day request end"
        raise ValueError(msg)
    if today is None:
        today = datetime.now(UTC).astimezone().date()
    if begin > today:
        msg = "Future day request window"
        raise ValueError(msg)
    if now is None:
        # Local wall clock, but timezone-aware: ``now.date()`` must match the
        # local ``today`` computed above, and callers compare it against the
        # app's local ``beginDate``.
        now = datetime.now(UTC).astimezone()
    return begin, now


def _battery_day_y2_is_soc(source: dict[str, Any], section: str, stat_key: str) -> bool:
    """Identify the observed battery day shape: signed W y1 and SOC % y2."""
    if not section.startswith(APP_SECTION_BATTERY_STAT) or stat_key not in {
        APP_STAT_TOTAL_CHARGE,
        APP_STAT_TOTAL_DISCHARGE,
    }:
        return False
    raw_y1 = source.get(APP_CHART_SERIES_Y1)
    raw_y2 = source.get(APP_CHART_SERIES_Y2)
    if not isinstance(raw_y1, list) or not isinstance(raw_y2, list):
        return False
    y1 = [value for raw in raw_y1 if (value := safe_float(raw)) is not None]
    y2 = [value for raw in raw_y2 if (value := safe_float(raw)) is not None]
    return (
        bool(y1)
        and len(y2) >= _SOC_TRACE_MIN_SAMPLES
        and max(abs(value) for value in y1) > _SOC_TRACE_POWER_MIN_W
        and all(0 <= value <= _SOC_PERCENT_MAX for value in y2)
    )


def day_power_energy_points(  # ruff: ignore[too-many-arguments, too-many-locals, too-many-branches, too-many-statements, complex-structure]  # ruff: ignore[too-many-return-statements]
    source: dict[str, Any],
    section: str,
    stat_key: str,
    *,
    bucket_minutes: int = 60,
    today: date | None = None,
    now: datetime | None = None,
    keep_curve_over_smaller_scalar: bool = False,
    require_complete_day: bool = False,
    use_scalar_total: bool = True,
    require_complete_bucket: bool = False,
) -> list[TrendStatisticPoint]:
    """Convert a day chart curve into kWh statistic buckets for the requested day.

    Parses a chart-series day curve containing documented five-minute power samples in
    watts or energy samples in kWh and aggregates them into contiguous kWh buckets of
    `bucket_minutes`, optionally constraining to `today`/`now` when the request begins
    today. Watt samples are integrated over their five-minute interval; they are never
    treated as energy or summed as watts. If the payload includes a positive scalar
    period total, real curve buckets are reconciled to that total; with
    ``keep_curve_over_smaller_scalar`` (Recorder import) a scalar smaller than the
    curve is treated as lagging and ignored. A scalar without a
    curve cannot establish when energy occurred and is therefore not imported into
    Recorder.

    ``require_complete_day`` additionally requires all documented five-minute
    slots exactly once before the result can certify a completed day's total.
    Extra null positions on an explicit aligned axis are not observations;
    they cannot hide complete own samples or certify an actual gap. Partial
    curves remain available to the normal Recorder bucket path.

    ``use_scalar_total=False`` integrates the samples without energy-total
    reconciliation. Native hourly power means use this mode so a conflicting
    cloud energy scalar cannot change the measured watt curve.

    Parameters:
        source (dict[str, Any]): App payload containing chart series, optional labels
        and request meta.
        section (str): Payload section key used to resolve series and totals.
        stat_key (str): Statistic key used to locate the scalar period total when
        present.
        bucket_minutes (int): Size of each output bucket in minutes; must evenly divide
        24*60. Defaults to 60.
        today: Reference date; defaults to the current local date.
        now (datetime | None): Reference time for limiting samples when the request
        begins today; defaults to current time.

    Returns:
        list[TrendStatisticPoint]: Ordered list of points where `start_date` is the
        bucket start (local date/time for the request day) and `value` is the bucket kWh
        (rounded to 5 decimal places). Returns an empty list for invalid inputs,
        unsupported units, out-of-range request dates, or when scaling rules prevent
        producing buckets.
    """
    # Day charts arrive via multiple paths (HTTP backfill, MQTT shadow, periodic poll).
    # Do NOT discard entire payloads here — only filter implausible values later.
    # Resolve only the series mapped to this metric.
    series_key = day_power_series_key(source, section, stat_key)
    # Day charts arrive via multiple paths (HTTP backfill, MQTT shadow,
    # periodic poll). Do NOT discard entire payloads here — only filter
    # implausible values later. Accept any recognized unit, defaulting to
    # watt when the payload omits unit evidence entirely. Resolve the
    # request window; when no request meta is present (e.g. live MQTT/
    # day-poll payloads) default to the full day so the curve is never
    # discarded.
    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if unit not in {_APP_UNIT_WATT, APP_UNIT_KWH, "wh"}:
        unit = _APP_UNIT_WATT
    try:
        window = _resolve_day_request_window(source, today=today, now=now)
    except ValueError:
        # Preserve the source, but never book invalid dates into today's buckets.
        return []
    if window is None:
        if today is None:
            today = datetime.now(UTC).astimezone().date()
        if now is None:
            now = datetime.now(UTC).astimezone()
        window = (today, now)
    series = source.get(series_key) if series_key else None
    if (
        bucket_minutes <= 0
        or _MINUTES_PER_DAY % bucket_minutes != 0
        or series_key is None
        or not isinstance(series, list)
        or not series
    ):
        return []
    begin, now = window

    labels = source.get(APP_CHART_LABELS)
    if require_complete_bucket:
        if not labels:
            if len(series) != _MINUTES_PER_DAY // _DAY_POWER_SAMPLE_MINUTES:
                return []
        else:
            axis = _explicit_day_curve_minutes(labels, len(series))
            if axis is None or any(
                minute % _DAY_POWER_SAMPLE_MINUTES for minute in axis
            ):
                return []
    parsed_labels = labels if isinstance(labels, list) else None
    current_day_limit_minute = (
        now.hour * _MINUTES_PER_HOUR + now.minute
        if begin == now.date()
        else _MINUTES_PER_DAY - 1
    )
    scalar_total = (
        effective_period_total_value(source, section, stat_key)
        if use_scalar_total
        else None
    )
    energy_scale = app_energy_unit_scale(source) if unit != _APP_UNIT_WATT else None
    if scalar_total is not None and energy_scale is not None:
        scalar_total *= energy_scale
    if (
        is_pv_generation_metric(section, stat_key)
        and scalar_total is not None
        and scalar_total < 0
    ):
        scalar_total = None
    if unit == _APP_UNIT_WATT and _battery_day_y2_is_soc(source, section, stat_key):
        # The observed scalar can lag the signed power samples by hours.
        # Keep the measured curve; the independent local-day guard can still
        # reconcile today's complete total when it is available.
        scalar_total = None

    buckets: dict[int, float] = {}
    sample_minutes: set[int] = set()
    observed_samples = 0
    for index, raw in enumerate(series):
        minute = _day_power_sample_minute(parsed_labels, index)
        if minute is None or minute > current_day_limit_minute:
            continue
        sample_value = _day_power_sample_energy_value(
            raw,
            section,
            stat_key,
            series_key,
        )
        if sample_value is None:
            continue
        observed_samples += 1
        sample_minutes.add(minute)
        sample_kwh = (
            sample_value * energy_scale
            if energy_scale is not None
            else sample_value
            * _DAY_POWER_SAMPLE_MINUTES
            / _MINUTES_PER_HOUR
            / _WATTS_PER_KILOWATT
        )

        bucket_minute = (minute // bucket_minutes) * bucket_minutes
        buckets[bucket_minute] = buckets.get(bucket_minute, 0.0) + sample_kwh

    if not buckets:
        return []
    if require_complete_day:
        day_samples = _MINUTES_PER_DAY // _DAY_POWER_SAMPLE_MINUTES
        known_axis = (
            len(series) == day_samples
            if labels is None or labels == []
            else _explicit_day_curve_minutes(labels, len(series)) is not None
        )
        if (
            observed_samples != day_samples
            or sample_minutes
            != set(range(0, _MINUTES_PER_DAY, _DAY_POWER_SAMPLE_MINUTES))
            or not known_axis
        ):
            return []

    if require_complete_bucket:
        # A complete hour needs all of its distinct, own five-minute samples.
        # A missing value is never interpreted as zero or a full-hour mean.
        buckets = {
            minute: value
            for minute, value in buckets.items()
            if all(
                sample in sample_minutes
                for sample in range(
                    minute, minute + bucket_minutes, _DAY_POWER_SAMPLE_MINUTES
                )
            )
        }
        if not buckets:
            return []

    raw_total = sum(buckets.values())
    # Statistics import: a smaller cloud scalar lags the measured curve
    # (payloads 2026-08-14..09-23; docs/SENSOR_SOURCE_PATHS.md), so keep the
    # observed 5-minute energy instead of shrinking it.
    lagging_scalar = keep_curve_over_smaller_scalar and (
        scalar_total is not None and 0 < scalar_total < raw_total
    )
    reconciled_total = None if lagging_scalar else scalar_total
    if reconciled_total is not None and reconciled_total > 0:
        if raw_total > 0:
            scale = reconciled_total / raw_total
            buckets = {minute: value * scale for minute, value in buckets.items()}
        else:
            return []
    elif reconciled_total is not None and reconciled_total == 0 and raw_total == 0:
        # Zero total with zero curve: keep the zero-valued buckets rather
        # than discarding them. A day with 0 kWh is valid data.
        pass

    bucket_items = sorted(buckets.items())
    rounded_values = [round(max(value, 0.0), 5) for _minute, value in bucket_items]
    if reconciled_total is not None and reconciled_total > 0 and raw_total > 0:
        rounded_values = _reconcile_rounded_day_values(
            rounded_values,
            reconciled_total,
        )

    # Intentionally naive: these are LOCAL wall-clock bucket starts from the
    # app's day curve. ``JackerySolarVaultCoordinator._local_statistic_start``
    # attaches Home Assistant's configured timezone. Adding tzinfo here would
    # shift every bucket by the UTC offset and corrupt long-term statistics.
    return [
        TrendStatisticPoint(
            datetime(begin.year, begin.month, begin.day, minute // 60, minute % 60),
            bucket_value,
        )
        for (minute, _value), bucket_value in zip(
            bucket_items, rounded_values, strict=False
        )
    ]


@dataclass(frozen=True, slots=True)
class AppDataQualityWarning:
    """One non-repairing app-statistics contradiction warning."""

    level: str
    reason: str
    metric_key: str
    label: str
    source_section: str
    source_value: float
    reference_section: str
    reference_value: float
    source_request: dict[str, Any] | None = None
    reference_request: dict[str, Any] | None = None
    source_chart_series_key: str | None = None
    reference_chart_series_key: str | None = None
    total_method: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a compact diagnostics-safe dict without empty optional fields."""
        data: dict[str, Any] = {
            "level": self.level,
            "reason": self.reason,
            "metric_key": self.metric_key,
            "label": self.label,
            "source_section": self.source_section,
            "source_value": self.source_value,
            "reference_section": self.reference_section,
            "reference_value": self.reference_value,
        }
        if self.source_request is not None:
            data["source_request"] = self.source_request
        if self.reference_request is not None:
            data["reference_request"] = self.reference_request
        if self.source_chart_series_key is not None:
            data["source_chart_series_key"] = self.source_chart_series_key
        if self.reference_chart_series_key is not None:
            data["reference_chart_series_key"] = self.reference_chart_series_key
        if self.total_method is not None:
            data["total_method"] = self.total_method
        return data


_DATA_QUALITY_PERIOD_METRICS: Final = (
    (
        "device_ongrid_output_energy",
        "Device grid-side output energy",
        APP_SECTION_HOME_STAT,
        APP_STAT_TOTAL_OUT_GRID_ENERGY,
    ),
    (
        "device_ongrid_input_energy",
        "Device grid-side input energy",
        APP_SECTION_HOME_STAT,
        APP_STAT_TOTAL_IN_GRID_ENERGY,
    ),
    (
        "pv_energy",
        "PV energy",
        APP_SECTION_PV_STAT,
        APP_STAT_TOTAL_SOLAR_ENERGY,
    ),
    (
        "battery_charge_energy",
        "Battery charge energy",
        APP_SECTION_BATTERY_STAT,
        APP_STAT_TOTAL_CHARGE,
    ),
    (
        "battery_discharge_energy",
        "Battery discharge energy",
        APP_SECTION_BATTERY_STAT,
        APP_STAT_TOTAL_DISCHARGE,
    ),
)


def _request_dict(source: dict[str, Any]) -> dict[str, Any] | None:
    request = source.get(APP_REQUEST_META)
    return dict(request) if isinstance(request, dict) else None


def _period_source(
    payload: dict[str, Any],
    section_prefix: str,
    period: str,
) -> tuple[str, dict[str, Any]] | None:
    section = _period_section(section_prefix, period)
    source = payload.get(section)
    if not isinstance(source, dict):
        return None
    return section, source


def _period_warning(  # ruff: ignore[too-many-arguments]
    *,
    reason: str,
    metric_key: str,
    label: str,
    source_section: str,
    source_source: dict[str, Any],
    source_value: float,
    reference_section: str,
    reference_source: dict[str, Any],
    reference_value: float,
    stat_key: str,
) -> AppDataQualityWarning:
    return AppDataQualityWarning(
        level="warning",
        reason=reason,
        metric_key=metric_key,
        label=label,
        source_section=source_section,
        source_value=source_value,
        reference_section=reference_section,
        reference_value=reference_value,
        source_request=_request_dict(source_source),
        reference_request=_request_dict(reference_source),
        source_chart_series_key=trend_series_key(source_section, stat_key),
        reference_chart_series_key=trend_series_key(reference_section, stat_key),
        total_method="chart_series_sum",
    )


def app_data_quality_warnings(
    payload: dict[str, Any],
    *,
    today: date | None = None,
) -> list[AppDataQualityWarning]:
    """Return contradictions without mutating or repairing app statistics."""
    _ = today
    warnings: list[AppDataQualityWarning] = []
    for metric_key, label, section_prefix, stat_key in _DATA_QUALITY_PERIOD_METRICS:
        year = _period_source(payload, section_prefix, DATE_TYPE_YEAR)
        week = _period_source(payload, section_prefix, DATE_TYPE_WEEK)
        if year is not None and week is not None:
            year_section, year_source = year
            week_section, week_source = week
            year_total = effective_period_total_value(
                year_source, year_section, stat_key
            )
            week_total = effective_period_total_value(
                week_source, week_section, stat_key
            )
            if (
                year_total is not None
                and week_total is not None
                and year_total + _NEAR_ZERO_EPSILON < week_total
            ):
                warnings.append(
                    _period_warning(
                        reason="year_less_than_week",
                        metric_key=metric_key,
                        label=label,
                        source_section=year_section,
                        source_source=year_source,
                        source_value=year_total,
                        reference_section=week_section,
                        reference_source=week_source,
                        reference_value=week_total,
                        stat_key=stat_key,
                    )
                )

    statistic = payload.get("statistic")
    pv_year = _period_source(payload, APP_SECTION_PV_STAT, DATE_TYPE_YEAR)
    if isinstance(statistic, dict) and pv_year is not None:
        year_section, year_source = pv_year
        lifetime_total = safe_float(statistic.get(APP_STAT_TOTAL_GENERATION))
        year_total = effective_period_total_value(
            year_source,
            year_section,
            APP_STAT_TOTAL_SOLAR_ENERGY,
        )
        if (
            lifetime_total is not None
            and year_total is not None
            and lifetime_total + _NEAR_ZERO_EPSILON < year_total
        ):
            warnings.append(
                AppDataQualityWarning(
                    level="warning",
                    reason="lifetime_less_than_year",
                    metric_key="pv_energy",
                    label="PV energy",
                    source_section="statistic",
                    source_value=lifetime_total,
                    reference_section=year_section,
                    reference_value=year_total,
                    reference_request=_request_dict(year_source),
                    reference_chart_series_key=trend_series_key(
                        year_section,
                        APP_STAT_TOTAL_SOLAR_ENERGY,
                    ),
                    total_method="chart_series_sum",
                )
            )
    return warnings


def normalized_data_quality_warnings(
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicate and sort warning dictionaries for stable diagnostics."""
    unique: dict[str, dict[str, Any]] = {}
    for warning in warnings:
        key = json.dumps(warning, sort_keys=True, default=str, separators=(",", ":"))
        unique[key] = warning
    return sorted(
        unique.values(),
        key=lambda item: (
            str(item.get("reason") or ""),
            str(item.get("metric_key") or ""),
            str(item.get("source_section") or ""),
            str(item.get("reference_section") or ""),
        ),
    )


def _request_range_text(section: str, request: object) -> str | None:
    if not isinstance(request, dict):
        return None
    date_type = request.get(APP_REQUEST_DATE_TYPE) or request.get(
        APP_REQUEST_DATE_TYPE_ALT
    )
    begin = request.get(APP_REQUEST_BEGIN_DATE) or request.get(
        APP_REQUEST_BEGIN_DATE_ALT
    )
    end = request.get(APP_REQUEST_END_DATE) or request.get(APP_REQUEST_END_DATE_ALT)
    if not date_type or not begin or not end:
        return None
    return f"{section}: {date_type} {begin}..{end}"


def format_data_quality_warning(warning: dict[str, Any]) -> str:
    """Format one data-quality warning for repairs/system-log messages."""
    label = warning.get("label") or warning.get("metric_key") or "App statistic"
    source_section = warning.get("source_section")
    reference_section = warning.get("reference_section")
    source_value = warning.get("source_value")
    reference_value = warning.get("reference_value")
    message = (
        f"{label}: {source_section}={source_value} "
        f"< {reference_section}={reference_value}"
    )
    ranges = [
        value
        for value in (
            _request_range_text(str(source_section), warning.get("source_request")),
            _request_range_text(
                str(reference_section),
                warning.get("reference_request"),
            ),
        )
        if value is not None
    ]
    if ranges:
        message = f"{message} [{"; ".join(ranges)}]"
    return message


# ---------------------------------------------------------------------------
# Power-flow calculation helpers
# ---------------------------------------------------------------------------
def directional_power_value(
    source: dict[str, Any],
    positive_keys: tuple[str, ...],
    negative_keys: tuple[str, ...],
) -> float | None:
    """Compute the net directional power.

    Sum values from positive keys and subtract values from negative keys.

    Parameters:
        source (dict[str, Any]): Mapping containing numeric power values.
        positive_keys (tuple[str, ...]): Keys in `source` whose values contribute
        positively to the net sum.
        negative_keys (tuple[str, ...]): Keys in `source` whose values contribute
        negatively to the net sum.

    Returns:
        float | None: The net power (sum of positive keys minus sum of negative keys) if
        at least one numeric value is present, `None` otherwise.
    """
    positive = 0.0
    negative = 0.0
    found = False

    for key in positive_keys:
        if key in source and source.get(key) is not None:
            value = safe_float(source.get(key))
            if value is not None:
                positive += value
                found = True

    for key in negative_keys:
        if key in source and source.get(key) is not None:
            value = safe_float(source.get(key))
            if value is not None:
                negative += value
                found = True

    return positive - negative if found else None


def signed_phase_power_values(ct: dict[str, Any]) -> list[float] | None:
    """Determine signed power for each CT phase.

    Positive values indicate grid import and negative values indicate export.

    Parameters:
        ct (dict[str, Any]): CT payload mapping containing phase power fields referenced
        by CT_PHASE_POWER_PAIRS.

    Returns:
        list[float] | None: A list of signed per-phase power values in the same order as
        CT_PHASE_POWER_PAIRS, or `None` if any phase value is missing or cannot be
        computed.
    """
    values: list[float] = []
    for pos_key, neg_key in CT_PHASE_POWER_PAIRS:
        value = directional_power_value(ct, (pos_key,), (neg_key,))
        if value is None:
            return None
        values.append(value)
    return values


def smart_meter_net_power(ct: dict[str, Any]) -> float | None:
    """Determine the net grid power from a CT payload.

    Returns:
        float: Net grid power in watts; positive = import, negative = export.
        `None` if no CT-derived power values are available.
    """
    total = directional_power_value(
        ct, (CT_TOTAL_POWER_PAIR[0],), (CT_TOTAL_POWER_PAIR[1],)
    )
    if total is not None:
        return total
    phases = signed_phase_power_values(ct)
    return sum(phases) if phases is not None else None


def calculated_smart_meter_power(
    ct: dict[str, Any],
    calculation: str,
) -> float | None:
    """Return a power value derived from CT payloads.

    Apply the requested calculation mode.

    Parameters:
        ct (dict): CT/meter payload used to derive signed net and per-phase power
        values.
        calculation: ``net_import``, ``net_export``, ``gross_import``,
            ``gross_export`` or ``gross_flow``.

    Returns:
        float | None: Calculated power in the same units as the input values, or `None`
        when required inputs are missing or the calculation mode is unrecognized.
    """
    net = smart_meter_net_power(ct)
    phases = signed_phase_power_values(ct)

    result: float | None = None
    if calculation == "net_import" and net is not None:
        result = max(net, 0.0)
    elif calculation == "net_export" and net is not None:
        result = max(-net, 0.0)
    elif calculation == "gross_import" and phases is not None:
        result = sum(max(value, 0.0) for value in phases)
    elif calculation == "gross_export" and phases is not None:
        result = sum(max(-value, 0.0) for value in phases)
    elif calculation == "gross_flow" and phases is not None:
        result = sum(abs(value) for value in phases)
    return result


class HomeConsumptionPower(NamedTuple):
    """Calculated home-load value plus diagnostic components."""

    value: float
    smart_meter_net_power: float | None
    jackery_input_power: float
    jackery_output_power: float
    source: str


def first_power_value(source: dict[str, Any], *keys: str) -> float | None:
    """Get the first numeric power value from the provided keys.

    Check keys in source order.

    Parameters:
        source (dict[str, Any]): Mapping containing candidate power values.
        *keys (str): Keys to check in priority order.

    Returns:
        float | None: The first value successfully coerced to a number, or `None` if no
        numeric value is found.
    """
    for key in keys:
        if key in source and source.get(key) is not None:
            value = safe_float(source.get(key))
            if value is not None:
                return value
    return None


def first_nonzero_power_value(source: dict[str, Any], *keys: str) -> float | None:
    """Return the first non-zero power value, falling back to the first zero."""
    first_zero: float | None = None
    for key in keys:
        if key not in source or source.get(key) is None:
            continue
        value = safe_float(source.get(key))
        if value is None:
            continue
        if value != 0:
            return value
        if first_zero is None:
            first_zero = value
    return first_zero


def jackery_reported_home_load_power(props: dict[str, Any]) -> float | None:
    """Get the Jackery-reported live home/other-load power from device properties.

    Checks the known fields for reported home/other load power and returns the first
    available value.

    Parameters:
        props (dict[str, Any]): Device properties payload to inspect for power fields.

    Returns:
        float | None: The reported power in watts if present and parseable, `None`
        otherwise.
    """
    return first_power_value(props, FIELD_OTHER_LOAD_PW)


def jackery_grid_side_input_power(props: dict[str, Any]) -> float | None:
    """AC input power reported by the Jackery device from the grid/home side.

    Returns:
        float: AC input power in watts, or `None` if no suitable value is present.
    """
    return first_nonzero_power_value(
        props,
        FIELD_IN_GRID_SIDE_PW,
        FIELD_IN_ONGRID_PW,
    )


def jackery_grid_side_output_power(props: dict[str, Any]) -> float | None:
    """Return the AC power Jackery is supplying to the grid/home side.

    Parameters:
        props (dict[str, Any]): Device properties dictionary to read output power fields
        from.

    Returns:
        float: Power in watts if a known output field contains a numeric value, `None`
        otherwise.
    """
    return first_nonzero_power_value(
        props,
        FIELD_OUT_GRID_SIDE_PW,
        FIELD_OUT_ONGRID_PW,
    )


def jackery_inverter_ac_input_power(props: dict[str, Any]) -> float | None:
    """AC power the inverter draws on its AC port (grid/AC charging).

    ``gridInPw`` (SystemBody) / ``inOngridPw`` (HomeBody) are the
    inverter's AC input, NOT the household grid import — the import
    measurement point is ``inGridSidePw``.

    Returns:
        float | None: AC input power in watts, or `None` when neither
        inverter field is present.
    """
    return first_nonzero_power_value(
        props,
        FIELD_GRID_IN_PW,
        FIELD_IN_ONGRID_PW,
    )


def jackery_inverter_ac_output_power(props: dict[str, Any]) -> float | None:
    """Total AC power the inverter delivers (house share + grid export).

    ``gridOutPw`` (SystemBody) / ``outOngridPw`` (HomeBody) per the
    SystemBody wire identity ``otherLoadPw = gridOutPw - outGridSidePw +
    inGridSidePw``; the pure export is ``outGridSidePw``.

    Returns:
        float | None: AC output power in watts, or `None` when neither
        inverter field is present.
    """
    return first_nonzero_power_value(
        props,
        FIELD_GRID_OUT_PW,
        FIELD_OUT_ONGRID_PW,
    )


def jackery_grid_net_power(props: dict[str, Any]) -> int | None:
    """Net grid-side power: positive = import, negative = export.

    Uses only the true grid-side measurement fields ``inGridSidePw`` /
    ``outGridSidePw``. The on-grid family (``gridIn/OutPw``,
    ``in/outOngridPw``) is the inverter's AC input/output (house share +
    export) per the SystemBody wire identity ``otherLoadPw = gridOutPw -
    outGridSidePw + inGridSidePw`` and must never be used as an
    import/export fallback.

    Returns:
        int | None: Net grid power in watts, or `None` when the device
        reports no grid-side measurement point (HomeBody frames).
    """
    in_pw = safe_int(first_power_value(props, FIELD_IN_GRID_SIDE_PW))
    out_pw = safe_int(first_power_value(props, FIELD_OUT_GRID_SIDE_PW))
    if in_pw is None or out_pw is None:
        return None
    return in_pw - out_pw


def jackery_corrected_home_consumption_power(
    ct: dict[str, Any],
    props: dict[str, Any],
) -> HomeConsumptionPower | None:
    """Compute corrected home consumption power and accompanying diagnostic fields.

    If the Jackery device reports an explicit home/other load, that reported value
    (clamped to zero) is used and returned with diagnostic fields. If no reported home
    load is available and either the smart-meter net power is missing or both Jackery
    input and output powers are zero, the function returns `None`. Otherwise the
    function computes `meter_net - jackery_input + jackery_output`, clamps the result to
    zero, and returns it with diagnostic fields and a source identifier.

    Parameters:
        ct (dict[str, Any]): CT/smart-meter payload used to derive smart-meter net
        power.
        props (dict[str, Any]): Jackery device properties payload used to read reported
        home load and grid-side input/output powers.

    Returns:
        HomeConsumptionPower | None: A NamedTuple with fields
            - `value`: corrected non-negative home consumption power.
            - `smart_meter_net_power`: smart-meter net power when available.
            - `jackery_input_power`: Jackery grid-side input power,
            - `jackery_output_power`: Jackery grid-side output power,
            - `source`: reported other-load field or the smart-meter calculation.
        Returns `None` when insufficient inputs are available to compute a corrected
        consumption.
    """
    meter_net = smart_meter_net_power(ct)
    jackery_input = jackery_grid_side_input_power(props) or 0.0
    jackery_output = jackery_grid_side_output_power(props) or 0.0

    reported_home_load = jackery_reported_home_load_power(props)
    if reported_home_load is not None:
        return HomeConsumptionPower(
            value=max(reported_home_load, 0.0),
            smart_meter_net_power=meter_net,
            jackery_input_power=jackery_input,
            jackery_output_power=jackery_output,
            source=FIELD_OTHER_LOAD_PW,
        )

    if meter_net is None or (not jackery_input and not jackery_output):
        return None

    calculated = meter_net - jackery_input + jackery_output
    return HomeConsumptionPower(
        value=max(calculated, 0.0),
        smart_meter_net_power=meter_net,
        jackery_input_power=jackery_input,
        jackery_output_power=jackery_output,
        source="smart_meter_net_minus_input_plus_output",
    )


# ---------------------------------------------------------------------------
# Trend/statistic helpers
# ---------------------------------------------------------------------------
def _chart_series_key_for_stat(section: str, stat_key: str) -> str | None:
    """Map an app section and statistic key to the corresponding chart-series key.

    Parameters:
        section (str): App payload section identifier (e.g., PV/home/CT/battery trend or
        stat section).
        stat_key (str): Statistic key within the section.

    Returns:
        str | None: The chart-series key (e.g., `APP_CHART_SERIES_Y`,
        `APP_CHART_SERIES_Y1`, ...) associated with the given section/stat pair, or
        `None` if no mapping exists.
    """
    if stat_key == APP_STAT_TOTAL_SOLAR_REVENUE:
        return None
    if section.startswith((APP_SECTION_PV_TRENDS, APP_SECTION_HOME_TRENDS)):
        return APP_CHART_SERIES_Y

    # The EPS day view exposes one unlabeled x/y curve and cannot safely back
    # either directional total. Other period shapes have documented mappings.
    if section.startswith(APP_SECTION_EPS_STAT) and section.endswith(
        f"_{DATE_TYPE_DAY}"
    ):
        return None

    mappings = (
        (
            APP_SECTION_PV_STAT,
            {
                APP_STAT_TOTAL_SOLAR_ENERGY: APP_CHART_SERIES_Y,
                APP_STAT_PV1_ENERGY: APP_CHART_SERIES_Y1,
                APP_STAT_PV2_ENERGY: APP_CHART_SERIES_Y2,
                APP_STAT_PV3_ENERGY: APP_CHART_SERIES_Y3,
                APP_STAT_PV4_ENERGY: APP_CHART_SERIES_Y4,
            },
        ),
        (
            APP_SECTION_HOME_STAT,
            {
                APP_STAT_TOTAL_IN_GRID_ENERGY: APP_CHART_SERIES_Y1,
                APP_STAT_TOTAL_OUT_GRID_ENERGY: APP_CHART_SERIES_Y2,
            },
        ),
        (
            APP_SECTION_CT_STAT,
            {
                APP_STAT_TOTAL_CT_INPUT_ENERGY: APP_CHART_SERIES_Y1,
                APP_STAT_TOTAL_CT_OUTPUT_ENERGY: APP_CHART_SERIES_Y2,
            },
        ),
        (
            APP_SECTION_EPS_STAT,
            {
                APP_STAT_TOTAL_IN_EPS_ENERGY: APP_CHART_SERIES_Y1,
                APP_STAT_TOTAL_OUT_EPS_ENERGY: APP_CHART_SERIES_Y2,
            },
        ),
        (
            APP_SECTION_BATTERY_TRENDS,
            {
                APP_STAT_TOTAL_TREND_CHARGE_ENERGY: APP_CHART_SERIES_Y1,
                APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY: APP_CHART_SERIES_Y2,
            },
        ),
        (
            APP_SECTION_BATTERY_STAT,
            {
                APP_STAT_TOTAL_CHARGE: APP_CHART_SERIES_Y1,
                APP_STAT_TOTAL_DISCHARGE: APP_CHART_SERIES_Y2,
                APP_DEVICE_STAT_ONGRID_TO_BATTERY: APP_CHART_SERIES_Y3,
                APP_DEVICE_STAT_BATTERY_TO_AC: APP_CHART_SERIES_Y4,
                APP_DEVICE_STAT_BATTERY_TO_GRID: APP_CHART_SERIES_Y5,
            },
        ),
    )
    for prefix, mapping in mappings:
        if section.startswith(prefix):
            return mapping.get(stat_key)

    return None


def _series_contains_negative_samples(source: dict[str, Any], series_key: str) -> bool:
    """Return true if an app chart series contains signed negative samples."""
    series = source.get(series_key)
    if not isinstance(series, list):
        return False
    return any((value := safe_float(raw)) is not None and value < 0 for raw in series)


def _series_contains_positive_samples(source: dict[str, Any], series_key: str) -> bool:
    """Return true if an app chart series contains usable positive samples."""
    series = source.get(series_key)
    if not isinstance(series, list):
        return False
    return any((value := safe_float(raw)) is not None and value > 0 for raw in series)


def _series_signed_magnitude(
    source: dict[str, Any],
    series_key: str,
    *,
    negative: bool,
) -> float:
    """Return the total magnitude of samples with the requested sign."""
    series = source.get(series_key)
    if not isinstance(series, list):
        return 0.0
    magnitude = 0.0
    for raw in series:
        value = safe_float(raw)
        if value is None:
            continue
        if negative and value < 0:
            magnitude -= value
        elif not negative and value > 0:
            magnitude += value
    return magnitude


def trend_series_key(section: str, stat_key: str) -> str | None:
    """Map a statistic to its period chart-series key.

    Use the section and statistic key to select the corresponding series.

    Only returns a chart-series key when `section` denotes a day, week, month, or
    year payload; otherwise returns `None`. Day sections map too - the sensor
    layer uses this as its period-sensor dispatch - but their arrays are W power
    curves, so the energy interpretation is blocked downstream
    (:func:`effective_trend_series_values` returns ``None`` for day payloads).

    Returns:
        Chart-series key, or ``None`` when no documented mapping exists.
    """
    if not section.endswith((
        f"_{DATE_TYPE_DAY}",
        f"_{DATE_TYPE_WEEK}",
        f"_{DATE_TYPE_MONTH}",
        f"_{DATE_TYPE_YEAR}",
    )):
        return None
    return _chart_series_key_for_stat(section, stat_key)


def day_power_series_key(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> str | None:
    """Get the chart-series key used for day power curves.

    Prefer explicit day-period detection, but fall back to any available
    chart series in the payload. Day curves arrive via HTTP backfill, MQTT
    shadow responses, and periodic polls — not all carry the `_day` suffix
    or request meta.
    """
    # First try the strict day-period detection
    if is_day_period_payload(source, section):
        if section.startswith(APP_SECTION_EPS_STAT):
            key = {
                APP_STAT_TOTAL_IN_EPS_ENERGY: APP_CHART_SERIES_Y1,
                APP_STAT_TOTAL_OUT_EPS_ENERGY: APP_CHART_SERIES_Y2,
            }.get(stat_key)
            return (
                key if key is not None and isinstance(source.get(key), list) else None
            )
        if (
            section.startswith((APP_SECTION_BATTERY_STAT, APP_SECTION_BATTERY_TRENDS))
            and stat_key
            in {
                APP_STAT_TOTAL_CHARGE,
                APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
                APP_STAT_TOTAL_DISCHARGE,
                APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
            }
            and not any(
                isinstance(source.get(key), list) and source[key]
                for key in (APP_CHART_SERIES_Y1, APP_CHART_SERIES_Y2)
            )
            and isinstance(source.get(APP_CHART_SERIES_Y), list)
        ):
            return APP_CHART_SERIES_Y
        if _battery_day_y2_is_soc(source, section, stat_key):
            return APP_CHART_SERIES_Y1
        if section.startswith((
            APP_SECTION_BATTERY_STAT,
            APP_SECTION_BATTERY_TRENDS,
        )) and stat_key in {
            APP_STAT_TOTAL_DISCHARGE,
            APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
        }:
            signed_y1 = _series_signed_magnitude(
                source,
                APP_CHART_SERIES_Y1,
                negative=True,
            )
            positive_y2 = _series_signed_magnitude(
                source,
                APP_CHART_SERIES_Y2,
                negative=False,
            )
            if signed_y1 > positive_y2:
                return APP_CHART_SERIES_Y1
        return _chart_series_key_for_stat(section, stat_key)

    # Missing request metadata cannot authorize a different metric's curve.
    mapped = _chart_series_key_for_stat(section, stat_key)
    return (
        mapped if mapped is not None and isinstance(source.get(mapped), list) else None
    )


def trend_series_total(  # ruff: ignore[too-many-return-statements]
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> float | None:
    """Compute the period total for a trend/chart statistic section from an app payload.

    For day-period sections the function uses the effective period total derived from
    the payload.
    For non-day sections it requires a mapped chart-series key and that the section unit
    is `kwh`.
    If the chart-series list is missing the function applies guarded fallbacks:
    - Home-stat zero totals remain valid when grid series are present.
    - CT/EPS totals remain valid with a compatible unit, including zero.

    Returns:
        float: The period total rounded to 2 decimals, or `None` when a reliable total
        cannot be determined.
    """
    if is_day_period_payload(source, section):
        total = effective_period_total_value(source, section, stat_key)

        # Only reject "W" for CT/EPS day periods where the entire payload is invalid
        # For PV/Home/Grid, "W" on day period means the power curves are in W
        # but scalar totals are in kWh - this is valid
        is_ct_eps_day = section.startswith((APP_SECTION_CT_STAT, APP_SECTION_EPS_STAT))
        unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
        if is_ct_eps_day and unit == "w":
            return None

        if is_ct_eps_day and total is not None and total < 0:
            # CT/EPS day endpoints commonly return a scalar negative alongside an
            # empty/no-data power curve. Negative is not affirmative energy data.
            # Zero is a valid reported value (no energy flow in that period).
            return None
        return round(total, 2) if total is not None else None

    values = effective_trend_series_values(source, section, stat_key) or []
    if pv_channel_scalar_is_lifetime_offset(source, section, stat_key) and (
        not values or any(value is None for value in values)
    ):
        return None

    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return None

    unit_scale = app_energy_unit_scale(source)
    if unit_scale is None:
        return None

    is_ct_eps = section.startswith((APP_SECTION_CT_STAT, APP_SECTION_EPS_STAT))

    series = source.get(series_key)
    if not isinstance(series, list):
        server_total = effective_period_total_value(source, section, stat_key)
        if (
            section.startswith(APP_SECTION_HOME_STAT)
            and server_total is not None
            and not server_total
            and any(isinstance(source.get(k), list) for k in APP_HOME_GRID_SERIES_KEYS)
        ):
            return 0.0
        if is_ct_eps and server_total is not None and server_total >= 0:
            return round(server_total * unit_scale, 2)
        return None

    if _trend_date_type(section, source) == DATE_TYPE_YEAR:
        server_total = effective_period_total_value(source, section, stat_key)
        year_total = complete_year_series_total(values, server_total)
        return round(year_total * unit_scale, 2) if year_total is not None else None
    valid_values = [v for v in values if v is not None]

    if not valid_values:
        server_total = effective_period_total_value(source, section, stat_key)
        if is_ct_eps and server_total is not None and server_total >= 0:
            return round(server_total * unit_scale, 2)
        return None

    return round(sum(valid_values) * unit_scale, 2)


def trend_series_has_value(  # ruff: ignore[too-many-return-statements]
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> bool:
    """Determine whether an app period has a usable numeric value.

    Inspect the specified section and statistic key.

    Considers day-period scalars, chart-series lists for non-day periods (only when the
    unit is kWh or unspecified), and the module's special-case allowances for home and
    CT/EPS sections when series data or server totals imply a valid value.

    Returns:
        `true` if a numeric value can be derived from the payload for the section and
        stat_key, `false` otherwise.
    """
    unit_scale = app_energy_unit_scale(source)
    is_ct_eps = section.startswith((APP_SECTION_CT_STAT, APP_SECTION_EPS_STAT))

    if is_day_period_payload(source, section):
        server_total = safe_float(source.get(stat_key))
        # Only reject "W" for CT/EPS day periods where the entire payload is invalid
        # For PV/Home/Grid, "W" on day period means the power curves are in W
        # but scalar totals are in kWh - this is valid
        is_ct_eps_day = is_ct_eps
        unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
        if is_ct_eps_day and unit == "w":
            return False

        if is_ct_eps_day:
            # CT/EPS day totals are valid when explicitly non-negative in kWh.
            return (
                unit_scale is not None
                and server_total is not None
                and server_total >= 0
            )
        return server_total is not None

    series_key = trend_series_key(section, stat_key)
    if not series_key:
        return False

    # CT/EPS period totals cannot use watts, which is a power unit.
    unit = str(source.get(APP_STAT_UNIT) or "").strip().lower()
    if is_ct_eps and unit == "w":
        return False

    if unit_scale is None:
        # For CT/EPS stat, if unit is missing but we have valid totals, allow it
        server_total = effective_period_total_value(source, section, stat_key)
        if is_ct_eps and server_total is not None and server_total >= 0:
            # Check if we have any series data at all
            series = source.get(series_key) if series_key else None
            if isinstance(series, list) and len(series) == 0:
                # Empty series but valid total - this is a valid no-chart-data case
                return True
        return False

    series = source.get(series_key)
    if not isinstance(series, list):
        server_total = effective_period_total_value(source, section, stat_key)
        if (
            section.startswith(APP_SECTION_HOME_STAT)
            and server_total is not None
            and not server_total
            and any(isinstance(source.get(k), list) for k in APP_HOME_GRID_SERIES_KEYS)
        ):
            return True
        # An explicit non-negative total with a known unit is a real reading,
        # even without a curve: Jackery serves the series as an empty *string*
        # for young devices (``device_pv_stat_month`` with ``y: ''`` and
        # ``totalSolarEnergy: '0'``, see docs/DIAGNOSE_2026-09-02 §D). Only the
        # empty envelope from ``code=0`` stays discarded — it carries neither a
        # unit nor a total, per docs/CODEMAPS/data.md.
        return bool(
            unit_scale is not None and server_total is not None and server_total >= 0
        )

    if any(safe_float(item) is not None for item in series):
        return True

    # An all-empty curve may still carry an explicit non-negative total. A
    # freshly commissioned device legitimately reports zeros (observed:
    # ``device_battery_stat_month`` with ``y1: []`` and ``totalDischarge:
    # '0.01'``), and discarding those left the period sensors unavailable.
    # The bare ``code=0`` envelope carries no unit and no total, so it stays
    # discarded per docs/CODEMAPS/data.md.
    stat_value = safe_float(source.get(stat_key))
    return bool(unit_scale is not None and stat_value is not None and stat_value >= 0)


def task_plan_value(
    task_plan: dict[str, Any], *keys: str
) -> str | int | float | bool | None:  # primitive payload value
    """Retrieve the first non-None task-plan value.

    Search for any of the given keys.

    Searches in this order: the top-level of `task_plan`, the `TASK_PLAN_BODY`
    dictionary (if present), then each dictionary item in the `TASK_PLAN_TASKS` list (if
    present). Keys are checked in the order provided and the first non-`None` match is
    returned.

    Parameters:
        task_plan (dict): The task-plan payload to search.
        *keys (str): One or more keys to look up, checked in order.

    Returns:
        Any: The first non-`None` value found for the provided keys, or `None` if none
        are present.
    """
    for key in keys:
        if key in task_plan and task_plan.get(key) is not None:
            return task_plan.get(key)

    body = task_plan.get(TASK_PLAN_BODY)
    if isinstance(body, dict):
        for key in keys:
            if key in body and body.get(key) is not None:
                return body.get(key)

    tasks = task_plan.get(TASK_PLAN_TASKS)
    if isinstance(tasks, list):
        for item in tasks:
            if isinstance(item, dict):
                for key in keys:
                    if key in item and item.get(key) is not None:
                        return item.get(key)
    return None


def trend_payload_has_value(
    source: dict[str, Any],
    section: str,
    stat_key: str,
) -> bool:
    """Determine whether a trend payload has a usable period value.

    Inspect the provided period sensor data.

    Checks for a computed chart-series total for the given section/statistic and, if
    absent, falls back to the scalar value at `stat_key`.

    Returns:
        True if a usable period value exists, False otherwise.
    """
    if section.startswith((APP_SECTION_CT_STAT, APP_SECTION_EPS_STAT)):
        # Keep CT/EPS discovery aligned with the sensor resolver: a scalar
        # zero plus an empty/missing series is the observed no-data shell.
        return trend_series_has_value(source, section, stat_key)
    if trend_series_total(source, section, stat_key) is not None:
        return True
    return safe_float(source.get(stat_key)) is not None


def first_nonblank_int(*values: object) -> int | None:
    """Return the first nonblank value parsed as an integer."""
    result: int | None = None
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            break
        if isinstance(value, int):
            result = value
            break
        if isinstance(value, float):
            result = int(value) if value.is_integer() else None
            break
        text = str(value).strip()
        if not text:
            continue
        if not WHOLE_INT_TEXT_RE.fullmatch(text):
            break
        whole, _dot, _fraction = text.partition(".")
        with contextlib.suppress(ValueError):
            result = int(whole)
        break
    return result


def _is_signed_battery_energy_curve(section: str, stat_key: str) -> bool:
    """Return whether a day curve is a signed battery charge/discharge curve."""
    return section.startswith((
        APP_SECTION_BATTERY_STAT,
        APP_SECTION_BATTERY_TRENDS,
    )) and stat_key in {
        APP_STAT_TOTAL_CHARGE,
        APP_STAT_TOTAL_DISCHARGE,
        APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
        APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
    }


def _can_distribute_scalar_day_total(section: str, stat_key: str) -> bool:
    """Return whether an explicit zero day curve may be filled from its total."""
    return section.startswith(APP_SECTION_HOME_STAT) and stat_key in {
        APP_STAT_TOTAL_IN_GRID_ENERGY,
        APP_STAT_TOTAL_OUT_GRID_ENERGY,
    }


def normalize_account(value: str) -> str:
    """Normalize user-facing account identifiers before auth and unique IDs."""
    return value.strip()


# Home/Portable payload classification. Previously copied verbatim into all
# seven platform modules (button, number, select, sensor, services, switch,
# text); the copies had already drifted in signature and docstrings.
HOME_PAYLOAD_EVIDENCE_KEYS: Final = frozenset({
    "autoStandby",
    "batInPw",
    "batOutPw",
    "batSoc",
    "defaultPw",
    "gridInPw",
    "isAutoStandby",
    "isFollowMeterPw",
    "maxGridStdPw",
    "maxInvStdPw",
    "maxIotNum",
    "maxOutPw",
    "pvPw",
    "swEps",
    "tempUnit",
    "workModel",
})


def has_home_payload_evidence(props: dict[str, Any]) -> bool:
    """Return True when props carry Home/System-body-only fields."""
    return any(key in props for key in HOME_PAYLOAD_EVIDENCE_KEYS)


def payload_has_home_payload_evidence(
    payload: dict[str, Any],
    props: dict[str, Any] | None = None,
) -> bool:
    """Return True when merged or raw payload props identify a Home/System body."""
    if props is not None and has_home_payload_evidence(props):
        return True
    if isinstance(payload.get(PAYLOAD_SYSTEM), dict) and payload[PAYLOAD_SYSTEM]:
        return True
    for section in (PAYLOAD_PROPERTIES, PAYLOAD_HTTP_PROPERTIES):
        raw = payload.get(section) or {}
        if isinstance(raw, dict) and has_home_payload_evidence(raw):
            return True
    return False


def is_portable_payload(
    payload: dict[str, Any],
    props: dict[str, Any] | None = None,
) -> bool:
    """Return True for Explorer/Portable payloads without Home/System evidence."""
    if payload_has_home_payload_evidence(payload, props):
        return False
    for section in (PAYLOAD_DEVICE, PAYLOAD_DISCOVERY):
        meta = payload.get(section) or {}
        if (
            isinstance(meta, dict)
            and meta.get(PAYLOAD_DISCOVERY_SOURCE) == DISCOVERY_SOURCE_LEGACY_BIND_LIST
        ):
            return True
    return False
