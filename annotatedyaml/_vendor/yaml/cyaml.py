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
    def __init__(self, stream) -> None:  # noqa: ANN001, D107
        CParser.__init__(self, stream)
        BaseConstructor.__init__(self)  # noqa: F405
        BaseResolver.__init__(self)  # noqa: F405


class CSafeLoader(CParser, SafeConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001, D107
        CParser.__init__(self, stream)
        SafeConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class CFullLoader(CParser, FullConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001, D107
        CParser.__init__(self, stream)
        FullConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class CUnsafeLoader(CParser, UnsafeConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001, D107
        CParser.__init__(self, stream)
        UnsafeConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class CLoader(CParser, Constructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001, D107
        CParser.__init__(self, stream)
        Constructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class CBaseDumper(CEmitter, BaseRepresenter, BaseResolver):  # noqa: D101, F405
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
