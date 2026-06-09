class Token:  # noqa: D100, D101
    def __init__(self, start_mark, end_mark) -> None:  # noqa: ANN001, D107
        """
        Initialize the token with its source location marks.
        
        Parameters:
            start_mark: The mark indicating where the token begins in the source (or None).
            end_mark: The mark indicating where the token ends in the source (or None).
        """
        self.start_mark = start_mark
        self.end_mark = end_mark

    def __repr__(self) -> str:  # noqa: D105
        """
        Produce a deterministic string representation using the instance's non-*_mark attributes.
        
        Attributes whose names end with "_mark" are excluded; the remaining attributes are sorted by name and formatted as `key=value` using `repr`. The final string is returned in the form `ClassName(attr1=..., attr2=...)`.
        
        Returns:
            str: The formatted representation of the instance.
        """
        attributes = [key for key in self.__dict__ if not key.endswith("_mark")]
        attributes.sort()
        arguments = ", ".join([f"{key}={getattr(self, key)!r}" for key in attributes])
        return f"{self.__class__.__name__}({arguments})"


# class BOMToken(Token):
#    id = '<byte order mark>'


class DirectiveToken(Token):  # noqa: D101
    id = "<directive>"

    def __init__(self, name, value, start_mark, end_mark) -> None:  # noqa: ANN001, D107
        """
        Initialize the directive token with its name, value, and source location marks.
        
        Parameters:
            name (str): The directive name (e.g., "YAML", "TAG").
            value (str): The directive value as found in the source.
            start_mark: The start location mark for the directive in the source (may be None).
            end_mark: The end location mark for the directive in the source (may be None).
        """
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
        """
        Initialize a StreamStartToken with optional source location marks and encoding.
        
        Parameters:
            start_mark: Start location mark for the token.
            end_mark: End location mark for the token.
            encoding (str | None): Character encoding of the stream, if provided.
        """
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
        """
        Initialize the token with its value and source location marks.
        
        Parameters:
            value: The token's payload (for example, an alias, anchor, or tag string).
            start_mark: Source mark object indicating where the token begins (may be None).
            end_mark: Source mark object indicating where the token ends (may be None).
        """
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark


class AnchorToken(Token):  # noqa: D101
    id = "<anchor>"

    def __init__(self, value, start_mark, end_mark) -> None:  # noqa: ANN001, D107
        """
        Initialize the token with its value and source location marks.
        
        Parameters:
            value: The token's payload (for example, an alias, anchor, or tag string).
            start_mark: Source mark object indicating where the token begins (may be None).
            end_mark: Source mark object indicating where the token ends (may be None).
        """
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark


class TagToken(Token):  # noqa: D101
    id = "<tag>"

    def __init__(self, value, start_mark, end_mark) -> None:  # noqa: ANN001, D107
        """
        Initialize the token with its value and source location marks.
        
        Parameters:
            value: The token's payload (for example, an alias, anchor, or tag string).
            start_mark: Source mark object indicating where the token begins (may be None).
            end_mark: Source mark object indicating where the token ends (may be None).
        """
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark


class ScalarToken(Token):  # noqa: D101
    id = "<scalar>"

    def __init__(self, value, plain, start_mark, end_mark, style=None) -> None:  # noqa: ANN001, D107
        """
        Initialize a scalar token with its value and parsing metadata.
        
        Parameters:
            value: The scalar content.
            plain: True if the scalar is plain (unquoted), False otherwise.
            start_mark: Source location mark where the token starts.
            end_mark: Source location mark where the token ends.
            style: Optional scalar style indicator (for example, quote style), or None.
        """
        self.value = value
        self.plain = plain
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.style = style
