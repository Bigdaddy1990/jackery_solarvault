class Node:  # noqa: D100, D101
    def __init__(self, tag, value, start_mark, end_mark) -> None:  # noqa: ANN001
        """Initialize a Node with its tag, value, and optional source-location marks.

        Parameters:
            tag: YAML tag identifier for the node.
            value: Python object representing the node content.
            start_mark: Source-location mark for the node's start position, or None.
            end_mark: Source-location mark for the node's end position, or None.
        """
        self.tag = tag
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark

    def __repr__(self) -> str:
        """Concise string representation of the node that includes its runtime class name, tag, and value.

        The node's value is rendered with `repr()`.

        Returns:
            A string formatted as ClassName(tag=<tag_repr>, value=<value_repr>).
        """
        value = self.value
        # if isinstance(value, list):
        #    if len(value) == 0:
        #        value = '<empty>'
        #    elif len(value) == 1:
        #        value = '<1 item>'
        #    else:
        #        value = '<%d items>' % len(value)
        # else:
        #    if len(value) > 75:
        #        value = repr(value[:70]+u' ... ')
        #    else:
        #        value = repr(value)
        value = repr(value)
        return f"{self.__class__.__name__}(tag={self.tag!r}, value={value})"


class ScalarNode(Node):  # noqa: D101
    id = "scalar"

    def __init__(self, tag, value, start_mark=None, end_mark=None, style=None) -> None:  # noqa: ANN001
        """Initialize a YAML scalar node with a tag, value, optional start/end location marks, and an optional display style.

        Parameters:
                tag: The YAML tag for the scalar (e.g., "tag:yaml.org,2002:str").
                value: The scalar value.
                start_mark: Optional parse location for the start of the scalar or None.
                end_mark: Optional parse location for the end of the scalar or None.
                style: Optional scalar style indicator (e.g., plain, single-quoted, double-quoted) or None.
        """
        self.tag = tag
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.style = style


class CollectionNode(Node):  # noqa: D101
    def __init__(
        self,
        tag,  # noqa: ANN001
        value,  # noqa: ANN001
        start_mark=None,  # noqa: ANN001
        end_mark=None,  # noqa: ANN001
        flow_style=None,  # noqa: ANN001
    ) -> None:
        """
        Create a collection-style YAML node with a tag, contained value, optional source marks, and flow-style indicator.
        
        Parameters:
            tag: YAML tag that identifies the node.
            value: Node content — for SequenceNode, a list of child nodes; for MappingNode, a list of (key_node, value_node) pairs.
            start_mark: Optional source location marking the start of the node.
            end_mark: Optional source location marking the end of the node.
            flow_style: Optional flow-style indicator: True for flow form, False for block form, or None to preserve/leave unspecified.
        """
        self.tag = tag
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.flow_style = flow_style


class SequenceNode(CollectionNode):  # noqa: D101
    id = "sequence"


class MappingNode(CollectionNode):  # noqa: D101
    id = "mapping"
