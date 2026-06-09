"""Local-MQTT diagnostics classification tests."""

from typing import Any

from custom_components.jackery_solarvault.const import (
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
)
from custom_components.jackery_solarvault.diagnostics import (
    _local_mqtt_diagnostics,  # noqa: PLC2701
)


class _FakeHass:
    """Minimal hass stub for diagnostics tests."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}


class _FakeEntry:
    """Config-entry-like stub with options/data maps."""

    def __init__(self, entry_id: str, options: dict[str, Any]) -> None:
        self.entry_id = entry_id
        self.options = options
        self.data: dict[str, Any] = {}


def test_local_mqtt_diagnostics_disabled_when_bridge_off() -> None:
    """Local MQTT diagnostics should indicate disabled bridge by reason code."""
    hass = _FakeHass()
    entry = _FakeEntry("entry_1", options={CONF_LOCAL_MQTT_ENABLE: False})

    result = _local_mqtt_diagnostics(hass, entry, redactions_disabled=False)

    assert result["enabled"] is False
    assert result["disabled_reason"] == "bridge_disabled"


def test_local_mqtt_diagnostics_blocks_broad_topic_filter() -> None:
    """Broad wildcard filters must be reported as blocked for CPU safety."""
    hass = _FakeHass()
    entry = _FakeEntry(
        "entry_2",
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "192.168.1.100",
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "#",
        },
    )

    result = _local_mqtt_diagnostics(hass, entry, redactions_disabled=False)

    assert result["enabled"] is False
    assert result["disabled_reason"] == "broad_topic_filter_blocked"


def test_local_mqtt_diagnostics_requires_topic_filter() -> None:
    """Empty topic filters should keep the local listener disabled."""
    hass = _FakeHass()
    entry = _FakeEntry(
        "entry_3",
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "192.168.1.100",
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "",
        },
    )

    result = _local_mqtt_diagnostics(hass, entry, redactions_disabled=False)

    assert result["enabled"] is False
    assert result["disabled_reason"] == "missing_topic_filter"
