"""Guard Dependabot-managed versions against the requirements generator."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import sync_requirements


class SyncRequirementsTests(unittest.TestCase):
    """Exercise --check and --write against isolated requirement files."""

    def setUp(self) -> None:
        """Copy the checked-in dependency files into a temporary repository."""
        source_root = sync_requirements.ROOT
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.test_path = self.root / "requirements-test.txt"
        self.test_path.write_text(
            (source_root / "requirements-test.txt").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        self.runtime_path = self.root / "requirements.txt"
        self.runtime_path.write_text(
            (source_root / "requirements.txt").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        manifest_dir = self.root / "custom_components" / "jackery_solarvault"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "manifest.json").write_text(
            json.dumps({
                "requirements": self.runtime_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            }),
            encoding="utf-8",
        )
        root_patch = patch.object(sync_requirements, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    @staticmethod
    def requirement_line(content: str, name: str) -> str:
        """Find a package line independently of its current version."""
        return next(
            line
            for line in content.splitlines(keepends=True)
            if line.strip()
            and sync_requirements.requirement_name(line)
            == sync_requirements.requirement_name(name)
        )

    def test_write_preserves_future_dependabot_version_bumps(self) -> None:
        """A future version change must survive --write byte for byte."""
        current = self.test_path.read_text(encoding="utf-8")
        updated = current.replace(self.requirement_line(current, "ty"), "ty<=999.0.0\n")
        updated = updated.replace(
            self.requirement_line(updated, "hypothesis"), "hypothesis>=999.0.0\n"
        )
        for name in ("coverage[toml]", "pytest-homeassistant-custom-component"):
            updated = updated.replace(
                self.requirement_line(updated, name), f"{name}>=999.0.0\n"
            )
        self.test_path.write_text(updated, encoding="utf-8")

        assert sync_requirements.main(["--check"]) == 0
        assert sync_requirements.main(["--write"]) == 0
        assert self.test_path.read_text(encoding="utf-8") == updated

    def test_write_refuses_missing_test_package_without_relaxing_constraint(
        self,
    ) -> None:
        """A missing pinned tool needs a deliberate replacement constraint."""
        original = self.test_path.read_text(encoding="utf-8")
        without_ty = original.replace(self.requirement_line(original, "ty"), "")
        self.test_path.write_text(without_ty, encoding="utf-8")

        assert sync_requirements.main(["--check"]) == 1
        assert sync_requirements.main(["--write"]) == 1
        assert self.test_path.read_text(encoding="utf-8") == without_ty

    def test_write_refuses_to_revert_runtime_dependabot_update(self) -> None:
        """Runtime changes require an explicit manifest reconciliation."""
        current = self.runtime_path.read_text(encoding="utf-8")
        updated = current.replace(
            self.requirement_line(current, "segno"), "segno>=999.0.0\n"
        )
        self.runtime_path.write_text(updated, encoding="utf-8")

        assert sync_requirements.main(["--check"]) == 1
        assert sync_requirements.main(["--write"]) == 1
        assert self.runtime_path.read_text(encoding="utf-8") == updated

    def test_check_rejects_package_outside_inventory(self) -> None:
        """An obsolete or accidental test package must not escape the gate."""
        updated = (
            self.test_path.read_text(encoding="utf-8") + "unused-dependency>=9.9.9\n"
        )
        self.test_path.write_text(updated, encoding="utf-8")

        assert sync_requirements.main(["--check"]) == 1
        assert sync_requirements.main(["--write"]) == 1
        assert self.test_path.read_text(encoding="utf-8") == updated


if __name__ == "__main__":
    unittest.main()
