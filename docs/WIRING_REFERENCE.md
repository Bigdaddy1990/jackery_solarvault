# Jackery SolarVault — Wiring Reference (Code ↔ App-Contract)

Stand: 2026-06-01. Single source of truth: welcher Endpunkt → welche Fields →
welche Sensoren/Entitäten → welche Stats/Getter/Setter/Backfills → welche Verbindung.
Erzeugt durch read-only Audit (parallele Agenten) + erst-Hand-Verifikation der
strittigen Punkte direkt am Code.

> **Ground truth = `source-of-truth/`**, primär die Smali-Extraktionen
> `source-of-truth/hbxn_commands.html` (Befehle/actionIds) und `source-of-truth/hbxn_model_fields.html` sowie
> `source-of-truth/jackery_command_catalog_v2.html` / `source-of-truth/jackery_entity_field_candidates_v2.html` /
> `source-of-truth/jackery_http_model_fields_v2.html` / `source-of-truth/jackery_http_api_endpoints_v2.html`
> (Modell-/Endpoint-Felder). Die **narrativen** Anhänge `APP_POLLING_MQTT.html`
> und `MQTT_PROTOCOL.html` (und ihre `.md`-Kopien) sind abgeleitet; bei
> Widerspruch folgt der Code hbxn/Smali. `SENSOR_SOURCE_PATHS.*` ist aus `sensor.py` generiert,
> kein unabhängiger Beweis. Zeilennummern beziehen sich auf den Snapshot in
> `D:\Downloads\Clause` und sind indikativ.

---

## §0 — Befund-Index

### A. Verifizierte Code-Bugs (erst-Hand am Code geprüft)

| ID | Schwere | Befund | Ort (verifiziert) |
|---|---|---|---|
| **C1** | Mittel | **Behoben: Battery-Pack Stale-Removal Off-by-one.** Pack-Entitäten und Pack-Devices werden 1-basiert erzeugt; `_drop_stale_battery_packs()` liefert weiterhin die 0-basierte Originallistenposition, der Registry-Removal wandelt vor dem Identifier-Bau jetzt explizit auf 1-basiert (`entity_pack_index = pack_index + 1`). | Erzeugung: `sensor.py:3212`, Identifier `sensor.py:3896`/`4056`. Removal-Fix: `coordinator.py:2646`→`2652`. |

### B. Geprüft, aktuell kein Code-Bug

| ID | Befund | Hinweis |
|---|---|---|
| **U1** | `meter_head_*`-Entitäten sind nicht rein positional. Entity-Erzeugung nutzt `sorted_meter_heads()`, `meter_head_serial()` und `stable_subdevice_key()`, Werte werden per gespeicherter Serial/ID wiedergefunden. | `sensor.py:3254`→`3261`, `sensor.py:4247`→`4258`, `util.py:797`→`829`. |
| **U2** | `smart_plug_*`-Entitäten sind nicht rein positional. Unique-ID-/Device-Suffix und Wertauflösung nutzen Serial/ID; der Index bleibt nur als Anzeige-/Fallback-Attribut. | `sensor.py:3222`→`3248`, `binary_sensor.py:121`→`126`, `switch.py:724`→`729`, `entity.py:184`→`185`, `util.py:751`→`794`. |

### C. Ehemalige docs/-Abweichungen — korrigiert gegen Smali + Frida

| ID | Betroffene docs/-Datei | Doku-Fehler | Code-Beleg (korrekt) |
|---|---|---|---|
| **S1** | `MQTT_PROTOCOL.md`, `APP_POLLING_MQTT.md` | Behoben: `3022=EPS/swEps`, `3023=Standby/autoStandby`. Smali (`hbxn_commands.html`): `3022=CONTROL_AC_OFF_GRID_SWITCH`, `3023=CONTROL_STANDBY`. | `const.py:1465`/`1466`, `coordinator.py:4230`/`4355`. |
| **S2** | `APP_POLLING_MQTT.md` | Behoben: `maxOutPw` läuft über `DevicePropertyChange/107`, nicht `ControlCombine/121`. | `const.py:1445`, `coordinator.py:4311`/`4321`. |
| **S3** | `MQTT_PROTOCOL.md`, `APP_POLLING_MQTT.md` | Behoben: Lade-/Entladelimit läuft als ein kombinierter `3028`-Frame (`SET_CHARGE_DISCHARGE_LINE`) mit `socChgLimit` + `socDischgLimit`. | `const.py:1440`, `coordinator.py:4242`/`4280`. |
| **S4** | `SENSOR_SOURCE_PATHS.md` (auto-generiert aus sensor.py) | listet Fallback-Keys `onGridStat`/`ctState`/`gridState`/`gridStat`, die in keinem Smali-Modell existieren. Verhaltensneutral (Primärkey `ongridStat`/`ctStat`/`gridSate` zuerst in `_prop_any`). | `const.py` FIELD_*-Aliasse; `sensor.py:819/827/835`. |

