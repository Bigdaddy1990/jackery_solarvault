__all__ = [  # noqa: D100
    "BaseConstructor",
    "Constructor",
    "ConstructorError",
    "FullConstructor",
    "SafeConstructor",
    "UnsafeConstructor",
]

import base64
import binascii
import collections.abc
import datetime
import re
import sys
import types
from typing import Never

from .error import *  # noqa: F403
from .nodes import *  # noqa: F403


class ConstructorError(MarkedYAMLError):  # noqa: D101, F405
    pass


class BaseConstructor:  # noqa: D101
    yaml_constructors = {}  # noqa: RUF012
    yaml_multi_constructors = {}  # noqa: RUF012

    def __init__(self) -> None:
        """
        Set up internal state used during YAML node construction and recursion detection.
        
        Attributes:
            constructed_objects (dict): Maps YAML node objects to their constructed Python objects.
            recursive_objects (dict): Tracks nodes currently being constructed to detect unconstructable recursion.
            state_generators (list): Queue of generator objects for deferred or incremental construction.
            deep_construct (bool): When True, generator-based constructors are exhausted immediately; when False, they may be deferred.
        """
        self.constructed_objects = {}
        self.recursive_objects = {}
        self.state_generators = []
        self.deep_construct = False

    def check_data(self):  # noqa: ANN201
        # If there are more documents available?
        """Determine whether another document node is available for construction.

        Returns:
            `True` if a node/document is available for construction, `False` otherwise.
        """
        return self.check_node()

    def check_state_key(self, key) -> None:  # noqa: ANN001
        """Prevent setting blacklisted attribute names on a deserialized object.

        Parameters:
            key (str): Attribute name from the incoming state being validated.

        Raises:
            ConstructorError: If `key` matches the constructor's blacklist for state keys.
        """
        if self.get_state_keys_blacklist_regexp().match(key):
            raise ConstructorError(
                None,
                None,
                f"blacklisted key '{key}' in instance state found",
                None,
            )

    def get_data(self):  # noqa: ANN201
        # Construct and return the next document.
        """Constructs and returns the next document from the node stream.

        Returns:
            The constructed document object, or None if no node is available.
        """
        if self.check_node():
            return self.construct_document(self.get_node())
        return None

    def get_single_data(self):  # noqa: ANN201
        # Ensure that the stream contains a single document and construct it.
        """Construct and return the single YAML document from the stream, if present.

        If the stream contains a document, constructs and returns its Python representation;
        if the stream is empty, returns `None`.

        Returns:
            The constructed Python object for the single document, or `None` if no document is present.
        """
        node = self.get_single_node()
        if node is not None:
            return self.construct_document(node)
        return None

    def construct_document(self, node):  # noqa: ANN001, ANN201
        """
        Construct a Python representation of a YAML document from the given root node.
        
        Finalizes any queued generator-based constructors before returning and resets the
        constructor's transient state (constructed_objects, recursive_objects, deep_construct).
        
        Parameters:
            node: Root YAML node to be constructed.
        
        Returns:
            The fully constructed Python object representing the document.
        """
        data = self.construct_object(node)
        while self.state_generators:
            state_generators = self.state_generators
            self.state_generators = []
            for generator in state_generators:
                for _dummy in generator:
                    pass
        self.constructed_objects = {}
        self.recursive_objects = {}
        self.deep_construct = False
        return data

    def construct_object(self, node, deep=False):  # noqa: ANN001, ANN201, PLR0912
        """Construct a Python object from a YAML node, caching the result and detecting recursion.

        Selects and invokes the appropriate constructor for the node's tag, caches the constructed value so subsequent constructions of the same node return the cached object, and supports constructor generators by advancing them to their first yield (generators may be exhausted immediately when `deep` is True or queued for later completion).

        Parameters:
            node: The YAML node to construct.
            deep (bool): If True, force full construction of nested values for this call.

        Returns:
            The Python object constructed from `node`.

        Raises:
            ConstructorError: If a recursive node is detected that cannot be constructed (construction already in progress).
        """
        if node in self.constructed_objects:
            return self.constructed_objects[node]
        if deep:
            old_deep = self.deep_construct
            self.deep_construct = True
        if node in self.recursive_objects:
            raise ConstructorError(
                None, None, "found unconstructable recursive node", node.start_mark
            )
        self.recursive_objects[node] = None
        constructor = None
        tag_suffix = None
        if node.tag in self.yaml_constructors:
            constructor = self.yaml_constructors[node.tag]
        else:
            for tag_prefix in self.yaml_multi_constructors:
                if tag_prefix is not None and node.tag.startswith(tag_prefix):
                    tag_suffix = node.tag[len(tag_prefix) :]
                    constructor = self.yaml_multi_constructors[tag_prefix]
                    break
            else:
                if None in self.yaml_multi_constructors:
                    tag_suffix = node.tag
                    constructor = self.yaml_multi_constructors[None]
                elif None in self.yaml_constructors:
                    constructor = self.yaml_constructors[None]
                elif isinstance(node, ScalarNode):  # noqa: F405
                    constructor = self.__class__.construct_scalar
                elif isinstance(node, SequenceNode):  # noqa: F405
                    constructor = self.__class__.construct_sequence
                elif isinstance(node, MappingNode):  # noqa: F405
                    constructor = self.__class__.construct_mapping
        if tag_suffix is None:
            data = constructor(self, node)
        else:
            data = constructor(self, tag_suffix, node)
        if isinstance(data, types.GeneratorType):
            generator = data
            data = next(generator)
            if self.deep_construct:
                for _dummy in generator:
                    pass
            else:
                self.state_generators.append(generator)
        self.constructed_objects[node] = data
        del self.recursive_objects[node]
        if deep:
            self.deep_construct = old_deep
        return data

    def construct_scalar(self, node):  # noqa: ANN001, ANN201, PLR6301
        """Constructs a Python value from a YAML scalar node.

        Parameters:
            node (ScalarNode): The YAML scalar node to construct.

        Returns:
            The scalar node's value.

        Raises:
            ConstructorError: If `node` is not a `ScalarNode`.
        """
        if not isinstance(node, ScalarNode):  # noqa: F405
            raise ConstructorError(
                None,
                None,
                f"expected a scalar node, but found {node.id}",
                node.start_mark,
            )
        return node.value

    def construct_sequence(self, node, deep=False):  # noqa: ANN001, ANN201
        """Constructs a Python list from a YAML sequence node.

        Parameters:
            node (SequenceNode): The YAML sequence node to construct.
            deep (bool): If true, force deep construction of child nodes.

        Returns:
            list: A list containing the constructed values of the node's children.

        Raises:
            ConstructorError: If `node` is not a SequenceNode.
        """
        if not isinstance(node, SequenceNode):  # noqa: F405
            raise ConstructorError(
                None,
                None,
                f"expected a sequence node, but found {node.id}",
                node.start_mark,
            )
        return [self.construct_object(child, deep=deep) for child in node.value]

    def construct_mapping(self, node, deep=False):  # noqa: ANN001, ANN201
        """Construct a Python dict from a YAML mapping node.

        Parameters:
            node (MappingNode): The YAML mapping node to construct.
            deep (bool): If True, construct keys and values with deep construction semantics (used for object state).

        Returns:
            dict: A mapping of constructed keys to constructed values.

        Raises:
            ConstructorError: If `node` is not a MappingNode or if a constructed key is not hashable.
        """
        if not isinstance(node, MappingNode):  # noqa: F405
            raise ConstructorError(
                None,
                None,
                f"expected a mapping node, but found {node.id}",
                node.start_mark,
            )
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, collections.abc.Hashable):
                raise ConstructorError(  # noqa: TRY003
                    "while constructing a mapping",
                    node.start_mark,
                    "found unhashable key",
                    key_node.start_mark,
                )
            value = self.construct_object(value_node, deep=deep)
            mapping[key] = value
        return mapping

    def construct_pairs(self, node, deep=False):  # noqa: ANN001, ANN201
        """
        Construct a list of (key, value) tuples from a mapping node's entries.
        
        Parameters:
            node (MappingNode): Mapping node whose entries will be converted into pairs.
            deep (bool): If true, construct keys and values with deep construction.
        
        Returns:
            list: A list of (key, value) tuples constructed from the mapping's entries.
        
        Raises:
            ConstructorError: If `node` is not a MappingNode.
        """
        if not isinstance(node, MappingNode):  # noqa: F405
            raise ConstructorError(
                None,
                None,
                f"expected a mapping node, but found {node.id}",
                node.start_mark,
            )
        pairs = []
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            value = self.construct_object(value_node, deep=deep)
            pairs.append((key, value))
        return pairs

    @classmethod
    def add_constructor(cls, tag, constructor) -> None:  # noqa: ANN001
        """
        Register a constructor callable for an exact YAML tag on the class.
        
        Adds `constructor` to the class-level `yaml_constructors` mapping under `tag`. If the class inherited `yaml_constructors` from a base class, the mapping is shallow-copied first to avoid mutating the parent class's registry.
        
        Parameters:
            cls (type): Class on which to register the constructor.
            tag (str): Exact YAML tag to register (e.g., 'tag:yaml.org,2002:str').
            constructor (callable): Callable used to construct Python objects for nodes with the given tag.
        """
        if "yaml_constructors" not in cls.__dict__:
            cls.yaml_constructors = cls.yaml_constructors.copy()
        cls.yaml_constructors[tag] = constructor

    @classmethod
    def add_multi_constructor(cls, tag_prefix, multi_constructor) -> None:  # noqa: ANN001
        """Register a multi-constructor for a YAML tag prefix on the class.

        If the class inherits `yaml_multi_constructors`, a shallow copy is made before modification to avoid mutating the parent class's mapping. Associates `tag_prefix` (use `None` for the fallback) with `multi_constructor`.

        Parameters:
            tag_prefix (str | None): Tag prefix to register; use `None` to provide a fallback constructor.
            multi_constructor (callable): Constructor callable invoked for tags that start with `tag_prefix`.
        """
        if "yaml_multi_constructors" not in cls.__dict__:
            cls.yaml_multi_constructors = cls.yaml_multi_constructors.copy()
        cls.yaml_multi_constructors[tag_prefix] = multi_constructor


