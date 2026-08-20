"""Unit tests for ``custom_components.jackery_solarvault.client.ble``.

The wire-format constants and crypto come from reverse-engineered Jackery
app smali. These tests lock down the bit-level layout, the CRC reference
vector and the AES round-trip so future refactors cannot silently break
compatibility with the Jackery firmware.

No real Bluetooth I/O happens here — everything is pure Python so the
tests run on every supported platform without bleak or BlueZ.
"""

import asyncio
import base64
import json
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.client.ble import (
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
    BleBinaryFrame,
    BleFrame,
    aes_decrypt,
    aes_encrypt,
    build_binary_frame,
    build_plaintext_frame,
    chunk_size_for_mtu,
    crc16_hex,
    crc16_modbus,
    decrypt_binary_notify,
    decrypt_frame,
    encrypt_binary_notify,
    encrypt_frame,
    hex16,
    hex_decode,
    hex_encode,
    parse_hex16,
    parse_plaintext_frame,
    random_iv,
    split_body_for_mtu,
    split_payload_into_frames,
)
from custom_components.jackery_solarvault.client.ble_transport import (
    JackeryBleListener,
    _GattSession,  # noqa: PLC2701, RUF105
)
from custom_components.jackery_solarvault.const import (
    CONF_ENABLE_BLE_TRANSPORT,
    CONF_ENABLE_BLE_WRITES,
    DEFAULT_BLE_ACK_TIMEOUT_SEC,
    FIELD_BLUETOOTH_KEY,
    FIELD_CMD,
    FIELD_DEVICE_SN,
    PAYLOAD_DEVICE_META,
    PAYLOAD_SYSTEM_META,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.exceptions import ServiceValidationError

# ---------------------------------------------------------------------------
# Constants — pinned to the smali-verified literals
# ---------------------------------------------------------------------------


def test_wire_format_constants_match_smali() -> None:
    """Wire-format string literals match HomeControlFormat.smali."""
    assert BLE_FRAME_MAGIC == "DFED"
    assert BLE_FRAME_VERSION == "0001"
    assert BLE_FRAME_PAYLOAD_MARKER == "0001"
    assert BLE_AES_IV_LEN == 16
    # Both AES-128 (16 bytes) and AES-256 (32 bytes) are accepted; the
    # length is selected per-device from the base64-decoded bluetoothKey.
    # A SolarVault 3 Pro Max captured 2026-05-16 returned a 16-byte key
    # ("hr2c0hh361336138" → AES-128), so the helpers must accept that too.
    assert BLE_AES_KEY_LEN_AES128 == 16
    assert BLE_AES_KEY_LEN_AES256 == 32
    assert set(BLE_AES_KEY_LENGTHS) == {16, 32}
    # The legacy single-value alias points at AES-128 because that is the
    # observed wild-type for SolarVault.
    assert BLE_AES_KEY_LEN == BLE_AES_KEY_LEN_AES128


def test_gatt_uuids_match_smali_and_live_capture() -> None:
    """GATT service/char UUIDs match sb/v.smali and the live HA scan capture."""
    assert BLE_SERVICE_UUID == "0000bdee-0000-1000-8000-00805f9b34fb"
    assert BLE_WRITE_CHAR_UUID == "0000ee01-0000-1000-8000-00805f9b34fb"
    assert BLE_NOTIFY_CHAR_UUID == "0000ee02-0000-1000-8000-00805f9b34fb"
    assert BLE_MANUFACTURER_ID == 0x4802  # 18434 — confirmed in adv data


# ---------------------------------------------------------------------------
# Hex helpers
# ---------------------------------------------------------------------------


def test_hex16_upper_case_4_digit_format() -> None:
    """``hex16`` produces a 4-char upper-case hex string (matches ``sb/d.d``)."""
    assert hex16(0) == "0000"
    assert hex16(1) == "0001"
    assert hex16(0xBEE) == "0BEE"  # actionId 3046 = 0x0BEE
    assert hex16(0x71) == "0071"  # cmd 113 = 0x71
    assert hex16(0xFFFF) == "FFFF"


def test_hex16_rejects_out_of_range() -> None:
    """``hex16`` refuses values that do not fit into 16 bits."""
    with pytest.raises(ValueError):
        hex16(-1)
    with pytest.raises(ValueError):
        hex16(0x10000)


def test_parse_hex16_round_trips() -> None:
    """``parse_hex16`` inverts ``hex16``."""
    for value in (0, 1, 0x1234, 0xBEE, 0xFFFF):
        assert parse_hex16(hex16(value)) == value


def test_parse_hex16_rejects_wrong_width() -> None:
    """``parse_hex16`` enforces the 4-char width."""
    with pytest.raises(ValueError):
        parse_hex16("BEE")
    with pytest.raises(ValueError):
        parse_hex16("00BEE")


def test_parse_hex16_rejects_non_hex_characters_with_context() -> None:
    """``parse_hex16`` reports malformed 4-char fields with parser context."""
    with pytest.raises(ValueError, match="parse_hex16: expected hex chars"):
        parse_hex16("00GG")


def test_hex_encode_decode_round_trip() -> None:
    """``hex_encode`` and ``hex_decode`` are inverse for arbitrary bytes."""
    data = bytes(range(256))
    assert hex_decode(hex_encode(data)) == data


# ---------------------------------------------------------------------------
# CRC-16 (Modbus) — pin the reference vector
# ---------------------------------------------------------------------------


def test_crc16_modbus_reference_vector() -> None:
    """Standard Modbus CRC-16 of ``"123456789"`` is ``0x4B37``."""
    # https://crccalc.com — CRC-16/MODBUS (poly 0xA001, init 0xFFFF, reflected).
    assert crc16_modbus(b"123456789") == 0x4B37


def test_crc16_hex_is_4_chars_upper() -> None:
    """``crc16_hex`` returns the CRC as a 4-char upper-case hex string."""
    assert crc16_hex(b"123456789") == "4B37"


# ---------------------------------------------------------------------------
# AES-256-CBC-PKCS7
# ---------------------------------------------------------------------------


def test_aes_round_trip_with_deterministic_iv_aes256() -> None:
    """AES-256 encrypt + decrypt with a fixed IV recovers the plaintext."""
    key = bytes(range(BLE_AES_KEY_LEN_AES256))
    iv = bytes(BLE_AES_IV_LEN)
    plaintext = b"Hello, Jackery!" * 4
    ciphertext = aes_encrypt(plaintext, key, iv)
    assert aes_decrypt(ciphertext, key, iv) == plaintext


def test_aes_round_trip_with_aes128_key_observed_in_the_wild() -> None:
    """AES-128 with the SolarVault-shaped 16-byte key round-trips too.

    Pinned input is the actual ``bluetoothKey`` captured 2026-05-16 from
    a SolarVault 3 Pro Max: ``base64.b64decode("aHIyYzBoaDM2MTMzNjEzOA==")``
    → ``b"hr2c0hh361336138"``. This is the regression that motivated
    accepting both key lengths.
    """
    key = base64.b64decode("aHIyYzBoaDM2MTMzNjEzOA==")
    assert len(key) == BLE_AES_KEY_LEN_AES128 == 16
    iv = bytes(BLE_AES_IV_LEN)
    plaintext = b"DFED0001000100010BEE007100010000"
    ciphertext = aes_encrypt(plaintext, key, iv)
    assert aes_decrypt(ciphertext, key, iv) == plaintext


def test_aes_rejects_wrong_key_or_iv_length() -> None:
    """Length validation catches caller mistakes before they hit OpenSSL."""
    # Reject key lengths that are neither AES-128 nor AES-256.
    for bad_key_len in (15, 17, 24, 31, 33, 64):
        with pytest.raises(ValueError):
            aes_encrypt(b"x", b"\x00" * bad_key_len, b"\x00" * 16)
        with pytest.raises(ValueError):
            aes_decrypt(b"x", b"\x00" * bad_key_len, b"\x00" * 16)
    # Reject wrong IV lengths.
    with pytest.raises(ValueError):
        aes_encrypt(b"x", b"\x00" * 32, b"\x00" * 15)
    with pytest.raises(ValueError):
        aes_decrypt(b"x", b"\x00" * 32, b"\x00" * 17)


def test_random_iv_returns_fresh_16_byte_values() -> None:
    """``random_iv`` returns 16 bytes that differ between calls."""
    iv1 = random_iv()
    iv2 = random_iv()
    assert len(iv1) == BLE_AES_IV_LEN
    assert len(iv2) == BLE_AES_IV_LEN
    assert iv1 != iv2


# ---------------------------------------------------------------------------
# Plaintext frame builder / parser
# ---------------------------------------------------------------------------


def test_build_plaintext_frame_smali_layout() -> None:
    """Verify the exact frame string against the smali format string.

    The string must match ``"DFED" + "0001" + 4×hex16(idx,cnt,actionId,bleCmd)
    + "0001" + hex16(len) + chunk_hex`` exactly, byte by byte.
    """  # ruff: ignore[ambiguous-unicode-character-docstring]
    frame = BleFrame(
        frame_index=1,
        chunk_count=1,
        action_id=0x0BEE,  # 3046
        ble_cmd=0x0071,  # 113
        chunk_payload=b'{"enable":1}',
    )
    text = build_plaintext_frame(frame)
    expected = (
        "DFED"  # magic
        "0001"  # version
        "0001"  # frame_index
        "0001"  # chunk_count
        "0BEE"  # action_id 3046
        "0071"  # ble_cmd 113
        "0001"  # payload marker
        "000C"  # chunk_len 12 bytes
        "7B22656E61626C65223A317D"  # '{"enable":1}' hex
    )
    assert text == expected


def test_parse_plaintext_frame_inverts_builder() -> None:
    """Builder + parser round-trips frame metadata + payload bytes."""
    for chunk in (b"", b"x", b"hello", bytes(range(64))):
        frame = BleFrame(
            frame_index=3,
            chunk_count=7,
            action_id=3019,
            ble_cmd=120,
            chunk_payload=chunk,
        )
        text = build_plaintext_frame(frame)
        assert parse_plaintext_frame(text) == frame


def test_parse_plaintext_frame_rejects_bad_magic_or_marker() -> None:
    """Unexpected magic or payload marker raises ``ValueError``."""
    valid = build_plaintext_frame(
        BleFrame(
            frame_index=1,
            chunk_count=1,
            action_id=1,
            ble_cmd=107,
            chunk_payload=b"",
        )
    )
    with pytest.raises(ValueError):
        parse_plaintext_frame("BEEF" + valid[4:])
    with pytest.raises(ValueError):  # ruff: ignore[pytest-raises-with-multiple-statements]
        # Corrupt the payload marker (position after header fields).
        broken = valid[:24] + "BEEF" + valid[28:]
        parse_plaintext_frame(broken)


def test_parse_plaintext_frame_detects_truncation() -> None:
    """Truncated payload raises rather than silently returning short data."""
    valid = build_plaintext_frame(
        BleFrame(
            frame_index=1,
            chunk_count=1,
            action_id=1,
            ble_cmd=107,
            chunk_payload=b"deadbeef",
        )
    )
    with pytest.raises(ValueError):
        parse_plaintext_frame(valid[:-4])


# ---------------------------------------------------------------------------
# Full encrypt/decrypt pipeline
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_round_trip_recovers_frame_aes256() -> None:
    """``encrypt_frame`` + ``decrypt_frame`` recovers the original BleFrame."""
    key = bytes.fromhex("00112233445566778899AABBCCDDEEFF" * 2)
    frame = BleFrame(
        frame_index=1,
        chunk_count=1,
        action_id=3046,
        ble_cmd=113,
        chunk_payload=(
            b'{"enable":1,"ip":"192.168.2.212","port":1883,'
            b'"userName":"mqtt_user","password":"12345678","token":""}'
        ),
    )
    blob = encrypt_frame(frame, key, iv=bytes(BLE_AES_IV_LEN), random16=0x1234)
    assert blob[:BLE_AES_IV_LEN] == bytes(BLE_AES_IV_LEN)
    parsed = decrypt_frame(blob, key)
    assert parsed == frame


def test_encrypt_decrypt_round_trip_with_solarvault_aes128_key() -> None:
    """End-to-end frame round-trip with the captured 16-byte device key."""
    key = base64.b64decode("aHIyYzBoaDM2MTMzNjEzOA==")
    frame = BleFrame(
        frame_index=1,
        chunk_count=1,
        action_id=3019,  # READ_SYSTEM_INFO
        ble_cmd=120,
        chunk_payload=b"{}",
    )
    blob = encrypt_frame(frame, key, iv=bytes(BLE_AES_IV_LEN), random16=0xABCD)
    parsed = decrypt_frame(blob, key)
    assert parsed == frame


def test_encrypt_frame_uses_random_iv_when_omitted() -> None:
    """Each call without ``iv=`` produces a fresh IV (no nonce reuse)."""
    key = bytes(BLE_AES_KEY_LEN_AES128)
    frame = BleFrame(
        frame_index=1,
        chunk_count=1,
        action_id=3019,
        ble_cmd=120,
        chunk_payload=b"{}",
    )
    blob1 = encrypt_frame(frame, key)
    blob2 = encrypt_frame(frame, key)
    assert blob1[:BLE_AES_IV_LEN] != blob2[:BLE_AES_IV_LEN]


def test_decrypt_rejects_crc_tampering() -> None:
    """Flipping a bit in the ciphertext trips the CRC check on decrypt."""
    key = bytes(BLE_AES_KEY_LEN_AES128)
    frame = BleFrame(
        frame_index=1,
        chunk_count=1,
        action_id=3019,
        ble_cmd=120,
        chunk_payload=b"{}",
    )
    blob = bytearray(encrypt_frame(frame, key, iv=bytes(BLE_AES_IV_LEN)))
    blob[-1] ^= 0x01
    with pytest.raises(ValueError):
        decrypt_frame(bytes(blob), key)


# ---------------------------------------------------------------------------
# Chunking helper
# ---------------------------------------------------------------------------


def test_chunk_size_matches_smali_formula() -> None:
    """``chunk_size_for_mtu(mtu) == mtu - 60`` exactly."""
    assert chunk_size_for_mtu(247) == 187
    assert chunk_size_for_mtu(100) == 40
    assert chunk_size_for_mtu(61) == 1


def test_chunk_size_refuses_too_small_mtu() -> None:
    """MTU at or below the 60-byte overhead is rejected."""
    with pytest.raises(ValueError):
        chunk_size_for_mtu(60)
    with pytest.raises(ValueError):
        chunk_size_for_mtu(0)


def test_split_payload_emits_correct_number_of_frames() -> None:
    """A 500-byte payload at MTU=247 splits into ceil(500/187)=3 chunks."""
    payload = bytes(range(256)) + bytes(range(244))  # exactly 500 bytes
    frames = split_payload_into_frames(
        payload,
        action_id=3019,
        ble_cmd=120,
        mtu=247,
    )
    assert len(frames) == 3
    assert frames[0].frame_index == 1
    assert frames[0].chunk_count == 3
    assert frames[-1].frame_index == 3
    assert b"".join(f.chunk_payload for f in frames) == payload
    # All but the last chunk are at the MTU-derived max length.
    for f in frames[:-1]:
        assert len(f.chunk_payload) == chunk_size_for_mtu(247)
    assert len(frames[-1].chunk_payload) <= chunk_size_for_mtu(247)


def test_split_payload_handles_empty_payload() -> None:
    """An empty payload still produces one frame so query messages round-trip."""
    frames = split_payload_into_frames(b"", action_id=3046, ble_cmd=113, mtu=247)
    assert len(frames) == 1
    assert frames[0].chunk_count == 1
    assert frames[0].chunk_payload == b""


# ---------------------------------------------------------------------------
# Live-format binary notify decoder (device → app on char 0xEE02)
# ---------------------------------------------------------------------------


_LIVE_KEY_B64 = "aHIyYzBoaDM2MTMzNjEzOA=="  # 16-byte AES-128 key from device

_LIVE_NOTIFY_SAMPLES: tuple[tuple[str, int, int, str], ...] = (
    # (raw_hex, expected_cmd, expected_body_len, first_body_byte_marker)
    # Captured 2026-05-16 from SolarVault 3 Pro Max via ESPHome BLE proxy.
    (
        (
            "32373731383339313431373738393000"
            "e85350bc2bc69eb244252c5745eb0a619f341bfa5a92b34c3be3786db38a4ebf"
            "2d578615198d49eea423bf07199c66b9118a57c33b027a71be688bd64d36917e"
            "f39233a369ccd53a9eccbdf712a901961f8fd9b555f08dc75320909403a7c442"
            "d63745bc549382cabc227ef031ed865645aeda679dbfc027bf1ac1c2dff827b3"
            "3c9236e7baf89ccf97c910defa78e356d409dad3c9b5d3c1fdaaed8286334869"
            "ed300b6b056b37b1866b1fea8196d65ce839a5f46f3c42d40f17e233ea7dfbdf"
            "97b8efca02b6722050998354402ee58c70afcfdc33ee29b220b251828d46a4ab"
            "f7b862ec34ef1473d2ba21c34564cb241470a85f3eef7a9f409c327fd8ac0e13"
            "8f17f38446261f24974099a3e72dbe775395b21baede73017a3db60f519ac5a3"
            "d41ab2aba9b013e754ed4a0d0a666442"
        ),
        107,  # DevicePropertyChange
        272,
        "{",
    ),
    (
        (
            "3134373339363034343131373738390030b643e9f3747cb075dabffa3115ecb0"
            "261ed615b0a5e0ec7c767ebca1b0b127a744947c40497e2d5f2e1e579d206147"
            "320499d1e235be30e08251195d8a7d6167dbb5edc976f237ec80d1137ab31f25"
            "d56acc46040630775e163c84f9b1cf1ae92ebad5f7fc2c68be73daca864a1c6c"
            "6240f285a4b5782cba46870dcafb5f52c581dcc868b5541ddd221516b9363ae7"
        ),
        111,  # ControlSubDevice (sub-device incremental property)
        118,
        "{",
    ),
    (
        (
            "393936333539323634313737383930006852f3235ffc0dbd0586c39e2237a31a"
            "c8d0d400d3286e94dca1adeb3de5db4c5f8d251fcfc16549af868bdd29e12c17"
            "d682bd1c2e0eb6ea0fdd508c18d2c41880a737b5e554ad218db53182ec08ed59"
            "d0e245afc3cffd959eb463a07a658fc25e83c7e0f45c444345b7d05be3bee98a"
        ),
        111,
        78,
        "{",
    ),
)


def test_decrypt_binary_notify_recovers_real_telemetry() -> None:
    """The live binary decoder reproduces real device JSON bodies.

    Pinned inputs are wire-bytes captured 2026-05-16 from a SolarVault 3
    Pro Max via the ESPHome BLE proxy. Decoding them recovers the JSON
    telemetry that the integration would otherwise have to wait for from
    the cloud.
    """
    key = base64.b64decode(_LIVE_KEY_B64)
    assert len(key) == BLE_AES_KEY_LEN_AES128

    for raw_hex, expected_cmd, expected_body_len, body_marker in _LIVE_NOTIFY_SAMPLES:
        raw = bytes.fromhex(raw_hex)
        frame = decrypt_binary_notify(raw, key)
        assert isinstance(frame, BleBinaryFrame)
        assert frame.cmd == expected_cmd, (frame.cmd, expected_cmd)
        assert frame.frame_index == 1
        assert frame.chunk_count == 1
        assert len(frame.body) == expected_body_len
        body_text = frame.body.decode("utf-8")
        assert body_text.startswith(body_marker)
        # The bodies are always JSON dicts that include the ``cmd`` field
        # mirroring the binary header — the integration's sink strips it.
        payload = json.loads(body_text)
        assert isinstance(payload, dict)
        assert payload.get("cmd") == expected_cmd
        # Trailer is always 4 bytes — assumed CRC; opaque for now.
        assert len(frame.trailer) == 4


def test_decrypt_binary_notify_rejects_short_frame() -> None:
    """Frames smaller than ``IV + header + trailer`` raise ``ValueError``."""
    key = base64.b64decode(_LIVE_KEY_B64)
    with pytest.raises(ValueError):
        decrypt_binary_notify(b"too short", key)


def test_build_then_decrypt_binary_frame_round_trips() -> None:
    """Encode a frame, encrypt it, then run the live decoder to recover it.

    Pins the symmetry between :func:`build_binary_frame` /
    :func:`encrypt_binary_notify` and the read-path
    :func:`decrypt_binary_notify`. The trailer is opaque (see
    :class:`.ble.BleBinaryFrame` docstring); the round-trip test uses
    explicit zero bytes that the decoder simply passes through.
    """
    key = base64.b64decode(_LIVE_KEY_B64)
    body = b'{"cmd":107,"swEps":1}'
    plain = build_binary_frame(cmd=107, body=body, flags=42, security=0x1234)
    blob = encrypt_binary_notify(plain, key, iv=bytes(BLE_AES_IV_LEN))
    parsed = decrypt_binary_notify(blob, key)
    assert parsed.cmd == 107
    assert parsed.flags == 42
    assert parsed.frame_index == 1
    assert parsed.chunk_count == 1
    assert parsed.body == body
    assert parsed.trailer[:2] == b"\x12\x34"
    assert parsed.trailer[2:] != b"\x00\x00"


def test_listener_async_send_command_returns_false_without_client() -> None:
    """``async_send_command`` falls back to ``False`` when no GATT session exists.

    This is the contract callers (coordinator setter routing) rely on to
    decide whether to fall back to the cloud-MQTT pipeline when the BLE
    proxy hasn't (re-)connected yet.
    """

    async def _run() -> None:
        """Call async_send_command with no active GATT session and expect False."""
        listener = _build_bare_listener(b"x" * 16)
        sent = await listener.async_send_command(
            "573702884982521856",
            msg_id=3022,
            ble_msg_type=107,
            body=b'{"swEps":1}',
        )
        assert sent is False

    asyncio.run(_run())


def test_listener_async_send_command_writes_through_fake_client() -> None:
    """The writer path encrypts the right body and writes to char 0xEE01.

    Drives ``async_send_command`` against an in-memory client double that
    captures the ``write_gatt_char`` invocation, then decrypts the captured
    blob with the same key to confirm it round-trips through the live
    binary decoder.
    """
    captured: dict[str, object] = {}

    class _FakeClient:
        is_connected = True

        async def write_gatt_char(  # ruff: ignore[no-self-use]
            self, uuid: str, blob: bytes, *, response: bool
        ) -> None:
            """Record a GATT characteristic write attempt into `captured`."""
            captured["uuid"] = uuid
            captured["blob"] = bytes(blob)
            captured["response"] = response

    async def _run() -> None:
        """Send over a fake GATT session and assert the frame round-trips.

        The wire header carries the actionId in ``flags`` and the BLE
        message type in ``cmd`` (source-backed home-frame mapping), so
        ``msg_id=3022`` / ``ble_msg_type=107`` must come back as
        ``flags==3022`` / ``cmd==107`` after decryption.
        """
        key = base64.b64decode(_LIVE_KEY_B64)
        listener = _build_bare_listener(key)
        _attach_session(listener, "573702884982521856", _FakeClient())
        ok = await listener.async_send_command(
            "573702884982521856",
            msg_id=3022,
            ble_msg_type=107,
            body=b'{"swEps":1}',
        )
        assert ok is True
        assert captured["uuid"] == BLE_WRITE_CHAR_UUID
        assert captured["response"] is False
        parsed = decrypt_binary_notify(cast("bytes", captured["blob"]), key)
        assert parsed.cmd == 107
        assert parsed.flags == 3022
        assert parsed.body == b'{"swEps":1}'

    asyncio.run(_run())


def test_build_binary_frame_rejects_oversized_fields() -> None:
    """Every header field is range-checked before encryption."""
    with pytest.raises(ValueError):
        build_binary_frame(cmd=107, body=b"x", frame_index=0)
    with pytest.raises(ValueError):
        build_binary_frame(cmd=107, body=b"x", chunk_count=0x1_0000)
    with pytest.raises(ValueError):
        build_binary_frame(cmd=107, body=b"x", flags=-1)
    with pytest.raises(ValueError):
        build_binary_frame(cmd=0x1_0000, body=b"x")
    with pytest.raises(ValueError):
        build_binary_frame(cmd=107, body=b"x" * 0x1_0001)
    with pytest.raises(ValueError):
        build_binary_frame(cmd=107, body=b"x", trailer=b"\x00\x00\x00")


# ---------------------------------------------------------------------------
# Integration plumbing — coordinator + manifest + config option
# ---------------------------------------------------------------------------


def test_manifest_declares_bluetooth_matcher_and_dependency() -> None:
    """Assert the integration manifest declares the BLE service matcher, manufacturer, dependency, and requirement.

    Checks that manifest.json contains a bluetooth service matcher with `service_uuid` equal to BLE_SERVICE_UUID, a `manufacturer_id` equal to BLE_MANUFACTURER_ID, includes "bluetooth" in `after_dependencies`, and lists a requirement that starts with "bleak-retry-connector".
    """
    import json  # ruff: ignore[import-outside-top-level]
    from pathlib import Path  # ruff: ignore[import-outside-top-level]

    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "custom_components" / "jackery_solarvault" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    matchers = manifest.get("bluetooth", [])
    assert any(
        m.get("service_uuid", "").lower() == BLE_SERVICE_UUID for m in matchers
    ), matchers
    assert any(m.get("manufacturer_id") == BLE_MANUFACTURER_ID for m in matchers), (
        matchers
    )
    assert "bluetooth" in (manifest.get("after_dependencies") or [])
    assert any(
        req.startswith("bleak-retry-connector")
        for req in manifest.get("requirements", [])
    ), manifest.get("requirements")


def test_const_exposes_ble_option_and_field() -> None:
    """Config option + bluetoothKey field constants exist in const.py."""
    from custom_components.jackery_solarvault import const  # noqa: PLC0415, RUF105

    assert const.CONF_ENABLE_BLE_TRANSPORT == "enable_ble_transport"
    assert const.DEFAULT_ENABLE_BLE_TRANSPORT is False
    assert const.CONF_ENABLE_BLE_WRITES == "enable_ble_writes"
    assert const.DEFAULT_ENABLE_BLE_WRITES is False
    assert const.FIELD_BLUETOOTH_KEY == "bluetoothKey"


def test_coordinator_surfaces_ble_diagnostic_hooks() -> None:
    """Coordinator class exposes the BLE listener / diagnostics helpers."""
    from custom_components.jackery_solarvault.coordinator import (  # noqa: PLC0415, RUF105
        JackerySolarVaultCoordinator,
    )

    for attr in (
        "device_bluetooth_key",
        "async_start_ble_transport",
        "async_send_ble_command",
        "ble_observations",
    ):
        assert hasattr(JackerySolarVaultCoordinator, attr), attr


def test_ble_transport_module_exports_listener() -> None:
    """``client.ble_transport`` exports the listener + observation classes."""
    from custom_components.jackery_solarvault.client import (  # noqa: PLC0415, RUF105
        ble_transport,
    )

    for symbol in (
        "JackeryBleListener",
        "BleFrameObservation",
        "BleListenerStats",
    ):
        assert hasattr(ble_transport, symbol), symbol


def test_ble_listener_async_stop_cancels_runner_tasks_promptly() -> None:
    """``async_stop()`` cancels stuck runners without blocking shutdown.

    The runner sits in ``asyncio.wait_for(_stop_event.wait(), 30s)`` while
    backing off after a disconnect. HA's shutdown logs "tasks still
    pending" if cancellation does not take effect quickly. This test
    pins that behaviour: a task parked at the backoff wait must be done
    well within the listener's own ``_STOP_TIMEOUT_SEC`` budget after
    ``async_stop()`` is awaited.
    """

    async def _runner() -> None:
        listener = _build_bare_listener()

        async def _stuck() -> None:
            # Mimic the real runner's backoff wait. Without
            # cancellation propagation this would park 30s.
            """Simulate a runner task that waits for the listener stop event with a 30-second backoff.

            This coroutine blocks on listener._stop_event until it is set or the 30.0 second timeout elapses,
            and is intended for tests that assert prompt cancellation of long-running runner tasks.
            """
            await asyncio.wait_for(listener._stop_event.wait(), timeout=30.0)  # ruff: ignore[private-member-access]

        task = asyncio.create_task(_stuck())
        listener._connections["dev"] = task  # ruff: ignore[private-member-access]
        # Give the loop a tick so the task actually parks at the wait.
        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()
        before = loop.time()
        await listener.async_stop()
        elapsed = loop.time() - before
        assert task.done(), "stuck runner task was not cancelled"
        # Must be far under the 5 s hard stop budget; in practice this
        # finishes in single-digit milliseconds.
        assert elapsed < 1.0, f"async_stop took {elapsed:.3f}s — too slow"

    asyncio.run(_runner())


def test_coordinator_send_ble_command_requires_write_option() -> None:
    """The public BLE sender is inert until both BLE options are enabled."""
    import asyncio  # ruff: ignore[import-outside-top-level]

    from custom_components.jackery_solarvault.coordinator import (  # ruff: ignore[import-outside-top-level]
        JackerySolarVaultCoordinator,
    )

    class _Entry:
        data: dict[str, object] = {}
        options = {
            CONF_ENABLE_BLE_TRANSPORT: True,
            CONF_ENABLE_BLE_WRITES: False,
        }

    class _Listener:
        async def async_send_command(self, *_args: object, **_kwargs: object) -> bool:  # ruff: ignore[no-self-use]
            """Stub method that fails immediately to indicate the BLE listener must not be invoked.

            Raises:
                AssertionError: Always raised with the message "BLE listener must not be called".
            """
            raise AssertionError("BLE listener must not be called")

    async def _run() -> None:
        """Verify that async_send_ble_command returns False when invoked on a coordinator stub with no BLE listener.

        Constructs a minimal JackerySolarVaultCoordinator instance, sets up a placeholder config entry and listener, calls `async_send_ble_command` for device "dev1" with `cmd=107` and a matching body, and asserts the call reports that the BLE send did not occur (`False`).
        """
        self = cast(
            "Any",
            JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator),
        )
        self.entry = _Entry()
        self._ble_listener = _Listener()
        sent = await JackerySolarVaultCoordinator.async_send_ble_command(
            self,
            "dev1",
            cmd=107,
            body={"cmd": 107},
        )
        assert sent is False

    asyncio.run(_run())


