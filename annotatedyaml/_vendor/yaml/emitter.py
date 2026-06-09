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
        """Initialize a ScalarAnalysis that records which scalar styles and formatting are allowed for a given scalar value.

        Parameters:
            scalar (str): The original scalar string.
            empty (bool): True if the scalar is empty.
            multiline (bool): True if the scalar contains line breaks.
            allow_flow_plain (bool): True if the scalar may be emitted as a plain scalar in flow context.
            allow_block_plain (bool): True if the scalar may be emitted as a plain scalar in block context.
            allow_single_quoted (bool): True if the scalar may be emitted using single quotes.
            allow_double_quoted (bool): True if the scalar may be emitted using double quotes.
            allow_block (bool): True if the scalar may be emitted as a block scalar (literal or folded).
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
        """Enqueue a YAML emission event and drive the emitter state machine to process available events.

        This method appends `event` to the internal event queue and repeatedly processes queued events (advancing the emitter's state machine) until additional lookahead is required. Processing events causes the emitter to produce output to its configured stream and to update its internal emission state.

        Parameters:
            event: A YAML event object to be emitted (e.g., StreamStartEvent, DocumentStartEvent, ScalarEvent, SequenceStartEvent, MappingStartEvent, AliasEvent, DocumentEndEvent, StreamEndEvent).
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
        """Determine whether the queued events are insufficient to look ahead by `count` events.

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
        """Adjust the emitter's indentation for a new nested context, saving the previous indent.

        Parameters:
            flow (bool): If True and there is no current indent, initialize indent to the emitter's configured best indent;
                otherwise initialize to 0 when there is no current indent.
            indentless (bool): If True, do not increase the current indent (only push the previous value onto the indent stack).
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
        """Raise an EmitterError when an event is encountered while no events are expected.

        Raises:
            EmitterError: always raised with a message describing the unexpected `self.event`.
        """
        raise EmitterError(f"expected nothing, but got {self.event}")  # noqa: TRY003

    # Document handlers.

    def expect_first_document_start(self):  # noqa: ANN201
        """Handle the first DOCUMENT-START event in the stream and transition the emitter to process the document root.

        Returns:
            None
        """
        return self.expect_document_start(first=True)

    def expect_document_start(self, first=False) -> None:  # noqa: ANN001
        """Handle the start of a document or the end of the stream and transition the emitter state accordingly.

        When the current event is a DocumentStartEvent, prepare and emit any YAML version and %TAG directives, update the emitter's tag prefixes from the event, and emit an explicit document start marker (`---`) unless the document may be emitted implicitly (typically for the first document without directives and not canonical). After handling the start, set the next state to process the document root.

        When the current event is a StreamEndEvent, emit any pending end marker (`...`) if open-ended, finalize the stream, and set the next state to expect no further events.

        Parameters:
            first (bool): True if this document is the first in the stream; affects whether an explicit document start marker is required and whether the document may be emitted implicitly.

        Raises:
            EmitterError: if the current event is neither a DocumentStartEvent nor a StreamEndEvent.
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
        """Handle a DocumentEndEvent by emitting document termination markers and returning the emitter to document-start state.

        If the current event is a DocumentEndEvent, write indentation, emit the explicit document end indicator (`...`) when the event is explicit, write a following indentation, flush the underlying stream, and set the next state to expect_document_start. If the current event is not a DocumentEndEvent, raise an EmitterError describing the unexpected event.
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
        """Begin emission of a document's root node and schedule the document end handler.

        This pushes the document-end state onto the state stack and processes the document root node.
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
        """Handle the current node event: set node context flags, emit any anchor/tag, and dispatch to the appropriate node emitter.

        This method configures the emitter's context according to the boolean flags:
        - root: treat the node as a document root.
        - sequence: treat the node as a sequence item.
        - mapping: treat the node as a mapping key or value.
        - simple_key: allow simple-key formatting rules for the node.

        Behavior:
        - If the event is an alias, emit it as an alias.
        - If the event is a scalar or a collection start, emit any anchor and tag present and then:
          - Emit a scalar for scalar events.
          - Emit a flow or block sequence for sequence events depending on flow level, canonical mode, the event's flow style, or whether the sequence is empty.
          - Emit a flow or block mapping for mapping events using analogous criteria.
        - If the event is none of the above node types, raise an EmitterError indicating an unexpected event type.
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
        """Emit an alias node using the current event's anchor and resume the previous emitter state.

        Raises:
            EmitterError: If the current event has no anchor.
        """
        if self.event.anchor is None:
            raise EmitterError("anchor is not specified for alias")  # noqa: TRY003
        self.process_anchor("*")
        self.state = self.states.pop()

    def expect_scalar(self) -> None:
        """Handle a scalar node during emission by emitting the current scalar event and restoring indentation and state.

        The method temporarily increases indentation for flow context, emits the scalar value for the current event, then restores the previous indentation level and returns the emitter to its prior state.
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
        """Handle the first item of a flow sequence during emission.

        If the current event is a SequenceEndEvent, close the flow sequence by restoring the previous indent, decreasing the flow level, writing the closing `]`, and returning to the previous state. Otherwise, write an indent when in canonical mode or column overflow, push the handler for subsequent flow sequence items onto the state stack, and process the first sequence item node.
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
        """Handle the emission of an item or the end marker for a flow-style sequence.

        If the current event ends the sequence, close the flow sequence, restore indentation and flow level, emit any canonical separators/terminators, and return to the previous emitter state. Otherwise, emit the item separator (and indentation when required), schedule this handler for subsequent items, and proceed to emit the next sequence element.
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

        Writes the opening `{`, increments the flow nesting level, increases indentation for flow context, and sets the emitter state to handle the first mapping key.
        """
        self.write_indicator("{", True, whitespace=True)
        self.flow_level += 1
        self.increase_indent(flow=True)
        self.state = self.expect_first_flow_mapping_key

    def expect_first_flow_mapping_key(self) -> None:
        """Handle emission of the first key in a flow-style mapping as part of the emitter state machine.

        If the current event ends the mapping, close the flow mapping (write '}', restore indentation and flow level) and return to the previous state. Otherwise, ensure proper indentation for canonical mode or line-width overflow, then either emit the key as a simple key when allowed (pushing the corresponding simple-value handler) or emit an explicit mapping key indicator ('?'), push the mapping-value handler, and process the key node.
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
        """Handle the next key in a flow-style mapping or close the mapping when its end event is received.

        If the current event is a mapping end, closes the flow mapping by restoring indentation, decrementing the flow level, writing the closing indicator, and returning to the previous emitter state. Otherwise, writes the item separator, decides whether to emit the next key as a simple key or as an explicit key (writing `?`), pushes the appropriate follow-up state for handling the corresponding value, and begins emitting the key node.
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
        """Emit the colon separator for a flow-mapping simple key, then parse the following node as the corresponding mapping value and schedule handling of subsequent mapping keys."""
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
        """Prepare the emitter to emit a block sequence by adjusting indentation and switching the state machine to the first-block-sequence-item handler.

        Calculates whether the sequence should be indentless when inside a mapping and not currently at indention, increases the indentation for block mode accordingly, and sets the emitter state to expect the first block sequence item.
        """
        indentless = self.mapping_context and not self.indention
        self.increase_indent(flow=False, indentless=indentless)
        self.state = self.expect_first_block_sequence_item

    def expect_first_block_sequence_item(self):  # noqa: ANN201
        """Delegate to expect_block_sequence_item with `first=True` to handle the first item of a block sequence."""
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
        """Handle emission of the first key in a block-style mapping.

        Sets the emitter state to process a mapping key in block context (the initial key of the mapping).
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
        """Emit a block mapping simple-value indicator and process the following node as the mapping value.

        Writes the ':' indicator for a simple key/value form, pushes the block-mapping-key handler onto the state stack so emission returns to the next key afterwards, and dispatches the next event as a mapping value node.
        """
        self.write_indicator(":", False)
        self.states.append(self.expect_block_mapping_key)
        self.expect_node(mapping=True)

    def expect_block_mapping_value(self) -> None:
        """Emit the value part of a block mapping entry.

        Writes the correct indentation and a ':' indicator for the mapping value, pushes the block-mapping-key handler onto the state stack so the emitter returns to key processing afterwards, and processes the following node as the mapping value.
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
        """Determine whether the current event is a mapping start immediately followed by its end.

        Returns:
            bool: `True` if `self.event` is a `MappingStartEvent` and the next queued event is a `MappingEndEvent`, `False` otherwise.
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
        """Determine whether the current event can be emitted as a simple key.

        Computes the approximate length of the event's key representation (including prepared anchor, prepared tag, and scalar text) and returns whether that length is less than 128 and the event is eligible to be a simple key (an alias, a non-empty single-line scalar, or an empty sequence/mapping). May populate `self.prepared_anchor`, `self.prepared_tag`, and `self.analysis` as cached side effects.

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
        """Determine whether the current event's tag should be emitted, prepare it if needed, and write it to the output.

        For scalar events this respects scalar style and the event's implicit flags; for non-scalar events it respects the event's implicit flag. If tag emission is suppressed by the emitter's canonical/implicit rules, no tag is written. Otherwise the tag is prepared via prepare_tag() and emitted with write_indicator().

        Raises:
            EmitterError: If a tag is required but not provided.
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
        """Selects the YAML scalar style to emit for the current scalar event.

        Chooses a style based on the event's explicit style, the emitter's canonical mode,
        the event's implicit flags, the current context (flow level and simple-key),
        and the results of scalar analysis.

        Returns:
            style (str): One of:
              - `"` : double-quoted style
              - `''` : plain style (empty string)
              - `'` : single-quoted style
              - `|` or `>` : block literal or folded style (when allowed by context)
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
        """Selects an appropriate YAML scalar style for the current event and writes the scalar to the output stream.

        Ensures the scalar has been analyzed and a style has been chosen, then emits the scalar using the selected writer (plain, single-quoted, double-quoted, folded, or literal). After emission, clears the temporary analysis and style state. The function writes directly to the emitter's output stream and affects emitter formatting state.
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
        """Validate a YAML version tuple and return it formatted as "major.minor".

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
        """Validate and return a YAML tag handle that is bounded by '!' characters and contains only allowed characters.

        Parameters:
            handle (str): The raw tag handle to validate; must be non-empty, start and end with '!', and contain only ASCII letters, digits, hyphen, or underscore between the bounding '!'.

        Returns:
            str: The validated tag handle (unchanged).

        Raises:
            EmitterError: If `handle` is empty, does not start and end with '!', or contains an invalid character between the bounding '!'s.
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
        """Prepare a tag prefix by validating allowed characters and percent-encoding any disallowed characters.

        Parameters:
            prefix (str): The tag prefix to prepare; may start with '!' which is preserved.

        Returns:
            str: The prepared prefix where characters not in the allowed set are percent-encoded using UTF-8 bytes.

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
        """Prepare a YAML tag for emission by resolving any registered handle and percent-encoding disallowed characters.

        Parameters:
            tag (str): The input tag to prepare; must be non-empty.

        Returns:
            str: A tag string suitable for emission. If the tag is the single-character local tag "!", it is returned unchanged. If the tag matches a registered prefix, the corresponding handle is prepended to the encoded suffix; otherwise the suffix is percent-encoded and returned in the form `!<suffix>`.

        Raises:
            EmitterError: If `tag` is empty.
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
        """Validate a YAML anchor name and return it unchanged.

        Parameters:
        	anchor (str): Candidate anchor name. Must be non-empty and contain only ASCII letters, digits, hyphen (`-`), or underscore (`_`).

        Returns:
        	anchor (str): The validated anchor string.

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
        """Write an indicator string to the output stream, prefixing a space when required, and update the emitter's position and state flags.

        Parameters:
            indicator (str): The indicator text to write (eg. '-', '?', ':', ',', '[', ']', '{', '}', '&', '*').
            need_whitespace (bool): If True, ensure a separating space is present before the indicator unless the emitter is already at whitespace.
            whitespace (bool): Value to assign to the emitter's `whitespace` flag after writing the indicator.
            indention (bool): If False, clear the emitter's `indention` flag; if True, preserve its current value (i.e., `indention` becomes `self.indention and True`).
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
        """Ensure the output position is at the current indentation level by inserting a line break if needed and writing spaces up to `self.indent`.

        If the current column or whitespace state requires a new line, a line break is written. Then, if the column is less than the target indent, spaces are written to the stream to advance the column to `self.indent`; `self.whitespace` and `self.column` are updated accordingly. If `self.encoding` is set, the written spaces are encoded before writing.
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
        r"""Write a line break to the output stream and update the emitter's position and spacing state.

        Parameters:
            data (str | bytes | None): The line break sequence to write (e.g., "\n", "\r\n"). If None, uses the emitter's `best_line_break`. Bytes will be written directly; str will be encoded using the emitter's `encoding` when set.
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
        """Write a YAML version directive to the output stream.

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
        """Write a %TAG directive to the output stream followed by a line break.

        Parameters:
        	handle_text (str): The tag handle (e.g., '!yaml!') to appear after `%TAG`.
        	prefix_text (str): The tag prefix (a URI or local tag prefix) to appear after the handle.
        """
        data = f"%TAG {handle_text} {prefix_text}"
        if self.encoding:
            data = data.encode(self.encoding)
        self.stream.write(data)
        self.write_line_break()

    # Scalar streams.

    def write_single_quoted(self, text, split=True) -> None:  # noqa: ANN001, PLR0912
        """Write a single-quoted YAML scalar to the emitter's stream, surrounding the text with single quotes and emitting any necessary line breaks, indentation, and escapes.

        Parameters:
            text (str): The scalar content to emit. Internal single quotes are escaped by doubling (`'` -> `''`); existing line breaks are preserved and emitted as YAML line breaks.
            split (bool): If True, allow the emitter to insert line breaks/indentation to respect the configured line width; if False, emit the scalar without automatic splitting.
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
        r"""Serialize `text` as a double-quoted YAML scalar and write it (including surrounding double quotes) to the emitter's output stream.

        Parameters:
            text (str): The scalar content to emit.
            split (bool): If True, allow inserting line continuations to respect the emitter's `best_width`; if False, emit the scalar without inserting soft line breaks.

        Notes:
            - Special characters are emitted using YAML double-quoted escaping and non-printable or disallowed code points are escaped as `\\x`, `\\u`, or `\\U` sequences as appropriate.
            - Unicode characters are emitted directly when `self.allow_unicode` permits; otherwise they are escaped.
            - If the emitter has an `encoding` set, emitted text and escape sequences are encoded using that encoding before writing to the stream.
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
        """Compute YAML block scalar hints (indentation and chomping indicator) for the given text.

        The returned hint string may contain an indentation indicator (the emitter's best indent as a decimal string)
        followed by a chomping indicator: '-' to strip the final line break, '+' to keep trailing line breaks,
        or no chomping indicator when the default behavior applies.

        Parameters:
            text (str): The scalar content to analyze.

        Returns:
            str: The hint string to append to a block scalar indicator (e.g., '|2-', '|+', or '').
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
        """Emit a folded block scalar (using the `>` indicator) for the given text.

        Writes the folded block scalar header including computed chomping/indent hints, then serializes `text` using YAML's folded-scalar rules: preserving and folding line breaks and spaces as required and emitting appropriate indentation and line breaks. If the computed chomping indicator is `+`, sets `self.open_ended = True`. The emitted bytes/characters are written to the emitter's output stream and update the emitter's line/column/indentation state.
        Parameters:
            text (str): The scalar content to serialize as a folded block scalar.
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
        """Emit a YAML literal block scalar for the given text to the output stream.

        Writes a '|' block scalar including its chomping/indentation hints, preserves all line breaks and whitespace in `text`, and sends the resulting literal block to the configured stream. If the chosen chomping indicator ends with '+', sets `self.open_ended` to True to indicate the document remains open.

        Parameters:
            text (str): The scalar content to emit as a literal block; may contain arbitrary Unicode and line break characters.
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
        """Write a plain (unquoted) scalar to the output stream, preserving spaces and line breaks and applying line folding/indentation when needed.

        This writes `text` directly to `self.stream`, inserting a leading space if the emitter is not currently at whitespace, encoding bytes when `self.encoding` is set, and handling internal sequences of spaces and line breaks so indentation and YAML line folding rules are respected. If `self.root_context` is true, `self.open_ended` is set to True before writing.

        Parameters:
            text (str): The scalar content to emit; if empty, nothing is written.
            split (bool): If True, allow inserting indentation/line breaks when a single space would cause the column to exceed `self.best_width`; if False, avoid automatic splitting.
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
