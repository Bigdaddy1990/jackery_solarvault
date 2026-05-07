# Werte aus APP-Cloud

> Hinweis: Diese Datei bleibt als deutsch benannter Kompatibilitaetspfad erhalten.
> Der kanonische, verlinkte Pfad ist `docs/APP_CLOUD_VALUES.md`.


falsch:

PV Gesamt Jahr: 4=0kWh 5=81,51kWh

Erzeugung gesamt: 85,57kWh

Einnahmen: 23,96€

Co2 gesamt: 85,31kg

Ursache:

Die Jackery-App/Cloud liefert bei mehreren `dateType=year`-Antworten nur den
aktuellen Monat im Jahres-Slot. Die Vormonate sind weiterhin ueber explizite
`dateType=month`-Abfragen abrufbar. Deshalb werden Jahreswerte nicht aus
Wochenwerten repariert, sondern nur durch Monatsantworten desselben Endpoints
und desselben Kalenderjahres nach oben abgesichert.



richtig:

Home Verbrauch Jahr: 4=107,17kWh 5=59,80kWh Jahr=166,97kWh

Batterie rein: 4=47,05kWh 5 =20,96kWh  Jahr=68,01kWh

Batterie raus: 4=33,54kWh 5=20,99kWh Jahr=54,53kWh

PV Gesamt: 4=146,51kWh PV-ertrag4=41,04€ ; 5=81,51kWh PV-ertrag 5=22,82€ ; Jahr=228,02kWh PV-ertrag=63,86€

Wichtig: `PV-ertrag` ist nicht dasselbe wie `Gesamt Ersparnis`.
Der PV-Ertrag in Euro ist `PV-kWh * Strompreis` fuer die PV-Periode. Die
Ersparnis zaehlt dagegen nur Energie, die das Haus tatsaechlich vom SolarVault
nutzt. PV-Anteile, die ins oeffentliche Netz gehen, im Akku verbleiben oder als
Umrichter-/Batterieverlust verloren gehen, duerfen nicht als Ersparnis zaehlen.

Berechnung fuer `systemStatistic.totalRevenue` / `Gesamt Ersparnis`:

- Basis ist die netzseitige AC-Ausgabe des Geraets nach Umrichter-/
  Akkueffekten: `device_home_stat_year.totalOutGridEnergy` minus
  `device_home_stat_year.totalInGridEnergy`, sofern der Eingangswert vorhanden
  ist. Energie, die zuerst aus dem Netz ins Geraet kam, wird damit nicht als
  PV-Ersparnis gezaehlt.
- Wenn ein CT-/Smart-Meter-Jahreswert vorhanden ist, wird
  `device_ct_stat_year.totalOutCtEnergy` als oeffentliche Netzeinspeisung
  abgezogen.
- Das Ergebnis wird auf `home_trends_year.totalHomeEgy` begrenzt, damit
  oeffentliche Einspeisung oder andere Ueberschuesse nicht als Haus-Ersparnis
  gezaehlt werden.
- Der Euro-Wert ist `Ersparnis-kWh * price.singlePrice`. Wenn der Preis nicht
  direkt vorhanden ist, wird er aus `PV-ertrag / PV-kWh` abgeleitet.
- Batterie rein/raus wird als Plausibilitaets-/Diagnosewert mitgefuehrt; die
  eigentliche Ersparnis nutzt die AC-Ausgabe, damit Umrichter- und
  Batterie-/Speicherverluste nicht doppelt gezaehlt werden.

Absicherung in der Integration:

- `device_pv_stat_year`: Summe der expliziten Monatsantworten von
  `/v1/device/stat/pv`; PV1..PV4 und Solarertrag werden mit abgesichert.
- `device_battery_stat_year`: Summe der expliziten Monatsantworten von
  `/v1/device/stat/battery` fuer Batterie rein/raus.
- `device_home_stat_year`: Summe der expliziten Monatsantworten von
  `/v1/device/stat/onGrid` fuer Netzseite Eingang/Ausgang.
- `home_trends_year`: Summe der expliziten Monatsantworten von
  `/v1/device/stat/sys/home/trends` fuer Hausverbrauch.
- `systemStatistic.totalGeneration` und `totalCarbon` werden nicht abgesenkt,
  sondern nur auf den korrigierten PV-Jahreswert als Lower Bound angehoben.
- `systemStatistic.totalRevenue` wird separat aus den Jahresflusswerten fuer
  Haus-Eigenverbrauch berechnet, wenn diese Werte vorhanden sind. Ein
  Cloud-Wert, der nur PV-Ertrag abbildet oder unter der berechneten aktuellen
  Jahres-Ersparnis liegt, wird ersetzt. Ein hoeherer plausibler Cloud-Gesamtwert
  bleibt erhalten, damit zukuenftig korrekte Jackery-Gesamtwerte nicht
  ueberschrieben werden.
- Jeder korrigierte Jahrespayload traegt `_year_month_backfill`; korrigierte
  Gesamtwerte tragen `_total_lower_bound_guard`, damit Diagnoseexporte Rohwert,
  korrigierten Wert und Quelle nachvollziehbar zeigen.
- Der Ersparnis-Sensor traegt `_savings_calculation` mit Formel, Rohwert,
  veroeffentlichtem Wert und den verwendeten Energiekomponenten. Optional
  koennen diese Zwischenwerte und die geschaetzte aktuelle Verlustleistung als
  eigene Home-Assistant-Entitaeten aktiviert werden.






