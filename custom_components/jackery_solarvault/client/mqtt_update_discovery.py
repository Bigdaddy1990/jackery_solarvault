"""Mirror native, layer-specific update entities through HA MQTT discovery."""

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components import mqtt
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.util import slugify

from ..const import DOMAIN
from .mqtt_discovery import (
    _SharedTelemetry,
    _device_config,
    _finish_retained_publish,
    _record_mqtt_operation_error,
    _topic_publish_lock,
    _update_topics_for_entry as update_topics,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.components.update import UpdateEntity

    from .mqtt_discovery import _EntityDeviceLike

_RETRY_SECONDS = 30.0
_PUBLISH_TIMEOUT = 4.0
_CORE_UPDATE_DISCOVERY_FILTER = "homeassistant/update/+/+/config"


class _UpdateMirrorOperationError(Exception):
    """Keep the exact failed MQTT operation without exposing payloads."""

    def __init__(self, operation: str, topic: str, cause: Exception) -> None:
        super().__init__(operation)
        self.operation, self.topic, self.cause = operation, topic, cause


class JackeryMqttUpdatePublisher:
    """Mirror existing update entities without sending Jackery MQTT commands."""

    def __init__(  # ruff: ignore[undocumented-public-init]
        self,
        hass: Any,  # ruff: ignore[any-type]
        entry: Any,  # ruff: ignore[any-type]
        coordinator: Any,  # ruff: ignore[any-type]
        *,
        mirror_mode: bool = True,
    ) -> None:
        # Default preserved for legacy publishers; the unified platform
        # uses cleanup-only to avoid separate MQTT firmware entities.
        self.mirror_mode = mirror_mode
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        domain_data = hass.data.setdefault(DOMAIN, {})
        self.owner_bucket = domain_data.setdefault(entry.entry_id, {})
        previous = self.owner_bucket.get("mqtt_update_publisher")
        if previous is not None:
            previous.retire()
        retired = self.owner_bucket.pop("mqtt_update_retired", None)
        if retired is not None:
            retired.cancel_retirement()
        self.owner_bucket["mqtt_update_publisher"] = self
        shared = domain_data.get("mqtt_shared_telemetry")
        if not isinstance(shared, _SharedTelemetry):
            shared = _SharedTelemetry()
            domain_data["mqtt_shared_telemetry"] = shared
        self.shared = shared
        self.entities: dict[str, Any] = {}
        self.subscriptions: dict[str, Any] = {}
        self.published: dict[str, tuple[str, str, str, str]] = {}
        # Retained discovery from a removed entry can collide with the same
        # native unique_id after HA re-creates that entry.
        self.legacy_discovery_topics: dict[str, str] = {}
        self.legacy_cleared_topics: set[str] = set()
        self.legacy_discovery_unsubscribe: Any = None
        self.broker_generation = 0
        self.pending = False
        self.stopping = False
        self.task: asyncio.Task[None] | None = None
        self.retry: Callable[[], None] | None = None
        self.inflight: set[str] = set()
        self.retirement_tasks: set[asyncio.Task[None]] = set()
        self.unsubscribers: list[Any] = []
        try:
            self.unsubscribers.append(
                hass.bus.async_listen(
                    er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_event
                )
            )
            self.unsubscribers.append(
                hass.bus.async_listen(EVENT_STATE_CHANGED, self._state_event)
            )
            self.unsubscribers.append(
                mqtt.async_subscribe_connection_status(hass, self._connection_event)
            )
            self.unsubscribers.append(coordinator.async_add_listener(self.schedule))
        except BaseException:
            self.retire()
            raise

    def track(self, entities: list[Any]) -> None:
        """Track native update entities using their unchanged unique IDs."""
        for entity in entities:
            if unique_id := getattr(entity, "unique_id", None):
                unique_id = str(unique_id)
                if (
                    previous := self.entities.get(unique_id)
                ) is not None and previous is not entity:
                    if unsubscribe := self.subscriptions.pop(unique_id, None):
                        unsubscribe()
                    self.published.pop(unique_id, None)
                self.entities[unique_id] = entity
        self.schedule()

    @callback
    def _registry_event(self, _event: Any) -> None:  # ruff: ignore[any-type]
        self.schedule()

    @callback
    def _state_event(self, event: Any) -> None:  # ruff: ignore[any-type]
        entity_id = event.data.get("entity_id")
        if any(
            getattr(entity, "entity_id", None) == entity_id
            for entity in self.entities.values()
        ):
            self.schedule()

    @callback
    def _legacy_discovery_message(self, message: Any) -> None:  # ruff: ignore[any-type, too-many-return-statements]
        """Queue only retained discovery belonging to our registered native updates."""
        if self.stopping or not getattr(message, "retain", False):
            return
        topic = getattr(message, "topic", "")
        prefix = f"homeassistant/update/{DOMAIN}/"
        if (
            not isinstance(topic, str)
            or not topic.startswith(prefix)
            or not topic.endswith("/config")
            or topic in self.legacy_cleared_topics
        ):
            return
        payload = message.payload
        if not isinstance(payload, dict):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError, AttributeError):
                return
        if not isinstance(payload, dict):
            return
        unique_id = payload.get("unique_id")
        unique_prefix = f"{DOMAIN}_mqtt_"
        if not isinstance(unique_id, str) or not unique_id.startswith(unique_prefix):
            return
        native_unique_id = unique_id.removeprefix(unique_prefix)
        canonical_id = native_unique_id
        if not self.mirror_mode:
            if native_unique_id.endswith("_device_cloud_firmware_update"):
                canonical_id = (
                    native_unique_id.removesuffix("_device_cloud_firmware_update")
                    + "_device_firmware_update"
                )
            elif native_unique_id.endswith("_cloud_firmware_update"):
                canonical_id = (
                    native_unique_id.removesuffix("_cloud_firmware_update")
                    + "_firmware_update"
                )
        entity = self.entities.get(canonical_id)
        if (
            entity is None
            or self.entry.runtime_data is not self.coordinator
            or self.owner_bucket.get("mqtt_update_publisher") is not self
        ):
            return
        old_entry, separator, _ = topic[len(prefix) : -len("/config")].partition("_")
        if (
            not separator  # ruff: ignore[too-many-boolean-expressions]
            or not old_entry
            or (old_entry == self.entry.entry_id and self.mirror_mode)
            or topic != update_topics(old_entry, native_unique_id)[3]
            or (
                old_entry != self.entry.entry_id
                and self.hass.config_entries.async_get_entry(old_entry) is not None
            )
        ):
            return
        if self.legacy_discovery_topics.get(topic) == canonical_id:
            return
        self.legacy_discovery_topics[topic] = canonical_id
        self.schedule()

    def _seed_legacy_from_mqtt_debug_info(self) -> None:
        """Find already-discovered retained updates even without MQTT replay.

        Home Assistant keeps accepted discovery topics in MQTT debug metadata.
        Use that read-only evidence to identify the old topic, then apply the
        same strict owner and unique-id checks as broker messages. The
        shared Core filter also lets us see retained configs Core did not accept.
        """
        mqtt_data = self.hass.data.get("mqtt")
        records = getattr(mqtt_data, "debug_info_entities", None)
        if not isinstance(records, dict):
            return
        prefix = f"homeassistant/update/{DOMAIN}/"
        for record in tuple(records.values()):
            if not isinstance(record, dict):
                continue
            discovery = record.get("discovery_data")
            if not isinstance(discovery, dict):
                continue
            topic = discovery.get("discovery_topic")
            if not isinstance(topic, str) or not topic.startswith(prefix):
                continue
            payload = discovery.get("discovery_payload")
            if not isinstance(payload, (dict, str, bytes)):
                continue
            self._legacy_discovery_message(
                SimpleNamespace(topic=topic, payload=payload, retain=True)
            )

    async def _async_reconcile_legacy_updates(self) -> bool:  # ruff: ignore[too-many-branches]
        """Retire obsolete retained configs only after the new one was published."""
        failed = False
        client = getattr(self.hass.data.get("mqtt"), "client", None)
        is_active_subscription = getattr(client, "is_active_subscription", None)
        topic_filter = _CORE_UPDATE_DISCOVERY_FILTER
        # Join Core's existing filter so Jackery unload leaves the subscription
        # identifier valid if Core is still sending a queued MQTT 5 SUBSCRIBE.
        if (
            self.legacy_discovery_unsubscribe is None
            and self.entities
            and callable(is_active_subscription)
            and is_active_subscription(topic_filter)
        ):
            try:
                async with asyncio.timeout(_PUBLISH_TIMEOUT):
                    unsubscribe = await mqtt.async_subscribe(
                        self.hass, topic_filter, self._legacy_discovery_message, qos=0
                    )
                if self.stopping:
                    unsubscribe()
                    return False
                self.legacy_discovery_unsubscribe = unsubscribe
            except asyncio.CancelledError:
                raise
            except Exception as err:  # ruff: ignore[blind-except]
                _record_mqtt_operation_error(
                    self.hass,
                    topic_filter,
                    "subscribe",
                    self.entry.entry_id,
                    err,
                    entry=self.entry,
                )
                failed = True
        self._seed_legacy_from_mqtt_debug_info()
        for topic, unique_id in tuple(self.legacy_discovery_topics.items()):
            if self.stopping:
                break
            entity = self.entities.get(unique_id)
            if entity is None:
                self.legacy_discovery_topics.pop(topic, None)
                continue
            # Retained discovery can arrive before HA registers native entities.
            # Keep this receipt until its rightful owner is ready.
            if self._native_id(unique_id, entity) is None:
                continue
            # Never remove an older config while its owner still exists.
            old_entry = topic.removeprefix(f"homeassistant/update/{DOMAIN}/").split(
                "_", 1
            )[0]
            if (
                old_entry != self.entry.entry_id
                and self.hass.config_entries.async_get_entry(old_entry) is not None
            ):
                self.legacy_discovery_topics.pop(topic, None)
                continue
            if self.mirror_mode and unique_id not in self.published:
                continue
            try:
                await self._publish(topic, "")
                # Reannounce only when a separate MQTT mirror remains enabled.
                # Cleanup-only never creates a second firmware endpoint.
                if self.mirror_mode:
                    await self._publish(
                        update_topics(self.entry.entry_id, unique_id)[3],
                        self.published[unique_id][2],
                    )
            except asyncio.CancelledError:
                raise
            except Exception as err:  # ruff: ignore[blind-except]
                failed = True
                _record_mqtt_operation_error(
                    self.hass,
                    topic,
                    "cleanup_publish",
                    unique_id,
                    err,
                    entry=self.entry,
                )
            else:
                self.legacy_discovery_topics.pop(topic, None)
                self.legacy_cleared_topics.add(topic)
        return failed

    @callback
    def _connection_event(self, connected: bool) -> None:
        if connected:
            self.legacy_cleared_topics.clear()
            self.broker_generation += 1
            self.published.clear()
            if self.retry is not None:
                self.retry()
                self.retry = None
            self.schedule()

    @callback
    def schedule(self) -> None:  # ruff: ignore[undocumented-public-method]
        if self.stopping:
            return
        self.pending = True
        if self.retry is not None or (self.task is not None and not self.task.done()):
            return
        self.task = self.hass.async_create_background_task(
            self._run(),
            f"{DOMAIN}_mqtt_update_mirror_{self.entry.entry_id}",
            eager_start=False,
        )

    @callback
    def _retry(self, _now: Any) -> None:  # ruff: ignore[any-type]
        self.retry = None
        self.schedule()

    def _native_id(self, unique_id: str, entity: Any) -> str | None:  # ruff: ignore[any-type]
        if (
            self.stopping
            or self.entry.runtime_data is not self.coordinator
            or self.owner_bucket.get("mqtt_update_publisher") is not self
            or self.entities.get(unique_id) is not entity
        ):
            return None
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id("update", DOMAIN, unique_id)
        registered = registry.async_get(entity_id) if entity_id else None
        if (
            registered is None
            or registered.config_entry_id != self.entry.entry_id
            or getattr(entity, "entity_id", None) != entity_id
            or self.hass.states.get(entity_id) is None
        ):
            return None
        return entity_id

    async def _publish(
        self, topic: str, payload: str, *, offline: bool = False
    ) -> None:
        lock = _topic_publish_lock(self.shared, topic)
        async with lock:
            owner = self.owner_bucket.get("mqtt_update_publisher")
            if (offline and owner is not None) or (
                not offline and (self.stopping or owner is not self)
            ):
                return
            try:
                await _finish_retained_publish(
                    mqtt.async_publish(self.hass, topic, payload, qos=0, retain=True)
                )
            except asyncio.CancelledError:
                raise
            except Exception as err:
                raise _UpdateMirrorOperationError("publish", topic, err) from err

    def _hide_local_duplicate(self, unique_id: str, native_id: str) -> None:
        """Hide an extra local view once, preserving user choices and commands."""
        registry = er.async_get(self.hass)
        mirror_id = registry.async_get_entity_id(
            "update", "mqtt", f"{DOMAIN}_mqtt_{unique_id}"
        )
        mirror = registry.async_get(mirror_id) if mirror_id else None
        if mirror is None:
            return
        native = registry.async_get(native_id)
        if (
            native is None  # ruff: ignore[too-many-boolean-expressions]
            or native.config_entry_id != self.entry.entry_id
            or native.hidden_by is not None
            or native.disabled_by is not None
            or mirror.hidden_by is not None
            or mirror.disabled_by is not None
        ):
            return
        broker_entry = self.hass.config_entries.async_get_entry(mirror.config_entry_id)
        if broker_entry is None or broker_entry.domain != "mqtt":
            return
        options = dict(mirror.options.get(DOMAIN, {}))
        if options.get("native_update_duplicate_migrated"):
            return
        registry.async_update_entity(
            mirror.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
        )
        options["native_update_duplicate_migrated"] = True
        registry.async_update_entity_options(mirror.entity_id, DOMAIN, options)

    async def _run(self) -> None:  # ruff: ignore[too-many-branches]
        failed = False
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            while self.pending and not self.stopping:
                self.pending = False
                for unique_id, entity in tuple(self.entities.items()):
                    # The stop flag can change while earlier entities await MQTT.
                    if getattr(self, "stopping", False):
                        return
                    try:  # ruff: ignore[too-many-statements-in-try-clause]
                        entity_id = self._native_id(unique_id, entity)
                        if entity_id is not None and self.mirror_mode:
                            self._hide_local_duplicate(unique_id, entity_id)
                            await self._publish_entity(unique_id, entity, entity_id)
                        elif (
                            self.mirror_mode
                            and (previous := self.published.get(unique_id)) is not None
                            and previous[1] != "offline"
                        ):
                            # A removed, disabled or replaced native entity must
                            # not leave a retained mirror falsely online.
                            availability = update_topics(
                                self.entry.entry_id, unique_id
                            )[1]
                            await self._publish(availability, "offline")
                            self.published[unique_id] = (
                                previous[0],
                                "offline",
                                previous[2],
                                previous[3],
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception as err:  # ruff: ignore[blind-except]
                        failed = True
                        operation = (
                            err.operation
                            if isinstance(err, _UpdateMirrorOperationError)
                            else "publish"
                        )
                        topic = (
                            err.topic
                            if isinstance(err, _UpdateMirrorOperationError)
                            else "update_mirror"
                        )
                        cause = (
                            err.cause
                            if isinstance(err, _UpdateMirrorOperationError)
                            else err
                        )
                        _record_mqtt_operation_error(
                            self.hass,
                            topic,
                            operation,
                            unique_id,
                            cause,
                            entry=self.entry,
                        )
                if not failed and await self._async_reconcile_legacy_updates():
                    failed = True
                if failed:
                    self.pending = True
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:  # ruff: ignore[blind-except]
            failed = True
            self.pending = True
            _record_mqtt_operation_error(
                self.hass,
                "update_mirror",
                "publish",
                self.entry.entry_id,
                err,
                entry=self.entry,
            )
        finally:
            self.task = None
            if failed and not self.stopping:
                self.retry = async_call_later(self.hass, _RETRY_SECONDS, self._retry)

    async def _publish_entity(  # ruff: ignore[too-many-locals]
        self,
        unique_id: str,
        entity: UpdateEntity,
        entity_id: str,
    ) -> None:
        generation = self.broker_generation
        source = next(iter(getattr(entity, "command_sources", ())), None)
        if source not in {"ble", "http"}:
            return
        state_topic, availability_topic, command_topic, discovery_topic = update_topics(
            self.entry.entry_id, unique_id
        )
        if unique_id not in self.subscriptions:

            @callback
            def command(message: Any) -> None:  # ruff: ignore[any-type]
                current_id = self._native_id(unique_id, entity)
                if (
                    getattr(message, "retain", False)  # ruff: ignore[too-many-boolean-expressions]
                    or message.payload != "install"
                    or unique_id not in self.published
                    or self.published[unique_id][1] != "online"
                    or unique_id in self.inflight
                    or current_id is None
                ):
                    return
                self.inflight.add(unique_id)
                self.hass.async_create_background_task(
                    self._install(unique_id, entity, current_id),
                    f"{DOMAIN}_mqtt_update_install",
                    eager_start=False,
                )

            try:
                async with asyncio.timeout(_PUBLISH_TIMEOUT):
                    self.subscriptions[unique_id] = await mqtt.async_subscribe(
                        self.hass, command_topic, command, qos=0
                    )
            except asyncio.CancelledError:
                raise
            except Exception as err:
                raise _UpdateMirrorOperationError(
                    "subscribe", command_topic, err
                ) from err
        if self._native_id(unique_id, entity) != entity_id:
            unsubscribe = self.subscriptions.pop(unique_id, None)
            if unsubscribe is not None:
                unsubscribe()
            return
        if self.broker_generation != generation:
            self.pending = True
            return
        # Firmware updates expose the device-info subset used by this helper;
        # the sensor-oriented Protocol also demands native_value, which
        # UpdateEntity does not implement.
        _device_id, device = _device_config(cast("_EntityDeviceLike", entity))
        state_values = {
            "installed_version": entity.installed_version,
            "latest_version": entity.latest_version,
            "in_progress": entity.in_progress,
            "update_percentage": entity.update_percentage,
        }
        state = json.dumps({
            key: value
            for key, value in state_values.items()
            if value is not None or key == "update_percentage"
        })
        # MQTT update entities retain omitted fields. Never leave an older
        # version/progress visible as current when the native source lost it.
        complete = all(
            state_values[key] is not None
            for key in ("installed_version", "latest_version", "in_progress")
        )
        availability = "online" if entity.available and complete else "offline"
        attributes_topic = state_topic.rsplit("/", 1)[0] + "/attributes"
        attributes = json.dumps(
            getattr(entity, "extra_state_attributes", None) or {}, sort_keys=True
        )
        config = json.dumps(
            {
                "name": f"Firmware ({"BLE" if source == "ble" else "HTTP"})",
                "default_entity_id": "update.jackery_mqtt_" + slugify(unique_id),
                "unique_id": f"{DOMAIN}_mqtt_{unique_id}",
                "device": device,
                "device_class": "firmware",
                "entity_category": "config",
                "state_topic": state_topic,
                "availability_topic": availability_topic,
                "json_attributes_topic": attributes_topic,
                "json_attributes_template": (
                    "{{ value_json | tojson "
                    "if value_json is defined and value_json is mapping else '{}' }}"
                ),
                "command_topic": command_topic,
                "payload_install": "install",
            },
            sort_keys=True,
        )
        values = state, availability, config, attributes
        previous = self.published.get(unique_id)
        # When information disappears, withdraw availability before sending
        # an incomplete state: MQTT update entities retain omitted fields.
        publications = [(0, state_topic, state), (1, availability_topic, availability)]
        if availability == "offline":
            publications.reverse()
        publications.extend((
            (3, attributes_topic, attributes),
            (2, discovery_topic, config),
        ))
        for index, topic, payload in publications:
            if previous is None or previous[index] != payload:
                await self._publish(topic, payload)
                if self._native_id(unique_id, entity) != entity_id:
                    return
                if self.broker_generation != generation:
                    self.pending = True
                    return
        self.published[unique_id] = values

    async def _install(self, unique_id: str, entity: Any, entity_id: str) -> None:  # ruff: ignore[any-type]
        try:
            if (
                self._native_id(unique_id, entity) != entity_id
                or not entity.available
                or entity.installed_version is None
                or entity.latest_version is None
                or entity.in_progress is None
            ):
                return
            await self.hass.services.async_call(
                "update", "install", {"entity_id": entity_id}, blocking=True
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # ruff: ignore[blind-except]
            _record_mqtt_operation_error(
                self.hass,
                update_topics(self.entry.entry_id, unique_id)[2],
                "install",
                entity_id,
                err,
                entry=self.entry,
            )
        finally:
            self.inflight.discard(unique_id)
            self.schedule()

    @callback
    def retire(self) -> None:
        """Stop subscriptions and mark mirrored entities unavailable."""
        if self.stopping:
            return
        self.stopping = True
        if self.owner_bucket.get("mqtt_update_publisher") is self:
            self.owner_bucket.pop("mqtt_update_publisher", None)
        self.owner_bucket["mqtt_update_retired"] = self
        if self.retry is not None:
            self.retry()
            self.retry = None
        if self.task is not None and not self.task.done():
            self.task.cancel()
        for unsubscribe in (*self.unsubscribers, *self.subscriptions.values()):
            unsubscribe()
        self.subscriptions.clear()
        if self.legacy_discovery_unsubscribe is not None:
            self.legacy_discovery_unsubscribe()
            self.legacy_discovery_unsubscribe = None
        self.legacy_discovery_topics.clear()
        for unique_id in self.published:
            topic = update_topics(self.entry.entry_id, unique_id)[1]
            task = self.hass.async_create_background_task(
                self._retire_availability(topic),
                f"{DOMAIN}_mqtt_update_offline",
                eager_start=False,
            )
            self.retirement_tasks.add(task)
            task.add_done_callback(self._retirement_finished)
        if (
            not self.retirement_tasks
            and self.owner_bucket.get("mqtt_update_retired") is self
        ):
            self.owner_bucket.pop("mqtt_update_retired", None)

    def _retirement_finished(self, task: asyncio.Task[None]) -> None:
        self.retirement_tasks.discard(task)
        if (
            not self.retirement_tasks
            and self.owner_bucket.get("mqtt_update_retired") is self
        ):
            self.owner_bucket.pop("mqtt_update_retired", None)

    async def _retire_availability(self, topic: str) -> None:
        """Retry offline availability until a replacement takes ownership."""
        while self.owner_bucket.get("mqtt_update_publisher") is None and not getattr(
            self.hass, "is_stopping", False
        ):
            try:
                await self._publish(topic, "offline", offline=True)
                return  # ruff: ignore[try-consider-else]
            except asyncio.CancelledError:
                raise
            except Exception as err:  # ruff: ignore[blind-except]
                cause = (
                    err.cause if isinstance(err, _UpdateMirrorOperationError) else err
                )
                _record_mqtt_operation_error(
                    self.hass,
                    topic,
                    "publish",
                    self.entry.entry_id,
                    cause,
                    entry=self.entry,
                )
                await asyncio.sleep(_RETRY_SECONDS)

    @callback
    def cancel_retirement(self) -> None:
        """Prevent obsolete offline writes after replacement or entry removal."""
        for task in tuple(self.retirement_tasks):
            task.cancel()
