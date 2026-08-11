"""Shared entity base class."""

import logging
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DEFAULT_DEVICE_MODEL_FALLBACK,
    DOMAIN,
    FIELD_CURRENT_VERSION,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEV_MODEL,
    FIELD_ID,
    FIELD_MODEL,
    FIELD_MODEL_NAME,
    FIELD_ONLINE_STATE,
    FIELD_ONLINE_STATUS,
    FIELD_SCAN_NAME,
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_NAME,
    FIELD_SYSTEM_SN,
    FIELD_TYPE_NAME,
    FIELD_VERSION,
    FIELD_WNAME,
    MANUFACTURER,
    PAYLOAD_ALARM,
    PAYLOAD_BATTERY_TRENDS,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_DISCOVERY,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_LOCATION,
    PAYLOAD_OTA,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_PV_TRENDS,
    PAYLOAD_SOCKET_STAT,
    PAYLOAD_STATISTIC,
    PAYLOAD_SYMMETRY_STAT,
    PAYLOAD_SYSTEM,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_WEATHER_PLAN,
)
from .coordinator import JackerySolarVaultCoordinator
from .util import (
    first_nonblank_text,
    jackery_online_state,
    nonblank_text,
    smart_plug_serial,
    stable_subdevice_key,
    subdevice_branding,
)

_LOGGER = logging.getLogger(__name__)

HTTP_DATA_SOURCES = ("http",)
LAYER5_DATA_SOURCES = ("cloud_mqtt", "local_mqtt", "ble")
ALL_LIVE_DATA_SOURCES = (*HTTP_DATA_SOURCES, *LAYER5_DATA_SOURCES)
# The App has no Local-MQTT command publisher. Direct device commands are
# BLE-first with cloud MQTT fallback; explicit REST setters declare HTTP
# separately.
LAYER5_COMMAND_SOURCES = ("ble", "cloud_mqtt")
HTTP_COMMAND_SOURCES = ("http",)
HTTP_AND_LAYER5_COMMAND_SOURCES = (*HTTP_COMMAND_SOURCES, *LAYER5_COMMAND_SOURCES)
_SOURCE_FIELD_SEQUENCE_ATTRIBUTES = (
    "app_fields",
    "source_keys",
    "required_property_keys",
    "aliases",
    "negative_aliases",
    "sum_fields",
    "negative_sum_fields",
    "fallback_fields",
)
_SOURCE_FIELD_SCALAR_ATTRIBUTES = ("field", "smali_field", "stat_key")
_FALLBACK_SOURCE_FIELD_INDEX = 1


def system_device_identifiers(
    payload: dict[str, Any],
) -> set[tuple[str, str]]:
    """Return stable HA identifiers for a discovered SolarVault system."""
    system = payload.get(PAYLOAD_SYSTEM) or {}
    if not isinstance(system, dict):
        return set()
    identifiers: set[tuple[str, str]] = set()
    system_id = first_nonblank_text(
        system.get(FIELD_SYSTEM_ID),
        system.get(FIELD_ID),
    )
    system_sn = nonblank_text(system.get(FIELD_SYSTEM_SN))
    if system_id is not None:
        identifiers.add((DOMAIN, f"system_{system_id}"))
    if system_sn is not None:
        identifiers.add((DOMAIN, f"system_sn_{system_sn}"))
    return identifiers


def system_primary_identifier(
    payload: dict[str, Any],
) -> tuple[str, str] | None:
    """Return the deterministic identifier used by child ``via_device`` links."""
    system = payload.get(PAYLOAD_SYSTEM) or {}
    if not isinstance(system, dict):
        return None
    system_id = first_nonblank_text(
        system.get(FIELD_SYSTEM_ID),
        system.get(FIELD_ID),
    )
    if system_id is not None:
        return (DOMAIN, f"system_{system_id}")
    system_sn = nonblank_text(system.get(FIELD_SYSTEM_SN))
    if system_sn is not None:
        return (DOMAIN, f"system_sn_{system_sn}")
    return None


def system_device_info_from_payload(
    payload: dict[str, Any],
    device_id: str,
) -> DeviceInfo | None:
    """Build the parent-system record that must exist before child entities."""
    identifiers = system_device_identifiers(payload)
    if not identifiers:
        return None
    system = payload.get(PAYLOAD_SYSTEM) or {}
    if not isinstance(system, dict):
        return None
    name = first_nonblank_text(
        system.get(FIELD_SYSTEM_NAME),
        system.get(FIELD_DEVICE_NAME),
        fallback=f"Jackery SolarVault system {device_id}",
    )
    system_sn = nonblank_text(system.get(FIELD_SYSTEM_SN))
    return DeviceInfo(
        identifiers=identifiers,
        manufacturer=MANUFACTURER,
        name=str(name),
        model=DEFAULT_DEVICE_MODEL_FALLBACK,
        serial_number=system_sn,
    )


