# Jackery SolarVault — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A custom [Home Assistant](https://www.home-assistant.io/) integration for Jackery
SolarVault / HomePower home-energy systems and Explorer portable power stations.
It talks to the Jackery cloud over HTTPS as the primary, authoritative data path,
and layers optional MQTT push and Bluetooth (BLE) transports on top for lower
latency — without ever letting those supplemental layers block the HTTP path.

> **Status:** Targets the Home Assistant *Platinum* quality scale. Configuration
> is UI-only (config entries); there is no YAML configuration.

## High-level description

The integration signs in to a Jackery cloud account and exposes each bound power
station as a Home Assistant device with sensors (power, state-of-charge, energy,
temperatures, grid/PV flows), configuration entities (charge/discharge limits,
output priorities, working mode, output timers), and actions (services) that
mirror the Jackery mobile app — including device binding, QR-code sharing,
time-of-use scheduling, and Shelly cloud-to-cloud linking.

Long-run energy statistics are backfilled into Home Assistant's long-term
statistics from 5-minute samples up through weekly, monthly and yearly period
totals.

## Supported devices

- **Home energy systems:** Jackery SolarVault / HomePower series (reported by the
  cloud as `Powerstation` devices, carrying `HomeBody` / `SystemBody` / `BoxBody`
  telemetry).
- **Explorer portable power stations:** `E240`, `E557`, `E900`, `E1000`,
  `E1500V2`, `E1800`, `E2000`, `E3000`, `E7647`, `E7987` (portable `PortableBody`
  telemetry and controls).
- **Sub-devices / accessories:** battery packs, CT / smart meters, smart plugs and
  sockets, meter heads, and Shelly cloud sockets bound to the account.

## Supported functions

Entities are created across these platforms:

| Platform | Examples |
|----------|----------|
| `sensor` | SOC, input/output/PV power, grid in/out, temperatures, remaining runtime, energy period totals |
| `binary_sensor` | charging / online / fault flags |
| `number` | charge power, energy-storage charge limit, per-port output-priority SOC, custom-use battery bounds, output countdowns, AC output delay-open time, Bluetooth sleep time |
| `select` | working mode, charge mode, battery mode, output priority (master + per AC1/AC2/DC port), UPS model, temperature unit, CT phase, electricity price mode |
| `switch` | EPS output, AC/DC outputs, output-priority master, discharge memory, energy saving, super charge |
| `button` | reboot, power-pack blink, plan queries |
| `text` | Wi-Fi / diagnostic identifiers |

## Installation

### HACS (recommended)

1. In HACS, add this repository as a custom repository (category: *Integration*).
2. Install **Jackery SolarVault**.
3. Restart Home Assistant.

### Manual

Copy `custom_components/jackery_solarvault` into your Home Assistant
`config/custom_components/` directory and restart Home Assistant.

## Configuration

Add the integration from **Settings → Devices & Services → Add Integration →
Jackery SolarVault**, then complete the config flow.

### Configuration parameters

| Parameter | Required | Description |
|-----------|----------|-------------|
| Account e-mail | yes | The Jackery cloud account e-mail. Use a **dedicated** account (see limitations). |
| Password | yes | The Jackery cloud account password. |
| Enable BLE transport | no | Use Bluetooth as a supplemental low-latency transport when the device is in range. |
| Local MQTT host / port / username / password | no | Point at a local MQTT broker that the device has been provisioned to publish to. |
| Third-party MQTT host / port / username / password | no | Credentials for a third-party MQTT relay. |

MQTT and BLE are **supplemental**: the HTTP cloud path always runs and remains the
source of truth. Missing MQTT/BLE settings simply disable those layers.

## Removal

Remove the integration from **Settings → Devices & Services**, open the Jackery
SolarVault entry's menu and choose **Delete**. All devices, entities and the
stored account credentials are removed with the config entry. To fully uninstall
the code, remove it from HACS (or delete `custom_components/jackery_solarvault`)
and restart Home Assistant.

## Data updates

