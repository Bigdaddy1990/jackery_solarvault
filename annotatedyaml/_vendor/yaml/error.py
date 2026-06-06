__all__ = ["Mark", "MarkedYAMLError", "YAMLError"]  # noqa: D100


class Mark:  # noqa: D101
    def __init__(self, name, index, line, column, buffer, pointer) -> None:  # noqa: ANN001, D107, PLR0913, PLR0917
        self.name = name
        self.index = index
        self.line = line
        self.column = column
        self.buffer = buffer
        self.pointer = pointer

    def get_snippet(self, indent=4, max_length=75):  # noqa: ANN001, ANN201, D102
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

    def __str__(self) -> str:  # noqa: D105
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
    def __init__(  # noqa: D107
        self,
        context=None,  # noqa: ANN001
        context_mark=None,  # noqa: ANN001
        problem=None,  # noqa: ANN001
        problem_mark=None,  # noqa: ANN001
        note=None,  # noqa: ANN001
    ) -> None:
        self.context = context
        self.context_mark = context_mark
        self.problem = problem
        self.problem_mark = problem_mark
        self.note = note

    def __str__(self) -> str:  # noqa: D105
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
