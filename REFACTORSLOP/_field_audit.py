"""Validate const.py FIELD_* payload-field constants against the extracted app data.

Two directions:
  A) FIELD_* whose string value is NOT in any app field source -> typo/stale/non-payload (suspect).
  B) Entity-relevant app fields (from entity_field_candidates) with NO matching FIELD_* constant
     -> possibly unconsumed payload field (missing sensor coverage).

Sources of truth (docs/): jackery_http_model_fields_v2.csv, hbxn_model_fields.csv,
jackery_entity_field_candidates_v2.json. Stdlib only. Python 3.14+.
"""  # noqa: INP001

import ast
import csv
import json
from pathlib import Path

ROOT = Path(r"M:\v0.1.0")
DOCS = ROOT / "docs"
CONST = ROOT / "custom_components" / "jackery_solarvault" / "const.py"

# --- app field sources -----------------------------------------------------
app_fields: set[str] = set()
field_origin: dict[str, set[str]] = {}


def _add(field: str, origin: str) -> None:
    field = field.strip()
    if field:
        app_fields.add(field)
        field_origin.setdefault(field, set()).add(origin)


with (DOCS / "jackery_http_model_fields_v2.csv").open(encoding="utf-8") as fh:
    for row in csv.DictReader(fh):
        _add(row["field"], "http")

with (DOCS / "hbxn_model_fields.csv").open(encoding="utf-8") as fh:
    for row in csv.DictReader(fh):
        _add(row["field"], "hbxn")

entity_candidates: dict[str, list[str]] = {}
for entry in json.loads(
    (DOCS / "jackery_entity_field_candidates_v2.json").read_text(encoding="utf-8")
):
    cls = entry["class"]
    entity_candidates[cls] = entry["fields"]
    for f in entry["fields"]:
        _add(f, "entity")

# --- FIELD_* constants from const.py ---------------------------------------
tree = ast.parse(CONST.read_text(encoding="utf-8"))
field_consts: dict[str, str] = {}  # const name -> string value
for node in tree.body:
    target = None
    value = None
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        target, value = node.target.id, node.value
    elif (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        target, value = node.targets[0].id, node.value
    if (
        target
        and target.startswith("FIELD_")
        and isinstance(value, ast.Constant)
        and isinstance(value.value, str)
    ):
        field_consts[target] = value.value

const_values = set(field_consts.values())

# --- Direction A: FIELD_* value not in any app source ----------------------
suspect_consts = {
    name: val for name, val in sorted(field_consts.items()) if val not in app_fields
}

# --- Direction B: entity-relevant fields with no FIELD_* constant ----------
uncovered: dict[str, list[str]] = {}
for cls, fields in entity_candidates.items():
    missing = [f for f in fields if f not in const_values]
    if missing:
        uncovered[cls] = missing

print(f"App fields: {len(app_fields)} | FIELD_* consts: {len(field_consts)}")  # noqa: T201
print()  # noqa: T201
print(  # noqa: T201
    f"=== A) FIELD_* values NOT in any app source ({len(suspect_consts)}) — typo/stale/non-payload ==="
)  # noqa: RUF100, T201
for name, val in suspect_consts.items():
    print(f"  {name} = {val!r}")  # noqa: T201
print()  # noqa: T201
print(  # noqa: T201
    f"=== B) Entity-candidate fields with NO FIELD_* constant ({sum(len(v) for v in uncovered.values())}) ==="
)  # noqa: RUF100, T201
for cls, fields in uncovered.items():
    print(f"  [{cls}]")  # noqa: T201
    for f in fields:
        print(f"      {f}   (origins: {sorted(field_origin.get(f, set()))})")  # noqa: T201
