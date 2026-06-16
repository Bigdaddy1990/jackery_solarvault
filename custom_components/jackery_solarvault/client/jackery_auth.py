"""jackery_auth.py — Referenz-Implementierung für eine Jackery
Home-Assistant-Integration.

Verifiziert aus dem Bytecode von com.hbxn.jackery 2.1.1:
  - jc.e.f()            (MQTT-Credential-Aufbau)
  - com.blankj.utilcode.util.e0.q/p/w0   (AES)
  - com.blankj.utilcode.util.d0.a/d      (Base64 decode/encode)
  - com.blankj.utilcode.util.c0.o/q/s    (deviceId / getUniqueDeviceId)
  - com.hbxn.jackery.router.bean.User    (Getter-Mapping)

Benötigt: pip install pycryptodome paho-mqtt requests
"""  # noqa: D205

import base64
import hashlib
import uuid

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------
REST_BASE = "https://iot.jackeryapp.com"  # ggf. + "/v1"
MQTT_HOST = "emqx.jackeryapp.com"
MQTT_PORT = 8883  # TLS


# ---------------------------------------------------------------------------
# MQTT-Auth  (exakt aus jc.e.f() + BlankJ e0/d0)
# ---------------------------------------------------------------------------
def make_mqtt_credentials(user_id: str, mqtt_password_b64: str, device_id: str) -> dict:
    """Identifier = userId + "@APP"
    username   = userId + "@" + deviceId
    key        = base64decode(mqttPassWord)        # 32 Byte -> AES-256
    iv         = key[:16]
    password   = base64encode( AES/CBC/PKCS5(username.utf8, key, iv) ).
    """  # noqa: D205
    identifier = f"{user_id}@APP"
    username = f"{user_id}@{device_id}"
    key = base64.b64decode(mqtt_password_b64)
    iv = key[:16]
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(username.encode("utf-8"), 16))
    password = base64.b64encode(ct).decode()
    return {"client_id": identifier, "username": username, "password": password}


def verify_against_capture(
    mqtt_password_b64: str,
    observed_username: str,
    observed_password_b64: str,
) -> bool:
    """Self-Check mit EINEM zusammengehörigen Capture-Paar (gleiche Login-Session!).
    observed_username ist exakt 'userId@deviceId' aus dem MQTT-CONNECT.
    """  # noqa: D205
    key = base64.b64decode(mqtt_password_b64)
    iv = key[:16]
    ct = AES.new(key, AES.MODE_CBC, iv).encrypt(
        pad(observed_username.encode("utf-8"), 16),
    )
    return base64.b64encode(ct).decode() == observed_password_b64


def decrypt_mqtt_password(mqtt_password_b64: str, password_b64: str) -> bytes:
    """Gegenrichtung: MQTT-Passwort entschlüsseln -> muss den username ergeben."""
    key = base64.b64decode(mqtt_password_b64)
    iv = key[:16]
    return unpad(
        AES.new(key, AES.MODE_CBC, iv).decrypt(base64.b64decode(password_b64)),
        16,
    )


# ---------------------------------------------------------------------------
# deviceId  (BlankJ getUniqueDeviceId: "2" + UUIDv3(androidId) | "9" + randomUUID)
# Fuer HA: einmal generieren, persistent speichern, konstant wiederverwenden.
# ---------------------------------------------------------------------------
def device_id_from_android_id(android_id: str) -> str:  # noqa: D103
    md5 = bytearray(hashlib.md5(android_id.encode()).digest())
    md5[6] = (md5[6] & 0x0F) | 0x30  # UUID version 3
    md5[8] = (md5[8] & 0x3F) | 0x80  # variant
    return "2" + md5.hex()


def random_device_id() -> str:  # noqa: D103
    return "9" + uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Payload-(De)Verschlüsselung  (HYPOTHESE: gleiche AES-Parameter wie Auth)
# Erst nach Capture von hb/app/<sn>/device bestaetigen.
# ---------------------------------------------------------------------------
def try_decrypt_payload(mqtt_password_b64: str, payload_b64: str) -> bytes | None:  # noqa: D103
    try:
        key = base64.b64decode(mqtt_password_b64)
        iv = key[:16]
        return unpad(
            AES.new(key, AES.MODE_CBC, iv).decrypt(base64.b64decode(payload_b64)),
            16,
        )
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# MQTT-Topics
# ---------------------------------------------------------------------------
def topics(device_sn: str) -> dict:  # noqa: D103
    base = f"hb/app/{device_sn}"
    return {
        "device": f"{base}/device",  # sub: Telemetrie/Shadow
        "command": f"{base}/command",  # pub: Steuerbefehle
        "action": f"{base}/action",  # pub: Aktionen/Tasks
        "config": f"{base}/config",  # bidirektional
        "alert": f"{base}/alert",  # sub: Alarme
        "notice": f"{base}/notice",  # sub: Hinweise
    }


