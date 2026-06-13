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
        """Create a C-accelerated base YAML loader configured with the given input stream.

        Parameters:
            stream: YAML input source — a text string, bytes, or a file-like object providing YAML content.
        """
        CParser.__init__(self, stream)
        BaseConstructor.__init__(self)  # noqa: F405
        BaseResolver.__init__(self)  # noqa: F405


class CSafeLoader(CParser, SafeConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Constructs a safe C-accelerated YAML loader for the provided input stream.

        Parameters:
            stream: YAML input to parse — typically a file-like object, string, or bytes containing YAML content.
        """
        CParser.__init__(self, stream)
        SafeConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class CFullLoader(CParser, FullConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Create a C-accelerated loader that applies full construction and tag resolution to the given input stream.

        Parameters:
            stream: YAML input source (text/bytes or a file-like object) to be parsed.
        """
        CParser.__init__(self, stream)
        FullConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class CUnsafeLoader(CParser, UnsafeConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Initialize a YAML loader that parses `stream` and constructs Python objects using unsafe construction rules.

        Parameters:
            stream: A text or binary file-like object or string containing YAML content to be loaded.
        """
        CParser.__init__(self, stream)
        UnsafeConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class CLoader(CParser, Constructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Initialize the default C-backed YAML loader with parsing, construction, and resolution components.

        Parameters:
            stream: Input source for YAML content; a text or binary stream or path-like object accepted by the underlying C parser.
        """
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
        r"""Initialize a C-accelerated YAML dumper configured for emission, representation, and resolution.

        Parameters:
            stream: IO-like target to write the emitted YAML to.
            default_style: Default scalar style (e.g., '|', '>', '"', "'") or None to use no explicit style.
            default_flow_style: Prefer flow style for collections when True; prefer block style when False.
            canonical: When True, produce canonical YAML form; when None, use the emitter default.
            indent: Number of spaces to indent nested structures, or None to use the emitter default.
            width: Preferred line width for scalar folding, or None to disable wrapping.
            allow_unicode: When True, allow non-ASCII characters directly; when False, escape them; None uses default.
            line_break: Line break sequence to use (e.g., '\n', '\r\n') or None to use platform default.
            encoding: Output encoding to declare for the stream, or None to omit an encoding directive.
            explicit_start: When True, write an explicit document start marker ("---"); when False/None, omit unless needed.
            explicit_end: When True, write an explicit document end marker ("..."); when False/None, omit.
            version: YAML version tuple (major, minor) to emit, or None to omit a version directive.
            tags: Mapping of tag prefixes to URIs for tag resolution, or None to use defaults.
            sort_keys: When True, sort mapping keys when representing mappings; when False, preserve insertion order.
        """
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
        r"""Create a safe C-backed YAML dumper configured with emitter and representer options.

        Parameters:
            stream: Output stream or file-like object to write YAML to.
            default_style (str | None): Default scalar style (e.g., '|', '>', '"') or None to use no default.
            default_flow_style (bool): Whether collections use flow style by default.
            canonical (bool | None): Produce canonical form when True; None leaves emitter default unchanged.
            indent (int | None): Number of spaces for indentation; None leaves emitter default unchanged.
            width (int | None): Preferred line width for folding; None leaves emitter default unchanged.
            allow_unicode (bool | None): Allow non-ASCII characters directly when True; None leaves emitter default unchanged.
            line_break (str | None): Line break sequence to use (e.g., '\n', '\r\n'); None leaves emitter default unchanged.
            encoding (str | None): Output encoding for byte-oriented streams; None leaves emitter default unchanged.
            explicit_start (bool | None): Emit start marker '---' when True; None leaves emitter default unchanged.
            explicit_end (bool | None): Emit end marker '...' when True; None leaves emitter default unchanged.
            version (tuple | None): YAML version tuple to emit (major, minor), or None to omit version.
            tags (dict | None): Mapping of tag handles to tag URIs for the emitter.
            sort_keys (bool): Whether to sort mapping keys when representing mappings.
        """
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
        r"""Initialize a C-accelerated YAML dumper configured for emission, representation, and resolution.

        Parameters:
            stream: IO-like target to write the emitted YAML to.
            default_style: Default scalar style (e.g., '|', '>', '"', "'") or None to use no explicit style.
            default_flow_style: Prefer flow style for collections when True; prefer block style when False.
            canonical: When True, produce canonical YAML form; when None, use the emitter default.
            indent: Number of spaces to indent nested structures, or None to use the emitter default.
            width: Preferred line width for scalar folding, or None to disable wrapping.
            allow_unicode: When True, allow non-ASCII characters directly; when False, escape them; None uses default.
            line_break: Line break sequence to use (e.g., '\n', '\r\n') or None to use platform default.
            encoding: Output encoding to declare for the stream, or None to omit an encoding directive.
            explicit_start: When True, write an explicit document start marker ("---"); when False/None, omit unless needed.
            explicit_end: When True, write an explicit document end marker ("..."); when False/None, omit.
            version: YAML version tuple (major, minor) to emit, or None to omit a version directive.
            tags: Mapping of tag prefixes to URIs for tag resolution, or None to use defaults.
            sort_keys: When True, sort mapping keys when representing mappings; when False, preserve insertion order.
        """
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