class SafeConstructor(BaseConstructor):  # noqa: D101
    def construct_scalar(self, node):  # noqa: ANN001, ANN201
        """
        Get the scalar value represented by a YAML node, using a mapping entry tagged `tag:yaml.org,2002:value` when present.
        
        If `node` is a MappingNode that contains an entry whose key has the tag `tag:yaml.org,2002:value`, the value of that entry is used as the constructed scalar. Otherwise the node is handled by the superclass scalar constructor.
        
        Parameters:
            node: The YAML node to construct from; typically a ScalarNode or a MappingNode containing a `tag:yaml.org,2002:value` entry.
        
        Returns:
            The constructed scalar value for `node`.
        """
        if isinstance(node, MappingNode):  # noqa: F405
            for key_node, value_node in node.value:
                if key_node.tag == "tag:yaml.org,2002:value":
                    return self.construct_scalar(value_node)
        return super().construct_scalar(node)

    def flatten_mapping(self, node) -> None:  # noqa: ANN001
        """Process YAML merge keys (<<) in a mapping node, expanding and inlining referenced mappings.

        This modifies `node.value` in-place: it locates entries with tag `tag:yaml.org,2002:merge` and replaces them by the mappings they reference. If a merge value is a single mapping, that mapping's key/value pairs are flattened and prepended. If a merge value is a sequence, each element must be a mapping; their pairs are flattened in sequence order (later entries override earlier ones). Entries with tag `tag:yaml.org,2002:value` are retagged to `tag:yaml.org,2002:str`.

        Parameters:
            node (MappingNode): The mapping node whose `value` (list of key/value node pairs) will be flattened for YAML merges.

        Raises:
            ConstructorError: If a merge value is neither a mapping nor a sequence of mappings, or if an element of a merge sequence is not a mapping.
        """
        merge = []
        index = 0
        while index < len(node.value):
            key_node, value_node = node.value[index]
            if key_node.tag == "tag:yaml.org,2002:merge":
                del node.value[index]
                if isinstance(value_node, MappingNode):  # noqa: F405
                    self.flatten_mapping(value_node)
                    merge.extend(value_node.value)
                elif isinstance(value_node, SequenceNode):  # noqa: F405
                    submerge = []
                    for subnode in value_node.value:
                        if not isinstance(subnode, MappingNode):  # noqa: F405
                            raise ConstructorError(  # noqa: TRY003
                                "while constructing a mapping",
                                node.start_mark,
                                f"expected a mapping for merging, but found {subnode.id}",
                                subnode.start_mark,
                            )
                        self.flatten_mapping(subnode)
                        submerge.append(subnode.value)
                    submerge.reverse()
                    for value in submerge:
                        merge.extend(value)
                else:
                    raise ConstructorError(  # noqa: TRY003
                        "while constructing a mapping",
                        node.start_mark,
                        f"expected a mapping or list of mappings for merging, but found {value_node.id}",
                        value_node.start_mark,
                    )
            elif key_node.tag == "tag:yaml.org,2002:value":
                key_node.tag = "tag:yaml.org,2002:str"
                index += 1
            else:
                index += 1
        if merge:
            node.value = merge + node.value

    def construct_mapping(self, node, deep=False):  # noqa: ANN001, ANN201
        """Flatten YAML merge keys on `node` (if it's a mapping) and construct a Python dict from the mapping.

        Parameters:
            node: The mapping node to construct; if tagged with YAML merge keys (`<<`), those merges will be resolved before construction.
            deep (bool): If True, construct child nodes deeply (resolve any deferred generator-based construction immediately).

        Returns:
            dict: The constructed mapping with keys and values produced from the node's entries.

        Raises:
            ConstructorError: If `node` is not a mapping node, contains unhashable keys, or otherwise cannot be constructed.
        """
        if isinstance(node, MappingNode):  # noqa: F405
            self.flatten_mapping(node)
        return super().construct_mapping(node, deep=deep)

    def construct_yaml_null(self, node) -> None:  # noqa: ANN001
        """Produce Python None for a YAML null node.

        This constructor maps a YAML null scalar to Python's None; the node's scalar value is ignored.
        """
        self.construct_scalar(node)

    bool_values = {  # noqa: RUF012
        "yes": True,
        "no": False,
        "true": True,
        "false": False,
        "on": True,
        "off": False,
    }

    def construct_yaml_bool(self, node):  # noqa: ANN001, ANN201
        """
        Convert a YAML boolean scalar node to a Python bool.
        
        Parameters:
            node (ScalarNode): YAML scalar node containing a boolean lexical value (e.g., "yes", "No").
        
        Returns:
            `true` if the YAML value represents boolean true, `false` otherwise.
        """
        value = self.construct_scalar(node)
        return self.bool_values[value.lower()]

    def construct_yaml_int(self, node):  # noqa: ANN001, ANN201
        """
        Parse an integer from a YAML scalar supporting sign, underscores, binary (0b), hexadecimal (0x), octal (leading zero), and sexagesimal (colon-separated) formats.
        
        Parameters:
            node (ScalarNode): YAML scalar node containing the textual integer representation.
        
        Returns:
            int: The parsed integer value.
        """
        value = self.construct_scalar(node)
        value = value.replace("_", "")
        sign = +1
        if value[0] == "-":
            sign = -1
        if value[0] in "+-":
            value = value[1:]
        if value == "0":
            return 0
        if value.startswith(("0b", "0x")):
            return sign * int(value, 0)
        if value[0] == "0":
            return sign * int(value, 8)
        if ":" in value:
            digits = [int(part) for part in value.split(":")]
            digits.reverse()
            base = 1
            value = 0
            for digit in digits:
                value += digit * base
                base *= 60
            return sign * value
        return sign * int(value)

    inf_value = 1e300
    while inf_value != inf_value * inf_value:
        inf_value *= inf_value
    nan_value = -inf_value / inf_value  # Trying to make a quiet NaN (like C99).

    def construct_yaml_float(self, node):  # noqa: ANN001, ANN201
        """
        Parse a YAML scalar node into a Python float.
        
        Parameters:
            node: YAML ScalarNode containing the textual float representation.
        
        Returns:
            A float parsed from the scalar. The strings `+.inf`/`-.inf` map to `self.inf_value` with sign applied, `.nan` maps to `self.nan_value`, and sexagesimal values containing `:` are interpreted as base-60 components (for example, `1:2:3` -> 1*3600 + 2*60 + 3).
        """
        value = self.construct_scalar(node)
        value = value.replace("_", "").lower()
        sign = +1
        if value[0] == "-":
            sign = -1
        if value[0] in "+-":
            value = value[1:]
        if value == ".inf":
            return sign * self.inf_value
        if value == ".nan":
            return self.nan_value
        if ":" in value:
            digits = [float(part) for part in value.split(":")]
            digits.reverse()
            base = 1
            value = 0.0
            for digit in digits:
                value += digit * base
                base *= 60
            return sign * value
        return sign * float(value)

    def construct_yaml_binary(self, node):  # noqa: ANN001, ANN201
        """Decode a base64-encoded YAML scalar into raw bytes.

        Parameters:
            node (ScalarNode): YAML scalar node containing base64-encoded ASCII data.

        Returns:
            bytes: Decoded binary data.

        Raises:
            ConstructorError: If the scalar cannot be encoded to ASCII or if base64 decoding fails.
        """
        try:
            value = self.construct_scalar(node).encode("ascii")
        except UnicodeEncodeError as exc:
            raise ConstructorError(  # noqa: B904
                None,
                None,
                f"failed to convert base64 data into ascii: {exc}",
                node.start_mark,
            )
        try:
            return base64.decodebytes(value)
        except binascii.Error as exc:
            raise ConstructorError(  # noqa: B904
                None, None, f"failed to decode base64 data: {exc}", node.start_mark
            )

    timestamp_regexp = re.compile(
        r"""^(?P<year>[0-9][0-9][0-9][0-9])
                -(?P<month>[0-9][0-9]?)
                -(?P<day>[0-9][0-9]?)
                (?:(?:[Tt]|[ \t]+)
                (?P<hour>[0-9][0-9]?)
                :(?P<minute>[0-9][0-9])
                :(?P<second>[0-9][0-9])
                (?:\.(?P<fraction>[0-9]*))?
                (?:[ \t]*(?P<tz>Z|(?P<tz_sign>[-+])(?P<tz_hour>[0-9][0-9]?)
                (?::(?P<tz_minute>[0-9][0-9]))?))?)?$""",
        re.VERBOSE,
    )

    def construct_yaml_timestamp(self, node):  # noqa: ANN001, ANN201
        """
        Convert a YAML timestamp scalar to a Python date or datetime.
        
        If the scalar contains only a date (YYYY-MM-DD) returns a datetime.date. If the scalar includes time, returns a datetime.datetime with hour, minute, second, microsecond (fractional seconds truncated or padded to six digits) and tzinfo set for a timezone offset or UTC.
        
        Parameters:
            node: A YAML scalar node containing an ISO-like timestamp string.
        
        Returns:
            `datetime.date` when the scalar contains only a date; otherwise `datetime.datetime` with parsed time, microseconds, and `tzinfo` for timezone offsets or UTC.
        """
        self.construct_scalar(node)
        match = self.timestamp_regexp.match(node.value)
        values = match.groupdict()
        year = int(values["year"])
        month = int(values["month"])
        day = int(values["day"])
        if not values["hour"]:
            return datetime.date(year, month, day)
        hour = int(values["hour"])
        minute = int(values["minute"])
        second = int(values["second"])
        fraction = 0
        tzinfo = None
        if values["fraction"]:
            fraction = values["fraction"][:6]
            while len(fraction) < 6:  # noqa: PLR2004
                fraction += "0"
            fraction = int(fraction)
        if values["tz_sign"]:
            tz_hour = int(values["tz_hour"])
            tz_minute = int(values["tz_minute"] or 0)
            delta = datetime.timedelta(hours=tz_hour, minutes=tz_minute)
            if values["tz_sign"] == "-":
                delta = -delta
            tzinfo = datetime.timezone(delta)
        elif values["tz"]:
            tzinfo = datetime.UTC
        return datetime.datetime(
            year, month, day, hour, minute, second, fraction, tzinfo=tzinfo
        )

    def construct_yaml_omap(self, node):  # noqa: ANN001, ANN201
        # Note: we do not check for duplicate keys, because it's too
        # CPU-expensive.
        """
        Construct an ordered mapping as a list of (key, value) pairs from a YAML sequence node.
        
        Parameters:
            node (SequenceNode): A sequence whose elements must each be a single-item MappingNode representing one key/value pair.
        
        Returns:
            omap (list): A list of (key, value) tuples preserving the order from the sequence.
        
        Raises:
            ConstructorError: If `node` is not a SequenceNode, if any element is not a MappingNode, or if any mapping element does not contain exactly one item.
        """
        omap = []
        yield omap
        if not isinstance(node, SequenceNode):  # noqa: F405
            raise ConstructorError(  # noqa: TRY003
                "while constructing an ordered map",
                node.start_mark,
                f"expected a sequence, but found {node.id}",
                node.start_mark,
            )
        for subnode in node.value:
            if not isinstance(subnode, MappingNode):  # noqa: F405
                raise ConstructorError(  # noqa: TRY003
                    "while constructing an ordered map",
                    node.start_mark,
                    f"expected a mapping of length 1, but found {subnode.id}",
                    subnode.start_mark,
                )
            if len(subnode.value) != 1:
                raise ConstructorError(  # noqa: TRY003
                    "while constructing an ordered map",
                    node.start_mark,
                    "expected a single mapping item, but found %d items"  # noqa: UP031
                    % len(subnode.value),
                    subnode.start_mark,
                )
            key_node, value_node = subnode.value[0]
            key = self.construct_object(key_node)
            value = self.construct_object(value_node)
            omap.append((key, value))

    def construct_yaml_pairs(self, node):  # noqa: ANN001, ANN201
        # Note: the same code as `construct_yaml_omap`.
        """Builds a list of (key, value) pairs from a YAML sequence whose elements are single-item mappings.

        Parameters:
            node: SequenceNode — a YAML sequence where each element must be a MappingNode containing exactly one key/value pair.

        Returns:
            pairs (list): A list of (key, value) tuples constructed from the sequence elements.

        Raises:
            ConstructorError: If `node` is not a sequence, if an element is not a mapping, or if a mapping element does not contain exactly one item.
        """
        pairs = []
        yield pairs
        if not isinstance(node, SequenceNode):  # noqa: F405
            raise ConstructorError(  # noqa: TRY003
                "while constructing pairs",
                node.start_mark,
                f"expected a sequence, but found {node.id}",
                node.start_mark,
            )
        for subnode in node.value:
            if not isinstance(subnode, MappingNode):  # noqa: F405
                raise ConstructorError(  # noqa: TRY003
                    "while constructing pairs",
                    node.start_mark,
                    f"expected a mapping of length 1, but found {subnode.id}",
                    subnode.start_mark,
                )
            if len(subnode.value) != 1:
                raise ConstructorError(  # noqa: TRY003
                    "while constructing pairs",
                    node.start_mark,
                    "expected a single mapping item, but found %d items"  # noqa: UP031
                    % len(subnode.value),
                    subnode.start_mark,
                )
            key_node, value_node = subnode.value[0]
            key = self.construct_object(key_node)
            value = self.construct_object(value_node)
            pairs.append((key, value))

    def construct_yaml_set(self, node):  # noqa: ANN001, ANN201
        """Construct a Python set from a YAML mapping node, yielding an empty set first to support recursive references.

        Yields an empty set immediately so other nodes can reference it during construction, then populates and returns the set with keys constructed from the mapping node.

        Parameters:
            node: The YAML MappingNode whose keys represent set members.

        Returns:
            set: A Python set containing the constructed keys from the mapping node.
        """
        data = set()
        yield data
        value = self.construct_mapping(node)
        data.update(value)

    def construct_yaml_str(self, node):  # noqa: ANN001, ANN201
        """Constructs a Python string from a YAML scalar node.

        Parameters:
            node (ScalarNode): YAML scalar node to construct.

        Returns:
            str: The constructed Python string.
        """
        return self.construct_scalar(node)

    def construct_yaml_seq(self, node):  # noqa: ANN001, ANN201
        """Construct a YAML sequence node into a Python list, yielding an initially empty list to support recursive references.

        Returns:
            list: The constructed list; yielded empty before population so recursive structures can reference it during construction.
        """
        data = []
        yield data
        data.extend(self.construct_sequence(node))

    def construct_yaml_map(self, node):  # noqa: ANN001, ANN201
        """Construct a Python dict from a YAML mapping node, yielding an initially empty dict to support recursive references.

        Parameters:
            node (MappingNode): The YAML mapping node to construct.

        Returns:
            dict: A dictionary populated with constructed key/value pairs from the mapping node.
        """
        data = {}
        yield data
        value = self.construct_mapping(node)
        data.update(value)

    def construct_yaml_object(self, node, cls):  # noqa: ANN001, ANN201
        """Create an uninitialized instance of `cls` from a mapping node and populate its state.

        Yields the newly allocated instance so callers can obtain a reference before its contents are filled. After the yield, constructs a mapping from `node` and applies it to the instance: if the instance implements `__setstate__`, the constructed mapping is passed to `__setstate__` (constructed with deep construction); otherwise the mapping is used to update the instance's `__dict__`.

        Parameters:
            node: The mapping node containing the serialized state.
            cls: The class whose uninitialized instance will be created.

        Returns:
            data: The newly allocated instance (yielded before population).
        """
        data = cls.__new__(cls)
        yield data
        if hasattr(data, "__setstate__"):
            state = self.construct_mapping(node, deep=True)
            data.__setstate__(state)
        else:
            state = self.construct_mapping(node)
            data.__dict__.update(state)

    def construct_undefined(self, node) -> Never:  # noqa: ANN001, PLR6301
        """Raise a ConstructorError indicating no registered constructor exists for the given YAML node tag.

        Parameters:
            node: The YAML node whose tag could not be resolved to a constructor. The error includes the node's start mark.

        Raises:
            ConstructorError: Always raised to signal that no constructor is available for `node.tag`.
        """
        raise ConstructorError(
            None,
            None,
            f"could not determine a constructor for the tag {node.tag!r}",
            node.start_mark,
        )


