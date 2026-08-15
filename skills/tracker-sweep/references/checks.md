# The mechanical checks

Every check in `checks.mjs` is a fact about the graph or the text. If one is wrong, it is a bug in
the script, not a judgement call. Severity is about *how confident the check is*, not how urgent the
work is.

| kind | severity | what it means | mechanically fixable |
|---|---|---|---|
| `dependency-cycle` | error | A blocks B blocks A. Nothing in the cycle can ever be unblocked. | no — someone must decide which edge is wrong |
| `prose-blocker-not-declared` | error | The body says "blocked by #N" and no edge exists. | yes — `add-blocked-by` |
| `prose-blocking-not-declared` | error | The body says "blocks #N" and no edge exists. | yes — `add-blocked-by` on the other side |
| `closed-with-open-blocker` | warn | A closed issue still declares an open blocker. | no — either it closed early or the edge is spent |
| `epic-closed-with-open-children` | warn | The epic closed and children did not. | no |
| `epic-complete` | warn | Every child closed but the epic is open. | no — often means work exists that was never made a child |
| `orphan-epic-child` | warn | Title matches the epic-child convention, no parent. | yes — `add-sub-issue` |
| `open-pr-for-closed-issue` | warn | A PR is still open for work whose issue is closed. | no — the PR needs merging, closing or relinking |
| `stale-assignment` | warn | Assigned, and untouched past the threshold. | no |
| `unlabelled` | warn | No labels at all. | yes, once you know which label |
| `unblocked` | info | Every declared blocker has closed — this is buildable now. | n/a — this is the frontier, and the most useful output |
| `references-closed-since` | info | The body points at issues that closed after it was written. | no — candidate list for the stale-prose pass |
| `unticked-done-item` | info | A checklist item points at a closed issue. | no |
| `duplicate-candidate` | info | Two titles overlap above the threshold. | no — candidate only |
| `missing-label-group` | info | None of a required label group is present. | yes, once you know which |
| `stale-open` / `stale-pr` | info | Untouched past the threshold. | no |
| `pr-closes-nothing` | info | An open PR declares no closing issue. | no |

## Reading the noisy ones

**`duplicate-candidate`** is title-token Jaccard overlap. It cannot tell "the same work twice" from
"two tickets about the same screen", and both are worth knowing about — the second is where
*conflicts* live. Expect a handful of false positives on a tracker with a consistent naming
convention; that is the intended trade.

**`references-closed-since`** fires on almost every long-lived issue, because referencing earlier
work is normal. Its value is as a *ranked reading list*: the issues at the top are the ones most
likely to be describing a world that no longer exists. Do not report these to the user as findings —
report what you found when you read them.

**`unblocked`** is not a defect. It is the answer to "what can be built now", derived rather than
remembered, and it is usually the thing the user actually wanted from the sweep.

## What the scripts deliberately do not check

- **Comments.** Bodies only. Comments are where decisions, supersessions and announced blockers
  actually live in most repos, so the judgement pass fetches them for the issues that matter. Doing
  it for every issue would cost an API call each and mostly return noise.
- **Whether the code matches the ticket.** Out of scope; a sweep audits the tracker.
- **Semantic duplication.** Two tickets describing the same work in different words will not trip
  the title check. That is the model's job, reading the frontier.
