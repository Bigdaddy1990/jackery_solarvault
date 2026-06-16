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
        <CHUNK_LEN>      # 16-bit big-endian, hex-encoded (4 chars) — byte count of
        <CHUNK_HEX>/2
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

from custom_components.jackery_solarvault.const import (
    BLE_AES_IV_LEN,
    BLE_AES_KEY_LEN,
    BLE_AES_KEY_LENGTHS,
    BLE_AES_KEY_LEN_AES128,
    BLE_AES_KEY_LEN_AES256,
    BLE_FRAME_MAGIC,
    BLE_FRAME_PAYLOAD_MARKER,
    BLE_FRAME_VERSION,
    BLE_MANUFACTURER_ID,
    BLE_NOTIFY_CHAR_UUID,
    BLE_SERVICE_UUID,
    BLE_WRITE_CHAR_UUID,
    DEFAULT_BLE_MTU,
    _BINARY_FRAME_HEADER_LEN,
    _BINARY_FRAME_MAGIC_BE,
    _BINARY_FRAME_PAYLOAD_MARKER_BE,
    _BINARY_FRAME_TRAILER_LEN,
    _BINARY_FRAME_VERSION_BE,
    _BLE_FRAME_OVERHEAD,
    _HEADER_HEX_LEN,
    _HEX16_WIDTH,
)

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Low-level hex helpers (mirror ``sb.d.d`` in the app)
# ---------------------------------------------------------------------------


def hex16(value: int) -> str:
    """Encode a 16-bit unsigned integer as a 4-character uppercase hexadecimal string.

    Returns:
        A 4-character uppercase hex string representing the input value (e.g. 0 ->
        "0000", 65535 -> "FFFF").

    Raises:
        ValueError: If `value` is not between 0 and 0xFFFF (inclusive).
    """
    if not 0 <= value <= 0xFFFF:  # noqa: PLR2004
        msg = f"hex16: {value} does not fit into 16 bits"
        raise ValueError(msg)
    return f"{value:04X}"


def parse_hex16(text: str) -> int:
    """Parse a 4-character hexadecimal string and return its integer value.

    Parameters:
        text (str): Hexadecimal string exactly 4 characters long (case-insensitive).

    Returns:
        int: Integer value represented by `text`.

    Raises:
        ValueError: If `text` is not exactly 4 characters long or contains invalid
        hexadecimal digits.
    """
    if len(text) != _HEX16_WIDTH:
        msg = f"parse_hex16: expected {_HEX16_WIDTH} hex chars, got {len(text)}"
        raise ValueError(
            msg,
        )
    try:
        return int(text, 16)
    except ValueError as err:
        msg = "parse_hex16: expected hex chars"
        raise ValueError(msg) from err


def hex_encode(data: bytes) -> str:
    """Produce an uppercase hexadecimal string of the given bytes with no separators or.

    "0x" prefix.

    Returns:
        str: Uppercase hexadecimal representation of `data`.
    """
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
    """Ensure `key` has a supported AES length (16 or 32 bytes).

    Parameters:
        key (bytes): AES key to validate.
        fn (str): Name of the calling function; used as a prefix in the raised error
        message.

    Raises:
        ValueError: If `len(key)` is not 16 or 32; message is prefixed with `fn`.
    """
    if len(key) not in BLE_AES_KEY_LENGTHS:
        msg = (
            f"{fn}: key must be one of {BLE_AES_KEY_LENGTHS} bytes "
            f"(got {len(key)} bytes)"
        )
        raise ValueError(
            msg,
        )


