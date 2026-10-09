#!/usr/bin/env node
// Deterministic checks over a tracker cache. No network, no model, no judgement:
// everything here is a fact about the graph or the text, so it is either true or
// it is a bug in this file.
//
//   node checks.mjs [--cache .tracker-sweep/cache.json] [--config .tracker-sweep/config.json]
//                   [--json] [--only kind,kind]
//
// Findings that carry a `fix` are mechanically applicable by apply.mjs. Findings
// without one are for a human or a model to judge — they are surfaced as
// candidates, never as conclusions.

import { readFileSync, existsSync } from 'node:fs';

const args = Object.fromEntries(
    process.argv.slice(2).flatMap((a, i, all) =>
        a.startsWith('--') ? [[a.slice(2), all[i + 1]?.startsWith('--') === false ? all[i + 1] : true]] : []),
);

const cachePath = args.cache || '.tracker-sweep/cache.json';
if (!existsSync(cachePath)) {
    console.error(`no cache at ${cachePath} — run fetch-tracker.mjs first`);
    process.exit(2);
}
const cache = JSON.parse(readFileSync(cachePath, 'utf8'));

const DEFAULTS = {
    // Titles matching this become candidates for epic membership.
    epicChildTitlePattern: null,   // e.g. "^v1: "
    epics: [],                     // issue numbers that are epics; [] = infer from subIssues
    requiredLabelGroups: [],       // e.g. [["bug","enhancement","question"]] — one of each group
    staleOpenDays: 45,
    staleAssignmentDays: 14,
    duplicateThreshold: 0.55,
    ignoreLabels: ['wontfix', 'icebox', 'parked'],
};
const configPath = args.config || '.tracker-sweep/config.json';
const config = { ...DEFAULTS, ...(existsSync(configPath) ? JSON.parse(readFileSync(configPath, 'utf8')) : {}) };

const issues = cache.issues;
const byNumber = new Map(issues.map((i) => [i.number, i]));
const open = issues.filter((i) => i.state === 'OPEN');
const isOpen = (n) => byNumber.get(n)?.state === 'OPEN';
const ignored = (i) => i.labels.some((l) => config.ignoreLabels.includes(l));
const days = (iso) => (Date.now() - Date.parse(iso)) / 86400000;

const findings = [];
const add = (f) => findings.push(f);

// ── Graph integrity ────────────────────────────────────────────────────────────

// An open issue every one of whose declared blockers has closed is buildable now.
// Not a defect, but the single most useful thing a sweep produces, and the thing
// a stale hand-written "frontier" comment always gets wrong.
for (const i of open) {
    if (ignored(i) || i.blockedBy.length === 0) continue;
    const live = i.blockedBy.filter((b) => isOpen(b.number));
    if (live.length === 0) {
        add({
            kind: 'unblocked',
            severity: 'info',
            issues: [i.number],
            summary: `#${i.number} declares ${i.blockedBy.length} blocker(s), all closed — it is buildable now`,
            evidence: i.blockedBy.map((b) => `#${b.number} ${b.state.toLowerCase()}`).join(', '),
        });
    }
}

// A closed issue still declaring an open blocker: either it closed early, or the
// edge outlived its meaning.
for (const i of issues.filter((x) => x.state === 'CLOSED')) {
    const live = i.blockedBy.filter((b) => isOpen(b.number));
    if (live.length) {
        add({
            kind: 'closed-with-open-blocker',
            severity: 'warn',
            issues: [i.number, ...live.map((b) => b.number)],
            summary: `#${i.number} is closed but still declares open blocker(s) ${live.map((b) => '#' + b.number).join(', ')}`,
            evidence: 'Either the work shipped around the blocker (drop the edge) or it closed prematurely.',
        });
    }
}

