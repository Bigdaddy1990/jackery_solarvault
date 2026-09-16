"""Tests for BLE transport data structures, notification logging, and statistics."""

from datetime import UTC, datetime

from custom_components.jackery_solarvault.client.ble_transport import (
    BleFrameObservation,
    BleListenerStats,
    _should_log_ble_notification,
)


def test_should_log_ble_notification() -> None:
    """Test sparse logging interval rules for BLE notifications."""
    assert _should_log_ble_notification(1) is True
    assert _should_log_ble_notification(2) is False
    assert _should_log_ble_notification(256) is True
    assert _should_log_ble_notification(512) is True
    assert _should_log_ble_notification(300) is False


def test_ble_listener_stats_defaults() -> None:
    """Test BleListenerStats initialization and default counter values."""
    stats = BleListenerStats()
    assert stats.advertisements_seen == 0
    assert stats.connect_attempts == 0
    assert stats.frames_received == 0
    assert stats.frames_decoded == 0
    assert stats.last_error is None
    assert stats.unrouted_frames_by_cmd == {}


def test_ble_frame_observation() -> None:
    """Test BleFrameObservation creation."""
    now = datetime.now(UTC)
    obs = BleFrameObservation(
        received_at=now,
        raw_bytes=b"\x00\x01\x02",
        base64_encoded="AAEC",
        parsed=None,
        decode_error="CRC mismatch",
    )
    assert obs.received_at == now
    assert obs.raw_bytes == b"\x00\x01\x02"
    assert obs.base64_encoded == "AAEC"
    assert obs.parsed is None
    assert obs.decode_error == "CRC mismatch"
