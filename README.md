# Jackery SolarVault for Home Assistant

Languages:
[English](./README.md) · [Deutsch](./docs/README.de.md) · [Français](./docs/README.fr.md) · [Español](./docs/README.es.md)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A custom [Home Assistant](https://www.home-assistant.io/) integration that brings your Jackery SolarVault, HomePower, and Explorer power stations directly into your smart home.

It combines Jackery Cloud API features with optional **Local MQTT** and **Bluetooth (BLE)** telemetry and supported commands. Feature availability depends on the device, firmware and backend; initial device provisioning still uses the Jackery app.

---

## 🏆 Why this integration is the best choice

You might have heard of other manual MQTT workarounds or older integrations. Here is why this integration is the clearly superior choice:

1. **Automatic Cloud Setup:** Setup uses your cloud credentials to discover devices and obtain available device keys and MQTT session material. You do not need to intercept network traffic or build JSON payloads for that discovery.
2. **Local Transports:** Optional **Local MQTT** and **Bluetooth (BLE)** provide local telemetry and supported commands after cloud setup. They do not disable cloud authentication or guarantee simultaneous use of the mobile app; see [Account sessions and local access](./docs/account-sessions.md).
3. **Cloud Features:** The integration exposes Time-of-Use scheduling, Shelly integration, firmware checks and advanced charging settings where supported. It does not implement the app's initial BLE Wi-Fi provisioning; QR, binding and sharing services are described in [Account sessions and local access](./docs/account-sessions.md#qr-codes-account-binding-and-sharing).

---

## 🔋 Supported Devices

This integration supports a wide range of Jackery devices, including:

- **Home Energy Systems:** Jackery SolarVault and HomePower series.
- **Portable Power Stations (Explorer Series):** E240, E557, E900, E1000, E1500V2, E1800, E2000, E3000, E7647, E7987.
- **Accessories:** Battery packs, Smart Meters, Smart Plugs, and linked Shelly cloud sockets.

---

## ✨ Features & Entities

The integration creates dozens of entities per device to give you full visibility and control:

| Platform | Examples |
|----------|----------|
| `sensor` | SOC, input/output/PV power, grid in/out, temperatures, remaining runtime, cumulative energy statistics |
| `binary_sensor` | charging, online, and fault status |
| `number` | charge power limit, energy-storage limits, custom battery bounds, AC output delay-open time |
| `select` | working mode, charge mode, battery mode, output priority, UPS model, electricity price mode |
| `switch` | EPS output, AC/DC outputs, energy saving, super charge |
| `button` | reboot, power-pack blink |
| `text` | Wi-Fi and diagnostic identifiers |

### 🛠️ Advanced Services

We also expose 60+ custom services in Home Assistant, giving you the power of the Jackery App in your automations:
- **Device Management:** `bind_device`, `unbind_device`, `get_share_qr_code`
- **Cloud-to-Cloud:** `get_shelly_auth_url`, `list_shelly_devices`
- **Energy Scheduling:** `save_tou_plan`, `insert_electricity_strategy`, `bind_currency`
- **Statistics:** `query_charge_report`, `query_soc_stat`, `query_profit_stat`

Jackery supports sharing only for eligible models. These generic cloud API services do not check model eligibility or enable SolarVault sharing.

---

## 🛠️ Installation

### HACS (Recommended)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)

1. Open HACS.
2. Open the three-dot menu.
3. Select `Custom repositories`.
4. Add `https://github.com/Bigdaddy1990/jackery_solarvault` as an `Integration`.
5. Search for `Jackery SolarVault` and install it.
6. Restart Home Assistant.
7. Go to `Settings > Devices & services > Add integration`.
8. Select `Jackery SolarVault`.

### Option 2: Manual

1. Download the latest release.
2. Copy the `custom_components/jackery_solarvault` folder into your Home Assistant `config/custom_components/` directory.
3. Restart Home Assistant.

---

## ⚙️ Configuration

1. Go to **Settings → Devices & Services**.
2. Click **Add Integration** and search for **Jackery SolarVault**.
3. Add and provision the device in the Jackery app first. Follow the HA setup wizard using the Jackery Cloud account that added it.

> [!WARNING]
> **SolarVault account limitation:** Use the account that added the SolarVault system. Jackery's App User Manual (§7.2) excludes SolarVault from device sharing, so a second Home Assistant account is not a workaround.
> **App and Home Assistant:** Concurrent cloud access with the same account is not reliably supported by this integration. Another login can replace a session; automatic re-login and MQTT backoff attempt recovery but cannot guarantee that the app stays signed in or that live updates continue. See [Account sessions and local access](./docs/account-sessions.md) for sources, troubleshooting and the separate official local-MQTT option.

### Configuration Options

- **Email & Password:** Credentials for the Jackery account that owns your SolarVault system.
- **Bluetooth (BLE):** Optional. Allows direct communication when your HA server is in Bluetooth range of the Jackery.
- **Local MQTT:** Optional. Use this if your device is configured to publish data to a local MQTT broker.

All discovery paths still lead to cloud-account setup. Saved bootstrap data and optional local transports do not provide a cloud-free mode in this integration.

---

## 💡 Automations & Examples

**Notify when the battery is low:**

```yaml
automation:
  - alias: "Jackery Low Battery Warning"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.jackery_solarvault_state_of_charge
        below: 20
    actions:
      - action: notify.notify
        data:
          message: "Your Jackery battery is below 20%!"
```

**Set a Time-of-Use Schedule:**

```yaml
action: jackery_solarvault.save_tou_plan
data:
  device_id: "<your_device_id>"
  body:
    tasks:
      - start: "04:00"
        end: "06:00"
        loops: "1111111"
        pw: 700
        sysSwitch: 1
```

---

## ❓ Troubleshooting

- **My integration keeps disconnecting or sensors say "unavailable":**
  If this coincides with app logins, a session conflict may be involved. For SolarVault, do not create a second account to share the system. To isolate the cause, disable this integration before signing in to the app, or sign out of the app before enabling Home Assistant again. Check HTTP authentication failures, MQTT rejections, broker reachability and device connectivity; reconnect attempts alone do not establish stable concurrent access. Follow [Account-session troubleshooting](./docs/account-sessions.md#troubleshooting).
- **Where are the lifetime energy sensors?**
  The provided week, month, and year sensors are *period totals* and reset automatically. For the Home Assistant Energy Dashboard, please use the cumulative energy sensors provided by the integration.

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

Normal diagnostic exports include complete device/system identifiers, serial numbers, broker addresses, MQTT usernames/client IDs and MQTT topics. Passwords, tokens and cryptographic keys remain masked. The developer debug option is not required to export identifiers or topics.
