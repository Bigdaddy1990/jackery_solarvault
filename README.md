# Jackery SolarVault for Home Assistant

Languages:
[English](./README.md) · [Deutsch](./docs/README.de.md) · [Français](./docs/README.fr.md) · [Español](./docs/README.es.md)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Community integration for Jackery SolarVault systems, especially SolarVault 3 Pro Max. It reads live values, energy statistics and configurable settings from the Jackery cloud, and uses MQTT push for fast live updates and control commands.

This integration is not an official Jackery product and is not affiliated with Jackery Inc.

## What it provides

- Automatic system and device discovery through your Jackery cloud account.
- Main unit, smart meter and expansion batteries as separate Home Assistant devices.
- Live power sensors for battery, PV total, PV channels, grid import/export, EPS, stack power and smart meter phases.
- Energy sensors for Jackery app periods: day, week, month and year.
- Configurable entities for EPS, standby, limits, output power, smart meter follow mode, storm warning, temperature unit and electricity price.
- Device restart button and cloud services for system name and storm-alert management.
- Diagnostics for raw redacted data, MQTT status, firmware, system limits and data-quality warnings.

## Requirements

- Home Assistant 2025.8.0 or newer.
- Python 3.14 or newer, as provided by Home Assistant.
- A Jackery cloud account.
- SolarVault online through Wi-Fi or Ethernet.
- HACS for the recommended installation method.

## Recommended Jackery Account Setup

Jackery effectively allows only one active session per account. If the official Jackery app and Home Assistant use the same account at the same time, tokens and MQTT credentials can rotate. That may cause expired-token errors, MQTT authentication errors or temporary stale data.

Recommended setup:

1. Create a second Jackery account.
2. Share the SolarVault with that second account in the Jackery app.
3. Use the second account only for Home Assistant.

## Installation

### HACS

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)

1. Open HACS.
2. Open the three-dot menu.
3. Select `Custom repositories`.
4. Add `https://github.com/Bigdaddy1990/jackery_solarvault` as an `Integration`.
5. Search for `Jackery SolarVault` and install it.
6. Restart Home Assistant.
7. Go to `Settings > Devices & services > Add integration`.
8. Select `Jackery SolarVault`.

### Manual