def test_ble_observations_include_known_devices_without_frames() -> None:
    """BLE diagnostics should not be empty before the first advertisement."""
    from custom_components.jackery_solarvault.coordinator import (  # ruff: ignore[import-outside-top-level]
        JackerySolarVaultCoordinator,
    )

    class _Entry:
        data: dict[str, object] = {}
        options = {
            CONF_ENABLE_BLE_TRANSPORT: True,
            CONF_ENABLE_BLE_WRITES: False,
        }

    class _Listener:
        def all_stats(self) -> dict[str, object]:  # ruff: ignore[no-self-use]
            """Return a snapshot of listener statistics as a mapping of statistic names to values.

            The returned dictionary contains current monitoring fields (counters, timestamps, and optional diagnostic strings) keyed by their descriptive names; callers may read but should not assume mutability of internal state.

            Returns:
                stats (dict[str, object]): A snapshot mapping statistic names to their current values.
            """
            return {}

        def mtu_for_device(self, device_id: str) -> int:  # ruff: ignore[no-self-use]
            """Get the negotiated MTU size for the specified device.

            Returns:
                int: MTU size in bytes for the device.
            """
            assert device_id == "dev1"
            return 517

    coordinator = cast(
        "Any",
        JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator),
    )
    coordinator.entry = _Entry()
    coordinator._device_index = {"dev1": {}}  # ruff: ignore[private-member-access]
    coordinator._ble_listener = None  # ruff: ignore[private-member-access]

    idle = JackerySolarVaultCoordinator.ble_observations(coordinator)["dev1"]
    assert idle["enabled"] is True
    # Single-gate design: enabling the BLE transport enables writes with
    # it (the CONF_ENABLE_BLE_WRITES second gate was removed on purpose —
    # see coordinator._ble_writes_enabled).
    assert idle["write_enabled"] is True
    assert idle["running"] is False
    assert idle["frames_decoded"] == 0
    assert idle["mtu"] is None

    coordinator._ble_listener = _Listener()  # ruff: ignore[private-member-access]
    running = JackerySolarVaultCoordinator.ble_observations(coordinator)["dev1"]
    assert running["enabled"] is True
    assert running["running"] is True
    assert running["frames_decoded"] == 0
    assert running["mtu"] == 517


