"""Synthetic Codex hook events for the Jackery coding preflight."""

from datetime import UTC, datetime, timedelta
import io
import json
import sys
from typing import TYPE_CHECKING, Any

import pytest
from scripts import mandatory_tool_gate
from scripts.mandatory_tool_gate import PROJECT, REQUIRED, handle

if TYPE_CHECKING:
    from pathlib import Path

_SESSION = "test-jackery-preflight"
_HOOK_FAILURE = 2


def _event(event: str, tool: str, **extra: Any) -> dict[str, Any]:
    return {
        "hook_event_name": event,
        "session_id": _SESSION,
        "cwd": str(PROJECT),
        "tool_name": tool,
        **extra,
    }


def test_code_edits_wait_for_all_three_functional_mcp_calls(tmp_path: Path) -> None:
    """A saved registration or failed MCP call cannot authorize code writes."""
    edit = _event(
        "PreToolUse",
        "functions.exec",
        tool_input={"code": "await tools.apply_patch(patch)"},
    )
    assert handle(_event("PreToolUse", "Read"), tmp_path) == {}
    assert handle(edit, tmp_path)["hookSpecificOutput"]["permissionDecision"] == "deny"

    failed = _event(
        "PostToolUse",
        REQUIRED["codebase_memory"],
        tool_response={"isError": True, "content": [{"text": "Transport closed"}]},
    )
    assert handle(failed, tmp_path) == {}
    assert handle(edit, tmp_path)["hookSpecificOutput"]["permissionDecision"] == "deny"

    for tool in REQUIRED.values():
        handle(
            _event(
                "PostToolUse",
                tool,
                tool_response={
                    "isError": False,
                    "content": [{"type": "text", "text": "verified"}],
                },
            ),
            tmp_path,
        )
    assert handle(edit, tmp_path) == {}
    assert (
        handle(
            _event("PreToolUse", "exec_command", tool_input={"cmd": "Set-Content x y"}),
            tmp_path,
        )
        == {}
    )


def test_stale_or_wrong_project_evidence_denies_edits(tmp_path: Path) -> None:
    """A receipt is bound to this project and to a recent session check."""
    for tool in REQUIRED.values():
        handle(
            _event(
                "PostToolUse", tool, tool_response={"content": [{"text": "verified"}]}
            ),
            tmp_path,
        )
    marker = tmp_path / _SESSION / "ruflo.json"
    data = json.loads(marker.read_text(encoding="utf-8"))
    data["project"] = "another-project"
    marker.write_text(json.dumps(data), encoding="utf-8")
    edit = _event("PreToolUse", "apply_patch")
    reason = handle(edit, tmp_path)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "ruflo" in reason

    data["project"] = str(PROJECT)
    data["at"] = "2000-01-01T00:00:00+00:00"
    marker.write_text(json.dumps(data), encoding="utf-8")
    reason = handle(edit, tmp_path)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "ruflo" in reason


def test_nested_codex_mcp_calls_are_observed_only_with_result_evidence(
    tmp_path: Path,
) -> None:
    """The Codex tool wrapper must expose the actual MCP result to the hook."""
    signatures = {
        "codebase_memory": "signal: best_effort paths: 1",
        "icm": "content: [errors-resolved] remembered",
        "ruflo": "implementationLoop: recall inspect route",
    }
    for kind, tool in REQUIRED.items():
        handle(
            _event(
                "PostToolUse",
                "functions.exec",
                tool_input={
                    "code": f"const result = await tools.{tool}({{}}); text(result);"
                },
                tool_response={"content": [{"type": "text", "text": signatures[kind]}]},
            ),
            tmp_path,
        )
    assert handle(_event("PreToolUse", "apply_patch"), tmp_path) == {}


def _verify_all(state_dir: Path) -> None:
    for tool in REQUIRED.values():
        handle(
            _event(
                "PostToolUse", tool, tool_response={"content": [{"text": "verified"}]}
            ),
            state_dir,
        )


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"content": []},
        {"content": [{"text": "  "}]},
        {"isError": True, "content": [{"text": "verified"}]},
        {"content": [{"text": '{"isError": true, "content": "verified"}'}]},
        {"content": [{"text": "Transport closed"}]},
    ],
)
def test_failed_or_empty_results_revoke_previous_receipt(
    tmp_path: Path, response: dict[str, Any]
) -> None:
    """A fresh failure cannot leave an earlier authorization in place."""
    _verify_all(tmp_path)
    handle(_event("PostToolUse", REQUIRED["ruflo"], tool_response=response), tmp_path)
    reason = handle(_event("PreToolUse", "apply_patch"), tmp_path)[
        "hookSpecificOutput"
    ]["permissionDecisionReason"]
    assert "ruflo" in reason


