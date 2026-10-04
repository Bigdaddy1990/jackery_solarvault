"""Project/session preflight reconstructed from the committed hook contract.

Consume trusted PreToolUse/PostToolUse events; never start MCPs or invent proof.
An empty result means no objection, not a host-level permission grant.
"""

import json
import re
import shlex
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Final

PROJECT: Final = Path(__file__).resolve().parents[1]
# Canonical adapter names, not evidence that these MCP connections are loaded.
REQUIRED: Final = {
    "codebase_memory": "mcp__codebase_memory_mcp__trace_path",
    "icm": "mcp__icm__icm_memory_recall",
    "ruflo": "mcp__ruflo__hooks_guidance",
}
MAX_AGE: Final = timedelta(hours=1)
_SIGNATURES: Final = {
    "codebase_memory": ("signal:", "paths:"),
    "icm": ("content:",),
    "ruflo": ("implementationLoop:",),
}
_READ_TOOLS: Final = {"Read", "Glob", "Grep", "read_file", "view_image"}
_SESSION: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_ERROR: Final = re.compile(
    r"(?im)^\s*(?:error\b|exception\b|traceback\b)|"
    r"\b(?:transport closed|server disconnected|connection refused)\b"
)


def _deny(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _read_only_shell(command: Any) -> bool:
    """Allow a small read-only grammar; opaque commands require preflight."""
    if not isinstance(command, str) or not command.strip():
        return False
    if any(token in command for token in (">", "<", "$", "`", "\n", "\\")):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|;&")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return False
    parts: list[list[str]] = [[]]
    for token in tokens:
        if token in {"|", "&&", ";"}:
            parts.append([])
        elif re.fullmatch(r"[|;&]+", token):
            return False
        else:
            parts[-1].append(token)
    for part in parts:
        if not part:
            return False
        if part[0] == "git":
            if len(part) < 2 or part[1] not in {
                "status",
                "log",
                "show",
                "diff",
                "ls-files",
                "ls-tree",
                "rev-parse",
            }:
                return False
            if any(
                arg.startswith(("--output", "--ext-diff", "--textconv")) for arg in part
            ):
                return False
            if part[1] in {"diff", "show", "log"} and "--no-textconv" not in part:
                return False
        elif part[0] not in {"pwd", "ls", "cat", "head", "tail", "rg", "sort", "wc"}:
            return False
        elif any(
            arg.startswith(("-o", "--output", "--pre", "--compress-program"))
            for arg in part
        ):
            return False
        if part[0] == "sort" and any(
            arg.startswith("-")
            and arg
            not in {"--", "--unique", "--reverse", "--numeric-sort", "--ignore-case"}
            and not re.fullmatch(r"-[urnf]+", arg)
            for arg in part[1:]
        ):
            return False
    return True


def _wrapper_call(code: str) -> tuple[str, bool] | None:
    """Recognize one literal JSON call, with an optional bound result exposure."""
    match = re.fullmatch(
        r"\s*(?:const|let)\s+(\w+)\s*=\s*await\s+tools\.(\w+)\s*"
        r"\((.*)\)\s*;\s*text\(\s*\1\s*\)\s*;?\s*",
        code,
        re.DOTALL,
    )
    if match:
        name, arguments, exposed = match[2], match[3], True
    else:
        match = re.fullmatch(
            r"\s*await\s+tools\.(\w+)\s*\((.*)\)\s*;?\s*", code, re.DOTALL
        )
        if not match:
            return None
        name, arguments, exposed = match[1], match[2], False
    try:
        if not isinstance(json.loads(arguments), dict):
            return None
    except ValueError:
        return None
    return name, exposed


def _read_only(event: dict[str, Any]) -> bool:
    tool = event.get("tool_name", "")
    if tool in _READ_TOOLS or tool in REQUIRED.values():
        return True
    inputs = event.get("tool_input")
    if not isinstance(inputs, dict):
        return False
    if tool in {"exec_command", "functions.exec_command", "Bash", "shell"}:
        return _read_only_shell(inputs.get("cmd", inputs.get("command")))
    if tool == "functions.exec":
        code = inputs.get("code", "")
        if not isinstance(code, str):
            return False
        call = _wrapper_call(code)
        return bool(call and (call[0] in _READ_TOOLS or call[0] in REQUIRED.values()))
    return False


def _has_error(value: Any) -> bool:
    """Check nested/serialized MCP errors, including functions.exec output."""
    if isinstance(value, dict):
        if value.get("isError") or value.get("error") or value.get("success") is False:
            return True
        if value.get("exit_code") not in (None, 0):
            return True
        return any(_has_error(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_error(item) for item in value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError, TypeError:
            return bool(_ERROR.search(value))
        if isinstance(decoded, (dict, list)):
            return _has_error(decoded)
    return False


def _result_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return "\n".join(
            _result_text(value[key])
            for key in ("text", "content", "structuredContent")
            if key in value
        ).strip()
    if isinstance(value, list):
        return "\n".join(_result_text(item) for item in value).strip()
    return ""


def _observed(event: dict[str, Any]) -> dict[str, bool]:
    tool = event.get("tool_name")
    response = event.get("tool_response")
    text = _result_text(response)
    success = bool(text) and not _has_error(response)
    for kind, name in REQUIRED.items():
        if tool == name:
            return {kind: success}
    inputs = event.get("tool_input")
    if tool != "functions.exec" or not isinstance(inputs, dict):
        return {}
    code = inputs.get("code")
    if not isinstance(code, str):
        return {}
    observed: dict[str, bool] = {}
    call = _wrapper_call(code)
    for kind, name in REQUIRED.items():
        if not re.search(rf"\btools\.{re.escape(name)}\s*\(", code):
            continue
        # A narrow grammar prevents reassignment, forged text and batch ambiguity.
        exposed = bool(call and call == (name, True))
        observed[kind] = (
            success
            and exposed
            and all(signature in text for signature in _SIGNATURES[kind])
        )
    return observed


def _valid_receipt(marker: Path, session: str, kind: str, now: datetime) -> bool:
    try:
        if marker.is_symlink():
            return False
        data = json.loads(marker.read_text(encoding="utf-8"))
        at = datetime.fromisoformat(data["at"])
        return (
            data["project"] == str(PROJECT)
            and data["session_id"] == session
            and data["tool"] == REQUIRED[kind]
            and at.tzinfo is not None
            and timedelta(0) <= now - at <= MAX_AGE
        )
    except OSError, ValueError, TypeError, KeyError:
        return False


def _save_receipt(marker: Path, session: str, kind: str, now: datetime) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "project": str(PROJECT),
        "session_id": session,
        "tool": REQUIRED[kind],
        "at": now.isoformat(),
    }
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=marker.parent, delete=False
    ) as receipt:
        temporary = Path(receipt.name)
        json.dump(data, receipt)
    try:
        temporary.replace(marker)
    finally:
        temporary.unlink(missing_ok=True)


def handle(event: dict[str, Any], state_dir: Path) -> dict[str, Any]:
    """Record functional results and deny writes without all recent receipts."""
    cwd = event.get("cwd")
    if not isinstance(cwd, str):
        cwd = None
    if isinstance(cwd, str) and cwd:
        try:
            if not Path(cwd).resolve().is_relative_to(PROJECT):
                return {}
        except OSError, ValueError:
            cwd = None
    hook = event.get("hook_event_name")
    if hook == "PreToolUse" and _read_only(event):
        return {}
    session = event.get("session_id")
    if not cwd or not isinstance(session, str) or not _SESSION.fullmatch(session):
        return (
            _deny("Missing project/session identity for mandatory preflight.")
            if hook == "PreToolUse"
            else {}
        )
    directory = Path(state_dir) / session
    if directory.is_symlink() or Path(state_dir).is_symlink():
        return (
            _deny("Unsafe preflight receipt directory.") if hook == "PreToolUse" else {}
        )
    now = datetime.now(UTC)
    if hook == "PostToolUse":
        for kind, success in _observed(event).items():
            marker = directory / f"{kind}.json"
            try:
                if success:
                    _save_receipt(marker, session, kind, now)
                else:
                    marker.unlink(missing_ok=True)
            except OSError:
                # Abort the hook when evidence cannot be updated or revoked.
                raise
        return {}
    if hook == "PreToolUse":
        missing = [
            kind
            for kind in REQUIRED
            if not _valid_receipt(directory / f"{kind}.json", session, kind, now)
        ]
        if missing:
            return _deny("Functional MCP preflight required: " + ", ".join(missing))
    return {}


def main() -> int:
    """Process one hook event on stdin and write the host decision as JSON."""
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise ValueError("Expected a hook object")
        result = handle(event, PROJECT / ".mandatory-tool-gate")
    except OSError, ValueError, TypeError:
        result = _deny("Invalid mandatory preflight hook input.")
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
        return 2
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
