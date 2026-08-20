"""Unit tests for services coverage gaps."""

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock

import pytest
import voluptuous as vol

from custom_components.jackery_solarvault import services
from custom_components.jackery_solarvault.const import DOMAIN, SERVICE_FIELD_DEVICE_ID
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.exceptions import ServiceValidationError

from .test_services import (  # ruff: ignore[banned-api]
    _Call,
    _Coordinator,
    _Device,
    _Registry,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall


def _translation_error(error: ServiceValidationError) -> str:
    """Return the required error placeholder from a validation error."""
    placeholders = error.translation_placeholders
    assert placeholders is not None
    return placeholders["error"]


def test_loaded_coordinators_finds_valid_coordinator(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _loaded_coordinators yields a valid coordinator from runtime_data."""
    mock_entry = Mock(spec=ConfigEntry)
    mock_entry.domain = DOMAIN
    mock_entry.state = ConfigEntryState.LOADED

    coordinator = Mock(spec=services.JackerySolarVaultCoordinator)
    mock_entry.runtime_data = coordinator

    monkeypatch.setattr(
        hass.config_entries,
        "async_loaded_entries",
        Mock(return_value=[mock_entry]),
    )
    assert services._loaded_coordinators(hass) == [coordinator]


def test_payload_has_home_evidence_with_props() -> None:
    """Test _payload_has_home_payload_evidence with explicit props."""
    props = {"maxOutPw": 1}
    assert services._payload_has_home_payload_evidence({}, props) is True


def test_service_validation_error_with_extra_placeholders() -> None:
    """Test _service_validation_error with extra placeholders."""
    err = services._service_validation_error(
        "key", device_id="dev1", error="err1", extra_placeholders={"extra": "val"}
    )
    assert err.translation_placeholders == {
        "device_id": "dev1",
        "error": "err1",
        "extra": "val",
    }


def test_rename_name_from_service_rejects_non_string() -> None:
    """Test _rename_name_from_service rejects non-string types."""
    with pytest.raises(ServiceValidationError) as exc:
        services._rename_name_from_service(123, "sys1")
    assert "must be text" in _translation_error(exc.value)


def test_storm_alert_id_from_service_rejects_non_string() -> None:
    """Test _storm_alert_id_from_service rejects non-string types."""
    with pytest.raises(ServiceValidationError) as exc:
        services._storm_alert_id_from_service(123, "dev1")
    assert "must be text" in _translation_error(exc.value)


def test_json_native_body_rejects_non_dict_normalization() -> None:
    """Test _json_native_body rejects a string body after normalization."""
    with pytest.raises(ServiceValidationError) as exc:
        services._json_native_body(cast("dict[Any, Any]", [1, 2, 3]), "dev1")
    assert "Expected dict body" in _translation_error(exc.value)


def test_ble_body_from_service_rejects_invalid_types() -> None:
    """Test _ble_body_from_service rejects strings that are lists and non-string/non-dicts."""
    with pytest.raises(ServiceValidationError) as exc:
        services._ble_body_from_service("[1, 2, 3]", "dev1")
    assert "must be an object" in _translation_error(exc.value)

    with pytest.raises(ServiceValidationError) as exc2:
        services._ble_body_from_service(123, "dev1")
    assert "must be a mapping or JSON" in _translation_error(exc2.value)


def test_ble_body_from_service_accepts_valid_string() -> None:
    """Test _ble_body_from_service accepts a valid JSON string dict."""
    assert services._ble_body_from_service('{"a": 1}', "dev1") == {"a": 1}


def test_service_required_text_rejects_invalid() -> None:
    """Test _service_required_text validations."""
    with pytest.raises(ServiceValidationError) as exc:
        services._service_required_text(
            123, field_name="f", translation_key="k", device_id="d", max_length=10
        )
    assert "must be text" in _translation_error(exc.value)

    with pytest.raises(ServiceValidationError) as exc2:
        services._service_required_text(
            "a" * 11, field_name="f", translation_key="k", device_id="d", max_length=10
        )
    assert "must be at most 10" in _translation_error(exc2.value)


def test_service_float_rejects_invalid() -> None:
    """Test _service_float validation limits."""
    with pytest.raises(ServiceValidationError) as exc:
        services._service_float(
            float("inf"),
            field_name="f",
            translation_key="k",
            device_id="d",
            min_value=0,
            max_value=100,
        )
    assert "must be a number" in _translation_error(exc.value)

    with pytest.raises(ServiceValidationError) as exc2:
        services._service_float(
            -1.0,
            field_name="f",
            translation_key="k",
            device_id="d",
            min_value=0,
            max_value=100,
        )
    assert "must be between 0 and 100" in _translation_error(exc2.value)

    with pytest.raises(ServiceValidationError) as exc3:
        services._service_float(
            101.0,
            field_name="f",
            translation_key="k",
            device_id="d",
            min_value=0,
            max_value=100,
        )
    assert "must be between 0 and 100" in _translation_error(exc3.value)


async def test_async_handle_get_share_qr_code_success(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test successful QR code share retrieval."""
    mock_call = cast(
        "ServiceCall",
        _Call(
            data={
                SERVICE_FIELD_DEVICE_ID: "jackery_dev1",
                "qr_code_id": "q1",
                "user_id": "u1",
            }
        ),
    )

    registry = _Registry({"jackery_dev1": _Device({(DOMAIN, "d1")})})
    monkeypatch.setattr(
        "homeassistant.helpers.device_registry.async_get", lambda h: registry
    )

    from unittest.mock import AsyncMock

    coordinator = _Coordinator(True)
    cast(Any, coordinator).async_get_share_qr_code = AsyncMock(return_value={})

    def mock_coordinator_for_device(h: HomeAssistant, d: str) -> _Coordinator:
        return coordinator

    monkeypatch.setattr(
        "custom_components.jackery_solarvault.services._coordinator_for_device",
        mock_coordinator_for_device,
    )

    monkeypatch.setattr(
        "custom_components.jackery_solarvault.services._notify_share_qr_code", Mock()
    )

    res = await services._async_handle_get_share_qr_code(hass, mock_call)
    assert res == {"qr_code_id": None, "user_id": None}


def test_service_integer_parser_rejects_bools() -> None:
    """Test integer parser rejects bools."""
    with pytest.raises(vol.Invalid, match="expected integer"):
        services._coerce_service_int(True)
    with pytest.raises(vol.Invalid, match="expected integer"):
        services._coerce_service_int(False)


def test_service_integer_parser_accepts_whole_floats() -> None:
    """Test integer parser accepts whole floats."""
    assert services._coerce_service_int(42.0) == 42
    with pytest.raises(vol.Invalid, match="expected integer"):
        services._coerce_service_int(42.5)


def test_service_integer_parser_rejects_empty_string() -> None:
    """Test integer parser rejects empty strings."""
    with pytest.raises(vol.Invalid, match="expected integer"):
        services._coerce_service_int("   ")


def test_service_integer_parser_rejects_unrecognized_type() -> None:
    """Test integer parser rejects unrecognized types."""
    with pytest.raises(vol.Invalid, match="expected integer"):
        services._coerce_service_int(object())
