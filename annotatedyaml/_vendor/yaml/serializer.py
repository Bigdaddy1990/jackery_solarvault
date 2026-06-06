__all__ = ["Serializer", "SerializerError"]  # noqa: D100

from .error import YAMLError
from .events import *  # noqa: F403
from .nodes import *  # noqa: F403


class SerializerError(YAMLError):  # noqa: D101
    pass


class Serializer:  # noqa: D101
    ANCHOR_TEMPLATE = "id%03d"

    def __init__(  # noqa: D107
        self,
        encoding=None,  # noqa: ANN001
        explicit_start=None,  # noqa: ANN001
        explicit_end=None,  # noqa: ANN001
        version=None,  # noqa: ANN001
        tags=None,  # noqa: ANN001
    ) -> None:
        self.use_encoding = encoding
        self.use_explicit_start = explicit_start
        self.use_explicit_end = explicit_end
        self.use_version = version
        self.use_tags = tags
        self.serialized_nodes = {}
        self.anchors = {}
        self.last_anchor_id = 0
        self.closed = None

    def open(self) -> None:  # noqa: D102
        if self.closed is None:
            self.emit(StreamStartEvent(encoding=self.use_encoding))  # noqa: F405
            self.closed = False
        elif self.closed:
            raise SerializerError("serializer is closed")  # noqa: TRY003
        else:
            raise SerializerError("serializer is already opened")  # noqa: TRY003

    def close(self) -> None:  # noqa: D102
        if self.closed is None:
            raise SerializerError("serializer is not opened")  # noqa: TRY003
        if not self.closed:
            self.emit(StreamEndEvent())  # noqa: F405
            self.closed = True

    # def __del__(self):
    #    self.close()

    def serialize(self, node) -> None:  # noqa: ANN001, D102
        if self.closed is None:
            raise SerializerError("serializer is not opened")  # noqa: TRY003
        if self.closed:
            raise SerializerError("serializer is closed")  # noqa: TRY003
        self.emit(
            DocumentStartEvent(  # noqa: F405
                explicit=self.use_explicit_start,
                version=self.use_version,
                tags=self.use_tags,
            )
        )
        self.anchor_node(node)
        self.serialize_node(node, None, None)
        self.emit(DocumentEndEvent(explicit=self.use_explicit_end))  # noqa: F405
        self.serialized_nodes = {}
        self.anchors = {}
        self.last_anchor_id = 0

    def anchor_node(self, node) -> None:  # noqa: ANN001, D102
        if node in self.anchors:
            if self.anchors[node] is None:
                self.anchors[node] = self.generate_anchor(node)
        else:
            self.anchors[node] = None
            if isinstance(node, SequenceNode):  # noqa: F405
                for item in node.value:
                    self.anchor_node(item)
            elif isinstance(node, MappingNode):  # noqa: F405
                for key, value in node.value:
                    self.anchor_node(key)
                    self.anchor_node(value)

    def generate_anchor(self, node):  # noqa: ANN001, ANN201, D102
        self.last_anchor_id += 1
        return self.ANCHOR_TEMPLATE % self.last_anchor_id

    def serialize_node(self, node, parent, index) -> None:  # noqa: ANN001, D102
        alias = self.anchors[node]
        if node in self.serialized_nodes:
            self.emit(AliasEvent(alias))  # noqa: F405
        else:
            self.serialized_nodes[node] = True
            self.descend_resolver(parent, index)
            if isinstance(node, ScalarNode):  # noqa: F405
                detected_tag = self.resolve(ScalarNode, node.value, (True, False))  # noqa: F405
                default_tag = self.resolve(ScalarNode, node.value, (False, True))  # noqa: F405
                implicit = (node.tag == detected_tag), (node.tag == default_tag)
                self.emit(
                    ScalarEvent(alias, node.tag, implicit, node.value, style=node.style)  # noqa: F405
                )
            elif isinstance(node, SequenceNode):  # noqa: F405
                implicit = node.tag == self.resolve(SequenceNode, node.value, True)  # noqa: F405
                self.emit(
                    SequenceStartEvent(  # noqa: F405
                        alias, node.tag, implicit, flow_style=node.flow_style
                    )
                )
                index = 0
                for item in node.value:
                    self.serialize_node(item, node, index)
                    index += 1  # noqa: SIM113
                self.emit(SequenceEndEvent())  # noqa: F405
            elif isinstance(node, MappingNode):  # noqa: F405
                implicit = node.tag == self.resolve(MappingNode, node.value, True)  # noqa: F405
                self.emit(
                    MappingStartEvent(  # noqa: F405
                        alias, node.tag, implicit, flow_style=node.flow_style
                    )
                )
                for key, value in node.value:
                    self.serialize_node(key, node, None)
                    self.serialize_node(value, node, key)
                self.emit(MappingEndEvent())  # noqa: F405
            self.ascend_resolver()
