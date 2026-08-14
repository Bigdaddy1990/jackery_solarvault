"""Regression tests for logging/diagnostics visibility.

Two logging rules are covered:

1. Redacted payload JSONL capture is opt-in. The config-entry option or the
   effective DEBUG level of the dedicated ``payload_debug`` logger activates
   it. ``JACKERY_DEV_MODE=1`` alone must not.
2. Local MQTT connection failures must be visible at WARNING in the default
   Home Assistant log, not swallowed at DEBUG.
"""

import asyncio
from collections import deque
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

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
    assert parent is not None
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
    parent = restore_payload_debug_logger.parent
    assert parent is not None
    parent.setLevel(logging.DEBUG)
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


@pytest.mark.asyncio
async def test_local_mqtt_unavailable_ha_client_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unavailable shared HA MQTT client must produce a visible warning."""
    hass = MagicMock()
    hass.async_create_background_task.side_effect = lambda coroutine, **_kwargs: (
        asyncio.create_task(coroutine)
    )
    client = JackeryLocalMqttClient(
        hass,
        sink=None,
        topic_filter="hb/app/#",
    )

    with (
        patch.object(
            local_mqtt_module.mqtt,
            "async_wait_for_mqtt_client",
            new=AsyncMock(return_value=False),
        ),
        caplog.at_level(logging.WARNING, logger=_LOCAL_MQTT_LOGGER),
    ):
        await client.async_start()
        await client.async_stop()

    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and record.name == _LOCAL_MQTT_LOGGER
    ]
    assert warnings, "an unavailable HA MQTT integration must log at WARNING"
    assert any("hb/app/#" in record.getMessage() for record in warnings)
    assert any(
        "Home Assistant MQTT integration" in record.getMessage() for record in warnings
    )