### D. Minor / kosmetisch (kein Bug)

| ID | Befund |
|---|---|
| M1 | `cell_temperature` ÷10-Skalierung (`sensor.py`) ist aus den App-Modell-Dumps nicht belegbar — am Gerät verifizieren (tenths-of-°C ist plausibel). |
| M2 | `grid_in_power`/`grid_out_power` fassen je 3 distinkte Modellfelder via `_prop_any` zusammen (`gridInPw`/`inGridSidePw`/`inOngridPw`). Richtung korrekt; falls die Felder verschiedene Messpunkte sind, ist der Wert quellabhängig. |
| M3 | **Felder ohne `source-of-truth/`-Modellbeleg (am Code bestätigt) — echte Abweichung gegen die Extraktion:** `stackInPw`/`stackOutPw` (stack_in/out_power, const.py:481-482) und `chargePlanPw` (charge_plan_power, const.py:484) in KEINEM `source-of-truth/hbxn_model_fields.html`-Modell (HomeBody/SystemBody). `ongridOtBatEgy`/`pvOtBatEgy`/`batOtGridEgy` (device_today_*-Stats, const.py:945-947) NICHT im `DeviceStatDeviceStatistic$Bean` (`source-of-truth/jackery_http_model_fields_v2.html`; dort nur batChgEgy/batDisChgEgy/inEpsEgy/inOngridEgy/outEpsEgy/outOngridEgy/pvEgy). Stehen nur in narrativen Captures (`MQTT_PROTOCOL.html`) bzw. laut `api.py:922`-Docstring laufzeitabhängig → evtl. reale, im Dump fehlende Felder, aber gegen die html-Extraktion Abweichungen. |
| M4 | 12 Extra-Sensoren ggü. `SENSOR_SOURCE_PATHS`-Tabelle: 8× EPS (`device_eps_stat_*`) + 4× Today-KPI (`de/dg/dh/ds`). Im Code begründet (Smali), nur nicht in der auto-gen Tabelle. |
| M5 | BLE-Write nutzt ein Frida-verifiziertes Binärformat; das in den Smali-Docs beschriebene ASCII-Hex-Format (`DFED0001…`) ist die ältere Beschreibung. (Diesen Lauf nicht erneut verifiziert.) |

### E. Gegen `source-of-truth/` verifiziert (dieser Durchgang)

- **CT/Subdevice-Felder ↔ `source-of-truth/jackery_entity_field_candidates_v2.html`: konform.** `const.py:551-591` ist explizit darauf verankert — CtSub (`funForm`/`schePhase`/`tPhasePw`/`tnPhasePw`/`a-c(n)PhasePw`), AccCTBody (`volt`/`curr`/`power`/`freq`/`fact`/`ap`/`rep` je Phase), PlugSub (`switchSta`/`sysSwitch`/`socketPri`/`inPw`/`outPw`), BatteryPackSub/CollectorSub/AccSocketBody. Keine Abweichung.
- **Savings ↔ `APP_CLOUD_VALUES.html`: konform.** `_calculated_savings_from_year` (util.py:1628-1714) = (`totalOutGridEnergy` − `totalInGridEnergy` − `totalOutCtEnergy`), begrenzt auf `totalHomeEgy`, × `singlePrice`; `battery_gap`/`conversion_loss`/`pv_residual` separate Diagnose; `totalRevenue` roh.
- **Backfill one-way/same-endpoint ↔ `DATA_SOURCE_PRIORITY.html`: korrekt** — eine Erweiterung (S5).

### F. Abweichung Backfill

