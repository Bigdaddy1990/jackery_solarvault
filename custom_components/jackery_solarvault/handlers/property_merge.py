"""Pure payload merge helpers shared by coordinator and transport handlers."""

from typing import Any


def merge_dict_values(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge two dictionaries without mutating either input."""
    merged = dict(base)
    for key, value in updates.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_dict_values(current, value)
        else:
            merged[key] = value
    return merged


def sync_property_aliases(
    values: dict[str, Any],
    alias_pairs: tuple[tuple[str, str], ...] | list[tuple[str, str]],
) -> dict[str, Any]:
    """Copy populated values between equivalent app-property aliases."""
    synced = dict(values)
    for left, right in alias_pairs:
        left_value = synced.get(left)
        right_value = synced.get(right)
        if left_value is None and right_value is not None:
            synced[left] = right_value
        elif right_value is None and left_value is not None:
            synced[right] = left_value
    return synced


def find_dict_with_any_key(obj: object, keys: frozenset[str]) -> dict[str, Any] | None:
    """Return the first nested dict containing any key from ``keys``."""
    if isinstance(obj, dict):
        if any(key in obj for key in keys):
            return obj
        for value in obj.values():
            found = find_dict_with_any_key(value, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_dict_with_any_key(value, keys)
            if found is not None:
                return found
    return None


def find_list_for_key(obj: object, key: str) -> list[Any] | None:
    """Return the first nested list stored under ``key``."""
    if isinstance(obj, dict):
        value = obj.get(key)
        if isinstance(value, list):
            return value
        for nested in obj.values():
            found = find_list_for_key(nested, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for nested in obj:
            found = find_list_for_key(nested, key)
            if found is not None:
                return found
    return None


def strip_lifetime_counters(
    source: dict[str, Any],
    lifetime_counter_keys: frozenset[str],
) -> dict[str, Any]:
    """Return ``source`` without device lifetime counters."""
    return {
        key: value for key, value in source.items() if key not in lifetime_counter_keys
    }


__all__ = [
    "find_dict_with_any_key",
    "find_list_for_key",
    "merge_dict_values",
    "strip_lifetime_counters",
    "sync_property_aliases",
]