1. Download the ZIP from the [Releases page](https://github.com/Bigdaddy1990/jackery_solarvault/releases).
2. Copy `custom_components/jackery_solarvault` to `<HA-config>/custom_components/`.
3. Restart Home Assistant.
4. Add `Jackery SolarVault` from `Settings > Devices & services`.

## Setup and Options

The setup flow asks for:

- Jackery cloud email address.
- Jackery cloud password.
- Whether calculated smart-meter sensors should be created.
- Whether calculated net-power sensors should be created.
- Whether savings-calculation detail sensors should be created.

Device ID, system ID, MQTT `macId` and region are derived from cloud and MQTT data. They are not entered manually.

The same options can be changed later from the integration options. Credentials can be updated through Home Assistant's reconfigure or reauth flow without deleting the integration.

## Devices and Entities

### Main SolarVault Device

Typical sensors:

- State of charge.
- Battery charge and discharge power.
- PV total power and PV1 to PV4 power.
- Grid import, grid export and grid net power.
- Grid-side input and output power.
- EPS power.
- Stack charge and discharge power.
- Other load power.
- Electricity price.
- App day/week/month/year energy values.
- Active alarm count.

Typical controls:

- EPS output.
- Standby.
- Auto-off in off-grid mode and auto-off time.
- Charge and discharge limits.
- Feed-in power limit.
- Maximum output power.
- Default output power.
- Follow smart meter.
- Energy consumption mode.
- Price mode and flat-rate price.
- Temperature unit.
- Storm warning and warning lead time.
- Restart.

### Expansion Batteries

Expansion batteries are created as separate devices when Jackery provides their data. Up to five batteries are supported. Depending on the payload, each battery can expose:

- State of charge.
- Cell temperature.
- Charge and discharge power.
- Firmware version.
- Serial number.
- Communication status attributes.

### Smart Meter

When a Jackery smart meter is connected, it is created as its own device. It can expose:

- Total meter power.
- Phase 1, phase 2 and phase 3 power.
- Raw meter attributes for diagnostics.
- Calculated home-consumption sensors when the option is enabled.

## Services

The integration registers these services under `jackery_solarvault`:

| Service | Purpose |
|---|---|
| `jackery_solarvault.rename_system` | Rename the SolarVault system in the Jackery cloud |
| `jackery_solarvault.refresh_weather_plan` | Fetch the current storm-warning plan |
| `jackery_solarvault.delete_storm_alert` | Delete an active storm alert through a cloud command |

Use `services.yaml` or `Developer Tools > Services` in Home Assistant for the required service parameters.

## Energy Dashboard and Sensor Meaning

Use the energy sensors carefully. Jackery exposes several values that sound similar but have different meanings.

- Battery discharge power is what the battery is delivering.
- Grid net power is grid import minus grid export. It does not have to match battery discharge power because PV, house load, smart meter values and internal regulation sit in between.
- Stack input/output describes the expansion-battery stack or the power flow between the main unit and expansion batteries.
- Smart meter values come from the connected meter and are handled separately from main-unit values.
- `Current house consumption` uses Jackery's live house-load value (`otherLoadPw`) when available. If that value is missing, the integration falls back to smart-meter net power minus Jackery grid-side input plus Jackery grid-side output.
- `Daily on-grid output (Jackery cloud)` is the Jackery `todayLoad` field. It is not reliable as real household consumption. For household consumption, use the calculated smart-meter/home-consumption sensors when available.
- `App total savings` is the raw Jackery app KPI. It may look like PV revenue. `Calculated savings` is the local estimate based on self-consumed AC energy, grid-side input/output, optional public export, house consumption and the configured electricity price.

For Home Assistant Energy Dashboard configuration, prefer real cumulative/day values and the calculated home-consumption sensors. Do not treat week, month or year period sensors as lifetime utility meters.

Savings calculation details are documented in [`docs/APP_CLOUD_VALUES.md`](docs/APP_CLOUD_VALUES.md).

## Period Rules and Data Quality

The integration uses the same local period boundaries as the Jackery app:

- Week: Monday to Sunday.
- Month: calendar month.
- Year: calendar year.

Important behavior:

- Period sensors are period totals, not lifetime counters.
- Weekly values are not used to repair monthly, yearly or lifetime values.
- When Jackery returns a current-month value as a yearly or lifetime generation/carbon value, the integration may guard it upward with explicit same-endpoint monthly values from the same calendar year.
- `App total savings` stays the raw cloud value. The calculated savings value is separate.
- At the start of a month, a weekly value can be higher than the monthly value if the current week includes days from the previous month. That is expected.
- If Jackery returns contradictory data that cannot be guarded safely, the integration creates a Home Assistant repair issue and stores details in the diagnostics export under `data_quality`.

## Polling, MQTT and TLS

MQTT push is the primary live update path once connected. HTTP polling remains as startup, fallback and keep-alive path:

- Fast HTTP refresh uses a 30-second base interval.
- When MQTT is live, fast HTTP ticks are skipped and a full HTTP refresh is kept to a slower keep-alive cadence.
- Slow cloud statistics and price/config data are queried less frequently because the Jackery cloud does not update them every second.

The MQTT TLS connection verifies the broker certificate chain and hostname. The integration includes `custom_components/jackery_solarvault/jackery_ca.crt` as a trust anchor for `emqx.jackeryapp.com` because the Jackery broker certificate is not signed by a public CA. There is no automatic insecure TLS fallback. TLS status is visible in the diagnostics export.

Implementation details for the TLS handling are documented in [`docs/STRICT_WORK_INSTRUCTIONS.md`](docs/STRICT_WORK_INSTRUCTIONS.md).

## Diagnostics and Troubleshooting

For authentication or MQTT problems, download diagnostics from:

`Settings > Devices & services > Jackery SolarVault > three-dot menu > Download diagnostics`

Sensitive fields are redacted. MQTT topic paths are exported as `hb/app/**REDACTED**/...`; the raw Jackery user ID is not included. The diagnostics export also contains counters for dropped payloads, MQTT connection timestamps and data-quality warnings.

Enable normal debug logging when investigating a problem:

```yaml
logger:
  default: info
  logs:
    custom_components.jackery_solarvault: debug
```

Raw HTTP/MQTT payload debug logging is separate and intentionally opt-in. It only writes `/config/jackery_solarvault_payload_debug.jsonl` when this dedicated logger is set to `debug`:

```yaml
logger:
  logs:
    custom_components.jackery_solarvault.payload_debug: debug
```

The payload debug file is throttled and rotated to `jackery_solarvault_payload_debug.jsonl.1` at 2 MB. On normal installations it does not exist.

Home Assistant brand icons are loaded from the local brand cache at `/homeassistant/.cache/brands/integrations/jackery/` when available.

## Reference Documentation

- [`docs/APP_CLOUD_VALUES.md`](docs/APP_CLOUD_VALUES.md): Jackery app/cloud values and savings calculation.
- [`docs/DATA_SOURCE_PRIORITY.md`](docs/DATA_SOURCE_PRIORITY.md): MQTT, HTTP and app-statistics source priority.
- [`docs/MQTT_PROTOCOL.md`](docs/MQTT_PROTOCOL.md): MQTT topics and payload contracts.
- [`docs/APP_POLLING_MQTT.md`](docs/APP_POLLING_MQTT.md): HTTP and MQTT polling details.

## Contributing

Please submit bug reports and feature requests through [GitHub Issues](https://github.com/Bigdaddy1990/jackery_solarvault/issues). For authentication, MQTT or data-quality problems, include a Home Assistant diagnostics export when possible. Sensitive fields are automatically redacted, but still review the file before sharing it publicly.

## License

MIT License. See [LICENSE](LICENSE).
