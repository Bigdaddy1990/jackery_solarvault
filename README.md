# Jackery SolarVault — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A custom [Home Assistant](https://www.home-assistant.io/) integration that brings your Jackery SolarVault, HomePower, and Explorer power stations directly into your smart home. 

Whether you want to monitor your solar input, check your battery level, or automate your home based on your Jackery's energy flow – this integration provides real-time data and full control over your Jackery devices.

---

## 🔋 Supported Devices

This integration supports a wide range of Jackery devices, including:

- **Home Energy Systems:** Jackery SolarVault and HomePower series.
- **Portable Power Stations (Explorer Series):** E240, E557, E900, E1000, E1500V2, E1800, E2000, E3000, E7647, E7987.
- **Accessories:** Battery packs, Smart Meters, Smart Plugs, and linked Shelly cloud sockets.

---

## ✨ Features

- **Live Monitoring:** Track State of Charge (SOC), PV (Solar) input power, grid import/export, output power, and temperatures.
- **Full Control:** Toggle AC/DC outputs, change working modes, set charge limits, and prioritize outputs.
- **Automations:** Seamlessly automate your Jackery power station based on conditions (e.g., turn on a smart plug when the battery reaches 100%).
- **Energy Dashboard:** Integrates perfectly with the Home Assistant Energy Dashboard using long-term statistics (daily, weekly, monthly, yearly).
- **Fast & Reliable:** Uses the Jackery Cloud as the primary source of truth, enhanced by local Bluetooth (BLE) and MQTT for instant, low-latency updates.

---

## 🛠️ Installation

### Option 1: HACS (Recommended)
1. Open Home Assistant and go to **HACS**.
2. Add this repository as a custom repository (Category: *Integration*).
3. Search for **Jackery SolarVault** and click Download.
4. Restart Home Assistant.

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
You can use the integration's custom services to change advanced settings like charging schedules:
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
- **I don't see live updates, it takes a few minutes to refresh:**
  The cloud API is polled every few minutes. For instant live updates, enable the optional Bluetooth (BLE) feature in the configuration, provided your Home Assistant server is close enough to the Jackery device.
- **Where are the lifetime energy sensors?**
  The provided week, month, and year sensors are *period totals* and reset automatically. For the Home Assistant Energy Dashboard, please use the cumulative energy sensors provided by the integration.

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
