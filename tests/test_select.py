"""Unit tests for Jackery select helpers."""

from dataclasses import dataclass

from custom_components.jackery_solarvault.const import (
    FIELD_COMPANY_NAME,
    FIELD_COUNTRY,
    FIELD_NAME,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_SYSTEM_REGION,
    PAYLOAD_PRICE_SOURCES,
)
from custom_components.jackery_solarvault.select import (
    _price_mode_dynamic_available,  # noqa: PLC2701
    _price_provider_current,  # noqa: PLC2701
    _price_source_label,  # noqa: PLC2701
    _price_source_matches_current,  # noqa: PLC2701
    _price_sources_from_payload,  # noqa: PLC2701
)


@dataclass(slots=True)
class _Entity:
    _price: dict[str, object]
    _payload: dict[str, object]


def test_price_sources_from_payload_filters_invalid_entries() -> None:
    """Only selectable price providers should count as available sources."""
    payload = {
        PAYLOAD_PRICE_SOURCES: [
            {"name": "missing ids"},
            {FIELD_PLATFORM_COMPANY_ID: "", FIELD_COUNTRY: "DE"},
            {FIELD_PLATFORM_COMPANY_ID: "  ", FIELD_COUNTRY: "DE"},
            {FIELD_PLATFORM_COMPANY_ID: "abc", FIELD_COUNTRY: "DE"},
            {FIELD_PLATFORM_COMPANY_ID: "8.9", FIELD_COUNTRY: "DE"},
            {FIELD_PLATFORM_COMPANY_ID: 7, FIELD_COUNTRY: ""},
            {FIELD_PLATFORM_COMPANY_ID: 7, FIELD_COUNTRY: "  "},
            {
                FIELD_PLATFORM_COMPANY_ID: 9,
                FIELD_COUNTRY: "  ",
                FIELD_SYSTEM_REGION: "AT",
            },
            {FIELD_PLATFORM_COMPANY_ID: 8, FIELD_COUNTRY: "DE"},
        ],
    }

    assert _price_sources_from_payload(payload) == [
        {FIELD_PLATFORM_COMPANY_ID: 9, FIELD_COUNTRY: "  ", FIELD_SYSTEM_REGION: "AT"},
        {FIELD_PLATFORM_COMPANY_ID: 8, FIELD_COUNTRY: "DE"},
    ]


def test_price_provider_helpers_normalize_whitespace() -> None:
    """Current provider matching should ignore harmless whitespace."""
    source = {
        FIELD_PLATFORM_COMPANY_ID: "8",
        FIELD_COUNTRY: "DE, AT",
        FIELD_COMPANY_NAME: "Grid Co",
    }
    entity = _Entity(
        _price={FIELD_PLATFORM_COMPANY_ID: " 8 ", FIELD_SYSTEM_REGION: " DE "},
        _payload={PAYLOAD_PRICE_SOURCES: [source]},
    )

    assert (
        _price_source_label({
            FIELD_PLATFORM_COMPANY_ID: " 8.0 ",
            FIELD_COUNTRY: " ",
            FIELD_SYSTEM_REGION: " DE ",
            FIELD_COMPANY_NAME: "Grid Co",
        })
        == "Grid Co (DE) #8"
    )
    assert _price_source_matches_current(source, " 8.0 ", " de ")
    assert _price_mode_dynamic_available(entity)
    assert _price_provider_current(entity) == "Grid Co (DE, AT) #8"


def test_price_source_label_falls_back_from_blank_company_name() -> None:
    """Provider labels should not render blank names."""
    assert (
        _price_source_label({
            FIELD_COMPANY_NAME: "  ",
            FIELD_NAME: "Grid Co",
            FIELD_PLATFORM_COMPANY_ID: "8.0",
            FIELD_COUNTRY: "DE",
        })
        == "Grid Co (DE) #8"
    )


def test_price_provider_current_ignores_blank_company_id() -> None:
    """Invalid provider IDs are not selectable current providers."""
    entity = _Entity(
        _price={FIELD_PLATFORM_COMPANY_ID: "  ", FIELD_SYSTEM_REGION: "DE"},
        _payload={PAYLOAD_PRICE_SOURCES: []},
    )

    assert _price_provider_current(entity) is None
    assert not _price_mode_dynamic_available(entity)

    entity._price[FIELD_PLATFORM_COMPANY_ID] = "abc"
    assert _price_provider_current(entity) is None
    assert not _price_mode_dynamic_available(entity)