SafeConstructor.add_constructor(
    "tag:yaml.org,2002:null", SafeConstructor.construct_yaml_null
)

SafeConstructor.add_constructor(
    "tag:yaml.org,2002:bool", SafeConstructor.construct_yaml_bool
)

SafeConstructor.add_constructor(
    "tag:yaml.org,2002:int", SafeConstructor.construct_yaml_int
)

SafeConstructor.add_constructor(
    "tag:yaml.org,2002:float", SafeConstructor.construct_yaml_float
)

SafeConstructor.add_constructor(
    "tag:yaml.org,2002:binary", SafeConstructor.construct_yaml_binary
)

SafeConstructor.add_constructor(
    "tag:yaml.org,2002:timestamp", SafeConstructor.construct_yaml_timestamp
)

SafeConstructor.add_constructor(
    "tag:yaml.org,2002:omap", SafeConstructor.construct_yaml_omap
)

SafeConstructor.add_constructor(
    "tag:yaml.org,2002:pairs", SafeConstructor.construct_yaml_pairs
)

SafeConstructor.add_constructor(
    "tag:yaml.org,2002:set", SafeConstructor.construct_yaml_set
)

SafeConstructor.add_constructor(
    "tag:yaml.org,2002:str", SafeConstructor.construct_yaml_str
)