def aes_encrypt(plaintext: bytes, key: bytes, iv: bytes) -> bytes:
    """Encrypts plaintext using AES in CBC mode with PKCS#7 padding.

    Key length selects the AES variant: 16 bytes → AES-128, 32 bytes → AES-256. The
    caller-provided IV must be exactly 16 bytes; providing a different IV length raises
    ValueError.

    Parameters:
        plaintext (bytes): Data to be encrypted.
        key (bytes): Encryption key; must be 16 or 32 bytes.
        iv (bytes): Initialization vector; must be 16 bytes.

    Returns:
        bytes: Ciphertext produced by AES-CBC over the PKCS#7-padded plaintext.

    Raises:
        ValueError: If `key` length is not 16 or 32 bytes, or if `iv` is not 16 bytes.
    """
    _validate_key_len(key, fn="aes_encrypt")
    if len(iv) != BLE_AES_IV_LEN:
        msg = f"aes_encrypt: iv must be {BLE_AES_IV_LEN} bytes"
        raise ValueError(msg)
    padder = PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def aes_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """Decrypt AES-CBC-PKCS7 ciphertext using the provided key and IV.

    Accepts 16-byte (AES-128) or 32-byte (AES-256) keys; IV must be exactly 16 bytes.
    The function removes PKCS#7 padding and returns the resulting plaintext.

    Parameters:
        ciphertext (bytes): Ciphertext to decrypt (AES-CBC, block-aligned).
        key (bytes): AES key (16 or 32 bytes).
        iv (bytes): Initialization vector (must be 16 bytes).

    Returns:
        bytes: Decrypted plaintext with PKCS#7 padding removed.

    Raises:
        ValueError: If `key` length is not 16 or 32 bytes, if `iv` is not 16 bytes, or
        if the ciphertext is malformed or padding is invalid.
    """
    _validate_key_len(key, fn="aes_decrypt")
    if len(iv) != BLE_AES_IV_LEN:
        msg = f"aes_decrypt: iv must be {BLE_AES_IV_LEN} bytes"
        raise ValueError(msg)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def random_iv() -> bytes:
    """Generate a fresh 16-byte CBC IV using a cryptographic RNG."""
    return secrets.token_bytes(BLE_AES_IV_LEN)


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


