"""Behavioral tests for diagnostics shape, null preservation and redaction."""

from datetime import timedelta
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault import coordinator as co
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)
from custom_components.jackery_solarvault.const import (
    CONF_ENABLE_UNREDACTED_DEBUG,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    DIAGNOSTICS_SCHEMA_VERSION,
    DOMAIN,
    FIELD_MAC_ID,
    FIELD_TOKEN,
    LOCAL_MQTT_RUNTIME_KEY,
    PAYLOAD_PROPERTIES,
    REDACTED_VALUE,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    RejectionMetrics,
)
from custom_components.jackery_solarvault.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.components.diagnostics import REDACTED

_ENTRY_ID = "diag-export-entry"


def _assert_secret_values_absent(
    result: dict[str, Any],
    *secret_values: str,
) -> None:
    """Assert that no supplied literal survives anywhere in the JSON export."""
    rendered = json.dumps(result, sort_keys=True, default=str)
    for secret in secret_values:
        assert secret not in rendered


def _diagnostics_rig(  # test builder wires every accessor the export touches  # ruff: ignore[too-many-arguments]
    *,
    options: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    coordinator_data: dict[str, Any] | None = None,
    api_overrides: dict[str, Any] | None = None,
    endpoint_backoff: dict[str, Any] | None = None,
    statistics_backfill_state: dict[str, Any] | None = None,
    statistics_backfill_loaded: bool = False,
    rejection_metrics: RejectionMetrics | None = None,
    hass_data: dict[str, Any] | None = None,
) -> tuple[Any, Any]:
    """Build a bare coordinator + config entry for `async_get_config_entry_diagnostics`."""  # ruff: ignore[line-too-long]
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    entry = SimpleNamespace(
        data=data or {},
        options=options or {},
        entry_id=_ENTRY_ID,
        runtime_data=coordinator,
    )
    obj.entry = entry
    obj.hass = SimpleNamespace(
        config=SimpleNamespace(time_zone="UTC"),
        data=hass_data if hass_data is not None else {},
    )
    obj.data = coordinator_data or {}
    api_defaults: dict[str, Any] = {
        "last_login_response": None,
        "last_system_list_response": None,
        "last_property_responses": {},
        "last_alarm_response": None,
        "last_statistic_response": None,
        "last_price_response": None,
        "last_price_sources_response": None,
        "last_price_history_config_response": None,
        "last_device_statistic_responses": {},
        "last_device_period_stat_responses": {},
        "last_battery_pack_responses": {},
        "last_ota_responses": {},
        "last_location_responses": {},
    }
    api_defaults.update(api_overrides or {})
    obj.api = SimpleNamespace(**api_defaults)
    obj._configured_update_interval = timedelta(seconds=30)  # ruff: ignore[private-member-access]
    obj._polling_diagnostics = {}  # ruff: ignore[private-member-access]
    obj._mqtt = None  # ruff: ignore[private-member-access]
    obj._endpoint_backoff = endpoint_backoff or {}  # ruff: ignore[private-member-access]
    obj._ble_listener = None  # ruff: ignore[private-member-access]
    obj._device_index = {}  # ruff: ignore[private-member-access]
    obj._local_mqtt_config_diagnostics = {  # ruff: ignore[private-member-access]
        "scheduled": 0,
        "attempts": 0,
        "target_count": 0,
        "last_status": "not_started",
        "last_attempt_at": None,
        "last_success_at": None,
        "last_errors": {},
    }
    obj._statistics_backfill_state = statistics_backfill_state or {}  # ruff: ignore[private-member-access]
    obj._statistics_backfill_state_loaded = statistics_backfill_loaded  # ruff: ignore[private-member-access]
    obj.rejection_metrics = (
        rejection_metrics if rejection_metrics is not None else RejectionMetrics()
    )
    return coordinator, entry


