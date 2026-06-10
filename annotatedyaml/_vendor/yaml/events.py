# Abstract classes.  # noqa: D100


class Event:  # noqa: D101
    def __init__(self, start_mark=None, end_mark=None) -> None:  # noqa: ANN001
        """Initialize the event with optional start and end position marks.

        Parameters:
            start_mark: Optional mark indicating the start position of the event.
            end_mark: Optional mark indicating the end position of the event.
        """
        self.start_mark = start_mark
        self.end_mark = end_mark

    def __repr__(self) -> str:
        """Build a representation string listing the event's present attributes among `anchor`, `tag`, `implicit`, and `value`.

        Returns:
            representation (str): String in the form `ClassName(key=value, ...)` containing only attributes that exist on the instance.
        """  # noqa: E501
        attributes = [
            key for key in ["anchor", "tag", "implicit", "value"] if hasattr(self, key)
        ]
        arguments = ", ".join([f"{key}={getattr(self, key)!r}" for key in attributes])
        return f"{self.__class__.__name__}({arguments})"


class NodeEvent(Event):  # noqa: D101
    def __init__(self, anchor, start_mark=None, end_mark=None) -> None:  # noqa: ANN001
        """Initialize the event with a node anchor and optional source position marks.

        Parameters:
            anchor: Anchor identifier for the node (may be None).
            start_mark: Optional mark indicating the start position in the source.
            end_mark: Optional mark indicating the end position in the source.
        """
        self.anchor = anchor
        self.start_mark = start_mark
        self.end_mark = end_mark


class CollectionStartEvent(NodeEvent):  # noqa: D101
    def __init__(  # noqa: PLR0913, PLR0917
        self,
        anchor,  # noqa: ANN001
        tag,  # noqa: ANN001
        implicit,  # noqa: ANN001
        start_mark=None,  # noqa: ANN001
        end_mark=None,  # noqa: ANN001
        flow_style=None,  # noqa: ANN001
    ) -> None:
        """Create a collection-start event with an anchor, tag, implicitness, optional position marks, and flow style.

        Parameters:
            anchor: Anchor name for the collection, or None if not anchored.
            tag: Tag for the collection, or None if not provided.
            implicit: `True` if the tag may be omitted, `False` if the tag is explicit.
            start_mark: Optional start position metadata for the event.
            end_mark: Optional end position metadata for the event.
            flow_style: `True` to request flow style, `False` to request block style, or `None` if unspecified.
        """  # noqa: E501
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
    def __init__(self, start_mark=None, end_mark=None, encoding=None) -> None:  # noqa: ANN001
        """Create a StreamStartEvent that records optional start/end position markers and an optional stream encoding.

        Parameters:
            start_mark: Optional start position marker associated with the event.
            end_mark: Optional end position marker associated with the event.
            encoding: Optional name of the stream's text encoding (e.g., "utf-8").
        """  # noqa: E501
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.encoding = encoding


class StreamEndEvent(Event):  # noqa: D101
    pass


class DocumentStartEvent(Event):  # noqa: D101
    def __init__(
        self,
        start_mark=None,  # noqa: ANN001
        end_mark=None,  # noqa: ANN001
        explicit=None,  # noqa: ANN001
        version=None,  # noqa: ANN001
        tags=None,  # noqa: ANN001
    ) -> None:
        """Initialize a document start event with optional position, explicitness, version, and tag declarations.

        Parameters:
            start_mark: Mark object indicating the event's start position, or None.
            end_mark: Mark object indicating the event's end position, or None.
            explicit: `True` if the document start marker (`---`) was present, `False` or `None` otherwise.
            version: YAML version tuple (e.g., `(major, minor)`) or `None` if unspecified.
            tags: Mapping of tag handles to tag prefixes, or `None` if no tag directives are present.
        """  # noqa: E501
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.explicit = explicit
        self.version = version
        self.tags = tags


class DocumentEndEvent(Event):  # noqa: D101
    def __init__(self, start_mark=None, end_mark=None, explicit=None) -> None:  # noqa: ANN001
        """Create a DocumentEndEvent with optional source position marks and explicitness.

        Parameters:
            start_mark: Optional start position metadata for the event.
            end_mark: Optional end position metadata for the event.
            explicit (bool | None): `True` if the document end marker was explicit, `False` if it was implicit, or `None` if unspecified.
        """  # noqa: E501
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.explicit = explicit


class AliasEvent(NodeEvent):  # noqa: D101
    pass


class ScalarEvent(NodeEvent):  # noqa: D101
    def __init__(  # noqa: PLR0913, PLR0917
        self,
        anchor,  # noqa: ANN001
        tag,  # noqa: ANN001
        implicit,  # noqa: ANN001
        value,  # noqa: ANN001
        start_mark=None,  # noqa: ANN001
        end_mark=None,  # noqa: ANN001
        style=None,  # noqa: ANN001
    ) -> None:
        """Initialize a ScalarEvent representing a scalar node with optional position, tag resolution, and presentation style.

        Parameters:
            anchor: Anchor identifier for the node (may be None).
            tag: The node's tag (may be None).
            implicit: Whether the tag was implicitly determined (`True`) or explicitly provided (`False`).
            value: The scalar node's value.
            start_mark: Optional start position mark for the node.
            end_mark: Optional end position mark for the node.
            style: Optional scalar presentation style (e.g., plain, single-quoted, double-quoted, literal).
        """  # noqa: E501
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
