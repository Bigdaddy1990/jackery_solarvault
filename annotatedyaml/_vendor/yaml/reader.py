# This module contains abstractions for the input stream. You don't have to  # noqa: D100
# looks further, there are no pretty code.
#
# We define two classes here.
#
#   Mark(source, line, column)
# It's just a record and its only use is producing nice error messages.
# Parser does not use it for any other purposes.
#
#   Reader(source, data)
# Reader determines the encoding of `data` and converts it to unicode.
# Reader provides the following methods and attributes:
#   reader.peek(length=1) - return the next `length` characters
#   reader.forward(length=1) - move the current position to `length` characters.
#   reader.index - the number of the current character.
#   reader.line, stream.column - the line and the column of the current character.

__all__ = ["Reader", "ReaderError"]

import codecs
import re

from .error import Mark, YAMLError


class ReaderError(YAMLError):  # noqa: D101
    def __init__(self, name, position, character, encoding, reason) -> None:  # noqa: ANN001
        """
        Create a ReaderError holding context about a decoding or character validation failure.
        
        Parameters:
            name (str): Source name or identifier where the error occurred.
            position (int): Byte or character index in the source where the offending value was observed.
            character (int | bytes | str): The offending value — an integer code point, a single-character string, or a raw byte.
            encoding (str): The encoding associated with the error (for example, "utf-8" or "utf-16-le").
            reason (str): Human-readable explanation of why the value is invalid or could not be decoded.
        """  # noqa: E501
        self.name = name
        self.character = character
        self.position = position
        self.encoding = encoding
        self.reason = reason

    def __str__(self) -> str:
        """Format a human-readable message describing the decoding or unacceptable-character failure.

        If `character` is a `bytes` value the message reports the codec, the byte value in hex,
        the decoding reason, the input name, and the byte position. Otherwise the message
        reports the character code in hex, the reason, the input name, and the character position.

        Returns:
            The formatted error message string.
        """  # noqa: E501
        if isinstance(self.character, bytes):
            return (
                "'%s' codec can't decode byte #x%02x: %s\n"  # noqa: UP031
                '  in "%s", position %d'
                % (
                    self.encoding,
                    ord(self.character),
                    self.reason,
                    self.name,
                    self.position,
                )
            )
        return 'unacceptable character #x%04x: %s\n  in "%s", position %d' % (  # noqa: UP031
            self.character,
            self.reason,
            self.name,
            self.position,
        )


