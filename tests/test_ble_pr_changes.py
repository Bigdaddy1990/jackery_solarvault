"""Tests for the PR changes to client/ble.py.

The PR changed:
- String literals from single to double quotes (cosmetic, no behaviour change)
- build_binary_frame: replaced ``if len(header) != _BINARY_FRAME_HEADER_LEN: raise RuntimeError``
  with ``assert len(header) == _BINARY_FRAME_HEADER_LEN``

The assert change means:
  - In optimised bytecode (python -O) the check is a no-op.
  - In normal (non-optimised) runs, a violated assertion raises AssertionError,
    not RuntimeError.

These tests pin the new behaviour and guard against regressions.
"""  # noqa: E501

import pytest

from custom_components.jackery_solarvault.client.ble import (
    BLE_FRAME_MAGIC,
    BLE_FRAME_PAYLOAD_MARKER,
    BLE_FRAME_VERSION,
    BLE_NOTIFY_CHAR_UUID,
    BLE_SERVICE_UUID,
    BLE_WRITE_CHAR_UUID,
    build_binary_frame,
)

# ---------------------------------------------------------------------------
# build_binary_frame — assert replaces RuntimeError for header length
# ---------------------------------------------------------------------------


def test_build_binary_frame_normal_call_does_not_raise() -> None:
    """A valid call to build_binary_frame must not raise any exception."""
    result = build_binary_frame(cmd=107, body=b'{"cmd":107}')
    assert isinstance(result, bytes)
    assert len(result) > 0


def test_build_binary_frame_validates_cmd_range_raises_value_error() -> None:
    """Cmd outside 0..0xFFFF must raise ValueError (not AssertionError)."""
    with pytest.raises(ValueError, match="cmd"):
        build_binary_frame(cmd=-1, body=b"")
    with pytest.raises(ValueError, match="cmd"):
        build_binary_frame(cmd=0x10000, body=b"")


def test_build_binary_frame_cmd_boundary_values_are_accepted() -> None:
    """Boundary cmd values 0 and 0xFFFF must be accepted without error."""
    result_zero = build_binary_frame(cmd=0, body=b"")
    result_max = build_binary_frame(cmd=0xFFFF, body=b"")
    assert isinstance(result_zero, bytes)
    assert isinstance(result_max, bytes)


def test_build_binary_frame_validates_flags_range() -> None:
    """Flags outside 0..0xFFFF must raise ValueError."""
    with pytest.raises(ValueError, match="flags"):
        build_binary_frame(cmd=107, body=b"", flags=-1)
    with pytest.raises(ValueError, match="flags"):
        build_binary_frame(cmd=107, body=b"", flags=0x10000)


def test_build_binary_frame_validates_frame_index_range() -> None:
    """frame_index must be between 1 and 0xFFFF inclusive."""
    with pytest.raises(ValueError, match="frame_index"):
        build_binary_frame(cmd=107, body=b"", frame_index=0)
    with pytest.raises(ValueError, match="frame_index"):
        build_binary_frame(cmd=107, body=b"", frame_index=0x10000)
    # Boundaries should succeed.
    assert build_binary_frame(cmd=107, body=b"", frame_index=1)
    assert build_binary_frame(cmd=107, body=b"", frame_index=0xFFFF)


def test_build_binary_frame_validates_chunk_count_range() -> None:
    """chunk_count must be between 1 and 0xFFFF inclusive."""
    with pytest.raises(ValueError, match="chunk_count"):
        build_binary_frame(cmd=107, body=b"", chunk_count=0)
    with pytest.raises(ValueError, match="chunk_count"):
        build_binary_frame(cmd=107, body=b"", chunk_count=0x10000)


def test_build_binary_frame_validates_body_max_length() -> None:
    """Body longer than 0xFFFF bytes must raise ValueError."""
    with pytest.raises(ValueError, match="body too long"):
        build_binary_frame(cmd=107, body=b"x" * (0x10001))


