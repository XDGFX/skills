#!/usr/bin/env node
// Pull a whole issue tracker into one local cache file, in as few API calls as
// possible. Everything else in this skill reads the cache, never the network.
//
//   node fetch-tracker.mjs --repo owner/name [--out .tracker-sweep/cache.json]
//                          [--bodies open|recent|all] [--since ISO8601]
//
// Bodies dominate the payload on a large tracker, so they are fetched only where
// a judgement check can actually use them: open issues by default, plus anything
// updated since the last sweep.

import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync, readFileSync, existsSync } from 'node:fs';
import { dirname } from 'node:path';

const args = Object.fromEntries(
    process.argv.slice(2).flatMap((a, i, all) =>
        a.startsWith('--') ? [[a.slice(2), all[i + 1]?.startsWith('--') === false ? all[i + 1] : true]] : []),
);

const repo = args.repo;
if (!repo || !repo.includes('/')) {
    console.error('usage: fetch-tracker.mjs --repo owner/name [--out PATH] [--bodies open|recent|all] [--since ISO]');
    process.exit(2);
}
const [owner, name] = repo.split('/');
const out = args.out || '.tracker-sweep/cache.json';
const bodyMode = args.bodies || 'open';

const gh = (query, vars = {}) => {
    const argv = ['api', 'graphql', '-f', `query=${query}`];
    for (const [k, v] of Object.entries(vars)) argv.push('-f', `${k}=${v}`);
    const raw = execFileSync('gh', argv, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
    const json = JSON.parse(raw);
    if (json.errors) throw new Error(JSON.stringify(json.errors));
    return json.data;
};

const ISSUE_FIELDS = `
    number title state stateReason createdAt updatedAt closedAt url body
    author { login }
    labels(first: 30) { nodes { name } }
    assignees(first: 10) { nodes { login } }
    milestone { title state }
    blockedBy(first: 50) { nodes { number state title } }
    blocking(first: 50) { nodes { number state title } }
    parent { number title state }
    subIssues(first: 100) { nodes { number state title } }
    timelineItems(last: 1, itemTypes: [CROSS_REFERENCED_EVENT]) {
        nodes { ... on CrossReferencedEvent { createdAt source { ... on PullRequest { number state isDraft } } } }
    }
`;

const query = `
query($owner: String!, $name: String!, $cursor: String) {
    repository(owner: $owner, name: $name) {
        issues(first: 50, after: $cursor, orderBy: { field: UPDATED_AT, direction: DESC }) {
            pageInfo { hasNextPage endCursor }
            nodes { ${ISSUE_FIELDS} }
        }
    }
}`;

const PR_QUERY = `
query($owner: String!, $name: String!, $cursor: String) {
    repository(owner: $owner, name: $name) {
        pullRequests(first: 50, after: $cursor, states: OPEN, orderBy: { field: UPDATED_AT, direction: DESC }) {
            pageInfo { hasNextPage endCursor }
            nodes {
                number title state isDraft updatedAt url body
                author { login }
                headRefName baseRefName
                closingIssuesReferences(first: 20) { nodes { number state title } }
            }
        }
    }
}`;

const pageAll = (q, path) => {
    const all = [];
    let cursor = null;
    for (;;) {
        const data = gh(q, cursor ? { owner, name, cursor } : { owner, name });
        const conn = path.reduce((o, k) => o[k], data);
        all.push(...conn.nodes);
        if (!conn.pageInfo.hasNextPage) break;
        cursor = conn.pageInfo.endCursor;
    }
    return all;
};

process.stderr.write(`fetching ${repo} …\n`);
const issues = pageAll(query, ['repository', 'issues']);
const prs = pageAll(PR_QUERY, ['repository', 'pullRequests']);

// Flatten, and drop bodies we were not asked for. A dropped body is recorded as
// null with its length kept, so a check can tell "not fetched" from "empty".
const since = args.since ? Date.parse(args.since) : null;
const keepBody = (i) =>
    bodyMode === 'all' ||
    (bodyMode === 'open' && i.state === 'OPEN') ||
    (bodyMode === 'recent' && since && Date.parse(i.updatedAt) >= since) ||
    (since && Date.parse(i.updatedAt) >= since);

const flat = issues.map((i) => ({
    number: i.number,
    title: i.title,
    state: i.state,
    stateReason: i.stateReason,
    createdAt: i.createdAt,
    updatedAt: i.updatedAt,
    closedAt: i.closedAt,
    url: i.url,
    author: i.author?.login ?? null,
    labels: i.labels.nodes.map((l) => l.name),
    assignees: i.assignees.nodes.map((a) => a.login),
    milestone: i.milestone?.title ?? null,
    blockedBy: i.blockedBy.nodes.map((n) => ({ number: n.number, state: n.state, title: n.title })),
    blocking: i.blocking.nodes.map((n) => ({ number: n.number, state: n.state, title: n.title })),
    parent: i.parent ? { number: i.parent.number, state: i.parent.state } : null,
    subIssues: i.subIssues.nodes.map((n) => ({ number: n.number, state: n.state, title: n.title })),
    bodyLength: i.body?.length ?? 0,
    body: keepBody(i) ? i.body : null,
}));

const cache = {
    repo,
    fetchedAt: new Date().toISOString(),
    bodyMode,
    since: args.since ?? null,
    counts: {
        issues: flat.length,
        open: flat.filter((i) => i.state === 'OPEN').length,
        openPrs: prs.length,
    },
    issues: flat,
    pullRequests: prs.map((p) => ({
        number: p.number,
        title: p.title,
        isDraft: p.isDraft,
        updatedAt: p.updatedAt,
        url: p.url,
        author: p.author?.login ?? null,
        headRefName: p.headRefName,
        baseRefName: p.baseRefName,
        closes: p.closingIssuesReferences.nodes.map((n) => ({ number: n.number, state: n.state })),
        body: p.body,
    })),
};

// Carry the previous sweep's state forward so repeat runs can go incremental.
const statePath = dirname(out) + '/state.json';
let prior = null;
if (existsSync(statePath)) {
    try { prior = JSON.parse(readFileSync(statePath, 'utf8')); } catch { /* stale state is not fatal */ }
}
cache.priorSweepAt = prior?.lastSweepAt ?? null;
cache.changedSince = prior?.lastSweepAt
    ? flat.filter((i) => Date.parse(i.updatedAt) > Date.parse(prior.lastSweepAt)).map((i) => i.number)
    : null;

mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, JSON.stringify(cache, null, 2));

process.stderr.write(
    `wrote ${out} — ${cache.counts.issues} issues (${cache.counts.open} open), ` +
    `${cache.counts.openPrs} open PRs` +
    (cache.changedSince ? `, ${cache.changedSince.length} changed since last sweep` : ', no prior sweep') +
    `\n`,
);
