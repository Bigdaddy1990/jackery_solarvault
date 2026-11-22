"""Track the Box BLE firmware exchange within one session generation."""

from __future__ import annotations

from typing import Any, Literal

from ..const import (
    MQTT_CMD_DEVICE_GET_OTA_PAGE_DATA,
    MQTT_CMD_NOTIFY_DEVICE_CAN_OTA,
    MQTT_CMD_NOTIFY_DEVICE_OTA_TOTAL_PAGE,
)
from .ble import split_box_ota_pages, split_home_ota_pages

_OTA_STATUS = 104
_OTA_SUCCESS = 4


class BoxOtaTransfer:
    """Track the device-requested firmware pages for one BLE session."""

    def __init__(
        self,
        serial: str,
        firmware: dict[str, bytes],
        *,
        mtu: int,
        generation: int,
        family: Literal["box", "home"] = "box",
    ) -> None:
        """Prepare the firmware for the selected BLE session."""
        if (
            not isinstance(serial, str)  # ruff: ignore[too-many-boolean-expressions]
            or not serial.strip()
            or not firmware
            or any(
                not isinstance(name, str) or not name.strip() or "," in name
                for name in firmware
            )
            or isinstance(generation, bool)
            or not isinstance(generation, int)
        ):
            msg = "Invalid Box OTA transfer identity"
            raise ValueError(msg)
        self.serial = serial
        self.firmware = firmware.copy()
        splitter = split_home_ota_pages if family == "home" else split_box_ota_pages
        self.pages = {name: splitter(data, mtu=mtu) for name, data in firmware.items()}
        self.generation = generation
        self.state = "ready"
        self._sequence = 0
        self._pending = False
        self._pending_firmware: str | None = None
        self._announced_firmware: set[str] = set()

    def announcement(self) -> dict[str, str]:
        """Prepare the firmware-type announcement."""
        if self.state != "ready":
            msg = "Box OTA transfer was already started"
            raise ValueError(msg)
        self._pending = True
        self.state = "announcing"
        return {"sn": self.serial, "f": ",".join(self.firmware)}

    def response_sent(self) -> None:
        """Mark the pending response as written."""
        if not self._pending:
            msg = "No Box OTA response is pending"
            raise ValueError(msg)
        if self._pending_firmware is not None:
            self._announced_firmware.add(self._pending_firmware)
            self._pending_firmware = None
        self._pending = False

    def receive(
        self,
        cmd: int,
        body: dict[str, Any],
        *,
        status: int,
        generation: int,
        sequence: int,
    ) -> dict[str, Any] | None:
        """Accept a newer event from the same session."""
        if (
            isinstance(generation, bool)  # ruff: ignore[too-many-boolean-expressions]
            or generation != self.generation
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence <= self._sequence
            or self._pending
            or self.state in {"ready", "failed", "success"}
        ):
            msg = "Invalid Box OTA session or event sequence"
            raise ValueError(msg)
        self._sequence = sequence
        if status != 0:
            self.state = "failed"
            msg = "Box OTA device rejected the exchange"
            raise ValueError(msg)
        if cmd == MQTT_CMD_NOTIFY_DEVICE_CAN_OTA and self.state == "announcing":
            self.state = "transferring"
            return None
        if (
            cmd == MQTT_CMD_NOTIFY_DEVICE_OTA_TOTAL_PAGE
            and self.state == "transferring"
        ):
            firmware_type = body.get("f")
            if not isinstance(firmware_type, str) or firmware_type not in self.pages:
                self.state = "failed"
                msg = "Unknown Box OTA firmware type"
                raise ValueError(msg)
            self._pending = True
            self._pending_firmware = firmware_type
            return {
                "cmd": 102,
                "sn": self.serial,
                "f": firmware_type,
                "p": len(self.pages[firmware_type]),
                "s": len(self.firmware[firmware_type]),
            }
        if cmd == MQTT_CMD_DEVICE_GET_OTA_PAGE_DATA and self.state == "transferring":
            firmware_type = body.get("f")
            page = body.get("p")
            if (
                not isinstance(firmware_type, str)  # ruff: ignore[too-many-boolean-expressions]
                or firmware_type not in self.pages
                or firmware_type not in self._announced_firmware
                or isinstance(page, bool)
                or not isinstance(page, int)
                or not 1 <= page <= len(self.pages[firmware_type])
            ):
                self.state = "failed"
                msg = "Invalid Box OTA page request"
                raise ValueError(msg)
            self._pending = True
            return {
                "cmd": 103,
                "f": firmware_type,
                "p": page,
                "pages": self.pages[firmware_type],
            }
        if cmd == _OTA_STATUS and self.state in {"transferring", "installing"}:
            code = body.get("code")
            if code in (2, 3):  # ruff: ignore[literal-membership]
                self.state = "installing"
            elif code == _OTA_SUCCESS:
                self.state = "success"
            else:
                self.state = "failed"
            return None
        msg = "Unexpected Box OTA event"
        raise ValueError(msg)
