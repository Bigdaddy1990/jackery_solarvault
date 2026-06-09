__all__ = ["BaseDumper", "Dumper", "SafeDumper"]  # noqa: D100

from .emitter import *  # noqa: F403
from .representer import *  # noqa: F403
from .resolver import *  # noqa: F403
from .serializer import *  # noqa: F403


class BaseDumper(Emitter, Serializer, BaseRepresenter, BaseResolver):  # noqa: D101, F405
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
        """
        Initialize the dumper with the output stream and YAML formatting/serialization options.
        
        Parameters:
            stream: Output stream or file-like object to write YAML to.
            default_style: Preferred scalar style or None to use node-specific styles.
            default_flow_style: Use flow style for collections when True.
            canonical: Emit YAML in canonical form when True.
            indent: Number of spaces to use for indentation.
            width: Preferred line width for wrapping.
            allow_unicode: Allow non-ASCII characters in output when True.
            line_break: Line break style to use.
            encoding: Character encoding for the output.
            explicit_start: Emit an explicit start marker (`---`) when True.
            explicit_end: Emit an explicit end marker (`...`) when True.
            version: YAML version tuple to include in the document (for example, (1, 2)).
            tags: Mapping of tag handles to tag prefixes for the serializer.
            sort_keys: Sort mapping keys before emitting when True.
        """  # noqa: E501
        Emitter.__init__(  # noqa: F405
            self,
            stream,
            canonical=canonical,
            indent=indent,
            width=width,
            allow_unicode=allow_unicode,
            line_break=line_break,
        )
        Serializer.__init__(  # noqa: F405
            self,
            encoding=encoding,
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


class SafeDumper(Emitter, Serializer, SafeRepresenter, Resolver):  # noqa: D101, F405
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
        """Initialize the dumper with emitter, serializer, representer, and resolver configuration.

        Parameters:
            stream: Output stream or file-like object where YAML will be written.
            default_style: Default scalar style to use when representing values (e.g., '|', '>' or None).
            default_flow_style: If True, use flow style for collections by default; otherwise use block style.
            canonical: If True, produce the canonical YAML form.
            indent: Number of spaces to use for indentation.
            width: Preferred line width for folding long lines.
            allow_unicode: If True, allow non-ASCII characters in output.
            line_break: Line break sequence to use in output.
            encoding: Encoding name for the serialized output (if applicable).
            explicit_start: If True, emit an explicit document start marker.
            explicit_end: If True, emit an explicit document end marker.
            version: YAML version tuple to include in the document header (e.g., (1, 2)) or None.
            tags: Mapping of tag handles to tag prefixes to include in the document.
            sort_keys: If True, sort mapping keys when representing mappings.
        """  # noqa: E501
        Emitter.__init__(  # noqa: F405
            self,
            stream,
            canonical=canonical,
            indent=indent,
            width=width,
            allow_unicode=allow_unicode,
            line_break=line_break,
        )
        Serializer.__init__(  # noqa: F405
            self,
            encoding=encoding,
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


class Dumper(Emitter, Serializer, Representer, Resolver):  # noqa: D101, F405
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
        """
        Initialize the dumper with the output stream and YAML formatting/serialization options.
        
        Parameters:
            stream: Output stream or file-like object to write YAML to.
            default_style: Preferred scalar style or None to use node-specific styles.
            default_flow_style: Use flow style for collections when True.
            canonical: Emit YAML in canonical form when True.
            indent: Number of spaces to use for indentation.
            width: Preferred line width for wrapping.
            allow_unicode: Allow non-ASCII characters in output when True.
            line_break: Line break style to use.
            encoding: Character encoding for the output.
            explicit_start: Emit an explicit start marker (`---`) when True.
            explicit_end: Emit an explicit end marker (`...`) when True.
            version: YAML version tuple to include in the document (for example, (1, 2)).
            tags: Mapping of tag handles to tag prefixes for the serializer.
            sort_keys: Sort mapping keys before emitting when True.
        """  # noqa: E501
        Emitter.__init__(  # noqa: F405
            self,
            stream,
            canonical=canonical,
            indent=indent,
            width=width,
            allow_unicode=allow_unicode,
            line_break=line_break,
        )
        Serializer.__init__(  # noqa: F405
            self,
            encoding=encoding,
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
