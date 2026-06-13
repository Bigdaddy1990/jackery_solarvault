"""Tests for annotatedyaml/_vendor/yaml changes introduced in this PR.

Covers:
- warnings() stub function behavior
- add_implicit_resolver() registering on correct loaders/dumpers
- add_path_resolver() registering on correct loaders/dumpers
- add_constructor() registering on correct loaders
- add_multi_constructor() registering on correct loaders
- add_representer() and add_multi_representer() registering on dumpers
- construct_yaml_binary() base64 decoding (direct decodebytes, no hasattr guard)
- construct_python_bytes() base64 decoding (same simplification)
"""

import base64
import re
from unittest.mock import MagicMock

from annotatedyaml._vendor import yaml  # noqa: PLC2701
from annotatedyaml._vendor.yaml import load as _yaml_load, loader as _loader
from annotatedyaml._vendor.yaml.constructor import (  # noqa: PLC2701
    ConstructorError,
    FullConstructor,
    SafeConstructor,
)
from annotatedyaml._vendor.yaml.nodes import ScalarNode  # noqa: PLC2701
import pytest


def _load_with_loader(stream: str, loader: type[object]) -> object:
    """Load test YAML through a specific vendored loader API under test."""
    return _yaml_load(stream, Loader=loader)  # nosec B506

# ---------------------------------------------------------------------------
# warnings() stub
# ---------------------------------------------------------------------------


class TestWarnings:
    """Tests for the deprecated warnings() stub function."""

    def test_warnings_no_args_returns_empty_dict(self) -> None:  # noqa: PLR6301
        """warnings() with no arguments should return an empty dict."""
        result = yaml.warnings()
        assert result == {}
        assert isinstance(result, dict)

    def test_warnings_none_arg_returns_empty_dict(self) -> None:  # noqa: PLR6301
        """warnings(None) is equivalent to no-arg call and returns an empty dict."""
        result = yaml.warnings(None)
        assert result == {}

    def test_warnings_with_dict_arg_returns_none(self) -> None:  # noqa: PLR6301
        """warnings({...}) should return None (no-op for non-None settings)."""
        result = yaml.warnings({"CheckUTF8": True})
        assert result is None

    def test_warnings_with_empty_dict_arg_returns_none(self) -> None:  # noqa: PLR6301
        """warnings({}) — empty dict is not None, so should return None."""
        result = yaml.warnings({})
        assert result is None

    def test_warnings_with_falsy_nonempty_arg_returns_none(self) -> None:  # noqa: PLR6301
        """Any non-None value for settings, including 0 or False, returns None."""
        assert yaml.warnings(0) is None
        assert yaml.warnings(False) is None
        assert yaml.warnings("") is None


# ---------------------------------------------------------------------------
# add_constructor()
# ---------------------------------------------------------------------------


class TestAddConstructor:
    """Tests for add_constructor() registering on default and specific loaders."""

    def test_add_constructor_loader_none_registers_on_all_default_loaders(self) -> None:  # noqa: PLR6301
        """add_constructor(tag, fn) with Loader=None must register on Loader, FullLoader, and UnsafeLoader."""  # noqa: E501
        tag = "!test_add_constructor_all_loaders_unique_9f3a"
        sentinel = object()
        constructor_fn = lambda loader, node: sentinel  # noqa: E731

        yaml.add_constructor(tag, constructor_fn, Loader=None)

        assert tag in _loader.Loader.yaml_constructors
        assert tag in _loader.FullLoader.yaml_constructors
        assert tag in _loader.UnsafeLoader.yaml_constructors

        # Functional check: loading a node with this tag returns the sentinel
        result = _load_with_loader(f"{tag} value", _loader.Loader)
        assert result is sentinel

    def test_add_constructor_specific_loader_registers_only_on_that_loader(  # noqa: PLR6301
        self,
    ) -> None:
        """add_constructor(tag, fn, Loader=X) must register only on X, not on the other defaults."""  # noqa: E501
        tag = "!test_add_constructor_specific_loader_unique_8b2c"
        called_with = []

        def constructor_fn(loader, node) -> str:  # noqa: ANN001
            called_with.append(loader)
            return "custom_value"

        # Use a fresh subclass to avoid polluting global Loader state
        class IsolatedLoader(_loader.SafeLoader):
            pass

        yaml.add_constructor(tag, constructor_fn, Loader=IsolatedLoader)

        assert tag in IsolatedLoader.yaml_constructors
        assert tag not in _loader.Loader.yaml_constructors
        assert tag not in _loader.FullLoader.yaml_constructors
        assert tag not in _loader.UnsafeLoader.yaml_constructors

    def test_add_constructor_loader_none_functional_round_trip(self) -> None:  # noqa: PLR6301
        """Constructor registered with Loader=None is invoked when loading via safe_load-equivalent."""  # noqa: E501
        tag = "!test_constructor_round_trip_unique_7d1e"
        expected = {"decoded": True}

        def my_constructor(loader, node):  # noqa: ANN001, ANN202
            return expected

        yaml.add_constructor(tag, my_constructor, Loader=None)

        result = _load_with_loader(f"{tag} anything", _loader.Loader)
        assert result is expected

    def test_add_constructor_specific_loader_end_to_end(self) -> None:  # noqa: PLR6301
        """Constructor registered on a specific loader subclass is invoked correctly."""
        tag = "!test_constructor_specific_e2e_unique_4a7f"
        return_value = [1, 2, 3]

        class MyLoader(_loader.SafeLoader):
            pass

        yaml.add_constructor(tag, lambda loader, node: return_value, Loader=MyLoader)

        result = _load_with_loader(f"{tag} whatever", MyLoader)
        assert result is return_value