SafeConstructor.add_constructor(
    "tag:yaml.org,2002:seq", SafeConstructor.construct_yaml_seq
)

SafeConstructor.add_constructor(
    "tag:yaml.org,2002:map", SafeConstructor.construct_yaml_map
)

SafeConstructor.add_constructor(None, SafeConstructor.construct_undefined)


class FullConstructor(SafeConstructor):  # noqa: D101
    # 'extend' is blacklisted because it is used by
    # construct_python_object_apply to add `listitems` to a newly generate
    # python instance
    def get_state_keys_blacklist(self):  # noqa: ANN201, PLR6301
        """
        Return regex patterns that blacklist attribute names from being assigned when applying object state.
        
        Returns:
            list[str]: Regular-expression strings; each pattern matches a state key that must not be assigned (for example, "^extend$" and names matching "^__.*__$").
        """
        return ["^extend$", "^__.*__$"]

    def get_state_keys_blacklist_regexp(self):  # noqa: ANN201
        """Compile and cache a regular expression that matches any blacklisted state key.

        Compiles a regex from the patterns returned by get_state_keys_blacklist() and stores it on the instance as the attribute `state_keys_blacklist_regexp` for reuse.

        Returns:
            re.Pattern: Compiled regular expression matching blacklisted state keys.
        """
        if not hasattr(self, "state_keys_blacklist_regexp"):
            self.state_keys_blacklist_regexp = re.compile(
                "(" + "|".join(self.get_state_keys_blacklist()) + ")"
            )
        return self.state_keys_blacklist_regexp

    def construct_python_str(self, node):  # noqa: ANN001, ANN201
        """Constructs a text value from a YAML scalar node.

        Parameters:
            node: The YAML scalar node to construct.

        Returns:
            str: The constructed string value.
        """
        return self.construct_scalar(node)

    def construct_python_unicode(self, node):  # noqa: ANN001, ANN201
        """Constructs a Unicode value from a scalar node.

        Parameters:
            node: The YAML scalar node to construct.

        Returns:
            A `str` containing the node's scalar value.
        """
        return self.construct_scalar(node)

    def construct_python_bytes(self, node):  # noqa: ANN001, ANN201
        """Construct a bytes object from a YAML scalar by decoding its base64-encoded ASCII content.

        Parameters:
            node: YAML scalar node containing base64-encoded ASCII text.

        Returns:
            The decoded binary data as bytes.

        Raises:
            ConstructorError: If the scalar cannot be converted to ASCII or if base64 decoding fails.
        """
        try:
            value = self.construct_scalar(node).encode("ascii")
        except UnicodeEncodeError as exc:
            raise ConstructorError(  # noqa: B904
                None,
                None,
                f"failed to convert base64 data into ascii: {exc}",
                node.start_mark,
            )
        try:
            return base64.decodebytes(value)
        except binascii.Error as exc:
            raise ConstructorError(  # noqa: B904
                None, None, f"failed to decode base64 data: {exc}", node.start_mark
            )

    def construct_python_long(self, node):  # noqa: ANN001, ANN201
        """Convert a YAML integer node to a Python integer.

        Returns:
            int: The integer value represented by the YAML node.
        """
        return self.construct_yaml_int(node)

    def construct_python_complex(self, node):  # noqa: ANN001, ANN201
        """Constructs a Python complex number from a scalar node.

        Parameters:
            node: A scalar node whose value is a string acceptable to Python's `complex()` constructor.

        Returns:
            complex: The complex number produced from the node's scalar value.
        """
        return complex(self.construct_scalar(node))

    def construct_python_tuple(self, node):  # noqa: ANN001, ANN201
        """
        Construct a Python tuple from a YAML SequenceNode.
        
        Parameters:
            node (SequenceNode): YAML sequence node whose elements will be constructed.
        
        Returns:
            tuple: Tuple containing the constructed elements.
        """
        return tuple(self.construct_sequence(node))

    def find_python_module(self, name, mark, unsafe=False):  # noqa: ANN001, ANN201, PLR6301
        """Locate and return a loaded Python module by name, optionally attempting to import it first.

        Parameters:
            name: Module name to find; must be a non-empty string.
            mark: Node mark used to report precise error location when raising ConstructorError.
            unsafe: If True, attempt to import the module before checking loaded modules.

        Returns:
            The module object from sys.modules corresponding to `name`.

        Raises:
            ConstructorError: If `name` is empty, if `unsafe` import fails, or if the module is not present in sys.modules.
        """
        if not name:
            raise ConstructorError(  # noqa: TRY003
                "while constructing a Python module",
                mark,
                "expected non-empty name appended to the tag",
                mark,
            )
        if unsafe:
            try:
                __import__(name)
            except ImportError as exc:
                raise ConstructorError(  # noqa: B904, TRY003
                    "while constructing a Python module",
                    mark,
                    f"cannot find module {name!r} ({exc})",
                    mark,
                )
        if name not in sys.modules:
            raise ConstructorError(  # noqa: TRY003
                "while constructing a Python module",
                mark,
                f"module {name!r} is not imported",
                mark,
            )
        return sys.modules[name]

    def find_python_name(self, name, mark, unsafe=False):  # noqa: ANN001, ANN201, PLR6301
        """
        Resolve a Python object by dotted name, looking up the module (or builtins) and returning the referenced attribute.
        
        Parameters:
            name (str): Dotted name like "pkg.module.Name" or a plain name (resolved in builtins).
            mark: Location mark used for error reporting.
            unsafe (bool): If True, attempt to import the module before lookup.
        
        Returns:
            The resolved Python object referenced by `name`.
        
        Raises:
            ConstructorError: If `name` is empty; if importing the module fails when `unsafe` is True;
                               if the module is not present in sys.modules; or if the attribute is missing.
        """
        if not name:
            raise ConstructorError(  # noqa: TRY003
                "while constructing a Python object",
                mark,
                "expected non-empty name appended to the tag",
                mark,
            )
        if "." in name:
            module_name, object_name = name.rsplit(".", 1)
        else:
            module_name = "builtins"
            object_name = name
        if unsafe:
            try:
                __import__(module_name)
            except ImportError as exc:
                raise ConstructorError(  # noqa: B904, TRY003
                    "while constructing a Python object",
                    mark,
                    f"cannot find module {module_name!r} ({exc})",
                    mark,
                )
        if module_name not in sys.modules:
            raise ConstructorError(  # noqa: TRY003
                "while constructing a Python object",
                mark,
                f"module {module_name!r} is not imported",
                mark,
            )
        module = sys.modules[module_name]
        if not hasattr(module, object_name):
            raise ConstructorError(  # noqa: TRY003
                "while constructing a Python object",
                mark,
                f"cannot find {object_name!r} in the module {module.__name__!r}",
                mark,
            )
        return getattr(module, object_name)

    def construct_python_name(self, suffix, node):  # noqa: ANN001, ANN201
        """
        Resolve a Python object from a tag suffix.
        
        Parameters:
            suffix (str): Dotted Python name encoded in the tag suffix (module or module.object).
            node (ScalarNode): YAML scalar node that must be empty; used for error location.
        
        Returns:
            object: The Python object referenced by `suffix`.
        
        Raises:
            ConstructorError: If the scalar node's value is not empty or the name cannot be resolved.
        """
        value = self.construct_scalar(node)
        if value:
            raise ConstructorError(  # noqa: TRY003
                "while constructing a Python name",
                node.start_mark,
                f"expected the empty value, but found {value!r}",
                node.start_mark,
            )
        return self.find_python_name(suffix, node.start_mark)

    def construct_python_module(self, suffix, node):  # noqa: ANN001, ANN201
        """Resolve the Python module named by the tag suffix.

        Parameters:
            suffix (str): Dotted module name to resolve.
            node (ScalarNode): YAML scalar node that must be empty; its start_mark is used for error reporting.

        Returns:
            module: The module object corresponding to `suffix`.

        Raises:
            ConstructorError: If the scalar node is not empty or the module cannot be resolved/imported.
        """
        value = self.construct_scalar(node)
        if value:
            raise ConstructorError(  # noqa: TRY003
                "while constructing a Python module",
                node.start_mark,
                f"expected the empty value, but found {value!r}",
                node.start_mark,
            )
        return self.find_python_module(suffix, node.start_mark)

    def make_python_instance(  # noqa: ANN201, PLR0913, PLR0917
        self,
        suffix,  # noqa: ANN001
        node,  # noqa: ANN001
        args=None,  # noqa: ANN001
        kwds=None,  # noqa: ANN001
        newobj=False,  # noqa: ANN001
        unsafe=False,  # noqa: ANN001
    ):
        """Instantiate a Python object by resolving a dotted-name suffix and constructing it with the provided arguments.

        Parameters:
            suffix (str): Dotted-name identifying the object to instantiate.
            node: YAML node used to provide location information for errors.
            args (list, optional): Positional arguments to pass to the constructor. Defaults to an empty list.
            kwds (dict, optional): Keyword arguments to pass to the constructor. Defaults to an empty dict.
            newobj (bool, optional): If True and the resolved object is a type, allocate an uninitialized instance via its `__new__` without calling `__init__`.
            unsafe (bool, optional): If False, require the resolved object to be a class; if True, allow non-type callables.

        Returns:
            The newly created object instance.

        Raises:
            ConstructorError: If the name cannot be resolved or, when `unsafe` is False, the resolved object is not a class.
        """
        if not args:
            args = []
        if not kwds:
            kwds = {}
        cls = self.find_python_name(suffix, node.start_mark)
        if not (unsafe or isinstance(cls, type)):
            raise ConstructorError(  # noqa: TRY003
                "while constructing a Python instance",
                node.start_mark,
                f"expected a class, but found {type(cls)!r}",
                node.start_mark,
            )
        if newobj and isinstance(cls, type):
            return cls.__new__(cls, *args, **kwds)
        return cls(*args, **kwds)

    def set_python_instance_state(self, instance, state, unsafe=False) -> None:  # noqa: ANN001
        """Set the deserialized state on an existing Python object instance.

        Parameters:
            instance: The object whose state will be restored.
            state: Either a mapping of attributes to set, or a (state, slotstate) 2-tuple where `state` is applied to __dict__ (if present) and `slotstate` contains attributes to set via setattr.
            unsafe (bool): If False, validate attribute names with `check_state_key` before setting; if True, skip validation.

        Raises:
            ConstructorError: If a state key is blacklisted by `check_state_key` and `unsafe` is False.
        """
        if hasattr(instance, "__setstate__"):
            instance.__setstate__(state)
        else:
            slotstate = {}
            if isinstance(state, tuple) and len(state) == 2:  # noqa: PLR2004
                state, slotstate = state
            if hasattr(instance, "__dict__"):
                if not unsafe and state:
                    for key in state:
                        self.check_state_key(key)
                instance.__dict__.update(state)
            elif state:
                slotstate.update(state)
            for key, value in slotstate.items():
                if not unsafe:
                    self.check_state_key(key)
                setattr(instance, key, value)

    def construct_python_object(self, suffix, node):  # noqa: ANN001, ANN201
        # Format:
        #   !!python/object:module.name { ... state ... }
        """
        Constructs a Python object instance from a mapping node and applies its saved state, yielding the partially constructed instance to support recursive references.
        
        Parameters:
            suffix (str): Class identifier suffix from the tag (e.g. "module.ClassName").
            node (MappingNode): YAML mapping node containing the instance state.
        
        Returns:
            instance: The initialized instance of the resolved Python class.
        """
        instance = self.make_python_instance(suffix, node, newobj=True)
        yield instance
        deep = hasattr(instance, "__setstate__")
        state = self.construct_mapping(node, deep=deep)
        self.set_python_instance_state(instance, state)

    def construct_python_object_apply(self, suffix, node, newobj=False):  # noqa: ANN001, ANN201
        # Format:
        #   !!python/object/apply       # (or !!python/object/new)
        #   args: [ ... arguments ... ]
        #   kwds: { ... keywords ... }
        #   state: ... state ...
        #   listitems: [ ... listitems ... ]
        #   dictitems: { ... dictitems ... }
        # or short format:
        #   !!python/object/apply [ ... arguments ... ]
        # The difference between !!python/object/apply and !!python/object/new
        # is how an object is created, check make_python_instance for details.
        """Construct a Python object by applying a callable/class with explicit args/kwargs or by using a recorded instance state.

        Supports two input shapes:
        - Short sequence form: a SequenceNode whose items are positional arguments (treated as args).
        - Mapping form: a MappingNode with optional keys `args`, `kwds`, `state`, `listitems`, and `dictitems` describing constructor arguments, keyword arguments, post-construction state, list extensions, and dict assignments respectively.

        Parameters:
            suffix (str): Dotted Python name used to resolve the callable or class to instantiate.
            node (SequenceNode | MappingNode): YAML node describing the call/application (either a sequence of args or a mapping with named fields).
            newobj (bool): If true, allocate a new uninitialized instance via the target type's __new__ semantics instead of calling its constructor.

        Returns:
            object: The constructed Python object after applying state, extending list contents, and assigning dict items.
        """
        if isinstance(node, SequenceNode):  # noqa: F405
            args = self.construct_sequence(node, deep=True)
            kwds = {}
            state = {}
            listitems = []
            dictitems = {}
        else:
            value = self.construct_mapping(node, deep=True)
            args = value.get("args", [])
            kwds = value.get("kwds", {})
            state = value.get("state", {})
            listitems = value.get("listitems", [])
            dictitems = value.get("dictitems", {})
        instance = self.make_python_instance(suffix, node, args, kwds, newobj)
        if state:
            self.set_python_instance_state(instance, state)
        if listitems:
            instance.extend(listitems)
        if dictitems:
            for key in dictitems:
                instance[key] = dictitems[key]
        return instance

    def construct_python_object_new(self, suffix, node):  # noqa: ANN001, ANN201
        """Constructs a Python object using `__new__`-based instantiation and applies construction parameters described by the YAML node.

        Parameters:
            suffix (str): The suffix portion of the Python name tag identifying the target class.
            node (yaml.nodes.Node): YAML node describing constructor arguments; may be a SequenceNode (treated as positional args) or a MappingNode (may contain `args`, `kwds`, `state`, `listitems`, `dictitems`).

        Returns:
            instance: A newly created Python object instance constructed according to the node's specification.
        """
        return self.construct_python_object_apply(suffix, node, newobj=True)


