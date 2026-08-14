"""Behavioral tests for the diagnostics null-normalizer and its coverage gate.

These exercise `_diagnostic_json_null_free` directly (the helper that keeps raw
JSON `null` leaves out of the diagnostics export) and lock in the
`diagnostics_null_export_violations` AST gate that guards against someone
removing the normalizer call from `async_get_config_entry_diagnostics`.
"""

import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from scripts.check_reference_coverage import diagnostics_null_export_violations

from custom_components.jackery_solarvault.const import (
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    REDACT_KEYS,
)
from custom_components.jackery_solarvault.diagnostics import (
    _diagnostic_json_null_free,  # test drives the module-private normalizer  # ruff: ignore[import-private-name]
)
from custom_components.jackery_solarvault.util import active_redact_keys
from homeassistant.components.diagnostics import REDACTED, async_redact_data

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _contains_raw_none(value: object) -> bool:
    """Return True if `value` contains a raw `None` anywhere in its tree."""
    if value is None:
        return True
    if isinstance(value, dict):
        return any(_contains_raw_none(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_raw_none(item) for item in value)
    return False


# ---------------------------------------------------------------------------
# _diagnostic_json_null_free
# ---------------------------------------------------------------------------


def test_null_free_maps_none_to_empty_string() -> None:
    """A bare `None` leaf becomes an empty string."""
    result = _diagnostic_json_null_free(None)

    assert isinstance(result, str)
    assert result == ""  # asserts the exact "" value, not truthiness


def test_null_free_recurses_into_nested_dicts() -> None:
    """None values nested inside dicts-of-dicts are normalized in place."""
    result = _diagnostic_json_null_free({
        "outer": {"inner": None, "kept": "value"},
    })

    assert result == {"outer": {"inner": "", "kept": "value"}}


def test_null_free_recurses_into_lists() -> None:
    """None entries inside a list are normalized element-by-element."""
    result = _diagnostic_json_null_free([1, None, "x"])

    assert result == [1, "", "x"]


def test_null_free_converts_tuples_to_normalized_lists() -> None:
    """Tuples are converted to lists and their None entries normalized."""
    result = _diagnostic_json_null_free((None, "kept"))

    assert result == ["", "kept"]
    assert isinstance(result, list)


def test_null_free_passes_through_non_none_scalars_unchanged() -> None:
    """int/str/bool/float scalars are returned unchanged, not stringified."""
    assert _diagnostic_json_null_free(42) == 42
    assert _diagnostic_json_null_free("text") == "text"
    assert _diagnostic_json_null_free(value=True) is True
    assert _diagnostic_json_null_free(math.pi) == math.pi


# ---------------------------------------------------------------------------
# regression: no raw None survives a realistic nested export shape
# ---------------------------------------------------------------------------


def test_null_free_regression_no_raw_none_survives_nested_export_shape() -> None:
    """A dict shaped like a real diagnostics export has no None leaves left."""
    export_like: dict[str, Any] = {
        "entry_data": {"host": None, "port": 1883},
        "options": {},
        "devices": {
            "device_1": {
                "properties": {"soc": None, "power": 100},
                "history": [None, {"ts": None, "value": 5}],
                "tags": (None, "ok"),
            },
        },
        "raw_api": {
            "login_response": None,
            "ota_responses": {"ota_1": {"version": None}},
        },
    }

    result = _diagnostic_json_null_free(export_like)

    assert _contains_raw_none(result) is False


# ---------------------------------------------------------------------------
# checker-fix lock: cast(...)-wrapped normalizer call must be accepted
# ---------------------------------------------------------------------------


def test_diagnostics_null_export_gate_accepts_cast_wrapped_normalizer() -> None:
    """The gate must accept the real cast(...)-wrapped normalizer call."""
    assert diagnostics_null_export_violations(root=_REPO_ROOT) == ()


# ---------------------------------------------------------------------------
# F-SW2-1: local/third-party MQTT broker credentials must be redacted
# ---------------------------------------------------------------------------


def test_local_and_third_party_mqtt_credentials_redacted_from_options() -> None:
    """Local- and third-party-MQTT broker credentials never leave the export.

    `async_get_config_entry_diagnostics` redacts `entry.options` with
    `async_redact_data(dict(entry.options), active_redact_keys(entry))`
    (diagnostics.py). Every credential-bearing MQTT broker option key —
    both the canonical local-broker keys and their legacy
    ``third_party_mqtt_*`` aliases, which are also real persisted option
    keys for the separate third-party-broker feature — must be listed in
    `REDACT_KEYS`, or a diagnostics export leaks broker passwords/tokens
    in cleartext.
    """
    entry = SimpleNamespace(
        options={
            CONF_LOCAL_MQTT_HOST: "192.168.1.50",
            CONF_LOCAL_MQTT_USERNAME: "mqtt-user",
            CONF_LOCAL_MQTT_PASSWORD: "super-secret",
            CONF_THIRD_PARTY_MQTT_IP: "192.168.1.60",
            CONF_THIRD_PARTY_MQTT_USERNAME: "third-party-user",
            CONF_THIRD_PARTY_MQTT_PASSWORD: "third-party-secret",
            CONF_THIRD_PARTY_MQTT_TOKEN: "third-party-token",
        },
    )

    redacted = async_redact_data(dict(entry.options), active_redact_keys())

    assert redacted[CONF_LOCAL_MQTT_HOST] == REDACTED
    assert redacted[CONF_LOCAL_MQTT_USERNAME] == REDACTED
    assert redacted[CONF_LOCAL_MQTT_PASSWORD] == REDACTED
    assert redacted[CONF_THIRD_PARTY_MQTT_IP] == REDACTED
    assert redacted[CONF_THIRD_PARTY_MQTT_USERNAME] == REDACTED
    assert redacted[CONF_THIRD_PARTY_MQTT_PASSWORD] == REDACTED
    assert redacted[CONF_THIRD_PARTY_MQTT_TOKEN] == REDACTED


def test_redaction_key_contract_is_immutable_and_mandatory() -> None:
    """The share-safe redaction key set cannot be cleared per config entry."""
    assert isinstance(REDACT_KEYS, frozenset)
    assert active_redact_keys() == REDACT_KEYS
    assert REDACT_KEYS
