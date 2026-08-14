"""Final behavioural branch coverage for number, text, and ingest models."""

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import number as number_mod, text as text_mod
from custom_components.jackery_solarvault.const import (
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    FIELD_DEVICE_NAME,
    FIELD_GRID_STANDARD,
    FIELD_THIRD_PARTY_MQTT_IP,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
)
from custom_components.jackery_solarvault.models import (
    DataSource,
    IngestResult,
    Observation,
)
from custom_components.jackery_solarvault.number import (
    JackeryNumber,
    JackeryNumberDescription,
    _is_portable_payload as number_is_portable_payload,  # ruff: ignore[import-private-name]
    _payload_has_home_payload_evidence as number_has_home_evidence,  # ruff: ignore[import-private-name]
    _set_max_feed_grid,  # ruff: ignore[import-private-name]
    _wire_float,  # ruff: ignore[import-private-name]
    _wire_int,  # ruff: ignore[import-private-name]
)
from custom_components.jackery_solarvault.text import (
    JackeryDeviceNameText,
    JackeryGridStandardText,
    JackerySystemNameText,
    JackeryThirdPartyMqttText,
)
from homeassistant.components.text import TextMode
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

_DEVICE_ID = "device-coverage"


def _coordinator(data: dict[str, Any]) -> MagicMock:
    """Return the minimal coordinator boundary used by the real entities."""
    coordinator = MagicMock(name="coordinator")
    coordinator.data = data
    coordinator.last_update_success = True
    coordinator.is_device_locally_reachable = MagicMock(return_value=False)
    coordinator.is_device_reachable = MagicMock(return_value=True)
    coordinator.third_party_mqtt_config_plaintext = MagicMock(return_value={})
    coordinator.async_set_max_feed_grid = AsyncMock(return_value=None)
    coordinator.async_set_device_name = AsyncMock(return_value=None)
    coordinator.async_set_device_nickname = AsyncMock(return_value=None)
    coordinator.async_set_system_name = AsyncMock(return_value=None)
    coordinator.async_sync_grid_standard = AsyncMock(return_value=None)
    coordinator.async_request_refresh = AsyncMock(return_value=None)
    coordinator.async_update_third_party_mqtt_config = AsyncMock(return_value=None)
    return coordinator


def _number(
    description: JackeryNumberDescription,
    *,
    properties: dict[str, Any] | None = None,
) -> JackeryNumber:
    """Build the real number entity without attaching it to HA."""
    entity = JackeryNumber.__new__(JackeryNumber)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator({
        _DEVICE_ID: {PAYLOAD_PROPERTIES: properties or {}},
    })
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = description
    return entity


def _text_entity(entity_type: type[Any], data: dict[str, Any]) -> Any:
    """Build a real text entity with a mocked transport boundary."""
    entity = object.__new__(entity_type)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator(data)
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.async_write_ha_state = MagicMock()
    return entity


def test_observation_rejects_naive_timestamp_and_accepts_aware_timestamp() -> None:
    """Transport observations must never carry ambiguous wall-clock time."""
    with pytest.raises(ValueError, match="timezone-aware"):
        Observation(
            source=DataSource.HTTP,
            device_id=_DEVICE_ID,
            section="properties",
            payload={},
            observed_at=datetime(2026, 8, 14, 12),
        )

    observation = Observation(
        source=DataSource.BLE,
        device_id=_DEVICE_ID,
        section="properties",
        payload={},
        observed_at=datetime(2026, 8, 14, 12, tzinfo=UTC),
    )

    assert observation.observed_at is not None
    assert observation.observed_at.utcoffset() is not None


def test_ingest_result_accepted_reflects_empty_and_nonempty_field_sets() -> None:
    """Acceptance is false for a fully rejected frame and true for one field."""
    assert IngestResult({}, {}, frozenset()).accepted is False
    assert IngestResult({"soc": 50}, {}, frozenset({"soc"})).accepted is True


