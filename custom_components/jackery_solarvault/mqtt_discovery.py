"""Publish native Jackery sensor values through Home Assistant MQTT discovery."""

import asyncio
from collections.abc import Callable, Mapping
import contextlib
from datetime import date, datetime
from enum import Enum
from itertools import starmap
import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import slugify

from .const import DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)
_DISCOVERY_PREFIX = "homeassistant"
_STATE_PREFIX = DOMAIN
_CLEANUP_TIMEOUT_SEC = 1.0
_CLEANUP_RETRY_DELAYS_SEC = (1.0, 5.0, 30.0)
_PUBLISH_CONCURRENCY = 8
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
    """Return a readable, stable discovery name without localization coupling."""
    raw = (
        getattr(description, "translation_key", None)
        or getattr(description, "key", None)
        or getattr(entity, "_attr_translation_key", None)
        or unique_id
    )
    if name := _CT_DISCOVERY_NAMES.get(str(raw)):
        return name
    return str(raw).replace("_", " ").strip().title()


class JackeryMqttSensorPublisher:
    """Mirror value-bearing native Jackery sensors to the configured HA broker."""

    def __init__(self, hass: HomeAssistant, *, entry_id: str) -> None:
        """Initialize an entry-scoped publisher."""
        self._hass = hass
        self._entry_id = entry_id
        self._entities: dict[str, Any] = {}
        self._published_configs: dict[str, str] = {}
        self._published_states: dict[str, tuple[str, str]] = {}
        self._published_availability: dict[str, tuple[str, str]] = {}
        self._cleared_unknowns: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_lock = asyncio.Lock()
        self._cleanup_topics: set[str] = set()
        self._cleanup_unsubscribe: Callable[[], None] | None = None
        self._pending = False
        self._stopping = False
        self._owns_topics = True
        self._owner_bucket: dict[str, Any] | None = None
        hass_data = getattr(hass, "data", None)
        if isinstance(hass_data, dict):
            domain_bucket = hass_data.setdefault(DOMAIN, {})
            if isinstance(domain_bucket, dict):
                entry_bucket = domain_bucket.setdefault(entry_id, {})
                if isinstance(entry_bucket, dict):
                    previous = entry_bucket.get(_PUBLISHER_RUNTIME_KEY)
                    if isinstance(previous, JackeryMqttSensorPublisher):
                        previous.async_retire()
                    entry_bucket[_PUBLISHER_RUNTIME_KEY] = self
                    self._owner_bucket = entry_bucket

    @callback
    def async_retire(self) -> None:
        """Relinquish retained-topic ownership to a replacement publisher."""
        self._owns_topics = False
        self._stopping = True
        self._cleanup_topics.clear()
        if self._cleanup_unsubscribe is not None:
            self._cleanup_unsubscribe()
            self._cleanup_unsubscribe = None
        task = self._cleanup_task
        if task is not None and not task.done():
            task.cancel()

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
            self._entities[str(unique_id)] = entity

    @callback
    def async_schedule_publish(self) -> None:
        """Coalesce coordinator callbacks into an immediate broker snapshot."""
        if self._stopping:
            return
        self._pending = True
        if self._task is not None and not self._task.done():
            return
        self._task = self._hass.async_create_background_task(
            self._async_publish_loop(),
            name=f"{DOMAIN}_mqtt_sensor_publish_{self._entry_id}",
            eager_start=False,
        )

    async def _async_publish_loop(self) -> None:
        """Publish the newest snapshot and rerun once if updates raced with I/O."""
        try:
            while self._pending and not self._stopping:
                self._pending = False
                await self.async_publish_pending()
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Unable to publish Jackery MQTT sensor discovery")
        finally:
            self._task = None

    async def async_publish_pending(self) -> None:
        """Publish discovery and changed states for all value-bearing sensors."""
        semaphore = asyncio.Semaphore(_PUBLISH_CONCURRENCY)

        async def _publish_one(unique_id: str, entity: Any) -> None:
            async with semaphore:
                await self._async_publish_entity(unique_id, entity)

        results = await asyncio.gather(
            *starmap(_publish_one, tuple(self._entities.items())),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _async_publish_entity(self, unique_id: str, entity: Any) -> None:
        """Publish one entity in config, state, availability order."""
        description = getattr(entity, "entity_description", None)
        device_id, device = _device_config(entity)
        parent_device_id = str(getattr(entity, "_device_id", device_id))
        suffix = unique_id.removeprefix(f"{parent_device_id}_")
        object_id = slugify(unique_id)
        state_topic = f"{_STATE_PREFIX}/{device_id}/sensor/{suffix}/state"
        availability_topic = f"{_STATE_PREFIX}/{device_id}/sensor/{suffix}/availability"
        config_topic = f"{_DISCOVERY_PREFIX}/sensor/{DOMAIN}/{object_id}/config"
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
            if unique_id not in self._published_configs:
                if unique_id in self._cleared_unknowns:
                    return
                for topic in (config_topic, state_topic, availability_topic):
                    await self._async_publish(topic, "")
                self._cleared_unknowns.add(unique_id)
                return
            availability = (availability_topic, "offline")
            if self._published_availability.get(unique_id) != availability:
                await self._async_publish(*availability)
                self._published_availability[unique_id] = availability
            if unique_id in self._published_states:
                await self._async_publish(state_topic, "")
                self._published_states.pop(unique_id, None)
            return
        if unique_id not in self._published_configs:
            config = self._discovery_config(
                entity,
                description,
                unique_id=unique_id,
                state_topic=state_topic,
                availability_topic=availability_topic,
                device=device,
            )
            await self._async_publish(config_topic, json.dumps(config))
            self._published_configs[unique_id] = config_topic
            self._cleared_unknowns.discard(unique_id)
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

    async def _async_publish(self, topic: str, payload: str) -> None:
        """Publish retained discovery state through Home Assistant's broker."""
        await mqtt.async_publish(self._hass, topic, payload, qos=0, retain=True)

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
            if not mqtt.is_connected(self._hass):
                attempt += 1
                continue
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
                    await self._async_publish(topic, "")
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
            self._published_configs.clear()
            self._published_states.clear()
            self._published_availability.clear()
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
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._cleanup_topics.update({
            *self._published_configs.values(),
            *(topic for topic, _payload in self._published_states.values()),
            *(topic for topic, _payload in self._published_availability.values()),
        })
        if self._cleanup_topics and self._cleanup_unsubscribe is None:
            self._cleanup_unsubscribe = mqtt.async_subscribe_connection_status(
                self._hass,
                self._async_mqtt_connection_state_changed,
            )
        if not self._cleanup_topics or not mqtt.is_connected(self._hass):
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
