"""Behavior checks for the local Codex task hook."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "task_quality_hook",
    Path(__file__).resolve().parents[1] / "scripts/task_quality_hook.py",
)
assert SPEC is not None
assert SPEC.loader is not None
HOOK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HOOK)


def test_snapshot_tracks_new_modified_deleted_files_but_not_symlinks() -> None:
    """Track scoped file changes without following aliases."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        subprocess.run(
            ["git", "-c", "init.templateDir=", "init", "-q", directory], check=True
        )
        (root / "scripts").mkdir()
        file = root / "scripts/sample.py"
        file.write_text("value = 1\n")
        (root / "scripts/alias.py").symlink_to(file)
        before = HOOK.snapshot(root)
        assert list(before) == ["scripts/sample.py"]
        file.write_text("value = 2\n")
        assert HOOK.changed(before, HOOK.snapshot(root)) == ["scripts/sample.py"]
        file.unlink()
        assert HOOK.changed(before, HOOK.snapshot(root)) == ["scripts/sample.py"]


def test_no_changes_never_launch_checks_and_missing_baseline_is_reported() -> None:
    """Skip unchanged tasks and report missing snapshots."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        payload = {
            "cwd": directory,
            "session_id": "../../unsafe",
            "hook_event_name": "Stop",
        }
        with (
            patch.object(HOOK, "snapshot", return_value={"scripts/a.py": "hash"}),
            patch.object(HOOK, "run_checks") as run,
        ):
            assert (
                "no task-start snapshot" in HOOK.handle(root, payload)["systemMessage"]
            )
            payload["hook_event_name"] = "UserPromptSubmit"
            assert HOOK.handle(root, payload) == {}
            payload["hook_event_name"] = "Stop"
            assert HOOK.handle(root, payload) == {}
            run.assert_not_called()
            assert len(list((root / ".claude-flow/task-quality").glob("*.json"))) == 1


def test_autofix_rechecked_and_real_tests_run_with_backup() -> None:
    """Recheck fixes and retain originals before launching tests."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "scripts").mkdir()
        (root / "scripts/a.py").write_text("value=1\n")
        (root / "pyproject.toml").write_text("[project]\n")
        calls = []

        class Process:
            def __init__(self, args: list[str], **kwargs: object) -> None:
                calls.append(args)
                self.code = int(
                    "--files" in args and not any("--files" in c for c in calls[:-1])
                )

            def wait(self, timeout: int | None = None) -> int:
                return self.code

        with (
            patch.object(HOOK.subprocess, "Popen", Process),
            patch.object(HOOK, "snapshot", return_value={}),
        ):
            report = HOOK.run_checks(root, ["scripts/a.py"], root / "results")
        assert report["status"] == "passed"
        assert [c for c in calls if "--files" in c] == [calls[1], calls[1]]
        assert any("scripts/run_ha_tests.py" in c and "--co" not in c for c in calls)
        assert (root / "results/before/scripts/a.py").read_text() == "value=1\n"
        assert (root / "pyproject.toml").read_text() == "[project]\n"
        assert (
            json.loads((root / "results/result.json").read_text())["status"] == "passed"
        )


def test_busy_lock_reports_no_autofix() -> None:
    """Do not start a second auto-fix process in the worktree."""
    with (
        tempfile.TemporaryDirectory() as directory,
        patch.object(HOOK.fcntl, "flock", side_effect=BlockingIOError),
        patch.object(HOOK, "run_checks") as run,
    ):
        result = HOOK.handle(
            Path(directory),
            {"cwd": directory, "session_id": "one", "hook_event_name": "Stop"},
        )
        assert "busy" in result["systemMessage"]
        run.assert_not_called()


def test_steering_preserves_edits_and_failed_result_is_reused() -> None:
    """Retain early edits across prompts and cache failures without reruns."""
    with (
        tempfile.TemporaryDirectory() as directory,
        patch.object(HOOK, "snapshot") as snap,
        patch.object(HOOK, "run_checks") as run,
    ):
        root = Path(directory)
        payload = {
            "cwd": directory,
            "session_id": "steering",
            "hook_event_name": "UserPromptSubmit",
        }
        snap.return_value = {"scripts/a.py": "before"}
        HOOK.handle(root, payload)
        snap.return_value = {"scripts/a.py": "after"}
        HOOK.handle(root, payload)
        run.return_value = {
            "source_after": snap.return_value,
            "status": "failed",
            "failures": ["ha-tests"],
        }
        payload["hook_event_name"] = "Stop"
        first = HOOK.handle(root, payload)
        assert run.call_args.args[1] == ["scripts/a.py"]
        assert "FAILED" in first["systemMessage"]
        assert HOOK.handle(root, payload) == first
        run.assert_called_once()


def test_symlink_replacement_never_copies_or_fixes_external_file() -> None:
    """Reject both direct and parent-directory symlink escapes."""
    with (
        tempfile.TemporaryDirectory() as directory,
        tempfile.TemporaryDirectory() as outside,
        patch.object(HOOK, "snapshot", return_value={}),
        patch.object(HOOK.subprocess, "Popen") as process,
    ):
        root = Path(directory)
        (root / "scripts").mkdir()
        secret = Path(outside) / "sample.py"
        secret.write_text("private = 1\n")
        (root / "scripts/direct.py").symlink_to(secret)
        (root / "scripts/linked").symlink_to(outside, target_is_directory=True)
        process.return_value.wait.return_value = 0
        names = ["scripts/direct.py", "scripts/linked/sample.py"]
        HOOK.run_checks(root, names, root / "results")
        assert not (root / "results/before").exists()
        for call in process.call_args_list:
            assert not set(names).intersection(call.args[0])
        assert secret.read_text() == "private = 1\n"
