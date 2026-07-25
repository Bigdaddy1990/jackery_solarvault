"""Tests for MQTT push logging filters and helper utilities."""

import logging

from custom_components.jackery_solarvault.client.mqtt_push import (
    _AioMqttPassiveDisconnectFilter,
    _AioMqttTeardownNoiseFilter,
)


def test_aiomqtt_passive_disconnect_filter() -> None:
    """Test filtering of expected socket reset error messages."""
    filter_inst = _AioMqttPassiveDisconnectFilter()

    # Normal messages pass
    rec1 = logging.LogRecord("aiomqtt", logging.INFO, "", 0, "Normal message", (), None)
    assert filter_inst.filter(rec1) is True

    # Socket reset messages are suppressed
    rec2 = logging.LogRecord(
        "aiomqtt",
        logging.ERROR,
        "",
        0,
        "failed to receive on socket: Connection reset by peer",
        (),
        None,
    )
    assert filter_inst.filter(rec2) is False

    rec3 = logging.LogRecord(
        "aiomqtt",
        logging.ERROR,
        "",
        0,
        "failed to receive on socket: Errno 104",
        (),
        None,
    )
    assert filter_inst.filter(rec3) is False


def test_aiomqtt_teardown_noise_filter() -> None:
    """Test filtering of late ACK and teardown race messages."""
    filter_inst = _AioMqttTeardownNoiseFilter()

    # Normal error passes
    rec1 = logging.LogRecord(
        "aiomqtt", logging.ERROR, "", 0, "Unexpected error in callback", (), None
    )
    assert filter_inst.filter(rec1) is True

    # Late ack template message is demoted/filtered
    rec2 = logging.LogRecord(
        "aiomqtt",
        logging.ERROR,
        "",
        0,
        'Unexpected message ID "%d" in on_subscribe callback',
        (),
        None,
    )
    assert filter_inst.filter(rec2) is False

    # Teardown message prefix is filtered
    rec3 = logging.LogRecord(
        "aiomqtt",
        logging.ERROR,
        "",
        0,
        "Caught exception in on_disconnect during cleanup",
        (),
        None,
    )
    assert filter_inst.filter(rec3) is False
