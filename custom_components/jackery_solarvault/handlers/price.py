"""Price source helper functions extracted from coordinator.

Pure functions for filtering and parsing energy price source data.
Source: coordinator.py lines 4641-4692 (Phase 2d extraction).
"""

import logging
from typing import TYPE_CHECKING, Any

from ..const import (  # noqa: RUF100, TID252
    FIELD_COUNTRY,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_SYSTEM_REGION,
)
from ..util import WHOLE_INT_TEXT_RE  # noqa: RUF100, TID252

if TYPE_CHECKING:
    from ..coordinator import JackerySolarVaultCoordinator  # noqa: RUF100, TID252

_LOGGER = logging.getLogger(__name__)


async def call(
    coordinator: JackerySolarVaultCoordinator,
    method: str,
    *args: Any,  # ruff:ignore[any-type]
    **kwargs: Any,  # ruff:ignore[any-type]
) -> object:
    """Call a characterized coordinator setter by name."""
    return await getattr(coordinator, method)(*args, **kwargs)


def normalized_company_id(value: object) -> int | None:
    """Return a provider ID when the app payload encodes a whole number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text or not WHOLE_INT_TEXT_RE.fullmatch(text):
        return None
    try:
        return int(float(text))
    except ValueError as err:
        _LOGGER.debug("Discarding non-numeric provider ID %r: %s", text, err)
        return None


def normalized_region(value: object) -> str | None:
    """Return a normalized electricity-price region token."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def normalized_source_regions(source: dict[str, Any]) -> list[str]:
    """Return normalized region tokens for a price provider source."""
    regions: list[str] = []
    for region in source_regions(source):
        normalized = normalized_region(region)
        if normalized is not None and normalized not in regions:
            regions.append(normalized)
    return regions


def first_nonblank_source_name(source: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty provider display-name field."""
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


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
        company_id = normalized_company_id(item.get(FIELD_PLATFORM_COMPANY_ID))
        if company_id is None or not normalized_source_regions(item):
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
