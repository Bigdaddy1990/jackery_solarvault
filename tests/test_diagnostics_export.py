"""Behavioral tests for the config-entry diagnostics export path.

`_diagnostic_json_null_free` and the null-export AST gate are covered in
`test_diagnostics.py`; this file drives `async_get_config_entry_diagnostics`
itself end-to-end through a bare coordinator + `SimpleNamespace` config
entry, asserting the export shape, secret redaction, and the local-MQTT
sub-section branching of the real export path (diagnostics.py). Only the
accessors the export path actually touches are wired on the coordinator
shell; everything else is left uninitialised.
"""

from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault import util as util_module
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)
from custom_components.jackery_solarvault.const import (
    CONF_ENABLE_UNREDACTED_DIAGNOSTICS,
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_TOPIC,
    CONF_LOCAL_MQTT_USERNAME,
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


@pytest.fixture(autouse=True)
def _reset_dev_mode_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the module-level JACKERY_DEV_MODE cache deterministic per test.

    `dev_mode_redactions_disabled()` memoizes the env-var lookup in a module
    global the first time it is called in a process. Without resetting it,
    whichever test happens to run first would decide the cached value for
    every later test in this file.
    """
    monkeypatch.setattr(util_module, "_DEV_MODE_CACHED", False)


def _contains_raw_none(value: object) -> bool:
    """Return True if `value` contains a raw `None` anywhere in its tree."""
    if value is None:
        return True
    if isinstance(value, dict):
        return any(_contains_raw_none(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_raw_none(item) for item in value)
    return False


def _diagnostics_rig(  # ruff:ignore[too-many-arguments]  # test builder wires every accessor the export touches
    *,
    options: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    coordinator_data: dict[str, Any] | None = None,
    api_overrides: dict[str, Any] | None = None,
    endpoint_backoff: dict[str, Any] | None = None,
    statistics_backfill_state: dict[str, Any] | None = None,
    statistics_backfill_loaded: bool = False,
    rejection_metrics: RejectionMetrics | None = None,
    local_mqtt_unsubs: dict[str, Any] | None = None,
    hass_data: dict[str, Any] | None = None,
) -> tuple[Any, Any]:
    """Build a bare coordinator + config entry for `async_get_config_entry_diagnostics`."""
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
    obj._configured_update_interval = timedelta(seconds=30)
    obj._polling_diagnostics = {}
    obj._mqtt = None
    obj._endpoint_backoff = endpoint_backoff or {}
    obj._ble_listener = None
    obj._device_index = {}
    obj._statistics_backfill_state = statistics_backfill_state or {}
    obj._statistics_backfill_state_loaded = statistics_backfill_loaded
    obj.rejection_metrics = (
        rejection_metrics if rejection_metrics is not None else RejectionMetrics()
    )
    if local_mqtt_unsubs is not None:
        obj._local_mqtt_unsubs = local_mqtt_unsubs
    return coordinator, entry


# ---------------------------------------------------------------------------
# top-level shape / null-free regression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_export_has_expected_top_level_keys_and_no_raw_none() -> None:
    """The export carries its complete documented schema without raw nulls."""
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
    last_rejection = metrics["last_rejection"]
    assert isinstance(last_rejection, str)
    assert not last_rejection
    assert _contains_raw_none(result) is False


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
            CONF_LOCAL_MQTT_HOST: "192.168.1.50",
            CONF_LOCAL_MQTT_USERNAME: "mqtt-user",
            CONF_LOCAL_MQTT_PASSWORD: "super-secret",
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert result["options"][CONF_LOCAL_MQTT_HOST] == REDACTED
    assert result["options"][CONF_LOCAL_MQTT_USERNAME] == REDACTED
    assert result["options"][CONF_LOCAL_MQTT_PASSWORD] == REDACTED


# ---------------------------------------------------------------------------
# devices mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_export_devices_are_labeled_in_sorted_order_and_redacted() -> None:
    """Device ids are replaced with stable, sorted `device_N` labels."""
    coordinator, entry = _diagnostics_rig(
        coordinator_data={
            "dev-b": {PAYLOAD_PROPERTIES: {"soc": 55}},
            "dev-a": {PAYLOAD_PROPERTIES: {FIELD_MAC_ID: "AA:BB:CC", "soc": 42}},
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert set(result["devices"]) == {"device_1", "device_2"}
    # "dev-a" sorts before "dev-b", so it becomes device_1.
    assert result["devices"]["device_1"][PAYLOAD_PROPERTIES]["soc"] == 42
    assert result["devices"]["device_1"][PAYLOAD_PROPERTIES][FIELD_MAC_ID] == REDACTED
    assert result["devices"]["device_2"][PAYLOAD_PROPERTIES]["soc"] == 55


# ---------------------------------------------------------------------------
# raw_api: api response snapshots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_raw_api_login_redacted_and_property_responses_labeled() -> None:
    """login_response secrets are redacted; per-device responses are labeled."""
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
    # "dev-1" sorts before "dev-2".
    assert raw["property_responses"]["property_response_1"]["soc"] == 10
    assert raw["property_responses"]["property_response_2"]["soc"] == 90


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
    assert coordinator_meta["update_interval_seconds"] == 30
    assert coordinator_meta["coordinator_polling"] is True
    assert coordinator_meta["redactions_disabled"] is False


# ---------------------------------------------------------------------------
# raw_api: endpoint backoff / statistics backfill / app chart import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_export_raw_api_includes_active_endpoint_backoff_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An active non-energy backoff window surfaces in the export."""
    from custom_components.jackery_solarvault import coordinator as co

    monkeypatch.setattr(co.time, "monotonic", lambda: 1_000.0)
    coordinator, entry = _diagnostics_rig(
        endpoint_backoff={"device_list": {"until": 1_030.0, "code": 500, "level": 2}},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    backoff = result["raw_api"]["endpoint_backoff"]
    assert backoff["active_count"] == 1
    assert backoff["active"]["device_list"]["code"] == 500


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


# ---------------------------------------------------------------------------
# local_mqtt sub-section
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_bridge_disabled() -> None:
    """Neither local nor third-party bridge enabled yields bridge_disabled."""
    coordinator, entry = _diagnostics_rig(
        options={CONF_LOCAL_MQTT_ENABLE: False},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    local_mqtt = result["raw_api"]["local_mqtt"]
    assert local_mqtt["enabled"] is False
    assert local_mqtt["disabled_reason"] == "bridge_disabled"


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_missing_broker_host() -> None:
    """An enabled bridge with no configured host reports missing_broker_host."""
    coordinator, entry = _diagnostics_rig(
        options={CONF_LOCAL_MQTT_ENABLE: True},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert result["raw_api"]["local_mqtt"]["disabled_reason"] == "missing_broker_host"


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_broad_topic_filter_blocked() -> None:
    """A broker-wide `#` topic filter is blocked even with host/port set."""
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "192.168.1.10",
            CONF_LOCAL_MQTT_TOPIC: "#",
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    local_mqtt = result["raw_api"]["local_mqtt"]
    assert local_mqtt["disabled_reason"] == "broad_topic_filter_blocked"
    # The export's null-normalization pass (_diagnostic_json_null_free) turns
    # the raw `None` computed inside _local_mqtt_diagnostics into "".
    assert local_mqtt["configured_local_mqtt"]["effective_topic_filter"] == ""  # ruff:ignore[compare-to-empty-string]


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_keeps_valid_prefixed_custom_topic() -> None:
    """A non-blocked topic filter already under the Jackery prefix is kept as-is."""
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "192.168.1.10",
            CONF_LOCAL_MQTT_TOPIC: "hb/app/custom",
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    configured = result["raw_api"]["local_mqtt"]["configured_local_mqtt"]
    assert configured["topic_filter"] == "hb/app/custom"
    assert configured["effective_topic_filter"] == "hb/app/custom"


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_keeps_local_device_topic() -> None:
    """Diagnostics must report the same local topic the runtime subscribes to."""
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "192.168.1.10",
            CONF_LOCAL_MQTT_TOPIC: "homeassistant",
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    configured = result["raw_api"]["local_mqtt"]["configured_local_mqtt"]
    assert configured["topic_filter"] == "homeassistant"
    assert configured["effective_topic_filter"] == "homeassistant"


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_client_not_started_with_valid_config() -> None:
    """A fully valid config with no registered client falls back to client_not_started."""
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "192.168.1.10",
        },
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    local_mqtt = result["raw_api"]["local_mqtt"]
    assert local_mqtt["disabled_reason"] == "client_not_started"
    assert local_mqtt["configured_local_mqtt"]["host"] == REDACTED_VALUE
    assert local_mqtt["configured_local_mqtt"]["port"] == REDACTED_VALUE


@pytest.mark.asyncio()
async def test_local_mqtt_ha_integration_mode_from_active_unsubs() -> None:
    """Active HA-MQTT-integration subscriptions report `homeassistant_mqtt_integration`."""
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "192.168.1.10",
        },
        local_mqtt_unsubs={"topic-1": lambda: None, "topic-2": lambda: None},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    local_mqtt = result["raw_api"]["local_mqtt"]
    assert local_mqtt["enabled"] is True
    assert local_mqtt["mode"] == "homeassistant_mqtt_integration"
    assert local_mqtt["subscribed_topic_count"] == 2


@pytest.mark.asyncio()
async def test_local_mqtt_diagnostics_uses_registered_client_snapshot() -> None:
    """A real registered local-MQTT client's own snapshot is used verbatim."""
    client = MagicMock(spec=JackeryLocalMqttClient)
    client.diagnostics_snapshot.return_value = {"messages_received": 7}
    coordinator, entry = _diagnostics_rig(
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "192.168.1.10",
        },
        hass_data={DOMAIN: {_ENTRY_ID: {LOCAL_MQTT_RUNTIME_KEY: client}}},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert result["raw_api"]["local_mqtt"] == {"messages_received": 7}
    client.diagnostics_snapshot.assert_called_once_with(redact=True)


# ---------------------------------------------------------------------------
# redactions-disabled paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_unredacted_via_entry_option_keeps_secrets_cleartext() -> None:
    """`enable_unredacted_diagnostics=True` on the entry disables redaction."""
    coordinator, entry = _diagnostics_rig(
        options={CONF_ENABLE_UNREDACTED_DIAGNOSTICS: True},
        data={FIELD_TOKEN: "cleartext-token"},
    )

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert result["entry_data"][FIELD_TOKEN] == "cleartext-token"
    assert result["raw_api"]["coordinator"]["redactions_disabled"] is True


@pytest.mark.asyncio()
async def test_export_unredacted_via_dev_mode_env_keeps_secrets_in_cleartext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`JACKERY_DEV_MODE=1` disables redaction globally, regardless of entry options."""
    monkeypatch.setattr(util_module, "_DEV_MODE_CACHED", None)
    monkeypatch.setenv("JACKERY_DEV_MODE", "1")
    coordinator, entry = _diagnostics_rig(data={FIELD_TOKEN: "cleartext-token"})

    result = await async_get_config_entry_diagnostics(coordinator.hass, entry)

    assert result["entry_data"][FIELD_TOKEN] == "cleartext-token"
    assert result["raw_api"]["coordinator"]["dev_mode"] is True
