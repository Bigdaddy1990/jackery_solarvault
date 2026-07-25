# Jackery SolarVault — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Eine benutzerdefinierte [Home Assistant](https://www.home-assistant.io/) Integration, die deine Jackery SolarVault, HomePower und Explorer Powerstations direkt in dein Smart Home bringt.

Egal, ob du den solaren Ertrag überwachen, deinen Akkustand prüfen oder dein Haus basierend auf dem Energiefluss deiner Jackery automatisieren möchtest – diese Integration bietet dir Echtzeit-Daten und volle Kontrolle über deine Jackery Geräte.

---

## 🔋 Unterstützte Geräte

Diese Integration unterstützt eine Vielzahl von Jackery-Geräten, darunter:

- **Heim-Energiesysteme:** Jackery SolarVault und HomePower Serie.
- **Tragbare Powerstations (Explorer Serie):** E240, E557, E900, E1000, E1500V2, E1800, E2000, E3000, E7647, E7987.
- **Zubehör:** Akku-Packs, Smart Meter, Smart Plugs und verknüpfte Shelly-Cloud-Steckdosen.

---

## ✨ Funktionen

- **Live-Überwachung:** Verfolge Ladezustand (SOC), PV (Solar)-Eingangsleistung, Netzbezug/-einspeisung, Ausgangsleistung und Temperaturen.
- **Volle Kontrolle:** Schalte AC/DC-Ausgänge, wechsle Arbeitsmodi, setze Ladelimits und priorisiere Ausgänge.
- **Automatisierungen:** Automatisiere deine Jackery Powerstation basierend auf Bedingungen (z.B. eine Steckdose einschalten, wenn der Akku 100% erreicht).
- **Energie-Dashboard:** Perfekte Integration in das Home Assistant Energie-Dashboard mit langfristigen Statistiken (täglich, wöchentlich, monatlich, jährlich).
- **Schnell & Zuverlässig:** Nutzt die Jackery Cloud als primäre Datenquelle, erweitert durch lokales Bluetooth (BLE) und MQTT für sofortige Live-Updates ohne Verzögerung.

---

## 🛠️ Installation

### Option 1: HACS (Empfohlen)
1. Öffne Home Assistant und gehe zu **HACS**.
2. Füge dieses Repository als benutzerdefiniertes Repository hinzu (Kategorie: *Integration*).
3. Suche nach **Jackery SolarVault** und klicke auf Herunterladen.
4. Starte Home Assistant neu.

### Option 2: Manuell
1. Lade das neueste Release herunter.
2. Kopiere den Ordner `custom_components/jackery_solarvault` in dein Home Assistant `config/custom_components/` Verzeichnis.
3. Starte Home Assistant neu.

---

## ⚙️ Konfiguration

1. Gehe zu **Einstellungen → Geräte & Dienste**.
2. Klicke auf **Integration hinzufügen** und suche nach **Jackery SolarVault**.
3. Folge dem Einrichtungsassistenten und gib deine Jackery-Cloud-Zugangsdaten ein.

> [!WARNING]
> **Wichtige Account-Einschränkung:** Jackery erlaubt nur eine aktive Sitzung pro Account. Wenn du dich mit dem Hauptaccount deiner App anmeldest, wirst du auf dem Handy regelmäßig abgemeldet, oder die Integration verliert die Verbindung.  
> **Lösung:** Erstelle einen **zweiten, dedizierten Jackery-Account** nur für Home Assistant. Teile deine Jackery-Geräte aus deinem Haupt-App-Account mit diesem neuen, dedizierten HA-Account!

### Konfigurationsoptionen
- **E-Mail & Passwort:** Die Zugangsdaten deines dedizierten Jackery-Cloud-Accounts.
- **Bluetooth (BLE):** Optional. Erlaubt die direkte Kommunikation, wenn sich dein HA-Server in Bluetooth-Reichweite der Jackery befindet.
- **Lokales MQTT:** Optional. Nutze dies, wenn dein Gerät so konfiguriert ist, dass es Daten an einen lokalen MQTT-Broker sendet.

---

## 💡 Automatisierungen & Beispiele

**Benachrichtigung bei niedrigem Akkustand:**
```yaml
automation:
  - alias: "Jackery Akku Warnung"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.jackery_solarvault_state_of_charge
        below: 20
    actions:
      - action: notify.notify
        data:
          message: "Dein Jackery Akku ist unter 20% gefallen!"
```

**Zeitbasierten Ladeplan (Time-of-Use) einstellen:**
Du kannst die benutzerdefinierten Dienste der Integration nutzen, um erweiterte Einstellungen wie Ladepläne zu ändern:
```yaml
action: jackery_solarvault.save_tou_plan
data:
  device_id: "<deine_device_id>"
  body:
    tasks:
      - start: "04:00"
        end: "06:00"
        loops: "1111111"
        pw: 700
        sysSwitch: 1
```

---

## ❓ Fehlerbehebung (FAQ)

- **Meine Integration verliert ständig die Verbindung oder Sensoren sind "nicht verfügbar":**
  Dies passiert in der Regel, weil du denselben Account in der Jackery-App auf deinem Handy und in Home Assistant verwendest. Bitte erstelle einen dedizierten Account für Home Assistant und teile deine Geräte mit diesem.
- **Ich sehe keine Live-Updates, es dauert ein paar Minuten bis zur Aktualisierung:**
  Die Cloud-API wird alle paar Minuten abgefragt. Für sofortige Live-Updates aktiviere die optionale Bluetooth (BLE) Funktion in der Konfiguration, sofern dein Home Assistant Server nah genug am Jackery-Gerät steht.
- **Wo sind die Sensoren für die gesamte Lebensdauer-Energie?**
  Die bereitgestellten Sensoren für Woche, Monat und Jahr sind *Periodensummen* und werden automatisch zurückgesetzt. Für das Home Assistant Energie-Dashboard nutze bitte die kumulativen Energie-Sensoren, die von der Integration bereitgestellt werden.

---

## 📜 Lizenz

Dieses Projekt ist unter der MIT-Lizenz lizenziert - siehe die [LICENSE](LICENSE) Datei für Details.
