# The judgement pass

The scripts hand you a graph and a candidate list. This is the part that needs reading, and it is
where the findings a user could not have got themselves come from.

Work in this order — it is cheapest-first, and each stage narrows the next.

## 1. Stale prose

**Candidates:** `references-closed-since`, highest reference count first, plus anything changed since
the last sweep.

For each, the question is not *is this sentence out of date* but **would an agent picking this ticket
up today build the wrong thing?** A historical reference is fine. A stated premise that has since
become false is a defect.

The three that matter most, in rough order of damage:

- **A stated dependency that has been removed.** "Draw it through X" where a closed ticket took X out
  of the project. An agent will reinstate it.
- **A stated absence that has since been filled.** "The settings screen does not exist yet, so this
  cannot land" — when it now does. The ticket reads as blocked when it is buildable, and the reasoning
  built on the absence is void.
- **A stated open item that has closed.** Cheap to fix, and it is how a reader loses trust in the
  rest of the document.

Epics and specs are worth reading properly even when nothing flags them: they are the longest-lived
prose in the tracker and the most-read, so they rot furthest. Check the epic's own spec against what
its closed children actually decided.

## 2. Conflicts

The highest-value finding, and invisible to every script. Two open tickets conflict when:

- they specify **incompatible outcomes** for the same thing — one says a card carries a list of
  specifics, the other says cut the list;
- they **rewrite the same file** with no ordering declared between them, so whichever lands second
  either clobbers the first or has to be rewritten;
- one is **written against a premise the other overturns**, most often because a design question was
  settled between the two being filed.

How to find them cheaply: group open tickets by the files and surfaces they name, then read each
group together. `duplicate-candidate` pairs are one input; so is any two tickets naming the same
route, component or document.

Report both numbers, state which is the later word and why, and propose an ordering edge rather than
a rewrite — the ordering is mechanical and safe, the rewrite is the user's call.

## 3. Blockers announced in comments

The recurring failure mode: a comment says "this now blocks #81", everyone believes it, and the
graph never learns. It survives because the comment reads as authoritative and nobody re-derives it.

```bash
gh issue view N --repo owner/name --comments
```

Worth doing for: every issue in the frontier, every epic, and anything with a long recent thread.
Search the text for *blocks*, *blocked by*, *waiting on*, *gated on*, *now blocks*, *freed*,
*superseded*. Every one of those that is not an edge is an `add-blocked-by`/`remove-blocked-by`
action — and this is exactly the kind of fix the plan-and-validate flow exists for.

## 4. Missing build steps

Two different gaps, and both count:

- **Named in the spec, no ticket.** Read the epic's acceptance criteria or user stories against the
  set of tickets. Anything that must be built and is nobody's is a gap — report it, and offer to
  draft the ticket rather than filing one unasked.
- **Has a ticket, not in the epic.** Work that is plainly a build step but was filed standalone.
  Symptoms: it matches the epic's naming convention, or it is a sibling in kind to existing children.
  This is `add-sub-issue` and it is safely mechanical.

Be careful about the converse: bugs, docs fixes and tooling are often *deliberately* outside a build
epic. Check what the existing children have in common before proposing membership, and say what rule
you inferred.

## 5. Order

With the graph correct, the frontier is derived, not remembered — that is the whole point. From the
`unblocked` findings, rank by:

1. **What it unblocks.** An item that frees a chain beats an item that frees nothing.
2. **Whether it gates a drawing, a decision or a build.** Anything waiting on a human decision should
   be surfaced to the user *now*, because it is the only class of work they can advance themselves.
3. **Cheapness.** Among equals, the short one first.

Say plainly which items no agent can advance — those are the user's queue, and a sweep that does not
separate them has not done its job.

## 6. What to leave alone

- A ticket held open on purpose as a queue or a register. Look for a body that says so.
- Long-open design questions awaiting the user. These are not stale; they are pending. Report them
  as a waiting-on-you list, not as rot.
- Anything the user dismissed on a previous sweep — check `.tracker-sweep/dismissed.json` first.
