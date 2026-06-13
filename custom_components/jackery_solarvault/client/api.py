"""Async API client for the Jackery SolarVault cloud (iot.jackeryapp.com).

Endpoint paths and polling rules are mirrored from PROTOCOL.md §2.
MQTT command details are documented separately in PROTOCOL.md §3.

Domain-specific endpoint methods are organized into mixins under
``_endpoints/``.  This module composes them into the unified
``JackeryApi`` facade.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ._crypto import (  # noqa: F401  — public re-export
    _generate_udid,
    _rsa_pkcs1v15_encrypt,
    encrypt_mqtt_body,
)
from ._endpoints.accessories import AccessoriesEndpointMixin
from ._endpoints.auth import AuthEndpointMixin
from ._endpoints.device import DeviceEndpointMixin
from ._endpoints.energy_price import EnergyPriceEndpointMixin
from ._endpoints.misc import MiscEndpointMixin
from ._endpoints.push import PushEndpointMixin
from ._endpoints.shelly import ShellyEndpointMixin
from ._endpoints.smart_mode import SmartModeEndpointMixin
from ._endpoints.statistics import StatisticsEndpointMixin
from ._http import (  # noqa: F401  — public re-export for error hierarchy
    BaseHTTPMixin,
    JackeryApiError,
    JackeryAuthError,
    JackeryError,
    _write_accepted,
)

if TYPE_CHECKING:
    import aiohttp

_LOGGER = logging.getLogger(__name__)


class JackeryApi(
    AuthEndpointMixin,
    DeviceEndpointMixin,
    StatisticsEndpointMixin,
    EnergyPriceEndpointMixin,
    ShellyEndpointMixin,
    AccessoriesEndpointMixin,
    PushEndpointMixin,
    SmartModeEndpointMixin,
    MiscEndpointMixin,
):
    """Async client for the Jackery SolarVault cloud.

    Inherits all domain-specific endpoint mixins and the shared HTTP
    infrastructure from ``BaseHTTPMixin``.  The ``__init__`` below
    initializes state that all mixins reference via ``self.*``.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        account: str,
        password: str,
        mqtt_mac_id: str | None = None,
        region_code: str | None = None,
    ) -> None:
        """Initialize the JackeryApi client.

        Parameters:
            session: HTTP session used for API requests.
            account: Account identifier for login.
            password: Account password for login.
            mqtt_mac_id: Optional preconfigured MQTT MAC identifier.
            region_code: Optional region/country code.
        """
        self._session = session
        self._account = account
        self._password = password
        self._region_code = (region_code or "").strip().upper() or None
        self._mqtt_mac_id_configured = mqtt_mac_id
        self._mqtt_mac_id_source = "generated"
        self._token: str | None = None
        self._lock = asyncio.Lock()
        self._mqtt_user_id: str | None = None
        self._mqtt_seed_b64: str | None = None
        self._mqtt_mac_id: str | None = None

        # Diagnostics buffers
        self.last_login_response: dict[str, Any] | None = None
        self.last_system_list_response: dict[str, Any] | None = None
        self.last_property_responses: dict[str, dict[str, Any]] = {}
        self.last_alarm_response: dict[str, Any] | None = None
        self.last_statistic_response: dict[str, Any] | None = None
        self.last_price_response: dict[str, Any] | None = None
        self.last_price_sources_response: dict[str, Any] | None = None
        self.last_price_history_config_response: dict[str, Any] | None = None
        self.last_device_statistic_responses: dict[str, dict[str, Any]] = {}
        self.last_device_period_stat_responses: dict[str, dict[str, Any]] = {}
        self.last_battery_pack_responses: dict[str, dict[str, Any]] = {}
        self.last_ota_responses: dict[str, dict[str, Any]] = {}
        self.last_location_responses: dict[str, dict[str, Any]] = {}
        self.payload_debug_callback = None

        # Transport counters for diagnostic sensors (reset on HA restart).
        self._requests_total = 0
        self._requests_failed = 0
        self._timeouts_total = 0
        self._auth_retries = 0
