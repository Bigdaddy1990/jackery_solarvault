# Dependabot merge safety

## Incident and configuration evidence (2026-10-04)

Baseline: `3ad7b8781a971086c6e8f78e28533f3f55f1a5ca` on `main`.

| PR | Head | Merge time (UTC) | Creation to merge |
| --- | --- | --- | --- |
| [452](https://github.com/Bigdaddy1990/jackery_solarvault/pull/452) | `b6c439e67d3f383bb5542508d836442ecfb2446b` | 2026-10-01 20:29:18 | 13 seconds |
| [453](https://github.com/Bigdaddy1990/jackery_solarvault/pull/453) | `69daa266dab890c77e4b19151df3e3c53268232b` | 2026-10-02 20:24:03 | 12 seconds |
| [454](https://github.com/Bigdaddy1990/jackery_solarvault/pull/454) | `196bd4d6e627457c52f80f50a7bf604faba56d5b` | 2026-10-02 20:25:43 | 12 seconds |

All three heads subsequently reported failed pull-request runs for CI, Validate,
Pre-commit Lite, pre-commit-autofix, Python Modernization and Pyrefly Type Check.
Dependency Review succeeded, but even it completed after the merge. These were
merges before checks finished; the recorded failures are the later final results.

The initial authenticated repository response reported `allow_auto_merge: true` and
repository-level push/admin permissions. `/rulesets?includes_parents=true` returned
`[]`. The public `/rules/branches/main` response returned `[]`.
`/branches/main` reported `protected: true`, status-check enforcement
`non_admins`, and empty `contexts` and `checks` arrays.
The full classic protection endpoint initially returned 403 through the integration.
On 2026-10-04, authorized Administration access installed and read back ruleset
`24458330`, active on main with 14 app-bound checks, strict enforcement and no
bypass actors. Classic protection was retained.

The only open Dependabot PR at inspection was #455, with `auto_merge: null`.
No other native auto-merge enabler was found in repository workflows.

## Follow-up audit (2026-10-06)

PR #458 remained blocked on head `e10f031806ddab8910101d1f2938b5a95862f457`,
but a collaborator had re-enabled its native auto-merge request. GitHub accepts
`skipped` and `neutral` as passing required checks. A completion-triggered workflow
cannot disarm native auto-merge before GitHub itself acts on the final check.
Repository-native auto-merge was therefore disabled and read back at
2026-10-06 10:57:07 UTC. This also disables native auto-merge for human PRs, which
can still be merged manually after server requirements pass. The custom Dependabot
workflow continues to use the immediate, SHA-bound merge API.

GitHub hides `bypass_actors` from tokens without ruleset write access. The previous
workflow therefore blocked even correctly installed protection. `policy.json`
now records the Administration-verified ruleset ID and full millisecond revision
`2026-10-04T14:55:29.575Z`. A hidden bypass list is accepted only at this exact
unchanged revision; a visible list must be an empty array. Any ruleset edit, missing
revision or different ID blocks until a new administrative audit is recorded in
trusted main code. UTC and offset representations are normalized without discarding
milliseconds. No privileged token is added to Actions.

Run #37370712000 failed one regression assertion because JSON autofix reordered
policy keys. Contexts and integration IDs are now compared as complete sorted
lists; missing, duplicate or wrong-source checks still fail. Workflow text checks
also handle CRLF. The required regression now audits live settings and protection
using its read-only workflow token.

## Enforced behavior

`policy.json` names eight mandatory workflows and their 14 required job contexts.
The merge workflow runs trusted base/default-branch code only, with no execution
of the PR head and no artifact download. It reacts to completed checks and PR
updates; a manual dispatch sweeps open Dependabot PRs. No polling runner is kept
alive and no native auto-merge request is enabled.

For each open, non-draft, same-repository Dependabot PR targeting main:

1. Revoke any existing native auto-merge request and require repository-native
   auto-merge to be disabled. Re-enabled requests also trigger a safety sweep.
2. Require the active, admin-verified revision of `main-required-ci`, no bypass actors, all app-bound
   required checks, strict up-to-date checks, a PR requirement, no deletion or
   force pushes, and effective required checks on main from this exact ruleset ID. Missing/unreadable
   protection blocks merging.
3. Read all pages of PR workflow runs for the exact current head SHA and branch.
   Select the newest run of each workflow and jobs from its latest attempt.
   Require every expected job to exist and every job/run to finish with `success`.
   Failure, missing, pending, cancellation, skipped and neutral results block.
   A push or manual CI run cannot substitute for PR validation.
4. Refresh the PR; reject head/base changes, conflicts, unknown mergeability,
   a behind/blocked branch or a new native auto-merge request.
5. Recheck rules and runs; reject changed run/attempt identities.
6. Call GitHub's merge API with `sha` equal to the checked head and `squash`.
   GitHub's atomic SHA precondition and strict server-side required checks remain
   the final enforcement boundary. No administrator bypass is requested.

A workflow run can have an empty `pull_requests` association, so exact repository,
head branch, head SHA, workflow path and event are required even in that case.
If there is a PR association, it must also match this PR and head.

The Modernization gate now exits nonzero when its initial checks fail without an
allowed autofix; the initial manual typing check is no longer ignored.

## Required administration step

The live repository already has the required ruleset and native auto-merge is
disabled. The following procedure installs or refreshes the configuration elsewhere
or after drift. Until its new revision is recorded on main, automatic merges are
intentionally blocked. Existing unrelated branch rules are not removed.

From a checkout containing this change, using Node 24 and GitHub CLI authenticated
with repository Administration write permission:

```bash
node .github/merge-safety/apply-ruleset.cjs
node .github/merge-safety/apply-ruleset.cjs --apply
```

The first command audits native auto-merge, classic protection and rulesets. The second disables
repository-native auto-merge, verifies that setting, and creates the
active ruleset from `main-ruleset.json`, or updates the same named repository
ruleset while retaining unrelated restrictions and stricter review settings.
It removes bypass actors from this ruleset and reads back its effective rules.
Other rulesets and classic branch-protection rules remain in place.
It prints the verified ruleset ID and revision. Commit those exact values to
`policy.json` through the normal reviewed PR path before dispatching the merge
workflow. Never fill a missing bypass list with an assumed empty list during an
administrative audit.

Alternatively create a branch ruleset in Settings > Rules > Rulesets for
`refs/heads/main`, import the exact JSON, set enforcement Active, leave the bypass
list empty, and verify every context and its GitHub Actions app binding (15368).
The JSON is also an exact REST create request body for
`POST /repos/Bigdaddy1990/jackery_solarvault/rulesets`.
Disable native auto-merge in Settings > General > Pull Requests, or apply
`{"allow_auto_merge":false}` to `PATCH /repos/Bigdaddy1990/jackery_solarvault`.
Read back both settings and rules with Administration permission, then record the
exact returned ruleset ID and `updated_at` in the trusted policy.

After enforcement is verified, dispatch `Auto-merge Dependabot` on main to audit
open bot PRs. All existing red checks stay blocking; this change does not repair
the dependency-installation, lint, typing or HA validation failures themselves.
PRs created before the new regression workflow exists need a fresh PR event
(synchronize/reopen) to get its required PR run; a manually run check is rejected.

## Regression and acceptance

```bash
node --test .github/merge-safety/gate.test.cjs
```

The built-in Node tests exercise the same `mergeCandidate` entry point used in
production, asserting that the merge API is never called for red/missing/stale
checks, newer reruns, skipped jobs, untrusted PRs, changed heads, missing rules,
API failures, enabled native auto-merge, changed protection revisions or bypassable rules.
They also cover the real read-only ruleset response shape and the administrative
disable/readback procedure. They include modeled replays of the six failed
workflows at the three historical incident SHAs. The positive case asserts the
exact SHA sent to the merge API. The workflow makes this regression a required
check on every PR, without a path filter or conditional skip.

Local follow-up validation: 96 tests passed on Node 24.19.0. The live read-only
audit runs separately in the required GitHub workflow. Passing this regression
does not imply that the repository's dependency, lint, typing or HA checks pass.
This follow-up must pass normal main protection before becoming the production gate.

For a server-side acceptance test use a draft Dependabot-like test PR in an
isolated test repository with the same ruleset. A failed required job, missing
job or pushed replacement head must block merging, including through GitHub's
UI/API; all required jobs passing on the current up-to-date head permits it.
No deliberately failing or mergeable probe PR is opened in the production repo.
Manual merges retain GitHub's native skipped/neutral semantics. The exact-success
guarantee applies to the custom Dependabot automatic path; this change does not
claim to prohibit every administrator-configured or manual merge.

References:
- https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request
- https://docs.github.com/en/rest/repos/rules#create-a-repository-ruleset
- https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks
