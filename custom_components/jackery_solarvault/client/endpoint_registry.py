"""CSV-backed Jackery HTTP endpoint contract registry."""

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class JackeryEndpointMapping:
    """Single CSV endpoint mapped to a client implementation or exemption."""

    source_class: str
    source_method: str
    path: str
    request_fields: tuple[str, ...]
    client_method: str | None
    http_method: str | None
    auth_required: bool
    exemption_reason: str | None = None

    @property
    def implemented(self) -> bool:
        """Return true when the endpoint has a client method."""
        return self.client_method is not None

    @property
    def exempted(self) -> bool:
        """Return true when a missing endpoint is intentionally exempted."""
        return self.exemption_reason is not None


CSV_ENDPOINT_SOURCE: Path = (
    Path(__file__).resolve().parents[3]
    / "source-of-truth"
    / "jackery_http_api_endpoints_v2.csv"
)
CLIENT_ENDPOINTS: dict[str, tuple[str, str]] = {
    "api/agreeUpgrade": ("async_agree_privacy_consent", "POST"),
    "api/alarm": ("async_get_alarm", "GET"),
    "api/alarm/detail": ("async_get_alarm_detail", "GET"),
    "api/diy/gcsList": ("async_get_gcs_list", "GET"),
    "api/diy/zoneList": ("async_get_zone_list", "GET"),
    "api/faq/answer": ("async_get_faq_answer", "GET"),
    "api/faqList": ("async_get_faq_list", "GET"),
    "api/file/feedback": ("async_submit_feedback", "POST"),
    "api/instruction": ("async_get_product_instruction", "GET"),
    "api/isUpgradeRequired": ("async_check_privacy_update", "GET"),
    "api/push/configGet": ("async_get_push_config", "GET"),
    "api/push/configSet": ("async_set_push_config", "POST"),
    "api/push/notifyList": ("async_get_notify_list", "GET"),
    "api/push/unreadCount": ("async_get_unread_count", "GET"),
    "app/banner/list": ("async_get_banner_list", "GET"),
    "app/version/getNewVersion": ("async_check_app_version", "GET"),
    "auth/cancel": ("async_cancel_account", "POST"),
    "auth/check_verification": ("async_check_verification_code", "POST"),
    "auth/headimg": ("async_upload_headimg", "POST"),
    "auth/login": ("async_login", "POST"),
    "auth/loginOut": ("async_logout", "POST"),
    "auth/modifyInfo": ("async_update_user_info", "POST"),
    "auth/modifyPassword": ("async_reset_password", "POST"),
    "auth/register": ("async_register", "POST"),
    "auth/updateRegisterId": ("async_update_register_id", "POST"),
    "auth/verificationCode": ("async_send_verification_code", "POST"),
    "device/accept_bind": ("async_accept_shared_device", "POST"),
    "device/accessories": ("async_get_accessories", "GET"),
    "device/accessories/exist": ("async_check_accessories_exist", "GET"),
    "device/accessories/exists": ("async_check_jackery_accessories_exist", "GET"),
    "device/accessories/list": ("async_get_accessories_list", "GET"),
    "device/accessories/name": ("async_set_accessories_name", "POST"),
    "device/accessories/synchronizeSmartAccessoriesData": (
        "async_sync_smart_accessories",
        "POST",
    ),
    "device/alert": ("async_sync_alerts", "POST"),
    "device/battery/pack/list": ("async_get_battery_pack_list", "GET"),
    "device/bind": ("async_bind_device", "POST"),
    "device/bind/list": ("async_list_devices_legacy", "GET"),
    "device/bind/nickname": ("async_set_device_nickname", "POST"),
    "device/bind/remove": ("async_remove_shared_access", "POST"),
    "device/bind/removeAll": ("async_remove_all_shared_access", "POST"),
    "device/bind/share/list": ("async_get_device_shared_managers", "GET"),
    "device/bind/shared": ("async_get_device_shared_list", "GET"),
    "device/chargeReport": ("async_get_charge_report", "GET"),
    "device/currencies/bindCurrency": ("async_bind_currency", "POST"),
    "device/currencies/currencyList": ("async_get_currency_list", "GET"),
    "device/currencies/deviceCurrency": ("async_get_device_currency", "GET"),
    "device/deviceMaxPowerRecord/saveRecord": ("async_set_max_power", "POST"),
    "device/dynamic/cancelContractAuth": ("async_cancel_contract_auth", "POST"),
    "device/dynamic/contractList": ("async_get_contract_list", "GET"),
    "device/dynamic/dynamicPrice": ("async_get_dynamic_price", "GET"),
    "device/dynamic/historyConfig": ("async_get_price_history_config", "GET"),
    "device/dynamic/loginUrl": ("async_get_dynamic_price_login_url", "GET"),
    "device/dynamic/powerPriceConfig": ("async_get_power_price", "GET"),
    "device/dynamic/priceCompany": ("async_get_price_sources", "GET"),
    "device/dynamic/saveContractAuth": ("async_save_contract_auth", "POST"),
    "device/dynamic/saveDynamicMode": ("async_set_dynamic_mode", "POST"),
    "device/dynamic/saveLocationId": ("async_save_location_id", "POST"),
    "device/dynamic/saveSingleMode": ("async_set_single_mode", "POST"),
    "device/location": ("async_get_location", "GET"),
    "device/offline/stat": ("async_get_offline_statistics", "GET"),
    "device/ota/bluetooth": ("async_get_ble_ota_link", "GET"),
    "device/ota/list": ("async_get_ota_info", "GET"),
    "device/ota/update": ("async_start_ota_update", "POST"),
    "device/ota/version/list": ("async_get_ble_ota_versions", "POST"),
    "device/property": ("async_get_device_property", "GET"),
    "device/property/power3": ("async_get_power3", "GET"),
    "device/property/pv": ("async_modify_pv_name", "POST"),
    "device/property/subShadow": ("async_get_sub_shadow", "GET"),
    "device/property/systemShadow": ("async_get_system_shadow", "GET"),
    "device/shelly/binding/failures": ("async_get_shelly_binding_failures", "GET"),
    "device/shelly/devices": ("async_get_shelly_devices", "GET"),
    "device/smartMode/checkIfSet": ("async_check_smart_mode_set", "POST"),
    "device/smartMode/getSmartMode": ("async_get_smart_mode_info", "GET"),
    "device/smartMode/startSmartMode": ("async_start_smart_mode", "POST"),
    "device/stat": ("async_get_box_stat", "GET"),
    "device/stat/battery": ("async_get_device_battery_stat", "GET"),
    "device/stat/carbon": ("async_get_carbon_stat", "GET"),
    "device/stat/ct": ("async_get_device_ct_stat", "GET"),
    "device/stat/ct/statics": ("async_get_portable_ct_stat", "GET"),
    "device/stat/cutoff": ("async_get_cutoff_stat", "GET"),
    "device/stat/deviceStatistic": ("async_get_device_statistic", "GET"),
    "device/stat/eps": ("async_get_device_eps_stat", "GET"),
    "device/stat/getSmartSchedulePrediction": (
        "async_get_smart_schedule_prediction",
        "GET",
    ),
    "device/stat/meter": ("async_get_device_meter_stat", "GET"),
    "device/stat/onGrid": ("async_get_device_home_stat", "GET"),
    "device/stat/profit": ("async_get_profit_stat", "GET"),
    "device/stat/pv": ("async_get_device_pv_stat", "GET"),
    "device/stat/smartSocketStatistic": ("async_get_device_socket_statistic", "GET"),
    "device/stat/soc": ("async_get_soc_stat", "GET"),
    "device/stat/socket": ("async_get_device_socket_stat", "GET"),
    "device/stat/symmetry": ("async_get_symmetry_stat", "GET"),
    "device/stat/sys/battery/trends": ("async_get_battery_trends", "GET"),
    "device/stat/sys/home/trends": ("async_get_home_trends", "GET"),
    "device/stat/sys/pv/trends": ("async_get_pv_trends", "GET"),
    "device/stat/systemStatistic": ("async_get_system_statistic", "GET"),
    "device/stat/today": ("async_get_today_energy", "GET"),
    "device/system": ("async_create_system", "POST"),
    "device/system/deviceName": ("async_modify_device_name", "POST"),
    "device/system/exist": ("async_check_system_bound", "GET"),
    "device/system/list": ("async_get_system_list", "GET"),
    "device/system/name": ("async_set_system_name", "PUT"),
    "device/tou/queryTouPlan": ("async_query_tou_plan", "GET"),
    "device/tou/saveTouPlan": ("async_save_tou_plan", "POST"),
    "device/unbind": ("async_unbind_device", "POST"),
    "user/info": ("async_get_user_info", "GET"),
    "wss-cloud/device/shelly/auth-url": ("async_get_shelly_auth_url", "POST"),
    "wss-cloud/device/shelly/device/control": ("async_control_shelly_device", "POST"),
    "wss-cloud/device/shelly/device/realtime-power": (
        "async_get_shelly_realtime_power",
        "GET",
    ),
    "wss-cloud/device/shelly/unbind/account": ("async_unbind_shelly_account", "POST"),
    "wss-cloud/device/shelly/unbind/device": ("async_unbind_shelly_device", "POST"),
}

