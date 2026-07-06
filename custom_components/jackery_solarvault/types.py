"""Typed TypedDicts for ALL HTTP response DTOs from the Jackery API.

Extracted from Jackery_2.1.1 documentation.  Every mapping mirrors the
shape returned by the corresponding cloud endpoint.  Optional fields use
``NotRequired`` so callers can narrow types with confidence.

All types are runtime-resolvable — no ``from __future__ import annotations``
(ruff TID251).
"""

from typing import Any, NotRequired, TypedDict

# =============================================================================
# Generic Wrapper
# =============================================================================


class ApiBaseResponse(TypedDict):
    """Generic API response envelope — every endpoint returns this shape.

    ``code`` is ``0`` on success.  ``data`` carries the endpoint-specific
    payload (dict, list, or ``None``).  ``rsaForAesKey`` is present only
    when the response body is AES-encrypted.
    """

    code: int
    msg: str
    data: dict[str, Any] | list[dict[str, Any]] | None
    encryption: bool
    rsaForAesKey: NotRequired[str]


# =============================================================================
# Auth / Login
# =============================================================================


class LoginResponseData(TypedDict):
    """auth/login response data — also used by user/info."""

    userId: str
    username: str
    appUserName: str
    mqttPassWord: str  # Base64, 32 bytes — rotates per login
    account: str
    nickname: str | None
    mobPhone: str | None
    avatar: str | None
    passwordFlag: bool


class LoginResponseEnvelope(TypedDict):
    """auth/login envelope — token lives here, not in data.

    Extends ``ApiBaseResponse`` with a ``token`` field that carries the
    JWT HS256 session token.
    """

    code: int
    msg: str
    data: LoginResponseData
    encryption: bool
    rsaForAesKey: NotRequired[str]
    token: str  # JWT HS256


class JwtResponseData(TypedDict):
    """auth/generatedJwt response data."""

    jwt: str


UserInfoResponseData = LoginResponseData
# user/info response data has the same shape as LoginResponseData.


class MqttCredentialsResponseData(TypedDict):
    """auth/getMqttCreds response data."""

    mqttUserName: str
    mqttPassWord: str
    mqttHost: str
    mqttPort: int


# =============================================================================
# Device / Bind
# =============================================================================


class DeviceListItem(TypedDict):
    """device/bind/list — single device in the bound-device list."""

    devSn: str
    devId: str
    devModel: str
    devName: str
    devNickname: str
    nickName: str
    devType: int
    subType: int
    modelCode: int
    devState: int
    devStateShow: int
    bindKey: int
    bluetoothKey: str
    region: str
    mainDeviceSn: NotRequired[str]
    isCloud: bool
    bs: int  # SOC %
    elec: int
    level: int
    scanName: str
    iconPath: str
    userId: str


class ShareBean(TypedDict):
    """device/bind/shared — single share entry."""

    shareId: str
    account: str
    username: str
    bindUserId: str
    avatar: str
    level: int
    count: int


class SharedDeviceItem(TypedDict):
    """device/bind/share/list — shared device info."""

    devId: str
    devSn: str
    devModel: str
    devName: str
    devNickname: str
    icon: str


class DeviceInfo(TypedDict):
    """Device metadata from device/property or system/list."""

    id: int
    deviceSn: str
    deviceName: str
    deviceCode: str
    modelId: str
    modelName: str
    onlineStatus: int
    createTime: int
    updateTime: int
    authorizeType: int
    deviceSecret: NotRequired[Any]
    randomSalt: NotRequired[Any]
    isDelete: int
    ownerId: NotRequired[Any]
    manufacturerCode: NotRequired[Any]
    remark: NotRequired[Any]


class QrCodeResponseData(TypedDict):
    """device/bind/qrcode response data."""

    qrCodeId: str
    userId: str


# =============================================================================
# System / DIY
# =============================================================================


class SystemDevice(TypedDict):
    """Single device within a system from system/list."""

    deviceId: str
    deviceSn: str
    deviceName: str
    devModel: str
    devType: int
    modelCode: int
    subType: int
    productModel: str
    typeName: str
    iconPath: str
    scanName: str
    onlineState: int
    isCloud: bool
    currency: str
    rb: int