# ---------------------------------------------------------------------------
# add_multi_constructor()
# ---------------------------------------------------------------------------


class TestAddMultiConstructor:
    """Tests for add_multi_constructor() registering on default and specific loaders."""

    def test_add_multi_constructor_loader_none_registers_on_all_defaults(self) -> None:  # noqa: PLR6301
        """add_multi_constructor with Loader=None must register on Loader, FullLoader, and UnsafeLoader."""  # noqa: E501
        prefix = "!testmulti_all_unique_6c2d/"
        multi_fn = lambda loader, suffix, node: f"matched:{suffix}"  # noqa: E731

        yaml.add_multi_constructor(prefix, multi_fn, Loader=None)

        assert prefix in _loader.Loader.yaml_multi_constructors
        assert prefix in _loader.FullLoader.yaml_multi_constructors
        assert prefix in _loader.UnsafeLoader.yaml_multi_constructors

    def test_add_multi_constructor_specific_loader_only_registers_on_that_loader(  # noqa: PLR6301
        self,
    ) -> None:
        """add_multi_constructor with Loader=X only registers on X."""
        prefix = "!testmulti_specific_unique_3e9b/"

        class IsolatedLoader(_loader.SafeLoader):
            pass

        multi_fn = lambda loader, suffix, node: suffix  # noqa: E731
        yaml.add_multi_constructor(prefix, multi_fn, Loader=IsolatedLoader)

        assert prefix in IsolatedLoader.yaml_multi_constructors
        assert prefix not in _loader.Loader.yaml_multi_constructors
        assert prefix not in _loader.FullLoader.yaml_multi_constructors
        assert prefix not in _loader.UnsafeLoader.yaml_multi_constructors

    def test_add_multi_constructor_functional_with_default_loaders(self) -> None:  # noqa: PLR6301
        """Multi-constructor registered via add_multi_constructor(Loader=None) is invoked during load."""  # noqa: E501
        prefix = "!testmulti_func_unique_1b8e/"
        results: list[str] = []

        def multi_fn(loader, suffix, node) -> str:  # noqa: ANN001
            results.append(suffix)
            return f"value:{suffix}"

        yaml.add_multi_constructor(prefix, multi_fn, Loader=None)
        result = _load_with_loader(f"{prefix}mysuffix value", _loader.Loader)
        assert "mysuffix" in results
        assert result == "value:mysuffix"


# ---------------------------------------------------------------------------
# add_implicit_resolver()
# ---------------------------------------------------------------------------


