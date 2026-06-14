"""Fresh symbol-level loss audit: current integration vs each backup snapshot.

Read-only. Parses every .py under custom_components/jackery_solarvault in the
live tree and in each backup, extracts the full set of qualified symbols
(functions, classes, methods, nested defs) plus module-level constant names,
and reports:
  * MISSING  - symbol present in a backup but absent in current code
  * SHRUNK   - symbol present in both, but current body spans fewer statements
               (possible truncation / partial loss during merge)
  * CONST_MISSING - module-level constant present in backup, absent in current

Stdlib only. Python 3.14+.
"""  # noqa: INP001

from __future__ import annotations  # noqa: I001, TID251

import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(r"M:\v0.1.0")
PKG_REL = Path("custom_components/jackery_solarvault")
CURRENT = ROOT / PKG_REL
BACKUPS = {
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


@dataclass
class SymbolInfo:
    qualname: str
    kind: str  # "function" | "class"
    lineno: int
    stmt_count: int  # number of statements in the body (recursive)


@dataclass
class ModuleInfo:
    symbols: dict[str, SymbolInfo] = field(default_factory=dict)
    constants: set[str] = field(default_factory=set)
    parse_error: str | None = None


def _count_stmts(node: ast.AST) -> int:
    return sum(1 for _ in ast.walk(node) if isinstance(_, ast.stmt))


def extract(path: Path) -> ModuleInfo:
    info = ModuleInfo()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as err:  # noqa: BLE001, RUF100
        info.parse_error = f"{type(err).__name__}: {err}"
        return info

    # Module-level constants (Name targets of Assign / AnnAssign at module scope).
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    info.constants.add(tgt.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            info.constants.add(node.target.id)

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qn = f"{prefix}{child.name}"
                info.symbols[qn] = SymbolInfo(
                    qn, "function", child.lineno, _count_stmts(child)
                )
                visit(child, f"{qn}.")
            elif isinstance(child, ast.ClassDef):
                qn = f"{prefix}{child.name}"
                info.symbols[qn] = SymbolInfo(
                    qn, "class", child.lineno, _count_stmts(child)
                )
                visit(child, f"{qn}.")

    visit(tree, "")
    return info


def collect_tree(base: Path) -> dict[str, ModuleInfo]:
    out: dict[str, ModuleInfo] = {}
    if not base.exists():
        return out
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(base).as_posix()
        out[rel] = extract(path)
    return out


def main() -> int:  # noqa: C901, PLR0912, PLR0914, PLR0915
    current = collect_tree(CURRENT)
    backups = {name: collect_tree(base) for name, base in BACKUPS.items()}

    # Package-wide indices of the CURRENT tree to detect relocation vs true loss.
    cur_qualnames: set[str] = set()
    cur_leafnames: dict[str, int] = {}  # leaf symbol name -> count of definitions
    cur_all_consts: set[str] = set()
    for mod in current.values():
        for qn, sym in mod.symbols.items():  # noqa: B007
            cur_qualnames.add(qn)
            leaf = qn.rsplit(".", 1)[-1]
            cur_leafnames[leaf] = cur_leafnames.get(leaf, 0) + 1
        cur_all_consts |= mod.constants

    report: dict[str, dict] = {}
    totals = {
        "missing_symbols": 0,
        "truly_missing_symbols": 0,
        "shrunk_symbols": 0,
        "missing_consts": 0,
        "truly_missing_consts": 0,
        "missing_files": 0,
    }
    truly_missing_index: dict[str, list[str]] = {}  # "file::qualname" -> backups
    truly_missing_consts_index: dict[str, list[str]] = {}

    # Union of all files seen across current + all backups.
    all_files: set[str] = set(current)
    for b in backups.values():
        all_files |= set(b)

    for rel in sorted(all_files):  # noqa: PLR1702
        cur = current.get(rel)
        file_entry: dict = {}

        if cur is None:
            sources = [n for n, b in backups.items() if rel in b]
            file_entry["FILE_MISSING_IN_CURRENT"] = sources
            totals["missing_files"] += 1
            report[rel] = file_entry
            continue
        if cur.parse_error:
            file_entry["CURRENT_PARSE_ERROR"] = cur.parse_error

        missing: dict[str, list[str]] = {}
        shrunk: dict[str, dict] = {}
        const_missing: dict[str, list[str]] = {}

        for bname, btree in backups.items():
            bmod = btree.get(rel)
            if bmod is None or bmod.parse_error:
                continue
            for qn, sym in bmod.symbols.items():
                if qn not in cur.symbols:
                    missing.setdefault(qn, []).append(bname)
                else:
                    cur_sym = cur.symbols[qn]
                    # Flag meaningful shrinkage (>3 stmts smaller and >15% loss).
                    delta = sym.stmt_count - cur_sym.stmt_count
                    if delta > 3 and delta / max(sym.stmt_count, 1) > 0.15:  # noqa: PLR2004
                        prev = shrunk.get(qn)
                        if prev is None or sym.stmt_count > prev["backup_stmts"]:
                            shrunk[qn] = {
                                "backup": bname,
                                "backup_stmts": sym.stmt_count,
                                "current_stmts": cur_sym.stmt_count,
                                "delta": delta,
                            }
            for cname in bmod.constants:
                if cname not in cur.constants:
                    const_missing.setdefault(cname, []).append(bname)

        if missing:
            # Classify each per-file missing symbol: relocated vs truly missing.
            classified: dict[str, dict] = {}
            for qn, srcs in sorted(missing.items()):
                leaf = qn.rsplit(".", 1)[-1]
                if qn in cur_qualnames:
                    status = "same_qualname_elsewhere"
                elif cur_leafnames.get(leaf):
                    status = f"relocated?(leaf x{cur_leafnames[leaf]})"
                else:
                    status = "TRULY_MISSING"
                    totals["truly_missing_symbols"] += 1
                    truly_missing_index[f"{rel}::{qn}"] = srcs
                classified[qn] = {"backups": srcs, "status": status}
            file_entry["MISSING_SYMBOLS"] = classified
            totals["missing_symbols"] += len(missing)
        if shrunk:
            file_entry["SHRUNK_SYMBOLS"] = dict(sorted(shrunk.items()))
            totals["shrunk_symbols"] += len(shrunk)
        if const_missing:
            classified_c: dict[str, dict] = {}
            for cname, srcs in sorted(const_missing.items()):
                if cname in cur_all_consts:
                    status = "centralized/relocated"
                else:
                    status = "TRULY_MISSING"
                    totals["truly_missing_consts"] += 1
                    truly_missing_consts_index[f"{rel}::{cname}"] = srcs
                classified_c[cname] = {"backups": srcs, "status": status}
            file_entry["CONST_MISSING"] = classified_c
            totals["missing_consts"] += len(const_missing)

        if file_entry:
            report[rel] = file_entry

    out = {
        "totals": totals,
        "TRULY_MISSING_SYMBOLS": dict(sorted(truly_missing_index.items())),
        "TRULY_MISSING_CONSTS": dict(sorted(truly_missing_consts_index.items())),
        "files": report,
    }
    out_path = ROOT / "clause backups" / "_loss_audit_current.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    # Human summary to stdout.
    print("=== LOSS AUDIT (current vs backups) ===")  # noqa: T201
    print(f"TOTALS: {json.dumps(totals)}")  # noqa: T201
    print(f"Full JSON: {out_path}")  # noqa: T201
    print()  # noqa: T201
    for rel, entry in report.items():
        flags = []
        if "FILE_MISSING_IN_CURRENT" in entry:
            flags.append(f"FILE GONE (in {entry['FILE_MISSING_IN_CURRENT']})")
        if "MISSING_SYMBOLS" in entry:
            flags.append(f"{len(entry['MISSING_SYMBOLS'])} missing sym")
        if "SHRUNK_SYMBOLS" in entry:
            flags.append(f"{len(entry['SHRUNK_SYMBOLS'])} shrunk")
        if "CONST_MISSING" in entry:
            flags.append(f"{len(entry['CONST_MISSING'])} const gone")
        if "CURRENT_PARSE_ERROR" in entry:
            flags.append("PARSE ERROR")
        print(f"  {rel}: {', '.join(flags)}")  # noqa: T201

    print()  # noqa: T201
    print(  # noqa: T201
        f"=== TRULY MISSING SYMBOLS ({len(truly_missing_index)}) — absent from entire current package ==="
    )  # noqa: RUF100, T201
    for key, srcs in sorted(truly_missing_index.items()):
        print(f"  {key}   <- {srcs}")  # noqa: T201
    print()  # noqa: T201
    print(f"=== TRULY MISSING CONSTANTS ({len(truly_missing_consts_index)}) ===")  # noqa: T201
    for key, srcs in sorted(truly_missing_consts_index.items()):
        print(f"  {key}   <- {srcs}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