| ID | Befund | Ort |
|---|---|---|
| **S5** | Year-Month-Backfill deckt zusätzlich `CT_STAT` (`totalCtInput/Output`) und `EPS_STAT` (`totalIn/OutEps`) ab; `DATA_SOURCE_PRIORITY.html` dokumentiert nur pv/battery/onGrid/sys-home-trends. Gleiche one-way-same-endpoint-Logik, in der html aber nicht gelistet → undokumentierte, konsistente Erweiterung. | `coordinator.py:710-717` |

### G. BLE / Credentials ↔ Smali-BLE-html + MQTT_PROTOCOL.html (verifiziert)

**Konform:** Service-UUID `0000bdee…` (ble.py:110), ASCII-Frame-Definition `DFED0001%s%s%s%s0001%s%s` (ble.py:72-79/491-507), Chunk-Byte-Budget `MTU-60` (= dok. `(MTU-60)*2` Hex; ble.py:654-668), MQTT-Credential-Ableitung (key=32 / iv=raw[:16] / plaintext=username / AES-256-CBC, vs `MQTT_PROTOCOL.html`), Third-Party-MQTT 3046/3047/113/114 (vs `hbxn_commands.html`).

**Abweichungen:**

| ID | Befund | Ort |
|---|---|---|
| **S6** | Aktiver Write-Pfad nutzt das **Binärformat** (`build_binary_frame`, Version `0x0064`), NICHT das html-dokumentierte ASCII-`DFED0001…` (Version `0001`). Der konforme ASCII-Builder (`build_plaintext_frame`) existiert, wird vom Write-Pfad aber nicht genutzt. (Code-Kommentar: Frida-Capture 2026-05-16.) | ble.py:295/344/375 vs 491-507; ble_transport.py:395 |
| **S7** | Portable/Box-Frames `DFEC00…`/`DFEC80…` sind in `source-of-truth/` dokumentiert, aber nicht implementiert (Home-Family-Scope). | ble.py (nur DFED) |
| n/a | Write/Notify-Char-UUIDs `0000ee01`/`ee02` und der BLE-Frame-AES-Codec stehen NICHT in `source-of-truth/` (Klasse `Lbb/c` fehlt dort) — im Code aus Live-Capture; gegen `source-of-truth/` nicht verifizierbar. | ble.py:113/116 |

**Verifiziert korrekt (keine Abweichung):** alle implementierten HTTP-Endpunkte (Pfad/Verb/Params), Periodenbereiche, alle Live-Sensoren (Richtung/Einheit), alle Setter (actionId/messageType/cmd/Body/Wert gegen Smali+Frida), intern/stack/pack-Trennung, MQTT-Credential-Ableitung, unique_id-Basismuster.

---

## §1 — Verbindungen

### HTTP (Cloud)
- `BASE_URL = https://iot.jackeryapp.com` (const.py:10), Prefix `/v1/`.
- Header: `token`, `platform=2`, `app_version*`, `sys_version`, `model`, `network`, `Accept-Language`.
- Login `POST /v1/auth/login`: `aesEncryptData` (AES-128-ECB-PKCS7 LoginBean-JSON) + `rsaForAesKey` (RSA/ECB/PKCS1). Liefert `userId`, `token`, `mqttPassWord`. `macId` = AndroidId oder MD5→UUIDv3.

### MQTT (Cloud-Push, primär für Live)
- `emqx.jackeryapp.com:8883`, TLS1.2+, system-truststore + `jackery_ca.crt`. `VERIFY_X509_STRICT` gezielt gecligt (nur dieses Bit), Hostname/Chain bleiben geprüft.
- Credential-Ableitung (api.py): `clientId=<userId>@APP`; `username=<userId>@<macId>`; `raw=base64decode(mqttPassWord)` (32 B); `key=raw`, `iv=raw[:16]`, plaintext=`username`; `password=base64(AES-256-CBC-PKCS7(...))`.
- Topics: SUB `hb/app/<userId>/{device,alert,config,notice}`; PUB `…/command`. `…/action` definiert, bewusst ungenutzt.
- Envelope: `{deviceSn,id=ts,version=0,messageType,actionId,timestamp=ts,body}`; `cmd` nur in `body` wenn `cmd>0` (Weather-Kmds lassen es weg).
- On-Connect: `QueryCombineData` 3019/120, `QueryWeatherPlan` 3020/23, `QuerySubDeviceGroupProperty`.

