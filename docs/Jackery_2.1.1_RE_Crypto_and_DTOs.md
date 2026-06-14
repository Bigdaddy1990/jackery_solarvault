# Jackery 2.1.1 RE — Teil 3: Krypto-Schichten & REST-DTOs (komplett aus Bytecode)

> Ergänzung zu Teil 1+2. Hier: **alle drei Verschlüsselungs-Schichten** vollständig aus dem Bytecode rekonstruiert, plus die Request/Response-DTOs der wichtigsten REST-Endpunkte. Alles statisch belegt.

## 1. Überblick: Drei unabhängige Krypto-Schichten

| Schicht | Zweck | Algorithmus | Key |
|---------|-------|-------------|-----|
| **A — Login-Request** | `auth/login`-Body schützen | AES/ECB/PKCS5 (Daten) + RSA/ECB/PKCS1 (Key-Wrap) | zufälliger AES-Key pro Login, RSA-Pubkey hardcodiert |
| **B — MQTT-Connect-Passwort** | Broker-Auth | AES-256-CBC/PKCS5 | `Base64.decode(mqttPassWord)` (rotiert pro Login) |
| **C — MQTT/BLE-Payload** | `body` der Nachrichten | AES-128-CBC/PKCS7 | `Base64.decode(bluetoothKey)` (pro Gerät) |

Schicht B ist in Teil 1 (§3) dokumentiert. Hier A und C im Detail.

---

## 2. Schicht A — Login-Request-Verschlüsselung (verifiziert)

`auth/login` sendet **nicht** Klartext, sondern `{ "aesEncryptData": "...", "rsaForAesKey": "..." }`.

### Ablauf (aus LoginApi.LoginBean + od.d.b())
1. Klartext-Request (LoginBean) als JSON (GsonUtils.toJson):
   `{account, password, loginType(0=PASSWORD/1=CODE), regionCode, registerAppId, macId, phone, verificationCode}`
2. Zufälligen AES-Key erzeugen: `KeyGenerator("AES").init(128).generateKey()` → 16 Byte → Base64-String (= aesKeyStr). Fallback: 16 Zufallsziffern, Base64-kodiert.
3. `aesEncryptData = Base64( AES/ECB/PKCS5Padding( toJson(login).utf8, key=aesKeyStr.utf8 ) )`  (e0.q, transformation="AES", iv=null ⇒ ECB)
4. `rsaForAesKey  = Base64( RSA/ECB/PKCS1Padding( aesKeyStr.utf8, RSA_PUBLIC_KEY ) )`  (e0.a0, 1024-bit)
5. `POST {aesEncryptData, rsaForAesKey}`

### Hardcodierter RSA-Public-Key (1024-bit, X.509/DER, Base64)
```
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCVmzgJy/4XolxPnkfu32YtJqYGFLYqf9/rnVgURJED+8J9J3Pccd6+9L97/+7COZE5OkejsgOkqeLNC9C3r5mhpE4zk/HStss7Q8/5DqkGD1annQ+eoICo3oi0dITZ0Qll56Dowb8lXi6WHViVDdih/oeUwVJY89uJNtTWrz7t7QIDAQAB
```
(Server entschlüsselt rsaForAesKey mit dem Private-Key → AES-Key → entschlüsselt aesEncryptData.)

### Login-Response
`code, msg, token (JWT), data{userId, username, mqttPassWord, account, nickname, …}, encryption, rsaForAesKey`. Bei `encryption=true` ist `data` analog AES-verschlüsselt; im Capture war es Klartext (false).

---

## 3. Schicht C — MQTT/BLE-Payload-Verschlüsselung (verifiziert)

Krypto-Handler `bb.a/b/e/f/g` (erben `bb.c`), erzeugt von Factory `bb.d.a(type, key)`. Der **key ist der bluetoothKey** des Geräts (Parametername im Bytecode bestätigt: `cc.q.i(deviceSn, …, bluetoothKey, region, …)`).

Key-Aufbau: `key = Base64.decode(bluetoothKey)` → `SecretKeySpec(key,"AES")`. Da der MQTT-IV = Key ist (16 Byte nötig), ist bluetoothKey **16 Byte → AES-128**.

### MQTT (bb.e: d=encrypt / b=decrypt)
- Verschlüsseln: `payload = Base64( AES/CBC/PKCS7( body_json.utf8, key=K, iv=K ) )`  (IV == Key!)
- Entschlüsseln: `body_json = AES/CBC/PKCS7_decrypt( Base64decode(payload), key=K, iv=K ).utf8`
→ Der `body` im MQTT-Envelope (Teil 2 §1) ist dieser **Base64-String**, nicht das rohe JSON.

### BLE (c=encrypt / a=decrypt) — 🔶
- encrypt: frame = data + randomHex; frame += CRC; ct = AES/CBC/PKCS7(hexToBytes(frame), K, IV=randomIV); out = hex(randomIV ++ ct)
- decrypt: iv = data[:16]; pt = AES/CBC/PKCS7_decrypt(data[16:], K, iv); muss mit "DFED" beginnen, CRC (letzte 8 hex) prüfen
BLE: zufälliger vorangestellter IV + CRC + DFED-Frame. MQTT: IV=Key, Base64. Gleicher Key.

