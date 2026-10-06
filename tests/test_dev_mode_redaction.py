"""``JACKERY_DEV_MODE`` turns every diagnostics/payload-debug redaction off.

docs/AGENTS.md §4.3 guards debug features with ``JACKERY_DEV_MODE`` and
docs/source-of-truth/diagnostics.md requires the switch to work again. Without
it, the mandatory credential redaction stays exactly as before.
"""

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.client.mqtt_push import JackeryMqttPushClient
from custom_components.jackery_solarvault.const import (
    CONF_ENABLE_UNREDACTED_DEBUG,
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
    assert JackeryMqttPushClient._redact_topic(_TOPIC) == _TOPIC  # ruff: ignore[private-member-access]


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


def test_dev_mode_payload_debug_log_restarts_without_creating_archives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Raw debugging remains bounded without timestamp/UUID archive files."""
    monkeypatch.setenv(_ENV, "1")
    path = _oversized_log(tmp_path)

    append_payload_debug_lines(path, [_SECRET_EVENT])

    segments = list(tmp_path.glob("jackery_solarvault_payload_debug.*.jsonl"))
    assert not segments
    assert path.stat().st_size <= PAYLOAD_DEBUG_LOG_MAX_BYTES
    event = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["bluetoothKey"] == "k3y"


@pytest.mark.parametrize("dev_mode", [False, True])
def test_http_debug_event_is_a_complete_receipt_time_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    dev_mode: bool,
) -> None:
    """Later merges must not rewrite the HTTP body captured as raw evidence."""
    monkeypatch.setenv(_ENV, "1" if dev_mode else "0")
    client = JackeryApi(session=AsyncMock(), account="test", password="test")
    series = [0.34, 0.72]
    response = {"data": {"unit": "kWh", "y1": series}}
    params = {"dateType": "week", "beginDate": "2026-09-28"}
    event = client._http_payload_debug(  # ruff: ignore[private-member-access]
        request=("GET", "/v1/device/stat/pv"),
        params=params,
        response=response,
    )
    series[1] = 120431.96
    params["beginDate"] = "2026-10-05"

    assert event["response"]["data"]["y1"] == [0.34, 0.72]
    assert event["params"]["beginDate"] == "2026-09-28"


def test_payload_debug_log_restarts_outside_dev_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Normal operation bounds the same file while retaining redaction."""
    monkeypatch.delenv(_ENV, raising=False)
    path = _oversized_log(tmp_path)

    append_payload_debug_lines(path, [_SECRET_EVENT])

    segments = list(tmp_path.glob("jackery_solarvault_payload_debug.*.jsonl"))
    assert not segments
    event = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["bluetoothKey"] == REDACTED_VALUE


def test_payload_debug_log_bounds_large_single_batch_without_archives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """One queued executor batch must not bypass the per-file size limit."""
    max_bytes = 120
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.util.PAYLOAD_DEBUG_LOG_MAX_BYTES",
        max_bytes,
    )
    path = tmp_path / "jackery_solarvault_payload_debug.jsonl"
    events = [_SECRET_EVENT] * 8

    append_payload_debug_lines(path, events, unredacted=True)

    segments = list(tmp_path.glob("jackery_solarvault_payload_debug*.jsonl"))
    assert segments == [path]
    assert all(segment.stat().st_size <= max_bytes for segment in segments)
    line_count = sum(
        len(segment.read_text(encoding="utf-8").splitlines()) for segment in segments
    )
    assert line_count > 0
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[-1]) == {
        "kind": "http",
        "bluetoothKey": "k3y",
        "series": [1, 2],
    }


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
    assert JackeryMqttPushClient._redact_topic(_TOPIC, cast("Any", enabled)) == _TOPIC  # ruff: ignore[private-member-access]
    assert JackeryMqttPushClient._redact_topic(_TOPIC, cast("Any", disabled)) == _TOPIC  # ruff: ignore[private-member-access]

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
