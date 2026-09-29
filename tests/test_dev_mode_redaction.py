"""``JACKERY_DEV_MODE`` turns every diagnostics/payload-debug redaction off.

docs/AGENTS.md §4.3 guards debug features with ``JACKERY_DEV_MODE`` and
docs/source-of-truth/diagnostics.md requires the switch to work again. Without
it, the mandatory credential redaction stays exactly as before.
"""

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.client.mqtt_push import JackeryMqttPushClient
from custom_components.jackery_solarvault.const import (
    CONF_ENABLE_UNREDACTED_DEBUG,
    PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX,
    PAYLOAD_DEBUG_LOG_MAX_BYTES,
    REDACTED_VALUE,
    REDACT_KEYS,
)
from custom_components.jackery_solarvault.coordinator import (
    _payload_debug_capture_enabled,  # ruff: ignore[import-private-name]
)
from custom_components.jackery_solarvault.util import (
    active_redact_keys,
    append_payload_debug_lines,
    jackery_dev_mode_enabled,
    redacted_json_safe_payload,
)

if TYPE_CHECKING:
    from pathlib import Path

_ENV = "JACKERY_DEV_MODE"
_TOPIC = "hb/app/1234567/device"
_SECRET_EVENT = {"kind": "http", "bluetoothKey": "k3y", "series": (1, 2)}


def _oversized_log(tmp_path: Path) -> Path:
    """Return a debug log already above the rotation threshold."""
    path = tmp_path / "jackery_solarvault_payload_debug.jsonl"
    path.write_text("x" * (PAYLOAD_DEBUG_LOG_MAX_BYTES + 1) + "\n", encoding="utf-8")
    return path


def test_dev_mode_is_off_without_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default operation keeps the mandatory redaction set."""
    monkeypatch.delenv(_ENV, raising=False)
    assert not jackery_dev_mode_enabled()
    assert active_redact_keys() == REDACT_KEYS
    redacted = redacted_json_safe_payload(_SECRET_EVENT)
    assert isinstance(redacted, dict)
    assert redacted["bluetoothKey"] == REDACTED_VALUE
    assert JackeryMqttPushClient._redact_topic(_TOPIC) != _TOPIC  # ruff: ignore[private-member-access]


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_dev_mode_disables_all_redaction(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Dev mode returns complete payloads, topics and credential fields."""
    monkeypatch.setenv(_ENV, value)
    assert jackery_dev_mode_enabled()
    assert active_redact_keys() == frozenset()
    assert redacted_json_safe_payload(_SECRET_EVENT) == {
        "kind": "http",
        "bluetoothKey": "k3y",
        "series": [1, 2],
    }
    assert JackeryMqttPushClient._redact_topic(_TOPIC) == _TOPIC  # ruff: ignore[private-member-access]
    client = JackeryApi(session=AsyncMock(), account="test", password="test")
    event = client._http_payload_debug(  # ruff: ignore[private-member-access]
        request=("POST", "/test"),
        body={"account": "owner@example.test"},
        response={"data": {"mqttPassWord": "credential"}},
    )
    assert event["request_body"]["account"] == "owner@example.test"
    assert event["response"]["data"]["mqttPassWord"] == "credential"


def test_dev_mode_payload_debug_log_is_unredacted_and_never_rotated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No captured payload is discarded by rotation while dev mode is on."""
    monkeypatch.setenv(_ENV, "1")
    path = _oversized_log(tmp_path)

    append_payload_debug_lines(path, [_SECRET_EVENT])

    backup = path.with_suffix(f"{PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX}{path.suffix}")
    assert not backup.exists()
    event = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["bluetoothKey"] == "k3y"


def test_payload_debug_log_still_rotates_outside_dev_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Normal operation keeps the bounded, redacted debug log."""
    monkeypatch.delenv(_ENV, raising=False)
    path = _oversized_log(tmp_path)

    append_payload_debug_lines(path, [_SECRET_EVENT])

    backup = path.with_suffix(f"{PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX}{path.suffix}")
    assert backup.exists()
    event = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["bluetoothKey"] == REDACTED_VALUE


def test_entry_debug_option_is_scoped_to_its_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An explicit entry option unlocks its own capture without a process-wide leak."""
    monkeypatch.delenv(_ENV, raising=False)
    enabled = SimpleNamespace(options={CONF_ENABLE_UNREDACTED_DEBUG: True}, data={})
    disabled = SimpleNamespace(options={}, data={})
    assert jackery_dev_mode_enabled(enabled)
    assert not jackery_dev_mode_enabled(disabled)
    assert active_redact_keys(enabled) == frozenset()
    assert active_redact_keys(disabled) == REDACT_KEYS
    assert _payload_debug_capture_enabled(enabled)
    assert JackeryMqttPushClient._redact_topic(_TOPIC, enabled) == _TOPIC  # ruff: ignore[private-member-access]
    assert JackeryMqttPushClient._redact_topic(_TOPIC, disabled) != _TOPIC  # ruff: ignore[private-member-access]

    client = JackeryApi(session=AsyncMock(), account="test", password="test")
    client.dev_mode_entry = enabled
    raw_event = client._http_payload_debug(  # ruff: ignore[private-member-access]
        request=("GET", "/test"), response={"mqttPassWord": "secret"}
    )
    assert raw_event["response"]["mqttPassWord"] == "secret"
    client.dev_mode_entry = disabled
    redacted_event = client._http_payload_debug(  # ruff: ignore[private-member-access]
        request=("GET", "/test"), response={"mqttPassWord": "secret"}
    )
    assert redacted_event["response"]["mqttPassWord"] == REDACTED_VALUE

    path = tmp_path / "payload.jsonl"
    append_payload_debug_lines(path, [_SECRET_EVENT], jackery_dev_mode_enabled(enabled))
    append_payload_debug_lines(
        path, [_SECRET_EVENT], jackery_dev_mode_enabled(disabled)
    )
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["bluetoothKey"] == "k3y"
    assert lines[1]["bluetoothKey"] == REDACTED_VALUE
