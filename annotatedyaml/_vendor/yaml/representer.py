__all__ = ["BaseRepresenter", "Representer", "RepresenterError", "SafeRepresenter"]  # noqa: D100

import base64
import collections
import contextlib
import copyreg
import datetime
import types
from typing import Never

from .error import *  # noqa: F403
from .nodes import *  # noqa: F403


class RepresenterError(YAMLError):  # noqa: D101, F405
    pass


class BaseRepresenter:  # noqa: D101
    yaml_representers = {}  # noqa: RUF012
    yaml_multi_representers = {}  # noqa: RUF012

    def __init__(
        self,
        default_style=None,  # noqa: ANN001
        default_flow_style=False,  # noqa: ANN001
        sort_keys=True,  # noqa: ANN001
    ) -> None:
        """Initialize a representer with serialization defaults and alias-tracking state.

        Parameters:
            default_style (Optional[str]): Default scalar style to use when no explicit style is provided.
            default_flow_style (bool): Default flow style for sequences and mappings when not specified.
            sort_keys (bool): Whether mapping keys should be sorted when representing mappings.
        """
        self.default_style = default_style
        self.sort_keys = sort_keys
        self.default_flow_style = default_flow_style
        self.represented_objects = {}
        self.object_keeper = []
        self.alias_key = None

    def represent(self, data) -> None:  # noqa: ANN001
        """Represent the given Python object as YAML and serialize the resulting node.

        This builds a YAML node for `data`, emits it via the representer's serializer, and then clears internal caches used for aliasing and object tracking.

        Parameters:
            data: The Python object to represent and serialize.
        """
        node = self.represent_data(data)
        self.serialize(node)
        self.represented_objects = {}
        self.object_keeper = []
        self.alias_key = None

    def represent_data(self, data):  # noqa: ANN001, ANN201
        """Create and return a YAML node that represents the given Python object.

        This selects an appropriate registered representer for `data`'s type (including
        multi-representers and fallback representers) and produces the resulting node.
        When aliasing is enabled for the value, a previously created node for the same
        object may be returned from an internal cache.

        Parameters:
            data: The Python object to convert into a YAML node.

        Returns:
            node: A YAML node representing `data`.
        """
        if self.ignore_aliases(data):
            self.alias_key = None
        else:
            self.alias_key = id(data)
        if self.alias_key is not None:
            if self.alias_key in self.represented_objects:
                return self.represented_objects[self.alias_key]
                # if node is None:
                #    raise RepresenterError("recursive objects are not allowed: %r" % data)
            # self.represented_objects[alias_key] = None
            self.object_keeper.append(data)
        data_types = type(data).__mro__
        if data_types[0] in self.yaml_representers:
            node = self.yaml_representers[data_types[0]](self, data)
        else:
            for data_type in data_types:
                if data_type in self.yaml_multi_representers:
                    node = self.yaml_multi_representers[data_type](self, data)
                    break
            else:
                if None in self.yaml_multi_representers:
                    node = self.yaml_multi_representers[None](self, data)
                elif None in self.yaml_representers:
                    node = self.yaml_representers[None](self, data)
                else:
                    node = ScalarNode(None, str(data))  # noqa: F405
        # if alias_key is not None:
        #    self.represented_objects[alias_key] = node
        return node

    @classmethod
    def add_representer(cls, data_type, representer) -> None:  # noqa: ANN001
        """Register a representer callable for a given Python type on this representer class.

        Ensures the class has its own copy of the `yaml_representers` mapping (copy-on-write)
        before assigning, so subclasses can override registrations without mutating the
        parent class's registry.

        Parameters:
            data_type (type | None): The Python type key to register the representer for.
                `None` may be used as a fallback catch-all key.
            representer (callable): A callable that produces a YAML node for instances of
                `data_type` (typically a method taking the representer instance and the data).
        """
        if "yaml_representers" not in cls.__dict__:
            cls.yaml_representers = cls.yaml_representers.copy()
        cls.yaml_representers[data_type] = representer

    @classmethod
    def add_multi_representer(cls, data_type, representer) -> None:  # noqa: ANN001
        """Register a multi-type representer on the class using copy-on-write to avoid mutating a parent class's registry.

        Parameters:
            data_type (type | None): Type for which the representer will be used, or `None` to set the fallback representer.
            representer (callable): Callable that converts instances of `data_type` into a node.
        """
        if "yaml_multi_representers" not in cls.__dict__:
            cls.yaml_multi_representers = cls.yaml_multi_representers.copy()
        cls.yaml_multi_representers[data_type] = representer

    def represent_scalar(self, tag, value, style=None):  # noqa: ANN001, ANN201
        """Create a YAML ScalarNode with the given tag, value, and style.

        If `style` is None, `self.default_style` is used. If `self.alias_key` is not None, the created node is cached in `self.represented_objects` under that key.

        Parameters:
            tag (str): YAML tag to assign to the scalar node.
            value (any): Content of the scalar node.
            style (str | None): Scalar style indicator (e.g., plain, single-quoted, folded); if None, `default_style` is applied.

        Returns:
            ScalarNode: The created YAML scalar node.
        """
        if style is None:
            style = self.default_style
        node = ScalarNode(tag, value, style=style)  # noqa: F405
        if self.alias_key is not None:
            self.represented_objects[self.alias_key] = node
        return node

    def represent_sequence(self, tag, sequence, flow_style=None):  # noqa: ANN001, ANN201
        """Create a YAML SequenceNode for the given Python sequence, representing each item and applying the requested or default flow style.

        Parameters:
            tag (str): YAML tag to assign to the resulting sequence node.
            sequence (iterable): The Python sequence whose elements will be converted to YAML nodes.
            flow_style (bool | None): If True or False, force the node's flow style; if None, use the representer's default_flow_style when set, otherwise infer the style from element nodes.

        Returns:
            SequenceNode: A YAML SequenceNode containing the represented elements. The node may be cached in the representer when an alias key is active.
        """
        value = []
        node = SequenceNode(tag, value, flow_style=flow_style)  # noqa: F405
        if self.alias_key is not None:
            self.represented_objects[self.alias_key] = node
        best_style = True
        for item in sequence:
            node_item = self.represent_data(item)
            if not (isinstance(node_item, ScalarNode) and not node_item.style):  # noqa: F405
                best_style = False
            value.append(node_item)
        if flow_style is None:
            if self.default_flow_style is not None:
                node.flow_style = self.default_flow_style
            else:
                node.flow_style = best_style
        return node

    def represent_mapping(self, tag, mapping, flow_style=None):  # noqa: ANN001, ANN201
        """Create a MappingNode with represented key/value nodes for the given mapping.

        Parameters:
            tag (str): YAML tag to attach to the mapping node.
            mapping (Mapping | Iterable[tuple]): A mapping (has `.items()`) or an iterable of (key, value) pairs to represent.
            flow_style (bool | None): If not None, forces the node's flow style; if None, the flow style is chosen from `default_flow_style` when set, otherwise inferred from the represented key/value nodes.

        Notes:
            - If `self.alias_key` is set, the created node is cached in `self.represented_objects` under that key.
            - If `mapping` provides `.items()` and `self.sort_keys` is True, keys are sorted when possible (TypeError from sorting is ignored).
            - The node's flow style is set to `self.default_flow_style` when that is not None; otherwise it is set to True only when all key and value nodes are unstyled scalars.

        Returns:
            MappingNode: A YAML mapping node containing the represented (key_node, value_node) pairs.
        """
        value = []
        node = MappingNode(tag, value, flow_style=flow_style)  # noqa: F405
        if self.alias_key is not None:
            self.represented_objects[self.alias_key] = node
        best_style = True
        if hasattr(mapping, "items"):
            mapping = list(mapping.items())
            if self.sort_keys:
                with contextlib.suppress(TypeError):
                    mapping = sorted(mapping)
        for item_key, item_value in mapping:
            node_key = self.represent_data(item_key)
            node_value = self.represent_data(item_value)
            if not (isinstance(node_key, ScalarNode) and not node_key.style):  # noqa: F405
                best_style = False
            if not (isinstance(node_value, ScalarNode) and not node_value.style):  # noqa: F405
                best_style = False
            value.append((node_key, node_value))
        if flow_style is None:
            if self.default_flow_style is not None:
                node.flow_style = self.default_flow_style
            else:
                node.flow_style = best_style
        return node

    def ignore_aliases(self, data) -> bool:  # noqa: ANN001, PLR6301
        """Indicates whether an object should be excluded from YAML anchor/alias handling during representation.

        The base implementation always allows aliasing for all objects.

        Returns:
            bool: `True` if the object should be ignored for aliasing (no anchor created), `False` otherwise.
        """
        return False


