# Scanner produces tokens of the following types:  # noqa: D100
# STREAM-START
# STREAM-END
# DIRECTIVE(name, value)
# DOCUMENT-START
# DOCUMENT-END
# BLOCK-SEQUENCE-START
# BLOCK-MAPPING-START
# BLOCK-END
# FLOW-SEQUENCE-START
# FLOW-MAPPING-START
# FLOW-SEQUENCE-END
# FLOW-MAPPING-END
# BLOCK-ENTRY
# FLOW-ENTRY
# KEY
# VALUE
# ALIAS(value)
# ANCHOR(value)
# TAG(value)
# SCALAR(value, plain, style)
#
# Read comments in the Scanner code for more details.
#

__all__ = ["Scanner", "ScannerError"]

import string

from .error import MarkedYAMLError
from .tokens import *  # noqa: F403


class ScannerError(MarkedYAMLError):  # noqa: D101
    pass


class SimpleKey:
    # See below simple keys treatment.

    def __init__(self, token_number, required, index, line, column, mark) -> None:  # noqa: ANN001, PLR0913, PLR0917
        """Initialize a SimpleKey instance carrying positional and token metadata for a potential simple key.

        Parameters:
            token_number (int): Absolute token index where the candidate key would be inserted.
            required (bool): Whether a following `:` is required for this candidate to be valid.
            index (int): Character offset in the input stream where the candidate starts.
            line (int): Line number of the candidate start (0-based).
            column (int): Column number of the candidate start (0-based).
            mark (Mark): Scanner mark object representing the exact start position for error reporting.
        """  # noqa: E501
        self.token_number = token_number
        self.required = required
        self.index = index
        self.line = line
        self.column = column
        self.mark = mark


