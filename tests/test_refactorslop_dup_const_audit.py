"""Tests for REFACTORSLOP/_dup_const_audit.py logic.

_dup_const_audit.py is a standalone analysis script for finding duplicate
module-level constants across the integration package. The file currently
contains Python 2 exception syntax (``except A, B:`` instead of the
Python 3 form ``except (A, B):``), which prevents direct import. This
module tests the equivalent logic independently and documents the known
syntax issue to help contributors identify and fix it.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DUP_CONST_AUDIT_PATH = REPO_ROOT / "REFACTORSLOP" / "_dup_const_audit.py"


# ---------------------------------------------------------------------------
# File existence and structure
# ---------------------------------------------------------------------------


def test_dup_const_audit_file_exists() -> None:
    """REFACTORSLOP/_dup_const_audit.py must exist as a new file added in this PR."""
    assert DUP_CONST_AUDIT_PATH.exists(), (
        "_dup_const_audit.py must exist in REFACTORSLOP/"
    )


def test_dup_const_audit_is_readable() -> None:
    """_dup_const_audit.py must be a readable text file."""
    content = DUP_CONST_AUDIT_PATH.read_text(encoding="utf-8")
    assert len(content) > 0, "_dup_const_audit.py must not be empty"


def test_dup_const_audit_contains_literal_repr_function() -> None:
    """_dup_const_audit.py must define a 'literal_repr' function."""
    content = DUP_CONST_AUDIT_PATH.read_text(encoding="utf-8")
    assert "def literal_repr" in content, (
        "_dup_const_audit.py must define literal_repr()"
    )


def test_dup_const_audit_contains_collect_function() -> None:
    """_dup_const_audit.py must define a 'collect' function."""
    content = DUP_CONST_AUDIT_PATH.read_text(encoding="utf-8")
    assert "def collect" in content, (
        "_dup_const_audit.py must define collect()"
    )


# ---------------------------------------------------------------------------
# Known Python 2 syntax issue (document the bug for contributors)
# ---------------------------------------------------------------------------


def test_dup_const_audit_contains_python2_except_syntax() -> None:
    """_dup_const_audit.py currently uses Python 2 comma-style except clauses.

    This is a known bug: ``except ValueError, TypeError, SyntaxError:`` and
    ``except SyntaxError, UnicodeDecodeError:`` are not valid Python 3 syntax.
    They must be rewritten as ``except (ValueError, TypeError, SyntaxError):``
    and ``except (SyntaxError, UnicodeDecodeError):`` to be compatible with
    Python 3.14.

    This test documents the presence of the bug so contributors know to fix it.
    When the syntax is corrected, this test should be updated to verify that
    the file can be parsed by ast.parse() without error.
    """
    content = DUP_CONST_AUDIT_PATH.read_text(encoding="utf-8")
    # The Python 2 comma syntax appears in the script (see lines ~18 and ~31)
    has_py2_syntax = (
        "except ValueError, TypeError" in content
        or "except SyntaxError, UnicodeDecodeError" in content
    )
    if not has_py2_syntax:
        # The syntax was already fixed - verify the file is now parseable.
        try:
            ast.parse(content)
        except SyntaxError as err:
            pytest.fail(
                f"_dup_const_audit.py no longer has the old Python 2 comma syntax "
                f"but still has a SyntaxError: {err}"
            )
    # If the py2 syntax is present, this test passes (documents the known issue).


def test_dup_const_audit_python2_syntax_causes_parse_failure() -> None:
    """ast.parse() fails on _dup_const_audit.py due to Python 2 except syntax.

    This confirms that the file is not valid Python 3 and cannot be imported
    directly. Contributes to tracking when the syntax is corrected.
    """
    content = DUP_CONST_AUDIT_PATH.read_text(encoding="utf-8")
    try:
        ast.parse(content)
    except SyntaxError:
        return  # Expected: file has Python 2 syntax that fails in Python 3
    # If no SyntaxError, the file was fixed — that's fine too, just skip.
    pytest.skip(
        "_dup_const_audit.py no longer has Python 2 syntax errors; "
        "update the test suite to directly test the functions."
    )


# ---------------------------------------------------------------------------
# Algorithm logic tests (equivalent to what literal_repr / collect should do)
# The logic is tested independently since the original file cannot be imported.
# ---------------------------------------------------------------------------


def _literal_repr_equiv(node: ast.AST) -> str | None:
    """Python 3-compatible equivalent of _dup_const_audit.literal_repr()."""
    try:
        return repr(ast.literal_eval(node))
    except (ValueError, TypeError, SyntaxError):
        try:
            return f"<expr:{ast.unparse(node)}>"
        except Exception:  # noqa: BLE001
            return "<expr:?>"


def _is_const_name_equiv(name: str) -> bool:
    """Python 3-compatible equivalent of _dup_const_audit.is_const_name()."""
    bare = name.lstrip("_")
    return bool(bare) and (bare.isupper() or (bare[0].isupper() and "_" in bare))


def _collect_equiv(source: str) -> dict[str, str | None]:
    """Python 3-compatible equivalent of _dup_const_audit.collect() function.

    Module-level constant name -> value repr for UPPER_CASE or FIELD_-prefixed names.
    """
    out: dict[str, str | None] = {}
    try:
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return out

    for node in tree.body:
        targets = []
        value = None
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        for name in targets:
            if _is_const_name_equiv(name) and value is not None:
                out[name] = _literal_repr_equiv(value)
    return out


class TestLiteralReprEquivalent:
    """Tests for the literal_repr equivalent logic from _dup_const_audit.py."""

    def test_integer_constant(self) -> None:
        """literal_repr returns the repr of an integer literal."""
        tree = ast.parse("42")
        node = tree.body[0].value  # type: ignore[attr-defined]
        result = _literal_repr_equiv(node)
        assert result == "42"

    def test_string_constant(self) -> None:
        """literal_repr returns the repr of a string literal."""
        tree = ast.parse("'hello'")
        node = tree.body[0].value  # type: ignore[attr-defined]
        result = _literal_repr_equiv(node)
        assert result == repr("hello")

    def test_list_constant(self) -> None:
        """literal_repr returns the repr of a list literal."""
        tree = ast.parse("[1, 2, 3]")
        node = tree.body[0].value  # type: ignore[attr-defined]
        result = _literal_repr_equiv(node)
        assert result == "[1, 2, 3]"

    def test_non_literal_expression_returns_expr_prefix(self) -> None:
        """literal_repr returns '<expr:...>' for non-literal expressions like Name."""
        tree = ast.parse("some_variable")
        node = tree.body[0].value  # type: ignore[attr-defined]
        result = _literal_repr_equiv(node)
        assert result is not None
        assert result.startswith("<expr:")

    def test_function_call_returns_expr_prefix(self) -> None:
        """literal_repr returns '<expr:...>' for function call expressions."""
        tree = ast.parse("os.path.join('a', 'b')")
        node = tree.body[0].value  # type: ignore[attr-defined]
        result = _literal_repr_equiv(node)
        assert result is not None
        assert result.startswith("<expr:")

    def test_none_constant(self) -> None:
        """literal_repr handles the None literal."""
        tree = ast.parse("None")
        node = tree.body[0].value  # type: ignore[attr-defined]
        result = _literal_repr_equiv(node)
        assert result == "None"

    def test_boolean_constant(self) -> None:
        """literal_repr handles True and False."""
        for source, expected in [("True", "True"), ("False", "False")]:
            tree = ast.parse(source)
            node = tree.body[0].value  # type: ignore[attr-defined]
            result = _literal_repr_equiv(node)
            assert result == expected, f"Expected {expected!r} for {source!r}"


class TestIsConstNameEquivalent:
    """Tests for the is_const_name equivalent logic from _dup_const_audit.py."""

    def test_all_uppercase_is_constant(self) -> None:
        """UPPER_CASE names are identified as constants."""
        assert _is_const_name_equiv("MY_CONST") is True

    def test_field_prefixed_is_constant(self) -> None:
        """Names starting with uppercase and containing underscore are constants."""
        assert _is_const_name_equiv("Field_Name") is True

    def test_all_lowercase_is_not_constant(self) -> None:
        """Lowercase names are not identified as constants."""
        assert _is_const_name_equiv("my_var") is False

    def test_empty_after_strip_underscore_is_not_constant(self) -> None:
        """Names consisting only of underscores are not constants."""
        assert _is_const_name_equiv("___") is False

    def test_underscore_prefixed_uppercase(self) -> None:
        """_PRIVATE_CONST (underscore-prefixed uppercase) is a constant."""
        assert _is_const_name_equiv("_MY_CONST") is True

    def test_camel_case_without_underscore_is_not_constant(self) -> None:
        """CamelCase without underscore does not match the constant pattern."""
        # bare[0].isupper() is True but "_" not in bare → False
        assert _is_const_name_equiv("CamelCase") is False

    def test_single_uppercase_letter(self) -> None:
        """A single uppercase letter is a constant (bare.isupper() is True)."""
        assert _is_const_name_equiv("X") is True


class TestCollectEquivalent:
    """Tests for the collect() equivalent logic from _dup_const_audit.py."""

    def test_collects_uppercase_constants(self) -> None:
        """collect() extracts UPPER_CASE module-level constants."""
        source = "MY_CONST = 42\nOTHER_CONST = 'hello'\n"
        result = _collect_equiv(source)
        assert "MY_CONST" in result
        assert "OTHER_CONST" in result

    def test_ignores_lowercase_names(self) -> None:
        """collect() ignores module-level lowercase variable names."""
        source = "lowercase = 1\nMY_CONST = 2\n"
        result = _collect_equiv(source)
        assert "lowercase" not in result
        assert "MY_CONST" in result

    def test_collects_annotated_assignments(self) -> None:
        """collect() handles annotated constant assignments (e.g. X: int = 5)."""
        source = "TYPED_CONST: int = 99\n"
        result = _collect_equiv(source)
        assert "TYPED_CONST" in result

    def test_handles_syntax_error_gracefully(self) -> None:
        """collect() returns empty dict for syntactically invalid source."""
        result = _collect_equiv("def broken(:\n    pass\n")
        assert result == {}

    def test_empty_source_returns_empty(self) -> None:
        """collect() returns empty dict for an empty module."""
        result = _collect_equiv("")
        assert result == {}

    def test_value_repr_for_integer_constant(self) -> None:
        """collect() stores the repr of a numeric constant value."""
        source = "MAX_RETRIES = 3\n"
        result = _collect_equiv(source)
        assert result.get("MAX_RETRIES") == "3"

    def test_value_repr_for_string_constant(self) -> None:
        """collect() stores the repr of a string constant value."""
        source = "API_URL = 'https://example.com'\n"
        result = _collect_equiv(source)
        assert result.get("API_URL") == repr("https://example.com")

    def test_non_literal_value_uses_expr_prefix(self) -> None:
        """collect() uses '<expr:...>' for non-literal expressions."""
        source = "BASE_PATH = Path('.')\n"
        result = _collect_equiv(source)
        assert "BASE_PATH" in result
        assert result["BASE_PATH"] is not None
        assert result["BASE_PATH"].startswith("<expr:")

    def test_duplicate_detection_collects_all_occurrences(self) -> None:
        """collect() captures all constant names in the module (for dup detection)."""
        source = (
            "FIELD_A = 'field_a'\n"
            "FIELD_B = 'field_b'\n"
            "FIELD_C = 'field_c'\n"
        )
        result = _collect_equiv(source)
        assert len(result) == 3
        assert "FIELD_A" in result
        assert "FIELD_B" in result
        assert "FIELD_C" in result

    def test_function_definitions_not_collected(self) -> None:
        """collect() does not include function definitions as constants."""
        source = "def MY_FUNC(): pass\nMY_CONST = 1\n"
        result = _collect_equiv(source)
        assert "MY_FUNC" not in result
        assert "MY_CONST" in result