@pytest.mark.parametrize(
    ["payload", "props", "expected"],
    [
        [{PAYLOAD_SYSTEM: []}, None, False],
        [{PAYLOAD_SYSTEM: {"id": "system"}}, None, True],
        [{"http_properties": {"batSoc": 50}}, None, True],
        [{PAYLOAD_PROPERTIES: "invalid"}, None, False],
        [{}, {"maxOutPw": 2500}, True],
    ],
)
def test_number_home_evidence_handles_all_payload_shapes(
    payload: dict[str, Any],
    props: dict[str, Any] | None,
    expected: bool,
) -> None:
    """Home evidence is detected without treating malformed sections as Home."""
    assert number_has_home_evidence(payload, props) is expected


def test_number_portable_detection_checks_device_and_rejects_home_evidence() -> None:
    """A legacy device is portable unless a Home-only property is present."""
    portable = {
        PAYLOAD_DEVICE: {
            PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
        },
    }
    assert number_is_portable_payload(portable) is True
    assert number_is_portable_payload(portable, {"batSoc": 20}) is False
    assert number_is_portable_payload({PAYLOAD_DEVICE: "invalid"}) is False


def test_number_description_resolves_smali_and_explicit_capabilities() -> None:
    """Description post-init preserves explicit sources and derives missing fields."""
    explicit_sources = ("explicit",)
    description = JackeryNumberDescription(
        key="smali",
        smali_field="wireField",
        data_sources=explicit_sources,
        command_sources=explicit_sources,
        setter=AsyncMock(),
    )
    third_party = JackeryNumberDescription(
        key="third_party",
        source_keys=("port",),
        source_section=PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
    )
    http_only = JackeryNumberDescription(
        key="http",
        source_keys=("price",),
        source_section="price",
    )

    assert description.app_fields == ("wireField",)
    assert description.data_sources == explicit_sources
    assert description.command_sources == explicit_sources
    assert third_party.data_sources == number_mod.LAYER5_DATA_SOURCES
    assert http_only.data_sources == number_mod.HTTP_DATA_SOURCES


@pytest.mark.parametrize("parser", [_wire_int, _wire_float])
def test_number_wire_parsers_reject_non_numeric_input(parser: Any) -> None:
    """Wire values fail closed instead of silently coercing invalid input."""
    with pytest.raises(HomeAssistantError, match="invalid number value"):
        parser("not-a-number")


async def test_max_feed_grid_low_capability_writes_800_watts() -> None:
    """The low branch maps every supported low request to the 800 W command."""
    coordinator = _coordinator({})

    await _set_max_feed_grid(coordinator, _DEVICE_ID, 800)

    coordinator.async_set_max_feed_grid.assert_awaited_once_with(_DEVICE_ID, 800)


def test_number_static_fallbacks_and_non_numeric_native_value() -> None:
    """Static description metadata and malformed telemetry produce safe values."""
    description = JackeryNumberDescription(
        key="static",
        source_keys=("value",),
        native_min_value=0,
        native_max_value=42,
        native_unit_of_measurement="widgets",
        allowed_values=(1.0, 2.0),
        display_precision=2,
    )
    entity = _number(description, properties={"value": "bad"})

    assert entity.native_value is None
    assert entity.native_max_value == pytest.approx(42)
    assert entity.native_unit_of_measurement == "widgets"
    assert entity.suggested_display_precision == 2
    assert entity._allowed_values() == (1.0, 2.0)  # ruff: ignore[private-member-access]


def test_number_empty_description_has_safe_defaults() -> None:
    """Descriptions without limits or discrete values expose empty defaults."""
    entity = _number(JackeryNumberDescription(key="empty"))

    assert entity.native_max_value == pytest.approx(0)
    assert entity._allowed_values() == ()  # ruff: ignore[private-member-access]


async def test_number_without_setter_validates_then_performs_no_write() -> None:
    """A read-only description returns cleanly after range validation."""
    entity = _number(
        JackeryNumberDescription(
            key="readonly",
            native_min_value=0,
            native_max_value=10,
        ),
    )

    await entity.async_set_native_value(5)


@pytest.mark.parametrize(
    ["error", "raises"],
    [
        [ConfigEntryAuthFailed("reauth"), ConfigEntryAuthFailed],
        [
            HomeAssistantError(
                translation_domain="jackery_solarvault",
                translation_key="already_translated",
                translation_placeholders={},
            ),
            HomeAssistantError,
        ],
    ],
)
async def test_number_preserves_structured_setter_errors(
    error: Exception,
    raises: type[Exception],
) -> None:
    """Structured HA errors keep their original semantics."""
    setter = AsyncMock(side_effect=error)
    entity = _number(
        JackeryNumberDescription(
            key="structured",
            native_min_value=0,
            native_max_value=10,
            setter=setter,
        ),
    )

    with pytest.raises(raises) as caught:
        await entity.async_set_native_value(5)

    if isinstance(error, HomeAssistantError):
        assert caught.value is error


