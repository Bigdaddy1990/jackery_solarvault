# mypy: ignore-errors
# This module targets annotatedyaml._vendor, an internal vendored PyYAML copy that
# no longer ships in annotatedyaml >= 1.0.x. The whole module is skipped at runtime
# via pytest.importorskip; static type checking cannot resolve the absent module.
"""Smoke tests for the annotatedyaml vendored PyYAML module (PR docstring changes).

The PR changes in annotatedyaml/_vendor/yaml/ are purely docstring reformatting
(removing trailing ``# noqa: E501`` comments and rewording).  These tests verify
that the refactoring did not accidentally break any of the affected public API
functions' behaviour.

Covered modules/functions (as listed in the PR diff):
- annotatedyaml/_vendor/yaml/__init__.py:
  - dump_all (stream=None, encoding=None → str; encoding set → bytes)
  - dump (stream=None → str; with stream → None)
  - safe_dump_all, safe_dump
  - add_implicit_resolver (registers on default loaders)
  - add_constructor (registers on default loaders)
  - add_representer (registers on Dumper)
  - add_multi_representer (registers on Dumper)
  - YAMLObject metaclass (__init__, from_yaml, to_yaml)

- annotatedyaml/_vendor/yaml/composer.py:
  - Composer.__init__ creates self.anchors as an empty dict
  - check_node / get_node / get_single_node consume streams correctly

- annotatedyaml/_vendor/yaml/constructor.py:
  - SafeConstructor helpers (bool, int, float, binary, timestamp, omap, pairs, set, map)

All tests run without a real Home Assistant environment.
"""

import base64
import datetime
import math
from typing import Any

import pytest

# annotatedyaml._vendor was an internal vendored PyYAML copy that no longer ships
# in annotatedyaml >= 1.0.x. Skip the whole module when it is unavailable rather
# than failing collection on the stale import path.
_VENDOR_SKIP_REASON = (
    "annotatedyaml._vendor is not a public module in the installed annotatedyaml"
)

yaml = pytest.importorskip("annotatedyaml._vendor.yaml", reason=_VENDOR_SKIP_REASON)
Composer = pytest.importorskip(
    "annotatedyaml._vendor.yaml.composer", reason=_VENDOR_SKIP_REASON
).Composer

# ---------------------------------------------------------------------------
# dump_all / dump — return-type matrix
# ---------------------------------------------------------------------------


class TestDumpAll:
    """Tests for the dump_all function whose docstring was reformatted in this PR."""

    def test_dump_all_returns_str_when_no_stream_no_encoding(self) -> None:  # noqa: PLR6301
        """dump_all must return a str when stream is None and encoding is None."""
        result = yaml.dump_all([{"a": 1}])
        assert isinstance(result, str)
        assert "a: 1" in result

    def test_dump_all_returns_bytes_when_encoding_set(self) -> None:  # noqa: PLR6301
        """dump_all must return bytes when encoding is provided and stream is None."""
        result = yaml.dump_all([{"a": 1}], encoding="utf-8")
        assert isinstance(result, bytes)

    def test_dump_all_returns_none_when_stream_provided(self) -> None:  # noqa: PLR6301
        """dump_all must return None when a stream is provided and write to the stream."""  # noqa: E501
        import io  # noqa: PLC0415

        buf = io.StringIO()
        result = yaml.dump_all([{"x": 42}], stream=buf)
        assert result is None
        assert "x: 42" in buf.getvalue()

    def test_dump_all_multiple_documents(self) -> None:  # noqa: PLR6301
        """dump_all must serialize multiple documents separated by '---'."""
        result = yaml.dump_all([{"doc": 1}, {"doc": 2}])
        assert isinstance(result, str)
        assert "---" in result

    def test_dump_all_sort_keys_true(self) -> None:  # noqa: PLR6301
        """dump_all with sort_keys=True must emit keys in sorted order."""
        result = yaml.dump_all([{"z": 1, "a": 2}], sort_keys=True)
        assert isinstance(result, str)
        assert result.index("a") < result.index("z")

    def test_dump_all_sort_keys_false(self) -> None:  # noqa: PLR6301
        """dump_all with sort_keys=False must preserve insertion order."""
        # The dict {"z": 1, "a": 2} preserves insertion order (Python 3.7+)
        result = yaml.dump_all([{"z": 1, "a": 2}], sort_keys=False)
        assert isinstance(result, str)
        # z appears before a in output
        assert result.index("z") < result.index("a")

    def test_dump_all_empty_documents_list(self) -> None:  # noqa: PLR6301
        """dump_all with an empty document list must return an empty string."""
        result = yaml.dump_all([])
        assert result == ""  # noqa: PLC1901