- **HTTP cloud (primary):** polled on a fixed interval; this is the authoritative
  data path and the only one that performs authentication.
- **MQTT push (supplemental, "Layer 5"):** merges live device-property changes
  into coordinator data between polls. It never gates or delays an HTTP fetch.
- **BLE (supplemental):** optional local transport for command delivery and live
  updates when the device is in Bluetooth range.

Energy figures are aggregated into long-term statistics from 5-minute samples up
to week / month / year period totals.

## Actions (services)

The integration registers 60+ services under the `jackery_solarvault` domain that
mirror the app. Notable examples:

- **Device & pairing:** `bind_device`, `unbind_device`, `set_device_nickname`,
  `get_share_qr_code` (QR-code sharing), `accept_shared_device`,
  `list_shared_devices`, `check_system_bound`.
- **Cloud-to-cloud (Shelly):** `get_shelly_auth_url`, `list_shelly_devices`,
  `unbind_shelly_device`, `unbind_shelly_account`.
- **Accessories / sub-devices:** `list_accessories`, `get_accessories`,
  `set_accessory_name`, `bind_smart_part`, `unbind_smart_part`,
  `refresh_subdevices`, `query_socket_stat`.
- **Energy scheduling & tariffs:** `save_tou_plan`, `query_tou_plan`,
  `insert_electricity_strategy`, `update_electricity_strategy`,
  `save_dynamic_price_contract_auth`, `bind_currency`.
- **Statistics:** `query_charge_report`, `query_soc_stat`, `query_carbon_stat`,
  `query_profit_stat`, `get_offline_statistics`.
- **Maintenance:** `rename_system`, `save_device_max_power`, `send_ble_command`,
  `set_third_party_mqtt_config`, `sync_alerts`.

See `custom_components/jackery_solarvault/services.yaml` for the full list and
each service's fields.

## Examples

Notify when the battery drops below 20 %:

```yaml
automation:
  - alias: "Jackery low battery"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.jackery_solarvault_state_of_charge
        below: 20
    actions:
      - action: notify.mobile_app
        data:
          message: "Jackery SolarVault battery is below 20%."
```

Save a time-of-use schedule via a service call:

```yaml
action: jackery_solarvault.save_tou_plan
data:
  device_id: "<your device id>"
  body:
    tasks:
      - start: "04:00"
        end: "06:00"
        loops: "1111111"
        pw: 700
        sysSwitch: 1
```

## Known limitations

- **One active cloud session per account.** Jackery allows only a single active
  session per account. Use a **dedicated HA-only account** and share the
  SolarVault with it. Sharing the same account with the mobile app causes MQTT
  auth failures from token rotation.
- **Custom TLS CA.** The MQTT broker (`emqx.jackeryapp.com`) is signed by a custom
  CA (`jackery_ca.crt`) shipped with the integration; there is no insecure
  fallback.
- **Period energy sensors are not cumulative.** Week (Mon–Sun), month and year
  sensors report period totals, not lifetime values — do not feed them into the
  Energy Dashboard as lifetime totals.

## Troubleshooting

- **MQTT keeps re-authenticating / drops:** the account is probably shared with
  the mobile app. Use a dedicated account (see limitations).
- **No live updates, only polled values:** MQTT/BLE are optional; verify the MQTT
  settings, or rely on the HTTP polling interval.
- **Entities show *unavailable* after a restart:** the first cloud poll must
  succeed; transient auth/timeout failures keep the last known data and retry on
  the next interval.
- **Contradictory monthly/yearly figures:** upward "repair" of contradictory
  period values is intentionally blocked by data-quality guards.

## Use cases

- Monitor SOC, PV production, grid import/export and load in real time.
- Automate output ports, charge/discharge limits and working mode from Home
  Assistant automations.
- Drive time-of-use / peak-trough charging schedules and dynamic electricity
  pricing.
- Track carbon savings and profit statistics, and integrate Shelly cloud sockets
  bound to the same account.

## License

See [LICENSE](LICENSE).