FullConstructor.add_constructor(
    "tag:yaml.org,2002:python/none", FullConstructor.construct_yaml_null
)

FullConstructor.add_constructor(
    "tag:yaml.org,2002:python/bool", FullConstructor.construct_yaml_bool
)

FullConstructor.add_constructor(
    "tag:yaml.org,2002:python/str", FullConstructor.construct_python_str
)

FullConstructor.add_constructor(
    "tag:yaml.org,2002:python/unicode", FullConstructor.construct_python_unicode
)

FullConstructor.add_constructor(
    "tag:yaml.org,2002:python/bytes", FullConstructor.construct_python_bytes
)

FullConstructor.add_constructor(
    "tag:yaml.org,2002:python/int", FullConstructor.construct_yaml_int
)

FullConstructor.add_constructor(
    "tag:yaml.org,2002:python/long", FullConstructor.construct_python_long
)

FullConstructor.add_constructor(
    "tag:yaml.org,2002:python/float", FullConstructor.construct_yaml_float
)

FullConstructor.add_constructor(
    "tag:yaml.org,2002:python/complex", FullConstructor.construct_python_complex
)

FullConstructor.add_constructor(
    "tag:yaml.org,2002:python/list", FullConstructor.construct_yaml_seq
)

FullConstructor.add_constructor(
    "tag:yaml.org,2002:python/tuple", FullConstructor.construct_python_tuple
)