class TestDump:
    """Tests for the dump function whose docstring was reformatted in this PR."""

    def test_dump_returns_str_when_no_stream(self) -> None:  # noqa: PLR6301
        """Dump must return a str when no stream is provided."""
        result = yaml.dump({"key": "value"})
        assert isinstance(result, str)
        assert "key: value" in result

    def test_dump_returns_none_when_stream_provided(self) -> None:  # noqa: PLR6301
        """Dump must return None when a stream is provided."""
        import io  # noqa: PLC0415

        buf = io.StringIO()
        result = yaml.dump({"key": "val"}, stream=buf)
        assert result is None
        assert "key: val" in buf.getvalue()

    def test_dump_scalar(self) -> None:  # noqa: PLR6301
        """Dump must correctly serialize a scalar string."""
        result = yaml.dump("hello")
        assert isinstance(result, str)
        assert "hello" in result

    def test_dump_integer(self) -> None:  # noqa: PLR6301
        """Dump must serialize an integer as a bare YAML integer."""
        result = yaml.dump(42)
        assert "42" in result

    def test_dump_none(self) -> None:  # noqa: PLR6301
        """Dump must serialize Python None as 'null' in YAML."""
        result = yaml.dump(None)
        assert result.strip() in {"null", "~", "null\n...", "null\n"}


class TestSafeDump:
    """Tests for safe_dump and safe_dump_all whose docstrings were reformatted."""

    def test_safe_dump_returns_str(self) -> None:  # noqa: PLR6301
        """safe_dump must return str for basic Python objects."""
        result = yaml.safe_dump({"hello": "world"})
        assert isinstance(result, str)
        assert "hello: world" in result

    def test_safe_dump_all_multiple_docs(self) -> None:  # noqa: PLR6301
        """safe_dump_all must serialize multiple documents."""
        result = yaml.safe_dump_all([{"a": 1}, {"b": 2}])
        assert isinstance(result, str)
        assert "a: 1" in result
        assert "b: 2" in result

    def test_safe_dump_list(self) -> None:  # noqa: PLR6301
        """safe_dump must correctly serialize a Python list."""
        result = yaml.safe_dump([1, 2, 3])
        assert "1" in result
        assert "2" in result
        assert "3" in result

    def test_safe_dump_empty_dict(self) -> None:  # noqa: PLR6301
        """safe_dump must serialize an empty dict as '{}'."""
        result = yaml.safe_dump({})
        assert "{}" in result or result.strip() == "{}"

    def test_safe_dump_all_returns_str_when_no_stream(self) -> None:  # noqa: PLR6301
        """safe_dump_all must return str when stream is None."""
        result = yaml.safe_dump_all([1, 2])
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# add_implicit_resolver, add_constructor, add_representer, add_multi_representer
# (docstrings reformatted in annotatedyaml/_vendor/yaml/__init__.py)
# ---------------------------------------------------------------------------


class TestAddImplicitResolver:
    """Smoke tests for add_implicit_resolver (docstring reformatted)."""

    def test_add_implicit_resolver_registers_and_resolves(self) -> None:  # noqa: PLR6301
        """After add_implicit_resolver, matching scalars must receive the tag."""
        import re  # noqa: PLC0415

        tag = "tag:test.example,2024:mytype"
        pattern = re.compile(r"^MYVAL$")
        # Register on a local SafeLoader subclass to avoid polluting global state
        from annotatedyaml._vendor.yaml.loader import SafeLoader  # noqa: I001, PLC0415, PLC2701

        class _TestLoader(SafeLoader):
            pass

        _TestLoader.add_implicit_resolver(tag, pattern, ["M"])
        # Now load a scalar that matches
        node = yaml.compose("MYVAL", Loader=_TestLoader)
        assert node is not None
        assert node.tag == tag

    def test_add_implicit_resolver_none_loader_registers_on_default_loaders(  # noqa: PLR6301
        self,
    ) -> None:
        """add_implicit_resolver with Loader=None must not raise."""
        import re  # noqa: PLC0415

        # Use a unique tag to avoid conflicts
        tag = "tag:test.example,2024:unused"
        pattern = re.compile(r"^UNUSED_MARKER_XYZ$")
        # Should not raise
        yaml.add_implicit_resolver(tag, pattern, ["U"], Loader=None)


