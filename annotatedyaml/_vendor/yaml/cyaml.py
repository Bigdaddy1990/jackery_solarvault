__all__ = [  # noqa: D100
    "CBaseDumper",
    "CBaseLoader",
    "CDumper",
    "CFullLoader",
    "CLoader",
    "CSafeDumper",
    "CSafeLoader",
    "CUnsafeLoader",
]

from yaml._yaml import CEmitter, CParser

from .constructor import *  # noqa: F403
from .representer import *  # noqa: F403
from .resolver import *  # noqa: F403
from .serializer import *  # noqa: F403


class CBaseLoader(CParser, BaseConstructor, BaseResolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Initialize the C-backed base YAML loader.

        Parameters:
            stream: YAML input — a text string, bytes, or a file-like object providing YAML content.
        """  # noqa: E501
        CParser.__init__(self, stream)
        BaseConstructor.__init__(self)  # noqa: F405
        BaseResolver.__init__(self)  # noqa: F405


class CSafeLoader(CParser, SafeConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Initialize a safe C-based YAML loader for the given input stream.

        Parameters:
            stream: YAML input to parse — typically a file-like object, string, or bytes containing YAML content.
        """  # noqa: E501
        CParser.__init__(self, stream)
        SafeConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class CFullLoader(CParser, FullConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Initialize a C-accelerated "full" YAML loader configured for the given input stream.

        Parameters:
            stream: The YAML input source (e.g., a text/byte string or a file-like object) to be parsed.
        """  # noqa: E501
        CParser.__init__(self, stream)
        FullConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class CUnsafeLoader(CParser, UnsafeConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Initialize a YAML loader that parses `stream` and constructs Python objects using unsafe construction rules.

        Parameters:
            stream: A text or binary file-like object or string containing YAML content to be loaded.
        """  # noqa: E501
        CParser.__init__(self, stream)
        UnsafeConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class CLoader(CParser, Constructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Initialize the default C-backed YAML loader with parsing, construction, and resolution components.

        Parameters:
            stream: Input source for YAML content; a text or binary stream or path-like object accepted by the underlying C parser.
        """  # noqa: E501
        CParser.__init__(self, stream)
        Constructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class CBaseDumper(CEmitter, BaseRepresenter, BaseResolver):  # noqa: D101, F405
    def __init__(  # noqa: PLR0913, PLR0917
        self,
        stream,  # noqa: ANN001
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
    ) -> None:
        r"""Initialize the dumper with C-based emitter behavior and representer/resolver configuration.

        Parameters:
            stream: IO-like target to write the emitted YAML to.
            default_style: Default scalar style (e.g., '|', '>', '"', "'") or None to use no explicit style.
            default_flow_style: If True, prefer flow style for collections; if False, prefer block style.
            canonical: If True, produce canonical YAML form; if None, use default emitter behavior.
            indent: Number of spaces to indent nested structures, or None to use the emitter default.
            width: Preferred line width for scalar folding, or None to disable line wrapping.
            allow_unicode: If True, allow non-ASCII characters directly; if False, escape them; None uses default.
            line_break: Line break style to use (e.g., '\n', '\r\n') or None to use platform default.
            encoding: Output encoding to declare for the stream, or None to omit encoding declaration.
            explicit_start: If True, write explicit document start marker ("---"); if False/None, omit unless needed.
            explicit_end: If True, write explicit document end marker ("..."); if False/None, omit.
            version: YAML version tuple (major, minor) to emit, or None to omit version directive.
            tags: Mapping of tag prefixes to URIs for tag resolution, or None to use defaults.
            sort_keys: If True, sort mapping keys when representing mappings; if False, preserve insertion order.
        """  # noqa: E501
        CEmitter.__init__(
            self,
            stream,
            canonical=canonical,
            indent=indent,
            width=width,
            encoding=encoding,
            allow_unicode=allow_unicode,
            line_break=line_break,
            explicit_start=explicit_start,
            explicit_end=explicit_end,
            version=version,
            tags=tags,
        )
        Representer.__init__(  # noqa: F405
            self,
            default_style=default_style,
            default_flow_style=default_flow_style,
            sort_keys=sort_keys,
        )
        Resolver.__init__(self)  # noqa: F405


class CSafeDumper(CEmitter, SafeRepresenter, Resolver):  # noqa: D101, F405
    def __init__(  # noqa: PLR0913, PLR0917
        self,
        stream,  # noqa: ANN001
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
    ) -> None:
        r"""Initialize a safe C-backed YAML dumper with configurable emitter and representer options.

        Parameters:
            stream: Output stream or file-like object to write YAML to.
            default_style (str | None): Default scalar style (e.g., '|', '>', '"') or None to use no default.
            default_flow_style (bool): Whether to use flow style by default for collections.
            canonical (bool | None): Whether to produce canonical form; None leaves emitter default unchanged.
            indent (int | None): Number of spaces used for indentation; None leaves emitter default unchanged.
            width (int | None): Preferred line width for folding; None leaves emitter default unchanged.
            allow_unicode (bool | None): Whether to allow non-ASCII characters directly; None leaves emitter default unchanged.
            line_break (str | None): Line break type to use (e.g., '\n', '\r\n'); None leaves emitter default unchanged.
            encoding (str | None): Output encoding for byte-oriented streams; None leaves emitter default unchanged.
            explicit_start (bool | None): Whether to explicitly emit start marker '---'; None leaves emitter default unchanged.
            explicit_end (bool | None): Whether to explicitly emit end marker '...'; None leaves emitter default unchanged.
            version (tuple | None): YAML version tuple to emit (major, minor), or None to omit version.
            tags (dict | None): Tag handles mapping to tag URIs for the emitter.
            sort_keys (bool): Whether to sort mapping keys when representing mappings.
        """  # noqa: E501
        CEmitter.__init__(
            self,
            stream,
            canonical=canonical,
            indent=indent,
            width=width,
            encoding=encoding,
            allow_unicode=allow_unicode,
            line_break=line_break,
            explicit_start=explicit_start,
            explicit_end=explicit_end,
            version=version,
            tags=tags,
        )
        SafeRepresenter.__init__(  # noqa: F405
            self,
            default_style=default_style,
            default_flow_style=default_flow_style,
            sort_keys=sort_keys,
        )
        Resolver.__init__(self)  # noqa: F405


class CDumper(CEmitter, Serializer, Representer, Resolver):  # noqa: D101, F405
    def __init__(  # noqa: PLR0913, PLR0917
        self,
        stream,  # noqa: ANN001
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
    ) -> None:
        r"""Initialize the dumper with C-based emitter behavior and representer/resolver configuration.

        Parameters:
            stream: IO-like target to write the emitted YAML to.
            default_style: Default scalar style (e.g., '|', '>', '"', "'") or None to use no explicit style.
            default_flow_style: If True, prefer flow style for collections; if False, prefer block style.
            canonical: If True, produce canonical YAML form; if None, use default emitter behavior.
            indent: Number of spaces to indent nested structures, or None to use the emitter default.
            width: Preferred line width for scalar folding, or None to disable line wrapping.
            allow_unicode: If True, allow non-ASCII characters directly; if False, escape them; None uses default.
            line_break: Line break style to use (e.g., '\n', '\r\n') or None to use platform default.
            encoding: Output encoding to declare for the stream, or None to omit encoding declaration.
            explicit_start: If True, write explicit document start marker ("---"); if False/None, omit unless needed.
            explicit_end: If True, write explicit document end marker ("..."); if False/None, omit.
            version: YAML version tuple (major, minor) to emit, or None to omit version directive.
            tags: Mapping of tag prefixes to URIs for tag resolution, or None to use defaults.
            sort_keys: If True, sort mapping keys when representing mappings; if False, preserve insertion order.
        """  # noqa: E501
        CEmitter.__init__(
            self,
            stream,
            canonical=canonical,
            indent=indent,
            width=width,
            encoding=encoding,
            allow_unicode=allow_unicode,
            line_break=line_break,
            explicit_start=explicit_start,
            explicit_end=explicit_end,
            version=version,
            tags=tags,
        )
        Representer.__init__(  # noqa: F405
            self,
            default_style=default_style,
            default_flow_style=default_flow_style,
            sort_keys=sort_keys,
        )
        Resolver.__init__(self)  # noqa: F405