class TestAddImplicitResolver:
    """Tests for add_implicit_resolver() registering on loaders and dumper."""

    def test_add_implicit_resolver_loader_none_registers_on_all_loaders(self) -> None:  # noqa: PLR6301
        """add_implicit_resolver with Loader=None registers on Loader, FullLoader, UnsafeLoader, and Dumper."""  # noqa: E501
        tag = "tag:example.com,2024:test_implicit_resolver_unique_5f2a"
        regexp = re.compile(r"^__UNIQUE_5F2A__")
        first = ["_"]

        yaml.add_implicit_resolver(tag, regexp, first, Loader=None)

        # Check all three loaders have the resolver
        for loader_cls in (_loader.Loader, _loader.FullLoader, _loader.UnsafeLoader):
            resolvers_for_underscore = loader_cls.yaml_implicit_resolvers.get("_", [])
            tags_for_underscore = [t for t, _ in resolvers_for_underscore]
            assert tag in tags_for_underscore, (
                f"Expected tag '{tag}' in {loader_cls.__name__}.yaml_implicit_resolvers['_']"  # noqa: E501
            )

        # Also check Dumper
        dumper_resolvers = yaml.Dumper.yaml_implicit_resolvers.get("_", [])
        assert tag in [t for t, _ in dumper_resolvers]

    def test_add_implicit_resolver_with_specific_loader_only_registers_on_that_loader(  # noqa: PLR6301
        self,
    ) -> None:
        """add_implicit_resolver with a specific Loader registers only on that loader (plus Dumper)."""  # noqa: E501
        tag = "tag:example.com,2024:test_implicit_specific_unique_9a3c"
        regexp = re.compile(r"^__UNIQUE_9A3C__")

        class IsolatedLoader(_loader.SafeLoader):
            pass

        yaml.add_implicit_resolver(tag, regexp, None, Loader=IsolatedLoader)

        # Should be on IsolatedLoader
        wildcard_resolvers = IsolatedLoader.yaml_implicit_resolvers.get(None, [])
        assert tag in [t for t, _ in wildcard_resolvers]

        loader_wildcard = _loader.Loader.yaml_implicit_resolvers.get(None, [])
        full_loader_wildcard = _loader.FullLoader.yaml_implicit_resolvers.get(None, [])
        unsafe_loader_wildcard = _loader.UnsafeLoader.yaml_implicit_resolvers.get(
            None, []
        )  # noqa: E501, RUF100
        assert tag not in [t for t, _ in loader_wildcard]
        assert tag not in [t for t, _ in full_loader_wildcard]
        assert tag not in [t for t, _ in unsafe_loader_wildcard]

    def test_add_implicit_resolver_with_first_none_registers_under_wildcard_key(  # noqa: PLR6301
        self,
    ) -> None:
        """When first=None, the resolver is registered under the None key in yaml_implicit_resolvers."""  # noqa: E501
        tag = "tag:example.com,2024:test_implicit_wildcard_unique_2d7f"
        regexp = re.compile(r"^__UNIQUE_2D7F__")

        class IsolatedLoader(_loader.Loader):
            pass

        yaml.add_implicit_resolver(tag, regexp, None, Loader=IsolatedLoader)

        # first=None means "wildcard" → stored under key None
        wildcard_resolvers = IsolatedLoader.yaml_implicit_resolvers.get(None, [])
        assert tag in [t for t, _ in wildcard_resolvers]


# ---------------------------------------------------------------------------
# add_representer() and add_multi_representer()
# ---------------------------------------------------------------------------


class TestAddRepresenter:
    """Tests for add_representer() and add_multi_representer()."""

    def test_add_representer_registers_on_specified_dumper(self) -> None:  # noqa: PLR6301
        """add_representer registers the callable on the given Dumper class."""

        class MyCustomType:
            pass

        representer_fn = lambda d, v: None  # noqa: E731
        dumper_mock = MagicMock()
        yaml.add_representer(MyCustomType, representer_fn, Dumper=dumper_mock)
        dumper_mock.add_representer.assert_called_once_with(
            MyCustomType, representer_fn
        )

    def test_add_representer_default_dumper_round_trip(self) -> None:  # noqa: PLR6301
        """add_representer functional test: custom type is serialized with the registered representer."""  # noqa: E501

        class MyTaggedType:  # noqa: B903
            def __init__(self, value: str) -> None:
                self.value = value

        class IsolatedDumper(yaml.Dumper):
            pass

        def my_representer(dumper, data):  # noqa: ANN001, ANN202
            return dumper.represent_scalar("!mytaggedtype", data.value)

        yaml.add_representer(MyTaggedType, my_representer, Dumper=IsolatedDumper)

        obj = MyTaggedType("hello")
        result = yaml.dump(obj, Dumper=IsolatedDumper)
        assert "!mytaggedtype" in result
        assert "hello" in result

    def test_add_multi_representer_registers_on_specified_dumper(self) -> None:  # noqa: PLR6301
        """add_multi_representer registers the callable on the given Dumper class."""

        class BaseType:
            pass

        class IsolatedDumper(yaml.Dumper):
            pass

        def multi_rep(dumper, data):  # noqa: ANN001, ANN202
            return dumper.represent_scalar("!base", str(data))

        yaml.add_multi_representer(BaseType, multi_rep, Dumper=IsolatedDumper)

        # Verify registration
        assert BaseType in IsolatedDumper.yaml_multi_representers

    def test_add_representer_called_via_module_level_api(self) -> None:  # noqa: PLR6301
        """add_representer correctly forwards the call to Dumper.add_representer."""

        class AnotherType:
            pass

        class TrackedDumper(yaml.Dumper):
            registered: list = []  # noqa: RUF012

            @classmethod
            def add_representer(cls, data_type, representer) -> None:  # noqa: ANN001
                TrackedDumper.registered.append(data_type)
                super().add_representer(data_type, representer)

        yaml.add_representer(AnotherType, lambda d, v: None, Dumper=TrackedDumper)
        assert AnotherType in TrackedDumper.registered


