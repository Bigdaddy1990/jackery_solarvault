"""Regression tests for bounded, non-secret and non-flooding HA logs."""

import inspect
import logging
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import coordinator as coordinator_module
from custom_components.jackery_solarvault.client import (
    api as api_module,
    ble_transport as ble_transport_module,
)
from custom_components.jackery_solarvault.client.api import JackeryApiError
from custom_components.jackery_solarvault.client.ble_transport import JackeryBleListener
from custom_components.jackery_solarvault.client.mqtt_push import JackeryMqttPushClient
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)


def test_http_debug_body_is_bounded_shape_only() -> None:
    """The HA log must not contain response secrets or full 288-point curves."""
    secret = "do-not-leak-this-password"
    body = {
        "password": secret,
        "email": "private@example.invalid",
        "y": list(range(288)),
        "nested": {"token": "also-secret"},
    }

    rendered = api_module._log_body(body)

    assert secret not in rendered
    assert "private@example.invalid" not in rendered
    assert "also-secret" not in rendered
    assert "list[288]" in rendered
    assert len(rendered) < 512


def test_ble_notification_debug_sampling_is_bounded() -> None:
    """Busy BLE notifications log the first frame and sparse progress samples."""
    should_log = ble_transport_module._should_log_ble_notification

    assert should_log(1) is True
    assert should_log(2) is False
    assert should_log(255) is False
    assert should_log(256) is True


def test_transport_debug_log_calls_do_not_render_payload_bodies() -> None:
    """Cloud MQTT and BLE handlers log bounded shape metadata, not wire bodies."""
    mqtt_source = inspect.getsource(JackeryMqttPushClient._handle_message)
    ble_source = inspect.getsource(JackeryBleListener._handle_notification)

    assert "len(text),\n            text," not in mqtt_source
    assert "parsed.body," not in ble_source
    assert "body_keys" in mqtt_source
    assert "body_bytes" in ble_source


def test_transient_slow_endpoint_timeout_uses_debug_level() -> None:
    """Cached background timeouts do not emit one WARNING per endpoint."""
    error = JackeryApiError("GET /v1/device/stat/pv request failed")
    error.__cause__ = TimeoutError()

    assert (
        coordinator_module._slow_fetch_failure_log_level(
            error,
            suppressed=False,
        )
        == logging.DEBUG
    )


def test_non_timeout_slow_endpoint_failure_remains_warning() -> None:
    """A new non-transient endpoint failure stays visible to the operator."""
    error = JackeryApiError("GET /v1/device/stat/pv code=10422")

    assert (
        coordinator_module._slow_fetch_failure_log_level(
            error,
            suppressed=False,
        )
        == logging.WARNING
    )


@pytest.mark.asyncio()
async def test_cmd113_log_does_not_expose_broker_or_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The device-config INFO line contains status, never private connection data."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    obj = cast("Any", coordinator)
    obj._generated_third_party_mqtt_token = None
    obj.device_bluetooth_key = MagicMock(return_value=b"0123456789abcdef")
    obj._async_publish_command_ble_first = AsyncMock()
    obj._apply_local_third_party_mqtt_config_patch = MagicMock()

    with caplog.at_level(
        logging.INFO,
        logger="custom_components.jackery_solarvault.coordinator",
    ):
        await coordinator.async_set_third_party_mqtt_config(
            "device-1",
            enable=True,
            ip="192.168.2.212",
            port=1883,
            username="private-user",
            password="private-password",
            token="123456789",
        )

    rendered = "\n".join(record.getMessage() for record in caplog.records)
    assert "192.168.2.212" not in rendered
    assert "private-user" not in rendered
    assert "private-password" not in rendered
    assert "123456789" not in rendered
