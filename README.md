<h1><img src="assets/header.svg" alt="Claude Skills — xdgfx" width="100%"></h1>

A collection of Claude Code skills.

## Install (Claude Code)

```
/plugin marketplace add xdgfx/skills
/plugin install xdgfx-skills@xdgfx
```

## Install (npx skills)

```bash
npx skills add xdgfx/skills@tracker-sweep
```

## Skills

### Moved: wireviz and pipeviz

Both were replaced by one `ferrule` skill, which lives with the renderer in
[XDGFX/ferrule](https://github.com/XDGFX/ferrule) (`skills/ferrule`): `npx skills add xdgfx/ferrule@ferrule`.

### fusion-modelling

Rules for building Autodesk Fusion models the way a disciplined person would, so the result can be
opened, read and changed by hand: the origin on a functional datum, native sketch tools, a few
dimensions between the things they relate, named parameters only for shared or measured values,
holes and repeats as features, assemblies held by the joints the real thing has, and fasteners
from Fusion's library instead of modelled. Carries the Fusion API call behind each tool, and a blind
review in which a second agent reads the finished model without knowing what the part is.

### fusion-gate (plugin)

Lets several agents share one Fusion. A hook on every `mcp__fusion360__*` call asks a small lock
service for a turn. The service lets one call at a time through and lends the design to one session
at a time. It keeps a queue that puts short reads first, notices when Fusion has crashed, and pauses
agents while you use Fusion, resuming once you stop. The hook starts the service when it's first
needed, and your MCP config is left alone.

```
/plugin install fusion-gate@xdgfx
```

Needs `uv`. Then `python3 <plugin>/fusion_gate.py status|pause|resume`, or ask any agent to pause
Fusion for you.

### tracker-sweep

Audit a GitHub issue tracker for staleness, wrong or missing blockers, duplicates, conflicting
tickets, orphaned epic children and ordering mistakes — then propose fixes and apply only the
approved ones. Every write is re-validated against the live tracker first, and the whole plan is
refused if any single action has gone stale. Designed to be re-run every few days on a large
tracker: the second sweep only deep-reads what moved. Trigger: `/tracker-sweep`.

### ticket-workflow

Carry a GitHub Issues ticket from claim to merge when several agents share one GitHub account: the
assignee is the lock and a claim comment breaks ties, one worktree branch per ticket, draft PRs,
merge and clean-up, and closing on delivery, including into integration branches. Sits on top of
the `docs/agents/issue-tracker.md` that mattpocock/skills' `setup-matt-pocock-skills` writes.

## Forks

`forks/` holds third-party skills carried with local changes, each with its upstream licence and
an `UPSTREAM` file naming the commit it is based on. `forks/upstream-sync.sh check [fork]` lists
upstream commits not yet taken in; `merge <fork>` three-way merges them. Forks are not part of
the plugin.

- `improve-codebase-architecture` from [mattpocock/skills](https://github.com/mattpocock/skills)
  (MIT): the report is built from a filled template with dark mode and before/after cards.
- `setup-matt-pocock-skills` from [mattpocock/skills](https://github.com/mattpocock/skills) (MIT):
  seeds a `## Gates` section and a `parked` label, and points the tracker doc at `ticket-workflow`.