def test_coordinator_send_ble_command_json_compacts_dict_body() -> None:
    """Dict service bodies are compact-JSON encoded before GATT write."""
    import asyncio  # ruff: ignore[import-outside-top-level]

    from custom_components.jackery_solarvault.coordinator import (  # ruff: ignore[import-outside-top-level]
        JackerySolarVaultCoordinator,
    )

    class _Entry:
        data: dict[str, object] = {}
        options = {
            CONF_ENABLE_BLE_TRANSPORT: True,
            CONF_ENABLE_BLE_WRITES: True,
        }

    captured: dict[str, object] = {}

    class _Listener:
        async def async_send_command(  # ruff: ignore[no-self-use]
            self,
            device_id: str,
            *,
            msg_id: int,
            ble_msg_type: int,
            body: bytes,
            timeout_sec: float = 10.0,
            wait_for_ack: bool = False,
            ack_timeout_sec: float = 5.0,
            mtu_override: int | None = None,
        ) -> bool:
            """Capture the listener-level write parameters."""
            captured["device_id"] = device_id
            captured["msg_id"] = msg_id
            captured["ble_msg_type"] = ble_msg_type
            captured["body"] = body
            captured["wait_for_ack"] = wait_for_ack
            captured["ack_timeout_sec"] = ack_timeout_sec
            captured["mtu_override"] = mtu_override
            return True

    async def _run() -> None:
        """Verify async_send_ble_command JSON-compacts dict bodies.

        The wrapper keeps the wire-header parameter names (``cmd`` is the
        BLE message type, ``flags`` carries the actionId) and must map
        them onto the listener's ``ble_msg_type``/``msg_id``. Only
        catalog-known (actionId, bleMsgType) pairs pass the gate, so the
        test uses the real EPS pair (3022, 107).
        """
        self = cast(
            "Any",
            JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator),
        )
        self.entry = _Entry()
        self._ble_listener = _Listener()
        sent = await JackerySolarVaultCoordinator.async_send_ble_command(
            self,
            "dev1",
            cmd=107,
            body={"cmd": 107, "swEps": 1},
            flags=3022,
        )
        assert sent is True

        assert captured == {
            "device_id": "dev1",
            "msg_id": 3022,
            "ble_msg_type": 107,
            "body": b'{"cmd":107,"swEps":1}',
            "wait_for_ack": False,
            "ack_timeout_sec": DEFAULT_BLE_ACK_TIMEOUT_SEC,
            "mtu_override": None,
        }

    asyncio.run(_run())


