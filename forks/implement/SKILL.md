---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

This skill is a local fork. First run `~/git/skills/forks/upstream-sync.sh check implement`. If it
lists upstream commits, show them to the user and ask whether to merge them first
(`upstream-sync.sh merge implement`, then resolve its conflict markers and commit) or carry on. If
upstream is unreachable, carry on.

Implement the work described by the user in the spec or tickets.

If the user passes a ticket reference, fetch it from the issue tracker and state its title before
starting. If the reference is ambiguous, ask.

For ticketed implementation work, claim the ticket and then create or resume its dedicated Git
worktree before reading or editing code. Work only in that worktree; the primary checkout is never
the ticket workspace. Follow the repository's established `worktree-…` branch convention and include
the ticket number.

Call the Skill tool with "tdd" where possible, at pre-agreed seams.

Run typechecking regularly, single test files regularly, and the full test suite once at the end.

Once done, call the Skill tool with "code-review" to review the work.

Commit and push the ticket-worktree branch, then open or update its single PR with `Closes #<ticket>`
in the body. Verify the PR is open. A local commit alone is not completion.
