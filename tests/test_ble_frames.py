"""Behaviour tests for the packed-binary BLE frame contract.

Covers the wire layout emitted by ``build_binary_frame`` and the guards
enforced by ``decrypt_binary_notify``. The codec performs no I/O, so no
GATT client is involved and nothing is mocked; the transport's own
write/notify/ACK behaviour is tested separately.

Header bytes are asserted as literals so a layout change fails loudly.
The layout is pinned to the live capture documented in ``const.py``
(2026-05-16): 16-byte header of big-endian uint16 fields, then the body,
then a 4-byte trailer.

App 2.4.0 proves that the four-byte trailer is a two-byte random security
value followed by a Modbus CRC-16 in little-endian wire order. These tests
pin that exact outbound contract while retaining explicit trailer injection
only for malformed/inbound parser fixtures.
"""

from typing import Any

import pytest

from custom_components.jackery_solarvault.client.ble import (
    BleBinaryFrame,
    build_binary_frame,
    decrypt_binary_notify,
    encrypt_binary_notify,
    split_body_for_mtu,
)

_KEY = b"0123456789abcdef"
_IV = b"fedcba9876543210"
_IV_LEN = 16

_CMD = 107  # HomeCmdAction.bleMsgType - DevicePropertyChange (0x006B).
_MSG_ID = 42  # HomeCmdAction.msgId (0x002A), carried as `flags`.
_BODY = b'{"a":1}'  # 7 bytes.
_IDX = 2
_CNT = 3
_TRAILER = b"\xde\xad\xbe\xef"  # Opaque 4 bytes; contents are never derived.
_SECURITY = 0x1234
_APP_CRC_FOR_DEFAULT_FRAME = b"\xed\x27"

_HEADER_LEN = 16
_TRAILER_LEN = 4
_U16_OVERFLOW = 0x10000

_MTU = 100
_CHUNK = 40


# --- outbound layout ------------------------------------------------------


def test_build_binary_frame_packs_the_documented_header_layout() -> None:
    """Header fields are 2-byte big-endian in the documented order."""
    frame = build_binary_frame(
        cmd=_CMD,
        body=_BODY,
        flags=_MSG_ID,
        frame_index=_IDX,
        chunk_count=_CNT,
        trailer=_TRAILER,
    )

    assert frame[:_HEADER_LEN] == (
        b"\xdf\xed"  # magic
        b"\x00\x01"  # outbound version — HomeControlFormat 2.4.0 literal
        b"\x00\x02"  # frame_index
        b"\x00\x03"  # chunk_count
        b"\x00\x2a"  # flags / HomeCmdAction.msgId
        b"\x00\x6b"  # cmd / HomeCmdAction.bleMsgType
        b"\x00\x01"  # payload-type marker
        b"\x00\x07"  # body_length
    )
    assert frame[_HEADER_LEN : _HEADER_LEN + len(_BODY)] == _BODY
    assert frame[-_TRAILER_LEN:] == _TRAILER
    assert len(frame) == _HEADER_LEN + len(_BODY) + _TRAILER_LEN


def test_build_binary_frame_appends_app_security_and_little_endian_crc() -> None:
    """The App 2.4.0 security tag and CRC replace the invalid zero trailer."""
    frame = build_binary_frame(cmd=_CMD, body=_BODY, security=_SECURITY)

    assert frame[4:6] == b"\x00\x01"  # frame_index defaults to 1.
    assert frame[6:8] == b"\x00\x01"  # chunk_count defaults to 1.
    assert frame[8:10] == b"\x00\x00"  # flags is optional and defaults to 0.
    assert frame[-_TRAILER_LEN:] == b"\x12\x34" + _APP_CRC_FOR_DEFAULT_FRAME


def test_build_binary_frame_passes_the_opaque_trailer_through_untouched() -> None:
    """The trailer is caller-supplied, never computed from the frame."""
    first = build_binary_frame(cmd=_CMD, body=_BODY, trailer=_TRAILER)
    second = build_binary_frame(cmd=_CMD, body=_BODY, trailer=b"\x00\x11\x22\x33")

    assert first[-_TRAILER_LEN:] == _TRAILER
    assert second[-_TRAILER_LEN:] == b"\x00\x11\x22\x33"
    assert first[:-_TRAILER_LEN] == second[:-_TRAILER_LEN]  # Body/header unaffected.


def test_binary_frame_survives_encrypt_decrypt_with_every_field_intact() -> None:
    """A built frame round-trips through the notify envelope unchanged."""
    plaintext = build_binary_frame(
        cmd=_CMD,
        body=_BODY,
        flags=_MSG_ID,
        frame_index=_IDX,
        chunk_count=_CNT,
        trailer=_TRAILER,
    )

    raw = encrypt_binary_notify(plaintext, _KEY, iv=_IV)
    frame = decrypt_binary_notify(raw, _KEY)

    assert raw[:_IV_LEN] == _IV  # IV is a plaintext prefix, not encrypted.
    assert isinstance(frame, BleBinaryFrame)
    assert frame.cmd == _CMD
    assert frame.flags == _MSG_ID
    assert frame.frame_index == _IDX
    assert frame.chunk_count == _CNT
    assert frame.body == _BODY
    assert frame.trailer == _TRAILER  # Handed back verbatim, not validated.