def test_coordinator_ble_first_leaves_cmd_zero_mqtt_only() -> None:
    """cmd=0 actions are not sent through the experimental BLE writer."""
    captured: dict[str, object] = {}

    async def _run() -> None:
        self = cast(
            "Any",
            JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator),
        )

        async def _send_ble(*_args: object, **_kwargs: object) -> bool:  # ruff: ignore[unused-async]
            """Guard that prevents attempting BLE sends for command 0.

            Raises:
                AssertionError: Always raised with message "cmd=0 must not attempt BLE" to indicate command 0 must not be sent over BLE.
            """
            raise AssertionError("cmd=0 must not attempt BLE")

        async def _publish_mqtt(  # ruff: ignore[unused-async]
            device_id: str,
            *,
            message_type: str,
            action_id: int,
            cmd: int,
            body_fields: dict[str, object],
            cloud_attempt: object | None = None,
            ensure_mqtt: bool = True,
        ) -> None:
            """Publish a device-specific command message to MQTT with the given action, command, and body fields.

            Parameters:
                device_id (str): Unique identifier of the target device.
                message_type (str): MQTT message topic suffix or type identifier used for routing.
                action_id (int): Protocol action identifier associated with this message.
                cmd (int): Numeric command identifier included in the message body.
                body_fields (dict[str, object]): Additional payload fields to include in the MQTT message.
                ensure_mqtt (bool): If True, ensure the message is delivered via MQTT (fallback behavior may be enforced); if False, allow non-MQTT delivery paths.
            """
            del cloud_attempt
            captured["device_id"] = device_id
            captured["message_type"] = message_type
            captured["action_id"] = action_id
            captured["cmd"] = cmd
            captured["body_fields"] = body_fields
            captured["ensure_mqtt"] = ensure_mqtt

        self.async_send_ble_command = _send_ble
        self._async_publish_command = _publish_mqtt
        await JackerySolarVaultCoordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
            self,
            "dev1",
            message_type="SendWeatherAlert",
            action_id=3040,
            cmd=0,
            body_fields={"wpc": 30},
        )
        assert captured == {
            "device_id": "dev1",
            "message_type": "SendWeatherAlert",
            "action_id": 3040,
            "cmd": 0,
            "body_fields": {"wpc": 30},
            "ensure_mqtt": True,
        }


