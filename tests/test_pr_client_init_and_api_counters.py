"""Tests for changes in client/__init__.py and JackeryApi.__init__ transport counters.

Covers:
- client/__init__.py __getattr__: uses import_module instead of direct import
  - JackeryMqttPushClient is accessible via module.__getattr__
  - AttributeError is raised for unknown attribute names
  - Direct exports (JackeryApi, JackeryAuthError, JackeryError, JackeryApiError) work
- JackeryApi.__init__: new transport counter attributes
  - _requests_total starts at 0
  - _requests_failed starts at 0
  - _timeouts_total starts at 0
  - _auth_retries starts at 0
"""

from unittest.mock import MagicMock

import pytest

import custom_components.jackery_solarvault.client as client_pkg
from custom_components.jackery_solarvault.client.api import JackeryApi

# ---------------------------------------------------------------------------
# client/__init__.py __getattr__ — lazy import via import_module
# ---------------------------------------------------------------------------


class TestClientPackageGetattr:
    """Tests for the PEP 562 __getattr__ in client/__init__.py."""

    def test_jackery_mqtt_push_client_is_accessible(self) -> None:
        """JackeryMqttPushClient must be retrievable via the client package namespace."""
        # This exercises the __getattr__ path in client/__init__.py
        cls = client_pkg.JackeryMqttPushClient
        # Must be a class, not None or a sentinel
        assert cls is not None
        assert isinstance(cls, type)

    def test_jackery_mqtt_push_client_is_same_class_as_direct_import(
        self,
    ) -> None:
        """The lazily loaded class must be identical to the directly imported one."""
        from custom_components.jackery_solarvault.client.mqtt_push import (
            JackeryMqttPushClient,
        )

        lazy_cls = client_pkg.JackeryMqttPushClient
        assert lazy_cls is JackeryMqttPushClient

    def test_unknown_attribute_raises_attribute_error(self) -> None:
        """Accessing an unknown attribute must raise AttributeError."""
        with pytest.raises(AttributeError):
            _ = client_pkg.NonExistentClass  # type: ignore[attr-defined]

    def test_attribute_error_message_contains_attribute_name(self) -> None:
        """AttributeError message should include the requested attribute name."""
        attr_name = "CompletelyMadeUpAttribute"
        with pytest.raises(AttributeError) as exc_info:
            getattr(client_pkg, attr_name)
        assert attr_name in str(exc_info.value)

    def test_direct_exports_still_accessible(self) -> None:
        """The statically exported names must be importable without __getattr__."""
        from custom_components.jackery_solarvault.client import (
            JackeryApi,
            JackeryApiError,
            JackeryAuthError,
            JackeryError,
        )

        assert JackeryApi is not None
        assert JackeryApiError is not None
        assert JackeryAuthError is not None
        assert JackeryError is not None

    def test_getattr_called_multiple_times_returns_same_class(self) -> None:
        """Multiple calls for JackeryMqttPushClient must return the same class object."""
        cls1 = client_pkg.JackeryMqttPushClient
        cls2 = client_pkg.JackeryMqttPushClient
        assert cls1 is cls2


# ---------------------------------------------------------------------------
# JackeryApi.__init__ transport counters
# ---------------------------------------------------------------------------


def _make_api(
    account: str = "user@example.com",
    password: str = "pw",  # noqa: S107
    mqtt_mac_id: str | None = None,
    region_code: str | None = None,
) -> JackeryApi:
    """Create a JackeryApi instance using a mock aiohttp session."""
    session = MagicMock()
    return JackeryApi(
        session=session,
        account=account,
        password=password,
        mqtt_mac_id=mqtt_mac_id,
        region_code=region_code,
    )


class TestJackeryApiTransportCounters:
    """Tests for the transport diagnostic counter attributes added to JackeryApi.__init__."""

    def test_requests_total_initialises_to_zero(self) -> None:
        """_requests_total must start at 0 after construction."""
        api = _make_api()
        assert api._requests_total == 0

    def test_requests_failed_initialises_to_zero(self) -> None:
        """_requests_failed must start at 0 after construction."""
        api = _make_api()
        assert api._requests_failed == 0

    def test_timeouts_total_initialises_to_zero(self) -> None:
        """_timeouts_total must start at 0 after construction."""
        api = _make_api()
        assert api._timeouts_total == 0

    def test_auth_retries_initialises_to_zero(self) -> None:
        """_auth_retries must start at 0 after construction."""
        api = _make_api()
        assert api._auth_retries == 0

    def test_counters_are_independent_across_instances(self) -> None:
        """Transport counters on one instance must not affect another instance."""
        api1 = _make_api(account="user1@example.com")
        api2 = _make_api(account="user2@example.com")

        # Simulate incrementing counters on one instance
        api1._requests_total = 10
        api1._requests_failed = 2

        assert api2._requests_total == 0
        assert api2._requests_failed == 0

    def test_region_code_is_uppercased(self) -> None:
        """region_code is stored as uppercase."""
        api = _make_api(region_code="de")
        assert api._region_code == "DE"

    def test_region_code_none_stays_none(self) -> None:
        """Empty/None region_code normalises to None, not empty string."""
        api = _make_api(region_code=None)
        assert api._region_code is None

    def test_empty_region_code_normalises_to_none(self) -> None:
        """An empty string region_code is treated as None."""
        api = _make_api(region_code="")
        assert api._region_code is None

    def test_whitespace_region_code_normalises_to_none(self) -> None:
        """A whitespace region_code is stripped to empty and then converted to None."""
        api = _make_api(region_code="  ")
        assert api._region_code is None

    def test_token_starts_as_none(self) -> None:
        """The authentication token must be None before any login call."""
        api = _make_api()
        assert api._token is None

    def test_mqtt_user_id_starts_as_none(self) -> None:
        """_mqtt_user_id must be None until populated by login."""
        api = _make_api()
        assert api._mqtt_user_id is None

    def test_mqtt_seed_b64_starts_as_none(self) -> None:
        """_mqtt_seed_b64 must be None until populated by login."""
        api = _make_api()
        assert api._mqtt_seed_b64 is None

    def test_mqtt_mac_id_starts_as_none(self) -> None:
        """_mqtt_mac_id must be None until populated by login."""
        api = _make_api()
        assert api._mqtt_mac_id is None

    def test_configured_mqtt_mac_id_stored(self) -> None:
        """An explicitly provided mqtt_mac_id must be stored as _mqtt_mac_id_configured."""
        api = _make_api(mqtt_mac_id="2abcdef1234567890")
        assert api._mqtt_mac_id_configured == "2abcdef1234567890"

    def test_all_required_diagnostics_buffers_initialised(self) -> None:
        """All documented diagnostics buffer attributes must be present after __init__."""
        api = _make_api()
        assert api.last_login_response is None
        assert api.last_system_list_response is None
        assert isinstance(api.last_property_responses, dict)
        assert api.last_alarm_response is None
        assert api.last_statistic_response is None
        assert api.last_price_response is None
        assert isinstance(api.last_device_statistic_responses, dict)
        assert isinstance(api.last_device_period_stat_responses, dict)
        assert isinstance(api.last_battery_pack_responses, dict)
        assert isinstance(api.last_ota_responses, dict)

    def test_payload_debug_callback_starts_as_none(self) -> None:
        """payload_debug_callback must be None by default."""
        api = _make_api()
        assert api.payload_debug_callback is None
