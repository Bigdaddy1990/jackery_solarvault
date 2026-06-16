# Jackery 2.1.1 — Stats & Trends (DTO-Aufschlüsselung)

> Nachtrag zu Teil 1 §5: dort waren nur die Pfade gelistet. Hier die **Request-/Response-Felder** jedes Statistik-/Trends-Endpunkts (für historische HA-Sensoren & Diagramme). Alles aus classes5 extrahiert.

## 1. Gemeinsame Muster

**Request (die meisten Stat-Calls):**
`{ deviceId | deviceSn | systemId, beginDate, endDate, dateType }`
- `dateType` ∈ **`day, week, month, year, total`** (intern auch DAY/MONTH/YEAR)
- `beginDate`/`endDate` als Datums-Strings; Geräte-Stats nutzen `deviceId/deviceSn`, System-Stats `systemId`.

**Response (Chart-Form):** `{ x:[Labels], y:[Werte], y1, y2, y3, y4, unit, total… }`
- `x` = Achsenbeschriftung (Zeit), `y…` = eine oder mehrere Datenreihen, `unit` = Einheit (z.B. kWh), `total…` = Summen.
- Mehrere `y`-Reihen = gestapelte/mehrfache Kurven (z.B. y=Laden, y1=Entladen).

---

## 2. Geräte-Statistiken (`deviceId`/`deviceSn`)

| Endpunkt (Api) | Pfad | Request | Response-Felder |
|----------------|------|---------|-----------------|
| **BoxEleStatApi** | `device/stat` | beginDate, endDate, dateType, deviceSn, **key** | `total, unit, x[], y[]` |
| **DeviceStatSocApi** | `device/stat/soc` | deviceId | `x[], y1[]` (SOC-Verlauf) |
| **TodayEnergyApi** | `device/stat/today` | deviceSn | `de, dg, dh, ds` (Tages-Energiewerte 🔶) |
| **PvStatApi** | `device/stat/pv` | +systemId | `totalSolarEnergy, totalSolarRevenue, currency, unit, x[], y[]…y4[]` |
| **BatteryStatApi** | `device/stat/battery` | (Standard) | `totalCharge, totalDischarge, unit, x[], y[],y1[],y2[],y3[]` |
| **HomeStatApi** | `device/stat/onGrid` | (Standard) | `totalInGridEnergy, totalOutGridEnergy, unit, x[], y[],y1[],y2[]` |
| **EpsStatApi** | `device/stat/eps` | (Standard) | `totalInEpsEnergy, totalOutEpsEnergy, unit, x[], y[],y1[],y2[]` |
| **CtStatApi** | `device/stat/ct` | (Standard) | `totalInCtEnergy, totalOutCtEnergy, unit, x[], y1[],y2[]` |
| **PortableCtStatApi** | `device/stat/ct/statics` | deviceId | `l1, l2, total` (Phasen-Summen) |
| **AccMeterStatApi** | `device/stat/meter` | deviceId | `chargingEnergy, dischargingEnergy` |
| **AccSocketStatApi** | `device/stat/socket` | (Standard, deviceId) | `useEnergy, unit, x[], y[]` |
| **EleStorageStatApi** | `device/stat/symmetry` | +negative,positive | `p[], n[], totalP, totalN, unit, x[]` (Laden/Entladen-Symmetrie) |
| **BoxPowerOutageStatApi** | `device/stat/cutoff` | beginDate, endDate, deviceSn | `cnt, totalInterval` (Stromausfälle) |
| **StatProfitApi** | `device/stat/profit` | deviceId | `total, currency` |
| **SocialContributionsApi** | `device/stat/carbon` | deviceId | (CO₂-Beitrag) |
| **DeviceChargeReportApi** | `device/chargeReport` | deviceSn, pageIndex | Liste `ChargeReport{startTs,endTs,startSoc,endSoc,maxPower,totalEnergy,chargeInterval,id}` |
| **PowerReportApi** | `device/property/power3` | deviceSn, properties:Map | (Power-Report-Push) |

### Geräte-Statistik-Aggregat
| **DeviceStatDeviceStatistic** | `device/stat/deviceStatistic` | deviceId | `pvEgy, batChgEgy, batDisChgEgy, inOngridEgy, outOngridEgy, inEpsEgy, outEpsEgy` (Energie-Zähler) |
| **DeviceStatSocketStatistic** | `device/stat/smartSocketStatistic` | smartSocketId | `todayEgy, totalEgy` |

