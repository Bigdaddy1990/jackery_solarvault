class Node:  # noqa: D100, D101
    def __init__(self, tag, value, start_mark, end_mark) -> None:  # noqa: ANN001, D107
        """
        Initialize the node with its tag, value, and source-location marks.
        
        Parameters:
            tag: The node's tag (e.g., a YAML tag identifier).
            value: The node's associated value (any Python object representing the node content).
            start_mark: The mark indicating the node's start position in the source (or None).
            end_mark: The mark indicating the node's end position in the source (or None).
        """
        self.tag = tag
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark

    def __repr__(self) -> str:  # noqa: D105
        """
        Return a concise string representation of the node showing its class name, tag, and value.
        
        The node's value is rendered using its `repr()` form.
        
        Returns:
            A string in the format `ClassName(tag=<tag_repr>, value=<value_repr>)`.
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

    def __init__(self, tag, value, start_mark=None, end_mark=None, style=None) -> None:  # noqa: ANN001, D107
        """
        Create a YAML scalar node that stores its tag, value, optional start/end location marks, and display style.
        
        Parameters:
            tag: The YAML tag associated with the scalar (e.g., "tag:yaml.org,2002:str").
            value: The scalar value.
            start_mark: Optional parse location for the start of the scalar (mark object) or None.
            end_mark: Optional parse location for the end of the scalar (mark object) or None.
            style: Optional scalar style indicator (e.g., plain, single-quoted, double-quoted) or None.
        """
        self.tag = tag
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.style = style


class CollectionNode(Node):  # noqa: D101
    def __init__(  # noqa: D107
        self,
        tag,  # noqa: ANN001
        value,  # noqa: ANN001
        start_mark=None,  # noqa: ANN001
        end_mark=None,  # noqa: ANN001
        flow_style=None,  # noqa: ANN001
    ) -> None:
        """
        Initialize a collection-style YAML node with its tag, contained value, optional source marks, and flow style.
        
        Parameters:
            tag: The YAML tag identifying the node.
            value: The node's content — for sequences, a list of child nodes; for mappings, a list of key/value node pairs.
            start_mark: Optional source location marking the start of the node.
            end_mark: Optional source location marking the end of the node.
            flow_style: Optional flag controlling flow style: `True` for flow form, `False` for block form, or `None` to preserve the original/unspecified style.
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
