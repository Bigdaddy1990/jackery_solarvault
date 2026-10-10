# Dependabot CI repair, 2026-10-10

Baseline: `f6238a4c74ab2e0f34a2b6a2b8943304c3a9b950`.

## Merge safety

A fresh repository API read returned `allow_auto_merge: true`. A targeted
repository PATCH disabled it; independent fresh GETs through the administrative
credential and GitHub connector returned `false`.

Administrative reads of ruleset `24458330` and effective `main` rules verified
active enforcement, strict status checks, all 14 GitHub Actions checks bound to
integration `15368`, and `bypass_actors: []`. Its revision remains
`2026-10-04T14:55:29.575Z`, matching `policy.json`. No ruleset rewrite or bypass
was needed. Existing regression tests reject enabled native auto-merge; an
additional test covers re-enabling it after the initial administrative readback.

## Ruff conflict

Full job logs were retrieved for jobs `114023670218`, `114023669598`,
`114024199130`, and `114024199264` (PRs #480 and #481).

The workflow installed Ruff 0.17.0, but the isolated pre-commit hook used
0.16.10. A sequential reproduction on a temporary copy of
`tests/test_ble_frame.py` demonstrated that 0.17.0 removes exactly 19 obsolete
`private-member-access` suppressions, then 0.16.10 reports those accesses.

The upstream [0.17.0 release notes](https://github.com/astral-sh/ruff/releases/tag/0.17.0)
document SLF001 allowing private access on `object.__new__(cls)` instances
(#29001), and the new `datetime as dt` convention (#28790).

The two local Python hooks share identical open-ended `ruff>=0.17.0`
dependencies. The workflow uses their same environment for fixing and mandatory
verification, instead of interleaving a separately installed Ruff. The project
minimum also rejects older Ruff versions. SLF001, RUF100 and all existing hook
rules remain enabled. The 19 obsolete suppressions and one datetime alias were
updated to the current rules.

Speculative `pyrefly infer` rewrites were removed from the formatting job;
the existing mandatory Pyrefly hook and dedicated type-check workflows remain.
The lite action runs only after mandatory verification succeeds.

Regression tests cover the shared hook configuration, verification-before-write
ordering, preserved necessary ignores over repeated fixing passes, continued
SLF001 enforcement on unguarded access, and linting the original failing file.
