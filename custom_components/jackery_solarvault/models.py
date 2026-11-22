"""Shared transport ingest models and the supported API response envelope.

All types are runtime-resolvable — no ``from __future__ import annotations``
(ruff TID251).
"""

from dataclasses import dataclass, field
from enum import StrEnum
import logging
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

_LOGGER = logging.getLogger(__name__)

# =============================================================================
# Shared transport ingest
# =============================================================================


class DataSource(StrEnum):
    """Transport that produced a decoded Jackery observation."""

    HTTP = "http"
    CLOUD_MQTT = "cloud_mqtt"
    LOCAL_MQTT = "local_mqtt"
    BLE = "ble"


class BleProcessDisposition(StrEnum):
    """Explicit downstream outcome for one accepted BLE notification."""

    CONFIRMED = "confirmed"
    RETRY = "retry"
    FRAGMENT = "fragment"
    UNROUTED = "unrouted"
    INVALID = "invalid"


@dataclass(slots=True)
class BoxSafetyIngress:
    """Private receipt for a single accepted MQTT FIFO item."""

    source: DataSource
    topic: str
    identities: tuple[tuple[str, int, str], ...] = field(repr=False)
    received_at: datetime
    received_at_monotonic: float
    delivery_id: str = field(repr=False)
    content_digest: bytes | None = field(default=None, repr=False)
    payload_verified: bool = False
    active: bool = False
    claimed: bool = False
    identity_epochs: tuple[tuple[str, int], ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class Observation:
    """One decoded transport payload before it reaches coordinator state."""

    source: DataSource
    device_id: str
    section: str
    payload: Mapping[str, Any]
    observed_at: datetime | None = None
    request_id: str | None = None
    received_at_monotonic: float | None = None
    family: str | None = None
    device_identity: tuple[int, str] | None = field(default=None, repr=False)
    safety_owner: tuple[int, int, str, bytes] | None = field(default=None, repr=False)
    safety_received_at: datetime | None = None
    safety_received_monotonic: float | None = None
    safety_mqtt_delivery: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Reject ambiguous wall-clock timestamps at the ingest boundary."""
        if self.observed_at is not None and self.observed_at.utcoffset() is None:
            msg = "observed_at must be timezone-aware"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class FieldProvenance:
    """Source metadata retained outside entity-visible payload dictionaries."""

    source: DataSource
    section: str
    observed_at: datetime | None
    received_at_monotonic: float
    request_id: str | None = None
    family: str | None = None
    device_identity: tuple[int, str] | None = field(default=None, repr=False)
    safety_owner: tuple[int, int, str, bytes] | None = field(default=None, repr=False)
    safety_received_at: datetime | None = None
    safety_received_monotonic: float | None = None
    safety_mqtt_delivery: str | None = field(default=None, repr=False)
    safety_revoked_by: str | None = field(default=None, repr=False)


type ProvenanceKey = str | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Result of applying one observation to a single payload section."""

    payload: dict[str, Any]
    provenance: dict[ProvenanceKey, FieldProvenance]
    accepted_fields: frozenset[str]

    @property
    def accepted(self) -> bool:
        """Whether at least one field passed the ingest contract."""
        return bool(self.accepted_fields)


# =============================================================================
# Generic Wrapper
# =============================================================================


class ApiBaseResponse(TypedDict):
    """Generic API response envelope — every endpoint returns this shape.

    ``code`` is ``0`` on success.  ``data`` carries the endpoint-specific
    payload (dict, list, or ``None``).  ``rsaForAesKey`` is present only
    when the response body is AES-encrypted.
    """

    code: int
    msg: str
    data: dict[str, Any] | list[dict[str, Any]] | None
    encryption: bool
    rsaForAesKey: NotRequired[str]
