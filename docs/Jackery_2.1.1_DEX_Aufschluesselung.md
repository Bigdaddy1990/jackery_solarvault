# Jackery 2.1.1 — Komplette DEX-Aufschlüsselung

> Strukturkarte aller 6 DEX (~45.900 Klassen). Zweck: Navigation für die HA-Integration — wo liegt was. App-eigener Code = `com.hbxn.*` (~3.140 Klassen) + obfuskierte Logik-Pakete; der Rest sind Frameworks/Bibliotheken.

## 1. DEX-Übersicht

| DEX | Größe | Klassen | Rolle / Hauptinhalt |
|-----|------:|--------:|---------------------|
| classes.dex | 10 MB | 9.579 | **Jetpack Compose UI** (androidx.compose 7.5k) + AppCompat/Activity/Support |
| classes2.dex | 275 KB | 148 | Java-8-Time-Desugaring (`j$.time`) — Mini-DEX |
| classes3.dex | 8,3 MB | 9.283 | **Google/Firebase/GMS** (2,3k) + **BlankJ-Utils (Krypto e0/d0/c0)** + EngageLab-Push + AndroidX (core/lifecycle/recyclerview/fragment) |
| classes4.dex | 8,7 MB | 8.873 | **Device-Control-SDK `com.hbxn.control.*`** + **Krypto/Protokoll (bb/cc/cb/jc)** + Google-Libs (protobuf/ExoPlayer) |
| classes5.dex | 7,6 MB | 8.904 | **Die Jackery-App `com.hbxn.jackery.*`** (UI/HTTP/API, 2,7k) + RxJava + **Netty (HiveMQ-Transport)** + Umeng |
| classes6.dex | 7,6 MB | 9.146 | Kotlin-Reflect/Coroutines + **Zendesk** (Support-Chat) + **Nordic BLE (no.nordicsemi)** + OkHttp |

---

## 2. Die App: `com.hbxn.jackery.*` (1.711 Top-Klassen, v.a. classes5)

### 2.1 Einstieg & Basis (classes3)
- `app.AppApplication` (Application), `app.AppActivity`, `app.AppFragment`, `app.BaseViewModelActivity`, `app.AppBottomDialogActivity`, `app.TitleBarFragment`
- `aop.*` — AspectJ-Aspekte: `CheckLogin`, `CheckNet`, `Permissions`, `SingleClick`, `Log` (+ je `…Cut`)

### 2.2 Geräte-Controller (classes4) — **Steuer-Orchestrierung**
- `controller.BaseDeviceController` — baut Krypto-Handler (`bb.d.a(type, bluetoothKey)`), hält BLE-Adapter, verbindet MQTT/BLE
- `controller.home.HomeDeviceController`
- `dto.home.BindResult`

### 2.3 HTTP/REST (classes5)
- `http.api.*` (64) + `http.api.home.*` (57) + `http.api.shelly.*` (7) = **128 Endpunkt-Klassen** (vollständig in Teil 1/3)
- `http.model.RequestHandler` (Response-Parsing), `RequestServer` (Basis-URL/Header), `HttpData`/`HttpListData` (Wrapper)
- `http.repository.MainRepository`
- `http.entity.*` — Mall/Country/Accessories-Entities
- `http.glide.GlideConfig` (Bild-Loader-Config)

### 2.4 MQTT/Push/Service
- MQTT-Manager: **`jc.e`** (obfuskiert, §4) — nicht unter jackery.*, aber Kern
- `push.JgMsgReceiver`, `push.JgUserService` (JPush/极光), `push.ZendeskMessagingService`
- `service.FirmwareUpgradeService` (OTA)
- `router.bean.User`, `router.bean.NetworkInfo`

### 2.5 UI nach Gerätefamilie (classes5) — **wo die Panels/Logik je Gerät liegen**
| Paket | Klassen | Inhalt |
|-------|--------:|--------|
| `ui.activity.home` | 477 | Heim-Energiesysteme (HomePower/DIY), Strompreis, Smart-Mode |
| `ui.activity.portable` | 234 | Explorer-Powerstations (Panels, Settings, Pläne) |
| `ui.activity.accessory` | 126 | Zubehör (CT, Meter, Socket, **Shelly cloud2cloud**) |
| `ui.activity.box` | 81 | Box/HomePower-Box |
| `ui.activity.device` | 77 | Geräte-übergreifend (Bind, Detail, OTA, Share) |
| `ui.activity.mine` | 31 | Konto/Profil |
| `ui.activity.login` | 26 | Login/Register |
| `ui.activity.ats` | 22 | ATS (Automatic Transfer Switch) |
| `ui.common.*` | ~176 | gemeinsame Activities/Fragments/VMs |
| `ui.compose.components` | 68 | Compose-UI-Bausteine |
| `ui.dialog.*` | ~80 | Bottom-Sheets/Dialoge (WiFi, Timer, Socket, Currency, …) |
| `ui.vm.*` | — | `BaseViewModel`, `BasePanelVM` |