### BLE (optional)
- Service `0000bdee-…`, Write `…ee01`, Notify `…ee02`, Manufacturer `0x4802`. AES key {16,32}, IV 16. Write-Pfad = Frida-verifiziertes Binärformat (M5).

### Third-Party-MQTT-Bridge (Service)
- SET `ThirdPartMQTTConfig` 3046/cmd113, GET `QueryThirdPartMQTTConfig` 3047/cmd114; Body `enable,ip,port,userName,password,token` (user/pw/token AES-codiert via bluetoothKey).

---

## §2 — HTTP-Endpunkte (alle verifiziert korrekt: Pfad/Verb/Params = Katalog)

| Pfad | Verb | Params (Code) | → Sensoren/Section |
|---|---|---|---|
| `/v1/auth/login` | POST | aesEncryptData,rsaForAesKey | Token+mqttPassWord |
| `/v1/device/system/list` | GET | — | Discovery |
| `/v1/device/bind/list` | GET | — | Legacy-Fallback |
| `/v1/device/property` | GET | deviceId | §3 Live (HTTP-Fallback zu MQTT) |
| `/v1/api/alarm` | GET | systemId | `alarm_count` |
| `/v1/device/stat/systemStatistic` | GET | systemId | `today_load,total_generation,total_revenue,total_carbon_saved` |
| `/v1/device/stat/deviceStatistic` | GET | deviceId | `device_today_ongrid_to_battery/pv_to_battery/battery_to_ongrid` (+Fallbacks) |
| `/v1/device/stat/pv` | GET | deviceId,systemId,dateType,begin,end | `pv_*_energy`, `device_pv{1..4}_*_energy` |
| `/v1/device/stat/battery` | GET | deviceId,dateType,begin,end | `battery_{charge,discharge}_*_energy` |
| `/v1/device/stat/onGrid` | GET | deviceId,dateType,begin,end | `device_ongrid_{input,output}_*_energy` |
| `/v1/device/stat/ct` | GET | deviceId(=CT-Acc),dateType,begin,end | CT-Jahreswerte (Ersparnis) |
| `/v1/device/stat/eps` | GET | deviceId,dateType,begin,end | `eps_{input,output}_*_energy` (M4) |
| `/v1/device/stat/meter` | GET | deviceId | Meter-Panel |
| `/v1/device/stat/socket` | GET | deviceId,dateType,begin,end | Socket-Chart |
| `/v1/device/stat/smartSocketStatistic` | GET | smartSocketId | Socket today/total |
| `/v1/device/stat/today` | GET | deviceSn | `today_{feed_in,grid_import,home_load,battery}_energy` (M4) |
| `/v1/device/stat/sys/pv/trends` | GET | systemId,dateType,begin,end | PV-Trend |
| `/v1/device/stat/sys/home/trends` | GET | systemId,dateType,begin,end | `home_*_energy` |
| `/v1/device/stat/sys/battery/trends` | GET | systemId,dateType,begin,end | Batterietrend |
| `/v1/device/dynamic/powerPriceConfig` | GET | systemId | `power_price` |
| `/v1/device/dynamic/priceCompany` | GET | systemId | Select-Optionen |
| `/v1/device/dynamic/historyConfig` | GET | systemId | — |
| `/v1/device/dynamic/saveSingleMode` | POST | systemId,singlePrice,currency | Setter |
| `/v1/device/dynamic/saveDynamicMode` | POST | systemId,platformCompanyId,systemRegion | Setter |
| `/v1/device/battery/pack/list` | GET | deviceSn | §4 Packs (Fallback; SolarVault data:null) |
| `/v1/device/ota/list` | GET | deviceSnList | Pack-Firmware |
| `/v1/device/location` | GET | deviceId | — |
| `/v1/device/system/name` | PUT | systemName,id | Text `system_name` |
| `/v1/device/shelly/devices`, `…/realtime-power`, `…/control` | GET/POST | (Shelly Cloud) | Shelly-Plug-Pfad |
| `/v1/device/deviceMaxPowerRecord/saveRecord` | POST | deviceId,maxPower | (nicht verdrahtet) |

Periodenbereiche (`util.py` `app_period_range`): day=heute..heute, week=Mo..So, month=1..letzter, year=01.01..31.12 — month/year nie today..today. Verifiziert.

---

## §3 — Live-Sensoren (HTTP property ⊕ MQTT device-Topic) — alle Richtungen/Einheiten verifiziert

