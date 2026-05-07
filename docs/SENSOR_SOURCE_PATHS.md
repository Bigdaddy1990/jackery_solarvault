# Sensor → API/MQTT source path mapping

Auto-generated from `custom_components/jackery_solarvault/sensor.py` against
the path contracts in `APP_POLLING_MQTT.md`, `MQTT_PROTOCOL.md` and
`DATA_SOURCE_PRIORITY.md`. Every entity unique-id is `<device_id>_<key>`
per `UNIQUE_ID_CONTRACT.md`.

## Live sensors (HTTP `/v1/device/property` ⊕ MQTT `device` topic)

Source: APP_POLLING_MQTT.md HTTP `/v1/device/property` (30 s polling) merged
with MQTT push from `hb/app/<userId>/device` topic — actionId 0/3011 for
`DevicePropertyChange`, actionId 3019 for `UploadCombineData` per
MQTT_PROTOCOL.md. MQTT values overlay HTTP values; `_payload_http_prop()`
fallbacks read the unmerged HTTP layer when MQTT has not delivered yet.

| Entity key | Payload key (FIELD_*) | MD reference |
|---|---|---|
| `soc` | `FIELD_SOC` = `soc` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `bat_soc` | `FIELD_BAT_SOC` = `batSoc` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `cell_temperature` | `FIELD_CELL_TEMP` = `cellTemp` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `battery_charge_power` | `FIELD_BAT_IN_PW` = `batInPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `battery_discharge_power` | `FIELD_BAT_OUT_PW` = `batOutPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `pv_power_total` | `FIELD_PV_PW` = `pvPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `pv1_power` | `FIELD_PV1` = `pv1` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `pv2_power` | `FIELD_PV2` = `pv2` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `pv3_power` | `FIELD_PV3` = `pv3` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `pv4_power` | `FIELD_PV4` = `pv4` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `grid_in_power` | `FIELD_IN_ONGRID_PW` = `inOngridPw`, `FIELD_GRID_IN_PW` = `gridInPw`, `FIELD_IN_GRID_SIDE_PW` = `inGridSidePw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `grid_out_power` | `FIELD_OUT_ONGRID_PW` = `outOngridPw`, `FIELD_GRID_OUT_PW` = `gridOutPw`, `FIELD_OUT_GRID_SIDE_PW` = `outGridSidePw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `eps_in_power` | `FIELD_SW_EPS_IN_PW` = `swEpsInPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `eps_out_power` | `FIELD_SW_EPS_OUT_PW` = `swEpsOutPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `stack_in_power` | `FIELD_STACK_IN_PW` = `stackInPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `stack_out_power` | `FIELD_STACK_OUT_PW` = `stackOutPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `wifi_signal` | `FIELD_WSIG` = `wsig` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `wifi_name` | `FIELD_WNAME` = `wname` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `wifi_ip` | `FIELD_WIP` = `wip` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `mac_address` | `FIELD_MAC` = `mac` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `eth_port` | `FIELD_ETH_PORT` = `ethPort` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `ability_bits` | `FIELD_ABILITY` = `ability` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `max_iot_num` | `FIELD_MAX_IOT_NUM` = `maxIotNum` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `eps_switch_state` | `FIELD_SW_EPS_STATE` = `swEpsState` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `reboot_flag` | `FIELD_REBOOT` = `reboot` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `soc_charge_limit` | `FIELD_SOC_CHG_LIMIT` = `socChgLimit`, `FIELD_SOC_CHARGE_LIMIT` = `socChargeLimit` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `soc_discharge_limit` | `FIELD_SOC_DISCHG_LIMIT` = `socDischgLimit`, `FIELD_SOC_DISCHARGE_LIMIT` = `socDischargeLimit` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `max_output_power` | `FIELD_MAX_OUT_PW` = `maxOutPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `max_grid_power` | `FIELD_MAX_GRID_STD_PW` = `maxGridStdPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `max_inverter_power` | `FIELD_MAX_INV_STD_PW` = `maxInvStdPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `battery_count` | `FIELD_BAT_NUM` = `batNum` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `battery_state` | `FIELD_BAT_STATE` = `batState` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `auto_standby` | `FIELD_IS_AUTO_STANDBY` = `isAutoStandby` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `system_state` | `FIELD_STAT` = `stat` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `ongrid_state` | `FIELD_ONGRID_STAT` = `ongridStat`, `FIELD_ON_GRID_STAT` = `onGridStat` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `ct_state` | `FIELD_CT_STAT` = `ctStat`, `FIELD_CT_STATE` = `ctState` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `grid_state` | `FIELD_GRID_STATE` = `gridSate`, `FIELD_GRID_STATE_ALT` = `gridState`, `FIELD_GRID_STAT` = `gridStat` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `work_mode` | `FIELD_WORK_MODEL` = `workModel` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `max_system_output_power` | `FIELD_MAX_SYS_OUT_PW` = `maxSysOutPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `max_system_input_power` | `FIELD_MAX_SYS_IN_PW` = `maxSysInPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `off_grid_time` | `FIELD_OFF_GRID_TIME` = `offGridTime` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `default_power` | `FIELD_DEFAULT_PW` = `defaultPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `standby_power` | `FIELD_STANDBY_PW` = `standbyPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `other_load_power` | `FIELD_OTHER_LOAD_PW` = `otherLoadPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `energy_plan_power` | `FIELD_ENERGY_PLAN_PW` = `energyPlanPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `charge_plan_power` | `FIELD_CHARGE_PLAN_PW` = `chargePlanPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `follow_meter_state` | `FIELD_IS_FOLLOW_METER_PW` = `isFollowMeterPw` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `off_grid_shutdown_state` | `FIELD_OFF_GRID_DOWN` = `offGridDown` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `function_enable_flags` | `FIELD_FUNC_ENABLE` = `funcEnable` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `temp_unit` | `FIELD_TEMP_UNIT` = `tempUnit` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `storm_warning_enabled` | `FIELD_WPS` = `wps` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |
| `storm_warning_minutes` | `FIELD_WPC` = `wpc`, `FIELD_MINS_INTERVAL` = `minsInterval` | MQTT_PROTOCOL.md `DevicePropertyChange` / `UploadCombineData` |

## Statistic sensors (HTTP app endpoints)

Source: APP_POLLING_MQTT.md HTTP table. Each entity selects one app
endpoint via `section` + the chart-series key contract from
DATA_SOURCE_PRIORITY.md. Device `dateType=year` series are passed
through `expanded_year_series_values()` with cross-validation against
the documented total field; if Jackery returns the current month as the
whole year, the coordinator may raise the year buckets via documented
same-endpoint month backfill before sensors read the payload.

| Entity key | Section (`source_section`) | Stat key | HTTP path | Chart series |
|---|---|---|---|---|
| `today_load` | `statistic` | `todayLoad` | `/v1/device/stat/systemStatistic` | `—` |
| `today_battery_charge` | `statistic` | `todayBatteryChg` | `/v1/device/stat/systemStatistic` | `—` |
| `today_battery_discharge` | `statistic` | `todayBatteryDisChg` | `/v1/device/stat/systemStatistic` | `—` |
| `today_generation` | `statistic` | `todayGeneration` | `/v1/device/stat/systemStatistic` | `—` |
| `total_generation` | `statistic` | `totalGeneration` | `/v1/device/stat/systemStatistic` | `—` |
| `total_revenue` | `statistic` | `totalRevenue` | `/v1/device/stat/systemStatistic` | `—` |
| `total_carbon_saved` | `statistic` | `totalCarbon` | `/v1/device/stat/systemStatistic` | `—` |
| `pv_week_energy` | `device_pv_stat_week` | `totalSolarEnergy` | `/v1/device/stat/pv` | `y` |
| `pv_month_energy` | `device_pv_stat_month` | `totalSolarEnergy` | `/v1/device/stat/pv` | `y` |
| `pv_year_energy` | `device_pv_stat_year` | `totalSolarEnergy` | `/v1/device/stat/pv` | `y` |
| `device_pv1_week_energy` | `device_pv_stat_week` | `pv1Egy` | `/v1/device/stat/pv` | `y1` |
| `device_pv1_month_energy` | `device_pv_stat_month` | `pv1Egy` | `/v1/device/stat/pv` | `y1` |
| `device_pv1_year_energy` | `device_pv_stat_year` | `pv1Egy` | `/v1/device/stat/pv` | `y1` |
| `device_pv2_week_energy` | `device_pv_stat_week` | `pv2Egy` | `/v1/device/stat/pv` | `y2` |
| `device_pv2_month_energy` | `device_pv_stat_month` | `pv2Egy` | `/v1/device/stat/pv` | `y2` |
| `device_pv2_year_energy` | `device_pv_stat_year` | `pv2Egy` | `/v1/device/stat/pv` | `y2` |
| `device_pv3_week_energy` | `device_pv_stat_week` | `pv3Egy` | `/v1/device/stat/pv` | `y3` |
| `device_pv3_month_energy` | `device_pv_stat_month` | `pv3Egy` | `/v1/device/stat/pv` | `y3` |
| `device_pv3_year_energy` | `device_pv_stat_year` | `pv3Egy` | `/v1/device/stat/pv` | `y3` |
| `device_pv4_week_energy` | `device_pv_stat_week` | `pv4Egy` | `/v1/device/stat/pv` | `y4` |
| `device_pv4_month_energy` | `device_pv_stat_month` | `pv4Egy` | `/v1/device/stat/pv` | `y4` |
| `device_pv4_year_energy` | `device_pv_stat_year` | `pv4Egy` | `/v1/device/stat/pv` | `y4` |
| `home_week_energy` | `home_trends_week` | `totalHomeEgy` | `/v1/device/stat/sys/home/trends` | `y` |
| `home_month_energy` | `home_trends_month` | `totalHomeEgy` | `/v1/device/stat/sys/home/trends` | `y` |
| `home_year_energy` | `home_trends_year` | `totalHomeEgy` | `/v1/device/stat/sys/home/trends` | `y` |
| `device_ongrid_input_week_energy` | `device_home_stat_week` | `totalInGridEnergy` | `/v1/device/stat/onGrid` | `y1` |
| `device_ongrid_input_month_energy` | `device_home_stat_month` | `totalInGridEnergy` | `/v1/device/stat/onGrid` | `y1` |
| `device_ongrid_input_year_energy` | `device_home_stat_year` | `totalInGridEnergy` | `/v1/device/stat/onGrid` | `y1` |
| `device_ongrid_output_week_energy` | `device_home_stat_week` | `totalOutGridEnergy` | `/v1/device/stat/onGrid` | `y2` |
| `device_ongrid_output_month_energy` | `device_home_stat_month` | `totalOutGridEnergy` | `/v1/device/stat/onGrid` | `y2` |
| `device_ongrid_output_year_energy` | `device_home_stat_year` | `totalOutGridEnergy` | `/v1/device/stat/onGrid` | `y2` |
| `battery_charge_week_energy` | `device_battery_stat_week` | `totalCharge` | `/v1/device/stat/battery` | `y1` |
| `battery_charge_month_energy` | `device_battery_stat_month` | `totalCharge` | `/v1/device/stat/battery` | `y1` |
| `battery_charge_year_energy` | `device_battery_stat_year` | `totalCharge` | `/v1/device/stat/battery` | `y1` |
| `battery_discharge_week_energy` | `device_battery_stat_week` | `totalDischarge` | `/v1/device/stat/battery` | `y2` |
| `battery_discharge_month_energy` | `device_battery_stat_month` | `totalDischarge` | `/v1/device/stat/battery` | `y2` |
| `battery_discharge_year_energy` | `device_battery_stat_year` | `totalDischarge` | `/v1/device/stat/battery` | `y2` |
| `power_price` | `price` | `singlePrice` | `/v1/device/dynamic/powerPriceConfig` | `—` |
| `device_today_pv_energy` | `device_statistic` | `pvEgy` | `/v1/device/stat/deviceStatistic` | `—` |
| `device_today_battery_charge` | `device_statistic` | `batChgEgy` | `/v1/device/stat/deviceStatistic` | `—` |
| `device_today_battery_discharge` | `device_statistic` | `batDisChgEgy` | `/v1/device/stat/deviceStatistic` | `—` |
| `device_today_ongrid_input` | `device_statistic` | `inOngridEgy` | `/v1/device/stat/deviceStatistic` | `—` |
| `device_today_ongrid_output` | `device_statistic` | `outOngridEgy` | `/v1/device/stat/deviceStatistic` | `—` |
| `device_today_ongrid_to_battery` | `device_statistic` | `ongridOtBatEgy` | `/v1/device/stat/deviceStatistic` | `—` |
| `device_today_pv_to_battery` | `device_statistic` | `pvOtBatEgy` | `/v1/device/stat/deviceStatistic` | `—` |
| `device_today_battery_to_ongrid` | `device_statistic` | `batOtGridEgy` | `/v1/device/stat/deviceStatistic` | `—` |

`total_revenue` is published from `statistic.totalRevenue`, but the coordinator
may replace that raw cloud field with calculated house-side savings. The
calculation uses `device_home_stat_year.totalOutGridEnergy`, optional
`device_home_stat_year.totalInGridEnergy`, optional
`device_ct_stat_year.totalOutCtEnergy`, `home_trends_year.totalHomeEgy`, and
`price.singlePrice`; details are exposed on the entity as
`savings_calculation`. Optional detail entities expose each `_savings_calculation` component using the stable `savings_*` keys, plus `conversion_loss_power` as a live estimated residual from the power balance.

## Smart-Meter / CT live values (MQTT only)

Source: MQTT `QuerySubDeviceGroupProperty` actionId 3031 cmd 110 with
`devType=3` per APP_POLLING_MQTT.md. Inbound topic
`hb/app/<userId>/device`, message type
`UploadSubDeviceIncrementalProperty` (cmd 111) per MQTT_PROTOCOL.md.

Phase fields: `aPhasePw`, `bPhasePw`, `cPhasePw` (positive),
`anPhasePw`, `bnPhasePw`, `cnPhasePw` (export). Aggregate
fields: `tPhasePw` / `tnPhasePw` (CT_TOTAL_POWER_PAIR).

Net = positive-import minus positive-export, per `directional_power_value()`.

## Battery packs (MQTT only)

Source: MQTT `QuerySubDeviceGroupProperty` actionId 3014 cmd 110 with
`devType=1`. HTTP `/v1/device/battery/pack/list` returns `data: null`
for SolarVault and is only used as a fallback per APP_POLLING_MQTT.md.

Per-pack fields documented in APP_POLLING_MQTT.md "Zusatzbatterie-Appmodell":
`deviceSn`, `subType`, `commState`, `scanName`, `deviceName`,
`commMode`, `batSoc`, `inPw`, `outPw`, `cellTemp`,
`isFirmwareUpgrade`, `version`.

## Setters (number / select / switch / button / text)

All setters publish `command` topic per MQTT_PROTOCOL.md tables. The
actionId/messageType mapping mirrors APP_POLLING_MQTT.md "MQTT-Write-
Kommandos" exactly. No HTTP-based setters except the cloud-rename and
tariff endpoints (`/v1/device/system/name`, `/v1/device/dynamic/save*`).
