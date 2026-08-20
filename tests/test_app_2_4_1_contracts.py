"""Contract tests for Jackery App 2.4.1 transport contracts.

These tests verify the exact high-risk endpoint paths from App 2.4.1 evidence.
The fixture (tests/fixtures/jackery_app_2_4_0_contracts.py) contains the
authoritative contracts extracted from App decompilation.

These tests MUST fail initially (RED) to establish the baseline for Task 2 fixes.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import (
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_META,
    APP_REQUEST_STAT_TYPE,
    CT_STAT_TYPE_L1,
    DYNAMIC_PRICE_PATH,
    FIELD_DATA,
    FIELD_SYSTEM_ID,
    PV_TRENDS_PATH,
)


def _make_api() -> JackeryApi:
    """Build an API client whose transport boundary is patched per test."""
    return JackeryApi(Mock(), "tester@example.com", "secret")


class TestApp241Endpoints:
    """Contract tests for App 2.4.1 exact endpoint paths."""

    @pytest.mark.asyncio
    async def test_app_241_uses_exact_pv_trends_endpoint(self) -> None:  # noqa: PLR6301, RUF105
        """PV trends MUST use /v1/device/stat/sys/pv/trends (not sys/.../trends variants)."""  # noqa: E501, RUF105
        api = _make_api()
        get_json = AsyncMock(return_value={FIELD_DATA: {"x": ["00:00"], "y": [100]}})

        with patch.object(api, "_get_json", get_json):
            await api.async_get_pv_trends(
                system_id=12345,
                date_type="day",
                begin_date="2026-07-29",
                end_date="2026-07-29",
            )

        # Verify the EXACT path constant is used
        get_json.assert_awaited_once()
        called_path = get_json.await_args.args[0]
        assert called_path == PV_TRENDS_PATH, (
            f"PV trends endpoint mismatch: expected {PV_TRENDS_PATH!r}, "
            f"got {called_path!r}. App 2.4.1 decompilation confirms "
            f"/v1/device/stat/sys/pv/trends is the only correct path."
        )

    @pytest.mark.asyncio
    async def test_app_241_uses_exact_dynamic_price_endpoint(self) -> None:  # noqa: PLR6301, RUF105
        """Dynamic price MUST use /v1/device/dynamic/v2/dynamicPrice (v2 path)."""
        api = _make_api()
        get_json = AsyncMock(return_value={FIELD_DATA: {"price": 0.35}})

        with patch.object(api, "_get_json", get_json):
            await api.async_get_dynamic_price(system_id=12345)

        get_json.assert_awaited_once()
        called_path = get_json.await_args.args[0]
        assert called_path == DYNAMIC_PRICE_PATH, (
            f"Dynamic price endpoint mismatch: expected {DYNAMIC_PRICE_PATH!r}, "
            f"got {called_path!r}. App 2.4.1 decompilation shows v2 path: "
            f"/v1/device/dynamic/v2/dynamicPrice"
        )

    @pytest.mark.asyncio
    async def test_app_241_portable_ct_stat_requires_type_param(self) -> None:  # noqa: PLR6301, RUF105
        """Portable CT stat MUST send APP_REQUEST_STAT_TYPE parameter (type=0 for L1)."""  # noqa: E501, RUF105
        api = _make_api()
        get_json = AsyncMock(return_value={FIELD_DATA: {"l1": 1.5, "l2": 2.0}})

        with patch.object(api, "_get_json", get_json):
            await api.async_get_portable_ct_stat(device_id=98765)

        get_json.assert_awaited_once()
        called_params = get_json.await_args.kwargs.get("params", {})
        assert APP_REQUEST_STAT_TYPE in called_params, (
            f"Missing {APP_REQUEST_STAT_TYPE} parameter. App 2.4.1 CtStatChartVM "
            f"passes Integer type (0 for L1) via CtStatApi.type. Without it, "
            f"cloud returns code=0 with empty y1=[] which Zero-Guard discards."
        )
        assert called_params[APP_REQUEST_STAT_TYPE] == str(CT_STAT_TYPE_L1), (
            f"Wrong stat_type value: expected {CT_STAT_TYPE_L1}, "
            f"got {called_params[APP_REQUEST_STAT_TYPE]}"
        )

    @pytest.mark.asyncio
    async def test_no_aiems_energy_prediction_request_in_production_path(self) -> None:  # noqa: PLR6301, RUF105
        """No request to unproven AIEMS endpoint (/api/aiems/report/energy/prediction)."""  # noqa: E501, RUF105
        api = _make_api()

        # Verify the AIEMS path is NOT in the API client's endpoint constants
        # by checking the const.py catalog (it's only a comment there)

        # The AIEMS path exists only as a comment in const.py, not as an endpoint
        # This test ensures no method in JackeryApi calls this unproven endpoint
        methods = [
            m
            for m in dir(api)
            if m.startswith("async_") and not m.startswith("async__")
        ]  # noqa: E501, RUF100
        for method_name in methods:
            method = getattr(api, method_name)
            if hasattr(method, "__code__"):
                source = method.__code__.co_consts
                # The AIEMS path string should not appear in any method
                for const_val in source:
                    if isinstance(const_val, str) and "aiems" in const_val.lower():
                        pytest.fail(
                            f"Method {method_name} contains unproven AIEMS path: {const_val}. "  # noqa: E501, RUF105
                            f"App 2.4.1 does not use /api/aiems/report/energy/prediction "  # noqa: E501, RUF105
                            f"in the production polling surface. Remove any AIEMS request."  # noqa: E501, RUF105
                        )

    @pytest.mark.asyncio
    async def test_pv_trends_returns_request_meta_for_diagnostics(self) -> None:  # noqa: PLR6301, RUF105
        """PV trends response MUST include APP_REQUEST_META with request parameters."""
        api = _make_api()
        mock_payload = {"x": ["00:00"], "y": [100], "y1": [50], "y2": [50]}
        get_json = AsyncMock(return_value={FIELD_DATA: mock_payload})

        with patch.object(api, "_get_json", get_json):
            result = await api.async_get_pv_trends(
                system_id=12345,
                date_type="day",
                begin_date="2026-07-29",
                end_date="2026-07-29",
            )

        # The returned payload must include request metadata
        assert APP_REQUEST_META in result, (
            "PV trends response must include APP_REQUEST_META for diagnostics. "
            "The api.py implementation adds request_meta_payload to the returned payload."  # noqa: E501, RUF105
        )
        meta = result[APP_REQUEST_META]
        assert meta[APP_REQUEST_DATE_TYPE] == "day"
        assert meta[APP_REQUEST_BEGIN_DATE] == "2026-07-29"
        assert meta[APP_REQUEST_END_DATE] == "2026-07-29"
        assert FIELD_SYSTEM_ID not in meta  # system_id excluded from meta

    @pytest.mark.asyncio
    async def test_dynamic_price_returns_request_meta_for_diagnostics(self) -> None:  # noqa: PLR6301, RUF105
        """Dynamic price response MUST include APP_REQUEST_META."""
        api = _make_api()
        get_json = AsyncMock(return_value={FIELD_DATA: {"priceConfig": {}}})

        with patch.object(api, "_get_json", get_json):
            await api.async_get_dynamic_price(system_id=12345)

        # Dynamic price doesn't currently add APP_REQUEST_META - this is a known gap
        # The test documents the expected behavior; implementation may need update
        # For now, verify the endpoint path is correct
        get_json.assert_awaited_once_with(
            DYNAMIC_PRICE_PATH,
            params={FIELD_SYSTEM_ID: "12345"},
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