class Scanner:  # noqa: D101, PLR0904
    def __init__(self) -> None:
        """Set up internal scanner state and emit the initial STREAM-START token.

        Initializes flags and structures used during scanning, including:
        - done: whether end of stream has been reached
        - flow_level: nesting depth for flow collections ('{' and '[')
        - tokens: buffered tokens awaiting emission
        - tokens_taken: count of tokens already returned by get_token()
        - indent and indents: current and stacked indentation levels
        - allow_simple_key and possible_simple_keys: state and candidates used to detect simple keys

        Also calls fetch_stream_start() to enqueue the STREAM-START token.
        """  # noqa: E501
        # It is assumed that Scanner and Reader will have a common descendant.
        # Reader do the dirty work of checking for BOM and converting the
        # input data to Unicode. It also adds NUL to the end.
        #
        # Reader supports the following methods
        #   self.peek(i=0)       # peek the next i-th character
        #   self.prefix(l=1)     # peek the next l characters
        #   self.forward(l=1)    # read the next l characters and move the pointer.

        # Had we reached the end of the stream?
        self.done = False

        # The number of unclosed '{' and '['. `flow_level == 0` means block
        # context.
        self.flow_level = 0

        # List of processed tokens that are not yet emitted.
        self.tokens = []

        # Add the STREAM-START token.
        self.fetch_stream_start()

        # Number of tokens that were emitted through the `get_token` method.
        self.tokens_taken = 0

        # The current indentation level.
        self.indent = -1

        # Past indentation levels.
        self.indents = []

        # Variables related to simple keys treatment.

        # A simple key is a key that is not denoted by the '?' indicator.
        # Example of simple keys:
        #   ---
        #   block simple key: value
        #   ? not a simple key:
        #   : { flow simple key: value }
        # We emit the KEY token before all keys, so when we find a potential
        # simple key, we try to locate the corresponding ':' indicator.
        # Simple keys should be limited to a single line and 1024 characters.

        # Can a simple key start at the current position? A simple key may
        # start:
        # - at the beginning of the line, not counting indentation spaces
        #       (in block context),
        # - after '{', '[', ',' (in the flow context),
        # - after '?', ':', '-' (in the block context).
        # In the block context, this flag also signifies if a block collection
        # may start at the current position.
        self.allow_simple_key = True

        # Keep track of possible simple keys. This is a dictionary. The key
        # is `flow_level`; there can be no more that one possible simple key
        # for each level. The value is a SimpleKey record:
        #   (token_number, required, index, line, column, mark)
        # A simple key may start with ALIAS, ANCHOR, TAG, SCALAR(flow),
        # '[', or '{' tokens.
        self.possible_simple_keys = {}

    # Public methods.

    def check_token(self, *choices) -> bool:  # noqa: ANN002
        # Check if the next token is one of the given types.
        """
        Determine whether the next queued token matches any of the provided token classes.
        
        Parameters:
            choices (type): One or more token classes to test against the next queued token. If omitted, the method only checks that a token is available.
        
        Returns:
            True if the next queued token is an instance of any provided class, False otherwise.
        """  # noqa: E501
        while self.need_more_tokens():
            self.fetch_more_tokens()
        if self.tokens:
            if not choices:
                return True
            for choice in choices:
                if isinstance(self.tokens[0], choice):
                    return True
        return False

    def peek_token(self):  # noqa: ANN201
        # Return the next token, but do not delete if from the queue.
        # Return None if no more tokens.
        """Return the next queued token without removing it.

        Ensures the scanner has produced tokens and returns the first queued token, or `None` if no tokens remain.

        Returns:
            token | None: The next token from the queue, or `None` when the token stream is exhausted.
        """  # noqa: E501
        while self.need_more_tokens():
            self.fetch_more_tokens()
        if self.tokens:
            return self.tokens[0]
        return None

    def get_token(self):  # noqa: ANN201
        # Return the next token.
        """Return and consume the next queued token, advancing the scanner's token position.

        Returns:
            token | None: The next token from the queue, or `None` if the stream is exhausted.
        """  # noqa: E501
        while self.need_more_tokens():
            self.fetch_more_tokens()
        if self.tokens:
            self.tokens_taken += 1
            return self.tokens.pop(0)
        return None

    # Private methods.

    def need_more_tokens(self) -> bool | None:
        """Determine whether the scanner must fetch additional tokens to continue tokenization.

        Returns:
            `False` if the stream is finished, `True` if more tokens are required (the token buffer is empty or a pending simple-key candidate must be resolved), `None` if there is currently no need to fetch more tokens.
        """  # noqa: E501
        if self.done:
            return False
        if not self.tokens:
            return True
        # The current token may be a potential simple key, so we
        # need to look further.
        self.stale_possible_simple_keys()
        if self.next_possible_simple_key() == self.tokens_taken:
            return True
        return None

    def fetch_more_tokens(self):  # noqa: ANN201, PLR0911, PLR0912
        # Eat whitespaces and comments until we reach the next token.
        """Scan input from the current position and append the next token(s) to the scanner's token buffer.

        This method advances the scanner by skipping whitespace and comments, resolving stale simple-key candidates, and determining which token (stream/document indicator, structural marker, anchor/tag/alias, block/flow scalar, or plain scalar) starts at the current location. It updates scanner state such as indentation, flow-level, and simple-key tracking as required.

        Raises:
            ScannerError: If the character at the current position cannot start any valid token.
        """  # noqa: E501
        self.scan_to_next_token()

        # Remove obsolete possible simple keys.
        self.stale_possible_simple_keys()

        # Compare the current indentation and column. It may add some tokens
        # and decrease the current indentation level.
        self.unwind_indent(self.column)

        # Peek the next character.
        ch = self.peek()

        # Is it the end of stream?
        if ch == "\0":
            return self.fetch_stream_end()

        # Is it a directive?
        if ch == "%" and self.check_directive():
            return self.fetch_directive()

        # Is it the document start?
        if ch == "-" and self.check_document_start():
            return self.fetch_document_start()

        # Is it the document end?
        if ch == "." and self.check_document_end():
            return self.fetch_document_end()

        # TODO: support for BOM within a stream.
        # if ch == '\uFEFF':
        #    return self.fetch_bom()    <-- issue BOMToken

        # Note: the order of the following checks is NOT significant.

        # Is it the flow sequence start indicator?
        if ch == "[":
            return self.fetch_flow_sequence_start()

        # Is it the flow mapping start indicator?
        if ch == "{":
            return self.fetch_flow_mapping_start()

        # Is it the flow sequence end indicator?
        if ch == "]":
            return self.fetch_flow_sequence_end()

        # Is it the flow mapping end indicator?
        if ch == "}":
            return self.fetch_flow_mapping_end()

        # Is it the flow entry indicator?
        if ch == ",":
            return self.fetch_flow_entry()

        # Is it the block entry indicator?
        if ch == "-" and self.check_block_entry():
            return self.fetch_block_entry()

        # Is it the key indicator?
        if ch == "?" and self.check_key():
            return self.fetch_key()

        # Is it the value indicator?
        if ch == ":" and self.check_value():
            return self.fetch_value()

        # Is it an alias?
        if ch == "*":
            return self.fetch_alias()

        # Is it an anchor?
        if ch == "&":
            return self.fetch_anchor()

        # Is it a tag?
        if ch == "!":
            return self.fetch_tag()

        # Is it a literal scalar?
        if ch == "|" and not self.flow_level:
            return self.fetch_literal()

        # Is it a folded scalar?
        if ch == ">" and not self.flow_level:
            return self.fetch_folded()

        # Is it a single quoted scalar?
        if ch == "'":
            return self.fetch_single()

        # Is it a double quoted scalar?
        if ch == '"':
            return self.fetch_double()

        # It must be a plain scalar then.
        if self.check_plain():
            return self.fetch_plain()

        # No? It's an error. Let's produce a nice error message.
        raise ScannerError(  # noqa: TRY003
            "while scanning for the next token",
            None,
            f"found character {ch!r} that cannot start any token",
            self.get_mark(),
        )

    # Simple keys treatment.

    def next_possible_simple_key(self):  # noqa: ANN201
        # Return the number of the nearest possible simple key. Actually we
        # don't need to loop through the whole dictionary. We may replace it
        # with the following code:
        #   if not self.possible_simple_keys:
        #       return None
        #   return self.possible_simple_keys[
        #           min(self.possible_simple_keys.keys())].token_number
        """
        Get the token number of the earliest pending simple-key candidate.
        
        Returns:
            The smallest `token_number` among saved simple-key candidates, or `None` if no candidates exist.
        """  # noqa: E501
        min_token_number = None
        for level in self.possible_simple_keys:
            key = self.possible_simple_keys[level]
            if min_token_number is None or key.token_number < min_token_number:
                min_token_number = key.token_number
        return min_token_number

    def stale_possible_simple_keys(self) -> None:
        # Remove entries that are no longer possible simple keys. According to
        # the YAML specification, simple keys
        # - should be limited to a single line,
        # - should be no longer than 1024 characters.
        # Disabling this procedure will allow simple keys of any length and
        # height (may cause problems if indentation is broken though).
        """Discard saved simple-key candidates that are no longer valid.

        Removes any candidate simple key whose start is on a different line than
        the current scanner position or whose length from start to current index
        exceeds 1024 characters. If a removed candidate was marked `required`, a
        ScannerError is raised indicating that the expected ':' could not be
        found.

        Raises:
            ScannerError: If a required simple key is expired (missing ':' before
            its expiry).
        """
        for level in list(self.possible_simple_keys):
            key = self.possible_simple_keys[level]
            if key.line != self.line or self.index - key.index > 1024:  # noqa: PLR2004
                if key.required:
                    raise ScannerError(  # noqa: TRY003
                        "while scanning a simple key",
                        key.mark,
                        "could not find expected ':'",
                        self.get_mark(),
                    )
                del self.possible_simple_keys[level]

    def save_possible_simple_key(self) -> None:
        # The next token may start a simple key. We check if it's possible
        # and save its position. This function is called for
        #   ALIAS, ANCHOR, TAG, SCALAR(flow), '[', and '{'.

        # Check if a simple key is required at the current position.
        """
        Record the current position as a candidate simple key for later resolution.
        
        If simple keys are allowed at this position, remove any existing candidate for the current flow level and save a new SimpleKey containing:
        - token_number: the index in the token stream where the key token would be inserted,
        - required: true when in block context and the current column equals the current indent (indicating a required key),
        - the scanner position (index, line, column) and mark for error reporting.
        """  # noqa: E501
        required = not self.flow_level and self.indent == self.column

        # The next token might be a simple key. Let's save it's number and
        # position.
        if self.allow_simple_key:
            self.remove_possible_simple_key()
            token_number = self.tokens_taken + len(self.tokens)
            key = SimpleKey(
                token_number,
                required,
                self.index,
                self.line,
                self.column,
                self.get_mark(),
            )
            self.possible_simple_keys[self.flow_level] = key

    def remove_possible_simple_key(self) -> None:
        # Remove the saved possible key position at the current flow level.
        """Remove any saved simple-key candidate for the current flow level.

        If a candidate exists at the current flow level and its `required` flag is True,
        raises ScannerError reporting that a ':' was not found; otherwise the candidate
        is removed.

        Raises:
            ScannerError: If a required simple key is missing its ':' marker.
        """
        if self.flow_level in self.possible_simple_keys:
            key = self.possible_simple_keys[self.flow_level]

            if key.required:
                raise ScannerError(  # noqa: TRY003
                    "while scanning a simple key",
                    key.mark,
                    "could not find expected ':'",
                    self.get_mark(),
                )

            del self.possible_simple_keys[self.flow_level]

    # Indentation functions.

    def unwind_indent(self, column) -> None:  # noqa: ANN001
        # In flow context, tokens should respect indentation.
        # Actually the condition should be `self.indent >= column` according to
        # the spec. But this condition will prohibit intuitively correct
        # constructions such as
        # key : {
        # }
        # if self.flow_level and self.indent > column:
        #    raise ScannerError(None, None,
        #            "invalid indentation or unclosed '[' or '{'",
        #            self.get_mark())

        # In the flow context, indentation is ignored. We make the scanner less
        # restrictive then specification requires.
        """Reduce the current block indentation down to the given column, emitting BlockEnd tokens for each dedent.

        This method is a no-op when in flow context (i.e., when `flow_level` is nonzero). In block context it repeatedly pops indentation levels from `self.indents`, updates `self.indent`, and appends a `BlockEndToken` for each indentation level removed.

        Parameters:
            column (int): Target column to unwind to; indentation levels strictly greater than this column will be removed.
        """  # noqa: E501
        if self.flow_level:
            return

        # In block context, we may need to issue the BLOCK-END tokens.
        while self.indent > column:
            mark = self.get_mark()
            self.indent = self.indents.pop()
            self.tokens.append(BlockEndToken(mark, mark))  # noqa: F405

    def add_indent(self, column) -> bool:  # noqa: ANN001
        # Check if we need to increase indentation.
        """
        Increase the current indentation level to column when column is greater than the current indent.
        
        Parameters:
            column (int): Candidate column to use as the new indentation level.
        
        Returns:
            True if the indentation was increased to column, False otherwise.
        """  # noqa: E501
        if self.indent < column:
            self.indents.append(self.indent)
            self.indent = column
            return True
        return False

    # Fetchers.

    def fetch_stream_start(self) -> None:
        # We always add STREAM-START as the first token and STREAM-END as the
        # last token.

        # Read the token.
        """Append a STREAM-START token to the scanner's token buffer.

        The appended token uses the scanner's current mark for both start and end and includes the scanner's configured encoding.
        """  # noqa: E501
        mark = self.get_mark()

        # Add STREAM-START.
        self.tokens.append(StreamStartToken(mark, mark, encoding=self.encoding))  # noqa: F405

    def fetch_stream_end(self) -> None:
        # Set the current indentation to -1.
        """Finalize scanning by emitting a STREAM-END token and resetting scanner state.

        Resets indentation to -1, clears any pending simple-key candidates and disables simple-key handling, appends a `StreamEndToken` at the current mark, and marks the scanner as finished.
        """  # noqa: E501
        self.unwind_indent(-1)

        # Reset simple keys.
        self.remove_possible_simple_key()
        self.allow_simple_key = False
        self.possible_simple_keys = {}

        # Read the token.
        mark = self.get_mark()

        # Add STREAM-END.
        self.tokens.append(StreamEndToken(mark, mark))  # noqa: F405

        # The steam is finished.
        self.done = True

    def fetch_directive(self) -> None:
        # Set the current indentation to -1.
        """Process a directive at the current scanner position and append the resulting DirectiveToken to the token buffer.

        This will unwind block indentation to the base level, clear any pending simple-key candidate and disable further simple-key recognition before scanning and appending the directive token.
        """  # noqa: E501
        self.unwind_indent(-1)

        # Reset simple keys.
        self.remove_possible_simple_key()
        self.allow_simple_key = False

        # Scan and add DIRECTIVE.
        self.tokens.append(self.scan_directive())

    def fetch_document_start(self) -> None:
        """Handle a document start marker ('---') and append a DocumentStartToken to the token buffer.

        This prepares scanner state for a new document by resetting indentation and simple-key tracking, consumes the document-start indicator, and enqueues the corresponding DocumentStartToken with its marks.
        """  # noqa: E501
        self.fetch_document_indicator(DocumentStartToken)  # noqa: F405

    def fetch_document_end(self) -> None:
        """Process a document end marker ('...') at the current position and append a DocumentEndToken to the token queue."""  # noqa: E501
        self.fetch_document_indicator(DocumentEndToken)  # noqa: F405

    def fetch_document_indicator(self, TokenClass) -> None:  # noqa: ANN001, N803
        # Set the current indentation to -1.
        """Handle a document indicator ('---' or '...') by resetting indentation and simple-key state, consuming the three-character indicator, and appending the corresponding document token.

        Parameters:
            TokenClass (type): Token class to create for the indicator (e.g., DocumentStartToken or DocumentEndToken). The token will be instantiated with the start and end marks surrounding the three-character indicator.
        """  # noqa: E501
        self.unwind_indent(-1)

        # Reset simple keys. Note that there could not be a block collection
        # after '---'.
        self.remove_possible_simple_key()
        self.allow_simple_key = False

        # Add DOCUMENT-START or DOCUMENT-END.
        start_mark = self.get_mark()
        self.forward(3)
        end_mark = self.get_mark()
        self.tokens.append(TokenClass(start_mark, end_mark))

    def fetch_flow_sequence_start(self) -> None:
        """Start a flow sequence by updating the scanner's flow context and appending a FlowSequenceStartToken to the token buffer."""  # noqa: E501
        self.fetch_flow_collection_start(FlowSequenceStartToken)  # noqa: F405

    def fetch_flow_mapping_start(self) -> None:
        """Mark the start of a flow-style mapping (`{`) and update scanner state for entering a flow collection.
        This adjusts flow-level tracking and enables simple-key recognition appropriate for the new flow context.
        """  # noqa: D205, E501
        self.fetch_flow_collection_start(FlowMappingStartToken)  # noqa: F405

    def fetch_flow_collection_start(self, TokenClass) -> None:  # noqa: ANN001, N803
        # '[' and '{' may start a simple key.
        """Handle the start of a flow collection by updating scanner state and emitting the corresponding start token.

        This records a possible simple key at the current position, increments the flow nesting level, enables simple keys inside the new flow context, and appends an instance of `TokenClass` (using the scanner's current start and end marks) to the token buffer.

        Parameters:
            TokenClass (type): Token class to instantiate for the flow collection start (e.g., FlowSequenceStartToken or FlowMappingStartToken).
        """  # noqa: E501
        self.save_possible_simple_key()

        # Increase the flow level.
        self.flow_level += 1

        # Simple keys are allowed after '[' and '{'.
        self.allow_simple_key = True

        # Add FLOW-SEQUENCE-START or FLOW-MAPPING-START.
        start_mark = self.get_mark()
        self.forward()
        end_mark = self.get_mark()
        self.tokens.append(TokenClass(start_mark, end_mark))

    def fetch_flow_sequence_end(self) -> None:
        """Process the end of a flow sequence (`]`), update scanner flow/simple-key state, and emit a FlowSequenceEndToken."""  # noqa: E501
        self.fetch_flow_collection_end(FlowSequenceEndToken)  # noqa: F405

    def fetch_flow_mapping_end(self) -> None:
        """Finalize a flow mapping by consuming its closing delimiter and emitting a FlowMappingEndToken.

        This ends the current flow-mapping context and appends the corresponding FlowMappingEndToken to the scanner's token buffer.
        """  # noqa: E501
        self.fetch_flow_collection_end(FlowMappingEndToken)  # noqa: F405

    def fetch_flow_collection_end(self, TokenClass) -> None:  # noqa: ANN001, N803
        # Reset possible simple key on the current level.
        """
        Handle the end of a flow collection by emitting its corresponding end token.
        
        Parameters:
            TokenClass (type): Token class to instantiate for the collection end (e.g., FlowSequenceEndToken or FlowMappingEndToken).
        """  # noqa: E501
        self.remove_possible_simple_key()

        # Decrease the flow level.
        self.flow_level -= 1

        # No simple keys after ']' or '}'.
        self.allow_simple_key = False

        # Add FLOW-SEQUENCE-END or FLOW-MAPPING-END.
        start_mark = self.get_mark()
        self.forward()
        end_mark = self.get_mark()
        self.tokens.append(TokenClass(start_mark, end_mark))

    def fetch_flow_entry(self) -> None:
        # Simple keys are allowed after ','.
        """Consume a flow entry separator (`,`) and emit a FlowEntryToken while enabling simple-key recognition and clearing any pending simple-key candidate at the current flow level.

        This modifies scanner state by advancing one character, appending a FlowEntryToken for the consumed separator, setting `allow_simple_key` to True, and removing any saved possible simple key for the current flow level.
        """  # noqa: E501
        self.allow_simple_key = True

        # Reset possible simple key on the current level.
        self.remove_possible_simple_key()

        # Add FLOW-ENTRY.
        start_mark = self.get_mark()
        self.forward()
        end_mark = self.get_mark()
        self.tokens.append(FlowEntryToken(start_mark, end_mark))  # noqa: F405

    def fetch_block_entry(self) -> None:
        # Block context needs additional checks.
        """Handle a block sequence entry indicator (`-`) at the current scanner position.

        Validates that a sequence entry is allowed in the current block context and, if needed, starts a new block sequence (by emitting a block-sequence-start token). After processing the indicator, permits simple keys at the following position, clears any pending simple-key candidate for the current level, and emits a block-entry token.

        Raises:
                ScannerError: If sequence entries are not allowed at the current position.
        """  # noqa: D206, E101, E501
        if not self.flow_level:
            # Are we allowed to start a new entry?
            if not self.allow_simple_key:
                raise ScannerError(
                    None, None, "sequence entries are not allowed here", self.get_mark()
                )

            # We may need to add BLOCK-SEQUENCE-START.
            if self.add_indent(self.column):
                mark = self.get_mark()
                self.tokens.append(BlockSequenceStartToken(mark, mark))  # noqa: F405

        # It's an error for the block entry to occur in the flow context,
        # but we let the parser detect this.
        else:
            pass

        # Simple keys are allowed after '-'.
        self.allow_simple_key = True

        # Reset possible simple key on the current level.
        self.remove_possible_simple_key()

        # Add BLOCK-ENTRY.
        start_mark = self.get_mark()
        self.forward()
        end_mark = self.get_mark()
        self.tokens.append(BlockEntryToken(start_mark, end_mark))  # noqa: F405

    def fetch_key(self) -> None:
        # Block context needs additional checks.
        """
        Handle a mapping key indicator ('?') and produce the corresponding Key token.
        
        In block context, enforce that mapping keys are allowed and, if the column increases indentation, start a new block mapping. Update simple-key allowance for the current context, clear any pending simple-key candidate at this flow level, and emit a KeyToken for the indicator.
        
        Raises:
            ScannerError: if a mapping key is not allowed at the current position.
        """  # noqa: E501
        if not self.flow_level:
            # Are we allowed to start a key (not necessary a simple)?
            if not self.allow_simple_key:
                raise ScannerError(
                    None, None, "mapping keys are not allowed here", self.get_mark()
                )

            # We may need to add BLOCK-MAPPING-START.
            if self.add_indent(self.column):
                mark = self.get_mark()
                self.tokens.append(BlockMappingStartToken(mark, mark))  # noqa: F405

        # Simple keys are allowed after '?' in the block context.
        self.allow_simple_key = not self.flow_level

        # Reset possible simple key on the current level.
        self.remove_possible_simple_key()

        # Add KEY.
        start_mark = self.get_mark()
        self.forward()
        end_mark = self.get_mark()
        self.tokens.append(KeyToken(start_mark, end_mark))  # noqa: F405

    def fetch_value(self) -> None:
        # Do we determine a simple key?
        """
        Handle a ':' value indicator at the current position, resolving any pending simple-key candidate or treating it as a complex mapping value.
        
        If a simple-key candidate exists for the current flow level, insert a Key token at the candidate's token position, optionally insert a BlockMappingStart token when that key begins a new block mapping, and disallow further simple keys. Otherwise, in block context validate that a mapping value is allowed, optionally start a new block mapping when indentation increases, allow simple keys after the colon only in block context, and clear the possible simple-key candidate for the current level. Finally, consume the ':' and append a Value token spanning the consumed character.
        
        Raises:
            ScannerError: When in block context and mapping values are not allowed at this position.
        """  # noqa: E501
        if self.flow_level in self.possible_simple_keys:
            # Add KEY.
            key = self.possible_simple_keys[self.flow_level]
            del self.possible_simple_keys[self.flow_level]
            self.tokens.insert(
                key.token_number - self.tokens_taken,
                KeyToken(key.mark, key.mark),  # noqa: F405
            )

            # If this key starts a new block mapping, we need to add
            # BLOCK-MAPPING-START.
            if not self.flow_level and self.add_indent(key.column):
                self.tokens.insert(
                    key.token_number - self.tokens_taken,
                    BlockMappingStartToken(key.mark, key.mark),  # noqa: F405
                )

            # There cannot be two simple keys one after another.
            self.allow_simple_key = False

        # It must be a part of a complex key.
        else:
            # Block context needs additional checks.
            # (Do we really need them? They will be caught by the parser
            # anyway.)
            if not self.flow_level:  # noqa: SIM102
                # We are allowed to start a complex value if and only if
                # we can start a simple key.
                if not self.allow_simple_key:
                    raise ScannerError(
                        None,
                        None,
                        "mapping values are not allowed here",
                        self.get_mark(),
                    )

            # If this value starts a new block mapping, we need to add
            # BLOCK-MAPPING-START.  It will be detected as an error later by
            # the parser.
            if not self.flow_level and self.add_indent(self.column):
                mark = self.get_mark()
                self.tokens.append(BlockMappingStartToken(mark, mark))  # noqa: F405

            # Simple keys are allowed after ':' in the block context.
            self.allow_simple_key = not self.flow_level

            # Reset possible simple key on the current level.
            self.remove_possible_simple_key()

        # Add VALUE.
        start_mark = self.get_mark()
        self.forward()
        end_mark = self.get_mark()
        self.tokens.append(ValueToken(start_mark, end_mark))  # noqa: F405

    def fetch_alias(self) -> None:
        # ALIAS could be a simple key.
        """
        Scan an alias and append an AliasToken to the token buffer.
        
        If a simple key can start at this position, record it; then disable simple-key allowance and append the scanned AliasToken.
        """  # noqa: E501
        self.save_possible_simple_key()

        # No simple keys after ALIAS.
        self.allow_simple_key = False

        # Scan and add ALIAS.
        self.tokens.append(self.scan_anchor(AliasToken))  # noqa: F405

    def fetch_anchor(self) -> None:
        # ANCHOR could start a simple key.
        """
        Scan an anchor at the current position and append the resulting AnchorToken to the token buffer.
        
        Saves a possible simple-key candidate for the current flow level, disables further simple-key recognition, and appends the scanned AnchorToken to self.tokens.
        """  # noqa: E501
        self.save_possible_simple_key()

        # No simple keys after ANCHOR.
        self.allow_simple_key = False

        # Scan and add ANCHOR.
        self.tokens.append(self.scan_anchor(AnchorToken))  # noqa: F405

    def fetch_tag(self) -> None:
        # TAG could start a simple key.
        """Handle a YAML tag indicator at the current scanner position.

        Records the current position as a possible simple key (if allowed), disables further simple-key recognition, and appends the scanned TagToken to the internal token buffer.
        """  # noqa: E501
        self.save_possible_simple_key()

        # No simple keys after TAG.
        self.allow_simple_key = False

        # Scan and add TAG.
        self.tokens.append(self.scan_tag())

    def fetch_literal(self) -> None:
        """Fetches a literal block scalar token using the '|' block scalar style."""
        self.fetch_block_scalar(style="|")

    def fetch_folded(self) -> None:
        """Fetches a folded block scalar and appends the resulting scalar token to the token stream using the ">" style."""  # noqa: E501
        self.fetch_block_scalar(style=">")

    def fetch_block_scalar(self, style) -> None:  # noqa: ANN001
        # A simple key may follow a block scalar.
        """
        Buffer a block scalar token and adjust simple-key tracking.
        
        Enables simple keys after the scalar, clears any pending simple-key candidate at the current flow level, and appends the scanned block scalar to the scanner's token buffer.
        
        Parameters:
            style (str): Block scalar style indicator (e.g., '|' for literal, '>' for folded).
        """  # noqa: E501
        self.allow_simple_key = True

        # Reset possible simple key on the current level.
        self.remove_possible_simple_key()

        # Scan and add SCALAR.
        self.tokens.append(self.scan_block_scalar(style))

    def fetch_single(self) -> None:
        """Read a single-quoted (') flow scalar from the input and add the resulting scalar token to the scanner's token buffer."""  # noqa: E501
        self.fetch_flow_scalar(style="'")

    def fetch_double(self) -> None:
        """
        Read a double-quoted flow scalar and append the resulting ScalarToken to the token buffer.
        """  # noqa: E501
        self.fetch_flow_scalar(style='"')

    def fetch_flow_scalar(self, style) -> None:  # noqa: ANN001
        # A flow scalar could be a simple key.
        """
        Scan a quoted (flow) scalar and append the resulting ScalarToken to the token buffer.
        
        Parameters:
            style (str): Quote character indicating scalar style, e.g. "'" for single-quoted or '"' for double-quoted.
        """  # noqa: E501
        self.save_possible_simple_key()

        # No simple keys after flow scalars.
        self.allow_simple_key = False

        # Scan and add SCALAR.
        self.tokens.append(self.scan_flow_scalar(style))

    def fetch_plain(self) -> None:
        # A plain scalar could be a simple key.
        """
        Consume a plain scalar and append the resulting ScalarToken to the token buffer.
        
        Records a possible simple-key candidate at the current position, disables simple-key recognition for subsequent characters (the invoked scan may re-enable it if the scalar ends at a line start), and appends the token produced by scan_plain() to self.tokens.
        """  # noqa: E501
        self.save_possible_simple_key()

        # No simple keys after plain scalars. But note that `scan_plain` will
        # change this flag if the scan is finished at the beginning of the
        # line.
        self.allow_simple_key = False

        # Scan and add SCALAR. May change `allow_simple_key`.
        self.tokens.append(self.scan_plain())

    # Checkers.

    def check_directive(self) -> bool | None:
        # DIRECTIVE:        ^ '%' ...
        # The '%' indicator is already checked.
        """
        Determine whether a '%' directive indicator is valid at the current scanner column.
        
        Returns:
            `True` if the scanner is at column 0, `None` otherwise.
        """  # noqa: E501
        if self.column == 0:
            return True
        return None

    def check_document_start(self) -> bool | None:
        # DOCUMENT-START:   ^ '---' (' '|'\n')
        """
        Check whether a document start marker (`---`) begins at the current column.
        
        This is valid only at column 0 and requires the three-character sequence `---` to be followed by end-of-stream, a space, a tab, or a recognized line break character.
        
        Returns:
            `True` if a document start marker is present at the current position, `None` otherwise.
        """  # noqa: E501
        if self.column == 0:  # noqa: SIM102
            if self.prefix(3) == "---" and self.peek(3) in "\0 \t\r\n\x85\u2028\u2029":
                return True
        return None

    def check_document_end(self) -> bool | None:
        # DOCUMENT-END:     ^ '...' (' '|'\n')
        """
        Determine whether a document-end indicator ("...") starts at the current line's column zero.
        
        Checks that the scanner is at column 0, the next three characters are "..." and the following character is a valid terminator (end-of-stream, space, tab, or any recognized line break).
        
        Returns:
            `True` if a document-end indicator is present at column 0 and followed by whitespace, a line break, or end-of-stream; `None` otherwise.
        """  # noqa: E501
        if self.column == 0:  # noqa: SIM102
            if self.prefix(3) == "..." and self.peek(3) in "\0 \t\r\n\x85\u2028\u2029":
                return True
        return None

    def check_block_entry(self) -> bool:
        # BLOCK-ENTRY:      '-' (' '|'\n')
        """Determine whether a '-' at the current position should be treated as a block sequence entry indicator.

        Returns:
            True if the character following '-' is space, tab, a line-break (including CR, LF, NEL, LS, PS), or the end-of-stream marker; False otherwise.
        """  # noqa: E501
        return self.peek(1) in "\0 \t\r\n\x85\u2028\u2029"

    def check_key(self) -> bool:
        # KEY(flow context):    '?'
        """Determine whether a question mark at the current position is a valid mapping key indicator.

        In flow context a question mark is always a valid key indicator. In block context it is valid only if the following character is a space, tab, a line break (including CR, LF, NEL, LS, PS), or the end-of-stream marker.

        Returns:
            True if a key indicator is valid at the current scanning position, False otherwise.
        """  # noqa: E501
        if self.flow_level:
            return True

        # KEY(block context):   '?' (' '|'\n')
        return self.peek(1) in "\0 \t\r\n\x85\u2028\u2029"

    def check_value(self) -> bool:
        # VALUE(flow context):  ':'
        """Determine whether a ':' at the current position is a valid mapping value indicator in the current scanner context.

        Returns:
            True if ':' is a valid value indicator in the current context; False otherwise. In flow context this is always True; in block context it is True only when the following character is end-of-stream, a space, tab, or a line-break (CR, LF, NEL, LS, PS).
        """  # noqa: E501
        if self.flow_level:
            return True

        # VALUE(block context): ':' (' '|'\n')
        return self.peek(1) in "\0 \t\r\n\x85\u2028\u2029"

    def check_plain(self):  # noqa: ANN201
        # A plain scalar may start with any non-space character except:
        #   '-', '?', ':', ',', '[', ']', '{', '}',
        #   '#', '&', '*', '!', '|', '>', '\'', '\"',
        #   '%', '@', '`'.
        #
        # It may also start with
        #   '-', '?', ':'
        # if it is followed by a non-space character.
        #
        # Note that we limit the last rule to the block context (except the
        # '-' character) because we want the flow context to be space
        # independent.
        r"""Determine whether a plain scalar may start at the current scanner position.

        Considers YAML's starter-character rules: a plain scalar may begin with any non-space character except
        the reserved set: "-", "?", ":", ",", "[", "]", "{", "}", "#", "&", "*", "!", "|", ">", "'", "\"",
        "%", "@", "`". The characters "-", "?", and ":" are allowed to start a plain scalar when followed by a
        non-space character; the "?" and ":" allowance applies only in block context (when `flow_level == 0`),
        while "-" is allowed regardless of flow context.

        Returns:
            `True` if a plain scalar can start at the current position, `False` otherwise.
        """  # noqa: E501
        ch = self.peek()
        return ch not in "\0 \t\r\n\x85\u2028\u2029-?:,[]{}#&*!|>'\"%@`" or (
            self.peek(1) not in "\0 \t\r\n\x85\u2028\u2029"
            and (ch == "-" or (not self.flow_level and ch in "?:"))
        )

    # Scanners.

    def scan_to_next_token(self) -> None:
        # We ignore spaces, line breaks and comments.
        # If we find a line break in the block context, we set the flag
        # `allow_simple_key` on.
        # The byte order mark is stripped if it's the first character in the
        # stream. We do not yet support BOM inside the stream as the
        # specification requires. Any such mark will be considered as a part
        # of the document.
        #
        # TODO: We need to make tab handling rules more sane. A good rule is
        #   Tabs cannot precede tokens
        #   BLOCK-SEQUENCE-START, BLOCK-MAPPING-START, BLOCK-END,
        #   KEY(block), VALUE(block), BLOCK-ENTRY
        # So the checking code is
        #   if <TAB>:
        #       self.allow_simple_keys = False
        # We also need to add the check for `allow_simple_keys == True` to
        # `unwind_indent` before issuing BLOCK-END.
        # Scanners for block, flow, and plain scalars need to be modified.
        """Advance the scanner to the next non-space, non-comment, non-line-break character.

        Consumes a leading UTF-8 byte order mark (U+FEFF) only if it appears at the very start of the stream, then skips spaces, comments (from `#` to the line break), and normalized line breaks. If a line break is encountered while in block context (flow level == 0), sets `allow_simple_key` to True. Stops with the scanner positioned at the first character that can start a token or at end-of-stream.
        """  # noqa: E501
        if self.index == 0 and self.peek() == "\ufeff":
            self.forward()
        found = False
        while not found:
            while self.peek() == " ":
                self.forward()
            if self.peek() == "#":
                while self.peek() not in "\0\r\n\x85\u2028\u2029":
                    self.forward()
            if self.scan_line_break():
                if not self.flow_level:
                    self.allow_simple_key = True
            else:
                found = True

    def scan_directive(self):  # noqa: ANN201
        # See the specification for details.
        """
        Parse a YAML directive at the current position.
        
        Parses the directive name and its directive-specific value (if any), consumes the remainder of the directive line, and produces a DirectiveToken.
        
        Returns:
            directive_token (DirectiveToken): Token containing the directive `name`, a directive-specific
            `value` (`(major, minor)` tuple of ints for "YAML", `(handle, prefix)` tuple of strings for "TAG",
            or `None` for other directives), the `start_mark`, and the `end_mark`.
        """
        start_mark = self.get_mark()
        self.forward()
        name = self.scan_directive_name(start_mark)
        value = None
        if name == "YAML":
            value = self.scan_yaml_directive_value(start_mark)
            end_mark = self.get_mark()
        elif name == "TAG":
            value = self.scan_tag_directive_value(start_mark)
            end_mark = self.get_mark()
        else:
            end_mark = self.get_mark()
            while self.peek() not in "\0\r\n\x85\u2028\u2029":
                self.forward()
        self.scan_directive_ignored_line(start_mark)
        return DirectiveToken(name, value, start_mark, end_mark)  # noqa: F405

    def scan_directive_name(self, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        """
        Parse a YAML directive name starting at the given start mark.
        
        Parameters:
            start_mark (Mark): Position where directive scanning began; used for error context.
        
        Returns:
            directive_name (str): The parsed directive name.
        
        Raises:
            ScannerError: If no valid name character is found at start_mark or if the name is not terminated by a space, line break, or end-of-stream character.
        """  # noqa: E501
        length = 0
        ch = self.peek(length)
        while "0" <= ch <= "9" or "A" <= ch <= "Z" or "a" <= ch <= "z" or ch in "-_":
            length += 1
            ch = self.peek(length)
        if not length:
            raise ScannerError(  # noqa: TRY003
                "while scanning a directive",
                start_mark,
                f"expected alphabetic or numeric character, but found {ch!r}",
                self.get_mark(),
            )
        value = self.prefix(length)
        self.forward(length)
        ch = self.peek()
        if ch not in "\0 \r\n\x85\u2028\u2029":
            raise ScannerError(  # noqa: TRY003
                "while scanning a directive",
                start_mark,
                f"expected alphabetic or numeric character, but found {ch!r}",
                self.get_mark(),
            )
        return value

    def scan_yaml_directive_value(self, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        """
        Parse the value of a `%YAML` directive into major and minor version numbers.
        
        Parameters:
            start_mark: Mark object pointing to the directive start for error reporting.
        
        Returns:
            (major, minor): Tuple of two integers for the YAML version's major and minor numbers.
        
        Raises:
            ScannerError: If the value is not a valid `major.minor` numeric form or is terminated incorrectly.
        """  # noqa: E501
        while self.peek() == " ":
            self.forward()
        major = self.scan_yaml_directive_number(start_mark)
        if self.peek() != ".":
            raise ScannerError(  # noqa: TRY003
                "while scanning a directive",
                start_mark,
                f"expected a digit or '.', but found {self.peek()!r}",
                self.get_mark(),
            )
        self.forward()
        minor = self.scan_yaml_directive_number(start_mark)
        if self.peek() not in "\0 \r\n\x85\u2028\u2029":
            raise ScannerError(  # noqa: TRY003
                "while scanning a directive",
                start_mark,
                f"expected a digit or ' ', but found {self.peek()!r}",
                self.get_mark(),
            )
        return (major, minor)

    def scan_yaml_directive_number(self, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        """Parse a decimal number from the current scanner position for a YAML directive.

        Consumes consecutive ASCII digits, advances the scanner past them, and returns the parsed integer.

        Parameters:
            start_mark: The mark representing the start position used for error context.

        Returns:
            The integer value parsed from the digit sequence.

        Raises:
            ScannerError: If the next character is not a digit.
        """  # noqa: E501
        ch = self.peek()
        if not ("0" <= ch <= "9"):
            raise ScannerError(  # noqa: TRY003
                "while scanning a directive",
                start_mark,
                f"expected a digit, but found {ch!r}",
                self.get_mark(),
            )
        length = 0
        while "0" <= self.peek(length) <= "9":
            length += 1
        value = int(self.prefix(length))
        self.forward(length)
        return value

    def scan_tag_directive_value(self, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        """
        Parse the value of a `%TAG` directive.
        
        Parameters:
            start_mark: Mark object for the directive start used to provide context in parsing errors when extracting the handle and prefix.
        
        Returns:
            (handle, prefix): `handle` is the tag handle (including its `!` markers), and `prefix` is the tag prefix URI.
        """  # noqa: E501
        while self.peek() == " ":
            self.forward()
        handle = self.scan_tag_directive_handle(start_mark)
        while self.peek() == " ":
            self.forward()
        prefix = self.scan_tag_directive_prefix(start_mark)
        return (handle, prefix)

    def scan_tag_directive_handle(self, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        """
        Parse a tag directive handle at start_mark and require a single space immediately after it.
        
        Parameters:
            start_mark: Mark where scanning of the directive began.
        
        Returns:
            handle (str): The parsed tag directive handle.
        
        Raises:
            ScannerError: If the character following the handle is not a space.
        """  # noqa: E501
        value = self.scan_tag_handle("directive", start_mark)
        ch = self.peek()
        if ch != " ":
            raise ScannerError(  # noqa: TRY003
                "while scanning a directive",
                start_mark,
                f"expected ' ', but found {ch!r}",
                self.get_mark(),
            )
        return value

    def scan_tag_directive_prefix(self, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        """
        Parse a TAG directive's URI prefix and verify it is followed by a valid terminator.
        
        Parameters:
            start_mark: The mark at the start of the directive used for error context.
        
        Returns:
            prefix (str): The parsed tag URI prefix.
        
        Raises:
            ScannerError: If the character following the URI is not a valid terminator (space, line break, NEL, U+2028/U+2029, or end-of-stream).
        """  # noqa: D206, E101, E501
        value = self.scan_tag_uri("directive", start_mark)
        ch = self.peek()
        if ch not in "\0 \r\n\x85\u2028\u2029":
            raise ScannerError(  # noqa: TRY003
                "while scanning a directive",
                start_mark,
                f"expected ' ', but found {ch!r}",
                self.get_mark(),
            )
        return value

    def scan_directive_ignored_line(self, start_mark) -> None:  # noqa: ANN001
        # See the specification for details.
        """Consume the remainder of a directive line: optional spaces, an optional comment, and a terminating line break.

        Skips any spaces, then if a comment (`#`) is present consumes characters until a line break or end-of-stream. If the next character after optional spaces/comment is not a line break or end-of-stream, raises a ScannerError reporting an unexpected character at the provided start mark.

        Parameters:
            start_mark: The mark representing the position where the directive began; used as context if an error is raised.

        Raises:
            ScannerError: If the line does not end with a comment or a line break (an unexpected character is encountered).
        """  # noqa: E501
        while self.peek() == " ":
            self.forward()
        if self.peek() == "#":
            while self.peek() not in "\0\r\n\x85\u2028\u2029":
                self.forward()
        ch = self.peek()
        if ch not in "\0\r\n\x85\u2028\u2029":
            raise ScannerError(  # noqa: TRY003
                "while scanning a directive",
                start_mark,
                f"expected a comment or a line break, but found {ch!r}",
                self.get_mark(),
            )
        self.scan_line_break()

    def scan_anchor(self, TokenClass):  # noqa: ANN001, ANN201, N803
        # The specification does not restrict characters for anchors and
        # aliases. This may lead to problems, for instance, the document:
        #   [ *alias, value ]
        # can be interpreted in two ways, as
        #   [ "value" ]
        # and
        #   [ *alias , "value" ]
        # Therefore we restrict aliases to numbers and ASCII letters.
        """
        Parse an anchor or alias at the current scanner position.
        
        Consumes the anchor ('&') or alias ('*') indicator and the following identifier (ASCII letters, digits, '-' or '_'), validates that an identifier is present and terminated by a valid YAML delimiter, and returns a token representing that identifier.
        
        Parameters:
            TokenClass (type): Token class to instantiate with the parsed identifier and its start/end marks.
        
        Returns:
            token (TokenClass): An instance of TokenClass containing the parsed identifier and its start and end marks.
        
        Raises:
            ScannerError: If no identifier follows the indicator or if the identifier is not properly terminated by a valid YAML delimiter.
        """  # noqa: E501
        start_mark = self.get_mark()
        indicator = self.peek()
        name = "alias" if indicator == "*" else "anchor"
        self.forward()
        length = 0
        ch = self.peek(length)
        while "0" <= ch <= "9" or "A" <= ch <= "Z" or "a" <= ch <= "z" or ch in "-_":
            length += 1
            ch = self.peek(length)
        if not length:
            raise ScannerError(  # noqa: TRY003
                f"while scanning an {name}",
                start_mark,
                f"expected alphabetic or numeric character, but found {ch!r}",
                self.get_mark(),
            )
        value = self.prefix(length)
        self.forward(length)
        ch = self.peek()
        if ch not in "\0 \t\r\n\x85\u2028\u2029?:,]}%@`":
            raise ScannerError(  # noqa: TRY003
                f"while scanning an {name}",
                start_mark,
                f"expected alphabetic or numeric character, but found {ch!r}",
                self.get_mark(),
            )
        end_mark = self.get_mark()
        return TokenClass(value, start_mark, end_mark)

    def scan_tag(self):  # noqa: ANN201
        # See the specification for details.
        """
        Parse a YAML tag at the current scanner position and return a TagToken.
        
        Supports the three YAML tag forms: `<...>`, the bare `!`, and `!handle!suffix`. Consumes the tag text, validates required delimiters and the required following spacing, and produces a TagToken whose `value` is a `(handle, suffix)` tuple.
        
        Returns:
            TagToken: A token whose `value` is `(handle, suffix)`. `handle` is the tag handle or `"!"` when no explicit handle is present; `suffix` is the tag URI or `"!"` for a bare `!` tag.
        
        Raises:
            ScannerError: If the tag is malformed (for example, missing closing `>` for a `<...>` tag or invalid termination).
        """  # noqa: E501
        start_mark = self.get_mark()
        ch = self.peek(1)
        if ch == "<":
            handle = None
            self.forward(2)
            suffix = self.scan_tag_uri("tag", start_mark)
            if self.peek() != ">":
                raise ScannerError(  # noqa: TRY003
                    "while parsing a tag",
                    start_mark,
                    f"expected '>', but found {self.peek()!r}",
                    self.get_mark(),
                )
            self.forward()
        elif ch in "\0 \t\r\n\x85\u2028\u2029":
            handle = None
            suffix = "!"
            self.forward()
        else:
            length = 1
            use_handle = False
            while ch not in "\0 \r\n\x85\u2028\u2029":
                if ch == "!":
                    use_handle = True
                    break
                length += 1
                ch = self.peek(length)
            handle = "!"
            if use_handle:
                handle = self.scan_tag_handle("tag", start_mark)
            else:
                handle = "!"
                self.forward()
            suffix = self.scan_tag_uri("tag", start_mark)
        ch = self.peek()
        if ch not in "\0 \r\n\x85\u2028\u2029":
            raise ScannerError(  # noqa: TRY003
                "while scanning a tag",
                start_mark,
                f"expected ' ', but found {ch!r}",
                self.get_mark(),
            )
        value = (handle, suffix)
        end_mark = self.get_mark()
        return TagToken(value, start_mark, end_mark)  # noqa: F405

    def scan_block_scalar(self, style):  # noqa: ANN001, ANN201
        # See the specification for details.
        """Parse a block scalar (literal `|` or folded `>`) starting at the current scanner position.

        Scans the block scalar header (chomping and optional indentation indicators), determines the effective indentation, collects the scalar content applying YAML's chomping and folding rules, and returns a scalar token representing the resulting value in the original block style.

        Parameters:
            style (str): The block scalar style character: `'|'` for literal or `'>'` for folded; controls whether folding is applied.

        Returns:
            ScalarToken: A token containing the parsed scalar value (with line breaks and folding applied according to the header and style), the original start/end marks, and the provided style.
        """  # noqa: E501
        folded = style == ">"

        chunks = []
        start_mark = self.get_mark()

        # Scan the header.
        self.forward()
        chomping, increment = self.scan_block_scalar_indicators(start_mark)
        self.scan_block_scalar_ignored_line(start_mark)

        # Determine the indentation level and go to the first non-empty line.
        min_indent = self.indent + 1
        min_indent = max(min_indent, 1)
        if increment is None:
            breaks, max_indent, end_mark = self.scan_block_scalar_indentation()
            indent = max(min_indent, max_indent)
        else:
            indent = min_indent + increment - 1
            breaks, end_mark = self.scan_block_scalar_breaks(indent)
        line_break = ""

        # Scan the inner part of the block scalar.
        while self.column == indent and self.peek() != "\0":
            chunks.extend(breaks)
            leading_non_space = self.peek() not in " \t"
            length = 0
            while self.peek(length) not in "\0\r\n\x85\u2028\u2029":
                length += 1
            chunks.append(self.prefix(length))
            self.forward(length)
            line_break = self.scan_line_break()
            breaks, end_mark = self.scan_block_scalar_breaks(indent)
            if self.column == indent and self.peek() != "\0":
                # Unfortunately, folding rules are ambiguous.
                #
                # This is the folding according to the specification:

                if (
                    folded
                    and line_break == "\n"
                    and leading_non_space
                    and self.peek() not in " \t"
                ):
                    if not breaks:
                        chunks.append(" ")
                else:
                    chunks.append(line_break)

                # This is Clark Evans's interpretation (also in the spec
                # examples):
                #
                # if folded and line_break == '\n':
                #    if not breaks:
                #        if self.peek() not in ' \t':
                #            chunks.append(' ')
                #        else:
                #            chunks.append(line_break)
                # else:
                #    chunks.append(line_break)
            else:
                break

        # Chomp the tail.
        if chomping is not False:
            chunks.append(line_break)
        if chomping is True:
            chunks.extend(breaks)

        # We are done.
        return ScalarToken("".join(chunks), False, start_mark, end_mark, style)  # noqa: F405

    def scan_block_scalar_indicators(self, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        """Parse optional chomping and indentation indicators from a block scalar header.

        Reads an optional chomping indicator ('+' for keep, '-' for strip) and/or a single-digit
        indentation indicator (1–9) starting at the current scanner position. Validates that any
        found indentation digit is between 1 and 9 and that the next character is a valid
        indicator terminator (whitespace, line break, or end-of-stream).

        Parameters:
            start_mark: Mark object representing the start position used to construct error messages.

        Returns:
            tuple: (chomping, increment)
                chomping (bool | None): `True` for '+', `False` for '-', or `None` if absent.
                increment (int | None): indentation indicator (1–9) or `None` if absent.

        Raises:
            ScannerError: if an indentation digit of '0' is encountered or if an unexpected
            non-terminating character follows the indicators.
        """  # noqa: E501, RUF002
        chomping = None
        increment = None
        ch = self.peek()
        if ch in "+-":
            chomping = ch == "+"
            self.forward()
            ch = self.peek()
            if ch in string.digits:
                increment = int(ch)
                if increment == 0:
                    raise ScannerError(  # noqa: TRY003
                        "while scanning a block scalar",
                        start_mark,
                        "expected indentation indicator in the range 1-9, but found 0",
                        self.get_mark(),
                    )
                self.forward()
        elif ch in string.digits:
            increment = int(ch)
            if increment == 0:
                raise ScannerError(  # noqa: TRY003
                    "while scanning a block scalar",
                    start_mark,
                    "expected indentation indicator in the range 1-9, but found 0",
                    self.get_mark(),
                )
            self.forward()
            ch = self.peek()
            if ch in "+-":
                chomping = ch == "+"
                self.forward()
        ch = self.peek()
        if ch not in "\0 \r\n\x85\u2028\u2029":
            raise ScannerError(  # noqa: TRY003
                "while scanning a block scalar",
                start_mark,
                f"expected chomping or indentation indicators, but found {ch!r}",
                self.get_mark(),
            )
        return chomping, increment

    def scan_block_scalar_ignored_line(self, start_mark) -> None:  # noqa: ANN001
        # See the specification for details.
        """Consume the remainder of a block scalar header line (optional spaces and comment), then consume its terminating line break.

        Skips any leading spaces, consumes an optional comment (characters up to the line break), and then requires a line break or end-of-stream. If neither a comment nor a line break is found, raises ScannerError using `start_mark` and the current scanner mark to report the error.

        Parameters:
            start_mark: Mark object representing the position where scanning of the block scalar began (used for error reporting).
        """  # noqa: E501
        while self.peek() == " ":
            self.forward()
        if self.peek() == "#":
            while self.peek() not in "\0\r\n\x85\u2028\u2029":
                self.forward()
        ch = self.peek()
        if ch not in "\0\r\n\x85\u2028\u2029":
            raise ScannerError(  # noqa: TRY003
                "while scanning a block scalar",
                start_mark,
                f"expected a comment or a line break, but found {ch!r}",
                self.get_mark(),
            )
        self.scan_line_break()

    def scan_block_scalar_indentation(self):  # noqa: ANN201
        # See the specification for details.
        """Determine the block scalar's indentation context by consuming leading spaces and line breaks.

        Scans from the current position while characters are spaces or line break characters. For each line break encountered, appends its normalized representation to `chunks` and updates `end_mark` to the current mark. For runs of spaces, updates `max_indent` with the maximum column reached (the deepest indentation seen). Scanning stops when a non-space, non-line-break character is reached.

        Returns:
            tuple: A 3-tuple (chunks, max_indent, end_mark) where
                - chunks (list[str]): Normalized line break strings encountered before the scalar content.
                - max_indent (int): The maximum column (indentation) observed among consumed space runs.
                - end_mark (Mark): The scanner mark at the end of the consumed sequence.
        """  # noqa: E501
        chunks = []
        max_indent = 0
        end_mark = self.get_mark()
        while self.peek() in " \r\n\x85\u2028\u2029":
            if self.peek() != " ":
                chunks.append(self.scan_line_break())
                end_mark = self.get_mark()
            else:
                self.forward()
                max_indent = max(max_indent, self.column)
        return chunks, max_indent, end_mark

    def scan_block_scalar_breaks(self, indent):  # noqa: ANN001, ANN201
        # See the specification for details.
        """Collect block-scalar line breaks and return them along with the final mark.

        This method consumes optional leading spaces (up to the given indentation) and then repeatedly
        consumes and normalizes successive line breaks, collecting each normalized break into a list.
        After each consumed break it again skips spaces up to `indent`. It stops when the next character
        is not a line break. The returned `end_mark` reflects the scanner position after the last consumed break
        or after the initial space-skipping when no breaks were found.

        Parameters:
            indent (int): Target column indentation; spaces are skipped only while the current column is less than this value.

        Returns:
            (list[str], Mark): A tuple where the first element is a list of normalized line break strings collected
            from the block scalar, and the second element is the mark representing the scanner position after the last consumed break.
        """  # noqa: E501
        chunks = []
        end_mark = self.get_mark()
        while self.column < indent and self.peek() == " ":
            self.forward()
        while self.peek() in "\r\n\x85\u2028\u2029":
            chunks.append(self.scan_line_break())
            end_mark = self.get_mark()
            while self.column < indent and self.peek() == " ":
                self.forward()
        return chunks, end_mark

    def scan_flow_scalar(self, style):  # noqa: ANN001, ANN201
        # See the specification for details.
        # Note that we loose indentation rules for quoted scalars. Quoted
        # scalars don't need to adhere indentation because " and ' clearly
        # mark the beginning and the end of them. Therefore we are less
        # restrictive then the specification requires. We only need to check
        # that document separators are not included in scalars.
        """Scan and return a quoted (flow) scalar token delimited by the given quote style.

        Parses a flow scalar enclosed by the provided quote character (either '"' or "'"), processing escapes, embedded spaces, and line breaks according to flow-scalar rules and returning the resulting scalar content (without surrounding quotes).

        Parameters:
            style (str): The quote character used for the scalar; expected values are '"' for double-quoted or "'" for single-quoted scalars.

        Returns:
            ScalarToken: A token containing the parsed scalar value (unquoted and with escapes handled), with its style set to the provided `style` and start/end marks set to the scalar's extent.
        """  # noqa: E501
        double = style == '"'
        chunks = []
        start_mark = self.get_mark()
        quote = self.peek()
        self.forward()
        chunks.extend(self.scan_flow_scalar_non_spaces(double, start_mark))
        while self.peek() != quote:
            chunks.extend(self.scan_flow_scalar_spaces(double, start_mark))
            chunks.extend(self.scan_flow_scalar_non_spaces(double, start_mark))
        self.forward()
        end_mark = self.get_mark()
        return ScalarToken("".join(chunks), False, start_mark, end_mark, style)  # noqa: F405

    ESCAPE_REPLACEMENTS = {  # noqa: RUF012
        "0": "\0",
        "a": "\x07",
        "b": "\x08",
        "t": "\x09",
        "\t": "\x09",
        "n": "\x0a",
        "v": "\x0b",
        "f": "\x0c",
        "r": "\x0d",
        "e": "\x1b",
        " ": "\x20",
        '"': '"',
        "\\": "\\",
        "/": "/",
        "N": "\x85",
        "_": "\xa0",
        "L": "\u2028",
        "P": "\u2029",
    }

    ESCAPE_CODES = {  # noqa: RUF012
        "x": 2,
        "u": 4,
        "U": 8,
    }

    def scan_flow_scalar_non_spaces(self, double, start_mark):  # noqa: ANN001, ANN201, PLR0912
        # See the specification for details.
        """Collects non-space segments and decoded escape sequences of a flow (quoted) scalar until a delimiter or boundary is reached.

        Parameters:
            double (bool): True when scanning a double-quoted scalar; enables backslash escape processing and hex/unicode escapes.
            start_mark (Mark): Mark representing the scalar start used in error messages.

        Returns:
            list[str]: A list of string chunks representing raw content and decoded escape sequences that together form the next non-space portion of the flow scalar.

        Raises:
            ScannerError: If a hex/unicode escape in a double-quoted scalar has an invalid length or contains non-hex digits, or if an unknown escape character is encountered while scanning a double-quoted scalar.
        """  # noqa: E501
        chunks = []
        while True:
            length = 0
            while self.peek(length) not in "'\"\\\0 \t\r\n\x85\u2028\u2029":
                length += 1
            if length:
                chunks.append(self.prefix(length))
                self.forward(length)
            ch = self.peek()
            if not double and ch == "'" and self.peek(1) == "'":
                chunks.append("'")
                self.forward(2)
            elif (double and ch == "'") or (not double and ch in '"\\'):
                chunks.append(ch)
                self.forward()
            elif double and ch == "\\":
                self.forward()
                ch = self.peek()
                if ch in self.ESCAPE_REPLACEMENTS:
                    chunks.append(self.ESCAPE_REPLACEMENTS[ch])
                    self.forward()
                elif ch in self.ESCAPE_CODES:
                    length = self.ESCAPE_CODES[ch]
                    self.forward()
                    for k in range(length):
                        if self.peek(k) not in "0123456789ABCDEFabcdef":
                            raise ScannerError(  # noqa: TRY003
                                "while scanning a double-quoted scalar",
                                start_mark,
                                "expected escape sequence of %d hexadecimal numbers, but found %r"  # noqa: E501, UP031
                                % (length, self.peek(k)),
                                self.get_mark(),
                            )
                    code = int(self.prefix(length), 16)
                    chunks.append(chr(code))
                    self.forward(length)
                elif ch in "\r\n\x85\u2028\u2029":
                    self.scan_line_break()
                    chunks.extend(self.scan_flow_scalar_breaks(double, start_mark))
                else:
                    raise ScannerError(  # noqa: TRY003
                        "while scanning a double-quoted scalar",
                        start_mark,
                        f"found unknown escape character {ch!r}",
                        self.get_mark(),
                    )
            else:
                return chunks

    def scan_flow_scalar_spaces(self, double, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        """Consume and return whitespace and normalized line-break fragments that follow non-space runs inside a flow (quoted) scalar.

        This function reads contiguous spaces and tabs, then either returns them as a single whitespace chunk or, if a line break follows, normalizes and returns the appropriate line-break and folded-break chunks produced by scan_line_break() and scan_flow_scalar_breaks().

        Parameters:
            double (bool): True when scanning a double-quoted scalar; passed through to break-scanning behavior.
            start_mark (Mark): Mark representing the start position of the scalar for error reporting.

        Returns:
            list[str]: A list of whitespace and/or line-break fragments to be appended to the scalar content.

        Raises:
            ScannerError: If the stream ends unexpectedly while scanning a quoted scalar.
        """  # noqa: E501
        chunks = []
        length = 0
        while self.peek(length) in " \t":
            length += 1
        whitespaces = self.prefix(length)
        self.forward(length)
        ch = self.peek()
        if ch == "\0":
            raise ScannerError(  # noqa: TRY003
                "while scanning a quoted scalar",
                start_mark,
                "found unexpected end of stream",
                self.get_mark(),
            )
        if ch in "\r\n\x85\u2028\u2029":
            line_break = self.scan_line_break()
            breaks = self.scan_flow_scalar_breaks(double, start_mark)
            if line_break != "\n":
                chunks.append(line_break)
            elif not breaks:
                chunks.append(" ")
            chunks.extend(breaks)
        else:
            chunks.append(whitespaces)
        return chunks

    def scan_flow_scalar_breaks(self, double, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        """Collects line-break chunks that occur inside a flow (quoted) scalar starting at the current position.

        Scans sequences of optional spaces/tabs and normalized line breaks, accumulating the normalized line-break chunks until a non-space/non-line-break character is encountered. If a document separator (`---` or `...`) is found at the current position (followed by a valid separator character), raises ScannerError with `start_mark` as the scalar start context.

        Parameters:
            double (bool): True if the scalar is double-quoted, False if single-quoted (affects caller semantics).
            start_mark (Mark): The start mark of the scalar used for error reporting.

        Returns:
            list: A list of collected line-break chunks (strings).
        """  # noqa: E501
        chunks = []
        while True:
            # Instead of checking indentation, we check for document
            # separators.
            prefix = self.prefix(3)
            if (prefix in {"---", "..."}) and self.peek(
                3
            ) in "\0 \t\r\n\x85\u2028\u2029":
                raise ScannerError(  # noqa: TRY003
                    "while scanning a quoted scalar",
                    start_mark,
                    "found unexpected document separator",
                    self.get_mark(),
                )
            while self.peek() in " \t":
                self.forward()
            if self.peek() in "\r\n\x85\u2028\u2029":
                chunks.append(self.scan_line_break())
            else:
                return chunks

    def scan_plain(self):  # noqa: ANN201
        # See the specification for details.
        # We add an additional restriction for the flow context:
        #   plain scalars in the flow context cannot contain ',' or '?'.
        # We also keep track of the `allow_simple_key` flag here.
        # Indentation rules are loosed for the flow context.
        """Scan a plain scalar from the current scanner position and produce its ScalarToken.

        Scans consecutive non-delimiter characters according to YAML plain-scalar rules, honoring block/flow context differences (notably stricter allowed characters in flow context), stopping at comments, line/indentation boundaries, or token-delimiting characters. Updates scanner simple-key allowance as part of scanning.

        Returns:
            ScalarToken: A plain-style scalar token containing the scanned string and its start/end marks.
        """  # noqa: E501
        chunks = []
        start_mark = self.get_mark()
        end_mark = start_mark
        indent = self.indent + 1
        # We allow zero indentation for scalars, but then we need to check for
        # document separators at the beginning of the line.
        # if indent == 0:
        #    indent = 1
        spaces = []
        while True:
            length = 0
            if self.peek() == "#":
                break
            while True:
                ch = self.peek(length)
                if (
                    ch in "\0 \t\r\n\x85\u2028\u2029"
                    or (
                        ch == ":"
                        and self.peek(length + 1)
                        in "\0 \t\r\n\x85\u2028\u2029"
                        + (",[]{}" if self.flow_level else "")
                    )
                    or (self.flow_level and ch in ",?[]{}")
                ):
                    break
                length += 1
            if length == 0:
                break
            self.allow_simple_key = False
            chunks.extend(spaces)
            chunks.append(self.prefix(length))
            self.forward(length)
            end_mark = self.get_mark()
            spaces = self.scan_plain_spaces(indent, start_mark)
            if (
                not spaces
                or self.peek() == "#"
                or (not self.flow_level and self.column < indent)
            ):
                break
        return ScalarToken("".join(chunks), True, start_mark, end_mark)  # noqa: F405

    def scan_plain_spaces(self, indent, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        # The specification is really confusing about tabs in plain scalars.
        # We just forbid them completely. Do not use tabs in YAML!
        """
        Interpret spaces following a plain scalar and produce whitespace and normalized line-break chunks, or signal that the plain scalar must terminate.
        
        Parses contiguous spaces after the scalar, then handles an optional following line break sequence. If a document separator (`---` or `...`) is encountered at the start of the next non-space content, the scalar is considered terminated and the function returns `None`.
        
        Parameters:
            indent (int): Column used to decide whether the scalar may continue across a line break.
            start_mark: Marker for the start of the scalar used for contextual error reporting.
        
        Returns:
            list: A list of string chunks representing consumed spaces and/or normalized line breaks, or
            `None` if the plain scalar must end (for example when a document separator follows).
        """  # noqa: E501
        chunks = []
        length = 0
        while self.peek(length) == " ":
            length += 1
        whitespaces = self.prefix(length)
        self.forward(length)
        ch = self.peek()
        if ch in "\r\n\x85\u2028\u2029":
            line_break = self.scan_line_break()
            self.allow_simple_key = True
            prefix = self.prefix(3)
            if (prefix in {"---", "..."}) and self.peek(
                3
            ) in "\0 \t\r\n\x85\u2028\u2029":
                return None
            breaks = []
            while self.peek() in " \r\n\x85\u2028\u2029":
                if self.peek() == " ":
                    self.forward()
                else:
                    breaks.append(self.scan_line_break())
                    prefix = self.prefix(3)
                    if (prefix in {"---", "..."}) and self.peek(
                        3
                    ) in "\0 \t\r\n\x85\u2028\u2029":
                        return None
            if line_break != "\n":
                chunks.append(line_break)
            elif not breaks:
                chunks.append(" ")
            chunks.extend(breaks)
        elif whitespaces:
            chunks.append(whitespaces)
        return chunks

    def scan_tag_handle(self, name, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        # For some strange reasons, the specification does not allow '_' in
        # tag handles. I have allowed it anyway.
        """
        Parse a tag handle from the current scanner position and return the consumed text.
        
        Parameters:
            name (str): Context name used in error messages (for example, "tag directive").
            start_mark (Mark): Start mark used for error reporting.
        
        Returns:
            str: The consumed tag handle text, including the leading `!` and the trailing `!` when present.
        
        Raises:
            ScannerError: If the current character is not `!`, or if a handle begins but is not terminated by a closing `!`.
        """  # noqa: E501
        ch = self.peek()
        if ch != "!":
            raise ScannerError(  # noqa: TRY003
                f"while scanning a {name}",
                start_mark,
                f"expected '!', but found {ch!r}",
                self.get_mark(),
            )
        length = 1
        ch = self.peek(length)
        if ch != " ":
            while (
                "0" <= ch <= "9" or "A" <= ch <= "Z" or "a" <= ch <= "z" or ch in "-_"
            ):
                length += 1
                ch = self.peek(length)
            if ch != "!":
                self.forward(length)
                raise ScannerError(  # noqa: TRY003
                    f"while scanning a {name}",
                    start_mark,
                    f"expected '!', but found {ch!r}",
                    self.get_mark(),
                )
            length += 1
        value = self.prefix(length)
        self.forward(length)
        return value

    def scan_tag_uri(self, name, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        # Note: we do not check if URI is well-formed.
        """Parse a tag URI at the current scanner position and return the accumulated URI string.

        Parses a sequence of URI characters (alphanumerics and permitted punctuation), decoding any percent-encoded escapes encountered. Used for tag URI parsing; does not validate overall URI semantics.

        Parameters:
            name (str): Context name for error messages (e.g., "tag").
            start_mark (Mark): Mark used as the start position in error reports.

        Returns:
            str: The parsed URI with percent-escapes decoded.

        Raises:
            ScannerError: If no valid URI content is found at the current position.
        """  # noqa: E501
        chunks = []
        length = 0
        ch = self.peek(length)
        while (
            "0" <= ch <= "9"
            or "A" <= ch <= "Z"
            or "a" <= ch <= "z"
            or ch in "-;/?:@&=+$,_.!~*'()[]%"
        ):
            if ch == "%":
                chunks.append(self.prefix(length))
                self.forward(length)
                length = 0
                chunks.append(self.scan_uri_escapes(name, start_mark))
            else:
                length += 1
            ch = self.peek(length)
        if length:
            chunks.append(self.prefix(length))
            self.forward(length)
            length = 0
        if not chunks:
            raise ScannerError(  # noqa: TRY003
                f"while parsing a {name}",
                start_mark,
                f"expected URI, but found {ch!r}",
                self.get_mark(),
            )
        return "".join(chunks)

    def scan_uri_escapes(self, name, start_mark):  # noqa: ANN001, ANN201
        # See the specification for details.
        """Decode consecutive percent-encoded (`%HH`) sequences at the current scanner position and return the resulting UTF-8 string.

        Parameters:
            name (str): Context name used in error messages (e.g., "tag" or "directive").
            start_mark (Mark): Start mark to use when reporting scan errors.

        Returns:
            str: The Unicode string produced by decoding the sequence of percent-encoded bytes.

        Raises:
            ScannerError: If an escape sequence does not contain two hexadecimal digits or if the decoded bytes are not valid UTF-8.
        """  # noqa: E501
        codes = []
        mark = self.get_mark()
        while self.peek() == "%":
            self.forward()
            for k in range(2):
                if self.peek(k) not in "0123456789ABCDEFabcdef":
                    raise ScannerError(  # noqa: TRY003
                        f"while scanning a {name}",
                        start_mark,
                        f"expected URI escape sequence of 2 hexadecimal numbers, but found {self.peek(k)!r}",  # noqa: E501
                        self.get_mark(),
                    )
            codes.append(int(self.prefix(2), 16))
            self.forward(2)
        try:
            value = bytes(codes).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ScannerError(f"while scanning a {name}", start_mark, str(exc), mark)  # noqa: B904, TRY003
        return value

    def scan_line_break(self):  # noqa: ANN201
        # Transforms:
        #   '\r\n'      :   '\n'
        #   '\r'        :   '\n'
        #   '\n'        :   '\n'
        #   '\x85'      :   '\n'
        #   '\u2028'    :   '\u2028'
        #   '\u2029     :   '\u2029'
        #   default     :   ''
        r"""Normalize and consume a line break at the current scanner position.

        Consumes one or two input characters that form a YAML-recognized line break and returns the normalized line-break sequence. CR+LF, CR, LF, and NEL (U+0085) are normalized to '\n'. Unicode line separators U+2028 and U+2029 are returned unchanged. If no line break is present, nothing is consumed and an empty string is returned.

        Returns:
            str: The normalized line break: '\n' for CR/LF/CRLF/NEL, U+2028 or U+2029 when present, or '' if no line break was found.
        """  # noqa: E501
        ch = self.peek()
        if ch in "\r\n\x85":
            if self.prefix(2) == "\r\n":
                self.forward(2)
            else:
                self.forward()
            return "\n"
        if ch in "\u2028\u2029":
            self.forward()
            return ch
        return ""
