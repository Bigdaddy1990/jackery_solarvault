# Jackery SolarVault 3 Pro Max Home Assistant Integration

**🌍 Language / Sprache / Idioma / Langue:**
[🇬🇧 English](README.md) · [🇩🇪 Deutsch](docs/README.de.md) · [🇫🇷 Français](docs/README.fr.md) · [🇪🇸 Español](docs/README.es.md)

---

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)

Community integration for Jackery SolarVault systems, especially SolarVault 3 Pro Max. The integration reads live values, energy statistics, and configurable parameters from the Jackery cloud and uses MQTT push for fast status updates and control commands.

> ⚠️ This integration is not an official Jackery product and is not affiliated with Jackery Inc.


## Features

- Automatic device and system discovery through the Jackery account
- Regular HTTP refresh of standard values at a fixed 30-second interval
- MQTT push for live status, smart meter, expansion batteries, and control commands
- Main unit, smart meter, and expansion batteries as separate Home Assistant devices
- Support for up to 5 expansion batteries
- Live power: battery, total PV, PV channels, grid import, grid export, EPS, and expansion battery stack
- Energy statistics: day, week, month, and year for PV, consumption, and battery
- Long-term values suitable for the Energy Dashboard only for cumulative total/daily values; weekly/monthly/yearly values are display-only values
- Smart meter power including phase values, if a smart meter is connected
- Configuration via entities: EPS, charge/discharge limits, feed-in power limit, maximum output power, energy consumption mode, auto-off, smart meter follow, storm warning, temperature unit, electricity price, and standby
- Restart button for the device
- Diagnostic entities for online status, firmware, system limits, grid standard, country code, raw data, and MQTT state

## Installation via HACS

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)

1. Open HACS.
2. Open the three-dot menu in the top right.
3. Select `Custom repositories`.
4. Enter the repository URL `https://github.com/Bigdaddy1990/jackery_solarvault` and choose the category `Integration`.
5. Search for `Jackery SolarVault` and install it.
6. Restart Home Assistant.
7. Go to Settings → Devices & services → Add integration → `Jackery SolarVault`.

## Manual installation

