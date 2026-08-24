"""Crash-safe write-ahead journal for accepted BLE notifications."""

import asyncio
from collections import deque
from collections.abc import Callable, Coroutine, Sequence
from datetime import datetime
from hashlib import sha256
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from homeassistant.core import callback

from ..const import DOMAIN
from .ble_notification_spool_backend import _SqliteBleSpoolBackend
from .ble_notification_spool_models import (
    BleSpoolMetrics,
    BleSpoolRecord,
    BleSpoolStatus,
    BleSpoolTicket,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_WRITER_BATCH_ROWS = 128
_WRITER_RETRY_INITIAL_SEC = 0.05
_WRITER_RETRY_MAX_SEC = 5.0


class BleNotificationSpool:
    """Entry-owned asynchronous write-ahead BLE notification spool."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        entry_id: str,
        entry: ConfigEntry | None = None,
        path: Path | None = None,
        backend_factory: Callable[
            [Path, str], _SqliteBleSpoolBackend
        ] = _SqliteBleSpoolBackend,
    ) -> None:
        """Initialize an entry-scoped spool without opening its database yet."""
        self._hass = hass
        self._entry = entry
        self._entry_id = entry_id
        if path is None:
            digest = sha256(entry_id.encode()).hexdigest()[:32]
            path = Path(
                hass.config.path(
                    ".storage",
                    DOMAIN,
                    f"ble-spool-{digest}.sqlite3",
                )
            )
        self._backend = backend_factory(path, entry_id)
        self._namespace: str | None = None
        self._next_sequence = 1
        self._durable_through = 0
        self._staging: deque[BleSpoolRecord] = deque()
        self._staging_bytes = 0
        self._metrics = BleSpoolMetrics()
        self._writer_task: asyncio.Task[None] | None = None
        self._writer_condition = asyncio.Condition()
        self._mutation_lock = asyncio.Lock()
        self._last_persist_error: Exception | None = None
        self._opened = False
        self._closing = False

    @property
    def namespace(self) -> str:
        """Persistent entry namespace after opening."""
        if self._namespace is None:
            raise RuntimeError("Jackery BLE spool is not open")
        return self._namespace

    @property
    def metrics(self) -> BleSpoolMetrics:
        """O(1) pressure metrics including uncommitted staging."""
        pending_rows = self._metrics.pending_rows + len(self._staging)
        pending_bytes = self._metrics.pending_bytes + self._staging_bytes
        return BleSpoolMetrics(
            pending_rows=pending_rows,
            pending_bytes=pending_bytes,
            depth_high_watermark=max(
                self._metrics.depth_high_watermark,
                pending_rows,
            ),
            bytes_high_watermark=max(
                self._metrics.bytes_high_watermark,
                pending_bytes,
            ),
        )

    async def _async_executor(self, method: Callable[..., Any], *args: Any) -> Any:
        add_executor_job = getattr(self._hass, "async_add_executor_job", None)
        if callable(add_executor_job):
            return await add_executor_job(method, *args)
        return await asyncio.to_thread(method, *args)

    def _create_background_task(
        self,
        target: Coroutine[Any, Any, None],
        *,
        name: str,
    ) -> asyncio.Task[None]:
        try:
            if self._entry is not None:
                return self._entry.async_create_background_task(
                    self._hass,
                    target,
                    name=name,
                    eager_start=False,
                )
            create_task = getattr(self._hass, "async_create_background_task", None)
            if callable(create_task):
                return cast(
                    "asyncio.Task[None]",
                    create_task(target, name=name, eager_start=False),
                )
            return asyncio.create_task(target, name=name)
        except Exception:
            target.close()
            raise

    async def async_open(self) -> None:
        """Open, validate, and recover the persistent spool."""
        if self._opened:
            return
        namespace, next_sequence, metrics = await self._async_executor(
            self._backend.initialize
        )
        self._namespace = namespace
        self._next_sequence = next_sequence
        self._durable_through = next_sequence - 1
        self._metrics = metrics
        self._opened = True

    @callback
    def stage_notification(
        self,
        *,
        device_id: str,
        session_id: str,
        session_generation: int,
        notify_sequence: int,
        received_at: datetime,
        raw: bytes,
    ) -> BleSpoolTicket:
        """Observe one callback and schedule its write-ahead commit."""
        if not self._opened or self._namespace is None:
            raise RuntimeError("Jackery BLE spool is not open")
        if self._closing:
            raise RuntimeError("Jackery BLE spool is closing")
        sequence = self._next_sequence
        self._next_sequence += 1
        delivery_id = f"{self._namespace}:n:{sequence}"
        record = BleSpoolRecord(
            sequence=sequence,
            delivery_id=delivery_id,
            device_id=device_id,
            session_id=session_id,
            session_generation=session_generation,
            notify_sequence=notify_sequence,
            received_at=received_at,
            raw=bytes(raw),
        )
        self._staging.append(record)
        self._staging_bytes += len(record.raw)
        self._async_start_writer()
        return BleSpoolTicket(sequence=sequence, delivery_id=delivery_id)

    @callback
    def _async_start_writer(self) -> bool:
        """Start exactly one finite entry-owned commit worker when HA accepts it."""
        if self._writer_task is not None and not self._writer_task.done():
            return True
        target = self._async_writer()
        try:
            self._writer_task = self._create_background_task(
                target,
                name=f"{DOMAIN}_ble_spool_writer_{self._entry_id}",
            )
        except Exception as err:  # ruff: ignore[blind-except]
            # The synchronous Bleak callback cannot await or retry task creation.
            # Keep the immutable row in staging; the next flush/start attempt owns it.
            self._last_persist_error = err
            self._writer_task = None
            return False
        return True

    async def _async_writer(self) -> None:
        """Commit staged rows in order and retain them on every failure."""
        retry_delay = _WRITER_RETRY_INITIAL_SEC
        try:
            while self._staging:
                batch = tuple(islice(self._staging, 0, _WRITER_BATCH_ROWS))
                try:
                    async with self._mutation_lock:
                        metrics = await self._async_executor(
                            self._backend.append_batch,
                            batch,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as err:  # ruff: ignore[blind-except]
                    self._last_persist_error = err
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, _WRITER_RETRY_MAX_SEC)
                    continue
                retry_delay = _WRITER_RETRY_INITIAL_SEC
                self._last_persist_error = None
                for expected in batch:
                    current = self._staging.popleft()
                    if current.delivery_id != expected.delivery_id:
                        raise RuntimeError("Jackery BLE staging FIFO ownership changed")
                    self._staging_bytes -= len(current.raw)
                async with self._writer_condition:
                    self._metrics = metrics
                    self._durable_through = max(
                        self._durable_through,
                        batch[-1].sequence,
                    )
                    self._writer_condition.notify_all()
        finally:
            async with self._writer_condition:
                self._writer_task = None
                self._writer_condition.notify_all()
            if self._staging and not self._closing:
                self._async_start_writer()

    async def async_flush_through(self, sequence: int) -> None:
        """Wait until every observed record through ``sequence`` is durable."""
        while self._durable_through < sequence:
            if self._writer_task is None or self._writer_task.done():
                if not self._async_start_writer():
                    msg = "Home Assistant rejected the Jackery BLE spool writer"
                    raise RuntimeError(msg) from self._last_persist_error
            async with self._writer_condition:
                await self._writer_condition.wait_for(
                    lambda: (
                        self._durable_through >= sequence
                        or self._writer_task is None
                        or self._writer_task.done()
                    )
                )

    async def async_flush(self) -> None:
        """Commit every record observed before this call."""
        target = self._next_sequence - 1
        if target >= 1:
            await self.async_flush_through(target)

    async def async_load_records(self) -> tuple[BleSpoolRecord, ...]:
        """Load all durable, unconfirmed rows in callback order."""
        await self.async_flush()
        return cast(
            "tuple[BleSpoolRecord, ...]",
            await self._async_executor(self._backend.load_records),
        )

    async def async_load_record(self, sequence: int) -> BleSpoolRecord | None:
        """Load one durable, unconfirmed row by sequence."""
        if sequence <= 0 or sequence >= self._next_sequence:
            return None
        await self.async_flush_through(sequence)
        return cast(
            "BleSpoolRecord | None",
            await self._async_executor(self._backend.load_record, sequence),
        )

    async def async_confirm(self, sequences: Sequence[int]) -> None:
        """Delete only rows explicitly confirmed by the downstream sink."""
        durable_sequences = tuple(
            sequence for sequence in sequences if 0 < sequence < self._next_sequence
        )
        if not durable_sequences:
            return
        await self.async_flush_through(max(durable_sequences))
        async with self._mutation_lock:
            self._metrics = cast(
                "BleSpoolMetrics",
                await self._async_executor(
                    self._backend.delete_sequences,
                    durable_sequences,
                ),
            )

    async def async_mark_status(
        self,
        sequence: int,
        status: BleSpoolStatus,
        *,
        assembly_key: str | None = None,
        chunk_index: int | None = None,
        chunk_count: int | None = None,
    ) -> None:
        """Persist a fragment, unrouted, or invalid disposition without deletion."""
        if sequence <= 0 or sequence >= self._next_sequence:
            raise ValueError(f"Unknown Jackery BLE spool sequence {sequence}")
        await self.async_flush_through(sequence)
        async with self._mutation_lock:
            updated = await self._async_executor(
                self._backend.mark_status,
                sequence,
                status,
                assembly_key,
                chunk_index,
                chunk_count,
            )
        if not updated:
            raise RuntimeError(
                f"Jackery BLE spool sequence {sequence} is no longer pending"
            )

    async def async_close(self) -> None:
        """Flush observed bytes and checkpoint the WAL without deleting records."""
        if not self._opened:
            return
        await self.async_flush()
        self._closing = True
        writer = self._writer_task
        if writer is not None and not writer.done():
            await asyncio.shield(writer)
        async with self._mutation_lock:
            await self._async_executor(self._backend.checkpoint)
        self._opened = False


__all__ = [
    "BleNotificationSpool",
    "BleSpoolMetrics",
    "BleSpoolRecord",
    "BleSpoolStatus",
    "BleSpoolTicket",
]
