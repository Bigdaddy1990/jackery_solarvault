'use strict';

const policy = require('./policy.json');
const requiredContexts = Object.values(policy.workflows).flatMap(w => w.jobs);

function eligible(pr, repository) {
  return pr.state === 'open' && !pr.draft &&
    pr.user?.login === 'dependabot[bot]' &&
    pr.base?.ref === policy.base && pr.base?.repo?.full_name === repository &&
    pr.head?.repo?.full_name === repository;
}

function requiredRule(rule) {
  const params = rule?.parameters;
  return rule?.type === 'required_status_checks' &&
    params?.strict_required_status_checks_policy === true &&
    params?.do_not_enforce_on_create !== true &&
    requiredContexts.every(name => params.required_status_checks?.some(
      check => check.context === name && check.integration_id === policy.integration_id
    ));
}

function protectedBy(ruleset, effectiveRules) {
  return ruleset?.name === policy.ruleset_name && ruleset.enforcement === 'active' &&
    ruleset.target === 'branch' && ruleset.bypass_actors?.length === 0 &&
    ['pull_request', 'deletion', 'non_fast_forward'].every(type =>
      ruleset.rules?.some(rule => rule.type === type)) &&
    ruleset.rules.some(requiredRule) && Number.isInteger(ruleset.id) &&
    effectiveRules.some(rule => rule.ruleset_id === ruleset.id && requiredRule(rule));
}

function latestRuns(runs, pr, repository) {
  return Object.keys(policy.workflows).map(file => {
    const candidates = runs.filter(run =>
      run.path === `.github/workflows/${file}` && run.event === 'pull_request' &&
      run.head_sha === pr.head.sha && run.head_branch === pr.head.ref &&
      run.head_repository?.full_name === repository &&
      Array.isArray(run.pull_requests) &&
      (run.pull_requests.length === 0 || run.pull_requests.some(item => item.number === pr.number &&
        item.head?.sha === pr.head.sha && item.base?.ref === policy.base)));
    return candidates.sort((a, b) => b.id - a.id || b.run_attempt - a.run_attempt)[0];
  });
}

function successful(runs, jobsByRun) {
  return runs.every((run, index) => {
    if (!run || run.status !== 'completed' || run.conclusion !== 'success') return false;
    const jobs = jobsByRun.get(run.id);
    const expected = Object.values(policy.workflows)[index].jobs;
    return jobs?.length > 0 && expected.every(name => jobs.some(job => job.name === name)) &&
      jobs.every(job => job.run_id === run.id && job.status === 'completed' &&
        job.conclusion === 'success');
  });
}

async function inspect(github, repo, pr) {
  const repository = `${repo.owner}/${repo.repo}`;
  const summaries = await github.paginate('GET /repos/{owner}/{repo}/rulesets', {
    ...repo, includes_parents: true, per_page: 100
  });
  const summary = summaries.find(item => item.name === policy.ruleset_name);
  if (!summary) return { ok: false, reason: 'Mandatory active ruleset is missing' };
  const { data: ruleset } = await github.request(
    'GET /repos/{owner}/{repo}/rulesets/{ruleset_id}', { ...repo, ruleset_id: summary.id }
  );
  const { data: effectiveRules } = await github.request(
    'GET /repos/{owner}/{repo}/rules/branches/{branch}', { ...repo, branch: policy.base }
  );
  if (!protectedBy(ruleset, effectiveRules)) {
    return { ok: false, reason: 'Ruleset is inactive, bypassable, incomplete or not effective' };
  }
  const runs = await github.paginate(github.rest.actions.listWorkflowRunsForRepo, {
    ...repo, head_sha: pr.head.sha, event: 'pull_request', per_page: 100
  });
  const selected = latestRuns(runs, pr, repository);
  const jobsByRun = new Map();
  for (const run of selected) {
    if (!run || run.status !== 'completed' || run.conclusion !== 'success') continue;
    // Explicit attempt avoids accepting jobs from an earlier successful rerun.
    const jobs = await github.paginate(
      'GET /repos/{owner}/{repo}/actions/runs/{run_id}/attempts/{attempt_number}/jobs',
      { ...repo, run_id: run.id, attempt_number: run.run_attempt, per_page: 100 }
    );
    jobsByRun.set(run.id, jobs);
  }
  return {
    ok: successful(selected, jobsByRun),
    reason: 'All mandatory current-head workflows and jobs must complete with success',
    snapshot: selected.map(run => run && `${run.id}:${run.run_attempt}`).join(',')
  };
}

async function mergeCandidate({ github, repo, number, core }) {
  const repository = `${repo.owner}/${repo.repo}`;
  const get = async () => (await github.rest.pulls.get({ ...repo, pull_number: number })).data;
  const pr = await get();
  if (!eligible(pr, repository)) return false;
  // Never leave an old native auto-merge request armed for a future, unchecked head.
  if (pr.auto_merge) {
    await github.graphql(
      'mutation($id: ID!) { disablePullRequestAutoMerge(input: {pullRequestId: $id}) { clientMutationId } }',
      { id: pr.node_id }
    );
  }
  const first = await inspect(github, repo, pr);
  if (!first.ok) {
    core.info(`#${number}: BLOCKED (${pr.head.sha}): ${first.reason}`);
    return false;
  }
  const current = await get();
  if (!eligible(current, repository) || current.head.sha !== pr.head.sha ||
      current.base.sha !== pr.base.sha || current.mergeable !== true ||
      current.mergeable_state !== 'clean' || current.auto_merge) {
    core.info(`#${number}: BLOCKED: head/base changed or merge is not clean`);
    return false;
  }
  // Detect reruns and ruleset changes made while the first inspection was running.
  const final = await inspect(github, repo, current);
  if (!final.ok || final.snapshot !== first.snapshot) {
    core.info(`#${number}: BLOCKED: checks or protection changed during inspection`);
    return false;
  }
  // GitHub atomically rejects an updated head (409) and enforces strict required checks.
  const { data: result } = await github.rest.pulls.merge({
    ...repo, pull_number: number, sha: pr.head.sha, merge_method: 'squash'
  });
  if (result.merged !== true) throw new Error(`GitHub refused merge of #${number}`);
  core.info(`#${number}: merged verified head ${pr.head.sha} as ${result.sha}`);
  return true;
}

async function run({ github, context, core }) {
  const repo = context.repo;
  const event = context.payload;
  let candidates;
  if (context.eventName === 'pull_request_target') {
    candidates = [event.pull_request.number];
  } else {
    const open = await github.paginate(github.rest.pulls.list, {
      ...repo, state: 'open', base: policy.base, per_page: 100
    });
    // A sweep also handles workflow_run payloads with an empty pull_requests array.
    candidates = open.filter(pr => pr.user?.login === 'dependabot[bot]').map(pr => pr.number);
  }
  for (const number of candidates) {
    try {
      await mergeCandidate({ github, repo, number, core });
    } catch (error) {
      // API errors, including unavailable protection, always fail closed.
      core.setFailed(`#${number}: no merge: ${error.message}`);
    }
  }
}

module.exports = { policy, requiredContexts, eligible, protectedBy, latestRuns, successful,
  inspect, mergeCandidate, run };
