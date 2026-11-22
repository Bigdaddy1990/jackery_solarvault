"""Publish native Jackery sensor values through Home Assistant MQTT discovery."""

import asyncio
from collections.abc import Mapping
import contextlib
from datetime import date, datetime
from enum import Enum
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import quote, unquote
import weakref

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from homeassistant.components import mqtt as ha_mqtt
from homeassistant.components.recorder.db_schema import StatesMeta, StatisticsMeta
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import CoreState, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.recorder import (
    get_instance as get_recorder_instance,
    session_scope,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.util import slugify

from ..const import (
    DOMAIN,
    MANUFACTURER,
    MQTT_SENSOR_PUBLISHER_RUNTIME_KEY,
    MQTT_TELEMETRY_SCHEMA,
)
from ..util import jackery_dev_mode_enabled, redacted_json_safe_payload
from .credentials import redacted_error

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.typing import StateType


_LOGGER = logging.getLogger(__name__)
_DISCOVERY_PREFIX = "homeassistant"
_STATE_PREFIX = DOMAIN
_TELEMETRY_TOPIC = "homeassistant"
_CLEANUP_TIMEOUT_SEC = 1.0
_REGISTRY_HISTORY_TIMEOUT_SEC = 1.0
_PUBLISH_TIMEOUT_SEC = 4.0
_PUBLISH_RETRY_DELAY_SEC = 30.0
_MAX_PUBLISH_ERROR_CAUSES = 64
_CLEANUP_RETRY_DELAYS_SEC = (1.0, 5.0, 30.0)
_PUBLISH_CONCURRENCY = 4
_SENSOR_SCAN_YIELD_BATCH = 32
_MAX_SNAPSHOT_ATTEMPTS = 3
_PUBLISHER_RUNTIME_KEY = MQTT_SENSOR_PUBLISHER_RUNTIME_KEY
_SHARED_TELEMETRY_RUNTIME_KEY = "mqtt_shared_telemetry"
_CT_DISCOVERY_NAMES = {
    "smart_meter_phase_1_power": "CT Phase A",
    "smart_meter_phase_2_power": "CT Phase B",
    "smart_meter_phase_3_power": "CT Phase C",
    "smart_meter_power": "CT Phase T",
}
_MIRROR_UNIQUE_ID_PREFIX = f"{DOMAIN}_mqtt_"
_IDENTIFIER_PART_COUNT = 2


def registered_mirror_config_topics(hass: HomeAssistant) -> set[str]:
    """Return retained config topics created by the removed sensor mirror."""
    registry = er.async_get(hass)
    topics: set[str] = set()
    for registry_entry in registry.entities.values():
        unique_id = registry_entry.unique_id
        if registry_entry.platform != "mqtt" or not unique_id.startswith(
            _MIRROR_UNIQUE_ID_PREFIX
        ):
            continue
        native_unique_id = unique_id.removeprefix(_MIRROR_UNIQUE_ID_PREFIX)
        object_id = slugify(native_unique_id)
        topics.add(f"{_DISCOVERY_PREFIX}/sensor/{DOMAIN}/{object_id}/config")
    return topics


def _record_mqtt_operation_error(  # ruff: ignore[too-many-arguments]
    hass: HomeAssistant,
    topic: str,
    operation: str,
    entity: str,
    err: Exception,
    *,
    entry: ConfigEntry | None = None,
) -> None:
    """Keep bounded, redacted evidence for setup and obsolete-topic cleanup."""
    evidence = hass.data.setdefault(DOMAIN, {}).setdefault("mqtt_operation_errors", {})
    counts = evidence.setdefault("causes", {})
    text = _mirror_error_detail(err, entry)
    cause = (operation, topic, entity, type(err).__name__, text)
    if cause not in counts and len(counts) >= _MAX_PUBLISH_ERROR_CAUSES:
        counts.pop(next(iter(counts)))
    counts[cause] = counts.get(cause, 0) + 1
    evidence["errors"] = evidence.get("errors", 0) + 1
    evidence["last_error"] = {
        "topic": topic,
        "operation": operation,
        "qos": 0 if operation in {"cleanup_publish", "publish", "subscribe"} else None,
        "entity": entity,
        "error": text,
    }
    log = _LOGGER.warning if counts[cause] == 1 else _LOGGER.debug
    log(
        "Jackery MQTT failed: topic=%s operation=%s qos=%s entity=%s error=%s count=%d",
        topic,
        operation,
        evidence["last_error"]["qos"],
        entity,
        text,
        counts[cause],
    )


def subscribe_connection_status(
    hass: HomeAssistant, listener: Callable[[bool], None], entry_id: str
) -> Callable[[], None]:
    """Register a local connection listener and propagate contextual setup errors."""
    try:
        return ha_mqtt.async_subscribe_connection_status(hass, listener)
    except Exception as err:
        _record_mqtt_operation_error(
            hass,
            "connection_status",
            "subscribe_connection_status",
            entry_id,
            err,
            entry=hass.config_entries.async_get_entry(entry_id),
        )
        raise


class _EntityDeviceLike(Protocol):
    """Minimal protocol for entities that expose device_info and native_value.

    Read-only properties, matching ``SensorEntity`` (settable attributes in a
    Protocol reject every real entity).
    """

    @property
    def device_info(
        self,
    ) -> dr.DeviceInfo | dr.ChildDeviceInfo | Mapping[str, Any] | None: ...

    @property
    def native_value(self) -> StateType | date | datetime | Decimal: ...

    @property
    def unique_id(self) -> str | None: ...


class _DescriptionLike(Protocol):
    """Minimal protocol for sensor descriptions."""

    translation_key: str | None
    key: str | None
    entity_registry_enabled_default: bool
    device_class: str | None
    entity_category: str | None
    state_class: str | None
    native_unit_of_measurement: str | None


def _broker_connected(hass: HomeAssistant) -> bool:
    """Return MQTT connectivity; an MQTT entry not yet set up is disconnected."""
    try:
        return ha_mqtt.is_connected(hass)
    except KeyError:  # hass.data["mqtt"] exists only once the MQTT entry is set up
        return False


def _mirror_error_detail(error: Exception, entry: ConfigEntry | None) -> str:
    """Avoid echoing unlabelled credentials outside explicit raw-debug mode."""
    if jackery_dev_mode_enabled(entry):
        return redacted_error(error, entry=entry)
    if isinstance(error, OSError) and type(error.errno) is int:
        return f"{type(error).__name__}: errno={error.errno}"
    return type(error).__name__


def _exception_locations(error: Exception) -> list[tuple[str, int, str]]:
    """Return traceback locations without embedding the exception text."""
    frames = []
    tb = error.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        frames.append((frame.f_code.co_filename, tb.tb_lineno, frame.f_code.co_name))
        tb = tb.tb_next
    return frames


def _enum_value(value: Any) -> Any:  # ruff:ignore[any-type] — generic enum/unwrap helper
    """Return a JSON/MQTT scalar for Home Assistant enums."""
    return value.value if isinstance(value, Enum) else value


async def _finish_retained_publish(publish: Any) -> None:  # ruff: ignore[any-type]
    """Finish one bounded retained write before releasing its topic lock."""

    async def bounded_publish() -> None:
        # A caller's timeout must not release its lock while the broker write
        # can still finish. Bound the shielded task itself, including cleanup
        # writes that call Home Assistant MQTT directly.
        async with asyncio.timeout(_PUBLISH_TIMEOUT_SEC):
            await publish

    task = asyncio.create_task(bounded_publish())
    completed: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    task.add_done_callback(lambda _task: completed.set_result(None))
    cancelled = False
    while not completed.done():
        try:
            # Shield completion rather than the task's exception. Python 3.14
            # otherwise reports a shielded exception after caller cancellation
            # even when task.result() below consumes it.
            await asyncio.shield(completed)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        with contextlib.suppress(Exception, asyncio.CancelledError):
            task.result()
        raise asyncio.CancelledError
    task.result()


def _state_payload(
    value: Any,  # ruff: ignore[any-type] — accepts heterogeneous sensor values
    reset: str | None = None,
) -> str:
    """Serialize one native sensor value without inventing an unknown marker."""
    value = _enum_value(value)
    if value is None or (
        isinstance(value, str) and value in {"unknown", "unavailable"}
    ):
        value = "None"
    if reset is not None:
        return json.dumps({"value": _state_payload(value), "last_reset": reset})
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, dict | list | tuple):
        return json.dumps(value, separators=(",", ":"), default=str)
    return str(value)


def _entry_snapshot_prefix(unique_id: str, entry_id: str) -> str:
    """Return Jinja that selects this entry's sensor from the shared snapshot."""
    return (
        "{%- set d = value_json if value_json is defined else none -%}"
        "{%- set o = d.get('origin') if d is mapping else none -%}"
        "{%- set valid = (o is mapping and d.get('schema') == "
        + json.dumps(MQTT_TELEMETRY_SCHEMA)
        + " and o.get('domain') == "
        + json.dumps(DOMAIN)
        + ") -%}"
        "{%- set entries = d.get('entries') if valid else none -%}"
        "{%- set own = entries.get("
        + json.dumps(entry_id)
        + ") if entries is mapping else (d if valid and o.get('entry_id') == "
        + json.dumps(entry_id)
        + " else none) -%}"
        "{%- set s = own.get('sensors') if own is mapping else none -%}"
        "{%- set e = s.get(" + json.dumps(unique_id) + ") if s is mapping else none -%}"
    )


def _reset_timestamp(entity: _EntityDeviceLike) -> str | None:
    """Return the reset boundary of a TOTAL sensor mirrored through MQTT."""
    if _enum_value(getattr(entity, "state_class", None)) != "total":
        return None
    reset = getattr(entity, "last_reset", None)
    return reset.isoformat() if isinstance(reset, datetime) else None


def _device_info_mapping(raw: object) -> dict[str, Any]:
    """Normalize legacy mappings and modern HA device-info dataclasses."""
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, dr.DeviceInfo | dr.ChildDeviceInfo):
        # HA 2026.11 returns dataclasses whose absent fields are UNDEFINED.
        # Keep only supported MQTT metadata; never stringify the sentinel.
        return {
            key: value
            for key in (
                "identifiers",
                "name",
                "manufacturer",
                "model",
                "sw_version",
                "hw_version",
                "serial_number",
                "via_device",
            )
            if (value := getattr(raw, key, UNDEFINED)) is not UNDEFINED
        }
    return {}


def _device_config(entity: _EntityDeviceLike) -> tuple[str, dict[str, Any]]:
    """Build a JSON-safe MQTT device block and return its stable identifier."""
    info = _device_info_mapping(getattr(entity, "device_info", None))
    registered = getattr(entity, "device_entry", None)
    if isinstance(registered, dr.BaseDeviceEntry):
        # The registered device is stable; device_info follows the payload (a CT
        # payload without identity falls back to "<device>_smart_meter_1"), which
        # moved the mirror to topics its retained config never subscribed to.
        # pyrefly: ignore [missing-attribute]
        info["identifiers"] = registered.identifiers
        # pyrefly: ignore [missing-attribute]
        info["name"] = registered.name_by_user or registered.name
    # Only a full DeviceEntry has these fields; reading them on a ChildDeviceEntry
    # (battery packs) is a deprecated compat shim that breaks in HA 2027.9.
    if isinstance(registered, dr.DeviceEntry):
        info.update(
            (key, value)
            for key, value in (
                # pyrefly: ignore [missing-attribute]
                ("manufacturer", registered.manufacturer),
                # pyrefly: ignore [missing-attribute]
                ("model", registered.model),
                # pyrefly: ignore [missing-attribute]
                ("sw_version", registered.sw_version),
                # pyrefly: ignore [missing-attribute]
                ("hw_version", registered.hw_version),
                # pyrefly: ignore [missing-attribute]
                ("serial_number", registered.serial_number),
            )
            if value is not None
        )
    identifiers: list[str] = []
    device_id = "unknown"
    for identifier in sorted(info.get("identifiers", ()) or (), key=str):
        if isinstance(identifier, tuple) and len(identifier) == _IDENTIFIER_PART_COUNT:
            namespace, identifier_value = str(identifier[0]), str(identifier[1])
            identifiers.append(f"{namespace}:{identifier_value}")
            if namespace == DOMAIN and device_id == "unknown":
                device_id = identifier_value
        elif identifier:
            identifiers.append(str(identifier))
    if not identifiers:
        unique_id = str(getattr(entity, "unique_id", "unknown"))
        device_id = unique_id.split("_", 1)[0]
        identifiers.append(f"{DOMAIN}:{device_id}")
    config: dict[str, Any] = {"identifiers": sorted(set(identifiers))}
    for key in (
        "name",
        "manufacturer",
        "model",
        "sw_version",
        "hw_version",
        "serial_number",
    ):
        metadata_value = info.get(key)
        if metadata_value is not None and str(metadata_value).strip():
            config[key] = str(metadata_value)
    via_device = info.get("via_device")
    if isinstance(via_device, tuple) and len(via_device) == _IDENTIFIER_PART_COUNT:
        config["via_device"] = f"{via_device[0]}:{via_device[1]}"
    elif via_device:
        config["via_device"] = str(via_device)
    config.setdefault("manufacturer", MANUFACTURER)
    config.setdefault("name", f"Jackery {device_id}")
    return device_id, config