# ---------------------------------------------------------------------------
# top-level shape / missing-value preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_export_preserves_missing_values_and_documented_schema() -> None:
    """Missing measurements and rejection details remain JSON null, not text."""
    coordinator, entry = _diagnostics_rig(
        data={"host": None},
        coordinator_data={"dev-1": {PAYLOAD_PROPERTIES: {"soc": None}}},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert set(result) == {
        "entry_data",
        "options",
        "devices",
        "schema_version",
        "rejection_metrics",
        "raw_api",
    }
    assert result["schema_version"] == DIAGNOSTICS_SCHEMA_VERSION
    metrics = result["rejection_metrics"]
    assert metrics["schema_version"] == DIAGNOSTICS_SCHEMA_VERSION
    assert metrics["counters"] == {
        "http_auth_rejections": 0,
        "mqtt_broker_rejections": 0,
        "payload_validation_rejections": 0,
        "schema_rejections": 0,
        "timestamp_skew_rejections": 0,
        "auth_token_expiry_rejections": 0,
    }
    assert metrics["last_rejection"] is None
    assert result["devices"]["device_1"][PAYLOAD_PROPERTIES]["soc"] is None
    decoded = json.loads(json.dumps(result))
    assert decoded["devices"]["device_1"][PAYLOAD_PROPERTIES]["soc"] is None


@pytest.mark.asyncio()
async def test_export_surfaces_recorded_rejection_metrics() -> None:
    """A recorded rejection reaches diagnostics with its structured context."""
    rejection_metrics = RejectionMetrics()
    rejection_metrics.increment("http_auth_rejections", "unauthorized")
    coordinator, entry = _diagnostics_rig(rejection_metrics=rejection_metrics)

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    exported = result["rejection_metrics"]
    assert exported["counters"]["http_auth_rejections"] == 1
    assert exported["last_rejection"]["counter"] == "http_auth_rejections"
    assert exported["last_rejection"]["reason"] == "unauthorized"
    assert isinstance(exported["last_rejection"]["at"], str)


# ---------------------------------------------------------------------------
# redaction of entry_data / options
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_export_redacts_token_from_entry_data() -> None:
    """A stored auth token never leaves the export in cleartext."""
    coordinator, entry = _diagnostics_rig(
        data={FIELD_TOKEN: "super-secret-token", "unrelated": "kept"},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert result["entry_data"][FIELD_TOKEN] == REDACTED
    assert result["entry_data"]["unrelated"] == "kept"


@pytest.mark.asyncio()
async def test_export_redacts_local_mqtt_credentials_from_options() -> None:
    """Local-MQTT broker credentials stored in options are redacted."""
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_THIRD_PARTY_MQTT_IP: "192.168.1.50",
            CONF_THIRD_PARTY_MQTT_USERNAME: "mqtt-user",
            CONF_THIRD_PARTY_MQTT_PASSWORD: "super-secret",
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert result["options"][CONF_THIRD_PARTY_MQTT_IP] == "192.168.1.50"
    assert result["options"][CONF_THIRD_PARTY_MQTT_USERNAME] == "mqtt-user"
    assert result["options"][CONF_THIRD_PARTY_MQTT_PASSWORD] == REDACTED


# ---------------------------------------------------------------------------
# devices mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_export_devices_label_identifiers_and_keep_measurements() -> None:
    """Device ids remain available to correlate transport payloads."""
    coordinator, entry = _diagnostics_rig(
        coordinator_data={
            "dev-b": {PAYLOAD_PROPERTIES: {"soc": 55}},
            "dev-a": {PAYLOAD_PROPERTIES: {FIELD_MAC_ID: "AA:BB:CC", "soc": 42}},
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert set(result["devices"]) == {"device_1", "device_2"}
    assert result["devices"]["device_1"][PAYLOAD_PROPERTIES]["soc"] == 42  # ruff: ignore[magic-value-comparison]
    assert result["devices"]["device_1"][PAYLOAD_PROPERTIES][FIELD_MAC_ID] == "AA:BB:CC"
    assert result["devices"]["device_2"][PAYLOAD_PROPERTIES]["soc"] == 55  # ruff: ignore[magic-value-comparison]


# ---------------------------------------------------------------------------
# raw_api: api response snapshots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_raw_api_login_redacted_and_property_responses_identified() -> None:
    """Login secrets stay masked; property responses retain device identity."""
    coordinator, entry = _diagnostics_rig(
        api_overrides={
            "last_login_response": {FIELD_TOKEN: "abc123", "kept": "value"},
            "last_property_responses": {
                "dev-2": {"soc": 90},
                "dev-1": {"soc": 10},
            },
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    raw = result["raw_api"]
    assert raw["login_response"][FIELD_TOKEN] == REDACTED
    assert raw["login_response"]["kept"] == "value"
    assert set(raw["property_responses"]) == {
        "property_response_1",
        "property_response_2",
    }
    assert raw["property_responses"]["property_response_1"]["soc"] == 10  # ruff: ignore[magic-value-comparison]
    assert raw["property_responses"]["property_response_2"]["soc"] == 90  # ruff: ignore[magic-value-comparison]


@pytest.mark.asyncio()
async def test_raw_api_non_dict_payload_is_wrapped_before_redaction() -> None:
    """A non-dict per-device payload is wrapped as `{"value": ...}` before redaction."""
    coordinator, entry = _diagnostics_rig(
        api_overrides={"last_ota_responses": {"dev-1": "not-a-dict-payload"}},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    ota = result["raw_api"]["ota_responses"]
    assert ota["ota_response_1"] == {"value": "not-a-dict-payload"}


@pytest.mark.asyncio()
async def test_export_raw_api_coordinator_metadata_reflects_polling_interval() -> None:
    """The coordinator metadata block reports the configured poll interval."""
    coordinator, entry = _diagnostics_rig()

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    coordinator_meta = result["raw_api"]["coordinator"]
    assert coordinator_meta["update_interval_seconds"] == 30  # ruff: ignore[magic-value-comparison]
    assert coordinator_meta["coordinator_polling"] is True
    assert coordinator_meta["redactions_enforced"] is True


# ---------------------------------------------------------------------------
# raw_api: endpoint backoff / statistics backfill / app chart import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_export_raw_api_includes_active_endpoint_backoff_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An active non-energy backoff window surfaces in the export."""
    monkeypatch.setattr(co.time, "monotonic", lambda: 1_000.0)
    coordinator, entry = _diagnostics_rig(
        endpoint_backoff={"device_list": {"until": 1_030.0, "code": 500, "level": 2}},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    backoff = result["raw_api"]["endpoint_backoff"]
    assert backoff["active_count"] == 1
    assert backoff["active"]["device_list"]["code"] == 500  # ruff: ignore[magic-value-comparison]


@pytest.mark.asyncio()
async def test_export_raw_api_statistics_backfill_reflects_coordinator_state() -> None:
    """The statistics-backfill block mirrors the coordinator's persisted state."""
    coordinator, entry = _diagnostics_rig(
        statistics_backfill_loaded=True,
        statistics_backfill_state={
            "devices": {"SN-A": {"last_repair_date": "2026-01-01"}},
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    backfill = result["raw_api"]["statistics_backfill"]
    assert backfill["loaded"] is True
    assert backfill["tracked_devices"] == 1


@pytest.mark.asyncio()
async def test_export_raw_api_app_chart_import_empty_when_no_devices() -> None:
    """With no polled devices, the app-chart-import block reports no devices."""
    coordinator, entry = _diagnostics_rig()

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert result["raw_api"]["app_chart_import"]["devices"] == {}


@pytest.mark.asyncio()
async def test_app_chart_import_reports_nonzero_scalar_without_series() -> None:
    """A real scalar the recorder never receives is surfaced for diagnosability.

    A NON-zero scalar without a curve is a value the integration discards
    because a scalar cannot establish when the energy occurred. Users must be
    able to tell that apart from a cloud zero-shell, which carries no value.
    """
    day_section = "device_battery_stat_day"
    coordinator, entry = _diagnostics_rig(
        coordinator_data={
            "device_1": {
                day_section: {
                    "unit": "W",
                    "batOtGridEgy": "0.01",
                    "y1": [0.0] * 4,
                    "y2": [5.0, 5.0, 4.0, 4.0],
                },
            },
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    discarded = result["raw_api"]["app_chart_import"]["discarded_scalar_without_series"]
    assert discarded["count"] >= 1
    entry_row = next(
        item for item in discarded["entries"] if item["stat_key"] == "batOtGridEgy"
    )
    assert entry_row["scalar_total"] == pytest.approx(0.01)
    assert entry_row["unit"] == "W"
    assert entry_row["section"] == day_section


@pytest.mark.asyncio()
async def test_app_chart_import_ignores_zero_scalar_zero_shell() -> None:
    """A zero scalar without a curve is a cloud zero-shell, not a discarded value.

    Reporting it would fire on every CT/EPS period that legitimately carries no
    energy, drowning the signal from genuine discard cases.
    """
    day_section = "device_ct_stat_day"
    coordinator, entry = _diagnostics_rig(
        coordinator_data={
            "device_1": {
                day_section: {
                    "unit": "kWh",
                    "totalInCtEnergy": "0",
                    "totalOutCtEnergy": "0",
                    "y1": [],
                    "y2": [],
                },
            },
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    discarded = result["raw_api"]["app_chart_import"]["discarded_scalar_without_series"]
    assert discarded["count"] == 0
    assert discarded["entries"] == []


# ---------------------------------------------------------------------------
# local_mqtt sub-section
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_bridge_disabled() -> None:
    """Neither local nor third-party bridge enabled yields bridge_disabled."""
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: False,
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    local_mqtt = result["raw_api"]["local_mqtt"]
    assert local_mqtt["enabled"] is False
    assert local_mqtt["disabled_reason"] == "bridge_disabled"


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_missing_broker_host() -> None:
    """An enabled bridge with no configured host reports missing_broker_host."""
    coordinator, entry = _diagnostics_rig(
        options={CONF_THIRD_PARTY_MQTT_ENABLE: True},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert result["raw_api"]["local_mqtt"]["disabled_reason"] == "missing_broker_host"


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_redacts_broker_wide_topic() -> None:
    """A user-selected broker-wide topic remains private in diagnostics."""
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.1.10",
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "#",
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    local_mqtt = result["raw_api"]["local_mqtt"]
    assert local_mqtt["disabled_reason"] == "client_not_started"
    assert local_mqtt["configured_local_mqtt"]["effective_topic_filter"] == "#"


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_redacts_valid_prefixed_custom_topic() -> None:
    """A valid Jackery topic remains private in the shareable export."""
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.1.10",
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "hb/app/custom",
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    configured = result["raw_api"]["local_mqtt"]["configured_local_mqtt"]
    assert configured["topic_filter"] == "hb/app/custom"
    assert configured["effective_topic_filter"] == "hb/app/custom"


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_redacts_local_device_topic() -> None:
    """A device-specific local topic never appears in a diagnostics export."""
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.1.10",
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "homeassistant",
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    configured = result["raw_api"]["local_mqtt"]["configured_local_mqtt"]
    assert configured["topic_filter"] == "homeassistant"
    assert configured["effective_topic_filter"] == "homeassistant"


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_client_not_started_with_valid_config() -> None:
    """A fully valid config with no registered client falls back to client_not_started."""  # ruff: ignore[line-too-long]
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.1.10",
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    local_mqtt = result["raw_api"]["local_mqtt"]
    assert local_mqtt["disabled_reason"] == "client_not_started"
    assert local_mqtt["configured_local_mqtt"]["host"] == "192.168.1.10"
    assert local_mqtt["configured_local_mqtt"]["port"] == "1883"


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_uses_registered_client_snapshot() -> None:
    """A real registered local-MQTT client's own snapshot is used verbatim."""
    client = MagicMock(spec=JackeryLocalMqttClient)
    client.diagnostics_snapshot.return_value = {"messages_received": 7}
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.1.10",
        },
        hass_data={DOMAIN: {_ENTRY_ID: {LOCAL_MQTT_RUNTIME_KEY: client}}},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert result["raw_api"]["local_mqtt"] == {"messages_received": 7}
    client.diagnostics_snapshot.assert_called_once_with()


@pytest.mark.asyncio()
async def test_entry_raw_option_unredacts_local_mqtt_snapshot() -> None:
    """Local MQTT topics are requested unredacted for this entry's export."""
    client = MagicMock(spec=JackeryLocalMqttClient)
    client.diagnostics_snapshot.return_value = {"last_topic": "hb/device/serial/status"}
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_ENABLE_UNREDACTED_DEBUG: True,
            CONF_THIRD_PARTY_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "192.168.1.10",
        },
        hass_data={DOMAIN: {_ENTRY_ID: {LOCAL_MQTT_RUNTIME_KEY: client}}},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert result["raw_api"]["local_mqtt"]["last_topic"] == ("hb/device/serial/status")
    client.diagnostics_snapshot.assert_called_once_with(redact=False)


# ---------------------------------------------------------------------------
# mandatory redaction paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_legacy_unredacted_option_cannot_disable_export_redaction() -> None:
    """A stale persisted raw-data option cannot bypass mandatory redaction."""
    token = "diag-token-secret"
    account_id = "diag-account-secret"
    password = "diag-password-secret"
    broker_secret = "diag-broker-secret"
    bluetooth_key = "diag-bluetooth-secret"
    latitude = "52.520008"
    longitude = "13.404954"
    coordinator, entry = _diagnostics_rig(
        options={
            "enable_unredacted_diagnostics": True,
            CONF_THIRD_PARTY_MQTT_PASSWORD: broker_secret,
        },
        data={
            FIELD_TOKEN: token,
            "nested": {
                "PASSWORD": password,
                "bluetoothKey": bluetooth_key,
                "Latitude": latitude,
                "LONGITUDE": longitude,
            },
        },
        api_overrides={
            "last_login_response": {
                "ACCOUNTID": account_id,
                "nested": {"MQTTPASSWORD": broker_secret},
            },
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    _assert_secret_values_absent(result, token, password, broker_secret)
    rendered = json.dumps(result, default=str)
    assert account_id in rendered
    assert bluetooth_key not in rendered
    assert latitude in rendered
    assert longitude in rendered
    assert result["entry_data"][FIELD_TOKEN] in {REDACTED, REDACTED_VALUE}
    metadata = result["raw_api"]["coordinator"]
    assert metadata["redactions_enforced"] is True
    assert "redactions_disabled" not in metadata


@pytest.mark.asyncio()
async def test_dev_mode_environment_disables_export_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JACKERY_DEV_MODE exports complete, unredacted data (never share it).

    docs/source-of-truth/diagnostics.md: "DEV_MODE wieder funktional machen";
    docs/html/diagnostics.html describes redactions_disabled + dev_mode source.
    """
    monkeypatch.setenv("JACKERY_DEV_MODE", "1")
    token = "env-token-secret"
    broker_secret = "env-broker-secret"
    coordinator, entry = _diagnostics_rig(
        data={
            FIELD_TOKEN: token,
            "nested": {"mqttPassWord": broker_secret},
        },
        options={CONF_THIRD_PARTY_MQTT_PASSWORD: broker_secret},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    exported = json.dumps(result, default=str)
    assert token in exported
    assert broker_secret in exported
    metadata = result["raw_api"]["coordinator"]
    assert metadata["redactions_enforced"] is False
    assert metadata["dev_mode"] == "JACKERY_DEV_MODE=1"


@pytest.mark.asyncio()
async def test_entry_option_disables_export_redaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The options-flow debug switch exposes complete payloads for one entry."""
    monkeypatch.delenv("JACKERY_DEV_MODE", raising=False)
    coordinator, entry = _diagnostics_rig(
        options={CONF_ENABLE_UNREDACTED_DEBUG: True},
        data={FIELD_TOKEN: "entry-token-secret"},
        coordinator_data={"device-secret": {"bluetoothKey": "entry-ble-secret"}},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    rendered = json.dumps(result, default=str)
    assert "entry-token-secret" in rendered
    assert "entry-ble-secret" in rendered
    assert "device-secret" in result["devices"]
    metadata = result["raw_api"]["coordinator"]
    assert metadata["redactions_enforced"] is False
    assert metadata["dev_mode"] == "options.enable_unredacted_debug"


@pytest.mark.asyncio()
async def test_export_stays_redacted_without_dev_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without JACKERY_DEV_MODE the export keeps mandatory redaction."""
    monkeypatch.delenv("JACKERY_DEV_MODE", raising=False)
    token = "env-token-secret"
    broker_secret = "env-broker-secret"
    coordinator, entry = _diagnostics_rig(
        data={
            FIELD_TOKEN: token,
            "nested": {"mqttPassWord": broker_secret},
        },
        options={CONF_THIRD_PARTY_MQTT_PASSWORD: broker_secret},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    _assert_secret_values_absent(result, token, broker_secret)
    metadata = result["raw_api"]["coordinator"]
    assert metadata["redactions_enforced"] is True
    assert "dev_mode" not in metadata


@pytest.mark.asyncio()
async def test_sensitive_option_value_is_scrubbed_from_error_text() -> None:
    """Known credentials are removed even when echoed inside an error string."""
    broker_secret = "broker-password-echo-secret"
    coordinator, entry = _diagnostics_rig(
        options={CONF_THIRD_PARTY_MQTT_PASSWORD: broker_secret},
    )
    coordinator._polling_diagnostics = {  # ruff: ignore[private-member-access]
        "last_error": f"Authentication rejected for password {broker_secret}",
    }

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    _assert_secret_values_absent(result, broker_secret)
    assert (
        result["raw_api"]["polling"]["last_error"]
        == "Authentication rejected for password **REDACTED**"
    )
