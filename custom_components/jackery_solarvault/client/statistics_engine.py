"""Statistics engine and app chart series generation mixin."""

import asyncio
import copy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from functools import partial
import logging
import time
from typing import TYPE_CHECKING, Any, Final, TypedDict, Unpack

from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.util import dt as dt_util

from ..client import JackeryError
from ..const import (
    APP_DEVICE_STAT_BATTERY_CHARGE,
    APP_DEVICE_STAT_BATTERY_DISCHARGE,
    APP_DEVICE_STAT_BATTERY_TO_AC,
    APP_DEVICE_STAT_BATTERY_TO_GRID,
    APP_DEVICE_STAT_ONGRID_INPUT,
    APP_DEVICE_STAT_ONGRID_OUTPUT,
    APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    APP_DEVICE_STAT_PV_ENERGY,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_TOTAL_CT_INPUT_ENERGY,
    APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_UNIT,
    CT_STAT_TYPE_L1,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DOMAIN,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_ID,
    FIELD_MODEL_CODE,
    FIELD_SYSTEM_ID,
    PAYLOAD_DEVICE_META,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_SYSTEM_META,
)
from ..ingest import local_period_total_supersedes_cloud
from ..util import (
    app_chart_name_prefix,
    app_chart_period_meta,
    app_energy_unit_scale,
    app_month_request_kwargs,
    app_period_request_kwargs,
    app_year_request_kwargs,
    dated_daily_chart_values,
    day_power_energy_points,
    day_power_series_key,
    effective_period_total_value,
    iter_calendar_months,
    iter_calendar_weeks,
    iter_calendar_years,
    pv_channel_scalar_is_lifetime_offset,
    request_date,
    safe_float,
    stat_row_start,
    supplement_day_curve_samples,
    trend_series_points,
)
from .api import DevicePeriodQuery
from .recorder_reconcile import (
    BackfillStatus,
    JackeryStatisticHistoryPendingError,
    _HistoricalPeriodSource,
    _HttpDayBackfillCandidate,
    _HttpDayBackfillProgress,
    _VerifiedDayUpdates,
    _app_chart_stat_metrics,
    _coordinator_recorder_attr,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping


_LOGGER = logging.getLogger(__name__)


def exception_debug_message(err: BaseException) -> str:
    """Return a useful debug message for exceptions with empty str(err)."""
    return f"{type(err).__name__}: {err or "(no message)"}"


_STATISTICS_IMPORT_THROTTLE_SEC = 15
_STATISTICS_IMPORT_MAX_SUM_KWH = 1_000_000_000.0
_STATISTICS_BACKFILL_STORE_DEVICES = "devices"
_STATISTICS_HTTP_CYCLE_REQUEST_BUDGET = 12
_STATISTICS_HTTP_CYCLE_TIME_BUDGET_SEC = 20.0
_STATISTICS_HTTP_BACKFILL_REQUEST_BUDGET = 12
# Recovery has its own retry policy; unrelated slow-source TTLs must not delay it.
_STATISTICS_HTTP_BACKFILL_INTERVAL_SEC = 15
_PV_MONTH_RECOVERY_RETRY_INITIAL_SEC = 60.0
_PV_MONTH_RECOVERY_RETRY_MAX_SEC = 1800.0
_STATISTICS_IMPORT_STATE_TOLERANCE = 1e-4

_DAY_TREND_SOURCE_BY_METRIC_KEY: Final[dict[str, tuple[str, str]]] = {
    "home_energy": (PAYLOAD_HOME_TRENDS, APP_STAT_TOTAL_HOME_ENERGY),
}

_LOCAL_DAILY_METRIC_BY_CHART_METRIC_KEY: Final[dict[str, str]] = {
    "pv_energy": APP_DEVICE_STAT_PV_ENERGY,
    "pv1_energy": APP_STAT_PV1_ENERGY,
    "pv2_energy": APP_STAT_PV2_ENERGY,
    "pv3_energy": APP_STAT_PV3_ENERGY,
    "pv4_energy": APP_STAT_PV4_ENERGY,
    "device_ongrid_input_energy": APP_DEVICE_STAT_ONGRID_INPUT,
    "device_ongrid_output_energy": APP_DEVICE_STAT_ONGRID_OUTPUT,
    "battery_charge_energy": APP_DEVICE_STAT_BATTERY_CHARGE,
    "battery_discharge_energy": APP_DEVICE_STAT_BATTERY_DISCHARGE,
    "grid_to_battery_energy": APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    "battery_to_ac_energy": APP_DEVICE_STAT_BATTERY_TO_AC,
    "battery_to_grid_energy": APP_DEVICE_STAT_BATTERY_TO_GRID,
    "ct_input_energy": FIELD_CT_TOTAL_PHASE_ENERGY,
    "ct_output_energy": FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    "eps_input_energy": "eps_input_energy",
    "eps_output_energy": "eps_output_energy",
}


class _DayChartPointsRequired(TypedDict):
    device_id: str
    payload: dict[str, Any]
    section_prefix: str
    stat_key: str
    metric_key: str
    bucket_minutes: int
    now: datetime


class _DayChartPointsOptions(_DayChartPointsRequired, total=False):
    use_local_day_guard: bool
    require_complete_day: bool
    require_complete_bucket: bool
    native_power: bool
    use_scalar_total: bool
    target_day: date | None
    recorder_guard: Callable[[], bool] | None
    recorder_state: dict[str, Any] | None


class _HistoricalAppChartFetchRequired(TypedDict):
    device_id: str
    system_id: str | None
    section_prefix: str
    date_type: str
    period_start: date


class _HistoricalAppChartFetchOptions(
    _HistoricalAppChartFetchRequired,
    total=False,
):
    ct_device_id: str | None


RECORDER_BACKGROUND_TASK_ERRORS: tuple[type[BaseException], ...] = (
    HomeAssistantError,
    JackeryError,
    TimeoutError,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
    LookupError,
    asyncio.TimeoutError,
)

BACKGROUND_TASK_ERRORS: Final = RECORDER_BACKGROUND_TASK_ERRORS


if TYPE_CHECKING:
    from collections.abc import Coroutine
    from datetime import tzinfo

    from homeassistant.core import HomeAssistant

    from .api import JackeryApi
    from .recorder_reconcile import _HttpDayBackfillOptions, _VerifiedDayWindow
    from .storage import JackeryStore as Store


class JackeryStatisticsEngineMixin:
    """Mixin for statistics calculation, app chart generation, and offset handling."""

    if TYPE_CHECKING:
        _last_stat_import_monotonic: float

        _shutdown_started: bool

        _slow_metrics_bg_task: asyncio.Task[None] | None

        _stat_import_last_sig: dict[str, str]

        _statistics_backfill_deadline: float

        _statistics_backfill_state: dict[str, Any]

        _statistics_backfill_state_loaded: bool

        _statistics_backfill_store: Store[dict[str, Any]]

        _statistics_backfill_store_lock: asyncio.Lock

        _statistics_backfill_task: asyncio.Task[None] | None

        _statistics_import_diagnostics: dict[str, Any]

        _statistics_import_ready: bool

        _statistics_rate_limit_until: float | None

        _statistics_recorder_lock: asyncio.Lock

        _statistics_startup_fetched: set[tuple[str, str, str]]

        _statistics_startup_sync_pending: bool

        api: JackeryApi

        hass: HomeAssistant

        data: dict[str, dict[str, Any]]

        _observed_day_metric_revisions: dict[tuple[str, str, str, str], int]

        def _invalidate_observed_day_source_bindings(
            self, *, notify: bool = True
        ) -> bool:
            raise NotImplementedError

        def _observed_day_source_owner(
            self,
            device_id: str,
            section_prefix: str,
            payload: dict[str, Any] | None = None,
        ) -> tuple[object, ...] | None:
            raise NotImplementedError

        @staticmethod
        def _observed_day_owner_key(owner: object) -> list[object] | None:
            raise NotImplementedError

        @staticmethod
        def _cached_observed_day_totals(
            day_state: dict[str, Any],
            section_prefix: str,
        ) -> dict[str, dict[str, Any]]:
            raise NotImplementedError

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
            raise NotImplementedError

        def _record_observed_day_totals_update(
            self,
            updates: _VerifiedDayUpdates,
            *,
            source: dict[str, Any],
            day_state: dict[str, Any],
            **window: Unpack[_VerifiedDayWindow],
        ) -> bool:
            raise NotImplementedError

        def _publish_observed_day_updates(self, updates: _VerifiedDayUpdates) -> None:
            raise NotImplementedError

        async def _async_http_backfill_period_statistics(
            self,
            snapshot: dict[str, dict[str, Any]],
            *,
            request_budget: int | None = None,
        ) -> dict[str, int]:
            raise NotImplementedError

        async def _async_http_backfill_recent_day_statistics(
            self,
            snapshot: dict[str, dict[str, Any]],
            **options: Unpack[_HttpDayBackfillOptions],
        ) -> dict[str, Any]:
            raise NotImplementedError

        async def _async_reconcile_statistic_day(  # ruff: ignore[too-many-arguments]
            self,
            statistic_id: str,
            hours: list[float],
            hourly_energy: dict[float, float],
            *,
            reset_period: str = DATE_TYPE_DAY,
            aggregate_only: bool = False,
            recorder_guard: Callable[[], bool] | None = None,
            recorder_state: dict[str, Any] | None = None,
        ) -> int:
            raise NotImplementedError

        async def _async_run_owner_day_requeue(
            self, snapshot: dict[str, dict[str, Any]]
        ) -> bool:
            raise NotImplementedError

        async def _async_slow_http_call[Result](
            self, call: Callable[[], Awaitable[Result]]
        ) -> Result:
            raise NotImplementedError

        async def _async_verify_imported_day_totals(
            self,
            candidate: _HttpDayBackfillCandidate,
            progress: _HttpDayBackfillProgress,
            *,
            week_start: date,
            today: date,
        ) -> bool:
            raise NotImplementedError

        @staticmethod
        def _cached_verified_day_totals(
            day_state: dict[str, Any],
            section_prefix: str,
            *,
            owner_key: list[object] | None = None,
        ) -> dict[str, float]:
            raise NotImplementedError

        def _historical_period_request_owner_matches(
            self, options: Mapping[str, Any], owner: object
        ) -> bool:
            raise NotImplementedError

        def _historical_period_source_is_current(
            self, key: tuple[str, str, str, str], source: object
        ) -> bool:
            raise NotImplementedError

        def _create_entry_background_task(
            self, operation: Coroutine[Any, Any, Any], *, name: str
        ) -> asyncio.Task[Any]:
            raise NotImplementedError

        def _energy_statistic_targets(
            self, device_id: str, section_prefix: str, stat_key: str
        ) -> list[tuple[str, str]]:
            raise NotImplementedError

        def _http_day_backfill_days_state(
            self, device_id: str, section_prefix: str
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            raise NotImplementedError

        def _local_timezone(self) -> tzinfo:
            raise NotImplementedError

        def _local_today(self) -> date:
            raise NotImplementedError

        def _merge_verified_day_totals_update(
            self,
            updates: dict[str, dict[str, Any]],
            *,
            day_totals: dict[str, float],
            **window: Unpack[_VerifiedDayWindow],
        ) -> None:
            raise NotImplementedError

        def _metric_source_candidates(
            self, section_prefix: str, stat_key: str, metric_key: str
        ) -> list[tuple[str, str]]:
            raise NotImplementedError

        def _publish_verified_day_updates(self, updates: _VerifiedDayUpdates) -> None:
            raise NotImplementedError

        def _statistics_backfill_should_stop(self) -> bool:
            raise NotImplementedError

        def defer_background_auth_failure(self, err: ConfigEntryAuthFailed) -> None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def local_daily_energy_kwh(
            self, device_id: str, metric_key: str
        ) -> float | None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

    def _schedule_statistics_import(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Queue recorder statistic imports without blocking setup or polling."""
        if self._shutdown_started or not self._statistics_import_ready:
            return
        now_monotonic = time.monotonic()
        # Current Recorder buckets follow their own cadence. A pending startup
        # history queue must never slow or pause live/current statistics.
        import_throttle_sec = _STATISTICS_IMPORT_THROTTLE_SEC
        if now_monotonic - self._last_stat_import_monotonic < import_throttle_sec:
            return
        self._last_stat_import_monotonic = now_monotonic

        periodic_snapshot: dict[str, dict[str, Any]] = {}
        for device_id, payload in snapshot.items():
            periodic_payload = dict(payload)

            # Day charts arrive as 15-minute payloads in the slow cache (HTTP backfill
            # and MQTT shadow). They are not merged into self.data by the fast cycle.
            # Inject them here so the statistics import can process live day curves.
            slow_dev = getattr(self, "_slow_cache", {}).get(f"dev:{device_id}", {})
            for cache_key, (_ts, value) in slow_dev.items():
                if isinstance(value, dict) and value:
                    periodic_payload[cache_key] = value

            periodic_snapshot[device_id] = periodic_payload
        if not periodic_snapshot:
            _LOGGER.debug(
                "No periodic sections found in snapshot for statistics import"
            )
            return

        self._schedule_statistics_backfill(periodic_snapshot)

    @callback
    def _schedule_statistics_backfill(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Run bounded historical HTTP queues independently from current imports."""
        if self._shutdown_started:
            return
        if (
            self._statistics_backfill_task is not None
            and not self._statistics_backfill_task.done()
        ):
            return
        self._statistics_backfill_task = self._create_entry_background_task(
            self._async_statistics_backfill_job(snapshot),
            name=f"{DOMAIN}_statistics_backfill",
        )

    async def _async_statistics_backfill_job(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Advance startup history or one later bounded retry cycle."""
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            if self._shutdown_started:
                return
            await self._async_ensure_statistics_backfill_state_loaded()
            # Shutdown may start while the awaited storage load is running.
            if getattr(self, "_shutdown_started", False):
                return
            if self._capture_observed_day_statistics(snapshot):
                await self._async_save_statistics_backfill_state()
            if self._statistics_startup_sync_pending:
                await self._async_complete_statistics_startup_sync(snapshot)
            else:
                await self._async_advance_statistics_backfill(snapshot)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self._async_save_statistics_backfill_state())
            except Exception:
                _LOGGER.exception("Could not save interrupted statistics backfill")
            raise
        except ConfigEntryAuthFailed as err:
            self.defer_background_auth_failure(err)
        except RECORDER_BACKGROUND_TASK_ERRORS:
            _LOGGER.exception("Jackery historical statistics backfill failed")
        finally:
            if asyncio.current_task() is self._statistics_backfill_task:
                self._statistics_backfill_task = None

    async def _async_complete_statistics_startup_sync(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Run the complete startup pass; later scheduling fills any actual failures."""
        if self._shutdown_started or not self._statistics_startup_sync_is_pending():
            return
        # Recorder backfills have their own read-only HTTP requests. A slow
        # OTA, alarm, or chart-cache refresh must never hold up this queue.
        await self._async_advance_statistics_backfill(snapshot)

    def _statistics_startup_sync_is_pending(self) -> bool:
        """Return whether startup history still has immediately queued work."""
        return self._statistics_startup_sync_pending

    async def _async_advance_statistics_backfill(  # ruff: ignore[too-many-return-statements]
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Advance owner, period and day history within the current cycle budget."""
        if not snapshot:
            return
        await self._async_ensure_statistics_backfill_state_loaded()
        retry_until = safe_float(getattr(self, "_statistics_rate_limit_until", None))
        if retry_until is not None and retry_until > time.time():
            self._statistics_import_diagnostics.update({
                "last_http_backfill_rate_limited": True,
                "next_http_backfill_allowed_in_seconds": retry_until - time.time(),
            })
            return
        # Backfill is supplementary HTTP history, never the ingress path for
        # continuously arriving telemetry. Keep each pass within the existing
        # regular 12-request / 20-second budget, including startup; the old
        # 400-request startup sweep delayed unrelated live work for minutes.
        self._statistics_backfill_deadline = (
            time.monotonic() + _STATISTICS_HTTP_CYCLE_TIME_BUDGET_SEC
        )
        budget_per_queue = _STATISTICS_HTTP_CYCLE_REQUEST_BUDGET // 3
        # One deterministic, bounded pass instead of a changing queue order.
        # The real PV curve gets a fair daily slice, while other periods and
        # owner-specific repair retain their own slots.
        day = await self._async_http_backfill_recent_day_statistics(
            snapshot,
            force=True,
            include_current_year=True,
            request_budget=budget_per_queue,
        )
        if day.get("rate_limited") or day.get("stopped"):
            return
        if self._statistics_backfill_should_stop():
            return
        period = await self._async_http_backfill_period_statistics(
            snapshot, request_budget=budget_per_queue
        )
        if period.get("rate_limited") or period.get("stopped"):
            return
        if self._statistics_backfill_should_stop():
            return
        requested = await self._async_run_owner_day_requeue(snapshot)
        owner = self._statistics_import_diagnostics.get("owner_day_requeue", {})
        if requested and owner.get("stopped"):
            return
        if (
            self._statistics_startup_sync_pending
            and day.get("actionable_sources", 0) == 0
            and period.get("actionable_sources", 0) == 0
            and not owner.get("open", 0)
        ):
            self._statistics_startup_sync_pending = False
            await self._async_save_statistics_backfill_state()

    @staticmethod
    def _missing_pv_month_days(
        payload: dict[str, Any], today: date
    ) -> dict[date, set[str]]:
        """Select dated own channel gaps, never placeholders or other totals."""
        section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}"
        source = payload.get(section)
        if not isinstance(source, dict):
            return {}
        aggregate = dated_daily_chart_values(
            source, section, APP_STAT_TOTAL_SOLAR_ENERGY
        )
        week_section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}"
        week_source = payload.get(week_section)
        if not isinstance(week_source, dict):
            week_source = {}
        missing: dict[date, set[str]] = {}
        for key in (
            APP_STAT_PV1_ENERGY,
            APP_STAT_PV2_ENERGY,
            APP_STAT_PV3_ENERGY,
            APP_STAT_PV4_ENERGY,
        ):
            if not pv_channel_scalar_is_lifetime_offset(source, section, key):
                continue
            own = dated_daily_chart_values(source, section, key)
            own_week = dated_daily_chart_values(week_source, week_section, key)
            for day in aggregate:
                if (
                    day < today  # ruff: ignore[too-many-boolean-expressions]
                    and (day.year, day.month) == (today.year, today.month)
                    and (day not in own or own[day] < 0)
                    and (day not in own_week or own_week[day] < 0)
                ):
                    missing.setdefault(day, set()).add(key)
        return missing

    def _pv_month_recovery_owner(self, device_id: str) -> tuple[object, ...] | None:
        """Bind a retry delay to the physical unit across equivalent rediscovery."""
        index = getattr(self, "_device_index", None)
        record = index.get(device_id) if isinstance(index, dict) else None
        if not isinstance(record, dict):
            return None
        device = record.get(PAYLOAD_DEVICE_META)
        system = record.get(PAYLOAD_SYSTEM_META)
        if not isinstance(device, dict):
            return None
        serial = device.get(FIELD_DEVICE_SN)
        if not isinstance(serial, str) or not serial:
            return None
        system_id = record.get(FIELD_SYSTEM_ID)
        epochs = getattr(self, "_system_binding_epochs", {})
        parent_epochs = getattr(self, "_http_parent_identity_epochs", {})
        entry = getattr(self, "entry", None)
        if entry is not None and getattr(entry, "runtime_data", self) is not self:
            return None
        values = (
            device.get(FIELD_DEVICE_ID),
            serial,
            system_id,
            device.get(FIELD_MODEL_CODE),
            system.get(FIELD_ID) if isinstance(system, dict) else None,
            epochs.get(system_id),
            epochs.get(device_id),
            parent_epochs.get(device_id),
        )
        if any(value is not None and type(value) not in {str, int} for value in values):
            return None
        return entry, values

    @staticmethod
    def _pv_month_recovery_same_owner(
        previous: tuple[object, ...] | None, current: tuple[object, ...] | None
    ) -> bool:
        """Physical identity, binding epochs and the entry retain their ownership."""
        return bool(
            previous is not None
            and current is not None
            and previous[0] is current[0]
            and previous[1] == current[1]
        )

    async def _async_recover_pv_month_days(  # ruff: ignore[too-many-branches]  # ruff: ignore[complex-structure, too-many-locals, too-many-statements]
        self, snapshot: dict[str, dict[str, Any]]
    ) -> None:
        """Certify missing own days before unrelated historical Recorder work."""
        await self._async_ensure_statistics_backfill_state_loaded()
        today = self._local_today()
        week_start = today - timedelta(days=today.weekday())
        now_epoch = time.time()
        retries = getattr(self, "_pv_month_recovery_retry_state", None)
        if not isinstance(retries, dict):
            self._pv_month_recovery_retry_state: dict[
                tuple[str, str], dict[str, Any]
            ] = {}
            retries = self._pv_month_recovery_retry_state
        month_prefix = today.isoformat()[:7]
        for retry_key in tuple(retries):
            if retry_key[0] not in snapshot or not retry_key[1].startswith(
                month_prefix
            ):
                del retries[retry_key]
        recovery_context: dict[
            tuple[str, str], tuple[tuple[object, ...] | None, set[str]]
        ] = {}
        progress = _HttpDayBackfillProgress(
            target_days=[],
            force=False,
            window_days=0,
            include_current_year=False,
            now_monotonic=time.monotonic(),
            verification_only=True,
        )
        candidates: list[_HttpDayBackfillCandidate] = []
        for device_id, payload in snapshot.items():
            missing_days = self._missing_pv_month_days(payload, today)
            missing_dates = {day.isoformat() for day in missing_days}
            resolved_retries = [
                key
                for key in retries
                if key[0] == device_id and key[1] not in missing_dates
            ]
            if resolved_retries:
                _, resolved_days = self._http_day_backfill_days_state(
                    device_id, APP_SECTION_PV_STAT
                )
                for retry_key in resolved_retries:
                    del retries[retry_key]
                    resolved = resolved_days.get(retry_key[1])
                    if (
                        isinstance(resolved, dict)
                        and resolved.pop("pv_month_recovery_retry", None) is not None
                    ):
                        progress.state_changed = True
            for target_day, keys in sorted(missing_days.items()):
                if self._shutdown_started:
                    progress.stopped = True
                    break
                _, days = self._http_day_backfill_days_state(
                    device_id, APP_SECTION_PV_STAT
                )
                state = days.setdefault(
                    target_day.isoformat(), {"status": BackfillStatus.PENDING.value}
                )
                if not isinstance(state, dict):
                    state = days[target_day.isoformat()] = {
                        "status": BackfillStatus.PENDING.value
                    }
                cached = self._cached_verified_day_totals(
                    state,
                    APP_SECTION_PV_STAT,
                    owner_key=self._observed_day_owner_key(
                        self._observed_day_source_owner(device_id, APP_SECTION_PV_STAT)
                    ),
                )
                retry_key = (device_id, target_day.isoformat())
                if keys.issubset(cached):
                    retries.pop(retry_key, None)
                    if state.pop("pv_month_recovery_retry", None) is not None:
                        progress.state_changed = True
                    self._merge_verified_day_totals_update(
                        progress.verified_day_updates,
                        day_totals=cached,
                        device_id=device_id,
                        target_day=target_day,
                        section_prefix=APP_SECTION_PV_STAT,
                        week_start=week_start,
                        today=today,
                    )
                    continue
                needed = keys.difference(cached)
                owner = self._pv_month_recovery_owner(device_id)
                retry = retries.get(retry_key)
                if retry is not None:
                    now_monotonic = time.monotonic()
                    if (
                        self._pv_month_recovery_same_owner(retry["owner"], owner)
                        and retry["missing"] == needed
                        and retry["attempted"] <= now_monotonic < retry["next"]
                    ):
                        continue
                    if (
                        not self._pv_month_recovery_same_owner(retry["owner"], owner)
                        or retry["missing"] != needed
                        or now_monotonic < retry["attempted"]
                    ):
                        retries.pop(retry_key, None)
                        if state.pop("pv_month_recovery_retry", None) is not None:
                            progress.state_changed = True
                retry_until = safe_float(
                    getattr(self, "_statistics_rate_limit_until", None)
                )
                if retry_until is not None and retry_until > time.time():
                    continue
                attempted = dt_util.parse_datetime(
                    str(state.get("totals_last_attempt_at") or "")
                )
                if (
                    attempted is not None
                    and attempted.tzinfo is not None
                    and 0
                    <= now_epoch - attempted.timestamp()
                    < _STATISTICS_HTTP_BACKFILL_INTERVAL_SEC
                ):
                    continue
                if (
                    now_epoch
                    < self._local_statistic_start(
                        target_day + timedelta(days=1)
                    ).timestamp()
                    + 2 * 3600
                ):
                    continue
                candidates.append(
                    _HttpDayBackfillCandidate(
                        priority=0,
                        attempted=int(attempted is not None),
                        last_attempt=str(state.get("totals_last_attempt_at") or ""),
                        target_day=target_day,
                        attempts=0,
                        device_id=device_id,
                        section_prefix=APP_SECTION_PV_STAT,
                        payload=payload,
                        day_state=state,
                        days_state=days,
                    )
                )
                recovery_context[retry_key] = owner, needed
            if progress.stopped:
                break
        candidates.sort(
            key=lambda item: (
                item.attempted,
                item.last_attempt,
                item.target_day,
                item.device_id,
            )
        )
        for candidate in candidates[: _STATISTICS_HTTP_BACKFILL_REQUEST_BUDGET // 2]:
            retry_until = safe_float(
                getattr(self, "_statistics_rate_limit_until", None)
            )
            if self._shutdown_started or (
                retry_until is not None and retry_until > time.time()
            ):
                break
            retry_key = (candidate.device_id, candidate.target_day.isoformat())
            owner, needed = recovery_context[retry_key]
            if owner is not None and not self._pv_month_recovery_same_owner(
                owner, self._pv_month_recovery_owner(candidate.device_id)
            ):
                retries.pop(retry_key, None)
                continue
            progress.pending_sources += 1
            progress.actionable_sources += 1
            previous_attempt = candidate.day_state.get("totals_last_attempt_at")
            try:
                stopped = await self._async_verify_imported_day_totals(
                    candidate,
                    progress,
                    week_start=week_start,
                    today=today,
                )
            except JackeryStatisticHistoryPendingError as err:
                # This direct caller bypasses the general backfill dispatcher,
                # which normally persists the pending Recorder conflict. Keep
                # the verified updates and defer this same owner/day instead
                # of fetching the identical curve at every fast timer tick.
                candidate.day_state.update({
                    "totals_last_attempt_at": datetime.fromtimestamp(
                        time.time(), UTC
                    ).isoformat(),
                    "totals_last_error": "recorder_pending",
                    "last_error": "recorder_pending",
                    "recorder_pending_reason": str(err),
                    "retry_after_epoch": time.time()
                    + _PV_MONTH_RECOVERY_RETRY_INITIAL_SEC,
                })
                progress.state_changed = True
                stopped = False
            # A cancellation never reaches this point. A delay belongs only to
            # this completed attempt, physical owner and still-missing metrics.
            if (
                owner is not None
                and not self._shutdown_started
                and self._pv_month_recovery_same_owner(
                    owner, self._pv_month_recovery_owner(candidate.device_id)
                )
            ):
                state = candidate.day_state
                remaining = needed.difference(
                    self._cached_verified_day_totals(
                        state,
                        APP_SECTION_PV_STAT,
                        owner_key=self._observed_day_owner_key(
                            self._observed_day_source_owner(
                                candidate.device_id, APP_SECTION_PV_STAT
                            )
                        ),
                    )
                )
                previous = retries.get(retry_key)
                if remaining == needed and (
                    (
                        state.get("totals_last_error")
                        in {"incomplete_day_curve", "recorder_pending"}
                        and state.get("totals_last_attempt_at") != previous_attempt
                    )
                    or (
                        state.get("refresh_source_required") is True
                        and state.get("totals_last_attempt_at") == previous_attempt
                    )
                ):
                    failures = (
                        previous["failures"] + 1
                        if previous is not None and previous["missing"] == remaining
                        else 1
                    )
                    delay = min(
                        _PV_MONTH_RECOVERY_RETRY_INITIAL_SEC
                        * 2 ** min(failures - 1, 5),
                        _PV_MONTH_RECOVERY_RETRY_MAX_SEC,
                    )
                    attempted_monotonic = time.monotonic()
                    retries[retry_key] = {
                        "owner": owner,
                        "missing": remaining,
                        "failures": failures,
                        "attempted": attempted_monotonic,
                        "next": attempted_monotonic + delay,
                    }
                    state["pv_month_recovery_retry"] = {
                        "failures": failures,
                        "missing_metrics": sorted(remaining),
                        "retry_after_seconds": delay,
                        "next_retry_at": datetime.fromtimestamp(
                            time.time() + delay, UTC
                        ).isoformat(),
                    }
                    progress.state_changed = True
                else:
                    retries.pop(retry_key, None)
                    if state.pop("pv_month_recovery_retry", None) is not None:
                        progress.state_changed = True
            else:
                retries.pop(retry_key, None)
                if candidate.day_state.pop("pv_month_recovery_retry", None) is not None:
                    progress.state_changed = True
            if stopped:
                break
        if progress.verified_day_updates:
            self._publish_verified_day_updates(progress.verified_day_updates)
        if progress.state_changed:
            await self._async_save_statistics_backfill_state()

    @staticmethod
    def _split_backfill_request_budget(total: int, workers: int) -> list[int]:
        """Split one hard request cap fairly between independent devices."""
        if workers <= 0:
            return []
        quotient, remainder = divmod(max(0, total), workers)
        return [quotient + int(index < remainder) for index in range(workers)]

    @staticmethod
    def _merge_backfill_results(results: list[dict[str, Any]]) -> dict[str, Any]:
        """Merge per-device queue accounting without losing rate limits."""
        merged: dict[str, Any] = {}
        for result in results:
            for key, value in result.items():
                if key == "rate_limited":
                    merged[key] = bool(merged.get(key)) or value is True
                elif isinstance(value, int | float) and not isinstance(value, bool):
                    merged[key] = merged.get(key, 0) + value
        return merged

    # ------------------------------------------------------------------
    # Statistics import & data-quality reporting
    # ------------------------------------------------------------------

    def _local_statistic_start(self, bucket_date: date | datetime) -> datetime:
        """Return a UTC timestamp for a local app-statistic bucket start."""
        timezone = self._local_timezone()
        if isinstance(bucket_date, datetime):
            if bucket_date.tzinfo is None:
                local_start = bucket_date.replace(tzinfo=timezone)
            else:
                local_start = bucket_date.astimezone(timezone)
        else:
            local_start = datetime.combine(
                bucket_date,
                datetime.min.time(),
                tzinfo=timezone,
            )
        return local_start

    @staticmethod
    def _stat_row_start(row: Mapping[str, Any]) -> float | None:
        """Return a statistics row start timestamp in seconds."""
        return stat_row_start(row)

    async def async_load_statistics_backfill_state(self) -> None:
        """Load persistent recorder-statistics repair state."""
        lock = getattr(self, "_statistics_backfill_store_lock", None)
        if lock is None:
            lock = self._statistics_backfill_store_lock = asyncio.Lock()
        async with lock:
            if self._statistics_backfill_state_loaded:
                return
            loaded = await self._statistics_backfill_store.async_load()
            if isinstance(loaded, dict):
                devices = loaded.get(_STATISTICS_BACKFILL_STORE_DEVICES)
                if isinstance(devices, dict):
                    self._statistics_backfill_state = {
                        _STATISTICS_BACKFILL_STORE_DEVICES: devices,
                    }
                seen = loaded.get("startup_seen")
                if isinstance(seen, list):
                    self._statistics_startup_fetched.update(
                        tuple(item)
                        for item in seen
                        if isinstance(item, list)
                        and len(item) == 3  # ruff: ignore[magic-value-comparison]
                        and all(isinstance(value, str) for value in item)
                    )
                if loaded.get("startup_complete") is True:
                    self._statistics_startup_sync_pending = False
                retry_until = safe_float(loaded.get("rate_limit_until"))
                if retry_until is not None:
                    current = safe_float(
                        getattr(self, "_statistics_rate_limit_until", None)
                    )
                    self._statistics_rate_limit_until = max(retry_until, current or 0.0)
            self._statistics_backfill_state_loaded = True
            self._restore_observed_day_statistics()

    def _restore_observed_day_statistics(self) -> None:  # ruff: ignore[too-many-branches]
        """Publish bound persisted sample sums once per physical source binding."""
        self._invalidate_observed_day_source_bindings()
        devices = self._statistics_backfill_state.get(
            _STATISTICS_BACKFILL_STORE_DEVICES
        )
        if not isinstance(devices, dict):
            return
        restored = getattr(self, "_observed_day_restored_bindings", None)
        if restored is None:
            self._observed_day_restored_bindings: dict[
                tuple[str, str], tuple[object, ...]
            ] = {}
            restored = self._observed_day_restored_bindings
        revisions = getattr(self, "_observed_day_metric_revisions", None)
        if revisions is None:
            revisions = self._observed_day_metric_revisions = {}
        updates = _VerifiedDayUpdates()
        today = self._local_today()
        for device_id, state in devices.items():
            if not isinstance(state, dict):
                continue
            queue = state.get("http_day_backfill")
            sources = queue.get("sources") if isinstance(queue, dict) else None
            if not isinstance(sources, dict):
                continue
            for prefix, source_state in sources.items():
                owner = self._observed_day_source_owner(device_id, prefix)
                if owner is None or restored.get((device_id, prefix)) == owner:
                    continue
                days = (
                    source_state.get("days") if isinstance(source_state, dict) else None
                )
                if not isinstance(days, dict):
                    continue
                owner_key = self._observed_day_owner_key(owner)
                for day_key, day_state in days.items():
                    try:
                        target_day = date.fromisoformat(day_key)
                    except (TypeError, ValueError):
                        continue
                    if target_day > today or not isinstance(day_state, dict):
                        continue
                    if day_state.get("observed_source_owner") != owner_key:
                        continue
                    totals = self._cached_observed_day_totals(day_state, prefix)
                    if not totals:
                        continue
                    revision = max(
                        (
                            revisions.get((device_id, day_key, prefix, metric), 0)
                            for metric in totals
                        ),
                        default=0,
                    )
                    for metric in totals:
                        revisions[device_id, day_key, prefix, metric] = revision
                    self._stage_observed_day_totals(
                        updates,
                        device_id=device_id,
                        target_day=target_day,
                        section_prefix=prefix,
                        totals=totals,
                        owner=owner,
                        revision=revision,
                    )
                restored[device_id, prefix] = owner
        self._publish_observed_day_updates(updates)

    def _capture_observed_day_statistics(
        self, snapshot: dict[str, dict[str, Any]]
    ) -> bool:
        """Reuse delivered day curves before historical queues; issue no requests."""
        self._restore_observed_day_statistics()
        updates = _VerifiedDayUpdates()
        changed = getattr(self, "_verified_day_state_dirty", False)
        self._verified_day_state_dirty = False
        today = self._local_today()
        week_start = today - timedelta(days=today.weekday())
        prefixes = {prefix for prefix, _, _, _ in _app_chart_stat_metrics()}
        for device_id, payload in snapshot.items():
            for prefix in prefixes:
                source = payload.get(f"{prefix}_{DATE_TYPE_DAY}")
                if not isinstance(source, dict):
                    continue
                target_day = request_date(
                    source, APP_REQUEST_BEGIN_DATE, APP_REQUEST_BEGIN_DATE_ALT
                )
                if target_day is None or target_day > today:
                    continue
                _, days = self._http_day_backfill_days_state(device_id, prefix)
                state = days.get(target_day.isoformat())
                state = state if isinstance(state, dict) else {}
                updated = self._record_observed_day_totals_update(
                    updates,
                    source=source,
                    day_state=state,
                    device_id=device_id,
                    target_day=target_day,
                    section_prefix=prefix,
                    week_start=week_start,
                    today=today,
                )
                if state.get("observed_totals"):
                    days[target_day.isoformat()] = state
                changed = updated or changed
        self._publish_observed_day_updates(updates)
        return changed

    async def _async_save_statistics_backfill_state(self) -> None:
        """Persist recorder-statistics repair state."""
        await self._async_ensure_statistics_backfill_state_loaded()
        lock = getattr(self, "_statistics_backfill_store_lock", None)
        if lock is None:
            lock = self._statistics_backfill_store_lock = asyncio.Lock()
        async with lock:
            if getattr(self, "_statistics_startup_sync_pending", True) is False:
                self._statistics_backfill_state["startup_complete"] = True
                self._statistics_backfill_state.pop("startup_seen", None)
            elif seen := getattr(self, "_statistics_startup_fetched", None):
                self._statistics_backfill_state["startup_seen"] = sorted(seen)
            retry_until = safe_float(
                getattr(self, "_statistics_rate_limit_until", None)
            )
            if retry_until is not None:
                self._statistics_backfill_state["rate_limit_until"] = retry_until

            await self._statistics_backfill_store.async_save(
                copy.deepcopy(self._statistics_backfill_state),
            )

    async def _async_ensure_statistics_backfill_state_loaded(self) -> None:
        """Load persistent repair state on demand."""
        if not self._statistics_backfill_state_loaded:
            await self.async_load_statistics_backfill_state()

    @property
    def statistics_backfill_diagnostics(self) -> dict[str, Any]:
        """Redaction-safe statistics repair diagnostics."""
        devices = self._statistics_backfill_state.get(
            _STATISTICS_BACKFILL_STORE_DEVICES,
        )
        if not isinstance(devices, dict):
            devices = {}
        redacted_devices: dict[str, Any] = {}
        for device_id in sorted(devices, key=str):
            state = devices.get(device_id)
            redacted_devices[str(device_id)] = (
                dict(state) if isinstance(state, dict) else {}
            )
        return {
            "loaded": self._statistics_backfill_state_loaded,
            "tracked_devices": len(redacted_devices),
            "devices": redacted_devices,
        }

    def _statistics_backfill_device_state(self, device_id: str) -> dict[str, Any]:
        """Return the mutable persistent repair state for one device."""
        devices = self._statistics_backfill_state.setdefault(
            _STATISTICS_BACKFILL_STORE_DEVICES,
            {},
        )
        if not isinstance(devices, dict):
            devices = {}
            self._statistics_backfill_state[_STATISTICS_BACKFILL_STORE_DEVICES] = (
                devices
            )
        state = devices.setdefault(str(device_id), {})
        if not isinstance(state, dict):
            state = {}
            devices[str(device_id)] = state
        return state

    @staticmethod
    def _iter_calendar_months(start_date: date, end_date: date) -> list[date]:
        """Return first-of-month dates intersecting an inclusive date range.

        Static method (parallel to ``_iter_calendar_weeks`` below). The
        missing ``@staticmethod`` decorator caused
        ``self._iter_calendar_months(from_date, to_date)`` to pass three
        positional arguments to a two-arg function, breaking every
        ``async_import_statistics`` entity-stat repair attempt — the
        Recorder-side ``sensor.solarvault_3_pro_max_*`` entity statistic
        IDs that feed the Energy Dashboard's flow accounting. Observed
        2026-05-16 production log:

            Jackery recorder-statistics import failed:
            JackerySolarVaultCoordinator._iter_calendar_months() takes
            2 positional arguments but 3 were given
        """
        return iter_calendar_months(start_date, end_date)

    @staticmethod
    def _iter_calendar_weeks(start_date: date, end_date: date) -> list[date]:
        """Return Monday week starts intersecting an inclusive date range."""
        return iter_calendar_weeks(start_date, end_date)

    @staticmethod
    def _iter_calendar_years(start_date: date, end_date: date) -> list[int]:
        """Return calendar years intersecting an inclusive date range."""
        return iter_calendar_years(start_date, end_date)

    @staticmethod
    def _app_chart_period_meta(date_type: str) -> tuple[str, str] | None:
        """Return the external bucket id and label for an app chart period."""
        return app_chart_period_meta(date_type)

    @staticmethod
    def _app_chart_name_prefix(device_id: str, payload: dict[str, Any]) -> str:
        """Return a stable, user-readable app chart statistic name prefix."""
        return app_chart_name_prefix(device_id, payload)

    def _day_chart_source_candidates(
        self,
        section_prefix: str,
        stat_key: str,
        metric_key: str,
    ) -> list[tuple[str, str]]:
        """Return candidate payload sections for one day power-curve metric."""
        candidates: list[tuple[str, str]] = []
        trend_source = _DAY_TREND_SOURCE_BY_METRIC_KEY.get(metric_key)
        if trend_source is not None:
            candidates.append(trend_source)
        for candidate_prefix, candidate_stat_key in self._metric_source_candidates(
            section_prefix,
            stat_key,
            metric_key,
        ):
            candidates.append((
                f"{candidate_prefix}_{DATE_TYPE_DAY}",
                candidate_stat_key,
            ))

        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            deduped.append(candidate)
        return deduped

    def _reconcile_day_chart_points_to_local_total(
        self,
        options: _DayChartPointsOptions,
        source: dict[str, Any],
        source_stat_key: str,
        points: list[Any],
    ) -> list[Any]:
        """Reconcile a positive cloud curve to its authoritative local total."""
        metric_key = options["metric_key"]
        local_total = self._local_day_chart_total_for_metric(
            options["device_id"],
            metric_key,
        )
        cloud_total = effective_period_total_value(
            source,
            options["section_prefix"],
            source_stat_key,
        )
        energy_scale = app_energy_unit_scale(source)
        if cloud_total is not None and energy_scale is not None:
            cloud_total *= energy_scale
        point_total = round(sum(point.value for point in points), 5)
        source_total = max(
            0.0,
            point_total,
            cloud_total if cloud_total is not None else 0.0,
        )
        if not local_period_total_supersedes_cloud(
            source_total,
            local_total,
            tolerance=_STATISTICS_IMPORT_STATE_TOLERANCE,
        ):
            return points
        if point_total <= 0 or local_total is None:
            return []
        scale = local_total / point_total
        scaled_values = [round(max(point.value * scale, 0.0), 5) for point in points]
        correction = round(local_total - sum(scaled_values), 5)
        target_index = next(
            (
                index
                for index in range(len(scaled_values) - 1, -1, -1)
                if scaled_values[index] > 0
            ),
            len(scaled_values) - 1,
        )
        scaled_values[target_index] = round(
            max(0.0, scaled_values[target_index] + correction),
            5,
        )
        reconciled = [
            point._replace(value=value)
            for point, value in zip(points, scaled_values, strict=True)
        ]
        _LOGGER.debug(
            "Reconciled Jackery %s day chart to local_daily_energy "
            "%s=%.5f from cloud/chart %.5f",
            metric_key,
            _LOCAL_DAILY_METRIC_BY_CHART_METRIC_KEY.get(metric_key),
            local_total,
            source_total,
        )
        return reconciled

    def _day_chart_points_for_metric(
        self,
        **options: Unpack[_DayChartPointsOptions],
    ) -> list[Any]:
        """Return converted day power-curve points for one metric."""
        candidates = self._day_chart_source_candidates(
            options["section_prefix"],
            options["stat_key"],
            options["metric_key"],
        )
        for section, source_stat_key in candidates:
            source = options["payload"].get(section)
            if not isinstance(source, dict):
                continue
            if options.get("native_power") and (
                str(source.get(APP_STAT_UNIT, "")).strip().lower() != "w"
            ):
                continue
            # A metric's alternate cloud/local day source may fill missing
            # five-minute timestamps. Never replace an observed first-source
            # sample, or borrow a different metric, device, day or unit.
            series_key = day_power_series_key(source, section, source_stat_key)
            if series_key is not None:
                for other_section, other_stat_key in candidates:
                    if (other_section, other_stat_key) == (section, source_stat_key):
                        continue
                    alternate = options["payload"].get(other_section)
                    if not isinstance(alternate, dict):
                        continue
                    other_key = day_power_series_key(
                        alternate, other_section, other_stat_key
                    )
                    if other_key is None or not isinstance(
                        alternate.get(other_key), list
                    ):
                        continue
                    counterpart = {
                        key: value
                        for key, value in alternate.items()
                        if key
                        in {
                            "x",
                            "_request",
                            "unit",
                            "deviceId",
                            "deviceSn",
                            "systemId",
                            "systemSn",
                        }
                    }
                    counterpart[series_key] = alternate[other_key]
                    source = supplement_day_curve_samples(
                        source, counterpart, series_keys=(series_key,)
                    )
            complete_bucket_options = (
                {"require_complete_bucket": True}
                if options.get("require_complete_bucket")
                else {}
            )
            points = _coordinator_recorder_attr(
                "day_power_energy_points", day_power_energy_points
            )(
                source,
                section,
                source_stat_key,
                bucket_minutes=options["bucket_minutes"],
                today=options["now"].date(),
                now=options["now"],
                keep_curve_over_smaller_scalar=True,
                require_complete_day=options.get("require_complete_day", False),
                use_scalar_total=options.get(
                    "use_scalar_total", not options.get("native_power")
                ),
                **complete_bucket_options,
            )
            if not points:
                continue
            target_day = options.get("target_day")
            if target_day is not None and any(
                (
                    point.start_date.date()
                    if isinstance(point.start_date, datetime)
                    else point.start_date
                )
                != target_day
                for point in points
            ):
                continue
            if options.get("use_local_day_guard", True):
                points = self._reconcile_day_chart_points_to_local_total(
                    options,
                    source,
                    source_stat_key,
                    points,
                )
            return points
        return []

    def _local_day_chart_total_for_metric(
        self,
        device_id: str,
        metric_key: str,
    ) -> float | None:
        """Return the documented local-day total for an app-chart metric."""
        local_metric_key = _LOCAL_DAILY_METRIC_BY_CHART_METRIC_KEY.get(metric_key)
        if local_metric_key is None:
            return None
        return self.local_daily_energy_kwh(device_id, local_metric_key)

    @staticmethod
    def _historical_app_period_kwargs(
        date_type: str,
        period_start: date,
    ) -> dict[str, Any] | None:
        """Build one explicit App period request."""
        if date_type == DATE_TYPE_WEEK:
            return app_period_request_kwargs(DATE_TYPE_WEEK, today=period_start)
        if date_type == DATE_TYPE_MONTH:
            return app_month_request_kwargs(period_start.year, period_start.month)
        if date_type == DATE_TYPE_YEAR:
            return app_year_request_kwargs(period_start.year)
        return None

    def _historical_app_chart_request(
        self,
        options: _HistoricalAppChartFetchOptions,
        kwargs: dict[str, Any],
    ) -> Callable[[], Awaitable[dict[str, Any]]] | None:
        """Return the documented request for one App chart section."""
        device_id = options["device_id"]
        system_id = options["system_id"]
        section = options["section_prefix"]
        request: Callable[[], Awaitable[dict[str, Any]]] | None = None
        if section == APP_SECTION_PV_STAT:
            request = partial(
                self.api.async_get_device_pv_stat,
                device_id,
                system_id,
                **kwargs,
            )
        elif section == APP_SECTION_BATTERY_STAT:
            request = partial(
                self.api.async_get_device_battery_stat,
                device_id,
                **kwargs,
            )
        elif section == APP_SECTION_HOME_STAT:
            request = partial(
                self.api.async_get_device_home_stat,
                device_id,
                **kwargs,
            )
        elif section == APP_SECTION_HOME_TRENDS and system_id:
            request = partial(self.api.async_get_home_trends, system_id, **kwargs)
        elif section == APP_SECTION_CT_STAT:
            request = partial(
                self._async_get_device_ct_stat_with_l2_fallback,
                options.get("ct_device_id") or device_id,
                DevicePeriodQuery.from_kwargs(kwargs),
            )
        elif section == APP_SECTION_EPS_STAT:
            request = partial(
                self.api.async_get_device_eps_stat,
                device_id,
                **kwargs,
            )
        return request

    async def _async_fetch_historical_app_chart_source(
        self,
        **options: Unpack[_HistoricalAppChartFetchOptions],
    ) -> dict[str, Any]:
        """Fetch one explicit historical app chart source payload."""
        kwargs = self._historical_app_period_kwargs(
            options["date_type"],
            options["period_start"],
        )
        if kwargs is None:
            return {}
        request_factory = self._historical_app_chart_request(options, kwargs)
        if request_factory is None:
            return {}
        owner = self._observed_day_source_owner(
            options["device_id"], options["section_prefix"]
        )
        if not self._historical_period_request_owner_matches(options, owner):
            raise JackeryStatisticHistoryPendingError(  # ruff: ignore[raise-vanilla-args]
                "Historical period request no longer matches its physical source"
            )
        key = (
            options["device_id"],
            options["section_prefix"],
            options["date_type"],
            options["period_start"].isoformat(),
        )
        revision = getattr(self, "_historical_period_request_revision", 0) + 1
        self._historical_period_request_revision = revision
        revisions = getattr(self, "_http_period_source_revisions", None)
        if revisions is None:
            self._http_period_source_revisions: dict[
                tuple[str, str, str, str], int
            ] = {}
            revisions = self._http_period_source_revisions
        revisions[key] = revision
        day_revision = getattr(self, "_historical_day_request_revision", 0)
        fetched = await self._async_slow_http_call(request_factory)
        source = _HistoricalPeriodSource(
            copy.deepcopy(fetched) if isinstance(fetched, dict) else {}
        )
        source.source_owner = owner
        source.request_key = key
        source.revision = revision
        source.day_revision = day_revision
        if not self._historical_period_source_is_current(key, source):
            raise JackeryStatisticHistoryPendingError(  # ruff: ignore[raise-vanilla-args]
                "Historical period source changed while the request was in flight"
            )
        return source

    @staticmethod
    def _ct_stat_source_has_observable_data(source: dict[str, Any]) -> bool:
        """Return whether a CT chart payload contains an actual observation."""
        for series_key in ("series", "y", "y1", "y2"):
            series = source.get(series_key)
            if isinstance(series, list | tuple) and any(
                safe_float(value) is not None for value in series
            ):
                return True
        for total_key in (
            APP_STAT_TOTAL_CT_INPUT_ENERGY,
            APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        ):
            total = safe_float(source.get(total_key))
            if total is not None and total > 0:
                return True
        return False

    async def _async_get_device_ct_stat_with_l2_fallback(
        self,
        device_id: str,
        query: DevicePeriodQuery,
    ) -> dict[str, Any]:
        """Fetch only the grid CT chart; third-PV readings cannot fill grid gaps."""
        l1_source = await self.api.async_get_device_ct_stat(
            device_id,
            query=replace(query, stat_type=CT_STAT_TYPE_L1),
        )
        if self._ct_stat_source_has_observable_data(l1_source):
            return l1_source
        return {}

    async def _import_collected_repair_buckets(
        self,
        *,
        device_id: str,
        name_prefix: str,
        collected: dict[tuple[str, str, date], dict[str, Any]],
        period_meta_by_type: dict[str, tuple[str, str]],
        to_date: date,
    ) -> tuple[int, int, int, dict[str, str]]:
        """Return rows, failures, successes and pending native targets."""
        del name_prefix, period_meta_by_type
        repaired = 0
        failed = 0
        reconciled = 0
        pending_targets: dict[str, str] = {}
        for (section_prefix, date_type, period_start), source in collected.items():
            source_key = device_id, section_prefix, date_type, period_start.isoformat()
            if not self._historical_period_source_is_current(source_key, source):
                raise JackeryStatisticHistoryPendingError(  # ruff: ignore[raise-vanilla-args]
                    "Waiting for a current owner-bound historical period source"
                )
            section = f"{section_prefix}_{date_type}"
            for (
                metric_section,
                stat_key,
                _metric_key,
                _label,
            ) in _app_chart_stat_metrics():
                if metric_section != section_prefix:
                    continue
                points = trend_series_points(
                    source,
                    section,
                    stat_key,
                    today=to_date,
                )
                targets = [
                    statistic_id
                    for statistic_id, reset_period in self._energy_statistic_targets(
                        device_id, section_prefix, stat_key
                    )
                    if reset_period == date_type
                ]
                for point in points:
                    value = safe_float(point.value)
                    if value is None or value < 0:
                        continue
                    point_start = point.start_date
                    day = (
                        point_start.astimezone(self._local_timezone()).date()
                        if isinstance(point_start, datetime)
                        else point_start
                    )
                    day_start = self._local_statistic_start(day).timestamp()
                    bucket_end = day + timedelta(days=1)
                    if date_type == DATE_TYPE_YEAR:
                        # A year-chart point is a month aggregate, never day 1 energy.
                        bucket_end = (day.replace(day=28) + timedelta(days=4)).replace(
                            day=1
                        )
                        if bucket_end > to_date + timedelta(days=1):
                            continue
                    day_end = self._local_statistic_start(bucket_end).timestamp()
                    hours = [
                        float(hour)
                        for hour in range(int(day_start), int(day_end), 3600)
                    ]
                    for statistic_id in targets:
                        if statistic_id in pending_targets:
                            continue
                        try:
                            repaired += await self._async_reconcile_statistic_day(
                                statistic_id,
                                hours,
                                {hours[-1]: value},
                                reset_period=date_type,
                                aggregate_only=True,
                                recorder_guard=partial(
                                    self._historical_period_source_is_current,
                                    source_key,
                                    source,
                                ),
                            )
                            reconciled += 1
                        except JackeryStatisticHistoryPendingError as err:
                            pending_targets[statistic_id] = str(err)
                            _LOGGER.debug(
                                "Jackery native period statistics deferred for %s: %s",
                                statistic_id,
                                err,
                            )
                        except RECORDER_BACKGROUND_TASK_ERRORS:
                            failed += 1
                            _LOGGER.exception(
                                "Jackery native period statistics repair failed for %s",
                                statistic_id,
                            )
        return repaired, failed, reconciled, pending_targets

    # ------------------------------------------------------------------
    # Coordinator update cycle (merge of HTTP + MQTT + caches)
    # ------------------------------------------------------------------
