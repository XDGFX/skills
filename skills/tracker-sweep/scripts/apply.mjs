#!/usr/bin/env node
// Apply an approved sweep plan — but validate every action against the live
// tracker first, and write nothing at all unless the whole plan validates.
//
//   node apply.mjs --plan plan.json                 # validate only (default)
//   node apply.mjs --plan plan.json --confirm       # validate, then write
//   node apply.mjs --plan plan.json --confirm --allow-drift
//
// The gate exists because a sweep reasons over a cache, and a cache is a claim
// about the past. Between the fetch and the write, somebody may have closed the
// issue, set the edge by hand, or moved the work. Validation re-reads the live
// state of every issue a plan touches and refuses the plan if reality has moved
// underneath it. Partial application is never on the table: a half-applied
// dependency graph is worse than an unapplied one.
//
// Plan format:
// {
//   "repo": "owner/name",
//   "fetchedAt": "<cache.fetchedAt — drift is measured against this>",
//   "actions": [
//     { "action": "add-blocked-by",    "issue": 81, "blocker": 127, "why": "..." },
//     { "action": "remove-blocked-by", "issue": 81, "blocker": 110, "why": "..." },
//     { "action": "add-sub-issue",     "parent": 66, "child": 171,  "why": "..." },
//     { "action": "add-label",         "issue": 193, "label": "ready-for-agent" },
//     { "action": "remove-label",      "issue": 170, "label": "needs-triage" },
//     { "action": "comment",           "issue": 66, "body": "..." },
//     { "action": "close",             "issue": 158, "reason": "completed", "comment": "..." }
//   ]
// }

import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';

const args = Object.fromEntries(
    process.argv.slice(2).flatMap((a, i, all) =>
        a.startsWith('--') ? [[a.slice(2), all[i + 1]?.startsWith('--') === false ? all[i + 1] : true]] : []),
);
if (!args.plan) {
    console.error('usage: apply.mjs --plan plan.json [--confirm] [--allow-drift] [--receipt PATH]');
    process.exit(2);
}

const plan = JSON.parse(readFileSync(args.plan, 'utf8'));
const [owner, name] = plan.repo.split('/');
const confirm = !!args.confirm;
const allowDrift = !!args['allow-drift'];