class Reader:  # noqa: D101
    # Reader:
    # - determines the data encoding and converts it to a unicode string,
    # - checks if characters are in allowed range,
    # - adds '\0' to the end.

    # Reader accepts
    #  - a `bytes` object,
    #  - a `str` object,
    #  - a file-like object with its `read` method returning `str`,
    #  - a file-like object with its `read` method returning `unicode`.

    # Yeah, it's ugly and slow.

    def __init__(self, stream) -> None:  # noqa: ANN001
        """
        Initialize the Reader for a Unicode string, a bytes object, or a file-like stream and prepare decoding, buffering, and position-tracking state.
        
        Parameters:
            stream (str | bytes | file-like): Source to read from. If a `str`, the content is validated for allowed characters and stored as the internal Unicode buffer with a terminating NUL. If `bytes`, the raw byte buffer is stored and encoding detection is performed. Otherwise `stream` is treated as a file-like object (expected to support `read`) and the reader will read raw bytes from it and detect/initialize decoding.
        
        Notes:
            The initializer sets reader metadata (`name`), cursor/position counters (`index`, `line`, `column`, `pointer`, `stream_pointer`), buffer fields (`buffer`, `raw_buffer`), decoding routine (`raw_decode`) and `encoding`, and the `eof` flag as appropriate for the provided input.
        """  # noqa: E501
        self.name = None
        self.stream = None
        self.stream_pointer = 0
        self.eof = True
        self.buffer = ""
        self.pointer = 0
        self.raw_buffer = None
        self.raw_decode = None
        self.encoding = None
        self.index = 0
        self.line = 0
        self.column = 0
        if isinstance(stream, str):
            self.name = "<unicode string>"
            self.check_printable(stream)
            self.buffer = stream + "\0"
        elif isinstance(stream, bytes):
            self.name = "<byte string>"
            self.raw_buffer = stream
            self.determine_encoding()
        else:
            self.stream = stream
            self.name = getattr(stream, "name", "<file>")
            self.eof = False
            self.raw_buffer = None
            self.determine_encoding()

    def peek(self, index=0):  # noqa: ANN001, ANN201
        """
        Get the character at the current pointer plus an optional offset.
        
        Ensures the requested position is available in the internal buffer before accessing it.
        
        Parameters:
            index (int): Offset from the current pointer (0 returns the current character).
        
        Returns:
            str: The character at buffer[pointer + index].
        """  # noqa: E501
        try:
            return self.buffer[self.pointer + index]
        except IndexError:
            self.update(index + 1)
            return self.buffer[self.pointer + index]

    def prefix(self, length=1):  # noqa: ANN001, ANN201
        """
        Return the next substring from the unread buffer without advancing the reader.
        
        Parameters:
            length (int): Number of characters to include; ensures at least this many characters are available before slicing.
        
        Returns:
            str: The substring of length `length` starting at the current unread buffer position.
        """  # noqa: E501
        if self.pointer + length >= len(self.buffer):
            self.update(length)
        return self.buffer[self.pointer : self.pointer + length]

    def forward(self, length=1) -> None:  # noqa: ANN001
        r"""Advance the reader's current position by the given number of characters.

        This moves the internal cursor forward by `length` characters and updates position tracking: `pointer` (buffer offset), `index` (absolute character count), `line`, and `column`. Newline characters ("\n", "\x85", "\u2028", "\u2029") and carriage returns not followed by "\n" increment the line counter and reset the column to 0; the zero-width BOM ("\ufeff") does not increase the column.

        Parameters:
            length (int): Number of characters to consume from the buffer.
        """  # noqa: E501
        if self.pointer + length + 1 >= len(self.buffer):
            self.update(length + 1)
        while length:
            ch = self.buffer[self.pointer]
            self.pointer += 1
            self.index += 1
            if ch in "\n\x85\u2028\u2029" or (
                ch == "\r" and self.buffer[self.pointer] != "\n"
            ):
                self.line += 1
                self.column = 0
            elif ch != "\ufeff":
                self.column += 1
            length -= 1

    def get_mark(self):  # noqa: ANN201
        """
        Create a Mark for the reader's current position for error reporting.
        
        If the reader was created from an in-memory buffer (no underlying stream), the mark includes the current Unicode buffer and buffer pointer; otherwise the buffer and pointer fields are None.
        
        Returns:
            Mark: Contains `name`, `index`, `line`, `column`, and `buffer`/`pointer` (buffer and pointer are `None` for stream-backed readers).
        """  # noqa: E501
        if self.stream is None:
            return Mark(
                self.name, self.index, self.line, self.column, self.buffer, self.pointer
            )
        return Mark(self.name, self.index, self.line, self.column, None, None)

    def determine_encoding(self) -> None:
        """Detects and configures the text encoding for the input stream.

        Ensures enough initial raw bytes are available to detect a UTF-16 little-endian or big-endian BOM.
        Sets `self.raw_decode` to the corresponding decoder and `self.encoding` to the detected encoding; defaults to UTF-8 if no BOM is present.
        After selecting a decoder, ensures the Unicode buffer contains at least one decoded character.
        """  # noqa: E501
        while not self.eof and (self.raw_buffer is None or len(self.raw_buffer) < 2):  # noqa: PLR2004
            self.update_raw()
        if isinstance(self.raw_buffer, bytes):
            if self.raw_buffer.startswith(codecs.BOM_UTF16_LE):
                self.raw_decode = codecs.utf_16_le_decode
                self.encoding = "utf-16-le"
            elif self.raw_buffer.startswith(codecs.BOM_UTF16_BE):
                self.raw_decode = codecs.utf_16_be_decode
                self.encoding = "utf-16-be"
            else:
                self.raw_decode = codecs.utf_8_decode
                self.encoding = "utf-8"
        self.update(1)

    NON_PRINTABLE = re.compile(
        r"[^\x09\x0a\x0d\x20-\x7e\x85\xa0-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
    )

    def check_printable(self, data) -> None:  # noqa: ANN001
        """Validate a text segment and raise an error if it contains disallowed (non-printable) Unicode characters.

        Parameters:
            data (str): The text to inspect for non-printable characters.

        Raises:
            ReaderError: If a disallowed character is found; the error includes the source name, absolute position, Unicode code point of the offending character, encoding `"unicode"`, and the reason `"special characters are not allowed"`.
        """  # noqa: E501
        match = self.NON_PRINTABLE.search(data)
        if match:
            character = match.group()
            position = self.index + (len(self.buffer) - self.pointer) + match.start()
            raise ReaderError(
                self.name,
                position,
                ord(character),
                "unicode",
                "special characters are not allowed",
            )

    def update(self, length) -> None:  # noqa: ANN001
        """Ensure the internal Unicode buffer contains at least `length` characters from the current pointer by decoding and appending data from the raw input.

        Parameters:
            length (int): The minimum number of characters that must be available in the buffer from the current pointer.

        Raises:
            ReaderError: If a decoding error occurs while converting raw bytes to Unicode, or if the decoded text contains disallowed (non-printable) characters.
        """  # noqa: E501
        if self.raw_buffer is None:
            return
        self.buffer = self.buffer[self.pointer :]
        self.pointer = 0
        while len(self.buffer) < length:
            if not self.eof:
                self.update_raw()
            if self.raw_decode is not None:
                try:
                    data, converted = self.raw_decode(
                        self.raw_buffer, "strict", self.eof
                    )
                except UnicodeDecodeError as exc:
                    character = self.raw_buffer[exc.start]
                    if self.stream is not None:
                        position = (
                            self.stream_pointer - len(self.raw_buffer) + exc.start
                        )
                    else:
                        position = exc.start
                    raise ReaderError(  # noqa: B904
                        self.name, position, character, exc.encoding, exc.reason
                    )
            else:
                data = self.raw_buffer
                converted = len(data)
            self.check_printable(data)
            self.buffer += data
            self.raw_buffer = self.raw_buffer[converted:]
            if self.eof:
                self.buffer += "\0"
                self.raw_buffer = None
                break

    def update_raw(self, size=4096) -> None:  # noqa: ANN001
        """Read up to `size` bytes from the underlying stream and append them to the raw byte buffer.

        Parameters:
                size (int): Maximum number of bytes to read from the stream (default 4096).

        Description:
                This method reads up to `size` bytes from `self.stream`, initializes `self.raw_buffer` if it is None or appends the data otherwise, increments `self.stream_pointer` by the number of bytes read, and sets `self.eof` to `True` when no bytes are returned.
        """  # noqa: D206, E101, E501
        data = self.stream.read(size)
        if self.raw_buffer is None:
            self.raw_buffer = data
        else:
            self.raw_buffer += data
        self.stream_pointer += len(data)
        if not data:
            self.eof = True