class SystemListItem(TypedDict):
    """system/list — single energy system."""

    id: str
    systemSn: str
    systemName: str
    systemState: int
    onlineState: int
    deviceId: str
    deviceSn: str
    bindKey: int
    bluetoothKey: str
    countryCode: str
    currency: str
    gridStandard: str
    region: str
    timezone: str
    devices: list[SystemDevice]


class SystemBindExistResponseData(TypedDict):
    """system/exist response data."""

    bindKey: int
    deviceSn: str
    guid: str


class PropertyBean(TypedDict):
    """Sub-shadow or system-shadow response."""

    name: str
    value: str


# =============================================================================
# Battery Pack
# =============================================================================


class BatteryPackItem(TypedDict):
    """battery/pack/list — single battery pack (fields from coordinator parsing)."""

    batteryPackSn: str
    batteryPackId: str
    batteryPackModel: str
    batteryPackStatus: int
    batteryPackSoc: int
    batteryPackVoltage: int
    batteryPackCurrent: int
    batteryPackTemperature: int
    batteryPackHealth: int
    batteryPackRemainCapacity: int
    batteryPackFullCapacity: int
    cycleCount: int
    manufactureDate: str
    firmwareVersion: str
    hardwareVersion: str


# =============================================================================
# OTA
# =============================================================================


class OtaListItem(TypedDict):
    """ota/list — single device OTA status."""

    deviceSn: str
    currentVersion: str
    targetVersion: str
    targetVersionId: str
    updateContent: str
    updateStatus: int
    upgradeType: int
    beginUpgradeTimestamp: int
    expireTimestamp: int
    currentTimestamp: int
    targetModuleVersion: list[str]


class OtaStartResponseData(TypedDict):
    """ota/update response data."""

    currentVersion: str
    targetVersion: str
    targetVersionId: str
    updateContent: str
    updateStatus: int
    upgradeType: int
    targetModuleVersion: list[str]


class BleOtaVersionData(TypedDict):
    """ota/version/list — per-module firmware version info."""

    deviceSn: str
    versionId: int
    MAIN: str
    BMS: str
    BMSX: str
    BMSX_BOOT: str
    EBMS: str
    EBMS_BOOT: str
    ESP32: str
    HMI: str
    INV1: str
    INV2: str
    LEDBOARD: str
    PCSAC: str
    PCSAC_BOOT: str
    PCSDC: str
    PCSDC_BOOT: str
    PV: str
    DIY: str


# =============================================================================
# Statistics — Device Level
# =============================================================================


class TodayEnergyData(TypedDict):
    """stat/today — today's energy breakdown."""

    dg: float  # today generation
    dh: float  # today home/load
    ds: float  # today solar/storage
    de: float  # today grid/discharge


class BoxEleStatData(TypedDict):
    """stat — generic box electricity stat (unit + time series)."""

    total: str
    unit: str
    x: list[str]
    y: list[float]


class PvStatData(TypedDict):
    """stat/pv — PV statistics."""

    totalSolarEnergy: str
    totalSolarRevenue: float
    currency: str
    unit: str
    x: list[str]
    y: list[float]
    y1: list[float]
    y2: list[float]
    y3: list[float]
    y4: list[float]


class BatteryStatData(TypedDict):
    """stat/battery — battery charge/discharge statistics."""

    totalCharge: str
    totalDischarge: str
    unit: str
    x: list[str]
    y: list[float]  # charge
    y1: list[float]  # discharge
    y2: list[float]
    y3: list[float]


class HomeStatData(TypedDict):
    """stat/onGrid — home/grid energy statistics."""

    totalInGridEnergy: str
    totalOutGridEnergy: str
    unit: str
    x: list[str]
    y: list[float]
    y1: list[float]
    y2: list[float]


class EpsStatData(TypedDict):
    """stat/eps — EPS backup energy statistics."""

    totalInEpsEnergy: str
    totalOutEpsEnergy: str
    unit: str
    x: list[str]
    y: list[float]
    y1: list[float]
    y2: list[float]