def test_command_body_for_transport_parses_cmd_defensively() -> None:
    """Transport command bodies accept integral text and reject bad values."""
    from custom_components.jackery_solarvault.coordinator import (  # noqa: PLC0415, RUF105
        JackerySolarVaultCoordinator,
    )

    assert JackerySolarVaultCoordinator._command_body_for_transport(  # ruff: ignore[private-member-access]
        {"swEps": 1},
        cmd="107.0",
    ) == {"swEps": 1, FIELD_CMD: 107}
    assert JackerySolarVaultCoordinator._command_body_for_transport(  # ruff: ignore[private-member-access]
        {"wpc": 30},
        cmd=0,
    ) == {"wpc": 30}

    for bad_cmd in (True, float("nan"), "107.5"):
        with pytest.raises(ValueError, match="cmd must be an integer"):
            JackerySolarVaultCoordinator._command_body_for_transport(  # ruff: ignore[private-member-access]
                {},
                cmd=bad_cmd,
            )


def test_send_ble_service_body_accepts_dict_and_json_string() -> None:
    """Service body normalization accepts the two user-facing input shapes."""
    from custom_components.jackery_solarvault import services  # noqa: PLC0415, RUF105

    assert services._ble_body_from_service({"cmd": 107}, "dev1") == {"cmd": 107}  # ruff: ignore[private-member-access]
    assert services._ble_body_from_service('{"cmd":107,"swEps":1}', "dev1") == {  # ruff: ignore[private-member-access]
        "cmd": 107,
        "swEps": 1,
    }
    with pytest.raises(ServiceValidationError):
        services._ble_body_from_service("[1,2,3]", "dev1")  # ruff: ignore[private-member-access]
    with pytest.raises(ServiceValidationError):
        services._ble_body_from_service("{bad json", "dev1")  # ruff: ignore[private-member-access]


def test_device_bluetooth_key_falls_back_to_system_meta() -> None:
    """Live HTTP capture puts the AES key at the system level, not per-device.

    The 2026-05-16 ``/v1/device/system/list`` capture from a SolarVault 3
    Pro Max had ``data[].bluetoothKey == "aHIyYzBoaDM2MTMzNjEzOA=="`` at
    the system level and ``data[].devices[0].bluetoothKey == null`` for
    the main device. Before this regression test the integration only
    looked at the per-device slot and silently failed to decrypt BLE
    notify frames with ``decode_error="no bluetoothKey for device"`` —
    visible in the BLE-transport diagnostics export from that capture.
    """
    self = cast(
        "Any",
        JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator),
    )
    self.data = None
    self._device_index = {
        "573702884982521856": {
            PAYLOAD_DEVICE_META: {
                # Key is null at device level — matches the live HTTP shape.
                FIELD_BLUETOOTH_KEY: None,
            },
            PAYLOAD_SYSTEM_META: {
                # System-level key — base64-decoded "hr2c0hh361336138".
                FIELD_BLUETOOTH_KEY: "aHIyYzBoaDM2MTMzNjEzOA==",
            },
        }
    }
    key = JackerySolarVaultCoordinator.device_bluetooth_key(self, "573702884982521856")
    assert key == b"hr2c0hh361336138"


def test_device_bluetooth_key_prefers_device_meta_when_both_set() -> None:
    """A per-device key (newer firmware?) wins over the system-level key.

    Future firmware may migrate the key down to the per-device slot. The
    lookup picks the most specific value so the integration stays
    forwards-compatible.
    """
    self = cast(
        "Any",
        JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator),
    )
    self.data = None
    # Two distinct valid keys so we can tell which one came back.
    device_key = base64.b64encode(b"D" * 16).decode("ascii")
    system_key = base64.b64encode(b"S" * 16).decode("ascii")
    self._device_index = {
        "dev1": {
            PAYLOAD_DEVICE_META: {FIELD_BLUETOOTH_KEY: device_key},
            PAYLOAD_SYSTEM_META: {FIELD_BLUETOOTH_KEY: system_key},
        }
    }
    assert JackerySolarVaultCoordinator.device_bluetooth_key(self, "dev1") == b"D" * 16


def test_serial_resolver_strips_http_prefix_letter() -> None:
    """BLE-broadcast serial maps to its HTTP counterpart even with a model letter.

    Live capture 2026-05-16: HTTP returns ``HR2C04000280HH3`` while the
    BLE manufacturer-data field carries ``R2C04000280HH3`` (no leading
    H). The coordinator's ``device_id_for_ble_serial`` must accept the
    BLE form as a suffix of the HTTP form.
    """
    # Build a stub instance just enough to exercise the lookup. We bypass
    # __init__ because the coordinator constructor pulls in HA fixtures
    # that the static-test harness can't load on Windows.
    self = cast(
        "Any",
        JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator),
    )
    self._device_index = {
        "573702884982521856": {
            PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: "HR2C04000280HH3"},
        }
    }

    assert (
        JackerySolarVaultCoordinator.device_id_for_ble_serial(self, "R2C04000280HH3")
        == "573702884982521856"
    )
    # Exact match also works (future firmware may align them).
    assert (
        JackerySolarVaultCoordinator.device_id_for_ble_serial(self, "HR2C04000280HH3")
        == "573702884982521856"
    )
    # Unknown serial returns None so the listener can fall through quietly.
    assert (
        JackerySolarVaultCoordinator.device_id_for_ble_serial(self, "DOESNOTEXIST")
        is None
    )


# ---------------------------------------------------------------------------
# BLE write-path ACK correlation
# ---------------------------------------------------------------------------


class _HassStub:
    """Minimal hass double for listener tests.

    Only ``loop`` is ever touched on the notify/write paths exercised
    here; connect and keep-alive paths never run against this stub.
    """

    @property
    def loop(self) -> object:
        return asyncio.get_running_loop()


def _build_bare_listener(key: bytes | None = None) -> JackeryBleListener:
    """Return a real JackeryBleListener wired with inert callbacks.

    Uses the actual constructor so the test attribute set can never
    drift from the implementation; tests attach fake GATT sessions via
    :func:`_attach_session` instead of patching privates.
    """

    async def _sink(_device_id: str, _observation: object) -> bool:  # ruff: ignore[unused-async]
        return True

    return JackeryBleListener(
        cast("Any", _HassStub()),
        _sink,
        key_resolver=lambda _device_id: key,
        ble_address_resolver=lambda _device_id: None,
        connect_backoff_remaining=lambda _device_id, _horizon: 0.0,
        connect_backoff_note_failure=lambda _device_id, _horizon: 0.0,
        connect_backoff_note_success=lambda _device_id: None,
        keep_alive_msg_id=None,
        keep_alive_ble_msg_type=None,
    )


def _attach_session(
    listener: JackeryBleListener,
    device_id: str,
    client: object,
) -> _GattSession:
    """Install a fake connected GATT session for ``device_id``."""
    session = _GattSession(generation=1, client=client)
    listener._sessions[device_id] = session  # ruff: ignore[private-member-access]
    listener._clients[device_id] = client  # ruff: ignore[private-member-access]
    return session