# ---------------------------------------------------------------------------
# construct_yaml_binary() — SafeConstructor (simplified base64)
# ---------------------------------------------------------------------------


class TestConstructYamlBinary:
    """Tests for SafeConstructor.construct_yaml_binary() which now uses base64.decodebytes directly."""  # noqa: E501

    def _make_scalar_node(self, value: str) -> ScalarNode:  # noqa: PLR6301
        return ScalarNode(tag="tag:yaml.org,2002:binary", value=value)

    def _make_safe_constructor(self) -> SafeConstructor:  # noqa: PLR6301
        sc = SafeConstructor.__new__(SafeConstructor)
        SafeConstructor.__init__(sc)  # noqa: PLC2801
        return sc

    def test_valid_base64_decodes_to_bytes(self) -> None:
        """construct_yaml_binary decodes a valid base64 string to bytes."""
        raw = b"Hello, World!"
        b64_value = base64.encodebytes(raw).decode("ascii")
        node = self._make_scalar_node(b64_value)
        sc = self._make_safe_constructor()

        result = sc.construct_yaml_binary(node)
        assert result == raw

    def test_valid_base64_empty_bytes(self) -> None:
        """construct_yaml_binary handles base64-encoded empty bytes."""
        node = self._make_scalar_node("")
        sc = self._make_safe_constructor()

        result = sc.construct_yaml_binary(node)
        assert result == b""

    def test_valid_base64_binary_data(self) -> None:
        """construct_yaml_binary decodes arbitrary binary (non-ASCII) data correctly."""
        raw = bytes(range(256))
        b64_value = base64.encodebytes(raw).decode("ascii")
        node = self._make_scalar_node(b64_value)
        sc = self._make_safe_constructor()

        result = sc.construct_yaml_binary(node)
        assert result == raw

    def test_non_ascii_scalar_raises_constructor_error(self) -> None:
        """construct_yaml_binary raises ConstructorError when scalar contains non-ASCII characters."""  # noqa: E501
        node = self._make_scalar_node("café\u2019")
        sc = self._make_safe_constructor()

        with pytest.raises(
            ConstructorError, match="failed to convert base64 data into ascii"
        ):
            sc.construct_yaml_binary(node)

    def test_invalid_base64_raises_constructor_error(self) -> None:
        """construct_yaml_binary raises ConstructorError when base64 decoding fails."""
        # "abc!" has incorrect padding and triggers binascii.Error
        node = self._make_scalar_node("abc!")
        sc = self._make_safe_constructor()

        with pytest.raises(ConstructorError, match="failed to decode base64 data"):
            sc.construct_yaml_binary(node)

    def test_via_safe_load_round_trip(self) -> None:  # noqa: PLR6301
        """safe_load round-trips binary data through YAML's !!binary tag correctly."""
        raw = b"\x00\x01\x02\x03\xff\xfe"
        b64 = base64.encodebytes(raw).decode("ascii").strip()
        yaml_src = f"!!binary '{b64}'"

        result = yaml.safe_load(yaml_src)
        assert result == raw

    def test_construct_yaml_binary_uses_decodebytes_directly(self) -> None:  # noqa: PLR6301
        """Regression: construct_yaml_binary calls base64.decodebytes without hasattr guard."""  # noqa: E501
        import inspect

        source = inspect.getsource(SafeConstructor.construct_yaml_binary)
        assert "decodebytes" in source
        # Ensure the old hasattr guard is gone
        assert "hasattr" not in source