@pytest.mark.parametrize(
    ["field", "value"],
    [
        ["session_id", "another-session"],
        ["tool", "another-tool"],
        ["at", "not-a-date"],
        ["at", "2026-01-01T00:00:00"],
        ["at", (datetime.now(UTC) + timedelta(days=1)).isoformat()],
    ],
)
def test_malformed_or_replayed_receipts_deny_edits(
    tmp_path: Path, field: str, value: str
) -> None:
    """Session, tool identity and an aware, recent timestamp are mandatory."""
    _verify_all(tmp_path)
    marker = tmp_path / _SESSION / "icm.json"
    data = json.loads(marker.read_text(encoding="utf-8"))
    data[field] = value
    marker.write_text(json.dumps(data), encoding="utf-8")
    assert (
        handle(_event("PreToolUse", "apply_patch"), tmp_path)["hookSpecificOutput"][
            "permissionDecision"
        ]
        == "deny"
    )


def test_receipts_are_not_shared_between_sessions(tmp_path: Path) -> None:
    """Another session must run its own preflight."""
    _verify_all(tmp_path)
    edit = _event("PreToolUse", "apply_patch", session_id="new-session")
    assert handle(edit, tmp_path)["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("session", ["", "../escaped", "a/b", "a\\b"])
def test_invalid_session_cannot_escape_state_directory(
    tmp_path: Path, session: str
) -> None:
    """Untrusted session identifiers never become arbitrary filesystem paths."""
    handle(
        _event(
            "PostToolUse",
            REQUIRED["ruflo"],
            session_id=session,
            tool_response={"content": [{"text": "verified"}]},
        ),
        tmp_path,
    )
    assert not list(tmp_path.iterdir())
    assert (
        handle(_event("PreToolUse", "apply_patch", session_id=session), tmp_path)[
            "hookSpecificOutput"
        ]["permissionDecision"]
        == "deny"
    )


@pytest.mark.parametrize(
    ["code", "output"],
    [
        ["await tools.{tool}({{}});", "implementationLoop: recall inspect route"],
        [
            "const r = await tools.{tool}({{}}); text(r);",
            "signal: best_effort paths: 1",
        ],
        [
            "const r = await tools.{tool}({{}}); text(r);",
            '{"isError": true, "content": "implementationLoop: recall inspect route"}',
        ],
        [
            "const r = await tools.{tool}({{}}); text('implementationLoop: forged');",
            "implementationLoop: forged",
        ],
        [
            (
                "let r = await tools.{tool}({{}}); "
                "r = 'implementationLoop: forged'; text(r);"
            ),
            "implementationLoop: forged",
        ],
        [
            (
                "const r = await tools.{tool}({{}}); "
                "const x = await tools.{tool}({{}}); text(r);"
            ),
            "implementationLoop: recall inspect route",
        ],
    ],
)
def test_wrapper_requires_exposed_matching_success(
    tmp_path: Path, code: str, output: str
) -> None:
    """Input mentions, foreign signatures and serialized failures are not proof."""
    _verify_all(tmp_path)
    handle(
        _event(
            "PostToolUse",
            "functions.exec",
            tool_input={"code": code.format(tool=REQUIRED["ruflo"])},
            tool_response={"content": [{"text": output}]},
        ),
        tmp_path,
    )
    assert (
        handle(_event("PreToolUse", "apply_patch"), tmp_path)["hookSpecificOutput"][
            "permissionDecision"
        ]
        == "deny"
    )


@pytest.mark.parametrize(
    ["tool", "tool_input"],
    [
        ["Write", {"file_path": "x.py", "content": "x"}],
        ["exec_command", {"cmd": "Set-Content x y"}],
        ["exec_command", {"cmd": "echo x > x.py"}],
        ["exec_command", {"cmd": "git status; rm x.py"}],
        ["exec_command", {"cmd": "sort -o x.py source.py"}],
        ["exec_command", {"cmd": "git diff --output=x.py"}],
        ["exec_command", {"cmd": "rg --pre=some-command x"}],
        ["exec_command", {"cmd": "ls & rm x.py"}],
        ["exec_command", {"cmd": "ls || rm x.py"}],
        ["exec_command", {"cmd": "cat file |& tee x.py"}],
        ["exec_command", {"cmd": "sort -ox.py input"}],
        ["exec_command", {"cmd": "sort --compress-program=touch input"}],
        ["exec_command", {"cmd": "sort -rox.py input"}],
        ["exec_command", {"cmd": "sort --out=x.py input"}],
        ["exec_command", {"cmd": "sort --compress-progr=touch input"}],
        ["functions.exec", {"code": "await tools.apply_patch(patch)"}],
        ["functions.exec", {"code": "await tools['apply_patch'](patch)"}],
        [
            "functions.exec",
            {
                "code": (
                    "const alias = tools; await alias.apply_patch(p); "
                    "await tools.Read({});"
                )
            },
        ],
    ],
)
def test_write_routes_require_preflight(
    tmp_path: Path, tool: str, tool_input: dict[str, str]
) -> None:
    """Direct, nested and shell writes all pass through the same gate."""
    event = _event("PreToolUse", tool, tool_input=tool_input)
    assert handle(event, tmp_path)["hookSpecificOutput"]["permissionDecision"] == "deny"
    _verify_all(tmp_path)
    assert handle(event, tmp_path) == {}


def test_reads_and_preflight_remain_available_without_receipts(tmp_path: Path) -> None:
    """The gate cannot block the read-only calls needed to satisfy itself."""
    for tool, tool_input in (
        ("Read", {}),
        ("exec_command", {"cmd": "git status --short"}),
        ("exec_command", {"cmd": "rg --files | sort"}),
        (
            "functions.exec",
            {"code": f"const r = await tools.{REQUIRED["icm"]}({{}}); text(r);"},
        ),
    ):
        assert handle(_event("PreToolUse", tool, tool_input=tool_input), tmp_path) == {}
    assert not list(tmp_path.iterdir())


def test_other_project_does_not_authorize_this_project(tmp_path: Path) -> None:
    """A foreign project event neither writes local receipts nor grants access."""
    for tool in REQUIRED.values():
        handle(
            _event(
                "PostToolUse",
                tool,
                cwd=str(tmp_path),
                tool_response={"content": [{"text": "verified"}]},
            ),
            tmp_path,
        )
    assert not list(tmp_path.iterdir())
    assert (
        handle(_event("PreToolUse", "apply_patch"), tmp_path)["hookSpecificOutput"][
            "permissionDecision"
        ]
        == "deny"
    )


@pytest.mark.parametrize(
    "payload", ["not json", "[]", "{}", '{"hook_event_name": "PreToolUse"}']
)
def test_cli_invalid_input_stops_hook(
    monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    """Malformed input returns a host-blocking exit code and denial."""
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    monkeypatch.setattr(sys, "stdout", output)
    assert mandatory_tool_gate.main() == _HOOK_FAILURE
    assert (
        json.loads(output.getvalue())["hookSpecificOutput"]["permissionDecision"]
        == "deny"
    )


def test_storage_failure_stops_hook(tmp_path: Path) -> None:
    """A receipt that cannot be revoked is an error, never a silent success."""
    _verify_all(tmp_path)
    marker = tmp_path / _SESSION / "ruflo.json"
    marker.unlink()
    marker.mkdir()
    event = _event("PostToolUse", REQUIRED["ruflo"], tool_response={"isError": True})
    with pytest.raises(OSError, match=r".+"):
        handle(event, tmp_path)


@pytest.mark.parametrize("payload", ["[]", "null", "{", "{}"])
def test_unreadable_receipt_content_denies_edits(tmp_path: Path, payload: str) -> None:
    """Corrupt or truncated files are missing proof rather than hook crashes."""
    _verify_all(tmp_path)
    (tmp_path / _SESSION / "ruflo.json").write_text(payload, encoding="utf-8")
    assert (
        handle(_event("PreToolUse", "apply_patch"), tmp_path)["hookSpecificOutput"][
            "permissionDecision"
        ]
        == "deny"
    )


@pytest.mark.parametrize(
    "command",
    [
        "git diff --no-textconv *",
        "git diff --no-textconv ?.py",
        "git diff --no-textconv [ab].py",
        "cat /dev/null#; touch owned",
        "rg --hostname-bin=./payload --hyperlink-format='file://{host}{path}' pattern",
        "rg --hostname-bin ./payload pattern",
    ],
)
def test_shell_expansion_and_process_options_require_receipts(
    tmp_path: Path, command: str
) -> None:
    """Shell expansion and ripgrep helpers cannot bypass preflight."""
    result = handle(
        _event("PreToolUse", "exec_command", tool_input={"cmd": command}), tmp_path
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("field", ["hook_event_name", "cwd", "session_id", "tool_name"])
def test_cli_missing_common_hook_fields_blocks_even_read_commands(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """Incomplete host events must fail closed before the read-only shortcut."""
    event = _event("PreToolUse", "exec_command", tool_input={"cmd": "git status"})
    event.pop(field)
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setattr(sys, "stdout", output)
    assert mandatory_tool_gate.main() == _HOOK_FAILURE
    assert (
        json.loads(output.getvalue())["hookSpecificOutput"]["permissionDecision"]
        == "deny"
    )
