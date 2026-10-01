"""Synthetic Codex hook events for the Jackery coding preflight."""

import json
from typing import TYPE_CHECKING, Any

from scripts.mandatory_tool_gate import PROJECT, REQUIRED, handle

if TYPE_CHECKING:
    from pathlib import Path

_SESSION = "test-jackery-preflight"


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