EXEMPT_ENDPOINTS: dict[str, str] = {
    "auth/generatedJwt": (
        "mobile app push JWT; HA does not register mobile push identity"
    ),
    "device/bind/qrcode": (
        "mobile QR pairing; HA config flow uses bindKey/manual credentials"
    ),
    "device/bluetoothKey": (
        "mobile BLE key fetch; HA captures bluetoothKey from MQTT/discovery"
    ),
}


def normalize_endpoint_path(path: str) -> str:
    """Normalize CSV and client constants to comparable endpoint paths."""
    return path.strip().removeprefix("/").removeprefix("v1/")


def parse_request_fields(value: str) -> tuple[str, ...]:
    """Parse comma-separated request fields from the reverse-engineered CSV."""
    return tuple(field.strip() for field in value.split(",") if field.strip())


def load_csv_endpoint_mapping(
    csv_path: Path = CSV_ENDPOINT_SOURCE,
) -> dict[str, JackeryEndpointMapping]:
    """Load Jackery reverse-engineered endpoint CSV into the client contract map."""
    mappings: dict[str, JackeryEndpointMapping] = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            path = normalize_endpoint_path(row["path"])
            client = CLIENT_ENDPOINTS.get(path)
            mappings[path] = JackeryEndpointMapping(
                source_class=row["class"],
                source_method=row["method"],
                path=path,
                request_fields=parse_request_fields(row["request_fields"]),
                client_method=client[0] if client else None,
                http_method=client[1] if client else None,
                auth_required=path != "auth/login",
                exemption_reason=EXEMPT_ENDPOINTS.get(path),
            )
    return mappings