def property_data_sources(
    *fields: str,
    layer5_proven: bool = False,
) -> tuple[str, ...]:
    """Return the App-proven source family for property-model fields.

    HomeBody, SystemBody, PortableBody and decoded subdevice property models
    are available through the documented HTTP property/shadow reads and the
    decoded Layer-5 observation pipeline. A description with no property field
    stays HTTP-only; this is the safe default for statistics, plans, price,
    discovery and other REST-owned sections.
    """
    return ALL_LIVE_DATA_SOURCES if layer5_proven and any(fields) else HTTP_DATA_SOURCES


def payload_properties_for_sources(
    payload: dict[str, Any],
    data_sources: tuple[str, ...] = ALL_LIVE_DATA_SOURCES,
) -> dict[str, Any]:
    """Return the transport-neutral coordinator-resolved live properties.

    ``data_sources`` remains part of the entity-description interface, but it
    must never filter already-decoded live values. HTTP, cloud MQTT, local MQTT
    and BLE all contribute to the same non-blank property snapshot.
    """
    del data_sources
    props = payload.get(PAYLOAD_PROPERTIES) or {}
    merged_props = props if isinstance(props, dict) else {}
    if merged_props:
        return merged_props
    http_props = payload.get(PAYLOAD_HTTP_PROPERTIES) or {}
    return http_props if isinstance(http_props, dict) else {}


