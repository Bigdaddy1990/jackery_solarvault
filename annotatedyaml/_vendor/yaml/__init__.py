from . import loader as _loader  # noqa: D104
from .dumper import *
from .error import *
from .events import *
from .loader import *
from .nodes import *
from .tokens import *

__version__ = "6.0.3"
try:
    from .cyaml import *

    __with_libyaml__ = True
except ImportError:
    __with_libyaml__ = False

import io


# ------------------------------------------------------------------------------
# XXX "Warnings control" is now deprecated. Leaving in the API function to not
# break code that uses it.
# ------------------------------------------------------------------------------
def warnings(settings=None):  # noqa: ANN001, ANN201
    """Deprecated: stub for configuring YAML warnings — when called with no arguments returns the current warning settings, otherwise performs no action.

    Parameters:
        settings (optional): If provided, the function does not modify any state and returns None.

    Returns:
        dict: The current warning settings (an empty dict) when `settings` is None, `None` otherwise.
    """  # noqa: E501
    if settings is None:
        return {}
    return None


# ------------------------------------------------------------------------------
def scan(stream, Loader=Loader):  # noqa: ANN001, ANN201, F405, N803
    """Produce scanning tokens from a YAML input stream.

    Parameters:
        stream: A YAML input source (string or file-like object) to be scanned.

    Yields:
        Token objects representing lexical tokens from the input stream.
    """
    loader = Loader(stream)
    try:
        while loader.check_token():
            yield loader.get_token()
    finally:
        loader.dispose()


def parse(stream, Loader=Loader):  # noqa: ANN001, ANN201, F405, N803
    """Parse a YAML stream and produce parsing events.

    Returns:
        Event: `Event` instances parsed from the stream, yielded one at a time.
    """
    loader = Loader(stream)
    try:
        while loader.check_event():
            yield loader.get_event()
    finally:
        loader.dispose()


def compose(stream, Loader=Loader):  # noqa: ANN001, ANN201, F405, N803
    """Parse the first YAML document from a stream and return its representation tree root node.

    Parameters:
        stream: A text or binary stream (or stream-like object) containing YAML content.
        Loader: Loader class to use for parsing; the class will be instantiated with `stream`.

    Returns:
        The root node of the first YAML document.
    """  # noqa: E501
    loader = Loader(stream)
    try:
        return loader.get_single_node()
    finally:
        loader.dispose()


def compose_all(stream, Loader=Loader):  # noqa: ANN001, ANN201, F405, N803
    """Yield the root node of the representation tree for each YAML document in the given stream.

    Parameters:
        stream: A text or binary stream (or string) containing one or more YAML documents.
        Loader (class): Loader class to use for parsing; must accept `stream` in its constructor.

    Yields:
        nodes.Node: The root node of the representation tree for each parsed document.
    """  # noqa: E501
    loader = Loader(stream)
    try:
        while loader.check_node():
            yield loader.get_node()
    finally:
        loader.dispose()


def load(stream, Loader):  # noqa: ANN001, ANN201, N803
    """Parse the first YAML document from the given stream and return its Python representation.

    Parameters:
        stream: The input source containing YAML (stream or string).
        Loader: The loader class to instantiate for parsing.

    Returns:
        data: The Python object produced from the first YAML document.
    """  # noqa: E501
    loader = Loader(stream)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def load_all(stream, Loader):  # noqa: ANN001, ANN201, N803
    """Parse all YAML documents in a stream and produce corresponding Python objects.

    Parameters:
        stream: A readable stream (or file-like object) containing YAML content.
        Loader: A Loader class to instantiate for parsing the stream.

    Yields:
        data: Python objects constructed from each YAML document in the stream.
    """
    loader = Loader(stream)
    try:
        while loader.check_data():
            yield loader.get_data()
    finally:
        loader.dispose()


