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

function audit(api) {
  console.log('Read-only audit. Add --apply to disable native auto-merge and enforce main-required-ci.');
  console.log(JSON.stringify({ allow_auto_merge: api(prefix).allow_auto_merge }));
  console.log(JSON.stringify(api(`${prefix}/branches/main`), null, 2));
  console.log(JSON.stringify(api(`${prefix}/branches/main/protection`), null, 2));
  console.log(JSON.stringify(api(`${prefix}/rulesets?includes_parents=true`), null, 2));
}

function apply(api) {
  // Native auto-merge accepts skipped/neutral checks, unlike the exact-success gate.
  api(prefix, 'PATCH', { allow_auto_merge: false });
  if (api(prefix).allow_auto_merge !== false) throw new Error('Native auto-merge disable readback failed');
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
  const verified = { id: readback.id, updated_at: readback.updated_at };
  // An administrative read must expose the actual bypass list before attesting it.
  if (!Array.isArray(readback.bypass_actors) || !protectedBy(readback, effective, verified) ||
      api(prefix).allow_auto_merge !== false) throw new Error('Ruleset/settings readback failed');
  console.log(`Verified active strict ruleset ${updated.id} on main, no bypass actors.`);
  console.log('Record this admin-verified revision in policy.json through a reviewed commit:');
  console.log(JSON.stringify({ verified_ruleset: verified }, null, 2));
  console.log('Until that revision is on main, the merge workflow remains blocked.');
  return verified;
}

if (require.main === module) {
  if (process.argv.includes('--apply')) apply(api);
  else audit(api);
}

module.exports = { apply };
