"""Entity-to-wire-field contract metadata for Jackery entities."""

from typing import Literal, Protocol

JackeryDataSource = Literal["REST", "MQTT", "BLE"]

DEFAULT_LIVE_SOURCES: tuple[JackeryDataSource, ...] = ("REST", "MQTT", "BLE")
DEFAULT_NULL_SEMANTICS = (
    "None/missing/unparseable means unknown; numeric 0 is valid when the "
    "Smali field is present; string 'unknown' is not recorded."
)
DEFAULT_RECORDER_ALLOWED = True


class JackeryContractDescription(Protocol):
    """Description metadata required by the entity contract tests."""

    key: str
    smali_field: str | None
    data_sources: tuple[JackeryDataSource, ...]
    null_semantics: str
    recorder_allowed: bool
    ha_derived: bool


def contract_field(description: JackeryContractDescription) -> str:
    """Return the Smali field or HA-derived calculation name for a description."""
    if description.smali_field:
        return description.smali_field
    if description.ha_derived:
        return f"ha:{description.key}"
    return description.key