def full_load(stream):  # noqa: ANN001, ANN201
    """Parse the first YAML document in a stream and produce the corresponding Python object.

    Resolve all YAML tags except those considered unsafe for untrusted input.

    Parameters:
        stream: A text or binary stream containing YAML documents.

    Returns:
        The Python object represented by the first YAML document in the stream.
    """  # noqa: E501
    return load(stream, FullLoader)  # noqa: F405


def full_load_all(stream):  # noqa: ANN001, ANN201
    """Produce Python objects for each YAML document in a stream using the FullLoader.

    Resolve YAML tags while avoiding tag handlers that are unsafe for untrusted input.

    Returns:
        generator: An iterator that yields the Python object produced for each document in the stream.
    """  # noqa: E501
    return load_all(stream, FullLoader)  # noqa: F405


def safe_load(stream):  # noqa: ANN001, ANN201
    """Parse the first YAML document from a stream and produce the corresponding Python object.

    This loader resolves only basic YAML tags and is safe for untrusted input.

    Returns:
        The Python object constructed from the first YAML document in `stream`.
    """  # noqa: E501
    return load(stream, SafeLoader)  # noqa: F405


def safe_load_all(stream):  # noqa: ANN001, ANN201
    """Parse all YAML documents in a stream
    and produce corresponding Python objects.

    Resolve only basic YAML tags. This is known
    to be safe for untrusted input.
    """  # noqa: D205
    return load_all(stream, SafeLoader)  # noqa: F405


def unsafe_load(stream):  # noqa: ANN001, ANN201
    """Parse the first YAML document from a stream.

    Resolves all YAML tags, including those that may be unsafe when processing untrusted input.

    Returns:
        The Python object represented by the first document.
    """  # noqa: E501
    return load(stream, UnsafeLoader)  # noqa: F405


def unsafe_load_all(stream):  # noqa: ANN001, ANN201
    """Parse all YAML documents in a stream using the unsafe loader.

    Returns:
        iterator: An iterator that yields the Python object produced for each document.
    """
    return load_all(stream, UnsafeLoader)  # noqa: F405


def emit(  # noqa: ANN201, PLR0913, PLR0917
    events,  # noqa: ANN001
    stream=None,  # noqa: ANN001
    Dumper=Dumper,  # noqa: ANN001, F405, N803
    canonical=None,  # noqa: ANN001
    indent=None,  # noqa: ANN001
    width=None,  # noqa: ANN001
    allow_unicode=None,  # noqa: ANN001
    line_break=None,  # noqa: ANN001
):
    """Emit a sequence of YAML events to a stream.

    Parameters:
        events (iterable): An iterable of YAML event objects to emit.
        stream (IO[str] | None): Destination text stream. If `None`, an in-memory string buffer is used and its value is returned.
        Dumper (class): Dumper class to instantiate for emitting events.
        canonical (bool | None): Whether to use canonical output style.
        indent (int | None): Indentation level for nested structures.
        width (int | None): Preferred maximum line width.
        allow_unicode (bool | None): Whether to allow non-ASCII characters.
        line_break (str | None): Line break style to use.

    Returns:
        str | None: The produced YAML string if `stream` was `None`, otherwise `None`.
    """  # noqa: E501
    getvalue = None
    if stream is None:
        stream = io.StringIO()
        getvalue = stream.getvalue
    dumper = Dumper(
        stream,
        canonical=canonical,
        indent=indent,
        width=width,
        allow_unicode=allow_unicode,
        line_break=line_break,
    )
    try:
        for event in events:
            dumper.emit(event)
    finally:
        dumper.dispose()
    if getvalue:
        return getvalue()
    return None


