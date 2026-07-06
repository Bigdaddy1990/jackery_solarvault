"""Pure payload merge helpers shared by coordinator and transport handlers."""

from typing import Any

_DICT_LIST_ID_KEYS = frozenset({"devId", "deviceId", "id", "idx"})
_DICT_LIST_SERIAL_KEYS = frozenset({"devSn", "deviceSn", "sn"})


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


def merge_present_dict_values(
    base: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Merge sparse live payloads without blanking populated existing values."""
    merged = dict(base)
    for key, value in updates.items():
        current = merged.get(key)
        if _is_blank_value(value) and not _is_blank_value(current):
            continue
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_present_dict_values(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            identified = _merge_identified_dict_lists(current, value)
            merged[key] = value if identified is None else identified
        else:
            merged[key] = value
    return merged


def _is_blank_value(value: object) -> bool:
    """Return whether an update value is too sparse to replace a populated one."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return isinstance(value, (list, dict)) and not value


def _dict_list_identity_values(item: dict[str, Any]) -> frozenset[str]:
    """Return stable identity tokens for an incremental dict-list item."""
    identities: set[str] = set()
    for key in _DICT_LIST_SERIAL_KEYS:
        value = item.get(key)
        if not _is_blank_value(value):
            identities.add(f"serial:{value}")
    for key in _DICT_LIST_ID_KEYS:
        value = item.get(key)
        if not _is_blank_value(value):
            identities.add(f"{key}:{value}")
    return frozenset(identities)


def _clean_dict_list_update(update: dict[str, Any]) -> dict[str, Any]:
    """Drop blank values before appending a new sparse list item."""
    return {key: value for key, value in update.items() if not _is_blank_value(value)}


def _merge_identified_dict_lists(
    current: list[Any],
    updates: list[Any],
) -> list[dict[str, Any]] | None:
    """Merge sparse dict-list updates when every update carries stable identity."""
    if not all(isinstance(item, dict) for item in current):
        return None
    if not all(isinstance(item, dict) for item in updates):
        return None
    typed_updates = [item for item in updates if isinstance(item, dict)]
    update_identities = [_dict_list_identity_values(item) for item in typed_updates]
    if not update_identities or any(not identities for identities in update_identities):
        return None

    merged = [dict(item) for item in current if isinstance(item, dict)]
    for raw_update, identities in zip(typed_updates, update_identities, strict=True):
        target_idx = next(
            (
                idx
                for idx, item in enumerate(merged)
                if identities & _dict_list_identity_values(item)
            ),
            None,
        )
        if target_idx is None:
            cleaned = _clean_dict_list_update(raw_update)
            if cleaned:
                merged.append(cleaned)
            continue
        merged[target_idx] = merge_present_dict_values(
            merged[target_idx],
            raw_update,
        )
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
    "merge_present_dict_values",
    "strip_lifetime_counters",
    "sync_property_aliases",
]
