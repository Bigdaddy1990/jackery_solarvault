"""Statistics, trends, and energy endpoint mixins."""

from typing import Any

from ...const import (
    ALARM_PATH,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_META,
    BATTERY_TRENDS_PATH,
    BOX_STAT_PATH,
    CARBON_STAT_PATH,
    CUTOFF_STAT_PATH,
    DATE_TYPE_DAY,
    DEVICE_BATTERY_STAT_PATH,
    DEVICE_CT_STAT_PATH,
    DEVICE_EPS_STAT_PATH,
    DEVICE_HOME_STAT_PATH,
    DEVICE_METER_STAT_PATH,
    DEVICE_PORTABLE_CT_STAT_PATH,
    DEVICE_PV_STAT_PATH,
    DEVICE_SOCKET_STATISTIC_PATH,
    DEVICE_SOCKET_STAT_PATH,
    DEVICE_STATISTIC_PATH,
    DEVICE_TODAY_ENERGY_PATH,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_SMART_SOCKET_ID,
    FIELD_SYSTEM_ID,
    HOME_TRENDS_PATH,
    PROFIT_STAT_PATH,
    PV_TRENDS_PATH,
    SLOW_ENDPOINT_TIMEOUT_SEC,
    SMART_SCHEDULE_PATH,
    SOC_STAT_PATH,
    SYMMETRY_STAT_PATH,
    SYSTEM_STATISTIC_PATH,
)
from ...util import app_period_date_bounds
from .._http import BaseHTTPMixin