async def test_optional_number_setter_ignores_transport_failure() -> None:
    """An explicitly optional write logs a transport failure without masking HA."""
    setter = AsyncMock(side_effect=TimeoutError("offline"))
    entity = _number(
        JackeryNumberDescription(
            key="optional",
            native_min_value=0,
            native_max_value=10,
            setter=setter,
            raise_on_setter_error=False,
        ),
    )

    await entity.async_set_native_value(5)

    setter.assert_awaited_once()


def test_device_and_system_name_native_fallbacks() -> None:
    """Name readers traverse all documented fallbacks and return None when absent."""
    device = _text_entity(
        JackeryDeviceNameText,
        {_DEVICE_ID: {PAYLOAD_SYSTEM: {FIELD_DEVICE_NAME: "System fallback"}}},
    )
    missing = _text_entity(JackeryDeviceNameText, {_DEVICE_ID: {}})
    system = _text_entity(
        JackerySystemNameText,
        {_DEVICE_ID: {PAYLOAD_SYSTEM: {FIELD_DEVICE_NAME: "Product fallback"}}},
    )

    assert device.native_value == "System fallback"
    assert missing.native_value is None
    assert system.native_value == "Product fallback"


@pytest.mark.parametrize(
    ["error", "expected_key"],
    [
        [text_mod.JackeryAuthError("denied"), None],
        [text_mod.JackeryError("offline"), "set_device_nickname_failed"],
    ],
)
async def test_device_name_maps_transport_errors(
    error: Exception,
    expected_key: str | None,
) -> None:
    """Device rename maps auth to reauth and API errors to translated failures."""
    entity = _text_entity(JackeryDeviceNameText, {_DEVICE_ID: {}})
    entity.coordinator.async_set_device_name = AsyncMock(side_effect=error)

    expected = ConfigEntryAuthFailed if expected_key is None else HomeAssistantError
    with pytest.raises(expected) as caught:
        await entity.async_set_value("New")

    if expected_key is not None:
        assert caught.value.translation_key == expected_key


async def test_device_name_rejects_blank_value() -> None:
    """A blank device label never reaches an HTTP endpoint."""
    entity = _text_entity(JackeryDeviceNameText, {_DEVICE_ID: {}})

    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_set_value("  ")

    assert caught.value.translation_key == "invalid_text_value"


async def test_system_name_maps_api_failure() -> None:
    """A failed system rename retains its system id in a translated error."""
    entity = _text_entity(
        JackerySystemNameText,
        {_DEVICE_ID: {PAYLOAD_SYSTEM: {"id": "system-1"}}},
    )
    entity.coordinator.async_set_system_name = AsyncMock(
        side_effect=text_mod.JackeryError("offline"),
    )

    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_set_value("New")

    assert caught.value.translation_key == "rename_system_failed"
    assert caught.value.translation_placeholders == {
        "system_id": "system-1",
        "error": "offline",
    }


def _grid_standard(data: dict[str, Any]) -> JackeryGridStandardText:
    return cast("JackeryGridStandardText", _text_entity(JackeryGridStandardText, data))


def test_grid_standard_native_value_handles_absent_empty_and_numeric_values() -> None:
    """The grid code exposes strings while empty wire values remain unavailable."""
    assert _grid_standard({_DEVICE_ID: {PAYLOAD_SYSTEM: {}}}).native_value is None
    assert (
        _grid_standard(
            {_DEVICE_ID: {PAYLOAD_SYSTEM: {FIELD_GRID_STANDARD: ""}}},
        ).native_value
        is None
    )
    assert (
        _grid_standard(
            {_DEVICE_ID: {PAYLOAD_SYSTEM: {FIELD_GRID_STANDARD: 103}}},
        ).native_value
        == "103"
    )