def test_listener_resolves_pending_ack_on_matching_cmd() -> None:
    """A decoded notify with the same cmd completes the pending ack future."""
    captured: dict[str, object] = {}

    class _FakeClient:
        is_connected = True

        async def write_gatt_char(  # ruff: ignore[no-self-use]
            self, _uuid: str, blob: bytes, *, response: bool
        ) -> None:
            """Record the GATT write payload and response flag."""
            captured["blob"] = bytes(blob)
            captured["response"] = response

    async def _run() -> None:
        """Send with wait_for_ack and resolve it via a matching echo.

        App 2.4.0 does not expose the outbound msg_id as a response
        transaction ID. A newer frame in the same session with the expected
        BLE message type therefore completes the serialized ACK wait even
        when its observed flags field differs.
        """
        key = base64.b64decode(_LIVE_KEY_B64)
        listener = _build_bare_listener(key)
        session = _attach_session(listener, "dev", _FakeClient())

        async def _drive_ack() -> None:
            # Give async_send_command a tick to register its pending ack
            # and to issue the write, then synthesise the echo frame the
            # device would have pushed back on the notify channel.
            await asyncio.sleep(0)
            echo_plain = build_binary_frame(
                cmd=107,
                flags=0x002C,
                body=b'{"cmd":107,"swEps":1}',
            )
            echo_blob = encrypt_binary_notify(echo_plain, key)
            session.notify_sequence += 1
            await listener._handle_notification(  # ruff: ignore[private-member-access]
                "dev",
                echo_blob,
                session=session,
                notify_sequence=session.notify_sequence,
            )

        sender = listener.async_send_command(
            "dev",
            msg_id=3022,
            ble_msg_type=107,
            body=b'{"swEps":1}',
            wait_for_ack=True,
            ack_timeout_sec=2.0,
        )
        sent, _ = await asyncio.gather(sender, _drive_ack())
        assert sent is True
        stats = listener.stats_for("dev")
        assert stats.acks_received == 1
        assert stats.acks_timed_out == 0
        assert stats.last_ack_at is not None
        assert listener._pending_acks == {}  # ruff: ignore[private-member-access]
        # The frame round-trips through the real decoder.
        parsed = BleBinaryFrame.__name__  # smoke import
        del parsed

    asyncio.run(_run())


def test_listener_ack_timeout_raises_runtime_error() -> None:
    """Verify that sending a command with `wait_for_ack=True` that receives no notify within `ack_timeout_sec`
    raises a `RuntimeError`, increments the device's `acks_timed_out` stat, and clears pending ack state.

    After the timeout the listener's `acks_received` remains 0, `acks_timed_out` increases by 1, and the
    pending ack registry is empty so late notifications cannot resolve the timed-out future.
    """

    class _FakeClient:
        is_connected = True

        async def write_gatt_char(  # ruff: ignore[no-self-use]
            self, _uuid: str, _blob: bytes, *, response: bool
        ) -> None:
            return None

    async def _run() -> None:
        """Assert ack-timeout behaviour and pending-ack cleanup."""
        key = base64.b64decode(_LIVE_KEY_B64)
        listener = _build_bare_listener(key)
        _attach_session(listener, "dev", _FakeClient())

        with pytest.raises(RuntimeError, match="ack timeout"):
            await listener.async_send_command(
                "dev",
                msg_id=3022,
                ble_msg_type=107,
                body=b'{"swEps":1}',
                wait_for_ack=True,
                ack_timeout_sec=0.05,
            )
        stats = listener.stats_for("dev")
        assert stats.acks_received == 0
        assert stats.acks_timed_out == 1
        # Pending bucket is cleaned up so a later notify doesn't fire
        # into a dropped future.
        assert listener._pending_acks == {}  # ruff: ignore[private-member-access]

    asyncio.run(_run())


def test_listener_ack_cmd_filter_ignores_mismatched_cmd() -> None:
    """A notify with a non-listed cmd does not satisfy a cmd-filtered ack."""
    import asyncio  # ruff: ignore[import-outside-top-level]
    import base64  # ruff: ignore[import-outside-top-level]

    from custom_components.jackery_solarvault.client.ble import (  # ruff: ignore[import-outside-top-level]
        build_binary_frame,
        encrypt_binary_notify,
    )

    class _FakeClient:
        is_connected = True

        async def write_gatt_char(  # ruff: ignore[no-self-use]
            self, _uuid: str, _blob: bytes, *, response: bool
        ) -> None:
            return None

    async def _run() -> None:
        """A mismatched echo must not resolve the pending ACK; the matching one must.

        ACK matching uses the newer notify sequence, session ownership and
        BLE message type; the observed flags field is not a transaction ID.
        """
        key = base64.b64decode(_LIVE_KEY_B64)
        listener = _build_bare_listener(key)
        session = _attach_session(listener, "dev", _FakeClient())

        async def _drive_wrong_pair_then_right_pair() -> None:
            await asyncio.sleep(0)
            # First a frame with a foreign (msg_id, type) pair — must NOT
            # complete the pending ack.
            mismatched = encrypt_binary_notify(
                build_binary_frame(cmd=42, flags=9999, body=b"unrelated"), key
            )
            session.notify_sequence += 1
            await listener._handle_notification(  # ruff: ignore[private-member-access]
                "dev",
                mismatched,
                session=session,
                notify_sequence=session.notify_sequence,
            )
            assert listener._pending_acks.get("dev"), (  # ruff: ignore[private-member-access]
                "mismatched echo must leave the pending ack registered"
            )
            # Then the expected echo — this fulfils the future.
            matching = encrypt_binary_notify(
                build_binary_frame(cmd=107, flags=0x002C, body=b'{"ok":1}'), key
            )
            session.notify_sequence += 1
            await listener._handle_notification(  # ruff: ignore[private-member-access]
                "dev",
                matching,
                session=session,
                notify_sequence=session.notify_sequence,
            )

        sender = listener.async_send_command(
            "dev",
            msg_id=3022,
            ble_msg_type=107,
            body=b'{"swEps":1}',
            wait_for_ack=True,
            ack_timeout_sec=2.0,
        )
        sent, _ = await asyncio.gather(sender, _drive_wrong_pair_then_right_pair())
        assert sent is True
        stats = listener.stats_for("dev")
        assert stats.acks_received == 1
        assert stats.acks_timed_out == 0

    asyncio.run(_run())


def test_listener_send_command_validates_msg_id_and_type() -> None:
    """Invalid msg_id/ble_msg_type inputs fail before any pending future exists."""
    import asyncio  # ruff: ignore[import-outside-top-level]

    async def _run() -> None:
        listener = _build_bare_listener(b"x" * 16)

        with pytest.raises(ValueError, match="msg_id must be an integer"):
            await listener.async_send_command(
                "dev",
                msg_id=True,
                ble_msg_type=107,
                body=b"",
            )
        with pytest.raises(ValueError, match="ble_msg_type must be an integer"):
            await listener.async_send_command(
                "dev",
                msg_id=3022,
                ble_msg_type=-1,
                body=b"",
            )

        assert listener._pending_acks == {}  # ruff: ignore[private-member-access]

    asyncio.run(_run())


def test_listener_async_stop_cancels_pending_acks() -> None:
    """Pending ack futures are cancelled on shutdown, never left dangling."""
    import asyncio  # ruff: ignore[import-outside-top-level]

    async def _run() -> None:
        """Test that calling `async_stop` cancels any registered pending ACK futures and clears the listener's pending-ack registry.

        Registers two pending ACKs for different device IDs, invokes `async_stop`, and asserts both pending futures are cancelled and the listener's `_pending_acks` mapping is empty.
        """
        listener = _build_bare_listener()
        # Register two pending acks manually — we are not driving a real
        # write here, just pinning the cleanup behaviour.
        session_a = _attach_session(listener, "dev1", object())
        session_b = _attach_session(listener, "dev2", object())
        ack_a = listener._register_pending_ack("dev1", session_a, 3022, 107)  # ruff: ignore[private-member-access]
        ack_b = listener._register_pending_ack("dev2", session_b, 3019, 120)  # ruff: ignore[private-member-access]

        await listener.async_stop()

        assert ack_a.future.cancelled()
        assert ack_b.future.cancelled()
        assert listener._pending_acks == {}  # ruff: ignore[private-member-access]

    asyncio.run(_run())


def test_listener_send_command_write_failure_releases_pending_ack() -> None:
    """A failed GATT write must not leave a pending ack behind."""
    import asyncio  # ruff: ignore[import-outside-top-level]
    import base64  # ruff: ignore[import-outside-top-level]

    class _ExplodingClient:
        async def write_gatt_char(  # ruff: ignore[no-self-use]
            self, _uuid: str, _blob: bytes, *, response: bool
        ) -> None:
            """Simulate a GATT characteristic write that always fails.

            Parameters:
                _uuid (str): GATT characteristic UUID to write to.
                _blob (bytes): Bytes payload to write.
                response (bool): Whether the write requests a response from the device.

            Raises:
                RuntimeError: Always raised with message "simulated GATT failure".
            """
            raise RuntimeError("simulated GATT failure")

    async def _run() -> None:
        """Exercise the listener's send-command path using a client that fails on write and assert that pending ACKs are cleared after the failure.

        Builds a bare listener configured with the captured live AES key and an _ExplodingClient that raises on GATT writes, calls async_send_command with wait_for_ack enabled (expecting a `RuntimeError` matching "simulated GATT failure"), and verifies the listener's pending-ack registry is empty afterwards.
        """
        key = base64.b64decode(_LIVE_KEY_B64)
        listener = _build_bare_listener(key)
        exploding = _ExplodingClient()
        cast("Any", exploding).is_connected = True
        _attach_session(listener, "dev", exploding)

        with pytest.raises(RuntimeError, match="simulated GATT failure"):
            await listener.async_send_command(
                "dev",
                msg_id=3022,
                ble_msg_type=107,
                body=b'{"swEps":1}',
                wait_for_ack=True,
                ack_timeout_sec=2.0,
            )
        # Pending bucket cleared so a stray late notify cannot fulfil a
        # future the caller already gave up on.
        assert listener._pending_acks == {}  # ruff: ignore[private-member-access]

    asyncio.run(_run())