class StatisticsEndpointMixin(BaseHTTPMixin):
    """Statistics, trends, and energy endpoint methods."""

    async def async_get_alarm(self, system_id: str | int) -> Any:  # noqa: ANN401  # parsed JSON response, indexed by callers
        """Fetches the alarm list for the specified system.

        Stores the raw parsed response in `self.last_alarm_response`.

        Returns:
            The backend `data` field (commonly a list of alarm dictionaries) or `None` if the field is absent.
        """
        data = await self._get_json(
            ALARM_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        self.last_alarm_response = data
        return data.get(FIELD_DATA)

    async def async_get_system_statistic(self, system_id: str | int) -> dict:
        """GET /v1/device/stat/systemStatistic — today/total KPIs.

        Response keys (verified):
            todayLoad, todayBatteryDisChg, todayBatteryChg, todayGeneration,
            totalGeneration, totalRevenue, totalCarbon, isSetPrice
        """
        data = await self._get_json(
            SYSTEM_STATISTIC_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        self.last_statistic_response = data
        return self._payload_dict(data, SYSTEM_STATISTIC_PATH)

    async def async_get_pv_trends(
        self,
        system_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """GET /v1/device/stat/sys/pv/trends — historical curves."""
        begin_date, end_date = app_period_date_bounds(
            date_type, begin_date=begin_date, end_date=end_date
        )
        params = {
            FIELD_SYSTEM_ID: str(system_id),
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        data = await self._get_json(
            PV_TRENDS_PATH, params=params, request_timeout=SLOW_ENDPOINT_TIMEOUT_SEC
        )
        payload = self._payload_dict(data, PV_TRENDS_PATH)
        if payload:
            payload.setdefault(
                APP_REQUEST_META,
                {k: v for k, v in params.items() if k != FIELD_SYSTEM_ID},
            )
        return payload

    async def async_get_device_statistic(self, device_id: str | int) -> dict:
        """Return current-day energy-flow statistics for the specified device.

        The result is a mapping of metric keys to their values as numeric strings representing kilowatt-hours (kWh). Available keys vary by device and backend response; examples include `pvEgy`, `inEpsEgy`, `ongridOtBatEgy`, `pvOtBatEgy`, `inOngridEgy`, `outOngridEgy`, `batOtGridEgy`, `outEpsEgy`, `batDisChgEgy`, `acOtBatEgy`, `batOtAcEgy`, and `batChgEgy`.

        Parameters:
            device_id (str | int): Device identifier (deviceId) to query.

        Returns:
            dict: Mapping from metric key (str) to its value as a string in kWh.
        """
        data = await self._get_json(
            DEVICE_STATISTIC_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_device_statistic_responses[str(device_id)] = data
        return self._payload_dict(data, DEVICE_STATISTIC_PATH)

    async def _async_get_device_period_stat(  # noqa: PLR0913
        self,
        path: str,
        *,
        device_id: str | int,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
        system_id: str | int | None = None,
    ) -> dict[str, Any]:
        """Fetch period-based chart data for a specific device and date range.

        The returned value is the endpoint's `data` object normalized to a dict. If absent, an empty dict is returned. An `APP_REQUEST_META` entry is added (when missing) containing the request parameters used to fetch the data, excluding `deviceId` and `systemId`, so callers can correlate the payload with the requested period.

        Parameters:
            path (str): Endpoint path to query.
            device_id (str | int): Device identifier to request data for.
            date_type (str): Period granularity (e.g., day, month, year). `begin_date`/`end_date` are computed if omitted.
            begin_date (str | None): Start date for the period (computed if None).
            end_date (str | None): End date for the period (computed if None).
            system_id (str | int | None): Optional system identifier included in the request.

        Returns:
            dict[str, Any]: Normalized payload dict from the endpoint's `data` field, augmented with `APP_REQUEST_META`.
        """
        # PROTOCOL.md §2: Periodenabfragen use explicit full ranges.
        # month/year with today..today can return day-like partial totals.
        begin_date, end_date = app_period_date_bounds(
            date_type, begin_date=begin_date, end_date=end_date
        )
        params: dict[str, str] = {
            FIELD_DEVICE_ID: str(device_id),
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        if system_id is not None:
            params[FIELD_SYSTEM_ID] = str(system_id)
        data = await self._get_json(path, params=params)
        data.setdefault(APP_REQUEST_META, {"path": path, "params": dict(params)})
        self.last_device_period_stat_responses[f"{path}:{device_id}:{date_type}"] = data
        payload = self._payload_dict(data, path)
        payload.setdefault(
            APP_REQUEST_META,
            {
                k: v
                for k, v in params.items()
                if k not in {FIELD_DEVICE_ID, FIELD_SYSTEM_ID}
            },
        )
        return payload

    async def async_get_device_pv_stat(
        self,
        device_id: str | int,
        system_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve photovoltaic (PV) statistics for a single device within a system.

        Parameters:
            device_id (str | int): Device identifier.
            system_id (str | int): System identifier that the device belongs to.
            date_type (str): Period granularity (e.g., day, month); defaults to DATE_TYPE_DAY.
            begin_date (str | None): Inclusive start date for the period (format depends on API); when omitted the API's default period bounds are used.
            end_date (str | None): Inclusive end date for the period (format depends on API); when omitted the API's default period bounds are used.

        Returns:
            dict: Parsed response payload from the endpoint, typically containing chart series and related metadata.
        """
        return await self._async_get_device_period_stat(
            DEVICE_PV_STAT_PATH,
            device_id=device_id,
            system_id=system_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_device_battery_stat(
        self,
        device_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """GET /v1/device/stat/battery — app battery statistics for one device."""
        return await self._async_get_device_period_stat(
            DEVICE_BATTERY_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_device_home_stat(
        self,
        device_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Fetch on-grid (home) statistics for the specified device and period.

        Returns:
            Normalized response payload dict containing chart/statistics data. When present, `APP_REQUEST_META` contains the request metadata for the query (excluding `deviceId`).
        """
        return await self._async_get_device_period_stat(
            DEVICE_HOME_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_device_ct_stat(
        self,
        device_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve CT (smart-meter) statistics for the given device and period.

        Parameters:
            device_id: Identifier sent as the `deviceId` query parameter.
            date_type: Period type for the chart (e.g., day or month).
            begin_date: Optional start date for the period (ISO-like string).
            end_date: Optional end date for the period (ISO-like string).

        Returns:
            A dictionary containing the parsed CT/smart-meter statistics payload. May include `APP_REQUEST_META` with the request parameters when a date range is supplied.
        """
        return await self._async_get_device_period_stat(
            DEVICE_CT_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_device_eps_stat(
        self,
        device_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve EPS (off-grid) energy input/output statistics for a device over a specified period.

        The returned payload includes aggregated totals (for example `totalInEpsEnergy`, `totalOutEpsEnergy`), time-series chart arrays (`x`, `y`, `y1`, `y2`), and may include an `APP_REQUEST_META` dict with the request parameters used.

        Returns:
            dict: Parsed backend response containing `data` with aggregates and series; may include `APP_REQUEST_META`.
        """
        return await self._async_get_device_period_stat(
            DEVICE_EPS_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_today_energy(self, device_sn: str) -> dict[str, Any]:
        """Retrieve today's compact energy KPIs for a device.

        Parameters:
            device_sn (str): Device serial number; sent as the `deviceSn` query parameter.

        Returns:
            dict: Parsed JSON response containing KPI fields such as `de` (feed-in), `dg` (grid import), `dh` (home load), and `ds` (battery energy).
        """
        data = await self._get_json(
            DEVICE_TODAY_ENERGY_PATH,
            params={FIELD_DEVICE_SN: str(device_sn)},
        )
        return self._payload_dict(data, DEVICE_TODAY_ENERGY_PATH)

    async def async_get_portable_ct_stat(
        self,
        device_id: str | int,
    ) -> dict[str, Any]:
        """GET /v1/device/stat/ct/statics — portable device CT phase totals.

        Parameters:
            device_id (str | int): Portable device identifier.

        Returns:
            dict: Parsed payload with phase totals (l1, l2, total).
        """
        data = await self._get_json(
            DEVICE_PORTABLE_CT_STAT_PATH,
            params={FIELD_DEVICE_ID: str(device_id)},
        )
        return self._payload_dict(data, DEVICE_PORTABLE_CT_STAT_PATH)

    async def async_get_device_meter_stat(
        self,
        device_id: str | int,
    ) -> dict[str, Any]:
        """Get smart-meter (CT accessory) panel totals for the specified device.

        Parameters:
            device_id (str | int): Smart-Meter / CT accessory `deviceId` (not the SolarVault main deviceId).

        Returns:
            dict: Parsed payload containing the meter panel totals returned by the device meter statistics endpoint.
        """
        data = await self._get_json(
            DEVICE_METER_STAT_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        self.last_device_period_stat_responses[
            f"{DEVICE_METER_STAT_PATH}:{device_id}:panel"
        ] = data
        return self._payload_dict(data, DEVICE_METER_STAT_PATH)

    async def async_get_device_socket_statistic(
        self,
        smart_socket_id: str | int,
    ) -> dict[str, Any]:
        """Get socket panel totals for the specified smart socket.

        Returns:
            The response `data` payload as a dict; an empty dict if the payload is missing or not a dict.
        """
        data = await self._get_json(
            DEVICE_SOCKET_STATISTIC_PATH,
            params={FIELD_SMART_SOCKET_ID: str(smart_socket_id)},
        )
        self.last_device_period_stat_responses[
            f"{DEVICE_SOCKET_STATISTIC_PATH}:{smart_socket_id}:panel"
        ] = data
        return self._payload_dict(data, DEVICE_SOCKET_STATISTIC_PATH)

    async def async_get_device_socket_stat(
        self,
        device_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve socket-chart statistics for a device over a specified period.

        If the returned payload is non-empty, it will include `APP_REQUEST_META` containing the request parameters (`dateType`, `beginDate`, `endDate`) used to produce the chart (excluding `deviceId`/`systemId`).

        Returns:
            dict: The normalized `data` payload for the device socket chart.
        """
        return await self._async_get_device_period_stat(
            DEVICE_SOCKET_STAT_PATH,
            device_id=device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_home_trends(
        self,
        system_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """GET /v1/device/stat/sys/home/trends — home consumption breakdown."""
        begin_date, end_date = app_period_date_bounds(
            date_type, begin_date=begin_date, end_date=end_date
        )
        params = {
            FIELD_SYSTEM_ID: str(system_id),
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        data = await self._get_json(
            HOME_TRENDS_PATH, params=params, request_timeout=SLOW_ENDPOINT_TIMEOUT_SEC
        )
        payload = self._payload_dict(data, HOME_TRENDS_PATH)
        if payload:
            payload.setdefault(
                APP_REQUEST_META,
                {k: v for k, v in params.items() if k != FIELD_SYSTEM_ID},
            )
        return payload

    async def async_get_battery_trends(
        self,
        system_id: str | int,
        *,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict:
        """Retrieve battery charge and discharge trends for the given system.

        If the returned payload is non-empty, attaches request metadata under `APP_REQUEST_META`
        containing the request's `dateType`, `beginDate`, and `endDate`.

        Returns:
            dict: Normalized payload dictionary extracted from the API response (may be empty).
        """
        begin_date, end_date = app_period_date_bounds(
            date_type, begin_date=begin_date, end_date=end_date
        )
        params = {
            FIELD_SYSTEM_ID: str(system_id),
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        data = await self._get_json(
            BATTERY_TRENDS_PATH,
            params=params,
            request_timeout=SLOW_ENDPOINT_TIMEOUT_SEC,
        )
        payload = self._payload_dict(data, BATTERY_TRENDS_PATH)
        if payload:
            payload.setdefault(
                APP_REQUEST_META,
                {k: v for k, v in params.items() if k != FIELD_SYSTEM_ID},
            )
        return payload

    # --- New statistics endpoints -------------------------------------------

    async def _async_get_period_stat(  # noqa: PLR0913
        self,
        path: str,
        *,
        device_id: str | int | None = None,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
        system_id: str | int | None = None,
    ) -> dict[str, Any]:
        """Generic period-based stat fetcher (symmetry, cutoff, SOC, etc.)."""
        begin_date, end_date = app_period_date_bounds(
            date_type, begin_date=begin_date, end_date=end_date
        )
        params: dict[str, str] = {
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        if device_id is not None:
            params[FIELD_DEVICE_ID] = str(device_id)
        if system_id is not None:
            params[FIELD_SYSTEM_ID] = str(system_id)
        data = await self._get_json(path, params=params)
        return self._payload_dict(data, path)

    async def async_get_symmetry_stat(
        self,
        *,
        device_sn: str,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """GET /v1/device/stat/symmetry — charge/discharge symmetry stats.

        Parameters:
            device_sn: Device serial number.
            date_type: Period granularity.
            begin_date: Start date (computed if None).
            end_date: End date (computed if None).

        Returns:
            dict: Symmetry statistics payload.
        """
        return await self._async_get_period_stat(
            SYMMETRY_STAT_PATH,
            device_id=device_sn,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_cutoff_stat(
        self,
        *,
        device_sn: str,
        begin_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """GET /v1/device/stat/cutoff — power outage statistics.

        Parameters:
            device_sn: Device serial number.
            begin_date: Start date (computed if None).
            end_date: End date (computed if None).

        Returns:
            dict: Cutoff statistics payload.
        """
        return await self._async_get_period_stat(
            CUTOFF_STAT_PATH,
            device_id=device_sn,
            date_type="day",
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_get_soc_stat(
        self,
        *,
        device_id: str | int,
    ) -> dict[str, Any]:
        """GET /v1/device/stat/soc — state of charge statistics.

        Parameters:
            device_id: Device identifier.

        Returns:
            dict: SOC statistics payload.
        """
        data = await self._get_json(
            SOC_STAT_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        return self._payload_dict(data, SOC_STAT_PATH)

    async def async_get_carbon_stat(
        self,
        *,
        device_sn: str,
    ) -> dict[str, Any]:
        """GET /v1/device/stat/carbon — carbon offset contribution.

        Parameters:
            device_sn: Device serial number.

        Returns:
            dict: Carbon statistics payload.
        """
        data = await self._get_json(
            CARBON_STAT_PATH, params={FIELD_DEVICE_ID: str(device_sn)}
        )
        return self._payload_dict(data, CARBON_STAT_PATH)

    async def async_get_profit_stat(
        self,
        *,
        device_id: str | int,
    ) -> dict[str, Any]:
        """GET /v1/device/stat/profit — revenue/profit statistics.

        Parameters:
            device_id: Device identifier.

        Returns:
            dict: Profit statistics payload.
        """
        data = await self._get_json(
            PROFIT_STAT_PATH, params={FIELD_DEVICE_ID: str(device_id)}
        )
        return self._payload_dict(data, PROFIT_STAT_PATH)

    async def async_get_box_stat(
        self,
        *,
        device_sn: str,
        date_type: str = DATE_TYPE_DAY,
        begin_date: str | None = None,
        end_date: str | None = None,
        key: str = "",
    ) -> dict[str, Any]:
        """GET /v1/device/stat — box electricity statistics.

        Parameters:
            device_sn: Device serial number.
            date_type: Period granularity.
            begin_date: Start date (computed if None).
            end_date: End date (computed if None).
            key: Stat key filter.

        Returns:
            dict: Box statistics payload.
        """
        begin_date, end_date = app_period_date_bounds(
            date_type, begin_date=begin_date, end_date=end_date
        )
        params: dict[str, str] = {
            "deviceSn": device_sn,
            APP_REQUEST_DATE_TYPE: date_type,
            APP_REQUEST_BEGIN_DATE: str(begin_date),
            APP_REQUEST_END_DATE: str(end_date),
        }
        if key:
            params["key"] = key
        data = await self._get_json(BOX_STAT_PATH, params=params)
        return self._payload_dict(data, BOX_STAT_PATH)

    async def async_get_smart_schedule_prediction(
        self,
        *,
        system_id: str | int,
    ) -> dict[str, Any]:
        """GET /v1/device/stat/getSmartSchedulePrediction — AI smart schedule.

        Parameters:
            system_id: System identifier.

        Returns:
            dict: Smart schedule prediction payload.
        """
        data = await self._get_json(
            SMART_SCHEDULE_PATH, params={FIELD_SYSTEM_ID: str(system_id)}
        )
        return self._payload_dict(data, SMART_SCHEDULE_PATH)
