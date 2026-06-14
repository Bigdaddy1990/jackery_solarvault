"""Extract backup source for every TRULY_MISSING symbol/constant.

Reads _loss_audit_current.json, locates each truly-missing symbol/constant in
the richest backup that contains it, slices its source segment, and writes a
compact reference file (_missing_sources.md) plus a machine-readable index
(_missing_sources.json) for downstream verification.

Stdlib only. Python 3.14+.
"""  # noqa: INP001

from __future__ import annotations  # noqa: TID251

import ast
import json
from pathlib import Path

ROOT = Path(r"M:\v0.1.0")
PKG_REL = Path("custom_components/jackery_solarvault")
BACKUP_DIRS = {
    "pre_transfer": ROOT
    / "clause backups"
    / "_pre_transfer_backup_20260607_171740"
    / PKG_REL,
    "pre_recovery": ROOT
    / "clause backups"
    / "_pre_recovery_backup_20260607_192846"
    / PKG_REL,
    "pre_reconcile": ROOT
    / "clause backups"
    / "_pre_reconcile_backup_20260607_211759"
    / PKG_REL,
}
# Preference order: earliest-richest first, then latest reconcile, then recovery.
PREFERENCE = ["pre_transfer", "pre_reconcile", "pre_recovery"]

audit = json.loads(
    (ROOT / "clause backups" / "_loss_audit_current.json").read_text(encoding="utf-8")
)


def find_symbol_node(tree: ast.AST, qualname: str) -> ast.AST | None:
    parts = qualname.split(".")

    def walk(node: ast.AST, remaining: list[str]) -> ast.AST | None:
        head, *tail = remaining
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and child.name == head
            ):
                if not tail:
                    return child
                return walk(child, tail)
        return None

    return walk(tree, parts)


def slice_source(path: Path, node: ast.AST) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = node.lineno - 1
    # include preceding decorators
    if hasattr(node, "decorator_list") and node.decorator_list:
        start = min(start, node.decorator_list[0].lineno - 1)
    end = node.end_lineno
    return "\n".join(lines[start:end])


def find_const_node(tree: ast.Module, name: str) -> ast.AST | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return node
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return node
    return None


def pick_backup(srcs: list[str]) -> str:
    for pref in PREFERENCE:
        if pref in srcs:
            return pref
    return srcs[0]


out_md: list[str] = ["# Truly-missing symbol sources (from backups)\n"]
index: list[dict] = []

print("=== Extracting symbol sources ===")  # noqa: T201
for key, srcs in audit["TRULY_MISSING_SYMBOLS"].items():
    rel, qualname = key.split("::", 1)
    bname = pick_backup(srcs)
    path = BACKUP_DIRS[bname] / rel
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, FileNotFoundError) as err:
        out_md.append(f"## {key}\n*EXTRACT ERROR: {err}*\n")
        continue
    node = find_symbol_node(tree, qualname)
    if node is None:
        out_md.append(f"## {key}\n*NOT FOUND in {bname}*\n")
        continue
    src = slice_source(path, node)
    lines = src.count("\n") + 1
    out_md.append(
        f"## {rel} :: {qualname}\n"
        f"- source backup: `{bname}` (also in {srcs})\n"
        f"- backup lines: {node.lineno}-{getattr(node, 'end_lineno', '?')} ({lines} lines)\n\n"
        f"```python\n{src}\n```\n"
    )
    index.append({
        "key": key,
        "rel": rel,
        "qualname": qualname,
        "backup": bname,
        "lineno": node.lineno,
        "end_lineno": getattr(node, "end_lineno", None),
        "lines": lines,
    })

print("=== Extracting constant sources ===")  # noqa: T201
out_md.append("\n# Truly-missing constants\n")
for key, srcs in audit["TRULY_MISSING_CONSTS"].items():
    rel, name = key.split("::", 1)
    bname = pick_backup(srcs)
    path = BACKUP_DIRS[bname] / rel
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, FileNotFoundError) as err:
        out_md.append(f"## {key}\n*EXTRACT ERROR: {err}*\n")
        continue
    node = find_const_node(tree, name)
    if node is None:
        out_md.append(f"## {key}\n*NOT FOUND in {bname}*\n")
        continue
    src = slice_source(path, node)
    out_md.append(
        f"## {rel} :: {name}\n- source backup: `{bname}` (also in {srcs})\n\n```python\n{src}\n```\n"
    )
    index.append({
        "key": key,
        "rel": rel,
        "const": name,
        "backup": bname,
        "lineno": node.lineno,
    })

md_path = ROOT / "clause backups" / "_missing_sources.md"
md_path.write_text("\n".join(out_md), encoding="utf-8")
(ROOT / "clause backups" / "_missing_sources.json").write_text(
    json.dumps(index, indent=2), encoding="utf-8"
)
print(f"Wrote {md_path} ({len(out_md)} blocks)")  # noqa: T201
print(f"Total chars: {len(chr(10).join(out_md))}")  # noqa: T201