def test_coordinator_send_ble_command_forwards_ack_options() -> None:
    """``async_send_ble_command`` threads the ack knobs through to the listener."""
    import asyncio  # ruff: ignore[import-outside-top-level]

    from custom_components.jackery_solarvault.coordinator import (  # ruff: ignore[import-outside-top-level]
        JackerySolarVaultCoordinator,
    )

    class _Entry:
        data: dict[str, object] = {}
        options = {
            CONF_ENABLE_BLE_TRANSPORT: True,
            CONF_ENABLE_BLE_WRITES: True,
        }

    captured: dict[str, object] = {}

    class _Listener:
        async def async_send_command(  # ruff: ignore[no-self-use]
            self,
            device_id: str,
            *,
            msg_id: int,
            ble_msg_type: int,
            body: bytes,
            timeout_sec: float = 10.0,
            wait_for_ack: bool = False,
            ack_timeout_sec: float = 5.0,
            mtu_override: int | None = None,
        ) -> bool:
            """Capture the ack knobs threaded through to the listener."""
            captured["device_id"] = device_id
            captured["msg_id"] = msg_id
            captured["ble_msg_type"] = ble_msg_type
            captured["body"] = body
            captured["wait_for_ack"] = wait_for_ack
            captured["ack_timeout_sec"] = ack_timeout_sec
            captured["mtu_override"] = mtu_override
            return True

    async def _run() -> None:
        self = cast(
            "Any",
            JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator),
        )
        self.entry = _Entry()
        self._ble_listener = _Listener()
        sent = await JackerySolarVaultCoordinator.async_send_ble_command(
            self,
            "dev1",
            cmd=107,
            body={"cmd": 107, "swEps": 1},
            flags=3022,
            wait_for_ack=True,
            ack_timeout_sec=3.5,
            mtu_override=120,
        )
        assert sent is True
        assert captured["msg_id"] == 3022
        assert captured["ble_msg_type"] == 107
        assert captured["wait_for_ack"] is True
        assert captured["ack_timeout_sec"] == 3.5  # ruff: ignore[float-equality-comparison]
        assert captured["mtu_override"] == 120

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# MTU + chunked write path
# ---------------------------------------------------------------------------


def test_split_body_for_mtu_matches_smali_budget() -> None:
    """Body chunks honour the smali ``mtu - 60`` per-frame budget."""
    from custom_components.jackery_solarvault.client.ble import (  # ruff: ignore[import-outside-top-level]
        chunk_size_for_mtu,
    )

    # Default MTU (247) → 187 bytes per chunk, matching the Android app.
    assert chunk_size_for_mtu(DEFAULT_BLE_MTU) == 187
    body = b"a" * 400
    chunks = split_body_for_mtu(body, DEFAULT_BLE_MTU)
    assert [len(c) for c in chunks] == [187, 187, 26]
    assert b"".join(chunks) == body

    # Empty body still emits one envelope so the writer can ship a header
    # for cmd=0 queries.
    assert split_body_for_mtu(b"", DEFAULT_BLE_MTU) == [b""]

    # Tiny MTU honours the same formula (70 - 60 = 10 bytes/chunk).
    assert split_body_for_mtu(b"abcdefghij", 70) == [b"abcdefghij"]
    assert split_body_for_mtu(b"abcdefghij" * 3, 70) == [
        b"abcdefghij",
        b"abcdefghij",
        b"abcdefghij",
    ]
    assert split_body_for_mtu(b"x" * 25, 70) == [b"x" * 10, b"x" * 10, b"x" * 5]


def test_split_body_for_mtu_rejects_mtu_below_overhead() -> None:
    """``chunk_size_for_mtu`` refuses values below the 60-byte overhead."""
    with pytest.raises(ValueError, match="below"):
        split_body_for_mtu(b"x", 23)


def test_listener_chunks_oversize_body_into_indexed_frames() -> None:
    """Verify that a body larger than the per-MTU chunk size is split into multiple indexed frames and sent as separate writes.

    Asserts that sending a >187-byte body at the default MTU (247) produces two encrypted write operations; each decrypted frame has the correct `frame_index`, `chunk_count`, and `cmd`, and the concatenation of their `body` fields equals the original payload.
    """
    import asyncio  # ruff: ignore[import-outside-top-level]
    import base64  # ruff: ignore[import-outside-top-level]

    from custom_components.jackery_solarvault.client.ble import (  # ruff: ignore[import-outside-top-level]
        BLE_WRITE_CHAR_UUID,
        decrypt_binary_notify,
    )

    writes: list[bytes] = []

    class _FakeClient:
        is_connected = True

        async def write_gatt_char(  # ruff: ignore[no-self-use]
            self, uuid: str, blob: bytes, *, response: bool
        ) -> None:
            assert uuid == BLE_WRITE_CHAR_UUID
            assert response is False
            writes.append(bytes(blob))

    async def _run() -> None:
        """A >187-byte body splits into two indexed frames that reassemble."""
        key = base64.b64decode(_LIVE_KEY_B64)
        listener = _build_bare_listener(key)
        _attach_session(listener, "dev", _FakeClient())

        body = b'{"big":"' + (b"A" * 200) + b'"}'  # > 187 bytes
        sent = await listener.async_send_command(
            "dev",
            msg_id=3022,
            ble_msg_type=107,
            body=body,
        )
        assert sent is True
        # Two writes for 209 bytes at MTU 247 (187 + 22).
        assert len(writes) == 2
        first = decrypt_binary_notify(writes[0], key)
        second = decrypt_binary_notify(writes[1], key)
        assert first.frame_index == 1
        assert first.chunk_count == 2
        assert first.cmd == 107
        assert len(first.body) == 187
        assert second.frame_index == 2
        assert second.chunk_count == 2
        assert second.cmd == 107
        assert second.body == body[187:]
        assert first.body + second.body == body

    asyncio.run(_run())


def test_listener_mtu_override_forces_smaller_chunks() -> None:
    """``mtu_override`` overrides the cached/default value for chunk sizing."""
    import asyncio  # ruff: ignore[import-outside-top-level]
    import base64  # ruff: ignore[import-outside-top-level]

    from custom_components.jackery_solarvault.client.ble import (  # ruff: ignore[import-outside-top-level]
        decrypt_binary_notify,
    )

    writes: list[bytes] = []

    class _FakeClient:
        is_connected = True

        async def write_gatt_char(  # ruff: ignore[no-self-use]
            self, _uuid: str, blob: bytes, *, response: bool
        ) -> None:
            writes.append(bytes(blob))

    async def _run() -> None:
        """``mtu_override`` forces smaller chunking than the cached MTU."""
        key = base64.b64decode(_LIVE_KEY_B64)
        listener = _build_bare_listener(key)
        _attach_session(listener, "dev", _FakeClient())
        listener._mtu = {"dev": 247}  # ruff: ignore[private-member-access]

        body = b"x" * 25
        # MTU 70 → 10 bytes / chunk → three frames.
        sent = await listener.async_send_command(
            "dev",
            msg_id=3022,
            ble_msg_type=42,
            body=body,
            mtu_override=70,
        )
        assert sent is True
        assert len(writes) == 3
        parsed = [decrypt_binary_notify(w, key) for w in writes]
        assert [p.frame_index for p in parsed] == [1, 2, 3]
        assert all(p.chunk_count == 3 for p in parsed)
        assert b"".join(p.body for p in parsed) == body

    asyncio.run(_run())


def test_listener_mtu_override_rejects_non_integer_value() -> None:
    """``mtu_override`` validation catches non-integer diagnostic input early."""
    import asyncio  # ruff: ignore[import-outside-top-level]
    import base64  # ruff: ignore[import-outside-top-level]

    class _FakeClient:
        is_connected = True

        async def write_gatt_char(  # ruff: ignore[no-self-use]
            self, _uuid: str, _blob: bytes, *, response: bool
        ) -> None:
            """Fail fast when an invalid MTU write attempt occurs."""
            raise AssertionError("invalid MTU must not write to GATT")

    async def _run() -> None:
        """Runs a minimal listener scenario to verify validation of the `mtu_override` parameter.

        Constructs a bare listener with a fake client and a resolved AES key, then calls
        `async_send_command` with a non-integer `mtu_override` to assert input validation.

        Raises:
            ValueError: if `mtu_override` is not an integer (expected message: "mtu_override must be an integer").
        """
        key = base64.b64decode(_LIVE_KEY_B64)
        listener = _build_bare_listener(key)
        _attach_session(listener, "dev", _FakeClient())

        with pytest.raises(ValueError, match="mtu_override must be an integer"):
            await listener.async_send_command(
                "dev",
                msg_id=3022,
                ble_msg_type=42,
                body=b"x",
                mtu_override=cast("Any", float("nan")),
            )

    asyncio.run(_run())


