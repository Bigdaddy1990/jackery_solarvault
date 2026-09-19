"""Behaviour tests for the pure BLE codec helpers.

These exercise the crypto, CRC and MTU-chunking primitives in
``client/ble.py``. The module performs no I/O, so nothing here is mocked.
Frame layout lives in ``test_ble_frames.py``.

Expected values are written as protocol literals rather than re-imported
module constants: the tests must fail if a wire value changes, not follow
it.
"""

import pytest

from custom_components.jackery_solarvault.client.ble import (
    DEFAULT_BLE_MTU,
    aes_decrypt,
    aes_encrypt,
    chunk_size_for_mtu,
    crc16_modbus,
    random_iv,
    split_body_for_mtu,
)

_KEY_AES128 = b"0123456789abcdef"
_KEY_AES256 = b"0123456789abcdef0123456789abcdef"
_IV = b"fedcba9876543210"
_IV_LEN = 16
_IV_SAMPLES = 8
_PADDED_BODY_LEN = 32

_MTU = 100
_CHUNK = 40  # _MTU minus the 60-byte frame overhead.
_DEFAULT_MTU_CHUNK = 187  # DEFAULT_BLE_MTU (247) minus the same overhead.
_MIN_USABLE_MTU = 61

_MODBUS_CHECK = 0x4B37  # CRC-16/MODBUS register for b"123456789".
_MODBUS_CHECK_WIRE = b"\x37\x4b"  # Same register, low byte first.


# --- CRC-16/MODBUS --------------------------------------------------------


def test_crc16_modbus_matches_the_canonical_vector_and_wire_order() -> None:
    """The canonical register is reproduced and serialised low byte first."""
    register = crc16_modbus(b"123456789")

    assert register == _MODBUS_CHECK
    assert register.to_bytes(2, "little") == _MODBUS_CHECK_WIRE


# --- AES-CBC/PKCS7 --------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(_KEY_AES128, id="aes-128"),
        pytest.param(_KEY_AES256, id="aes-256"),
    ],
)
def test_aes_round_trips_body_with_both_accepted_key_widths(key: bytes) -> None:
    """Both observed key widths encrypt and decrypt a JSON body losslessly."""
    plaintext = b'{"actionId":3011}'  # 17 bytes -> PKCS7 pads to 32.

    ciphertext = aes_encrypt(plaintext, key, _IV)

    assert ciphertext != plaintext
    assert len(ciphertext) == _PADDED_BODY_LEN
    assert aes_decrypt(ciphertext, key, _IV) == plaintext


@pytest.mark.parametrize(
    "bad_key",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"tooshort", id="eight-bytes"),
        pytest.param(b"x" * 24, id="aes-192-is-not-accepted"),
        pytest.param(b"x" * 33, id="over-aes-256"),
    ],
)
def test_aes_rejects_keys_that_are_not_aes128_or_aes256(bad_key: bytes) -> None:
    """Only 16- and 32-byte keys are accepted, on both directions."""
    with pytest.raises(ValueError, match="key must be one of"):
        aes_encrypt(b"payload", bad_key, _IV)
    with pytest.raises(ValueError, match="key must be one of"):
        aes_decrypt(b"x" * 16, bad_key, _IV)


@pytest.mark.parametrize(
    "bad_iv",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"short", id="five-bytes"),
        pytest.param(b"x" * 17, id="one-over-a-block"),
    ],
)
def test_aes_rejects_ivs_that_are_not_block_sized(bad_iv: bytes) -> None:
    """A non-16-byte IV is refused before the cipher is built."""
    with pytest.raises(ValueError, match="iv must be"):
        aes_encrypt(b"payload", _KEY_AES128, bad_iv)
    with pytest.raises(ValueError, match="iv must be"):
        aes_decrypt(b"x" * 16, _KEY_AES128, bad_iv)


def test_random_iv_is_block_sized_and_fresh_per_call() -> None:
    """Every generated IV is 16 bytes and distinct from its predecessors."""
    ivs = {random_iv() for _ in range(_IV_SAMPLES)}

    assert all(len(iv) == _IV_LEN for iv in ivs)
    assert len(ivs) == _IV_SAMPLES


# --- MTU chunking ---------------------------------------------------------


@pytest.mark.parametrize(
    ["mtu", "expected"],
    [
        pytest.param(_MTU, _CHUNK, id="hundred-byte-mtu"),
        pytest.param(DEFAULT_BLE_MTU, _DEFAULT_MTU_CHUNK, id="app-default-mtu"),
        pytest.param(_MIN_USABLE_MTU, 1, id="smallest-usable-mtu"),
    ],
)
def test_chunk_size_for_mtu_subtracts_the_frame_overhead(
    mtu: int, expected: int
) -> None:
    """The payload budget per frame is the negotiated MTU minus 60 bytes."""
    assert chunk_size_for_mtu(mtu) == expected


@pytest.mark.parametrize(
    "mtu",
    [
        pytest.param(60, id="exactly-the-overhead"),
        pytest.param(23, id="ble-default-mtu-is-too-small"),
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
    ],
)
def test_chunk_size_for_mtu_rejects_an_mtu_at_or_below_the_overhead(
    mtu: int,
) -> None:
    """An MTU leaving no payload room cannot carry a frame at all."""
    with pytest.raises(ValueError, match="overhead"):
        chunk_size_for_mtu(mtu)


def test_split_body_for_mtu_emits_one_envelope_for_an_empty_body() -> None:
    """An empty body still yields a single empty chunk for the writer."""
    assert split_body_for_mtu(b"", _MTU) == [b""]


def test_split_body_for_mtu_splits_on_the_chunk_boundary() -> None:
    """A long body is cut into MTU-sized chunks that rejoin losslessly."""
    body = b"x" * (_CHUNK * 2 + 5)

    chunks = split_body_for_mtu(body, _MTU)

    assert [len(chunk) for chunk in chunks] == [_CHUNK, _CHUNK, 5]
    assert b"".join(chunks) == body


def test_split_body_for_mtu_does_not_emit_a_trailing_empty_chunk() -> None:
    """A body that is an exact multiple of the chunk size has no empty tail."""
    body = b"y" * (_CHUNK * 2)

    chunks = split_body_for_mtu(body, _MTU)

    assert [len(chunk) for chunk in chunks] == [_CHUNK, _CHUNK]
    assert b"".join(chunks) == body
