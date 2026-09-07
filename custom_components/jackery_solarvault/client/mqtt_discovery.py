"""Remove obsolete MQTT-discovery mirrors of native Jackery sensors."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.components import mqtt
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from ..const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_DISCOVERY_PREFIX = "homeassistant"
_MIRROR_UNIQUE_ID_PREFIX = f"{DOMAIN}_mqtt_"
_CLEANUP_SCAN_DELAYS_SEC = (0.0, 5.0, 30.0)


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
        pending: set[str] = set()
        removed = 0
        initial_scan = True
        try:
            for delay in _CLEANUP_SCAN_DELAYS_SEC:
                if delay:
                    await asyncio.sleep(delay)
                if stopped or not mqtt.is_connected(hass):
                    return
                pending.update(registered_mirror_config_topics(hass))
                removed += await _async_clear_topics(hass, pending)
                if not initial_scan and not pending:
                    if connection_unsubscribe is not None:
                        connection_unsubscribe()
                        connection_unsubscribe = None
                    if removed:
                        _LOGGER.info(
                            "Removed %d obsolete Jackery MQTT sensor mirrors",
                            removed,
                        )
                    return
                initial_scan = False
            if pending:
                _LOGGER.warning(
                    "%d obsolete Jackery MQTT discovery topics remain; "
                    "cleanup will retry after the next MQTT reconnect",
                    len(pending),
                )
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
    if mqtt.is_connected(hass):
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