> Varianten bb.a/b/f/g unterscheiden sich nur im Framing je Gerätefamilie; Kern überall AES-128 + PKCS7 mit bluetoothKey.

---

## 4. Schicht B — Zusammenfassung (Detail Teil 1 §3)
`password = Base64( AES-256-CBC-PKCS5( username.utf8, key=Base64.decode(mqttPassWord), iv=key[:16] ) )`. Hier Key 32 Byte (AES-256), IV = erste 16 Byte. **Anderer Key/Padding/Größe als Schicht C** — nicht verwechseln.

---

## 5. Gerätetyp-Enum cb.b (→ Krypto-Variante & Modell)
`cb.b.getSelf(int)` mappt einen Typ-Code auf:
- HOME_010 / HOME_011 / HOME_013 / HOME_014 (Heim-Energiesysteme)
- BOX_785 / BOX_ATS (Box/ATS)
- PORTABLE_095/097/099/102/103/109/110/112/116/1161/117/118/119/130/131/135/136/137/139/140/149/151/152/153/154/156/157/158/159/163/280 (Explorer-Varianten)
- ACC_CT_906/907/2604, ACC_METER_892/905/910, ACC_SOCKET_904 (Zubehör)
- UNKNOWN

Typ-Code kommt aus Geräte-Listen-Feld devType/modelCode (§6).

---

## 6. REST-DTOs der Kern-Endpunkte

### auth/login (LoginApi)
- Request: aesEncryptData, rsaForAesKey (§2)
- Response.data: userId, username, mqttPassWord, account, nickname, avatar, mobPhone
- LoginBean (Klartext): account, password, phone, verificationCode, loginType(0/1), regionCode, registerAppId, macId

### auth/generatedJwt (JWTApi): Req – · Resp jwt
### auth/register (RegisterAccountApi): email, password, regionCode, registerAppId, verificationCode
### auth/verificationCode (GetVerificationCodeApi): email, method, phone

### device/bind/list (UserDeviceListApi) — zentral
Response je Gerät: devSn, devId, devModel, devName, devNickname, nickName, devType, subType, modelCode, devState, devStateShow, bindKey, **bluetoothKey**, region, mainDeviceSn, isCloud, bs(SOC %), elec, level, scanName, iconPath, userId.
→ Liefert alles: devSn (Topic+Username), bluetoothKey (Payload-Key), devType/modelCode (Krypto-Variante), isCloud, Schnell-SOC bs.

### device/bind (DeviceBindApi): devId, guid, bindKey, timezoneOffset
### device/unbind (DeviceUnBindApi): deviceId
### device/bind/nickname (DeviceNickNameApi): deviceId, nickname
### device/bluetoothKey (DeviceBluetoothApi): Req deviceSn, guid · Resp bluetoothKey, modelCode, region

### device/property (DeviceDetailApi) — Telemetrie-Shadow per REST
- Request: deviceId
- Response polymorph: properties = HomeBody | SystemBody | BoxBody | PortableBody | AccBaseBody (Felder in Teil 2 §5)
- zusätzlich device (DeviceInfo): deviceSn, deviceName, deviceCode, modelId, modelName, onlineStatus, createTime, updateTime, authorizeType, deviceSecret, randomSalt (letztere meist null)

### OTA
- device/ota/list (DeviceMqttOTASelectApi): Req deviceSnList → Resp currentVersion, targetVersion, targetVersionId, updateContent, updateStatus, upgradeType, beginUpgradeTimestamp, expireTimestamp, targetModuleVersion[]
- device/ota/update (DeviceMqttOTAStartApi): Req deviceSn, subDeviceSn, targetFirmwareIds, targetVersionId

---

## 7. Kompletter End-to-End-Flow für HA
1. Login (Schicht A): AES-Key generieren → JSON AES/ECB-verschlüsseln → AES-Key RSA-wrappen → POST auth/login → token + mqttPassWord.
2. Geräteliste GET device/bind/list → pro Gerät devSn, bluetoothKey, devType, modelCode.
3. MQTT-Connect (Schicht B): Credentials aus mqttPassWord (Teil 1 §3) → TLS zu emqx.jackeryapp.com:8883.
4. Subscribe hb/app/<devSn>/device; eingehende body (Schicht C) mit Base64.decode(bluetoothKey) als AES-128-CBC/PKCS7 (IV=Key) entschlüsseln → JSON-Telemetrie (Teil 2 §5).
5. Steuern: Command-JSON (Teil 2 §3/§4) → Schicht C verschlüsseln → Envelope body → publish hb/app/<devSn>/command.
6. REST-Stats zusätzlich aus device/stat/* (Teil 1 §5).

Offen nur per Live-Capture: exakte Feld-Einheiten/Skalierung, messageType-Default der CONTROL_*-Befehle, genaue Variantenwahl (bb.a/b/e/f/g) je Modell.