# ---------------------------------------------------------------------------
# construct_python_bytes() — FullConstructor (simplified base64)
# ---------------------------------------------------------------------------


class TestConstructPythonBytes:
    """Tests for FullConstructor.construct_python_bytes() with direct base64.decodebytes call."""  # noqa: E501

    def _make_scalar_node(self, value: str) -> ScalarNode:  # noqa: PLR6301
        return ScalarNode(tag="tag:yaml.org,2002:python/bytes", value=value)

    def _make_full_constructor(self) -> FullConstructor:  # noqa: PLR6301
        fc = FullConstructor.__new__(FullConstructor)
        FullConstructor.__init__(fc)  # noqa: PLC2801
        return fc

    def test_valid_base64_decodes_to_bytes(self) -> None:
        """construct_python_bytes decodes a valid base64 scalar to bytes."""
        raw = b"test bytes data"
        b64_value = base64.encodebytes(raw).decode("ascii")
        node = self._make_scalar_node(b64_value)
        fc = self._make_full_constructor()

        result = fc.construct_python_bytes(node)
        assert result == raw

    def test_valid_base64_empty_value(self) -> None:
        """construct_python_bytes handles base64 encoding of empty bytes."""
        node = self._make_scalar_node("")
        fc = self._make_full_constructor()

        result = fc.construct_python_bytes(node)
        assert result == b""

    def test_non_ascii_raises_constructor_error(self) -> None:
        """construct_python_bytes raises ConstructorError when scalar has non-ASCII characters."""  # noqa: E501
        node = self._make_scalar_node("héllo\u00e9")
        fc = self._make_full_constructor()

        with pytest.raises(
            ConstructorError, match="failed to convert base64 data into ascii"
        ):
            fc.construct_python_bytes(node)

    def test_invalid_base64_raises_constructor_error(self) -> None:
        """construct_python_bytes raises ConstructorError for malformed base64 data."""
        # "abc!" has incorrect padding and triggers binascii.Error
        node = self._make_scalar_node("abc!")
        fc = self._make_full_constructor()

        with pytest.raises(ConstructorError, match="failed to decode base64 data"):
            fc.construct_python_bytes(node)

    def test_large_binary_data_round_trip(self) -> None:
        """construct_python_bytes handles large binary payloads correctly."""
        raw = bytes(i % 256 for i in range(1024))
        b64_value = base64.encodebytes(raw).decode("ascii")
        node = self._make_scalar_node(b64_value)
        fc = self._make_full_constructor()

        result = fc.construct_python_bytes(node)
        assert result == raw

    def test_construct_python_bytes_uses_decodebytes_directly(self) -> None:  # noqa: PLR6301
        """Regression: construct_python_bytes calls base64.decodebytes without hasattr guard."""  # noqa: E501
        import inspect

        source = inspect.getsource(FullConstructor.construct_python_bytes)
        assert "decodebytes" in source
        assert "hasattr" not in source


# ---------------------------------------------------------------------------
# YAMLObjectMetaclass registration
# ---------------------------------------------------------------------------