def _description_name(
    entity: _EntityDeviceLike, description: _DescriptionLike | None, unique_id: str
) -> str:
    """Use the native entity's translated name for its MQTT mirror."""
    translated = getattr(entity, "name", None)
    if isinstance(translated, str) and translated.strip():
        return translated.strip()
    raw = (
        getattr(description, "translation_key", None)
        or getattr(description, "key", None)
        or getattr(entity, "_attr_translation_key", None)
        or unique_id
    )
    if name := _CT_DISCOVERY_NAMES.get(str(raw)):
        return name
    return str(raw).replace("_", " ").strip().title()


def _legacy_description_name(
    entity: _EntityDeviceLike, description: _DescriptionLike | None, unique_id: str
) -> str:
    """Keep the former MQTT object ID while translating its display name."""
    raw = (
        getattr(description, "translation_key", None)
        or getattr(description, "key", None)
        or getattr(entity, "_attr_translation_key", None)
        or unique_id
    )
    return _CT_DISCOVERY_NAMES.get(str(raw), str(raw).replace("_", " ").strip().title())


class _SharedTelemetry:
    """Coordinate retained topics across active config entries."""

    def __init__(self) -> None:
        self.publishers: dict[str, JackeryMqttSensorPublisher] = {}
        self.lock = asyncio.Lock()
        self.telemetry_lock = asyncio.Lock()
        self.topic_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self.revision = 0
        self.broker_generation = 0
        self.payload: str | None = None
        self.last_payload: str | None = None
        self.pending_own_wire: tuple[str, bytes] | None = None
        self.confirmed_own_wire: tuple[str, bytes] | None = None
        self.snapshot_sequence = 0
        self.published_snapshot_sequence = 0
        self.retirement_task: asyncio.Task[None] | None = None


def _sensor_topics_for_entry(entry_id: str, unique_id: str) -> tuple[str, str, str]:
    """Return stable, owned topics without creating a live publisher."""
    topic_base = f"{_STATE_PREFIX}/{entry_id}/sensor/{quote(unique_id, safe="")}"
    return (
        f"{topic_base}/state",
        f"{topic_base}/availability",
        f"{_DISCOVERY_PREFIX}/sensor/{DOMAIN}/{slugify(unique_id)}/config",
    )


def _update_topics_for_entry(
    entry_id: str, unique_id: str
) -> tuple[str, str, str, str]:
    """Return stable state, availability, command and discovery topics."""
    escaped = quote(unique_id, safe="")
    root = f"{DOMAIN}/{entry_id}/update/{escaped}"
    discovery = f"homeassistant/update/{DOMAIN}/{entry_id}_{escaped}/config"
    return f"{root}/state", f"{root}/availability", f"{root}/set", discovery


def _topic_publish_lock(shared: _SharedTelemetry, topic: str) -> asyncio.Lock:
    """Serialize related retained topics across publishers and their cleanup."""
    if topic.endswith(("/state", "/availability", "/attributes")):
        topic = topic.rsplit("/", 1)[0]
    locks: weakref.WeakValueDictionary[str, asyncio.Lock] | None = getattr(
        shared, "topic_locks", None
    )
    if locks is None:
        # A task waiting for or holding a lock keeps it alive. Unused topic
        # locks disappear, including after entity churn and entry removal.
        locks = weakref.WeakValueDictionary()
        shared.topic_locks = locks
    return locks.setdefault(topic, asyncio.Lock())


class _OwnTelemetryText(str):  # ruff: ignore[subclass-builtin]
    """Mark a document constructed by the integration's telemetry serializer."""

    __slots__ = ()


def _serialize_shared_telemetry(
    snapshots: dict[str, dict[str, Any]], flags: dict[str, bool]
) -> str:
    """Serialize operational layer data without export-time credential masking."""

    def directly_json_compatible(value: object) -> bool:
        """Recognize detached trees whose raw JSON representation is unchanged."""
        pending = [value]
        seen: set[int] = set()
        while pending:
            item = pending.pop()
            if type(item) is dict:
                assert isinstance(item, dict)
                if id(item) in seen or any(type(key) is not str for key in item):
                    return False
                seen.add(id(item))
                pending.extend(item.values())
            elif isinstance(item, (list, tuple)):
                if type(item) not in {list, tuple} or id(item) in seen:
                    return False
                seen.add(id(item))
                pending.extend(item)
            elif item is not None and type(item) not in {str, int, float, bool}:
                return False
        return True

    entries = {
        entry_id: {
            "sensors": snapshot["sensors"],
            "source_receipts": (
                snapshot["source_receipts"]
                if directly_json_compatible(snapshot["source_receipts"])
                else redacted_json_safe_payload(
                    snapshot["source_receipts"], unredacted=True
                )
            ),
        }
        for entry_id, snapshot in snapshots.items()
    }
    primary_id = min(entries)
    own = entries[primary_id]
    document = {
        "schema": MQTT_TELEMETRY_SCHEMA,
        "origin": {
            "domain": DOMAIN,
            "entry_id": primary_id,
            "entry_ids": sorted(entries),
        },
        "sensors": own["sensors"],
        "source_receipts": own["source_receipts"],
    }
    if len(entries) > 1:
        document["entries"] = entries
    return _OwnTelemetryText(json.dumps(document))


def _retired_telemetry_payload(payload: str | None, entry_id: str) -> str:
    """Keep the last values/receipts while marking retired sensors offline."""
    document = (
        json.loads(payload)
        if payload
        else {
            "schema": MQTT_TELEMETRY_SCHEMA,
            "origin": {"domain": DOMAIN, "entry_id": entry_id},
            "sensors": {},
            "source_receipts": [],
        }
    )
    snapshots = [document]
    entries = document.get("entries")
    if isinstance(entries, dict):
        snapshots.extend(entries.values())
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        sensors = snapshot.get("sensors")
        if isinstance(sensors, dict):
            for sensor in sensors.values():
                if isinstance(sensor, dict):
                    sensor["availability"] = "offline"
    return json.dumps(document)