@pytest.mark.parametrize(
    ["field", "value", "expected"],
    [
        pytest.param(
            "cmd",
            _U16_OVERFLOW,
            "cmd 65536 does not fit into 16 bits",
            id="cmd-above-u16",
        ),
        pytest.param("cmd", -1, "cmd -1 does not fit into 16 bits", id="cmd-negative"),
        pytest.param(
            "flags",
            _U16_OVERFLOW,
            "flags 65536 does not fit into 16 bits",
            id="msg-id-above-u16",
        ),
        pytest.param(
            "flags", -1, "flags -1 does not fit into 16 bits", id="msg-id-negative"
        ),
        pytest.param(
            "frame_index", 0, "frame_index 0 out of range", id="frame-index-below-one"
        ),
        pytest.param(
            "frame_index",
            _U16_OVERFLOW,
            "frame_index 65536 out of range",
            id="frame-index-above-u16",
        ),
        pytest.param(
            "chunk_count", 0, "chunk_count 0 out of range", id="chunk-count-below-one"
        ),
        pytest.param(
            "chunk_count",
            _U16_OVERFLOW,
            "chunk_count 65536 out of range",
            id="chunk-count-above-u16",
        ),
    ],
)
def test_build_binary_frame_rejects_out_of_range_header_fields(
    field: str, value: int, expected: str
) -> None:
    """Every packed uint16 field is range-checked before serialisation."""
    kwargs: Any = {"cmd": _CMD, field: value}

    with pytest.raises(ValueError, match=expected):
        build_binary_frame(body=_BODY, **kwargs)


def test_build_binary_frame_rejects_a_body_that_overflows_the_length_field() -> None:
    """`body_length` is a uint16, so the body may not exceed 65535 bytes."""
    with pytest.raises(ValueError, match="body too long"):
        build_binary_frame(cmd=_CMD, body=b"x" * (_U16_OVERFLOW))


@pytest.mark.parametrize(
    "trailer",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"\x00\x00\x00", id="one-short"),
        pytest.param(b"\x00\x00\x00\x00\x00", id="one-over"),
    ],
)
def test_build_binary_frame_rejects_a_trailer_that_is_not_four_bytes(
    trailer: bytes,
) -> None:
    """The trailer is opaque but its width is fixed by the observed layout."""
    with pytest.raises(ValueError, match="trailer must be"):
        build_binary_frame(cmd=_CMD, body=_BODY, trailer=trailer)


def test_encrypt_binary_notify_rejects_an_iv_that_is_not_block_sized() -> None:
    """The envelope refuses a non-16-byte IV before touching the cipher."""
    plaintext = build_binary_frame(cmd=_CMD, body=_BODY, flags=_MSG_ID)

    with pytest.raises(ValueError, match="iv must be"):
        encrypt_binary_notify(plaintext, _KEY, iv=b"short")


# --- inbound guards -------------------------------------------------------


@pytest.mark.parametrize(
    ["raw", "expected"],
    [
        pytest.param(b"\x00" * 4, "notify too short", id="below-iv-header-trailer"),
        pytest.param(
            _IV + b"\x00" * 21,
            "not aligned to AES block size",
            id="misaligned-ciphertext",
        ),
    ],
)
def test_decrypt_binary_notify_rejects_malformed_envelopes(
    raw: bytes, expected: str
) -> None:
    """Envelope guards run before AES so corrupt input never reaches it."""
    with pytest.raises(ValueError, match=expected):
        decrypt_binary_notify(raw, _KEY)


@pytest.mark.parametrize(
    ["offset", "replacement", "expected"],
    [
        pytest.param(0, b"\xaa\xbb", "DFED magic", id="bad-magic"),
        pytest.param(
            12, b"\x00\x02", "unexpected payload marker", id="bad-payload-marker"
        ),
    ],
)
def test_decrypt_binary_notify_rejects_structurally_invalid_headers(
    offset: int, replacement: bytes, expected: str
) -> None:
    """Magic and payload marker are enforced before the body is trusted."""
    good = build_binary_frame(cmd=_CMD, body=_BODY, flags=_MSG_ID)
    forged = good[:offset] + replacement + good[offset + len(replacement) :]
    raw = encrypt_binary_notify(forged, _KEY, iv=_IV)

    with pytest.raises(ValueError, match=expected):
        decrypt_binary_notify(raw, _KEY)


def test_decrypt_binary_notify_rejects_a_declared_body_length_that_lies() -> None:
    """A body_length larger than the bytes present truncates the trailer."""
    good = build_binary_frame(cmd=_CMD, body=_BODY, flags=_MSG_ID)
    forged = good[:14] + (len(_BODY) + 1).to_bytes(2, "big") + good[16:]
    raw = encrypt_binary_notify(forged, _KEY, iv=_IV)

    with pytest.raises(ValueError, match="frame truncated"):
        decrypt_binary_notify(raw, _KEY)


def test_decrypt_binary_notify_preserves_chunk_indices_across_a_split_body() -> None:
    """Each chunk of a split body round-trips carrying its own 1-based index."""
    body = b"z" * (_CHUNK * 2 + 3)
    chunks = split_body_for_mtu(body, _MTU)
    count = len(chunks)

    decoded: list[BleBinaryFrame] = []
    for index, chunk in enumerate(chunks, start=1):
        plaintext = build_binary_frame(
            cmd=_CMD,
            body=chunk,
            flags=_MSG_ID,
            frame_index=index,
            chunk_count=count,
        )
        raw = encrypt_binary_notify(plaintext, _KEY, iv=_IV)
        decoded.append(decrypt_binary_notify(raw, _KEY))

    assert [frame.frame_index for frame in decoded] == [1, 2, 3]
    assert {frame.chunk_count for frame in decoded} == {count}
    assert b"".join(frame.body for frame in decoded) == body