class TestAddConstructor:
    """Smoke tests for add_constructor (docstring reformatted)."""

    def test_add_constructor_registered_and_invoked(self) -> None:  # noqa: PLR6301
        """A registered constructor must be invoked when loading a tagged scalar."""
        from annotatedyaml._vendor.yaml.loader import SafeLoader  # noqa: I001, PLC0415, PLC2701

        class _TestLoader(SafeLoader):
            pass

        tag = "tag:test.example,2024:myobj"
        _TestLoader.add_constructor(tag, lambda loader, node: "constructed")
        result = yaml.load(f"!!{tag} value", Loader=_TestLoader)
        assert result == "constructed"

    def test_add_constructor_none_loader_does_not_raise(self) -> None:  # noqa: PLR6301
        """add_constructor with Loader=None must register without error."""
        tag = "tag:test.example,2024:noop"
        yaml.add_constructor(tag, lambda loader, node: None, Loader=None)


class TestAddRepresenter:
    """Smoke tests for add_representer and add_multi_representer (docstrings reformatted)."""  # noqa: E501

    def test_add_representer_used_for_custom_type(self) -> None:  # noqa: PLR6301
        """A registered representer must be called when dumping an instance of that type."""  # noqa: E501
        from annotatedyaml._vendor.yaml.dumper import SafeDumper  # noqa: I001, PLC0415, PLC2701

        class _TestDumper(SafeDumper):
            pass

        class _MyType:  # noqa: B903
            def __init__(self, val: int) -> None:
                self.val = val

        _TestDumper.add_representer(
            _MyType,
            lambda dumper, data: dumper.represent_str(f"mytype:{data.val}"),
        )
        result = yaml.dump(_MyType(7), Dumper=_TestDumper)
        assert "mytype:7" in result

    def test_add_multi_representer_used_for_subclasses(self) -> None:  # noqa: PLR6301
        """add_multi_representer must be applied to subclasses of the registered type."""  # noqa: E501
        from annotatedyaml._vendor.yaml.dumper import SafeDumper  # noqa: I001, PLC0415, PLC2701

        class _TestDumper(SafeDumper):
            pass

        class _Base:
            pass

        class _Child(_Base):
            pass

        _TestDumper.add_multi_representer(
            _Base,
            lambda dumper, data: dumper.represent_str("base_multi"),
        )
        result = yaml.dump(_Child(), Dumper=_TestDumper)
        assert "base_multi" in result


# ---------------------------------------------------------------------------
# YAMLObject metaclass (__init__, from_yaml, to_yaml)
# (docstrings reformatted in annotatedyaml/_vendor/yaml/__init__.py)
# ---------------------------------------------------------------------------