class JackeryMqttSensorPublisher:
    """Mirror value-bearing native Jackery sensors to the configured HA broker."""

    def __init__(self, hass: HomeAssistant, *, entry_id: str) -> None:
        """Initialize an entry-scoped publisher."""
        self._hass = hass
        self._entry_id = entry_id
        entry = hass.config_entries.async_get_entry(entry_id)
        self._entry = entry
        # Input subscriptions may contain wildcards or carry original device
        # frames. Export snapshots must use their own concrete publish topic.
        self._telemetry_topic = _TELEMETRY_TOPIC
        self._telemetry_payload: str | None = None
        self._broker_generation = 0
        self._entities: dict[str, Any] = {}
        self._entity_tracking_revision = 0
        self._native_refresh_pending: set[str] = set()
        self._native_pending: dict[str, _EntityDeviceLike] = {}
        self._native_entity_ids: dict[str, str] = {}
        self._passive_cache_cleanup: dict[str, Callable[[], None]] = {}
        self._native_registry_unsubscribe: Callable[[], None] | None = None
        self._native_state_unsubscribe: Callable[[], None] | None = None
        self._enum_migrations: dict[str, tuple[str, ...]] = {}
        self._enum_migration_unsubscribe: Callable[[], None] | None = None
        self._entry_state_unsubscribe: Callable[[], None] | None = (
            entry.async_on_state_change(self._async_entry_state_changed)
            if entry is not None
            else None
        )
        self._published_configs: dict[str, tuple[str, str]] = {}
        self._published_states: dict[str, tuple[str, str]] = {}
        self._decoded_published_states: dict[
            str, tuple[str, tuple[Any, Any] | None]
        ] = {}
        self._published_availability: dict[str, tuple[str, str]] = {}
        self._owned_sensor_topics: set[str] = set()
        self._cleared_unknowns: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._sensor_publish_wakeup: asyncio.Event | None = None
        self._shared_telemetry_task: asyncio.Task[None] | None = None
        self._shared_telemetry_pending = False
        self._shared_telemetry_retry_unsubscribe: Callable[[], None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_lock = asyncio.Lock()
        self._cleanup_topics: set[str] = set()
        self._cleanup_unsubscribe: Callable[[], None] | None = None
        self._pending = False
        self._retry_unsubscribe: Callable[[], None] | None = None
        self._publish_loop_error_counts: dict[tuple[str, str], int] = {}
        self._start_unsubscribe: Callable[[], None] | None = None
        self._stopping = False
        self._owns_topics = True
        self._owner_bucket: dict[str, Any] | None = None
        self._shared_telemetry = _SharedTelemetry()
        self._device_unsubscribe: Callable[[], None] | None = hass.bus.async_listen(
            dr.EVENT_DEVICE_REGISTRY_UPDATED, self._async_link_mqtt_device
        )
        self._register_shared_runtime()
        for device in dr.async_get(hass).devices:
            self._link_mqtt_device(device.id)

    def _register_shared_runtime(self) -> None:
        """Register this entry generation with the shared topic owner."""
        hass_data = getattr(self._hass, "data", None)
        if isinstance(hass_data, dict):
            domain_bucket = hass_data.setdefault(DOMAIN, {})
            if isinstance(domain_bucket, dict):
                shared = domain_bucket.get(_SHARED_TELEMETRY_RUNTIME_KEY)
                if not isinstance(shared, _SharedTelemetry):
                    shared = _SharedTelemetry()
                    domain_bucket[_SHARED_TELEMETRY_RUNTIME_KEY] = shared
                self._shared_telemetry = shared
                entry_bucket = domain_bucket.setdefault(self._entry_id, {})
                if isinstance(entry_bucket, dict):
                    previous = entry_bucket.get(_PUBLISHER_RUNTIME_KEY)
                    if isinstance(previous, JackeryMqttSensorPublisher):
                        self._owned_sensor_topics.update(
                            previous._owned_sensor_topics  # ruff: ignore[private-member-access]
                        )
                        previous.async_retire()
                    entry_bucket[_PUBLISHER_RUNTIME_KEY] = self
                    self._owner_bucket = entry_bucket
        retirement_task = self._shared_telemetry.retirement_task
        if retirement_task is not None:
            retirement_task.cancel()
            self._shared_telemetry.retirement_task = None
        self._shared_telemetry.publishers[self._entry_id] = self
        self._shared_telemetry.revision += 1

    @callback
    def _async_link_mqtt_device(
        self, event: Event[dr.EventDeviceRegistryUpdatedData]
    ) -> None:
        """Attach newly discovered MQTT mirrors to their native device."""
        device_id = event.data.get("device_id")
        if device_id:
            self._link_mqtt_device(device_id)

    @staticmethod
    def _via_target_id(registry: dr.DeviceRegistry, device_id: str) -> str | None:
        """Return the nearest non-child ancestor usable as ``via_device_id``.

        HA 2026.9 registers battery packs as ``ChildDeviceEntry`` and rejects a
        child as via device ("is a child device, which can't be a via
        device"), so a pack's MQTT mirror is linked to the head unit instead.
        """
        seen: set[str] = set()
        current = registry.async_get(device_id)
        while isinstance(current, dr.ChildDeviceEntry) and current.id not in seen:
            seen.add(current.id)
            current = registry.async_get(current.parent_device_id)
        if current is None or isinstance(current, dr.ChildDeviceEntry):
            return None
        return current.id

    @callback
    def _link_mqtt_device(self, device_id: str) -> None:  # ruff: ignore[too-many-return-statements, too-many-branches]
        registry = dr.async_get(self._hass)
        device = registry.async_get(device_id)
        if device is None:
            return
        if any(namespace == DOMAIN for namespace, _ in device.identifiers):  # ruff: ignore[too-many-nested-blocks]
            if device.config_entry_id != self._entry_id:
                return
            via_id = self._via_target_id(registry, device.id)
            if via_id is None:
                return
            for namespace, identifier in device.identifiers:
                if namespace == DOMAIN:
                    mirror_identifier = ("mqtt", f"{DOMAIN}:{identifier}")
                    for broker_entry in self._hass.config_entries.async_entries("mqtt"):
                        mirror = registry.async_get_device_by_identifier(
                            mirror_identifier, broker_entry.entry_id
                        )
                        if mirror is not None and mirror.id != via_id:
                            url = f"homeassistant://config/devices/device/{device.id}"
                            if (
                                mirror.via_device_id != via_id
                                or mirror.configuration_url != url
                            ):
                                registry.async_update_device(
                                    mirror.id,
                                    via_device_id=via_id,
                                    configuration_url=url,
                                )
            return
        if self._entry_id == device.config_entry_id:
            return
        for namespace, identifier in device.identifiers:
            prefix = f"{DOMAIN}:"
            if namespace != "mqtt" or not identifier.startswith(prefix):
                continue
            native = registry.async_get_device_by_identifier(
                (DOMAIN, identifier.removeprefix(prefix)), self._entry_id
            )
            child_lookup = getattr(
                registry, "async_get_child_device_by_identifier", None
            )
            if native is None and callable(child_lookup):
                native = child_lookup(
                    (DOMAIN, identifier.removeprefix(prefix)), self._entry_id
                )
            if native is None:
                return
            via_id = self._via_target_id(registry, native.id)
            if via_id is None:
                return
            # A via link cannot target a HA child. Keep valid ancestry and
            # provide an exact local counterpart link without merging identities.
            url = f"homeassistant://config/devices/device/{native.id}"
            if via_id != device.id and (
                getattr(device, "via_device_id", None) != via_id
                or getattr(device, "configuration_url", None) != url
            ):
                registry.async_update_device(
                    device.id, via_device_id=via_id, configuration_url=url
                )
            return

    @callback
    def _cancel_publish_retry(self) -> None:
        """Cancel the pending publish retry, if any."""
        unsubscribe = getattr(self, "_retry_unsubscribe", None)
        if unsubscribe is not None:
            unsubscribe()
            self._retry_unsubscribe = None

    @callback
    def async_retire(self) -> None:
        """Relinquish retained-topic ownership to a replacement publisher."""
        was_owner = self._is_current_owner()
        self._owns_topics = False
        self._stopping = True
        telemetry_task = getattr(self, "_shared_telemetry_task", None)
        if telemetry_task is not None and not telemetry_task.done():
            telemetry_task.cancel()
        telemetry_retry = getattr(self, "_shared_telemetry_retry_unsubscribe", None)
        if telemetry_retry is not None:
            telemetry_retry()
            self._shared_telemetry_retry_unsubscribe = None
        self._detach_shared_telemetry()
        topics = tuple(
            topic
            for topic in getattr(self, "_owned_sensor_topics", ())
            if topic.endswith("/availability")
        )
        if was_owner and topics:
            self._availability_retirement_task = (
                self._hass.async_create_background_task(
                    self._async_retire_availability(topics),
                    name=f"{DOMAIN}_mqtt_retired_sensors_{self._entry_id}",
                    eager_start=False,
                )
            )
        self._cancel_publish_retry()
        self._native_pending.clear()
        self._native_entity_ids.clear()
        self._unsubscribe_native_readiness()
        for entity_id in tuple(self._enum_migrations):
            self._forget_enum_migration(entity_id)
        for stop in self._passive_cache_cleanup.values():
            stop()
        self._passive_cache_cleanup.clear()
        if self._entry_state_unsubscribe is not None:
            self._entry_state_unsubscribe()
            self._entry_state_unsubscribe = None
        start_unsubscribe = getattr(self, "_start_unsubscribe", None)
        if start_unsubscribe is not None:
            start_unsubscribe()
            self._start_unsubscribe = None
        # Retire runs twice (replacement publisher and entry unload); a second
        # bus unsubscribe raises "Unable to remove unknown job listener".
        if self._device_unsubscribe is not None:
            self._device_unsubscribe()
            self._device_unsubscribe = None
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._cleanup_topics.clear()
        if self._cleanup_unsubscribe is not None:
            self._cleanup_unsubscribe()
            self._cleanup_unsubscribe = None
        task = self._cleanup_task
        if task is not None and not task.done():
            task.cancel()

    async def _async_retire_availability(self, topics: tuple[str, ...]) -> None:
        """Mark this entry offline without racing its replacement or removal."""
        shared = self._shared_telemetry
        pending = {
            topic
            for topic in topics
            if topic in self._owned_sensor_topics and topic.endswith("/availability")
        }
        adoption_owner: object = None
        adoption_revision: object = None
        adopted_topics: set[str] = set()

        def replacement_adopts(topic: str) -> bool:
            nonlocal adoption_owner, adoption_revision, adopted_topics
            replacement = shared.publishers.get(self._entry_id)
            if (
                replacement is None
                or getattr(replacement, "_stopping", False)
                or not replacement._is_current_owner()  # ruff: ignore[private-member-access]
            ):
                return False
            entities = replacement._entities  # ruff: ignore[private-member-access]
            revision = getattr(replacement, "_entity_tracking_revision", None)
            if revision is None:
                revision = tuple(entities)
            if replacement is not adoption_owner or revision != adoption_revision:
                adoption_owner, adoption_revision = replacement, revision
                adopted_topics = {
                    replacement._sensor_topics(unique_id)[1]  # ruff: ignore[private-member-access]
                    for unique_id in entities
                }
            return topic in adopted_topics

        while pending:
            pending.difference_update(
                topic for topic in tuple(pending) if replacement_adopts(topic)
            )
            if not pending:
                return
            try:  # ruff: ignore[too-many-statements-in-try-clause]
                if self._hass.is_stopping and not _broker_connected(self._hass):
                    return
                async with asyncio.timeout(_PUBLISH_TIMEOUT_SEC):
                    if not await self._async_ensure_mqtt_connected():
                        raise HomeAssistantError("MQTT client unavailable")  # ruff: ignore[raise-vanilla-args, raise-within-try]
                for topic in sorted(pending):
                    async with _topic_publish_lock(shared, topic):
                        if replacement_adopts(topic):
                            pending.discard(topic)
                            continue
                        await _finish_retained_publish(
                            self._async_publish(topic, "offline")
                        )
                        pending.discard(topic)
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # ruff: ignore[blind-except] - retain retry after unload
                self._log_unexpected_publish_error(err)
                if self._hass.is_stopping:
                    return
                await asyncio.sleep(_PUBLISH_RETRY_DELAY_SEC)

    @callback
    def _detach_shared_telemetry(self) -> None:
        """Remove only this entry generation from the shared snapshot."""
        shared = self._shared_telemetry
        if shared.publishers.get(self._entry_id) is self:
            previous_payload = (
                shared.payload
                or getattr(shared, "last_payload", None)
                or self._telemetry_payload
            )
            shared.publishers.pop(self._entry_id)
            shared.revision += 1
            shared.payload = None
            for publisher in shared.publishers.values():
                for unique_id in getattr(self, "_entities", {}):
                    getattr(publisher, "_published_configs", {}).pop(unique_id, None)
                publisher.async_schedule_publish()
            if not shared.publishers:
                shared.retirement_task = self._hass.async_create_background_task(
                    self._async_publish_retired_telemetry(
                        shared, shared.revision, previous_payload
                    ),
                    name=f"{DOMAIN}_mqtt_retired_availability_{self._entry_id}",
                    eager_start=False,
                )

    async def _async_publish_retired_telemetry(
        self, shared: _SharedTelemetry, revision: int, previous_payload: str | None
    ) -> None:
        """Make the final retained snapshot offline without racing a reload."""
        try:  # ruff: ignore[too-many-nested-blocks]
            payload = await self._hass.async_add_executor_job(
                _retired_telemetry_payload, previous_payload, self._entry_id
            )
            while not shared.publishers and shared.revision == revision:
                latest = getattr(shared, "last_payload", None) or previous_payload
                if latest != previous_payload:
                    payload = await self._hass.async_add_executor_job(
                        _retired_telemetry_payload, latest, self._entry_id
                    )
                    previous_payload = latest
                try:  # ruff: ignore[too-many-statements-in-try-clause]
                    if self._hass.is_stopping and not _broker_connected(self._hass):
                        return
                    async with asyncio.timeout(_PUBLISH_TIMEOUT_SEC):
                        if not await self._async_ensure_mqtt_connected():
                            raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args, raise-within-try]
                                "MQTT client unavailable for Jackery retired availability"  # ruff: ignore[line-too-long]
                            )
                        async with getattr(shared, "telemetry_lock", shared.lock):
                            if shared.publishers or shared.revision != revision:
                                return
                            latest = (
                                getattr(shared, "last_payload", None)
                                or previous_payload
                            )
                            if latest != previous_payload:
                                # An in-flight online write completed while
                                # this retirement waited. Rebuild outside the
                                # lock before publishing the offline snapshot.
                                continue
                            await _finish_retained_publish(
                                self._async_publish(self._telemetry_topic, payload)
                            )
                            if not shared.publishers and shared.revision == revision:
                                shared.payload = payload
                                shared.last_payload = payload
                            return
                except Exception as err:  # ruff: ignore[blind-except] - retain failed unload availability until a replacement owns the topic
                    self._log_unexpected_publish_error(err)
                    if self._hass.is_stopping:
                        return
                    await asyncio.sleep(_PUBLISH_RETRY_DELAY_SEC)
        finally:
            if shared.retirement_task is asyncio.current_task():
                shared.retirement_task = None

    def _is_current_owner(self) -> bool:
        """Return whether this generation may mutate its retained topics."""
        return self._owns_topics and (
            self._owner_bucket is None
            or self._owner_bucket.get(_PUBLISHER_RUNTIME_KEY) is self
        )

    def track(self, entity: _EntityDeviceLike) -> None:
        """Track one registered native sensor by its stable unique ID."""
        unique_id = getattr(entity, "unique_id", None)
        if unique_id:
            self._entities[str(unique_id)] = entity
            self._entity_tracking_revision = (
                getattr(self, "_entity_tracking_revision", 0) + 1
            )
            self._owned_sensor_topics.add(self._sensor_topics(str(unique_id))[2])
            self._shared_telemetry.revision += 1

    def _sensor_topics(self, unique_id: str) -> tuple[str, str, str]:
        """Return this entry's retained state, availability and discovery topics."""
        return _sensor_topics_for_entry(self._entry_id, unique_id)

    def owns_sensor_topic(self, topic: str) -> bool:
        """Return whether this publisher has claimed one exact retained topic."""
        return topic in self._owned_sensor_topics

    @callback
    def track_when_native_ready(self, entity: _EntityDeviceLike) -> None:
        """Wait for this entry's native registry and initial state before mirroring."""
        unique_id = entity.unique_id
        if not unique_id or self._stopping:
            return
        self._native_pending[unique_id] = entity
        if self._native_registry_unsubscribe is None:
            self._native_registry_unsubscribe = self._hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._async_native_event
            )
        self._promote_native_if_ready(unique_id)

    @callback
    def _async_native_event(self, event: Event[Any]) -> None:
        entity_id = event.data.get("entity_id")
        if not isinstance(entity_id, str) or not self._native_pending:
            return
        if event.event_type == EVENT_STATE_CHANGED:
            unique_id = self._native_entity_ids.get(entity_id)
            if unique_id is not None:
                self._promote_native_if_ready(unique_id)
            return
        old_entity_id = event.data.get("old_entity_id")
        if isinstance(old_entity_id, str):
            self._forget_native_state_entity(old_entity_id)
        registered = er.async_get(self._hass).async_get(entity_id)
        if registered is None:
            unique_id = self._native_entity_ids.get(entity_id)
            self._forget_native_state_entity(entity_id)
            if unique_id is not None:
                self._native_pending.pop(unique_id, None)
            if not self._native_pending:
                self._unsubscribe_native_readiness()
            return
        if registered.unique_id in self._native_pending:
            self._promote_native_if_ready(registered.unique_id)

    @callback
    def _forget_native_state_entity(self, entity_id: str) -> None:
        self._native_entity_ids.pop(entity_id, None)
        if not self._native_entity_ids and self._native_state_unsubscribe is not None:
            self._native_state_unsubscribe()
            self._native_state_unsubscribe = None

    @callback
    def _promote_native_if_ready(self, unique_id: str) -> None:
        registry = er.async_get(self._hass)
        entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id is None:
            return
        registered = registry.async_get(entity_id)
        if registered is None:
            return
        if registered.config_entry_id != self._entry_id:
            self._forget_native_state_entity(entity_id)
            self._native_pending.pop(unique_id, None)
            if not self._native_pending:
                self._unsubscribe_native_readiness()
            return
        self._native_entity_ids[entity_id] = unique_id
        if registered.disabled_by is None and self._hass.states.get(entity_id) is None:
            if self._native_state_unsubscribe is None:
                self._native_state_unsubscribe = self._hass.bus.async_listen(
                    EVENT_STATE_CHANGED, self._async_native_event
                )
            return
        self._forget_native_state_entity(entity_id)
        entity = self._native_pending.pop(unique_id, None)
        if entity is not None:
            stop = self._passive_cache_cleanup.pop(unique_id, None)
            if stop is not None:
                stop()
            self.track(entity)
            if registered.disabled_by is not None:
                starter = getattr(type(entity), "async_start_passive_cache", None)
                if callable(starter):
                    stop = starter(entity, self.async_schedule_publish)
                    if callable(stop):
                        self._passive_cache_cleanup[unique_id] = stop
            self.async_schedule_publish()
        if not self._native_pending:
            self._unsubscribe_native_readiness()

    @callback
    def _unsubscribe_native_readiness(self) -> None:
        for name in ("_native_registry_unsubscribe", "_native_state_unsubscribe"):
            unsubscribe = getattr(self, name)
            if unsubscribe is not None:
                unsubscribe()
                setattr(self, name, None)

    @callback
    def _async_entry_state_changed(self) -> None:
        if (
            self._entry is not None
            and self._entry.state is ConfigEntryState.LOADED
            and self._pending
        ):
            self.async_schedule_publish()

    @callback
    def async_broker_connected(self) -> None:
        """Republish discovery and states after a broker reconnect."""
        self._broker_generation += 1
        self._shared_telemetry.broker_generation += 1
        self._shared_telemetry.payload = None
        self._shared_telemetry.revision += 1
        self._invalidate_published_snapshots()
        self._cancel_publish_retry()
        telemetry_retry = getattr(self, "_shared_telemetry_retry_unsubscribe", None)
        if telemetry_retry is not None:
            telemetry_retry()
            self._shared_telemetry_retry_unsubscribe = None
        if getattr(self, "_shared_telemetry_pending", False):
            self._schedule_shared_telemetry()
        self.async_schedule_publish()

    def _invalidate_published_snapshots(self) -> None:
        """Discard confirmations belonging to the previous broker connection."""
        self._published_configs.clear()
        self._published_states.clear()
        getattr(self, "_decoded_published_states", {}).clear()
        self._published_availability.clear()
        self._telemetry_payload = None

    @callback
    def async_schedule_publish(self) -> None:
        """Coalesce coordinator callbacks into an immediate broker snapshot."""
        if self._stopping:
            return
        self._shared_telemetry.revision += 1
        self._pending = True
        if wakeup := getattr(self, "_sensor_publish_wakeup", None):
            wakeup.set()
        if getattr(self, "_retry_unsubscribe", None) is not None:
            return
        if not self._entities or (
            self._entry is not None and self._entry.state is not ConfigEntryState.LOADED
        ):
            return
        if self._hass.state is not CoreState.running:
            if getattr(self, "_start_unsubscribe", None) is None:
                self._start_unsubscribe = async_at_started(
                    self._hass, self._async_publish_after_ha_ready
                )
            return
        if self._task is not None and not self._task.done():
            return
        self._task = self._hass.async_create_background_task(
            self._async_publish_loop(),
            name=f"{DOMAIN}_mqtt_sensor_publish_{self._entry_id}",
            eager_start=False,
        )

    @callback
    def _async_publish_after_ha_ready(self, _hass: HomeAssistant) -> None:
        """Resume the retained latest snapshot once HA startup is complete."""
        self._start_unsubscribe = None
        self.async_schedule_publish()

    def _begin_publish_attempt(self) -> int:
        """Bind one publish attempt to its broker generation."""
        self._pending = False
        return getattr(self, "_broker_generation", 0)

    def _log_unexpected_publish_error(self, err: Exception) -> None:
        """Report bounded, safe error details and source locations."""
        counts = getattr(self, "_publish_loop_error_counts", None)
        if not isinstance(counts, dict):
            counts = self._publish_loop_error_counts = {}
        detail = _mirror_error_detail(
            err, self._hass.config_entries.async_get_entry(self._entry_id)
        )
        cause = (type(err).__name__, detail)
        if cause not in counts and len(counts) >= _MAX_PUBLISH_ERROR_CAUSES:
            counts.pop(next(iter(counts)))
        counts[cause] = counts.get(cause, 0) + 1
        log = _LOGGER.warning if counts[cause] == 1 else _LOGGER.debug
        log(
            "Unable to publish Jackery MQTT mirror data: %s count=%d",
            detail,
            counts[cause],
        )
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "MQTT mirror publish failure frames: %s", _exception_locations(err)
            )

    def _schedule_failed_publish_retry(self, generation: int) -> None:
        """Retry on a new connection immediately, otherwise after backoff."""
        if (
            self._stopping
            or not self._is_current_owner()
            or not _broker_connected(self._hass)
        ):
            return
        if self._broker_generation != generation:
            self.async_schedule_publish()
        else:
            self._retry_unsubscribe = async_call_later(
                self._hass, _PUBLISH_RETRY_DELAY_SEC, self._async_retry_publish
            )

    async def _async_publish_loop(self) -> None:
        """Publish the newest snapshot and rerun once if updates raced with I/O."""
        retry = False
        attempt_generation = getattr(self, "_broker_generation", 0)
        try:
            while self._pending and not self._stopping:
                attempt_generation = self._begin_publish_attempt()
                if not await self._async_ensure_mqtt_connected():
                    self._pending = True
                    return
                await self.async_publish_pending()
        except asyncio.CancelledError:
            self._pending = True
            retry = True
            raise
        except (TimeoutError, HomeAssistantError) as err:
            self._pending = True
            retry = True
            _LOGGER.debug(
                "Jackery MQTT mirror remains pending: %s",
                _mirror_error_detail(
                    err, self._hass.config_entries.async_get_entry(self._entry_id)
                ),
            )
        except Exception as err:  # ruff: ignore[blind-except] - retain failed publisher work
            self._pending = True
            retry = True
            self._log_unexpected_publish_error(err)
        finally:
            self._task = None
            if retry:
                self._schedule_failed_publish_retry(attempt_generation)

    @callback
    def _async_retry_publish(self, _now: datetime) -> None:
        """Retry a failed publish without waiting for a new sensor update."""
        self._retry_unsubscribe = None
        if self._pending:
            self.async_schedule_publish()

    async def _async_ensure_mqtt_connected(self) -> bool:
        """Report whether retained publishes can use an available MQTT client."""
        if _broker_connected(self._hass):
            return True
        connected = await ha_mqtt.async_wait_for_mqtt_client(self._hass)
        if not connected:
            _LOGGER.debug("MQTT client not available, skipping discovery publish")
        return bool(connected)

    async def async_publish_pending(self) -> None:  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
        """Keep changed states live while fairly completing the discovery sweep."""
        generation = self._broker_generation
        wakeup = asyncio.Event()
        self._sensor_publish_wakeup = wakeup
        background = dict(self._entities)
        remaining = set(background)
        inflight: dict[asyncio.Task[None], str] = {}
        waiter: asyncio.Task[bool] | None = None
        priority_streak = 0
        priority: dict[str, _EntityDeviceLike] = {}
        scan_all = True
        published_state_inputs = getattr(self, "_decoded_published_states", None)
        if published_state_inputs is None:
            published_state_inputs = self._decoded_published_states = {}

        def queue_changed(unique_id: str, entity: _EntityDeviceLike) -> None:
            states = getattr(self, "_published_states", {})
            if unique_id in getattr(self, "_native_refresh_pending", ()):
                priority.setdefault(unique_id, entity)
                return
            if unique_id not in states:
                return
            try:
                value = entity.native_value
                available = bool(getattr(entity, "available", True))
            except Exception:  # ruff: ignore[blind-except]
                value, available = None, False
            state_inputs = (_state_payload(value), _reset_timestamp(entity))
            publication = states[unique_id][1]
            previous = published_state_inputs.get(unique_id)
            if previous is None or previous[0] != publication:
                # These immutable publication bytes, rather than an entity or
                # mutable payload identity, own the reusable comparison value.
                try:
                    decoded = json.loads(publication)
                except TypeError, json.JSONDecodeError:
                    decoded = None
                previous = (
                    publication,
                    (decoded["value"], decoded["last_reset"])
                    if isinstance(decoded, dict)
                    and set(decoded) == {"value", "last_reset"}
                    else None,
                )
                published_state_inputs[unique_id] = previous
            availability = getattr(self, "_published_availability", {})
            if previous[1] != state_inputs or availability.get(unique_id, (None, None))[
                1
            ] != ("online" if available else "offline"):
                # Keep existing queue order. A still-changing completed sensor
                # rejoins at the tail instead of starving later dirty sensors.
                priority.setdefault(unique_id, entity)
            else:
                priority.pop(unique_id, None)

        try:  # ruff: ignore[too-many-nested-blocks]
            self._schedule_shared_telemetry()
            while remaining or inflight:
                if (
                    self._stopping
                    or not self._is_current_owner()
                    or generation != self._broker_generation
                ):
                    break
                # Clear before scanning so a later callback cannot be lost.
                # Full cached-state scans run once per coalesced notification,
                # not once per individual sensor publication.
                scan_all = scan_all or wakeup.is_set()
                wakeup.clear()
                busy = set(inflight.values())
                if scan_all:
                    for index, (unique_id, entity) in enumerate(
                        tuple(self._entities.items()), start=1
                    ):
                        queue_changed(unique_id, entity)
                        if index % _SENSOR_SCAN_YIELD_BATCH == 0:
                            # A large installation can have hundreds of
                            # entities. Preserve every scan while giving live
                            # ingress and timers a turn between small batches.
                            await asyncio.sleep(0)
                            if (
                                self._stopping
                                or not self._is_current_owner()
                                or generation != self._broker_generation
                            ):
                                break
                    scan_all = False
                    if (
                        self._stopping
                        or not self._is_current_owner()
                        or generation != self._broker_generation
                    ):
                        break
                if remaining and len(inflight) < _PUBLISH_CONCURRENCY:
                    while len(inflight) < _PUBLISH_CONCURRENCY:
                        # After three live choices, reserve a slot for the
                        # remaining discovery work even under constant updates.
                        ready = next(
                            (
                                unique_id
                                for unique_id in priority
                                if unique_id not in busy
                            ),
                            None,
                        )
                        if ready is not None and (
                            not background or priority_streak < _PUBLISH_CONCURRENCY - 1
                        ):
                            unique_id = ready
                            entity = priority.pop(unique_id)
                            background.pop(unique_id, None)
                            priority_streak += 1
                        elif background:
                            unique_id = next(iter(background))
                            entity = background.pop(unique_id)
                            priority_streak = 0
                        else:
                            break
                        if unique_id in busy:
                            continue
                        if (
                            entity is None
                            or self._entities.get(unique_id) is not entity
                        ):
                            remaining.discard(unique_id)
                            continue
                        task = asyncio.create_task(
                            self._async_publish_entity(
                                unique_id, cast("_EntityDeviceLike", entity)
                            )
                        )
                        inflight[task] = unique_id
                        busy.add(unique_id)
                if not inflight:
                    break
                waiter = asyncio.create_task(wakeup.wait())
                await asyncio.wait(
                    (*inflight, waiter), return_when=asyncio.FIRST_COMPLETED
                )
                if not waiter.done():
                    waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
                waiter = None
                for task in tuple(inflight):
                    if task.done():
                        unique_id = inflight.pop(task)
                        task.result()
                        remaining.discard(unique_id)
                        entity = self._entities.get(unique_id)
                        if entity is not None:
                            queue_changed(unique_id, entity)
                # When the original sweep completes, join its bounded live
                # work and return. The outer pending pass then checks metadata
                # and schedules another complete diagnostic snapshot.
        finally:
            if waiter is not None:
                waiter.cancel()
            for task in inflight:
                task.cancel()
            await asyncio.gather(
                *inflight,
                *((waiter,) if waiter is not None else ()),
                return_exceptions=True,
            )
            if getattr(self, "_sensor_publish_wakeup", None) is wakeup:
                self._sensor_publish_wakeup = None
            if generation != self._broker_generation:
                self._invalidate_published_snapshots()

    @callback
    def _schedule_shared_telemetry(self) -> None:
        """Coalesce complete exports independently of individual sensor values."""
        if self._stopping or not self._is_current_owner():
            return
        self._shared_telemetry_pending = True
        task = self._shared_telemetry_task
        if (
            task is not None and not task.done()
        ) or self._shared_telemetry_retry_unsubscribe is not None:
            return
        self._shared_telemetry_task = self._hass.async_create_background_task(
            self._async_shared_telemetry_loop(),
            name=f"{DOMAIN}_mqtt_telemetry_export_{self._entry_id}",
            eager_start=False,
        )

    @callback
    def _async_retry_shared_telemetry(self, _now: datetime) -> None:
        """Retry the complete export after a transient broker or export error."""
        self._shared_telemetry_retry_unsubscribe = None
        if self._shared_telemetry_pending:
            self._schedule_shared_telemetry()

    async def _async_shared_telemetry_loop(self) -> None:
        """Publish full diagnostics without delaying independent mirror states."""
        retry = False
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            while self._shared_telemetry_pending and not self._stopping:
                self._shared_telemetry_pending = False
                if not await self._async_ensure_mqtt_connected():
                    self._shared_telemetry_pending = True
                    retry = True
                    break
                await self._async_publish_shared_telemetry()
        except asyncio.CancelledError:
            raise
        except Exception as err:  # ruff: ignore[blind-except]
            self._shared_telemetry_pending = True
            retry = True
            self._log_unexpected_publish_error(err)
        finally:
            self._shared_telemetry_task = None
            if retry and not self._stopping and self._is_current_owner():
                self._shared_telemetry_retry_unsubscribe = async_call_later(
                    self._hass,
                    _PUBLISH_RETRY_DELAY_SEC,
                    self._async_retry_shared_telemetry,
                )

    def _shared_telemetry_document(
        self,
        entities: tuple[tuple[str, _EntityDeviceLike], ...],
        *,
        source_receipts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Freeze the full export independently of per-sensor state topics."""
        entry = self._hass.config_entries.async_get_entry(self._entry_id)
        coordinator = getattr(entry, "runtime_data", None)
        source_export = getattr(coordinator, "layer_source_diagnostics", None)
        snapshot: dict[str, Any] = {
            "schema": MQTT_TELEMETRY_SCHEMA,
            "origin": {"domain": DOMAIN, "entry_id": self._entry_id},
            "sensors": {},
            "source_receipts": (
                source_receipts
                if source_receipts is not None
                else source_export()
                if callable(source_export)
                else []
            ),
        }
        for unique_id, entity in entities:
            try:
                value = entity.native_value
                available = bool(getattr(entity, "available", True))
            except Exception:
                _LOGGER.debug(
                    "Unreadable Jackery MQTT sensor %s", unique_id, exc_info=True
                )
                value, available = None, False
            snapshot["sensors"][unique_id] = {
                "value": _state_payload(value) if value is not None else None,
                "availability": "online" if available else "offline",
                "last_reset": _reset_timestamp(entity),
            }
        return snapshot

    async def _async_shared_telemetry_document(
        self,
        entities: tuple[tuple[str, _EntityDeviceLike], ...],
        *,
        source_receipts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Build the complete document in cooperative sensor batches."""
        snapshot = self._shared_telemetry_document(
            entities[:_SENSOR_SCAN_YIELD_BATCH], source_receipts=source_receipts
        )
        for offset in range(
            _SENSOR_SCAN_YIELD_BATCH, len(entities), _SENSOR_SCAN_YIELD_BATCH
        ):
            await asyncio.sleep(0)
            if self._stopping or not self._is_current_owner():
                return None
            batch = self._shared_telemetry_document(
                entities[offset : offset + _SENSOR_SCAN_YIELD_BATCH],
                source_receipts=snapshot["source_receipts"],
            )
            snapshot["sensors"].update(batch["sensors"])
        return snapshot

    async def _async_prepare_shared_snapshot(
        self, shared: _SharedTelemetry
    ) -> tuple[str, int, tuple[tuple[str, _EntityDeviceLike], ...]] | None:
        """Prepare a source-complete snapshot for all active entries."""
        for attempt in range(_MAX_SNAPSHOT_ATTEMPTS):
            revision = shared.revision
            snapshots: dict[str, dict[str, Any]] = {}
            flags: dict[str, bool] = {}
            own_entities: tuple[tuple[str, _EntityDeviceLike], ...] = ()
            for entry_id, publisher in sorted(shared.publishers.items()):
                if publisher._stopping or not publisher._is_current_owner():  # ruff: ignore[private-member-access]
                    continue
                entry = self._hass.config_entries.async_get_entry(entry_id)
                coordinator = getattr(entry, "runtime_data", None)
                async_export = getattr(
                    coordinator, "async_layer_source_diagnostics", None
                )
                receipts = await async_export() if callable(async_export) else None
                if publisher._stopping or not publisher._is_current_owner():  # ruff: ignore[private-member-access]
                    continue
                entities = tuple(publisher._entities.items())  # ruff: ignore[private-member-access]
                snapshot = await publisher._async_shared_telemetry_document(  # ruff: ignore[private-member-access]
                    entities, source_receipts=receipts
                )
                if snapshot is None:
                    continue
                snapshots[entry_id] = snapshot
                flags[entry_id] = jackery_dev_mode_enabled(entry)
                if publisher is self:
                    own_entities = entities
            if self._entry_id not in snapshots:
                return None
            # A concurrent update already invalidated this attempt while its
            # receipts were copied. Retry before serializing the complete
            # document; the final bounded attempt still publishes and queues
            # the latest revision in the normal way.
            if shared.revision != revision and attempt + 1 < _MAX_SNAPSHOT_ATTEMPTS:
                continue
            payload = await self._hass.async_add_executor_job(
                _serialize_shared_telemetry, snapshots, flags
            )
            if any(
                flags[entry_id]
                != jackery_dev_mode_enabled(
                    self._hass.config_entries.async_get_entry(entry_id)
                )
                for entry_id in flags
            ):
                if attempt + 1 == _MAX_SNAPSHOT_ATTEMPTS:
                    self._shared_telemetry_pending = True
                    return None
                continue
            if shared.revision == revision or attempt + 1 == _MAX_SNAPSHOT_ATTEMPTS:
                return payload, revision, own_entities
        return None

    async def _async_publish_shared_telemetry(
        self,
    ) -> tuple[tuple[str, _EntityDeviceLike], ...] | None:
        """Publish one coherent retained diagnostic snapshot."""
        shared = self._shared_telemetry
        if self._stopping or shared.publishers.get(self._entry_id) is not self:
            return None
        shared.snapshot_sequence = getattr(shared, "snapshot_sequence", 0) + 1
        sequence = shared.snapshot_sequence
        owners = tuple(shared.publishers.items())
        # Capturing/serializing raw receipts can be slow. Do not hold the
        # retained-publication lock while that independent work is in flight.
        prepared = await self._async_prepare_shared_snapshot(shared)
        if prepared is None:
            return None
        payload, revision, entities = prepared
        async with getattr(shared, "telemetry_lock", shared.lock):
            if self._stopping or shared.publishers.get(self._entry_id) is not self:
                return None
            if (
                sequence < getattr(shared, "published_snapshot_sequence", 0)
                or tuple(shared.publishers.items()) != owners
            ):
                # A co-owner may have retired while the raw snapshot was built.
                self._shared_telemetry_pending = True
                return entities
            if payload != shared.payload:
                broker_generation = shared.broker_generation
                own_wire = (
                    (self._telemetry_topic, payload.encode())
                    if isinstance(payload, _OwnTelemetryText)
                    else None
                )
                shared.pending_own_wire = own_wire
                publish_reported_timeout = False

                async def publish_snapshot() -> None:
                    nonlocal publish_reported_timeout
                    try:
                        await self._async_publish(self._telemetry_topic, payload)
                    except TimeoutError:
                        # _async_publish already recorded its own timeout.
                        publish_reported_timeout = True
                        raise
                    # The shielded write can finish after owner cancellation.
                    # Retain its confirmed raw snapshot for final retirement.
                    shared.last_payload = payload

                try:
                    await _finish_retained_publish(publish_snapshot())
                except TimeoutError as err:
                    # The outer retained-write deadline can cancel the inner
                    # publisher first, before its own timeout records context.
                    if not publish_reported_timeout:
                        self._record_publish_failure(self._telemetry_topic, err)
                    raise
                finally:
                    shared.pending_own_wire = None
                if (
                    shared.broker_generation == broker_generation
                    and shared.publishers.get(self._entry_id) is self
                    and not self._stopping
                ):
                    shared.payload = payload
                    shared.last_payload = payload
                    shared.confirmed_own_wire = own_wire
                    shared.published_snapshot_sequence = sequence
                else:
                    shared.payload = None
                    self._shared_telemetry_pending = True
                    return None
            self._telemetry_payload = payload
            if payload == shared.payload:
                shared.published_snapshot_sequence = sequence
            if shared.revision != revision:
                self._shared_telemetry_pending = True

        return entities

    async def _async_publish_entity(  # ruff: ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]
        self, unique_id: str, entity: _EntityDeviceLike
    ) -> None:
        """Update shared snapshot inputs and publish this sensor's discovery."""
        generation = self._broker_generation
        state_topic, availability_topic, config_topic = self._sensor_topics(unique_id)
        shared = getattr(self, "_shared_telemetry", None)
        state_payload: str | None = None
        state_inputs: tuple[Any, Any] | None = None
        if published := self._published_states.get(unique_id):
            cache = getattr(self, "_decoded_published_states", None)
            if cache is None:
                cache = self._decoded_published_states = {}
            cached = cache.get(unique_id)
            if cached is None or cached[0] != published[1]:
                try:
                    decoded = json.loads(published[1])
                except TypeError, json.JSONDecodeError:
                    decoded = None
                inputs = (
                    (decoded["value"], decoded["last_reset"])
                    if isinstance(decoded, dict)
                    and set(decoded) == {"value", "last_reset"}
                    else None
                )
                cached = cache[unique_id] = (published[1], inputs)
            if cached[1] is not None:
                state_payload = published[1]
                state_inputs = cached[1]

        def current_entity() -> bool:
            return (
                not self._stopping
                and self._is_current_owner()
                and generation == self._broker_generation
                and self._entities.get(unique_id) is entity
            )

        def refresh_native() -> None:
            if self._stopping or not self._is_current_owner():
                return
            pending = getattr(self, "_native_refresh_pending", None)
            if pending is None:
                pending = self._native_refresh_pending = set()
            if unique_id in self._entities:
                pending.add(unique_id)
                self.async_schedule_publish()

        def snapshot() -> tuple[Any, ...]:
            nonlocal state_payload, state_inputs
            try:
                value = entity.native_value
                available = bool(getattr(entity, "available", True))
            except Exception:
                _LOGGER.debug(
                    "Unreadable Jackery MQTT sensor %s", unique_id, exc_info=True
                )
                value, available = None, False
            reset = _reset_timestamp(entity)
            device_id, device = _device_config(entity)
            if device["name"] == f"Jackery {device_id}" and (
                native := dr.async_get(self._hass).async_get_device_by_identifier(
                    (DOMAIN, device_id), self._entry_id
                )
            ):
                device["name"] = native.name_by_user or native.name or device["name"]
            config = self._registered_discovery_config(
                entity,
                getattr(entity, "entity_description", None),
                topics=(state_topic, availability_topic),
                unique_id=unique_id,
                device=device,
            )
            # The state encoder returns strings, so its comparison inputs do
            # not retain a mutable native mapping. Metadata is encoded fresh.
            current_inputs = (_state_payload(value), reset)
            if state_payload is None or current_inputs != state_inputs:
                state_payload = json.dumps({
                    "value": current_inputs[0],
                    "last_reset": current_inputs[1],
                })
                state_inputs = current_inputs
            coordinator = getattr(entity, "coordinator", None)
            parent_id = getattr(entity, "_device_id", None)
            resolve_serial = getattr(coordinator, "_resolve_device_sn", None)
            cached_attributes = getattr(entity, "_cached_attrs", {})
            energy_owner = (
                cached_attributes.get("energy_owner")
                if isinstance(cached_attributes, dict)
                else None
            )
            owner = (
                id(coordinator),
                id(
                    getattr(
                        getattr(coordinator, "entry", None), "runtime_data", coordinator
                    )
                ),
                resolve_serial(parent_id) if callable(resolve_serial) else None,
                getattr(coordinator, "_http_parent_identity_epochs", {}).get(parent_id),
                getattr(coordinator, "_system_binding_epochs", {}).get(parent_id),
                json.dumps(energy_owner, sort_keys=True, default=str),
            )
            return (
                state_payload,
                "online" if available else "offline",
                (config_topic, json.dumps(config)),
                owner,
                reset,
            )

        latest: tuple[Any, ...] | None = None

        def source_still_current(captured: tuple[Any, ...]) -> bool:
            nonlocal latest
            if not current_entity():
                refresh_native()
                return False
            latest = snapshot()
            if latest != captured:
                refresh_native()
            # Ordinary newer readings still need discovery. Changed ownership,
            # availability, reset or metadata must not finish the old frame.
            return latest[1:] == captured[1:]

        async def publish_value(
            publication: tuple[str, str],
            cache: dict[str, tuple[str, str]],
            captured: tuple[Any, ...],
        ) -> bool:
            if not current_entity():
                refresh_native()
                return False
            if cache.get(unique_id) == publication:
                return True
            await _finish_retained_publish(self._async_publish(*publication))
            if not source_still_current(captured):
                return False
            cache[unique_id] = publication
            return True

        state_lock = (
            _topic_publish_lock(shared, state_topic) if shared is not None else None
        )
        config_lock = (
            _topic_publish_lock(shared, config_topic) if shared is not None else None
        )

        def lock_is_idle(lock: asyncio.Lock | None) -> bool:
            if lock is None:
                return True
            # asyncio may wake a waiter after releasing the lock but before
            # that waiter resumes and marks it locked again. Do not bypass it.
            waiters = getattr(lock, "_waiters", None)
            return hasattr(lock, "_waiters") and not lock.locked() and not waiters

        # No retained write or lock wait is needed when every publication is
        # already confirmed and neither topic has an in-flight writer. Check
        # the actual current metadata once: state alone is insufficient.
        if (
            unique_id in self._published_states  # ruff: ignore[too-many-boolean-expressions]
            and unique_id in self._published_availability
            and unique_id in self._published_configs
            and current_entity()
            and lock_is_idle(state_lock)
            and lock_is_idle(config_lock)
        ):
            unchanged = snapshot()
            if (
                self._published_states.get(unique_id) == (state_topic, unchanged[0])
                and self._published_availability.get(unique_id)
                == (availability_topic, unchanged[1])
                and self._published_configs.get(unique_id) == unchanged[2]
            ):
                self._owned_sensor_topics.update((
                    state_topic,
                    availability_topic,
                    config_topic,
                ))
                getattr(self, "_native_refresh_pending", set()).discard(unique_id)
                return

        async with state_lock if state_lock is not None else contextlib.nullcontext():
            if not current_entity():
                refresh_native()
                return
            # Resolve the native value only after prior same-topic writes drain.
            captured = latest = snapshot()
            self._owned_sensor_topics.update((
                state_topic,
                availability_topic,
                config_topic,
            ))
            # Confirm each small retained value before exposing its discovery.
            if not await publish_value(
                (state_topic, captured[0]), self._published_states, captured
            ):
                return
            if not await publish_value(
                (availability_topic, captured[1]),
                self._published_availability,
                captured,
            ):
                return
            async with (
                config_lock if config_lock is not None else contextlib.nullcontext()
            ):
                if not source_still_current(captured):
                    return
                publishers = getattr(shared, "publishers", {})
                owners = [
                    entry_id
                    for entry_id, publisher in publishers.items()
                    if unique_id in getattr(publisher, "_entities", {})
                    and not getattr(publisher, "_stopping", False)
                ]
                if not owners or self._entry_id == min(owners):  # ruff: ignore[collapsible-if]
                    if not await publish_value(
                        captured[2], self._published_configs, captured
                    ):
                        return
            if latest == captured:
                getattr(self, "_native_refresh_pending", set()).discard(unique_id)

    def _registered_discovery_config(
        self,
        entity: _EntityDeviceLike,
        description: _DescriptionLike | None,
        *,
        unique_id: str,
        topics: tuple[str, str],
        device: dict[str, Any],
    ) -> dict[str, Any]:
        """Bind the discovery document to its active or restorable history ID."""
        config = self._discovery_config(
            entity,
            description,
            unique_id=unique_id,
            topics=topics,
            device=device,
            entry_id=self._entry_id,
        )
        if topics[0] == _TELEMETRY_TOPIC:
            prefix = _entry_snapshot_prefix(unique_id, self._entry_id)
            device_class = config.get("device_class")
            numeric = (
                "unit_of_measurement" in config
                or "state_class" in config
                or device_class not in {None, "date", "enum", "timestamp", "uptime"}
            )
            previous = (
                "''"
                if numeric
                else "(this.state if this is defined and this.state not in "
                "('unknown', 'unavailable') else none)"
            )
            config["value_template"] = prefix + (
                "{{ (e.get('value') if e is mapping else none) if s is mapping else "
                + previous
                + " }}"
            )
            config["availability_template"] = prefix + (
                "{{ (e.get('availability', 'offline') if e is mapping else 'offline') "
                "if s is mapping else '' }}"
            )
            if "last_reset_value_template" in config:
                config["last_reset_value_template"] = prefix + (
                    "{{ (e.get('last_reset') or '') if e is mapping else '' }}"
                )
        registry = er.async_get(self._hass)
        mirror_unique_id = f"{_MIRROR_UNIQUE_ID_PREFIX}{unique_id}"
        if entity_id := registry.async_get_entity_id(
            "sensor", "mqtt", mirror_unique_id
        ):
            config["default_entity_id"] = entity_id
            if options := config.get("options"):
                state = self._hass.states.get(entity_id)
                if (
                    state is not None
                    and not state.attributes.get("options")
                    and state.state not in options
                ):
                    # Old templates could store "unavailable" as a literal
                    # enum value while no options existed. HA validates the
                    # existing value immediately on a discovery update, before
                    # the replacement template necessarily receives telemetry.
                    # Update that template first; add the strict options after
                    # a real allowed value has replaced the old placeholder.
                    config.pop("options")
                    self._defer_enum_options(entity_id, tuple(options))
                else:
                    self._forget_enum_migration(entity_id)
        elif deleted := registry.deleted_entities.get((
            "sensor",
            "mqtt",
            mirror_unique_id,
        )):
            config["default_entity_id"] = deleted.entity_id
        return config

    @callback
    def _defer_enum_options(self, entity_id: str, options: tuple[str, ...]) -> None:
        """Wait for one legacy MQTT mirror to leave its old placeholder value."""
        self._enum_migrations[entity_id] = options
        if self._enum_migration_unsubscribe is None:
            self._enum_migration_unsubscribe = self._hass.bus.async_listen(
                EVENT_STATE_CHANGED, self._async_enum_migration_state_changed
            )

    @callback
    def _forget_enum_migration(self, entity_id: str) -> None:
        """Remove a completed migration and its temporary listener."""
        self._enum_migrations.pop(entity_id, None)
        if not self._enum_migrations and self._enum_migration_unsubscribe is not None:
            self._enum_migration_unsubscribe()
            self._enum_migration_unsubscribe = None

    @callback
    def _async_enum_migration_state_changed(self, event: Event[Any]) -> None:
        """Publish strict enum options once the old state is safe to validate."""
        if self._stopping or not self._is_current_owner():
            return
        entity_id = event.data.get("entity_id")
        options = self._enum_migrations.get(entity_id)
        state = event.data.get("new_state")
        if options is not None and state is not None and state.state in options:
            self._forget_enum_migration(entity_id)
            self.async_schedule_publish()

    @staticmethod
    def _discovery_config(  # ruff: ignore[too-many-arguments]
        entity: _EntityDeviceLike,
        description: _DescriptionLike | None,
        *,
        unique_id: str,
        topics: tuple[str, str],
        device: dict[str, Any],
        entry_id: str = "",
    ) -> dict[str, Any]:
        """Build one Home Assistant MQTT sensor discovery document."""
        state_topic = topics[0]
        config: dict[str, Any] = {
            "device": device,
            "enabled_by_default": bool(
                getattr(
                    entity,
                    "_attr_entity_registry_enabled_default",
                    getattr(
                        description,
                        "entity_registry_enabled_default",
                        True,
                    ),
                )
            ),
            "name": _description_name(entity, description, unique_id),
            # Own namespace: a slug equal to the native entity_id (e.g.
            # "Cloud-MQTT", "Firmware-Version") pushed the native sensor to "_2".
            "default_entity_id": "sensor.jackery_mqtt_"
            + slugify(
                f"{device["name"].replace("Zusatzbatterie", "Battery pack")} "
                f"{_legacy_description_name(entity, description, unique_id)}"
            ),
            "origin": {
                "name": "Jackery SolarVault",
                "support_url": ("https://github.com/Bigdaddy1990/jackery_solarvault"),
            },
            "payload_available": "online",
            "payload_not_available": "offline",
            "state_topic": state_topic,
            "availability_topic": topics[1],
            "unique_id": f"{DOMAIN}_mqtt_{unique_id}",
        }
        optional_fields = {
            "device_class": getattr(entity, "device_class", None)
            or getattr(description, "device_class", None),
            "entity_category": getattr(entity, "entity_category", None)
            or getattr(description, "entity_category", None),
            # An explicitly absent live state class is meaningful for an
            # identity-uncertain pack slot; do not restore its static class.
            "state_class": getattr(
                entity, "state_class", getattr(description, "state_class", None)
            ),
            "unit_of_measurement": getattr(entity, "native_unit_of_measurement", None)
            or getattr(description, "native_unit_of_measurement", None),
        }
        for key, raw in optional_fields.items():
            scalar_val = _enum_value(raw)
            if scalar_val is not None:
                config[key] = scalar_val
        if config.get("device_class") == "enum":
            for owner in (entity, description):
                options = getattr(owner, "options", None)
                if (
                    isinstance(options, list | tuple)
                    and options
                    and all(isinstance(option, str) and option for option in options)
                ):
                    config["options"] = list(options)
                    break
        # MQTT retained deletion has an empty payload and no value_json.
        # Keep a real zero/null distinct from a deletion/control message.
        config["value_template"] = (
            "{{ value_json.get('value', '') "
            "if value_json is defined and value_json is mapping else '' }}"
        )
        # Existing MQTT entities may retain the old shared-snapshot template
        # when a discovery update simply omits the availability template.
        config["availability_template"] = "{{ value }}"
        if _reset_timestamp(entity) is not None and (
            _enum_value(config.get("state_class")) == "total"
        ):
            config["last_reset_value_template"] = (
                "{{ (value_json.get('last_reset') or '') "
                "if value_json is defined and value_json is mapping else '' }}"
            )
        return config

    async def _async_publish(self, topic: str, payload: str) -> None:
        """Publish retained discovery state through Home Assistant's broker."""
        try:
            async with asyncio.timeout(_PUBLISH_TIMEOUT_SEC):
                await ha_mqtt.async_publish(
                    self._hass, topic, payload, qos=0, retain=True
                )
        except Exception as err:
            self._record_publish_failure(topic, err)
            raise

    def _record_publish_failure(self, topic: str, err: Exception) -> None:
        """Record the actual topic and operation for a failed publish."""
        counts = getattr(self, "_publish_error_counts", None)
        if not isinstance(counts, dict):
            counts = {}
            self._publish_error_counts = counts
        text = _mirror_error_detail(
            err, self._hass.config_entries.async_get_entry(self._entry_id)
        )
        cause = (type(err).__name__, text)
        if cause not in counts and len(counts) >= _MAX_PUBLISH_ERROR_CAUSES:
            counts.pop(next(iter(counts)))
        counts[cause] = counts.get(cause, 0) + 1
        entity_id = next(
            (
                getattr(entity, "entity_id", unique_id)
                for unique_id, entity in self._entities.items()
                if unique_id in topic.split("/")
            ),
            "untracked",
        )
        self._publish_error_total = getattr(self, "_publish_error_total", 0) + 1
        self._last_publish_error = {
            "topic": topic,
            "operation": "publish",
            "qos": 0,
            "entity": entity_id,
            "error": text,
        }
        log = _LOGGER.warning if counts[cause] == 1 else _LOGGER.debug
        log(
            "Jackery MQTT publish failed: topic=%s operation=publish qos=0 "
            "entity=%s error=%s count=%d",
            topic,
            entity_id,
            text,
            counts[cause],
        )

    def diagnostics_snapshot(self) -> dict[str, Any]:
        """Expose bounded, redacted error evidence for this mirror publisher."""
        return {
            "publish_errors": getattr(self, "_publish_error_total", 0),
            "last_publish_error": getattr(self, "_last_publish_error", None),
        }

    @callback
    def _async_mqtt_connection_state_changed(self, connected: bool) -> None:
        """Retry retained-topic removal when Home Assistant reconnects MQTT."""
        if not connected or not self._cleanup_topics or not self._is_current_owner():
            return
        self._async_schedule_cleanup_worker()

    @callback
    def _async_schedule_cleanup_worker(self) -> None:
        """Start one non-blocking retained-topic cleanup worker."""
        if not self._is_current_owner():
            return
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._cleanup_task = self._hass.async_create_background_task(
            self._async_cleanup_worker(),
            name=f"{DOMAIN}_mqtt_sensor_cleanup_{self._entry_id}",
            eager_start=False,
        )

    async def _async_cleanup_worker(self) -> None:
        """Retry retained cleanup with bounded backoff until it succeeds."""
        attempt = 0
        while self._cleanup_topics and self._is_current_owner():
            delay = _CLEANUP_RETRY_DELAYS_SEC[
                min(attempt, len(_CLEANUP_RETRY_DELAYS_SEC) - 1)
            ]
            await asyncio.sleep(delay)
            if not _broker_connected(self._hass):
                attempt += 1
                continue
            try:
                async with asyncio.timeout(_CLEANUP_TIMEOUT_SEC):
                    await self._async_clear_retained_topics()
            except TimeoutError:
                pass
            attempt += 1

    async def _async_clear_retained_topics(self) -> None:  # ruff: ignore[too-many-branches]
        """Clear known retained topics while preserving failures for retry."""
        async with self._cleanup_lock:
            if not self._is_current_owner():
                return
            for topic in sorted(
                self._cleanup_topics,
                key=lambda item: (not item.endswith("/config"), item),
            ):
                # Do not erase JSON data while a failed discovery deletion
                # leaves its MQTT entity subscribed. Retry the config first.
                if not topic.endswith("/config") and any(
                    item.endswith("/config") for item in self._cleanup_topics
                ):
                    continue
                if topic in {_TELEMETRY_TOPIC, f"{DOMAIN}/telemetry"}:
                    self._cleanup_topics.discard(topic)
                    continue
                if not self._is_current_owner():
                    return
                try:  # ruff: ignore[too-many-statements-in-try-clause]
                    async with _topic_publish_lock(self._shared_telemetry, topic):
                        if _mirror_topic_owned_elsewhere(self._hass, topic):
                            self._cleanup_topics.discard(topic)
                            continue
                        if _mqtt_json_topic_has_active_entity(self._hass, topic):
                            continue
                        await _finish_retained_publish(self._async_publish(topic, ""))
                except Exception as err:  # ruff: ignore[blind-except] - keep cleanup pending
                    if _LOGGER.isEnabledFor(logging.DEBUG):
                        _LOGGER.debug(
                            "Unable to clear retained Jackery MQTT topic %s: %s %s",
                            topic,
                            _mirror_error_detail(
                                err,
                                self._hass.config_entries.async_get_entry(
                                    self._entry_id
                                ),
                            ),
                            _exception_locations(err),
                        )
                else:
                    self._cleanup_topics.discard(topic)
            if self._cleanup_topics:
                return
            self._published_configs.clear()
            self._published_states.clear()
            getattr(self, "_decoded_published_states", {}).clear()
            self._published_availability.clear()
            self._owned_sensor_topics.clear()
            if self._cleanup_unsubscribe is not None:
                self._cleanup_unsubscribe()
                self._cleanup_unsubscribe = None
            if (
                self._owner_bucket is not None
                and self._owner_bucket.get(_PUBLISHER_RUNTIME_KEY) is self
            ):
                self._owner_bucket.pop(_PUBLISHER_RUNTIME_KEY, None)
            self._owns_topics = False

    async def async_shutdown(self) -> None:
        """Remove retained discovery/state topics owned by this config entry."""
        self._stopping = True
        telemetry_retry = getattr(self, "_shared_telemetry_retry_unsubscribe", None)
        if telemetry_retry is not None:
            telemetry_retry()
            self._shared_telemetry_retry_unsubscribe = None
        telemetry_task = getattr(self, "_shared_telemetry_task", None)
        if telemetry_task is not None and not telemetry_task.done():
            telemetry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await telemetry_task
        self._detach_shared_telemetry()
        retirement = getattr(self, "_availability_retirement_task", None)
        if retirement is not None and not retirement.done():
            retirement.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await retirement
        self._cancel_publish_retry()
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for entity in er.async_entries_for_config_entry(
            er.async_get(self._hass), self._entry_id
        ):
            if entity.platform == DOMAIN and entity.entity_id.startswith("sensor."):
                self._owned_sensor_topics.update(self._sensor_topics(entity.unique_id))
        self._cleanup_topics.update({
            *self._owned_sensor_topics,
            *(topic for topic, _payload in self._published_configs.values()),
            *(topic for topic, _payload in self._published_states.values()),
            *(topic for topic, _payload in self._published_availability.values()),
        })
        if self._telemetry_topic is not None:
            self._cleanup_topics.discard(self._telemetry_topic)
        if self._cleanup_topics and self._cleanup_unsubscribe is None:
            self._cleanup_unsubscribe = subscribe_connection_status(
                self._hass,
                self._async_mqtt_connection_state_changed,
                self._entry_id,
            )
        if not self._cleanup_topics or not _broker_connected(self._hass):
            return
        try:
            async with asyncio.timeout(_CLEANUP_TIMEOUT_SEC):
                await self._async_clear_retained_topics()
        except TimeoutError:
            _LOGGER.debug(
                "Timed out clearing retained Jackery MQTT topics during unload; "
                "cleanup will continue in the background"
            )
        if self._cleanup_topics and _broker_connected(self._hass):
            self._async_schedule_cleanup_worker()


def orphaned_mirror_config_topics(hass: HomeAssistant, entry_id: str) -> set[str]:
    """Return retained configs of this entry's mirrors whose native sensor is gone.

    Retired sensors, migrated index-keyed packs and removed cloud-meter
    measurements are never published again, so their retained discovery
    config would keep a frozen MQTT entity forever. Mirrors of devices this
    entry does not own (another instance on a shared broker) stay untouched.
    """
    own_heads = tuple(
        f"{identifier}_"
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id)
        for namespace, identifier in device.identifiers
        if namespace == DOMAIN
    )
    registry = er.async_get(hass)
    topics: set[str] = set()
    for registry_entry in registry.entities.values():
        if registry_entry.platform != "mqtt" or not registry_entry.unique_id.startswith(
            _MIRROR_UNIQUE_ID_PREFIX
        ):
            continue
        native_unique_id = registry_entry.unique_id.removeprefix(
            _MIRROR_UNIQUE_ID_PREFIX
        )
        if (
            native_unique_id.startswith(own_heads)
            and registry.async_get_entity_id("sensor", DOMAIN, native_unique_id) is None
        ):
            object_id = slugify(native_unique_id)
            topics.add(f"{_DISCOVERY_PREFIX}/sensor/{DOMAIN}/{object_id}/config")
    return topics