class CtStatData(TypedDict):
    """stat/ct — CT clamp energy statistics."""

    totalInCtEnergy: str
    totalOutCtEnergy: str
    unit: str
    x: list[str]
    y1: list[float]
    y2: list[float]


class SymmetryStatData(TypedDict):
    """stat/symmetry — charge/discharge symmetry analysis."""

    p: list[float]
    n: list[float]
    totalP: str
    totalN: str
    unit: str
    x: list[str]


class BoxCutoffStatData(TypedDict):
    """stat/cutoff — power outage statistics."""

    cnt: int
    totalInterval: int


class StatProfitData(TypedDict):
    """stat/profit — total energy profit."""

    total: int
    currency: str


class CarbonStatData(TypedDict):
    """stat/carbon — CO2 offset equivalent."""

    co2: float
    coal: float
    tree: float


class DeviceStatisticData(TypedDict):
    """stat/deviceStatistic — aggregate device statistics."""

    pvEgy: float
    batChgEgy: float
    batDisChgEgy: float
    inOngridEgy: float
    outOngridEgy: float
    inEpsEgy: float
    outEpsEgy: float


class SocketStatisticData(TypedDict):
    """stat/smartSocketStatistic — smart socket energy statistics."""

    todayEgy: float
    totalEgy: float


class AccMeterStatData(TypedDict):
    """stat/meter — smart meter charge/discharge energy."""

    chargingEnergy: str
    dischargingEnergy: str


class AccSocketStatData(TypedDict):
    """stat/socket — smart socket energy consumption."""

    useEnergy: str
    unit: str
    x: list[str]
    y: list[float]


class ChargeReportItem(TypedDict):
    """chargeReport — single charge event."""

    id: int
    deviceSn: str
    startTs: int
    endTs: int
    startSoc: int
    endSoc: int
    maxPower: int
    totalEnergy: int
    chargeInterval: float


# =============================================================================
# Statistics — System Level (Energy Flow)
# =============================================================================


class PvSource(TypedDict):
    """PV energy flow source distribution."""

    ac: int
    pv: int


class PvUsage(TypedDict):
    """PV energy flow usage distribution."""

    ac: int
    battery: int
    home: int


class BatterySources(TypedDict):
    """Battery energy flow source distribution."""

    ac: int
    home: int
    pv: int


class BatteryUsage(TypedDict):
    """Battery energy flow usage distribution."""

    ac: int
    home: int


class HomeSource(TypedDict):
    """Home energy flow source distribution."""

    ac: int
    battery: int
    pv: int


class SystemStatisticData(TypedDict):
    """stat/systemStatistic — aggregate system statistics."""

    todayGeneration: float
    todayLoad: float
    todayBatteryChg: float
    todayBatteryDisChg: float
    totalGeneration: float
    totalRevenue: str
    totalCarbon: float
    isSetPrice: int


class SysPvStatData(TypedDict):
    """stat/sys/pv/trends — system PV trend with energy flow."""

    totalSolarEnergy: str
    totalSolarRevenue: float
    currency: str
    count: int
    showDash: bool
    weather: list[int]
    pvSources: PvSource
    pvUsage: PvUsage
    unit: str
    x: list[str]
    y: list[float]


class SysBatteryStatData(TypedDict):
    """stat/sys/battery/trends — system battery trend with energy flow."""

    totalChgEgy: str
    totalDisChgEgy: str
    batterySources: BatterySources
    batteryUsage: BatteryUsage
    unit: str
    x: list[str]
    y: list[float]
    y1: list[float]
    y2: list[float]


class SysHomeStatData(TypedDict):
    """stat/sys/home/trends — system home energy trend."""

    totalHomeEgy: str
    homeSources: HomeSource
    unit: str
    x: list[str]
    y: list[float]


# =============================================================================
# Stat SOC
# =============================================================================


class StatSocData(TypedDict):
    """stat/soc — SOC curve time series."""

    x: list[str]
    y1: list[float]


# =============================================================================
# AI Smart Schedule
# =============================================================================


