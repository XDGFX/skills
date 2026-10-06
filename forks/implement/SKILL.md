---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

Implement the work described by the user in the spec or tickets.

For ticketed implementation work, claim the ticket and then create or resume its dedicated Git
worktree before reading or editing code. Work only in that worktree; the primary checkout is never
the ticket workspace. Follow the repository's established `worktree-…` branch convention and include
the ticket number.

Use /tdd where possible, at pre-agreed seams.

Run typechecking regularly, single test files regularly, and the full test suite once at the end.

Once done, use /code-review to review the work.

Commit and push the ticket-worktree branch, then open or update its single PR with `Closes #<ticket>`
in the body. Verify the PR is open. A local commit alone is not completion.
