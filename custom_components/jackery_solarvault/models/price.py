"""Price source helper functions extracted from coordinator.

Pure functions for filtering and parsing energy price source data.
Source: coordinator.py lines 4641-4692 (Phase 2d extraction).
"""

from typing import Any

from custom_components.jackery_solarvault.const import (
    FIELD_COUNTRY,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_SYSTEM_REGION,
)


def valid_price_sources(sources: object) -> list[dict[str, Any]]:
    """Filter a collection to dictionaries that contain a platform company ID and a.

    region.

    Parameters:
        sources (object): The value to filter; expected to be a list of dictionaries.
        Non-list inputs result in an empty list.

    Returns:
        list[dict[str, Any]]: The subset of input dictionaries that include a non-empty
        platform company ID and a non-empty region field.
    """
    if not isinstance(sources, list):
        return []
    valid: list[dict[str, Any]] = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        company_id = item.get(FIELD_PLATFORM_COMPANY_ID)
        region = item.get(FIELD_COUNTRY) or item.get(FIELD_SYSTEM_REGION)
        if company_id in {None, ""} or not region:
            continue
        valid.append(item)
    return valid


def source_regions(source: dict[str, Any]) -> list[str]:
    """Extracts and normalizes region tokens from a price source's region field.

    Reads the value from FIELD_SYSTEM_REGION or, if absent/empty, FIELD_COUNTRY, splits
    a comma-separated string into parts, trims whitespace, and returns only non-empty
    tokens.

    Parameters:
        source (dict[str, Any]): Mapping representing a price source; the function
        looks up `FIELD_SYSTEM_REGION` then `FIELD_COUNTRY` for the raw region value.

    Returns:
        list[str]: A list of trimmed, non-empty region strings.
    """
    raw = source.get(FIELD_SYSTEM_REGION) or source.get(FIELD_COUNTRY)
    if raw in {None, ""}:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]