class SmartScheduleData(TypedDict):
    """stat/getSmartSchedulePrediction — AI smart schedule prediction."""

    xList: list[str]
    priceList: list[float]
    pvPowerList: list[float]
    homeList: list[float]
    profit: str
    currency: str
    days: str


class SmartModeInfoData(TypedDict):
    """smartMode/getSmartMode — current smart mode status."""

    isActive: int
    systemId: str
    timeDifference: int


class SmartConditionData(TypedDict):
    """smartMode/checkIfSet — smart mode prerequisites check."""

    isSetLocation: bool
    isSetPowerPrice: bool
    systemId: str


# =============================================================================
# Dynamic Price / Electricity
# =============================================================================


class PriceMapData(TypedDict):
    """Price time-series from dynamicPrice response."""

    x: list[str]
    y1: list[float]
    y2: list[float]


class DynamicPriceData(TypedDict):
    """dynamic/dynamicPrice — current electricity price data."""

    isContractAuth: bool
    todayHigh: float | None
    todayLow: float | None
    nextdayHigh: float | None
    nextdayLow: float | None
    platformCompanyId: int
    priceCompanyLogo: str
    priceCompanyName: str
    priceMap: PriceMapData


class PriceSourceItem(TypedDict):
    """dynamic/priceCompany — single electricity price provider."""

    cid: str
    companyName: str
    country: str
    loginAllowed: int
    platformCompanyId: int


class PriceSettingsData(TypedDict):
    """dynamic/powerPriceConfig — current price settings."""

    companyName: str
    currency: str
    currencyCode: str
    dynamicOrSingle: int
    loginAllowed: int
    platformCompanyId: int
    singleCurrency: str
    singleCurrencyCode: str
    singlePrice: str
    systemId: str
    systemRegion: str


class FlatpeakBean(TypedDict):
    """dynamic/historyConfig — flatpeak auth status."""

    cid: str
    companyName: str
    country: str
    flatpeakIsAuth: bool
    loginAllowed: str
    platform: str
    platformCompanyId: int
    priceCompanyLogo: str
    priceCompanyName: str


class RabotBean(TypedDict):
    """dynamic/historyConfig — Rabot auth status."""

    cid: str
    companyName: str
    country: str
    loginAllowed: int
    platform: str
    platformCompanyId: int
    rabotIsAuth: bool


class PriceHistoryConfigData(TypedDict):
    """dynamic/historyConfig — price history provider config."""

    flatpeakBean: FlatpeakBean
    rabotBean: RabotBean


class ContractItem(TypedDict):
    """dynamic/contractList — single electricity contract."""

    contractNumber: str
    contractState: str
    name: str
    tariffName: str


class DynamicPriceLoginUrlData(TypedDict):
    """dynamic/loginUrl — OAuth login URL for price provider.

    Fields are not fully documented in the Jackery spec; the payload is
    returned as a raw dict to avoid losing data.
    """

    platformCompanyId: int | None
    systemId: str | None


# =============================================================================
# Currency
# =============================================================================


class CurrencyItem(TypedDict):
    """currencies/currencyList — single currency option."""

    id: str
    name: str
    currencyCode: str
    currencySymbol: str


class DeviceCurrencyData(TypedDict):
    """currencies/deviceCurrency — device currency settings."""

    currency: str
    currencyCode: str
    currencySymbol: str


# =============================================================================
# Location
# =============================================================================


class LocationData(TypedDict):
    """device/location — storm/alert location."""

    deviceId: str
    latitude: float
    longitude: float


# =============================================================================
# Shelly
# =============================================================================


class ShellyAuthUrlData(TypedDict):
    """shelly/auth-url — OAuth URL for Shelly binding."""

    authUrl: str
    state: str


class ShellyDeviceItem(TypedDict):
    """shelly/devices — single Shelly device."""

    id: int
    deviceSn: str
    deviceId: str
    deviceCode: str
    deviceType: str
    name: str
    scanName: str
    host: str
    icon: str
    iconPath: str
    onlineStatus: int
    devType: int
    subType: int
    controlAllowed: bool
    integratorEnabled: bool