# ---------------------------------------------------------------------------
# REST-Endpunkte  (relativ zu REST_BASE; via EasyHttp getApi() extrahiert)
# ---------------------------------------------------------------------------
ENDPOINTS = {
    # auth/account
    "login": "auth/login",
    "logout": "auth/loginOut",
    "register": "auth/register",
    "jwt": "auth/generatedJwt",
    "verification_code": "auth/verificationCode",
    "check_verification": "auth/check_verification",
    "modify_password": "auth/modifyPassword",
    "modify_info": "auth/modifyInfo",
    "headimg": "auth/headimg",
    "cancel": "auth/cancel",
    "update_register_id": "auth/updateRegisterId",
    "user_info": "user/info",
    # device bind / property
    "device_bind": "device/bind",
    "device_unbind": "device/unbind",
    "device_nickname": "device/bind/nickname",
    "device_list": "device/bind/list",
    "device_qrcode": "device/bind/qrcode",
    "device_property": "device/property",
    "device_power3": "device/property/power3",
    "device_timezone": "device/timezone",
    "device_location": "device/location",
    # ota
    "ota_list": "device/ota/list",
    "ota_update": "device/ota/update",
    "ota_ble_versions": "device/ota/version/list",
    "ota_bluetooth": "device/ota/bluetooth",
    # stats
    "stat": "device/stat",
    "stat_soc": "device/stat/soc",
    "stat_today": "device/stat/today",
    "stat_profit": "device/stat/profit",
    "stat_carbon": "device/stat/carbon",
    "stat_pv": "device/stat/pv",
    "stat_battery": "device/stat/battery",
    "stat_ct": "device/stat/ct",
    "stat_meter": "device/stat/meter",
    "stat_socket": "device/stat/socket",
    "stat_ongrid": "device/stat/onGrid",
    "stat_eps": "device/stat/eps",
    "charge_report": "device/chargeReport",
    "stat_sys_battery": "device/stat/sys/battery/trends",
    "stat_sys_home": "device/stat/sys/home/trends",
    "stat_sys_pv": "device/stat/sys/pv/trends",
    # diy system
    "system_list": "device/system/list",
    "system": "device/system",
    "sub_shadow": "device/property/subShadow",
    "system_shadow": "device/property/systemShadow",
    "alert": "device/alert",
    "offline_stat": "device/offline/stat",
    # accessories
    "accessories": "device/accessories",
    "accessories_list": "device/accessories/list",
    "accessories_bind": "device/accessories/bind",
    "accessories_unbind": "device/accessories/unbind",
    "accessories_exist": "device/accessories/exist",
    "battery_pack_list": "device/battery/pack/list",
    "bluetooth_key": "device/bluetoothKey",
    # dynamic price / tou / smart
    "dynamic_price": "device/dynamic/dynamicPrice",
    "tou_query": "device/tou/queryTouPlan",
    "tou_save": "device/tou/saveTouPlan",
    "smart_mode_get": "device/smartMode/getSmartMode",
    "smart_mode_start": "device/smartMode/startSmartMode",
    # shelly cloud2cloud
    "shelly_auth_url": "wss-cloud/device/shelly/auth-url",
    "shelly_devices": "device/shelly/devices",
    "shelly_control": "wss-cloud/device/shelly/device/control",
    "shelly_realtime_power": "wss-cloud/device/shelly/device/realtime-power",
    "shelly_unbind_device": "wss-cloud/device/shelly/unbind/device",
    "shelly_unbind_account": "wss-cloud/device/shelly/unbind/account",
    # misc
    "app_version": "app/version/getNewVersion",
    "push_list": "api/push/notifyList",
    "alarm_list": "api/alarm",
}

