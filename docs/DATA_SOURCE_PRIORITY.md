# Data source priority — Jackery SolarVault

This document is the contract between the Jackery cloud APIs and the Home
Assistant entities exposed by the integration. Every period entity carries
the section name and stat key from this document in its
`extra_state_attributes` so a parser/source bug stays visible without a
debug log.

## Period boundaries

The integration follows the same period semantics that the Jackery app
shows next to its day/week/month/year toggles, **localised to the user's
Home Assistant timezone**:

* **Day** — local calendar day, `00:00:00` to `23:59:59`.
* **Week** — Monday to Sunday, ISO 8601, in the user's local timezone.
* **Month** — full calendar month, day 1 `00:00` to last-day `23:59`.
* **Year** — full calendar year, January 1st `00:00` to December 31st
  `23:59`.

App-period `year` chart arrays carry one bucket per month
(12 entries). The integration imports them as Home Assistant external
statistics with monthly buckets dated to the **first day of each month**.

## No cross-period repair

The integration must never silently fix one period using another period.
This rule is enforced by tests and matches the explicit guidance from the
Jackery app behaviour.

* Do not use day values to repair week/month/year/lifetime totals.
* Do not use week values to repair month/year/lifetime totals.
* Do not use month values to repair year/lifetime totals.
* Do not use year values to repair lifetime totals.

When the cloud returns inconsistent values (e.g. lifetime statistic
`81.95 kWh` while the year chart sums to `139 kWh`) the integration
emits a Home Assistant **Repair issue** plus a `data_quality` entry on
the diagnostics export. The entity itself **must not rewrite the entity state with another period**; the documented source value is preserved so
the problem stays visible.

The same rule applies to the date range used in the period requests:
`month` and `year` must never fall back to `beginDate=endDate=today`
on a quiet failure. The integration retries with exponential backoff and
preserves the last good payload until the documented range is fetched.

## Source-of-truth fields per period entity

| Period | Endpoint | Total field | Chart series |
|---|---|---|---|
| Day | `/v1/device/stat/pv?dateType=day&beginDate=YYYY-MM-DD&endDate=YYYY-MM-DD` | `totalSolarEnergy` | `y` (24 hourly buckets) |
| Week | `/v1/device/stat/pv?dateType=week&beginDate=YYYY-MM-DD&endDate=YYYY-MM-DD` | `totalSolarEnergy` | `y` (7 daily buckets) |
| Month | `/v1/device/stat/pv?dateType=month&beginDate=YYYY-MM-01&endDate=YYYY-MM-LAST` | `totalSolarEnergy` | `y` (28-31 daily buckets) |
| Year | `/v1/device/stat/pv?dateType=year&beginDate=YYYY-01-01&endDate=YYYY-12-31` | `totalSolarEnergy` | `y` (12 monthly buckets) |

The same shape applies to `device_battery_stat_*` (`totalCharge` / `y1`,
`totalDischarge` / `y2`) and `device_home_stat_*` (`totalInGridEnergy` /
`y1`, `totalOutGridEnergy` / `y2`).

For year-period entities the integration cross-validates the raw chart
sum against the documented total field. When they match, the raw
floating-point chart values are used as-is. When they diverge but the
"compact two-month encoding" expansion matches the total, the compact
expansion is applied. This avoids the historical bug where every
floating-point chart value was incorrectly interpreted as an
encoded two-month bucket.

## Minimal entity diagnostic attributes

Every period entity exposes a stable, minimal set of attributes for
debugging — never the raw payload. These are written from the
coordinator into the per-update sensor cache.

* `source_section` — the section name in the merged coordinator payload
  (e.g. `device_pv_stat_year`).
* `source_key` — the documented total field name (e.g.
  `totalSolarEnergy`).
* `chart_series_key` — when the entity reads from a chart series, the
  array key (e.g. `y`, `y1`, ...).
* `chart_series_sum` — sum of the chart series, rounded.
* `server_total` — value of the documented total field as reported by
  the cloud.
* `period_labels`, `period_labels_count`, `period_labels_json` — the
  `x` axis labels of the chart, when present.
* `period_values_count`, `period_values_json`,
  `period_values_by_label_json` — the chart values aligned to the labels.
* `request` — the original `dateType`/`beginDate`/`endDate` request
  metadata.

Earlier internal-only attributes were dropped from the schema; tests
lock the cleaned-up attribute set above.

## Raw payload debugging

Raw HTTP responses and MQTT frames are **never** written by default.
They are only logged to `jackery_solarvault_payload_debug.jsonl`. The
file is enabled only when logger `custom_components.jackery_solarvault.payload_debug`
is at DEBUG. On normal installations no file is created.

The log writer applies a per-channel content-aware dedup plus a
`PAYLOAD_DEBUG_THROTTLE_SEC = 60` throttle so the file does not grow at
the MQTT push-rate. Every genuinely new content fingerprint is written
immediately; repetitions within the throttle window are skipped.

The file rotates at 2 MB to a `.jsonl.1` backup. Both files are listed
in `.gitignore` and excluded from any release artefact.
