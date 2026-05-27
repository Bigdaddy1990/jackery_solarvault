"""Jackery SolarVault BLE wire-format helpers.

Pure-Python frame builder, parser and crypto for the Jackery app's BLE
protocol, reverse-engineered from the official Android app smali:

- ``com.hbxn.control.device.cmd.home.HomeControlFormat`` — frame builder
- ``com.hbxn.control.device.cmd.home.HomeCmdAction`` — actionId/cmd table
- ``bb.a``, ``bb.c`` — AES-256-CBC-PKCS7 wrapper used on each frame
- ``sb.b`` — CRC-16 checksum helper
- ``sb.d`` — 16-bit big-endian hex encode/decode helpers

No I/O, no Home Assistant or bleak imports. Everything in this module is
fully unit-testable and can be exercised against a captured frame from
nRF Connect / Wireshark before being wired into the live integration.

The matching GATT plumbing (BLE scan, GATT connect, MTU negotiation,
chunked write to char ``0xEE01``, subscribe to notify on char ``0xEE02``)
belongs in a separate module that depends on Home Assistant's
``bluetooth`` integration plus ``bleak-retry-connector``; that module
imports the helpers here.

Wire format (from ``HomeControlFormat.smali`` line 420-587):

    plaintext_hex_frame =
        "DFED"           # 2-byte magic
        "0001"           # 2-byte protocol version
        <FRAME_IDX>      # 16-bit big-endian, hex-encoded (4 chars)
        <CHUNK_CNT>      # 16-bit big-endian, hex-encoded (4 chars)
        <ACTION_ID>      # 16-bit big-endian, hex-encoded (4 chars)
        <BLE_CMD>        # 16-bit big-endian, hex-encoded (4 chars)
        "0001"           # 2-byte payload-type marker (JSON chunk)
        <CHUNK_LEN>      # 16-bit big-endian, hex-encoded (4 chars) — byte count of <CHUNK_HEX>/2
        <CHUNK_HEX>      # hex-encoded payload bytes

    crc_suffix = sb_b_crc16(plaintext_hex_frame)     # 4-char hex (16-bit)
    random_suffix = sb_d_d(random_int)               # 4-char hex (16-bit)
    plaintext_string = plaintext_hex_frame + random_suffix + crc_suffix
    ciphertext = AES_256_CBC_PKCS7(utf8(plaintext_string), key, random_iv)
    wire_payload = base64(random_iv + ciphertext)    # actually written to GATT

The plaintext payload (chunk) is itself an arbitrary byte stream — for
``DevicePropertyChange``/``ControlCombine`` setters it is the JSON body
serialized as bytes, then hex-encoded. For chunked transmission a single
logical message is split into ``CHUNK_CNT`` frames, each carrying
``CHUNK_LEN`` bytes of the payload; the receiver reassembles by
``FRAME_IDX``.

PROTOCOL.md §14 documents the AES key source: per-device ``bluetoothKey``
from the HTTP ``/v1/device/system/list`` response. The base64-decoded
length picks the cipher mode — observed in the wild: a SolarVault 3 Pro
Max returned a 16-byte key (AES-128). The crypto helpers below accept
both 16-byte (AES-128) and 32-byte (AES-256) keys to stay compatible
with whatever the device hands out. See ``coordinator.device_bluetooth_key()``.
"""

from dataclasses import dataclass
import logging
import os
import secrets

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Wire-format constants
# ---------------------------------------------------------------------------

#: Magic prefix that every plaintext frame starts with.
BLE_FRAME_MAGIC: str = "DFED"

#: Protocol version following the magic. Constant in the app's
#: ``BLE_SEND_DATA_FORMAT_HEX = "DFED0001%s%s%s%s0001%s%s"``.
BLE_FRAME_VERSION: str = "0001"

#: Payload-type marker between the header block and the chunk length.
BLE_FRAME_PAYLOAD_MARKER: str = "0001"

#: Length in hex characters of every fixed-width 16-bit field.
_HEX16_WIDTH: int = 4

