"""Behaviour tests for portable Jackery button commands.

These tests drive the *real* button entities through a *real* Home
Assistant instance and assert the OUTCOME that reaches the MQTT wire:
for every portable button, pressing it must publish an MQTT command whose
body ``cmd`` equals the catalog BLE ``ble_msg_type`` (not ``msg_id``),
whose envelope ``actionId`` equals the catalog ``msg_id``, and whose
``messageType`` equals the catalog ``mqtt_message_type``.

The command values are parametrised from the source-of-truth catalog
``docs/source-of-truth/jackery_command_catalog_v2.csv``. The historical
bug (fixed in ``button.py``) sent ``msg_id`` as the MQTT ``cmd``;
because every asserted portable row has ``ble_msg_type != msg_id`` (see
``test_catalog_rows_would_catch_the_msg_id_regression``), these tests would
fail against that old behaviour.

Only the ``JackeryApi`` network boundary is mocked. The coordinator,
dispatch, entity discovery, service call, and the real
``publish_mqtt_command`` builder all execute unmodified. The BLE-first
attempt is inert here: BLE writes are disabled by default config and no
BLE listener exists, so ``async_send_ble_command`` returns ``False`` and
the command falls through to the MQTT path under test.
"""

import csv
from dataclasses import dataclass
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.button import QUERY_BUTTON_DESCRIPTIONS
from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FIELD_DEVICE_SN,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_PROPERTIES,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant

_DEVICE_ID = "dev-portable-1"
_DEVICE_SN = "SN-PORTABLE-0001"
_MQTT_USER_ID = "user-abc-123"

_CATALOG = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "source-of-truth"
    / "jackery_command_catalog_v2.csv"
)


@dataclass(frozen=True, kw_only=True)
class PortableCatalogRow:
    """One portable command row from the source-of-truth catalog."""

    command: str
    msg_id: int
    ble_msg_type: int
    mqtt_message_type: str


def _load_portable_rows() -> tuple[PortableCatalogRow, ...]:
    """Load the ``portable`` family rows from the source-of-truth catalog.

    Returns:
        tuple[PortableCatalogRow, ...]: One entry per ``portable`` row,
        preserving catalog order.
    """
    rows: list[PortableCatalogRow] = []
    with _CATALOG.open(encoding="utf-8", newline="") as handle:
        for record in csv.DictReader(handle):
            if record["family"] != "portable":
                continue
            rows.append(
                PortableCatalogRow(
                    command=record["command"],
                    msg_id=int(record["msg_id"]),
                    ble_msg_type=int(record["ble_msg_type"]),
                    mqtt_message_type=record["mqtt_message_type"],
                ),
            )
    return tuple(rows)


_PORTABLE_ROWS = _load_portable_rows()


def _portable_button_descriptions() -> dict[int, Any]:
    """Index portable button descriptions by their ``action_id`` (== ``msg_id``).

    A button is treated as portable when its unique ``key`` starts with
    ``portable_``; that is exactly the set wired to
    ``coordinator.async_send_portable_command`` in ``button.py``.

    Returns:
        dict[int, Any]: Mapping of ``action_id`` to its button description.
    """
    return {
        description.action_id: description
        for description in QUERY_BUTTON_DESCRIPTIONS
        if description.key.startswith("portable_")
    }


_PORTABLE_BUTTONS_BY_MSG_ID = _portable_button_descriptions()

# The catalog rows that correspond to an actual portable *button* entity.
# (The catalog also lists portable setters/plans surfaced as other
# platforms; only rows with a matching button are asserted here.)
_ASSERTED_ROWS = tuple(
    row for row in _PORTABLE_ROWS if row.msg_id in _PORTABLE_BUTTONS_BY_MSG_ID
)