### 2.6 Custom-Views & Web (classes5)
- `widget.*` — z.B. `BatteryRingProgressView`, `DiyDeviceEnergyFlowAnimView`, `SystemFlowAnimView`, `ElectricityPriceChart`, `DeviceElectricityPanelView`, `DeviceControlSwitch`, `BleScanView`, `DeviceOutputTimerView`, `RealTimeLineChart`
- `webview.BrowserView`, `NestedScrollWebView` (für Shelly-OAuth-WebView, FAQ, Mall)

---

## 3. Device-Control-SDK: `com.hbxn.control.*` (96 Top-Klassen, classes4)

Das ist die **Geräte-Abstraktion** — der wichtigste Teil für die Integration.

| Paket | Klassen | Inhalt |
|-------|--------:|--------|
| `device.bean.home` | 32 | `HomeBody`, `SystemBody`, `PV`, Sub-Geräte (`CtSub`, `PlugSub`, `BatteryPackSub`, `CollectorSub`), `ThirdPartyMqttBody`, `WifiBean`, Tasks |
| `device.bean.box` | 8 | `BoxBody`, `Ac`, `Circuit`, `Fault`, `Plan`, `BatteryPack` |
| `device.bean.portable` | 8 | `PortableBody` (96 Felder), `PeaksTroughs*` |
| `device.bean.accessory` | 9 | `AccCTBody`, `AccSocketBody`, `AccBaseBody`, Socket-Tasks |
| `device.bean.battery` | 2 | `BatteryPackBody` |
| `device.bean.alarm` | 3 | `HomeAlarmBody`, `SubAlarm` |
| `device.bean.storm` | 3 | `StormEventBody` (Sturm-/Wetter-Events) |
| `device.cmd.home` | 5 | `HomeControlFormat`, **`HomeCmdAction`** (47 Befehle) |
| `device.cmd.portable` | 3 | `PortableControlFormat`, **`cmd.portable.b`** (51 Befehle) |
| `device.cmd.box` | 2 | `BoxControlFormat` |
| `device.protocol` | 2 | `MqttBean`, `MqttBody` (Envelope) |
| `nordic.scan` | 13 | `BleScanAdapter` + obfuskierte Scan-Logik (Nordic BLE) |

(Feld-/Befehlsdetails: Teil 2.)

---

## 4. Obfuskierte App-Logik-Pakete (Top-Level, KRITISCH) ⭐

Diese kurzen Paketnamen sind **Jackerys eigene** (nicht Dritt-)Logik — Krypto, Protokoll, State:

| Paket | Schlüsselklasse | Rolle |
|-------|-----------------|-------|
| **`jc`** | `jc.e` | **MQTT-Connection-Manager** — `f()` baut Credentials (Schicht B), `y()` connectet, `publishes`/subscribe |
| **`bb`** | `bb.a/b/c/e/f/g` + `bb.d` | **Payload-Krypto** (Schicht C). `bb.d.a(type,key)` = Factory; `bb.e.d/b` = MQTT-AES; `bb.c` = Basis (IV-Logik) |
| **`cb`** | `cb.b` | **Gerätetyp-Enum** (HOME_/BOX_/PORTABLE_/ACC_) → wählt Krypto-Variante |
| **`cc`** | `cc.q` | **Geräte-Config** (deviceSn, type, **bluetoothKey**, region) + Verbindungsobjekt |
| **`od`** | `od.d` | **Globaler App-State** (User-Cache) + **Login-AES-Keygen** (`od.d.b()`) |
| **`sb`** | `sb.b`, `sb.d` | **CRC + Hex-Helfer** (für BLE-Frames) |
| **`pc`/`rc`** | `pc.b`→`rc.a` | **User-Provider** (`pc.b.d()` liefert `User`) |
| **`lc`** | `lc.b` | **Login-Flow** (`lc.b.p()` baut verschlüsselten Login-Request) |
| **`bq`** | `bq.b` | **Logging** (Timber-artig; Tags wie `HB_MQTT_TAG`) |