| Entity | Payload-Key | Hinweis |
|---|---|---|
| soc / bat_soc | soc / batSoc | System- vs interner Batterie-SOC |
| cell_temperature | cellTemp ÷10 | M1 (Skalierung nicht doc-belegt) |
| battery_charge_power / _discharge_power | batInPw / batOutPw | intern; charge=in, discharge=out |
| stack_in_power / stack_out_power | stackInPw / stackOutPw | kompletter Stack (M3) |
| pv_power_total / pv1..4_power | pvPw / pv1..4→.pvPw | verschachteltes PV-Objekt |
| grid_in_power / grid_out_power | _prop_any(in*/out* je 3 Felder) | M2; Richtung korrekt |
| eps_in_power / eps_out_power | swEpsInPw / swEpsOutPw | nicht vertauscht |
| soc_charge_limit / soc_discharge_limit | socChgLimit / socDischgLimit | (+Alias-Fallbacks) |
| max_output_power / max_inverter_power | maxOutPw / maxInvStdPw | |
| max_system_output/input_power | maxSysOutPw / maxSysInPw | |
| battery_count / battery_state | batNum / batState | |
| work_mode / auto_standby / temp_unit | workModel / isAutoStandby / tempUnit | |
| system/ongrid/ct/grid_state | stat / ongridStat / ctStat / gridSate | S4: Phantom-Aliasse als Fallback |
| off_grid_time / off_grid_shutdown_state | offGridTime / offGridDown | |
| default/standby/other_load/energy_plan/charge_plan_power | defaultPw/standbyPw/otherLoadPw/energyPlanPw/chargePlanPw | chargePlanPw → M3 |
| follow_meter_state | isFollowMeterPw | |
| storm_warning_enabled / _minutes | wps / (wpc, minsInterval) | |
| eps_switch_state / reboot_flag | swEpsState / reboot | |
| wifi_*, mac, eth_port, ability_bits, max_iot_num | wsig/wname/wip, mac, ethPort, ability, maxIotNum | Diagnose |

Intern (`batInPw/batOutPw`) vs Stack (`stackInPw/stackOutPw`) vs Pack (`inPw/outPw`): 3 getrennte Entity-Sätze; Main wird durch `SUBDEVICE_ONLY_PROPERTY_KEYS`-Sanitizing + `SUBDEVICE_MAIN_MIRROR_KEYS`-Whitelist nachweislich nicht von Pack/Stack überschrieben. Verifiziert.

---

## §4 — MQTT-Subdevices

| devType | Gerät | Query actionId/cmd | Bucket |
|---|---|---|---|
| 1 | Battery-Pack | 3014/110 | PAYLOAD_BATTERY_PACKS |
| 3 | CT/Smart-Meter | 3031/110 | PAYLOAD_CT_METER |
| 2 | Combo | 3037/110 | — |
| 4 | Meter-Head | 3033/110 | PAYLOAD_METER_HEADS |
| 6 | Socket | 3032/110 | PAYLOAD_SMART_PLUGS |

CT-Netz = positiver Import (`aPhasePw/bPhasePw/cPhasePw`/`tPhasePw`) − positiver Export (`an/bn/cnPhasePw`/`tnPhasePw`) via `directional_power_value`. Pack: soc←batSoc, charge←inPw, discharge←outPw, cellTemp nur wenn Pack-Payload es führt; HTTP pack/list nur Fallback. (Erst-Lauf verifiziert; CT/Subdevice-Agent dieses Laufs traf Session-Limit — §0 E.)

---

## §5 — Setter (Getter/Setter) — gegen Smali + Frida verifiziert, alle korrekt

Envelope via `_async_publish_command` (version=0, ts==id). `cmd` bei Weather-Kmds weggelassen.

