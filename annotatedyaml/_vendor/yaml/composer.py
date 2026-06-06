__all__ = ["Composer", "ComposerError"]  # noqa: D100

from .error import MarkedYAMLError
from .events import *  # noqa: F403
from .nodes import *  # noqa: F403


class ComposerError(MarkedYAMLError):  # noqa: D101
    pass


class Composer:  # noqa: D101
    def __init__(self) -> None:  # noqa: D107
        self.anchors = {}

    def check_node(self) -> bool:  # noqa: D102
        # Drop the STREAM-START event.
        if self.check_event(StreamStartEvent):  # noqa: F405
            self.get_event()

        # If there are more documents available?
        return not self.check_event(StreamEndEvent)  # noqa: F405

    def get_node(self):  # noqa: ANN201, D102
        # Get the root node of the next document.
        if not self.check_event(StreamEndEvent):  # noqa: F405
            return self.compose_document()
        return None

    def get_single_node(self):  # noqa: ANN201, D102
        # Drop the STREAM-START event.
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

    def compose_document(self):  # noqa: ANN201, D102
        # Drop the DOCUMENT-START event.
        self.get_event()

        # Compose the root node.
        node = self.compose_node(None, None)

        # Drop the DOCUMENT-END event.
        self.get_event()

        self.anchors = {}
        return node

    def compose_node(self, parent, index):  # noqa: ANN001, ANN201, D102
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

    def compose_scalar_node(self, anchor):  # noqa: ANN001, ANN201, D102
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

    def compose_sequence_node(self, anchor):  # noqa: ANN001, ANN201, D102
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

    def compose_mapping_node(self, anchor):  # noqa: ANN001, ANN201, D102
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