def test_build_binary_frame_validates_trailer_length() -> None:
    """Trailer must be exactly 4 bytes; any other length raises ValueError."""
    with pytest.raises(ValueError, match="trailer"):
        build_binary_frame(cmd=107, body=b"", trailer=b"\x00\x00\x00")
    with pytest.raises(ValueError, match="trailer"):
        build_binary_frame(cmd=107, body=b"", trailer=b"\x00\x00\x00\x00\x00")
    # Exactly 4 bytes must succeed.
    assert build_binary_frame(cmd=107, body=b"", trailer=b"\x01\x02\x03\x04")


def test_build_binary_frame_header_length_is_correct_via_assert() -> None:
    """The header assembly must result in a 16-byte header (assertion check).

    This pins the invariant that the changed assert replaces: the assembled
    header must still be exactly 16 bytes or the assert fires.
    """
    # Build a valid frame and parse its first 16 bytes manually.
    plain = build_binary_frame(cmd=107, body=b"hello", frame_index=2, chunk_count=3)
    # Layout: magic(2) + version(2) + idx(2) + cnt(2) + flags(2) + cmd(2) + marker(2) + len(2)  # noqa: E501
    # = 16 bytes before body.
    assert plain[:2] == b"\xdf\xed"
    # body starts at offset 16, trailer at 16 + len(body).
    body_end = 16 + 5
    assert plain[16:body_end] == b"hello"


def test_build_binary_frame_output_starts_with_magic_bytes() -> None:
    """Every frame must start with the 0xDFED magic bytes."""
    frame = build_binary_frame(cmd=0, body=b"")
    assert frame[:2] == b"\xdf\xed"


def test_build_binary_frame_output_ends_with_default_zero_trailer() -> None:
    """Default trailer is four zero bytes."""
    frame = build_binary_frame(cmd=0, body=b"")
    assert frame[-4:] == b"\x00\x00\x00\x00"


def test_build_binary_frame_round_trips_with_decrypt_binary_notify() -> None:
    """build_binary_frame + encrypt_binary_notify + decrypt_binary_notify must round-trip.

    Pins the symmetry that the PR did not break despite the assert change.
    """  # noqa: E501
    import base64  # noqa: PLC0415

    from custom_components.jackery_solarvault.client.ble import (  # noqa: PLC0415
        BLE_AES_IV_LEN,
        decrypt_binary_notify,
        encrypt_binary_notify,
    )

    key = base64.b64decode("aHIyYzBoaDM2MTMzNjEzOA==")  # 16-byte device key
    body = b'{"cmd":107,"swEps":1}'
    plain = build_binary_frame(
        cmd=107,
        body=body,
        flags=7,
        frame_index=1,
        chunk_count=1,
    )
    blob = encrypt_binary_notify(plain, key, iv=bytes(BLE_AES_IV_LEN))
    parsed = decrypt_binary_notify(blob, key)
    assert parsed.cmd == 107  # noqa: PLR2004
    assert parsed.flags == 7  # noqa: PLR2004
    assert parsed.body == body


# ---------------------------------------------------------------------------
# String literal constants — verify the values were not accidentally changed
# ---------------------------------------------------------------------------


def test_ble_frame_string_constants_unchanged() -> None:
    """Wire-format string constants must still match smali reference values.

    The PR changed single-quoted literals to double-quoted literals.
    Values must be identical.
    """
    assert BLE_FRAME_MAGIC == "DFED"
    assert BLE_FRAME_VERSION == "0001"
    assert BLE_FRAME_PAYLOAD_MARKER == "0001"


def test_gatt_uuid_string_constants_unchanged() -> None:
    """GATT UUIDs must be exact lowercase strings, unchanged by the PR."""
    assert BLE_SERVICE_UUID == "0000bdee-0000-1000-8000-00805f9b34fb"
    assert BLE_WRITE_CHAR_UUID == "0000ee01-0000-1000-8000-00805f9b34fb"
    assert BLE_NOTIFY_CHAR_UUID == "0000ee02-0000-1000-8000-00805f9b34fb"
