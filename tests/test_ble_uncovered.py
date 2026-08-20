"""Tests for uncovered paths in client/ble.py to increase coverage."""

import pytest

from custom_components.jackery_solarvault.client.ble import BleBinaryFrame


class TestBleBinaryFrame:
    """Test BleBinaryFrame dataclass."""

    def test_creation(self) -> None:
        """Test BleBinaryFrame creation."""
        frame = BleBinaryFrame(
            frame_index=1,
            chunk_count=1,
            flags=0,
            cmd=1,
            body=b"test",
            trailer=b"0000",
        )
        assert frame is not None
        assert frame.frame_index == 1
        assert frame.chunk_count == 1
        assert frame.flags == 0
        assert frame.cmd == 1
        assert frame.body == b"test"
        assert frame.trailer == b"0000"

    def test_frame_index_property(self) -> None:
        """Test frame_index property."""
        frame = BleBinaryFrame(
            frame_index=5,
            chunk_count=1,
            flags=0,
            cmd=1,
            body=b"test",
            trailer=b"0000",
        )
        assert frame.frame_index == 5

    def test_chunk_count_property(self) -> None:
        """Test chunk_count property."""
        frame = BleBinaryFrame(
            frame_index=1,
            chunk_count=3,
            flags=0,
            cmd=1,
            body=b"test",
            trailer=b"0000",
        )
        assert frame.chunk_count == 3

    def test_flags_property(self) -> None:
        """Test flags property."""
        frame = BleBinaryFrame(
            frame_index=1,
            chunk_count=1,
            flags=0x1234,
            cmd=1,
            body=b"test",
            trailer=b"0000",
        )
        assert frame.flags == 0x1234

    def test_cmd_property(self) -> None:
        """Test cmd property."""
        frame = BleBinaryFrame(
            frame_index=1,
            chunk_count=1,
            flags=0,
            cmd=0xEE01,
            body=b"test",
            trailer=b"0000",
        )
        assert frame.cmd == 0xEE01

    def test_body_property(self) -> None:
        """Test body property."""
        frame = BleBinaryFrame(
            frame_index=1,
            chunk_count=1,
            flags=0,
            cmd=1,
            body=b"payload_data",
            trailer=b"0000",
        )
        assert frame.body == b"payload_data"

    def test_trailer_property(self) -> None:
        """Test trailer property."""
        frame = BleBinaryFrame(
            frame_index=1,
            chunk_count=1,
            flags=0,
            cmd=1,
            body=b"test",
            trailer=b"abcd",
        )
        assert frame.trailer == b"abcd"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