| Entity/Service | actionId | messageType | cmd | Body | Quelle |
|---|---|---|---|---|---|
| soc_charge_limit + soc_discharge_limit | **3028** | DevicePropertyChange | 107 | socChgLimit + socDischgLimit (kombiniert) | Smali SET_CHARGE_DISCHARGE_LINE (S3) |
| eps_output | **3022** | DevicePropertyChange | 107 | swEps | Smali CONTROL_AC_OFF_GRID_SWITCH + Frida (S1) |
| standby | **3023** | DevicePropertyChange | 107 | autoStandby (1/2) | Smali CONTROL_STANDBY (S1) |
| max_output_power_set | **3038** | DevicePropertyChange | **107** | maxOutPw | Frida 2026-05-14 (S2) |
| max_feed_grid | 3029 | ControlCombine | 121 | maxFeedGrid | |
| work_mode_select | 3027 | ControlCombine | 121 | workModel {2,4,7,8} | |
| auto_standby_set | 3021 | ControlCombine | 121 | isAutoStandby | |
| off_grid_shutdown | 3039 | ControlCombine | 121 | offGridDown | |
| auto_off_island_mode | 3040 | ControlCombine | 121 | offGridTime (=h×60) | |
| temp_unit_select | 3041 | ControlCombine | 121 | tempUnit {0,1} | |
| default_power_set | 3043 | ControlCombine | 121 | defaultPw | |
| follow_meter | 3044 | ControlCombine | 121 | isFollowMeterPw | |
| storm_warning | 3036 | ControlCombine | 0 | wps | |
| storm_warning_minutes_select | 3034 | SendWeatherAlert | 0 | minsInterval | |
| delete_storm_alert | 3035 | CancelWeatherAlert | 0 | alertId | |
| reboot_device | 3030 | DevicePropertyChange | 107 | reboot=1 | |
| ct_phase_select | 3026 | ControlSubDevice | 111 | devType=3,deviceSn,schePhase | |
| smart_plug_*_switch | 3024 | ControlSubDevice | 111 | devType=6,deviceSn,sysSwitch | |
| smart_plug_*_priority_enabled | 3025 | ControlSubDevice | 111 | devType=6,deviceSn,socketPri | |
| system_name (Text/Service) | — | HTTP PUT system/name | — | systemName,id | |
| Tarif single / dynamic | — | HTTP POST saveSingleMode / saveDynamicMode | — | s. §2 | |
| set/query_third_party_mqtt_config | 3046/3047 | ThirdPart…/Query… | 113/114 | config-body | |
| send_device_schedule | 3015-3018 | DownloadDeviceSchedule | 112 | caller-body | |

**Keine falsch verdrahteten Setter.** Die früher gemeldeten EPS/SOC/maxOut-„Abweichungen" sind S1–S3 (Doku veraltet, Code korrekt).

---

## §6 — Statistiken & Backfill (Erst-Lauf; §0 E)

Section→Endpoint→stat-key→Serie: PV `device_pv_stat_*`/stat/pv (`y=totalSolarEnergy`, `y1..y4=pv1..4Egy`); Batterie `device_battery_stat_*`/stat/battery (`y1=totalCharge`, `y2=totalDischarge`); Netzseite `device_home_stat_*`/stat/onGrid (`y1=totalInGridEnergy`, `y2=totalOutGridEnergy`); Haus `home_trends_*`/sys/home/trends (`y=totalHomeEgy`); System `statistic`/systemStatistic.

Year-Month-Backfill: one-way (Cloud-Jahr bleibt wenn ≥ Monatssumme, sonst Monatssumme als Lower Bound; nie senken; nie Woche→Monat/Jahr; nie Jahr→Lifetime). Ersparnis `_calculated_savings_from_year`: (totalOutGridEnergy − totalInGridEnergy − totalOutCtEnergy), begrenzt auf totalHomeEgy, × singlePrice; `totalRevenue` bleibt roh.

---

## §7 — unique_id / Entitäts-Identität

- Basis `entity.py`: `f"{device_id}_{key}"`. Kein Anzeigename/Label im unique_id. Hauptplattformen konform.
- Battery-Pack: `…_battery_pack_<index>_<suffix>` (1-basiert Erzeugung). Stale-Removal wandelt die 0-basierte Drop-Position vor dem Device-Identifier wieder auf 1-basiert.
- Meter-Heads und Smart-Plugs: Unique-ID-/Device-Suffix über stabile Serial/ID (`stable_subdevice_key()`); Werte werden per gespeicherter Serial/ID aus sortierten Payloads aufgelöst.
- Smart-Meter (singular): `…_smart_meter_<key>` — stabil.
- Churn-Schutz: App/MQTT/Combine-Entitäten werden bei transienten Payload-Lücken nicht entfernt (`__init__.py`). Einziger Device-Removal-Pfad = Battery-Pack-Stale.
- Plattformen: sensor, binary_sensor, switch, text, number, select, button.