#: Key lengths (in bytes) accepted by the BLE crypto helpers.
#:
#: PROTOCOL.md §14 originally documented a fixed 32-byte AES-256 key, but the
#: live ``/v1/device/system/list`` capture from a SolarVault 3 Pro Max
#: returned a 16-byte key (``base64.b64decode("aHIyYzBoaDM2MTMzNjEzOA==")``
#: → ``hr2c0hh361336138``). The Jackery app's smali ``bb/a`` accepts either
#: width because ``Cipher.getInstance("AES/CBC/PKCS7Padding")`` selects
#: AES-128 or AES-256 from the key length implicitly. Both are listed here
#: so callers can pick the right one without hard-coding either.
BLE_AES_KEY_LEN_AES128: int = 16
BLE_AES_KEY_LEN_AES256: int = 32

#: Tuple of accepted key lengths, used for input validation.
BLE_AES_KEY_LENGTHS: tuple[int, ...] = (
    BLE_AES_KEY_LEN_AES128,
    BLE_AES_KEY_LEN_AES256,
)

# Backwards-compatible alias kept until call sites migrate; new code should
# branch on the actual key length the device returns.
BLE_AES_KEY_LEN: int = BLE_AES_KEY_LEN_AES128

#: AES-CBC IV length in bytes.
BLE_AES_IV_LEN: int = 16

#: GATT service UUID advertised by the SolarVault BLE radio.
BLE_SERVICE_UUID: str = "0000bdee-0000-1000-8000-00805f9b34fb"

#: Write-without-response characteristic (app -> device).
BLE_WRITE_CHAR_UUID: str = "0000ee01-0000-1000-8000-00805f9b34fb"

#: Notify characteristic (device -> app); needs CCCD ``0x2902`` enabled.
BLE_NOTIFY_CHAR_UUID: str = "0000ee02-0000-1000-8000-00805f9b34fb"

#: Bluetooth SIG company identifier under which the SolarVault advertises
#: its serial number in the manufacturer-data field.
BLE_MANUFACTURER_ID: int = 0x4802  # 18434 decimal — confirmed via live scan


# ---------------------------------------------------------------------------
# Low-level hex helpers (mirror ``sb.d.d`` in the app)
# ---------------------------------------------------------------------------


def hex16(value: int) -> str:
    """Encode a 16-bit unsigned int as a 4-character upper-case hex string.

    Mirrors ``sb/d.d(I) -> String`` in the app smali. Raises ``ValueError``
    if the value does not fit into 16 bits — the caller is responsible for
    range-checking inputs (e.g. ``CHUNK_LEN <= (MTU - 60)``).
    """
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"hex16: {value} does not fit into 16 bits")
    return f"{value:04X}"


def parse_hex16(text: str) -> int:
    """Parse a 4-character hex string back to an int. Inverse of :func:`hex16`."""
    if len(text) != _HEX16_WIDTH:
        raise ValueError(
            f"parse_hex16: expected {_HEX16_WIDTH} hex chars, got {len(text)}"
        )
    return int(text, 16)


def hex_encode(data: bytes) -> str:
    """Upper-case hex encode a byte string (no separators, no prefix)."""
    return data.hex().upper()


def hex_decode(text: str) -> bytes:
    """Decode an upper- or lower-case hex string back to bytes."""
    return bytes.fromhex(text)


# ---------------------------------------------------------------------------
# CRC-16 (mirrors ``sb.b.b`` in the app)
# ---------------------------------------------------------------------------
#
# The smali implementation in ``sb/b.b([B) String`` is a textbook Modbus
# CRC-16 (polynomial 0xA001, reflected): for each input byte XOR into the
# low byte of the accumulator, then shift right 8 times, XORing with
# 0xA001 whenever a 1 is shifted out. Result is a 16-bit value returned
# as a 4-character hex string (smali uses ``Integer.toHexString`` and
# left-pads with zeros — line 219, 245).


