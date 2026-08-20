"""Transport Supervisor abstraction for independent transport management.

Each transport (BLE, Cloud MQTT, Local MQTT) gets its own supervisor with
independent lifecycle, reconnect logic, and credential management.
"""

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from dataclasses import dataclass
from enum import StrEnum
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class SupervisorState(StrEnum):
    """Lifecycle states for a transport supervisor."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    DEGRADED = "degraded"


@dataclass(slots=True)
class SupervisorConfig:
    """Configuration for a transport supervisor."""

    name: str
    enabled_check: Callable[[ConfigEntry], bool]
    start_fn: Callable[[], Awaitable[Any]]
    stop_fn: Callable[[], Awaitable[Any]]
    update_credentials_fn: Callable[[], Awaitable[Any]] | None = None
    health_check_fn: Callable[[], bool] | None = None
    reconnect_delay_sec: float = 5.0
    max_reconnect_delay_sec: float = 300.0


class TransportSupervisor:
    """Independent supervisor for one transport layer."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: DataUpdateCoordinator,
        config: SupervisorConfig,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.config = config
        self._state = SupervisorState.STOPPED
        self._task: asyncio.Task[Any] | None = None
        self._reconnect_task: asyncio.Task[Any] | None = None
        self._shutdown = False
        self._last_error: Exception | None = None

    @property
    def state(self) -> SupervisorState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state == SupervisorState.RUNNING

    @property
    def is_starting(self) -> bool:
        return self._state == SupervisorState.STARTING

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    async def async_start(self) -> None:
        """Start the transport supervisor."""
        if not self.config.enabled_check(self.entry):
            _LOGGER.debug(
                "Transport %s is disabled via config entry option",
                self.config.name,
            )
            return

        if self._state in {SupervisorState.STARTING, SupervisorState.RUNNING}:
            return

        self._state = SupervisorState.STARTING
        _LOGGER.info("Starting transport supervisor: %s", self.config.name)

        try:
            await self.config.start_fn()
            self._state = SupervisorState.RUNNING
            _LOGGER.info("Transport supervisor started: %s", self.config.name)

            # Start health monitoring if available
            if self.config.health_check_fn:
                self._task = self.hass.async_create_background_task(
                    self._health_monitor(),
                    name=f"{self.config.name}_health_monitor",
                )

        except ConfigEntryAuthFailed:
            _LOGGER.warning(
                "Transport %s auth failure - deferring to HTTP path",
                self.config.name,
            )
            self._state = SupervisorState.DEGRADED
            raise
        except Exception as err:
            _LOGGER.warning(
                "Transport %s failed to start: %s - will retry independently",
                self.config.name,
                err,
            )
            self._last_error = err
            self._state = SupervisorState.DEGRADED
            self._schedule_reconnect()

    async def _health_monitor(self) -> None:
        """Monitor transport health and trigger reconnect if needed."""
        while not self._shutdown and self._state == SupervisorState.RUNNING:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                if self.config.health_check_fn and not self.config.health_check_fn():
                    _LOGGER.warning(
                        "Transport %s health check failed - marking degraded",
                        self.config.name,
                    )
                    self._state = SupervisorState.DEGRADED
                    self._schedule_reconnect()
                    break
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.debug(
                    "Health monitor error for %s: %s",
                    self.config.name,
                    err,
                )

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnect attempt with exponential backoff."""
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return

        delay = self.config.reconnect_delay_sec

        async def _reconnect() -> None:
            nonlocal delay
            while not self._shutdown and self._state != SupervisorState.RUNNING:
                if self._state == SupervisorState.STOPPED:
                    return
                await asyncio.sleep(delay)
                try:
                    await self.config.start_fn()
                    self._state = SupervisorState.RUNNING
                    _LOGGER.info("Transport %s reconnected successfully", self.config.name)
                    return
                except ConfigEntryAuthFailed:
                    self._state = SupervisorState.DEGRADED
                    return
                except Exception as err:
                    _LOGGER.debug(
                        "Transport %s reconnect attempt failed: %s",
                        self.config.name,
                        err,
                    )
                    delay = min(delay * 2, self.config.max_reconnect_delay_sec)
                # Continue loop for next attempt

        self._reconnect_task = self.hass.async_create_background_task(
            _reconnect(),
            name=f"{self.config.name}_reconnect",
        )

    async def async_update_credentials_or_metadata(self) -> None:
        """Update credentials or metadata for the transport."""
        if self.config.update_credentials_fn:
            try:
                await self.config.update_credentials_fn()
            except Exception as err:
                _LOGGER.debug(
                    "Transport %s credential update failed: %s",
                    self.config.name,
                    err,
                )

    async def async_stop(self) -> None:
        """Stop the transport supervisor and cancel all tasks."""
        _LOGGER.info("Stopping transport supervisor: %s", self.config.name)
        self._shutdown = True

        # Cancel health monitor
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        # Cancel reconnect
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task

        # Stop the transport
        try:
            await self.config.stop_fn()
        except Exception as err:
            _LOGGER.warning(
                "Error stopping transport %s: %s",
                self.config.name,
                err,
            )

        self._state = SupervisorState.STOPPED
        _LOGGER.info("Transport supervisor stopped: %s", self.config.name)


class TransportSupervisorManager:
    """Manages all transport supervisors for an integration."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: DataUpdateCoordinator,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._supervisors: dict[str, TransportSupervisor] = {}

    def register(
        self,
        name: str,
        config: SupervisorConfig,
    ) -> TransportSupervisor:
        """Register a new transport supervisor."""
        supervisor = TransportSupervisor(self.hass, self.entry, self.coordinator, config)
        self._supervisors[name] = supervisor
        return supervisor

    def get(self, name: str) -> TransportSupervisor | None:
        """Get a supervisor by name."""
        return self._supervisors.get(name)

    async def async_start_all(self) -> None:
        """Start all registered supervisors concurrently."""
        start_tasks = []
        for name, supervisor in self._supervisors.items():
            if supervisor.config.enabled_check(self.entry):
                start_tasks.append(supervisor.async_start())

        if start_tasks:
            results = await asyncio.gather(*start_tasks, return_exceptions=True)
            for (name, _), result in zip(self._supervisors.items(), results, strict=False):
                if isinstance(result, ConfigEntryAuthFailed):
                    _LOGGER.warning(
                        "Transport %s auth failure - HTTP remains auth authority",
                        name,
                    )
                elif isinstance(result, Exception):
                    _LOGGER.warning(
                        "Transport %s startup failed: %s - running independently",
                        name,
                        result,
                    )

    async def async_stop_all(self) -> None:
        """Stop all supervisors."""
        stop_tasks = [s.async_stop() for s in self._supervisors.values()]
        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

    async def async_update_all_credentials(self) -> None:
        """Update credentials for all supervisors after HTTP refresh."""
        update_tasks = [
            s.async_update_credentials_or_metadata()
            for s in self._supervisors.values()
        ]
        if update_tasks:
            await asyncio.gather(*update_tasks, return_exceptions=True)

    @property
    def states(self) -> dict[str, SupervisorState]:
        """Get current states of all supervisors."""
        return {name: s.state for name, s in self._supervisors.items()}