async def test_grid_standard_success_writes_integer_and_refreshes() -> None:
    """A decimal grid code is converted to int and followed by a refresh."""
    entity = _grid_standard({_DEVICE_ID: {PAYLOAD_SYSTEM: {}}})

    await entity.async_set_value(" 103 ")

    entity.coordinator.async_sync_grid_standard.assert_awaited_once_with(
        _DEVICE_ID,
        103,
    )
    entity.coordinator.async_request_refresh.assert_awaited_once_with()


@pytest.mark.parametrize(
    ["error", "expected"],
    [
        [text_mod.JackeryAuthError("denied"), ConfigEntryAuthFailed],
        [HomeAssistantError("specific"), HomeAssistantError],
        [text_mod.JackeryError("offline"), HomeAssistantError],
    ],
)
async def test_grid_standard_maps_write_errors(
    error: Exception,
    expected: type[Exception],
) -> None:
    """Grid-standard errors retain reauth and HA action semantics."""
    entity = _grid_standard({_DEVICE_ID: {PAYLOAD_SYSTEM: {}}})
    entity.coordinator.async_sync_grid_standard = AsyncMock(side_effect=error)

    with pytest.raises(expected) as caught:
        await entity.async_set_value("103")

    if isinstance(error, HomeAssistantError):
        assert caught.value is error
    elif isinstance(error, text_mod.JackeryError) and not isinstance(
        error,
        text_mod.JackeryAuthError,
    ):
        assert (
            cast("HomeAssistantError", caught.value).translation_key
            == "entity_action_failed"
        )


async def test_grid_standard_rejects_non_decimal_value() -> None:
    """Only the app's decimal grid-standard format is accepted."""
    entity = _grid_standard({_DEVICE_ID: {PAYLOAD_SYSTEM: {}}})

    with pytest.raises(HomeAssistantError) as caught:
        await entity.async_set_value("10.3")

    assert caught.value.translation_key == "invalid_text_value"


def _third_party() -> JackeryThirdPartyMqttText:
    entity = JackeryThirdPartyMqttText.__new__(JackeryThirdPartyMqttText)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator({_DEVICE_ID: {}})
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable._field = FIELD_THIRD_PARTY_MQTT_IP  # ruff: ignore[private-member-access]
    mutable._attr_translation_key = "third_party_mqtt_ip"  # ruff: ignore[private-member-access]
    mutable._attr_mode = TextMode.TEXT  # ruff: ignore[private-member-access]
    return entity


def test_third_party_native_value_none_and_constructor_pattern_branch() -> None:
    """Missing plaintext remains unavailable and optional patterns are installed."""
    entity = _third_party()
    constructed = JackeryThirdPartyMqttText(
        entity.coordinator,
        _DEVICE_ID,
        key_suffix="token",
        translation_key="third_party_mqtt_token",
        field="token",
        mode=TextMode.TEXT,
        pattern=r"^\d+$",
    )

    assert entity.native_value is None
    assert constructed.pattern == r"^\d+$"


@pytest.mark.parametrize(
    ["error", "expected"],
    [
        [text_mod.JackeryAuthError("denied"), ConfigEntryAuthFailed],
        [ConfigEntryAuthFailed("reauth"), ConfigEntryAuthFailed],
        [
            HomeAssistantError(
                translation_domain="jackery_solarvault",
                translation_key="specific",
                translation_placeholders={},
            ),
            HomeAssistantError,
        ],
        [HomeAssistantError("offline"), HomeAssistantError],
        [TimeoutError("offline"), HomeAssistantError],
    ],
)
async def test_third_party_maps_all_write_error_families(
    error: Exception,
    expected: type[Exception],
) -> None:
    """Third-party MQTT text preserves reauth and translates generic failures."""
    entity = _third_party()
    entity.coordinator.async_update_third_party_mqtt_config = AsyncMock(
        side_effect=error,
    )

    with pytest.raises(expected) as caught:
        await entity.async_set_value(" broker ")

    if isinstance(error, ConfigEntryAuthFailed) or (
        isinstance(error, HomeAssistantError) and error.translation_key
    ):
        assert caught.value is error
    elif not isinstance(error, text_mod.JackeryAuthError):
        assert (
            cast("HomeAssistantError", caught.value).translation_key
            == "entity_action_failed"
        )
