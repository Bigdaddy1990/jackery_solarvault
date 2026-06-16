# Jackery HTTP Endpoint Mapping

Generated from `source-of-truth/jackery_http_api_endpoints_v2.csv` via `endpoint_registry.py`.

| CSV path | Client method | HTTP | Auth | Request fields | Status |
|---|---|---|---|---|---|
| `api/agreeUpgrade` | `async_agree_privacy_consent` | POST | yes | pendingAgreeVersionIds | implemented |
| `api/alarm` | `async_get_alarm` | GET | yes | systemId | implemented |
| `api/alarm/detail` | `async_get_alarm_detail` | GET | yes | alarmKey | implemented |
| `api/diy/gcsList` | `async_get_gcs_list` | GET | yes | country | implemented |
| `api/diy/zoneList` | `async_get_zone_list` | GET | yes | — | implemented |
| `api/faq/answer` | `async_get_faq_answer` | GET | yes | — | implemented |
| `api/faqList` | `async_get_faq_list` | GET | yes | — | implemented |
| `api/file/feedback` | `async_submit_feedback` | POST | yes | contactInfo, content, deviceSn, image | implemented |
| `api/instruction` | `async_get_product_instruction` | GET | yes | devSn, type | implemented |
| `api/isUpgradeRequired` | `async_check_privacy_update` | GET | yes | — | implemented |
| `api/push/configGet` | `async_get_push_config` | GET | yes | — | implemented |
| `api/push/configSet` | `async_set_push_config` | POST | yes | set | implemented |
| `api/push/notifyList` | `async_get_notify_list` | GET | yes | currentTime, deviceSn, pageNo, pageSize | implemented |
| `api/push/unreadCount` | `async_get_unread_count` | GET | yes | — | implemented |
| `app/banner/list` | `async_get_banner_list` | GET | yes | — | implemented |
| `app/version/getNewVersion` | `async_check_app_version` | GET | yes | type, versionName | implemented |
| `auth/cancel` | `async_cancel_account` | POST | yes | email, verificationCode | implemented |
| `auth/check_verification` | `async_check_verification_code` | POST | yes | code, email, method, phone | implemented |
| `auth/generatedJwt` | `—` | — | yes | — | exempt: mobile app push JWT; HA does not register mobile push identity |
| `auth/headimg` | `async_upload_headimg` | POST | yes | image | implemented |
| `auth/login` | `async_login` | POST | no | aesEncryptData, rsaForAesKey | implemented |
| `auth/loginOut` | `async_logout` | POST | yes | — | implemented |
| `auth/modifyInfo` | `async_update_user_info` | POST | yes | nickName | implemented |
| `auth/modifyPassword` | `async_reset_password` | POST | yes | confirmPassword, email, password, verificationCode | implemented |
| `auth/register` | `async_register` | POST | yes | email, password, regionCode, registerAppId, verificationCode | implemented |
| `auth/updateRegisterId` | `async_update_register_id` | POST | yes | registerId | implemented |
| `auth/verificationCode` | `async_send_verification_code` | POST | yes | email, method, phone | implemented |
| `device/accept_bind` | `async_accept_shared_device` | POST | yes | devId, qrCodeId | implemented |
| `device/accessories` | `async_get_accessories` | GET | yes | devices, id, parentDeviceId | implemented |
| `device/accessories/exist` | `async_check_accessories_exist` | GET | yes | devices | implemented |
| `device/accessories/exists` | `async_check_jackery_accessories_exist` | GET | yes | deviceSnInfos | implemented |
| `device/accessories/list` | `async_get_accessories_list` | GET | yes | deviceId | implemented |
| `device/accessories/name` | `async_set_accessories_name` | POST | yes | deviceName, id | implemented |
| `device/accessories/synchronizeSmartAccessoriesData` | `async_sync_smart_accessories` | POST | yes | — | implemented |
| `device/alert` | `async_sync_alerts` | POST | yes | content, id | implemented |
| `device/battery/pack/list` | `async_get_battery_pack_list` | GET | yes | deviceSn | implemented |
| `device/bind` | `async_bind_device` | POST | yes | bindKey, devId, guid, timezoneOffset | implemented |
| `device/bind/list` | `async_list_devices_legacy` | GET | yes | — | implemented |
| `device/bind/nickname` | `async_set_device_nickname` | POST | yes | deviceId, nickname | implemented |
| `device/bind/qrcode` | `—` | — | yes | — | exempt: mobile QR pairing; HA config flow uses bindKey/manual credentials |
| `device/bind/remove` | `async_remove_shared_access` | POST | yes | bindUserId, deviceId | implemented |
| `device/bind/removeAll` | `async_remove_all_shared_access` | POST | yes | bindUserId, level | implemented |
| `device/bind/share/list` | `async_get_device_shared_managers` | GET | yes | bindUserId, level | implemented |
| `device/bind/shared` | `async_get_device_shared_list` | GET | yes | — | implemented |
| `device/bluetoothKey` | `—` | — | yes | deviceSn, guid | exempt: mobile BLE key fetch; HA captures bluetoothKey from MQTT/discovery |
| `device/chargeReport` | `async_get_charge_report` | GET | yes | deviceSn, pageIndex | implemented |
| `device/currencies/bindCurrency` | `async_bind_currency` | POST | yes | currency, deviceId, systemId | implemented |
| `device/currencies/currencyList` | `async_get_currency_list` | GET | yes | — | implemented |
| `device/currencies/deviceCurrency` | `async_get_device_currency` | GET | yes | deviceId | implemented |
| `device/deviceMaxPowerRecord/saveRecord` | `async_set_max_power` | POST | yes | deviceId, maxPower | implemented |
| `device/dynamic/cancelContractAuth` | `async_cancel_contract_auth` | POST | yes | platformCompanyId, systemId | implemented |
| `device/dynamic/contractList` | `async_get_contract_list` | GET | yes | customerNumber, platformCompanyId | implemented |
| `device/dynamic/dynamicPrice` | `async_get_dynamic_price` | GET | yes | systemId | implemented |
| `device/dynamic/historyConfig` | `async_get_price_history_config` | GET | yes | systemId | implemented |
| `device/dynamic/loginUrl` | `async_get_dynamic_price_login_url` | GET | yes | platformCompanyId, systemId | implemented |
| `device/dynamic/powerPriceConfig` | `async_get_power_price` | GET | yes | systemId | implemented |
| `device/dynamic/priceCompany` | `async_get_price_sources` | GET | yes | systemId | implemented |
| `device/dynamic/saveContractAuth` | `async_save_contract_auth` | POST | yes | contractId, customId, platformCompanyId, systemId | implemented |
| `device/dynamic/saveDynamicMode` | `async_set_dynamic_mode` | POST | yes | platformCompanyId, systemId, systemRegion | implemented |
| `device/dynamic/saveLocationId` | `async_save_location_id` | POST | yes | connectToken | implemented |
| `device/dynamic/saveSingleMode` | `async_set_single_mode` | POST | yes | currency, singlePrice, systemId | implemented |
| `device/location` | `async_get_location` | GET | yes | deviceId, latitude, longitude | implemented |
| `device/offline/stat` | `async_get_offline_statistics` | GET | yes | — | implemented |
| `device/ota/bluetooth` | `async_get_ble_ota_link` | GET | yes | deviceSn, subDeviceSn, targetFirmwareIds, targetVersionId | implemented |
| `device/ota/list` | `async_get_ota_info` | GET | yes | deviceSnList | implemented |
| `device/ota/update` | `async_start_ota_update` | POST | yes | deviceSn, subDeviceSn, targetFirmwareIds, targetVersionId | implemented |
| `device/ota/version/list` | `async_get_ble_ota_versions` | POST | yes | list | implemented |
| `device/property` | `async_get_device_property` | GET | yes | deviceId | implemented |
| `device/property/power3` | `async_get_power3` | GET | yes | deviceSn, properties | implemented |
| `device/property/pv` | `async_modify_pv_name` | POST | yes | deviceSn, index, name | implemented |
| `device/property/subShadow` | `async_get_sub_shadow` | GET | yes | devType, deviceSn, subDeviceSn | implemented |
| `device/property/systemShadow` | `async_get_system_shadow` | GET | yes | deviceSn, diySn | implemented |
| `device/shelly/binding/failures` | `async_get_shelly_binding_failures` | GET | yes | state | implemented |
| `device/shelly/devices` | `async_get_shelly_devices` | GET | yes | — | implemented |
| `device/smartMode/checkIfSet` | `async_check_smart_mode_set` | POST | yes | deviceId, systemId | implemented |
| `device/smartMode/getSmartMode` | `async_get_smart_mode_info` | GET | yes | systemId | implemented |
| `device/smartMode/startSmartMode` | `async_start_smart_mode` | POST | yes | systemId | implemented |
| `device/stat` | `async_get_box_stat` | GET | yes | beginDate, dateType, deviceSn, endDate, key | implemented |
| `device/stat/battery` | `async_get_device_battery_stat` | GET | yes | beginDate, dateType, deviceId, endDate | implemented |
| `device/stat/carbon` | `async_get_carbon_stat` | GET | yes | deviceSn | implemented |
| `device/stat/ct` | `async_get_device_ct_stat` | GET | yes | beginDate, dateType, deviceId, endDate | implemented |
| `device/stat/cutoff` | `async_get_cutoff_stat` | GET | yes | beginDate, deviceSn, endDate | implemented |
| `device/stat/deviceStatistic` | `async_get_device_statistic` | GET | yes | deviceId | implemented |
| `device/stat/eps` | `async_get_device_eps_stat` | GET | yes | beginDate, dateType, deviceId, endDate | implemented |
| `device/stat/getSmartSchedulePrediction` | `async_get_smart_schedule_prediction` | GET | yes | systemId | implemented |
| `device/stat/meter` | `async_get_device_meter_stat` | GET | yes | deviceId | implemented |
| `device/stat/onGrid` | `async_get_device_home_stat` | GET | yes | beginDate, dateType, deviceId, endDate | implemented |
| `device/stat/profit` | `async_get_profit_stat` | GET | yes | deviceId | implemented |
| `device/stat/pv` | `async_get_device_pv_stat` | GET | yes | beginDate, dateType, deviceId, endDate, systemId | implemented |
| `device/stat/smartSocketStatistic` | `async_get_device_socket_statistic` | GET | yes | smartSocketId | implemented |
| `device/stat/soc` | `async_get_soc_stat` | GET | yes | deviceId | implemented |
| `device/stat/socket` | `async_get_device_socket_stat` | GET | yes | beginDate, dateType, deviceId, endDate | implemented |
| `device/stat/symmetry` | `async_get_symmetry_stat` | GET | yes | beginDate, dateType, deviceSn, endDate, negative, positive | implemented |
| `device/stat/sys/battery/trends` | `async_get_battery_trends` | GET | yes | beginDate, dateType, endDate, systemId | implemented |
| `device/stat/sys/home/trends` | `async_get_home_trends` | GET | yes | beginDate, dateType, endDate, systemId | implemented |
| `device/stat/sys/pv/trends` | `async_get_pv_trends` | GET | yes | beginDate, dateType, endDate, systemId | implemented |
| `device/stat/systemStatistic` | `async_get_system_statistic` | GET | yes | systemId | implemented |
| `device/stat/today` | `async_get_today_energy` | GET | yes | deviceSn | implemented |
| `device/system` | `async_create_system` | POST | yes | bindKey, countryCode, deviceSn, gridStandard, guid, id, systemName, timezone | implemented |
| `device/system/deviceName` | `async_modify_device_name` | POST | yes | deviceName, id | implemented |
| `device/system/exist` | `async_check_system_bound` | GET | yes | bindKey, deviceSn, guid | implemented |
| `device/system/list` | `async_get_system_list` | GET | yes | — | implemented |
| `device/system/name` | `async_set_system_name` | PUT | yes | id, systemName | implemented |
| `device/tou/saveTouPlan` | `async_save_tou_plan` | POST | yes | deviceId, tasks | implemented |
| `device/unbind` | `async_unbind_device` | POST | yes | deviceId | implemented |
| `user/info` | `async_get_user_info` | GET | yes | — | implemented |
| `wss-cloud/device/shelly/auth-url` | `async_get_shelly_auth_url` | POST | yes | — | implemented |
| `wss-cloud/device/shelly/device/control` | `async_control_shelly_device` | POST | yes | action, deviceId, function | implemented |
| `wss-cloud/device/shelly/device/realtime-power` | `async_get_shelly_realtime_power` | GET | yes | deviceId | implemented |
| `wss-cloud/device/shelly/unbind/account` | `async_unbind_shelly_account` | POST | yes | — | implemented |
| `wss-cloud/device/shelly/unbind/device` | `async_unbind_shelly_device` | POST | yes | bindingId, deviceId | implemented |
