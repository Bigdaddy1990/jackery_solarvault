"""Closed-day Recorder reconciliation and historical HTTP backfill mixin."""

import asyncio
from collections.abc import Mapping
import contextlib
import copy
from dataclasses import dataclass, field as dataclass_field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum
from functools import partial
import hashlib
import json
import logging
import math
import sys
import time
from typing import TYPE_CHECKING, Any, Final, TypedDict, Unpack, cast

from homeassistant.components.recorder.db_schema import (
    Statistics,
    StatisticsMeta,
    StatisticsShortTerm,
)
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.components.recorder.tasks import SynchronizeTask
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.recorder import get_instance, session_scope
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter, PowerConverter

from ..client import JackeryAuthError, JackeryError
from ..const import (
    APP_CHART_LABELS,
    APP_CHART_SERIES_Y,
    APP_CHART_SERIES_Y1,
    APP_CHART_SERIES_Y2,
    APP_CHART_STAT_METRICS,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_END_DATE_ALT,
    APP_REQUEST_META,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_STAT_TOTAL_CHARGE,
    APP_STAT_TOTAL_DISCHARGE,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
    APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
    APP_STAT_UNIT,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DOMAIN,
    FIELD_DEVICE_SN,
    FIELD_ID,
    FIELD_SYSTEM_ID,
    PAYLOAD_CT_METER,
    PAYLOAD_OBSERVED_DAY_STATISTICS,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
    PAYLOAD_VERIFIED_DAY_STATISTICS,
    SLOW_METRICS_INTERVAL_SEC,
)
from ..util import (
    STATISTICS_AGGREGATE_MATCH_TOLERANCE_KWH,
    StatisticRow,
    _explicit_day_curve_minutes,
    app_period_request_kwargs,
    chart_value_for_day,
    day_power_energy_points,
    day_power_series_key,
    historical_day_payload_from_sources,
    parse_utc_datetime,
    plan_statistic_day_reconcile,
    plan_statistic_month_reconcile,
    plan_statistic_partial_day_reconcile,
    request_date,
    safe_float,
    statistics_http_backfill_dates as statistics_http_backfill_dates,  # ruff: ignore[useless-import-alias] - compatibility export
    supplement_day_curve_samples,
    supplement_pv_day_curve,
    utc_now,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.components.recorder.models import StatisticData

    from ..coordinator import _HistoricalAppChartFetchOptions
    from .statistics_engine import _DayChartPointsOptions

_LOGGER = logging.getLogger(__name__)


def exception_debug_message(err: BaseException) -> str:
    """Return a useful debug message for exceptions with empty str(err)."""
    return f"{type(err).__name__}: {err or "(no message)"}"


class BackfillStatus(StrEnum):
    """Serializable lifecycle states for one statistics backfill bucket."""

    PENDING = "pending"
    RETRYABLE = "retryable"
    IMPORTED = "imported"


def _coordinator_recorder_attr[T](name: str, fallback: T) -> T:
    """Resolve a recorder helper through coordinator for test patching."""
    coordinator_mod = sys.modules.get(
        "custom_components.jackery_solarvault.coordinator"
    )
    return cast("T", getattr(coordinator_mod, name, fallback))


def _app_chart_stat_metrics() -> tuple[tuple[str, str, str, str], ...]:
    """Return active app chart metrics, allowing coordinator test overrides."""
    return _coordinator_recorder_attr("APP_CHART_STAT_METRICS", APP_CHART_STAT_METRICS)


class JackeryStatisticHistoryPendingError(HomeAssistantError):
    """Raised when recorder historical backfill is still running."""


_STATISTICS_HTTP_BACKFILL_WINDOW_DAYS = 365
_DAY_CURVE_RETRY_WINDOW_DAYS: Final = 90
_STATISTICS_HTTP_BACKFILL_INTERVAL_SEC = SLOW_METRICS_INTERVAL_SEC
_STATISTICS_HTTP_BACKFILL_RETRY_SEC = SLOW_METRICS_INTERVAL_SEC
_STATISTICS_HTTP_EMPTY_DEFERRALS = "empty_deferrals"
_STATISTICS_HTTP_RETRY_AFTER_EPOCH = "retry_after_epoch"
_STATISTICS_HTTP_VERIFIED_TOTALS = "verified_totals"
_STATISTICS_HTTP_VERIFIED_TOTALS_RULE = "complete_5_minute_day_v3_date_bound"
_STATISTICS_HTTP_OBSERVED_TOTALS = "observed_totals"
_STATISTICS_HTTP_OBSERVED_TOTALS_RULE = "observed_5_minute_day_v1_date_bound"
_OBSERVED_DAY_EXPECTED_SAMPLES: Final = 288
_STATISTICS_RECORDER_PENDING_RETRY_SECONDS: Final = 60
_RECORDER_HOUR_MINUTES: Final = 60
_HTTP_DAY_EMPTY_IMPORT_RULE = "month_chart_bucket_v2_incomplete_curve"
_HTTP_DAY_CURVE_IMPORT_RULE: Final = "day_curve_with_month_fallback_v1"
_STATISTICS_IMPORT_STATE_TOLERANCE = 1e-4
_SYSTEM_DAY_CURVE_FALLBACK: Final[
    dict[str, tuple[str, tuple[str, ...], tuple[tuple[str, str], ...]]]
] = {
    APP_SECTION_PV_STAT: (
        "async_get_pv_trends",
        (APP_CHART_SERIES_Y,),
        ((APP_STAT_TOTAL_SOLAR_ENERGY, APP_STAT_TOTAL_SOLAR_ENERGY),),
    ),
    APP_SECTION_BATTERY_STAT: (
        "async_get_battery_trends",
        (APP_CHART_SERIES_Y1, APP_CHART_SERIES_Y2),
        (
            (APP_STAT_TOTAL_TREND_CHARGE_ENERGY, APP_STAT_TOTAL_CHARGE),
            (APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY, APP_STAT_TOTAL_DISCHARGE),
        ),
    ),
}

_OWNER_DAY_REQUEUE_RULE: Final = "owner_20261008_v1"
_OWNER_DAY_REQUEUE_DEVICE_ID: Final = "573702884982521856"
_OWNER_DAY_REQUEUE_BUDGET: Final = 4
_OWNER_DAY_REQUEUE_DATES: Final = (
    date(2026, 7, 12),
    date(2026, 7, 13),
    date(2026, 7, 23),
    date(2026, 7, 24),
    date(2026, 7, 25),
    *(date(2026, 9, day) for day in range(12, 19)),
)

_POWER_SENSOR_BY_CHART_METRIC: Final[dict[str, str]] = {
    "pv_energy": "pv_power_total",
    "pv1_energy": "pv1_power",
    "pv2_energy": "pv2_power",
    "pv3_energy": "pv3_power",
    "pv4_energy": "pv4_power",
    "battery_charge_energy": "stack_in_power",
    "battery_discharge_energy": "stack_out_power",
    "device_ongrid_input_energy": "inverter_ac_input_power",
    "device_ongrid_output_energy": "inverter_ac_output_power",
    "eps_input_energy": "eps_in_power",
    "eps_output_energy": "eps_out_power",
}


def _backfill_period_is_closed(
    target_day: date,
    today: date,
    date_type: str = DATE_TYPE_DAY,
) -> bool:
    """Return True if period containing target_day is closed before today."""
    if date_type == DATE_TYPE_DAY:
        return target_day < today
    if date_type == DATE_TYPE_WEEK:
        week_start = target_day - timedelta(days=target_day.weekday())
        week_end = week_start + timedelta(days=6)
        return week_end < today
    if date_type == DATE_TYPE_MONTH:
        next_month = target_day.replace(day=28) + timedelta(days=4)
        month_start_next = next_month.replace(day=1)
        return month_start_next <= today
    if date_type == DATE_TYPE_YEAR:
        return target_day.year < today.year
    return False


def _normalize_backfill_status(
    value: object,
    *,
    closed: bool,
) -> BackfillStatus:
    """Map legacy/transient cache values onto the durable state contract."""
    try:
        status = BackfillStatus(str(value))
    except TypeError, ValueError:
        if value in {
            "auth_error",
            "deferred",
            "empty_ambiguous",
            "fetched",
            "recorder_error",
            "transport_error",
            "unavailable",
        }:
            status = BackfillStatus.RETRYABLE
        else:
            status = BackfillStatus.PENDING
    if not closed and status is BackfillStatus.IMPORTED:
        return BackfillStatus.RETRYABLE
    return status


def _is_system_busy_error(err: BaseException) -> bool:
    """Detect Jackery cloud 10426 system busy error."""
    text = str(err)
    return "10426" in text or "system busy" in text.lower()


def _rate_limit_retry_after_seconds(err: BaseException) -> float:
    """Return a bounded Retry-After delay exposed by common HTTP clients."""
    coordinator_helper = cast(
        "Callable[[BaseException], float] | None",
        _coordinator_recorder_attr("_rate_limit_retry_after_seconds", None),
    )
    if (
        coordinator_helper is not None
        and coordinator_helper is not _rate_limit_retry_after_seconds
    ):
        return float(coordinator_helper(err))
    value: object = getattr(err, "retry_after", None)
    response = getattr(err, "response", None)
    headers = getattr(response, "headers", None) or getattr(err, "headers", None)
    if value is None and isinstance(headers, Mapping):
        value = headers.get("Retry-After") or headers.get("retry-after")
    seconds: float | None
    if isinstance(value, timedelta):
        seconds = value.total_seconds()
    else:
        seconds = (
            safe_float(value)
            if isinstance(value, (float, str))
            else float(value)
            if isinstance(value, int) and not isinstance(value, bool)
            else None
        )
    if seconds is None and isinstance(value, str):
        with contextlib.suppress(ValueError, TypeError, OverflowError):
            retry_at = parsedate_to_datetime(value)
            seconds = (retry_at.astimezone(UTC) - datetime.now(UTC)).total_seconds()
    delay = SLOW_METRICS_INTERVAL_SEC if seconds is None else seconds
    return max(1.0, delay)


@dataclass(slots=True)
class _HttpDayBackfillCandidate:
    """One independently retryable historical source/day bucket."""

    priority: int
    attempted: int
    last_attempt: str
    target_day: date
    attempts: int
    device_id: str
    section_prefix: str
    payload: dict[str, Any]
    day_state: dict[str, Any]
    days_state: dict[str, Any]
    month_metric_revisions: dict[str, int] = dataclass_field(default_factory=dict)


class _VerifiedDayUpdates(dict[str, dict[str, Any]]):  # ruff: ignore[subclass-builtin]
    """Keep dict compatibility and capture-time metadata for buffered updates."""

    def __init__(self) -> None:
        super().__init__()
        self.baselines: dict[str, dict[str, Any]] = {}
        self.revisions: dict[tuple[str, str, str, str], int] = {}
        self.owners: dict[tuple[str, str], object] = {}
        self.observed: dict[str, dict[str, Any]] = {}
        self.observed_baselines: dict[tuple[str, str, str, str], object] = {}
        self.observed_revisions: dict[tuple[str, str, str, str], int] = {}
        self.observed_owners: dict[tuple[str, str], object] = {}

    def __bool__(self) -> bool:
        """A partial observation still needs publication without a certificate."""
        return bool(len(self) or self.observed)


class _HistoricalDaySource(dict[str, Any]):  # ruff: ignore[subclass-builtin]
    """Keep dict-based source guards while attaching request metadata."""

    revision: int
    stale_metrics: bool = False
    accepted_metrics: frozenset[str] = frozenset()
    source_owner: object = None

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        """Persist only payloads, never live ConfigEntry identities or receipts."""
        return copy.deepcopy(dict(self), memo)


class _HistoricalPeriodSource(_HistoricalDaySource):
    """Bind a period response to the request that actually fetched it."""

    request_key: tuple[str, str, str, str]
    day_revision: int


def _clean_source_dict(source: object) -> object:
    """Retain same-session receipts; storage snapshots strip runtime metadata."""
    if isinstance(source, _HistoricalDaySource):
        # Recorder can become ready without fetching this source again. A plain
        # dict here discarded the owner/revision required to safely resume.
        # deepcopy at the storage boundary deliberately writes payload only;
        # reloaded history must acquire a new receipt before it can be imported.
        return copy.copy(source)
    if isinstance(source, dict):
        return dict(source)
    return source


@dataclass(slots=True)
class _HttpDayBackfillProgress:
    """Mutable accounting for one bounded day-backfill pass."""

    target_days: list[date]
    force: bool
    window_days: int
    include_current_year: bool
    now_monotonic: float
    external_rows: int = 0
    gap_filled_rows: int = 0
    source_days: int = 0
    terminal_transitions: int = 0
    requests: int = 0
    pending_sources: int = 0
    actionable_sources: int = 0
    state_changed: bool = False
    verification_only: bool = False
    rate_limited: bool = False
    stopped: bool = False
    empty_sources: int = 0
    unmapped_sources: int = 0
    successful_devices: set[str] = dataclass_field(default_factory=set)
    month_sources: dict[tuple[str, str, str], dict[str, Any]] = dataclass_field(
        default_factory=dict
    )
    month_source_revisions: dict[tuple[str, str, str], int] = dataclass_field(
        default_factory=dict
    )
    verified_day_updates: _VerifiedDayUpdates = dataclass_field(
        default_factory=_VerifiedDayUpdates
    )


@dataclass(slots=True)
class _HttpPeriodBackfillCandidate:
    """One independently retryable closed source/period bucket."""

    priority: int
    attempted: int
    last_attempt: str
    period_start: date
    device_id: str
    section_prefix: str
    date_type: str
    payload: dict[str, Any]
    bucket_state: dict[str, Any]
    type_state: dict[str, Any]


@dataclass(slots=True)
class _HttpPeriodBackfillProgress:
    """Mutable accounting for one bounded period-backfill pass."""

    requests: int = 0
    imported_sources: int = 0
    terminal_transitions: int = 0
    pending_sources: int = 0
    actionable_sources: int = 0
    open_sources: int = 0
    state_changed: bool = False
    rate_limited: bool = False
    stopped: bool = False
    empty_sources: int = 0
    unmapped_sources: int = 0


class _VerifiedDayWindow(TypedDict):
    device_id: str
    target_day: date
    section_prefix: str
    week_start: date
    today: date


class _HttpDayBackfillOptions(TypedDict, total=False):
    force: bool
    window_days: int
    include_current_year: bool
    request_budget: int | None
    verification_only: bool


class _RecorderGuardOptions(TypedDict, total=False):
    recorder_guard: Callable[[], bool] | None


class _PowerRecorderOptions(_RecorderGuardOptions, total=False):
    recorder_state: dict[str, Any] | None
    recorder_metric: str | None


class _HistoricalDayImportOptions(TypedDict, total=False):
    reconciled_metrics: set[str]
    recorder_state: dict[str, Any]


if TYPE_CHECKING:
    from collections.abc import Awaitable
    from datetime import tzinfo

    from homeassistant.core import HomeAssistant

    from ..ingest import TransportSource
    from .api import JackeryApi


class JackeryRecorderReconcileMixin:
    """Mixin for closed-day Recorder reconciliation and historical HTTP backfills."""

    if TYPE_CHECKING:
        _historical_day_request_revision: int

        _last_statistics_http_backfill_monotonic: float

        _owner_day_requeue_lock: asyncio.Lock

        _period_backfill_hold_save_pending: set[tuple[str, str, str]]

        _stale_imported_power_hours: dict[str, dict[float, float]]

        _statistics_import_diagnostics: dict[str, Any]

        _statistics_rate_limit_until: float | None

        _statistics_recorder_lock: asyncio.Lock

        _statistics_startup_fetched: set[tuple[str, str, str]]

        _verified_day_metric_revisions: dict[tuple[str, str, str, str], int]

        _verified_day_published_revisions: dict[
            tuple[str, str, str, str], tuple[int, float]
        ]

        api: JackeryApi

        data: dict[str, dict[str, Any]]

        hass: HomeAssistant

        @staticmethod
        def _app_chart_period_meta(date_type: str) -> tuple[str, str] | None:
            raise NotImplementedError

        async def _async_ensure_statistics_backfill_state_loaded(self) -> None:
            raise NotImplementedError

        async def _async_fetch_historical_app_chart_source(
            self, **options: Unpack[_HistoricalAppChartFetchOptions]
        ) -> dict[str, Any]:
            raise NotImplementedError

        async def _async_save_statistics_backfill_state(self) -> None:
            raise NotImplementedError

        async def _async_slow_http_call[Result](
            self, call: Callable[[], Awaitable[Result]]
        ) -> Result:
            raise NotImplementedError

        def _day_chart_points_for_metric(
            self, **options: Unpack[_DayChartPointsOptions]
        ) -> list[Any]:
            raise NotImplementedError

        def _enabled_app_chart_date_types(self) -> set[str]:
            raise NotImplementedError

        def _historical_app_chart_request(
            self, options: _HistoricalAppChartFetchOptions, kwargs: dict[str, Any]
        ) -> Callable[[], Awaitable[dict[str, Any]]] | None:
            raise NotImplementedError

        async def _import_collected_repair_buckets(
            self,
            *,
            device_id: str,
            name_prefix: str,
            collected: dict[tuple[str, str, date], dict[str, Any]],
            period_meta_by_type: dict[str, tuple[str, str]],
            to_date: date,
        ) -> tuple[int, int, int, dict[str, str]]:
            raise NotImplementedError

        @staticmethod
        def _iter_calendar_months(start_date: date, end_date: date) -> list[date]:
            raise NotImplementedError

        @staticmethod
        def _iter_calendar_weeks(start_date: date, end_date: date) -> list[date]:
            raise NotImplementedError

        @staticmethod
        def _iter_calendar_years(start_date: date, end_date: date) -> list[int]:
            raise NotImplementedError

        def _local_now(self) -> datetime:
            raise NotImplementedError

        def _local_statistic_start(self, bucket_date: date | datetime) -> datetime:
            raise NotImplementedError

        def _local_timezone(self) -> tzinfo:
            raise NotImplementedError

        def _local_today(self) -> date:
            raise NotImplementedError

        def _pv_month_recovery_owner(self, device_id: str) -> tuple[object, ...] | None:
            raise NotImplementedError

        def _push_partial_update(  # ruff: ignore[too-many-arguments]
            self,
            new_data: dict[str, dict[str, Any]],
            *,
            source: TransportSource | None = None,
            observed_at: datetime | None = None,
            allow_during_shutdown: bool = False,
            battery_packs_complete: bool = False,
            acknowledge_unchanged: bool = False,
            request_id: str | None = None,
            on_commit: Callable[[], None] | None = None,
            mark_success: bool = True,
            replace_device_ids: frozenset[str] = frozenset(),
            remove_device_ids: frozenset[str] = frozenset(),
            record_source_payload: bool = True,
            weather_plan_rows: Mapping[str, list[Any]] | None = None,
            notify_listeners: bool = True,
        ) -> bool:
            raise NotImplementedError

        @classmethod
        def _smart_meter_accessory_device_id(cls, source: dict[str, Any]) -> str | None:
            raise NotImplementedError

        def _statistics_backfill_device_state(self, device_id: str) -> dict[str, Any]:
            raise NotImplementedError

    @staticmethod
    def _statistics_http_backfill_dates(
        today: date,
        *,
        window_days: int = _STATISTICS_HTTP_BACKFILL_WINDOW_DAYS,
        include_current_year: bool = False,
    ) -> list[date]:
        """Return completed local days covered by automatic HTTP backfill."""
        return statistics_http_backfill_dates(
            today,
            window_days=window_days,
            include_current_year=include_current_year,
        )

    def _system_id_from_payload(
        self,
        device_id: str,
        payload: dict[str, Any],
    ) -> str | None:
        """Resolve the system id needed by system trend endpoints."""
        device_index = getattr(self, "_device_index", {})
        for source in (
            payload.get(PAYLOAD_SYSTEM),
            payload.get(PAYLOAD_SYSTEM_META),
            device_index.get(device_id),
        ):
            if not isinstance(source, dict):
                continue
            sys_id = source.get(FIELD_ID) or source.get(FIELD_SYSTEM_ID)
            if sys_id is not None:
                return str(sys_id)
        return None

    @staticmethod
    def _historical_day_payload_from_sources(
        section_sources: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Convert section-source dicts into the normal day payload shape."""
        return historical_day_payload_from_sources(section_sources)

    def _historical_day_source_prefixes(
        self,
        device_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, ...]:
        """Return the available historical sources in a stable queue order."""
        prefixes = [
            APP_SECTION_BATTERY_STAT,
            APP_SECTION_HOME_STAT,
            APP_SECTION_CT_STAT,
            APP_SECTION_EPS_STAT,
        ]
        if self._system_id_from_payload(device_id, payload) is not None:
            prefixes.extend((APP_SECTION_PV_STAT, APP_SECTION_HOME_TRENDS))
        return tuple(prefixes)

    async def _async_fetch_historical_day_chart_source(  # ruff: ignore[too-many-statements]
        self,
        *,
        device_id: str,
        payload: dict[str, Any],
        target_day: date,
        section_prefix: str,
    ) -> tuple[str, dict[str, Any]]:
        """Fetch and gate exactly one historical source/day pair."""
        request_kwargs = app_period_request_kwargs(DATE_TYPE_DAY, today=target_day)
        system_id = self._system_id_from_payload(device_id, payload)
        device_index = getattr(self, "_device_index", {})
        index = device_index.get(device_id) or {}
        request_options: _HistoricalAppChartFetchOptions = {
            "device_id": device_id,
            "system_id": system_id,
            "ct_device_id": (
                self._smart_meter_accessory_device_id(payload)
                or self._smart_meter_accessory_device_id(index)
            ),
            "section_prefix": section_prefix,
            "date_type": DATE_TYPE_DAY,
            "period_start": target_day,
        }
        request_factory = self._historical_app_chart_request(
            request_options,
            request_kwargs,
        )
        if request_factory is None:
            return "unsupported", {}
        source_owner = self._observed_day_source_owner(
            device_id, section_prefix, payload
        )
        if (
            source_owner is not None
            and section_prefix == APP_SECTION_CT_STAT
            and request_options.get("ct_device_id") != source_owner[2]
        ):
            return "stale_owner", {}

        revision = 0

        async def fetch_with_system_fallback() -> object:
            nonlocal revision
            revision = getattr(self, "_historical_day_request_revision", 0) + 1
            self._historical_day_request_revision = revision
            fetched = await request_factory()
            device_has_points = isinstance(fetched, dict) and any(
                isinstance(values, list)
                and any(safe_float(value) is not None for value in values)
                for key, values in fetched.items()
                if key == APP_CHART_SERIES_Y
                or (key.startswith(APP_CHART_SERIES_Y) and key[1:].isdigit())
            )
            aggregate = (
                fetched.get(APP_CHART_SERIES_Y) if isinstance(fetched, dict) else None
            )
            pv_aggregate_missing = section_prefix == APP_SECTION_PV_STAT and not (
                isinstance(aggregate, list)
                and any(safe_float(value) is not None for value in aggregate)
            )
            if device_has_points and not pv_aggregate_missing:
                return fetched
            try:
                fallback = await self._async_system_day_curve_fallback(
                    section_prefix=section_prefix,
                    system_id=system_id,
                    target_day=target_day,
                )
            except (JackeryError, TimeoutError, HomeAssistantError) as err:
                if not device_has_points:
                    raise
                if _is_system_busy_error(err):
                    self._set_statistics_rate_limit(err)
                _LOGGER.debug(
                    "Jackery supplementary system PV curve failed for %s on %s; "
                    "device channels remain usable: %s",
                    device_id,
                    target_day.isoformat(),
                    exception_debug_message(err),
                )
                return fetched
            if fallback and isinstance(fetched, dict):
                if section_prefix == APP_SECTION_PV_STAT:
                    curve = fallback.get(APP_CHART_SERIES_Y)
                    if not isinstance(curve, list) or not any(
                        safe_float(value) is not None for value in curve
                    ):
                        return fetched
                    if not device_has_points:
                        # An empty device answer has no five-minute axis to
                        # align. Keep the dated system curve only when units,
                        # dates and any shared physical owner IDs agree.
                        device_unit = (
                            str(fetched.get(APP_STAT_UNIT) or "W").strip().lower()
                        )
                        system_unit = (
                            str(fallback.get(APP_STAT_UNIT) or "W").strip().lower()
                        )
                        device_request = fetched.get(APP_REQUEST_META)
                        system_request = fallback.get(APP_REQUEST_META)
                        same_request = not isinstance(device_request, dict) or (
                            isinstance(system_request, dict)
                            and all(
                                device_request.get(field) == system_request.get(field)
                                for field in (
                                    APP_REQUEST_DATE_TYPE,
                                    APP_REQUEST_BEGIN_DATE,
                                    APP_REQUEST_END_DATE,
                                )
                            )
                        )
                        same_owner = all(
                            key not in fetched
                            or key not in fallback
                            or fetched[key] == fallback[key]
                            for key in ("deviceId", "deviceSn", "systemId", "systemSn")
                        )
                        if device_unit == system_unit and same_request and same_owner:
                            # Retain device scalars, but never attach null,
                            # unaligned device vectors to the system curve.
                            metadata = {
                                key: value
                                for key, value in fetched.items()
                                if key
                                not in {
                                    APP_CHART_LABELS,
                                    APP_REQUEST_META,
                                    APP_STAT_UNIT,
                                }
                                and key != APP_CHART_SERIES_Y
                                and not (key.startswith("y") and key[1:].isdigit())
                            }
                            fetched = {**fallback, **metadata}
                    return supplement_pv_day_curve(fetched, fallback)
                return {**fetched, **fallback}
            return fallback or fetched

        error_status: str | None = None
        result: object = {}
        try:
            result = await self._async_slow_http_call(fetch_with_system_fallback)
        except JackeryAuthError as err:
            # Supplementary historical requests are not the authentication
            # authority. The primary property poll owns reauthentication.
            _LOGGER.debug(
                "Jackery historical %s fetch for %s on %s was auth-rejected; "
                "live polling remains the auth authority: %s",
                section_prefix,
                device_id,
                target_day.isoformat(),
                exception_debug_message(err),
            )
            error_status = "auth_error"
        except (TimeoutError, HomeAssistantError, JackeryError) as err:
            _LOGGER.debug(
                "Jackery historical %s fetch for %s on %s failed: %s",
                section_prefix,
                device_id,
                target_day.isoformat(),
                exception_debug_message(err),
            )
            error_status = (
                "rate_limited" if _is_system_busy_error(err) else "transport_error"
            )
            if error_status == "rate_limited":
                self._set_statistics_rate_limit(err)
        except Exception as err:  # ruff: ignore[blind-except]  # Dynamic endpoint failures remain retryable per source.
            _LOGGER.debug(
                "Jackery historical %s fetch for %s on %s failed: %s",
                section_prefix,
                device_id,
                target_day.isoformat(),
                exception_debug_message(err),
            )
            error_status = "transport_error"

        observed = isinstance(result, dict) and (
            any(
                (
                    key == APP_CHART_SERIES_Y
                    or (key.startswith(APP_CHART_SERIES_Y) and key[1:].isdigit())
                )
                and isinstance(values, list)
                and any(safe_float(sample) is not None for sample in values)
                for key, values in result.items()
            )
            or any(
                source_section == section_prefix
                and safe_float(result.get(metric)) is not None
                for source_section, metric, _key, _label in _app_chart_stat_metrics()
            )
        )
        # Unit, currency, date labels and request metadata cannot turn an
        # otherwise empty or mismatched day response into a measured source.
        if error_status is None and isinstance(result, dict) and observed:
            if (
                source_owner is not None
                and source_owner
                != self._observed_day_source_owner(device_id, section_prefix)
            ):
                return "stale_owner", {}
            source = _HistoricalDaySource(result)
            source.revision = revision
            source.source_owner = source_owner
            return "fetched", source

        return error_status or "empty_ambiguous", {}

    async def _async_system_day_curve_fallback(
        self,
        *,
        section_prefix: str,
        system_id: str | None,
        target_day: date,
    ) -> dict[str, Any]:
        """Return the system 5-minute curve for a day in device-section shape."""
        spec = _SYSTEM_DAY_CURVE_FALLBACK.get(section_prefix)
        if spec is None or system_id is None:
            return {}
        method_name, series_keys, totals = spec
        day = target_day.isoformat()
        source = await getattr(self.api, method_name)(
            system_id,
            date_type=DATE_TYPE_DAY,
            begin_date=day,
            end_date=day,
        )
        if not isinstance(source, dict) or not any(
            isinstance(source.get(key), list) and source[key] for key in series_keys
        ):
            return {}
        copied_keys = (APP_CHART_LABELS, *series_keys)
        converted: dict[str, Any] = {
            key: source[key] for key in copied_keys if key in source
        }
        # ``sys/pv/statics`` omits the unit; its samples are watts like the
        # device curve (identical values on overlapping days).
        converted[APP_STAT_UNIT] = source.get(APP_STAT_UNIT) or "W"
        for system_key, device_key in totals:
            if system_key in source:
                converted[device_key] = source[system_key]
        # The explicit day window dates every bucket; without it the converter
        # would place historical samples on today.
        converted[APP_REQUEST_META] = {
            APP_REQUEST_DATE_TYPE: DATE_TYPE_DAY,
            APP_REQUEST_BEGIN_DATE: day,
            APP_REQUEST_END_DATE: day,
        }
        return converted

    def _owned_native_statistic(self, statistic_id: str, keys: tuple[str, ...]) -> bool:
        """Require a declared native sensor owned by this config entry."""
        registered = er.async_get(self.hass).async_get(statistic_id)
        entry = getattr(self, "entry", None)
        if (
            registered is None
            or entry is None
            or not statistic_id.startswith("sensor.")
            or registered.platform != DOMAIN
            or registered.config_entry_id != entry.entry_id
        ):
            return False
        return any(registered.unique_id.endswith(f"_{key}") for key in keys)

    def _owned_energy_statistic(self, statistic_id: str) -> bool:
        """Require a declared native energy sensor owned by this config entry."""
        from ..descriptions import STAT_DESCRIPTIONS  # ruff: ignore[import-outside-top-level]

        return self._owned_native_statistic(
            statistic_id,
            tuple(
                description.key
                for description in STAT_DESCRIPTIONS
                if description.reset_period is not None
                and description.native_unit_of_measurement
                == UnitOfEnergy.KILO_WATT_HOUR
            ),
        )

    def _period_reset_ts(self, hour_ts: float, reset_period: str) -> float:
        """Return the local start of a native counter's reset period."""
        local = datetime.fromtimestamp(hour_ts, self._local_timezone()).date()
        if reset_period == DATE_TYPE_WEEK:
            local -= timedelta(days=local.weekday())
        elif reset_period == DATE_TYPE_MONTH:
            local = local.replace(day=1)
        elif reset_period == DATE_TYPE_YEAR:
            local = local.replace(month=1, day=1)
        return self._local_statistic_start(local).timestamp()

    async def _async_wait_native_recorder_commit(self) -> None:
        """Wait for queued and already executing Recorder transactions."""
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            recorder = get_instance(self.hass)
            async with asyncio.timeout(10.0):
                await recorder.async_block_till_done()
                committed = self.hass.loop.create_future()
                recorder.queue_task(SynchronizeTask(committed))
                await asyncio.shield(committed)
        except Exception as err:  # ruff: ignore[blind-except]
            _LOGGER.debug(
                "SynchronizeTask queue or block_till_done failed: %s",
                err,
            )

    def _load_native_energy_window(
        self, statistic_id: str, first: float, last: float
    ) -> tuple[dict[float, StatisticRow], bool, dict[float, float], str | None]:
        """Read native Recorder history under the caller-owned reconciliation lock."""
        try:
            recorder = get_instance(self.hass)
        except KeyError as err:
            msg = f"Waiting for Recorder instance for {statistic_id}"
            raise JackeryStatisticHistoryPendingError(msg) from err
        with _coordinator_recorder_attr("session_scope", session_scope)(
            session=recorder.get_session()
        ) as session:
            meta = (
                session
                .query(StatisticsMeta)
                .filter(StatisticsMeta.statistic_id == statistic_id)
                .first()
            )
            if meta is None:
                return {}, True, {}, None
            if meta.unit_of_measurement != UnitOfEnergy.KILO_WATT_HOUR:
                msg = (
                    f"Cannot reconcile {statistic_id}: expected kWh, "
                    f"found {meta.unit_of_measurement}"
                )
                raise JackeryStatisticHistoryPendingError(msg)
            if (
                meta.source != "recorder"
                or not meta.has_sum
                or meta.has_mean
                or meta.mean_type not in {None, StatisticMeanType.NONE}
                or meta.unit_class not in {None, EnergyConverter.UNIT_CLASS}
            ):
                msg = f"Cannot reconcile {statistic_id}: incompatible native metadata"
                raise JackeryStatisticHistoryPendingError(msg)
            base = session.query(
                Statistics.start_ts,
                Statistics.sum,
                Statistics.state,
                Statistics.last_reset_ts,
            ).filter(Statistics.metadata_id == meta.id, Statistics.sum.is_not(None))
            found = [
                *base.filter(
                    Statistics.start_ts >= first, Statistics.start_ts < last
                ).all(),
                *base
                .filter(Statistics.start_ts < first)
                .order_by(Statistics.start_ts.desc())
                .limit(1)
                .all(),
                *base
                .filter(Statistics.start_ts >= last)
                .order_by(Statistics.start_ts)
                .limit(1)
                .all(),
            ]
            empty = (
                not found
                and session
                .query(Statistics.id)
                .filter(Statistics.metadata_id == meta.id)
                .first()
                is None
                and session
                .query(StatisticsShortTerm.id)
                .filter(StatisticsShortTerm.metadata_id == meta.id)
                .first()
                is None
            )
            short = (
                session
                .query(StatisticsShortTerm.start_ts, StatisticsShortTerm.sum)
                .filter(
                    StatisticsShortTerm.metadata_id == meta.id,
                    StatisticsShortTerm.start_ts >= last,
                    StatisticsShortTerm.sum.is_not(None),
                )
                .order_by(StatisticsShortTerm.start_ts)
                .limit(1)
                .all()
            )
            return (
                {
                    float(start): StatisticRow(float(total), state, reset)
                    for start, total, state, reset in found
                },
                empty,
                {float(start): float(total) for start, total in short},
                meta.name,
            )

    @staticmethod
    def _verify_committed_native_values(
        statistic_id: str,
        expected: dict[float, float],
        committed: dict[float, float],
        label: str,
    ) -> None:
        """Require every expected native sum to be present after the commit barrier."""
        for start, value in expected.items():
            if start not in committed or not math.isclose(
                committed[start],
                value,
                rel_tol=0.0,
                abs_tol=_STATISTICS_IMPORT_STATE_TOLERANCE,
            ):
                msg = f"Waiting for committed {label} of {statistic_id}"
                raise JackeryStatisticHistoryPendingError(msg)

    async def _async_reconcile_statistic_day(  # ruff: ignore[too-many-arguments, too-many-branches]  # ruff: ignore[too-many-statements]
        self,
        statistic_id: str,
        hours: list[float],
        hourly_energy: dict[float, float],
        *,
        reset_period: str = DATE_TYPE_DAY,
        aggregate_only: bool = False,
        allow_partial_observed: bool = False,
        recorder_guard: Callable[[], bool] | None = None,
        recorder_state: dict[str, Any] | None = None,
    ) -> int:
        """Reconcile closed, owned native energy history and verify committed writes."""
        if not self._owned_energy_statistic(statistic_id):
            msg = f"Cannot reconcile {statistic_id}: not an owned native energy sensor"
            raise JackeryStatisticHistoryPendingError(msg)
        first, last = hours[0], hours[-1] + 3600
        if self._local_now().timestamp() < last + 2 * 3600:
            msg = f"Waiting for closed Recorder window of {statistic_id}"
            raise JackeryStatisticHistoryPendingError(msg)
        try:
            recorder = get_instance(self.hass)
        except KeyError as err:
            msg = f"Waiting for Recorder instance for {statistic_id}"
            raise JackeryStatisticHistoryPendingError(msg) from err

        async with self._statistics_recorder_lock:
            await self._async_wait_native_recorder_commit()
            (
                existing,
                initialize_empty,
                short,
                metadata_name,
            ) = await recorder.async_add_executor_job(
                partial(self._load_native_energy_window, statistic_id, first, last)
            )
            if not self._owned_energy_statistic(statistic_id):
                msg = (
                    f"Cannot reconcile {statistic_id}: "
                    "no longer an owned native energy sensor"
                )
                raise JackeryStatisticHistoryPendingError(msg)
            if not existing and not initialize_empty:
                msg = f"Waiting for hourly history of {statistic_id}"
                raise JackeryStatisticHistoryPendingError(msg)
            if not aggregate_only and (
                allow_partial_observed
                or any(
                    hour not in hourly_energy
                    and (hour not in existing or hour - 3600 not in existing)
                    for hour in hours
                )
            ):
                if not allow_partial_observed:
                    msg = f"Waiting for missing curve hours of {statistic_id}"
                    raise JackeryStatisticHistoryPendingError(msg)
                # A partial PV frame can safely add *measured* hours only
                # when an adjacent native sum anchors them. It may not move
                # any later HA sum or certify the remaining empty hours.
                plan = plan_statistic_partial_day_reconcile(
                    hours,
                    hourly_energy,
                    existing,
                    lambda hour: self._period_reset_ts(hour, reset_period),
                )
                if not plan.rows:
                    msg = f"Waiting for anchored curve hours of {statistic_id}"
                    raise JackeryStatisticHistoryPendingError(msg)
            else:
                plan = (
                    plan_statistic_month_reconcile
                    if aggregate_only
                    else plan_statistic_day_reconcile
                )(
                    hours,
                    hourly_energy,
                    existing,
                    lambda hour: self._period_reset_ts(hour, reset_period),
                    initialize_empty=initialize_empty,
                )
            if aggregate_only and not plan.rows:
                before = [start for start in existing if start < first]
                recorded_hours = [hour for hour in hours if hour in existing]
                # The aggregate planner accepts an already matching bucket even
                # with trailing gaps. Do not demand an invented final-hour row,
                # or reject a residual that the planner already treats as equal.
                if (
                    not before
                    or not recorded_hours
                    or not math.isclose(
                        existing[max(recorded_hours)].sum - existing[max(before)].sum,
                        sum(hourly_energy.values()),
                        rel_tol=0.0,
                        abs_tol=STATISTICS_AGGREGATE_MATCH_TOLERANCE_KWH,
                    )
                ):
                    msg = f"Waiting for unambiguous aggregate history of {statistic_id}"
                    raise JackeryStatisticHistoryPendingError(msg)
            if recorder_guard is not None and not recorder_guard():
                msg = f"Newer source available for {statistic_id}"
                raise JackeryStatisticHistoryPendingError(msg)
            if recorder_state is not None and (
                plan.rows or plan.shift_start is not None
            ):
                await self._async_save_statistics_backfill_state()
                if recorder_guard is not None and not recorder_guard():
                    msg = f"Newer source available for {statistic_id}"
                    raise JackeryStatisticHistoryPendingError(msg)
            if plan.rows:
                _coordinator_recorder_attr(
                    "async_import_statistics", async_import_statistics
                )(
                    self.hass,
                    {
                        "mean_type": StatisticMeanType.NONE,
                        "has_sum": True,
                        "name": metadata_name,
                        "source": "recorder",
                        "statistic_id": statistic_id,
                        "unit_class": EnergyConverter.UNIT_CLASS,
                        "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
                    },
                    cast("list[StatisticData]", plan.rows),
                )
            if plan.shift_start is not None:
                recorder.async_adjust_statistics(
                    statistic_id,
                    datetime.fromtimestamp(plan.shift_start, UTC),
                    plan.shift,
                    UnitOfEnergy.KILO_WATT_HOUR,
                )
            await self._async_wait_native_recorder_commit()
            committed, _, committed_short, _ = await recorder.async_add_executor_job(
                partial(self._load_native_energy_window, statistic_id, first, last)
            )
            committed_sums = {start: row.sum for start, row in committed.items()}
            self._verify_committed_native_values(
                statistic_id,
                {row["start"].timestamp(): row["sum"] for row in plan.rows},
                committed_sums,
                "reconciliation",
            )
            if plan.shift_start is not None:
                self._verify_committed_native_values(
                    statistic_id,
                    {
                        start: previous.sum + plan.shift
                        for start, previous in existing.items()
                        if start >= plan.shift_start
                    },
                    committed_sums,
                    "sum adjustment",
                )
                self._verify_committed_native_values(
                    statistic_id,
                    {
                        start: previous_sum + plan.shift
                        for start, previous_sum in short.items()
                    },
                    committed_short,
                    "short-term adjustment",
                )
            if recorder_guard is not None and not recorder_guard():
                msg = f"Newer source available for {statistic_id}"
                raise JackeryStatisticHistoryPendingError(msg)
            return len(plan.rows)

    def _validate_historical_day_axes(
        self, section_sources: dict[str, dict[str, Any]]
    ) -> None:
        """Require dated offset-aware axes on daylight-saving transition days."""
        for source in section_sources.values():
            day = request_date(
                source, APP_REQUEST_BEGIN_DATE, APP_REQUEST_BEGIN_DATE_ALT
            )
            if day is None:
                continue
            duration = (
                self._local_statistic_start(day + timedelta(days=1)).timestamp()
                - self._local_statistic_start(day).timestamp()
            )
            if duration == 24 * 3600:
                continue
            labels = source.get(APP_CHART_LABELS)
            explicit_axis = (
                bool(labels)
                and isinstance(labels, list)
                and all(
                    isinstance(label, str)
                    and (parsed := dt_util.parse_datetime(label)) is not None
                    and parsed.utcoffset() is not None
                    for label in labels
                )
            )
            if not explicit_axis:
                msg = "Ambiguous app bucket axis on a daylight-saving transition day"
                raise JackeryStatisticHistoryPendingError(msg)

    async def _async_import_historical_day_chart_statistics_for_device(  # ruff: ignore[too-many-arguments, too-many-locals]  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
        self,
        *,
        device_id: str,
        payload: dict[str, Any],
        section_sources: dict[str, dict[str, Any]],
        target_day: date | None = None,
        reconciled_metrics: set[str] | None = None,
        recorder_state: dict[str, Any] | None = None,
        power_only: bool = False,
    ) -> tuple[bool | None, int]:
        """Fill the energy sensors' missing hours of one closed day.

        Only hours that ended at least two hours ago are used, so the
        recorder's own compile of the previous hour is never raced.
        """
        self._validate_historical_day_axes(section_sources)
        # The coordinator already receives sparse day curves continuously.
        # Match their original five-minute timestamps against this historical
        # HTTP response before any Recorder logic; do not request an extra
        # cloud frame or discard cached local observations.
        cached_sections = {
            name: section
            for name, section in payload.items()
            if isinstance(section, dict)
        }
        historical_payload = self._historical_day_payload_from_sources(section_sources)
        if not historical_payload:
            return None, 0
        now = self._local_now()
        newest_hour = now.timestamp() - 2 * 3600
        imported_rows = 0
        handled = False
        for section_prefix, stat_key, metric_key, _label in _app_chart_stat_metrics():  # ruff: ignore[too-many-nested-blocks]
            # An opt-in live estimate must never become native house history.
            # Historical imports require this metric's own fetched source.
            source = section_sources.get(section_prefix)
            if not isinstance(source, dict):
                continue
            fetched = self._historical_day_payload_from_sources({
                section_prefix: source
            })
            historical_payload = {**cached_sections, **fetched}
            for key, cloud_section in fetched.items():
                local_section = cached_sections.get(key)
                if isinstance(local_section, dict):
                    historical_payload[key] = supplement_day_curve_samples(
                        cloud_section, local_section
                    )
            points = (
                []
                if power_only
                else self._day_chart_points_for_metric(
                    device_id=device_id,
                    payload=historical_payload,
                    section_prefix=section_prefix,
                    stat_key=stat_key,
                    metric_key=metric_key,
                    bucket_minutes=60,
                    now=now,
                    use_local_day_guard=False,
                    require_complete_day=True,
                    target_day=target_day,
                )
            )
            hourly_energy: dict[float, float] = {}
            for point in points:
                value = safe_float(point.value)
                if value is not None and point.start_date.date() < now.date():
                    start = self._local_statistic_start(point.start_date)
                    hourly_energy[start.timestamp()] = value
            if not hourly_energy:
                # A cloud or local curve may contain only some genuine
                # five-minute samples. Import those individual observations;
                # neither a complete hour nor a complete day is required.
                partial_samples = self._day_chart_points_for_metric(
                    device_id=device_id,
                    payload=historical_payload,
                    section_prefix=section_prefix,
                    stat_key=stat_key,
                    metric_key=metric_key,
                    bucket_minutes=5,
                    now=now,
                    use_local_day_guard=False,
                    use_scalar_total=False,
                    target_day=target_day,
                )
                if not partial_samples:
                    continue
                day = partial_samples[0].start_date.date()
                day_start = self._local_statistic_start(day).timestamp()
                day_end = self._local_statistic_start(
                    day + timedelta(days=1)
                ).timestamp()
                # Do not reinterpret a DST fold's duplicate wall-clock hour
                # without an offset-aware time axis.
                if (
                    day >= now.date()
                    or day_end > newest_hour
                    or day_end - day_start != 86400  # ruff: ignore[magic-value-comparison]
                ):
                    continue
                source = section_sources.get(section_prefix)
                if not isinstance(source, _HistoricalDaySource):
                    continue
                if source.source_owner is None:
                    if recorder_state is not None:
                        recorder_state["refresh_source_required"] = True
                    raise JackeryStatisticHistoryPendingError(  # ruff: ignore[raise-vanilla-args]
                        "Waiting for an owner-bound five-minute source"
                    )
                recorder_guard = partial(
                    self._partial_day_power_source_is_current,
                    (device_id, day.isoformat(), section_prefix, stat_key),
                    source,
                )
                # Means use the *observed* sample count; twelve invented
                # zeroes may never dilute a sparse hourly W observation.
                if str(source.get(APP_STAT_UNIT) or "W").strip().lower() == "w":
                    try:
                        power_rows = await self._async_import_day_power_for_metric(
                            device_id=device_id,
                            payload=historical_payload,
                            section_prefix=section_prefix,
                            stat_key=stat_key,
                            metric_key=metric_key,
                            bucket_minutes=5,
                            now=now,
                            target_day=target_day,
                            recorder_guard=recorder_guard,
                            recorder_state=recorder_state,
                        )
                        if power_rows is not None:
                            imported_rows += power_rows
                    except JackeryStatisticHistoryPendingError:
                        if recorder_state is not None:
                            pending = set(
                                recorder_state.get("pending_reconcile_metrics") or ()
                            )
                            pending.add(stat_key)
                            recorder_state["pending_reconcile_metrics"] = sorted(
                                pending
                            )
                if not power_only:
                    observed_hours: dict[float, float] = {}
                    for sample in partial_samples:
                        value = safe_float(sample.value)
                        if value is None or value < 0:
                            continue
                        stamp = self._local_statistic_start(
                            sample.start_date.replace(minute=0, second=0, microsecond=0)
                        ).timestamp()
                        observed_hours[stamp] = observed_hours.get(stamp, 0.0) + value
                    observed_hours = {
                        hour: round(value, 5) for hour, value in observed_hours.items()
                    }
                    hours = [
                        float(hour)
                        for hour in range(int(day_start), int(day_end), 3600)
                    ]
                    for statistic_id, reset_period in self._energy_statistic_targets(
                        device_id, section_prefix, stat_key
                    ):
                        if reset_period != DATE_TYPE_DAY or not observed_hours:
                            continue
                        try:
                            imported_rows += await self._async_reconcile_statistic_day(
                                statistic_id,
                                hours,
                                observed_hours,
                                recorder_guard=recorder_guard,
                                recorder_state=recorder_state,
                                allow_partial_observed=True,
                            )
                        except JackeryStatisticHistoryPendingError:
                            # One unanchored Recorder metric must not prevent
                            # other energy paths from importing their samples.
                            if recorder_state is not None:
                                pending = set(
                                    recorder_state.get("pending_reconcile_metrics")
                                    or ()
                                )
                                pending.add(stat_key)
                                recorder_state["pending_reconcile_metrics"] = sorted(
                                    pending
                                )
                # A partial curve remains retryable; it is never a complete
                # daily certificate or a fabricated monthly-end hour.
                continue
            day = datetime.fromtimestamp(
                min(hourly_energy), self._local_timezone()
            ).date()
            day_start = self._local_statistic_start(day).timestamp()
            day_end = self._local_statistic_start(day + timedelta(days=1)).timestamp()
            if day_end > newest_hour:
                continue
            source = section_sources.get(section_prefix)
            recorder_guard = partial(
                self._verified_day_source_is_current,
                (device_id, day.isoformat(), section_prefix, stat_key),
                source,
            )
            if not recorder_guard():
                if recorder_state is not None:
                    recorder_state["refresh_source_required"] = True
                raise JackeryStatisticHistoryPendingError(  # ruff: ignore[raise-vanilla-args]
                    "Waiting for a current owner-bound historical energy curve"
                )
            hours = [float(hour) for hour in range(int(day_start), int(day_end), 3600)]
            energy_targets = [
                statistic_id
                for statistic_id, reset_period in self._energy_statistic_targets(
                    device_id, section_prefix, stat_key
                )
                if reset_period == DATE_TYPE_DAY
            ]
            for statistic_id in energy_targets:
                # Week/month/year sensors carry a state across days: after an
                # outage their first recorded hour already holds the missing
                # day, so only daily sensors are rewritten from day curves.
                handled = True
                guard_options: _RecorderGuardOptions = {}
                if recorder_guard is not None:
                    guard_options["recorder_guard"] = recorder_guard
                imported_rows += await self._async_reconcile_statistic_day(
                    statistic_id,
                    hours,
                    hourly_energy,
                    **guard_options,
                )
            power_rows = await self._async_import_day_power_for_metric(
                device_id=device_id,
                payload=historical_payload,
                section_prefix=section_prefix,
                stat_key=stat_key,
                metric_key=metric_key,
                bucket_minutes=60,
                now=now,
                target_day=target_day,
                recorder_guard=recorder_guard,
                recorder_state=recorder_state,
            )
            if power_rows is not None:
                handled = True
                imported_rows += power_rows
            if reconciled_metrics is not None:
                power_key = _POWER_SENSOR_BY_CHART_METRIC.get(metric_key)
                power_target = power_key and er.async_get(
                    self.hass
                ).async_get_entity_id("sensor", DOMAIN, f"{device_id}_{power_key}")
                if (energy_targets or power_target) and (
                    not power_target or power_rows is not None
                ):
                    reconciled_metrics.add(stat_key)
        return (True if handled else None), imported_rows

    def _load_native_power_window(
        self, statistic_id: str, first: float, last: float
    ) -> tuple[bool, dict[float, float], str | None]:
        """Read native Recorder history under the caller-owned reconciliation lock."""
        try:
            recorder = get_instance(self.hass)
        except KeyError as err:
            msg = f"Waiting for Recorder instance for {statistic_id}"
            raise JackeryStatisticHistoryPendingError(msg) from err
        with _coordinator_recorder_attr("session_scope", session_scope)(
            session=recorder.get_session()
        ) as session:
            meta = (
                session
                .query(StatisticsMeta)
                .filter(StatisticsMeta.statistic_id == statistic_id)
                .first()
            )
            if meta is None or (
                (
                    meta.source,
                    meta.unit_of_measurement,
                    meta.mean_type,
                    bool(meta.has_sum),
                )
                != ("recorder", UnitOfPower.WATT, StatisticMeanType.ARITHMETIC, False)
                or meta.unit_class not in {None, PowerConverter.UNIT_CLASS}
            ):
                return False, {}, None
            present = (
                session
                .query(Statistics.start_ts, Statistics.mean)
                .filter(
                    Statistics.metadata_id == meta.id,
                    Statistics.mean.is_not(None),
                    Statistics.start_ts >= first,
                    Statistics.start_ts < last,
                )
                .all()
            )
            return (
                True,
                {float(start): float(mean) for start, mean in present},
                meta.name,
            )

    async def _async_fill_power_statistic_day(  # ruff: ignore[too-many-branches, too-many-locals, too-many-statements]
        self,
        statistic_id: str,
        hourly_mean_w: dict[float, float],
        *,
        recorder_guard: Callable[[], bool] | None = None,
        recorder_state: dict[str, Any] | None = None,
        recorder_metric: str | None = None,
    ) -> int | None:
        """Fill closed native W means while retaining every recorded hour."""
        if not hourly_mean_w or not self._owned_native_statistic(
            statistic_id,
            tuple(_POWER_SENSOR_BY_CHART_METRIC.values()),
        ):
            return None
        first, last = min(hourly_mean_w), max(hourly_mean_w) + 3600
        if self._local_now().timestamp() < last + 2 * 3600:
            return None
        try:
            recorder = get_instance(self.hass)
        except KeyError as err:
            msg = f"Waiting for Recorder instance for {statistic_id}"
            raise JackeryStatisticHistoryPendingError(msg) from err

        async with self._statistics_recorder_lock:
            await self._async_wait_native_recorder_commit()
            valid, present, name = await recorder.async_add_executor_job(
                partial(self._load_native_power_window, statistic_id, first, last)
            )
            if not valid or not self._owned_native_statistic(
                statistic_id,
                tuple(_POWER_SENSOR_BY_CHART_METRIC.values()),
            ):
                return None
            if recorder_guard is not None and not recorder_guard():
                msg = f"Newer source available for {statistic_id}"
                raise JackeryStatisticHistoryPendingError(msg)
            stale_by_stat = getattr(self, "_stale_imported_power_hours", None)
            if stale_by_stat is None:
                stale_by_stat = self._stale_imported_power_hours = {}
            stale_hours = stale_by_stat.setdefault(statistic_id, {})

            def _replaceable_stale_hours(
                current: dict[float, float],
            ) -> set[float]:
                return {
                    hour
                    for hour, mean in current.items()
                    if (prev := stale_hours.get(hour)) is not None
                    and math.isclose(
                        mean,
                        prev,
                        rel_tol=0.0,
                        abs_tol=_STATISTICS_IMPORT_STATE_TOLERANCE,
                    )
                }

            replaceable = _replaceable_stale_hours(present)
            rows: list[StatisticData] = [
                {"start": datetime.fromtimestamp(hour, UTC), "mean": mean}
                for hour, mean in sorted(hourly_mean_w.items())
                if hour not in present or hour in replaceable
            ]
            if rows:
                # Native recorder commits can race with the first read window;
                # re-check committed hours before importing older chart means.
                await self._async_wait_native_recorder_commit()
                valid, present, latest_name = await recorder.async_add_executor_job(
                    partial(self._load_native_power_window, statistic_id, first, last)
                )
                if not valid or not self._owned_native_statistic(
                    statistic_id,
                    tuple(_POWER_SENSOR_BY_CHART_METRIC.values()),
                ):
                    return None
                if latest_name is not None:
                    name = latest_name
                if recorder_guard is not None and not recorder_guard():
                    msg = f"Newer source available for {statistic_id}"
                    raise JackeryStatisticHistoryPendingError(msg)
                replaceable = _replaceable_stale_hours(present)
                rows = [
                    {"start": datetime.fromtimestamp(hour, UTC), "mean": mean}
                    for hour, mean in sorted(hourly_mean_w.items())
                    if hour not in present or hour in replaceable
                ]
            if rows:
                _coordinator_recorder_attr(
                    "async_import_statistics", async_import_statistics
                )(
                    self.hass,
                    {
                        "mean_type": StatisticMeanType.ARITHMETIC,
                        "has_sum": False,
                        "name": name,
                        "source": "recorder",
                        "statistic_id": statistic_id,
                        "unit_class": PowerConverter.UNIT_CLASS,
                        "unit_of_measurement": UnitOfPower.WATT,
                    },
                    rows,
                )
            await self._async_wait_native_recorder_commit()
            valid, committed, _ = await recorder.async_add_executor_job(
                partial(self._load_native_power_window, statistic_id, first, last)
            )
            kept_present = {
                hour: mean for hour, mean in present.items() if hour not in replaceable
            }
            expected = {**hourly_mean_w, **kept_present}
            if not valid or any(
                hour not in committed
                or not math.isclose(
                    committed[hour],
                    value,
                    rel_tol=0.0,
                    abs_tol=_STATISTICS_IMPORT_STATE_TOLERANCE,
                )
                for hour, value in expected.items()
            ):
                msg = f"Waiting for committed power history of {statistic_id}"
                raise JackeryStatisticHistoryPendingError(msg)
            # Preserve the exact integration-written receipts, even when the
            # source was still current at commit time. A later corrected
            # measured curve may replace these hours, but never an unrelated
            # Recorder/live hour that does not match its own prior receipt.
            for row in rows:
                stale_hours[row["start"].timestamp()] = row["mean"]
            if recorder_guard is not None and not recorder_guard():
                if recorder_state is not None and recorder_metric is not None:
                    pending = set(recorder_state.get("pending_reconcile_metrics") or ())
                    pending.add(recorder_metric)
                    recorder_state["pending_reconcile_metrics"] = sorted(pending)
                msg = f"Newer source available for {statistic_id}"
                raise JackeryStatisticHistoryPendingError(msg)
            if (
                recorder_state is not None
                and recorder_metric
                in (recorder_state.get("pending_reconcile_metrics") or ())
                and any(
                    hour in kept_present
                    and not math.isclose(
                        kept_present[hour],
                        value,
                        rel_tol=0.0,
                        abs_tol=_STATISTICS_IMPORT_STATE_TOLERANCE,
                    )
                    for hour, value in hourly_mean_w.items()
                )
            ):
                msg = f"Cannot safely replace existing power history for {statistic_id}"
                raise JackeryStatisticHistoryPendingError(msg)
            # A manually supplied/native hour is never an importer-owned
            # replacement target, even if an older receipt used that slot.
            for hour in kept_present:
                stale_hours.pop(hour, None)
            return len(rows)

    async def _async_import_day_power_for_metric(
        self,
        **options: Unpack[_DayChartPointsOptions],
    ) -> int | None:
        """Fill an existing matching W target from unscaled hourly curve means."""
        recorder_guard = options.pop("recorder_guard", None)
        recorder_state = options.pop("recorder_state", None)
        power_key = _POWER_SENSOR_BY_CHART_METRIC.get(options["metric_key"])
        statistic_id = power_key and er.async_get(self.hass).async_get_entity_id(
            "sensor", DOMAIN, f"{options["device_id"]}_{power_key}"
        )
        if not statistic_id:
            return None
        options["native_power"] = True
        options["use_local_day_guard"] = False
        options["require_complete_day"] = options[
            "bucket_minutes"
        ] == _RECORDER_HOUR_MINUTES and not options.get("require_complete_bucket")
        options["use_scalar_total"] = False
        points = self._day_chart_points_for_metric(**options)
        if not points:
            return None
        recorder_options: _PowerRecorderOptions = {}
        if recorder_guard is not None:
            recorder_options["recorder_guard"] = recorder_guard
        if recorder_state is not None:
            recorder_options["recorder_state"] = recorder_state
            recorder_options["recorder_metric"] = options["stat_key"]
        # Five-minute samples contribute their actual *observed* mean to the
        # corresponding Recorder hour. Never assume twelve readings were
        # present, and never book a five-minute timestamp as an hourly row.
        per_hour: dict[float, list[float]] = {}
        for point in points:
            start = self._local_statistic_start(
                point.start_date.replace(minute=0, second=0, microsecond=0)
            ).timestamp()
            per_hour.setdefault(start, []).append(point.value)
        sample_minutes = options["bucket_minutes"]
        hourly_mean_w = {
            start: round(sum(values) * 1000 * 60 / (len(values) * sample_minutes), 5)
            for start, values in per_hour.items()
        }
        return await self._async_fill_power_statistic_day(
            statistic_id,
            hourly_mean_w,
            **recorder_options,
        )

    async def _async_import_month_chart_day(  # ruff: ignore[too-many-branches, too-many-statements]
        self,
        candidate: _HttpDayBackfillCandidate,
        progress: _HttpDayBackfillProgress,
    ) -> bool | None:
        """Fill a day without any curve from its month-chart kWh bucket.

        Day curves end ~90 days back; the month chart still carries one kWh
        value per day back to commissioning (ENERGY_F0_EVIDENCE_2026-09-24.md).
        No hourly shape is invented: the day's energy is booked in its last
        hour, where the day total is known.
        """
        day = candidate.target_day
        key = (
            candidate.device_id,
            candidate.section_prefix,
            day.replace(day=1).isoformat(),
        )
        period_key = key[0], key[1], DATE_TYPE_MONTH, key[2]
        source = progress.month_sources.get(key)
        if source is not None and not self._historical_period_source_is_current(
            period_key, source
        ):
            progress.month_sources.pop(key, None)
            progress.month_source_revisions.pop(key, None)
            source = None
        if source is None:
            progress.requests += 1
            try:
                source = await self._async_fetch_historical_app_chart_source(
                    device_id=candidate.device_id,
                    system_id=self._system_id_from_payload(
                        candidate.device_id, candidate.payload
                    ),
                    ct_device_id=(
                        self._smart_meter_accessory_device_id(candidate.payload)
                        or self._smart_meter_accessory_device_id(
                            getattr(self, "_device_index", {}).get(candidate.device_id)
                            or {}
                        )
                    ),
                    section_prefix=candidate.section_prefix,
                    date_type=DATE_TYPE_MONTH,
                    period_start=day.replace(day=1),
                )
            except JackeryStatisticHistoryPendingError:
                candidate.day_state["refresh_source_required"] = True
                raise
            except JackeryAuthError:
                candidate.day_state["last_error"] = "auth_error"
                progress.stopped = True
                return None
            except (TimeoutError, HomeAssistantError, JackeryError) as err:
                if _is_system_busy_error(err):
                    self._set_statistics_rate_limit(err)
                    candidate.day_state["last_error"] = "rate_limited"
                    progress.rate_limited = True
                    progress.stopped = True
                    return None
                _LOGGER.debug(
                    "Jackery month chart %s for %s (%s) failed: %s",
                    candidate.section_prefix,
                    candidate.device_id,
                    key[2],
                    exception_debug_message(err),
                )
                candidate.day_state["last_error"] = "transport_error"
                return None
            progress.month_sources[key] = source
        if not isinstance(
            source, _HistoricalPeriodSource
        ) or not self._historical_period_source_is_current(period_key, source):
            candidate.day_state["refresh_source_required"] = True
            raise JackeryStatisticHistoryPendingError(  # ruff: ignore[raise-vanilla-args]
                "Waiting for a current owner-bound month fallback"
            )
        cutoff = source.day_revision
        progress.month_source_revisions[key] = cutoff
        if not source:
            return None
        section = f"{candidate.section_prefix}_{DATE_TYPE_MONTH}"
        day_start = self._local_statistic_start(day).timestamp()
        day_end = self._local_statistic_start(day + timedelta(days=1)).timestamp()
        hours = [float(hour) for hour in range(int(day_start), int(day_end), 3600)]
        imported_rows = 0
        planned: list[tuple[str, float, tuple[str, str, str, str], list[str]]] = []
        for metric_section, stat_key, _metric_key, _label in _app_chart_stat_metrics():
            if metric_section != candidate.section_prefix:
                continue
            value = chart_value_for_day(source, section, stat_key, today=day)
            if value is None or value < 0:
                continue
            metric_key = (
                candidate.device_id,
                day.isoformat(),
                candidate.section_prefix,
                stat_key,
            )
            targets = [
                statistic_id
                for statistic_id, reset_period in self._energy_statistic_targets(
                    candidate.device_id, candidate.section_prefix, stat_key
                )
                if reset_period == DATE_TYPE_DAY
            ]
            if not targets:
                continue
            if not self._month_day_source_is_current(metric_key, source):
                pending = set(
                    candidate.day_state.get("pending_reconcile_metrics") or ()
                )
                pending.add(stat_key)
                candidate.day_state["pending_reconcile_metrics"] = sorted(pending)
                candidate.day_state["refresh_source_required"] = True
                continue
            planned.append((stat_key, value, metric_key, targets))
        if planned:
            intent = set(candidate.day_state.get("recorder_import_intent") or ())
            intent.update(stat_key for stat_key, _value, _key, _targets in planned)
            candidate.day_state["recorder_import_intent"] = sorted(intent)
            candidate.day_state["month_source_owner"] = self._observed_day_owner_key(
                source.source_owner
            )
            candidate.day_state["month_source_request_key"] = list(period_key)
        for stat_key, value, metric_key, targets in planned:
            for statistic_id in targets:
                imported_rows += await self._async_reconcile_statistic_day(
                    statistic_id,
                    hours,
                    {hours[-1]: value},
                    aggregate_only=True,
                    recorder_guard=partial(
                        self._month_day_source_is_current, metric_key, source
                    ),
                    recorder_state=candidate.day_state,
                )
                candidate.month_metric_revisions[stat_key] = cutoff
        progress.gap_filled_rows += imported_rows
        if not planned:
            return None
        candidate.day_state["source"] = "month_chart"
        return True

    def _verified_historical_day_totals(
        self,
        *,
        device_id: str,
        section_prefix: str,
        target_day: date,
        source: dict[str, Any],
    ) -> dict[str, float]:
        """Return verified kWh totals from one fetched HTTP day curve.

        This deliberately reuses the Recorder import converter.  A scalar
        without curve points therefore cannot become entity data, while an
        explicit all-zero curve remains valid corroboration for a zero day.
        """
        historical_payload = self._historical_day_payload_from_sources(
            {section_prefix: source},
        )
        now = self._local_now()
        totals: dict[str, float] = {}
        for metric_section, stat_key, metric_key, _label in _app_chart_stat_metrics():
            if metric_section != section_prefix:
                continue
            points = self._day_chart_points_for_metric(
                device_id=device_id,
                payload=historical_payload,
                section_prefix=section_prefix,
                stat_key=stat_key,
                metric_key=metric_key,
                bucket_minutes=60,
                now=now,
                use_local_day_guard=False,
                require_complete_day=True,
                target_day=target_day,
            )
            if points:
                totals[stat_key] = round(sum(point.value for point in points), 5)
        return totals

    def _merge_verified_day_totals_update(
        self,
        updates: dict[str, dict[str, Any]],
        *,
        day_totals: dict[str, float],
        **window: Unpack[_VerifiedDayWindow],
    ) -> None:
        """Merge verified totals for one completed day into a batched update."""
        device_id = window["device_id"]
        target_day = window["target_day"]
        section_prefix = window["section_prefix"]
        week_start = window["week_start"]
        today = window["today"]
        window_start = min(today.replace(month=1, day=1), week_start)
        if not window_start <= target_day < today:
            return
        if not day_totals:
            return

        self._invalidate_verified_day_source_bindings(notify=False)
        if isinstance(updates, _VerifiedDayUpdates):
            owner = self._observed_day_source_owner(device_id, section_prefix)
            if owner is None:
                return
            updates.owners[device_id, section_prefix] = owner

        device_days = updates.get(device_id)
        if device_days is None:
            current_payload = (self.data or {}).get(device_id)
            current_days = (
                current_payload.get(PAYLOAD_VERIFIED_DAY_STATISTICS)
                if isinstance(current_payload, dict)
                else None
            )
            device_days = {
                day_key: copy.deepcopy(day_value)
                for day_key, day_value in (
                    current_days.items() if isinstance(current_days, dict) else ()
                )
                if window_start.isoformat() <= day_key < today.isoformat()
                and isinstance(day_value, dict)
            }
            if isinstance(updates, _VerifiedDayUpdates):
                updates.baselines[device_id] = copy.deepcopy(device_days)
            updates[device_id] = device_days

        target_sources = device_days.setdefault(target_day.isoformat(), {})
        if isinstance(target_sources, dict):
            target_sources[section_prefix] = {
                **target_sources.get(section_prefix, {}),
                **day_totals,
            }

    def _publish_verified_day_updates(self, updates: _VerifiedDayUpdates) -> None:
        """Apply only captured changes that do not overwrite a newer update."""
        self._publish_observed_day_updates(updates)
        missing = object()
        emitted: dict[tuple[str, str, str, str], tuple[int, float]] = {}
        # Publication is synchronous. Resolve each source once for this call;
        # a later publication must take a fresh physical-identity snapshot.
        owners: dict[tuple[str, str], object] = {}

        def changes(
            device_id: str,
            path: tuple[str, ...],
            baseline: dict[str, Any],
            proposed: dict[str, Any],
            current: dict[str, Any],
        ) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in proposed.items():
                old = baseline.get(key, missing)
                recent = current.get(key, missing)
                if isinstance(value, dict):
                    nested = changes(
                        device_id,
                        (*path, key),
                        old if isinstance(old, dict) else {},
                        value,
                        recent if isinstance(recent, dict) else {},
                    )
                    if nested:
                        result[key] = nested
                else:
                    if len(path) != 2:  # ruff: ignore[magic-value-comparison]
                        continue
                    source_key = device_id, path[1]
                    owner = updates.owners.get(source_key)
                    if source_key not in owners:
                        owners[source_key] = self._observed_day_source_owner(
                            *source_key
                        )
                    if owner is None or owner != owners[source_key]:
                        continue
                    metric_key = (device_id, path[0], path[1], key)
                    revision = updates.revisions.get(metric_key)
                    if revision is None:
                        if value != old and recent == old:
                            result[key] = value
                        continue
                    latest = self._verified_day_metric_revisions.get(metric_key, 0)
                    previous = self._verified_day_published_revisions.get(metric_key)
                    if revision != latest or value == recent:
                        continue
                    if recent == old or (
                        previous is not None
                        and previous[0] < revision
                        and recent == previous[1]
                    ):
                        result[key] = value
                        emitted[metric_key] = (revision, value)
            return result

        pending: dict[str, dict[str, Any]] = {}
        for device_id, proposed in updates.items():
            current_payload = (self.data or {}).get(device_id) or {}
            current = current_payload.get(PAYLOAD_VERIFIED_DAY_STATISTICS)
            delta = changes(
                device_id,
                (),
                updates.baselines.get(device_id, {}),
                proposed,
                current if isinstance(current, dict) else {},
            )
            if delta:
                pending[device_id] = {PAYLOAD_VERIFIED_DAY_STATISTICS: delta}
        if pending:
            bindings = getattr(self, "_verified_day_payload_bindings", None)
            if bindings is None:
                self._verified_day_payload_bindings: dict[tuple[str, str], object] = {}
                bindings = self._verified_day_payload_bindings
            for device_id, update in pending.items():
                prefixes = {
                    prefix
                    for sections in update[PAYLOAD_VERIFIED_DAY_STATISTICS].values()
                    for prefix in sections
                }
                for prefix in prefixes:
                    bindings[device_id, prefix] = updates.owners[device_id, prefix]
            self._push_partial_update(pending)
            self._verified_day_published_revisions.update(emitted)

    def _observed_day_source_owner(
        self, device_id: str, section_prefix: str, payload: dict[str, Any] | None = None
    ) -> tuple[object, ...] | None:
        """Bind derived evidence to its physical parent and, for CT, accessory."""
        if getattr(self, "_shutdown_started", False):
            return None
        owner = self._pv_month_recovery_owner(device_id)
        if owner is None:
            return None
        ct_id = ct_serial = None
        if section_prefix == APP_SECTION_CT_STAT:
            current = (getattr(self, "data", None) or {}).get(device_id)
            index = getattr(self, "_device_index", {}).get(device_id) or {}
            record = current if isinstance(current, dict) else payload or index
            ct_id = self._smart_meter_accessory_device_id(record)
            if ct_id is None:
                ct_id = self._smart_meter_accessory_device_id(index)
            if ct_id is None:
                return None
            meter = record.get(PAYLOAD_CT_METER)
            if isinstance(meter, dict):
                ct_serial = meter.get(FIELD_DEVICE_SN)
            if ct_serial is not None and not isinstance(ct_serial, str):
                return None
        return owner[0], owner[1], ct_id, ct_serial

    @staticmethod
    def _observed_day_owner_key(owner: object) -> list[object] | None:
        """Persist physical identity without process-local entry/epoch objects."""
        if not isinstance(owner, tuple) or len(owner) != 4:  # ruff: ignore[magic-value-comparison]
            return None
        parent = owner[1]
        if not isinstance(parent, tuple) or len(parent) < 5:  # ruff: ignore[magic-value-comparison]
            return None
        # Model/configuration enrichment and process-local epochs do not change
        # which physical parent/system/accessory supplied a recorded sample.
        return [*parent[:3], owner[2], owner[3]]

    def _observed_day_curve_totals(
        self, source: dict[str, Any], section_prefix: str, target_day: date
    ) -> dict[str, dict[str, Any]]:
        """Integrate only dated, axis-qualified own samples, never a scalar fill."""
        now = self._local_now()
        request = source.get(APP_REQUEST_META)
        if (
            not isinstance(request, dict)  # ruff: ignore[too-many-boolean-expressions]
            or request.get(APP_REQUEST_DATE_TYPE) != DATE_TYPE_DAY
            or request_date(source, APP_REQUEST_BEGIN_DATE, APP_REQUEST_BEGIN_DATE_ALT)
            != target_day
            or request_date(source, APP_REQUEST_END_DATE, APP_REQUEST_END_DATE_ALT)
            != target_day
            or target_day > now.date()
            or str(source.get(APP_STAT_UNIT) or "").strip().lower()
            not in {"w", "wh", "kwh"}
        ):
            return {}
        totals: dict[str, dict[str, Any]] = {}
        section = f"{section_prefix}_{DATE_TYPE_DAY}"
        for prefix, stat_key, _metric, _label in _app_chart_stat_metrics():
            if prefix != section_prefix:
                continue
            series_key = day_power_series_key(source, section, stat_key)
            series = source.get(series_key) if series_key else None
            if not isinstance(series, list) or not series:
                continue
            labels = source.get(APP_CHART_LABELS)
            if labels is None or labels == []:
                if len(series) != _OBSERVED_DAY_EXPECTED_SAMPLES:
                    continue
            else:
                if not isinstance(labels, list) or len(labels) != len(series):
                    continue
                # A null placeholder has no observed timestamp. Check the axis
                # of actual finite samples without turning null gaps into zero.
                observed_labels = [
                    "23:55"
                    if label == "24:00"
                    and len(series) == _OBSERVED_DAY_EXPECTED_SAMPLES
                    and index == _OBSERVED_DAY_EXPECTED_SAMPLES - 1
                    else label
                    for index, (label, raw) in enumerate(
                        zip(labels, series, strict=True)
                    )
                    if isinstance(raw, (int, float, str))
                    and safe_float(raw) is not None
                ]
                minutes = _explicit_day_curve_minutes(
                    observed_labels, len(observed_labels)
                )
                if minutes is None or any(minute % 5 for minute in minutes):
                    continue
            points = day_power_energy_points(
                source,
                section,
                stat_key,
                bucket_minutes=5,
                today=now.date(),
                now=now,
                require_complete_day=False,
                use_scalar_total=False,
            )
            if not points or any(
                (
                    point.start_date.date()
                    if isinstance(point.start_date, datetime)
                    else point.start_date
                )
                != target_day
                for point in points
            ):
                continue
            value = round(sum(point.value for point in points), 5)
            if not math.isfinite(value) or value < 0:
                continue
            count = len(points)
            totals[stat_key] = {
                "value": value,
                "observed_samples": count,
                "expected_samples": _OBSERVED_DAY_EXPECTED_SAMPLES,
                "complete": count == _OBSERVED_DAY_EXPECTED_SAMPLES,
                "source": "five_minute_power_curve",
            }
        return totals

    @staticmethod
    def _cached_observed_day_totals(
        day_state: dict[str, Any], section_prefix: str
    ) -> dict[str, dict[str, Any]]:
        """Restore qualified observations separately from complete-day certificates."""
        if (
            day_state.get("observed_totals_rule")
            != _STATISTICS_HTTP_OBSERVED_TOTALS_RULE
        ):
            return {}
        rows = day_state.get(_STATISTICS_HTTP_OBSERVED_TOTALS)
        if not isinstance(rows, dict):
            return {}
        expected = {
            key
            for prefix, key, _, _ in _app_chart_stat_metrics()
            if prefix == section_prefix
        }
        result: dict[str, dict[str, Any]] = {}
        for key, row in rows.items():
            if key not in expected or not isinstance(row, dict):
                continue
            value = safe_float(row.get("value"))
            count = row.get("observed_samples")
            if (
                value is None  # ruff: ignore[too-many-boolean-expressions]
                or value < 0
                or type(count) is not int
                or not 0 < count <= _OBSERVED_DAY_EXPECTED_SAMPLES
                or row.get("expected_samples") != _OBSERVED_DAY_EXPECTED_SAMPLES
                or type(row.get("complete")) is not bool
                or row["complete"] != (count == _OBSERVED_DAY_EXPECTED_SAMPLES)
                or row.get("source") != "five_minute_power_curve"
            ):
                continue
            result[key] = {
                "value": value,
                "observed_samples": count,
                "expected_samples": _OBSERVED_DAY_EXPECTED_SAMPLES,
                "complete": row["complete"],
                "source": "five_minute_power_curve",
            }
        return result

    def _stage_observed_day_totals(  # ruff: ignore[too-many-arguments]
        self,
        updates: _VerifiedDayUpdates,
        *,
        device_id: str,
        target_day: date,
        section_prefix: str,
        totals: dict[str, dict[str, Any]],
        owner: object,
        revision: int,
    ) -> None:
        """Capture whole metric leaves and their publication baseline."""
        current_days = ((self.data or {}).get(device_id) or {}).get(
            PAYLOAD_OBSERVED_DAY_STATISTICS
        )
        day_key = target_day.isoformat()
        current_day = (
            current_days.get(day_key) if isinstance(current_days, dict) else None
        )
        current_section = (
            current_day.get(section_prefix) if isinstance(current_day, dict) else None
        )
        destination = (
            updates.observed
            .setdefault(device_id, {})
            .setdefault(day_key, {})
            .setdefault(section_prefix, {})
        )
        updates.observed_owners[device_id, section_prefix] = owner
        for metric, value in totals.items():
            key = device_id, day_key, section_prefix, metric
            if key not in updates.observed_baselines:
                updates.observed_baselines[key] = copy.deepcopy(
                    current_section.get(metric)
                    if isinstance(current_section, dict)
                    else None
                )
            updates.observed_revisions[key] = revision
            destination[metric] = copy.deepcopy(value)

    def _invalidate_verified_day_source_bindings(self, *, notify: bool = True) -> bool:  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
        """Retire certificates from another physical parent or CT, never Recorder rows."""  # ruff: ignore[line-too-long]
        if getattr(self, "_shutdown_started", False):
            return False
        changed = False
        checked = getattr(self, "_verified_day_persisted_bindings", None)
        if checked is None:
            self._verified_day_persisted_bindings: dict[
                tuple[str, str], tuple[object, object]
            ] = {}
            checked = self._verified_day_persisted_bindings
        devices = getattr(self, "_statistics_backfill_state", {}).get("devices", {})
        for device_id, state in devices.items() if isinstance(devices, dict) else ():  # ruff: ignore[too-many-nested-blocks]
            queue = state.get("http_day_backfill") if isinstance(state, dict) else None
            sources = queue.get("sources") if isinstance(queue, dict) else None
            for prefix, source_state in (
                sources.items() if isinstance(sources, dict) else ()
            ):
                owner_key = self._observed_day_owner_key(
                    self._observed_day_source_owner(device_id, prefix)
                )
                if owner_key is None:
                    continue
                previous = checked.get((device_id, prefix))
                if (
                    previous is not None
                    and previous[0] is source_state
                    and previous[1] == owner_key
                ):
                    continue
                days = (
                    source_state.get("days") if isinstance(source_state, dict) else None
                )
                for day_state in days.values() if isinstance(days, dict) else ():
                    if not isinstance(day_state, dict) or (
                        day_state.get("verified_source_owner") == owner_key
                    ):
                        continue
                    if any(
                        key in day_state
                        for key in (
                            _STATISTICS_HTTP_VERIFIED_TOTALS,
                            "verified_totals_rule",
                            "verified_source_owner",
                        )
                    ):
                        previous_owner = day_state.get("verified_source_owner")
                        for key in (
                            _STATISTICS_HTTP_VERIFIED_TOTALS,
                            "verified_totals_rule",
                            "verified_source_owner",
                            "totals_last_attempt_at",
                            "totals_checked_date",
                        ):
                            day_state.pop(key, None)
                        if previous_owner is not None:
                            # A known physical replacement must not inherit the
                            # old source's successful import or pending raw curve.
                            for key in (
                                "unimported_source",
                                "imported_rows",
                                "last_attempt_at",
                                "checked_date",
                                "pending_reconcile_metrics",
                                "recorder_import_intent",
                                _STATISTICS_HTTP_RETRY_AFTER_EPOCH,
                            ):
                                day_state.pop(key, None)
                            day_state["status"] = BackfillStatus.PENDING.value
                            day_state["refresh_source_required"] = True
                            visited: set[tuple[str, str, str]] = getattr(
                                self, "_statistics_startup_fetched", set()
                            )
                            for item in tuple(visited):
                                if item[:2] == (device_id, prefix):
                                    visited.discard(item)
                        else:
                            # Legacy ownerless certificates are untrusted. Keep
                            # Recorder idempotency and recover only via verification.
                            day_state["owner_verification_required"] = True
                        changed = True
                checked[device_id, prefix] = source_state, owner_key
        if changed:
            self._verified_day_state_dirty = True

        bindings = getattr(self, "_verified_day_payload_bindings", None)
        if bindings is None:
            self._verified_day_payload_bindings = {}
            bindings = self._verified_day_payload_bindings
        pending: dict[str, dict[str, Any]] = {}
        for device_id, payload in (self.data or {}).items():
            days = payload.get(PAYLOAD_VERIFIED_DAY_STATISTICS)
            if not isinstance(days, dict) or not days:
                continue
            prefixes = {
                prefix
                for sections in days.values()
                if isinstance(sections, dict)
                for prefix in sections
            }
            stale = {
                prefix
                for prefix in prefixes
                if bindings.get((device_id, prefix)) is None
                or bindings[device_id, prefix]
                != self._observed_day_source_owner(device_id, prefix)
            }
            if not stale:
                continue
            clean = {
                day: {
                    prefix: values
                    for prefix, values in sections.items()
                    if prefix not in stale
                }
                for day, sections in days.items()
                if isinstance(sections, dict)
            }
            clean = {day: sections for day, sections in clean.items() if sections}
            pending[device_id] = {**payload, PAYLOAD_VERIFIED_DAY_STATISTICS: clean}
            for prefix in stale:
                bindings.pop((device_id, prefix), None)
        if pending:
            if notify:
                # A complete device replacement removes retired certificate leaves;
                # sparse telemetry merges intentionally retain omitted values.
                self._push_partial_update(
                    pending, replace_device_ids=frozenset(pending)
                )
            else:
                self.data = {**self.data, **pending}
        return changed or bool(pending)

    def _invalidate_observed_day_source_bindings(self, *, notify: bool = True) -> bool:
        """Remove old physical-source floors even when no replacement curve arrives."""
        if getattr(self, "_shutdown_started", False):
            return False
        verified_changed = self._invalidate_verified_day_source_bindings(notify=notify)
        bindings = getattr(self, "_observed_day_payload_bindings", None)
        if bindings is None:
            self._observed_day_payload_bindings: dict[
                tuple[str, str], tuple[object, ...] | None
            ] = {}
            bindings = self._observed_day_payload_bindings
        pending: dict[str, dict[str, Any]] = {}
        for device_id, payload in (self.data or {}).items():
            days = payload.get(PAYLOAD_OBSERVED_DAY_STATISTICS)
            if not isinstance(days, dict) or not days:
                continue
            prefixes = {
                prefix
                for sections in days.values()
                if isinstance(sections, dict)
                for prefix in sections
            }
            stale = set()
            for prefix in prefixes:
                owner = self._observed_day_source_owner(device_id, prefix)
                key = device_id, prefix
                if key not in bindings or (
                    self._observed_day_owner_key(bindings[key])
                    != self._observed_day_owner_key(owner)
                ):
                    stale.add(prefix)
                bindings[key] = owner
            if not stale:
                continue
            clean = {
                day: {
                    prefix: metrics
                    for prefix, metrics in sections.items()
                    if prefix not in stale
                }
                for day, sections in days.items()
                if isinstance(sections, dict)
            }
            clean = {day: sections for day, sections in clean.items() if sections}
            if clean != days:
                pending[device_id] = {PAYLOAD_OBSERVED_DAY_STATISTICS: clean}
        if not pending:
            return verified_changed
        if notify:
            self._push_partial_update(pending)
        else:
            self.data = {
                **self.data,
                **{
                    device_id: {**self.data[device_id], **update}
                    for device_id, update in pending.items()
                },
            }
        return True

    def _record_observed_day_totals_update(  # ruff: ignore[too-many-locals]
        self,
        updates: _VerifiedDayUpdates,
        *,
        source: dict[str, Any],
        day_state: dict[str, Any],
        **window: Unpack[_VerifiedDayWindow],
    ) -> bool:
        """Retain measured partial sums without certifying or importing history."""
        device_id = window["device_id"]
        prefix = window["section_prefix"]
        target_day = window["target_day"]
        owner = self._observed_day_source_owner(device_id, prefix)
        owner_key = self._observed_day_owner_key(owner)
        if owner is None or owner_key is None:
            return False
        if (
            isinstance(source, _HistoricalDaySource)
            and source.source_owner is not None
            and source.source_owner != owner
        ):
            # Owner tuples can contain unhashable metadata; compare by value.
            return False
        now = self._local_now()
        try:
            encoded = json.dumps(
                [
                    source,
                    now.hour * 12 + now.minute // 5
                    if target_day == now.date()
                    else None,
                ],
                sort_keys=True,
            )
        except TypeError, ValueError:
            signature = None
        else:
            signature = hashlib.sha256(encoded.encode()).hexdigest()
        same_owner = day_state.get("observed_source_owner") == owner_key
        cached = (
            self._cached_observed_day_totals(day_state, prefix) if same_owner else {}
        )
        if (
            same_owner
            and signature is not None
            and signature == day_state.get("observed_source_signature")
        ):
            totals = cached
        else:
            totals = self._observed_day_curve_totals(source, prefix, target_day)
        if not totals:
            return False
        revisions = getattr(self, "_observed_day_metric_revisions", None)
        if revisions is None:
            self._observed_day_metric_revisions: dict[
                tuple[str, str, str, str], int
            ] = {}
            revisions = self._observed_day_metric_revisions
        revision = getattr(source, "revision", None)
        if type(revision) is not int:
            revision = getattr(self, "_historical_day_request_revision", 0) + 1
            self._historical_day_request_revision = revision
        accepted = {}
        for metric, value in totals.items():
            key = device_id, target_day.isoformat(), prefix, metric
            if revision >= revisions.get(key, 0):
                revisions[key] = revision
                accepted[metric] = value
        if not accepted:
            return False
        combined = {**cached, **accepted}
        changed = (
            combined != cached
            or not same_owner
            or day_state.get("observed_source_signature") != signature
        )
        day_state.update({
            _STATISTICS_HTTP_OBSERVED_TOTALS: combined,
            "observed_totals_rule": _STATISTICS_HTTP_OBSERVED_TOTALS_RULE,
            "observed_source_owner": owner_key,
            "observed_source_signature": signature,
        })
        self._stage_observed_day_totals(
            updates,
            device_id=device_id,
            target_day=target_day,
            section_prefix=prefix,
            totals=combined,
            owner=owner,
            revision=revision,
        )
        return changed

    def _publish_observed_day_updates(self, updates: _VerifiedDayUpdates) -> None:  # ruff: ignore[too-many-branches]
        """Publish complete metric leaves only while their source binding is current."""
        self._invalidate_observed_day_source_bindings()
        if not isinstance(updates, _VerifiedDayUpdates) or not updates.observed:
            return
        revisions = getattr(self, "_observed_day_metric_revisions", {})
        pending: dict[str, dict[str, Any]] = {}
        owners: dict[tuple[str, str], tuple[object, ...] | None] = {}
        accepted_owners: dict[tuple[str, str], tuple[object, ...] | None] = {}
        for device_id, days in updates.observed.items():
            current = ((self.data or {}).get(device_id) or {}).get(
                PAYLOAD_OBSERVED_DAY_STATISTICS
            ) or {}
            for day, sections in days.items():
                for prefix, metrics in sections.items():
                    source_key = device_id, prefix
                    if source_key not in owners:
                        owners[source_key] = self._observed_day_source_owner(
                            *source_key
                        )
                    if updates.observed_owners.get(source_key) != owners[source_key]:
                        continue
                    for metric, value in metrics.items():
                        key = device_id, day, prefix, metric
                        if updates.observed_revisions.get(key, 0) != revisions.get(
                            key, 0
                        ):
                            continue
                        recent = ((current.get(day) or {}).get(prefix) or {}).get(
                            metric
                        )
                        if value == recent:
                            continue
                        if recent != updates.observed_baselines.get(key):
                            continue
                        if device_id not in pending:
                            # setdefault would copy the entire retained year for
                            # every metric, including after the key already exists.
                            pending[device_id] = {
                                PAYLOAD_OBSERVED_DAY_STATISTICS: copy.deepcopy(current)
                            }
                        destination = pending[device_id][
                            PAYLOAD_OBSERVED_DAY_STATISTICS
                        ]
                        target = destination.setdefault(day, {}).setdefault(prefix, {})
                        target[metric] = value
                        accepted_owners[source_key] = owners[source_key]
        if pending:
            bindings = getattr(self, "_observed_day_payload_bindings", None)
            if bindings is None:
                bindings = self._observed_day_payload_bindings = {}
            # Retained, unchanged leaves were validated above by the invalidator.
            # Only newly accepted sources need a binding, once per source.
            bindings.update(accepted_owners)
            self._push_partial_update(pending)

    @staticmethod
    def _cached_verified_day_totals(
        day_state: dict[str, Any],
        section_prefix: str,
        *,
        owner_key: list[object] | None = None,
    ) -> dict[str, float]:
        """Read only finite, nonnegative totals certified by the current rule."""
        if (
            owner_key is None
            or day_state.get("verified_source_owner") != owner_key
            or day_state.get("verified_totals_rule")
            != _STATISTICS_HTTP_VERIFIED_TOTALS_RULE
        ):
            return {}
        cached_totals = day_state.get(_STATISTICS_HTTP_VERIFIED_TOTALS)
        if not isinstance(cached_totals, dict):
            return {}
        expected_keys = {
            stat_key
            for metric_section, stat_key, _, _ in _app_chart_stat_metrics()
            if metric_section == section_prefix
        }
        return {
            key: value
            for key, raw_value in cached_totals.items()
            if key in expected_keys
            if (value := safe_float(raw_value)) is not None and value >= 0
        }

    def _record_verified_day_totals_update(  # ruff: ignore[too-many-branches]  # ruff: ignore[too-many-statements]
        self,
        updates: dict[str, dict[str, Any]],
        *,
        source: dict[str, Any],
        day_state: dict[str, Any],
        **window: Unpack[_VerifiedDayWindow],
    ) -> dict[str, float]:
        """Convert and batch one completed current-week HTTP curve."""
        device_id = window["device_id"]
        target_day = window["target_day"]
        section_prefix = window["section_prefix"]
        today = window["today"]
        owner = self._observed_day_source_owner(device_id, section_prefix)
        owner_key = self._observed_day_owner_key(owner)
        if owner_key is None or not self._historical_day_source_owner_is_current(
            (device_id, target_day.isoformat(), section_prefix, ""), source
        ):
            if isinstance(source, _HistoricalDaySource):
                source.stale_metrics = True
            day_state["refresh_source_required"] = True
            return {}
        day_totals = self._verified_historical_day_totals(
            device_id=device_id,
            section_prefix=section_prefix,
            target_day=target_day,
            source=source,
        )
        revision = getattr(source, "revision", 0)
        if isinstance(source, _HistoricalDaySource) and day_totals:
            revisions = getattr(self, "_verified_day_metric_revisions", None)
            if revisions is None:
                revisions = self._verified_day_metric_revisions = {}
            day_key = target_day.isoformat()
            accepted: dict[str, float] = {}
            for metric, value in day_totals.items():
                key = (device_id, day_key, section_prefix, metric)
                if revision < revisions.get(key, 0):
                    if isinstance(source, _HistoricalDaySource):
                        source.stale_metrics = True
                    continue
                revisions[key] = revision
                accepted[metric] = value
                if isinstance(updates, _VerifiedDayUpdates):
                    updates.revisions[key] = revision
            source.accepted_metrics = frozenset(accepted)
            cached_source = day_state.get("unimported_source")
            if (
                accepted
                and day_state.get("last_error") == "recorder_pending"
                and isinstance(cached_source, dict)
                and revision > getattr(cached_source, "revision", 0)
            ):
                day_state["refresh_source_required"] = True
            if source.stale_metrics and accepted:
                pending = set(day_state.get("pending_reconcile_metrics") or ())
                for metric in accepted:
                    if self._has_historical_day_recorder_target(
                        device_id, section_prefix, metric
                    ):
                        pending.add(metric)
                if pending:
                    day_state["pending_reconcile_metrics"] = sorted(pending)
                    day_state["status"] = BackfillStatus.PENDING.value
            day_totals = accepted
            if not day_totals:
                return {}
        if day_totals:
            day_totals = {
                **self._cached_verified_day_totals(
                    day_state, section_prefix, owner_key=owner_key
                ),
                **day_totals,
            }
        self._merge_verified_day_totals_update(
            updates,
            day_totals=day_totals,
            **window,
        )
        if day_totals and target_day < today:
            # Persist the small derived totals alongside the source/day queue.
            # On restart, an already-imported Recorder bucket can restore
            # entity data without another cloud request.
            day_state[_STATISTICS_HTTP_VERIFIED_TOTALS] = day_totals
            day_state["verified_totals_rule"] = _STATISTICS_HTTP_VERIFIED_TOTALS_RULE
            day_state["verified_source_owner"] = owner_key
            day_state.pop("owner_verification_required", None)
        if isinstance(updates, _VerifiedDayUpdates):
            self._record_observed_day_totals_update(
                updates, source=source, day_state=day_state, **window
            )
        return day_totals

    def _restore_or_reopen_imported_day_totals(
        self,
        updates: dict[str, dict[str, Any]],
        *,
        state_status: BackfillStatus,
        day_state: dict[str, Any],
        **window: Unpack[_VerifiedDayWindow],
    ) -> tuple[bool, bool, bool]:
        """Restore cached totals without reopening an idempotent successful import.

        Returns ``(skip_candidate, state_changed, has_verified_totals)``.
        """
        target_day = window["target_day"]
        section_prefix = window["section_prefix"]
        week_start = window["week_start"]
        today = window["today"]
        in_completed_window = (
            min(today.replace(month=1, day=1), week_start) <= target_day < today
        )
        usable_totals = (
            self._cached_verified_day_totals(
                day_state,
                section_prefix,
                owner_key=self._observed_day_owner_key(
                    self._observed_day_source_owner(window["device_id"], section_prefix)
                ),
            )
            if in_completed_window
            else {}
        )
        has_verified_totals = bool(usable_totals)
        if not usable_totals:
            return state_status is BackfillStatus.IMPORTED, False, False
        current_payload = (self.data or {}).get(window["device_id"]) or {}
        current_days = current_payload.get(PAYLOAD_VERIFIED_DAY_STATISTICS)
        current_day = (
            current_days.get(target_day.isoformat())
            if isinstance(current_days, dict)
            else None
        )
        current_section = (
            current_day.get(section_prefix) if isinstance(current_day, dict) else None
        )
        missing_totals = {
            key: value
            for key, value in usable_totals.items()
            if not isinstance(current_section, dict) or key not in current_section
        }
        if missing_totals:
            self._merge_verified_day_totals_update(
                updates,
                day_totals=missing_totals,
                **window,
            )
        return state_status is BackfillStatus.IMPORTED, False, has_verified_totals

    def _set_statistics_rate_limit(self, error: BaseException) -> None:
        """Keep the longest active pause reported by parallel statistics reads."""
        current = safe_float(getattr(self, "_statistics_rate_limit_until", None)) or 0.0
        self._statistics_rate_limit_until = max(
            current, time.time() + _rate_limit_retry_after_seconds(error)
        )

    def _verified_day_metric_is_current(
        self, key: tuple[str, str, str, str], revision: int
    ) -> bool:
        """Protect Recorder writes from a newer certified HTTP source."""
        revisions: dict[tuple[str, str, str, str], int] = getattr(
            self, "_verified_day_metric_revisions", {}
        )
        return revisions.get(key, 0) <= revision

    def _partial_day_power_source_is_current(
        self, key: tuple[str, str, str, str], source: _HistoricalDaySource
    ) -> bool:
        """Bind incomplete chart samples to their current owner and revisions."""
        if not self._historical_day_source_owner_is_current(key, source):
            return False
        observed = getattr(self, "_observed_day_metric_revisions", {})
        return observed.get(
            key, 0
        ) <= source.revision and self._verified_day_metric_is_current(
            key, source.revision
        )

    def _historical_day_source_owner_is_current(
        self, key: tuple[str, str, str, str], source: object
    ) -> bool:
        """Fence awaited Recorder work by entry lifecycle and physical source."""
        if getattr(self, "_shutdown_started", False) or not isinstance(
            source, _HistoricalDaySource
        ):
            return False
        owner = source.source_owner
        current_owner = self._observed_day_source_owner(key[0], key[2])
        return not (
            not isinstance(owner, tuple)
            or not isinstance(current_owner, tuple)
            or not owner
            or not current_owner
            or owner[0] is not current_owner[0]
            or owner[1:] != current_owner[1:]
        )

    def _verified_day_source_is_current(
        self, key: tuple[str, str, str, str], source: object
    ) -> bool:
        """Keep complete curves bound through Recorder waits and state saves."""
        return (
            isinstance(source, _HistoricalDaySource)
            and (self._historical_day_source_owner_is_current(key, source))
            and (self._verified_day_metric_is_current(key, source.revision))
        )

    def _historical_period_request_owner_matches(
        self, options: Mapping[str, Any], owner: object
    ) -> bool:
        """Reject stale snapshot IDs before attaching a current physical owner."""
        if not isinstance(owner, tuple) or self._observed_day_owner_key(owner) is None:
            return False
        parent = owner[1]
        if not isinstance(parent, tuple):
            return False
        if parent[0] is not None and str(parent[0]) != str(options["device_id"]):
            return False
        section = options["section_prefix"]
        if section in {APP_SECTION_PV_STAT, APP_SECTION_HOME_TRENDS} and (
            str(options.get("system_id") or "") != str(parent[2] or "")
        ):
            return False
        return section != APP_SECTION_CT_STAT or (
            owner[2] is not None
            and str(options.get("ct_device_id") or options["device_id"])
            == str(owner[2])
        )

    def _historical_period_source_is_current(
        self, key: tuple[str, str, str, str], source: object
    ) -> bool:
        """Fence each period import by physical owner and latest request generation."""
        return (
            isinstance(source, _HistoricalPeriodSource)
            and getattr(source, "request_key", None) == key
            and type(getattr(source, "revision", None)) is int
            and source.revision > 0
            and self._historical_day_source_owner_is_current(
                (key[0], key[3], key[1], ""), source
            )
            and getattr(self, "_http_period_source_revisions", {}).get(key)
            == source.revision
        )

    def _month_day_source_is_current(
        self, metric_key: tuple[str, str, str, str], source: object
    ) -> bool:
        """A month fallback must retain its own owner and the day-metric cutoff."""
        period_key = (
            metric_key[0],
            metric_key[2],
            DATE_TYPE_MONTH,
            date.fromisoformat(metric_key[1]).replace(day=1).isoformat(),
        )
        return (
            isinstance(source, _HistoricalPeriodSource)
            and (self._historical_period_source_is_current(period_key, source))
            and (
                type(getattr(source, "day_revision", None)) is int
                and source.day_revision >= 0
            )
            and (self._verified_day_metric_is_current(metric_key, source.day_revision))
        )

    def _statistics_backfill_should_stop(self) -> bool:
        """Return whether shutdown or the shared cycle deadline stops work."""
        deadline = getattr(self, "_statistics_backfill_deadline", None)
        retry_until = safe_float(getattr(self, "_statistics_rate_limit_until", None))
        return (
            bool(getattr(self, "_shutdown_started", False))
            or (isinstance(deadline, int | float) and time.monotonic() >= deadline)
            or (retry_until is not None and retry_until > time.time())
        )

    def _energy_statistic_targets(
        self,
        device_id: str,
        section_prefix: str,
        stat_key: str,
    ) -> list[tuple[str, str]]:
        """Return ``(statistic_id, reset_period)`` of sensors fed by one metric."""
        # Local import: descriptions import the entity layer, which imports
        # this module.
        from ..descriptions import STAT_DESCRIPTIONS  # ruff: ignore[import-outside-top-level]

        registry = er.async_get(self.hass)
        targets: list[tuple[str, str]] = []
        for description in STAT_DESCRIPTIONS:
            period = description.reset_period
            if period is None:
                continue
            primary = (
                description.stat_key == stat_key
                and description.section == f"{section_prefix}_{period}"
            )
            # A day sensor whose documented alternate source is this metric
            # needs the same history. Matching only the primary section left
            # "Netzbezug heute", "Hauslast heute" and the battery flow days
            # without any backfill and every home_trends day "unmapped".
            alternate = period == DATE_TYPE_DAY and any(
                fallback_key == stat_key
                and fallback_section in {section_prefix, f"{section_prefix}_{period}"}
                for fallback_section, fallback_key in description.fallback_sources
            )
            if not (primary or alternate):
                continue
            entity_id = registry.async_get_entity_id(
                "sensor", DOMAIN, f"{device_id}_{description.key}"
            )
            if entity_id is None and description.key == "today_feed_in_energy":
                entity_id = registry.async_get_entity_id(
                    "sensor", DOMAIN, f"{device_id}_today_solar_energy"
                )
            if entity_id is not None:
                targets.append((entity_id, period))
        return targets

    def _has_historical_day_recorder_target(
        self, device_id: str, section_prefix: str, metric: str
    ) -> bool:
        """Return whether one chart metric has a native day Recorder target."""
        if any(
            period == DATE_TYPE_DAY
            for _statistic_id, period in self._energy_statistic_targets(
                device_id, section_prefix, metric
            )
        ):
            return True
        chart_metric = next(
            (
                metric_key
                for section, stat_key, metric_key, _label in _app_chart_stat_metrics()
                if section == section_prefix and stat_key == metric
            ),
            None,
        )
        power_key = _POWER_SENSOR_BY_CHART_METRIC.get(chart_metric or "")
        return bool(
            power_key
            and er.async_get(self.hass).async_get_entity_id(
                "sensor", DOMAIN, f"{device_id}_{power_key}"
            )
        )

    def _http_day_backfill_days_state(
        self,
        device_id: str,
        section_prefix: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return normalized persistent state for one day source."""
        device_state = self._statistics_backfill_device_state(device_id)
        queue_state = device_state.setdefault("http_day_backfill", {})
        if not isinstance(queue_state, dict):
            queue_state = {}
            device_state["http_day_backfill"] = queue_state
        sources_state = queue_state.setdefault("sources", {})
        if not isinstance(sources_state, dict):
            sources_state = {}
            queue_state["sources"] = sources_state
        source_state = sources_state.setdefault(section_prefix, {})
        if not isinstance(source_state, dict):
            source_state = {}
            sources_state[section_prefix] = source_state
        days_state = source_state.setdefault("days", {})
        if not isinstance(days_state, dict):
            days_state = {}
            source_state["days"] = days_state
        if source_state.get("curve_rule") != _HTTP_DAY_CURVE_IMPORT_RULE:
            for day, status in days_state.items():
                if (
                    isinstance(status, dict)
                    and status.get("status") == "imported"
                    and status.get("imported_rows") == 0
                ):
                    days_state[day] = {"status": "pending"}
            source_state["curve_rule"] = _HTTP_DAY_CURVE_IMPORT_RULE
        if source_state.get("empty_rule") != _HTTP_DAY_EMPTY_IMPORT_RULE:
            for status in days_state.values():
                if isinstance(status, dict) and status.get("status") in {
                    "empty",
                    "unmapped",
                }:
                    status.pop("checked_date", None)
            source_state["empty_rule"] = _HTTP_DAY_EMPTY_IMPORT_RULE
        return source_state, days_state

    def _http_day_totals_verification_candidate(
        self,
        progress: _HttpDayBackfillProgress,
        candidate: _HttpDayBackfillCandidate,
        today: date,
        now_epoch: float,
    ) -> _HttpDayBackfillCandidate | None:
        """Select completed imports missing certified totals without reopening them."""
        state = candidate.day_state
        window_start = min(
            today.replace(month=1, day=1), today - timedelta(days=today.weekday())
        )
        if not window_start <= candidate.target_day < today:
            return None
        if state.get("status") != BackfillStatus.IMPORTED.value:
            return None
        expected = {
            stat_key
            for section, stat_key, _metric, _label in _app_chart_stat_metrics()
            if section == candidate.section_prefix
        }
        if not expected:
            return None
        totals = self._cached_verified_day_totals(
            state,
            candidate.section_prefix,
            owner_key=self._observed_day_owner_key(
                self._observed_day_source_owner(
                    candidate.device_id, candidate.section_prefix
                )
            ),
        )
        if expected.issubset(totals):
            return None
        try:
            attempted_at = datetime.fromisoformat(state["totals_last_attempt_at"])
            if (
                attempted_at.tzinfo is not None
                and now_epoch - attempted_at.timestamp()
                < _STATISTICS_HTTP_BACKFILL_INTERVAL_SEC
            ):
                return None
        except KeyError, TypeError, ValueError:
            pass
        progress.pending_sources += 1
        return candidate

    def _http_day_backfill_candidate(  # ruff: ignore[too-many-return-statements]
        self,
        progress: _HttpDayBackfillProgress,
        candidate: _HttpDayBackfillCandidate,
        *,
        week_start: date,
        today: date,
        now_epoch: float,
    ) -> _HttpDayBackfillCandidate | None:
        """Select missing days, retaining completed imports and startup progress."""
        day_state = candidate.day_state
        next_day = candidate.target_day + timedelta(days=1)
        ready_epoch = self._local_statistic_start(next_day).timestamp() + 2 * 3600
        if now_epoch < ready_epoch:
            return None
        if progress.verification_only:
            return self._http_day_totals_verification_candidate(
                progress, candidate, today, now_epoch
            )
        key = (
            candidate.device_id,
            candidate.section_prefix,
            candidate.target_day.isoformat(),
        )
        startup = getattr(self, "_statistics_startup_sync_pending", False)
        visited: set[tuple[str, str, str]] = getattr(
            self, "_statistics_startup_fetched", set()
        )
        status = _normalize_backfill_status(day_state.get("status"), closed=True)
        # Month-only day aggregates have no five-minute shape. Revisit
        # recent days for every energy family when an actual curve arrives,
        # without treating the original aggregate as a complete hourly trace.
        month_curve_revisit = (
            status is BackfillStatus.IMPORTED
            and day_state.get("source") == "month_chart"
            and 0 < (today - candidate.target_day).days <= _DAY_CURVE_RETRY_WINDOW_DAYS
        )
        if (
            startup
            and key in visited
            and not day_state.get("owner_verification_required")
            and not month_curve_revisit
        ):
            return None
        skip, changed, _ = self._restore_or_reopen_imported_day_totals(
            progress.verified_day_updates,
            state_status=status,
            day_state=day_state,
            device_id=candidate.device_id,
            target_day=candidate.target_day,
            section_prefix=candidate.section_prefix,
            week_start=week_start,
            today=today,
        )
        progress.state_changed |= changed
        if skip and day_state.get("owner_verification_required"):
            return self._http_day_totals_verification_candidate(
                progress, candidate, today, now_epoch
            )
        checked_today = (
            day_state.get("status") in {"empty", "unmapped"}
            and day_state.get("checked_date") == today.isoformat()
        )
        attempted_before_ready = False
        if checked_today:
            try:
                attempted_at = datetime.fromisoformat(day_state["last_attempt_at"])
                attempted_before_ready = (
                    attempted_at.tzinfo is not None
                    and attempted_at.timestamp() < ready_epoch
                )
            except KeyError, TypeError, ValueError:
                pass
        if month_curve_revisit:
            try:
                last_attempt = datetime.fromisoformat(day_state["last_attempt_at"])
                if (
                    last_attempt.tzinfo is not None
                    and now_epoch - last_attempt.timestamp()
                    < _STATISTICS_HTTP_BACKFILL_INTERVAL_SEC
                ):
                    return None
            except KeyError, TypeError, ValueError:
                pass
            skip = False
        if skip or (checked_today and not attempted_before_ready):
            return None
        day_state["status"] = BackfillStatus.PENDING.value
        progress.pending_sources += 1
        if (
            day_state.get("last_error")
            in {"recorder_pending", "partial_day_curve_pending"}
            and (safe_float(day_state.get(_STATISTICS_HTTP_RETRY_AFTER_EPOCH)) or 0)
            > now_epoch
        ):
            return None
        day_state.pop(_STATISTICS_HTTP_RETRY_AFTER_EPOCH, None)
        day_state.pop(_STATISTICS_HTTP_EMPTY_DEFERRALS, None)
        progress.state_changed = True
        return candidate

    def _collect_http_day_backfill_candidates(
        self,
        snapshot: dict[str, dict[str, Any]],
        target_days: list[date],
        *,
        today: date,
        now_epoch: float,
        progress: _HttpDayBackfillProgress,
    ) -> list[_HttpDayBackfillCandidate]:
        """Build the persistent day queue without issuing HTTP requests."""
        week_start = today - timedelta(days=today.weekday())
        candidates: list[_HttpDayBackfillCandidate] = []
        startup_seen: set[tuple[str, str, str]] = (
            getattr(self, "_statistics_startup_fetched", set())
            if getattr(self, "_statistics_startup_sync_pending", False)
            and not progress.verification_only
            else set()
        )
        for device_id in sorted(snapshot, key=str):
            payload = snapshot[device_id]
            for section_prefix in self._historical_day_source_prefixes(
                device_id,
                payload,
            ):
                _, days_state = self._http_day_backfill_days_state(
                    device_id,
                    section_prefix,
                )
                for target_day in target_days:
                    target_key = target_day.isoformat()
                    day_state = days_state.setdefault(target_key, {})
                    if not isinstance(day_state, dict):
                        day_state = {}
                        days_state[target_key] = day_state
                    # The candidate selector discards these same entries before
                    # any state changes. Avoid constructing thousands of
                    # temporary objects on each resumed startup pass.
                    if (
                        str(device_id),
                        section_prefix,
                        target_key,
                    ) in startup_seen and not day_state.get(
                        "owner_verification_required"
                    ):
                        continue
                    attempt_key = (
                        "totals_last_attempt_at"
                        if progress.verification_only
                        else "last_attempt_at"
                    )
                    last_attempt = str(day_state.get(attempt_key) or "")
                    candidate = self._http_day_backfill_candidate(
                        progress,
                        _HttpDayBackfillCandidate(
                            priority=(0 if week_start <= target_day < today else 1),
                            attempted=int(bool(last_attempt)),
                            last_attempt=last_attempt,
                            target_day=target_day,
                            attempts=0,
                            device_id=str(device_id),
                            section_prefix=section_prefix,
                            payload=payload,
                            day_state=day_state,
                            days_state=days_state,
                        ),
                        week_start=week_start,
                        today=today,
                        now_epoch=now_epoch,
                    )
                    if candidate is not None:
                        candidates.append(candidate)
        candidates.sort(
            key=lambda candidate: (
                # A persisted, unfinished Recorder metric must not starve
                # behind every newly discovered HTTP day/source each cycle.
                0 if candidate.day_state.get("pending_reconcile_metrics") else 1,
                candidate.attempted,
                candidate.last_attempt,
                candidate.priority,
                candidate.target_day.toordinal(),
                candidate.attempts,
                candidate.device_id,
                candidate.section_prefix,
            )
        )
        progress.actionable_sources = len(candidates)
        return candidates

    async def _async_verify_imported_day_totals(
        self,
        candidate: _HttpDayBackfillCandidate,
        progress: _HttpDayBackfillProgress,
        *,
        week_start: date,
        today: date,
    ) -> bool:
        """Recover certified totals without reimporting or resetting imported status."""
        progress.requests += 1
        status, source = await self._async_fetch_historical_day_chart_source(
            device_id=candidate.device_id,
            payload=candidate.payload,
            target_day=candidate.target_day,
            section_prefix=candidate.section_prefix,
        )
        state = candidate.day_state
        if status not in {"fetched", "empty_ambiguous"}:
            state["totals_last_attempt_at"] = utc_now().isoformat()
            progress.state_changed = True
            state["totals_last_error"] = status
            progress.rate_limited = status == "rate_limited"
            progress.stopped = status in {"rate_limited", "auth_error"}
            return progress.stopped
        totals = self._record_verified_day_totals_update(
            progress.verified_day_updates,
            device_id=candidate.device_id,
            target_day=candidate.target_day,
            section_prefix=candidate.section_prefix,
            source=source,
            day_state=state,
            week_start=week_start,
            today=today,
        )
        progress.state_changed = True
        if isinstance(source, _HistoricalDaySource) and source.stale_metrics:
            return False
        if (
            status == "fetched"
            and isinstance(source, _HistoricalDaySource)
            and self._local_statistic_start(
                candidate.target_day + timedelta(days=1)
            ).timestamp()
            - self._local_statistic_start(candidate.target_day).timestamp()
            == 86400  # ruff: ignore[magic-value-comparison]
        ):
            # Verification may revisit a day already marked imported through
            # a month total. Fill its independently verified power gaps without
            # recertifying or rewriting cumulative energy statistics.
            (
                _,
                imported,
            ) = await self._async_import_historical_day_chart_statistics_for_device(
                device_id=candidate.device_id,
                payload=candidate.payload,
                section_sources={candidate.section_prefix: source},
                target_day=candidate.target_day,
                recorder_state=state,
                power_only=True,
            )
            progress.gap_filled_rows += imported
        state["totals_last_attempt_at"] = utc_now().isoformat()
        state["totals_checked_date"] = today.isoformat()
        expected = {
            stat_key
            for section, stat_key, _metric, _label in _app_chart_stat_metrics()
            if section == candidate.section_prefix
        }
        missing_metrics = sorted(expected.difference(totals))
        state["totals_missing_metrics"] = missing_metrics
        state["totals_last_error"] = "incomplete_day_curve" if missing_metrics else None
        if totals:
            progress.source_days += 1
            progress.successful_devices.add(candidate.device_id)
        else:
            progress.unmapped_sources += 1
        progress.pending_sources -= 1
        progress.actionable_sources -= 1
        progress.terminal_transitions += 1
        await asyncio.sleep(0)
        return False

    def _mark_http_day_backfill_terminal(
        self,
        candidate: _HttpDayBackfillCandidate,
        progress: _HttpDayBackfillProgress,
    ) -> None:
        """Record one terminal source/day and advance the bounded queue accounting."""
        visited = getattr(self, "_statistics_startup_fetched", None)
        if visited is None:
            visited = self._statistics_startup_fetched = set()
        visited.add((
            candidate.device_id,
            candidate.section_prefix,
            candidate.target_day.isoformat(),
        ))
        progress.pending_sources -= 1
        progress.actionable_sources -= 1
        progress.terminal_transitions += 1

    async def _async_process_http_day_backfill_candidate(  # ruff: ignore[too-many-branches, too-many-locals, too-many-return-statements, too-many-statements, complex-structure]
        self,
        candidate: _HttpDayBackfillCandidate,
        progress: _HttpDayBackfillProgress,
        *,
        week_start: date,
        today: date,
    ) -> bool:
        """Fetch one day once and retain usable data independently of scalar totals."""
        state = candidate.day_state
        if state.get("owner_verification_required"):
            return await self._async_verify_imported_day_totals(
                candidate, progress, week_start=week_start, today=today
            )
        prior_intent = state.get("recorder_import_intent")
        if isinstance(prior_intent, list) and prior_intent:
            pending = set(state.get("pending_reconcile_metrics") or ())
            pending.update(prior_intent)
            state["pending_reconcile_metrics"] = sorted(pending)
            state["refresh_source_required"] = True
        cached_source = state.get("unimported_source")
        source: dict[str, Any]
        cached_revision = getattr(cached_source, "revision", 0)
        current_revisions = getattr(self, "_verified_day_metric_revisions", {})
        cached_is_stale = any(
            current_revisions.get(
                (
                    candidate.device_id,
                    candidate.target_day.isoformat(),
                    candidate.section_prefix,
                    stat_key,
                ),
                0,
            )
            > cached_revision
            for section, stat_key, _metric, _label in _app_chart_stat_metrics()
            if section == candidate.section_prefix
        )
        if (
            state.get("last_error") == "recorder_pending"  # ruff: ignore[too-many-boolean-expressions]
            and isinstance(cached_source, dict)
            and cached_source
            and not cached_is_stale
            and not state.get("refresh_source_required")
            and self._historical_day_source_owner_is_current(
                (
                    candidate.device_id,
                    candidate.target_day.isoformat(),
                    candidate.section_prefix,
                    "",
                ),
                cached_source,
            )
        ):
            source = cached_source
            status = "fetched"
        else:
            progress.requests += 1
            status, source = await self._async_fetch_historical_day_chart_source(
                device_id=candidate.device_id,
                payload=candidate.payload,
                target_day=candidate.target_day,
                section_prefix=candidate.section_prefix,
            )
        if status == "fetched":
            self._record_verified_day_totals_update(
                progress.verified_day_updates,
                device_id=candidate.device_id,
                target_day=candidate.target_day,
                section_prefix=candidate.section_prefix,
                source=source,
                day_state=state,
                week_start=week_start,
                today=today,
            )
            if isinstance(source, _HistoricalDaySource) and source.stale_metrics:
                progress.state_changed = True
                return False
        state.update({
            "last_attempt_at": utc_now().isoformat(),
            "status": BackfillStatus.PENDING.value,
        })
        state.pop("last_error", None)
        state.pop(_STATISTICS_HTTP_RETRY_AFTER_EPOCH, None)
        state.pop(_STATISTICS_HTTP_EMPTY_DEFERRALS, None)
        progress.state_changed = True
        if status not in {"fetched", "empty_ambiguous"}:
            state["last_error"] = status
            progress.rate_limited = status == "rate_limited"
            progress.stopped = status in {"rate_limited", "auth_error"}
            return progress.stopped
        imported = 0
        ok: bool | None = None
        reconciled_metrics: set[str] = set()
        if status == "fetched":
            progress.source_days += 1
            state["unimported_source"] = _clean_source_dict(source)
            state.pop("refresh_source_required", None)
            if isinstance(source, _HistoricalDaySource):
                intent = {
                    metric
                    for metric in source.accepted_metrics
                    if self._has_historical_day_recorder_target(
                        candidate.device_id, candidate.section_prefix, metric
                    )
                }
                intent.update(state.get("pending_reconcile_metrics") or ())
                if intent:
                    state["recorder_import_intent"] = sorted(intent)
                    await self._async_save_statistics_backfill_state()
            import_options: _HistoricalDayImportOptions = {}
            if state.get("pending_reconcile_metrics"):
                import_options["reconciled_metrics"] = reconciled_metrics
            if isinstance(source, _HistoricalDaySource):
                import_options["recorder_state"] = state
            (
                ok,
                imported,
            ) = await self._async_import_historical_day_chart_statistics_for_device(
                device_id=candidate.device_id,
                payload=candidate.payload,
                section_sources={candidate.section_prefix: source},
                target_day=candidate.target_day,
                **import_options,
            )
            progress.gap_filled_rows += imported
            # The fetched source stays in state for inspection and a later fill.
            if ok is False:
                state["last_error"] = "recorder_error"
                return False
        if ok is None:
            # Sparse five-minute observations matter for every energy
            # family, not just PV. Do not replace them with a fabricated
            # month-chart last-hour spike; keep the missing positions retryable.
            if status == "fetched" and any(
                isinstance(values, list)
                and any(safe_float(sample) is not None for sample in values)
                for key, values in source.items()
                if key == APP_CHART_SERIES_Y
                or (key.startswith(APP_CHART_SERIES_Y) and key[1:].isdigit())
            ):
                state["last_error"] = "partial_day_curve_pending"
                state["refresh_source_required"] = True
                state[_STATISTICS_HTTP_RETRY_AFTER_EPOCH] = (
                    time.time() + _STATISTICS_HTTP_BACKFILL_INTERVAL_SEC
                )
                return False
            # Only when no usable day samples exist at all may the month chart
            # provide its honest aggregate without inventing an hourly shape.
            if self._statistics_backfill_should_stop():
                progress.stopped = True
                return True
            before_month = progress.gap_filled_rows
            ok = await self._async_import_month_chart_day(candidate, progress)
            imported += progress.gap_filled_rows - before_month
            if progress.stopped:
                return True
            if state.get("last_error") == "transport_error":
                return False
            if ok:
                reconciled_metrics.update(candidate.month_metric_revisions)
        pending_metrics = set(state.get("pending_reconcile_metrics") or ())
        revisions = getattr(self, "_verified_day_metric_revisions", {})
        if isinstance(source, _HistoricalDaySource):
            for metric in source.accepted_metrics:
                key = (
                    candidate.device_id,
                    candidate.target_day.isoformat(),
                    candidate.section_prefix,
                    metric,
                )
                if revisions.get(key, 0) > source.revision and (
                    self._has_historical_day_recorder_target(
                        candidate.device_id, candidate.section_prefix, metric
                    )
                ):
                    pending_metrics.add(metric)
                    state["refresh_source_required"] = True
        for metric, cutoff in candidate.month_metric_revisions.items():
            key = (
                candidate.device_id,
                candidate.target_day.isoformat(),
                candidate.section_prefix,
                metric,
            )
            if revisions.get(key, 0) > cutoff:
                pending_metrics.add(metric)
                state["refresh_source_required"] = True
        if pending_metrics:
            if ok:
                pending_metrics.difference_update(
                    metric
                    for metric in reconciled_metrics
                    if revisions.get(
                        (
                            candidate.device_id,
                            candidate.target_day.isoformat(),
                            candidate.section_prefix,
                            metric,
                        ),
                        0,
                    )
                    <= candidate.month_metric_revisions.get(
                        metric, getattr(source, "revision", 0)
                    )
                )
            if pending_metrics:
                state["pending_reconcile_metrics"] = sorted(pending_metrics)
                state["recorder_import_intent"] = sorted(pending_metrics)
                state["status"] = BackfillStatus.PENDING.value
                state["last_error"] = "additional_metrics_pending"
                return False
            state.pop("pending_reconcile_metrics", None)
        state.pop("recorder_import_intent", None)
        state["status"] = (
            BackfillStatus.IMPORTED.value
            if ok
            else "unmapped"
            if status == "fetched"
            else "empty"
        )
        state["checked_date"] = today.isoformat()
        if state["status"] == "unmapped":
            state["last_error"] = "no_mapped_points"
            progress.unmapped_sources += 1
        else:
            state.pop("last_error", None)
        if ok:
            # A fully reconciled observed curve supersedes the legacy
            # single-hour month estimate; otherwise its source flag would
            # reopen the same day on every historical retry.
            if status == "fetched" and candidate.section_prefix == APP_SECTION_PV_STAT:
                state["source"] = "day_curve"
            state["imported_rows"] = imported
            state["completed_at"] = utc_now().isoformat()
            state.pop("unimported_source", None)
            progress.successful_devices.add(candidate.device_id)
        else:
            state.pop("completed_at", None)
            progress.empty_sources += int(state["status"] == "empty")
        self._mark_http_day_backfill_terminal(candidate, progress)
        await asyncio.sleep(0)
        return False

    async def _async_process_http_day_backfill_candidates(
        self,
        candidates: list[_HttpDayBackfillCandidate],
        progress: _HttpDayBackfillProgress,
        *,
        request_budget: int | None,
        week_start: date,
        today: date,
    ) -> None:
        """Process all selected days; honour explicit caller limits only."""
        process = (
            self._async_verify_imported_day_totals
            if progress.verification_only
            else self._async_process_http_day_backfill_candidate
        )
        for candidate in (
            candidates
            if request_budget is None
            else candidates[: max(0, request_budget)]
        ):
            if self._statistics_backfill_should_stop():
                break
            try:
                stopped = await process(
                    candidate,
                    progress,
                    week_start=week_start,
                    today=today,
                )
            except JackeryStatisticHistoryPendingError as err:
                attempt_key = (
                    "totals_last_attempt_at"
                    if progress.verification_only
                    else "last_attempt_at"
                )
                candidate.day_state.update({
                    attempt_key: utc_now().isoformat(),
                    "last_error": "recorder_pending",
                    "recorder_pending_reason": str(err),
                    _STATISTICS_HTTP_RETRY_AFTER_EPOCH: time.time()
                    + _STATISTICS_RECORDER_PENDING_RETRY_SECONDS,
                })
                progress.state_changed = True
                stopped = False
            if stopped:
                break
            if (
                getattr(self, "_statistics_startup_sync_pending", False)
                and not progress.verification_only
            ):
                key = (
                    candidate.device_id,
                    candidate.section_prefix,
                    candidate.target_day.isoformat(),
                )
                if key not in self._statistics_startup_fetched:
                    self._statistics_startup_fetched.add(key)
                    progress.actionable_sources -= 1

    async def _async_finalize_http_day_backfill(
        self,
        progress: _HttpDayBackfillProgress,
    ) -> dict[str, Any]:
        """Publish verified totals, persist state, and record day diagnostics."""
        target_days = progress.target_days
        if progress.verified_day_updates:
            self._publish_verified_day_updates(progress.verified_day_updates)
        if progress.state_changed:
            await self._async_save_statistics_backfill_state()
        backfill_had_source = progress.source_days > 0 and bool(
            progress.successful_devices
        )
        retry_after_sec = (
            _STATISTICS_HTTP_BACKFILL_INTERVAL_SEC
            if backfill_had_source
            else _STATISTICS_HTTP_BACKFILL_RETRY_SEC
        )
        self._last_statistics_http_backfill_monotonic = (
            progress.now_monotonic
            - _STATISTICS_HTTP_BACKFILL_INTERVAL_SEC
            + retry_after_sec
        )
        result: dict[str, Any] = {
            "external_rows": progress.external_rows,
            "entity_imported_rows": progress.gap_filled_rows,
            "entity_failed_rows": 0,
            "source_days": progress.source_days,
            "successful_devices": len(progress.successful_devices),
            "requests": progress.requests,
            "terminal_transitions": progress.terminal_transitions,
            "pending_sources": progress.pending_sources,
            "actionable_sources": progress.actionable_sources,
            "rate_limited": progress.rate_limited,
            "stopped": progress.stopped,
            "empty_sources": progress.empty_sources,
            "unmapped_sources": progress.unmapped_sources,
        }
        self._statistics_import_diagnostics.update({
            "last_http_backfill_checked_at": utc_now().isoformat(),
            "last_http_backfill_forced": progress.force,
            "last_http_backfill_window_days": progress.window_days,
            "last_http_backfill_include_current_year": (progress.include_current_year),
            "last_http_backfill_day_count": len(target_days),
            "last_http_backfill_oldest_day": (
                target_days[0].isoformat() if target_days else None
            ),
            "last_http_backfill_newest_day": (
                target_days[-1].isoformat() if target_days else None
            ),
            "last_http_backfill_status": (
                "unmapped"
                if progress.unmapped_sources
                else "empty"
                if progress.empty_sources
                else "completed"
                if progress.pending_sources == 0
                else ("progress" if progress.terminal_transitions else "pending")
            ),
            "last_http_backfill_external_rows": progress.external_rows,
            "last_http_backfill_entity_imported_rows": progress.gap_filled_rows,
            "last_http_backfill_entity_failed_rows": 0,
            "last_http_backfill_source_days": progress.source_days,
            "last_http_backfill_successful_device_count": len(
                progress.successful_devices
            ),
            "last_http_backfill_requests": progress.requests,
            "last_http_backfill_terminal_transitions": (progress.terminal_transitions),
            "last_http_backfill_pending_sources": progress.pending_sources,
            "last_http_backfill_actionable_sources": progress.actionable_sources,
            "last_http_backfill_rate_limited": progress.rate_limited,
            "next_http_backfill_allowed_in_seconds": retry_after_sec,
        })
        return result

    async def _async_http_backfill_recent_day_statistics(
        self,
        snapshot: dict[str, dict[str, Any]],
        **options: Unpack[_HttpDayBackfillOptions],
    ) -> dict[str, Any]:
        """Fill every selected missing historical day."""
        force = options.get("force", False)
        window_days = options.get("window_days", _STATISTICS_HTTP_BACKFILL_WINDOW_DAYS)
        include_current_year = options.get("include_current_year", False)
        request_budget = options.get("request_budget")
        verification_only = options.get("verification_only", False)
        diag = self._statistics_import_diagnostics
        now_monotonic = time.monotonic()
        since_last = now_monotonic - self._last_statistics_http_backfill_monotonic
        if not force and since_last < _STATISTICS_HTTP_BACKFILL_INTERVAL_SEC:
            diag["last_http_backfill_status"] = "throttled"
            diag["next_http_backfill_allowed_in_seconds"] = round(
                _STATISTICS_HTTP_BACKFILL_INTERVAL_SEC - since_last,
                3,
            )
            return {
                "external_rows": 0,
                "entity_imported_rows": 0,
                "entity_failed_rows": 0,
                "source_days": 0,
                "successful_devices": 0,
                "requests": 0,
                "terminal_transitions": 0,
                "pending_sources": 0,
            }

        await self._async_ensure_statistics_backfill_state_loaded()
        today = self._local_today()
        target_days = self._statistics_http_backfill_dates(
            today,
            window_days=window_days,
            include_current_year=include_current_year,
        )
        if include_current_year:
            first_day = self._statistics_history_start(snapshot, today)
            target_days = [
                first_day + timedelta(days=offset)
                for offset in range((today - first_day).days)
            ]
        progress = _HttpDayBackfillProgress(
            target_days=target_days,
            force=force,
            window_days=window_days,
            include_current_year=include_current_year,
            now_monotonic=now_monotonic,
            verification_only=verification_only,
        )
        candidates = self._collect_http_day_backfill_candidates(
            snapshot,
            target_days,
            today=today,
            now_epoch=time.time(),
            progress=progress,
        )
        week_start = today - timedelta(days=today.weekday())
        await self._async_process_http_day_backfill_candidates(
            candidates,
            progress,
            request_budget=request_budget,
            week_start=week_start,
            today=today,
        )

        return await self._async_finalize_http_day_backfill(progress)

    def _owner_day_requeue_candidates(
        self, snapshot: dict[str, dict[str, Any]]
    ) -> list[_HttpDayBackfillCandidate]:
        """Seed the existing owner's 24 pairs once and retain successful receipts."""
        payload = snapshot.get(_OWNER_DAY_REQUEUE_DEVICE_ID)
        if payload is None:
            return []
        jobs: list[_HttpDayBackfillCandidate] = []
        for section in (APP_SECTION_HOME_STAT, APP_SECTION_EPS_STAT):
            _, days = self._http_day_backfill_days_state(
                _OWNER_DAY_REQUEUE_DEVICE_ID, section
            )
            for target in _OWNER_DAY_REQUEUE_DATES:
                state = days.setdefault(target.isoformat(), {})
                if state.get("owner_requeue_rule") != _OWNER_DAY_REQUEUE_RULE:
                    state.update({
                        "owner_requeue_rule": _OWNER_DAY_REQUEUE_RULE,
                        "owner_requeue_complete": False,
                        "status": BackfillStatus.PENDING.value,
                    })
                jobs.append(
                    _HttpDayBackfillCandidate(
                        priority=0,
                        attempted=int(bool(state.get("owner_requeue_receipt"))),
                        last_attempt=str(state.get("last_attempt_at") or ""),
                        target_day=target,
                        attempts=0,
                        device_id=_OWNER_DAY_REQUEUE_DEVICE_ID,
                        section_prefix=section,
                        payload=payload,
                        day_state=state,
                        days_state=days,
                    )
                )
        return jobs

    async def _async_run_owner_day_requeue(
        self, snapshot: dict[str, dict[str, Any]]
    ) -> bool:
        """Advance four jobs serially through the existing historical import gate."""
        lock = getattr(self, "_owner_day_requeue_lock", None)
        if lock is None:
            lock = self._owner_day_requeue_lock = asyncio.Lock()
        async with lock:
            await self._async_ensure_statistics_backfill_state_loaded()
            jobs = self._owner_day_requeue_candidates(snapshot)
            open_jobs = [
                job for job in jobs if not job.day_state["owner_requeue_complete"]
            ]
            if not open_jobs:
                return False
            await self._async_save_statistics_backfill_state()
            today = self._local_today()
            progress = _HttpDayBackfillProgress(
                target_days=list(_OWNER_DAY_REQUEUE_DATES),
                force=True,
                window_days=0,
                include_current_year=True,
                now_monotonic=time.monotonic(),
            )
            now = time.time()
            ready_jobs = [
                job
                for job in open_jobs
                if (safe_float(job.day_state.get("owner_requeue_retry_after")) or 0.0)
                <= now
            ]
            for candidate in sorted(
                ready_jobs,
                key=lambda job: (job.attempted, job.last_attempt, job.target_day),
            )[:_OWNER_DAY_REQUEUE_BUDGET]:
                if self._statistics_backfill_should_stop():
                    break
                before_rows = progress.gap_filled_rows
                before_requests = progress.requests
                try:
                    stopped = await self._async_process_http_day_backfill_candidate(
                        candidate,
                        progress,
                        week_start=today - timedelta(days=today.weekday()),
                        today=today,
                    )
                except JackeryStatisticHistoryPendingError as err:
                    candidate.day_state.update({
                        "last_error": "recorder_pending",
                        "recorder_pending_reason": str(err),
                    })
                    stopped = False
                state = candidate.day_state
                state["owner_requeue_complete"] = state.get("status") == "imported"
                state["owner_requeue_receipt"] = {
                    "attempted_at": utc_now().isoformat(),
                    "source": candidate.section_prefix,
                    "day": candidate.target_day.isoformat(),
                    "outcome": state.get("status"),
                    "error": state.get("last_error"),
                    "rows": progress.gap_filled_rows - before_rows,
                    "requests": progress.requests - before_requests,
                }
                state["owner_requeue_retry_after"] = (
                    0
                    if state["owner_requeue_complete"]
                    else time.time() + _STATISTICS_HTTP_BACKFILL_RETRY_SEC
                )
                await self._async_save_statistics_backfill_state()
                if stopped:
                    break
            if progress.verified_day_updates:
                self._publish_verified_day_updates(progress.verified_day_updates)
            self._statistics_import_diagnostics["owner_day_requeue"] = {
                "rule": _OWNER_DAY_REQUEUE_RULE,
                "jobs": len(jobs),
                "completed": sum(
                    bool(job.day_state["owner_requeue_complete"]) for job in jobs
                ),
                "open": sum(
                    not job.day_state["owner_requeue_complete"] for job in jobs
                ),
                "requests": progress.requests,
                "stopped": progress.stopped,
            }
            return progress.requests > 0

    def _statistics_history_start(
        self,
        snapshot: dict[str, dict[str, Any]],
        today: date,
    ) -> date:
        """Include earlier device records and persisted history across year changes."""

        def has_source_observations(bucket: dict[str, Any]) -> bool:
            source = bucket.get("unimported_source")
            if not isinstance(source, dict):
                return False
            # An empty request receipt is not evidence of an installation date.
            # Preserve observed zero-valued series and nonzero aggregate history,
            # including records from before a later device re-registration.
            return any(
                isinstance(series := source.get(axis), list)
                and any(value is not None for value in series)
                for axis in ("y", "y1", "y2", "y3", "y4")
            ) or any(
                safe_float(value) not in {None, 0.0}
                for key, value in source.items()
                if key.startswith("total")
            )

        starts: list[date] = []
        for device_id, payload in snapshot.items():
            for metadata in (payload.get("device"), payload.get("system")):
                if not isinstance(metadata, dict):
                    continue
                try:
                    created = parse_utc_datetime(metadata.get("createTime", "")).date()
                except TypeError, ValueError, OverflowError, OSError:
                    continue
                if date(1970, 1, 1) < created <= today:
                    starts.append(created)
            state = self._statistics_backfill_device_state(device_id)
            for queue_name in ("http_day_backfill", "http_period_backfill"):
                queue = state.get(queue_name)
                if not isinstance(queue, dict):
                    continue
                sources = queue.get("sources")
                if not isinstance(sources, dict):
                    continue
                period_maps = (
                    periods
                    for source in sources.values()
                    if isinstance(source, dict)
                    for periods in source.values()
                    if isinstance(periods, dict)
                )
                for periods in period_maps:
                    recorded_dates = (
                        key
                        for key, bucket in periods.items()
                        if isinstance(bucket, dict)
                        and (
                            bucket.get("status") == "imported"
                            or bucket.get("pending_reconcile_metrics")
                            or has_source_observations(bucket)
                        )
                    )
                    for key in recorded_dates:
                        try:
                            recorded = date.fromisoformat(key)
                        except TypeError, ValueError:
                            continue
                        if date(1970, 1, 1) < recorded <= today:
                            starts.append(recorded)
        return min(starts, default=today.replace(month=1, day=1))

    def _http_period_backfill_plan(
        self,
        today: date,
        *,
        from_date: date | None = None,
    ) -> tuple[tuple[str, list[date]], ...]:
        """Return enabled period ranges in their queue-priority order."""
        from_date = from_date or date(today.year, 1, 1)
        enabled = self._enabled_app_chart_date_types()
        return tuple(
            item
            for item in (
                (DATE_TYPE_MONTH, self._iter_calendar_months(from_date, today)),
                (
                    DATE_TYPE_YEAR,
                    [
                        date(year, 1, 1)
                        for year in self._iter_calendar_years(from_date, today)
                    ],
                ),
                (DATE_TYPE_WEEK, self._iter_calendar_weeks(from_date, today)),
            )
            if item[0] in enabled
        )

    def _http_period_backfill_sources_state(
        self,
        device_id: str,
    ) -> dict[str, Any]:
        """Return normalized persistent period-source state for one device."""
        device_state = self._statistics_backfill_device_state(device_id)
        queue_state = device_state.setdefault("http_period_backfill", {})
        if not isinstance(queue_state, dict):
            queue_state = {}
            device_state["http_period_backfill"] = queue_state
        sources_state = queue_state.setdefault("sources", {})
        if not isinstance(sources_state, dict):
            sources_state = {}
            queue_state["sources"] = sources_state
        return sources_state

    def _http_period_backfill_candidate(  # ruff: ignore[too-many-return-statements]
        self,
        progress: _HttpPeriodBackfillProgress,
        candidate: _HttpPeriodBackfillCandidate,
        *,
        today: date,
        now_epoch: float,
    ) -> _HttpPeriodBackfillCandidate | None:
        """Select missing closed periods without resetting completed neighbours."""
        bucket_state = candidate.bucket_state
        closed = _backfill_period_is_closed(
            target_day=candidate.period_start,
            today=today,
            date_type=candidate.date_type,
        )
        was_open = bucket_state.get("period_open")
        if not closed:
            if was_open is not True:
                bucket_state["period_open"] = True
                progress.state_changed = True
            progress.open_sources += 1
            return None
        key = (
            candidate.device_id,
            f"{candidate.section_prefix}:{candidate.date_type}",
            candidate.period_start.isoformat(),
        )
        if self._period_receipt_owner_changed(candidate, bucket_state):
            progress.state_changed = True
        startup = getattr(self, "_statistics_startup_sync_pending", False)
        if startup and key in getattr(self, "_statistics_startup_fetched", set()):
            return None
        status = _normalize_backfill_status(bucket_state.get("status"), closed=True)
        if (
            status is BackfillStatus.IMPORTED
            and bucket_state.get("source_owner") is None
            and was_open is not True
        ):
            # Preserve a legacy import receipt without assigning today's owner
            # to its historical rows or replaying the source after a restart.
            return None
        if not startup and was_open is not True:
            if status is BackfillStatus.IMPORTED:
                return None
            if (
                bucket_state.get("status") in {"empty", "unmapped"}
                and bucket_state.get("checked_date") == today.isoformat()
            ):
                return None
        bucket_state["status"] = BackfillStatus.PENDING.value
        bucket_state["period_open"] = False
        progress.pending_sources += 1
        if bucket_state.get("recovery_requires_resolution") is True:
            if key in getattr(self, "_period_backfill_hold_save_pending", set()):
                progress.state_changed = True
            return None
        if (
            bucket_state.get("last_error") == "recorder_pending"
            and (safe_float(bucket_state.get(_STATISTICS_HTTP_RETRY_AFTER_EPOCH)) or 0)
            > now_epoch
        ):
            return None
        bucket_state.pop(_STATISTICS_HTTP_RETRY_AFTER_EPOCH, None)
        bucket_state.pop(_STATISTICS_HTTP_EMPTY_DEFERRALS, None)
        progress.state_changed = True
        return candidate

    def _period_receipt_owner_changed(
        self, candidate: _HttpPeriodBackfillCandidate, state: dict[str, Any]
    ) -> bool:
        """Quarantine known old-owner receipts without replaying or deleting history."""
        previous = state.get("source_owner")
        current = self._observed_day_owner_key(
            self._observed_day_source_owner(
                candidate.device_id, candidate.section_prefix
            )
        )
        recorded = (
            state.get("status") == BackfillStatus.IMPORTED.value
            or state.get("imported_rows")
            or state.get("last_attempt_imported_rows")
            or state.get("completed_at")
        )
        if previous is None or current is None or previous == current or not recorded:
            return False
        state["recovery_requires_resolution"] = True
        state["last_error"] = "physical_source_changed"
        state["status"] = BackfillStatus.PENDING.value
        return True

    def _collect_http_period_backfill_candidates(
        self,
        snapshot: dict[str, dict[str, Any]],
        period_plan: tuple[tuple[str, list[date]], ...],
        *,
        today: date,
        now_epoch: float,
        progress: _HttpPeriodBackfillProgress,
    ) -> list[_HttpPeriodBackfillCandidate]:
        """Build the persistent closed-period queue without issuing HTTP."""
        priorities = {
            DATE_TYPE_MONTH: 0,
            DATE_TYPE_YEAR: 1,
            DATE_TYPE_WEEK: 2,
        }
        prefixes = tuple(
            dict.fromkeys(metric[0] for metric in _app_chart_stat_metrics())
        )
        candidates: list[_HttpPeriodBackfillCandidate] = []
        for device_id in sorted(snapshot, key=str):
            payload = snapshot[device_id]
            sources_state = self._http_period_backfill_sources_state(device_id)
            for section_prefix in prefixes:
                source_state = sources_state.setdefault(section_prefix, {})
                if not isinstance(source_state, dict):
                    source_state = {}
                    sources_state[section_prefix] = source_state
                for date_type, period_starts in period_plan:
                    type_state = source_state.setdefault(date_type, {})
                    if not isinstance(type_state, dict):
                        type_state = {}
                        source_state[date_type] = type_state
                    for period_start in period_starts:
                        period_key = period_start.isoformat()
                        bucket_state = type_state.setdefault(period_key, {})
                        if not isinstance(bucket_state, dict):
                            bucket_state = {}
                            type_state[period_key] = bucket_state
                        candidate = self._http_period_backfill_candidate(
                            progress,
                            _HttpPeriodBackfillCandidate(
                                priority=priorities[date_type],
                                attempted=int(
                                    bool(bucket_state.get("last_attempt_at"))
                                ),
                                last_attempt=str(
                                    bucket_state.get("last_attempt_at") or ""
                                ),
                                period_start=period_start,
                                device_id=str(device_id),
                                section_prefix=section_prefix,
                                date_type=date_type,
                                payload=payload,
                                bucket_state=bucket_state,
                                type_state=type_state,
                            ),
                            today=today,
                            now_epoch=now_epoch,
                        )
                        if candidate is not None:
                            candidates.append(candidate)
        candidates.sort(
            key=lambda candidate: (
                candidate.attempted,
                candidate.last_attempt,
                candidate.priority,
                candidate.period_start.toordinal(),
                candidate.device_id,
                candidate.section_prefix,
                candidate.date_type,
            )
        )
        progress.actionable_sources = len(candidates)
        _LOGGER.debug(
            "Period backfill candidates before budget slice: %d total",
            len(candidates),
        )
        return candidates

    async def _async_fetch_http_period_backfill_candidate(
        self,
        candidate: _HttpPeriodBackfillCandidate,
    ) -> tuple[str, dict[str, Any]]:
        """Fetch one period source while isolating supplementary failures."""
        status = "empty_ambiguous"
        error: Exception | None = None
        try:
            fetched = await self._async_fetch_historical_app_chart_source(
                device_id=candidate.device_id,
                system_id=self._system_id_from_payload(
                    candidate.device_id,
                    candidate.payload,
                ),
                ct_device_id=(
                    self._smart_meter_accessory_device_id(candidate.payload)
                    or self._smart_meter_accessory_device_id(
                        getattr(self, "_device_index", {}).get(candidate.device_id)
                        or {}
                    )
                ),
                section_prefix=candidate.section_prefix,
                date_type=candidate.date_type,
                period_start=candidate.period_start,
            )
        except JackeryStatisticHistoryPendingError as err:
            status = "stale_owner"
            error = err
        except JackeryAuthError as err:
            status = "auth_error"
            error = err
        except (TimeoutError, HomeAssistantError, JackeryError) as err:
            status = "rate_limited" if _is_system_busy_error(err) else "transport_error"
            error = err
            if status == "rate_limited":
                self._set_statistics_rate_limit(err)
        except Exception as err:  # ruff: ignore[blind-except]  # Dynamic endpoint failures remain retryable per bucket.
            status = "transport_error"
            error = err
        else:
            if isinstance(fetched, dict) and fetched:
                return "fetched", fetched
            return "empty_ambiguous", {}
        if error is not None:
            _LOGGER.debug(
                "Jackery period backfill %s for %s %s %s: %s",
                status,
                candidate.device_id,
                candidate.section_prefix,
                candidate.period_start.isoformat(),
                exception_debug_message(error),
            )
        return status, {}

    async def _async_process_http_period_backfill_candidate(  # ruff: ignore[too-many-statements]  # ruff: ignore[too-many-return-statements, too-many-branches, too-many-locals]
        self,
        candidate: _HttpPeriodBackfillCandidate,
        progress: _HttpPeriodBackfillProgress,
        *,
        today: date,
    ) -> bool:
        """Fetch and import one closed period; return whether the queue must stop."""
        state = candidate.bucket_state
        if self._period_receipt_owner_changed(candidate, state):
            progress.state_changed = True
            return False
        source_key = (
            candidate.device_id,
            candidate.section_prefix,
            candidate.date_type,
            candidate.period_start.isoformat(),
        )
        cached_source = state.get("unimported_source")
        live_sources = getattr(self, "_http_period_pending_sources", None)
        if live_sources is None:
            self._http_period_pending_sources: dict[
                tuple[str, str, str, str], _HistoricalPeriodSource
            ] = {}
            live_sources = self._http_period_pending_sources
        live_source = live_sources.get(source_key)
        refetched = False
        resume_pending = (
            state.get("last_error") == "recorder_pending"
            and isinstance(cached_source, dict)
            and bool(cached_source)
        )
        status: str
        source: dict[str, Any]
        if (
            resume_pending
            and isinstance(live_source, _HistoricalPeriodSource)
            and self._historical_period_source_is_current(source_key, live_source)
            and dict(live_source) == cached_source
        ):
            status, source = "fetched", live_source
        else:
            live_sources.pop(source_key, None)
            refetched = True
            progress.requests += 1
            status, source = await self._async_fetch_http_period_backfill_candidate(
                candidate
            )
        state.update({
            "attempts": candidate.attempted + 1,
            "last_attempt_at": utc_now().isoformat(),
            "status": "pending",
            "period_open": False,
        })
        progress.state_changed = True
        if (
            status == "empty_ambiguous"
            and isinstance(cached_source, dict)
            and cached_source
        ):
            return False
        if status not in {"fetched", "empty_ambiguous"}:
            state["last_error"] = status
            progress.rate_limited = status == "rate_limited"
            progress.stopped = status in {"rate_limited", "auth_error"}
            return progress.stopped
        if (
            refetched
            and status == "fetched"
            and isinstance(cached_source, dict)
            and cached_source
            and not (
                isinstance(source, _HistoricalPeriodSource)
                and state.get("source_owner")
                == self._observed_day_owner_key(source.source_owner)
                and state.get("source_request_key") == list(source_key)
                and dict(source) == cached_source
            )
        ):
            state["unimported_refetch_source"] = _clean_source_dict(source)
            if isinstance(source, _HistoricalPeriodSource):
                state["unimported_refetch_owner"] = self._observed_day_owner_key(
                    source.source_owner
                )
                state["unimported_refetch_request_key"] = list(source.request_key)
            state["recovery_requires_resolution"] = True
            state["last_error"] = "recorder_reconciliation_required"
            pending: set[tuple[str, str, str]] = getattr(
                self, "_period_backfill_hold_save_pending", set()
            )
            self._period_backfill_hold_save_pending = pending
            hold_key = (
                candidate.device_id,
                f"{candidate.section_prefix}:{candidate.date_type}",
                candidate.period_start.isoformat(),
            )
            pending.add(hold_key)
            await self._async_save_statistics_backfill_state()
            pending.discard(hold_key)
            return False
        repaired = failed = reconciled = 0
        pending_targets: dict[str, str] = {}
        if status == "fetched":
            if not isinstance(
                source, _HistoricalPeriodSource
            ) or not self._historical_period_source_is_current(source_key, source):
                state["last_error"] = "stale_owner"
                return False
            live_sources[source_key] = source
            state["source_owner"] = self._observed_day_owner_key(source.source_owner)
            state["source_request_key"] = list(source.request_key)
            state["unimported_source"] = _clean_source_dict(source)
            await self._async_save_statistics_backfill_state()
            period_meta = self._app_chart_period_meta(candidate.date_type)
            try:
                (
                    repaired,
                    failed,
                    reconciled,
                    pending_targets,
                ) = await self._import_collected_repair_buckets(
                    device_id=candidate.device_id,
                    name_prefix=candidate.device_id,
                    collected={
                        (
                            candidate.section_prefix,
                            candidate.date_type,
                            candidate.period_start,
                        ): source
                    },
                    period_meta_by_type=(
                        {candidate.date_type: period_meta}
                        if period_meta is not None
                        else {}
                    ),
                    to_date=today,
                )
            except JackeryStatisticHistoryPendingError as err:
                pending_targets["source"] = str(err)
            if not self._historical_period_source_is_current(source_key, source):
                pending_targets["source"] = (
                    "Historical period source changed during import"
                )
        if pending_targets:
            state.update({
                "last_error": "recorder_pending",
                "recorder_pending_targets": pending_targets,
                "unimported_source": _clean_source_dict(source),
                "last_attempt_imported_rows": repaired,
                "last_attempt_reconciled_target_days": reconciled,
                "last_attempt_failed_targets": failed,
                _STATISTICS_HTTP_RETRY_AFTER_EPOCH: time.time()
                + _STATISTICS_RECORDER_PENDING_RETRY_SECONDS,
            })
            return False
        state.pop("recorder_pending_targets", None)
        if failed:
            state.update({
                "last_error": "recorder_error",
                "unimported_source": _clean_source_dict(source),
            })
            return False
        state["status"] = (
            "imported" if reconciled else "unmapped" if status == "fetched" else "empty"
        )
        state["checked_date"] = today.isoformat()
        if reconciled:
            state.update({
                "imported_rows": repaired,
                "reconciled_target_days": reconciled,
                "completed_at": utc_now().isoformat(),
            })
            state.pop("last_error", None)
            state.pop("unimported_source", None)
            live_sources.pop(source_key, None)
            progress.imported_sources += 1
        elif state["status"] == "unmapped":
            state.update({
                "last_error": "no_mapped_points",
                "unimported_source": _clean_source_dict(source),
            })
            progress.unmapped_sources += 1
        else:
            state.pop("last_error", None)
            live_sources.pop(source_key, None)
            progress.empty_sources += 1
        visited = getattr(self, "_statistics_startup_fetched", None)
        if visited is None:
            visited = self._statistics_startup_fetched = set()
        visited.add((
            candidate.device_id,
            f"{candidate.section_prefix}:{candidate.date_type}",
            candidate.period_start.isoformat(),
        ))
        progress.pending_sources -= 1
        progress.actionable_sources -= 1
        progress.terminal_transitions += 1
        await asyncio.sleep(0)
        return False

    async def _async_process_http_period_backfill_candidates(
        self,
        candidates: list[_HttpPeriodBackfillCandidate],
        progress: _HttpPeriodBackfillProgress,
        *,
        request_budget: int | None,
        today: date,
    ) -> None:
        """Process all selected periods; honour explicit caller limits only."""
        for candidate in (
            candidates
            if request_budget is None
            else candidates[: max(0, request_budget)]
        ):
            if self._statistics_backfill_should_stop():
                break
            if await self._async_process_http_period_backfill_candidate(
                candidate,
                progress,
                today=today,
            ):
                break
            if getattr(self, "_statistics_startup_sync_pending", False):
                key = (
                    candidate.device_id,
                    f"{candidate.section_prefix}:{candidate.date_type}",
                    candidate.period_start.isoformat(),
                )
                if key not in self._statistics_startup_fetched:
                    self._statistics_startup_fetched.add(key)
                    progress.actionable_sources -= 1

    async def _async_finalize_http_period_backfill(
        self,
        progress: _HttpPeriodBackfillProgress,
    ) -> dict[str, int]:
        """Persist queue state and record period-backfill diagnostics."""
        if progress.state_changed:
            pending: set[tuple[str, str, str]] = getattr(
                self, "_period_backfill_hold_save_pending", set()
            )
            saved_holds = set(pending)
            await self._async_save_statistics_backfill_state()
            pending.difference_update(saved_holds)
        self._statistics_import_diagnostics.update({
            "last_period_backfill_requests": progress.requests,
            "last_period_backfill_imported_sources": progress.imported_sources,
            "last_period_backfill_terminal_transitions": (
                progress.terminal_transitions
            ),
            "last_period_backfill_pending_sources": progress.pending_sources,
            "last_period_backfill_actionable_sources": progress.actionable_sources,
            "last_period_backfill_open_sources": progress.open_sources,
            "last_period_backfill_rate_limited": progress.rate_limited,
            "last_period_backfill_empty_sources": progress.empty_sources,
            "last_period_backfill_unmapped_sources": progress.unmapped_sources,
        })
        return {
            "requests": progress.requests,
            "imported_sources": progress.imported_sources,
            "terminal_transitions": progress.terminal_transitions,
            "pending_sources": progress.pending_sources,
            "actionable_sources": progress.actionable_sources,
            "open_sources": progress.open_sources,
            "rate_limited": progress.rate_limited,
            "stopped": progress.stopped,
            "empty_sources": progress.empty_sources,
            "unmapped_sources": progress.unmapped_sources,
        }

    async def _async_http_backfill_period_statistics(
        self,
        snapshot: dict[str, dict[str, Any]],
        *,
        request_budget: int | None = None,
    ) -> dict[str, int]:
        """Advance persistent week/month/year HTTP history independently.

        Each closed source/period bucket has its own terminal state. Open
        week/month/year buckets are already fetched by the normal HTTP cycle
        and imported from its snapshot; fetching them here as well duplicates
        cloud requests without adding data. Month and year lead the historical
        queue so an empty weekly endpoint cannot block other closed buckets.
        """
        _LOGGER.debug(
            "Period backfill START: snapshot_keys=%s budget=%s",
            list(snapshot.keys()),
            request_budget,
        )
        await self._async_ensure_statistics_backfill_state_loaded()
        today = self._local_today()
        progress = _HttpPeriodBackfillProgress()
        candidates = self._collect_http_period_backfill_candidates(
            snapshot,
            self._http_period_backfill_plan(
                today,
                from_date=self._statistics_history_start(snapshot, today),
            ),
            today=today,
            now_epoch=time.time(),
            progress=progress,
        )

        await self._async_process_http_period_backfill_candidates(
            candidates,
            progress,
            request_budget=request_budget,
            today=today,
        )

        return await self._async_finalize_http_period_backfill(progress)