class ShellyDevicesData(TypedDict):
    """shelly/devices — list of bound and supported Shelly devices."""

    lastSyncTime: int
    supportedDevices: list[ShellyDeviceItem]
    boundDevices: list[ShellyDeviceItem]


class ShellyControlData(TypedDict):
    """shelly/device/control — Shelly device control response."""

    accepted: bool
    deviceId: str
    requestId: str


class ShellyRealtimePowerData(TypedDict):
    """shelly/device/realtime-power — real-time power data."""

    bindId: int
    deviceCode: str
    deviceId: str
    deviceType: str
    online: int
    controlAllowed: bool
    message: str
    powerBody: dict[str, Any]  # dynamic per-channel data


class ShellyBindingFailuresData(TypedDict):
    """shelly/binding/failures — binding status."""

    state: str
    bindCount: int
    successDeviceSns: list[str]
    failedDeviceSns: list[str]


# =============================================================================
# Accessories
# =============================================================================


class AccessoriesExistData(TypedDict):
    """accessories/exist — check if accessories exist."""

    deviceSn: str
    scanName: str
    bindCode: int
    devType: int
    subType: int
    param: int
    typeName: str


class JackeryAccessoriesExistData(TypedDict):
    """accessories/exists — Jackery accessory identification."""

    deviceSn: str
    scanName: str
    bindCode: int
    devType: int | None
    subType: int | None
    param: int | None
    typeName: str
    iconPath: str
    modelCode: str
    scanType: str


class AccessoriesListItem(TypedDict):
    """accessories/list — bound accessory device."""

    id: str
    parentDeviceId: str
    deviceSn: str
    deviceName: str
    productModel: str


class SyncAccessoriesResult(TypedDict):
    """accessories/synchronizeSmartAccessoriesData — sync result."""

    result: bool
    deviceSns: list[str]


# =============================================================================
# Push / Notifications
# =============================================================================


class MsgListItem(TypedDict):
    """push/notifyList — single notification message."""

    id: str
    title: str
    content: str
    nickName: str
    messageType: str
    createTime: int


class UnreadCountData(TypedDict):
    """push/unreadCount — unread message count."""

    count: int


class PushConfigData(TypedDict):
    """push/configGet — push notification config status."""

    push: int


# =============================================================================
# Alarms
# =============================================================================


class AlarmListItem(TypedDict):
    """api/alarm — single alarm entry."""

    alarmKey: str
    mainDeviceSn: str
    deviceNickName: str
    reminder: str
    suggestion: str


class AlarmDetailData(TypedDict):
    """api/alarm/detail — alarm detail."""

    alarmKey: str
    suggestion: str


# =============================================================================
# FAQ / Help / Privacy
# =============================================================================


class FaqListItem(TypedDict):
    """api/faqList — FAQ category."""

    faqId: str
    name: str


class FaqAnswerData(TypedDict):
    """api/faq/answer — FAQ answer content."""

    title: str


class PrivacyUpdateData(TypedDict):
    """api/isUpgradeRequired — privacy consent status."""

    pendingAgreeVersionIds: list[str]
    versionTag: str


# =============================================================================
# App Version / Banner
# =============================================================================


class AppVersionData(TypedDict):
    """app/version/getNewVersion — app update info."""

    appType: str
    appVersion: str
    versionCode: str
    versionName: str
    downloadUrl: str
    updateContent: str
    updateType: int
    remark: str


class BannerItem(TypedDict):
    """app/banner/list — single banner."""

    imageUrl: str
    jumpLink: str
    type: int


# =============================================================================
# Power Report
# =============================================================================


class PowerReportRequestData(TypedDict):
    """device/property/power3 — power report push payload."""

    deviceSn: str
    properties: dict[str, str]


# =============================================================================
# MQTT Session Snapshot
# =============================================================================


class MqttSessionSnapshot(TypedDict):
    """Serialisable MQTT session fields returned by BaseHTTPMixin.mqtt_session_snapshot.

    Keys mirror the MQTT_SESSION_* constants in const.py.
    """

    user_id: str
    seed_b64: str
    mac_id: str
    mac_id_source: str
