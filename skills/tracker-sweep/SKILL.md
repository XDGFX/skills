---
name: tracker-sweep
description: >
    Audit a GitHub issue tracker for staleness, wrong or missing blockers, duplicates, conflicts
    between tickets, orphaned epic children, and ordering mistakes — then propose fixes and apply
    the approved ones. Use whenever the user asks to sweep, audit, tidy, groom or spring-clean an
    issue tracker or backlog; asks whether anything is stale, outdated, duplicated or contradictory;
    asks whether blockers are correct or missing; asks whether the build epic is missing steps or
    the order is wrong; or asks what is buildable next. Also for recurring backlog hygiene runs on
    a project with a large tracker. Trigger: /tracker-sweep.
---

# tracker-sweep

A tracker rots in ways the tracker itself cannot see. Blockers get announced in a comment and never
set as edges. An issue's body describes a world four merged PRs ago. Two tickets are written against
the same file by different sessions and neither knows. A hand-maintained "here is the frontier"
comment is authoritative for about a day.

This skill finds those, **validates every finding against reality before anything is written**, and
applies only what the user approves.

## The rules that make this safe

1. **Read everything from one cache, decide nothing from memory.** `fetch-tracker.mjs` pulls the
   tracker once; every check reads that file.
2. **Nothing is written until it is re-validated live.** The cache is a claim about the past.
   `apply.mjs` re-reads the live state of every issue a plan touches and **refuses the entire plan**
   if any action is stale, contradictory, already true, or would create a cycle. A half-applied
   dependency graph is worse than an unapplied one.
3. **The user approves the plan before it is applied.** Findings are proposals. The default mode of
   `apply.mjs` writes nothing.
4. **A mechanical finding and a judgement finding are different things.** The scripts only ever
   assert facts about the graph and the text. Anything requiring taste — is this a duplicate, is
   this prose stale, do these two tickets conflict — is surfaced as a *candidate* for you to read
   and decide, and reported to the user with your reasoning, never applied silently.

## Procedure

### 1. Establish the target and the working directory

Work in `<repo>/.tracker-sweep/` (git-ignored, or under the job's scratch directory if the user
would rather not touch the repo). If `--repo` is not obvious from the request, infer it from the
current repo's `origin` remote and say which one you picked.

### 2. Fetch

```bash
node scripts/fetch-tracker.mjs --repo owner/name --out .tracker-sweep/cache.json
```

One paginated GraphQL query for every issue — state, labels, assignees, timestamps, `blockedBy`,
`blocking`, `parent`, `subIssues` — plus open PRs and what they close. Bodies are fetched for open
issues by default (`--bodies all` for a full-history audit, `--bodies recent --since ISO` for a
routine re-run). On a 500-issue tracker this is seconds and a handful of API calls.

If a previous sweep left `.tracker-sweep/state.json`, the cache records which issues have changed
since — use it to keep repeat runs cheap (see *Running this every few days*).

### 3. Run the mechanical checks

```bash
node scripts/checks.mjs --cache .tracker-sweep/cache.json --json > .tracker-sweep/findings.json
node scripts/checks.mjs --cache .tracker-sweep/cache.json   # human-readable
```

The catalogue and what each check is worth is in `references/checks.md`. Read it before
interpreting the output — several checks are deliberately noisy because a cheap candidate list is
more useful than a confident wrong answer.

`checks.mjs` reads optional per-repo settings from `.tracker-sweep/config.json`:

```json
{
    "epics": [66],
    "epicChildTitlePattern": "^v1: ",
    "requiredLabelGroups": [["bug", "enhancement", "question", "ready-for-agent"]],
    "staleOpenDays": 45,
    "staleAssignmentDays": 14,
    "duplicateThreshold": 0.55,
    "ignoreLabels": ["icebox"]
}
```

Write this file on the first sweep of a repo, from what you learn about its conventions, and tell
the user it exists so they can tune it. It is what makes the second sweep sharper than the first.

### 4. Do the judgement pass — the part the scripts cannot do

The scripts hand you candidates. Now read. **Budget your reading**: on a large tracker, deep-read
only the open issues, plus any issue named in an `error`/`warn` finding, plus anything changed since
the last sweep.

