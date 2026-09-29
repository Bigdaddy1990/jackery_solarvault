"""Publish native Jackery sensor values through Home Assistant MQTT discovery."""

import asyncio
from collections.abc import Mapping
import contextlib
from datetime import date, datetime
from enum import Enum
from itertools import starmap
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.components import mqtt
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import slugify

from ..const import DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant
    from homeassistant.helpers.typing import StateType


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
_MIRROR_UNIQUE_ID_PREFIX = f"{DOMAIN}_mqtt_"
_IDENTIFIER_PART_COUNT = 2


class _EntityDeviceLike(Protocol):
    """Minimal protocol for entities that expose device_info and native_value.

    Read-only properties, matching ``SensorEntity`` (settable attributes in a
    Protocol reject every real entity).
    """

    @property
    def device_info(self) -> Mapping[str, Any] | None: ...

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
        return mqtt.is_connected(hass)
    except KeyError:  # hass.data["mqtt"] exists only once the MQTT entry is set up
        return False


def _enum_value(value: Any) -> Any:  # ruff:ignore[any-type] — generic enum/unwrap helper
    """Return a JSON/MQTT scalar for Home Assistant enums."""
    return value.value if isinstance(value, Enum) else value


def _state_payload(
    value: Any,  # ruff: ignore[any-type] — accepts heterogeneous sensor values
    reset: str | None = None,
) -> str:
    """Serialize one native sensor value without inventing an unknown marker."""
    if reset is not None:
        return json.dumps({"value": _state_payload(value), "last_reset": reset})
    value = _enum_value(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, dict | list | tuple):
        return json.dumps(value, separators=(",", ":"), default=str)
    return str(value)


def _reset_timestamp(entity: _EntityDeviceLike) -> str | None:
    """Return the reset boundary of a TOTAL sensor mirrored through MQTT."""
    if _enum_value(getattr(entity, "state_class", None)) != "total":
        return None
    reset = getattr(entity, "last_reset", None)
    return reset.isoformat() if isinstance(reset, datetime) else None


def _device_config(entity: _EntityDeviceLike) -> tuple[str, dict[str, Any]]:
    """Build a JSON-safe MQTT device block and return its stable identifier."""
    raw = getattr(entity, "device_info", None)
    info: dict[str, Any] = dict(raw) if isinstance(raw, Mapping) else {}
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


