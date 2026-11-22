"""Bind optional Box safety evidence to one accepted MQTT FIFO item."""

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..models import BoxSafetyIngress

_LOGGER = logging.getLogger(__name__)


def safety_payload_digest(payload: object) -> bytes | None:
    """Bind a receipt to its exact decoded cloud or raw local payload."""
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if isinstance(payload, dict):
            data = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        elif isinstance(payload, str):
            data = payload.encode()
        elif isinstance(payload, (bytes, bytearray)):
            data = bytes(payload)
        else:
            return None
    except (TypeError, ValueError, UnicodeError):
        return None
    return hashlib.sha256(data).digest()


class SafetyIngressRegistry:
    """Retain a receipt only while its exact queue item remains owned."""

    def __init__(
        self, capture: Callable[[str], BoxSafetyIngress | None] | None
    ) -> None:
        """Bind the capture callback and initialize the owned receipt registry."""
        self._capture = capture
        self._receipts: dict[int, tuple[object, BoxSafetyIngress]] = {}

    def add(self, item: tuple[str, Any]) -> None:
        """Capture safety evidence for this exact accepted queue item."""
        if self._capture is None:
            return
        try:
            receipt = self._capture(item[0])
        except Exception:  # ruff: ignore[blind-except] -- telemetry remains independent
            _LOGGER.warning("Unable to capture Box command-safety ingress evidence")
            return
        if receipt is not None:
            receipt.content_digest = safety_payload_digest(item[1])
            if receipt.content_digest is None:
                return
            self._receipts[id(item)] = (item, receipt)

    def get(self, item: object) -> BoxSafetyIngress | None:
        """Return the receipt belonging to the same live queue item."""
        entry = self._receipts.get(id(item))
        return entry[1] if entry is not None and entry[0] is item else None

    def finish(self, item: object) -> None:
        """Retire the receipt when its owning queue item finishes."""
        entry = self._receipts.get(id(item))
        if entry is not None and entry[0] is item:
            entry[1].active = False
            self._receipts.pop(id(item), None)