async def async_release_native_entity_ids(hass: HomeAssistant) -> None:
    """Give native sensors back the entity IDs their MQTT mirrors took.

    Mirrors published before ``default_entity_id`` moved to the ``jackery_mqtt_``
    namespace registered first and pushed the native sensor to ``<id>_2``.
    Registry renames carry the recorder statistics along with each entity.
    """
    registry = er.async_get(hass)
    candidates: list[tuple[er.RegistryEntry, str, str]] = []
    for mirror in list(registry.entities.values()):
        if mirror.platform != "mqtt" or not mirror.unique_id.startswith(
            _MIRROR_UNIQUE_ID_PREFIX
        ):
            continue
        native_id = registry.async_get_entity_id(
            "sensor", DOMAIN, mirror.unique_id.removeprefix(_MIRROR_UNIQUE_ID_PREFIX)
        )
        if native_id is None or native_id != f"{mirror.entity_id}_2":
            continue
        target = f"sensor.jackery_mqtt_{mirror.entity_id.removeprefix("sensor.")}"
        if registry.async_is_registered(target):
            continue
        candidates.append((mirror, native_id, target))
    if not candidates or "recorder" not in hass.data:
        return
    targets = {target for _mirror, _native_id, target in candidates}

    def occupied_history_ids() -> set[str]:
        with session_scope(hass=hass, read_only=True) as session:
            recorded_ids = set(
                session.scalars(
                    select(StatesMeta.entity_id).where(
                        StatesMeta.entity_id.in_(targets)
                    )
                )
            ) | set(
                session.scalars(
                    select(StatisticsMeta.statistic_id).where(
                        StatisticsMeta.statistic_id.in_(targets)
                    )
                )
            )
            return {entity_id for entity_id in recorded_ids if entity_id is not None}

    try:
        async with asyncio.timeout(_REGISTRY_HISTORY_TIMEOUT_SEC):
            occupied = await get_recorder_instance(hass).async_add_executor_job(
                occupied_history_ids
            )
    except (TimeoutError, SQLAlchemyError) as err:
        _LOGGER.debug("Keeping Jackery entity IDs: history check unavailable: %s", err)
        return
    for mirror, native_id, target in candidates:
        if (
            target in occupied
            or registry.async_is_registered(target)
            or not all(
                hass.states.async_available(entity_id)
                for entity_id in (mirror.entity_id, native_id, target)
            )
            or registry.async_get(mirror.entity_id) is not mirror
            or registry.async_get_entity_id(
                "sensor",
                DOMAIN,
                mirror.unique_id.removeprefix(_MIRROR_UNIQUE_ID_PREFIX),
            )
            != native_id
        ):
            continue
        registry.async_update_entity(mirror.entity_id, new_entity_id=target)
        registry.async_update_entity(native_id, new_entity_id=mirror.entity_id)
        _LOGGER.info(
            "Moved Jackery MQTT mirror %s to %s and restored native %s",
            mirror.entity_id,
            target,
            mirror.entity_id,
        )


