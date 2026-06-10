__all__ = ["BaseLoader", "FullLoader", "Loader", "SafeLoader", "UnsafeLoader"]  # noqa: D100

from .composer import *  # noqa: F403
from .constructor import *  # noqa: F403
from .parser import *  # noqa: F403
from .reader import *  # noqa: F403
from .resolver import *  # noqa: F403
from .scanner import *  # noqa: F403


class BaseLoader(Reader, Scanner, Parser, Composer, BaseConstructor, BaseResolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Initialize the loader with the provided YAML input stream.

        Parameters:
            stream: A text or binary stream, or a string containing YAML content to be read by the loader.
        """  # noqa: E501
        Reader.__init__(self, stream)  # noqa: F405
        Scanner.__init__(self)  # noqa: F405
        Parser.__init__(self)  # noqa: F405
        Composer.__init__(self)  # noqa: F405
        BaseConstructor.__init__(self)  # noqa: F405
        BaseResolver.__init__(self)  # noqa: F405


class FullLoader(Reader, Scanner, Parser, Composer, FullConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Configure the full loader for the given YAML input stream.

        Parameters:
            stream: Input YAML as a string or a text/binary file-like object.
        """
        Reader.__init__(self, stream)  # noqa: F405
        Scanner.__init__(self)  # noqa: F405
        Parser.__init__(self)  # noqa: F405
        Composer.__init__(self)  # noqa: F405
        FullConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class SafeLoader(Reader, Scanner, Parser, Composer, SafeConstructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Configure the SafeLoader to read and construct YAML nodes from the given input.

        Parameters:
            stream: YAML input source (e.g., a string or a file-like object) to be consumed by the loader.
        """  # noqa: E501
        Reader.__init__(self, stream)  # noqa: F405
        Scanner.__init__(self)  # noqa: F405
        Parser.__init__(self)  # noqa: F405
        Composer.__init__(self)  # noqa: F405
        SafeConstructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405


class Loader(Reader, Scanner, Parser, Composer, Constructor, Resolver):  # noqa: D101, F405
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Initialize the legacy unsafe loader with a YAML input stream.

        Parameters:
            stream: YAML input as text, bytes, or a file-like object.
        """
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
    def __init__(self, stream) -> None:  # noqa: ANN001
        """Initialize the unsafe loader with a YAML input stream.

        Parameters:
            stream: YAML input as text, bytes, or a file-like object.
        """
        Reader.__init__(self, stream)  # noqa: F405
        Scanner.__init__(self)  # noqa: F405
        Parser.__init__(self)  # noqa: F405
        Composer.__init__(self)  # noqa: F405
        Constructor.__init__(self)  # noqa: F405
        Resolver.__init__(self)  # noqa: F405