class SafeRepresenter(BaseRepresenter):  # noqa: D101
    def ignore_aliases(self, data) -> bool | None:  # noqa: ANN001, PLR6301
        """Determine whether YAML aliases should be disabled for a value.

        Returns:
            `True` if aliases should be ignored for the value (for `None`, the empty tuple `()`, or instances of `str`, `bytes`, `bool`, `int`, or `float`); `None` otherwise.
        """
        if data is None:
            return True
        if isinstance(data, tuple) and data == ():
            return True
        if isinstance(data, (str, bytes, bool, int, float)):
            return True
        return None

    def represent_none(self, data):  # noqa: ANN001, ANN201
        """Represent Python None as a YAML null scalar.

        Returns:
            A ScalarNode with tag "tag:yaml.org,2002:null" and value "null".
        """
        return self.represent_scalar("tag:yaml.org,2002:null", "null")

    def represent_str(self, data):  # noqa: ANN001, ANN201
        """Represent a Python string as a YAML scalar using the standard string tag.

        Returns:
            ScalarNode: YAML scalar node with tag "tag:yaml.org,2002:str" containing the input string.
        """
        return self.represent_scalar("tag:yaml.org,2002:str", data)

    def represent_binary(self, data):  # noqa: ANN001, ANN201
        """Represent binary data as a YAML binary scalar.

        Base64-encodes the given bytes and returns a YAML scalar tagged `tag:yaml.org,2002:binary` using block style (`"|"`).

        Returns:
            ScalarNode: A scalar node containing the base64-encoded ASCII string with block style `"|"`.
        """
        if hasattr(base64, "encodebytes"):
            data = base64.encodebytes(data).decode("ascii")
        else:
            data = base64.encodestring(data).decode("ascii")
        return self.represent_scalar("tag:yaml.org,2002:binary", data, style="|")

    def represent_bool(self, data):  # noqa: ANN001, ANN201
        """Create a YAML scalar node using the YAML boolean tag.

        Returns:
            ScalarNode: A scalar node with the YAML boolean tag whose value is "true" if the input is truthy, "false" otherwise.
        """
        value = "true" if data else "false"
        return self.represent_scalar("tag:yaml.org,2002:bool", value)

    def represent_int(self, data):  # noqa: ANN001, ANN201
        """Represent an integer as a YAML integer scalar.

        Parameters:
            data (int): Integer value to represent.

        Returns:
            ScalarNode: YAML scalar node tagged 'tag:yaml.org,2002:int' containing the integer's decimal string.
        """
        return self.represent_scalar("tag:yaml.org,2002:int", str(data))

    inf_value = 1e300
    while repr(inf_value) != repr(inf_value * inf_value):
        inf_value *= inf_value

    def represent_float(self, data):  # noqa: ANN001, ANN201
        """Represent a floating-point number as a YAML float scalar.

        Maps NaN to `.nan`, positive infinity to `.inf`, negative infinity to `-.inf`, and otherwise uses the lowercase Python `repr()` of the value (inserting `.0` before an exponent when the representation lacks a decimal point).

        Returns:
            A `ScalarNode` with tag "tag:yaml.org,2002:float" whose value is the YAML text form of `data`.
        """
        if data != data or (data == 0.0 and data == 1.0):  # noqa: PLR0124, RUF069
            value = ".nan"
        elif data == self.inf_value:
            value = ".inf"
        elif data == -self.inf_value:
            value = "-.inf"
        else:
            value = repr(data).lower()
            # Note that in some cases `repr(data)` represents a float number
            # without the decimal parts.  For instance:
            #   >>> repr(1e17)
            #   '1e17'
            # Unfortunately, this is not a valid float representation according
            # to the definition of the `!!float` tag.  We fix this by adding
            # '.0' before the 'e' symbol.
            if "." not in value and "e" in value:
                value = value.replace("e", ".0e", 1)
        return self.represent_scalar("tag:yaml.org,2002:float", value)

    def represent_list(self, data):  # noqa: ANN001, ANN201
        # pairs = (len(data) > 0 and isinstance(data, list))
        # if pairs:
        #    for item in data:
        #        if not isinstance(item, tuple) or len(item) != 2:
        #            pairs = False
        #            break
        # if not pairs:
        """Represent a Python list as a YAML sequence node.

        Returns:
            SequenceNode: YAML sequence node tagged "tag:yaml.org,2002:seq" containing the represented elements of `data`.
        """
        return self.represent_sequence("tag:yaml.org,2002:seq", data)

    # value = []
    # for item_key, item_value in data:
    #    value.append(self.represent_mapping(u'tag:yaml.org,2002:map',
    #        [(item_key, item_value)]))
    # return SequenceNode(u'tag:yaml.org,2002:pairs', value)

    def represent_dict(self, data):  # noqa: ANN001, ANN201
        """Represent a Python dict as a YAML mapping node using the standard YAML map tag.

        Parameters:
            data (dict): Mapping to represent.

        Returns:
            MappingNode: A YAML mapping node tagged with "tag:yaml.org,2002:map".
        """
        return self.represent_mapping("tag:yaml.org,2002:map", data)

    def represent_set(self, data):  # noqa: ANN001, ANN201
        """Represent a Python set as a YAML set node where each element is a mapping key with a YAML null value.

        Returns:
            MappingNode: A mapping node tagged "tag:yaml.org,2002:set" whose keys are the set elements and whose values are YAML null (represented as Python `None`).
        """
        value = {}
        for key in data:
            value[key] = None
        return self.represent_mapping("tag:yaml.org,2002:set", value)

    def represent_date(self, data):  # noqa: ANN001, ANN201
        """Represent a date object as a YAML timestamp scalar.

        Parameters:
            data (datetime.date): The date to represent.

        Returns:
            ScalarNode: A YAML scalar node tagged `tag:yaml.org,2002:timestamp` containing the date in ISO 8601 format (YYYY-MM-DD).
        """
        value = data.isoformat()
        return self.represent_scalar("tag:yaml.org,2002:timestamp", value)

    def represent_datetime(self, data):  # noqa: ANN001, ANN201
        """Represent a datetime as an ISO 8601 timestamp scalar.

        Parameters:
            data (datetime.datetime): The datetime to represent; the date and time are separated by a space.

        Returns:
            ScalarNode: A YAML scalar node tagged `tag:yaml.org,2002:timestamp` whose value is `data.isoformat(" ")`.
        """
        value = data.isoformat(" ")
        return self.represent_scalar("tag:yaml.org,2002:timestamp", value)

    def represent_yaml_object(self, tag, data, cls, flow_style=None):  # noqa: ANN001, ANN201
        """Represent a Python object's serializable state as a YAML mapping node tagged with `tag`.

        If the object defines `__getstate__()`, its result is used as the mapping; otherwise the object's `__dict__` is copied and used. The resulting mapping node will use `flow_style` when provided.

        Parameters:
            tag (str): YAML tag to apply to the resulting mapping node.
            data (object): The object whose state will be represented.
            cls (type): The object's class (provided for callers; not inspected by this method).
            flow_style (bool | None): If specified, forces the node's flow style; otherwise the default/auto behavior is used.

        Returns:
            MappingNode: A YAML mapping node containing the object's state and tagged with `tag`.
        """
        if hasattr(data, "__getstate__"):
            state = data.__getstate__()
        else:
            state = data.__dict__.copy()
        return self.represent_mapping(tag, state, flow_style=flow_style)

    def represent_undefined(self, data) -> Never:  # noqa: ANN001, PLR6301
        """Always raises a RepresenterError indicating the object cannot be represented.

        Raises:
            RepresenterError: always raised with message "cannot represent an object" and the unrepresentable `data` passed as the second argument.
        """
        raise RepresenterError("cannot represent an object", data)  # noqa: TRY003