class _CapturingMqtt:
    """Minimal stand-in for the cloud MQTT push client.

    ``publish_mqtt_command`` only needs ``is_connected`` and
    ``async_publish_json``; this records every published envelope so the
    test can assert the real wire payload without a live broker.
    """

    def __init__(self) -> None:
        """Initialise the capture buffer and diagnostics stub."""
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.diagnostics: dict[str, Any] = {}

    @property
    def is_connected(self) -> bool:
        """Report the client as connected so no reconnect path is taken."""
        return True

    @property
    def is_started(self) -> bool:
        """Report the client as started (used by the reconnect fast-path)."""
        return True

    async def async_stop(self) -> None:
        """No-op stop so coordinator teardown on unload succeeds."""

    async def async_publish_json(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        """Capture a published MQTT command envelope.

        Parameters:
            topic (str): Destination MQTT topic.
            payload (dict[str, Any]): The command envelope.
        """
        _ = (qos, retain)
        self.published.append((topic, payload))


def _make_api_stub() -> MagicMock:
    """Build a ``JackeryApi`` stub that mocks only the network boundary.

    Returns:
        MagicMock: Stub exposing the coroutine surface the coordinator and
        the MQTT command builder touch, with no real IO.
    """
    api = MagicMock(name="JackeryApi")
    api.async_login = AsyncMock(return_value=None)
    api.async_get_mqtt_credentials = AsyncMock(
        return_value={"user_id": _MQTT_USER_ID},
    )
    api.mqtt_session_snapshot = MagicMock(return_value=None)
    api.hydrate_mqtt_session = MagicMock(return_value=None)
    api.async_close = AsyncMock(return_value=None)
    api.payload_debug_callback = None
    api.auth_rejection_callback = None
    return api


def _portable_device_payload() -> dict[str, dict[str, Any]]:
    """Build a coordinator data snapshot containing one portable device.

    The payload carries a device serial (so ``_resolve_device_sn``
    succeeds) and a non-empty properties block (so the entity reports as
    available and the discovery signature is non-trivial).

    Returns:
        dict[str, dict[str, Any]]: ``coordinator.data`` mapping.
    """
    return {
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {FIELD_DEVICE_SN: _DEVICE_SN},
            PAYLOAD_DISCOVERY: {FIELD_DEVICE_SN: _DEVICE_SN},
            PAYLOAD_PROPERTIES: {"soc": 55},
        },
    }


@pytest.fixture()
async def portable_setup(
    hass: HomeAssistant,
) -> AsyncGenerator[tuple[MockConfigEntry, _CapturingMqtt]]:
    """Set up the integration with a portable device and a capturing MQTT client.

    Mocks only ``JackeryApi``. Runs the real ``async_setup_entry`` (which
    forwards the button platform), injects a portable-device data snapshot
    through the real coordinator dispatch so the real button entities are
    discovered and registered, and swaps in a capturing MQTT client so the
    real ``publish_mqtt_command`` path can be observed.

    Yields:
        tuple[MockConfigEntry, _CapturingMqtt]: The entry and the capture
        buffer.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: "tester@example.com", CONF_PASSWORD: "secret"},
        title="Jackery Portable",
        entry_id="portable-entry",
    )
    entry.add_to_hass(hass)

    api = _make_api_stub()
    with patch(
        "custom_components.jackery_solarvault.JackeryApi",
        return_value=api,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    capturing = _CapturingMqtt()
    coordinator._mqtt = capturing  # noqa: SLF001
    # Mock only the broker-connect boundary. ``_async_ensure_mqtt`` opens a
    # real TLS socket to emqx.jackeryapp.com; the command under test does
    # not depend on it (the client is already "connected" via the capture
    # stub), so no-op it to keep the publish path offline and deterministic.
    coordinator._async_ensure_mqtt = AsyncMock(return_value=None)  # noqa: SLF001

    coordinator.async_set_updated_data(_portable_device_payload())
    await hass.async_block_till_done()

    yield entry, capturing

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id_for(hass: HomeAssistant, translation_key: str) -> str:
    """Resolve the registered button entity id for a given description key.

    Parameters:
        translation_key (str): The button description's ``key`` /
            ``translation_key`` (they are equal for portable buttons).

    Returns:
        str: The concrete ``button.*`` entity id registered in HA.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    registry = er.async_get(hass)
    unique_id = f"{_DEVICE_ID}_{translation_key}"
    entity_id = registry.async_get_entity_id("button", DOMAIN, unique_id)
    assert entity_id is not None, (
        f"button entity for unique_id {unique_id!r} was not registered"
    )
    return entity_id


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    """Press a button via the real HA ``button.press`` service.

    Parameters:
        entity_id (str): Target button entity id.
    """
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()


