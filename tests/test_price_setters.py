"""Unit tests for coordinator price writer behavior."""

from homeassistant.exceptions import HomeAssistantError
import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_COMPANY_NAME,
    FIELD_COUNTRY,
    FIELD_CURRENCY,
    FIELD_NAME,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_SINGLE_PRICE,
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_REGION,
    PAYLOAD_PRICE,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)


class _RejectingPriceApi:
    async def async_set_single_mode(
        self,
        *,
        system_id: str,
        single_price: float,
        currency: str,
    ) -> bool:
        return False

    async def async_set_dynamic_mode(
        self,
        *,
        system_id: str,
        platform_company_id: int,
        system_region: str,
    ) -> bool:
        return False


class _AcceptingPriceApi:
    def __init__(self) -> None:
        self.dynamic_calls: list[tuple[str, int, str]] = []

    async def async_set_dynamic_mode(
        self,
        *,
        system_id: str,
        platform_company_id: int,
        system_region: str,
    ) -> bool:
        self.dynamic_calls.append((system_id, platform_company_id, system_region))
        return True


def _coordinator() -> JackerySolarVaultCoordinator:
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator.api = _RejectingPriceApi()
    coordinator.data = {
        "dev1": {
            PAYLOAD_PRICE: {
                FIELD_CURRENCY: "EUR",
                FIELD_SINGLE_PRICE: 0.25,
                FIELD_PLATFORM_COMPANY_ID: 7,
                FIELD_SYSTEM_REGION: "DE",
            }
        }
    }
    coordinator._device_index = {"dev1": {FIELD_SYSTEM_ID: "sys1"}}
    coordinator._slow_cache = {}
    coordinator._price_overrides = {}

    def _fail_push(_data: object) -> None:
        raise AssertionError("rejected writer must not patch local price data")

    coordinator._push_partial_update = _fail_push
    return coordinator


async def test_single_price_rejects_false_api_response() -> None:
    """Rejected single-price writes must not update local price state."""
    with pytest.raises(HomeAssistantError, match="single tariff"):
        await _coordinator().async_set_single_price("dev1", 0.30)


async def test_single_price_rejects_invalid_value_before_api_call() -> None:
    """Invalid single-price writes must stop before API/local state mutation."""
    with pytest.raises(HomeAssistantError, match="invalid singlePrice"):
        await _coordinator().async_set_single_price("dev1", float("nan"))


async def test_single_price_mode_rejects_invalid_cached_price() -> None:
    """Switching to single mode must not cast corrupt cached prices directly."""
    coordinator = _coordinator()
    coordinator.data["dev1"][PAYLOAD_PRICE][FIELD_SINGLE_PRICE] = "nan"

    with pytest.raises(HomeAssistantError, match="invalid singlePrice"):
        await coordinator.async_set_price_mode_single("dev1")


async def test_dynamic_price_rejects_false_api_response() -> None:
    """Rejected dynamic-price writes must not update local price state."""
    with pytest.raises(HomeAssistantError, match="dynamic tariff"):
        await _coordinator().async_set_price_mode_dynamic("dev1")


def test_valid_price_sources_filters_blank_company_and_region() -> None:
    """Coordinator price-source validation rejects whitespace-only fields."""
    assert JackerySolarVaultCoordinator._valid_price_sources([
        {FIELD_PLATFORM_COMPANY_ID: "", FIELD_COUNTRY: "DE"},
        {FIELD_PLATFORM_COMPANY_ID: "  ", FIELD_COUNTRY: "DE"},
        {FIELD_PLATFORM_COMPANY_ID: "abc", FIELD_COUNTRY: "DE"},
        {FIELD_PLATFORM_COMPANY_ID: "8.9", FIELD_COUNTRY: "DE"},
        {FIELD_PLATFORM_COMPANY_ID: 7, FIELD_COUNTRY: ""},
        {FIELD_PLATFORM_COMPANY_ID: 7, FIELD_COUNTRY: "  "},
        {FIELD_PLATFORM_COMPANY_ID: 9, FIELD_COUNTRY: "  ", FIELD_SYSTEM_REGION: "AT"},
        {FIELD_PLATFORM_COMPANY_ID: 8, FIELD_COUNTRY: "DE"},
    ]) == [
        {FIELD_PLATFORM_COMPANY_ID: 9, FIELD_COUNTRY: "  ", FIELD_SYSTEM_REGION: "AT"},
        {FIELD_PLATFORM_COMPANY_ID: 8, FIELD_COUNTRY: "DE"},
    ]


def test_find_matching_price_source_normalizes_current_price_fields() -> None:
    """Coordinator provider lookup should ignore harmless whitespace/casing."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator.data = {}

    source = {
        FIELD_PLATFORM_COMPANY_ID: 8,
        FIELD_COUNTRY: "DE, AT",
    }

    assert (
        coordinator._find_matching_price_source(
            "dev1",
            [source],
            {FIELD_PLATFORM_COMPANY_ID: " 8 ", FIELD_SYSTEM_REGION: " de "},
        )
        == source
    )


async def test_dynamic_price_mode_normalizes_current_provider_fields() -> None:
    """Dynamic tariff writes should send normalized provider fields."""
    api = _AcceptingPriceApi()
    coordinator = _coordinator()
    coordinator.api = api
    coordinator.data["dev1"][PAYLOAD_PRICE] = {
        FIELD_PLATFORM_COMPANY_ID: " 8.0 ",
        FIELD_SYSTEM_REGION: " DE ",
    }
    coordinator._push_partial_update = lambda data: setattr(coordinator, "data", data)

    await coordinator.async_set_price_mode_dynamic("dev1")

    assert api.dynamic_calls == [("sys1", 8, "DE")]
    price = coordinator.data["dev1"][PAYLOAD_PRICE]
    assert price[FIELD_PLATFORM_COMPANY_ID] == 8
    assert price[FIELD_SYSTEM_REGION] == "DE"


async def test_price_source_write_normalizes_blank_company_name() -> None:
    """Selected provider metadata should use the first nonblank name."""
    api = _AcceptingPriceApi()
    coordinator = _coordinator()
    coordinator.api = api
    coordinator._push_partial_update = lambda data: setattr(coordinator, "data", data)

    await coordinator.async_set_price_source(
        "dev1",
        {
            FIELD_COMPANY_NAME: " ",
            FIELD_NAME: "Grid Co",
            FIELD_PLATFORM_COMPANY_ID: "8.0",
            FIELD_COUNTRY: "DE",
        },
    )

    price = coordinator.data["dev1"][PAYLOAD_PRICE]
    assert price[FIELD_COMPANY_NAME] == "Grid Co"
