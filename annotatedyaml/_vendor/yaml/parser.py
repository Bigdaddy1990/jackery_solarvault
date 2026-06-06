# The following YAML grammar is LL(1) and is parsed by a recursive descent  # noqa: D100
# parser.
#
# stream            ::= STREAM-START implicit_document? explicit_document* STREAM-END
# implicit_document ::= block_node DOCUMENT-END*
# explicit_document ::= DIRECTIVE* DOCUMENT-START block_node? DOCUMENT-END*
# block_node_or_indentless_sequence ::=
#                       ALIAS
#                       | properties (block_content | indentless_block_sequence)?
#                       | block_content
#                       | indentless_block_sequence
# block_node        ::= ALIAS
#                       | properties block_content?
#                       | block_content
# flow_node         ::= ALIAS
#                       | properties flow_content?
#                       | flow_content
# properties        ::= TAG ANCHOR? | ANCHOR TAG?
# block_content     ::= block_collection | flow_collection | SCALAR
# flow_content      ::= flow_collection | SCALAR
# block_collection  ::= block_sequence | block_mapping
# flow_collection   ::= flow_sequence | flow_mapping
# block_sequence    ::= BLOCK-SEQUENCE-START (BLOCK-ENTRY block_node?)* BLOCK-END
# indentless_sequence   ::= (BLOCK-ENTRY block_node?)+
# block_mapping     ::= BLOCK-MAPPING_START
#                       ((KEY block_node_or_indentless_sequence?)?
#                       (VALUE block_node_or_indentless_sequence?)?)*
#                       BLOCK-END
# flow_sequence     ::= FLOW-SEQUENCE-START
#                       (flow_sequence_entry FLOW-ENTRY)*
#                       flow_sequence_entry?
#                       FLOW-SEQUENCE-END
# flow_sequence_entry   ::= flow_node | KEY flow_node? (VALUE flow_node?)?
# flow_mapping      ::= FLOW-MAPPING-START
#                       (flow_mapping_entry FLOW-ENTRY)*
#                       flow_mapping_entry?
#                       FLOW-MAPPING-END
# flow_mapping_entry    ::= flow_node | KEY flow_node? (VALUE flow_node?)?
#
# FIRST sets:
#
# stream: { STREAM-START }
# explicit_document: { DIRECTIVE DOCUMENT-START }
# implicit_document: FIRST(block_node)
# block_node: { ALIAS TAG ANCHOR SCALAR BLOCK-SEQUENCE-START BLOCK-MAPPING-START FLOW-SEQUENCE-START FLOW-MAPPING-START }  # noqa: E501
# flow_node: { ALIAS ANCHOR TAG SCALAR FLOW-SEQUENCE-START FLOW-MAPPING-START }
# block_content: { BLOCK-SEQUENCE-START BLOCK-MAPPING-START FLOW-SEQUENCE-START FLOW-MAPPING-START SCALAR }  # noqa: E501
# flow_content: { FLOW-SEQUENCE-START FLOW-MAPPING-START SCALAR }
# block_collection: { BLOCK-SEQUENCE-START BLOCK-MAPPING-START }
# flow_collection: { FLOW-SEQUENCE-START FLOW-MAPPING-START }
# block_sequence: { BLOCK-SEQUENCE-START }
# block_mapping: { BLOCK-MAPPING-START }
# block_node_or_indentless_sequence: { ALIAS ANCHOR TAG SCALAR BLOCK-SEQUENCE-START BLOCK-MAPPING-START FLOW-SEQUENCE-START FLOW-MAPPING-START BLOCK-ENTRY }  # noqa: E501
# indentless_sequence: { ENTRY }
# flow_collection: { FLOW-SEQUENCE-START FLOW-MAPPING-START }
# flow_sequence: { FLOW-SEQUENCE-START }
# flow_mapping: { FLOW-MAPPING-START }
# flow_sequence_entry: { ALIAS ANCHOR TAG SCALAR FLOW-SEQUENCE-START FLOW-MAPPING-START KEY }  # noqa: E501
# flow_mapping_entry: { ALIAS ANCHOR TAG SCALAR FLOW-SEQUENCE-START FLOW-MAPPING-START KEY }  # noqa: E501

__all__ = ["Parser", "ParserError"]

