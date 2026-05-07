# Jackery SolarVault 3 Pro Max Home Assistant Integration

**🌍 Language / Sprache / Idioma / Langue:**
[🇬🇧 English](../README.md) · [🇩🇪 Deutsch](./README.de.md) · [🇫🇷 Français](./README.fr.md) · [🇪🇸 Español](./README.es.md)

---

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)


Community-Integration für Jackery SolarVault Systeme, insbesondere SolarVault 3 Pro Max. Die Integration liest Livewerte, Energie-Statistiken und konfigurierbare Parameter aus der Jackery-Cloud und nutzt MQTT-Push für schnelle Statusänderungen und Steuerbefehle.

> ⚠️ Diese Integration ist kein offizielles Jackery-Produkt und steht in keinem Verhältnis zu Jackery Inc.


## Funktionen

- Automatische Geräte- und System-Erkennung über den Jackery-Account
- Regelmäßige HTTP-Aktualisierung der Standardwerte mit festem 30-Sekunden-Intervall
- MQTT-Push für Live-Status, Smart-Meter, Zusatzbatterien und Steuerbefehle
- Hauptgerät, Smart-Meter und Zusatzbatterien als getrennte Home-Assistant-Geräte
- Unterstützung für bis zu 5 Zusatzbatterien
- Live-Leistung: Batterie, PV gesamt, PV-Kanäle, Netzbezug, Netzeinspeisung, EPS und Zusatzbatterie-Stack
- Energie-Statistiken: Tag, Woche, Monat und Jahr für PV, Verbrauch und Batterie
- Energy-Dashboard-taugliche Langzeitwerte nur für kumulative Gesamt-/Tageswerte; Wochen-/Monats-/Jahreswerte sind reine Anzeigewerte
- Smart-Meter-Leistung inklusive Phasenwerte, sofern ein Smart-Meter verbunden ist
- Konfiguration per Entities: EPS, Lade-/Entladelimits, Einspeiseleistungsgrenze, maximale Ausgangsleistung, Energieverbrauchsmodus, Auto-aus, Smart-Meter-Folge, Unwetterwarnung, Temperatureinheit, Strompreis und Standby
- Neustart-Button für das Gerät
- Diagnose-Entities für Online-Status, Firmware, Systemgrenzen, Netzstandard, Ländercode, Rohdaten und MQTT-Zustand

## Installation über HACS

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)

1. HACS öffnen.
2. Drei Punkte oben rechts öffnen.
3. `Custom repositories` auswählen.
4. Repository-URL `https://github.com/Bigdaddy1990/jackery_solarvault` eintragen und Kategorie `Integration` wählen.
5. Nach `Jackery SolarVault` suchen und installieren.
6. Home Assistant neu starten.
7. Einstellungen → Geräte & Dienste → Integration hinzufügen → `Jackery SolarVault`.

## Manuelle Installation

