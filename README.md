# Claude Skills — xdgfx

A collection of Claude Code skills.

## Install (Claude Code)

```
/plugin install github:xdgfx/skills
```

## Install (npx skills)

```bash
npx skills add xdgfx/skills@tracker-sweep
```

## Skills

### Moved: wireviz and pipeviz

Both were replaced by one `ferrule` skill, which lives with the renderer in
[XDGFX/ferrule](https://github.com/XDGFX/ferrule) (`skills/ferrule`): `npx skills add xdgfx/ferrule@ferrule`.

### tracker-sweep

Audit a GitHub issue tracker for staleness, wrong or missing blockers, duplicates, conflicting
tickets, orphaned epic children and ordering mistakes — then propose fixes and apply only the
approved ones. Every write is re-validated against the live tracker first, and the whole plan is
refused if any single action has gone stale. Designed to be re-run every few days on a large
tracker: the second sweep only deep-reads what moved. Trigger: `/tracker-sweep`.

## Forks

`forks/` holds third-party skills carried with local changes, each with its upstream licence and
an `UPSTREAM` file naming the commit it is based on. `forks/upstream-sync.sh check [fork]` lists
upstream commits not yet taken in; `merge <fork>` three-way merges them. Forks are not part of
the plugin.

- `improve-codebase-architecture` from [mattpocock/skills](https://github.com/mattpocock/skills)
  (MIT): the report is built from a filled template with dark mode and before/after cards.