from .error import MarkedYAMLError
from .events import *  # noqa: F403
from .scanner import *  # noqa: F403
from .tokens import *  # noqa: F403


class ParserError(MarkedYAMLError):  # noqa: D101
    pass


class Parser:  # noqa: D101, PLR0904
    # Since writing a recursive-descendant parser is a straightforward task, we
    # do not give many comments here.

    DEFAULT_TAGS = {  # noqa: RUF012
        "!": "!",
        "!!": "tag:yaml.org,2002:",
    }

    def __init__(self) -> None:  # noqa: D107
        self.current_event = None
        self.yaml_version = None
        self.tag_handles = {}
        self.states = []
        self.marks = []
        self.state = self.parse_stream_start

    def dispose(self) -> None:  # noqa: D102
        # Reset the state attributes (to clear self-references)
        self.states = []
        self.state = None

    def check_event(self, *choices) -> bool:  # noqa: ANN002, D102
        # Check the type of the next event.
        if self.current_event is None and self.state:
            self.current_event = self.state()
        if self.current_event is not None:
            if not choices:
                return True
            for choice in choices:
                if isinstance(self.current_event, choice):
                    return True
        return False

    def peek_event(self):  # noqa: ANN201, D102
        # Get the next event.
        if self.current_event is None and self.state:
            self.current_event = self.state()
        return self.current_event

    def get_event(self):  # noqa: ANN201, D102
        # Get the next event and proceed further.
        if self.current_event is None and self.state:
            self.current_event = self.state()
        value = self.current_event
        self.current_event = None
        return value

    # stream    ::= STREAM-START implicit_document? explicit_document* STREAM-END
    # implicit_document ::= block_node DOCUMENT-END*
    # explicit_document ::= DIRECTIVE* DOCUMENT-START block_node? DOCUMENT-END*

    def parse_stream_start(self):  # noqa: ANN201, D102

        # Parse the stream start.
        token = self.get_token()
        event = StreamStartEvent(  # noqa: F405
            token.start_mark, token.end_mark, encoding=token.encoding
        )

        # Prepare the next state.
        self.state = self.parse_implicit_document_start

        return event

    def parse_implicit_document_start(self):  # noqa: ANN201, D102

        # Parse an implicit document.
        if not self.check_token(DirectiveToken, DocumentStartToken, StreamEndToken):  # noqa: F405
            self.tag_handles = self.DEFAULT_TAGS
            token = self.peek_token()
            start_mark = end_mark = token.start_mark
            event = DocumentStartEvent(start_mark, end_mark, explicit=False)  # noqa: F405

            # Prepare the next state.
            self.states.append(self.parse_document_end)
            self.state = self.parse_block_node

            return event

        return self.parse_document_start()

    def parse_document_start(self):  # noqa: ANN201, D102

        # Parse any extra document end indicators.
        while self.check_token(DocumentEndToken):  # noqa: F405
            self.get_token()

        # Parse an explicit document.
        if not self.check_token(StreamEndToken):  # noqa: F405
            token = self.peek_token()
            start_mark = token.start_mark
            version, tags = self.process_directives()
            if not self.check_token(DocumentStartToken):  # noqa: F405
                raise ParserError(
                    None,
                    None,
                    f"expected '<document start>', but found {self.peek_token().id!r}",
                    self.peek_token().start_mark,
                )
            token = self.get_token()
            end_mark = token.end_mark
            event = DocumentStartEvent(  # noqa: F405
                start_mark, end_mark, explicit=True, version=version, tags=tags
            )
            self.states.append(self.parse_document_end)
            self.state = self.parse_document_content
        else:
            # Parse the end of the stream.
            token = self.get_token()
            event = StreamEndEvent(token.start_mark, token.end_mark)  # noqa: F405
            assert not self.states  # noqa: RUF100, S101
            assert not self.marks  # noqa: RUF100, S101
            self.state = None
        return event

    def parse_document_end(self):  # noqa: ANN201, D102

        # Parse the document end.
        token = self.peek_token()
        start_mark = end_mark = token.start_mark
        explicit = False
        if self.check_token(DocumentEndToken):  # noqa: F405
            token = self.get_token()
            end_mark = token.end_mark
            explicit = True
        event = DocumentEndEvent(start_mark, end_mark, explicit=explicit)  # noqa: F405

        # Prepare the next state.
        self.state = self.parse_document_start

        return event

    def parse_document_content(self):  # noqa: ANN201, D102
        if self.check_token(
            DirectiveToken,  # noqa: F405
            DocumentStartToken,  # noqa: F405
            DocumentEndToken,  # noqa: F405
            StreamEndToken,  # noqa: F405
        ):
            event = self.process_empty_scalar(self.peek_token().start_mark)
            self.state = self.states.pop()
            return event
        return self.parse_block_node()

    def process_directives(self):  # noqa: ANN201, D102
        self.yaml_version = None
        self.tag_handles = {}
        while self.check_token(DirectiveToken):  # noqa: F405
            token = self.get_token()
            if token.name == "YAML":
                if self.yaml_version is not None:
                    raise ParserError(
                        None, None, "found duplicate YAML directive", token.start_mark
                    )
                major, _minor = token.value
                if major != 1:
                    raise ParserError(
                        None,
                        None,
                        "found incompatible YAML document (version 1.* is required)",
                        token.start_mark,
                    )
                self.yaml_version = token.value
            elif token.name == "TAG":
                handle, prefix = token.value
                if handle in self.tag_handles:
                    raise ParserError(
                        None, None, f"duplicate tag handle {handle!r}", token.start_mark
                    )
                self.tag_handles[handle] = prefix
        if self.tag_handles:
            value = self.yaml_version, self.tag_handles.copy()
        else:
            value = self.yaml_version, None
        for key in self.DEFAULT_TAGS:
            if key not in self.tag_handles:
                self.tag_handles[key] = self.DEFAULT_TAGS[key]
        return value

    # block_node_or_indentless_sequence ::= ALIAS
    #               | properties (block_content | indentless_block_sequence)?
    #               | block_content
    #               | indentless_block_sequence
    # block_node    ::= ALIAS
    #                   | properties block_content?
    #                   | block_content
    # flow_node     ::= ALIAS
    #                   | properties flow_content?
    #                   | flow_content
    # properties    ::= TAG ANCHOR? | ANCHOR TAG?
    # block_content     ::= block_collection | flow_collection | SCALAR
    # flow_content      ::= flow_collection | SCALAR
    # block_collection  ::= block_sequence | block_mapping
    # flow_collection   ::= flow_sequence | flow_mapping

    def parse_block_node(self):  # noqa: ANN201, D102
        return self.parse_node(block=True)

    def parse_flow_node(self):  # noqa: ANN201, D102
        return self.parse_node()

    def parse_block_node_or_indentless_sequence(self):  # noqa: ANN201, D102
        return self.parse_node(block=True, indentless_sequence=True)

    def parse_node(self, block=False, indentless_sequence=False):  # noqa: ANN001, ANN201, D102, PLR0912, PLR0915
        if self.check_token(AliasToken):  # noqa: F405
            token = self.get_token()
            event = AliasEvent(token.value, token.start_mark, token.end_mark)  # noqa: F405
            self.state = self.states.pop()
        else:
            anchor = None
            tag = None
            start_mark = end_mark = tag_mark = None
            if self.check_token(AnchorToken):  # noqa: F405
                token = self.get_token()
                start_mark = token.start_mark
                end_mark = token.end_mark
                anchor = token.value
                if self.check_token(TagToken):  # noqa: F405
                    token = self.get_token()
                    tag_mark = token.start_mark
                    end_mark = token.end_mark
                    tag = token.value
            elif self.check_token(TagToken):  # noqa: F405
                token = self.get_token()
                start_mark = tag_mark = token.start_mark
                end_mark = token.end_mark
                tag = token.value
                if self.check_token(AnchorToken):  # noqa: F405
                    token = self.get_token()
                    end_mark = token.end_mark
                    anchor = token.value
            if tag is not None:
                handle, suffix = tag
                if handle is not None:
                    if handle not in self.tag_handles:
                        raise ParserError(  # noqa: TRY003
                            "while parsing a node",
                            start_mark,
                            f"found undefined tag handle {handle!r}",
                            tag_mark,
                        )
                    tag = self.tag_handles[handle] + suffix
                else:
                    tag = suffix
            # if tag == '!':
            #    raise ParserError("while parsing a node", start_mark,
            #            "found non-specific tag '!'", tag_mark,
            #            "Please check 'http://pyyaml.org/wiki/YAMLNonSpecificTag' and share your opinion.")  # noqa: E501
            if start_mark is None:
                start_mark = end_mark = self.peek_token().start_mark
            event = None
            implicit = tag is None or tag == "!"
            if indentless_sequence and self.check_token(BlockEntryToken):  # noqa: F405
                end_mark = self.peek_token().end_mark
                event = SequenceStartEvent(anchor, tag, implicit, start_mark, end_mark)  # noqa: F405
                self.state = self.parse_indentless_sequence_entry
            elif self.check_token(ScalarToken):  # noqa: F405
                token = self.get_token()
                end_mark = token.end_mark
                if (token.plain and tag is None) or tag == "!":
                    implicit = (True, False)
                elif tag is None:
                    implicit = (False, True)
                else:
                    implicit = (False, False)
                event = ScalarEvent(  # noqa: F405
                    anchor,
                    tag,
                    implicit,
                    token.value,
                    start_mark,
                    end_mark,
                    style=token.style,
                )
                self.state = self.states.pop()
            elif self.check_token(FlowSequenceStartToken):  # noqa: F405
                end_mark = self.peek_token().end_mark
                event = SequenceStartEvent(  # noqa: F405
                    anchor, tag, implicit, start_mark, end_mark, flow_style=True
                )
                self.state = self.parse_flow_sequence_first_entry
            elif self.check_token(FlowMappingStartToken):  # noqa: F405
                end_mark = self.peek_token().end_mark
                event = MappingStartEvent(  # noqa: F405
                    anchor, tag, implicit, start_mark, end_mark, flow_style=True
                )
                self.state = self.parse_flow_mapping_first_key
            elif block and self.check_token(BlockSequenceStartToken):  # noqa: F405
                end_mark = self.peek_token().start_mark
                event = SequenceStartEvent(  # noqa: F405
                    anchor, tag, implicit, start_mark, end_mark, flow_style=False
                )
                self.state = self.parse_block_sequence_first_entry
            elif block and self.check_token(BlockMappingStartToken):  # noqa: F405
                end_mark = self.peek_token().start_mark
                event = MappingStartEvent(  # noqa: F405
                    anchor, tag, implicit, start_mark, end_mark, flow_style=False
                )
                self.state = self.parse_block_mapping_first_key
            elif anchor is not None or tag is not None:
                # Empty scalars are allowed even if a tag or an anchor is
                # specified.
                event = ScalarEvent(  # noqa: F405
                    anchor, tag, (implicit, False), "", start_mark, end_mark
                )
                self.state = self.states.pop()
            else:
                node = "block" if block else "flow"
                token = self.peek_token()
                raise ParserError(  # noqa: TRY003
                    f"while parsing a {node} node",
                    start_mark,
                    f"expected the node content, but found {token.id!r}",
                    token.start_mark,
                )
        return event

    # block_sequence ::= BLOCK-SEQUENCE-START (BLOCK-ENTRY block_node?)* BLOCK-END

    def parse_block_sequence_first_entry(self):  # noqa: ANN201, D102
        token = self.get_token()
        self.marks.append(token.start_mark)
        return self.parse_block_sequence_entry()

    def parse_block_sequence_entry(self):  # noqa: ANN201, D102
        if self.check_token(BlockEntryToken):  # noqa: F405
            token = self.get_token()
            if not self.check_token(BlockEntryToken, BlockEndToken):  # noqa: F405
                self.states.append(self.parse_block_sequence_entry)
                return self.parse_block_node()
            self.state = self.parse_block_sequence_entry
            return self.process_empty_scalar(token.end_mark)
        if not self.check_token(BlockEndToken):  # noqa: F405
            token = self.peek_token()
            raise ParserError(  # noqa: TRY003
                "while parsing a block collection",
                self.marks[-1],
                f"expected <block end>, but found {token.id!r}",
                token.start_mark,
            )
        token = self.get_token()
        event = SequenceEndEvent(token.start_mark, token.end_mark)  # noqa: F405
        self.state = self.states.pop()
        self.marks.pop()
        return event

    # indentless_sequence ::= (BLOCK-ENTRY block_node?)+

    def parse_indentless_sequence_entry(self):  # noqa: ANN201, D102
        if self.check_token(BlockEntryToken):  # noqa: F405
            token = self.get_token()
            if not self.check_token(
                BlockEntryToken,  # noqa: F405
                KeyToken,  # noqa: F405
                ValueToken,  # noqa: F405
                BlockEndToken,  # noqa: F405
            ):
                self.states.append(self.parse_indentless_sequence_entry)
                return self.parse_block_node()
            self.state = self.parse_indentless_sequence_entry
            return self.process_empty_scalar(token.end_mark)
        token = self.peek_token()
        event = SequenceEndEvent(token.start_mark, token.start_mark)  # noqa: F405
        self.state = self.states.pop()
        return event

    # block_mapping     ::= BLOCK-MAPPING_START
    #                       ((KEY block_node_or_indentless_sequence?)?
    #                       (VALUE block_node_or_indentless_sequence?)?)*
    #                       BLOCK-END

    def parse_block_mapping_first_key(self):  # noqa: ANN201, D102
        token = self.get_token()
        self.marks.append(token.start_mark)
        return self.parse_block_mapping_key()

    def parse_block_mapping_key(self):  # noqa: ANN201, D102
        if self.check_token(KeyToken):  # noqa: F405
            token = self.get_token()
            if not self.check_token(KeyToken, ValueToken, BlockEndToken):  # noqa: F405
                self.states.append(self.parse_block_mapping_value)
                return self.parse_block_node_or_indentless_sequence()
            self.state = self.parse_block_mapping_value
            return self.process_empty_scalar(token.end_mark)
        if not self.check_token(BlockEndToken):  # noqa: F405
            token = self.peek_token()
            raise ParserError(  # noqa: TRY003
                "while parsing a block mapping",
                self.marks[-1],
                f"expected <block end>, but found {token.id!r}",
                token.start_mark,
            )
        token = self.get_token()
        event = MappingEndEvent(token.start_mark, token.end_mark)  # noqa: F405
        self.state = self.states.pop()
        self.marks.pop()
        return event

    def parse_block_mapping_value(self):  # noqa: ANN201, D102
        if self.check_token(ValueToken):  # noqa: F405
            token = self.get_token()
            if not self.check_token(KeyToken, ValueToken, BlockEndToken):  # noqa: F405
                self.states.append(self.parse_block_mapping_key)
                return self.parse_block_node_or_indentless_sequence()
            self.state = self.parse_block_mapping_key
            return self.process_empty_scalar(token.end_mark)
        self.state = self.parse_block_mapping_key
        token = self.peek_token()
        return self.process_empty_scalar(token.start_mark)

    # flow_sequence     ::= FLOW-SEQUENCE-START
    #                       (flow_sequence_entry FLOW-ENTRY)*
    #                       flow_sequence_entry?
    #                       FLOW-SEQUENCE-END
    # flow_sequence_entry   ::= flow_node | KEY flow_node? (VALUE flow_node?)?
    #
    # Note that while production rules for both flow_sequence_entry and
    # flow_mapping_entry are equal, their interpretations are different.
    # For `flow_sequence_entry`, the part `KEY flow_node? (VALUE flow_node?)?`
    # generate an inline mapping (set syntax).

    def parse_flow_sequence_first_entry(self):  # noqa: ANN201, D102
        token = self.get_token()
        self.marks.append(token.start_mark)
        return self.parse_flow_sequence_entry(first=True)

    def parse_flow_sequence_entry(self, first=False):  # noqa: ANN001, ANN201, D102
        if not self.check_token(FlowSequenceEndToken):  # noqa: F405
            if not first:
                if self.check_token(FlowEntryToken):  # noqa: F405
                    self.get_token()
                else:
                    token = self.peek_token()
                    raise ParserError(  # noqa: TRY003
                        "while parsing a flow sequence",
                        self.marks[-1],
                        f"expected ',' or ']', but got {token.id!r}",
                        token.start_mark,
                    )

            if self.check_token(KeyToken):  # noqa: F405
                token = self.peek_token()
                event = MappingStartEvent(  # noqa: F405
                    None, None, True, token.start_mark, token.end_mark, flow_style=True
                )
                self.state = self.parse_flow_sequence_entry_mapping_key
                return event
            if not self.check_token(FlowSequenceEndToken):  # noqa: F405
                self.states.append(self.parse_flow_sequence_entry)
                return self.parse_flow_node()
        token = self.get_token()
        event = SequenceEndEvent(token.start_mark, token.end_mark)  # noqa: F405
        self.state = self.states.pop()
        self.marks.pop()
        return event

    def parse_flow_sequence_entry_mapping_key(self):  # noqa: ANN201, D102
        token = self.get_token()
        if not self.check_token(ValueToken, FlowEntryToken, FlowSequenceEndToken):  # noqa: F405
            self.states.append(self.parse_flow_sequence_entry_mapping_value)
            return self.parse_flow_node()
        self.state = self.parse_flow_sequence_entry_mapping_value
        return self.process_empty_scalar(token.end_mark)

    def parse_flow_sequence_entry_mapping_value(self):  # noqa: ANN201, D102
        if self.check_token(ValueToken):  # noqa: F405
            token = self.get_token()
            if not self.check_token(FlowEntryToken, FlowSequenceEndToken):  # noqa: F405
                self.states.append(self.parse_flow_sequence_entry_mapping_end)
                return self.parse_flow_node()
            self.state = self.parse_flow_sequence_entry_mapping_end
            return self.process_empty_scalar(token.end_mark)
        self.state = self.parse_flow_sequence_entry_mapping_end
        token = self.peek_token()
        return self.process_empty_scalar(token.start_mark)

    def parse_flow_sequence_entry_mapping_end(self):  # noqa: ANN201, D102
        self.state = self.parse_flow_sequence_entry
        token = self.peek_token()
        return MappingEndEvent(token.start_mark, token.start_mark)  # noqa: F405

    # flow_mapping  ::= FLOW-MAPPING-START
    #                   (flow_mapping_entry FLOW-ENTRY)*
    #                   flow_mapping_entry?
    #                   FLOW-MAPPING-END
    # flow_mapping_entry    ::= flow_node | KEY flow_node? (VALUE flow_node?)?

    def parse_flow_mapping_first_key(self):  # noqa: ANN201, D102
        token = self.get_token()
        self.marks.append(token.start_mark)
        return self.parse_flow_mapping_key(first=True)

    def parse_flow_mapping_key(self, first=False):  # noqa: ANN001, ANN201, D102
        if not self.check_token(FlowMappingEndToken):  # noqa: F405
            if not first:
                if self.check_token(FlowEntryToken):  # noqa: F405
                    self.get_token()
                else:
                    token = self.peek_token()
                    raise ParserError(  # noqa: TRY003
                        "while parsing a flow mapping",
                        self.marks[-1],
                        f"expected ',' or '}}', but got {token.id!r}",
                        token.start_mark,
                    )
            if self.check_token(KeyToken):  # noqa: F405
                token = self.get_token()
                if not self.check_token(
                    ValueToken,  # noqa: F405
                    FlowEntryToken,  # noqa: F405
                    FlowMappingEndToken,  # noqa: F405
                ):
                    self.states.append(self.parse_flow_mapping_value)
                    return self.parse_flow_node()
                self.state = self.parse_flow_mapping_value
                return self.process_empty_scalar(token.end_mark)
            if not self.check_token(FlowMappingEndToken):  # noqa: F405
                self.states.append(self.parse_flow_mapping_empty_value)
                return self.parse_flow_node()
        token = self.get_token()
        event = MappingEndEvent(token.start_mark, token.end_mark)  # noqa: F405
        self.state = self.states.pop()
        self.marks.pop()
        return event

    def parse_flow_mapping_value(self):  # noqa: ANN201, D102
        if self.check_token(ValueToken):  # noqa: F405
            token = self.get_token()
            if not self.check_token(FlowEntryToken, FlowMappingEndToken):  # noqa: F405
                self.states.append(self.parse_flow_mapping_key)
                return self.parse_flow_node()
            self.state = self.parse_flow_mapping_key
            return self.process_empty_scalar(token.end_mark)
        self.state = self.parse_flow_mapping_key
        token = self.peek_token()
        return self.process_empty_scalar(token.start_mark)

    def parse_flow_mapping_empty_value(self):  # noqa: ANN201, D102
        self.state = self.parse_flow_mapping_key
        return self.process_empty_scalar(self.peek_token().start_mark)

    def process_empty_scalar(self, mark):  # noqa: ANN001, ANN201, D102, PLR6301
        return ScalarEvent(None, None, (True, False), "", mark, mark)  # noqa: F405