1. ZIP von der [Releases-Seite](https://github.com/Bigdaddy1990/jackery_solarvault/releases) herunterladen.
2. Ordner `custom_components/jackery_solarvault` nach `<HA-config>/custom_components/` kopieren.
3. Home Assistant neu starten.
4. Integration über Einstellungen → Geräte & Dienste hinzufügen.

## Einrichtung

Benötigt werden:

- Jackery-Cloud E-Mail
- Jackery-Cloud Passwort
- optional: berechnete Smart-Meter-Sensoren aktivieren/deaktivieren
- optional: berechnete Leistungssensoren aktivieren/deaktivieren
- optional: Detail-Sensoren zur Ersparnis-Berechnung aktivieren/deaktivieren

Geräte-ID, System-ID, MQTT-macId und Region werden aus den Cloud-/MQTT-Daten abgeleitet und im UI nicht mehr manuell abgefragt.

## Wichtiger Hinweis zum Jackery-Login

Jackery erlaubt praktisch nur eine aktive Sitzung pro Account. Wenn die offizielle App und Home Assistant gleichzeitig mit demselben Account angemeldet sind, können Token und MQTT-Zugangsdaten rotieren. Das kann zu ablaufenden Token oder MQTT-Authentifizierungsfehlern führen.

Empfohlen:

1. Zweites Jackery-Konto erstellen.
2. SolarVault in der Jackery-App per Teilen/QR-Code an das Zweitkonto freigeben.
3. Das Zweitkonto in Home Assistant verwenden.

## Entitäten

### Normale Sensoren

- Gesamt-SOC und interne Batterie
- Batterie-Ladeleistung und Batterie-Entladeleistung
- PV-Leistung gesamt und PV-Kanäle 1-4
- Netzbezug, Netzeinspeisung und Netto-Netzleistung
- Netzseitige Eingangs-/Ausgangsleistung
- EPS-Leistung
- Zusatzbatterie-Lade-/Entladeleistung
- Sonstige Lastleistung
- Strompreis
- Aktive Alarme
- Tages-/Wochen-/Monats-/Jahreswerte für PV, Verbrauch und Batterie

### Zusatzbatterien

Zusatzbatterien werden getrennt vom Hauptgerät angelegt. Pro erkannter Batterie werden, soweit verfügbar, angezeigt:

- SOC
- Zelltemperatur
- Ladeleistung
- Entladeleistung
- Firmware-Version
- Seriennummer
- Firmware-Version und Seriennummer als Home-Assistant-Geräteinformation, wenn verfügbar
- Kommunikationsstatus als Attribute

### Smart-Meter

Das Smart-Meter wird als eigenes Gerät unter dem SolarVault angelegt. Unterstützt werden:

- Gesamtleistung
- Phase 1 Leistung
- Phase 2 Leistung
- Phase 3 Leistung
- verfügbare Rohwerte als Attribute

### Konfigurierbare Entities

- EPS-Ausgang
- Standby
- Auto-aus im Inselbetrieb (mit Auto-aus-Zeit)
- Lade- und Entladelimit
- Einspeiseleistungsgrenze
- Maximale Ausgangsleistung
- Standardausgangsleistung
- Smart-Meter folgen
- Energieverbrauchsmodus
- Strompreismodus
- Einheitstarif-Preis
- Temperatureinheit
- Unwetterwarnung und Vorwarnzeit
- Neustart

## Services

Die Integration registriert drei Services im `jackery_solarvault`-Namespace:

| Service | Zweck |
|---|---|
| `jackery_solarvault.rename_system` | System (SolarVault-Gerät) in der Cloud umbenennen |
| `jackery_solarvault.refresh_weather_plan` | Aktuellen Unwetter-Warnplan vom Cloud-Server holen |
| `jackery_solarvault.delete_storm_alert` | Aktiven Sturm-Alarm via Cloud-Befehl löschen |

Details zu den erforderlichen Parametern siehe `services.yaml` oder den HA Dev-Tools → Services-Editor.

## Energie- und Leistungssensoren richtig lesen

- Batterie-Entladeleistung zeigt, was die Batterie abgibt.
- Netz-Netto ist Netzbezug minus Netzeinspeisung. Dieser Wert muss nicht der Batterie-Entladeleistung entsprechen, weil Hauslast, PV, Smart-Meter und interne Regelung dazwischenliegen.
- Stack-Eingang/-Ausgang bezieht sich auf den Zusatzbatterie-Stack bzw. den Leistungsfluss zwischen Hauptgerät und Zusatzbatterien.
- Smart-Meter-Werte kommen vom angeschlossenen Meter und werden getrennt von den Hauptgerätewerten geführt.
- Der Sensor `Hausverbrauch aktuell` berechnet den Momentanverbrauch aus Jackerys gemeldetem Live-Hausverbrauch (`otherLoadPw`) und nutzt Smart-Meter-Nettoleistung minus Jackery-Netzseite-Eingang plus Jackery-Netzseite-Ausgang nur als Fallback. Damit wird die Einspeisung des SolarVault nicht mehr fälschlich vom Hausverbrauch abgezogen.
- `Gesamt Ersparnis` ist nicht der PV-Ertrag in Euro. Wenn Jahresflusswerte vorhanden sind, berechnet die Integration die Ersparnis aus der netzseitigen AC-Ausgabe des SolarVault nach Umrichter-/Akkueffekten, zieht netzseitigen Eingang und vorhandene CT-Netzeinspeisung ab, begrenzt auf den Hausverbrauch und multipliziert mit dem konfigurierten Strompreis.
- Tages-/Wochen-/Monats-/Jahres-Energiesensoren nutzen `state_class: total` mit passendem `last_reset` für die jeweilige App-Periode. Sie sind Periodenwerte, keine lebenslang monoton steigenden Zähler.
- Wochen-, Monats- und Jahreswerte werden identisch aus der jeweiligen App-Chart-Serie berechnet. Die Serie hängt vom Payload ab: PV-/Home-Trend-Gesamtwerte nutzen meist `y`, Batterie-Ladung/Entladung nutzt `y1`/`y2`, Geräte-Netzseite Eingang/Ausgang nutzt `y1`/`y2`, PV1..PV4 nutzt `y1`..`y4`. Die Server-Totalfelder werden nur noch als Fallback/Diagnose verwendet, weil Monats-/Jahres-Totalfelder je nach Payload irreführend sein können.

### Perioden, Gesamtwerte und Warnungen

- Woche = Montag bis Sonntag.
- Monat = Kalendermonat.
- Jahr = Kalenderjahr.
- Gesamtwerte/Lifetime-Werte für Erzeugung und CO2 kommen aus den dokumentierten App-/HTTP-/MQTT-Gesamtfeldern und werden nur mit expliziten Monatswerten desselben Endpoints abgesichert, wenn Jackery den aktuellen Monat als Jahreswert liefert.
- `Gesamtersparnis` wird wenn möglich aus selbst genutzter AC-Energie berechnet, nicht aus PV-Ertrag in Euro. Ein fehlender, zu niedriger oder PV-Ertrag-förmiger Cloud-Wert wird ersetzt; ein höherer plausibler Cloud-Gesamtwert bleibt erhalten. Aktiviere die Detail-Sensoren zur Ersparnis-Berechnung, um Zwischenwerte und die geschätzte Verlustleistung als Entitäten zu sehen. Details stehen in [`APP_CLOUD_VALUES.md`](APP_CLOUD_VALUES.md).
- Es gibt ausdrücklich keine Wochenwerte zur Reparatur von Monats-, Jahres- oder Gesamtwerten. Monatswerte dürfen nur Jahreswerte desselben Endpoint-Typs und Kalenderjahres absichern.
- Am Monatsanfang kann der Wochenwert höher als der Monatswert sein, wenn die laufende Woche noch Tage aus dem Vormonat enthält. Das ist kein Fehler.
- Wenn Jackery widersprüchliche Daten liefert, z. B. Jahreswert kleiner als eine komplett im selben Jahr liegende Woche oder Gesamtertrag kleiner als Jahresertrag, ändert die Integration keine Entity-Werte heimlich. Stattdessen erzeugt sie einen Repair-Hinweis und legt Details im Diagnose-Export unter `data_quality` ab.

## Polling und Aktualisierung

Das schnelle HTTP-Polling läuft fest alle 30 Sekunden. Langsame Cloud-Statistiken werden bewusst seltener abgefragt, weil Jackery diese Daten serverseitig nicht im Sekundentakt aktualisiert.

MQTT-Push aktualisiert Livewerte unabhängig vom Polling, sobald der Broker verbunden ist.

Die MQTT-TLS-Verbindung verifiziert die Broker-Zertifikatskette aktiv. Mitgeliefert wird ``custom_components/jackery_solarvault/jackery_ca.crt`` als dokumentierter Trust-Anker für ``emqx.jackeryapp.com``, weil Jackery den Broker nicht von einer öffentlichen CA signieren lässt. Auf Python 3.10+/OpenSSL 3.x wird zusätzlich gezielt das Strict-Flag ``VERIFY_X509_STRICT`` deaktiviert, weil das Server-Zertifikat die Erweiterung ``Authority Key Identifier`` nicht mitliefert. Hostname-Check, Kettenprüfung und Signaturprüfung bleiben aktiv (``CERT_REQUIRED`` + ``check_hostname = True``). Es gibt keinen automatischen Fallback auf ``tls_insecure`` oder ``CERT_NONE`` — TLS-Fehler bleiben sichtbar. Der Diagnostics-Export zeigt unter ``mqtt_status`` u.a. ``tls_custom_ca_loaded``, ``tls_x509_strict_disabled`` und ``tls_certificate_source``, sodass die TLS-Konfiguration ohne Debug-Logging nachvollziehbar ist. Hintergrund und Änderungsregeln für diese Strategie stehen in ``docs/STRICT_WORK_INSTRUCTIONS.md``.

MQTT-Diagnosedaten enthalten nur redigierte Topic-Pfade (`hb/app/**REDACTED**/...`), Zähler und Zeitstempel für Verbindung, letzte Nachricht, letzte Veröffentlichung und verworfene Payloads. Der Jackery-`userId`-Teil des Topics wird nicht im Diagnoseexport ausgegeben.

## Debug-Logging

Für Fehleranalyse:

```yaml
logger:
  default: info
  logs:
    custom_components.jackery_solarvault: debug
```

## Voraussetzungen

- Home Assistant 2025.8.0 oder neuer
- Python 3.14+ (wird über Home Assistant bereitgestellt)
- Jackery-Cloud-Account
- SolarVault online über WLAN oder Ethernet
- HACS für die empfohlene Installation

## Mitwirken

Bug-Reports und Feature-Requests bitte über die [GitHub Issues](https://github.com/Bigdaddy1990/jackery_solarvault/issues). Bei Auth- oder MQTT-Problemen ist ein Diagnose-Export aus HA (Einstellungen → Geräte & Dienste → Jackery SolarVault → drei Punkte → Diagnose herunterladen) sehr hilfreich. Sensible Felder werden automatisch redacted; einen Diagnoseexport vor dem Teilen trotzdem kurz prüfen.

## Lizenz

MIT-Lizenz. Siehe [LICENSE](LICENSE).