def _mqtt_json_topic_has_active_entity(hass: HomeAssistant, topic: str) -> bool:
    """Keep retained JSON until local discovery removal has completed.

    Queuing a discovery tombstone does not synchronously unsubscribe its
    entity. An empty state/attributes message during that interval is invalid
    input for legacy JSON templates and attributes consumers.
    """
    parts = topic.split("/")
    if (
        len(parts) != 5  # ruff: ignore[magic-value-comparison]
        or parts[0] != _STATE_PREFIX
        or parts[2] not in {"sensor", "update"}
        or parts[4] not in {"state", "attributes"}
    ):
        return False
    unique_id = f"{_MIRROR_UNIQUE_ID_PREFIX}{unquote(parts[3])}"
    entity_id = er.async_get(hass).async_get_entity_id(parts[2], "mqtt", unique_id)
    return entity_id is not None and hass.states.get(entity_id) is not None


def _mirror_topic_owned_elsewhere(hass: HomeAssistant, topic: str) -> bool:
    """Fence delayed removal against a replacement or another live entry."""
    # Both locations contain complete raw receipts. Keep the documented
    # export and the previously used export even after the final entry leaves.
    if topic in {_TELEMETRY_TOPIC, f"{DOMAIN}/telemetry"}:
        return True
    shared = hass.data.get(DOMAIN, {}).get(_SHARED_TELEMETRY_RUNTIME_KEY)
    publishers = getattr(shared, "publishers", {})
    sensor_owned = any(
        topic in publisher._owned_sensor_topics  # ruff: ignore[private-member-access]
        for publisher in publishers.values()
    )
    if sensor_owned:
        return True
    for bucket in hass.data.get(DOMAIN, {}).values():
        if not isinstance(bucket, dict):
            continue
        publisher = bucket.get("mqtt_update_publisher")
        if publisher is None or getattr(publisher, "stopping", True):
            continue
        for unique_id in publisher.entities:
            state, availability, _command, discovery = _update_topics_for_entry(
                publisher.entry.entry_id, unique_id
            )
            if topic in {
                state,
                availability,
                discovery,
                state.rsplit("/", 1)[0] + "/attributes",
            }:
                return True
    return False