# ---------------------------------------------------------------------------
# Steuerbefehle (MQTT command/action) — Konstanten-Namen aus dem Control-Layer
# ---------------------------------------------------------------------------
COMMANDS = {
    "output": [
        "CONTROL_OUTPUT_AC",
        "CONTROL_OUTPUT_AC240",
        "CONTROL_OUTPUT_DC",
        "CONTROL_OUTPUT_DC_CAR",
        "CONTROL_OUTPUT_DC_USB",
        "CONTROL_OUTPUT_PRIORITY_SWITCH",
        "CONTROL_AC_OFF_GRID_SWITCH",
        "CONTROL_MAX_OUT_PW",
        "AC_OUTPUT_MODE",
        "AC_OUTPUT_COUNTDOWN",
        "AC_OUTPUT_DELAY_OPEN_TIME",
        "DC_OUTPUT_COUNTDOWN",
        "DC_CAR_OUTPUT_COUNTDOWN",
        "DC_USB_OUTPUT_COUNTDOWN",
    ],
    "device": [
        "CONTROL_LIGHT",
        "CONTROL_SCREEN",
        "CONTROL_STANDBY",
        "SYSTEM_CONTROL_AUTO_STANDBY",
        "CONTROL_REBOOT",
        "CONTROL_POWER_PACK_BLINK",
        "USE_POWER_MODE",
    ],
    "charge": [
        "AUTO_CHARGE",
        "SETTING_CHARGE",
        "SETTING_SUPER_CHARGE",
        "SET_CHARGE_POWER",
        "BATTERY_PRIORITY",
        "DISCHARGE_MEMORY",
        "SETTING_DISCHARGE_MEMORY",
        "ENERGY_STORAGE_CHARGE_LIMIT",
        "SETTING_OUTPUT_PRIORITY",
        "SETTING_OUTPUT_PRIORITY_SOC",
        "SET_CHARGE_DISCHARGE_LINE",
        "CHARGE_PLAN",
        "ADD_CHARGE_DISCHARGE_PLAN",
        "DELETE_CHARGE_DISCHARGE_PLAN",
        "UPDATE_CHARGE_DISCHARGE_PLAN",
        "GET_CHARGE_DISCHARGE_PLAN",
        "CURRENT_CHARGE_DISCHARGE_PLAN",
        "BACKUP",
        "SYSTEM_SET_FEED_GRID_POWER",
        "SYNC_GRID_STANDARD",
        "EV_CHARGE_OPTIONS",
    ],
    "smart_timer": [
        "SMART_MODE",
        "CUSTOM_MODE_TIMER",
        "SMART_PLUG_TIMER",
        "TIME_ELEC_TIMER",
        "TIMER_TASK_ADD",
        "TIMER_TASK_DELETE",
        "TIMER_TASK_READ",
        "TIMER_TASK_UPDATE",
        "SYSTEM_STORM_EVENT_SWITCH",
    ],
    "sub_device": [
        "SUB_CONTROL_SOCKET_SWITCH",
        "SUB_CONTROL_SOCKET_PRI_ENABLE",
        "SUB_SET_CT_SCHEDULE_PHASE",
        "READ_SUB_DEVICE_SOCKET",
        "BIND_SMART_PART",
        "UNBIND_SMART_PART",
    ],
    "provisioning_ota": [
        "WIFI",
        "WRITE_WIFI_INFO",
        "WRITE_WIFI_AND_MQTT_INFO",
        "GET_WIFI_CONFIG",
        "READ_WIFI_LIST",
        "SYNC_MQTT_CONNECT_INFO",
        "CUSTOM_MQTT",
        "GET_DEVICE_OTA_VERSION",
        "DEVICE_GET_OTA_PAGE_DATA",
        "NOTIFY_DEVICE_CAN_OTA",
        "NOTIFY_DEVICE_OTA_TOTAL_PAGE",
        "CMD_SYNC",
        "CMD_RST",
        "CMD_RST_FULL",
    ],
}

DEVICE_MODELS = [
    "E240",
    "E557",
    "E900",
    "E1000",
    "E1500V2",
    "E1800",
    "E2000",
    "E3000",
    "E7647",
    "E7987",
]
ACCESSORIES = [
    "ACC_CT_906",
    "ACC_CT_907",
    "ACC_CT_2604",
    "ACC_METER_892",
    "ACC_METER_905",
    "ACC_METER_910",
    "ACC_SOCKET_904",
    "SHELLY_PLUG_S",
    "SHELLY_PLUG_SG3",
    "SHELLY_PRO_3EM",
    "SHELLY_PRO_3EM63",
    "SHELLY_PRO_EM50",
]


# ---------------------------------------------------------------------------
# Schicht A — Login-Request-Verschlüsselung (RSA-gewrappter AES-Key)
#   aesEncryptData = Base64( AES/ECB/PKCS5( toJson(loginBean), aesKey ) )
#   rsaForAesKey   = Base64( RSA/ECB/PKCS1( aesKey, RSA_PUBLIC_KEY ) )
# Benötigt zusätzlich: from Crypto.PublicKey import RSA; from Crypto.Cipher import
# PKCS1_v1_5
# ---------------------------------------------------------------------------
import json as _json  # noqa: E402
import secrets as _secrets  # noqa: E402