// Dependency cycles make "what is buildable" unanswerable.
{
    const colour = new Map();
    const stack = [];
    const walk = (n) => {
        if (colour.get(n) === 'done') return;
        if (colour.get(n) === 'open') {
            const cycle = stack.slice(stack.indexOf(n)).concat(n);
            add({
                kind: 'dependency-cycle',
                severity: 'error',
                issues: [...new Set(cycle)],
                summary: `Dependency cycle: ${cycle.map((x) => '#' + x).join(' → ')}`,
                evidence: 'Nothing in the cycle can ever be unblocked.',
            });
            return;
        }
        colour.set(n, 'open');
        stack.push(n);
        for (const b of byNumber.get(n)?.blockedBy ?? []) walk(b.number);
        stack.pop();
        colour.set(n, 'done');
    };
    for (const i of issues) walk(i.number);
}

// ── Epic / sub-issue hygiene ───────────────────────────────────────────────────

const epics = config.epics.length
    ? config.epics.map((n) => byNumber.get(n)).filter(Boolean)
    : issues.filter((i) => i.subIssues.length > 0);

for (const e of epics) {
    const openKids = e.subIssues.filter((s) => s.state === 'OPEN');
    if (e.state === 'OPEN' && e.subIssues.length && openKids.length === 0) {
        add({
            kind: 'epic-complete',
            severity: 'warn',
            issues: [e.number],
            summary: `Epic #${e.number} has ${e.subIssues.length} children and every one is closed`,
            evidence: 'Either it is finished, or work is happening under it that was never made a child.',
        });
    }
    if (e.state === 'CLOSED' && openKids.length) {
        add({
            kind: 'epic-closed-with-open-children',
            severity: 'warn',
            issues: [e.number, ...openKids.map((k) => k.number)],
            summary: `Epic #${e.number} is closed but ${openKids.length} child(ren) are open`,
            evidence: openKids.map((k) => '#' + k.number).join(', '),
        });
    }
}

// An issue that looks like a child of an epic by naming convention, but is not one.
if (config.epicChildTitlePattern) {
    const re = new RegExp(config.epicChildTitlePattern);
    for (const i of open) {
        if (i.parent || !re.test(i.title) || ignored(i)) continue;
        add({
            kind: 'orphan-epic-child',
            severity: 'warn',
            issues: [i.number],
            summary: `#${i.number} matches the epic-child title convention but has no parent epic`,
            evidence: `title: ${i.title}`,
            fix: epics.length === 1 ? { action: 'add-sub-issue', parent: epics[0].number, child: i.number } : undefined,
        });
    }
}

// ── Prose that should be a tracker edge ────────────────────────────────────────
// The recurring failure: a comment or body announces a blocker, everyone believes
// it, and the graph never learns. Body-only here; the model pass reads comments.

const REF = /#(\d{1,6})\b/g;
const BLOCKED_PHRASES = [
    /blocked\s+by[^.\n]*?#(\d+)/gi,
    /waiting\s+on[^.\n]*?#(\d+)/gi,
    /gated\s+(?:on|by)[^.\n]*?#(\d+)/gi,
    /depends\s+on[^.\n]*?#(\d+)/gi,
];
const BLOCKING_PHRASES = [
    /blocks?\s+#(\d+)/gi,
    /now\s+blocks?\s+#(\d+)/gi,
];

for (const i of open) {
    if (!i.body) continue;
    const declared = new Set(i.blockedBy.map((b) => b.number));
    const declaredOut = new Set(i.blocking.map((b) => b.number));

    for (const re of BLOCKED_PHRASES) {
        for (const m of i.body.matchAll(re)) {
            const n = Number(m[1]);
            const line = m[0].replace(/\s+/g, ' ').trim();
            // Struck-through references are the convention for a superseded blocker.
            if (i.body.includes(`~~#${n}~~`)) continue;
            if (!byNumber.has(n) || declared.has(n) || n === i.number) continue;
            add({
                kind: 'prose-blocker-not-declared',
                severity: isOpen(n) ? 'error' : 'info',
                issues: [i.number, n],
                summary: `#${i.number} says it is blocked by #${n} in prose, but no tracker edge exists`,
                evidence: `"${line}" — #${n} is ${byNumber.get(n).state.toLowerCase()}`,
                fix: isOpen(n) ? { action: 'add-blocked-by', issue: i.number, blocker: n } : undefined,
            });
        }
    }
    for (const re of BLOCKING_PHRASES) {
        for (const m of i.body.matchAll(re)) {
            const n = Number(m[1]);
            if (!byNumber.has(n) || declaredOut.has(n) || n === i.number) continue;
            if (!isOpen(n)) continue;
            add({
                kind: 'prose-blocking-not-declared',
                severity: 'error',
                issues: [i.number, n],
                summary: `#${i.number} says it blocks #${n} in prose, but no tracker edge exists`,
                evidence: `"${m[0].replace(/\s+/g, ' ').trim()}"`,
                fix: { action: 'add-blocked-by', issue: n, blocker: i.number },
            });
        }
    }
}

