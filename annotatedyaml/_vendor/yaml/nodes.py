class Node:  # noqa: D100, D101
    def __init__(self, tag, value, start_mark, end_mark) -> None:  # noqa: ANN001, D107
        self.tag = tag
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark

    def __repr__(self) -> str:  # noqa: D105
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
        self.tag = tag
        self.value = value
        self.start_mark = start_mark
        self.end_mark = end_mark
        self.flow_style = flow_style


class SequenceNode(CollectionNode):  # noqa: D101
    id = "sequence"


class MappingNode(CollectionNode):  # noqa: D101
    id = "mapping"