RSA_PUBLIC_KEY_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCVmzgJy/4XolxPnkfu32YtJqYGFLYqf9/rnVgURJED"
    "+8J9J3Pccd6+9L97/+7COZE5OkejsgOkqeLNC9C3r5mhpE4zk/HStss7Q8/5DqkGD1annQ+eoICo3oi"
    "0dITZ0Qll56Dowb8lXi6WHViVDdih/oeUwVJY89uJNtTWrz7t7QIDAQAB"
)


def make_login_request(login_bean: dict) -> dict:
    """login_bean z.B. {'account':..,'password':..,'loginType':0,'regionCode':..,
    'registerAppId':..,'macId':..}. Liefert {'aesEncryptData','rsaForAesKey'}.
    """  # noqa: D205
    from Crypto.Cipher import PKCS1_v1_5  # noqa: PLC0415
    from Crypto.PublicKey import RSA  # noqa: PLC0415

    # 1) zufälligen AES-128-Key -> Base64-String (wie od.d.b())
    aes_key_str = base64.b64encode(_secrets.token_bytes(16)).decode()
    key_bytes = aes_key_str.encode("utf-8")
    # 2) JSON AES/ECB/PKCS5 verschlüsseln
    plain = _json.dumps(login_bean, separators=(",", ":")).encode("utf-8")
    aes_ct = AES.new(key_bytes, AES.MODE_ECB).encrypt(pad(plain, 16))
    aes_encrypt_data = base64.b64encode(aes_ct).decode()
    # 3) AES-Key per RSA/ECB/PKCS1 wrappen
    pub = RSA.import_key(base64.b64decode(RSA_PUBLIC_KEY_B64))
    rsa_ct = PKCS1_v1_5.new(pub).encrypt(key_bytes)
    rsa_for_aes_key = base64.b64encode(rsa_ct).decode()
    return {"aesEncryptData": aes_encrypt_data, "rsaForAesKey": rsa_for_aes_key}


# ---------------------------------------------------------------------------
# Schicht C — MQTT-Payload-Verschlüsselung
#   AES-128-CBC/PKCS7, IV == Key (= Base64.decode(bluetoothKey))
# ---------------------------------------------------------------------------
def payload_encrypt(body_json: str, bluetooth_key_b64: str) -> str:  # noqa: D103
    k = base64.b64decode(bluetooth_key_b64)  # 16 Byte -> AES-128
    ct = AES.new(k, AES.MODE_CBC, k).encrypt(pad(body_json.encode("utf-8"), 16))
    return base64.b64encode(ct).decode()


def payload_decrypt(payload_b64: str, bluetooth_key_b64: str) -> str:  # noqa: D103
    k = base64.b64decode(bluetooth_key_b64)
    pt = unpad(AES.new(k, AES.MODE_CBC, k).decrypt(base64.b64decode(payload_b64)), 16)
    return pt.decode("utf-8")


# ---------------------------------------------------------------------------
# MQTT-Envelope (Teil 2 §1) — Command bauen
# ---------------------------------------------------------------------------
import time as _time  # noqa: E402


def build_command_envelope(  # noqa: D103, PLR0913, PLR0917
    device_sn: str,
    message_type: str,
    body: dict,
    bluetooth_key_b64: str,
    msg_id: int = 1,
    version: int = 1,
    action_id: int = 0,
) -> str:
    enc_body = payload_encrypt(
        _json.dumps(body, separators=(",", ":")),
        bluetooth_key_b64,
    )
    env = {
        "deviceSn": device_sn,
        "id": msg_id,
        "version": version,
        "messageType": message_type,
        "actionId": action_id,
        "timestamp": int(_time.time() * 1000),
        "body": enc_body,
    }
    return _json.dumps(env, separators=(",", ":"))


if __name__ == "__main__":
    # Demo (Werte aus verschiedenen Sessions -> Match erwartet False;
    # mit einem zusammengehoerigen Paar liefert verify_against_capture True)
    creds = make_mqtt_credentials(
        user_id="2041425653828689920",
        mqtt_password_b64="71CEWes8n5Ciem/B1eVsiaIsEI89RTsz6ATS+7GR8c0=",
        device_id="271c55f5731fa3d9ba1fe131e088946e0",
    )
    for _k, _v in creds.items():
        pass