def crc16_modbus(data: bytes) -> int:
    """Modbus CRC-16 (poly 0xA001, init 0xFFFF, reflected) of *data*."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def crc16_hex(data: bytes) -> str:
    """Return the Modbus CRC-16 of *data* as a 4-character upper-case hex string."""
    return hex16(crc16_modbus(data))


# ---------------------------------------------------------------------------
# AES-256-CBC-PKCS7 helpers (mirror ``bb.a`` for SolarVault)
# ---------------------------------------------------------------------------


def _validate_key_len(key: bytes, *, fn: str) -> None:
    """Reject keys whose length does not match AES-128 or AES-256."""
    if len(key) not in BLE_AES_KEY_LENGTHS:
        raise ValueError(
            f"{fn}: key must be one of {BLE_AES_KEY_LENGTHS} bytes "
            f"(got {len(key)} bytes)"
        )


def aes_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-CBC-PKCS7 encrypt *plaintext* using *key* and *iv*.

    The key length picks the cipher: 16 bytes → AES-128, 32 bytes →
    AES-256. The Jackery app's smali ``bb/a`` uses the same dynamic
    selection via ``Cipher.getInstance("AES/CBC/PKCS7Padding")``.

    Caller controls the IV so unit tests can pin a known value. In live
    use the IV is generated per-frame via :func:`random_iv`.
    """
    _validate_key_len(key, fn="aes_encrypt")
    if len(iv) != BLE_AES_IV_LEN:
        raise ValueError(f"aes_encrypt: iv must be {BLE_AES_IV_LEN} bytes")
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def aes_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-CBC-PKCS7 decrypt *ciphertext* using *key* and *iv*.

    Accepts both 16-byte (AES-128) and 32-byte (AES-256) keys; see
    :func:`aes_encrypt` for the rationale.
    """
    _validate_key_len(key, fn="aes_decrypt")
    if len(iv) != BLE_AES_IV_LEN:
        raise ValueError(f"aes_decrypt: iv must be {BLE_AES_IV_LEN} bytes")
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def random_iv() -> bytes:
    """Generate a fresh 16-byte CBC IV using a cryptographic RNG."""
    return secrets.token_bytes(BLE_AES_IV_LEN)


# ---------------------------------------------------------------------------
# Observed binary frame layout (device → app notify on char 0xEE02)
# ---------------------------------------------------------------------------
#
# Live capture 2026-05-16 against a SolarVault 3 Pro Max (via an ESPHome BLE
# proxy) gave the *real* wire format. The smali-reconstructed layout in
# ``build_plaintext_frame`` / ``parse_plaintext_frame`` above stores all
# header fields as ASCII hex; what actually goes over the air is the same
# logical fields but **packed as big-endian binary** inside the encrypted
# payload. The 16-byte IV is plaintext-prefixed to every frame.
#
# Wire structure (after :func:`aes_decrypt`):
#
#   bytes 0..1   magic  = 0xDFED
#   bytes 2..3   0x0064 (constant in every observed frame — possibly
#                       a protocol-version / framing-version marker)
#   bytes 4..5   frame_index  (big-endian uint16, 1-based)
#   bytes 6..7   chunk_count  (big-endian uint16)
#   bytes 8..9   flags / actionId hint (purpose not yet pinned; the
#                       decoded JSON also carries ``cmd``, so this is
#                       redundant for routing)
#   bytes 10..11 ble_cmd      (big-endian uint16; matches MQTT cmd:
#                       107 = DevicePropertyChange, 121 = ControlCombine,
#                       110 = QuerySubDeviceGroupProperty, etc.)
#   bytes 12..13 payload-type marker = 0x0001
#   bytes 14..15 body_length  (big-endian uint16, bytes in ``body``)
#   bytes 16..16+body_length   body — JSON for SolarVault telemetry
#   final 4 bytes              trailer (probable CRC-32; not yet
#                              re-derived from any documented helper —
#                              treated as opaque on decode for now)

_BINARY_FRAME_HEADER_LEN: int = 16
_BINARY_FRAME_TRAILER_LEN: int = 4
_BINARY_FRAME_MAGIC_BE: bytes = b"\xdf\xed"
_BINARY_FRAME_VERSION_BE: bytes = b"\x00\x64"
_BINARY_FRAME_PAYLOAD_MARKER_BE: bytes = b"\x00\x01"


@dataclass(frozen=True, slots=True)
class BleBinaryFrame:
    """Decoded view of a notify frame in the live BLE wire format.

    Only ``frame_index``, ``chunk_count``, ``cmd`` and ``body`` are reliably
    populated. ``flags`` is captured raw for future analysis; ``trailer``
    is the 4 bytes after the body — purpose currently unknown.

    The trailer is opaque: it did not match any of the standard CRC-16/32
    variants (Modbus, zlib, MPEG-2, JAMCRC, BZIP2, ISCSI, Kermit, X.25,
    ARC, Autosar, Posix, IEC, etc.) over any tested input range (body,
    header+body, IV+body, IV+header+body), nor MD5/SHA-1/SHA-256/AES-CMAC
    truncations with the ``bluetoothKey`` as the MAC key. AES-CBC-PKCS7
    already provides decode-time integrity, so the read path can safely
    leave the trailer untouched. The write path (Phase 3b) will need
    either a Frida-derived algorithm or a black-box probe of what the
    device firmware actually validates.
    """

    frame_index: int
    chunk_count: int
    flags: int
    cmd: int
    body: bytes
    trailer: bytes


# ---------------------------------------------------------------------------
# Outbound frame builder (Phase 3b, write to char 0xEE01)
# ---------------------------------------------------------------------------
#
# Mirror image of :func:`decrypt_binary_notify`: serialise the same binary
# layout, PKCS7-pad and AES-CBC-encrypt, then prefix the IV so the device
# can recover the original plaintext with its own key.
#
# Open questions captured in :class:`BleBinaryFrame` apply: the trailer
# algorithm is unknown, so this builder accepts an explicit ``trailer``
# parameter that defaults to four NUL bytes. The right path forward is
# to push a no-op command (e.g. ``QueryDeviceProperty``) at the device and
# watch the notify stream: if the device responds without the listener
# logging a malformed frame, the trailer is not strictly validated and
# zero is acceptable. Otherwise the trailer will need to be reversed
# from the captured request frames.


def build_binary_frame(
    *,
    cmd: int,
    body: bytes,
    flags: int = 0,
    frame_index: int = 1,
    chunk_count: int = 1,
    trailer: bytes = b"\x00\x00\x00\x00",
) -> bytes:
    """Serialise the binary header + body + trailer for the wire (no AES).

    Returns the **plaintext** that goes into :func:`encrypt_binary_notify`.
    The default ``trailer`` of four zero bytes is a placeholder until the
    firmware-side checksum algorithm is identified.
    """
    if not 0 <= cmd <= 0xFFFF:
        raise ValueError(f"cmd {cmd} does not fit into 16 bits")
    if not 0 <= flags <= 0xFFFF:
        raise ValueError(f"flags {flags} does not fit into 16 bits")
    if not 1 <= frame_index <= 0xFFFF:
        raise ValueError(f"frame_index {frame_index} out of range")
    if not 1 <= chunk_count <= 0xFFFF:
        raise ValueError(f"chunk_count {chunk_count} out of range")
    if len(body) > 0xFFFF:
        raise ValueError(f"body too long: {len(body)} bytes")
    if len(trailer) != _BINARY_FRAME_TRAILER_LEN:
        raise ValueError(
            f"trailer must be {_BINARY_FRAME_TRAILER_LEN} bytes, got {len(trailer)}"
        )
    header = (
        _BINARY_FRAME_MAGIC_BE
        + _BINARY_FRAME_VERSION_BE
        + frame_index.to_bytes(2, "big")
        + chunk_count.to_bytes(2, "big")
        + flags.to_bytes(2, "big")
        + cmd.to_bytes(2, "big")
        + _BINARY_FRAME_PAYLOAD_MARKER_BE
        + len(body).to_bytes(2, "big")
    )
    assert len(header) == _BINARY_FRAME_HEADER_LEN
    return header + body + trailer


def encrypt_binary_notify(
    plaintext_frame: bytes,
    key: bytes,
    *,
    iv: bytes | None = None,
) -> bytes:
    """AES-CBC-PKCS7 encrypt *plaintext_frame* and return ``iv || ciphertext``.

    The output is exactly the byte string that :func:`decrypt_binary_notify`
    accepts on the read path. When *iv* is omitted, a cryptographically
    random one is generated; pass an explicit value for tests or to mimic
    the device's observed ASCII-counter IVs.
    """
    actual_iv = random_iv() if iv is None else iv
    if len(actual_iv) != BLE_AES_IV_LEN:
        raise ValueError(f"iv must be {BLE_AES_IV_LEN} bytes")
    ciphertext = aes_encrypt(plaintext_frame, key, actual_iv)
    return actual_iv + ciphertext


def decrypt_binary_notify(raw: bytes, key: bytes) -> BleBinaryFrame:
    r"""Decrypt a raw 0xEE02 notify payload and parse the binary header.

    The notify characteristic delivers ``iv (16) || ciphertext`` per frame
    (verified against the 2026-05-16 live capture). The IV in those frames
    is an ASCII numeric counter padded with one ``\x00`` to align to 16
    bytes — the integration treats the IV as opaque bytes and feeds it
    straight to AES-CBC, so future firmware that changes the counter
    encoding still works.

    Raises ``ValueError`` for any structural mismatch (magic, length,
    truncated trailer) so the listener's defensive logging can record the
    full raw bytes for offline analysis.
    """
    if len(raw) < BLE_AES_IV_LEN + _BINARY_FRAME_HEADER_LEN + _BINARY_FRAME_TRAILER_LEN:
        raise ValueError(f"notify too short: {len(raw)} bytes")
    iv = raw[:BLE_AES_IV_LEN]
    ciphertext = raw[BLE_AES_IV_LEN:]
    if len(ciphertext) % 16 != 0:
        raise ValueError(
            f"ciphertext is not aligned to AES block size: {len(ciphertext)} bytes"
        )
    plaintext = aes_decrypt(ciphertext, key, iv)
    if not plaintext.startswith(_BINARY_FRAME_MAGIC_BE):
        raise ValueError(
            f"plaintext does not start with DFED magic — got {plaintext[:4].hex()}"
        )
    if plaintext[2:4] != _BINARY_FRAME_VERSION_BE:
        # Soft assertion — every frame seen so far carries 0x0064 here.
        # We do not refuse parsing on mismatch, but the caller's debug
        # log will surface unexpected values for analysis.
        pass
    if plaintext[12:14] != _BINARY_FRAME_PAYLOAD_MARKER_BE:
        raise ValueError(f"unexpected payload marker {plaintext[12:14].hex()!r}")
    frame_index = int.from_bytes(plaintext[4:6], "big")
    chunk_count = int.from_bytes(plaintext[6:8], "big")
    flags = int.from_bytes(plaintext[8:10], "big")
    cmd = int.from_bytes(plaintext[10:12], "big")
    body_length = int.from_bytes(plaintext[14:16], "big")
    if (
        len(plaintext)
        < _BINARY_FRAME_HEADER_LEN + body_length + _BINARY_FRAME_TRAILER_LEN
    ):
        raise ValueError(
            f"frame truncated: body_length={body_length} but plaintext is "
            f"{len(plaintext)} bytes"
        )
    body = plaintext[_BINARY_FRAME_HEADER_LEN : _BINARY_FRAME_HEADER_LEN + body_length]
    trailer = plaintext[
        _BINARY_FRAME_HEADER_LEN + body_length : _BINARY_FRAME_HEADER_LEN
        + body_length
        + _BINARY_FRAME_TRAILER_LEN
    ]
    return BleBinaryFrame(
        frame_index=frame_index,
        chunk_count=chunk_count,
        flags=flags,
        cmd=cmd,
        body=body,
        trailer=trailer,
    )


# ---------------------------------------------------------------------------
# Frame dataclass + builder/parser
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BleFrame:
    """Parsed view of a single Jackery BLE frame.

    A logical message is split into ``chunk_count`` frames, each carrying
    a fragment of the payload. Receivers reassemble by ordering ``frame_index``
    from 1..chunk_count and concatenating ``chunk_payload`` in order.
    """

    frame_index: int
    chunk_count: int
    action_id: int
    ble_cmd: int
    chunk_payload: bytes


def build_plaintext_frame(frame: BleFrame) -> str:
    """Serialise *frame* to the plaintext hex-string layout (no AES, no CRC).

    Used as the input to :func:`encrypt_frame` and as the expected output
    of :func:`parse_plaintext_frame` after AES decrypt + CRC strip.
    """
    chunk_hex = hex_encode(frame.chunk_payload)
    if len(chunk_hex) % 2 != 0:
        raise ValueError("chunk_payload must serialise to an even hex length")
    return (
        BLE_FRAME_MAGIC
        + BLE_FRAME_VERSION
        + hex16(frame.frame_index)
        + hex16(frame.chunk_count)
        + hex16(frame.action_id)
        + hex16(frame.ble_cmd)
        + BLE_FRAME_PAYLOAD_MARKER
        + hex16(len(frame.chunk_payload))
        + chunk_hex
    )


# Length of the fixed header before the variable-width ``CHUNK_HEX`` field:
# magic(4) + version(4) + frame_idx(4) + chunk_cnt(4) + action_id(4)
# + ble_cmd(4) + payload_marker(4) + chunk_len(4) = 32 hex chars.
_HEADER_HEX_LEN: int = 4 + 4 + 4 + 4 + 4 + 4 + 4 + 4


def parse_plaintext_frame(text: str) -> BleFrame:
    """Parse a plaintext hex frame string back into a :class:`BleFrame`.

    Raises ``ValueError`` when the magic, version or payload-marker do
    not match the expected literals.
    """
    if len(text) < _HEADER_HEX_LEN:
        raise ValueError("frame too short")
    if not text.startswith(BLE_FRAME_MAGIC):
        raise ValueError(f"frame does not start with {BLE_FRAME_MAGIC!r}")
    cursor = len(BLE_FRAME_MAGIC)
    version = text[cursor : cursor + _HEX16_WIDTH]
    if version != BLE_FRAME_VERSION:
        raise ValueError(f"unexpected protocol version {version!r}")
    cursor += _HEX16_WIDTH
    frame_index = parse_hex16(text[cursor : cursor + _HEX16_WIDTH])
    cursor += _HEX16_WIDTH
    chunk_count = parse_hex16(text[cursor : cursor + _HEX16_WIDTH])
    cursor += _HEX16_WIDTH
    action_id = parse_hex16(text[cursor : cursor + _HEX16_WIDTH])
    cursor += _HEX16_WIDTH
    ble_cmd = parse_hex16(text[cursor : cursor + _HEX16_WIDTH])
    cursor += _HEX16_WIDTH
    marker = text[cursor : cursor + _HEX16_WIDTH]
    if marker != BLE_FRAME_PAYLOAD_MARKER:
        raise ValueError(f"unexpected payload marker {marker!r}")
    cursor += _HEX16_WIDTH
    chunk_len = parse_hex16(text[cursor : cursor + _HEX16_WIDTH])
    cursor += _HEX16_WIDTH
    expected_hex_len = chunk_len * 2
    chunk_hex = text[cursor : cursor + expected_hex_len]
    if len(chunk_hex) != expected_hex_len:
        raise ValueError(
            f"chunk payload truncated: expected {expected_hex_len} hex chars, "
            f"have {len(chunk_hex)}"
        )
    return BleFrame(
        frame_index=frame_index,
        chunk_count=chunk_count,
        action_id=action_id,
        ble_cmd=ble_cmd,
        chunk_payload=hex_decode(chunk_hex),
    )


# ---------------------------------------------------------------------------
# Full encrypt/decrypt pipeline
# ---------------------------------------------------------------------------


def _append_random_and_crc(plaintext_frame: str, random16: int | None) -> str:
    """Append a random tag and Modbus-CRC-16 suffix matching ``bb.a.c``.

    The app inserts a 16-bit random tag right before the CRC suffix (line
    518 in ``bb/a.smali``). The receiver in ``bb.a.a`` validates the CRC
    over everything except the trailing 4 hex chars (line 280-313). When
    *random16* is None this helper picks a fresh value via ``secrets``.
    """
    if random16 is None:
        random16 = secrets.randbelow(0x10000)
    tag = hex16(random16)
    with_tag = plaintext_frame + tag
    crc = crc16_hex(with_tag.encode("utf-8"))
    return with_tag + crc


def encrypt_frame(
    frame: BleFrame,
    key: bytes,
    *,
    iv: bytes | None = None,
    random16: int | None = None,
) -> bytes:
    """Build and encrypt a single BLE frame ready to be GATT-written.

    Returns ``iv || ciphertext`` (16 bytes IV concatenated with the AES
    ciphertext). The wire format used by the app then base64-encodes this
    blob before writing to GATT (``bb/a.c`` line 530-577).
    """
    plaintext_frame = build_plaintext_frame(frame)
    payload = _append_random_and_crc(plaintext_frame, random16)
    actual_iv = random_iv() if iv is None else iv
    ciphertext = aes_encrypt(payload.encode("utf-8"), key, actual_iv)
    return actual_iv + ciphertext


def decrypt_frame(blob: bytes, key: bytes) -> BleFrame:
    """Reverse of :func:`encrypt_frame`.

    Accepts ``iv || ciphertext`` (the same shape the app's ``bb.a.a``
    routine expects after base64 decoding the GATT payload), validates
    the CRC suffix, strips magic and returns the parsed :class:`BleFrame`.
    """
    if len(blob) < BLE_AES_IV_LEN + algorithms.AES.block_size // 8:
        raise ValueError("ciphertext blob too short")
    iv = blob[:BLE_AES_IV_LEN]
    ciphertext = blob[BLE_AES_IV_LEN:]
    plaintext = aes_decrypt(ciphertext, key, iv).decode("utf-8")
    if len(plaintext) < _HEX16_WIDTH * 2:
        raise ValueError("plaintext too short to carry random tag + CRC")
    crc_received = plaintext[-_HEX16_WIDTH:]
    body = plaintext[:-_HEX16_WIDTH]
    crc_expected = crc16_hex(body.encode("utf-8"))
    if crc_received.upper() != crc_expected.upper():
        raise ValueError(
            f"CRC mismatch: payload says {crc_received!r}, computed {crc_expected!r}"
        )
    # Strip the trailing 16-bit random tag the app appends before the CRC.
    frame_text = body[:-_HEX16_WIDTH]
    return parse_plaintext_frame(frame_text)


# ---------------------------------------------------------------------------
# Chunking helper (encode side, for Phase 3b setters)
# ---------------------------------------------------------------------------
#
# The app sizes each chunk at ``(MTU - 60) * 2`` *hex characters*, i.e. the
# raw payload-byte budget per frame is ``MTU - 60``. The default BLE 5.0
# MTU is 23 (= 3 bytes/chunk after the 60-byte overhead), but the app
# typically negotiates 247-byte MTU on Android → ~187 bytes/chunk. The
# device may negotiate something else; this helper takes the negotiated
# value as a parameter and refuses to chunk below the minimum.


_BLE_FRAME_OVERHEAD: int = 60

#: MTU the official Jackery Android app negotiates with the SolarVault.
#: Used as a fallback when the bleak transport doesn't expose
#: ``client.mtu_size`` (some backends only learn the value after the
#: first long write). Matches the value pinned by
#: :func:`chunk_size_for_mtu` to ``247 - 60 = 187`` payload bytes/frame.
DEFAULT_BLE_MTU: int = 247


def chunk_size_for_mtu(mtu: int) -> int:
    """Return the maximum payload-byte count per frame for a given MTU.

    Matches the app's calculation at ``HomeControlFormat.smali`` line 464:
    ``payload_bytes_per_frame = mtu - 60``. Refuses values that would
    produce a non-positive chunk size — the GATT MTU must be negotiated
    to at least 61 before frames can be sent at all.
    """
    if mtu <= _BLE_FRAME_OVERHEAD:
        raise ValueError(
            f"BLE MTU {mtu} is below the {_BLE_FRAME_OVERHEAD}-byte frame overhead"
        )
    return mtu - _BLE_FRAME_OVERHEAD


def split_body_for_mtu(body: bytes, mtu: int) -> list[bytes]:
    """Split a JSON body across the binary-frame stream the device expects.

    Each returned chunk fits into one :func:`build_binary_frame` payload
    on a link that negotiated the given MTU. The caller wraps each chunk
    in a binary frame with matching ``frame_index`` (1-based) and
    ``chunk_count`` (= ``len(result)``), encrypts it independently and
    issues one GATT write per chunk.

    Returns ``[b""]`` for an empty body so the writer still emits one
    envelope (parity with :func:`split_payload_into_frames`).
    """
    size = chunk_size_for_mtu(mtu)
    if not body:
        return [b""]
    return [body[offset : offset + size] for offset in range(0, len(body), size)]


def split_payload_into_frames(
    payload: bytes,
    *,
    action_id: int,
    ble_cmd: int,
    mtu: int,
) -> list[BleFrame]:
    """Split a logical payload into the BLE-Frame stream the device expects."""
    chunk_size = chunk_size_for_mtu(mtu)
    if not payload:
        # Send a single empty-body frame so queries (cmd=0 weather alerts
        # etc.) still get a chunk_count=1 envelope on the wire.
        return [
            BleFrame(
                frame_index=1,
                chunk_count=1,
                action_id=action_id,
                ble_cmd=ble_cmd,
                chunk_payload=b"",
            )
        ]
    chunks = [
        payload[offset : offset + chunk_size]
        for offset in range(0, len(payload), chunk_size)
    ]
    count = len(chunks)
    return [
        BleFrame(
            frame_index=idx + 1,
            chunk_count=count,
            action_id=action_id,
            ble_cmd=ble_cmd,
            chunk_payload=chunk,
        )
        for idx, chunk in enumerate(chunks)
    ]


__all__ = [
    "BLE_AES_IV_LEN",
    "BLE_AES_KEY_LEN",
    "BLE_AES_KEY_LENGTHS",
    "BLE_AES_KEY_LEN_AES128",
    "BLE_AES_KEY_LEN_AES256",
    "BLE_FRAME_MAGIC",
    "BLE_FRAME_PAYLOAD_MARKER",
    "BLE_FRAME_VERSION",
    "BLE_MANUFACTURER_ID",
    "BLE_NOTIFY_CHAR_UUID",
    "BLE_SERVICE_UUID",
    "BLE_WRITE_CHAR_UUID",
    "BleBinaryFrame",
    "BleFrame",
    "DEFAULT_BLE_MTU",
    "aes_decrypt",
    "aes_encrypt",
    "build_binary_frame",
    "build_plaintext_frame",
    "decrypt_binary_notify",
    "encrypt_binary_notify",
    "chunk_size_for_mtu",
    "crc16_hex",
    "crc16_modbus",
    "decrypt_frame",
    "encrypt_frame",
    "hex16",
    "hex_decode",
    "hex_encode",
    "parse_hex16",
    "parse_plaintext_frame",
    "random_iv",
    "split_body_for_mtu",
    "split_payload_into_frames",
]

# Re-exported for callers that need to seed deterministic tests; not part of
# the public API. ``os.urandom`` is the underlying entropy source used by
# ``secrets.token_bytes``.
del os  # pragma: no cover — keep the import out of __all__ to avoid leakage.
