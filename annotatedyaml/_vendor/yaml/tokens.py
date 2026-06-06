class Token:  # noqa: D100, D101
    def __init__(self, start_mark, end_mark) -> None:  # noqa: ANN001, D107
        self.start_mark = start_mark
        self.end_mark = end_mark

    def __repr__(self) -> str:  # noqa: D105
        attributes = [key for key in self.__dict__ if not key.endswith("_mark")]
        attributes.sort()
        arguments = ", ".join([f"{key}={getattr(self, key)!r}" for key in attributes])
        return f"{self.__class__.__name__}({arguments})"


# class BOMToken(Token):
#    id = '<byte order mark>'


class DirectiveToken(Token):  # noqa: D101
    id = "<directive>"

    def __init__(self, name, value, start_mark, end_mark) -> None:  # noqa: ANN001, D107
        self.name = name
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark


class DocumentStartToken(Token):  # noqa: D101
    id = "<document start>"


class DocumentEndToken(Token):  # noqa: D101
    id = "<document end>"


class StreamStartToken(Token):  # noqa: D101
    id = "<stream start>"

    def __init__(self, start_mark=None, end_mark=None, encoding=None) -> None:  # noqa: ANN001, D107
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.encoding = encoding


class StreamEndToken(Token):  # noqa: D101
    id = "<stream end>"


class BlockSequenceStartToken(Token):  # noqa: D101
    id = "<block sequence start>"


class BlockMappingStartToken(Token):  # noqa: D101
    id = "<block mapping start>"


class BlockEndToken(Token):  # noqa: D101
    id = "<block end>"


class FlowSequenceStartToken(Token):  # noqa: D101
    id = "["


class FlowMappingStartToken(Token):  # noqa: D101
    id = "{"


class FlowSequenceEndToken(Token):  # noqa: D101
    id = "]"


class FlowMappingEndToken(Token):  # noqa: D101
    id = "}"


class KeyToken(Token):  # noqa: D101
    id = "?"


class ValueToken(Token):  # noqa: D101
    id = ":"


class BlockEntryToken(Token):  # noqa: D101
    id = "-"


class FlowEntryToken(Token):  # noqa: D101
    id = ","


class AliasToken(Token):  # noqa: D101
    id = "<alias>"

    def __init__(self, value, start_mark, end_mark) -> None:  # noqa: ANN001, D107
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark


class AnchorToken(Token):  # noqa: D101
    id = "<anchor>"

    def __init__(self, value, start_mark, end_mark) -> None:  # noqa: ANN001, D107
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark


class TagToken(Token):  # noqa: D101
    id = "<tag>"

    def __init__(self, value, start_mark, end_mark) -> None:  # noqa: ANN001, D107
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark


class ScalarToken(Token):  # noqa: D101
    id = "<scalar>"

    def __init__(self, value, plain, start_mark, end_mark, style=None) -> None:  # noqa: ANN001, D107
        self.value = value
        self.plain = plain
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.style = style
