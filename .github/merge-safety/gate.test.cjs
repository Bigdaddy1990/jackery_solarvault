'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { policy, requiredContexts, eligible, protectedBy, latestRuns, successful,
  mergeCandidate, run } = require('./gate.cjs');
const ruleset = { id: 1, ...require('./main-ruleset.json') };
const effective = rules => rules.map(rule => ({ ...rule, ruleset_id: 1 }));
const repository = 'Bigdaddy1990/jackery_solarvault';
const repo = { owner: 'Bigdaddy1990', repo: 'jackery_solarvault' };
const clone = value => JSON.parse(JSON.stringify(value));

function fixture() {
  const pr = { number: 999, node_id: 'PR_999', state: 'open', draft: false,
    user: { login: 'dependabot[bot]' }, auto_merge: null,
    base: { ref: 'main', sha: 'base-a', repo: { full_name: repository } },
    head: { sha: 'head-a', ref: 'dependabot/pip/example', repo: { full_name: repository } },
    mergeable: true, mergeable_state: 'clean' };
  const runs = Object.keys(policy.workflows).map((file, i) => ({
    id: i + 1, run_attempt: 1, path: `.github/workflows/${file}`,
    event: 'pull_request', head_sha: pr.head.sha, head_branch: pr.head.ref, status: 'completed', conclusion: 'success',
    head_repository: { full_name: repository },
    pull_requests: [{ number: pr.number, head: { sha: pr.head.sha }, base: { ref: 'main' } }]
  }));
  const jobs = new Map(runs.map((run, i) => [run.id,
    Object.values(policy.workflows)[i].jobs.map(name => ({
      run_id: run.id, name, status: 'completed', conclusion: 'success'
    }))]));
  return { pr, runs, jobs, rules: clone(ruleset) };
}

function client(f, change = () => {}) {
  const writes = [];
  let reads = 0;
  const github = {
    rest: { actions: { listWorkflowRunsForRepo: 'runs' }, pulls: {
      list: 'pulls', get: async () => {
        reads++;
        change(f, reads);
        return { data: clone(f.pr) };
      },
      merge: async args => {
        // Model GitHub's atomic head-SHA precondition.
        if (args.sha !== f.pr.head.sha) throw new Error('409 head changed');
        writes.push(args);
        return { data: { merged: true, sha: 'merge-sha' } };
      }
    } },
    paginate: async (route, args) => {
      if (route === 'runs') {
        assert.equal(args.head_sha, f.pr.head.sha);
        assert.equal(args.event, 'pull_request');
        return clone(f.runs);
      }
      if (route === 'pulls') return [clone(f.pr)];
      if (route.includes('/rulesets')) return [{ id: 1, name: policy.ruleset_name }];
      assert.ok(route.includes('/attempts/{attempt_number}/jobs'));
      assert.equal(args.attempt_number, f.runs.find(r => r.id === args.run_id).run_attempt);
      return clone(f.jobs.get(args.run_id) || []);
    },
    request: async route => ({ data: route.includes('/rules/branches/') ?
      effective(clone(f.rules.rules)) : clone(f.rules) }),
    graphql: async () => { writes.push('disable-auto-merge'); f.pr.auto_merge = null; }
  };
  const errors = [];
  return { github, writes, errors, core: { info() {}, setFailed: msg => errors.push(msg) } };
}

async function attempt(f, change) {
  const c = client(f, change);
  const merged = await mergeCandidate({ ...c, repo, number: f.pr.number });
  return { ...c, merged };
}

test('all green merges only the inspected current SHA with squash', async () => {
  const f = fixture();
  const c = await attempt(f);
  assert.equal(c.merged, true);
  assert.deepEqual(c.writes, [{ ...repo, pull_number: 999, sha: 'head-a', merge_method: 'squash' }]);
});

