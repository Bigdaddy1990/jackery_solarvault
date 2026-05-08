# Jackery SolarVault data-source priority

This file is the implementation contract for choosing between MQTT, HTTP and
app-statistic values. It prevents hidden cross-period repairs and keeps Home
Assistant entity states explainable.

## Source classes

| Source class | Preferred use | Notes |
| --- | --- | --- |
| MQTT live payload | Live power, switch/select state, fast diagnostic attributes | Most recent value wins for live state. |
| HTTP device property | Startup/backfill and slow fallback for live properties | Used when MQTT has not delivered a value yet. |
| App statistic endpoint | Today/week/month/year energy totals | Use the matching documented app period; year values may be guarded by same-endpoint month payloads when Jackery returns the current month as the year. |
| App chart series | HA statistic graph / period bucket diagnostics | Sum the documented series for period total sensors. |
| Same-endpoint month backfill | Guarded year lower bound | Only for elapsed months of the same calendar year and same endpoint family. |
| Lifetime app statistic | Total generation/revenue/carbon | Preferred source for generation/carbon; guarded so those values cannot be lower than the corrected current-year PV total. `totalRevenue` remains the raw app KPI; calculated savings are exposed separately via `_savings_calculation`. |


## Minimal entity diagnostic attributes

Period and total sensors keep `extra_state_attributes` lean. Entity attributes
may expose the concrete source section/key, parsed period values, the app
request range, `_year_month_backfill` metadata, `_total_lower_bound_guard`
metadata when those guards actually changed the value, and
`_savings_calculation` metadata for the total-revenue/savings sensor.

Raw parser proof, ambiguous cloud payloads, and redacted source snapshots belong
in the generated `jackery_solarvault_payload_debug.jsonl` file and Home
Assistant diagnostics, not as large per-entity attributes.

## Non-negotiable rules

- Do not use week values to repair month/year/lifetime totals.
- Month values may only guard year totals when they come from the same endpoint family and explicit calendar-month requests of the same year.
- Do not use year values to repair lifetime totals.
- Period entities expose the matching app period, except for the documented same-endpoint month backfill on faulty year payloads.
- Diagnostics must show when a year value was raised by month backfill.
- If sources still contradict each other after the guarded backfill, keep the guarded source and expose enough attributes/logging to diagnose the app/API issue.

## Same-Endpoint Month Backfill

Some Jackery app/cloud responses return the current month as the `dateType=year`
value. The app chart then has twelve month slots but only the current month is
non-zero, for example May `81.51 kWh` shown as the whole year even though April
has `146.51 kWh` in the monthly app statistic view.

When this shape is detected after January, the coordinator fetches explicit
`dateType=month` payloads for elapsed months of the same calendar year and
same endpoint family:

- `/v1/device/stat/pv` guards `device_pv_stat_year` including PV1..PV4 and solar revenue.
- `/v1/device/stat/battery` guards battery charge/discharge year values.
- `/v1/device/stat/onGrid` guards device grid-side input/output year values.
- `/v1/device/stat/sys/home/trends` guards home-consumption year values.

The guard is one-way:

- If the cloud year total is greater than or equal to the month sum, keep the
  cloud year. This lets a future fixed Jackery response win automatically.
- If the month sum is greater, use the month sum as a lower bound and attach
  `_year_month_backfill` metadata with raw and corrected totals.
- If historical month payloads are missing or lower than the cloud year, do not
  lower the year value.

`systemStatistic` total generation/carbon uses the corrected current year PV
value as a lower bound only. A genuine lifetime total from the cloud is kept
when it is higher; a month-only total is raised so Home Assistant does not see a
false total drop.

`systemStatistic.totalRevenue` / `total_revenue` remains the raw Jackery app
savings KPI. It can look like PV revenue, so the integration exposes a separate
calculated savings detail from year-flow data when available:

- Start with `device_home_stat_year.totalOutGridEnergy`, the device grid-side
  AC output after inverter and battery effects, and subtract
  `device_home_stat_year.totalInGridEnergy` when present so grid-sourced energy
  is not counted as PV savings.
- Subtract `device_ct_stat_year.totalOutCtEnergy` when CT period statistics are
  present, because public grid export is not house self-consumption.
- Bound the result by `home_trends_year.totalHomeEgy`.
- Multiply by `price.singlePrice`; if the price payload is missing, derive the
  tariff from PV year revenue divided by PV year kWh.

The cloud `totalRevenue` value is not replaced. The entity exposes
`_savings_calculation` for the raw cloud value, calculated value, method,
components, and whether older versions would have replaced the cloud value.

## Period ranges

The app period endpoints must always be queried with explicit ranges from
`APP_POLLING_MQTT.md`:

- `day`: today .. today
- `week`: Monday .. Sunday
- `month`: first day .. last day of the calendar month
- `year`: January 1 .. December 31

`month` and `year` must never fall back to `beginDate=endDate=today`.

## Contradictory app/cloud data

When mathematically comparable sources contradict each other after the guarded
month backfill, the integration attaches `data_quality` diagnostics to the
affected device payload and raises the `app_data_inconsistency` repair issue.

Examples that are valid to flag:

- `year < month` for the same metric and source family.
- `year < week` when the full current week is inside the current year.
- `month < week` only when the full current week is inside the current month.
- lifetime PV generation `<` current-year PV generation.

Examples that are not valid to flag:

- `month < week` at the start/end of a month when the week spans another month.
- `year < week` around New Year when the week spans another year.

The warning is diagnostic only. It protects the user from silent cross-period
"repairs" while making broken Jackery responses visible in Home Assistant.

## Repair issue presentation

The repair issue uses a deterministic, de-duplicated warning list. This prevents
Home Assistant from seeing a changed issue payload on every refresh just because
device iteration order changed or the same contradiction was discovered through
multiple payload paths.

Repair text may include a small number of examples, but it must not include raw
device IDs, serial numbers, tokens, coordinates or MQTT credentials. Full source
details belong in redacted diagnostics under `data_quality`.