const gh = (argv) => execFileSync('gh', argv, { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
const api = (path, opts = []) => JSON.parse(gh(['api', `repos/${owner}/${name}/${path}`, ...opts]) || 'null');

// One live read per issue the plan touches, cached for the run.
const live = new Map();
const fetchIssue = (n) => {
    if (live.has(n)) return live.get(n);
    let issue = null;
    try {
        issue = api(`issues/${n}`);
        issue.blockedBy = api(`issues/${n}/dependencies/blocked_by`).map((x) => x.number);
        issue.subIssueNumbers = (() => {
            try { return api(`issues/${n}/sub_issues`).map((x) => x.number); } catch { return []; }
        })();
    } catch {
        issue = null;
    }
    live.set(n, issue);
    return issue;
};

const numbersIn = (a) => [a.issue, a.blocker, a.parent, a.child].filter((n) => typeof n === 'number');

// ── Validate ───────────────────────────────────────────────────────────────────

const problems = [];
const noops = [];
const ready = [];

const fail = (a, why) => problems.push({ action: a, why });
const noop = (a, why) => noops.push({ action: a, why });

for (const a of plan.actions) {
    const touched = numbersIn(a);
    const missing = touched.filter((n) => !fetchIssue(n));
    if (missing.length) {
        fail(a, `issue(s) ${missing.map((n) => '#' + n).join(', ')} could not be read`);
        continue;
    }

    // Drift: has anything the plan touches moved since the cache was taken?
    if (plan.fetchedAt && !allowDrift) {
        const moved = touched.filter((n) => Date.parse(fetchIssue(n).updated_at) > Date.parse(plan.fetchedAt));
        if (moved.length) {
            fail(a, `#${moved.join(', #')} changed since the sweep was taken ` +
                `(${moved.map((n) => fetchIssue(n).updated_at).join(', ')}) — re-read before applying, or pass --allow-drift`);
            continue;
        }
    }

    switch (a.action) {
        case 'add-blocked-by': {
            const i = fetchIssue(a.issue);
            const b = fetchIssue(a.blocker);
            if (i.blockedBy.includes(a.blocker)) { noop(a, `#${a.issue} already declares #${a.blocker}`); break; }
            if (a.issue === a.blocker) { fail(a, 'an issue cannot block itself'); break; }
            if (b.state === 'closed') {
                fail(a, `#${a.blocker} is closed — a closed blocker declares nothing; drop the action or reopen it`);
                break;
            }
            if (i.state === 'closed') { fail(a, `#${a.issue} is closed — adding a blocker to it says nothing`); break; }
            // Would this close a cycle? Walk the live graph from the blocker.
            const seen = new Set();
            const reaches = (from, target) => {
                if (from === target) return true;
                if (seen.has(from)) return false;
                seen.add(from);
                const node = fetchIssue(from);
                return (node?.blockedBy ?? []).some((n) => reaches(n, target));
            };
            if (reaches(a.blocker, a.issue)) { fail(a, `would create a dependency cycle via #${a.blocker}`); break; }
            ready.push(a);
            break;
        }
        case 'remove-blocked-by': {
            const i = fetchIssue(a.issue);
            if (!i.blockedBy.includes(a.blocker)) { noop(a, `#${a.issue} does not declare #${a.blocker}`); break; }
            ready.push(a);
            break;
        }
        case 'add-sub-issue': {
            const p = fetchIssue(a.parent);
            const c = fetchIssue(a.child);
            if (p.subIssueNumbers.includes(a.child)) { noop(a, `#${a.child} is already a child of #${a.parent}`); break; }
            if (c.parent && c.parent.number !== a.parent) {
                fail(a, `#${a.child} already has parent #${c.parent.number} — reparenting is not a sweep action`);
                break;
            }
            if (a.parent === a.child) { fail(a, 'an issue cannot parent itself'); break; }
            ready.push(a);
            break;
        }
        case 'remove-sub-issue': {
            const p = fetchIssue(a.parent);
            if (!p.subIssueNumbers.includes(a.child)) { noop(a, `#${a.child} is not a child of #${a.parent}`); break; }
            ready.push(a);
            break;
        }
        case 'add-label': {
            const i = fetchIssue(a.issue);
            if (i.labels.some((l) => l.name === a.label)) { noop(a, `#${a.issue} already carries "${a.label}"`); break; }
            const known = JSON.parse(gh(['api', `repos/${owner}/${name}/labels?per_page=100`]))
                .map((l) => l.name);
            if (!known.includes(a.label)) { fail(a, `label "${a.label}" does not exist in this repo`); break; }
            ready.push(a);
            break;
        }
        case 'remove-label': {
            const i = fetchIssue(a.issue);
            if (!i.labels.some((l) => l.name === a.label)) { noop(a, `#${a.issue} does not carry "${a.label}"`); break; }
            ready.push(a);
            break;
        }
        case 'close': {
            const i = fetchIssue(a.issue);
            if (i.state === 'closed') { noop(a, `#${a.issue} is already closed`); break; }
            if (!a.comment) { fail(a, 'closing without a comment leaves no record of why — add one'); break; }
            const blockers = i.blockedBy.filter((n) => fetchIssue(n)?.state === 'open');
            if (blockers.length) {
                fail(a, `#${a.issue} still declares open blocker(s) #${blockers.join(', #')} — resolve those first`);
                break;
            }
            ready.push(a);
            break;
        }
        case 'comment': {
            if (!a.body || !a.body.trim()) { fail(a, 'empty comment'); break; }
            ready.push(a);
            break;
        }
        default:
            fail(a, `unknown action "${a.action}"`);
    }
}

// ── Report ─────────────────────────────────────────────────────────────────────

const describe = (a) => {
    switch (a.action) {
        case 'add-blocked-by': return `#${a.issue} blocked by #${a.blocker}`;
        case 'remove-blocked-by': return `#${a.issue} no longer blocked by #${a.blocker}`;
        case 'add-sub-issue': return `#${a.child} becomes a child of #${a.parent}`;
        case 'remove-sub-issue': return `#${a.child} leaves #${a.parent}`;
        case 'add-label': return `#${a.issue} + label "${a.label}"`;
        case 'remove-label': return `#${a.issue} − label "${a.label}"`;
        case 'close': return `close #${a.issue}`;
        case 'comment': return `comment on #${a.issue}`;
        default: return JSON.stringify(a);
    }
};

console.log(`plan: ${plan.actions.length} action(s) against ${plan.repo}\n`);
if (ready.length) {
    console.log(`VALID (${ready.length}):`);
    for (const a of ready) console.log(`  ✓ ${describe(a)}${a.why ? ` — ${a.why}` : ''}`);
    console.log('');
}
if (noops.length) {
    console.log(`ALREADY TRUE (${noops.length}) — skipped:`);
    for (const n of noops) console.log(`  · ${describe(n.action)} — ${n.why}`);
    console.log('');
}
if (problems.length) {
    console.log(`REFUSED (${problems.length}):`);
    for (const p of problems) console.log(`  ✗ ${describe(p.action)} — ${p.why}`);
    console.log('');
}

if (problems.length) {
    console.log('Nothing was written: the whole plan is refused while any action fails validation.');
    console.log('Fix or drop the refused actions and re-run.');
    process.exit(1);
}
if (!confirm) {
    console.log(`Validation passed. Nothing written — re-run with --confirm to apply ${ready.length} action(s).`);
    process.exit(0);
}

// ── Write ──────────────────────────────────────────────────────────────────────

const idOf = (n) => fetchIssue(n).id;
const receipt = [];
for (const a of ready) {
    try {
        switch (a.action) {
            case 'add-blocked-by':
                gh(['api', '--method', 'POST', `repos/${owner}/${name}/issues/${a.issue}/dependencies/blocked_by`,
                    '-F', `issue_id=${idOf(a.blocker)}`]);
                break;
            case 'remove-blocked-by':
                gh(['api', '--method', 'DELETE',
                    `repos/${owner}/${name}/issues/${a.issue}/dependencies/blocked_by/${idOf(a.blocker)}`]);
                break;
            case 'add-sub-issue':
                gh(['api', '--method', 'POST', `repos/${owner}/${name}/issues/${a.parent}/sub_issues`,
                    '-F', `sub_issue_id=${idOf(a.child)}`]);
                break;
            case 'remove-sub-issue':
                gh(['api', '--method', 'DELETE', `repos/${owner}/${name}/issues/${a.parent}/sub_issue`,
                    '-F', `sub_issue_id=${idOf(a.child)}`]);
                break;
            case 'add-label':
                gh(['issue', 'edit', String(a.issue), '--repo', plan.repo, '--add-label', a.label]);
                break;
            case 'remove-label':
                gh(['issue', 'edit', String(a.issue), '--repo', plan.repo, '--remove-label', a.label]);
                break;
            case 'comment':
                gh(['issue', 'comment', String(a.issue), '--repo', plan.repo, '--body', a.body]);
                break;
            case 'close':
                gh(['issue', 'comment', String(a.issue), '--repo', plan.repo, '--body', a.comment]);
                gh(['issue', 'close', String(a.issue), '--repo', plan.repo,
                    '--reason', a.reason === 'not planned' ? 'not planned' : 'completed']);
                break;
        }
        receipt.push({ ...a, applied: true });
        console.log(`  ✓ ${describe(a)}`);
    } catch (e) {
        receipt.push({ ...a, applied: false, error: String(e.message).slice(0, 300) });
        console.log(`  ✗ ${describe(a)} — FAILED: ${String(e.message).slice(0, 200)}`);
    }
}

const applied = receipt.filter((r) => r.applied).length;
console.log(`\napplied ${applied} of ${ready.length}`);
if (args.receipt) {
    writeFileSync(args.receipt, JSON.stringify({ repo: plan.repo, at: new Date().toISOString(), receipt }, null, 2));
    console.log(`receipt written to ${args.receipt}`);
}
process.exit(receipt.some((r) => !r.applied) ? 1 : 0);