> Krypto-Helfer `e0`/`d0`/`c0` = **`com.blankj.utilcode.util.*`** (EncryptUtils/EncodeUtils/DeviceUtils), nicht die gleichnamigen Top-Level-Klassen.

---

## 5. Drittanbieter-Bibliotheken (Inventar)

### UI & Bilder
- **androidx.compose** (7.655) — Jetpack Compose (Haupt-UI, classes.dex)
- **com.airbnb** (Lottie, 109), **coil** (~215) + **com.bumptech** (Glide, 83) — Bild/Animation
- **com.scwang** (SmartRefreshLayout, 22), **com.yalantis** (uCrop, 87), **com.shockwave** (PdfViewer, 10)
- **com.hjq** (70) — 轮子哥-Libs: EasyHttp, XXPermissions, TitleBar, Toast

### Netzwerk & Daten
- **okhttp3** (265) + com.hjq EasyHttp — REST-Stack
- **io.netty** (1.437) — Transport für **HiveMQ MQTT-Client**
- **io.reactivex** (RxJava3, 1.456) — Reactive Streams
- **com.google.gson** / protobuf — JSON/Serialisierung (in com.google)
- **kotlinx.serialization** (94), **kotlinx.coroutines** (768)

### Geräte-Konnektivität
- **no.nordicsemi** (359) — **Nordic BLE** (Bluetooth-Provisionierung/Steuerung)
- HiveMQ (über io.netty) — MQTT

### Google / Firebase
- **com.google** (9.718 gesamt) — GMS, Firebase (Analytics/Crashlytics/Messaging), Play-Services, Maps/Places, ExoPlayer, ML, Material

### Analytics / Push / Support
- **com.umeng** (589) — Umeng-Analytics (chinesisch)
- **com.engagelab** (264) — JPush/极光 Push (intl. Marke)
- **zendesk.*** (~1.700) — Zendesk Support-Chat (messaging/conversationkit/ui/guidekit)

### Utilities
- **com.blankj** (289) — AndroidUtilCode (**Krypto/Device/Encode** — e0/d0/c0/z)
- **com.github** (162), **org.jctools** (183, Lock-free Queues), **j$** (Java-8-Desugaring)
- **zxing** — QR-Code (Geräte-Bind per QR)

---

## 6. „Wo schaue ich hin" — Cross-Reference für die HA-Integration

| Aufgabe | Klasse(n) | DEX |
|---------|-----------|-----|
| Login verschlüsseln | `LoginApi$LoginBean.a/c`, `od.d.b` | classes5 |
| MQTT-Credentials | `jc.e.f` + `com.blankj…e0/d0/c0` | classes4/3 |
| Payload ver-/entschlüsseln | `bb.e` (+ Factory `bb.d`, Typ `cb.b`) | classes4 |
| bluetoothKey beziehen | `UserDeviceListApi`, `DeviceBluetoothApi` | classes5 |
| Telemetrie-Felder | `device.bean.{home,box,portable,accessory}` | classes4 |
| Befehle | `HomeCmdAction`, `cmd.portable.b`, `*ControlFormat` | classes4 |
| MQTT-Envelope | `device.protocol.MqttBean/Body`, `*ControlFormat` | classes4 |
| REST-Pfade | `http.api.*` (`getApi()`) | classes5 |
| Geräte-Verbindung aufbauen | `controller.BaseDeviceController.T` | classes4 |
| BLE-Pfad | `com.hbxn.control.nordic.scan.*`, `no.nordicsemi.*` | classes4/6 |
| Shelly cloud2cloud | `ui.activity.accessory.cloud2cloud.*`, `http.api.shelly.*` | classes5 |

---

## 7. Beobachtungen
- **Kein nativer Krypto-Code nötig:** Alle relevanten Algorithmen sind in Java/Kotlin-Bytecode (AES/RSA via JCE + BlankJ). Die `.so`-Libs (config.arm64_v8a.apk) sind v.a. Compose/Netty/Nordic/Firebase-Native — für die Integration irrelevant.
- **Compose-UI:** Die App ist großteils Jetpack Compose (classes.dex) + Restbestände klassischer Views (`widget.*`). Für RE der Logik irrelevant, aber erklärt die hohe Klassenzahl.
- **Zwei Push-Systeme** (EngageLab/JPush + Firebase) und **Umeng** — alles Tracking/Notifications, für HA ignorierbar.
- **Klares Schichtenmodell:** App-UI (jackery.ui) → Controller (jackery.controller) → Device-SDK (control.device) → Krypto/Transport (bb/jc + HiveMQ/Nordic). Die Integration dockt am Device-SDK-/Krypto-Layer an.