Work through `references/judgement-pass.md`. In short, you are looking for:

- **Stale prose** — a body that describes the world before a closed ticket changed it. The
  `references-closed-since` findings are your candidate list; the question for each is *would an
  agent picking this up today build the wrong thing?*
- **Conflicts** — two open tickets that specify incompatible outcomes, or that rewrite the same file
  with no ordering between them. These are the most valuable finding a sweep produces and no script
  can see them.
- **Prose blockers** — `blockedBy` edges announced in a **comment**. The scripts scan bodies only,
  so fetch comments for issues in the frontier and any issue with a recent long comment thread:
  `gh issue view N --repo R --comments`.
- **Missing build steps** — read the epic's own spec against its children. A step named in the spec
  with no ticket, or a ticket that is plainly a build step but is not a child, both count.
- **Order** — with the graph correct, what is genuinely buildable now, and what should be taken
  first? Prefer the item that unblocks the most, and say so.
- **Duplicates and supersession** — `duplicate-candidate` pairs, plus anything a later ticket
  visibly re-decided.

### 5. Report before you propose

Give the user the findings organised by *what they should do about it*, not by check name:

- **Wrong** — the tracker asserts something false. Missing edges, stale blockers, bad labels.
- **Stale** — true once, misleading now. Name the ticket, the sentence, and what made it false.
- **Conflicting** — two tickets that cannot both be built as written. Always name both, and say
  which one you believe is the later word.
- **Missing** — work that exists but has no ticket, or a ticket with no epic.
- **Order** — the buildable frontier, best-first, with the reason.

Every item names its issue numbers. Keep it short enough to read in one sitting; the detail lives in
the findings file.

### 6. Propose a plan, and validate it

Write the mechanical fixes — and *only* the mechanical ones — into a plan file:

```json
{
    "repo": "owner/name",
    "fetchedAt": "<copy cache.fetchedAt verbatim>",
    "actions": [
        { "action": "add-blocked-by", "issue": 81, "blocker": 127, "why": "declared in the 12 Aug comment, never set" }
    ]
}
```

Then validate, which writes nothing:

```bash
node scripts/apply.mjs --plan .tracker-sweep/plan.json
```

Show the user the validation output. Anything refused is a finding in its own right — a refusal
usually means the tracker knows something the sweep did not.

### 7. Apply, once the user says yes

```bash
node scripts/apply.mjs --plan .tracker-sweep/plan.json --confirm --receipt .tracker-sweep/receipt.json
```

**Never pass `--confirm` on the same turn the user first sees the plan.** Editing issue *bodies* is
not a sweep action and never goes in a plan: propose the wording and let the user take it, or open a
ticket for the correction if that is the repo's convention.

Then record the sweep so the next one can be incremental:

```bash
printf '{"lastSweepAt":"%s"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > .tracker-sweep/state.json
```

## Running this every few days

The second and later sweeps should cost a fraction of the first:

- Fetch with `--bodies recent --since <lastSweepAt>`; the cache still carries the full graph, so
  every graph check stays complete while the expensive text reading shrinks to what moved.
- `cache.changedSince` lists exactly which issues moved. **Deep-read only those**, plus anything a
  new `error`/`warn` finding names.
- Findings you and the user have already dismissed should stay dismissed: keep them in
  `.tracker-sweep/dismissed.json` as `{"kind":…,"issues":[…],"why":…}` and filter them out of the
  report rather than re-raising them every few days. A sweep that repeats itself gets ignored.
- If nothing needs a decision, say so in one line and stop. A clean tracker is a valid result and
  should be cheap to report.

Good cadence: a light sweep every few days, and a full one (`--bodies all`, dismissals reviewed)
after any milestone or big merge — those are when supersession and stale prose actually accumulate.

## What this skill will not do

- Close, reopen or re-scope work on its own judgement. It proposes; the user disposes.
- Rewrite issue bodies. Stale prose is reported, and the correction is the user's call — in several
  repos the convention is that a correction is itself a ticket.
- Reparent an issue that already has a parent, or apply a plan whose issues moved underneath it.
- Treat its own previous sweep comment as authoritative. Every sweep re-derives from the graph,
  which is the whole reason a hand-maintained frontier comment goes stale and this does not.