SafeRepresenter.add_representer(type(None), SafeRepresenter.represent_none)

SafeRepresenter.add_representer(str, SafeRepresenter.represent_str)

SafeRepresenter.add_representer(bytes, SafeRepresenter.represent_binary)

SafeRepresenter.add_representer(bool, SafeRepresenter.represent_bool)

SafeRepresenter.add_representer(int, SafeRepresenter.represent_int)

SafeRepresenter.add_representer(float, SafeRepresenter.represent_float)

SafeRepresenter.add_representer(list, SafeRepresenter.represent_list)

SafeRepresenter.add_representer(tuple, SafeRepresenter.represent_list)

SafeRepresenter.add_representer(dict, SafeRepresenter.represent_dict)

SafeRepresenter.add_representer(set, SafeRepresenter.represent_set)

SafeRepresenter.add_representer(datetime.date, SafeRepresenter.represent_date)

SafeRepresenter.add_representer(datetime.datetime, SafeRepresenter.represent_datetime)

SafeRepresenter.add_representer(None, SafeRepresenter.represent_undefined)


class Representer(SafeRepresenter):  # noqa: D101
    def represent_complex(self, data):  # noqa: ANN001, ANN201
        """Represent a complex number as a YAML scalar using the `tag:yaml.org,2002:python/complex` tag.

        The scalar value is a string form of the complex number:
        - If the imaginary part is 0, the value is the real part (e.g. "1.0").
        - If the real part is 0, the value is the imaginary part with a trailing `j` (e.g. "2.0j").
        - Otherwise the value combines real and imaginary parts, using `+` or `-` as appropriate (e.g. "1.0+2.0j" or "1.0-2.0j").

        Returns:
            ScalarNode: A YAML scalar node tagged `tag:yaml.org,2002:python/complex` containing the complex value as a string.
        """
        if data.imag == 0.0:  # noqa: RUF069
            data = f"{data.real!r}"
        elif data.real == 0.0:  # noqa: RUF069
            data = f"{data.imag!r}j"
        elif data.imag > 0:
            data = f"{data.real!r}+{data.imag!r}j"
        else:
            data = f"{data.real!r}{data.imag!r}j"
        return self.represent_scalar("tag:yaml.org,2002:python/complex", data)

    def represent_tuple(self, data):  # noqa: ANN001, ANN201
        """Represent a Python tuple as a YAML sequence tagged for Python tuples.

        Parameters:
            data (tuple): The tuple to represent.

        Returns:
            SequenceNode: A YAML sequence node tagged with `tag:yaml.org,2002:python/tuple` containing the represented tuple items.
        """
        return self.represent_sequence("tag:yaml.org,2002:python/tuple", data)

    def represent_name(self, data):  # noqa: ANN001, ANN201
        """Represent a Python object's fully qualified name as a YAML scalar.

        Constructs a ScalarNode tagged with "tag:yaml.org,2002:python/name:{module}.{name}" and an empty string value.

        Parameters:
            data: An object (typically a function or class) exposing `__module__` and `__name__` attributes.

        Returns:
            ScalarNode: A scalar node whose tag encodes the object's module and name and whose value is an empty string.
        """
        name = f"{data.__module__}.{data.__name__}"
        return self.represent_scalar("tag:yaml.org,2002:python/name:" + name, "")

    def represent_module(self, data):  # noqa: ANN001, ANN201
        """Represent a Python module as a YAML scalar node tagged with the module's name.

        Parameters:
            data (module): The module object to represent.

        Returns:
            ScalarNode: A scalar node with tag `tag:yaml.org,2002:python/module:<module_name>` and an empty string value.
        """
        return self.represent_scalar(
            "tag:yaml.org,2002:python/module:" + data.__name__, ""
        )

    def represent_object(self, data):  # noqa: ANN001, ANN201, PLR0912
        # We use __reduce__ API to save the data. data.__reduce__ returns
        # a tuple of length 2-5:
        #   (function, args, state, listitems, dictitems)

        # For reconstructing, we calls function(*args), then set its state,
        # listitems, and dictitems if they are not None.

        # A special case is when function.__name__ == '__newobj__'. In this
        # case we create the object with args[0].__new__(*args).

        # Another special case is when __reduce__ returns a string - we don't
        # support it.

        # We produce a !!python/object, !!python/object/new or
        # !!python/object/apply node.
        """Create a YAML node that represents an arbitrary Python object using the object's pickle reduction information.

        Parameters:
            data (object): The Python object to represent.

        Returns:
            node: A YAML node (mapping or sequence) that encodes the object's construction and state using `tag:yaml.org,2002:python/object:*`, `.../new:*`, or `.../apply:*` forms as appropriate.

        Raises:
            RepresenterError: If the object cannot be represented via the reduction protocol.
        """
        cls = type(data)
        if cls in copyreg.dispatch_table:
            reduce = copyreg.dispatch_table[cls](data)
        elif hasattr(data, "__reduce_ex__"):
            reduce = data.__reduce_ex__(2)
        elif hasattr(data, "__reduce__"):
            reduce = data.__reduce__()
        else:
            raise RepresenterError("cannot represent an object", data)  # noqa: TRY003
        reduce = (list(reduce) + [None] * 5)[:5]
        function, args, state, listitems, dictitems = reduce
        args = list(args)
        if state is None:
            state = {}
        if listitems is not None:
            listitems = list(listitems)
        if dictitems is not None:
            dictitems = dict(dictitems)
        if function.__name__ == "__newobj__":
            function = args[0]
            args = args[1:]
            tag = "tag:yaml.org,2002:python/object/new:"
            newobj = True
        else:
            tag = "tag:yaml.org,2002:python/object/apply:"
            newobj = False
        function_name = f"{function.__module__}.{function.__name__}"
        if (
            not args
            and not listitems
            and not dictitems
            and isinstance(state, dict)
            and newobj
        ):
            return self.represent_mapping(
                "tag:yaml.org,2002:python/object:" + function_name, state
            )
        if not listitems and not dictitems and isinstance(state, dict) and not state:
            return self.represent_sequence(tag + function_name, args)
        value = {}
        if args:
            value["args"] = args
        if state or not isinstance(state, dict):
            value["state"] = state
        if listitems:
            value["listitems"] = listitems
        if dictitems:
            value["dictitems"] = dictitems
        return self.represent_mapping(tag + function_name, value)

    def represent_ordered_dict(self, data):  # noqa: ANN001, ANN201
        # Provide uniform representation across different Python versions.
        """Represent an ordered mapping as a YAML `python/object/apply` sequence while preserving iteration order.

        Parameters:
            data: An ordered mapping (for example, collections.OrderedDict) whose iteration order will be preserved.

        Returns:
            A SequenceNode tagged with `tag:yaml.org,2002:python/object/apply:{module}.{typename}` containing a single sequence whose element is a list of `[key, value]` pairs representing the mapping's items.
        """
        data_type = type(data)
        tag = f"tag:yaml.org,2002:python/object/apply:{data_type.__module__}.{data_type.__name__}"
        items = [[key, value] for key, value in data.items()]
        return self.represent_sequence(tag, [items])


Representer.add_representer(complex, Representer.represent_complex)

Representer.add_representer(tuple, Representer.represent_tuple)

Representer.add_multi_representer(type, Representer.represent_name)

Representer.add_representer(collections.OrderedDict, Representer.represent_ordered_dict)

Representer.add_representer(types.FunctionType, Representer.represent_name)

Representer.add_representer(types.BuiltinFunctionType, Representer.represent_name)

Representer.add_representer(types.ModuleType, Representer.represent_module)

Representer.add_multi_representer(object, Representer.represent_object)