class JackeryMqttSensorPublisher:
    """Mirror value-bearing native Jackery sensors to the configured HA broker."""

    def __init__(self, hass: HomeAssistant, *, entry_id: str) -> None:
        """Initialize an entry-scoped publisher."""
        self._hass = hass
        self._entry_id = entry_id
        self._entities: dict[str, Any] = {}
        self._published_configs: dict[str, tuple[str, str]] = {}
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
        self._device_unsubscribe: Callable[[], None] | None = hass.bus.async_listen(
            dr.EVENT_DEVICE_REGISTRY_UPDATED, self._async_link_mqtt_device
        )
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
        for device in dr.async_get(hass).devices:
            self._link_mqtt_device(device.id)

    @callback
    def _async_link_mqtt_device(self, event: Event) -> None:
        """Attach newly discovered MQTT mirrors to their native device."""
        device_id = event.data.get("device_id")
        if device_id:
            self._link_mqtt_device(device_id)

    @staticmethod
    def _via_target_id(registry: dr.DeviceRegistry, device_id: str) -> str:
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
        return current.id if current is not None else device_id

    @callback
    def _link_mqtt_device(self, device_id: str) -> None:
        registry = dr.async_get(self._hass)
        device = registry.async_get(device_id)
        if device is None:
            return
        if any(namespace == DOMAIN for namespace, _ in device.identifiers):
            via_id = self._via_target_id(registry, device.id)
            for namespace, identifier in device.identifiers:
                if namespace == DOMAIN:
                    mirror_identifier = ("mqtt", f"{DOMAIN}:{identifier}")
                    for mirror in registry.devices:
                        if mirror_identifier in mirror.identifiers and via_id not in {
                            mirror.id,
                            mirror.via_device_id,
                        }:
                            registry.async_update_device(
                                mirror.id, via_device_id=via_id
                            )
            return
        if self._entry_id in device.config_entries:
            return
        for namespace, identifier in device.identifiers:
            prefix = f"{DOMAIN}:"
            if namespace != "mqtt" or not identifier.startswith(prefix):
                continue
            native = registry.async_get_device_by_identifier(
                (DOMAIN, identifier.removeprefix(prefix)), self._entry_id
            )
            if native is None:
                return
            via_id = self._via_target_id(registry, native.id)
            if via_id != device.id and getattr(device, "via_device_id", None) != via_id:
                registry.async_update_device(device.id, via_device_id=via_id)
            return

    @callback
    def async_retire(self) -> None:
        """Relinquish retained-topic ownership to a replacement publisher."""
        self._owns_topics = False
        self._stopping = True
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

    @callback
    def async_broker_connected(self) -> None:
        """Republish discovery and states after a broker reconnect."""
        self._published_configs.clear()
        self._published_states.clear()
        self._published_availability.clear()
        self.async_schedule_publish()

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
                # A disconnected broker cannot ACK; the reconnect callback
                # (async_broker_connected) republishes the full snapshot.
                if not _broker_connected(self._hass):
                    return
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

        async def _publish_one(unique_id: str, entity: _EntityDeviceLike) -> None:
            async with semaphore:
                await self._async_publish_entity(unique_id, entity)

        results = await asyncio.gather(
            *starmap(_publish_one, tuple(self._entities.items())),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _async_publish_entity(
        self, unique_id: str, entity: _EntityDeviceLike
    ) -> None:
        """Publish one entity in config, state, availability order."""
        description = getattr(entity, "entity_description", None)
        device_id, device = _device_config(entity)
        if device["name"] == f"Jackery {device_id}" and (
            native := dr.async_get(self._hass).async_get_device_by_identifier(
                (DOMAIN, device_id), self._entry_id
            )
        ):
            # device_info of a secondary entity may omit the name; the mirror
            # must still carry the native device's name, not its raw identifier.
            device["name"] = native.name_by_user or native.name or device["name"]
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
        # Republish whenever the document changes: topics and device follow the
        # native registration, and a config sent only once kept stale topics.
        config = (
            config_topic,
            json.dumps(
                self._discovery_config(
                    entity,
                    description,
                    topics=(state_topic, availability_topic),
                    unique_id=unique_id,
                    device=device,
                )
            ),
        )
        if self._published_configs.get(unique_id) != config:
            await self._async_publish(*config)
            self._published_configs[unique_id] = config
        if not available or value is None:
            availability = (availability_topic, "offline")
            if self._published_availability.get(unique_id) != availability:
                await self._async_publish(*availability)
                self._published_availability[unique_id] = availability
            # "offline" availability already hides the state; an empty state
            # payload broke every mirrored sensor with a value_json template.
            self._published_states.pop(unique_id, None)
            return
        payload = _state_payload(value, _reset_timestamp(entity))
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
        entity: _EntityDeviceLike,
        description: _DescriptionLike | None,
        *,
        unique_id: str,
        topics: tuple[str, str],
        device: dict[str, Any],
    ) -> dict[str, Any]:
        """Build one Home Assistant MQTT sensor discovery document."""
        state_topic, availability_topic = topics
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
        for key, raw in optional_fields.items():
            scalar_val = _enum_value(raw)
            if scalar_val is not None:
                config[key] = scalar_val
        if _reset_timestamp(entity) is not None:
            config["value_template"] = "{{ value_json.value }}"
            config["last_reset_value_template"] = "{{ value_json.last_reset }}"
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
            if not _broker_connected(self._hass):
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
            *(topic for topic, _payload in self._published_configs.values()),
            *(topic for topic, _payload in self._published_states.values()),
            *(topic for topic, _payload in self._published_availability.values()),
        })
        if self._cleanup_topics and self._cleanup_unsubscribe is None:
            self._cleanup_unsubscribe = mqtt.async_subscribe_connection_status(
                self._hass,
                self._async_mqtt_connection_state_changed,
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


@callback
def async_release_native_entity_ids(hass: HomeAssistant) -> None:
    """Give native sensors back the entity IDs their MQTT mirrors took.

    Mirrors published before ``default_entity_id`` moved to the ``jackery_mqtt_``
    namespace registered first and pushed the native sensor to ``<id>_2``.
    Registry renames carry the recorder statistics along with each entity.
    """
    registry = er.async_get(hass)
    for mirror in list(registry.entities.values()):
        if mirror.platform != "mqtt" or not mirror.unique_id.startswith(
            _MIRROR_UNIQUE_ID_PREFIX
        ):
            continue
        native_id = registry.async_get_entity_id(
            "sensor", DOMAIN, mirror.unique_id.removeprefix(_MIRROR_UNIQUE_ID_PREFIX)
        )
        if native_id != f"{mirror.entity_id}_2":
            continue
        target = f"sensor.jackery_mqtt_{mirror.entity_id.removeprefix("sensor.")}"
        if registry.async_is_registered(target):
            continue
        registry.async_update_entity(mirror.entity_id, new_entity_id=target)
        registry.async_update_entity(native_id, new_entity_id=mirror.entity_id)
        _LOGGER.info(
            "Moved Jackery MQTT mirror %s to %s and restored native %s",
            mirror.entity_id,
            target,
            mirror.entity_id,
        )


async def _async_clear_topics(
    hass: HomeAssistant,
    pending: set[str],
) -> int:
    """Publish retained tombstones and leave failures pending for retry."""
    removed = 0
    for topic in sorted(pending):
        try:
            await mqtt.async_publish(hass, topic, "", qos=0, retain=True)
        except (HomeAssistantError, OSError) as err:
            _LOGGER.debug(
                "Unable to remove obsolete Jackery MQTT discovery topic %s: %s",
                topic,
                err,
            )
        else:
            pending.discard(topic)
            removed += 1
    return removed


@callback
def async_start_mqtt_discovery_cleanup(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> Callable[[], None]:
    """Remove obsolete retained mirrors without blocking entry setup."""
    cleanup_task: asyncio.Task[None] | None = None
    connection_unsubscribe: Callable[[], None] | None = None
    stopped = False

    async def _async_cleanup() -> None:
        nonlocal cleanup_task, connection_unsubscribe
        try:
            if stopped or not _broker_connected(hass):
                return
            pending = orphaned_mirror_config_topics(hass, entry.entry_id)
            removed = await _async_clear_topics(hass, pending)
            if removed:
                _LOGGER.info("Removed %d obsolete Jackery MQTT sensor mirrors", removed)
            if pending:
                _LOGGER.warning(
                    "%d obsolete Jackery MQTT discovery topics remain; "
                    "cleanup will retry after the next MQTT reconnect",
                    len(pending),
                )
            elif connection_unsubscribe is not None:
                connection_unsubscribe()
                connection_unsubscribe = None
        finally:
            cleanup_task = None

    @callback
    def _schedule_cleanup() -> None:
        nonlocal cleanup_task
        if stopped or (cleanup_task is not None and not cleanup_task.done()):
            return
        cleanup_task = entry.async_create_background_task(
            hass,
            _async_cleanup(),
            name=f"{DOMAIN}_mqtt_discovery_cleanup_{entry.entry_id}",
            eager_start=False,
        )

    @callback
    def _connection_state_changed(connected: bool) -> None:
        if connected:
            _schedule_cleanup()

    connection_unsubscribe = mqtt.async_subscribe_connection_status(
        hass,
        _connection_state_changed,
    )
    if _broker_connected(hass):
        _schedule_cleanup()

    @callback
    def _stop_cleanup() -> None:
        nonlocal stopped, cleanup_task, connection_unsubscribe
        stopped = True
        if connection_unsubscribe is not None:
            connection_unsubscribe()
            connection_unsubscribe = None
        if cleanup_task is not None and not cleanup_task.done():
            cleanup_task.cancel()
        cleanup_task = None

    return _stop_cleanup
