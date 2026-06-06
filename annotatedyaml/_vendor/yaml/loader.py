__all__ = ["BaseLoader", "FullLoader", "Loader", "SafeLoader", "UnsafeLoader"]  # noqa: D100

from .composer import *  # noqa: F403
from .constructor import *  # noqa: F403
from .parser import *  # noqa: F403
from .reader import *  # noqa: F403
from .resolver import *  # noqa: F403
from .scanner import *  # noqa: F403


class BaseLoader(Reader, Scanner, Parser, Composer, BaseConstructor, BaseResolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001, D107
        Reader.__init__(self, stream)  # noqa: F405
        Scanner.__init__(self)  # noqa: F405
        Parser.__init__(self)  # noqa: F405
        Composer.__init__(self)  # noqa: F405
        BaseConstructor.__init__(self)  # noqa: F405
        BaseResolver.__init__(self)  # noqa: F405


class FullLoader(Reader, Scanner, Parser, Composer, FullConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001, D107
        Reader.__init__(self, stream)  # noqa: F405
        Scanner.__init__(self)  # noqa: F405
        Parser.__init__(self)  # noqa: F405
        Composer.__init__(self)  # noqa: F405
        FullConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class SafeLoader(Reader, Scanner, Parser, Composer, SafeConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001, D107
        Reader.__init__(self, stream)  # noqa: F405
        Scanner.__init__(self)  # noqa: F405
        Parser.__init__(self)  # noqa: F405
        Composer.__init__(self)  # noqa: F405
        SafeConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class Loader(Reader, Scanner, Parser, Composer, Constructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001, D107
        Reader.__init__(self, stream)  # noqa: F405
        Scanner.__init__(self)  # noqa: F405
        Parser.__init__(self)  # noqa: F405
        Composer.__init__(self)  # noqa: F405
        Constructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


# UnsafeLoader is the same as Loader (which is and was always unsafe on
# untrusted input). Use of either Loader or UnsafeLoader should be rare, since
# FullLoad should be able to load almost all YAML safely. Loader is left intact
# to ensure backwards compatibility.
class UnsafeLoader(Reader, Scanner, Parser, Composer, Constructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001, D107
        Reader.__init__(self, stream)  # noqa: F405
        Scanner.__init__(self)  # noqa: F405
        Parser.__init__(self)  # noqa: F405
        Composer.__init__(self)  # noqa: F405
        Constructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405
