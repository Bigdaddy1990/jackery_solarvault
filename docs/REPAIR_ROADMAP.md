# Repair roadmap

This roadmap keeps fixes biased toward the data pipeline that Home Assistant
actually runs, rather than preserving broken behavior with isolated unit tests.

## Phase 1: Establish real Home Assistant test infrastructure

- Keep `pytest-homeassistant-custom-component` wired through
  `requirements-test.txt`.
- Run the Home Assistant tests from `.github/workflows/validate.yml` with
  `pytest-ha.ini`.
- Prefer tests that exercise `raw HTTP/MQTT payload -> parser -> coordinator
  data -> entity native value`.

## Phase 2: Lock statistic source contracts

- Keep app period requests explicit for day, week, month, and year.
- Prevent unrelated period repair, especially week-to-month or week-to-year
  adjustments.
- Allow only documented same-endpoint month backfill for faulty year payloads.

## Phase 3: Keep diagnostics actionable

- Put raw payload proof in diagnostics or the redacted payload debug log.
- Keep entity attributes small and stable.
- Document every guard that changes a value so future cloud fixes can be
  accepted without code churn.