def serialize_all(  # noqa: ANN201, PLR0913, PLR0917
    nodes,  # noqa: ANN001
    stream=None,  # noqa: ANN001
    Dumper=Dumper,  # noqa: ANN001, F405, N803
    canonical=None,  # noqa: ANN001
    indent=None,  # noqa: ANN001
    width=None,  # noqa: ANN001
    allow_unicode=None,  # noqa: ANN001
    line_break=None,  # noqa: ANN001
    encoding=None,  # noqa: ANN001
    explicit_start=None,  # noqa: ANN001
    explicit_end=None,  # noqa: ANN001
    version=None,  # noqa: ANN001
    tags=None,  # noqa: ANN001
):
    """Serialize a sequence of representation nodes into a YAML stream.

    When `stream` is provided, the serialized YAML is written to it. When `stream` is None, the function returns the produced content: a `str` if `encoding` is None, otherwise `bytes`.

    Parameters:
        nodes: A sequence of YAML representation nodes to serialize (document root nodes).
        stream: An IO-like object to write the serialized YAML to. If `None`, an in-memory buffer is used and its value is returned.
        Dumper: Dumper class to perform serialization (defaults to module `Dumper`).
        canonical: If True, produce canonical YAML form.
        indent: Indentation level to use for nested structures.
        width: Preferred line width for folded lines.
        allow_unicode: If True, allow non-ASCII characters as-is.
        line_break: Line break style to use.
        encoding: If provided, output is produced as `bytes`; otherwise `str`.
        explicit_start: If True, emit explicit start markers for each document.
        explicit_end: If True, emit explicit end markers for each document.
        version: YAML version tuple to emit (e.g., `(1, 2)`), or None.
        tags: Optional mapping of tag handles to URIs for tag directives in the output.

    Returns:
        `str` if `stream` is None and `encoding` is None, `bytes` if `stream` is None and `encoding` is provided, `None` if a `stream` was supplied.
    """  # noqa: E501
    getvalue = None
    if stream is None:
        stream = io.StringIO() if encoding is None else io.BytesIO()
        getvalue = stream.getvalue
    dumper = Dumper(
        stream,
        canonical=canonical,
        indent=indent,
        width=width,
        allow_unicode=allow_unicode,
        line_break=line_break,
        encoding=encoding,
        version=version,
        tags=tags,
        explicit_start=explicit_start,
        explicit_end=explicit_end,
    )
    try:
        dumper.open()
        for node in nodes:
            dumper.serialize(node)
        dumper.close()
    finally:
        dumper.dispose()
    if getvalue:
        return getvalue()
    return None


def serialize(node, stream=None, Dumper=Dumper, **kwds):  # noqa: ANN001, ANN003, ANN201, F405, N803
    """Serialize a representation tree into a YAML stream.

    Returns:
        str: The produced YAML string if `stream` is None, `None` otherwise.
    """
    return serialize_all([node], stream, Dumper=Dumper, **kwds)


def dump_all(  # noqa: ANN201, PLR0913, PLR0917
    documents,  # noqa: ANN001
    stream=None,  # noqa: ANN001
    Dumper=Dumper,  # noqa: ANN001, F405, N803
    default_style=None,  # noqa: ANN001
    default_flow_style=False,  # noqa: ANN001
    canonical=None,  # noqa: ANN001
    indent=None,  # noqa: ANN001
    width=None,  # noqa: ANN001
    allow_unicode=None,  # noqa: ANN001
    line_break=None,  # noqa: ANN001
    encoding=None,  # noqa: ANN001
    explicit_start=None,  # noqa: ANN001
    explicit_end=None,  # noqa: ANN001
    version=None,  # noqa: ANN001
    tags=None,  # noqa: ANN001
    sort_keys=True,  # noqa: ANN001
):
    """Serialize a sequence of Python objects into YAML and write it to the given stream or return the serialized content when no stream is provided.

    If `stream` is None, the function returns the produced YAML: a `str` when `encoding` is None, or `bytes` when `encoding` is provided.

    Parameters:
        stream: Optional file-like object to write output to. If omitted, an in-memory text or binary buffer is used and its contents are returned.
        Dumper: Dumper class to use for serialization.
        encoding: If provided, output is produced as `bytes`; otherwise output is `str`.
        sort_keys: When True, mapping keys are sorted before serialization; when False, insertion order is preserved.

    Returns:
        `str` if `stream` is None and `encoding` is None, `bytes` if `stream` is None and `encoding` is provided, or `None` when writing to a provided stream.
    """  # noqa: E501
    getvalue = None
    if stream is None:
        stream = io.StringIO() if encoding is None else io.BytesIO()
        getvalue = stream.getvalue
    dumper = Dumper(
        stream,
        default_style=default_style,
        default_flow_style=default_flow_style,
        canonical=canonical,
        indent=indent,
        width=width,
        allow_unicode=allow_unicode,
        line_break=line_break,
        encoding=encoding,
        version=version,
        tags=tags,
        explicit_start=explicit_start,
        explicit_end=explicit_end,
        sort_keys=sort_keys,
    )
    try:
        dumper.open()
        for data in documents:
            dumper.represent(data)
        dumper.close()
    finally:
        dumper.dispose()
    if getvalue:
        return getvalue()
    return None


