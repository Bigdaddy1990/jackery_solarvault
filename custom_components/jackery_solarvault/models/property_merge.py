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
    """
    Merge two dictionaries recursively, combining nested mappings and applying updates.
    
    Parameters:
        base (dict): Original dictionary whose keys act as defaults.
        updates (dict): Dictionary with values to apply over `base`. If a value and the
            corresponding value in `base` are both dictionaries, they are merged
            recursively; otherwise the value from `updates` replaces the one in `base`.
    
    Returns:
        dict: A new dictionary containing keys from `base` updated by `updates`. Nested
        dictionaries are merged rather than replaced.
    """
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
    """
    Mirror equivalent property names in a properties mapping according to provided alias pairs.
    
    Parameters:
        props (dict[str, Any]): Source properties; not modified.
        alias_pairs (tuple[tuple[str, str], ...]): Iterable of (left, right) alias name pairs. For each pair, if one name exists with a non-`None` value and the other is missing or `None`, the missing name is set to the existing value.
    
    Returns:
        dict[str, Any]: A new dictionary containing the original properties with aliases synchronized.
    """
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
    """
    Locate the first nested dictionary that contains any of the specified keys.
    
    Searches recursively through dictionaries and lists in `obj` and returns the first dictionary encountered that has at least one key from `keys`.
    
    Parameters:
        obj (object): The nested structure (dicts/lists/values) to search.
        keys (set[str] | frozenset[str]): Key names to look for.
    
    Returns:
        dict[str, Any] | None: The first dictionary that contains any of the specified keys, or `None` if no such dictionary exists.
    """
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
    """
    Locate the first nested list of dictionaries found under the specified dictionary key.
    
    Parameters:
        obj (object): Nested structure to search (dicts and lists).
        key (str): Dictionary key name whose associated list to find.
    
    Returns:
        list[dict[str, Any]] | None: The first list containing only dictionary elements found at `key`, or `None` if no such list exists.
    """
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
    """
    Produce a copy of the source dictionary excluding specified lifetime counter keys.
    
    Parameters:
        source (dict[str, Any]): Original property mapping to filter.
        lifetime_counter_keys (frozenset[str]): Keys that represent cumulative lifetime counters to remove.
    
    Returns:
        dict[str, Any]: New dictionary containing entries from `source` whose keys are not in `lifetime_counter_keys`.
    """
    return {
        key: value for key, value in source.items() if key not in lifetime_counter_keys
    }
