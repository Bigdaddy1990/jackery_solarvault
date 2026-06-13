# Emitter expects events obeying the following grammar:  # noqa: D100
# stream ::= STREAM-START document* STREAM-END
# document ::= DOCUMENT-START node DOCUMENT-END
# node ::= SCALAR | sequence | mapping
# sequence ::= SEQUENCE-START node* SEQUENCE-END
# mapping ::= MAPPING-START (node node)* MAPPING-END

__all__ = ["Emitter", "EmitterError"]

from typing import Never

from .error import YAMLError
from .events import *  # noqa: F403


class EmitterError(YAMLError):  # noqa: D101
    pass


class ScalarAnalysis:
    def __init__(  # noqa: PLR0913, PLR0917
        self,
        scalar,  # noqa: ANN001
        empty,  # noqa: ANN001
        multiline,  # noqa: ANN001
        allow_flow_plain,  # noqa: ANN001
        allow_block_plain,  # noqa: ANN001
        allow_single_quoted,  # noqa: ANN001
        allow_double_quoted,  # noqa: ANN001
        allow_block,  # noqa: ANN001
    ) -> None:
        """Record which scalar styles and formatting are allowed for a given scalar value.

        Parameters:
            scalar (str): The original scalar text.
            empty (bool): True if the scalar is empty.
            multiline (bool): True if the scalar contains one or more line breaks.
            allow_flow_plain (bool): True if a plain scalar is allowed in flow context.
            allow_block_plain (bool): True if a plain scalar is allowed in block context.
            allow_single_quoted (bool): True if single-quoted style is allowed.
            allow_double_quoted (bool): True if double-quoted style is allowed.
            allow_block (bool): True if a block scalar (literal or folded) is allowed.
        """
        self.scalar = scalar
        self.empty = empty
        self.multiline = multiline
        self.allow_flow_plain = allow_flow_plain
        self.allow_block_plain = allow_block_plain
        self.allow_single_quoted = allow_single_quoted
        self.allow_double_quoted = allow_double_quoted
        self.allow_block = allow_block


