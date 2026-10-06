# Mandatory tool preflight

## Provenance

`tests/test_mandatory_tool_gate.py` was introduced by
[`8e93adc85a38d11d8dfb92a7260f5a45d4457f5a`](https://github.com/Bigdaddy1990/jackery_solarvault/commit/8e93adc85a38d11d8dfb92a7260f5a45d4457f5a)
on 2026-10-01. On main
`61fa37d9c663391a4929690eb6d6d4467a45f13e`, the unchanged test imported an
absent module, breaking normal pytest collection. [PR #456](https://github.com/Bigdaddy1990/jackery_solarvault/pull/456)
also records that failure.

The full clone's 2,163 reachable commits, path history and object-path inventory
contain no `scripts/mandatory_tool_gate.py`. The deleted historical `.codex`
ECC bundle contains no equivalent. This module reconstructs the **committed
test contract**, not an unavailable original file. None of the three original
tests is removed, skipped or relaxed.

## Contract and explicit new decisions

Before project writes, Codebase Memory, ICM and Ruflo must each produce a
successful, nonempty `PostToolUse` result. Registration, input text or failed
calls do not count. A failed/empty subsequent call revokes that tool's receipt.
Receipts require the checkout's resolved project path, exact session ID,
required tool identity and a timezone-aware timestamp no more than one hour
old. Future, malformed, foreign or missing receipts deny writes. The one-hour
limit and additional identity fields are new conservative implementation
choices; the original test specifies only recent project/session-bound proof.

`PROJECT` resolves from this module, so no historical Windows path is assumed.
`REQUIRED` defines explicit canonical adapter identifiers:

| Family | Adapter identifier |
| --- | --- |
| Codebase Memory | `mcp__codebase_memory_mcp__trace_path` |
| ICM | `mcp__icm__icm_memory_recall` |
| Ruflo | `mcp__ruflo__hooks_guidance` |

The historical host-qualified names are not present in Git. These identifiers
are new adapter choices, not proof of installed, loaded or functional tools.
Hosts with different server prefixes must map their actual tool names to this
contract while retaining all three families. No discovery response or invented
receipt may substitute for a functional result.

For nested `functions.exec`, only this bounded form is accepted as evidence:

```javascript
const result = await tools.<required_identifier>({});
text(result);
```

Arguments must be literal JSON objects. Output must also contain the family's
committed signature (`signal:` plus `paths:`, `content:`, or
`implementationLoop:`). Serialized MCP errors still invalidate the result.
Reassigned results, arbitrary JavaScript and ambiguous batched calls do not
create receipts. Direct required-tool results remain supported.

Reads and the preflight calls are available before receipts. A bounded shell
read grammar allows simple inspection commands and read-only pipelines;
unknown commands, mutation options, background operators and substitutions
require preflight. Git diff/show/log also require `--no-textconv`. This is a
workflow gate over trusted host events, not a general shell sandbox or a
cryptographic attestation of user-editable receipt files.

## Invocation and verification

`handle(event, state_dir)` supports an explicit receipt root. The optional CLI
`python -m scripts.mandatory_tool_gate` reads one JSON event from stdin and
writes its JSON decision. Its local default root is `.mandatory-tool-gate/`
(ignored by Git). Invalid input or a receipt storage error exits 2, which the
host must treat as a blocking hook failure. A normal denied `PreToolUse` event
returns the host's `permissionDecision: deny` object.

This repository patch does not install or activate a host hook, start any MCP,
or claim live Windows enforcement. Host loading and real MCP connectivity
require their own same-route verification.

Focused verification with the repository's existing test dependencies:

```bash
python -m pytest --collect-only --no-cov
python -m pytest tests/test_mandatory_tool_gate.py tests/test_sync_requirements.py -o addopts= -q
```

The second command scopes execution and omits unrelated integration coverage
output. The first keeps the normal plugins, test paths and import configuration
and excludes no test file; only coverage execution is disabled for collection.
The unmodified `--collect-only` command also collected successfully, but its
coverage output reports the expected unmet threshold because no tests execute.
Passing these checks does not establish a green full CI baseline.
