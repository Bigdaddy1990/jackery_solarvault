__all__ = ["Mark", "MarkedYAMLError", "YAMLError"]  # noqa: D100


class Mark:  # noqa: D101
    def __init__(self, name, index, line, column, buffer, pointer) -> None:  # noqa: ANN001, PLR0913, PLR0917
        """Initialize a Mark representing a location within a source buffer.

        Parameters:
            name: Identifier for the source (e.g., filename or stream name).
            index: Absolute character index in the buffer.
            line: Zero-based line number containing the position.
            column: Zero-based column number within the line.
            buffer: The full source text (or `None` if unavailable).
            pointer: Character index within `buffer` that the mark points to.
        """
        self.name = name
        self.index = index
        self.line = line
        self.column = column
        self.buffer = buffer
        self.pointer = pointer

    def get_snippet(self, indent=4, max_length=75):  # noqa: ANN001, ANN201
        """Return a one-line excerpt of the source buffer and a caret marker.

        Parameters:
            indent (int): Number of leading spaces before the snippet.
            max_length (int): Maximum length of the excerpt before truncation.

        Returns:
            str | None: Snippet text followed by a caret marker, or ``None``
                when the mark has no backing buffer.
        """
        if self.buffer is None:
            return None
        head = ""
        start = self.pointer
        while start > 0 and self.buffer[start - 1] not in "\0\r\n\x85\u2028\u2029":
            start -= 1
            if self.pointer - start > max_length / 2 - 1:
                head = " ... "
                start += 5
                break
        tail = ""
        end = self.pointer
        while (
            end < len(self.buffer) and self.buffer[end] not in "\0\r\n\x85\u2028\u2029"
        ):
            end += 1
            if end - self.pointer > max_length / 2 - 1:
                tail = " ... "
                end -= 5
                break
        snippet = self.buffer[start:end]
        return (
            " " * indent
            + head
            + snippet
            + tail
            + "\n"
            + " " * (indent + self.pointer - start + len(head))
            + "^"
        )

    def __str__(self) -> str:
        """Return a human-readable description of this mark.

        Returns:
            str: Description containing the source name plus 1-based line and
                column numbers. If a buffer is available, a one-line excerpt and
                caret marker are appended.
        """
        snippet = self.get_snippet()
        where = '  in "%s", line %d, column %d' % (  # noqa: UP031
            self.name,
            self.line + 1,
            self.column + 1,
        )
        if snippet is not None:
            where += ":\n" + snippet
        return where


class YAMLError(Exception):  # noqa: D101
    pass


class MarkedYAMLError(YAMLError):  # noqa: D101
    def __init__(
        self,
        context=None,  # noqa: ANN001
        context_mark=None,  # noqa: ANN001
        problem=None,  # noqa: ANN001
        problem_mark=None,  # noqa: ANN001
        note=None,  # noqa: ANN001
    ) -> None:
        """Initialize the MarkedYAMLError with optional contextual messages and their source locations.

        Parameters:
            context (str | None): Human-readable context describing where or why the error occurred.
            context_mark (Mark | None): Source location associated with `context`.
            problem (str | None): Short description of the specific problem encountered.
            problem_mark (Mark | None): Source location associated with `problem`.
            note (str | None): Optional additional information or suggestion related to the error.
        """
        self.context = context
        self.context_mark = context_mark
        self.problem = problem
        self.problem_mark = problem_mark
        self.note = note

    def __str__(self) -> str:
        """Format the error as a multiline message combining context, source marks, problem, and note.

        Assembles lines in this order when present: context; the context mark (included only if the problem or problem mark is missing, or if the context and problem marks refer to different name, line, or column); problem; problem mark; note. The returned string is the lines joined with newline characters.

        Returns:
            str: The formatted error message.
        """
        lines = []
        if self.context is not None:
            lines.append(self.context)
        if self.context_mark is not None and (  # noqa: PLR0916
            self.problem is None
            or self.problem_mark is None
            or self.context_mark.name != self.problem_mark.name
            or self.context_mark.line != self.problem_mark.line
            or self.context_mark.column != self.problem_mark.column
        ):
            lines.append(str(self.context_mark))
        if self.problem is not None:
            lines.append(self.problem)
        if self.problem_mark is not None:
            lines.append(str(self.problem_mark))
        if self.note is not None:
            lines.append(self.note)
        return "\n".join(lines)