def build_binary_frame(  # noqa: PLR0913
    *,
    cmd: int,
    body: bytes,
    flags: int = 0,
    frame_index: int = 1,
    chunk_count: int = 1,
    trailer: bytes = b"\x00\x00\x00\x00",
) -> bytes:
    r"""Builds a plaintext binary frame consisting of header, body, and trailer.

    The header encodes magic, version, frame index, chunk count, flags, command,
    a payload marker, and the body length; the result is the concatenation of
    that header, the raw body bytes, and the 4-byte trailer.

    Parameters:
        cmd (int): 16-bit command identifier.
        body (bytes): Payload bytes (length must fit in 16 bits).
        flags (int): 16-bit flags field. Defaults to 0.
        frame_index (int): 1-based frame index in the message sequence. Defaults to 1.
        chunk_count (int): Total number of chunks in the message. Defaults to 1.
        trailer (bytes): 4-byte opaque trailer appended after the body. Defaults to
        b"\\x00\\x00\\x00\\x00".

    Returns:
        bytes: The assembled plaintext binary frame (header || body || trailer).

    Raises:
        ValueError: If `cmd` or `flags` do not fit in 16 bits, if `frame_index` or
            `chunk_count` are outside the range 1..0xFFFF, if `body` is longer than
            0xFFFF bytes, or if `trailer` is not exactly 4 bytes.
    """
    if not 0 <= cmd <= 0xFFFF:  # noqa: PLR2004
        msg = f"cmd {cmd} does not fit into 16 bits"
        raise ValueError(msg)
    if not 0 <= flags <= 0xFFFF:  # noqa: PLR2004
        msg = f"flags {flags} does not fit into 16 bits"
        raise ValueError(msg)
    if not 1 <= frame_index <= 0xFFFF:  # noqa: PLR2004
        msg = f"frame_index {frame_index} out of range"
        raise ValueError(msg)
    if not 1 <= chunk_count <= 0xFFFF:  # noqa: PLR2004
        msg = f"chunk_count {chunk_count} out of range"
        raise ValueError(msg)
    if len(body) > 0xFFFF:  # noqa: PLR2004
        msg = f"body too long: {len(body)} bytes"
        raise ValueError(msg)
    if len(trailer) != _BINARY_FRAME_TRAILER_LEN:
        msg = f"trailer must be {_BINARY_FRAME_TRAILER_LEN} bytes, got {len(trailer)}"
        raise ValueError(
            msg,
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
    assert len(header) == _BINARY_FRAME_HEADER_LEN  # noqa: S101
    return header + body + trailer


def encrypt_binary_notify(
    plaintext_frame: bytes,
    key: bytes,
    *,
    iv: bytes | None = None,
) -> bytes:
    """Encrypts a binary plaintext frame using AES-CBC with PKCS7 padding.

    If `iv` is omitted a cryptographically random 16-byte IV is generated; when
    provided the IV must be exactly 16 bytes. The function returns the IV
    concatenated with the ciphertext.

    Parameters:
        iv (bytes | None): Optional 16-byte initialization vector. If `None`, a
            random IV is used.

    Returns:
        bytes: 16-byte IV followed by the AES-CBC ciphertext (`iv || ciphertext`).

    Raises:
        ValueError: If a provided `iv` is not 16 bytes long.
    """
    actual_iv = random_iv() if iv is None else iv
    if len(actual_iv) != BLE_AES_IV_LEN:
        msg = f"iv must be {BLE_AES_IV_LEN} bytes"
        raise ValueError(msg)
    ciphertext = aes_encrypt(plaintext_frame, key, actual_iv)
    return actual_iv + ciphertext


def decrypt_binary_notify(raw: bytes, key: bytes) -> BleBinaryFrame:
    """Decrypt a raw notify payload (IV || ciphertext) and parse the contained binary.

    frame header.

    Expects `raw` as 16-byte IV concatenated with AES-CBC ciphertext; decrypts the
    ciphertext and validates the binary-frame structure (magic, payload marker,
    declared body length, and trailer). Raises `ValueError` for any structural or
    length mismatch (too-short input, ciphertext not AES-block-aligned, wrong magic or
    payload marker, or truncated body).

    Returns:
        BleBinaryFrame: Parsed frame with `frame_index`, `chunk_count`, `flags`, `cmd`,
        `body`, and 4-byte `trailer`.
    """
    if len(raw) < BLE_AES_IV_LEN + _BINARY_FRAME_HEADER_LEN + _BINARY_FRAME_TRAILER_LEN:
        msg = f"notify too short: {len(raw)} bytes"
        raise ValueError(msg)
    iv = raw[:BLE_AES_IV_LEN]
    ciphertext = raw[BLE_AES_IV_LEN:]
    if len(ciphertext) % 16 != 0:
        msg = f"ciphertext is not aligned to AES block size: {len(ciphertext)} bytes"
        raise ValueError(
            msg,
        )
    plaintext = aes_decrypt(ciphertext, key, iv)
    if not plaintext.startswith(_BINARY_FRAME_MAGIC_BE):
        msg = f"plaintext does not start with DFED magic — got {plaintext[:4].hex()}"
        raise ValueError(
            msg,
        )
    if plaintext[2:4] != _BINARY_FRAME_VERSION_BE:
        # Soft assertion - every frame seen so far carries 0x0064 here.
        # We do not refuse parsing on mismatch, but log unexpected values for analysis.
        _LOGGER.debug(
            "BLE binary frame version mismatch: expected %s, got %s",
            _BINARY_FRAME_VERSION_BE.hex(),
            plaintext[2:4].hex(),
        )
    if plaintext[12:14] != _BINARY_FRAME_PAYLOAD_MARKER_BE:
        msg = f"unexpected payload marker {plaintext[12:14].hex()!r}"
        raise ValueError(msg)
    frame_index = int.from_bytes(plaintext[4:6], "big")
    chunk_count = int.from_bytes(plaintext[6:8], "big")
    flags = int.from_bytes(plaintext[8:10], "big")
    cmd = int.from_bytes(plaintext[10:12], "big")
    body_length = int.from_bytes(plaintext[14:16], "big")
    if (
        len(plaintext)
        < _BINARY_FRAME_HEADER_LEN + body_length + _BINARY_FRAME_TRAILER_LEN
    ):
        msg = (
            f"frame truncated: body_length={body_length} but plaintext is "
            f"{len(plaintext)} bytes"
        )
        raise ValueError(
            msg,
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
    """Serialize a BleFrame into the plaintext hex-string frame layout.

    The result contains the protocol magic, version, header fields (frame index, chunk
    count, action id, BLE command),
    the payload marker, the payload length, and the hex-encoded chunk payload — without
    CRC or encryption.

    Returns:
        str: Hex-string representation of the plaintext frame suitable for
        CRC-appending and AES encryption.

    Raises:
        ValueError: If the chunk_payload's hex encoding has an odd length.
    """
    chunk_hex = hex_encode(frame.chunk_payload)
    if len(chunk_hex) % 2 != 0:
        msg = "chunk_payload must serialise to an even hex length"
        raise ValueError(msg)
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


def parse_plaintext_frame(text: str) -> BleFrame:
    """Parse a hex-encoded plaintext BLE frame into a BleFrame dataclass.

    The input must be the module's plaintext-hex frame: magic, version, frame index,
    chunk count, action id, command, payload marker, payload byte-length (hex16),
    followed by the payload bytes encoded as uppercase hex.

    Parameters:
        text (str): Hex-encoded plaintext frame string in the format described above.

    Returns:
        BleFrame: Parsed frame containing `frame_index`, `chunk_count`, `action_id`,
        `ble_cmd`, and `chunk_payload` (raw bytes).

    Raises:
        ValueError: If the input is too short, the magic/version/payload marker do not
        match expected values, the declared payload length does not match the available
        hex data, or any hex field fails to parse.
    """
    if len(text) < _HEADER_HEX_LEN:
        msg = "frame too short"
        raise ValueError(msg)
    if not text.startswith(BLE_FRAME_MAGIC):
        msg = f"frame does not start with {BLE_FRAME_MAGIC!r}"
        raise ValueError(msg)
    cursor = len(BLE_FRAME_MAGIC)
    version = text[cursor : cursor + _HEX16_WIDTH]
    if version != BLE_FRAME_VERSION:
        msg = f"unexpected protocol version {version!r}"
        raise ValueError(msg)
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
        msg = f"unexpected payload marker {marker!r}"
        raise ValueError(msg)
    cursor += _HEX16_WIDTH
    chunk_len = parse_hex16(text[cursor : cursor + _HEX16_WIDTH])
    cursor += _HEX16_WIDTH
    expected_hex_len = chunk_len * 2
    chunk_hex = text[cursor : cursor + expected_hex_len]
    if len(chunk_hex) != expected_hex_len:
        msg = (
            f"chunk payload truncated: expected {expected_hex_len} hex chars, "
            f"have {len(chunk_hex)}"
        )
        raise ValueError(
            msg,
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
    """Append a 16-bit hex tag and a 4-hex-digit Modbus CRC-16 suffix to a plaintext.

    hex-frame string.

    Parameters:
        plaintext_frame (str): Hex-string plaintext frame (header + payload) to be
        extended.
        random16 (int | None): 16-bit integer to use as the tag (0..0xFFFF). If None, a
        random value is chosen.

    Returns:
        str: The input string followed by a 4-character uppercase hex tag and a
        4-character uppercase CRC-16 (Modbus) computed over the UTF-8 bytes of
        (plaintext_frame + tag).
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
    """Serialize a BleFrame to the plaintext hex-frame format, append a 16-bit random.

    tag and CRC-16, and encrypt the result.

    The plaintext frame is built from `frame`, a 16-bit random tag is appended (or
    `random16` is used if provided), then a Modbus CRC-16 over the UTF-8 bytes is
    appended. The concatenated UTF-8 bytes are encrypted with AES-CBC-PKCS7 using `key`
    and `iv` (generated if omitted).

    Parameters:
        frame (BleFrame): Logical frame to serialize and encrypt.
        key (bytes): AES key (must be a supported length).
        iv (bytes | None): Optional 16-byte IV to use for AES-CBC. If `None`, a
        cryptographically random IV is generated.
        random16 (int | None): Optional 16-bit random tag to append before CRC. If
        `None`, a random value in [0, 0xFFFF] is chosen.

    Returns:
        bytes: The concatenation of the 16-byte IV and the AES ciphertext (`iv ||
        ciphertext`).
    """
    plaintext_frame = build_plaintext_frame(frame)
    payload = _append_random_and_crc(plaintext_frame, random16)
    actual_iv = random_iv() if iv is None else iv
    ciphertext = aes_encrypt(payload.encode("utf-8"), key, actual_iv)
    return actual_iv + ciphertext


def decrypt_frame(blob: bytes, key: bytes) -> BleFrame:
    """Decrypt a BLE plaintext frame encrypted with AES-CBC-PKCS7 and return the parsed.

    BleFrame.

    Decrypts an IV-prefixed blob (16-byte IV || ciphertext), verifies the trailing
    Modbus CRC-16 over the plaintext-with-tag, removes the appended 16-bit random tag,
    and parses the remaining hex-string plaintext into a BleFrame.

    Returns:
        BleFrame: The parsed frame extracted from the decrypted and verified plaintext.

    Raises:
        ValueError: If the input blob is shorter than IV + one AES block, if the
        decrypted plaintext is too short to contain the random tag and CRC, or if the
        CRC verification fails.
    """
    if len(blob) < BLE_AES_IV_LEN + algorithms.AES.block_size // 8:
        msg = "ciphertext blob too short"
        raise ValueError(msg)
    iv = blob[:BLE_AES_IV_LEN]
    ciphertext = blob[BLE_AES_IV_LEN:]
    plaintext = aes_decrypt(ciphertext, key, iv).decode("utf-8")
    if len(plaintext) < _HEX16_WIDTH * 2:
        msg = "plaintext too short to carry random tag + CRC"
        raise ValueError(msg)
    crc_received = plaintext[-_HEX16_WIDTH:]
    body = plaintext[:-_HEX16_WIDTH]
    crc_expected = crc16_hex(body.encode("utf-8"))
    if crc_received.upper() != crc_expected.upper():
        msg = f"CRC mismatch: payload says {crc_received!r}, computed {crc_expected!r}"
        raise ValueError(
            msg,
        )
    # Strip the trailing 16-bit random tag the app appends before the CRC.
    frame_text = body[:-_HEX16_WIDTH]
    return parse_plaintext_frame(frame_text)


def chunk_size_for_mtu(mtu: int) -> int:
    """Compute the maximum payload byte count per BLE frame for a given MTU.

    Raises ValueError if mtu is less than or equal to 60 because the frame overhead
    requires at least a 61-byte MTU.

    Returns:
        int: Maximum number of payload bytes allowed in a single frame (mtu - 60).
    """
    if mtu <= _BLE_FRAME_OVERHEAD:
        msg = f"BLE MTU {mtu} is below the {_BLE_FRAME_OVERHEAD}-byte frame overhead"
        raise ValueError(
            msg,
        )
    return mtu - _BLE_FRAME_OVERHEAD


def split_body_for_mtu(body: bytes, mtu: int) -> list[bytes]:
    """Split a byte payload into chunks sized to fit a single binary-frame payload for.

    the given MTU.

    Each returned chunk's length does not exceed the per-frame payload capacity derived
    from `mtu`. If `body` is empty, returns `[b""]` so the sender still emits one
    envelope.

    Returns:
        list[bytes]: A list of byte chunks ready to be embedded in individual binary
        frames.
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
    """Split a payload into BLE-sized frames for the given MTU.

    Non-empty payloads are sliced into chunks of size chunk_size_for_mtu(mtu); each
    chunk is returned as a BleFrame with a 1-based frame_index, the total chunk_count,
    and the provided action_id and ble_cmd. For an empty payload, returns a single
    BleFrame with an empty chunk_payload and chunk_count set to 1.

    Parameters:
        payload (bytes): The full logical payload to split.
        action_id (int): Action identifier to include in each resulting frame.
        ble_cmd (int): BLE command identifier to include in each resulting frame.
        mtu (int): Maximum transmission unit used to compute per-frame chunk size.

    Returns:
        list[BleFrame]: Ordered list of frames representing the payload chunks.
    """
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
            ),
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
    "DEFAULT_BLE_MTU",
    "BleBinaryFrame",
    "BleFrame",
    "aes_decrypt",
    "aes_encrypt",
    "build_binary_frame",
    "build_plaintext_frame",
    "chunk_size_for_mtu",
    "crc16_hex",
    "crc16_modbus",
    "decrypt_binary_notify",
    "decrypt_frame",
    "encrypt_binary_notify",
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
