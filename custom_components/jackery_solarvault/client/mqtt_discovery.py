"""Publish native Jackery sensor values through Home Assistant MQTT discovery."""

import asyncio
from collections import deque
from collections.abc import Callable, Iterable, Mapping
import contextlib
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import json
import logging
from time import monotonic
from typing import Any

from homeassistant.components import mqtt
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_STATE_CHANGED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
from homeassistant.util import slugify

from ..const import DOMAIN, MANUFACTURER
from ..util import normalize_mac_address

_LOGGER = logging.getLogger(__name__)
_DISCOVERY_PREFIX = "homeassistant"
_STATE_PREFIX = DOMAIN
_CLEANUP_TIMEOUT_SEC = 1.0
_CLEANUP_RETRY_DELAYS_SEC = (1.0, 5.0, 30.0)
_CLEANUP_MAX_ATTEMPTS = 3
_PUBLISH_RETRY_DELAYS_SEC = (0.25, 1.0, 5.0, 30.0)
_PUBLISH_TASK_START_RETRY_SEC = 1.0
_RELOAD_HANDOFF_GRACE_SEC = 5.0
_PUBLISHER_RUNTIME_KEY = "mqtt_sensor_publisher"
_CT_DISCOVERY_NAMES = {
    "smart_meter_phase_1_power": "CT Phase A",
    "smart_meter_phase_2_power": "CT Phase B",
    "smart_meter_phase_3_power": "CT Phase C",
    "smart_meter_power": "CT Phase T",
}


def _enum_value(value: Any) -> Any:
    """Return a JSON/MQTT scalar for Home Assistant enums."""
    return value.value if isinstance(value, Enum) else value


def _state_payload(value: Any) -> str:
    """Serialize one native sensor value without inventing an unknown marker."""
    value = _enum_value(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, dict | list | tuple):
        return json.dumps(value, separators=(",", ":"), default=str)
    return str(value)


def _device_config(entity: Any) -> tuple[str, dict[str, Any]]:
    """Build a JSON-safe MQTT device block and return its stable identifier."""
    raw = getattr(entity, "device_info", None)
    info: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    identifiers: list[str] = []
    device_id = "unknown"
    for identifier in info.get("identifiers", ()) or ():
        if isinstance(identifier, tuple) and len(identifier) == 2:
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
    connections: list[list[str]] = []
    for connection in info.get("connections", ()) or ():
        if not isinstance(connection, tuple) or len(connection) != 2:
            continue
        connection_type = str(connection[0])
        connection_id = str(connection[1])
        if connection_type == dr.CONNECTION_NETWORK_MAC:
            normalized = normalize_mac_address(connection_id)
            if normalized is None:
                continue
            connection_id = normalized
        if connection_id.strip():
            connections.append([connection_type, connection_id])
    if connections:
        config["connections"] = sorted(connections)
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
    if isinstance(via_device, tuple) and len(via_device) == 2:
        config["via_device"] = f"{via_device[0]}:{via_device[1]}"
    elif via_device:
        config["via_device"] = str(via_device)
    config.setdefault("manufacturer", MANUFACTURER)
    config.setdefault("name", f"Jackery {device_id}")
    return device_id, config


def _description_name(entity: Any, description: Any, unique_id: str) -> str:
    """Return the native entity's translated name with a stable fallback."""
    try:
        entity_name = entity.name
    except AttributeError:
        entity_name = None
    if isinstance(entity_name, str) and entity_name.strip():
        return entity_name.strip()
    raw = (
        getattr(description, "translation_key", None)
        or getattr(description, "key", None)
        or getattr(entity, "_attr_translation_key", None)
        or unique_id
    )
    if name := _CT_DISCOVERY_NAMES.get(str(raw)):
        return name
    return str(raw).replace("_", " ").strip().title()


@dataclass(slots=True)
class _LiveStateSnapshot:
    """One immutable Home Assistant state event awaiting broker delivery."""

    unique_id: str
    available: bool
    payload: str | None
    state_published: bool = False
    availability_published: bool = False
    enqueued_at: float = field(default_factory=monotonic)

    @property
    def estimated_bytes(self) -> int:
        """Conservative diagnostic size for this queued event."""
        return len(self.unique_id.encode()) + len((self.payload or "").encode()) + 2