def test_listener_mtu_for_device_falls_back_to_default() -> None:
    """An un-learnt device id surfaces the Android-app default MTU."""
    listener = _build_bare_listener()
    assert listener.mtu_for_device("unknown") == DEFAULT_BLE_MTU
    listener._mtu["known"] = 120  # ruff: ignore[private-member-access]
    assert listener.mtu_for_device("known") == 120


def test_listener_record_negotiated_mtu_reads_bleak_mtu_size() -> None:
    """``_record_negotiated_mtu`` accepts the bleak ``mtu_size`` attribute."""
    listener = _build_bare_listener()

    class _Client:
        mtu_size = 185

    listener._record_negotiated_mtu("dev", cast("Any", _Client()))  # ruff: ignore[private-member-access]
    assert listener.mtu_for_device("dev") == 185


def test_listener_record_negotiated_mtu_ignores_garbage() -> None:
    """Non-int / out-of-range MTU values leave the cache empty."""
    listener = _build_bare_listener()

    class _NoMtu:
        pass

    class _Bad:
        mtu_size = 12  # below the 60-byte overhead

    listener._record_negotiated_mtu("dev", cast("Any", _NoMtu()))  # ruff: ignore[private-member-access]
    listener._record_negotiated_mtu("dev2", cast("Any", _Bad()))  # ruff: ignore[private-member-access]
    # Both fall back to the default — the cache stays untouched.
    assert listener.mtu_for_device("dev") == DEFAULT_BLE_MTU
    assert listener.mtu_for_device("dev2") == DEFAULT_BLE_MTU


def test_listener_successful_notify_decode_clears_stale_last_error() -> None:
    """Verifies that a successfully decoded BLE notify clears any previously stored GATT error and increments the decoded frame count.

    Asserts that after handling a valid encrypted notify for a device, the listener's per-device statistics have `frames_decoded` increased and `last_error` set to `None`.
    """
    import asyncio  # ruff: ignore[import-outside-top-level]
    import base64  # ruff: ignore[import-outside-top-level]

    from custom_components.jackery_solarvault.client.ble import (  # ruff: ignore[import-outside-top-level]
        build_binary_frame,
        encrypt_binary_notify,
    )

    async def _run() -> None:
        """Exercise the listener's notification handling by delivering a real encrypted binary notify and asserting the listener decodes it and clears a previous error state.

        This async helper sets a known AES key on a bare listener, injects a prior `last_error`, delivers an encrypted binary notify carrying an empty JSON body, and asserts that `stats.frames_decoded` increments to reflect a successfully decoded frame and that `stats.last_error` becomes `None`.
        """
        key = base64.b64decode(_LIVE_KEY_B64)
        listener = _build_bare_listener(key)
        stats = listener.stats_for("dev")
        # Mirror the real decode-failure path, which stamps BOTH fields
        # (see _handle_notification); recovery only resets last_error
        # when it still points at the stale decode error.
        stats.last_decode_error = "notify: Bluetooth GATT Error error=133"
        stats.last_error = stats.last_decode_error

        blob = encrypt_binary_notify(build_binary_frame(cmd=120, body=b"{}"), key)
        await listener._handle_notification("dev", blob)  # ruff: ignore[private-member-access]

        assert stats.frames_decoded == 1
        assert stats.last_error is None

    asyncio.run(_run())


def test_listener_chunked_write_uses_single_ack_for_whole_message() -> None:
    """Chunked writes register one pending ack covering all frames combined."""
    import asyncio  # ruff: ignore[import-outside-top-level]
    import base64  # ruff: ignore[import-outside-top-level]

    from custom_components.jackery_solarvault.client.ble import (  # ruff: ignore[import-outside-top-level]
        build_binary_frame,
        encrypt_binary_notify,
    )

    writes: list[bytes] = []

    class _FakeClient:
        is_connected = True

        async def write_gatt_char(  # ruff: ignore[no-self-use]
            self, _uuid: str, blob: bytes, *, response: bool
        ) -> None:
            writes.append(bytes(blob))

    async def _run() -> None:
        """Chunked writes register ONE pending ack for the whole message."""
        key = base64.b64decode(_LIVE_KEY_B64)
        listener = _build_bare_listener(key)
        session = _attach_session(listener, "dev", _FakeClient())

        async def _drive_ack_after_writes() -> None:
            # Wait until both chunked writes have hit the wire, then push
            # one echo frame — that single notify must complete the ack.
            for _ in range(100):
                if len(writes) >= 2:
                    break
                await asyncio.sleep(0.005)
            echo = encrypt_binary_notify(
                build_binary_frame(cmd=107, flags=3022, body=b'{"ok":1}'), key
            )
            session.notify_sequence += 1
            await listener._handle_notification(  # ruff: ignore[private-member-access]
                "dev",
                echo,
                session=session,
                notify_sequence=session.notify_sequence,
            )

        sender = listener.async_send_command(
            "dev",
            msg_id=3022,
            ble_msg_type=107,
            body=b"P" * 250,  # 250 bytes → 2 frames at default MTU
            wait_for_ack=True,
            ack_timeout_sec=2.0,
        )
        sent, _ = await asyncio.gather(sender, _drive_ack_after_writes())
        assert sent is True
        assert len(writes) == 2
        stats = listener.stats_for("dev")
        assert stats.acks_received == 1
        assert stats.acks_timed_out == 0
        # No leftover pending ack — one notify cleared the registry.
        assert listener._pending_acks == {}  # ruff: ignore[private-member-access]

    asyncio.run(_run())


def test_merge_battery_pack_lifetime_from_ble_updates_matching_pack() -> None:
    """cmd=120 BLE for devType=1 merges inEgy/outEgy into the matching pack.

    Pinned 2026-05-17: BLE frame
    ``{cmd:120, deviceSn:"HQ2C01400955HP3", devType:1, subType:0,
       outEgy:5095, inEgy:5648}`` arrives. MQTT ``UploadSubDeviceGroupProperty``
    has already populated the pack via ``deviceSn`` match. The helper
    enriches the matching pack with the BLE-only lifetime counters.
    """
    from custom_components.jackery_solarvault.coordinator import (  # ruff: ignore[import-outside-top-level]
        JackerySolarVaultCoordinator,
    )

    updated = {
        "battery_packs": [
            {
                "deviceSn": "HQ2C01400955HP3",
                "devType": 1,
                "subType": 0,
                "batSoc": 53,
                "inPw": 0,
                "outPw": 200,
            },
        ],
    }
    body = {
        "deviceSn": "HQ2C01400955HP3",
        "devType": 1,
        "subType": 0,
        "outEgy": 5095,
        "inEgy": 5648,
    }
    touched = JackerySolarVaultCoordinator._merge_battery_pack_lifetime_from_ble(  # ruff: ignore[private-member-access]
        updated, body
    )
    assert touched is True
    pack = updated["battery_packs"][0]
    assert pack["inEgy"] == 5648
    assert pack["outEgy"] == 5095
    # Existing fields preserved.
    assert pack["batSoc"] == 53
    assert pack["inPw"] == 0
    assert pack["outPw"] == 200


def test_merge_battery_pack_lifetime_from_ble_creates_minimal_pack() -> None:
    """BLE-only lifetime data creates a minimal pack entry.

    The captured SolarVault payloads show HTTP ``pack/list`` returning
    ``data:null`` while cmd=120 BLE still reports the pack ``deviceSn``
    plus ``inEgy``/``outEgy``. Without a minimal entry the lifetime
    counters stay unrouted forever and the opt-in pack energy entities
    never receive data.
    """
    from custom_components.jackery_solarvault.coordinator import (  # ruff: ignore[import-outside-top-level]
        JackerySolarVaultCoordinator,
    )

    updated = {
        "battery_packs": [
            {
                "deviceSn": "HQ2C01400955HP3",
                "devType": 1,
            },
        ],
    }
    body = {
        "deviceSn": "DIFFERENT_PACK_SN",  # not in the list
        "devType": 1,
        "subType": 0,
        "outEgy": 99,
        "inEgy": 88,
    }
    touched = JackerySolarVaultCoordinator._merge_battery_pack_lifetime_from_ble(  # ruff: ignore[private-member-access]
        updated, body
    )
    assert touched is True
    assert len(updated["battery_packs"]) == 2
    pack = updated["battery_packs"][1]
    assert pack["deviceSn"] == "DIFFERENT_PACK_SN"
    assert pack["devType"] == 1
    assert pack["subType"] == 0
    assert pack["inEgy"] == 88
    assert pack["outEgy"] == 99
    assert "_last_seen_at" in pack


def test_merge_battery_pack_lifetime_from_ble_no_lifetime_fields_no_op() -> None:
    """A cmd=120 BLE body without inEgy/outEgy must not touch the pack."""
    from custom_components.jackery_solarvault.coordinator import (  # ruff: ignore[import-outside-top-level]
        JackerySolarVaultCoordinator,
    )

    updated = {
        "battery_packs": [{"deviceSn": "HQ2C01400955HP3", "devType": 1}],
    }
    body = {"deviceSn": "HQ2C01400955HP3", "devType": 1, "subType": 0}
    touched = JackerySolarVaultCoordinator._merge_battery_pack_lifetime_from_ble(  # ruff: ignore[private-member-access]
        updated, body
    )
    assert touched is False
