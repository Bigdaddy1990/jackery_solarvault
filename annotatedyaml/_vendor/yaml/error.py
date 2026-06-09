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
        """
        Produce a single-line excerpt of the source buffer with a caret pointing at the marker's pointer.
        
        Parameters:
        	indent (int): Number of leading spaces before the snippet.
        	max_length (int): Maximum length of the excerpt; longer content is truncated and indicated with " ... ".
        
        Returns:
        	str or None: The formatted snippet followed by a newline and a caret aligned under the pointer, or `None` if the mark has no buffer.
        """  # noqa: E501
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
        """
        Return a human-readable description of this mark's location and an optional source snippet.
        
        Returns:
            str: Description containing the mark's name and 1-based line and column numbers; if a buffer is available, the description is followed by a one-line source excerpt with a caret (`^`) indicating the marker position.
        """  # noqa: E501
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
        """  # noqa: E501
        self.context = context
        self.context_mark = context_mark
        self.problem = problem
        self.problem_mark = problem_mark
        self.note = note

    def __str__(self) -> str:
        """
        Return a formatted error message that includes optional context, source marks, a problem description, and a note.
        
        Assembles lines in the following order, joining them with newline characters: context (if present); the context mark only when the problem or problem mark is missing or when the context and problem marks refer to different locations (name, line, or column); problem (if present); problem mark (if present); note (if present).
        """  # noqa: E501
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
