"""Value objects shared by the BLE notification spool layers."""

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum


class BleProcessDisposition(StrEnum):
    """Explicit downstream outcome for one BLE notification delivery."""

    CONFIRMED = "confirmed"
    RETRY = "retry"
    FRAGMENT = "fragment"
    UNROUTED = "unrouted"
    INVALID = "invalid"


class BleSpoolStatus(IntEnum):
    """Durable processing state of one raw BLE callback."""

    READY = 0
    FRAGMENT = 1
    ASSEMBLY_READY = 2
    UNROUTED = 3
    INVALID = 4


@dataclass(frozen=True, slots=True)
class BleSpoolTicket:
    """Identity assigned synchronously to one observed callback."""

    sequence: int
    delivery_id: str


@dataclass(frozen=True, slots=True)
class BleSpoolRecord:
    """One immutable raw notification stored in the write-ahead journal."""

    sequence: int
    delivery_id: str
    device_id: str
    session_id: str
    session_generation: int
    notify_sequence: int
    received_at: datetime
    raw: bytes
    status: BleSpoolStatus = BleSpoolStatus.READY
    assembly_key: str | None = None
    chunk_index: int | None = None
    chunk_count: int | None = None


@dataclass(frozen=True, slots=True)
class BleSpoolMetrics:
    """O(1) durable pressure counters."""

    pending_rows: int = 0
    pending_bytes: int = 0
    depth_high_watermark: int = 0
    bytes_high_watermark: int = 0


__all__ = [
    "BleProcessDisposition",
    "BleSpoolMetrics",
    "BleSpoolRecord",
    "BleSpoolStatus",
    "BleSpoolTicket",
]
