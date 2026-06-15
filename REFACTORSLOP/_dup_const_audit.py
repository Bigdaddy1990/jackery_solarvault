"""Find module-level constants DEFINED (assigned) in more than one module.

Distinguishes duplicates with matching values (safe to centralize) from VALUE
CONFLICTS (same name, different literal value across modules — a latent bug).
Only counts assignments, not imports. Stdlib only. Python 3.14+.
"""  # noqa: INP001

import ast
from pathlib import Path

PKG = Path(r"M:\v0.1.0\custom_components\jackery_solarvault")


def literal_repr(node: ast.AST) -> str | None:
    """Best-effort stable repr of a literal/simple constant value, else None."""
    try:
        return repr(ast.literal_eval(node))
    except ValueError, TypeError, SyntaxError:
        # Non-literal (expression / name reference) — represent by source-ish unparse.
        try:
            return f"<expr:{ast.unparse(node)}>"
        except Exception:  # noqa: BLE001
            return "<expr:?>"


def collect(path: Path) -> dict[str, str | None]:
    """Module-level constant name -> value repr (UPPER_CASE or FIELD_/_-prefixed names)."""
    out: dict[str, str | None] = {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError, UnicodeDecodeError:
        return out

    def is_const_name(name: str) -> bool:
        bare = name.lstrip("_")
        return bool(bare) and (bare.isupper() or bare[0].isupper() and "_" in bare)  # noqa: RUF021

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
            if is_const_name(name) and value is not None:
                out[name] = literal_repr(value)
    return out


modules: dict[str, dict[str, str | None]] = {}
for path in sorted(PKG.rglob("*.py")):
    rel = path.relative_to(PKG).as_posix()
    modules[rel] = collect(path)

# name -> {module: value_repr}
by_name: dict[str, dict[str, str | None]] = {}
for rel, consts in modules.items():
    for name, val in consts.items():
        by_name.setdefault(name, {})[rel] = val

dup = {n: m for n, m in by_name.items() if len(m) > 1}
conflicts = {n: m for n, m in dup.items() if len({v for v in m.values()}) > 1}  # noqa: C416
same = {n: m for n, m in dup.items() if n not in conflicts}

print(f"Modules scanned: {len(modules)} | distinct module-level consts: {len(by_name)}")  # noqa: T201
print(  # noqa: T201
    f"Duplicated across modules: {len(dup)}  (value-conflicts: {len(conflicts)}, same-value: {len(same)})"
)  # noqa: RUF100, T201
print()  # noqa: T201
print(  # noqa: T201
    f"=== VALUE CONFLICTS ({len(conflicts)}) — same name, DIFFERENT value across modules (latent bugs) ==="
)  # noqa: RUF100, T201
for name in sorted(conflicts):
    print(f"  {name}:")  # noqa: T201
    for mod, val in sorted(conflicts[name].items()):
        print(f"      {mod}: {val}")  # noqa: T201
print()  # noqa: T201
print(f"=== SAME-VALUE DUPLICATES ({len(same)}) — centralization candidates ===")  # noqa: T201
for name in sorted(same):
    mods = ", ".join(sorted(same[name]))
    print(f"  {name} = {next(iter(same[name].values()))}   in: {mods}")  # noqa: T201