@dataclass(slots=True)
class _PublisherHandoff:
    """Entry-scoped lossless state transferred to a replacement generation."""

    live_events: tuple[_LiveStateSnapshot, ...]
    published_configs: dict[str, str]
    published_states: dict[str, tuple[str, str]]
    published_availability: dict[str, tuple[str, str]]
    cleanup_topics: set[str]
    pending: bool
    pending_all: bool
    pending_unique_ids: set[str]


class _RetryableMqttPublishError(Exception):
    """One broker-bound publish failed before Home Assistant confirmed delivery."""


class JackeryMqttSensorPublisher:
    """Mirror value-bearing native Jackery sensors to the configured HA broker."""

    def __init__(self, hass: HomeAssistant, *, entry_id: str) -> None:
        """Initialize an entry-scoped publisher."""
        self._hass = hass
        self._entry_id = entry_id
        self._entities: dict[str, Any] = {}
        self._entity_ids: dict[str, str] = {}
        self._published_configs: dict[str, str] = {}
        self._published_states: dict[str, tuple[str, str]] = {}
        self._published_availability: dict[str, tuple[str, str]] = {}
        self._live_state_events: deque[_LiveStateSnapshot] = deque()
        self._live_state_event_high_watermark = 0
        self._live_state_event_bytes_high_watermark = 0
        self._task: asyncio.Task[None] | None = None
        self._publish_lock = asyncio.Lock()
        self._publish_start_retry_handle: asyncio.TimerHandle | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_lock = asyncio.Lock()
        self._cleanup_topics: set[str] = set()
        self._cleanup_grace_pending = False
        self._retired_cleanup_pending = False
        self._cleanup_unsubscribe: Callable[[], None] | None = None
        self._publish_connection_unsubscribe: Callable[[], None] | None = None
        self._waiting_for_publish_reconnect = False
        self._waiting_for_entity_unique_id: str | None = None
        self._start_unsubscribe: Callable[[], None] | None = None
        self._state_change_unsubscribe: Callable[[], None] | None = None
        self._entity_registry_change_unsubscribe: Callable[[], None] | None = None
        self._pending = False
        self._pending_all = False
        self._pending_unique_ids: set[str] = set()
        self._restart_publish_after_current = False
        self._stopping = False
        self._owns_topics = True
        self._owner_bucket: dict[str, Any] | None = None
        owner_bucket: dict[str, Any] | None = None
        hass_data = getattr(hass, "data", None)
        if isinstance(hass_data, dict):
            domain_bucket = hass_data.setdefault(DOMAIN, {})
            if isinstance(domain_bucket, dict):
                entry_bucket = domain_bucket.setdefault(entry_id, {})
                if isinstance(entry_bucket, dict):
                    owner_bucket = entry_bucket
        bus = getattr(hass, "bus", None)
        listen = getattr(bus, "async_listen", None)
        try:
            if callable(listen):
                self._state_change_unsubscribe = listen(
                    EVENT_STATE_CHANGED,
                    self._async_state_changed,
                )
                self._entity_registry_change_unsubscribe = listen(
                    EVENT_ENTITY_REGISTRY_UPDATED,
                    self._async_entity_registry_updated,
                )
        except Exception:
            self._async_unsubscribe_bus_listeners()
            raise
        if owner_bucket is not None:
            previous = owner_bucket.get(_PUBLISHER_RUNTIME_KEY)
            if isinstance(previous, JackeryMqttSensorPublisher):
                handoff = previous.async_retire()
                self._live_state_events.extend(handoff.live_events)
                self._published_configs.update(handoff.published_configs)
                self._published_states.update(handoff.published_states)
                self._published_availability.update(handoff.published_availability)
                self._cleanup_topics.update(handoff.cleanup_topics)
                self._pending = handoff.pending
                self._pending_all = handoff.pending_all
                self._pending_unique_ids.update(handoff.pending_unique_ids)
                self._retired_cleanup_pending = bool(self._cleanup_topics)
                self._refresh_live_queue_high_watermarks()
            owner_bucket[_PUBLISHER_RUNTIME_KEY] = self
            self._owner_bucket = owner_bucket

    @callback
    def _async_unsubscribe_bus_listeners(self) -> None:
        """Release whichever global bus listeners were registered successfully."""
        if self._state_change_unsubscribe is not None:
            self._state_change_unsubscribe()
            self._state_change_unsubscribe = None
        if self._entity_registry_change_unsubscribe is not None:
            self._entity_registry_change_unsubscribe()
            self._entity_registry_change_unsubscribe = None

    @callback
    def async_take_pending_live_events(self) -> tuple[_LiveStateSnapshot, ...]:
        """Transfer accepted live events to a replacement publisher generation."""
        pending = tuple(self._live_state_events)
        self._live_state_events.clear()
        return pending

    @callback
    def async_retire(self) -> _PublisherHandoff:
        """Relinquish ownership and transfer every accepted/published item."""
        self._owns_topics = False
        self._stopping = True
        pending_cleanup = {
            *self._cleanup_topics,
            *self._published_configs.values(),
        }
        if self._cleanup_unsubscribe is not None:
            self._cleanup_unsubscribe()
            self._cleanup_unsubscribe = None
        self._async_unsubscribe_start()
        self._async_unsubscribe_publish_reconnect()
        self._async_unsubscribe_bus_listeners()
        publish_task = self._task
        if publish_task is not None and not publish_task.done():
            publish_task.cancel()
        task = self._cleanup_task
        if task is not None and not task.done():
            task.cancel()
        if self._publish_start_retry_handle is not None:
            self._publish_start_retry_handle.cancel()
            self._publish_start_retry_handle = None
        handoff = _PublisherHandoff(
            live_events=tuple(self._live_state_events),
            published_configs=dict(self._published_configs),
            published_states=dict(self._published_states),
            published_availability=dict(self._published_availability),
            cleanup_topics=pending_cleanup,
            pending=self._pending,
            pending_all=self._pending_all,
            pending_unique_ids=set(self._pending_unique_ids),
        )
        self._live_state_events.clear()
        self._published_configs.clear()
        self._published_states.clear()
        self._published_availability.clear()
        self._cleanup_topics.clear()
        self._pending = False
        self._pending_all = False
        self._pending_unique_ids.clear()
        return handoff

    @callback
    def _append_live_state_event(self, snapshot: _LiveStateSnapshot) -> None:
        """Accept one immutable event and update pressure diagnostics."""
        self._live_state_events.append(snapshot)
        self._refresh_live_queue_high_watermarks()

    @callback
    def _popleft_live_state_event(self) -> _LiveStateSnapshot:
        """Acknowledge exactly the current FIFO head after complete delivery."""
        return self._live_state_events.popleft()

    @callback
    def _refresh_live_queue_high_watermarks(self) -> None:
        """Record lossless FIFO depth/byte high-watermarks."""
        depth = len(self._live_state_events)
        queued_bytes = sum(item.estimated_bytes for item in self._live_state_events)
        self._live_state_event_high_watermark = max(
            self._live_state_event_high_watermark,
            depth,
        )
        self._live_state_event_bytes_high_watermark = max(
            self._live_state_event_bytes_high_watermark,
            queued_bytes,
        )

    @property
    def pending_live_event_diagnostics(self) -> dict[str, Any]:
        """Expose broker-outage pressure without dropping accepted values."""
        now = monotonic()
        oldest = self._live_state_events[0] if self._live_state_events else None
        return {
            "depth": len(self._live_state_events),
            "estimated_bytes": sum(
                item.estimated_bytes for item in self._live_state_events
            ),
            "depth_high_watermark": self._live_state_event_high_watermark,
            "bytes_high_watermark": self._live_state_event_bytes_high_watermark,
            "oldest_age_seconds": (
                max(0.0, now - oldest.enqueued_at) if oldest is not None else 0.0
            ),
            "waiting_for_reconnect": self._waiting_for_publish_reconnect,
        }

    def _is_current_owner(self) -> bool:
        """Return whether this generation may mutate its retained topics."""
        return self._owns_topics and (
            self._owner_bucket is None
            or self._owner_bucket.get(_PUBLISHER_RUNTIME_KEY) is self
        )

    def track(self, entity: Any) -> None:
        """Track one registered native sensor by its stable unique ID."""
        unique_id = getattr(entity, "unique_id", None)
        if unique_id:
            unique_id = str(unique_id)
            self._entities[unique_id] = entity
            entity_id = getattr(entity, "entity_id", None)
            if isinstance(entity_id, str):
                self._entity_ids[entity_id] = unique_id
            if any(
                snapshot.unique_id == unique_id for snapshot in self._live_state_events
            ):
                if self._waiting_for_entity_unique_id == unique_id:
                    self._waiting_for_entity_unique_id = None
                self._async_start_publish_worker()

    def _refresh_entity_id_index(self) -> None:
        """Index registered native entity IDs before an initial full snapshot."""
        self._entity_ids = {
            entity_id: unique_id
            for unique_id, entity in self._entities.items()
            if isinstance((entity_id := getattr(entity, "entity_id", None)), str)
        }

    @callback
    def async_schedule_initial_publish(self) -> None:
        """Publish the startup snapshot after Home Assistant finished MQTT discovery.

        The retained MQTT discovery documents are necessary for a fresh broker,
        but publishing the whole native entity set while Home Assistant is still
        subscribing to its own discovery wildcard causes broker startup backlogs.
        Deferring only this one snapshot leaves every later state event immediate.
        """
        if self._stopping:
            return
        if bool(getattr(self._hass, "is_running", False)):
            self.async_schedule_publish()
            return
        bus = getattr(self._hass, "bus", None)
        listen_once = getattr(bus, "async_listen_once", None)
        if not callable(listen_once):
            self.async_schedule_publish()
            return
        if self._start_unsubscribe is None:
            self._start_unsubscribe = listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                self._async_homeassistant_started,
            )

    @callback
    def _async_homeassistant_started(self, _event: Event) -> None:
        """Release the initial retained snapshot once HA MQTT setup is complete."""
        # EventBus.async_listen_once removes this listener before invoking us.
        self._start_unsubscribe = None
        self.async_schedule_publish()

    @callback
    def _async_unsubscribe_start(self) -> None:
        """Remove a pending Home Assistant-start listener."""
        if self._start_unsubscribe is not None:
            self._start_unsubscribe()
            self._start_unsubscribe = None

    @callback
    def async_schedule_publish(
        self,
        unique_ids: Iterable[str] | None = None,
    ) -> None:
        """Queue an immediate full or entity-scoped reconciliation snapshot."""
        if self._stopping:
            return
        self._pending = True
        if unique_ids is None:
            self._pending_all = True
            self._pending_unique_ids.clear()
        elif not self._pending_all:
            self._pending_unique_ids.update(str(unique_id) for unique_id in unique_ids)
        self._async_start_publish_worker()

    @callback
    def _async_start_publish_worker(self) -> None:
        """Start the single FIFO publisher as finite, integration-owned work."""
        if (
            self._stopping
            or self._waiting_for_publish_reconnect
            or self._waiting_for_entity_unique_id is not None
        ):
            return
        if self._publish_start_retry_handle is not None:
            return
        if self._task is not None and not self._task.done():
            self._restart_publish_after_current = True
            return
        target = self._async_publish_loop()
        create_task = getattr(self._hass, "async_create_task", None)
        try:
            if callable(create_task):
                self._task = create_task(
                    target,
                    name=f"{DOMAIN}_mqtt_sensor_publish_{self._entry_id}",
                    eager_start=False,
                )
                return
            self._task = asyncio.create_task(
                target,
                name=f"{DOMAIN}_mqtt_sensor_publish_{self._entry_id}",
            )
        except Exception:
            target.close()
            self._task = None
            _LOGGER.exception(
                "Unable to create Jackery MQTT sensor publisher task; retrying"
            )
            loop = asyncio.get_running_loop()
            self._publish_start_retry_handle = loop.call_later(
                _PUBLISH_TASK_START_RETRY_SEC,
                self._async_retry_publish_worker_start,
            )

    @callback
    def _async_retry_publish_worker_start(self) -> None:
        """Retry a rejected HA task creation without losing queued work."""
        self._publish_start_retry_handle = None
        if self._pending or self._live_state_events:
            self._async_start_publish_worker()

    @callback
    def _async_entity_registry_updated(self, event: Event) -> None:
        """Keep the entity-ID index current across create, rename, and removal."""
        data = event.data
        action = data.get("action")
        entity_id = data.get("entity_id")
        if not isinstance(entity_id, str):
            return
        if action == "remove":
            self._entity_ids.pop(entity_id, None)
            return
        if action == "update":
            old_entity_id = data.get("old_entity_id")
            if isinstance(old_entity_id, str):
                if unique_id := self._entity_ids.pop(old_entity_id, None):
                    self._entity_ids[entity_id] = unique_id
                    return
        if action not in {"create", "update"}:
            return
        self._refresh_entity_id_index()
        if entity_id in self._entity_ids:
            return
        try:
            registry_entry = er.async_get(self._hass).async_get(entity_id)
        except Exception:  # ruff: ignore[blind-except]
            return
        if (
            registry_entry is not None
            and registry_entry.platform == DOMAIN
            and registry_entry.unique_id in self._entities
        ):
            self._entity_ids[entity_id] = registry_entry.unique_id

    @callback
    def _async_state_changed(self, event: Event) -> None:
        """Capture and queue the exact native state carried by one HA event."""
        entity_id = event.data.get("entity_id")
        if not isinstance(entity_id, str):
            return
        unique_id = self._entity_ids.get(entity_id)
        if unique_id is None:
            return
        entity = self._entities.get(unique_id)
        if entity is None:
            return
        if "new_state" in event.data and event.data["new_state"] is None:
            available = False
            payload = None
        else:
            new_state = event.data.get("new_state")
            state = getattr(new_state, "state", None)
            if isinstance(state, str):
                available = state not in {STATE_UNAVAILABLE, STATE_UNKNOWN}
                payload = state if available else None
            else:
                try:
                    available = bool(getattr(entity, "available", True))
                    value = entity.native_value
                except Exception:  # ruff: ignore[blind-except]
                    available = False
                    value = None
                payload = (
                    _state_payload(value) if available and value is not None else None
                )
        self._append_live_state_event(
            _LiveStateSnapshot(
                unique_id=unique_id,
                available=available,
                payload=payload,
            )
        )
        self._async_start_publish_worker()

    async def _async_publish_loop(self) -> None:
        """Publish the newest snapshot and rerun once if updates raced with I/O."""
        try:
            await self._async_publish_until_idle()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Unable to publish Jackery MQTT sensor discovery")
        finally:
            restart = self._restart_publish_after_current and (
                self._pending or bool(self._live_state_events)
            )
            self._restart_publish_after_current = False
            self._task = None
            if restart and not self._stopping:
                self._async_start_publish_worker()

    async def _async_publish_until_idle(self) -> None:
        """Publish pending snapshots until idle or defer one until reconnect."""
        retry_attempt = 0
        while (self._pending or self._live_state_events) and not self._stopping:
            live_snapshot = (
                self._live_state_events[0] if self._live_state_events else None
            )
            if live_snapshot is None:
                self._pending = False
                publish_all = self._pending_all
                unique_ids = None if publish_all else tuple(self._pending_unique_ids)
                self._pending_all = False
                self._pending_unique_ids.clear()
            else:
                publish_all = False
                unique_ids = ()
            try:
                complete = await self._async_publish_work_item(
                    live_snapshot,
                    unique_ids,
                    publish_all=publish_all,
                )
            except asyncio.CancelledError:
                raise
            except _RetryableMqttPublishError as err:
                if live_snapshot is None:
                    self._pending = True
                    if publish_all:
                        self._pending_all = True
                    elif not self._pending_all and unique_ids is not None:
                        self._pending_unique_ids.update(unique_ids)
                if not mqtt.is_connected(self._hass):
                    self._async_subscribe_publish_reconnect()
                    if mqtt.is_connected(self._hass):
                        self._waiting_for_publish_reconnect = False
                        self._async_unsubscribe_publish_reconnect()
                        continue
                    _LOGGER.debug(
                        "Deferring Jackery MQTT sensor discovery until the "
                        "Home Assistant MQTT client reconnects"
                    )
                    return
                delay = _PUBLISH_RETRY_DELAYS_SEC[
                    min(retry_attempt, len(_PUBLISH_RETRY_DELAYS_SEC) - 1)
                ]
                retry_attempt += 1
                _LOGGER.debug(
                    "Retrying Jackery MQTT sensor publication in %.2fs: %s",
                    delay,
                    err,
                )
                await asyncio.sleep(delay)
                continue
            if not complete:
                if live_snapshot is None:
                    self._pending = True
                    if publish_all:
                        self._pending_all = True
                    elif not self._pending_all and unique_ids is not None:
                        self._pending_unique_ids.update(unique_ids)
                return
            retry_attempt = 0

    async def _async_publish_work_item(
        self,
        live_snapshot: _LiveStateSnapshot | None,
        unique_ids: Iterable[str] | None,
        *,
        publish_all: bool,
    ) -> bool:
        """Publish one serialized FIFO event or reconciliation snapshot."""
        async with self._publish_lock:
            if live_snapshot is not None:
                if (
                    not self._live_state_events
                    or self._live_state_events[0] is not live_snapshot
                ):
                    return True
                if not await self._async_publish_live_snapshot(live_snapshot):
                    return False
                self._popleft_live_state_event()
                return True
            complete = await self._async_publish_pending_unlocked(unique_ids)
            if complete and publish_all:
                self._async_finish_transferred_cleanup()
            return complete

    @callback
    def _async_subscribe_publish_reconnect(self) -> None:
        """Subscribe once so a disconnected snapshot resumes on reconnect."""
        if self._publish_connection_unsubscribe is not None or self._stopping:
            return
        self._publish_connection_unsubscribe = mqtt.async_subscribe_connection_status(
            self._hass,
            self._async_publish_connection_state_changed,
        )
        self._waiting_for_publish_reconnect = True

    @callback
    def _async_unsubscribe_publish_reconnect(self) -> None:
        """Remove the temporary active-publish reconnect listener."""
        if self._publish_connection_unsubscribe is None:
            return
        self._publish_connection_unsubscribe()
        self._publish_connection_unsubscribe = None

    @callback
    def _async_publish_connection_state_changed(self, connected: bool) -> None:
        """Resume a pending discovery snapshot after MQTT reconnects."""
        if not connected or self._stopping or not self._is_current_owner():
            return
        self._waiting_for_publish_reconnect = False
        self._async_unsubscribe_publish_reconnect()
        if self._task is not None and not self._task.done():
            self._restart_publish_after_current = True
            return
        self._async_start_publish_worker()

    async def async_publish_pending(
        self,
        unique_ids: Iterable[str] | None = None,
    ) -> None:
        """Publish a serialized full or targeted reconciliation snapshot."""
        async with self._publish_lock:
            await self._async_publish_pending_unlocked(unique_ids)

    async def _async_publish_pending_unlocked(
        self,
        unique_ids: Iterable[str] | None,
    ) -> bool:
        """Publish one reconciliation while the shared publisher lock is held."""
        if unique_ids is None:
            self._refresh_entity_id_index()
        entities = (
            tuple(self._entities.items())
            if unique_ids is None
            else tuple(
                (unique_id, self._entities[unique_id])
                for unique_id in unique_ids
                if unique_id in self._entities
            )
        )
        for unique_id, entity in entities:
            if not await self._async_drain_live_events_unlocked():
                return False
            await self._async_publish_entity(unique_id, entity)
        return await self._async_drain_live_events_unlocked()

    async def _async_drain_live_events_unlocked(self) -> bool:
        """Drain accepted live events while retaining an untracked FIFO head."""
        while self._live_state_events and not self._stopping:
            live_snapshot = self._live_state_events[0]
            if not await self._async_publish_live_snapshot(live_snapshot):
                return False
            self._popleft_live_state_event()
        return True

    @staticmethod
    def _entity_publish_context(
        unique_id: str,
        entity: Any,
    ) -> tuple[Any, dict[str, Any], str, str, str]:
        """Return immutable metadata and topics shared by snapshot/event writes."""
        description = getattr(entity, "entity_description", None)
        device_id, device = _device_config(entity)
        parent_device_id = str(getattr(entity, "_device_id", device_id))
        suffix = unique_id.removeprefix(f"{parent_device_id}_")
        object_id = slugify(unique_id)
        state_topic = f"{_STATE_PREFIX}/{device_id}/sensor/{suffix}/state"
        availability_topic = f"{_STATE_PREFIX}/{device_id}/sensor/{suffix}/availability"
        config_topic = f"{_DISCOVERY_PREFIX}/sensor/{DOMAIN}/{object_id}/config"
        return description, device, state_topic, availability_topic, config_topic

    async def _async_ensure_discovery_config(
        self,
        unique_id: str,
        entity: Any,
        description: Any,
        device: dict[str, Any],
        state_topic: str,
        availability_topic: str,
        config_topic: str,
    ) -> None:
        """Publish a retained entity definition even before its first value exists."""
        if unique_id in self._published_configs:
            self._cleanup_topics.discard(self._published_configs[unique_id])
            return
        config = self._discovery_config(
            entity,
            description,
            unique_id=unique_id,
            state_topic=state_topic,
            availability_topic=availability_topic,
            device=device,
        )
        await self._async_publish(config_topic, json.dumps(config), retain=True)
        self._published_configs[unique_id] = config_topic
        self._cleanup_topics.discard(config_topic)

    async def _async_publish_live_snapshot(
        self,
        snapshot: _LiveStateSnapshot,
    ) -> bool:
        """Publish one captured live state without rereading mutable entity data."""
        unique_id = snapshot.unique_id
        entity = self._entities.get(unique_id)
        if entity is None:
            self._waiting_for_entity_unique_id = unique_id
            return False
        if self._waiting_for_entity_unique_id == unique_id:
            self._waiting_for_entity_unique_id = None
        (
            description,
            device,
            state_topic,
            availability_topic,
            config_topic,
        ) = self._entity_publish_context(unique_id, entity)
        await self._async_ensure_discovery_config(
            unique_id,
            entity,
            description,
            device,
            state_topic,
            availability_topic,
            config_topic,
        )
        if not snapshot.available or snapshot.payload is None:
            availability = (availability_topic, "offline")
            if not snapshot.availability_published:
                if self._published_availability.get(unique_id) != availability:
                    await self._async_publish(*availability)
                    self._published_availability[unique_id] = availability
                snapshot.availability_published = True
            if not snapshot.state_published:
                if unique_id in self._published_states:
                    await self._async_publish(state_topic, "")
                    self._published_states.pop(unique_id, None)
                snapshot.state_published = True
            return True
        if not snapshot.state_published:
            await self._async_publish(state_topic, snapshot.payload)
            self._published_states[unique_id] = (state_topic, snapshot.payload)
            snapshot.state_published = True
        availability = (availability_topic, "online")
        if not snapshot.availability_published:
            if self._published_availability.get(unique_id) != availability:
                await self._async_publish(*availability)
                self._published_availability[unique_id] = availability
            snapshot.availability_published = True
        return True

    async def _async_publish_entity(self, unique_id: str, entity: Any) -> None:
        """Publish retained discovery and immediate, non-retained live updates."""
        (
            description,
            device,
            state_topic,
            availability_topic,
            config_topic,
        ) = self._entity_publish_context(unique_id, entity)
        await self._async_ensure_discovery_config(
            unique_id,
            entity,
            description,
            device,
            state_topic,
            availability_topic,
            config_topic,
        )
        if any(snapshot.unique_id == unique_id for snapshot in self._live_state_events):
            return
        try:
            available = bool(getattr(entity, "available", True))
            value = entity.native_value
        except Exception:
            _LOGGER.debug(
                "Skipping MQTT export for unreadable Jackery sensor %s",
                unique_id,
                exc_info=True,
            )
            available = False
            value = None
        if not available or value is None:
            availability = (availability_topic, "offline")
            if self._published_availability.get(unique_id) != availability:
                await self._async_publish(*availability)
                self._published_availability[unique_id] = availability
            if unique_id in self._published_states:
                await self._async_publish(state_topic, "")
                self._published_states.pop(unique_id, None)
            return
        payload = _state_payload(value)
        previous = self._published_states.get(unique_id)
        if previous != (state_topic, payload):
            await self._async_publish(state_topic, payload)
            self._published_states[unique_id] = (state_topic, payload)
        availability = (availability_topic, "online")
        if self._published_availability.get(unique_id) != availability:
            await self._async_publish(*availability)
            self._published_availability[unique_id] = availability

    @staticmethod
    def _discovery_config(
        entity: Any,
        description: Any,
        *,
        unique_id: str,
        state_topic: str,
        availability_topic: str,
        device: dict[str, Any],
    ) -> dict[str, Any]:
        """Build one Home Assistant MQTT sensor discovery document."""
        config: dict[str, Any] = {
            "availability_topic": availability_topic,
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
            "origin": {
                "name": "Jackery SolarVault",
                "support_url": ("https://github.com/Bigdaddy1990/jackery_solarvault"),
            },
            "payload_available": "online",
            "payload_not_available": "offline",
            "state_topic": state_topic,
            "unique_id": f"{DOMAIN}_mqtt_{unique_id}",
        }
        optional_fields = {
            "device_class": getattr(description, "device_class", None)
            or getattr(entity, "device_class", None),
            "entity_category": getattr(description, "entity_category", None)
            or getattr(entity, "entity_category", None),
            "state_class": getattr(description, "state_class", None)
            or getattr(entity, "state_class", None),
            "unit_of_measurement": getattr(
                description, "native_unit_of_measurement", None
            )
            or getattr(entity, "native_unit_of_measurement", None),
        }
        for key, value in optional_fields.items():
            value = _enum_value(value)
            if value is not None:
                config[key] = value
        return config

    async def _async_publish(
        self, topic: str, payload: str, *, retain: bool = False
    ) -> None:
        """Publish only while this generation owns the MQTT topics."""
        if not self._is_current_owner():
            return
        try:
            await mqtt.async_publish(
                self._hass,
                topic,
                payload,
                qos=0,
                retain=retain,
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:
            raise _RetryableMqttPublishError(
                f"MQTT publish to {topic!r} failed: {err}"
            ) from err

    @callback
    def _async_finish_transferred_cleanup(self) -> None:
        """Clear only replacement topics absent after its first full snapshot."""
        if not self._retired_cleanup_pending:
            return
        self._retired_cleanup_pending = False
        if not self._cleanup_topics:
            return
        if mqtt.is_connected(self._hass):
            self._async_schedule_cleanup_worker()
            return
        if self._cleanup_unsubscribe is None:
            self._cleanup_unsubscribe = mqtt.async_subscribe_connection_status(
                self._hass,
                self._async_mqtt_connection_state_changed,
            )

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
        """Retry retained cleanup a finite number of times per connection."""
        if self._cleanup_grace_pending:
            self._cleanup_grace_pending = False
            await asyncio.sleep(_RELOAD_HANDOFF_GRACE_SEC)
            if not self._is_current_owner():
                return
        attempt = 0
        while (
            self._cleanup_topics
            and self._is_current_owner()
            and attempt < _CLEANUP_MAX_ATTEMPTS
        ):
            delay = _CLEANUP_RETRY_DELAYS_SEC[
                min(attempt, len(_CLEANUP_RETRY_DELAYS_SEC) - 1)
            ]
            await asyncio.sleep(delay)
            if not mqtt.is_connected(self._hass):
                return
            try:
                async with asyncio.timeout(_CLEANUP_TIMEOUT_SEC):
                    await self._async_clear_retained_topics()
            except TimeoutError:
                pass
            attempt += 1

    async def _async_clear_retained_topics(self) -> None:
        """Clear known retained topics while preserving failures for retry."""
        async with self._cleanup_lock:
            if not self._is_current_owner():
                return
            for topic in sorted(self._cleanup_topics):
                if not self._is_current_owner():
                    return
                try:
                    await self._async_publish(topic, "", retain=True)
                except Exception:
                    _LOGGER.debug(
                        "Unable to clear retained Jackery MQTT topic %s during unload",
                        topic,
                        exc_info=True,
                    )
                else:
                    self._cleanup_topics.discard(topic)
            if self._cleanup_topics:
                return
            if self._cleanup_unsubscribe is not None:
                self._cleanup_unsubscribe()
                self._cleanup_unsubscribe = None
            # A replacement publisher may inherit stale cleanup work from a
            # retired generation. That cleanup must not retire the active
            # replacement after it has published its own complete snapshot.
            if not self._stopping:
                return
            self._published_configs.clear()
            self._published_states.clear()
            self._published_availability.clear()
            if (
                self._owner_bucket is not None
                and self._owner_bucket.get(_PUBLISHER_RUNTIME_KEY) is self
            ):
                self._owner_bucket.pop(_PUBLISHER_RUNTIME_KEY, None)
            self._owns_topics = False

    async def async_shutdown(self) -> None:
        """Remove retained discovery topics owned by this config entry."""
        self._stopping = True
        self._waiting_for_publish_reconnect = False
        self._async_unsubscribe_start()
        self._async_unsubscribe_publish_reconnect()
        if self._publish_start_retry_handle is not None:
            self._publish_start_retry_handle.cancel()
            self._publish_start_retry_handle = None
        if self._state_change_unsubscribe is not None:
            self._state_change_unsubscribe()
            self._state_change_unsubscribe = None
        if self._entity_registry_change_unsubscribe is not None:
            self._entity_registry_change_unsubscribe()
            self._entity_registry_change_unsubscribe = None
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._cleanup_topics.update({
            *self._published_configs.values(),
        })
        if self._cleanup_topics and self._cleanup_unsubscribe is None:
            self._cleanup_unsubscribe = mqtt.async_subscribe_connection_status(
                self._hass,
                self._async_mqtt_connection_state_changed,
            )
        if not self._cleanup_topics or not mqtt.is_connected(self._hass):
            return
        if self._owner_bucket is not None:
            self._cleanup_grace_pending = True
            self._async_schedule_cleanup_worker()
            return
        try:
            async with asyncio.timeout(_CLEANUP_TIMEOUT_SEC):
                await self._async_clear_retained_topics()
        except TimeoutError:
            _LOGGER.debug(
                "Timed out clearing retained Jackery MQTT topics during unload; "
                "cleanup will continue in the background"
            )
        if self._cleanup_topics and mqtt.is_connected(self._hass):
            self._async_schedule_cleanup_worker()
