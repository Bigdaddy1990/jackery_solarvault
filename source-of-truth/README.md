# Source of Truth

`source-of-truth/` is the binding technical reference for this repository.

## Authority

- Treat every file in `source-of-truth/` as authoritative unless a more specific
  source-of-truth file documents a conflict or supersession.
- Treat `docs/` and `tests/` as derived material. They may explain, summarize,
  exercise, or package the source of truth, but they do not override it.
- Do not use old agent comments, work logs, chat transcripts, interim plans, or
  historical implementation notes as technical truth. They are non-authoritative
  context only and must be re-checked against `source-of-truth/` before use.

## Test expectations

All future tests must derive expected values from `source-of-truth/` or from
fixtures generated from `source-of-truth/`. Hard-coded expected values are only
acceptable when they directly mirror a cited source-of-truth value and the test
makes that relationship explicit.