for (const [i, workflow] of Object.values(policy.workflows).entries()) {
  for (const conclusion of ['failure', 'cancelled', 'timed_out', 'action_required', 'neutral', 'skipped', null]) {
    test(`${workflow.name}: ${conclusion} cannot call merge`, async () => {
      const f = fixture();
      f.runs[i].conclusion = conclusion;
      const c = await attempt(f);
      assert.equal(c.merged, false);
      assert.deepEqual(c.writes, []);
    });
  }
  test(`${workflow.name}: absent or running cannot call merge`, async () => {
    for (const status of ['missing', 'queued', 'in_progress']) {
      const f = fixture();
      if (status === 'missing') f.runs.splice(i, 1);
      else f.runs[i].status = status;
      assert.equal((await attempt(f)).merged, false);
    }
  });
}

for (const conclusion of ['failure', 'cancelled', 'neutral', 'skipped', null]) {
  test(`successful workflow with a ${conclusion} job cannot call merge`, async () => {
    const f = fixture();
    f.jobs.get(1)[0].conclusion = conclusion;
    assert.equal((await attempt(f)).merged, false);
  });
}

test('missing expected job or empty job list fails closed', async () => {
  for (const jobs of [[], fixture().jobs.get(1).slice(1)]) {
    const f = fixture();
    f.jobs.set(1, jobs);
    assert.equal((await attempt(f)).merged, false);
  }
});

test('green old run cannot hide a newer red run or running rerun', async () => {
  const f = fixture();
  const newer = clone(f.runs[0]);
  newer.id = 100;
  newer.conclusion = 'failure';
  f.runs.push(newer);
  assert.equal((await attempt(f)).merged, false);
  f.runs.pop();
  f.runs[0].run_attempt = 2;
  f.runs[0].status = 'in_progress';
  assert.equal((await attempt(f)).merged, false);
});

test('old head, push, dispatch, fork or another PR cannot satisfy policy', async () => {
  for (const mutate of [r => r.head_sha = 'old', r => r.event = 'push',
    r => r.event = 'workflow_dispatch', r => r.head_repository.full_name = 'attacker/fork',
    r => r.pull_requests[0].number = 998, r => r.head_branch = 'another-branch']) {
    const f = fixture();
    mutate(f.runs[0]);
    assert.equal((await attempt(f)).merged, false);
  }
});

test('empty run PR association is accepted only with exact repository, branch and SHA', async () => {
  const f = fixture(); f.runs.forEach(r => r.pull_requests = []);
  assert.equal((await attempt(f)).merged, true);
});

test('draft, fork, closed, human or other base cannot merge', () => {
  for (const mutate of [p => p.draft = true, p => p.state = 'closed',
    p => p.head.repo.full_name = 'attacker/fork', p => p.user.login = 'human',
    p => p.base.ref = 'release']) {
    const f = fixture(); mutate(f.pr);
    assert.equal(eligible(f.pr, repository), false);
  }
});

test('head or base changing after checks blocks merge', async () => {
  for (const side of ['head', 'base']) {
    const f = fixture();
    const c = await attempt(f, (state, reads) => { if (reads === 2) state.pr[side].sha = 'changed'; });
    assert.equal(c.merged, false);
    assert.deepEqual(c.writes, []);
  }
});

test('rerun beginning during final inspection blocks merge', async () => {
  const f = fixture();
  const c = await attempt(f, (state, reads) => {
    if (reads === 2) { state.runs[0].run_attempt = 2; state.runs[0].status = 'queued'; }
  });
  assert.equal(c.merged, false);
  assert.deepEqual(c.writes, []);
});

test('new successful attempt during inspection also needs a fresh decision', async () => {
  const f = fixture();
  assert.equal((await attempt(f, (state, reads) => {
    if (reads === 2) state.runs[0].run_attempt = 2;
  })).merged, false);
});

test('conflict, behind, unknown mergeability and blocked state cannot merge', async () => {
  for (const state of ['behind', 'dirty', 'blocked', 'unknown', 'unstable']) {
    const f = fixture(); f.pr.mergeable_state = state;
    assert.equal((await attempt(f)).merged, false);
  }
  const f = fixture(); f.pr.mergeable = null;
  assert.equal((await attempt(f)).merged, false);
});

test('protection must be active, strict, complete, app-bound, effective and without bypass', () => {
  assert.equal(protectedBy(ruleset, effective(ruleset.rules)), true);
  for (const mutate of [r => r.enforcement = 'evaluate',
    r => r.bypass_actors.push({ actor_id: 5, actor_type: 'Integration', bypass_mode: 'always' }),
    r => r.rules.pop(), r => r.rules[3].parameters.strict_required_status_checks_policy = false,
    r => r.rules[3].parameters.required_status_checks.pop(),
    r => r.rules[3].parameters.required_status_checks[0].integration_id = null]) {
    const r = clone(ruleset); mutate(r);
    assert.equal(protectedBy(r, effective(r.rules)), false);
  }
  assert.equal(protectedBy(ruleset, []), false);
});

test('checks from another effective ruleset cannot prove the no-bypass ruleset applies', () => {
  assert.equal(protectedBy(ruleset, ruleset.rules.map(rule => ({ ...rule, ruleset_id: 2 }))), false);
});

test('missing protection, API error or changed protection never merges', async () => {
  const f = fixture(); const c = client(f);
  c.github.paginate = async () => [];
  assert.equal(await mergeCandidate({ ...c, repo, number: 999 }), false);
  assert.deepEqual(c.writes, []);
  const e = client(f); e.github.request = async () => { throw new Error('403'); };
  await run({ ...e, context: { repo, eventName: 'pull_request_target', payload: { pull_request: f.pr } } });
  assert.equal(e.errors.length, 1); assert.deepEqual(e.writes, []);
  const changed = await attempt(f, (state, reads) => {
    if (reads === 2) state.rules.enforcement = 'disabled';
  });
  assert.equal(changed.merged, false);
});

test('old native auto-merge is revoked even while CI is red', async () => {
  const f = fixture(); f.pr.auto_merge = {}; f.runs[0].conclusion = 'failure';
  const c = await attempt(f);
  assert.deepEqual(c.writes, ['disable-auto-merge']);
  assert.equal(c.merged, false);
});

test('workflow_run with no associated PRs sweeps open bot PRs', async () => {
  const f = fixture(); const c = client(f);
  await run({ ...c, context: { repo, eventName: 'workflow_run', payload: { workflow_run: { pull_requests: [] } } } });
  assert.equal(c.writes.length, 1);
});

test('ruleset contexts and completion triggers stay synchronized with policy', () => {
  const rules = ruleset.rules.find(r => r.type === 'required_status_checks');
  assert.deepEqual(rules.parameters.required_status_checks.map(c => c.context).sort(), [...requiredContexts].sort());
  const workflow = fs.readFileSync(path.join(__dirname, '../workflows/Auto-merge-Dependabot.yml'), 'utf8');
  for (const w of Object.values(policy.workflows)) assert.ok(workflow.includes(`      - ${w.name}\n`));
  assert.ok(workflow.includes('ref: ${{ github.sha }}'));
  assert.ok(workflow.includes('persist-credentials: false'));
  assert.ok(!workflow.includes('enable-pull-request-automerge'));
  assert.ok(!workflow.includes('github.event.pull_request.head.sha'));
  const modern = fs.readFileSync(path.join(__dirname, '../workflows/python-modernization.yml'), 'utf8');
  const enforcement = modern.split('name: Enforce strict failure when checks did not pass')[1];
  assert.ok(enforcement.includes('exit 1'));
  assert.ok(!enforcement.includes('exit 0'));
});

// Replay the real failures seen at the three merged Dependabot heads.
for (const [number, sha] of [[452, 'b6c439e67d3f383bb5542508d836442ecfb2446b'],
  [453, '69daa266dab890c77e4b19151df3e3c53268232b'],
  [454, '196bd4d6e627457c52f80f50a7bf604faba56d5b']]) {
  test(`historical #${number}: Dependency Review success cannot override six red workflows`, async () => {
    const f = fixture(); f.pr.number = number; f.pr.head.sha = sha;
    f.runs.forEach(r => {
      r.head_sha = sha; r.pull_requests[0].number = number; r.pull_requests[0].head.sha = sha;
      if (!['dependency-review.yml', 'merge-safety-regression.yml'].some(p => r.path.endsWith(p))) {
        r.conclusion = 'failure';
      }
    });
    assert.equal(successful(latestRuns(f.runs, f.pr, repository), f.jobs), false);
    const c = await attempt(f);
    assert.equal(c.merged, false); assert.deepEqual(c.writes, []);
  });
}
