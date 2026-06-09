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

    def __init__(self) -> None:
        """Initialize parser internal state and prepare for parsing.

        Sets up lookahead cache, directive-derived YAML version and tag handle storage, parser continuation stacks, collection mark stack, and assigns the initial parsing state to begin stream parsing.
        """  # noqa: E501
        self.current_event = None
        self.yaml_version = None
        self.tag_handles = {}
        self.states = []
        self.marks = []
        self.state = self.parse_stream_start

    def dispose(self) -> None:
        # Reset the state attributes (to clear self-references)
        """
        Clear parser state and release references held by continuation/state objects.
        
        Resets the internal state stack and clears the current state function to break reference cycles and allow resources to be freed.
        """  # noqa: E501
        self.states = []
        self.state = None

    def check_event(self, *choices) -> bool:  # noqa: ANN002
        # Check the type of the next event.
        """
        Check whether the next parse event matches any of the provided event classes.
        
        Parameters:
            *choices (type): Zero or more event classes to test the next event against. If omitted, the method checks only for the presence of a next event.
        
        Returns:
            `true` if the next event is an instance of any provided classes, or (when no classes are given) if any next event is available; `false` otherwise.
        """  # noqa: E501
        if self.current_event is None and self.state:
            self.current_event = self.state()
        if self.current_event is not None:
            if not choices:
                return True
            for choice in choices:
                if isinstance(self.current_event, choice):
                    return True
        return False

    def peek_event(self):  # noqa: ANN201
        # Get the next event.
        """Peek at the next parser event without advancing the parser.

        Subsequent calls return the same event until `get_event()` is called to consume it.

        Returns:
            Event or None: The next parsing event, or `None` when the parser has no further events.
        """  # noqa: E501
        if self.current_event is None and self.state:
            self.current_event = self.state()
        return self.current_event

    def get_event(self):  # noqa: ANN201
        # Get the next event and proceed further.
        """Retrieve and consume the next parser event.

        If no event is currently cached, compute it using the parser's current state function. The returned event is removed from the lookahead cache so subsequent calls will advance the parser.

        Returns:
            The next Event object, or `None` if no more events are available.
        """  # noqa: E501
        if self.current_event is None and self.state:
            self.current_event = self.state()
        value = self.current_event
        self.current_event = None
        return value

    # stream    ::= STREAM-START implicit_document? explicit_document* STREAM-END
    # implicit_document ::= block_node DOCUMENT-END*
    # explicit_document ::= DIRECTIVE* DOCUMENT-START block_node? DOCUMENT-END*

    def parse_stream_start(self):  # noqa: ANN201
        # Parse the stream start.
        """Emit a StreamStartEvent for the next token and transition the parser to parse an implicit document start.

        Returns:
            StreamStartEvent: Event constructed from the consumed StreamStartToken's start and end marks and its encoding.
        """  # noqa: E501
        token = self.get_token()
        event = StreamStartEvent(  # noqa: F405
            token.start_mark, token.end_mark, encoding=token.encoding
        )

        # Prepare the next state.
        self.state = self.parse_implicit_document_start

        return event

    def parse_implicit_document_start(self):  # noqa: ANN201
        # Parse an implicit document.
        """Initiate parsing of an implicit YAML document when the next token does not start a directive, an explicit document, or the stream end.

        When an implicit document is started, records the default tag handles, emits a DocumentStartEvent with explicit=False, and updates the parser state to parse the document content and its end. If the stream begins with a directive, an explicit document start, or stream end, delegates to the explicit document-start handling and returns that resulting event.

        Returns:
            DocumentStartEvent: for an implicit document, or the event produced when handling an explicit document start or stream end.
        """  # noqa: E501
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

    def parse_document_start(self):  # noqa: ANN201
        # Parse any extra document end indicators.
        """Begin parsing an explicit YAML document or the end of the stream and produce the corresponding start or end event.

        If a document is present, processes any leading directives and returns a DocumentStartEvent for an explicit document; the parser state is advanced to parse the document content and the document-end handler is pushed onto the internal state stack. If the stream has ended, returns a StreamEndEvent and clears the parser state.

        Raises:
            ParserError: If directives were processed but a `<document start>` token is not found.

        Returns:
            DocumentStartEvent or StreamEndEvent: A `DocumentStartEvent` for an explicit document, or a `StreamEndEvent` when the stream end is encountered.
        """  # noqa: D420, E501
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

    def parse_document_end(self):  # noqa: ANN201
        # Parse the document end.
        """Create a DocumentEndEvent for the current document, consuming a DocumentEndToken if one is present.

        Returns:
            DocumentEndEvent: Event representing the end of the document. The `explicit` attribute is `True` if a `DocumentEndToken` was consumed; `start_mark` and `end_mark` reflect the token marks used.
        """  # noqa: E501
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

    def parse_document_content(self):  # noqa: ANN201
        """
        Generate the event for the first node of the current document or an empty scalar when the document is empty.
        
        If the next token marks a document boundary (directive, document start/end, or stream end), emit an empty ScalarEvent using that token's start mark and restore the previous parser state; otherwise parse and return the first block node's event.
        
        Returns:
            event (Event): An empty `ScalarEvent` at the document's start mark if the document is empty, otherwise the event for the document's first node.
        """  # noqa: E501
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

    def process_directives(self):  # noqa: ANN201
        """
        Process consecutive YAML directive tokens and update parser state.
        
        Reads `DirectiveToken`s from the token stream and applies their effects:
        - `YAML` directive: records the version in `self.yaml_version`; rejects duplicates and any major version other than 1.
        - `TAG` directive: records tag handle → prefix mappings in `self.tag_handles`; rejects duplicate handles.
        After processing, ensures any missing default tag handles from `DEFAULT_TAGS` are added to `self.tag_handles`.
        
        Returns:
            (version, tags): `version` is the parsed YAML version tuple (e.g., `(1, 2)`) or `None` if no YAML directive was present; `tags` is a copy of the explicit tag-handle mapping if any `TAG` directives were provided, or `None` otherwise.
        
        Raises:
            ParserError: if a duplicate `YAML` directive is found, if a `YAML` directive specifies a major version other than 1, or if a duplicate tag handle is encountered.
        """  # noqa: E501
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

    def parse_block_node(self):  # noqa: ANN201
        """
        Parse the next YAML node using block-style grammar.
        
        Returns:
            event: The event representing the parsed node (e.g., `ScalarEvent`, `SequenceStartEvent`, `MappingStartEvent`, or `AliasEvent`).
        """  # noqa: E501
        return self.parse_node(block=True)

    def parse_flow_node(self):  # noqa: ANN201
        """
        Parse a flow-style YAML node.
        
        Returns:
            event: A YAML event representing the next flow node — a scalar, sequence, mapping, alias, or an empty scalar.
        """  # noqa: E501
        return self.parse_node()

    def parse_block_node_or_indentless_sequence(self):  # noqa: ANN201
        """Parse either a block node or an indentless sequence.

        Returns:
            Event: The next parsing event representing the parsed node (e.g., a `ScalarEvent`,
            `SequenceStartEvent`, `MappingStartEvent`, or `AliasEvent`).
        """  # noqa: E501
        return self.parse_node(block=True, indentless_sequence=True)

    def parse_node(self, block=False, indentless_sequence=False):  # noqa: ANN001, ANN201, PLR0912, PLR0915
        """Parse a single YAML node and produce the corresponding parsing event.

        This method consumes tokens for one node (alias, scalar, sequence, or mapping), resolves optional anchor and tag properties (raising ParserError for undefined tag handles), and may change the parser's state to continue parsing the node's contents. If an anchor or tag is present but no node content follows, an empty scalar event is produced. If no valid node form is found, a ParserError is raised.

        Parameters:
            block (bool): If True, allow block-style collections (block sequence/mapping) as valid node forms.
            indentless_sequence (bool): If True, treat a leading block entry as the start of an indentless sequence.

        Returns:
            event: The created event instance representing the parsed node (e.g., AliasEvent, ScalarEvent, SequenceStartEvent, MappingStartEvent).
        """  # noqa: E501
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

    def parse_block_sequence_first_entry(self):  # noqa: ANN201
        """
        Start parsing a block sequence and parse its first entry.
        
        Returns:
            event: The parser event for the sequence's first entry — either a `SequenceStartEvent` or the event produced by parsing that entry.
        """  # noqa: E501
        token = self.get_token()
        self.marks.append(token.start_mark)
        return self.parse_block_sequence_entry()

    def parse_block_sequence_entry(self):  # noqa: ANN201
        """Parse a single entry or the end of a block sequence.

        If a block entry token is present, parse its content as a block node; if the entry is immediately followed by another entry or the end of the block, emit an empty scalar for that entry. If a block end token is present, emit a SequenceEndEvent and restore the previous parser state. If neither an entry nor a block end is found, raise a ParserError describing the unexpected token.

        Returns:
            SequenceEndEvent when the block sequence is closed, a ScalarEvent representing an empty entry when an empty entry is encountered, or the event produced by parsing a non-empty block node.

        Raises:
            ParserError: when the next token is not a block entry or a block end.
        """  # noqa: E501
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

    def parse_indentless_sequence_entry(self):  # noqa: ANN201
        """
        Parse an entry of an indentless block sequence or emit the sequence end.
        
        If a block entry token is present, returns the event for the entry's node or an empty scalar when the entry is omitted. If no block entry token is present, emits a SequenceEndEvent and restores the previous parser state.
        
        Returns:
            event: A YAML event representing the parsed node or empty scalar for the current entry, or a `SequenceEndEvent` marking the end of the indentless sequence.
        """  # noqa: E501
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

    def parse_block_mapping_first_key(self):  # noqa: ANN201
        """
        Start a block mapping and parse its first key.
        
        Appends the mapping's start mark to the internal mark stack and delegates to parsing the mapping's first key.
        
        Returns:
            The parser event produced by parsing the mapping's first key.
        """  # noqa: E501
        token = self.get_token()
        self.marks.append(token.start_mark)
        return self.parse_block_mapping_key()

    def parse_block_mapping_key(self):  # noqa: ANN201
        """Parse the next key of a block mapping or emit the mapping end event.

        Parses a mapping key when a `KeyToken` is present (parsing either an explicit key node
        or emitting an empty scalar for an empty key). If the block mapping is closed,
        consumes the `BlockEndToken` and returns a `MappingEndEvent`. If an unexpected token
        is found where the block end was expected, raises a `ParserError`.

        Returns:
            MappingEndEvent when the block mapping is closed; otherwise the event produced
            for the parsed key (a `ScalarEvent` for an empty key or the first event of the
            parsed node).

        Raises:
            ParserError: If neither a `KeyToken` nor `BlockEndToken` is found where expected.
        """  # noqa: E501
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

    def parse_block_mapping_value(self):  # noqa: ANN201
        """Parse the value part of a block mapping entry.

        If a `ValueToken` is present, consume it and:
        - if the following token begins a node, push `parse_block_mapping_key` as the continuation and parse that node;
        - otherwise set the parser state to `parse_block_mapping_key` and produce an empty scalar at the value token's end mark.

        If no `ValueToken` is present, set the parser state to `parse_block_mapping_key` and produce an empty scalar at the next token's start mark.

        Returns:
            An event representing the parsed node or an empty `ScalarEvent` when the mapping value is absent.
        """  # noqa: E501
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

    def parse_flow_sequence_first_entry(self):  # noqa: ANN201
        """
        Begin parsing a flow sequence and return the event for its first entry.
        
        Pushes the flow sequence start mark onto the parser's mark stack and delegates parsing to parse_flow_sequence_entry(first=True).
        
        Returns:
            The parsing event for the first entry of the flow sequence.
        """  # noqa: E501
        token = self.get_token()
        self.marks.append(token.start_mark)
        return self.parse_flow_sequence_entry(first=True)

    def parse_flow_sequence_entry(self, first=False):  # noqa: ANN001, ANN201
        """Parse the next entry in a flow sequence.

        When called, this either:
        - returns a MappingStartEvent if an inline mapping (a key) begins at the current position,
        - defers to and returns the event produced by parsing a flow node for a sequence entry,
        - or returns a SequenceEndEvent when the flow sequence is closed.

        Parameters:
            first (bool): True if parsing the first entry of the sequence (no leading comma expected); False if subsequent entries (a leading comma is required).

        Returns:
            Event: The YAML parsing event produced for the sequence entry (MappingStartEvent, a node event from parse_flow_node, or SequenceEndEvent).
        """  # noqa: E501
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

    def parse_flow_sequence_entry_mapping_key(self):  # noqa: ANN201
        """
        Parse a mapping key used as an entry inside a flow sequence.
        
        If a non-empty key node follows, push the mapping-value continuation and return the parsed key node event. If the key is omitted, set the next state to parse the mapping value and return an empty scalar event representing the missing key.
        
        Returns:
            The parsed key node event, or an empty scalar event when the key is omitted.
        """  # noqa: E501
        token = self.get_token()
        if not self.check_token(ValueToken, FlowEntryToken, FlowSequenceEndToken):  # noqa: F405
            self.states.append(self.parse_flow_sequence_entry_mapping_value)
            return self.parse_flow_node()
        self.state = self.parse_flow_sequence_entry_mapping_value
        return self.process_empty_scalar(token.end_mark)

    def parse_flow_sequence_entry_mapping_value(self):  # noqa: ANN201
        """
        Parse the value for a mapping entry inside a flow sequence.
        
        Sets the parser state to resume at the mapping-entry end. If a `ValueToken` is present and followed by a value node, parses and returns that flow-node event (pushing a continuation to handle the mapping-entry end afterwards); otherwise emits and returns an empty `ScalarEvent` for an omitted value.
        
        Returns:
            An event representing the mapping value: the parsed flow-node event if a value node is present, or an empty `ScalarEvent` when the value is absent.
        """  # noqa: E501
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

    def parse_flow_sequence_entry_mapping_end(self):  # noqa: ANN201
        """
        Emit a MappingEndEvent for an inline mapping inside a flow sequence and restore the parser state.
        
        The event uses the current token's start mark for both its start and end marks; the parser state is set to `parse_flow_sequence_entry`.
        
        Returns:
            MappingEndEvent: Event whose start and end marks are the current token's start mark.
        """  # noqa: E501
        self.state = self.parse_flow_sequence_entry
        token = self.peek_token()
        return MappingEndEvent(token.start_mark, token.start_mark)  # noqa: F405

    # flow_mapping  ::= FLOW-MAPPING-START
    #                   (flow_mapping_entry FLOW-ENTRY)*
    #                   flow_mapping_entry?
    #                   FLOW-MAPPING-END
    # flow_mapping_entry    ::= flow_node | KEY flow_node? (VALUE flow_node?)?

    def parse_flow_mapping_first_key(self):  # noqa: ANN201
        """
        Begin parsing a flow mapping and return the event for its first key or the mapping end.
        
        Returns:
            Event: The event representing the mapping's first key, or a `MappingEndEvent` if the flow mapping is empty.
        """  # noqa: E501
        token = self.get_token()
        self.marks.append(token.start_mark)
        return self.parse_flow_mapping_key(first=True)

    def parse_flow_mapping_key(self, first=False):  # noqa: ANN001, ANN201
        """
        Parse the next key (or an empty key/value) or the end of a flow mapping and return the corresponding parsing event.
        
        Parameters:
            first (bool): True when parsing the mapping's first key so a leading comma is not required.
        
        Returns:
            MappingEndEvent when the flow mapping is closed, `ScalarEvent` for an omitted/empty key or value, or the event produced by parsing a flow node used as a key.
        
        Raises:
            ParserError: If a mapping separator is required but an unexpected token is encountered (neither `,` nor `}`).
        """  # noqa: D206, E101, E501
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

    def parse_flow_mapping_value(self):  # noqa: ANN201
        """
        Parse the value of a flow mapping entry and return the corresponding event.
        
        If a `ValueToken` precedes an actual node, parse that node and push the continuation to resume parsing the next mapping key. If the value is omitted (either because `ValueToken` is followed by a separator/end or no `ValueToken` is present), produce an empty `ScalarEvent`. In all cases the parser state is set to continue parsing the next mapping key.
        
        Returns:
            yaml.events.Event: An event representing the mapping value — either the parsed node's event or an empty `ScalarEvent` when the value is omitted.
        """  # noqa: E501
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

    def parse_flow_mapping_empty_value(self):  # noqa: ANN201
        """
        Emit an empty scalar for a missing value in a flow mapping and prepare to parse the next key.
        
        Sets the parser state to `parse_flow_mapping_key` and returns a `ScalarEvent` created at the current token's start mark.
        
        Returns:
            ScalarEvent: An empty scalar event at the current token's start mark.
        """  # noqa: E501
        self.state = self.parse_flow_mapping_key
        return self.process_empty_scalar(self.peek_token().start_mark)

    def process_empty_scalar(self, mark):  # noqa: ANN001, ANN201, PLR6301
        """Create a ScalarEvent representing an empty YAML scalar at the given mark.

        Parameters:
                mark: The mark to use for both the start and end positions of the empty scalar.

        Returns:
                ScalarEvent: An event for an empty scalar (value ""), with no anchor or explicit tag and implicit flags (True, False), located at `mark`.
        """  # noqa: D206, E101, E501
        return ScalarEvent(None, None, (True, False), "", mark, mark)  # noqa: F405