def dump(data, stream=None, Dumper=Dumper, **kwds):  # noqa: ANN001, ANN003, ANN201, F405, N803
    """Serialize a Python object to YAML.

    If `stream` is provided, write the YAML output to that file-like object; if `stream` is `None`, return the YAML string.

    Parameters:
        stream (io.TextIOBase | io.BufferedIOBase | None): Optional file-like object to write the YAML to. When `None`, the function returns the produced YAML string.
        Dumper (type): Dumper class to use for serialization (defaults to module-level `Dumper`).
        **kwds: Additional dumper configuration options (e.g., `default_style`, `encoding`, `explicit_start`, `tags`).

    Returns:
        str or None: The YAML string when `stream` is `None`, otherwise `None`.
    """  # noqa: E501
    return dump_all([data], stream, Dumper=Dumper, **kwds)


def safe_dump_all(documents, stream=None, **kwds):  # noqa: ANN001, ANN003, ANN201
    """Serialize a sequence of Python objects to YAML using only basic YAML tags.

    Parameters:
        documents (iterable): Sequence of Python objects to serialize.
        stream (io.TextIO|io.BufferedIOBase|None): Destination to write YAML to. If `None`, the YAML string is returned.
        **kwds: Additional dumper options (e.g., `default_style`, `indent`, `allow_unicode`, `encoding`, `explicit_start`, `explicit_end`, `version`, `tags`, `sort_keys`) forwarded to the dumper.

    Returns:
        str|None: The YAML string when `stream` is `None`, otherwise `None`.
    """  # noqa: E501
    return dump_all(documents, stream, Dumper=SafeDumper, **kwds)  # noqa: F405


def safe_dump(data, stream=None, **kwds):  # noqa: ANN001, ANN003, ANN201
    """Serialize a Python object into a YAML document using the safe dumper (basic YAML tags only).

    Parameters:
        stream (IO[str] | None): Destination stream to write YAML to. If `None`, the YAML text is returned.

    Returns:
        str: The YAML document when `stream` is `None`; `None` otherwise.
    """  # noqa: E501
    return dump_all([data], stream, Dumper=SafeDumper, **kwds)  # noqa: F405


def add_implicit_resolver(tag, regexp, first=None, Loader=None, Dumper=Dumper) -> None:  # noqa: ANN001, F405, N803
    """Register an implicit scalar resolver that assigns `tag` to scalars matching `regexp`.

    Registers the resolver on the provided `Loader` (or on the default Loader, FullLoader, and UnsafeLoader when `Loader` is None) and on `Dumper`. Scalars whose text matches `regexp` will be assigned `tag`; `first` can restrict the check to scalars beginning with any character in the given sequence or be `None` to skip this optimization.

    Parameters:
        tag (str): The YAML tag to assign to matching scalars.
        regexp (str or Pattern): A regular expression (string or compiled pattern) used to detect matching scalar values.
        first (iterable[str] or None): Optional sequence of possible initial characters to pre-filter candidates, or `None` to disable this optimization.
        Loader (type or None): Loader class to register the resolver on; if `None`, registers on the module's default loaders.
        Dumper (type): Dumper class to register the resolver on (defaults to the module-level `Dumper`).
    """  # noqa: E501
    if Loader is None:
        _loader.Loader.add_implicit_resolver(tag, regexp, first)
        _loader.FullLoader.add_implicit_resolver(tag, regexp, first)
        _loader.UnsafeLoader.add_implicit_resolver(tag, regexp, first)
    else:
        Loader.add_implicit_resolver(tag, regexp, first)
    Dumper.add_implicit_resolver(tag, regexp, first)