@callback
def async_remove_entry_mirrors(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Queue only this removed entry's known retained topics for deletion."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    publisher = runtime.get(_PUBLISHER_RUNTIME_KEY)
    pending = set(getattr(publisher, "_owned_sensor_topics", ()))
    update_publisher = runtime.get("mqtt_update_publisher")
    if update_publisher is not None:
        update_publisher.retire()
    retired_update_publisher = runtime.pop("mqtt_update_retired", None)
    if retired_update_publisher is not None:
        retired_update_publisher.cancel_retirement()
    retire = getattr(publisher, "async_retire", None)
    if callable(retire):
        retire()
    availability_task = getattr(publisher, "_availability_retirement_task", None)
    if availability_task is not None and not availability_task.done():
        availability_task.cancel()
    for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        if entity.platform == DOMAIN and entity.entity_id.startswith("sensor."):
            pending.update(_sensor_topics_for_entry(entry.entry_id, entity.unique_id))
        elif entity.platform == DOMAIN and entity.entity_id.startswith("update."):
            state, availability, _command, discovery = _update_topics_for_entry(
                entry.entry_id, entity.unique_id
            )
            pending.update((
                state,
                availability,
                discovery,
                state.rsplit("/", 1)[0] + "/attributes",
            ))
    pending.discard(_TELEMETRY_TOPIC)
    pending.discard(f"{DOMAIN}/telemetry")
    if "mqtt" not in hass.data:
        _LOGGER.warning(
            "Cannot clear %d retained Jackery MQTT topics: MQTT is not configured",
            len(pending),
        )
        return
    # The entry is already unloaded; its task/unload hooks cannot own this job.
    async_start_mqtt_discovery_cleanup(
        hass, entry, pending_topics=pending, entry_removed=True
    )


async def _async_clear_topics(
    hass: HomeAssistant,
    pending: set[str],
    *,
    entry: ConfigEntry | None = None,
    entry_removed: bool = False,
) -> int:
    """Publish retained tombstones and leave failures pending for retry."""
    removed = 0
    for topic in sorted(pending, key=lambda item: (not item.endswith("/config"), item)):
        # Keep state/attribute deletion pending until all discovery removals
        # succeed. An empty state is not a valid JSON sensor measurement.
        if not topic.endswith("/config") and any(
            item.endswith("/config") for item in pending
        ):
            continue
        # Native registration can rescue a mirror while an earlier publish awaits.
        if topic not in pending:
            continue
        if topic in {_TELEMETRY_TOPIC, f"{DOMAIN}/telemetry"}:
            pending.discard(topic)
            continue
        shared = hass.data.get(DOMAIN, {}).get(_SHARED_TELEMETRY_RUNTIME_KEY)
        lock = _topic_publish_lock(shared, topic) if shared is not None else None
        # Sensor tombstones remain ordered against replacement publishers.
        async with lock if lock is not None else contextlib.nullcontext():
            if topic not in pending:
                continue
            if _mirror_topic_owned_elsewhere(hass, topic):
                pending.discard(topic)
                continue
            if _mqtt_json_topic_has_active_entity(hass, topic):
                continue
            if not _broker_connected(hass):
                break
            try:
                async with asyncio.timeout(_PUBLISH_TIMEOUT_SEC):
                    await _finish_retained_publish(
                        ha_mqtt.async_publish(hass, topic, "", qos=0, retain=True)
                    )
            except (HomeAssistantError, OSError, TimeoutError) as err:
                _record_mqtt_operation_error(
                    hass, topic, "cleanup_publish", "obsolete_mirror", err, entry=entry
                )
            else:
                pending.discard(topic)
                removed += 1
        await asyncio.sleep(0.01)
    return removed


@callback
def async_start_mqtt_discovery_cleanup(  # ruff: ignore[complex-structure, too-many-statements]
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    pending_topics: set[str] | None = None,
    entry_removed: bool = False,
) -> Callable[[], None]:
    """Remove obsolete retained mirrors without blocking entry setup."""
    cleanup_task: asyncio.Task[None] | None = None
    connection_unsubscribe: Callable[[], None] | None = None
    native_registry_unsubscribe: Callable[[], None] | None = None
    retry_unsubscribe: Callable[[], None] | None = None
    pending = set(pending_topics or ())
    stopped = False

    async def _async_cleanup() -> None:
        nonlocal \
            cleanup_task, \
            connection_unsubscribe, \
            retry_unsubscribe, \
            native_registry_unsubscribe
        try:
            if stopped or not _broker_connected(hass):
                return
            if pending_topics is None:
                # A formerly missing sensor may have been registered since setup.
                # Never retain a stale deletion across a delayed retry.
                pending.clear()
                pending.update(orphaned_mirror_config_topics(hass, entry.entry_id))
            # This task already runs in the background and yields after each
            # bounded publish. A whole-task timeout repeatedly aborted valid work.
            removed = await _async_clear_topics(
                hass, pending, entry=entry, entry_removed=entry_removed
            )
            if removed:
                _LOGGER.info("Removed %d obsolete Jackery MQTT sensor mirrors", removed)
            if pending:
                if _broker_connected(hass):
                    _LOGGER.warning(
                        "%d obsolete Jackery MQTT discovery topics remain; "
                        "cleanup will retry in %.0fs or after MQTT reconnect",
                        len(pending),
                        _PUBLISH_RETRY_DELAY_SEC,
                    )
                    retry_unsubscribe = async_call_later(
                        hass, _PUBLISH_RETRY_DELAY_SEC, lambda _now: _schedule_cleanup()
                    )
                else:
                    _LOGGER.warning(
                        "%d obsolete Jackery MQTT discovery topics remain; "
                        "cleanup paused until MQTT reconnect",
                        len(pending),
                    )
            else:
                if connection_unsubscribe is not None:
                    connection_unsubscribe()
                    connection_unsubscribe = None
                if native_registry_unsubscribe is not None:
                    native_registry_unsubscribe()
                    native_registry_unsubscribe = None
        finally:
            cleanup_task = None

    @callback
    def _schedule_cleanup() -> None:
        nonlocal cleanup_task, retry_unsubscribe
        if stopped or (cleanup_task is not None and not cleanup_task.done()):
            return
        if retry_unsubscribe is not None:
            retry_unsubscribe()
            retry_unsubscribe = None
        name = f"{DOMAIN}_mqtt_discovery_cleanup_{entry.entry_id}"
        if entry_removed:
            cleanup_task = hass.async_create_background_task(
                _async_cleanup(), name=name, eager_start=False
            )
        else:
            cleanup_task = entry.async_create_background_task(
                hass, _async_cleanup(), name=name, eager_start=False
            )

    @callback
    def _connection_state_changed(connected: bool) -> None:
        if connected:
            _schedule_cleanup()

    @callback
    def _native_registered(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        entity_id = event.data.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id.startswith("sensor."):
            return
        registered = er.async_get(hass).async_get(entity_id)
        if (
            registered is not None
            and registered.platform == DOMAIN
            and registered.config_entry_id == entry.entry_id
        ):
            pending.discard(
                _sensor_topics_for_entry(entry.entry_id, registered.unique_id)[2]
            )

    connection_unsubscribe = subscribe_connection_status(
        hass,
        _connection_state_changed,
        entry.entry_id,
    )
    if not entry_removed:
        native_registry_unsubscribe = hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, _native_registered
        )
    if _broker_connected(hass):
        _schedule_cleanup()

    @callback
    def _stop_cleanup() -> None:
        nonlocal \
            stopped, \
            cleanup_task, \
            connection_unsubscribe, \
            retry_unsubscribe, \
            native_registry_unsubscribe
        stopped = True
        if retry_unsubscribe is not None:
            retry_unsubscribe()
            retry_unsubscribe = None
        if connection_unsubscribe is not None:
            connection_unsubscribe()
            connection_unsubscribe = None
        if native_registry_unsubscribe is not None:
            native_registry_unsubscribe()
            native_registry_unsubscribe = None
        if cleanup_task is not None and not cleanup_task.done():
            cleanup_task.cancel()
        cleanup_task = None

    return _stop_cleanup
