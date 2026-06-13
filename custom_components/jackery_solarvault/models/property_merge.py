"""Property-merge and payload-search helpers extracted from coordinator.

All functions in this module are **pure** - they take explicit arguments and
return results without touching coordinator state.  This makes them safe to
import from ``subdevices/``, ``handlers/``, and ``setters/`` without creating
circular dependencies.

Source: coordinator.py lines 2607-2715 (Phase 1 extraction).
"""

from typing import Any


def merge_dict_values(
    base: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Recursively merge nested dictionaries while preserving old keys."""
    merged: dict[str, Any] = dict(base)
    for key, value in updates.items():
        cur = merged.get(key)
        if isinstance(cur, dict) and isinstance(value, dict):
            merged[key] = merge_dict_values(cur, value)
        else:
            merged[key] = value
    return merged


def sync_property_aliases(
    props: dict[str, Any],
    alias_pairs: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    """Mirror equivalent app property names after merge operations."""
    normalized = dict(props)
    for left, right in alias_pairs:
        if normalized.get(left) is not None and normalized.get(right) is None:
            normalized[right] = normalized[left]
        if normalized.get(right) is not None and normalized.get(left) is None:
            normalized[left] = normalized[right]
    return normalized


def find_dict_with_any_key(
    obj: object,
    keys: set[str] | frozenset[str],
) -> dict[str, Any] | None:
    """Find the first nested dict containing any of the requested keys."""
    if isinstance(obj, dict):
        if any(key in obj for key in keys):
            return obj
        for value in obj.values():
            found = find_dict_with_any_key(value, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_dict_with_any_key(item, keys)
            if found is not None:
                return found
    return None


def find_list_for_key(obj: object, key: str) -> list[dict[str, Any]] | None:
    """Find a nested list of dicts under a key such as batteryPacks."""
    if isinstance(obj, dict):
        value = obj.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        for child in obj.values():
            found = find_list_for_key(child, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_list_for_key(item, key)
            if found is not None:
                return found
    return None


def strip_lifetime_counters(
    source: dict[str, Any],
    lifetime_counter_keys: frozenset[str],
) -> dict[str, Any]:
    """Remove cumulative energy counters before merging live properties."""
    return {
        key: value for key, value in source.items() if key not in lifetime_counter_keys
    }