def add_path_resolver(tag, path, kind=None, Loader=None, Dumper=Dumper) -> None:  # noqa: ANN001, F405, N803
    """Register a path-based resolver that associates `tag` with nodes matching `path` in the representation tree.

    A `path` is a sequence of keys (strings, integers, or `None`) that describes a path from the document root to a node; the resolver will apply when a node is reached at that path. The optional `kind` restricts the resolver to nodes of a particular kind.

    Parameters:
        tag (str): The YAML tag to assign when the resolver matches.
        path (list): Sequence of keys (str, int, or None) defining the path to match in the representation tree.
        kind (optional): Node kind to restrict the resolver (e.g., mapping, sequence, scalar).
        Loader (optional): Loader class to register the resolver on; if `None`, the resolver is registered on the module's default loaders.
        Dumper (optional): Dumper class to register the resolver on (defaults to the module's `Dumper`).
    """  # noqa: E501
    if Loader is None:
        _loader.Loader.add_path_resolver(tag, path, kind)
        _loader.FullLoader.add_path_resolver(tag, path, kind)
        _loader.UnsafeLoader.add_path_resolver(tag, path, kind)
    else:
        Loader.add_path_resolver(tag, path, kind)
    Dumper.add_path_resolver(tag, path, kind)


def add_constructor(tag, constructor, Loader=None) -> None:  # noqa: ANN001, N803
    """Register a constructor function for a YAML tag on one or more Loader classes.

    If `Loader` is omitted, the constructor is added to the module's default loaders (loader.Loader, loader.FullLoader, loader.UnsafeLoader); otherwise it is added to the specified `Loader`.

    Parameters:
        tag (str): YAML tag to associate with the constructor.
        constructor (callable): Function that accepts a Loader instance and a node, and returns the constructed Python object.
        Loader (type | object, optional): Specific loader class or loader instance to register the constructor on. Omit to register on the default loaders.
    """  # noqa: E501
    if Loader is None:
        _loader.Loader.add_constructor(tag, constructor)
        _loader.FullLoader.add_constructor(tag, constructor)
        _loader.UnsafeLoader.add_constructor(tag, constructor)
    else:
        Loader.add_constructor(tag, constructor)


def add_multi_constructor(tag_prefix, multi_constructor, Loader=None) -> None:  # noqa: ANN001, N803
    """Register a multi-constructor for YAML tags that start with the given prefix.

    When a node's tag begins with `tag_prefix`, the `multi_constructor` will be invoked
    with three arguments: the loader instance, the tag suffix (the part after
    `tag_prefix`), and the node; it must return the corresponding Python object.

    Parameters:
        tag_prefix (str): Tag prefix to match (e.g. "!foo/").
        multi_constructor (callable): Callable with signature
            `(loader, tag_suffix, node) -> object`.
        Loader (type | None): If provided, register the multi-constructor on that
            loader class; if `None`, register on the module's default loaders
            (`Loader`, `FullLoader`, and `UnsafeLoader`).
    """
    if Loader is None:
        _loader.Loader.add_multi_constructor(tag_prefix, multi_constructor)
        _loader.FullLoader.add_multi_constructor(tag_prefix, multi_constructor)
        _loader.UnsafeLoader.add_multi_constructor(tag_prefix, multi_constructor)
    else:
        Loader.add_multi_constructor(tag_prefix, multi_constructor)


