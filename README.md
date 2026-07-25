# Jackery SolarVault for Home Assistant

Languages:
[English](./README.md) · [Deutsch](./docs/README.de.md) · [Français](./docs/README.fr.md) · [Español](./docs/README.es.md)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A custom [Home Assistant](https://www.home-assistant.io/) integration that brings your Jackery SolarVault, HomePower, and Explorer power stations directly into your smart home.

**This is the ultimate (non-plus-ultra) Jackery integration for Home Assistant.** It combines 100% of the official App's functionality (Cloud API) with the speed and reliability of **Local MQTT** and **Bluetooth (BLE)**.

---

## 🏆 Why this integration is the best choice

You might have heard of other manual MQTT workarounds or older integrations. Here is why this integration is the clearly superior choice:

1. **Zero Manual Token Extraction:** We require your Cloud credentials during setup. **Why?** Because the integration automatically discovers all your devices and securely fetches the complex encryption keys and tokens required for local communication. You don't have to intercept network traffic or manually configure JSON payloads.
2. **True Local Control:** Once the initial cloud handshake is complete, the integration connects directly to your device via **Local MQTT** and **Bluetooth (BLE)** for instant, sub-second updates and local control.
3. **100% App Functionality:** Unlike basic local-only scripts that only read battery levels, this integration supports *everything* the Jackery App does, including Time-of-Use scheduling, Shelly integration, firmware checks, and advanced charging settings.

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

---

## 🛠️ Installation

### HACS (Recommended)
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
3. Follow the setup wizard and enter your Jackery Cloud credentials.

> [!WARNING]
> **Important Account Limitation:** Jackery only allows one active session per account. If you log in with your primary app account, you will regularly be logged out on your phone, or the integration will disconnect.
> **Solution:** Create a **second, dedicated Jackery account** just for Home Assistant. Share your Jackery devices from your main app account with this new dedicated HA account!

### Configuration Options
- **Email & Password:** Your dedicated Jackery Cloud account credentials.
- **Bluetooth (BLE):** Optional. Allows direct communication when your HA server is in Bluetooth range of the Jackery.
- **Local MQTT:** Optional. Use this if your device is configured to publish data to a local MQTT broker.

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
  This usually happens because you are using the same account in the Jackery App on your phone and in Home Assistant. Please create a dedicated account for Home Assistant and share your devices to it.
- **Where are the lifetime energy sensors?**
  The provided week, month, and year sensors are *period totals* and reset automatically. For the Home Assistant Energy Dashboard, please use the cumulative energy sensors provided by the integration.

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