FullConstructor.add_constructor(
    "tag:yaml.org,2002:python/dict", FullConstructor.construct_yaml_map
)

FullConstructor.add_multi_constructor(
    "tag:yaml.org,2002:python/name:", FullConstructor.construct_python_name
)


class UnsafeConstructor(FullConstructor):  # noqa: D101
    def find_python_module(self, name, mark):  # noqa: ANN001, ANN201
        """Resolve and return a Python module by name using the unsafe importer.

        Attempts to import the named module if needed and returns the module object from sys.modules.
        Raises ConstructorError if `name` is empty, the import fails, or the module is not present in sys.modules.

        Parameters:
            name (str): Fully qualified module name to resolve.
            mark: Location mark used for error reporting when raising ConstructorError.

        Returns:
            module: The imported or already-loaded module object.
        """
        return super().find_python_module(name, mark, unsafe=True)

    def find_python_name(self, name, mark):  # noqa: ANN001, ANN201
        """
        Resolve a Python dotted name to the referenced object, importing modules if necessary.
        
        Parameters:
            name (str): Dotted name to resolve (e.g., "module.Class" or "builtins.int").
            mark: Location marker used to annotate errors if resolution fails.
        
        Returns:
            The Python object identified by `name`.
        
        Raises:
            ConstructorError: If `name` is empty, the module or object cannot be found, or an import fails.
        """
        return super().find_python_name(name, mark, unsafe=True)

    def make_python_instance(self, suffix, node, args=None, kwds=None, newobj=False):  # noqa: ANN001, ANN201
        """Construct a Python object from a YAML node using unsafe name/module resolution.

        Parameters:
            suffix (str): Suffix used to resolve the target Python name or module from the tag.
            node (yaml.nodes.Node): YAML node describing the object to construct; may be a mapping or sequence providing constructor arguments or state.
            args (list, optional): Positional arguments to pass to the target constructor if provided.
            kwds (dict, optional): Keyword arguments to pass to the target constructor if provided.
            newobj (bool, optional): If true and the resolved target is a type, create the instance via its `__new__` without calling `__init__`.

        Returns:
            object: The constructed Python instance; may be created unsafely (module/name imports and state application are allowed).
        """
        return super().make_python_instance(
            suffix, node, args, kwds, newobj, unsafe=True
        )

    def set_python_instance_state(self, instance, state):  # noqa: ANN001, ANN201
        """
        Apply a saved state to a Python object instance without performing state-key safety checks.
        
        If the instance defines __setstate__, that method is called with the provided state. Otherwise, the function applies the state by updating instance.__dict__ (if present) and assigning any slot-state entries as attributes.
        
        Parameters:
            instance: Target object to restore state onto.
            state: The state to apply. May be a mapping or a two-tuple (state, slotstate). The first element (state) is applied to the instance's dictionary when present; the second element (slotstate) is a mapping of attribute names to values that will be set on the instance.
        """
        return super().set_python_instance_state(instance, state, unsafe=True)


UnsafeConstructor.add_multi_constructor(
    "tag:yaml.org,2002:python/module:", UnsafeConstructor.construct_python_module
)

UnsafeConstructor.add_multi_constructor(
    "tag:yaml.org,2002:python/object:", UnsafeConstructor.construct_python_object
)

UnsafeConstructor.add_multi_constructor(
    "tag:yaml.org,2002:python/object/new:",
    UnsafeConstructor.construct_python_object_new,
)

UnsafeConstructor.add_multi_constructor(
    "tag:yaml.org,2002:python/object/apply:",
    UnsafeConstructor.construct_python_object_apply,
)


# Constructor is same as UnsafeConstructor. Need to leave this in place in case
# people have extended it directly.
class Constructor(UnsafeConstructor):  # noqa: D101
    pass