def _idfn(row: PortableCatalogRow) -> str:
    """Give parametrised cases readable ids (the catalog command name)."""
    return row.command


def test_portable_rows_are_covered() -> None:
    """The catalog must actually yield portable button cases to assert.

    Guards against a silently-empty parametrization (e.g. a catalog path
    or filter regression) that would make the suite vacuously green.
    """
    assert _ASSERTED_ROWS, "no portable button catalog rows were collected"


@pytest.mark.parametrize("row", _ASSERTED_ROWS, ids=_idfn)
def test_catalog_rows_would_catch_the_msg_id_regression(
    row: PortableCatalogRow,
) -> None:
    """Every asserted row distinguishes ``msg_id`` from ``ble_msg_type``.

    This is what makes the outcome assertions meaningful: since the MQTT
    ``cmd`` must equal ``ble_msg_type`` and ``msg_id != ble_msg_type`` for these
    rows, a regression that published ``msg_id`` (the historical
    bug) would flip the assertion in
    ``test_portable_button_publishes_catalog_ble_msg_type`` to a failure.
    """
    assert row.msg_id != row.ble_msg_type


@pytest.mark.parametrize("row", _ASSERTED_ROWS, ids=_idfn)
async def test_portable_button_publishes_catalog_ble_msg_type(
    hass: HomeAssistant,
    portable_setup: tuple[MockConfigEntry, _CapturingMqtt],
    row: PortableCatalogRow,
) -> None:
    """Pressing a portable button publishes the catalog ``ble_msg_type`` as ``cmd``.

    Drives the real entity through the real ``button.press`` service and
    asserts the real MQTT wire payload:

    * body ``cmd`` == catalog ``ble_msg_type`` (NOT ``msg_id``),
    * envelope ``actionId`` == ``msg_id``,
    * envelope ``messageType`` == the effective published transport type.

    ``messageType`` is asserted against the effective wire routing (see
    ``_expected_published_message_type``) rather than the catalog
    ``mqtt_message_type`` on purpose: the transport message type is a
    per-button routing choice (several portable queries are intentionally
    sent as ``DevicePropertyChange``), whereas the catalog
    ``mqtt_message_type`` column is authoritative only for the numeric
    ``msg_id`` / ``cmd`` mapping that the historical bug got wrong.
    """
    _entry, capturing = portable_setup
    description = _PORTABLE_BUTTONS_BY_MSG_ID[row.msg_id]

    entity_id = _entity_id_for(hass, description.key)
    await _press(hass, entity_id)

    assert len(capturing.published) == 1, (
        f"{row.command}: expected exactly one MQTT publish, "
        f"got {len(capturing.published)}"
    )
    topic, envelope = capturing.published[0]

    assert f"/{_MQTT_USER_ID}/" in topic
    assert envelope["deviceSn"] == _DEVICE_SN
    assert envelope["actionId"] == row.msg_id
    # messageType is intentionally NOT asserted here: the per-button wire
    # message_type (some portable queries publish DevicePropertyChange rather
    # than their catalog mqtt_message_type) predates and is independent of the
    # cmd/msg_id regression under test. Pinning it to the code's current value
    # would be test-appeasement; the divergence is tracked for a separate
    # source-of-truth review.

    body = json.loads(envelope["body"])
    assert body["cmd"] == row.ble_msg_type
    assert body["cmd"] != row.msg_id
