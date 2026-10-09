---
name: ticket-workflow
description: >
    Claim, build, merge and close a GitHub Issues ticket, and file new issues. Use when claiming a
    ticket, picking the next ticket to build, filing an issue, opening, readying or merging a
    ticket's PR, or closing a ticket.
---

# Ticket workflow

The repo's `docs/agents/issue-tracker.md` holds the `gh` recipes: create, sub-issue, blocking
edges, labels. This skill holds the lifecycle around them. Where the two differ, the repo doc wins.

`<default>` below is the default branch: `gh repo view --json defaultBranchRef --jq .defaultBranchRef.name`.

## Filing an issue

- File only the issues the user asked for. An audit, review or investigation reports its findings
  in chat or in `docs/research/`.
- Verify a finding against the code, and measure it where you can, before it becomes an issue.
- One issue per piece of work, not per finding. Sub-issues come only from `/to-tickets`.
- Approval covers the one issue it was given for. If a reply could mean more, ask.

## Finding work

The **frontier** is every ticket that can start now: open, `ready-for-agent`, unassigned, no open
blockers, and no sub-issues (a parent is a spec, built through its tickets).

```sh
gh issue list --state open --label ready-for-agent --search no:assignee --limit 100 \
  --json number --jq '.[].number' | while read n; do
  gh api "repos/{owner}/{repo}/issues/$n" --jq 'select((.issue_dependencies_summary.blocked_by // 0) == 0
    and (.sub_issues_summary.total // 0) == 0) | "READY #\(.number) \(.title)"'
done
```

Blocking edges encode logical dependency, not textual conflict. Two frontier tickets that touch the
same module will collide at merge: run those one after the other.

## Claiming

Every agent may share one GitHub account, so the assignee shows a ticket is held but not by whom.
Claim before reading code:

1. `gh issue view <n> --json assignees`. An assignee means another session holds it: pick another.
2. `gh issue edit <n> --add-assignee @me`
3. `gh issue comment <n> --body "Claimed by session <id>."`, with `<id>` from `uuidgen`.
4. `gh issue view <n> --comments`. If another session's claim comment came first, it wins: delete
   yours, unassign, and tell the user.

The ticket keeps `ready-for-agent`; the assignee is the lock. Stay assigned while waiting on the
user, on CI or on a merge: the claim is what stops a second session building work already in
flight. Unassign only when abandoning the ticket, with a comment saying where the work got to.

## Building

One ticket per branch, named `worktree-issue-<n>-<slug>`, in a worktree at
`.claude/worktrees/<branch>`.

The completing commit ends with `Closes #<n>.` on its own line, above any `Co-Authored-By`.

Open the PR as a draft at the first push. Before you stop:

1. Merge `<default>` in.
2. Re-run the repo's gates for what that merge can touch, and fix CI and conflicts.
3. `gh pr ready <p>`, even with CI still running.

## Merging

Wait for CI in the background: `gh pr checks <p> --watch --interval 60`. Several agents polling at
the default 10s exhaust the GraphQL rate limit.

Merge `<default>` in again only when GitHub reports a conflict or the repo requires branches to be
up to date.

Once green, merge from the main checkout and clean up:

```sh
gh pr merge <p> --merge
git worktree remove .claude/worktrees/<branch>
git branch -D <branch>
```

If the repo doesn't delete merged branches itself (`gh repo view --json deleteBranchOnMerge`), run
`git push origin --delete <branch>`.

## Closing

A ticket closes when its work is delivered: when its PR merges, into whichever branch. A delivered
ticket left open is a blocker nobody can clear, and the dependency graph then deadlocks.

- **Into `<default>`:** the trailer closes it.
- **Into an integration branch:** closing keywords fire only on the default branch, so close it
  yourself at merge, and keep the trailer as the link from issue to commit. Merge an integration
  branch, never squash it: a squash drops the trailers.

  ```sh
  gh issue close <n> --comment "Delivered on \`<branch>\` as \`<sha>\` (PR #<p>). Reaches \`<default>\` when that branch lands."
  ```

- **Merged without a trailer:** close it by hand, quoting the SHA.
- **Not delivered:** close it only when superseded, duplicated or decided against, with
  `--reason "not planned"` and a comment saying which.

Done when the ticket is closed and its worktree and local branch are gone.
