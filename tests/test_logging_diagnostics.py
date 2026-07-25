"""Regression tests for logging/diagnostics visibility.

Two logging rules are covered:

1. Raw-payload JSONL capture is controlled exclusively through Home
   Assistant's logging system (deep review 2026-07-25): only raising the
   dedicated ``payload_debug`` logger to DEBUG activates it.
   ``JACKERY_DEV_MODE=1`` alone must NOT — coupling capture to the env
   flag made the trace grow unbounded on every dev-mode install.
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

from custom_components.jackery_solarvault import util as util_module
from custom_components.jackery_solarvault.client import local_mqtt as local_mqtt_module
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)
from custom_components.jackery_solarvault.const import PAYLOAD_DEBUG_LOGGER_NAME
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_LOCAL_MQTT_LOGGER = "custom_components.jackery_solarvault.client.local_mqtt"


@pytest.fixture(autouse=True)
def _reset_dev_mode_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the JACKERY_DEV_MODE cache deterministic for every test."""
    monkeypatch.setattr(util_module, "_DEV_MODE_CACHED", None)


@pytest.fixture()
def restore_payload_debug_logger() -> Iterator[logging.Logger]:
    """Yield the dedicated payload-debug logger and restore its level after."""
    logger = logging.getLogger(PAYLOAD_DEBUG_LOGGER_NAME)
    old_level = logger.level
    try:
        yield logger
    finally:
        logger.setLevel(old_level)


def _payload_debug_coordinator() -> JackerySolarVaultCoordinator:
    """Build the minimal coordinator shell the payload-debug writer touches."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.entry = SimpleNamespace(data={}, options={}, entry_id="log-test-entry")
    obj.hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda *_a: "payload_debug.jsonl"),
        async_add_executor_job=AsyncMock(side_effect=lambda func, *args: func(*args)),
    )
    obj._shutdown_started = False
    obj._payload_debug_pending_events = deque()
    obj._payload_debug_last_sig = {}
    obj._payload_debug_last_emit_ts = {}
    return coordinator


@pytest.mark.asyncio()
async def test_dev_mode_alone_does_not_activate_payload_debug_capture(
    restore_payload_debug_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JACKERY_DEV_MODE=1 alone must NOT switch on raw-payload capture.

    Capture is steered exclusively through HA's logging controls (deep
    review 2026-07-25); the env flag no longer force-enables the JSONL
    trace.
    """
    restore_payload_debug_logger.setLevel(logging.WARNING)
    monkeypatch.setenv("JACKERY_DEV_MODE", "1")
    monkeypatch.setattr(util_module, "_DEV_MODE_CACHED", None)

    coordinator = _payload_debug_coordinator()
    writes: list[dict[str, Any]] = []

    def _capture(_path: str, event: dict[str, Any], _redactions: bool) -> None:
        writes.append(event)

    with patch(
        "custom_components.jackery_solarvault.coordinator.append_payload_debug_line",
        side_effect=_capture,
    ):
        await coordinator._async_payload_debug_event(
            {"kind": "http", "path": "/v1/x", "payload": {"soc": 55}},
        )

    assert not writes, "dev mode alone must not run the raw-payload writer"


@pytest.mark.asyncio()
async def test_debug_logger_activates_payload_debug_capture(
    restore_payload_debug_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raising the dedicated logger to DEBUG activates the JSONL capture."""
    restore_payload_debug_logger.setLevel(logging.DEBUG)
    monkeypatch.delenv("JACKERY_DEV_MODE", raising=False)
    monkeypatch.setattr(util_module, "_DEV_MODE_CACHED", None)

    coordinator = _payload_debug_coordinator()
    writes: list[dict[str, Any]] = []

    def _capture(_path: str, event: dict[str, Any], _redactions: bool) -> None:
        writes.append(event)

    with patch(
        "custom_components.jackery_solarvault.coordinator.append_payload_debug_line",
        side_effect=_capture,
    ):
        await coordinator._async_payload_debug_event(
            {"kind": "http", "path": "/v1/x", "payload": {"soc": 55}},
        )

    assert writes, "a DEBUG payload_debug logger must run the raw-payload writer"


@pytest.mark.asyncio()
async def test_payload_debug_capture_off_without_dev_mode_or_debug_logger(
    restore_payload_debug_logger: logging.Logger,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With dev mode off and the logger below DEBUG the file stays unwritten."""
    restore_payload_debug_logger.setLevel(logging.WARNING)
    monkeypatch.delenv("JACKERY_DEV_MODE", raising=False)
    monkeypatch.setattr(util_module, "_DEV_MODE_CACHED", None)

    coordinator = _payload_debug_coordinator()
    writes: list[dict[str, Any]] = []

    def _capture(_path: str, event: dict[str, Any], _redactions: bool) -> None:
        writes.append(event)

    with patch(
        "custom_components.jackery_solarvault.coordinator.append_payload_debug_line",
        side_effect=_capture,
    ):
        await coordinator._async_payload_debug_event(
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


@pytest.mark.asyncio()
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
        await client._async_run_session()

    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.name == _LOCAL_MQTT_LOGGER
    ]
    assert warnings, "a failed local MQTT connection must log at WARNING"
    assert any("192.0.2.10:1883" in record.getMessage() for record in warnings)