---

## 3. System-Statistiken & Trends (`systemId`) — Heim-Energiesysteme

| Endpunkt | Pfad | Request | Response |
|----------|------|---------|----------|
| **DeviceStatSystemStatistic** | `device/stat/systemStatistic` | systemId | `todayGeneration, todayLoad, todayBatteryChg, todayBatteryDisChg, totalGeneration, totalRevenue, totalCarbon, isSetPrice` |
| **SysPvStatApi** | `device/stat/sys/pv/trends` | systemId, beginDate, endDate, dateType | `totalSolarEnergy, totalSolarRevenue, currency, count, showDash, weather[], pvSources, pvUsage, unit, x[], y[]` |
| **SysBatteryStatApi** | `device/stat/sys/battery/trends` | (System-Standard) | `totalChgEgy, totalDisChgEgy, batterySources, batteryUsage, unit, x[], y[],y1[],y2[]` |
| **SysHomeStatApi** | `device/stat/sys/home/trends` | (System-Standard) | `totalHomeEgy, homeSources, unit, x[], y[]` |
| **AiSmartScheduleApi** | `device/stat/getSmartSchedulePrediction` | systemId | `xList[], priceList[], pvPowerList[], homeList[], profit, currency, days` (Smart-Vorhersage) |

### Energiefluss-Zerlegung (verschachtelte Objekte in den Trends)
- **PvSource** `{ ac, pv }` — wohin der PV-Strom geht
- **PvUsage** `{ ac, battery, home }` — PV-Nutzung nach Senke
- **BatterySources** `{ ac, home, pv }` — woher die Akku-Ladung kommt
- **BatteryUsage** `{ ac, home }` — wohin die Akku-Entladung geht
- **HomeSource** `{ ac, battery, pv }` — woraus der Hausverbrauch gedeckt wird

→ Damit lässt sich ein vollständiges Energiefluss-Diagramm (PV → Akku/Haus/Netz) rekonstruieren — ideal für HA-Energy-Dashboard.

---

## 4. Energie-Zähler-Glossar (klar benannte Felder)
`pvEgy`=PV-Energie · `batChgEgy`/`batDisChgEgy`=Akku Laden/Entladen · `inOngridEgy`/`outOngridEgy`=Netzbezug/-einspeisung · `inEpsEgy`/`outEpsEgy`=EPS rein/raus · `totalGeneration`=Gesamterzeugung · `totalLoad`/`todayLoad`=Verbrauch · `totalRevenue`=Erlös · `totalCarbon`=CO₂-Einsparung · `chargingEnergy`/`dischargingEnergy`=Meter Laden/Entladen · `useEnergy`=Steckdosen-Verbrauch · `todayEgy`/`totalEgy`=Tages-/Gesamt-Energie.

**TodayEnergyApi `de/dg/dh/ds`** (🔶 unklar benannt, beste Annahme): `dg`=Tageserzeugung (generation), `dh`=Tagesverbrauch (home/load), `ds`=Tages-Solar/-Speicher, `de`=Tages-Netz/-Entladung. Per Capture gegen die App-Anzeige kalibrieren.

---

## 5. Praxis für HA
- **Verlaufs-Sensoren / Diagramme:** die `x[]`/`y[]`-Chart-Endpunkte (pro `dateType`) liefern fertige Zeitreihen — direkt als HA-Statistik/Chart nutzbar.
- **Energy-Dashboard:** `DeviceStatDeviceStatistic` (Gerät) bzw. `DeviceStatSystemStatistic` + `sys/*/trends` (System) liefern die kWh-Zähler und die Quellen/Senken-Zerlegung für das HA-Energie-Dashboard (PV-Erzeugung, Netzbezug/-einspeisung, Akku, Verbrauch, CO₂, Erlös).
- **Schnellwerte ohne Stat-Call:** `bs` (SOC) und `elec` kommen schon aus `device/bind/list`; Live-Leistungen aus dem MQTT-`device`-Shadow (Teil 2 §5).
- Alle Stat-Calls sind REST (`iot.jackeryapp.com`) mit Bearer-Token — unabhängig vom MQTT-Pfad.
