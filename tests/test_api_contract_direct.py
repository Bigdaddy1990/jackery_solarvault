"""Direct API contract regression for ``async_get_symmetry_stat``.

Imports the integration normally (it loads cleanly) and uses pytest-asyncio's
managed event loop. Earlier revisions hand-loaded modules into ``sys.modules``
and called ``asyncio.run()``; that polluted the module table and leaked the
event-loop resolver into later tests, so it was removed.
"""

import asyncio
from typing import Any
from unittest.mock import patch

from custom_components.jackery_solarvault import const
from custom_components.jackery_solarvault.client.api import JackeryApi


async def test_async_get_symmetry_stat_sends_required_direction_flags() -> None:
    """Symmetry stats require positive/negative selector params."""
    api = JackeryApi.__new__(JackeryApi)
    captured: dict[str, Any] = {}
    expected_response = {
        const.FIELD_CODE: 0,
        const.FIELD_DATA: {"totalP": "1.2", "totalN": "0.4"},
    }

    async def _fake_get_json(
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        await asyncio.sleep(0)
        captured["path"] = path
        captured["params"] = dict(params)
        return expected_response

    with patch.object(api, "_get_json", _fake_get_json):
        result = await api.async_get_symmetry_stat(
            device_sn="SN123456",
            date_type=const.DATE_TYPE_DAY,
            begin_date="2026-06-21",
            end_date="2026-06-21",
        )

    assert captured["path"] == const.SYMMETRY_STAT_PATH
    assert captured["params"][const.FIELD_DEVICE_SN] == "SN123456"
    assert captured["params"]["negative"] == "1"
    assert captured["params"]["positive"] == "1"
    assert result == expected_response[const.FIELD_DATA]


async def test_async_get_box_stat_builds_params_and_parses_payload() -> None:
    """device/stat (BoxEleStatApi) — alternate generic electricity endpoint.

    Covers param-building + payload parse so the otherwise-unwired method is not
    dead-uncovered. Its KPIs duplicate the typed period stats, so it is
    intentionally not surfaced as a sensor (see ``async_get_box_stat`` docstring).
    """
    api = JackeryApi.__new__(JackeryApi)
    captured: dict[str, Any] = {}
    expected_response = {
        const.FIELD_CODE: 0,
        const.FIELD_DATA: {
            "total": "12.5",
            "unit": "kWh",
            "x": ["2026-06-21"],
            "y": [12.5],
        },
    }

    async def _fake_get_json(
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        await asyncio.sleep(0)
        captured["path"] = path
        captured["params"] = dict(params)
        return expected_response

    with patch.object(api, "_get_json", _fake_get_json):
        result = await api.async_get_box_stat(
            device_sn="SN123456",
            date_type=const.DATE_TYPE_DAY,
            begin_date="2026-06-21",
            end_date="2026-06-21",
            key="generation",
        )

    assert captured["path"] == const.BOX_STAT_PATH
    assert captured["params"][const.FIELD_DEVICE_SN] == "SN123456"
    assert captured["params"][const.APP_REQUEST_DATE_TYPE] == const.DATE_TYPE_DAY
    assert captured["params"]["key"] == "generation"
    assert result == expected_response[const.FIELD_DATA]


async def test_async_get_box_stat_omits_empty_key() -> None:
    """Empty key (default) must not be sent as a query param."""
    api = JackeryApi.__new__(JackeryApi)
    captured: dict[str, Any] = {}

    async def _fake_get_json(
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        await asyncio.sleep(0)
        captured["params"] = dict(params)
        return {
            const.FIELD_CODE: 0,
            const.FIELD_DATA: {"total": "0", "unit": "kWh"},
        }

    with patch.object(api, "_get_json", _fake_get_json):
        await api.async_get_box_stat(
            device_sn="SN123456",
            date_type=const.DATE_TYPE_DAY,
            begin_date="2026-06-21",
            end_date="2026-06-21",
        )

    assert "key" not in captured["params"]


async def test_async_get_qr_code_gets_path_and_parses_payload() -> None:
    """P3a: share QR code is a no-param GET parsed via ``_payload_dict``."""
    api = JackeryApi.__new__(JackeryApi)
    captured: dict[str, Any] = {}
    expected = {"qrCodeId": "qr-123", "userId": "user-9"}

    async def _fake_get_json(path: str) -> dict[str, Any]:
        await asyncio.sleep(0)
        captured["path"] = path
        return {const.FIELD_CODE: 0, const.FIELD_DATA: expected}

    with patch.object(api, "_get_json", _fake_get_json):
        result = await api.async_get_qr_code()

    assert captured["path"] == const.DEVICE_QR_CODE_PATH
    assert result == expected


async def test_async_get_accessories_builds_params_and_parses_payload() -> None:
    """Orphan cover: accessories GET sends devices/id/parentDeviceId as strings."""
    api = JackeryApi.__new__(JackeryApi)
    captured: dict[str, Any] = {}
    expected = {"items": []}

    async def _fake_get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        captured["path"] = path
        captured["params"] = dict(params)
        return {const.FIELD_CODE: 0, const.FIELD_DATA: expected}

    with patch.object(api, "_get_json", _fake_get_json):
        result = await api.async_get_accessories(
            devices="dev-1,dev-2",
            id=42,
            parent_device_id=7,
        )

    assert captured["path"] == const.ACCESSORIES_PATH
    assert captured["params"]["devices"] == "dev-1,dev-2"
    assert captured["params"]["id"] == "42"
    assert captured["params"]["parentDeviceId"] == "7"
    assert result == expected


async def test_async_check_accessories_exist_builds_params_and_parses() -> None:
    """Orphan cover: accessory existence GET forwards the devices filter."""
    api = JackeryApi.__new__(JackeryApi)
    captured: dict[str, Any] = {}
    expected = {"dev-1": True}

    async def _fake_get_json(path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        captured["path"] = path
        captured["params"] = dict(params)
        return {const.FIELD_CODE: 0, const.FIELD_DATA: expected}

    with patch.object(api, "_get_json", _fake_get_json):
        result = await api.async_check_accessories_exist(devices="dev-1")

    assert captured["path"] == const.ACCESSORIES_EXIST_PATH
    assert captured["params"]["devices"] == "dev-1"
    assert result == expected


async def test_async_check_jackery_accessories_exist_builds_params() -> None:
    """Orphan cover: Jackery accessory existence GET forwards deviceSnInfos."""
    api = JackeryApi.__new__(JackeryApi)
    captured: dict[str, Any] = {}
    expected = {"SN1": False}

    async def _fake_get_json(path: str, *, params: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        captured["path"] = path
        captured["params"] = dict(params)
        return {const.FIELD_CODE: 0, const.FIELD_DATA: expected}

    with patch.object(api, "_get_json", _fake_get_json):
        result = await api.async_check_jackery_accessories_exist(
            device_sn_infos="SN1:type",
        )

    assert captured["path"] == const.ACCESSORIES_JACKERY_EXIST_PATH
    assert captured["params"]["deviceSnInfos"] == "SN1:type"
    assert result == expected
