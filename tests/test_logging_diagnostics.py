"""Regression tests for logging/diagnostics visibility.

Two logging rules are covered:

1. Redacted payload JSONL capture is opt-in. The config-entry option or the
   effective DEBUG level of the dedicated ``payload_debug`` logger activates
   it. ``JACKERY_DEV_MODE=1`` alone must not.
2. Local MQTT connection failures must be visible at WARNING in the default
   Home Assistant log, not swallowed at DEBUG.
"""

from collections import deque
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Self, cast
from unittest.mock import AsyncMock, MagicMock, patch

from aiomqtt import MqttError
import pytest

from custom_components.jackery_solarvault.client import local_mqtt as local_mqtt_module
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)
from custom_components.jackery_solarvault.const import (
    CONF_ENABLE_PAYLOAD_DEBUG_LOG,
    PAYLOAD_DEBUG_LOGGER_NAME,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOCAL_MQTT_LOGGER = "custom_components.jackery_solarvault.client.local_mqtt"


@pytest.fixture
def restore_payload_debug_logger() -> Iterator[logging.Logger]:
    """Yield the payload logger and restore its and its parent's levels."""
    logger = logging.getLogger(PAYLOAD_DEBUG_LOGGER_NAME)
    parent = logger.parent
    old_level = logger.level
    old_parent_level = parent.level
    try:
        yield logger
    finally:
        logger.setLevel(old_level)
        parent.setLevel(old_parent_level)


def _payload_debug_coordinator(
    *,
    options: dict[str, Any] | None = None,
) -> JackerySolarVaultCoordinator:
    """Build the minimal coordinator shell the payload-debug writer touches."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.entry = SimpleNamespace(
        data={},
        options=options or {},
        entry_id="log-test-entry",
    )
    obj.hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda *_a: "payload_debug.jsonl"),
        async_add_executor_job=AsyncMock(side_effect=lambda func, *args: func(*args)),
    )
    obj._shutdown_started = False  # ruff: ignore[private-member-access]
    obj._payload_debug_pending_events = deque()  # ruff: ignore[private-member-access]
    obj._payload_debug_last_sig = {}  # ruff: ignore[private-member-access]
    obj._payload_debug_last_emit_ts = {}  # ruff: ignore[private-member-access]
    return coordinator


@pytest.mark.asyncio
async def test_dev_mode_alone_does_not_activate_payload_debug_capture(
    restore_payload_debug_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JACKERY_DEV_MODE=1 alone must not switch on payload capture."""
    restore_payload_debug_logger.setLevel(logging.WARNING)
    monkeypatch.setenv("JACKERY_DEV_MODE", "1")

    coordinator = _payload_debug_coordinator()
    writes: list[dict[str, Any]] = []

    def _capture(_path: str, event: dict[str, Any]) -> None:
        writes.append(event)

    with patch(
        "custom_components.jackery_solarvault.coordinator.append_payload_debug_line",
        side_effect=_capture,
    ):
        await coordinator._async_payload_debug_event(  # ruff: ignore[private-member-access]
            {"kind": "http", "path": "/v1/x", "payload": {"soc": 55}},
        )

    assert not writes, "dev mode alone must not run the payload writer"


@pytest.mark.asyncio
async def test_payload_debug_option_activates_capture(
    restore_payload_debug_logger: logging.Logger,
) -> None:
    """The explicit config-entry option activates redacted payload capture."""
    restore_payload_debug_logger.setLevel(logging.WARNING)
    coordinator = _payload_debug_coordinator(
        options={CONF_ENABLE_PAYLOAD_DEBUG_LOG: True},
    )
    writes: list[dict[str, Any]] = []

    def _capture(_path: str, event: dict[str, Any]) -> None:
        writes.append(event)

    with patch(
        "custom_components.jackery_solarvault.coordinator.append_payload_debug_line",
        side_effect=_capture,
    ):
        await coordinator._async_payload_debug_event(  # ruff: ignore[private-member-access]
            {"kind": "http", "path": "/v1/x", "payload": {"soc": 55}},
        )

    assert writes, "the payload-debug option must activate the payload writer"


@pytest.mark.asyncio
async def test_debug_logger_activates_payload_debug_capture(
    restore_payload_debug_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raising the dedicated logger to DEBUG activates the JSONL capture."""
    restore_payload_debug_logger.setLevel(logging.DEBUG)
    monkeypatch.delenv("JACKERY_DEV_MODE", raising=False)

    coordinator = _payload_debug_coordinator()
    writes: list[dict[str, Any]] = []

    def _capture(_path: str, event: dict[str, Any]) -> None:
        writes.append(event)

    with patch(
        "custom_components.jackery_solarvault.coordinator.append_payload_debug_line",
        side_effect=_capture,
    ):
        await coordinator._async_payload_debug_event(  # ruff: ignore[private-member-access]
            {"kind": "http", "path": "/v1/x", "payload": {"soc": 55}},
        )

    assert writes, "a DEBUG payload_debug logger must run the raw-payload writer"


@pytest.mark.asyncio
async def test_inherited_effective_debug_logger_activates_capture(
    restore_payload_debug_logger: logging.Logger,
) -> None:
    """An inherited DEBUG level is honored when the child level is NOTSET."""
    restore_payload_debug_logger.setLevel(logging.NOTSET)
    restore_payload_debug_logger.parent.setLevel(logging.DEBUG)
    coordinator = _payload_debug_coordinator()
    writes: list[dict[str, Any]] = []

    def _capture(_path: str, event: dict[str, Any]) -> None:
        writes.append(event)

    with patch(
        "custom_components.jackery_solarvault.coordinator.append_payload_debug_line",
        side_effect=_capture,
    ):
        await coordinator._async_payload_debug_event(  # ruff: ignore[private-member-access]
            {"kind": "http", "path": "/v1/x", "payload": {"soc": 55}},
        )

    assert writes, "an inherited effective DEBUG level must activate capture"


@pytest.mark.asyncio
async def test_payload_debug_capture_off_without_dev_mode_or_debug_logger(
    restore_payload_debug_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With dev mode off and the logger below DEBUG the file stays unwritten."""
    restore_payload_debug_logger.setLevel(logging.WARNING)
    monkeypatch.delenv("JACKERY_DEV_MODE", raising=False)

    coordinator = _payload_debug_coordinator()
    writes: list[dict[str, Any]] = []

    def _capture(_path: str, event: dict[str, Any]) -> None:
        writes.append(event)

    with patch(
        "custom_components.jackery_solarvault.coordinator.append_payload_debug_line",
        side_effect=_capture,
    ):
        await coordinator._async_payload_debug_event(  # ruff: ignore[private-member-access]
            {"kind": "http", "path": "/v1/x", "payload": {"soc": 55}},
        )

    assert not writes, "capture must stay off without dev mode or a DEBUG logger"


class _FailingAioMqttClient:
    """Stand-in aiomqtt client whose connect always refuses."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        """Accept and ignore the real client's constructor signature."""

    async def __aenter__(self) -> Self:
        """Fail the connection the way an unreachable broker would."""
        msg = "Could not connect to broker"
        raise MqttError(msg)

    async def __aexit__(self, *_exc: object) -> bool:
        """Never suppress the raised connection error."""
        return False


@pytest.mark.asyncio
async def test_local_mqtt_connect_failure_logs_warning(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable local broker must log a WARNING carrying the host:port."""
    monkeypatch.setattr(
        local_mqtt_module.aiomqtt,
        "Client",
        _FailingAioMqttClient,
    )
    client = JackeryLocalMqttClient(
        MagicMock(),
        host="192.0.2.10",
        port=1883,
        username=None,
        password=None,
        client_id="test-client",
        sink=None,
        topic_filter="hb/app/#",
    )

    with caplog.at_level(logging.WARNING, logger=_LOCAL_MQTT_LOGGER):
        await client._async_run_session()  # ruff: ignore[private-member-access]

    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.name == _LOCAL_MQTT_LOGGER
    ]
    assert warnings, "a failed local MQTT connection must log at WARNING"
    assert any("192.0.2.10:1883" in record.getMessage() for record in warnings)