class TestYAMLObjectMetaclass:
    """Tests for YAMLObjectMetaclass.__init__ — auto-registration of constructors/representers."""  # noqa: E501

    def test_class_with_yaml_tag_registers_constructor_and_representer(self) -> None:  # noqa: PLR6301
        """A YAMLObject subclass with yaml_tag auto-registers from_yaml and to_yaml."""

        class IsolatedLoader(yaml.Loader):  # type: ignore[misc]
            pass

        class IsolatedDumper(yaml.Dumper):  # type: ignore[misc]
            pass

        class MyYAMLObj(yaml.YAMLObject):
            yaml_tag = "!test_metaclass_registration_unique_3c1a"
            yaml_loader = [IsolatedLoader]  # noqa: RUF012
            yaml_dumper = IsolatedDumper

            def __init__(self, x: int) -> None:
                self.x = x

        # Constructor should be registered on IsolatedLoader
        assert (
            "!test_metaclass_registration_unique_3c1a"
            in IsolatedLoader.yaml_constructors
        )

        # Representer should be registered on IsolatedDumper
        assert MyYAMLObj in IsolatedDumper.yaml_representers

    def test_class_without_yaml_tag_does_not_register(self) -> None:  # noqa: PLR6301
        """A YAMLObject subclass with yaml_tag=None does not register anything."""

        class IsolatedLoader(yaml.Loader):  # type: ignore[misc]
            pass

        class IsolatedDumper(yaml.Dumper):  # type: ignore[misc]
            pass

        class MyUntaggedObj(yaml.YAMLObject):
            yaml_tag = None
            yaml_loader = [IsolatedLoader]  # noqa: RUF012
            yaml_dumper = IsolatedDumper

        # Should not have any new tag registered
        # (the class's yaml_tag is None, so no registration should occur)
        assert None not in IsolatedLoader.yaml_constructors or (
            IsolatedLoader.yaml_constructors.get(None) != MyUntaggedObj.from_yaml
        )

    def test_yaml_object_round_trip_with_metaclass(self) -> None:  # noqa: PLR6301
        """YAMLObject subclass with yaml_tag can be serialized and deserialized via YAML."""  # noqa: E501

        class Point(yaml.YAMLObject):
            yaml_tag = "!test_point_metaclass_unique_7f4b"
            yaml_loader = [yaml.Loader, yaml.FullLoader, yaml.UnsafeLoader]  # noqa: RUF012
            yaml_dumper = yaml.Dumper

            def __init__(self, x: float, y: float) -> None:
                self.x = x
                self.y = y

        p = Point(1.5, 2.5)
        serialized = yaml.dump(p)
        assert "!test_point_metaclass_unique_7f4b" in serialized

        loaded = _load_with_loader(serialized, yaml.Loader)
        assert isinstance(loaded, Point)
        assert loaded.x == pytest.approx(1.5)
        assert loaded.y == pytest.approx(2.5)

    def test_yaml_object_with_list_of_loaders_registers_on_each(self) -> None:  # noqa: PLR6301
        """When yaml_loader is a list, metaclass registers from_yaml on every loader in the list."""  # noqa: E501

        class LoaderA(yaml.Loader):  # type: ignore[misc]
            pass

        class LoaderB(yaml.Loader):  # type: ignore[misc]
            pass

        tag = "!test_multi_loader_metaclass_unique_8e5c"

        class MultiLoaderObj(yaml.YAMLObject):
            yaml_tag = tag
            yaml_loader = [LoaderA, LoaderB]  # noqa: RUF012
            yaml_dumper = yaml.Dumper

        assert tag in LoaderA.yaml_constructors
        assert tag in LoaderB.yaml_constructors


# ---------------------------------------------------------------------------
# add_path_resolver() — basic smoke test
# ---------------------------------------------------------------------------


class TestAddPathResolver:
    """Tests for add_path_resolver() registering on loaders and dumper."""

    def test_add_path_resolver_loader_none_registers_on_all_default_loaders(  # noqa: PLR6301
        self,
    ) -> None:
        """add_path_resolver with Loader=None registers on Loader, FullLoader, UnsafeLoader, and Dumper.

        yaml_path_resolvers maps (path_pattern, kind) -> tag, so we check values.
        """  # noqa: E501
        tag = "tag:example.com,2024:test_path_resolver_unique_6d3b"
        path = [None]

        yaml.add_path_resolver(tag, path, Loader=None)

        # yaml_path_resolvers maps (path_tuple, kind) -> tag_string
        for loader_cls in (_loader.Loader, _loader.FullLoader, _loader.UnsafeLoader):
            found = tag in loader_cls.yaml_path_resolvers.values()
            assert found, (
                f"Expected path resolver tag '{tag}' in {loader_cls.__name__}.yaml_path_resolvers values"  # noqa: E501
            )

        # Also check Dumper
        assert tag in yaml.Dumper.yaml_path_resolvers.values()

    def test_add_path_resolver_specific_loader_registers_only_on_that_loader(  # noqa: PLR6301
        self,
    ) -> None:
        """add_path_resolver with a specific Loader registers only on that class."""
        tag = "tag:example.com,2024:test_path_resolver_specific_unique_4a8d"
        path = [None]

        class IsolatedLoader(_loader.SafeLoader):
            pass

        yaml.add_path_resolver(tag, path, Loader=IsolatedLoader)

        assert tag in IsolatedLoader.yaml_path_resolvers.values()
        assert tag not in _loader.Loader.yaml_path_resolvers.values()
        assert tag not in _loader.FullLoader.yaml_path_resolvers.values()
        assert tag not in _loader.UnsafeLoader.yaml_path_resolvers.values()
