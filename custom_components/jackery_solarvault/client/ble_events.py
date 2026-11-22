"""BLE protocol-event adapter; all value selection uses root ingest.py."""

import asyncio
from collections.abc import Mapping
import contextlib
import copy
from dataclasses import dataclass, field as dataclass_field
from functools import partial
import hashlib
import importlib
import inspect
import json
from json import JSONDecodeError
import logging
import random
import sys
import time
from typing import TYPE_CHECKING, Any, Final, Literal, TypedDict, Unpack, cast

from homeassistant.exceptions import HomeAssistantError

from ..client import JackeryError
from ..const import (
    ACTION_ID_BOX_READ_DEVICE_INFO,
    ACTION_ID_GET_DEVICE_OTA_VERSION,
    ACTION_ID_GET_TIME_ZONE,
    ACTION_ID_PORTABLE_ADD_CHARGE_PLAN,
    ACTION_ID_PORTABLE_CURRENT_CHARGE_PLAN,
    ACTION_ID_PORTABLE_CUSTOM_USE_BATTERY,
    ACTION_ID_PORTABLE_DELETE_CHARGE_PLAN,
    ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
    ACTION_ID_PORTABLE_GET_PEAKS_TROUGHS,
    ACTION_ID_PORTABLE_GET_WIFI_CONFIG,
    ACTION_ID_PORTABLE_READ_DEVICE_INFO,
    ACTION_ID_PORTABLE_SEND_TIME_ZONE,
    ACTION_ID_PORTABLE_SET_PEAKS_TROUGHS,
    ACTION_ID_PORTABLE_UPDATE_CHARGE_PLAN,
    ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
    ACTION_ID_QUERY_WIFI_CONFIG,
    ACTION_ID_READ_WIFI_LIST,
    ACTION_ID_SEND_TIME_ZONE,
    ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
    ACTION_ID_SUBDEVICE_3031,
    ACTION_ID_SYNC_GRID_STANDARD,
    ACTION_ID_SYNC_MQTT_CONNECT_INFO,
    BLE_CMD_BOX_READ_DEVICE_INFO,
    BLE_CONNECT_BACKOFF_INITIAL_SEC,
    BLE_CONNECT_BACKOFF_MAX_SEC,
    CONF_ENABLE_BLE_TRANSPORT,
    DEFAULT_ENABLE_BLE_TRANSPORT,
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    DISCOVERY_SOURCE_SYSTEM_LIST,
    DOMAIN,
    FIELD_ACTION_ID,
    FIELD_BATTERY_PACKS,
    FIELD_BAT_SOC,
    FIELD_BLE_BOX_HOUSE_POWER,
    FIELD_BLE_BOX_MAINS_POWER,
    FIELD_BLE_PORTABLE_INPUT_POWER,
    FIELD_BLE_PORTABLE_OUTPUT_POWER,
    FIELD_BLUETOOTH_KEY,
    FIELD_BODY,
    FIELD_CIR,
    FIELD_CMD,
    FIELD_DEVICE_SN,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_GRID_STANDARD,
    FIELD_IP,
    FIELD_IS_PACK_CONNECT,
    FIELD_MESSAGE_TYPE,
    FIELD_MODEL_CODE,
    FIELD_OP,
    FIELD_RB,
    FIELD_SAFETY,
    FIELD_SN,
    FIELD_SRFC,
    FIELD_SRL,
    FIELD_TIMEZONE,
    FIELD_UPDATES,
    MQTT_ACTION_IDS_ALARM,
    MQTT_ACTION_IDS_COMBINE,
    MQTT_CMD_CONTROL_COMBINE,
    MQTT_CMD_CONTROL_SUB_DEVICE,
    MQTT_CMD_DEVICE_PROPERTY_CHANGE,
    MQTT_CMD_DOWNLOAD_DEVICE_SCHEDULE,
    MQTT_CMD_GET_DEVICE_OTA_VERSION,
    MQTT_CMD_GET_TIME_ZONE,
    MQTT_CMD_QUERY_COMBINE_DATA,
    MQTT_CMD_QUERY_DEVICE_PROPERTY,
    MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_QUERY_WEATHER_PLAN,
    MQTT_CMD_QUERY_WIFI_CONFIG,
    MQTT_CMD_READ_WIFI_LIST,
    MQTT_CMD_SEND_TIME_ZONE,
    MQTT_CMD_SYNC_GRID_STANDARD,
    MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
    MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_UPLOAD_DEVICE_ALERT,
    MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
    MQTT_MESSAGE_CONTROL_COMBINE,
    MQTT_MESSAGE_DELETE_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE,
    MQTT_MESSAGE_INSERT_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_COMBINE_DATA,
    MQTT_MESSAGE_QUERY_CURRENT_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_QUERY_TOU_SCHEDULE,
    MQTT_MESSAGE_QUERY_WEATHER_PLAN,
    MQTT_MESSAGE_QUERY_WIFI_CONFIG,
    MQTT_MESSAGE_SEND_WEATHER_ALERT,
    MQTT_MESSAGE_SET_BATTERY_BOUNDARY,
    MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_TOU_SCHEDULE,
    MQTT_MESSAGE_UPDATE_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_UPLOAD_COMBINE_DATA,
    MQTT_MESSAGE_UPLOAD_DEVICE_ALERT,
    MQTT_MESSAGE_UPLOAD_INCREMENTAL_COMBINE_DATA,
    MQTT_MESSAGE_UPLOAD_WEATHER_PLAN,
    PAYLOAD_BATTERY_BOUNDARY,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_BLE_OTA_EVENTS,
    PAYLOAD_BLE_OTA_VERSIONS,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_ALERT,
    PAYLOAD_DEVICE_META,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_ELECTRICITY_STRATEGY,
    PAYLOAD_MQTT_CONNECT_INFO,
    PAYLOAD_OTA,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
    PAYLOAD_TIMEZONE_CONFIG,
    PAYLOAD_TOU_SCHEDULE,
    PAYLOAD_WEATHER_PLAN,
    PAYLOAD_WIFI_CONFIG,
    PAYLOAD_WIFI_LIST,
    PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID,
    SUBDEVICE_DEV_TYPE_CT,
)
from ..ingest import (
    TransportSource,
    merge_dict_values,
    merge_live_properties,
    merge_present_dict_values,
)
from ..models import BleProcessDisposition, BoxSafetyIngress
from ..util import (
    chart_series_debug,
    config_entry_bool_option,
    safe_float,
    stable_subdevice_key,
    utc_now,
)
from .ble import portable_ble_profile
from .ota_pending import async_load_pending
from .pack_candidates import (
    _PACK_STATE_DELETED as _PACK_STATE_DELETED,  # ruff: ignore[useless-import-alias] - compatibility export
    _PACK_TRANSACTION as _PACK_TRANSACTION,  # ruff: ignore[useless-import-alias] - compatibility export
    _HttpPackFieldCandidate as _HttpPackFieldCandidate,  # ruff: ignore[useless-import-alias] - compatibility export
    _PackCandidateTransaction as _PackCandidateTransaction,  # ruff: ignore[useless-import-alias] - compatibility export
    _commit_pack_candidates as _commit_pack_candidates,  # ruff: ignore[useless-import-alias] - compatibility export
    _pack_state_owner_key as _pack_state_owner_key,  # ruff: ignore[useless-import-alias] - compatibility export
    _pack_transaction as _pack_transaction,  # ruff: ignore[useless-import-alias] - compatibility export
    _transactional_pack_candidates as _transactional_pack_candidates,  # ruff: ignore[useless-import-alias] - compatibility export
)
from .weather_plan import is_weather_plan_event

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from ..models import IngestResult
    from .ble import BleBinaryFrame
    from .ble_transport import BleFrameObservation

_BACKGROUND_TASK_STOP_TIMEOUT_SEC = 2.0

_BOX_APP_INTEGER_MAX: Final = 2_147_483_647

_HOME_OTA_MIN_SOC: Final = 10

_HOME_OTA_MAX_SOC: Final = 100

_PORTABLE_UPS_BIND_EVIDENCE = "_portable_ups_bind_evidence"


def _portable_ups_region_matches(value: object, region: str) -> bool:
    return value is None or (isinstance(value, str) and (not value or value == region))


def _box_rc_invalid_safety_paths(updates: Mapping[str, Any]) -> set[tuple[str, ...]]:
    """Find malformed safety values without treating sparse fields as clears."""
    invalid: set[tuple[str, ...]] = set()

    def check(path: tuple[str, ...], value: object, low: int, high: int) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not low <= value <= high
        ):
            invalid.add(path)

    for root, names in {
        "fz": ("gs",),
        "ac1": ("bi", "rb"),
        "ac2": ("bi", "rb"),
    }.items():
        if root not in updates:
            continue
        container = updates[root]
        if not isinstance(container, Mapping):
            invalid.update((root, name) for name in names)
            continue
        for name in names:
            if name in container:
                high = (
                    1 if name == "bi" else 100 if name == "rb" else _BOX_APP_INTEGER_MAX
                )
                check((root, name), container[name], 0, high)
    for name, high in (
        ("rc", 1),
        ("ps", 1),
        ("wps", 1),
        ("en", 2),
        ("dt", 100),
        ("autoDt", 100),
    ):
        if name in updates:
            check((name,), updates[name], 0, high)
    if "storm" in updates:
        storms = updates["storm"]
        if not isinstance(storms, list) or any(
            not isinstance(storm, Mapping)
            or isinstance(storm.get("status"), bool)
            or not isinstance(storm.get("status"), int)
            or storm.get("status") not in {0, 1}
            for storm in storms
        ):
            invalid.add(("storm",))
    return invalid


def _box_rc_safety_values(updates: Mapping[str, Any]) -> dict[tuple[str, ...], object]:
    """Retain only comparison tokens for explicitly supplied RC safety fields."""
    values: dict[tuple[str, ...], object] = {}
    invalid = _box_rc_invalid_safety_paths(updates)
    for root, names in {
        "fz": ("gs",),
        "ac1": ("bi", "rb"),
        "ac2": ("bi", "rb"),
    }.items():
        if root not in updates:
            continue
        container = updates[root]
        for name in names:
            path: tuple[str, ...] = (root, name)
            if path in invalid:
                values[path] = ("invalid",)
            elif isinstance(container, Mapping) and name in container:
                values[path] = ("int", container[name])
    for name in ("rc", "ps", "wps", "en", "dt", "autoDt"):
        if name in updates:
            path = (name,)
            values[path] = ("invalid",) if path in invalid else ("int", updates[name])
    if "storm" in updates:
        path = ("storm",)
        if path in invalid:
            values[path] = ("invalid",)
        else:
            values[path] = (
                "storm",
                tuple(sorted(storm["status"] for storm in updates["storm"])),
            )
    return values


def _portable_ups_discovery_identity(
    device: Mapping[str, Any],
) -> tuple[int, str] | None:
    model = device.get(FIELD_MODEL_CODE)
    if isinstance(model, bool) or not isinstance(model, (int, str)):
        return None
    try:
        model_code = int(model)
    except ValueError:
        return None
    serials: set[str] = set()
    for field in (FIELD_DEVICE_SN, FIELD_DEV_SN):
        value = device.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            return None
        if not value:
            continue
        if value != value.strip():
            return None
        serials.add(value)
    if model_code not in {13, 22} or len(serials) != 1:
        return None
    return model_code, serials.pop()


_BLE_DELIVERY_RESULT_CACHE_SIZE: Final = 4096

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class BleConnectBackoff:
    """Exponential per-device spacing between BLE connect attempts with jitter.

    Jitter (±25%) prevents synchronized reconnect storms across multiple
    ESPHome BT-proxies when a peripheral or broker outage affects several
    devices simultaneously (Owner live observation: 8+ esp32.crash reports
    2026-07-03 from thundering-herd reconnects).
    """

    initial_sec: float = BLE_CONNECT_BACKOFF_INITIAL_SEC
    max_sec: float = BLE_CONNECT_BACKOFF_MAX_SEC
    _delay_sec: float = dataclass_field(default=0.0, init=False)
    _not_before: float = dataclass_field(default=0.0, init=False)

    def seconds_until_allowed(self, now: float) -> float:
        """Return how long the caller must still wait before connecting."""
        return max(0.0, self._not_before - now)

    def record_failure(self, now: float) -> float:
        """Register a failed connect and open a new retry window with jitter."""
        if self._delay_sec <= 0:
            self._delay_sec = self.initial_sec
        else:
            self._delay_sec = min(self._delay_sec * 2, self.max_sec)
        # Apply ±25% jitter to desynchronize concurrent backoff ladders
        jittered_delay = self._delay_sec * (0.75 + 0.5 * random.random())
        self._not_before = now + jittered_delay
        return self._delay_sec

    def record_success(self) -> None:
        """Reset the ladder after a successful connect."""
        self._delay_sec = 0.0
        self._not_before = 0.0


ACTION_WRITE_ERRORS = (
    JackeryError,
    HomeAssistantError,
    TimeoutError,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
)

BACKGROUND_TASK_ERRORS = ACTION_WRITE_ERRORS

PAYLOAD_PARSE_ERRORS = (
    UnicodeDecodeError,
    JSONDecodeError,
    ValueError,
    TypeError,
    KeyError,
)