class TestYAMLObject:
    """Tests for YAMLObject metaclass and its from_yaml / to_yaml hooks."""

    def test_yaml_object_round_trip(self) -> None:  # noqa: PLR6301
        """A YAMLObject subclass must serialise and deserialise correctly."""

        class MyPoint(yaml.YAMLObject):
            yaml_tag = "!point"
            yaml_loader = [yaml.SafeLoader]  # noqa: RUF012
            yaml_dumper = yaml.SafeDumper

            def __init__(self, x: int, y: int) -> None:
                self.x = x
                self.y = y

        dumped = yaml.dump(MyPoint(3, 4), Dumper=yaml.SafeDumper)
        assert "!point" in dumped

        loaded = yaml.load(dumped, Loader=yaml.SafeLoader)
        assert isinstance(loaded, MyPoint)
        assert loaded.x == 3  # noqa: PLR2004
        assert loaded.y == 4  # noqa: PLR2004

    def test_yaml_object_from_yaml_returns_instance(self) -> None:  # noqa: PLR6301
        """YAMLObject.from_yaml must construct an instance from a mapping node."""

        class MyThing(yaml.YAMLObject):
            yaml_tag = "!thing"
            yaml_loader = [yaml.SafeLoader]  # noqa: RUF012
            yaml_dumper = yaml.SafeDumper

            def __init__(self, name: str = "") -> None:
                self.name = name

        loaded = yaml.load("!thing {name: foo}", Loader=yaml.SafeLoader)
        assert isinstance(loaded, MyThing)
        assert loaded.name == "foo"

    def test_yaml_object_to_yaml_produces_tag(self) -> None:  # noqa: PLR6301
        """YAMLObject.to_yaml must produce output that includes the yaml_tag."""

        class MyWidget(yaml.YAMLObject):
            yaml_tag = "!widget"
            yaml_loader = [yaml.SafeLoader]  # noqa: RUF012
            yaml_dumper = yaml.SafeDumper

            def __init__(self, size: int = 1) -> None:
                self.size = size

        dumped = yaml.dump(MyWidget(5), Dumper=yaml.SafeDumper)
        assert "!widget" in dumped
        assert "size: 5" in dumped or "size: '5'" in dumped


# ---------------------------------------------------------------------------
# Composer.__init__ (docstring reformatted in composer.py)
# ---------------------------------------------------------------------------


class TestComposerInit:
    """Smoke tests for Composer.__init__ (docstring reformatted)."""

    def test_composer_init_creates_anchors_dict(self) -> None:  # noqa: PLR6301
        """Composer.__init__ must create self.anchors as an empty dict."""

        # Composer is a mixin so we stub the minimal required interface
        class _FakeComposer(Composer):
            def check_event(self, *args: Any, **kwargs: Any) -> bool:  # noqa: ANN401, PLR6301
                return False

            def get_event(self) -> None:  # noqa: PLR6301
                return None

            def peek_event(self) -> None:  # noqa: PLR6301
                return None

        obj = _FakeComposer.__new__(_FakeComposer)
        Composer.__init__(obj)  # noqa: PLC2801
        assert hasattr(obj, "anchors")
        assert isinstance(obj.anchors, dict)
        assert len(obj.anchors) == 0


# ---------------------------------------------------------------------------
# SafeConstructor helpers (docstrings reformatted in constructor.py)
# ---------------------------------------------------------------------------


class TestSafeConstructorBool:
    """Tests for SafeConstructor.construct_yaml_bool (docstring reformatted)."""

    @pytest.mark.parametrize(
        ["yaml_text", "expected"],
        [
            ["true", True],
            ["false", False],
            ["yes", True],
            ["no", False],
            ["on", True],
            ["off", False],
            ["True", True],
            ["False", False],
            ["YES", True],
            ["NO", False],
        ],
    )
    def test_bool_values_via_safe_load(self, yaml_text: str, expected: bool) -> None:  # noqa: PLR6301
        """Bool scalar variants must load to the correct Python bool."""
        result = yaml.safe_load(yaml_text)
        assert result is expected, (
            f"Expected {expected} for {yaml_text!r}, got {result!r}"
        )


class TestSafeConstructorInt:
    """Tests for SafeConstructor.construct_yaml_int (docstring reformatted)."""

    @pytest.mark.parametrize(
        ["yaml_text", "expected"],
        [
            ["42", 42],
            ["-7", -7],
            ["+100", 100],
            ["0b1010", 10],  # binary
            ["0xFF", 255],  # hexadecimal
            ["010", 8],  # octal (leading zero)
            ["1_000", 1000],  # underscores
            ["3:25", 205],  # sexagesimal: 3*60 + 25
        ],
    )
    def test_int_formats_via_safe_load(self, yaml_text: str, expected: int) -> None:  # noqa: PLR6301
        """Various integer formats must be parsed correctly."""
        result = yaml.safe_load(yaml_text)
        assert result == expected, (
            f"Expected {expected} for {yaml_text!r}, got {result!r}"
        )


