"""Verify docstring coverage stays above the documented baseline."""

import argparse
import ast
from dataclasses import dataclass, field
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_ROOT = REPO_ROOT / "custom_components" / "jackery_solarvault"
BASELINE_PATH = REPO_ROOT / "docs" / "docstring_baseline.json"


@dataclass
class DocstringStats:  # noqa: D101
    total_defs: int = 0
    documented_defs: int = 0
    undocumented_defs: list[str] = field(default_factory=list, repr=False)

    @property
    def coverage(self) -> float:  # noqa: D102
        if self.total_defs == 0:
            return 1.0
        return self.documented_defs / self.total_defs

    def to_dict(self) -> dict[str, float]:  # noqa: D102
        return {
            "total_defs": self.total_defs,
            "documented_defs": self.documented_defs,
            "coverage": self.coverage,
        }


_DEF_TYPES = (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)


def _gather_docstring_stats() -> DocstringStats:
    stats = DocstringStats()

    for path in sorted(INTEGRATION_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, _DEF_TYPES):
                stats.total_defs += 1
                if ast.get_docstring(node):
                    stats.documented_defs += 1
                else:
                    stats.undocumented_defs.append(
                        f"{relative_path}:{node.lineno}:{node.name}"
                    )

    return stats


def _load_baseline() -> dict[str, float] | None:
    if not BASELINE_PATH.exists():
        return None
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _write_baseline(stats: DocstringStats) -> None:
    BASELINE_PATH.write_text(
        json.dumps(stats.to_dict(), indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="rewrite the baseline with the current docstring coverage",
    )
    return parser.parse_args()


def main() -> int:  # noqa: D103
    args = _parse_args()
    stats = _gather_docstring_stats()
    baseline = _load_baseline()

    if args.update or baseline is None:
        _write_baseline(stats)
        return 0

    coverage = stats.coverage
    baseline_coverage = float(baseline.get("coverage", 0.0))

    if coverage + 1e-9 < baseline_coverage:
        baseline_documented = int(baseline.get("documented_defs", 0))
        baseline_total = int(baseline.get("total_defs", 0))
        report = [
            (
                "Docstring coverage regressed: "
                f"current {stats.documented_defs}/{stats.total_defs} "
                f"({coverage:.6%}); baseline {baseline_documented}/"
                f"{baseline_total} ({baseline_coverage:.6%})"
            ),
            "Undocumented definitions:",
            *(f"  {definition}" for definition in stats.undocumented_defs),
        ]
        sys.stderr.write("\n".join(report) + "\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