def add_representer(data_type, representer, Dumper=Dumper) -> None:  # noqa: ANN001, F405, N803
    """Register a representer function to convert objects of a type into a YAML node.

    Parameters:
        data_type (type): The Python type to register the representer for.
        representer (callable): Function taking a Dumper instance and an object of `data_type`, returning a representation node.
        Dumper (type): Dumper class to register the representer on (defaults to the module-level `Dumper`).
    """  # noqa: E501
    Dumper.add_representer(data_type, representer)


def add_multi_representer(data_type, multi_representer, Dumper=Dumper) -> None:  # noqa: ANN001, F405, N803
    """Register a multi-representer for a data type on the specified Dumper.

    A multi-representer is called for instances of the given data type or its subclasses and must return a representation node for the dumper.

    Parameters:
        data_type (type or tuple[type, ...]): The Python type (or tuple of types) to register the multi-representer for.
        multi_representer (callable): A function with signature `(dumper, data)` that returns a node representing `data`.
        Dumper (type): The Dumper class on which to register the multi-representer.
    """  # noqa: E501
    Dumper.add_multi_representer(data_type, multi_representer)


class YAMLObjectMetaclass(type):
    """The metaclass for YAMLObject."""

    def __init__(cls, name, bases, kwds) -> None:  # noqa: ANN001
        """Initialize the metaclass and, if a `yaml_tag` is provided, register the class with the YAML loaders and dumper.

        If `kwds` contains a non-None `yaml_tag`, registers `cls.from_yaml` as the constructor for that tag on each loader listed in `cls.yaml_loader` (or on the single loader object), and registers `cls.to_yaml` as the representer for `cls` on `cls.yaml_dumper`.

        Parameters:
            name: The class name being created.
            bases: The base classes of the class being created.
            kwds: Keyword arguments passed to the metaclass; may include `yaml_tag` to enable automatic YAML registration.
        """  # noqa: E501
        super().__init__(name, bases, kwds)
        if "yaml_tag" in kwds and kwds["yaml_tag"] is not None:
            if isinstance(cls.yaml_loader, list):
                for loader in cls.yaml_loader:
                    loader.add_constructor(cls.yaml_tag, cls.from_yaml)
            else:
                cls.yaml_loader.add_constructor(cls.yaml_tag, cls.from_yaml)

            cls.yaml_dumper.add_representer(cls, cls.to_yaml)


class YAMLObject(metaclass=YAMLObjectMetaclass):
    """An object that can dump itself to a YAML stream
    and load itself from a YAML stream.
    """  # noqa: D205

    __slots__ = ()  # no direct instantiation, so allow immutable subclasses

    yaml_loader = [Loader, FullLoader, UnsafeLoader]  # noqa: F405, RUF012
    yaml_dumper = Dumper  # noqa: F405

    yaml_tag = None
    yaml_flow_style = None

    @classmethod
    def from_yaml(cls, loader, node):  # noqa: ANN001, ANN206
        """Construct an instance of the class from a YAML representation node.

        Parameters:
            loader: The loader instance used to construct Python objects from nodes.
            node: The representation node describing the object.

        Returns:
            An instance of `cls` constructed from `node`.
        """
        return loader.construct_yaml_object(node, cls)

    @classmethod
    def to_yaml(cls, dumper, data):  # noqa: ANN001, ANN206
        """Represent an instance of the class as a YAML node using the class's `yaml_tag`.

        Parameters:
            dumper: The Dumper instance used to create representation nodes.
            data: The instance of the class to represent.

        Returns:
            A YAML node representing `data`, using `cls.yaml_tag` and `cls.yaml_flow_style`.
        """  # noqa: E501
        return dumper.represent_yaml_object(
            cls.yaml_tag, data, cls, flow_style=cls.yaml_flow_style
        )