def implemented_endpoint_mapping() -> dict[str, JackeryEndpointMapping]:
    """Return every implemented or explicitly exempted CSV endpoint."""
    return load_csv_endpoint_mapping()


def render_endpoint_mapping_markdown(mapping: dict[str, JackeryEndpointMapping]) -> str:
    """Render documentation from the endpoint mapping structure."""
    rows = [
        "# Jackery HTTP Endpoint Mapping",
        "",
        (
            "Generated from `source-of-truth/jackery_http_api_endpoints_v2.csv` "
            "via `endpoint_registry.py`."
        ),
        "",
        "| CSV path | Client method | HTTP | Auth | Request fields | Status |",
        "|---|---|---|---|---|---|",
    ]
    for endpoint in sorted(mapping.values(), key=lambda item: item.path):
        status = (
            "implemented"
            if endpoint.implemented
            else f"exempt: {endpoint.exemption_reason}"
        )
        fields = ", ".join(endpoint.request_fields) or "—"
        rows.append(
            f"| `{endpoint.path}` | `{endpoint.client_method or '—'}` | "
            f"{endpoint.http_method or '—'} | "
            f"{'yes' if endpoint.auth_required else 'no'} | "
            f"{fields} | {status} |",
        )
    rows.append("")
    return "\n".join(rows)