class JackeryEntity(CoordinatorEntity[JackerySolarVaultCoordinator]):
    """Jackery entity."""

    _attr_has_entity_name = True
    data_sources: tuple[str, ...] = HTTP_DATA_SOURCES
    command_sources: tuple[str, ...] = ()
    app_fields: tuple[str, ...] = ()
    availability_uses_supervisor = False
    device_registry_role = "head"

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        key_suffix: str,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{key_suffix}"

    @property
    def _payload(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self._device_id, {}) or {}

    @property
    def _properties(self) -> dict[str, Any]:
        return self._source_properties(ALL_LIVE_DATA_SOURCES)

    @property
    def _merged_properties(self) -> dict[str, Any]:
        props = self._payload.get(PAYLOAD_PROPERTIES) or {}
        return props if isinstance(props, dict) else {}

    @property
    def _http_properties(self) -> dict[str, Any]:
        props = self._payload.get(PAYLOAD_HTTP_PROPERTIES) or {}
        return props if isinstance(props, dict) else {}

    def _source_properties(
        self,
        data_sources: tuple[str, ...],
    ) -> dict[str, Any]:
        """Return live properties without reapplying transport priority."""
        return payload_properties_for_sources(self._payload, data_sources)

    def _payload_section_for_sources(
        self,
        section: str,
        data_sources: tuple[str, ...] = ALL_LIVE_DATA_SOURCES,
    ) -> dict[str, Any]:
        if section == PAYLOAD_PROPERTIES:
            return self._source_properties(data_sources)
        source = self._payload.get(section) or {}
        return source if isinstance(source, dict) else {}

    def _payload_for_sources(
        self,
        data_sources: tuple[str, ...] = ALL_LIVE_DATA_SOURCES,
    ) -> dict[str, Any]:
        payload = dict(self._payload)
        payload[PAYLOAD_PROPERTIES] = self._source_properties(data_sources)
        return payload

    @property
    def _device_meta(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_DEVICE) or {}

    @property
    def _discovery(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_DISCOVERY) or {}

    @property
    def _system(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_SYSTEM) or {}

    @property
    def _statistic(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_STATISTIC) or {}

    @property
    def _price(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_PRICE) or {}

    @property
    def _pv_trends(self) -> dict[str, Any]:
        """Fetch the photovoltaic (PV) trends section from the device payload.

        Returns:
            dict[str, Any]: PV trends data from the device payload, or an empty dict if
            not present.
        """  # noqa: D421, RUF105
        return self._payload.get(PAYLOAD_PV_TRENDS) or {}

    @property
    def _alarm(self) -> object:
        """Return the alarm payload for the device.

        Returns:
            The alarm payload object from the device payload, or None if no alarm data
            is present.
        """  # noqa: D421, RUF105
        return self._payload.get(PAYLOAD_ALARM)

    @property
    def _device_statistic(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_DEVICE_STATISTIC) or {}

    @property
    def _ota(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_OTA) or {}

    @property
    def _location(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_LOCATION) or {}

    @property
    def _home_trends(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_HOME_TRENDS) or {}

    @property
    def _battery_trends(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_BATTERY_TRENDS) or {}

    @property
    def _weather_plan(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_WEATHER_PLAN) or {}

    @property
    def _task_plan(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_TASK_PLAN) or {}

    @property
    def _symmetry_stat(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_SYMMETRY_STAT) or {}

    @property
    def _socket_stat(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_SOCKET_STAT) or {}

    @property
    def device_info(self) -> DeviceInfo:
        """Constructs the DeviceInfo for the parent SolarVault device.

        The result includes the parent identifier, manufacturer, display name,
        model, and optional serial and software versions. Names and models use
        deterministic payload fallbacks so temporary source loss cannot change
        registry identity.

        Returns:
            DeviceInfo: DeviceInfo populated for the parent SolarVault device.
        """
        if self.device_registry_role == "system":
            system_info = self._system_device_info()
            if system_info is not None:
                return system_info

        name = first_nonblank_text(
            self._discovery.get(FIELD_DEVICE_NAME),
            self._device_meta.get(FIELD_DEVICE_NAME),
            self._properties.get(FIELD_WNAME),
            fallback=f"Jackery {self._device_id}",
        )

        model = first_nonblank_text(
            self._discovery.get(FIELD_DEV_MODEL),
            self._device_meta.get(FIELD_MODEL_NAME),
            fallback=DEFAULT_DEVICE_MODEL_FALLBACK,
        )
        sw_version = nonblank_text(self._ota.get(FIELD_CURRENT_VERSION))
        sn = first_nonblank_text(
            self._device_meta.get(FIELD_DEVICE_SN),
            self._discovery.get(FIELD_DEVICE_SN),
        )

        device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer=MANUFACTURER,
            name=str(name),
            model=str(model),
            serial_number=sn,
            sw_version=sw_version,
        )
        if (parent_identifier := system_primary_identifier(self._payload)) is not None:
            device_info["via_device"] = parent_identifier
        return device_info

    def _system_identifiers(self) -> set[tuple[str, str]]:
        """Return stable HA identifiers for the parent SolarVault system."""
        return system_device_identifiers(self._payload)

    def _system_primary_identifier(self) -> tuple[str, str] | None:
        """Return the deterministic parent identifier used by ``via_device``."""
        return system_primary_identifier(self._payload)

    def _system_device_info(self) -> DeviceInfo | None:
        """Build the separate parent-system registry record when discovery proves it."""
        return system_device_info_from_payload(self._payload, self._device_id)

    def _build_smart_plug_device_info(
        self,
        plug_index: int,
        plug: dict[str, Any],
        plug_key: str | None = None,
    ) -> DeviceInfo:
        """Construct DeviceInfo for a smart-plug subdevice.

        The subdevice remains attached to the parent SolarVault.

        Parameters:
            plug_index (int): 1-based index used to form the subdevice identifier and
            fallback display name.
            plug (dict[str, Any]): Smart-plug payload containing fields such as serial
            numbers, model/type names, scan name, device name, and version.

        Returns:
            DeviceInfo: Device registry metadata for the smart-plug including
            identifiers, manufacturer, name, model, serial_number, sw_version, and
            via_device.
        """
        base_name = first_nonblank_text(
            self._system.get(FIELD_DEVICE_NAME),
            self._discovery.get(FIELD_DEVICE_NAME),
            self._properties.get(FIELD_WNAME),
            fallback=f"Jackery {self._device_id}",
        )
        sn = smart_plug_serial(plug)
        stable_key = plug_key or stable_subdevice_key("smart_plug", sn, plug_index)
        # Branding lookup against the documented accessory catalog so the
        # UI shows "Shelly Plus Plug S" instead of the raw "shellyplusplugs"
        # wire identifier (PROTOCOL §3 + source-of-truth scanName table).
        manufacturer_brand, model_label = subdevice_branding(plug.get(FIELD_SCAN_NAME))
        display_name = first_nonblank_text(
            plug.get(FIELD_DEVICE_NAME),
            model_label,
            plug.get(FIELD_SCAN_NAME),
            fallback=f"Smart Plug {plug_index}",
        )
        model = first_nonblank_text(
            model_label,
            plug.get(FIELD_MODEL),
            plug.get(FIELD_MODEL_NAME),
            plug.get(FIELD_TYPE_NAME),
            fallback="Smart Plug",
        )
        version = first_nonblank_text(
            plug.get(FIELD_VERSION),
            plug.get(FIELD_CURRENT_VERSION),
        )
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{stable_key}")},
            manufacturer=manufacturer_brand or MANUFACTURER,
            name=f"{base_name} {display_name}",
            model=str(model),
            serial_number=str(sn) if sn else None,
            sw_version=str(version) if version else None,
            via_device=(DOMAIN, self._device_id),
        )

    def _source_capability_contract(
        self,
    ) -> tuple[
        bool,
        tuple[str, ...],
        tuple[str, ...],
        tuple[str, ...],
        bool,
    ]:
        """Return product support, source declarations and referenced App fields."""
        description = getattr(self, "entity_description", None)
        if description is None:
            description = getattr(self, "_query_description", None)
        product_support = getattr(description, "product_support", None)
        if product_support is None:
            product_support = getattr(self, "product_support", None)
        supported = not callable(product_support) or bool(
            product_support(self._payload),
        )
        data_sources = tuple(
            getattr(description, "data_sources", self.data_sources)
            or HTTP_DATA_SOURCES,
        )
        command_sources = tuple(
            getattr(description, "command_sources", self.command_sources) or (),
        )
        field_candidates: list[str] = []
        for owner in (description, self):
            if owner is None:
                continue
            for attribute in _SOURCE_FIELD_SEQUENCE_ATTRIBUTES:
                values = getattr(owner, attribute, ())
                if isinstance(values, str):
                    field_candidates.append(values)
                elif isinstance(values, tuple):
                    field_candidates.extend(str(value) for value in values if value)
            for attribute in _SOURCE_FIELD_SCALAR_ATTRIBUTES:
                value = getattr(owner, attribute, None)
                if value:
                    field_candidates.append(str(value))
            fallback_sources = getattr(owner, "fallback_sources", ())
            if isinstance(fallback_sources, tuple):
                field_candidates.extend(
                    str(fallback[1])
                    for fallback in fallback_sources
                    if (
                        isinstance(fallback, tuple)
                        and len(fallback) > _FALLBACK_SOURCE_FIELD_INDEX
                        and fallback[_FALLBACK_SOURCE_FIELD_INDEX]
                    )
                )
        return (
            supported,
            data_sources,
            command_sources,
            tuple(dict.fromkeys(field_candidates)),
            bool(
                getattr(
                    description,
                    "availability_uses_supervisor",
                    self.availability_uses_supervisor,
                )
            ),
        )

    def _online_marker_available(self, transport_reachable: bool) -> bool:
        """Return availability after applying the optional cloud online marker."""
        online = self._device_meta.get(FIELD_ONLINE_STATUS)
        if online is None:
            online = self._system.get(FIELD_ONLINE_STATE)
        parsed_online = jackery_online_state(online) if online is not None else None
        if parsed_online is not None:
            return parsed_online or transport_reachable
        return transport_reachable or self._device_id in (self.coordinator.data or {})

    @property
    def available(self) -> bool:
        """Availability for the entity's product, fields and transports.

        Explicit product support and source-specific freshness are checked before
        the parent device's online marker. A fresh independent transport remains
        authoritative when a stale cloud marker incorrectly reports the device
        offline.
        """
        if self._device_id not in (self.coordinator.data or {}):
            return False
        supported, data_sources, command_sources, fields, supervisor_only = (
            self._source_capability_contract()
        )
        if not supported:
            return False
        source_checker = getattr(
            self.coordinator,
            "is_entity_source_available",
            None,
        )
        if callable(source_checker) and (command_sources or supervisor_only):
            transport_reachable = bool(
                source_checker(
                    self._device_id,
                    data_sources=data_sources,
                    command_sources=command_sources,
                    fields=fields,
                    supervisor_only=supervisor_only,
                )
            )
            if not transport_reachable:
                return False
        else:
            transport_reachable = self.coordinator.is_device_reachable(
                self._device_id,
            )
        if not super().available and not transport_reachable:
            return False
        return self._online_marker_available(transport_reachable)

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to Home Assistant."""
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is about to be removed from Home Assistant."""
        await super().async_will_remove_from_hass()
