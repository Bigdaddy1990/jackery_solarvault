__all__ = ["Serializer", "SerializerError"]  # noqa: D100

from .error import YAMLError
from .events import *  # noqa: F403
from .nodes import *  # noqa: F403


class SerializerError(YAMLError):  # noqa: D101
    pass


class Serializer:  # noqa: D101
    ANCHOR_TEMPLATE = "id%03d"

    def __init__(
        self,
        encoding=None,  # noqa: ANN001
        explicit_start=None,  # noqa: ANN001
        explicit_end=None,  # noqa: ANN001
        version=None,  # noqa: ANN001
        tags=None,  # noqa: ANN001
    ) -> None:
        """Initialize the serializer's configuration and internal tracking state.

        Parameters:
            encoding (str | None): Optional document encoding to emit (e.g., "utf-8"); when None the encoding is omitted.
            explicit_start (bool | None): If True, emit an explicit document start marker; if False, omit it; if None, leave behavior to defaults.
            explicit_end (bool | None): If True, emit an explicit document end marker; if False, omit it; if None, leave behavior to defaults.
            version (tuple | None): Optional YAML version tuple to include in the document start (e.g., (1, 2)); if None no version is emitted.
            tags (dict | None): Optional mapping of tag handles to tag prefixes to include in the document start; if None no tags are emitted.
        """  # noqa: E501
        self.use_encoding = encoding
        self.use_explicit_start = explicit_start
        self.use_explicit_end = explicit_end
        self.use_version = version
        self.use_tags = tags
        self.serialized_nodes = {}
        self.anchors = {}
        self.last_anchor_id = 0
        self.closed = None

    def open(self) -> None:
        """
        Open the serializer and start a YAML stream.
        
        If the serializer has not been opened, emits a StreamStartEvent using the configured encoding and marks the serializer as opened. Raises SerializerError if the serializer is already opened or has already been closed.
        """  # noqa: E501
        if self.closed is None:
            self.emit(StreamStartEvent(encoding=self.use_encoding))  # noqa: F405
            self.closed = False
        elif self.closed:
            raise SerializerError("serializer is closed")  # noqa: TRY003
        else:
            raise SerializerError("serializer is already opened")  # noqa: TRY003

    def close(self) -> None:
        """
        Close the serializer stream and mark the serializer as closed.
        
        If the serializer has not been opened, raises a SerializerError. If the
        serializer is open, emits a StreamEndEvent and sets the serializer state to
        closed. If the serializer is already closed, this method does nothing.
        
        Raises:
            SerializerError: If the serializer has not been opened.
        """
        if self.closed is None:
            raise SerializerError("serializer is not opened")  # noqa: TRY003
        if not self.closed:
            self.emit(StreamEndEvent())  # noqa: F405
            self.closed = True

    # def __del__(self):
    #    self.close()

    def serialize(self, node) -> None:  # noqa: ANN001
        """
        Serialize the given node tree into a YAML document by emitting the appropriate stream and document events.
        
        Parameters:
            node (Node): Root node of the node tree to serialize (for example, ScalarNode, SequenceNode, or MappingNode).
        
        Raises:
            SerializerError: If the serializer has not been opened or has already been closed.
        """  # noqa: E501
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

    def anchor_node(self, node) -> None:  # noqa: ANN001
        """
        Register the given node and its descendants in the serializer's anchor table and assign a generated anchor if the same node is seen more than once.
        
        This updates self.anchors so every visited node has an entry (initially None). If a node is encountered again, an anchor identifier is generated and stored for that node. Sequence and mapping children are processed recursively.
        
        Parameters:
            node (ScalarNode | SequenceNode | MappingNode): The root node to register; for sequences and mappings, child nodes are traversed recursively.
        """  # noqa: E501
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

    def generate_anchor(self, node):  # noqa: ANN001, ANN201
        """
        Generate a unique anchor identifier for a node.
        
        Parameters:
            node: The node to assign an anchor to.
        
        Returns:
            str: Anchor string formatted with ANCHOR_TEMPLATE and a monotonically increasing numeric id (e.g., "id001").
        """  # noqa: E501
        self.last_anchor_id += 1
        return self.ANCHOR_TEMPLATE % self.last_anchor_id

    def serialize_node(self, node, parent, index) -> None:  # noqa: ANN001
        """
        Emit YAML events for `node`, handling aliases for repeated nodes and emitting the appropriate scalar, sequence, or mapping event sequences.
        
        If `node` has already been serialized, an AliasEvent for the node's anchor is emitted. Otherwise the node is marked as serialized, the resolver context is adjusted based on `parent` and `index`, the node's events are emitted (including implicit tag decisions), and the resolver context is restored.
        
        Parameters:
            node: The node to serialize (ScalarNode, SequenceNode, or MappingNode).
            parent: The parent node used to establish resolver context; may be None.
            index: The position of `node` within `parent` used by the resolver; may be an integer, a key node, or None.
        """  # noqa: E501
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