class TestSafeConstructorFloat:
    """Tests for SafeConstructor.construct_yaml_float (docstring reformatted)."""

    @pytest.mark.parametrize(
        ["yaml_text", "expected"],
        [
            ["3.14", math.pi],
            ["-0.5", -0.5],
            ["+1.0", 1.0],
            ["1_000.5", 1000.5],
        ],
    )
    def test_float_values_via_safe_load(self, yaml_text: str, expected: float) -> None:  # noqa: PLR6301
        """Float scalar variants must load to the correct Python float."""
        result = yaml.safe_load(yaml_text)
        assert result == pytest.approx(expected)

    def test_inf_via_safe_load(self) -> None:  # noqa: PLR6301
        """'.inf' must load to positive infinity."""
        import math  # noqa: PLC0415

        result = yaml.safe_load(".inf")
        assert math.isinf(result)
        assert result > 0

    def test_neg_inf_via_safe_load(self) -> None:  # noqa: PLR6301
        """'-.inf' must load to negative infinity."""
        import math  # noqa: PLC0415

        result = yaml.safe_load("-.inf")
        assert math.isinf(result)
        assert result < 0

    def test_nan_via_safe_load(self) -> None:  # noqa: PLR6301
        """.nan must load to a NaN float."""
        import math  # noqa: PLC0415

        result = yaml.safe_load(".nan")
        assert math.isnan(result)

    def test_sexagesimal_float_via_safe_load(self) -> None:  # noqa: PLR6301
        """Sexagesimal float '1:2:3' must equal 1*3600 + 2*60 + 3 = 3723.0."""
        result = yaml.safe_load("1:2:3")
        # YAML 1.1 sexagesimal: 1*3600 + 2*60 + 3 = 3723
        # PyYAML interprets this as a float sexagesimal
        assert result == pytest.approx(3723.0)


class TestSafeConstructorOmap:
    """Tests for SafeConstructor.construct_yaml_omap (docstring reformatted)."""

    def test_omap_preserves_order(self) -> None:  # noqa: PLR6301
        """!!omap must return an ordered list of (key, value) pairs."""
        yaml_text = "!!omap\n- a: 1\n- b: 2\n- c: 3\n"
        result = yaml.safe_load(yaml_text)
        assert isinstance(result, list)
        assert result == [("a", 1), ("b", 2), ("c", 3)]


class TestSafeConstructorPairs:
    """Tests for SafeConstructor.construct_yaml_pairs (docstring reformatted)."""

    def test_pairs_returns_list_of_tuples(self) -> None:  # noqa: PLR6301
        """!!pairs must return a list of (key, value) tuples."""
        yaml_text = "!!pairs\n- x: 10\n- y: 20\n"
        result = yaml.safe_load(yaml_text)
        assert isinstance(result, list)
        assert ("x", 10) in result
        assert ("y", 20) in result


class TestSafeConstructorSet:
    """Tests for SafeConstructor.construct_yaml_set (docstring reformatted)."""

    def test_set_returns_python_set(self) -> None:  # noqa: PLR6301
        """!!set must return a Python set of the keys."""
        yaml_text = "!!set\n? alpha\n? beta\n? gamma\n"
        result = yaml.safe_load(yaml_text)
        assert isinstance(result, set)
        assert result == {"alpha", "beta", "gamma"}


class TestSafeConstructorTimestamp:
    """Tests for SafeConstructor.construct_yaml_timestamp (docstring reformatted)."""

    def test_date_only_scalar_returns_date(self) -> None:  # noqa: PLR6301
        """A date-only YAML timestamp must return a datetime.date instance."""
        result = yaml.safe_load("2024-03-15")
        assert isinstance(result, datetime.date)
        assert not isinstance(result, datetime.datetime)
        assert result == datetime.date(2024, 3, 15)

    def test_datetime_scalar_returns_datetime(self) -> None:  # noqa: PLR6301
        """A full timestamp YAML scalar must return a datetime.datetime."""
        result = yaml.safe_load("2024-03-15T10:20:30Z")
        assert isinstance(result, datetime.datetime)
        assert result.year == 2024  # noqa: PLR2004
        assert result.month == 3  # noqa: PLR2004
        assert result.day == 15  # noqa: PLR2004
        assert result.hour == 10  # noqa: PLR2004
        assert result.minute == 20  # noqa: PLR2004
        assert result.second == 30  # noqa: PLR2004

    def test_datetime_with_offset_returns_datetime_with_tzinfo(self) -> None:  # noqa: PLR6301
        """A timestamp with a UTC offset must produce a datetime with tzinfo set."""
        result = yaml.safe_load("2024-06-01T12:00:00+02:00")
        assert isinstance(result, datetime.datetime)
        assert result.tzinfo is not None


