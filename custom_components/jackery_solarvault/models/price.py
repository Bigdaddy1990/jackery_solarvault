"""Price source helper functions extracted from coordinator.

Pure functions for filtering and parsing energy price source data.
Source: coordinator.py lines 4641-4692 (Phase 2d extraction).
"""

from typing import Any

from ..const import FIELD_COUNTRY, FIELD_PLATFORM_COMPANY_ID, FIELD_SYSTEM_REGION


def valid_price_sources(sources: object) -> list[dict[str, Any]]:
    """Filter a list of price source dicts to valid entries.

    An entry is valid when it has both a ``company_id`` and a ``region``.
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
    """Parse a comma-separated region string into a list of stripped parts."""
    raw = source.get(FIELD_SYSTEM_REGION) or source.get(FIELD_COUNTRY)
    if raw in {None, ""}:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]
