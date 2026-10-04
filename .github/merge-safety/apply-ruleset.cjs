'use strict';

// Explicit administrative step; no tokens are stored in this repository.
const { execFileSync } = require('node:child_process');
const { protectedBy, policy } = require('./gate.cjs');
const desired = require('./main-ruleset.json');
const repository = 'Bigdaddy1990/jackery_solarvault';
const prefix = `repos/${repository}`;
const api = (endpoint, method = 'GET', body) => JSON.parse(execFileSync('gh',
  ['api', endpoint, '--method', method, ...(body ? ['--input', '-'] : [])],
  { encoding: 'utf8', input: body ? JSON.stringify(body) : undefined }));

if (!process.argv.includes('--apply')) {
  console.log('Read-only audit. Add --apply to create/update main-required-ci.');
  console.log(JSON.stringify(api(`${prefix}/branches/main`), null, 2));
  console.log(JSON.stringify(api(`${prefix}/branches/main/protection`), null, 2));
  console.log(JSON.stringify(api(`${prefix}/rulesets?includes_parents=true`), null, 2));
} else {
  const listed = api(`${prefix}/rulesets?includes_parents=true`);
  const existing = listed.find(r => r.name === policy.ruleset_name);
  let body = structuredClone(desired);
  if (existing) {
    if (existing.source !== repository) throw new Error('Inherited ruleset must be edited at its source');
    const current = api(`${prefix}/rulesets/${existing.id}`);
    // Preserve unrelated restrictions and stricter review requirements.
    for (const rule of current.rules) {
      const matching = body.rules.find(r => r.type === rule.type);
      if (!matching) body.rules.push(rule);
      else if (rule.type === 'pull_request') {
        matching.parameters = { ...rule.parameters, ...matching.parameters,
          required_approving_review_count: Math.max(rule.parameters.required_approving_review_count || 0, 0),
          require_code_owner_review: rule.parameters.require_code_owner_review === true,
          require_last_push_approval: rule.parameters.require_last_push_approval === true,
          allowed_merge_methods: rule.parameters.allowed_merge_methods || matching.parameters.allowed_merge_methods };
      } else if (rule.type === 'required_status_checks') {
        const checks = matching.parameters.required_status_checks;
        for (const check of rule.parameters.required_status_checks) {
          if (!checks.some(c => c.context === check.context)) checks.push(check);
        }
      }
    }
  }
  const updated = api(`${prefix}/rulesets${existing ? '/' + existing.id : ''}`,
    existing ? 'PUT' : 'POST', body);
  const readback = api(`${prefix}/rulesets/${updated.id}`);
  const effective = api(`${prefix}/rules/branches/main`);
  if (!protectedBy(readback, effective)) throw new Error('Ruleset readback/effective enforcement failed');
  console.log(`Verified active strict ruleset ${updated.id} on main, no bypass actors.`);
  console.log('Run Auto-merge Dependabot via workflow_dispatch to audit open bot PRs.');
}
