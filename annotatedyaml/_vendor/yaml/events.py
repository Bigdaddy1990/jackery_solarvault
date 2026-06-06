# Abstract classes.  # noqa: D100


class Event:  # noqa: D101
    def __init__(self, start_mark=None, end_mark=None) -> None:  # noqa: ANN001, D107
        self.start_mark = start_mark
        self.end_mark = end_mark

    def __repr__(self) -> str:  # noqa: D105
        attributes = [
            key for key in ["anchor", "tag", "implicit", "value"] if hasattr(self, key)
        ]
        arguments = ", ".join([f"{key}={getattr(self, key)!r}" for key in attributes])
        return f"{self.__class__.__name__}({arguments})"


class NodeEvent(Event):  # noqa: D101
    def __init__(self, anchor, start_mark=None, end_mark=None) -> None:  # noqa: ANN001, D107
        self.anchor = anchor
        self.start_mark = start_mark
        self.end_mark = end_mark


class CollectionStartEvent(NodeEvent):  # noqa: D101
    def __init__(  # noqa: D107, PLR0913, PLR0917
        self,
        anchor,  # noqa: ANN001
        tag,  # noqa: ANN001
        implicit,  # noqa: ANN001
        start_mark=None,  # noqa: ANN001
        end_mark=None,  # noqa: ANN001
        flow_style=None,  # noqa: ANN001
    ) -> None:
        self.anchor = anchor
        self.tag = tag
        self.implicit = implicit
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.flow_style = flow_style


class CollectionEndEvent(Event):  # noqa: D101
    pass


# Implementations.


class StreamStartEvent(Event):  # noqa: D101
    def __init__(self, start_mark=None, end_mark=None, encoding=None) -> None:  # noqa: ANN001, D107
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.encoding = encoding


class StreamEndEvent(Event):  # noqa: D101
    pass


class DocumentStartEvent(Event):  # noqa: D101
    def __init__(  # noqa: D107
        self,
        start_mark=None,  # noqa: ANN001
        end_mark=None,  # noqa: ANN001
        explicit=None,  # noqa: ANN001
        version=None,  # noqa: ANN001
        tags=None,  # noqa: ANN001
    ) -> None:
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.explicit = explicit
        self.version = version
        self.tags = tags


class DocumentEndEvent(Event):  # noqa: D101
    def __init__(self, start_mark=None, end_mark=None, explicit=None) -> None:  # noqa: ANN001, D107
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.explicit = explicit


class AliasEvent(NodeEvent):  # noqa: D101
    pass


class ScalarEvent(NodeEvent):  # noqa: D101
    def __init__(  # noqa: D107, PLR0913, PLR0917
        self,
        anchor,  # noqa: ANN001
        tag,  # noqa: ANN001
        implicit,  # noqa: ANN001
        value,  # noqa: ANN001
        start_mark=None,  # noqa: ANN001
        end_mark=None,  # noqa: ANN001
        style=None,  # noqa: ANN001
    ) -> None:
        self.anchor = anchor
        self.tag = tag
        self.implicit = implicit
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.style = style


class SequenceStartEvent(CollectionStartEvent):  # noqa: D101
    pass


class SequenceEndEvent(CollectionEndEvent):  # noqa: D101
    pass


class MappingStartEvent(CollectionStartEvent):  # noqa: D101
    pass


class MappingEndEvent(CollectionEndEvent):  # noqa: D101
    pass