1. Download the ZIP from the [Releases page](https://github.com/Bigdaddy1990/jackery_solarvault/releases).
2. Copy the folder `custom_components/jackery_solarvault` to `<HA-config>/custom_components/`.
3. Restart Home Assistant.
4. Add the integration via Settings → Devices & services.

## Setup

Required:

- Jackery cloud email address
- Jackery cloud password
- optional: enable/disable calculated smart meter sensors
- optional: enable/disable calculated power sensors

Device ID, system ID, MQTT macId, and region are derived from the cloud/MQTT data and are no longer requested manually in the UI.

## Important note about Jackery login

Jackery effectively allows only one active session per account. If the official app and Home Assistant are logged in with the same account at the same time, tokens and MQTT credentials may rotate. This can lead to expired tokens or MQTT authentication errors.

Recommended:

1. Create a second Jackery account.
2. Share the SolarVault with the second account in the Jackery app via sharing/QR code.
3. Use the second account in Home Assistant.

## Entities

### Standard sensors

- Total SOC and internal battery
- Battery charging power and battery discharging power
- Total PV power and PV channels 1-4
- Grid import, grid export, and net grid power
- Grid-side input/output power
- EPS power
- Expansion battery charging/discharging power
- Other load power
- Electricity price
- Active alarms
- Daily/weekly/monthly/yearly values for PV, consumption, and battery

### Expansion batteries

Expansion batteries are created separately from the main unit. For each detected battery, the following is shown where available:

- SOC
- Cell temperature
- Charging power
- Discharging power
- Firmware version
- Communication status as attributes

### Smart meter

The smart meter is created as its own device under the SolarVault. Supported values:

- Total power
- Phase 1 power
- Phase 2 power
- Phase 3 power
- Available raw values as attributes

### Configurable entities

- EPS output
- Standby
- Auto-off in off-grid mode (with auto-off time)
- Charge and discharge limit
- Feed-in power limit
- Maximum output power
- Default output power
- Follow smart meter
- Energy consumption mode
- Electricity price mode
- Flat-rate price
- Temperature unit
- Storm warning and warning lead time
- Restart

## Services

The integration registers three services in the `jackery_solarvault` namespace:

| Service | Purpose |
|---|---|
| `jackery_solarvault.rename_system` | Rename the system (SolarVault device) in the cloud |
| `jackery_solarvault.refresh_weather_plan` | Fetch the current storm warning plan from the cloud server |
| `jackery_solarvault.delete_storm_alert` | Delete an active storm alert via cloud command |

For details about the required parameters, see `services.yaml` or the HA Developer Tools → Services editor.

## Reading energy and power sensors correctly

- Battery discharging power shows what the battery is delivering.
- Net grid is grid import minus grid export. This value does not have to match the battery discharging power because house load, PV, smart meter, and internal regulation sit in between.
- Stack input/output refers to the expansion battery stack or the power flow between the main unit and the expansion batteries.
- Smart meter values come from the connected meter and are handled separately from the main unit values.
- The `Current house consumption` sensor calculates the instantaneous consumption from Jackery's reported live house consumption (`otherLoadPw`) and only uses smart meter net power minus Jackery grid-side input plus Jackery grid-side output as a fallback. This prevents SolarVault feed-in from being incorrectly subtracted from house consumption.
- Daily/weekly/monthly/yearly energy sensors use `state_class: total` with the appropriate `last_reset` for the respective app period. They are period values, not lifetime monotonically increasing counters.
- Weekly, monthly, and yearly values are calculated identically from the respective app chart series. The series depends on the payload: PV/home trend totals usually use `y`, battery charge/discharge uses `y1`/`y2`, device grid-side input/output uses `y1`/`y2`, and PV1..PV4 uses `y1`..`y4`. Server total fields are now only used as fallback/diagnostic values because monthly/yearly total fields may be misleading depending on the payload.

### Periods, totals, and warnings

- Week = Monday to Sunday.
- Month = calendar month.
- Year = calendar year.
- Total/lifetime values come from the documented app/HTTP/MQTT total fields and are not assembled from weekly, monthly, or yearly values.
- Weekly values are explicitly not used to repair monthly, yearly, or total values, and monthly values are not used to repair yearly or total values.
- At the start of a month, the weekly value can be higher than the monthly value if the current week still includes days from the previous month. That is not a bug.
- If Jackery provides contradictory data, for example a yearly value lower than a complete week within the same year or a total yield lower than the yearly yield, the integration does not silently change entity values. Instead, it creates a repair notice and stores details in the diagnostics export under `data_quality`.

## Polling and updates

Fast HTTP polling runs at a fixed 30-second interval. Slow cloud statistics are intentionally queried less frequently because Jackery does not update these data server-side every second.

MQTT push updates live values independently of polling as soon as the broker is connected.

The MQTT TLS connection actively verifies the broker certificate chain. The file ``custom_components/jackery_solarvault/jackery_ca.crt`` is included as a documented trust anchor for ``emqx.jackeryapp.com`` because Jackery does not have the broker signed by a public CA. On Python 3.10+/OpenSSL 3.x, the Strict flag ``VERIFY_X509_STRICT`` is additionally disabled in a targeted way because the server certificate does not provide the ``Authority Key Identifier`` extension. Hostname verification, chain verification, and signature verification remain active (``CERT_REQUIRED`` + ``check_hostname = True``). There is no automatic fallback to ``tls_insecure`` or ``CERT_NONE`` — TLS errors remain visible. The diagnostics export shows ``tls_custom_ca_loaded``, ``tls_x509_strict_disabled``, and ``tls_certificate_source`` under ``mqtt_status``, among other fields, so the TLS configuration can be checked without debug logging. Background information and change rules for this strategy are documented in ``docs/STRICT_WORK_INSTRUCTIONS.md``.

MQTT diagnostics data contains only redacted topic paths (`hb/app/**REDACTED**/...`), counters, and timestamps for connection, last message, last publish, and discarded payloads. The Jackery `userId` part of the topic is not included in the diagnostics export.

## Debug logging

For troubleshooting:

```yaml
logger:
  default: info
  logs:
    custom_components.jackery_solarvault: debug
```

## Requirements

- Home Assistant 2025.8.0 or newer
- Python 3.13+ (provided by Home Assistant)
- Jackery cloud account
- SolarVault online via Wi-Fi or Ethernet
- HACS for the recommended installation method

## Contributing

Please submit bug reports and feature requests through [GitHub Issues](https://github.com/Bigdaddy1990/jackery_solarvault/issues). For authentication or MQTT issues, a diagnostics export from HA (Settings → Devices & services → Jackery SolarVault → three dots → Download diagnostics) is very helpful. Sensitive fields are automatically redacted; nevertheless, briefly review a diagnostics export before sharing it.

## License

MIT License. See [LICENSE](LICENSE).
