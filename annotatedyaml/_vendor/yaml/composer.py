__all__ = ["Composer", "ComposerError"]  # noqa: D100

from .error import MarkedYAMLError
from .events import *  # noqa: F403
from .nodes import *  # noqa: F403


class ComposerError(MarkedYAMLError):  # noqa: D101
    pass


class Composer:  # noqa: D101
    def __init__(self) -> None:
        """Initialize the composer and its anchor registry.

        Creates self.anchors, a dictionary that maps anchor names (strings) to composed node objects used to resolve aliases during document composition.
        """  # noqa: E501
        self.anchors = {}

    def check_node(self) -> bool:
        # Drop the STREAM-START event.
        """Indicates whether another document is available in the event stream.

        If a leading `StreamStartEvent` is present, it is consumed.

        Returns:
            `true` if the stream has not reached a `StreamEndEvent` (another document is available), `false` otherwise.
        """  # noqa: E501
        if self.check_event(StreamStartEvent):  # noqa: F405
            self.get_event()

        # If there are more documents available?
        return not self.check_event(StreamEndEvent)  # noqa: F405

    def get_node(self):  # noqa: ANN201
        # Get the root node of the next document.
        """Retrieve the root node of the next YAML document or None if the stream has been exhausted.

        Returns:
            node (Node | None): The composed document root node, or None when no further documents are available.
        """  # noqa: E501
        if not self.check_event(StreamEndEvent):  # noqa: F405
            return self.compose_document()
        return None

    def get_single_node(self):  # noqa: ANN201
        # Drop the STREAM-START event.
        """Compose and return the single document from the current YAML stream, or None if the stream is empty.

        This consumes the initial STREAM-START and final STREAM-END events. If a document is present, it is composed and returned; if the stream is empty, returns `None`.

        Returns:
            The document root node, or `None` if the stream contains no documents.

        Raises:
            ComposerError: If more than one document is found in the stream.
        """  # noqa: E501
        self.get_event()

        # Compose a document if the stream is not empty.
        document = None
        if not self.check_event(StreamEndEvent):  # noqa: F405
            document = self.compose_document()

        # Ensure that the stream contains no more documents.
        if not self.check_event(StreamEndEvent):  # noqa: F405
            event = self.get_event()
            raise ComposerError(  # noqa: TRY003
                "expected a single document in the stream",
                document.start_mark,
                "but found another document",
                event.start_mark,
            )

        # Drop the STREAM-END event.
        self.get_event()

        return document

    def compose_document(self):  # noqa: ANN201
        # Drop the DOCUMENT-START event.
        """Compose a single YAML document from the event stream.

        Consumes the document start and end events and resets the composer's anchor registry for the next document.

        Returns:
            The root node of the composed document.
        """  # noqa: E501
        self.get_event()

        # Compose the root node.
        node = self.compose_node(None, None)

        # Drop the DOCUMENT-END event.
        self.get_event()

        self.anchors = {}
        return node

    def compose_node(self, parent, index):  # noqa: ANN001, ANN201
        """Compose and return a node for the next events in the stream, resolving anchors and aliases.

        Parameters:
            parent: The parent node used to inform resolver context, or `None` if there is no parent.
            index: The index (position) within the parent used to inform resolver context, or `None` when not applicable.

        Returns:
            node: The composed node (a `ScalarNode`, `SequenceNode`, or `MappingNode`), or an existing node referenced by an alias.

        Raises:
            ComposerError: If an alias refers to an undefined anchor or if an anchor is defined more than once.
        """  # noqa: E501
        if self.check_event(AliasEvent):  # noqa: F405
            event = self.get_event()
            anchor = event.anchor
            if anchor not in self.anchors:
                raise ComposerError(
                    None, None, f"found undefined alias {anchor!r}", event.start_mark
                )
            return self.anchors[anchor]
        event = self.peek_event()
        anchor = event.anchor
        if anchor is not None and anchor in self.anchors:
            raise ComposerError(  # noqa: TRY003
                f"found duplicate anchor {anchor!r}; first occurrence",
                self.anchors[anchor].start_mark,
                "second occurrence",
                event.start_mark,
            )
        self.descend_resolver(parent, index)
        if self.check_event(ScalarEvent):  # noqa: F405
            node = self.compose_scalar_node(anchor)
        elif self.check_event(SequenceStartEvent):  # noqa: F405
            node = self.compose_sequence_node(anchor)
        elif self.check_event(MappingStartEvent):  # noqa: F405
            node = self.compose_mapping_node(anchor)
        self.ascend_resolver()
        return node

    def compose_scalar_node(self, anchor):  # noqa: ANN001, ANN201
        """Compose and return a ScalarNode for the next scalar event, registering it under `anchor` if provided.

        Parameters:
            anchor (str | None): Anchor name to register the created node under, or `None` to skip registration.

        Returns:
            ScalarNode: The composed scalar node with its tag resolved when the event tag is `None` or `"!"`.
        """  # noqa: E501
        event = self.get_event()
        tag = event.tag
        if tag is None or tag == "!":
            tag = self.resolve(ScalarNode, event.value, event.implicit)  # noqa: F405
        node = ScalarNode(  # noqa: F405
            tag, event.value, event.start_mark, event.end_mark, style=event.style
        )
        if anchor is not None:
            self.anchors[anchor] = node
        return node

    def compose_sequence_node(self, anchor):  # noqa: ANN001, ANN201
        """Compose a sequence node representing the next YAML sequence in the event stream.

        Creates a SequenceNode for the upcoming sequence start event, resolves the node tag when unspecified, registers the node under `anchor` if provided, composes and appends child nodes until the sequence end event, sets the node's end mark, and returns the completed SequenceNode.

        Parameters:
            anchor (str | None): Anchor name to associate with the created node, or None if no anchor.

        Returns:
            SequenceNode: The composed sequence node containing its children and end mark.
        """  # noqa: E501
        start_event = self.get_event()
        tag = start_event.tag
        if tag is None or tag == "!":
            tag = self.resolve(SequenceNode, None, start_event.implicit)  # noqa: F405
        node = SequenceNode(  # noqa: F405
            tag, [], start_event.start_mark, None, flow_style=start_event.flow_style
        )
        if anchor is not None:
            self.anchors[anchor] = node
        index = 0
        while not self.check_event(SequenceEndEvent):  # noqa: F405
            node.value.append(self.compose_node(node, index))
            index += 1
        end_event = self.get_event()
        node.end_mark = end_event.end_mark
        return node

    def compose_mapping_node(self, anchor):  # noqa: ANN001, ANN201
        """Compose a mapping node from the upcoming events in the stream.

        Resolves the node tag if unspecified, creates a MappingNode, registers it under `anchor` if provided, then composes key/value node pairs until the mapping end event and sets the node's end mark.

        Parameters:
            anchor (str | None): Optional anchor name to register the composed node under.

        Returns:
            MappingNode: The composed mapping node whose `value` is a list of (key_node, value_node) pairs.
        """  # noqa: E501
        start_event = self.get_event()
        tag = start_event.tag
        if tag is None or tag == "!":
            tag = self.resolve(MappingNode, None, start_event.implicit)  # noqa: F405
        node = MappingNode(  # noqa: F405
            tag, [], start_event.start_mark, None, flow_style=start_event.flow_style
        )
        if anchor is not None:
            self.anchors[anchor] = node
        while not self.check_event(MappingEndEvent):  # noqa: F405
            # key_event = self.peek_event()
            item_key = self.compose_node(node, None)
            # if item_key in node.value:
            #    raise ComposerError("while composing a mapping", start_event.start_mark,  # noqa: E501
            #            "found duplicate key", key_event.start_mark)
            item_value = self.compose_node(node, item_key)
            # node.value[item_key] = item_value
            node.value.append((item_key, item_value))
        end_event = self.get_event()
        node.end_mark = end_event.end_mark
        return node