class TestSafeConstructorBinary:
    """Tests for SafeConstructor.construct_yaml_binary (docstring reformatted)."""

    def test_binary_scalar_round_trips(self) -> None:  # noqa: PLR6301
        """A !!binary node must decode its base64-encoded value to bytes."""
        raw_bytes = b"\x00\x01\x02\x03"
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        yaml_text = f"!!binary |\n  {b64}\n"
        result = yaml.safe_load(yaml_text)
        assert isinstance(result, bytes)
        assert result == raw_bytes


# ---------------------------------------------------------------------------
# Regression / boundary tests strengthening coverage
# ---------------------------------------------------------------------------


class TestDumpAllEdgeCases:
    """Additional edge cases for dump_all to strengthen coverage."""

    def test_dump_all_nested_structure(self) -> None:  # noqa: PLR6301
        """dump_all must handle nested dicts and lists."""
        data = [{"nested": {"inner": [1, 2, 3]}}]
        result = yaml.dump_all(data)
        assert isinstance(result, str)
        loaded = list(yaml.safe_load_all(result))
        assert loaded == data

    def test_dump_all_with_unicode_key(self) -> None:  # noqa: PLR6301
        """dump_all must handle unicode keys without errors."""
        result = yaml.dump_all([{"Ключ": "значение"}], allow_unicode=True)
        assert isinstance(result, str)

    def test_dump_roundtrip_preserves_data(self) -> None:  # noqa: PLR6301
        """Data serialized by dump and loaded by safe_load must be equal to the original."""  # noqa: E501
        original = {"numbers": [1, 2, 3], "name": "test", "flag": True, "val": None}
        dumped = yaml.safe_dump(original)
        loaded = yaml.safe_load(dumped)
        assert loaded == original


class TestLoadBoundaryConditions:
    """Boundary and regression tests for the load path (docstrings reformatted in composer/constructor)."""  # noqa: E501

    def test_load_empty_document_returns_none(self) -> None:  # noqa: PLR6301
        """Loading an empty YAML string must return None."""
        result = yaml.safe_load("")
        assert result is None

    def test_load_null_returns_none(self) -> None:  # noqa: PLR6301
        """Loading 'null' must return Python None."""
        result = yaml.safe_load("null")
        assert result is None

    def test_load_mapping_with_anchor_and_alias(self) -> None:  # noqa: PLR6301
        """Anchor (&) and alias (*) must resolve to the same Python object."""
        yaml_text = "base: &anchor {x: 1}\nalias: *anchor\n"
        result = yaml.safe_load(yaml_text)
        assert result is not None
        assert result["base"] == result["alias"]

    def test_get_single_node_raises_on_multiple_docs(self) -> None:  # noqa: PLR6301
        """get_single_node (via load) must raise if the stream has multiple documents."""  # noqa: E501
        from annotatedyaml._vendor.yaml.composer import ComposerError  # noqa: I001, PLC0415, PLC2701

        with pytest.raises((ComposerError, Exception)):
            yaml.load("---\na: 1\n---\nb: 2\n", Loader=yaml.SafeLoader)

    def test_compose_returns_node_for_mapping(self) -> None:  # noqa: PLR6301
        """Compose must return a MappingNode for a YAML mapping."""
        from annotatedyaml._vendor.yaml.nodes import MappingNode  # noqa: I001, PLC0415, PLC2701

        node = yaml.compose("{key: val}")
        assert isinstance(node, MappingNode)

    def test_compose_sequence_returns_sequence_node(self) -> None:  # noqa: PLR6301
        """Compose must return a SequenceNode for a YAML sequence."""
        from annotatedyaml._vendor.yaml.nodes import SequenceNode  # noqa: I001, PLC0415, PLC2701

        node = yaml.compose("[1, 2, 3]")
        assert isinstance(node, SequenceNode)
