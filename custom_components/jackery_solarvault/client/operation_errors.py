"""Bounded operation evidence honoring explicit owner raw debugging."""

from typing import TYPE_CHECKING, Any

from .credentials import redacted_error

_MAX_CAUSES = 64

if TYPE_CHECKING:
    import logging


class MqttPublishOutcomeUnknownError(RuntimeError):
    """A publish started but its broker outcome could not be established."""


class BridgeWriteOutcomeUnknownError(RuntimeError):
    """A bridge configuration write was attempted but not confirmed."""


class OperationErrors:
    """Count real failures and warn once per operation and redacted cause."""

    def __init__(self, logger: logging.Logger, entity: str) -> None:
        """Bind operation evidence to the caller's logger and transport identity."""
        self._logger = logger
        self._entity = entity
        self._causes: dict[tuple[str, str, str], int] = {}
        self._errors = 0
        self._last_error: dict[str, Any] | None = None

    def record(  # ruff: ignore[too-many-arguments] - operation evidence context
        self,
        error: Exception,
        *,
        operation: str,
        target: str,
        qos: int | None,
        entry: object | None = None,
        error_text: str | None = None,
    ) -> None:
        """Record bounded failure evidence using the active debug choice."""
        text = (
            error_text if error_text is not None else redacted_error(error, entry=entry)
        )
        cause = (operation, target, text)
        if cause not in self._causes and len(self._causes) >= _MAX_CAUSES:
            self._causes.pop(next(iter(self._causes)))
        self._causes[cause] = self._causes.get(cause, 0) + 1
        self._errors += 1
        self._last_error = {
            "operation": operation,
            "target": target,
            "entity": self._entity,
            "qos": qos,
            "error": text,
        }
        log = self._logger.warning if self._causes[cause] == 1 else self._logger.debug
        log(
            "Jackery operation failed: operation=%s target=%s qos=%s entity=%s "
            "error=%s count=%d",
            operation,
            target,
            qos,
            self._entity,
            text,
            self._causes[cause],
        )

    def snapshot(self) -> dict[str, Any]:
        """Return public evidence without the internal cause cache."""
        return {"errors": self._errors, "last_error": self._last_error}
