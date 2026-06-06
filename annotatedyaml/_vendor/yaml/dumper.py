__all__ = ["BaseDumper", "Dumper", "SafeDumper"]  # noqa: D100

from .emitter import *  # noqa: F403
from .representer import *  # noqa: F403
from .resolver import *  # noqa: F403
from .serializer import *  # noqa: F403


class BaseDumper(Emitter, Serializer, BaseRepresenter, BaseResolver):  # noqa: D101, F405
    def __init__(  # noqa: D107, PLR0913, PLR0917
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
    def __init__(  # noqa: D107, PLR0913, PLR0917
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
    def __init__(  # noqa: D107, PLR0913, PLR0917
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
