# Test categories

The suite is organized by domain so coverage explains integration behavior instead of pull-request history.

## Domains

| Directory | Contract focus |
| --- | --- |
| `tests/protocol/` | HTTP endpoints, MQTT handlers, BLE frames, encryption, payload debug contracts. |
| `tests/stats/` | Backfill/statistics metadata and long-term statistic contracts. |
| `tests/entities/` | Sensor/binary-sensor/button native values, availability, entity descriptions, and service-visible outcomes. |
| `tests/ha/` | Home Assistant config flow, setup entry, startup orchestration, repairs, unload, and lifecycle behavior. |
| `tests/` root | Cross-domain quality gates, source-backed contracts, vendor smoke tests, and focused regressions. |

## Outcome expectations

Tests should prefer business outcomes: native values, availability, repairs, diagnostics, service exceptions, and protocol encoder/decoder contracts.
Private call-order assertions are reserved for protocol encoder/decoder boundaries where ordering is part of the wire contract.