def is_alarm_message(
    msg_type: str | None,
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether an MQTT message represents an alarm or alert.

    Checks the message type, action ID, and the command field in `body` for known
    alarm/alert indicators.

    Returns:
        `true` if the message is an alarm or alert, `false` otherwise.
    """
    return (
        msg_type == MQTT_MESSAGE_UPLOAD_DEVICE_ALERT
        or action_id in MQTT_ACTION_IDS_ALARM
        or body.get(FIELD_CMD) == MQTT_CMD_UPLOAD_DEVICE_ALERT
    )


def is_third_party_mqtt_config_message(
    msg_type: str | None,
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether an MQTT message is a third-party MQTT configuration.

    operation.

    Parameters:
        msg_type (str | None): The message type to check.
        action_id (int | None): The numeric action identifier to check.
        body (dict[str, Any]): The message payload; the function checks
        `body.get(FIELD_CMD)` for command matching.

    Returns:
        True if the message type, action id, or `body[FIELD_CMD]` indicates a
        third-party MQTT config request or query, False otherwise.
    """
    return (
        msg_type
        in {
            MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
            MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
        }
        or action_id
        in {
            ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
            ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
        }
        or body.get(FIELD_CMD)
        in {
            MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
            MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
        }
    )


def is_wifi_config_message(
    msg_type: str | None,
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether the MQTT message represents a WiFi configuration query.

    Returns:
        `true` if the message is a WiFi config query, `false` otherwise.
    """
    return (
        action_id in {ACTION_ID_QUERY_WIFI_CONFIG, ACTION_ID_PORTABLE_GET_WIFI_CONFIG}
        or msg_type == MQTT_MESSAGE_QUERY_WIFI_CONFIG
        or body.get(FIELD_CMD) == MQTT_CMD_QUERY_WIFI_CONFIG
    )


def is_wifi_list_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether the MQTT message requests the WiFi list.

    Parameters:
        action_id (int | None): Action identifier from the message header; may match
        ACTION_ID_READ_WIFI_LIST.
        body (dict[str, Any]): Message payload; may contain a command under FIELD_CMD.

    Returns:
        true if the message requests a WiFi list, false otherwise.
    """
    return (
        action_id == ACTION_ID_READ_WIFI_LIST
        or body.get(FIELD_CMD) == MQTT_CMD_READ_WIFI_LIST
    )


def is_time_zone_config_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Identify MQTT messages that request or provide the device time zone.

    Parameters:
        action_id (int | None): Message action identifier that may indicate a time zone
        get/send.
        body (dict[str, Any]): Message payload; `FIELD_CMD` may contain the command key.

    Returns:
        True if the message is a time zone get or send command, False otherwise.
    """
    return action_id in {
        ACTION_ID_GET_TIME_ZONE,
        ACTION_ID_SEND_TIME_ZONE,
    } or body.get(FIELD_CMD) in {MQTT_CMD_GET_TIME_ZONE, MQTT_CMD_SEND_TIME_ZONE}


def is_grid_standard_sync_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether the MQTT message represents a grid standard synchronization.

    @returns
        `true` if the message represents a grid standard sync, `false` otherwise.
    """
    return (
        action_id == ACTION_ID_SYNC_GRID_STANDARD
        or body.get(FIELD_CMD) == MQTT_CMD_SYNC_GRID_STANDARD
    )


def is_mqtt_connect_info_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether a message requests or synchronizes MQTT connection information.

    Parameters:
        action_id (int | None): Numeric action identifier from the message metadata;
        may be None.
        body (dict[str, Any]): Message body; the function checks the value under
        `FIELD_CMD`.

    Returns:
        bool: `True` if `action_id` equals `ACTION_ID_SYNC_MQTT_CONNECT_INFO` or
        `body.get(FIELD_CMD)` equals `MQTT_CMD_SYNC_MQTT_CONNECT_INFO`, `False`
        otherwise.
    """
    return (
        action_id == ACTION_ID_SYNC_MQTT_CONNECT_INFO
        or body.get(FIELD_CMD) == MQTT_CMD_SYNC_MQTT_CONNECT_INFO
    )


def is_device_ota_version_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether the MQTT message requests the device OTA version.

    Parameters:
        action_id (int | None): Numeric action identifier from the MQTT message.
        body (dict[str, Any]): Message payload; may contain a command under FIELD_CMD.

    Returns:
        bool: `true` if the message is a device OTA version query, `false` otherwise.
    """
    return (
        action_id == ACTION_ID_GET_DEVICE_OTA_VERSION
        or body.get(FIELD_CMD) == MQTT_CMD_GET_DEVICE_OTA_VERSION
    )


def _portable_pack_serial(item: object) -> str | None:
    """Accept only one consistent serial across a Portable pack's wrappers."""
    if not isinstance(item, dict):
        return None
    pending = [item]
    serials: set[str] = set()
    while pending:
        current = pending.pop()
        for key in (FIELD_DEVICE_SN, FIELD_DEV_SN, FIELD_SN):
            value = current.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip().isprintable():
                return None
            serials.add(value.strip())
        pending.extend(
            nested
            for key in (FIELD_UPDATES, FIELD_BODY, PAYLOAD_PROPERTIES)
            if isinstance(nested := current.get(key), dict)
        )
    return next(iter(serials)) if len(serials) == 1 else None


_CT_MODEL_CONFLICT_KEY: Final = ("jackery_ct_model_conflict",)


@dataclass(frozen=True, slots=True)
class _MqttRouteContext:
    """Normalized metadata for one serialized MQTT frame."""

    device_id: str
    topic: str
    payload: dict[str, Any]
    body: dict[str, Any]
    current: dict[str, Any]
    source: TransportSource
    observed_at: datetime | None
    message_type: str | None
    local_report_type: int | None
    action_id: int | None
    cmd: int | None
    classified_body: dict[str, Any]
    is_subdevice: bool
    is_alarm: bool
    combine_data: bool
    original_payload: Mapping[str, object] | None = None
    safety_ingress: BoxSafetyIngress | None = None
    stale_box_ingress: bool = False


@dataclass(frozen=True, slots=True)
class _BleGroundIdentity:
    """One session-bound, accepted Portable ground accessory identity."""

    serial: str
    observed_at: datetime
    generation: int
    sequence: int
    first_sequence: int
    fingerprint: bytes
    delivery_id: str


class _OriginalFrameOptions(TypedDict, total=False):
    record_source_payload: bool
    original_frame: Mapping[str, object] | None
    original_payload: Mapping[str, object] | None
    http_pack_generation: int | None
    http_pack_received_at: float | None
    http_pack_original_payload: Mapping[str, object] | None
    http_pack_owner_epochs: Mapping[str, int] | None
    family: str | None
    received_at_monotonic: float | None
    request_id: str | None
    safety_identity: tuple[int, str] | None
    safety_owner: tuple[int, int, str, bytes] | None
    safety_received_at: datetime | None
    safety_received_monotonic: float | None
    safety_mqtt_delivery: str | None


if TYPE_CHECKING:
    from collections import deque
    from collections.abc import Coroutine
    from typing import ClassVar

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, callback

    from ..coordinator import (
        _MergePropertiesOptions,
        _serialize_mqtt_messages_by_device,
    )
    from ..models import FieldProvenance, ProvenanceKey
    from .command_dispatcher import _PublishAllTransportOptions
    from .weather_plan import WeatherPlanState


class JackeryBleEventAdapterMixin:
    """Mixin handling BLE advertisement/observation ingestion and BLE transport."""

    if TYPE_CHECKING:
        _COMBINE_CANONICAL_PROPERTY_KEYS: ClassVar[frozenset[str]]

        _ble_box_ota_faults: dict[str, tuple[datetime, int, int, bool, bytes | None]]

        _ble_connect_backoff: dict[str, BleConnectBackoff]

        _ble_delivery_result_order: deque[str]

        _ble_delivery_results: dict[str, BleProcessDisposition]

        _ble_ground_identity: dict[str, _BleGroundIdentity]

        _ble_ground_recovery_triggers: dict[str, int]

        _ble_listener: Any

        _ble_original_observations: dict[str, dict[str, Any]]

        _ble_ota_battery: dict[
            str, tuple[datetime, int, int, bool, bytes | None, str | None]
        ]

        _ble_ota_events_observed_at: dict[tuple[str, str], datetime]

        _ble_ota_pending: dict[str, dict[str, str]] | None

        _ble_ota_recovered: dict[str, tuple[Any, ...]]

        _ble_ota_recovery_retry_at: dict[str, float]

        _ble_ota_versions_fingerprint: dict[str, bytes | None]

        _ble_ota_versions_first_sequence: dict[str, int | None]

        _ble_ota_versions_generation: dict[str, int | None]

        _ble_ota_versions_observed_at: dict[str, datetime]

        _ble_sink_token: object | None

        _ble_start_lock: asyncio.Lock

        _box_strategy_epochs: dict[str, int]

        _box_strategy_identity_seen: dict[str, tuple[int, str] | None]

        _box_strategy_mqtt_receipts: dict[
            str, dict[str, tuple[tuple[int, str], TransportSource, str, bytes]]
        ]

        _box_strategy_receipts: dict[
            str, dict[str, tuple[tuple[int, str], int, int, str, bytes, bytes]]
        ]

        _box_wifi_scan_receipts: dict[
            str, tuple[tuple[int, str], int, int, str, bytes, bytes]
        ]

        _device_index: dict[str, dict[str, Any]]

        _property_overrides: dict[str, tuple[float, dict[str, Any]]]

        _property_source_state: dict[str, dict[ProvenanceKey, FieldProvenance]]

        _shutdown_started: bool

        data: dict[str, dict[str, Any]]

        entry: ConfigEntry

        hass: HomeAssistant

        # Implemented by JackeryCommandDispatcherMixin in the composed coordinator.
        async def _async_publish_command_ble_first(
            self, device_id: str, **options: Unpack[_PublishAllTransportOptions]
        ) -> None:
            raise NotImplementedError

        async def async_query_device_info(  # ruff: ignore[too-many-arguments]
            self,
            device_id: str,
            *,
            ensure_mqtt: bool = True,
            allow_ble: bool = True,
            allow_cloud: bool = True,
            allow_local_mqtt: bool = True,
            include_http: bool = True,
        ) -> None:
            """Contract for the composed device-property query method."""
            raise NotImplementedError

        async def async_query_system_info(  # ruff: ignore[too-many-arguments]
            self,
            device_id: str,
            *,
            ensure_mqtt: bool = True,
            allow_ble: bool = True,
            allow_cloud: bool = True,
            allow_local_mqtt: bool = True,
            include_http: bool = True,
        ) -> None:
            """Contract for the composed system-property query method."""
            raise NotImplementedError

        async def _async_run_ground_ota_recovery(self, device_id: str) -> None:
            raise NotImplementedError

        def _ble_ota_pending_blocked(self, device_id: str) -> bool:
            raise NotImplementedError

        @staticmethod
        def _ble_ota_versions_idle_for_target(versions: object, target_sn: str) -> bool:
            raise NotImplementedError

        def _ble_partial_update_base(self, device_id: str) -> dict[str, Any] | None:
            raise NotImplementedError

        def _ble_writes_enabled(self) -> bool:
            raise NotImplementedError

        def _capture_box_ble_identity(self, device_id: str) -> tuple[int, str] | None:
            raise NotImplementedError

        def _is_battery_pack_frame(
            self, device_id: str | None, current: dict[str, Any], body: dict[str, Any]
        ) -> bool:
            raise NotImplementedError

        @classmethod
        def _is_subdevice_payload(
            cls, payload: dict[str, Any], body: dict[str, Any]
        ) -> bool:
            raise NotImplementedError

        def _merge_main_properties_for_device(
            self,
            device_id: str,
            base: dict[str, Any],
            updates: dict[str, Any],
            **options: Unpack[_MergePropertiesOptions],
        ) -> dict[str, Any]:
            raise NotImplementedError

        def _merge_partial_device_update(  # ruff: ignore[too-many-arguments]
            self,
            device_id: str,
            current: dict[str, Any],
            incoming: dict[str, Any],
            *,
            source: TransportSource | None = None,
            observed_at: datetime | None = None,
            battery_packs_complete: bool = False,
            observation_family: str | None = None,
            request_id: str | None = None,
            received_at_monotonic: float | None = None,
            record_source_payload: bool = True,
        ) -> dict[str, Any]:
            raise NotImplementedError

        def _merge_subdevice_data(
            self,
            updated: dict[str, Any],
            source: dict[str, Any],
            *,
            device_id: str | None = None,
            source_transport: TransportSource = TransportSource.CLOUD_MQTT,
            observed_at: datetime | None = None,
            **options: Unpack[_OriginalFrameOptions],
        ) -> bool:
            raise NotImplementedError

        @staticmethod
        def _normalize_live_property_payload(
            source: dict[str, Any], *, combine_data: bool = True
        ) -> dict[str, Any]:
            raise NotImplementedError

        def _patch_device_index_system_meta(
            self, device_id: str, updates: dict[str, Any]
        ) -> None:
            raise NotImplementedError

        def _prepare_weather_plan_event(  # ruff: ignore[too-many-arguments]
            self,
            device_id: str,
            body: dict[str, Any],
            source: TransportSource,
            *,
            observed_at: datetime | None,
            received_at_monotonic: float | None = None,
            request_id: str | None = None,
        ) -> WeatherPlanState | None:
            raise NotImplementedError

        def _record_box_rc_source_watermarks(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
            self,
            device_id: str,
            source: TransportSource,
            identity: tuple[int, str],
            observed_at: datetime,
            updates: Mapping[str, Any],
            owner: tuple[int, int, str, bytes] | None,
        ) -> None:
            raise NotImplementedError

        def _release_portable_reserve_override(
            self,
            device_id: str,
            payload: dict[str, Any],
            observation: BleFrameObservation,
        ) -> None:
            raise NotImplementedError

        def _resolve_device_sn(self, device_id: str) -> str | None:
            raise NotImplementedError

        def _restore_portable_reserve_override(
            self,
            device_id: str,
            previous_barriers: dict[str, tuple[object | None, tuple[int, int] | None]],
            previous_override: tuple[float, dict[str, Any]] | None,
        ) -> None:
            raise NotImplementedError

        def _schedule_background_once(
            self,
            key: str,
            factory: Callable[[], Coroutine[Any, Any, Any]],
            *,
            name: str,
        ) -> asyncio.Task[Any] | None:
            raise NotImplementedError

        def _schedule_ble_partial_update(  # ruff: ignore[too-many-arguments]
            self,
            device_id: str,
            updated_payload: dict[str, Any],
            *,
            observed_at: datetime | None = None,
            request_id: str | None = None,
            on_commit: Callable[[], None] | None = None,
            weather_plan_rows: list[Any] | None = None,
        ) -> bool:
            raise NotImplementedError

        def _schedule_payload_debug_event(
            self, event_or_factory: dict[str, Any] | Callable[[], dict[str, Any]]
        ) -> None:
            raise NotImplementedError

        def _store_third_party_mqtt_config_body(  # ruff: ignore[too-many-arguments]
            self,
            device_id: str,
            body: dict[str, Any],
            action_id: int | None,
            *,
            message_type: str | None = None,
            command: int | None = None,
            received_at_monotonic: float | None = None,
            reported_device_sn: str | None = None,
        ) -> dict[str, Any]:
            raise NotImplementedError

        def _transport_source_freshness_window(self) -> float:
            raise NotImplementedError

        def _weather_plan_commit_callback(
            self,
            device_id: str,
            state: WeatherPlanState,
            previous: Callable[[], None] | None,
        ) -> Callable[[], None]:
            raise NotImplementedError

        def _weather_plan_payload(
            self, device_id: str, body: dict[str, Any], state: WeatherPlanState
        ) -> dict[str, Any]:
            raise NotImplementedError

        async def async_handle_local_mqtt_message(
            self,
            topic: str,
            payload: dict[str, Any] | None = None,
            raw_bytes: bytes | None = None,
            *,
            _box_safety_ingress: BoxSafetyIngress | None = None,
        ) -> bool | Literal["broker_metadata"]:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        @_serialize_mqtt_messages_by_device
        @_transactional_pack_candidates
        async def async_handle_mqtt_message(
            self,
            topic: str,
            payload: dict[str, Any],
            *,
            source: TransportSource = TransportSource.CLOUD_MQTT,
            original_payload: Mapping[str, object] | None = None,
            _box_safety_ingress: BoxSafetyIngress | None = None,
        ) -> str | None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        @callback
        def async_update_listeners(self) -> None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def battery_pack_identity_serial(
            self, parent_device_id: str, pack_index: int
        ) -> str | None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def device_ble_profile(self, device_id: str) -> str | None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def device_bluetooth_key(self, device_id: str) -> bytes | None:
            """Provide the coordinator host operation."""
            raise NotImplementedError

        def device_family(
            self, device_id: str
        ) -> Literal["home", "box", "portable", "unknown"]:
            """Provide the coordinator host operation."""
            raise NotImplementedError

    def _ble_backoff_for_device(self, device_id: str) -> BleConnectBackoff:
        """Return the Coordinator-owned BLE connect backoff for one device."""
        backoff = self._ble_connect_backoff.get(device_id)
        if backoff is None:
            backoff = BleConnectBackoff()
            self._ble_connect_backoff[device_id] = backoff
        return backoff

    def _ble_connect_backoff_remaining(self, device_id: str, now: float) -> float:
        """Return remaining BLE connect backoff seconds for a device."""
        return self._ble_backoff_for_device(device_id).seconds_until_allowed(now)

    def _ble_note_connect_failure(self, device_id: str, now: float) -> float:
        """Record a BLE connect failure and return the next retry delay."""
        return self._ble_backoff_for_device(device_id).record_failure(now)

    def _ble_note_connect_success(self, device_id: str) -> None:
        """Clear BLE connect backoff after a successful device session."""
        self._ble_backoff_for_device(device_id).record_success()

    async def _async_ingest_ble_observation(
        self,
        device_id: str,
        observation: BleFrameObservation,
    ) -> BleProcessDisposition:
        """Apply and cache only confirmed BLE delivery IDs."""
        delivery_id = observation.delivery_id
        if delivery_id is not None and delivery_id in self._ble_delivery_results:
            return self._ble_delivery_results[delivery_id]

        # The decoder below contains no suspension point, so mutation plus this
        # commit is atomic with respect to event-loop cancellation. A retried
        # transport delivery therefore observes the committed result.
        result_or_awaitable = self._async_ingest_ble_observation_once(
            device_id,
            observation,
        )
        result: BleProcessDisposition = (
            await result_or_awaitable
            if inspect.isawaitable(result_or_awaitable)
            else result_or_awaitable
        )
        if delivery_id is not None and result is BleProcessDisposition.CONFIRMED:
            if len(self._ble_delivery_result_order) >= _BLE_DELIVERY_RESULT_CACHE_SIZE:
                oldest = self._ble_delivery_result_order.popleft()
                self._ble_delivery_results.pop(oldest, None)
            self._ble_delivery_result_order.append(delivery_id)
            self._ble_delivery_results[delivery_id] = result
        return result

    def _decode_ble_payload(  # ruff: ignore[too-many-return-statements]
        self,
        device_id: str,
        observation: BleFrameObservation,
    ) -> dict[str, Any] | None:
        """Decode one BLE JSON body and remove its transport command field."""
        parsed = observation.parsed
        if observation.is_fragment or parsed is None or not parsed.body:
            return None
        if (parsed.flags, parsed.cmd) == (8, 6) and self.device_ble_profile(
            device_id
        ) not in {"rc4", "aes_fixed", "aes_random"}:
            return None
        try:
            decoded = json.loads(parsed.body.decode("utf-8"))
        except PAYLOAD_PARSE_ERRORS as err:
            _LOGGER.debug(
                "Jackery BLE %s: body is not JSON (cmd=%d, %d bytes): %s",
                device_id,
                parsed.cmd,
                len(parsed.body),
                err,
            )
            return None
        if isinstance(decoded, list) and parsed.cmd == MQTT_CMD_GET_DEVICE_OTA_VERSION:
            return {PAYLOAD_BLE_OTA_VERSIONS: decoded}
        if (
            isinstance(decoded, list)
            and (parsed.flags, parsed.cmd) == (8, 6)
            and self.device_ble_profile(device_id) in {"rc4", "aes_fixed", "aes_random"}
        ):
            return {FIELD_BATTERY_PACKS: decoded}
        if not isinstance(decoded, dict):
            _LOGGER.debug(
                "Jackery BLE %s: body decoded to %s, expected dict",
                device_id,
                type(decoded).__name__,
            )
            return None
        payload = {key: value for key, value in decoded.items() if key != FIELD_CMD}
        return payload or None

    @staticmethod
    def _message_type_for_payload(payload: Mapping[str, Any]) -> str | None:
        """Return a non-empty App message type."""
        value = payload.get(FIELD_MESSAGE_TYPE)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _is_combine_data_message(
        message_type: str | None,
        action_id: int | None,
        cmd: int | None,
    ) -> bool:
        """Return whether a frame carries CombineData properties."""
        return (
            message_type
            in {
                MQTT_MESSAGE_QUERY_COMBINE_DATA,
                MQTT_MESSAGE_UPLOAD_COMBINE_DATA,
                MQTT_MESSAGE_UPLOAD_INCREMENTAL_COMBINE_DATA,
                MQTT_MESSAGE_CONTROL_COMBINE,
            }
            or action_id in MQTT_ACTION_IDS_COMBINE
            or cmd in {MQTT_CMD_QUERY_COMBINE_DATA, MQTT_CMD_CONTROL_COMBINE}
        )

    def _ble_config_payload_update(  # ruff: ignore[too-many-branches]  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        current: dict[str, Any],
        payload: dict[str, Any],
        cmd: int,
        action_id: int,
        *,
        received_at_monotonic: float | None = None,
    ) -> dict[str, Any] | None:
        """Return the first matching BLE config-family update."""
        if PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID.get(
            action_id
        ) == cmd and self.device_ble_profile(device_id) in {
            "rc4",
            "aes_fixed",
            "aes_random",
        }:
            if action_id == ACTION_ID_PORTABLE_SEND_TIME_ZONE:
                portable_update = {PAYLOAD_TIMEZONE_CONFIG: payload}
                timezone = payload.get(FIELD_TIMEZONE)
                if timezone is not None:
                    system = dict(current.get(PAYLOAD_SYSTEM) or {})
                    system[FIELD_TIMEZONE] = timezone
                    portable_update[PAYLOAD_SYSTEM] = system
                    self._patch_device_index_system_meta(
                        device_id, {FIELD_TIMEZONE: timezone}
                    )
                return portable_update
            if action_id in {
                ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
                ACTION_ID_PORTABLE_ADD_CHARGE_PLAN,
                ACTION_ID_PORTABLE_UPDATE_CHARGE_PLAN,
                ACTION_ID_PORTABLE_DELETE_CHARGE_PLAN,
                ACTION_ID_PORTABLE_CURRENT_CHARGE_PLAN,
            }:
                return {PAYLOAD_ELECTRICITY_STRATEGY: payload}
            if action_id in {
                ACTION_ID_PORTABLE_SET_PEAKS_TROUGHS,
                ACTION_ID_PORTABLE_GET_PEAKS_TROUGHS,
            }:
                return {PAYLOAD_TOU_SCHEDULE: payload}
            if action_id == ACTION_ID_PORTABLE_CUSTOM_USE_BATTERY:
                return {PAYLOAD_BATTERY_BOUNDARY: payload}
        message_type = self._message_type_for_payload(payload)
        classified = {**payload, FIELD_CMD: cmd}
        route_action_id = (
            None
            if cmd
            in {
                MQTT_CMD_QUERY_DEVICE_PROPERTY,
                MQTT_CMD_DEVICE_PROPERTY_CHANGE,
                MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
                MQTT_CMD_CONTROL_SUB_DEVICE,
                MQTT_CMD_QUERY_COMBINE_DATA,
                MQTT_CMD_CONTROL_COMBINE,
            }
            else action_id
        )
        update: dict[str, Any] | None = None
        if is_alarm_message(message_type, route_action_id, classified):
            update = {
                PAYLOAD_DEVICE_ALERT: merge_live_properties(
                    current.get(PAYLOAD_DEVICE_ALERT) or {}, payload
                )
            }
        elif is_third_party_mqtt_config_message(
            message_type,
            route_action_id,
            classified,
        ):
            update = {
                PAYLOAD_THIRD_PARTY_MQTT_CONFIG: (
                    self._store_third_party_mqtt_config_body(
                        device_id,
                        payload,
                        action_id or None,
                        message_type=message_type,
                        command=cmd,
                        received_at_monotonic=received_at_monotonic,
                    )
                )
            }
        elif is_wifi_config_message(message_type, route_action_id, classified):
            update = {PAYLOAD_WIFI_CONFIG: payload}
        elif is_wifi_list_message(route_action_id, classified):
            update = {PAYLOAD_WIFI_LIST: payload}
        elif is_time_zone_config_message(route_action_id, classified):
            update = {PAYLOAD_TIMEZONE_CONFIG: payload}
            timezone = payload.get(FIELD_TIMEZONE)
            if timezone is not None:
                system = dict(current.get(PAYLOAD_SYSTEM) or {})
                system[FIELD_TIMEZONE] = timezone
                update[PAYLOAD_SYSTEM] = system
                self._patch_device_index_system_meta(
                    device_id,
                    {FIELD_TIMEZONE: timezone},
                )
        elif is_grid_standard_sync_message(route_action_id, classified):
            update = {}
            value = payload.get(FIELD_GRID_STANDARD)
            if value is None:
                value = payload.get(FIELD_SAFETY)
            if value is not None:
                system = dict(current.get(PAYLOAD_SYSTEM) or {})
                system[FIELD_GRID_STANDARD] = str(value)
                update[PAYLOAD_SYSTEM] = system
                self._patch_device_index_system_meta(
                    device_id,
                    {FIELD_GRID_STANDARD: str(value)},
                )
        elif is_mqtt_connect_info_message(route_action_id, classified):
            update = {PAYLOAD_MQTT_CONNECT_INFO: payload}
        elif is_device_ota_version_message(route_action_id, classified):
            ota = dict(current.get(PAYLOAD_OTA) or {})
            ota.update(payload)
            update = {PAYLOAD_OTA: ota}
        return update

    def _ble_app_payload_update(
        self,
        payload: dict[str, Any],
        cmd: int,
        action_id: int,
    ) -> dict[str, Any] | None:
        """Return the first matching BLE app-feature update."""
        message_type = self._message_type_for_payload(payload)
        if (
            message_type
            in {
                MQTT_MESSAGE_UPLOAD_WEATHER_PLAN,
                MQTT_MESSAGE_QUERY_WEATHER_PLAN,
                MQTT_MESSAGE_SEND_WEATHER_ALERT,
                MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
            }
            or cmd == MQTT_CMD_QUERY_WEATHER_PLAN
        ):
            return {PAYLOAD_WEATHER_PLAN: payload}
        if (
            message_type == MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE
            or cmd == MQTT_CMD_DOWNLOAD_DEVICE_SCHEDULE
        ):
            return {PAYLOAD_TASK_PLAN: payload}
        if message_type in {
            MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
            MQTT_MESSAGE_INSERT_ELECTRICITY_STRATEGY,
            MQTT_MESSAGE_UPDATE_ELECTRICITY_STRATEGY,
            MQTT_MESSAGE_DELETE_ELECTRICITY_STRATEGY,
            MQTT_MESSAGE_QUERY_CURRENT_ELECTRICITY_STRATEGY,
        }:
            return {PAYLOAD_ELECTRICITY_STRATEGY: payload}
        if message_type in {
            MQTT_MESSAGE_TOU_SCHEDULE,
            MQTT_MESSAGE_QUERY_TOU_SCHEDULE,
        }:
            return {PAYLOAD_TOU_SCHEDULE: payload}
        if message_type == MQTT_MESSAGE_SET_BATTERY_BOUNDARY:
            return {PAYLOAD_BATTERY_BOUNDARY: payload}
        return None

    def _apply_ble_subdevice_or_properties(  # ruff: ignore[too-many-return-statements, too-many-locals]  # ruff: ignore[too-many-branches]
        self,
        device_id: str,
        observation: BleFrameObservation,
        current: dict[str, Any],
        updated: dict[str, Any],
        payload: dict[str, Any],
        **options: Unpack[_OriginalFrameOptions],
    ) -> bool:
        """Apply the final exclusive BLE subdevice or property route."""
        parsed = observation.parsed
        if parsed is None:
            return False
        cmd = parsed.cmd
        action_id = parsed.flags
        if (action_id, cmd) == (8, 6) and self.device_ble_profile(device_id) in {
            "rc4",
            "aes_fixed",
            "aes_random",
        }:
            if (
                FIELD_BATTERY_PACKS in payload
                and FIELD_BODY in payload
                and payload[FIELD_BATTERY_PACKS] != payload[FIELD_BODY]
            ):
                return False
            rows = payload.get(FIELD_BATTERY_PACKS, payload.get(FIELD_BODY))
            if rows is None and (
                FIELD_BATTERY_PACKS in payload or FIELD_BODY in payload
            ):
                rows = []
            if not isinstance(rows, list):
                return False
            serials = [_portable_pack_serial(row) for row in rows]
            packs = [
                row
                for row in rows
                if isinstance(row, dict)
                and (serial := _portable_pack_serial(row)) is not None
                and serials.count(serial) == 1
            ]
            packs_touched = self._merge_subdevice_data(
                updated,
                {FIELD_BATTERY_PACKS: packs},
                device_id=device_id,
                source_transport=TransportSource.BLE,
                observed_at=observation.received_at,
                original_frame=options.get("original_frame"),
            )
            if any(not isinstance(row, dict) or not row for row in rows):
                return packs_touched
            previous_properties = current.get(PAYLOAD_PROPERTIES) or {}
            properties = self._merge_main_properties_for_device(
                device_id,
                previous_properties,
                {FIELD_IS_PACK_CONNECT: bool(rows)},
                source=TransportSource.BLE,
                observed_at=observation.received_at,
                request_id=observation.delivery_id or f"ble:{id(observation)}",
            )
            updated[PAYLOAD_PROPERTIES] = properties
            return packs_touched or properties != previous_properties
        box_circuit = (
            (action_id, cmd) == (7, 10)
            and self.device_ble_profile(device_id) == "box_fixed"
            and isinstance(payload.get(FIELD_CIR), list)
        )
        circuit_touched = False
        if box_circuit:
            circuit_touched = self._merge_subdevice_data(
                updated,
                {FIELD_CIR: payload[FIELD_CIR]},
                device_id=device_id,
                source_transport=TransportSource.BLE,
                observed_at=observation.received_at,
                original_frame=options.get("original_frame"),
            )
            payload = {key: value for key, value in payload.items() if key != FIELD_CIR}
            if not payload:
                return circuit_touched
        message_type = self._message_type_for_payload(payload)
        classified = {**payload, FIELD_CMD: cmd}
        envelope: dict[str, Any] = {}
        if cmd == MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY:
            envelope[FIELD_ACTION_ID] = action_id
        if message_type is not None:
            envelope[FIELD_MESSAGE_TYPE] = message_type
        is_subdevice = self._is_subdevice_payload(
            envelope, classified
        ) or self._is_battery_pack_frame(device_id, current, classified)
        if (
            is_subdevice
            or cmd == MQTT_CMD_CONTROL_SUB_DEVICE
            or (
                cmd == MQTT_CMD_QUERY_COMBINE_DATA
                and self._is_subdevice_payload(payload, classified)
            )
        ):
            return self._merge_subdevice_data(
                updated,
                payload,
                device_id=device_id,
                source_transport=TransportSource.BLE,
                observed_at=observation.received_at,
                original_frame=options.get("original_frame"),
            )

        combine_data = self._is_combine_data_message(message_type, action_id, cmd)
        property_payload = self._normalize_live_property_payload(
            payload,
            combine_data=combine_data,
        )
        if (
            action_id == ACTION_ID_PORTABLE_READ_DEVICE_INFO
            and cmd
            == PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[ACTION_ID_PORTABLE_READ_DEVICE_INFO]
        ):
            self._release_portable_reserve_override(
                device_id, property_payload, observation
            )
        safety_owner = None
        if (
            observation.device_identity is not None  # ruff: ignore[too-many-boolean-expressions]
            and observation.session_generation is not None
            and observation.notify_sequence is not None
            and observation.delivery_id is not None
            and observation.key_fingerprint is not None
            and observation.key_fingerprint == observation.session_key_fingerprint
            and observation.parsed is not None
            and observation.safety_received_at is not None
            and observation.received_at_monotonic is not None
        ):
            safety_owner = (
                observation.session_generation,
                observation.notify_sequence,
                observation.delivery_id,
                observation.key_fingerprint,
            )
        updated[PAYLOAD_PROPERTIES] = self._merge_main_properties_for_device(
            device_id,
            current.get(PAYLOAD_PROPERTIES) or {},
            property_payload,
            source=TransportSource.BLE,
            observed_at=observation.received_at,
            original_frame=options.get("original_frame"),
            request_id=observation.delivery_id or f"ble:{id(observation)}",
            safety_identity=observation.device_identity if safety_owner else None,
            safety_owner=safety_owner,
            safety_received_at=observation.safety_received_at if safety_owner else None,
            safety_received_monotonic=(
                observation.received_at_monotonic if safety_owner else None
            ),
            canonical_live_keys=(
                self._COMBINE_CANONICAL_PROPERTY_KEYS if combine_data else frozenset()
            ),
        )
        return bool(property_payload) or circuit_touched

    def _finalize_ble_observation_route(  # ruff: ignore[too-many-arguments]
        self,
        device_id: str,
        observation: BleFrameObservation,
        current: dict[str, Any],
        updated: dict[str, Any],
        touched: bool,
        *,
        on_stage: Callable[[], None] | None = None,
        on_commit: Callable[[], None] | None = None,
        weather_plan_rows: list[Any] | None = None,
    ) -> BleProcessDisposition:
        """Return the delivery disposition after one atomic BLE route."""
        if on_stage is not None:
            on_stage()
        if not touched:
            if self._ble_listener is not None and observation.parsed is not None:
                cmd = observation.parsed.cmd
                stats = self._ble_listener.stats_for(device_id)
                stats.unrouted_frames_by_cmd[cmd] = (
                    stats.unrouted_frames_by_cmd.get(cmd, 0) + 1
                )
            return BleProcessDisposition.UNROUTED
        if updated == current:
            if on_commit is not None:
                on_commit()
            return BleProcessDisposition.CONFIRMED
        committed = self._schedule_ble_partial_update(
            device_id,
            updated,
            observed_at=observation.received_at,
            request_id=observation.delivery_id or f"ble:{id(observation)}",
            on_commit=on_commit,
            weather_plan_rows=weather_plan_rows,
        )
        return (
            BleProcessDisposition.CONFIRMED
            if committed
            else BleProcessDisposition.RETRY
        )

    @_transactional_pack_candidates
    async def _async_ingest_ble_observation_once(  # ruff: ignore[too-many-locals, too-many-branches, too-many-statements]  # ruff: ignore[complex-structure]
        self,
        device_id: str,
        observation: BleFrameObservation,
    ) -> BleProcessDisposition:
        """Preserve uncommitted BLE ownership and property overrides."""
        provenance = getattr(self, "_box_strategy_provenance", {})
        previous = copy.deepcopy(provenance.get(device_id))
        barriers = getattr(self, "_portable_reserve_override_barriers", {})
        previous_barriers = {
            field: barriers[device_id, field]
            for field in (FIELD_SRFC, FIELD_SRL)
            if (device_id, field) in barriers
        }
        previous_override = (
            self._property_overrides.get(device_id) if previous_barriers else None
        )
        previous_data = getattr(self, "data", None)
        accessory_state = getattr(self, "_accessory_source_state", {})
        previous_accessories = {
            key: value for key, value in accessory_state.items() if key[0] == device_id
        }
        staged_accessories: dict[tuple[str, str, str], IngestResult] | None = None
        route_committed = False

        def note_stage() -> None:
            nonlocal staged_accessories
            staged_accessories = {
                key: value
                for key, value in accessory_state.items()
                if key[0] == device_id
            }

        def note_commit() -> None:
            nonlocal route_committed
            _commit_pack_candidates(self)
            route_committed = True

        previous_ct_conflicts = {
            key
            for key, value in previous_accessories.items()
            if _CT_MODEL_CONFLICT_KEY in value.provenance
        }
        previous_strategy_current = {
            field: self.strategy_field_is_current(device_id, field)
            for field in ("cds", "cep")
        }
        if self.device_family(device_id) in {"box", "portable"}:
            self._box_strategy_identity_seen.setdefault(
                device_id, self._strategy_device_identity(device_id)
            )
        strategy_epoch = getattr(self, "_box_strategy_epochs", {}).get(device_id, 0)
        predecessor_epoch = getattr(self, "_box_strategy_predecessor_epochs", {}).get(
            device_id, 0
        )
        previous_property_state = dict(
            getattr(self, "_property_source_state", {}).get(device_id, {})
        )
        previous_box_epochs = {
            channel: getattr(self, "_box_solar_epochs", {}).get((device_id, channel))
            for channel in ("ac1", "ac2")
        }
        request_id = observation.delivery_id or f"ble:{id(observation)}"
        previous_families = {
            field: item.family
            for field, item in getattr(self, "_property_source_state", {})
            .get(device_id, {})
            .items()
        }
        previous_safety = {
            field: item.safety_revoked_by
            for field, item in previous_property_state.items()
        }
        confirmed = False
        try:
            result = await self._async_ingest_ble_observation_once_impl(
                device_id, observation, on_stage=note_stage, on_commit=note_commit
            )
            confirmed = result is not BleProcessDisposition.RETRY
            blocked_session = observation.session_generation is not None and getattr(
                self, "_box_strategy_blocked_generation", {}
            ).get(device_id) == (
                getattr(self, "_ble_listener", None),
                observation.session_generation,
            )
            if confirmed and not blocked_session:
                self._record_ble_ground_identity(device_id, observation, result)
                self._record_box_rc_ble_watermarks(device_id, observation, result)
                self._record_box_wifi_scan(device_id, observation, result)
                self._record_box_strategy(
                    device_id,
                    observation,
                    result,
                    previous_data,
                    previous_strategy_current,
                    strategy_epoch,
                    predecessor_epoch,
                )
            if confirmed and getattr(self, "data", None) is previous_data:
                current_families = {
                    field: item.family
                    for field, item in getattr(self, "_property_source_state", {})
                    .get(device_id, {})
                    .items()
                }
                current_safety = {
                    field: item.safety_revoked_by
                    for field, item in getattr(self, "_property_source_state", {})
                    .get(device_id, {})
                    .items()
                }
                if (
                    current_families != previous_families
                    or current_safety != previous_safety
                    or previous_ct_conflicts
                    != {
                        key
                        for key, value in accessory_state.items()
                        if key[0] == device_id
                        and _CT_MODEL_CONFLICT_KEY in value.provenance
                    }
                ):
                    self.async_update_listeners()
            return result
        finally:
            if not confirmed:
                epochs = getattr(self, "_box_solar_epochs", {})
                current_property_state = getattr(
                    self, "_property_source_state", {}
                ).get(device_id, {})
                for channel, previous_epoch in previous_box_epochs.items():
                    key = (device_id, channel)
                    current_epoch = epochs.get(key)
                    if current_epoch is None or current_epoch.request_id != request_id:
                        continue
                    for field in ("ss", "acpsp"):
                        path = (channel, field)
                        if (
                            path not in current_property_state
                            and (old_field := previous_property_state.get(path))
                            is not None
                        ):
                            current_property_state[path] = old_field
                    if previous_epoch is None:
                        epochs.pop(key, None)
                    else:
                        epochs[key] = previous_epoch
                for field, item in tuple(current_property_state.items()):
                    if request_id not in {item.request_id, item.safety_revoked_by}:
                        continue
                    if field in previous_property_state:
                        current_property_state[field] = previous_property_state[field]
                    else:
                        current_property_state.pop(field, None)
            if not confirmed and not route_committed:
                written = staged_accessories
                if written is None:
                    written = {
                        key: value
                        for key, value in accessory_state.items()
                        if key[0] == device_id
                    }
                for accessory_key, value in written.items():
                    if (
                        accessory_key[1] == PAYLOAD_BATTERY_PACKS
                        and _pack_transaction(self) is not None
                    ):
                        continue
                    if accessory_state.get(accessory_key) is not value:
                        continue
                    if accessory_key in previous_accessories:
                        accessory_state[accessory_key] = previous_accessories[
                            accessory_key
                        ]
                    else:
                        accessory_state.pop(accessory_key, None)
            if not confirmed and getattr(self, "data", None) is previous_data:
                self._restore_portable_reserve_override(
                    device_id, previous_barriers, previous_override
                )
                provenance = getattr(self, "_box_strategy_provenance", {})
                if previous is None:
                    provenance.pop(device_id, None)
                else:
                    provenance[device_id] = previous

    def _record_box_wifi_scan(
        self,
        device_id: str,
        observation: BleFrameObservation,
        result: BleProcessDisposition,
    ) -> None:
        """Bind a confirmed Box Wi-Fi list to its BLE delivery and identity."""
        parsed = observation.parsed
        listener = getattr(self, "_ble_listener", None)
        identity = observation.device_identity
        generation = observation.session_generation
        sequence = observation.notify_sequence
        delivery, fingerprint = observation.delivery_id, observation.key_fingerprint
        if (
            result is not BleProcessDisposition.CONFIRMED
            or parsed is None
            or (parsed.flags, parsed.cmd) != (254, 1)
            or observation.protocol_status != 0
        ):
            return
        if (
            listener is None
            or identity is None
            or identity != self._box_safety_device_identity(device_id)
        ):
            return
        if (
            generation is None
            or sequence is None
            or delivery is None
            or fingerprint is None
        ):
            return
        if (
            fingerprint != observation.session_key_fingerprint
            or not listener.owns_current_observation(device_id, observation)
        ):
            return
        payload = self._decode_ble_payload(device_id, observation)
        names = payload.get("f") if isinstance(payload, dict) else None
        current = ((self.data or {}).get(device_id) or {}).get(PAYLOAD_WIFI_LIST)
        if not isinstance(names, list) or not all(
            isinstance(name, str) for name in names
        ):
            if self._box_wifi_scan_receipts.pop(device_id, None) is not None:
                self.async_update_listeners()
            return
        if not isinstance(current, Mapping) or current.get("f") != names:
            return
        digest = hashlib.sha256(json.dumps(names, ensure_ascii=False).encode()).digest()
        self._box_wifi_scan_receipts[device_id] = (
            identity,
            generation,
            sequence,
            delivery,
            fingerprint,
            digest,
        )
        self.async_update_listeners()

    def box_wifi_scan_is_current(self, device_id: str) -> bool:
        """Return whether the retained list belongs to the current Box session."""
        receipt: tuple[tuple[int, str], int, int, str, bytes, bytes] | None = getattr(
            self, "_box_wifi_scan_receipts", {}
        ).get(device_id)
        listener = getattr(self, "_ble_listener", None)
        if receipt is None or listener is None:
            return False
        identity, generation, sequence, delivery, fingerprint, digest = receipt
        key = self.device_bluetooth_key(device_id)
        current = ((self.data or {}).get(device_id) or {}).get(PAYLOAD_WIFI_LIST)
        names = current.get("f") if isinstance(current, Mapping) else None
        if identity != self._box_safety_device_identity(device_id) or key is None:
            return False
        if hashlib.sha256(key).digest() != fingerprint:
            return False
        if not listener.owns_current_delivery(
            device_id, generation, sequence, delivery
        ):
            return False
        if not isinstance(names, list) or not all(
            isinstance(name, str) for name in names
        ):
            return False
        actual = hashlib.sha256(json.dumps(names, ensure_ascii=False).encode()).digest()
        return actual == digest

    def _record_box_strategy(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        self,
        device_id: str,
        observation: BleFrameObservation,
        result: BleProcessDisposition,
        previous_data: dict[str, Any] | None,
        previous_current: dict[str, bool],
        strategy_epoch: int,
        predecessor_epoch: int,
    ) -> None:
        """Bind an accepted Box plan field to its BLE delivery and identity."""
        frame = observation.parsed
        field = (
            (
                {(12, 15): "cds", (18, 21): "cep"}
                if self.device_family(device_id) == "box"
                else {
                    (26, 15): "cds",
                    (27, 16): "cds",
                    (28, 17): "cds",
                    (29, 18): "cds",
                    (30, 21): "cep",
                }
                if self.device_family(device_id) == "portable"
                else {}
            ).get((frame.flags, frame.cmd))
            if frame is not None
            else None
        )
        listener = getattr(self, "_ble_listener", None)
        identity = observation.device_identity
        generation = observation.session_generation
        sequence = observation.notify_sequence
        delivery = observation.delivery_id
        fingerprint = observation.key_fingerprint
        key = self.device_bluetooth_key(device_id)
        if (
            field is None  # ruff: ignore[too-many-boolean-expressions]
            or getattr(self, "_box_strategy_epochs", {}).get(device_id, 0)
            != strategy_epoch
            or result is not BleProcessDisposition.CONFIRMED
            or observation.protocol_status != 0
            or identity is None
            or identity != self._strategy_device_identity(device_id)
            or listener is None
            or generation is None
            or sequence is None
            or delivery is None
            or fingerprint is None
            or getattr(self, "_box_strategy_blocked_generation", {}).get(device_id)
            == (listener, generation)
            or fingerprint != observation.session_key_fingerprint
            or key is None
            or hashlib.sha256(key).digest() != fingerprint
            or not listener.owns_current_observation(device_id, observation)
        ):
            return
        payload = self._decode_ble_payload(device_id, observation)
        strategy = ((self.data or {}).get(device_id) or {}).get(
            PAYLOAD_ELECTRICITY_STRATEGY
        )
        provenance = (
            getattr(self, "_box_strategy_provenance", {}).get(device_id, {}).get(field)
        )
        if (
            not isinstance(payload, dict)  # ruff: ignore[too-many-boolean-expressions]
            or field not in payload
            or not isinstance(strategy, Mapping)
            or field not in strategy
            or not self._box_strategy_update_matches(
                device_id,
                field,
                strategy[field],
                payload[field],
                previous_data,
                previous_current.get(field, False)
                and getattr(self, "_box_strategy_predecessor_epochs", {}).get(
                    device_id, 0
                )
                == predecessor_epoch,
            )
            or provenance is None
            or provenance.source is not TransportSource.BLE
            or provenance.request_id != delivery
        ):
            return
        digest = hashlib.sha256(
            json.dumps(strategy[field], sort_keys=True, ensure_ascii=False).encode()
        ).digest()
        self._box_strategy_receipts.setdefault(device_id, {})[field] = (
            identity,
            generation,
            sequence,
            delivery,
            fingerprint,
            digest,
        )
        self.async_update_listeners()

    @staticmethod
    def _box_strategy_update_matches(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        device_id: str,
        field: str,
        current: object,
        incoming: object,
        previous_data: dict[str, Any] | None,
        previous_current: bool,
    ) -> bool:
        """Allow a sparse merge only onto a previously bound plan."""
        if current == incoming:
            return True
        previous = ((previous_data or {}).get(device_id) or {}).get(
            PAYLOAD_ELECTRICITY_STRATEGY
        )
        return bool(
            previous_current
            and isinstance(previous, Mapping)
            and isinstance(previous.get(field), dict)
            and isinstance(incoming, dict)
            and merge_dict_values(previous[field], incoming) == current
        )

    def _record_box_strategy_mqtt(
        self,
        context: _MqttRouteContext,
        previous_data: dict[str, Any] | None,
        previous_current: dict[str, bool],
        strategy_epoch: int,
        predecessor_epoch: int,
    ) -> None:
        """Bind selected MQTT plan fields after their coordinator commit."""
        receipt = context.safety_ingress
        if (
            receipt is None
            or getattr(self, "_box_strategy_epochs", {}).get(context.device_id, 0)
            != strategy_epoch
            # A key change can revoke the BLE predecessor without changing
            # the physical identity epoch. A message admitted before that
            # revocation must not certify a new MQTT plan after the commit.
            or getattr(self, "_box_strategy_predecessor_epochs", {}).get(
                context.device_id, 0
            )
            != predecessor_epoch
        ):
            return
        captured_epoch = dict(receipt.identity_epochs).get(context.device_id)
        if captured_epoch != strategy_epoch:
            return
        identity = next(
            (
                (model, serial)
                for device, model, serial in receipt.identities
                if device == context.device_id
            ),
            None,
        )
        if identity is None or identity != self._strategy_device_identity(
            context.device_id
        ):
            return
        strategy = ((self.data or {}).get(context.device_id) or {}).get(
            PAYLOAD_ELECTRICITY_STRATEGY
        )
        provenance = getattr(self, "_box_strategy_provenance", {}).get(
            context.device_id, {}
        )
        if not isinstance(strategy, Mapping):
            return
        for field in ("cds", "cep"):
            incoming = context.body.get(field)
            # A null-only current-plan delta is not independent evidence for
            # the preceding BLE plan, especially across a key A-B-A change.
            if (
                field == "cep"
                and isinstance(incoming, Mapping)
                and not any(value is not None for value in incoming.values())
            ):
                continue
            item = provenance.get(field)
            if (
                field not in context.body  # ruff: ignore[too-many-boolean-expressions]
                or field not in strategy
                or not self._box_strategy_update_matches(
                    context.device_id,
                    field,
                    strategy[field],
                    context.body[field],
                    previous_data,
                    previous_current.get(field, False)
                    and getattr(self, "_box_strategy_predecessor_epochs", {}).get(
                        context.device_id, 0
                    )
                    == predecessor_epoch,
                )
                or item is None
                or item.source is not context.source
                or item.request_id != receipt.delivery_id
            ):
                continue
            digest = hashlib.sha256(
                json.dumps(strategy[field], sort_keys=True, ensure_ascii=False).encode()
            ).digest()
            self._box_strategy_mqtt_receipts.setdefault(context.device_id, {})[
                field
            ] = (identity, context.source, receipt.delivery_id, digest)

    def strategy_field_is_current(self, device_id: str, field: str) -> bool:
        """Return whether a plan belongs to the device and selected delivery."""
        provenance = (
            getattr(self, "_box_strategy_provenance", {}).get(device_id, {}).get(field)
        )
        strategy = ((self.data or {}).get(device_id) or {}).get(
            PAYLOAD_ELECTRICITY_STRATEGY
        )
        if (
            not isinstance(strategy, Mapping)
            or field not in strategy
            or device_id in getattr(self, "_box_safety_mqtt_pending", set())
        ):
            return False
        actual = hashlib.sha256(
            json.dumps(strategy[field], sort_keys=True, ensure_ascii=False).encode()
        ).digest()
        mqtt_receipt = (
            getattr(self, "_box_strategy_mqtt_receipts", {})
            .get(device_id, {})
            .get(field)
        )
        mqtt_matches = bool(
            mqtt_receipt is not None
            and mqtt_receipt[0] == self._strategy_device_identity(device_id)
            and mqtt_receipt[3] == actual
        )
        if provenance is None:
            return mqtt_matches
        if provenance.source in {
            TransportSource.CLOUD_MQTT,
            TransportSource.LOCAL_MQTT,
        }:
            return bool(
                mqtt_matches
                and mqtt_receipt
                == (
                    self._strategy_device_identity(device_id),
                    provenance.source,
                    provenance.request_id,
                    actual,
                )
            )
        receipt = (
            getattr(self, "_box_strategy_receipts", {}).get(device_id, {}).get(field)
        )
        listener = getattr(self, "_ble_listener", None)
        if receipt is None or listener is None:
            return mqtt_matches
        identity, generation, sequence, delivery, fingerprint, digest = receipt
        key = self.device_bluetooth_key(device_id)
        return mqtt_matches or bool(
            identity == self._strategy_device_identity(device_id)
            and key is not None
            and hashlib.sha256(key).digest() == fingerprint
            and listener.owns_current_delivery(
                device_id, generation, sequence, delivery
            )
            and provenance.source is TransportSource.BLE
            and provenance.request_id == delivery
            and actual == digest
        )

    def _record_box_rc_ble_watermarks(
        self,
        device_id: str,
        observation: BleFrameObservation,
        result: BleProcessDisposition,
    ) -> None:
        """Keep confirmed BLE safety observations separate from telemetry priority."""
        if result is not BleProcessDisposition.CONFIRMED:
            return
        listener = getattr(self, "_ble_listener", None)
        if listener is None:
            return
        identity = observation.device_identity
        generation = observation.session_generation
        sequence = observation.notify_sequence
        delivery = observation.delivery_id
        fingerprint = observation.key_fingerprint
        stamp = observation.safety_received_at
        received = observation.received_at_monotonic
        key = self.device_bluetooth_key(device_id)
        if (
            identity is None  # ruff: ignore[too-many-boolean-expressions]
            or identity != self._box_safety_device_identity(device_id)
            or generation is None
            or sequence is None
            or delivery is None
            or fingerprint is None
            or fingerprint != observation.session_key_fingerprint
            or key is None
            or hashlib.sha256(key).digest() != fingerprint
            or stamp is None
            or received is None
            or not listener.owns_current_observation(device_id, observation)
        ):
            return
        window = self._transport_source_freshness_window()
        if not 0 <= (utc_now() - stamp).total_seconds() <= window or not (
            0 <= time.monotonic() - received <= window
        ):
            return
        payload = self._decode_ble_payload(device_id, observation)
        if not isinstance(payload, dict) or not _box_rc_safety_values(payload):
            return
        self._record_box_rc_source_watermarks(
            device_id,
            TransportSource.BLE,
            identity,
            stamp,
            payload,
            (generation, sequence, delivery, fingerprint),
        )
        self.async_update_listeners()

    def _record_ble_ground_identity(
        self,
        device_id: str,
        observation: BleFrameObservation,
        result: BleProcessDisposition,
    ) -> None:
        """Retain an accepted ground identity bound to its BLE session."""
        listener = getattr(self, "_ble_listener", None)
        owns = getattr(listener, "owns_current_observation", None)
        if not callable(owns) or not owns(device_id, observation):
            return
        state = getattr(self, "_ble_ground_identity", None)
        if state is None:
            state = self._ble_ground_identity = {}
        previous = state.get(device_id)
        if previous is not None and observation.received_at < previous.observed_at:
            return
        if observation.decode_error is not None:
            state.pop(device_id, None)
            return
        frame = observation.parsed or observation.rejected_frame
        invalid = (
            result is not BleProcessDisposition.CONFIRMED
            or observation.is_fragment
            or observation.protocol_status != 0
        )
        if (
            frame is not None
            and (frame.flags, frame.cmd) == (6, 3)
            and (invalid or frame.checksum_valid is not True)
        ):
            state.pop(device_id, None)
            return
        if (
            result is not BleProcessDisposition.CONFIRMED
            or frame is None
            or (frame.flags, frame.cmd) != (6, 3)
            or self.device_family(device_id) != "portable"
        ):
            return
        self._accept_ble_ground_identity(device_id, observation, frame)

    def _accept_ble_ground_identity(
        self,
        device_id: str,
        observation: BleFrameObservation,
        frame: BleBinaryFrame,
    ) -> None:
        """Bind a valid Ground payload to accepted property provenance."""
        state = self._ble_ground_identity
        previous = state.get(device_id)
        try:
            body = json.loads(frame.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            state.pop(device_id, None)
            return
        if not isinstance(body, dict) or "grdAccSn" not in body:
            return
        raw = body["grdAccSn"]
        serial = raw.strip() if isinstance(raw, str) else ""
        generation = observation.session_generation
        sequence = observation.notify_sequence
        fingerprint = observation.key_fingerprint
        delivery = observation.delivery_id
        props = (self.data or {}).get(device_id, {}).get(PAYLOAD_PROPERTIES, {})
        provenance = self._property_source_state.get(device_id, {}).get("grdAccSn")
        if (
            generation is None
            or sequence is None
            or fingerprint is None
            or delivery is None
        ):
            state.pop(device_id, None)
            return
        invalid_payload = not isinstance(props, dict) or props.get("grdAccSn") != raw
        invalid_provenance = (
            provenance is None
            or provenance.source is not TransportSource.BLE
            or provenance.family != "portable"
            or provenance.request_id != delivery
        )
        if not serial or invalid_payload or invalid_provenance:
            state.pop(device_id, None)
            return
        first_sequence = (
            previous.first_sequence
            if previous is not None
            and previous.serial == serial
            and previous.generation == generation
            and previous.fingerprint == fingerprint
            else sequence
        )
        state[device_id] = _BleGroundIdentity(
            serial,
            observation.received_at,
            generation,
            sequence,
            first_sequence,
            fingerprint,
            delivery,
        )
        marker = (self._ble_ota_pending or {}).get(device_id)
        if (
            isinstance(marker, dict)
            and marker.get("target_kind") == "ground_accessory"
            and marker.get("target_sn") == serial
            and isinstance(marker.get("source_session"), str)
            and marker["source_session"]
            != self._ground_ble_session_fingerprint(state[device_id])
        ):
            if (
                previous is None
                or previous.generation != generation
                or previous.first_sequence != first_sequence
                or previous.fingerprint != fingerprint
            ):
                self._ble_ground_recovery_triggers[device_id] = (
                    self._ble_ground_recovery_triggers.get(device_id, 0) + 1
                )
            self._schedule_background_once(
                f"ground_ota_recovery:{device_id}",
                partial(self._async_run_ground_ota_recovery, device_id),
                name=f"{DOMAIN}_ground_ota_recovery",
            )

    def _apply_ble_ota_event(
        self,
        device_id: str,
        observation: BleFrameObservation,
        frame: BleBinaryFrame,
        current: dict[str, Any],
    ) -> BleProcessDisposition:
        """Preserve an OTA event without treating it as a device property."""
        try:
            body = json.loads(frame.body.decode("utf-8")) if frame.body else {}
        except PAYLOAD_PARSE_ERRORS:
            return BleProcessDisposition.INVALID
        if not isinstance(body, dict):
            return BleProcessDisposition.INVALID
        firmware_type = body.get("f")
        event_key = (
            f"{frame.cmd}:{firmware_type if isinstance(firmware_type, str) else ""}"
        )
        times = getattr(self, "_ble_ota_events_observed_at", None)
        if times is None:
            self._ble_ota_events_observed_at = {}
            times = self._ble_ota_events_observed_at
        identity = (device_id, event_key)
        previous_time = times.get(identity)
        if previous_time is not None and observation.received_at < previous_time:
            return BleProcessDisposition.CONFIRMED
        events = current.get(PAYLOAD_BLE_OTA_EVENTS)
        events = dict(events) if isinstance(events, dict) else {}
        if (
            event_key in events
            and max(0.0, (utc_now() - observation.received_at).total_seconds())
            > self._transport_source_freshness_window()
        ):
            return BleProcessDisposition.CONFIRMED
        events[event_key] = {
            "action_id": frame.flags,
            "ble_msg_type": frame.cmd,
            "status": observation.protocol_status,
            "body": body,
            "received_at": observation.received_at.isoformat(),
            "session_generation": observation.session_generation,
            "notify_sequence": observation.notify_sequence,
        }
        updated = {**current, PAYLOAD_BLE_OTA_EVENTS: events}
        previous_data = getattr(self, "data", None)
        try:
            result = self._finalize_ble_observation_route(
                device_id, observation, current, updated, True
            )
        except BaseException:
            if getattr(self, "data", None) is not previous_data:
                times[identity] = observation.received_at
            raise
        if result is BleProcessDisposition.CONFIRMED:
            times[identity] = observation.received_at
        return result

    def _apply_ble_ota_version_list(
        self,
        device_id: str,
        observation: BleFrameObservation,
        current: dict[str, Any],
        versions: list[Any],
    ) -> BleProcessDisposition:
        """Commit one timestamp-ordered BLE OTA version list."""
        observed_times = getattr(self, "_ble_ota_versions_observed_at", None)
        if observed_times is None:
            self._ble_ota_versions_observed_at = {}
            observed_times = self._ble_ota_versions_observed_at
        last_observed = observed_times.get(device_id)
        if last_observed is not None and observation.received_at < last_observed:
            return BleProcessDisposition.CONFIRMED
        if (
            PAYLOAD_BLE_OTA_VERSIONS in current
            and max(0.0, (utc_now() - observation.received_at).total_seconds())
            > self._transport_source_freshness_window()
        ):
            return BleProcessDisposition.CONFIRMED
        updated = {**current, PAYLOAD_BLE_OTA_VERSIONS: versions}
        previous_data = getattr(self, "data", None)
        previous_identity = (
            self._ble_ota_versions_generation.get(device_id),
            self._ble_ota_versions_fingerprint.get(device_id),
        )

        def commit_report_identity() -> None:
            observed_times[device_id] = observation.received_at
            self._ble_ota_versions_generation[device_id] = (
                observation.session_generation
            )
            self._ble_ota_versions_fingerprint[device_id] = (
                observation.key_fingerprint
                if self.device_ble_profile(device_id) == "box_fixed"
                or (
                    observation.parsed is not None
                    and observation.parsed.checksum_valid is True
                )
                else None
            )
            self._ble_ota_versions_first_sequence[device_id] = (
                observation.first_notify_sequence
            )

        try:
            result = self._finalize_ble_observation_route(
                device_id,
                observation,
                current,
                updated,
                True,
                on_commit=commit_report_identity,
            )
        except BaseException:
            if getattr(self, "data", None) is not previous_data:
                commit_report_identity()
            raise
        if (
            result is BleProcessDisposition.CONFIRMED
            and updated == current
            and previous_identity
            != (
                self._ble_ota_versions_generation.get(device_id),
                self._ble_ota_versions_fingerprint.get(device_id),
            )
        ):
            self.async_update_listeners()
        return result

    async def _async_ingest_ble_observation_once_impl(  # ruff: ignore[complex-structure, too-many-locals, too-many-branches, too-many-return-statements, too-many-statements]
        self,
        device_id: str,
        observation: BleFrameObservation,
        *,
        on_stage: Callable[[], None] | None = None,
        on_commit: Callable[[], None] | None = None,
    ) -> BleProcessDisposition:
        """Decode and merge one requested or unsolicited BLE frame."""
        payload = self._decode_ble_payload(device_id, observation)
        blocked_session = observation.session_generation is not None and getattr(
            self, "_box_strategy_blocked_generation", {}
        ).get(device_id) == (
            getattr(self, "_ble_listener", None),
            observation.session_generation,
        )
        home_battery = self._ble_ota_battery.get(device_id)
        home_frame = observation.parsed or observation.rejected_frame
        if (
            not blocked_session  # ruff: ignore[too-many-boolean-expressions]
            and home_battery is not None
            and observation.session_generation == home_battery[1]
            and observation.received_at >= home_battery[0]
            and (
                observation.decode_error is not None
                or (
                    home_frame is not None
                    and (home_frame.flags, home_frame.cmd) in {(3011, 106), (6, 3)}
                    and (
                        observation.is_fragment
                        or payload is None
                        or observation.protocol_status != 0
                        or home_frame.checksum_valid is not True
                    )
                )
            )
        ):
            self._ble_ota_battery.pop(device_id, None)
        faults = getattr(self, "_ble_box_ota_faults", {})
        previous_fault = faults.get(device_id)
        fragment = observation.parsed
        if (
            not blocked_session  # ruff: ignore[too-many-boolean-expressions]
            and (
                observation.decode_error is not None
                or (
                    observation.is_fragment
                    and fragment is not None
                    and (fragment.flags, fragment.cmd) == (252, 9)
                )
                or (
                    fragment is not None
                    and (fragment.flags, fragment.cmd) == (252, 9)
                    and payload is None
                )
            )
            and previous_fault is not None
            and observation.session_generation == previous_fault[1]
            and observation.received_at >= previous_fault[0]
        ):
            faults[device_id] = (
                observation.received_at,
                previous_fault[1],
                observation.notify_sequence or previous_fault[2],
                False,
                observation.key_fingerprint,
            )
        parsed = observation.parsed
        event_frame = parsed or observation.rejected_frame
        cmd = event_frame.cmd if event_frame is not None else None
        recovered = self._ble_ota_recovered.get(device_id)
        invalid_version = (
            cmd == MQTT_CMD_GET_DEVICE_OTA_VERSION
            and not observation.is_fragment
            and payload is None
        )
        if (
            not blocked_session
            and recovered is not None
            and observation.session_generation == recovered[0]
            and (observation.decode_error is not None or invalid_version)
        ):
            self._ble_ota_recovered.pop(device_id, None)
        originals: dict[str, dict[str, Any]] | None = getattr(
            self, "_ble_original_observations", None
        )
        if originals is None:
            originals = {}
            self._ble_original_observations = originals
        original_body = None
        if event_frame is not None:
            with contextlib.suppress(UnicodeDecodeError, json.JSONDecodeError):
                original_body = json.loads(event_frame.body)
        original_frame = {
            "cmd": cmd,
            "flags": event_frame.flags if event_frame is not None else None,
            "frame_index": event_frame.frame_index if event_frame is not None else None,
            "chunk_count": event_frame.chunk_count if event_frame is not None else None,
            "body": original_body,
            "body_hex": event_frame.body.hex() if event_frame is not None else None,
            "raw_hex": observation.raw_bytes.hex(),
            "received_at": observation.received_at.isoformat(),
            "session_generation": observation.session_generation,
            "notify_sequence": observation.notify_sequence,
            "received_at_monotonic": time.monotonic(),
        }
        originals.setdefault(device_id, {})[str(cmd)] = original_frame
        self._schedule_payload_debug_event(
            lambda: {
                "kind": TransportSource.BLE.value,
                "topic": f"ble://{device_id}/cmd{cmd}",
                "device_id": device_id,
                "cmd": cmd,
                "flags": event_frame.flags if event_frame is not None else None,
                "frame_index": event_frame.frame_index
                if event_frame is not None
                else None,
                "chunk_count": event_frame.chunk_count
                if event_frame is not None
                else None,
                "is_fragment": observation.is_fragment,
                "first_notify_sequence": observation.first_notify_sequence,
                "body_size": len(event_frame.body) if event_frame is not None else None,
                "payload": payload,
                "payload_chart_series_debug": chart_series_debug(payload),
                "decode_error": observation.decode_error,
                "raw_hex": observation.raw_bytes.hex(),
                "received_at": observation.received_at.isoformat(),
                "session_generation": observation.session_generation,
                "notify_sequence": observation.notify_sequence,
            },
        )
        if blocked_session:
            return BleProcessDisposition.CONFIRMED
        if (
            event_frame is not None
            and event_frame.cmd in {101, 102, 103, 104}
            and not observation.is_fragment
        ):
            self._ble_ota_recovered.pop(device_id, None)
            current_device = self._ble_partial_update_base(device_id)
            if not isinstance(current_device, dict):
                return BleProcessDisposition.RETRY
            return self._apply_ble_ota_event(
                device_id, observation, event_frame, current_device
            )
        if payload is None:
            return BleProcessDisposition.INVALID
        assert parsed is not None
        assert cmd is not None
        action_id = parsed.flags
        current_device = self._ble_partial_update_base(device_id)
        if not isinstance(current_device, dict):
            # The GATT session is already bound to a cached identity. A frame
            # for an identity absent from both cache and committed state is
            # rejected rather than attributed to another device.
            return BleProcessDisposition.RETRY
        updated = dict(current_device)
        if cmd == MQTT_CMD_GET_DEVICE_OTA_VERSION and isinstance(
            payload.get(PAYLOAD_BLE_OTA_VERSIONS), list
        ):
            return self._apply_ble_ota_version_list(
                device_id,
                observation,
                current_device,
                payload[PAYLOAD_BLE_OTA_VERSIONS],
            )
        box_strategy_touched = False
        box_plan_key = (
            {(12, 15): "cds", (18, 21): "cep"}.get((action_id, cmd))
            if self.device_ble_profile(device_id) == "box_fixed"
            else {
                (26, 15): "cds",
                (27, 16): "cds",
                (28, 17): "cds",
                (29, 18): "cds",
                (30, 21): "cep",
            }.get((action_id, cmd))
            if self.device_ble_profile(device_id) in {"rc4", "aes_fixed", "aes_random"}
            else None
        )
        if box_plan_key is not None and box_plan_key in payload:
            value = payload[box_plan_key]
            existing = current_device.get(PAYLOAD_ELECTRICITY_STRATEGY)
            if (
                isinstance(existing, Mapping)
                and box_plan_key in existing
                and max(0.0, (utc_now() - observation.received_at).total_seconds())
                > self._transport_source_freshness_window()
            ):
                payload = {
                    key: item for key, item in payload.items() if key != box_plan_key
                }
                if not payload:
                    return BleProcessDisposition.CONFIRMED
                box_plan_key = None
            if (box_plan_key == "cds" and isinstance(value, list)) or (
                box_plan_key == "cep" and (isinstance(value, dict) or value is None)
            ):
                selected = self._merge_partial_device_update(
                    device_id,
                    current_device,
                    {PAYLOAD_ELECTRICITY_STRATEGY: {box_plan_key: value}},
                    source=TransportSource.BLE,
                    observed_at=observation.received_at,
                    request_id=observation.delivery_id or f"ble:{id(observation)}",
                )
                updated[PAYLOAD_ELECTRICITY_STRATEGY] = selected[
                    PAYLOAD_ELECTRICITY_STRATEGY
                ]
                box_strategy_touched = True
                payload = {
                    key: item for key, item in payload.items() if key != box_plan_key
                }
        portable_property = (
            action_id == ACTION_ID_PORTABLE_READ_DEVICE_INFO
            and cmd
            == PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[ACTION_ID_PORTABLE_READ_DEVICE_INFO]
            and self.device_ble_profile(device_id) in {"rc4", "aes_fixed", "aes_random"}
        )
        box_property = (action_id, cmd) == (
            ACTION_ID_BOX_READ_DEVICE_INFO,
            BLE_CMD_BOX_READ_DEVICE_INFO,
        ) and self.device_ble_profile(device_id) == "box_fixed"
        if portable_property or box_property:
            power_fields = (
                (FIELD_IP, FIELD_BLE_BOX_MAINS_POWER, FIELD_BLE_PORTABLE_INPUT_POWER),
                (FIELD_OP, FIELD_BLE_BOX_HOUSE_POWER, FIELD_BLE_PORTABLE_OUTPUT_POWER),
            )
            for source_field, box_field, portable_field in power_fields:
                value = payload.get(source_field)
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and safe_float(value) is not None
                ):
                    payload = {
                        **payload,
                        (box_field if box_property else portable_field): value,
                    }
        route_update = (
            {}
            if not payload and box_strategy_touched
            else None
            if portable_property
            else self._ble_config_payload_update(
                device_id,
                current_device,
                payload,
                cmd,
                action_id,
                received_at_monotonic=observation.received_at_monotonic,
            )
        )
        weather_rows = None
        if route_update is None:
            route_update = self._ble_app_payload_update(payload, cmd, action_id)
        if route_update is None:
            touched = self._apply_ble_subdevice_or_properties(
                device_id,
                observation,
                current_device,
                updated,
                payload,
                original_frame=original_frame,
            )
        else:
            extra_strategy = route_update.get(PAYLOAD_ELECTRICITY_STRATEGY)
            selected_strategy = updated.get(PAYLOAD_ELECTRICITY_STRATEGY)
            if (
                box_strategy_touched
                and isinstance(extra_strategy, dict)
                and isinstance(selected_strategy, dict)
            ):
                route_update[PAYLOAD_ELECTRICITY_STRATEGY] = merge_present_dict_values(
                    selected_strategy,
                    {
                        key: value
                        for key, value in extra_strategy.items()
                        if key not in {"cds", "cep"}
                    },
                )
            weather = route_update.get(PAYLOAD_WEATHER_PLAN)
            if (
                self.device_family(device_id) in {"home", "portable"}
                and isinstance(weather, dict)
                and is_weather_plan_event(weather)
            ):
                state = self._prepare_weather_plan_event(
                    device_id,
                    weather,
                    TransportSource.BLE,
                    observed_at=observation.received_at,
                    received_at_monotonic=observation.received_at_monotonic,
                    request_id=observation.delivery_id,
                )
                if state is None:
                    previous = current_device.get(PAYLOAD_WEATHER_PLAN)
                    if isinstance(previous, dict):
                        route_update[PAYLOAD_WEATHER_PLAN] = previous
                    else:
                        route_update.pop(PAYLOAD_WEATHER_PLAN, None)
                else:
                    route_update[PAYLOAD_WEATHER_PLAN] = self._weather_plan_payload(
                        device_id, weather, state
                    )
                    weather_rows = state.rows
                    on_commit = self._weather_plan_commit_callback(
                        device_id, state, on_commit
                    )
            updated.update(route_update)
            touched = True

        if (
            not box_property
            or observation.protocol_status != 0
            or observation.session_generation is None
            or observation.notify_sequence is None
        ):
            result = self._finalize_ble_observation_route(
                device_id,
                observation,
                current_device,
                updated,
                touched or box_strategy_touched,
                on_stage=on_stage,
                on_commit=on_commit,
                weather_plan_rows=weather_rows,
            )
            ota_profile = self.device_ble_profile(device_id)
            if (
                result is BleProcessDisposition.CONFIRMED  # ruff: ignore[too-many-boolean-expressions]
                and (
                    (ota_profile is None and (action_id, cmd) == (3011, 106))
                    or (
                        ota_profile in {"rc4", "aes_fixed", "aes_random"}
                        and (action_id, cmd) == (6, 3)
                    )
                )
                and observation.protocol_status == 0
                and parsed.checksum_valid is True
                and observation.session_generation is not None
                and observation.notify_sequence is not None
                and isinstance(original_body, dict)
            ):
                previous = self._ble_ota_battery.get(device_id)
                if previous is None or observation.received_at >= previous[0]:
                    value = original_body.get(
                        FIELD_BAT_SOC if ota_profile is None else FIELD_RB
                    )
                    self._ble_ota_battery[device_id] = (
                        observation.received_at,
                        observation.session_generation,
                        observation.notify_sequence,
                        isinstance(value, int)
                        and not isinstance(value, bool)
                        and _HOME_OTA_MIN_SOC < value <= _HOME_OTA_MAX_SOC,
                        observation.key_fingerprint,
                        ota_profile,
                    )
            return result
        previous_data = self.data
        previous_fault = self._ble_box_ota_faults.get(device_id)
        fault_updated = False
        fault = original_body.get("fz") if isinstance(original_body, dict) else None
        gs = fault.get("gs") if isinstance(fault, dict) else None
        if previous_fault is None or observation.received_at >= previous_fault[0]:
            self._ble_box_ota_faults[device_id] = (
                observation.received_at,
                observation.session_generation,
                observation.notify_sequence,
                isinstance(gs, int) and not isinstance(gs, bool) and gs == 0,
                observation.key_fingerprint,
            )
            fault_updated = True
        try:
            result = self._finalize_ble_observation_route(
                device_id,
                observation,
                current_device,
                updated,
                touched or box_strategy_touched,
                on_stage=on_stage,
                on_commit=on_commit,
            )
        except BaseException:
            if fault_updated and self.data is previous_data:
                if previous_fault is None:
                    self._ble_box_ota_faults.pop(device_id, None)
                else:
                    self._ble_box_ota_faults[device_id] = previous_fault
            raise
        if (
            fault_updated
            and result is not BleProcessDisposition.CONFIRMED
            and self.data is previous_data
        ):
            if previous_fault is None:
                self._ble_box_ota_faults.pop(device_id, None)
            else:
                self._ble_box_ota_faults[device_id] = previous_fault
        return result

    def _ble_ota_model_evidence(self, device_id: str) -> tuple[Any, ...]:
        entry = self._device_index.get(device_id) or {}
        payload = (self.data or {}).get(device_id) or {}
        return tuple(
            copy.deepcopy(source.get(FIELD_MODEL_CODE))
            if isinstance(source, Mapping)
            else None
            for source in (
                entry.get(PAYLOAD_DEVICE_META),
                payload.get(PAYLOAD_DEVICE),
                payload.get(PAYLOAD_DISCOVERY),
                payload.get(PAYLOAD_DEVICE_META),
            )
        )

    def device_model_code(self, device_id: str) -> int | None:
        """Return the unambiguous model code across device sources."""
        entry = self._device_index.get(device_id) or {}
        payload = (self.data or {}).get(device_id) or {}
        codes: set[int] = set()
        for source in (
            entry.get(PAYLOAD_DEVICE_META),
            payload.get(PAYLOAD_DEVICE),
            payload.get(PAYLOAD_DISCOVERY),
            payload.get(PAYLOAD_DEVICE_META),
        ):
            if not isinstance(source, Mapping):
                continue
            value = source.get(FIELD_MODEL_CODE)
            if value is None or (isinstance(value, str) and not value):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                return None
            try:
                codes.add(int(value))
            except ValueError:
                return None
        codes.discard(0)
        return next(iter(codes)) if len(codes) == 1 else None

    def _box_safety_device_identity(self, device_id: str) -> tuple[int, str] | None:
        model = self.device_model_code(device_id)
        metadata = (self._device_index.get(device_id) or {}).get(PAYLOAD_DEVICE_META)
        if model not in {2001, 2002} or not isinstance(metadata, Mapping):
            return None
        serials: set[str] = set()
        for field in (FIELD_DEVICE_SN, FIELD_DEV_SN):
            value = metadata.get(field)
            if value is None or (isinstance(value, str) and not value):
                continue
            if not isinstance(value, str) or value != value.strip():
                return None
            serials.add(value)
        if len(serials) != 1:
            return None
        return model, serials.pop()

    def _strategy_device_identity(self, device_id: str) -> tuple[int, str] | None:
        """Resolve the device identity used to bind Box and Portable plans."""
        if self.device_family(device_id) == "box":
            return self._box_safety_device_identity(device_id)
        if self.device_family(device_id) != "portable":
            return None
        model = self.device_model_code(device_id)
        metadata = (self._device_index.get(device_id) or {}).get(PAYLOAD_DEVICE_META)
        if model is None or not isinstance(metadata, Mapping):
            return None
        serials: set[str] = set()
        for field in (FIELD_DEVICE_SN, FIELD_DEV_SN):
            value = metadata.get(field)
            if value is None or (isinstance(value, str) and not value):
                continue
            if not isinstance(value, str) or value != value.strip():
                return None
            serials.add(value)
        if len(serials) != 1:
            return None
        return model, serials.pop()

    def capture_box_safety_ingress(
        self, source: TransportSource, topic: str
    ) -> BoxSafetyIngress | None:
        """Capture immutable Box identities at an accepted MQTT FIFO ingress."""
        if getattr(self, "_shutdown_started", False) or source not in {
            TransportSource.CLOUD_MQTT,
            TransportSource.LOCAL_MQTT,
        }:
            return None
        identities = tuple(
            (device_id, identity[0], identity[1])
            for device_id in self._device_index
            if (identity := self._strategy_device_identity(device_id)) is not None
        )
        if not identities:
            return None
        for device_id, model, serial in identities:
            self._box_strategy_identity_seen.setdefault(device_id, (model, serial))
        return BoxSafetyIngress(
            source=source,
            topic=topic,
            identities=identities,
            received_at=utc_now(),
            received_at_monotonic=time.monotonic(),
            delivery_id=f"box-safety:{random.getrandbits(128):032x}",
            identity_epochs=tuple(
                (device_id, self._box_strategy_epochs.get(device_id, 0))
                for device_id, _, _ in identities
            ),
        )

    def capture_cloud_box_safety_ingress(self, topic: str) -> BoxSafetyIngress | None:
        """Capture Box identities for a cloud MQTT message."""
        return self.capture_box_safety_ingress(TransportSource.CLOUD_MQTT, topic)

    def capture_local_box_safety_ingress(self, topic: str) -> BoxSafetyIngress | None:
        """Capture Box identities for a local MQTT message."""
        return self.capture_box_safety_ingress(TransportSource.LOCAL_MQTT, topic)

    async def async_handle_cloud_box_safety_ingress(
        self, topic: str, payload: dict[str, Any], receipt: BoxSafetyIngress
    ) -> str | None:
        """Handle a cloud MQTT message with its captured Box identities."""
        return await self.async_handle_mqtt_message(
            topic, payload, _box_safety_ingress=receipt
        )

    async def async_handle_local_box_safety_ingress(
        self,
        topic: str,
        payload: dict[str, Any] | None,
        raw: bytes,
        receipt: BoxSafetyIngress,
    ) -> bool | Literal["broker_metadata"]:
        """Handle a local MQTT message with its captured Box identities."""
        return await self.async_handle_local_mqtt_message(
            topic, payload, raw, _box_safety_ingress=receipt
        )

    def device_ups_region(self, device_id: str) -> str | None:
        """Return the App's per-device bind-list region when it is unambiguous."""
        record = self._device_index.get(device_id) or {}
        metadata = record.get(PAYLOAD_DEVICE_META)
        if not isinstance(metadata, Mapping):
            return None
        source = metadata.get(PAYLOAD_DISCOVERY_SOURCE)
        if not isinstance(source, str) or source not in {
            DISCOVERY_SOURCE_LEGACY_BIND_LIST,
            DISCOVERY_SOURCE_SYSTEM_LIST,
        }:
            return None
        evidence = record.get(_PORTABLE_UPS_BIND_EVIDENCE)
        identity = _portable_ups_discovery_identity(metadata)
        if not isinstance(evidence, Mapping) or identity is None:
            return None
        region = evidence.get("region")
        if (
            not isinstance(region, str)
            or region not in {"US", "JP"}
            or (evidence.get("model"), evidence.get(FIELD_DEVICE_SN)) != identity
            or not _portable_ups_region_matches(metadata.get("region"), region)
        ):
            return None
        return region

    def device_ups_identity(self, device_id: str) -> tuple[str, str] | None:
        """Bind the App device region to its own unambiguous serial."""
        record = self._device_index.get(device_id) or {}
        metadata = record.get(PAYLOAD_DEVICE_META)
        if not isinstance(metadata, Mapping):
            return None
        discovery_identity = _portable_ups_discovery_identity(metadata)
        region = self.device_ups_region(device_id)
        if (
            region is None
            or discovery_identity is None
            or self.device_model_code(device_id) != discovery_identity[0]
            or self._resolve_device_sn(device_id) != discovery_identity[1]
        ):
            return None
        return region, discovery_identity[1]

    def device_ups_ble_key(self, device_id: str) -> bytes | None:
        """Require a device-owned BLE key without gating independent MQTT."""
        record = self._device_index.get(device_id) or {}
        evidence = record.get(_PORTABLE_UPS_BIND_EVIDENCE)
        if (
            not isinstance(evidence, Mapping)
            or self.device_ups_identity(device_id) is None
        ):
            return None
        fingerprint = evidence.get(FIELD_BLUETOOTH_KEY)
        key = self.device_bluetooth_key(device_id)
        if not isinstance(fingerprint, str) or key is None:
            return None
        return key if hashlib.sha256(key).hexdigest() == fingerprint else None

    def _home_ble_ota_model_is_known(self, device_id: str) -> bool:
        return self.device_model_code(device_id) in {3001, 3002, 3003, 3004}

    def _ble_ota_profile_matches(self, device_id: str, profile: str | None) -> bool:
        if self.device_ble_profile(device_id) != profile:
            return False
        if profile is None:
            return self._home_ble_ota_model_is_known(device_id)
        if profile == "box_fixed":
            return self.device_model_code(device_id) in {2001, 2002}
        return portable_ble_profile(self.device_model_code(device_id)) == profile

    def home_ble_ota_allowed(self, device_id: str, generation: int) -> bool:
        """Require a fresh main-battery state from the current Home BLE session."""
        if not self._home_ble_ota_model_is_known(device_id):
            return False
        return self._ble_ota_battery_allowed(device_id, generation, None)

    def portable_ble_ota_allowed(self, device_id: str, generation: int) -> bool:
        """Require the current Portable model and its own fresh BLE battery state."""
        profile = self.device_ble_profile(device_id)
        if (
            profile not in {"rc4", "aes_fixed", "aes_random"}
            or portable_ble_profile(self.device_model_code(device_id)) != profile
        ):
            return False
        return self._ble_ota_battery_allowed(device_id, generation, profile)

    def _ble_ota_battery_allowed(
        self, device_id: str, generation: int, profile: str | None
    ) -> bool:
        battery = self._ble_ota_battery.get(device_id)
        listener = self._ble_listener
        if (
            battery is None  # ruff: ignore[too-many-boolean-expressions]
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or listener is None
            or not self._ble_writes_enabled()
            or self.device_ble_profile(device_id) != profile
            or not listener.session_is_current(device_id, generation, battery[2])
        ):
            return False
        (
            observed_at,
            observed_generation,
            _sequence,
            allowed,
            fingerprint,
            source_profile,
        ) = battery
        key = self.device_bluetooth_key(device_id)
        age = (utc_now() - observed_at).total_seconds()
        return (
            generation == observed_generation
            and profile == source_profile
            and allowed
            and fingerprint is not None
            and key is not None
            and hashlib.sha256(key).digest() == fingerprint
            and 0 <= age <= self._transport_source_freshness_window()
        )

    def box_ble_ota_allowed(self, device_id: str, generation: int) -> bool:
        """Require a fresh fault-free Box status from the current BLE session."""
        fault = self._ble_box_ota_faults.get(device_id)
        listener = self._ble_listener
        if (
            fault is None  # ruff: ignore[too-many-boolean-expressions]
            or isinstance(generation, bool)
            or not isinstance(generation, int)
            or listener is None
            or not self._ble_writes_enabled()
            or self.device_ble_profile(device_id) != "box_fixed"
            or not listener.session_is_current(device_id, generation, fault[2])
        ):
            return False
        observed_at, observed_generation, _sequence, allowed, fingerprint = fault
        key = self.device_bluetooth_key(device_id)
        age = (utc_now() - observed_at).total_seconds()
        return (
            generation == observed_generation
            and allowed
            and fingerprint is not None
            and key is not None
            and hashlib.sha256(key).digest() == fingerprint
            and 0 <= age <= self._transport_source_freshness_window()
        )

    def box_ble_ota_target_allowed(
        self, device_id: str, generation: int, target_sn: str
    ) -> bool:
        """Require the selected serial to belong to the current Box session."""
        return self.box_ble_ota_allowed(
            device_id, generation
        ) and self._ble_ota_target_is_owned(device_id, generation, target_sn)

    def ground_ble_identity(
        self, device_id: str, *, require_connected: bool = True
    ) -> _BleGroundIdentity | None:
        """Resolve a current Ground identity without assuming a version report."""
        state: dict[str, _BleGroundIdentity] = getattr(self, "_ble_ground_identity", {})
        identity = state.get(device_id)
        listener = self._ble_listener
        key = self.device_bluetooth_key(device_id)
        profile = self.device_ble_profile(device_id)
        if (
            identity is None
            or listener is None
            or key is None
            or profile not in {"rc4", "aes_fixed", "aes_random"}
            or portable_ble_profile(self.device_model_code(device_id)) != profile
        ):
            return None
        device = (self.data or {}).get(device_id, {})
        props = device.get(PAYLOAD_PROPERTIES)
        provenance = self._property_source_state.get(device_id, {}).get("grdAccSn")
        fingerprint = hashlib.sha256(key).digest()
        serial = identity.serial
        report_sequence = self._ble_ota_versions_first_sequence.get(device_id)
        through_sequence = (
            max(identity.sequence, report_sequence)
            if self._ble_ota_versions_generation.get(device_id) == identity.generation
            and report_sequence is not None
            else identity.sequence
        )
        if (
            not isinstance(props, dict)  # ruff: ignore[too-many-boolean-expressions]
            or not isinstance(props.get("grdAccSn"), str)
            or props["grdAccSn"].strip() != serial
            or provenance is None
            or provenance.source is not TransportSource.BLE
            or provenance.family != "portable"
            or provenance.request_id != identity.delivery_id
            or identity.fingerprint != fingerprint
            or not listener.owns_delivery(
                device_id, identity.generation, identity.sequence, identity.delivery_id
            )
            or (
                require_connected
                and not listener.owns_current_delivery(
                    device_id,
                    identity.generation,
                    identity.sequence,
                    identity.delivery_id,
                )
            )
            or not 0
            <= (utc_now() - identity.observed_at).total_seconds()
            <= self._transport_source_freshness_window()
            or (
                require_connected
                and not listener.session_is_current(
                    device_id, identity.generation, through_sequence
                )
            )
            or self._resolve_device_sn(device_id) == serial
            or any(
                self.battery_pack_identity_serial(device_id, index) == serial
                for index in range(1, 6)
            )
        ):
            return None
        return identity

    def ground_ble_ota_target(self, device_id: str) -> str | None:
        """Resolve Ground OTA candidates from current identity and version proof."""
        identity = self.ground_ble_identity(device_id)
        if identity is None:
            return None
        versions = (self.data or {}).get(device_id, {}).get(PAYLOAD_BLE_OTA_VERSIONS)
        report_time = self._ble_ota_versions_observed_at.get(device_id)
        report_sequence = self._ble_ota_versions_first_sequence.get(device_id)
        if (
            report_time is None
            or not 0
            <= (utc_now() - report_time).total_seconds()
            <= self._transport_source_freshness_window()
        ):
            return None
        if (
            self._ble_ota_versions_generation.get(device_id) != identity.generation
            or self._ble_ota_versions_fingerprint.get(device_id) != identity.fingerprint
            or report_sequence is None
            or report_sequence <= identity.first_sequence
            or not self._ble_ota_versions_idle_for_target(versions, identity.serial)
        ):
            return None
        return identity.serial

    def _ble_pending_target_is_owned(
        self, device_id: str, generation: int, target_sn: str, pending: dict[str, str]
    ) -> bool:
        if pending.get("target_kind") == "ground_accessory":
            identity = self.ground_ble_identity(device_id)
            return bool(
                identity is not None
                and identity.generation == generation
                and self.ground_ble_ota_target(device_id) == target_sn
            )
        if pending.get("target_kind") not in {None, "device"}:
            return False
        return self._ble_ota_target_is_owned(device_id, generation, target_sn)

    @staticmethod
    def _ground_ble_session_fingerprint(identity: _BleGroundIdentity) -> str:
        session = identity.delivery_id.rsplit(":", 1)[0]
        return hashlib.sha256(session.encode("utf-8", "surrogatepass")).hexdigest()

    def _ble_ota_report_matches_target(
        self,
        device_id: str,
        generation: int,
        target_sn: str,
        *,
        allow_started: bool = False,
    ) -> bool:
        """Verify one target against the current BLE report and session."""
        listener = self._ble_listener
        observed_at = self._ble_ota_versions_observed_at.get(device_id)
        sequence = self._ble_ota_versions_first_sequence.get(device_id)
        key = self.device_bluetooth_key(device_id)
        if (
            not target_sn  # ruff: ignore[too-many-boolean-expressions]
            or listener is None
            or listener.current_session_generation(device_id) != generation
            or observed_at is None
            or sequence is None
            or key is None
            or self._ble_ota_versions_generation.get(device_id) != generation
            or self._ble_ota_versions_fingerprint.get(device_id)
            != hashlib.sha256(key).digest()
        ):
            return False
        started = (
            allow_started and listener.box_ota_started(device_id, generation) is True
        )
        if not started and (
            not 0
            <= (utc_now() - observed_at).total_seconds()
            <= self._transport_source_freshness_window()
            or not listener.session_is_current(device_id, generation, sequence)
        ):
            return False
        versions = (self.data or {}).get(device_id, {}).get(PAYLOAD_BLE_OTA_VERSIONS)
        if not isinstance(versions, list):
            return False
        serials = [
            serial
            for version in versions
            if isinstance(version, dict)
            and isinstance(serial := version.get(FIELD_DEVICE_SN), str)
            and serial
        ]
        if serials.count(target_sn) != 1:
            return False
        target_key = stable_subdevice_key("battery_pack", target_sn, 1)
        return not any(
            serial != target_sn
            and stable_subdevice_key("battery_pack", serial, 1) == target_key
            for serial in serials
        )

    def _ble_ota_target_is_owned(
        self, device_id: str, generation: int, target_sn: str
    ) -> bool:
        """Require BLE-owned target evidence rather than another transport."""
        return self._ble_ota_report_matches_target(
            device_id, generation, target_sn, allow_started=True
        )

    def _schedule_ble_reassembly_retry(
        self, device_id: str, cmd: int, flags: int
    ) -> None:
        """Re-query a documented BLE read after an unrecoverable chunk gap.

        These are read-only App commands, never setters or cross-layer fallback.
        No missing fragment is synthesized or joined to a different response.
        """
        pair = (cmd, flags)
        if (
            pair not in {(106, 3011), (120, 3019), (110, 3031)}
            or self._shutdown_started
            or getattr(self, "_ble_listener", None) is None
            or self.device_family(device_id) != "home"
        ):
            return
        key = (device_id, cmd, flags)
        retry_after = getattr(self, "_ble_reassembly_retry_after", None)
        if retry_after is None:
            retry_after = {}
            self._ble_reassembly_retry_after = retry_after
        now = time.monotonic()
        if now < retry_after.get(key, 0.0):
            return
        retry_after[key] = now + 60.0
        sink_token = self._ble_sink_token

        async def retry() -> None:
            # Avoid competing with the fragments of the just-started query.
            await asyncio.sleep(1.0)
            if (
                self._shutdown_started
                or self._ble_sink_token is not sink_token
                or getattr(self, "_ble_listener", None) is None
                or self.device_family(device_id) != "home"
            ):
                return
            listener = self._ble_listener
            sessions = getattr(listener, "_sessions", None)
            if isinstance(sessions, dict):
                # Incomplete data from a closed GATT session requires a fresh
                # read after reconnect; never send into the disconnected link.
                for attempt in range(13):
                    session = sessions.get(device_id)
                    if (
                        session is not None
                        and session.active
                        and session.notify_started
                        and bool(getattr(session.client, "is_connected", False))
                    ):
                        break
                    if attempt == 12:  # ruff: ignore[magic-value-comparison]
                        _LOGGER.warning(
                            "BLE read recovery for cmd %d has no reconnected session",
                            cmd,
                        )
                        return
                    await asyncio.sleep(5.0)
                    if (
                        self._shutdown_started
                        or self._ble_sink_token is not sink_token
                        or self._ble_listener is not listener
                        or self.device_family(device_id) != "home"
                    ):
                        return
            try:  # ruff: ignore[too-many-statements-in-try-clause]
                if pair == (106, 3011):
                    await self.async_query_device_info(
                        device_id,
                        ensure_mqtt=False,
                        allow_ble=True,
                        allow_cloud=False,
                        allow_local_mqtt=False,
                        include_http=False,
                    )
                elif pair == (120, 3019):
                    await self.async_query_system_info(
                        device_id,
                        ensure_mqtt=False,
                        allow_ble=True,
                        allow_cloud=False,
                        allow_local_mqtt=False,
                        include_http=False,
                    )
                else:
                    await self._async_publish_command_ble_first(
                        device_id,
                        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
                        action_id=ACTION_ID_SUBDEVICE_3031,
                        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
                        body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_CT},
                        ensure_mqtt=False,
                        allow_ble=True,
                        allow_cloud=False,
                        allow_local_mqtt=False,
                    )
            except Exception as err:  # ruff: ignore[blind-except] -- retry must not interfere with other layers
                _LOGGER.warning(
                    "BLE incomplete read retry failed for cmd %d: %s",
                    cmd,
                    type(err).__name__,
                )

        self._schedule_background_once(
            f"ble_reassembly_retry_{device_id}_{cmd}_{flags}",
            retry,
            name=f"{DOMAIN}_ble_reassembly_retry_{cmd}_{flags}",
        )

    async def async_start_ble_transport(self) -> None:  # ruff: ignore[too-many-return-statements, too-many-statements]  # ruff: ignore[too-many-branches]
        """Start the optional BLE listener if the config-entry option is set.

        Safe to call repeatedly; only the first call attaches a listener.
        Failures are logged at WARNING and don't propagate — BLE is an
        opt-in diagnostic channel and must not break cloud setup.
        """
        async with self._ble_start_lock:
            if self._shutdown_started:
                return
            if not config_entry_bool_option(
                self.entry,
                CONF_ENABLE_BLE_TRANSPORT,
                DEFAULT_ENABLE_BLE_TRANSPORT,
            ):
                return

            if self._ble_ota_pending is None:
                try:
                    coordinator_mod = sys.modules.get(
                        "custom_components.jackery_solarvault.coordinator"
                    )
                    load_pending = getattr(
                        coordinator_mod, "async_load_pending", async_load_pending
                    )
                    self._ble_ota_pending = await load_pending(
                        self.hass, self.entry.entry_id
                    )
                    self._ble_ota_recovered.clear()
                    self._ble_ota_recovery_retry_at.clear()
                except (OSError, ValueError) as err:
                    _LOGGER.warning(
                        "Jackery BLE OTA pending state could not be loaded: %s", err
                    )
                    self._schedule_ble_start_retry()
                    return

            if self._ble_listener is not None:
                return

            sink_token = object()

            async def _sink(
                device_id: str,
                observation: BleFrameObservation,
            ) -> BleProcessDisposition:
                """Route every requested or unsolicited frame through shared ingest."""
                if self._ble_sink_token is not sink_token:
                    return BleProcessDisposition.INVALID
                return await self._async_ingest_ble_observation(device_id, observation)

            def _on_disconnect(device_id: str, generation: int) -> None:
                def invalidate() -> None:
                    if self._ble_sink_token is not sink_token:
                        return
                    receipt = self._box_wifi_scan_receipts.get(device_id)
                    if receipt is not None and receipt[1] == generation:
                        self._box_wifi_scan_receipts.pop(device_id, None)
                        self.async_update_listeners()

                self.hass.loop.call_soon_threadsafe(invalidate)

            try:
                ble_transport_module = await self.hass.async_add_executor_job(
                    importlib.import_module,
                    f"{__package__}.ble_transport",
                )
                listener_class = cast(  # ty: ignore[redundant-cast]
                    "Any", ble_transport_module
                ).JackeryBleListener
            except (AttributeError, ImportError) as err:
                _LOGGER.warning(
                    "Jackery BLE transport is unavailable; retrying independently: %s",
                    err,
                )
                self._schedule_ble_start_retry()
                return

            # The executor import is a suspension point.  Shutdown raises its
            # fence before waiting for this same start lock, so do not create or
            # start a transport after that fence.
            if bool(getattr(self, "_shutdown_started", False)):
                return

            self._ble_sink_token = sink_token
            for name in (
                "_ble_ota_battery",
                "_ble_box_ota_faults",
                "_ble_ground_identity",
                "_ble_ota_versions_observed_at",
                "_ble_ota_versions_generation",
                "_ble_ota_versions_fingerprint",
                "_ble_ota_versions_first_sequence",
                "_ble_ota_events_observed_at",
            ):
                if (evidence := getattr(self, name, None)) is not None:
                    evidence.clear()

            listener = listener_class(
                self.hass,
                _sink,
                key_resolver=self.device_bluetooth_key,
                ble_address_resolver=self._ble_address_for_device,
                connect_backoff_remaining=self._ble_connect_backoff_remaining,
                connect_backoff_note_failure=self._ble_note_connect_failure,
                connect_backoff_note_success=self._ble_note_connect_success,
                serial_resolver=self.device_id_for_ble_serial,
                device_identity_resolver=self._capture_box_ble_identity,
                profile_resolver=self.device_ble_profile,
                ota_pending_resolver=self._ble_ota_pending_blocked,
                disconnect_callback=_on_disconnect,
                reassembly_gap_callback=self._schedule_ble_reassembly_retry,
                config_entry=self.entry,
            )
            try:
                await listener.async_start(list(self._device_index.keys()))
            except BACKGROUND_TASK_ERRORS as err:
                cleanup_failed = False
                try:
                    await listener.async_stop()
                except Exception as stop_err:  # ruff: ignore[blind-except]
                    cleanup_failed = True
                    _LOGGER.warning(
                        "Jackery: BLE listener stop after start failure "
                        "also failed: %s",
                        stop_err,
                    )
                _LOGGER.warning(
                    "Jackery BLE listener failed to start; retrying independently: %s",
                    err,
                )
                if cleanup_failed:
                    # A partial start may already own callbacks, clients or
                    # accepted FIFO work. Retain that exact object so bounded
                    # Layer-5 cleanup retries it before any replacement exists.
                    self._ble_listener = listener
                else:
                    self._release_failed_ble_sink(sink_token)
                return
            if bool(getattr(self, "_shutdown_started", False)):
                # Startup crossed the shutdown fence while suspended. Publish
                # the local listener only into the coordinator's stop ownership;
                # shutdown is waiting on this lock and will drain it through the
                # bounded, retryable Layer-5 stop path before reporting success.
                self._ble_listener = listener
                return
            self._ble_listener = listener
            _LOGGER.info(
                "Jackery BLE listener attached for %d device(s)",
                len(self._device_index),
            )

    def _release_failed_ble_sink(self, sink_token: object) -> None:
        """Fence a cleaned-up listener before scheduling a new attempt."""
        if self._ble_sink_token is sink_token:
            self._ble_sink_token = None
        if not self._shutdown_started:
            self._schedule_ble_start_retry()

    def _schedule_ble_start_retry(self) -> asyncio.Task[Any] | None:
        """Keep retrying BLE listener startup without involving other transports."""

        async def _retry() -> None:
            delay = BLE_CONNECT_BACKOFF_INITIAL_SEC
            while (
                not self._shutdown_started
                and self._ble_listener is None
                and config_entry_bool_option(
                    self.entry,
                    CONF_ENABLE_BLE_TRANSPORT,
                    DEFAULT_ENABLE_BLE_TRANSPORT,
                )
            ):
                await asyncio.sleep(delay)
                await self.async_start_ble_transport()
                delay = min(delay * 2, BLE_CONNECT_BACKOFF_MAX_SEC)

        return self._schedule_background_once(
            "ble_start_retry",
            _retry,
            name=f"{DOMAIN}_ble_start_retry",
        )

    async def async_reconcile_ble_transport(self) -> None:
        """Restart only BLE after its option changes, leaving HTTP untouched."""
        if self._shutdown_started:
            return
        listener = self._ble_listener
        if listener is not None:
            try:
                async with asyncio.timeout(_BACKGROUND_TASK_STOP_TIMEOUT_SEC):
                    await listener.async_stop()
            except TimeoutError:
                _LOGGER.warning(
                    "Jackery BLE option reconfiguration exceeded %.1fs; "
                    "HTTP remains active",
                    _BACKGROUND_TASK_STOP_TIMEOUT_SEC,
                )
                return
            except Exception as err:  # ruff: ignore[blind-except]  # Optional BLE failures stay isolated from HTTP.
                _LOGGER.warning(
                    "Jackery BLE option reconfiguration failed; HTTP remains "
                    "active: %s",
                    err,
                )
                return
            if self._ble_listener is listener:
                self._ble_sink_token = None
                self._ble_listener = None
        await self.async_start_ble_transport()

    def _ble_address_for_device(self, device_id: str) -> str | None:
        """Best-effort lookup of the BLE MAC for a Jackery device id.

        BLE addresses are learnt at advertisement time (the listener stores
        ``serial → MAC`` in its own state). The coordinator does not cache
        the MAC because it can change between adapter resets; the listener's
        in-memory map is the source of truth.
        """
        if self._ble_listener is None:
            return None
        return cast("str | None", self._ble_listener.address_for_device_id(device_id))

    def device_id_for_ble_serial(self, ble_serial: str) -> str | None:
        """Map a BLE-broadcast serial to its Jackery device id.

        The HTTP ``/v1/device/system/list`` response uses a longer
        serial form than the BLE manufacturer-data field. Example from a
        SolarVault 3 Pro Max captured 2026-05-16:

            HTTP  deviceSn: ``HR2C04000280HH3``  (15 chars, ``H`` prefix)
            BLE   adv data: ``R2C04000280HH3``   (14 chars, no prefix)

        The mapping is therefore "BLE serial is a suffix of HTTP serial".
        We accept exact match too in case future firmware aligns them, and
        we case-fold both sides because Jackery is inconsistent.
        """
        if not ble_serial:
            return None
        target = ble_serial.strip().upper()
        matches: list[str] = []
        for device_id, idx in self._device_index.items():
            device_meta = idx.get(PAYLOAD_DEVICE_META) or {}
            http_sn = (
                str(
                    device_meta.get(FIELD_DEVICE_SN)
                    or device_meta.get(FIELD_DEV_SN)
                    or "",
                )
                .strip()
                .upper()
            )
            if not http_sn:
                continue
            if http_sn == target or http_sn.endswith(target):
                matches.append(device_id)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            _LOGGER.warning(
                "Jackery BLE advertisement matched multiple cached devices; "
                "rejecting the ambiguous identity",
            )
        return None