// ── Stale prose ────────────────────────────────────────────────────────────────
// An open issue pointing at issues that have since closed. Not a defect by itself
// — most references are historical — so these go to the model pass ranked by how
// much of the body is downstream of the reference.

for (const i of open) {
    if (!i.body) continue;
    const refs = [...new Set([...i.body.matchAll(REF)].map((m) => Number(m[1])))]
        .filter((n) => n !== i.number && byNumber.has(n));
    const closedSince = refs.filter((n) => {
        const r = byNumber.get(n);
        return r.state === 'CLOSED' && r.closedAt && Date.parse(r.closedAt) > Date.parse(i.createdAt);
    });
    const dangling = refs.length ? [] : [];
    if (closedSince.length) {
        add({
            kind: 'references-closed-since',
            severity: 'info',
            issues: [i.number, ...closedSince],
            summary: `#${i.number} references ${closedSince.length} issue(s) that closed after it was written`,
            evidence: closedSince.map((n) => `#${n} closed ${byNumber.get(n).closedAt?.slice(0, 10)}`).join(', '),
            needsJudgement: 'Does the body still read correctly now those are done?',
        });
    }
    void dangling;
}

// A checklist item pointing at a closed issue is done and nobody ticked it.
for (const i of open) {
    if (!i.body) continue;
    for (const m of i.body.matchAll(/^\s*[-*]\s*\[ \]\s*(.*#(\d+).*)$/gim)) {
        const n = Number(m[2]);
        if (byNumber.has(n) && !isOpen(n)) {
            add({
                kind: 'unticked-done-item',
                severity: 'info',
                issues: [i.number, n],
                summary: `#${i.number} has an unticked checklist item pointing at closed #${n}`,
                evidence: m[1].trim().slice(0, 120),
            });
        }
    }
}

// ── Pull requests ──────────────────────────────────────────────────────────────

for (const p of cache.pullRequests) {
    for (const c of p.closes) {
        if (c.state === 'CLOSED') {
            add({
                kind: 'open-pr-for-closed-issue',
                severity: 'warn',
                issues: [c.number],
                summary: `PR #${p.number} is open but its issue #${c.number} is already closed`,
                evidence: `${p.isDraft ? 'draft' : 'ready'}, last touched ${p.updatedAt.slice(0, 10)} — ${p.title}`,
            });
        }
    }
    if (p.closes.length === 0) {
        add({
            kind: 'pr-closes-nothing',
            severity: 'info',
            issues: [],
            summary: `PR #${p.number} declares no closing issue`,
            evidence: p.title.slice(0, 100),
        });
    }
    if (days(p.updatedAt) > config.staleAssignmentDays) {
        add({
            kind: 'stale-pr',
            severity: 'info',
            issues: p.closes.map((c) => c.number),
            summary: `PR #${p.number} untouched for ${Math.round(days(p.updatedAt))} days`,
            evidence: `${p.isDraft ? 'draft' : 'ready'} — ${p.title.slice(0, 80)}`,
        });
    }
}

// ── Labels, assignment, staleness ──────────────────────────────────────────────

for (const i of open) {
    if (ignored(i)) continue;
    if (i.labels.length === 0) {
        add({
            kind: 'unlabelled',
            severity: 'warn',
            issues: [i.number],
            summary: `#${i.number} carries no labels`,
            evidence: i.title.slice(0, 100),
        });
    }
    for (const group of config.requiredLabelGroups) {
        if (!group.some((l) => i.labels.includes(l))) {
            add({
                kind: 'missing-label-group',
                severity: 'info',
                issues: [i.number],
                summary: `#${i.number} carries none of [${group.join(', ')}]`,
                evidence: `has: ${i.labels.join(', ') || 'none'}`,
            });
        }
    }
    if (i.assignees.length && days(i.updatedAt) > config.staleAssignmentDays) {
        add({
            kind: 'stale-assignment',
            severity: 'warn',
            issues: [i.number],
            summary: `#${i.number} assigned to ${i.assignees.join(', ')} but untouched for ${Math.round(days(i.updatedAt))} days`,
            evidence: 'A held ticket nobody is advancing blocks whoever would pick it up.',
        });
    }
    if (days(i.updatedAt) > config.staleOpenDays) {
        add({
            kind: 'stale-open',
            severity: 'info',
            issues: [i.number],
            summary: `#${i.number} untouched for ${Math.round(days(i.updatedAt))} days`,
            evidence: i.title.slice(0, 100),
        });
    }
}

// ── Duplicate candidates ───────────────────────────────────────────────────────
// Title-token overlap only. Deliberately noisy-but-cheap: it proposes pairs for
// the model to read, and never concludes anything on its own.

const STOP = new Set(('the a an and or of to for in on is it as at by with be are that this what does do' +
    ' should can from into not no its their our your v1 build add fix').split(' '));
const tokens = (s) => new Set(s.toLowerCase().replace(/[^a-z0-9\s]/g, ' ').split(/\s+/)
    .filter((w) => w.length > 2 && !STOP.has(w)));

const recentClosed = issues.filter((i) => i.state === 'CLOSED' && i.closedAt && days(i.closedAt) < 90);
const pool = [...open, ...recentClosed];
for (let a = 0; a < pool.length; a++) {
    for (let b = a + 1; b < pool.length; b++) {
        if (pool[a].state === 'CLOSED' && pool[b].state === 'CLOSED') continue;
        const ta = tokens(pool[a].title);
        const tb = tokens(pool[b].title);
        if (!ta.size || !tb.size) continue;
        const inter = [...ta].filter((t) => tb.has(t)).length;
        const j = inter / (ta.size + tb.size - inter);
        if (j >= config.duplicateThreshold) {
            add({
                kind: 'duplicate-candidate',
                severity: 'info',
                issues: [pool[a].number, pool[b].number],
                summary: `#${pool[a].number} and #${pool[b].number} overlap ${Math.round(j * 100)}% by title`,
                evidence: `"${pool[a].title}" / "${pool[b].title}"`,
                needsJudgement: 'Same work, or neighbouring work?',
            });
        }
    }
}

// ── Output ─────────────────────────────────────────────────────────────────────

const only = args.only ? new Set(String(args.only).split(',')) : null;
const kept = only ? findings.filter((f) => only.has(f.kind)) : findings;
const rank = { error: 0, warn: 1, info: 2 };
kept.sort((a, b) => rank[a.severity] - rank[b.severity] || a.kind.localeCompare(b.kind));

if (args.json) {
    console.log(JSON.stringify({ repo: cache.repo, fetchedAt: cache.fetchedAt, findings: kept }, null, 2));
} else {
    const byKind = new Map();
    for (const f of kept) byKind.set(f.kind, [...(byKind.get(f.kind) ?? []), f]);
    console.log(`${cache.repo} — ${cache.counts.open} open of ${cache.counts.issues}, ${kept.length} findings\n`);
    for (const [kind, fs] of byKind) {
        console.log(`## ${kind} (${fs.length}) [${fs[0].severity}]`);
        for (const f of fs) {
            console.log(`  - ${f.summary}`);
            if (f.evidence) console.log(`      ${f.evidence}`);
            if (f.fix) console.log(`      FIX: ${JSON.stringify(f.fix)}`);
            if (f.needsJudgement) console.log(`      JUDGE: ${f.needsJudgement}`);
        }
        console.log('');
    }
}
