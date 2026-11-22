"""Persist per-device BLE firmware transfer interlocks."""

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import CoreState
from homeassistant.helpers.storage import Store

from ..const import DOMAIN
from ..util import get_store_lock

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    return Store(hass, 1, f"{DOMAIN}/ble_ota_pending_{entry_id}")


def _is_stopping(hass: HomeAssistant) -> bool:
    return hass.state in {CoreState.stopping, CoreState.final_write, CoreState.stopped}


def _validate_disk_file(path: str) -> None:
    """Reject damaged state without allowing HA to rename or ignore it."""
    target = Path(path)
    if target.parent.exists() and any(target.parent.glob(f"{target.name}.corrupt.*")):
        msg = "A corrupted BLE OTA pending state requires repair"
        raise ValueError(msg)
    try:
        size = target.stat().st_size
    except FileNotFoundError:
        return
    except OSError as err:
        msg = "BLE OTA pending state is unreadable"
        raise ValueError(msg) from err
    try:
        if size > 1024 * 1024:
            msg = "BLE OTA pending state exceeds its size limit"
            raise ValueError(msg)
        with target.open(encoding="utf-8") as file:
            envelope = json.load(file)
    except (OSError, UnicodeError, json.JSONDecodeError) as err:
        msg = "BLE OTA pending state is unreadable"
        raise ValueError(msg) from err
    if (
        not isinstance(envelope, dict)  # ruff: ignore[too-many-boolean-expressions]
        or type(envelope.get("version")) is not int
        or envelope["version"] != 1
        or type(envelope.get("minor_version", 1)) is not int
        or envelope.get("minor_version", 1) != 1
        or not isinstance(envelope.get("data"), dict)
    ):
        msg = "BLE OTA pending state has an invalid storage envelope"
        raise ValueError(msg)
    _devices(envelope["data"])


def _devices(data: object) -> dict[str, dict[str, str]]:
    if data is None:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("devices"), dict):
        msg = "Invalid BLE OTA pending state"
        raise ValueError(msg)  # ruff: ignore[type-check-without-type-error]
    devices = data["devices"]
    if any(
        not isinstance(device, str)
        or not device
        or not isinstance(marker, dict)
        or not marker
        or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in marker.items()
        )
        for device, marker in devices.items()
    ):
        msg = "Invalid BLE OTA pending device entry"
        raise ValueError(msg)
    return {device: dict(marker) for device, marker in devices.items()}


async def async_load_pending(
    hass: HomeAssistant, entry_id: str
) -> dict[str, dict[str, str]]:
    """Load the entry's pending transfers without accepting malformed state."""
    async with get_store_lock(hass, f"ble_ota_pending_{entry_id}.lock"):
        store = _store(hass, entry_id)
        await hass.async_add_executor_job(_validate_disk_file, store.path)
        return _devices(await store.async_load())


async def async_set_pending(
    hass: HomeAssistant,
    entry_id: str,
    device_id: str,
    marker: dict[str, str] | None,
) -> None:
    """Atomically update one device while retaining other pending transfers."""
    if not device_id or (marker is not None and not marker):
        msg = "Invalid BLE OTA pending update"
        raise ValueError(msg)
    if _is_stopping(hass):
        msg = "Cannot persist BLE OTA state while Home Assistant is stopping"
        raise RuntimeError(msg)
    detached = dict(marker) if marker is not None else None

    async def persist() -> None:
        async with get_store_lock(hass, f"ble_ota_pending_{entry_id}.lock"):
            store = _store(hass, entry_id)
            await hass.async_add_executor_job(_validate_disk_file, store.path)
            devices = _devices(await store.async_load())
            if detached is None:
                devices.pop(device_id, None)
            else:
                devices[device_id] = detached
            _devices({"devices": devices})
            if _is_stopping(hass):
                msg = "Cannot persist BLE OTA state while Home Assistant is stopping"
                raise RuntimeError(msg)
            await store.async_save({"devices": devices})
            if _is_stopping(hass):
                msg = "BLE OTA state was not confirmed before shutdown"
                raise RuntimeError(msg)
            if _devices(await _store(hass, entry_id).async_load()) != devices:
                msg = "BLE OTA pending state could not be verified"
                raise OSError(msg)

    task = hass.async_create_task(persist(), eager_start=False)
    await asyncio.shield(task)