class Emitter:  # noqa: D101, PLR0904
    DEFAULT_TAG_PREFIXES = {  # noqa: RUF012
        "!": "!",
        "tag:yaml.org,2002:": "!!",
    }

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        stream,  # noqa: ANN001
        canonical=None,  # noqa: ANN001
        indent=None,  # noqa: ANN001
        width=None,  # noqa: ANN001
        allow_unicode=None,  # noqa: ANN001
        line_break=None,  # noqa: ANN001
    ) -> None:
        # The stream should have the methods `write` and possibly `flush`.
        r"""Initialize the Emitter with output stream and formatting options and set up its internal state machine.

        Parameters:
            stream: An object with a `write` method (and optionally `flush`) where YAML will be written.
            canonical (bool | None): If true, emit YAML in canonical form; otherwise use normal formatting.
            indent (int | None): Preferred indentation width (used when 2 <= indent < 10); defaults to 2.
            width (int | None): Preferred maximum line width (used when > best_indent * 2); defaults to 80.
            allow_unicode (bool | None): If true, allow non-ASCII characters in output; otherwise escape as needed.
            line_break (str | None): Line break sequence to use; must be one of "\n", "\r", or "\r\n" if provided.

        Notes:
            The constructor configures default formatting parameters, initializes emission state (state
            stack, current state handler, event queue, indentation and flow contexts), and prepares
            buffers for tag/anchor and scalar analysis. It does not perform any I/O by itself.
        """
        self.stream = stream

        # Encoding can be overridden by STREAM-START.
        self.encoding = None

        # Emitter is a state machine with a stack of states to handle nested
        # structures.
        self.states = []
        self.state = self.expect_stream_start

        # Current event and the event queue.
        self.events = []
        self.event = None

        # The current indentation level and the stack of previous indents.
        self.indents = []
        self.indent = None

        # Flow level.
        self.flow_level = 0

        # Contexts.
        self.root_context = False
        self.sequence_context = False
        self.mapping_context = False
        self.simple_key_context = False

        # Characteristics of the last emitted character:
        #  - current position.
        #  - is it a whitespace?
        #  - is it an indention character
        #    (indentation space, '-', '?', or ':')?
        self.line = 0
        self.column = 0
        self.whitespace = True
        self.indention = True

        # Whether the document requires an explicit document indicator
        self.open_ended = False

        # Formatting details.
        self.canonical = canonical
        self.allow_unicode = allow_unicode
        self.best_indent = 2
        if indent and 1 < indent < 10:  # noqa: PLR2004
            self.best_indent = indent
        self.best_width = 80
        if width and width > self.best_indent * 2:
            self.best_width = width
        self.best_line_break = "\n"
        if line_break in {"\r", "\n", "\r\n"}:
            self.best_line_break = line_break

        # Tag prefixes.
        self.tag_prefixes = None

        # Prepared anchor and tag.
        self.prepared_anchor = None
        self.prepared_tag = None

        # Scalar analysis and style.
        self.analysis = None
        self.style = None

    def dispose(self) -> None:
        # Reset the state attributes (to clear self-references)
        """Clear the emitter's internal state references to facilitate disposal.

        Resets the state stack and current state handler (sets self.states to an empty list and self.state to None) to break references that could prevent garbage collection.
        """
        self.states = []
        self.state = None

    def emit(self, event) -> None:  # noqa: ANN001
        """Enqueue a YAML event and drive the emitter's state machine to process available events.

        Appends the given event to the internal queue and processes queued events until additional lookahead is required, producing YAML output to the configured stream and updating the emitter's internal state.

        Parameters:
            event: YAML event to emit (e.g., StreamStartEvent, DocumentStartEvent, ScalarEvent,
                   SequenceStartEvent, MappingStartEvent, AliasEvent, DocumentEndEvent,
                   StreamEndEvent).
        """
        self.events.append(event)
        while not self.need_more_events():
            self.event = self.events.pop(0)
            self.state()
            self.event = None

    # In some cases, we wait for a few next events before emitting.

    def need_more_events(self):  # noqa: ANN201
        """Determine whether additional input events are required before emitting the next event.

        Checks the queued events and returns True when:
        - no events are queued, or
        - the next event is a DocumentStartEvent (requires one additional event),
        - the next event is a SequenceStartEvent (requires two additional events),
        - the next event is a MappingStartEvent (requires three additional events).

        Returns:
            bool: `True` if more events are required to proceed, `False` otherwise.
        """
        if not self.events:
            return True
        event = self.events[0]
        if isinstance(event, DocumentStartEvent):  # noqa: F405
            return self.need_events(1)
        if isinstance(event, SequenceStartEvent):  # noqa: F405
            return self.need_events(2)
        if isinstance(event, MappingStartEvent):  # noqa: F405
            return self.need_events(3)
        return False

    def need_events(self, count):  # noqa: ANN001, ANN201
        """
        Decide whether more events must be available to satisfy a lookahead of `count` events.
        
        Parameters:
            count (int): Number of events to look ahead after the current event.
        
        Returns:
            bool: `True` if the event queue contains fewer than `count` events beyond the current event, `False` otherwise.
        """
        level = 0
        for event in self.events[1:]:
            if isinstance(event, (DocumentStartEvent, CollectionStartEvent)):  # noqa: F405
                level += 1
            elif isinstance(event, (DocumentEndEvent, CollectionEndEvent)):  # noqa: F405
                level -= 1
            elif isinstance(event, StreamEndEvent):  # noqa: F405
                level = -1
            if level < 0:
                return False
        return len(self.events) < count + 1

    def increase_indent(self, flow=False, indentless=False) -> None:  # noqa: ANN001
        """
        Adjust the emitter's indentation for a new nested context and save the previous indent.
        
        Parameters:
            flow (bool): When True and the current indent is None, set the indent to the emitter's configured best indent;
                otherwise, when the current indent is None, set it to 0.
            indentless (bool): When True, do not increase the current indent value (the previous indent is still pushed onto the indent stack).
        """
        self.indents.append(self.indent)
        if self.indent is None:
            if flow:
                self.indent = self.best_indent
            else:
                self.indent = 0
        elif not indentless:
            self.indent += self.best_indent

    # States.

    # Stream handlers.

    def expect_stream_start(self) -> None:
        """Handle a StreamStartEvent and transition the emitter to expect the first document start.

        If the current event is a StreamStartEvent, apply its encoding to the emitter when the output stream has no encoding, write the stream start marker, and set the next state to expect the first document start.

        Raises:
            EmitterError: If the current event is not a StreamStartEvent.
        """
        if isinstance(self.event, StreamStartEvent):  # noqa: F405
            if self.event.encoding and not hasattr(self.stream, "encoding"):
                self.encoding = self.event.encoding
            self.write_stream_start()
            self.state = self.expect_first_document_start
        else:
            raise EmitterError(f"expected StreamStartEvent, but got {self.event}")  # noqa: TRY003

    def expect_nothing(self) -> Never:
        """
        Signal that an event was received when the emitter expected no further events.
        
        Raises:
            EmitterError: always raised with a message describing the unexpected event (`self.event`).
        """
        raise EmitterError(f"expected nothing, but got {self.event}")  # noqa: TRY003

    # Document handlers.

    def expect_first_document_start(self):  # noqa: ANN201
        """Begin processing the first document start event and transition the emitter to process the document root."""
        return self.expect_document_start(first=True)

    def expect_document_start(self, first=False) -> None:  # noqa: ANN001
        """Process a document start or stream end event and transition the emitter to the next state.

        When the current event is a DocumentStartEvent, prepare and emit any YAML version and %TAG directives, update tag prefixes from the event, and emit a document start marker (`---`) unless the document may be emitted implicitly (e.g., the first document without directives and not canonical). When the current event is a StreamEndEvent, emit a pending end marker (`...`) if open-ended and finalize the stream.

        Parameters:
            first (bool): If True, treat this as the first document in the stream which may allow omitting an explicit `---` marker.

        Raises:
            EmitterError: If the current event is neither a DocumentStartEvent nor a StreamEndEvent.
        """
        if isinstance(self.event, DocumentStartEvent):  # noqa: F405
            if (self.event.version or self.event.tags) and self.open_ended:
                self.write_indicator("...", True)
                self.write_indent()
            if self.event.version:
                version_text = self.prepare_version(self.event.version)
                self.write_version_directive(version_text)
            self.tag_prefixes = self.DEFAULT_TAG_PREFIXES.copy()
            if self.event.tags:
                handles = sorted(self.event.tags.keys())
                for handle in handles:
                    prefix = self.event.tags[handle]
                    self.tag_prefixes[prefix] = handle
                    handle_text = self.prepare_tag_handle(handle)
                    prefix_text = self.prepare_tag_prefix(prefix)
                    self.write_tag_directive(handle_text, prefix_text)
            implicit = (
                first
                and not self.event.explicit
                and not self.canonical
                and not self.event.version
                and not self.event.tags
                and not self.check_empty_document()
            )
            if not implicit:
                self.write_indent()
                self.write_indicator("---", True)
                if self.canonical:
                    self.write_indent()
            self.state = self.expect_document_root
        elif isinstance(self.event, StreamEndEvent):  # noqa: F405
            if self.open_ended:
                self.write_indicator("...", True)
                self.write_indent()
            self.write_stream_end()
            self.state = self.expect_nothing
        else:
            raise EmitterError(f"expected DocumentStartEvent, but got {self.event}")  # noqa: TRY003

    def expect_document_end(self) -> None:
        """
        Handle a DocumentEndEvent by emitting the document terminator, flushing the stream, and transitioning to expect the next document start.
        
        If the current event is explicit, emits "..." as the document end indicator and writes indentation before flushing.
        
        Raises:
            EmitterError: if the current event is not a DocumentEndEvent.
        """
        if isinstance(self.event, DocumentEndEvent):  # noqa: F405
            self.write_indent()
            if self.event.explicit:
                self.write_indicator("...", True)
                self.write_indent()
            self.flush_stream()
            self.state = self.expect_document_start
        else:
            raise EmitterError(f"expected DocumentEndEvent, but got {self.event}")  # noqa: TRY003

    def expect_document_root(self) -> None:
        """
        Begin emitting a document's root node and schedule handling for the document end.
        
        Pushes the document-end state onto the internal state stack and dispatches emission for the document root node.
        """
        self.states.append(self.expect_document_end)
        self.expect_node(root=True)

    # Node handlers.

    def expect_node(
        self,
        root=False,  # noqa: ANN001
        sequence=False,  # noqa: ANN001
        mapping=False,  # noqa: ANN001
        simple_key=False,  # noqa: ANN001
    ) -> None:
        """
        Handle the current node event: set node context flags, emit any anchor and tag, and dispatch emission for the node.
        
        Configures the emitter context using the boolean flags and then emits the node according to its event type:
        - Emits an alias for AliasEvent.
        - Emits a scalar for ScalarEvent.
        - Emits a flow or block sequence for SequenceStartEvent depending on flow level, canonical mode, the event's flow style, or emptiness.
        - Emits a flow or block mapping for MappingStartEvent using analogous criteria.
        
        Raises:
            EmitterError: if the current event is not a recognized node event.
        """
        self.root_context = root
        self.sequence_context = sequence
        self.mapping_context = mapping
        self.simple_key_context = simple_key
        if isinstance(self.event, AliasEvent):  # noqa: F405
            self.expect_alias()
        elif isinstance(self.event, (ScalarEvent, CollectionStartEvent)):  # noqa: F405
            self.process_anchor("&")
            self.process_tag()
            if isinstance(self.event, ScalarEvent):  # noqa: F405
                self.expect_scalar()
            elif isinstance(self.event, SequenceStartEvent):  # noqa: F405
                if (
                    self.flow_level
                    or self.canonical
                    or self.event.flow_style
                    or self.check_empty_sequence()
                ):
                    self.expect_flow_sequence()
                else:
                    self.expect_block_sequence()
            elif isinstance(self.event, MappingStartEvent):  # noqa: F405
                if (
                    self.flow_level
                    or self.canonical
                    or self.event.flow_style
                    or self.check_empty_mapping()
                ):
                    self.expect_flow_mapping()
                else:
                    self.expect_block_mapping()
        else:
            raise EmitterError(f"expected NodeEvent, but got {self.event}")  # noqa: TRY003

    def expect_alias(self) -> None:
        """Emit an alias node for the current event and restore the previous emitter state.

        Raises:
            EmitterError: If the current event has no anchor.
        """
        if self.event.anchor is None:
            raise EmitterError("anchor is not specified for alias")  # noqa: TRY003
        self.process_anchor("*")
        self.state = self.states.pop()

    def expect_scalar(self) -> None:
        """
        Emit the current scalar event and restore the previous indentation and emitter state.
        
        In flow contexts this increases the indentation before emitting the scalar; after emission the previous `indent` and `state` values are restored.
        """
        self.increase_indent(flow=True)
        self.process_scalar()
        self.indent = self.indents.pop()
        self.state = self.states.pop()

    # Flow sequence handlers.

    def expect_flow_sequence(self) -> None:
        """Prepare the emitter to serialize a flow-style sequence.

        Writes the opening "[" indicator, increments the flow nesting level, adjusts indentation for flow context, and sets the emitter state to handle the first flow sequence item.
        """
        self.write_indicator("[", True, whitespace=True)
        self.flow_level += 1
        self.increase_indent(flow=True)
        self.state = self.expect_first_flow_sequence_item

    def expect_first_flow_sequence_item(self) -> None:
        """Emit the first item of a flow sequence or close the sequence if it is empty.

        If the current event is a SequenceEndEvent, closes the flow sequence and restores prior indentation and state; otherwise, writes indentation when required, registers the handler for subsequent items, and emits the first sequence element.
        """
        if isinstance(self.event, SequenceEndEvent):  # noqa: F405
            self.indent = self.indents.pop()
            self.flow_level -= 1
            self.write_indicator("]", False)
            self.state = self.states.pop()
        else:
            if self.canonical or self.column > self.best_width:
                self.write_indent()
            self.states.append(self.expect_flow_sequence_item)
            self.expect_node(sequence=True)

    def expect_flow_sequence_item(self) -> None:
        """Emit the next element of a flow-style sequence or close the sequence when it ends.

        If the current event is a sequence end, write the closing `]`, restore indentation and flow level, and return to the previous emitter state. Otherwise, write the item separator (`,`), optionally emit indentation when required, schedule this handler for subsequent items, and emit the next sequence element.
        """
        if isinstance(self.event, SequenceEndEvent):  # noqa: F405
            self.indent = self.indents.pop()
            self.flow_level -= 1
            if self.canonical:
                self.write_indicator(",", False)
                self.write_indent()
            self.write_indicator("]", False)
            self.state = self.states.pop()
        else:
            self.write_indicator(",", False)
            if self.canonical or self.column > self.best_width:
                self.write_indent()
            self.states.append(self.expect_flow_sequence_item)
            self.expect_node(sequence=True)

    # Flow mapping handlers.

    def expect_flow_mapping(self) -> None:
        """Begin emitting a mapping in flow style.

        Writes the opening `{`, increments the emitter's flow nesting level, adjusts indentation for flow context, and transitions the state machine to expect the first flow mapping key.
        """
        self.write_indicator("{", True, whitespace=True)
        self.flow_level += 1
        self.increase_indent(flow=True)
        self.state = self.expect_first_flow_mapping_key

    def expect_first_flow_mapping_key(self) -> None:
        """
        Emit the first key of a flow-style mapping or close the mapping if it ends.
        
        If the current event terminates the mapping, write the closing '}' and restore flow/indentation state. Otherwise, ensure proper indentation when in canonical mode or when the current line exceeds the configured width; then emit the key either as a simple key (when allowed) and schedule the simple-value handler, or as an explicit key indicator ('?') and schedule the normal mapping-value handler.
        """
        if isinstance(self.event, MappingEndEvent):  # noqa: F405
            self.indent = self.indents.pop()
            self.flow_level -= 1
            self.write_indicator("}", False)
            self.state = self.states.pop()
        else:
            if self.canonical or self.column > self.best_width:
                self.write_indent()
            if not self.canonical and self.check_simple_key():
                self.states.append(self.expect_flow_mapping_simple_value)
                self.expect_node(mapping=True, simple_key=True)
            else:
                self.write_indicator("?", True)
                self.states.append(self.expect_flow_mapping_value)
                self.expect_node(mapping=True)

    def expect_flow_mapping_key(self) -> None:
        """
        Handle the next key emission for a flow-style mapping or close the mapping when its end event is encountered.
        
        If the current event ends the mapping, close the flow mapping and restore the previous emitter state. Otherwise, emit the item separator, adjust indentation if required, and emit the next key either as a simple key (scheduling the simple-value handler) when eligible or as an explicit key (writing `?` and scheduling the mapping-value handler).
        """
        if isinstance(self.event, MappingEndEvent):  # noqa: F405
            self.indent = self.indents.pop()
            self.flow_level -= 1
            if self.canonical:
                self.write_indicator(",", False)
                self.write_indent()
            self.write_indicator("}", False)
            self.state = self.states.pop()
        else:
            self.write_indicator(",", False)
            if self.canonical or self.column > self.best_width:
                self.write_indent()
            if not self.canonical and self.check_simple_key():
                self.states.append(self.expect_flow_mapping_simple_value)
                self.expect_node(mapping=True, simple_key=True)
            else:
                self.write_indicator("?", True)
                self.states.append(self.expect_flow_mapping_value)
                self.expect_node(mapping=True)

    def expect_flow_mapping_simple_value(self) -> None:
        """
        Emit the ":" separator for a simple key in a flow mapping, then emit its value and restore mapping-key handling.
        
        Writes the ":" indicator, schedules returning to flow-mapping key processing, and parses the next node as the corresponding mapping value.
        """
        self.write_indicator(":", False)
        self.states.append(self.expect_flow_mapping_key)
        self.expect_node(mapping=True)

    def expect_flow_mapping_value(self) -> None:
        """Emit the value part of a key/value pair inside a flow mapping.

        Writes a separating colon (and an indentation if necessary based on canonical mode or line width), pushes the flow-mapping-key handler onto the state stack, and processes the next event as the mapping value node, causing the value to be emitted to the output stream.
        """
        if self.canonical or self.column > self.best_width:
            self.write_indent()
        self.write_indicator(":", True)
        self.states.append(self.expect_flow_mapping_key)
        self.expect_node(mapping=True)

    # Block sequence handlers.

    def expect_block_sequence(self) -> None:
        """
        Prepare the emitter to write a block sequence by adjusting indentation and switching to the first block-sequence-item state.
        
        If the sequence is nested inside a mapping and the emitter is not currently at an indention point, increase indentation in "indentless" mode so sequence items are emitted without an additional indentation level.
        """
        indentless = self.mapping_context and not self.indention
        self.increase_indent(flow=False, indentless=indentless)
        self.state = self.expect_first_block_sequence_item

    def expect_first_block_sequence_item(self):  # noqa: ANN201
        """
        Handle emission of the first item in a block sequence.
        """
        return self.expect_block_sequence_item(first=True)

    def expect_block_sequence_item(self, first=False) -> None:  # noqa: ANN001
        """Handle emission of a block sequence item within the current mapping/sequence context.

        If called for a terminating `SequenceEndEvent` (and not the first item), restores the previous indentation and returns control to the prior state; otherwise writes the block sequence item marker (`-`) and emits the next sequence element.

        Parameters:
            first (bool): True when emitting the first item of the sequence; affects termination handling.
        """
        if not first and isinstance(self.event, SequenceEndEvent):  # noqa: F405
            self.indent = self.indents.pop()
            self.state = self.states.pop()
        else:
            self.write_indent()
            self.write_indicator("-", True, indention=True)
            self.states.append(self.expect_block_sequence_item)
            self.expect_node(sequence=True)

    # Block mapping handlers.

    def expect_block_mapping(self) -> None:
        """Prepare emitter for a block mapping by increasing block indentation and switching to the first-key handler.

        This configures indentation for a subsequent block-style mapping and sets the emitter state to expect the first mapping key.
        """
        self.increase_indent(flow=False)
        self.state = self.expect_first_block_mapping_key

    def expect_first_block_mapping_key(self):  # noqa: ANN201
        """
        Prepare the emitter to emit the first key of a block-style mapping.
        """
        return self.expect_block_mapping_key(first=True)

    def expect_block_mapping_key(self, first=False) -> None:  # noqa: ANN001
        """Handle the emitter state for processing a block mapping key.

        If the current event is a MappingEndEvent (and this is not the first key), restore the previous indentation and state. Otherwise, write the current indentation and either emit the next node as a simple mapping key (pushing the handler for its simple value) or write the `?` key indicator and prepare to emit a full key/value pair.

        Parameters:
            first (bool): True when handling the first key in the mapping; suppresses handling of an immediate MappingEndEvent.
        """
        if not first and isinstance(self.event, MappingEndEvent):  # noqa: F405
            self.indent = self.indents.pop()
            self.state = self.states.pop()
        else:
            self.write_indent()
            if self.check_simple_key():
                self.states.append(self.expect_block_mapping_simple_value)
                self.expect_node(mapping=True, simple_key=True)
            else:
                self.write_indicator("?", True, indention=True)
                self.states.append(self.expect_block_mapping_value)
                self.expect_node(mapping=True)

    def expect_block_mapping_simple_value(self) -> None:
        """
        Emit the ':' separator for a simple block mapping key and then emit the corresponding value node.
        
        Pushes the block-mapping-key state so emission will continue with the next mapping key after the value is emitted.
        """
        self.write_indicator(":", False)
        self.states.append(self.expect_block_mapping_key)
        self.expect_node(mapping=True)

    def expect_block_mapping_value(self) -> None:
        """
        Emit the value part of a block mapping entry.
        
        Write indentation, emit a ':' indicator for the value (marking indention), push the block-mapping-key handler so the emitter returns to key processing afterwards, and emit the following node as the mapping value.
        """
        self.write_indent()
        self.write_indicator(":", True, indention=True)
        self.states.append(self.expect_block_mapping_key)
        self.expect_node(mapping=True)

    # Checkers.

    def check_empty_sequence(self):  # noqa: ANN201
        """Determine whether the current event represents the start of a sequence whose next queued event is the corresponding end (i.e., an empty sequence).

        Returns:
            bool: `True` if `self.event` is a `SequenceStartEvent` and the next queued event is a `SequenceEndEvent`, `False` otherwise.
        """
        return (
            isinstance(self.event, SequenceStartEvent)  # noqa: F405
            and self.events
            and isinstance(self.events[0], SequenceEndEvent)  # noqa: F405
        )

    def check_empty_mapping(self):  # noqa: ANN201
        """
        Determine whether the current event is a mapping start immediately followed by its end.
        
        Returns:
            `True` if the current event is a `MappingStartEvent` and the next queued event is a `MappingEndEvent`, `False` otherwise.
        """
        return (
            isinstance(self.event, MappingStartEvent)  # noqa: F405
            and self.events
            and isinstance(self.events[0], MappingEndEvent)  # noqa: F405
        )

    def check_empty_document(self):  # noqa: ANN201
        """Determine whether the next queued event represents an empty implicit document.

        Checks if the current event is a DocumentStartEvent and the next queued event is a ScalarEvent that has no anchor, no tag, is implicit, and has an empty value.

        Returns:
            `True` if the next queued event is an implicit empty scalar (an empty document), `False` otherwise.
        """
        if not isinstance(self.event, DocumentStartEvent) or not self.events:  # noqa: F405
            return False
        event = self.events[0]
        return (
            isinstance(event, ScalarEvent)  # noqa: F405
            and event.anchor is None
            and event.tag is None
            and event.implicit
            and event.value == ""  # noqa: PLC1901
        )

    def check_simple_key(self):  # noqa: ANN201
        """
        Determine whether the current event can be emitted as a YAML simple key.
        
        Computes an approximate key length (including any prepared anchor, prepared tag, and scalar text) and checks eligibility: the total length must be less than 128 and the event must be an alias, a non-empty single-line scalar, or an empty sequence/mapping. May populate `self.prepared_anchor`, `self.prepared_tag`, and `self.analysis` as cached side effects.
        
        Returns:
            `true` if the event qualifies as a simple key, `false` otherwise.
        """
        length = 0
        if isinstance(self.event, NodeEvent) and self.event.anchor is not None:  # noqa: F405
            if self.prepared_anchor is None:
                self.prepared_anchor = self.prepare_anchor(self.event.anchor)
            length += len(self.prepared_anchor)
        if (
            isinstance(self.event, (ScalarEvent, CollectionStartEvent))  # noqa: F405
            and self.event.tag is not None
        ):
            if self.prepared_tag is None:
                self.prepared_tag = self.prepare_tag(self.event.tag)
            length += len(self.prepared_tag)
        if isinstance(self.event, ScalarEvent):  # noqa: F405
            if self.analysis is None:
                self.analysis = self.analyze_scalar(self.event.value)
            length += len(self.analysis.scalar)
        return length < 128 and (  # noqa: PLR2004
            isinstance(self.event, AliasEvent)  # noqa: F405
            or (
                isinstance(self.event, ScalarEvent)  # noqa: F405
                and not self.analysis.empty
                and not self.analysis.multiline
            )
            or self.check_empty_sequence()
            or self.check_empty_mapping()
        )

    # Anchor, Tag, and Scalar processors.

    def process_anchor(self, indicator) -> None:  # noqa: ANN001
        """Write the current event's anchor to the output using the provided indicator and then clear the prepared-anchor buffer.

        If the current event has no anchor, the prepared-anchor buffer is cleared and nothing is written.

        Parameters:
            indicator (str): Prefix to write before the anchor (e.g., '&' for node anchors or '*' for aliases).
        """
        if self.event.anchor is None:
            self.prepared_anchor = None
            return
        if self.prepared_anchor is None:
            self.prepared_anchor = self.prepare_anchor(self.event.anchor)
        if self.prepared_anchor:
            self.write_indicator(indicator + self.prepared_anchor, True)
        self.prepared_anchor = None

    def process_tag(self) -> None:
        """Decide whether the current event's tag should be emitted, prepare it if required, and write it to the output.

        For scalar events this honors the chosen scalar style and the event's implicit flags; for non-scalar events it honors the event's implicit flag. If tag emission is suppressed by emitter rules, no tag is written. If a tag must be emitted but is not provided, an exception is raised.

        Raises:
            EmitterError: If a tag is required but not specified.
        """
        tag = self.event.tag
        if isinstance(self.event, ScalarEvent):  # noqa: F405
            if self.style is None:
                self.style = self.choose_scalar_style()
            if (not self.canonical or tag is None) and (  # noqa: PLR0916
                (self.style == "" and self.event.implicit[0])  # noqa: PLC1901
                or (self.style != "" and self.event.implicit[1])  # noqa: PLC1901
            ):
                self.prepared_tag = None
                return
            if self.event.implicit[0] and tag is None:
                tag = "!"
                self.prepared_tag = None
        elif (not self.canonical or tag is None) and self.event.implicit:
            self.prepared_tag = None
            return
        if tag is None:
            raise EmitterError("tag is not specified")  # noqa: TRY003
        if self.prepared_tag is None:
            self.prepared_tag = self.prepare_tag(tag)
        if self.prepared_tag:
            self.write_indicator(self.prepared_tag, True)
        self.prepared_tag = None

    def choose_scalar_style(self):  # noqa: ANN201
        """
        Select the YAML scalar style indicator for the current scalar event.
        
        Considers the event's explicit style, the emitter's canonical mode, implicitness, flow/simple-key context, and the scalar analysis cache when choosing a style.
        
        Returns:
            str: The style indicator: '"' for double-quoted, '' (empty string) for plain, "'" for single-quoted, or '|' / '>' for block literal or folded styles.
        """
        if self.analysis is None:
            self.analysis = self.analyze_scalar(self.event.value)
        if self.event.style == '"' or self.canonical:
            return '"'
        if (
            not self.event.style  # noqa: PLR0916
            and self.event.implicit[0]
            and not (
                self.simple_key_context
                and (self.analysis.empty or self.analysis.multiline)
            )
            and (
                (self.flow_level and self.analysis.allow_flow_plain)
                or (not self.flow_level and self.analysis.allow_block_plain)
            )
        ):
            return ""
        if (
            self.event.style
            and self.event.style in "|>"
            and (
                not self.flow_level
                and not self.simple_key_context
                and self.analysis.allow_block
            )
        ):
            return self.event.style
        if not self.event.style or self.event.style == "'":  # noqa: SIM102
            if self.analysis.allow_single_quoted and not (
                self.simple_key_context and self.analysis.multiline
            ):
                return "'"
        return '"'

    def process_scalar(self) -> None:
        """Emit the current event's scalar using the appropriate YAML scalar style.

        Ensures the scalar has been analyzed and a style has been selected, then writes the scalar to the output using the chosen writer (plain, single-quoted, double-quoted, folded, or literal). Uses line-splitting when not emitting a simple key, and clears the emitter's temporary analysis and style state after emission.
        """
        if self.analysis is None:
            self.analysis = self.analyze_scalar(self.event.value)
        if self.style is None:
            self.style = self.choose_scalar_style()
        split = not self.simple_key_context
        # if self.analysis.multiline and split    \
        #        and (not self.style or self.style in '\'\"'):
        #    self.write_indent()
        if self.style == '"':
            self.write_double_quoted(self.analysis.scalar, split)
        elif self.style == "'":
            self.write_single_quoted(self.analysis.scalar, split)
        elif self.style == ">":
            self.write_folded(self.analysis.scalar)
        elif self.style == "|":
            self.write_literal(self.analysis.scalar)
        else:
            self.write_plain(self.analysis.scalar, split)
        self.analysis = None
        self.style = None

    # Analyzers.

    def prepare_version(self, version) -> str:  # noqa: ANN001, PLR6301
        """
        Validate a YAML version tuple.
        
        Parameters:
            version (tuple[int, int]): A (major, minor) pair specifying the YAML version.
        
        Returns:
            str: The version formatted as "major.minor".
        
        Raises:
            EmitterError: If the major version is not 1.
        """
        major, minor = version
        if major != 1:
            raise EmitterError("unsupported YAML version: %d.%d" % (major, minor))  # noqa: UP031
        return "%d.%d" % (major, minor)  # noqa: UP031

    def prepare_tag_handle(self, handle):  # noqa: ANN001, ANN201, PLR6301
        """
        Validate a YAML tag handle and return it unchanged.
        
        Parameters:
            handle (str): Tag handle that must be non-empty, start and end with "!", and contain only ASCII letters, digits, hyphen, or underscore between the bounding exclamation marks.
        
        Returns:
            str: The validated tag handle.
        
        Raises:
            EmitterError: If `handle` is empty, does not start and end with "!", or contains an invalid character between the bounding "!"s.
        """
        if not handle:
            raise EmitterError("tag handle must not be empty")  # noqa: TRY003
        if handle[0] != "!" or handle[-1] != "!":
            raise EmitterError(f"tag handle must start and end with '!': {handle!r}")  # noqa: TRY003
        for ch in handle[1:-1]:
            if not (
                "0" <= ch <= "9" or "A" <= ch <= "Z" or "a" <= ch <= "z" or ch in "-_"
            ):
                raise EmitterError(  # noqa: TRY003
                    f"invalid character {ch!r} in the tag handle: {handle!r}"
                )
        return handle

    def prepare_tag_prefix(self, prefix):  # noqa: ANN001, ANN201, PLR6301
        """
        Prepare a YAML tag prefix by validating characters and percent-encoding any disallowed bytes.
        
        Parameters:
            prefix (str): Tag prefix to prepare. A leading '!' is preserved; all other characters outside the allowed set are percent-encoded using UTF-8 octets.
        
        Returns:
            str: The prepared prefix with disallowed characters percent-encoded (e.g. '%XX' sequences).
        
        Raises:
            EmitterError: If `prefix` is empty.
        """
        if not prefix:
            raise EmitterError("tag prefix must not be empty")  # noqa: TRY003
        chunks = []
        start = end = 0
        if prefix[0] == "!":
            end = 1
        while end < len(prefix):
            ch = prefix[end]
            if (
                "0" <= ch <= "9"
                or "A" <= ch <= "Z"
                or "a" <= ch <= "z"
                or ch in "-;/?!:@&=+$,_.~*'()[]"
            ):
                end += 1
            else:
                if start < end:
                    chunks.append(prefix[start:end])
                start = end = end + 1
                data = ch.encode("utf-8")
                chunks.extend(f"%{byte:02X}" for byte in data)
        if start < end:
            chunks.append(prefix[start:end])
        return "".join(chunks)

    def prepare_tag(self, tag):  # noqa: ANN001, ANN201
        """Prepare a YAML tag for emission by resolving registered tag handles and percent-encoding disallowed characters.

        If the tag is the single-character local tag `"!"`, it is returned unchanged. If the tag starts with a registered prefix, the matching handle is prepended to the percent-encoded suffix; otherwise the suffix is percent-encoded and returned in the form `!<suffix>`.

        Parameters:
            tag (str): The input tag to prepare; must be non-empty.

        Returns:
            str: The prepared tag suitable for emission (either the unchanged `"!"`, `handle + encoded_suffix`, or `!<encoded_suffix>`).

        Raises:
            EmitterError: If `tag` is an empty string.
        """
        if not tag:
            raise EmitterError("tag must not be empty")  # noqa: TRY003
        if tag == "!":
            return tag
        handle = None
        suffix = tag
        prefixes = sorted(self.tag_prefixes.keys())
        for prefix in prefixes:
            if tag.startswith(prefix) and (prefix == "!" or len(prefix) < len(tag)):
                handle = self.tag_prefixes[prefix]
                suffix = tag[len(prefix) :]
        chunks = []
        start = end = 0
        while end < len(suffix):
            ch = suffix[end]
            if (
                "0" <= ch <= "9"  # noqa: PLR0916
                or "A" <= ch <= "Z"
                or "a" <= ch <= "z"
                or ch in "-;/?:@&=+$,_.~*'()[]"
                or (ch == "!" and handle != "!")
            ):
                end += 1
            else:
                if start < end:
                    chunks.append(suffix[start:end])
                start = end = end + 1
                data = ch.encode("utf-8")
                chunks.extend(f"%{byte:02X}" for byte in data)
        if start < end:
            chunks.append(suffix[start:end])
        suffix_text = "".join(chunks)
        if handle:
            return f"{handle}{suffix_text}"
        return f"!<{suffix_text}>"

    def prepare_anchor(self, anchor):  # noqa: ANN001, ANN201, PLR6301
        """
        Validate a YAML anchor name and return it unchanged.
        
        Parameters:
            anchor (str): Candidate anchor name; must be non-empty and contain only ASCII letters, digits, hyphen (`-`), or underscore (`_`).
        
        Returns:
            str: The validated anchor string.
        
        Raises:
            EmitterError: If `anchor` is empty or contains any character other than `0-9`, `A-Z`, `a-z`, `-`, or `_`.
        """
        if not anchor:
            raise EmitterError("anchor must not be empty")  # noqa: TRY003
        for ch in anchor:
            if not (
                "0" <= ch <= "9" or "A" <= ch <= "Z" or "a" <= ch <= "z" or ch in "-_"
            ):
                raise EmitterError(  # noqa: TRY003
                    f"invalid character {ch!r} in the anchor: {anchor!r}"
                )
        return anchor

    def analyze_scalar(self, scalar):  # noqa: ANN001, ANN201, C901, PLR0912, PLR0914, PLR0915
        # Empty scalar is a special case.
        """Analyze a scalar string and determine which YAML scalar styles and features are allowed.

        Parameters:
            scalar (str): The scalar text to analyze.

        Returns:
            ScalarAnalysis: Analysis of `scalar` including:
                - `empty`: True if the scalar is empty.
                - `multiline`: True if the scalar contains line breaks.
                - `allow_flow_plain`: True if plain style is allowed in flow context.
                - `allow_block_plain`: True if plain style is allowed in block context.
                - `allow_single_quoted`: True if single-quoted style is allowed.
                - `allow_double_quoted`: True if double-quoted style is allowed.
                - `allow_block`: True if block styles (`|` or `>`) are allowed.
        """
        if not scalar:
            return ScalarAnalysis(
                scalar=scalar,
                empty=True,
                multiline=False,
                allow_flow_plain=False,
                allow_block_plain=True,
                allow_single_quoted=True,
                allow_double_quoted=True,
                allow_block=False,
            )

        # Indicators and special characters.
        block_indicators = False
        flow_indicators = False
        line_breaks = False
        special_characters = False

        # Important whitespace combinations.
        leading_space = False
        leading_break = False
        trailing_space = False
        trailing_break = False
        break_space = False
        space_break = False

        # Check document indicators.
        if scalar.startswith(("---", "...")):
            block_indicators = True
            flow_indicators = True

        # First character or preceded by a whitespace.
        preceded_by_whitespace = True

        # Last character or followed by a whitespace.
        followed_by_whitespace = (
            len(scalar) == 1 or scalar[1] in "\0 \t\r\n\x85\u2028\u2029"
        )

        # The previous character is a space.
        previous_space = False

        # The previous character is a break.
        previous_break = False

        index = 0
        while index < len(scalar):
            ch = scalar[index]

            # Check for indicators.
            if index == 0:
                # Leading indicators are special characters.
                if ch in "#,[]{}&*!|>'\"%@`":
                    flow_indicators = True
                    block_indicators = True
                if ch in "?:":
                    flow_indicators = True
                    if followed_by_whitespace:
                        block_indicators = True
                if ch == "-" and followed_by_whitespace:
                    flow_indicators = True
                    block_indicators = True
            else:
                # Some indicators cannot appear within a scalar as well.
                if ch in ",?[]{}":
                    flow_indicators = True
                if ch == ":":
                    flow_indicators = True
                    if followed_by_whitespace:
                        block_indicators = True
                if ch == "#" and preceded_by_whitespace:
                    flow_indicators = True
                    block_indicators = True

            # Check for line breaks, special, and unicode characters.
            if ch in "\n\x85\u2028\u2029":
                line_breaks = True
            if not (ch == "\n" or "\x20" <= ch <= "\x7e"):
                if (
                    ch == "\x85"
                    or "\xa0" <= ch <= "\ud7ff"
                    or "\ue000" <= ch <= "\ufffd"
                    or "\U00010000" <= ch < "\U0010ffff"
                ) and ch != "\ufeff":
                    if not self.allow_unicode:
                        special_characters = True
                else:
                    special_characters = True

            # Detect important whitespace combinations.
            if ch == " ":
                if index == 0:
                    leading_space = True
                if index == len(scalar) - 1:
                    trailing_space = True
                if previous_break:
                    break_space = True
                previous_space = True
                previous_break = False
            elif ch in "\n\x85\u2028\u2029":
                if index == 0:
                    leading_break = True
                if index == len(scalar) - 1:
                    trailing_break = True
                if previous_space:
                    space_break = True
                previous_space = False
                previous_break = True
            else:
                previous_space = False
                previous_break = False

            # Prepare for the next character.
            index += 1
            preceded_by_whitespace = ch in "\0 \t\r\n\x85\u2028\u2029"
            followed_by_whitespace = (
                index + 1 >= len(scalar)
                or scalar[index + 1] in "\0 \t\r\n\x85\u2028\u2029"
            )

        # Let's decide what styles are allowed.
        allow_flow_plain = True
        allow_block_plain = True
        allow_single_quoted = True
        allow_double_quoted = True
        allow_block = True

        # Leading and trailing whitespaces are bad for plain scalars.
        if leading_space or leading_break or trailing_space or trailing_break:
            allow_flow_plain = allow_block_plain = False

        # We do not permit trailing spaces for block scalars.
        if trailing_space:
            allow_block = False

        # Spaces at the beginning of a new line are only acceptable for block
        # scalars.
        if break_space:
            allow_flow_plain = allow_block_plain = allow_single_quoted = False

        # Spaces followed by breaks, as well as special character are only
        # allowed for double quoted scalars.
        if space_break or special_characters:
            allow_flow_plain = allow_block_plain = allow_single_quoted = allow_block = (
                False
            )

        # Although the plain scalar writer supports breaks, we never emit
        # multiline plain scalars.
        if line_breaks:
            allow_flow_plain = allow_block_plain = False

        # Flow indicators are forbidden for flow plain scalars.
        if flow_indicators:
            allow_flow_plain = False

        # Block indicators are forbidden for block plain scalars.
        if block_indicators:
            allow_block_plain = False

        return ScalarAnalysis(
            scalar=scalar,
            empty=False,
            multiline=line_breaks,
            allow_flow_plain=allow_flow_plain,
            allow_block_plain=allow_block_plain,
            allow_single_quoted=allow_single_quoted,
            allow_double_quoted=allow_double_quoted,
            allow_block=allow_block,
        )

    # Writers.

    def flush_stream(self) -> None:
        """Flushes the underlying output stream if it provides a flush() method."""
        if hasattr(self.stream, "flush"):
            self.stream.flush()

    def write_stream_start(self) -> None:
        # Write BOM if needed.
        """Write a UTF-16 byte order mark to the output stream when the emitter encoding is a UTF-16 variant.

        If `self.encoding` starts with `"utf-16"`, writes the Unicode BOM (`"\ufeff"`) encoded with `self.encoding` to `self.stream`. No action is performed for other encodings.
        """
        if self.encoding and self.encoding.startswith("utf-16"):
            self.stream.write("\ufeff".encode(self.encoding))

    def write_stream_end(self) -> None:
        """Finalize the output stream for the end of YAML emission by ensuring any buffered data is written."""
        self.flush_stream()

    def write_indicator(
        self,
        indicator,  # noqa: ANN001
        need_whitespace,  # noqa: ANN001
        whitespace=False,  # noqa: ANN001
        indention=False,  # noqa: ANN001
    ) -> None:
        """Write a YAML indicator to the output stream and update the emitter's position and state.

        Ensures a separating space is inserted before the indicator when required, updates `whitespace`, `indention`, `column`, and clears `open_ended`, and encodes the output using the emitter's `encoding` when set.

        Parameters:
            indicator (str): The indicator text to write (e.g. '-', '?', ':', ',', '[', ']', '{', '}', '&', '*').
            need_whitespace (bool): If True, prepend a space before `indicator` unless the emitter is already at whitespace.
            whitespace (bool): Value to assign to the emitter's `whitespace` flag after writing the indicator.
            indention (bool): If True, preserve the emitter's current `indention` (logical AND); if False, clear it.
        """
        data = indicator if self.whitespace or not need_whitespace else " " + indicator
        self.whitespace = whitespace
        self.indention = self.indention and indention
        self.column += len(data)
        self.open_ended = False
        if self.encoding:
            data = data.encode(self.encoding)
        self.stream.write(data)

    def write_indent(self) -> None:
        """
        Position the emitter at the configured indentation level.
        
        If the current output position is not at the target indentation, write a line break and then write spaces until the column equals the configured indent. Updates `self.whitespace` and `self.column`; when `self.encoding` is set the written spaces are encoded before being written to `self.stream`.
        """
        indent = self.indent or 0
        if (
            not self.indention
            or self.column > indent
            or (self.column == indent and not self.whitespace)
        ):
            self.write_line_break()
        if self.column < indent:
            self.whitespace = True
            data = " " * (indent - self.column)
            self.column = indent
            if self.encoding:
                data = data.encode(self.encoding)
            self.stream.write(data)

    def write_line_break(self, data=None) -> None:  # noqa: ANN001
        """
        Write a line break to the output stream and update the emitter's position and spacing state.
        
        If `data` is None, the emitter's `best_line_break` is used. If `data` is a `str` and an encoding is configured, the string is encoded with `self.encoding` before writing; `bytes` are written unchanged. This method sets `whitespace` and `indention` to True, increments `line` by one, and resets `column` to 0.
        """
        if data is None:
            data = self.best_line_break
        self.whitespace = True
        self.indention = True
        self.line += 1
        self.column = 0
        if self.encoding:
            data = data.encode(self.encoding)
        self.stream.write(data)

    def write_version_directive(self, version_text) -> None:  # noqa: ANN001
        """
        Write the YAML version directive for the given version to the emitter's output.
        
        Writes the directive "%YAML <version_text>" followed by a line break. If the emitter has an encoding configured, the directive is encoded with that encoding before being written.
        
        Parameters:
            version_text (str): Version string in "major.minor" form (for example, "1.2").
        """
        data = f"%YAML {version_text}"
        if self.encoding:
            data = data.encode(self.encoding)
        self.stream.write(data)
        self.write_line_break()

    def write_tag_directive(self, handle_text, prefix_text) -> None:  # noqa: ANN001
        """Write a %TAG directive with the given handle and prefix, then write a line break.

        Parameters:
            handle_text (str): Tag handle to write after `%TAG` (e.g., '!yaml!').
            prefix_text (str): Tag prefix or URI to write after the handle.
        """
        data = f"%TAG {handle_text} {prefix_text}"
        if self.encoding:
            data = data.encode(self.encoding)
        self.stream.write(data)
        self.write_line_break()

    # Scalar streams.

    def write_single_quoted(self, text, split=True) -> None:  # noqa: ANN001, PLR0912
        """Emit a single-quoted YAML scalar to the emitter's output stream.

        The scalar is written surrounded by single quotes; internal single quotes are represented by doubling them, and existing line breaks in the text are preserved and emitted as YAML line breaks. When `split` is True, the emitter may insert line breaks and indentation to respect the configured line width; when False, the scalar is emitted without automatic splitting.

        Parameters:
            text (str): The scalar content to emit.
            split (bool): Allow the emitter to insert line breaks/indentation to respect configured line width when True.
        """
        self.write_indicator("'", True)
        spaces = False
        breaks = False
        start = end = 0
        while end <= len(text):
            ch = None
            if end < len(text):
                ch = text[end]
            if spaces:
                if ch is None or ch != " ":
                    if (
                        start + 1 == end
                        and self.column > self.best_width
                        and split
                        and start != 0
                        and end != len(text)
                    ):
                        self.write_indent()
                    else:
                        data = text[start:end]
                        self.column += len(data)
                        if self.encoding:
                            data = data.encode(self.encoding)
                        self.stream.write(data)
                    start = end
            elif breaks:
                if ch is None or ch not in "\n\x85\u2028\u2029":
                    if text[start] == "\n":
                        self.write_line_break()
                    for br in text[start:end]:
                        if br == "\n":
                            self.write_line_break()
                        else:
                            self.write_line_break(br)
                    self.write_indent()
                    start = end
            elif ch is None or ch in " \n\x85\u2028\u2029" or ch == "'":  # noqa: SIM102
                if start < end:
                    data = text[start:end]
                    self.column += len(data)
                    if self.encoding:
                        data = data.encode(self.encoding)
                    self.stream.write(data)
                    start = end
            if ch == "'":
                data = "''"
                self.column += 2
                if self.encoding:
                    data = data.encode(self.encoding)
                self.stream.write(data)
                start = end + 1
            if ch is not None:
                spaces = ch == " "
                breaks = ch in "\n\x85\u2028\u2029"
            end += 1
        self.write_indicator("'", False)

    ESCAPE_REPLACEMENTS = {  # noqa: RUF012
        "\0": "0",
        "\x07": "a",
        "\x08": "b",
        "\x09": "t",
        "\x0a": "n",
        "\x0b": "v",
        "\x0c": "f",
        "\x0d": "r",
        "\x1b": "e",
        '"': '"',
        "\\": "\\",
        "\x85": "N",
        "\xa0": "_",
        "\u2028": "L",
        "\u2029": "P",
    }

    def write_double_quoted(self, text, split=True) -> None:  # noqa: ANN001, PLR0912
        """
        Write the given text as a YAML double-quoted scalar (including surrounding double quotes) to the emitter's output stream.
        
        The scalar is emitted using YAML double-quoted escaping: known control characters are replaced with their backslash escapes from ESCAPE_REPLACEMENTS, other non-printable or disallowed code points are escaped as `\xNN`, `\uNNNN`, or `\UNNNNNNNN`. Characters above ASCII are emitted directly only if `self.allow_unicode` permits; otherwise they are escaped. If the emitter has an `encoding` set, emitted bytes (including escape sequences) are encoded with that encoding before being written.
        
        Parameters:
            text (str): Scalar content to emit.
            split (bool): If True, allow inserting YAML soft line continuations (`\` + line break + indentation) when needed to respect the emitter's `best_width`; if False, do not insert soft line breaks.
        
        Side effects:
            Writes bytes/strings to `self.stream` and updates emitter state used for column/whitespace/indention tracking.
        """
        self.write_indicator('"', True)
        start = end = 0
        while end <= len(text):
            ch = None
            if end < len(text):
                ch = text[end]
            if (
                ch is None
                or ch in '"\\\x85\u2028\u2029\ufeff'
                or not (
                    "\x20" <= ch <= "\x7e"
                    or (
                        self.allow_unicode
                        and ("\xa0" <= ch <= "\ud7ff" or "\ue000" <= ch <= "\ufffd")
                    )
                )
            ):
                if start < end:
                    data = text[start:end]
                    self.column += len(data)
                    if self.encoding:
                        data = data.encode(self.encoding)
                    self.stream.write(data)
                    start = end
                if ch is not None:
                    if ch in self.ESCAPE_REPLACEMENTS:
                        data = "\\" + self.ESCAPE_REPLACEMENTS[ch]
                    elif ch <= "\xff":
                        data = f"\\x{ord(ch):02X}"
                    elif ch <= "\uffff":
                        data = f"\\u{ord(ch):04X}"
                    else:
                        data = f"\\U{ord(ch):08X}"
                    self.column += len(data)
                    if self.encoding:
                        data = data.encode(self.encoding)
                    self.stream.write(data)
                    start = end + 1
            if (
                0 < end < len(text) - 1
                and (ch == " " or start >= end)
                and self.column + (end - start) > self.best_width
                and split
            ):
                data = text[start:end] + "\\"
                start = max(start, end)
                self.column += len(data)
                if self.encoding:
                    data = data.encode(self.encoding)
                self.stream.write(data)
                self.write_indent()
                self.whitespace = False
                self.indention = False
                if text[start] == " ":
                    data = "\\"
                    self.column += len(data)
                    if self.encoding:
                        data = data.encode(self.encoding)
                    self.stream.write(data)
            end += 1
        self.write_indicator('"', False)

    def determine_block_hints(self, text):  # noqa: ANN001, ANN201
        """
        Compute block scalar header hints for indentation and chomping.
        
        Determines whether to include an indentation indicator (the emitter's best indent)
        when the text begins with whitespace or a line break, and a chomping indicator:
        '-' to strip the final line break, '+' to keep trailing line breaks, or '' when
        the default chomping applies.
        
        Parameters:
            text (str): The scalar content to inspect.
        
        Returns:
            str: The hint string to append to a block scalar indicator (e.g. '2-', '+', or '').
        """
        hints = ""
        if text:
            if text[0] in " \n\x85\u2028\u2029":
                hints += str(self.best_indent)
            if text[-1] not in "\n\x85\u2028\u2029":
                hints += "-"
            elif len(text) == 1 or text[-2] in "\n\x85\u2028\u2029":
                hints += "+"
        return hints

    def write_folded(self, text) -> None:  # noqa: ANN001, PLR0912
        """Emit a folded block scalar using the `>` indicator.

        Writes the block header with computed indentation and chomping hints, then serializes `text` according to YAML folded-scalar rules (preserving and folding line breaks and spaces and emitting required indentation/line breaks). If the computed chomping indicator is `+`, sets `self.open_ended = True`.

        Parameters:
            text (str): Scalar content to emit as a folded block scalar.
        """
        hints = self.determine_block_hints(text)
        self.write_indicator(">" + hints, True)
        if hints[-1:] == "+":
            self.open_ended = True
        self.write_line_break()
        leading_space = True
        spaces = False
        breaks = True
        start = end = 0
        while end <= len(text):
            ch = None
            if end < len(text):
                ch = text[end]
            if breaks:
                if ch is None or ch not in "\n\x85\u2028\u2029":
                    if (
                        not leading_space
                        and ch is not None
                        and ch != " "
                        and text[start] == "\n"
                    ):
                        self.write_line_break()
                    leading_space = ch == " "
                    for br in text[start:end]:
                        if br == "\n":
                            self.write_line_break()
                        else:
                            self.write_line_break(br)
                    if ch is not None:
                        self.write_indent()
                    start = end
            elif spaces:
                if ch != " ":
                    if start + 1 == end and self.column > self.best_width:
                        self.write_indent()
                    else:
                        data = text[start:end]
                        self.column += len(data)
                        if self.encoding:
                            data = data.encode(self.encoding)
                        self.stream.write(data)
                    start = end
            elif ch is None or ch in " \n\x85\u2028\u2029":
                data = text[start:end]
                self.column += len(data)
                if self.encoding:
                    data = data.encode(self.encoding)
                self.stream.write(data)
                if ch is None:
                    self.write_line_break()
                start = end
            if ch is not None:
                breaks = ch in "\n\x85\u2028\u2029"
                spaces = ch == " "
            end += 1

    def write_literal(self, text) -> None:  # noqa: ANN001, PLR0912
        """
        Emit a YAML literal block scalar for the given text to the output stream.
        
        Writes the block header with indentation and chomping hints and then emits the text exactly as provided, preserving all line breaks and whitespace. If the chomping hint is '+', sets self.open_ended to True to indicate the document remains open.
        
        Parameters:
            text (str): Scalar content to emit as a literal block; may contain arbitrary Unicode and line break characters.
        """
        hints = self.determine_block_hints(text)
        self.write_indicator("|" + hints, True)
        if hints[-1:] == "+":
            self.open_ended = True
        self.write_line_break()
        breaks = True
        start = end = 0
        while end <= len(text):
            ch = None
            if end < len(text):
                ch = text[end]
            if breaks:
                if ch is None or ch not in "\n\x85\u2028\u2029":
                    for br in text[start:end]:
                        if br == "\n":
                            self.write_line_break()
                        else:
                            self.write_line_break(br)
                    if ch is not None:
                        self.write_indent()
                    start = end
            elif ch is None or ch in "\n\x85\u2028\u2029":
                data = text[start:end]
                if self.encoding:
                    data = data.encode(self.encoding)
                self.stream.write(data)
                if ch is None:
                    self.write_line_break()
                start = end
            if ch is not None:
                breaks = ch in "\n\x85\u2028\u2029"
            end += 1

    def write_plain(self, text, split=True) -> None:  # noqa: ANN001, PLR0912, PLR0915
        """
        Emit a plain (unquoted) scalar value to the emitter's output stream.
        
        If the emitter is in root context, marks the emitter open-ended before writing. Writes nothing for an empty `text`. If `split` is True, the emitter may insert indentation and line breaks to keep lines within the configured `best_width`.
        
        Parameters:
            text (str): The scalar content to emit.
            split (bool): Allow automatic insertion of indentation/line breaks when True.
        """
        if self.root_context:
            self.open_ended = True
        if not text:
            return
        if not self.whitespace:
            data = " "
            self.column += len(data)
            if self.encoding:
                data = data.encode(self.encoding)
            self.stream.write(data)
        self.whitespace = False
        self.indention = False
        spaces = False
        breaks = False
        start = end = 0
        while end <= len(text):
            ch = None
            if end < len(text):
                ch = text[end]
            if spaces:
                if ch != " ":
                    if start + 1 == end and self.column > self.best_width and split:
                        self.write_indent()
                        self.whitespace = False
                        self.indention = False
                    else:
                        data = text[start:end]
                        self.column += len(data)
                        if self.encoding:
                            data = data.encode(self.encoding)
                        self.stream.write(data)
                    start = end
            elif breaks:
                if ch not in "\n\x85\u2028\u2029":
                    if text[start] == "\n":
                        self.write_line_break()
                    for br in text[start:end]:
                        if br == "\n":
                            self.write_line_break()
                        else:
                            self.write_line_break(br)
                    self.write_indent()
                    self.whitespace = False
                    self.indention = False
                    start = end
            elif ch is None or ch in " \n\x85\u2028\u2029":
                data = text[start:end]
                self.column += len(data)
                if self.encoding:
                    data = data.encode(self.encoding)
                self.stream.write(data)
                start = end
            if ch is not None:
                spaces = ch == " "
                breaks = ch in "\n\x85\u2028\u2029"
            end += 1
